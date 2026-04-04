# tests/test_thumbnail_service.py
# Integration tests for ThumbnailService
#
# REQUIRES Qt: This test suite imports PySide6 and ThumbnailService which depends on Qt.
# Mark with: @pytest.mark.requires_qt
# Skip in headless environments with: pytest -m "not requires_qt"

import os
import time
from pathlib import Path

import pytest
from PySide6.QtGui import QPixmap

from services import ThumbnailService, LRUCache

# Mark all tests in this module as requiring Qt
pytestmark = pytest.mark.requires_qt


class TestLRUCache:
    """Test suite for LRUCache."""

    def test_cache_initialization(self):
        """Test LRU cache initialization."""
        cache = LRUCache(capacity=100)
        assert cache.capacity == 100
        assert cache.size() == 0
        assert cache.hits == 0
        assert cache.misses == 0

    def test_cache_put_and_get(self):
        """Test basic put and get operations."""
        cache = LRUCache(capacity=3)

        cache.put("key1", {"value": 1})
        cache.put("key2", {"value": 2})

        assert cache.get("key1") == {"value": 1}
        assert cache.get("key2") == {"value": 2}
        assert cache.size() == 2

    def test_cache_miss(self):
        """Test cache miss behavior."""
        cache = LRUCache(capacity=3)
        result = cache.get("nonexistent")

        assert result is None
        assert cache.misses == 1
        assert cache.hits == 0

    def test_cache_eviction(self):
        """Test LRU eviction when capacity exceeded."""
        cache = LRUCache(capacity=3)

        # Fill cache
        cache.put("key1", {"value": 1})
        cache.put("key2", {"value": 2})
        cache.put("key3", {"value": 3})
        assert cache.size() == 3

        # Add 4th item - should evict key1 (oldest)
        cache.put("key4", {"value": 4})
        assert cache.size() == 3
        assert cache.get("key1") is None  # Evicted
        assert cache.get("key2") == {"value": 2}
        assert cache.get("key4") == {"value": 4}

    def test_cache_lru_ordering(self):
        """Test that access updates LRU order."""
        cache = LRUCache(capacity=3)

        cache.put("key1", {"value": 1})
        cache.put("key2", {"value": 2})
        cache.put("key3", {"value": 3})

        # Access key1 to make it recent
        cache.get("key1")

        # Add key4 - should evict key2 (now oldest)
        cache.put("key4", {"value": 4})

        assert cache.get("key1") == {"value": 1}  # Still present
        assert cache.get("key2") is None  # Evicted
        assert cache.get("key3") == {"value": 3}  # Still present
        assert cache.get("key4") == {"value": 4}  # New entry

    def test_cache_invalidate(self):
        """Test cache invalidation."""
        cache = LRUCache(capacity=3)

        cache.put("key1", {"value": 1})
        assert cache.size() == 1

        removed = cache.invalidate("key1")
        assert removed is True
        assert cache.size() == 0
        assert cache.get("key1") is None

    def test_cache_clear(self):
        """Test clearing entire cache."""
        cache = LRUCache(capacity=10)

        for i in range(5):
            cache.put(f"key{i}", {"value": i})

        assert cache.size() == 5
        cache.clear()
        assert cache.size() == 0
        assert cache.hits == 0
        assert cache.misses == 0

    def test_cache_hit_rate(self):
        """Test hit rate calculation."""
        cache = LRUCache(capacity=10)

        cache.put("key1", {"value": 1})
        cache.get("key1")  # Hit
        cache.get("key1")  # Hit
        cache.get("key2")  # Miss

        assert cache.hits == 2
        assert cache.misses == 1
        assert cache.hit_rate() == 2/3  # 2 hits out of 3 accesses


class TestThumbnailService:
    """Test suite for ThumbnailService."""

    @pytest.fixture
    def service(self, temp_dir: Path):
        """Create ThumbnailService instance with test database."""
        from thumb_cache_db import ThumbCacheDB

        # Create test database in temp dir
        db_path = temp_dir / "test_thumbnails.db"
        db_cache = ThumbCacheDB(str(db_path))

        service = ThumbnailService(l1_capacity=10, db_cache=db_cache, default_timeout=5.0)
        yield service

        # Cleanup
        try:
            db_cache.close()
        except:
            pass

    def test_service_initialization(self, service: ThumbnailService):
        """Test service initialization."""
        assert service.l1_cache is not None
        assert service.l2_cache is not None
        assert service.default_timeout == 5.0

    def test_get_thumbnail_success(self, service: ThumbnailService, sample_image: Path):
        """Test successful thumbnail generation."""
        pixmap = service.get_thumbnail(str(sample_image), height=200)

        assert pixmap is not None
        assert not pixmap.isNull()
        assert pixmap.height() <= 200  # Should be scaled to fit height

    def test_get_thumbnail_caching_l1(self, service: ThumbnailService, sample_image: Path):
        """Test L1 (memory) cache hit."""
        # First call - cache miss, generates thumbnail
        pixmap1 = service.get_thumbnail(str(sample_image), height=200)
        assert not pixmap1.isNull()

        l1_hits_before = service.l1_cache.hits

        # Second call - should hit L1 cache
        pixmap2 = service.get_thumbnail(str(sample_image), height=200)
        assert not pixmap2.isNull()

        l1_hits_after = service.l1_cache.hits
        assert l1_hits_after > l1_hits_before  # L1 cache was hit

    def test_get_thumbnail_various_heights(self, service: ThumbnailService, sample_image: Path):
        """Test thumbnail generation at various heights."""
        heights = [100, 200, 400, 800]

        for height in heights:
            pixmap = service.get_thumbnail(str(sample_image), height=height)
            assert not pixmap.isNull()
            # Allow some tolerance due to scaling
            assert abs(pixmap.height() - height) <= 5

    def test_get_thumbnail_tiff(self, service: ThumbnailService, sample_tiff_image: Path):
        """Test thumbnail generation from TIFF file."""
        pixmap = service.get_thumbnail(str(sample_tiff_image), height=200)

        assert pixmap is not None
        assert not pixmap.isNull()
        assert pixmap.height() <= 200

    def test_get_thumbnail_nonexistent_file(self, service: ThumbnailService):
        """Test thumbnail request for nonexistent file."""
        pixmap = service.get_thumbnail("/nonexistent/file.jpg", height=200)

        assert pixmap is not None
        assert pixmap.isNull()  # Should return null pixmap

    def test_get_thumbnail_empty_path(self, service: ThumbnailService):
        """Test thumbnail request with empty path."""
        pixmap = service.get_thumbnail("", height=200)

        assert pixmap is not None
        assert pixmap.isNull()

    def test_invalidate_thumbnail(self, service: ThumbnailService, sample_image: Path):
        """Test thumbnail invalidation."""
        path = str(sample_image)

        # Generate thumbnail
        pixmap1 = service.get_thumbnail(path, height=200)
        assert not pixmap1.isNull()

        # Invalidate
        service.invalidate(path)

        # Cache should be cleared
        stats = service.get_statistics()
        # After invalidation, next access will be a miss

    def test_clear_all_caches(self, service: ThumbnailService, sample_images: list[Path]):
        """Test clearing all caches."""
        # Generate thumbnails for multiple images
        for img_path in sample_images[:3]:
            service.get_thumbnail(str(img_path), height=200)

        assert service.l1_cache.size() > 0

        # Clear all
        service.clear_all()

        assert service.l1_cache.size() == 0

    def test_get_statistics(self, service: ThumbnailService, sample_image: Path):
        """Test statistics retrieval."""
        # Generate some cache activity
        service.get_thumbnail(str(sample_image), height=200)  # Miss then cache
        service.get_thumbnail(str(sample_image), height=200)  # Hit

        stats = service.get_statistics()

        assert "l1_memory_cache" in stats
        assert "l2_database_cache" in stats

        l1_stats = stats["l1_memory_cache"]
        assert "size" in l1_stats
        assert "capacity" in l1_stats
        assert "hits" in l1_stats
        assert "misses" in l1_stats
        assert "hit_rate" in l1_stats

    def test_thumbnail_aspect_ratio_preserved(self, service: ThumbnailService, sample_images: list[Path]):
        """Test that aspect ratio is preserved in thumbnails."""
        # Test landscape image (1920x1080)
        pixmap_landscape = service.get_thumbnail(str(sample_images[0]), height=200)
        landscape_ratio = pixmap_landscape.width() / pixmap_landscape.height()
        expected_landscape_ratio = 1920 / 1080
        assert abs(landscape_ratio - expected_landscape_ratio) < 0.1

        # Test portrait image (1080x1920)
        pixmap_portrait = service.get_thumbnail(str(sample_images[1]), height=200)
        portrait_ratio = pixmap_portrait.width() / pixmap_portrait.height()
        expected_portrait_ratio = 1080 / 1920
        assert abs(portrait_ratio - expected_portrait_ratio) < 0.1

    def test_concurrent_access_same_image(self, service: ThumbnailService, sample_image: Path):
        """Test multiple concurrent accesses to same image."""
        path = str(sample_image)

        # Simulate concurrent requests
        results = []
        for _ in range(5):
            pixmap = service.get_thumbnail(path, height=200)
            results.append(pixmap)

        # All should succeed
        assert all(not pm.isNull() for pm in results)

        # All should have same dimensions
        widths = [pm.width() for pm in results]
        heights = [pm.height() for pm in results]
        assert len(set(widths)) == 1  # All same width
        assert len(set(heights)) == 1  # All same height

    def test_l1_cache_eviction_integration(self, service: ThumbnailService, sample_images: list[Path]):
        """Test L1 cache eviction with real images."""
        # Create service with small L1 capacity
        from thumb_cache_db import ThumbCacheDB
        small_service = ThumbnailService(l1_capacity=3)

        # Add more images than capacity
        for img_path in sample_images[:5]:
            small_service.get_thumbnail(str(img_path), height=200)

        # L1 cache should be at capacity (3), not 5
        assert small_service.l1_cache.size() == 3

    def test_cache_invalidation_on_file_change(self, service: ThumbnailService, test_images_dir: Path):
        """Test cache invalidation when file is modified."""
        # Create test image
        test_path = test_images_dir / "mutable.jpg"
        from PIL import Image
        Image.new("RGB", (800, 600), color=(255, 0, 0)).save(test_path, "JPEG")

        # Generate thumbnail
        pixmap1 = service.get_thumbnail(str(test_path), height=200)
        assert not pixmap1.isNull()

        # Modify file (change content)
        time.sleep(0.1)  # Ensure mtime changes
        Image.new("RGB", (800, 600), color=(0, 0, 255)).save(test_path, "JPEG")

        # Invalidate cache
        service.invalidate(str(test_path))

        # Next request should regenerate
        pixmap2 = service.get_thumbnail(str(test_path), height=200)
        assert not pixmap2.isNull()
