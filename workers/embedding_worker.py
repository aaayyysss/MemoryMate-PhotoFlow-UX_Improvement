"""
EmbeddingWorker - Background Visual Embedding Extraction

Version: 1.0.0
Date: 2026-01-01

Qt QRunnable worker that extracts visual embeddings for photos using
CLIP/SigLIP models. Integrates with JobService for crash-safe orchestration.

Architecture:
    JobService.enqueue_job('embed', {...})
        → EmbeddingWorker claims job
        → Loads CLIP model (lazy, cached)
        → Processes photos in batch
        → Stores embeddings in photo_embedding table
        → Sends progress updates
        → Completes or fails job

Usage:
    from workers.embedding_worker import EmbeddingWorker
    from PySide6.QtCore import QThreadPool

    # Create worker
    worker = EmbeddingWorker(
        job_id=123,
        photo_ids=[1, 2, 3],
        model_variant='openai/clip-vit-base-patch32'
    )

    # Connect signals
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.error.connect(on_error)

    # Start in thread pool
    QThreadPool.globalInstance().start(worker)
"""

import time
import uuid
from typing import List, Optional
from pathlib import Path
from PySide6.QtCore import QRunnable, QObject, Signal, Slot

from services.job_service import get_job_service
from services.embedding_service import get_embedding_service
from repository.photo_repository import PhotoRepository
from repository.project_repository import ProjectRepository
from logging_config import get_logger

logger = get_logger(__name__)


class ModelAutoSelectWarning(UserWarning):
    """Warning issued when model is auto-selected without project context."""
    pass


class EmbeddingWorkerSignals(QObject):
    """
    Signals for EmbeddingWorker.

    Qt signals must be defined in a QObject, not QRunnable.
    """
    # Progress: (current, total, photo_path, message)
    progress = Signal(int, int, str, str)

    # Finished: (success_count, failed_count)
    finished = Signal(int, int)

    # Error: (error_message)
    error = Signal(str)


class EmbeddingWorker(QRunnable):
    """
    QRunnable worker for background embedding extraction.

    This worker:
    1. Claims a job from JobService
    2. Loads photos from database
    3. Extracts embeddings using EmbeddingService
    4. Stores embeddings in photo_embedding table
    5. Sends progress updates via heartbeat
    6. Handles errors and retries

    Thread Safety:
    - Runs in QThreadPool background thread
    - Uses signals for UI communication
    - Database operations are thread-safe
    """

    def __init__(self,
                 job_id: int,
                 photo_ids: Optional[List[int]] = None,
                 model_variant: Optional[str] = None,
                 device: str = 'auto',
                 project_id: Optional[int] = None):
        """
        Initialize embedding worker.

        IMPORTANT: Model selection follows the "project canonical model" principle.
        When project_id is provided, the model_variant is resolved from the project's
        semantic_model setting. This ensures embedding consistency within a project.

        Args:
            job_id: Job ID from ml_job table
            photo_ids: Optional list of photo IDs (loaded from job if None)
            model_variant: CLIP model variant (DEPRECATED - prefer using project_id)
                          If provided, must match project's canonical model when project_id is set.
            device: Compute device ('auto', 'cpu', 'cuda', 'mps')
            project_id: Project ID for canonical model resolution (RECOMMENDED)
        """
        super().__init__()
        self.job_id = job_id
        self.photo_ids = photo_ids
        self.project_id = project_id

        # Resolve model variant with project canonical model enforcement
        self.model_variant = self._resolve_model_variant(model_variant)
        self.device = device

        self.signals = EmbeddingWorkerSignals()
        self.worker_id = f"embedding-worker-{uuid.uuid4().hex[:8]}"

        # Services
        self.job_service = get_job_service()
        self.embedding_service = get_embedding_service(device=device)
        self.photo_repo = PhotoRepository()

        # Stats
        self.success_count = 0
        self.failed_count = 0

        # Cancellation flag
        self._is_cancelled = False

    def _resolve_model_variant(self, requested_variant: Optional[str]) -> str:
        """
        Resolve model variant with project canonical model enforcement.

        Priority:
        1. Project's canonical model (if project_id provided)
        2. Requested model variant (if matches canonical or no project)
        3. Auto-detect best available (WARNING: may cause inconsistency)

        Args:
            requested_variant: Optional model variant requested by caller

        Returns:
            Resolved model variant
        """
        # If project_id is provided, use project's canonical model
        if self.project_id is not None:
            try:
                project_repo = ProjectRepository()
                canonical_model = project_repo.get_semantic_model(self.project_id)

                # Warn if caller requested a different model
                if requested_variant is not None and requested_variant != canonical_model:
                    logger.warning(
                        f"[EmbeddingWorker] Requested model '{requested_variant}' differs from "
                        f"project canonical model '{canonical_model}'. Using canonical model."
                    )

                logger.info(
                    f"[EmbeddingWorker] Using project canonical model: {canonical_model} "
                    f"(project_id={self.project_id})"
                )
                return canonical_model

            except Exception as e:
                logger.warning(
                    f"[EmbeddingWorker] Could not read project canonical model: {e}. "
                    f"Falling back to requested/auto-detect."
                )

        # No project_id - fall back to requested or auto-detect
        if requested_variant is not None:
            logger.info(f"[EmbeddingWorker] Using requested model variant: {requested_variant}")
            return requested_variant

        # Auto-detect - WARNING: This can cause model inconsistency!
        import warnings
        from utils.clip_check import get_recommended_variant
        model_variant = get_recommended_variant()

        warnings.warn(
            f"[EmbeddingWorker] Auto-selecting model variant '{model_variant}' without project context. "
            f"This may cause vector space contamination if different models are used within a project. "
            f"Provide project_id parameter to enforce canonical model.",
            ModelAutoSelectWarning
        )
        logger.warning(
            f"[EmbeddingWorker] Auto-selected model variant: {model_variant} "
            f"(NO PROJECT CONTEXT - may cause inconsistency!)"
        )

        return model_variant

    def run(self):
        """
        Execute embedding extraction.

        Called by QThreadPool when worker starts.
        """
        logger.info(f"[EmbeddingWorker] Starting: job={self.job_id}, worker={self.worker_id}")

        try:
            # Step 1: Claim job
            claimed = self.job_service.claim_job(
                self.job_id,
                worker_id=self.worker_id,
                lease_seconds=600  # 10 minutes for large batches
            )

            if not claimed:
                logger.warning(f"[EmbeddingWorker] Failed to claim job {self.job_id}")
                self.signals.error.emit(f"Failed to claim job {self.job_id}")
                return

            # Step 2: Load job parameters
            job = self.job_service.get_job(self.job_id)
            if not job:
                logger.error(f"[EmbeddingWorker] Job {self.job_id} not found after claim")
                return

            if self.photo_ids is None:
                # Load from job payload
                self.photo_ids = job.payload.get('photo_ids', [])

            if not self.photo_ids:
                logger.warning(f"[EmbeddingWorker] No photo IDs in job {self.job_id}")
                self.job_service.complete_job(self.job_id, success=True)
                self.signals.finished.emit(0, 0)
                return

            logger.info(
                f"[EmbeddingWorker] Processing {len(self.photo_ids)} photos "
                f"with model {self.model_variant}"
            )

            # Step 3: Load CLIP model
            try:
                model_id = self.embedding_service.load_clip_model(self.model_variant)
                logger.info(f"[EmbeddingWorker] Model loaded: ID={model_id}")
            except Exception as e:
                error_msg = f"Failed to load model: {e}"
                logger.error(f"[EmbeddingWorker] {error_msg}")
                self.job_service.complete_job(self.job_id, success=False, error=error_msg)
                self.signals.error.emit(error_msg)
                return

            # Step 4: Process photos
            total = len(self.photo_ids)
            last_heartbeat = time.time()

            for i, photo_id in enumerate(self.photo_ids, 1):
                # Check for cancellation
                if self._is_cancelled:
                    logger.info(f"[EmbeddingWorker] Cancelled by user at {i}/{total}")
                    self.job_service.complete_job(self.job_id, success=False, error="Cancelled by user")
                    self.signals.finished.emit(self.success_count, self.failed_count)
                    return

                try:
                    # Get photo path for progress display
                    photo_path = self._get_photo_path(photo_id)
                    photo_name = Path(photo_path).name if photo_path != "Unknown" else "Unknown"

                    # Emit progress signal before processing
                    msg = f"Embedding photo #{i}/{total}: {photo_name}"
                    self.signals.progress.emit(i, total, photo_path, msg)

                    # Extract and store embedding
                    self._process_photo(photo_id, model_id)
                    self.success_count += 1

                    # Update progress
                    progress_pct = i / total
                    self.job_service.heartbeat(self.job_id, progress=progress_pct)

                    # Log progress every 10 photos or every 30 seconds
                    now = time.time()
                    if i % 10 == 0 or (now - last_heartbeat) >= 30:
                        logger.info(f"[EmbeddingWorker] Processed {i}/{total} photos")
                        last_heartbeat = now

                except Exception as e:
                    logger.error(f"[EmbeddingWorker] Failed to process photo {photo_id}: {e}")
                    self.failed_count += 1
                    # Continue with next photo (don't fail entire job)

            # Step 5: Complete job
            logger.info(
                f"[EmbeddingWorker] Completed: "
                f"job={self.job_id}, success={self.success_count}, failed={self.failed_count}"
            )

            self.job_service.complete_job(self.job_id, success=True)
            self.signals.finished.emit(self.success_count, self.failed_count)

        except Exception as e:
            error_msg = f"Worker failed: {e}"
            logger.error(f"[EmbeddingWorker] {error_msg}", exc_info=True)
            self.job_service.complete_job(self.job_id, success=False, error=error_msg)
            self.signals.error.emit(error_msg)

    def _get_photo_path(self, photo_id: int) -> str:
        """
        Get photo path from database.

        Args:
            photo_id: Photo ID

        Returns:
            str: Photo file path
        """
        try:
            with self.photo_repo.connection() as conn:
                cursor = conn.execute(
                    "SELECT path FROM photo_metadata WHERE id = ?",
                    (photo_id,)
                )
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"[EmbeddingWorker] Photo {photo_id} not found in database")
                    return "Unknown"

                # Handle both dict and tuple row formats
                if isinstance(row, dict):
                    if 'path' in row:
                        return row['path']
                    else:
                        logger.error(f"[EmbeddingWorker] Row is dict but has no 'path' key. Keys: {list(row.keys())}")
                        return "Unknown"
                else:
                    # Tuple/list format
                    if len(row) > 0:
                        return row[0]
                    else:
                        logger.error(f"[EmbeddingWorker] Row is empty tuple/list")
                        return "Unknown"

        except Exception as e:
            logger.error(f"[EmbeddingWorker] Error getting path for photo {photo_id}: {e}", exc_info=True)
            return "Unknown"

    def _process_photo(self, photo_id: int, model_id: int):
        """
        Extract and store embedding for a single photo.

        Args:
            photo_id: Photo ID
            model_id: Model ID from ml_model table

        Raises:
            Exception: If extraction or storage fails
        """
        try:
            # Get photo path
            logger.debug(f"[EmbeddingWorker] Step 1: Getting path for photo {photo_id}")
            photo_path = self._get_photo_path(photo_id)
            logger.debug(f"[EmbeddingWorker] Step 1 result: photo_path = {photo_path}")

            if photo_path == "Unknown":
                raise ValueError(f"Photo {photo_id} not found in database")

            # Check file exists
            logger.debug(f"[EmbeddingWorker] Step 2: Checking if file exists: {photo_path}")
            if not Path(photo_path).exists():
                raise FileNotFoundError(f"Photo file not found: {photo_path}")

            # Extract embedding
            logger.debug(f"[EmbeddingWorker] Step 3: Extracting embedding for {photo_path}")
            embedding = self.embedding_service.extract_image_embedding(photo_path, model_id)
            logger.debug(f"[EmbeddingWorker] Step 3 result: embedding shape = {embedding.shape}")

            # Store in database
            logger.debug(f"[EmbeddingWorker] Step 4: Storing embedding for photo {photo_id}")
            self.embedding_service.store_embedding(photo_id, embedding, model_id)

            logger.debug(f"[EmbeddingWorker] ✓ Processed photo {photo_id}: {Path(photo_path).name}")

        except Exception as e:
            logger.error(
                f"[EmbeddingWorker] Exception in _process_photo for photo {photo_id}: "
                f"type={type(e).__name__}, message='{e}'",
                exc_info=True
            )
            raise

    def cancel(self):
        """Cancel the worker gracefully."""
        logger.info(f"[EmbeddingWorker] Cancel requested for job {self.job_id}")
        self._is_cancelled = True


def launch_embedding_worker(photo_ids: List[int],
                            model_variant: Optional[str] = None,
                            device: str = 'auto',
                            project_id: Optional[int] = None) -> int:
    """
    Convenience function to enqueue embedding job and launch worker.

    Args:
        photo_ids: List of photo IDs to process
        model_variant: CLIP model variant (optional - will use project's canonical model if project_id provided)
        device: Compute device
        project_id: Project ID for canonical model enforcement (RECOMMENDED)

    Returns:
        int: Job ID

    Example:
        job_id = launch_embedding_worker(
            photo_ids=[1, 2, 3, 4, 5],
            project_id=1,  # Will use project's canonical model
            device='cuda'
        )
    """
    from PySide6.QtCore import QThreadPool

    # Enqueue job
    job_service = get_job_service()
    job_id = job_service.enqueue_job(
        kind='embed',
        payload={
            'photo_ids': photo_ids,
            'model_variant': model_variant,
            'project_id': project_id
        },
        backend=device
    )

    logger.info(f"[EmbeddingWorker] Enqueued job {job_id} for {len(photo_ids)} photos (project_id={project_id})")

    # Create and start worker
    worker = EmbeddingWorker(
        job_id=job_id,
        photo_ids=photo_ids,
        model_variant=model_variant,
        device=device,
        project_id=project_id
    )

    # Connect signals (optional - caller can also connect)
    worker.signals.progress.connect(
        lambda curr, total, path, msg: logger.info(
            f"[EmbeddingWorker] Progress: {curr}/{total} - {msg} - {Path(path).name}"
        )
    )
    worker.signals.finished.connect(
        lambda success, failed: logger.info(
            f"[EmbeddingWorker] Finished: success={success}, failed={failed}"
        )
    )
    worker.signals.error.connect(
        lambda error: logger.error(f"[EmbeddingWorker] Error: {error}")
    )

    QThreadPool.globalInstance().start(worker)

    return job_id
