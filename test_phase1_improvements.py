#!/usr/bin/env python3
"""
Test script for Phase 1 improvements:
- Configuration validation
- Adaptive parameter selection
- Performance monitoring
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.getcwd())

def test_performance_monitor():
    """Test PerformanceMonitor import and basic functionality."""
    print("=" * 70)
    print("TEST 1: PerformanceMonitor Import and Basic Functionality")
    print("=" * 70)

    try:
        from services.performance_monitor import PerformanceMonitor, OperationMetric
        print("✅ PerformanceMonitor imports successfully")

        # Test basic usage
        monitor = PerformanceMonitor("test_session")
        print("✅ PerformanceMonitor instantiation successful")

        # Test recording operation
        metric = monitor.record_operation("test_operation", {"test_key": "test_value"})
        print("✅ record_operation() works")

        # Simulate work
        import time
        time.sleep(0.1)

        # Finish operation
        metric.finish()
        print("✅ metric.finish() works")

        # Test summary generation
        monitor.finish_monitoring()
        summary = monitor.get_summary()
        print(f"✅ get_summary() works - found {summary['total_operations']} operations")

        # Test print summary
        monitor.print_summary()

        return True
    except Exception as e:
        print(f"❌ PerformanceMonitor test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_validation():
    """Test configuration validation."""
    print("\n" + "=" * 70)
    print("TEST 2: Configuration Validation")
    print("=" * 70)

    try:
        from config.face_detection_config import FaceDetectionConfig
        print("✅ FaceDetectionConfig imports successfully")

        config = FaceDetectionConfig()
        print("✅ FaceDetectionConfig instantiation successful")

        # Test valid value
        is_valid, msg = config.validate_value("clustering_eps", 0.35)
        assert is_valid, f"Valid value rejected: {msg}"
        print("✅ Valid value accepted: clustering_eps=0.35")

        # Test invalid type
        is_valid, msg = config.validate_value("clustering_eps", "0.35")
        assert not is_valid, "Invalid type accepted"
        print(f"✅ Invalid type rejected: {msg}")

        # Test out of range (too low)
        is_valid, msg = config.validate_value("clustering_eps", 0.10)
        assert not is_valid, "Out of range value accepted"
        print(f"✅ Out of range value rejected: {msg}")

        # Test out of range (too high)
        is_valid, msg = config.validate_value("clustering_eps", 0.60)
        assert not is_valid, "Out of range value accepted"
        print(f"✅ Out of range value rejected: {msg}")

        # Test set() with validation
        try:
            config.set("clustering_eps", 0.32)
            print("✅ set() accepts valid value")
        except ValueError:
            print("❌ set() rejected valid value")
            return False

        try:
            config.set("clustering_eps", 0.10)
            print("❌ set() accepted invalid value")
            return False
        except ValueError as e:
            print(f"✅ set() rejects invalid value: {e}")

        return True
    except Exception as e:
        print(f"❌ Configuration validation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_adaptive_parameters():
    """Test adaptive parameter selection."""
    print("\n" + "=" * 70)
    print("TEST 3: Adaptive Parameter Selection")
    print("=" * 70)

    try:
        from config.face_detection_config import FaceDetectionConfig

        config = FaceDetectionConfig()
        print("✅ FaceDetectionConfig loaded")

        # Test tiny dataset
        params = config.get_optimal_clustering_params(30)
        assert params["category"] == "tiny", f"Expected 'tiny', got '{params['category']}'"
        assert params["eps"] == 0.42, f"Expected eps=0.42, got {params['eps']}"
        print(f"✅ Tiny dataset (30 faces): eps={params['eps']}, min_samples={params['min_samples']}")
        print(f"   Rationale: {params['rationale']}")

        # Test small dataset
        params = config.get_optimal_clustering_params(150)
        assert params["category"] == "small", f"Expected 'small', got '{params['category']}'"
        assert params["eps"] == 0.38, f"Expected eps=0.38, got {params['eps']}"
        print(f"✅ Small dataset (150 faces): eps={params['eps']}, min_samples={params['min_samples']}")

        # Test medium dataset
        params = config.get_optimal_clustering_params(500)
        assert params["category"] == "medium", f"Expected 'medium', got '{params['category']}'"
        assert params["eps"] == 0.35, f"Expected eps=0.35, got {params['eps']}"
        print(f"✅ Medium dataset (500 faces): eps={params['eps']}, min_samples={params['min_samples']}")

        # Test large dataset
        params = config.get_optimal_clustering_params(3000)
        assert params["category"] == "large", f"Expected 'large', got '{params['category']}'"
        assert params["eps"] == 0.32, f"Expected eps=0.32, got {params['eps']}"
        print(f"✅ Large dataset (3000 faces): eps={params['eps']}, min_samples={params['min_samples']}")

        # Test xlarge dataset
        params = config.get_optimal_clustering_params(8000)
        assert params["category"] == "xlarge", f"Expected 'xlarge', got '{params['category']}'"
        assert params["eps"] == 0.30, f"Expected eps=0.30, got {params['eps']}"
        print(f"✅ XLarge dataset (8000 faces): eps={params['eps']}, min_samples={params['min_samples']}")

        return True
    except Exception as e:
        print(f"❌ Adaptive parameter selection test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_worker_imports():
    """Test that modified workers can be imported."""
    print("\n" + "=" * 70)
    print("TEST 4: Worker Import Validation")
    print("=" * 70)

    try:
        from workers.face_cluster_worker import FaceClusterWorker, FaceClusterSignals
        print("✅ FaceClusterWorker imports successfully")

        from workers.face_detection_worker import FaceDetectionWorker, FaceDetectionSignals
        print("✅ FaceDetectionWorker imports successfully")

        # Test that workers can be instantiated
        # Note: We won't run them without a valid project_id and database
        print("✅ Worker classes available for use")

        return True
    except Exception as e:
        print(f"❌ Worker import test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("PHASE 1 IMPLEMENTATION VALIDATION")
    print("Testing: Configuration Validation, Adaptive Parameters, Performance Monitoring")
    print("=" * 70 + "\n")

    results = []

    # Run tests
    results.append(("PerformanceMonitor", test_performance_monitor()))
    results.append(("Configuration Validation", test_config_validation()))
    results.append(("Adaptive Parameters", test_adaptive_parameters()))
    results.append(("Worker Imports", test_worker_imports()))

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {name}")

    print("=" * 70)
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("✅ ALL TESTS PASSED - Phase 1 implementation validated successfully!")
        return 0
    else:
        print(f"❌ {total - passed} test(s) failed - please review errors above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
