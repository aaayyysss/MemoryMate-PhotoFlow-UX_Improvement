#!/usr/bin/env python3
"""
Comprehensive InsightFace Model Diagnostic Tool

Performs detailed diagnostics on InsightFace models to identify issues:
- File existence and permissions
- File size and integrity
- Directory structure
- InsightFace version compatibility
- Actual model loading test
"""

import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def diagnose_models(path: str = None) -> dict:
    """
    Comprehensive diagnostic of InsightFace models.

    Args:
        path: Path to diagnose (auto-detects if None)

    Returns:
        dict: Diagnostic results
    """
    results = {
        'success': False,
        'issues': [],
        'warnings': [],
        'info': []
    }

    logger.info("=" * 80)
    logger.info("INSIGHTFACE MODEL DIAGNOSTIC TOOL")
    logger.info("=" * 80)
    logger.info("")

    # Step 1: Determine path to check
    if path:
        check_paths = [path]
        logger.info(f"Checking specified path: {path}")
    else:
        logger.info("Auto-detecting model paths...")
        check_paths = []

        # Check custom path from settings
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            custom_path = settings.get_setting('insightface_model_path', '')
            if custom_path:
                check_paths.append(custom_path)
                logger.info(f"  • Custom path from settings: {custom_path}")
        except Exception as e:
            logger.debug(f"Could not read settings: {e}")

        # Check app directory
        try:
            app_root = Path(__file__).parent.parent
            app_models = app_root / 'models' / 'buffalo_l'
            check_paths.append(str(app_models))
            logger.info(f"  • App directory: {app_models}")
        except Exception as e:
            logger.debug(f"Could not determine app root: {e}")

        # Check user home
        user_home = Path.home() / '.insightface' / 'models' / 'buffalo_l'
        check_paths.append(str(user_home))
        logger.info(f"  • User home: {user_home}")

    logger.info("")

    # Detector variants (accept either one)
    detector_variants = ['det_10g.onnx', 'scrfd_10g_bnkps.onnx']

    def has_detector(path):
        """Check if path contains at least one detector variant."""
        for detector in detector_variants:
            if os.path.exists(os.path.join(path, detector)):
                return True, detector
        return False, None

    # Step 2: Check each path
    found_path = None
    detector_found = None
    for test_path in check_paths:
        logger.info(f"Checking: {test_path}")

        if not os.path.exists(test_path):
            logger.warning(f"  ✗ Path does not exist")
            continue

        # Check if it's buffalo_l directory
        has_det, det_file = has_detector(test_path)
        if has_det:
            found_path = test_path
            detector_found = det_file
            logger.info(f"  ✓ Found buffalo_l directory (detector: {det_file})")
            break
        else:
            # Check for models/buffalo_l/ subdirectory
            buffalo_sub = os.path.join(test_path, 'models', 'buffalo_l')
            has_det, det_file = has_detector(buffalo_sub)
            if has_det:
                found_path = buffalo_sub
                detector_found = det_file
                logger.info(f"  ✓ Found buffalo_l at: {buffalo_sub} (detector: {det_file})")
                break
            else:
                # Check for buffalo_l/ subdirectory (non-standard)
                buffalo_sub = os.path.join(test_path, 'buffalo_l')
                has_det, det_file = has_detector(buffalo_sub)
                if has_det:
                    found_path = buffalo_sub
                    detector_found = det_file
                    logger.info(f"  ✓ Found buffalo_l at: {buffalo_sub} (detector: {det_file})")
                    break
                else:
                    # Check for nested buffalo_l/buffalo_l/ (user's structure)
                    buffalo_nested = os.path.join(test_path, 'buffalo_l', 'buffalo_l')
                    has_det, det_file = has_detector(buffalo_nested)
                    if has_det:
                        found_path = buffalo_nested
                        detector_found = det_file
                        logger.info(f"  ✓ Found buffalo_l at: {buffalo_nested} (detector: {det_file})")
                        break
                    else:
                        logger.warning(f"  ✗ No buffalo_l models found")

    if not found_path:
        results['issues'].append("No buffalo_l models found at any checked location")
        logger.error("\n❌ CRITICAL: No buffalo_l models found!")
        logger.info("\nExpected structure:")
        logger.info("  path/to/buffalo_l/")
        logger.info("    ├── det_10g.onnx")
        logger.info("    ├── w600k_r50.onnx")
        logger.info("    └── ... (other model files)")
        return results

    logger.info(f"\n✓ Using buffalo_l directory: {found_path}")
    results['info'].append(f"Buffalo_l directory: {found_path}")

    # Step 3: Check required files (accept EITHER detector variant)
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1: Checking Model Files")
    logger.info("=" * 80)

    all_files_ok = True

    # Check detector (at least ONE variant required)
    detector_ok = False
    for detector in detector_variants:
        filepath = os.path.join(found_path, detector)
        if os.path.exists(filepath):
            size_mb = os.path.getsize(filepath) / (1024 * 1024)

            if size_mb < 100:
                logger.error(f"✗ CORRUPTED: {detector} ({size_mb:.1f} MB) - Too small, expected 100-200 MB")
                results['issues'].append(f"File too small (possibly corrupted): {detector}")
            elif size_mb > 200:
                logger.warning(f"⚠ UNUSUAL: {detector} ({size_mb:.1f} MB) - Larger than expected 200 MB")
                results['warnings'].append(f"File larger than expected: {detector}")
                logger.info(f"  ✓ {detector} ({size_mb:.1f} MB) - Detection model [FOUND]")
                detector_ok = True
            else:
                logger.info(f"  ✓ {detector} ({size_mb:.1f} MB) - Detection model [FOUND]")
                detector_ok = True

            # Check permissions
            if not os.access(filepath, os.R_OK):
                logger.error(f"✗ PERMISSION: {detector} - Not readable")
                results['issues'].append(f"Cannot read file (permission denied): {detector}")
                detector_ok = False

            break  # Found one detector, that's enough

    if not detector_ok:
        logger.error(f"✗ MISSING: Detection model - Need one of: {', '.join(detector_variants)}")
        results['issues'].append(f"Missing detection model (need one of: {', '.join(detector_variants)})")
        all_files_ok = False

    # Check recognition model (always required)
    recognition_file = 'w600k_r50.onnx'
    filepath = os.path.join(found_path, recognition_file)

    if not os.path.exists(filepath):
        logger.error(f"✗ MISSING: {recognition_file} - Recognition model (ArcFace)")
        results['issues'].append(f"Missing required file: {recognition_file}")
        all_files_ok = False
    else:
        size_mb = os.path.getsize(filepath) / (1024 * 1024)

        if size_mb < 30:
            logger.error(f"✗ CORRUPTED: {recognition_file} ({size_mb:.1f} MB) - Too small, expected 30-100 MB")
            results['issues'].append(f"File too small (possibly corrupted): {recognition_file}")
            all_files_ok = False
        elif size_mb > 100:
            logger.warning(f"⚠ UNUSUAL: {recognition_file} ({size_mb:.1f} MB) - Larger than expected 100 MB")
            results['warnings'].append(f"File larger than expected: {recognition_file}")
            logger.info(f"  ✓ {recognition_file} ({size_mb:.1f} MB) - Recognition model (ArcFace)")
        else:
            logger.info(f"  ✓ {recognition_file} ({size_mb:.1f} MB) - Recognition model (ArcFace)")

        # Check permissions
        if not os.access(filepath, os.R_OK):
            logger.error(f"✗ PERMISSION: {recognition_file} - Not readable")
            results['issues'].append(f"Cannot read file (permission denied): {recognition_file}")
            all_files_ok = False

    if not all_files_ok:
        logger.error("\n❌ CRITICAL: Required model files are missing or corrupted!")
        return results

    # Step 4: Check directory structure
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: Checking Directory Structure")
    logger.info("=" * 80)

    dir_name = os.path.basename(found_path)
    logger.info(f"✓ Directory: {found_path}")
    logger.info(f"✓ Directory name: {dir_name}")

    # Using proof of concept approach: pass buffalo_l directory DIRECTLY as root
    logger.info(f"✓ Will use buffalo_l directory directly as root (proof of concept approach)")
    results['info'].append(f"Using buffalo_l directory as root: {found_path}")

    # Step 5: Check InsightFace installation
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Checking InsightFace Installation")
    logger.info("=" * 80)

    try:
        import insightface
        logger.info(f"✓ InsightFace installed: version {insightface.__version__ if hasattr(insightface, '__version__') else 'unknown'}")
        results['info'].append(f"InsightFace version: {insightface.__version__ if hasattr(insightface, '__version__') else 'unknown'}")
    except ImportError as e:
        logger.error(f"✗ InsightFace not installed: {e}")
        results['issues'].append("InsightFace library not installed")
        logger.info("\nInstall with: pip install insightface onnxruntime")
        return results

    try:
        import onnxruntime as ort
        ort_version = ort.__version__
        logger.info(f"✓ ONNXRuntime installed: version {ort_version}")
        results['info'].append(f"ONNXRuntime version: {ort_version}")

        # Check providers
        providers = ort.get_available_providers()
        logger.info(f"✓ Available providers: {', '.join(providers)}")
        results['info'].append(f"Available providers: {', '.join(providers)}")
    except ImportError as e:
        logger.error(f"✗ ONNXRuntime not installed: {e}")
        results['issues'].append("ONNXRuntime library not installed")
        logger.info("\nInstall with: pip install onnxruntime")
        return results

    # Step 6: Test actual model loading (proof of concept approach)
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: Testing Model Loading")
    logger.info("=" * 80)

    try:
        from insightface.app import FaceAnalysis

        # CRITICAL: Pass buffalo_l directory DIRECTLY as root (proof of concept approach)
        # DO NOT pass providers to FaceAnalysis.__init__() for compatibility
        logger.info(f"\n  Initializing FaceAnalysis...")
        logger.info(f"  Using buffalo_l directory as root: {found_path}")
        logger.info(f"  NOT passing providers parameter (proof of concept approach)")

        app = FaceAnalysis(name='buffalo_l', root=found_path)

        logger.info(f"  ✓ FaceAnalysis created successfully")

        # Use providers ONLY for ctx_id selection (proof of concept approach)
        use_cuda = isinstance(providers, (list, tuple)) and 'CUDAExecutionProvider' in providers
        ctx_id = 0 if use_cuda else -1

        logger.info(f"\n  Preparing model...")
        logger.info(f"  Hardware: {'GPU (CUDA)' if use_cuda else 'CPU'}")
        logger.info(f"  Context ID: {ctx_id}")

        # Prepare with simple parameters (matches proof of concept)
        app.prepare(ctx_id=ctx_id, det_size=(640, 640))

        logger.info(f"  ✓ Model prepared successfully")

        # Verify detection model loaded
        if hasattr(app, 'models'):
            if 'detection' in app.models:
                logger.info(f"  ✓ Detection model loaded: {type(app.models['detection']).__name__}")
                results['success'] = True
            else:
                logger.error(f"  ✗ Detection model not loaded!")
                logger.error(f"    Available models: {list(app.models.keys())}")
                results['issues'].append("Detection model not loaded")
                return results

        logger.info("\n" + "=" * 80)
        logger.info("✅ ALL TESTS PASSED - Models are working correctly!")
        logger.info("=" * 80)

        results['success'] = True
        return results

    except Exception as e:
        logger.error(f"\n✗ Model loading failed: {e}")
        logger.error(f"  Error type: {type(e).__name__}")

        import traceback
        traceback_str = traceback.format_exc()
        logger.debug(f"\nFull traceback:\n{traceback_str}")

        results['issues'].append(f"Model loading failed: {e}")

        # Provide specific guidance based on error type
        if "AssertionError" in str(type(e).__name__):
            logger.info("\n💡 AssertionError suggests model files are not being loaded correctly.")
            logger.info("   Possible causes:")
            logger.info("   1. Model files are corrupted")
            logger.info("   2. InsightFace version incompatible with model files")
            logger.info("   3. Directory structure issue")
        elif "RuntimeError" in str(type(e).__name__) and "model routing" in str(e):
            logger.info("\n💡 Model routing error suggests path/structure issue.")
            logger.info("   Try reorganizing to standard structure: .../models/buffalo_l/")

        return results


def print_summary(results: dict):
    """Print diagnostic summary."""
    print("\n" + "=" * 80)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 80)

    if results['info']:
        print("\n📋 Information:")
        for info in results['info']:
            print(f"  • {info}")

    if results['warnings']:
        print("\n⚠️  Warnings:")
        for warning in results['warnings']:
            print(f"  • {warning}")

    if results['issues']:
        print("\n❌ Issues Found:")
        for issue in results['issues']:
            print(f"  • {issue}")

    if results['success']:
        print("\n✅ RESULT: InsightFace models are working correctly!")
    else:
        print("\n❌ RESULT: Issues detected that prevent model loading")
        print("\nRecommended Actions:")
        if any("missing" in i.lower() or "corrupted" in i.lower() for i in results['issues']):
            print("  1. Re-download buffalo_l models")
            print("  2. Run: python download_face_models.py")
        if any("structure" in i.lower() for i in results['issues'] + results['warnings']):
            print("  1. Reorganize to standard structure: .../models/buffalo_l/")
        if any("permission" in i.lower() for i in results['issues']):
            print("  1. Check file permissions")
            print("  2. Ensure files are readable")

    print("=" * 80 + "\n")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Diagnose InsightFace model issues')
    parser.add_argument('--path', help='Specific path to check (optional, auto-detects if omitted)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    results = diagnose_models(args.path)
    print_summary(results)

    sys.exit(0 if results['success'] else 1)
