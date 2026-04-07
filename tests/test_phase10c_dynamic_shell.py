# tests/test_phase10c_dynamic_shell.py
"""
Phase 10C dynamic shell features unit tests.

Tests:
- Dynamic Dates tree (set_date_years populates sidebar)
- Video classification branches (videos_all, videos_short, etc.)
- Similar Shots routing
- _sync_shell_date_tree helper
- MainWindow router docstring updated to Phase 10C

No PySide6 or display server required.

Run with:
    pytest tests/test_phase10c_dynamic_shell.py -v
    pytest tests/test_phase10c_dynamic_shell.py -v -m unit
"""

import sys
import types
import os
import ast
import textwrap
import pytest
from unittest.mock import MagicMock, patch, call
import functools


# ---------------------------------------------------------------------------
# Mock import bootstrap
# ---------------------------------------------------------------------------

class _MockImportFinder:
    def __init__(self):
        self._active = True

    def find_module(self, name, path=None):
        if not self._active or name in sys.modules:
            return None
        return self

    def load_module(self, name):
        if name in sys.modules:
            return sys.modules[name]
        self._active = False
        try:
            import importlib
            mod = importlib.import_module(name)
            return mod
        except Exception:
            mock_mod = MagicMock()
            mock_mod.__name__ = name
            mock_mod.__path__ = []
            mock_mod.__file__ = f"<mock:{name}>"
            mock_mod.__spec__ = None
            mock_mod.__all__ = []
            sys.modules[name] = mock_mod
            return mock_mod
        finally:
            self._active = True


import importlib
import importlib.util

_finder = _MockImportFinder()
sys.meta_path.insert(0, _finder)

_layouts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "layouts")

_layouts_pkg = types.ModuleType("layouts")
_layouts_pkg.__path__ = [_layouts_dir]
_layouts_pkg.__file__ = os.path.join(_layouts_dir, "__init__.py")
_layouts_pkg.__package__ = "layouts"
sys.modules["layouts"] = _layouts_pkg

_gl_spec = importlib.util.spec_from_file_location(
    "layouts.google_layout",
    os.path.join(_layouts_dir, "google_layout.py"),
    submodule_search_locations=[],
)
_gl_mod = importlib.util.module_from_spec(_gl_spec)
sys.modules["layouts.google_layout"] = _gl_mod
_gl_spec.loader.exec_module(_gl_mod)
GooglePhotosLayout = _gl_mod.GooglePhotosLayout

# Extract MainWindow method via AST
_mw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "main_window_qt.py")


def _extract_method_from_file(filepath, class_name, method_name):
    with open(filepath, "r") as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == method_name:
                        method_src = ast.get_source_segment(source, item)
                        if method_src:
                            method_src = textwrap.dedent(method_src)
                            local_ns = {}
                            exec(compile(method_src, filepath, "exec"), local_ns)
                            return local_ns[method_name]
    return None


_mw_search_branch_router = _extract_method_from_file(
    _mw_path, "MainWindow", "_handle_search_sidebar_branch_request"
)

sys.meta_path = [p for p in sys.meta_path if not isinstance(p, _MockImportFinder)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_layout(**overrides):
    """Build a mock GooglePhotosLayout with Phase 10C features."""
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()
    layout.google_shell_sidebar.set_shell_state_text = MagicMock()
    layout.google_shell_sidebar.set_date_years = MagicMock()

    # Bind real helpers
    layout._set_shell_active_branch = functools.partial(
        GooglePhotosLayout._set_shell_active_branch, layout
    )
    layout._is_legacy_section_retired = functools.partial(
        GooglePhotosLayout._is_legacy_section_retired, layout
    )
    layout._set_shell_state_text = functools.partial(
        GooglePhotosLayout._set_shell_state_text, layout
    )
    layout._set_view_mode = functools.partial(
        GooglePhotosLayout._set_view_mode, layout
    )
    layout._sync_shell_date_tree = functools.partial(
        GooglePhotosLayout._sync_shell_date_tree, layout
    )
    layout._current_view_mode = "all"
    layout._retired_legacy_sections = overrides.get(
        "_retired_legacy_sections",
        {"find", "devices", "videos", "locations", "duplicates"}
    )

    # Accordion sidebar mock
    layout.accordion_sidebar = MagicMock()
    layout.accordion_sidebar._expand_section = MagicMock()

    # Main window mock
    layout.main_window = MagicMock()

    # Deduplication state
    layout._last_passive_section = None
    layout._last_passive_section_ts = 0.0

    # Legacy tools group mock
    layout.legacy_tools_group = MagicMock()

    return layout


def _call_shell_branch(layout, branch):
    GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)


# ===========================================================================
# Test Class: Video classification branches
# ===========================================================================

@pytest.mark.unit
class TestVideoClassificationBranches:
    """Video classification branches should call _on_accordion_video_clicked with correct spec."""

    @pytest.mark.parametrize("branch,expected_spec", [
        ("videos_all", "all"),
        ("videos_short", "duration:short"),
        ("videos_medium", "duration:medium"),
        ("videos_long", "duration:long"),
        ("videos_hd", "resolution:hd"),
        ("videos_fhd", "resolution:fhd"),
        ("videos_4k", "resolution:4k"),
    ])
    def test_video_branch_calls_correct_filter(self, branch, expected_spec):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        layout._on_accordion_video_clicked.assert_called_once_with(expected_spec)

    @pytest.mark.parametrize("branch", [
        "videos_all", "videos_short", "videos_medium", "videos_long",
        "videos_hd", "videos_fhd", "videos_4k",
    ])
    def test_video_branch_sets_videos_mode(self, branch):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        assert layout._current_view_mode == "videos"

    @pytest.mark.parametrize("branch", [
        "videos_all", "videos_short", "videos_medium", "videos_long",
        "videos_hd", "videos_fhd", "videos_4k",
    ])
    def test_video_branch_sets_emphasis_false(self, branch):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    @pytest.mark.parametrize("branch", [
        "videos_all", "videos_short", "videos_medium", "videos_long",
        "videos_hd", "videos_fhd", "videos_4k",
    ])
    def test_video_branch_sets_active_branch(self, branch):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_active_branch.assert_called_with(branch)

    def test_video_branch_falls_back_to_load_photos(self):
        layout = _make_mock_layout()
        # No _on_accordion_video_clicked
        del layout._on_accordion_video_clicked
        _call_shell_branch(layout, "videos_all")
        layout._load_photos.assert_called()


# ===========================================================================
# Test Class: Similar Shots routing
# ===========================================================================

@pytest.mark.unit
class TestSimilarShotsRouting:
    """Similar Shots should set review mode and open duplicates dialog."""

    def test_similar_sets_review_mode(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        assert layout._current_view_mode == "review"

    def test_similar_sets_active_branch(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("similar_shots")

    def test_similar_sets_emphasis_false(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    def test_similar_opens_duplicates_dialog(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        layout._open_duplicates_dialog.assert_called_once()

    def test_similar_state_text(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "REVIEW \u2022 Similar shots review"
        )


# ===========================================================================
# Test Class: _sync_shell_date_tree
# ===========================================================================

@pytest.mark.unit
class TestSyncShellDateTree:
    """_sync_shell_date_tree should push date hierarchy to sidebar."""

    def test_sync_calls_set_date_years(self):
        layout = _make_mock_layout()
        layout.project_id = 1
        db = MagicMock()
        db.get_date_hierarchy.return_value = {
            "2025": {"01": ["01", "15"], "06": ["10"]},
            "2024": {"12": ["25"]},
        }
        layout.db = db
        layout._sync_shell_date_tree()
        layout.google_shell_sidebar.set_date_years.assert_called_once()
        args = layout.google_shell_sidebar.set_date_years.call_args[0][0]
        # Should be sorted newest first
        assert args[0][0] == 2025
        assert args[1][0] == 2024
        # Counts should reflect total days (not months)
        assert args[0][1] == 3  # 2025: 3 days total
        assert args[1][1] == 1  # 2024: 1 day total

    def test_sync_no_project_does_nothing(self):
        layout = _make_mock_layout()
        layout.project_id = None
        layout._sync_shell_date_tree()
        layout.google_shell_sidebar.set_date_years.assert_not_called()

    def test_sync_no_sidebar_does_not_crash(self):
        layout = _make_mock_layout()
        layout.project_id = 1
        layout.google_shell_sidebar = None
        layout._sync_shell_date_tree = functools.partial(
            GooglePhotosLayout._sync_shell_date_tree, layout
        )
        # Should not raise
        layout._sync_shell_date_tree()

    def test_sync_fallback_when_no_db(self):
        layout = _make_mock_layout()
        layout.project_id = 1
        layout.db = None
        layout.reference_db = None
        layout._sync_shell_date_tree()
        # Should still call set_date_years with fallback years
        layout.google_shell_sidebar.set_date_years.assert_called_once()
        args = layout.google_shell_sidebar.set_date_years.call_args[0][0]
        assert len(args) == 5  # 5 fallback years
        assert args[0][1] == 0  # No counts for fallback

    def test_sync_uses_reference_db_if_no_db(self):
        layout = _make_mock_layout()
        layout.project_id = 1
        layout.db = None
        ref_db = MagicMock()
        ref_db.get_date_hierarchy.return_value = {
            "2023": {"03": ["15"]},
        }
        layout.reference_db = ref_db
        layout._sync_shell_date_tree()
        ref_db.get_date_hierarchy.assert_called_once_with(1)


# ===========================================================================
# Test Class: MainWindow Phase 10C router
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase10CRouter:
    """Verify MainWindow router is Phase 10C labeled."""

    def test_router_has_phase_10c_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "10C" in doc or "10c" in doc

    def test_router_still_delegates_people(self):
        mw = MagicMock()
        mw._handle_people_branch = MagicMock()
        _mw_search_branch_router(mw, "people_tools")
        mw._handle_people_branch.assert_called_once_with("people_tools")

    def test_router_year_shortcuts_still_work(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "year_2025")
        layout.request_reload.assert_called_once_with(reason="year_filter", year=2025)

    def test_video_branches_route_to_layout(self):
        """Video classification branches should route through to layout."""
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "videos_short")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("videos_short")

    def test_similar_shots_routes_to_layout(self):
        """Similar shots branch should route through to layout."""
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "similar_shots")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("similar_shots")
