# repository/stack_repository.py
# Version 01.00.00.00 dated 20260115
# Repository for media_stack and media_stack_member
#
# Part of the asset-centric duplicate management system.
# Manages:
# - media_stack: grouping containers (duplicate, near_duplicate, similar, burst)
# - media_stack_member: members with similarity scores and ranks
# - media_stack_meta: optional parameters for debugging/auditing

from typing import Optional, List, Dict, Any, Tuple
import json
from .base_repository import BaseRepository, DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


class StackRepository(BaseRepository):
    """
    StackRepository manages materialized groupings.

    Tables:
    - media_stack: grouping container
    - media_stack_member: junction table with scores
    - media_stack_meta: optional params (JSON)

    Responsibilities:
    - Create and manage stacks (duplicate, near_duplicate, similar, burst)
    - Add/remove stack members
    - Clear stacks for regeneration
    - Query stacks and members efficiently
    """

    def __init__(self, db: DatabaseConnection):
        """
        Initialize StackRepository.

        Args:
            db: DatabaseConnection instance
        """
        super().__init__(db)

    def _table_name(self) -> str:
        """Primary table name managed by this repository."""
        return "media_stack"

    # =========================================================================
    # STACK OPERATIONS
    # =========================================================================

    def create_stack(
        self,
        project_id: int,
        stack_type: str,
        representative_photo_id: Optional[int],
        rule_version: str = "1",
        created_by: str = "system",
        params_json: Optional[str] = None
    ) -> int:
        """
        Create stack and optional meta record.

        Args:
            project_id: Project ID
            stack_type: One of: duplicate, near_duplicate, similar, burst
            representative_photo_id: Representative photo ID for preview
            rule_version: Algorithm version (allows regeneration)
            created_by: Creator identifier (system, user, ml)
            params_json: Optional JSON string with parameters

        Returns:
            stack_id of newly created stack
        """
        # Validate stack_type
        valid_types = ['duplicate', 'near_duplicate', 'similar', 'burst']
        if stack_type not in valid_types:
            raise ValueError(f"Invalid stack_type '{stack_type}'. Must be one of: {valid_types}")

        with self._db_connection.get_connection(read_only=False) as conn:
            cur = conn.execute(
                """
                INSERT INTO media_stack
                (project_id, stack_type, representative_photo_id, rule_version, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, stack_type, representative_photo_id, rule_version, created_by)
            )
            stack_id = int(cur.lastrowid)

            # Optionally store parameters for debugging/auditing
            if params_json is not None:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO media_stack_meta (stack_id, project_id, params_json)
                    VALUES (?, ?, ?)
                    """,
                    (stack_id, project_id, params_json)
                )

            conn.commit()

        self.logger.debug(f"Created {stack_type} stack {stack_id} (rule v{rule_version})")
        return stack_id

    def get_stack_by_id(self, project_id: int, stack_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve stack by stack_id.

        Args:
            project_id: Project ID
            stack_id: Stack ID

        Returns:
            Stack dictionary or None if not found
        """
        sql = """
            SELECT stack_id, project_id, stack_type, representative_photo_id, rule_version, created_by, created_at
            FROM media_stack
            WHERE project_id = ? AND stack_id = ?
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, stack_id))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_stack_by_photo_id(self, project_id: int, photo_id: int) -> Optional[Dict[str, Any]]:
        """
        Get stack information for a photo if it belongs to one.

        Args:
            project_id: Project ID
            photo_id: Photo ID

        Returns:
            Stack dictionary or None if photo is not in a stack
        """
        sql = """
            SELECT s.stack_id, s.project_id, s.stack_type, s.representative_photo_id,
                   s.rule_version, s.created_by, s.created_at
            FROM media_stack s
            INNER JOIN media_stack_member m ON s.stack_id = m.stack_id AND s.project_id = m.project_id
            WHERE s.project_id = ? AND m.photo_id = ?
            LIMIT 1
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, photo_id))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_stacks(
        self,
        project_id: int,
        stack_type: Optional[str] = None,
        limit: int = 200,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        List stacks for a project, optionally filtered by type.

        Args:
            project_id: Project ID
            stack_type: Optional filter by stack type
            limit: Maximum number of stacks to return
            offset: Number of stacks to skip (for pagination)

        Returns:
            List of stack dictionaries ordered by created_at DESC
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            if stack_type:
                cur = conn.execute(
                    """
                    SELECT stack_id, stack_type, representative_photo_id, rule_version, created_by, created_at
                    FROM media_stack
                    WHERE project_id = ? AND stack_type = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (project_id, stack_type, limit, offset)
                )
            else:
                cur = conn.execute(
                    """
                    SELECT stack_id, stack_type, representative_photo_id, rule_version, created_by, created_at
                    FROM media_stack
                    WHERE project_id = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (project_id, limit, offset)
                )
            return [dict(r) for r in cur.fetchall()]

    def count_stacks(self, project_id: int, stack_type: Optional[str] = None) -> int:
        """
        Count stacks for a project, optionally filtered by type.

        Args:
            project_id: Project ID
            stack_type: Optional filter by stack type

        Returns:
            Number of stacks
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            if stack_type:
                cur = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM media_stack
                    WHERE project_id = ? AND stack_type = ?
                    """,
                    (project_id, stack_type)
                )
            else:
                cur = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM media_stack
                    WHERE project_id = ?
                    """,
                    (project_id,)
                )
            row = cur.fetchone()
            return int(row["count"]) if row else 0

    def clear_stacks_by_type(
        self,
        project_id: int,
        stack_type: str,
        rule_version: Optional[str] = None
    ) -> int:
        """
        Delete stacks by type (and optionally rule_version).

        Use this when regenerating stacks for new thresholds or algorithm updates.
        CASCADE delete will automatically remove stack_member and stack_meta rows.

        Args:
            project_id: Project ID
            stack_type: Stack type to clear
            rule_version: Optional rule version filter

        Returns:
            Number of stacks deleted
        """
        with self._db_connection.get_connection(read_only=False) as conn:
            if rule_version is None:
                cur = conn.execute(
                    "DELETE FROM media_stack WHERE project_id = ? AND stack_type = ?",
                    (project_id, stack_type)
                )
            else:
                cur = conn.execute(
                    "DELETE FROM media_stack WHERE project_id = ? AND stack_type = ? AND rule_version = ?",
                    (project_id, stack_type, rule_version)
                )
            deleted_count = int(cur.rowcount)
            conn.commit()

        self.logger.info(f"Cleared {deleted_count} {stack_type} stacks (rule v{rule_version or 'all'})")
        return deleted_count

    def delete_stack(self, project_id: int, stack_id: int) -> bool:
        """
        Delete a specific stack.

        CASCADE delete will automatically remove:
        - All stack_member rows
        - All stack_meta rows

        Args:
            project_id: Project ID
            stack_id: Stack ID to delete

        Returns:
            True if stack was deleted, False if not found
        """
        with self._db_connection.get_connection(read_only=False) as conn:
            cur = conn.execute(
                "DELETE FROM media_stack WHERE project_id = ? AND stack_id = ?",
                (project_id, stack_id)
            )
            deleted = cur.rowcount > 0
            conn.commit()

        if deleted:
            self.logger.info(f"Deleted stack {stack_id} from project {project_id}")
        else:
            self.logger.warning(f"Stack {stack_id} not found in project {project_id}")

        return deleted

    # =========================================================================
    # STACK MEMBER OPERATIONS
    # =========================================================================

    def add_stack_member(
        self,
        project_id: int,
        stack_id: int,
        photo_id: int,
        similarity_score: Optional[float] = None,
        rank: Optional[int] = None
    ) -> None:
        """
        Add member to stack.

        Uses INSERT OR REPLACE for idempotency.

        Args:
            project_id: Project ID
            stack_id: Stack ID
            photo_id: Photo ID to add
            similarity_score: Optional similarity score (0.0 to 1.0)
            rank: Optional rank for ordering (lower = better)
        """
        with self._db_connection.get_connection(read_only=False) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO media_stack_member
                (stack_id, project_id, photo_id, similarity_score, rank)
                VALUES (?, ?, ?, ?, ?)
                """,
                (stack_id, project_id, photo_id, similarity_score, rank)
            )
            conn.commit()

        self.logger.debug(f"Added photo {photo_id} to stack {stack_id} (score={similarity_score}, rank={rank})")

    def add_stack_members_batch(
        self,
        project_id: int,
        stack_id: int,
        members: List[Dict[str, Any]]
    ) -> None:
        """
        Add multiple members to stack in a single transaction.

        Args:
            project_id: Project ID
            stack_id: Stack ID
            members: List of member dicts with keys: photo_id, similarity_score, rank
        """
        with self._db_connection.get_connection(read_only=False) as conn:
            for member in members:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO media_stack_member
                    (stack_id, project_id, photo_id, similarity_score, rank)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        stack_id,
                        project_id,
                        member.get("photo_id"),
                        member.get("similarity_score"),
                        member.get("rank")
                    )
                )
            conn.commit()

        self.logger.debug(f"Added {len(members)} members to stack {stack_id}")

    def list_stack_members(self, project_id: int, stack_id: int) -> List[Dict[str, Any]]:
        """
        List stack members ordered by rank then score.

        Smart ORDER BY: ranks nulls last, then orders by rank ASC and score DESC.

        Args:
            project_id: Project ID
            stack_id: Stack ID

        Returns:
            List of member dictionaries with photo_id, similarity_score, rank
        """
        sql = """
            SELECT photo_id, similarity_score, rank, created_at
            FROM media_stack_member
            WHERE project_id = ? AND stack_id = ?
            ORDER BY
                CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
                rank ASC,
                similarity_score DESC
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, stack_id))
            return [dict(r) for r in cur.fetchall()]

    def count_stack_members(self, project_id: int, stack_id: int) -> int:
        """
        Count members in a stack.

        Args:
            project_id: Project ID
            stack_id: Stack ID

        Returns:
            Number of members
        """
        sql = """
            SELECT COUNT(*) AS count
            FROM media_stack_member
            WHERE project_id = ? AND stack_id = ?
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, stack_id))
            row = cur.fetchone()
            return int(row["count"]) if row else 0

    def remove_stack_member(self, project_id: int, stack_id: int, photo_id: int) -> bool:
        """
        Remove member from stack.

        Args:
            project_id: Project ID
            stack_id: Stack ID
            photo_id: Photo ID to remove

        Returns:
            True if member was removed, False if not found
        """
        with self._db_connection.get_connection(read_only=False) as conn:
            cur = conn.execute(
                """
                DELETE FROM media_stack_member
                WHERE project_id = ? AND stack_id = ? AND photo_id = ?
                """,
                (project_id, stack_id, photo_id)
            )
            removed = cur.rowcount > 0
            conn.commit()

        if removed:
            self.logger.debug(f"Removed photo {photo_id} from stack {stack_id}")

        return removed

    def remove_stack_members(self, project_id: int, stack_id: int, photo_ids: List[int]) -> int:
        """
        Remove multiple members from stack (batch operation).

        Args:
            project_id: Project ID
            stack_id: Stack ID
            photo_ids: List of photo IDs to remove

        Returns:
            Number of members removed
        """
        if not photo_ids:
            return 0

        with self._db_connection.get_connection(read_only=False) as conn:
            # Build placeholders for IN clause
            placeholders = ','.join('?' * len(photo_ids))
            sql = f"""
                DELETE FROM media_stack_member
                WHERE project_id = ? AND stack_id = ? AND photo_id IN ({placeholders})
            """

            params = [project_id, stack_id] + list(photo_ids)
            cur = conn.execute(sql, params)
            removed_count = cur.rowcount
            conn.commit()

        if removed_count > 0:
            self.logger.info(f"Removed {removed_count} photos from stack {stack_id}")

        return removed_count

    # =========================================================================
    # STACK META OPERATIONS
    # =========================================================================

    def get_stack_meta(self, project_id: int, stack_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve stack metadata (parameters used to build stack).

        Args:
            project_id: Project ID
            stack_id: Stack ID

        Returns:
            Dictionary with params_json or None if not found
        """
        sql = """
            SELECT stack_id, project_id, params_json, created_at
            FROM media_stack_meta
            WHERE project_id = ? AND stack_id = ?
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, stack_id))
            row = cur.fetchone()
            if row:
                result = dict(row)
                # Parse JSON if present
                if result.get("params_json"):
                    try:
                        result["params"] = json.loads(result["params_json"])
                    except json.JSONDecodeError:
                        result["params"] = None
                return result
            return None

    # =========================================================================
    # HELPER QUERIES
    # =========================================================================

    def find_stacks_for_photo(self, project_id: int, photo_id: int) -> List[Dict[str, Any]]:
        """
        Find all stacks that contain a specific photo.

        Args:
            project_id: Project ID
            photo_id: Photo ID

        Returns:
            List of stack dictionaries containing this photo
        """
        sql = """
            SELECT s.stack_id, s.stack_type, s.representative_photo_id, s.rule_version,
                   s.created_by, s.created_at
            FROM media_stack s
            JOIN media_stack_member m ON m.stack_id = s.stack_id AND m.project_id = s.project_id
            WHERE s.project_id = ? AND m.photo_id = ?
            ORDER BY s.created_at DESC
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, photo_id))
            return [dict(r) for r in cur.fetchall()]

    def get_stack_with_member_count(self, project_id: int, stack_id: int) -> Optional[Dict[str, Any]]:
        """
        Get stack with member count in one query.

        Args:
            project_id: Project ID
            stack_id: Stack ID

        Returns:
            Stack dictionary with member_count field
        """
        sql = """
            SELECT s.stack_id, s.stack_type, s.representative_photo_id, s.rule_version,
                   s.created_by, s.created_at,
                   COUNT(m.photo_id) AS member_count
            FROM media_stack s
            LEFT JOIN media_stack_member m ON m.stack_id = s.stack_id AND m.project_id = s.project_id
            WHERE s.project_id = ? AND s.stack_id = ?
            GROUP BY s.stack_id
        """
        with self._db_connection.get_connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, stack_id))
            row = cur.fetchone()
            return dict(row) if row else None
