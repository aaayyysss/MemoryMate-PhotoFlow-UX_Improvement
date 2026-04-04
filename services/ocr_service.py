# services/ocr_service.py
# Version 01.00.00.00 dated 20260306
#
# OCR (Optical Character Recognition) service for extracting text from photos.
# Uses EasyOCR as the primary backend with Tesseract as a fallback.
#
# Architecture:
#   - Lazy model loading (EasyOCR reader is heavy ~200MB)
#   - Thread-safe via lock (one reader shared across workers)
#   - Stores extracted text in photo_metadata.ocr_text
#   - Populates ocr_fts5 FTS5 virtual table for fast MATCH queries
#   - Integrated into post-scan pipeline as optional step

import os
import threading
from typing import Optional, List, Tuple
from pathlib import Path

from logging_config import get_logger

logger = get_logger(__name__)

# Singleton lock for model loading
_model_lock = threading.Lock()
_reader_instance = None
_reader_backend = None   # "easyocr" | "tesseract" | None
_reader_failed = False   # Cache failure so we don't retry 29 times


def _get_reader(languages: Optional[List[str]] = None):
    """
    Get or create an OCR reader (singleton, thread-safe).

    Tries EasyOCR first, then falls back to pytesseract.

    Args:
        languages: Language codes for OCR (default: ['en'])

    Returns:
        OCR reader instance (easyocr.Reader or a _TesseractReader wrapper)

    Raises:
        ImportError: If neither easyocr nor pytesseract is available
    """
    global _reader_instance, _reader_backend, _reader_failed

    if _reader_instance is not None:
        return _reader_instance

    if _reader_failed:
        raise ImportError(
            "No OCR backend available. "
            "Install one of: pip install easyocr  OR  pip install pytesseract"
        )

    with _model_lock:
        # Double-check after acquiring lock
        if _reader_instance is not None:
            return _reader_instance
        if _reader_failed:
            raise ImportError("No OCR backend available (cached)")

        langs = languages or ['en']

        # Try EasyOCR first
        try:
            logger.info(f"[OCRService] Loading EasyOCR reader for languages: {langs}")
            import easyocr
            _reader_instance = easyocr.Reader(
                langs,
                gpu=_detect_gpu(),
                verbose=False,
            )
            _reader_backend = "easyocr"
            logger.info("[OCRService] EasyOCR reader loaded successfully")
            return _reader_instance
        except ImportError:
            logger.info("[OCRService] EasyOCR not installed, trying pytesseract fallback...")
        except Exception as e:
            logger.warning(f"[OCRService] EasyOCR failed to load: {e}, trying pytesseract fallback...")

        # Fallback: pytesseract
        try:
            import pytesseract
            # Verify tesseract binary is accessible
            pytesseract.get_tesseract_version()
            lang_str = '+'.join(langs)
            _reader_instance = _TesseractReader(lang_str)
            _reader_backend = "tesseract"
            logger.info(f"[OCRService] Tesseract OCR loaded (languages: {lang_str})")
            return _reader_instance
        except ImportError:
            logger.warning(
                "[OCRService] pytesseract not installed. "
                "Install with: pip install pytesseract"
            )
        except Exception as e:
            logger.warning(f"[OCRService] Tesseract not available: {e}")

        # Neither backend available - cache the failure
        _reader_failed = True
        raise ImportError(
            "No OCR backend available. "
            "Install one of: pip install easyocr  OR  pip install pytesseract  "
            "(pytesseract also requires the tesseract binary)"
        )


class _TesseractReader:
    """Minimal wrapper around pytesseract that mimics the EasyOCR readtext() API."""

    def __init__(self, lang: str = "eng"):
        self._lang = lang

    def readtext(self, image, detail=1):
        """
        Run Tesseract OCR and return results in EasyOCR format:
        list of (bbox, text, confidence).
        """
        import pytesseract
        from PIL import Image
        import numpy as np

        if isinstance(image, np.ndarray):
            image = Image.fromarray(image)

        data = pytesseract.image_to_data(
            image, lang=self._lang, output_type=pytesseract.Output.DICT
        )

        results = []
        n = len(data['text'])
        for i in range(n):
            text = data['text'][i].strip()
            conf = int(data['conf'][i])
            if conf < 0 or not text:
                continue
            confidence = conf / 100.0
            x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
            bbox = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            results.append((bbox, text, confidence))

        return results


def _detect_gpu() -> bool:
    """Detect if GPU (CUDA) is available for EasyOCR."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


class OCRService:
    """
    Service for extracting text from photos using OCR.

    Thread-safe. Shares a single EasyOCR reader across all instances.
    Designed to be called from background workers (never on UI thread).
    """

    # Minimum image dimensions for OCR (skip tiny thumbnails/icons)
    MIN_EDGE_SIZE = 100

    # Maximum image dimension before downscaling (memory safety)
    MAX_EDGE_SIZE = 4096

    # Minimum confidence threshold for OCR results
    MIN_CONFIDENCE = 0.3

    # Supported image extensions
    SUPPORTED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif',
        '.webp', '.heic', '.heif',
    }

    def __init__(self, languages: Optional[List[str]] = None):
        self._languages = languages or ['en']
        self._reader = None

    def _ensure_reader(self):
        """Lazy-load the OCR reader."""
        if self._reader is None:
            self._reader = _get_reader(self._languages)

    def is_supported(self, path: str) -> bool:
        """Check if a file is supported for OCR processing."""
        ext = Path(path).suffix.lower()
        return ext in self.SUPPORTED_EXTENSIONS

    def extract_text(self, image_path: str) -> Optional[str]:
        """
        Extract text from an image file.

        Args:
            image_path: Path to the image file

        Returns:
            Extracted text string, or None if no text found or error
        """
        if not os.path.exists(image_path):
            logger.warning(f"[OCRService] File not found: {image_path}")
            return None

        if not self.is_supported(image_path):
            return None

        try:
            # Load and validate image dimensions
            img = self._load_image(image_path)
            if img is None:
                return None

            # Run OCR
            self._ensure_reader()
            results = self._reader.readtext(img, detail=1)

            if not results:
                return None

            # Filter by confidence and join text
            text_parts = []
            for bbox, text, confidence in results:
                if confidence >= self.MIN_CONFIDENCE and text.strip():
                    text_parts.append(text.strip())

            if not text_parts:
                return None

            combined = ' '.join(text_parts)

            # Sanity check: if the "text" is mostly noise, skip it
            if len(combined) < 2:
                return None

            logger.debug(
                f"[OCRService] Extracted {len(text_parts)} text regions "
                f"({len(combined)} chars) from {Path(image_path).name}"
            )
            return combined

        except Exception as e:
            logger.error(
                f"[OCRService] OCR failed for {Path(image_path).name}: {e}"
            )
            return None

    def extract_text_with_regions(
        self, image_path: str
    ) -> List[Tuple[str, float, list]]:
        """
        Extract text with bounding boxes and confidence scores.

        Returns:
            List of (text, confidence, bbox) tuples
        """
        if not os.path.exists(image_path) or not self.is_supported(image_path):
            return []

        try:
            img = self._load_image(image_path)
            if img is None:
                return []

            self._ensure_reader()
            results = self._reader.readtext(img, detail=1)

            regions = []
            for bbox, text, confidence in results:
                if confidence >= self.MIN_CONFIDENCE and text.strip():
                    regions.append((text.strip(), confidence, bbox))

            return regions
        except Exception:
            return []

    def _load_image(self, image_path: str):
        """
        Load image as numpy array, with dimension validation and downscaling.

        Returns:
            numpy array or None if image is too small or can't be loaded
        """
        try:
            from PIL import Image
            import numpy as np

            img = Image.open(image_path)
            w, h = img.size

            # Skip tiny images (icons, thumbnails)
            if w < self.MIN_EDGE_SIZE or h < self.MIN_EDGE_SIZE:
                return None

            # Downscale if too large (memory safety)
            if max(w, h) > self.MAX_EDGE_SIZE:
                scale = self.MAX_EDGE_SIZE / max(w, h)
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            # Convert to RGB (EasyOCR expects RGB numpy array)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            return np.array(img)

        except Exception as e:
            logger.warning(
                f"[OCRService] Failed to load image {Path(image_path).name}: {e}"
            )
            return None

    def store_ocr_text(self, photo_id: int, text: str, project_id: int):
        """
        Store extracted OCR text in the database.

        Updates both photo_metadata.ocr_text and the ocr_fts5 FTS5 table.

        Args:
            photo_id: The photo's row ID in photo_metadata
            text: Extracted text to store
            project_id: Project ID for the photo
        """
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                # Update the ocr_text column
                conn.execute(
                    "UPDATE photo_metadata SET ocr_text = ? WHERE id = ?",
                    (text, photo_id),
                )

                # Update search_asset_features if it exists
                try:
                    conn.execute(
                        "UPDATE search_asset_features SET ocr_text = ? "
                        "WHERE path = (SELECT path FROM photo_metadata WHERE id = ?)",
                        (text, photo_id),
                    )
                except Exception:
                    pass  # Table may not exist yet

                # Sync FTS5 index
                # FTS5 content-sync tables need explicit INSERT/DELETE
                try:
                    # Delete old entry if exists
                    conn.execute(
                        "INSERT INTO ocr_fts5(ocr_fts5, rowid, ocr_text) "
                        "VALUES('delete', ?, "
                        "(SELECT ocr_text FROM photo_metadata WHERE id = ?))",
                        (photo_id, photo_id),
                    )
                except Exception:
                    pass  # No previous entry

                try:
                    conn.execute(
                        "INSERT INTO ocr_fts5(rowid, ocr_text) VALUES(?, ?)",
                        (photo_id, text),
                    )
                except Exception as e:
                    logger.debug(f"[OCRService] FTS5 insert skipped: {e}")

                conn.commit()
                logger.debug(
                    f"[OCRService] Stored OCR text for photo {photo_id} "
                    f"({len(text)} chars)"
                )

        except Exception as e:
            logger.error(f"[OCRService] Failed to store OCR text: {e}")

    def get_photos_needing_ocr(
        self, project_id: int, limit: int = 0
    ) -> List[Tuple[int, str]]:
        """
        Get photos that haven't been OCR-processed yet.

        Returns:
            List of (photo_id, path) tuples for photos with NULL ocr_text
        """
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                sql = """
                    SELECT id, path FROM photo_metadata
                    WHERE project_id = ?
                      AND ocr_text IS NULL
                      AND path IS NOT NULL
                    ORDER BY id
                """
                params: list = [project_id]
                if limit > 0:
                    sql += " LIMIT ?"
                    params.append(limit)

                rows = conn.execute(sql, params).fetchall()
                return [(row['id'], row['path']) for row in rows]

        except Exception as e:
            logger.error(f"[OCRService] Failed to query photos needing OCR: {e}")
            return []

    def get_ocr_stats(self, project_id: int) -> dict:
        """Get OCR processing statistics for a project."""
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN ocr_text IS NOT NULL THEN 1 ELSE 0 END) as processed,
                        SUM(CASE WHEN ocr_text IS NOT NULL AND ocr_text != '' THEN 1 ELSE 0 END) as with_text
                    FROM photo_metadata
                    WHERE project_id = ?
                """, (project_id,)).fetchone()

                return {
                    "total": row['total'],
                    "processed": row['processed'],
                    "with_text": row['with_text'],
                    "pending": row['total'] - row['processed'],
                }
        except Exception as e:
            logger.error(f"[OCRService] Failed to get OCR stats: {e}")
            return {"total": 0, "processed": 0, "with_text": 0, "pending": 0}


# Module-level convenience
_ocr_service_instance = None
_ocr_service_lock = threading.Lock()


def get_ocr_service(languages: Optional[List[str]] = None) -> OCRService:
    """Get or create the singleton OCR service."""
    global _ocr_service_instance
    if _ocr_service_instance is None:
        with _ocr_service_lock:
            if _ocr_service_instance is None:
                _ocr_service_instance = OCRService(languages)
    return _ocr_service_instance
