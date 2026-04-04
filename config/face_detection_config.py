# face_detection_config.py
# Version 10.01.01.03 dated 20260115

"""
Face Detection Configuration
Manages settings for face detection, recognition, and clustering.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


class FaceDetectionConfig:
    """Configuration for face detection and recognition."""

    # Adaptive clustering parameters based on dataset size
    # Optimized through empirical testing with InsightFace buffalo_l embeddings
    ADAPTIVE_CLUSTERING_PARAMS = {
        # Tiny datasets: Very few faces, need looser clustering to avoid over-fragmentation
        "tiny": {
            "max_faces": 50,
            "eps": 0.42,
            "min_samples": 2,
            "rationale": "Tiny dataset: Looser clustering prevents over-fragmentation (< 50 faces)"
        },
        # Small datasets: Few faces, slightly looser than default
        "small": {
            "max_faces": 200,
            "eps": 0.38,
            "min_samples": 2,
            "rationale": "Small dataset: Slightly looser clustering (50-200 faces)"
        },
        # Medium datasets: Typical photo collection, balanced approach
        "medium": {
            "max_faces": 1000,
            "eps": 0.35,
            "min_samples": 2,
            "rationale": "Medium dataset: Balanced clustering (200-1000 faces, current default)"
        },
        # Large datasets: Many faces, need stricter clustering for precision
        "large": {
            "max_faces": 5000,
            "eps": 0.32,
            "min_samples": 3,
            "rationale": "Large dataset: Stricter clustering prevents false merges (1000-5000 faces)"
        },
        # Extra large datasets: Very many faces, very strict to maintain precision
        "xlarge": {
            "max_faces": float('inf'),
            "eps": 0.30,
            "min_samples": 3,
            "rationale": "XLarge dataset: Very strict clustering for high precision (> 5000 faces)"
        }
    }

    # Validation rules for configuration parameters
    VALIDATION_RULES = {
        # Clustering parameters (most critical)
        "clustering_eps": {
            "min": 0.20,
            "max": 0.50,
            "type": float,
            "description": "DBSCAN epsilon (cosine distance threshold)"
        },
        "clustering_min_samples": {
            "min": 1,
            "max": 10,
            "type": int,
            "description": "Minimum faces to form a cluster"
        },

        # Detection parameters
        "confidence_threshold": {
            "min": 0.0,
            "max": 1.0,
            "type": float,
            "description": "Minimum confidence for face detection"
        },
        "min_face_size": {
            "min": 10,
            "max": 200,
            "type": int,
            "description": "Minimum face size in pixels"
        },
        "min_quality_score": {
            "min": 0.0,
            "max": 100.0,
            "type": float,
            "description": "Minimum quality score for face filtering (0-100, 0=disabled)"
        },
        "upsample_times": {
            "min": 0,
            "max": 3,
            "type": int,
            "description": "Number of times to upsample image"
        },

        # Performance parameters
        "batch_size": {
            "min": 1,
            "max": 500,
            "type": int,
            "description": "Batch size for database commits"
        },
        "max_workers": {
            "min": 1,
            "max": 16,
            "type": int,
            "description": "Maximum parallel workers"
        },
        "gpu_batch_size": {
            "min": 1,
            "max": 16,
            "type": int,
            "description": "GPU batch size for parallel image processing"
        },
        "gpu_batch_min_photos": {
            "min": 1,
            "max": 100,
            "type": int,
            "description": "Minimum photos required to enable GPU batch processing"
        },
        "ui_yield_ms": {
            "min": 0,
            "max": 100,
            "type": int,
            "description": "Milliseconds to yield between photos for UI responsiveness (0=disabled)"
        },
        "process_workers": {
            "min": 1,
            "max": 8,
            "type": int,
            "description": "Number of workers in multiprocessing mode"
        },

        # Storage parameters
        "crop_size": {
            "min": 64,
            "max": 512,
            "type": int,
            "description": "Face crop size in pixels"
        },
        "crop_quality": {
            "min": 1,
            "max": 100,
            "type": int,
            "description": "JPEG quality for face crops"
        },
        "thumbnail_size": {
            "min": 32,
            "max": 256,
            "type": int,
            "description": "Thumbnail size for UI"
        },
    }

    DEFAULT_CONFIG = {
        # Backend selection
        "backend": "insightface",  # Options: "insightface" (recommended, uses buffalo_l + OnnxRuntime)
        "enabled": False,  # Face detection disabled by default

        # Detection parameters
        "detection_model": "hog",  # face_recognition: "hog" (fast) or "cnn" (accurate)
        "upsample_times": 1,  # Number of times to upsample image for detection
        "min_face_size": 20,  # Minimum face size in pixels (smaller = detect smaller/distant faces)
        "confidence_threshold": 0.65,  # Minimum confidence for face detection (0.6-0.7 recommended)
                                        # Higher = fewer false positives, fewer missed faces
                                        # Lower = more faces detected, more false positives
                                        # Default 0.65 balances accuracy and recall

        # Quality Filtering (ENHANCEMENT 2026-01-07)
        "min_quality_score": 0.0,  # Minimum quality score for faces (0-100 scale, 0 = disabled)
                                   # 0 = All faces (default, backward compatible)
                                   # 40 = Fair quality and above (filters very blurry/poor faces)
                                   # 60 = Good quality and above (recommended for cleaner clusters)
                                   # 80 = Excellent quality only (very strict, may miss valid faces)
                                   # Quality based on: blur, lighting, size, aspect ratio, confidence
                                   # Expected reduction: 20-30% of faces at threshold 60

        # InsightFace specific
        "insightface_model": "buffalo_l",  # Model: buffalo_s, buffalo_l, antelopev2
        "insightface_det_size": (640, 640),  # Detection size for InsightFace

        # Clustering parameters
        "clustering_enabled": True,
        "clustering_eps": 0.35,  # DBSCAN epsilon (distance threshold)
                                  # Lower = stricter grouping (more clusters, better separation)
                                  # Higher = looser grouping (fewer clusters, may group different people)
                                  # Optimal for InsightFace: 0.30-0.35 (cosine distance)
                                  # Previous: 0.42 (too loose, grouped different people together)
        "clustering_min_samples": 2,  # Minimum faces to form a cluster
                                       # Allows people with 2+ photos to form a cluster
                                       # Single-photo outliers will be marked as noise
                                       # Previous: 3 (too high, missed people with only 2 photos)
        "auto_cluster_after_scan": True,

        # Performance
        "batch_size": 50,  # Number of images to process before committing to DB
        "max_workers": 4,  # Max parallel face detection workers
        "skip_detected": True,  # Skip images that already have faces detected

        # Execution Mode (best-practice non-blocking UI)
        "execution_mode": "thread",  # "thread" (default) or "process"
                                      # thread: Uses QThreadPool, simpler, good for most cases
                                      # process: Uses ProcessPoolExecutor, better CPU utilization
                                      # Start with "thread", switch to "process" if UI still janky
        "process_workers": 2,  # Number of workers in multiprocessing mode
        "ui_yield_ms": 1,  # Milliseconds to yield between photos for UI responsiveness
                           # 0 = disabled (max speed, may cause UI jank)
                           # 1-5 = recommended for smooth UI while scanning
                           # Higher values = slower but smoother UI

        # GPU Batch Processing (ENHANCEMENT 2026-01-07)
        "enable_gpu_batch": True,  # Enable GPU batch processing when GPU is available
        "gpu_batch_size": 4,  # Number of images to process in single GPU call (2-8 recommended)
                              # Higher = better GPU utilization but more VRAM usage
                              # 4 is optimal for most consumer GPUs (6-8GB VRAM)
        "gpu_batch_min_photos": 10,  # Minimum photos to enable batch processing
                                      # Batch overhead not worth it for < 10 photos

        # Storage
        "save_face_crops": True,
        "crop_size": 160,  # Face crop size in pixels
        "crop_quality": 95,  # JPEG quality for face crops
        "face_cache_dir": ".face_cache",  # Directory for face crops

        # UI preferences
        "show_face_boxes": True,
        "show_confidence": False,
        "default_view": "grid",  # "grid" or "list"
        "thumbnail_size": 128,

        # Privacy
        "anonymize_untagged": False,
        "require_confirmation": True,  # Confirm before starting face detection
        "show_low_confidence": False,
        "project_overrides": {}
    }

    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration.

        Args:
            config_path: Path to configuration file. If None, uses default location.
        """
        if config_path is None:
            # Store config in app root directory (where main.py is located)
            # This keeps all project-related files together instead of in user home
            config_dir = Path(__file__).parent.parent / "config_data"
            config_dir.mkdir(exist_ok=True)
            config_path = config_dir / "face_detection_config.json"

        self.config_path = Path(config_path)
        self.config = self.DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                    self.config.update(loaded)
                print(f"[FaceConfig] Loaded from {self.config_path}")
            except Exception as e:
                print(f"[FaceConfig] Failed to load config: {e}")
                # Keep defaults

    def save(self) -> None:
        """Save configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
            print(f"[FaceConfig] Saved to {self.config_path}")
        except Exception as e:
            print(f"[FaceConfig] Failed to save config: {e}")

    def validate_value(self, key: str, value: Any) -> tuple[bool, str]:
        """
        Validate a configuration value against validation rules.

        Args:
            key: Configuration key
            value: Value to validate

        Returns:
            (is_valid, error_message): True if valid, False with error message if invalid
        """
        if key not in self.VALIDATION_RULES:
            return True, ""  # No validation rule for this key

        rule = self.VALIDATION_RULES[key]
        expected_type = rule["type"]
        description = rule.get("description", key)

        # Type validation
        if not isinstance(value, expected_type):
            return False, (
                f"{key} must be {expected_type.__name__}, got {type(value).__name__}. "
                f"({description})"
            )

        # Range validation for numeric types
        if expected_type in (int, float):
            if "min" in rule and value < rule["min"]:
                return False, (
                    f"{key} must be >= {rule['min']}, got {value}. "
                    f"({description})"
                )
            if "max" in rule and value > rule["max"]:
                return False, (
                    f"{key} must be <= {rule['max']}, got {value}. "
                    f"({description})"
                )

        return True, ""

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value
        """
        return self.config.get(key, default)

    def set(self, key: str, value: Any, save_now: bool = True) -> None:
        """Set configuration value with validation.

        Args:
            key: Configuration key
            value: Value to set
            save_now: Whether to save immediately (default: True)

        Raises:
            ValueError: If value fails validation
        """
        # Validate value if validation rules exist
        is_valid, error_msg = self.validate_value(key, value)
        if not is_valid:
            raise ValueError(f"Invalid configuration: {error_msg}")

        self.config[key] = value
        if save_now:
            self.save()

    def is_enabled(self) -> bool:
        """Check if face detection is enabled."""
        return self.config.get("enabled", False)

    def get_backend(self) -> str:
        """Get selected backend."""
        return self.config.get("backend", "insightface")

    def get_clustering_params(self, project_id: Optional[int] = None) -> Dict[str, Any]:
        """Get clustering parameters, honoring per-project overrides if provided."""
        if project_id is not None:
            po = self.config.get("project_overrides", {})
            ov = po.get(str(project_id), None)
            if ov:
                return {
                    "eps": ov.get("clustering_eps", self.config.get("clustering_eps", 0.35)),
                    "min_samples": ov.get("clustering_min_samples", self.config.get("clustering_min_samples", 2)),
                }
        return {
            "eps": self.config.get("clustering_eps", 0.35),
            "min_samples": self.config.get("clustering_min_samples", 2),
        }

    def get_optimal_clustering_params(self, face_count: int, project_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get optimal clustering parameters based on dataset size.

        Uses adaptive parameter selection to choose appropriate eps and min_samples
        based on the number of faces in the dataset. Larger datasets benefit from
        stricter clustering to prevent false merges, while smaller datasets need
        looser clustering to avoid over-fragmentation.

        Args:
            face_count: Total number of faces in dataset
            project_id: Optional project ID for manual overrides

        Returns:
            dict with keys:
                - eps: DBSCAN epsilon parameter
                - min_samples: DBSCAN minimum samples parameter
                - rationale: Explanation of parameter selection
                - category: Dataset size category (tiny/small/medium/large/xlarge)

        Example:
            >>> config = FaceDetectionConfig()
            >>> params = config.get_optimal_clustering_params(150)
            >>> print(params)
            {
                'eps': 0.38,
                'min_samples': 2,
                'rationale': 'Small dataset: Slightly looser clustering (50-200 faces)',
                'category': 'small'
            }
        """
        # Check for manual project overrides first (highest priority)
        if project_id is not None:
            overrides = self.config.get("project_overrides", {}).get(str(project_id))
            if overrides and "clustering_eps" in overrides:
                return {
                    "eps": overrides["clustering_eps"],
                    "min_samples": overrides.get("clustering_min_samples", 2),
                    "rationale": f"Manual project override for project {project_id}",
                    "category": "manual_override"
                }

        # Find appropriate size category based on face count
        for category, params in self.ADAPTIVE_CLUSTERING_PARAMS.items():
            if face_count <= params["max_faces"]:
                return {
                    "eps": params["eps"],
                    "min_samples": params["min_samples"],
                    "rationale": params["rationale"],
                    "category": category
                }

        # Fallback (shouldn't reach here due to xlarge having inf max_faces)
        return {
            "eps": 0.35,
            "min_samples": 2,
            "rationale": "Default fallback (medium dataset)",
            "category": "fallback"
        }

    def set_project_overrides(self, project_id: int, overrides: Dict[str, Any]) -> None:
        """Set per-project overrides for detection/clustering thresholds.

        Args:
            project_id: Project ID
            overrides: Dictionary of parameter overrides

        Raises:
            ValueError: If any override value fails validation
        """
        # Prepare override values with type conversion
        override_values = {
            "min_face_size": int(overrides.get("min_face_size", self.config.get("min_face_size", 20))),
            "confidence_threshold": float(overrides.get("confidence_threshold", self.config.get("confidence_threshold", 0.65))),
            "clustering_eps": float(overrides.get("clustering_eps", self.config.get("clustering_eps", 0.35))),
            "clustering_min_samples": int(overrides.get("clustering_min_samples", self.config.get("clustering_min_samples", 2))),
        }

        # Validate each override value
        for key, value in override_values.items():
            is_valid, error_msg = self.validate_value(key, value)
            if not is_valid:
                raise ValueError(f"Invalid project override: {error_msg}")

        # All valid, save overrides
        po = self.config.get("project_overrides", {})
        po[str(project_id)] = override_values
        self.config["project_overrides"] = po
        self.save()

    def get_detection_params(self, project_id: Optional[int] = None) -> Dict[str, Any]:
        """Get detection parameters, honoring per-project overrides if provided."""
        if project_id is not None:
            po = self.config.get("project_overrides", {})
            ov = po.get(str(project_id), None)
            if ov:
                return {
                    "min_face_size": ov.get("min_face_size", self.config.get("min_face_size", 20)),
                    "confidence_threshold": ov.get("confidence_threshold", self.config.get("confidence_threshold", 0.65)),
                }
        return {
            "min_face_size": self.config.get("min_face_size", 20),
            "confidence_threshold": self.config.get("confidence_threshold", 0.65),
        }

    def get_face_cache_dir(self) -> Path:
        """Get face cache directory path."""
        cache_dir = Path(self.config.get("face_cache_dir", ".face_cache"))
        cache_dir.mkdir(exist_ok=True)
        return cache_dir

    def reset_to_defaults(self) -> None:
        """Reset configuration to defaults."""
        self.config = self.DEFAULT_CONFIG.copy()
        self.save()


# Global configuration instance
_config: Optional[FaceDetectionConfig] = None


def get_face_config() -> FaceDetectionConfig:
    """Get global face detection configuration instance."""
    global _config
    if _config is None:
        _config = FaceDetectionConfig()
    return _config


def reload_config() -> None:
    """Reload configuration from disk."""
    global _config
    _config = FaceDetectionConfig()
