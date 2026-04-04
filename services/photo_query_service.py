# services/photo_query_service.py
# Paged photo query service for both layouts.
#
# Provides count_photos() and fetch_page() with stable ordering so that
# both GoogleLayout and CurrentLayout can load large result sets in
# incremental pages without duplicates or missing rows.
#
# SQL mirrors google_components/photo_helpers.PhotoLoadWorker exactly:
#   photos → photo_metadata JOIN project_images ON image_path
#   videos → video_metadata WHERE project_id = ?  (direct, no join)
#
# Thresholds are centralised here and can be tuned from preferences.

import logging
import platform
from typing import Optional, Dict, Any, List

from reference_db import ReferenceDB

logger = logging.getLogger(__name__)

# ── Tunable thresholds ───────────────────────────────────────────────
SMALL_THRESHOLD = 3_000      # below this: load everything in one shot
PAGE_SIZE = 250              # rows per page (base grid)
PREFETCH_PAGES = 2           # keep N pages ahead of the viewport
MAX_IN_MEMORY_ROWS = 10_000  # upper cap to avoid OOM

_IS_WIN = platform.system() == "Windows"


def _normalize_folder(raw: str) -> str:
    """Normalize folder for LIKE comparison (match PhotoLoadWorker)."""
    normalized = raw.replace("\\", "/")
    if _IS_WIN:
        normalized = normalized.lower()
    return normalized.rstrip("/")


class PhotoQueryService:
    """
    Centralised photo-query backend used by both layouts.

    Usage:
        svc = PhotoQueryService()
        total = svc.count_photos(project_id, filters)
        rows  = svc.fetch_page(project_id, filters, offset=0, limit=250)
    """

    def __init__(self):
        self.db = ReferenceDB()

    # ── Public API ───────────────────────────────────────────────────

    def count_photos(
        self,
        project_id: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Return total matching asset count (photos + videos) for *project_id* + *filters*.

        Note: Despite the name, this returns the count of ALL assets (photos and videos).
        The name is kept for backwards compatibility. Use count_assets() for clarity.
        """
        filters = filters or {}
        sql, params = self._build_count_sql(project_id, filters)
        with self.db._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            total = row[0] if row else 0
        logger.debug(
            "[PhotoQueryService] count_assets (photos+videos) pid=%d filters=%s -> %d",
            project_id, filters, total,
        )
        return total

    def count_assets(
        self,
        project_id: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Alias for count_photos() — returns total photos + videos.

        This is the preferred API for clarity. The returned count includes
        both photos and videos matching the filters.
        """
        return self.count_photos(project_id, filters)

    def fetch_page(
        self,
        project_id: int,
        filters: Optional[Dict[str, Any]] = None,
        offset: int = 0,
        limit: int = PAGE_SIZE,
    ) -> List[Dict[str, Any]]:
        """
        Fetch a page of assets (photos + videos) sorted by date_taken DESC, path DESC.

        Returns list of dicts: {path, date_taken, width, height, media_type}.
        Each row has 'media_type' = 'photo' or 'video' to distinguish them.
        """
        filters = filters or {}
        sql, params = self._build_page_sql(project_id, filters, offset, limit)
        with self.db._connect() as conn:
            cur = conn.execute(sql, params)
            columns = [d[0] for d in cur.description]
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        logger.debug(
            "[PhotoQueryService] fetch_assets pid=%d offset=%d limit=%d -> %d rows",
            project_id, offset, limit, len(rows),
        )
        return rows

    def fetch_assets(
        self,
        project_id: int,
        filters: Optional[Dict[str, Any]] = None,
        offset: int = 0,
        limit: int = PAGE_SIZE,
    ) -> List[Dict[str, Any]]:
        """Alias for fetch_page() — returns photos + videos together.

        This is the preferred API for clarity.
        """
        return self.fetch_page(project_id, filters, offset, limit)

    def should_page(self, total: int) -> bool:
        """Return True if the result set is large enough to warrant paging."""
        return total > SMALL_THRESHOLD

    # ── Internal: build photo WHERE ──────────────────────────────────

    def _photo_where(
        self, project_id: int, filters: Dict[str, Any]
    ) -> tuple[str, list]:
        """WHERE clause for photo_metadata m JOIN project_images pi."""
        clauses = ["pi.project_id = ?"]
        params: list = [project_id]

        if filters.get("year"):
            clauses.append("strftime('%Y', pm.created_date) = ?")
            params.append(str(filters["year"]))
        if filters.get("month"):
            clauses.append("strftime('%m', pm.created_date) = ?")
            params.append(str(filters["month"]).zfill(2))
        if filters.get("day"):
            clauses.append("strftime('%d', pm.created_date) = ?")
            params.append(str(filters["day"]).zfill(2))
        if filters.get("folder"):
            folder = _normalize_folder(filters["folder"])
            clauses.append("pm.path LIKE ?")
            params.append(f"{folder}%")
        if filters.get("person_branch_key"):
            clauses.append(
                "pm.path IN ("
                "  SELECT DISTINCT fc.image_path FROM face_crops fc"
                "  WHERE fc.project_id = ? AND fc.branch_key = ?"
                ")"
            )
            params.extend([project_id, filters["person_branch_key"]])

        return " AND ".join(clauses), params

    # ── Internal: build video WHERE ──────────────────────────────────

    def _video_where(
        self, project_id: int, filters: Dict[str, Any]
    ) -> tuple[str, list]:
        """WHERE clause for video_metadata vm (direct project_id, no join)."""
        clauses = ["vm.project_id = ?"]
        params: list = [project_id]

        if filters.get("year"):
            clauses.append("strftime('%Y', vm.created_date) = ?")
            params.append(str(filters["year"]))
        if filters.get("month"):
            clauses.append("strftime('%m', vm.created_date) = ?")
            params.append(str(filters["month"]).zfill(2))
        if filters.get("day"):
            clauses.append("strftime('%d', vm.created_date) = ?")
            params.append(str(filters["day"]).zfill(2))
        if filters.get("folder"):
            folder = _normalize_folder(filters["folder"])
            clauses.append("vm.path LIKE ?")
            params.append(f"{folder}%")

        # No person filter for videos (face_crops is photo-only)
        return " AND ".join(clauses), params

    # ── SQL assemblers ───────────────────────────────────────────────

    def _include_videos(self, filters: Dict[str, Any]) -> bool:
        """Videos are excluded when filtering by person (no face data)."""
        return not filters.get("person_branch_key")

    def _build_count_sql(
        self, project_id: int, filters: Dict[str, Any]
    ) -> tuple[str, list]:
        pw, pp = self._photo_where(project_id, filters)

        photo_sql = (
            "SELECT COUNT(DISTINCT pm.path) FROM photo_metadata pm "
            "JOIN project_images pi ON pm.path = pi.image_path "
            f"WHERE {pw}"
        )

        if self._include_videos(filters):
            vw, vp = self._video_where(project_id, filters)
            video_sql = (
                "SELECT COUNT(DISTINCT vm.path) FROM video_metadata vm "
                f"WHERE {vw}"
            )
            sql = f"SELECT (({photo_sql}) + ({video_sql}))"
            params = pp + vp
        else:
            sql = photo_sql
            params = pp

        return sql, params

    def _build_page_sql(
        self,
        project_id: int,
        filters: Dict[str, Any],
        offset: int,
        limit: int,
    ) -> tuple[str, list]:
        pw, pp = self._photo_where(project_id, filters)

        photo_sql = (
            "SELECT DISTINCT pm.path, pm.created_date AS date_taken, "
            "pm.width, pm.height, 'photo' AS media_type "
            "FROM photo_metadata pm "
            "JOIN project_images pi ON pm.path = pi.image_path "
            f"WHERE {pw}"
        )

        if self._include_videos(filters):
            vw, vp = self._video_where(project_id, filters)
            video_sql = (
                "SELECT DISTINCT vm.path, vm.created_date AS date_taken, "
                "vm.width, vm.height, 'video' AS media_type "
                "FROM video_metadata vm "
                f"WHERE {vw}"
            )
            union_sql = (
                f"SELECT * FROM ({photo_sql} UNION ALL {video_sql}) "
                "ORDER BY date_taken DESC, path DESC "
                "LIMIT ? OFFSET ?"
            )
            all_params = pp + vp + [limit, offset]
        else:
            union_sql = (
                f"{photo_sql} "
                "ORDER BY date_taken DESC, path DESC "
                "LIMIT ? OFFSET ?"
            )
            all_params = pp + [limit, offset]

        return union_sql, all_params
