# services/query_intent_planner.py
# Family-first query intent planning.
#
# Converts raw query text or preset clicks into a normalized QueryIntent
# that drives family-specific candidate builders. This is the first stage
# of the hybrid retrieval pipeline:
#
#   Query -> QueryIntentPlanner -> FamilyResolver -> CandidateBuilder
#         -> GateEngine -> Ranker -> SearchConfidencePolicy -> ResultSet
#
# Design grounded in how Apple Photos, Google Photos, Lightroom, and Excire
# expose search: natural-language retrieval with multimodal understanding,
# text reading, and structured ranking — not app-specific end-to-end models.

"""
QueryIntentPlanner - Decompose queries into structured intent for candidate builders.

Usage:
    from services.query_intent_planner import QueryIntentPlanner, QueryIntent

    planner = QueryIntentPlanner(project_id=1)
    intent = planner.plan("Ammar at the beach in 2024")
    # intent.family_hint = "people_event"
    # intent.person_terms = ["Ammar"]
    # intent.scene_terms = ["beach"]
    # intent.year_terms = [2024]
"""

from __future__ import annotations
import re
from datetime import datetime
from typing import Optional, List, Dict
from dataclasses import dataclass, field

from logging_config import get_logger
from services.ranker import PRESET_FAMILIES, get_preset_family

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════
# QueryIntent dataclass
# ══════════════════════════════════════════════════════════════════════

@dataclass
class QueryIntent:
    """Normalized intent produced by QueryIntentPlanner."""
    raw_query: str = ""
    normalized_query: str = ""

    # Family routing
    family_hint: Optional[str] = None  # type, people_event, scenic, pet, utility
    preset_id: Optional[str] = None

    # Decomposed terms
    person_terms: List[str] = field(default_factory=list)
    scene_terms: List[str] = field(default_factory=list)
    object_terms: List[str] = field(default_factory=list)
    text_terms: List[str] = field(default_factory=list)
    location_terms: List[str] = field(default_factory=list)

    # Temporal
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    year_terms: List[int] = field(default_factory=list)
    month_terms: List[int] = field(default_factory=list)

    # Constraints
    require_faces: bool = False
    exclude_faces: bool = False
    require_screenshot: bool = False
    exclude_screenshot: bool = False
    require_ocr: bool = False
    photos_only: bool = False
    videos_only: bool = False

    # Quality / sort
    quality_sort: Optional[str] = None  # best, recent, favorite
    result_limit: Optional[int] = None

    # Confidence
    planner_confidence: float = 0.0
    parse_notes: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════
# Lexicons for family detection
# ══════════════════════════════════════════════════════════════════════

_DOCUMENT_TERMS = frozenset({
    "document", "documents", "doc", "docs", "invoice", "receipt",
    "bill", "letter", "contract", "certificate", "form", "report",
    "scan", "scans", "scanned", "pdf", "paper", "page", "pages",
    "statement", "application", "tax", "insurance",
})

_SCREENSHOT_TERMS = frozenset({
    "screenshot", "screenshots", "screen shot", "screen capture",
    "screencast", "screen grab", "bildschirmfoto",
})

_PEOPLE_TERMS = frozenset({
    "wedding", "party", "birthday", "celebration", "portrait",
    "portraits", "selfie", "selfies", "group", "family",
    "baby", "babies", "toddler", "child", "children", "kids",
    "graduation", "ceremony", "reunion",
})

_PET_TERMS = frozenset({
    "pet", "pets", "dog", "dogs", "cat", "cats", "puppy", "kitten",
    "animal", "animals", "bird", "birds", "fish", "hamster", "rabbit",
})

_SCENE_TERMS = frozenset({
    "beach", "mountain", "mountains", "sunset", "sunrise", "forest",
    "lake", "ocean", "sea", "city", "skyline", "park", "garden",
    "snow", "rain", "night", "architecture", "building", "bridge",
    "street", "road", "river", "waterfall", "desert", "island",
    "countryside", "field", "meadow", "hill", "valley", "cave",
    "cliff", "coast", "harbor", "village", "town",
})

_QUALITY_PATTERNS = [
    (re.compile(r"\b(best|top|greatest|finest)\b", re.I), "best"),
    (re.compile(r"\b(favorite|favourite|fav|starred)\b", re.I), "favorite"),
    (re.compile(r"\b(recent|latest|newest)\b", re.I), "recent"),
]

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

_PERSON_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]{2,})\b")

# Preset names and utility terms that must never be treated as person names
_NON_PERSON_TERMS = frozenset({
    "favorites", "favourite", "favourites", "favorite",
    "videos", "video", "panoramas", "panorama",
    "screenshots", "screenshot", "documents", "document",
    "gps", "location", "locations", "starred",
    "recent", "latest", "newest", "oldest",
})


# ══════════════════════════════════════════════════════════════════════
# QueryIntentPlanner
# ══════════════════════════════════════════════════════════════════════

class QueryIntentPlanner:
    """
    Convert raw query text or preset clicks into a normalized QueryIntent
    that drives family-specific candidate builders.
    """

    def __init__(self, project_id: int):
        self.project_id = project_id

    def plan(self, raw_query: str, preset_id: Optional[str] = None) -> QueryIntent:
        """
        Main entry point: decompose a query into structured intent.

        Args:
            raw_query: Free text query or preset name
            preset_id: Optional preset ID (from SmartFind click)

        Returns:
            QueryIntent with all extracted slots
        """
        intent = QueryIntent(
            raw_query=raw_query,
            preset_id=preset_id,
        )

        # Normalize
        intent.normalized_query = self._normalize_query(raw_query)
        text = intent.normalized_query

        # Family hint from preset
        intent.family_hint = self._detect_family_hint(text, preset_id)

        # Extract structured slots
        intent.person_terms = self._extract_people_terms(text)
        intent.scene_terms = self._extract_scene_terms(text)
        intent.text_terms = self._extract_text_terms(text)

        # Dates
        (intent.date_from, intent.date_to,
         intent.year_terms, intent.month_terms) = self._extract_dates(text)

        # Media constraints
        constraints = self._extract_media_constraints(text)
        intent.require_faces = constraints.get("require_faces", False)
        intent.exclude_faces = constraints.get("exclude_faces", False)
        intent.require_screenshot = constraints.get("require_screenshot", False)
        intent.exclude_screenshot = constraints.get("exclude_screenshot", False)
        intent.require_ocr = constraints.get("require_ocr", False)
        intent.photos_only = constraints.get("photos_only", False)
        intent.videos_only = constraints.get("videos_only", False)

        # Quality sort
        intent.quality_sort = self._extract_quality_sort(text)

        # Result limit
        intent.result_limit = self._extract_limit(text)

        # Infer constraints from family
        if intent.family_hint == "type":
            intent.require_ocr = True
        if intent.family_hint == "people_event" and not intent.exclude_faces:
            intent.require_faces = True
        # Utility presets are metadata-only — no CLIP, no OCR, no faces
        if intent.family_hint == "utility":
            intent.require_ocr = False
            if intent.preset_id == "videos":
                intent.videos_only = True
            elif intent.preset_id == "favorites":
                intent.quality_sort = "favorite"

        # Score confidence
        intent.planner_confidence = self._score_planner_confidence(intent)

        logger.info(
            f"[QueryIntentPlanner] \"{raw_query}\" -> "
            f"family={intent.family_hint} "
            f"persons={intent.person_terms} "
            f"scenes={intent.scene_terms} "
            f"text={intent.text_terms} "
            f"years={intent.year_terms} "
            f"confidence={intent.planner_confidence:.2f}"
        )

        return intent

    @staticmethod
    def _normalize_query(text: str) -> str:
        """Lowercase and clean whitespace."""
        text = text.strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _detect_family_hint(text: str, preset_id: Optional[str]) -> Optional[str]:
        """Determine dominant retrieval family from text/preset.

        Preset clicks are deterministic — the PRESET_FAMILIES map is the
        single source of truth.  NLP heuristics only apply when no preset
        is active (free-text queries).
        """
        # Preset clicks are deterministic — always trust the map
        if preset_id and preset_id in PRESET_FAMILIES:
            return PRESET_FAMILIES[preset_id]

        text_lower = text.lower()
        words = set(text_lower.split())

        # Check family lexicons (only for free-text, not preset clicks)
        if words & _DOCUMENT_TERMS or words & _SCREENSHOT_TERMS:
            return "type"
        if words & _PEOPLE_TERMS:
            return "people_event"
        if words & _PET_TERMS:
            return "animal_object"

        # Text search indicators
        if re.search(r"\bwith\s+(?:text|words?)\b", text_lower):
            return "type"
        if re.search(r"\bcontaining\s+", text_lower):
            return "type"

        # Scene terms default to scenic
        if words & _SCENE_TERMS:
            return "scenic"

        return None  # Let orchestrator decide

    @staticmethod
    def _extract_people_terms(text: str) -> List[str]:
        """Extract person name candidates from text."""
        # Look for capitalized names in the original (pre-normalized) query
        # Since we normalized to lowercase, we use the pattern on common names
        terms = []
        words = text.split()
        for w in words:
            # Skip preset/utility terms that are never person names
            if w in _NON_PERSON_TERMS:
                continue
            # Skip common scene/object words
            if w in _SCENE_TERMS or w in _DOCUMENT_TERMS or w in _PET_TERMS:
                continue
            if w in _PEOPLE_TERMS:
                continue
            # Skip stopwords
            if w in {"the", "at", "in", "on", "from", "with", "and", "or",
                      "of", "to", "for", "a", "an", "my", "our", "their",
                      "best", "top", "recent", "latest", "photos", "photo",
                      "pictures", "images", "last", "this", "year", "month"}:
                continue
            # Skip numbers and date-like tokens
            if re.match(r"^\d+$", w):
                continue
            # Remaining multi-char words could be person names
            # (heuristic — real resolution happens via PersonSearchService)
            if len(w) >= 3 and w.isalpha():
                terms.append(w)

        return terms[:5]  # Cap at 5 person candidates

    @staticmethod
    def _extract_scene_terms(text: str) -> List[str]:
        """Extract scene/location terms."""
        words = set(text.split())
        return sorted(words & _SCENE_TERMS)

    @staticmethod
    def _extract_text_terms(text: str) -> List[str]:
        """Extract terms that should be searched via OCR/FTS."""
        # Look for quoted phrases or terms after specific patterns
        terms = []

        # Quoted phrases
        for m in re.finditer(r'"([^"]+)"', text):
            terms.append(m.group(1))

        # "with text X", "containing X"
        for pat in [r"\bwith\s+text\s+(\w+)", r"\bcontaining\s+(\w+)"]:
            for m in re.finditer(pat, text, re.I):
                terms.append(m.group(1))

        # For document-family queries, all non-stopword terms are text terms
        words = set(text.split())
        if words & _DOCUMENT_TERMS:
            for w in words:
                if (w not in _DOCUMENT_TERMS and len(w) >= 3
                        and w not in {"the", "with", "from", "and", "for"}):
                    terms.append(w)

        return list(dict.fromkeys(terms))[:10]  # Dedupe, cap at 10

    @staticmethod
    def _extract_dates(text: str):
        """Extract date range, year terms, and month terms."""
        date_from = None
        date_to = None
        year_terms = []
        month_terms = []

        # Year extraction: "2024", "in 2024"
        for m in re.finditer(r"\b(\d{4})\b", text):
            year = int(m.group(1))
            if 1990 <= year <= 2099:
                year_terms.append(year)

        # Month extraction
        text_lower = text.lower()
        for name, num in _MONTH_NAMES.items():
            if name in text_lower:
                month_terms.append(num)
                break

        # Build date range from year + month
        if year_terms:
            year = year_terms[0]
            if month_terms:
                import calendar
                month = month_terms[0]
                last_day = calendar.monthrange(year, month)[1]
                date_from = f"{year}-{month:02d}-01"
                date_to = f"{year}-{month:02d}-{last_day:02d}"
            else:
                date_from = f"{year}-01-01"
                date_to = f"{year}-12-31"

        return date_from, date_to, year_terms, month_terms

    @staticmethod
    def _extract_media_constraints(text: str) -> dict:
        """Extract media type and structural constraints."""
        constraints = {}
        text_lower = text.lower()

        if re.search(r"\bscreenshots?\b", text_lower):
            constraints["require_screenshot"] = True
        if re.search(r"\b(documents?|invoice|receipt|scan)\b", text_lower):
            constraints["require_ocr"] = True
            constraints["exclude_faces"] = True
        if re.search(r"\b(without|no|exclude)\s+(people|person|faces?)\b", text_lower):
            constraints["exclude_faces"] = True
        if re.search(r"\b(without|no|exclude)\s+screenshots?\b", text_lower):
            constraints["exclude_screenshot"] = True
        if re.search(r"\bphotos?\s*only\b|\bonly\s*photos?\b", text_lower):
            constraints["photos_only"] = True
        if re.search(r"\bvideos?\s*only\b|\bonly\s*videos?\b", text_lower):
            constraints["videos_only"] = True

        return constraints

    @staticmethod
    def _extract_quality_sort(text: str) -> Optional[str]:
        """Extract quality/sort preference."""
        for pattern, sort_type in _QUALITY_PATTERNS:
            if pattern.search(text):
                return sort_type
        return None

    @staticmethod
    def _extract_limit(text: str) -> Optional[int]:
        """Extract result limit from query."""
        m = re.search(r"\b(?:top|first|last|recent)\s+(\d+)\b", text, re.I)
        if m:
            limit = int(m.group(1))
            if 1 <= limit <= 1000:
                return limit
        return None

    @staticmethod
    def _score_planner_confidence(intent: QueryIntent) -> float:
        """Score how confident the planner is in the decomposition."""
        score = 0.0

        # Family hint resolved
        if intent.family_hint:
            score += 0.3

        # Has structured slots
        if intent.person_terms:
            score += 0.2
        if intent.scene_terms:
            score += 0.1
        if intent.text_terms:
            score += 0.15
        if intent.year_terms or intent.date_from:
            score += 0.15
        if intent.quality_sort:
            score += 0.05

        # Preset gives high confidence
        if intent.preset_id:
            score += 0.3

        return min(1.0, score)


# ── Module-level helpers ──

_planner_cache: Dict[int, QueryIntentPlanner] = {}


def get_query_intent_planner(project_id: int) -> QueryIntentPlanner:
    """Get or create a QueryIntentPlanner for a project."""
    if project_id not in _planner_cache:
        _planner_cache[project_id] = QueryIntentPlanner(project_id)
    return _planner_cache[project_id]
