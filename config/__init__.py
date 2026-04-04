"""
Configuration Module
Centralized configuration management for MemoryMate Photo Flow.

Usage:
    from config import (
        get_face_config,
        get_google_layout_config,
        get_embedding_config
    )

    # Get configuration instances
    face_config = get_face_config()
    layout_config = get_google_layout_config()
    embedding_config = get_embedding_config()

    # Access settings
    eps = face_config.get_clustering_params()['eps']
    zoom = layout_config.ui.zoom_factor
    variant = embedding_config.clip_model.preferred_variant
"""

from config.face_detection_config import (
    FaceDetectionConfig,
    get_face_config,
    reload_config as reload_face_config
)

from config.google_layout_config import (
    GoogleLayoutConfig,
    ThumbnailConfig,
    CacheConfig,
    UIConfig,
    PeopleConfig,
    PerformanceConfig,
    EditingConfig,
    get_google_layout_config,
    reload_config as reload_google_layout_config
)

from config.embedding_config import (
    EmbeddingConfig,
    CLIPModelConfig,
    EmbeddingExtractionConfig,
    SemanticSearchConfig,
    DimensionHandlingConfig,
    get_embedding_config,
    reload_config as reload_embedding_config
)

__all__ = [
    # Face Detection
    'FaceDetectionConfig',
    'get_face_config',
    'reload_face_config',

    # Google Photos Layout
    'GoogleLayoutConfig',
    'ThumbnailConfig',
    'CacheConfig',
    'UIConfig',
    'PeopleConfig',
    'PerformanceConfig',
    'EditingConfig',
    'get_google_layout_config',
    'reload_google_layout_config',

    # Embedding/CLIP
    'EmbeddingConfig',
    'CLIPModelConfig',
    'EmbeddingExtractionConfig',
    'SemanticSearchConfig',
    'DimensionHandlingConfig',
    'get_embedding_config',
    'reload_embedding_config',
]


def reload_all_configs():
    """Reload all configuration files from disk."""
    reload_face_config()
    reload_google_layout_config()
    reload_embedding_config()
    print("[Config] All configurations reloaded from disk")
