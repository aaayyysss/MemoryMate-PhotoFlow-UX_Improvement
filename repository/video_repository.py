# repository/video_repository.py
# Version 1.0.0 dated 2025-11-09
# Repository for video_metadata, project_videos, and video_tags table operations

from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository
from logging_config import get_logger
import platform

logger = get_logger(__name__)


class VideoRepository(BaseRepository):
    """
    Repository for video operations (Schema v3.2.0).

    Handles all database operations related to videos:
    - Video metadata CRUD operations
    - Project-video associations
    - Video-tag associations
    - Bulk operations
    - Path normalization (Windows case-insensitive)

    This repository provides pure data access with no business logic.
    Business logic belongs in VideoService (service layer).
    """

    def _table_name(self) -> str:
        return "video_metadata"

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        Normalize path for consistent storage and querying.

        On Windows, paths are case-insensitive, so we lowercase them
        AND convert backslashes to forward slashes for consistent keys.
        This matches photo path normalization exactly, preventing join
        mismatches between photos and videos in tags, duplicates, and stacks.

        On Unix, paths are case-sensitive, so we keep them as-is.

        Args:
            path: File path

        Returns:
            Normalized path (forward slashes, lowercase on Windows)
        """
        if platform.system() == 'Windows':
            # CRITICAL: Replace backslashes THEN lowercase to match photo normalization
            # Without this, C:\foo\bar.mp4 and c:/foo/bar.mp4 are different DB keys
            return path.replace('\\', '/').lower()
        return path

    # ========================================================================
    # VIDEO METADATA CRUD OPERATIONS
    # ========================================================================

    def get_by_path(self, path: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get video metadata by path within a project.

        Args:
            path: Video file path
            project_id: Project ID for isolation

        Returns:
            Video metadata dict, or None if not found
        """
        normalized_path = self._normalize_path(path)

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM video_metadata
                WHERE path = ? AND project_id = ?
            """, (normalized_path, project_id))
            return cur.fetchone()

    def get_by_folder(self, folder_id: int, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos in a folder (and optionally subfolders).

        Args:
            folder_id: Folder ID
            project_id: Project ID for isolation

        Returns:
            List of video metadata dicts
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM video_metadata
                WHERE folder_id = ? AND project_id = ?
                ORDER BY path
            """, (folder_id, project_id))
            return cur.fetchall()

    def get_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos in a project.

        Args:
            project_id: Project ID

        Returns:
            List of video metadata dicts
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM video_metadata
                WHERE project_id = ?
                ORDER BY date_taken DESC, path
            """, (project_id,))
            return cur.fetchall()

    def create(self, path: str, folder_id: int, project_id: int, **metadata) -> int:
        """
        Create a new video metadata entry.

        Args:
            path: Video file path
            folder_id: Folder ID
            project_id: Project ID
            **metadata: Optional metadata (size_kb, modified, duration_seconds, etc.)

        Returns:
            Video ID

        Example:
            >>> repo.create("/videos/clip.mp4", folder_id=5, project_id=1,
            ...             size_kb=102400, duration_seconds=45.2,
            ...             width=1920, height=1080, fps=30.0, codec="h264")
            123
        """
        normalized_path = self._normalize_path(path)

        # Build dynamic SQL for optional metadata
        columns = ['path', 'folder_id', 'project_id']
        values = [normalized_path, folder_id, project_id]

        # Add optional metadata fields
        optional_fields = [
            'size_kb', 'modified', 'duration_seconds', 'width', 'height',
            'fps', 'codec', 'bitrate', 'date_taken', 'created_ts',
            'created_date', 'created_year', 'updated_at',
            'metadata_status', 'metadata_fail_count', 'thumbnail_status'
        ]

        for field in optional_fields:
            if field in metadata:
                columns.append(field)
                values.append(metadata[field])

        placeholders = ','.join(['?'] * len(values))
        column_names = ','.join(columns)

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                INSERT INTO video_metadata ({column_names})
                VALUES ({placeholders})
            """, values)
            conn.commit()
            video_id = cur.lastrowid

        self.logger.debug(f"Created video: {path} (id={video_id}, project={project_id})")
        return video_id

    def upsert(self, path: str, folder_id: int, project_id: int, **metadata) -> int:
        """
        Create or update a video metadata entry.

        Args:
            path: Video file path
            folder_id: Folder ID
            project_id: Project ID
            **metadata: Optional metadata

        Returns:
            Video ID

        Example:
            >>> repo.upsert("/videos/clip.mp4", folder_id=5, project_id=1,
            ...             size_kb=102400, duration_seconds=45.2)
            123
        """
        existing = self.get_by_path(path, project_id)

        if existing:
            # Update existing entry
            video_id = existing['id']
            self.update(video_id, **metadata)
            return video_id
        else:
            # Create new entry
            return self.create(path, folder_id, project_id, **metadata)

    def update(self, video_id: int, **metadata) -> bool:
        """
        Update video metadata fields.

        Args:
            video_id: Video ID
            **metadata: Fields to update

        Returns:
            True if updated, False if not found

        Example:
            >>> repo.update(123, duration_seconds=45.2, width=1920, height=1080)
            True
        """
        if not metadata:
            return True  # Nothing to update

        # Build dynamic SQL for updates
        set_clauses = []
        values = []

        for field, value in metadata.items():
            set_clauses.append(f"{field} = ?")
            values.append(value)

        values.append(video_id)

        with self.connection() as conn:
            cur = conn.cursor()

            # Check if exists
            cur.execute("SELECT 1 FROM video_metadata WHERE id = ?", (video_id,))
            if not cur.fetchone():
                return False

            # Update
            set_sql = ', '.join(set_clauses)
            cur.execute(f"""
                UPDATE video_metadata
                SET {set_sql}
                WHERE id = ?
            """, values)
            conn.commit()

        self.logger.debug(f"Updated video id={video_id}: {list(metadata.keys())}")
        return True

    def delete(self, video_id: int) -> bool:
        """
        Delete a video metadata entry (CASCADE will remove associations).

        Args:
            video_id: Video ID

        Returns:
            True if deleted, False if not found
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Check if exists
            cur.execute("SELECT 1 FROM video_metadata WHERE id = ?", (video_id,))
            if not cur.fetchone():
                return False

            # Delete (CASCADE will remove project_videos and video_tags)
            cur.execute("DELETE FROM video_metadata WHERE id = ?", (video_id,))
            conn.commit()

        self.logger.debug(f"Deleted video id={video_id}")
        return True

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def bulk_upsert(self, rows: List[Dict[str, Any]], project_id: int) -> int:
        """
        Bulk insert/update video metadata entries.

        Args:
            rows: List of video dicts with 'path', 'folder_id', and optional metadata
            project_id: Project ID

        Returns:
            Number of rows inserted/updated

        Example:
            >>> rows = [
            ...     {'path': '/vid1.mp4', 'folder_id': 5, 'size_kb': 102400},
            ...     {'path': '/vid2.mp4', 'folder_id': 5, 'size_kb': 204800}
            ... ]
            >>> repo.bulk_upsert(rows, project_id=1)
            2
        """
        if not rows:
            return 0

        count = 0
        with self.connection() as conn:
            cur = conn.cursor()

            for row in rows:
                path = self._normalize_path(row['path'])
                folder_id = row['folder_id']

                # Extract metadata (exclude path and folder_id)
                metadata = {k: v for k, v in row.items() if k not in ('path', 'folder_id')}

                # Check if exists
                cur.execute("""
                    SELECT id FROM video_metadata
                    WHERE path = ? AND project_id = ?
                """, (path, project_id))
                existing = cur.fetchone()

                if existing:
                    # Update
                    if metadata:
                        set_clauses = []
                        values = []
                        for field, value in metadata.items():
                            set_clauses.append(f"{field} = ?")
                            values.append(value)
                        values.append(existing['id'])

                        set_sql = ', '.join(set_clauses)
                        cur.execute(f"""
                            UPDATE video_metadata
                            SET {set_sql}
                            WHERE id = ?
                        """, values)
                else:
                    # Insert
                    columns = ['path', 'folder_id', 'project_id']
                    values = [path, folder_id, project_id]

                    for field, value in metadata.items():
                        columns.append(field)
                        values.append(value)

                    placeholders = ','.join(['?'] * len(values))
                    column_names = ','.join(columns)

                    cur.execute(f"""
                        INSERT INTO video_metadata ({column_names})
                        VALUES ({placeholders})
                    """, values)

                count += 1

            conn.commit()

        self.logger.info(f"Bulk upserted {count} videos for project {project_id}")
        return count

    def get_unprocessed_videos(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get videos that need metadata extraction.

        Args:
            limit: Maximum number of videos to return

        Returns:
            List of video metadata dicts with pending status
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM video_metadata
                WHERE metadata_status = 'pending'
                ORDER BY id
                LIMIT ?
            """, (limit,))
            return cur.fetchall()

    # ========================================================================
    # PROJECT-VIDEO ASSOCIATIONS
    # ========================================================================

    def add_to_project_branch(self, project_id: int, branch_key: str, video_path: str, label: str = None) -> bool:
        """
        Add video to a project branch (like project_images).

        Args:
            project_id: Project ID
            branch_key: Branch key (e.g., 'all', date, folder name)
            video_path: Video file path
            label: Optional label

        Returns:
            True if added, False if already exists
        """
        normalized_path = self._normalize_path(video_path)

        with self.connection() as conn:
            cur = conn.cursor()

            # Check if already exists
            cur.execute("""
                SELECT 1 FROM project_videos
                WHERE project_id = ? AND branch_key = ? AND video_path = ?
            """, (project_id, branch_key, normalized_path))

            if cur.fetchone():
                return False  # Already exists

            # Add association
            cur.execute("""
                INSERT INTO project_videos (project_id, branch_key, video_path, label)
                VALUES (?, ?, ?, ?)
            """, (project_id, branch_key, normalized_path, label))
            conn.commit()

        self.logger.debug(f"Added video to project {project_id}/{branch_key}: {video_path}")
        return True

    def get_videos_by_branch(self, project_id: int, branch_key: str) -> List[str]:
        """
        Get all video paths in a project branch.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            List of video file paths
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT video_path FROM project_videos
                WHERE project_id = ? AND branch_key = ?
                ORDER BY video_path
            """, (project_id, branch_key))
            return [row['video_path'] for row in cur.fetchall()]

    # ========================================================================
    # VIDEO-TAG ASSOCIATIONS
    # ========================================================================

    def add_tag(self, video_id: int, tag_id: int) -> bool:
        """
        Associate a tag with a video.

        Args:
            video_id: Video ID
            tag_id: Tag ID

        Returns:
            True if added, False if already existed
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Check if already exists
            cur.execute("""
                SELECT 1 FROM video_tags
                WHERE video_id = ? AND tag_id = ?
            """, (video_id, tag_id))

            if cur.fetchone():
                return False  # Already exists

            # Add association
            cur.execute("""
                INSERT INTO video_tags (video_id, tag_id)
                VALUES (?, ?)
            """, (video_id, tag_id))
            conn.commit()

        self.logger.debug(f"Added tag {tag_id} to video {video_id}")
        return True

    def remove_tag(self, video_id: int, tag_id: int) -> bool:
        """
        Remove a tag from a video.

        Args:
            video_id: Video ID
            tag_id: Tag ID

        Returns:
            True if removed, False if didn't exist
        """
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM video_tags
                WHERE video_id = ? AND tag_id = ?
            """, (video_id, tag_id))
            affected = cur.rowcount
            conn.commit()

        if affected > 0:
            self.logger.debug(f"Removed tag {tag_id} from video {video_id}")
            return True
        return False

    def get_tags_for_video(self, video_id: int) -> List[Dict[str, Any]]:
        """
        Get all tags for a video.

        Args:
            video_id: Video ID

        Returns:
            List of tag dicts with 'id' and 'name'
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.id, t.name
                FROM tags t
                JOIN video_tags vt ON vt.tag_id = t.id
                WHERE vt.video_id = ?
                ORDER BY t.name COLLATE NOCASE
            """, (video_id,))
            return cur.fetchall()

    def get_videos_by_tag(self, tag_id: int) -> List[int]:
        """
        Get all video IDs that have this tag.

        Args:
            tag_id: Tag ID

        Returns:
            List of video IDs
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT video_id FROM video_tags
                WHERE tag_id = ?
                ORDER BY video_id
            """, (tag_id,))
            return [row['video_id'] for row in cur.fetchall()]
