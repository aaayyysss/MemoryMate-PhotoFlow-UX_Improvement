# services/video_metadata_service.py
# Version 1.0.0 dated 2025-11-09
# Video metadata extraction using ffmpeg/ffprobe

import subprocess
import json
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime
from logging_config import get_logger

logger = get_logger(__name__)


class VideoMetadataService:
    """
    Service for extracting video metadata using ffmpeg/ffprobe.

    Uses ffprobe (part of ffmpeg) as the primary tool for metadata extraction.
    Falls back to basic file info if ffprobe is not available.

    Extracts:
    - Duration (seconds)
    - Resolution (width x height)
    - Frame rate (fps)
    - Codec
    - Bitrate
    - Creation date
    """

    def __init__(self, ffprobe_path: str = None):
        """
        Initialize VideoMetadataService.

        Args:
            ffprobe_path: Optional custom path to ffprobe executable.
                         If None, will check settings and fall back to PATH.
        """
        self.logger = logger
        self._ffprobe_path = self._get_ffprobe_path(ffprobe_path)
        self._ffprobe_available = self._check_ffprobe()

    def _get_ffprobe_path(self, custom_path: str = None) -> str:
        """
        Get ffprobe path from settings or use default.

        Args:
            custom_path: Optional custom path provided at init

        Returns:
            Path to ffprobe executable ('ffprobe' if using system PATH)
        """
        if custom_path:
            return custom_path

        # Try to get from settings
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            saved_path = settings.get_setting('ffprobe_path', '')
            if saved_path and Path(saved_path).exists():
                self.logger.info(f"Using ffprobe from settings: {saved_path}")
                return saved_path
        except Exception as e:
            self.logger.debug(f"Could not load ffprobe path from settings: {e}")

        # Default to PATH
        return 'ffprobe'

    def _check_ffprobe(self) -> bool:
        """
        Check if ffprobe is available.

        Returns:
            True if ffprobe is available, False otherwise
        """
        try:
            result = subprocess.run(
                [self._ffprobe_path, '-version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            available = result.returncode == 0
            if available:
                self.logger.info(f"ffprobe detected at '{self._ffprobe_path}' - video metadata extraction enabled")
            else:
                self.logger.warning(f"ffprobe at '{self._ffprobe_path}' not available - video metadata extraction limited")
            return available
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.logger.warning(f"ffprobe not found at '{self._ffprobe_path}' - video metadata extraction limited")
            return False

    def extract_metadata(self, video_path: str) -> Dict[str, Any]:
        """
        Extract comprehensive metadata from a video file.

        Args:
            video_path: Path to video file

        Returns:
            Dict with metadata fields:
            - duration_seconds: Video duration in seconds (float)
            - width: Video width in pixels (int)
            - height: Video height in pixels (int)
            - fps: Frame rate (float)
            - codec: Video codec name (str)
            - bitrate: Bitrate in kbps (int)
            - date_taken: Creation date (str, YYYY-MM-DD HH:MM:SS format)
            - size_kb: File size in KB (float)
            - modified: Last modified timestamp (str)

        Example:
            >>> service.extract_metadata('/videos/clip.mp4')
            {
                'duration_seconds': 45.2,
                'width': 1920,
                'height': 1080,
                'fps': 30.0,
                'codec': 'h264',
                'bitrate': 5000,
                ...
            }
        """
        metadata = {}

        # Get basic file info (always available)
        try:
            file_path = Path(video_path)
            stat = file_path.stat()
            metadata['size_kb'] = stat.st_size / 1024
            metadata['modified'] = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            self.logger.warning(f"Failed to get file info for {video_path}: {e}")

        # Extract video metadata using ffprobe
        if self._ffprobe_available:
            ffprobe_data = self._extract_with_ffprobe(video_path)
            metadata.update(ffprobe_data)
        else:
            self.logger.warning(f"Skipping metadata extraction for {video_path} (ffprobe not available)")

        return metadata

    def _extract_with_ffprobe(self, video_path: str) -> Dict[str, Any]:
        """
        Extract metadata using ffprobe.

        Args:
            video_path: Path to video file

        Returns:
            Dict with video metadata fields
        """
        try:
            # Run ffprobe with JSON output
            cmd = [
                self._ffprobe_path,
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                video_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                self.logger.error(f"ffprobe failed for {video_path}: {result.stderr}")
                return {}

            # Parse JSON output
            data = json.loads(result.stdout)

            # Extract metadata
            metadata = {}

            # Get format info
            if 'format' in data:
                fmt = data['format']

                # Duration
                if 'duration' in fmt:
                    try:
                        metadata['duration_seconds'] = float(fmt['duration'])
                    except (ValueError, TypeError):
                        pass

                # Bitrate
                if 'bit_rate' in fmt:
                    try:
                        metadata['bitrate'] = int(float(fmt['bit_rate']) / 1000)  # Convert to kbps
                    except (ValueError, TypeError):
                        pass

                # Creation time - try multiple sources with fallbacks
                date_str = None

                # Strategy 1: Format-level creation_time tag
                if 'tags' in fmt and 'creation_time' in fmt['tags']:
                    date_str = fmt['tags']['creation_time']

                # Strategy 2: Format-level date tag (some encoders use this)
                elif 'tags' in fmt and 'date' in fmt['tags']:
                    date_str = fmt['tags']['date']

                # Strategy 3: Format-level DATE tag (uppercase variant)
                elif 'tags' in fmt and 'DATE' in fmt['tags']:
                    date_str = fmt['tags']['DATE']

                if date_str:
                    try:
                        # Parse ISO format: 2022-08-18T14:30:45.000000Z
                        # Remove microseconds and Z/timezone info
                        date_str = date_str.split('.')[0].replace('Z', '').replace('T', ' ').strip()

                        # Try parsing as ISO datetime
                        if 'T' in fmt['tags'].get('creation_time', '') or '-' in date_str:
                            # ISO format or similar
                            date_str = date_str.replace('T', ' ')
                            dt = datetime.fromisoformat(date_str)
                        else:
                            # Try other common formats
                            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')

                        metadata['date_taken'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception as e:
                        self.logger.debug(f"Failed to parse date from format tags: {e}")

            # Get video stream info
            if 'streams' in data:
                # Find first video stream
                video_stream = None
                for stream in data['streams']:
                    if stream.get('codec_type') == 'video':
                        video_stream = stream
                        break

                if video_stream:
                    # Width and height
                    if 'width' in video_stream:
                        metadata['width'] = int(video_stream['width'])
                    if 'height' in video_stream:
                        metadata['height'] = int(video_stream['height'])

                    # Frame rate
                    if 'r_frame_rate' in video_stream:
                        try:
                            # Parse fraction like "30/1" or "30000/1001"
                            num, den = video_stream['r_frame_rate'].split('/')
                            fps = float(num) / float(den)
                            metadata['fps'] = round(fps, 2)
                        except (ValueError, ZeroDivisionError):
                            pass

                    # Codec
                    if 'codec_name' in video_stream:
                        metadata['codec'] = video_stream['codec_name']

                    # Strategy 4: Stream-level creation_time tag (if not found in format)
                    if 'date_taken' not in metadata and 'tags' in video_stream:
                        stream_date = None
                        if 'creation_time' in video_stream['tags']:
                            stream_date = video_stream['tags']['creation_time']
                        elif 'DATE' in video_stream['tags']:
                            stream_date = video_stream['tags']['DATE']
                        elif 'date' in video_stream['tags']:
                            stream_date = video_stream['tags']['date']

                        if stream_date:
                            try:
                                stream_date = stream_date.split('.')[0].replace('Z', '').replace('T', ' ').strip()
                                dt = datetime.fromisoformat(stream_date)
                                metadata['date_taken'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                            except Exception as e:
                                self.logger.debug(f"Failed to parse date from stream tags: {e}")

            # Strategy 5: Ultimate fallback - use file modified time if no date found
            if 'date_taken' not in metadata and 'modified' in metadata:
                try:
                    # modified is already in format 'YYYY-MM-DD HH:MM:SS', use it as date_taken
                    metadata['date_taken'] = metadata['modified']
                    self.logger.debug(f"Using file modified time as date_taken for {video_path}")
                except Exception as e:
                    self.logger.debug(f"Failed to use modified time as fallback: {e}")

            return metadata

        except subprocess.TimeoutExpired:
            self.logger.error(f"ffprobe timeout for {video_path}")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse ffprobe JSON for {video_path}: {e}")
            return {}
        except Exception as e:
            self.logger.error(f"Unexpected error extracting metadata for {video_path}: {e}")
            return {}

    def is_ffprobe_available(self) -> bool:
        """
        Check if ffprobe is available.

        Returns:
            True if ffprobe is available, False otherwise

        Example:
            >>> service.is_ffprobe_available()
            True
        """
        return self._ffprobe_available

    def get_video_duration(self, video_path: str) -> Optional[float]:
        """
        Get video duration in seconds (fast method).

        Args:
            video_path: Path to video file

        Returns:
            Duration in seconds, or None if failed

        Example:
            >>> service.get_video_duration('/videos/clip.mp4')
            45.2
        """
        if not self._ffprobe_available:
            return None

        try:
            cmd = [
                self._ffprobe_path,
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return float(result.stdout.strip())
            return None

        except (ValueError, subprocess.TimeoutExpired, Exception) as e:
            self.logger.warning(f"Failed to get duration for {video_path}: {e}")
            return None

    def get_video_resolution(self, video_path: str) -> Optional[tuple[int, int]]:
        """
        Get video resolution (width, height) (fast method).

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (width, height), or None if failed

        Example:
            >>> service.get_video_resolution('/videos/clip.mp4')
            (1920, 1080)
        """
        if not self._ffprobe_available:
            return None

        try:
            cmd = [
                self._ffprobe_path,
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=s=x:p=0',
                video_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                width, height = result.stdout.strip().split('x')
                return (int(width), int(height))
            return None

        except (ValueError, subprocess.TimeoutExpired, Exception) as e:
            self.logger.warning(f"Failed to get resolution for {video_path}: {e}")
            return None


# ========================================================================
# SINGLETON PATTERN
# ========================================================================

_video_metadata_service_instance = None


def get_video_metadata_service() -> VideoMetadataService:
    """
    Get singleton VideoMetadataService instance.

    Returns:
        VideoMetadataService instance

    Example:
        >>> from services.video_metadata_service import get_video_metadata_service
        >>> service = get_video_metadata_service()
        >>> metadata = service.extract_metadata('/videos/clip.mp4')
    """
    global _video_metadata_service_instance
    if _video_metadata_service_instance is None:
        _video_metadata_service_instance = VideoMetadataService()
    return _video_metadata_service_instance
