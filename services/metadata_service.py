# services/metadata_service.py
# Version 01.00.00.00 dated 20251102
# Metadata extraction service - EXIF, dimensions, date parsing

import os
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from dataclasses import dataclass

from PIL import Image, ExifTags
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ImageMetadata:
    """
    Structured container for image metadata.

    All fields are optional since metadata extraction can fail or be incomplete.
    """
    # File information
    path: str
    file_size_bytes: Optional[int] = None
    file_size_kb: Optional[float] = None
    modified_time: Optional[str] = None  # ISO format: "2024-11-02 12:34:56"

    # Image dimensions
    width: Optional[int] = None
    height: Optional[int] = None

    # EXIF data
    date_taken: Optional[str] = None  # From EXIF DateTimeOriginal
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    iso: Optional[int] = None
    focal_length: Optional[float] = None
    aperture: Optional[float] = None
    shutter_speed: Optional[str] = None
    orientation: Optional[int] = None

    # Computed fields
    created_timestamp: Optional[int] = None  # Unix timestamp
    created_date: Optional[str] = None  # "YYYY-MM-DD"
    created_year: Optional[int] = None

    # Perceptual hash for pixel-based staleness detection (v9.3.0)
    # Uses dHash (difference hash) which is resilient to metadata-only changes
    image_content_hash: Optional[str] = None

    # Status
    success: bool = False
    error_message: Optional[str] = None


class MetadataService:
    """
    Service for extracting metadata from image files.

    Responsibilities:
    - Extract EXIF data (date, camera info, settings)
    - Get image dimensions
    - Parse and normalize dates from various formats
    - Handle file system metadata (size, modified time)
    - Error handling for corrupted or unsupported files

    Does NOT handle:
    - Face detection (separate service)
    - Thumbnail generation (ThumbnailService)
    - Database operations (use repositories)
    """

    # Supported EXIF date formats
    EXIF_DATE_FORMATS = [
        "%Y:%m:%d %H:%M:%S",     # EXIF standard: "2024:10:15 12:34:56"
        "%Y-%m-%d %H:%M:%S",     # ISO format
        "%Y/%m/%d %H:%M:%S",     # Slash format
        "%d.%m.%Y %H:%M:%S",     # European format
        "%Y-%m-%d",              # Date only
    ]

    def __init__(self,
                 extract_camera_info: bool = False,
                 extract_shooting_params: bool = False):
        """
        Initialize metadata service.

        Args:
            extract_camera_info: Extract camera make/model
            extract_shooting_params: Extract ISO, aperture, etc.
        """
        self.extract_camera_info = extract_camera_info
        self.extract_shooting_params = extract_shooting_params

    def extract_metadata(self, file_path: str) -> ImageMetadata:
        """
        Extract all available metadata from an image file.

        Args:
            file_path: Path to image file

        Returns:
            ImageMetadata with all extracted information
        """
        metadata = ImageMetadata(path=file_path)

        try:
            # Step 1: File system metadata
            self._extract_file_metadata(file_path, metadata)

            # Step 2: Open image and extract dimensions + EXIF
            with Image.open(file_path) as img:
                self._extract_dimensions(img, metadata)
                self._extract_exif(img, metadata)

            # Step 3: Compute derived fields
            self._compute_created_fields(metadata)

            metadata.success = True
            logger.debug(f"Successfully extracted metadata from {file_path}")

        except FileNotFoundError:
            metadata.error_message = "File not found"
            logger.debug(f"File not found: {file_path}")
        except Exception as e:
            metadata.error_message = str(e)
            logger.debug(f"Failed to extract metadata from {file_path}: {e}")

        return metadata

    def extract_basic_metadata(self, file_path: str) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[float], Optional[float], Optional[str]]:
        """
        Fast extraction of dimensions, date, GPS coordinates, and content hash (for scanning).

        This method is optimized for photo scanning performance while extracting
        essential metadata including GPS location data and perceptual hash for
        embedding staleness detection.

        Args:
            file_path: Path to image file

        Returns:
            Tuple of (width, height, date_taken, gps_latitude, gps_longitude, image_content_hash)
            Returns (None, None, None, None, None, None) on failure
        """
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                date_taken = self._get_exif_date(img)

                # Extract GPS coordinates (LONG-TERM FIX: 2026-01-08)
                # Automatically extract GPS during photo scanning for Locations section
                gps_lat, gps_lon = self._get_exif_gps(img)

                # Compute perceptual hash for pixel-based staleness detection (v9.3.0)
                # This is resilient to EXIF metadata changes that don't affect pixels
                content_hash = self._compute_dhash(img)

                return (int(width), int(height), date_taken, gps_lat, gps_lon, content_hash)
        except Exception as e:
            logger.debug(f"Failed basic metadata extraction for {file_path}: {e}")
            return (None, None, None, None, None, None)

    def _extract_file_metadata(self, file_path: str, metadata: ImageMetadata):
        """Extract file system metadata (size, modified time)."""
        try:
            stat_result = os.stat(file_path)

            metadata.file_size_bytes = stat_result.st_size
            metadata.file_size_kb = stat_result.st_size / 1024.0

            # Format modified time as ISO string
            mtime = datetime.fromtimestamp(stat_result.st_mtime)
            metadata.modified_time = mtime.strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            logger.warning(f"Could not extract file metadata for {file_path}: {e}")

    def _extract_dimensions(self, img: Image.Image, metadata: ImageMetadata):
        """Extract image dimensions."""
        try:
            width, height = img.size
            metadata.width = int(width)
            metadata.height = int(height)
        except Exception as e:
            logger.warning(f"Could not extract dimensions: {e}")

    def _extract_exif(self, img: Image.Image, metadata: ImageMetadata):
        """Extract EXIF data from image."""
        try:
            exif = img.getexif()
            if not exif:
                return

            # Map numeric tags to names
            exif_dict = {
                ExifTags.TAGS.get(key, key): value
                for key, value in exif.items()
            }

            # Extract date (highest priority)
            metadata.date_taken = self._extract_exif_date(exif_dict)

            # Extract orientation
            if 'Orientation' in exif_dict:
                metadata.orientation = int(exif_dict['Orientation'])

            # Extract camera info (optional)
            if self.extract_camera_info:
                metadata.camera_make = exif_dict.get('Make')
                metadata.camera_model = exif_dict.get('Model')

            # Extract shooting parameters (optional)
            if self.extract_shooting_params:
                if 'ISOSpeedRatings' in exif_dict:
                    metadata.iso = int(exif_dict['ISOSpeedRatings'])

                if 'FocalLength' in exif_dict:
                    focal = exif_dict['FocalLength']
                    # Handle tuple format (numerator, denominator)
                    if isinstance(focal, tuple) and len(focal) == 2:
                        metadata.focal_length = float(focal[0]) / float(focal[1])
                    else:
                        metadata.focal_length = float(focal)

                if 'FNumber' in exif_dict:
                    fnum = exif_dict['FNumber']
                    if isinstance(fnum, tuple) and len(fnum) == 2:
                        metadata.aperture = float(fnum[0]) / float(fnum[1])
                    else:
                        metadata.aperture = float(fnum)

                if 'ExposureTime' in exif_dict:
                    exp = exif_dict['ExposureTime']
                    if isinstance(exp, tuple) and len(exp) == 2:
                        metadata.shutter_speed = f"{exp[0]}/{exp[1]}"
                    else:
                        metadata.shutter_speed = str(exp)

        except Exception as e:
            logger.debug(f"EXIF extraction failed: {e}")

    def _get_exif_date(self, img: Image.Image) -> Optional[str]:
        """
        Fast EXIF date extraction (used by extract_basic_metadata).

        Returns normalized date string or None.
        Works with all image formats including TIFF, JPEG, PNG, HEIC, etc.
        """
        try:
            # Use getexif() instead of deprecated _getexif()
            # getexif() works with all formats including TIFF
            exif = img.getexif()
            if not exif:
                return None

            # Try to get date from common tags
            for key, value in exif.items():
                tag = ExifTags.TAGS.get(key, key)
                if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    return self._normalize_exif_date(str(value))

            return None

        except AttributeError:
            # Fallback for very old PIL/Pillow versions or exotic formats
            logger.debug(f"Image type {img.format} does not support EXIF extraction")
            return None
        except Exception as e:
            logger.debug(f"EXIF date extraction error: {e}")
            return None

    def _extract_exif_date(self, exif_dict: Dict[str, Any]) -> Optional[str]:
        """
        Extract date from EXIF dictionary with priority order.

        Priority:
        1. DateTimeOriginal (when photo was taken)
        2. DateTimeDigitized (when photo was scanned/digitized)
        3. DateTime (file modification in camera)

        Returns normalized date string or None.
        """
        for tag in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
            if tag in exif_dict:
                date_str = str(exif_dict[tag])
                normalized = self._normalize_exif_date(date_str)
                if normalized:
                    return normalized

        return None

    def _normalize_exif_date(self, date_str: str) -> Optional[str]:
        """
        Normalize EXIF date to standard format.

        EXIF dates use colons: "2024:10:15 12:34:56"
        We normalize to:      "2024-10-15 12:34:56"

        Args:
            date_str: Raw EXIF date string

        Returns:
            Normalized date string or None if invalid
        """
        if not date_str:
            return None

        try:
            # Split date and time
            parts = date_str.split(" ", 1)
            if not parts:
                return None

            # Replace colons in date part with hyphens
            date_part = parts[0].replace(":", "-", 2)
            time_part = parts[1] if len(parts) > 1 else ""

            result = date_part
            if time_part:
                result = f"{date_part} {time_part}"

            # Validate by parsing
            self.parse_date(result)

            return result.strip()

        except Exception:
            return None

    def _get_exif_gps(self, img: Image.Image) -> Tuple[Optional[float], Optional[float]]:
        """
        Fast GPS extraction from EXIF data (used by extract_basic_metadata).

        Extracts latitude and longitude coordinates from photo EXIF GPS tags.
        This enables automatic population of the Locations section during photo scanning.

        Uses Pillow's get_ifd(0x8825) for robust GPS IFD access (Pillow 9.2+).

        Args:
            img: PIL Image object

        Returns:
            Tuple of (latitude, longitude) in decimal degrees
            Returns (None, None) if GPS data not available
        """
        try:
            from PIL.ExifTags import GPSTAGS

            # Get EXIF data
            exif = img.getexif()
            if not exif:
                return (None, None)

            # ── Primary method: use get_ifd(0x8825) for GPS sub-IFD (Pillow 9.2+) ──
            gps_ifd = None
            try:
                gps_ifd = exif.get_ifd(0x8825)  # GPS IFD tag
            except (AttributeError, KeyError):
                # Pillow version doesn't support get_ifd or no GPS IFD
                pass

            # ── Fallback: iterate main IFD for embedded GPS dict (older Pillow) ──
            if not gps_ifd:
                for tag_id, value in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, tag_id)
                    if tag_name == 'GPSInfo' and isinstance(value, dict):
                        gps_ifd = value
                        break

            if not gps_ifd:
                return (None, None)

            # Convert GPS IFD to readable dictionary with string keys
            gps_data = {}
            for tag_id, value in gps_ifd.items():
                tag_name = GPSTAGS.get(tag_id, tag_id)
                gps_data[tag_name] = value

            # Extract and convert coordinates
            if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                lat = self._convert_gps_to_decimal(
                    gps_data['GPSLatitude'],
                    gps_data.get('GPSLatitudeRef', 'N')
                )
                lon = self._convert_gps_to_decimal(
                    gps_data['GPSLongitude'],
                    gps_data.get('GPSLongitudeRef', 'E')
                )

                # Validate coordinates
                if lat is not None and lon is not None:
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        logger.debug(f"GPS extracted: lat={lat}, lon={lon}")
                        return (lat, lon)

            return (None, None)

        except Exception as e:
            logger.debug(f"GPS extraction failed: {e}")
            return (None, None)

    def _convert_gps_to_decimal(self, gps_coord, ref) -> Optional[float]:
        """
        Convert GPS coordinates from degrees/minutes/seconds to decimal degrees.

        Handles multiple Pillow formats:
        - Tuples of floats: (52.0, 30.0, 0.0)
        - Tuples of IFDRational: each with numerator/denominator
        - Mixed formats from different camera manufacturers

        Args:
            gps_coord: GPS coordinate in DMS format (degrees, minutes, seconds)
            ref: Reference direction ('N', 'S', 'E', 'W')

        Returns:
            Decimal degrees or None if conversion fails
        """
        try:
            def _to_float(val) -> float:
                """Convert various Pillow value types to float."""
                # IFDRational has numerator/denominator attributes
                if hasattr(val, 'numerator') and hasattr(val, 'denominator'):
                    if val.denominator == 0:
                        return 0.0
                    return float(val.numerator) / float(val.denominator)
                # Tuple format (numerator, denominator) from older Pillow
                if isinstance(val, tuple) and len(val) == 2:
                    if val[1] == 0:
                        return 0.0
                    return float(val[0]) / float(val[1])
                # Already a number
                return float(val)

            degrees = _to_float(gps_coord[0])
            minutes = _to_float(gps_coord[1])
            seconds = _to_float(gps_coord[2])

            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)

            # Apply hemisphere reference
            if ref in ['S', 'W']:
                decimal = -decimal

            return decimal

        except (IndexError, TypeError, ValueError, ZeroDivisionError) as e:
            logger.debug(f"GPS coordinate conversion failed: {e}")
            return None

    def _compute_dhash(self, img: Image.Image, hash_size: int = 8) -> Optional[str]:
        """
        Compute difference hash (dHash) of image for pixel-based staleness detection.

        dHash is a perceptual hash that is:
        - Fast to compute (simple resize + compare)
        - Resilient to metadata changes (GPS, EXIF edits don't affect it)
        - Resilient to minor compression artifacts
        - Stable across image format conversions (JPEG→PNG, etc.)

        How it works:
        1. Resize image to (hash_size+1) x hash_size grayscale
        2. Compare adjacent horizontal pixels
        3. If left pixel > right pixel, that bit is 1
        4. Produces hash_size² bits (default: 64 bits = 16 hex chars)

        Args:
            img: PIL Image object (already opened)
            hash_size: Size of hash in bits per dimension (default: 8 → 64-bit hash)

        Returns:
            Hex string of dHash (16 characters for 64-bit hash)
            Returns None if hashing fails
        """
        try:
            # Resize to (hash_size + 1) x hash_size for horizontal gradient
            # We need one extra column to compute differences
            resized = img.convert('L').resize(
                (hash_size + 1, hash_size),
                Image.Resampling.LANCZOS
            )

            # Get pixel data as flat list
            pixels = list(resized.getdata())

            # Compute difference hash
            # Compare each pixel to its right neighbor
            diff_bits = []
            for row in range(hash_size):
                for col in range(hash_size):
                    idx = row * (hash_size + 1) + col
                    # 1 if left pixel is brighter than right pixel
                    diff_bits.append(1 if pixels[idx] > pixels[idx + 1] else 0)

            # Convert bits to hex string
            # Group bits into bytes (8 bits each)
            hash_int = 0
            for bit in diff_bits:
                hash_int = (hash_int << 1) | bit

            # Convert to hex string with leading zeros
            hex_length = hash_size * hash_size // 4  # 64 bits = 16 hex chars
            dhash_hex = format(hash_int, f'0{hex_length}x')

            logger.debug(f"Computed dHash: {dhash_hex}")
            return dhash_hex

        except Exception as e:
            logger.debug(f"dHash computation failed: {e}")
            return None

    def compute_dhash_for_file(self, file_path: str) -> Optional[str]:
        """
        Compute dHash for an image file (standalone method for re-hashing).

        Use this to compute hash for existing photos that don't have one.

        Args:
            file_path: Path to image file

        Returns:
            Hex string of dHash or None if failed
        """
        try:
            with Image.open(file_path) as img:
                return self._compute_dhash(img)
        except Exception as e:
            logger.debug(f"Failed to compute dHash for {file_path}: {e}")
            return None

    def _compute_created_fields(self, metadata: ImageMetadata):
        """
        Compute derived fields from date_taken or modified_time.

        Sets:
        - created_timestamp (Unix timestamp)
        - created_date (YYYY-MM-DD)
        - created_year (integer)
        """
        # Try date_taken first, fallback to modified_time
        date_str = metadata.date_taken or metadata.modified_time

        if not date_str:
            return

        try:
            dt = self.parse_date(date_str)
            if dt:
                metadata.created_timestamp = int(dt.timestamp())
                metadata.created_date = dt.strftime("%Y-%m-%d")
                metadata.created_year = dt.year
        except Exception as e:
            logger.debug(f"Failed to compute created fields: {e}")

    def parse_date(self, date_str: str) -> Optional[datetime]:
        """
        Parse date string using multiple format attempts.

        Args:
            date_str: Date string in various possible formats

        Returns:
            datetime object or None if parsing fails
        """
        if not date_str:
            return None

        # Try known formats
        for fmt in self.EXIF_DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # Last resort: try ISO format parsing
        try:
            return datetime.fromisoformat(date_str)
        except Exception:
            return None

    def compute_created_fields_from_dates(self,
                                         date_taken: Optional[str],
                                         modified: Optional[str]) -> Tuple[Optional[int], Optional[str], Optional[int]]:
        """
        Legacy helper for backward compatibility with db_writer.

        Args:
            date_taken: EXIF date string
            modified: File modified time string

        Returns:
            Tuple of (timestamp, date_string, year)
        """
        date_str = date_taken or modified

        if not date_str:
            return (None, None, None)

        try:
            dt = self.parse_date(date_str)
            if dt:
                return (
                    int(dt.timestamp()),
                    dt.strftime("%Y-%m-%d"),
                    dt.year
                )
        except Exception:
            pass

        return (None, None, None)

    @staticmethod
    def is_image_file(file_path: str) -> bool:
        """
        Check if file is a supported image format.

        Args:
            file_path: Path to file

        Returns:
            True if supported image format
        """
        ext = Path(file_path).suffix.lower()
        return ext in {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.heic', '.heif'}


# Example usage:
"""
from services import MetadataService

# Basic usage
service = MetadataService()
metadata = service.extract_metadata("/path/to/photo.jpg")

if metadata.success:
    print(f"Dimensions: {metadata.width}x{metadata.height}")
    print(f"Date taken: {metadata.date_taken}")
    print(f"Camera: {metadata.camera_make} {metadata.camera_model}")

# Fast extraction (for scanning)
width, height, date = service.extract_basic_metadata("/path/to/photo.jpg")

# With extended info
service = MetadataService(
    extract_camera_info=True,
    extract_shooting_params=True
)
metadata = service.extract_metadata("/path/to/photo.jpg")
print(f"ISO: {metadata.iso}, Aperture: f/{metadata.aperture}")
"""
