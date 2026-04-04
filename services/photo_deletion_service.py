# services/photo_deletion_service.py
# Version 01.00.00.00 dated 20251105
# Service for photo deletion operations

import os
from typing import List, Dict, Any, Optional
from logging_config import get_logger
from repository import PhotoRepository, FolderRepository

logger = get_logger(__name__)


class DeletionResult:
    """Result of photo deletion operation."""

    def __init__(self):
        self.photos_deleted_from_db = 0
        self.files_deleted_from_disk = 0
        self.files_not_found = 0
        self.errors: List[str] = []
        self.paths_deleted: List[str] = []


class PhotoDeletionService:
    """
    Service for photo deletion operations.

    Handles:
    - Deleting photo metadata from database
    - Optionally deleting actual files from disk
    - Updating folder photo counts
    - Clearing thumbnail cache
    """

    def __init__(
        self,
        photo_repo: Optional[PhotoRepository] = None,
        folder_repo: Optional[FolderRepository] = None
    ):
        """
        Initialize photo deletion service.

        Args:
            photo_repo: PhotoRepository instance (creates new if None)
            folder_repo: FolderRepository instance (creates new if None)
        """
        self.photo_repo = photo_repo or PhotoRepository()
        self.folder_repo = folder_repo or FolderRepository()
        self.logger = logger

    def delete_photos(
        self,
        paths: List[str],
        delete_files: bool = False,
        invalidate_cache: bool = True
    ) -> DeletionResult:
        """
        Delete photos from database and optionally from disk.

        Args:
            paths: List of file paths to delete
            delete_files: If True, also delete actual files from disk
            invalidate_cache: If True, invalidate thumbnail cache entries

        Returns:
            DeletionResult with operation details
        """
        result = DeletionResult()

        if not paths:
            self.logger.warning("No paths provided for deletion")
            return result

        self.logger.info(f"Deleting {len(paths)} photos (delete_files={delete_files})")

        # First, get photo metadata to know folder_ids and project_ids before deletion
        folders_to_update = set()
        project_ids = set()
        for path in paths:
            photo = self.photo_repo.get_by_path(path)
            if photo:
                if photo.get('folder_id'):
                    folders_to_update.add(photo['folder_id'])
                if photo.get('project_id'):
                    project_ids.add(photo['project_id'])

        # Delete from database (per project for safety)
        try:
            deleted_count = 0
            for project_id in project_ids:
                deleted_count += self.photo_repo.delete_by_paths(paths, project_id)
            result.photos_deleted_from_db = deleted_count
            result.paths_deleted = paths[:deleted_count]
            self.logger.info(f"Deleted {deleted_count} photos from database")
        except Exception as e:
            error_msg = f"Database deletion failed: {e}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)
            return result

        # Delete actual files if requested
        if delete_files:
            for path in paths:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        result.files_deleted_from_disk += 1
                        self.logger.info(f"Deleted file: {path}")
                    else:
                        result.files_not_found += 1
                        self.logger.warning(f"File not found: {path}")
                except Exception as e:
                    error_msg = f"Failed to delete file {path}: {e}"
                    self.logger.error(error_msg)
                    result.errors.append(error_msg)

        # Update folder photo counts
        if folders_to_update and project_ids:
            project_id = list(project_ids)[0]
            self._update_folder_counts(folders_to_update, project_id)

        # Invalidate thumbnail cache
        if invalidate_cache:
            self._invalidate_thumbnails(paths)

        return result

    def delete_photos_by_ids(
        self,
        photo_ids: List[int],
        delete_files: bool = False,
        invalidate_cache: bool = True
    ) -> DeletionResult:
        """
        Delete photos by photo IDs from database and optionally from disk.

        This method is useful for duplicate management where we work with photo IDs
        rather than paths. The CASCADE foreign key on media_instance will automatically
        remove instance entries when photos are deleted.

        Args:
            photo_ids: List of photo IDs to delete
            delete_files: If True, also delete actual files from disk
            invalidate_cache: If True, invalidate thumbnail cache entries

        Returns:
            DeletionResult with operation details
        """
        result = DeletionResult()

        if not photo_ids:
            self.logger.warning("No photo IDs provided for deletion")
            return result

        self.logger.info(f"Deleting {len(photo_ids)} photos by ID (delete_files={delete_files})")

        # First, get photo metadata for paths and folder_ids
        paths = []
        folders_to_update = set()
        project_ids = set()  # Track project IDs for folder count updates

        for photo_id in photo_ids:
            photo = self.photo_repo.get_by_id(photo_id)
            if photo:
                path = photo.get('path')
                if path:
                    paths.append(path)
                if photo.get('folder_id'):
                    folders_to_update.add(photo['folder_id'])
                if photo.get('project_id'):
                    project_ids.add(photo['project_id'])

        if not paths:
            self.logger.warning("No valid photos found for provided IDs")
            return result

        # Delete from database (CASCADE will handle media_instance)
        try:
            # Use raw SQL to delete by IDs
            from repository.base_repository import DatabaseConnection
            db_conn = DatabaseConnection()

            placeholders = ','.join('?' * len(photo_ids))
            sql = f"DELETE FROM photo_metadata WHERE id IN ({placeholders})"

            with db_conn.get_connection(read_only=False) as conn:
                cur = conn.execute(sql, photo_ids)
                deleted_count = cur.rowcount
                conn.commit()

            result.photos_deleted_from_db = deleted_count
            result.paths_deleted = paths
            self.logger.info(f"Deleted {deleted_count} photos from database")

        except Exception as e:
            error_msg = f"Database deletion failed: {e}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)
            return result

        # Delete actual files if requested
        if delete_files:
            for path in paths:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        result.files_deleted_from_disk += 1
                        self.logger.info(f"Deleted file: {path}")
                    else:
                        result.files_not_found += 1
                        self.logger.warning(f"File not found: {path}")
                except Exception as e:
                    error_msg = f"Failed to delete file {path}: {e}"
                    self.logger.error(error_msg)
                    result.errors.append(error_msg)

        # Update folder photo counts
        if folders_to_update and project_ids:
            # Use first project_id (duplicates should all be from same project)
            project_id = list(project_ids)[0]
            self._update_folder_counts(folders_to_update, project_id)

        # Invalidate thumbnail cache
        if invalidate_cache:
            self._invalidate_thumbnails(paths)

        return result

    def delete_folder_photos(
        self,
        folder_id: int,
        project_id: int,
        delete_files: bool = False
    ) -> DeletionResult:
        """
        Delete all photos in a folder.

        Args:
            folder_id: Folder ID
            project_id: Project ID for proper isolation
            delete_files: If True, also delete actual files from disk

        Returns:
            DeletionResult with operation details
        """
        result = DeletionResult()

        # Get all photo paths in folder first (for file deletion if needed)
        photos = self.photo_repo.get_by_folder(folder_id)
        paths = [photo['path'] for photo in photos]

        if not paths:
            self.logger.info(f"No photos found in folder {folder_id}")
            return result

        self.logger.info(f"Deleting {len(paths)} photos from folder {folder_id}")

        # Delete from database
        try:
            deleted_count = self.photo_repo.delete_by_folder(folder_id, project_id)
            result.photos_deleted_from_db = deleted_count
            result.paths_deleted = paths
            self.logger.info(f"Deleted {deleted_count} photos from database")
        except Exception as e:
            error_msg = f"Database deletion failed: {e}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)
            return result

        # Delete actual files if requested
        if delete_files:
            for path in paths:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        result.files_deleted_from_disk += 1
                        self.logger.info(f"Deleted file: {path}")
                    else:
                        result.files_not_found += 1
                except Exception as e:
                    error_msg = f"Failed to delete file {path}: {e}"
                    self.logger.error(error_msg)
                    result.errors.append(error_msg)

        # Update folder photo count to 0
        try:
            self.folder_repo.update_photo_count(folder_id, 0)
            self.logger.info(f"Updated folder {folder_id} photo count to 0")
        except Exception as e:
            self.logger.warning(f"Failed to update folder count: {e}")

        # Invalidate thumbnail cache
        self._invalidate_thumbnails(paths)

        return result

    def _update_folder_counts(self, folder_ids: set, project_id: int):
        """
        Update photo counts for affected folders.

        Args:
            folder_ids: Set of folder IDs to update
            project_id: Project ID containing the folders
        """
        for folder_id in folder_ids:
            try:
                count = self.photo_repo.count_by_folder(folder_id, project_id)
                self.folder_repo.update_photo_count(folder_id, count)
                self.logger.debug(f"Updated folder {folder_id} count to {count}")
            except Exception as e:
                self.logger.warning(f"Failed to update folder {folder_id} count: {e}")

    def _invalidate_thumbnails(self, paths: List[str]):
        """
        Invalidate thumbnail cache entries for deleted photos.

        Args:
            paths: List of file paths
        """
        try:
            # Import here to avoid circular dependency
            from services import ThumbnailService
            thumb_service = ThumbnailService()

            for path in paths:
                thumb_service.invalidate(path)

            self.logger.debug(f"Invalidated {len(paths)} thumbnail cache entries")
        except Exception as e:
            self.logger.warning(f"Failed to invalidate thumbnails: {e}")
