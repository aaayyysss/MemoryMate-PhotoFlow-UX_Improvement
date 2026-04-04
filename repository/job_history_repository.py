"""
JobHistoryRepository - Lightweight Persistence for Tracked Job History

Stores start, progress, finish status, and result JSON for every tracked
job so the Activity Center can show a History tab.

Schema is self-healing (CREATE TABLE IF NOT EXISTS).
Auto-prunes to keep only the most recent N rows.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from repository.base_repository import DatabaseConnection
from db_config import get_db_path


_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS job_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL,
    job_type    TEXT    NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'running',
    created_ts  REAL    NOT NULL,
    started_ts  REAL,
    finished_ts REAL,
    progress    REAL    DEFAULT 0.0,
    result_json TEXT,
    error       TEXT,
    canceled    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_job_history_created_ts
    ON job_history(created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_job_history_job_id
    ON job_history(job_id);
"""


@dataclass
class JobHistoryRow:
    """Read-only projection of a single job_history row."""
    job_id: str
    job_type: str
    title: str
    status: str
    created_ts: float
    started_ts: Optional[float] = None
    finished_ts: Optional[float] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    canceled: int = 0


class JobHistoryRepository:
    """
    Lightweight persistence for JobManager tracked jobs.

    Designed for UI history display, not for high-frequency progress writes.
    All writes are fire-and-forget with exception handling in the caller
    (JobManager) so DB issues never break scan/face pipelines.
    """

    # Maximum rows to keep; older rows are auto-pruned after finish()
    MAX_ROWS = 500

    def __init__(self, db_path: Optional[str] = None):
        self._db = DatabaseConnection(db_path or get_db_path(), auto_init=False)
        self._ensure()

    def _ensure(self) -> None:
        """Create the job_history table if it does not exist."""
        with self._db.get_connection() as conn:
            conn.executescript(_CREATE_SQL)
            conn.commit()

    # ── Writes ───────────────────────────────────────────────────────────

    def upsert_start(self, *, job_id: str, job_type: str, title: str) -> None:
        """Record that a tracked job has started."""
        now = time.time()
        with self._db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO job_history
                    (job_id, job_type, title, status, created_ts, started_ts, progress)
                VALUES (?, ?, ?, 'running', ?, ?, 0.0)
                """,
                (job_id, job_type, title, now, now),
            )
            conn.commit()

    def update_progress(self, *, job_id: str, progress: float) -> None:
        """Update the progress fraction (0.0 – 1.0) for a running job.

        Retries with exponential backoff to handle transient 'database is
        locked' errors when face-clustering or other workers hold long
        write transactions.
        """
        import sqlite3 as _sqlite3
        import time as _time

        backoff = [0.2, 0.5, 1.0, 2.0]  # 4 retries, up to ~3.7s total wait
        for attempt in range(len(backoff) + 1):
            try:
                with self._db.get_connection() as conn:
                    conn.execute(
                        "UPDATE job_history SET progress = ? WHERE job_id = ? AND status = 'running'",
                        (float(progress), job_id),
                    )
                    conn.commit()
                return
            except _sqlite3.OperationalError:
                if attempt < len(backoff):
                    _time.sleep(backoff[attempt])
                # Final attempt failure is swallowed by caller (JobManager)

    def finish(
        self,
        *,
        job_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        canceled: int = 0,
    ) -> None:
        """Mark a job as finished (succeeded / failed / canceled)."""
        now = time.time()
        result_json = json.dumps(result) if result is not None else None
        with self._db.get_connection() as conn:
            conn.execute(
                """
                UPDATE job_history
                   SET status      = ?,
                       finished_ts = ?,
                       progress    = COALESCE(progress, 1.0),
                       result_json = ?,
                       error       = ?,
                       canceled    = ?
                 WHERE job_id = ?
                """,
                (status, now, result_json, error, int(canceled), job_id),
            )
            conn.commit()
        # Auto-prune old rows
        self._prune()

    # ── Reads ────────────────────────────────────────────────────────────

    def list_recent(self, limit: int = 200) -> List[JobHistoryRow]:
        """Return the most recent job history rows, newest first."""
        with self._db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT job_id, job_type, title, status, created_ts,
                       started_ts, finished_ts, progress, result_json,
                       error, canceled
                  FROM job_history
                 ORDER BY created_ts DESC
                 LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

        out: List[JobHistoryRow] = []
        for r in rows:
            result = None
            if r["result_json"]:
                try:
                    result = json.loads(r["result_json"])
                except Exception:
                    result = None
            out.append(
                JobHistoryRow(
                    job_id=r["job_id"],
                    job_type=r["job_type"],
                    title=r["title"],
                    status=r["status"],
                    created_ts=float(r["created_ts"]),
                    started_ts=float(r["started_ts"]) if r["started_ts"] else None,
                    finished_ts=float(r["finished_ts"]) if r["finished_ts"] else None,
                    progress=float(r["progress"]) if r["progress"] is not None else None,
                    result=result,
                    error=r["error"],
                    canceled=int(r["canceled"] or 0),
                )
            )
        return out

    def clear_all(self) -> None:
        """Delete all job history rows."""
        with self._db.get_connection() as conn:
            conn.execute("DELETE FROM job_history")
            conn.commit()

    # ── Retention ────────────────────────────────────────────────────────

    def _prune(self) -> None:
        """Keep only the most recent MAX_ROWS rows."""
        with self._db.get_connection() as conn:
            conn.execute(
                """
                DELETE FROM job_history
                 WHERE id NOT IN (
                    SELECT id FROM job_history
                     ORDER BY created_ts DESC
                     LIMIT ?
                 )
                """,
                (self.MAX_ROWS,),
            )
            conn.commit()
