# services/candidate_builders/document_candidate_builder.py
# OCR-first and structure-first candidate builder for type family.
#
# For Documents and Screenshots, OCR is the retrieval backbone, not a
# bonus score. This matches how Google describes Ask Photos as being
# able to "read text in the image" when required.
#
# Retrieval priority order for documents:
#   1. OCR FTS hits
#   2. OCR lexicon hits
#   3. Structural document candidates (page-like geometry)
#   4. Document-like extension candidates
#   5. Optional semantic rerank inside candidate pool (later)
#
# CLIP can be a reranker or fallback, not the first gate.

"""
DocumentCandidateBuilder - OCR/structure-first retrieval for type family.

Usage:
    from services.candidate_builders.document_candidate_builder import (
        DocumentCandidateBuilder,
    )

    builder = DocumentCandidateBuilder(project_id=1)
    candidate_set = builder.build(intent, project_meta)
"""

from __future__ import annotations
import os
import re
from typing import Dict, List, Set, Optional

from services.candidate_builders.base_candidate_builder import (
    BaseCandidateBuilder,
    CandidateSet,
)
from services.query_intent_planner import QueryIntent
from services.document_evidence_evaluator import (
    DocumentEvidenceEvaluator,
    DOC_NATIVE_EXTENSIONS as _DOC_NATIVE_EXTENSIONS,
    IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS,
    DOC_OCR_MIN_LENGTH as _DOC_OCR_MIN_LENGTH,
    PAGE_RATIO_MIN as _PAGE_RATIO_MIN,
    PAGE_RATIO_MAX as _PAGE_RATIO_MAX,
    MIN_EDGE_SIZE as _MIN_EDGE_SIZE,
    DOC_LEXICON as _DOC_LEXICON,
)
from logging_config import get_logger

logger = get_logger(__name__)

# Canonical evaluator — same contract as GateEngine
_doc_evaluator = DocumentEvidenceEvaluator()

# Screenshot markers for the screenshot sub-builder
_SCREENSHOT_MARKERS = frozenset({
    "screenshot", "screen shot", "screen_shot", "screen-shot",
    "bildschirmfoto", "captura", "schermopname",
})

# UI terms for OCR-based screenshot detection
_UI_TERMS = frozenset({
    "battery", "wifi", "lte", "5g", "notification",
    "settings", "search", "cancel", "back", "menu",
    "home", "share", "download", "whatsapp", "instagram",
    "telegram", "messenger", "chrome", "safari",
})


class DocumentCandidateBuilder(BaseCandidateBuilder):
    """
    Retrieve document-like assets from OCR and structure first.

    For documents:
      - OCR FTS hits → OCR lexicon hits → structural → extension → merge
      - Hard exclusions: screenshots, faces, tiny images

    For screenshots:
      - Screenshot flag → filename markers → UI-text OCR → merge
      - No generic CLIP-only fallback
    """

    def build(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int = 500,
    ) -> CandidateSet:
        """Build candidate pool for type-family queries."""
        preset_id = intent.preset_id
        if not project_meta:
            return self._empty("type", "No project metadata available")

        if preset_id == "screenshots":
            return self._build_screenshots(intent, project_meta, limit)
        else:
            return self._build_documents(intent, project_meta, limit)

    def _build_documents(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int,
    ) -> CandidateSet:
        """Build document candidate pool from OCR + structure."""
        text_terms = intent.text_terms or []

        # Source retrieval (each produces a set of paths)
        ocr_fts_paths = self._query_ocr_fts(
            self.project_id, text_terms, limit
        )
        ocr_lexicon_paths = self._query_document_lexicon(
            self.project_id, text_terms, project_meta, limit
        )
        structural_paths = self._query_structural_documents(
            project_meta, limit
        )
        extension_paths = self._query_doc_extensions(project_meta, limit)

        # Merge all sources
        all_candidates = self._merge_sources(
            ocr_fts_paths, ocr_lexicon_paths, structural_paths, extension_paths
        )

        # Apply exclusions using canonical DocumentEvidenceEvaluator.
        # Key change: instead of a hard legal gate that returns 0 when
        # OCR is absent, we admit low-confidence candidates with relaxed
        # evidence and let SearchConfidencePolicy label the trust level.
        #
        # Hard exclusions (always rejected):
        #   - is_screenshot (wrong category)
        #   - has_faces (likely a photo, not a document)
        #   - too_small (below MIN_EDGE_SIZE)
        #
        # Relaxed admission (kept as low-confidence):
        #   - page-like geometry without OCR
        #   - doc extension without OCR
        #   - These are labeled "low_confidence" in evidence so
        #     SearchConfidencePolicy can warn honestly.
        kept = []
        evidence_by_path = {}
        rejection_counts = {}
        low_confidence_count = 0
        for path in all_candidates:
            meta = project_meta.get(path, {})

            doc_evidence = _doc_evaluator.evaluate(meta, path)

            # Hard rejections: screenshot, faces, too small
            if doc_evidence.rejection_reason in ("is_screenshot", "has_faces", "too_small"):
                rejection_counts[doc_evidence.rejection_reason] = (
                    rejection_counts.get(doc_evidence.rejection_reason, 0) + 1
                )
                continue

            # Full acceptance: canonical evaluator says it's a document
            is_low_confidence = False
            if not doc_evidence.is_document:
                # Phase 9: low-confidence admission is now narrow.
                # We only keep structural-only candidates when they are
                # plausibly document-like raster pages, not generic photos.
                if (
                    doc_evidence.is_page_like
                    and doc_evidence.has_text_dense_layout
                    and not doc_evidence.is_screenshot
                    and doc_evidence.face_count == 0
                ):
                    is_low_confidence = True
                    low_confidence_count += 1
                else:
                    reason = doc_evidence.rejection_reason or "insufficient_evidence"
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                    continue

            # Build evidence (augmented with canonical evaluation)
            evidence = self._build_evidence(
                path, meta, text_terms,
                ocr_fts_paths, ocr_lexicon_paths,
                structural_paths, extension_paths,
            )
            # Tag confidence level for downstream policy evaluation
            evidence["confidence_level"] = "low" if is_low_confidence else "high"
            evidence["ocr_missing"] = not doc_evidence.has_ocr
            kept.append(path)
            evidence_by_path[path] = evidence

        # Limit
        kept = kept[:limit]

        source_counts = {
            "ocr_fts": len(ocr_fts_paths),
            "ocr_lexicon": len(ocr_lexicon_paths),
            "structural": len(structural_paths),
            "extension": len(extension_paths),
            "after_exclusions": len(kept),
        }

        confidence = self._score_builder_confidence(
            len(kept),
            len(ocr_fts_paths) + len(ocr_lexicon_paths),
            len(structural_paths),
        )

        logger.info(
            f"[DocumentCandidateBuilder] documents: "
            f"{len(kept)}/{len(project_meta)} candidates "
            f"(ocr_fts={len(ocr_fts_paths)}, lexicon={len(ocr_lexicon_paths)}, "
            f"structural={len(structural_paths)}, extension={len(extension_paths)}, "
            f"low_confidence={low_confidence_count})"
        )
        if rejection_counts:
            logger.info(
                f"[DocumentCandidateBuilder] rejections: {rejection_counts} "
                f"({sum(rejection_counts.values())} total from "
                f"{len(all_candidates)} pre-filter candidates)"
            )
        if low_confidence_count > 0:
            logger.info(
                f"[DocumentCandidateBuilder] {low_confidence_count} candidates "
                f"admitted as low-confidence (structural evidence without OCR). "
                f"Run OCR processing for better document detection."
            )

        return CandidateSet(
            family="type",
            candidate_paths=kept,
            evidence_by_path=evidence_by_path,
            source_counts=source_counts,
            builder_confidence=confidence,
            ready_state="ready" if kept else "empty",
            notes=[f"Document builder: {len(kept)} candidates "
                   f"({low_confidence_count} low-confidence)"],
            diagnostics={
                "rejections": rejection_counts,
                "pre_filter_candidates": len(all_candidates),
                "admitted": len(kept),
                "low_confidence_candidates": low_confidence_count,
                "structural_only_admits": sum(
                    1 for ev in evidence_by_path.values()
                    if ev.get("low_confidence_admit")
                ),
                "strong_raster_documents": sum(
                    1 for ev in evidence_by_path.values()
                    if ev.get("strong_raster_document")
                ),
                "text_dense_layout_admits": sum(
                    1 for ev in evidence_by_path.values()
                    if ev.get("has_text_dense_layout")
                ),
            },
        )

    def _build_screenshots(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int,
    ) -> CandidateSet:
        """Build screenshot candidate pool."""
        text_terms = intent.text_terms or []
        candidates = []
        evidence_by_path = {}

        for path, meta in project_meta.items():
            is_screenshot = bool(meta.get("is_screenshot"))
            basename_lower = os.path.basename(path).lower() if path else ""
            filename_marker = any(m in basename_lower for m in _SCREENSHOT_MARKERS)

            # OCR-based UI detection
            ocr_text = (meta.get("ocr_text") or "").lower()
            ui_hit = any(t in ocr_text for t in _UI_TERMS) if ocr_text else False

            # Text term match in OCR
            term_hit = False
            if text_terms and ocr_text:
                term_hit = any(t.lower() in ocr_text for t in text_terms)

            if is_screenshot or filename_marker or ui_hit:
                evidence = {
                    "builder": "screenshot",
                    "is_screenshot_flag": is_screenshot,
                    "filename_marker": filename_marker,
                    "ui_text_hit": ui_hit,
                    "text_term_hit": term_hit,
                    "ocr_text_len": len(ocr_text),
                }
                candidates.append(path)
                evidence_by_path[path] = evidence

            if len(candidates) >= limit:
                break

        logger.info(
            f"[DocumentCandidateBuilder] screenshots: "
            f"{len(candidates)}/{len(project_meta)} candidates"
        )

        return CandidateSet(
            family="type",
            candidate_paths=candidates,
            evidence_by_path=evidence_by_path,
            source_counts={"screenshot_candidates": len(candidates)},
            builder_confidence=0.8 if candidates else 0.0,
            ready_state="ready" if candidates else "empty",
            notes=[f"Screenshot builder: {len(candidates)} candidates"],
        )

    # ── Source retrieval methods ──

    def _query_ocr_fts(
        self,
        project_id: int,
        text_terms: List[str],
        limit: int,
    ) -> Set[str]:
        """Query OCR FTS5 index for matching paths."""
        if not text_terms:
            return set()

        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            import re as _re
            tokens = []
            for term in text_terms:
                for t in _re.findall(r"\w+", term):
                    tokens.append(t)
            if not tokens:
                return set()
            fts_query = " OR ".join(f'"{t}"' for t in tokens)
            with db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT pm.path
                    FROM ocr_fts5 fts
                    JOIN photo_metadata pm ON fts.rowid = pm.id
                    WHERE fts.ocr_text MATCH ? AND pm.project_id = ?
                    LIMIT ?
                """, (fts_query, project_id, limit)).fetchall()
                return {row["path"] for row in rows}
        except Exception:
            return set()

    @staticmethod
    def _query_document_lexicon(
        project_id: int,
        text_terms: List[str],
        project_meta: Dict[str, dict],
        limit: int,
    ) -> Set[str]:
        """Find paths where OCR text contains document lexicon terms."""
        results = set()
        search_terms = list(_DOC_LEXICON)
        if text_terms:
            search_terms.extend(t.lower() for t in text_terms)

        for path, meta in project_meta.items():
            ocr_text = (meta.get("ocr_text") or "").lower()
            if not ocr_text or len(ocr_text) < _DOC_OCR_MIN_LENGTH:
                continue
            if any(term in ocr_text for term in search_terms):
                results.add(path)
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _query_structural_documents(
        project_meta: Dict[str, dict],
        limit: int,
    ) -> Set[str]:
        """Find paths with page-like structure (aspect ratio, dimensions)."""
        results = set()
        for path, meta in project_meta.items():
            w = meta.get("width") or 0
            h = meta.get("height") or 0
            if not w or not h:
                continue
            if min(w, h) < _MIN_EDGE_SIZE:
                continue
            aspect = max(w, h) / max(1, min(w, h))
            if _PAGE_RATIO_MIN <= aspect <= _PAGE_RATIO_MAX:
                results.add(path)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _query_doc_extensions(
        project_meta: Dict[str, dict],
        limit: int,
    ) -> Set[str]:
        """Find paths with document-native file extensions."""
        results = set()
        for path in project_meta:
            ext = os.path.splitext(path)[1].lower() if path else ""
            if ext in _DOC_NATIVE_EXTENSIONS:
                results.add(path)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _merge_sources(*path_sets: Set[str]) -> Set[str]:
        """Merge multiple source path sets into one."""
        merged = set()
        for ps in path_sets:
            merged |= ps
        return merged

    @staticmethod
    def _build_evidence(
        path: str,
        meta: dict,
        matched_terms: List[str],
        ocr_fts_paths: Set[str],
        ocr_lexicon_paths: Set[str],
        structural_paths: Set[str],
        extension_paths: Set[str],
    ) -> dict:
        """Build per-path evidence payload."""
        ext = os.path.splitext(path)[1].lower() if path else ""
        ocr_text = (meta.get("ocr_text") or "").lower()
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        aspect = max(w, h) / max(1, min(w, h)) if w and h else 0

        # Which terms actually matched in OCR
        terms_found = [t for t in matched_terms if t.lower() in ocr_text]

        ocr_fts_hit = path in ocr_fts_paths
        ocr_lexicon_hit = path in ocr_lexicon_paths
        doc_extension = ext in _DOC_NATIVE_EXTENSIONS
        structural_hit = path in structural_paths

        low_confidence_admit = (
            structural_hit
            and not ocr_fts_hit
            and not ocr_lexicon_hit
            and not doc_extension
        )

        doc_eval = _doc_evaluator.evaluate(meta, path)

        return {
            "builder": "document",
            "ocr_fts_hit": ocr_fts_hit,
            "ocr_lexicon_hit": ocr_lexicon_hit,
            "ocr_terms": terms_found,
            "ocr_text_len": len(meta.get("ocr_text") or ""),
            "doc_extension": doc_extension,
            "page_like_ratio": _PAGE_RATIO_MIN <= aspect <= _PAGE_RATIO_MAX,
            "structural_hit": structural_hit,
            "low_confidence_admit": low_confidence_admit,
            "has_text_dense_layout": doc_eval.has_text_dense_layout,
            "strong_raster_document": doc_eval.strong_raster_document,
            "face_count": meta.get("face_count") or 0,
            "screenshot_flag": bool(meta.get("is_screenshot")),
        }

    @staticmethod
    def _score_builder_confidence(
        total: int,
        ocr_hits: int,
        structural_hits: int,
    ) -> float:
        """Score builder confidence based on evidence quality."""
        if total == 0:
            return 0.0
        # More OCR hits = higher confidence
        ocr_ratio = ocr_hits / max(1, total)
        structural_ratio = structural_hits / max(1, total)
        # OCR evidence is stronger than structural alone
        return min(1.0, 0.3 + 0.5 * ocr_ratio + 0.2 * structural_ratio)
