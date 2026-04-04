"""
Test script to verify the clip_check.py indentation bug fix.

This script creates a mock model directory structure and verifies that
both legacy and new directory naming conventions are properly detected.
"""

import tempfile
import shutil
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from utils.clip_check import check_clip_availability, get_clip_download_status


def create_mock_model(base_path, dir_name, commit_hash="abc123"):
    """Create a mock CLIP model directory structure."""
    # Create directory structure
    snapshot_dir = base_path / 'models' / dir_name / 'snapshots' / commit_hash
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    refs_dir = base_path / 'models' / dir_name / 'refs'
    refs_dir.mkdir(parents=True, exist_ok=True)

    # Create required files
    required_files = [
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "tokenizer.json",
        "special_tokens_map.json",
        "pytorch_model.bin"
    ]

    for filename in required_files:
        file_path = snapshot_dir / filename
        file_path.write_text('{"mock": true}')

    # Create refs/main
    refs_main = refs_dir / 'main'
    refs_main.write_text(commit_hash)

    return snapshot_dir


def test_clip_detection():
    """Test that both old and new directory naming conventions are detected."""
    print("=" * 70)
    print("Testing CLIP Model Detection (Bug Fix Verification)")
    print("=" * 70)
    print()

    # Create temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Test 1: New directory naming convention (openai--clip-vit-base-patch32)
        print("[Test 1] New naming convention: openai--clip-vit-base-patch32")
        mock_path_new = create_mock_model(tmpdir, 'openai--clip-vit-base-patch32')
        print(f"  Created mock model at: {mock_path_new}")

        # Temporarily override _get_model_search_paths
        from utils import clip_check
        original_fn = clip_check._get_model_search_paths
        clip_check._get_model_search_paths = lambda: [str(tmpdir)]

        try:
            available, message = check_clip_availability('openai/clip-vit-base-patch32')
            print(f"  Available: {available}")
            if available:
                print("  ✅ PASS: Model detected with new naming convention")
            else:
                print("  ❌ FAIL: Model NOT detected with new naming convention")
                print(f"  Message: {message}")
                return False
        finally:
            # Clean up for next test
            clip_check._get_model_search_paths = original_fn

        print()

        # Test 2: Legacy directory naming convention (clip-vit-base-patch32)
        print("[Test 2] Legacy naming convention: clip-vit-base-patch32")
        tmpdir2 = Path(tempfile.mkdtemp())
        try:
            mock_path_legacy = create_mock_model(tmpdir2, 'clip-vit-base-patch32')
            print(f"  Created mock model at: {mock_path_legacy}")

            clip_check._get_model_search_paths = lambda: [str(tmpdir2)]

            available, message = check_clip_availability('openai/clip-vit-base-patch32')
            print(f"  Available: {available}")
            if available:
                print("  ✅ PASS: Model detected with legacy naming convention")
            else:
                print("  ❌ FAIL: Model NOT detected with legacy naming convention")
                print(f"  Message: {message}")
                return False
        finally:
            clip_check._get_model_search_paths = original_fn
            shutil.rmtree(tmpdir2)

        print()

        # Test 3: get_clip_download_status function
        print("[Test 3] Testing get_clip_download_status function")
        tmpdir3 = Path(tempfile.mkdtemp())
        try:
            mock_path = create_mock_model(tmpdir3, 'openai--clip-vit-large-patch14')
            print(f"  Created mock model at: {mock_path}")

            clip_check._get_model_search_paths = lambda: [str(tmpdir3)]

            status = get_clip_download_status('openai/clip-vit-large-patch14')
            print(f"  Available: {status['models_available']}")
            print(f"  Model path: {status.get('model_path', 'None')}")
            print(f"  Message: {status['message']}")

            if status['models_available']:
                print("  ✅ PASS: get_clip_download_status works correctly")
            else:
                print("  ❌ FAIL: get_clip_download_status failed")
                return False
        finally:
            clip_check._get_model_search_paths = original_fn
            shutil.rmtree(tmpdir3)

    print()
    print("=" * 70)
    print("All tests PASSED! ✅")
    print("=" * 70)
    print()
    print("Summary:")
    print("  ✓ New directory naming (openai--clip-vit-*) works")
    print("  ✓ Legacy directory naming (clip-vit-*) works")
    print("  ✓ get_clip_download_status works")
    print()
    print("The indentation bug has been successfully fixed!")
    return True


if __name__ == '__main__':
    try:
        success = test_clip_detection()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
