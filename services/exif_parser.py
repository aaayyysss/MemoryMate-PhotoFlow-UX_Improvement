"""
EXIF Parser - Extract metadata from photos and videos

Parses EXIF data to get capture dates, camera info, GPS, etc.
Used for auto-organizing imported files by date.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
import os
import logging

logger = logging.getLogger(__name__)


class EXIFParser:
    """Parse EXIF metadata from photos and videos"""

    def __init__(self):
        """Initialize EXIF parser with HEIC support"""
        self._heic_support_enabled = False
        self._ffprobe_path = self._resolve_ffprobe()

        # Try to enable HEIC support
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            self._heic_support_enabled = True
            logger.info("HEIC/HEIF support enabled (pillow-heif)")
        except ImportError:
            logger.warning("pillow-heif not installed - HEIC files will use file dates")
        except Exception as e:
            logger.error("Could not enable HEIC support: %s", e)

    @staticmethod
    def _resolve_ffprobe() -> str:
        """Resolve ffprobe path from settings, falling back to PATH."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            saved = settings.get_setting('ffprobe_path', '')
            if saved and Path(saved).exists():
                return saved
        except Exception:
            pass
        return 'ffprobe'

    def get_capture_date(self, file_path: str) -> datetime:
        """
        Get the best available capture date for a file.

        Priority:
        1. EXIF DateTimeOriginal (when photo was taken)
        2. EXIF DateTimeDigitized (when photo was scanned/imported)
        3. EXIF DateTime (file modification in camera)
        4. File modified time
        5. File created time

        Args:
            file_path: Path to image or video file

        Returns:
            datetime object (never None, always returns something)
        """
        file_path_obj = Path(file_path)

        # Try EXIF for images
        if self._is_image(file_path):
            exif_date = self._get_exif_date(file_path)
            if exif_date:
                return exif_date

        # Try video metadata
        elif self._is_video(file_path):
            video_date = self._get_video_date(file_path)
            if video_date:
                return video_date

        # Fallback to file system dates
        return self._get_file_date(file_path_obj)

    def _is_image(self, file_path: str) -> bool:
        """Check if file is an image"""
        ext = Path(file_path).suffix.lower()
        return ext in ['.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif', '.bmp', '.tiff', '.webp']

    def _is_video(self, file_path: str) -> bool:
        """Check if file is a video"""
        ext = Path(file_path).suffix.lower()
        return ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp', '.flv']

    def _get_exif_date(self, file_path: str) -> Optional[datetime]:
        """
        Extract EXIF date from image file with detailed logging.

        Returns:
            datetime object if EXIF date found, None otherwise
        """
        file_name = Path(file_path).name

        try:
            from PIL import Image
            from PIL.ExifTags import TAGS

            logger.debug("Parsing EXIF from: %s", file_name)

            # Try to open image
            # BUG-C2 FIX: Use context manager to prevent resource leak
            try:
                with Image.open(file_path) as img:
                    logger.debug("  ✓ Opened: %s %dx%d", img.format, img.size[0], img.size[1])

                    # Get EXIF data
                    # Get EXIF data (use modern getexif() instead of deprecated _getexif())
                    exif_data = img.getexif()

                    if not exif_data:
                        logger.debug("  No EXIF data in file")
                        return None

                    # Look for date tags in priority order
                    date_tags = [
                        36867,  # DateTimeOriginal (when photo was taken)
                        36868,  # DateTimeDigitized (when photo was scanned)
                        306,    # DateTime (file modification in camera)
                    ]

                    for tag_id in date_tags:
                        if tag_id in exif_data:
                            date_str = exif_data[tag_id]

                            # Parse EXIF date format: "YYYY:MM:DD HH:MM:SS"
                            try:
                                dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                                tag_name = TAGS.get(tag_id, tag_id)
                                logger.debug("  ✓ Found %s: %s", tag_name, dt.strftime('%Y-%m-%d %H:%M:%S'))
                                return dt
                            except ValueError:
                                continue

                    logger.debug("  No valid date tags found in EXIF")
                    return None
                # BUG-C2 FIX: img automatically closed by context manager

            except Exception as e:
                logger.warning("  ✗ Error getting EXIF from %s: %s", file_name, e)
                return None

        except ImportError:
            logger.error("  ✗ PIL not available, cannot parse EXIF")
            return None
        except Exception as e:
            logger.error("  ✗ Error parsing EXIF: %s", e)
            return None

    def _get_video_date(self, file_path: str) -> Optional[datetime]:
        """
        Extract creation date from video file metadata.

        Returns:
            datetime object if video date found, None otherwise
        """
        try:
            # Try using ffprobe (part of FFmpeg) to get video metadata
            import subprocess
            import json

            logger.debug("Parsing video metadata from: %s", Path(file_path).name)

            # Run ffprobe to get video metadata
            cmd = [
                self._ffprobe_path,
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                file_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                metadata = json.loads(result.stdout)

                # Try to get creation_time from format tags
                if 'format' in metadata and 'tags' in metadata['format']:
                    tags = metadata['format']['tags']

                    # Different video formats use different tag names
                    for tag_name in ['creation_time', 'date', 'DATE', 'creation_date']:
                        if tag_name in tags:
                            date_str = tags[tag_name]

                            # Parse ISO format: "2024-10-15T14:30:00.000000Z"
                            try:
                                # Remove microseconds and timezone
                                date_str = date_str.split('.')[0].replace('Z', '')
                                dt = datetime.fromisoformat(date_str)
                                logger.debug("  ✓ Found %s: %s", tag_name, dt.strftime('%Y-%m-%d %H:%M:%S'))
                                return dt
                            except ValueError:
                                continue

                logger.debug("  No creation_time found in video metadata")
                return None

            else:
                logger.warning("  ✗ ffprobe failed (not installed or error) for %s", file_path)
                return None

        except FileNotFoundError:
            # ffprobe not installed, fall back to file dates
            logger.warning("  ✗ ffprobe not found (FFmpeg not installed)")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("  ✗ ffprobe timeout")
            return None
        except Exception as e:
            logger.error("  ✗ Error parsing video metadata: %s", e)
            return None

    def _get_file_date(self, file_path: Path) -> datetime:
        """
        Get file system date (modified or created time).

        Returns:
            datetime object (never None)
        """
        try:
            # Try modified time first (more accurate for photos copied from camera)
            mtime = file_path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime)
            logger.debug("  Using file modified time: %s", dt.strftime('%Y-%m-%d %H:%M:%S'))
            return dt

        except Exception as e:
            # Last resort: use current time
            logger.warning("  ✗ Error getting file date for %s, using current time: %s", file_path, e)
            return datetime.now()

    def parse_image_full(self, file_path: str) -> Dict:
        """
        Extract full EXIF metadata from image (for future use).

        Returns dict with:
            - datetime_original: When photo was taken
            - camera_make: Camera manufacturer
            - camera_model: Camera model
            - width: Image width
            - height: Image height
            - orientation: EXIF orientation
            - gps_latitude: GPS latitude (if available)
            - gps_longitude: GPS longitude (if available)
        """
        metadata = {
            'datetime_original': None,
            'camera_make': None,
            'camera_model': None,
            'width': None,
            'height': None,
            'orientation': None,
            'gps_latitude': None,
            'gps_longitude': None,
        }

        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS

            with Image.open(file_path) as img:
                # Get basic image info
                metadata['width'] = img.width
                metadata['height'] = img.height

                # Get EXIF data (use modern getexif() instead of deprecated _getexif())
                exif_data = img.getexif()
                if not exif_data:
                    return metadata

                # Extract common EXIF tags
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)

                    if tag_name == 'DateTimeOriginal':
                        try:
                            metadata['datetime_original'] = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        except (ValueError, TypeError) as e:
                            # BUG-H1 FIX: Log date parsing failures instead of silently ignoring
                            logger.warning("Failed to parse DateTimeOriginal '%s': %s", value, e)
                    elif tag_name == 'Make':
                        metadata['camera_make'] = value
                    elif tag_name == 'Model':
                        metadata['camera_model'] = value
                    elif tag_name == 'Orientation':
                        metadata['orientation'] = value
                    elif tag_name == 'GPSInfo':
                        # Parse GPS data
                        gps_data = {}
                        for gps_tag_id in value:
                            gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                            gps_data[gps_tag_name] = value[gps_tag_id]

                        # Convert GPS to decimal degrees
                        if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                            metadata['gps_latitude'] = self._convert_gps_to_decimal(
                                gps_data['GPSLatitude'],
                                gps_data.get('GPSLatitudeRef', 'N')
                            )
                            metadata['gps_longitude'] = self._convert_gps_to_decimal(
                                gps_data['GPSLongitude'],
                                gps_data.get('GPSLongitudeRef', 'E')
                            )

        except Exception as e:
            print(f"[EXIFParser] Error parsing full EXIF: {e}")

        return metadata

    def _convert_gps_to_decimal(self, gps_coord, ref):
        """Convert GPS coordinates from degrees/minutes/seconds to decimal"""
        try:
            degrees = float(gps_coord[0])
            minutes = float(gps_coord[1])
            seconds = float(gps_coord[2])

            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)

            if ref in ['S', 'W']:
                decimal = -decimal

            return decimal
        except (ValueError, TypeError, IndexError) as e:
            # BUG-H1 FIX: Log GPS conversion failures
            logger.warning("Failed to convert GPS coordinates: %s", e)
            return None

    def parse_all_exif_fields(self, file_path: str) -> Dict:
        """
        Extract ALL available EXIF metadata from image.

        Returns comprehensive dict organized by category:
        - basic: filename, size, dimensions, format
        - datetime: all date/time fields
        - camera: make, model, lens, serial number
        - exposure: ISO, aperture, shutter, exposure compensation
        - image: color space, resolution, compression
        - gps: coordinates, altitude, timestamp, satellite info
        - technical: software, copyright, artist, all other fields
        """
        result = {
            'basic': {},
            'datetime': {},
            'camera': {},
            'exposure': {},
            'image': {},
            'gps': {},
            'technical': {},
            'raw_exif': {}  # All unprocessed EXIF tags
        }

        try:
            import os
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS

            # Basic file info
            result['basic']['filename'] = os.path.basename(file_path)
            result['basic']['file_size'] = os.path.getsize(file_path)
            result['basic']['file_path'] = file_path

            with Image.open(file_path) as img:
                # Image format and dimensions
                result['basic']['format'] = img.format
                result['basic']['width'] = img.width
                result['basic']['height'] = img.height
                result['basic']['mode'] = img.mode

                # Get EXIF data
                exif_data = img.getexif()
                if not exif_data:
                    return result

                # Process all EXIF tags
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, f"Unknown_{tag_id}")
                    result['raw_exif'][tag_name] = str(value) if not isinstance(value, (str, int, float)) else value

                    # Categorize known tags
                    # DateTime fields
                    if tag_name == 'DateTime':
                        result['datetime']['modified'] = value
                    elif tag_name == 'DateTimeOriginal':
                        result['datetime']['taken'] = value
                    elif tag_name == 'DateTimeDigitized':
                        result['datetime']['digitized'] = value

                    # Camera info
                    elif tag_name == 'Make':
                        result['camera']['make'] = value
                    elif tag_name == 'Model':
                        result['camera']['model'] = value
                    elif tag_name == 'LensModel':
                        result['camera']['lens_model'] = value
                    elif tag_name == 'LensMake':
                        result['camera']['lens_make'] = value
                    elif tag_name == 'LensSerialNumber':
                        result['camera']['lens_serial'] = value
                    elif tag_name == 'SerialNumber' or tag_name == 'BodySerialNumber':
                        result['camera']['body_serial'] = value

                    # Exposure settings
                    elif tag_name == 'ISOSpeedRatings' or tag_name == 'ISO':
                        result['exposure']['iso'] = value
                    elif tag_name == 'FNumber':
                        result['exposure']['aperture'] = value
                    elif tag_name == 'ExposureTime':
                        result['exposure']['shutter_speed'] = value
                    elif tag_name == 'FocalLength':
                        result['exposure']['focal_length'] = value
                    elif tag_name == 'FocalLengthIn35mmFilm':
                        result['exposure']['focal_length_35mm'] = value
                    elif tag_name == 'ExposureCompensation' or tag_name == 'ExposureBiasValue':
                        result['exposure']['exposure_compensation'] = value
                    elif tag_name == 'MeteringMode':
                        result['exposure']['metering_mode'] = value
                    elif tag_name == 'Flash':
                        result['exposure']['flash'] = value
                    elif tag_name == 'WhiteBalance':
                        result['exposure']['white_balance'] = value
                    elif tag_name == 'ExposureMode':
                        result['exposure']['exposure_mode'] = value
                    elif tag_name == 'ExposureProgram':
                        result['exposure']['exposure_program'] = value
                    elif tag_name == 'SceneCaptureType':
                        result['exposure']['scene_type'] = value
                    elif tag_name == 'GainControl':
                        result['exposure']['gain_control'] = value
                    elif tag_name == 'Contrast':
                        result['exposure']['contrast'] = value
                    elif tag_name == 'Saturation':
                        result['exposure']['saturation'] = value
                    elif tag_name == 'Sharpness':
                        result['exposure']['sharpness'] = value

                    # Image properties
                    elif tag_name == 'Orientation':
                        result['image']['orientation'] = value
                    elif tag_name == 'ColorSpace':
                        result['image']['color_space'] = value
                    elif tag_name == 'Compression':
                        result['image']['compression'] = value
                    elif tag_name == 'XResolution':
                        result['image']['x_resolution'] = value
                    elif tag_name == 'YResolution':
                        result['image']['y_resolution'] = value
                    elif tag_name == 'ResolutionUnit':
                        result['image']['resolution_unit'] = value
                    elif tag_name == 'YCbCrPositioning':
                        result['image']['ycbcr_positioning'] = value

                    # Technical/Software
                    elif tag_name == 'Software':
                        result['technical']['software'] = value
                    elif tag_name == 'Artist':
                        result['technical']['artist'] = value
                    elif tag_name == 'Copyright':
                        result['technical']['copyright'] = value
                    elif tag_name == 'ImageDescription':
                        result['technical']['description'] = value
                    elif tag_name == 'UserComment':
                        result['technical']['user_comment'] = value

                    # GPS Info
                    elif tag_name == 'GPSInfo':
                        gps_data = {}
                        # ENHANCED DEFENSIVE FIX: Handle various GPSInfo formats robustly
                        # Issue: piexif sometimes writes GPSInfo as integer IDs instead of dictionaries
                        # This causes "GPSInfo value is not iterable" errors during rescanning

                        try:
                            # Case 1: Proper dictionary (normal case)
                            if isinstance(value, dict):
                                for gps_tag_id in value:
                                    gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                                    gps_data[gps_tag_name] = value[gps_tag_id]

                            # Case 2: Iterable object (list, tuple, custom iterable)
                            elif hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
                                # Try to iterate and build GPS data dictionary
                                for gps_tag_id in value:
                                    gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                                    gps_data[gps_tag_name] = value[gps_tag_id]

                            # Case 3: Integer or other non-iterable (problematic case from piexif)
                            else:
                                # This is the source of the warning - GPSInfo written as integer
                                # Use logger at DEBUG level instead of WARNING since this is expected behavior
                                logger.debug("GPSInfo is non-iterable type %s, likely from piexif - skipping GPS parsing", type(value))
                                # Continue without GPS data - this is normal for some photo sources
                                continue

                        except (TypeError, KeyError, AttributeError) as e:
                            # Gracefully handle any iteration errors
                            logger.warning("GPS iteration error (likely from piexif-written GPS): %s", e)
                            continue  # Skip GPS parsing for this photo

                        # Store all GPS fields
                        result['gps']['raw'] = gps_data

                        # Convert coordinates to decimal
                        if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                            result['gps']['latitude'] = self._convert_gps_to_decimal(
                                gps_data['GPSLatitude'],
                                gps_data.get('GPSLatitudeRef', 'N')
                            )
                            result['gps']['longitude'] = self._convert_gps_to_decimal(
                                gps_data['GPSLongitude'],
                                gps_data.get('GPSLongitudeRef', 'E')
                            )

                        # GPS Altitude
                        if 'GPSAltitude' in gps_data:
                            altitude = gps_data['GPSAltitude']
                            if isinstance(altitude, tuple):
                                altitude = altitude[0] / altitude[1] if altitude[1] != 0 else altitude[0]
                            result['gps']['altitude'] = altitude
                            result['gps']['altitude_ref'] = gps_data.get('GPSAltitudeRef', 0)

                        # GPS Timestamp
                        if 'GPSTimeStamp' in gps_data:
                            result['gps']['timestamp'] = gps_data['GPSTimeStamp']
                        if 'GPSDateStamp' in gps_data:
                            result['gps']['datestamp'] = gps_data['GPSDateStamp']

                        # GPS Direction/Speed
                        if 'GPSImgDirection' in gps_data:
                            result['gps']['image_direction'] = gps_data['GPSImgDirection']
                        if 'GPSSpeed' in gps_data:
                            result['gps']['speed'] = gps_data['GPSSpeed']
                        if 'GPSSpeedRef' in gps_data:
                            result['gps']['speed_ref'] = gps_data['GPSSpeedRef']

                        # GPS Satellites
                        if 'GPSSatellites' in gps_data:
                            result['gps']['satellites'] = gps_data['GPSSatellites']

        except Exception as e:
            logger.error("Error extracting all EXIF fields: %s", e, exc_info=True)

        return result

    def extract_video_metadata_full(self, file_path: str) -> Dict:
        """
        Extract comprehensive video metadata using ffprobe.

        Returns dict organized by category:
        - basic: filename, size, format
        - video: codec, resolution, fps, bitrate, duration
        - audio: codec, sample rate, channels, bitrate
        - technical: container, creation time, rotation, all other fields
        """
        result = {
            'basic': {},
            'video': {},
            'audio': {},
            'technical': {},
            'raw_metadata': {}
        }

        try:
            import os
            import json
            import subprocess

            # Basic file info
            result['basic']['filename'] = os.path.basename(file_path)
            result['basic']['file_size'] = os.path.getsize(file_path)
            result['basic']['file_path'] = file_path

            # Use ffprobe to extract metadata
            cmd = [
                self._ffprobe_path,
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                file_path
            ]

            output = subprocess.check_output(cmd, timeout=5.0, stderr=subprocess.DEVNULL)
            data = json.loads(output)

            # Store raw metadata
            result['raw_metadata'] = data

            # Process format info
            if 'format' in data:
                fmt = data['format']
                result['basic']['format'] = fmt.get('format_name', 'Unknown')
                result['basic']['format_long'] = fmt.get('format_long_name', 'Unknown')

                # Duration
                if 'duration' in fmt:
                    result['video']['duration'] = float(fmt['duration'])

                # Bitrate
                if 'bit_rate' in fmt:
                    result['video']['bitrate'] = int(fmt['bit_rate'])

                # Creation time and other tags
                if 'tags' in fmt:
                    tags = fmt['tags']
                    result['technical']['tags'] = tags

                    # Creation time (various formats)
                    for key in ['creation_time', 'date', 'com.apple.quicktime.creationdate']:
                        if key in tags:
                            result['technical']['creation_time'] = tags[key]
                            break

                    # Other useful tags
                    if 'encoder' in tags:
                        result['technical']['encoder'] = tags['encoder']
                    if 'copyright' in tags:
                        result['technical']['copyright'] = tags['copyright']
                    if 'artist' in tags:
                        result['technical']['artist'] = tags['artist']
                    if 'title' in tags:
                        result['technical']['title'] = tags['title']
                    if 'comment' in tags:
                        result['technical']['comment'] = tags['comment']

            # Process streams
            if 'streams' in data:
                for stream in data['streams']:
                    codec_type = stream.get('codec_type')

                    if codec_type == 'video':
                        # Video stream
                        result['video']['codec'] = stream.get('codec_name', 'Unknown')
                        result['video']['codec_long'] = stream.get('codec_long_name', 'Unknown')
                        result['video']['width'] = stream.get('width')
                        result['video']['height'] = stream.get('height')
                        result['video']['profile'] = stream.get('profile')
                        result['video']['level'] = stream.get('level')

                        # FPS (frame rate)
                        if 'r_frame_rate' in stream:
                            fps_str = stream['r_frame_rate']
                            if '/' in fps_str:
                                num, den = fps_str.split('/')
                                result['video']['fps'] = float(num) / float(den) if float(den) != 0 else 0
                            else:
                                result['video']['fps'] = float(fps_str)

                        # Pixel format
                        if 'pix_fmt' in stream:
                            result['video']['pixel_format'] = stream['pix_fmt']

                        # Color space
                        if 'color_space' in stream:
                            result['video']['color_space'] = stream['color_space']
                        if 'color_range' in stream:
                            result['video']['color_range'] = stream['color_range']

                        # Rotation (from side data or tags)
                        if 'tags' in stream:
                            if 'rotate' in stream['tags']:
                                result['video']['rotation'] = stream['tags']['rotate']
                        if 'side_data_list' in stream:
                            for side_data in stream['side_data_list']:
                                if side_data.get('side_data_type') == 'Display Matrix':
                                    if 'rotation' in side_data:
                                        result['video']['rotation'] = side_data['rotation']

                        # Bitrate (stream-specific)
                        if 'bit_rate' in stream:
                            result['video']['video_bitrate'] = int(stream['bit_rate'])

                    elif codec_type == 'audio':
                        # Audio stream
                        result['audio']['codec'] = stream.get('codec_name', 'Unknown')
                        result['audio']['codec_long'] = stream.get('codec_long_name', 'Unknown')
                        result['audio']['sample_rate'] = stream.get('sample_rate')
                        result['audio']['channels'] = stream.get('channels')
                        result['audio']['channel_layout'] = stream.get('channel_layout')

                        # Bitrate
                        if 'bit_rate' in stream:
                            result['audio']['bitrate'] = int(stream['bit_rate'])

        except subprocess.TimeoutExpired:
            logger.warning("ffprobe timeout for video: %s", file_path)
        except subprocess.CalledProcessError as e:
            logger.error("ffprobe error: %s", e)
        except FileNotFoundError:
            logger.warning("ffprobe not found - install ffmpeg to enable video metadata")
        except Exception as e:
            logger.error("Error extracting video metadata: %s", e, exc_info=True)

        return result
