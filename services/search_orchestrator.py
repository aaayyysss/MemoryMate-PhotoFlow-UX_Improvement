# services/search_orchestrator.py
# Unified Search Orchestrator - One search pipeline, one mental model
#
# This is the "one system" that Google Photos, Apple Photos, Lightroom,
# and Excire all converge on: a single entry point that understands
# free text, structured tokens, semantic similarity, metadata filters,
# and produces ranked results with explainability.
#
# All search entry points (SmartFind presets, toolbar widget, free text)
# route through this orchestrator so ranking is always consistent.

"""
SearchOrchestrator - Unified search pipeline.

Architecture:
1. QueryParser: Parse user input into a QueryPlan
   - Structured tokens (type:video, is:fav, has:location, date:2024, camera:iPhone)
   - Natural language tokens (dates, ratings via NLQueryParser)
   - Everything else becomes semantic text for CLIP

2. CandidateRetrieval: Get initial candidate sets
   - Semantic: multi-prompt CLIP search (via SmartFindService internals)
   - Metadata: SQL filter constraints

3. ScoringContract: Deterministic, explainable ranking
   - S = w_clip * clip_sim + w_recency * recency_boost + w_fav * is_favorite
       + w_face * face_match + w_loc * has_location + w_quality * aesthetic
   - All weights configurable, all components logged

4. FacetComputer: Compute result-set facets for chip display
   - Media type distribution, date buckets, people, locations
   - Only from current result set (not global)

5. ExplainabilityLogger: Top-10 score breakdown per query

Usage:
    from services.search_orchestrator import get_search_orchestrator

    orch = get_search_orchestrator(project_id=1)
    result = orch.search("wedding Munich 2023 screenshots")
    # result.facets -> {media: {photo: 5, video: 2}, years: {2023: 7}, ...}
    # result.explanations -> [{path, clip: 0.35, recency: 0.02, ...}, ...]
"""

import os
import re
import time
import threading
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field
from logging_config import get_logger

# ── Extracted modules (Phase 1 decomposition) ──
from services.gate_engine import GateEngine
from services.ranker import (
    Ranker, ScoringWeights, ScoredResult,
    get_preset_family, get_weights_for_family, is_people_implied,
    PRESET_FAMILIES,
    SCENIC_ANTI_TYPE_PENALTIES, SCENIC_OCR_TEXT_PENALTY_THRESHOLD,
)
from services.deduplicator import Deduplicator, is_copy_filename
# Phase 4 imports: Family-first hybrid retrieval
from services.query_intent_planner import QueryIntentPlanner, QueryIntent, get_query_intent_planner
from services.candidate_builders import CANDIDATE_BUILDERS, PRESET_BUILDERS, CandidateSet
from services.search_confidence_policy import SearchConfidencePolicy, SearchDecision
# Phase 3 imports (lazy - actual classes loaded on first use)
# from services.person_search_service import PersonSearchService
# from services.entity_graph import get_entity_graph
# from services.suggestion_service import get_suggestion_service
# from services.query_intent_service import get_query_intent_service

logger = get_logger(__name__)

# Optional FAISS import for ANN retrieval
try:
    import numpy as np
    _numpy_available = True
except ImportError:
    _numpy_available = False

try:
    import faiss as _faiss
    _faiss_available = True
except ImportError:
    _faiss_available = False


# ══════════════════════════════════════════════════════════════════════
# Query Plan - structured representation of parsed user intent
# ══════════════════════════════════════════════════════════════════════

@dataclass
class QueryPlan:
    """Structured representation of a parsed search query."""
    # Raw input
    raw_query: str = ""

    # Semantic text for CLIP (after token extraction)
    semantic_text: str = ""

    # Multi-prompt list (from presets or expanded synonyms)
    semantic_prompts: List[str] = field(default_factory=list)

    # Extracted structured filters
    filters: Dict[str, Any] = field(default_factory=dict)

    # Source: "text", "preset", "combined"
    source: str = "text"

    # Preset ID if from a preset
    preset_id: Optional[str] = None

    # Weights override (per-preset or default)
    semantic_weight: float = 0.8

    # Per-preset backoff policy (precision-first presets set False)
    allow_backoff: bool = True

    # Per-preset CLIP threshold override (None = use global default)
    threshold_override: Optional[float] = None

    # Negative prompts for penalty scoring (e.g. Documents excludes screenshots)
    negative_prompts: List[str] = field(default_factory=list)

    # Presets that should exclude photos containing faces (e.g. Documents)
    exclude_faces: bool = False

    # ── Gate Profile fields (hard pre-filters applied before scoring) ──
    # Screenshots preset: keep only photos flagged as screenshots
    require_screenshot: bool = False
    # Documents preset: drop screenshots from results
    exclude_screenshots: bool = False
    # People-centric presets (Wedding, Party): require face presence
    require_faces: bool = False
    # Minimum face count (e.g. group presets need >= 2)
    min_face_count: int = 0
    # Location-dependent presets: hard-require GPS
    require_gps_gate: bool = False
    # Documents: drop tiny images (icons, thumbnails) below this edge size
    min_edge_size: int = 0

    # Documents: require at least one positive document signal
    # (OCR text, document extension, page-like structure)
    require_document_signal: bool = False

    # Tokens that were extracted (for chip display)
    extracted_tokens: List[Dict[str, str]] = field(default_factory=list)

    def has_semantic(self) -> bool:
        return bool(self.semantic_text) or bool(self.semantic_prompts)

    def has_filters(self) -> bool:
        return bool(self.filters)


# ══════════════════════════════════════════════════════════════════════
# Scored Result - imported from services.ranker
# ══════════════════════════════════════════════════════════════════════
# ScoredResult is now defined in services/ranker.py and imported above.


# ══════════════════════════════════════════════════════════════════════
# Search Result - full orchestrator output
# ══════════════════════════════════════════════════════════════════════

@dataclass
class OrchestratorResult:
    """Complete search result with facets and explanations."""
    # Ranked paths
    paths: List[str] = field(default_factory=list)
    total_matches: int = 0

    # Full scored results (for explainability)
    scored_results: List[ScoredResult] = field(default_factory=list)

    # Score lookup
    scores: Dict[str, float] = field(default_factory=dict)

    # Facets computed from result set
    facets: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Query plan used
    query_plan: Optional[QueryPlan] = None

    # Performance
    execution_time_ms: float = 0.0
    label: str = ""

    # Backoff
    backoff_applied: bool = False

    # Progressive search phase: "metadata" (fast) or "full" (complete)
    phase: str = "full"

    # Phase label for UI transparency (e.g. "Metadata results", "Semantic refined")
    phase_label: str = ""

    # Duplicate stacking stats
    stacked_duplicates: int = 0  # how many results were folded into stacks

    # Phase 4: Confidence policy decision
    family: str = ""  # The retrieval family used for this search
    confidence_label: str = ""  # high, medium, low, not_ready, empty
    confidence_warning: str = ""  # user-facing warning when confidence is low


# ══════════════════════════════════════════════════════════════════════
# Scoring Weights - imported from services.ranker
# ══════════════════════════════════════════════════════════════════════
# ScoringWeights is now defined in services/ranker.py and imported above.


# ══════════════════════════════════════════════════════════════════════
# Token Parser - structured token extraction
# ══════════════════════════════════════════════════════════════════════

class TokenParser:
    """
    Parse structured tokens from search queries.

    Supports:
        type:video, type:photo, type:screenshot
        is:fav, is:favorite, is:starred
        has:location, has:gps, has:faces
        date:2024, date:2024-06, date:last_month, date:this_year
        camera:iPhone, camera:Canon
        ext:heic, ext:jpg, ext:png
        rating:4, rating:5
        person:face_001 (for face-filtered search)

    Everything else is passed to CLIP as semantic text.
    """

    # Token patterns: key:value (no spaces in value)
    _TOKEN_PATTERN = re.compile(
        r'\b(type|is|has|date|camera|ext|rating|person|in|from)'
        r':(\S+)',
        re.IGNORECASE
    )

    # Also handle natural language via NLQueryParser
    _NL_PARSER = None

    @classmethod
    def _get_nl_parser(cls):
        if cls._NL_PARSER is None:
            from services.smart_find_service import NLQueryParser
            cls._NL_PARSER = NLQueryParser
        return cls._NL_PARSER

    @classmethod
    def parse(cls, raw_query: str) -> QueryPlan:
        """
        Parse a raw query string into a QueryPlan.

        Examples:
            "beach type:photo date:2024" ->
                semantic_text="beach", filters={media_type: photo, date_from: 2024-01-01}
            "wedding Munich is:fav" ->
                semantic_text="wedding Munich", filters={rating_min: 4}
            "sunset" ->
                semantic_text="sunset", filters={}
        """
        plan = QueryPlan(raw_query=raw_query, source="text")
        remaining = raw_query.strip()
        filters = {}
        tokens = []

        # Extract structured tokens
        for match in cls._TOKEN_PATTERN.finditer(remaining):
            key = match.group(1).lower()
            value = match.group(2).strip()
            token_info = cls._process_token(key, value, filters)
            if token_info:
                tokens.append(token_info)

        # Remove extracted tokens from remaining text
        remaining = cls._TOKEN_PATTERN.sub('', remaining).strip()
        remaining = re.sub(r'\s+', ' ', remaining).strip()

        # Run NL parser on remaining text for date/rating/media extraction
        nl_parser = cls._get_nl_parser()
        nl_remaining, nl_filters = nl_parser.parse(remaining)

        # Merge NL filters (structured tokens take priority)
        for k, v in nl_filters.items():
            if k not in filters:
                filters[k] = v
                if k == "date_from":
                    tokens.append({"type": "date", "label": f"Date: {v}", "key": k, "value": v})
                elif k == "rating_min":
                    tokens.append({"type": "rating", "label": f"Rating >= {v}", "key": k, "value": str(v)})
                elif k == "media_type":
                    tokens.append({"type": "type", "label": v.title(), "key": k, "value": v})

        # Resolve relative dates
        if "_relative_date" in filters:
            rel = filters.pop("_relative_date")
            date_filters = cls._resolve_relative_date(rel)
            filters.update(date_filters)
            tokens.append({"type": "date", "label": rel.replace('_', ' ').title(), "key": "date", "value": rel})

        plan.semantic_text = nl_remaining
        plan.semantic_prompts = [nl_remaining] if nl_remaining else []
        plan.filters = filters
        plan.extracted_tokens = tokens

        return plan

    @classmethod
    def _process_token(cls, key: str, value: str, filters: Dict) -> Optional[Dict]:
        """Process a single structured token."""
        value_lower = value.lower()

        if key == "type":
            if value_lower in ("video", "videos"):
                filters["media_type"] = "video"
                return {"type": "type", "label": "Videos", "key": "media_type", "value": "video"}
            elif value_lower in ("photo", "photos", "image", "images"):
                filters["media_type"] = "photo"
                return {"type": "type", "label": "Photos", "key": "media_type", "value": "photo"}
            elif value_lower in ("screenshot", "screenshots"):
                filters["media_type"] = "photo"
                filters["_is_screenshot"] = True
                return {"type": "type", "label": "Screenshots", "key": "media_type", "value": "screenshot"}

        elif key == "is":
            if value_lower in ("fav", "favorite", "favourite", "starred"):
                filters["flag"] = "pick"
                return {"type": "quality", "label": "Favorites", "key": "flag", "value": "pick"}

        elif key == "has":
            if value_lower in ("location", "gps", "geo"):
                filters["has_gps"] = True
                return {"type": "meta", "label": "Has Location", "key": "has_gps", "value": "true"}
            elif value_lower in ("face", "faces", "people"):
                filters["has_faces"] = True
                return {"type": "meta", "label": "Has Faces", "key": "has_faces", "value": "true"}
            elif value_lower in ("text", "ocr", "words"):
                filters["has_ocr_text"] = True
                return {"type": "meta", "label": "Has Text", "key": "has_ocr_text", "value": "true"}

        elif key in ("date", "in", "from"):
            return cls._process_date_token(value, filters)

        elif key == "ext":
            ext = value_lower if value_lower.startswith('.') else f".{value_lower}"
            filters["extension"] = ext
            return {"type": "ext", "label": f"Format: {ext}", "key": "extension", "value": ext}

        elif key == "rating":
            try:
                rating = int(value)
                if 1 <= rating <= 5:
                    filters["rating_min"] = rating
                    return {"type": "quality", "label": f"Rating >= {rating}", "key": "rating_min", "value": str(rating)}
            except ValueError:
                pass

        elif key == "person":
            filters["person_id"] = value
            return {"type": "person", "label": f"Person: {value}", "key": "person_id", "value": value}

        return None

    @classmethod
    def _process_date_token(cls, value: str, filters: Dict) -> Optional[Dict]:
        """Parse date token values."""
        value_lower = value.lower().replace('_', ' ')

        # Relative dates
        relative_map = {
            "today": "today", "yesterday": "yesterday",
            "this week": "this_week", "last week": "last_week",
            "thisweek": "this_week", "lastweek": "last_week",
            "this month": "this_month", "last month": "last_month",
            "thismonth": "this_month", "lastmonth": "last_month",
            "this year": "this_year", "last year": "last_year",
            "thisyear": "this_year", "lastyear": "last_year",
        }
        if value_lower in relative_map:
            filters["_relative_date"] = relative_map[value_lower]
            return None  # Will be processed after

        # Year: date:2024
        year_match = re.match(r'^(\d{4})$', value)
        if year_match:
            year = int(year_match.group(1))
            if 1990 <= year <= 2099:
                filters["date_from"] = f"{year}-01-01"
                filters["date_to"] = f"{year}-12-31"
                return {"type": "date", "label": str(year), "key": "date", "value": value}

        # Year-Month: date:2024-06
        ym_match = re.match(r'^(\d{4})-(\d{1,2})$', value)
        if ym_match:
            year = int(ym_match.group(1))
            month = int(ym_match.group(2))
            if 1990 <= year <= 2099 and 1 <= month <= 12:
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                filters["date_from"] = f"{year}-{month:02d}-01"
                filters["date_to"] = f"{year}-{month:02d}-{last_day:02d}"
                return {"type": "date", "label": f"{year}-{month:02d}", "key": "date", "value": value}

        # Full date: date:2024-06-15
        date_match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', value)
        if date_match:
            filters["date_from"] = value
            filters["date_to"] = value
            return {"type": "date", "label": value, "key": "date", "value": value}

        return None

    @classmethod
    def _resolve_relative_date(cls, relative: str) -> Dict:
        """Resolve relative date tokens to absolute date ranges."""
        today = datetime.now().date()
        filters = {}
        rel = relative.replace(' ', '_').lower()

        if rel == "today":
            filters["date_from"] = today.strftime("%Y-%m-%d")
            filters["date_to"] = today.strftime("%Y-%m-%d")
        elif rel == "yesterday":
            d = today - timedelta(days=1)
            filters["date_from"] = d.strftime("%Y-%m-%d")
            filters["date_to"] = d.strftime("%Y-%m-%d")
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
            first = today.replace(day=1)
            end = first - timedelta(days=1)
            start = end.replace(day=1)
            filters["date_from"] = start.strftime("%Y-%m-%d")
            filters["date_to"] = end.strftime("%Y-%m-%d")
        elif rel == "this_year":
            filters["date_from"] = f"{today.year}-01-01"
            filters["date_to"] = today.strftime("%Y-%m-%d")
        elif rel == "last_year":
            filters["date_from"] = f"{today.year - 1}-01-01"
            filters["date_to"] = f"{today.year - 1}-12-31"

        return filters


# ══════════════════════════════════════════════════════════════════════
# Facet Computer - compute chips from result set
# ══════════════════════════════════════════════════════════════════════

class FacetComputer:
    """
    Compute facets/chips from the current result set.

    Only shows what's actually present in results, not global.
    This is the Google Photos / Lightroom filter bar concept.

    Hygiene rules:
    - Facets suppressed entirely for small result sets (< _MIN_RESULTS_FOR_FACETS)
    - Each facet must have 2+ meaningful buckets with _MIN_BUCKET_SIZE items each
    - 90/10 splits are hidden (the minority bucket must hold >= 10% of results)
    - Facets are ordered by entropy (most discriminating first)
    """

    # Video extensions for media type detection
    _VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.webm', '.m4v', '.flv'})
    _MIN_BUCKET_SIZE = 2  # Minimum items per bucket to show
    _MIN_RESULTS_FOR_FACETS = 8  # Don't show facets below this result count

    @classmethod
    def compute(cls, paths: List[str], project_meta: Dict[str, Dict]) -> Dict[str, Dict[str, int]]:
        """
        Compute facets from a result set.

        Returns:
            {
                "media": {"Photos": 15, "Videos": 3},
                "years": {"2024": 10, "2023": 5, "2025": 3},
                "has_location": {"Yes": 5, "No": 13},
                "rated": {"Rated": 4, "Unrated": 14},
            }
        """
        if not paths or len(paths) < cls._MIN_RESULTS_FOR_FACETS:
            return {}

        facets = {}

        # Media type
        media_counts = {"Photos": 0, "Videos": 0}
        for p in paths:
            ext = '.' + p.rsplit('.', 1)[-1].lower() if '.' in p else ''
            if ext in cls._VIDEO_EXTS:
                media_counts["Videos"] += 1
            else:
                media_counts["Photos"] += 1
        # Only include if there's a mix
        if media_counts["Videos"] > 0 and media_counts["Photos"] > 0:
            facets["media"] = media_counts
        elif media_counts["Videos"] > 0:
            facets["media"] = {"Videos": media_counts["Videos"]}

        # Year distribution
        year_counts = {}
        for p in paths:
            meta = project_meta.get(p, {})
            created = meta.get("created_date") or meta.get("date_taken", "")
            if created and len(str(created)) >= 4:
                year = str(created)[:4]
                if year.isdigit():
                    year_counts[year] = year_counts.get(year, 0) + 1
        if len(year_counts) > 1:
            # Sort descending
            facets["years"] = dict(sorted(year_counts.items(), reverse=True))

        # Location
        loc_yes = 0
        loc_no = 0
        for p in paths:
            meta = project_meta.get(p, {})
            if meta.get("has_gps"):
                loc_yes += 1
            else:
                loc_no += 1
        if loc_yes > 0 and loc_no > 0:
            facets["location"] = {"With Location": loc_yes, "No Location": loc_no}

        # Rating
        rated = 0
        unrated = 0
        for p in paths:
            meta = project_meta.get(p, {})
            rating = meta.get("rating", 0) or 0
            if rating >= 1:
                rated += 1
            else:
                unrated += 1
        if rated > 0 and unrated > 0:
            facets["rated"] = {"Rated": rated, "Unrated": unrated}

        # Hygiene: prune small buckets, drop single-bucket facets, order by entropy
        return cls._apply_hygiene(facets)

    @classmethod
    def _apply_hygiene(cls, facets: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
        """Prune small buckets, drop degenerate/lopsided facets, order by entropy."""
        cleaned = {}
        for name, buckets in facets.items():
            # Prune buckets below minimum size
            pruned = {k: v for k, v in buckets.items() if v >= cls._MIN_BUCKET_SIZE}
            # Need at least 2 buckets to be a meaningful facet
            if len(pruned) < 2:
                continue
            # Drop lopsided splits where the smallest bucket < 10% of total
            total = sum(pruned.values())
            if total > 0 and min(pruned.values()) / total < 0.10:
                continue
            cleaned[name] = pruned

        # Order by entropy (most discriminating first)
        def _entropy(counts: Dict[str, int]) -> float:
            total = sum(counts.values())
            if total == 0:
                return 0.0
            return -sum(
                (c / total) * math.log2(c / total)
                for c in counts.values() if c > 0
            )

        ordered = sorted(cleaned.items(), key=lambda kv: _entropy(kv[1]), reverse=True)
        return dict(ordered)


# ══════════════════════════════════════════════════════════════════════
# Search Orchestrator - the unified pipeline
# ══════════════════════════════════════════════════════════════════════

class SearchOrchestrator:
    """
    Unified search pipeline.

    All search entry points route through here:
    - SmartFind presets
    - Toolbar semantic search widget
    - Free-text queries
    - Combined preset + text + filter queries

    Guarantees: same query always produces same ranking,
    regardless of entry point.
    """

    # People-implied detection delegated to services.ranker module

    # Known embedding model quality tiers (dim → tier label)
    _MODEL_QUALITY_TIERS = {
        512: "base (ViT-B/32, 512-D)",
        768: "large (ViT-L/14, 768-D)",
    }

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._weights = ScoringWeights()
        self._weights.validate()
        self._smart_find_service = None  # Lazy
        self._project_meta_cache: Optional[Dict[str, Dict]] = None
        self._meta_cache_time: float = 0.0
        self._META_CACHE_TTL = 60.0  # Refresh metadata cache every 60s
        self._embedding_quality_logged = False
        # ── Extracted modules (Phase 1 decomposition) ──
        self._gate_engine = GateEngine()
        self._ranker = Ranker()
        self._deduplicator = Deduplicator(project_id)
        self._search_feature_repo = None  # Lazy
        # ── Phase 4: Family-first hybrid retrieval ──
        self._intent_planner = None  # Lazy
        self._confidence_policy = SearchConfidencePolicy()
        # ── Phase 3: Person search, entity graph, suggestions, intent ──
        self._person_search = None   # Lazy
        self._entity_graph = None    # Lazy
        self._suggestion_svc = None  # Lazy
        self._intent_svc = None      # Lazy

    # ── Lazy Service Access ──

    @property
    def _person_search_svc(self):
        if self._person_search is None:
            try:
                from services.person_search_service import PersonSearchService
                self._person_search = PersonSearchService(self.project_id)
            except Exception:
                pass
        return self._person_search

    @property
    def _entity_graph_svc(self):
        if self._entity_graph is None:
            try:
                from services.entity_graph import get_entity_graph
                self._entity_graph = get_entity_graph(self.project_id)
            except Exception:
                pass
        return self._entity_graph

    @property
    def _suggestion_service(self):
        if self._suggestion_svc is None:
            try:
                from services.suggestion_service import get_suggestion_service
                self._suggestion_svc = get_suggestion_service(self.project_id)
            except Exception:
                pass
        return self._suggestion_svc

    @property
    def _query_intent_planner_svc(self):
        if self._intent_planner is None:
            try:
                self._intent_planner = get_query_intent_planner(self.project_id)
            except Exception:
                pass
        return self._intent_planner

    @property
    def _query_intent_svc(self):
        if self._intent_svc is None:
            try:
                from services.query_intent_service import get_query_intent_service
                self._intent_svc = get_query_intent_service(self.project_id)
            except Exception:
                pass
        return self._intent_svc

    @property
    def _smart_find(self):
        if self._smart_find_service is None:
            from services.smart_find_service import get_smart_find_service
            self._smart_find_service = get_smart_find_service(self.project_id)
        return self._smart_find_service

    # ── Public API ──

    def search(self, query: str, top_k: int = 200,
               extra_filters: Optional[Dict] = None) -> OrchestratorResult:
        """
        Unified search from free text.

        Handles: "wedding Munich 2023 screenshots is:fav type:photo"
        Also handles Phase 3 advanced intent decomposition:
        - "best sunset photos from last summer"
        - "photos of John and Sarah at the beach"
        - "Christmas 2024 without screenshots"
        """
        start = time.time()
        plan = TokenParser.parse(query)
        if extra_filters:
            plan.filters.update(extra_filters)

        # Phase 3: Advanced intent decomposition for richer NL queries.
        # Only apply when TokenParser didn't extract structured filters,
        # meaning the query is mostly free text.
        intent_svc = self._query_intent_svc
        if intent_svc and not plan.has_filters():
            try:
                intent = intent_svc.decompose(query)
                overrides = intent.to_query_plan_overrides()
                if overrides:
                    # Merge intent-derived filters
                    if "filters" in overrides:
                        plan.filters.update(overrides["filters"])
                    if "semantic_text" in overrides and overrides["semantic_text"]:
                        plan.semantic_text = overrides["semantic_text"]
                        plan.semantic_prompts = [overrides["semantic_text"]]
                    if overrides.get("exclude_faces"):
                        plan.exclude_faces = True
                    if overrides.get("exclude_screenshots"):
                        plan.exclude_screenshots = True
                    if overrides.get("require_faces"):
                        plan.require_faces = True
                    logger.info(
                        f"[SearchOrchestrator] Intent decomposition applied: "
                        f"intents={intent.intents_found}"
                    )
            except Exception as e:
                logger.debug(f"[SearchOrchestrator] Intent decomposition skipped: {e}")

        # Phase 3: Person cluster integration.
        # Resolve person_id filter to photo paths using PersonSearchService.
        plan = self._resolve_person_filters(plan)

        result = self._execute(plan, top_k)
        result.execution_time_ms = (time.time() - start) * 1000
        result.label = self._build_label(plan, result)

        # Phase 3: Enrich facets with person facet
        self._enrich_person_facet(result)

        self._log_explainability(plan, result)
        return result

    def search_by_preset(self, preset_id: str, top_k: int = 200,
                         extra_filters: Optional[Dict] = None) -> OrchestratorResult:
        """
        Search using a SmartFind preset. Routes through the same pipeline.
        """
        start = time.time()
        plan = self._plan_from_preset(preset_id, extra_filters)

        result = self._execute(plan, top_k)
        result.execution_time_ms = (time.time() - start) * 1000
        result.label = self._build_label(plan, result)

        self._log_explainability(plan, result)
        return result

    def search_combined(self, preset_ids: List[str],
                        text_query: Optional[str] = None,
                        extra_filters: Optional[Dict] = None,
                        top_k: int = 200) -> OrchestratorResult:
        """
        Combined search: multiple presets + text + filters.
        """
        start = time.time()
        plan = QueryPlan(raw_query=text_query or "", source="combined")

        for pid in preset_ids:
            preset = self._smart_find._lookup_preset(pid)
            if preset:
                plan.semantic_prompts.extend(preset.get("prompts", []))
                for k, v in preset.get("filters", {}).items():
                    plan.filters[k] = v

        if text_query:
            sub_plan = TokenParser.parse(text_query)
            if sub_plan.semantic_text:
                plan.semantic_prompts.append(sub_plan.semantic_text)
            plan.filters.update(sub_plan.filters)
            plan.extracted_tokens.extend(sub_plan.extracted_tokens)

        if extra_filters:
            plan.filters.update(extra_filters)

        result = self._execute(plan, top_k)
        result.execution_time_ms = (time.time() - start) * 1000
        result.label = self._build_label(plan, result)

        self._log_explainability(plan, result)
        return result

    # ── Phase 3: Person Cluster Integration ──

    def _resolve_person_filters(self, plan: QueryPlan) -> QueryPlan:
        """
        Resolve person_id or person_ids filters to photo paths.

        When the user searches for a person (via structured token or NL),
        this resolves the person's branch_key to actual photo paths and
        injects them as metadata candidates.
        """
        person_id = plan.filters.get("person_id")
        person_ids = plan.filters.get("person_ids")
        person_mode = plan.filters.pop("person_mode", "any")

        if not person_id and not person_ids:
            return plan

        pss = self._person_search_svc
        if not pss:
            return plan

        try:
            if person_id:
                # Single person: resolve name if not a branch_key
                branch_keys = pss.resolve_person_name(person_id)
                if not branch_keys:
                    branch_keys = [person_id]  # Try raw as branch_key
                person_paths = pss.get_person_photo_paths_multi(branch_keys)
            elif person_ids:
                # Multi-person
                all_keys = []
                for pid in person_ids:
                    keys = pss.resolve_person_name(pid)
                    all_keys.extend(keys if keys else [pid])

                if person_mode == "all":
                    person_paths = pss.get_co_occurrence_paths(all_keys)
                else:
                    person_paths = pss.get_person_photo_paths_multi(all_keys)
            else:
                return plan

            if person_paths:
                plan.filters["_person_paths"] = person_paths
                logger.info(
                    f"[SearchOrchestrator] Person filter resolved: "
                    f"{len(person_paths)} photos for person(s)"
                )
        except Exception as e:
            logger.debug(f"[SearchOrchestrator] Person filter resolution failed: {e}")

        return plan

    def _enrich_person_facet(self, result: OrchestratorResult):
        """Add person facet to search results if people are detected."""
        if not result.paths or len(result.paths) < 5:
            return

        pss = self._person_search_svc
        if not pss:
            return

        try:
            person_facet = pss.compute_person_facet(result.paths, max_people=8)
            if person_facet and len(person_facet) >= 2:
                result.facets["people"] = person_facet
        except Exception:
            pass

    # ── Phase 3: Suggestion API ──

    def get_suggestions(self, prefix: str, limit: int = 12) -> List[Dict]:
        """
        Get search suggestions for autocomplete.

        Args:
            prefix: Current search widget text
            limit: Maximum suggestions

        Returns:
            List of suggestion dicts with type, text, icon, action, score
        """
        svc = self._suggestion_service
        if not svc:
            return []
        try:
            return svc.suggest(prefix, limit=limit)
        except Exception:
            return []

    # ── Phase 3: Entity Graph API ──

    def get_photo_entities(self, photo_path: str) -> List[Dict]:
        """Get all entities linked to a photo (for detail panels)."""
        graph = self._entity_graph_svc
        if not graph:
            return []
        try:
            nodes = graph.get_photo_entities(photo_path)
            return [
                {
                    "type": n.entity_type,
                    "id": n.entity_id,
                    "name": n.display_name,
                    "photo_count": n.photo_count,
                }
                for n in nodes
            ]
        except Exception:
            return []

    def get_entity_context(self, entity_type: str, entity_id: str) -> Dict:
        """Get full context for an entity (for knowledge cards)."""
        graph = self._entity_graph_svc
        if not graph:
            return {}
        try:
            context = graph.get_entity_context(entity_type, entity_id)
            result = {}
            for etype, nodes in context.items():
                result[etype] = [
                    {
                        "type": n.entity_type,
                        "id": n.entity_id,
                        "name": n.display_name,
                        "photo_count": n.photo_count,
                    }
                    for n in nodes
                ]
            return result
        except Exception:
            return {}

    # ── Internal Pipeline ──

    # Minimum results before backoff triggers
    _MIN_RESULTS_TARGET = 20

    # Face coverage floor for people-event presets.
    # Below this, return "not ready" instead of silently degraded results.
    _FACE_COVERAGE_FLOOR = 0.10

    def _is_people_implied(self, plan: QueryPlan) -> bool:
        """Detect if a query implies people/faces (portraits, baby, wedding, etc.)."""
        return is_people_implied(plan)

    def _execute_utility_metadata_only(
        self,
        plan: QueryPlan,
        project_meta: Dict[str, Dict],
        top_k: int,
    ) -> OrchestratorResult:
        """
        Metadata-only execution path for utility-family presets.
        No builder, no CLIP, no FAMILY_FALLBACK.
        """
        from services.smart_find_service import get_smart_find_service
        sf = get_smart_find_service(self.project_id)

        # Re-use metadata filter from SmartFindService
        matched_paths = sf._run_metadata_filter(plan.filters)

        family = "utility"

        scored = []
        for path in matched_paths:
            scored.append(
                self._score_result(
                    path=path,
                    clip_score=0.0,
                    matched_prompt="",
                    project_meta=project_meta,
                    active_filters=plan.filters,
                    people_implied=False,
                    family=family,
                )
            )

        scored.sort(key=lambda r: r.final_score, reverse=True)
        scored = scored[:top_k]

        result_paths = [r.path for r in scored]
        facets = FacetComputer.compute(result_paths, project_meta)

        logger.info(
            f"[SearchOrchestrator] UTILITY_FAST_PATH: "
            f"preset={plan.preset_id!r} filters={plan.filters} "
            f"matched={len(result_paths)}"
        )

        return OrchestratorResult(
            paths=result_paths,
            total_matches=len(result_paths),
            scored_results=scored,
            scores={r.path: r.final_score for r in scored},
            facets=facets,
            query_plan=plan,
            family=family,
            phase="full",
            phase_label="Metadata results",
        )

    # ── Phase 4: Family-first candidate builder dispatch ──

    def _build_candidate_set(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, Dict],
        top_k: int,
    ) -> Optional[CandidateSet]:
        """
        Dispatch to family-specific candidate builder if available.

        Returns CandidateSet for families that have dedicated builders,
        or None for families that should fall through to the legacy
        CLIP-first pipeline.
        """
        family = intent.family_hint
        if not family:
            return None

        # Preset-specific builder takes priority (e.g. screenshots)
        builder_cls = PRESET_BUILDERS.get(intent.preset_id)
        if builder_cls is None:
            builder_cls = CANDIDATE_BUILDERS.get(family)
        if builder_cls is None:
            # Structured fallback event: no dedicated builder for this family
            logger.info(
                f"[SearchOrchestrator] FAMILY_FALLBACK: "
                f"family={family!r} preset={intent.preset_id!r} "
                f"query={intent.raw_query!r} -> "
                f"no dedicated builder, falling back to legacy CLIP pipeline"
            )
            return None

        try:
            builder = builder_cls(self.project_id)
            candidate_set = builder.build(intent, project_meta, limit=top_k * 3)
            logger.info(
                f"[SearchOrchestrator] CandidateBuilder({family}): "
                f"{candidate_set.count} candidates, "
                f"ready_state={candidate_set.ready_state}, "
                f"confidence={candidate_set.builder_confidence:.2f}"
            )
            return candidate_set
        except Exception as e:
            logger.warning(
                f"[SearchOrchestrator] CandidateBuilder({family}) failed: {e}; "
                f"falling back to legacy pipeline"
            )
            return None

    def _apply_confidence_policy(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
        family: str,
    ) -> SearchDecision:
        """Evaluate result trustworthiness via SearchConfidencePolicy."""
        policy = getattr(self, '_confidence_policy', None) or SearchConfidencePolicy()
        return policy.evaluate(intent, candidate_set, ranked_results, family)

    def _get_screenshot_semantic_fallback_candidates(
        self,
        top_k: int,
        threshold: float,
        fusion_mode: str,
    ) -> Dict[int, tuple]:
        """
        Narrow semantic supplement for screenshots only.

        This is NOT a legacy fallback. It is a constrained semantic probe used
        only when the dedicated screenshot builder returns zero candidates.
        """
        if not self._smart_find.clip_available:
            return {}

        prompts = [
            "screenshot",
            "screen capture",
            "mobile app screen",
            "phone screenshot",
            "chat screenshot",
            "settings screen",
        ]

        try:
            return self._smart_find._run_clip_multi_prompt(
                prompts,
                top_k=max(20, top_k * 2),
                threshold=max(0.18, threshold - 0.02),
                fusion_mode=fusion_mode,
            )
        except Exception as e:
            logger.warning(
                f"[SearchOrchestrator] screenshot semantic supplement failed: {e}"
            )
            return {}

    def _prune_document_survivors(
        self,
        scored_results: List[ScoredResult],
        builder_evidence: Optional[Dict[str, dict]],
        project_meta: Dict[str, Dict],
    ) -> List[ScoredResult]:
        """
        Final integrity pass for Documents preset.

        A surviving document result must either:
        - pass canonical document evaluation, or
        - be explicitly admitted by DocumentCandidateBuilder evidence.
        """
        if not scored_results:
            return scored_results

        kept = []
        dropped = 0
        builder_evidence = builder_evidence or {}

        for r in scored_results:
            ev = builder_evidence.get(r.path) or {}
            meta = project_meta.get(r.path, {})

            builder_ok = bool(
                ev.get("ocr_fts_hit")
                or ev.get("ocr_lexicon_hit")
                or ev.get("doc_extension")
                or ev.get("strong_raster_document")
                or (
                    ev.get("low_confidence_admit")
                    and ev.get("has_text_dense_layout")
                )
            )

            gate_ok = self._gate_engine._passes_document_gate(meta, r.path)

            if builder_ok or gate_ok:
                kept.append(r)
            else:
                logger.debug(
                    f"[SearchOrchestrator] DOCUMENT_PRUNE_DROP: "
                    f"{os.path.basename(r.path)} "
                    f"ocr_fts={ev.get('ocr_fts_hit')} "
                    f"ocr_lex={ev.get('ocr_lexicon_hit')} "
                    f"doc_ext={ev.get('doc_extension')} "
                    f"strong_raster={ev.get('strong_raster_document')} "
                    f"low_conf={ev.get('low_confidence_admit')} "
                    f"text_dense={ev.get('has_text_dense_layout')}"
                )
                dropped += 1

        if dropped:
            logger.warning(
                f"[SearchOrchestrator] DOCUMENT_SURVIVOR_PRUNE: "
                f"kept={len(kept)}/{len(scored_results)} dropped={dropped}"
            )

        return kept

    def _get_builder_screenshot_score(
        self,
        path: str,
        builder_evidence_by_path: Dict[str, dict],
    ) -> float:
        """Extract screenshot score from builder evidence."""
        evidence = builder_evidence_by_path.get(path, {})
        return float(evidence.get("screenshot_score", 0.0) or 0.0)

    def _get_scenic_structural_score(
        self,
        path: str,
        scenic_evidence: Optional[Dict[str, dict]],
    ) -> float:
        """Return scenic-positive structural support from builder evidence."""
        if not scenic_evidence:
            return 0.0
        ev = scenic_evidence.get(path) or {}
        return float(ev.get("scenic_positive", 0.0) or 0.0) + float(ev.get("soft_penalty", 0.0) or 0.0)

    def _has_structural_screenshot_signal(self, evidence: Dict[str, Any]) -> bool:
        """Non-semantic screenshot signals that justify supplement admission."""
        if not evidence:
            return False
        return any([
            bool(evidence.get("is_screenshot_flag")),
            bool(evidence.get("filename_marker")),
            bool(evidence.get("ui_text_hit")),
            bool(evidence.get("looks_like_phone_screen")),
            bool(evidence.get("looks_like_tablet_screen")),
            bool(evidence.get("looks_like_desktop_screen")),
            bool(evidence.get("dense_ui_ocr")),
            bool(evidence.get("flat_ui_fallback")),
        ])

    def _is_screenshot_supplement_admissible(
        self,
        path: str,
        sem_score: float,
        type_evidence: Dict[str, dict],
        project_meta: Dict[str, Dict],
    ) -> bool:
        """
        Weak semantic similarity may help ranking, but must not create
        screenshot legality by itself.

        Admit supplemental hits if they have at least one valid structural
        signal and meet a permissive semantic threshold.
        """
        evidence = type_evidence.get(path, {}) or {}

        # 1. Admit anything the builder already liked
        builder_score = float(evidence.get("screenshot_score", 0.0) or 0.0)
        if builder_score >= 0.20:
            return True

        # 2. Rescue assets with structural signals if they have CLIP support
        has_structural = self._has_structural_screenshot_signal(evidence)
        if has_structural and sem_score >= 0.23:
            return True

        # 3. High confidence rescue (strong builder score but not quite 0.20)
        if builder_score >= 0.15 and sem_score >= 0.21:
            return True

        return False

    def _log_type_builder_diagnostics(
        self,
        preset_id: str,
        candidate_set: CandidateSet,
        project_meta: Dict[str, dict],
    ):
        """Log granular diagnostics for empty type-family results."""
        diag = candidate_set.diagnostics or {}
        rejections = diag.get("rejections", {})
        acceptance_reasons = diag.get("acceptance_reasons", {})
        has_any_screenshot = diag.get("has_any_screenshot_flag", False)
        has_any_ocr = diag.get("has_any_ocr_text", False)

        logger.info(
            f"[SearchOrchestrator] {preset_id!r} DIAGNOSTICS: "
            f"library_size={len(project_meta)} "
            f"has_screenshot_metadata={has_any_screenshot} "
            f"has_ocr_text={has_any_ocr} "
            f"rejections={rejections}"
        )

        if acceptance_reasons:
            logger.info(
                f"[SearchOrchestrator] SCREENSHOT_ACCEPTANCE_REASONS: "
                f"{acceptance_reasons}"
            )

    def get_last_candidate_diagnostics(self) -> dict:
        """Return diagnostics from the most recent candidate builder run."""
        return getattr(self, "_last_candidate_diagnostics", {})

    def _resolve_family(self, intent: QueryIntent, plan: QueryPlan) -> str:
        """Resolve the dominant retrieval family from intent + plan."""
        # Intent family_hint takes priority if set
        if intent.family_hint:
            return intent.family_hint
        # Fall back to preset-based family
        return get_preset_family(plan.preset_id)

    # ── Legacy _build_type_candidates() removed ──
    # The inline structural candidate builder was a split-brain:
    # it reimplemented DocumentEvidenceEvaluator logic with its own
    # constants, extensions set, and GateEngine calls.  All type-family
    # candidate generation now goes through DocumentCandidateBuilder
    # (services/candidate_builders/document_candidate_builder.py) which
    # uses the canonical DocumentEvidenceEvaluator.  If the builder is
    # unavailable, search returns empty rather than silently switching
    # to a parallel evidence contract.

    @staticmethod
    def _fuse_candidate_sets(*candidate_sets: CandidateSet) -> CandidateSet:
        """
        Merge multiple CandidateSets by path with evidence accumulation.

        When the same path appears in multiple builders, evidence dicts are
        merged (later builder wins on key conflicts).  The fused set inherits
        the family from the first non-empty set.

        This is the Google-style fusion pattern: run multiple retrieval
        strategies in parallel, then merge into one candidate pool.
        """
        merged_paths = []
        merged_evidence = {}
        merged_source_counts = {}
        seen = set()
        family = "type"
        all_notes = []
        all_diagnostics = {}
        best_confidence = 0.0

        for cs in candidate_sets:
            if cs is None:
                continue
            if cs.candidate_paths:
                family = cs.family
            for path in cs.candidate_paths:
                if path not in seen:
                    seen.add(path)
                    merged_paths.append(path)
                    merged_evidence[path] = cs.evidence_by_path.get(path, {})
                else:
                    # Merge evidence from additional builder
                    existing = merged_evidence.get(path, {})
                    new_ev = cs.evidence_by_path.get(path, {})
                    existing.update(new_ev)
                    merged_evidence[path] = existing

            for k, v in cs.source_counts.items():
                merged_source_counts[k] = merged_source_counts.get(k, 0) + v
            all_notes.extend(cs.notes or [])
            if cs.diagnostics:
                all_diagnostics[cs.notes[0] if cs.notes else "builder"] = cs.diagnostics
            best_confidence = max(best_confidence, cs.builder_confidence)

        ready = "ready" if merged_paths else "empty"
        return CandidateSet(
            family=family,
            candidate_paths=merged_paths,
            evidence_by_path=merged_evidence,
            source_counts=merged_source_counts,
            builder_confidence=best_confidence,
            ready_state=ready,
            notes=all_notes,
            diagnostics=all_diagnostics,
        )

    def _check_face_readiness(
        self, plan: QueryPlan, project_meta: Dict[str, Dict],
    ) -> Optional[OrchestratorResult]:
        """
        Check if face index is ready for people-event presets.

        Returns an OrchestratorResult with a "not ready" status if face
        coverage is below the floor threshold. Returns None if ready.

        This prevents silently degraded results that look convincing but
        are actually just generic CLIP-only portraits.
        """
        current_family = get_preset_family(plan.preset_id)
        if current_family != "people_event":
            return None

        total_photos = len(project_meta) if project_meta else 0
        if total_photos == 0:
            return None

        face_photo_count = sum(
            1 for m in project_meta.values()
            if (m.get("face_count", 0) or 0) > 0
        )
        face_coverage = face_photo_count / total_photos

        if face_coverage >= self._FACE_COVERAGE_FLOOR:
            return None

        logger.warning(
            f"[SearchOrchestrator] Face index not ready for "
            f"preset={plan.preset_id!r}: "
            f"coverage={face_photo_count}/{total_photos} "
            f"({face_coverage:.0%}) < floor={self._FACE_COVERAGE_FLOOR:.0%}. "
            f"Run face detection pipeline for accurate results."
        )

        return OrchestratorResult(
            paths=[],
            total_matches=0,
            scored_results=[],
            scores={},
            facets={},
            query_plan=plan,
            phase_label=(
                f"Face index not ready ({face_photo_count}/{total_photos} "
                f"photos indexed). Run face detection for "
                f"{plan.preset_id!r} results."
            ),
            label=self._build_label(plan, OrchestratorResult()),
        )

    def _execute(self, plan: QueryPlan, top_k: int) -> OrchestratorResult:
        """Execute the full search pipeline from a QueryPlan."""
        # Initialize candidate pools and evidence early to avoid UnboundLocalError
        semantic_hits = {}
        type_evidence = {}
        people_event_evidence = {}
        scenic_evidence = {}
        type_structural_candidates = None
        people_event_candidates = None
        scenic_candidate_pool = None

        cfg = self._smart_find._get_config()
        threshold = plan.threshold_override if plan.threshold_override is not None else cfg["threshold"]
        fusion_mode = cfg["fusion_mode"]

        # Detect people-implied queries for face presence scoring
        people_implied = self._is_people_implied(plan)

        # Step 0: Load project metadata early (needed for candidate builders)
        project_meta = self._get_project_meta()

        # Phase 11: utility presets are metadata-only first-class paths.
        # Check this BEFORE any planner/builder fallback logging.
        current_family = get_preset_family(plan.preset_id)
        if current_family == "utility":
            logger.info(
                f"[SearchOrchestrator] UTILITY_ROUTE: "
                f"preset={plan.preset_id!r} family='utility' "
                f"executing metadata-only fast path"
            )
            return self._execute_utility_metadata_only(
                plan=plan,
                project_meta=project_meta,
                top_k=top_k,
            )

        # ── Phase 4: Try family-first candidate builder pipeline ──
        # For families with dedicated builders (type, people_event),
        # the builder is the mandatory first-stage retrieval path.
        # The ranking stage only sees the builder's candidate pool.
        planner = self._query_intent_planner_svc
        builder_intent = None
        builder_candidate_set = None
        if planner:
            try:
                builder_intent = planner.plan(
                    plan.raw_query, preset_id=plan.preset_id
                )
                builder_candidate_set = self._build_candidate_set(
                    builder_intent, project_meta, top_k
                )
            except Exception as e:
                logger.debug(
                    f"[SearchOrchestrator] Phase 4 planner/builder skipped: {e}"
                )

        # If builder produced a not_ready result, return immediately
        if (builder_candidate_set is not None
                and builder_candidate_set.ready_state == "not_ready"):
            decision = self._apply_confidence_policy(
                builder_intent, builder_candidate_set, [],
                builder_intent.family_hint or get_preset_family(plan.preset_id),
            )
            return OrchestratorResult(
                paths=[],
                total_matches=0,
                scored_results=[],
                scores={},
                facets={},
                query_plan=plan,
                phase_label=decision.warning_message or "Index not ready",
                label=self._build_label(plan, OrchestratorResult()),
                confidence_label=decision.confidence_label,
                confidence_warning=decision.warning_message or "",
            )

        # If builder produced a candidate set, use it as the retrieval pool
        # (replaces both the old type_structural_candidates and face_readiness)
        builder_active = (
            builder_candidate_set is not None
            and builder_candidate_set.is_ready
            and builder_candidate_set.count > 0
        )

        # Step 0a: Face readiness check for people-event presets
        # (skipped if builder already handled it)
        if not builder_active:
            face_block = self._check_face_readiness(plan, project_meta)
            if face_block is not None:
                return face_block

        current_family = get_preset_family(plan.preset_id)
        # Override family if builder resolved it
        if builder_intent and builder_intent.family_hint:
            current_family = builder_intent.family_hint

        # ── Family-path consistency assertion ──
        # If the builder resolved a family, it must match the preset-derived
        # family. A mismatch means QueryIntentPlanner and PRESET_FAMILIES
        # disagree — log a warning so the inconsistency is visible.
        if builder_intent and builder_intent.family_hint:
            preset_family = get_preset_family(plan.preset_id)
            if builder_intent.family_hint != preset_family:
                logger.warning(
                    f"[SearchOrchestrator] FAMILY_MISMATCH: "
                    f"builder says {builder_intent.family_hint!r} but "
                    f"preset {plan.preset_id!r} maps to {preset_family!r}. "
                    f"Using builder hint. Check PRESET_FAMILIES mapping."
                )

        # Step 0b: Type-family structural candidate generation
        # (Variables initialized at start of method)
        if builder_active and current_family == "scenic":
            scenic_candidate_pool = set(builder_candidate_set.candidate_paths)
            scenic_evidence = builder_candidate_set.evidence_by_path or {}
            if not scenic_candidate_pool:
                logger.info(
                    f"[SearchOrchestrator] Scenic builder returned empty pool for "
                    f"preset={plan.preset_id!r}; proceeding with full CLIP fallback"
                )
                scenic_candidate_pool = None
            else:
                logger.info(
                    f"[SearchOrchestrator] Scenic builder pre-filtered: "
                    f"{len(scenic_candidate_pool)} candidates "
                    f"(excluded {len(project_meta) - len(scenic_candidate_pool)} "
                    f"non-scenic assets)"
                )

        if builder_active and current_family == "type":
            type_structural_candidates = set(builder_candidate_set.candidate_paths)
            type_evidence = builder_candidate_set.evidence_by_path or {}
            if not type_structural_candidates:
                logger.info(
                    f"[SearchOrchestrator] TYPE_BUILDER_EMPTY: "
                    f"family={current_family!r} preset={plan.preset_id!r} "
                    f"builder returned 0 legal candidates"
                )

                self._log_type_builder_diagnostics(
                    plan.preset_id,
                    builder_candidate_set,
                    project_meta,
                )

                # Phase 2: screenshots-only semantic supplement
                if (plan.preset_id or "").lower() == "screenshots":
                    semantic_hits = self._get_screenshot_semantic_fallback_candidates(
                        top_k=top_k,
                        threshold=threshold,
                        fusion_mode=fusion_mode,
                    )

                    if semantic_hits:
                        logger.warning(
                            f"[SearchOrchestrator] SCREENSHOT_SUPPLEMENT: "
                            f"builder empty, semantic supplement produced "
                            f"{len(semantic_hits)} raw hits"
                        )
                        type_structural_candidates = set()
                    else:
                        return OrchestratorResult(
                            paths=[],
                            total_matches=0,
                            scored_results=[],
                            scores={},
                            facets={},
                            query_plan=plan,
                            phase_label=f"No {plan.preset_id} found in library",
                            label=self._build_label(plan, OrchestratorResult()),
                            confidence_label="empty",
                            confidence_warning="No screenshot candidates found.",
                        )
                else:
                    return OrchestratorResult(
                        paths=[],
                        total_matches=0,
                        scored_results=[],
                        scores={},
                        facets={},
                        query_plan=plan,
                        phase_label=f"No {plan.preset_id} found in library",
                        label=self._build_label(plan, OrchestratorResult()),
                        confidence_label="empty",
                        confidence_warning="No legal type-family candidates found.",
                    )
        elif builder_active and current_family == "people_event":
            # People_event gets its own candidate pool — never reuse
            # type_structural_candidates variable.
            people_event_candidates = set(builder_candidate_set.candidate_paths)
            people_event_evidence = builder_candidate_set.evidence_by_path or {}
            if not people_event_candidates:
                logger.info(
                    f"[SearchOrchestrator] [PeopleEvent] Builder returned empty "
                    f"candidates for preset={plan.preset_id!r}; returning empty"
                )
                return OrchestratorResult(
                    paths=[],
                    total_matches=0,
                    scored_results=[],
                    scores={},
                    facets={},
                    query_plan=plan,
                    phase_label=f"No {plan.preset_id} found in library",
                    label=self._build_label(plan, OrchestratorResult()),
                    confidence_label="empty",
                )
            logger.info(
                f"[SearchOrchestrator] [PeopleEvent] {len(people_event_candidates)} "
                f"candidates from builder, {len(people_event_evidence)} with evidence"
            )
        elif not builder_active and current_family == "type" and plan.preset_id in ("documents", "screenshots"):
            # No legacy fallback: builder is the single source of truth
            # for type-family candidates.
            logger.info(
                f"[SearchOrchestrator] BUILDER_UNAVAILABLE: "
                f"family={current_family!r} preset={plan.preset_id!r} -> "
                f"no builder candidates, attempting family-internal supplement"
            )

            # Phase 2: screenshots-only semantic supplement
            if (plan.preset_id or "").lower() == "screenshots":
                semantic_hits = self._get_screenshot_semantic_fallback_candidates(
                    top_k=top_k,
                    threshold=threshold,
                    fusion_mode=fusion_mode,
                )

                if semantic_hits:
                    logger.warning(
                        f"[SearchOrchestrator] SCREENSHOT_SUPPLEMENT: "
                        f"no legal builder candidates, semantic supplement produced "
                        f"{len(semantic_hits)} raw hits"
                    )
                    type_structural_candidates = set()
                else:
                    return OrchestratorResult(
                        paths=[],
                        total_matches=0,
                        scored_results=[],
                        scores={},
                        facets={},
                        query_plan=plan,
                        phase_label=f"No {plan.preset_id} found — builder unavailable",
                        label=self._build_label(plan, OrchestratorResult()),
                        confidence_label="empty",
                    )
            else:
                return OrchestratorResult(
                    paths=[],
                    total_matches=0,
                    scored_results=[],
                    scores={},
                    facets={},
                    query_plan=plan,
                    phase_label=f"No {plan.preset_id} found — builder unavailable",
                    label=self._build_label(plan, OrchestratorResult()),
                    confidence_label="empty",
                )

        # Step 1: Semantic candidates
        # ARCHITECTURAL FIX: Skip full-corpus CLIP for type-family presets
        # (documents, screenshots) when structural candidates exist.
        # These presets are structure-first: OCR + geometry + metadata are
        # sufficient for retrieval.  Running CLIP over the full embedding
        # universe is unnecessary and introduces crash risk (the CLIP text
        # inference path on Windows is fragile under concurrent Qt workers).
        # CLIP is only used as an optional reranker on the small structural
        # candidate set later, if at all.
        skip_clip_for_type = (
            (type_structural_candidates is not None
             and len(type_structural_candidates) > 0)
            or (people_event_candidates is not None
                and len(people_event_candidates) > 0)
        )
        # Step 1: Semantic candidates
        _supplemental_hits = semantic_hits.copy()
        semantic_hits = {}  # {photo_id: (score, prompt)}
        if plan.has_semantic() and self._smart_find.clip_available and not skip_clip_for_type:
            # One-time embedding quality diagnostic
            if not self._embedding_quality_logged:
                self._log_embedding_quality()

            prompts = plan.semantic_prompts if plan.semantic_prompts else [plan.semantic_text]
            semantic_hits = self._smart_find._run_clip_multi_prompt(
                prompts, top_k * 3, threshold, fusion_mode
            )

        if _supplemental_hits:
            # Merge supplemental hits (e.g. from screenshot supplement)
            # Broad hits take priority if they overlap.
            for photo_id, val in _supplemental_hits.items():
                if photo_id not in semantic_hits:
                    semantic_hits[photo_id] = val
        elif skip_clip_for_type:
            # Log which pool caused the skip (type or people_event)
            _skip_pool_size = (
                len(type_structural_candidates) if type_structural_candidates is not None
                else len(people_event_candidates) if people_event_candidates is not None
                else 0
            )
            _skip_pool_name = (
                "structural" if type_structural_candidates is not None
                else "people_event" if people_event_candidates is not None
                else "unknown"
            )
            logger.info(
                f"[SearchOrchestrator] Skipping CLIP for {current_family} preset "
                f"{plan.preset_id!r}: {_skip_pool_size} "
                f"{_skip_pool_name} candidates will be scored without full-corpus CLIP"
            )

        # Step 2: Metadata filter candidates
        metadata_candidate_paths = None
        if plan.has_filters():
            metadata_paths = self._smart_find._run_metadata_filter(plan.filters)
            metadata_candidate_paths = set(metadata_paths)

        # Step 2a: Person path filter (Phase 3)
        # If person_id resolved to a set of paths, intersect with candidates
        person_paths = plan.filters.pop("_person_paths", None)
        if person_paths:
            person_path_set = set(person_paths) if not isinstance(person_paths, set) else person_paths
            if metadata_candidate_paths is not None:
                metadata_candidate_paths &= person_path_set
            else:
                metadata_candidate_paths = person_path_set
            logger.info(
                f"[SearchOrchestrator] Person path filter: "
                f"{len(person_path_set)} person photos → "
                f"{len(metadata_candidate_paths)} after intersection"
            )

        # Step 2b: OCR text candidates
        # When user types free text, also search OCR-extracted text.
        # OCR matches get injected as metadata candidates so they appear
        # even when CLIP doesn't find them semantically.
        ocr_match_paths = set()
        ocr_query = plan.semantic_text or plan.raw_query
        if ocr_query and len(ocr_query.strip()) >= 2:
            try:
                ocr_paths = SearchOrchestrator.search_ocr_text(
                    self.project_id, ocr_query, limit=top_k
                )
                if ocr_paths:
                    ocr_match_paths = set(ocr_paths)
                    logger.info(
                        f"[SearchOrchestrator] OCR text search found "
                        f"{len(ocr_match_paths)} matches for '{ocr_query}'"
                    )
                    # Merge OCR matches into metadata candidates
                    if metadata_candidate_paths is not None:
                        metadata_candidate_paths.update(ocr_match_paths)
                    else:
                        # If no metadata filters, OCR matches become candidates
                        if not semantic_hits:
                            metadata_candidate_paths = ocr_match_paths
            except Exception as e:
                logger.debug(f"[SearchOrchestrator] OCR search skipped: {e}")

        # Step 3: Resolve photo_id -> path
        path_lookup = {}
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
                logger.error(f"[SearchOrchestrator] Path resolution failed: {e}")

        if people_implied:
            face_photo_count = sum(
                1 for m in project_meta.values()
                if (m.get("face_count", 0) or 0) > 0
            )
            total_photos = len(project_meta)
            face_coverage = face_photo_count / total_photos if total_photos else 0
            logger.info(
                f"[SearchOrchestrator] People-implied query: "
                f"{face_photo_count}/{total_photos} photos have face_count > 0 "
                f"(coverage={face_coverage:.0%})"
            )
            # If face data is essentially absent (<1% coverage), temporarily
            # zero out face weight so it doesn't waste ranking budget on a
            # null signal.  Redistribute its weight to clip.
            if face_coverage < 0.01 and total_photos > 0:
                people_implied = False  # disable face boosting for this query
                logger.info(
                    "[SearchOrchestrator] Face data unavailable "
                    "(coverage < 1%); face scoring disabled for this query. "
                    "Run face detection for better people results."
                )

        # Step 4b: Pre-compute structural + OCR + event scores per family
        # Structural scores are computed once and folded into the weighted
        # scoring contract as first-class terms (w_structural * structural,
        # w_ocr * ocr, w_event * event).
        structural_scores: Dict[str, float] = {}
        ocr_scores: Dict[str, float] = {}
        event_scores: Dict[str, float] = {}
        if current_family == "type":
            structural_scores = self._compute_structural_scores(
                plan, project_meta, ocr_match_paths
            )
            ocr_scores = self._compute_ocr_scores(plan, project_meta)
            # Supplement with builder evidence when available.
            # The builder's evidence_by_path contains boolean signals
            # (ocr_fts_hit, ocr_lexicon_hit, structural_hit, etc.)
            # that can boost candidates the metadata scan may have
            # scored conservatively.
            if type_evidence:
                boosted = 0
                for path, ev in type_evidence.items():
                    bonus = 0.0
                    if ev.get("ocr_fts_hit"):
                        bonus += 0.10
                    if ev.get("ocr_lexicon_hit"):
                        bonus += 0.08
                    if ev.get("structural_hit"):
                        bonus += 0.05
                    if bonus > 0:
                        structural_scores[path] = min(
                            1.0, structural_scores.get(path, 0.0) + bonus
                        )
                        boosted += 1
                if boosted:
                    logger.info(
                        f"[SearchOrchestrator] Builder evidence boosted "
                        f"structural scores for {boosted}/{len(type_evidence)} "
                        f"type candidates"
                    )
        elif current_family == "scenic":
            structural_scores = self._compute_scenic_anti_type_scores(
                plan, project_meta
            )
        elif current_family == "people_event" and people_event_evidence:
            # Extract event_score from builder evidence — this is the
            # composite signal (named match, co-occurrence, face count,
            # portrait, favorite) computed by PeopleCandidateBuilder.
            event_scores = {
                path: ev.get("event_score", 0.0)
                for path, ev in people_event_evidence.items()
            }
            logger.info(
                f"[SearchOrchestrator] [PeopleEvent] Extracted event_scores "
                f"for {len(event_scores)} candidates "
                f"(avg={sum(event_scores.values()) / max(1, len(event_scores)):.3f})"
            )

        # Step 5: Score every candidate
        scored: List[ScoredResult] = []

        # ── People-event branch: own scoring path with event_score ──
        # Builder-produced candidates are scored with event evidence
        # extracted from people_event_evidence, not reusing type path.
        if people_event_candidates is not None:
            # Optional CLIP rerank within the people pool
            clip_path_scores = {}
            if semantic_hits:
                for photo_id, (sem_score, matched_prompt) in semantic_hits.items():
                    path = path_lookup.get(photo_id)
                    if path:
                        clip_path_scores[path] = (sem_score, matched_prompt)

            for path in people_event_candidates:
                clip_info = clip_path_scores.get(path, (0.0, ""))
                sem_score, matched_prompt = clip_info
                ev_score = event_scores.get(path, 0.0)
                sr = self._score_result(
                    path, sem_score, matched_prompt, project_meta,
                    plan.filters, people_implied,
                    event_score=ev_score,
                    family=current_family,
                )
                scored.append(sr)

            logger.info(
                f"[SearchOrchestrator] [PeopleEvent] retrieval: "
                f"{len(scored)} candidates scored with event_score "
                f"({len(clip_path_scores)} had CLIP scores)"
            )

        # ── Type-family structural branch (documents, screenshots) ──
        # Seed from structural candidates, not from CLIP hits.
        # CLIP may re-rank but may NOT create candidates by itself.
        elif type_structural_candidates is not None:
            # Score all structural candidates (CLIP score applied if available)
            clip_path_scores = {}
            if semantic_hits:
                for photo_id, (sem_score, matched_prompt) in semantic_hits.items():
                    path = path_lookup.get(photo_id)
                    if path:
                        clip_path_scores[path] = (sem_score, matched_prompt)

            # Resolve screenshot supplemental semantic hits into the pool,
            # but only if they pass screenshot legality admission.
            supplement_admitted = 0
            supplement_rejected = 0
            weak_semantic_only = 0

            if semantic_hits and (plan.preset_id or "").lower() == "screenshots":
                for photo_id, (sem_score, matched_prompt) in semantic_hits.items():
                    path = path_lookup.get(photo_id)
                    if not path:
                        continue

                    # Circular reasoning fix: evaluate admissibility using ORIGINAL builder evidence
                    # BEFORE injecting the 0.20 supplement prior.
                    ev = type_evidence.get(path, {})
                    if self._is_screenshot_supplement_admissible(
                        path=path,
                        sem_score=sem_score,
                        type_evidence=type_evidence,
                        project_meta=project_meta,
                    ):
                        if path not in type_evidence:
                            type_evidence[path] = {
                                "builder": "screenshot_supplement",
                                "supplement_only": True,
                                "admission_mode": "semantic_rescue",
                                "screenshot_score": 0.20,
                            }
                        else:
                            ev.setdefault("builder", "screenshot_supplement")
                            ev["supplement_only"] = True
                            ev["admission_mode"] = "structural_rescue"
                            ev.setdefault("screenshot_score", 0.20)

                        type_structural_candidates.add(path)
                        supplement_admitted += 1
                    else:
                        supplement_rejected += 1
                        if not self._has_structural_screenshot_signal(ev):
                            weak_semantic_only += 1

                builder_diag = getattr(builder_candidate_set, "diagnostics", None) or {}
                builder_diag["supplement_admitted"] = supplement_admitted
                builder_diag["supplement_rejected"] = supplement_rejected
                builder_diag["weak_semantic_only"] = weak_semantic_only
                if builder_candidate_set is not None:
                    builder_candidate_set.diagnostics = builder_diag

                logger.info(
                    f"[SearchOrchestrator] SCREENSHOT_SUPPLEMENT_FILTER: "
                    f"admitted={supplement_admitted} rejected={supplement_rejected} "
                    f"weak_semantic_only={weak_semantic_only} "
                    f"builder_acceptance={builder_diag.get('acceptance_reasons', {})}"
                )

            for path in type_structural_candidates:
                clip_info = clip_path_scores.get(path, (0.0, ""))
                sem_score, matched_prompt = clip_info
                struct = structural_scores.get(path, 0.0)
                ocr = ocr_scores.get(path, 0.0)

                screenshot_score = 0.0
                if (plan.preset_id or "").lower() == "screenshots":
                    screenshot_score = self._get_builder_screenshot_score(
                        path, type_evidence
                    )

                    # Phase 2: semantic supplement may produce candidates not present
                    # in builder_evidence_by_path. Give them a weak screenshot prior,
                    # not zero, so they can be filtered but still compete.
                    if screenshot_score <= 0.0 and sem_score > 0.0:
                        screenshot_score = 0.20

                    if path not in type_evidence:
                        type_evidence[path] = {}

                    type_evidence[path].setdefault("builder", "screenshot_supplement")
                    type_evidence[path].setdefault("screenshot_score", screenshot_score)

                sr = self._score_result(path, sem_score, matched_prompt, project_meta,
                                        plan.filters, people_implied,
                                        structural_score=struct, ocr_score=ocr,
                                        screenshot_score=screenshot_score,
                                        family=current_family)
                scored.append(sr)

            if (plan.preset_id or "").lower() == "screenshots" and scored:
                for sr in scored[:10]:
                    logger.info(
                        f"[SearchOrchestrator][Screenshots] pre-sort "
                        f"path={sr.path!r} clip={sr.clip_score:.3f} "
                        f"screen={getattr(sr, 'screenshot_score', 0.0):.3f} "
                        f"ocr={sr.ocr_score:.3f} struct={sr.structural_score:.3f}"
                    )

            logger.info(
                f"[SearchOrchestrator] Type-structural retrieval: "
                f"{len(scored)} candidates seeded from structure "
                f"({len(clip_path_scores)} had CLIP scores)"
            )

        elif semantic_hits:
            scenic_pool_filtered = 0
            for photo_id, (sem_score, matched_prompt) in semantic_hits.items():
                path = path_lookup.get(photo_id)
                if not path:
                    continue
                if metadata_candidate_paths is not None and path not in metadata_candidate_paths:
                    continue

                # Scenic builder pool filter: if the scenic builder ran and
                # excluded this asset, skip it.  This is the key architectural
                # change: CLIP results are intersected with the pre-filtered
                # scenic pool, not scored against the whole corpus.
                if scenic_candidate_pool is not None and path not in scenic_candidate_pool:
                    scenic_pool_filtered += 1
                    continue

                structural_score = 0.0
                if current_family == "scenic":
                    structural_score = self._get_scenic_structural_score(
                        path,
                        getattr(builder_candidate_set, "evidence_by_path", None) if builder_candidate_set else None,
                    )
                else:
                    structural_score = structural_scores.get(path, 0.0)

                ocr = ocr_scores.get(path, 0.0)
                sr = self._score_result(path, sem_score, matched_prompt, project_meta,
                                        plan.filters, people_implied,
                                        structural_score=structural_score, ocr_score=ocr,
                                        family=current_family)
                scored.append(sr)

            if scenic_pool_filtered > 0:
                logger.info(
                    f"[SearchOrchestrator] Scenic pool filter: "
                    f"removed {scenic_pool_filtered} CLIP results "
                    f"that were hard-excluded by ScenicCandidateBuilder"
                )

        elif metadata_candidate_paths is not None:
            for path in metadata_candidate_paths:
                struct = structural_scores.get(path, 0.0)
                ocr = ocr_scores.get(path, 0.0)
                sr = self._score_result(path, 0.0, "", project_meta,
                                        plan.filters, people_implied,
                                        structural_score=struct, ocr_score=ocr,
                                        family=current_family)
                scored.append(sr)

        # Step 5b: Boost OCR-matched results
        # Photos where the query text matches OCR-extracted text get a score
        # boost so they rank higher, especially for type-family searches
        # (Documents, Screenshots) where text content is the primary signal.
        if ocr_match_paths:
            OCR_BOOST = 0.15  # additive bonus for OCR text match
            scored_paths = {r.path for r in scored}
            for sr in scored:
                if sr.path in ocr_match_paths:
                    sr.final_score += OCR_BOOST
                    sr.reasons.append(f"ocr_text_match=+{OCR_BOOST}")

            # Also add OCR-only matches that weren't in semantic results
            for opath in ocr_match_paths:
                if opath not in scored_paths:
                    struct = structural_scores.get(opath, 0.0)
                    ocr = ocr_scores.get(opath, 0.0)
                    sr = self._score_result(
                        opath, 0.0, "", project_meta,
                        plan.filters, people_implied,
                        structural_score=struct, ocr_score=ocr,
                        family=current_family,
                    )
                    sr.final_score += OCR_BOOST
                    sr.reasons.append(f"ocr_text_match=+{OCR_BOOST}")
                    scored.append(sr)

        # Step 5c: Apply additional scenic-family adjustments
        # (Heuristic anti-type penalties and builder-computed boosts)
        if current_family == "scenic" and (structural_scores or scenic_evidence):
            penalized = 0
            boosted = 0
            for sr in scored:
                # 1. Anti-type penalty from orchestrator metadata scan
                penalty = structural_scores.get(sr.path, 0.0) if structural_scores else 0.0
                if penalty < 0:
                    sr.final_score = max(0, sr.final_score + penalty)
                    sr.reasons.append(f"scenic_anti_type={penalty:.3f}")
                    penalized += 1

                # 2. Builder-computed boosts/penalties
                if scenic_evidence:
                    ev = scenic_evidence.get(sr.path, {})
                    soft_pen = ev.get("soft_penalty", 0.0)
                    scenic_boost = ev.get("scenic_boost", 0.0)
                    adjustment = soft_pen + scenic_boost
                    if adjustment != 0.0:
                        sr.final_score = max(0, sr.final_score + adjustment)
                        if soft_pen < 0:
                            sr.reasons.append(f"scenic_soft_pen={soft_pen:.3f}")
                            penalized += 1
                        if scenic_boost > 0:
                            sr.reasons.append(f"scenic_boost={scenic_boost:.3f}")
                            boosted += 1

            if penalized or boosted:
                logger.info(
                    f"[SearchOrchestrator] Scenic refinement: {penalized} penalized, {boosted} boosted"
                )

        # Step 5d: Post-scoring family-path consistency check
        # Verify that the scoring channels used match what the family expects.
        # A type-family result with zero structural and zero OCR, or a
        # people_event result with zero event_score, suggests a wiring bug.
        if scored:
            if current_family == "type":
                no_signal = sum(
                    1 for sr in scored
                    if sr.structural_score == 0.0 and sr.ocr_score == 0.0
                )
                if no_signal == len(scored):
                    logger.warning(
                        f"[SearchOrchestrator] SCORING_ANOMALY: "
                        f"all {len(scored)} type-family results have "
                        f"structural=0 AND ocr=0. Builder may not be "
                        f"producing evidence."
                    )
            elif current_family == "people_event":
                no_event = sum(
                    1 for sr in scored if sr.event_score == 0.0
                )
                if no_event == len(scored):
                    logger.warning(
                        f"[SearchOrchestrator] SCORING_ANOMALY: "
                        f"all {len(scored)} people_event results have "
                        f"event_score=0. PeopleCandidateBuilder may not "
                        f"be producing evidence."
                    )

        # Step 6: Sort by final_score
        scored.sort(key=lambda r: r.final_score, reverse=True)

        if current_family == "scenic":
            scored = self._collapse_duplicate_families_for_scenic(
                scored_results=scored,
                project_meta=project_meta,
            )

        # Step 7: Backoff if below min_results_target and semantic was used
        # Adaptive target AND step for library size:
        #   - 25 photos: target=2, step=0.02  (only backoff on 0-1 results)
        #   - 100 photos: target=10, step=0.02
        #   - 500+ photos: target=20, step=0.04 (original behaviour)
        #
        # Why: ViT-B/32 CLIP at threshold 0.22 typically finds 1-6 genuine
        # matches in a 25-photo library. A target of 7+ forces backoff on
        # every query, lowering threshold to 0.18 where everything matches,
        # destroying discrimination entirely.
        backoff_applied = False
        total_library = len(project_meta) if project_meta else 0
        min_target = min(
            self._MIN_RESULTS_TARGET,
            max(2, total_library // 10),  # 10% of library, floor of 2
        )
        # Smaller backoff step for small libraries: 0.22 → 0.20 instead
        # of 0.22 → 0.18, which keeps some CLIP discrimination alive.
        if total_library <= 100:
            backoff_step = 0.02
        else:
            backoff_step = cfg.get("backoff_step", 0.04)
        if (len(scored) < min_target and plan.has_semantic()
                and self._smart_find.clip_available and plan.allow_backoff
                and not skip_clip_for_type):
            max_retries = cfg.get("backoff_retries", 2)
            prompts = plan.semantic_prompts if plan.semantic_prompts else [plan.semantic_text]
            logger.info(
                f"[SearchOrchestrator] Backoff triggered: {len(scored)} results "
                f"< min_results_target={min_target}"
            )

            # Floor: never drop more than 0.04 below the original threshold.
            # At 0.18 or below, ViT-B/32 loses all discrimination and
            # returns essentially random images.
            backoff_floor = max(0.05, threshold - 0.04)
            already_scored = {sr.path for sr in scored}
            for retry in range(1, max_retries + 1):
                # Check cancellation before each backoff retry
                with self._smart_find._inflight_lock:
                    _token = self._smart_find._inflight_token
                if _token is not None and _token.is_cancelled:
                    logger.info("[SearchOrchestrator] Backoff cancelled (stale search)")
                    break
                lowered = max(backoff_floor, threshold - (backoff_step * retry))
                logger.info(f"[SearchOrchestrator] Backoff retry {retry}: {threshold:.2f} -> {lowered:.2f}")
                retry_hits = self._smart_find._run_clip_multi_prompt(
                    prompts, top_k * 3, lowered, fusion_mode
                )
                if retry_hits:
                    for photo_id, (sem_score, prompt) in retry_hits.items():
                        path = path_lookup.get(photo_id)
                        if not path:
                            # Resolve new photo IDs
                            try:
                                from repository.base_repository import DatabaseConnection
                                db = DatabaseConnection()
                                with db.get_connection() as conn:
                                    row = conn.execute(
                                        "SELECT path FROM photo_metadata WHERE id = ?",
                                        (photo_id,)
                                    ).fetchone()
                                    if row:
                                        path = row['path']
                                        path_lookup[photo_id] = path
                            except Exception:
                                continue
                        if not path:
                            continue
                        if path in already_scored:
                            continue
                        if metadata_candidate_paths is not None and path not in metadata_candidate_paths:
                            continue
                        struct = structural_scores.get(path, 0.0)
                        ocr = ocr_scores.get(path, 0.0)
                        sr = self._score_result(path, sem_score, prompt, project_meta,
                                                plan.filters, people_implied,
                                                structural_score=struct, ocr_score=ocr,
                                                family=current_family)
                        sr.reasons.append("(backoff)")
                        scored.append(sr)
                        already_scored.add(path)

                    scored.sort(key=lambda r: r.final_score, reverse=True)
                    backoff_applied = True
                    logger.info(
                        f"[SearchOrchestrator] Backoff succeeded: {len(scored)} results "
                        f"after retry {retry}"
                    )
                    break
        elif plan.has_semantic() and self._smart_find.clip_available:
            logger.debug(
                f"[SearchOrchestrator] No backoff needed: {len(scored)} results "
                f">= min_results_target={min_target}"
            )

        # Step 7b: Negative prompt penalty (soft scoring adjustment)
        # For presets like Documents that define negative_prompts, compute
        # negative CLIP scores and penalize matching results.
        # Skip for type-family presets where CLIP was intentionally disabled.
        if (plan.negative_prompts and scored and self._smart_find.clip_available
                and not skip_clip_for_type):
            scored = self._apply_negative_prompt_penalty(
                scored, plan, project_meta
            )

        # Step 7c: Structural scoring is now integrated into the scoring
        # contract (w_structural * structural_score) computed in Step 4b.
        # No post-hoc adjustment needed.

        # Step 7d: Gate engine (hard pre-filters)
        # Consolidated gate logic: exclude_faces, exclude_screenshots,
        # require_screenshot, require_faces, min_face_count, require_gps,
        # min_edge_size. Replaces scattered inline gate blocks.
        # Pass combined type evidence (documents/screenshots) for rescues
        scored = self._apply_gates(
            scored, plan, project_meta,
            builder_evidence=(type_evidence or people_event_evidence)
        )

        if (plan.preset_id or "").lower() == "documents":
            builder_evidence = None
            if builder_candidate_set is not None:
                builder_evidence = getattr(builder_candidate_set, "evidence_by_path", None)

            scored = self._prune_document_survivors(
                scored_results=scored,
                builder_evidence=builder_evidence,
                project_meta=project_meta,
            )

        # Step 7e: Family-specific debug logging (post-gate, pre-dedup)
        if scored:
            logger.info(
                f"[SearchOrchestrator] pre-rank preset={plan.preset_id!r} "
                f"family={current_family!r} survivors={len(scored)}"
            )
            for idx, sr in enumerate(scored[:5]):
                logger.info(
                    f"  #{idx} {sr.final_score:.4f} | clip={sr.clip_score:.3f} "
                    f"struct={sr.structural_score:.3f} ocr={sr.ocr_score:.3f} "
                    f"event={sr.event_score:.3f} face={sr.face_match_score:.3f} "
                    f"| {os.path.basename(sr.path)}"
                )

        # Step 8: Deduplicate (stack duplicates behind representative)
        scored, stacked_count = self._deduplicate_results(scored)

        # Step 8b: Post-dedup gate re-validation (Bug C fix)
        # If dedup chose a representative that doesn't satisfy active gates,
        # remove it. This prevents dedup from undoing gate outcomes.
        pre_revalidation = len(scored)
        scored = self._apply_gates(
            scored, plan, project_meta,
            builder_evidence=(type_evidence or people_event_evidence)
        )
        post_revalidation = len(scored)
        if pre_revalidation != post_revalidation:
            logger.warning(
                f"[SearchOrchestrator] Post-dedup gate re-validation dropped "
                f"{pre_revalidation - post_revalidation} result(s) that "
                f"dedup reintroduced outside the gated set"
            )

        # Step 9: Enforce strict path uniqueness
        # After backoff merge + dedup, the same path can still appear if it
        # entered through different candidate paths (initial vs backoff).
        # Keep only the first (highest-scored) occurrence of each path.
        scored = self._enforce_unique_paths(scored)

        # Step 10: Limit to top_k
        scored = scored[:top_k]

        # Step 11: Compute facets from result set
        result_paths = [r.path for r in scored]
        facets = FacetComputer.compute(result_paths, project_meta)

        # Capture builder diagnostics for confidence policy and debugging
        if builder_candidate_set is not None:
            self._last_candidate_diagnostics = builder_candidate_set.diagnostics or {}

        # Step 12: Apply confidence policy (Phase 4)
        confidence_label = ""
        confidence_warning = ""
        if builder_candidate_set is not None and builder_intent is not None:
            try:
                decision = self._apply_confidence_policy(
                    builder_intent, builder_candidate_set, scored,
                    current_family,
                )
                confidence_label = decision.confidence_label
                confidence_warning = decision.warning_message or ""
                if decision.confidence_label in ("low", "not_ready"):
                    logger.warning(
                        f"[SearchOrchestrator] Confidence policy: "
                        f"{decision.confidence_label} — "
                        f"{decision.warning_message}"
                    )
            except Exception as e:
                logger.debug(
                    f"[SearchOrchestrator] Confidence policy skipped: {e}"
                )

        return OrchestratorResult(
            paths=result_paths,
            total_matches=len(result_paths),
            scored_results=scored,
            scores={r.path: r.final_score for r in scored},
            facets=facets,
            query_plan=plan,
            backoff_applied=backoff_applied,
            phase_label="Semantic refined" if plan.has_semantic() else "Filter results",
            stacked_duplicates=stacked_count,
            family=current_family,
            confidence_label=confidence_label,
            confidence_warning=confidence_warning,
        )

    def _score_result(
        self,
        path: str,
        clip_score: float,
        matched_prompt: str,
        project_meta: Dict[str, Dict],
        active_filters: Optional[Dict] = None,
        people_implied: bool = False,
        structural_score: float = 0.0,
        ocr_score: float = 0.0,
        event_score: float = 0.0,
        screenshot_score: float = 0.0,
        family: Optional[str] = None,
    ) -> ScoredResult:
        """
        Apply the deterministic scoring contract to a single result.
        Delegates to the family-aware Ranker module.
        """
        meta = project_meta.get(path, {})
        # Lazy init for tests that bypass __init__ via __new__
        ranker = getattr(self, '_ranker', None) or Ranker()
        return ranker.score(
            path, clip_score, matched_prompt, meta,
            active_filters, people_implied, family=family,
            structural_score=structural_score,
            ocr_score=ocr_score,
            event_score=event_score,
            screenshot_score=screenshot_score,
        )

    def _get_search_feature_repo(self):
        """Lazy accessor for SearchFeatureRepository."""
        if getattr(self, '_search_feature_repo', None) is None:
            try:
                from repository.search_feature_repository import SearchFeatureRepository
                repo = SearchFeatureRepository()
                if repo.table_exists():
                    self._search_feature_repo = repo
            except Exception:
                pass
        return getattr(self, '_search_feature_repo', None)

    def _get_project_meta(self) -> Dict[str, Dict]:
        """Get project photo metadata with caching (includes flag, dimensions, face counts).

        Fast path: reads from search_asset_features table if populated.
        Fallback: original JOIN-based approach for backward compatibility.
        """
        now = time.time()
        # Resilient to tests that bypass __init__ via __new__
        _cache = getattr(self, '_project_meta_cache', None)
        _cache_time = getattr(self, '_meta_cache_time', 0.0)
        _cache_ttl = getattr(self, '_META_CACHE_TTL', 60.0)
        if (_cache is not None
                and (now - _cache_time) < _cache_ttl):
            return _cache

        # Fast path: use flattened search_asset_features table if available
        repo = self._get_search_feature_repo()
        if repo is not None:
            try:
                meta = repo.get_project_meta(self.project_id)
                if meta:
                    self._project_meta_cache = meta
                    self._meta_cache_time = now
                    logger.debug(
                        f"[SearchOrchestrator] Loaded {len(meta)} rows from "
                        f"search_asset_features (fast path)"
                    )
                    # Validate critical columns are populated
                    self._validate_search_features(meta)
                    return meta
                # Table exists but empty for this project — auto-rebuild
                logger.info(
                    f"[SearchOrchestrator] search_asset_features empty for "
                    f"project {self.project_id}, triggering auto-rebuild"
                )
                rebuilt = repo.refresh_project(self.project_id)
                if rebuilt > 0:
                    meta = repo.get_project_meta(self.project_id)
                    if meta:
                        self._project_meta_cache = meta
                        self._meta_cache_time = now
                        logger.info(
                            f"[SearchOrchestrator] Auto-rebuilt {rebuilt} rows "
                            f"in search_asset_features"
                        )
                        self._validate_search_features(meta)
                        return meta
            except Exception as e:
                logger.debug(f"[SearchOrchestrator] search_asset_features fallback: {e}")

        # Fallback: original JOIN-based approach
        logger.warning(
            "[SearchOrchestrator] search_asset_features unavailable, using slow fallback. "
            "Search purity may be less stable until flattened features are rebuilt."
        )
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, path, rating, flag, width, height, "
                    "gps_latitude, gps_longitude, "
                    "created_date, date_taken "
                    "FROM photo_metadata WHERE project_id = ?",
                    (self.project_id,)
                )
                result = {}
                id_to_path = {}
                for row in cursor.fetchall():
                    path = row['path']
                    lat = row['gps_latitude']
                    lon = row['gps_longitude']
                    result[path] = {
                        "rating": row['rating'],
                        "flag": row['flag'] or "none",
                        "width": row['width'],
                        "height": row['height'],
                        "has_gps": lat is not None and lon is not None and lat != 0 and lon != 0,
                        "created_date": row['created_date'],
                        "date_taken": row['date_taken'],
                        "is_screenshot": self._detect_screenshot(
                            path, row['width'], row['height']
                        ),
                    }
                    id_to_path[row['id']] = path

                # Face counts from face_crops table (populated by face pipeline)
                try:
                    import os as _os
                    face_cursor = conn.execute("""
                        SELECT fc.image_path, COUNT(*) as cnt
                        FROM face_crops fc
                        WHERE fc.project_id = ?
                        GROUP BY fc.image_path
                    """, (self.project_id,))
                    face_rows = face_cursor.fetchall()
                    face_hit_count = 0
                    for frow in face_rows:
                        p = frow['image_path']
                        if not p:
                            continue
                        # Try exact match first, then normalized match
                        if p in result:
                            result[p]["face_count"] = frow['cnt']
                            face_hit_count += 1
                        else:
                            norm_p = _os.path.normpath(p)
                            for rp in result:
                                if _os.path.normpath(rp) == norm_p:
                                    result[rp]["face_count"] = frow['cnt']
                                    face_hit_count += 1
                                    break
                    logger.debug(
                        f"[SearchOrchestrator] face_count: {face_hit_count}/{len(result)} photos "
                        f"({len(face_rows)} face_crops groups)"
                    )
                except Exception:
                    pass  # face_crops table may not exist yet

                self._project_meta_cache = result
                self._meta_cache_time = now
                return result
        except Exception as e:
            logger.warning(f"[SearchOrchestrator] Metadata load failed: {e}")
            return self._project_meta_cache or {}

    @staticmethod
    def _validate_search_features(meta: Dict[str, Dict]) -> None:
        """
        Validate that search_asset_features has critical columns populated.

        Logs warnings when structural signals are missing so that downstream
        search quality degradation is visible in the logs.  This replaces
        the silent fallback that previously masked purity issues.
        """
        if not meta:
            return

        sample_size = min(50, len(meta))
        paths = list(meta.keys())[:sample_size]

        missing_screenshot = 0
        missing_face_count = 0
        missing_dimensions = 0
        missing_ocr = 0
        missing_duplicate_group = 0
        missing_ext = 0
        missing_media_type = 0

        for p in paths:
            m = meta[p]
            if "is_screenshot" not in m:
                missing_screenshot += 1
            if "face_count" not in m:
                missing_face_count += 1
            if not m.get("width") or not m.get("height"):
                missing_dimensions += 1
            if "ocr_text" not in m:
                missing_ocr += 1
            if "duplicate_group_id" not in m:
                missing_duplicate_group += 1
            if "ext" not in m:
                missing_ext += 1
            if "media_type" not in m:
                missing_media_type += 1

        warnings = []
        if missing_screenshot > sample_size * 0.5:
            warnings.append(f"is_screenshot missing on {missing_screenshot}/{sample_size}")
        if missing_face_count > sample_size * 0.5:
            warnings.append(f"face_count missing on {missing_face_count}/{sample_size}")
        if missing_dimensions > sample_size * 0.3:
            warnings.append(f"dimensions missing on {missing_dimensions}/{sample_size}")

        if missing_duplicate_group > 0:
            logger.warning(
                f"[SearchOrchestrator] search_asset_features missing duplicate_group_id "
                f"for {missing_duplicate_group}/{sample_size} rows; duplicate collapse may be weaker."
            )
        if missing_ext > 0:
            logger.warning(
                f"[SearchOrchestrator] search_asset_features missing ext "
                f"for {missing_ext}/{sample_size} rows; category detection may be weaker."
            )
        if missing_media_type > 0:
            logger.warning(
                f"[SearchOrchestrator] search_asset_features missing media_type "
                f"for {missing_media_type}/{sample_size} rows."
            )
        if missing_ocr > 0:
            logger.warning(
                f"[SearchOrchestrator] search_asset_features missing ocr_text column "
                f"for {missing_ocr}/{sample_size} rows."
            )

        if warnings:
            logger.warning(
                f"[SearchOrchestrator] FEATURE_QUALITY_DEGRADED: "
                f"{'; '.join(warnings)}. "
                f"Type/scenic purity may be unstable. "
                f"Rebuild search_asset_features to fix."
            )
        else:
            logger.debug(
                f"[SearchOrchestrator] search_asset_features validated: "
                f"critical columns populated"
            )

    # ── Screenshot Detection (multi-signal confidence) ──
    # Delegates to search_feature_repository._compute_screenshot_confidence
    # for consistent detection logic. Resolution alone is no longer sufficient.

    @classmethod
    def _detect_screenshot(cls, path: str, width, height) -> bool:
        """
        Conservative screenshot detection.

        Delegates to the shared _detect_screenshot function which uses
        filename as the only hard positive. Resolution alone is NOT sufficient.
        """
        from repository.search_feature_repository import _detect_screenshot
        return _detect_screenshot(path, width, height)

    def _plan_from_preset(self, preset_id: str,
                          extra_filters: Optional[Dict]) -> QueryPlan:
        """Build a QueryPlan from a SmartFind preset."""
        preset = self._smart_find._lookup_preset(preset_id)
        if not preset:
            logger.warning(f"[SearchOrchestrator] Unknown preset: {preset_id}")
            return QueryPlan(raw_query=preset_id, source="preset")

        # Read gate_profile from preset (declarative hard filters)
        gp = preset.get("gate_profile", {})

        plan = QueryPlan(
            raw_query=preset.get("name", preset_id),
            source="preset",
            preset_id=preset_id,
            semantic_prompts=list(preset.get("prompts", [])),
            filters=dict(preset.get("filters", {})),
            semantic_weight=preset.get("semantic_weight", 0.8),
            allow_backoff=preset.get("allow_backoff", True),
            threshold_override=preset.get("threshold_override"),
            negative_prompts=list(preset.get("negative_prompts", [])),
            exclude_faces=preset.get("exclude_faces", False) or gp.get("exclude_faces", False),
            # Gate profile fields
            require_screenshot=gp.get("require_screenshot", False),
            exclude_screenshots=gp.get("exclude_screenshots", False),
            require_faces=gp.get("require_faces", False),
            min_face_count=gp.get("min_face_count", 0),
            require_gps_gate=gp.get("require_gps_gate", False),
            min_edge_size=gp.get("min_edge_size", 0),
            require_document_signal=gp.get("require_document_signal", False),
        )

        if extra_filters:
            plan.filters.update(extra_filters)

        return plan

    def _build_label(self, plan: QueryPlan, result: OrchestratorResult) -> str:
        """Build a human-readable label for the search result."""
        parts = []

        if plan.preset_id:
            preset = self._smart_find._lookup_preset(plan.preset_id)
            if preset:
                parts.append(f"{preset.get('icon', '')} {preset['name']}")
        elif plan.raw_query:
            parts.append(f"\U0001f50d {plan.raw_query}")

        if plan.extracted_tokens:
            token_labels = [t["label"] for t in plan.extracted_tokens]
            parts.append(f"[{', '.join(token_labels)}]")

        return ' '.join(parts) if parts else "Search"

    def _log_explainability(self, plan: QueryPlan, result: OrchestratorResult):
        """
        Log top-10 results with score components.

        This is the key debugging tool: every search produces
        a compact, reproducible log of why results ranked as they did.
        """
        top_n = min(10, len(result.scored_results))
        if top_n == 0:
            logger.info(
                f"[SearchOrchestrator] query=\"{plan.raw_query}\" "
                f"| 0 results | {result.execution_time_ms:.0f}ms"
                f"{' | filters=' + str(plan.filters) if plan.filters else ''}"
            )
            return

        # Compact summary line with family-aware weights
        family = result.family or get_preset_family(plan.preset_id)
        weights = get_weights_for_family(family)
        logger.info(
            f'[SearchOrchestrator] query="{plan.raw_query}" '
            f'| {result.total_matches} results | {result.execution_time_ms:.0f}ms '
            f'| family={family} '
            f'| weights=[clip={weights.w_clip:.2f} rec={weights.w_recency:.2f} '
            f'fav={weights.w_favorite:.2f} loc={weights.w_location:.2f} '
            f'face={weights.w_face_match:.2f} struct={weights.w_structural:.2f} '
            f'ocr={weights.w_ocr:.2f} event={weights.w_event:.2f} '
            f'screen={weights.w_screenshot:.2f}]'
            f"{'| backoff' if result.backoff_applied else ''}"
            f"{' | filters=' + str(plan.filters) if plan.filters else ''}"
        )

        # Top-N breakdown
        for i, sr in enumerate(result.scored_results[:top_n]):
            import os
            basename = os.path.basename(sr.path)
            components = (
                f"clip={sr.clip_score:.3f} rec={sr.recency_score:.3f} "
                f"fav={sr.favorite_score:.2f} loc={sr.location_score:.1f} "
                f"face={sr.face_match_score:.1f} struct={sr.structural_score:.3f} "
                f"ocr={sr.ocr_score:.3f} event={sr.event_score:.3f} "
                f"screen={sr.screenshot_score:.3f}"
            )
            dup_tag = f" [+{sr.duplicate_count}]" if sr.duplicate_count else ""
            logger.info(
                f"  #{i+1} {sr.final_score:.4f} | {components} | {basename}{dup_tag}"
            )

        # Facets summary
        if result.facets:
            facet_summary = {k: dict(v) for k, v in result.facets.items()}
            logger.info(f"  facets: {facet_summary}")

    def invalidate_meta_cache(self):
        """Invalidate the metadata cache (call after scans, rating changes, etc.)."""
        self._project_meta_cache = None
        self._meta_cache_time = 0.0

    def _log_embedding_quality(self):
        """One-time diagnostic: log current embedding model and quality tier."""
        self._embedding_quality_logged = True
        try:
            from repository.project_repository import ProjectRepository
            proj_repo = ProjectRepository()
            model_name = proj_repo.get_semantic_model(self.project_id) or \
                "openai/clip-vit-base-patch32"

            # Detect dimension from a sample embedding
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                row = conn.execute("""
                    SELECT se.dim
                    FROM semantic_embeddings se
                    JOIN photo_metadata pm ON se.photo_id = pm.id
                    WHERE pm.project_id = ?
                    LIMIT 1
                """, (self.project_id,)).fetchone()
                dim = abs(row['dim']) if row else 0

            tier = self._MODEL_QUALITY_TIERS.get(dim, f"unknown ({dim}-D)")
            logger.info(
                f"[SearchOrchestrator] Embedding model: {model_name} | "
                f"dimension: {dim}-D | quality tier: {tier}"
            )
            if 0 < dim <= 512:
                logger.info(
                    f"[SearchOrchestrator] Semantic quality ceiling: base model "
                    f"({dim}-D). For better search separation, re-extract with "
                    f"clip-vit-large-patch14 (768-D) via Tools > Extract Embeddings."
                )
        except Exception as e:
            logger.debug(f"[SearchOrchestrator] Embedding quality check skipped: {e}")

    # ── Gate Engine (hard pre-filters before dedup and top_k) ──

    def _apply_gates(
        self,
        scored: List[ScoredResult],
        plan: QueryPlan,
        project_meta: Dict[str, Dict],
        builder_evidence: Optional[Dict[str, dict]] = None,
    ) -> List[ScoredResult]:
        """
        Hard filters applied after scoring but before dedup and top_k slicing.
        Delegates to the extracted GateEngine module.
        """
        # Lazy init for tests that bypass __init__ via __new__
        gate_engine = getattr(self, '_gate_engine', None) or GateEngine()
        kept, _dropped = gate_engine.apply(scored, plan, project_meta, builder_evidence)
        return kept

    # ── Preset Family Classification (delegated to services.ranker) ──

    _PRESET_FAMILIES = PRESET_FAMILIES  # Backward-compat class attribute

    @classmethod
    def _get_preset_family(cls, preset_id: str) -> str:
        """Get the preset family for gate profile selection."""
        return get_preset_family(preset_id)

    # ── Structural Scoring (first-class scoring term) ──

    def _compute_structural_scores(
        self,
        plan: QueryPlan,
        project_meta: Dict[str, Dict],
        ocr_match_paths: set,
    ) -> Dict[str, float]:
        """
        Compute structural scores for type-family presets.

        Returns {path: structural_score} where score is in [-1.0, 1.0]
        internally, then clamped to [-1.0, 1.0].  This score is multiplied
        by w_structural in the weighted scoring contract.

        For Documents:
        - Document-native extensions get strong positive boost
        - Image extensions (.jpg/.jpeg/.heic) get strong negative penalty
        - Faces and screenshots are strong negatives
        - OCR evidence is the strongest positive signal
        - Page-like geometry is a moderate positive

        For Screenshots:
        - is_screenshot flag is the primary signal
        """
        import os
        from services.gate_engine import GateEngine

        scores: Dict[str, float] = {}

        # Document-native extensions
        _DOC_EXTENSIONS = frozenset({'.pdf', '.png', '.tiff', '.tif', '.bmp'})
        # Photo-like extensions (need strong content evidence)
        _PHOTO_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.heic', '.heif', '.webp', '.cr2', '.nef', '.arw'})

        is_documents = (plan.preset_id == "documents")
        is_screenshots = (plan.preset_id == "screenshots")

        for path, meta in project_meta.items():
            score = 0.0
            w = meta.get("width", 0) or 0
            h = meta.get("height", 0) or 0
            ext = os.path.splitext(path)[1].lower() if path else ""
            face_count = int(meta.get("face_count") or 0)
            is_screenshot = bool(meta.get("is_screenshot", False))

            if is_documents:
                has_ocr = GateEngine.has_document_ocr_signal(meta)
                page_like = GateEngine.is_page_like(meta)
                min_edge = min(w, h) if w and h else 0

                # Extension signals
                if ext in _DOC_EXTENSIONS:
                    score += 0.35
                if ext in _PHOTO_EXTENSIONS:
                    score -= 0.25

                # Content signals
                if face_count > 0:
                    score -= 0.45
                if is_screenshot:
                    score -= 0.40
                if min_edge < 700:
                    score -= 0.12
                if page_like:
                    score += 0.18
                if has_ocr:
                    score += 0.42
                if path in ocr_match_paths:
                    score += 0.15

                score = max(-1.0, min(1.0, score))

            elif is_screenshots:
                if is_screenshot:
                    score += 0.70
                else:
                    score -= 0.50
                if face_count > 0:
                    score -= 0.10
                score = max(-1.0, min(1.0, score))

            else:
                score = 0.0  # Neutral for other type-family presets

            scores[path] = score

        computed = sum(1 for v in scores.values() if v != 0.0)
        if computed:
            logger.info(
                f"[SearchOrchestrator] Structural scores computed for "
                f"{computed}/{len(scores)} assets "
                f"(preset={plan.preset_id})"
            )

        return scores

    def _compute_ocr_scores(
        self,
        plan: QueryPlan,
        project_meta: Dict[str, Dict],
    ) -> Dict[str, float]:
        """
        Compute OCR text relevance scores for type-family presets.

        Returns {path: ocr_score} where ocr_score is 0..1.
        This score is multiplied by w_ocr in the weighted scoring
        contract, making it a first-class ranking term.

        For Documents: normalized signal from text length + lexicon hits.
        For Screenshots: looks for UI-related terms (battery, wifi, etc.)
        """
        preset = (plan.preset_id or "").lower()
        family = get_preset_family(preset)

        if family != "type":
            return {}

        scores: Dict[str, float] = {}

        for path, meta in project_meta.items():
            ocr_text = (meta.get("ocr_text") or "").strip()
            if not ocr_text:
                continue

            ocr_lower = ocr_text.lower()
            score = 0.0

            if preset == "documents":
                doc_terms = (
                    "invoice", "receipt", "bill", "total", "amount", "date",
                    "address", "account", "iban", "customer", "page",
                    "signature", "form", "application", "reference",
                )
                hit_count = sum(1 for t in doc_terms if t in ocr_lower)
                length_score = min(len(ocr_text) / 80.0, 1.0)
                term_score = min(hit_count / 3.0, 1.0)
                score = min(1.0, 0.45 * length_score + 0.55 * term_score)

            elif preset == "screenshots":
                ui_terms = (
                    "battery", "wifi", "lte", "5g", "notification",
                    "settings", "search", "cancel", "back", "menu",
                    "home", "share", "download",
                )
                hit_count = sum(1 for t in ui_terms if t in ocr_lower)
                score = min(1.0, len(ocr_text) / 120.0)
                if hit_count > 0:
                    score = min(1.0, score + hit_count * 0.10)
            else:
                continue

            if score > 0:
                scores[path] = score

        if scores:
            logger.info(
                f"[SearchOrchestrator] OCR scores computed for "
                f"{len(scores)} assets (preset={preset})"
            )

        return scores

    def _compute_scenic_anti_type_scores(
        self,
        plan: QueryPlan,
        project_meta: Dict[str, Dict],
    ) -> Dict[str, float]:
        """
        Compute soft anti-type structural penalties for scenic presets.

        Scenic families do not use hard document exclusion (kills recall),
        but they apply a soft negative score for assets that look type-like.
        This prevents doc2.png from appearing in Travel results.

        The scenic family has w_structural=0.00, so these penalties are
        applied as direct score adjustments after initial scoring.
        We store them so they can be logged, and apply them in Step 5b
        via a small additive penalty.
        """
        import os

        _DOC_EXTENSIONS = frozenset({'.pdf', '.png', '.tif', '.tiff', '.bmp'})

        scores: Dict[str, float] = {}
        for path, meta in project_meta.items():
            penalty = 0.0
            ext = os.path.splitext(path)[1].lower() if path else ""
            ocr_text = meta.get("ocr_text", "") or ""
            ocr_len = len(ocr_text)
            w = meta.get("width", 0) or 0
            h = meta.get("height", 0) or 0

            if ext in _DOC_EXTENSIONS:
                penalty += SCENIC_ANTI_TYPE_PENALTIES["doc_extension"]

            if ocr_len > SCENIC_OCR_TEXT_PENALTY_THRESHOLD:
                penalty += SCENIC_ANTI_TYPE_PENALTIES["high_ocr_text"]

            # Scan/page-like aspect ratio (tall narrow)
            if w > 0 and h > 0:
                ratio = max(w, h) / min(w, h)
                if 1.3 < ratio < 1.5:  # A4/letter-like
                    penalty += SCENIC_ANTI_TYPE_PENALTIES["scan_aspect"]

            if penalty != 0.0:
                scores[path] = penalty

        if scores:
            logger.info(
                f"[SearchOrchestrator] Scenic anti-type penalties computed for "
                f"{len(scores)} assets (preset={plan.preset_id})"
            )

        return scores

    # ── Duplicate Stacking (delegated to services.deduplicator) ──

    @staticmethod
    def _is_copy_filename(path: str) -> bool:
        """Detect filenames that look like copies. Delegates to deduplicator module."""
        return is_copy_filename(path)

    def _collapse_duplicate_families_for_scenic(
        self,
        scored_results: List[ScoredResult],
        project_meta: Dict[str, Dict],
    ) -> List[ScoredResult]:
        """
        Collapse duplicate-family scenic results so one duplicate cluster
        does not dominate the top of the list.
        """
        if not scored_results:
            return scored_results

        kept = []
        seen_groups = set()
        seen_basenames = set()

        for r in scored_results:
            meta = project_meta.get(r.path, {}) or {}
            dup_group = meta.get("duplicate_group_id")
            basename = os.path.basename(r.path).lower()

            if dup_group:
                key = f"group:{dup_group}"
                if key in seen_groups:
                    continue
                seen_groups.add(key)
                kept.append(r)
                continue

            # Lightweight fallback collapse for repeated copy families
            stem = basename
            if is_copy_filename(basename):
                stem = re.sub(r'(?i)\s*\(\d+\)|\s*copy|\s*-\s*copy', '', basename).strip()

            if stem in seen_basenames:
                continue
            seen_basenames.add(stem)
            kept.append(r)

        return kept

    def _deduplicate_results(self, scored: List[ScoredResult]) -> Tuple[List[ScoredResult], int]:
        """
        Stack duplicate results: keep best representative per duplicate group.
        Delegates to the extracted Deduplicator module.
        """
        project_meta = self._get_project_meta()
        # Lazy init for tests that bypass __init__ via __new__
        dedup = getattr(self, '_deduplicator', None)
        if dedup is None:
            dedup = Deduplicator(getattr(self, 'project_id', 0))
        # Backward compat: if tests set _dup_cache directly on the orchestrator,
        # propagate it to the deduplicator
        legacy_cache = getattr(self, '_dup_cache', None)
        if legacy_cache is not None:
            dedup._dup_cache = legacy_cache
            dedup._dup_cache_time = getattr(self, '_dup_cache_time', 0.0)
        return dedup.deduplicate(scored, project_meta)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize a file path for consistent comparison.

        Handles trailing slashes, redundant separators, and OS-specific
        differences so that the same physical file always maps to one key.
        """
        import os
        return os.path.normpath(path)

    def _apply_negative_prompt_penalty(
        self, scored: List[ScoredResult], plan: QueryPlan,
        project_meta: Dict[str, Dict],
    ) -> List[ScoredResult]:
        """
        Penalize results that match negative prompts.

        Soft penalty: negative CLIP prompt scores reduce final_score.
        This prevents 'text'-like images (chat screenshots, UI, menus)
        from polluting document-type searches.

        Note: Hard exclusions (screenshots, faces) are now handled by
        _apply_gates() which runs after this method.
        """
        neg_prompts = plan.negative_prompts
        if not neg_prompts:
            return scored

        # Soft penalty from negative CLIP prompts
        try:
            neg_hits = self._smart_find._run_clip_multi_prompt(
                neg_prompts, top_k=600, threshold=0.18, fusion_mode="max"
            )
        except Exception:
            neg_hits = {}

        if neg_hits:
            neg_penalty_weight = 0.15  # Scale of penalty
            penalized = 0

            # neg_hits is keyed by photo_id; do path→id lookup from DB
            try:
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                with db.get_connection() as conn:
                    scored_paths = [sr.path for sr in scored]
                    if scored_paths:
                        placeholders = ",".join("?" * len(scored_paths))
                        rows = conn.execute(
                            f"SELECT id, path FROM photo_metadata "
                            f"WHERE project_id = ? AND path IN ({placeholders})",
                            [self.project_id] + scored_paths,
                        ).fetchall()
                        path_to_id = {r['path']: r['id'] for r in rows}

                        for sr in scored:
                            pid = path_to_id.get(sr.path)
                            if pid and pid in neg_hits:
                                neg_score, neg_prompt = neg_hits[pid]
                                penalty = neg_penalty_weight * neg_score
                                sr.final_score = max(0, sr.final_score - penalty)
                                sr.reasons.append(f"(neg:{neg_prompt}:-{penalty:.3f})")
                                penalized += 1
            except Exception as e:
                logger.debug(f"[SearchOrchestrator] Negative prompt lookup failed: {e}")

            if penalized:
                scored.sort(key=lambda r: r.final_score, reverse=True)
                logger.info(
                    f"[SearchOrchestrator] Negative prompt penalty applied to "
                    f"{penalized} results"
                )

        return scored

    @staticmethod
    def _enforce_unique_paths(scored: List[ScoredResult]) -> List[ScoredResult]:
        """
        Final safety net: ensure no path appears more than once.

        Input must be sorted by score (descending). Keeps the first (highest-
        scored) occurrence of each normalized path and drops later duplicates.
        Logs how many duplicates were removed for debugging.
        """
        import os
        seen: set = set()
        unique: List[ScoredResult] = []
        dropped = 0
        for sr in scored:
            norm = os.path.normpath(sr.path)
            if norm not in seen:
                seen.add(norm)
                unique.append(sr)
            else:
                dropped += 1
        if dropped > 0:
            logger.info(
                f"[SearchOrchestrator] _enforce_unique_paths: "
                f"dropped {dropped} duplicate path(s) "
                f"({len(scored)} → {len(unique)})"
            )
        return unique

    # ── Progressive Search (metadata first, then semantic) ──

    def search_metadata_only(self, query: str, top_k: int = 200,
                             extra_filters: Optional[Dict] = None) -> OrchestratorResult:
        """
        Phase 1 of progressive search: metadata-only results.

        Returns instantly (<50ms) with filter-matched results.
        No CLIP/semantic scoring - just structured tokens + NL date/rating
        extraction + SQL metadata filters. Results scored by recency/fav/location
        only (clip_score=0 for all).

        The UI should call this first, then call search() for full results.
        """
        start = time.time()
        plan = TokenParser.parse(query)
        if extra_filters:
            plan.filters.update(extra_filters)

        # Force metadata-only: even if there's semantic text, only use filters
        project_meta = self._get_project_meta()
        people_implied = self._is_people_implied(plan)
        scored: List[ScoredResult] = []

        family = get_preset_family(plan.preset_id)
        if plan.has_filters():
            metadata_paths = self._smart_find._run_metadata_filter(plan.filters)
            for path in metadata_paths[:top_k]:
                sr = self._score_result(path, 0.0, "", project_meta,
                                        plan.filters, people_implied,
                                        family=family)
                scored.append(sr)
        else:
            # No explicit filters from tokens - show recent photos as baseline
            all_paths = list(project_meta.keys())
            # Sort by date (most recent first)
            def _date_key(p):
                m = project_meta.get(p, {})
                d = m.get("created_date") or m.get("date_taken") or ""
                return str(d)
            all_paths.sort(key=_date_key, reverse=True)
            for path in all_paths[:top_k]:
                sr = self._score_result(path, 0.0, "", project_meta,
                                        plan.filters, people_implied,
                                        family=family)
                scored.append(sr)

        scored.sort(key=lambda r: r.final_score, reverse=True)
        scored = self._apply_gates(scored, plan, project_meta)
        scored = self._enforce_unique_paths(scored)
        scored = scored[:top_k]

        result_paths = [r.path for r in scored]
        facets = FacetComputer.compute(result_paths, project_meta)

        result = OrchestratorResult(
            paths=result_paths,
            total_matches=len(result_paths),
            scored_results=scored,
            scores={r.path: r.final_score for r in scored},
            facets=facets,
            query_plan=plan,
            execution_time_ms=(time.time() - start) * 1000,
            label=self._build_label(plan, OrchestratorResult(paths=result_paths)),
            phase="metadata",
            phase_label="Metadata results",
        )
        logger.info(
            f"[SearchOrchestrator] Progressive phase=metadata: "
            f"query=\"{query}\" → {len(result_paths)} results in "
            f"{result.execution_time_ms:.0f}ms"
        )
        return result

    # ── Find Similar (Excire-style image-to-image) ──

    def find_similar(self, photo_path: str, top_k: int = 50,
                     threshold: float = 0.5) -> OrchestratorResult:
        """
        Find photos visually similar to a reference photo.

        Uses CLIP embeddings + FAISS/numpy cosine similarity (not text query).
        Returns results through the same OrchestratorResult interface so
        facets, scoring, and UI all work identically.

        Args:
            photo_path: Path to the reference photo
            top_k: Maximum results
            threshold: Minimum similarity (0-1)
        """
        start = time.time()

        if not _numpy_available:
            return OrchestratorResult(label="Find Similar - numpy not available")

        try:
            from services.semantic_embedding_service import get_semantic_embedding_service
            from repository.project_repository import ProjectRepository
            from repository.base_repository import DatabaseConnection

            # Get canonical model
            proj_repo = ProjectRepository()
            model_name = proj_repo.get_semantic_model(self.project_id) or \
                "openai/clip-vit-base-patch32"
            service = get_semantic_embedding_service(model_name=model_name)

            if not service._available:
                logger.warning("[SearchOrchestrator] find_similar: semantic service not available")
                return OrchestratorResult(label="Find Similar - AI not available")

            # Get reference photo's embedding
            db = DatabaseConnection()
            ref_photo_id = None
            ref_embedding = None

            with db.get_connection() as conn:
                # Look up photo_id from path
                row = conn.execute(
                    "SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?",
                    (photo_path, self.project_id)
                ).fetchone()
                if not row:
                    return OrchestratorResult(label="Find Similar - Photo not found")
                ref_photo_id = row['id']

                # Get its embedding
                emb_row = conn.execute(
                    "SELECT embedding FROM semantic_embeddings WHERE photo_id = ?",
                    (ref_photo_id,)
                ).fetchone()
                if not emb_row:
                    return OrchestratorResult(label="Find Similar - No embedding (run Extract Embeddings)")
                ref_embedding = np.frombuffer(emb_row['embedding'], dtype=np.float32)
                if len(ref_embedding) == 0:
                    ref_embedding = np.frombuffer(emb_row['embedding'], dtype=np.float16).astype(np.float32)

                # Get all project embeddings
                rows = conn.execute("""
                    SELECT se.photo_id, se.embedding, pm.path
                    FROM semantic_embeddings se
                    JOIN photo_metadata pm ON se.photo_id = pm.id
                    WHERE pm.project_id = ?
                """, (self.project_id,)).fetchall()

            if not rows:
                return OrchestratorResult(label="Find Similar - No embeddings")

            # Build embedding dict
            embeddings = {}
            path_by_id = {}
            for row in rows:
                try:
                    emb = np.frombuffer(row['embedding'], dtype=np.float32)
                    if len(emb) == 0:
                        emb = np.frombuffer(row['embedding'], dtype=np.float16).astype(np.float32)
                    if len(emb) > 0:
                        embeddings[row['photo_id']] = emb
                        path_by_id[row['photo_id']] = row['path']
                except Exception:
                    continue

            # Use the embedding service's find_similar_photos
            similar_results = service.find_similar_photos(
                query_embedding=ref_embedding,
                embeddings=embeddings,
                top_k=top_k,
                threshold=threshold,
                exclude_photo_id=ref_photo_id,
            )

            # Score through orchestrator pipeline
            project_meta = self._get_project_meta()
            scored: List[ScoredResult] = []
            import os
            ref_basename = os.path.basename(photo_path)

            for photo_id, sim_score in similar_results:
                path = path_by_id.get(photo_id, "")
                if not path:
                    continue
                sr = self._score_result(
                    path, sim_score, f"similar to {ref_basename}", project_meta
                )
                scored.append(sr)

            similar_plan = QueryPlan(
                raw_query=f"similar:{ref_basename}",
                source="similar",
                semantic_text=f"similar to {ref_basename}",
            )
            scored.sort(key=lambda r: r.final_score, reverse=True)
            scored = self._apply_gates(scored, similar_plan, project_meta)
            scored = self._enforce_unique_paths(scored)

            result_paths = [r.path for r in scored]
            facets = FacetComputer.compute(result_paths, project_meta)

            result = OrchestratorResult(
                paths=result_paths,
                total_matches=len(result_paths),
                scored_results=scored,
                scores={r.path: r.final_score for r in scored},
                facets=facets,
                query_plan=similar_plan,
                execution_time_ms=(time.time() - start) * 1000,
                label=f"\U0001f3af Similar to {ref_basename}",
            )
            self._log_explainability(result.query_plan, result)
            return result

        except Exception as e:
            logger.error(f"[SearchOrchestrator] find_similar failed: {e}", exc_info=True)
            return OrchestratorResult(label=f"Find Similar - Error: {e}")

    # ── ANN Retrieval (two-stage: FAISS candidate → full scoring) ──

    _ann_index_cache: Dict[int, Tuple] = {}  # class-level: {project_id: (index, photo_ids, vectors, timestamp)}
    _ann_dirty: set = set()  # class-level: project_ids with new embeddings since last build
    _ANN_CACHE_TTL = 300.0  # 5 minutes

    def _get_or_build_ann_index(self):
        """
        Get or build a FAISS/numpy ANN index for the project.

        Caches the index for _ANN_CACHE_TTL seconds.
        Rebuilds immediately if marked dirty (new embeddings added).
        For projects with >500 embeddings and FAISS available,
        uses FAISS IndexFlatIP for O(log n) retrieval.
        """
        if not _numpy_available:
            return None, [], {}

        now = time.time()
        is_dirty = self.project_id in SearchOrchestrator._ann_dirty
        cached = SearchOrchestrator._ann_index_cache.get(self.project_id)
        if cached and not is_dirty and (now - cached[3]) < self._ANN_CACHE_TTL:
            return cached[0], cached[1], cached[2]

        if is_dirty:
            logger.info(f"[SearchOrchestrator] ANN index dirty (new embeddings) — rebuilding for project {self.project_id}")
            SearchOrchestrator._ann_dirty.discard(self.project_id)

        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()

            with db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT se.photo_id, se.embedding, pm.path
                    FROM semantic_embeddings se
                    JOIN photo_metadata pm ON se.photo_id = pm.id
                    WHERE pm.project_id = ?
                """, (self.project_id,)).fetchall()

            if not rows:
                return None, [], {}

            photo_ids = []
            vectors = []
            path_lookup = {}

            for row in rows:
                try:
                    emb = np.frombuffer(row['embedding'], dtype=np.float32)
                    if len(emb) == 0:
                        emb = np.frombuffer(row['embedding'], dtype=np.float16).astype(np.float32)
                    if len(emb) > 0:
                        photo_ids.append(row['photo_id'])
                        vectors.append(emb)
                        path_lookup[row['photo_id']] = row['path']
                except Exception:
                    continue

            if not vectors:
                return None, [], {}

            vectors_np = np.vstack(vectors).astype('float32')
            # Normalize for cosine similarity
            norms = np.linalg.norm(vectors_np, axis=1, keepdims=True)
            vectors_np = vectors_np / np.maximum(norms, 1e-8)

            n_vectors = len(photo_ids)
            dim = vectors_np.shape[1]

            if _faiss_available and n_vectors >= 500:
                index = _faiss.IndexFlatIP(dim)
                index.add(vectors_np)
                index_data = ('faiss', index, vectors_np)
                logger.info(
                    f"[SearchOrchestrator] Built FAISS ANN index: "
                    f"{n_vectors} vectors, dim={dim}"
                )
            else:
                index_data = ('numpy', None, vectors_np)
                logger.debug(
                    f"[SearchOrchestrator] Using numpy brute-force: "
                    f"{n_vectors} vectors"
                )

            SearchOrchestrator._ann_index_cache[self.project_id] = (
                index_data, photo_ids, path_lookup, now
            )
            return index_data, photo_ids, path_lookup

        except Exception as e:
            logger.error(f"[SearchOrchestrator] ANN index build failed: {e}")
            return None, [], {}

    def search_ann(self, query_text: str, top_k: int = 200,
                   extra_filters: Optional[Dict] = None) -> OrchestratorResult:
        """
        Two-stage ANN search: FAISS candidate retrieval → full scoring.

        Stage 1: Encode query with CLIP, use FAISS for fast top-K candidates
        Stage 2: Apply full scoring contract (recency, favorites, etc.)

        Falls back to standard search() if no ANN index is available.
        """
        index_data, photo_ids, path_lookup = self._get_or_build_ann_index()
        if index_data is None or not photo_ids:
            # Fallback to standard search
            return self.search(query_text, top_k, extra_filters)

        start = time.time()
        plan = TokenParser.parse(query_text)
        if extra_filters:
            plan.filters.update(extra_filters)

        try:
            from services.semantic_embedding_service import get_semantic_embedding_service
            from repository.project_repository import ProjectRepository

            proj_repo = ProjectRepository()
            model_name = proj_repo.get_semantic_model(self.project_id) or \
                "openai/clip-vit-base-patch32"
            service = get_semantic_embedding_service(model_name=model_name)

            if not service._available or not plan.has_semantic():
                return self.search(query_text, top_k, extra_filters)

            # Stage 1: Encode query and retrieve candidates via ANN
            semantic_text = plan.semantic_text or (
                plan.semantic_prompts[0] if plan.semantic_prompts else ""
            )
            if not semantic_text:
                return self.search(query_text, top_k, extra_filters)

            query_emb = service.encode_text(semantic_text)
            if query_emb is None:
                logger.error("[SearchOrchestrator] encode_text returned None for: %r", semantic_text)
                return self.search(query_text, top_k, extra_filters)
            query_emb = query_emb.astype('float32')
            query_norm = np.linalg.norm(query_emb)
            if query_norm > 0:
                query_emb = query_emb / query_norm
            query_emb = query_emb.reshape(1, -1)

            index_type, index, vectors = index_data
            candidate_k = min(top_k * 3, len(photo_ids))

            ann_hits = {}  # {photo_id: (score, query_text)}

            if index_type == 'faiss' and _faiss_available:
                sims, indices = index.search(query_emb, candidate_k)
                for sim, idx in zip(sims[0], indices[0]):
                    if 0 <= idx < len(photo_ids):
                        ann_hits[photo_ids[idx]] = (float(sim), semantic_text)
            else:
                sims = np.dot(vectors, query_emb.T).flatten()
                if len(sims) > candidate_k:
                    top_idx = np.argpartition(sims, -candidate_k)[-candidate_k:]
                else:
                    top_idx = np.arange(len(sims))
                for idx in top_idx:
                    ann_hits[photo_ids[idx]] = (float(sims[idx]), semantic_text)

            # Stage 2: Apply metadata filters + full scoring
            metadata_candidate_paths = None
            if plan.has_filters():
                metadata_paths = self._smart_find._run_metadata_filter(plan.filters)
                metadata_candidate_paths = set(metadata_paths)

            project_meta = self._get_project_meta()
            people_implied = self._is_people_implied(plan)
            cfg = self._smart_find._get_config()
            threshold = cfg["threshold"]
            scored: List[ScoredResult] = []

            family = get_preset_family(plan.preset_id)
            for photo_id, (sem_score, prompt) in ann_hits.items():
                if sem_score < threshold:
                    continue
                path = path_lookup.get(photo_id)
                if not path:
                    continue
                if metadata_candidate_paths is not None and path not in metadata_candidate_paths:
                    continue
                sr = self._score_result(path, sem_score, prompt, project_meta,
                                        plan.filters, people_implied,
                                        family=family)
                scored.append(sr)

            scored.sort(key=lambda r: r.final_score, reverse=True)
            scored = self._apply_gates(scored, plan, project_meta)
            scored = self._enforce_unique_paths(scored)
            scored = scored[:top_k]

            result_paths = [r.path for r in scored]
            facets = FacetComputer.compute(result_paths, project_meta)

            result = OrchestratorResult(
                paths=result_paths,
                total_matches=len(result_paths),
                scored_results=scored,
                scores={r.path: r.final_score for r in scored},
                facets=facets,
                query_plan=plan,
                execution_time_ms=(time.time() - start) * 1000,
                label=self._build_label(plan, OrchestratorResult(paths=result_paths)),
            )
            self._log_explainability(plan, result)
            logger.info(
                f"[SearchOrchestrator] ANN search: "
                f"index_type={index_type}, candidates={len(ann_hits)}, "
                f"final={len(scored)}, {result.execution_time_ms:.0f}ms"
            )
            return result

        except Exception as e:
            logger.error(f"[SearchOrchestrator] ANN search failed, falling back: {e}")
            return self.search(query_text, top_k, extra_filters)

    def invalidate_ann_cache(self):
        """Invalidate ANN index cache (call after embedding extraction)."""
        SearchOrchestrator._ann_index_cache.pop(self.project_id, None)
        SearchOrchestrator._ann_dirty.discard(self.project_id)

    @classmethod
    def mark_ann_dirty(cls, project_id: int):
        """
        Mark ANN index as dirty for a project.

        Call this after new embeddings are generated or backfilled.
        The next search_ann() call will rebuild the index instead of
        serving a stale cached copy that's missing the new vectors.
        """
        cls._ann_dirty.add(project_id)
        logger.info(f"[SearchOrchestrator] ANN index marked dirty for project {project_id}")

    # ── Relevance Feedback (search_events + personal boost) ──

    @staticmethod
    def record_search_event(project_id: int, query_hash: str,
                            asset_path: str, action: str):
        """
        Record a user interaction with a search result for relevance feedback.

        Actions: 'click', 'open', 'add_to_album', 'favorite_toggle', 'share'.
        This data powers a future personal_relevance scoring term.
        """
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO search_events
                        (project_id, query_hash, asset_path, action)
                    VALUES (?, ?, ?, ?)
                """, (project_id, query_hash, asset_path, action))
                conn.commit()
        except Exception as e:
            # Table may not exist yet; log and move on
            logger.debug(f"[SearchOrchestrator] record_search_event: {e}")

    @staticmethod
    def get_personal_boost(project_id: int, query_hash: str,
                           path: str) -> float:
        """
        Compute a capped personal relevance boost from past interactions.

        Returns a value in [0.0, 0.15] based on how often this asset
        was clicked/opened for similar queries.
        """
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM search_events
                    WHERE project_id = ? AND query_hash = ? AND asset_path = ?
                """, (project_id, query_hash, path)).fetchone()
                if row and row['cnt'] > 0:
                    # Capped log boost: 1 interaction = 0.05, 3 = 0.10, 10+ = 0.15
                    return min(0.15, 0.05 * math.log2(1 + row['cnt']))
        except Exception:
            pass
        return 0.0

    # ── Query Autocomplete (history + library stats) ──

    @staticmethod
    def autocomplete(project_id: int, prefix: str,
                     max_results: int = 8) -> List[Dict[str, str]]:
        """
        Generate autocomplete suggestions from search history + library stats.

        Returns a list of {"label": "...", "query": "...", "source": "..."} dicts
        combining:
        1. Recent queries matching the prefix (from search_history table)
        2. Library-based suggestions from LibraryAnalyzer
        3. Token completions (type:, date:, rating:, has:, is:, ext:, person:)
        """
        suggestions: List[Dict[str, str]] = []
        prefix_lower = prefix.strip().lower()

        if not prefix_lower:
            return suggestions

        # 1. Search history matches
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT DISTINCT query FROM search_history
                    WHERE project_id = ? AND LOWER(query) LIKE ?
                    ORDER BY timestamp DESC
                    LIMIT 4
                """, (project_id, f"{prefix_lower}%")).fetchall()
                for row in rows:
                    suggestions.append({
                        "label": row['query'],
                        "query": row['query'],
                        "source": "history",
                    })
        except Exception:
            pass

        # 2. Token completions
        token_prefixes = {
            "type:": ["type:video", "type:photo"],
            "is:": ["is:fav", "is:favorite"],
            "has:": ["has:location", "has:gps", "has:faces"],
            "date:": ["date:2024", "date:2025", "date:2026"],
            "rating:": ["rating:3", "rating:4", "rating:5"],
            "ext:": ["ext:jpg", "ext:png", "ext:heic", "ext:raw"],
            "person:": [],
        }
        for tok_prefix, completions in token_prefixes.items():
            if tok_prefix.startswith(prefix_lower) or prefix_lower.startswith(tok_prefix):
                for comp in completions:
                    if comp.lower().startswith(prefix_lower) and len(suggestions) < max_results:
                        suggestions.append({
                            "label": comp,
                            "query": comp,
                            "source": "token",
                        })

        # 3. Library suggestions (if prefix is long enough for semantic matching)
        if len(prefix_lower) >= 3:
            try:
                lib_suggestions = LibraryAnalyzer.suggest(project_id, max_suggestions=4)
                for s in lib_suggestions:
                    label = s.get("label", "")
                    if prefix_lower in label.lower() and len(suggestions) < max_results:
                        suggestions.append({
                            "label": label,
                            "query": s.get("query", ""),
                            "source": "library",
                        })
            except Exception:
                pass

        return suggestions[:max_results]

    # ── OCR Search Integration Point ──

    @staticmethod
    def search_ocr_text(project_id: int, query: str,
                        limit: int = 50) -> List[str]:
        """
        Search photos by OCR text content using FTS5.

        Returns a list of photo paths matching the query in their
        extracted OCR text. The FTS5 table (ocr_fts5) is populated
        by the OCR pipeline during background processing.

        Falls back gracefully if the ocr_text column or FTS5 table
        doesn't exist yet.
        """
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            # Sanitize query for FTS5: quote each token to avoid syntax
            # errors from special characters like & | ( ) " * etc.
            import re
            tokens = re.findall(r'\w+', query)
            if not tokens:
                return []
            fts_query = " OR ".join(f'"{t}"' for t in tokens)
            with db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT pm.path
                    FROM ocr_fts5 fts
                    JOIN photo_metadata pm ON fts.rowid = pm.id
                    WHERE fts.ocr_text MATCH ? AND pm.project_id = ?
                    LIMIT ?
                """, (fts_query, project_id, limit)).fetchall()
                return [row['path'] for row in rows]
        except Exception:
            # FTS5 table not created yet — OCR pipeline hasn't run
            return []


# ══════════════════════════════════════════════════════════════════════
# Library Analyzer - suggested searches from library stats
# ══════════════════════════════════════════════════════════════════════

class LibraryAnalyzer:
    """
    Analyze library metadata to generate contextual search suggestions.

    Produces "suggested searches" chips based on what's actually in the
    library - inspired by Google Photos' auto-generated albums and
    Excire's tag suggestions.

    Only suggests what exists (no "Sunsets" if there are no sunset photos).
    """

    @staticmethod
    def suggest(project_id: int, max_suggestions: int = 8) -> List[Dict[str, str]]:
        """
        Generate search suggestions from library metadata stats.

        Returns list of suggestion dicts:
            [
                {"label": "2024 Photos (1,234)", "query": "date:2024", "icon": "📅"},
                {"label": "Favorites (56)", "query": "is:fav", "icon": "⭐"},
                {"label": "iPhone shots (890)", "query": "camera:iPhone", "icon": "📱"},
                ...
            ]
        """
        suggestions = []

        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()

            with db.get_connection() as conn:
                # 1. Year distribution - suggest top years
                year_rows = conn.execute("""
                    SELECT
                        SUBSTR(COALESCE(created_date, date_taken), 1, 4) as year,
                        COUNT(*) as cnt
                    FROM photo_metadata
                    WHERE project_id = ?
                      AND COALESCE(created_date, date_taken) IS NOT NULL
                      AND LENGTH(COALESCE(created_date, date_taken)) >= 4
                    GROUP BY year
                    HAVING cnt >= 5
                    ORDER BY cnt DESC
                    LIMIT 4
                """, (project_id,)).fetchall()

                for row in year_rows:
                    year = row['year']
                    cnt = row['cnt']
                    if year and year.isdigit() and 1990 <= int(year) <= 2099:
                        suggestions.append({
                            "label": f"{year} ({cnt:,})",
                            "query": f"date:{year}",
                            "icon": "\U0001f4c5",
                        })

                # 2. Favorites count (flag='pick' is the canonical favorite)
                fav_row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM photo_metadata
                    WHERE project_id = ? AND flag = 'pick'
                """, (project_id,)).fetchone()
                if fav_row and fav_row['cnt'] > 0:
                    suggestions.append({
                        "label": f"Favorites ({fav_row['cnt']:,})",
                        "query": "is:fav",
                        "icon": "\u2b50",
                    })

                # 3. Photos with GPS
                gps_row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM photo_metadata
                    WHERE project_id = ?
                      AND gps_latitude IS NOT NULL
                      AND gps_longitude IS NOT NULL
                      AND gps_latitude != 0
                """, (project_id,)).fetchone()
                if gps_row and gps_row['cnt'] > 0:
                    suggestions.append({
                        "label": f"With Location ({gps_row['cnt']:,})",
                        "query": "has:location",
                        "icon": "\U0001f4cd",
                    })

                # 4. Videos
                vid_row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM photo_metadata
                    WHERE project_id = ?
                      AND LOWER(SUBSTR(path, -4)) IN ('.mp4', '.mov', '.avi', '.mkv', '.m4v')
                """, (project_id,)).fetchone()
                if vid_row and vid_row['cnt'] > 0:
                    suggestions.append({
                        "label": f"Videos ({vid_row['cnt']:,})",
                        "query": "type:video",
                        "icon": "\U0001f3ac",
                    })

                # 5. Photos with faces (face_crops table, not 'faces')
                try:
                    face_row = conn.execute("""
                        SELECT COUNT(DISTINCT fc.image_path) as cnt
                        FROM face_crops fc
                        WHERE fc.project_id = ?
                    """, (project_id,)).fetchone()
                    if face_row and face_row['cnt'] > 0:
                        suggestions.append({
                            "label": f"With Faces ({face_row['cnt']:,})",
                            "query": "has:faces",
                            "icon": "\U0001f464",
                        })
                except Exception:
                    pass  # face_crops table may not exist yet

                # 6. Rated photos (3+)
                rated_row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM photo_metadata
                    WHERE project_id = ? AND rating >= 3
                """, (project_id,)).fetchone()
                if rated_row and rated_row['cnt'] > 0:
                    suggestions.append({
                        "label": f"3+ Stars ({rated_row['cnt']:,})",
                        "query": "rating:3",
                        "icon": "\u2b50",
                    })

        except Exception as e:
            logger.warning(f"[LibraryAnalyzer] suggest failed: {e}")

        return suggestions[:max_suggestions]


# ══════════════════════════════════════════════════════════════════════
# Module-level singleton cache
# ══════════════════════════════════════════════════════════════════════

_orchestrators: Dict[int, SearchOrchestrator] = {}
_lock = threading.Lock()


def get_search_orchestrator(project_id: int) -> SearchOrchestrator:
    """Get or create SearchOrchestrator for a project (singleton per project)."""
    with _lock:
        if project_id not in _orchestrators:
            _orchestrators[project_id] = SearchOrchestrator(project_id)
        return _orchestrators[project_id]
