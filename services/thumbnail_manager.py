"""
ThumbnailManager - Thumbnail loading, caching, and delivery system (Pipeline A).

Extracted from main_window_qt.py (Phase 2, Step 2.4)

Components:
- _ThumbLoaded: Signal emitter for loaded thumbnails
- _ThumbTask: Worker thread for decoding/scaling images
- ThumbnailManager: Orchestrates thumbnail loading, caching, zoom, and delivery to grid

Responsibilities:
- Async thumbnail loading with QThreadPool
- LRU cache with size limit (prevents unbounded memory growth)
- Zoom level management
- Qt and Pillow decoder support
- Grid delivery abstraction

Note: This is Pipeline A. Pipeline C (ThumbWorker in thumbnail_grid_qt.py) is used
by the Current Layout and is the preferred pipeline for viewport-based lazy loading.
Pipeline A is used primarily for zoom integration in MainWindow.

Version: 09.20.00.00
"""

from typing import Optional, Dict, Tuple, Iterable
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QSize, Qt
from PySide6.QtGui import QPixmap, QImageReader


class _ThumbLoaded(QObject):
    loaded = Signal(str, int, QPixmap)  # path, size, pixmap


class _ThumbTask(QRunnable):
    """Worker: decode + scale a single image to 'size' and emit a pixmap."""
    def __init__(self, path: str, size: int, emitter: _ThumbLoaded):
        super().__init__()
        self.path = str(path)
        self.size = int(size)
        self.emitter = emitter
        self.setAutoDelete(True)

    def run(self):
        pm = None
        # Fast path: Qt decoder with scaled decode and EXIF auto-transform.
        try:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            reader.setScaledSize(QSize(self.size, self.size))
            img = reader.read()
            if img and not img.isNull():
                pm = QPixmap.fromImage(img)
        except Exception:
            pm = None

        # Fallback: Pillow if available.
        if pm is None:
            try:
                from PIL import Image, ImageQt
                # BUG-C1 FIX: Use context manager to prevent resource leak
                with Image.open(self.path) as im:
                    im.thumbnail((self.size, self.size), Image.LANCZOS)
                    if im.mode not in ("RGBA", "LA"):
                        im = im.convert("RGBA")
                    pm = QPixmap.fromImage(ImageQt.ImageQt(im))
            except Exception:
                pm = None

        # Last resort: gray placeholder.
        if pm is None:
            pm = QPixmap(self.size, self.size)
            pm.fill(Qt.lightGray)

        self.emitter.loaded.emit(self.path, self.size, pm)


class ThumbnailManager(QObject):
    """
    Orchestrates thumbnail loading, caching, zoom, and delivery to the grid.
    - grid: an object that can accept thumbnails via set_thumbnail(path, pixmap)
            or update_item_thumbnail(path, pixmap).
    - cache: optional dict-like; if None, an internal cache is used.
    - log: callable like gui_log.debug/info/warn (optional).

    CRITICAL: Internal cache now has size limit to prevent unbounded memory growth.
    """
    MAX_CACHE_SIZE = 500  # Maximum number of cached thumbnails

    def __init__(self, grid, cache: Optional[Dict[Tuple[str, int], QPixmap]], log=None, initial_size: int = 160):
        super().__init__()
        self._grid = grid
        self._log = log
        self._size = max(24, int(initial_size))
        # CRITICAL FIX: Use provided cache or create size-limited internal cache
        if cache is not None:
            self._cache = cache
            self._owns_cache = False
        else:
            self._cache: Dict[Tuple[str, int], QPixmap] = {}
            self._owns_cache = True  # We manage this cache
        self._emitter = _ThumbLoaded()
        self._emitter.loaded.connect(self._on_loaded)
        self._pool = QThreadPool.globalInstance()

        # detect grid API once (no hard dependency on exact class)
        self._apply_thumb = None
        if hasattr(self._grid, "set_thumbnail"):
            self._apply_thumb = self._grid.set_thumbnail
        elif hasattr(self._grid, "update_item_thumbnail"):
            self._apply_thumb = self._grid.update_item_thumbnail

    # ---------- public API ----------
    def load_thumbnails(self, image_paths: Iterable[str]) -> None:
        size = self._size
        for p in image_paths:
            key = (p, size)
            if key in self._cache:
                # Cache hit → push to grid immediately.
                self._deliver_to_grid(p, self._cache[key])
                continue
            # Submit async decode.
            self._pool.start(_ThumbTask(p, size, self._emitter))

    def update_zoom(self, factor: float) -> None:
        """
        'factor' can be a multiplier (e.g., 1.25) or an absolute int if >= 24.
        Re-renders visible thumbs at the new size (cache is keyed by size).
        """
        if isinstance(factor, (int, float)) and factor >= 24:
            new_size = int(factor)
        else:
            new_size = int(self._size * float(factor))
        new_size = max(24, min(1024, new_size))
        if new_size == self._size:
            return
        self._size = new_size
        if self._log:
            try:
                self._log.debug(f"[Thumbs] Zoom set → {self._size}px")
            except Exception:
                pass
        # Ask grid for visible paths if it can provide them, else do nothing.
        paths = None
        if hasattr(self._grid, "visible_paths"):
            try:
                paths = list(self._grid.visible_paths())
            except Exception:
                paths = None
        if paths:
            self.load_thumbnails(paths)

    def clear(self) -> None:
        """Optional: clear only current-size entries to free memory."""
        to_del = [k for k in self._cache.keys() if isinstance(k, tuple) and len(k) == 2 and k[1] == self._size]
        for k in to_del:
            self._cache.pop(k, None)

    # ---------- internal ----------
    def _on_loaded(self, path: str, size: int, pm: QPixmap) -> None:
        # store in cache and deliver
        self._cache[(path, size)] = pm

        # CRITICAL FIX: Evict oldest entries if internal cache grows too large
        if self._owns_cache and len(self._cache) > self.MAX_CACHE_SIZE:
            # Simple eviction: remove oldest 20% of entries
            num_to_remove = self.MAX_CACHE_SIZE // 5
            keys_to_remove = list(self._cache.keys())[:num_to_remove]
            for key in keys_to_remove:
                self._cache.pop(key, None)
            if self._log:
                try:
                    self._log.debug(f"[ThumbnailManager] Evicted {num_to_remove} old thumbnails (cache size: {len(self._cache)})")
                except Exception:
                    pass

        if size == self._size:
            self._deliver_to_grid(path, pm)

    def _deliver_to_grid(self, path: str, pm: QPixmap) -> None:
        if self._apply_thumb:
            try:
                self._apply_thumb(path, pm)
            except Exception:
                pass

    def shutdown_threads(self):
        """Gracefully shutdown thumbnail pool (for app close)."""
        try:
            if hasattr(self, "_pool") and self._pool:
                self._pool.waitForDone(1000)
                print("[ThumbnailManager] Thread pool shut down.")
        except Exception as e:
            print(f"[ThumbnailManager] shutdown error: {e}")
