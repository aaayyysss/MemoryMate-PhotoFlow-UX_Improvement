# meta_backfill_single.py
# !/usr/bin/env python3
# Version 01.1.01.03 dated 20251024

"""
meta_backfill.py

Controller script to backfill missing metadata (width,height,date_taken) in the DB.

Usage (example):
    python meta_backfill.py --workers 6 --timeout 6 --batch 200 --dry-run

Behavior:
 - Queries ReferenceDB for photo_metadata rows where width or height or date_taken is NULL.
 - Processes rows in batches, launching metadata_extractor.py as a short-lived subprocess
   per file with a per-file timeout.
 - Applies DB updates in small batches using ReferenceDB.upsert_photo_metadata(...).
 - Logs progress to stdout and to app_log.txt via safe_log (same helper used by the app).
"""
import sys, os, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from pathlib import Path

# Use the app's safe_log if present
def safe_log_local(msg: str):
    try:
        p = Path.cwd() / "app_log.txt"
        with p.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        try:
            print("[LOG FAIL]", msg)
        except Exception:
            pass

def call_extractor(path, timeout, python_exe=None):
    python_exe = python_exe or sys.executable
    script = Path(__file__).with_name("metadata_extractor.py")
    cmd = [python_exe, str(script), str(path)]
    try:
        import subprocess
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        stdout = proc.stdout.strip()
        if not stdout:
            return {"path": str(path), "error": "empty extractor output"}
        try:
            return json.loads(stdout)
        except Exception as e:
            return {"path": str(path), "error": f"invalid json: {e}", "raw": stdout[:300]}
    except subprocess.TimeoutExpired:
        return {"path": str(path), "error": f"timeout({timeout}s)"}
    except Exception as e:
        return {"path": str(path), "error": str(e)}

def batch(iterable, n=100):
    it = iter(iterable)
    while True:
        chunk = []
        try:
            for _ in range(n):
                chunk.append(next(it))
        except StopIteration:
            if chunk:
                yield chunk
            break
        yield chunk

def main(argv=None):
    argv = argv or sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6, help="Parallel extractor subprocesses")
    ap.add_argument("--timeout", type=float, default=6.0, help="Per-file extractor timeout (s)")
    ap.add_argument("--batch", type=int, default=200, help="DB update batch size")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of files (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="Do not write DB; just show plan")
    args = ap.parse_args(argv)

    safe_log_local(f"[meta_backfill] start workers={args.workers} timeout={args.timeout} batch={args.batch} dry_run={args.dry_run}")

    # Import ReferenceDB from your project; this assumes repo root is PYTHONPATH or same folder
    try:
        from reference_db import ReferenceDB
    except Exception as e:
        safe_log_local(f"[meta_backfill] failed to import ReferenceDB: {e}")
        print("Failed to import ReferenceDB from repo; run this from project root.")
        return 2

    db = ReferenceDB()

    # find rows needing metadata
    # The ReferenceDB interface in your repo has methods used elsewhere; we expect:
    # - db.get_images_missing_metadata(limit=None) OR we query photo_metadata directly
    # Fallback: use a direct query via db._connect()
    to_process = []
    try:
        with db._connect() as conn:
            cur = conn.cursor()
            q = "SELECT path FROM photo_metadata WHERE width IS NULL OR height IS NULL OR date_taken IS NULL"
            if args.limit and args.limit > 0:
                q += f" LIMIT {int(args.limit)}"
            cur.execute(q)
            rows = cur.fetchall()
            to_process = [r[0] for r in rows]
    except Exception as e:
        safe_log_local(f"[meta_backfill] DB query failed: {e}")
        print("DB query failed:", e)
        return 3

    total = len(to_process)
    safe_log_local(f"[meta_backfill] found {total} files needing metadata")
    print(f"Found {total} files needing metadata")

    if total == 0:
        return 0

    # Executor will manage parallel subprocess runs; using ThreadPoolExecutor is fine since the heavy work is in subprocess.
    results = []
    processed = 0
    start_time = time.time()
    python_exe = sys.executable

    with ThreadPoolExecutor(max_workers=args.workers) as exec:
        futures = {exec.submit(call_extractor, p, args.timeout, python_exe): p for p in to_process}

        # Collect and update DB in batches
        update_batch = []
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"path": str(p), "error": str(e)}
            processed += 1
            # Add result to batch
            update_batch.append(res)

            # Print progress
            if processed % 10 == 0 or processed == total:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"[meta_backfill] processed {processed}/{total} ({rate:.1f}/s)")

            # When batch fills, write to DB
            if len(update_batch) >= args.batch or processed == total:
                if args.dry_run:
                    # Summarize
                    ok_count = sum(1 for r in update_batch if r.get("ok"))
                    err_count = len(update_batch) - ok_count
                    print(f"[dry-run] batch: ok={ok_count}, err={err_count}")
                    safe_log_local(f"[meta_backfill][dry-run] batch: ok={ok_count}, err={err_count}")
                else:
                    # For each result, update DB row by reading existing row and calling upsert_photo_metadata
                    updates = 0
                    with db._connect() as conn:
                        cur = conn.cursor()
                        for r in update_batch:
                            path = r.get("path")
                            if not path:
                                continue
                            if r.get("ok"):
                                width = r.get("width")
                                height = r.get("height")
                                date_taken = r.get("date_taken")
                            else:
                                # mark as failed by setting a flag column if you have one, or skip
                                width = None
                                height = None
                                date_taken = None
                            # Read existing row
                            cur.execute("SELECT folder_id, size_kb, modified, tags FROM photo_metadata WHERE path = ?", (path,))
                            row = cur.fetchone()
                            if row:
                                folder_id, size_kb, modified, tags = row
                            else:
                                # row missing -> skip
                                continue
                            try:
                                # Use ReferenceDB.upsert_photo_metadata if available; fallback to SQL
                                try:
                                    db.upsert_photo_metadata(path, folder_id, size_kb, modified, width, height, date_taken, tags)
                                except Exception:
                                    # raw SQL fallback
                                    cur.execute("""
                                        UPDATE photo_metadata
                                        SET width = ?, height = ?, date_taken = ?
                                        WHERE path = ?
                                    """, (width, height, date_taken, path))
                                    conn.commit()
                                updates += 1
                            except Exception as e:
                                safe_log_local(f"[meta_backfill] update failed for {path}: {e}")
                    safe_log_local(f"[meta_backfill] applied {updates} updates to DB")
                update_batch.clear()

    elapsed = time.time() - start_time
    safe_log_local(f"[meta_backfill] done processed={processed} total={total} elapsed={elapsed:.1f}s")
    print(f"Done. processed={processed}. elapsed={elapsed:.1f}s")
    return 0

if __name__ == "__main__":
    sys.exit(main())