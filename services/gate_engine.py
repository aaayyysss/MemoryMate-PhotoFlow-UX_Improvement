# services/gate_engine.py
# Extracted from SearchOrchestrator._apply_gates()
#
# Hard structural filters applied after scoring but before dedup and top_k.
# Aligned with Apple Photos / Google Photos / Lightroom category purity.
#
# Uses existing meta fields only:
#   is_screenshot, face_count, width, height, has_gps, flag, ext, ocr_text

"""
GateEngine - Hard pre-filters for search category purity.

Presets declare which gates apply via gate_profile dicts.
The engine enforces them in one pass, logging dropped counts
for debuggability.

Usage:
    from services.gate_engine import GateEngine

    engine = GateEngine()
    kept, dropped = engine.apply(scored_results, plan, project_meta)
"""

import os
from collections import Counter
from typing import List, Dict, Tuple, Optional, Any

from logging_config import get_logger
from services.document_evidence_evaluator import (
    DocumentEvidenceEvaluator,
    DOC_NATIVE_EXTENSIONS as _DOC_NATIVE_EXTENSIONS,
    IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS,
    DOC_OCR_MIN_LENGTH as _DOC_OCR_MIN_LENGTH,
    PAGE_RATIO_MIN as _PAGE_RATIO_MIN,
    PAGE_RATIO_MAX as _PAGE_RATIO_MAX,
    DOC_LEXICON as _DOC_LEXICON,
)

logger = get_logger(__name__)

# Module-level canonical evaluator instance
_doc_evaluator = DocumentEvidenceEvaluator()


class GateEngine:
    """
    Hard structural filters applied after scoring, before dedup/top_k.

    Gate profiles are declarative (set per preset via gate_profile dict).
    This consolidates all hard-filtering logic into one place.
    """

    # ── Document signal helpers (delegating to canonical evaluator) ──

    @staticmethod
    def has_document_ocr_signal(meta: dict) -> bool:
        """Check if OCR text contains document-like content."""
        return _doc_evaluator.has_ocr_signal(meta)

    @staticmethod
    def is_page_like(meta: dict) -> bool:
        """Check if dimensions suggest a page-like aspect ratio."""
        return _doc_evaluator.is_page_like(meta)

    def _passes_document_gate(self, meta: dict, path: str = "") -> bool:
        """
        Strict document gate — delegates to canonical DocumentEvidenceEvaluator.

        Builder and gate now use the same evidence contract.
        """
        evidence = _doc_evaluator.evaluate(meta, path)
        return evidence.is_document

    @staticmethod
    def _passes_screenshot_gate(meta: dict) -> bool:
        """Strict screenshot gate — only true screenshots pass."""
        return bool(meta.get("is_screenshot"))

    @staticmethod
    def _passes_pets_gate(meta: dict) -> bool:
        """
        Precision gate for pets preset.

        Excludes screenshots and photos with detected faces to prevent
        human portraits from dominating the pet category.
        """
        if meta.get("is_screenshot"):
            return False
        if (meta.get("face_count") or 0) > 0:
            return False
        return True

    def _rescue_document_result_from_builder_evidence(
        self,
        result: Any,
        builder_evidence: Optional[Dict[str, dict]],
    ) -> bool:
        """
        Document rescue is intentionally narrow.

        A result may survive the strict document gate only if the builder
        explicitly marked it as a document candidate with canonical evidence
        or as a low-confidence structural document admit.
        """
        if not builder_evidence:
            return False

        evidence = builder_evidence.get(result.path) or {}
        if not evidence:
            return False

        return bool(
            evidence.get("ocr_fts_hit")
            or evidence.get("ocr_lexicon_hit")
            or evidence.get("doc_extension")
            or evidence.get("strong_raster_document")
            or (
                evidence.get("low_confidence_admit")
                and evidence.get("has_text_dense_layout")
            )
        )

    def _rescue_screenshot_result_from_builder_evidence(
        self,
        result: Any,
        builder_evidence: Optional[Dict[str, dict]],
    ) -> bool:
        """
        Screenshot rescue logic.

        A candidate may bypass the strict screenshot gate if it has a
        sufficient screenshot score (either from builder or semantic prior)
        and at least one valid structural signal.
        """
        if not builder_evidence:
            return False

        evidence = builder_evidence.get(result.path) or {}
        if not evidence:
            return False

        screenshot_score = float(evidence.get("screenshot_score", 0.0) or 0.0)

        # 1. Admit anything with a high screenshot score
        if screenshot_score >= 0.30:
            return True

        # 2. Rescue assets with valid structural signals if they have a
        # positive score (either from builder or 0.20 supplement prior).
        # This aligns with orchestrator admissibility.
        has_structural = any([
            bool(evidence.get("is_screenshot_flag")),
            bool(evidence.get("filename_marker")),
            bool(evidence.get("ui_text_hit")),
            bool(evidence.get("looks_like_phone_screen")),
            bool(evidence.get("looks_like_tablet_screen")),
            bool(evidence.get("looks_like_desktop_screen")),
            bool(evidence.get("dense_ui_ocr")),
            bool(evidence.get("flat_ui_fallback")),
        ])

        if has_structural and screenshot_score >= 0.15:
            return True

        return False

    def apply(
        self,
        scored: list,
        plan: Any,
        project_meta: Dict[str, Dict],
        builder_evidence: Optional[Dict[str, dict]] = None,
    ) -> Tuple[list, Dict[str, int]]:
        """
        Apply hard gates to scored results.

        Args:
            scored: List of ScoredResult objects (from ranker)
            plan: QueryPlan with gate fields
            project_meta: {path: {is_screenshot, face_count, width, height, has_gps, flag, ...}}

        Returns:
            (kept_results, dropped_counts) where dropped_counts maps gate_name -> count
        """
        if not scored:
            return scored, {}

        # Read gate flags from plan
        preset_id = getattr(plan, 'preset_id', None)
        require_screenshot = getattr(plan, 'require_screenshot', False)
        exclude_screenshots = (
            getattr(plan, 'exclude_screenshots', False)
            or preset_id == "documents"
        )
        exclude_faces = getattr(plan, 'exclude_faces', False)
        require_faces = getattr(plan, 'require_faces', False)
        min_face_count = getattr(plan, 'min_face_count', 0)
        require_gps = getattr(plan, 'require_gps_gate', False)
        min_edge = getattr(plan, 'min_edge_size', 0)
        require_doc_signal = getattr(plan, 'require_document_signal', False)

        # Detect preset-specific gates
        is_pets = (preset_id == "pets")

        # Safety: skip face-requiring gates if face data is essentially absent
        # Note: For people_event presets, the orchestrator now blocks execution
        # entirely when face coverage < 10%. This gate-level fallback handles
        # edge cases where face data is nearly absent (<1%) for non-preset queries.
        if require_faces or min_face_count > 0:
            total_photos = len(project_meta) if project_meta else 0
            face_photo_count = sum(
                1 for m in project_meta.values()
                if (m.get("face_count", 0) or 0) > 0
            ) if project_meta else 0
            face_coverage = face_photo_count / total_photos if total_photos > 0 else 0
            if face_coverage < 0.01 and total_photos > 0:
                logger.info(
                    f"[GateEngine] Face coverage < 1% "
                    f"({face_photo_count}/{total_photos}); "
                    f"skipping require_faces/min_face_count gates for "
                    f"preset={preset_id!r}. "
                    f"Run face detection for better results."
                )
                require_faces = False
                min_face_count = 0

        # Fast path: no gates active
        if not any([require_screenshot, exclude_screenshots, exclude_faces,
                     require_faces, min_face_count > 0, require_gps,
                     min_edge > 0, require_doc_signal, is_pets]):
            return scored, {}

        dropped = Counter()
        kept: list = []

        # Build normalized-path → meta lookup for Bug B fix
        _norm_meta: Dict[str, Dict] = {}
        for mp, mv in project_meta.items():
            _norm_meta[os.path.normpath(mp).lower()] = mv

        for r in scored:
            # Try exact match first, then normalized match
            meta = project_meta.get(r.path, {})
            if not meta:
                norm_key = os.path.normpath(r.path).lower()
                meta = _norm_meta.get(norm_key, {})

            is_screenshot = bool(meta.get("is_screenshot", False))
            face_count = int(meta.get("face_count") or 0)
            has_gps = bool(meta.get("has_gps", False))

            # Gate: require screenshot (strict flag or semantic supplement hit)
            if require_screenshot:
                # We allow it to pass if it has the explicit metadata flag
                # OR if it has a positive screenshot_score from the pipeline
                # (which covers semantic supplement hits).
                screenshot_score = getattr(r, 'screenshot_score', 0.0)

                # Check for rescue via builder evidence (semantic supplement)
                rescued = False
                if builder_evidence and self._rescue_screenshot_result_from_builder_evidence(r, builder_evidence):
                    rescued = True

                if not rescued and not self._passes_screenshot_gate(meta) and screenshot_score < 0.20:
                    dropped["require_screenshot"] += 1
                    continue
                logger.debug(
                    f"[GateEngine] Screenshot PASS: {os.path.basename(r.path)} "
                    f"is_screenshot={is_screenshot} score={r.final_score:.4f} rescued={rescued}"
                )

            # Gate: exclude screenshots
            if exclude_screenshots and is_screenshot:
                dropped["exclude_screenshot"] += 1
                continue

            # Gate: exclude faces
            if exclude_faces and face_count > 0:
                dropped["exclude_faces"] += 1
                continue

            # Gate: require faces present
            if require_faces and face_count == 0:
                dropped["require_faces"] += 1
                continue

            # Gate: minimum face count
            if min_face_count > 0 and face_count < min_face_count:
                dropped["min_face_count"] += 1
                continue

            # Gate: require GPS
            if require_gps and not has_gps:
                dropped["require_gps"] += 1
                continue

            # Gate: minimum edge size
            if min_edge > 0:
                w = meta.get("width")
                h = meta.get("height")
                try:
                    w = int(w) if w is not None else None
                    h = int(h) if h is not None else None
                except (TypeError, ValueError):
                    w, h = None, None
                if w is not None and h is not None and min(w, h) < min_edge:
                    dropped["min_edge_size"] += 1
                    continue

            # Gate: strict document signal (extension-aware)
            if require_doc_signal:
                # Check for rescue via builder evidence (DocumentCandidateBuilder low-confidence)
                rescued = False
                if builder_evidence and self._rescue_document_result_from_builder_evidence(r, builder_evidence):
                    rescued = True

                if not rescued and not self._passes_document_gate(meta, r.path):
                    dropped["require_document_signal"] += 1
                    continue

            # Gate: pets precision (no faces, no screenshots)
            if is_pets:
                if not self._passes_pets_gate(meta):
                    dropped["pets_precision_gate"] += 1
                    continue

            if require_doc_signal and builder_evidence:
                evidence = builder_evidence.get(r.path) or {}
                if not (
                    evidence.get("ocr_fts_hit")
                    or evidence.get("ocr_lexicon_hit")
                    or evidence.get("doc_extension")
                    or evidence.get("structural_hit")
                    or evidence.get("low_confidence_admit")
                    or self._passes_document_gate(meta, r.path)
                ):
                    logger.warning(
                        f"[GateEngine] DOCUMENT_SURVIVOR_ANOMALY: "
                        f"{os.path.basename(r.path)} survived without canonical document evidence"
                    )

            kept.append(r)

        if dropped:
            logger.info(
                f"[GateEngine] Gates applied preset={preset_id!r} "
                f"kept={len(kept)}/{len(scored)} dropped={dict(dropped)}"
            )

        # Log gate survivors for debugging
        if (require_screenshot or exclude_screenshots or exclude_faces
                or require_doc_signal or is_pets) and kept:
            for k in kept[:5]:
                kmeta = project_meta.get(k.path, {})
                if not kmeta:
                    kmeta = _norm_meta.get(os.path.normpath(k.path).lower(), {})
                logger.debug(
                    f"[GateEngine] Survivor: {os.path.basename(k.path)} "
                    f"is_screenshot={kmeta.get('is_screenshot')} "
                    f"face_count={kmeta.get('face_count', 0)} "
                    f"ext={os.path.splitext(k.path)[1].lower()} "
                    f"score={k.final_score:.4f}"
                )

        return kept, dict(dropped)
