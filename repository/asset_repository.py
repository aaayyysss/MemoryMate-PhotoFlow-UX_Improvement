# repository/asset_repository.py
# Version 01.01.00.00 dated 20260122
# Repository for media_asset and media_instance
#
# Part of the asset-centric duplicate management system.
# Manages:
# - media_asset: unique content identity (content_hash)
# - media_instance: physical file occurrences linked to photo_metadata

from typing import Optional, List, Dict, Any, Tuple
from .base_repository import BaseRepository, DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)

class AssetRepository(BaseRepository):
    """
    AssetRepository manages asset-centric identity.
    Tables:
    - media_asset: (project_id, content_hash) unique identity
    - media_instance: links existing photo_metadata rows to assets
    """
    def __init__(self, db: Optional[DatabaseConnection] = None):
        super().__init__(db)

    def _table_name(self) -> str:
        return "media_asset"

    # ── Asset Operations ──────────────────────────────────────────────────

    def get_asset_by_hash(self, project_id: int, content_hash: str) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT asset_id, project_id, content_hash, perceptual_hash,
                   representative_photo_id, created_at, updated_at
            FROM media_asset
            WHERE project_id = ? AND content_hash = ?
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, content_hash))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_asset_by_id(self, project_id: int, asset_id: int) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT asset_id, project_id, content_hash, perceptual_hash,
                   representative_photo_id, created_at, updated_at
            FROM media_asset
            WHERE project_id = ? AND asset_id = ?
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, asset_id))
            row = cur.fetchone()
            return dict(row) if row else None

    def create_asset_if_missing(self, project_id: int, content_hash: str,
                                representative_photo_id: Optional[int] = None,
                                perceptual_hash: Optional[str] = None) -> int:
        with self.connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO media_asset (project_id, content_hash,
                                                  representative_photo_id, perceptual_hash)
                VALUES (?, ?, ?, ?)
            """, (project_id, content_hash, representative_photo_id, perceptual_hash))

            cur = conn.execute("""
                SELECT asset_id FROM media_asset
                WHERE project_id = ? AND content_hash = ?
            """, (project_id, content_hash))
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"Failed to create media_asset for hash {content_hash[:16]}...")
            return int(row["asset_id"])

    def set_representative_photo(self, project_id: int, asset_id: int, photo_id: int) -> None:
        with self.connection() as conn:
            conn.execute("""
                UPDATE media_asset SET representative_photo_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ? AND asset_id = ?
            """, (photo_id, project_id, asset_id))

    def set_perceptual_hash(self, project_id: int, asset_id: int, perceptual_hash: str) -> None:
        with self.connection() as conn:
            conn.execute("""
                UPDATE media_asset SET perceptual_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ? AND asset_id = ?
            """, (perceptual_hash, project_id, asset_id))

    # ── Instance Operations ───────────────────────────────────────────────

    def link_instance(self, project_id: int, asset_id: int, photo_id: int,
                      source_device_id: Optional[str] = None,
                      source_path: Optional[str] = None,
                      import_session_id: Optional[str] = None,
                      file_size: Optional[int] = None) -> None:
        with self.connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO media_instance
                (project_id, asset_id, photo_id, source_device_id, source_path,
                 import_session_id, file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (project_id, asset_id, photo_id, source_device_id, source_path,
                  import_session_id, file_size))

    def get_instance_by_photo(self, project_id: int, photo_id: int) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT instance_id, project_id, asset_id, photo_id, source_device_id,
                   source_path, import_session_id, file_size, created_at
            FROM media_instance
            WHERE project_id = ? AND photo_id = ?
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, photo_id))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_asset_instances(self, project_id: int, asset_id: int) -> List[Dict[str, Any]]:
        sql = """
            SELECT i.instance_id, i.photo_id, i.source_device_id, i.source_path,
                   i.import_session_id, i.file_size, i.created_at
            FROM media_instance i
            WHERE i.project_id = ? AND i.asset_id = ?
            ORDER BY i.created_at ASC
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, asset_id))
            return [dict(r) for r in cur.fetchall()]

    def count_instances_for_asset(self, project_id: int, asset_id: int) -> int:
        sql = "SELECT COUNT(*) AS count FROM media_instance WHERE project_id = ? AND asset_id = ?"
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, asset_id))
            row = cur.fetchone()
            return int(row["count"]) if row else 0

    def get_asset_id_by_photo_id(self, project_id: int, photo_id: int) -> Optional[int]:
        sql = "SELECT asset_id FROM media_instance WHERE project_id = ? AND photo_id = ? LIMIT 1"
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, photo_id))
            row = cur.fetchone()
            return int(row["asset_id"]) if row else None

    # ── Search & Discovery Helpers ────────────────────────────────────────

    def get_path_to_asset_map(self, project_id: int) -> Dict[str, int]:
        """
        Returns a map of photo path -> asset_id for all photos in a project.
        Essential for search feature cache population.
        """
        sql = """
            SELECT pm.path, mi.asset_id
            FROM media_instance mi
            JOIN photo_metadata pm ON pm.id = mi.photo_id
            WHERE pm.project_id = ?
        """
        result = {}
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id,))
            for row in cur.fetchall():
                if row["path"]:
                    result[row["path"]] = row["asset_id"]
        return result

    def list_duplicate_assets(
        self,
        project_id: int,
        min_instances: int = 2,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        sql = """
            WITH asset_counts AS (
                SELECT asset_id, COUNT(*) as instance_count
                FROM media_instance WHERE project_id = ?
                GROUP BY asset_id HAVING COUNT(*) >= ?
            )
            SELECT a.*, ac.instance_count
            FROM asset_counts ac
            JOIN media_asset a ON a.asset_id = ac.asset_id
            ORDER BY ac.instance_count DESC
        """
        params: list = [project_id, min_instances]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def count_duplicate_assets(self, project_id: int, min_instances: int = 2) -> int:
        sql = """
            SELECT COUNT(*) as count
            FROM (
                SELECT asset_id
                FROM media_instance
                WHERE project_id = ?
                GROUP BY asset_id
                HAVING COUNT(*) >= ?
            )
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, min_instances))
            row = cur.fetchone()
            return int(row["count"]) if row else 0

    def delete_asset(self, project_id: int, asset_id: int) -> bool:
        """Delete an asset by its ID within a project."""
        sql = "DELETE FROM media_asset WHERE asset_id = ? AND project_id = ?"
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (asset_id, project_id))
            conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            self.logger.info(f"Deleted asset {asset_id} from project {project_id}")
        return deleted

    # ── Backfill Support ──────────────────────────────────────────────────

    def get_photos_without_instance(self, project_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        sql = """
            SELECT pm.id, pm.path, pm.file_hash, pm.size_kb, pm.project_id
            FROM photo_metadata pm
            LEFT JOIN media_instance mi ON mi.photo_id = pm.id AND mi.project_id = pm.project_id
            WHERE pm.project_id = ? AND mi.instance_id IS NULL
            LIMIT ?
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id, limit))
            return [dict(r) for r in cur.fetchall()]

    def count_photos_without_instance(self, project_id: int) -> int:
        sql = """
            SELECT COUNT(*) AS count
            FROM photo_metadata pm
            LEFT JOIN media_instance mi ON mi.photo_id = pm.id AND mi.project_id = pm.project_id
            WHERE pm.project_id = ? AND mi.instance_id IS NULL
        """
        with self.connection(read_only=True) as conn:
            cur = conn.execute(sql, (project_id,))
            row = cur.fetchone()
            return int(row["count"]) if row else 0
