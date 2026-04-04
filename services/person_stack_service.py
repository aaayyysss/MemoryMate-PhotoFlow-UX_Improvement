# services/person_stack_service.py
# Person-based photo grouping service
# Groups photos by person (face clusters) with similarity filtering

"""
PersonStackService - Face-based photo grouping

Provides person-centric similar photo management where:
- Each person (branch_key) represents a group
- Photos are filtered by face similarity threshold
- Similarity is computed using face embeddings

Based on best practices from Google Photos "People" feature.
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
from logging_config import get_logger
import numpy as np

logger = get_logger(__name__)


class PersonStackService:
    """
    Service for person-based photo grouping with similarity filtering.

    Features:
    - Get all photos for a specific person
    - Compute face similarity scores
    - Filter photos by similarity threshold
    - Representative face management
    """

    def __init__(self, db):
        """
        Initialize PersonStackService.

        Args:
            db: ReferenceDB instance
        """
        self.db = db

    def get_person_photos(
        self,
        project_id: int,
        branch_key: str,
        similarity_threshold: float = 0.0
    ) -> Dict[str, Any]:
        """
        Get all photos for a person with similarity scores.

        Args:
            project_id: Project ID
            branch_key: Person identifier (branch_key from face_branch_reps)
            similarity_threshold: Minimum similarity to include (0.0-1.0)

        Returns:
            Dictionary with:
            - branch_key: Person identifier
            - display_name: Person's name
            - representative_path: Path to representative face photo
            - photos: List of photo dicts with:
                - image_path: Path to photo
                - similarity_score: Similarity to representative (0.0-1.0)
                - is_representative: Boolean
        """
        try:
            # Get person info (representative, name, etc.)
            person_info = self._get_person_info(project_id, branch_key)
            if not person_info:
                logger.warning(f"Person {branch_key} not found in project {project_id}")
                return {
                    'branch_key': branch_key,
                    'display_name': branch_key,
                    'representative_path': None,
                    'photos': []
                }

            # Get all photos for this person
            photo_paths = self.db.get_images_by_branch(project_id, branch_key)

            if not photo_paths:
                logger.info(f"No photos found for person {branch_key}")
                return {
                    'branch_key': branch_key,
                    'display_name': person_info['display_name'],
                    'representative_path': person_info.get('rep_path'),
                    'photos': []
                }

            # Get face crops for similarity computation
            face_data = self._get_face_data_for_person(project_id, branch_key)

            # Compute similarity scores
            photos_with_scores = self._compute_similarity_scores(
                photo_paths=photo_paths,
                face_data=face_data,
                representative_path=person_info.get('rep_path')
            )

            # Filter by threshold
            filtered_photos = [
                photo for photo in photos_with_scores
                if photo['is_representative'] or photo['similarity_score'] >= similarity_threshold
            ]

            # Sort by similarity (highest first)
            filtered_photos.sort(key=lambda x: x['similarity_score'], reverse=True)

            return {
                'branch_key': branch_key,
                'display_name': person_info['display_name'],
                'representative_path': person_info.get('rep_path'),
                'member_count': len(filtered_photos),
                'photos': filtered_photos
            }

        except Exception as e:
            logger.error(f"Error getting photos for person {branch_key}: {e}", exc_info=True)
            return {
                'branch_key': branch_key,
                'display_name': branch_key,
                'representative_path': None,
                'photos': []
            }

    def _get_person_info(self, project_id: int, branch_key: str) -> Optional[Dict[str, Any]]:
        """Get person information from face_branch_reps."""
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT
                        branch_key,
                        COALESCE(label, branch_key) AS display_name,
                        count AS member_count,
                        rep_path,
                        rep_thumb_png
                    FROM face_branch_reps
                    WHERE project_id = ? AND branch_key = ?
                """, (project_id, branch_key))

                row = cur.fetchone()
                if not row:
                    return None

                return {
                    'branch_key': row[0],
                    'display_name': row[1],
                    'member_count': row[2],
                    'rep_path': row[3],
                    'rep_thumb_png': row[4]
                }

        except Exception as e:
            logger.error(f"Error getting person info: {e}", exc_info=True)
            return None

    def _get_face_data_for_person(
        self,
        project_id: int,
        branch_key: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get face crop data with embeddings for a person.

        Returns:
            Dictionary mapping image_path -> face data
        """
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT
                        image_path,
                        embedding,
                        confidence,
                        is_representative
                    FROM face_crops
                    WHERE project_id = ? AND branch_key = ?
                """, (project_id, branch_key))

                face_data = {}
                for row in cur.fetchall():
                    image_path = row[0]
                    embedding_blob = row[1]
                    confidence = row[2]
                    is_representative = row[3]

                    # Deserialize embedding if available
                    embedding = None
                    if embedding_blob:
                        try:
                            embedding = np.frombuffer(embedding_blob, dtype=np.float32)
                        except Exception as e:
                            logger.warning(f"Failed to load embedding for {image_path}: {e}")

                    face_data[image_path] = {
                        'embedding': embedding,
                        'confidence': confidence,
                        'is_representative': bool(is_representative)
                    }

                return face_data

        except Exception as e:
            logger.error(f"Error getting face data: {e}", exc_info=True)
            return {}

    def _compute_similarity_scores(
        self,
        photo_paths: List[str],
        face_data: Dict[str, Dict[str, Any]],
        representative_path: Optional[str]
    ) -> List[Dict[str, Any]]:
        """
        Compute similarity scores for photos based on face embeddings.

        Args:
            photo_paths: List of photo paths
            face_data: Face data with embeddings (keyed by image_path)
            representative_path: Path to representative photo

        Returns:
            List of photo dicts with similarity scores
        """
        # Get representative embedding
        rep_embedding = None
        if representative_path and representative_path in face_data:
            rep_embedding = face_data[representative_path].get('embedding')

        photos_with_scores = []

        for photo_path in photo_paths:
            # Check if this photo has face data
            face_info = face_data.get(photo_path, {})
            embedding = face_info.get('embedding')
            is_rep = face_info.get('is_representative', False)

            # Compute similarity if both embeddings available
            similarity_score = 1.0 if is_rep else 0.0

            if embedding is not None and rep_embedding is not None and not is_rep:
                try:
                    # Normalize embeddings
                    embedding_norm = embedding / np.linalg.norm(embedding)
                    rep_embedding_norm = rep_embedding / np.linalg.norm(rep_embedding)

                    # Cosine similarity
                    similarity_score = float(np.dot(embedding_norm, rep_embedding_norm))

                    # Clamp to [0, 1]
                    similarity_score = max(0.0, min(1.0, similarity_score))

                except Exception as e:
                    logger.warning(f"Failed to compute similarity for {photo_path}: {e}")
                    similarity_score = 0.5  # Default moderate similarity

            photos_with_scores.append({
                'image_path': photo_path,
                'similarity_score': similarity_score,
                'is_representative': is_rep,
                'confidence': face_info.get('confidence', 0.0)
            })

        return photos_with_scores

    def get_all_people(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all people (face clusters) in a project.

        Args:
            project_id: Project ID

        Returns:
            List of person dicts with:
            - branch_key: Person identifier
            - display_name: Person's name
            - member_count: Number of photos
            - rep_path: Representative photo path
            - rep_thumb_png: Thumbnail PNG blob
        """
        try:
            return self.db.get_face_clusters(project_id) or []
        except Exception as e:
            logger.error(f"Error getting all people: {e}", exc_info=True)
            return []
