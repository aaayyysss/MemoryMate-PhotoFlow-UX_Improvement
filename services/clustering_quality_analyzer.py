# services/clustering_quality_analyzer.py
# Clustering Quality Analysis for DBSCAN face clustering
# Phase 2A: Advanced Analytics & Quality Improvements

"""
Clustering Quality Analyzer

Provides comprehensive quality metrics for face clustering results to enable:
- Assessment of clustering quality
- Parameter tuning guidance
- Outlier detection
- Representative face selection optimization

Metrics:
1. Silhouette Score (cluster cohesion and separation)
2. Davies-Bouldin Index (cluster similarity)
3. Cluster Compactness (within-cluster variance)
4. Cluster Separation (between-cluster distances)
5. Noise Ratio (percentage of unassigned faces)
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from sklearn.metrics import silhouette_score, davies_bouldin_score
from sklearn.metrics import pairwise_distances
import logging

logger = logging.getLogger(__name__)


@dataclass
class ClusterQualityMetrics:
    """
    Comprehensive quality metrics for clustering results.

    Attributes:
        silhouette_score: Overall clustering quality (-1 to 1, higher is better)
        davies_bouldin_index: Cluster separation quality (0+, lower is better)
        avg_cluster_compactness: Average within-cluster variance (0+, lower is better)
        avg_cluster_separation: Average between-cluster distance (0+, higher is better)
        noise_ratio: Percentage of faces marked as noise (0-1)
        cluster_count: Number of clusters found
        face_count: Total number of faces
        noise_count: Number of faces marked as noise (-1 label)
        cluster_sizes: List of cluster sizes
        cluster_silhouettes: Silhouette score per cluster
        overall_quality: Combined quality score (0-100)
        quality_label: Human-readable quality assessment
    """
    silhouette_score: float
    davies_bouldin_index: float
    avg_cluster_compactness: float
    avg_cluster_separation: float
    noise_ratio: float
    cluster_count: int
    face_count: int
    noise_count: int
    cluster_sizes: List[int]
    cluster_silhouettes: List[float]
    overall_quality: float
    quality_label: str

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/logging."""
        return {
            'silhouette_score': self.silhouette_score,
            'davies_bouldin_index': self.davies_bouldin_index,
            'avg_cluster_compactness': self.avg_cluster_compactness,
            'avg_cluster_separation': self.avg_cluster_separation,
            'noise_ratio': self.noise_ratio,
            'cluster_count': self.cluster_count,
            'face_count': self.face_count,
            'noise_count': self.noise_count,
            'cluster_sizes': self.cluster_sizes,
            'cluster_silhouettes': self.cluster_silhouettes,
            'overall_quality': self.overall_quality,
            'quality_label': self.quality_label
        }


class ClusteringQualityAnalyzer:
    """
    Analyzer for assessing face clustering quality.

    Uses multiple metrics to provide comprehensive quality assessment:
    - Silhouette score (cluster cohesion and separation)
    - Davies-Bouldin index (cluster similarity)
    - Cluster compactness (within-cluster variance)
    - Cluster separation (between-cluster distances)
    - Noise ratio (outlier detection quality)

    Helps with:
    - Evaluating clustering parameter choices (eps, min_samples)
    - Detecting poor quality clusters that need re-clustering
    - Identifying outliers and noise
    - Optimizing representative face selection
    """

    # Quality thresholds for overall assessment
    QUALITY_THRESHOLDS = {
        'silhouette_excellent': 0.7,    # > 0.7: Excellent separation
        'silhouette_good': 0.5,         # 0.5-0.7: Good separation
        'silhouette_fair': 0.25,        # 0.25-0.5: Fair separation
        # < 0.25: Poor separation

        'db_excellent': 0.5,            # < 0.5: Excellent (low is better)
        'db_good': 1.0,                 # 0.5-1.0: Good
        'db_fair': 1.5,                 # 1.0-1.5: Fair
        # > 1.5: Poor

        'noise_acceptable': 0.15,       # < 15% noise is acceptable
        'noise_concerning': 0.30        # > 30% noise is concerning
    }

    # Weights for overall quality calculation
    QUALITY_WEIGHTS = {
        'silhouette': 0.40,      # 40% - most important metric
        'davies_bouldin': 0.30,  # 30% - cluster separation
        'noise_ratio': 0.20,     # 20% - outlier handling
        'compactness': 0.10      # 10% - cluster tightness
    }

    def __init__(self):
        """Initialize clustering quality analyzer."""
        pass

    def analyze_clustering(self,
                          embeddings: np.ndarray,
                          labels: np.ndarray,
                          metric: str = 'euclidean') -> ClusterQualityMetrics:
        """
        Analyze quality of clustering results.

        Args:
            embeddings: Face embeddings (N, D) array
            labels: Cluster labels (N,) array (noise = -1)
            metric: Distance metric ('euclidean', 'cosine', etc.)

        Returns:
            ClusterQualityMetrics with all quality scores
        """
        try:
            # Validate inputs
            if embeddings.shape[0] != labels.shape[0]:
                logger.error(f"Embeddings and labels shape mismatch: {embeddings.shape[0]} vs {labels.shape[0]}")
                return self._default_metrics(len(labels))

            if embeddings.shape[0] < 2:
                logger.warning("Not enough faces for clustering analysis (need at least 2)")
                return self._default_metrics(len(labels))

            # Extract clustering statistics
            face_count = len(labels)
            noise_mask = labels == -1
            noise_count = np.sum(noise_mask)
            noise_ratio = noise_count / face_count if face_count > 0 else 0.0

            unique_labels = np.unique(labels[~noise_mask])
            cluster_count = len(unique_labels)

            # Calculate cluster sizes
            cluster_sizes = [np.sum(labels == label) for label in unique_labels]

            # Need at least 2 clusters for silhouette/DB index
            if cluster_count < 2:
                logger.warning(f"Not enough clusters for quality analysis (found {cluster_count}, need at least 2)")
                return self._minimal_metrics(
                    face_count, noise_count, noise_ratio, cluster_count, cluster_sizes
                )

            # Calculate metrics (only on non-noise faces)
            non_noise_mask = ~noise_mask
            non_noise_embeddings = embeddings[non_noise_mask]
            non_noise_labels = labels[non_noise_mask]

            # Silhouette score (-1 to 1, higher is better)
            silhouette = self._calculate_silhouette_score(
                non_noise_embeddings, non_noise_labels, metric
            )

            # Davies-Bouldin index (0+, lower is better)
            db_index = self._calculate_davies_bouldin_index(
                non_noise_embeddings, non_noise_labels
            )

            # Per-cluster silhouette scores
            cluster_silhouettes = self._calculate_cluster_silhouettes(
                non_noise_embeddings, non_noise_labels, unique_labels, metric
            )

            # Cluster compactness (within-cluster variance)
            avg_compactness = self._calculate_avg_compactness(
                non_noise_embeddings, non_noise_labels, unique_labels, metric
            )

            # Cluster separation (between-cluster distances)
            avg_separation = self._calculate_avg_separation(
                non_noise_embeddings, non_noise_labels, unique_labels, metric
            )

            # Calculate overall quality score (0-100)
            overall_quality = self._calculate_overall_quality(
                silhouette, db_index, noise_ratio, avg_compactness
            )

            # Get quality label
            quality_label = self._get_quality_label(overall_quality)

            return ClusterQualityMetrics(
                silhouette_score=silhouette,
                davies_bouldin_index=db_index,
                avg_cluster_compactness=avg_compactness,
                avg_cluster_separation=avg_separation,
                noise_ratio=noise_ratio,
                cluster_count=cluster_count,
                face_count=face_count,
                noise_count=noise_count,
                cluster_sizes=cluster_sizes,
                cluster_silhouettes=cluster_silhouettes,
                overall_quality=overall_quality,
                quality_label=quality_label
            )

        except Exception as e:
            logger.error(f"Error analyzing clustering quality: {e}", exc_info=True)
            return self._default_metrics(len(labels) if labels is not None else 0)

    def _calculate_silhouette_score(self,
                                   embeddings: np.ndarray,
                                   labels: np.ndarray,
                                   metric: str) -> float:
        """
        Calculate silhouette score for clustering.

        Silhouette score measures how similar an object is to its own cluster
        compared to other clusters. Range: -1 to 1.
        - 1: Perfect clustering (far from other clusters)
        - 0: Overlapping clusters
        - -1: Wrong clustering (closer to other clusters)

        Args:
            embeddings: Face embeddings (non-noise only)
            labels: Cluster labels (non-noise only)
            metric: Distance metric

        Returns:
            Silhouette score (-1 to 1)
        """
        try:
            if len(embeddings) < 2 or len(np.unique(labels)) < 2:
                return 0.0

            score = silhouette_score(embeddings, labels, metric=metric)
            return float(score)

        except Exception as e:
            logger.debug(f"Error calculating silhouette score: {e}")
            return 0.0

    def _calculate_davies_bouldin_index(self,
                                       embeddings: np.ndarray,
                                       labels: np.ndarray) -> float:
        """
        Calculate Davies-Bouldin index for clustering.

        DB index measures the average similarity ratio of each cluster with
        its most similar cluster. Range: 0 to infinity.
        - 0: Perfect clustering (well-separated clusters)
        - Higher values: Worse clustering (overlapping clusters)

        Typical values:
        - < 0.5: Excellent
        - 0.5-1.0: Good
        - 1.0-1.5: Fair
        - > 1.5: Poor

        Args:
            embeddings: Face embeddings (non-noise only)
            labels: Cluster labels (non-noise only)

        Returns:
            Davies-Bouldin index (0+, lower is better)
        """
        try:
            if len(embeddings) < 2 or len(np.unique(labels)) < 2:
                return 999.0  # Very high value for poor clustering

            index = davies_bouldin_score(embeddings, labels)
            return float(index)

        except Exception as e:
            logger.debug(f"Error calculating Davies-Bouldin index: {e}")
            return 999.0

    def _calculate_cluster_silhouettes(self,
                                      embeddings: np.ndarray,
                                      labels: np.ndarray,
                                      unique_labels: np.ndarray,
                                      metric: str) -> List[float]:
        """
        Calculate silhouette score for each cluster.

        Args:
            embeddings: Face embeddings (non-noise only)
            labels: Cluster labels (non-noise only)
            unique_labels: Unique cluster labels
            metric: Distance metric

        Returns:
            List of silhouette scores per cluster
        """
        try:
            from sklearn.metrics import silhouette_samples

            if len(embeddings) < 2 or len(unique_labels) < 2:
                return [0.0] * len(unique_labels)

            # Calculate silhouette for each sample
            sample_silhouettes = silhouette_samples(embeddings, labels, metric=metric)

            # Average silhouette per cluster
            cluster_silhouettes = []
            for label in unique_labels:
                mask = labels == label
                avg_silhouette = np.mean(sample_silhouettes[mask])
                cluster_silhouettes.append(float(avg_silhouette))

            return cluster_silhouettes

        except Exception as e:
            logger.debug(f"Error calculating cluster silhouettes: {e}")
            return [0.0] * len(unique_labels)

    def _calculate_avg_compactness(self,
                                  embeddings: np.ndarray,
                                  labels: np.ndarray,
                                  unique_labels: np.ndarray,
                                  metric: str) -> float:
        """
        Calculate average cluster compactness (within-cluster variance).

        Compactness measures how tight each cluster is. Lower is better.

        Args:
            embeddings: Face embeddings (non-noise only)
            labels: Cluster labels (non-noise only)
            unique_labels: Unique cluster labels
            metric: Distance metric

        Returns:
            Average compactness (0+, lower is better)
        """
        try:
            if len(unique_labels) == 0:
                return 0.0

            compactness_values = []

            for label in unique_labels:
                cluster_mask = labels == label
                cluster_embeddings = embeddings[cluster_mask]

                if len(cluster_embeddings) < 2:
                    continue

                # Calculate centroid
                centroid = np.mean(cluster_embeddings, axis=0)

                # Calculate average distance to centroid
                if metric == 'cosine':
                    # Cosine distance
                    from sklearn.metrics.pairwise import cosine_distances
                    distances = cosine_distances(cluster_embeddings, centroid.reshape(1, -1))
                else:
                    # Euclidean distance
                    distances = np.linalg.norm(cluster_embeddings - centroid, axis=1)

                avg_distance = np.mean(distances)
                compactness_values.append(avg_distance)

            if not compactness_values:
                return 0.0

            return float(np.mean(compactness_values))

        except Exception as e:
            logger.debug(f"Error calculating compactness: {e}")
            return 0.0

    def _calculate_avg_separation(self,
                                 embeddings: np.ndarray,
                                 labels: np.ndarray,
                                 unique_labels: np.ndarray,
                                 metric: str) -> float:
        """
        Calculate average cluster separation (between-cluster distances).

        Separation measures how far apart clusters are. Higher is better.

        Args:
            embeddings: Face embeddings (non-noise only)
            labels: Cluster labels (non-noise only)
            unique_labels: Unique cluster labels
            metric: Distance metric

        Returns:
            Average separation (0+, higher is better)
        """
        try:
            if len(unique_labels) < 2:
                return 0.0

            # Calculate cluster centroids
            centroids = []
            for label in unique_labels:
                cluster_mask = labels == label
                cluster_embeddings = embeddings[cluster_mask]
                centroid = np.mean(cluster_embeddings, axis=0)
                centroids.append(centroid)

            centroids = np.array(centroids)

            # Calculate pairwise distances between centroids
            if metric == 'cosine':
                from sklearn.metrics.pairwise import cosine_distances
                distances = cosine_distances(centroids)
            else:
                distances = pairwise_distances(centroids, metric='euclidean')

            # Average of upper triangle (exclude diagonal)
            n = len(centroids)
            if n < 2:
                return 0.0

            upper_triangle = distances[np.triu_indices(n, k=1)]
            avg_separation = np.mean(upper_triangle)

            return float(avg_separation)

        except Exception as e:
            logger.debug(f"Error calculating separation: {e}")
            return 0.0

    def _calculate_overall_quality(self,
                                  silhouette: float,
                                  db_index: float,
                                  noise_ratio: float,
                                  compactness: float) -> float:
        """
        Calculate overall quality score (0-100) as weighted combination.

        Args:
            silhouette: Silhouette score (-1 to 1)
            db_index: Davies-Bouldin index (0+, lower is better)
            noise_ratio: Noise ratio (0-1)
            compactness: Average compactness (0+, lower is better)

        Returns:
            Overall quality score 0-100
        """
        # Normalize silhouette to 0-100 (from -1 to 1 range)
        silhouette_normalized = ((silhouette + 1) / 2) * 100

        # Normalize DB index to 0-100 (lower is better, cap at 3.0 for terrible)
        # 0 -> 100, 1.5 -> 50, 3.0 -> 0
        db_normalized = max(0, 100 - (db_index / 3.0) * 100)

        # Normalize noise ratio to 0-100 (lower is better)
        # 0% noise -> 100, 30% noise -> 0
        noise_normalized = max(0, 100 - (noise_ratio / 0.30) * 100)

        # Normalize compactness to 0-100 (lower is better, cap at 2.0)
        # For cosine distance, typical compactness is 0-1
        # For euclidean, typical compactness is 0-2
        compactness_cap = 2.0 if compactness > 1.0 else 1.0
        compactness_normalized = max(0, 100 - (compactness / compactness_cap) * 100)

        # Weighted combination
        overall = (
            silhouette_normalized * self.QUALITY_WEIGHTS['silhouette'] +
            db_normalized * self.QUALITY_WEIGHTS['davies_bouldin'] +
            noise_normalized * self.QUALITY_WEIGHTS['noise_ratio'] +
            compactness_normalized * self.QUALITY_WEIGHTS['compactness']
        )

        return float(overall)

    def _get_quality_label(self, overall_quality: float) -> str:
        """
        Get human-readable quality label.

        Args:
            overall_quality: Overall quality score 0-100

        Returns:
            Quality label (Excellent, Good, Fair, Poor)
        """
        if overall_quality >= 80:
            return "Excellent"
        elif overall_quality >= 60:
            return "Good"
        elif overall_quality >= 40:
            return "Fair"
        else:
            return "Poor"

    def get_tuning_suggestions(self, metrics: ClusterQualityMetrics) -> List[str]:
        """
        Get parameter tuning suggestions based on quality metrics.

        Args:
            metrics: ClusterQualityMetrics from analyze_clustering()

        Returns:
            List of tuning suggestions
        """
        suggestions = []

        # Check silhouette score
        if metrics.silhouette_score < self.QUALITY_THRESHOLDS['silhouette_fair']:
            suggestions.append(
                f"Low silhouette score ({metrics.silhouette_score:.3f}): "
                "Consider increasing eps to merge similar clusters, or decreasing eps to split overlapping clusters."
            )

        # Check Davies-Bouldin index
        if metrics.davies_bouldin_index > self.QUALITY_THRESHOLDS['db_fair']:
            suggestions.append(
                f"High Davies-Bouldin index ({metrics.davies_bouldin_index:.3f}): "
                "Clusters are too similar. Try increasing eps to merge them, or adjusting min_samples."
            )

        # Check noise ratio
        if metrics.noise_ratio > self.QUALITY_THRESHOLDS['noise_concerning']:
            suggestions.append(
                f"High noise ratio ({metrics.noise_ratio:.1%}): "
                "Too many unassigned faces. Try decreasing eps or min_samples to include more faces in clusters."
            )
        elif metrics.noise_ratio < 0.05 and metrics.cluster_count > metrics.face_count * 0.3:
            suggestions.append(
                f"Very low noise ratio ({metrics.noise_ratio:.1%}) but many small clusters: "
                "Might be over-clustering. Try increasing eps to merge similar clusters."
            )

        # Check cluster sizes
        if metrics.cluster_sizes:
            max_size = max(metrics.cluster_sizes)
            avg_size = np.mean(metrics.cluster_sizes)

            if max_size > avg_size * 10:
                suggestions.append(
                    f"One very large cluster (size {max_size} vs avg {avg_size:.1f}): "
                    "May indicate under-clustering. Try decreasing eps to split large clusters."
                )

            singleton_count = sum(1 for size in metrics.cluster_sizes if size == 1)
            if singleton_count > metrics.cluster_count * 0.3:
                suggestions.append(
                    f"Many singleton clusters ({singleton_count}/{metrics.cluster_count}): "
                    "Try increasing min_samples to require larger clusters, or increasing eps."
                )

        # Check per-cluster quality
        if metrics.cluster_silhouettes:
            poor_clusters = [i for i, s in enumerate(metrics.cluster_silhouettes) if s < 0.25]
            if poor_clusters:
                suggestions.append(
                    f"{len(poor_clusters)} clusters have poor silhouette scores: "
                    "These clusters may need manual review or re-clustering."
                )

        if not suggestions:
            suggestions.append("Clustering quality looks good! No tuning needed.")

        return suggestions

    def _default_metrics(self, face_count: int) -> ClusterQualityMetrics:
        """
        Return default/fallback metrics when analysis fails.

        Args:
            face_count: Number of faces

        Returns:
            ClusterQualityMetrics with default values
        """
        return ClusterQualityMetrics(
            silhouette_score=0.0,
            davies_bouldin_index=999.0,
            avg_cluster_compactness=0.0,
            avg_cluster_separation=0.0,
            noise_ratio=1.0,
            cluster_count=0,
            face_count=face_count,
            noise_count=face_count,
            cluster_sizes=[],
            cluster_silhouettes=[],
            overall_quality=0.0,
            quality_label="Unknown"
        )

    def _minimal_metrics(self,
                        face_count: int,
                        noise_count: int,
                        noise_ratio: float,
                        cluster_count: int,
                        cluster_sizes: List[int]) -> ClusterQualityMetrics:
        """
        Return minimal metrics when advanced analysis cannot be performed.

        Used when there are < 2 clusters (can't calculate silhouette/DB index).

        Args:
            face_count: Number of faces
            noise_count: Number of noise faces
            noise_ratio: Noise ratio
            cluster_count: Number of clusters
            cluster_sizes: List of cluster sizes

        Returns:
            ClusterQualityMetrics with minimal analysis
        """
        # Can't calculate proper quality without at least 2 clusters
        # Base quality on noise ratio only
        noise_quality = max(0, 100 - (noise_ratio / 0.30) * 100)

        return ClusterQualityMetrics(
            silhouette_score=0.0,
            davies_bouldin_index=999.0,
            avg_cluster_compactness=0.0,
            avg_cluster_separation=0.0,
            noise_ratio=noise_ratio,
            cluster_count=cluster_count,
            face_count=face_count,
            noise_count=noise_count,
            cluster_sizes=cluster_sizes,
            cluster_silhouettes=[0.0] * cluster_count,
            overall_quality=noise_quality * 0.3,  # Reduced quality due to lack of clusters
            quality_label="Insufficient Clusters"
        )
