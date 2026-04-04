"""
HashBackfillWorker - Background Hash Computation and Asset Linking

Version: 01.00.00.00
Date: 2026-01-15

Qt QRunnable worker that backfills file_hash for legacy photos and links them
to media_asset/media_instance tables. Part of asset-centric duplicate management.

Architecture:
    JobService.enqueue_job('hash_backfill', {'project_id': 1})
        → HashBackfillWorker claims job
        → Computes SHA256 for photos without file_hash
        → Creates media_asset for unique content_hash
        → Links media_instance to photo_metadata
        → Sends progress updates
        → Completes job

Usage:
    from workers.hash_backfill_worker import HashBackfillWorker
    from PySide6.QtCore import QThreadPool

    # Create worker
    worker = HashBackfillWorker(
        job_id=123,
        project_id=1,
        batch_size=500,
        stop_after=None  # Process all photos
    )

    # Connect signals
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.error.connect(on_error)

    # Start in thread pool
    QThreadPool.globalInstance().start(worker)
"""

import time
from typing import Optional
from PySide6.QtCore import QRunnable, QObject, Signal

from services.job_service import get_job_service
from services.asset_service import AssetService
from repository.photo_repository import PhotoRepository
from repository.asset_repository import AssetRepository
from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


class HashBackfillWorkerSignals(QObject):
    """
    Signals for HashBackfillWorker.

    Qt signals must be defined in a QObject, not QRunnable.
    """
    # Progress: (current, total, message)
    progress = Signal(int, int, str)

    # Finished: (scanned, hashed, linked, errors)
    finished = Signal(int, int, int, int)

    # Error: (error_message)
    error = Signal(str)


class HashBackfillWorker(QRunnable):
    """
    QRunnable worker for background hash backfill and asset linking.

    This worker:
    1. Claims a job from JobService
    2. Fetches photos without media_instance
    3. Computes SHA256 hash if missing
    4. Creates or links media_asset
    5. Links media_instance to photo
    6. Sends progress updates via heartbeat
    7. Handles errors gracefully

    The worker is designed to be:
    - Resumable: Can be stopped and restarted
    - Idempotent: Safe to run multiple times
    - Observable: Emits progress signals
    - Crash-safe: Uses JobService lease/heartbeat pattern
    """

    def __init__(
        self,
        job_id: int,
        project_id: int,
        batch_size: int = 500,
        stop_after: Optional[int] = None,
        db_path: Optional[str] = None
    ):
        """
        Initialize HashBackfillWorker.

        Args:
            job_id: Job ID from JobService
            project_id: Project ID to process
            batch_size: Number of photos per batch
            stop_after: Optional limit for testing
            db_path: Optional database path (defaults to singleton)
        """
        super().__init__()
        self.job_id = job_id
        self.project_id = project_id
        self.batch_size = batch_size
        self.stop_after = stop_after
        self.db_path = db_path

        self.signals = HashBackfillWorkerSignals()

        # Will be initialized in run()
        self.job_service = None
        self.asset_service = None
        self.worker_id = f"hash_backfill_{int(time.time())}"

    def run(self):
        """
        Main worker execution method.

        Called by QThreadPool when worker starts.
        """
        try:
            # Initialize services
            self._init_services()

            # Claim job (transitions from 'queued' to 'running')
            if not self.job_service.claim_job(self.job_id, self.worker_id):
                logger.warning(f"[HashBackfill] Job {self.job_id} already claimed or not queued")
                return

            logger.info(f"[HashBackfill] Starting backfill for project {self.project_id}")

            # Run backfill with progress callback
            def progress_callback(current: int, total: int):
                """Progress callback for asset_service."""
                progress_pct = (current / total * 100) if total > 0 else 0
                progress_float = current / total if total > 0 else 0.0
                message = f"Processing {current}/{total} ({progress_pct:.1f}%)"

                # Emit progress signal
                self.signals.progress.emit(current, total, message)

                # Send heartbeat to JobService (progress must be 0.0-1.0)
                self.job_service.heartbeat(self.job_id, progress_float)

            # Execute backfill
            stats = self.asset_service.backfill_hashes_and_link_assets(
                project_id=self.project_id,
                batch_size=self.batch_size,
                stop_after=self.stop_after,
                progress_callback=progress_callback
            )

            # Mark job as complete
            result_message = (
                f"Backfill complete: {stats.scanned} scanned, {stats.hashed} hashed, "
                f"{stats.linked} linked, {stats.errors} errors"
            )
            self.job_service.complete_job(self.job_id, success=True)

            logger.info(f"[HashBackfill] {result_message}")

            # Emit finished signal
            self.signals.finished.emit(stats.scanned, stats.hashed, stats.linked, stats.errors)

        except Exception as e:
            error_msg = f"Hash backfill failed: {str(e)}"
            logger.error(f"[HashBackfill] {error_msg}", exc_info=True)

            # Mark job as failed
            try:
                self.job_service.complete_job(self.job_id, success=False, error=error_msg)
            except Exception:
                pass

            # Emit error signal
            self.signals.error.emit(error_msg)

    def _init_services(self):
        """
        Initialize services (called in worker thread).

        Services must be initialized in the worker thread, not main thread,
        to avoid database connection issues.
        """
        # Initialize database connection
        if self.db_path:
            db_conn = DatabaseConnection(self.db_path)
        else:
            db_conn = DatabaseConnection()

        # Initialize repositories
        photo_repo = PhotoRepository(db_conn)
        asset_repo = AssetRepository(db_conn)

        # Initialize services
        self.asset_service = AssetService(photo_repo, asset_repo)
        self.job_service = get_job_service()

        logger.debug("[HashBackfill] Services initialized")


def create_hash_backfill_worker(
    job_id: int,
    project_id: int,
    batch_size: int = 500,
    stop_after: Optional[int] = None
) -> HashBackfillWorker:
    """
    Factory function to create HashBackfillWorker.

    Args:
        job_id: Job ID from JobService
        project_id: Project ID to process
        batch_size: Photos per batch
        stop_after: Optional limit for testing

    Returns:
        HashBackfillWorker instance
    """
    return HashBackfillWorker(
        job_id=job_id,
        project_id=project_id,
        batch_size=batch_size,
        stop_after=stop_after
    )
