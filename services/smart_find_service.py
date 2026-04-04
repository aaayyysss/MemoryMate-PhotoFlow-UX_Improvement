# services/smart_find_service.py
# Smart Find Service - Combines CLIP semantic search + metadata filtering
# Inspired by iPhone/Google Photos/Lightroom/Excire discovery patterns

"""
SmartFindService - Intelligent photo discovery engine.

Architecture (aligned with audit reference design):
1. Query normalization (resolve preset, NLP extraction)
2. Hybrid candidate retrieval (semantic CLIP + metadata SQL)
3. Graded score fusion with explainability
4. Cache with index_version invalidation
5. Query cancellation (only latest request wins)

Scoring fusion strategy:
- Semantic: multi-prompt fusion (max, weighted_max, soft_or)
- Metadata: graded scoring (soft boosts, not just pass/fail)
- Final = alpha * semantic + (1-alpha) * metadata + boosts
- Dynamic threshold backoff for empty results

Usage:
    from services.smart_find_service import SmartFindService, get_smart_find_service

    service = get_smart_find_service(project_id=1)
    result = service.find_by_preset("beach")
    # Returns: SmartFindResult with explainability
"""

import json
import re
import time
import uuid
import threading
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from logging_config import get_logger

logger = get_logger(__name__)


# ── Builtin Presets (iPhone/Google Photos inspired categories) ──

BUILTIN_PRESETS = [
    # ── Family: scenic (recall-first, multi-prompt expansion) ──
    # These use broad semantic recall with soft gates only.
    # Expanded prompt lists aligned with Excire/Google Photos multi-prompt practice.
    {
        "id": "beach", "name": "Beach", "icon": "\U0001f3d6\ufe0f",
        "prompts": [
            "beach", "sandy beach", "shoreline", "seaside",
            "ocean coast", "waves on shore", "tropical beach",
        ],
        "category": "places",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "mountains", "name": "Mountains", "icon": "\u26f0\ufe0f",
        "prompts": [
            "mountain landscape", "alpine peaks", "rocky ridge",
            "snow mountain", "mountain valley", "hiking trail",
            "summit view", "mountain panorama",
        ],
        "category": "places",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "city", "name": "City", "icon": "\U0001f3d9\ufe0f",
        "prompts": [
            "city skyline", "urban street", "buildings",
            "downtown", "street view", "cityscape",
            "metropolitan area", "city at night",
        ],
        "category": "places",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "forest", "name": "Forest", "icon": "\U0001f332",
        "prompts": [
            "forest", "dense trees", "woods",
            "nature trail", "woodland path", "rainforest",
            "autumn forest", "tree canopy",
        ],
        "category": "places",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "lake", "name": "Lake & River", "icon": "\U0001f3de\ufe0f",
        "prompts": [
            "lake", "river", "waterfall",
            "pond", "stream", "lakeside",
            "river bank", "calm water reflection",
        ],
        "category": "places",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },

    # ── Family: people_event (face-required) ──
    {
        "id": "wedding", "name": "Wedding", "icon": "\U0001f492",
        "prompts": [
            "wedding", "bride", "wedding ceremony",
            "wedding dress", "wedding reception",
            "groom", "wedding couple",
        ],
        "category": "events",
        "family": "people_event",
        "requires_entity_index": "faces",
        "gate_profile": {"require_faces": True, "min_face_count": 1},
    },
    {
        "id": "party", "name": "Party", "icon": "\U0001f389",
        "prompts": [
            "party", "celebration", "birthday party",
            "gathering", "birthday cake", "festive crowd",
        ],
        "category": "events",
        "family": "people_event",
        "requires_entity_index": "faces",
        "gate_profile": {"require_faces": True, "min_face_count": 1},
    },

    # ── Family: scenic (events / activities) ──
    {
        "id": "travel", "name": "Travel", "icon": "\u2708\ufe0f",
        "prompts": [
            "travel", "vacation", "sightseeing",
            "tourist attraction", "landmark",
            "travel destination", "holiday trip",
        ],
        "category": "events",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "sport", "name": "Sports", "icon": "\u26bd",
        "prompts": [
            "sport", "playing sports", "athletic activity",
            "game", "exercise", "stadium",
            "outdoor sports", "fitness",
        ],
        "category": "events",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },

    # ── Family: scenic (subjects) ──
    {
        "id": "sunset", "name": "Sunset & Sunrise", "icon": "\U0001f305",
        "prompts": [
            "sunset", "sunrise", "golden hour",
            "dusk sky", "dawn light", "orange sky",
            "sun setting over horizon",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "food", "name": "Food & Drinks", "icon": "\U0001f355",
        "prompts": [
            "food", "meal", "dish",
            "restaurant table", "cooking",
            "gourmet plate", "dessert",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "pets", "name": "Pets & Animals", "icon": "\U0001f43e",
        "prompts": [
            "pet dog", "dog", "puppy",
            "pet cat", "cat", "kitten",
            "dog portrait", "cat portrait",
            "puppy playing", "kitten playing",
        ],
        "negative_prompts": [
            "person portrait", "selfie", "group photo",
            "wedding", "party", "human face",
            "people standing", "family photo",
            "landscape scenery", "building architecture",
        ],
        "category": "subjects",
        "family": "animal_object",
        "allow_backoff": False,
        "semantic_weight": 0.6,
        "gate_profile": {
            "exclude_screenshots": True,
            "exclude_faces": True,
        },
    },

    # ── Family: people_event (subjects) ──
    {
        "id": "baby", "name": "Baby & Kids", "icon": "\U0001f476",
        "prompts": [
            "baby", "infant", "toddler",
            "small child", "kids playing",
            "newborn", "child portrait",
        ],
        "category": "subjects",
        "family": "people_event",
        "requires_entity_index": "faces",
        "gate_profile": {"require_faces": True, "min_face_count": 1},
    },
    {
        "id": "portraits", "name": "Portraits", "icon": "\U0001f5bc\ufe0f",
        "prompts": [
            "portrait", "headshot", "face close-up",
            "person posing", "profile photo",
        ],
        "category": "subjects",
        "family": "people_event",
        "requires_entity_index": "faces",
        "gate_profile": {"require_faces": True, "min_face_count": 1},
    },

    # ── Family: scenic (more subjects) ──
    {
        "id": "flowers", "name": "Flowers & Garden", "icon": "\U0001f338",
        "prompts": [
            "flowers", "garden", "blooming",
            "bouquet", "floral", "wildflowers",
            "flower field", "rose garden",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "snow", "name": "Snow & Winter", "icon": "\u2744\ufe0f",
        "prompts": [
            "snow", "winter", "skiing",
            "snowfall", "ice", "frozen lake",
            "winter landscape", "snowy trees",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "night", "name": "Night & Stars", "icon": "\U0001f319",
        "prompts": [
            "night sky", "stars", "night photography",
            "moon", "city lights at night",
            "starry sky", "milky way",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "architecture", "name": "Architecture", "icon": "\U0001f3db\ufe0f",
        "prompts": [
            "architecture", "building facade", "interior design",
            "monument", "church", "modern building",
            "historic architecture", "skyscraper",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },
    {
        "id": "car", "name": "Cars & Vehicles", "icon": "\U0001f697",
        "prompts": [
            "car", "vehicle", "automobile",
            "motorcycle", "truck", "sports car",
            "classic car", "car on road",
        ],
        "category": "subjects",
        "family": "scenic",
        "gate_profile": {"exclude_screenshots": True},
    },

    # ── Family: type (metadata-driven, precision-first) ──
    {
        "id": "screenshots", "name": "Screenshots", "icon": "\U0001f4f1",
        "prompts": ["screenshot", "screen capture", "phone screen"],
        "category": "media",
        "family": "type",
        "retrieval_mode": "screenshot_structural",
        "semantic_weight": 0.2,  # Metadata-dominant: heuristic detection is primary signal
        "allow_backoff": False,  # Precision-first: purity over recall
        "gate_profile": {"require_screenshot": True},
    },
    {
        "id": "documents", "name": "Documents", "icon": "\U0001f4c4",
        "prompts": [
            "scanned document", "printed page", "form",
            "invoice", "receipt", "handwritten note",
            "typed text on paper", "letter page",
        ],
        "negative_prompts": [
            "portrait photo", "person standing", "landscape photo",
            "travel photo", "pet photo", "selfie",
            "screenshot", "phone screen", "app interface",
        ],
        "category": "media",
        "family": "type",
        "retrieval_mode": "type_structural",
        "semantic_weight": 0.4,
        "allow_backoff": False,
        "exclude_faces": True,
        "gate_profile": {
            "exclude_faces": True,
            "exclude_screenshots": True,
            "min_edge_size": 700,
            "require_document_signal": True,
        },
    },
    {
        "id": "videos", "name": "Videos", "icon": "\U0001f3ac",
        "prompts": [],
        "filters": {"media_type": "video"},
        "category": "media",
        "family": "utility",
        "semantic_weight": 0.0,  # Pure metadata
    },
    {
        "id": "panoramas", "name": "Panoramas", "icon": "\U0001f304",
        "prompts": ["panoramic view", "wide landscape"],
        "filters": {"orientation": "landscape", "width_min": 4000},
        "category": "media",
        "family": "type",
        "semantic_weight": 0.4,
    },

    # Quality flags (metadata-only, type family)
    {
        "id": "favorites", "name": "Favorites", "icon": "\u2b50",
        "prompts": [],
        "filters": {"flag": "pick"},
        "category": "quality",
        "family": "utility",
        "semantic_weight": 0.0,
    },
    {
        "id": "gps_photos", "name": "With Location", "icon": "\U0001f4cd",
        "prompts": [],
        "filters": {"has_gps": True},
        "category": "quality",
        "family": "utility",
        "semantic_weight": 0.0,
    },
]

# Build lookup for fast preset access
_BUILTIN_LOOKUP = {p["id"]: p for p in BUILTIN_PRESETS}


# ── Explainability-enriched result ──

@dataclass
class SmartFindResult:
    """Result from a Smart Find query with explainability."""
    paths: List[str]
    query_label: str  # Human-readable label (e.g., "Beach", "Mountains")
    total_matches: int
    execution_time_ms: float
    scores: Optional[Dict[str, float]] = None  # path -> final_score for ranking
    excluded_paths: Optional[List[str]] = None  # paths user chose to exclude
    _cached_at: float = 0.0  # Timestamp when this result was cached
    # ── Explainability fields (audit requirement) ──
    matched_prompts: Optional[Dict[str, str]] = None  # path -> winning prompt
    semantic_scores: Optional[Dict[str, float]] = None  # path -> raw semantic score
    metadata_scores: Optional[Dict[str, float]] = None  # path -> metadata boost
    reasons: Optional[Dict[str, List[str]]] = None  # path -> list of reason strings
    backoff_applied: bool = False  # True if threshold was lowered to get results
    request_id: Optional[str] = None  # For cancellation tracking
    index_version: int = 0  # Index version at time of query


# ── Cancellation Token ──

class _CancelToken:
    """Lightweight cancellation token for inflight queries."""
    __slots__ = ('_cancelled', 'request_id')

    def __init__(self, request_id: str):
        self.request_id = request_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


# ── Natural Language Parser ──

class NLQueryParser:
    """
    Parse natural language queries to extract structured metadata filters.

    Deterministic extraction only (no LLM) for speed and predictability.
    """

    # Date patterns
    _YEAR_PATTERN = re.compile(
        r'\b(?:from|in|during|taken\s+in|shot\s+in)\s+(\d{4})\b', re.IGNORECASE)
    _MONTH_YEAR_PATTERN = re.compile(
        r'\b(?:from|in|during)\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|'
        r'may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|'
        r'nov(?:ember)?|dec(?:ember)?)\s+(\d{4})\b', re.IGNORECASE)
    _RELATIVE_DATE_PATTERN = re.compile(
        r'\b(today|yesterday|this\s+week|last\s+week|this\s+month|last\s+month|'
        r'this\s+year|last\s+year)\b', re.IGNORECASE)

    # Rating patterns
    _RATING_PATTERN = re.compile(
        r'\b(\d)\s*(?:star|stars|\u2605|\u2b50)s?\b', re.IGNORECASE)
    _RATING_WORD_PATTERN = re.compile(
        r'\b(?:rated|rating)\s*(?:>=?\s*)?(\d)\b', re.IGNORECASE)
    _FAVORITES_PATTERN = re.compile(
        r'\bfavorit(?:e|es)\b', re.IGNORECASE)

    # Media type patterns
    _VIDEO_PATTERN = re.compile(r'\bvideos?\b', re.IGNORECASE)
    _PHOTO_PATTERN = re.compile(r'\b(?:photos?\s+only|only\s+photos?)\b', re.IGNORECASE)

    # Location patterns
    _GPS_PATTERN = re.compile(
        r'\b(?:with\s+(?:gps|location|coordinates)|geo-?tagged)\b', re.IGNORECASE)

    # Month name to number mapping
    _MONTH_MAP = {
        'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
        'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6,
        'jul': 7, 'july': 7, 'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
        'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
    }

    @classmethod
    def parse(cls, query: str) -> Tuple[str, Dict]:
        """
        Parse a natural language query into CLIP text + metadata filters.

        Returns:
            (clip_query, filters_dict) - clip_query is the remaining text for CLIP,
            filters_dict has extracted metadata filters.
        """
        filters = {}
        remaining = query

        # Extract month+year (must check before year-only)
        m = cls._MONTH_YEAR_PATTERN.search(remaining)
        if m:
            month_name = m.group(1).lower()
            year = int(m.group(2))
            month_num = cls._MONTH_MAP.get(month_name, 1)
            import calendar
            last_day = calendar.monthrange(year, month_num)[1]
            filters["date_from"] = f"{year}-{month_num:02d}-01"
            filters["date_to"] = f"{year}-{month_num:02d}-{last_day:02d}"
            remaining = remaining[:m.start()] + remaining[m.end():]

        # Extract year
        if "date_from" not in filters:
            m = cls._YEAR_PATTERN.search(remaining)
            if m:
                year = int(m.group(1))
                if 1990 <= year <= 2099:
                    filters["date_from"] = f"{year}-01-01"
                    filters["date_to"] = f"{year}-12-31"
                    remaining = remaining[:m.start()] + remaining[m.end():]

        # Extract relative dates
        if "date_from" not in filters:
            m = cls._RELATIVE_DATE_PATTERN.search(remaining)
            if m:
                filters["_relative_date"] = m.group(1).lower().strip()
                remaining = remaining[:m.start()] + remaining[m.end():]

        # Extract rating
        m = cls._RATING_PATTERN.search(remaining)
        if m:
            filters["rating_min"] = int(m.group(1))
            remaining = remaining[:m.start()] + remaining[m.end():]
        else:
            m = cls._RATING_WORD_PATTERN.search(remaining)
            if m:
                filters["rating_min"] = int(m.group(1))
                remaining = remaining[:m.start()] + remaining[m.end():]
            elif cls._FAVORITES_PATTERN.search(remaining):
                filters["flag"] = "pick"
                remaining = cls._FAVORITES_PATTERN.sub('', remaining)

        # Extract media type
        if cls._VIDEO_PATTERN.search(remaining):
            filters["media_type"] = "video"
            remaining = cls._VIDEO_PATTERN.sub('', remaining)
        elif cls._PHOTO_PATTERN.search(remaining):
            filters["media_type"] = "photo"
            remaining = cls._PHOTO_PATTERN.sub('', remaining)

        # Extract GPS
        if cls._GPS_PATTERN.search(remaining):
            filters["has_gps"] = True
            remaining = cls._GPS_PATTERN.sub('', remaining)

        # Clean remaining text for CLIP
        remaining = re.sub(r'\s+', ' ', remaining).strip()
        # Remove dangling prepositions
        remaining = re.sub(r'\b(?:from|in|during|with|taken|shot)\s*$', '', remaining).strip()

        return remaining, filters


# ── Index Version Provider ──

class _IndexVersionProvider:
    """
    Tracks index version for cache invalidation.

    Incremented when embeddings, scans, metadata, or face pipeline updates occur.
    """
    _version: int = 0
    _lock = threading.Lock()

    @classmethod
    def get(cls, project_id: int) -> int:
        return cls._version

    @classmethod
    def bump(cls, reason: str = ""):
        with cls._lock:
            cls._version += 1
            logger.debug(f"[IndexVersion] Bumped to {cls._version} ({reason})")


class SmartFindService:
    """
    Intelligent photo discovery combining CLIP + metadata with
    explainability, cancellation, and graded scoring.
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._semantic_service = None  # Lazy init
        self._search_service = None  # Lazy init
        self._result_cache: Dict[str, SmartFindResult] = {}
        self._custom_presets: Optional[List[Dict]] = None  # Lazy-loaded
        self._excluded_paths: set = set()  # "Not this" exclusions for current session
        # Cancellation: per-project inflight token
        self._inflight_token: Optional[_CancelToken] = None
        self._inflight_lock = threading.Lock()

    # ── Config Properties ──

    @property
    def _cache_ttl(self) -> int:
        try:
            from config.search_config import SearchConfig
            return SearchConfig.get_cache_ttl()
        except Exception:
            return 300

    def _get_config(self):
        """Get all search config values in one call (reduce repeated imports)."""
        try:
            from config.search_config import SearchConfig, SearchDefaults
            return {
                "top_k": SearchConfig.get_default_top_k(),
                "threshold": SearchConfig.get_clip_threshold(),
                "nlp_enabled": SearchConfig.get_nlp_enabled(),
                "fusion_mode": SearchConfig.get_fusion_mode(),
                "semantic_weight": SearchConfig.get_semantic_weight(),
                "backoff_enabled": SearchConfig.get_threshold_backoff_enabled(),
                "backoff_step": SearchConfig.get_threshold_backoff_step(),
                "backoff_retries": SearchConfig.get_threshold_backoff_max_retries(),
                "meta_boost_gps": SearchConfig.get_meta_boost_gps(),
                "meta_boost_rating": SearchConfig.get_meta_boost_rating(),
                "meta_boost_date": SearchConfig.get_meta_boost_date(),
            }
        except Exception:
            return {
                "top_k": 200, "threshold": 0.22, "nlp_enabled": True,
                "fusion_mode": "max", "semantic_weight": 0.8,
                "backoff_enabled": True, "backoff_step": 0.04, "backoff_retries": 2,
                "meta_boost_gps": 0.05, "meta_boost_rating": 0.10, "meta_boost_date": 0.03,
            }

    @property
    def semantic_service(self):
        if self._semantic_service is None:
            try:
                from services.semantic_search_service import get_semantic_search_service_for_project
                self._semantic_service = get_semantic_search_service_for_project(self.project_id)
            except Exception as e:
                logger.warning(f"[SmartFind] Semantic search not available: {e}")
        return self._semantic_service

    @property
    def search_service(self):
        if self._search_service is None:
            from services.search_service import SearchService
            self._search_service = SearchService()
        return self._search_service

    @property
    def clip_available(self) -> bool:
        svc = self.semantic_service
        return svc is not None and svc.available

    # ── Cancellation ──

    def _begin_query(self) -> _CancelToken:
        """Cancel any inflight query and create a new token."""
        with self._inflight_lock:
            if self._inflight_token is not None:
                self._inflight_token.cancel()
                logger.debug("[SmartFind] Cancelled inflight query")
            token = _CancelToken(str(uuid.uuid4()))
            self._inflight_token = token
            return token

    # ── Preset Access (builtin + custom merged) ──

    def get_presets(self) -> List[Dict]:
        all_presets = list(BUILTIN_PRESETS)
        custom = self._load_custom_presets()
        all_presets.extend(custom)
        return all_presets

    def get_presets_by_category(self) -> Dict[str, List[Dict]]:
        categories = {}
        for preset in self.get_presets():
            cat = preset.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(preset)
        return categories

    def _lookup_preset(self, preset_id: str) -> Optional[Dict]:
        if preset_id in _BUILTIN_LOOKUP:
            return _BUILTIN_LOOKUP[preset_id]
        for p in self._load_custom_presets():
            if p["id"] == preset_id:
                return p
        return None

    # ── Custom Preset CRUD ──

    def _load_custom_presets(self) -> List[Dict]:
        if self._custom_presets is not None:
            return self._custom_presets

        self._custom_presets = []
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, name, icon, category, config_json, sort_order "
                    "FROM smart_find_presets WHERE project_id = ? ORDER BY sort_order, name",
                    (self.project_id,)
                )
                for row in cursor.fetchall():
                    config = json.loads(row['config_json'])
                    self._custom_presets.append({
                        "id": f"custom_{row['id']}",
                        "db_id": row['id'],
                        "name": row['name'],
                        "icon": row['icon'] or "\U0001f516",
                        "category": row['category'] or "custom",
                        "prompts": config.get("prompts", []),
                        "filters": config.get("filters", {}),
                        "threshold": config.get("threshold", 0.22),
                        "is_custom": True,
                    })
        except Exception as e:
            logger.warning(f"[SmartFind] Failed to load custom presets: {e}")

        return self._custom_presets

    def save_custom_preset(self, name: str, icon: str, prompts: List[str],
                           filters: Optional[Dict] = None,
                           threshold: float = 0.22,
                           category: str = "custom") -> Optional[int]:
        config = {
            "prompts": prompts,
            "filters": filters or {},
            "threshold": threshold,
        }
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO smart_find_presets "
                    "(project_id, name, icon, category, config_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self.project_id, name, icon, category, json.dumps(config))
                )
                conn.commit()
                new_id = cursor.lastrowid
                self._custom_presets = None
                self.invalidate_cache()
                logger.info(f"[SmartFind] Saved custom preset '{name}' (id={new_id})")
                return new_id
        except Exception as e:
            logger.error(f"[SmartFind] Failed to save preset: {e}")
            return None

    def update_custom_preset(self, db_id: int, name: str, icon: str,
                             prompts: List[str], filters: Optional[Dict] = None,
                             threshold: float = 0.22,
                             category: str = "custom") -> bool:
        config = {
            "prompts": prompts,
            "filters": filters or {},
            "threshold": threshold,
        }
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE smart_find_presets SET name=?, icon=?, category=?, "
                    "config_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND project_id=?",
                    (name, icon, category, json.dumps(config), db_id, self.project_id)
                )
                conn.commit()
                self._custom_presets = None
                self.invalidate_cache()
                logger.info(f"[SmartFind] Updated preset '{name}' (id={db_id})")
                return True
        except Exception as e:
            logger.error(f"[SmartFind] Failed to update preset: {e}")
            return False

    def delete_custom_preset(self, db_id: int) -> bool:
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                conn.execute(
                    "DELETE FROM smart_find_presets WHERE id=? AND project_id=?",
                    (db_id, self.project_id)
                )
                conn.commit()
                self._custom_presets = None
                self.invalidate_cache()
                logger.info(f"[SmartFind] Deleted preset id={db_id}")
                return True
        except Exception as e:
            logger.error(f"[SmartFind] Failed to delete preset: {e}")
            return False

    def save_current_search(self, name: str, icon: str,
                            preset_id: Optional[str] = None,
                            text_query: Optional[str] = None,
                            extra_filters: Optional[Dict] = None) -> Optional[int]:
        prompts = []
        filters = extra_filters.copy() if extra_filters else {}

        if preset_id:
            source = self._lookup_preset(preset_id)
            if source:
                prompts = list(source.get("prompts", []))
                base_filters = source.get("filters", {})
                merged = dict(base_filters)
                merged.update(filters)
                filters = merged
        elif text_query:
            prompts = [text_query]

        return self.save_custom_preset(name, icon, prompts, filters)

    # ── Orchestrator Access (unified pipeline) ──

    @property
    def orchestrator(self):
        """Get the SearchOrchestrator for this project (lazy)."""
        if not hasattr(self, '_orchestrator') or self._orchestrator is None:
            from services.search_orchestrator import get_search_orchestrator
            self._orchestrator = get_search_orchestrator(self.project_id)
        return self._orchestrator

    def _orchestrator_to_smartfind(self, orch_result) -> SmartFindResult:
        """Convert OrchestratorResult to SmartFindResult for backward compatibility."""
        matched_prompts = {}
        semantic_scores = {}
        metadata_scores = {}
        all_reasons = {}
        for sr in orch_result.scored_results:
            if sr.matched_prompt:
                matched_prompts[sr.path] = sr.matched_prompt
            semantic_scores[sr.path] = sr.clip_score
            metadata_scores[sr.path] = (
                sr.recency_score + sr.favorite_score
                + sr.location_score + sr.face_match_score
            )
            all_reasons[sr.path] = sr.reasons

        return SmartFindResult(
            paths=orch_result.paths,
            query_label=orch_result.label,
            total_matches=orch_result.total_matches,
            execution_time_ms=orch_result.execution_time_ms,
            scores=orch_result.scores if orch_result.scores else None,
            matched_prompts=matched_prompts if matched_prompts else None,
            semantic_scores=semantic_scores if semantic_scores else None,
            metadata_scores=metadata_scores if metadata_scores else None,
            reasons=all_reasons if all_reasons else None,
            backoff_applied=orch_result.backoff_applied,
        )

    # ── Core Search Pipeline (routed through SearchOrchestrator) ──

    def find_by_preset(self, preset_id: str,
                       top_k: Optional[int] = None,
                       threshold: Optional[float] = None,
                       extra_filters: Optional[Dict] = None) -> SmartFindResult:
        """Execute Smart Find using a preset with full pipeline."""
        cfg = self._get_config()
        if top_k is None:
            top_k = cfg["top_k"]
        if threshold is None:
            threshold = cfg["threshold"]

        token = self._begin_query()

        # Cache with index_version
        idx_v = _IndexVersionProvider.get(self.project_id)
        cache_key = f"preset:{preset_id}:{top_k}:{threshold}:{extra_filters}:{idx_v}"
        if cache_key in self._result_cache:
            cached = self._result_cache[cache_key]
            if (time.time() - cached._cached_at) < self._cache_ttl:
                return cached

        start = time.time()

        preset = self._lookup_preset(preset_id)
        if not preset:
            logger.warning(f"[SmartFind] Unknown preset: {preset_id}")
            return SmartFindResult(
                paths=[], query_label=preset_id,
                total_matches=0, execution_time_ms=0
            )

        # Route through orchestrator for unified ranking + explainability
        orch_result = self.orchestrator.search_by_preset(
            preset_id, top_k=top_k, extra_filters=extra_filters
        )

        if token.is_cancelled:
            return SmartFindResult(paths=[], query_label="(cancelled)",
                                  total_matches=0, execution_time_ms=0)

        result = self._orchestrator_to_smartfind(orch_result)
        result.request_id = token.request_id
        result.index_version = idx_v
        result._cached_at = time.time()

        self._result_cache[cache_key] = result

        prompts = preset.get("prompts", [])
        filters = dict(preset.get("filters", {}))
        logger.info(
            f"[SmartFind] Preset '{preset['name']}': {result.total_matches} results "
            f"in {result.execution_time_ms:.0f}ms (CLIP={bool(prompts and self.clip_available)}, "
            f"filters={bool(filters)}, backoff={result.backoff_applied})"
        )

        return result

    def find_by_text(self, query: str,
                     top_k: Optional[int] = None,
                     threshold: Optional[float] = None,
                     extra_filters: Optional[Dict] = None) -> SmartFindResult:
        """Free-text Smart Find with NLP parsing and hybrid pipeline."""
        cfg = self._get_config()
        if top_k is None:
            top_k = cfg["top_k"]
        if threshold is None:
            threshold = cfg["threshold"]

        token = self._begin_query()

        # Route through orchestrator for unified ranking + explainability
        orch_result = self.orchestrator.search(
            query, top_k=top_k, extra_filters=extra_filters
        )

        if token.is_cancelled:
            return SmartFindResult(paths=[], query_label="(cancelled)",
                                  total_matches=0, execution_time_ms=0)

        result = self._orchestrator_to_smartfind(orch_result)
        result.request_id = token.request_id

        return result

    def find_combined(self, preset_ids: List[str],
                      text_query: Optional[str] = None,
                      extra_filters: Optional[Dict] = None,
                      top_k: Optional[int] = None,
                      threshold: Optional[float] = None) -> SmartFindResult:
        """Combinable filters: stack multiple presets + text + metadata."""
        cfg = self._get_config()
        if top_k is None:
            top_k = cfg["top_k"]
        if threshold is None:
            threshold = cfg["threshold"]

        token = self._begin_query()

        # Route through orchestrator for unified ranking + explainability
        orch_result = self.orchestrator.search_combined(
            preset_ids, text_query=text_query,
            extra_filters=extra_filters, top_k=top_k
        )

        if token.is_cancelled:
            return SmartFindResult(paths=[], query_label="(cancelled)",
                                  total_matches=0, execution_time_ms=0)

        result = self._orchestrator_to_smartfind(orch_result)
        result.request_id = token.request_id

        return result

    # ── Core Hybrid Pipeline (audit design) ──

    def _execute_hybrid_search(
        self,
        prompts: List[str],
        filters: Dict,
        top_k: int,
        threshold: float,
        semantic_weight: float,
        fusion_mode: str,
        cfg: Dict,
        token: _CancelToken,
    ) -> SmartFindResult:
        """
        Hybrid search pipeline:
        1. Semantic retrieval (CLIP, multi-prompt)
        2. Metadata retrieval + graded scoring
        3. Score fusion with explainability
        4. Dynamic threshold backoff if 0 results
        """
        # ── Step 1: Semantic candidate retrieval ──
        # {photo_id: (best_score, matched_prompt)}
        semantic_hits: Dict[int, Tuple[float, str]] = {}

        if prompts and self.clip_available:
            semantic_hits = self._run_clip_multi_prompt(
                prompts, top_k * 3, threshold, fusion_mode
            )

        if token.is_cancelled:
            return SmartFindResult(paths=[], query_label="", total_matches=0, execution_time_ms=0)

        # ── Step 2: Metadata candidate pool + graded scoring ──
        # {path: (metadata_score, reasons[])}
        meta_evaluated: Dict[str, Tuple[float, List[str]]] = {}
        metadata_candidate_paths: Optional[set] = None

        if filters:
            metadata_paths = self._run_metadata_filter(filters)
            metadata_candidate_paths = set(metadata_paths)

        # ── Step 3: Resolve photo_id -> path and fuse scores ──
        path_lookup = {}  # photo_id -> path
        if semantic_hits:
            try:
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                photo_ids = list(semantic_hits.keys())
                placeholders = ','.join(['?'] * len(photo_ids))
                with db.get_connection() as conn:
                    cursor = conn.execute(
                        f"SELECT id, path FROM photo_metadata WHERE id IN ({placeholders})",
                        photo_ids
                    )
                    for row in cursor.fetchall():
                        path_lookup[row['id']] = row['path']
            except Exception as e:
                logger.error(f"[SmartFind] Failed to resolve photo paths: {e}")

        if token.is_cancelled:
            return SmartFindResult(paths=[], query_label="", total_matches=0, execution_time_ms=0)

        # Load per-path metadata for graded scoring
        project_meta = self._load_project_photo_metadata()

        # Fuse scores
        fused_results: List[Tuple[str, float, float, float, str, List[str]]] = []
        # Each: (path, final_score, sem_score, meta_score, matched_prompt, reasons)

        if semantic_hits:
            # Semantic-first: score each semantic hit
            for photo_id, (sem_score, matched_prompt) in semantic_hits.items():
                path = path_lookup.get(photo_id)
                if not path:
                    continue

                # If hard metadata filter exists, check membership
                if metadata_candidate_paths is not None and path not in metadata_candidate_paths:
                    continue  # Excluded by hard filter

                # Graded metadata scoring
                meta_score, reasons = self._evaluate_metadata_score(
                    path, filters, project_meta, cfg
                )

                # Final fusion: alpha * semantic + (1-alpha) * metadata
                alpha = semantic_weight
                final_score = alpha * sem_score + (1 - alpha) * meta_score

                reasons.insert(0, f"semantic: \"{matched_prompt}\" score={sem_score:.3f}")

                fused_results.append((
                    path, final_score, sem_score, meta_score, matched_prompt, reasons
                ))
        elif metadata_candidate_paths is not None:
            # Metadata-only search (Videos, Favorites, etc.)
            for path in metadata_candidate_paths:
                meta_score, reasons = self._evaluate_metadata_score(
                    path, filters, project_meta, cfg
                )
                fused_results.append((path, meta_score, 0.0, meta_score, None, reasons))

        # Apply "Not this" exclusions
        if self._excluded_paths:
            fused_results = [r for r in fused_results if r[0] not in self._excluded_paths]

        # Sort by final_score descending
        fused_results.sort(key=lambda r: r[1], reverse=True)

        # ── Step 4: Dynamic threshold backoff ──
        backoff_applied = False
        if (not fused_results and prompts and self.clip_available
                and cfg.get("backoff_enabled", True)):
            backoff_step = cfg.get("backoff_step", 0.04)
            max_retries = cfg.get("backoff_retries", 2)

            for retry in range(1, max_retries + 1):
                if token.is_cancelled:
                    break
                lowered = max(0.05, threshold - (backoff_step * retry))
                logger.info(
                    f"[SmartFind] Backoff retry {retry}: threshold {threshold:.2f} -> {lowered:.2f}"
                )
                retry_hits = self._run_clip_multi_prompt(
                    prompts, top_k * 3, lowered, fusion_mode
                )
                if retry_hits:
                    for photo_id, (sem_score, matched_prompt) in retry_hits.items():
                        path = path_lookup.get(photo_id)
                        if not path:
                            # Need to resolve new photo_ids
                            continue
                        if metadata_candidate_paths is not None and path not in metadata_candidate_paths:
                            continue
                        meta_score, reasons = self._evaluate_metadata_score(
                            path, filters, project_meta, cfg
                        )
                        alpha = semantic_weight
                        final_score = alpha * sem_score + (1 - alpha) * meta_score
                        reasons.insert(0, f"semantic: \"{matched_prompt}\" score={sem_score:.3f} (backoff)")
                        fused_results.append((
                            path, final_score, sem_score, meta_score, matched_prompt, reasons
                        ))

                    if self._excluded_paths:
                        fused_results = [r for r in fused_results if r[0] not in self._excluded_paths]
                    fused_results.sort(key=lambda r: r[1], reverse=True)
                    backoff_applied = True
                    break

        # Limit to top_k
        fused_results = fused_results[:top_k]

        # Build result with explainability
        paths = [r[0] for r in fused_results]
        scores = {r[0]: r[1] for r in fused_results}
        matched_prompts = {r[0]: r[4] for r in fused_results if r[4]}
        semantic_scores = {r[0]: r[2] for r in fused_results}
        metadata_scores = {r[0]: r[3] for r in fused_results}
        all_reasons = {r[0]: r[5] for r in fused_results}

        return SmartFindResult(
            paths=paths,
            query_label="",  # Set by caller
            total_matches=len(paths),
            execution_time_ms=0,  # Set by caller
            scores=scores if scores else None,
            matched_prompts=matched_prompts if matched_prompts else None,
            semantic_scores=semantic_scores if semantic_scores else None,
            metadata_scores=metadata_scores if metadata_scores else None,
            reasons=all_reasons if all_reasons else None,
            backoff_applied=backoff_applied,
        )

    # ── Multi-prompt CLIP Search with Fusion Modes ──

    def _run_clip_multi_prompt(
        self,
        prompts: List[str],
        top_k: int,
        threshold: float,
        fusion_mode: str,
    ) -> Dict[int, Tuple[float, str]]:
        """
        Multi-prompt CLIP search with fusion.

        Returns: {photo_id: (best_score, matched_prompt)}

        Fusion modes:
          max: S(i) = max_p s_i(p)
          weighted_max: S(i) = max_p(w_p * s_i(p)), main=1.0, synonyms=0.7
          soft_or: S(i) = 1 - prod_p(1 - clamp01(s_i(p)))
        """
        svc = self.semantic_service
        if not svc:
            return {}

        # Collect per-prompt scores: {photo_id: {prompt: score}}
        per_prompt: Dict[int, Dict[str, float]] = {}

        for idx, prompt in enumerate(prompts):
            # Check cancellation before each prompt to abandon stale searches
            with self._inflight_lock:
                token = self._inflight_token
            if token is not None and token.is_cancelled:
                logger.info(
                    f"[SmartFind] CLIP search cancelled before prompt {idx+1}/{len(prompts)}"
                )
                return {}

            try:
                results = svc.search(
                    query=prompt,
                    top_k=top_k,
                    threshold=threshold,
                    include_metadata=False
                )
                for r in results:
                    if r.photo_id not in per_prompt:
                        per_prompt[r.photo_id] = {}
                    per_prompt[r.photo_id][prompt] = r.relevance_score
            except Exception as e:
                logger.warning(f"[SmartFind] CLIP search failed for '{prompt}': {e}")

        if not per_prompt:
            return {}

        # Apply fusion
        result: Dict[int, Tuple[float, str]] = {}

        if fusion_mode == "soft_or":
            # S(i) = 1 - prod_p(1 - clamp(s_i(p)))
            for photo_id, prompt_scores in per_prompt.items():
                product = 1.0
                best_prompt = ""
                best_score = 0.0
                for prompt, score in prompt_scores.items():
                    clamped = max(0.0, min(1.0, score))
                    product *= (1.0 - clamped)
                    if score > best_score:
                        best_score = score
                        best_prompt = prompt
                fused = 1.0 - product
                result[photo_id] = (fused, best_prompt)

        elif fusion_mode == "weighted_max":
            # Main prompt (first) gets weight 1.0, synonyms 0.7
            main_prompt = prompts[0] if prompts else ""
            for photo_id, prompt_scores in per_prompt.items():
                best_weighted = 0.0
                best_prompt = ""
                for prompt, score in prompt_scores.items():
                    weight = 1.0 if prompt == main_prompt else 0.7
                    weighted = weight * score
                    if weighted > best_weighted:
                        best_weighted = weighted
                        best_prompt = prompt
                result[photo_id] = (best_weighted, best_prompt)

        else:  # "max" (default, most robust)
            for photo_id, prompt_scores in per_prompt.items():
                best_prompt = max(prompt_scores, key=prompt_scores.get)
                result[photo_id] = (prompt_scores[best_prompt], best_prompt)

        return result

    def _evaluate_metadata_score(
        self,
        path: str,
        filters: Dict,
        project_meta: Dict[str, Dict],
        cfg: Dict,
    ) -> Tuple[float, List[str]]:
        """
        Graded metadata scoring (not just pass/fail).

        Returns: (metadata_score, reasons)
        """
        score = 0.0
        reasons = []
        meta = project_meta.get(path, {})

        # GPS boost
        has_gps = meta.get("has_gps", False)
        if has_gps:
            score += cfg.get("meta_boost_gps", 0.05)
            reasons.append(f"GPS: +{cfg.get('meta_boost_gps', 0.05):.2f}")

        # Rating boost
        rating = meta.get("rating", 0) or 0
        if rating >= 4:
            boost = cfg.get("meta_boost_rating", 0.10)
            score += boost
            reasons.append(f"rating={rating}: +{boost:.2f}")
        elif rating >= 3:
            boost = cfg.get("meta_boost_rating", 0.10) * 0.5
            score += boost
            reasons.append(f"rating={rating}: +{boost:.2f}")

        # Date proximity boost (photos from recent dates get a small boost)
        created_date = meta.get("created_date")
        if created_date and "date_from" in filters:
            score += cfg.get("meta_boost_date", 0.03)
            reasons.append(f"date match: +{cfg.get('meta_boost_date', 0.03):.2f}")

        # Clamp to [0, 1]
        score = max(0.0, min(1.0, score))

        return score, reasons

    def _load_project_photo_metadata(self) -> Dict[str, Dict]:
        """Load lightweight metadata for all project photos (for graded scoring)."""
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT path, rating, gps_latitude, gps_longitude, created_date "
                    "FROM photo_metadata WHERE project_id = ?",
                    (self.project_id,)
                )
                result = {}
                for row in cursor.fetchall():
                    path = row['path']
                    lat = row['gps_latitude']
                    lon = row['gps_longitude']
                    result[path] = {
                        "rating": row['rating'],
                        "has_gps": lat is not None and lon is not None and lat != 0 and lon != 0,
                        "created_date": row['created_date'],
                    }
                return result
        except Exception as e:
            logger.warning(f"[SmartFind] Failed to load project metadata: {e}")
            return {}

    # ── "Not This" Exclusion ──

    def exclude_path(self, path: str):
        self._excluded_paths.add(path)
        self.invalidate_cache()
        logger.info(f"[SmartFind] Excluded: {path}")

    def clear_exclusions(self):
        self._excluded_paths.clear()
        self.invalidate_cache()

    # ── Search Suggestions ──

    def get_suggestions(self) -> List[Dict]:
        suggestions = []
        for preset in BUILTIN_PRESETS:
            prompts = preset.get("prompts", [])
            filters = preset.get("filters", {})
            has_content = bool(prompts) or bool(filters)
            if has_content:
                suggestions.append({
                    "id": preset["id"],
                    "name": preset["name"],
                    "icon": preset.get("icon", ""),
                    "category": preset.get("category", "other"),
                })
        return suggestions

    # ── Internal Helpers ──

    def _resolve_relative_date(self, relative: str) -> Dict:
        from datetime import datetime, timedelta
        today = datetime.now().date()
        filters = {}

        rel = relative.replace(' ', '_').lower()
        if rel == "today":
            filters["date_from"] = today.strftime("%Y-%m-%d")
            filters["date_to"] = today.strftime("%Y-%m-%d")
        elif rel == "yesterday":
            yesterday = today - timedelta(days=1)
            filters["date_from"] = yesterday.strftime("%Y-%m-%d")
            filters["date_to"] = yesterday.strftime("%Y-%m-%d")
        elif rel == "this_week":
            start = today - timedelta(days=today.weekday())
            filters["date_from"] = start.strftime("%Y-%m-%d")
            filters["date_to"] = today.strftime("%Y-%m-%d")
        elif rel == "last_week":
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
            filters["date_from"] = start.strftime("%Y-%m-%d")
            filters["date_to"] = end.strftime("%Y-%m-%d")
        elif rel == "this_month":
            filters["date_from"] = today.replace(day=1).strftime("%Y-%m-%d")
            filters["date_to"] = today.strftime("%Y-%m-%d")
        elif rel == "last_month":
            first_of_month = today.replace(day=1)
            last_month_end = first_of_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            filters["date_from"] = last_month_start.strftime("%Y-%m-%d")
            filters["date_to"] = last_month_end.strftime("%Y-%m-%d")
        elif rel == "this_year":
            filters["date_from"] = f"{today.year}-01-01"
            filters["date_to"] = today.strftime("%Y-%m-%d")
        elif rel == "last_year":
            filters["date_from"] = f"{today.year - 1}-01-01"
            filters["date_to"] = f"{today.year - 1}-12-31"

        return filters

    def _run_metadata_filter(self, filters: Dict) -> List[str]:
        """Apply metadata filters using existing SearchService."""
        from services.search_service import SearchCriteria

        criteria = SearchCriteria()

        if "has_gps" in filters:
            criteria.has_gps = filters["has_gps"]

        if "orientation" in filters:
            criteria.orientation = filters["orientation"]

        if "date_from" in filters:
            criteria.date_from = filters["date_from"]

        if "date_to" in filters:
            criteria.date_to = filters["date_to"]

        if "width_min" in filters:
            criteria.width_min = filters["width_min"]

        if "media_type" in filters:
            media_type = filters["media_type"]
            if media_type == "video":
                criteria.path_contains = None

        if "rating_min" in filters:
            pass  # Handled separately below

        result = self.search_service.search(criteria)
        paths = result.paths

        # Filter to current project
        if paths:
            try:
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                with db.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT path FROM photo_metadata WHERE project_id = ?",
                        (self.project_id,)
                    )
                    project_paths = {row['path'] for row in cursor.fetchall()}
                paths = [p for p in paths if p in project_paths]
            except Exception as e:
                logger.warning(f"[SmartFind] Project filtering failed: {e}")

        # Media type filtering
        if "media_type" in filters:
            media_type = filters["media_type"]
            if media_type == "video":
                video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.webm', '.m4v', '.flv'}
                paths = [p for p in paths if any(p.lower().endswith(ext) for ext in video_exts)]
            elif media_type == "photo":
                video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.webm', '.m4v', '.flv'}
                paths = [p for p in paths if not any(p.lower().endswith(ext) for ext in video_exts)]

        # Rating filtering
        if "rating_min" in filters:
            rating_min = filters["rating_min"]
            try:
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                with db.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT path FROM photo_metadata WHERE rating >= ? AND project_id = ?",
                        (rating_min, self.project_id)
                    )
                    rated_paths = {row['path'] for row in cursor.fetchall()}
                if paths:
                    paths = [p for p in paths if p in rated_paths]
                else:
                    paths = list(rated_paths)
            except Exception as e:
                logger.warning(f"[SmartFind] Rating filter failed: {e}")

        # OCR text filtering (has:text)
        if "has_ocr_text" in filters:
            try:
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                with db.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT path FROM photo_metadata "
                        "WHERE ocr_text IS NOT NULL AND ocr_text != '' "
                        "AND project_id = ?",
                        (self.project_id,)
                    )
                    ocr_paths = {row['path'] for row in cursor.fetchall()}
                if paths:
                    paths = [p for p in paths if p in ocr_paths]
                else:
                    paths = list(ocr_paths)
            except Exception as e:
                logger.warning(f"[SmartFind] OCR text filter failed: {e}")

        # Flag filtering (Favorites = flag='pick')
        if "flag" in filters:
            flag_value = filters["flag"]
            try:
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                with db.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT path FROM photo_metadata WHERE flag = ? AND project_id = ?",
                        (flag_value, self.project_id)
                    )
                    flagged_paths = {row['path'] for row in cursor.fetchall()}
                if paths:
                    paths = [p for p in paths if p in flagged_paths]
                else:
                    paths = list(flagged_paths)
            except Exception as e:
                logger.warning(f"[SmartFind] Flag filter failed: {e}")

        return paths

    def invalidate_cache(self):
        """Clear result cache and bump index version."""
        self._result_cache.clear()
        _IndexVersionProvider.bump("cache_invalidated")
        logger.info("[SmartFind] Cache invalidated")


# Per-project service cache
_smart_find_services: Dict[int, SmartFindService] = {}


def get_smart_find_service(project_id: int) -> SmartFindService:
    """Get or create SmartFindService for a project."""
    if project_id not in _smart_find_services:
        _smart_find_services[project_id] = SmartFindService(project_id)
    return _smart_find_services[project_id]


def bump_index_version(reason: str = ""):
    """Bump the global index version (call after scans, embedding updates, etc.)."""
    _IndexVersionProvider.bump(reason)
