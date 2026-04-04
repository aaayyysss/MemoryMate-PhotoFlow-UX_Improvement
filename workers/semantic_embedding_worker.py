"""
SemanticEmbeddingWorker - Offline Batch Embedding

Version: 1.0.0
Date: 2026-01-05

Offline batch embedding extraction for semantic search.

Properties (non-negotiable):
- Offline (no blocking UI)
- Idempotent (safe to restart)
- Restart-safe (skips already processed)
- Progress reporting
- Per-photo error handling (doesn't fail entire batch)

Usage:
    worker = SemanticEmbeddingWorker(photo_ids=[1, 2, 3], model_name="clip-vit-b32")
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    QThreadPool.globalInstance().start(worker)
"""

import time
import hashlib
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QRunnable, QObject, Signal

from services.semantic_embedding_service import get_semantic_embedding_service
from repository.photo_repository import PhotoRepository
from repository.project_repository import ProjectRepository
from logging_config import get_logger

logger = get_logger(__name__)


class SemanticModelMismatchError(Exception):
    """Raised when trying to create embeddings with a model that doesn't match the project's canonical model."""
    pass


class SemanticEmbeddingSignals(QObject):
    """Signals for semantic embedding worker."""
    progress = Signal(int, int, str)  # (current, total, message)
    finished = Signal(dict)  # stats
    error = Signal(str)  # error message


class SemanticEmbeddingWorker(QRunnable):
    """
    Worker for batch semantic embedding extraction.

    Properties:
    - ✔ Offline (runs in background thread)
    - ✔ Idempotent (skip already processed)
    - ✔ Restart-safe (no state corruption on crash)
    - ✔ Per-photo error handling
    - ✔ Progress reporting
    - ✔ Resumable (saves progress for interrupted jobs)
    """

    def __init__(self,
                 photo_ids: List[int],
                 model_name: Optional[str] = None,
                 force_recompute: bool = False,
                 project_id: Optional[int] = None,
                 save_progress_interval: int = 10):
        """
        Initialize semantic embedding worker.

        IMPORTANT: Model selection follows the "project canonical model" principle.
        The model_name is determined by the project's semantic_model setting.

        Args:
            photo_ids: List of photo IDs to process
            model_name: DEPRECATED - If provided, must match project's canonical model.
                       Will be ignored if project_id is provided (project's model takes precedence).
            force_recompute: If True, recompute even if embedding exists
            project_id: Project ID (REQUIRED for canonical model enforcement)
            save_progress_interval: Save progress every N photos (default: 10)

        Raises:
            SemanticModelMismatchError: If model_name doesn't match project's canonical model
        """
        super().__init__()
        self.photo_ids = photo_ids
        self.force_recompute = force_recompute
        self.project_id = project_id
        self.save_progress_interval = save_progress_interval

        # Resolve canonical model from project
        self.model_name = self._resolve_canonical_model(model_name)

        self.signals = SemanticEmbeddingSignals()

        # Statistics
        self.success_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.start_time = None
        self._last_processed_photo_id = None

    def _resolve_canonical_model(self, requested_model: Optional[str]) -> str:
        """
        Resolve the canonical model for embedding generation.

        Priority order:
        1. Project's canonical model (if project_id provided)
        2. Requested model (if matches project canonical, or no project)
        3. Default model (if no project and no request)

        Args:
            requested_model: Optional model name requested by caller

        Returns:
            Resolved model name

        Raises:
            SemanticModelMismatchError: If requested model doesn't match project's canonical model
        """
        default_model = "clip-vit-b32"

        if self.project_id is not None:
            try:
                project_repo = ProjectRepository()
                canonical_model = project_repo.get_semantic_model(self.project_id)

                # If caller explicitly requested a different model, that's an error
                if requested_model is not None and requested_model != canonical_model:
                    raise SemanticModelMismatchError(
                        f"Model mismatch: requested '{requested_model}' but project {self.project_id} "
                        f"uses canonical model '{canonical_model}'. "
                        f"Either change the project's semantic model or omit the model_name parameter."
                    )

                logger.info(
                    f"[SemanticEmbeddingWorker] Using project canonical model: {canonical_model} "
                    f"(project_id={self.project_id})"
                )
                return canonical_model

            except SemanticModelMismatchError:
                raise  # Re-raise model mismatch
            except Exception as e:
                logger.warning(
                    f"[SemanticEmbeddingWorker] Could not read project canonical model: {e}. "
                    f"Falling back to requested/default model."
                )

        # No project_id or couldn't read project - use requested or default
        model = requested_model or default_model
        if self.project_id is None:
            logger.warning(
                f"[SemanticEmbeddingWorker] No project_id provided. "
                f"Using model '{model}' without canonical model enforcement. "
                f"This may lead to vector space contamination!"
            )
        return model

    def run(self):
        """Execute batch embedding extraction with progress saving for resumability."""
        self.start_time = time.time()

        logger.info(
            f"[SemanticEmbeddingWorker] Starting batch: {len(self.photo_ids)} photos, "
            f"model={self.model_name}, force={self.force_recompute}, project={self.project_id}"
        )

        try:
            # Initialize services
            embedder = get_semantic_embedding_service(model_name=self.model_name)
            photo_repo = PhotoRepository()

            # Check availability
            if not embedder.available:
                error_msg = "PyTorch/Transformers not available. Cannot extract embeddings."
                logger.error(f"[SemanticEmbeddingWorker] {error_msg}")
                self.signals.error.emit(error_msg)
                return

            total = len(self.photo_ids)

            # Save initial job progress if project_id is provided
            if self.project_id is not None:
                embedder.save_job_progress(
                    project_id=self.project_id,
                    last_photo_id=0,
                    total_photos=total,
                    processed_count=0,
                    status='in_progress'
                )

            # Track consecutive failures to detect model load issues early
            _consecutive_failures = 0
            _MAX_CONSECUTIVE_FAILURES = 3  # Abort after 3 consecutive failures

            for i, photo_id in enumerate(self.photo_ids, 1):
                try:
                    self._process_photo(photo_id, embedder, photo_repo)
                    self._last_processed_photo_id = photo_id
                    _consecutive_failures = 0  # Reset on success or skip
                except Exception as e:
                    logger.error(f"[SemanticEmbeddingWorker] Failed to process photo {photo_id}: {e}")
                    self.failed_count += 1
                    _consecutive_failures += 1

                    # Early abort: if model loading is broken, stop immediately
                    # instead of repeating the same error for every photo
                    if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        remaining = total - i
                        logger.error(
                            f"[SemanticEmbeddingWorker] Aborting batch: {_consecutive_failures} "
                            f"consecutive failures detected (likely model load issue). "
                            f"Skipping remaining {remaining} photos."
                        )
                        self.failed_count += remaining
                        break

                # Progress reporting and saving (every N photos or last)
                if i % self.save_progress_interval == 0 or i == total:
                    photo_name = "Unknown"
                    try:
                        photo = photo_repo.get_by_id(photo_id)
                        if photo:
                            photo_name = Path(photo.get('file_path') or photo.get('path') or "").name
                    except Exception:
                        pass

                    msg = f"Embedding photo #{i}/{total}: {photo_name}"
                    self.signals.progress.emit(i, total, msg)

                    # Save progress for resumability
                    if self.project_id is not None and self._last_processed_photo_id:
                        embedder.save_job_progress(
                            project_id=self.project_id,
                            last_photo_id=self._last_processed_photo_id,
                            total_photos=total,
                            processed_count=i,
                            status='in_progress'
                        )

            # Finish - mark job as completed
            duration = time.time() - self.start_time
            stats = {
                'total': total,
                'success': self.success_count,
                'skipped': self.skipped_count,
                'failed': self.failed_count,
                'duration_sec': duration,
                'resumed': False,  # Will be set by caller if this was a resumed job
            }

            # Mark job as completed
            if self.project_id is not None:
                embedder.save_job_progress(
                    project_id=self.project_id,
                    last_photo_id=self._last_processed_photo_id or 0,
                    total_photos=total,
                    processed_count=total,
                    status='completed'
                )

            logger.info(
                f"[SemanticEmbeddingWorker] Batch complete: "
                f"{self.success_count} success, {self.skipped_count} skipped, "
                f"{self.failed_count} failed in {duration:.1f}s"
            )

            self.signals.finished.emit(stats)

        except Exception as e:
            logger.error(f"[SemanticEmbeddingWorker] Fatal error: {e}", exc_info=True)

            # Save failed status for debugging
            if self.project_id is not None:
                try:
                    embedder = get_semantic_embedding_service(model_name=self.model_name)
                    embedder.save_job_progress(
                        project_id=self.project_id,
                        last_photo_id=self._last_processed_photo_id or 0,
                        total_photos=len(self.photo_ids),
                        processed_count=self.success_count + self.skipped_count + self.failed_count,
                        status='failed'
                    )
                except Exception:
                    pass  # Best effort

            self.signals.error.emit(str(e))

    def _process_photo(self,
                      photo_id: int,
                      embedder,
                      photo_repo: PhotoRepository):
        """
        Process single photo (idempotent).

        Args:
            photo_id: Photo ID
            embedder: SemanticEmbeddingService instance
            photo_repo: PhotoRepository instance
        """
        # Check if already processed (idempotent)
        if not self.force_recompute and embedder.has_embedding(photo_id):
            logger.debug(f"[SemanticEmbeddingWorker] Photo {photo_id} already has embedding, skipping")
            self.skipped_count += 1
            return

        # Get photo metadata
        photo = photo_repo.get_by_id(photo_id)
        if photo is None:
            logger.warning(f"[SemanticEmbeddingWorker] Photo {photo_id} not found in database")
            self.failed_count += 1
            return

        file_path = photo.get('file_path') or photo.get('path')
        if not file_path:
            logger.warning(f"[SemanticEmbeddingWorker] Photo {photo_id} has no file_path")
            self.failed_count += 1
            return

        # Check if file exists
        if not Path(file_path).exists():
            logger.warning(f"[SemanticEmbeddingWorker] Photo {photo_id} file not found: {file_path}")
            self.failed_count += 1
            return

        # Freshness tracking: use the photo's image_content_hash (dHash)
        # so the stale check (source_photo_hash == image_content_hash) compares
        # the same hash type.  Fall back to SHA256 only if dHash is missing.
        source_hash = photo.get('image_content_hash') or self._compute_hash(file_path)
        source_mtime = str(Path(file_path).stat().st_mtime)

        # Extract embedding
        photo_name = Path(file_path).name
        logger.info("[SemanticEmbeddingWorker] Embedding photo %d: %s", photo_id, file_path)
        try:
            embedding = embedder.encode_image(file_path)
        except Exception as e:
            logger.error(f"[SemanticEmbeddingWorker] Failed to encode photo {photo_id}: {e}")
            self.failed_count += 1
            return

        # encode_image returns None when the image could not be processed
        if embedding is None:
            logger.warning("[SemanticEmbeddingWorker] Skipping photo %d (encode returned None): %s", photo_id, file_path)
            self.failed_count += 1
            return

        # Store embedding
        try:
            embedder.store_embedding(
                photo_id=photo_id,
                embedding=embedding,
                source_hash=source_hash,
                source_mtime=source_mtime
            )
            self.success_count += 1
            logger.debug(f"[SemanticEmbeddingWorker] ✓ Photo {photo_id} processed")
        except Exception as e:
            logger.error(f"[SemanticEmbeddingWorker] Failed to store embedding for photo {photo_id}: {e}")
            self.failed_count += 1

    def _compute_hash(self, file_path: str) -> str:
        """
        Compute SHA256 hash of file (for freshness tracking).

        Performance optimization: Use 64KB chunks for faster I/O.

        Args:
            file_path: Path to file

        Returns:
            Hex digest of SHA256 hash
        """
        try:
            hasher = hashlib.sha256()
            with open(file_path, 'rb') as f:
                # Read in larger 64KB chunks for better performance
                for chunk in iter(lambda: f.read(65536), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.warning(f"[SemanticEmbeddingWorker] Failed to compute hash for {file_path}: {e}")
            return ""
