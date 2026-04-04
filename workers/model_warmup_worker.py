"""
ModelWarmupWorker - Background CLIP Model Loading

Version: 1.0.0
Date: 2026-02-08

Qt QRunnable worker that loads CLIP/SigLIP models in the background,
preventing UI freezes during model initialization.

Architecture:
    - Called from SemanticSearchWidget when search is first triggered
    - Loads model in background thread
    - Emits finished signal when ready
    - UI shows "Loading model..." state until ready

Usage:
    from workers.model_warmup_worker import ModelWarmupWorker
    from PySide6.QtCore import QThreadPool

    worker = ModelWarmupWorker(
        model_variant='openai/clip-vit-large-patch14',
        device='auto'
    )
    worker.signals.finished.connect(on_model_ready)
    worker.signals.error.connect(on_model_error)
    QThreadPool.globalInstance().start(worker)
"""

import uuid
from typing import Optional
from PySide6.QtCore import QRunnable, QObject, Signal

from logging_config import get_logger

logger = get_logger(__name__)


class ModelWarmupWorkerSignals(QObject):
    """
    Signals for ModelWarmupWorker.

    Qt signals must be defined in a QObject, not QRunnable.
    """
    # Finished: (model_id, model_variant) - model is ready
    finished = Signal(int, str)

    # Progress: (message) - status update
    progress = Signal(str)

    # Error: (error_message)
    error = Signal(str)


class ModelWarmupWorker(QRunnable):
    """
    QRunnable worker for background CLIP model loading.

    This worker:
    1. Imports heavy dependencies (torch, transformers) in background
    2. Loads the CLIP model
    3. Emits finished signal with model_id when ready

    Thread Safety:
    - Runs in QThreadPool background thread
    - Uses signals for UI communication
    - EmbeddingService handles thread-safe model access
    """

    def __init__(self,
                 model_variant: Optional[str] = None,
                 device: str = 'auto',
                 project_id: Optional[int] = None):
        """
        Initialize model warmup worker.

        Args:
            model_variant: CLIP model variant (optional - will use project's canonical model if project_id provided)
            device: Compute device ('auto', 'cpu', 'cuda', 'mps')
            project_id: Project ID for canonical model resolution (RECOMMENDED)
        """
        super().__init__()
        self.requested_variant = model_variant
        self.device = device
        self.project_id = project_id

        self.signals = ModelWarmupWorkerSignals()
        self.worker_id = f"model-warmup-{uuid.uuid4().hex[:8]}"

        # Will be set during run()
        self.model_variant = None
        self._is_cancelled = False

    def _resolve_model_variant(self) -> str:
        """
        Resolve model variant with project canonical model enforcement.

        Priority:
        1. Project's canonical model (if project_id provided)
        2. Requested model variant (if matches canonical or no project)
        3. Default model from registry

        Returns:
            Resolved model variant
        """
        # If project_id is provided, use project's canonical model
        if self.project_id is not None:
            try:
                from repository.project_repository import ProjectRepository
                project_repo = ProjectRepository()
                canonical_model = project_repo.get_semantic_model(self.project_id)

                if canonical_model:
                    # Warn if caller requested a different model
                    if self.requested_variant is not None and self.requested_variant != canonical_model:
                        logger.warning(
                            f"[ModelWarmupWorker] Requested model '{self.requested_variant}' differs from "
                            f"project canonical model '{canonical_model}'. Using canonical model."
                        )

                    logger.info(
                        f"[ModelWarmupWorker] Using project canonical model: {canonical_model} "
                        f"(project_id={self.project_id})"
                    )
                    return canonical_model

            except Exception as e:
                logger.warning(
                    f"[ModelWarmupWorker] Could not read project canonical model: {e}. "
                    f"Falling back to requested/default."
                )

        # No project_id - fall back to requested or default
        if self.requested_variant is not None:
            logger.info(f"[ModelWarmupWorker] Using requested model variant: {self.requested_variant}")
            return self.requested_variant

        # Use default model from registry
        from utils.clip_model_registry import normalize_model_id, DEFAULT_MODEL
        default = normalize_model_id(DEFAULT_MODEL)
        logger.info(f"[ModelWarmupWorker] Using default model variant: {default}")
        return default

    def run(self):
        """
        Execute model loading in background.

        Called by QThreadPool when worker starts.
        """
        logger.info(f"[ModelWarmupWorker] Starting: worker={self.worker_id}")

        try:
            # Step 1: Resolve model variant
            self.signals.progress.emit("Resolving model variant...")
            self.model_variant = self._resolve_model_variant()

            if self._is_cancelled:
                logger.info(f"[ModelWarmupWorker] Cancelled before loading model")
                return

            # Step 2: Import heavy dependencies
            self.signals.progress.emit("Importing dependencies...")
            logger.info(f"[ModelWarmupWorker] Importing torch and transformers...")

            # These imports are slow - that's why we do them in background
            try:
                import torch  # noqa: F401
                from transformers import CLIPModel, CLIPProcessor  # noqa: F401
            except (AttributeError, RuntimeError) as e:
                # NumPy 2.x incompatibility: torch/onnxruntime compiled against NumPy 1.x
                logger.error(
                    "[ModelWarmupWorker] Failed to import ML dependencies "
                    "(likely NumPy 2.x incompatibility): %s", e
                )
                self.signals.error.emit(
                    f"ML dependency import failed: {e}\n"
                    f'Fix: pip install "numpy<2" and restart.'
                )
                return

            if self._is_cancelled:
                logger.info(f"[ModelWarmupWorker] Cancelled after imports")
                return

            # Step 3: Get semantic embedding service (this caches the instance)
            # FIX 2026-02-08: Use semantic_embedding_service.py (the canonical service)
            # instead of legacy embedding_service.py
            self.signals.progress.emit("Initializing semantic embedding service...")
            from services.semantic_embedding_service import get_semantic_embedding_service
            embedding_service = get_semantic_embedding_service(model_name=self.model_variant)

            if not embedding_service._available:
                error_msg = "Embedding service not available (missing dependencies)"
                logger.warning(f"[ModelWarmupWorker] {error_msg}")
                self.signals.error.emit(error_msg)
                return

            if self._is_cancelled:
                logger.info(f"[ModelWarmupWorker] Cancelled before model load")
                return

            # Step 4: Load CLIP model
            self.signals.progress.emit(f"Loading {self.model_variant}...")
            logger.info(f"[ModelWarmupWorker] Loading CLIP model: {self.model_variant}")

            # FIX 2026-02-08: Use _load_model() method from SemanticEmbeddingService
            embedding_service._load_model()

            # Verify model is loaded
            if embedding_service._model is None:
                raise RuntimeError("Model failed to load (model is None after _load_model)")

            logger.info(
                f"[ModelWarmupWorker] Model loaded successfully: "
                f"variant={self.model_variant}, device={embedding_service._device}"
            )

            # Emit success signal (model_id is 0 for SemanticEmbeddingService as it doesn't use model IDs)
            self.signals.finished.emit(0, self.model_variant)

        except Exception as e:
            error_msg = f"Model loading failed: {e}"
            logger.error(f"[ModelWarmupWorker] {error_msg}", exc_info=True)
            self.signals.error.emit(error_msg)

    def cancel(self):
        """Cancel the worker gracefully."""
        logger.info(f"[ModelWarmupWorker] Cancel requested for worker {self.worker_id}")
        self._is_cancelled = True


def launch_model_warmup(model_variant: Optional[str] = None,
                        device: str = 'auto',
                        project_id: Optional[int] = None,
                        on_finished=None,
                        on_progress=None,
                        on_error=None) -> ModelWarmupWorker:
    """
    Convenience function to launch model warmup worker.

    Args:
        model_variant: CLIP model variant (optional)
        device: Compute device
        project_id: Project ID for canonical model enforcement
        on_finished: Callback for finished signal (model_id, model_variant)
        on_progress: Callback for progress signal (message)
        on_error: Callback for error signal (error_message)

    Returns:
        ModelWarmupWorker instance (already started)

    Example:
        worker = launch_model_warmup(
            project_id=1,
            on_finished=lambda model_id, variant: print(f"Model ready: {variant}"),
            on_error=lambda err: print(f"Error: {err}")
        )
    """
    from PySide6.QtCore import QThreadPool

    worker = ModelWarmupWorker(
        model_variant=model_variant,
        device=device,
        project_id=project_id
    )

    # Connect signals
    if on_finished:
        worker.signals.finished.connect(on_finished)
    if on_progress:
        worker.signals.progress.connect(on_progress)
    if on_error:
        worker.signals.error.connect(on_error)

    # Default logging
    worker.signals.finished.connect(
        lambda model_id, variant: logger.info(
            f"[ModelWarmup] Model ready: {variant} (id={model_id})"
        )
    )
    worker.signals.error.connect(
        lambda error: logger.error(f"[ModelWarmup] Error: {error}")
    )

    QThreadPool.globalInstance().start(worker)

    return worker
