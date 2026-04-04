# tests/test_screenshot_enhancement_mocked.py
import pytest
import sys
import os
import threading
from unittest import mock

# Mock PySide6
_pyside_mock = mock.MagicMock()
sys.modules.setdefault('PySide6', _pyside_mock)
sys.modules.setdefault('PySide6.QtCore', _pyside_mock)
sys.modules.setdefault('PySide6.QtWidgets', _pyside_mock)
sys.modules.setdefault('PySide6.QtGui', _pyside_mock)

from services.candidate_builders.screenshot_candidate_builder import ScreenshotCandidateBuilder
from services.query_intent_planner import QueryIntent
from services.search_orchestrator import SearchOrchestrator, QueryPlan
from services.search_confidence_policy import SearchConfidencePolicy

class TestScreenshotEnhancement:
    def test_permissive_detection(self):
        builder = ScreenshotCandidateBuilder(project_id=1)
        intent = QueryIntent(preset_id="screenshots")

        # Test case: flat PNG UI (soft candidate)
        meta = {
            "path1.png": {
                "width": 1080, "height": 1920, "face_count": 0,
                "ocr_text": "battery settings", "is_screenshot": False
            }
        }
        cs = builder.build(intent, meta)
        assert "path1.png" in cs.candidate_paths
        assert cs.evidence_by_path["path1.png"]["ui_text_hit"] is True
        assert cs.evidence_by_path["path1.png"]["screenshot_score"] >= 0.20

    def test_rejection_reasons(self):
        builder = ScreenshotCandidateBuilder(project_id=1)
        intent = QueryIntent(preset_id="screenshots")

        meta = {
            "photo.jpg": {
                "width": 4000, "height": 3000, "face_count": 2, # face rejection
                "ocr_text": "", "is_screenshot": False
            },
            "tiny.png": {
                "width": 100, "height": 100, "face_count": 0, # too small
                "ocr_text": "", "is_screenshot": False
            }
        }
        cs = builder.build(intent, meta)
        assert len(cs.candidate_paths) == 0
        assert cs.diagnostics["rejections"]["has_faces"] == 1
        assert cs.diagnostics["rejections"]["too_small"] == 1

    @mock.patch("services.smart_find_service.get_smart_find_service")
    @mock.patch("services.query_intent_planner.get_query_intent_planner")
    def test_orchestrator_fusion_fallback(self, mock_get_planner, mock_get_sf):
        # Mock SmartFindService
        mock_sf = mock.Mock()
        mock_sf.clip_available = True
        mock_sf._get_config.return_value = {"threshold": 0.22, "fusion_mode": "max"}
        mock_sf._inflight_lock = threading.Lock()
        mock_sf._inflight_token = None

        # Supplemental hit
        mock_sf._run_clip_multi_prompt.return_value = {101: (0.25, "screenshot")}
        mock_sf._lookup_preset.return_value = {
            "name": "Screenshots",
            "prompts": ["screenshot"],
            "filters": {"_is_screenshot": True},
            "gate_profile": {"require_screenshot": True}
        }
        mock_sf._run_metadata_filter.return_value = []
        mock_get_sf.return_value = mock_sf

        # Mock Planner
        mock_planner = mock.Mock()
        mock_planner.plan.return_value = QueryIntent(preset_id="screenshots", family_hint="type")
        mock_get_planner.return_value = mock_planner

        orchestrator = SearchOrchestrator(project_id=1)

        # Mock metadata
        project_meta = {
            "path/to/sem_hit.jpg": {"id": 101, "face_count": 0, "width": 1000, "height": 1000}
        }
        orchestrator._get_project_meta = mock.Mock(return_value=project_meta)

        # Mock DB for path resolution
        with mock.patch("repository.base_repository.DatabaseConnection") as mock_db_conn:
            mock_conn = mock_db_conn.return_value.get_connection.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchall.return_value = [{"id": 101, "path": "path/to/sem_hit.jpg"}]

            # Execute search
            plan = orchestrator._plan_from_preset("screenshots", None)
            # Empty builder result to trigger supplement
            with mock.patch.object(orchestrator, "_build_candidate_set") as mock_build_cs:
                from services.candidate_builders.base_candidate_builder import CandidateSet
                mock_build_cs.return_value = CandidateSet(family="type", ready_state="empty")

                # We need to make it admissible for the test to pass
                # Let's add a signal to meta
                project_meta["path/to/sem_hit.jpg"]["is_screenshot"] = True

                result = orchestrator._execute(plan, top_k=10)

                assert "path/to/sem_hit.jpg" in result.paths

    def test_confidence_policy_soft_evidence(self):
        policy = SearchConfidencePolicy()
        intent = QueryIntent(preset_id="screenshots")

        # 2 hard hits (0.45)
        evidence = {
            "hard1": {"screenshot_score": 0.45, "is_screenshot_flag": True},
            "hard2": {"screenshot_score": 0.45, "ui_text_hit": True}
        }
        from services.candidate_builders.base_candidate_builder import CandidateSet
        cs = CandidateSet(family="type", candidate_paths=["hard1", "hard2"], evidence_by_path=evidence)

        from services.ranker import ScoredResult
        results = [
            ScoredResult(path="hard1", final_score=0.8, screenshot_score=0.45),
            ScoredResult(path="hard2", final_score=0.8, screenshot_score=0.45)
        ]

        decision = policy.evaluate(intent, cs, results, "type")
        # 2/2 hard hits = 1.0 ratio -> high
        assert decision.confidence_label == "high"

        # 1 hard hit + 1 soft hit (0.28 with soft signal)
        evidence_mix = {
            "hard": {"screenshot_score": 0.45, "is_screenshot_flag": True},
            "soft": {"screenshot_score": 0.28, "flat_ui_fallback": True}
        }
        cs_mix = CandidateSet(family="type", candidate_paths=["hard", "soft"], evidence_by_path=evidence_mix)
        results_mix = [
            ScoredResult(path="hard", final_score=0.8, screenshot_score=0.45),
            ScoredResult(path="soft", final_score=0.6, screenshot_score=0.28)
        ]
        decision_mix = policy.evaluate(intent, cs_mix, results_mix, "type")
        # Phase 2: hard=1, soft=1. effective = 1 + 0.5 * 1 = 1.5. ratio = 1.5 / 2 = 0.75 -> high (since >= 0.7)
        assert decision_mix.confidence_label == "high"
