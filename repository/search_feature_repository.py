# repository/search_feature_repository.py
# Repository for the search_asset_features flattened table.
#
# This table pre-computes and caches the metadata that the search
# pipeline queries on every request (face_count, is_screenshot, has_gps, etc.)
# instead of rebuilding it via JOINs against photo_metadata + face_crops.
#
# At 50k+ photos the JOIN approach becomes a bottleneck; this table
# keeps search snappy with a single indexed SELECT.

"""
SearchFeatureRepository - Read/write the flattened search index.

Usage:
    from repository.search_feature_repository import SearchFeatureRepository

    repo = SearchFeatureRepository()
    repo.refresh_project(project_id)   # full rebuild
    repo.refresh_asset(path)           # single-asset update
    meta = repo.get_project_meta(project_id)  # {path: {...}}
"""

import os
import re
import time
from typing import Dict, Optional, List

from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)

# Known screenshot resolution pairs (weak signal — insufficient alone)
_SCREENSHOT_RESOLUTIONS = frozenset({
    (1170, 2532), (2532, 1170),
    (1179, 2556), (2556, 1179),
    (1284, 2778), (2778, 1284),
    (1290, 2796), (2796, 1290),
    (1080, 1920), (1920, 1080),
    (1080, 2340), (2340, 1080),
    (1080, 2400), (2400, 1080),
    (1440, 2560), (2560, 1440),
    (1440, 3200), (3200, 1440),
    (1440, 3088), (3088, 1440),
})

# Screenshot folder patterns (strong signal when combined with resolution)
_SCREENSHOT_FOLDER_RE = re.compile(
    r'(?i)(screenshots?|screen.?captures?|bildschirmfotos?)',
)

_SCREENSHOT_FILENAME_RE = re.compile(
    r'(?i)(screenshot|screen.?shot|screen.?cap|capture|bildschirmfoto'
    r'|schermopname|captura|snip|clip\d)',
)

# Camera EXIF markers — their ABSENCE is a weak screenshot hint
_CAMERA_EXIF_EXTENSIONS = frozenset({
    '.jpg', '.jpeg', '.heic', '.heif', '.cr2', '.nef', '.arw', '.dng',
})


def _compute_screenshot_confidence(
    path: str, width, height,
    has_camera_exif: bool = True,
    ocr_text: str = "",
) -> float:
    """
    Compute a screenshot confidence score (0.0 to 1.0).

    Combines multiple signals instead of using resolution alone:
    - Filename match: strong positive (0.90)
    - Screenshot folder: moderate positive (0.40)
    - Resolution match: weak positive, NOT sufficient alone (0.25)
    - No camera EXIF on photo extension: weak positive (0.15)
    - High OCR/UI text density: weak positive (0.15)
    - PNG extension (non-camera): weak positive (0.10)

    is_screenshot = confidence >= 0.50
    """
    confidence = 0.0
    basename = os.path.basename(path) if path else ""
    ext = os.path.splitext(basename)[1].lower() if basename else ""

    # Strong positive: filename match
    if _SCREENSHOT_FILENAME_RE.search(basename):
        confidence += 0.90

    # Moderate positive: screenshot folder
    dirname = os.path.dirname(path) if path else ""
    if _SCREENSHOT_FOLDER_RE.search(dirname):
        confidence += 0.40

    # Weak positive: known screenshot resolution (NOT sufficient alone)
    resolution_match = False
    try:
        w, h = int(width or 0), int(height or 0)
        if w > 0 and h > 0 and (w, h) in _SCREENSHOT_RESOLUTIONS:
            confidence += 0.25
            resolution_match = True
    except (TypeError, ValueError):
        pass

    # Weak positive: no camera EXIF on a photo-type extension
    if ext in _CAMERA_EXIF_EXTENSIONS and not has_camera_exif:
        confidence += 0.15

    # Weak positive: PNG (non-camera source)
    if ext == '.png' and resolution_match:
        confidence += 0.10

    # Weak positive: high OCR text density (UI text)
    if ocr_text and len(ocr_text) > 30:
        confidence += 0.15

    return min(1.0, confidence)


def _detect_screenshot(path: str, width, height) -> bool:
    """
    Conservative screenshot detection.

    Hard positive:
      - explicit screenshot-like filename (strong signal, >= 0.90)
      - screenshot folder + any weak signal (>= 0.50)

    Resolution alone is NOT sufficient — too many exported phone images
    share common screenshot resolutions (e.g. 1179x2556).
    """
    basename = os.path.basename(path or "").lower()

    # Strong positive: filename match alone is sufficient
    if _SCREENSHOT_FILENAME_RE.search(basename):
        return True

    # Moderate positive: screenshot folder (0.40) needs one weak signal
    dirname = os.path.dirname(path) if path else ""
    if _SCREENSHOT_FOLDER_RE.search(dirname):
        confidence = _compute_screenshot_confidence(path, width, height)
        return confidence >= 0.50

    # Resolution alone (0.25) is NOT sufficient — too noisy
    return False


class SearchFeatureRepository:
    """Read/write the search_asset_features flattened index."""

    def __init__(self, db: Optional[DatabaseConnection] = None):
        self.db = db or DatabaseConnection()

    def table_exists(self) -> bool:
        """Check if search_asset_features table exists."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='search_asset_features'"
                )
                return cursor.fetchone() is not None
        except Exception:
            return False

    def refresh_project(self, project_id: int) -> int:
        """
        Full rebuild of search_asset_features for a project.

        Joins photo_metadata + face_crops to produce the flattened row.
        Returns the number of rows written.
        """
        start = time.time()
        count = 0

        try:
            with self.db.get_connection() as conn:
                # Delete existing rows for project
                conn.execute(
                    "DELETE FROM search_asset_features WHERE project_id = ?",
                    (project_id,)
                )

                # Fetch photo metadata
                rows = conn.execute("""
                    SELECT id, path, width, height,
                           gps_latitude, gps_longitude,
                           flag, rating,
                           created_date, date_taken,
                           ocr_text
                    FROM photo_metadata
                    WHERE project_id = ?
                """, (project_id,)).fetchall()

                # Fetch face counts
                face_counts: Dict[str, int] = {}
                try:
                    face_rows = conn.execute("""
                        SELECT image_path, COUNT(*) as cnt
                        FROM face_crops
                        WHERE project_id = ?
                        GROUP BY image_path
                    """, (project_id,)).fetchall()
                    for fr in face_rows:
                        if fr['image_path']:
                            face_counts[fr['image_path']] = fr['cnt']
                            # Also store normalized key
                            face_counts[os.path.normpath(fr['image_path'])] = fr['cnt']
                except Exception:
                    pass  # face_crops may not exist yet

                # Fetch duplicate group mapping when available
                duplicate_groups: Dict[str, int] = {}
                try:
                    from repository.asset_repository import AssetRepository
                    asset_repo = AssetRepository(self.db)
                    duplicate_groups = asset_repo.get_path_to_asset_map(project_id)
                except Exception:
                    pass

                # Insert flattened rows
                for row in rows:
                    path = row['path']
                    w = row['width']
                    h = row['height']
                    lat = row['gps_latitude']
                    lon = row['gps_longitude']
                    has_gps = 1 if (lat is not None and lon is not None
                                    and lat != 0 and lon != 0) else 0

                    ocr_text = row["ocr_text"] or ""

                    ss_conf = _compute_screenshot_confidence(path, w, h, ocr_text=ocr_text)
                    is_ss = 1 if ss_conf >= 0.50 else 0
                    ext = os.path.splitext(path)[1].lower() if path else None
                    date = row['created_date'] or row['date_taken']

                    # Face count: try exact path, then normalized
                    fc = face_counts.get(path, 0)
                    if fc == 0:
                        fc = face_counts.get(os.path.normpath(path), 0)

                    media_type = "video" if (path and os.path.splitext(path)[1].lower() in {
                        ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".m4v", ".flv"
                    }) else "photo"

                    duplicate_group_id = duplicate_groups.get(path)
                    if duplicate_group_id is None:
                        duplicate_group_id = duplicate_groups.get(os.path.normpath(path))

                    # Try to store screenshot_confidence if column exists
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO search_asset_features
                            (path, project_id, media_type, width, height, has_gps,
                             face_count, is_screenshot, screenshot_confidence,
                             flag, ext, date_taken, duplicate_group_id, ocr_text,
                             rating, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """, (
                            path, project_id, media_type, w, h, has_gps,
                            fc, is_ss, ss_conf, row['flag'], ext, date,
                            duplicate_group_id, ocr_text, row['rating'] or 0,
                        ))
                    except Exception:
                        # Fallback: column doesn't exist yet (pre-migration)
                        conn.execute("""
                            INSERT OR REPLACE INTO search_asset_features
                            (path, project_id, media_type, width, height, has_gps,
                             face_count, is_screenshot, flag, ext, date_taken,
                             rating, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        """, (
                            path, project_id, media_type, w, h, has_gps,
                            fc, is_ss, row['flag'], ext, date,
                            row['rating'] or 0,
                        ))
                    count += 1

                conn.commit()

        except Exception as e:
            logger.error(f"[SearchFeatureRepo] refresh_project failed: {e}")
            return 0

        elapsed = (time.time() - start) * 1000
        logger.info(
            f"[SearchFeatureRepo] Refreshed {count} rows for project {project_id} "
            f"in {elapsed:.0f}ms"
        )
        return count

    def get_project_meta(self, project_id: int) -> Dict[str, Dict]:
        """
        Load the flattened search metadata for all assets in a project.

        Returns: {path: {face_count, is_screenshot, has_gps, width, height,
                         flag, rating, date_taken, ext, ...}}
        """
        result: Dict[str, Dict] = {}
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT path, media_type, width, height, has_gps,
                           face_count, is_screenshot, flag, ext,
                           date_taken, duplicate_group_id, ocr_text,
                           rating
                    FROM search_asset_features
                    WHERE project_id = ?
                """, (project_id,)).fetchall()

                for row in rows:
                    result[row['path']] = {
                        "media_type": row['media_type'],
                        "width": row['width'],
                        "height": row['height'],
                        "has_gps": bool(row['has_gps']),
                        "face_count": row['face_count'] or 0,
                        "is_screenshot": bool(row['is_screenshot']),
                        "flag": row['flag'] or "none",
                        "ext": row['ext'],
                        "created_date": row['date_taken'],
                        "date_taken": row['date_taken'],
                        "duplicate_group_id": row['duplicate_group_id'],
                        "ocr_text": row['ocr_text'],
                        "rating": row['rating'] or 0,
                    }
        except Exception as e:
            logger.warning(f"[SearchFeatureRepo] get_project_meta failed: {e}")

        return result

    def update_face_count(self, path: str, face_count: int):
        """Update face_count for a single asset (after face detection)."""
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE search_asset_features SET face_count = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                    (face_count, path)
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"[SearchFeatureRepo] update_face_count failed: {e}")

    def update_flag(self, path: str, flag: str):
        """Update flag for a single asset (after rating change)."""
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE search_asset_features SET flag = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                    (flag, path)
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"[SearchFeatureRepo] update_flag failed: {e}")

    def get_row_count(self, project_id: int) -> int:
        """Return number of indexed assets for a project."""
        try:
            with self.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM search_asset_features "
                    "WHERE project_id = ?",
                    (project_id,)
                ).fetchone()
                return row['cnt'] if row else 0
        except Exception:
            return 0

    def ensure_project_index(self, project_id: int) -> int:
        """
        Ensure flattened features exist for a project.
        Returns row count after rebuild/check.
        """
        count = self.get_row_count(project_id)
        if count > 0:
            return count
        return self.refresh_project(project_id)
