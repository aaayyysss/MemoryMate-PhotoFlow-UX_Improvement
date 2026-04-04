# services/safe_image_loader.py
# Version 01.00.00.00 dated 20260212
"""
SafeImageLoader - Memory-safe image decoding with retry ladder.

Consolidates all image decoding behind a single, memory-safe codepath.
Based on best practices from Google Photos, Apple Photos, and Lightroom:

- Decode at target size (never full resolution for UI display)
- QImageReader.setScaledSize() for pre-decode downsampling
- PIL draft() mode for huge JPEG images
- Retry ladder on decode failure (2560 → 1920 → 1280 → placeholder)
- Null checks at every step
- Thread-safe (returns QImage, never QPixmap)
- Instrumented logging for diagnostics

Usage:
    from services.safe_image_loader import safe_decode_qimage, create_placeholder

    # Load for lightbox viewport (capped at 2560px max edge)
    qimage = safe_decode_qimage(path, max_dim=2560)

    # Load thumbnail (256px max edge)
    qimage = safe_decode_qimage(path, max_dim=256)
"""

import os
import io
import time
import threading
from typing import Optional, Tuple

from PySide6.QtGui import QImage, QImageReader, QPainter, QColor, QFont
from PySide6.QtCore import Qt, QSize

from logging_config import get_logger

logger = get_logger(__name__)

# Serialize PIL decode operations (PIL is not fully thread-safe)
_pil_decode_lock = threading.Lock()

# Throttle concurrent decodes to limit memory pressure
_decode_semaphore = threading.Semaphore(4)

# Retry ladder dimensions (descending)
RETRY_LADDER = [2560, 1920, 1280, 800]

# Formats that should always use PIL (Qt has compatibility issues)
PIL_PREFERRED_FORMATS = {
    '.tif', '.tiff', '.tga', '.psd', '.ico', '.bmp',
}

# RAW formats that need rawpy
RAW_FORMATS = {
    '.cr2', '.cr3', '.nef', '.arw', '.orf', '.rw2', '.dng',
    '.raf', '.pef', '.srw', '.x3f', '.3fr', '.rwl', '.mrw',
}


def safe_decode_qimage(
    path: str,
    max_dim: int = 2560,
    timeout: float = 30.0,
    enable_retry_ladder: bool = True,
) -> QImage:
    """
    Decode an image file into a QImage, capped at max_dim pixels on the longest edge.

    This is the single entry point for ALL image decoding in the UI.
    Never decodes full resolution unless max_dim >= original size.

    Thread-safe: returns QImage (CPU-backed), never QPixmap.

    Args:
        path: Absolute path to image file.
        max_dim: Maximum dimension (width or height) in pixels.
                 Default 2560 = "full quality for viewport" (not raw resolution).
        timeout: Maximum decode time in seconds.
        enable_retry_ladder: If True, retry with smaller sizes on failure.

    Returns:
        QImage (may be a placeholder on total failure, never null).
    """
    if not path or not os.path.exists(path):
        logger.warning(f"[SafeImageLoader] File not found: {path}")
        return create_placeholder(max_dim, "File not found")

    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    start = time.perf_counter()

    ext = os.path.splitext(path)[1].lower()
    basename = os.path.basename(path)

    # Build retry ladder: requested size first, then fallbacks
    sizes_to_try = [max_dim]
    if enable_retry_ladder:
        for ladder_dim in RETRY_LADDER:
            if ladder_dim < max_dim and ladder_dim not in sizes_to_try:
                sizes_to_try.append(ladder_dim)

    last_error = ""
    for attempt_dim in sizes_to_try:
        try:
            with _decode_semaphore:
                if ext in RAW_FORMATS:
                    result = _decode_raw(path, attempt_dim, timeout)
                elif ext in PIL_PREFERRED_FORMATS:
                    result = _decode_pil(path, attempt_dim, timeout)
                else:
                    result = _decode_qt(path, attempt_dim, timeout)

                if result is not None and not result.isNull():
                    elapsed = time.perf_counter() - start
                    logger.info(
                        f"[SafeImageLoader] OK {basename} "
                        f"target={attempt_dim}px actual={result.width()}x{result.height()} "
                        f"file={file_size_mb:.1f}MB time={elapsed:.2f}s"
                    )
                    return result

                last_error = f"decode returned null at {attempt_dim}px"
                logger.warning(
                    f"[SafeImageLoader] Null result for {basename} at {attempt_dim}px, "
                    f"trying smaller..."
                )

        except MemoryError:
            last_error = f"MemoryError at {attempt_dim}px"
            logger.error(
                f"[SafeImageLoader] MemoryError decoding {basename} at {attempt_dim}px"
            )
        except Exception as e:
            last_error = f"{e} at {attempt_dim}px"
            logger.warning(
                f"[SafeImageLoader] Error decoding {basename} at {attempt_dim}px: {e}"
            )

    # All retries failed - return placeholder
    elapsed = time.perf_counter() - start
    logger.error(
        f"[SafeImageLoader] FAILED all decode attempts for {basename}: {last_error} "
        f"({elapsed:.2f}s)"
    )
    return create_placeholder(min(max_dim, 400), f"Cannot load\n{basename[:40]}")


def create_placeholder(size: int = 400, message: str = "Image Error") -> QImage:
    """
    Create a non-null placeholder QImage for display when decoding fails.

    Returns a valid QImage so UI code never receives null.
    """
    dim = max(min(size, 400), 100)
    placeholder = QImage(dim, dim, QImage.Format_RGB32)
    placeholder.fill(QColor(40, 40, 40))

    painter = QPainter(placeholder)
    painter.setRenderHint(QPainter.Antialiasing)

    # Border
    painter.setPen(QColor(100, 100, 100))
    painter.drawRect(0, 0, dim - 1, dim - 1)

    # Icon area
    painter.setPen(QColor(160, 160, 160))
    font = QFont()
    font.setPointSize(max(dim // 8, 12))
    painter.setFont(font)
    painter.drawText(placeholder.rect(), Qt.AlignCenter, message)

    painter.end()
    return placeholder


# ---------------------------------------------------------------------------
# Internal decoders
# ---------------------------------------------------------------------------

def _decode_qt(path: str, max_dim: int, timeout: float) -> Optional[QImage]:
    """
    Decode using QImageReader with pre-decode scaling.

    This is the fastest path for JPEG/PNG/WebP.
    Uses setScaledSize() to avoid full-resolution decode.
    """
    start = time.perf_counter()

    reader = QImageReader(path)
    reader.setAutoTransform(True)  # EXIF rotation

    if not reader.canRead():
        logger.debug(f"[SafeImageLoader] Qt cannot read: {path}, falling back to PIL")
        return _decode_pil(path, max_dim, timeout)

    original_size = reader.size()
    if not original_size.isValid():
        logger.debug(f"[SafeImageLoader] Qt invalid size for: {path}, falling back to PIL")
        return _decode_pil(path, max_dim, timeout)

    orig_w, orig_h = original_size.width(), original_size.height()
    target_w, target_h = _fit_dimensions(orig_w, orig_h, max_dim)

    # Pre-decode scaling: decode directly at target size (key RAM saver)
    if max(orig_w, orig_h) > max_dim:
        reader.setScaledSize(QSize(target_w, target_h))
        logger.debug(
            f"[SafeImageLoader] Qt scaled decode: {orig_w}x{orig_h} -> {target_w}x{target_h}"
        )

    # Timeout check
    if time.perf_counter() - start > timeout:
        logger.warning(f"[SafeImageLoader] Qt decode timeout (setup): {path}")
        return None

    img = reader.read()

    if img.isNull():
        error = reader.errorString()
        logger.debug(f"[SafeImageLoader] Qt read failed: {path} ({error}), trying PIL")
        return _decode_pil(path, max_dim, timeout)

    # Post-decode safety scaling (if setScaledSize didn't work or was approximate)
    if img.width() > max_dim or img.height() > max_dim:
        img = _scale_qimage(img, max_dim)

    elapsed = time.perf_counter() - start
    logger.debug(
        f"[SafeImageLoader] Qt decoded {os.path.basename(path)}: "
        f"{orig_w}x{orig_h} -> {img.width()}x{img.height()} in {elapsed:.2f}s"
    )
    return img


def _decode_pil(path: str, max_dim: int, timeout: float) -> Optional[QImage]:
    """
    Decode using PIL with draft mode and thread-safe locking.

    Handles TIFF, TGA, PSD, and fallback for Qt failures.
    Uses PIL.draft() to reduce memory during JPEG decode.
    """
    start = time.perf_counter()

    try:
        # Validate file
        file_size = os.path.getsize(path)
        if file_size == 0:
            logger.warning(f"[SafeImageLoader] Empty file: {path}")
            return None
        if file_size > 200 * 1024 * 1024:
            logger.warning(f"[SafeImageLoader] File too large ({file_size // (1024*1024)}MB): {path}")
            return None

        from PIL import Image, ImageOps

        img = Image.open(path)

        with img:
            # EXIF orientation
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass  # Some formats don't support EXIF

            orig_w, orig_h = img.width, img.height

            # Draft mode for huge JPEG images (decode at reduced resolution)
            if hasattr(img, 'draft') and max(orig_w, orig_h) > max_dim * 4:
                target_w, target_h = _fit_dimensions(orig_w, orig_h, max_dim)
                try:
                    img.draft(img.mode, (target_w * 2, target_h * 2))
                    logger.debug(f"[SafeImageLoader] PIL draft mode for: {path}")
                except Exception:
                    pass

            # Thread-safe decode
            with _pil_decode_lock:
                img.load()

            if time.perf_counter() - start > timeout:
                logger.warning(f"[SafeImageLoader] PIL decode timeout: {path}")
                return None

            # Resize to target
            target_w, target_h = _fit_dimensions(img.width, img.height, max_dim)
            if img.width > target_w or img.height > target_h:
                img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)

            # Color mode conversion
            if img.mode == 'CMYK':
                img = img.convert('RGB')
            elif img.mode in ('P', 'PA'):
                img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
            elif img.mode in ('L', 'LA', 'I', 'F'):
                img = img.convert('RGB')
            elif img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')

            # Convert to QImage via buffer (thread-safe)
            buf = io.BytesIO()
            fmt = "PNG" if img.mode == "RGBA" else "JPEG"
            quality_args = {"quality": 95} if fmt == "JPEG" else {"optimize": False}
            img.save(buf, format=fmt, **quality_args)

            qimg = QImage.fromData(buf.getvalue())

            if qimg.isNull():
                logger.warning(f"[SafeImageLoader] PIL->QImage conversion failed: {path}")
                return None

            elapsed = time.perf_counter() - start
            logger.debug(
                f"[SafeImageLoader] PIL decoded {os.path.basename(path)}: "
                f"{orig_w}x{orig_h} -> {qimg.width()}x{qimg.height()} in {elapsed:.2f}s"
            )
            return qimg

    except MemoryError:
        logger.error(f"[SafeImageLoader] PIL MemoryError: {path}")
        return None
    except Exception as e:
        logger.warning(f"[SafeImageLoader] PIL decode failed for {path}: {e}")
        return None


def _decode_raw(path: str, max_dim: int, timeout: float) -> Optional[QImage]:
    """
    Decode RAW files using rawpy with half_size option for memory safety.
    Falls back to PIL if rawpy not available.
    """
    try:
        import rawpy

        start = time.perf_counter()

        with rawpy.imread(path) as raw:
            # Use half_size for large images to reduce memory
            use_half = max_dim <= 2560
            rgb = raw.postprocess(
                use_camera_wb=True,
                half_size=use_half,
                no_auto_bright=False,
                output_bps=8,
            )

        from PIL import Image
        pil_img = Image.fromarray(rgb)
        del rgb  # Free raw array

        # Resize to target
        target_w, target_h = _fit_dimensions(pil_img.width, pil_img.height, max_dim)
        if pil_img.width > target_w or pil_img.height > target_h:
            pil_img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)

        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')

        buf = io.BytesIO()
        pil_img.save(buf, format='JPEG', quality=95)
        pil_img.close()

        qimg = QImage.fromData(buf.getvalue())
        if qimg.isNull():
            return None

        elapsed = time.perf_counter() - start
        logger.debug(
            f"[SafeImageLoader] RAW decoded {os.path.basename(path)}: "
            f"{qimg.width()}x{qimg.height()} in {elapsed:.2f}s"
        )
        return qimg

    except ImportError:
        logger.debug(f"[SafeImageLoader] rawpy not available, trying PIL for: {path}")
        return _decode_pil(path, max_dim, timeout)
    except Exception as e:
        logger.warning(f"[SafeImageLoader] RAW decode failed for {path}: {e}")
        return _decode_pil(path, max_dim, timeout)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fit_dimensions(orig_w: int, orig_h: int, max_dim: int) -> Tuple[int, int]:
    """
    Calculate target dimensions that fit within max_dim, preserving aspect ratio.
    """
    if orig_w <= 0 or orig_h <= 0:
        return (max_dim, max_dim)

    if max(orig_w, orig_h) <= max_dim:
        return (orig_w, orig_h)

    if orig_w >= orig_h:
        scale = max_dim / orig_w
    else:
        scale = max_dim / orig_h

    return (max(1, int(orig_w * scale)), max(1, int(orig_h * scale)))


def _scale_qimage(img: QImage, max_dim: int) -> QImage:
    """Scale a QImage so its longest edge is at most max_dim."""
    if img.isNull():
        return img
    if max(img.width(), img.height()) <= max_dim:
        return img

    if img.width() >= img.height():
        return img.scaledToWidth(max_dim, Qt.SmoothTransformation)
    else:
        return img.scaledToHeight(max_dim, Qt.SmoothTransformation)
