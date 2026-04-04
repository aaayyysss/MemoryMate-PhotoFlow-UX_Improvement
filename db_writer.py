# db_writer.py
# Version 01.01.01.06 dated 20251102
# DBWriter: single-writer SQLite queue, runs in its own QThread and commits batches.
# Writes now populate created_ts/created_date/created_year so date branches can be built.
# Uses centralized logging and MetadataService for date parsing.

import queue
import threading
import traceback
from typing import List, Tuple, Any, Optional

from PySide6.QtCore import QObject, Signal, QThread, QTimer, Slot
from reference_db import ReferenceDB
from logging_config import get_logger
from services import MetadataService

# Module logger
logger = get_logger(__name__)

# Shared metadata service instance for date parsing
_metadata_service = MetadataService()

# UpsertRow: (path, folder_id, size_kb, modified, width, height, date_taken, tags)
UpsertRow = Tuple[str, Optional[int], Optional[float], Optional[str], Optional[int], Optional[int], Optional[str], Optional[str]]


def _compute_created_fields(date_taken: Optional[str], modified: Optional[str]):
    """
    Return (created_ts:int|None, created_date:'YYYY-MM-DD'|None, created_year:int|None).
    Try date_taken first (EXIF-like), else modified.

    Now uses MetadataService for consistent date parsing across the application.
    """
    return _metadata_service.compute_created_fields_from_dates(date_taken, modified)


class DBWriter(QObject):
    """Single-threaded DB writer that accepts enqueued work from any thread.

    - Use enqueue_upserts(rows) to queue up rows.
    - Use enqueue_shutdown() to request a clean shutdown (flush + quit) from the writer thread.
    """
    error = Signal(str)
    started = Signal()
    stopped = Signal()
    committed = Signal(int)   # ✅ emits number of rows committed after each batch    

    def __init__(self, batch_size: int = 500, poll_interval_ms: int = 100):
        super().__init__()
        self._queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._batch_size = int(batch_size)
        self._poll_interval_ms = int(poll_interval_ms)

        self._thread: Optional[QThread] = None
        self._timer: Optional[QTimer] = None
        self._running = False
        self._lock = threading.Lock()
        self._shutting_down = False

    def start(self):
        """Start the writer in its own QThread. Safe to call from main thread."""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._start_timer)
        self._thread.start()

    @Slot()
    def _start_timer(self):
        """Create a QTimer inside the writer thread to periodically process the queue."""
        try:
            self._timer = QTimer()
            self._timer.setInterval(self._poll_interval_ms)
            self._timer.timeout.connect(self._process_queued_items)
            self._timer.start()
            logger.info("DBWriter timer started in worker thread")
            self.started.emit()
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Failed to start timer: {e}", exc_info=True)
            self.error.emit(f"DBWriter failed to start timer: {e}\n{tb}")

    def enqueue_upserts(self, rows: List[UpsertRow]):
        """Enqueue many upsert rows as a single message. Safe to call from any thread."""
        if not rows:
            return
        self._queue.put(("upsert_batch", rows))
        # lightweight log for diagnostics:
        try:
            qsize = self._queue.qsize()
            if qsize % 50 == 0:
                logger.debug(f"Queue size: {qsize} batches pending")
        except Exception:
            pass

    def enqueue_message(self, msg_type: str, payload: Any = None):
        """General enqueue (low-level)."""
        self._queue.put((msg_type, payload))

    def enqueue_shutdown(self):
        """Request writer to flush remaining items and shut itself down (processed on writer thread)."""
        self._queue.put(("shutdown", None))

    @Slot()
    def _process_queued_items(self):
        """
        Runs on the writer thread (QTimer). Drains messages and performs batched DB writes.
        Also handles a 'shutdown' message to cleanly stop the timer and quit the thread from inside.
        """
        try:
            if self._queue.empty():
                return

            collected: List[UpsertRow] = []
            shutdown_seen = False

            try:
                while not self._queue.empty() and len(collected) < self._batch_size:
                    msg_type, payload = self._queue.get_nowait()
                    if msg_type == "upsert_batch":
                        if isinstance(payload, list):
                            collected.extend(payload)
                        else:
                            collected.append(payload)
                    elif msg_type == "shutdown":
                        shutdown_seen = True
                    else:
                        pass
            except Exception:
                pass

            if collected:
                start = 0
                while start < len(collected):
                    chunk = collected[start : start + self._batch_size]
                    self._do_upsert_chunk(chunk)
                    start += self._batch_size

            if shutdown_seen:
                remaining: List[UpsertRow] = []
                try:
                    while not self._queue.empty():
                        msg_type, payload = self._queue.get_nowait()
                        if msg_type == "upsert_batch":
                            if isinstance(payload, list):
                                remaining.extend(payload)
                            else:
                                remaining.append(payload)
                except Exception:
                    pass

                if remaining:
                    start = 0
                    while start < len(remaining):
                        chunk = remaining[start : start + self._batch_size]
                        self._do_upsert_chunk(chunk)
                        start += self._batch_size

                try:
                    if self._timer and self._timer.isActive():
                        self._timer.stop()
                except Exception:
                    pass

                try:
                    thr = self._thread
                    if thr:
                        thr.quit()
                except Exception:
                    pass

                try:
                    self.stopped.emit()
                except Exception:
                    pass

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Queue processing error: {e}", exc_info=True)
            self.error.emit(f"DBWriter processing error: {e}\n{tb}")

    def _photo_metadata_has_created_cols(self, conn) -> bool:
        """Return True if photo_metadata has created_ts/created_date/created_year."""
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {r[1] for r in cur.fetchall()}
            return {"created_ts", "created_date", "created_year"}.issubset(cols)
        except Exception:
            return False

    def _do_upsert_chunk(self, rows: List[UpsertRow]):
        """Perform a single upsert transaction for provided rows.

        This method inspects the current DB schema and chooses a SQL form that matches
        available columns. If a chunk fails due to schema mismatch, it will retry with
        the legacy SQL (without created_*).
        """
        if not rows:
            return

        db = ReferenceDB()

        # Precompute params for both variants.
        now = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
        params_with_created = []
        params_legacy = []

        for r in rows:
            try:
                path = str(r[0])
                folder_id = int(r[1]) if r[1] is not None else None
                size_kb = float(r[2]) if r[2] is not None else None
                modified = str(r[3]) if r[3] is not None else None
                width = int(r[4]) if r[4] is not None else None
                height = int(r[5]) if r[5] is not None else None
                date_taken = str(r[6]) if r[6] is not None else None
                tags = str(r[7]) if r[7] is not None else None

                c_ts, c_date, c_year = _compute_created_fields(date_taken, modified)
                params_with_created.append((path, folder_id, size_kb, modified, width, height, date_taken, tags, now, c_ts, c_date, c_year))
                params_legacy.append((path, folder_id, size_kb, modified, width, height, date_taken, tags, now))
            except Exception:
                continue

        if not params_with_created and not params_legacy:
            return

        # SQL templates
        sql_with_created = """
            INSERT INTO photo_metadata
                (path, folder_id, size_kb, modified, width, height, date_taken, tags, updated_at,
                 created_ts, created_date, created_year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                folder_id = excluded.folder_id,
                size_kb   = excluded.size_kb,
                modified  = excluded.modified,
                width     = excluded.width,
                height    = excluded.height,
                date_taken= excluded.date_taken,
                tags      = excluded.tags,
                updated_at= excluded.updated_at,
                created_ts   = COALESCE(excluded.created_ts, created_ts),
                created_date = COALESCE(excluded.created_date, created_date),
                created_year = COALESCE(excluded.created_year, created_year)
        """

        sql_legacy = """
            INSERT INTO photo_metadata
                (path, folder_id, size_kb, modified, width, height, date_taken, tags, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                folder_id = excluded.folder_id,
                size_kb   = excluded.size_kb,
                modified  = excluded.modified,
                width     = excluded.width,
                height    = excluded.height,
                date_taken= excluded.date_taken,
                tags      = excluded.tags,
                updated_at= excluded.updated_at
        """

        try:
            with db.get_connection() as conn:
                has_created = self._photo_metadata_has_created_cols(conn)

                cur = conn.cursor()
                if has_created:
                    try:
                        cur.executemany(sql_with_created, params_with_created)
                        logger.info(f"Committed {len(params_with_created)} rows (with created_* fields)")
                        self.committed.emit(len(params_with_created))
                        return
                    except Exception as e:
                        tb = traceback.format_exc()
                        logger.warning(f"Upsert with created_* fields failed, falling back to legacy: {e}")
                        conn.rollback()
                        # fall through to legacy attempt
                # legacy attempt
                try:
                    cur.executemany(sql_legacy, params_legacy)
                    logger.info(f"Committed {len(params_legacy)} rows (legacy mode)")
                    self.committed.emit(len(params_legacy))
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(f"Legacy upsert failed: {e}", exc_info=True)
                    conn.rollback()
                    self.error.emit(f"DBWriter upsert failed: {e}\n{tb}")

        except Exception as e:
            tb = traceback.format_exc()
            logger.critical(f"Unexpected error during upsert: {e}", exc_info=True)
            self.error.emit(f"DBWriter upsert failed: {e}\n{tb}")

    def shutdown(self, wait: bool = True, timeout_ms: int = 5000):
        """
        Request a shutdown. This enqueues a 'shutdown' marker that will be processed
        on the writer thread. Optionally waits for the writer thread to finish.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        try:
            self.enqueue_message("shutdown", None)
        except Exception:
            pass

        try:
            if self._thread and wait:
                self._thread.wait(timeout_ms)
        except Exception:
            pass