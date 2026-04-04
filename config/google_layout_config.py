"""
Google Photos Layout Configuration
Centralizes all UI, performance, and display settings for the Google Photos Layout.

This replaces scattered magic numbers and hardcoded values throughout google_layout.py,
improving maintainability and making settings easier to tune.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class ThumbnailConfig:
    """Configuration for thumbnail loading and display."""

    # Loading parameters
    initial_load_limit: int = 50  # Load first N thumbnails immediately (was hardcoded at line 7919)
    initial_render_count: int = 5  # Render first N date groups immediately (was hardcoded at line 7926)

    # Thread pool settings
    max_thread_count: int = 4  # Max concurrent thumbnail loading threads (was hardcoded at line 7913)

    # Size presets (for different zoom levels)
    size_small: int = 120
    size_medium: int = 200
    size_large: int = 300
    size_xlarge: int = 400
    default_size: int = 200  # Default thumbnail size


@dataclass
class CacheConfig:
    """Configuration for image and data caching."""

    # Image cache limits
    cache_limit: int = 5  # Max photos in MediaLightbox cache (was hardcoded at line 674)
    thumbnail_cache_limit: int = 500  # Max cached thumbnail buttons (audit recommendation)

    # Preloading
    preload_enabled: bool = True
    preload_thread_count: int = 2  # Max preloading threads (was hardcoded at line 676)
    preload_cache_size: int = 10  # Number of adjacent photos to preload


@dataclass
class UIConfig:
    """Configuration for UI behavior and interaction."""

    # Zoom settings
    zoom_factor: float = 1.15  # Zoom increment per wheel step (was hardcoded at line 656)
    zoom_min: float = 0.1  # Minimum zoom level
    zoom_max: float = 10.0  # Maximum zoom level

    # Scrolling
    scroll_debounce_ms: int = 150  # Debounce delay for scroll events

    # Date indicator
    date_indicator_hide_delay_ms: int = 2000  # How long to show date indicator

    # Search
    search_debounce_ms: int = 300  # Debounce delay for search input
    search_min_chars: int = 2  # Minimum characters before search activates

    # Autosave
    autosave_enabled: bool = True
    autosave_interval_ms: int = 5000  # Auto-save interval (5 seconds)


@dataclass
class PeopleConfig:
    """Configuration for people/face management."""

    # Undo/Redo
    max_history: int = 20  # Max undo steps for people merges (was hardcoded at line 760)

    # Similarity thresholds
    merge_similarity_threshold: float = 0.75  # Minimum cosine similarity for suggesting merges (was 0.75 at line 10531)
    high_confidence_threshold: float = 0.80  # Threshold for high-confidence suggestions (was 0.80 at line 11423)

    # UI display
    thumbnail_size: int = 128  # Face thumbnail size in people sidebar
    max_suggestions: int = 12  # Max merge suggestions to show (was hardcoded at line 11424)
    suggestion_similarity_percent: int = 80  # Display threshold as percentage (was at line 11477)


@dataclass
class PerformanceConfig:
    """Configuration for performance tuning."""

    # Database timeouts
    db_busy_timeout_ms: int = 5000  # SQLite busy timeout

    # Thread pool cleanup
    thread_pool_wait_timeout_ms: int = 2000  # Max wait time for thread pool cleanup (was hardcoded at line 16129)

    # Batch processing
    batch_commit_size: int = 50  # Commit to database after N operations

    # Lazy loading
    lazy_load_enabled: bool = True
    viewport_buffer_ratio: float = 1.5  # Load images within viewport + 50% buffer


@dataclass
class EditingConfig:
    """Configuration for photo editing features."""

    # Adjustment ranges
    exposure_min: float = -2.0  # Minimum exposure adjustment
    exposure_max: float = 2.0  # Maximum exposure adjustment
    exposure_step: float = 0.1  # Exposure slider step

    contrast_min: float = -100.0
    contrast_max: float = 100.0

    brightness_min: float = -100.0
    brightness_max: float = 100.0

    saturation_min: float = -100.0
    saturation_max: float = 100.0

    # Histogram
    histogram_clip_threshold: float = 0.05  # Clipping detection threshold (was 0.05 at line 3181)

    # Quality settings
    export_quality_high: int = 95
    export_quality_medium: int = 85
    export_quality_low: int = 75


class GoogleLayoutConfig:
    """Main configuration manager for Google Photos Layout."""

    DEFAULT_CONFIG = {
        "thumbnail": ThumbnailConfig(),
        "cache": CacheConfig(),
        "ui": UIConfig(),
        "people": PeopleConfig(),
        "performance": PerformanceConfig(),
        "editing": EditingConfig(),
    }

    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration.

        Args:
            config_path: Path to configuration file. If None, uses default location.
        """
        if config_path is None:
            config_dir = Path.home() / ".memorymate"
            config_dir.mkdir(exist_ok=True)
            config_path = config_dir / "google_layout_config.json"

        self.config_path = Path(config_path)

        # Initialize with defaults
        self.thumbnail = ThumbnailConfig()
        self.cache = CacheConfig()
        self.ui = UIConfig()
        self.people = PeopleConfig()
        self.performance = PerformanceConfig()
        self.editing = EditingConfig()

        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)

                # Update dataclass instances from loaded data
                if "thumbnail" in data:
                    self.thumbnail = ThumbnailConfig(**data["thumbnail"])
                if "cache" in data:
                    self.cache = CacheConfig(**data["cache"])
                if "ui" in data:
                    self.ui = UIConfig(**data["ui"])
                if "people" in data:
                    self.people = PeopleConfig(**data["people"])
                if "performance" in data:
                    self.performance = PerformanceConfig(**data["performance"])
                if "editing" in data:
                    self.editing = EditingConfig(**data["editing"])

                print(f"[GoogleLayoutConfig] Loaded from {self.config_path}")
            except Exception as e:
                print(f"[GoogleLayoutConfig] Failed to load config: {e}, using defaults")

    def save(self) -> None:
        """Save configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "thumbnail": asdict(self.thumbnail),
                "cache": asdict(self.cache),
                "ui": asdict(self.ui),
                "people": asdict(self.people),
                "performance": asdict(self.performance),
                "editing": asdict(self.editing),
            }

            with open(self.config_path, 'w') as f:
                json.dump(data, f, indent=2)

            print(f"[GoogleLayoutConfig] Saved to {self.config_path}")
        except Exception as e:
            print(f"[GoogleLayoutConfig] Failed to save config: {e}")

    def reset_to_defaults(self) -> None:
        """Reset all configuration to defaults."""
        self.thumbnail = ThumbnailConfig()
        self.cache = CacheConfig()
        self.ui = UIConfig()
        self.people = PeopleConfig()
        self.performance = PerformanceConfig()
        self.editing = EditingConfig()
        self.save()

    def update_thumbnail_config(self, **kwargs) -> None:
        """Update thumbnail configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.thumbnail, key):
                setattr(self.thumbnail, key, value)
        self.save()

    def update_cache_config(self, **kwargs) -> None:
        """Update cache configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.cache, key):
                setattr(self.cache, key, value)
        self.save()

    def update_ui_config(self, **kwargs) -> None:
        """Update UI configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.ui, key):
                setattr(self.ui, key, value)
        self.save()

    def update_people_config(self, **kwargs) -> None:
        """Update people configuration parameters."""
        for key, value in kwargs.items():
            if hasattr(self.people, key):
                setattr(self.people, key, value)
        self.save()


# Global configuration instance
_config: Optional[GoogleLayoutConfig] = None


def get_google_layout_config() -> GoogleLayoutConfig:
    """Get global Google Photos Layout configuration instance."""
    global _config
    if _config is None:
        _config = GoogleLayoutConfig()
    return _config


def reload_config() -> None:
    """Reload configuration from disk."""
    global _config
    _config = GoogleLayoutConfig()
