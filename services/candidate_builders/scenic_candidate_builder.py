# services/candidate_builders/scenic_candidate_builder.py
# Pre-filter candidate builder for scenic family searches.
#
# This is the biggest quality improvement for scenic presets (Travel,
# Beach, Mountains, Panoramas, etc.).  Instead of running CLIP over
# the entire corpus then lightly penalizing document-like results,
# this builder EXCLUDES strong type-family assets BEFORE semantic
# retrieval.  This mirrors how Google Photos and Apple Photos handle
# category search: narrow the candidate pool early with metadata,
# modality, and structural signals, then let the semantic model rank
# within that clean pool.
#
# Hard exclusions (never enter scenic pool):
#   - Strong document evidence (doc extension + high OCR + page-like)
#   - Confirmed screenshots
#   - Tiny images (< 400px min edge)
#
# Soft exclusions (penalized but not blocked):
#   - Single weak document signal (e.g. just PNG extension)
#   - High OCR text without page-like geometry (could be signage photo)

"""
ScenicCandidateBuilder - Pre-filtered candidate pool for scenic searches.

Usage:
    from services.candidate_builders.scenic_candidate_builder import (
        ScenicCandidateBuilder,
    )

    builder = ScenicCandidateBuilder(project_id=1)
    candidate_set = builder.build(intent, project_meta)
"""

from __future__ import annotations
import os
from typing import Dict, List, Set

from services.candidate_builders.base_candidate_builder import (
    BaseCandidateBuilder,
    CandidateSet,
)
from services.query_intent_planner import QueryIntent
from logging_config import get_logger

logger = get_logger(__name__)

# Document-native extensions (strong document signal)
_DOC_NATIVE_EXTENSIONS = frozenset({'.pdf', '.tif', '.tiff', '.bmp'})

# High-OCR threshold for hard exclusion (characters)
_HIGH_OCR_THRESHOLD = 80

# Page-like aspect ratio bounds
_PAGE_RATIO_MIN = 1.20
_PAGE_RATIO_MAX = 1.60

# Minimum edge size for scenic candidates
_MIN_SCENIC_EDGE = 400

# Soft-penalty OCR threshold (lower than hard exclusion)
_SOFT_OCR_THRESHOLD = 50

# Scenic-positive heuristics
_LOW_OCR_THRESHOLD = 20
_PANORAMA_RATIO_MIN = 2.20
_FACE_HEAVY_THRESHOLD = 2


class ScenicCandidateBuilder(BaseCandidateBuilder):
    """
    Pre-filter the corpus for scenic family searches.

    Excludes assets with strong document/screenshot evidence BEFORE
    CLIP runs, so semantic retrieval operates on a clean pool.

    This builder does NOT perform the semantic search itself.  It
    produces a filtered candidate pool that the orchestrator then
    passes to CLIP for retrieval and ranking.

    Exclusion levels:
      HARD (removed from pool):
        - doc extension + page-like geometry
        - doc extension + high OCR (>= 80 chars)
        - confirmed screenshot (is_screenshot flag)
        - tiny images (< 400px min edge)

      SOFT (kept but penalized via evidence):
        - single weak document signal (PNG alone)
        - moderate OCR (50-80 chars) without page geometry
        - face-heavy images for landscape-only presets
    """

    # Presets where face-heavy images should be penalized (not excluded)
    _LANDSCAPE_PRESETS = frozenset({
        "panoramas", "mountains", "sunset", "beach", "lake",
        "forest", "snow", "architecture",
    })

    _GPS_BIASED_PRESETS = frozenset({
        "beach", "mountains", "lake", "forest", "travel", "sunset",
        "architecture", "city",
    })

    _PANORAMA_PRESETS = frozenset({"panoramas"})

    def build(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int = 500,
    ) -> CandidateSet:
        """Build a pre-filtered scenic candidate pool."""
        if not project_meta:
            return self._empty("scenic", "No project metadata available")

        preset_id = intent.preset_id or ""
        is_landscape = preset_id in self._LANDSCAPE_PRESETS

        kept = []
        evidence_by_path = {}
        hard_excluded = {}
        soft_penalties = 0

        for path, meta in project_meta.items():
            exclusion = self._check_hard_exclusion(path, meta)
            if exclusion is not None:
                hard_excluded[exclusion] = hard_excluded.get(exclusion, 0) + 1
                continue

            # Build evidence for soft-penalty evaluation
            evidence = self._build_evidence(path, meta, is_landscape, preset_id)

            kept.append(path)
            evidence_by_path[path] = evidence

            if evidence.get("soft_penalty", 0.0) < 0:
                soft_penalties += 1

            if len(kept) >= limit:
                break

        total = len(project_meta)
        excluded_count = sum(hard_excluded.values())

        logger.info(
            f"[ScenicCandidateBuilder] scenic pool: "
            f"{len(kept)}/{total} candidates "
            f"(hard_excluded={excluded_count}, soft_penalized={soft_penalties}) "
            f"exclusions={hard_excluded}"
        )

        return CandidateSet(
            family="scenic",
            candidate_paths=kept,
            evidence_by_path=evidence_by_path,
            source_counts={
                "total_assets": total,
                "scenic_candidates": len(kept),
                "hard_excluded": excluded_count,
                "soft_penalized": soft_penalties,
            },
            builder_confidence=0.90 if kept else 0.0,
            ready_state="ready" if kept else "empty",
            notes=[
                f"Scenic builder: {len(kept)}/{total} candidates "
                f"({excluded_count} hard-excluded)"
            ],
            diagnostics={
                "hard_exclusions": hard_excluded,
                "soft_penalties": soft_penalties,
                "preset": preset_id,
                "gps_positive": sum(
                    1 for ev in evidence_by_path.values() if ev.get("gps_positive")
                ),
                "panorama_positive": sum(
                    1 for ev in evidence_by_path.values() if ev.get("panorama_positive")
                ),
                "low_ocr_clean": sum(
                    1 for ev in evidence_by_path.values() if ev.get("low_ocr_clean")
                ),
                "face_heavy_penalized": sum(
                    1 for ev in evidence_by_path.values() if ev.get("face_heavy_penalty")
                ),
            },
        )

    @staticmethod
    def _check_hard_exclusion(path: str, meta: dict) -> str | None:
        """
        Check if an asset should be hard-excluded from the scenic pool.

        Returns the exclusion reason string, or None if the asset passes.
        """
        # 1. Confirmed screenshot
        if meta.get("is_screenshot"):
            return "is_screenshot"

        # 2. Dimensions check
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        min_edge = min(w, h) if w and h else 0
        if min_edge > 0 and min_edge < _MIN_SCENIC_EDGE:
            return "too_small"

        # 3. Extension-based exclusion
        ext = (meta.get("ext") or "").lower()
        if not ext and path:
            ext = os.path.splitext(path)[1].lower()

        is_doc_ext = ext in _DOC_NATIVE_EXTENSIONS

        # 4. Page-like geometry
        aspect = max(w, h) / max(1, min(w, h)) if w and h else 0.0
        is_page_like = _PAGE_RATIO_MIN <= aspect <= _PAGE_RATIO_MAX

        # 5. OCR text density
        ocr_text = meta.get("ocr_text") or ""
        ocr_len = len(ocr_text.strip()) if ocr_text else 0
        has_high_ocr = ocr_len >= _HIGH_OCR_THRESHOLD

        # Hard exclusion: doc extension + page-like geometry
        if is_doc_ext and is_page_like:
            return "doc_ext_page_like"

        # Hard exclusion: doc extension + high OCR text
        if is_doc_ext and has_high_ocr:
            return "doc_ext_high_ocr"

        # Hard exclusion: page-like + very high OCR (even without doc extension)
        if is_page_like and ocr_len >= 150:
            return "page_like_very_high_ocr"

        return None

    @staticmethod
    def _build_evidence(
        path: str,
        meta: dict,
        is_landscape: bool,
        preset_id: str,
    ) -> dict:
        """Build per-path scenic evidence with positive signals and soft penalties."""
        ext = (meta.get("ext") or "").lower()
        if not ext and path:
            ext = os.path.splitext(path)[1].lower()

        ocr_text = meta.get("ocr_text") or ""
        ocr_len = len(ocr_text.strip()) if ocr_text else 0
        face_count = meta.get("face_count") or 0
        has_gps = meta.get("has_gps", False)
        duplicate_group_id = meta.get("duplicate_group_id")

        w = meta.get("width") or 0
        h = meta.get("height") or 0
        aspect = max(w, h) / max(1, min(w, h)) if w and h else 0.0
        is_page_like = _PAGE_RATIO_MIN <= aspect <= _PAGE_RATIO_MAX
        is_panorama_like = aspect >= _PANORAMA_RATIO_MIN if w and h else False

        # Positive scenic evidence
        gps_positive = bool(
            has_gps and preset_id in ScenicCandidateBuilder._GPS_BIASED_PRESETS
        )
        panorama_positive = bool(
            preset_id in ScenicCandidateBuilder._PANORAMA_PRESETS and is_panorama_like
        )
        low_ocr_clean = bool(ocr_len <= _LOW_OCR_THRESHOLD)
        face_heavy_penalty = bool(is_landscape and face_count >= _FACE_HEAVY_THRESHOLD)

        # Scenic-positive score, used downstream as structural support
        scenic_positive = 0.0
        if gps_positive:
            scenic_positive += 0.08
        if panorama_positive:
            scenic_positive += 0.10
        if low_ocr_clean:
            scenic_positive += 0.05
        if face_count == 0:
            scenic_positive += 0.03
        if ext in {".jpg", ".jpeg", ".heic", ".heif", ".webp"}:
            scenic_positive += 0.02

        # Compute soft penalty
        soft_penalty = 0.0

        # PNG with moderate OCR: mild penalty
        if ext == ".png" and _SOFT_OCR_THRESHOLD <= ocr_len < _HIGH_OCR_THRESHOLD:
            soft_penalty -= 0.05

        # Moderate OCR without page geometry: possible sign/text-heavy image
        if _SOFT_OCR_THRESHOLD <= ocr_len < _HIGH_OCR_THRESHOLD and not is_page_like:
            soft_penalty -= 0.05

        # Page-like images are suspicious for scenic
        if is_page_like:
            soft_penalty -= 0.08

        # Face-heavy landscapes are penalized but not excluded
        if face_heavy_penalty:
            soft_penalty -= 0.06

        return {
            "builder": "scenic",
            "soft_penalty": soft_penalty,
            "scenic_positive": scenic_positive,
            "gps_positive": gps_positive,
            "panorama_positive": panorama_positive,
            "low_ocr_clean": low_ocr_clean,
            "face_heavy_penalty": face_heavy_penalty,
            "duplicate_group_id": duplicate_group_id,
            "ocr_text_len": ocr_len,
            "face_count": face_count,
            "has_gps": has_gps,
            "aspect_ratio": aspect,
            "is_page_like": is_page_like,
            "is_panorama_like": is_panorama_like,
            "ext": ext,
        }
