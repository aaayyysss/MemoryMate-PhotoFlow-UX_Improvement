# repository/photo_repository.py
# Version 02.01.00.01 dated 20260127
# Repository for photo_metadata table operations

from typing import Optional, List, Dict, Any
from .base_repository import BaseRepository, DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


class PhotoRepository(BaseRepository):
    """
    Repository for photo_metadata operations.

    Handles all database operations related to photo metadata:
    - CRUD operations
    - Searching and filtering
    - Metadata updates
    - Bulk operations
    """

    def _table_name(self) -> str:
        return "photo_metadata"

    def get_by_id(self, photo_id: int) -> Optional[Dict[str, Any]]:
        """Get photo metadata by primary ID."""
        return self.find_by_id(photo_id, id_column="id")

    def get_by_path(self, path: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get photo metadata by file path and project.

        Args:
            path: Full file path
            project_id: Project ID

        Returns:
            Photo metadata dict or None
        """
        # Normalize path for consistent lookups (handles Windows backslash/forward slash)
        normalized_path = self._normalize_path(path)

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM photo_metadata WHERE path = ? AND project_id = ?",
                (normalized_path, project_id)
            )
            return cur.fetchone()

    def _normalize_path(self, path: str) -> str:
        """
        Normalize file path for consistent database storage.

        On Windows, converts backslashes to forward slashes and normalizes case.
        This prevents duplicates like 'C:\\path\\photo.jpg' vs 'C:/path/photo.jpg'
        and 'C:/Path/Photo.jpg' vs 'c:/path/photo.jpg'

        Args:
            path: File path to normalize

        Returns:
            Normalized path string (lowercase on Windows)
        """
        import os
        import platform

        # Normalize path components (resolve .., ., etc)
        normalized = os.path.normpath(path)
        # Convert backslashes to forward slashes for consistent storage
        # SQLite stores paths as strings, so C:\path != C:/path
        normalized = normalized.replace('\\', '/')

        # CRITICAL FIX: Lowercase on Windows to handle case-insensitive filesystem
        # SQLite UNIQUE constraints are case-sensitive by default, so without this
        # C:/Path/Photo.jpg and c:/path/photo.jpg are treated as different rows
        if platform.system() == 'Windows':
            normalized = normalized.lower()

        return normalized

    def get_by_folder(self, folder_id: int, project_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all photos in a folder within a project.

        Args:
            folder_id: Folder ID
            project_id: Project ID
            limit: Optional maximum number of results

        Returns:
            List of photo metadata dicts
        """
        return self.find_all(
            where_clause="folder_id = ? AND project_id = ?",
            params=(folder_id, project_id),
            order_by="modified DESC",
            limit=limit
        )

    def get_by_date_range(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Get photos taken within a date range.

        Args:
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)

        Returns:
            List of photo metadata dicts
        """
        return self.find_all(
            where_clause="date_taken >= ? AND date_taken <= ?",
            params=(start_date, end_date),
            order_by="date_taken ASC"
        )

    def get_photos_in_time_window(
        self,
        project_id: int,
        reference_timestamp: int,
        time_window_seconds: int,
        folder_id: Optional[int] = None,
        exclude_photo_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get photos within a time window of a reference timestamp.

        Used for similar shot detection - finds photos taken near the same time
        as a reference photo (e.g., burst shots, photo series).

        Args:
            project_id: Project ID
            reference_timestamp: Reference Unix timestamp (created_ts)
            time_window_seconds: Time window in seconds (+/- around reference)
            folder_id: Optional folder filter (for same-folder burst detection)
            exclude_photo_ids: Optional list of photo IDs to exclude

        Returns:
            List of photo metadata dicts within time window, ordered by timestamp

        Example:
            # Find photos within 10 seconds of reference photo
            candidates = repo.get_photos_in_time_window(
                project_id=1,
                reference_timestamp=1704067200,  # 2024-01-01 00:00:00
                time_window_seconds=10,
                folder_id=5  # Same folder only
            )
        """
        # Calculate time bounds
        min_ts = reference_timestamp - time_window_seconds
        max_ts = reference_timestamp + time_window_seconds

        # Build WHERE clause
        where_parts = [
            "project_id = ?",
            "created_ts IS NOT NULL",
            "created_ts BETWEEN ? AND ?"
        ]
        params: List[Any] = [project_id, min_ts, max_ts]

        # Optional folder filter
        if folder_id is not None:
            where_parts.append("folder_id = ?")
            params.append(folder_id)

        # Optional exclusion list
        if exclude_photo_ids:
            placeholders = ','.join('?' * len(exclude_photo_ids))
            where_parts.append(f"id NOT IN ({placeholders})")
            params.extend(exclude_photo_ids)

        where_clause = " AND ".join(where_parts)

        return self.find_all(
            where_clause=where_clause,
            params=tuple(params),
            order_by="created_ts ASC"
        )

    def upsert(self,
               path: str,
               folder_id: int,
               project_id: int,
               size_kb: Optional[float] = None,
               modified: Optional[str] = None,
               width: Optional[int] = None,
               height: Optional[int] = None,
               date_taken: Optional[str] = None,
               tags: Optional[str] = None,
               created_ts: Optional[int] = None,
               created_date: Optional[str] = None,
               created_year: Optional[int] = None,
               gps_latitude: Optional[float] = None,
               gps_longitude: Optional[float] = None,
               image_content_hash: Optional[str] = None) -> int:
        """
        Insert or update photo metadata for a project.

        Args:
            path: Full file path
            folder_id: Folder ID
            project_id: Project ID
            size_kb: File size in KB
            modified: Last modified timestamp
            width: Image width in pixels
            height: Image height in pixels
            date_taken: EXIF date taken
            tags: Comma-separated tags
            created_ts: Unix timestamp for date hierarchy (BUG FIX #7)
            created_date: YYYY-MM-DD format for date queries (BUG FIX #7)
            created_year: Year for date grouping (BUG FIX #7)
            gps_latitude: GPS latitude in decimal degrees (LONG-TERM FIX 2026-01-08)
            gps_longitude: GPS longitude in decimal degrees (LONG-TERM FIX 2026-01-08)
            image_content_hash: Perceptual hash (dHash) for pixel-based staleness detection (v9.3.0)

        Returns:
            Photo ID (newly inserted or existing)
        """
        import time

        # Normalize path for consistent storage (prevents duplicates on Windows)
        normalized_path = self._normalize_path(path)

        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # DEFENSIVE FALLBACK: GPS/hash columns should be added by migration v9.2.0/v9.3.0 at app startup.
        # This fallback only triggers if migration didn't run (shouldn't happen in normal use).
        with self.connection() as conn:
            cur = conn.cursor()
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            missing_cols = []
            if 'gps_latitude' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_latitude REAL")
                missing_cols.append('gps_latitude')
            if 'gps_longitude' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_longitude REAL")
                missing_cols.append('gps_longitude')
            if 'location_name' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN location_name TEXT")
                missing_cols.append('location_name')
            if 'image_content_hash' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN image_content_hash TEXT")
                missing_cols.append('image_content_hash')
            if missing_cols:
                self.logger.warning(f"[PhotoRepository] Defensive fallback: added missing columns {missing_cols} - check migration system")
                conn.commit()

        # BUG FIX #7: Include created_ts, created_date, created_year for date hierarchy queries
        # LONG-TERM FIX (2026-01-08): Include gps_latitude, gps_longitude for Locations section
        # v9.3.0: Include image_content_hash for pixel-based embedding staleness detection
        sql = """
            INSERT INTO photo_metadata
                (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, updated_at,
                 created_ts, created_date, created_year, gps_latitude, gps_longitude, image_content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, project_id) DO UPDATE SET
                folder_id = excluded.folder_id,
                size_kb = excluded.size_kb,
                modified = excluded.modified,
                width = excluded.width,
                height = excluded.height,
                date_taken = excluded.date_taken,
                tags = excluded.tags,
                updated_at = excluded.updated_at,
                created_ts = excluded.created_ts,
                created_date = excluded.created_date,
                created_year = excluded.created_year,
                gps_latitude = COALESCE(excluded.gps_latitude, photo_metadata.gps_latitude),
                gps_longitude = COALESCE(excluded.gps_longitude, photo_metadata.gps_longitude),
                image_content_hash = COALESCE(excluded.image_content_hash, photo_metadata.image_content_hash)
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (normalized_path, folder_id, project_id, size_kb, modified, width, height,
                            date_taken, tags, now, created_ts, created_date, created_year, gps_latitude, gps_longitude, image_content_hash))
            conn.commit()

            # Get the ID of the inserted/updated row
            cur.execute("SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?", (normalized_path, project_id))
            result = cur.fetchone()
            photo_id = result['id'] if result else None

        self.logger.debug(f"Upserted photo: {normalized_path} (id={photo_id}, project={project_id}, GPS=({gps_latitude}, {gps_longitude}), hash={image_content_hash[:8] if image_content_hash else None}...)")
        return photo_id

    def bulk_upsert(self, rows: List[tuple], project_id: int) -> int:
        """
        Bulk insert or update multiple photos for a project.

        Args:
            rows: List of tuples: (path, folder_id, size_kb, modified, width, height, date_taken, tags,
                                   created_ts, created_date, created_year, gps_latitude, gps_longitude,
                                   image_content_hash)
            project_id: Project ID

        Returns:
            Number of rows affected
        """
        if not rows:
            return 0

        import time
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # DEFENSIVE FALLBACK: GPS/hash columns should be added by migration v9.2.0/v9.3.0 at app startup.
        # This fallback only triggers if migration didn't run (shouldn't happen in normal use).
        with self.connection() as conn:
            cur = conn.cursor()
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            missing_cols = []
            if 'gps_latitude' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_latitude REAL")
                missing_cols.append('gps_latitude')
            if 'gps_longitude' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_longitude REAL")
                missing_cols.append('gps_longitude')
            if 'location_name' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN location_name TEXT")
                missing_cols.append('location_name')
            if 'image_content_hash' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN image_content_hash TEXT")
                missing_cols.append('image_content_hash')
            if missing_cols:
                self.logger.warning(f"[PhotoRepository] Defensive fallback: added missing columns {missing_cols} - check migration system")
                conn.commit()

        # Normalize paths and add project_id + updated_at timestamp to each row
        rows_normalized = []
        for row in rows:
            # BUG FIX #7 + LONG-TERM FIX (2026-01-08) + v9.3.0: Unpack with created_*, GPS, and content hash
            # Input: (path, folder_id, size_kb, modified, width, height, date_taken, tags,
            #         created_ts, created_date, created_year, gps_latitude, gps_longitude, image_content_hash)
            path = row[0]
            normalized_path = self._normalize_path(path)
            # Rebuild tuple with normalized path and project_id
            # Output: (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags,
            #          updated_at, created_ts, created_date, created_year, gps_latitude, gps_longitude, image_content_hash)
            normalized_row = (normalized_path, row[1], project_id) + row[2:8] + (now,) + row[8:]
            rows_normalized.append(normalized_row)

        rows_with_timestamp = rows_normalized

        # BUG FIX #7: Include created_ts, created_date, created_year in INSERT
        # LONG-TERM FIX (2026-01-08): Include gps_latitude, gps_longitude for Locations section
        # v9.3.0: Include image_content_hash for pixel-based embedding staleness detection
        sql = """
            INSERT INTO photo_metadata
                (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, updated_at,
                 created_ts, created_date, created_year, gps_latitude, gps_longitude, image_content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, project_id) DO UPDATE SET
                folder_id = excluded.folder_id,
                size_kb = excluded.size_kb,
                modified = excluded.modified,
                width = excluded.width,
                height = excluded.height,
                date_taken = excluded.date_taken,
                tags = excluded.tags,
                updated_at = excluded.updated_at,
                created_ts = excluded.created_ts,
                created_date = excluded.created_date,
                created_year = excluded.created_year,
                gps_latitude = COALESCE(excluded.gps_latitude, photo_metadata.gps_latitude),
                gps_longitude = COALESCE(excluded.gps_longitude, photo_metadata.gps_longitude),
                image_content_hash = COALESCE(excluded.image_content_hash, photo_metadata.image_content_hash)
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.executemany(sql, rows_with_timestamp)
            conn.commit()
            affected = cur.rowcount

        self.logger.info(f"Bulk upserted {affected} photos for project {project_id}")
        return affected

    def update_metadata_status(self, photo_id: int, status: str, fail_count: int = 0):
        """
        Update metadata extraction status.

        Args:
            photo_id: Photo ID
            status: Status string (pending, success, failed)
            fail_count: Number of failed attempts
        """
        sql = """
            UPDATE photo_metadata
            SET metadata_status = ?, metadata_fail_count = ?
            WHERE id = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (status, fail_count, photo_id))
            conn.commit()

        self.logger.debug(f"Updated metadata status for photo {photo_id}: {status}")

    def update_photo_hash(self, photo_id: int, file_hash: str):
        """
        Update file_hash for a photo (used during hash backfill).

        Args:
            photo_id: Photo ID
            file_hash: SHA256 hash string
        """
        sql = """
            UPDATE photo_metadata
            SET file_hash = ?
            WHERE id = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (file_hash, photo_id))
            conn.commit()

        self.logger.debug(f"Updated file_hash for photo {photo_id}")

    def get_missing_metadata(self, project_id: int, max_failures: int = 3, limit: Optional[int] = None) -> List[str]:
        """
        Get photos that need metadata extraction for a specific project.

        Args:
            project_id: Project ID to filter by
            max_failures: Maximum allowed failure count
            limit: Optional maximum number of results

        Returns:
            List of file paths needing metadata
        """
        sql = """
            SELECT path FROM photo_metadata
            WHERE project_id = ?
              AND (metadata_status = 'pending'
                   OR (metadata_status = 'failed' AND metadata_fail_count < ?))
            ORDER BY id ASC
        """

        if limit:
            sql += f" LIMIT {int(limit)}"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, max_failures))
            return [row['path'] for row in cur.fetchall()]

    def count_by_folder(self, folder_id: int, project_id: int) -> int:
        """
        Count photos in a specific folder within a project.

        Args:
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            Number of photos
        """
        return self.count(where_clause="folder_id = ? AND project_id = ?", params=(folder_id, project_id))

    def count_photos_in_project(self, project_id: int) -> int:
        """
        Count total photos in a project.

        Args:
            project_id: Project ID

        Returns:
            Number of photos in the project
        """
        return self.count(where_clause="project_id = ?", params=(project_id,))

    def search(self,
               query: str,
               project_id: int,
               limit: int = 100) -> List[Dict[str, Any]]:
        """
        Search photos by path or tags within a specific project.

        Args:
            query: Search query
            project_id: Project ID to filter by
            limit: Maximum results

        Returns:
            List of matching photos
        """
        pattern = f"%{query}%"

        return self.find_all(
            where_clause="project_id = ? AND (path LIKE ? OR tags LIKE ?)",
            params=(project_id, pattern, pattern),
            order_by="modified DESC",
            limit=limit
        )

    def get_statistics(self, project_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get database statistics for a specific project or globally.

        Args:
            project_id: Project ID to filter by. If None, returns global stats (use with caution).

        Returns:
            Dict with counts and aggregates
        """
        with self.connection(read_only=True) as conn:
            cur = conn.cursor()

            if project_id is not None:
                # Project-specific statistics
                cur.execute("SELECT COUNT(*) as total FROM photo_metadata WHERE project_id = ?", (project_id,))
                total = cur.fetchone()['total']

                cur.execute("""
                    SELECT metadata_status, COUNT(*) as count
                    FROM photo_metadata
                    WHERE project_id = ?
                    GROUP BY metadata_status
                """, (project_id,))
                by_status = {row['metadata_status']: row['count'] for row in cur.fetchall()}

                cur.execute("SELECT SUM(size_kb) as total_size FROM photo_metadata WHERE project_id = ?", (project_id,))
                total_size_kb = cur.fetchone()['total_size'] or 0
            else:
                # Global statistics (legacy behavior - use with caution)
                self.logger.warning("get_statistics called without project_id - returning global stats")
                cur.execute("SELECT COUNT(*) as total FROM photo_metadata")
                total = cur.fetchone()['total']

                cur.execute("""
                    SELECT metadata_status, COUNT(*) as count
                    FROM photo_metadata
                    GROUP BY metadata_status
                """)
                by_status = {row['metadata_status']: row['count'] for row in cur.fetchall()}

                cur.execute("SELECT SUM(size_kb) as total_size FROM photo_metadata")
                total_size_kb = cur.fetchone()['total_size'] or 0

            return {
                "total_photos": total,
                "by_status": by_status,
                "total_size_mb": round(total_size_kb / 1024, 2),
                "project_id": project_id
            }

    def delete_by_path(self, path: str, project_id: int) -> bool:
        """
        Delete a photo by file path within a specific project.

        Args:
            path: Full file path
            project_id: Project ID to ensure we only delete from this project

        Returns:
            True if deleted, False if not found
        """
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM photo_metadata WHERE path = ? AND project_id = ?",
                (path, project_id)
            )
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted photo from project {project_id}: {path}")
        else:
            self.logger.warning(f"Photo not found for deletion in project {project_id}: {path}")

        return deleted

    def delete_by_paths(self, paths: List[str], project_id: int) -> int:
        """
        Delete multiple photos by file paths within a specific project.

        Args:
            paths: List of file paths
            project_id: Project ID to ensure we only delete from this project

        Returns:
            Number of photos deleted
        """
        if not paths:
            return 0

        placeholders = ','.join('?' * len(paths))
        sql = f"DELETE FROM photo_metadata WHERE path IN ({placeholders}) AND project_id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*paths, project_id))
            conn.commit()
            deleted = cur.rowcount

        self.logger.info(f"Bulk deleted {deleted} photos from project {project_id}")
        return deleted

    def delete_by_folder(self, folder_id: int, project_id: int) -> int:
        """
        Delete all photos in a folder within a specific project.

        Args:
            folder_id: Folder ID
            project_id: Project ID to ensure we only delete from this project

        Returns:
            Number of photos deleted
        """
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM photo_metadata WHERE folder_id = ? AND project_id = ?",
                (folder_id, project_id)
            )
            conn.commit()
            deleted = cur.rowcount

        self.logger.info(f"Deleted {deleted} photos from folder {folder_id} in project {project_id}")
        return deleted

    def cleanup_duplicate_paths(self, project_id: int) -> int:
        """
        Clean up duplicate photo entries caused by path format differences within a project.

        Removes duplicates where paths differ only in slash direction (e.g.,
        'C:\\path\\photo.jpg' vs 'C:/path/photo.jpg'), keeping the entry
        with the lowest ID (oldest).

        Args:
            project_id: Project ID to limit cleanup to

        Returns:
            Number of duplicate entries removed
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Find all photo paths for this project only
            cur.execute(
                "SELECT id, path FROM photo_metadata WHERE project_id = ? ORDER BY id",
                (project_id,)
            )
            all_photos = cur.fetchall()

            # Build map of normalized_path -> list of (id, original_path)
            normalized_map = {}
            for row in all_photos:
                photo_id = row['id']
                path = row['path']
                normalized = self._normalize_path(path)

                if normalized not in normalized_map:
                    normalized_map[normalized] = []
                normalized_map[normalized].append((photo_id, path))

            # Find duplicates and collect IDs to delete
            ids_to_delete = []
            for normalized, entries in normalized_map.items():
                if len(entries) > 1:
                    # Sort by ID (keep oldest), delete the rest
                    entries_sorted = sorted(entries, key=lambda x: x[0])
                    keep_id, keep_path = entries_sorted[0]

                    # Mark duplicates for deletion
                    for dup_id, dup_path in entries_sorted[1:]:
                        ids_to_delete.append(dup_id)
                        self.logger.debug(f"Duplicate found: keeping ID={keep_id} '{keep_path}', removing ID={dup_id} '{dup_path}'")

            # Delete duplicates (already filtered by project via the SELECT)
            if ids_to_delete:
                placeholders = ','.join('?' * len(ids_to_delete))
                sql = f"DELETE FROM photo_metadata WHERE id IN ({placeholders})"
                cur.execute(sql, ids_to_delete)
                conn.commit()

            deleted_count = len(ids_to_delete)
            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} duplicate photo entries in project {project_id}")
            else:
                self.logger.info(f"No duplicate photo entries found in project {project_id}")

            return deleted_count

    def get_photos_needing_embeddings(
        self,
        project_id: int,
        model: str = "clip-vit-b32",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get photos that don't have embeddings yet.

        Uses efficient single SQL query with LEFT JOIN instead of N+1 queries.
        For 1000 photos, this reduces from 1001 queries to just 1 query.

        Args:
            project_id: Project ID to filter by
            model: Embedding model name (unused, kept for backward compatibility)
            limit: Optional maximum number of results

        Returns:
            List of photo metadata dicts that need embeddings
        """
        # Single efficient query using LEFT JOIN
        # Returns photos where no matching embedding exists
        # CRITICAL FIX: Check photo_embedding table (where EmbeddingService stores embeddings)
        # NOT semantic_embeddings (which is a separate system)
        query = """
            SELECT p.id, p.path, p.created_ts, p.folder_id, p.project_id,
                   p.width, p.height,
                   CAST(ROUND(COALESCE(p.size_kb, 0) * 1024) AS INTEGER) AS file_size,
                   p.date_taken
            FROM photo_metadata p
            LEFT JOIN photo_embedding pe
                ON p.id = pe.photo_id AND pe.embedding_type = 'visual_semantic'
            WHERE p.project_id = ?
                AND pe.photo_id IS NULL
            ORDER BY p.id ASC
        """
        params = [project_id]

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self.connection(read_only=True) as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
