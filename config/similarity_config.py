"""
SimilarityConfig - Unified Configuration for Similar Photo Detection

Version: 1.0.0
Date: 2026-01-25

Centralizes all similarity detection parameters to ensure consistent
behavior across different entry points (scan_controller, google_layout, etc.).

Usage:
    from config.similarity_config import SimilarityConfig

    # Get parameters from user settings or defaults
    params = SimilarityConfig.get_params()

    # Use in stack generation
    stack_service.regenerate_similar_shot_stacks(project_id, params)
"""

from dataclasses import dataclass
from typing import Optional

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SimilarityDefaults:
    """Default values for similarity detection parameters."""

    # Similarity threshold (0.0 - 1.0)
    # Higher = more strict, fewer matches
    # 0.75 is a balanced default that catches similar shots without false positives
    SIMILARITY_THRESHOLD: float = 0.75

    # Time window in seconds for grouping similar shots
    # Photos must be taken within this window to be considered candidates
    TIME_WINDOW_SECONDS: int = 30

    # Minimum number of photos to form a stack
    MIN_STACK_SIZE: int = 2

    # Maximum candidates to consider per photo (performance limit)
    CANDIDATE_LIMIT_PER_PHOTO: int = 300

    # Top K similar photos to consider per candidate
    TOP_K: int = 30

    # Rule version for stack regeneration
    RULE_VERSION: str = "1"

    # Cross-date similarity detection (global pass)
    CROSS_DATE_SIMILARITY: bool = True
    CROSS_DATE_THRESHOLD: float = 0.85


class SimilarityConfig:
    """
    Centralized similarity detection configuration.

    Reads from user preferences with fallback to sensible defaults.
    Ensures all code paths use the same parameters.
    """

    @classmethod
    def get_params(cls):
        """
        Get StackGenParams from user settings or defaults.

        Returns:
            StackGenParams with current configuration
        """
        from services.stack_generation_service import StackGenParams

        # Try to get user preferences
        similarity_threshold = cls.get_similarity_threshold()
        time_window = cls.get_time_window_seconds()
        min_stack_size = cls.get_min_stack_size()

        cross_date_similarity = cls.get_cross_date_similarity()
        cross_date_threshold = cls.get_cross_date_threshold()

        return StackGenParams(
            rule_version=SimilarityDefaults.RULE_VERSION,
            time_window_seconds=time_window,
            min_stack_size=min_stack_size,
            top_k=SimilarityDefaults.TOP_K,
            similarity_threshold=similarity_threshold,
            candidate_limit_per_photo=SimilarityDefaults.CANDIDATE_LIMIT_PER_PHOTO,
            cross_date_similarity=cross_date_similarity,
            cross_date_threshold=cross_date_threshold,
        )

    @classmethod
    def get_similarity_threshold(cls) -> float:
        """Get similarity threshold from settings or default."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("similarity_threshold", None)
            if value is not None:
                threshold = float(value)
                # Clamp to valid range
                if 0.0 <= threshold <= 1.0:
                    return threshold
                else:
                    logger.warning(f"[SimilarityConfig] Invalid threshold {threshold}, using default")
        except Exception as e:
            logger.debug(f"[SimilarityConfig] Could not read settings: {e}")

        return SimilarityDefaults.SIMILARITY_THRESHOLD

    @classmethod
    def get_time_window_seconds(cls) -> int:
        """Get time window from settings or default."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("similarity_time_window", None)
            if value is not None:
                window = int(value)
                if window > 0:
                    return window
        except Exception as e:
            logger.debug(f"[SimilarityConfig] Could not read settings: {e}")

        return SimilarityDefaults.TIME_WINDOW_SECONDS

    @classmethod
    def get_min_stack_size(cls) -> int:
        """Get minimum stack size from settings or default."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("similarity_min_stack_size", None)
            if value is not None:
                size = int(value)
                if size >= 2:
                    return size
        except Exception as e:
            logger.debug(f"[SimilarityConfig] Could not read settings: {e}")

        return SimilarityDefaults.MIN_STACK_SIZE

    @classmethod
    def get_cross_date_similarity(cls) -> bool:
        """Get cross-date similarity enabled flag from settings or default."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("cross_date_similarity", None)
            if value is not None:
                return bool(value)
        except Exception as e:
            logger.debug(f"[SimilarityConfig] Could not read settings: {e}")

        return SimilarityDefaults.CROSS_DATE_SIMILARITY

    @classmethod
    def get_cross_date_threshold(cls) -> float:
        """Get cross-date similarity threshold from settings or default."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("cross_date_threshold", None)
            if value is not None:
                threshold = float(value)
                if 0.0 <= threshold <= 1.0:
                    return threshold
        except Exception as e:
            logger.debug(f"[SimilarityConfig] Could not read settings: {e}")

        return SimilarityDefaults.CROSS_DATE_THRESHOLD

    @classmethod
    def set_similarity_threshold(cls, threshold: float) -> bool:
        """
        Save similarity threshold to settings.

        Args:
            threshold: Value between 0.0 and 1.0

        Returns:
            True if saved successfully
        """
        if not 0.0 <= threshold <= 1.0:
            logger.warning(f"[SimilarityConfig] Invalid threshold {threshold}")
            return False

        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("similarity_threshold", threshold)
            logger.info(f"[SimilarityConfig] Saved similarity_threshold={threshold}")
            return True
        except Exception as e:
            logger.error(f"[SimilarityConfig] Failed to save threshold: {e}")
            return False

    @classmethod
    def set_time_window_seconds(cls, seconds: int) -> bool:
        """Save time window to settings."""
        if seconds <= 0:
            logger.warning(f"[SimilarityConfig] Invalid time window {seconds}")
            return False

        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("similarity_time_window", seconds)
            logger.info(f"[SimilarityConfig] Saved similarity_time_window={seconds}")
            return True
        except Exception as e:
            logger.error(f"[SimilarityConfig] Failed to save time window: {e}")
            return False

    @classmethod
    def set_min_stack_size(cls, size: int) -> bool:
        """Save minimum stack size to settings."""
        if size < 2:
            logger.warning(f"[SimilarityConfig] Invalid min stack size {size}")
            return False

        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("similarity_min_stack_size", size)
            logger.info(f"[SimilarityConfig] Saved similarity_min_stack_size={size}")
            return True
        except Exception as e:
            logger.error(f"[SimilarityConfig] Failed to save min stack size: {e}")
            return False
