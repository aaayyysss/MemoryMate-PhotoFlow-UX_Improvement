# thumb_cache_db.py
# Version 09.17.01.10 — 2025-10-26
# -----------------------------------------------------------
# Persistent thumbnail cache with auto-purge and diagnostics
# -----------------------------------------------------------

import os, sqlite3, io, time, threading, hashlib
from datetime import datetime
from PySide6.QtGui import QPixmap, QImage

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt

# Normalizer
def norm(p: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(p).strip()))
    except Exception:
        return str(p).strip()

CACHE_DB_PATH = os.path.join(os.path.dirname(__file__), "thumbnails_cache.db")
MAX_CACHE_MB = 500
PURGE_INTERVAL_DAYS = 7


class ThumbCacheDB:
    def __init__(self, db_path: str = CACHE_DB_PATH):
        self.db_path = db_path
        self.conn = None
        self.lock = threading.Lock()
        self._ensure_db()

        
        self.metrics = {
            "get_hits": 0,
            "get_misses": 0,
            "stores": 0,
            "get_count": 0,
            "get_total_ms": 0.0,
            "store_total_ms": 0.0,
        }
        
        # --- background housekeeping thread ---
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._auto_purge_worker, daemon=True)
        self._thread.start()

    # -------------------------------------------------------

    def _ensure_db(self):
        d = os.path.dirname(self.db_path) or "."
        os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS thumbnail_cache (
                path TEXT PRIMARY KEY,
                mtime REAL,
                width INTEGER,
                height INTEGER,
                hash TEXT,
                data BLOB
            )
        """)
        
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_thumb_mtime ON thumbnail_cache(mtime)")
        self.conn.commit()

    # -------------------------------------------------------
    def close(self):
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1)
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass

    # -------------------------------------------------------

    def compute_hash(self, path: str) -> str:
        try:
            st = os.stat(path)
            key = f"{path}:{st.st_size}:{st.st_mtime}"
            return hashlib.sha1(key.encode("utf-8")).hexdigest()
        except Exception:
            return hashlib.sha1(str(path).encode("utf-8")).hexdigest()

    # -------------------------------------------------------

    def get_cached_thumbnail(self, path: str, mtime: float = None, max_size: int = 512) -> QPixmap | None:
        """Retrieve thumbnail if present and valid. Uses normalized path and content hash."""
        start = time.time()
        try:
            npath = norm(path)
            with self.lock:
                cur = self.conn.cursor()
                # include stored mtime so we can inspect it if needed
                cur.execute("SELECT width, height, hash, data, mtime FROM thumbnail_cache WHERE path=?", (npath,))
                row = cur.fetchone()

            self.metrics["get_count"] += 1

            if not row:
                self.metrics["get_misses"] += 1
                return None

            width, height, hsh, blob, stored_mtime = row

            # validate via content signature (size+mtime) — robust against float formatting differences
            local_hash = self.compute_hash(path)
            if not hsh or hsh != local_hash:
                self.metrics["get_misses"] += 1
                return None

            img = QImage.fromData(blob)
            if img.isNull():
                self.metrics["get_misses"] += 1
                return None

            pm = QPixmap.fromImage(img)
            if max(pm.width(), pm.height()) > max_size:
                pm = pm.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

            self.metrics["get_hits"] += 1
            return pm
        except Exception as e:
            print(f"[ThumbCacheDB] get_cached_thumbnail failed: {e}")
            return None
        finally:
            self.metrics["get_total_ms"] += (time.time() - start) * 1000.0

    # -------------------------------------------------------

    def has_entry(self, path: str, mtime: float = None) -> bool:
        """
        Check whether we have a valid cache entry that matches current file content.
        Uses computed hash (size+mtime) to be robust against mtime formatting differences.
        """
        try:
            npath = norm(path)
            with self.lock:
                cur = self.conn.cursor()
                cur.execute("SELECT hash FROM thumbnail_cache WHERE path=?", (npath,))
                row = cur.fetchone()
                if not row:
                    return False
                stored_hash = row[0]
            local_hash = self.compute_hash(path)
            return stored_hash == local_hash
        except Exception:
            return False

   # -------------------------------------------------------
   
    def store_thumbnail(self, path: str, mtime: float, pixmap: QPixmap):
        """Store QPixmap thumbnail in cache DB with WEBP compression and PNG fallback."""
        start = time.time()
        try:
            npath = norm(path)
            if not isinstance(pixmap, QPixmap) or pixmap.isNull():
                return False

            img = pixmap.toImage()
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)

            # Try WEBP first, fallback to PNG
            ok = img.save(buffer, "WEBP", quality=85)
            if not ok:
                buffer.close()
                data.clear()
                buffer.open(QIODevice.WriteOnly)
                img.save(buffer, "PNG", quality=-1)
            buffer.close()

            hsh = self.compute_hash(path)
            blob_bytes = bytes(data) if isinstance(data, (bytes, bytearray)) else data.data()
            with self.lock:
                self.conn.execute("""
                    INSERT OR REPLACE INTO thumbnail_cache (path, mtime, width, height, hash, data)
                    VALUES (?,?,?,?,?,?)
                """, (npath, float(mtime or 0.0), int(img.width()), int(img.height()), hsh, sqlite3.Binary(blob_bytes)))
                self.conn.commit()

            self.metrics["stores"] += 1
            return True
        except Exception as e:
            print(f"[ThumbCacheDB] store_thumbnail failed: {e}")
            return False
        finally:
            self.metrics["store_total_ms"] += (time.time() - start) * 1000.0

   # -------------------------------------------------------

    def invalidate(self, path: str):
        npath = norm(path)
        try:
            with self.lock:
                self.conn.execute("DELETE FROM thumbnail_cache WHERE path=?", (npath,))
                self.conn.commit()
        except Exception:
            pass

   # -------------------------------------------------------
    def purge_stale(self, max_age_days: int = 30):
        try:
            cutoff = time.time() - max_age_days * 86400
            with self.lock:
                cur = self.conn.cursor()
                cur.execute("DELETE FROM thumbnail_cache WHERE mtime < ?", (cutoff,))
                n = cur.rowcount
                self.conn.commit()
            if n:
                print(f"[ThumbCacheDB] Purged {n} stale thumbnails (> {max_age_days} days).")
        except Exception as e:
            print(f"[ThumbCacheDB] purge_stale failed: {e}")

   # -------------------------------------------------------
    def get_stats(self) -> dict:
        try:
            with self.lock:
                cur = self.conn.cursor()
                cur.execute("SELECT COUNT(*), SUM(LENGTH(data)) FROM thumbnail_cache")
                count, total_bytes = cur.fetchone()
            total_bytes = total_bytes or 0
            mb = total_bytes / (1024 * 1024)
            last_mod = datetime.fromtimestamp(os.path.getmtime(self.db_path)).strftime("%Y-%m-%d %H:%M")
            return {
                "entries": count or 0,
                "size_mb": round(mb, 2),
                "path": self.db_path,
                "last_updated": last_mod
            }
        except Exception as e:
            return {"error": str(e)}
            
   # -------------------------------------------------------
   
    def get_metrics(self) -> dict:
        try:
            m = dict(self.metrics)
            m["avg_get_ms"] = (m["get_total_ms"] / m["get_count"]) if m["get_count"] else 0.0
            m["avg_store_ms"] = (m["store_total_ms"] / max(1, m["stores"])) if m["stores"] else 0.0
            return m
        except Exception as e:
            return {"error": str(e)}

    def _auto_purge_worker(self):
        last_run = 0
        while not self._stop_event.is_set():
            try:
                # check file size on disk
                size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
                if size_mb > MAX_CACHE_MB:
                    print(f"[ThumbCacheDB] Auto-purging: cache {size_mb:.1f} MB > limit {MAX_CACHE_MB} MB")
                    self.purge_stale(max_age_days=7)
                # weekly cleanup
                if time.time() - last_run > PURGE_INTERVAL_DAYS * 86400:
                    self.purge_stale(max_age_days=30)
                    last_run = time.time()
            except Exception as e:
                print(f"[ThumbCacheDB] Auto-purge thread error: {e}")
            try:
                self._stop_event.wait(timeout=6 * 3600)
            except (OSError, TypeError, AttributeError):
                # Event object may be torn down during interpreter shutdown
                break

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
            
   # -------------------------------------------------------


# ===========================================================
# 🔗 Global helper
# ===========================================================


import atexit

_global_cache = None

def get_cache() -> ThumbCacheDB:
    global _global_cache
    if _global_cache is None:
        _global_cache = ThumbCacheDB()
    return _global_cache


# ✅ graceful cleanup at app exit
def _shutdown_cache():
    global _global_cache
    if _global_cache:
        print("[ThumbCacheDB] Closing cache gracefully...")
        _global_cache.close()
        _global_cache = None

atexit.register(_shutdown_cache)
