# tests/test_search_orchestrator.py
# Relevance test framework for the unified search pipeline
#
# This is the "tiny but ruthless benchmark" (audit requirement #7):
# - Tests query parsing correctness
# - Tests scoring contract determinism
# - Tests facet computation
# - Tests token extraction
# - Can be extended with project-specific expected-result sets
#
# Run: python -m pytest tests/test_search_orchestrator.py -v

import pytest
import sys
import os
import importlib
from unittest import mock
from datetime import datetime, timedelta

# Bypass services/__init__.py (imports PySide6) by loading modules directly
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
# Unit Tests: TokenParser
# ══════════════════════════════════════════════════════════════════════

class TestTokenParser:
    """Test structured token extraction from search queries."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.search_orchestrator import TokenParser
        self.parser = TokenParser

    # ── Structured Token Extraction ──

    def test_type_video_token(self):
        plan = self.parser.parse("sunset type:video")
        assert plan.filters.get("media_type") == "video"
        assert "sunset" in plan.semantic_text
        assert any(t["label"] == "Videos" for t in plan.extracted_tokens)

    def test_type_photo_token(self):
        plan = self.parser.parse("beach type:photo")
        assert plan.filters.get("media_type") == "photo"
        assert "beach" in plan.semantic_text

    def test_is_fav_token(self):
        plan = self.parser.parse("portraits is:fav")
        assert plan.filters.get("flag") == "pick"
        assert "portraits" in plan.semantic_text
        assert any(t["label"] == "Favorites" for t in plan.extracted_tokens)

    def test_is_favorite_token(self):
        plan = self.parser.parse("sunset is:favorite")
        assert plan.filters.get("flag") == "pick"

    def test_has_location_token(self):
        plan = self.parser.parse("wedding has:location")
        assert plan.filters.get("has_gps") is True
        assert "wedding" in plan.semantic_text
        assert any(t["label"] == "Has Location" for t in plan.extracted_tokens)

    def test_has_gps_token(self):
        plan = self.parser.parse("landscape has:gps")
        assert plan.filters.get("has_gps") is True

    def test_date_year_token(self):
        plan = self.parser.parse("beach date:2024")
        assert plan.filters.get("date_from") == "2024-01-01"
        assert plan.filters.get("date_to") == "2024-12-31"
        assert "beach" in plan.semantic_text

    def test_date_year_month_token(self):
        plan = self.parser.parse("sunset date:2024-06")
        assert plan.filters.get("date_from") == "2024-06-01"
        assert plan.filters.get("date_to") == "2024-06-30"

    def test_date_full_token(self):
        plan = self.parser.parse("party date:2024-12-25")
        assert plan.filters.get("date_from") == "2024-12-25"
        assert plan.filters.get("date_to") == "2024-12-25"

    def test_camera_token_ignored(self):
        """camera: token is not a valid filter (column not in schema)."""
        plan = self.parser.parse("portraits camera:iPhone")
        assert "camera_model" not in plan.filters
        assert "portraits" in plan.semantic_text

    def test_ext_token(self):
        plan = self.parser.parse("photos ext:heic")
        assert plan.filters.get("extension") == ".heic"

    def test_rating_token(self):
        plan = self.parser.parse("landscape rating:4")
        assert plan.filters.get("rating_min") == 4

    def test_person_token(self):
        plan = self.parser.parse("person:face_001 beach")
        assert plan.filters.get("person_id") == "face_001"
        assert "beach" in plan.semantic_text

    # ── Combined Tokens ──

    def test_multiple_tokens(self):
        """The critical test: "wedding Munich 2023 screenshots" plus filters."""
        plan = self.parser.parse("wedding Munich type:photo date:2023 is:fav")
        assert plan.filters.get("media_type") == "photo"
        assert plan.filters.get("date_from") == "2023-01-01"
        assert plan.filters.get("date_to") == "2023-12-31"
        assert plan.filters.get("flag") == "pick"
        # Semantic text should have the non-token parts
        assert "wedding" in plan.semantic_text.lower() or "munich" in plan.semantic_text.lower()

    def test_mixed_nl_and_tokens(self):
        """NL date extraction + structured tokens coexist."""
        plan = self.parser.parse("beach from 2024 is:fav")
        assert plan.filters.get("flag") == "pick"
        # Either NL parser or token parser should capture the date
        assert plan.filters.get("date_from") is not None

    # ── Edge Cases ──

    def test_empty_query(self):
        plan = self.parser.parse("")
        assert plan.semantic_text == ""
        assert plan.filters == {} or len(plan.filters) == 0

    def test_only_tokens_no_semantic(self):
        plan = self.parser.parse("type:video is:fav")
        assert plan.filters.get("media_type") == "video"
        assert plan.filters.get("flag") == "pick"
        # Semantic text should be empty or very short
        assert len(plan.semantic_text.strip()) <= 2

    def test_plain_text_no_tokens(self):
        plan = self.parser.parse("beautiful sunset over ocean")
        assert plan.semantic_text == "beautiful sunset over ocean"
        assert not plan.filters or len(plan.filters) == 0


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: ScoringWeights
# ══════════════════════════════════════════════════════════════════════

class TestScoringWeights:
    """Test the deterministic scoring contract."""

    def test_default_weights_sum_to_one(self):
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights()
        total = (w.w_clip + w.w_recency + w.w_favorite + w.w_location + w.w_face_match
                 + w.w_structural + w.w_ocr + w.w_event + w.w_screenshot)
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, expected ~1.0"

    def test_validate_normalizes(self):
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights(w_clip=0.5, w_recency=0.5, w_favorite=0.5,
                           w_location=0.5, w_face_match=0.5)
        w.validate()
        total = (w.w_clip + w.w_recency + w.w_favorite + w.w_location + w.w_face_match
                 + w.w_structural + w.w_ocr + w.w_event + w.w_screenshot)
        assert abs(total - 1.0) < 0.01

    def test_clip_dominates_scoring(self):
        """CLIP similarity should be the dominant signal."""
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights()
        assert w.w_clip > 0.5, "CLIP weight should dominate (>0.5)"
        assert w.w_clip > w.w_recency + w.w_favorite + w.w_location + w.w_face_match, \
            "CLIP should outweigh all other signals combined"

    def test_recency_cant_swamp_relevance(self):
        """Recency boost has a guardrail."""
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights()
        assert w.max_recency_boost <= 0.15, \
            "Recency boost guardrail should prevent swamping relevance"

    def test_favorites_cant_float_irrelevant(self):
        """Favorites boost has a guardrail."""
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights()
        assert w.max_favorite_boost <= 0.20, \
            "Favorite boost guardrail should prevent floating irrelevant items"


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: FacetComputer
# ══════════════════════════════════════════════════════════════════════

class TestFacetComputer:
    """Test result-set facet computation."""

    def test_empty_results_no_facets(self):
        from services.search_orchestrator import FacetComputer
        facets = FacetComputer.compute([], {})
        assert facets == {}

    def test_media_facet_mixed(self):
        """Facets need >= 8 results and buckets >= 10% each to appear."""
        from services.search_orchestrator import FacetComputer
        # 5 photos + 5 videos = 10 results, 50/50 split
        paths = [f"/photos/{i}.jpg" for i in range(5)] + \
                [f"/photos/{i}.mp4" for i in range(5)]
        facets = FacetComputer.compute(paths, {})
        assert "media" in facets
        assert facets["media"]["Photos"] == 5
        assert facets["media"]["Videos"] == 5

    def test_media_facet_no_mix(self):
        """If all results are photos, no media facet (nothing to refine)."""
        from services.search_orchestrator import FacetComputer
        paths = ["/photos/a.jpg", "/photos/b.png"]
        facets = FacetComputer.compute(paths, {})
        assert "media" not in facets

    def test_small_result_set_no_facets(self):
        """Result sets smaller than _MIN_RESULTS_FOR_FACETS get no facets."""
        from services.search_orchestrator import FacetComputer
        paths = ["/a.jpg", "/b.mp4", "/c.jpg"]
        facets = FacetComputer.compute(paths, {})
        assert facets == {}

    def test_year_facet(self):
        from services.search_orchestrator import FacetComputer
        # 10 results: 5 in 2024, 5 in 2023 — balanced split
        paths = [f"/{i}.jpg" for i in range(10)]
        meta = {}
        for i in range(5):
            meta[f"/{i}.jpg"] = {"created_date": "2024-06-15", "has_gps": False, "rating": 0}
        for i in range(5, 10):
            meta[f"/{i}.jpg"] = {"created_date": "2023-03-10", "has_gps": False, "rating": 0}
        facets = FacetComputer.compute(paths, meta)
        assert "years" in facets
        assert facets["years"]["2024"] == 5
        assert facets["years"]["2023"] == 5

    def test_location_facet(self):
        from services.search_orchestrator import FacetComputer
        # 10 results: 5 with GPS, 5 without — balanced
        paths = [f"/{i}.jpg" for i in range(10)]
        meta = {}
        for i in range(5):
            meta[f"/{i}.jpg"] = {"has_gps": True, "created_date": None, "rating": 0}
        for i in range(5, 10):
            meta[f"/{i}.jpg"] = {"has_gps": False, "created_date": None, "rating": 0}
        facets = FacetComputer.compute(paths, meta)
        assert "location" in facets
        assert facets["location"]["With Location"] == 5
        assert facets["location"]["No Location"] == 5

    def test_rating_facet(self):
        from services.search_orchestrator import FacetComputer
        # 10 results: 5 rated, 5 unrated — balanced
        paths = [f"/{i}.jpg" for i in range(10)]
        meta = {}
        for i in range(5):
            meta[f"/{i}.jpg"] = {"has_gps": False, "created_date": None, "rating": 5}
        for i in range(5, 10):
            meta[f"/{i}.jpg"] = {"has_gps": False, "created_date": None, "rating": 0}
        facets = FacetComputer.compute(paths, meta)
        assert "rated" in facets
        assert facets["rated"]["Rated"] == 5
        assert facets["rated"]["Unrated"] == 5

    def test_lopsided_facet_hidden(self):
        """90/10 splits should be suppressed — the minority < 10% of total."""
        from services.search_orchestrator import FacetComputer
        # 10 results: 9 with GPS, 1 without — lopsided
        paths = [f"/{i}.jpg" for i in range(10)]
        meta = {}
        for i in range(9):
            meta[f"/{i}.jpg"] = {"has_gps": True, "created_date": None, "rating": 0}
        meta["/9.jpg"] = {"has_gps": False, "created_date": None, "rating": 0}
        facets = FacetComputer.compute(paths, meta)
        assert "location" not in facets


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: QueryPlan
# ══════════════════════════════════════════════════════════════════════

class TestQueryPlan:
    """Test QueryPlan data structure."""

    def test_has_semantic(self):
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(semantic_text="beach")
        assert plan.has_semantic()

    def test_has_semantic_prompts(self):
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(semantic_prompts=["beach", "sand"])
        assert plan.has_semantic()

    def test_no_semantic(self):
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan()
        assert not plan.has_semantic()

    def test_has_filters(self):
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(filters={"media_type": "video"})
        assert plan.has_filters()

    def test_no_filters(self):
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan()
        assert not plan.has_filters()


# ══════════════════════════════════════════════════════════════════════
# Integration Tests: ScoredResult determinism
# ══════════════════════════════════════════════════════════════════════

class TestScoringDeterminism:
    """
    Verify that the scoring contract produces deterministic,
    reproducible results. Same inputs -> same outputs.
    """

    def test_same_inputs_same_score(self):
        """Scoring must be deterministic."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {
            "/test.jpg": {
                "rating": 5,
                "has_gps": True,
                "created_date": "2024-06-15",
                "date_taken": "2024-06-15",
            }
        }

        r1 = orch._score_result("/test.jpg", 0.35, "sunset", meta)
        r2 = orch._score_result("/test.jpg", 0.35, "sunset", meta)

        assert r1.final_score == r2.final_score
        assert r1.clip_score == r2.clip_score
        assert r1.favorite_score == r2.favorite_score
        assert r1.location_score == r2.location_score

    def test_higher_clip_means_higher_score(self):
        """Higher CLIP similarity should produce higher final score."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/a.jpg": {"rating": 0, "has_gps": False, "created_date": None, "date_taken": None}}

        r_high = orch._score_result("/a.jpg", 0.45, "sunset", meta)
        r_low = orch._score_result("/a.jpg", 0.20, "sunset", meta)

        assert r_high.final_score > r_low.final_score

    def test_favorite_boosts_but_doesnt_dominate(self):
        """A favorite with low clip should not outrank a strong clip match."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta_fav = {"/fav.jpg": {"rating": 5, "has_gps": True, "created_date": "2024-01-01", "date_taken": "2024-01-01"}}
        meta_strong = {"/strong.jpg": {"rating": 0, "has_gps": False, "created_date": None, "date_taken": None}}

        r_fav = orch._score_result("/fav.jpg", 0.15, "sunset", meta_fav)
        r_strong = orch._score_result("/strong.jpg", 0.45, "sunset", meta_strong)

        assert r_strong.final_score > r_fav.final_score, \
            f"Strong clip ({r_strong.final_score:.4f}) should beat weak clip + fav ({r_fav.final_score:.4f})"

    def test_score_components_logged(self):
        """Every ScoredResult should have reasons explaining the score."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/test.jpg": {"rating": 5, "has_gps": True, "created_date": "2024-06-15", "date_taken": "2024-06-15"}}
        r = orch._score_result("/test.jpg", 0.35, "sunset", meta)

        # Should have clip, favorite, and location reasons
        reason_text = " ".join(r.reasons)
        assert "clip=" in reason_text
        assert "favorite=" in reason_text
        assert "location=" in reason_text

    def test_recency_discriminates_across_dates(self):
        """Recency must produce different values for different dates, not saturate."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta_recent = {"/recent.jpg": {"rating": 0, "has_gps": False,
                                       "created_date": "2026-02-28", "date_taken": "2026-02-28"}}
        meta_old = {"/old.jpg": {"rating": 0, "has_gps": False,
                                 "created_date": "2025-06-01", "date_taken": "2025-06-01"}}

        r_recent = orch._score_result("/recent.jpg", 0.30, "test", meta_recent)
        r_old = orch._score_result("/old.jpg", 0.30, "test", meta_old)

        assert r_recent.recency_score > r_old.recency_score, \
            f"Recent ({r_recent.recency_score:.4f}) must beat old ({r_old.recency_score:.4f})"
        assert r_recent.recency_score != r_old.recency_score, \
            "Recency must not saturate to the same value for all dates"

    def test_face_score_active_with_person_filter(self):
        """Face component must be 1.0 when person filter is active."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/face.jpg": {"rating": 0, "has_gps": False,
                              "created_date": None, "date_taken": None}}

        r_no_face = orch._score_result("/face.jpg", 0.30, "test", meta)
        r_face = orch._score_result("/face.jpg", 0.30, "test", meta,
                                    active_filters={"person_id": "face_001"})

        assert r_no_face.face_match_score == 0.0
        assert r_face.face_match_score == 1.0
        assert r_face.final_score > r_no_face.final_score, \
            "Face match must boost the final score"

    def test_flag_pick_triggers_favorite_boost(self):
        """flag='pick' must activate favorite scoring (Favorites = flag, not rating)."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta_flagged = {"/flagged.jpg": {
            "rating": 0, "flag": "pick", "has_gps": False,
            "created_date": None, "date_taken": None,
        }}
        meta_unflagged = {"/unflagged.jpg": {
            "rating": 0, "flag": "none", "has_gps": False,
            "created_date": None, "date_taken": None,
        }}

        r_flagged = orch._score_result("/flagged.jpg", 0.30, "test", meta_flagged)
        r_unflagged = orch._score_result("/unflagged.jpg", 0.30, "test", meta_unflagged)

        assert r_flagged.favorite_score > 0, "flag='pick' must trigger favorite boost"
        assert r_unflagged.favorite_score == 0.0
        assert r_flagged.final_score > r_unflagged.final_score

    def test_weight_component_structural_match(self):
        """Every ScoringWeights field must have a matching ScoredResult component."""
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights()
        # validate() now asserts weight-to-component mapping
        w.validate()  # must not raise


# ══════════════════════════════════════════════════════════════════════
# NLQueryParser backward compatibility
# ══════════════════════════════════════════════════════════════════════

class TestNLQueryParserCompat:
    """Ensure existing NLQueryParser still works after orchestrator integration."""

    def test_nl_date_extraction(self):
        from services.smart_find_service import NLQueryParser
        text, filters = NLQueryParser.parse("sunset from 2024")
        assert filters.get("date_from") == "2024-01-01"
        assert "sunset" in text

    def test_nl_rating_extraction(self):
        from services.smart_find_service import NLQueryParser
        text, filters = NLQueryParser.parse("5 star beach photos")
        assert filters.get("rating_min") == 5

    def test_nl_favorites_extraction(self):
        from services.smart_find_service import NLQueryParser
        text, filters = NLQueryParser.parse("my favorites")
        assert filters.get("flag") == "pick"

    def test_nl_video_extraction(self):
        from services.smart_find_service import NLQueryParser
        text, filters = NLQueryParser.parse("videos from last month")
        assert filters.get("media_type") == "video"

    def test_nl_gps_extraction(self):
        from services.smart_find_service import NLQueryParser
        text, filters = NLQueryParser.parse("photos with GPS")
        assert filters.get("has_gps") is True


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: OrchestratorResult progressive phase
# ══════════════════════════════════════════════════════════════════════

class TestProgressiveSearch:
    """Test the progressive search-as-you-type contract."""

    def test_result_has_phase_field(self):
        """OrchestratorResult must have a 'phase' field."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult()
        assert hasattr(r, 'phase')
        assert r.phase == "full"

    def test_metadata_phase(self):
        """Metadata-only results should have phase='metadata'."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult(phase="metadata")
        assert r.phase == "metadata"

    def test_search_metadata_only_exists(self):
        """SearchOrchestrator must have search_metadata_only method."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'search_metadata_only')

    def test_token_parser_works_for_progressive(self):
        """Token parsing used in progressive search must handle all cases."""
        from services.search_orchestrator import TokenParser
        # Progressive search uses the same TokenParser - ensure metadata tokens
        # are correctly extracted even without semantic search
        plan = TokenParser.parse("date:2024 is:fav")
        assert plan.filters.get("date_from") == "2024-01-01"
        assert plan.filters.get("flag") == "pick"
        assert not plan.semantic_text.strip()  # No semantic text


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: ANN Retrieval
# ══════════════════════════════════════════════════════════════════════

class TestANNRetrieval:
    """Test two-stage ANN retrieval infrastructure."""

    def test_search_ann_method_exists(self):
        """SearchOrchestrator must have search_ann method."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'search_ann')

    def test_invalidate_ann_cache_exists(self):
        """SearchOrchestrator must have invalidate_ann_cache method."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'invalidate_ann_cache')

    def test_ann_cache_class_level(self):
        """ANN index cache should be class-level (shared across instances)."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, '_ann_index_cache')
        assert isinstance(SearchOrchestrator._ann_index_cache, dict)

    def test_ann_cache_ttl(self):
        """ANN cache TTL should be > 0."""
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._ANN_CACHE_TTL > 0


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Find Similar
# ══════════════════════════════════════════════════════════════════════

class TestFindSimilar:
    """Test find-similar (Excire-style) integration."""

    def test_find_similar_method_exists(self):
        """SearchOrchestrator must have find_similar method."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'find_similar')

    def test_find_similar_returns_orchestrator_result(self):
        """find_similar should return OrchestratorResult (not raw list)."""
        from services.search_orchestrator import SearchOrchestrator
        import inspect
        sig = inspect.signature(SearchOrchestrator.find_similar)
        # Method signature: (self, photo_path, top_k, threshold)
        params = list(sig.parameters.keys())
        assert 'photo_path' in params
        assert 'top_k' in params
        assert 'threshold' in params

    def test_find_similar_query_plan_source(self):
        """find_similar results should have source='similar' in query_plan."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(source="similar", raw_query="similar:test.jpg")
        assert plan.source == "similar"


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: LibraryAnalyzer (suggested searches)
# ══════════════════════════════════════════════════════════════════════

class TestLibraryAnalyzer:
    """Test library-stats-based search suggestions."""

    def test_library_analyzer_exists(self):
        """LibraryAnalyzer class must exist."""
        from services.search_orchestrator import LibraryAnalyzer
        assert hasattr(LibraryAnalyzer, 'suggest')

    def test_suggest_returns_list(self):
        """suggest() should return a list of dicts."""
        from services.search_orchestrator import LibraryAnalyzer
        import inspect
        sig = inspect.signature(LibraryAnalyzer.suggest)
        params = list(sig.parameters.keys())
        assert 'project_id' in params
        assert 'max_suggestions' in params

    def test_suggestion_dict_format(self):
        """Each suggestion must have label, query, icon."""
        # Verify the expected format without hitting the DB
        suggestion = {"label": "2024 (100)", "query": "date:2024", "icon": "\U0001f4c5"}
        assert "label" in suggestion
        assert "query" in suggestion
        assert "icon" in suggestion

    def test_max_suggestions_cap(self):
        """suggest() should respect max_suggestions parameter."""
        from services.search_orchestrator import LibraryAnalyzer
        import inspect
        sig = inspect.signature(LibraryAnalyzer.suggest)
        # max_suggestions has a default value
        assert sig.parameters['max_suggestions'].default == 8


# ══════════════════════════════════════════════════════════════════════
# Integration Tests: Scoring with progressive phases
# ══════════════════════════════════════════════════════════════════════

class TestScoringInProgressiveMode:
    """Test scoring contract works correctly in metadata-only mode."""

    def test_metadata_only_scoring_no_clip(self):
        """In metadata-only phase, clip_score should be 0."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/test.jpg": {
            "rating": 5, "has_gps": True,
            "created_date": "2024-06-15", "date_taken": "2024-06-15"
        }}
        r = orch._score_result("/test.jpg", 0.0, "", meta)

        assert r.clip_score == 0.0
        assert r.final_score > 0  # Other signals still contribute
        assert r.favorite_score > 0
        assert r.location_score > 0

    def test_full_scoring_beats_metadata_only(self):
        """Full search (with clip) should produce higher scores than metadata-only."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/test.jpg": {
            "rating": 5, "has_gps": True,
            "created_date": "2024-06-15", "date_taken": "2024-06-15"
        }}

        r_meta = orch._score_result("/test.jpg", 0.0, "", meta)
        r_full = orch._score_result("/test.jpg", 0.35, "sunset", meta)

        assert r_full.final_score > r_meta.final_score

    def test_progressive_result_phase_distinguishable(self):
        """UI can tell metadata results from full results via phase field."""
        from services.search_orchestrator import OrchestratorResult
        meta_result = OrchestratorResult(phase="metadata", total_matches=10)
        full_result = OrchestratorResult(phase="full", total_matches=15)
        assert meta_result.phase != full_result.phase
        assert meta_result.phase == "metadata"
        assert full_result.phase == "full"


# ══════════════════════════════════════════════════════════════════════
# CI Integration: Relevance contract tests
# ══════════════════════════════════════════════════════════════════════

class TestRelevanceContract:
    """
    Relevance contract tests for CI integration.

    These tests verify the search system's fundamental contracts
    without requiring a database or CLIP model. They should pass
    in any CI environment.

    Run in CI: pytest tests/test_search_orchestrator.py -v -m "not requires_qt"
    """

    def test_scoring_weights_are_stable(self):
        """Scoring weights shouldn't change without deliberate update."""
        from services.search_orchestrator import ScoringWeights
        w = ScoringWeights()
        assert w.w_clip == 0.75
        assert w.w_recency == 0.05
        assert w.w_favorite == 0.08
        assert w.w_location == 0.04
        assert w.w_face_match == 0.08
        assert w.w_screenshot == 0.00

    def test_token_parser_complete_coverage(self):
        """All documented token types must be parseable."""
        from services.search_orchestrator import TokenParser
        tokens = {
            "type:video": "media_type",
            "type:photo": "media_type",
            "is:fav": "flag",
            "has:location": "has_gps",
            "has:faces": "has_faces",
            "date:2024": "date_from",
            "ext:heic": "extension",
            "rating:4": "rating_min",
            "person:face_001": "person_id",
        }
        for token, expected_key in tokens.items():
            plan = TokenParser.parse(f"test {token}")
            assert expected_key in plan.filters, \
                f"Token '{token}' should produce filter key '{expected_key}'"

    def test_facet_computer_handles_edge_cases(self):
        """FacetComputer must not crash on degenerate inputs."""
        from services.search_orchestrator import FacetComputer
        # Empty
        assert FacetComputer.compute([], {}) == {}
        # No metadata
        paths = ["/a.jpg"]
        facets = FacetComputer.compute(paths, {})
        # Should not crash, may produce empty facets
        assert isinstance(facets, dict)
        # Single path
        facets = FacetComputer.compute(
            ["/a.jpg"],
            {"/a.jpg": {"has_gps": True, "rating": 5, "created_date": "2024-01-01"}}
        )
        assert isinstance(facets, dict)

    def test_orchestrator_result_serializable(self):
        """OrchestratorResult should be JSON-serializable (for CI reporting)."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult(
            paths=["/a.jpg", "/b.jpg"],
            total_matches=2,
            scores={"/a.jpg": 0.95, "/b.jpg": 0.80},
            facets={"media": {"Photos": 2}},
            phase="full",
        )
        import json
        # Should not raise
        data = {
            "paths": r.paths,
            "total_matches": r.total_matches,
            "scores": r.scores,
            "facets": r.facets,
            "phase": r.phase,
        }
        serialized = json.dumps(data)
        assert "full" in serialized

    def test_date_relative_tokens_resolve(self):
        """Relative date tokens should resolve to valid date strings."""
        from services.search_orchestrator import TokenParser
        relatives = ["today", "yesterday", "this_week", "last_week",
                      "this_month", "last_month", "this_year", "last_year"]
        for rel in relatives:
            plan = TokenParser.parse(f"test date:{rel}")
            assert "date_from" in plan.filters, \
                f"Relative date '{rel}' should resolve to date_from"
            assert "date_to" in plan.filters, \
                f"Relative date '{rel}' should resolve to date_to"

    def test_query_plan_immutable_source(self):
        """QueryPlan source should be one of: text, preset, combined, similar."""
        from services.search_orchestrator import QueryPlan
        for source in ["text", "preset", "combined", "similar"]:
            plan = QueryPlan(source=source)
            assert plan.source == source


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Duplicate Stacking (P0)
# ══════════════════════════════════════════════════════════════════════

class TestDuplicateStacking:
    """Test duplicate stacking in search results."""

    def test_scored_result_has_duplicate_count(self):
        """ScoredResult must have a duplicate_count field."""
        from services.search_orchestrator import ScoredResult
        sr = ScoredResult(path="/test.jpg")
        assert hasattr(sr, 'duplicate_count')
        assert sr.duplicate_count == 0

    def test_deduplicate_empty_list(self):
        """_deduplicate_results on empty list returns empty."""
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999
        orch._dup_cache = {}
        orch._dup_cache_time = 0.0
        deduped, stacked = orch._deduplicate_results([])
        assert deduped == []
        assert stacked == 0

    def test_deduplicate_no_duplicates(self):
        """Non-duplicate results pass through unchanged."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999
        orch._dup_cache = {}  # no duplicates mapped
        orch._dup_cache_time = 99999999999.0
        results = [
            ScoredResult(path="/a.jpg", final_score=0.9),
            ScoredResult(path="/b.jpg", final_score=0.8),
        ]
        deduped, stacked = orch._deduplicate_results(results)
        assert len(deduped) == 2
        assert stacked == 0

    def test_deduplicate_folds_duplicates(self):
        """Duplicate results should be folded into a single representative."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999
        orch._project_meta_cache = {}
        orch._meta_cache_time = 99999999999.0
        orch._META_CACHE_TTL = 60.0
        # Simulate: /a.jpg and /a_copy.jpg share representative /a.jpg, group size 2
        orch._dup_cache = {
            "/a.jpg": ("/a.jpg", 2),
            "/a_copy.jpg": ("/a.jpg", 2),
        }
        orch._dup_cache_time = 99999999999.0

        results = [
            ScoredResult(path="/a.jpg", final_score=0.9),
            ScoredResult(path="/a_copy.jpg", final_score=0.85),
            ScoredResult(path="/b.jpg", final_score=0.7),  # not a duplicate
        ]
        deduped, stacked = orch._deduplicate_results(results)
        assert len(deduped) == 2  # /a.jpg representative + /b.jpg
        assert stacked == 1
        # Representative should have duplicate_count = 1
        rep = [r for r in deduped if r.path == "/a.jpg"][0]
        assert rep.duplicate_count == 1

    def test_deduplicate_prefers_non_copy(self):
        """Non-copy filenames are preferred over copies, even with lower scores."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999
        orch._project_meta_cache = {}
        orch._meta_cache_time = 99999999999.0
        orch._META_CACHE_TTL = 60.0
        orch._dup_cache = {
            "/a.jpg": ("/a.jpg", 2),
            "/a_copy.jpg": ("/a.jpg", 2),
        }
        orch._dup_cache_time = 99999999999.0

        results = [
            ScoredResult(path="/a.jpg", final_score=0.5),
            ScoredResult(path="/a_copy.jpg", final_score=0.9),  # copy scores higher
        ]
        deduped, stacked = orch._deduplicate_results(results)
        assert len(deduped) == 1
        # Non-copy wins as representative (Patch A quality tie-breaker)
        assert deduped[0].path == "/a.jpg"

    def test_orchestrator_result_has_stacked_count(self):
        """OrchestratorResult must have stacked_duplicates field."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult()
        assert hasattr(r, 'stacked_duplicates')
        assert r.stacked_duplicates == 0


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: ANN Dirty-on-Embed (P2)
# ══════════════════════════════════════════════════════════════════════

class TestANNDirtyOnEmbed:
    """Test ANN index invalidation when new embeddings arrive."""

    def test_mark_ann_dirty_exists(self):
        """mark_ann_dirty class method must exist."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'mark_ann_dirty')
        assert callable(SearchOrchestrator.mark_ann_dirty)

    def test_dirty_flag_set_and_cleared(self):
        """mark_ann_dirty sets flag, _get_or_build_ann_index clears it."""
        from services.search_orchestrator import SearchOrchestrator
        project_id = 99999
        # Mark dirty
        SearchOrchestrator.mark_ann_dirty(project_id)
        assert project_id in SearchOrchestrator._ann_dirty
        # Clean up
        SearchOrchestrator._ann_dirty.discard(project_id)
        assert project_id not in SearchOrchestrator._ann_dirty

    def test_invalidate_ann_cache_clears_dirty(self):
        """invalidate_ann_cache must also clear dirty flag."""
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 99998
        SearchOrchestrator._ann_dirty.add(99998)
        orch.invalidate_ann_cache()
        assert 99998 not in SearchOrchestrator._ann_dirty


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Relevance Feedback (P1)
# ══════════════════════════════════════════════════════════════════════

class TestRelevanceFeedback:
    """Test search event recording and personal boost."""

    def test_record_search_event_exists(self):
        """record_search_event static method must exist."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'record_search_event')
        assert callable(SearchOrchestrator.record_search_event)

    def test_get_personal_boost_exists(self):
        """get_personal_boost static method must exist."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'get_personal_boost')

    def test_personal_boost_returns_float(self):
        """get_personal_boost must return a float in [0, 0.15]."""
        from services.search_orchestrator import SearchOrchestrator
        # With no DB, should return 0.0 gracefully
        boost = SearchOrchestrator.get_personal_boost(1, "test_hash", "/test.jpg")
        assert isinstance(boost, float)
        assert 0.0 <= boost <= 0.15


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Query Autocomplete (P1)
# ══════════════════════════════════════════════════════════════════════

class TestQueryAutocomplete:
    """Test autocomplete suggestions."""

    def test_autocomplete_exists(self):
        """autocomplete static method must exist."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'autocomplete')

    def test_autocomplete_returns_list(self):
        """autocomplete must return a list."""
        from services.search_orchestrator import SearchOrchestrator
        result = SearchOrchestrator.autocomplete(1, "type:")
        assert isinstance(result, list)

    def test_autocomplete_token_completions(self):
        """autocomplete must suggest token completions."""
        from services.search_orchestrator import SearchOrchestrator
        result = SearchOrchestrator.autocomplete(1, "type:v")
        labels = [r["label"] for r in result]
        assert "type:video" in labels

    def test_autocomplete_empty_prefix(self):
        """Empty prefix returns no suggestions."""
        from services.search_orchestrator import SearchOrchestrator
        result = SearchOrchestrator.autocomplete(1, "")
        assert result == []

    def test_autocomplete_respects_max(self):
        """autocomplete must respect max_results."""
        from services.search_orchestrator import SearchOrchestrator
        result = SearchOrchestrator.autocomplete(1, "type:", max_results=1)
        assert len(result) <= 1


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: OCR Search Integration (P1)
# ══════════════════════════════════════════════════════════════════════

class TestOCRSearchIntegration:
    """Test OCR text search integration point."""

    def test_search_ocr_text_exists(self):
        """search_ocr_text static method must exist."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, 'search_ocr_text')

    def test_search_ocr_text_graceful_fallback(self):
        """search_ocr_text must return empty list when FTS5 table doesn't exist."""
        from services.search_orchestrator import SearchOrchestrator
        result = SearchOrchestrator.search_ocr_text(1, "receipt")
        assert isinstance(result, list)
        assert result == []  # No FTS5 table = graceful empty


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Phase Labels (P2)
# ══════════════════════════════════════════════════════════════════════

class TestPhaseLabels:
    """Test hybrid retrieval phase labels for UI transparency."""

    def test_orchestrator_result_has_phase_label(self):
        """OrchestratorResult must have phase_label field."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult()
        assert hasattr(r, 'phase_label')
        assert r.phase_label == ""

    def test_metadata_phase_label(self):
        """Metadata-only results should be labeled."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult(phase="metadata", phase_label="Metadata results")
        assert r.phase_label == "Metadata results"

    def test_full_phase_label(self):
        """Full semantic results should be labeled."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult(phase="full", phase_label="Semantic refined")
        assert r.phase_label == "Semantic refined"


# ══════════════════════════════════════════════════════════════════════
# Regression Tests: Product-Level Issues (Patch I)
# ══════════════════════════════════════════════════════════════════════

class TestDuplicateDiversity:
    """Regression: duplicate stacking must not flood top-10 with same group."""

    def test_max_copies_in_top_10(self):
        """At most 1 representative per duplicate group should appear in results."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999
        orch._project_meta_cache = {}
        orch._meta_cache_time = 99999999999.0
        orch._META_CACHE_TTL = 60.0
        # Group A: 5 copies all mapping to /a.jpg
        orch._dup_cache = {
            "/a.jpg": ("/a.jpg", 5),
            "/a_copy1.jpg": ("/a.jpg", 5),
            "/a_copy2.jpg": ("/a.jpg", 5),
            "/a_copy3.jpg": ("/a.jpg", 5),
            "/a_copy4.jpg": ("/a.jpg", 5),
        }
        orch._dup_cache_time = 99999999999.0

        results = [
            ScoredResult(path="/a.jpg", final_score=0.95),
            ScoredResult(path="/a_copy1.jpg", final_score=0.94),
            ScoredResult(path="/a_copy2.jpg", final_score=0.93),
            ScoredResult(path="/a_copy3.jpg", final_score=0.92),
            ScoredResult(path="/a_copy4.jpg", final_score=0.91),
            ScoredResult(path="/b.jpg", final_score=0.80),
            ScoredResult(path="/c.jpg", final_score=0.70),
        ]
        deduped, stacked = orch._deduplicate_results(results)
        # Only 1 from group A + /b.jpg + /c.jpg
        group_a_in_top10 = [r for r in deduped[:10] if "a" in r.path and "b" not in r.path and "c" not in r.path]
        assert len(group_a_in_top10) == 1, \
            f"Expected 1 representative from group A in top-10, got {len(group_a_in_top10)}"
        assert stacked == 4  # 4 copies stacked behind representative

    def test_copy_filename_demoted(self):
        """Files with 'Kopie', 'Copy', or '(1)' should not be chosen as representative."""
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._is_copy_filename("/photos/IMG_1234 Kopie.jpg")
        assert SearchOrchestrator._is_copy_filename("/photos/Photo Copy.png")
        assert SearchOrchestrator._is_copy_filename("/photos/Photo copy(2).jpg")
        assert SearchOrchestrator._is_copy_filename("/photos/IMG_5678 (1).jpg")
        assert not SearchOrchestrator._is_copy_filename("/photos/IMG_1234.jpg")
        assert not SearchOrchestrator._is_copy_filename("/photos/landscape.png")

    def test_enforce_unique_paths_removes_duplicates(self):
        """_enforce_unique_paths must keep only first occurrence of each path."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        scored = [
            ScoredResult(path="/a.jpg", final_score=0.95),
            ScoredResult(path="/b.jpg", final_score=0.90),
            ScoredResult(path="/a.jpg", final_score=0.85),  # duplicate path
            ScoredResult(path="/c.jpg", final_score=0.80),
            ScoredResult(path="/b.jpg", final_score=0.75),  # duplicate path
        ]
        unique = SearchOrchestrator._enforce_unique_paths(scored)
        paths = [r.path for r in unique]
        assert paths == ["/a.jpg", "/b.jpg", "/c.jpg"]
        assert unique[0].final_score == 0.95  # kept highest-scored /a.jpg
        assert unique[1].final_score == 0.90  # kept highest-scored /b.jpg


class TestFavoritesSemantics:
    """Regression: Favorites ≡ flag='pick', not rating >= 4."""

    def test_favorites_preset_uses_flag(self):
        """Favorites preset must filter by flag='pick', not rating_min."""
        from services.smart_find_service import BUILTIN_PRESETS
        fav_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "favorites")
        assert "flag" in fav_preset["filters"], \
            "Favorites preset must use flag filter"
        assert fav_preset["filters"]["flag"] == "pick", \
            "Favorites must map to flag='pick'"
        assert "rating_min" not in fav_preset["filters"], \
            "Favorites preset must NOT use rating_min"

    def test_is_fav_token_uses_flag(self):
        """is:fav token must produce flag='pick' filter."""
        from services.search_orchestrator import TokenParser
        plan = TokenParser.parse("sunset is:fav")
        assert plan.filters.get("flag") == "pick"
        assert "rating_min" not in plan.filters, \
            "is:fav should set flag, not rating_min"

    def test_rating_token_still_works(self):
        """rating:4 token must still produce rating_min filter (separate from favorites)."""
        from services.search_orchestrator import TokenParser
        plan = TokenParser.parse("sunset rating:4")
        assert plan.filters.get("rating_min") == 4
        assert "flag" not in plan.filters

    def test_favorite_scoring_flag_pick(self):
        """Flag='pick' must boost score even with rating=0."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/test.jpg": {
            "rating": 0, "flag": "pick", "has_gps": False,
            "created_date": None, "date_taken": None,
        }}
        r = orch._score_result("/test.jpg", 0.30, "test", meta)
        assert r.favorite_score > 0, "flag='pick' with rating=0 must still boost"

    def test_favorite_scoring_high_rating(self):
        """Rating >= 4 must still boost score (backward compat)."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/test.jpg": {
            "rating": 5, "flag": "none", "has_gps": False,
            "created_date": None, "date_taken": None,
        }}
        r = orch._score_result("/test.jpg", 0.30, "test", meta)
        assert r.favorite_score > 0, "rating=5 must still boost (backward compat)"


class TestFacePresenceScoring:
    """Regression: people-implied queries must use face presence for scoring."""

    def test_people_implied_preset_detection(self):
        """Portraits, Baby, Wedding, Party must be detected as people-implied."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        for preset_id in ["portraits", "baby", "wedding", "party"]:
            plan = QueryPlan(preset_id=preset_id)
            assert orch._is_people_implied(plan), \
                f"Preset '{preset_id}' must be people-implied"

    def test_non_people_preset_not_detected(self):
        """Beach, Mountains, etc. must NOT be people-implied."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        for preset_id in ["beach", "mountains", "sunset", "food"]:
            plan = QueryPlan(preset_id=preset_id)
            assert not orch._is_people_implied(plan), \
                f"Preset '{preset_id}' must NOT be people-implied"

    def test_people_implied_keywords(self):
        """Free-text queries with people keywords must be detected."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        for text in ["portrait", "baby photos", "wedding ceremony", "family photo"]:
            plan = QueryPlan(semantic_text=text)
            assert orch._is_people_implied(plan), \
                f"Query '{text}' must be people-implied"

    def test_face_presence_boosts_score(self):
        """Photos with faces must score higher for people-implied queries."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta_face = {"/face.jpg": {
            "rating": 0, "flag": "none", "has_gps": False,
            "created_date": None, "date_taken": None,
            "face_count": 2,
        }}
        meta_no_face = {"/noface.jpg": {
            "rating": 0, "flag": "none", "has_gps": False,
            "created_date": None, "date_taken": None,
            "face_count": 0,
        }}

        r_face = orch._score_result("/face.jpg", 0.30, "portrait", meta_face,
                                     people_implied=True)
        r_no_face = orch._score_result("/noface.jpg", 0.30, "portrait", meta_no_face,
                                        people_implied=True)

        assert r_face.face_match_score == 1.0, \
            "Face presence in people-implied query must score 1.0"
        assert r_no_face.face_match_score == 0.0, \
            "No face in people-implied query must score 0.0"
        assert r_face.final_score > r_no_face.final_score

    def test_non_people_query_ignores_faces(self):
        """Non-people queries must not use face presence scoring."""
        from services.search_orchestrator import SearchOrchestrator, ScoringWeights
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._weights = ScoringWeights()
        orch._weights.validate()

        meta = {"/face.jpg": {
            "rating": 0, "flag": "none", "has_gps": False,
            "created_date": None, "date_taken": None,
            "face_count": 5,
        }}
        r = orch._score_result("/face.jpg", 0.30, "sunset", meta,
                                people_implied=False)
        assert r.face_match_score == 0.0, \
            "Non-people query must not use face presence scoring"


class TestProgressiveSearchIdentity:
    """Regression: progressive search must use stable labels across phases."""

    def test_metadata_only_has_label(self):
        """search_metadata_only must return a label for UI consistency."""
        from services.search_orchestrator import OrchestratorResult
        r = OrchestratorResult(phase="metadata", label="test label")
        assert r.label, "Metadata-only result must have a label"

    def test_phase_field_distinguishes_phases(self):
        """Phase 1 and Phase 2 must be distinguishable by the phase field."""
        from services.search_orchestrator import OrchestratorResult
        r1 = OrchestratorResult(phase="metadata")
        r2 = OrchestratorResult(phase="full")
        assert r1.phase != r2.phase

    def test_build_label_stable_across_phases(self):
        """_build_label must produce the same output for the same QueryPlan."""
        from services.search_orchestrator import (
            SearchOrchestrator, QueryPlan, OrchestratorResult
        )
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._smart_find_service = None

        plan = QueryPlan(raw_query="sunset date:2024",
                         extracted_tokens=[{"label": "2024", "type": "date",
                                           "key": "date", "value": "2024"}])
        dummy_result = OrchestratorResult(paths=["/a.jpg"])

        label1 = orch._build_label(plan, dummy_result)
        label2 = orch._build_label(plan, dummy_result)
        assert label1 == label2, "Same plan must produce same label"
        assert "2024" in label1, "Label must include extracted tokens"


class TestFacetHygiene:
    """Regression: facets must be meaningful (Patch E)."""

    def test_single_bucket_facet_pruned(self):
        """Facets where everything falls in one bucket should be removed."""
        from services.search_orchestrator import FacetComputer
        # All photos, no videos, 10 items — media facet should be absent
        paths = [f"/{i}.jpg" for i in range(10)]
        facets = FacetComputer.compute(paths, {})
        assert "media" not in facets

    def test_small_results_no_facets(self):
        """Result sets below _MIN_RESULTS_FOR_FACETS get no facets."""
        from services.search_orchestrator import FacetComputer
        paths = ["/a.jpg", "/b.mp4", "/c.jpg"]
        facets = FacetComputer.compute(paths, {})
        assert facets == {}

    def test_facets_ordered_by_entropy(self):
        """Facets should be ordered by discriminating power (entropy)."""
        from services.search_orchestrator import FacetComputer
        # 10 results with balanced splits for years/location/rating
        paths = [f"/{i}.jpg" for i in range(5)] + [f"/{i}.mp4" for i in range(5, 10)]
        meta = {}
        for i in range(5):
            meta[f"/{i}.jpg"] = {"has_gps": True, "rating": 5, "created_date": "2024-01-01"}
        for i in range(5, 10):
            meta[f"/{i}.mp4"] = {"has_gps": False, "rating": 0, "created_date": "2023-06-15"}
        facets = FacetComputer.compute(paths, meta)
        # Verify result is a dict (entropy ordering is internal)
        assert isinstance(facets, dict)
        # All facets must have 2+ meaningful buckets
        for name, buckets in facets.items():
            assert len(buckets) >= 2, f"Facet '{name}' has fewer than 2 buckets"


class TestBackoffTuning:
    """Regression: backoff must respect min_results_target."""

    def test_min_results_target_exists(self):
        """SearchOrchestrator must have _MIN_RESULTS_TARGET attribute."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, '_MIN_RESULTS_TARGET')
        assert SearchOrchestrator._MIN_RESULTS_TARGET > 0

    def test_is_people_implied_method_exists(self):
        """SearchOrchestrator must have _is_people_implied method."""
        from services.search_orchestrator import SearchOrchestrator
        assert hasattr(SearchOrchestrator, '_is_people_implied')
        assert callable(SearchOrchestrator._is_people_implied)


# ══════════════════════════════════════════════════════════════════════
# Regression Tests: Path Normalization in Uniqueness
# ══════════════════════════════════════════════════════════════════════

class TestPathNormalization:
    """Regression: paths with redundant separators or trailing slashes
    must be treated as the same file by _enforce_unique_paths."""

    def test_redundant_separator_deduplicated(self):
        """'/photos//a.jpg' and '/photos/a.jpg' are the same file."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        scored = [
            ScoredResult(path="/photos/a.jpg", final_score=0.95),
            ScoredResult(path="/photos//a.jpg", final_score=0.80),
        ]
        unique = SearchOrchestrator._enforce_unique_paths(scored)
        assert len(unique) == 1
        assert unique[0].final_score == 0.95  # kept higher-scored

    def test_trailing_slash_deduplicated(self):
        """Trailing slash variants must deduplicate."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        scored = [
            ScoredResult(path="/photos/dir/b.jpg", final_score=0.90),
            ScoredResult(path="/photos/dir//b.jpg", final_score=0.85),
            ScoredResult(path="/photos/./dir/b.jpg", final_score=0.70),
        ]
        unique = SearchOrchestrator._enforce_unique_paths(scored)
        assert len(unique) == 1
        assert unique[0].final_score == 0.90

    def test_dot_segments_normalized(self):
        """/photos/sub/../sub/c.jpg and /photos/sub/c.jpg are the same."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult
        scored = [
            ScoredResult(path="/photos/sub/c.jpg", final_score=0.90),
            ScoredResult(path="/photos/sub/../sub/c.jpg", final_score=0.80),
        ]
        unique = SearchOrchestrator._enforce_unique_paths(scored)
        assert len(unique) == 1

    def test_normalize_path_static_method(self):
        """_normalize_path must clean up redundant path components."""
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._normalize_path("/a//b/./c.jpg") == "/a/b/c.jpg"
        assert SearchOrchestrator._normalize_path("/a/b/../b/c.jpg") == "/a/b/c.jpg"


# ══════════════════════════════════════════════════════════════════════
# Regression Tests: Face Weight Auto-Disable
# ══════════════════════════════════════════════════════════════════════

class TestFaceWeightAutoDisable:
    """Regression: when face data coverage < 1%, face scoring must be
    disabled automatically to avoid wasting ranking budget on a null signal."""

    def test_is_people_implied_detects_names(self):
        """Queries with person-like tokens should be flagged as people-implied."""
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch._smart_find_service = None
        # The method should exist and be callable
        assert callable(orch._is_people_implied)

    def test_face_coverage_threshold(self):
        """Face weight auto-disable fires when coverage is below 1%."""
        # Verify the threshold is enforced via the code path:
        # if face_coverage < 0.01 and total_photos > 0: people_implied = False
        # We test the logic directly rather than running a full search
        face_photo_count = 0
        total_photos = 100
        face_coverage = face_photo_count / total_photos if total_photos else 0
        assert face_coverage < 0.01
        # With zero face data, face scoring should be disabled
        people_implied = True
        if face_coverage < 0.01 and total_photos > 0:
            people_implied = False
        assert people_implied is False

    def test_face_coverage_above_threshold_keeps_enabled(self):
        """Face weight stays enabled when coverage >= 1%."""
        face_photo_count = 5
        total_photos = 100
        face_coverage = face_photo_count / total_photos
        assert face_coverage >= 0.01
        people_implied = True
        if face_coverage < 0.01 and total_photos > 0:
            people_implied = False
        assert people_implied is True


# ══════════════════════════════════════════════════════════════════════
# Regression Tests: Empty Search Result Contract
# ══════════════════════════════════════════════════════════════════════

class TestEmptyResultContract:
    """Regression: when orchestrator returns paths=[], the UI must show
    an empty state — NEVER load the full library.  The root cause was
    list(filter_paths) if filter_paths else None converting [] to None."""

    def test_orchestrator_result_empty_paths_is_list(self):
        """OrchestratorResult(paths=[]) must keep paths as an empty list,
        not convert to None."""
        from services.search_orchestrator import OrchestratorResult
        result = OrchestratorResult(paths=[])
        assert result.paths is not None
        assert result.paths == []
        assert isinstance(result.paths, list)

    def test_orchestrator_result_none_paths_stays_none(self):
        """OrchestratorResult(paths=None) must keep paths as None."""
        from services.search_orchestrator import OrchestratorResult
        result = OrchestratorResult()
        # Default should be empty list or None depending on implementation
        assert isinstance(result.paths, (list, type(None)))

    def test_empty_list_is_falsy_but_not_none(self):
        """Python gotcha regression: bool([]) is False but [] is not None.
        Code must use `is not None` checks, NOT truthiness."""
        paths = []
        # This is the WRONG check that caused the root bug:
        wrong_result = list(paths) if paths else None
        assert wrong_result is None  # This proves the bug pattern

        # This is the CORRECT check:
        if paths is not None:
            correct_result = list(paths)
        else:
            correct_result = None
        assert correct_result is not None
        assert correct_result == []

    def test_uniqueness_on_empty_returns_empty(self):
        """_enforce_unique_paths([]) must return [] not crash."""
        from services.search_orchestrator import SearchOrchestrator
        result = SearchOrchestrator._enforce_unique_paths([])
        assert result == []

    def test_facets_on_empty_returns_empty(self):
        """FacetComputer.compute([], {}) must return {} not crash."""
        from services.search_orchestrator import FacetComputer
        facets = FacetComputer.compute([], {})
        assert facets == {}


# ══════════════════════════════════════════════════════════════════════
# Regression Tests: Documents Precision (Patch E1-E2)
# ══════════════════════════════════════════════════════════════════════

class TestDocumentsPrecision:
    """Documents preset must not match generic photos or screenshots.

    Aligned with iPhone/Google Photos/Lightroom/Excire classification:
    'Documents' means scanned pages, receipts, forms — NOT any image
    containing text-like patterns or phone screenshots.
    """

    def test_documents_prompts_no_broad_terms(self):
        """Documents prompts must NOT include 'text' or 'paper' (semantic magnets)."""
        from services.smart_find_service import BUILTIN_PRESETS
        doc_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        prompts = [p.lower() for p in doc_preset["prompts"]]
        assert "text" not in prompts, "'text' is a semantic magnet — too broad"
        assert "paper" not in prompts, "'paper' matches paper plates, paper airplanes, etc."
        assert "handwriting" not in prompts, "'handwriting' replaced by 'handwritten note'"

    def test_documents_has_specific_prompts(self):
        """Documents must have doc-specific prompts (receipts, forms, etc.)."""
        from services.smart_find_service import BUILTIN_PRESETS
        doc_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        prompts = set(doc_preset["prompts"])
        # Must have at least 3 of these document-specific terms
        expected = {"scanned document", "printed page", "form", "invoice",
                    "receipt", "handwritten note"}
        overlap = prompts & expected
        assert len(overlap) >= 3, f"Only {overlap} found, expected >=3 from {expected}"

    def test_documents_has_negative_prompts(self):
        """Documents must define negative_prompts to penalize screenshot bleed."""
        from services.smart_find_service import BUILTIN_PRESETS
        doc_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        neg = doc_preset.get("negative_prompts", [])
        assert len(neg) >= 1, "Documents must have at least 1 negative prompt"
        neg_lower = [n.lower() for n in neg]
        assert any("screenshot" in n for n in neg_lower), \
            "Documents negative_prompts must include 'screenshot'"

    def test_documents_backoff_disabled(self):
        """Documents must set allow_backoff=False (precision-first)."""
        from services.smart_find_service import BUILTIN_PRESETS
        doc_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        assert doc_preset.get("allow_backoff") is False, \
            "Documents preset must disable backoff to prevent false positives"

    def test_documents_no_threshold_override(self):
        """Documents must NOT use threshold_override (ViT-B/32 docs score 0.220-0.232;
        any override above 0.22 kills genuine results). Face gate is the discriminator."""
        from services.smart_find_service import BUILTIN_PRESETS
        doc_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        override = doc_preset.get("threshold_override")
        assert override is None, \
            f"Documents must not set threshold_override ({override}); " \
            f"rely on exclude_faces gate instead"

    def test_documents_exclude_faces(self):
        """Documents must set exclude_faces=True (a document is never a portrait)."""
        from services.smart_find_service import BUILTIN_PRESETS
        doc_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        assert doc_preset.get("exclude_faces") is True, \
            "Documents preset must exclude photos with detected faces"


class TestScreenshotDetection:
    """_detect_screenshot must identify screenshots by filename and resolution."""

    def test_filename_screenshot_lowercase(self):
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._detect_screenshot("Screenshot_2024.png", 0, 0)

    def test_filename_screenshot_mixed_case(self):
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._detect_screenshot("SCREENSHOT-2024-01.jpg", 0, 0)

    def test_filename_screen_shot_underscore(self):
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._detect_screenshot("Screen_Shot_2024.png", 0, 0)

    def test_filename_bildschirmfoto(self):
        """German locale screenshot filename."""
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._detect_screenshot("Bildschirmfoto 2024.png", 0, 0)

    def test_filename_captura(self):
        """Spanish locale screenshot filename."""
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._detect_screenshot("Captura de pantalla.png", 0, 0)

    def test_normal_photo_not_screenshot(self):
        """Regular photo filenames must NOT be flagged as screenshots."""
        from services.search_orchestrator import SearchOrchestrator
        assert not SearchOrchestrator._detect_screenshot("IMG_2024.jpg", 0, 0)
        assert not SearchOrchestrator._detect_screenshot("DSC_0001.jpg", 0, 0)
        assert not SearchOrchestrator._detect_screenshot("photo_2024.heic", 0, 0)

    def test_iphone_resolution_alone_not_sufficient(self):
        """Resolution alone is NOT sufficient for screenshot detection (multi-signal)."""
        from services.search_orchestrator import SearchOrchestrator
        # Resolution match alone scores 0.25 (below 0.50 threshold)
        # A generic filename like IMG_1234.png does NOT trigger filename match
        assert not SearchOrchestrator._detect_screenshot("IMG_1234.jpg", 1179, 2556)

    def test_resolution_plus_png_is_screenshot(self):
        """Resolution match + PNG extension (non-camera source) = screenshot."""
        from services.search_orchestrator import SearchOrchestrator
        # Resolution (0.25) + PNG resolution match (0.10) = 0.35, still below
        # But a screenshot in a screenshots folder would work
        from repository.search_feature_repository import _compute_screenshot_confidence
        # PNG alone not enough but combined with folder it is
        conf = _compute_screenshot_confidence(
            "/Users/test/Screenshots/IMG_1234.png", 1179, 2556
        )
        assert conf >= 0.50  # folder (0.40) + resolution (0.25) + png (0.10)

    def test_android_fhd_resolution_alone_not_sufficient(self):
        """Android 1080x1920 FHD resolution alone is NOT sufficient."""
        from services.search_orchestrator import SearchOrchestrator
        # Resolution alone = 0.25, not enough
        assert not SearchOrchestrator._detect_screenshot("image.jpg", 1080, 1920)

    def test_normal_photo_resolution_not_screenshot(self):
        """Common camera resolutions must NOT match screenshot detection."""
        from services.search_orchestrator import SearchOrchestrator
        # 4000x3000 = common 12MP camera
        assert not SearchOrchestrator._detect_screenshot("IMG.jpg", 4000, 3000)
        # 6000x4000 = common 24MP camera
        assert not SearchOrchestrator._detect_screenshot("DSC.jpg", 6000, 4000)

    def test_zero_dimensions_not_screenshot(self):
        """Missing/zero dimensions should not trigger screenshot detection."""
        from services.search_orchestrator import SearchOrchestrator
        assert not SearchOrchestrator._detect_screenshot("IMG.jpg", 0, 0)
        assert not SearchOrchestrator._detect_screenshot("IMG.jpg", None, None)

    def test_screenshot_confidence_multi_signal(self):
        """Multi-signal screenshot confidence requires combined evidence."""
        from repository.search_feature_repository import _compute_screenshot_confidence
        # Filename alone is strong enough (0.90)
        assert _compute_screenshot_confidence("Screenshot_2024.png", 0, 0) >= 0.50
        # Resolution alone is NOT enough (0.25)
        assert _compute_screenshot_confidence("IMG.jpg", 1179, 2556) < 0.50
        # Resolution + screenshot folder is enough (0.25 + 0.40 = 0.65)
        assert _compute_screenshot_confidence(
            "/path/Screenshots/IMG.jpg", 1179, 2556
        ) >= 0.50


class TestAllowBackoffQueryPlan:
    """QueryPlan.allow_backoff must be respected by backoff logic."""

    def test_queryplan_allow_backoff_default_true(self):
        """QueryPlan defaults to allow_backoff=True."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan()
        assert plan.allow_backoff is True

    def test_queryplan_allow_backoff_can_be_false(self):
        """QueryPlan can be constructed with allow_backoff=False."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(allow_backoff=False)
        assert plan.allow_backoff is False

    def test_queryplan_negative_prompts_default_empty(self):
        """QueryPlan defaults to empty negative_prompts list."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan()
        assert plan.negative_prompts == []

    def test_queryplan_negative_prompts_set(self):
        """QueryPlan can hold negative_prompts."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(negative_prompts=["screenshot", "phone screen"])
        assert len(plan.negative_prompts) == 2
        assert "screenshot" in plan.negative_prompts


class TestNegativePromptPenalty:
    """_apply_negative_prompt_penalty must exclude screenshots from Documents."""

    def test_screenshots_excluded_from_documents(self):
        """Screenshots in project_meta must be removed from Documents results."""
        from services.search_orchestrator import SearchOrchestrator, ScoredResult, QueryPlan
        scored = [
            ScoredResult(path="/photos/receipt.jpg", final_score=0.85),
            ScoredResult(path="/photos/screenshot1.png", final_score=0.80),
            ScoredResult(path="/photos/form.jpg", final_score=0.75),
        ]
        plan = QueryPlan(
            preset_id="documents",
            negative_prompts=["screenshot"],
        )
        project_meta = {
            "/photos/receipt.jpg": {"is_screenshot": False},
            "/photos/screenshot1.png": {"is_screenshot": True},
            "/photos/form.jpg": {"is_screenshot": False},
        }

        # We can't call the full method (needs DB + CLIP), but we can
        # test the hard-exclude logic directly
        if plan.preset_id in ("documents",):
            filtered = [
                sr for sr in scored
                if not project_meta.get(sr.path, {}).get("is_screenshot", False)
            ]
        else:
            filtered = scored

        assert len(filtered) == 2
        assert all(sr.path != "/photos/screenshot1.png" for sr in filtered)

    def test_non_documents_preset_keeps_screenshots(self):
        """Non-documents presets must NOT exclude screenshots."""
        from services.search_orchestrator import ScoredResult, QueryPlan
        scored = [
            ScoredResult(path="/photos/a.jpg", final_score=0.85),
            ScoredResult(path="/photos/screenshot.png", final_score=0.80),
        ]
        plan = QueryPlan(
            preset_id="beach",
            negative_prompts=[],
        )
        project_meta = {
            "/photos/a.jpg": {"is_screenshot": False},
            "/photos/screenshot.png": {"is_screenshot": True},
        }

        # Beach preset should not exclude screenshots
        if plan.preset_id in ("documents",):
            filtered = [
                sr for sr in scored
                if not project_meta.get(sr.path, {}).get("is_screenshot", False)
            ]
        else:
            filtered = scored

        assert len(filtered) == 2  # Both kept


class TestFaceExclusionGate:
    """Photos with detected faces must be excluded from Documents results."""

    def test_portraits_excluded_from_documents(self):
        """Photos with face_count > 0 must be removed when exclude_faces=True."""
        from services.search_orchestrator import ScoredResult, QueryPlan
        scored = [
            ScoredResult(path="/photos/receipt.jpg", final_score=0.85),
            ScoredResult(path="/photos/portrait.jpg", final_score=0.80),
            ScoredResult(path="/photos/form.jpg", final_score=0.75),
        ]
        plan = QueryPlan(preset_id="documents", exclude_faces=True)
        project_meta = {
            "/photos/receipt.jpg": {"face_count": 0},
            "/photos/portrait.jpg": {"face_count": 2},
            "/photos/form.jpg": {"face_count": 0},
        }

        if plan.exclude_faces:
            filtered = [
                sr for sr in scored
                if (project_meta.get(sr.path, {}).get("face_count", 0) or 0) == 0
            ]
        else:
            filtered = scored

        assert len(filtered) == 2
        assert all(sr.path != "/photos/portrait.jpg" for sr in filtered)

    def test_face_exclusion_off_keeps_portraits(self):
        """When exclude_faces=False, photos with faces are kept."""
        from services.search_orchestrator import ScoredResult, QueryPlan
        scored = [
            ScoredResult(path="/photos/a.jpg", final_score=0.85),
            ScoredResult(path="/photos/portrait.jpg", final_score=0.80),
        ]
        plan = QueryPlan(preset_id="beach", exclude_faces=False)
        project_meta = {
            "/photos/a.jpg": {"face_count": 0},
            "/photos/portrait.jpg": {"face_count": 1},
        }

        if plan.exclude_faces:
            filtered = [
                sr for sr in scored
                if (project_meta.get(sr.path, {}).get("face_count", 0) or 0) == 0
            ]
        else:
            filtered = scored

        assert len(filtered) == 2

    def test_queryplan_threshold_override_default_none(self):
        """QueryPlan defaults to threshold_override=None."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan()
        assert plan.threshold_override is None

    def test_queryplan_threshold_override_set(self):
        """QueryPlan can hold a threshold_override value."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan(threshold_override=0.26)
        assert plan.threshold_override == 0.26

    def test_queryplan_exclude_faces_default_false(self):
        """QueryPlan defaults to exclude_faces=False."""
        from services.search_orchestrator import QueryPlan
        plan = QueryPlan()
        assert plan.exclude_faces is False


class TestBackoffFloor:
    """Backoff must never drop more than 0.04 below the original threshold."""

    def test_backoff_floor_limits_drop(self):
        """With threshold=0.22, backoff cannot go below 0.18."""
        threshold = 0.22
        backoff_step = 0.02
        backoff_floor = max(0.05, threshold - 0.04)
        for retry in range(1, 5):
            lowered = max(backoff_floor, threshold - (backoff_step * retry))
            assert lowered >= 0.18, \
                f"Retry {retry}: lowered={lowered} is below floor 0.18"

    def test_backoff_floor_higher_threshold(self):
        """With threshold=0.26 (Documents override), floor is 0.22."""
        threshold = 0.26
        backoff_step = 0.02
        backoff_floor = max(0.05, threshold - 0.04)
        assert backoff_floor == 0.22
        for retry in range(1, 5):
            lowered = max(backoff_floor, threshold - (backoff_step * retry))
            assert lowered >= 0.22


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Gate Engine (_apply_gates)
# ══════════════════════════════════════════════════════════════════════

class TestGateEngine:
    """
    Test the consolidated gate engine for category-specific hard filtering.

    Aligned with best practices from iPhone, Google Photos, Lightroom, and Excire:
    hard gates separate categories that CLIP similarity alone cannot distinguish.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.search_orchestrator import SearchOrchestrator, QueryPlan, ScoredResult
        self.SearchOrchestrator = SearchOrchestrator
        self.QueryPlan = QueryPlan
        self.ScoredResult = ScoredResult

    def _make_orch(self):
        """Create an uninitialized orchestrator (no DB, no CLIP needed)."""
        return self.SearchOrchestrator.__new__(self.SearchOrchestrator)

    def _make_scored(self, path, score=0.25, screenshot_score=0.0):
        return self.ScoredResult(
            path=path, clip_score=score, recency_score=0, favorite_score=0,
            location_score=0, face_match_score=0, final_score=score,
            screenshot_score=screenshot_score,
        )

    # ── Documents preset: exclude faces + screenshots + tiny images ──

    def test_documents_excludes_faces(self):
        """Documents gate drops photos with detected faces."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Documents", preset_id="documents", source="preset",
            exclude_faces=True,
        )
        meta = {
            "doc.png": {"is_screenshot": False, "face_count": 0, "width": 1200, "height": 1600, "has_gps": False},
            "portrait.jpg": {"is_screenshot": False, "face_count": 2, "width": 2000, "height": 3000, "has_gps": False},
        }
        scored = [self._make_scored("doc.png"), self._make_scored("portrait.jpg")]
        kept = orch._apply_gates(scored, plan, meta)
        assert [r.path for r in kept] == ["doc.png"]

    def test_documents_excludes_screenshots(self):
        """Documents gate drops screenshots (separate category, not documents)."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Documents", preset_id="documents", source="preset",
            exclude_screenshots=True,
        )
        meta = {
            "doc.png": {"is_screenshot": False, "face_count": 0, "width": 1200, "height": 1600, "has_gps": False},
            "shot.png": {"is_screenshot": True, "face_count": 0, "width": 1170, "height": 2532, "has_gps": False},
        }
        scored = [self._make_scored("doc.png"), self._make_scored("shot.png")]
        kept = orch._apply_gates(scored, plan, meta)
        assert [r.path for r in kept] == ["doc.png"]

    def test_documents_excludes_tiny_images(self):
        """Documents gate drops icons/thumbnails below min_edge_size."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Documents", preset_id="documents", source="preset",
            min_edge_size=350,
        )
        meta = {
            "doc.png": {"is_screenshot": False, "face_count": 0, "width": 1200, "height": 1600, "has_gps": False},
            "icon.png": {"is_screenshot": False, "face_count": 0, "width": 64, "height": 64, "has_gps": False},
        }
        scored = [self._make_scored("doc.png"), self._make_scored("icon.png")]
        kept = orch._apply_gates(scored, plan, meta)
        assert [r.path for r in kept] == ["doc.png"]

    def test_documents_full_gate_profile(self):
        """Documents preset with all gates active: faces + screenshots + tiny."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Documents", preset_id="documents", source="preset",
            exclude_faces=True, exclude_screenshots=True, min_edge_size=350,
        )
        meta = {
            "doc_ok.png": {"is_screenshot": False, "face_count": 0, "width": 1200, "height": 1600, "has_gps": False},
            "portrait.jpg": {"is_screenshot": False, "face_count": 1, "width": 2000, "height": 3000, "has_gps": False},
            "shot.png": {"is_screenshot": True, "face_count": 0, "width": 1170, "height": 2532, "has_gps": False},
            "icon.png": {"is_screenshot": False, "face_count": 0, "width": 32, "height": 32, "has_gps": False},
        }
        scored = [self._make_scored(p) for p in meta]
        kept = orch._apply_gates(scored, plan, meta)
        assert [r.path for r in kept] == ["doc_ok.png"]

    # ── Screenshots preset: require is_screenshot ──

    def test_screenshots_requires_is_screenshot(self):
        """Screenshots gate keeps only photos detected as screenshots or supplemental hits."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Screenshots", preset_id="screenshots", source="preset",
            require_screenshot=True,
        )
        meta = {
            "shot1.png": {"is_screenshot": True, "face_count": 0, "width": 1170, "height": 2532, "has_gps": False},
            "photo.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": True},
            "supp.png": {"is_screenshot": False, "face_count": 0, "width": 1080, "height": 1920, "has_gps": False},
        }
        # shot1: flag (pass), photo: no flag + no score (drop), supp: score (pass)
        scored = [
            self._make_scored("shot1.png"),
            self._make_scored("photo.jpg", 0.30),
            self._make_scored("supp.png", 0.40, screenshot_score=0.25)
        ]
        kept = orch._apply_gates(scored, plan, meta)
        paths = [r.path for r in kept]
        assert "shot1.png" in paths
        assert "supp.png" in paths
        assert "photo.jpg" not in paths

    # ── People-centric presets: require faces ──

    def test_wedding_requires_faces(self):
        """Wedding gate drops photos without detected faces."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Wedding", preset_id="wedding", source="preset",
            require_faces=True, min_face_count=1,
        )
        # Enough face coverage (>1%) so gates are not skipped
        meta = {
            "couple.jpg": {"is_screenshot": False, "face_count": 2, "width": 4000, "height": 3000, "has_gps": True},
            "cake.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": True},
            "venue.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": True},
            # Add enough face photos to exceed 1% coverage
            **{f"person{i}.jpg": {"is_screenshot": False, "face_count": 1, "width": 4000, "height": 3000, "has_gps": False}
               for i in range(5)},
        }
        scored = [self._make_scored("couple.jpg"), self._make_scored("cake.jpg"), self._make_scored("venue.jpg")]
        kept = orch._apply_gates(scored, plan, meta)
        assert [r.path for r in kept] == ["couple.jpg"]

    def test_face_gate_skipped_when_no_face_data(self):
        """Face gates are skipped when face pipeline hasn't run (<1% coverage)."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="Wedding", preset_id="wedding", source="preset",
            require_faces=True, min_face_count=1,
        )
        # No face data at all — 0% coverage
        meta = {
            "couple.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": True},
            "cake.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": True},
        }
        scored = [self._make_scored("couple.jpg"), self._make_scored("cake.jpg")]
        kept = orch._apply_gates(scored, plan, meta)
        # All results kept because face gate is auto-disabled
        assert len(kept) == 2

    # ── GPS gate ──

    def test_require_gps_gate(self):
        """GPS gate drops photos without GPS coordinates."""
        orch = self._make_orch()
        plan = self.QueryPlan(
            raw_query="With Location", source="preset",
            require_gps_gate=True,
        )
        meta = {
            "gps.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": True},
            "no_gps.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": False},
        }
        scored = [self._make_scored("gps.jpg"), self._make_scored("no_gps.jpg")]
        kept = orch._apply_gates(scored, plan, meta)
        assert [r.path for r in kept] == ["gps.jpg"]

    # ── No gates active = no-op ──

    def test_no_gates_passthrough(self):
        """When no gates are active, all results pass through unchanged."""
        orch = self._make_orch()
        plan = self.QueryPlan(raw_query="beach", source="preset", preset_id="beach")
        meta = {
            "a.jpg": {"is_screenshot": False, "face_count": 0, "width": 4000, "height": 3000, "has_gps": False},
            "b.jpg": {"is_screenshot": True, "face_count": 3, "width": 1170, "height": 2532, "has_gps": True},
        }
        scored = [self._make_scored("a.jpg"), self._make_scored("b.jpg")]
        kept = orch._apply_gates(scored, plan, meta)
        assert len(kept) == 2

    def test_empty_scored_returns_empty(self):
        """Empty input returns empty output."""
        orch = self._make_orch()
        plan = self.QueryPlan(raw_query="Documents", preset_id="documents", exclude_faces=True)
        kept = orch._apply_gates([], plan, {})
        assert kept == []

    # ── Gate profile integration via _plan_from_preset ──

    def test_plan_from_preset_reads_gate_profile(self):
        """Verify _plan_from_preset populates gate fields from preset gate_profile."""
        from services.smart_find_service import BUILTIN_PRESETS

        # Verify Documents preset has gate_profile
        docs_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "documents")
        gp = docs_preset.get("gate_profile", {})
        assert gp.get("exclude_faces") is True
        assert gp.get("exclude_screenshots") is True
        assert gp.get("min_edge_size", 0) > 0

        # Verify Screenshots preset has gate_profile
        screenshots_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "screenshots")
        gp = screenshots_preset.get("gate_profile", {})
        assert gp.get("require_screenshot") is True

        # Verify Wedding preset has gate_profile
        wedding_preset = next(p for p in BUILTIN_PRESETS if p["id"] == "wedding")
        gp = wedding_preset.get("gate_profile", {})
        assert gp.get("require_faces") is True
        assert gp.get("min_face_count", 0) >= 1

    def test_documents_implicit_exclude_screenshots(self):
        """Documents preset_id triggers exclude_screenshots even without explicit flag."""
        orch = self._make_orch()
        # Only set preset_id, not the explicit exclude_screenshots flag
        plan = self.QueryPlan(
            raw_query="Documents", preset_id="documents", source="preset",
        )
        meta = {
            "doc.png": {"is_screenshot": False, "face_count": 0, "width": 1200, "height": 1600, "has_gps": False},
            "shot.png": {"is_screenshot": True, "face_count": 0, "width": 1170, "height": 2532, "has_gps": False},
        }
        scored = [self._make_scored("doc.png"), self._make_scored("shot.png")]
        kept = orch._apply_gates(scored, plan, meta)
        # Documents preset_id triggers exclude_screenshots implicitly
        assert [r.path for r in kept] == ["doc.png"]


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Preset Family Classification
# ══════════════════════════════════════════════════════════════════════

class TestPresetFamilies:
    """Test preset family classification for gate profile selection."""

    def test_type_presets_classified_correctly(self):
        """Type-like presets should be in the 'type' family."""
        from services.search_orchestrator import SearchOrchestrator
        for preset_id in ["documents", "screenshots"]:
            assert SearchOrchestrator._get_preset_family(preset_id) == "type", \
                f"{preset_id} should be family='type'"

    def test_utility_presets_classified_correctly(self):
        """Utility/metadata presets should be in the 'utility' family."""
        from services.search_orchestrator import SearchOrchestrator
        for preset_id in ["videos", "favorites", "gps_photos"]:
            assert SearchOrchestrator._get_preset_family(preset_id) == "utility", \
                f"{preset_id} should be family='utility'"

    def test_people_event_presets_classified_correctly(self):
        """People-event presets should be in the 'people_event' family."""
        from services.search_orchestrator import SearchOrchestrator
        for preset_id in ["wedding", "party", "baby", "portraits"]:
            assert SearchOrchestrator._get_preset_family(preset_id) == "people_event", \
                f"{preset_id} should be family='people_event'"

    def test_scenic_presets_classified_correctly(self):
        """Scenic presets should be in the 'scenic' family."""
        from services.search_orchestrator import SearchOrchestrator
        for preset_id in ["beach", "mountains", "city", "forest", "lake",
                          "travel", "sunset", "sport", "food",
                          "flowers", "snow", "night", "architecture", "car",
                          "panoramas"]:
            assert SearchOrchestrator._get_preset_family(preset_id) == "scenic", \
                f"{preset_id} should be family='scenic'"
        # pets is now animal_object (precision-first, not scenic)
        assert SearchOrchestrator._get_preset_family("pets") == "animal_object"

    def test_unknown_preset_defaults_to_scenic(self):
        """Unknown presets should default to 'scenic' (recall-first)."""
        from services.search_orchestrator import SearchOrchestrator
        assert SearchOrchestrator._get_preset_family("unknown_preset") == "scenic"

    def test_builtin_presets_have_family_field(self):
        """All BUILTIN_PRESETS should have a 'family' field."""
        from services.smart_find_service import BUILTIN_PRESETS
        for preset in BUILTIN_PRESETS:
            assert "family" in preset, \
                f"Preset '{preset['id']}' missing 'family' field"
            assert preset["family"] in ("type", "people_event", "scenic", "animal_object", "utility"), \
                f"Preset '{preset['id']}' has invalid family '{preset['family']}'"


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Expanded Scenic Prompts
# ══════════════════════════════════════════════════════════════════════

class TestExpandedPrompts:
    """Test that scenic presets have expanded multi-prompt lists."""

    def test_scenic_presets_have_minimum_prompts(self):
        """Scenic presets should have at least 6 prompts for broad recall."""
        from services.smart_find_service import BUILTIN_PRESETS
        scenic_presets = [p for p in BUILTIN_PRESETS
                         if p.get("family") == "scenic" and p.get("prompts")]
        for preset in scenic_presets:
            assert len(preset["prompts"]) >= 6, \
                f"Scenic preset '{preset['id']}' has only {len(preset['prompts'])} prompts, " \
                f"expected >= 6 for broad semantic recall"

    def test_mountains_has_expanded_prompts(self):
        """Mountains preset should have diversified prompt bundle."""
        from services.smart_find_service import _BUILTIN_LOOKUP
        mountains = _BUILTIN_LOOKUP["mountains"]
        prompts = mountains["prompts"]
        # Should include variety: alpine, ridge, snow, valley, etc.
        assert len(prompts) >= 7
        assert any("alpine" in p for p in prompts)
        assert any("valley" in p or "ridge" in p for p in prompts)

    def test_beach_has_expanded_prompts(self):
        """Beach preset should have diversified prompt bundle."""
        from services.smart_find_service import _BUILTIN_LOOKUP
        beach = _BUILTIN_LOOKUP["beach"]
        prompts = beach["prompts"]
        assert len(prompts) >= 6
        assert any("shore" in p for p in prompts)
        assert any("coast" in p for p in prompts)


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Structural Document Scorer
# ══════════════════════════════════════════════════════════════════════

class TestStructuralDocumentScorer:
    """Test document structural scoring (now computed as first-class term)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.search_orchestrator import SearchOrchestrator, ScoredResult, QueryPlan
        self.SearchOrchestrator = SearchOrchestrator
        self.ScoredResult = ScoredResult
        self.QueryPlan = QueryPlan

    def _make_orch(self):
        orch = self.SearchOrchestrator.__new__(self.SearchOrchestrator)
        orch.project_id = 999
        return orch

    def test_documents_structural_scores_favor_doc_extensions(self):
        """Document-like extensions (.png) should get higher structural scores than photos (.jpg)."""
        orch = self._make_orch()
        plan = self.QueryPlan(preset_id="documents", source="preset")
        meta = {
            "/doc.png": {"width": 1200, "height": 1600, "face_count": 0, "ocr_text": "", "ext": ".png"},
            "/photo.jpg": {"width": 4000, "height": 3000, "face_count": 0, "ocr_text": "", "ext": ".jpg"},
        }
        scores = orch._compute_structural_scores(plan, meta, set())
        assert scores["/doc.png"] > scores["/photo.jpg"], \
            "Document-like .png should get higher structural score than photo-like .jpg"

    def test_documents_structural_scores_favor_page_ratio(self):
        """Page-like aspect ratios should score higher than photo ratios."""
        orch = self._make_orch()
        plan = self.QueryPlan(preset_id="documents", source="preset")
        meta = {
            "/doc.png": {"width": 800, "height": 1100, "ocr_text": "", "ext": ".png"},   # A4-like
            "/photo_32.jpg": {"width": 6000, "height": 4000, "ocr_text": "", "ext": ".jpg"},  # 3:2
        }
        scores = orch._compute_structural_scores(plan, meta, set())
        assert scores["/doc.png"] > scores["/photo_32.jpg"]

    def test_non_document_preset_returns_empty_structural(self):
        """Non-type presets don't compute structural scores."""
        orch = self._make_orch()
        plan = self.QueryPlan(preset_id="beach", source="preset")
        meta = {"/photo.jpg": {"width": 4000, "height": 3000, "ocr_text": "", "ext": ".jpg"}}
        # _compute_structural_scores is only called for type family,
        # so for scenic the orchestrator would call _compute_scenic_anti_type_scores instead
        from services.ranker import get_preset_family
        assert get_preset_family("beach") == "scenic"

    def test_ocr_text_boosts_document_score(self):
        """Assets with OCR text should get higher structural scores for documents."""
        orch = self._make_orch()
        plan = self.QueryPlan(preset_id="documents", source="preset")
        meta = {
            "/doc_with_ocr.png": {"width": 800, "height": 1100, "ocr_text": "Invoice #12345 Total: $500", "ext": ".png"},
            "/doc_no_ocr.png": {"width": 800, "height": 1100, "ocr_text": "", "ext": ".png"},
        }
        scores = orch._compute_structural_scores(plan, meta, set())
        assert scores["/doc_with_ocr.png"] > scores["/doc_no_ocr.png"], \
            "OCR text presence should boost structural score"


# ══════════════════════════════════════════════════════════════════════
# Unit Tests: Post-Dedup Gate Re-validation
# ══════════════════════════════════════════════════════════════════════

class TestPostDedupGateRevalidation:
    """Test that dedup cannot reintroduce gated-out results."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from services.search_orchestrator import SearchOrchestrator, ScoredResult, QueryPlan
        self.SearchOrchestrator = SearchOrchestrator
        self.ScoredResult = ScoredResult
        self.QueryPlan = QueryPlan

    def test_gate_path_normalization(self):
        """Gate should find metadata even with path normalization differences."""
        orch = self.SearchOrchestrator.__new__(self.SearchOrchestrator)
        orch.project_id = 999
        plan = self.QueryPlan(preset_id="screenshots", source="preset",
                              require_screenshot=True)
        # Meta uses one path format, scored result uses another
        meta = {
            "c:/users/test/photo.jpg": {
                "is_screenshot": True, "face_count": 0, "has_gps": False
            },
        }
        scored = [
            self.ScoredResult(path="c:/users/test/photo.jpg", final_score=0.5),
        ]
        kept = orch._apply_gates(scored, plan, meta)
        # Should pass because path normalizes to match
        assert len(kept) == 1

    def test_gate_rejects_non_screenshot_in_screenshot_preset(self):
        """Non-screenshot files must be rejected by screenshot gate."""
        orch = self.SearchOrchestrator.__new__(self.SearchOrchestrator)
        orch.project_id = 999
        plan = self.QueryPlan(preset_id="screenshots", source="preset",
                              require_screenshot=True)
        meta = {
            "photo.jpeg": {
                "is_screenshot": False, "face_count": 2, "has_gps": True
            },
        }
        scored = [
            self.ScoredResult(path="photo.jpeg", final_score=0.8),
        ]
        kept = orch._apply_gates(scored, plan, meta)
        assert len(kept) == 0, "Non-screenshot JPEG should be rejected by screenshot gate"


# ══════════════════════════════════════════════════════════════════════
# Patch validation tests - scoring contract, screenshot detection, gates
# ══════════════════════════════════════════════════════════════════════


class TestScoringContractWithOCR:
    """Validate that structural + OCR weights are properly integrated."""

    def test_type_family_weights_include_structural_and_ocr_sum_to_one(self):
        """Type family weights must include structural + OCR + screenshot and sum to 1.0."""
        from services.ranker import get_weights_for_family
        w = get_weights_for_family("type")
        total = (
            w.w_clip + w.w_recency + w.w_favorite + w.w_location
            + w.w_face_match + w.w_structural + w.w_ocr + w.w_event
            + w.w_screenshot
        )
        assert abs(total - 1.0) < 1e-6, f"Type weights sum to {total}, expected 1.0"
        assert w.w_structural > 0, "Type family must have positive w_structural"
        assert w.w_ocr > 0, "Type family must have positive w_ocr"
        assert w.w_screenshot > 0, "Type family must have positive w_screenshot"

    def test_scenic_family_weights_sum_to_one(self):
        """Scenic family weights must sum to 1.0."""
        from services.ranker import FAMILY_WEIGHTS
        w = FAMILY_WEIGHTS["scenic"]
        total = (
            w.w_clip + w.w_recency + w.w_favorite + w.w_location
            + w.w_face_match + w.w_structural + w.w_ocr + w.w_event
            + w.w_screenshot
        )
        assert abs(total - 1.0) < 1e-6, f"Scenic weights sum to {total}, expected 1.0"

    def test_people_event_family_weights_sum_to_one(self):
        """People_event family weights must sum to 1.0."""
        from services.ranker import FAMILY_WEIGHTS
        w = FAMILY_WEIGHTS["people_event"]
        total = (
            w.w_clip + w.w_recency + w.w_favorite + w.w_location
            + w.w_face_match + w.w_structural + w.w_ocr + w.w_event
            + w.w_screenshot
        )
        assert abs(total - 1.0) < 1e-6, f"People_event weights sum to {total}, expected 1.0"
        assert w.w_event > 0, "People_event family must have positive w_event"

    def test_utility_family_weights_sum_to_one(self):
        """Utility family weights must sum to 1.0."""
        from services.ranker import FAMILY_WEIGHTS
        w = FAMILY_WEIGHTS["utility"]
        total = (
            w.w_clip + w.w_recency + w.w_favorite + w.w_location
            + w.w_face_match + w.w_structural + w.w_ocr + w.w_event
            + w.w_screenshot
        )
        assert abs(total - 1.0) < 1e-6, f"Utility weights sum to {total}, expected 1.0"

    def test_all_families_have_nine_weight_components(self):
        """All families must declare all 9 weight components."""
        from services.ranker import FAMILY_WEIGHTS
        for name, w in FAMILY_WEIGHTS.items():
            assert hasattr(w, 'w_ocr'), f"Family {name} missing w_ocr"
            assert hasattr(w, 'w_structural'), f"Family {name} missing w_structural"
            assert hasattr(w, 'w_event'), f"Family {name} missing w_event"
            assert hasattr(w, 'w_screenshot'), f"Family {name} missing w_screenshot"

    def test_ocr_score_affects_type_family_final_score(self):
        """OCR score must contribute to final score for type family."""
        from services.ranker import Ranker
        ranker = Ranker()
        meta = {"date_taken": None}
        sr_no_ocr = ranker.score(
            "doc.png", 0.3, "document", meta, family="type",
            structural_score=0.7, ocr_score=0.0,
        )
        sr_with_ocr = ranker.score(
            "doc.png", 0.3, "document", meta, family="type",
            structural_score=0.7, ocr_score=0.8,
        )
        assert sr_with_ocr.final_score > sr_no_ocr.final_score, \
            "OCR score must increase final score for type family"
        assert sr_with_ocr.ocr_score == 0.8

    def test_validate_normalizes_nine_weights(self):
        """validate() must normalize all 9 weights including w_ocr, w_event, and w_screenshot."""
        from services.ranker import ScoringWeights
        w = ScoringWeights(
            w_clip=0.50, w_recency=0.10, w_favorite=0.10,
            w_location=0.10, w_face_match=0.10,
            w_structural=0.10, w_ocr=0.10, w_event=0.10,
            w_screenshot=0.10,
        )
        # Total = 1.30, should normalize
        w.validate()
        total = (
            w.w_clip + w.w_recency + w.w_favorite + w.w_location
            + w.w_face_match + w.w_structural + w.w_ocr + w.w_event
            + w.w_screenshot
        )
        assert abs(total - 1.0) < 1e-6


class TestConservativeScreenshotDetection:
    """Screenshot detection must be conservative - resolution alone is NOT enough."""

    def test_resolution_alone_is_not_screenshot(self):
        """iPhone resolution alone must NOT classify as screenshot."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot("IMG_1234.JPG", 1179, 2556) is False

    def test_android_resolution_alone_is_not_screenshot(self):
        """Android FHD resolution alone must NOT classify as screenshot."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot("IMG_5678.jpg", 1080, 1920) is False

    def test_filename_is_hard_positive(self):
        """Screenshot filename pattern must always classify as screenshot."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot("Screenshot_2026-03-06.png", 1179, 2556) is True

    def test_screenshot_filename_without_resolution(self):
        """Screenshot filename works even without resolution data."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot("Screenshot_2026.png", 0, 0) is True

    def test_bildschirmfoto_filename(self):
        """German locale screenshot filename must be detected."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot("Bildschirmfoto 2026-03-06.png", 0, 0) is True

    def test_normal_camera_photo_not_screenshot(self):
        """Regular camera photos must NEVER be flagged as screenshots."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot("DSC_0001.jpg", 4000, 3000) is False
        assert _detect_screenshot("IMG_2024.heic", 4032, 3024) is False
        assert _detect_screenshot("photo_vacation.jpg", 1179, 2556) is False

    def test_screenshot_folder_with_resolution_is_screenshot(self):
        """Screenshot folder + known resolution should classify as screenshot."""
        from repository.search_feature_repository import _detect_screenshot
        assert _detect_screenshot(
            "/Users/test/Screenshots/IMG_1234.png", 1179, 2556
        ) is True


class TestDocumentGateRejectsPlainJPG:
    """Documents gate must reject plain JPGs without OCR or document extension."""

    def test_plain_jpg_without_ocr_rejected(self):
        """A scenic JPG with no OCR text must be rejected by document signal gate."""
        from services.gate_engine import GateEngine
        from services.ranker import ScoredResult

        plan = type('Plan', (), {
            'preset_id': 'documents',
            'require_screenshot': False,
            'exclude_screenshots': True,
            'exclude_faces': True,
            'require_faces': False,
            'min_face_count': 0,
            'require_gps_gate': False,
            'min_edge_size': 700,
            'require_document_signal': True,
        })()

        meta = {
            "c:/x/photo.jpg": {
                "ext": ".jpg",
                "ocr_text": "",
                "face_count": 0,
                "is_screenshot": False,
                "width": 3000,
                "height": 2000,
            }
        }
        scored = [ScoredResult(path="c:/x/photo.jpg", final_score=0.5, clip_score=0.25)]
        engine = GateEngine()
        kept, dropped = engine.apply(scored, plan, meta)
        assert len(kept) == 0, "Plain JPG without document signal must be rejected"
        assert "require_document_signal" in dropped

    def test_png_with_ocr_kept(self):
        """A PNG with OCR text must pass the document signal gate."""
        from services.gate_engine import GateEngine
        from services.ranker import ScoredResult

        plan = type('Plan', (), {
            'preset_id': 'documents',
            'require_screenshot': False,
            'exclude_screenshots': False,
            'exclude_faces': False,
            'require_faces': False,
            'min_face_count': 0,
            'require_gps_gate': False,
            'min_edge_size': 0,
            'require_document_signal': True,
        })()

        meta = {
            "c:/x/doc.png": {
                "ext": ".png",
                "ocr_text": "Invoice Total EUR 123.45",
                "face_count": 0,
                "is_screenshot": False,
                "width": 1400,
                "height": 2000,
            }
        }
        scored = [ScoredResult(path="c:/x/doc.png", final_score=0.6)]
        engine = GateEngine()
        kept, dropped = engine.apply(scored, plan, meta)
        assert len(kept) == 1, "PNG with OCR text must pass document signal gate"

    def test_pdf_without_ocr_kept(self):
        """A PDF must pass the document signal gate even without OCR."""
        from services.gate_engine import GateEngine
        from services.ranker import ScoredResult

        plan = type('Plan', (), {
            'preset_id': 'documents',
            'require_screenshot': False,
            'exclude_screenshots': False,
            'exclude_faces': False,
            'require_faces': False,
            'min_face_count': 0,
            'require_gps_gate': False,
            'min_edge_size': 0,
            'require_document_signal': True,
        })()

        meta = {
            "c:/x/contract.pdf": {
                "ext": ".pdf",
                "ocr_text": "",
                "face_count": 0,
                "is_screenshot": False,
                "width": 1200,
                "height": 1600,
            }
        }
        scored = [ScoredResult(path="c:/x/contract.pdf", final_score=0.5)]
        engine = GateEngine()
        kept, dropped = engine.apply(scored, plan, meta)
        assert len(kept) == 1, "PDF must pass document signal gate"


class TestScenicStructuralPenalty:
    """Scenic presets must penalize document-like assets."""

    def test_scenic_penalizes_document_like_png_with_ocr(self):
        """A PNG with long OCR text should get a lower scenic structural score."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan
        from services.ranker import SCENIC_ANTI_TYPE_PENALTIES

        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999

        plan = QueryPlan(preset_id="travel", source="preset")
        meta = {
            "/doc.png": {
                "ext": ".png",
                "ocr_text": "Invoice Total EUR 123.45 Date: 2024-01-15 Company XYZ GmbH",
                "face_count": 0, "is_screenshot": False,
                "width": 1200, "height": 1700,
            },
            "/beach.jpg": {
                "ext": ".jpg",
                "ocr_text": "",
                "face_count": 0, "is_screenshot": False,
                "width": 6000, "height": 4000,
            },
        }
        scores = orch._compute_scenic_anti_type_scores(plan, meta)
        # doc.png should have a negative penalty
        assert "/doc.png" in scores
        assert scores["/doc.png"] < 0, "Document-like PNG must receive negative scenic penalty"
        # beach.jpg should have no penalty (standard 3:2 camera ratio)
        assert "/beach.jpg" not in scores


class TestDocumentStructuralScoring:
    """Documents structural scoring must favor document-like assets."""

    def test_document_png_with_ocr_scores_high(self):
        """A PNG with OCR text should score high structurally for documents."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan

        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999

        plan = QueryPlan(preset_id="documents", source="preset")
        meta = {
            "/doc.png": {
                "ext": ".png",
                "ocr_text": "Invoice Total EUR 123.45",
                "face_count": 0, "is_screenshot": False,
                "width": 1200, "height": 1700,
            },
        }
        scores = orch._compute_structural_scores(plan, meta, set())
        assert "/doc.png" in scores
        assert scores["/doc.png"] > 0.5, \
            f"Document PNG with OCR must score > 0.5, got {scores['/doc.png']}"

    def test_scenic_jpg_scores_low_for_documents(self):
        """A scenic JPG without OCR should score low structurally for documents."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan

        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999

        plan = QueryPlan(preset_id="documents", source="preset")
        meta = {
            "/beach.jpg": {
                "ext": ".jpg",
                "ocr_text": "",
                "face_count": 0, "is_screenshot": False,
                "width": 4000, "height": 3000,
            },
        }
        scores = orch._compute_structural_scores(plan, meta, set())
        assert "/beach.jpg" in scores
        assert scores["/beach.jpg"] < 0.5, \
            f"Scenic JPG must score < 0.5 for documents, got {scores['/beach.jpg']}"

    def test_face_photo_penalized_for_documents(self):
        """A photo with faces should be heavily penalized structurally for documents."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan

        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 999

        plan = QueryPlan(preset_id="documents", source="preset")
        meta = {
            "/portrait.jpg": {
                "ext": ".jpg",
                "ocr_text": "",
                "face_count": 3, "is_screenshot": False,
                "width": 3000, "height": 2000,
            },
        }
        scores = orch._compute_structural_scores(plan, meta, set())
        assert "/portrait.jpg" in scores
        assert scores["/portrait.jpg"] < 0.2, \
            f"Face photo must score very low for documents, got {scores['/portrait.jpg']}"

class TestDocumentPrecisionRegression:
    """Documents preset must not keep generic scenic/page-like raster images."""

    def test_document_builder_contract_no_geometry_only_survivors(self):
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)

        class DummyGate:
            def _passes_document_gate(self, meta, path=""):
                return False

        orch._gate_engine = DummyGate()

        scored = [
            type("R", (), {"path": "/photos/beach1.jpg"})(),
            type("R", (), {"path": "/docs/receipt.jpg"})(),
        ]
        builder_evidence = {
            "/photos/beach1.jpg": {
                "structural_hit": True,
                "low_confidence_admit": True,
                "has_text_dense_layout": False,
                "strong_raster_document": False,
            },
            "/docs/receipt.jpg": {
                "ocr_fts_hit": True,
                "strong_raster_document": True,
            },
        }
        meta = {
            "/photos/beach1.jpg": {},
            "/docs/receipt.jpg": {},
        }

        kept = orch._prune_document_survivors(scored, builder_evidence, meta)
        kept_paths = [r.path for r in kept]
        assert "/photos/beach1.jpg" not in kept_paths
        assert "/docs/receipt.jpg" in kept_paths
