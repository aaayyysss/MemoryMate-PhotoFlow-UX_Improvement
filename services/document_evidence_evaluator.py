# services/document_evidence_evaluator.py
# Canonical document evidence evaluation — one contract for all components.
#
# Used by:
#   - DocumentCandidateBuilder (candidate inclusion)
#   - GateEngine (hard filtering after scoring)
#   - SearchConfidencePolicy (trust evaluation)
#
# This eliminates the contract mismatch where the builder says
# "possible documents exist" but the gate says "none are legal".

"""
DocumentEvidenceEvaluator - Single source of truth for document evidence.

Usage:
    from services.document_evidence_evaluator import DocumentEvidenceEvaluator

    evaluator = DocumentEvidenceEvaluator()
    result = evaluator.evaluate(meta, path)
    # result.is_document -> True/False
    # result.has_ocr -> True/False
    # result.rejection_reason -> str or None
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

from logging_config import get_logger

logger = get_logger(__name__)


# ── Shared constants (single source of truth) ──

# Document-native extensions (strong positive signal)
DOC_NATIVE_EXTENSIONS = frozenset({'.pdf', '.tif', '.tiff', '.bmp'})

# Image extensions that need strong content evidence to pass as documents
IMAGE_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.heic', '.heif', '.webp'})

# Minimum OCR text length to count as a document signal
DOC_OCR_MIN_LENGTH = 15

# Page-like aspect ratio bounds (A4, US Letter)
PAGE_RATIO_MIN = 1.20
PAGE_RATIO_MAX = 1.60

# Minimum edge size — reject tiny images
MIN_EDGE_SIZE = 700

# Document lexicon terms for OCR content analysis
DOC_LEXICON = (
    "invoice", "receipt", "bill", "total", "amount", "date",
    "address", "account", "bank", "iban", "signature",
    "form", "application", "reference", "customer", "page",
)


@dataclass
class DocumentEvidence:
    """Result of document evidence evaluation for a single asset."""
    is_document: bool = False
    has_ocr: bool = False
    has_lexicon_hit: bool = False
    has_doc_extension: bool = False
    is_page_like: bool = False
    is_structural: bool = False
    has_text_dense_layout: bool = False
    strong_raster_document: bool = False
    rejection_reason: Optional[str] = None
    # Raw signals for downstream use
    ocr_text_len: int = 0
    face_count: int = 0
    is_screenshot: bool = False
    min_edge: int = 0
    aspect_ratio: float = 0.0


class DocumentEvidenceEvaluator:
    """
    Canonical evaluator for document evidence.

    All three consumers (builder, gate, confidence policy) call this
    evaluator so they agree on what constitutes "document evidence".
    """

    @staticmethod
    def has_ocr_signal(meta: dict) -> bool:
        """Check if OCR text contains document-like content."""
        text = (meta.get("ocr_text") or "").strip()
        if len(text) >= DOC_OCR_MIN_LENGTH:
            return True
        text_l = text.lower()
        return any(term in text_l for term in DOC_LEXICON)

    @staticmethod
    def has_lexicon_hit(meta: dict) -> bool:
        """Check if OCR text contains document lexicon terms."""
        text = (meta.get("ocr_text") or "").lower()
        return any(term in text for term in DOC_LEXICON)

    @staticmethod
    def is_page_like(meta: dict) -> bool:
        """Check if dimensions suggest a page-like aspect ratio."""
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        if not w or not h:
            return False
        aspect = max(w, h) / max(1, min(w, h))
        return PAGE_RATIO_MIN <= aspect <= PAGE_RATIO_MAX

    @staticmethod
    def get_aspect_ratio(meta: dict) -> float:
        """Compute aspect ratio."""
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        if not w or not h:
            return 0.0
        return max(w, h) / max(1, min(w, h))

    @staticmethod
    def has_text_dense_layout(meta: dict) -> bool:
        """
        Heuristic for raster documents that look like pages:
        enough OCR characters, enough OCR words, or multiple line breaks.
        This is weaker than true OCR-positive document confidence, but
        stronger than geometry alone.
        """
        text = (meta.get("ocr_text") or "").strip()
        if not text:
            return False

        if len(text) >= 40:
            return True
        if len(text.split()) >= 6:
            return True
        if text.count("\n") >= 2:
            return True
        return False

    def evaluate(self, meta: dict, path: str = "") -> DocumentEvidence:
        """
        Evaluate whether an asset qualifies as a document.

        This is the ONE function that builder, gate, and policy all use.
        The same evidence contract, the same hard rejections, the same
        extension-specific rules.

        Args:
            meta: Asset metadata dict
            path: File path (for extension detection)

        Returns:
            DocumentEvidence with all signals and final verdict
        """
        ext = (meta.get("ext") or "").lower()
        if not ext and path:
            ext = os.path.splitext(path)[1].lower()

        has_ocr = self.has_ocr_signal(meta)
        lexicon_hit = self.has_lexicon_hit(meta)
        page_like = self.is_page_like(meta)
        doc_ext = ext in DOC_NATIVE_EXTENSIONS
        is_screenshot = bool(meta.get("is_screenshot"))
        face_count = int(meta.get("face_count") or 0)
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        min_edge = min(w, h) if w and h else 0
        structural = page_like or doc_ext
        text_dense_layout = self.has_text_dense_layout(meta)

        evidence = DocumentEvidence(
            has_ocr=has_ocr,
            has_lexicon_hit=lexicon_hit,
            has_doc_extension=doc_ext,
            is_page_like=page_like,
            is_structural=structural,
            has_text_dense_layout=text_dense_layout,
            strong_raster_document=False,
            ocr_text_len=len((meta.get("ocr_text") or "").strip()),
            face_count=face_count,
            is_screenshot=is_screenshot,
            min_edge=min_edge,
            aspect_ratio=self.get_aspect_ratio(meta),
        )

        # Hard rejections (same in builder AND gate)
        if is_screenshot:
            evidence.rejection_reason = "is_screenshot"
            evidence.is_document = False
            return evidence
        if face_count > 0:
            evidence.rejection_reason = "has_faces"
            evidence.is_document = False
            return evidence
        if min_edge > 0 and min_edge < MIN_EDGE_SIZE:
            evidence.rejection_reason = "too_small"
            evidence.is_document = False
            return evidence

        # Extension-specific acceptance rules
        if ext == ".pdf":
            # PDF is strong native document evidence.
            evidence.is_document = True

        elif ext in {".tif", ".tiff", ".bmp"}:
            # Scan-like formats may pass with OCR or convincing page structure.
            evidence.is_document = has_ocr or (page_like and text_dense_layout)

        elif ext == ".png":
            # PNG is ambiguous: scanned page, export, or screenshot.
            # Require OCR OR page-like + text-dense layout.
            evidence.is_document = has_ocr or (page_like and text_dense_layout)

        elif ext in IMAGE_EXTENSIONS:
            # JPG/HEIC/WebP need stronger content evidence than geometry alone.
            evidence.strong_raster_document = bool(
                has_ocr or (page_like and lexicon_hit and text_dense_layout)
            )
            evidence.is_document = evidence.strong_raster_document

        else:
            # Unknown types remain conservative.
            evidence.is_document = has_ocr or (page_like and text_dense_layout)

        if not evidence.is_document:
            evidence.rejection_reason = "insufficient_evidence"

        return evidence
