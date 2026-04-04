"""
SearchConfig - Unified Configuration for Smart Find & Semantic Search

Centralizes all search/discovery parameters to ensure consistent
behavior and allow tuning from Preferences dialog.

Usage:
    from config.search_config import SearchConfig

    # Get Smart Find parameters
    threshold = SearchConfig.get_clip_threshold()
    top_k = SearchConfig.get_default_top_k()
    cache_ttl = SearchConfig.get_cache_ttl()

    # Get Semantic Search (toolbar) parameters
    min_sim = SearchConfig.get_semantic_min_similarity()
"""

from dataclasses import dataclass

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SearchDefaults:
    """Default values for search parameters."""

    # ── Smart Find (preset/NLP search) ──

    # CLIP similarity threshold for Smart Find presets (0.0 - 1.0)
    # Lower = more results but less precise, Higher = fewer but more accurate
    # 0.22 is tuned for CLIP ViT-B/32 text-image similarity
    CLIP_THRESHOLD: float = 0.22

    # Maximum results returned per Smart Find query
    DEFAULT_TOP_K: int = 200

    # Result cache TTL in seconds (how long cached results stay valid)
    CACHE_TTL: int = 300  # 5 minutes

    # ── Semantic Search (toolbar widget) ──

    # Minimum similarity for semantic search widget results
    # Higher than CLIP_THRESHOLD because this is direct user search
    SEMANTIC_MIN_SIMILARITY: float = 0.30

    # Default top_k for semantic search widget
    SEMANTIC_TOP_K: int = 20

    # Search debounce delay in milliseconds (for text input)
    SEARCH_DEBOUNCE_MS: int = 500

    # ── NLP Parser ──

    # Enable NLP parsing of free-text queries (extract dates, ratings, etc.)
    NLP_ENABLED: bool = True

    # ── Result Display ──

    # Show confidence scores on search result thumbnails
    SHOW_CONFIDENCE_SCORES: bool = True

    # Minimum confidence to display (filter out very low matches)
    MIN_DISPLAY_CONFIDENCE: float = 0.15

    # ── Fusion & Scoring ──

    # Fusion mode for multi-prompt scoring: "max", "weighted_max", "soft_or"
    FUSION_MODE: str = "max"

    # Semantic weight in final score (alpha):
    #   0.8 = concept searches (Beach, Wedding), 0.4 = utility searches (Screenshots)
    SEMANTIC_WEIGHT: float = 0.8

    # Metadata soft-boost values (graded scoring, not pass/fail)
    META_BOOST_GPS: float = 0.05
    META_BOOST_RATING: float = 0.10
    META_BOOST_DATE: float = 0.03

    # Dynamic threshold backoff: retry with lower threshold when 0 results
    THRESHOLD_BACKOFF_ENABLED: bool = True
    THRESHOLD_BACKOFF_STEP: float = 0.04  # Lower by this amount per retry
    THRESHOLD_BACKOFF_MAX_RETRIES: int = 2  # Max retries before giving up


class SearchConfig:
    """
    Centralized search configuration.

    Reads from user preferences with fallback to sensible defaults.
    """

    # ── Smart Find Parameters ──

    @classmethod
    def get_clip_threshold(cls) -> float:
        """Get CLIP similarity threshold for Smart Find."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_clip_threshold", None)
            if value is not None:
                threshold = float(value)
                if 0.05 <= threshold <= 0.80:
                    return threshold
                else:
                    logger.warning(f"[SearchConfig] Invalid CLIP threshold {threshold}, using default")
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.CLIP_THRESHOLD

    @classmethod
    def set_clip_threshold(cls, threshold: float) -> bool:
        """Save CLIP threshold to settings."""
        if not 0.05 <= threshold <= 0.80:
            logger.warning(f"[SearchConfig] Invalid CLIP threshold {threshold}")
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_clip_threshold", threshold)
            logger.info(f"[SearchConfig] Saved search_clip_threshold={threshold}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save CLIP threshold: {e}")
            return False

    @classmethod
    def get_default_top_k(cls) -> int:
        """Get default max results for Smart Find."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_default_top_k", None)
            if value is not None:
                top_k = int(value)
                if 10 <= top_k <= 2000:
                    return top_k
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.DEFAULT_TOP_K

    @classmethod
    def set_default_top_k(cls, top_k: int) -> bool:
        """Save default top_k to settings."""
        if not 10 <= top_k <= 2000:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_default_top_k", top_k)
            logger.info(f"[SearchConfig] Saved search_default_top_k={top_k}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save top_k: {e}")
            return False

    @classmethod
    def get_cache_ttl(cls) -> int:
        """Get result cache TTL in seconds."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_cache_ttl", None)
            if value is not None:
                ttl = int(value)
                if 0 <= ttl <= 3600:
                    return ttl
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.CACHE_TTL

    @classmethod
    def set_cache_ttl(cls, ttl: int) -> bool:
        """Save cache TTL to settings."""
        if not 0 <= ttl <= 3600:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_cache_ttl", ttl)
            logger.info(f"[SearchConfig] Saved search_cache_ttl={ttl}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save cache TTL: {e}")
            return False

    # ── Semantic Search (Toolbar) Parameters ──

    @classmethod
    def get_semantic_min_similarity(cls) -> float:
        """Get minimum similarity for semantic search widget."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_semantic_min_similarity", None)
            if value is not None:
                sim = float(value)
                if 0.05 <= sim <= 0.80:
                    return sim
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.SEMANTIC_MIN_SIMILARITY

    @classmethod
    def set_semantic_min_similarity(cls, similarity: float) -> bool:
        """Save semantic min similarity to settings."""
        if not 0.05 <= similarity <= 0.80:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_semantic_min_similarity", similarity)
            logger.info(f"[SearchConfig] Saved search_semantic_min_similarity={similarity}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save semantic min similarity: {e}")
            return False

    @classmethod
    def get_semantic_top_k(cls) -> int:
        """Get default top_k for semantic search widget."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_semantic_top_k", None)
            if value is not None:
                top_k = int(value)
                if 5 <= top_k <= 500:
                    return top_k
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.SEMANTIC_TOP_K

    @classmethod
    def set_semantic_top_k(cls, top_k: int) -> bool:
        """Save semantic top_k to settings."""
        if not 5 <= top_k <= 500:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_semantic_top_k", top_k)
            logger.info(f"[SearchConfig] Saved search_semantic_top_k={top_k}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save semantic top_k: {e}")
            return False

    @classmethod
    def get_search_debounce_ms(cls) -> int:
        """Get search debounce delay in milliseconds."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_debounce_ms", None)
            if value is not None:
                ms = int(value)
                if 100 <= ms <= 2000:
                    return ms
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.SEARCH_DEBOUNCE_MS

    @classmethod
    def set_search_debounce_ms(cls, ms: int) -> bool:
        """Save search debounce delay to settings."""
        if not 100 <= ms <= 2000:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_debounce_ms", ms)
            logger.info(f"[SearchConfig] Saved search_debounce_ms={ms}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save debounce: {e}")
            return False

    # ── NLP / Display Settings ──

    @classmethod
    def get_nlp_enabled(cls) -> bool:
        """Get whether NLP parsing is enabled for free-text queries."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_nlp_enabled", None)
            if value is not None:
                return bool(value)
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.NLP_ENABLED

    @classmethod
    def set_nlp_enabled(cls, enabled: bool) -> bool:
        """Save NLP enabled flag to settings."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_nlp_enabled", enabled)
            logger.info(f"[SearchConfig] Saved search_nlp_enabled={enabled}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save NLP enabled: {e}")
            return False

    @classmethod
    def get_show_confidence_scores(cls) -> bool:
        """Get whether confidence scores are shown on results."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_show_confidence", None)
            if value is not None:
                return bool(value)
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.SHOW_CONFIDENCE_SCORES

    @classmethod
    def set_show_confidence_scores(cls, show: bool) -> bool:
        """Save show confidence scores flag to settings."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_show_confidence", show)
            logger.info(f"[SearchConfig] Saved search_show_confidence={show}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save show confidence: {e}")
            return False

    @classmethod
    def get_min_display_confidence(cls) -> float:
        """Get minimum confidence to display in results."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_min_display_confidence", None)
            if value is not None:
                conf = float(value)
                if 0.0 <= conf <= 0.50:
                    return conf
        except Exception as e:
            logger.debug(f"[SearchConfig] Could not read settings: {e}")
        return SearchDefaults.MIN_DISPLAY_CONFIDENCE

    @classmethod
    def set_min_display_confidence(cls, confidence: float) -> bool:
        """Save min display confidence to settings."""
        if not 0.0 <= confidence <= 0.50:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_min_display_confidence", confidence)
            logger.info(f"[SearchConfig] Saved search_min_display_confidence={confidence}")
            return True
        except Exception as e:
            logger.error(f"[SearchConfig] Failed to save min display confidence: {e}")
            return False

    # ── Fusion & Scoring Settings ──

    @classmethod
    def get_fusion_mode(cls) -> str:
        """Get multi-prompt fusion mode: 'max', 'weighted_max', 'soft_or'."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_fusion_mode", None)
            if value and value in ("max", "weighted_max", "soft_or"):
                return value
        except Exception:
            pass
        return SearchDefaults.FUSION_MODE

    @classmethod
    def set_fusion_mode(cls, mode: str) -> bool:
        """Save fusion mode to settings."""
        if mode not in ("max", "weighted_max", "soft_or"):
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_fusion_mode", mode)
            return True
        except Exception:
            return False

    @classmethod
    def get_semantic_weight(cls) -> float:
        """Get semantic vs metadata weight (alpha). 0.0=all metadata, 1.0=all semantic."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_semantic_weight", None)
            if value is not None:
                w = float(value)
                if 0.0 <= w <= 1.0:
                    return w
        except Exception:
            pass
        return SearchDefaults.SEMANTIC_WEIGHT

    @classmethod
    def set_semantic_weight(cls, weight: float) -> bool:
        """Save semantic weight to settings."""
        if not 0.0 <= weight <= 1.0:
            return False
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_semantic_weight", weight)
            return True
        except Exception:
            return False

    @classmethod
    def get_threshold_backoff_enabled(cls) -> bool:
        """Get whether dynamic threshold backoff is enabled."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_threshold_backoff", None)
            if value is not None:
                return bool(value)
        except Exception:
            pass
        return SearchDefaults.THRESHOLD_BACKOFF_ENABLED

    @classmethod
    def set_threshold_backoff_enabled(cls, enabled: bool) -> bool:
        """Save threshold backoff enabled flag."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("search_threshold_backoff", enabled)
            return True
        except Exception:
            return False

    # ── Metadata Boost Settings ──

    @classmethod
    def get_meta_boost_gps(cls) -> float:
        """Get GPS metadata boost value."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_meta_boost_gps", None)
            if value is not None:
                v = float(value)
                if 0.0 <= v <= 0.50:
                    return v
        except Exception:
            pass
        return SearchDefaults.META_BOOST_GPS

    @classmethod
    def set_meta_boost_gps(cls, v: float) -> bool:
        if not 0.0 <= v <= 0.50:
            return False
        try:
            from settings_manager_qt import SettingsManager
            SettingsManager().set("search_meta_boost_gps", v)
            return True
        except Exception:
            return False

    @classmethod
    def get_meta_boost_rating(cls) -> float:
        """Get rating metadata boost value."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_meta_boost_rating", None)
            if value is not None:
                v = float(value)
                if 0.0 <= v <= 0.50:
                    return v
        except Exception:
            pass
        return SearchDefaults.META_BOOST_RATING

    @classmethod
    def set_meta_boost_rating(cls, v: float) -> bool:
        if not 0.0 <= v <= 0.50:
            return False
        try:
            from settings_manager_qt import SettingsManager
            SettingsManager().set("search_meta_boost_rating", v)
            return True
        except Exception:
            return False

    @classmethod
    def get_meta_boost_date(cls) -> float:
        """Get date metadata boost value."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_meta_boost_date", None)
            if value is not None:
                v = float(value)
                if 0.0 <= v <= 0.50:
                    return v
        except Exception:
            pass
        return SearchDefaults.META_BOOST_DATE

    @classmethod
    def set_meta_boost_date(cls, v: float) -> bool:
        if not 0.0 <= v <= 0.50:
            return False
        try:
            from settings_manager_qt import SettingsManager
            SettingsManager().set("search_meta_boost_date", v)
            return True
        except Exception:
            return False

    # ── Threshold Backoff Parameters ──

    @classmethod
    def get_threshold_backoff_step(cls) -> float:
        """Get threshold backoff step size."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_backoff_step", None)
            if value is not None:
                v = float(value)
                if 0.01 <= v <= 0.20:
                    return v
        except Exception:
            pass
        return SearchDefaults.THRESHOLD_BACKOFF_STEP

    @classmethod
    def set_threshold_backoff_step(cls, v: float) -> bool:
        if not 0.01 <= v <= 0.20:
            return False
        try:
            from settings_manager_qt import SettingsManager
            SettingsManager().set("search_backoff_step", v)
            return True
        except Exception:
            return False

    @classmethod
    def get_threshold_backoff_max_retries(cls) -> int:
        """Get max retries for threshold backoff."""
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            value = settings.get("search_backoff_max_retries", None)
            if value is not None:
                v = int(value)
                if 0 <= v <= 5:
                    return v
        except Exception:
            pass
        return SearchDefaults.THRESHOLD_BACKOFF_MAX_RETRIES

    @classmethod
    def set_threshold_backoff_max_retries(cls, v: int) -> bool:
        if not 0 <= v <= 5:
            return False
        try:
            from settings_manager_qt import SettingsManager
            SettingsManager().set("search_backoff_max_retries", v)
            return True
        except Exception:
            return False
