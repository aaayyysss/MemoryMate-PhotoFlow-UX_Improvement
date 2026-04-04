# services/people_group_service.py
# Version 2.0.0 dated 20260215
# Service for managing People Groups feature
#
# People Groups allow users to define groups of 2+ people and find photos
# where those people appear together (AND mode) or within a time window.
#
# Fix 2026-02-15: Rewritten to use canonical schema from schema.py
# - Removed non-existent columns: group_key, display_name, description, icon
# - Removed non-existent tables: person_group_state, person_group_matches
# - Uses group_asset_matches for match results
# - Uses name instead of display_name

"""
PeopleGroupService - Groups of people for finding photos together

Based on best practices from Google Photos, Apple Photos, and Adobe Lightroom.
Provides:
- Group CRUD operations (create, read, update, delete)
- Member management (add/remove people from groups)
- Match computation (Together AND, Event Window modes)
- Results stored in group_asset_matches table

Key Design Decisions:
1. Per-project scoped groups (not global)
2. Precomputed/cached results in group_asset_matches
3. Background computation with progress signals
"""

from __future__ import annotations
import json
import sqlite3
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from logging_config import get_logger

logger = get_logger(__name__)


class PeopleGroupService:
    """
    Service for managing people groups and computing group matches.

    Features:
    - Create/update/delete groups
    - Add/remove members from groups
    - Compute matches (Together AND mode)
    - Compute matches (Event Window mode)
    - Results cached in group_asset_matches table
    """

    def __init__(self, db):
        """
        Initialize PeopleGroupService.

        Args:
            db: ReferenceDB instance
        """
        self.db = db

    # =========================================================================
    # GROUP CRUD OPERATIONS
    # =========================================================================

    def create_group(
        self,
        project_id: int,
        name: str,
        member_branch_keys: List[str],
        display_name: Optional[str] = None,  # Alias for name (backwards compat)
        description: Optional[str] = None,   # Ignored (not in schema)
        icon: Optional[str] = None           # Ignored (not in schema)
    ) -> Dict[str, Any]:
        """
        Create a new people group.

        Args:
            project_id: Project ID
            name: User-visible name for the group
            member_branch_keys: List of branch_keys (face clusters) to include
            display_name: Alias for name (backwards compatibility)
            description: Ignored (not in canonical schema)
            icon: Ignored (not in canonical schema)

        Returns:
            Dict with group info including 'id'

        Raises:
            ValueError: If fewer than 2 members provided
        """
        # Support both 'name' and 'display_name' for backwards compat
        group_name = name or display_name or "New Group"

        if len(member_branch_keys) < 2:
            raise ValueError("A group must have at least 2 members")

        now = int(time.time())

        try:
            with self.db._connect() as conn:
                cur = conn.cursor()

                # Insert group using canonical schema columns
                cur.execute("""
                    INSERT INTO person_groups
                        (project_id, name, created_at, updated_at, last_used_at, is_pinned, is_deleted)
                    VALUES (?, ?, ?, ?, ?, 0, 0)
                """, (project_id, group_name, now, now, now))

                group_id = cur.lastrowid

                # Insert members (canonical schema: group_id, branch_key, added_at)
                for branch_key in member_branch_keys:
                    cur.execute("""
                        INSERT INTO person_group_members (group_id, branch_key, added_at)
                        VALUES (?, ?, ?)
                    """, (group_id, branch_key, now))

                conn.commit()

                logger.info(f"[PeopleGroupService] Created group '{group_name}' "
                           f"(id={group_id}) with {len(member_branch_keys)} members")

                return {
                    'id': group_id,
                    'name': group_name,
                    'display_name': group_name,  # Backwards compat alias
                    'member_count': len(member_branch_keys),
                    'members': member_branch_keys
                }

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to create group: {e}", exc_info=True)
            raise

    def get_group(self, project_id: int, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a single group by ID.

        Args:
            project_id: Project ID
            group_id: Group ID

        Returns:
            Group dict or None if not found
        """
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT
                        g.id, g.name, g.created_at, g.updated_at,
                        g.last_used_at, g.is_pinned,
                        COUNT(m.branch_key) as member_count
                    FROM person_groups g
                    LEFT JOIN person_group_members m ON g.id = m.group_id
                    WHERE g.project_id = ? AND g.id = ? AND g.is_deleted = 0
                    GROUP BY g.id
                """, (project_id, group_id))

                row = cur.fetchone()
                if not row:
                    return None

                # Get match count from group_asset_matches
                match_count_row = conn.execute("""
                    SELECT COUNT(*) FROM group_asset_matches
                    WHERE group_id = ?
                """, (group_id,)).fetchone()
                match_count = match_count_row[0] if match_count_row else 0

                # A group is stale if it has never been computed (last_used_at is NULL)
                # AND has no cached matches.  Having 0 matches after a successful
                # computation (last_used_at is set) is a valid result, not stale.
                last_used_at = row[4]
                is_stale = match_count == 0 and last_used_at is None

                return {
                    'id': row[0],
                    'name': row[1],
                    'display_name': row[1],  # Backwards compat alias
                    'created_at': row[2],
                    'updated_at': row[3],
                    'last_used_at': last_used_at,
                    'is_pinned': bool(row[5]),
                    'member_count': row[6],
                    'result_count': match_count,
                    'is_stale': is_stale
                }

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to get group {group_id}: {e}", exc_info=True)
            return None

    def get_all_groups(self, project_id: int, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        Get all groups for a project.

        Args:
            project_id: Project ID
            include_deleted: Include soft-deleted groups

        Returns:
            List of group dicts
        """
        try:
            with self.db._connect() as conn:
                deleted_clause = "" if include_deleted else "AND g.is_deleted = 0"

                cur = conn.execute(f"""
                    SELECT
                        g.id, g.name, g.created_at, g.updated_at,
                        g.last_used_at, g.is_pinned,
                        COUNT(m.branch_key) as member_count
                    FROM person_groups g
                    LEFT JOIN person_group_members m ON g.id = m.group_id
                    WHERE g.project_id = ? {deleted_clause}
                    GROUP BY g.id
                    ORDER BY g.is_pinned DESC, g.last_used_at DESC, g.name ASC
                """, (project_id,))

                groups = []
                for row in cur.fetchall():
                    group_id = row[0]

                    # Get match count from group_asset_matches
                    match_count_row = conn.execute("""
                        SELECT COUNT(*) FROM group_asset_matches
                        WHERE group_id = ?
                    """, (group_id,)).fetchone()
                    match_count = match_count_row[0] if match_count_row else 0

                    # A group is stale if it has never been computed (last_used_at is NULL)
                    # AND has no cached matches.  Having 0 matches after a successful
                    # computation (last_used_at is set) is a valid result, not stale.
                    last_used_at = row[4]
                    is_stale = match_count == 0 and last_used_at is None

                    # FIX 2026-03-14: When stale (never computed), report result_count
                    # as -1 so the UI shows "..." instead of "0 photos".  This follows
                    # the Google Photos / Apple Photos pattern where a group shows a
                    # loading indicator until its first computation completes.
                    # A group that HAS been computed (last_used_at is set) and has 0
                    # matches is a legitimate result and should display "0 photos".
                    display_count = -1 if is_stale else match_count

                    # Fetch top-3 member face thumbnail paths for GroupCard avatars
                    member_thumb_rows = conn.execute("""
                        SELECT r.rep_path
                        FROM person_group_members m
                        JOIN face_branch_reps r
                            ON r.branch_key = m.branch_key AND r.project_id = ?
                        WHERE m.group_id = ?
                        ORDER BY m.added_at ASC
                        LIMIT 3
                    """, (project_id, group_id)).fetchall()
                    member_rep_paths = [r[0] for r in member_thumb_rows if r[0]]

                    groups.append({
                        'id': group_id,
                        'name': row[1],
                        'display_name': row[1],  # Backwards compat alias
                        'created_at': row[2],
                        'updated_at': row[3],
                        'last_used_at': row[4],
                        'is_pinned': bool(row[5]),
                        'member_count': row[6],
                        'result_count': display_count,
                        'is_stale': is_stale,
                        'member_rep_paths': member_rep_paths,
                    })

                return groups

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to get groups: {e}", exc_info=True)
            return []

    def update_group(
        self,
        project_id: int,
        group_id: int,
        name: Optional[str] = None,
        display_name: Optional[str] = None,  # Alias for name (backwards compat)
        description: Optional[str] = None,   # Ignored (not in schema)
        icon: Optional[str] = None           # Ignored (not in schema)
    ) -> bool:
        """
        Update group metadata.

        Args:
            project_id: Project ID
            group_id: Group ID
            name: New name (or None to keep)
            display_name: Alias for name (backwards compat)
            description: Ignored (not in canonical schema)
            icon: Ignored (not in canonical schema)

        Returns:
            True if updated successfully
        """
        try:
            # Support both 'name' and 'display_name' for backwards compat
            group_name = name or display_name

            updates = []
            params = []

            if group_name is not None:
                updates.append("name = ?")
                params.append(group_name)

            if not updates:
                return True  # Nothing to update

            now = int(time.time())
            updates.append("updated_at = ?")
            params.append(now)
            params.extend([project_id, group_id])

            with self.db._connect() as conn:
                conn.execute(f"""
                    UPDATE person_groups
                    SET {', '.join(updates)}
                    WHERE project_id = ? AND id = ?
                """, params)
                conn.commit()

            logger.info(f"[PeopleGroupService] Updated group {group_id}")
            return True

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to update group: {e}", exc_info=True)
            return False

    def delete_group(self, project_id: int, group_id: int, soft_delete: bool = True) -> bool:
        """
        Delete a group.

        Args:
            project_id: Project ID
            group_id: Group ID
            soft_delete: If True, mark as deleted; if False, hard delete

        Returns:
            True if deleted successfully
        """
        try:
            now = int(time.time())

            with self.db._connect() as conn:
                if soft_delete:
                    conn.execute("""
                        UPDATE person_groups
                        SET is_deleted = 1, updated_at = ?
                        WHERE project_id = ? AND id = ?
                    """, (now, project_id, group_id))
                else:
                    # Hard delete cascades via foreign keys
                    conn.execute("DELETE FROM group_asset_matches WHERE group_id = ?", (group_id,))
                    conn.execute("DELETE FROM person_group_members WHERE group_id = ?", (group_id,))
                    conn.execute("""
                        DELETE FROM person_groups
                        WHERE project_id = ? AND id = ?
                    """, (project_id, group_id))

                conn.commit()

            logger.info(f"[PeopleGroupService] {'Soft' if soft_delete else 'Hard'} deleted group {group_id}")
            return True

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to delete group: {e}", exc_info=True)
            return False

    # =========================================================================
    # MEMBER MANAGEMENT
    # =========================================================================

    def get_group_members(self, project_id: int, group_id: int) -> List[Dict[str, Any]]:
        """
        Get all members of a group with their face cluster info.

        Args:
            project_id: Project ID
            group_id: Group ID

        Returns:
            List of member dicts with face cluster info
        """
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT
                        m.branch_key,
                        m.added_at,
                        COALESCE(f.label, m.branch_key) as display_name,
                        f.count as photo_count,
                        f.rep_path,
                        f.rep_thumb_png
                    FROM person_group_members m
                    LEFT JOIN face_branch_reps f
                        ON f.project_id = ? AND f.branch_key = m.branch_key
                    WHERE m.group_id = ?
                    ORDER BY display_name ASC
                """, (project_id, group_id))

                members = []
                for row in cur.fetchall():
                    members.append({
                        'branch_key': row[0],
                        'added_at': row[1],
                        'display_name': row[2],
                        'photo_count': row[3] or 0,
                        'rep_path': row[4],
                        'rep_thumb_png': row[5]
                    })

                return members

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to get members: {e}", exc_info=True)
            return []

    def add_member(self, project_id: int, group_id: int, branch_key: str) -> bool:
        """
        Add a person to a group.

        Args:
            project_id: Project ID
            group_id: Group ID
            branch_key: Face cluster branch_key to add

        Returns:
            True if added successfully
        """
        try:
            now = int(time.time())

            with self.db._connect() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO person_group_members (group_id, branch_key, added_at)
                    VALUES (?, ?, ?)
                """, (group_id, branch_key, now))

                # Clear cached matches since group composition changed
                conn.execute("DELETE FROM group_asset_matches WHERE group_id = ?", (group_id,))

                # Update group timestamp
                conn.execute("""
                    UPDATE person_groups SET updated_at = ? WHERE id = ?
                """, (now, group_id))

                conn.commit()

            logger.info(f"[PeopleGroupService] Added member {branch_key} to group {group_id}")
            return True

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to add member: {e}", exc_info=True)
            return False

    def remove_member(self, project_id: int, group_id: int, branch_key: str) -> bool:
        """
        Remove a person from a group.

        Args:
            project_id: Project ID
            group_id: Group ID
            branch_key: Face cluster branch_key to remove

        Returns:
            True if removed successfully
        """
        try:
            now = int(time.time())

            with self.db._connect() as conn:
                conn.execute("""
                    DELETE FROM person_group_members
                    WHERE group_id = ? AND branch_key = ?
                """, (group_id, branch_key))

                # Clear cached matches since group composition changed
                conn.execute("DELETE FROM group_asset_matches WHERE group_id = ?", (group_id,))

                # Update group timestamp
                conn.execute("""
                    UPDATE person_groups SET updated_at = ? WHERE id = ?
                """, (now, group_id))

                conn.commit()

            logger.info(f"[PeopleGroupService] Removed member {branch_key} from group {group_id}")
            return True

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to remove member: {e}", exc_info=True)
            return False

    # =========================================================================
    # MATCH COMPUTATION
    # =========================================================================

    def compute_together_matches(
        self,
        project_id: int,
        group_id: int,
        min_confidence: float = 0.5,
        include_videos: bool = False,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Compute 'together' matches: photos where ALL group members appear.

        This is the default AND mode - find photos containing all people in the group.

        Args:
            project_id: Project ID
            group_id: Group ID
            min_confidence: Minimum face detection confidence
            include_videos: Include video frames (future)
            progress_callback: Optional callback(current, total, message)

        Returns:
            Dict with computation results
        """
        start_time = time.time()
        max_retries = 3
        retry_delay = 2  # seconds, doubles on each retry

        for attempt in range(max_retries):
            try:
                with self.db._connect() as conn:
                    cur = conn.cursor()

                    # Get group members
                    cur.execute("""
                        SELECT branch_key FROM person_group_members
                        WHERE group_id = ?
                    """, (group_id,))
                    members = [row[0] for row in cur.fetchall()]

                    if len(members) < 2:
                        return {
                            'success': False,
                            'error': 'Group must have at least 2 members',
                            'match_count': 0
                        }

                    member_count = len(members)

                    if progress_callback:
                        progress_callback(0, 100, f"Finding photos with {member_count} people together...")

                    # Find photos where ALL members appear.
                    # Uses project_images (not face_crops) because merge operations
                    # update project_images.branch_key but NOT face_crops.branch_key.
                    placeholders = ','.join(['?'] * len(members))

                    cur.execute(f"""
                        SELECT
                            pi.image_path,
                            pm.id as photo_id,
                            COUNT(DISTINCT pi.branch_key) as person_count
                        FROM project_images pi
                        JOIN photo_metadata pm ON pm.path = pi.image_path AND pm.project_id = pi.project_id
                        WHERE pi.project_id = ?
                          AND pi.branch_key IN ({placeholders})
                        GROUP BY pi.image_path
                        HAVING COUNT(DISTINCT pi.branch_key) = ?
                    """, (project_id, *members, member_count))

                    matching_photos = cur.fetchall()

                    if progress_callback:
                        progress_callback(50, 100, f"Found {len(matching_photos)} matching photos")

                    # Clear previous matches for this group
                    cur.execute("""
                        DELETE FROM group_asset_matches
                        WHERE group_id = ? AND scope = 'same_photo'
                    """, (group_id,))

                    # Insert new matches
                    now = int(time.time())
                    match_count = 0
                    for image_path, photo_id, person_count in matching_photos:
                        cur.execute("""
                            INSERT INTO group_asset_matches
                                (group_id, scope, photo_id, computed_at)
                            VALUES (?, 'same_photo', ?, ?)
                        """, (group_id, photo_id, now))
                        match_count += 1

                    if progress_callback:
                        progress_callback(90, 100, f"Saved {match_count} matches")

                    # Update group last_used_at
                    cur.execute("""
                        UPDATE person_groups SET last_used_at = ? WHERE id = ?
                    """, (now, group_id))

                    conn.commit()

                    duration = time.time() - start_time

                    if progress_callback:
                        progress_callback(100, 100, f"Complete: {match_count} photos")

                    logger.info(f"[PeopleGroupService] Together matches for group {group_id}: "
                               f"{match_count} photos with {member_count} people in {duration:.2f}s")

                    return {
                        'success': True,
                        'match_count': match_count,
                        'member_count': member_count,
                        'duration_s': duration
                    }

            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    wait = retry_delay * (2 ** attempt)
                    logger.warning(
                        f"[PeopleGroupService] Database locked for group {group_id}, "
                        f"retrying in {wait}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait)
                    continue
                logger.error(f"[PeopleGroupService] Together match computation failed: {e}", exc_info=True)
                return {
                    'success': False,
                    'error': str(e),
                    'match_count': 0
                }

            except Exception as e:
                logger.error(f"[PeopleGroupService] Together match computation failed: {e}", exc_info=True)
                return {
                    'success': False,
                    'error': str(e),
                    'match_count': 0
                }

    def compute_event_window_matches(
        self,
        project_id: int,
        group_id: int,
        window_seconds: int = 30,
        min_confidence: float = 0.5,
        include_videos: bool = False,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Compute 'event_window' matches: photos taken within a time window
        where all group members appear somewhere in that window.

        Args:
            project_id: Project ID
            group_id: Group ID
            window_seconds: Time window in seconds (default 30s)
            min_confidence: Minimum face detection confidence
            include_videos: Include video frames (future)
            progress_callback: Optional callback(current, total, message)

        Returns:
            Dict with computation results
        """
        start_time = time.time()
        max_retries = 3
        retry_delay = 2  # seconds, doubles on each retry

        for attempt in range(max_retries):
            try:
                with self.db._connect() as conn:
                    cur = conn.cursor()

                    # Get group members
                    cur.execute("""
                        SELECT branch_key FROM person_group_members
                        WHERE group_id = ?
                    """, (group_id,))
                    members = [row[0] for row in cur.fetchall()]

                    if len(members) < 2:
                        return {
                            'success': False,
                            'error': 'Group must have at least 2 members',
                            'match_count': 0
                        }

                    member_count = len(members)

                    if progress_callback:
                        progress_callback(0, 100, f"Finding event windows with {member_count} people...")

                    # Get all photo timestamps for each member
                    placeholders = ','.join(['?'] * len(members))

                    cur.execute(f"""
                        SELECT
                            fc.branch_key,
                            pm.created_ts,
                            pm.id as photo_id,
                            pm.path
                        FROM face_crops fc
                        JOIN photo_metadata pm ON pm.path = fc.image_path AND pm.project_id = fc.project_id
                        WHERE fc.project_id = ?
                          AND fc.branch_key IN ({placeholders})
                          AND fc.confidence >= ?
                          AND pm.created_ts IS NOT NULL
                        ORDER BY pm.created_ts ASC
                    """, (project_id, *members, min_confidence))

                    events = cur.fetchall()

                    if not events:
                        return {
                            'success': True,
                            'match_count': 0,
                            'member_count': member_count,
                            'message': 'No photos with timestamps found'
                        }

                    if progress_callback:
                        progress_callback(20, 100, f"Analyzing {len(events)} photo events...")

                    # Find event windows where all members appear
                    matching_photos = set()

                    # Build person -> timestamp mapping
                    person_times: Dict[str, List[Tuple[int, int, str]]] = {m: [] for m in members}
                    for branch_key, ts, photo_id, path in events:
                        if ts is not None:
                            person_times[branch_key].append((ts, photo_id, path))

                    # Sort each person's timeline
                    for m in members:
                        person_times[m].sort(key=lambda x: x[0])

                    # Use the first member's timeline as anchor
                    if not person_times[members[0]]:
                        return {
                            'success': True,
                            'match_count': 0,
                            'member_count': member_count,
                            'message': 'No photos found for anchor person'
                        }

                    anchor_member = members[0]
                    other_members = members[1:]

                    for anchor_ts, anchor_photo_id, anchor_path in person_times[anchor_member]:
                        window_start = anchor_ts - window_seconds
                        window_end = anchor_ts + window_seconds

                        all_present = True
                        window_photos = [(anchor_photo_id, anchor_path)]

                        for other_member in other_members:
                            found_in_window = False
                            for other_ts, other_photo_id, other_path in person_times[other_member]:
                                if window_start <= other_ts <= window_end:
                                    found_in_window = True
                                    window_photos.append((other_photo_id, other_path))
                                    break

                            if not found_in_window:
                                all_present = False
                                break

                        if all_present:
                            for photo_id, path in window_photos:
                                matching_photos.add((photo_id, path))

                    if progress_callback:
                        progress_callback(70, 100, f"Found {len(matching_photos)} matching photos")

                    # Clear previous event_window matches
                    cur.execute("""
                        DELETE FROM group_asset_matches
                        WHERE group_id = ? AND scope = 'event_window'
                    """, (group_id,))

                    # Insert new matches
                    now = int(time.time())
                    match_count = 0
                    for photo_id, path in matching_photos:
                        cur.execute("""
                            INSERT OR IGNORE INTO group_asset_matches
                                (group_id, scope, photo_id, computed_at)
                            VALUES (?, 'event_window', ?, ?)
                        """, (group_id, photo_id, now))
                        match_count += 1

                    if progress_callback:
                        progress_callback(90, 100, f"Saved {match_count} matches")

                    # Update group last_used_at
                    cur.execute("""
                        UPDATE person_groups SET last_used_at = ? WHERE id = ?
                    """, (now, group_id))

                    conn.commit()

                    duration = time.time() - start_time

                    if progress_callback:
                        progress_callback(100, 100, f"Complete: {match_count} photos")

                    logger.info(f"[PeopleGroupService] Event window matches for group {group_id}: "
                               f"{match_count} photos within {window_seconds}s window in {duration:.2f}s")

                    return {
                        'success': True,
                        'match_count': match_count,
                        'member_count': member_count,
                        'duration_s': duration,
                        'window_seconds': window_seconds
                    }

            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    wait = retry_delay * (2 ** attempt)
                    logger.warning(
                        f"[PeopleGroupService] Database locked for group {group_id} (event_window), "
                        f"retrying in {wait}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait)
                    continue
                logger.error(f"[PeopleGroupService] Event window computation failed: {e}", exc_info=True)
                return {
                    'success': False,
                    'error': str(e),
                    'match_count': 0
                }

            except Exception as e:
                logger.error(f"[PeopleGroupService] Event window computation failed: {e}", exc_info=True)
                return {
                    'success': False,
                    'error': str(e),
                    'match_count': 0
                }

    def get_group_matches(
        self,
        project_id: int,
        group_id: int,
        match_mode: str = 'together',
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get cached matches for a group.

        Args:
            project_id: Project ID
            group_id: Group ID
            match_mode: 'together' or 'event_window'
            limit: Max results (None for all)
            offset: Results offset for pagination

        Returns:
            List of match dicts with photo info
        """
        try:
            # Map match_mode to scope
            scope = 'same_photo' if match_mode == 'together' else match_mode

            with self.db._connect() as conn:
                limit_clause = f"LIMIT {limit}" if limit else ""
                offset_clause = f"OFFSET {offset}" if offset else ""

                cur = conn.execute(f"""
                    SELECT
                        gam.photo_id,
                        pm.path,
                        gam.scope,
                        gam.computed_at,
                        pm.created_ts,
                        pm.created_date
                    FROM group_asset_matches gam
                    JOIN photo_metadata pm ON pm.id = gam.photo_id AND pm.project_id = ?
                    WHERE gam.group_id = ? AND gam.scope = ?
                    ORDER BY pm.created_ts DESC
                    {limit_clause}
                    {offset_clause}
                """, (project_id, group_id, scope))

                matches = []
                for row in cur.fetchall():
                    matches.append({
                        'asset_id': row[0],
                        'asset_path': row[1],
                        'asset_type': 'photo',
                        'scope': row[2],
                        'computed_at': row[3],
                        'created_ts': row[4],
                        'created_date': row[5]
                    })

                return matches

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to get matches: {e}", exc_info=True)
            return []

    # =========================================================================
    # STALENESS MANAGEMENT (simplified without person_group_state table)
    # =========================================================================

    def mark_groups_stale_for_person(self, project_id: int, branch_key: str) -> int:
        """
        Clear cached matches for all groups containing a person.

        Called when:
        - Face clustering runs
        - Person merge happens
        - Photos are added/deleted

        Args:
            project_id: Project ID
            branch_key: Person's branch_key

        Returns:
            Number of groups affected
        """
        try:
            with self.db._connect() as conn:
                # Get groups containing this person
                cur = conn.execute("""
                    SELECT DISTINCT m.group_id
                    FROM person_group_members m
                    JOIN person_groups g ON g.id = m.group_id
                    WHERE m.branch_key = ? AND g.project_id = ?
                """, (branch_key, project_id))

                group_ids = [row[0] for row in cur.fetchall()]

                if not group_ids:
                    return 0

                # Clear cached matches for these groups
                placeholders = ','.join(['?'] * len(group_ids))
                conn.execute(f"""
                    DELETE FROM group_asset_matches
                    WHERE group_id IN ({placeholders})
                """, group_ids)

                # Reset last_used_at so groups appear stale (need recomputation)
                conn.execute(f"""
                    UPDATE person_groups SET last_used_at = NULL
                    WHERE id IN ({placeholders})
                """, group_ids)

                affected = len(group_ids)
                conn.commit()

                if affected > 0:
                    logger.info(f"[PeopleGroupService] Cleared matches for {affected} groups "
                               f"affected by person {branch_key}")

                return affected

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to mark stale: {e}", exc_info=True)
            return 0

    def mark_all_groups_stale(self, project_id: int) -> int:
        """
        Clear cached matches for all groups in a project.

        Called when:
        - Photo scan completes
        - Face detection/clustering completes

        Args:
            project_id: Project ID

        Returns:
            Number of groups affected
        """
        try:
            with self.db._connect() as conn:
                # Get all groups for this project
                cur = conn.execute("""
                    SELECT id FROM person_groups
                    WHERE project_id = ? AND is_deleted = 0
                """, (project_id,))

                group_ids = [row[0] for row in cur.fetchall()]

                if not group_ids:
                    return 0

                # Clear cached matches
                placeholders = ','.join(['?'] * len(group_ids))
                conn.execute(f"""
                    DELETE FROM group_asset_matches
                    WHERE group_id IN ({placeholders})
                """, group_ids)

                # Reset last_used_at so groups appear stale (need recomputation)
                conn.execute(f"""
                    UPDATE person_groups SET last_used_at = NULL
                    WHERE id IN ({placeholders})
                """, group_ids)

                affected = len(group_ids)
                conn.commit()

                if affected > 0:
                    logger.info(f"[PeopleGroupService] Cleared matches for {affected} groups "
                               f"in project {project_id}")

                return affected

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to mark all stale: {e}", exc_info=True)
            return 0

    def get_stale_groups(self, project_id: int) -> List[int]:
        """
        Get list of group IDs that need recomputation.

        A group is considered stale if it has no cached matches AND has
        never been computed (last_used_at is NULL).  Groups that were
        computed but legitimately have 0 matches are NOT stale.

        Args:
            project_id: Project ID

        Returns:
            List of group IDs
        """
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT g.id
                    FROM person_groups g
                    LEFT JOIN group_asset_matches gam ON g.id = gam.group_id
                    WHERE g.project_id = ? AND g.is_deleted = 0
                      AND g.last_used_at IS NULL
                    GROUP BY g.id
                    HAVING COUNT(gam.photo_id) = 0
                """, (project_id,))

                return [row[0] for row in cur.fetchall()]

        except Exception as e:
            logger.error(f"[PeopleGroupService] Failed to get stale groups: {e}", exc_info=True)
            return []
