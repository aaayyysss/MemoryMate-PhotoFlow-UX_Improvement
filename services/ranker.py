# services/ranker.py
# Family-aware ranking profiles for search results.
#
# Different preset families use different scoring weights:
# - scenic: semantic-dominant (CLIP is primary signal)
# - type: structural/metadata-dominant (OCR, extension, dimensions)
# - people_event: face-presence is strong signal
# - utility: metadata-only (no semantic scoring)
#
# Extracted from SearchOrchestrator._score_result() to enable
# per-family weight profiles without duplicating the scoring logic.

"""
Ranker - Family-aware scoring profiles.

Usage:
    from services.ranker import Ranker

    ranker = Ranker()
    scored = ranker.score(path, clip_score, matched_prompt, meta,
                          active_filters, family="scenic")
"""

import re
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

from logging_config import get_logger
from config.ranking_config import RankingConfig

logger = get_logger(__name__)


# ======================================================================
# Scoring Weights per family
# ======================================================================

@dataclass
class ScoringWeights:
    """
    Deterministic scoring contract.

    S = w_clip * clip + w_recency * recency + w_fav * favorite
      + w_location * location + w_face * face_match
      + w_structural * structural + w_ocr * ocr
      + w_event * event + w_screenshot * screenshot

    All weights are explicit, testable, and logged.
    The structural weight reserves budget for document/screenshot/scenic
    structural signals so that validate() does not normalize it away.
    The OCR weight reserves budget for text-content relevance scoring.
    The event weight reserves budget for people_event composite scoring.
    The screenshot weight reserves budget for screenshot-specific evidence.
    """
    w_clip: float = 0.75
    w_recency: float = 0.05
    w_favorite: float = 0.08
    w_location: float = 0.04
    w_face_match: float = 0.08
    w_structural: float = 0.00  # reserved structural budget
    w_ocr: float = 0.00  # reserved OCR text relevance budget
    w_event: float = 0.00  # reserved event-evidence budget (people_event)
    w_screenshot: float = 0.00  # reserved screenshot-evidence budget

    # Guardrails
    max_recency_boost: float = 0.10
    max_favorite_boost: float = 0.15
    recency_halflife_days: int = 90

    # Canonical mapping for validation
    _WEIGHT_TO_COMPONENT = {
        "w_clip": "clip_score",
        "w_recency": "recency_score",
        "w_favorite": "favorite_score",
        "w_location": "location_score",
        "w_face_match": "face_match_score",
        "w_structural": "structural_score",
        "w_ocr": "ocr_score",
        "w_event": "event_score",
        "w_screenshot": "screenshot_score",
    }

    def validate(self):
        """Ensure weights sum to ~1.0 and normalize if needed.

        Includes w_structural, w_ocr, w_event, and w_screenshot in the total
        so that profiles with structural/OCR/event/screenshot budget are
        not silently renormalized.
        """
        total = (
            self.w_clip + self.w_recency + self.w_favorite
            + self.w_location + self.w_face_match
            + self.w_structural + self.w_ocr + self.w_event
            + self.w_screenshot
        )
        if abs(total - 1.0) > 0.01:
            logger.warning(
                f"[ScoringWeights] Weights sum to {total:.3f}, not 1.0. Normalizing."
            )
            if total > 0:
                self.w_clip /= total
                self.w_recency /= total
                self.w_favorite /= total
                self.w_location /= total
                self.w_face_match /= total
                self.w_structural /= total
                self.w_ocr /= total
                self.w_event /= total
                self.w_screenshot /= total


# FAMILY_SECTION weight profiles --
# Each profile must sum to 1.0 including w_structural.
# w_structural reserves budget for document/screenshot/scenic structural
# signals that are computed separately and folded into the final score.

FAMILY_WEIGHTS = {
    # Scenic: semantic is king, mild structural anti-type support/penalty
    "scenic": ScoringWeights(
        w_clip=0.82,
        w_recency=0.03,
        w_favorite=0.04,
        w_location=0.00,
        w_face_match=0.03,
        w_structural=0.08,  # scenic structural support (GPS, panorama, low-OCR)
        w_ocr=0.00,
        w_event=0.00,
        w_screenshot=0.00,
    ),
    # Type (Documents, Screenshots): structural + OCR dominate over CLIP
    # w_clip intentionally low (0.12) -- for type-family, CLIP re-ranks
    # inside a structurally-generated candidate pool, not as primary retrieval.
    "type": ScoringWeights(
        w_clip=0.12,
        w_recency=0.03,
        w_favorite=0.02,
        w_location=0.00,
        w_face_match=0.00,
        w_structural=0.45,
        w_ocr=0.25,
        w_event=0.00,
        w_screenshot=0.13,
    ),
    # People events: face presence + event evidence are critical.
    # w_event=0.25 stolen from w_clip (was 0.58) so builder-computed
    # event_score enters the final rank numerically.
    "people_event": ScoringWeights(
        w_clip=0.33,
        w_recency=0.07,
        w_favorite=0.05,
        w_location=0.00,
        w_face_match=0.30,
        w_structural=0.00,
        w_ocr=0.00,
        w_event=0.25,
    ),
    # Utility (Videos, Favorites, With Location): metadata-only
    "utility": ScoringWeights(
        w_clip=0.00,
        w_recency=0.20,
        w_favorite=0.45,
        w_location=0.25,
        w_face_match=0.10,
        w_structural=0.00,
        w_ocr=0.00,
        w_event=0.00,
    ),
    # Animal/Object (Pets): CLIP-dominant, face-negative, no OCR
    "animal_object": ScoringWeights(
        w_clip=0.88,
        w_recency=0.05,
        w_favorite=0.03,
        w_location=0.00,
        w_face_match=0.00,
        w_structural=0.04,
        w_ocr=0.00,
        w_event=0.00,
    ),
}

# Normalize all profiles on import
for _fw in FAMILY_WEIGHTS.values():
    _fw.validate()


# ======================================================================
# ScoredResult (shared dataclass, re-exported for convenience)
# ======================================================================

@dataclass
class ScoredResult:
    """A single search result with full score decomposition."""
    path: str
    final_score: float = 0.0
    clip_score: float = 0.0
    recency_score: float = 0.0
    favorite_score: float = 0.0
    location_score: float = 0.0
    face_match_score: float = 0.0
    structural_score: float = 0.0
    ocr_score: float = 0.0
    event_score: float = 0.0
    screenshot_score: float = 0.0
    matched_prompt: str = ""
    reasons: List[str] = field(default_factory=list)
    duplicate_count: int = 0


# ======================================================================
# Preset Family Classification
# ======================================================================

PRESET_FAMILIES = {
    # Document-like presets (OCR/structure-first, hard gates)
    "documents": "type",
    "screenshots": "type",
    # Utility/metadata presets (no CLIP, no builder -- pure metadata filters)
    "videos": "utility",
    "favorites": "utility",
    "gps_photos": "utility",
    # Scenic-visual presets (CLIP-dominant, soft gates)
    "panoramas": "scenic",
    # People-event presets (face-required)
    "wedding": "people_event",
    "party": "people_event",
    "baby": "people_event",
    "portraits": "people_event",
    # Scenic / semantic presets (recall-first, soft gates only)
    "beach": "scenic",
    "mountains": "scenic",
    "city": "scenic",
    "forest": "scenic",
    "lake": "scenic",
    "travel": "scenic",
    "sunset": "scenic",
    "sport": "scenic",
    "food": "scenic",
    "pets": "animal_object",
    "flowers": "scenic",
    "snow": "scenic",
    "night": "scenic",
    "architecture": "scenic",
    "car": "scenic",
}


# -- Scenic anti-type penalty thresholds --
# Scenic families use soft negative structural scores to demote
# assets that look type-like (document, screenshot).
SCENIC_ANTI_TYPE_PENALTIES = {
    "doc_extension": -0.30,     # .png/.pdf/.tif/.tiff/.bmp
    "high_ocr_text": -0.25,     # OCR text length > threshold
    "scan_aspect": -0.15,       # aspect ratio consistent with scan/page
}
SCENIC_OCR_TEXT_PENALTY_THRESHOLD = 50  # characters


def get_preset_family(preset_id: Optional[str]) -> str:
    """Get the preset family for gate/ranking profile selection."""
    if not preset_id:
        return "scenic"
    return PRESET_FAMILIES.get(preset_id, "scenic")


def get_weights_for_family(family: str) -> ScoringWeights:
    """Get the scoring weights for a preset family.

    All families read from user preferences (Preferences > Search & Discovery)
    with per-family hardcoded defaults as fallback.  Preference keys follow
    the pattern ``ranking_{family}_{weight_name}``.

    Examples:
        ranking_scenic_w_clip     -> scenic CLIP weight
        ranking_type_w_structural -> type structural weight
        ranking_animal_object_w_clip -> animal_object CLIP weight
    """
    resolved = family if family in FAMILY_WEIGHTS else "scenic"
    wd = RankingConfig.get_family_weights_dict(resolved)
    sw = ScoringWeights(
        w_clip=wd["w_clip"],
        w_recency=wd["w_recency"],
        w_favorite=wd["w_favorite"],
        w_location=wd["w_location"],
        w_face_match=wd["w_face_match"],
        w_structural=wd["w_structural"],
        w_ocr=wd["w_ocr"],
        w_event=wd.get("w_event", 0.0),
        w_screenshot=wd.get("w_screenshot", 0.0),
        max_recency_boost=RankingConfig.get_max_recency_boost(),
        max_favorite_boost=RankingConfig.get_max_favorite_boost(),
        recency_halflife_days=RankingConfig.get_recency_halflife_days(),
    )
    sw.validate()
    return sw


# ======================================================================
# People-implied detection
# ======================================================================

_PEOPLE_IMPLIED_PRESETS = frozenset({
    "portraits", "baby", "wedding", "party",
})

_PEOPLE_IMPLIED_KEYWORDS = frozenset({
    "portrait", "portraits", "baby", "babies", "toddler", "infant",
    "wedding", "party", "celebration", "group photo", "family",
    "selfie", "people", "person", "child", "children", "kids",
})


def is_people_implied(plan: Any) -> bool:
    """Detect if a query implies people/faces."""
    preset_id = getattr(plan, 'preset_id', None)
    if preset_id and preset_id in _PEOPLE_IMPLIED_PRESETS:
        return True
    semantic_text = getattr(plan, 'semantic_text', '')
    if semantic_text:
        text_lower = semantic_text.lower()
        if any(kw in text_lower for kw in _PEOPLE_IMPLIED_KEYWORDS):
            return True
    return False


# ======================================================================
# Ranker
# ======================================================================

class Ranker:
    """
    Family-aware result ranker.

    Different families use different weight profiles, so a Documents
    search doesn't over-weight CLIP similarity while a Beach search does.
    """

    def __init__(self, default_family: str = "scenic"):
        self._default_family = default_family

    def score(
        self,
        path: str,
        clip_score: float,
        matched_prompt: str,
        meta: Dict[str, Any],
        active_filters: Optional[Dict] = None,
        people_implied: bool = False,
        family: Optional[str] = None,
        structural_score: float = 0.0,
        ocr_score: float = 0.0,
        event_score: float = 0.0,
        screenshot_score: float = 0.0,
    ) -> ScoredResult:
        """
        Apply the deterministic scoring contract to a single result.

        S = w_clip * clip + w_recency * recency + w_fav * favorite
          + w_location * location + w_face * face_match
          + w_structural * structural + w_ocr * ocr
          + w_event * event + w_screenshot * screenshot

        structural_score, ocr_score, event_score, and screenshot_score are
        computed externally (by the orchestrator/builders) and passed in as
        first-class weight terms.
        """
        w = get_weights_for_family(family or self._default_family)
        reasons = []

        # Clip score
        if clip_score > 0 and matched_prompt:
            reasons.append(f"clip={clip_score:.3f} (\"{matched_prompt}\")")

        # Recency score
        recency = 0.0
        created = meta.get("created_date") or meta.get("date_taken")
        if created:
            try:
                if isinstance(created, str):
                    dt = datetime.strptime(created[:10], "%Y-%m-%d")
                else:
                    dt = created
                days_ago = max(0, (datetime.now() - dt).days)
                recency = w.max_recency_boost * (
                    2.0 ** (-days_ago / max(1, w.recency_halflife_days))
                )
                if recency > 0.001:
                    reasons.append(f"recency={recency:.4f} ({days_ago}d ago)")
            except (ValueError, TypeError):
                pass

        # Favorite score
        favorite = 0.0
        flag = meta.get("flag", "none") or "none"
        rating = meta.get("rating", 0) or 0
        if flag == "pick":
            favorite = min(w.max_favorite_boost, 1.0)
            reasons.append(f"favorite={favorite:.2f} (flagged pick)")
        elif rating >= 4:
            favorite = min(w.max_favorite_boost, 1.0)
            reasons.append(f"favorite={favorite:.2f} (rating={rating})")
        elif rating >= 3:
            favorite = min(w.max_favorite_boost, 0.5)
            reasons.append(f"favorite={favorite:.2f} (rating={rating})")

        # Location score
        location = 0.0
        if meta.get("has_gps"):
            location = 1.0
            reasons.append("location=1.0 (GPS)")

        # Face match score
        face_match = 0.0
        if active_filters and active_filters.get("person_id"):
            face_match = 1.0
            reasons.append(f"face=1.0 (person:{active_filters['person_id']})")
        elif people_implied:
            face_count = meta.get("face_count", 0) or 0
            if face_count > 0:
                face_match = 1.0
                reasons.append(f"face=1.0 (face_presence, {face_count} faces)")

        # Structural score (logged when non-zero)
        if structural_score != 0.0:
            reasons.append(f"structural={structural_score:.3f}")

        # OCR score (logged when non-zero)
        if ocr_score != 0.0:
            reasons.append(f"ocr={ocr_score:.3f}")

        # Event score (logged when non-zero)
        if event_score != 0.0:
            reasons.append(f"event={event_score:.3f}")

        # Screenshot score (logged when non-zero)
        if screenshot_score != 0.0:
            reasons.append(f"screenshot={screenshot_score:.3f}")

        # Final score
        final = (
            w.w_clip * clip_score
            + w.w_recency * recency
            + w.w_favorite * favorite
            + w.w_location * location
            + w.w_face_match * face_match
            + w.w_structural * structural_score
            + w.w_ocr * ocr_score
            + w.w_event * event_score
            + w.w_screenshot * screenshot_score
        )

        # Family-specific post-score adjustment
        adj = self._family_post_adjust(
            family or self._default_family, ocr_score, structural_score, face_match
        )
        if adj != 0.0:
            final += adj
            reasons.append(f"family_adjust={adj:+.3f}")

        return ScoredResult(
            path=path,
            final_score=final,
            clip_score=clip_score,
            recency_score=recency,
            favorite_score=favorite,
            location_score=location,
            face_match_score=face_match,
            structural_score=structural_score,
            ocr_score=ocr_score,
            event_score=event_score,
            screenshot_score=screenshot_score,
            matched_prompt=matched_prompt,
            reasons=reasons,
        )

    @staticmethod
    def _family_post_adjust(
        family: str,
        ocr_score: float,
        structural_score: float,
        face_match: float,
    ) -> float:
        """Small family-specific post-score adjustments.

        These help Documents and Pets without contaminating other families.
        """
        # Documents: penalize candidates with no OCR and negative structure
        if family == "type" and ocr_score <= 0 and structural_score < 0:
            return -0.05
        # Pets: penalize candidates that triggered face scoring
        if family == "animal_object" and face_match > 0:
            return -0.08
        return 0.0

    def score_many(
        self,
        candidates: list,
        project_meta: Dict[str, Dict],
        plan: Any,
        family: Optional[str] = None,
        structural_scores: Optional[Dict[str, float]] = None,
        ocr_scores: Optional[Dict[str, float]] = None,
        event_scores: Optional[Dict[str, float]] = None,
        screenshot_scores: Optional[Dict[str, float]] = None,
    ) -> List[ScoredResult]:
        """Score a batch of candidates using the same plan/family."""
        fam = family or get_preset_family(getattr(plan, 'preset_id', None))
        people = is_people_implied(plan)
        active_filters = getattr(plan, 'filters', None)
        struct_lookup = structural_scores or {}
        ocr_lookup = ocr_scores or {}
        event_lookup = event_scores or {}
        screenshot_lookup = screenshot_scores or {}

        results = []
        for c in candidates:
            # c can be a tuple (path, clip_score, matched_prompt) or a ScoredResult
            if hasattr(c, 'path'):
                path = c.path
                clip_score = getattr(c, 'clip_score', 0.0)
                prompt = getattr(c, 'matched_prompt', '')
            else:
                path, clip_score, prompt = c[0], c[1], c[2]

            meta = project_meta.get(path, {})
            struct = struct_lookup.get(path, 0.0)
            ocr = ocr_lookup.get(path, 0.0)
            event = event_lookup.get(path, 0.0)
            screenshot = screenshot_lookup.get(path, 0.0)
            sr = self.score(path, clip_score, prompt, meta,
                            active_filters, people, fam,
                            structural_score=struct,
                            ocr_score=ocr,
                            event_score=event,
                            screenshot_score=screenshot)
            results.append(sr)

        results.sort(key=lambda r: r.final_score, reverse=True)
        return results
