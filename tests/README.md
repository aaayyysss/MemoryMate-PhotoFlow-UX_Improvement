# MemoryMate-PhotoFlow Tests

Integration tests for the service layer and repository pattern.

## Test Structure

```
tests/
├── __init__.py                   # Test package
├── conftest.py                   # Pytest fixtures and configuration
├── test_metadata_service.py      # MetadataService tests
├── test_thumbnail_service.py     # ThumbnailService and LRUCache tests
├── test_repositories.py          # Repository layer tests
├── test_photo_scan_service.py    # PhotoScanService tests
└── README.md                     # This file
```

## Running Tests

### Prerequisites

Install pytest and pytest-qt:

```bash
pip install pytest pytest-qt
```

### Run All Tests

```bash
# From project root
pytest tests/

# With verbose output
pytest tests/ -v

# With coverage report
pytest tests/ --cov=services --cov=repository
```

### Run Specific Test Files

```bash
# Test specific service
pytest tests/test_metadata_service.py -v
pytest tests/test_thumbnail_service.py -v
pytest tests/test_repositories.py -v
pytest tests/test_photo_scan_service.py -v
```

### Run Specific Test Classes or Methods

```bash
# Test specific class
pytest tests/test_thumbnail_service.py::TestLRUCache -v

# Test specific method
pytest tests/test_metadata_service.py::TestMetadataService::test_extract_basic_metadata_jpeg -v
```

## Test Coverage

### MetadataService Tests (test_metadata_service.py)

- ✅ Basic metadata extraction (width, height, date_taken)
- ✅ Full metadata extraction with all fields
- ✅ EXIF date normalization (YYYY:MM:DD → YYYY-MM-DD)
- ✅ Multiple date format parsing
- ✅ Images without EXIF data
- ✅ Nonexistent file handling
- ✅ Invalid image file handling
- ✅ TIFF file support
- ✅ Batch processing multiple images
- ✅ File size and timestamp extraction

### ThumbnailService Tests (test_thumbnail_service.py)

#### LRUCache Tests
- ✅ Cache initialization
- ✅ Put and get operations
- ✅ Cache miss behavior
- ✅ LRU eviction when capacity exceeded
- ✅ Access order tracking
- ✅ Cache invalidation
- ✅ Cache clearing
- ✅ Hit rate calculation

#### ThumbnailService Tests
- ✅ Service initialization
- ✅ Thumbnail generation from various formats (JPEG, PNG, TIFF)
- ✅ L1 (memory) cache hits
- ✅ Various thumbnail heights
- ✅ Nonexistent file handling
- ✅ Empty path handling
- ✅ Cache invalidation
- ✅ Clear all caches
- ✅ Statistics retrieval
- ✅ Aspect ratio preservation
- ✅ Concurrent access to same image
- ✅ L1 cache eviction integration
- ✅ Cache invalidation on file modification

### Repository Tests (test_repositories.py)

#### DatabaseConnection Tests
- ✅ Singleton pattern
- ✅ Connection context manager
- ✅ WAL mode enabled
- ✅ Dict factory for row results

#### PhotoRepository Tests
- ✅ Find by ID
- ✅ Find by path
- ✅ Bulk upsert (insert)
- ✅ Bulk upsert (update)
- ✅ Get all photos
- ✅ Delete photo

#### FolderRepository Tests
- ✅ Ensure folder (insert new)
- ✅ Ensure folder (existing)
- ✅ Find by path
- ✅ Get children folders
- ✅ Update photo count
- ✅ Folder hierarchy integrity

#### ProjectRepository Tests
- ✅ Create project
- ✅ Get all projects
- ✅ Ensure branch (insert new)
- ✅ Ensure branch (existing)
- ✅ Get branches for project
- ✅ Delete project
- ✅ Transaction rollback on error

### PhotoScanService Tests (test_photo_scan_service.py)

- ✅ Service initialization
- ✅ Scan empty folder
- ✅ Scan single image
- ✅ Scan multiple images
- ✅ Scan nested folder structure
- ✅ Incremental scan (skip unchanged files)
- ✅ Incremental scan (detect changes)
- ✅ Progress callback
- ✅ Cancel callback (interruption)
- ✅ EXIF date extraction
- ✅ Skip EXIF extraction mode
- ✅ Batch processing
- ✅ Timeout protection for slow filesystems
- ✅ Update folder photo counts
- ✅ ScanResult structure validation
- ✅ Nonexistent folder handling
- ✅ File instead of folder handling
- ✅ Supported format detection
- ✅ Unsupported format filtering
- ✅ Concurrent scan safety
- ✅ Progress percentage accuracy

## Fixtures

### Common Fixtures (conftest.py)

- `temp_dir`: Temporary directory for test files
- `test_db_path`: Temporary database path
- `test_images_dir`: Directory for test images
- `sample_image`: Single JPEG image with EXIF data (800x600)
- `sample_images`: List of 5 images in various formats and sizes
- `sample_tiff_image`: TIFF format image
- `nested_folder_structure`: Nested folder hierarchy with images
- `init_test_database`: Initialized test database with schema
- `mock_photo_metadata`: Mock photo metadata for testing

## Writing New Tests

### Example Test Structure

```python
import pytest
from services import YourService

class TestYourService:
    """Test suite for YourService."""

    @pytest.fixture
    def service(self):
        """Create service instance."""
        return YourService()

    def test_basic_functionality(self, service: YourService):
        """Test basic service functionality."""
        result = service.do_something()
        assert result is not None

    def test_error_handling(self, service: YourService):
        """Test error handling."""
        with pytest.raises(ValueError):
            service.do_invalid_thing()
```

### Best Practices

1. **Isolation**: Each test should be independent
2. **Cleanup**: Use fixtures to ensure cleanup after tests
3. **Assertions**: Be specific about what you're testing
4. **Error Cases**: Test both success and failure paths
5. **Documentation**: Use clear test names and docstrings

## CI/CD Integration

To integrate with CI/CD pipelines:

```yaml
# GitHub Actions example
- name: Run Tests
  run: |
    pip install pytest pytest-qt pytest-cov
    pytest tests/ --cov=services --cov=repository --cov-report=xml
```

## Troubleshooting

### Qt Platform Plugin Error

If you see "Could not find the Qt platform plugin":

```bash
export QT_QPA_PLATFORM=offscreen
pytest tests/
```

### Database Locked Errors

Tests use temporary databases. If you see "database is locked":
- Ensure previous test cleanup completed
- Check that no other process is accessing test databases

### Image Generation Issues

If PIL/Pillow tests fail:
- Verify Pillow is installed: `pip install Pillow`
- Check image formats are supported in your Pillow installation

## Test Metrics

Current test coverage:
- **MetadataService**: ~95% coverage
- **ThumbnailService**: ~90% coverage
- **PhotoScanService**: ~85% coverage
- **Repository Layer**: ~90% coverage

## Future Test Additions

Planned test areas:
- Performance benchmarks
- Stress testing with large datasets
- Memory leak detection
- Concurrent access patterns
- Integration with MainWindow UI
