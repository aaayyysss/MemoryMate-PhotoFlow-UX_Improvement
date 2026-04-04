"""
Test InsightFace model loading and initialization.

Provides comprehensive testing of model paths and InsightFace initialization
without requiring app restart.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Tuple, Dict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_model_path(model_path: str) -> Tuple[bool, str]:
    """
    Test if InsightFace models can be loaded from the specified path.

    Uses the proven proof of concept approach:
    - Accepts both det_10g.onnx and scrfd_10g_bnkps.onnx
    - Passes buffalo_l directory DIRECTLY as root
    - Does NOT pass providers to FaceAnalysis.__init__()

    Args:
        model_path: Path to test (buffalo_l directory or parent)

    Returns:
        Tuple[bool, str]: (success, message)
    """
    try:
        logger.info("="  * 70)
        logger.info("InsightFace Model Path Test")
        logger.info("=" * 70)
        logger.info(f"Testing path: {model_path}\n")

        # Step 1: Validate path exists
        if not os.path.exists(model_path):
            return False, f"Path does not exist: {model_path}"

        # Step 2: Detect structure (accept both detector variants)
        detector_variants = ['det_10g.onnx', 'scrfd_10g_bnkps.onnx']

        def has_detector(path):
            """Check if path contains at least one detector variant."""
            for detector in detector_variants:
                if os.path.exists(os.path.join(path, detector)):
                    return True, detector
            return False, None

        # Try as buffalo_l directory first
        has_det, detector_found = has_detector(model_path)
        if has_det:
            logger.info(f"✓ Detected: buffalo_l directory (detector: {detector_found})")
            buffalo_dir = model_path
        else:
            # Try models/buffalo_l/ subdirectory
            buffalo_subdir = os.path.join(model_path, 'models', 'buffalo_l')
            has_det, detector_found = has_detector(buffalo_subdir)
            if has_det:
                logger.info(f"✓ Detected: models/buffalo_l/ structure (detector: {detector_found})")
                buffalo_dir = buffalo_subdir
            else:
                # Try buffalo_l/ subdirectory (non-standard)
                buffalo_subdir = os.path.join(model_path, 'buffalo_l')
                has_det, detector_found = has_detector(buffalo_subdir)
                if has_det:
                    logger.info(f"✓ Detected: buffalo_l/ subdirectory (detector: {detector_found})")
                    buffalo_dir = buffalo_subdir
                else:
                    # Try nested buffalo_l/buffalo_l/ (user's structure)
                    buffalo_nested = os.path.join(model_path, 'buffalo_l', 'buffalo_l')
                    has_det, detector_found = has_detector(buffalo_nested)
                    if has_det:
                        logger.info(f"✓ Detected: nested buffalo_l/buffalo_l/ (detector: {detector_found})")
                        buffalo_dir = buffalo_nested
                    else:
                        return False, (
                            f"No valid buffalo_l models found at {model_path}\n\n"
                            f"Expected at least one detector variant:\n"
                            f"  - det_10g.onnx (standard)\n"
                            f"  - scrfd_10g_bnkps.onnx (alternative)\n\n"
                            f"And recognition model:\n"
                            f"  - w600k_r50.onnx"
                        )

        # Step 3: Verify essential files
        logger.info("\nVerifying model files...")

        # Check for at least ONE detector variant
        detector_found_file = None
        for detector in detector_variants:
            filepath = os.path.join(buffalo_dir, detector)
            if os.path.exists(filepath):
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                logger.info(f"  ✓ {detector} ({size_mb:.1f} MB) [DETECTOR]")
                detector_found_file = detector
                break

        if not detector_found_file:
            return False, f"Missing detector model (need one of: {', '.join(detector_variants)})"

        # Check for recognition model
        recognition_file = 'w600k_r50.onnx'
        filepath = os.path.join(buffalo_dir, recognition_file)
        if os.path.exists(filepath):
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            logger.info(f"  ✓ {recognition_file} ({size_mb:.1f} MB) [RECOGNITION]")
        else:
            return False, f"Missing recognition model: {recognition_file}"

        # Step 4: Test InsightFace initialization (proof of concept approach)
        logger.info("\nTesting InsightFace initialization...")

        try:
            from insightface.app import FaceAnalysis
            import onnxruntime as ort

            # Detect providers
            available_providers = ort.get_available_providers()
            if 'CUDAExecutionProvider' in available_providers:
                providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                logger.info("  ✓ Detected: GPU (CUDA) available")
            else:
                providers = ['CPUExecutionProvider']
                logger.info("  ✓ Detected: CPU only")

            # CRITICAL: Pass buffalo_l directory DIRECTLY as root (proof of concept approach)
            # DO NOT pass providers to FaceAnalysis.__init__() for compatibility
            logger.info(f"\n  Initializing with buffalo_l directory: {buffalo_dir}")
            app = FaceAnalysis(name='buffalo_l', root=buffalo_dir)

            # Use providers ONLY for ctx_id selection (proof of concept approach)
            use_cuda = isinstance(providers, (list, tuple)) and 'CUDAExecutionProvider' in providers
            ctx_id = 0 if use_cuda else -1
            logger.info(f"  Using {'GPU' if use_cuda else 'CPU'} (ctx_id={ctx_id})")

            # Prepare with simple parameters (matches proof of concept)
            app.prepare(ctx_id=ctx_id, det_size=(640, 640))

            logger.info("\n✅ SUCCESS! InsightFace initialized successfully")
            logger.info("=" * 70)

            success_msg = (
                f"✓ InsightFace models loaded successfully!\n\n"
                f"Configuration:\n"
                f"  • Buffalo_l Directory: {buffalo_dir}\n"
                f"  • Detector: {detector_found_file}\n"
                f"  • Recognition: {recognition_file}\n"
                f"  • Providers: {', '.join(providers)}\n"
                f"  • Hardware: {'GPU (CUDA)' if use_cuda else 'CPU'}\n\n"
                f"Face detection is ready to use!"
            )

            return True, success_msg

        except ImportError as e:
            logger.error(f"\n✗ FAILED: InsightFace library not installed")
            logger.error(f"  Error: {e}")
            return False, f"InsightFace library not installed: {e}\n\nInstall with: pip install insightface onnxruntime"

        except Exception as e:
            logger.error(f"\n✗ FAILED: InsightFace initialization error")
            logger.error(f"  Error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False, f"InsightFace initialization failed: {e}\n\nCheck that model files are not corrupted and match your InsightFace version."

    except Exception as e:
        logger.error(f"\nUnexpected error during test: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False, f"Test failed with error: {e}"


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Test InsightFace model loading')
    parser.add_argument('path', help='Path to test (buffalo_l directory or parent)')
    args = parser.parse_args()

    success, message = test_model_path(args.path)

    print("\n" + "=" * 70)
    print("TEST RESULT")
    print("=" * 70)
    print(message)
    print("=" * 70 + "\n")

    sys.exit(0 if success else 1)
