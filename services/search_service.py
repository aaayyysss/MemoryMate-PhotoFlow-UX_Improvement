# services/search_service.py
# Version 01.00.00.00 dated 20251105
# Comprehensive photo search and filtering service

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import os
import re

from logging_config import get_logger
from repository import PhotoRepository, FolderRepository

logger = get_logger(__name__)


@dataclass
class SearchCriteria:
    """
    Search criteria for photo searching.

    All criteria are combined with AND logic.
    Set to None to skip that criterion.
    """
    # Text search
    filename_pattern: Optional[str] = None  # Partial match, case-insensitive
    path_contains: Optional[str] = None     # Path contains string

    # Date search
    date_from: Optional[str] = None  # YYYY-MM-DD format
    date_to: Optional[str] = None    # YYYY-MM-DD format

    # Size search (in KB)
    size_min: Optional[float] = None
    size_max: Optional[float] = None

    # Dimension search (in pixels)
    width_min: Optional[int] = None
    width_max: Optional[int] = None
    height_min: Optional[int] = None
    height_max: Optional[int] = None

    # Orientation
    orientation: Optional[str] = None  # 'landscape', 'portrait', 'square'

    # EXIF search
    camera_model: Optional[str] = None
    has_gps: Optional[bool] = None

    # Tag search
    tags: List[str] = field(default_factory=list)  # All tags must match (AND)
    tags_any: List[str] = field(default_factory=list)  # Any tag matches (OR)

    # Folder search
    folder_id: Optional[int] = None
    folder_recursive: bool = False  # Include subfolders

    # Limit results
    limit: Optional[int] = None
    offset: int = 0

    # Sorting
    sort_by: str = "date_taken"  # date_taken, modified, filename, size, dimensions
    sort_order: str = "DESC"  # ASC or DESC


@dataclass
class SearchResult:
    """Results from a search operation."""
    paths: List[str]
    total_count: int
    filtered_count: int
    criteria_used: SearchCriteria
    execution_time_ms: float


class SearchService:
    """
    Comprehensive photo search and filtering service.

    Provides advanced search capabilities:
    - Filename and path searching
    - Date range filtering
    - Size and dimension filtering
    - EXIF data searching
    - Tag-based filtering
    - Folder-based searching (recursive)
    - Combined multi-criteria search
    """

    def __init__(self,
                 photo_repo: Optional[PhotoRepository] = None,
                 folder_repo: Optional[FolderRepository] = None):
        """
        Initialize search service.

        Args:
            photo_repo: PhotoRepository instance (creates new if None)
            folder_repo: FolderRepository instance (creates new if None)
        """
        self.photo_repo = photo_repo or PhotoRepository()
        self.folder_repo = folder_repo or FolderRepository()
        logger.info("SearchService initialized")

    def search(self, criteria: SearchCriteria) -> SearchResult:
        """
        Search photos based on criteria.

        Args:
            criteria: Search criteria

        Returns:
            SearchResult with matching photos and metadata
        """
        import time
        start_time = time.time()

        try:
            # Build SQL query based on criteria
            where_clauses = []
            params = []

            # Filename pattern
            if criteria.filename_pattern:
                where_clauses.append("path LIKE ?")
                params.append(f"%{criteria.filename_pattern}%")

            # Path contains
            if criteria.path_contains:
                where_clauses.append("path LIKE ?")
                params.append(f"%{criteria.path_contains}%")

            # Date range
            if criteria.date_from:
                where_clauses.append("date_taken >= ?")
                params.append(criteria.date_from)

            if criteria.date_to:
                where_clauses.append("date_taken <= ?")
                params.append(criteria.date_to)

            # Size range
            if criteria.size_min is not None:
                where_clauses.append("size_kb >= ?")
                params.append(criteria.size_min)

            if criteria.size_max is not None:
                where_clauses.append("size_kb <= ?")
                params.append(criteria.size_max)

            # Dimension range
            if criteria.width_min is not None:
                where_clauses.append("width >= ?")
                params.append(criteria.width_min)

            if criteria.width_max is not None:
                where_clauses.append("width <= ?")
                params.append(criteria.width_max)

            if criteria.height_min is not None:
                where_clauses.append("height >= ?")
                params.append(criteria.height_min)

            if criteria.height_max is not None:
                where_clauses.append("height <= ?")
                params.append(criteria.height_max)

            # Orientation
            if criteria.orientation:
                if criteria.orientation == 'landscape':
                    where_clauses.append("width > height")
                elif criteria.orientation == 'portrait':
                    where_clauses.append("height > width")
                elif criteria.orientation == 'square':
                    where_clauses.append("width = height")

            # Camera model - column not yet in schema; skip to avoid SQL error
            # TODO: Add camera_model column to photo_metadata when EXIF persistence is implemented

            # GPS data
            # P2-31 FIX: Consistent NULL checking for both coordinate fields
            if criteria.has_gps is not None:
                if criteria.has_gps:
                    # Both coordinates must be present for valid GPS data
                    where_clauses.append("(gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL)")
                else:
                    # P2-31 FIX: Changed OR to AND for consistent filtering
                    # Photos with partial GPS data (one NULL) should be considered as "no GPS"
                    where_clauses.append("(gps_latitude IS NULL AND gps_longitude IS NULL)")

            # Folder
            if criteria.folder_id is not None:
                if criteria.folder_recursive:
                    # Get all descendant folder IDs
                    folder_ids = self._get_folder_tree(criteria.folder_id)
                    placeholders = ','.join('?' * len(folder_ids))
                    where_clauses.append(f"folder_id IN ({placeholders})")
                    params.extend(folder_ids)
                else:
                    where_clauses.append("folder_id = ?")
                    params.append(criteria.folder_id)

            # P2-18 FIX: Integrate tag filtering into SQL query instead of post-processing
            # This eliminates N+1 query pattern where each result requires separate tag lookup
            tag_join_clause = ""
            tag_where_clause = ""

            if criteria.tags:
                # tags_all: AND logic - photo must have ALL specified tags
                # Use HAVING COUNT to ensure all tags are present
                tag_placeholders = ','.join('?' * len(criteria.tags))
                tag_join_clause = """
                    INNER JOIN photo_tags pt ON pm.id = pt.photo_id
                    INNER JOIN tags t ON pt.tag_id = t.id
                """
                tag_where_clause = f"t.name IN ({tag_placeholders})"
                params.extend(criteria.tags)
                # Group by photo_id and ensure count matches number of tags
                group_by_clause = "GROUP BY pm.id HAVING COUNT(DISTINCT t.name) = ?"
                params.append(len(criteria.tags))
            elif criteria.tags_any:
                # tags_any: OR logic - photo must have AT LEAST ONE of specified tags
                tag_placeholders = ','.join('?' * len(criteria.tags_any))
                tag_join_clause = """
                    INNER JOIN photo_tags pt ON pm.id = pt.photo_id
                    INNER JOIN tags t ON pt.tag_id = t.id
                """
                tag_where_clause = f"t.name IN ({tag_placeholders})"
                params.extend(criteria.tags_any)
                group_by_clause = "GROUP BY pm.id"
            else:
                group_by_clause = ""

            # Build WHERE clause
            if tag_where_clause:
                where_clauses.append(tag_where_clause)
            where_clause = " AND ".join(where_clauses) if where_clauses else None

            # Determine sort column
            sort_column = self._get_sort_column(criteria.sort_by)
            order_by = f"{sort_column} {criteria.sort_order}"

            # P2-18 FIX: Execute search with tag JOIN if needed
            logger.debug(f"Search WHERE: {where_clause}")
            logger.debug(f"Search params: {params}")

            if tag_join_clause:
                # Custom query with tag JOINs
                base_query = f"""
                    SELECT DISTINCT pm.path
                    FROM photo_metadata pm
                    {tag_join_clause}
                    {"WHERE " + where_clause if where_clause else ""}
                    {group_by_clause}
                    ORDER BY {order_by}
                """
                if criteria.limit:
                    base_query += f" LIMIT {criteria.limit}"
                if criteria.offset:
                    base_query += f" OFFSET {criteria.offset}"

                # Execute custom query directly
                with self.photo_repo.db._connect() as conn:
                    cursor = conn.execute(base_query, tuple(params))
                    results = [{'path': row[0]} for row in cursor.fetchall()]
            else:
                # Standard query without tags
                results = self.photo_repo.find_all(
                    where_clause=where_clause,
                    params=tuple(params) if params else None,
                    order_by=order_by,
                    limit=criteria.limit,
                    offset=criteria.offset
                )

            # Extract paths
            paths = [r['path'] for r in results if 'path' in r]

            # P2-18 FIX: Tag filtering now happens in SQL - no post-processing needed!

            # Get total count (before limit/offset)
            total_count = len(paths)

            execution_time = (time.time() - start_time) * 1000

            logger.info(f"Search completed: {len(paths)} results in {execution_time:.2f}ms")

            return SearchResult(
                paths=paths,
                total_count=total_count,
                filtered_count=len(paths),
                criteria_used=criteria,
                execution_time_ms=execution_time
            )

        except Exception as e:
            logger.error(f"Search failed: {e}")
            import traceback
            logger.debug(f"Full traceback:\n{traceback.format_exc()}")
            return SearchResult(
                paths=[],
                total_count=0,
                filtered_count=0,
                criteria_used=criteria,
                execution_time_ms=(time.time() - start_time) * 1000
            )

    def quick_search(self, query: str, limit: int = 100) -> List[str]:
        """
        Quick search for photos by filename.

        Convenience method for simple filename searches.

        Args:
            query: Search query (matches filename)
            limit: Maximum results

        Returns:
            List of matching photo paths
        """
        criteria = SearchCriteria(
            filename_pattern=query,
            limit=limit,
            sort_by="filename",
            sort_order="ASC"
        )
        result = self.search(criteria)
        return result.paths

    def search_by_date_range(self,
                            date_from: str,
                            date_to: str,
                            limit: Optional[int] = None) -> List[str]:
        """
        Search photos by date range.

        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            limit: Optional result limit

        Returns:
            List of matching photo paths
        """
        criteria = SearchCriteria(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            sort_by="date_taken",
            sort_order="ASC"
        )
        result = self.search(criteria)
        return result.paths

    def search_by_dimensions(self,
                            width_range: Optional[Tuple[int, int]] = None,
                            height_range: Optional[Tuple[int, int]] = None,
                            orientation: Optional[str] = None,
                            limit: Optional[int] = None) -> List[str]:
        """
        Search photos by dimensions.

        Args:
            width_range: (min_width, max_width) or None
            height_range: (min_height, max_height) or None
            orientation: 'landscape', 'portrait', 'square', or None
            limit: Optional result limit

        Returns:
            List of matching photo paths
        """
        criteria = SearchCriteria(
            width_min=width_range[0] if width_range else None,
            width_max=width_range[1] if width_range else None,
            height_min=height_range[0] if height_range else None,
            height_max=height_range[1] if height_range else None,
            orientation=orientation,
            limit=limit
        )
        result = self.search(criteria)
        return result.paths

    def search_by_camera(self, camera_model: str, limit: Optional[int] = None) -> List[str]:
        """
        Search photos by camera model.

        Args:
            camera_model: Camera model (partial match)
            limit: Optional result limit

        Returns:
            List of matching photo paths
        """
        criteria = SearchCriteria(
            camera_model=camera_model,
            limit=limit
        )
        result = self.search(criteria)
        return result.paths

    def search_with_gps(self, limit: Optional[int] = None) -> List[str]:
        """
        Search photos that have GPS coordinates.

        Args:
            limit: Optional result limit

        Returns:
            List of photo paths with GPS data
        """
        criteria = SearchCriteria(
            has_gps=True,
            limit=limit
        )
        result = self.search(criteria)
        return result.paths

    def _get_folder_tree(self, folder_id: int) -> List[int]:
        """
        Get folder ID and all descendant folder IDs.

        Args:
            folder_id: Root folder ID

        Returns:
            List of folder IDs including root and all descendants
        """
        # Start with the folder itself
        folder_ids = [folder_id]

        # Recursively get subfolders
        def get_children(parent_id: int):
            children = self.folder_repo.get_children(parent_id)
            for child in children:
                child_id = child.get('id')
                if child_id and child_id not in folder_ids:
                    folder_ids.append(child_id)
                    get_children(child_id)

        get_children(folder_id)
        return folder_ids

    def _filter_by_tags(self,
                       paths: List[str],
                       tags_all: List[str],
                       tags_any: List[str]) -> List[str]:
        """
        Filter paths by tag requirements.

        Args:
            paths: Paths to filter
            tags_all: All these tags must be present (AND)
            tags_any: Any of these tags must be present (OR)

        Returns:
            Filtered list of paths
        """
        if not tags_all and not tags_any:
            return paths

        # Use TagService for tag filtering (replaced ReferenceDB usage)
        from .tag_service import get_tag_service
        tag_service = get_tag_service()

        filtered_paths = []
        for path in paths:
            photo_tags = tag_service.get_tags_for_path(path)

            # Check tags_all (AND logic)
            if tags_all:
                if not all(tag in photo_tags for tag in tags_all):
                    continue

            # Check tags_any (OR logic)
            if tags_any:
                if not any(tag in photo_tags for tag in tags_any):
                    continue

            filtered_paths.append(path)

        return filtered_paths

    def _get_sort_column(self, sort_by: str) -> str:
        """
        Map sort_by string to database column name.

        Args:
            sort_by: Sort field name

        Returns:
            Database column name
        """
        sort_mapping = {
            "filename": "path",
            "date_taken": "date_taken",
            "modified": "modified",
            "size": "size_kb",
            "dimensions": "width * height",
            "width": "width",
            "height": "height"
        }
        return sort_mapping.get(sort_by, "date_taken")

    def get_search_suggestions(self, query: str, limit: int = 10) -> Dict[str, List[str]]:
        """
        Get search suggestions based on partial query.

        Args:
            query: Partial search query
            limit: Maximum suggestions per category

        Returns:
            Dictionary with suggestions by category
        """
        suggestions = {
            "filenames": [],
            "folders": [],
            "tags": []
        }

        try:
            # Filename suggestions
            criteria = SearchCriteria(
                filename_pattern=query,
                limit=limit,
                sort_by="filename"
            )
            result = self.search(criteria)
            suggestions["filenames"] = [os.path.basename(p) for p in result.paths[:limit]]

            # Folder suggestions
            folders = self.folder_repo.find_all(
                where_clause="name LIKE ?",
                params=(f"%{query}%",),
                limit=limit
            )
            suggestions["folders"] = [f.get('name', '') for f in folders if f.get('name')]

            # Tag suggestions (using TagService instead of ReferenceDB)
            from .tag_service import get_tag_service
            tag_service = get_tag_service()
            all_tags = tag_service.get_all_tags()
            matching_tags = [tag for tag in all_tags if query.lower() in tag.lower()]
            suggestions["tags"] = matching_tags[:limit]

        except Exception as e:
            logger.error(f"Failed to get search suggestions: {e}")

        return suggestions
