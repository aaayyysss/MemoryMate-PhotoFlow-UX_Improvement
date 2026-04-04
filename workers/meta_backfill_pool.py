# meta_backfill_pool.py
# Version 01.1.01.07 dated 20251102
#!/usr/bin/env python3


"""
meta_backfill_pool.py

Persistent metadata backfill supervisor + worker pool.
Uses MetadataService for extraction and ReferenceDB API for storage.

Usage:
  python meta_backfill_pool.py --workers 4 --timeout 8 --batch 200 --limit 0

Run this as a separate detached process from the GUI for production-grade backfill.
"""
import sys
import time
import json
import argparse
import traceback
import os
from pathlib import Path
from multiprocessing import Process, Queue, Event, cpu_count
import multiprocessing

from progress_writer import write_status


# Import services and database from repo root
try:
    from reference_db import ReferenceDB
    from services import MetadataService
except Exception as e:
    print("Failed to import dependencies:", e)
    raise

# Worker function that runs inside a child process

# Worker function that runs inside a child process
def worker_loop(worker_id: int, task_q: Queue, result_q: Queue, stop_event: Event):
    """
    Persistent worker process. Pulls file paths from task_q, extracts metadata using
    MetadataService, and pushes result dicts to result_q.
    """
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    # Initialize MetadataService in worker process
    try:
        metadata_service = MetadataService()
    except Exception as e:
        result_q.put({"path": None, "ok": False, "error": f"MetadataService init failed: {e}"})
        return

    # Optional HEIF support
    try:
        import pillow_heif  # noqa
    except Exception:
        pass

    def extract(path):
        """Extract metadata using MetadataService for consistency."""
        out = {"path": str(path)}
        try:
            # Use MetadataService for extraction
            width, height, date_taken = metadata_service.extract_basic_metadata(path)

            out["width"] = width
            out["height"] = height
            out["date_taken"] = date_taken
            out["ok"] = True

        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)

        return out

    while not stop_event.is_set():
        try:
            p = task_q.get(timeout=0.5)
        except Exception:
            continue
        if p is None:
            break
        res = extract(p)
        res["elapsed"] = 0.0
        try:
            result_q.put(res)
        except Exception:
            pass

class PersistentPool:
    def __init__(self, workers=4, timeout=8.0):
        self.workers = max(1, min(int(workers), cpu_count() * 2))
        self.timeout = float(timeout)
        self.task_queues = []
        self.procs = []
        self.result_q = multiprocessing.Queue(maxsize=8192)
        self.stop_event = multiprocessing.Event()
        for i in range(self.workers):
            q = multiprocessing.Queue()
            p = Process(target=worker_loop, args=(i, q, self.result_q, self.stop_event), daemon=True)
            p.start()
            self.task_queues.append(q)
            self.procs.append(p)

    def submit_round_robin(self, paths):
        n = len(self.task_queues)
        i = 0
        for p in paths:
            try:
                self.task_queues[i % n].put(p)
            except Exception:
                pass
            i += 1

    def drain_results(self, max_items=200):
        out = []
        for _ in range(max_items):
            try:
                r = self.result_q.get_nowait()
                out.append(r)
            except Exception:
                break
        return out

    def shutdown(self):
        self.stop_event.set()
        for q in self.task_queues:
            try:
                q.put(None)
            except Exception:
                pass
        for p in self.procs:
            try:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=1.0)
            except Exception:
                pass
   

def controller_1st(workers=4, timeout=8.0, batch=200, limit=0, dry_run=False, max_retries=3, quiet=False):
    db = ReferenceDB()
    # Ensure DB has the metadata columns
    try:
        db.ensure_metadata_columns()
    except Exception:
        pass

    to_proc = db.get_images_missing_metadata(limit=limit or None, max_failures=max_retries)
    total = len(to_proc)
    if not quiet:
        print(f"[meta_backfill] found {total} images needing metadata")
    if total == 0:
        return 0

    pool = PersistentPool(workers=workers, timeout=timeout)
    pool.submit_round_robin(to_proc)

    processed = 0
    start = time.time()
    try:
        while True:
            results = pool.drain_results(max_items=batch * 2)
            if results:
                for r in results:
                    processed += 1
                    if r.get("ok"):
                        if not dry_run:
                            db.mark_metadata_success(r["path"], r.get("width"), r.get("height"), r.get("date_taken"))
                    else:
                        if not dry_run:
                            db.mark_metadata_failure(r["path"], error=r.get("error"), max_retries=max_retries)
                if (processed % 10 == 0 or processed == total) and not quiet:
                    elapsed = time.time() - start
                    rate = processed / elapsed if elapsed > 0 else 0.0
                    print(f"[meta_backfill] processed {processed}/{total} ({rate:.2f}/s)")
            # exit condition
            if processed >= total and pool.result_q.empty():
                break
            time.sleep(0.3)
    finally:
        pool.shutdown()
    elapsed = time.time() - start
    if not quiet:
        print(f"[meta_backfill] DONE processed={processed} total={total} elapsed={elapsed:.1f}s")
    return 0

def controller(workers=4, timeout=8.0, batch=200, limit=0, dry_run=False, max_retries=3, quiet=False):
    db = ReferenceDB()
    # Ensure DB has the metadata columns
    try:
        db.ensure_metadata_columns()
    except Exception:
        pass

    to_proc = db.get_images_missing_metadata(limit=limit or None, max_failures=max_retries)
    total = len(to_proc)
    if not quiet:
        print(f"[meta_backfill] found {total} images needing metadata")
    if total == 0:
        return 0

    pool = PersistentPool(workers=workers, timeout=timeout)
    pool.submit_round_robin(to_proc)

    processed = 0
    start = time.time()
#    status_path = os.path.join(os.getcwd(), "status", "backfill_status.json")
#    write_status(status_path, "starting", 0, total)

    from app_env import app_path
    status_path = app_path("status", "backfill_status.json")
    log_path = status_path.replace(".json", ".log")

    def _log_progress(phase, current, total):
        pct = round((current / total) * 100, 1) if total else 0
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {phase} {pct:.1f}% ({current}/{total})\n")

    write_status(status_path, "starting", 0, total)
    _log_progress("starting", 0, total)


    try:
        while True:
            results = pool.drain_results(max_items=batch * 2)
            if results:
                for r in results:
                    processed += 1
                    if r.get("ok"):
                        if not dry_run:
                            db.mark_metadata_success(r["path"], r.get("width"), r.get("height"), r.get("date_taken"))
                    else:
                        if not dry_run:
                            db.mark_metadata_failure(r["path"], error=r.get("error"), max_retries=max_retries)
                # 🧩 Emit progress update every 20 items or at end            
                if processed % 20 == 0 or processed >= total:
                    write_status(status_path, "processing", processed, total)
                    _log_progress("processing", processed, total)
                    if not quiet:
                        elapsed = time.time() - start
                        rate = processed / elapsed if elapsed > 0 else 0.0
                        print(f"[meta_backfill] processed {processed}/{total} ({rate:.2f}/s)")                    
            # exit condition
            if processed >= total and pool.result_q.empty():
                break                    

            time.sleep(0.3)
        # ✅ Mark completion
        write_status(status_path, "done", total, total)
        _log_progress("done", total, total)

    finally:
        pool.shutdown()


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--quiet", "--silent", action="store_true", dest="quiet",
                    help="Suppress console output (quiet mode). Useful when launching detached.")
    return ap.parse_args(argv)



if __name__ == "__main__":
    args = parse_args()
    rc = controller(
        workers=args.workers,
        timeout=args.timeout,
        batch=args.batch,
        limit=args.limit,
        dry_run=args.dry_run,
        max_retries=args.max_retries,
        quiet=args.quiet
    )
    sys.exit(rc)