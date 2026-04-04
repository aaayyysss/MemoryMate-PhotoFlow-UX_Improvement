# services/__init__.py
# Version 01.00.02.00 dated 20251105
# Service layer package - Business logic separated from UI and data access

from .photo_scan_service import (
    PhotoScanService,
    ScanResult,
    ScanProgress
)

from .scan_worker_adapter import (
    ScanWorkerAdapter,
    ScanWorker  # Backward compatibility alias
)

from .metadata_service import (
    MetadataService,
    ImageMetadata
)

# Thumbnail service requires Qt - only import if Qt is available
# This allows services to be imported in headless/CLI environments
try:
    from .thumbnail_service import (
        ThumbnailService,
        LRUCache,
        get_thumbnail_service,
        install_qt_message_handler,
        PIL_PREFERRED_FORMATS
    )
    _THUMBNAIL_SERVICE_AVAILABLE = True
except ImportError:
    # Qt not available - thumbnail service cannot be imported
    ThumbnailService = None
    LRUCache = None
    get_thumbnail_service = None
    install_qt_message_handler = None
    PIL_PREFERRED_FORMATS = None
    _THUMBNAIL_SERVICE_AVAILABLE = False

from .photo_deletion_service import (
    PhotoDeletionService,
    DeletionResult
)

from .search_service import (
    SearchService,
    SearchCriteria,
    SearchResult
)

from .tag_service import (
    TagService,
    get_tag_service
)

__all__ = [
    # Scanning
    'PhotoScanService',
    'ScanResult',
    'ScanProgress',
    'ScanWorkerAdapter',
    'ScanWorker',

    # Metadata
    'MetadataService',
    'ImageMetadata',

    # Thumbnails (may be None in headless mode)
    'ThumbnailService',
    'LRUCache',
    'get_thumbnail_service',
    'install_qt_message_handler',
    'PIL_PREFERRED_FORMATS',

    # Deletion
    'PhotoDeletionService',
    'DeletionResult',

    # Search
    'SearchService',
    'SearchCriteria',
    'SearchResult',

    # Tags
    'TagService',
    'get_tag_service',
]
