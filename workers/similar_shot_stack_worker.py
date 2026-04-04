"""
SimilarShotStackWorker - Background Similar Shot Stack Generation

Version: 01.00.00.00
Date: 2026-01-15

Qt QRunnable worker that generates similar shot stacks using time proximity
and visual similarity (embedding-based). Part of Phase 2 duplicate management.

Architecture:
    JobService.enqueue_job('similar_shot_stacks', {'project_id': 1})
        → SimilarShotStackWorker claims job
        → Loads photos with timestamps and embeddings
        → Finds time candidates and clusters by similarity
        → Creates stacks with representatives
        → Sends progress updates
        → Completes job

Usage:
    from workers.similar_shot_stack_worker import SimilarShotStackWorker
    from PySide6.QtCore import QThreadPool

    # Create worker
    worker = SimilarShotStackWorker(
        job_id=123,
        project_id=1,
        time_window_seconds=10,
        similarity_threshold=0.92,
        min_stack_size=3
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
from services.stack_generation_service import StackGenerationService, StackGenParams
from repository.photo_repository import PhotoRepository
from repository.stack_repository import StackRepository
from repository.project_repository import ProjectRepository
from repository.base_repository import DatabaseConnection
from services.semantic_embedding_service import SemanticEmbeddingService
from logging_config import get_logger

logger = get_logger(__name__)


class SimilarShotStackWorkerSignals(QObject):
    """
    Signals for SimilarShotStackWorker.

    Qt signals must be defined in a QObject, not QRunnable.
    """
    # Progress: (current, total, message)
    progress = Signal(int, int, str)

    # Finished: (photos_considered, stacks_created, memberships_created, errors)
    finished = Signal(int, int, int, int)

    # Error: (error_message)
    error = Signal(str)


class SimilarShotStackWorker(QRunnable):
    """
    QRunnable worker for background similar shot stack generation.

    This worker:
    1. Claims a job from JobService
    2. Loads photos with timestamps and embeddings
    3. Finds time candidates within configurable window
    4. Clusters by visual similarity (cosine of embeddings)
    5. Creates stacks with automatic representative selection
    6. Sends progress updates via heartbeat
    7. Handles errors gracefully

    The worker is designed to be:
    - Resumable: Can be stopped and restarted
    - Deterministic: Same params = same results
    - Observable: Emits progress signals
    - Crash-safe: Uses JobService lease/heartbeat pattern
    """

    def __init__(
        self,
        job_id: int,
        project_id: int,
        time_window_seconds: int = 10,
        similarity_threshold: float = 0.92,
        min_stack_size: int = 3,
        rule_version: str = "1",
        db_path: Optional[str] = None
    ):
        """
        Initialize SimilarShotStackWorker.

        Args:
            job_id: Job ID from JobService
            project_id: Project ID to process
            time_window_seconds: Time window for burst detection (seconds)
            similarity_threshold: Minimum cosine similarity (0.0-1.0)
            min_stack_size: Minimum photos per stack
            rule_version: Algorithm version for clearing/regeneration
            db_path: Optional database path (defaults to singleton)
        """
        super().__init__()
        self.job_id = job_id
        self.project_id = project_id
        self.time_window_seconds = time_window_seconds
        self.similarity_threshold = similarity_threshold
        self.min_stack_size = min_stack_size
        self.rule_version = rule_version
        self.db_path = db_path

        self.signals = SimilarShotStackWorkerSignals()

        # Will be initialized in run()
        self.job_service = None
        self.stack_gen_service = None
        self.worker_id = f"similar_shot_stacks_{int(time.time())}"

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
                logger.warning(f"[SimilarShotStacks] Job {self.job_id} already claimed or not queued")
                return

            logger.info(f"[SimilarShotStacks] Starting stack generation for project {self.project_id}")

            # Create parameters
            params = StackGenParams(
                rule_version=self.rule_version,
                time_window_seconds=self.time_window_seconds,
                min_stack_size=self.min_stack_size,
                similarity_threshold=self.similarity_threshold
            )

            # Execute stack generation
            # Note: Progress tracking is simpler here - just overall progress
            self.signals.progress.emit(0, 100, "Initializing...")
            self.job_service.heartbeat(self.job_id, 0.1)

            stats = self.stack_gen_service.regenerate_similar_shot_stacks(
                project_id=self.project_id,
                params=params
            )

            # Update progress to completion
            self.signals.progress.emit(100, 100, "Complete")
            self.job_service.heartbeat(self.job_id, 1.0)

            # Mark job as complete
            result_message = (
                f"Similar shot generation complete: {stats.photos_considered} photos considered, "
                f"{stats.stacks_created} stacks created, {stats.memberships_created} memberships, "
                f"{stats.errors} errors"
            )
            self.job_service.complete_job(self.job_id, success=True)

            logger.info(f"[SimilarShotStacks] {result_message}")

            # Emit finished signal
            self.signals.finished.emit(
                stats.photos_considered,
                stats.stacks_created,
                stats.memberships_created,
                stats.errors
            )

        except Exception as e:
            error_msg = f"Similar shot stack generation failed: {str(e)}"
            logger.error(f"[SimilarShotStacks] {error_msg}", exc_info=True)

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

        IMPORTANT: Uses the project's canonical semantic model to ensure
        embedding consistency and prevent vector space contamination.
        """
        # Initialize database connection
        if self.db_path:
            db_conn = DatabaseConnection(self.db_path)
        else:
            db_conn = DatabaseConnection()

        # Initialize repositories
        photo_repo = PhotoRepository(db_conn)
        stack_repo = StackRepository(db_conn)
        project_repo = ProjectRepository(db_conn)

        # Get the project's canonical semantic model
        canonical_model = project_repo.get_semantic_model(self.project_id)
        logger.info(
            f"[SimilarShotStacks] Using project canonical model: {canonical_model} "
            f"(project_id={self.project_id})"
        )

        # Check for embedding model mismatches before proceeding
        mismatch_info = project_repo.get_embedding_model_mismatch_count(self.project_id)
        if mismatch_info['mismatched_embeddings'] > 0:
            logger.warning(
                f"[SimilarShotStacks] WARNING: {mismatch_info['mismatched_embeddings']} embeddings "
                f"use a different model than canonical model '{canonical_model}'. "
                f"Models in use: {mismatch_info['models_in_use']}. "
                f"Consider running semantic reindex for accurate results."
            )

        # Initialize embedding service with the project's canonical model
        embedding_service = SemanticEmbeddingService(
            model_name=canonical_model,
            db_connection=db_conn
        )

        # Initialize stack generation service
        self.stack_gen_service = StackGenerationService(
            photo_repo=photo_repo,
            stack_repo=stack_repo,
            similarity_service=embedding_service
        )

        self.job_service = get_job_service()

        logger.debug(
            f"[SimilarShotStacks] Services initialized with model={canonical_model}"
        )


def create_similar_shot_stack_worker(
    job_id: int,
    project_id: int,
    time_window_seconds: int = 10,
    similarity_threshold: float = 0.92,
    min_stack_size: int = 3,
    rule_version: str = "1"
) -> SimilarShotStackWorker:
    """
    Factory function to create SimilarShotStackWorker.

    Args:
        job_id: Job ID from JobService
        project_id: Project ID to process
        time_window_seconds: Time window for burst detection
        similarity_threshold: Minimum cosine similarity
        min_stack_size: Minimum photos per stack
        rule_version: Algorithm version

    Returns:
        SimilarShotStackWorker instance
    """
    return SimilarShotStackWorker(
        job_id=job_id,
        project_id=project_id,
        time_window_seconds=time_window_seconds,
        similarity_threshold=similarity_threshold,
        min_stack_size=min_stack_size,
        rule_version=rule_version
    )
