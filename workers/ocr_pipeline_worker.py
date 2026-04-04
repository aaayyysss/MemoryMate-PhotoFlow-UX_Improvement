# workers/ocr_pipeline_worker.py
# Version 01.00.00.00 dated 20260306
#
# Background QRunnable worker that runs OCR text extraction on photos
# that haven't been processed yet. Designed to run as part of the
# post-scan pipeline or standalone.
#
# Architecture:
#   - Runs in QThreadPool (off UI thread)
#   - Processes photos in batches
#   - Emits progress signals for status bar updates
#   - Cancellable via cancel() flag
#   - Stores results in photo_metadata.ocr_text + ocr_fts5

import time
from typing import Optional, List

from PySide6.QtCore import QRunnable, QObject, Signal

from logging_config import get_logger

logger = get_logger(__name__)


class OCRPipelineSignals(QObject):
    """Signals emitted by the OCR pipeline worker."""
    # (current, total, photo_path, message)
    progress = Signal(int, int, str, str)
    # Emitted when pipeline finishes: {processed, with_text, failed, skipped}
    finished = Signal(dict)
    # Emitted on fatal error
    error = Signal(str)


class OCRPipelineWorker(QRunnable):
    """
    Background worker that extracts text from photos using OCR.

    Processes all photos in a project that haven't been OCR-scanned yet.
    Thread-safe, cancellable, emits progress signals.
    """

    def __init__(
        self,
        project_id: int,
        photo_ids: Optional[List[int]] = None,
        languages: Optional[List[str]] = None,
        batch_size: int = 50,
    ):
        """
        Args:
            project_id: Project to process
            photo_ids: Specific photo IDs (None = all unprocessed)
            languages: OCR language codes (default: ['en'])
            batch_size: Log progress every N photos
        """
        super().__init__()
        self.setAutoDelete(True)
        self.signals = OCRPipelineSignals()
        self.project_id = project_id
        self.photo_ids = photo_ids
        self.languages = languages
        self.batch_size = batch_size
        self._cancelled = False

    def cancel(self):
        """Request cancellation of the worker."""
        self._cancelled = True

    def run(self):
        """Execute OCR extraction in background thread."""
        logger.info(
            f"[OCRPipelineWorker] Starting OCR pipeline for project {self.project_id}"
        )

        stats = {
            "processed": 0,
            "with_text": 0,
            "failed": 0,
            "skipped": 0,
        }

        try:
            from services.ocr_service import OCRService

            ocr = OCRService(self.languages)

            # Fail fast: verify OCR backend is available before iterating photos
            try:
                ocr._ensure_reader()
            except ImportError as e:
                msg = (
                    f"OCR not available: {e}. "
                    "Install one of: pip install easyocr  OR  pip install pytesseract"
                )
                logger.error(f"[OCRPipelineWorker] {msg}")
                self.signals.error.emit(msg)
                return

            # Get photos to process
            if self.photo_ids:
                # Resolve paths for specified IDs
                photos = self._resolve_photo_paths(self.photo_ids)
            else:
                photos = ocr.get_photos_needing_ocr(self.project_id)

            total = len(photos)
            if total == 0:
                logger.info("[OCRPipelineWorker] No photos need OCR processing")
                self.signals.finished.emit(stats)
                return

            logger.info(f"[OCRPipelineWorker] Processing {total} photos")
            last_log = time.time()

            for i, (photo_id, path) in enumerate(photos, 1):
                if self._cancelled:
                    logger.info(
                        f"[OCRPipelineWorker] Cancelled at {i}/{total}"
                    )
                    break

                self.signals.progress.emit(
                    i, total, path, "Extracting text..."
                )

                try:
                    if not ocr.is_supported(path):
                        stats["skipped"] += 1
                        # Mark as processed with empty string to avoid re-processing
                        ocr.store_ocr_text(photo_id, "", self.project_id)
                        continue

                    text = ocr.extract_text(path)
                    stats["processed"] += 1

                    if text:
                        stats["with_text"] += 1
                        ocr.store_ocr_text(photo_id, text, self.project_id)
                    else:
                        # Store empty string to mark as processed
                        ocr.store_ocr_text(photo_id, "", self.project_id)

                except Exception as e:
                    stats["failed"] += 1
                    logger.warning(
                        f"[OCRPipelineWorker] Failed on photo {photo_id}: {e}"
                    )

                # Periodic logging
                now = time.time()
                if i % self.batch_size == 0 or (now - last_log) >= 30:
                    logger.info(
                        f"[OCRPipelineWorker] Progress: {i}/{total} "
                        f"(text_found={stats['with_text']}, failed={stats['failed']})"
                    )
                    last_log = now

            logger.info(
                f"[OCRPipelineWorker] Complete: processed={stats['processed']}, "
                f"with_text={stats['with_text']}, failed={stats['failed']}, "
                f"skipped={stats['skipped']}"
            )
            self.signals.finished.emit(stats)

        except ImportError as e:
            msg = (
                f"OCR dependencies not available: {e}. "
                "Install one of: pip install easyocr  OR  pip install pytesseract"
            )
            logger.error(f"[OCRPipelineWorker] {msg}")
            self.signals.error.emit(msg)
        except Exception as e:
            logger.error(
                f"[OCRPipelineWorker] Pipeline failed: {e}", exc_info=True
            )
            self.signals.error.emit(str(e))

    def _resolve_photo_paths(self, photo_ids: List[int]):
        """Resolve photo IDs to (id, path) tuples."""
        try:
            from repository.base_repository import DatabaseConnection

            db = DatabaseConnection()
            placeholders = ','.join(['?'] * len(photo_ids))
            with db.get_connection() as conn:
                rows = conn.execute(
                    f"SELECT id, path FROM photo_metadata "
                    f"WHERE id IN ({placeholders})",
                    photo_ids,
                ).fetchall()
                return [(row['id'], row['path']) for row in rows]
        except Exception as e:
            logger.error(f"[OCRPipelineWorker] Path resolution failed: {e}")
            return []
