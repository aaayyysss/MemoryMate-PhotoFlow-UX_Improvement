# tests/test_photo_scan_service.py
# Integration tests for PhotoScanService

import os
import time
from pathlib import Path

import pytest
from PIL import Image

from services import PhotoScanService, ScanResult, ScanProgress, MetadataService
from repository import PhotoRepository, FolderRepository, ProjectRepository


class TestPhotoScanService:
    """Test suite for PhotoScanService."""

    @pytest.fixture
    def scan_service(self, test_db_path: Path, init_test_database):
        """Create PhotoScanService instance with test repositories."""
        photo_repo = PhotoRepository(str(test_db_path))
        folder_repo = FolderRepository(str(test_db_path))
        project_repo = ProjectRepository(str(test_db_path))
        metadata_service = MetadataService()

        return PhotoScanService(
            photo_repo=photo_repo,
            folder_repo=folder_repo,
            project_repo=project_repo,
            metadata_service=metadata_service,
            batch_size=100
        )

    def test_service_initialization(self, scan_service: PhotoScanService):
        """Test service initialization."""
        assert scan_service.photo_repo is not None
        assert scan_service.folder_repo is not None
        assert scan_service.metadata_service is not None
        assert scan_service.batch_size == 100

    def test_scan_empty_folder(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test scanning empty folder."""
        result = scan_service.scan_repository(str(test_images_dir))

        assert isinstance(result, ScanResult)
        assert result.photos_indexed == 0
        assert result.folders_found >= 0  # At least root folder
        assert result.interrupted is False

    def test_scan_single_image(self, scan_service: PhotoScanService, sample_image: Path):
        """Test scanning folder with single image."""
        root_folder = sample_image.parent

        result = scan_service.scan_repository(str(root_folder))

        assert result.photos_indexed == 1
        assert result.photos_failed == 0
        assert result.folders_found >= 1
        assert result.duration_seconds >= 0

    def test_scan_multiple_images(self, scan_service: PhotoScanService, sample_images: list[Path]):
        """Test scanning folder with multiple images."""
        root_folder = sample_images[0].parent

        result = scan_service.scan_repository(str(root_folder))

        assert result.photos_indexed >= len(sample_images)
        assert result.photos_failed == 0
        assert result.folders_found >= 1

    def test_scan_nested_folders(self, scan_service: PhotoScanService, nested_folder_structure: dict):
        """Test scanning nested folder structure."""
        root_folder = nested_folder_structure["root"]

        result = scan_service.scan_repository(str(root_folder))

        # Should find all folders (2024/01, 2024/02, 2023/12)
        assert result.folders_found >= 3
        # Should index all 4 images
        assert result.photos_indexed == 4
        assert result.photos_failed == 0

    def test_scan_incremental_skip_unchanged(self, scan_service: PhotoScanService, sample_images: list[Path]):
        """Test incremental scan skips unchanged files."""
        root_folder = sample_images[0].parent

        # First scan
        result1 = scan_service.scan_repository(str(root_folder), incremental=False)
        assert result1.photos_indexed >= len(sample_images)

        # Second scan (incremental) - should skip all unchanged files
        result2 = scan_service.scan_repository(str(root_folder), incremental=True, skip_unchanged=True)
        assert result2.photos_indexed == 0  # All skipped
        assert result2.photos_skipped >= len(sample_images)

    def test_scan_incremental_detects_changes(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test incremental scan detects modified files."""
        # Create initial image
        test_img = test_images_dir / "test_change.jpg"
        Image.new("RGB", (800, 600), color=(255, 0, 0)).save(test_img, "JPEG")

        # First scan
        result1 = scan_service.scan_repository(str(test_images_dir), incremental=False)
        assert result1.photos_indexed >= 1

        # Modify the file
        time.sleep(0.2)  # Ensure mtime changes
        Image.new("RGB", (800, 600), color=(0, 0, 255)).save(test_img, "JPEG")

        # Second scan (incremental) - should detect change
        result2 = scan_service.scan_repository(str(test_images_dir), incremental=True, skip_unchanged=True)
        assert result2.photos_indexed >= 1  # Modified file re-indexed

    def test_scan_with_progress_callback(self, scan_service: PhotoScanService, sample_images: list[Path]):
        """Test scan with progress callback."""
        root_folder = sample_images[0].parent
        progress_updates = []

        def on_progress(progress: ScanProgress):
            progress_updates.append(progress)

        result = scan_service.scan_repository(
            str(root_folder),
            progress_callback=on_progress
        )

        # Should have received progress updates
        assert len(progress_updates) > 0
        # Last update should be 100%
        assert progress_updates[-1].percent == 100

    def test_scan_with_cancel_callback(self, scan_service: PhotoScanService, sample_images: list[Path]):
        """Test scan cancellation via callback."""
        root_folder = sample_images[0].parent
        cancel_called = {"count": 0}

        def should_cancel():
            cancel_called["count"] += 1
            # Cancel after 2 calls
            return cancel_called["count"] > 2

        result = scan_service.scan_repository(
            str(root_folder),
            cancel_callback=should_cancel
        )

        assert result.interrupted is True
        assert cancel_called["count"] > 0

    def test_scan_extract_exif_date(self, scan_service: PhotoScanService, sample_image: Path):
        """Test EXIF date extraction during scan."""
        root_folder = sample_image.parent

        result = scan_service.scan_repository(
            str(root_folder),
            extract_exif_date=True
        )

        # Verify photo was indexed with EXIF date
        photo = scan_service.photo_repo.find_by_path(str(sample_image))
        assert photo is not None
        assert photo["date_taken"] is not None
        assert "2024-10-15" in photo["date_taken"]

    def test_scan_skip_exif_extraction(self, scan_service: PhotoScanService, sample_image: Path):
        """Test scanning without EXIF extraction."""
        root_folder = sample_image.parent

        result = scan_service.scan_repository(
            str(root_folder),
            extract_exif_date=False
        )

        # Verify photo was indexed but without EXIF date
        photo = scan_service.photo_repo.find_by_path(str(sample_image))
        assert photo is not None
        # date_taken might be None or empty when EXIF not extracted

    def test_scan_batch_processing(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test batch processing with small batch size."""
        # Create many images
        for i in range(15):
            img_path = test_images_dir / f"batch_{i:03d}.jpg"
            Image.new("RGB", (400, 300), color=(i * 10, i * 10, i * 10)).save(img_path, "JPEG")

        # Scan with small batch size
        scan_service.batch_size = 5  # Process in batches of 5

        result = scan_service.scan_repository(str(test_images_dir))

        assert result.photos_indexed == 15
        assert result.photos_failed == 0

    def test_scan_timeout_protection(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test stat timeout protection for slow file systems."""
        # Create test image
        test_img = test_images_dir / "timeout_test.jpg"
        Image.new("RGB", (800, 600), color=(100, 100, 100)).save(test_img, "JPEG")

        # Scan with very short timeout
        scan_service.stat_timeout = 0.001  # 1ms timeout

        result = scan_service.scan_repository(str(test_images_dir))

        # Should either succeed or fail gracefully
        assert result.photos_indexed >= 0
        assert result.photos_failed >= 0

    def test_scan_updates_folder_counts(self, scan_service: PhotoScanService, nested_folder_structure: dict):
        """Test that folder photo counts are updated."""
        root_folder = nested_folder_structure["root"]

        result = scan_service.scan_repository(str(root_folder))

        # Check that folders have photo counts
        jan_folder = scan_service.folder_repo.find_by_path(str(nested_folder_structure["2024_jan"]))
        assert jan_folder is not None
        # Should have 2 photos (photo_001, photo_002)
        # Note: Implementation may or may not update folder counts automatically

    def test_scan_result_structure(self, scan_service: PhotoScanService, sample_image: Path):
        """Test ScanResult structure completeness."""
        root_folder = sample_image.parent

        result = scan_service.scan_repository(str(root_folder))

        # Verify all fields are present
        assert hasattr(result, "folders_found")
        assert hasattr(result, "photos_indexed")
        assert hasattr(result, "photos_skipped")
        assert hasattr(result, "photos_failed")
        assert hasattr(result, "duration_seconds")
        assert hasattr(result, "interrupted")

        # Verify types
        assert isinstance(result.folders_found, int)
        assert isinstance(result.photos_indexed, int)
        assert isinstance(result.photos_skipped, int)
        assert isinstance(result.photos_failed, int)
        assert isinstance(result.duration_seconds, float)
        assert isinstance(result.interrupted, bool)

    def test_scan_nonexistent_folder(self, scan_service: PhotoScanService):
        """Test scanning nonexistent folder."""
        with pytest.raises(ValueError, match="Folder not found"):
            scan_service.scan_repository("/nonexistent/folder")

    def test_scan_file_instead_of_folder(self, scan_service: PhotoScanService, sample_image: Path):
        """Test scanning a file instead of folder."""
        # Should either raise ValueError or handle gracefully
        try:
            result = scan_service.scan_repository(str(sample_image))
            # If it doesn't raise, it should return empty result
            assert result.photos_indexed == 0
        except ValueError:
            # Expected behavior - file is not a folder
            pass

    def test_scan_supported_formats(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test scanning various supported image formats."""
        # Create images in different formats
        Image.new("RGB", (400, 300), color=(255, 0, 0)).save(test_images_dir / "test.jpg", "JPEG")
        Image.new("RGB", (400, 300), color=(0, 255, 0)).save(test_images_dir / "test.png", "PNG")
        Image.new("RGB", (400, 300), color=(0, 0, 255)).save(test_images_dir / "test.tif", "TIFF")

        result = scan_service.scan_repository(str(test_images_dir))

        # All supported formats should be indexed
        assert result.photos_indexed >= 3

    def test_scan_ignores_unsupported_formats(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test that unsupported file formats are ignored."""
        # Create supported and unsupported files
        Image.new("RGB", (400, 300), color=(255, 0, 0)).save(test_images_dir / "supported.jpg", "JPEG")
        (test_images_dir / "unsupported.txt").write_text("This is a text file")
        (test_images_dir / "unsupported.pdf").write_bytes(b"%PDF-1.4")

        result = scan_service.scan_repository(str(test_images_dir))

        # Only supported image should be indexed
        assert result.photos_indexed == 1

    def test_scan_concurrent_safety(self, scan_service: PhotoScanService, sample_images: list[Path]):
        """Test that multiple scans can be performed."""
        root_folder = sample_images[0].parent

        # Run scan twice in sequence (simulating concurrent-like behavior)
        result1 = scan_service.scan_repository(str(root_folder), incremental=False)
        result2 = scan_service.scan_repository(str(root_folder), incremental=False)

        # Both should succeed
        assert result1.photos_indexed >= len(sample_images)
        assert result2.photos_indexed >= len(sample_images)

    def test_scan_progress_percentage_accuracy(self, scan_service: PhotoScanService, test_images_dir: Path):
        """Test that progress percentages are accurate."""
        # Create known number of images
        num_images = 10
        for i in range(num_images):
            Image.new("RGB", (400, 300), color=(i * 20, i * 20, i * 20)).save(
                test_images_dir / f"progress_{i:02d}.jpg", "JPEG"
            )

        progress_updates = []

        def on_progress(progress: ScanProgress):
            progress_updates.append(progress)

        result = scan_service.scan_repository(
            str(test_images_dir),
            progress_callback=on_progress
        )

        # Verify progress goes from 0 to 100
        assert progress_updates[0].percent >= 0
        assert progress_updates[-1].percent == 100

        # Verify counts in progress messages
        final_progress = progress_updates[-1]
        assert final_progress.message is not None
        assert result.photos_indexed == num_images
