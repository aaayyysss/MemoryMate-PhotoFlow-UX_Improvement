# services/stack_generation_service.py
# Version 01.02.00.00 dated 20260208
# Similar-shot and near-duplicate stack generation service
#
# Part of the asset-centric duplicate management system.
# Generates materialized stacks for:
# - Similar shots (burst, series, pose variations)
# - Near-duplicates (pHash-based detection - future)
#
# FIX 20260208: Cross-date similarity now works correctly. Previously, photos
# clustered in time-window pass were excluded from global cross-date pass,
# preventing visually similar photos from different dates from being grouped.

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
import json

import sqlite3

from logging_config import get_logger

from repository.stack_repository import StackRepository
from repository.photo_repository import PhotoRepository
from repository.base_repository import DatabaseConnection

logger = get_logger(__name__)


@dataclass(frozen=True)
class StackGenParams:
    """Parameters for stack generation algorithms."""
    rule_version: str = "1"
    time_window_seconds: int = 300  # 5 minutes - covers burst sequences and photo series
    min_stack_size: int = 3
    top_k: int = 30
    similarity_threshold: float = 0.85  # Balanced threshold - high quality but not overly strict
    candidate_limit_per_photo: int = 300
    cross_date_similarity: bool = True  # Enable global similarity pass across all dates
    cross_date_threshold: float = 0.85  # Cross-date threshold (was 0.90, lowered for real-world CLIP embeddings)


@dataclass(frozen=True)
class StackGenStats:
    """Statistics from stack generation operation."""
    photos_considered: int
    stacks_created: int
    memberships_created: int
    errors: int


class StackGenerationService:
    """
    Generates materialized stacks for similar shots and near-duplicates.

    This service orchestrates the creation of media_stack and media_stack_member
    records based on similarity detection algorithms.

    No UI responsibilities - pure data processing.

    Algorithms:
    1. Similar shots: Time proximity + visual embedding similarity
    2. Near-duplicates: Perceptual hash distance + embedding confirmation (future)
    """

    def __init__(
        self,
        photo_repo: PhotoRepository,
        stack_repo: StackRepository,
        similarity_service: Optional[Any] = None  # PhotoSimilarityService - optional for now
    ):
        """
        Initialize StackGenerationService.

        Args:
            photo_repo: PhotoRepository instance
            stack_repo: StackRepository instance
            similarity_service: Optional PhotoSimilarityService for embeddings
        """
        self.photo_repo = photo_repo
        self.stack_repo = stack_repo
        self.similarity_service = similarity_service
        self.logger = get_logger(self.__class__.__name__)

    def _get_representative_photo_ids(self, project_id: int) -> Optional[set[int]]:
        """
        Return the set of representative photo_ids for each asset.

        This enforces the policy:
        - Similar-shot stacks operate on asset representatives
        - Exact duplicate instances are handled by the Duplicates pipeline, not Similar

        If schema differs, returns None and caller falls back to legacy behavior.
        """
        try:
            db = getattr(self.photo_repo, "db", None) or DatabaseConnection()
            with db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT a.representative_photo_id AS photo_id
                    FROM media_asset a
                    WHERE a.project_id = ?
                      AND a.representative_photo_id IS NOT NULL
                """, (project_id,)).fetchall()
                rep_ids = {r["photo_id"] for r in rows if r and r["photo_id"] is not None}
                return rep_ids if rep_ids else set()
        except sqlite3.OperationalError as e:
            self.logger.debug(f"Representative filtering skipped (schema mismatch): {e}")
            return None


    # =========================================================================
    # SIMILAR SHOT STACKS
    # =========================================================================

    def regenerate_similar_shot_stacks(
        self,
        project_id: int,
        params: StackGenParams
    ) -> StackGenStats:
        """
        Generate stacks for similar shots (burst, series, pose variations).

        Contract:
        - Clears existing stacks of type "similar" for the same rule_version
        - Builds new stacks by:
          1) Candidate selection (time window, optional folder, optional device)
          2) Cosine similarity scoring with semantic embeddings
          3) Cluster into stacks, choose representative, persist memberships

        Must be:
        - Deterministic for same params
        - Resumable (optional enhancement - not implemented yet)

        Args:
            project_id: Project ID
            params: Stack generation parameters

        Returns:
            StackGenStats with operation results

        Note: This is a stub implementation. Full implementation requires:
        - Photo embedding extraction (PhotoSimilarityService integration)
        - Time-based candidate filtering
        - Clustering algorithm (DBSCAN, hierarchical, or greedy grouping)
        """
        self.logger.info(f"Starting similar shot stack generation for project {project_id}")
        self.logger.info(f"Parameters: {params}")

        if not self.similarity_service:
            self.logger.error("Cannot generate similar shot stacks: similarity_service not provided")
            return StackGenStats(
                photos_considered=0,
                stacks_created=0,
                memberships_created=0,
                errors=1
            )

        # Step 1: Clear existing stacks
        cleared = self.stack_repo.clear_stacks_by_type(
            project_id=project_id,
            stack_type="similar",
            rule_version=params.rule_version
        )
        self.logger.info(f"Cleared {cleared} existing similar shot stacks for rule v{params.rule_version}")
        if cleared > 0:
            self.logger.warning(
                f"IMPORTANT: {cleared} stacks were deleted. UI components displaying "
                f"stack badges should be refreshed to prevent 'Stack not found' errors."
            )

        # Step 2: Get ALL photos (including those without timestamps)
        # The time-window pass will skip dateless photos, but the global
        # cross-date pass needs them to detect visually similar photos
        # regardless of metadata availability.
        # Policy: Prefer asset representatives only, to prevent exact duplicates appearing in Similar stacks
        representative_ids = self._get_representative_photo_ids(project_id)

        # Note: For large projects, might need pagination
        all_photos = self.photo_repo.find_all(
            where_clause="project_id = ?",
            params=(project_id,),
            order_by="COALESCE(created_ts, 9999999999) ASC"
        )

        if not all_photos:
            self.logger.info("No photos found in project")
            return StackGenStats(
                photos_considered=0,
                stacks_created=0,
                memberships_created=0,
                errors=0
            )

        if representative_ids is not None:
            before = len(all_photos)
            all_photos = [p for p in all_photos if p.get("id") in representative_ids]
            self.logger.info(
                f"Representative-only mode enabled: {before} → {len(all_photos)} photos considered"
            )

        timestamped = sum(1 for p in all_photos if p.get("created_ts") is not None)
        self.logger.info(f"Found {len(all_photos)} photos ({timestamped} with timestamps, {len(all_photos) - timestamped} without)")

        # Step 2.5: PERFORMANCE OPTIMIZATION - Batch load all embeddings at once
        # This reduces N database queries to just 1 query
        photo_ids = [p["id"] for p in all_photos]
        preloaded_embeddings: Dict[int, Any] = {}

        if hasattr(self.similarity_service, 'get_all_embeddings_for_project'):
            # Use efficient batch method from SemanticEmbeddingService
            preloaded_embeddings = self.similarity_service.get_all_embeddings_for_project(project_id)
            self.logger.info(f"Batch-loaded {len(preloaded_embeddings)} embeddings for similarity comparison")
        elif hasattr(self.similarity_service, 'get_embeddings_batch'):
            # Fallback to batch method with photo_ids
            preloaded_embeddings = self.similarity_service.get_embeddings_batch(photo_ids)
            self.logger.info(f"Batch-loaded {len(preloaded_embeddings)} embeddings")
        else:
            # Last resort: load one by one (legacy path)
            self.logger.warning("similarity_service doesn't support batch loading - using slow path")

        # Step 3: Find clusters using time window + similarity
        photo_to_cluster: Dict[int, int] = {}  # photo_id -> cluster_id
        all_clusters: List[List[int]] = []
        photos_processed = 0
        errors = 0

        for photo in all_photos:
            photo_id = photo["id"]
            photos_processed += 1

            # Skip if already in a cluster
            if photo_id in photo_to_cluster:
                continue

            # Get embedding from preloaded cache or fetch individually
            embedding = preloaded_embeddings.get(photo_id)

            if embedding is None:
                # Fallback to individual fetch for backwards compatibility
                if hasattr(self.similarity_service, 'get_embedding'):
                    embedding = self.similarity_service.get_embedding(photo_id)
                elif hasattr(self.similarity_service, 'embedder'):
                    embedding = self.similarity_service.embedder.get_embedding(photo_id)

            if embedding is None:
                continue

            # Find candidates within time window
            created_ts = photo.get("created_ts")
            if not created_ts:
                continue

            candidates = self._find_time_candidates(
                project_id=project_id,
                reference_timestamp=created_ts,
                time_window_seconds=params.time_window_seconds,
                reference_photo_id=photo_id,
                folder_id=photo.get("folder_id")  # Same folder only
            )

            if not candidates:
                continue

            # Include reference photo in clustering
            all_candidates = [photo] + candidates

            # Cluster by similarity (pass preloaded embeddings to avoid N+1 queries)
            clusters = self._cluster_by_similarity(
                photos=all_candidates,
                similarity_threshold=params.similarity_threshold,
                min_cluster_size=params.min_stack_size,
                preloaded_embeddings=preloaded_embeddings
            )

            # Register clusters
            for cluster in clusters:
                cluster_id = len(all_clusters)
                all_clusters.append(cluster)

                for pid in cluster:
                    photo_to_cluster[pid] = cluster_id

        self.logger.info(f"Found {len(all_clusters)} similar shot clusters (time-window pass)")

        # Step 3b: Global similarity pass — find visually similar photos across ALL dates
        # FIX (2026-02-08): Now considers ALL photos with embeddings to enable cross-date grouping
        if params.cross_date_similarity and preloaded_embeddings:
            global_clusters = self._cluster_globally_by_similarity(
                all_photos=all_photos,
                preloaded_embeddings=preloaded_embeddings,
                already_clustered=photo_to_cluster,
                similarity_threshold=params.cross_date_threshold,
                min_cluster_size=params.min_stack_size,
            )

            # Track which time-window clusters are subsumed by global clusters
            subsumed_cluster_ids = set()

            for cluster in global_clusters:
                # Find which time-window clusters are fully contained in this global cluster
                cluster_set = set(cluster)
                for tw_cluster_id, tw_cluster in enumerate(all_clusters):
                    tw_cluster_set = set(tw_cluster)
                    if tw_cluster_set.issubset(cluster_set) and len(tw_cluster_set) < len(cluster_set):
                        # This time-window cluster is subsumed by the global cluster
                        subsumed_cluster_ids.add(tw_cluster_id)

            # Remove subsumed time-window clusters (they'll be replaced by larger global clusters)
            if subsumed_cluster_ids:
                self.logger.info(
                    f"Removing {len(subsumed_cluster_ids)} time-window clusters subsumed by global clusters"
                )
                all_clusters = [
                    c for i, c in enumerate(all_clusters)
                    if i not in subsumed_cluster_ids
                ]
                # Rebuild photo_to_cluster mapping
                photo_to_cluster.clear()
                for cluster_id, cluster in enumerate(all_clusters):
                    for pid in cluster:
                        photo_to_cluster[pid] = cluster_id

            # Add global clusters
            for cluster in global_clusters:
                cluster_id = len(all_clusters)
                all_clusters.append(cluster)
                for pid in cluster:
                    photo_to_cluster[pid] = cluster_id

            self.logger.info(
                f"Global cross-date pass found {len(global_clusters)} additional clusters "
                f"(threshold={params.cross_date_threshold:.2f})"
            )

        self.logger.info(f"Total similar shot clusters: {len(all_clusters)}")

        # Step 4: Create stacks in database
        stacks_created = 0
        memberships_created = 0

        for cluster in all_clusters:
            try:
                # Choose representative
                rep_photo_id = self._choose_stack_representative(project_id, cluster)
                if not rep_photo_id:
                    self.logger.warning(f"Could not choose representative for cluster {cluster}")
                    errors += 1
                    continue

                # Create stack
                stack_id = self.stack_repo.create_stack(
                    project_id=project_id,
                    stack_type="similar",
                    representative_photo_id=rep_photo_id,
                    rule_version=params.rule_version,
                    created_by="system"
                )

                stacks_created += 1

                # Add members (use preloaded embeddings for efficiency)
                import numpy as np
                for photo_id in cluster:
                    # Compute similarity score to representative
                    if photo_id == rep_photo_id:
                        similarity_score = 1.0
                    else:
                        # Use preloaded embeddings first (avoids repeated DB queries)
                        rep_emb = preloaded_embeddings.get(rep_photo_id)
                        photo_emb = preloaded_embeddings.get(photo_id)

                        # Fallback to individual fetch if not in cache
                        if rep_emb is None or photo_emb is None:
                            if hasattr(self.similarity_service, 'get_embedding'):
                                rep_emb = rep_emb or self.similarity_service.get_embedding(rep_photo_id)
                                photo_emb = photo_emb or self.similarity_service.get_embedding(photo_id)
                            elif hasattr(self.similarity_service, 'embedder'):
                                rep_emb = rep_emb or self.similarity_service.embedder.get_embedding(rep_photo_id)
                                photo_emb = photo_emb or self.similarity_service.embedder.get_embedding(photo_id)

                        if rep_emb is not None and photo_emb is not None:
                            # Normalize
                            rep_emb = rep_emb / np.linalg.norm(rep_emb)
                            photo_emb = photo_emb / np.linalg.norm(photo_emb)
                            similarity_score = float(np.dot(rep_emb, photo_emb))
                        else:
                            similarity_score = params.similarity_threshold  # Default

                    self.stack_repo.add_stack_member(
                        project_id=project_id,
                        stack_id=stack_id,
                        photo_id=photo_id,
                        similarity_score=similarity_score
                    )
                    memberships_created += 1

                self.logger.debug(
                    f"Created stack {stack_id} with {len(cluster)} members "
                    f"(representative: {rep_photo_id})"
                )

            except Exception as e:
                self.logger.error(f"Failed to create stack for cluster {cluster}: {e}")
                errors += 1

        self.logger.info(
            f"Similar shot stack generation complete: "
            f"{stacks_created} stacks, {memberships_created} memberships, {errors} errors"
        )

        return StackGenStats(
            photos_considered=photos_processed,
            stacks_created=stacks_created,
            memberships_created=memberships_created,
            errors=errors
        )

    def generate_stacks(
        self,
        project_id: int,
        similarity_threshold: float = 0.85,
        time_window_seconds: int = 300,
        cross_date_similarity: bool = True
    ) -> int:
        """
        Convenience method to generate similar shot stacks for all photos.

        This is a simplified interface for the duplicate detection dialog.

        Args:
            project_id: Project ID
            similarity_threshold: Minimum similarity (0.0-1.0)
            time_window_seconds: Time window for candidates
            cross_date_similarity: Enable global similarity across all dates

        Returns:
            Number of stacks created
        """
        params = StackGenParams(
            similarity_threshold=similarity_threshold,
            time_window_seconds=time_window_seconds,
            cross_date_similarity=cross_date_similarity,
        )
        stats = self.regenerate_similar_shot_stacks(project_id, params)
        return stats.stacks_created

    def generate_stacks_for_photos(
        self,
        project_id: int,
        photo_ids: List[int],
        similarity_threshold: float = 0.85,
        time_window_seconds: int = 300,
        cross_date_similarity: bool = True,
        cross_date_threshold: float = 0.85,
    ) -> int:
        """
        Generate similar shot stacks for a specific subset of photos.

        This method filters the processing to only consider the given photo IDs.

        Args:
            project_id: Project ID
            photo_ids: List of photo IDs to consider for stacking
            similarity_threshold: Minimum similarity (0.0-1.0)
            time_window_seconds: Time window for candidates
            cross_date_similarity: Enable global similarity across all dates
            cross_date_threshold: Threshold for cross-date similarity pass

        Returns:
            Number of stacks created
        """
        if not photo_ids:
            self.logger.warning("generate_stacks_for_photos called with empty photo_ids")
            return 0

        self.logger.info(
            f"Starting similar shot stack generation for {len(photo_ids)} selected photos"
        )

        if not self.similarity_service:
            self.logger.error("Cannot generate stacks: similarity_service not provided")
            return 0

        params = StackGenParams(
            similarity_threshold=similarity_threshold,
            time_window_seconds=time_window_seconds,
            cross_date_similarity=cross_date_similarity,
            cross_date_threshold=cross_date_threshold,
        )

        # Clear existing stacks for this rule version
        cleared = self.stack_repo.clear_stacks_by_type(
            project_id=project_id,
            stack_type="similar",
            rule_version=params.rule_version
        )
        if cleared > 0:
            self.logger.info(f"Cleared {cleared} existing similar shot stacks")

        # Get ALL photos from the selected set (including those without timestamps)
        # The time-window pass will skip dateless photos, but the global
        # cross-date pass needs them for visual similarity matching.
        photo_id_set = set(photo_ids)
        all_photos = self.photo_repo.find_all(
            where_clause="project_id = ?",
            params=(project_id,),
            order_by="COALESCE(created_ts, 9999999999) ASC"
        )

        # Filter to only selected photos
        selected_photos = [p for p in all_photos if p.get("id") in photo_id_set]

        if not selected_photos:
            self.logger.info("No selected photos found")
            return 0

        timestamped = sum(1 for p in selected_photos if p.get("created_ts") is not None)
        self.logger.info(f"Processing {len(selected_photos)} photos ({timestamped} with timestamps)")

        # Batch load embeddings for efficiency
        preloaded_embeddings: Dict[int, Any] = {}
        if hasattr(self.similarity_service, 'get_all_embeddings_for_project'):
            preloaded_embeddings = self.similarity_service.get_all_embeddings_for_project(project_id)
        elif hasattr(self.similarity_service, 'get_embeddings_batch'):
            preloaded_embeddings = self.similarity_service.get_embeddings_batch(photo_ids)

        self.logger.info(f"Loaded {len(preloaded_embeddings)} embeddings for comparison")

        # Find clusters using time window + similarity
        photo_to_cluster: Dict[int, int] = {}
        all_clusters: List[List[int]] = []

        for photo in selected_photos:
            photo_id = photo["id"]

            if photo_id in photo_to_cluster:
                continue

            embedding = preloaded_embeddings.get(photo_id)
            if embedding is None:
                if hasattr(self.similarity_service, 'get_embedding'):
                    embedding = self.similarity_service.get_embedding(photo_id)

            if embedding is None:
                continue

            created_ts = photo.get("created_ts")
            if not created_ts:
                continue

            # Find candidates within time window (from selected photos only)
            candidates = [
                p for p in selected_photos
                if p["id"] != photo_id
                and p.get("created_ts")
                and abs(p.get("created_ts") - created_ts) <= time_window_seconds
            ]

            if not candidates:
                continue

            all_candidates = [photo] + candidates

            # Cluster by similarity
            clusters = self._cluster_by_similarity(
                photos=all_candidates,
                similarity_threshold=similarity_threshold,
                min_cluster_size=params.min_stack_size,
                preloaded_embeddings=preloaded_embeddings
            )

            for cluster in clusters:
                cluster_id = len(all_clusters)
                all_clusters.append(cluster)
                for pid in cluster:
                    photo_to_cluster[pid] = cluster_id

        self.logger.info(f"Found {len(all_clusters)} similar shot clusters (time-window pass)")

        # Global cross-date pass for selected photos
        # FIX (2026-02-08): Now considers ALL photos with embeddings to enable cross-date grouping
        if params.cross_date_similarity and preloaded_embeddings:
            global_clusters = self._cluster_globally_by_similarity(
                all_photos=selected_photos,
                preloaded_embeddings=preloaded_embeddings,
                already_clustered=photo_to_cluster,
                similarity_threshold=params.cross_date_threshold,
                min_cluster_size=params.min_stack_size,
            )

            # Track which time-window clusters are subsumed by global clusters
            subsumed_cluster_ids = set()

            for cluster in global_clusters:
                # Find which time-window clusters are fully contained in this global cluster
                cluster_set = set(cluster)
                for tw_cluster_id, tw_cluster in enumerate(all_clusters):
                    tw_cluster_set = set(tw_cluster)
                    if tw_cluster_set.issubset(cluster_set) and len(tw_cluster_set) < len(cluster_set):
                        # This time-window cluster is subsumed by the global cluster
                        subsumed_cluster_ids.add(tw_cluster_id)

            # Remove subsumed time-window clusters (they'll be replaced by larger global clusters)
            if subsumed_cluster_ids:
                self.logger.info(
                    f"Removing {len(subsumed_cluster_ids)} time-window clusters subsumed by global clusters"
                )
                all_clusters = [
                    c for i, c in enumerate(all_clusters)
                    if i not in subsumed_cluster_ids
                ]
                # Rebuild photo_to_cluster mapping
                photo_to_cluster.clear()
                for cluster_id, cluster in enumerate(all_clusters):
                    for pid in cluster:
                        photo_to_cluster[pid] = cluster_id

            # Add global clusters
            for cluster in global_clusters:
                cluster_id = len(all_clusters)
                all_clusters.append(cluster)
                for pid in cluster:
                    photo_to_cluster[pid] = cluster_id

            self.logger.info(
                f"Global cross-date pass found {len(global_clusters)} additional clusters"
            )

        self.logger.info(f"Total similar shot clusters: {len(all_clusters)}")

        # Create stacks in database
        stacks_created = 0
        import numpy as np

        for cluster in all_clusters:
            try:
                rep_photo_id = self._choose_stack_representative(project_id, cluster)
                if not rep_photo_id:
                    continue

                stack_id = self.stack_repo.create_stack(
                    project_id=project_id,
                    stack_type="similar",
                    representative_photo_id=rep_photo_id,
                    rule_version=params.rule_version,
                    created_by="system"
                )
                stacks_created += 1

                for photo_id in cluster:
                    if photo_id == rep_photo_id:
                        similarity_score = 1.0
                    else:
                        rep_emb = preloaded_embeddings.get(rep_photo_id)
                        photo_emb = preloaded_embeddings.get(photo_id)

                        if rep_emb is not None and photo_emb is not None:
                            rep_emb = rep_emb / np.linalg.norm(rep_emb)
                            photo_emb = photo_emb / np.linalg.norm(photo_emb)
                            similarity_score = float(np.dot(rep_emb, photo_emb))
                        else:
                            similarity_score = similarity_threshold

                    self.stack_repo.add_stack_member(
                        project_id=project_id,
                        stack_id=stack_id,
                        photo_id=photo_id,
                        similarity_score=similarity_score
                    )

            except Exception as e:
                self.logger.error(f"Failed to create stack for cluster {cluster}: {e}")

        self.logger.info(f"Created {stacks_created} similar shot stacks")
        return stacks_created

    # =========================================================================
    # NEAR-DUPLICATE STACKS
    # =========================================================================

    def regenerate_near_duplicate_stacks(
        self,
        project_id: int,
        params: StackGenParams
    ) -> StackGenStats:
        """
        Generate stacks for near-duplicates (visual similarity despite encoding/resize).

        Recommended approach:
        - Add perceptual hashing (pHash, dHash) as pre-filter
        - Use Hamming distance < threshold for candidates
        - Confirm with embedding cosine similarity

        Reference for pHash:
        http://www.hackerfactor.com/blog/index.php?/archives/432-Looks-Like-It.html

        Args:
            project_id: Project ID
            params: Stack generation parameters

        Returns:
            StackGenStats with operation results

        Note: This is a stub implementation. Full implementation requires:
        - Perceptual hash computation (imagehash library)
        - Hamming distance comparison
        - Embedding confirmation for ambiguous cases
        """
        self.logger.info(f"Starting near-duplicate stack generation for project {project_id}")

        # Step 1: Clear existing stacks
        cleared = self.stack_repo.clear_stacks_by_type(
            project_id=project_id,
            stack_type="near_duplicate",
            rule_version=params.rule_version
        )
        self.logger.info(f"Cleared {cleared} existing near-duplicate stacks")

        # Step 2: STUB - Full implementation needed
        # TODO: Implement perceptual hash-based near-duplicate detection
        #
        # Algorithm outline:
        # 1. Ensure all photos have perceptual_hash (backfill if needed)
        # 2. Group photos by perceptual hash buckets (BK-tree or LSH)
        # 3. Within each bucket, compute Hamming distance
        # 4. If distance < threshold (e.g., 5-10 bits), consider near-duplicate
        # 5. Optionally confirm with embedding similarity
        # 6. Create stacks and add members

        self.logger.warning(
            "Near-duplicate stack generation not fully implemented. "
            "Requires perceptual hashing infrastructure (imagehash library)."
        )

        return StackGenStats(
            photos_considered=0,
            stacks_created=0,
            memberships_created=0,
            errors=0
        )

    # =========================================================================
    # HELPER METHODS (for future implementation)
    # =========================================================================

    def _find_time_candidates(
        self,
        project_id: int,
        reference_timestamp: int,
        time_window_seconds: int,
        reference_photo_id: Optional[int] = None,
        folder_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Find photos within time window of reference timestamp.

        Args:
            project_id: Project ID
            reference_timestamp: Reference Unix timestamp
            time_window_seconds: Time window in seconds (+/- around reference)
            reference_photo_id: Optional photo ID to exclude from results
            folder_id: Optional folder filter (same folder only)

        Returns:
            List of photo dictionaries within time window
        """
        exclude_ids = [reference_photo_id] if reference_photo_id else None

        return self.photo_repo.get_photos_in_time_window(
            project_id=project_id,
            reference_timestamp=reference_timestamp,
            time_window_seconds=time_window_seconds,
            folder_id=folder_id,
            exclude_photo_ids=exclude_ids
        )

    def _cluster_by_similarity(
        self,
        photos: List[Dict[str, Any]],
        similarity_threshold: float,
        min_cluster_size: int,
        preloaded_embeddings: Optional[Dict[int, Any]] = None
    ) -> List[List[int]]:
        """
        Cluster photos by visual similarity using strict complete-linkage clustering.

        Algorithm (FIXED - prevents transitive grouping):
        1. Load embeddings for all photos (or use preloaded cache)
        2. For each photo, find candidates that meet threshold with THIS photo
        3. STRICT CHECK: Only add to cluster if similar to ALL existing cluster members
        4. This prevents transitive grouping where A similar to B, B to C, but A not to C
        5. Return clusters meeting minimum size requirement

        Args:
            photos: List of photo dictionaries
            similarity_threshold: Minimum cosine similarity for same cluster (0.0-1.0)
            min_cluster_size: Minimum photos per cluster
            preloaded_embeddings: Optional pre-loaded embeddings dict (photo_id -> embedding)
                                  If provided, skips individual database lookups

        Returns:
            List of clusters (each cluster is a list of photo_ids)

        Note:
            Requires photos to have semantic embeddings. Photos without
            embeddings are skipped.

        Fix: Previous greedy algorithm allowed transitive clustering. Now requires
        each photo to be similar to ALL cluster members, not just one.
        """
        if not self.similarity_service:
            self.logger.warning("Cannot cluster: similarity_service not provided")
            return []

        import numpy as np

        # Load embeddings (use preloaded cache if available for performance)
        photo_embeddings: Dict[int, np.ndarray] = {}
        for photo in photos:
            photo_id = photo["id"]

            # Try preloaded cache first (avoids N database queries)
            embedding = preloaded_embeddings.get(photo_id) if preloaded_embeddings else None

            # Fallback to individual fetch only if not in cache
            if embedding is None:
                if hasattr(self.similarity_service, 'get_embedding'):
                    embedding = self.similarity_service.get_embedding(photo_id)
                elif hasattr(self.similarity_service, 'embedder'):
                    embedding = self.similarity_service.embedder.get_embedding(photo_id)

            if embedding is not None:
                # Normalize for cosine similarity
                norm = np.linalg.norm(embedding)
                if norm > 0:
                    photo_embeddings[photo_id] = embedding / norm

        if len(photo_embeddings) < min_cluster_size:
            self.logger.debug(f"Not enough photos with embeddings: {len(photo_embeddings)}")
            return []

        # FIXED: Complete-linkage clustering (strict similarity requirement)
        photo_ids = list(photo_embeddings.keys())
        assigned = set()
        clusters = []

        for i, photo_id in enumerate(photo_ids):
            if photo_id in assigned:
                continue

            # Start new cluster with this photo
            cluster = [photo_id]
            assigned.add(photo_id)

            # Find similar photos not yet assigned
            embedding = photo_embeddings[photo_id]

            for other_id in photo_ids[i+1:]:
                if other_id in assigned:
                    continue

                other_embedding = photo_embeddings[other_id]

                # CRITICAL FIX: Check similarity with ALL cluster members, not just seed
                is_similar_to_all = True
                for cluster_member_id in cluster:
                    cluster_member_embedding = photo_embeddings[cluster_member_id]
                    similarity = float(np.dot(other_embedding, cluster_member_embedding))

                    if similarity < similarity_threshold:
                        is_similar_to_all = False
                        break

                # Only add if similar to ALL cluster members
                if is_similar_to_all:
                    cluster.append(other_id)
                    assigned.add(other_id)

            # Keep cluster if meets minimum size
            if len(cluster) >= min_cluster_size:
                clusters.append(cluster)
                self.logger.debug(
                    f"Created cluster of {len(cluster)} photos "
                    f"(all-pairs similarity >= {similarity_threshold:.2f})"
                )

        return clusters

    def _cluster_globally_by_similarity(
        self,
        all_photos: List[Dict[str, Any]],
        preloaded_embeddings: Dict[int, Any],
        already_clustered: Dict[int, int],
        similarity_threshold: float,
        min_cluster_size: int,
    ) -> List[List[int]]:
        """
        Global similarity pass: find visually similar photos across ALL dates.

        Uses vectorized numpy matrix multiplication for efficient pairwise
        cosine similarity computation. Considers ALL photos with embeddings
        to enable cross-date grouping, even for photos already in time-window
        clusters.

        IMPORTANT FIX (2026-02-08): Previously skipped photos in already_clustered,
        which prevented cross-date grouping. Now considers all photos with embeddings
        to find visually similar photos across different dates/time-window clusters.

        Args:
            all_photos: All photos in the project
            preloaded_embeddings: Pre-loaded embeddings dict (photo_id -> embedding)
            already_clustered: Dict of photo_id -> cluster_id from time-window pass
                              (used for tracking, not filtering)
            similarity_threshold: Minimum cosine similarity for grouping
            min_cluster_size: Minimum photos per cluster

        Returns:
            List of new clusters (each cluster is a list of photo_ids)
        """
        import numpy as np

        # Collect ALL photos with embeddings (including already-clustered ones)
        # This enables cross-date grouping: photos from different time-window
        # clusters can still be grouped if visually similar
        candidate_ids = []
        candidate_embeddings = []
        for photo in all_photos:
            pid = photo["id"]
            # FIX: Don't skip already-clustered photos - we need to find
            # cross-date similarity even for photos in time-window clusters
            emb = preloaded_embeddings.get(pid)
            if emb is not None:
                norm = np.linalg.norm(emb)
                if norm > 0:
                    candidate_ids.append(pid)
                    candidate_embeddings.append(emb / norm)

        if len(candidate_ids) < min_cluster_size:
            return []

        unique_clustered = len(set(already_clustered.keys()))
        self.logger.info(
            f"Global similarity pass: {len(candidate_ids)} photos with embeddings "
            f"({unique_clustered} unique photos in {len(set(already_clustered.values()))} time-window clusters)"
        )

        # Build embedding matrix and compute pairwise cosine similarity
        emb_matrix = np.stack(candidate_embeddings)  # shape: (N, D)
        # Cosine similarity matrix via matrix multiplication (embeddings already normalized)
        sim_matrix = emb_matrix @ emb_matrix.T  # shape: (N, N)

        # Complete-linkage clustering on the similarity matrix
        n = len(candidate_ids)
        assigned = set()
        raw_clusters = []

        for i in range(n):
            if i in assigned:
                continue

            cluster_indices = [i]
            assigned.add(i)

            for j in range(i + 1, n):
                if j in assigned:
                    continue

                # Check similarity with ALL current cluster members
                is_similar_to_all = True
                for ci in cluster_indices:
                    if sim_matrix[j, ci] < similarity_threshold:
                        is_similar_to_all = False
                        break

                if is_similar_to_all:
                    cluster_indices.append(j)
                    assigned.add(j)

            if len(cluster_indices) >= min_cluster_size:
                cluster_photo_ids = [candidate_ids[idx] for idx in cluster_indices]
                raw_clusters.append(cluster_photo_ids)

        # Deduplicate: only keep clusters that span multiple time-window clusters
        # or contain photos not in any time-window cluster (true cross-date groups)
        clusters = []
        for cluster in raw_clusters:
            # Check how many different time-window clusters this global cluster spans
            time_window_cluster_ids = set()
            unclustered_count = 0
            for pid in cluster:
                if pid in already_clustered:
                    time_window_cluster_ids.add(already_clustered[pid])
                else:
                    unclustered_count += 1

            # Keep this cluster if:
            # 1. It spans multiple time-window clusters (true cross-date match), OR
            # 2. It contains unclustered photos mixed with time-window photos, OR
            # 3. It's entirely unclustered photos (would have been found before, but safety check)
            is_cross_date = len(time_window_cluster_ids) > 1
            is_mixed = len(time_window_cluster_ids) > 0 and unclustered_count > 0
            is_new_unclustered = unclustered_count >= min_cluster_size and len(time_window_cluster_ids) == 0

            if is_cross_date or is_mixed or is_new_unclustered:
                clusters.append(cluster)
                self.logger.debug(
                    f"Global cluster: {len(cluster)} photos "
                    f"(spans {len(time_window_cluster_ids)} time-window clusters, "
                    f"{unclustered_count} unclustered, similarity >= {similarity_threshold:.2f})"
                )

        return clusters

    def _choose_stack_representative(
        self,
        project_id: int,
        photo_ids: List[int]
    ) -> Optional[int]:
        """
        Choose representative photo for stack (deterministic).

        Uses same logic as AssetService.choose_representative_photo:
        1. Higher resolution
        2. Larger file size
        3. Earlier capture date
        4. Non-screenshot
        5. Earlier import

        Args:
            project_id: Project ID
            photo_ids: List of photo IDs in stack

        Returns:
            photo_id of representative, or None
        """
        if not photo_ids:
            return None

        # Fetch photo metadata for all candidates
        photos = []
        for photo_id in photo_ids:
            photo = self.photo_repo.get_by_id(photo_id)
            if photo:
                photos.append(photo)

        if not photos:
            return None

        # Selection key function (same as AssetService)
        def selection_key(photo: Dict[str, Any]) -> Tuple[float, float, float, int, int]:
            """
            Return tuple for sorting (lower = better, so negate values we want maximized).

            Priority order:
            1. Higher resolution (more pixels)
            2. Larger file size (less compression)
            3. Earlier capture date
            4. Non-screenshot paths
            5. Earlier import (lower photo ID)
            """
            # Resolution (prefer higher)
            width = photo.get("width") or 0
            height = photo.get("height") or 0
            resolution = width * height

            # File size (prefer larger)
            file_size = photo.get("size_kb") or 0.0

            # Capture timestamp (prefer earlier)
            if photo.get("created_ts"):
                timestamp = photo.get("created_ts") or float('inf')
            else:
                timestamp = float('inf')

            # Avoid screenshots
            path = photo.get("path") or ""
            is_screenshot = 1 if "screenshot" in path.lower() else 0

            # Earlier import (lower ID = earlier)
            photo_id = photo.get("id") or float('inf')

            return (
                -resolution,      # Higher resolution first (negated)
                -file_size,       # Larger file first (negated)
                timestamp,        # Earlier date first
                is_screenshot,    # Non-screenshots first
                photo_id          # Earlier import first
            )

        # Sort and select best
        sorted_photos = sorted(photos, key=selection_key)
        representative = sorted_photos[0]
        representative_id = representative["id"]

        self.logger.debug(
            f"Chose photo {representative_id} as stack representative "
            f"(resolution: {representative.get('width')}x{representative.get('height')}, "
            f"size: {representative.get('size_kb')} KB)"
        )

        return representative_id

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_stack_summary(self, project_id: int, stack_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get summary statistics for stacks.

        Args:
            project_id: Project ID
            stack_type: Optional filter by stack type

        Returns:
            Dictionary with stack statistics
        """
        total_stacks = self.stack_repo.count_stacks(project_id, stack_type)

        # Get member counts
        stacks = self.stack_repo.list_stacks(project_id, stack_type, limit=1000)
        member_counts = []
        for stack in stacks:
            count = self.stack_repo.count_stack_members(project_id, stack["stack_id"])
            member_counts.append(count)

        avg_members = sum(member_counts) / len(member_counts) if member_counts else 0

        return {
            "project_id": project_id,
            "stack_type": stack_type or "all",
            "total_stacks": total_stacks,
            "average_members_per_stack": round(avg_members, 2),
            "total_memberships": sum(member_counts)
        }
