# tests/test_metadata_service.py
# Integration tests for MetadataService

import os
import time
from pathlib import Path

import pytest
from PIL import Image, ExifTags

from services import MetadataService, ImageMetadata


class TestMetadataService:
    """Test suite for MetadataService."""

    @pytest.fixture
    def service(self):
        """Create MetadataService instance."""
        return MetadataService()

    def test_extract_basic_metadata_jpeg(self, service: MetadataService, sample_image: Path):
        """Test basic metadata extraction from JPEG."""
        width, height, date_taken, lat, lon, content_hash = service.extract_basic_metadata(str(sample_image))

        assert width == 800
        assert height == 600
        assert date_taken == "2024-10-15 14:30:45"  # Normalized format

    def test_extract_full_metadata_jpeg(self, service: MetadataService, sample_image: Path):
        """Test full metadata extraction from JPEG."""
        metadata = service.extract_metadata(str(sample_image))

        assert metadata.success is True
        assert metadata.path == str(sample_image)
        assert metadata.width == 800
        assert metadata.height == 600
        assert metadata.date_taken == "2024-10-15 14:30:45"
        assert metadata.file_size_bytes > 0
        assert metadata.created_timestamp > 0
        assert metadata.created_date is not None
        assert metadata.error_message is None

    def test_extract_metadata_no_exif(self, service: MetadataService, test_images_dir: Path):
        """Test metadata extraction from image without EXIF data."""
        # Create image without EXIF
        no_exif_path = test_images_dir / "no_exif.png"
        Image.new("RGB", (640, 480), color=(100, 100, 100)).save(no_exif_path, "PNG")

        metadata = service.extract_metadata(str(no_exif_path))

        assert metadata.success is True
        assert metadata.width == 640
        assert metadata.height == 480
        assert metadata.date_taken is None  # No EXIF data
        assert metadata.created_timestamp > 0  # Falls back to file mtime

    def test_extract_metadata_nonexistent_file(self, service: MetadataService):
        """Test extraction from nonexistent file."""
        metadata = service.extract_metadata("/nonexistent/file.jpg")

        assert metadata.success is False
        assert metadata.error_message is not None
        assert "not found" in metadata.error_message.lower() or "no such file" in metadata.error_message.lower()

    def test_extract_metadata_invalid_image(self, service: MetadataService, temp_dir: Path):
        """Test extraction from invalid image file."""
        invalid_path = temp_dir / "invalid.jpg"
        invalid_path.write_text("This is not an image file")

        metadata = service.extract_metadata(str(invalid_path))

        assert metadata.success is False
        assert metadata.error_message is not None

    def test_normalize_exif_date_standard(self, service: MetadataService):
        """Test EXIF date normalization with standard format."""
        result = service._normalize_exif_date("2024:10:15 14:30:45")
        assert result == "2024-10-15 14:30:45"

    def test_normalize_exif_date_date_only(self, service: MetadataService):
        """Test EXIF date normalization with date only."""
        result = service._normalize_exif_date("2024:10:15")
        assert result == "2024-10-15"

    def test_parse_exif_date_various_formats(self, service: MetadataService):
        """Test parsing EXIF dates in various formats."""
        test_cases = [
            ("2024:10:15 14:30:45", True),  # Standard EXIF
            ("2024-10-15 14:30:45", True),  # ISO format
            ("2024/10/15 14:30:45", True),  # Slash format
            ("15.10.2024 14:30:45", True),  # European format
            ("2024-10-15", True),           # Date only
            ("invalid date", False),         # Invalid
        ]

        for date_str, should_parse in test_cases:
            result = service.parse_date(date_str)
            if should_parse:
                assert result is not None, f"Failed to parse: {date_str}"
            else:
                assert result is None, f"Should not parse: {date_str}"

    def test_compute_created_fields_from_dates(self, service: MetadataService):
        """Test created field computation from date_taken and modified."""
        # Case 1: Has date_taken
        ts, date, year = service.compute_created_fields_from_dates("2024:10:15 14:30:45", "2024-10-16 10:00:00")
        assert ts is not None
        assert date == "2024-10-15"
        assert year == 2024

        # Case 2: No date_taken, use modified
        ts, date, year = service.compute_created_fields_from_dates(None, "2024-10-16 10:00:00")
        assert ts is not None
        assert date == "2024-10-16"
        assert year == 2024

        # Case 3: Neither date_taken nor modified
        ts, date, year = service.compute_created_fields_from_dates(None, None)
        assert ts is None
        assert date is None
        assert year is None

    def test_extract_multiple_images_batch(self, service: MetadataService, sample_images: list[Path]):
        """Test extracting metadata from multiple images."""
        results = []

        for img_path in sample_images:
            metadata = service.extract_metadata(str(img_path))
            results.append(metadata)

        # All should succeed
        assert all(m.success for m in results)

        # All should have dimensions
        assert all(m.width > 0 and m.height > 0 for m in results)

        # Check expected dimensions
        assert results[0].width == 1920 and results[0].height == 1080  # photo_001
        assert results[1].width == 1080 and results[1].height == 1920  # photo_002 (portrait)
        assert results[2].width == 1024 and results[2].height == 1024  # photo_003 (square)

    def test_extract_tiff_metadata(self, service: MetadataService, sample_tiff_image: Path):
        """Test metadata extraction from TIFF image."""
        metadata = service.extract_metadata(str(sample_tiff_image))

        assert metadata.success is True
        assert metadata.width == 640
        assert metadata.height == 480

    def test_metadata_caching_behavior(self, service: MetadataService, sample_image: Path):
        """Test that repeated extractions work correctly."""
        # Extract twice
        metadata1 = service.extract_metadata(str(sample_image))
        metadata2 = service.extract_metadata(str(sample_image))

        # Both should succeed with same values
        assert metadata1.success is True
        assert metadata2.success is True
        assert metadata1.width == metadata2.width
        assert metadata1.height == metadata2.height
        assert metadata1.date_taken == metadata2.date_taken

    def test_file_size_extraction(self, service: MetadataService, sample_image: Path):
        """Test file size is correctly extracted."""
        metadata = service.extract_metadata(str(sample_image))

        expected_size = os.path.getsize(sample_image)
        assert metadata.file_size_bytes == expected_size
        assert metadata.file_size_bytes > 0

    def test_created_timestamp_fallback(self, service: MetadataService, test_images_dir: Path):
        """Test created_timestamp falls back to file mtime when no EXIF date."""
        # Create image without EXIF date
        no_date_path = test_images_dir / "no_date.jpg"
        Image.new("RGB", (400, 300), color=(50, 50, 50)).save(no_date_path, "JPEG")

        # Get file mtime
        expected_mtime = int(os.path.getmtime(no_date_path))

        metadata = service.extract_metadata(str(no_date_path))

        assert metadata.success is True
        assert metadata.date_taken is None  # No EXIF
        assert abs(metadata.created_timestamp - expected_mtime) <= 1  # Allow 1 second tolerance
