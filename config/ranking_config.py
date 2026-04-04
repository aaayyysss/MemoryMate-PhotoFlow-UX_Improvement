"""
RankingConfig - Dynamic Configuration for Search Ranking Weights

Centralizes all ranking/scoring weight parameters so they can be
tuned from the Preferences dialog (Search & Discovery tab).

Each preset family (scenic, type, people_event, utility, animal_object)
has its own weight profile.  All family weights are user-tunable via
per-family preference keys (e.g. ranking_type_w_clip).  The hardcoded
defaults in FAMILY_DEFAULTS serve as fallbacks when no user preference
is set.

Usage:
    from config.ranking_config import RankingConfig

    # Get default (scenic) weights
    weights = RankingConfig.get_default_weights()

    # Get per-family weight
    clip_w = RankingConfig.get_family_weight("type", "w_clip")

    # Get recency guardrails
    halflife = RankingConfig.get_recency_halflife_days()
"""

from dataclasses import dataclass
from typing import Dict

from logging_config import get_logger

logger = get_logger(__name__)


# ── Hardcoded best-practice defaults ──

@dataclass
class RankingDefaults:
    """Default ranking weight values (scenic / general profile)."""

    # Primary scoring weights (must sum to ~1.0)
    W_CLIP: float = 0.75
    W_RECENCY: float = 0.05
    W_FAVORITE: float = 0.08
    W_LOCATION: float = 0.04
    W_FACE_MATCH: float = 0.08
    W_STRUCTURAL: float = 0.00
    W_OCR: float = 0.00

    # Guardrails
    MAX_RECENCY_BOOST: float = 0.10
    MAX_FAVORITE_BOOST: float = 0.15
    RECENCY_HALFLIFE_DAYS: int = 90

    # Metadata soft-boost values (applied on top of semantic score)
    META_BOOST_GPS: float = 0.05
    META_BOOST_RATING: float = 0.10
    META_BOOST_DATE: float = 0.03

    # Dynamic threshold backoff
    THRESHOLD_BACKOFF_STEP: float = 0.04
    THRESHOLD_BACKOFF_MAX_RETRIES: int = 2


# ── Per-family default weight profiles ──
# Keys: w_clip, w_recency, w_favorite, w_location, w_face_match,
#        w_structural, w_ocr
# Each profile must sum to ~1.0.

FAMILY_DEFAULTS: Dict[str, Dict[str, float]] = {
    "scenic": {
        "w_clip": 0.72, "w_recency": 0.04, "w_favorite": 0.04,
        "w_location": 0.08, "w_face_match": 0.02,
        "w_structural": 0.10, "w_ocr": 0.00, "w_event": 0.00,
        "w_screenshot": 0.00,
    },
    "type": {
        "w_clip": 0.20, "w_recency": 0.03, "w_favorite": 0.02,
        "w_location": 0.00, "w_face_match": 0.00,
        "w_structural": 0.40, "w_ocr": 0.22, "w_event": 0.00,
        "w_screenshot": 0.13,
    },
    "people_event": {
        "w_clip": 0.33, "w_recency": 0.07, "w_favorite": 0.05,
        "w_location": 0.00, "w_face_match": 0.30,
        "w_structural": 0.00, "w_ocr": 0.00, "w_event": 0.25,
        "w_screenshot": 0.00,
    },
    "utility": {
        "w_clip": 0.00, "w_recency": 0.20, "w_favorite": 0.55,
        "w_location": 0.25, "w_face_match": 0.00,
        "w_structural": 0.00, "w_ocr": 0.00, "w_event": 0.00,
        "w_screenshot": 0.00,
    },
    "animal_object": {
        "w_clip": 0.88, "w_recency": 0.05, "w_favorite": 0.03,
        "w_location": 0.00, "w_face_match": 0.00,
        "w_structural": 0.04, "w_ocr": 0.00, "w_event": 0.00,
        "w_screenshot": 0.00,
    },
}


class RankingConfig:
    """
    Centralized ranking configuration.

    Reads from user preferences with fallback to RankingDefaults.
    """

    # ── Per-family weight getters/setters ──

    _WEIGHT_KEYS = ("w_clip", "w_recency", "w_favorite", "w_location",
                     "w_face_match", "w_structural", "w_ocr", "w_event", "w_screenshot")

    @classmethod
    def get_family_weight(cls, family: str, weight_name: str) -> float:
        """Get a single weight for a specific family from preferences.

        Preference key format: ranking_{family}_{weight_name}
        Falls back to FAMILY_DEFAULTS, then to the scenic/general default.
        """
        family_defaults = FAMILY_DEFAULTS.get(family, FAMILY_DEFAULTS.get("scenic", {}))
        default = family_defaults.get(weight_name, 0.0)
        key = f"ranking_{family}_{weight_name}"
        return cls._get_float(key, default, 0.0, 1.0)

    @classmethod
    def set_family_weight(cls, family: str, weight_name: str, value: float) -> bool:
        """Set a single weight for a specific family in preferences."""
        key = f"ranking_{family}_{weight_name}"
        return cls._set_float(key, value, 0.0, 1.0)

    @classmethod
    def get_family_weights_dict(cls, family: str) -> dict:
        """Get all weights for a family as a dict, reading from preferences."""
        return {k: cls.get_family_weight(family, k) for k in cls._WEIGHT_KEYS}

    # ── Legacy scenic/general weight getters (backward compatible) ──

    @classmethod
    def get_w_clip(cls) -> float:
        return cls.get_family_weight("scenic", "w_clip")

    @classmethod
    def get_w_recency(cls) -> float:
        return cls.get_family_weight("scenic", "w_recency")

    @classmethod
    def get_w_favorite(cls) -> float:
        return cls.get_family_weight("scenic", "w_favorite")

    @classmethod
    def get_w_location(cls) -> float:
        return cls.get_family_weight("scenic", "w_location")

    @classmethod
    def get_w_face_match(cls) -> float:
        return cls.get_family_weight("scenic", "w_face_match")

    @classmethod
    def get_w_structural(cls) -> float:
        return cls.get_family_weight("scenic", "w_structural")

    @classmethod
    def get_w_ocr(cls) -> float:
        return cls.get_family_weight("scenic", "w_ocr")

    @classmethod
    def get_w_screenshot(cls) -> float:
        return cls.get_family_weight("scenic", "w_screenshot")

    # ── Guardrail getters ──

    @classmethod
    def get_max_recency_boost(cls) -> float:
        return cls._get_float("ranking_max_recency_boost", RankingDefaults.MAX_RECENCY_BOOST, 0.0, 1.0)

    @classmethod
    def get_max_favorite_boost(cls) -> float:
        return cls._get_float("ranking_max_favorite_boost", RankingDefaults.MAX_FAVORITE_BOOST, 0.0, 1.0)

    @classmethod
    def get_recency_halflife_days(cls) -> int:
        return cls._get_int("ranking_recency_halflife_days", RankingDefaults.RECENCY_HALFLIFE_DAYS, 1, 730)

    # ── Metadata boost getters ──

    @classmethod
    def get_meta_boost_gps(cls) -> float:
        return cls._get_float("ranking_meta_boost_gps", RankingDefaults.META_BOOST_GPS, 0.0, 0.50)

    @classmethod
    def get_meta_boost_rating(cls) -> float:
        return cls._get_float("ranking_meta_boost_rating", RankingDefaults.META_BOOST_RATING, 0.0, 0.50)

    @classmethod
    def get_meta_boost_date(cls) -> float:
        return cls._get_float("ranking_meta_boost_date", RankingDefaults.META_BOOST_DATE, 0.0, 0.50)

    # ── Threshold backoff getters ──

    @classmethod
    def get_threshold_backoff_step(cls) -> float:
        return cls._get_float("ranking_backoff_step", RankingDefaults.THRESHOLD_BACKOFF_STEP, 0.01, 0.20)

    @classmethod
    def get_threshold_backoff_max_retries(cls) -> int:
        return cls._get_int("ranking_backoff_max_retries", RankingDefaults.THRESHOLD_BACKOFF_MAX_RETRIES, 0, 5)

    # ── Legacy scenic setters (backward compatible) ──

    @classmethod
    def set_w_clip(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_clip", v)

    @classmethod
    def set_w_recency(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_recency", v)

    @classmethod
    def set_w_favorite(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_favorite", v)

    @classmethod
    def set_w_location(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_location", v)

    @classmethod
    def set_w_face_match(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_face_match", v)

    @classmethod
    def set_w_structural(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_structural", v)

    @classmethod
    def set_w_ocr(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_ocr", v)

    @classmethod
    def set_w_screenshot(cls, v: float) -> bool:
        return cls.set_family_weight("scenic", "w_screenshot", v)

    @classmethod
    def set_max_recency_boost(cls, v: float) -> bool:
        return cls._set_float("ranking_max_recency_boost", v, 0.0, 1.0)

    @classmethod
    def set_max_favorite_boost(cls, v: float) -> bool:
        return cls._set_float("ranking_max_favorite_boost", v, 0.0, 1.0)

    @classmethod
    def set_recency_halflife_days(cls, v: int) -> bool:
        return cls._set_int("ranking_recency_halflife_days", v, 1, 730)

    @classmethod
    def set_meta_boost_gps(cls, v: float) -> bool:
        return cls._set_float("ranking_meta_boost_gps", v, 0.0, 0.50)

    @classmethod
    def set_meta_boost_rating(cls, v: float) -> bool:
        return cls._set_float("ranking_meta_boost_rating", v, 0.0, 0.50)

    @classmethod
    def set_meta_boost_date(cls, v: float) -> bool:
        return cls._set_float("ranking_meta_boost_date", v, 0.0, 0.50)

    @classmethod
    def set_threshold_backoff_step(cls, v: float) -> bool:
        return cls._set_float("ranking_backoff_step", v, 0.01, 0.20)

    @classmethod
    def set_threshold_backoff_max_retries(cls, v: int) -> bool:
        return cls._set_int("ranking_backoff_max_retries", v, 0, 5)

    # ── Internal helpers ──

    @classmethod
    def _get_float(cls, key: str, default: float, lo: float, hi: float) -> float:
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get(key, None)
            if value is not None:
                v = float(value)
                if lo <= v <= hi:
                    return v
                logger.warning(f"[RankingConfig] {key}={v} out of range [{lo},{hi}], using default")
        except Exception as e:
            logger.debug(f"[RankingConfig] Could not read {key}: {e}")
        return default

    @classmethod
    def _get_int(cls, key: str, default: int, lo: int, hi: int) -> int:
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get(key, None)
            if value is not None:
                v = int(value)
                if lo <= v <= hi:
                    return v
                logger.warning(f"[RankingConfig] {key}={v} out of range [{lo},{hi}], using default")
        except Exception as e:
            logger.debug(f"[RankingConfig] Could not read {key}: {e}")
        return default

    @classmethod
    def _set_float(cls, key: str, v: float, lo: float, hi: float) -> bool:
        if not lo <= v <= hi:
            logger.warning(f"[RankingConfig] Invalid {key}={v}")
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set(key, v)
            logger.info(f"[RankingConfig] Saved {key}={v}")
            return True
        except Exception as e:
            logger.error(f"[RankingConfig] Failed to save {key}: {e}")
            return False

    @classmethod
    def _set_int(cls, key: str, v: int, lo: int, hi: int) -> bool:
        if not lo <= v <= hi:
            logger.warning(f"[RankingConfig] Invalid {key}={v}")
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set(key, v)
            logger.info(f"[RankingConfig] Saved {key}={v}")
            return True
        except Exception as e:
            logger.error(f"[RankingConfig] Failed to save {key}: {e}")
            return False
