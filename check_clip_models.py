"""
Check CLIP Model Installation

This script verifies which CLIP models are installed and where.
Run this to diagnose why clip-vit-large-patch14 is not being detected.

Usage:
    python check_clip_models.py

What it checks:
    1. Current app directory
    2. Expected model locations
    3. Which models are found
    4. File structure validation
    5. Recommendations
"""

import os
import sys
from pathlib import Path


def check_models():
    """Check for installed CLIP models."""

    print("=" * 70)
    print("CLIP Model Installation Checker")
    print("=" * 70)
    print()

    # Get current directory
    current_dir = Path.cwd()
    print(f"Current directory: {current_dir}")
    print()

    # Expected model locations
    models_dir = current_dir / 'models'

    print(f"Looking for models in: {models_dir}")
    print()

    if not models_dir.exists():
        print("❌ ERROR: models/ directory not found!")
        print()
        print("Expected location:")
        print(f"  {models_dir}")
        print()
        print("This directory should exist. Something is wrong.")
        return

    print("✓ models/ directory exists")
    print()

    # Check for each model
    models_to_check = {
        'clip-vit-base-patch32': 'Base model (512-D, 600MB)',
        'clip-vit-base-patch16': 'Base model (512-D, 600MB)',
        'clip-vit-large-patch14': 'Large model (768-D, 1700MB)'
    }

    found_models = []

    for model_name, description in models_to_check.items():
        model_path = models_dir / model_name

        print(f"Checking: {model_name}")
        print(f"  Location: {model_path}")

        if not model_path.exists():
            print(f"  ❌ NOT FOUND")
            print()
            continue

        print(f"  ✓ Directory exists")

        # Check for snapshots directory
        snapshots_dir = model_path / 'snapshots'
        if not snapshots_dir.exists():
            print(f"  ⚠️  Missing snapshots/ directory")
            print()
            continue

        print(f"  ✓ snapshots/ exists")

        # Check for snapshot subdirectories
        snapshot_dirs = list(snapshots_dir.iterdir())
        if not snapshot_dirs:
            print(f"  ⚠️  No snapshot directories found")
            print()
            continue

        print(f"  ✓ Found {len(snapshot_dirs)} snapshot(s)")

        # Check files in first snapshot
        snapshot = snapshot_dirs[0]
        required_files = [
            'config.json',
            'pytorch_model.bin',
            'preprocessor_config.json',
            'tokenizer_config.json'
        ]

        missing = []
        for filename in required_files:
            if not (snapshot / filename).exists():
                missing.append(filename)

        if missing:
            print(f"  ⚠️  Missing files: {', '.join(missing)}")
        else:
            print(f"  ✓ All required files present")

            # Get total size
            total_size = sum(f.stat().st_size for f in snapshot.rglob('*') if f.is_file())
            total_mb = total_size / (1024 * 1024)

            print(f"  ✓ Size: {total_mb:.1f} MB")
            print(f"  ✓ {description}")

            found_models.append(model_name)

        print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()

    if found_models:
        print(f"✅ Found {len(found_models)} model(s):")
        for model in found_models:
            desc = models_to_check[model]
            print(f"  ✓ {model}: {desc}")
    else:
        print("❌ No valid models found!")

    print()

    # Recommendations
    print("=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    print()

    if 'clip-vit-large-patch14' in found_models:
        print("✅ Large model is installed correctly!")
        print()
        print("If the app doesn't detect it:")
        print("  1. Make sure you're running the app from THIS directory")
        print(f"     {current_dir}")
        print("  2. Restart the app")
        print("  3. Check logs for model detection")
    elif 'clip-vit-base-patch32' in found_models:
        print("⚠️  Only base model found. Large model not installed.")
        print()
        print("To install large model:")
        print("  1. Make sure you're in the correct directory:")
        print(f"     {current_dir}")
        print("  2. Run: python download_clip_large.py")
        print("  3. Wait for download to complete (~1.7 GB)")
    else:
        print("❌ No models found at all!")
        print()
        print("This is unusual. Check:")
        print("  1. Are you in the correct app directory?")
        print("  2. Run: python download_clip_large.py")

    print()

    # Check for multiple app directories
    print("=" * 70)
    print("CHECKING FOR MULTIPLE APP COPIES")
    print("=" * 70)
    print()

    parent_dir = current_dir.parent
    app_dirs = list(parent_dir.glob('MemoryMate-PhotoFlow-Refactored*'))

    if len(app_dirs) > 1:
        print(f"⚠️  WARNING: Found {len(app_dirs)} app directories:")
        print()

        for app_dir in sorted(app_dirs):
            models_path = app_dir / 'models'
            if models_path.exists():
                model_count = len(list(models_path.iterdir()))
                marker = " ← YOU ARE HERE" if app_dir == current_dir else ""
                print(f"  {app_dir.name}: {model_count} model(s){marker}")
            else:
                print(f"  {app_dir.name}: No models/ directory")

        print()
        print("💡 You have multiple app copies!")
        print("   Models in one directory won't be visible in another.")
        print()
        print("Recommendation:")
        print("  1. Choose ONE directory to use")
        print("  2. Download models to THAT directory only")
        print("  3. Delete or move other copies")
    else:
        print("✓ Only one app directory found (good!)")

    print()


if __name__ == '__main__':
    try:
        check_models()
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
