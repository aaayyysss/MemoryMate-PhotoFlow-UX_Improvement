# services/person_search_service.py
# Person Cluster Integration into Search
#
# Bridges face clustering (face_branch_reps, face_crops) with the search
# orchestrator so that queries mentioning people by name or branch_key
# seamlessly filter results to photos containing those people.
#
# Supports:
# - person:branch_key structured token (existing)
# - person:DisplayName name-based lookup (new)
# - Natural language: "photos of John" → person filter (new)
# - Multi-person intersection: "John and Sarah" → co-occurrence (new)
# - Person facet in search results (new)

"""
PersonSearchService - Bridge between face clusters and search.

Usage:
    from services.person_search_service import PersonSearchService

    pss = PersonSearchService(project_id=1)
    # Resolve a display name to branch_keys
    keys = pss.resolve_person_name("John")

    # Get all photo paths for a person
    paths = pss.get_person_photo_paths("face_001")

    # Get co-occurrence paths (photos where both people appear)
    paths = pss.get_co_occurrence_paths(["face_001", "face_002"])

    # Enrich scored results with person metadata for facets
    person_facet = pss.compute_person_facet(result_paths)
"""

from __future__ import annotations
import os
import re
from typing import List, Dict, Optional, Set, Tuple, Any
from logging_config import get_logger

logger = get_logger(__name__)


# ── Natural language person patterns ──
# Matches: "photos of John", "pictures with Sarah", "John's photos"
_NL_PERSON_PATTERNS = [
    re.compile(r"(?:photos?|pictures?|images?)\s+(?:of|with|featuring)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE),
    re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'s\s+(?:photos?|pictures?|images?)", re.IGNORECASE),
    re.compile(r"(?:show|find|search)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE),
]

# Words that look like names but aren't (avoid false positives)
_NOT_NAMES = frozenset({
    "beach", "mountain", "city", "sunset", "sunrise", "forest", "lake",
    "wedding", "party", "food", "pets", "snow", "night", "car", "sport",
    "documents", "screenshots", "videos", "photos", "pictures", "images",
    "favorites", "landscape", "portrait", "travel", "flowers", "architecture",
    "all", "the", "this", "that", "these", "those", "some", "any",
    "recent", "latest", "best", "old", "new",
})


class PersonSearchService:
    """
    Bridge between face clustering and the search pipeline.

    Provides:
    - Name-to-branch_key resolution
    - Photo path retrieval per person
    - Multi-person co-occurrence (AND mode)
    - Person facets for search results
    - NL person name extraction from free text
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._name_cache: Optional[Dict[str, List[str]]] = None
        self._branch_to_name: Optional[Dict[str, str]] = None

    def _get_db(self):
        from repository.base_repository import DatabaseConnection
        return DatabaseConnection()

    def _load_name_cache(self):
        """Load display_name → [branch_key] mapping from face_branch_reps."""
        if self._name_cache is not None:
            return
        self._name_cache = {}
        self._branch_to_name = {}
        try:
            db = self._get_db()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT branch_key, label, count FROM face_branch_reps "
                    "WHERE project_id = ? AND count > 0",
                    (self.project_id,)
                )
                for row in cursor.fetchall():
                    bk = row["branch_key"]
                    label = row["label"] or bk
                    name_lower = label.lower().strip()
                    if name_lower not in self._name_cache:
                        self._name_cache[name_lower] = []
                    self._name_cache[name_lower].append(bk)
                    self._branch_to_name[bk] = label
        except Exception as e:
            logger.debug(f"[PersonSearch] Failed to load name cache: {e}")
            self._name_cache = {}
            self._branch_to_name = {}

    def resolve_person_name(self, name: str) -> List[str]:
        """
        Resolve a display name or branch_key to branch_key(s).

        Supports:
        - Exact match: "John" → ["face_001"]
        - Prefix match: "Joh" → ["face_001"]
        - Branch key passthrough: "face_001" → ["face_001"]

        Returns:
            List of matching branch_keys
        """
        self._load_name_cache()
        name_lower = name.lower().strip()

        # Direct branch_key passthrough
        if name_lower.startswith("face_") or name_lower.startswith("cluster_"):
            if name_lower in (self._branch_to_name or {}):
                return [name_lower]
            # Check if it exists in DB
            return [name] if self._branch_key_exists(name) else []

        # Exact match
        if name_lower in self._name_cache:
            return list(self._name_cache[name_lower])

        # Prefix match (for autocomplete/partial typing)
        matches = []
        for cached_name, branch_keys in self._name_cache.items():
            if cached_name.startswith(name_lower):
                matches.extend(branch_keys)
        return matches

    def _branch_key_exists(self, branch_key: str) -> bool:
        """Check if a branch_key exists in face_branch_reps."""
        try:
            db = self._get_db()
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM face_branch_reps "
                    "WHERE project_id = ? AND branch_key = ?",
                    (self.project_id, branch_key)
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def get_person_photo_paths(self, branch_key: str) -> List[str]:
        """
        Get all photo paths that contain a specific person.

        Args:
            branch_key: Person identifier from face_branch_reps

        Returns:
            List of photo paths
        """
        try:
            db = self._get_db()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT DISTINCT image_path FROM face_crops "
                    "WHERE project_id = ? AND branch_key = ?",
                    (self.project_id, branch_key)
                )
                return [row["image_path"] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"[PersonSearch] get_person_photo_paths failed: {e}")
            return []

    def get_person_photo_paths_multi(self, branch_keys: List[str]) -> Set[str]:
        """
        Get photo paths containing ANY of the specified people (OR mode).

        Args:
            branch_keys: List of person identifiers

        Returns:
            Set of photo paths
        """
        if not branch_keys:
            return set()
        paths = set()
        for bk in branch_keys:
            paths.update(self.get_person_photo_paths(bk))
        return paths

    def get_co_occurrence_paths(self, branch_keys: List[str]) -> Set[str]:
        """
        Get photo paths where ALL specified people appear (AND mode).

        Uses set intersection across per-person photo sets.

        Args:
            branch_keys: List of person identifiers (need 2+)

        Returns:
            Set of photo paths where all people co-occur
        """
        if len(branch_keys) < 2:
            return self.get_person_photo_paths_multi(branch_keys)

        per_person_sets = []
        for bk in branch_keys:
            person_paths = set(self.get_person_photo_paths(bk))
            if not person_paths:
                return set()  # If any person has 0 photos, intersection is empty
            per_person_sets.append(person_paths)

        return set.intersection(*per_person_sets)

    def compute_person_facet(
        self, paths: List[str], max_people: int = 10
    ) -> Dict[str, int]:
        """
        Compute person facet from a result set.

        Counts how many result photos each person appears in,
        returns top-N people with their display names and counts.

        Args:
            paths: Result photo paths
            max_people: Maximum people to include in facet

        Returns:
            {"John Smith": 15, "Sarah Miller": 8, ...}
        """
        if not paths:
            return {}

        self._load_name_cache()
        path_set = set(paths)

        try:
            db = self._get_db()
            with db.get_connection() as conn:
                placeholders = ",".join("?" * len(paths))
                cursor = conn.execute(
                    f"SELECT branch_key, image_path FROM face_crops "
                    f"WHERE project_id = ? AND image_path IN ({placeholders})",
                    [self.project_id] + list(paths)
                )

                person_counts: Dict[str, int] = {}
                for row in cursor.fetchall():
                    bk = row["branch_key"]
                    if bk and row["image_path"] in path_set:
                        name = (self._branch_to_name or {}).get(bk, bk)
                        person_counts[name] = person_counts.get(name, 0) + 1

                # Sort by count descending, take top-N
                sorted_counts = dict(
                    sorted(person_counts.items(), key=lambda kv: kv[1], reverse=True)[:max_people]
                )
                return sorted_counts
        except Exception as e:
            logger.debug(f"[PersonSearch] compute_person_facet failed: {e}")
            return {}

    def extract_person_names_from_query(self, query: str) -> Tuple[List[str], str]:
        """
        Extract person names from natural language query text.

        Detects patterns like:
        - "photos of John"
        - "John's pictures"
        - "show Sarah"
        - "John and Sarah at the beach"

        Returns:
            (resolved_branch_keys, remaining_query_text)
        """
        self._load_name_cache()
        if not self._name_cache:
            return [], query

        resolved_keys = []
        remaining = query

        # Try NL patterns first
        for pattern in _NL_PERSON_PATTERNS:
            match = pattern.search(remaining)
            if match:
                candidate_name = match.group(1).strip()
                if candidate_name.lower() not in _NOT_NAMES:
                    keys = self.resolve_person_name(candidate_name)
                    if keys:
                        resolved_keys.extend(keys)
                        remaining = remaining[:match.start()] + remaining[match.end():]

        # Also check for "X and Y" pattern for co-occurrence
        and_pattern = re.compile(
            r"([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)", re.IGNORECASE
        )
        and_match = and_pattern.search(remaining)
        if and_match:
            name1 = and_match.group(1).strip()
            name2 = and_match.group(2).strip()
            if (name1.lower() not in _NOT_NAMES and
                    name2.lower() not in _NOT_NAMES):
                keys1 = self.resolve_person_name(name1)
                keys2 = self.resolve_person_name(name2)
                if keys1 and keys2:
                    resolved_keys.extend(keys1)
                    resolved_keys.extend(keys2)
                    remaining = (
                        remaining[:and_match.start()] +
                        remaining[and_match.end():]
                    )

        # Direct name match against known names (for simple "John" queries)
        if not resolved_keys:
            words = remaining.split()
            for word in words:
                clean = word.strip(".,!?;:'\"")
                if clean and clean.lower() not in _NOT_NAMES:
                    keys = self.resolve_person_name(clean)
                    if keys:
                        resolved_keys.extend(keys)
                        remaining = remaining.replace(word, "", 1)

        remaining = re.sub(r"\s+", " ", remaining).strip()
        # Deduplicate while preserving order
        seen = set()
        unique_keys = []
        for k in resolved_keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)

        return unique_keys, remaining

    def get_all_person_names(self) -> List[Dict[str, Any]]:
        """
        Get all known person names and their photo counts.

        Returns:
            List of {"branch_key": str, "name": str, "photo_count": int}
        """
        self._load_name_cache()
        result = []
        try:
            db = self._get_db()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT branch_key, label, count FROM face_branch_reps "
                    "WHERE project_id = ? AND count > 0 "
                    "ORDER BY count DESC",
                    (self.project_id,)
                )
                for row in cursor.fetchall():
                    result.append({
                        "branch_key": row["branch_key"],
                        "name": row["label"] or row["branch_key"],
                        "photo_count": row["count"] or 0,
                    })
        except Exception as e:
            logger.debug(f"[PersonSearch] get_all_person_names failed: {e}")
        return result

    def get_group_match_paths(self, branch_keys: List[str]) -> Set[str]:
        """
        Get photo paths from precomputed person_groups / group_asset_matches.

        This leverages the existing person_groups schema for richer
        co-occurrence retrieval than raw face_crops intersection.
        Falls back gracefully if tables don't exist or are empty.

        Args:
            branch_keys: Person identifiers to look up in groups

        Returns:
            Set of photo paths from matching group_asset_matches
        """
        if not branch_keys:
            return set()
        try:
            db = self._get_db()
            with db.get_connection() as conn:
                # Find groups that contain any of these branch_keys as members
                placeholders = ",".join("?" * len(branch_keys))
                rows = conn.execute(
                    f"SELECT DISTINCT gam.asset_path "
                    f"FROM group_asset_matches gam "
                    f"JOIN person_groups pg ON gam.group_id = pg.id "
                    f"WHERE pg.project_id = ? "
                    f"AND gam.group_id IN ("
                    f"  SELECT pgm.group_id FROM person_group_members pgm "
                    f"  WHERE pgm.branch_key IN ({placeholders})"
                    f")",
                    [self.project_id] + list(branch_keys),
                ).fetchall()
                return {row["asset_path"] for row in rows}
        except Exception as e:
            logger.debug(
                f"[PersonSearch] get_group_match_paths failed "
                f"(table may not exist): {e}"
            )
            return set()

    def invalidate_cache(self):
        """Invalidate the name cache (call after face re-clustering)."""
        self._name_cache = None
        self._branch_to_name = None
