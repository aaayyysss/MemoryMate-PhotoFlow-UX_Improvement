# ffmpeg_detection_worker.py
# version 01.01.00.00 dated 20260225

"""
Async FFmpeg/FFprobe Detection Worker

Implements background detection of FFmpeg/FFprobe to prevent UI freezing
during application startup, following the same pattern as other async workers.

Delegates to utils.ffmpeg_check.check_ffmpeg_availability() which checks:
1. Custom ffprobe_path from user settings (Preferences → Video Settings)
2. Auto-detection from common locations (C:\\ffmpeg\\bin, etc.)
3. System PATH as fallback

The cache (.ffmpeg_cache.json) stores the configured ffprobe_path alongside
detection results.  If the user changes the path in Preferences, the cache
is automatically invalidated on next launch (path mismatch) and also
explicitly deleted by the preferences save handler.
"""

from typing import Optional, Tuple
from PySide6.QtCore import QRunnable, QObject, Signal
import time
import json
from pathlib import Path

from logging_config import get_logger

logger = get_logger(__name__)

# Cache file for storing detection results
device_cache_file = Path(__file__).parent.parent / ".ffmpeg_cache.json"
CACHE_EXPIRY_SECONDS = 3600  # 1 hour cache


def invalidate_cache():
    """Delete the FFmpeg detection cache file.

    Call this when the user changes the ffprobe_path setting so that
    the next startup performs a fresh detection.
    """
    try:
        if device_cache_file.exists():
            device_cache_file.unlink()
            logger.info("[FFmpegDetectionWorker] Cache invalidated")
    except Exception as e:
        logger.debug(f"[FFmpegDetectionWorker] Failed to invalidate cache: {e}")


class FFmpegDetectionSignals(QObject):
    """Signals for FFmpeg detection worker."""
    detection_complete = Signal(bool, bool, str)  # (ffmpeg_available, ffprobe_available, message)
    error = Signal(str)  # error message


class FFmpegDetectionWorker(QRunnable):
    """
    Worker for asynchronous FFmpeg/FFprobe detection.

    Properties:
    - ✔ Background detection (doesn't block UI)
    - ✔ Respects user-configured ffprobe_path from Preferences
    - ✔ Auto-detects from common locations
    - ✔ Timeout protection (5 second limit)
    - ✔ Cache invalidation when settings change
    - ✔ Signal-based communication
    """

    def __init__(self):
        super().__init__()
        self.signals = FFmpegDetectionSignals()

    def _get_configured_ffprobe_path(self) -> str:
        """Read the user-configured ffprobe_path from settings (empty = not set)."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            return settings.get("ffprobe_path", "") or ""
        except Exception:
            return ""

    def run(self):
        """Execute FFmpeg/FFprobe detection asynchronously."""
        logger.info("[FFmpegDetectionWorker] Starting async FFmpeg detection...")

        try:
            start_time = time.time()

            # Read the current configured path so we can validate the cache
            configured_path = self._get_configured_ffprobe_path()

            # Check cache first — only use it if the configured path hasn't changed
            cached_result = self._get_cached_result(configured_path)
            if cached_result:
                logger.info("[FFmpegDetectionWorker] Using cached FFmpeg detection result")
                ffmpeg_available, ffprobe_available, message = cached_result
                self.signals.detection_complete.emit(ffmpeg_available, ffprobe_available, message)
                return

            # Perform fresh detection using the comprehensive checker
            # which respects custom paths, auto-detects, and falls back to PATH
            ffmpeg_available, ffprobe_available, message = self._detect_ffmpeg_ffprobe()

            # Cache the result along with the current configured path
            self._cache_result(ffmpeg_available, ffprobe_available, message, configured_path)

            duration = time.time() - start_time
            logger.info(f"[FFmpegDetectionWorker] Fresh detection completed in {duration:.2f}s")

            # Emit results via signal
            self.signals.detection_complete.emit(ffmpeg_available, ffprobe_available, message)

        except Exception as e:
            logger.error(f"[FFmpegDetectionWorker] Detection failed: {e}")
            self.signals.error.emit(str(e))

    def _detect_ffmpeg_ffprobe(self) -> Tuple[bool, bool, str]:
        """
        Detect FFmpeg and FFprobe availability.

        Delegates to utils.ffmpeg_check.check_ffmpeg_availability() which
        checks custom paths from settings, auto-detects from common locations,
        and falls back to system PATH.

        Returns:
            Tuple of (ffmpeg_available, ffprobe_available, status_message)
        """
        try:
            from utils.ffmpeg_check import check_ffmpeg_availability
            return check_ffmpeg_availability()
        except ImportError:
            logger.warning("[FFmpegDetectionWorker] utils.ffmpeg_check not available, falling back to PATH check")
            return self._detect_ffmpeg_ffprobe_fallback()

    def _detect_ffmpeg_ffprobe_fallback(self) -> Tuple[bool, bool, str]:
        """Fallback PATH-only detection if utils.ffmpeg_check is unavailable."""
        import subprocess

        def check_cmd(cmd: str) -> bool:
            try:
                result = subprocess.run(
                    [cmd, '-version'], capture_output=True, text=True, timeout=5,
                )
                return result.returncode == 0
            except Exception:
                return False

        ffprobe_available = check_cmd('ffprobe')
        ffmpeg_available = check_cmd('ffmpeg')

        if ffprobe_available and ffmpeg_available:
            message = "✅ FFmpeg and FFprobe detected (system PATH) - full video support enabled"
        elif not ffprobe_available and not ffmpeg_available:
            message = "⚠️ Neither FFmpeg nor FFprobe found - video features limited"
        else:
            parts = []
            parts.append("✅ FFprobe detected" if ffprobe_available else "⚠️ FFprobe not found")
            parts.append("✅ FFmpeg detected" if ffmpeg_available else "⚠️ FFmpeg not found")
            message = "\n".join(parts)

        return ffmpeg_available, ffprobe_available, message

    def _get_cached_result(self, configured_path: str) -> Optional[Tuple[bool, bool, str]]:
        """
        Get cached FFmpeg detection result if still valid.

        The cache is invalidated if:
        - The cache file doesn't exist
        - The cache is older than CACHE_EXPIRY_SECONDS
        - The configured ffprobe_path has changed since the cache was written

        Args:
            configured_path: Current ffprobe_path from settings

        Returns:
            Cached result tuple or None if cache invalid/expired
        """
        try:
            if not device_cache_file.exists():
                return None

            with open(device_cache_file, 'r') as f:
                cache_data = json.load(f)

            # Check if cache is expired
            current_time = time.time()
            if current_time - cache_data.get('timestamp', 0) > CACHE_EXPIRY_SECONDS:
                logger.debug("[FFmpegDetectionWorker] Cache expired, performing fresh detection")
                return None

            # Check if the configured ffprobe_path has changed since cache was written
            cached_path = cache_data.get('configured_ffprobe_path', '')
            if cached_path != configured_path:
                logger.info(
                    f"[FFmpegDetectionWorker] FFprobe path changed "
                    f"(cached='{cached_path}' → current='{configured_path}'), "
                    f"invalidating cache"
                )
                return None

            # Return cached result
            ffmpeg_available = cache_data.get('ffmpeg_available', False)
            ffprobe_available = cache_data.get('ffprobe_available', False)
            message = cache_data.get('message', '')

            logger.debug(f"[FFmpegDetectionWorker] Loaded cached result: ffmpeg={ffmpeg_available}, ffprobe={ffprobe_available}")
            return (ffmpeg_available, ffprobe_available, message)

        except Exception as e:
            logger.debug(f"[FFmpegDetectionWorker] Failed to read cache: {e}")
            return None

    def _cache_result(self, ffmpeg_available: bool, ffprobe_available: bool, message: str,
                      configured_path: str = ""):
        """
        Cache FFmpeg detection result.

        Args:
            ffmpeg_available: Whether ffmpeg is available
            ffprobe_available: Whether ffprobe is available
            message: Status message
            configured_path: The ffprobe_path setting at detection time
        """
        try:
            cache_data = {
                'ffmpeg_available': ffmpeg_available,
                'ffprobe_available': ffprobe_available,
                'message': message,
                'configured_ffprobe_path': configured_path,
                'timestamp': time.time()
            }

            with open(device_cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)

            logger.debug(f"[FFmpegDetectionWorker] Cached result: ffmpeg={ffmpeg_available}, ffprobe={ffprobe_available}")

        except Exception as e:
            logger.debug(f"[FFmpegDetectionWorker] Failed to cache result: {e}")