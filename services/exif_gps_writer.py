#!/usr/bin/env python3
"""EXIF GPS Writer Service

Writes GPS coordinates back to photo file EXIF metadata.

This ensures that manually edited GPS locations persist with the photo file,
not just in the application database.

Features:
- Convert decimal coordinates to EXIF GPS format (degrees/minutes/seconds)
- Write GPS tags to photo EXIF data
- Preserve existing EXIF data
- Handle edge cases (no EXIF, read-only files, unsupported formats)

Usage:
    from services.exif_gps_writer import write_gps_to_exif

    # Write GPS coordinates
    success = write_gps_to_exif("/path/to/photo.jpg", 37.7749, -122.4194)

    # Clear GPS coordinates
    success = write_gps_to_exif("/path/to/photo.jpg", None, None)
"""

import os
from pathlib import Path
from typing import Optional, Tuple
from logging_config import get_logger

logger = get_logger(__name__)

# Try to import piexif
try:
    import piexif
    from PIL import Image
    PIEXIF_AVAILABLE = True
except ImportError:
    PIEXIF_AVAILABLE = False
    logger.warning("[GPS Writer] piexif not installed - GPS writing disabled. Install with: pip install piexif")


def decimal_to_dms(decimal: float, is_longitude: bool = False) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int], str]:
    """
    Convert decimal GPS coordinates to degrees/minutes/seconds format for EXIF.

    EXIF GPS format:
    - Latitude: Degrees, Minutes, Seconds + Reference ('N' or 'S')
    - Longitude: Degrees, Minutes, Seconds + Reference ('E' or 'W')

    Args:
        decimal: Decimal coordinate (e.g., 37.7749 or -122.4194)
        is_longitude: True if this is longitude, False if latitude

    Returns:
        Tuple of (degrees, minutes, seconds, reference)
        Each value is a tuple of (numerator, denominator) for rational representation

    Example:
        37.7749° → (37, 1), (46, 1), (2964, 100), 'N'
        -122.4194° → (122, 1), (25, 1), (984, 100), 'W'
    """
    # Determine reference (N/S for latitude, E/W for longitude)
    if is_longitude:
        ref = 'E' if decimal >= 0 else 'W'
    else:
        ref = 'N' if decimal >= 0 else 'S'

    # Work with absolute value
    decimal = abs(decimal)

    # Extract degrees
    degrees = int(decimal)
    decimal_minutes = (decimal - degrees) * 60

    # Extract minutes
    minutes = int(decimal_minutes)
    decimal_seconds = (decimal_minutes - minutes) * 60

    # Extract seconds (multiply by 100 to preserve 2 decimal places)
    seconds = int(decimal_seconds * 100)

    # Convert to EXIF rational format: (numerator, denominator)
    degrees_rational = (degrees, 1)
    minutes_rational = (minutes, 1)
    seconds_rational = (seconds, 100)  # Divide by 100 to get actual seconds

    return degrees_rational, minutes_rational, seconds_rational, ref


def write_gps_to_exif(photo_path: str, latitude: Optional[float], longitude: Optional[float]) -> bool:
    """
    Write GPS coordinates to photo file EXIF metadata.

    This ensures GPS data persists with the photo file, not just in the database.
    When the database is cleared and photos are rescanned, GPS data will be re-extracted
    from the file's EXIF metadata.

    Args:
        photo_path: Path to photo file
        latitude: GPS latitude (-90 to 90) or None to clear
        longitude: GPS longitude (-180 to 180) or None to clear

    Returns:
        True if successful, False otherwise

    EXIF GPS Tag Structure:
        piexif.GPSIFD.GPSLatitude: [(degrees, 1), (minutes, 1), (seconds, 100)]
        piexif.GPSIFD.GPSLatitudeRef: b'N' or b'S'
        piexif.GPSIFD.GPSLongitude: [(degrees, 1), (minutes, 1), (seconds, 100)]
        piexif.GPSIFD.GPSLongitudeRef: b'E' or b'W'
    """
    if not PIEXIF_AVAILABLE:
        logger.error(f"[GPS Writer] Cannot write GPS data - piexif not installed")
        return False

    try:
        photo_path = str(Path(photo_path).resolve())

        # Check if file exists and is writable
        if not os.path.exists(photo_path):
            logger.error(f"[GPS Writer] File not found: {photo_path}")
            return False

        if not os.access(photo_path, os.W_OK):
            logger.error(f"[GPS Writer] File is read-only: {photo_path}")
            return False

        # Check if file format supports EXIF
        file_ext = Path(photo_path).suffix.lower()
        # CRITICAL FIX: JFIF is JPEG, JPE is JPEG variant - all support EXIF
        supported_formats = ['.jpg', '.jpeg', '.jfif', '.jpe', '.tiff', '.tif']
        if file_ext not in supported_formats:
            logger.warning(f"[GPS Writer] File format {file_ext} may not support EXIF - skipping GPS write")
            return False

        # Load existing EXIF data
        try:
            exif_dict = piexif.load(photo_path)
        except Exception as e:
            # Photo has no EXIF data - create new EXIF dict
            logger.info(f"[GPS Writer] No existing EXIF found, creating new EXIF data: {e}")
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

        # Ensure GPS IFD exists
        if "GPS" not in exif_dict or exif_dict["GPS"] is None:
            exif_dict["GPS"] = {}

        # Handle GPS clearing (remove GPS tags)
        if latitude is None or longitude is None:
            logger.info(f"[GPS Writer] Clearing GPS data from {Path(photo_path).name}")

            # Remove GPS tags
            gps_tags_to_remove = [
                piexif.GPSIFD.GPSLatitude,
                piexif.GPSIFD.GPSLatitudeRef,
                piexif.GPSIFD.GPSLongitude,
                piexif.GPSIFD.GPSLongitudeRef
            ]

            for tag in gps_tags_to_remove:
                if tag in exif_dict["GPS"]:
                    del exif_dict["GPS"][tag]

        else:
            # Validate coordinates
            if not (-90 <= latitude <= 90):
                logger.error(f"[GPS Writer] Invalid latitude: {latitude}")
                return False

            if not (-180 <= longitude <= 180):
                logger.error(f"[GPS Writer] Invalid longitude: {longitude}")
                return False

            # Convert decimal to DMS format
            lat_deg, lat_min, lat_sec, lat_ref = decimal_to_dms(latitude, is_longitude=False)
            lon_deg, lon_min, lon_sec, lon_ref = decimal_to_dms(longitude, is_longitude=True)

            logger.info(
                f"[GPS Writer] Writing GPS to {Path(photo_path).name}: "
                f"({latitude:.6f}, {longitude:.6f}) → "
                f"{lat_deg[0]}°{lat_min[0]}'{lat_sec[0]/100:.2f}\"{lat_ref} "
                f"{lon_deg[0]}°{lon_min[0]}'{lon_sec[0]/100:.2f}\"{lon_ref}"
            )

            # Set GPS tags
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = [lat_deg, lat_min, lat_sec]
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = lat_ref.encode('ascii')
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = [lon_deg, lon_min, lon_sec]
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = lon_ref.encode('ascii')

        # Dump EXIF data to bytes
        try:
            exif_bytes = piexif.dump(exif_dict)
        except Exception as e:
            logger.error(f"[GPS Writer] Failed to dump EXIF data: {e}")
            return False

        # Write EXIF data back to file
        try:
            piexif.insert(exif_bytes, photo_path)
            logger.info(f"[GPS Writer] ✓ GPS data written successfully to {Path(photo_path).name}")
            return True

        except Exception as e:
            logger.error(f"[GPS Writer] Failed to write EXIF to file: {e}")
            return False

    except Exception as e:
        logger.error(f"[GPS Writer] Unexpected error writing GPS data: {e}", exc_info=True)
        return False


def read_gps_from_exif(photo_path: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Read GPS coordinates from photo file EXIF metadata.

    This is a helper function for testing GPS writing.

    Args:
        photo_path: Path to photo file

    Returns:
        Tuple of (latitude, longitude) or (None, None)
    """
    if not PIEXIF_AVAILABLE:
        return (None, None)

    try:
        photo_path = str(Path(photo_path).resolve())

        if not os.path.exists(photo_path):
            return (None, None)

        exif_dict = piexif.load(photo_path)

        if "GPS" not in exif_dict:
            return (None, None)

        gps = exif_dict["GPS"]

        # Check for GPS tags
        if (piexif.GPSIFD.GPSLatitude not in gps or
            piexif.GPSIFD.GPSLatitudeRef not in gps or
            piexif.GPSIFD.GPSLongitude not in gps or
            piexif.GPSIFD.GPSLongitudeRef not in gps):
            return (None, None)

        # Extract GPS data
        lat_dms = gps[piexif.GPSIFD.GPSLatitude]
        lat_ref = gps[piexif.GPSIFD.GPSLatitudeRef].decode('ascii')
        lon_dms = gps[piexif.GPSIFD.GPSLongitude]
        lon_ref = gps[piexif.GPSIFD.GPSLongitudeRef].decode('ascii')

        # Convert DMS to decimal
        def dms_to_decimal(dms, ref):
            degrees = dms[0][0] / dms[0][1]
            minutes = dms[1][0] / dms[1][1]
            seconds = dms[2][0] / dms[2][1]

            decimal = degrees + (minutes / 60) + (seconds / 3600)

            if ref in ['S', 'W']:
                decimal = -decimal

            return decimal

        latitude = dms_to_decimal(lat_dms, lat_ref)
        longitude = dms_to_decimal(lon_dms, lon_ref)

        return (latitude, longitude)

    except Exception as e:
        logger.error(f"[GPS Writer] Failed to read GPS from EXIF: {e}")
        return (None, None)


# Test code
if __name__ == '__main__':
    import sys

    if not PIEXIF_AVAILABLE:
        print("ERROR: piexif not installed. Install with: pip install piexif")
        sys.exit(1)

    # Test coordinate conversion
    print("Testing GPS coordinate conversion:")
    print("=" * 60)

    test_coords = [
        (37.7749, -122.4194, "San Francisco"),
        (51.5074, -0.1278, "London"),
        (35.6762, 139.6503, "Tokyo"),
        (-33.8688, 151.2093, "Sydney"),
    ]

    for lat, lon, city in test_coords:
        lat_dms = decimal_to_dms(lat, is_longitude=False)
        lon_dms = decimal_to_dms(lon, is_longitude=True)

        print(f"\n{city}:")
        print(f"  Decimal: {lat:.6f}, {lon:.6f}")
        print(f"  DMS:     {lat_dms[0][0]}°{lat_dms[1][0]}'{lat_dms[2][0]/100:.2f}\"{lat_dms[3]} "
              f"{lon_dms[0][0]}°{lon_dms[1][0]}'{lon_dms[2][0]/100:.2f}\"{lon_dms[3]}")

    print("\n" + "=" * 60)
    print("GPS Writer Service initialized successfully!")
