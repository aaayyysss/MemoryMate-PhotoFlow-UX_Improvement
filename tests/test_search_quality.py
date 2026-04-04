# tests/test_search_quality.py
# Tests for search quality improvements: gate engine, ranker families,
# structural/OCR scoring, and preset definitions.
#
# Run: python -m pytest tests/test_search_quality.py -v

import pytest
import sys
import os
from unittest import mock
from types import SimpleNamespace

# Bypass services/__init__.py (imports PySide6)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_pyside_mock = mock.MagicMock()
sys.modules.setdefault('PySide6', _pyside_mock)
sys.modules.setdefault('PySide6.QtCore', _pyside_mock)
sys.modules.setdefault('PySide6.QtWidgets', _pyside_mock)
sys.modules.setdefault('PySide6.QtGui', _pyside_mock)


# ══════════════════════════════════════════════════════════════════════
# GateEngine Tests
# ══════════════════════════════════════════════════════════════════════

class TestGateEngineDocuments:
    """Document gate must reject photo-like JPGs and accept real documents."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.gate_engine import GateEngine
        self.engine = GateEngine()

    def _make_plan(self, **kwargs):
        defaults = {
            "preset_id": "documents",
            "require_screenshot": False,
            "exclude_screenshots": True,
            "exclude_faces": True,
            "require_faces": False,
            "min_face_count": 0,
            "require_gps_gate": False,
            "min_edge_size": 700,
            "require_document_signal": True,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _make_scored(self, path, score=0.5):
        return SimpleNamespace(path=path, final_score=score, clip_score=0.3,
                               structural_score=0.0, ocr_score=0.0,
                               face_match_score=0.0, reasons=[])

    def test_jpg_no_ocr_page_like_rejected(self):
        """JPG with page-like aspect but no OCR must be rejected."""
        plan = self._make_plan()
        meta = {"/photos/scenic.jpg": {
            "ext": ".jpg", "width": 2100, "height": 2970,
            "ocr_text": "", "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/photos/scenic.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0
        assert "require_document_signal" in dropped

    def test_png_with_ocr_accepted(self):
        """PNG with sufficient OCR text must be accepted."""
        plan = self._make_plan()
        meta = {"/docs/doc1.png": {
            "ext": ".png", "width": 2100, "height": 2970,
            "ocr_text": "Invoice #12345 Total Amount Due: $500.00",
            "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/docs/doc1.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 1

    def test_pdf_no_ocr_but_page_like_accepted(self):
        """PDF with page-like geometry but no OCR should still pass."""
        plan = self._make_plan()
        meta = {"/docs/scan.pdf": {
            "ext": ".pdf", "width": 2100, "height": 2970,
            "ocr_text": "", "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/docs/scan.pdf")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 1

    def test_jpg_with_faces_rejected(self):
        """JPG portrait with face_count > 0 must be rejected."""
        plan = self._make_plan()
        meta = {"/photos/portrait.jpg": {
            "ext": ".jpg", "width": 2000, "height": 3000,
            "ocr_text": "Invoice text here for testing",
            "face_count": 2, "is_screenshot": False,
        }}
        scored = [self._make_scored("/photos/portrait.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0

    def test_screenshot_rejected_from_documents(self):
        """Screenshots must never pass document gate."""
        plan = self._make_plan()
        meta = {"/screen/capture.png": {
            "ext": ".png", "width": 1170, "height": 2532,
            "ocr_text": "Settings notification wifi battery",
            "face_count": 0, "is_screenshot": True,
        }}
        scored = [self._make_scored("/screen/capture.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0

    def test_small_image_rejected(self):
        """Images with min edge < 700 must be rejected."""
        plan = self._make_plan()
        meta = {"/icons/thumb.png": {
            "ext": ".png", "width": 500, "height": 500,
            "ocr_text": "Invoice text", "face_count": 0,
            "is_screenshot": False,
        }}
        scored = [self._make_scored("/icons/thumb.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0

    def test_jpg_with_strong_ocr_accepted(self):
        """JPG with strong OCR content (>= 15 chars) should pass."""
        plan = self._make_plan()
        meta = {"/scans/receipt.jpg": {
            "ext": ".jpg", "width": 2000, "height": 2800,
            "ocr_text": "Receipt from Store ABC - Total: $42.50 Date: 2024-01-15",
            "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/scans/receipt.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 1

    def test_png_page_like_no_ocr_rejected(self):
        """PNG with page-like geometry but no OCR/text-density must be rejected."""
        plan = self._make_plan()
        meta = {"/exports/graphic.png": {
            "ext": ".png", "width": 2100, "height": 2970,
            "ocr_text": "", "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/exports/graphic.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0
        assert "require_document_signal" in dropped

    def test_png_page_like_with_dense_text_accepted(self):
        """PNG scan/export with dense OCR text may pass as a document."""
        plan = self._make_plan()
        meta = {"/exports/form.png": {
            "ext": ".png", "width": 2100, "height": 2970,
            "ocr_text": "Application Form\nReference Number 12345\nCustomer Address\nSignature",
            "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/exports/form.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 1

    def test_jpg_page_like_sparse_text_rejected(self):
        """JPG with geometry and sparse OCR must still be rejected."""
        plan = self._make_plan()
        meta = {"/camera/pageish.jpg": {
            "ext": ".jpg", "width": 2100, "height": 2970,
            "ocr_text": "hello",
            "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/camera/pageish.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0


class TestGateEnginePets:
    """Pets gate must reject portraits and screenshots."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.gate_engine import GateEngine
        self.engine = GateEngine()

    def _make_plan(self, **kwargs):
        defaults = {
            "preset_id": "pets",
            "require_screenshot": False,
            "exclude_screenshots": True,
            "exclude_faces": True,
            "require_faces": False,
            "min_face_count": 0,
            "require_gps_gate": False,
            "min_edge_size": 0,
            "require_document_signal": False,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _make_scored(self, path, score=0.5):
        return SimpleNamespace(path=path, final_score=score, clip_score=0.3,
                               structural_score=0.0, ocr_score=0.0,
                               face_match_score=0.0, reasons=[])

    def test_portrait_with_faces_rejected(self):
        """Photo with face_count > 0 must be rejected from pets."""
        plan = self._make_plan()
        meta = {"/photos/person.jpg": {
            "face_count": 2, "is_screenshot": False,
        }}
        scored = [self._make_scored("/photos/person.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0

    def test_screenshot_rejected_from_pets(self):
        """Screenshots must be rejected from pets."""
        plan = self._make_plan()
        meta = {"/screen/cap.png": {
            "face_count": 0, "is_screenshot": True,
        }}
        scored = [self._make_scored("/screen/cap.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0

    def test_neutral_photo_accepted(self):
        """Non-face, non-screenshot photo should pass pets gate."""
        plan = self._make_plan()
        meta = {"/photos/garden.jpg": {
            "face_count": 0, "is_screenshot": False,
        }}
        scored = [self._make_scored("/photos/garden.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 1


class TestGateEngineScreenshots:
    """Screenshot gate must be strict."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.gate_engine import GateEngine
        self.engine = GateEngine()

    def _make_plan(self):
        return SimpleNamespace(
            preset_id="screenshots",
            require_screenshot=True,
            exclude_screenshots=False,
            exclude_faces=False,
            require_faces=False,
            min_face_count=0,
            require_gps_gate=False,
            min_edge_size=0,
            require_document_signal=False,
        )

    def _make_scored(self, path, score=0.5):
        return SimpleNamespace(path=path, final_score=score, clip_score=0.3,
                               structural_score=0.0, ocr_score=0.0,
                               face_match_score=0.0, reasons=[])

    def test_non_screenshot_rejected(self):
        plan = self._make_plan()
        meta = {"/photos/beach.jpg": {"is_screenshot": False}}
        scored = [self._make_scored("/photos/beach.jpg")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 0

    def test_true_screenshot_accepted(self):
        plan = self._make_plan()
        meta = {"/screen/cap.png": {"is_screenshot": True}}
        scored = [self._make_scored("/screen/cap.png")]
        kept, dropped = self.engine.apply(scored, plan, meta)
        assert len(kept) == 1


# ══════════════════════════════════════════════════════════════════════
# Ranker Tests
# ══════════════════════════════════════════════════════════════════════

class TestRankerFamilies:
    """Test family-specific scoring profiles."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.ranker import Ranker, FAMILY_WEIGHTS, ScoringWeights

        self.ranker = Ranker()
        self.FAMILY_WEIGHTS = FAMILY_WEIGHTS

    def test_all_families_sum_to_one(self):
        """All family weight profiles must sum to ~1.0."""
        for name, w in self.FAMILY_WEIGHTS.items():
            total = (w.w_clip + w.w_recency + w.w_favorite + w.w_location
                     + w.w_face_match + w.w_structural + w.w_ocr
                     + w.w_event + w.w_screenshot)
            assert abs(total - 1.0) < 0.02, (
                f"Family {name!r} weights sum to {total:.3f}, expected ~1.0"
            )

    def test_animal_object_family_exists(self):
        """animal_object family must exist in FAMILY_WEIGHTS."""
        assert "animal_object" in self.FAMILY_WEIGHTS

    def test_animal_object_clip_dominant(self):
        """animal_object family must be CLIP-dominant."""
        w = self.FAMILY_WEIGHTS["animal_object"]
        assert w.w_clip >= 0.80
        assert w.w_face_match == 0.0
        assert w.w_ocr == 0.0

    def test_type_family_structural_dominant(self):
        """Type family must have structural > clip."""
        w = self.FAMILY_WEIGHTS["type"]
        assert w.w_structural > w.w_clip

    def test_scenic_family_clip_dominant(self):
        """Scenic family must be CLIP-dominant."""
        w = self.FAMILY_WEIGHTS["scenic"]
        assert w.w_clip >= 0.80

    def test_document_png_scores_above_scenic_jpg_in_type(self):
        """Document PNG with OCR should score above scenic JPG under type family."""
        doc_meta = {"created_date": "2024-06-01", "flag": "none", "has_gps": False,
                    "face_count": 0, "rating": 0}
        jpg_meta = {"created_date": "2024-06-01", "flag": "none", "has_gps": False,
                    "face_count": 0, "rating": 0}

        doc_result = self.ranker.score(
            "/docs/invoice.png", 0.25, "scanned document", doc_meta,
            family="type", structural_score=0.77, ocr_score=0.85,
        )
        jpg_result = self.ranker.score(
            "/photos/scenic.jpg", 0.35, "scanned document", jpg_meta,
            family="type", structural_score=-0.25, ocr_score=0.0,
        )
        assert doc_result.final_score > jpg_result.final_score

    def test_pet_candidate_with_face_gets_penalty(self):
        """In animal_object family, face match should trigger negative adjustment."""
        meta = {"created_date": "2024-06-01", "flag": "none", "has_gps": False,
                "face_count": 3, "rating": 0}
        result = self.ranker.score(
            "/photos/person.jpg", 0.40, "dog", meta,
            family="animal_object", people_implied=True,
        )
        # The post-adjust should have fired
        assert any("family_adjust" in r for r in result.reasons)

    def test_family_post_adjust_type_no_ocr_negative_struct(self):
        """Type family with no OCR and negative structural gets penalty."""
        adj = self.ranker._family_post_adjust("type", 0.0, -0.3, 0.0)
        assert adj == -0.05

    def test_family_post_adjust_scenic_no_penalty(self):
        """Scenic family should get no post-adjustment."""
        adj = self.ranker._family_post_adjust("scenic", 0.0, 0.0, 0.0)
        assert adj == 0.0


# ══════════════════════════════════════════════════════════════════════
# Preset Family Mapping Tests
# ══════════════════════════════════════════════════════════════════════

class TestPresetFamilies:
    """Test preset -> family mapping."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.ranker import PRESET_FAMILIES, get_preset_family
        self.families = PRESET_FAMILIES
        self.get_family = get_preset_family

    def test_documents_maps_to_type(self):
        assert self.get_family("documents") == "type"

    def test_pets_maps_to_animal_object(self):
        assert self.get_family("pets") == "animal_object"

    def test_screenshots_maps_to_type(self):
        assert self.get_family("screenshots") == "type"

    def test_beach_maps_to_scenic(self):
        assert self.get_family("beach") == "scenic"

    def test_wedding_maps_to_people_event(self):
        assert self.get_family("wedding") == "people_event"


# ══════════════════════════════════════════════════════════════════════
# Smart Find Preset Tests
# ══════════════════════════════════════════════════════════════════════

class TestPresetDefinitions:
    """Test preset definitions for quality constraints."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.smart_find_service import BUILTIN_PRESETS
        self.presets = {p["id"]: p for p in BUILTIN_PRESETS}

    def test_documents_no_backoff(self):
        assert self.presets["documents"].get("allow_backoff") is False

    def test_pets_no_backoff(self):
        assert self.presets["pets"].get("allow_backoff") is False

    def test_documents_prompts_no_bare_paper_or_text(self):
        """Documents prompts should not contain overly broad terms alone."""
        prompts = self.presets["documents"]["prompts"]
        # "paper" alone or "text" alone are too broad
        assert "paper" not in prompts
        assert "text" not in prompts

    def test_pets_prompts_no_bare_animal(self):
        """Pets prompts should not contain the broad term 'animal'."""
        prompts = self.presets["pets"]["prompts"]
        assert "animal" not in prompts
        assert "a photo of an animal" not in prompts

    def test_pets_family_is_animal_object(self):
        assert self.presets["pets"]["family"] == "animal_object"

    def test_documents_family_is_type(self):
        assert self.presets["documents"]["family"] == "type"

    def test_pets_excludes_faces(self):
        gate = self.presets["pets"].get("gate_profile", {})
        assert gate.get("exclude_faces") is True

    def test_pets_excludes_screenshots(self):
        gate = self.presets["pets"].get("gate_profile", {})
        assert gate.get("exclude_screenshots") is True

    def test_documents_has_negative_prompts(self):
        negs = self.presets["documents"].get("negative_prompts", [])
        assert len(negs) > 0
        # Should include portrait/person rejections
        assert any("portrait" in n for n in negs)

    def test_screenshots_require_screenshot(self):
        gate = self.presets["screenshots"].get("gate_profile", {})
        assert gate.get("require_screenshot") is True


# ══════════════════════════════════════════════════════════════════════
# Document OCR Signal Helper Tests
# ══════════════════════════════════════════════════════════════════════

class TestDocumentOCRSignal:
    """Test the OCR signal helper used by gate and structural scorer."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.gate_engine import GateEngine
        self.has_signal = GateEngine.has_document_ocr_signal

    def test_empty_ocr_no_signal(self):
        assert self.has_signal({"ocr_text": ""}) is False

    def test_short_ocr_no_signal(self):
        assert self.has_signal({"ocr_text": "hello"}) is False

    def test_long_ocr_has_signal(self):
        assert self.has_signal({"ocr_text": "This is a long document text with enough characters"}) is True

    def test_invoice_term_has_signal(self):
        assert self.has_signal({"ocr_text": "invoice #123"}) is True

    def test_receipt_term_has_signal(self):
        assert self.has_signal({"ocr_text": "receipt total"}) is True


# ══════════════════════════════════════════════════════════════════════
# Dynamic Family Weight Config Tests
# ══════════════════════════════════════════════════════════════════════

class TestDynamicFamilyWeights:
    """All family weights must be readable/writable via RankingConfig."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from config.ranking_config import RankingConfig, FAMILY_DEFAULTS
        self.config = RankingConfig
        self.defaults = FAMILY_DEFAULTS

    def test_all_families_have_defaults(self):
        """Every known family must have a defaults entry."""
        expected = {"scenic", "type", "people_event", "utility", "animal_object"}
        assert expected == set(self.defaults.keys())

    def test_family_defaults_sum_to_one(self):
        """Each family default profile must sum to ~1.0."""
        for name, wd in self.defaults.items():
            total = sum(wd.values())
            assert abs(total - 1.0) < 0.02, (
                f"Family {name!r} defaults sum to {total:.3f}, expected ~1.0"
            )

    def test_get_family_weight_returns_default(self):
        """Reading a family weight with no user pref returns the default."""
        for family, wd in self.defaults.items():
            for key, expected in wd.items():
                actual = self.config.get_family_weight(family, key)
                assert actual == expected, (
                    f"{family}.{key}: expected {expected}, got {actual}"
                )

    def test_get_family_weights_dict_has_all_keys(self):
        """get_family_weights_dict must return all 7 weight keys."""
        for family in self.defaults:
            wd = self.config.get_family_weights_dict(family)
            assert set(wd.keys()) == set(self.config._WEIGHT_KEYS)

    def test_get_weights_for_family_uses_preferences(self):
        """get_weights_for_family should return a ScoringWeights built from config."""
        from services.ranker import get_weights_for_family
        for family in self.defaults:
            sw = get_weights_for_family(family)
            total = (sw.w_clip + sw.w_recency + sw.w_favorite + sw.w_location
                     + sw.w_face_match + sw.w_structural + sw.w_ocr
                     + sw.w_event + sw.w_screenshot)
            assert abs(total - 1.0) < 0.02, (
                f"Family {family!r} dynamic weights sum to {total:.3f}"
            )

    def test_unknown_family_falls_back_to_scenic(self):
        """Unknown family should resolve to scenic defaults."""
        from services.ranker import get_weights_for_family
        sw = get_weights_for_family("unknown_family_xyz")
        scenic_clip = self.defaults["scenic"]["w_clip"]
        assert sw.w_clip == scenic_clip

    def test_legacy_getters_match_scenic_defaults(self):
        """Legacy get_w_* methods should return scenic family values."""
        assert self.config.get_w_clip() == self.defaults["scenic"]["w_clip"]
        assert self.config.get_w_structural() == self.defaults["scenic"]["w_structural"]
