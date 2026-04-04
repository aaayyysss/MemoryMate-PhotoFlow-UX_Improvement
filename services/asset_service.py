# services/asset_service.py
# Version 01.01.00.00 dated 20260122
# Asset backfill and instance linking service
#
# Part of the asset-centric duplicate management system.
# Responsibilities:
# - Backfill file_hash for legacy photos
# - Link photos to assets via media_instance
# - Choose representative photos for duplicates
# - Provide duplicate listings for UI

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Callable
import hashlib
import os
from logging_config import get_logger

from repository.asset_repository import AssetRepository
from repository.photo_repository import PhotoRepository

logger = get_logger(__name__)


@dataclass(frozen=True)
class AssetBackfillStats:
    """Statistics from hash backfill and asset linking operation."""
    scanned: int
    hashed: int
    linked: int
    errors: int
    skipped: int


class AssetService:
    """
    AssetService manages the transition to an asset-centric model.

    Responsibilities:
    1) Ensure photo_metadata.file_hash is populated (backfill for legacy libraries)
    2) Ensure each photo has a media_instance pointing to a media_asset
    3) Choose and maintain representative photos for duplicate assets
    4) Provide duplicate listings for UI consumption

    This service is the bridge between file-centric (photo_metadata)
    and asset-centric (media_asset + media_instance) models.
    """

    def __init__(self, photo_repo: PhotoRepository, asset_repo: AssetRepository):
        """
        Initialize AssetService.

        Args:
            photo_repo: PhotoRepository instance
            asset_repo: AssetRepository instance
        """
        self.photo_repo = photo_repo
        self.asset_repo = asset_repo
        self.logger = get_logger(self.__class__.__name__)

    # =========================================================================
    # HASH COMPUTATION
    # =========================================================================

    @staticmethod
    def compute_file_hash(file_path: str, chunk_size: int = 65536) -> Optional[str]:
        """
        Compute SHA256 hash of file content.

        Performance optimization: Use 64KB chunks instead of 8KB for 3-5x faster I/O
        on modern systems with buffered disk access and SSD storage.

        Args:
            file_path: Path to file
            chunk_size: Read chunk size (default: 64KB for optimal performance)

        Returns:
            SHA256 hexdigest string or None if file doesn't exist/can't be read
        """
        if not os.path.exists(file_path):
            logger.warning(f"File not found for hashing: {file_path}")
            return None

        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                # Read in larger chunks for better throughput
                while chunk := f.read(chunk_size):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"Hash calculation failed for {file_path}: {e}")
            return None

    # =========================================================================
    # HASH BACKFILL & ASSET LINKING
    # =========================================================================

    def backfill_hashes_and_link_assets(
        self,
        project_id: int,
        batch_size: int = 500,
        stop_after: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> AssetBackfillStats:
        """
        Backfill algorithm (idempotent and resumable):

        Loop:
        1. Fetch photos missing file_hash OR missing media_instance link
        2. For each photo:
           a. Compute content hash (SHA256) if missing
           b. Update photo_metadata.file_hash
           c. Create or fetch media_asset by content_hash
           d. Link media_instance to asset
           e. Update representative photo if needed

        This algorithm is:
        - Idempotent: Safe to run multiple times
        - Resumable: Can be stopped and restarted
        - Progress-tracked: Calls progress_callback with (current, total)

        Args:
            project_id: Project ID
            batch_size: Number of photos to process per batch
            stop_after: Optional limit (for testing)
            progress_callback: Optional callback(current, total) for progress

        Returns:
            AssetBackfillStats with counts of operations
        """
        self.logger.info(f"Starting hash backfill and asset linking for project {project_id}")

        scanned = 0
        hashed = 0
        linked = 0
        errors = 0
        skipped = 0

        # Get total count for progress tracking
        total_without_instance = self.asset_repo.count_photos_without_instance(project_id)
        self.logger.info(f"Found {total_without_instance} photos without instance links")

        if total_without_instance == 0:
            self.logger.info("All photos already linked to assets")
            return AssetBackfillStats(
                scanned=0, hashed=0, linked=0, errors=0, skipped=0
            )

        processed = 0

        while True:
            # Fetch batch of photos without instances
            photos = self.asset_repo.get_photos_without_instance(project_id, limit=batch_size)

            if not photos:
                self.logger.info("No more photos to process")
                break

            for photo in photos:
                scanned += 1
                processed += 1

                # Call progress callback if provided
                if progress_callback:
                    progress_callback(processed, total_without_instance)

                try:
                    photo_id = photo["id"]
                    photo_path = photo["path"]
                    existing_hash = photo.get("file_hash")

                    # Step 1: Ensure file_hash exists
                    content_hash = existing_hash
                    if not content_hash:
                        content_hash = self.compute_file_hash(photo_path)
                        if not content_hash:
                            self.logger.warning(f"Could not compute hash for photo {photo_id} (path may be invalid)")
                            errors += 1
                            continue

                        # Update photo_metadata.file_hash
                        self.photo_repo.update_photo_hash(photo_id, content_hash)
                        hashed += 1
                        self.logger.debug(f"Computed hash for photo {photo_id}: {content_hash[:16]}...")

                    # Step 2: Create or fetch media_asset
                    asset_id = self.asset_repo.create_asset_if_missing(
                        project_id=project_id,
                        content_hash=content_hash,
                        representative_photo_id=None  # Will be chosen later
                    )

                    # Step 3: Link media_instance
                    self.asset_repo.link_instance(
                        project_id=project_id,
                        asset_id=asset_id,
                        photo_id=photo_id,
                        source_device_id=None,  # Unknown for legacy photos
                        source_path=photo_path,
                        import_session_id=None,
                        file_size=photo.get("size_kb") * 1024 if photo.get("size_kb") else None
                    )
                    linked += 1

                    # Step 4: Update representative photo if needed
                    self._update_representative_if_needed(project_id, asset_id)

                except Exception as e:
                    self.logger.error(f"Failed to process photo {photo.get('id')}: {e}", exc_info=True)
                    errors += 1
                    continue

                # Stop if limit reached
                if stop_after and processed >= stop_after:
                    self.logger.info(f"Stopped after processing {processed} photos (limit reached)")
                    break

            # Stop if limit reached
            if stop_after and processed >= stop_after:
                break

        stats = AssetBackfillStats(
            scanned=scanned,
            hashed=hashed,
            linked=linked,
            errors=errors,
            skipped=skipped
        )

        self.logger.info(f"Backfill complete: {stats}")
        return stats

    def _update_representative_if_needed(self, project_id: int, asset_id: int) -> None:
        """
        Update representative photo for asset if not set or if better candidate exists.

        Args:
            project_id: Project ID
            asset_id: Asset ID
        """
        asset = self.asset_repo.get_asset_by_id(project_id, asset_id)
        if not asset:
            return

        # If representative already set, skip (for now - could implement "better" logic later)
        if asset.get("representative_photo_id"):
            return

        # Choose representative from instances
        representative_photo_id = self.choose_representative_photo(project_id, asset_id)
        if representative_photo_id:
            self.asset_repo.set_representative_photo(project_id, asset_id, representative_photo_id)

    # =========================================================================
    # REPRESENTATIVE SELECTION
    # =========================================================================

    def choose_representative_photo(
        self,
        project_id: int,
        asset_id: int
    ) -> Optional[int]:
        """
        Deterministic representative selection.

        Contract:
        - Must be stable (same inputs = same output)
        - Must be explainable (log reasons)
        - Returns photo_id chosen, or None if no instances exist

        Selection criteria (in order):
        1. Higher resolution (width * height)
        2. Larger file size
        3. Earlier capture date
        4. Camera photos over screenshots (path heuristic)
        5. Earlier import time (created_at)

        Args:
            project_id: Project ID
            asset_id: Asset ID

        Returns:
            photo_id of chosen representative, or None
        """
        instances = self.asset_repo.list_asset_instances(project_id, asset_id)
        if not instances:
            self.logger.warning(f"No instances found for asset {asset_id}")
            return None

        # Fetch photo metadata for all instances
        photo_ids = [inst["photo_id"] for inst in instances]
        photos = []

        for photo_id in photo_ids:
            photo = self.photo_repo.get_by_id(photo_id)
            if photo:
                photos.append(photo)

        if not photos:
            self.logger.warning(f"No photo metadata found for asset {asset_id} instances")
            return None

        # Sort by selection criteria
        def selection_key(photo: Dict[str, Any]) -> tuple:
            """
            Return tuple for sorting (lower = better).
            We negate values that should be higher (resolution, file size).
            """
            width = photo.get("width") or 0
            height = photo.get("height") or 0
            resolution = width * height

            file_size = photo.get("size_kb") or 0

            # Date: earlier is better (use timestamp, fallback to high value)
            date_taken = photo.get("date_taken")
            if date_taken:
                # Convert to comparable value (earlier = lower)
                timestamp = photo.get("created_ts") or float('inf')
            else:
                timestamp = float('inf')

            # Prefer non-screenshot paths
            path = photo.get("path") or ""
            is_screenshot = 1 if "screenshot" in path.lower() else 0

            # Earlier import time (photo_id as proxy - lower ID = earlier)
            photo_id = photo.get("id") or float('inf')

            # Return tuple: (lower = better)
            return (
                -resolution,      # Higher resolution first (negated)
                -file_size,       # Larger file first (negated)
                timestamp,        # Earlier date first
                is_screenshot,    # Non-screenshots first
                photo_id          # Earlier import first
            )

        # Sort and select best
        sorted_photos = sorted(photos, key=selection_key)
        representative = sorted_photos[0]
        representative_id = representative["id"]

        self.logger.debug(
            f"Chose photo {representative_id} as representative for asset {asset_id} "
            f"(resolution: {representative.get('width')}x{representative.get('height')}, "
            f"size: {representative.get('size_kb')} KB)"
        )

        return representative_id

    # =========================================================================
    # DUPLICATE LISTING
    # =========================================================================

    def list_duplicates(
        self,
        project_id: int,
        min_instances: int = 2,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Returns duplicate assets with instance counts.
            
        Args:
            project_id: Project ID
            min_instances: Minimum number of instances to be considered duplicate (default: 2)
            limit: Maximum number of results to return (None = no limit)
            offset: Number of results to skip (for pagination)
            
        Returns:
            List of asset dicts with instance_count >= min_instances
        """
        return self.asset_repo.list_duplicate_assets(
            project_id, 
            min_instances=min_instances,
            limit=limit,
            offset=offset
        )
        
    def count_duplicates(self, project_id: int, min_instances: int = 2) -> int:
        """
        Count total number of duplicate assets.
            
        Args:
            project_id: Project ID
            min_instances: Minimum number of instances to be considered duplicate
            
        Returns:
            Total count of duplicate assets
        """
        return self.asset_repo.count_duplicate_assets(project_id, min_instances=min_instances)

    def get_duplicate_details(
        self,
        project_id: int,
        asset_id: int
    ) -> Dict[str, Any]:
        """
        Get detailed information about a duplicate asset for UI display.

        Args:
            project_id: Project ID
            asset_id: Asset ID

        Returns:
            Dictionary with asset info, instances, and photo details
        """
        asset = self.asset_repo.get_asset_by_id(project_id, asset_id)
        if not asset:
            return {}

        instances = self.asset_repo.list_asset_instances(project_id, asset_id)

        # Fetch photo details for each instance
        photos = []
        for instance in instances:
            photo = self.photo_repo.get_by_id(instance["photo_id"])
            if photo:
                # Merge instance metadata
                photo["instance_info"] = {
                    "source_device_id": instance.get("source_device_id"),
                    "source_path": instance.get("source_path"),
                    "import_session_id": instance.get("import_session_id"),
                    "file_size": instance.get("file_size"),
                    "import_date": instance.get("created_at")
                }
                photos.append(photo)

        return {
            "asset": asset,
            "instance_count": len(instances),
            "photos": photos
        }

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_backfill_progress(self, project_id: int) -> Dict[str, Any]:
        """
        Get current backfill progress statistics.

        Args:
            project_id: Project ID

        Returns:
            Dictionary with progress information
        """
        total_photos = self.photo_repo.count(where_clause="project_id = ?", params=(project_id,))
        photos_without_instance = self.asset_repo.count_photos_without_instance(project_id)
        photos_linked = total_photos - photos_without_instance

        progress_pct = (photos_linked / total_photos * 100) if total_photos > 0 else 100.0

        return {
            "total_photos": total_photos,
            "photos_linked": photos_linked,
            "photos_without_instance": photos_without_instance,
            "progress_percent": round(progress_pct, 2),
            "is_complete": photos_without_instance == 0
        }

    # =========================================================================
    # DUPLICATE DELETION
    # =========================================================================

    def delete_duplicate_photos(
        self,
        project_id: int,
        photo_ids: List[int],
        delete_files: bool = True
    ) -> Dict[str, Any]:
        """
        Delete duplicate photos and update asset representatives if needed.

        This method handles the complete deletion workflow:
        1. Identifies affected assets before deletion
        2. Deletes photos using PhotoDeletionService (CASCADE removes instances)
        3. Updates representatives for affected assets

        Args:
            project_id: Project ID
            photo_ids: List of photo IDs to delete
            delete_files: If True, also delete files from disk (default: True)

        Returns:
            Dictionary with deletion results and affected assets
        """
        from services.photo_deletion_service import PhotoDeletionService

        if not photo_ids:
            return {
                "success": False,
                "error": "No photo IDs provided",
                "photos_deleted": 0,
                "files_deleted": 0
            }

        self.logger.info(f"Deleting {len(photo_ids)} duplicate photos from project {project_id}")

        # Step 1: Identify affected assets BEFORE deletion
        affected_assets = set()
        representative_updates = {}  # asset_id -> was_representative_deleted

        for photo_id in photo_ids:
            # Check if this photo is linked to an asset
            asset_id = self.asset_repo.get_asset_id_by_photo_id(project_id, photo_id)
            if asset_id:
                affected_assets.add(asset_id)

                # Check if this photo is the representative
                asset = self.asset_repo.get_asset_by_id(project_id, asset_id)
                if asset and asset.get('representative_photo_id') == photo_id:
                    representative_updates[asset_id] = True
                    self.logger.info(f"Representative photo {photo_id} will be deleted from asset {asset_id}")

        self.logger.info(f"Deletion affects {len(affected_assets)} assets")

        # Step 2: Delete photos (CASCADE on media_instance handles instance deletion)
        deletion_service = PhotoDeletionService(
            photo_repo=self.photo_repo,
            folder_repo=None  # Will be created internally if needed
        )

        deletion_result = deletion_service.delete_photos_by_ids(
            photo_ids=photo_ids,
            delete_files=delete_files,
            invalidate_cache=True
        )

        # Step 3: Update representatives for affected assets
        updated_representatives = []

        for asset_id in affected_assets:
            # Check if asset still exists (could be deleted if all instances removed)
            asset = self.asset_repo.get_asset_by_id(project_id, asset_id)
            if not asset:
                self.logger.info(f"Asset {asset_id} was deleted (no remaining instances)")
                continue

            # Check remaining instance count
            remaining_instances = self.asset_repo.list_asset_instances(project_id, asset_id)

            if not remaining_instances:
                # No instances left - delete the asset
                self.asset_repo.delete({"asset_id": asset_id, "project_id": project_id})
                self.logger.info(f"Deleted asset {asset_id} (no remaining instances)")
            elif asset_id in representative_updates:
                # Representative was deleted, choose a new one
                new_rep_id = self.choose_representative_photo(project_id, asset_id)
                if new_rep_id:
                    self.asset_repo.set_representative_photo(project_id, asset_id, new_rep_id)
                    updated_representatives.append({
                        "asset_id": asset_id,
                        "new_representative_photo_id": new_rep_id
                    })
                    self.logger.info(f"Updated representative for asset {asset_id} to photo {new_rep_id}")

        # Step 4: Return results
        return {
            "success": True,
            "photos_deleted": deletion_result.photos_deleted_from_db,
            "files_deleted": deletion_result.files_deleted_from_disk,
            "files_not_found": deletion_result.files_not_found,
            "affected_assets": list(affected_assets),
            "updated_representatives": updated_representatives,
            "errors": deletion_result.errors
        }
