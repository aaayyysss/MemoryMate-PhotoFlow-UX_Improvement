# services/photo_similarity_service.py
# Version: 1.0.1 dated 20260128
 
"""
PhotoSimilarityService - Visual Similarity Search

Version: 1.0.1
Date: 2026-01-28

Find visually similar photos using semantic embeddings.

Core Principle (non-negotiable):
Given photo_id A, show top_k similar photos using cosine similarity.

Architecture:
- Uses semantic_embeddings table (NOT face_crops)
- Cosine similarity on normalized vectors
- Threshold filtering for quality control
- Minimal but correct implementation

Usage:
    from services.photo_similarity_service import get_photo_similarity_service

    service = get_photo_similarity_service()

    # Find similar photos
    similar = service.find_similar(photo_id=123, top_k=20, threshold=0.7)
    # Returns: [(photo_id, similarity_score), ...]
"""

import numpy as np
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
import sqlite3

from services.semantic_embedding_service import get_semantic_embedding_service
from repository.base_repository import DatabaseConnection
from repository.project_repository import ProjectRepository
from logging_config import get_logger

logger = get_logger(__name__)


class EmbeddingNotReadyError(Exception):
    """Raised when required embeddings are missing for similarity search."""
    pass


class ModelMismatchWarning:
    """Warning data for when embeddings don't match the canonical model."""
    def __init__(self, canonical_model: str, found_model: str, photo_id: int):
        self.canonical_model = canonical_model
        self.found_model = found_model
        self.photo_id = photo_id
        self.message = (
            f"Photo {photo_id} has embedding from model '{found_model}' "
            f"but project uses canonical model '{canonical_model}'. "
            f"Reindex required for accurate similarity search."
        )


@dataclass
class SimilarPhoto:
    """Similar photo result."""
    photo_id: int
    similarity_score: float
    file_path: Optional[str] = None
    thumbnail_path: Optional[str] = None


class PhotoSimilarityService:
    """
    Service for finding visually similar photos.

    Uses semantic embeddings (CLIP/SigLIP) for similarity computation.
    Does NOT use face embeddings.

    IMPORTANT: Always use for_project() factory method to ensure
    the correct canonical model is used for the project.
    """

    def __init__(self,
                 model_name: str = "clip-vit-b32",
                 db_connection: Optional[DatabaseConnection] = None,
                 project_id: Optional[int] = None):
        """
        Initialize photo similarity service.

        NOTE: Prefer using PhotoSimilarityService.for_project() factory method
        to automatically use the project's canonical model.

        Args:
            model_name: CLIP/SigLIP model variant (must match embeddings)
            db_connection: Optional database connection
            project_id: Optional project ID (used for model validation)
        """
        self.model_name = model_name
        self.db = db_connection or DatabaseConnection()
        self.project_id = project_id
        self.embedder = get_semantic_embedding_service(model_name=model_name)

        logger.info(f"[PhotoSimilarityService] Initialized with model={model_name}")

    @classmethod
    def for_project(cls, project_id: int, db_connection: Optional[DatabaseConnection] = None) -> 'PhotoSimilarityService':
        """
        Factory method to create a PhotoSimilarityService using the project's canonical model.

        This is the RECOMMENDED way to create a PhotoSimilarityService.
        It ensures that similarity search uses the same model that was used
        to generate the embeddings for this project.

        Args:
            project_id: Project ID
            db_connection: Optional database connection

        Returns:
            PhotoSimilarityService configured with the project's canonical model
        """
        project_repo = ProjectRepository()
        canonical_model = project_repo.get_semantic_model(project_id)

        logger.info(
            f"[PhotoSimilarityService] Creating service for project {project_id} "
            f"with canonical model: {canonical_model}"
        )

        return cls(
            model_name=canonical_model,
            db_connection=db_connection,
            project_id=project_id
        )

    def _get_asset_sibling_photo_ids(self, project_id: int, photo_id: int) -> set[int]:
        """
        Return all photo_ids that belong to the same asset as photo_id.

        This is the key to separating:
        - Exact duplicates (same asset, handled by Duplicates pipeline)
        - Similar photos (different assets, handled here)

        If your schema differs or the asset tables do not exist, this fails safely.
        """
        if project_id is None:
            return set()

        try:
            with self.db.get_connection() as conn:
                # Expected schema (asset-centric system):
                # media_instance(project_id, photo_id, asset_id)
                row = conn.execute("""
                    SELECT asset_id
                    FROM media_instance
                    WHERE project_id = ? AND photo_id = ?
                    LIMIT 1
                """, (project_id, photo_id)).fetchone()

                if not row or row["asset_id"] is None:
                    return set()

                asset_id = row["asset_id"]
                sibs = conn.execute("""
                    SELECT photo_id
                    FROM media_instance
                    WHERE project_id = ? AND asset_id = ?
                """, (project_id, asset_id)).fetchall()

                return {r["photo_id"] for r in sibs if r and r["photo_id"] is not None}

        except sqlite3.OperationalError as e:
            # Schema mismatch, do not crash similarity search
            logger.debug(f"[PhotoSimilarityService] Asset sibling lookup skipped: {e}")
            return set()


    def verify_embedding_ready(self, photo_id: int, project_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """
        Verify that a photo has an embedding matching the project's canonical model.

        This is a guardrail check that should be called before similarity search
        to ensure the reference photo has a valid embedding.

        Args:
            photo_id: Photo ID to check
            project_id: Project ID (uses self.project_id if not provided)

        Returns:
            Tuple of (is_ready: bool, error_message: Optional[str])
        """
        effective_project_id = project_id or self.project_id

        # Check if embedding exists
        embedding = self.embedder.get_embedding(photo_id)
        if embedding is None:
            return (False, f"Photo {photo_id} has no embedding. Index required.")

        # If we have a project context, verify the embedding model matches
        if effective_project_id is not None:
            # Check what model was used for this embedding
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT model FROM semantic_embeddings WHERE photo_id = ?",
                    (photo_id,)
                )
                row = cursor.fetchone()
                if row:
                    embedding_model = row['model']
                    if embedding_model != self.model_name:
                        return (
                            False,
                            f"Photo {photo_id} has embedding from model '{embedding_model}' "
                            f"but project uses canonical model '{self.model_name}'. Reindex required."
                        )

        return (True, None)

    def find_similar(self,
                    photo_id: int,
                    top_k: int = 20,
                    threshold: float = 0.7,
                    include_metadata: bool = False,
                    project_id: Optional[int] = None,
                    exclude_exact_duplicates: bool = True,
                    strict_model_check: bool = True) -> List[SimilarPhoto]:
        """
        Find visually similar photos.

        Args:
            photo_id: Reference photo ID
            top_k: Number of similar photos to return
            threshold: Minimum similarity score (0.0 to 1.0)
            include_metadata: If True, fetch file paths for results
            project_id: Optional project filter (recommended)
            exclude_exact_duplicates: If True, excludes photos from the same asset
            strict_model_check: If True, verify embedding matches canonical model

        Returns:
            List of SimilarPhoto objects, sorted by similarity descending

        Raises:
            EmbeddingNotReadyError: If strict_model_check is True and embedding
                                   is missing or uses wrong model

        Algorithm:
            1. Get reference embedding for photo_id
            2. Get all other embeddings from database
            3. Compute cosine similarity (dot product of normalized vectors)
            4. Filter by threshold
            5. Return top_k results
        """
        effective_project_id = project_id or self.project_id

        # Guardrail check: Verify embedding is ready
        if strict_model_check and effective_project_id is not None:
            is_ready, error_message = self.verify_embedding_ready(photo_id, effective_project_id)
            if not is_ready:
                logger.warning(f"[PhotoSimilarityService] {error_message}")
                raise EmbeddingNotReadyError(error_message)

        # Get reference embedding
        ref_embedding = self.embedder.get_embedding(photo_id)
        if ref_embedding is None:
            logger.warning(f"[PhotoSimilarityService] Photo {photo_id} has no embedding")
            return []

        # Exclude exact duplicates (handled by Duplicates pipeline)
        exclude_ids: set[int] = set()
        if exclude_exact_duplicates and project_id is not None:
            exclude_ids = self._get_asset_sibling_photo_ids(project_id, photo_id)
        exclude_ids.add(photo_id)

        # Get all other embeddings
#        candidates = self._get_all_embeddings(exclude_photo_id=photo_id)
        candidates = self._get_all_embeddings(exclude_photo_id=photo_id, project_id=project_id)
 
        if not candidates:
            logger.warning("[PhotoSimilarityService] No other embeddings found")
            return []

        # Compute similarities
        similarities = []
        for candidate_id, candidate_embedding in candidates:
            if candidate_id in exclude_ids:
                continue
                
            # Cosine similarity = dot product (vectors are normalized)
            score = float(np.dot(ref_embedding, candidate_embedding))

            # Filter by threshold
            if score >= threshold:
                similarities.append((candidate_id, score))

        # Sort by score descending and take top_k
        similarities.sort(key=lambda x: x[1], reverse=True)
        similarities = similarities[:top_k]

        logger.info(
            f"[PhotoSimilarityService] Found {len(similarities)} similar photos "
            f"(threshold={threshold:.2f}, top_k={top_k})"
        )

        # Convert to SimilarPhoto objects
        results = []
        for candidate_id, score in similarities:
            photo = SimilarPhoto(
                photo_id=candidate_id,
                similarity_score=score
            )
            results.append(photo)

        # Fetch metadata if requested
        if include_metadata:
            self._add_metadata(results)

        return results

#    def _get_all_embeddings(self, exclude_photo_id: int) -> List[Tuple[int, np.ndarray]]:
    def _get_all_embeddings(self, exclude_photo_id: int, project_id: Optional[int] = None) -> List[Tuple[int, np.ndarray]]:
 
        """
        Get all embeddings except reference photo.

        Args:
            exclude_photo_id: Photo ID to exclude
            project_id: Optional project filter

        Returns:
            List of (photo_id, embedding) tuples
        """
        with self.db.get_connection() as conn:
#            cursor = conn.execute("""
#                SELECT photo_id, embedding, dim
#                FROM semantic_embeddings
#                WHERE photo_id != ? AND model = ?
#            """, (exclude_photo_id, self.model_name))

            # Project filter is applied via join to photo_metadata (portable and safe)
            if project_id is None:
                cursor = conn.execute("""
                    SELECT se.photo_id, se.embedding, se.dim
                    FROM semantic_embeddings se
                    WHERE se.photo_id != ? AND se.model = ?
                """, (exclude_photo_id, self.model_name))
            else:
                cursor = conn.execute("""
                    SELECT se.photo_id, se.embedding, se.dim
                    FROM semantic_embeddings se
                    JOIN photo_metadata p ON p.id = se.photo_id
                    WHERE se.photo_id != ? AND se.model = ? AND p.project_id = ?
                """, (exclude_photo_id, self.model_name, project_id))
 

            results = []
            for row in cursor.fetchall():
                photo_id = row['photo_id']
                embedding_blob = row['embedding']
                dim = row['dim']

                # Deserialize
                if isinstance(embedding_blob, str):
                    embedding_blob = embedding_blob.encode('latin1')

                # CRITICAL FIX: Handle float16 vs float32 storage format
                # Negative dim indicates float16 format (new, 50% smaller)
                # Positive dim indicates float32 format (legacy)
                if dim < 0:
                    # float16 format - deserialize and convert to float32
                    actual_dim = abs(dim)
                    embedding = np.frombuffer(embedding_blob, dtype='float16').astype('float32')
                else:
                    # float32 format (legacy)
                    actual_dim = dim
                    embedding = np.frombuffer(embedding_blob, dtype='float32')

                if len(embedding) != actual_dim:
                    logger.warning(
                        f"[PhotoSimilarityService] Dimension mismatch for photo {photo_id}: "
                        f"expected {actual_dim}, got {len(embedding)}"
                    )
                    continue

                results.append((photo_id, embedding))

            return results

    def _add_metadata(self, results: List[SimilarPhoto]):
        """
        Add file paths and thumbnail paths to results.

        Args:
            results: List of SimilarPhoto objects (modified in-place)
        """
        if not results:
            return

        photo_ids = [r.photo_id for r in results]
        placeholders = ','.join(['?'] * len(photo_ids))

        with self.db.get_connection() as conn:
            cursor = conn.execute(f"""
                SELECT id, path
                FROM photo_metadata
                WHERE id IN ({placeholders})
            """, photo_ids)

            metadata = {row['id']: row for row in cursor.fetchall()}

        # Add metadata to results
        for result in results:
            meta = metadata.get(result.photo_id)
            if meta:
                result.file_path = meta.get('path')

    def get_embedding_coverage(self) -> dict:
        """
        Get embedding coverage statistics.

        Returns:
            Dict with total_photos, embedded_photos, coverage_percent
        """
        with self.db.get_connection() as conn:
            # Total photos
            cursor = conn.execute("SELECT COUNT(*) as count FROM photo_metadata")
            total_photos = cursor.fetchone()['count']

            # Embedded photos
            cursor = conn.execute("""
                SELECT COUNT(*) as count
                FROM semantic_embeddings
                WHERE model = ?
            """, (self.model_name,))
            embedded_photos = cursor.fetchone()['count']

            coverage_percent = (embedded_photos / total_photos * 100) if total_photos > 0 else 0.0

            return {
                'total_photos': total_photos,
                'embedded_photos': embedded_photos,
                'coverage_percent': coverage_percent,
                'model': self.model_name
            }


# Singleton instances (per project)
_photo_similarity_service = None
_project_similarity_services: Dict[int, PhotoSimilarityService] = {}


def get_photo_similarity_service(model_name: str = "clip-vit-b32") -> PhotoSimilarityService:
    """
    Get singleton photo similarity service.

    NOTE: Prefer get_photo_similarity_service_for_project() for project-aware service.

    Args:
        model_name: CLIP/SigLIP model variant

    Returns:
        PhotoSimilarityService instance
    """
    global _photo_similarity_service
    if _photo_similarity_service is None:
        _photo_similarity_service = PhotoSimilarityService(model_name=model_name)
    return _photo_similarity_service


def get_photo_similarity_service_for_project(project_id: int) -> PhotoSimilarityService:
    """
    Get photo similarity service configured with the project's canonical model.

    This is the RECOMMENDED way to get a PhotoSimilarityService.
    It ensures similarity search uses the project's canonical embedding model.

    Args:
        project_id: Project ID

    Returns:
        PhotoSimilarityService configured with the project's canonical model
    """
    global _project_similarity_services

    if project_id not in _project_similarity_services:
        _project_similarity_services[project_id] = PhotoSimilarityService.for_project(project_id)

    return _project_similarity_services[project_id]


def invalidate_project_similarity_service(project_id: int):
    """
    Invalidate the cached similarity service for a project.

    Call this when the project's semantic_model changes to force
    recreation of the service with the new model.

    Args:
        project_id: Project ID
    """
    global _project_similarity_services

    if project_id in _project_similarity_services:
        del _project_similarity_services[project_id]
        logger.info(
            f"[PhotoSimilarityService] Invalidated cached service for project {project_id}. "
            f"Next access will recreate with current canonical model."
        )
