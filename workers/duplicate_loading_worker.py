# workers/duplicate_loading_worker.py
# Version 01.00.00.00 dated 20260118
# Background worker for loading duplicates asynchronously
#
# Part of the async duplicates loading system.
# Prevents UI freezes when loading large duplicate datasets.

from __future__ import annotations
from typing import Optional, List, Dict, Any
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool
from logging_config import get_logger

logger = get_logger(__name__)


class DuplicateLoadSignals(QObject):
    """Signals for async duplicate loading operations."""
    duplicates_loaded = Signal(int, list)  # (generation, duplicates_list)
    details_loaded = Signal(int, dict)     # (generation, details_dict)  
    error = Signal(int, str)               # (generation, error_message)


class DuplicateLoadWorker(QRunnable):
    """
    Background worker for loading duplicate assets from database.
    
    Prevents GUI freezes with large datasets by moving heavy queries
    to background threads, similar to PhotoLoadWorker pattern.
    """
    
    def __init__(
        self,
        project_id: int,
        operation: str,  # "list_duplicates", "get_details", or "count_duplicates"
        generation: int,
        signals: DuplicateLoadSignals,
        asset_id: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ):
        """
        Initialize duplicate loading worker.
        
        Args:
            project_id: Project ID to load duplicates for
            operation: Type of operation ("list_duplicates", "get_details", "count_duplicates")
            generation: Generation number for staleness checking
            signals: Signal object for communication
            asset_id: Asset ID for details loading (optional)
            limit: Maximum number of results (for pagination)
            offset: Number of results to skip (for pagination)
        """
        super().__init__()
        self.project_id = project_id
        self.operation = operation
        self.generation = generation
        self.signals = signals
        self.asset_id = asset_id
        self.limit = limit
        self.offset = offset
        
    def run(self):
        """Execute the duplicate loading operation in background thread."""
        try:
            if self.operation == "list_duplicates":
                self._load_duplicate_list()
            elif self.operation == "get_details":
                if self.asset_id is not None:
                    self._load_duplicate_details()
                else:
                    raise ValueError("asset_id required for get_details operation")
            elif self.operation == "count_duplicates":
                self._count_duplicates()
            else:
                raise ValueError(f"Unknown operation: {self.operation}")
                
        except Exception as e:
            logger.error(f"Duplicate loading worker failed: {e}", exc_info=True)
            self.signals.error.emit(self.generation, str(e))
    
    def _load_duplicate_list(self):
        """Load list of duplicate assets in background."""
        from services.asset_service import AssetService
        from repository.asset_repository import AssetRepository
        from repository.photo_repository import PhotoRepository
        from repository.base_repository import DatabaseConnection
        
        # Create per-thread database connection (thread-safe)
        db_conn = DatabaseConnection()
        photo_repo = PhotoRepository(db_conn)
        asset_repo = AssetRepository(db_conn)
        asset_service = AssetService(photo_repo, asset_repo)
        
        try:
            # Load duplicates with pagination support
            duplicates = asset_service.list_duplicates(
                self.project_id, 
                min_instances=2,
                limit=self.limit,
                offset=self.offset
            )
            logger.info(f"Loaded {len(duplicates)} duplicate assets in background (limit={self.limit}, offset={self.offset})")
            
            # Emit results with generation number for staleness checking
            self.signals.duplicates_loaded.emit(self.generation, duplicates)
            
        except Exception as e:
            logger.error(f"Failed to load duplicates in background: {e}", exc_info=True)
            raise
        # Database connection automatically cleaned up by context manager
    
    def _count_duplicates(self):
        """Count total duplicate assets in background."""
        from services.asset_service import AssetService
        from repository.asset_repository import AssetRepository
        from repository.photo_repository import PhotoRepository
        from repository.base_repository import DatabaseConnection
        
        # Create per-thread database connection (thread-safe)
        db_conn = DatabaseConnection()
        photo_repo = PhotoRepository(db_conn)
        asset_repo = AssetRepository(db_conn)
        asset_service = AssetService(photo_repo, asset_repo)
        
        try:
            # Count total duplicates
            count = asset_service.count_duplicates(self.project_id, min_instances=2)
            logger.info(f"Counted {count} total duplicate assets in background")
            
            # Emit count result
            # Note: We'll need to add a count_loaded signal to DuplicateLoadSignals
            
        except Exception as e:
            logger.error(f"Failed to count duplicates in background: {e}", exc_info=True)
            raise
        # Database connection automatically cleaned up by context manager
    
    def _load_duplicate_details(self):
        """Load detailed information for a specific duplicate asset."""
        from services.asset_service import AssetService
        from repository.asset_repository import AssetRepository
        from repository.photo_repository import PhotoRepository
        from repository.base_repository import DatabaseConnection
        
        # Create per-thread database connection (thread-safe)
        db_conn = DatabaseConnection()
        photo_repo = PhotoRepository(db_conn)
        asset_repo = AssetRepository(db_conn)
        asset_service = AssetService(photo_repo, asset_repo)
        
        try:
            # Load detailed information for the asset
            details = asset_service.get_duplicate_details(self.project_id, self.asset_id)
            logger.info(f"Loaded details for asset {self.asset_id} in background")
            
            # Emit results with generation number
            self.signals.details_loaded.emit(self.generation, details)
            
        except Exception as e:
            logger.error(f"Failed to load details in background: {e}", exc_info=True)
            raise
        # Database connection automatically cleaned up by context manager


# Convenience function for easy usage
def load_duplicates_async(
    project_id: int,
    generation: int,
    signals: DuplicateLoadSignals
) -> None:
    """
    Load duplicate list asynchronously.
    
    Args:
        project_id: Project ID
        generation: Generation number for staleness checking
        signals: Signal object for results
    """
    worker = DuplicateLoadWorker(
        project_id=project_id,
        operation="list_duplicates",
        generation=generation,
        signals=signals
    )
    QThreadPool.globalInstance().start(worker)


def load_duplicate_details_async(
    project_id: int,
    asset_id: int,
    generation: int,
    signals: DuplicateLoadSignals
) -> None:
    """
    Load duplicate details asynchronously.
    
    Args:
        project_id: Project ID
        asset_id: Asset ID to load details for
        generation: Generation number for staleness checking
        signals: Signal object for results
    """
    worker = DuplicateLoadWorker(
        project_id=project_id,
        operation="get_details",
        generation=generation,
        signals=signals,
        asset_id=asset_id
    )
    QThreadPool.globalInstance().start(worker)