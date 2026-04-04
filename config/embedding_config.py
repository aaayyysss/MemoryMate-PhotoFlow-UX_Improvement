"""
Embedding Configuration
Manages settings for visual semantic embeddings (CLIP models).

This centralizes CLIP model selection, embedding extraction parameters,
and semantic search settings.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict


@dataclass
class CLIPModelConfig:
    """Configuration for CLIP model selection and loading."""

    # Model selection (auto-select best available if None)
    preferred_variant: Optional[str] = None  # e.g., 'openai/clip-vit-large-patch14'

    # Available variants (in priority order for auto-selection)
    variant_priority: List[str] = None  # Set in __post_init__

    # Device selection
    device: str = 'auto'  # Options: 'auto', 'cpu', 'cuda', 'mps'

    # Model metadata
    model_metadata: Dict[str, Dict[str, Any]] = None  # Set in __post_init__

    def __post_init__(self):
        """Initialize default lists and dicts."""
        if self.variant_priority is None:
            self.variant_priority = [
                'openai/clip-vit-large-patch14',  # 768-D, best quality
                'openai/clip-vit-base-patch16',   # 512-D, good quality
                'openai/clip-vit-base-patch32',   # 512-D, fastest
            ]

        if self.model_metadata is None:
            self.model_metadata = {
                'openai/clip-vit-large-patch14': {
                    'dimension': 768,
                    'quality': 'best',
                    'speed': 'slow',
                    'size_mb': 1700,
                },
                'openai/clip-vit-base-patch16': {
                    'dimension': 512,
                    'quality': 'good',
                    'speed': 'medium',
                    'size_mb': 600,
                },
                'openai/clip-vit-base-patch32': {
                    'dimension': 512,
                    'quality': 'fair',
                    'speed': 'fast',
                    'size_mb': 350,
                },
            }


@dataclass
class EmbeddingExtractionConfig:
    """Configuration for embedding extraction process."""

    # Batch processing
    batch_size: int = 32  # Number of images to process in one batch
    max_workers: int = 4  # Max parallel embedding extraction workers

    # Image preprocessing
    image_size: int = 224  # Input image size for CLIP (standard: 224x224)
    normalize: bool = True  # Normalize embeddings to unit vectors

    # Storage
    store_in_db: bool = True  # Store embeddings in database
    compression: Optional[str] = None  # Compression method ('none', 'fp16', None)

    # Performance
    skip_existing: bool = True  # Skip images that already have embeddings
    cache_model: bool = True  # Keep model in memory between batches


@dataclass
class SemanticSearchConfig:
    """Configuration for semantic search functionality."""

    # Search parameters
    min_similarity: float = 0.20  # Minimum cosine similarity for search results (0.0-1.0)
    default_top_k: int = 50  # Default number of results to return

    # Thresholds for quality tiers
    excellent_threshold: float = 0.40  # Similarity ≥ 0.40 = excellent match
    good_threshold: float = 0.30  # Similarity ≥ 0.30 = good match
    fair_threshold: float = 0.20  # Similarity ≥ 0.20 = fair match

    # Result filtering
    deduplicate_results: bool = True  # Remove duplicate results
    group_by_date: bool = False  # Group results by date taken

    # Performance
    max_results: int = 1000  # Hard limit on number of results
    timeout_seconds: int = 30  # Search timeout


@dataclass
class DimensionHandlingConfig:
    """Configuration for handling dimension mismatches."""

    # Dimension mismatch handling
    skip_mismatched: bool = True  # Skip embeddings with different dimensions
    warn_threshold: float = 0.10  # Warn if >10% of embeddings are skipped

    # Migration/re-extraction
    auto_detect_model_change: bool = True  # Detect when CLIP model changed
    suggest_re_extraction: bool = True  # Suggest re-extracting when model changes

    # Validation
    validate_dimensions: bool = True  # Validate embedding dimensions on load
    strict_mode: bool = False  # Fail on any dimension mismatch


class EmbeddingConfig:
    """Main configuration manager for embedding extraction and search."""

    DEFAULT_CONFIG = {
        "clip_model": CLIPModelConfig(),
        "extraction": EmbeddingExtractionConfig(),
        "search": SemanticSearchConfig(),
        "dimension_handling": DimensionHandlingConfig(),
    }

    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration.

        Args:
            config_path: Path to configuration file. If None, uses default location.
        """
        if config_path is None:
            config_dir = Path.home() / ".memorymate"
            config_dir.mkdir(exist_ok=True)
            config_path = config_dir / "embedding_config.json"

        self.config_path = Path(config_path)

        # Initialize with defaults
        self.clip_model = CLIPModelConfig()
        self.extraction = EmbeddingExtractionConfig()
        self.search = SemanticSearchConfig()
        self.dimension_handling = DimensionHandlingConfig()

        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)

                # Update dataclass instances from loaded data
                if "clip_model" in data:
                    # Handle variant_priority and model_metadata specially
                    clip_data = data["clip_model"]
                    self.clip_model = CLIPModelConfig(
                        preferred_variant=clip_data.get("preferred_variant"),
                        variant_priority=clip_data.get("variant_priority"),
                        device=clip_data.get("device", "auto"),
                        model_metadata=clip_data.get("model_metadata")
                    )

                if "extraction" in data:
                    self.extraction = EmbeddingExtractionConfig(**data["extraction"])

                if "search" in data:
                    self.search = SemanticSearchConfig(**data["search"])

                if "dimension_handling" in data:
                    self.dimension_handling = DimensionHandlingConfig(**data["dimension_handling"])

                print(f"[EmbeddingConfig] Loaded from {self.config_path}")
            except Exception as e:
                print(f"[EmbeddingConfig] Failed to load config: {e}, using defaults")

    def save(self) -> None:
        """Save configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "clip_model": asdict(self.clip_model),
                "extraction": asdict(self.extraction),
                "search": asdict(self.search),
                "dimension_handling": asdict(self.dimension_handling),
            }

            with open(self.config_path, 'w') as f:
                json.dump(data, f, indent=2)

            print(f"[EmbeddingConfig] Saved to {self.config_path}")
        except Exception as e:
            print(f"[EmbeddingConfig] Failed to save config: {e}")

    def reset_to_defaults(self) -> None:
        """Reset all configuration to defaults."""
        self.clip_model = CLIPModelConfig()
        self.extraction = EmbeddingExtractionConfig()
        self.search = SemanticSearchConfig()
        self.dimension_handling = DimensionHandlingConfig()
        self.save()

    def set_preferred_clip_variant(self, variant: str) -> None:
        """Set preferred CLIP model variant.

        Args:
            variant: Model variant name (e.g., 'openai/clip-vit-large-patch14')
        """
        self.clip_model.preferred_variant = variant
        self.save()

    def get_preferred_clip_variant(self) -> Optional[str]:
        """Get preferred CLIP model variant."""
        return self.clip_model.preferred_variant

    def update_search_thresholds(self,
                                 min_similarity: Optional[float] = None,
                                 excellent: Optional[float] = None,
                                 good: Optional[float] = None,
                                 fair: Optional[float] = None) -> None:
        """Update semantic search similarity thresholds.

        Args:
            min_similarity: Minimum similarity for any result
            excellent: Threshold for excellent matches
            good: Threshold for good matches
            fair: Threshold for fair matches
        """
        if min_similarity is not None:
            self.search.min_similarity = min_similarity
        if excellent is not None:
            self.search.excellent_threshold = excellent
        if good is not None:
            self.search.good_threshold = good
        if fair is not None:
            self.search.fair_threshold = fair

        self.save()

    def get_model_info(self, variant: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific model variant."""
        return self.clip_model.model_metadata.get(variant)

    def get_expected_dimension(self, variant: Optional[str] = None) -> int:
        """Get expected embedding dimension for a model variant.

        Args:
            variant: Model variant name. If None, uses preferred variant.

        Returns:
            Expected dimension (512 or 768)
        """
        if variant is None:
            variant = self.clip_model.preferred_variant

        if variant is None:
            # Use highest priority variant
            variant = self.clip_model.variant_priority[0]

        info = self.get_model_info(variant)
        if info:
            return info.get('dimension', 512)

        # Default fallback
        if 'large' in variant.lower():
            return 768
        return 512


# Global configuration instance
_config: Optional[EmbeddingConfig] = None


def get_embedding_config() -> EmbeddingConfig:
    """Get global embedding configuration instance."""
    global _config
    if _config is None:
        _config = EmbeddingConfig()
    return _config


def reload_config() -> None:
    """Reload configuration from disk."""
    global _config
    _config = EmbeddingConfig()
