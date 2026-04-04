# repository/folder_repository.py
# Version 01.00.00.00 dated 20251102
# Repository for photo_folders table operations

from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository
from logging_config import get_logger

logger = get_logger(__name__)


class FolderRepository(BaseRepository):
    """
    Repository for photo_folders operations.

    Handles folder hierarchy and navigation.
    """

    def _table_name(self) -> str:
        return "photo_folders"

    def get_by_path(self, path: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get folder by file system path and project.

        Uses case-insensitive matching on Windows to handle path casing variations.

        Args:
            path: File system path
            project_id: Project ID

        Returns:
            Folder dict or None
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()

            # CRITICAL FIX: Use case-insensitive matching on Windows
            # Windows file paths are case-insensitive but can be stored with different casing
            # SQLite's = operator is case-sensitive, so we normalize paths for comparison
            import platform
            if platform.system() == 'Windows':
                # Normalize both stored and query paths to lowercase for comparison
                # Also normalize slashes for consistency
                normalized_path = path.lower().replace('/', '\\')
                cur.execute(
                    """
                    SELECT * FROM photo_folders
                    WHERE LOWER(REPLACE(path, '/', '\\')) = ?
                    AND project_id = ?
                    """,
                    (normalized_path, project_id)
                )
            else:
                # Unix-like systems: use exact match (case-sensitive)
                cur.execute(
                    "SELECT * FROM photo_folders WHERE path = ? AND project_id = ?",
                    (path, project_id)
                )

            return cur.fetchone()

    def get_children(self, parent_id: Optional[int], project_id: int) -> List[Dict[str, Any]]:
        """
        Get all child folders of a parent within a project.

        Args:
            parent_id: Parent folder ID (None for root folders)
            project_id: Project ID

        Returns:
            List of child folders
        """
        if parent_id is None:
            where = "parent_id IS NULL AND project_id = ?"
            params = (project_id,)
        else:
            where = "parent_id = ? AND project_id = ?"
            params = (parent_id, project_id)

        return self.find_all(
            where_clause=where,
            params=params,
            order_by="name ASC"
        )

    def get_all_with_counts(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all folders with photo counts for a project.

        Args:
            project_id: Project ID

        Returns:
            List of folders with 'photo_count' field
        """
        sql = """
            SELECT
                f.id,
                f.parent_id,
                f.path,
                f.name,
                COUNT(p.id) as photo_count
            FROM photo_folders f
            LEFT JOIN photo_metadata p ON p.folder_id = f.id AND p.project_id = ?
            WHERE f.project_id = ?
            GROUP BY f.id
            ORDER BY f.parent_id IS NOT NULL, f.parent_id, f.name
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, project_id))
            return cur.fetchall()

    def ensure_folder(self, path: str, name: str, parent_id: Optional[int], project_id: int) -> int:
        """
        Ensure a folder exists in the database for a project (thread-safe).

        This method is safe for concurrent calls from multiple threads.
        It handles race conditions where multiple threads try to create
        the same folder simultaneously.

        Args:
            path: Full file system path
            name: Folder display name
            parent_id: Parent folder ID (None for root)
            project_id: Project ID

        Returns:
            Folder ID

        Thread Safety:
            Uses INSERT OR IGNORE followed by SELECT to atomically ensure
            the folder exists and retrieve its ID. This pattern is safe for
            concurrent operations.

        Algorithm:
            1. INSERT OR IGNORE (creates folder if doesn't exist, no-op if exists)
            2. COMMIT (always commit, operation is idempotent)
            3. SELECT to get folder ID (guaranteed to find it after commit)
        """
        sql_insert = """
            INSERT OR IGNORE INTO photo_folders (path, name, parent_id, project_id)
            VALUES (?, ?, ?, ?)
        """

        with self.connection() as conn:
            cur = conn.cursor()

            # Always try to insert (ignored if folder already exists)
            cur.execute(sql_insert, (path, name, parent_id, project_id))
            conn.commit()  # Always commit (idempotent operation)

            # CRITICAL FIX: Use case-insensitive matching on Windows for SELECT
            # Windows file paths are case-insensitive but can be stored with different casing
            # We need the same logic as get_by_path() to handle path variations
            import platform
            if platform.system() == 'Windows':
                # Normalize both stored and query paths to lowercase for comparison
                normalized_path = path.lower().replace('/', '\\')
                cur.execute(
                    """
                    SELECT id FROM photo_folders
                    WHERE LOWER(REPLACE(path, '/', '\\')) = ?
                    AND project_id = ?
                    """,
                    (normalized_path, project_id)
                )
            else:
                # Unix-like systems: use exact match (case-sensitive)
                cur.execute(
                    "SELECT id FROM photo_folders WHERE path = ? AND project_id = ?",
                    (path, project_id)
                )

            row = cur.fetchone()

            if row:
                # CRITICAL FIX: row is a dict (from row_factory), not a tuple!
                # Access by column name, not index
                folder_id = row['id']
                self.logger.debug(f"Ensured folder: {path} (id={folder_id}, project={project_id})")
                return folder_id

            # This should never happen (insert was successful, so select must find it)
            self.logger.error(f"CRITICAL: Folder disappeared after insert: {path} (project={project_id})")
            raise RuntimeError(f"Database inconsistency: folder {path} not found after INSERT")

    def get_folder_tree(self) -> List[Dict[str, Any]]:
        """
        Get folder hierarchy as a flat list with depth indicators.

        Returns:
            List of folders with computed depth
        """
        sql = """
            WITH RECURSIVE folder_tree AS (
                -- Root folders
                SELECT
                    id, parent_id, path, name,
                    0 as depth,
                    name as full_path
                FROM photo_folders
                WHERE parent_id IS NULL

                UNION ALL

                -- Child folders
                SELECT
                    f.id, f.parent_id, f.path, f.name,
                    ft.depth + 1,
                    ft.full_path || '/' || f.name
                FROM photo_folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
            )
            SELECT * FROM folder_tree
            ORDER BY full_path
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                return cur.fetchall()
            except Exception as e:
                # Fallback if recursive CTE not supported
                self.logger.warning(f"Recursive query failed: {e}, using simple query")
                return self.find_all(order_by="name ASC")

    def update_photo_count(self, folder_id: int, count: int):
        """
        Update the photo count for a folder.

        Args:
            folder_id: Folder ID
            count: Number of photos
        """
        sql = "UPDATE photo_folders SET photo_count = ? WHERE id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (count, folder_id))
            conn.commit()

        self.logger.debug(f"Updated folder {folder_id} photo count to {count}")

    def get_recursive_photo_count(self, folder_id: int, project_id: int) -> int:
        """
        Get total photo count including all subfolders within a project.

        Args:
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            Total photo count recursively
        """
        sql = """
            WITH RECURSIVE folder_tree AS (
                SELECT id FROM photo_folders WHERE id = ? AND project_id = ?
                UNION ALL
                SELECT f.id
                FROM photo_folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
                WHERE f.project_id = ?
            )
            SELECT COUNT(DISTINCT p.id) as count
            FROM photo_metadata p
            WHERE p.folder_id IN (SELECT id FROM folder_tree)
              AND p.project_id = ?
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, (folder_id, project_id, project_id, project_id))
                result = cur.fetchone()
                return result['count'] if result else 0
            except Exception as e:
                # Fallback to non-recursive count
                self.logger.warning(f"Recursive count failed: {e}, using simple count")
                from .photo_repository import PhotoRepository
                photo_repo = PhotoRepository(self.db_conn)
                return photo_repo.count_by_folder(folder_id, project_id)

    def get_all_folders(self) -> List[Dict[str, Any]]:
        """
        Get all folders ordered by path.

        Returns:
            List of all folders
        """
        return self.find_all(order_by="path ASC")

    def delete_folder(self, folder_id: int) -> bool:
        """
        Delete a folder (only if it has no photos).

        Args:
            folder_id: Folder ID

        Returns:
            True if deleted, False otherwise
        """
        # Check if folder has photos
        from .photo_repository import PhotoRepository
        photo_repo = PhotoRepository(self.db_conn)
        count = photo_repo.count_by_folder(folder_id)

        if count > 0:
            self.logger.warning(f"Cannot delete folder {folder_id}: has {count} photos")
            return False

        # Check if folder has children
        children = self.get_children(folder_id)
        if children:
            self.logger.warning(f"Cannot delete folder {folder_id}: has {len(children)} child folders")
            return False

        # Delete folder
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM photo_folders WHERE id = ?", (folder_id,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted folder {folder_id}")

        return deleted
