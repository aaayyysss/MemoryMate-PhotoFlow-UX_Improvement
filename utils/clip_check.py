# clip_check.py
# veriosn 01.00.00.00 dated 20260122

"""
CLIP model availability checker with user-friendly notifications.

Provides clear guidance when CLIP embedding models are not installed.
Supports multiple CLIP variants: base-patch32, base-patch16, large-patch14
"""

import os
import logging
from pathlib import Path
from typing import Tuple, Dict

logger = logging.getLogger(__name__)

# Model variant configurations
MODEL_CONFIGS = {
    'openai/clip-vit-base-patch32': {
        'dir_name': 'openai--clip-vit-base-patch32',  # Updated to match actual directory structure
        'legacy_dir_name': 'clip-vit-base-patch32',  # Legacy fallback for backward compatibility
        'dimension': 512,
        'description': 'Base model, fastest (512-D)',
        'size_mb': 600
    },
    'openai/clip-vit-base-patch16': {
        'dir_name': 'openai--clip-vit-base-patch16',  # Updated to match actual directory structure
        'legacy_dir_name': 'clip-vit-base-patch16',  # Legacy fallback for backward compatibility
        'dimension': 512,
        'description': 'Base model, better quality (512-D)',
        'size_mb': 600
    },
    'openai/clip-vit-large-patch14': {
        'dir_name': 'openai--clip-vit-large-patch14',  # Updated to match actual directory structure
        'legacy_dir_name': 'clip-vit-large-patch14',  # Legacy fallback for backward compatibility
        'dimension': 768,
        'description': 'Large model, best quality (768-D)',
        'size_mb': 1700
    }
}

# Required CLIP model files
REQUIRED_FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "tokenizer.json",
    "special_tokens_map.json"
]

# Model weights file (either format is acceptable)
MODEL_WEIGHTS_FILES = [
    "pytorch_model.bin",  # PyTorch format (older, larger)
    "model.safetensors"   # Safetensors format (newer, faster)
]


def check_clip_availability(variant: str = 'openai/clip-vit-base-patch32') -> Tuple[bool, str]:
    """
    Check if CLIP model files are available for a specific variant.

    Args:
        variant: Model variant (e.g., 'openai/clip-vit-base-patch32')

    Returns:
        Tuple[bool, str]: (available, message)
            - available: True if CLIP model files are ready
            - message: Status message for user display
    """
    if variant not in MODEL_CONFIGS:
        return False, f"❌ Unknown model variant: {variant}"

    config = MODEL_CONFIGS[variant]
    dir_name = config['dir_name']
    legacy_dir_name = config.get('legacy_dir_name', dir_name)

    # Check if models exist in any of the standard locations
    model_locations = _get_model_search_paths()
    models_found = False
    model_path = None

    # Check both new and legacy directory names
    dir_names_to_check = [dir_name]
    if legacy_dir_name != dir_name:
        dir_names_to_check.append(legacy_dir_name)

    # Also search HuggingFace cache directory naming format
    hf_dir_name = f"models--{dir_name.replace('/', '--')}"  # models--openai--clip-vit-...
    hf_legacy_name = f"models--{legacy_dir_name}" if legacy_dir_name != dir_name else None

    for location in model_locations:
        # Build list of candidate directories to check
        candidates = []
        for check_dir_name in dir_names_to_check:
            candidates.append(Path(location) / 'models' / check_dir_name)
        # HuggingFace cache format (models--openai--clip-vit-... at root, no models/ subdir)
        candidates.append(Path(location) / hf_dir_name)
        if hf_legacy_name:
            candidates.append(Path(location) / hf_legacy_name)

        for base_dir in candidates:
            if not base_dir.exists():
                continue

            # Check for snapshots directory
            snapshots_dir = base_dir / 'snapshots'
            if not snapshots_dir.exists():
                continue

            # Look for ANY commit hash directory in snapshots/
            for commit_dir in snapshots_dir.iterdir():
                if commit_dir.is_dir():
                    # Check if this directory has all required files
                    if _verify_model_files(str(commit_dir)):
                        models_found = True
                        model_path = str(commit_dir)
                        break

            if models_found:
                break

        if models_found:
            break

    if models_found:
        message = f"✅ CLIP model detected\n   Location: {model_path}"
        return True, message
    else:
        message = _get_install_message(variant)
        return False, message


def _get_model_search_paths() -> list:
    """
    Get list of paths to search for CLIP models.

    Priority order:
    1. App directory (./models/clip-vit-base-patch32/)
    2. HuggingFace cache (~/.cache/huggingface/hub/)
    3. Custom path from settings (for offline use)
    """
    import sys

    paths = []

    # 1. App directory (primary location)
    try:
        app_root = Path(__file__).parent.parent
        paths.append(str(app_root))
    except Exception:
        pass

    # 2. HuggingFace cache (transformers / huggingface_hub default cache)
    try:
        hf_cache = Path.home() / '.cache' / 'huggingface' / 'hub'
        if hf_cache.exists():
            # HF cache stores models in models--<org>--<name>/ format
            # Map them to a structure compatible with our check
            for model_dir in hf_cache.iterdir():
                if model_dir.is_dir() and model_dir.name.startswith('models--openai--clip-vit-'):
                    # HF cache structure: models--openai--clip-vit-.../snapshots/<hash>/
                    snapshots_dir = model_dir / 'snapshots'
                    if snapshots_dir.exists():
                        # Create a virtual root that maps HF names to our names
                        # e.g. models--openai--clip-vit-large-patch14 → openai--clip-vit-large-patch14
                        paths.append(str(hf_cache))
                        break  # Only need to add the cache root once
    except Exception:
        pass

    # 3. Custom path from settings (optional)
    try:
        from settings_manager_qt import SettingsManager
        settings = SettingsManager()
        custom_path = settings.get_setting('clip_model_path', '')
        if custom_path:
            custom_path = Path(custom_path)
            if custom_path.exists():
                # Check if this is the snapshot directory itself
                if (custom_path / 'pytorch_model.bin').exists():
                    # This is the snapshot dir, use great-grandparent as root
                    paths.append(str(custom_path.parent.parent.parent))
                elif (custom_path / 'models' / 'clip-vit-base-patch32').exists():
                    # This is the app root
                    paths.append(str(custom_path))
                else:
                    # Add it anyway, might have different structure
                    paths.append(str(custom_path))
    except Exception:
        pass

    return paths


def _verify_model_files(snapshot_path: str) -> bool:
    """
    Verify that all essential CLIP model files exist in snapshot directory.

    Args:
        snapshot_path: Path to snapshots/<commit_hash> directory

    Returns:
        True if all essential files are present, False otherwise
    """
    snapshot_path = Path(snapshot_path)

    # Check all required files
    for filename in REQUIRED_FILES:
        file_path = snapshot_path / filename
        if not file_path.exists():
            logger.debug(f"Missing CLIP model file: {filename}")
            return False

    # Check for model weights file (at least one format must exist)
    has_weights = False
    for weights_file in MODEL_WEIGHTS_FILES:
        if (snapshot_path / weights_file).exists():
            has_weights = True
            break

    if not has_weights:
        logger.debug(f"Missing model weights file (need one of: {MODEL_WEIGHTS_FILES})")
        return False

    # refs/main is optional — HuggingFace cache has it, manual installs may not
    return True


def get_clip_download_status(variant: str = 'openai/clip-vit-base-patch32') -> Dict[str, any]:
    """
    Get detailed status of CLIP model installation for a specific variant.

    Args:
        variant: Model variant to check

    Returns:
        Dictionary with:
            - 'models_available': bool
            - 'model_path': str or None
            - 'missing_files': list of missing file names
            - 'total_size_mb': float (approximate)
            - 'message': str
            - 'variant': str
    """
    status = {
        'models_available': False,
        'model_path': None,
        'missing_files': [],
        'total_size_mb': 0.0,
        'message': '',
        'variant': variant
    }

    if variant not in MODEL_CONFIGS:
        status['message'] = f"❌ Unknown variant: {variant}"
        return status

    config = MODEL_CONFIGS[variant]
    dir_name = config['dir_name']
    legacy_dir_name = config.get('legacy_dir_name', dir_name)

    # Check both new and legacy directory names
    dir_names_to_check = [dir_name]
    if legacy_dir_name != dir_name:
        dir_names_to_check.append(legacy_dir_name)

    # Check models
    model_locations = _get_model_search_paths()
    for location in model_locations:
        for check_dir_name in dir_names_to_check:
            base_dir = Path(location) / 'models' / check_dir_name
            if not base_dir.exists():
                continue

            snapshots_dir = base_dir / 'snapshots'
            if not snapshots_dir.exists():
                continue

            # Look for ANY commit hash directory
            for commit_dir in snapshots_dir.iterdir():
                if not commit_dir.is_dir():
                    continue

                # Check which files are missing
                missing = []
                total_size = 0

                for filename in REQUIRED_FILES:
                    file_path = commit_dir / filename
                    if file_path.exists():
                        total_size += file_path.stat().st_size
                    else:
                        missing.append(filename)

                # Check for model weights (at least one format)
                has_weights = False
                for weights_file in MODEL_WEIGHTS_FILES:
                    weights_path = commit_dir / weights_file
                    if weights_path.exists():
                        has_weights = True
                        total_size += weights_path.stat().st_size
                        break

                if not has_weights:
                    missing.append("pytorch_model.bin or model.safetensors")

                if not missing:
                    # All files present
                    status['models_available'] = True
                    status['model_path'] = str(commit_dir)
                    status['total_size_mb'] = round(total_size / (1024 * 1024), 1)
                    status['message'] = f"✅ {config['description']} installed ({status['total_size_mb']} MB)"
                    return status
                elif len(missing) < len(REQUIRED_FILES):
                    # Some files present
                    status['missing_files'] = missing
                    status['model_path'] = str(commit_dir)
                    status['message'] = f"⚠️ Incomplete installation - {len(missing)} files missing"
                    return status

    # No installation found
    status['message'] = f"❌ {config['description']} not installed"
    status['missing_files'] = REQUIRED_FILES.copy()
    return status


def _get_install_message(variant: str = 'openai/clip-vit-base-patch32') -> str:
    """
    Get user-friendly installation message for a specific model variant.

    Args:
        variant: Model variant

    Returns:
        Formatted message with installation instructions
    """
    if variant not in MODEL_CONFIGS:
        return f"❌ Unknown model variant: {variant}"

    config = MODEL_CONFIGS[variant]

    return f"""
═══════════════════════════════════════════════════════════════════
⚠️  CLIP Model Files Not Found
═══════════════════════════════════════════════════════════════════

Visual embedding extraction requires CLIP model files.

⚠️ Impact:
  ✅ Photos can still be viewed and organized
  ✅ Face detection will still work
  ❌ Visual semantic search won't work
  ❌ Embedding extraction will be disabled

📥 Download Models:
  Option 1: Run the download script
    python download_clip_model_offline.py --variant {variant}

  Option 2: Use the application preferences
    1. Go to Preferences (Ctrl+,)
    2. Navigate to "🔍 Visual Embeddings" section
    3. Click "Download CLIP Model"

💡 Model Details:
   - Model: {variant}
   - Description: {config['description']}
   - Size: ~{config['size_mb']}MB
   - Dimension: {config['dimension']}-D embeddings
   - Location: ./models/{config['dir_name']}/
   - Files: {len(REQUIRED_FILES)} files total

After download:
  1. Restart the application (or retry extraction)
  2. Embedding extraction will be automatically enabled
  3. Visual semantic search will be available

═══════════════════════════════════════════════════════════════════
"""


def get_available_variants() -> Dict[str, bool]:
    """
    Check which CLIP model variants are currently installed.

    Returns:
        Dictionary mapping variant names to availability status
    """
    variants_status = {}
    for variant in MODEL_CONFIGS.keys():
        available, _ = check_clip_availability(variant)
        variants_status[variant] = available
    return variants_status


def get_recommended_variant() -> str:
    """
    Get the recommended CLIP variant based on what's installed.

    Priority:
    1. clip-vit-large-patch14 (best quality if available)
    2. clip-vit-base-patch16 (good balance)
    3. clip-vit-base-patch32 (fallback, most common)

    Returns:
        Variant name (e.g., 'openai/clip-vit-large-patch14')
    """
    variants_status = get_available_variants()

    # Priority order: large > base-16 > base-32
    priority_order = [
        'openai/clip-vit-large-patch14',
        'openai/clip-vit-base-patch16',
        'openai/clip-vit-base-patch32'
    ]

    for variant in priority_order:
        if variants_status.get(variant, False):
            return variant

    # No models installed, return default
    return 'openai/clip-vit-base-patch32'


if __name__ == '__main__':
    # Test the checker for all variants
    print("Checking all CLIP variants...\n")

    variants_status = get_available_variants()
    for variant, available in variants_status.items():
        config = MODEL_CONFIGS[variant]
        status = "✅ Installed" if available else "❌ Not installed"
        print(f"{status}: {variant}")
        print(f"  {config['description']}")
        print(f"  Size: {config['size_mb']}MB, Dimension: {config['dimension']}-D")
        print()

    recommended = get_recommended_variant()
    print(f"Recommended variant: {recommended}")
