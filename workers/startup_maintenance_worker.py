"""
StartupMaintenanceWorker - Background worker for heavy DB operations at startup.

Moves database maintenance (backfill, index optimization) out of the GUI thread
to prevent UI freeze during app startup.

Following Material Design principle: App should be responsive immediately,
heavy work happens visibly in the background via Activity Center.
"""

from PySide6.QtCore import QObject, Signal
from logging_config import get_logger

logger = get_logger(__name__)


class StartupMaintenanceWorker(QObject):
    """
    Worker that performs startup database maintenance in background.

    Operations:
    - Backfill created_* fields for legacy photos
    - Optimize database indexes
    - Any other heavy startup tasks

    Emits progress signals for Activity Center integration.
    """

    # Signals for job manager integration
    started = Signal()
    progress = Signal(int, int, str)  # current, total, message
    finished = Signal(bool, str)  # success, message
    error = Signal(str)

    def __init__(self, db_path: str = None, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._cancelled = False

    def cancel(self):
        """Request cancellation (checked between operations)."""
        self._cancelled = True

    def run(self):
        """
        Execute maintenance tasks in background thread.

        Called by job manager in a worker thread.
        """
        self.started.emit()
        logger.info("[StartupMaintenance] Starting background maintenance...")

        try:
            from reference_db import ReferenceDB

            # Step 1: Initialize DB handle (fast)
            self.progress.emit(0, 3, "Connecting to database...")
            db = ReferenceDB(self.db_path) if self.db_path else ReferenceDB()

            if self._cancelled:
                self.finished.emit(False, "Cancelled")
                return

            # Step 2: Backfill legacy rows (can be slow for large DBs)
            self.progress.emit(1, 3, "Backfilling date fields...")
            try:
                updated_rows = db.single_pass_backfill_created_fields()
                if updated_rows:
                    logger.info(f"[StartupMaintenance] Backfilled {updated_rows} legacy rows")
            except Exception as e:
                logger.warning(f"[StartupMaintenance] Backfill skipped: {e}")

            if self._cancelled:
                self.finished.emit(False, "Cancelled")
                return

            # Step 3: Optimize indexes (important for performance)
            self.progress.emit(2, 3, "Optimizing indexes...")
            try:
                db.optimize_indexes()
                logger.info("[StartupMaintenance] Database indexes optimized")
            except Exception as e:
                logger.warning(f"[StartupMaintenance] Index optimization skipped: {e}")

            # Complete
            self.progress.emit(3, 3, "Maintenance complete")
            self.finished.emit(True, "Database maintenance completed")
            logger.info("[StartupMaintenance] Background maintenance completed successfully")

        except Exception as e:
            logger.exception("[StartupMaintenance] Maintenance failed")
            self.error.emit(str(e))
            self.finished.emit(False, f"Maintenance failed: {e}")
