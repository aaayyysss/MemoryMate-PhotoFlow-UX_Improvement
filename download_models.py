#!/usr/bin/env python3
"""
Model Download Script for PyInstaller Packaging

This script ensures InsightFace models are downloaded before packaging
with PyInstaller. Run this BEFORE building the executable.

Usage:
    python download_models.py

Requirements:
    pip install insightface onnxruntime
"""

import os
import sys
from pathlib import Path


def check_models_downloaded():
    """
    Check if InsightFace buffalo_l models are already downloaded.

    Returns:
        tuple: (bool, str) - (models_exist, models_path)
    """
    # Default InsightFace model location
    models_dir = os.path.expanduser('~/.insightface/models/buffalo_l')

    if os.path.exists(models_dir):
        # Count model files
        model_files = list(Path(models_dir).rglob('*.onnx'))
        if len(model_files) >= 2:  # buffalo_l should have detection + recognition models
            return True, models_dir

    return False, models_dir


def download_models():
    """
    Download InsightFace buffalo_l models by initializing the service.

    This triggers the automatic download from InsightFace's model zoo.
    """
    print("=" * 80)
    print("MemoryMate-PhotoFlow Model Download")
    print("=" * 80)
    print()

    # Check if already downloaded
    models_exist, models_path = check_models_downloaded()

    if models_exist:
        print(f"✓ Models already downloaded at: {models_path}")

        # List model files
        model_files = list(Path(models_path).rglob('*.onnx'))
        print(f"✓ Found {len(model_files)} model files:")
        for f in model_files:
            size_mb = f.stat().st_size / (1024 * 1024)
            print(f"  - {f.name} ({size_mb:.1f} MB)")

        print()
        print("✅ Models are ready for PyInstaller packaging!")
        return True

    print(f"📦 Models not found at: {models_path}")
    print("🔄 Downloading buffalo_l models from InsightFace model zoo...")
    print()

    try:
        # Import and initialize InsightFace
        # This will trigger automatic model download
        from insightface.app import FaceAnalysis

        print("Initializing InsightFace (this will download ~200MB of models)...")
        app = FaceAnalysis(
            name='buffalo_l',
            allowed_modules=['detection', 'recognition']
        )

        print("Preparing models (this may take a minute)...")
        app.prepare(ctx_id=-1, det_size=(640, 640))  # CPU mode

        # Verify download
        models_exist, models_path = check_models_downloaded()

        if models_exist:
            print()
            print("=" * 80)
            print("✅ SUCCESS! Models downloaded successfully")
            print("=" * 80)
            print(f"Location: {models_path}")

            # List downloaded files
            model_files = list(Path(models_path).rglob('*.onnx'))
            print(f"Downloaded {len(model_files)} model files:")
            for f in model_files:
                size_mb = f.stat().st_size / (1024 * 1024)
                print(f"  - {f.name} ({size_mb:.1f} MB)")

            print()
            print("✅ You can now proceed with PyInstaller packaging!")
            print("   Run: pyinstaller memorymate_pyinstaller.spec")
            return True
        else:
            print()
            print("❌ ERROR: Models download completed but files not found")
            print(f"Expected location: {models_path}")
            return False

    except ImportError as e:
        print()
        print("=" * 80)
        print("❌ ERROR: InsightFace not installed")
        print("=" * 80)
        print()
        print("Please install required packages:")
        print("  pip install insightface onnxruntime")
        print()
        return False

    except Exception as e:
        print()
        print("=" * 80)
        print(f"❌ ERROR: Failed to download models")
        print("=" * 80)
        print(f"Error: {e}")
        print()
        print("Troubleshooting:")
        print("  1. Check your internet connection")
        print("  2. Ensure you have write permissions to ~/.insightface/")
        print("  3. Try installing manually:")
        print("     python -c \"from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_l'); app.prepare(ctx_id=-1)\"")
        print()
        return False


def main():
    """Main entry point."""
    success = download_models()

    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
