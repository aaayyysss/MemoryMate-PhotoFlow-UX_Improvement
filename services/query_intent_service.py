# services/query_intent_service.py
# Full Query Intent Decomposition
#
# Decomposes complex natural language queries into structured sub-intents
# that the search orchestrator can execute. Goes beyond simple token parsing
# to understand multi-part queries with implicit constraints.
#
# Examples:
#   "sunset photos from last summer at the beach"
#   → semantic: "sunset beach"
#   → date: 2025-06 to 2025-08
#   → media_type: photo
#
#   "John and Sarah's wedding photos with location"
#   → persons: [face_001, face_002]
#   → semantic: "wedding"
#   → require: has_gps
#   → media_type: photo
#
#   "best food photos from our Italy trip 2024"
#   → semantic: "food Italy"
#   → quality: rating >= 4 or is:fav
#   → date: 2024
#   → media_type: photo

"""
QueryIntentService - Advanced query understanding.

Goes beyond TokenParser with:
1. Temporal expression parsing ("last summer", "Christmas 2024")
2. Person name resolution from entity graph
3. Quality intent detection ("best", "top", "favorite")
4. Spatial intent ("near Munich", "at the beach")
5. Negation ("without people", "no screenshots")
6. Quantity intent ("recent 10", "first photos")
7. Compound query decomposition ("A and B" → sub-queries)

Usage:
    from services.query_intent_service import QueryIntentService

    qis = QueryIntentService(project_id=1)
    intent = qis.decompose("best sunset photos from last summer")
    # intent.semantic_text = "sunset"
    # intent.quality_filter = "best"
    # intent.date_range = ("2025-06-01", "2025-08-31")
    # intent.media_type = "photo"
"""

from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class QueryIntent:
    """Decomposed query intent with all extracted sub-intents."""
    # Original query
    raw_query: str = ""

    # Semantic text for CLIP (remaining after all extraction)
    semantic_text: str = ""

    # Person intents
    person_branch_keys: List[str] = field(default_factory=list)
    person_names: List[str] = field(default_factory=list)
    person_mode: str = "any"  # "any" (OR) or "all" (AND/co-occurrence)

    # Temporal intents
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    temporal_label: str = ""  # Human-readable: "last summer", "Christmas 2024"

    # Media type
    media_type: Optional[str] = None  # "photo", "video", None

    # Quality intents
    quality_filter: Optional[str] = None  # "best", "favorite", None
    rating_min: Optional[int] = None

    # Spatial intents
    location_name: Optional[str] = None
    require_gps: bool = False

    # Negation intents
    exclude_people: bool = False
    exclude_screenshots: bool = False
    exclude_videos: bool = False

    # Quantity intents
    limit_count: Optional[int] = None
    sort_preference: Optional[str] = None  # "recent", "oldest", "best"

    # Structured tokens (passthrough from TokenParser)
    structured_filters: Dict[str, Any] = field(default_factory=dict)

    # Extraction metadata
    intents_found: List[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_query_plan_overrides(self) -> Dict[str, Any]:
        """
        Convert intents to overrides applicable to a QueryPlan.

        Returns a dict of fields that should be merged into/override
        the QueryPlan produced by TokenParser.
        """
        overrides: Dict[str, Any] = {}

        if self.semantic_text:
            overrides["semantic_text"] = self.semantic_text

        filters = dict(self.structured_filters)

        if self.date_from:
            filters["date_from"] = self.date_from
        if self.date_to:
            filters["date_to"] = self.date_to
        if self.media_type:
            filters["media_type"] = self.media_type
        if self.rating_min is not None:
            filters["rating_min"] = self.rating_min
        if self.quality_filter == "favorite":
            filters["flag"] = "pick"
        if self.require_gps:
            filters["has_gps"] = True
        if self.person_branch_keys:
            if len(self.person_branch_keys) == 1:
                filters["person_id"] = self.person_branch_keys[0]
            else:
                filters["person_ids"] = self.person_branch_keys
                filters["person_mode"] = self.person_mode

        if filters:
            overrides["filters"] = filters

        if self.exclude_people:
            overrides["exclude_faces"] = True
        if self.exclude_screenshots:
            overrides["exclude_screenshots"] = True

        if self.person_branch_keys:
            overrides["require_faces"] = True

        return overrides


# ═══════════════════════════════════════════════════════════════════
# Temporal Expression Patterns
# ═══════════════════════════════════════════════════════════════════

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

_SEASON_MONTHS = {
    "spring": (3, 5),
    "summer": (6, 8),
    "autumn": (9, 11),
    "fall": (9, 11),
    "winter": (12, 2),
}

_HOLIDAY_DATES = {
    "christmas": (12, 20, 12, 31),
    "xmas": (12, 20, 12, 31),
    "new year": (12, 28, 1, 5),
    "new years": (12, 28, 1, 5),
    "halloween": (10, 25, 11, 2),
    "thanksgiving": (11, 20, 11, 30),
    "easter": (3, 15, 4, 30),
    "valentines": (2, 10, 2, 18),
    "valentine": (2, 10, 2, 18),
}


# ═══════════════════════════════════════════════════════════════════
# Quality / Negation / Quantity Patterns
# ═══════════════════════════════════════════════════════════════════

_QUALITY_PATTERNS = [
    (re.compile(r"\b(best|top|greatest|finest)\b", re.I), "best"),
    (re.compile(r"\b(favorite|favourite|fav|starred)\b", re.I), "favorite"),
    (re.compile(r"\b(highly?\s*rated|top\s*rated)\b", re.I), "best"),
]

_NEGATION_PATTERNS = [
    (re.compile(r"\b(without|no|exclude|excluding)\s+(people|person|faces?)\b", re.I), "exclude_people"),
    (re.compile(r"\b(without|no|exclude|excluding)\s+screenshots?\b", re.I), "exclude_screenshots"),
    (re.compile(r"\b(without|no|exclude|excluding)\s+videos?\b", re.I), "exclude_videos"),
    (re.compile(r"\b(only\s+photos?|photos?\s+only)\b", re.I), "photos_only"),
    (re.compile(r"\b(only\s+videos?|videos?\s+only)\b", re.I), "videos_only"),
]

_QUANTITY_PATTERNS = [
    (re.compile(r"\b(recent|latest|last|newest)\s+(\d+)\b", re.I), "recent"),
    (re.compile(r"\b(first|oldest|earliest)\s+(\d+)\b", re.I), "oldest"),
    (re.compile(r"\btop\s+(\d+)\b", re.I), "best"),
]

_MEDIA_TYPE_PATTERNS = [
    (re.compile(r"\bphotos?\b", re.I), "photo"),
    (re.compile(r"\bpictures?\b", re.I), "photo"),
    (re.compile(r"\bimages?\b", re.I), "photo"),
    (re.compile(r"\bvideos?\b", re.I), "video"),
    (re.compile(r"\bclips?\b", re.I), "video"),
]

_LOCATION_PATTERN = re.compile(
    r"\b(?:at|in|near|from|around)\s+(?:the\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b"
)

_GPS_PATTERNS = [
    re.compile(r"\bwith\s+(?:gps|location|coordinates?)\b", re.I),
    re.compile(r"\bgeo\s*tagged\b", re.I),
]


class QueryIntentService:
    """
    Advanced query intent decomposition.

    Extracts structured intents from natural language queries
    and produces a QueryIntent that can be merged with TokenParser output.
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._person_service = None

    def _get_person_service(self):
        if self._person_service is None:
            try:
                from services.person_search_service import PersonSearchService
                self._person_service = PersonSearchService(self.project_id)
            except Exception:
                pass
        return self._person_service

    def decompose(self, query: str) -> QueryIntent:
        """
        Decompose a natural language query into structured intents.

        Processing order:
        1. Extract negation intents (highest priority - removes tokens)
        2. Extract quality intents
        3. Extract quantity intents
        4. Extract media type
        5. Extract temporal expressions
        6. Extract person names
        7. Extract location/spatial intents
        8. Extract GPS requirements
        9. Remaining text → semantic_text for CLIP

        Args:
            query: Raw user query string

        Returns:
            QueryIntent with all extracted sub-intents
        """
        intent = QueryIntent(raw_query=query)
        remaining = query.strip()

        # 1. Negation intents
        remaining = self._extract_negations(remaining, intent)

        # 2. Quality intents
        remaining = self._extract_quality(remaining, intent)

        # 3. Temporal expressions (before quantity to avoid "last 30 days"
        #    being consumed as quantity "last 30")
        remaining = self._extract_temporal(remaining, intent)

        # 4. Quantity intents
        remaining = self._extract_quantity(remaining, intent)

        # 5. Media type
        remaining = self._extract_media_type(remaining, intent)

        # 6. Person names
        remaining = self._extract_persons(remaining, intent)

        # 7. Location/spatial
        remaining = self._extract_location(remaining, intent)

        # 8. GPS requirement
        remaining = self._extract_gps(remaining, intent)

        # 9. Clean up remaining text
        remaining = re.sub(r"\s+", " ", remaining).strip()
        # Remove common filler words that don't help CLIP
        remaining = re.sub(
            r"\b(from|the|our|my|their|his|her|its|with|and|or|of|at|in|on|to|for|a|an)\b",
            " ", remaining, flags=re.I
        )
        remaining = re.sub(r"\s+", " ", remaining).strip()
        intent.semantic_text = remaining

        # Infer quality→rating
        if intent.quality_filter == "best" and intent.rating_min is None:
            intent.rating_min = 4

        logger.info(
            f"[QueryIntent] Decomposed \"{query}\" → "
            f"semantic=\"{intent.semantic_text}\" | "
            f"intents={intent.intents_found} | "
            f"persons={intent.person_names} | "
            f"date={intent.temporal_label or 'none'} | "
            f"quality={intent.quality_filter or 'none'}"
        )

        return intent

    def _extract_negations(self, text: str, intent: QueryIntent) -> str:
        """Extract negation patterns."""
        for pattern, negation_type in _NEGATION_PATTERNS:
            match = pattern.search(text)
            if match:
                if negation_type == "exclude_people":
                    intent.exclude_people = True
                elif negation_type == "exclude_screenshots":
                    intent.exclude_screenshots = True
                elif negation_type == "exclude_videos":
                    intent.exclude_videos = True
                    intent.media_type = "photo"
                elif negation_type == "photos_only":
                    intent.media_type = "photo"
                elif negation_type == "videos_only":
                    intent.media_type = "video"
                intent.intents_found.append(negation_type)
                text = text[:match.start()] + text[match.end():]
        return text

    def _extract_quality(self, text: str, intent: QueryIntent) -> str:
        """Extract quality intent (best, favorite, etc.)."""
        for pattern, quality_type in _QUALITY_PATTERNS:
            match = pattern.search(text)
            if match:
                intent.quality_filter = quality_type
                intent.intents_found.append(f"quality:{quality_type}")
                text = text[:match.start()] + text[match.end():]
                break
        return text

    def _extract_quantity(self, text: str, intent: QueryIntent) -> str:
        """Extract quantity/limit intent."""
        for pattern, sort_type in _QUANTITY_PATTERNS:
            match = pattern.search(text)
            if match:
                groups = match.groups()
                # Extract count (last numeric group)
                for g in reversed(groups):
                    if g and g.isdigit():
                        intent.limit_count = int(g)
                        break
                intent.sort_preference = sort_type
                intent.intents_found.append(f"quantity:{sort_type}")
                text = text[:match.start()] + text[match.end():]
                break
        return text

    def _extract_media_type(self, text: str, intent: QueryIntent) -> str:
        """Extract media type intent."""
        if intent.media_type:
            return text  # Already set by negation
        for pattern, media_type in _MEDIA_TYPE_PATTERNS:
            match = pattern.search(text)
            if match:
                intent.media_type = media_type
                intent.intents_found.append(f"media:{media_type}")
                text = text[:match.start()] + text[match.end():]
                break
        return text

    def _extract_temporal(self, text: str, intent: QueryIntent) -> str:
        """Extract temporal expressions."""
        text_lower = text.lower()
        now = datetime.now()
        current_year = now.year

        # Pattern: "last summer", "this winter", etc.
        season_match = re.search(
            r"\b(last|this|previous)\s+(spring|summer|autumn|fall|winter)\b",
            text, re.I,
        )
        if season_match:
            modifier = season_match.group(1).lower()
            season = season_match.group(2).lower()
            months = _SEASON_MONTHS.get(season)
            if months:
                start_month, end_month = months
                year = current_year if modifier == "this" else current_year - 1

                # Handle winter crossing year boundary
                if season == "winter" and modifier == "last":
                    intent.date_from = f"{year - 1}-{start_month:02d}-01"
                    intent.date_to = f"{year}-{end_month:02d}-28"
                elif season == "winter":
                    intent.date_from = f"{year}-{start_month:02d}-01"
                    intent.date_to = f"{year + 1}-{end_month:02d}-28"
                else:
                    intent.date_from = f"{year}-{start_month:02d}-01"
                    import calendar
                    last_day = calendar.monthrange(year, end_month)[1]
                    intent.date_to = f"{year}-{end_month:02d}-{last_day:02d}"

                intent.temporal_label = f"{modifier} {season}"
                intent.intents_found.append(f"temporal:{intent.temporal_label}")
                text = text[:season_match.start()] + text[season_match.end():]
                return text

        # Pattern: "Christmas 2024", "Halloween", etc.
        for holiday, (m_start, d_start, m_end, d_end) in _HOLIDAY_DATES.items():
            holiday_pattern = re.compile(
                rf"\b{re.escape(holiday)}\s*(\d{{4}})?\b", re.I
            )
            match = holiday_pattern.search(text)
            if match:
                year = int(match.group(1)) if match.group(1) else current_year
                # Handle year boundary for New Year
                if m_start > m_end:
                    intent.date_from = f"{year}-{m_start:02d}-{d_start:02d}"
                    intent.date_to = f"{year + 1}-{m_end:02d}-{d_end:02d}"
                else:
                    intent.date_from = f"{year}-{m_start:02d}-{d_start:02d}"
                    intent.date_to = f"{year}-{m_end:02d}-{d_end:02d}"
                intent.temporal_label = f"{holiday} {year}"
                intent.intents_found.append(f"temporal:{intent.temporal_label}")
                text = text[:match.start()] + text[match.end():]
                return text

        # Pattern: "June 2024", "March", "in 2023"
        month_year_match = re.search(
            r"\b(" + "|".join(_MONTH_NAMES.keys()) + r")\s*(\d{4})?\b",
            text, re.I,
        )
        if month_year_match:
            month_name = month_year_match.group(1).lower()
            month = _MONTH_NAMES.get(month_name)
            year = int(month_year_match.group(2)) if month_year_match.group(2) else current_year
            if month:
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                intent.date_from = f"{year}-{month:02d}-01"
                intent.date_to = f"{year}-{month:02d}-{last_day:02d}"
                intent.temporal_label = f"{month_name.title()} {year}"
                intent.intents_found.append(f"temporal:{intent.temporal_label}")
                text = text[:month_year_match.start()] + text[month_year_match.end():]
                return text

        # Pattern: standalone year "2024", "in 2023"
        year_match = re.search(r"\b(?:in\s+)?(\d{4})\b", text)
        if year_match:
            year = int(year_match.group(1))
            if 1990 <= year <= 2099:
                intent.date_from = f"{year}-01-01"
                intent.date_to = f"{year}-12-31"
                intent.temporal_label = str(year)
                intent.intents_found.append(f"temporal:{year}")
                text = text[:year_match.start()] + text[year_match.end():]
                return text

        # Pattern: "last N days/weeks/months"
        relative_match = re.search(
            r"\blast\s+(\d+)\s+(days?|weeks?|months?|years?)\b",
            text, re.I,
        )
        if relative_match:
            count = int(relative_match.group(1))
            unit = relative_match.group(2).lower().rstrip("s")
            if unit == "day":
                delta = timedelta(days=count)
            elif unit == "week":
                delta = timedelta(weeks=count)
            elif unit == "month":
                delta = timedelta(days=count * 30)
            elif unit == "year":
                delta = timedelta(days=count * 365)
            else:
                delta = timedelta(days=count)

            end_date = now.date()
            start_date = end_date - delta
            intent.date_from = start_date.strftime("%Y-%m-%d")
            intent.date_to = end_date.strftime("%Y-%m-%d")
            intent.temporal_label = f"last {count} {relative_match.group(2)}"
            intent.intents_found.append(f"temporal:{intent.temporal_label}")
            text = text[:relative_match.start()] + text[relative_match.end():]

        return text

    def _extract_persons(self, text: str, intent: QueryIntent) -> str:
        """Extract person names using PersonSearchService."""
        person_svc = self._get_person_service()
        if not person_svc:
            return text

        try:
            keys, remaining = person_svc.extract_person_names_from_query(text)
            if keys:
                intent.person_branch_keys = keys
                # Get display names
                person_svc._load_name_cache()
                for k in keys:
                    name = (person_svc._branch_to_name or {}).get(k, k)
                    intent.person_names.append(name)

                # Detect AND mode ("John and Sarah")
                if len(keys) > 1 and re.search(r"\band\b", text, re.I):
                    intent.person_mode = "all"
                else:
                    intent.person_mode = "any"

                intent.intents_found.append(
                    f"person:{','.join(intent.person_names)}"
                )
                return remaining
        except Exception as e:
            logger.debug(f"[QueryIntent] Person extraction failed: {e}")

        return text

    def _extract_location(self, text: str, intent: QueryIntent) -> str:
        """Extract location/spatial intent."""
        match = _LOCATION_PATTERN.search(text)
        if match:
            location = match.group(1)
            # Only treat as location if it's not a known person name
            person_svc = self._get_person_service()
            if person_svc:
                keys = person_svc.resolve_person_name(location)
                if keys:
                    return text  # It's a person, not a location

            intent.location_name = location
            intent.intents_found.append(f"location:{location}")
            # Don't remove from text - keep for CLIP semantic search
        return text

    def _extract_gps(self, text: str, intent: QueryIntent) -> str:
        """Extract GPS/location requirement."""
        for pattern in _GPS_PATTERNS:
            match = pattern.search(text)
            if match:
                intent.require_gps = True
                intent.intents_found.append("require_gps")
                text = text[:match.start()] + text[match.end():]
                break
        return text


# ── Module-level singleton cache ──
_service_cache: Dict[int, QueryIntentService] = {}


def get_query_intent_service(project_id: int) -> QueryIntentService:
    """Get or create a QueryIntentService for a project."""
    if project_id not in _service_cache:
        _service_cache[project_id] = QueryIntentService(project_id)
    return _service_cache[project_id]
