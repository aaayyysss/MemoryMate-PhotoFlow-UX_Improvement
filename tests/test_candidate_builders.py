# tests/test_candidate_builders.py
# Tests for Phase 4: Family-first hybrid retrieval
#
# Tests the new candidate builder architecture:
#   - QueryIntentPlanner decomposition
#   - DocumentCandidateBuilder (OCR-first retrieval)
#   - PeopleCandidateBuilder (face-first retrieval)
#   - SearchConfidencePolicy (trust evaluation)
#
# Run: python -m pytest tests/test_candidate_builders.py -v

import pytest
import sys
import os
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Mock PySide6 before any import touches it
_pyside_mock = mock.MagicMock()
sys.modules.setdefault('PySide6', _pyside_mock)
sys.modules.setdefault('PySide6.QtCore', _pyside_mock)
sys.modules.setdefault('PySide6.QtWidgets', _pyside_mock)
sys.modules.setdefault('PySide6.QtGui', _pyside_mock)


# ══════════════════════════════════════════════════════════════════════
# QueryIntentPlanner Tests
# ══════════════════════════════════════════════════════════════════════

class TestQueryIntentPlanner:
    """Test query decomposition into structured intent."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.query_intent_planner import QueryIntentPlanner
        self.planner = QueryIntentPlanner(project_id=1)

    def test_document_query_sets_type_family(self):
        intent = self.planner.plan("invoice from 2024")
        assert intent.family_hint == "type"
        assert intent.require_ocr is True
        assert 2024 in intent.year_terms

    def test_screenshot_query_sets_type_family(self):
        intent = self.planner.plan("screenshots with WhatsApp text")
        assert intent.family_hint == "type"
        assert intent.require_screenshot is True

    def test_wedding_query_sets_people_event(self):
        intent = self.planner.plan("wedding photos")
        assert intent.family_hint == "people_event"
        assert intent.require_faces is True

    def test_person_at_beach_decomposes(self):
        intent = self.planner.plan("ammar at the beach in 2024")
        assert 2024 in intent.year_terms
        assert "beach" in intent.scene_terms

    def test_pet_query_sets_animal_object(self):
        intent = self.planner.plan("my dog in the garden")
        assert intent.family_hint == "animal_object"

    def test_scenic_query(self):
        intent = self.planner.plan("sunset over the mountains")
        assert intent.family_hint == "scenic"
        assert "sunset" in intent.scene_terms
        assert "mountains" in intent.scene_terms

    def test_preset_overrides_family(self):
        intent = self.planner.plan("", preset_id="documents")
        assert intent.family_hint == "type"

    def test_date_extraction_year(self):
        intent = self.planner.plan("photos from 2023")
        assert 2023 in intent.year_terms
        assert intent.date_from == "2023-01-01"
        assert intent.date_to == "2023-12-31"

    def test_quality_sort_extraction(self):
        intent = self.planner.plan("best sunset photos")
        assert intent.quality_sort == "best"

    def test_text_terms_for_documents(self):
        intent = self.planner.plan("documents with invoice number")
        assert intent.family_hint == "type"
        assert any("invoice" in t for t in intent.text_terms) or "invoice" in intent.normalized_query

    def test_limit_extraction(self):
        intent = self.planner.plan("top 10 photos")
        assert intent.result_limit == 10

    def test_confidence_increases_with_slots(self):
        bare = self.planner.plan("something")
        rich = self.planner.plan("wedding photos from 2024")
        assert rich.planner_confidence > bare.planner_confidence

    def test_preset_gives_high_confidence(self):
        intent = self.planner.plan("", preset_id="documents")
        assert intent.planner_confidence >= 0.5


# ══════════════════════════════════════════════════════════════════════
# CandidateSet Tests
# ══════════════════════════════════════════════════════════════════════

class TestCandidateSet:
    """Test CandidateSet dataclass properties."""

    def test_empty_candidate_set(self):
        from services.candidate_builders.base_candidate_builder import CandidateSet
        cs = CandidateSet(family="type")
        assert cs.count == 0
        assert cs.is_ready

    def test_not_ready_state(self):
        from services.candidate_builders.base_candidate_builder import CandidateSet
        cs = CandidateSet(family="people_event", ready_state="not_ready")
        assert not cs.is_ready

    def test_partial_state_is_ready(self):
        from services.candidate_builders.base_candidate_builder import CandidateSet
        cs = CandidateSet(family="type", ready_state="partial")
        assert cs.is_ready


# ══════════════════════════════════════════════════════════════════════
# DocumentCandidateBuilder Tests
# ══════════════════════════════════════════════════════════════════════

class TestDocumentCandidateBuilder:
    """Test OCR-first document candidate retrieval."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.candidate_builders.document_candidate_builder import (
            DocumentCandidateBuilder,
        )
        self.builder = DocumentCandidateBuilder(project_id=1)

    def _make_intent(self, **kwargs):
        from services.query_intent_planner import QueryIntent
        defaults = {"raw_query": "documents", "preset_id": "documents",
                     "family_hint": "type"}
        defaults.update(kwargs)
        return QueryIntent(**defaults)

    def _make_meta(self):
        """Build a test project_meta dict with a mix of documents and photos."""
        return {
            "/photos/invoice.pdf": {
                "width": 2480, "height": 3508, "is_screenshot": False,
                "face_count": 0, "ocr_text": "Invoice Amount: $234.50 Date: 2024-01-15",
                "ext": ".pdf",
            },
            "/photos/receipt.png": {
                "width": 1200, "height": 1600, "is_screenshot": False,
                "face_count": 0, "ocr_text": "Receipt Total $45.00",
                "ext": ".png",
            },
            "/photos/beach_sunset.jpg": {
                "width": 4000, "height": 3000, "is_screenshot": False,
                "face_count": 0, "ocr_text": "",
                "ext": ".jpg",
            },
            "/photos/family_portrait.jpg": {
                "width": 3000, "height": 4000, "is_screenshot": False,
                "face_count": 3, "ocr_text": "",
                "ext": ".jpg",
            },
            "/photos/screenshot_chat.png": {
                "width": 1179, "height": 2556, "is_screenshot": True,
                "face_count": 0, "ocr_text": "WhatsApp battery wifi",
                "ext": ".png",
            },
            "/photos/tiny_icon.png": {
                "width": 64, "height": 64, "is_screenshot": False,
                "face_count": 0, "ocr_text": "",
                "ext": ".png",
            },
        }

    def test_documents_include_ocr_evidence(self):
        """Documents with OCR text should be in candidates."""
        intent = self._make_intent()
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        assert "/photos/invoice.pdf" in cs.candidate_paths
        assert "/photos/receipt.png" in cs.candidate_paths

    def test_documents_exclude_screenshots(self):
        """Screenshots should never enter document candidates."""
        intent = self._make_intent()
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        assert "/photos/screenshot_chat.png" not in cs.candidate_paths

    def test_documents_exclude_faces(self):
        """Photos with faces should not enter document candidates."""
        intent = self._make_intent()
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        assert "/photos/family_portrait.jpg" not in cs.candidate_paths

    def test_documents_exclude_tiny(self):
        """Tiny images should not enter document candidates."""
        intent = self._make_intent()
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        assert "/photos/tiny_icon.png" not in cs.candidate_paths

    def test_documents_exclude_scenic_jpg_without_ocr(self):
        """Scenic JPGs without dense text layout must be excluded (Phase 9)."""
        intent = self._make_intent()
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        # Phase 9: /photos/beach_sunset.jpg has page-like ratio but no text layout.
        # It must be excluded from DocumentCandidateBuilder.
        assert "/photos/beach_sunset.jpg" not in cs.candidate_paths

    def test_evidence_payload_has_builder_field(self):
        """Evidence dict should identify which builder produced it."""
        intent = self._make_intent()
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        for path in cs.candidate_paths:
            ev = cs.evidence_by_path.get(path)
            assert ev is not None
            assert ev["builder"] == "document"

    def test_screenshot_preset_uses_screenshot_builder(self):
        """Screenshots preset should use the screenshot sub-builder."""
        intent = self._make_intent(preset_id="screenshots")
        meta = self._make_meta()
        cs = self.builder.build(intent, meta)
        assert "/photos/screenshot_chat.png" in cs.candidate_paths
        # Should not include non-screenshot documents
        assert "/photos/invoice.pdf" not in cs.candidate_paths

    def test_empty_project_returns_empty(self):
        """Empty project should return empty candidate set."""
        intent = self._make_intent()
        cs = self.builder.build(intent, {})
        assert cs.count == 0


# ══════════════════════════════════════════════════════════════════════
# PeopleCandidateBuilder Tests
# ══════════════════════════════════════════════════════════════════════

class TestPeopleCandidateBuilder:
    """Test face-first people candidate retrieval."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.candidate_builders.people_candidate_builder import (
            PeopleCandidateBuilder,
        )
        self.builder = PeopleCandidateBuilder(project_id=1)

    def _make_intent(self, **kwargs):
        from services.query_intent_planner import QueryIntent
        defaults = {"raw_query": "wedding", "preset_id": "wedding",
                     "family_hint": "people_event"}
        defaults.update(kwargs)
        return QueryIntent(**defaults)

    def _make_meta_with_faces(self):
        """Project where some photos have faces detected."""
        return {
            "/photos/wedding_group.jpg": {
                "face_count": 5, "is_screenshot": False, "width": 4000, "height": 3000,
            },
            "/photos/bride_portrait.jpg": {
                "face_count": 1, "is_screenshot": False, "width": 3000, "height": 4000,
            },
            "/photos/sunset.jpg": {
                "face_count": 0, "is_screenshot": False, "width": 4000, "height": 3000,
            },
            "/photos/landscape.jpg": {
                "face_count": 0, "is_screenshot": False, "width": 5000, "height": 3000,
            },
        }

    def _make_meta_no_faces(self):
        """Project with no face data (face detection not run)."""
        return {
            f"/photos/photo_{i}.jpg": {
                "face_count": 0, "is_screenshot": False, "width": 4000, "height": 3000,
            }
            for i in range(100)
        }

    def test_returns_face_photos_when_index_ready(self):
        """Should return photos with faces when face index is ready."""
        intent = self._make_intent()
        meta = self._make_meta_with_faces()
        cs = self.builder.build(intent, meta)
        # Should include photos with faces
        assert "/photos/wedding_group.jpg" in cs.candidate_paths
        assert "/photos/bride_portrait.jpg" in cs.candidate_paths
        # Should not include zero-face photos
        assert "/photos/sunset.jpg" not in cs.candidate_paths

    def test_not_ready_when_no_faces(self):
        """Should return not_ready when face index has no coverage."""
        intent = self._make_intent()
        meta = self._make_meta_no_faces()
        cs = self.builder.build(intent, meta)
        assert cs.ready_state == "not_ready"
        assert cs.count == 0

    def test_empty_project(self):
        """Should return empty for empty project."""
        intent = self._make_intent()
        cs = self.builder.build(intent, {})
        assert cs.count == 0

    def test_evidence_has_builder_field(self):
        """Evidence should identify the people builder."""
        intent = self._make_intent()
        meta = self._make_meta_with_faces()
        cs = self.builder.build(intent, meta)
        for path in cs.candidate_paths:
            ev = cs.evidence_by_path.get(path)
            assert ev is not None
            assert ev["builder"] == "people"


# ══════════════════════════════════════════════════════════════════════
# SearchConfidencePolicy Tests
# ══════════════════════════════════════════════════════════════════════

class TestSearchConfidencePolicy:
    """Test result trust evaluation."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.search_confidence_policy import SearchConfidencePolicy
        self.policy = SearchConfidencePolicy()

    def _make_intent(self, **kwargs):
        from services.query_intent_planner import QueryIntent
        return QueryIntent(**kwargs)

    def _make_candidate_set(self, **kwargs):
        from services.candidate_builders.base_candidate_builder import CandidateSet
        return CandidateSet(**kwargs)

    def _make_scored_result(self, path, score=0.5):
        from services.ranker import ScoredResult
        return ScoredResult(path=path, final_score=score)

    def test_not_ready_returns_not_ready(self):
        """Not-ready builder state should produce not_ready decision."""
        intent = self._make_intent(raw_query="wedding")
        cs = self._make_candidate_set(
            family="people_event",
            ready_state="not_ready",
            notes=["Face index not ready"],
        )
        decision = self.policy.evaluate(intent, cs, [], "people_event")
        assert not decision.show_results
        assert decision.confidence_label == "not_ready"

    def test_empty_results_return_empty(self):
        """Empty results should produce empty decision."""
        intent = self._make_intent(raw_query="documents")
        cs = self._make_candidate_set(family="type", ready_state="ready")
        decision = self.policy.evaluate(intent, cs, [], "type")
        assert decision.confidence_label == "empty"

    def test_type_high_evidence_is_high(self):
        """Type family with strong OCR evidence should be high confidence."""
        intent = self._make_intent(raw_query="documents")
        results = [self._make_scored_result(f"/doc_{i}.pdf") for i in range(10)]
        evidence = {
            f"/doc_{i}.pdf": {
                "builder": "document",
                "ocr_fts_hit": True,
                "ocr_lexicon_hit": True,
                "doc_extension": True,
                "structural_hit": True,
            }
            for i in range(10)
        }
        cs = self._make_candidate_set(
            family="type",
            candidate_paths=[r.path for r in results],
            evidence_by_path=evidence,
            ready_state="ready",
        )
        decision = self.policy.evaluate(intent, cs, results, "type")
        assert decision.confidence_label == "high"

    def test_people_high_faces_is_high(self):
        """People family with mostly face results should be high."""
        intent = self._make_intent(raw_query="wedding")
        results = [self._make_scored_result(f"/wedding_{i}.jpg") for i in range(10)]
        evidence = {
            f"/wedding_{i}.jpg": {
                "builder": "people",
                "face_count": 3,
                "is_face_presence": True,
            }
            for i in range(10)
        }
        cs = self._make_candidate_set(
            family="people_event",
            candidate_paths=[r.path for r in results],
            evidence_by_path=evidence,
            ready_state="ready",
        )
        decision = self.policy.evaluate(intent, cs, results, "people_event")
        assert decision.confidence_label == "high"

    def test_type_low_evidence_is_low(self):
        """Type family with no OCR/structural evidence should be low."""
        intent = self._make_intent(raw_query="documents")
        results = [self._make_scored_result(f"/photo_{i}.jpg") for i in range(10)]
        evidence = {
            f"/photo_{i}.jpg": {
                "builder": "document",
                "ocr_fts_hit": False,
                "ocr_lexicon_hit": False,
                "doc_extension": False,
                "structural_hit": False,
            }
            for i in range(10)
        }
        cs = self._make_candidate_set(
            family="type",
            candidate_paths=[r.path for r in results],
            evidence_by_path=evidence,
            ready_state="ready",
        )
        decision = self.policy.evaluate(intent, cs, results, "type")
        assert decision.confidence_label in ("low", "medium")

    def test_scenic_is_high_by_default(self):
        """Scenic family should default to high confidence."""
        intent = self._make_intent(raw_query="sunset")
        results = [self._make_scored_result(f"/sunset_{i}.jpg") for i in range(5)]
        cs = self._make_candidate_set(
            family="scenic",
            candidate_paths=[r.path for r in results],
            evidence_by_path={},
            ready_state="ready",
        )
        decision = self.policy.evaluate(intent, cs, results, "scenic")
        assert decision.confidence_label == "high"

    def test_utility_is_always_high(self):
        """Utility family should always be high confidence."""
        intent = self._make_intent(raw_query="favorites")
        results = [self._make_scored_result(f"/fav_{i}.jpg") for i in range(5)]
        cs = self._make_candidate_set(
            family="utility",
            candidate_paths=[r.path for r in results],
            evidence_by_path={},
            ready_state="ready",
        )
        decision = self.policy.evaluate(intent, cs, results, "utility")
        assert decision.confidence_label == "high"
