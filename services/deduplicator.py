# services/deduplicator.py
# Extracted from SearchOrchestrator._deduplicate_results()
#
# Stacks duplicate results behind a single representative.
# Representative selection: non-copy filename > higher resolution > newer date.
#
# Key safety rule: the representative must come from the already-gated
# candidate set. Post-dedup re-validation catches illegal representatives.

"""
Deduplicator - Stack duplicates behind best representative.

Usage:
    from services.deduplicator import Deduplicator

    dedup = Deduplicator(project_id=1)
    deduped, stacked_count = dedup.deduplicate(scored_results, project_meta)
"""

import re
import time
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from logging_config import get_logger

logger = get_logger(__name__)

# Copy/duplicate filename patterns
_COPY_PATTERN = re.compile(
    r'[\s\-_]*(kopie|copy|kopia|copia)\s*(\(\d+\))?'
    r'|[\s\-_]*\(\d+\)\s*$',
    re.IGNORECASE,
)


def is_copy_filename(path: str) -> bool:
    """Detect filenames that look like copies (Kopie, Copy, (1), (2), etc.)."""
    import os
    basename = os.path.splitext(os.path.basename(path))[0]
    return bool(_COPY_PATTERN.search(basename))


class Deduplicator:
    """
    Stack duplicate search results behind a single representative.

    Uses the media_asset / media_instance tables to identify duplicates.
    Caches the duplicate map for performance.
    """

    _DUP_CACHE_TTL = 120.0  # 2 minutes

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._dup_cache: Optional[Dict[str, Tuple[str, int]]] = None
        self._dup_cache_time: float = 0.0

    def deduplicate(
        self,
        scored: list,
        project_meta: Dict[str, Dict],
    ) -> Tuple[list, int]:
        """
        Stack duplicate results: keep best representative per group.

        Representative selection priority (when scores tie within epsilon):
        1. Non-copy filename beats copy filename
        2. Higher resolution (width * height)
        3. Newer date_taken

        Returns (deduped_list, stacked_count).
        """
        dup_map = self._get_duplicate_map()
        if not dup_map:
            return scored, 0

        seen_reps: Dict[str, object] = {}
        non_dup: list = []
        stacked = 0

        def _quality_key(sr) -> Tuple:
            """Tie-breaking key: prefer non-copy, higher-res, newer."""
            meta = project_meta.get(sr.path, {})
            is_copy = is_copy_filename(sr.path)
            w = meta.get("width", 0) or 0
            h = meta.get("height", 0) or 0
            resolution = w * h
            date = meta.get("created_date") or meta.get("date_taken") or ""
            return (not is_copy, resolution, str(date), sr.final_score)

        for sr in scored:
            entry = dup_map.get(sr.path)
            if entry is None:
                non_dup.append(sr)
                continue

            rep_path, group_size = entry
            if rep_path in seen_reps:
                existing = seen_reps[rep_path]
                if _quality_key(sr) > _quality_key(existing):
                    sr.duplicate_count = existing.duplicate_count
                    seen_reps[rep_path] = sr
                stacked += 1
            else:
                sr.duplicate_count = group_size - 1
                seen_reps[rep_path] = sr

        result = non_dup + list(seen_reps.values())
        result.sort(key=lambda r: r.final_score, reverse=True)
        return result, stacked

    def invalidate_cache(self):
        """Force refresh of duplicate map on next use."""
        self._dup_cache = None
        self._dup_cache_time = 0.0

    def _get_duplicate_map(self) -> Dict[str, Tuple[str, int]]:
        """
        Build path -> (representative_path, group_size) map.

        Photos sharing the same content_hash (via media_asset) are duplicates.
        """
        now = time.time()
        if self._dup_cache is not None and (now - self._dup_cache_time) < self._DUP_CACHE_TTL:
            return self._dup_cache

        dup_map: Dict[str, Tuple[str, int]] = {}
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT
                        mi.photo_id,
                        pm.path,
                        a.representative_photo_id,
                        a.asset_id
                    FROM media_instance mi
                    JOIN media_asset a ON mi.asset_id = a.asset_id
                        AND mi.project_id = a.project_id
                    JOIN photo_metadata pm ON mi.photo_id = pm.id
                    WHERE mi.project_id = ?
                    AND a.asset_id IN (
                        SELECT asset_id FROM media_instance
                        WHERE project_id = ? GROUP BY asset_id HAVING COUNT(*) > 1
                    )
                    ORDER BY a.asset_id
                """, (self.project_id, self.project_id)).fetchall()

                asset_groups: Dict[int, List[Tuple[int, str]]] = defaultdict(list)
                rep_map: Dict[int, Optional[int]] = {}
                for row in rows:
                    asset_id = row['asset_id']
                    asset_groups[asset_id].append((row['photo_id'], row['path']))
                    if row['representative_photo_id'] is not None:
                        rep_map[asset_id] = row['representative_photo_id']

                for asset_id, members in asset_groups.items():
                    group_size = len(members)
                    rep_id = rep_map.get(asset_id)
                    rep_path = None
                    for pid, ppath in members:
                        if pid == rep_id:
                            rep_path = ppath
                            break
                    if not rep_path:
                        rep_path = members[0][1]

                    for _, ppath in members:
                        dup_map[ppath] = (rep_path, group_size)

        except Exception as e:
            logger.debug(f"[Deduplicator] Duplicate map build failed: {e}")

        self._dup_cache = dup_map
        self._dup_cache_time = now
        return dup_map
