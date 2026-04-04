# repository/tag_repository.py
# Version 01.00.00.00 dated 2025-11-05
# Repository for tags and photo_tags table operations

from typing import Optional, List, Dict, Any, Tuple
from .base_repository import BaseRepository, DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


class TagRepository(BaseRepository):
    """
    Repository for tag operations.

    Handles all database operations related to tags:
    - Tag CRUD operations
    - Photo-tag associations
    - Tag queries and statistics
    - Bulk operations

    This repository provides pure data access with no business logic.
    Business logic belongs in TagService (service layer).
    """

    def _table_name(self) -> str:
        return "tags"

    # ========================================================================
    # TAG CRUD OPERATIONS
    # ========================================================================

    def create(self, tag_name: str, project_id: int) -> int:
        """
        Create a new tag for a project (Schema v3.1.0).

        Args:
            tag_name: Tag name (case-insensitive)
            project_id: Project ID for tag isolation

        Returns:
            Tag ID

        Raises:
            Exception if tag creation fails
        """
        tag_name = tag_name.strip()
        if not tag_name:
            raise ValueError("Tag name cannot be empty")
        if project_id is None:
            raise ValueError("project_id is required for tag creation (Schema v3.1.0)")

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO tags (name, project_id) VALUES (?, ?)", (tag_name, project_id))
            conn.commit()
            tag_id = cur.lastrowid

        self.logger.debug(f"Created tag: {tag_name} (id={tag_id}, project={project_id})")
        return tag_id

    def get_by_id(self, tag_id: int) -> Optional[Dict[str, Any]]:
        """
        Get tag by ID.

        Args:
            tag_id: Tag ID

        Returns:
            Tag dict with 'id' and 'name', or None if not found
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name FROM tags WHERE id = ?", (tag_id,))
            return cur.fetchone()

    def get_by_name(self, tag_name: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get tag by name within a project (Schema v3.1.0).

        Args:
            tag_name: Tag name (case-insensitive)
            project_id: Project ID for tag isolation

        Returns:
            Tag dict with 'id', 'name', and 'project_id', or None if not found
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, project_id FROM tags WHERE name = ? AND project_id = ? COLLATE NOCASE",
                (tag_name, project_id)
            )
            return cur.fetchone()

    def get_all(self, project_id: int | None = None) -> List[Dict[str, Any]]:
        """
        Get all tags ordered alphabetically (Schema v3.1.0).

        Args:
            project_id: Filter by project_id. If None, returns all tags globally (for migration).

        Returns:
            List of tag dicts with 'id', 'name', and 'project_id'
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            if project_id is not None:
                cur.execute(
                    "SELECT id, name, project_id FROM tags WHERE project_id = ? ORDER BY name COLLATE NOCASE",
                    (project_id,)
                )
            else:
                cur.execute("SELECT id, name, project_id FROM tags ORDER BY name COLLATE NOCASE")
            return cur.fetchall()

    def delete(self, tag_id: int) -> bool:
        """
        Delete a tag and all its photo associations.

        Args:
            tag_id: Tag ID

        Returns:
            True if deleted, False if not found
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Check if exists
            cur.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,))
            if not cur.fetchone():
                return False

            # Delete tag (CASCADE will remove photo_tags entries)
            cur.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()

        self.logger.debug(f"Deleted tag id={tag_id}")
        return True

    def delete_by_name(self, tag_name: str, project_id: int) -> bool:
        """
        Delete a tag by name within a project (Schema v3.1.0).

        Args:
            tag_name: Tag name
            project_id: Project ID for tag isolation

        Returns:
            True if deleted, False if not found
        """
        tag = self.get_by_name(tag_name, project_id)
        if not tag:
            return False
        return self.delete(tag['id'])

    def rename(self, old_name: str, new_name: str, project_id: int) -> bool:
        """
        Rename a tag within a project (Schema v3.1.0). If new_name exists, merge old into new.

        Args:
            old_name: Current tag name
            new_name: New tag name
            project_id: Project ID for tag isolation

        Returns:
            True if renamed/merged, False if old_name not found
        """
        old_name = old_name.strip()
        new_name = new_name.strip()

        if not old_name or not new_name:
            raise ValueError("Tag names cannot be empty")

        if old_name.lower() == new_name.lower():
            return True  # Nothing to do

        with self.connection() as conn:
            cur = conn.cursor()

            # Get old tag
            old_tag = self.get_by_name(old_name, project_id)
            if not old_tag:
                return False

            # Check if new tag exists
            new_tag = self.get_by_name(new_name, project_id)

            if new_tag:
                # Merge: reassign all old_tag photos to new_tag
                cur.execute("""
                    INSERT OR IGNORE INTO photo_tags (photo_id, tag_id)
                    SELECT photo_id, ? FROM photo_tags WHERE tag_id = ?
                """, (new_tag['id'], old_tag['id']))

                # Delete old tag
                cur.execute("DELETE FROM tags WHERE id = ?", (old_tag['id'],))
                self.logger.info(f"Merged tag '{old_name}' into '{new_name}' (project={project_id})")
            else:
                # Simple rename
                cur.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, old_tag['id']))
                self.logger.info(f"Renamed tag '{old_name}' to '{new_name}' (project={project_id})")

            conn.commit()

        return True

    def ensure_exists(self, tag_name: str, project_id: int) -> int:
        """
        Ensure a tag exists within a project, creating it if necessary (Schema v3.1.0).

        Args:
            tag_name: Tag name
            project_id: Project ID for tag isolation

        Returns:
            Tag ID (existing or newly created)
        """
        tag_name = tag_name.strip()
        if not tag_name:
            raise ValueError("Tag name cannot be empty")

        # Try to get existing tag for this project
        tag = self.get_by_name(tag_name, project_id)
        if tag:
            return tag['id']

        # Create new tag for this project
        return self.create(tag_name, project_id)

    # ========================================================================
    # TAG STATISTICS & QUERIES
    # ========================================================================

    def get_all_with_counts(self, project_id: int | None = None) -> List[Tuple[str, int]]:
        """
        Get all tags with their photo counts.

        Args:
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all tags globally.

        Returns:
            List of tuples: (tag_name, photo_count)
            Ordered alphabetically by tag name
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id by joining with photo_metadata
                cur.execute("""
                    SELECT t.name, COUNT(DISTINCT pt.photo_id) as count
                    FROM tags t
                    LEFT JOIN photo_tags pt ON pt.tag_id = t.id
                    LEFT JOIN photo_metadata pm ON pm.id = pt.photo_id
                    WHERE t.project_id = ?
                      AND (pm.project_id = ? OR pm.id IS NULL)
                    GROUP BY t.id, t.name
                    ORDER BY t.name COLLATE NOCASE
                """, (project_id, project_id))
            else:
                # No project filter - get all tags globally
                cur.execute("""
                    SELECT t.name, COUNT(pt.photo_id) as count
                    FROM tags t
                    LEFT JOIN photo_tags pt ON pt.tag_id = t.id
                    GROUP BY t.id, t.name
                    ORDER BY t.name COLLATE NOCASE
                """)
            return [(row['name'], row['count']) for row in cur.fetchall()]

    def get_photo_count(self, tag_id: int) -> int:
        """
        Get number of photos with this tag.

        Args:
            tag_id: Tag ID

        Returns:
            Number of photos
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) as count
                FROM photo_tags
                WHERE tag_id = ?
            """, (tag_id,))
            result = cur.fetchone()
            return result['count'] if result else 0

    # ========================================================================
    # PHOTO-TAG ASSOCIATIONS
    # ========================================================================

    def add_to_photo(self, photo_id: int, tag_id: int) -> bool:
        """
        Associate a tag with a photo.

        Args:
            photo_id: Photo ID
            tag_id: Tag ID

        Returns:
            True if added, False if already existed
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Check if already exists
            cur.execute("""
                SELECT 1 FROM photo_tags
                WHERE photo_id = ? AND tag_id = ?
            """, (photo_id, tag_id))

            if cur.fetchone():
                return False  # Already exists

            # Validate project consistency (prevent cross-project leakage)
            cur.execute("SELECT project_id FROM photo_metadata WHERE id = ?", (photo_id,))
            photo_proj_row = cur.fetchone()
            cur.execute("SELECT project_id FROM tags WHERE id = ?", (tag_id,))
            tag_proj_row = cur.fetchone()
            if photo_proj_row and tag_proj_row:
                if photo_proj_row['project_id'] != tag_proj_row['project_id']:
                    raise ValueError(f"Cannot assign tag_id={tag_id} from project {tag_proj_row['project_id']} to photo_id={photo_id} in project {photo_proj_row['project_id']}")

            # Add association
            cur.execute("""
                INSERT INTO photo_tags (photo_id, tag_id)
                VALUES (?, ?)
            """, (photo_id, tag_id))
            conn.commit()

        self.logger.debug(f"Added tag {tag_id} to photo {photo_id}")
        return True

    def remove_from_photo(self, photo_id: int, tag_id: int) -> bool:
        """
        Remove a tag from a photo.

        Args:
            photo_id: Photo ID
            tag_id: Tag ID

        Returns:
            True if removed, False if didn't exist
        """
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM photo_tags
                WHERE photo_id = ? AND tag_id = ?
            """, (photo_id, tag_id))
            affected = cur.rowcount
            conn.commit()

        if affected > 0:
            self.logger.debug(f"Removed tag {tag_id} from photo {photo_id}")
            return True
        return False

    def get_tags_for_photo(self, photo_id: int) -> List[Dict[str, Any]]:
        """
        Get all tags for a photo.

        Args:
            photo_id: Photo ID

        Returns:
            List of tag dicts with 'id' and 'name'
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.id, t.name
                FROM tags t
                JOIN photo_tags pt ON pt.tag_id = t.id
                JOIN photo_metadata pm ON pm.id = pt.photo_id
                WHERE pt.photo_id = ?
                  AND t.project_id = pm.project_id
                ORDER BY t.name COLLATE NOCASE
            """, (photo_id,))
            return cur.fetchall()

    def get_photo_ids_by_tag(self, tag_id: int) -> List[int]:
        """
        Get all photo IDs that have this tag.

        Args:
            tag_id: Tag ID

        Returns:
            List of photo IDs
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT photo_id
                FROM photo_tags
                WHERE tag_id = ?
                ORDER BY photo_id
            """, (tag_id,))
            return [row['photo_id'] for row in cur.fetchall()]

    def get_photo_ids_by_tag_name(self, tag_name: str, project_id: int) -> List[int]:
        """
        Get all photo IDs within a project that have this tag (by name).

        Args:
            tag_name: Tag name
            project_id: Project ID to scope tag lookup

        Returns:
            List of photo IDs
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT pt.photo_id
                FROM photo_tags pt
                JOIN tags t ON t.id = pt.tag_id
                JOIN photo_metadata pm ON pm.id = pt.photo_id
                WHERE t.name = ? COLLATE NOCASE
                  AND t.project_id = ?
                  AND pm.project_id = ?
                ORDER BY pt.photo_id
            """, (tag_name, project_id, project_id))
            return [row['photo_id'] for row in cur.fetchall()]

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def get_tags_for_photos(self, photo_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """
        Get tags for multiple photos (bulk operation).

        Args:
            photo_ids: List of photo IDs

        Returns:
            Dict mapping photo_id to list of tag dicts
        """
        if not photo_ids:
            return {}

        # Initialize result dict
        result = {pid: [] for pid in photo_ids}

        # SQLite variable limit is 999, chunk to be safe
        CHUNK_SIZE = 500

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()

            for i in range(0, len(photo_ids), CHUNK_SIZE):
                chunk = photo_ids[i:i + CHUNK_SIZE]
                placeholders = ','.join('?' * len(chunk))

                cur.execute(f"""
                    SELECT pt.photo_id, t.id, t.name
                    FROM photo_tags pt
                    JOIN tags t ON t.id = pt.tag_id
                    JOIN photo_metadata pm ON pm.id = pt.photo_id
                    WHERE pt.photo_id IN ({placeholders})
                      AND t.project_id = pm.project_id
                    ORDER BY pt.photo_id, t.name COLLATE NOCASE
                """, chunk)

                for row in cur.fetchall():
                    photo_id = row['photo_id']
                    tag_dict = {'id': row['id'], 'name': row['name']}
                    if photo_id in result:
                        result[photo_id].append(tag_dict)

        return result

    def add_to_photos_bulk(self, photo_ids: List[int], tag_id: int) -> int:
        """
        Add a tag to multiple photos (bulk operation).

        Args:
            photo_ids: List of photo IDs
            tag_id: Tag ID

        Returns:
            Number of new associations created
        """
        if not photo_ids:
            return 0

        with self.connection() as conn:
            cur = conn.cursor()

            # Use INSERT OR IGNORE to skip existing associations
            values = [(pid, tag_id) for pid in photo_ids]
            cur.executemany("""
                INSERT OR IGNORE INTO photo_tags (photo_id, tag_id)
                VALUES (?, ?)
            """, values)

            affected = cur.rowcount
            conn.commit()

        self.logger.info(f"Added tag {tag_id} to {affected} photos (out of {len(photo_ids)} requested)")
        return affected

    def remove_from_photos_bulk(self, photo_ids: List[int], tag_id: int) -> int:
        """
        Remove a tag from multiple photos (bulk operation).

        Args:
            photo_ids: List of photo IDs
            tag_id: Tag ID

        Returns:
            Number of associations removed
        """
        if not photo_ids:
            return 0

        # Chunk for SQLite variable limit
        CHUNK_SIZE = 500
        total_removed = 0

        with self.connection() as conn:
            cur = conn.cursor()

            for i in range(0, len(photo_ids), CHUNK_SIZE):
                chunk = photo_ids[i:i + CHUNK_SIZE]
                placeholders = ','.join('?' * len(chunk))

                cur.execute(f"""
                    DELETE FROM photo_tags
                    WHERE photo_id IN ({placeholders}) AND tag_id = ?
                """, chunk + [tag_id])

                total_removed += cur.rowcount

            conn.commit()

        self.logger.info(f"Removed tag {tag_id} from {total_removed} photos")
        return total_removed
