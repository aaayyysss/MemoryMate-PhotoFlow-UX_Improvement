# services/candidate_builders/screenshot_candidate_builder.py
# Dedicated screenshot candidate builder for the "screenshots" preset.
#
# Extracted from DocumentCandidateBuilder._build_screenshots() to give
# screenshots their own builder with richer evidence (screenshot_score)
# that feeds into the w_screenshot scoring channel.
#
# Detection signals:
#   1. is_screenshot metadata flag (from camera/EXIF analysis)
#   2. Filename markers (screenshot, screen_shot, bildschirmfoto, etc.)
#   3. UI-text OCR patterns (battery, wifi, settings, etc.)
#   4. Page-like aspect ratio + high OCR text density
#
# Each signal contributes to a composite screenshot_score [0..1].

"""
ScreenshotCandidateBuilder - Dedicated screenshot retrieval.

Usage:
    from services.candidate_builders.screenshot_candidate_builder import (
        ScreenshotCandidateBuilder,
    )

    builder = ScreenshotCandidateBuilder(project_id=1)
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

# Screenshot filename markers
_SCREENSHOT_MARKERS = frozenset({
    "screenshot", "screen shot", "screen_shot", "screen-shot",
    "bildschirmfoto", "captura", "schermopname",
    "截屏", "截圖", "스크린샷", "スクリーンショット",
    "screenrecord", "screen_record", "screen-record",
})

# UI terms for OCR-based screenshot detection
_UI_TERMS = frozenset({
    "battery", "wifi", "lte", "5g", "notification",
    "settings", "search", "cancel", "back", "menu",
    "home", "share", "download", "whatsapp", "instagram",
    "telegram", "messenger", "chrome", "safari",
    "edit", "done", "reply", "delete", "save", "profile",
    "messages", "chat", "feed", "explore", "reels",
    "airplane mode", "bluetooth", "location", "brightness",
    "volume", "camera", "photos", "gallery", "messages",
    "call", "contacts", "storage", "privacy", "security",
    "update", "control center", "quick settings", "app store",
    "google play", "status bar", "signal", "alarm",
})

# Page-like aspect ratio range for screenshot detection
_SCREEN_RATIO_MIN = 1.5
_SCREEN_RATIO_MAX = 2.3
_MIN_OCR_FOR_SCREEN = 30  # chars of OCR text to count as text-dense
_MIN_UI_TERM_COUNT = 2
_RECOVERY_MIN_SIGNALS = 2


class ScreenshotCandidateBuilder(BaseCandidateBuilder):
    """
    Retrieve screenshot candidates using multi-signal detection.

    Each candidate gets a screenshot_score [0..1] that flows into
    the w_screenshot scoring channel for ranking.
    """

    @staticmethod
    def _ui_term_count(ocr_text: str) -> int:
        """Count distinct UI-term hits in OCR text."""
        if not ocr_text:
            return 0
        hits = {t for t in _UI_TERMS if t in ocr_text}
        return len(hits)

    @staticmethod
    def _looks_like_tablet_or_desktop_capture(w: int, h: int, face_count: int) -> bool:
        """
        Broader screen-like geometry for non-phone screenshots.
        Keeps faces excluded to avoid rescuing portraits.
        """
        if not w or not h or face_count > 0:
            return False
        aspect = max(w, h) / max(1, min(w, h))
        return (
            min(w, h) >= 650
            and 1.25 <= aspect <= 2.6
        )

    @staticmethod
    def _count_recovery_signals(evidence: dict) -> int:
        """Count non-semantic screenshot signals for low-confidence admission."""
        return sum([
            1 if evidence.get("filename_marker") else 0,
            1 if evidence.get("ui_text_hit") else 0,
            1 if evidence.get("looks_like_phone_screen") else 0,
            1 if evidence.get("looks_like_tablet_or_desktop_capture") else 0,
            1 if evidence.get("flat_ui_fallback") else 0,
        ])

    def build(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int = 500,
    ) -> CandidateSet:
        """Build screenshot candidate pool."""
        if not project_meta:
            return self._empty("type", "No project metadata available")

        text_terms = intent.text_terms or []
        candidates = []
        evidence_by_path = {}
        rejection_counts = {}

        for path, meta in project_meta.items():
            score, evidence = self._evaluate_screenshot(
                path, meta, text_terms
            )
            evidence["screenshot_score"] = score

            if score > 0.0:
                candidates.append(path)
                evidence_by_path[path] = evidence

                if len(candidates) >= limit:
                    break
            else:
                reason = evidence.get("rejection_reason", "not_screenshot")
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

                # Phase 2: Preserve evidence for rejected signals
                # so the orchestrator fusion path can still "rescue" them.
                # Only exclude hard negatives (faces, too small) if desired,
                # but the orchestrator handles admission logic.
                evidence_by_path[path] = evidence

        # Sort by screenshot_score descending
        candidates.sort(
            key=lambda p: evidence_by_path[p].get("screenshot_score", 0),
            reverse=True,
        )

        confidence = min(1.0, 0.3 + 0.5 * (len(candidates) / max(1, len(project_meta))))

        logger.info(
            f"[ScreenshotCandidateBuilder] screenshots: "
            f"{len(candidates)}/{len(project_meta)} candidates"
        )
        if candidates:
            top_preview = sorted(
                (
                    (p, evidence_by_path[p].get("screenshot_score", 0.0))
                    for p in candidates[:5]
                ),
                key=lambda x: x[1],
                reverse=True,
            )
            logger.info(
                f"[ScreenshotCandidateBuilder] top candidates: "
                f"{[(os.path.basename(p), round(s, 3)) for p, s in top_preview]}"
            )
        if rejection_counts:
            logger.info(
                f"[ScreenshotCandidateBuilder] rejections: {rejection_counts}"
            )

        return CandidateSet(
            family="type",
            candidate_paths=candidates,
            evidence_by_path=evidence_by_path,
            source_counts={"screenshot_candidates": len(candidates)},
            builder_confidence=confidence if candidates else 0.0,
            ready_state="ready" if candidates else "empty",
            notes=[f"Screenshot builder: {len(candidates)} candidates"],
            diagnostics={
                "rejections": rejection_counts,
                "pre_filter_candidates": len(project_meta),
                "kept": len(candidates),
                "mode": "screenshots",
                "has_any_screenshot_flag": any(
                    bool((project_meta.get(p) or {}).get("is_screenshot"))
                    for p in project_meta
                ),
                "has_any_ocr_text": any(
                    bool((project_meta.get(p) or {}).get("ocr_text"))
                    for p in project_meta
                ),
            },
        )

    @staticmethod
    def _evaluate_screenshot(
        path: str,
        meta: dict,
        text_terms: List[str],
    ) -> tuple:
        score = 0.0
        evidence = {"builder": "screenshot"}

        # Signal 1: metadata flag
        is_screenshot = bool(meta.get("is_screenshot"))
        if is_screenshot:
            score += 0.45
        evidence["is_screenshot_flag"] = is_screenshot

        # Signal 2: filename markers
        basename_lower = os.path.basename(path).lower() if path else ""
        filename_marker = any(m in basename_lower for m in _SCREENSHOT_MARKERS)
        if filename_marker:
            score += 0.25
        evidence["filename_marker"] = filename_marker

        # Signal 3: OCR UI text patterns
        ocr_text = (meta.get("ocr_text") or "").lower()
        ocr_len = len(ocr_text)

        # Count unique UI term hits
        ui_hits = {t for t in _UI_TERMS if t in ocr_text}
        ui_hit_count = len(ui_hits)
        ui_hit = ui_hit_count > 0

        if ui_hit:
            # Progressive scoring for UI terms:
            # 1 hit = 0.15, 2 hits = 0.20, 3+ hits = 0.30
            if ui_hit_count >= 3:
                ui_score = 0.30
            elif ui_hit_count == 2:
                ui_score = 0.20
            else:
                ui_score = 0.15
            score += ui_score

        evidence["ui_text_hit"] = ui_hit
        evidence["ui_hit_count"] = ui_hit_count
        evidence["ocr_text_len"] = ocr_len

        # Signal 4: dimensions / aspect
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        aspect = (max(w, h) / max(1, min(w, h))) if w and h else 0.0
        face_count = int(meta.get("face_count") or 0)

        # Phone: 16:9 to 21:9 (portrait or landscape)
        looks_like_phone_screen = (
            700 <= min(w, h) <= 1800
            and 1.5 <= aspect <= 2.4
            and face_count == 0
        )

        # Tablet: 4:3, 3:2
        looks_like_tablet_screen = (
            700 <= min(w, h) <= 2500
            and 1.2 <= aspect < 1.5
            and face_count == 0
        )

        # Desktop: 16:10, 16:9, 21:9
        looks_like_desktop_screen = (
            w >= 1280 and h >= 720
            and 1.3 <= aspect <= 2.5
            and face_count == 0
        )

        if looks_like_phone_screen:
            score += 0.10
        elif looks_like_tablet_screen:
            score += 0.08
        elif looks_like_desktop_screen:
            score += 0.05

        evidence["looks_like_phone_screen"] = looks_like_phone_screen
        evidence["looks_like_tablet_screen"] = looks_like_tablet_screen
        evidence["looks_like_desktop_screen"] = looks_like_desktop_screen

        # Signal 5: OCR Density (not a document, but text-heavy UI)
        dense_ui_ocr = ocr_len >= 150 and face_count == 0
        if dense_ui_ocr:
            score += 0.05
        evidence["dense_ui_ocr"] = dense_ui_ocr

        # Signal 6: flat PNG / UI-like fallback
        ext = os.path.splitext(path)[1].lower() if path else ""
        flat_ui_fallback = (
            ext == ".png"
            and face_count == 0
            and min(w, h) >= 600
            and (
                ui_hit
                or filename_marker
                or is_screenshot
                or ocr_len >= _MIN_OCR_FOR_SCREEN
            )
        )
        if flat_ui_fallback:
            score += 0.10
        evidence["flat_ui_fallback"] = flat_ui_fallback

        # Signal 7: Query text match in OCR
        term_hit = False
        if text_terms and ocr_text:
            term_hit = any(t.lower() in ocr_text for t in text_terms)
            if term_hit:
                score += 0.05
        evidence["text_term_hit"] = term_hit

        # Hard negatives
        if face_count > 0:
            evidence["rejection_reason"] = "has_faces"
            return 0.0, evidence

        if min(w, h) > 0 and min(w, h) < 400:
            evidence["rejection_reason"] = "too_small"
            return 0.0, evidence

        # Final threshold
        if score < 0.20:
            if not is_screenshot and not filename_marker and not ui_hit and not looks_like_phone_screen and not looks_like_tablet_screen and not looks_like_desktop_screen and not flat_ui_fallback:
                evidence["rejection_reason"] = "no_screenshot_signals"
            else:
                evidence["rejection_reason"] = "weak_screenshot_score"
            return 0.0, evidence

        return min(1.0, score), evidence
