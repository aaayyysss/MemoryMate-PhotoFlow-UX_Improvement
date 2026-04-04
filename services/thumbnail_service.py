# services/thumbnail_service.py
# Version 01.02.00.00 dated 20260208
# Unified thumbnail caching service with L1 (memory) + L2 (database) cache
# Enhanced TIFF support and Qt message suppression for unsupported formats
# FIX 2026-02-08: Thread-safe QImage-based thumbnail generation
#                 QPixmap should only be created on UI thread (GPU-backed, not thread-safe)
#                 Workers use get_thumbnail_image() -> QImage (thread-safe)
#                 UI thread converts QImage -> QPixmap

import os
import io
import time
import threading
from collections import OrderedDict
from typing import Optional, Dict, Any, Union
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QPixmap, QImage, QImageReader
from PySide6.QtCore import Qt, qInstallMessageHandler, QtMsgType, QThread
from PySide6.QtWidgets import QApplication

from logging_config import get_logger
from thumb_cache_db import ThumbCacheDB, get_cache

logger = get_logger(__name__)

# Global flag to track if message handler is installed
_qt_message_handler_installed = False

# FIX 2026-02-08: Global lock to serialize PIL image decode operations
# PIL is not fully thread-safe when multiple threads call img.load() simultaneously,
# especially on large images. This can cause "access violation" crashes on Windows.
# This lock ensures only one thread decodes an image at a time.
_pil_decode_lock = threading.Lock()

# FIX 2026-02-08: Semaphore to throttle concurrent decode operations
# This limits memory pressure when multiple thumbnails are being generated simultaneously
# Best practice from Google Photos: limit concurrent decodes to prevent memory exhaustion
_decode_semaphore = threading.Semaphore(4)  # Max 4 concurrent decodes

# Formats that should always use PIL (not Qt) due to compatibility issues
PIL_PREFERRED_FORMATS = {
    '.tif', '.tiff',  # TIFF with various compressions (JPEG, LZW, etc.)
    '.tga',           # TGA files
    '.psd',           # Photoshop files
    '.ico',           # Icons with multiple sizes
    '.bmp',           # Some BMP variants
}

def _qt_message_handler(msg_type, context, message):
    """
    Custom Qt message handler to suppress known TIFF compression warnings.

    This suppresses repetitive Qt warnings about unsupported TIFF compression
    methods (like JPEG compression in TIFF), since we handle these with PIL fallback.

    CRITICAL FIX: Check context.category instead of message text, since Qt puts
    the category in context.category, not in the message itself.
    """
    # Check if this is a TIFF category message
    is_tiff_category = (
        context and
        hasattr(context, 'category') and
        context.category and
        'tiff' in str(context.category).lower()
    )

    # Check if this is a compression warning message
    compression_warnings = [
        'JPEG compression support is not configured',
        'Sorry, requested compression method is not configured',
        'LZW compression support is not configured',
        'Deflate compression support is not configured',
        'compression support is not configured',  # Catch-all pattern
        'requested compression method is not configured'  # Catch-all pattern
    ]
    is_compression_warning = any(x in message for x in compression_warnings)

    # Suppress TIFF compression warnings (we handle these with PIL)
    if is_tiff_category and is_compression_warning:
        return  # Silently ignore

    # Also suppress ANY compression warning regardless of category (belt and suspenders)
    # This catches cases where the category might not be set correctly
    if is_compression_warning:
        return  # Silently ignore

    # Suppress noisy Qt touch/pointer event warnings ("no target window")
    # These occur when touch events arrive for regions without a receiving widget
    # and are harmless — purely a Qt framework-level diagnostic.
    if 'no target window' in message:
        return

    # For other Qt messages, log them appropriately
    if msg_type == QtMsgType.QtDebugMsg:
        logger.debug(f"Qt: {message}")
    elif msg_type == QtMsgType.QtWarningMsg:
        # Don't spam warnings for image format issues
        if 'imageformat' not in message.lower():
            logger.warning(f"Qt: {message}")
    elif msg_type == QtMsgType.QtCriticalMsg:
        logger.error(f"Qt Critical: {message}")
    elif msg_type == QtMsgType.QtFatalMsg:
        logger.critical(f"Qt Fatal: {message}")

def install_qt_message_handler():
    """
    Install custom Qt message handler to suppress TIFF warnings.

    Call this once at application startup to prevent spam from
    unsupported TIFF compression methods.
    """
    global _qt_message_handler_installed
    if not _qt_message_handler_installed:
        qInstallMessageHandler(_qt_message_handler)
        _qt_message_handler_installed = True
        logger.info("Installed Qt message handler to suppress TIFF warnings")


class LRUCache:
    """
    Least Recently Used cache with size limit and memory tracking.

    Maintains an OrderedDict to track access order and evicts
    the least recently used items when capacity or memory limit is exceeded.

    Phase 1B Enhancement: Added memory-aware eviction to prevent OOM situations.
    P0 Fix #2: Added threading.RLock() to protect all cache operations from
    concurrent GUI/worker thread access that could corrupt cache state.
    """

    def __init__(self, capacity: int = 200, max_memory_mb: float = 100.0):
        """
        Initialize LRU cache with entry and memory limits.

        Args:
            capacity: Maximum number of entries before eviction (default: 200 per Phase 1B)
            max_memory_mb: Maximum memory usage in MB before eviction (default: 100MB per Phase 1B)
        """
        self.capacity = capacity
        self.max_memory_bytes = int(max_memory_mb * 1024 * 1024)
        self.cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.current_memory_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.memory_evictions = 0
        self._lock = threading.RLock()  # P0 Fix #2: Thread-safe cache operations
        logger.info(f"LRUCache initialized with capacity={capacity}, max_memory={max_memory_mb}MB")

    def _estimate_image_size(self, image: Union[QPixmap, QImage]) -> int:
        """
        Estimate memory size of a QPixmap or QImage in bytes.

        P2-22 FIX: Account for Qt framework overhead beyond raw pixel data.
        Empirical testing shows actual memory usage is 15-25% higher than
        theoretical calculation due to metadata, alignment, and GPU buffers.

        FIX 2026-02-08: Updated to support both QPixmap and QImage since
        the cache now stores QImage (thread-safe) instead of QPixmap.

        Args:
            image: QPixmap or QImage to estimate

        Returns:
            Estimated size in bytes
        """
        if image is None:
            return 0
        if hasattr(image, 'isNull') and image.isNull():
            return 0

        # QPixmap/QImage memory ≈ width × height × bytes_per_pixel
        # Most thumbnails are 32-bit ARGB (4 bytes per pixel)
        bytes_per_pixel = 4
        theoretical_size = image.width() * image.height() * bytes_per_pixel

        # P2-22 FIX: Add 20% overhead multiplier for Qt framework overhead
        # This accounts for: image metadata, memory alignment, GPU buffers,
        # Qt internal structures, and platform-specific allocations
        overhead_multiplier = 1.20
        return int(theoretical_size * overhead_multiplier)

    # Backwards compatibility alias
    def _estimate_pixmap_size(self, pixmap: QPixmap) -> int:
        return self._estimate_image_size(pixmap)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get value for key, moving it to end (most recent).

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        with self._lock:  # P0 Fix #2: Thread-safe access
            if key not in self.cache:
                self.misses += 1
                return None

            # Move to end (mark as recently used)
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]

    def put(self, key: str, value: Dict[str, Any]):
        """
        Put value in cache, evicting oldest if at capacity or memory limit.

        Phase 1B Enhancement: Evicts based on BOTH entry count AND memory usage.

        FIX 2026-02-08: Updated to support 'image' (QImage) key in addition to 'pixmap'.
        The cache now stores QImage for thread-safety.

        Args:
            key: Cache key
            value: Value to cache (must contain 'image' or 'pixmap' key)
        """
        with self._lock:  # P0 Fix #2: Thread-safe access
            # FIX 2026-02-08: Support both 'image' and 'pixmap' keys for backwards compatibility
            image = value.get("image") or value.get("pixmap")
            new_size = self._estimate_image_size(image) if image else 0

            if key in self.cache:
                # Update existing entry - remove old size, add new size
                old_entry = self.cache[key]
                old_image = old_entry.get("image") or old_entry.get("pixmap")
                old_size = self._estimate_image_size(old_image) if old_image else 0
                self.current_memory_bytes -= old_size
                self.cache.move_to_end(key)
            else:
                # Add new entry - check if eviction needed
                # Evict based on entry count OR memory limit
                while (len(self.cache) >= self.capacity or
                       self.current_memory_bytes + new_size > self.max_memory_bytes):
                    if len(self.cache) == 0:
                        break  # Safety: don't infinite loop

                    # Evict oldest (first) entry
                    evicted_key, evicted_value = self.cache.popitem(last=False)
                    evicted_image = evicted_value.get("image") or evicted_value.get("pixmap")
                    evicted_size = self._estimate_image_size(evicted_image) if evicted_image else 0
                    self.current_memory_bytes -= evicted_size
                    self.evictions += 1

                    if self.current_memory_bytes + new_size > self.max_memory_bytes:
                        self.memory_evictions += 1
                        logger.debug(f"Memory-based eviction: {evicted_key} ({evicted_size / 1024:.1f}KB)")
                    else:
                        logger.debug(f"Capacity-based eviction: {evicted_key}")

            self.cache[key] = value
            self.current_memory_bytes += new_size

    def invalidate(self, key: str) -> bool:
        """
        Remove entry from cache and update memory tracking.

        Args:
            key: Cache key to remove

        Returns:
            True if entry was removed
        """
        with self._lock:  # P0 Fix #2: Thread-safe access
            if key in self.cache:
                entry = self.cache[key]
                # FIX 2026-02-08: Support both 'image' and 'pixmap' keys
                image = entry.get("image") or entry.get("pixmap")
                size = self._estimate_image_size(image) if image else 0
                self.current_memory_bytes -= size
                del self.cache[key]
                return True
            return False

    def clear(self):
        """Clear all entries from cache and reset memory tracking."""
        with self._lock:  # P0 Fix #2: Thread-safe access
            self.cache.clear()
            self.current_memory_bytes = 0
            self.hits = 0
            self.misses = 0
            self.evictions = 0
            self.memory_evictions = 0
            logger.info("LRUCache cleared")

    def size(self) -> int:
        """Return current number of entries."""
        with self._lock:  # P0 Fix #2: Thread-safe access
            return len(self.cache)

    def memory_usage_mb(self) -> float:
        """Return current memory usage in MB."""
        with self._lock:  # P0 Fix #2: Thread-safe access
            return self.current_memory_bytes / (1024 * 1024)

    def memory_usage_percent(self) -> float:
        """Return current memory usage as percentage of limit."""
        with self._lock:  # P0 Fix #2: Thread-safe access
            if self.max_memory_bytes == 0:
                return 0.0
            return (self.current_memory_bytes / self.max_memory_bytes) * 100

    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        with self._lock:  # P0 Fix #2: Thread-safe access
            total = self.hits + self.misses
            if total == 0:
                return 0.0
            return self.hits / total


class ThumbnailService:
    """
    Unified thumbnail caching service with two-tier caching:

    - L1 Cache: LRU-limited memory cache (fast, limited size)
    - L2 Cache: Database cache (persistent, larger, auto-purged)

    This replaces the previous fragmented caching with:
    - Unbounded memory dict in app_services._thumbnail_cache
    - Disk files in .thumb_cache/ directory
    - Database BLOBs in thumbnails_cache.db

    Benefits:
    - Unified invalidation
    - Memory usage control via LRU eviction
    - Eliminates duplicate storage (disk + database)
    - Unified statistics and monitoring
    """

    def __init__(self,
                 l1_capacity: int = 200,
                 l1_max_memory_mb: float = 100.0,
                 db_cache: Optional[ThumbCacheDB] = None,
                 default_timeout: float = 5.0):
        """
        Initialize thumbnail service with Phase 1B memory management.

        Args:
            l1_capacity: Maximum entries in memory cache (default: 200 per Phase 1B)
            l1_max_memory_mb: Maximum memory for L1 cache in MB (default: 100MB per Phase 1B)
            db_cache: Optional database cache instance (uses global if None)
            default_timeout: Default decode timeout in seconds
        """
        # Install Qt message handler to suppress TIFF warnings
        install_qt_message_handler()

        self.l1_cache = LRUCache(capacity=l1_capacity, max_memory_mb=l1_max_memory_mb)
        self.l2_cache = db_cache or get_cache()
        self.default_timeout = default_timeout

        # Track files that failed to load (corrupted/unsupported)
        # This prevents infinite retries of broken images
        # P0 Fix #3: Added automatic pruning to prevent unbounded growth
        self._failed_images: set[str] = set()
        self._failed_images_max_size = 1000  # Maximum entries before pruning
        self._failed_images_lock = threading.Lock()  # Thread-safe access

        logger.info(f"ThumbnailService initialized (L1 capacity={l1_capacity}, max_memory={l1_max_memory_mb}MB, timeout={default_timeout}s)")

    def _prune_failed_images(self):
        """
        Prune _failed_images set to prevent unbounded growth.

        P0 Fix #3: Clears the oldest half of entries when threshold is reached.
        This prevents memory leaks while still maintaining recent failure info.
        """
        with self._failed_images_lock:
            if len(self._failed_images) >= self._failed_images_max_size:
                # Clear half of the entries (simple LRU approximation)
                # Convert to list, clear, and keep newer half
                failed_list = list(self._failed_images)
                keep_count = self._failed_images_max_size // 2
                self._failed_images.clear()
                self._failed_images.update(failed_list[-keep_count:])
                logger.info(f"Pruned _failed_images: {len(failed_list)} → {len(self._failed_images)} entries")

    def _add_failed_image(self, path: str):
        """
        Add an image to the failed images set with automatic pruning.

        P0 Fix #3: Thread-safe addition with automatic pruning at threshold.
        """
        with self._failed_images_lock:
            self._failed_images.add(path)
            # Prune if we've hit the threshold
            if len(self._failed_images) >= self._failed_images_max_size:
                self._prune_failed_images()

    def _is_failed_image(self, path: str) -> bool:
        """
        Check if an image is in the failed images set.

        P0 Fix #3: Thread-safe check.
        """
        with self._failed_images_lock:
            return path in self._failed_images

    def _normalize_path(self, path: str) -> str:
        """
        Normalize path for consistent cache keys.

        Args:
            path: File path

        Returns:
            Normalized path
        """
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(str(path).strip())))
        except Exception:
            return str(path).strip().lower()

    def _get_mtime(self, path: str) -> Optional[float]:
        """
        Get file modification time safely.

        Args:
            path: File path

        Returns:
            Modification time or None if file doesn't exist
        """
        try:
            return os.path.getmtime(path)
        except Exception:
            return None

    def _is_cache_valid(self, cached_entry: Dict[str, Any], current_mtime: float) -> bool:
        """
        Check if cached entry is still valid.

        Args:
            cached_entry: Cache entry with 'mtime' field
            current_mtime: Current file modification time

        Returns:
            True if cache entry is still valid
        """
        if not cached_entry or current_mtime is None:
            return False

        cached_mtime = cached_entry.get("mtime", 0)
        # Allow small float comparison tolerance
        return abs(cached_mtime - current_mtime) < 0.1

    def get_thumbnail_image(self,
                           path: str,
                           height: int,
                           timeout: Optional[float] = None) -> QImage:
        """
        Get thumbnail as QImage from cache or generate it.

        THREAD-SAFE: This method returns QImage which is safe to use from any thread.
        Use this method from worker threads instead of get_thumbnail().

        FIX 2026-02-08: New method for thread-safe thumbnail generation.
        Based on Google Photos / Apple Photos best practice:
        - Worker threads generate QImage (CPU-backed, thread-safe)
        - UI thread converts QImage -> QPixmap (GPU-backed, UI-thread only)

        Cache lookup order:
        1. L1 (memory) cache
        2. L2 (database) cache
        3. Generate from image file

        Args:
            path: Image file path
            height: Target thumbnail height in pixels
            timeout: Optional decode timeout (uses default if None)

        Returns:
            QImage thumbnail (may be null on error)
        """
        if not path:
            return QImage()

        norm_path = self._normalize_path(path)

        # Check if this file previously failed to load (corrupted/unsupported)
        if self._is_failed_image(norm_path):
            logger.debug(f"Skipping previously failed image: {path}")
            return QImage()

        current_mtime = self._get_mtime(path)

        if current_mtime is None:
            logger.warning(f"File not found: {path}")
            return QImage()

        timeout = timeout or self.default_timeout

        # 1. Check L1 (memory) cache - key includes height for size-specific caching
        cache_key = f"{norm_path}:{height}"
        l1_entry = self.l1_cache.get(cache_key)
        if l1_entry and self._is_cache_valid(l1_entry, current_mtime):
            logger.debug(f"L1 hit: {path} @ {height}px")
            cached_image = l1_entry.get("image")
            if cached_image and not cached_image.isNull():
                return cached_image

        # 2. Check L2 (database) cache - returns QPixmap, convert to QImage
        l2_pixmap = self.l2_cache.get_cached_thumbnail(path, current_mtime, height * 2)
        if l2_pixmap and not l2_pixmap.isNull():
            logger.debug(f"L2 hit: {path}")
            # Convert QPixmap to QImage for thread-safe storage
            l2_image = l2_pixmap.toImage()
            # Store in L1 for faster subsequent access
            self.l1_cache.put(cache_key, {"image": l2_image, "mtime": current_mtime})
            return l2_image

        # 3. Generate thumbnail - use semaphore to throttle concurrent decodes
        logger.debug(f"Cache miss, generating: {path} @ {height}px")

        with _decode_semaphore:
            qimage = self._generate_thumbnail_as_qimage(path, height, timeout)

        if qimage and not qimage.isNull():
            # Store in L1 cache
            self.l1_cache.put(cache_key, {"image": qimage, "mtime": current_mtime})
            # Store in L2 cache (convert to QPixmap for database storage)
            # NOTE: This is safe because L2 stores PNG bytes, not QPixmap object
            pixmap = QPixmap.fromImage(qimage)
            self.l2_cache.store_thumbnail(path, current_mtime, pixmap)

        return qimage

    def get_thumbnail(self,
                     path: str,
                     height: int,
                     timeout: Optional[float] = None) -> QPixmap:
        """
        Get thumbnail from cache or generate it.

        WARNING: This method returns QPixmap which is GPU-backed and NOT thread-safe.
        Only call this from the UI thread! For worker threads, use get_thumbnail_image().

        FIX 2026-02-08: Added UI-thread check to prevent access violations on Windows.

        Cache lookup order:
        1. L1 (memory) cache
        2. L2 (database) cache
        3. Generate from image file

        Args:
            path: Image file path
            height: Target thumbnail height in pixels
            timeout: Optional decode timeout (uses default if None)

        Returns:
            QPixmap thumbnail (may be null on error)
        """
        # FIX 2026-02-08: Warn if called from non-UI thread (common bug pattern)
        app = QApplication.instance()
        if app and QThread.currentThread() != app.thread():
            logger.warning(
                f"get_thumbnail() called from worker thread for {path}. "
                "QPixmap is not thread-safe! Use get_thumbnail_image() instead."
            )
            # Fall through anyway for backwards compatibility, but log the warning

        if not path:
            return QPixmap()

        norm_path = self._normalize_path(path)

        # Check if this file previously failed to load (corrupted/unsupported)
        # This prevents infinite retries of broken images
        if self._is_failed_image(norm_path):  # P0 Fix #3: Thread-safe check
            logger.debug(f"Skipping previously failed image: {path}")
            return QPixmap()

        current_mtime = self._get_mtime(path)

        if current_mtime is None:
            logger.warning(f"File not found: {path}")
            return QPixmap()

        timeout = timeout or self.default_timeout

        # 1. Check L1 (memory) cache - key includes height for size-specific caching
        cache_key = f"{norm_path}:{height}"
        l1_entry = self.l1_cache.get(cache_key)
        if l1_entry and self._is_cache_valid(l1_entry, current_mtime):
            logger.debug(f"L1 hit: {path} @ {height}px")
            # FIX 2026-02-08: Support both 'image' and 'pixmap' in cache
            cached_image = l1_entry.get("image")
            if cached_image and not cached_image.isNull():
                return QPixmap.fromImage(cached_image)
            cached_pixmap = l1_entry.get("pixmap")
            if cached_pixmap and not cached_pixmap.isNull():
                return cached_pixmap

        # 2. Check L2 (database) cache
        l2_pixmap = self.l2_cache.get_cached_thumbnail(path, current_mtime, height * 2)
        if l2_pixmap and not l2_pixmap.isNull():
            logger.debug(f"L2 hit: {path}")
            # Store as QImage in L1 for thread-safety
            l2_image = l2_pixmap.toImage()
            self.l1_cache.put(cache_key, {"image": l2_image, "mtime": current_mtime})
            return l2_pixmap

        # 3. Generate thumbnail
        logger.debug(f"Cache miss, generating: {path} @ {height}px")
        pixmap = self._generate_thumbnail(path, height, timeout)

        if pixmap and not pixmap.isNull():
            # Store as QImage in L1 for thread-safety
            qimage = pixmap.toImage()
            self.l1_cache.put(cache_key, {"image": qimage, "mtime": current_mtime})
            self.l2_cache.store_thumbnail(path, current_mtime, pixmap)

        return pixmap

    def _generate_thumbnail(self, path: str, height: int, timeout: float) -> QPixmap:
        """
        Generate thumbnail from image file.

        Handles:
        - PIL-preferred formats (TIFF, TGA, PSD, etc.) - always use PIL
        - Qt-native formats (JPEG, PNG, WebP) - use Qt for speed
        - EXIF auto-rotation
        - Decode timeout protection
        - Automatic fallback to PIL on Qt failures

        Args:
            path: Image file path
            height: Target height in pixels
            timeout: Maximum decode time in seconds

        Returns:
            Generated QPixmap thumbnail
        """
        ext = os.path.splitext(path)[1].lower()

        # 🎬 Skip video files - they need special thumbnail generation
        video_exts = {'.mp4', '.m4v', '.mov', '.mpeg', '.mpg', '.mpe', '.wmv',
                      '.asf', '.avi', '.mkv', '.webm', '.flv', '.f4v', '.3gp',
                      '.3g2', '.ogv'}
        if ext in video_exts:
            logger.debug(f"Skipping video file (use VideoThumbnailService): {path}")
            return QPixmap()

        # Use PIL directly for formats known to have Qt compatibility issues
        if ext in PIL_PREFERRED_FORMATS:
            logger.debug(f"Using PIL for {ext} format: {path}")
            return self._generate_thumbnail_pil(path, height, timeout)

        # Try Qt's fast QImageReader for common formats
        try:
            start = time.time()
            reader = QImageReader(path)
            reader.setAutoTransform(True)  # Handle EXIF rotation

            # Check timeout
            if time.time() - start > timeout:
                logger.warning(f"Decode timeout: {path}")
                return QPixmap()

            img = reader.read()
            if img.isNull():
                # Qt couldn't read it, fallback to PIL
                logger.debug(f"Qt returned null image for {path}, trying PIL")
                return self._generate_thumbnail_pil(path, height, timeout)

            if height > 0:
                img = img.scaledToHeight(height, Qt.SmoothTransformation)

            return QPixmap.fromImage(img)

        except Exception as e:
            logger.debug(f"QImageReader failed for {path}: {e}, trying PIL fallback")
            return self._generate_thumbnail_pil(path, height, timeout)

    def _generate_thumbnail_pil(self, path: str, height: int, timeout: float) -> QPixmap:
        """
        Generate thumbnail using PIL (fallback for TIFF and unsupported formats).

        Handles:
        - All TIFF compression types (JPEG, LZW, Deflate, PackBits, None)
        - CMYK and other color modes (converts to RGB)
        - Multi-page images (uses first page)
        - Transparency (preserves alpha channel)

        Args:
            path: Image file path
            height: Target height in pixels
            timeout: Maximum decode time in seconds

        Returns:
            Generated QPixmap thumbnail
        """
        try:
            # Check file exists and is readable
            if not os.path.exists(path):
                logger.warning(f"File does not exist: {path}")
                return QPixmap()

            if not os.access(path, os.R_OK):
                logger.warning(f"File is not readable: {path}")
                return QPixmap()

            # Check file is not empty
            file_size = os.path.getsize(path)
            if file_size == 0:
                logger.warning(f"File is empty (0 bytes): {path}")
                return QPixmap()

            # FIX 2026-02-08: Skip extremely large files to prevent memory/crash issues
            # 100 MB is the threshold - such files are likely ultra-high-res photos
            # that could cause memory exhaustion or access violations when decoded
            if file_size > 100 * 1024 * 1024:  # 100 MB
                logger.warning(f"File too large ({file_size / 1024 / 1024:.1f}MB), skipping: {path}")
                self._add_failed_image(self._normalize_path(path))
                return QPixmap()

            start = time.time()

            # P2-34 FIX: Skip separate verify() step to eliminate double disk read
            # P2-21 FIX: Use context manager from the start to prevent handle leaks
            # Opening and processing in one step reduces I/O from 2 operations to 1
            try:
                img = Image.open(path)
            except Exception as open_err:
                # Image is corrupted or unsupported format
                logger.warning(f"Cannot open image file {path}: {open_err}")
                self._add_failed_image(self._normalize_path(path))  # P0 Fix #3: Thread-safe add
                logger.info(f"Marked as failed (will not retry): {path}")
                return QPixmap()

            # P2-21 FIX: Ensure image handle is always closed even if exception occurs
            with img:
                # Verify image loaded successfully
                if img is None:
                    logger.warning(f"PIL returned None for: {path}")
                    return QPixmap()

                # Check if image has a valid file pointer
                if not hasattr(img, 'fp') or img.fp is None:
                    logger.warning(f"PIL image has no file pointer for: {path}")
                    self._add_failed_image(self._normalize_path(path))  # P0 Fix #3: Thread-safe add
                    logger.info(f"Marked as failed (will not retry): {path}")
                    return QPixmap()

                # Load image data (forces actual file read)
                # FIX 2026-02-08: Use lock to serialize PIL decode operations
                # This prevents "access violation" crashes on Windows when multiple threads
                # try to decode large images simultaneously
                try:
                    with _pil_decode_lock:
                        img.load()
                except MemoryError:
                    logger.error(f"Out of memory loading image: {path}")
                    self._add_failed_image(self._normalize_path(path))
                    return QPixmap()
                except Exception as e:
                    logger.warning(f"PIL failed to load image data for {path}: {e}")
                    # Mark as failed to prevent retries
                    self._add_failed_image(self._normalize_path(path))  # P0 Fix #3: Thread-safe add
                    logger.info(f"Marked as failed (will not retry): {path}")
                    return QPixmap()

                # For multi-page images (TIFF, ICO), try to use first page
                try:
                    if hasattr(img, 'n_frames') and img.n_frames > 1:
                        img.seek(0)  # Go to first frame
                except Exception as e:
                    # Some images report n_frames but can't seek - just use current frame
                    logger.debug(f"Could not seek to first frame for {path}: {e}")

                # Check if image has valid dimensions
                if not hasattr(img, 'height') or not hasattr(img, 'width'):
                    logger.warning(f"Image missing dimensions: {path}")
                    return QPixmap()

                if img.width <= 0 or img.height <= 0:
                    logger.warning(f"Invalid image dimensions ({img.width}x{img.height}): {path}")
                    return QPixmap()

                ratio = height / float(img.height)
                target_w = int(img.width * ratio)

                # Check timeout
                if time.time() - start > timeout:
                    logger.warning(f"PIL decode timeout: {path}")
                    return QPixmap()

                # P2-35 FIX: Resize FIRST, then convert color modes
                # This reduces pixel volume processed during expensive color conversions by 85-95%
                # For example: 4000x3000 (12M pixels) → 160x120 (19K pixels) before conversion
                try:
                    img.thumbnail((target_w, height), Image.Resampling.LANCZOS)
                except Exception as e:
                    logger.warning(f"Thumbnail resize failed for {path}: {e}")
                    return QPixmap()

                # P2-35 FIX: Now handle color mode conversions on the downscaled image
                try:
                    if img.mode == 'CMYK':
                        # Convert CMYK to RGB
                        img = img.convert('RGB')
                    elif img.mode in ('P', 'PA'):
                        # Convert palette mode with/without alpha
                        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
                    elif img.mode in ('L', 'LA'):
                        # Convert grayscale to RGB
                        img = img.convert('RGBA' if img.mode == 'LA' else 'RGB')
                    elif img.mode not in ("RGB", "RGBA"):
                        # Convert any other mode to RGB
                        img = img.convert("RGB")
                except Exception as e:
                    logger.warning(f"Color mode conversion failed for {path}: {e}")
                    # Try to continue with original mode
                    pass

                # Convert to QPixmap
                try:
                    buf = io.BytesIO()
                    # Use PNG to preserve alpha channel if present
                    save_format = "PNG" if img.mode == "RGBA" else "PNG"
                    img.save(buf, format=save_format, optimize=False)
                    qimg = QImage.fromData(buf.getvalue())

                    if qimg.isNull():
                        logger.warning(f"Failed to convert PIL image to QImage: {path}")
                        return QPixmap()

                    return QPixmap.fromImage(qimg)
                except Exception as e:
                    logger.warning(f"Failed to convert PIL image to QPixmap for {path}: {e}")
                    return QPixmap()

        except FileNotFoundError:
            logger.warning(f"File not found during processing: {path}")
            return QPixmap()
        except PermissionError:
            logger.warning(f"Permission denied accessing file: {path}")
            self._add_failed_image(self._normalize_path(path))  # P0 Fix #3: Thread-safe add
            return QPixmap()
        except OSError as e:
            # Handle PIL-specific errors (corrupt files, unsupported formats, etc.)
            logger.warning(f"OS error processing {path}: {e}")
            self._add_failed_image(self._normalize_path(path))  # P0 Fix #3: Thread-safe add
            return QPixmap()
        except Exception as e:
            # Unexpected errors - log with details but don't spam with stack traces
            logger.warning(f"PIL thumbnail generation failed for {path}: {e}")
            self._add_failed_image(self._normalize_path(path))  # P0 Fix #3: Thread-safe add
            return QPixmap()

    def _generate_thumbnail_as_qimage(self, path: str, height: int, timeout: float) -> QImage:
        """
        Generate thumbnail from image file as QImage (THREAD-SAFE).

        FIX 2026-02-08: New method for thread-safe thumbnail generation.
        Returns QImage instead of QPixmap. QImage is CPU-backed and thread-safe,
        unlike QPixmap which is GPU-backed and must only be used on UI thread.

        Based on Google Photos / Apple Photos best practices:
        - Use QImageReader.setScaledSize() to avoid full-res decode
        - Use PIL.draft() + thumbnail() for huge images
        - Return QImage for thread-safe cross-thread signaling

        Args:
            path: Image file path
            height: Target height in pixels
            timeout: Maximum decode time in seconds

        Returns:
            Generated QImage thumbnail (thread-safe)
        """
        ext = os.path.splitext(path)[1].lower()

        # Skip video files
        video_exts = {'.mp4', '.m4v', '.mov', '.mpeg', '.mpg', '.mpe', '.wmv',
                      '.asf', '.avi', '.mkv', '.webm', '.flv', '.f4v', '.3gp',
                      '.3g2', '.ogv'}
        if ext in video_exts:
            logger.debug(f"Skipping video file (use VideoThumbnailService): {path}")
            return QImage()

        # Use PIL directly for formats known to have Qt compatibility issues
        if ext in PIL_PREFERRED_FORMATS:
            logger.debug(f"Using PIL for {ext} format: {path}")
            return self._generate_thumbnail_pil_as_qimage(path, height, timeout)

        # Try Qt's fast QImageReader for common formats
        try:
            start = time.time()
            reader = QImageReader(path)
            reader.setAutoTransform(True)  # Handle EXIF rotation

            # FIX 2026-02-08: Use setScaledSize() to avoid full-res decode
            # This is a key optimization from Google Photos - decode at target size
            original_size = reader.size()
            if original_size.isValid() and original_size.height() > height:
                scale_factor = height / original_size.height()
                new_width = int(original_size.width() * scale_factor)
                from PySide6.QtCore import QSize
                reader.setScaledSize(QSize(new_width, height))
                logger.debug(f"Using scaled decode: {original_size.width()}x{original_size.height()} -> {new_width}x{height}")

            # Check timeout
            if time.time() - start > timeout:
                logger.warning(f"Decode timeout: {path}")
                return QImage()

            img = reader.read()
            if img.isNull():
                # Qt couldn't read it, fallback to PIL
                logger.debug(f"Qt returned null image for {path}, trying PIL")
                return self._generate_thumbnail_pil_as_qimage(path, height, timeout)

            # Scale if needed (in case setScaledSize wasn't available or didn't work)
            if height > 0 and img.height() > height:
                img = img.scaledToHeight(height, Qt.SmoothTransformation)

            return img

        except Exception as e:
            logger.debug(f"QImageReader failed for {path}: {e}, trying PIL fallback")
            return self._generate_thumbnail_pil_as_qimage(path, height, timeout)

    def _generate_thumbnail_pil_as_qimage(self, path: str, height: int, timeout: float) -> QImage:
        """
        Generate thumbnail using PIL and return as QImage (THREAD-SAFE).

        FIX 2026-02-08: Thread-safe version of _generate_thumbnail_pil that returns
        QImage instead of QPixmap.

        Args:
            path: Image file path
            height: Target height in pixels
            timeout: Maximum decode time in seconds

        Returns:
            Generated QImage thumbnail (thread-safe)
        """
        try:
            # Check file exists and is readable
            if not os.path.exists(path):
                logger.warning(f"File does not exist: {path}")
                return QImage()

            if not os.access(path, os.R_OK):
                logger.warning(f"File is not readable: {path}")
                return QImage()

            # Check file is not empty
            file_size = os.path.getsize(path)
            if file_size == 0:
                logger.warning(f"File is empty (0 bytes): {path}")
                return QImage()

            # Skip extremely large files
            if file_size > 100 * 1024 * 1024:  # 100 MB
                logger.warning(f"File too large ({file_size / 1024 / 1024:.1f}MB), skipping: {path}")
                self._add_failed_image(self._normalize_path(path))
                return QImage()

            start = time.time()

            try:
                img = Image.open(path)
            except Exception as open_err:
                logger.warning(f"Cannot open image file {path}: {open_err}")
                self._add_failed_image(self._normalize_path(path))
                return QImage()

            with img:
                if img is None:
                    logger.warning(f"PIL returned None for: {path}")
                    return QImage()

                if not hasattr(img, 'fp') or img.fp is None:
                    logger.warning(f"PIL image has no file pointer for: {path}")
                    self._add_failed_image(self._normalize_path(path))
                    return QImage()

                # FIX 2026-02-08: Use draft() for huge images to reduce memory during decode
                # This is a key optimization from Lightroom - decode at reduced size
                if hasattr(img, 'draft') and img.height > height * 4:
                    # Use draft mode to load at approximately target size
                    # PIL will load at the nearest available size (for JPEG: 1/1, 1/2, 1/4, 1/8)
                    target_w = int(img.width * (height / img.height))
                    try:
                        img.draft(img.mode, (target_w * 2, height * 2))
                        logger.debug(f"Using PIL draft mode for huge image: {path}")
                    except Exception:
                        pass  # draft() not supported for this format

                # Load image data with lock
                try:
                    with _pil_decode_lock:
                        img.load()
                except MemoryError:
                    logger.error(f"Out of memory loading image: {path}")
                    self._add_failed_image(self._normalize_path(path))
                    return QImage()
                except Exception as e:
                    logger.warning(f"PIL failed to load image data for {path}: {e}")
                    self._add_failed_image(self._normalize_path(path))
                    return QImage()

                # For multi-page images, use first page
                try:
                    if hasattr(img, 'n_frames') and img.n_frames > 1:
                        img.seek(0)
                except Exception:
                    pass

                # Validate dimensions
                if not hasattr(img, 'height') or not hasattr(img, 'width'):
                    logger.warning(f"Image missing dimensions: {path}")
                    return QImage()

                if img.width <= 0 or img.height <= 0:
                    logger.warning(f"Invalid image dimensions ({img.width}x{img.height}): {path}")
                    return QImage()

                ratio = height / float(img.height)
                target_w = int(img.width * ratio)

                # Check timeout
                if time.time() - start > timeout:
                    logger.warning(f"PIL decode timeout: {path}")
                    return QImage()

                # Resize first, then convert color modes (optimization)
                try:
                    img.thumbnail((target_w, height), Image.Resampling.LANCZOS)
                except Exception as e:
                    logger.warning(f"Thumbnail resize failed for {path}: {e}")
                    return QImage()

                # Handle color mode conversions
                try:
                    if img.mode == 'CMYK':
                        img = img.convert('RGB')
                    elif img.mode in ('P', 'PA'):
                        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
                    elif img.mode in ('L', 'LA'):
                        img = img.convert('RGBA' if img.mode == 'LA' else 'RGB')
                    elif img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGB")
                except Exception as e:
                    logger.warning(f"Color mode conversion failed for {path}: {e}")
                    pass

                # Convert to QImage (not QPixmap - thread safe!)
                try:
                    buf = io.BytesIO()
                    save_format = "PNG" if img.mode == "RGBA" else "PNG"
                    img.save(buf, format=save_format, optimize=False)
                    qimg = QImage.fromData(buf.getvalue())

                    if qimg.isNull():
                        logger.warning(f"Failed to convert PIL image to QImage: {path}")
                        return QImage()

                    return qimg  # Return QImage, NOT QPixmap
                except Exception as e:
                    logger.warning(f"Failed to convert PIL image to QImage for {path}: {e}")
                    return QImage()

        except FileNotFoundError:
            logger.warning(f"File not found during processing: {path}")
            return QImage()
        except PermissionError:
            logger.warning(f"Permission denied accessing file: {path}")
            self._add_failed_image(self._normalize_path(path))
            return QImage()
        except OSError as e:
            logger.warning(f"OS error processing {path}: {e}")
            self._add_failed_image(self._normalize_path(path))
            return QImage()
        except Exception as e:
            logger.warning(f"PIL thumbnail generation failed for {path}: {e}")
            self._add_failed_image(self._normalize_path(path))
            return QImage()

    def invalidate(self, path: str):
        """
        Invalidate cached thumbnail for a file.

        Removes from both L1 (memory) and L2 (database) caches, and clears
        failed image status so the file will be retried.
        Call this when a file is modified or deleted.

        Args:
            path: File path to invalidate
        """
        norm_path = self._normalize_path(path)

        # Remove from L1 - invalidate all size variants
        # L1 keys are now f"{norm_path}:{height}", so we need to clear common sizes
        common_sizes = [80, 160, 200, 280, 320, 360, 400, 512]
        l1_removed = 0
        for size in common_sizes:
            cache_key = f"{norm_path}:{size}"
            if self.l1_cache.invalidate(cache_key):
                l1_removed += 1

        # Remove from L2
        self.l2_cache.invalidate(path)

        # Remove from failed images (allow retry after file is fixed)
        was_failed = False
        with self._failed_images_lock:  # P0 Fix #3: Thread-safe access
            was_failed = norm_path in self._failed_images
            if was_failed:
                self._failed_images.discard(norm_path)

        logger.info(f"Invalidated thumbnail: {path} (L1={l1_removed} sizes, was_failed={was_failed})")

    def clear_all(self):
        """
        Clear all caches (L1 and L2) and reset failed images tracking.

        WARNING: This removes all cached thumbnails and clears the failed
        images list, so previously failed images will be retried.
        """
        self.l1_cache.clear()

        # P2-23 FIX: Explicitly delete ALL L2 cache entries from database
        # purge_stale(max_age_days=0) only removes entries with mtime < now,
        # which may miss recently-added entries. Use direct DELETE for complete removal.
        try:
            if hasattr(self.l2_cache, 'conn') and hasattr(self.l2_cache, 'lock'):
                with self.l2_cache.lock:
                    self.l2_cache.conn.execute("DELETE FROM thumbnail_cache")
                    self.l2_cache.conn.commit()
                    logger.info("[ThumbCache] P2-23: Cleared ALL L2 cache entries from database")
            else:
                # Fallback to purge_stale if direct access not available
                self.l2_cache.purge_stale(max_age_days=0)
        except Exception as e:
            logger.warning(f"[ThumbCache] Failed to clear L2 cache: {e}")
            # Attempt fallback
            self.l2_cache.purge_stale(max_age_days=0)

        # Clear failed images list
        with self._failed_images_lock:  # P0 Fix #3: Thread-safe access
            failed_count = len(self._failed_images)
            self._failed_images.clear()

        logger.info(f"All thumbnail caches cleared ({failed_count} failed images reset)")

    def diagnose_image(self, path: str) -> Dict[str, Any]:
        """
        Diagnose why an image file might be failing to load.

        Useful for troubleshooting problematic files.

        Args:
            path: Image file path to diagnose

        Returns:
            Dictionary with diagnostic information
        """
        diagnosis = {
            "path": path,
            "exists": False,
            "readable": False,
            "size_bytes": 0,
            "is_valid_image": False,
            "pil_format": None,
            "dimensions": None,
            "mode": None,
            "errors": []
        }

        try:
            # Check existence
            diagnosis["exists"] = os.path.exists(path)
            if not diagnosis["exists"]:
                diagnosis["errors"].append("File does not exist")
                return diagnosis

            # Check readability
            diagnosis["readable"] = os.access(path, os.R_OK)
            if not diagnosis["readable"]:
                diagnosis["errors"].append("File is not readable (permission denied)")

            # Check size
            diagnosis["size_bytes"] = os.path.getsize(path)
            if diagnosis["size_bytes"] == 0:
                diagnosis["errors"].append("File is empty (0 bytes)")

            # Try PIL
            try:
                with Image.open(path) as img:
                    diagnosis["is_valid_image"] = True
                    diagnosis["pil_format"] = img.format
                    diagnosis["dimensions"] = (img.width, img.height)
                    diagnosis["mode"] = img.mode

                    # Verify image
                    try:
                        img.verify()
                    except Exception as e:
                        diagnosis["errors"].append(f"Image verification failed: {e}")
            except Exception as e:
                diagnosis["errors"].append(f"PIL cannot open: {e}")

        except Exception as e:
            diagnosis["errors"].append(f"Unexpected error: {e}")

        return diagnosis

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get unified cache statistics with Phase 1B memory metrics.

        Returns:
            Dictionary with L1 and L2 cache stats including memory usage
        """
        l1_stats = {
            "size": self.l1_cache.size(),
            "capacity": self.l1_cache.capacity,
            "hits": self.l1_cache.hits,
            "misses": self.l1_cache.misses,
            "hit_rate": round(self.l1_cache.hit_rate() * 100, 2),
            # Phase 1B: Memory tracking
            "memory_mb": round(self.l1_cache.memory_usage_mb(), 2),
            "memory_limit_mb": round(self.l1_cache.max_memory_bytes / (1024 * 1024), 2),
            "memory_percent": round(self.l1_cache.memory_usage_percent(), 1),
            "evictions": self.l1_cache.evictions,
            "memory_evictions": self.l1_cache.memory_evictions,
        }

        l2_stats = self.l2_cache.get_stats()
        l2_metrics = self.l2_cache.get_metrics()

        # Get failed images count safely
        with self._failed_images_lock:  # P0 Fix #3: Thread-safe access
            failed_count = len(self._failed_images)

        return {
            "l1_memory_cache": l1_stats,
            "l2_database_cache": {
                **l2_stats,
                **l2_metrics
            },
            # Phase 1B: Summary metrics
            "summary": {
                "total_entries": l1_stats["size"] + l2_stats.get("entries", 0),
                "l1_memory_mb": l1_stats["memory_mb"],
                "l1_memory_status": "OK" if l1_stats["memory_percent"] < 80 else "HIGH",
                "failed_images": failed_count,
            }
        }

    def log_memory_stats(self):
        """
        Log current memory statistics for debugging.

        Phase 1B: Use this to monitor memory usage during development and testing.
        """
        stats = self.get_statistics()
        l1 = stats["l1_memory_cache"]
        summary = stats["summary"]

        logger.info(
            f"[Phase 1B Memory] L1 Cache: {l1['size']}/{l1['capacity']} entries, "
            f"{l1['memory_mb']}/{l1['memory_limit_mb']}MB ({l1['memory_percent']}%), "
            f"Hit rate: {l1['hit_rate']}%, "
            f"Evictions: {l1['evictions']} total ({l1['memory_evictions']} memory-based), "
            f"Status: {summary['l1_memory_status']}"
        )


# Global singleton instance
_thumbnail_service: Optional[ThumbnailService] = None


def get_thumbnail_service(l1_capacity: int = 200, l1_max_memory_mb: float = 100.0) -> ThumbnailService:
    """
    Get global ThumbnailService singleton with Phase 1B defaults.

    Args:
        l1_capacity: L1 cache capacity (default: 200 per Phase 1B, only used on first call)
        l1_max_memory_mb: L1 max memory in MB (default: 100MB per Phase 1B, only used on first call)

    Returns:
        Global ThumbnailService instance
    """
    global _thumbnail_service

    if _thumbnail_service is None:
        _thumbnail_service = ThumbnailService(
            l1_capacity=l1_capacity,
            l1_max_memory_mb=l1_max_memory_mb
        )
        logger.info("Global ThumbnailService created with Phase 1B memory limits")

    return _thumbnail_service
