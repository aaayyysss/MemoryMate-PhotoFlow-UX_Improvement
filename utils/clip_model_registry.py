"""
Centralized CLIP model registry.

Single source of truth for model identifiers.
Canonical IDs are full HuggingFace names (e.g. 'openai/clip-vit-base-patch32').
Short aliases (e.g. 'clip-vit-b32') are accepted as input but never stored.
"""

# Canonical HuggingFace IDs (these are stored in DB and settings)
CLIP_VIT_B32 = "openai/clip-vit-base-patch32"
CLIP_VIT_B16 = "openai/clip-vit-base-patch16"
CLIP_VIT_L14 = "openai/clip-vit-large-patch14"

# Default model for new projects and fallback
DEFAULT_MODEL = CLIP_VIT_B32

# Short alias → canonical HF ID (for backward compatibility with old DB values)
_ALIAS_TO_CANONICAL = {
    "clip-vit-b32": CLIP_VIT_B32,
    "clip-vit-b16": CLIP_VIT_B16,
    "clip-vit-l14": CLIP_VIT_L14,
}

# Canonical → short label (for UI display only)
_CANONICAL_TO_LABEL = {
    CLIP_VIT_B32: "CLIP ViT-B/32",
    CLIP_VIT_B16: "CLIP ViT-B/16",
    CLIP_VIT_L14: "CLIP ViT-L/14",
}


def normalize_model_id(name: str) -> str:
    """Normalize any model name (short alias or full) to canonical HF ID.

    >>> normalize_model_id("clip-vit-b32")
    'openai/clip-vit-base-patch32'
    >>> normalize_model_id("openai/clip-vit-base-patch32")
    'openai/clip-vit-base-patch32'
    """
    if not name:
        return DEFAULT_MODEL
    return _ALIAS_TO_CANONICAL.get(name, name)


def model_display_label(canonical_id: str) -> str:
    """Get a human-readable label for a canonical model ID."""
    return _CANONICAL_TO_LABEL.get(canonical_id, canonical_id)


def all_aliases_for(canonical_id: str) -> list[str]:
    """Return all known names (canonical + short aliases) for DB queries."""
    names = [canonical_id]
    for alias, cid in _ALIAS_TO_CANONICAL.items():
        if cid == canonical_id:
            names.append(alias)
    return names
