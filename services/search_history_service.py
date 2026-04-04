"""
SearchHistoryService - Track and Manage Search History

Version: 1.0.0
Date: 2026-01-01

Service for tracking search history, saving searches, and providing quick recall.

Features:
- Record all searches (semantic, traditional, multi-modal)
- Track execution time and results
- Save frequently used searches
- Quick recall from history
- Search analytics

Usage:
    from services.search_history_service import get_search_history_service

    service = get_search_history_service()

    # Record a search
    search_id = service.record_search(
        query_type='semantic_text',
        query_text='sunset beach',
        result_count=42,
        top_photo_ids=[1, 2, 3, ...],
        execution_time_ms=235.6
    )

    # Get recent searches
    recent = service.get_recent_searches(limit=10)

    # Save a search
    service.save_search('Beach photos', 'sunset beach', filters={...})
"""

import json
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SearchRecord:
    """A single search history record."""
    search_id: int
    query_type: str
    query_text: Optional[str]
    query_image_path: Optional[str]
    result_count: int
    top_photo_ids: List[int]
    filters: Optional[Dict[str, Any]]
    created_at: str
    execution_time_ms: float
    model_id: Optional[int]


@dataclass
class SavedSearch:
    """A user-saved search."""
    saved_search_id: int
    name: str
    description: Optional[str]
    query_type: str
    query_text: Optional[str]
    query_image_path: Optional[str]
    filters: Optional[Dict[str, Any]]
    created_at: str
    last_used_at: Optional[str]
    use_count: int


class SearchHistoryService:
    """
    Service for managing search history.

    Tracks all searches and provides quick recall functionality.
    """

    def __init__(self, db_connection: Optional[DatabaseConnection] = None):
        """
        Initialize search history service.

        Args:
            db_connection: Optional database connection
        """
        self.db = db_connection or DatabaseConnection()
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure search history tables exist."""
        try:
            with self.db.get_connection() as conn:
                # Check if tables exist
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name IN ('search_history', 'saved_search')
                """)
                existing = {row['name'] for row in cursor.fetchall()}

                if 'search_history' not in existing or 'saved_search' not in existing:
                    # Run migration
                    logger.info("[SearchHistory] Creating search history tables...")
                    from pathlib import Path
                    migration_path = Path(__file__).parent.parent / 'migrations' / 'migration_v7_search_history.sql'

                    if migration_path.exists():
                        with open(migration_path, 'r') as f:
                            sql = f.read()
                        conn.executescript(sql)
                        conn.commit()
                        logger.info("[SearchHistory] Tables created successfully")
                    else:
                        logger.warning(f"[SearchHistory] Migration file not found: {migration_path}")

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to ensure tables: {e}")

    def record_search(self,
                     query_type: str,
                     query_text: Optional[str] = None,
                     query_image_path: Optional[str] = None,
                     result_count: int = 0,
                     top_photo_ids: Optional[List[int]] = None,
                     filters: Optional[Dict[str, Any]] = None,
                     execution_time_ms: float = 0.0,
                     model_id: Optional[int] = None) -> int:
        """
        Record a search in history.

        Args:
            query_type: 'semantic_text', 'semantic_image', 'semantic_multi', 'traditional'
            query_text: Search query text
            query_image_path: Path to query image
            result_count: Number of results found
            top_photo_ids: List of top photo IDs (up to 10)
            filters: Dictionary of filter criteria
            execution_time_ms: Search execution time in milliseconds
            model_id: Model ID used for search

        Returns:
            int: Search ID
        """
        try:
            # Limit to top 10 photo IDs
            if top_photo_ids and len(top_photo_ids) > 10:
                top_photo_ids = top_photo_ids[:10]

            # Serialize to JSON
            top_photo_ids_json = json.dumps(top_photo_ids) if top_photo_ids else None
            filters_json = json.dumps(filters) if filters else None

            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO search_history (
                        query_type, query_text, query_image_path,
                        result_count, top_photo_ids, filters_json,
                        execution_time_ms, model_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    query_type, query_text, query_image_path,
                    result_count, top_photo_ids_json, filters_json,
                    execution_time_ms, model_id
                ))

                search_id = cursor.lastrowid
                conn.commit()

                logger.debug(
                    f"[SearchHistory] Recorded search {search_id}: "
                    f"{query_type}, {result_count} results"
                )

                return search_id

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to record search: {e}")
            return -1

    def get_recent_searches(self, limit: int = 20, query_type: Optional[str] = None) -> List[SearchRecord]:
        """
        Get recent searches.

        Args:
            limit: Maximum number of searches to return
            query_type: Optional filter by query type

        Returns:
            List of SearchRecord objects
        """
        try:
            with self.db.get_connection() as conn:
                if query_type:
                    cursor = conn.execute("""
                        SELECT search_id, query_type, query_text, query_image_path,
                               result_count, top_photo_ids, filters_json,
                               created_at, execution_time_ms, model_id
                        FROM search_history
                        WHERE query_type = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (query_type, limit))
                else:
                    cursor = conn.execute("""
                        SELECT search_id, query_type, query_text, query_image_path,
                               result_count, top_photo_ids, filters_json,
                               created_at, execution_time_ms, model_id
                        FROM search_history
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (limit,))

                searches = []
                for row in cursor.fetchall():
                    # Parse JSON
                    top_photo_ids = json.loads(row[5]) if row[5] else []
                    filters = json.loads(row[6]) if row[6] else None

                    searches.append(SearchRecord(
                        search_id=row[0],
                        query_type=row[1],
                        query_text=row[2],
                        query_image_path=row[3],
                        result_count=row[4],
                        top_photo_ids=top_photo_ids,
                        filters=filters,
                        created_at=row[7],
                        execution_time_ms=row[8],
                        model_id=row[9]
                    ))

                return searches

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to get recent searches: {e}")
            return []

    def search_history(self, keyword: str, limit: int = 10) -> List[SearchRecord]:
        """
        Search through history by keyword.

        Args:
            keyword: Keyword to search for in query text
            limit: Maximum results

        Returns:
            List of matching SearchRecord objects
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT search_id, query_type, query_text, query_image_path,
                           result_count, top_photo_ids, filters_json,
                           created_at, execution_time_ms, model_id
                    FROM search_history
                    WHERE query_text LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (f'%{keyword}%', limit))

                searches = []
                for row in cursor.fetchall():
                    top_photo_ids = json.loads(row[5]) if row[5] else []
                    filters = json.loads(row[6]) if row[6] else None

                    searches.append(SearchRecord(
                        search_id=row[0],
                        query_type=row[1],
                        query_text=row[2],
                        query_image_path=row[3],
                        result_count=row[4],
                        top_photo_ids=top_photo_ids,
                        filters=filters,
                        created_at=row[7],
                        execution_time_ms=row[8],
                        model_id=row[9]
                    ))

                return searches

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to search history: {e}")
            return []

    def save_search(self,
                   name: str,
                   query_type: str,
                   query_text: Optional[str] = None,
                   query_image_path: Optional[str] = None,
                   filters: Optional[Dict[str, Any]] = None,
                   description: Optional[str] = None) -> int:
        """
        Save a search for quick recall.

        Args:
            name: User-friendly name for the search
            query_type: Type of search
            query_text: Search query text
            query_image_path: Path to query image
            filters: Filter criteria
            description: Optional description

        Returns:
            int: Saved search ID
        """
        try:
            filters_json = json.dumps(filters) if filters else None

            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    INSERT OR REPLACE INTO saved_search (
                        name, description, query_type, query_text,
                        query_image_path, filters_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, description, query_type, query_text, query_image_path, filters_json))

                saved_search_id = cursor.lastrowid
                conn.commit()

                logger.info(f"[SearchHistory] Saved search '{name}' (ID: {saved_search_id})")
                return saved_search_id

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to save search: {e}")
            return -1

    def get_saved_searches(self) -> List[SavedSearch]:
        """
        Get all saved searches.

        Returns:
            List of SavedSearch objects
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT saved_search_id, name, description, query_type,
                           query_text, query_image_path, filters_json,
                           created_at, last_used_at, use_count
                    FROM saved_search
                    ORDER BY last_used_at DESC NULLS LAST, name ASC
                """)

                searches = []
                for row in cursor.fetchall():
                    filters = json.loads(row[6]) if row[6] else None

                    searches.append(SavedSearch(
                        saved_search_id=row[0],
                        name=row[1],
                        description=row[2],
                        query_type=row[3],
                        query_text=row[4],
                        query_image_path=row[5],
                        filters=filters,
                        created_at=row[7],
                        last_used_at=row[8],
                        use_count=row[9]
                    ))

                return searches

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to get saved searches: {e}")
            return []

    def use_saved_search(self, saved_search_id: int):
        """
        Mark a saved search as used (updates last_used_at and use_count).

        Args:
            saved_search_id: Saved search ID
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute("""
                    UPDATE saved_search
                    SET last_used_at = datetime('now'),
                        use_count = use_count + 1
                    WHERE saved_search_id = ?
                """, (saved_search_id,))
                conn.commit()

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to update saved search usage: {e}")

    def delete_saved_search(self, saved_search_id: int):
        """
        Delete a saved search.

        Args:
            saved_search_id: Saved search ID
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute("DELETE FROM saved_search WHERE saved_search_id = ?", (saved_search_id,))
                conn.commit()
                logger.info(f"[SearchHistory] Deleted saved search {saved_search_id}")

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to delete saved search: {e}")

    def clear_history(self, older_than_days: Optional[int] = None):
        """
        Clear search history.

        Args:
            older_than_days: If specified, only clear searches older than this many days
        """
        try:
            with self.db.get_connection() as conn:
                if older_than_days:
                    conn.execute("""
                        DELETE FROM search_history
                        WHERE julianday('now') - julianday(created_at) > ?
                    """, (older_than_days,))
                else:
                    conn.execute("DELETE FROM search_history")

                deleted = conn.total_changes
                conn.commit()
                logger.info(f"[SearchHistory] Cleared {deleted} history records")

        except Exception as e:
            logger.error(f"[SearchHistory] Failed to clear history: {e}")


# Singleton instance
_search_history_service = None


def get_search_history_service() -> SearchHistoryService:
    """
    Get singleton search history service instance.

    Returns:
        SearchHistoryService instance
    """
    global _search_history_service
    if _search_history_service is None:
        _search_history_service = SearchHistoryService()
    return _search_history_service
