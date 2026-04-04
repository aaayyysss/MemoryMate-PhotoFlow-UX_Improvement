"""
Tests for Phase 3 Advanced Search Features:
- Person cluster integration into search
- Entity graph
- Suggestion service
- Full query intent decomposition

All tests are unit-level: no database, no Qt, no CLIP required.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import ModuleType

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock PySide6 if not available (headless test environment)
if "PySide6" not in sys.modules:
    pyside6_mock = ModuleType("PySide6")
    pyside6_core = ModuleType("PySide6.QtCore")
    pyside6_core.QObject = MagicMock
    pyside6_core.Signal = MagicMock(return_value=MagicMock())
    pyside6_core.QTimer = MagicMock
    pyside6_mock.QtCore = pyside6_core
    sys.modules["PySide6"] = pyside6_mock
    sys.modules["PySide6.QtCore"] = pyside6_core


# ═══════════════════════════════════════════════════════════════════
# PersonSearchService Tests
# ═══════════════════════════════════════════════════════════════════

class TestPersonSearchService:
    """Tests for services.person_search_service."""

    def _make_service(self):
        from services.person_search_service import PersonSearchService
        svc = PersonSearchService(project_id=1)
        # Pre-populate cache to avoid DB calls
        svc._name_cache = {
            "john smith": ["face_001"],
            "sarah miller": ["face_002"],
            "baby emma": ["face_003"],
        }
        svc._branch_to_name = {
            "face_001": "John Smith",
            "face_002": "Sarah Miller",
            "face_003": "Baby Emma",
        }
        return svc

    def test_resolve_exact_name(self):
        svc = self._make_service()
        keys = svc.resolve_person_name("John Smith")
        assert keys == ["face_001"]

    def test_resolve_case_insensitive(self):
        svc = self._make_service()
        keys = svc.resolve_person_name("john smith")
        assert keys == ["face_001"]

    def test_resolve_prefix_match(self):
        svc = self._make_service()
        keys = svc.resolve_person_name("Joh")
        assert "face_001" in keys

    def test_resolve_branch_key_passthrough(self):
        svc = self._make_service()
        # branch_key in name map passes through
        svc._branch_to_name["face_001"] = "John Smith"
        keys = svc.resolve_person_name("face_001")
        assert keys == ["face_001"]

    def test_resolve_unknown_returns_empty(self):
        svc = self._make_service()
        keys = svc.resolve_person_name("Unknown Person")
        assert keys == []

    def test_extract_person_from_nl_photos_of(self):
        svc = self._make_service()
        keys, remaining = svc.extract_person_names_from_query("photos of John Smith")
        assert "face_001" in keys
        assert "John Smith" not in remaining

    def test_extract_no_false_positive_on_common_words(self):
        svc = self._make_service()
        keys, remaining = svc.extract_person_names_from_query("beach sunset photos")
        assert keys == []

    def test_invalidate_cache(self):
        svc = self._make_service()
        assert svc._name_cache is not None
        svc.invalidate_cache()
        assert svc._name_cache is None


# ═══════════════════════════════════════════════════════════════════
# EntityGraph Tests
# ═══════════════════════════════════════════════════════════════════

class TestEntityGraph:
    """Tests for services.entity_graph."""

    def _make_graph(self):
        from services.entity_graph import EntityGraph
        graph = EntityGraph(project_id=1)
        # Manually build links (bypass DB)
        graph._add_link("/photos/beach.jpg", "person", "face_001", "John")
        graph._add_link("/photos/beach.jpg", "location", "munich", "Munich")
        graph._add_link("/photos/beach.jpg", "date", "2024-06", "2024-06")
        graph._add_link("/photos/beach.jpg", "tag", "vacation", "Vacation")

        graph._add_link("/photos/party.jpg", "person", "face_001", "John")
        graph._add_link("/photos/party.jpg", "person", "face_002", "Sarah")
        graph._add_link("/photos/party.jpg", "date", "2024-06", "2024-06")

        graph._add_link("/photos/solo.jpg", "person", "face_002", "Sarah")
        graph._add_link("/photos/solo.jpg", "location", "berlin", "Berlin")
        graph._add_link("/photos/solo.jpg", "date", "2024-07", "2024-07")

        graph._built_at = 9999999999.0  # Prevent rebuild
        return graph

    def test_get_photo_entities(self):
        graph = self._make_graph()
        entities = graph.get_photo_entities("/photos/beach.jpg")
        types = {e.entity_type for e in entities}
        assert "person" in types
        assert "location" in types
        assert "date" in types
        assert "tag" in types

    def test_get_entity_photos(self):
        graph = self._make_graph()
        photos = graph.get_entity_photos("person", "face_001")
        assert "/photos/beach.jpg" in photos
        assert "/photos/party.jpg" in photos
        assert "/photos/solo.jpg" not in photos

    def test_get_entities_by_type(self):
        graph = self._make_graph()
        people = graph.get_entities_by_type("person")
        assert len(people) == 2
        # Should be sorted by photo_count
        assert people[0].photo_count >= people[1].photo_count

    def test_get_related_entities_person_to_location(self):
        graph = self._make_graph()
        edges = graph.get_related_entities("person", "face_001", "location")
        assert len(edges) == 1
        assert edges[0].target_id == "munich"
        assert edges[0].weight == 1

    def test_get_related_entities_person_to_person(self):
        graph = self._make_graph()
        edges = graph.get_related_entities("person", "face_001", "person")
        assert len(edges) == 1
        assert edges[0].target_id == "face_002"

    def test_co_occurrence(self):
        graph = self._make_graph()
        count = graph.co_occurrence("person", "face_001", "person", "face_002")
        assert count == 1  # party.jpg

    def test_co_occurrence_zero(self):
        graph = self._make_graph()
        count = graph.co_occurrence("person", "face_001", "location", "berlin")
        assert count == 0

    def test_search_entities(self):
        graph = self._make_graph()
        results = graph.search_entities("Joh")
        assert len(results) == 1
        assert results[0].display_name == "John"

    def test_search_entities_substring(self):
        graph = self._make_graph()
        results = graph.search_entities("uni", entity_types=["location"])
        assert len(results) == 1
        assert results[0].display_name == "Munich"

    def test_get_entity_context(self):
        graph = self._make_graph()
        context = graph.get_entity_context("person", "face_001")
        assert "location" in context
        assert "date" in context

    def test_invalidate(self):
        graph = self._make_graph()
        graph.invalidate()
        assert graph._built_at == 0.0


# ═══════════════════════════════════════════════════════════════════
# SuggestionService Tests
# ═══════════════════════════════════════════════════════════════════

class TestSuggestionService:
    """Tests for services.suggestion_service."""

    def _make_service(self):
        from services.suggestion_service import SuggestionService
        svc = SuggestionService(project_id=1)
        return svc

    def test_token_completion_type(self):
        svc = self._make_service()
        suggestions = svc._suggest_tokens("type:")
        assert len(suggestions) > 0
        texts = [s.text for s in suggestions]
        assert "type:video" in texts
        assert "type:photo" in texts

    def test_token_completion_partial(self):
        svc = self._make_service()
        suggestions = svc._suggest_tokens("type:v")
        texts = [s.text for s in suggestions]
        assert "type:video" in texts

    def test_token_prefix_suggestion(self):
        svc = self._make_service()
        suggestions = svc._suggest_tokens("ty")
        texts = [s.text for s in suggestions]
        assert "type:" in texts

    def test_token_completion_date(self):
        svc = self._make_service()
        suggestions = svc._suggest_tokens("date:")
        assert len(suggestions) > 0
        texts = [s.text for s in suggestions]
        assert "date:today" in texts

    def test_token_completion_has(self):
        svc = self._make_service()
        suggestions = svc._suggest_tokens("has:")
        texts = [s.text for s in suggestions]
        assert "has:location" in texts

    def test_format_output(self):
        from services.suggestion_service import Suggestion
        svc = self._make_service()
        results = svc._format([
            Suggestion("test", "test text", "icon", "action", 0.5)
        ])
        assert len(results) == 1
        assert results[0]["type"] == "test"
        assert results[0]["text"] == "test text"
        assert results[0]["action"] == "action"

    def test_empty_suggestions(self):
        svc = self._make_service()
        results = svc.suggest("")
        # Should return some default suggestions
        assert isinstance(results, list)

    def test_preset_suggestions(self):
        svc = self._make_service()
        suggestions = svc._suggest_presets("bea")
        # Should match "Beach" preset
        texts = [s.text for s in suggestions]
        assert any("Beach" in t for t in texts)


# ═══════════════════════════════════════════════════════════════════
# QueryIntentService Tests
# ═══════════════════════════════════════════════════════════════════

class TestQueryIntentService:
    """Tests for services.query_intent_service."""

    def _make_service(self):
        from services.query_intent_service import QueryIntentService
        svc = QueryIntentService(project_id=1)
        # Disable person service to avoid DB calls
        svc._person_service = MagicMock()
        svc._person_service.extract_person_names_from_query.return_value = ([], "")
        svc._person_service.resolve_person_name.return_value = []
        return svc

    def test_extract_quality_best(self):
        svc = self._make_service()
        intent = svc.decompose("best sunset photos")
        assert intent.quality_filter == "best"
        assert intent.rating_min == 4

    def test_extract_quality_favorite(self):
        svc = self._make_service()
        intent = svc.decompose("my favorite beach photos")
        assert intent.quality_filter == "favorite"

    def test_extract_media_type_photo(self):
        svc = self._make_service()
        intent = svc.decompose("sunset photos")
        assert intent.media_type == "photo"

    def test_extract_media_type_video(self):
        svc = self._make_service()
        intent = svc.decompose("holiday videos")
        assert intent.media_type == "video"

    def test_extract_negation_no_screenshots(self):
        svc = self._make_service()
        intent = svc.decompose("documents without screenshots")
        assert intent.exclude_screenshots is True

    def test_extract_negation_no_people(self):
        svc = self._make_service()
        intent = svc.decompose("landscape without people")
        assert intent.exclude_people is True

    def test_extract_photos_only(self):
        svc = self._make_service()
        intent = svc.decompose("only photos of sunset")
        assert intent.media_type == "photo"

    def test_extract_temporal_last_summer(self):
        svc = self._make_service()
        intent = svc.decompose("beach photos from last summer")
        assert intent.date_from is not None
        assert intent.date_to is not None
        assert intent.temporal_label == "last summer"
        # Check it covers June-August
        assert "-06-" in intent.date_from
        assert "-08-" in intent.date_to

    def test_extract_temporal_christmas(self):
        svc = self._make_service()
        intent = svc.decompose("Christmas 2024 photos")
        assert intent.date_from is not None
        assert "2024-12-" in intent.date_from
        assert intent.temporal_label == "christmas 2024"

    def test_extract_temporal_year(self):
        svc = self._make_service()
        intent = svc.decompose("photos from 2023")
        assert intent.date_from == "2023-01-01"
        assert intent.date_to == "2023-12-31"

    def test_extract_temporal_month(self):
        svc = self._make_service()
        intent = svc.decompose("June 2024 photos")
        assert intent.date_from == "2024-06-01"
        assert intent.date_to == "2024-06-30"

    def test_extract_temporal_last_n_days(self):
        svc = self._make_service()
        intent = svc.decompose("last 30 days photos")
        assert intent.date_from is not None
        assert intent.temporal_label == "last 30 days"

    def test_extract_quantity_recent(self):
        svc = self._make_service()
        intent = svc.decompose("recent 20 photos")
        assert intent.limit_count == 20
        assert intent.sort_preference == "recent"

    def test_extract_gps_requirement(self):
        svc = self._make_service()
        intent = svc.decompose("photos with location")
        assert intent.require_gps is True

    def test_semantic_text_remainder(self):
        svc = self._make_service()
        intent = svc.decompose("best sunset photos from 2024")
        # After extracting quality, media type, and year,
        # "sunset" should remain as semantic text
        assert "sunset" in intent.semantic_text

    def test_to_query_plan_overrides(self):
        svc = self._make_service()
        intent = svc.decompose("best sunset photos from 2024")
        overrides = intent.to_query_plan_overrides()
        assert "filters" in overrides
        filters = overrides["filters"]
        assert filters.get("date_from") == "2024-01-01"
        assert filters.get("rating_min") == 4

    def test_compound_negation_and_quality(self):
        svc = self._make_service()
        intent = svc.decompose("best photos without screenshots from 2023")
        assert intent.quality_filter == "best"
        assert intent.exclude_screenshots is True
        assert intent.date_from == "2023-01-01"

    def test_empty_query(self):
        svc = self._make_service()
        intent = svc.decompose("")
        assert intent.semantic_text == ""
        assert len(intent.intents_found) == 0

    def test_simple_semantic_query(self):
        svc = self._make_service()
        intent = svc.decompose("sunset")
        assert "sunset" in intent.semantic_text


# ═══════════════════════════════════════════════════════════════════
# Integration: Orchestrator Phase 3 wiring
# ═══════════════════════════════════════════════════════════════════

class TestOrchestratorPhase3Integration:
    """Test that Phase 3 services are correctly wired into the orchestrator."""

    def test_orchestrator_has_phase3_properties(self):
        """Verify the orchestrator exposes Phase 3 service properties."""
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 1
        orch._person_search = None
        orch._entity_graph = None
        orch._suggestion_svc = None
        orch._intent_svc = None

        # Properties should exist and not crash when services are unavailable
        assert hasattr(orch, '_person_search_svc')
        assert hasattr(orch, '_entity_graph_svc')
        assert hasattr(orch, '_suggestion_service')
        assert hasattr(orch, '_query_intent_svc')

    def test_get_suggestions_returns_list(self):
        """Verify get_suggestions returns a list even when service is unavailable."""
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 1
        orch._suggestion_svc = None
        result = orch.get_suggestions("test")
        assert isinstance(result, list)

    def test_get_photo_entities_returns_list(self):
        """Verify get_photo_entities returns a list even when service is unavailable."""
        from services.search_orchestrator import SearchOrchestrator
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 1
        orch._entity_graph = None
        result = orch.get_photo_entities("/photos/test.jpg")
        assert isinstance(result, list)

    def test_resolve_person_filters_noop_without_person(self):
        """Verify _resolve_person_filters is a no-op when no person filter."""
        from services.search_orchestrator import SearchOrchestrator, QueryPlan
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 1
        orch._person_search = None
        plan = QueryPlan(raw_query="beach sunset")
        result = orch._resolve_person_filters(plan)
        assert "_person_paths" not in result.filters

    def test_enrich_person_facet_noop_without_service(self):
        """Verify _enrich_person_facet is a no-op without PersonSearchService."""
        from services.search_orchestrator import SearchOrchestrator, OrchestratorResult
        orch = SearchOrchestrator.__new__(SearchOrchestrator)
        orch.project_id = 1
        orch._person_search = None
        result = OrchestratorResult(paths=["/a.jpg"] * 10)
        orch._enrich_person_facet(result)
        assert "people" not in result.facets


# ═══════════════════════════════════════════════════════════════════
# Module import smoke tests
# ═══════════════════════════════════════════════════════════════════

class TestPhase3Imports:
    """Verify all Phase 3 modules import cleanly."""

    def test_import_person_search_service(self):
        from services.person_search_service import PersonSearchService
        assert PersonSearchService is not None

    def test_import_entity_graph(self):
        from services.entity_graph import EntityGraph, get_entity_graph
        assert EntityGraph is not None
        assert get_entity_graph is not None

    def test_import_suggestion_service(self):
        from services.suggestion_service import SuggestionService, get_suggestion_service
        assert SuggestionService is not None
        assert get_suggestion_service is not None

    def test_import_query_intent_service(self):
        from services.query_intent_service import QueryIntentService, get_query_intent_service
        assert QueryIntentService is not None
        assert get_query_intent_service is not None

    def test_import_entity_node_dataclass(self):
        from services.entity_graph import EntityNode
        node = EntityNode(
            entity_type="person", entity_id="face_001",
            display_name="John", photo_count=5,
        )
        assert node.display_name == "John"

    def test_import_query_intent_dataclass(self):
        from services.query_intent_service import QueryIntent
        intent = QueryIntent(raw_query="test")
        assert intent.raw_query == "test"
        assert intent.semantic_text == ""

    def test_import_suggestion_dataclass(self):
        from services.suggestion_service import Suggestion
        s = Suggestion("test", "test text", score=0.5)
        assert s.type == "test"
        assert s.score == 0.5
