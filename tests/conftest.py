# tests/conftest.py
# Pytest fixtures and configuration for integration tests

import os
import tempfile
import shutil
from pathlib import Path
from typing import Generator
import sqlite3

import pytest
from PIL import Image


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create temporary directory for test files."""
    tmpdir = tempfile.mkdtemp(prefix="memorymate_test_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def test_db_path(temp_dir: Path) -> Path:
    """Create temporary database path."""
    return temp_dir / "test_reference_data.db"


@pytest.fixture
def test_images_dir(temp_dir: Path) -> Path:
    """Create directory for test images."""
    img_dir = temp_dir / "images"
    img_dir.mkdir(exist_ok=True)
    return img_dir


@pytest.fixture
def sample_image(test_images_dir: Path) -> Path:
    """
    Create a sample test image with EXIF data.

    Returns path to a 800x600 RGB JPEG image.
    """
    img_path = test_images_dir / "sample_001.jpg"

    # Create RGB image
    img = Image.new("RGB", (800, 600), color=(100, 150, 200))

    # Add EXIF data
    from PIL import ExifTags
    exif_data = img.getexif()

    # Add DateTimeOriginal tag
    datetime_original_tag = None
    for tag_id, tag_name in ExifTags.TAGS.items():
        if tag_name == "DateTimeOriginal":
            datetime_original_tag = tag_id
            break

    if datetime_original_tag:
        exif_data[datetime_original_tag] = "2024:10:15 14:30:45"

    # Save with EXIF
    img.save(img_path, "JPEG", exif=exif_data, quality=85)

    return img_path


@pytest.fixture
def sample_images(test_images_dir: Path) -> list[Path]:
    """
    Create multiple test images with different properties.

    Returns list of paths to test images.
    """
    images = []

    # Image 1: Standard RGB JPEG
    img1 = test_images_dir / "photo_001.jpg"
    Image.new("RGB", (1920, 1080), color=(255, 0, 0)).save(img1, "JPEG")
    images.append(img1)

    # Image 2: Portrait orientation
    img2 = test_images_dir / "photo_002.jpg"
    Image.new("RGB", (1080, 1920), color=(0, 255, 0)).save(img2, "JPEG")
    images.append(img2)

    # Image 3: Square
    img3 = test_images_dir / "photo_003.jpg"
    Image.new("RGB", (1024, 1024), color=(0, 0, 255)).save(img3, "JPEG")
    images.append(img3)

    # Image 4: PNG format
    img4 = test_images_dir / "photo_004.png"
    Image.new("RGB", (800, 600), color=(255, 255, 0)).save(img4, "PNG")
    images.append(img4)

    # Image 5: Small thumbnail size
    img5 = test_images_dir / "photo_005.jpg"
    Image.new("RGB", (320, 240), color=(255, 0, 255)).save(img5, "JPEG")
    images.append(img5)

    return images


@pytest.fixture
def sample_tiff_image(test_images_dir: Path) -> Path:
    """Create a sample TIFF image."""
    tiff_path = test_images_dir / "sample.tif"
    img = Image.new("RGB", (640, 480), color=(128, 128, 128))
    img.save(tiff_path, "TIFF")
    return tiff_path


@pytest.fixture
def nested_folder_structure(test_images_dir: Path) -> dict[str, Path]:
    """
    Create nested folder structure with images.

    Structure:
    images/
      2024/
        01_January/
          photo_001.jpg
          photo_002.jpg
        02_February/
          photo_003.jpg
      2023/
        12_December/
          photo_004.jpg

    Returns dict mapping folder names to paths.
    """
    structure = {}

    # Create folders
    jan_2024 = test_images_dir / "2024" / "01_January"
    jan_2024.mkdir(parents=True, exist_ok=True)
    structure["2024_jan"] = jan_2024

    feb_2024 = test_images_dir / "2024" / "02_February"
    feb_2024.mkdir(parents=True, exist_ok=True)
    structure["2024_feb"] = feb_2024

    dec_2023 = test_images_dir / "2023" / "12_December"
    dec_2023.mkdir(parents=True, exist_ok=True)
    structure["2023_dec"] = dec_2023

    # Create images
    Image.new("RGB", (800, 600), color=(255, 0, 0)).save(jan_2024 / "photo_001.jpg", "JPEG")
    Image.new("RGB", (800, 600), color=(0, 255, 0)).save(jan_2024 / "photo_002.jpg", "JPEG")
    Image.new("RGB", (800, 600), color=(0, 0, 255)).save(feb_2024 / "photo_003.jpg", "JPEG")
    Image.new("RGB", (800, 600), color=(255, 255, 0)).save(dec_2023 / "photo_004.jpg", "JPEG")

    structure["root"] = test_images_dir
    return structure


@pytest.fixture
def init_test_database(test_db_path: Path) -> sqlite3.Connection:
    """
    Initialize test database with PRODUCTION schema.

    Uses the repository layer's schema definition to ensure tests
    validate against the exact same schema used in production.

    Returns connection to test database.
    """
    # Import repository components
    import sys
    import os
    # Add parent directory to path to import repository
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from repository.base_repository import DatabaseConnection

    # Create DatabaseConnection which auto-initializes schema
    db_conn = DatabaseConnection(str(test_db_path), auto_init=True)

    # Return a regular connection for test use
    conn = sqlite3.connect(str(test_db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


@pytest.fixture
def mock_photo_metadata() -> list[dict]:
    """Return mock photo metadata for testing."""
    return [
        {
            "path": "/test/photo_001.jpg",
            "folder_id": 1,
            "size_kb": 2048.5,
            "modified": "2024-10-15 10:30:00",
            "width": 1920,
            "height": 1080,
            "date_taken": "2024:10:15 10:30:00",
            "tags": None
        },
        {
            "path": "/test/photo_002.jpg",
            "folder_id": 1,
            "size_kb": 1856.3,
            "modified": "2024-10-16 14:20:00",
            "width": 1080,
            "height": 1920,
            "date_taken": "2024:10:16 14:20:00",
            "tags": None
        },
        {
            "path": "/test/photo_003.png",
            "folder_id": 2,
            "size_kb": 3200.1,
            "modified": "2024-10-17 09:15:00",
            "width": 3840,
            "height": 2160,
            "date_taken": None,
            "tags": "favorite"
        }
    ]
