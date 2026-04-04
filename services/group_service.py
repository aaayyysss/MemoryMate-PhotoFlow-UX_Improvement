# services/group_service.py
# People Groups service - CRUD + co-occurrence retrieval
#
# Manages user-defined groups of people (face clusters) and computes
# which photos contain all group members together (AND matching).
#
# Design:
#   - Groups are per-project, identified by person_groups.id
#   - Members are linked via branch_key (same as face_branch_reps)
#   - Match results are materialized in group_asset_matches for speed
#   - Live queries are available for small groups / interactive use

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GroupService:
    """
    Service for People Groups CRUD and co-occurrence retrieval.

    Thread safety:
        Each caller must pass its own ReferenceDB or sqlite3 connection.
        The service itself holds no mutable state.
    """

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def create_group(
        db,
        project_id: int,
        name: str,
        branch_keys: List[str],
        is_pinned: bool = False,
    ) -> int:
        """
        Create a new people group.

        Args:
            db: ReferenceDB instance
            project_id: Project this group belongs to
            name: Display name (e.g. "Family", "Ammar + Alya")
            branch_keys: List of face branch_keys (min 2)
            is_pinned: Pin group to top of list

        Returns:
            int: The new group ID

        Raises:
            ValueError: If fewer than 2 branch_keys are provided
        """
        if len(branch_keys) < 2:
            raise ValueError("A group requires at least 2 members")

        now = int(time.time())

        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO person_groups
                    (project_id, name, created_at, updated_at, last_used_at, is_pinned)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, name, now, now, now, int(is_pinned)),
            )
            group_id = cur.lastrowid

            member_rows = [
                (group_id, bk, now) for bk in branch_keys
            ]
            cur.executemany(
                "INSERT INTO person_group_members (group_id, branch_key, added_at) VALUES (?, ?, ?)",
                member_rows,
            )
            conn.commit()

        logger.info(
            "[GroupService] Created group %d '%s' with %d members (project %d)",
            group_id, name, len(branch_keys), project_id,
        )
        return group_id

    @staticmethod
    def update_group(
        db,
        group_id: int,
        name: Optional[str] = None,
        branch_keys: Optional[List[str]] = None,
        is_pinned: Optional[bool] = None,
    ) -> None:
        """
        Update an existing group's name, members, or pinned state.

        If branch_keys is provided, replaces all members (and clears cached matches).
        """
        now = int(time.time())

        with db.get_connection() as conn:
            cur = conn.cursor()

            updates = ["updated_at = ?"]
            params: list = [now]

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if is_pinned is not None:
                updates.append("is_pinned = ?")
                params.append(int(is_pinned))

            params.append(group_id)
            cur.execute(
                f"UPDATE person_groups SET {', '.join(updates)} WHERE id = ?",
                params,
            )

            if branch_keys is not None:
                if len(branch_keys) < 2:
                    raise ValueError("A group requires at least 2 members")

                # Replace members
                cur.execute("DELETE FROM person_group_members WHERE group_id = ?", (group_id,))
                member_rows = [(group_id, bk, now) for bk in branch_keys]
                cur.executemany(
                    "INSERT INTO person_group_members (group_id, branch_key, added_at) VALUES (?, ?, ?)",
                    member_rows,
                )
                # Clear stale match cache
                cur.execute("DELETE FROM group_asset_matches WHERE group_id = ?", (group_id,))

            conn.commit()

        logger.info("[GroupService] Updated group %d", group_id)

    @staticmethod
    def delete_group(db, group_id: int) -> None:
        """Soft-delete a group (sets is_deleted=1)."""
        now = int(time.time())
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE person_groups SET is_deleted = 1, updated_at = ? WHERE id = ?",
                (now, group_id),
            )
            conn.commit()
        logger.info("[GroupService] Soft-deleted group %d", group_id)

    @staticmethod
    def hard_delete_group(db, group_id: int) -> None:
        """Permanently delete a group and all its members + cached matches."""
        with db.get_connection() as conn:
            conn.execute("DELETE FROM group_asset_matches WHERE group_id = ?", (group_id,))
            conn.execute("DELETE FROM person_group_members WHERE group_id = ?", (group_id,))
            conn.execute("DELETE FROM person_groups WHERE id = ?", (group_id,))
            conn.commit()
        logger.info("[GroupService] Hard-deleted group %d", group_id)

    @staticmethod
    def get_groups(db, project_id: int, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        Get all groups for a project.

        Returns list of dicts with keys:
            id, name, created_at, updated_at, last_used_at, is_pinned,
            member_count, members (list of {branch_key, label})
        """
        with db.get_connection() as conn:
            cur = conn.cursor()

            deleted_filter = "" if include_deleted else "AND g.is_deleted = 0"
            cur.execute(
                f"""
                SELECT g.id, g.name, g.created_at, g.updated_at,
                       g.last_used_at, g.is_pinned,
                       COUNT(m.branch_key) AS member_count,
                       g.cover_asset_path
                FROM person_groups g
                LEFT JOIN person_group_members m ON m.group_id = g.id
                WHERE g.project_id = ? {deleted_filter}
                GROUP BY g.id
                ORDER BY g.is_pinned DESC, g.last_used_at DESC NULLS LAST, g.name ASC
                """,
                (project_id,),
            )
            groups = []
            for row in cur.fetchall():
                groups.append({
                    "id": row[0],
                    "name": row[1],
                    "created_at": row[2],
                    "updated_at": row[3],
                    "last_used_at": row[4],
                    "is_pinned": bool(row[5]),
                    "member_count": row[6],
                    "cover_asset_path": row[7],
                })

            # Load members for each group
            for g in groups:
                cur.execute(
                    """
                    SELECT m.branch_key,
                           COALESCE(r.label, m.branch_key) AS display_name,
                           r.rep_thumb_png
                    FROM person_group_members m
                    LEFT JOIN face_branch_reps r
                        ON r.branch_key = m.branch_key AND r.project_id = ?
                    WHERE m.group_id = ?
                    ORDER BY m.added_at ASC
                    """,
                    (project_id, g["id"]),
                )
                g["members"] = [
                    {"branch_key": r[0], "display_name": r[1], "rep_thumb_png": r[2]}
                    for r in cur.fetchall()
                ]

        return groups

    @staticmethod
    def get_group(db, group_id: int, project_id: int) -> Optional[Dict[str, Any]]:
        """Get a single group by ID with members."""
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, created_at, updated_at, last_used_at, is_pinned
                FROM person_groups
                WHERE id = ? AND project_id = ? AND is_deleted = 0
                """,
                (group_id, project_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            group = {
                "id": row[0],
                "name": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "last_used_at": row[4],
                "is_pinned": bool(row[5]),
            }

            cur.execute(
                """
                SELECT m.branch_key,
                       COALESCE(r.label, m.branch_key) AS display_name,
                       r.rep_thumb_png
                FROM person_group_members m
                LEFT JOIN face_branch_reps r
                    ON r.branch_key = m.branch_key AND r.project_id = ?
                WHERE m.group_id = ?
                ORDER BY m.added_at ASC
                """,
                (project_id, group_id),
            )
            group["members"] = [
                {"branch_key": r[0], "display_name": r[1], "rep_thumb_png": r[2]}
                for r in cur.fetchall()
            ]
            return group

    @staticmethod
    def touch_group(db, group_id: int) -> None:
        """Update last_used_at timestamp (called when user opens a group)."""
        now = int(time.time())
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE person_groups SET last_used_at = ? WHERE id = ?",
                (now, group_id),
            )
            conn.commit()

    @staticmethod
    def set_group_cover(db, group_id: int, asset_path: str) -> None:
        """Set a user-chosen cover photo for a group."""
        now = int(time.time())
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE person_groups SET cover_asset_path = ?, updated_at = ? WHERE id = ?",
                (asset_path, now, group_id),
            )
            conn.commit()
        logger.info("[GroupService] Set cover for group %d: %s", group_id, asset_path)

    @staticmethod
    def get_group_cover(db, project_id: int, group_id: int) -> Optional[str]:
        """Get the cover photo path for a group.

        Returns the user-chosen cover_asset_path if set, otherwise
        auto-derives from the first cached match photo (same_photo scope).
        """
        with db.get_connection() as conn:
            cur = conn.cursor()

            # Try user-chosen cover first
            cur.execute(
                "SELECT cover_asset_path FROM person_groups WHERE id = ?",
                (group_id,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]

            # Auto-derive: first cached match photo
            cur.execute(
                """
                SELECT pm.path
                FROM group_asset_matches gam
                JOIN photo_metadata pm ON pm.id = gam.photo_id AND pm.project_id = ?
                WHERE gam.group_id = ? AND gam.scope = 'same_photo'
                ORDER BY pm.created_ts DESC
                LIMIT 1
                """,
                (project_id, group_id),
            )
            row = cur.fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Co-occurrence queries (live, no materialization)
    # ------------------------------------------------------------------

    @staticmethod
    def query_same_photo_matches(
        db,
        project_id: int,
        group_id: int,
    ) -> List[int]:
        """
        Live AND-match: photos where ALL group members appear together.

        Uses project_images (not face_crops) because merge operations update
        project_images.branch_key but NOT face_crops.branch_key.
        Returns list of photo_metadata.id values.
        """
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                WITH members AS (
                    SELECT branch_key
                    FROM person_group_members
                    WHERE group_id = ?
                ),
                member_count AS (
                    SELECT COUNT(*) AS n FROM members
                )
                SELECT pm.id
                FROM project_images pi
                JOIN members m ON m.branch_key = pi.branch_key
                JOIN photo_metadata pm ON pm.path = pi.image_path AND pm.project_id = pi.project_id
                WHERE pi.project_id = ?
                GROUP BY pm.id
                HAVING COUNT(DISTINCT pi.branch_key) = (SELECT n FROM member_count)
                ORDER BY pm.created_ts DESC, pm.id DESC
                """,
                (group_id, project_id),
            )
            return [row[0] for row in cur.fetchall()]

    @staticmethod
    def query_same_photo_paths(
        db,
        project_id: int,
        group_id: int,
    ) -> List[str]:
        """
        Live AND-match returning file paths instead of IDs.
        Useful for grid display that works with paths.
        """
        with db.get_connection() as conn:
            cur = conn.cursor()
            # Uses project_images (not face_crops) because merge operations
            # update project_images.branch_key but NOT face_crops.branch_key.
            cur.execute(
                """
                WITH members AS (
                    SELECT branch_key
                    FROM person_group_members
                    WHERE group_id = ?
                ),
                member_count AS (
                    SELECT COUNT(*) AS n FROM members
                )
                SELECT pi.image_path
                FROM project_images pi
                JOIN members m ON m.branch_key = pi.branch_key
                WHERE pi.project_id = ?
                GROUP BY pi.image_path
                HAVING COUNT(DISTINCT pi.branch_key) = (SELECT n FROM member_count)
                ORDER BY pi.image_path
                """,
                (group_id, project_id),
            )
            return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Materialized match management
    # ------------------------------------------------------------------

    @staticmethod
    def compute_and_store_matches(
        db,
        project_id: int,
        group_id: int,
        scope: str = "same_photo",
    ) -> int:
        """
        Compute group matches and store in group_asset_matches.

        Returns number of matched photos.
        """
        now = int(time.time())
        photo_ids = GroupService.query_same_photo_matches(db, project_id, group_id)

        with db.get_connection() as conn:
            cur = conn.cursor()
            # Clear old matches for this group+scope
            cur.execute(
                "DELETE FROM group_asset_matches WHERE group_id = ? AND scope = ?",
                (group_id, scope),
            )
            if photo_ids:
                rows = [(group_id, scope, pid, now) for pid in photo_ids]
                cur.executemany(
                    "INSERT INTO group_asset_matches (group_id, scope, photo_id, computed_at) VALUES (?, ?, ?, ?)",
                    rows,
                )
            conn.commit()

        logger.info(
            "[GroupService] Computed %d matches for group %d scope=%s",
            len(photo_ids), group_id, scope,
        )
        return len(photo_ids)

    @staticmethod
    def get_cached_match_paths(
        db,
        project_id: int,
        group_id: int,
        scope: str = "same_photo",
    ) -> List[str]:
        """
        Get cached match paths from group_asset_matches.

        Falls back to live query if no cached results.
        """
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT pm.path
                FROM group_asset_matches gam
                JOIN photo_metadata pm ON pm.id = gam.photo_id AND pm.project_id = ?
                WHERE gam.group_id = ? AND gam.scope = ?
                ORDER BY pm.created_ts DESC, pm.id DESC
                """,
                (project_id, group_id, scope),
            )
            paths = [row[0] for row in cur.fetchall()]

        if not paths:
            # Fallback to live query
            paths = GroupService.query_same_photo_paths(db, project_id, group_id)

        return paths

    @staticmethod
    def get_cached_match_count(
        db,
        group_id: int,
        scope: str = "same_photo",
    ) -> int:
        """Get count of cached matches for a group."""
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM group_asset_matches WHERE group_id = ? AND scope = ?",
                (group_id, scope),
            )
            return cur.fetchone()[0]

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    @staticmethod
    def reindex_all_groups(db, project_id: int) -> Dict[int, int]:
        """
        Recompute matches for all active groups in a project.

        Returns dict of {group_id: match_count}.
        """
        groups = GroupService.get_groups(db, project_id)
        results = {}
        for g in groups:
            count = GroupService.compute_and_store_matches(
                db, project_id, g["id"], scope="same_photo"
            )
            results[g["id"]] = count
        logger.info(
            "[GroupService] Reindexed %d groups for project %d",
            len(results), project_id,
        )
        return results

    # ------------------------------------------------------------------
    # Smart name suggestion
    # ------------------------------------------------------------------

    @staticmethod
    def suggest_group_name(member_names: List[str]) -> str:
        """
        Generate a default group name from member display names.

        Examples:
            ["Ammar", "Alya"] -> "Ammar + Alya"
            ["Mom", "Dad", "Sis"] -> "Mom + Dad + Sis"
            More than 3: "Mom + Dad + 2 others"
        """
        if not member_names:
            return "New Group"
        if len(member_names) <= 3:
            return " + ".join(member_names)
        return f"{member_names[0]} + {member_names[1]} + {len(member_names) - 2} others"
