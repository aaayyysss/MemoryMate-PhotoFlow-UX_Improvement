# workers/group_index_worker.py
# Background worker for computing group photo matches
# Version: 1.0.0

"""
GroupIndexWorker - Background indexing for person groups

Computes "Together (AND)" matches for person groups:
- Runs in QThreadPool for non-blocking UI
- Emits progress signals for Activity Center integration
- Stores results in group_asset_matches cache table

Based on patterns from face_detection_worker.py and face_pipeline_worker.py.
"""

from __future__ import annotations

import time
import traceback
from typing import Optional, List

from PySide6.QtCore import QObject, QRunnable, Signal

from logging_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# SIGNALS
# ============================================================================

class GroupIndexSignals(QObject):
    """
    Signals for GroupIndexWorker progress reporting.

    Signal Contract:
    - started(group_id: int, project_id: int, scope: str)
        Emitted when indexing begins

    - progress(group_id: int, current: int, total: int, message: str)
        Emitted during processing (current/total for progress bar)

    - batch_committed(group_id: int, processed: int, total: int)
        Emitted after each batch is saved to database

    - completed(group_id: int, project_id: int, photo_count: int)
        Emitted when indexing completes successfully

    - error(group_id: int, project_id: int, error_message: str)
        Emitted when indexing fails
    """
    started = Signal(int, int, str)                 # (group_id, project_id, scope)
    progress = Signal(int, int, int, str)           # (group_id, current, total, message)
    batch_committed = Signal(int, int, int)         # (group_id, processed, total)
    completed = Signal(int, int, int)               # (group_id, project_id, photo_count)
    error = Signal(int, int, str)                   # (group_id, project_id, error_message)


# ============================================================================
# WORKER
# ============================================================================

class GroupIndexWorker(QRunnable):
    """
    Background worker for computing group photo matches.

    Usage:
        worker = GroupIndexWorker(
            project_id=1,
            group_id=42,
            scope="same_photo"
        )
        worker.signals.progress.connect(on_progress)
        worker.signals.completed.connect(on_completed)
        QThreadPool.globalInstance().start(worker)
    """

    def __init__(
        self,
        project_id: int,
        group_id: int,
        scope: str = "same_photo",
        batch_size: int = 500
    ):
        """
        Initialize GroupIndexWorker.

        Args:
            project_id: Project to query
            group_id: Group to index
            scope: "same_photo" or "event_window"
            batch_size: Number of results to write per batch
        """
        super().__init__()
        self.project_id = project_id
        self.group_id = group_id
        self.scope = scope
        self.batch_size = batch_size

        self.signals = GroupIndexSignals()
        self._cancelled = False
        self._db = None

    def cancel(self):
        """Request cancellation of the indexing job."""
        self._cancelled = True
        logger.info(f"[GroupIndexWorker] Cancellation requested for group {self.group_id}")

    def run(self):
        """
        Execute the indexing job.

        Workflow:
        1. Load group members
        2. Compute matching photos using SQL JOIN
        3. Store results in group_asset_matches cache
        4. Update job status
        """
        try:
            self.signals.started.emit(self.group_id, self.project_id, self.scope)
            logger.info(f"[GroupIndexWorker] Starting index for group {self.group_id}, scope={self.scope}")

            # Initialize database
            from reference_db import ReferenceDB
            self._db = ReferenceDB()

            # Step 1: Get group members
            self.signals.progress.emit(self.group_id, 0, 100, "Loading group members...")

            member_ids = self._get_group_members()
            if not member_ids:
                self.signals.error.emit(
                    self.group_id, self.project_id,
                    "No members found in group"
                )
                return

            if len(member_ids) < 2:
                self.signals.error.emit(
                    self.group_id, self.project_id,
                    "Group must have at least 2 members"
                )
                return

            logger.info(f"[GroupIndexWorker] Group {self.group_id} has {len(member_ids)} members")

            # Step 2: Compute matches
            self.signals.progress.emit(self.group_id, 10, 100, "Computing photo matches...")

            if self._cancelled:
                self._handle_cancellation()
                return

            photo_ids = self._compute_matches(member_ids)

            if self._cancelled:
                self._handle_cancellation()
                return

            logger.info(f"[GroupIndexWorker] Found {len(photo_ids)} matching photos")

            # Step 3: Store results in batches
            self.signals.progress.emit(self.group_id, 50, 100, "Caching results...")

            self._store_results(photo_ids)

            if self._cancelled:
                self._handle_cancellation()
                return

            # Step 4: Update job status
            self.signals.progress.emit(self.group_id, 90, 100, "Finalizing...")
            self._update_job_status("completed")

            # Done
            self.signals.progress.emit(self.group_id, 100, 100, "Complete")
            self.signals.completed.emit(self.group_id, self.project_id, len(photo_ids))

            logger.info(f"[GroupIndexWorker] Completed indexing group {self.group_id}: {len(photo_ids)} photos")

        except Exception as e:
            error_msg = f"Indexing failed: {str(e)}"
            logger.error(f"[GroupIndexWorker] {error_msg}", exc_info=True)
            traceback.print_exc()

            self._update_job_status("failed", error_msg)
            self.signals.error.emit(self.group_id, self.project_id, error_msg)

        finally:
            if self._db:
                try:
                    self._db.close()
                except Exception:
                    pass
                self._db = None

    def _get_group_members(self) -> List[str]:
        """Get branch_keys for group members."""
        try:
            with self._db._connect() as conn:
                cur = conn.execute(
                    "SELECT person_id FROM person_group_members WHERE group_id = ?",
                    (self.group_id,)
                )
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"[GroupIndexWorker] Failed to get members: {e}")
            return []

    def _compute_matches(self, member_ids: List[str]) -> List[int]:
        """
        Compute matching photo IDs using SQL.

        Same Photo Scope:
            Find photos where ALL group members have a face detected.

        Event Window Scope:
            Find all photos in events where ALL group members appear
            (across any photos in that event).
        """
        try:
            member_count = len(member_ids)

            with self._db._connect() as conn:
                if self.scope == "same_photo":
                    # Together (AND) query
                    # Using a CTE for the member list and JOIN to face_crops
                    cur = conn.execute(f"""
                        WITH members AS (
                            SELECT person_id FROM person_group_members WHERE group_id = ?
                        )
                        SELECT pm.id
                        FROM photo_metadata pm
                        JOIN face_crops fc ON fc.image_path = pm.path AND fc.project_id = pm.project_id
                        JOIN members m ON m.person_id = fc.branch_key
                        WHERE pm.project_id = ?
                        GROUP BY pm.id
                        HAVING COUNT(DISTINCT fc.branch_key) = ?
                        ORDER BY pm.created_ts DESC
                    """, (self.group_id, self.project_id, member_count))

                elif self.scope == "event_window":
                    # Event window query
                    # First find events where all members appear, then get all photos from those events
                    cur = conn.execute(f"""
                        WITH members AS (
                            SELECT person_id FROM person_group_members WHERE group_id = ?
                        ),
                        events_with_all_members AS (
                            SELECT pe.event_id
                            FROM face_crops fc
                            JOIN photo_metadata pm ON pm.path = fc.image_path AND pm.project_id = fc.project_id
                            JOIN photo_events pe ON pe.project_id = pm.project_id AND pe.photo_id = pm.id
                            JOIN members m ON m.person_id = fc.branch_key
                            WHERE fc.project_id = ?
                            GROUP BY pe.event_id
                            HAVING COUNT(DISTINCT fc.branch_key) = ?
                        )
                        SELECT pm.id
                        FROM photo_events pe
                        JOIN events_with_all_members e ON e.event_id = pe.event_id
                        JOIN photo_metadata pm ON pm.id = pe.photo_id
                        WHERE pe.project_id = ?
                        ORDER BY pm.created_ts DESC
                    """, (self.group_id, self.project_id, member_count, self.project_id))

                else:
                    logger.error(f"[GroupIndexWorker] Unknown scope: {self.scope}")
                    return []

                return [row[0] for row in cur.fetchall()]

        except Exception as e:
            logger.error(f"[GroupIndexWorker] Query failed: {e}", exc_info=True)
            return []

    def _store_results(self, photo_ids: List[int]):
        """Store match results in batches."""
        try:
            now = int(time.time())
            total = len(photo_ids)

            with self._db._connect() as conn:
                # Clear old cache for this group+scope
                conn.execute(
                    "DELETE FROM group_asset_matches WHERE group_id = ? AND scope = ?",
                    (self.group_id, self.scope)
                )

                # Insert in batches
                for i in range(0, total, self.batch_size):
                    if self._cancelled:
                        conn.rollback()
                        return

                    batch = photo_ids[i:i + self.batch_size]

                    for photo_id in batch:
                        conn.execute("""
                            INSERT INTO group_asset_matches (
                                project_id, group_id, scope, photo_id, computed_at
                            ) VALUES (?, ?, ?, ?, ?)
                        """, (self.project_id, self.group_id, self.scope, photo_id, now))

                    conn.commit()

                    # Emit progress
                    processed = min(i + len(batch), total)
                    progress_pct = 50 + int(40 * processed / max(total, 1))
                    self.signals.progress.emit(
                        self.group_id, progress_pct, 100,
                        f"Cached {processed}/{total} matches"
                    )
                    self.signals.batch_committed.emit(self.group_id, processed, total)

                conn.commit()

        except Exception as e:
            logger.error(f"[GroupIndexWorker] Failed to store results: {e}", exc_info=True)
            raise

    def _update_job_status(self, status: str, error_message: Optional[str] = None):
        """Update job status in database."""
        try:
            now = int(time.time())

            with self._db._connect() as conn:
                if status == "completed":
                    conn.execute("""
                        UPDATE group_index_jobs
                        SET status = 'completed', completed_at = ?, progress = 1.0
                        WHERE group_id = ? AND scope = ? AND status IN ('pending', 'running')
                    """, (now, self.group_id, self.scope))
                elif status == "failed":
                    conn.execute("""
                        UPDATE group_index_jobs
                        SET status = 'failed', completed_at = ?, error_message = ?
                        WHERE group_id = ? AND scope = ? AND status IN ('pending', 'running')
                    """, (now, error_message, self.group_id, self.scope))
                elif status == "cancelled":
                    conn.execute("""
                        UPDATE group_index_jobs
                        SET status = 'cancelled', completed_at = ?
                        WHERE group_id = ? AND scope = ? AND status IN ('pending', 'running')
                    """, (now, self.group_id, self.scope))

                conn.commit()

        except Exception as e:
            logger.error(f"[GroupIndexWorker] Failed to update job status: {e}")

    def _handle_cancellation(self):
        """Handle job cancellation."""
        logger.info(f"[GroupIndexWorker] Job cancelled for group {self.group_id}")
        self._update_job_status("cancelled")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def start_group_index_job(
    project_id: int,
    group_id: int,
    scope: str = "same_photo",
    on_progress=None,
    on_completed=None,
    on_error=None
) -> GroupIndexWorker:
    """
    Start a group indexing job on the global thread pool.

    Args:
        project_id: Project ID
        group_id: Group to index
        scope: "same_photo" or "event_window"
        on_progress: Callback for progress updates
        on_completed: Callback when complete
        on_error: Callback on error

    Returns:
        The worker instance (for cancellation if needed)
    """
    from PySide6.QtCore import QThreadPool

    worker = GroupIndexWorker(project_id, group_id, scope)

    if on_progress:
        worker.signals.progress.connect(on_progress)
    if on_completed:
        worker.signals.completed.connect(on_completed)
    if on_error:
        worker.signals.error.connect(on_error)

    QThreadPool.globalInstance().start(worker)
    return worker
