#!/usr/bin/env python3
"""
Test script for Phase 2A: Face Quality & Clustering Metrics

Tests:
1. FaceQualityAnalyzer - comprehensive quality scoring
2. ClusteringQualityAnalyzer - clustering quality metrics
3. Integration validation
"""

import sys
import os
import numpy as np
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.face_quality_analyzer import FaceQualityAnalyzer, FaceQualityMetrics
from services.clustering_quality_analyzer import ClusteringQualityAnalyzer, ClusterQualityMetrics


def test_face_quality_analyzer():
    """Test FaceQualityAnalyzer basic functionality."""
    print("=" * 80)
    print("TEST 1: FaceQualityAnalyzer")
    print("=" * 80)

    analyzer = FaceQualityAnalyzer()

    # Test 1: Default metrics (when analysis fails)
    print("\n1.1. Testing default metrics...")
    default_metrics = analyzer._default_metrics(confidence=0.8)
    assert isinstance(default_metrics, FaceQualityMetrics)
    assert default_metrics.confidence == 0.8
    assert default_metrics.overall_quality == 0.0
    assert default_metrics.is_good_quality == False
    print("✅ Default metrics work correctly")

    # Test 2: Quality thresholds
    print("\n1.2. Testing quality thresholds...")
    assert analyzer.DEFAULT_THRESHOLDS['blur_min'] == 100.0
    assert analyzer.DEFAULT_THRESHOLDS['overall_min'] == 60.0
    print("✅ Quality thresholds configured correctly")

    # Test 3: Quality weights
    print("\n1.3. Testing quality weights...")
    weights_sum = sum(analyzer.QUALITY_WEIGHTS.values())
    assert abs(weights_sum - 1.0) < 0.01, f"Weights sum should be 1.0, got {weights_sum}"
    print(f"✅ Quality weights sum to {weights_sum:.2f}")

    # Test 4: Quality labels
    print("\n1.4. Testing quality labels...")
    assert analyzer.get_quality_label(85) == "Excellent"
    assert analyzer.get_quality_label(65) == "Good"
    assert analyzer.get_quality_label(45) == "Fair"
    assert analyzer.get_quality_label(25) == "Poor"
    print("✅ Quality labels work correctly")

    # Test 5: Overall quality calculation
    print("\n1.5. Testing overall quality calculation...")
    quality = analyzer._calculate_overall_quality(
        blur_score=250.0,  # Good sharpness
        lighting_score=70.0,  # Good lighting
        size_score=60.0,  # Medium size
        aspect_ratio=1.0,  # Perfect aspect
        confidence=0.9  # High confidence
    )
    assert 0 <= quality <= 100, f"Quality should be 0-100, got {quality}"
    print(f"✅ Overall quality calculation: {quality:.1f}/100")

    print("\n" + "=" * 80)
    print("✅ FaceQualityAnalyzer: ALL TESTS PASSED")
    print("=" * 80)


def test_clustering_quality_analyzer():
    """Test ClusteringQualityAnalyzer basic functionality."""
    print("\n" + "=" * 80)
    print("TEST 2: ClusteringQualityAnalyzer")
    print("=" * 80)

    analyzer = ClusteringQualityAnalyzer()

    # Test 1: Perfect clustering (3 well-separated clusters)
    print("\n2.1. Testing perfect clustering scenario...")
    np.random.seed(42)

    # Create 3 well-separated clusters
    cluster1 = np.random.randn(20, 128) + np.array([5, 0] + [0] * 126)  # Centered at (5,0,...)
    cluster2 = np.random.randn(20, 128) + np.array([0, 5] + [0] * 126)  # Centered at (0,5,...)
    cluster3 = np.random.randn(20, 128) + np.array([-5, 0] + [0] * 126)  # Centered at (-5,0,...)

    embeddings = np.vstack([cluster1, cluster2, cluster3])
    labels = np.array([0] * 20 + [1] * 20 + [2] * 20)

    metrics = analyzer.analyze_clustering(embeddings, labels, metric='euclidean')

    assert isinstance(metrics, ClusterQualityMetrics)
    assert metrics.cluster_count == 3
    assert metrics.face_count == 60
    assert metrics.noise_count == 0
    assert metrics.noise_ratio == 0.0
    print(f"✅ Perfect clustering metrics:")
    print(f"   - Silhouette Score: {metrics.silhouette_score:.3f} ({analyzer._get_quality_label(metrics.overall_quality)})")
    print(f"   - Davies-Bouldin Index: {metrics.davies_bouldin_index:.3f}")
    print(f"   - Overall Quality: {metrics.overall_quality:.1f}/100 ({metrics.quality_label})")

    # Test 2: Poor clustering (overlapping clusters)
    print("\n2.2. Testing poor clustering scenario...")
    np.random.seed(42)

    # Create 2 overlapping clusters
    cluster1 = np.random.randn(15, 128) + np.array([1, 0] + [0] * 126)
    cluster2 = np.random.randn(15, 128) + np.array([0.5, 0] + [0] * 126)  # Close to cluster1

    embeddings_poor = np.vstack([cluster1, cluster2])
    labels_poor = np.array([0] * 15 + [1] * 15)

    metrics_poor = analyzer.analyze_clustering(embeddings_poor, labels_poor, metric='euclidean')

    print(f"✅ Poor clustering metrics:")
    print(f"   - Silhouette Score: {metrics_poor.silhouette_score:.3f}")
    print(f"   - Davies-Bouldin Index: {metrics_poor.davies_bouldin_index:.3f}")
    print(f"   - Overall Quality: {metrics_poor.overall_quality:.1f}/100 ({metrics_poor.quality_label})")

    # Test 3: Clustering with noise
    print("\n2.3. Testing clustering with noise...")
    embeddings_with_noise = np.vstack([cluster1, cluster2, cluster3])
    labels_with_noise = np.array([0] * 20 + [1] * 15 + [-1] * 5 + [2] * 20)  # 5 noise points

    metrics_noise = analyzer.analyze_clustering(embeddings_with_noise, labels_with_noise, metric='euclidean')

    assert metrics_noise.noise_count == 5
    assert abs(metrics_noise.noise_ratio - (5/60)) < 0.01
    print(f"✅ Clustering with noise:")
    print(f"   - Noise Count: {metrics_noise.noise_count}")
    print(f"   - Noise Ratio: {metrics_noise.noise_ratio:.1%}")
    print(f"   - Overall Quality: {metrics_noise.overall_quality:.1f}/100")

    # Test 4: Tuning suggestions
    print("\n2.4. Testing tuning suggestions...")
    suggestions = analyzer.get_tuning_suggestions(metrics_poor)
    assert len(suggestions) > 0
    print(f"✅ Tuning suggestions generated: {len(suggestions)} suggestion(s)")
    for i, suggestion in enumerate(suggestions, 1):
        print(f"   {i}. {suggestion[:80]}...")

    # Test 5: Edge cases
    print("\n2.5. Testing edge cases...")

    # Single cluster
    single_cluster_labels = np.array([0] * 30)
    metrics_single = analyzer.analyze_clustering(embeddings[:30], single_cluster_labels, metric='euclidean')
    assert metrics_single.cluster_count == 1
    print(f"✅ Single cluster handled: quality={metrics_single.overall_quality:.1f}/100")

    # All noise
    all_noise_labels = np.array([-1] * 30)
    metrics_all_noise = analyzer.analyze_clustering(embeddings[:30], all_noise_labels, metric='euclidean')
    assert metrics_all_noise.cluster_count == 0
    assert metrics_all_noise.noise_ratio == 1.0
    print(f"✅ All noise handled: quality={metrics_all_noise.overall_quality:.1f}/100")

    print("\n" + "=" * 80)
    print("✅ ClusteringQualityAnalyzer: ALL TESTS PASSED")
    print("=" * 80)


def test_integration():
    """Test integration between components."""
    print("\n" + "=" * 80)
    print("TEST 3: Integration Tests")
    print("=" * 80)

    # Test 1: Quality metrics to_dict() serialization
    print("\n3.1. Testing FaceQualityMetrics serialization...")
    from services.face_quality_analyzer import FaceQualityMetrics

    metrics = FaceQualityMetrics(
        blur_score=150.0,
        lighting_score=75.0,
        size_score=65.0,
        aspect_ratio=1.1,
        confidence=0.85,
        overall_quality=72.5,
        is_good_quality=True,
        quality_label="Good"
    )

    metrics_dict = metrics.to_dict()
    assert isinstance(metrics_dict, dict)
    assert metrics_dict['blur_score'] == 150.0
    assert metrics_dict['overall_quality'] == 72.5
    assert metrics_dict['quality_label'] == "Good"
    print("✅ FaceQualityMetrics serialization works")

    # Test 2: ClusterQualityMetrics to_dict() serialization
    print("\n3.2. Testing ClusterQualityMetrics serialization...")
    from services.clustering_quality_analyzer import ClusterQualityMetrics

    cluster_metrics = ClusterQualityMetrics(
        silhouette_score=0.65,
        davies_bouldin_index=0.75,
        avg_cluster_compactness=0.25,
        avg_cluster_separation=1.5,
        noise_ratio=0.12,
        cluster_count=5,
        face_count=50,
        noise_count=6,
        cluster_sizes=[12, 10, 8, 11, 9],
        cluster_silhouettes=[0.7, 0.65, 0.6, 0.68, 0.62],
        overall_quality=68.5,
        quality_label="Good"
    )

    cluster_dict = cluster_metrics.to_dict()
    assert isinstance(cluster_dict, dict)
    assert cluster_dict['cluster_count'] == 5
    assert len(cluster_dict['cluster_sizes']) == 5
    print("✅ ClusterQualityMetrics serialization works")

    # Test 3: Quality weights configuration
    print("\n3.3. Testing quality weights consistency...")
    face_analyzer = FaceQualityAnalyzer()
    cluster_analyzer = ClusteringQualityAnalyzer()

    face_weights_sum = sum(face_analyzer.QUALITY_WEIGHTS.values())
    cluster_weights_sum = sum(cluster_analyzer.QUALITY_WEIGHTS.values())

    assert abs(face_weights_sum - 1.0) < 0.01
    assert abs(cluster_weights_sum - 1.0) < 0.01
    print(f"✅ Quality weights are normalized:")
    print(f"   - Face weights sum: {face_weights_sum:.3f}")
    print(f"   - Cluster weights sum: {cluster_weights_sum:.3f}")

    print("\n" + "=" * 80)
    print("✅ Integration Tests: ALL TESTS PASSED")
    print("=" * 80)


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "PHASE 2A: QUALITY METRICS TEST SUITE" + " " * 26 + "║")
    print("╚" + "=" * 78 + "╝")

    try:
        test_face_quality_analyzer()
        test_clustering_quality_analyzer()
        test_integration()

        print("\n")
        print("╔" + "=" * 78 + "╗")
        print("║" + " " * 30 + "ALL TESTS PASSED! ✅" + " " * 28 + "║")
        print("╚" + "=" * 78 + "╝")
        print("\n")

        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
