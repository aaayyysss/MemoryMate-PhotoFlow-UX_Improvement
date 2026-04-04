# workers/post_scan_pipeline_worker.py
# Version 01.00.00.00 dated 20260201
#
# Background pipeline worker that runs heavy post-scan operations
# without blocking the UI thread.
#
# Operations (in order):
#   1. Hash backfill + asset linking
#   2. Exact duplicate detection
#   3. Embedding generation
#   4. Similar shot detection
#   5. OCR text extraction
#
# All steps are optional and controlled via options dict.

import os
import time
from typing import Dict, Any, Optional, List

from PySide6.QtCore import QRunnable, QObject, Signal, QThreadPool

from logging_config import get_logger

logger = get_logger(__name__)


class PostScanPipelineSignals(QObject):
    """Signals emitted by the pipeline worker."""
    # (step_name, current_step, total_steps, message)
    progress = Signal(str, int, int, str)
    # Emitted when pipeline finishes with stats dict
    finished = Signal(dict)
    # Emitted on fatal error
    error = Signal(str)


class PostScanPipelineWorker(QRunnable):
    """
    Background worker that chains heavy post-scan operations.

    Runs entirely off the UI thread. Emits progress signals for
    status bar updates. Each step is idempotent and safe to restart.
    """

    def __init__(
        self,
        project_id: int,
        options: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.signals = PostScanPipelineSignals()
        self.project_id = project_id
        self.options = options or {}
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        """Execute pipeline steps sequentially in background thread."""
        import threading
        _thread = threading.current_thread()
        _is_main = _thread is threading.main_thread()
        logger.info(
            "[PostScanPipelineWorker] Starting post-scan pipeline for project %d "
            "(thread=%s, is_main=%s)",
            self.project_id, _thread.name, _is_main,
        )

        results = {
            "hash_backfill": 0,
            "exact_duplicates": 0,
            "embeddings_generated": 0,
            "similar_stacks": 0,
            "ocr_processed": 0,
            "ocr_with_text": 0,
            "errors": [],
        }

        detect_exact = self.options.get("detect_exact", False)
        detect_similar = self.options.get("detect_similar", False)
        generate_embeddings = self.options.get("generate_embeddings", False)
        run_ocr = self.options.get("run_ocr", False)

        # Count total steps for progress
        total_steps = 0
        if detect_exact:
            total_steps += 2  # hash backfill + exact dup detection
        if generate_embeddings and detect_similar:
            total_steps += 1  # embedding generation
        if detect_similar:
            total_steps += 1  # similar shot detection
        if run_ocr:
            total_steps += 1  # OCR text extraction
        if total_steps == 0:
            self.signals.finished.emit(results)
            return

        current_step = 0

        try:
            # Imports done inside run() to avoid importing heavy modules on UI thread
            from repository.base_repository import DatabaseConnection
            from repository.photo_repository import PhotoRepository
            from repository.asset_repository import AssetRepository

            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)

            # FIX 2026-03-14: Resolve canonical_model early so it's available
            # for both Step 3 (embeddings) and Step 4 (similar shot detection).
            # Previously it was only resolved inside Step 3's try-block, so
            # Step 4 would crash with NameError if Step 3 was skipped.
            canonical_model = None
            if generate_embeddings or detect_similar:
                from repository.project_repository import ProjectRepository
                proj_repo = ProjectRepository(db_conn)
                canonical_model = proj_repo.get_semantic_model(self.project_id)

            # ── Step 1: Hash backfill ─────────────────────────────
            if detect_exact and not self._cancelled:
                current_step += 1
                self.signals.progress.emit(
                    "hash_backfill", current_step, total_steps,
                    "Computing photo hashes..."
                )

                try:
                    from services.asset_service import AssetService
                    asset_repo = AssetRepository(db_conn)
                    asset_service = AssetService(photo_repo, asset_repo)

                    backfill_stats = asset_service.backfill_hashes_and_link_assets(self.project_id)
                    results["hash_backfill"] = backfill_stats.hashed
                    logger.info(
                        "Hash backfill complete: %d scanned, %d hashed, %d linked",
                        backfill_stats.scanned, backfill_stats.hashed, backfill_stats.linked,
                    )
                except Exception as e:
                    logger.error("Hash backfill failed: %s", e, exc_info=True)
                    results["errors"].append(f"Hash backfill: {e}")

            # ── Step 2: Exact duplicate detection ─────────────────
            if detect_exact and not self._cancelled:
                current_step += 1
                self.signals.progress.emit(
                    "exact_duplicates", current_step, total_steps,
                    "Detecting exact duplicates..."
                )

                try:
                    asset_repo = AssetRepository(db_conn)
                    exact_assets = asset_repo.list_duplicate_assets(
                        self.project_id, min_instances=2
                    )
                    results["exact_duplicates"] = len(exact_assets)
                    logger.info("Found %d exact duplicate groups", len(exact_assets))
                except Exception as e:
                    logger.error("Exact duplicate detection failed: %s", e, exc_info=True)
                    results["errors"].append(f"Exact duplicates: {e}")

            # ── Step 3: Embedding generation ──────────────────────
            if generate_embeddings and detect_similar and not self._cancelled:
                current_step += 1
                self.signals.progress.emit(
                    "embeddings", current_step, total_steps,
                    "Generating AI embeddings..."
                )

                try:
                    from services.semantic_embedding_service import get_semantic_embedding_service

                    # FIX 2026-03-14: Use the singleton factory instead of direct
                    # instantiation.  Direct instantiation bypasses the per-model
                    # cache and can create a duplicate CLIP model instance that
                    # contends with the warmup singleton for MKL resources,
                    # causing native access violations on Windows.
                    embedding_service = get_semantic_embedding_service(
                        model_name=canonical_model,
                    )

                    all_photos = photo_repo.find_all(
                        where_clause="project_id = ?",
                        params=(self.project_id,),
                    )
                    photos_needing = [
                        p["id"] for p in all_photos
                        if not embedding_service.has_embedding(p["id"])
                    ]

                    if photos_needing:
                        logger.info(
                            "Found %d photos needing embeddings - generating in background",
                            len(photos_needing),
                        )
                        from workers.semantic_embedding_worker import SemanticEmbeddingWorker

                        worker = SemanticEmbeddingWorker(
                            photo_ids=photos_needing,
                            model_name=canonical_model,
                            force_recompute=False,
                            project_id=self.project_id,
                        )

                        # Run the embedding worker synchronously within this background thread
                        # (it's already off the UI thread, so this is safe)
                        embedding_stats = {"success": 0}
                        import threading
                        done_event = threading.Event()

                        def _on_emb_finished(stats):
                            embedding_stats.update(stats)
                            done_event.set()

                        def _on_emb_error(msg):
                            results["errors"].append(f"Embeddings: {msg}")
                            done_event.set()

                        def _on_emb_progress(current, total, msg):
                            # Pass through the detailed message from SemanticEmbeddingWorker (includes filename)
                            self.signals.progress.emit(
                                "embeddings", current_step, total_steps, msg
                            )

                        worker.signals.finished.connect(_on_emb_finished)
                        worker.signals.error.connect(_on_emb_error)
                        worker.signals.progress.connect(_on_emb_progress)

                        # Run directly in this thread (worker.run() is the actual work)
                        worker.run()

                        results["embeddings_generated"] = embedding_stats.get("success", 0)
                        logger.info("Embedding generation complete: %d generated", results["embeddings_generated"])
                    else:
                        logger.info("All photos already have embeddings")

                except Exception as e:
                    logger.error("Embedding generation failed: %s", e, exc_info=True)
                    results["errors"].append(f"Embeddings: {e}")

            # ── Step 4: Similar shot detection ────────────────────
            if detect_similar and not self._cancelled:
                current_step += 1
                self.signals.progress.emit(
                    "similar_shots", current_step, total_steps,
                    "Detecting similar shots..."
                )

                try:
                    from services.semantic_embedding_service import get_semantic_embedding_service
                    from services.stack_generation_service import StackGenerationService, StackGenParams
                    from repository.stack_repository import StackRepository

                    embedding_service = get_semantic_embedding_service(
                        model_name=canonical_model,
                    )
                    embedding_count = embedding_service.get_embedding_count()

                    if embedding_count == 0:
                        logger.warning(
                            "No embeddings found - skipping similar shot detection. "
                            "This is likely because the CLIP model failed to load. "
                            "Check: Preferences → Visual Embeddings for model path, "
                            "or try: pip install --upgrade transformers torch pillow"
                        )
                        results["errors"].append(
                            "No embeddings available for similar detection. "
                            "CLIP model may need reinstalling (pip install --upgrade transformers torch pillow)"
                        )
                    else:
                        stack_repo = StackRepository(db_conn)
                        stack_svc = StackGenerationService(
                            photo_repo=photo_repo,
                            stack_repo=stack_repo,
                            similarity_service=embedding_service,
                        )

                        from config.similarity_config import SimilarityConfig
                        from dataclasses import replace
                        params = SimilarityConfig.get_params()

                        overrides = {}
                        if self.options.get("time_window_seconds"):
                            overrides["time_window_seconds"] = self.options["time_window_seconds"]
                        if self.options.get("similarity_threshold"):
                            overrides["similarity_threshold"] = self.options["similarity_threshold"]
                        if self.options.get("min_stack_size"):
                            overrides["min_stack_size"] = self.options["min_stack_size"]
                        if overrides:
                            params = replace(params, **overrides)

                        stats = stack_svc.regenerate_similar_shot_stacks(
                            self.project_id, params
                        )
                        results["similar_stacks"] = stats.stacks_created
                        logger.info("Created %d similar shot stacks", stats.stacks_created)

                except Exception as e:
                    logger.error("Similar shot detection failed: %s", e, exc_info=True)
                    results["errors"].append(f"Similar shots: {e}")

            # ── Step 5: OCR text extraction ───────────────────────
            if run_ocr and not self._cancelled:
                current_step += 1
                self.signals.progress.emit(
                    "ocr", current_step, total_steps,
                    "Extracting text from photos (OCR)..."
                )

                try:
                    from workers.ocr_pipeline_worker import OCRPipelineWorker

                    ocr_worker = OCRPipelineWorker(
                        project_id=self.project_id,
                        languages=self.options.get("ocr_languages"),
                    )

                    # Run synchronously in this background thread
                    import threading
                    ocr_stats = {}
                    ocr_done = threading.Event()

                    def _on_ocr_finished(stats):
                        ocr_stats.update(stats)
                        ocr_done.set()

                    def _on_ocr_error(msg):
                        results["errors"].append(f"OCR: {msg}")
                        ocr_done.set()

                    def _on_ocr_progress(current, total, path, msg):
                        self.signals.progress.emit(
                            "ocr", current_step, total_steps,
                            f"Extracting text from photos... ({current}/{total})"
                        )

                    ocr_worker.signals.finished.connect(_on_ocr_finished)
                    ocr_worker.signals.error.connect(_on_ocr_error)
                    ocr_worker.signals.progress.connect(_on_ocr_progress)

                    ocr_worker.run()

                    results["ocr_processed"] = ocr_stats.get("processed", 0)
                    results["ocr_with_text"] = ocr_stats.get("with_text", 0)
                    logger.info(
                        "OCR complete: %d processed, %d with text",
                        results["ocr_processed"], results["ocr_with_text"],
                    )

                except Exception as e:
                    logger.error("OCR pipeline failed: %s", e, exc_info=True)
                    results["errors"].append(f"OCR: {e}")

            # Rebuild the flattened search_asset_features index so the
            # search orchestrator can use the fast path instead of the
            # slow JOIN-based fallback.
            try:
                from repository.search_feature_repository import SearchFeatureRepository
                repo = SearchFeatureRepository()
                if repo.table_exists():
                    count = repo.refresh_project(self.project_id)
                    logger.info(
                        "[PostScanPipelineWorker] Rebuilt search_asset_features: "
                        "%d rows for project %d", count, self.project_id,
                    )
            except Exception as e:
                logger.warning(
                    "[PostScanPipelineWorker] search_asset_features rebuild failed: %s", e
                )

            # Bump search index version so SmartFind caches are invalidated
            try:
                from services.smart_find_service import bump_index_version
                bump_index_version("post_scan_pipeline_complete")
            except Exception:
                pass

            self.signals.finished.emit(results)

        except Exception as e:
            logger.error("Post-scan pipeline failed: %s", e, exc_info=True)
            self.signals.error.emit(str(e))
