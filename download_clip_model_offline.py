r"""
Offline CLIP Model Downloader
===============================

This script downloads the CLIP model with SSL verification disabled,
for use in portable Python environments without proper SSL certificates.

Usage:
    python download_clip_model_offline.py

The model will be downloaded to the HuggingFace cache directory:
Windows: C:/Users/<username>/.cache/huggingface/hub/models--openai--clip-vit-base-patch32/
Linux:   ~/.cache/huggingface/hub/models--openai--clip-vit-base-patch32/
"""

import os
import sys
import urllib.request
import ssl
import json
from pathlib import Path

# Disable SSL verification
ssl._create_default_https_context = ssl._create_unverified_context

# Model files to download
MODEL_NAME = "openai/clip-vit-base-patch32"
BASE_URL = "https://huggingface.co/openai/clip-vit-base-patch32/resolve/main/"

# Known commit hash for this model (from HuggingFace)
# This is the latest stable version as of 2026-01-01
COMMIT_HASH = "e6a30b603a447e251fdaca1c3056b2a16cdfebeb"

FILES_TO_DOWNLOAD = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "tokenizer.json",
    "special_tokens_map.json",
    "pytorch_model.bin"  # Large file ~600MB
]

def get_cache_dir():
    """Get the CLIP model directory in app root (next to face detection models)."""
    # Get app root directory (where this script is located)
    app_root = Path(__file__).parent.absolute()

    # Store CLIP model next to face detection models
    # Structure: ./models/clip-vit-base-patch32/
    model_base_dir = app_root / 'models' / 'clip-vit-base-patch32'

    # For HuggingFace compatibility, create proper structure
    snapshot_dir = model_base_dir / 'snapshots' / COMMIT_HASH
    refs_dir = model_base_dir / 'refs'

    return {
        'model_dir': model_base_dir,
        'snapshot_dir': snapshot_dir,
        'refs_dir': refs_dir,
        'app_root': app_root
    }

def download_file(url, dest_path, filename):
    """Download a file with SSL verification disabled."""
    print(f"\n📥 Downloading: {filename}")
    print(f"   URL: {url}")
    print(f"   Destination: {dest_path}")

    try:
        # Download with progress
        def progress_callback(block_num, block_size, total_size):
            if total_size > 0:
                downloaded = block_num * block_size
                percent = min(100, (downloaded * 100) // total_size)
                mb_downloaded = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                print(f"\r   Progress: {percent}% ({mb_downloaded:.1f}MB / {mb_total:.1f}MB)", end='')

        urllib.request.urlretrieve(url, dest_path, reporthook=progress_callback)
        print()  # New line after progress
        print(f"   ✅ Downloaded successfully!")
        return True

    except Exception as e:
        print(f"\n   ❌ Error downloading {filename}: {e}")
        return False

def main():
    """Main download function."""
    print("=" * 70)
    print("CLIP Model Offline Downloader (SSL Verification Disabled)")
    print("=" * 70)

    # Get cache directories
    cache_dirs = get_cache_dir()
    snapshot_dir = cache_dirs['snapshot_dir']
    refs_dir = cache_dirs['refs_dir']
    model_dir = cache_dirs['model_dir']

    print(f"\n📁 Model directory: {model_dir}")
    print(f"📁 Snapshot directory: {snapshot_dir}")
    print(f"📁 Refs directory: {refs_dir}")

    # Create directories
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)
    print(f"✅ Cache directories created/verified")

    # Create refs/main file pointing to commit hash
    refs_main_file = refs_dir / 'main'
    if not refs_main_file.exists():
        print(f"\n📝 Creating refs/main file with commit hash: {COMMIT_HASH}")
        refs_main_file.write_text(COMMIT_HASH)
        print("✅ refs/main created")
    else:
        print(f"\n⏭️  refs/main already exists")

    # Download each file
    print(f"\n📦 Downloading {len(FILES_TO_DOWNLOAD)} files to snapshot directory...")

    success_count = 0
    failed_files = []

    for filename in FILES_TO_DOWNLOAD:
        url = BASE_URL + filename
        dest_path = snapshot_dir / filename

        # Skip if already exists
        if dest_path.exists():
            file_size_mb = dest_path.stat().st_size / (1024 * 1024)
            print(f"\n⏭️  Skipping {filename} (already exists, {file_size_mb:.1f}MB)")
            success_count += 1
            continue

        # Download
        if download_file(url, dest_path, filename):
            success_count += 1
        else:
            failed_files.append(filename)

    # Summary
    print("\n" + "=" * 70)
    print("DOWNLOAD SUMMARY")
    print("=" * 70)
    print(f"✅ Successfully downloaded: {success_count}/{len(FILES_TO_DOWNLOAD)} files")

    if failed_files:
        print(f"❌ Failed to download: {len(failed_files)} files")
        for f in failed_files:
            print(f"   - {f}")
        print("\n⚠️  Please try running this script again or download manually from:")
        print(f"   https://huggingface.co/{MODEL_NAME}/tree/main")
        return 1
    else:
        print("\n🎉 All files downloaded successfully!")
        print("\n📝 Next steps:")
        print("   1. Close and restart your application")
        print("   2. Try the embedding extraction feature again")
        print("   3. The model should now load from the local cache")
        return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n❌ Download cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
