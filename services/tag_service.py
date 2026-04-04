# services/tag_service.py
# Version 01.00.00.00 dated 2025-11-05
# Service layer for tag operations

from typing import Optional, List, Dict, Tuple
from logging_config import get_logger

logger = get_logger(__name__)


class TagService:
    """
    Service layer for tag operations.

    This service provides high-level tag operations that work with file paths
    instead of database IDs. It coordinates between TagRepository and
    PhotoRepository to provide a clean API for the UI layer.

    Responsibilities:
    - Business logic for tag operations
    - Path ↔ Photo ID resolution
    - Input validation
    - Error handling
    - Orchestration of repository calls

    Architecture:
        UI Layer (sidebar_qt.py, thumbnail_grid_qt.py)
                ↓
        TagService (this file)
                ↓
        TagRepository + PhotoRepository
                ↓
        DatabaseConnection
                ↓
        SQLite

    Exception Handling Approach:
    - Most methods return False/None/empty list on errors for UI convenience
    - All errors are logged with self.logger.error()
    - This design prioritizes UI usability over error propagation
    - Future enhancement: Could distinguish between expected errors
      (e.g., "tag not found") and unexpected errors (e.g., "database crashed")
      for better CLI/API support by raising custom exceptions
    """

    def __init__(self, tag_repository=None, photo_repository=None):
        """
        Initialize TagService with repositories.

        Args:
            tag_repository: TagRepository instance (optional, will create if None)
            photo_repository: PhotoRepository instance (optional, will create if None)
        """
        # Lazy import to avoid circular dependencies
        if tag_repository is None:
            from repository.tag_repository import TagRepository
            tag_repository = TagRepository()

        if photo_repository is None:
            from repository.photo_repository import PhotoRepository
            photo_repository = PhotoRepository()

        self._tag_repo = tag_repository
        self._photo_repo = photo_repository
        self.logger = logger

    # ========================================================================
    # TAG ASSIGNMENT (Work with paths, not IDs)
    # ========================================================================

    def assign_tag(self, photo_path: str, tag_name: str, project_id: int) -> bool:
        """
        Assign a tag to a photo by file path.

        Args:
            photo_path: Full path to photo file
            tag_name: Tag name to assign
            project_id: Project ID (Schema v3.0.0 requirement)

        Returns:
            True if assigned, False if photo not found or already had tag

        Example:
            >>> service.assign_tag("/photos/img001.jpg", "favorite", project_id=1)
            True
        """
        tag_name = tag_name.strip()
        if not tag_name:
            self.logger.warning("Cannot assign empty tag name")
            return False

        # Get photo ID from path, creating photo_metadata entry if needed
        photo = self._photo_repo.get_by_path(photo_path, project_id)
        if not photo:
            # Auto-create photo_metadata entry if it doesn't exist
            photo_id = self._ensure_photo_metadata_exists(photo_path, project_id)
            if not photo_id:
                self.logger.warning(f"Photo not found and could not be created: {photo_path}")
                return False
        else:
            photo_id = photo['id']

        # Ensure tag exists for this project (Schema v3.1.0)
        try:
            tag_id = self._tag_repo.ensure_exists(tag_name, project_id)
        except Exception as e:
            self.logger.error(f"Failed to ensure tag exists '{tag_name}': {e}")
            return False

        # Add tag to photo
        try:
            added = self._tag_repo.add_to_photo(photo_id, tag_id)
            if added:
                self.logger.info(f"Assigned tag '{tag_name}' to photo: {photo_path}")
            return added
        except Exception as e:
            self.logger.error(f"Failed to assign tag '{tag_name}' to {photo_path}: {e}")
            return False

    def remove_tag(self, photo_path: str, tag_name: str, project_id: int) -> bool:
        """
        Remove a tag from a photo by file path.

        Args:
            photo_path: Full path to photo file
            tag_name: Tag name to remove
            project_id: Project ID (Schema v3.0.0 requirement)

        Returns:
            True if removed, False if not found

        Example:
            >>> service.remove_tag("/photos/img001.jpg", "favorite", project_id=1)
            True
        """
        # Get photo ID
        photo = self._photo_repo.get_by_path(photo_path, project_id)
        if not photo:
            return False

        # Get tag ID for this project (Schema v3.1.0)
        tag = self._tag_repo.get_by_name(tag_name, project_id)
        if not tag:
            return False

        # Remove association
        try:
            removed = self._tag_repo.remove_from_photo(photo['id'], tag['id'])
            if removed:
                self.logger.info(f"Removed tag '{tag_name}' from photo: {photo_path}")
            return removed
        except Exception as e:
            self.logger.error(f"Failed to remove tag '{tag_name}' from {photo_path}: {e}")
            return False

    def get_tags_for_path(self, photo_path: str, project_id: int) -> List[str]:
        """
        Get all tag names for a photo by file path within a specific project.

        Args:
            photo_path: Full path to photo file
            project_id: Project ID for tag isolation

        Returns:
            List of tag names (empty if photo not found)
        """
        if project_id is None:
            # Project scoping is mandatory to avoid cross-project tag leakage
            self.logger.warning("get_tags_for_path called without project_id; returning empty list")
            return []

        photo = self._photo_repo.get_by_path(photo_path, project_id)
        if not photo:
            return []

        try:
            tags = self._tag_repo.get_tags_for_photo(photo['id'])
            return [tag['name'] for tag in tags]
        except Exception as e:
            self.logger.error(f"Failed to get tags for {photo_path} (project={project_id}): {e}")
            return []

    def get_paths_by_tag(self, tag_name: str, project_id: int) -> List[str]:
        """
        Get all photo paths in a project that have a specific tag.

        Args:
            tag_name: Tag name
            project_id: Project ID for tag isolation

        Returns:
            List of photo file paths
        """
        try:
            # Get photo IDs for this tag within the project
            photo_ids = self._tag_repo.get_photo_ids_by_tag_name(tag_name, project_id)
            if not photo_ids:
                return []

            # Get paths for these photo IDs
            paths = []
            for photo_id in photo_ids:
                photo = self._photo_repo.get_by_id(photo_id)
                if photo and 'path' in photo:
                    paths.append(photo['path'])

            return paths

        except Exception as e:
            self.logger.error(f"Failed to get paths for tag '{tag_name}' (project={project_id}): {e}")
            return []

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def assign_tags_bulk(self, photo_paths: List[str], tag_name: str, project_id: int) -> int:
        """
        Assign a tag to multiple photos (bulk operation).

        Args:
            photo_paths: List of photo file paths
            tag_name: Tag name to assign
            project_id: Project ID (Schema v3.0.0 requirement)

        Returns:
            Number of photos successfully tagged

        Example:
            >>> paths = ['/photos/img001.jpg', '/photos/img002.jpg']
            >>> service.assign_tags_bulk(paths, "vacation", project_id=1)
            2
        """
        if not photo_paths:
            return 0

        tag_name = tag_name.strip()
        if not tag_name:
            return 0

        try:
            # Ensure tag exists for this project (Schema v3.1.0)
            tag_id = self._tag_repo.ensure_exists(tag_name, project_id)

            # Get photo IDs for all paths, creating photo_metadata entries if needed
            photo_ids = []
            created_count = 0

            for path in photo_paths:
                photo = self._photo_repo.get_by_path(path, project_id)

                # If photo doesn't exist in photo_metadata, create it
                # (This happens when photos are in project_images but not photo_metadata)
                if not photo:
                    photo_id = self._ensure_photo_metadata_exists(path, project_id)
                    if photo_id:
                        photo_ids.append(photo_id)
                        created_count += 1
                else:
                    photo_ids.append(photo['id'])

            if created_count > 0:
                self.logger.info(f"Auto-created {created_count} photo_metadata entries for tagging")

            if not photo_ids:
                self.logger.warning(f"No valid photo IDs found for {len(photo_paths)} paths")
                return 0

            # Bulk add
            count = self._tag_repo.add_to_photos_bulk(photo_ids, tag_id)
            self.logger.info(f"Bulk assigned tag '{tag_name}' to {count} photos")
            return count

        except Exception as e:
            self.logger.error(f"Failed bulk tag assignment: {e}", exc_info=True)
            return 0

    def _find_parent_folder_id(self, folder_path: str, folder_repo, project_id: int) -> Optional[int]:
        """
        Find the parent folder ID for a given folder path.

        Walks up the directory tree to find an existing parent folder in the database.
        If no parent is found, returns None (indicating this should be a root folder).

        Args:
            folder_path: Full path to the folder
            folder_repo: FolderRepository instance
            project_id: Project ID

        Returns:
            Parent folder ID, or None if this is a root folder
        """
        import os

        # Walk up the directory tree to find existing parent
        current_path = os.path.dirname(folder_path)

        while current_path:
            # Try to find this parent in the database
            parent_folder = folder_repo.get_by_path(current_path, project_id)
            if parent_folder:
                self.logger.debug(f"Found parent folder for {folder_path}: {current_path} (id={parent_folder['id']})")
                return parent_folder['id']

            # Move up one level
            parent_path = os.path.dirname(current_path)

            # Avoid infinite loop - stop if we're not making progress
            if parent_path == current_path:
                break

            current_path = parent_path

        # No parent found - this will be a root folder
        self.logger.debug(f"No parent found for {folder_path} - will be root folder")
        return None

    def _ensure_photo_metadata_exists(self, path: str, project_id: int) -> Optional[int]:
        """
        Ensure a photo exists in photo_metadata table.

        This is needed when photos exist in project_images but not in photo_metadata.
        Creates minimal metadata entry so photo can be tagged.

        Args:
            path: Photo file path
            project_id: Project ID (Schema v3.0.0 requirement)

        Returns:
            Photo ID, or None if creation failed
        """
        import os

        try:
            # Check if file exists
            if not os.path.exists(path):
                self.logger.warning(f"Photo file does not exist: {path}")
                return None

            # Get or create folder for this photo
            from repository.folder_repository import FolderRepository
            folder_repo = FolderRepository(self._photo_repo._db_connection)

            folder_path = os.path.dirname(path)
            folder_name = os.path.basename(folder_path) if folder_path else "Unknown"

            # CRITICAL FIX: Find proper parent folder instead of using None
            # Using None creates orphaned folders that break the tree view
            parent_id = self._find_parent_folder_id(folder_path, folder_repo, project_id)

            # Ensure folder exists with proper parent
            folder_id = folder_repo.ensure_folder(folder_path, folder_name, parent_id, project_id)

            # Get file stats
            stat = os.stat(path)
            size_kb = stat.st_size / 1024.0
            import time
            modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

            # Create photo_metadata entry
            photo_id = self._photo_repo.upsert(
                path=path,
                folder_id=folder_id,
                project_id=project_id,
                size_kb=size_kb,
                modified=modified,
                width=None,  # Will be populated by metadata scan later
                height=None,
                date_taken=None,
                tags=None
            )

            # CRITICAL FIX: Also add photo to project_images for 'all' branch
            # Without this, the photo exists in photo_metadata but not in project_images,
            # causing count mismatches (e.g., 299 in metadata vs 298 in all branch)
            try:
                from reference_db import ReferenceDB
                db = ReferenceDB()
                db.add_project_image(project_id=project_id, image_path=path, branch_key='all', label=None)
                self.logger.debug(f"Added photo to project_images (all branch): {path}")
            except Exception as e:
                # If photo already exists in project_images, that's fine
                if "UNIQUE constraint failed" in str(e):
                    self.logger.debug(f"Photo already in project_images: {path}")
                else:
                    self.logger.warning(f"Failed to add photo to project_images: {e}")

            self.logger.debug(f"Created photo_metadata entry for: {path} (id={photo_id})")
            return photo_id

        except Exception as e:
            self.logger.error(f"Failed to ensure photo_metadata exists for {path}: {e}", exc_info=True)
            return None

    def get_tags_for_paths(self, photo_paths: List[str], project_id: int) -> Dict[str, List[str]]:
        """
        Get tags for multiple photos (bulk operation).

        Args:
            photo_paths: List of photo file paths
            project_id: Project ID (Schema v3.0.0 requirement)

        Returns:
            Dict mapping photo_path to list of tag names

        Example:
            >>> paths = ['/photos/img001.jpg', '/photos/img002.jpg']
            >>> service.get_tags_for_paths(paths, project_id=1)
            {
                '/photos/img001.jpg': ['favorite', 'vacation'],
                '/photos/img002.jpg': ['vacation', 'beach']
            }
        """
        if not photo_paths:
            return {}

        try:
            # Build path -> photo_id mapping
            path_to_id = {}
            id_to_path = {}

            for path in photo_paths:
                photo = self._photo_repo.get_by_path(path, project_id)
                if photo:
                    photo_id = photo['id']
                    path_to_id[path] = photo_id
                    id_to_path[photo_id] = path

            if not path_to_id:
                return {path: [] for path in photo_paths}

            # Get tags for all photo IDs (bulk)
            photo_ids = list(path_to_id.values())
            tags_by_id = self._tag_repo.get_tags_for_photos(photo_ids)

            # Convert back to paths
            result = {}
            for path in photo_paths:
                photo_id = path_to_id.get(path)
                if photo_id and photo_id in tags_by_id:
                    tag_names = [tag['name'] for tag in tags_by_id[photo_id]]
                    result[path] = tag_names
                else:
                    result[path] = []

            return result

        except Exception as e:
            self.logger.error(f"Failed to get tags for paths: {e}")
            return {path: [] for path in photo_paths}

    # ========================================================================
    # TAG MANAGEMENT
    # ========================================================================

    def get_all_tags_with_counts(self, project_id: int | None = None) -> List[Tuple[str, int]]:
        """
        Get all tags with their photo counts.

        Args:
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all tags globally.

        Returns:
            List of tuples: (tag_name, photo_count)
            Ordered alphabetically by tag name

        Example:
            >>> service.get_all_tags_with_counts(project_id=1)
            [('beach', 5), ('favorite', 12), ('vacation', 8)]
        """
        try:
            return self._tag_repo.get_all_with_counts(project_id)
        except Exception as e:
            self.logger.error(f"Failed to get tags with counts: {e}")
            return []

    def get_all_tags(self, project_id: int | None = None) -> List[str]:
        """
        Get all tag names.

        Args:
            project_id: Optional project ID to scope tag names

        Returns:
            List of tag names ordered alphabetically

        Example:
            >>> service.get_all_tags(project_id=1)
            ['beach', 'favorite', 'vacation']
        """
        try:
            tags = self._tag_repo.get_all(project_id)
            return [tag['name'] for tag in tags]
        except Exception as e:
            self.logger.error(f"Failed to get all tags: {e}")
            return []

    def ensure_tag_exists(self, tag_name: str, project_id: int) -> Optional[int]:
        """
        Ensure a tag exists within a project, creating it if necessary (Schema v3.1.0).

        Args:
            tag_name: Tag name
            project_id: Project ID for tag isolation

        Returns:
            Tag ID, or None if creation failed

        Example:
            >>> service.ensure_tag_exists("new-tag", project_id=1)
            42
        """
        tag_name = tag_name.strip()
        if not tag_name:
            return None

        try:
            return self._tag_repo.ensure_exists(tag_name, project_id)
        except Exception as e:
            self.logger.error(f"Failed to ensure tag exists '{tag_name}': {e}")
            return None

    def rename_tag(self, old_name: str, new_name: str, project_id: int) -> bool:
        """
        Rename a tag within a project (or merge if new name exists) (Schema v3.1.0).

        Args:
            old_name: Current tag name
            new_name: New tag name
            project_id: Project ID for tag isolation

        Returns:
            True if renamed/merged, False if failed

        Example:
            >>> service.rename_tag("favourites", "favorite", project_id=1)
            True
        """
        try:
            return self._tag_repo.rename(old_name, new_name, project_id)
        except Exception as e:
            self.logger.error(f"Failed to rename tag '{old_name}' to '{new_name}': {e}")
            return False

    def delete_tag(self, tag_name: str, project_id: int) -> bool:
        """
        Delete a tag from a project and remove it from all photos (Schema v3.1.0).

        Args:
            tag_name: Tag name to delete
            project_id: Project ID for tag isolation

        Returns:
            True if deleted, False if not found

        Example:
            >>> service.delete_tag("old-tag", project_id=1)
            True
        """
        try:
            return self._tag_repo.delete_by_name(tag_name, project_id)
        except Exception as e:
            self.logger.error(f"Failed to delete tag '{tag_name}': {e}")
            return False

    def get_photo_count(self, tag_name: str, project_id: int) -> int:
        """
        Get number of photos with this tag within a project (Schema v3.1.0).

        Args:
            tag_name: Tag name
            project_id: Project ID for tag isolation

        Returns:
            Number of photos, or 0 if tag not found

        Example:
            >>> service.get_photo_count("favorite", project_id=1)
            12
        """
        try:
            tag = self._tag_repo.get_by_name(tag_name, project_id)
            if not tag:
                return 0
            return self._tag_repo.get_photo_count(tag['id'])
        except Exception as e:
            self.logger.error(f"Failed to get photo count for tag '{tag_name}': {e}")
            return 0


# Singleton instance for convenient access
_tag_service_instance = None


def get_tag_service() -> TagService:
    """
    Get singleton TagService instance.

    Returns:
        TagService instance

    Example:
        >>> from services.tag_service import get_tag_service
        >>> tag_service = get_tag_service()
        >>> tag_service.assign_tag("/photos/img.jpg", "favorite")
    """
    global _tag_service_instance
    if _tag_service_instance is None:
        _tag_service_instance = TagService()
    return _tag_service_instance
