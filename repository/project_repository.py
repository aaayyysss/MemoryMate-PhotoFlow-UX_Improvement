# repository/project_repository.py
# Version 01.00.00.00 dated 20251102
# Repository for projects and branches

from typing import Optional, List, Dict, Any
from datetime import datetime
from .base_repository import BaseRepository
from logging_config import get_logger

logger = get_logger(__name__)


class ProjectRepository(BaseRepository):
    """
    Repository for projects table operations.

    Handles project CRUD and related branch operations.
    """

    def _table_name(self) -> str:
        return "projects"

    # Default semantic model for new projects (canonical HuggingFace ID)
    DEFAULT_SEMANTIC_MODEL = "openai/clip-vit-base-patch32"

    def _get_best_available_model(self) -> str:
        """
        Detect the highest-tier CLIP model available on the system.
        Priority: Large > Base-patch16 > Base-patch32
        """
        from utils.clip_model_registry import CLIP_VIT_L14, CLIP_VIT_B16, CLIP_VIT_B32
        from pathlib import Path
        import os

        # Use app-relative models directory
        app_root = Path(__file__).parent.parent.absolute()

        # Tiers in descending order of quality
        tiers = [CLIP_VIT_L14, CLIP_VIT_B16, CLIP_VIT_B32]

        for model_id in tiers:
            # Check for direct folder (HF-style or bare name)
            folder_name = model_id.replace("/", "--")
            bare_name = model_id.split("/")[-1]

            paths_to_check = [
                app_root / "models" / folder_name,
                app_root / "models" / bare_name,
                app_root / "Model" / folder_name,
                app_root / "Model" / bare_name,
                app_root / "model" / folder_name,
                app_root / "model" / bare_name,
            ]

            # Also check HuggingFace default cache location
            home = Path.home()
            paths_to_check.extend([
                home / ".cache" / "huggingface" / "hub" / f"models--{folder_name}",
                home / ".cache" / "huggingface" / "transformers" / folder_name,
            ])

            for p in paths_to_check:
                # Basic check: does config.json exist in this folder or a snapshot?
                if p.exists():
                    # Check for direct weights
                    if (p / "config.json").exists():
                        return model_id

                    # Check for HF snapshots
                    snapshots_dir = p / "snapshots"
                    if snapshots_dir.exists() and snapshots_dir.is_dir():
                        try:
                            if any(d.is_dir() for d in snapshots_dir.iterdir()):
                                return model_id
                        except Exception:
                            pass

        return self.DEFAULT_SEMANTIC_MODEL

    def create(self, name: str, folder: str, mode: str, semantic_model: Optional[str] = None) -> int:
        """
        Create a new project.

        Args:
            name: Project name
            folder: Root folder path
            mode: Project mode (date, faces, etc.)
            semantic_model: Canonical semantic embedding model (defaults to highest available)

        Returns:
            New project ID
        """
        if semantic_model is None:
            semantic_model = self._get_best_available_model()

        sql = """
            INSERT INTO projects (name, folder, mode, created_at, semantic_model)
            VALUES (?, ?, ?, ?, ?)
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (name, folder, mode, datetime.now().isoformat(), semantic_model))
            conn.commit()
            project_id = cur.lastrowid

        self.logger.info(f"Created project: {name} (id={project_id}, semantic_model={semantic_model})")
        return project_id

    def get_all_with_details(self) -> List[Dict[str, Any]]:
        """
        Get all projects with branch and image counts.

        Returns:
            List of projects with additional metadata

        Performance: Uses direct project_id from photo_metadata (schema v3.2.0+)
        instead of JOINing to project_images. Uses compound index
        idx_photo_metadata_project for fast counting.
        """
        sql = """
            SELECT
                p.id,
                p.name,
                p.folder,
                p.mode,
                p.created_at,
                COUNT(DISTINCT b.id) as branch_count,
                COUNT(DISTINCT pm.id) as image_count
            FROM projects p
            LEFT JOIN branches b ON b.project_id = p.id
            LEFT JOIN photo_metadata pm ON pm.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchall()

    def get_branches(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all branches for a project.

        Args:
            project_id: Project ID

        Returns:
            List of branches
        """
        sql = """
            SELECT branch_key, display_name
            FROM branches
            WHERE project_id = ?
            ORDER BY branch_key ASC
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id,))
            return cur.fetchall()

    def ensure_branch(self, project_id: int, branch_key: str, display_name: str) -> int:
        """
        Ensure a branch exists for a project.

        Args:
            project_id: Project ID
            branch_key: Unique branch identifier
            display_name: Human-readable name

        Returns:
            Branch ID
        """
        # Check if exists
        sql_check = """
            SELECT id FROM branches
            WHERE project_id = ? AND branch_key = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_check, (project_id, branch_key))
            existing = cur.fetchone()

            if existing:
                return existing['id']

            # Create new
            sql_insert = """
                INSERT INTO branches (project_id, branch_key, display_name)
                VALUES (?, ?, ?)
            """

            cur.execute(sql_insert, (project_id, branch_key, display_name))
            conn.commit()
            branch_id = cur.lastrowid

        self.logger.debug(f"Created branch: {branch_key} for project {project_id}")
        return branch_id

    def get_branch_by_key(self, project_id: int, branch_key: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific branch by key.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            Branch dict or None
        """
        sql = """
            SELECT * FROM branches
            WHERE project_id = ? AND branch_key = ?
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, branch_key))
            return cur.fetchone()

    def get_branch_image_count(self, project_id: int, branch_key: str) -> int:
        """
        Get number of images in a branch.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            Number of images
        """
        sql = """
            SELECT COUNT(*) as count
            FROM project_images pi
            JOIN branches b ON b.id = pi.branch_id
            WHERE b.project_id = ? AND b.branch_key = ?
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, branch_key))
            result = cur.fetchone()
            return result['count'] if result else 0

    def add_image_to_branch(self, branch_id: int, photo_id: int) -> bool:
        """
        Add an image to a branch.

        Args:
            branch_id: Branch ID
            photo_id: Photo ID

        Returns:
            True if added, False if already exists
        """
        sql = """
            INSERT OR IGNORE INTO project_images (project_id, branch_id, photo_id)
            SELECT b.project_id, ?, ?
            FROM branches b
            WHERE b.id = ?
        """

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (branch_id, photo_id, branch_id))
            conn.commit()
            added = cur.rowcount > 0

        if added:
            self.logger.debug(f"Added photo {photo_id} to branch {branch_id}")

        return added

    def bulk_add_images_to_branch(self, branch_id: int, photo_ids: List[int]) -> int:
        """
        Add multiple images to a branch.

        Args:
            branch_id: Branch ID
            photo_ids: List of photo IDs

        Returns:
            Number of images added
        """
        if not photo_ids:
            return 0

        # First get the project_id for this branch
        sql_get_project = "SELECT project_id FROM branches WHERE id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_get_project, (branch_id,))
            result = cur.fetchone()

            if not result:
                self.logger.warning(f"Branch {branch_id} not found")
                return 0

            project_id = result['project_id']

            # Bulk insert
            sql_insert = """
                INSERT OR IGNORE INTO project_images (project_id, branch_id, photo_id)
                VALUES (?, ?, ?)
            """

            rows = [(project_id, branch_id, photo_id) for photo_id in photo_ids]
            cur.executemany(sql_insert, rows)
            conn.commit()
            added = cur.rowcount

        self.logger.info(f"Added {added} images to branch {branch_id}")
        return added

    def remove_image_from_branch(self, branch_id: int, photo_id: int) -> bool:
        """
        Remove an image from a branch.

        Args:
            branch_id: Branch ID
            photo_id: Photo ID

        Returns:
            True if removed, False if not found
        """
        sql = "DELETE FROM project_images WHERE branch_id = ? AND photo_id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (branch_id, photo_id))
            conn.commit()
            removed = cur.rowcount > 0

        if removed:
            self.logger.debug(f"Removed photo {photo_id} from branch {branch_id}")

        return removed

    def delete_branch(self, branch_id: int) -> bool:
        """
        Delete a branch and all its image associations.

        Args:
            branch_id: Branch ID

        Returns:
            True if deleted
        """
        with self.connection() as conn:
            cur = conn.cursor()

            # Delete image associations first
            cur.execute("DELETE FROM project_images WHERE branch_id = ?", (branch_id,))

            # Delete branch
            cur.execute("DELETE FROM branches WHERE id = ?", (branch_id,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted branch {branch_id}")

        return deleted

    # =========================================================================
    # SEMANTIC MODEL MANAGEMENT (v9.1.0)
    # =========================================================================

    def get_semantic_model(self, project_id: int) -> str:
        """
        Get the canonical semantic embedding model for a project.

        This is the single source of truth for which model should be used
        for all embedding operations in this project.

        Args:
            project_id: Project ID

        Returns:
            Canonical HuggingFace model ID (e.g. 'openai/clip-vit-base-patch32')
            Falls back to DEFAULT_SEMANTIC_MODEL if not set
        """
        from utils.clip_model_registry import normalize_model_id

        sql = "SELECT semantic_model FROM projects WHERE id = ?"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id,))
            result = cur.fetchone()

            if result and result.get('semantic_model'):
                # Normalize old short names to canonical HF IDs
                return normalize_model_id(result['semantic_model'])

        best_model = self._get_best_available_model()
        self.logger.debug(
            f"Project {project_id} has no semantic_model set, "
            f"using best available: {best_model}"
        )
        return best_model

    def set_semantic_model(self, project_id: int, model_name: str) -> bool:
        """
        Set the canonical semantic embedding model for a project.

        WARNING: Changing the model does NOT automatically reindex embeddings.
        Use trigger_semantic_reindex() after changing the model.

        Args:
            project_id: Project ID
            model_name: New model name (e.g., 'clip-vit-b32', 'clip-vit-l14')

        Returns:
            True if updated successfully
        """
        sql = "UPDATE projects SET semantic_model = ? WHERE id = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (model_name, project_id))
            conn.commit()
            updated = cur.rowcount > 0

        if updated:
            self.logger.info(
                f"Project {project_id} semantic model changed to: {model_name}. "
                f"NOTE: Reindex required for embeddings to use new model."
            )
        else:
            self.logger.warning(f"Failed to update semantic_model for project {project_id}")

        return updated

    def get_by_id(self, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a project by ID.

        Args:
            project_id: Project ID

        Returns:
            Project dict with id, name, folder, mode, created_at, semantic_model
            or None if not found
        """
        sql = "SELECT * FROM projects WHERE id = ?"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id,))
            return cur.fetchone()

    def get_embedding_model_mismatch_count(self, project_id: int) -> Dict[str, Any]:
        """
        Check for embeddings that don't match the project's canonical model.

        This detects vector space contamination where embeddings were created
        with a different model than the project's current canonical model.

        Args:
            project_id: Project ID

        Returns:
            Dict with:
                - canonical_model: The project's canonical model
                - total_embeddings: Total embeddings for this project
                - mismatched_embeddings: Embeddings using a different model
                - models_in_use: Dict of model -> count for all models in use
        """
        canonical_model = self.get_semantic_model(project_id)

        sql = """
            SELECT se.model, COUNT(*) as count
            FROM semantic_embeddings se
            JOIN photo_metadata pm ON pm.id = se.photo_id
            WHERE pm.project_id = ?
            GROUP BY se.model
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id,))
            rows = cur.fetchall()

        models_in_use = {}
        total_embeddings = 0
        mismatched_embeddings = 0

        for row in rows:
            model = row['model']
            count = row['count']
            models_in_use[model] = count
            total_embeddings += count
            if model != canonical_model:
                mismatched_embeddings += count

        return {
            'canonical_model': canonical_model,
            'total_embeddings': total_embeddings,
            'mismatched_embeddings': mismatched_embeddings,
            'models_in_use': models_in_use
        }

    def get_photos_needing_reindex(self, project_id: int) -> List[int]:
        """
        Get photo IDs that need reindexing because they either:
        1. Have no embedding at all
        2. Have an embedding from a different model than the canonical model

        Args:
            project_id: Project ID

        Returns:
            List of photo_ids that need reindexing
        """
        canonical_model = self.get_semantic_model(project_id)

        # Find photos without embeddings or with wrong model
        sql = """
            SELECT pm.id
            FROM photo_metadata pm
            LEFT JOIN semantic_embeddings se ON pm.id = se.photo_id
            WHERE pm.project_id = ?
              AND (se.photo_id IS NULL OR se.model != ?)
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (project_id, canonical_model))
            rows = cur.fetchall()

        photo_ids = [row['id'] for row in rows]
        self.logger.info(
            f"Project {project_id} has {len(photo_ids)} photos needing reindex "
            f"for canonical model '{canonical_model}'"
        )
        return photo_ids

    def change_semantic_model(self, project_id: int, new_model: str, keep_old_embeddings: bool = True) -> Dict[str, Any]:
        """
        Change a project's canonical semantic model.

        This is the proper way to change models - it:
        1. Updates the project's semantic_model setting
        2. Returns information about what needs to be reindexed
        3. Optionally keeps old embeddings (for comparison/rollback)

        After calling this method, you should:
        1. Enqueue a semantic embedding job for the project
        2. Invalidate any cached similarity services

        Args:
            project_id: Project ID
            new_model: New model name (e.g., 'clip-vit-l14')
            keep_old_embeddings: If True, keeps old embeddings (recommended for rollback)
                               If False, deletes embeddings that don't match new model

        Returns:
            Dict with:
                - old_model: Previous canonical model
                - new_model: New canonical model
                - photos_to_reindex: Number of photos needing new embeddings
                - embeddings_to_delete: Number of embeddings to delete (if keep_old=False)
        """
        old_model = self.get_semantic_model(project_id)

        if old_model == new_model:
            self.logger.info(
                f"Project {project_id} already uses model '{new_model}', no change needed"
            )
            return {
                'old_model': old_model,
                'new_model': new_model,
                'photos_to_reindex': 0,
                'embeddings_to_delete': 0
            }

        # Count photos that will need reindexing
        sql_count_photos = """
            SELECT COUNT(*) as count
            FROM photo_metadata
            WHERE project_id = ?
        """

        # Count embeddings using the old model
        sql_count_old_embeddings = """
            SELECT COUNT(*) as count
            FROM semantic_embeddings se
            JOIN photo_metadata pm ON pm.id = se.photo_id
            WHERE pm.project_id = ? AND se.model != ?
        """

        with self.connection() as conn:
            cur = conn.cursor()

            # Count total photos
            cur.execute(sql_count_photos, (project_id,))
            total_photos = cur.fetchone()['count']

            # Count old embeddings (will be deleted if keep_old=False)
            cur.execute(sql_count_old_embeddings, (project_id, new_model))
            old_embeddings = cur.fetchone()['count']

            # Update the project's canonical model
            cur.execute(
                "UPDATE projects SET semantic_model = ? WHERE id = ?",
                (new_model, project_id)
            )

            # Delete old embeddings if requested
            embeddings_deleted = 0
            if not keep_old_embeddings:
                cur.execute("""
                    DELETE FROM semantic_embeddings
                    WHERE photo_id IN (
                        SELECT id FROM photo_metadata WHERE project_id = ?
                    ) AND model != ?
                """, (project_id, new_model))
                embeddings_deleted = cur.rowcount

            conn.commit()

        self.logger.info(
            f"Project {project_id} semantic model changed: {old_model} -> {new_model}. "
            f"Photos to reindex: {total_photos}. "
            f"Old embeddings {'deleted' if not keep_old_embeddings else 'kept'}: {old_embeddings}"
        )

        return {
            'old_model': old_model,
            'new_model': new_model,
            'photos_to_reindex': total_photos,
            'embeddings_to_delete': embeddings_deleted if not keep_old_embeddings else old_embeddings
        }
