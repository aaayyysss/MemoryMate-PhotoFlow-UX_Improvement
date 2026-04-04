"""
RerankingService - Relevance Feedback and Result Re-ranking

Version: 1.0.0
Date: 2026-01-01

Service for improving search results through user feedback (relevance feedback).

Features:
- Batch re-ranking based on positive/negative examples
- Query embedding refinement using Rocchio algorithm
- Persistent learning (stores user preferences)

Rocchio Algorithm:
    Q' = α·Q + β·(1/|D+|)·Σ(D+) - γ·(1/|D-|)·Σ(D-)

    Where:
    - Q = original query embedding
    - D+ = set of relevant (positive) photo embeddings
    - D- = set of non-relevant (negative) photo embeddings
    - α, β, γ = weighting parameters

Usage:
    from services.reranking_service import get_reranking_service

    service = get_reranking_service()

    # Re-rank results with user feedback
    refined_results = service.rerank_with_feedback(
        original_query_embedding=query_embedding,
        positive_photo_ids=[1, 5, 12],  # User selected as relevant
        negative_photo_ids=[3, 8],      # User selected as not relevant
        candidate_photo_ids=all_photo_ids,
        alpha=1.0,  # Original query weight
        beta=0.75,  # Positive examples weight
        gamma=0.25  # Negative examples weight
    )
"""

import numpy as np
from typing import List, Tuple, Optional
from pathlib import Path

from services.embedding_service import get_embedding_service
from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


class RerankingService:
    """
    Service for re-ranking search results based on user feedback.

    Uses Rocchio algorithm for relevance feedback.
    """

    def __init__(self, db_connection: Optional[DatabaseConnection] = None):
        """
        Initialize reranking service.

        Args:
            db_connection: Optional database connection
        """
        self.db = db_connection or DatabaseConnection()
        self.embedding_service = get_embedding_service()

    def get_photo_embedding(self, photo_id: int, model_id: int) -> Optional[np.ndarray]:
        """
        Retrieve embedding for a photo from database.

        Args:
            photo_id: Photo ID
            model_id: Model ID

        Returns:
            Embedding as numpy array, or None if not found
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT embedding FROM photo_embedding
                    WHERE photo_id = ? AND model_id = ? AND embedding_type = 'visual_semantic'
                """, (photo_id, model_id))

                row = cursor.fetchone()
                if not row:
                    logger.warning(f"[Reranking] No embedding found for photo {photo_id}")
                    return None

                # Deserialize - handle both bytes and string formats
                embedding_blob = row[0]
                if isinstance(embedding_blob, str):
                    # SQLite returned as string - try multiple conversion methods
                    try:
                        embedding_blob = bytes.fromhex(embedding_blob)
                    except (ValueError, TypeError):
                        # Raw binary string - encode to bytes using latin1
                        embedding_blob = embedding_blob.encode('latin1')

                # Validate buffer size - must be multiple of 4 (float32)
                # Support different model dimensions (512-D or 768-D)
                if len(embedding_blob) % 4 != 0:
                    logger.error(
                        f"[Reranking] Photo {photo_id}: Invalid embedding size "
                        f"{len(embedding_blob)} bytes (not a multiple of 4, cannot be float32 array)"
                    )
                    return None

                embedding = np.frombuffer(embedding_blob, dtype=np.float32)

                # Log dimension for debugging
                dimension = len(embedding)
                if dimension not in [512, 768]:
                    logger.warning(
                        f"[Reranking] Photo {photo_id}: Unusual embedding dimension {dimension} "
                        f"(expected 512 or 768)"
                    )

                return embedding

        except Exception as e:
            logger.error(f"[Reranking] Failed to get embedding for photo {photo_id}: {e}")
            return None

    def compute_centroid(self, embeddings: List[np.ndarray]) -> np.ndarray:
        """
        Compute centroid (mean) of a set of embeddings.

        Args:
            embeddings: List of embedding vectors

        Returns:
            Centroid embedding (normalized)
        """
        if not embeddings:
            raise ValueError("Cannot compute centroid of empty embedding set")

        # Stack and compute mean
        stacked = np.vstack(embeddings)
        centroid = np.mean(stacked, axis=0)

        # Normalize
        centroid = centroid / np.linalg.norm(centroid)

        return centroid

    def rocchio_refinement(self,
                          original_query: np.ndarray,
                          positive_embeddings: List[np.ndarray],
                          negative_embeddings: Optional[List[np.ndarray]] = None,
                          alpha: float = 1.0,
                          beta: float = 0.75,
                          gamma: float = 0.25) -> np.ndarray:
        """
        Refine query using Rocchio algorithm.

        Args:
            original_query: Original query embedding
            positive_embeddings: Embeddings of relevant photos
            negative_embeddings: Embeddings of non-relevant photos
            alpha: Weight for original query (default: 1.0)
            beta: Weight for positive examples (default: 0.75)
            gamma: Weight for negative examples (default: 0.25)

        Returns:
            Refined query embedding (normalized)
        """
        logger.info(
            f"[Reranking] Rocchio refinement: "
            f"{len(positive_embeddings)} positive, "
            f"{len(negative_embeddings) if negative_embeddings else 0} negative"
        )

        # Start with original query
        refined = alpha * original_query

        # Add positive centroid
        if positive_embeddings:
            pos_centroid = self.compute_centroid(positive_embeddings)
            refined += beta * pos_centroid
            logger.debug(f"[Reranking] Added positive centroid with weight {beta}")

        # Subtract negative centroid
        if negative_embeddings:
            neg_centroid = self.compute_centroid(negative_embeddings)
            refined -= gamma * neg_centroid
            logger.debug(f"[Reranking] Subtracted negative centroid with weight {gamma}")

        # Normalize result
        refined = refined / np.linalg.norm(refined)

        return refined

    def rerank_with_feedback(self,
                            original_query_embedding: np.ndarray,
                            positive_photo_ids: List[int],
                            negative_photo_ids: Optional[List[int]] = None,
                            candidate_photo_ids: Optional[List[int]] = None,
                            model_id: Optional[int] = None,
                            alpha: float = 1.0,
                            beta: float = 0.75,
                            gamma: float = 0.25,
                            top_k: Optional[int] = None) -> List[Tuple[int, float]]:
        """
        Re-rank search results using user feedback.

        Args:
            original_query_embedding: Original query embedding
            positive_photo_ids: Photo IDs marked as relevant
            negative_photo_ids: Photo IDs marked as not relevant
            candidate_photo_ids: Optional subset of photos to re-rank (defaults to all)
            model_id: Model ID (uses current CLIP model if None)
            alpha: Original query weight
            beta: Positive examples weight
            gamma: Negative examples weight
            top_k: Return top K results (None = return all)

        Returns:
            List of (photo_id, new_similarity_score) tuples, sorted by score descending
        """
        if model_id is None:
            model_id = self.embedding_service._clip_model_id
            if model_id is None:
                raise ValueError("No model loaded - call load_clip_model() first")

        # Collect positive embeddings
        positive_embeddings = []
        for photo_id in positive_photo_ids:
            emb = self.get_photo_embedding(photo_id, model_id)
            if emb is not None:
                positive_embeddings.append(emb)

        if not positive_embeddings:
            logger.warning("[Reranking] No positive embeddings found - returning original results")
            # Fallback: use original query
            return self.embedding_service.search_similar(
                original_query_embedding,
                top_k=top_k or 100,
                model_id=model_id,
                photo_ids=candidate_photo_ids
            )

        # Collect negative embeddings
        negative_embeddings = []
        if negative_photo_ids:
            for photo_id in negative_photo_ids:
                emb = self.get_photo_embedding(photo_id, model_id)
                if emb is not None:
                    negative_embeddings.append(emb)

        # Refine query using Rocchio
        refined_query = self.rocchio_refinement(
            original_query_embedding,
            positive_embeddings,
            negative_embeddings if negative_embeddings else None,
            alpha=alpha,
            beta=beta,
            gamma=gamma
        )

        logger.info("[Reranking] Query refined, re-ranking results...")

        # Re-search with refined query
        results = self.embedding_service.search_similar(
            refined_query,
            top_k=top_k or 1000,
            model_id=model_id,
            photo_ids=candidate_photo_ids
        )

        logger.info(f"[Reranking] Re-ranked {len(results)} photos")

        return results

    def boost_similar_to_examples(self,
                                  example_photo_ids: List[int],
                                  model_id: Optional[int] = None,
                                  top_k: int = 100) -> List[Tuple[int, float]]:
        """
        Find photos similar to a set of example photos.

        This is essentially a "find more like these" feature.

        Args:
            example_photo_ids: Photo IDs to use as examples
            model_id: Model ID
            top_k: Number of results to return

        Returns:
            List of (photo_id, similarity_score) tuples
        """
        if model_id is None:
            model_id = self.embedding_service._clip_model_id
            if model_id is None:
                raise ValueError("No model loaded")

        # Collect embeddings for examples
        example_embeddings = []
        for photo_id in example_photo_ids:
            emb = self.get_photo_embedding(photo_id, model_id)
            if emb is not None:
                example_embeddings.append(emb)

        if not example_embeddings:
            logger.warning("[Reranking] No example embeddings found")
            return []

        # Compute centroid of examples
        query_embedding = self.compute_centroid(example_embeddings)

        logger.info(f"[Reranking] Finding photos similar to {len(example_embeddings)} examples...")

        # Search using centroid
        results = self.embedding_service.search_similar(
            query_embedding,
            top_k=top_k,
            model_id=model_id
        )

        # Filter out the examples themselves
        results = [(pid, score) for pid, score in results if pid not in example_photo_ids]

        logger.info(f"[Reranking] Found {len(results)} similar photos")

        return results


# Singleton instance
_reranking_service = None


def get_reranking_service() -> RerankingService:
    """
    Get singleton reranking service instance.

    Returns:
        RerankingService instance
    """
    global _reranking_service
    if _reranking_service is None:
        _reranking_service = RerankingService()
    return _reranking_service
