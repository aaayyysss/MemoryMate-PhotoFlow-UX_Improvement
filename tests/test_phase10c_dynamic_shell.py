# tests/test_phase10c_dynamic_shell.py
"""
Phase 10C fix pack dynamic shell features unit tests.

Tests:
- Dynamic Dates tree (QTreeWidget-based set_date_tree)
- Video classification branches (renamed: videos_duration_*, videos_resolution_*)
- Similar Shots routing (now uses _on_find_similar_photos)
- Duplicates routing
- _sync_shell_date_tree / _sync_shell_folder_tree / _sync_shell_location_tree
- Dynamic folder_id: and location_name: and month_ routing
- MainWindow router (simplified, no year_ special case)

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
    """Build a mock GooglePhotosLayout with Phase 10C fix features."""
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()
    layout.google_shell_sidebar.set_shell_state_text = MagicMock()
    layout.google_shell_sidebar.set_date_tree = MagicMock()
    layout.google_shell_sidebar.set_date_years = MagicMock()
    layout.google_shell_sidebar.set_folder_tree = MagicMock()
    layout.google_shell_sidebar.set_location_tree = MagicMock()

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
    layout._sync_shell_folder_tree = functools.partial(
        GooglePhotosLayout._sync_shell_folder_tree, layout
    )
    layout._sync_shell_location_tree = functools.partial(
        GooglePhotosLayout._sync_shell_location_tree, layout
    )
    layout._current_view_mode = "all"
    layout._retired_legacy_sections = overrides.get(
        "_retired_legacy_sections",
        {"find", "devices", "videos", "locations", "duplicates"}
    )

    # Accordion sidebar mock
    layout.accordion_sidebar = MagicMock()
    layout.accordion_sidebar._expand_section = MagicMock()
    layout.accordion_sidebar.section_logic = {}

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
# Test Class: Video classification branches (renamed)
# ===========================================================================

@pytest.mark.unit
class TestVideoClassificationBranches:
    """Video classification branches with new naming convention."""

    @pytest.mark.parametrize("branch,expected_spec", [
        ("videos", "all"),
        ("videos_duration_short", "duration:short"),
        ("videos_duration_medium", "duration:medium"),
        ("videos_duration_long", "duration:long"),
        ("videos_resolution_hd", "resolution:hd"),
        ("videos_resolution_fhd", "resolution:fhd"),
        ("videos_resolution_4k", "resolution:4k"),
    ])
    def test_video_branch_calls_correct_filter(self, branch, expected_spec):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        layout._on_accordion_video_clicked.assert_called_once_with(expected_spec)

    @pytest.mark.parametrize("branch", [
        "videos", "videos_duration_short", "videos_duration_medium",
        "videos_duration_long", "videos_resolution_hd",
        "videos_resolution_fhd", "videos_resolution_4k",
    ])
    def test_video_branch_sets_videos_mode(self, branch):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        assert layout._current_view_mode == "videos"

    @pytest.mark.parametrize("branch", [
        "videos_duration_short", "videos_duration_medium", "videos_duration_long",
    ])
    def test_duration_branch_state_text(self, branch):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        # State text should contain VIDEOS
        calls = layout.google_shell_sidebar.set_shell_state_text.call_args_list
        assert any("VIDEOS" in str(c) for c in calls)

    @pytest.mark.parametrize("branch", [
        "videos_resolution_hd", "videos_resolution_fhd", "videos_resolution_4k",
    ])
    def test_resolution_branch_state_text(self, branch):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, branch)
        calls = layout.google_shell_sidebar.set_shell_state_text.call_args_list
        assert any("VIDEOS" in str(c) for c in calls)


# ===========================================================================
# Test Class: Duplicates routing
# ===========================================================================

@pytest.mark.unit
class TestDuplicatesRouting:
    """Duplicates branch should set review mode and open duplicates dialog."""

    def test_duplicates_sets_review_mode(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "duplicates")
        assert layout._current_view_mode == "review"

    def test_duplicates_opens_dialog(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "duplicates")
        layout._open_duplicates_dialog.assert_called_once()

    def test_duplicates_state_text(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "duplicates")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "REVIEW \u2022 Duplicates"
        )


# ===========================================================================
# Test Class: Similar Shots routing
# ===========================================================================

@pytest.mark.unit
class TestSimilarShotsRouting:
    """Similar Shots should set review mode and call _on_find_similar_photos."""

    def test_similar_sets_review_mode(self):
        layout = _make_mock_layout()
        layout.main_window._on_find_similar_photos = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        assert layout._current_view_mode == "review"

    def test_similar_calls_find_similar(self):
        layout = _make_mock_layout()
        layout.main_window._on_find_similar_photos = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        layout.main_window._on_find_similar_photos.assert_called_once()

    def test_similar_state_text(self):
        layout = _make_mock_layout()
        layout.main_window._on_find_similar_photos = MagicMock()
        _call_shell_branch(layout, "similar_shots")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "REVIEW \u2022 Similar shots"
        )


# ===========================================================================
# Test Class: Dynamic year/month routing
# ===========================================================================

@pytest.mark.unit
class TestDynamicDateRouting:
    """year_ and month_ branches should route to _on_accordion_date_clicked."""

    def test_year_routes_to_date_clicked(self):
        layout = _make_mock_layout()
        layout._on_accordion_date_clicked = MagicMock()
        _call_shell_branch(layout, "year_2025")
        layout._on_accordion_date_clicked.assert_called_once_with("2025")

    def test_year_sets_all_mode(self):
        layout = _make_mock_layout()
        layout._on_accordion_date_clicked = MagicMock()
        _call_shell_branch(layout, "year_2024")
        assert layout._current_view_mode == "all"

    def test_year_state_text(self):
        layout = _make_mock_layout()
        layout._on_accordion_date_clicked = MagicMock()
        _call_shell_branch(layout, "year_2025")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 Year \u2022 2025"
        )

    def test_month_routes_to_date_clicked(self):
        layout = _make_mock_layout()
        layout._on_accordion_date_clicked = MagicMock()
        _call_shell_branch(layout, "month_2025-06")
        layout._on_accordion_date_clicked.assert_called_once_with("2025-06")

    def test_month_sets_all_mode(self):
        layout = _make_mock_layout()
        layout._on_accordion_date_clicked = MagicMock()
        _call_shell_branch(layout, "month_2025-06")
        assert layout._current_view_mode == "all"

    def test_month_state_text(self):
        layout = _make_mock_layout()
        layout._on_accordion_date_clicked = MagicMock()
        _call_shell_branch(layout, "month_2025-06")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 Month \u2022 2025-06"
        )


# ===========================================================================
# Test Class: Dynamic folder routing
# ===========================================================================

@pytest.mark.unit
class TestDynamicFolderRouting:
    """folder_id: branches should route to _execute_folder_click."""

    def test_folder_id_sets_pending_and_executes(self):
        layout = _make_mock_layout()
        layout._execute_folder_click = MagicMock()
        _call_shell_branch(layout, "folder_id:42")
        assert layout._pending_folder_id == 42
        layout._execute_folder_click.assert_called_once()

    def test_folder_id_invalid_does_not_crash(self):
        layout = _make_mock_layout()
        layout._execute_folder_click = MagicMock(side_effect=Exception("boom"))
        # Should not raise
        _call_shell_branch(layout, "folder_id:abc")


# ===========================================================================
# Test Class: Dynamic location routing
# ===========================================================================

@pytest.mark.unit
class TestDynamicLocationRouting:
    """location_name: branches should route to _on_accordion_location_clicked."""

    def test_location_name_sets_locations_mode(self):
        layout = _make_mock_layout()
        loc_section = MagicMock()
        loc_section.location_clusters = [
            {"name": "Paris", "count": 5, "paths": ["/a.jpg"]},
        ]
        layout.accordion_sidebar.section_logic = {"locations": loc_section}
        layout._on_accordion_location_clicked = MagicMock()
        _call_shell_branch(layout, "location_name:Paris")
        assert layout._current_view_mode == "locations"

    def test_location_name_calls_location_clicked(self):
        layout = _make_mock_layout()
        loc_section = MagicMock()
        loc_section.location_clusters = [
            {"name": "Paris", "count": 5, "paths": ["/a.jpg"]},
        ]
        layout.accordion_sidebar.section_logic = {"locations": loc_section}
        layout._on_accordion_location_clicked = MagicMock()
        _call_shell_branch(layout, "location_name:Paris")
        layout._on_accordion_location_clicked.assert_called_once_with(
            {"name": "Paris", "count": 5, "paths": ["/a.jpg"]}
        )

    def test_location_name_state_text(self):
        layout = _make_mock_layout()
        loc_section = MagicMock()
        loc_section.location_clusters = [
            {"name": "Tokyo", "count": 3, "paths": ["/b.jpg"]},
        ]
        layout.accordion_sidebar.section_logic = {"locations": loc_section}
        layout._on_accordion_location_clicked = MagicMock()
        _call_shell_branch(layout, "location_name:Tokyo")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "LOCATIONS \u2022 Location \u2022 Tokyo"
        )

    def test_location_name_not_found_does_not_crash(self):
        layout = _make_mock_layout()
        loc_section = MagicMock()
        loc_section.location_clusters = []
        layout.accordion_sidebar.section_logic = {"locations": loc_section}
        # Should not raise
        _call_shell_branch(layout, "location_name:Nowhere")


# ===========================================================================
# Test Class: _sync_shell_date_tree (fix pack version)
# ===========================================================================

@pytest.mark.unit
class TestSyncShellDateTree:
    """_sync_shell_date_tree should read from accordion section_logic."""

    def test_sync_calls_set_date_tree(self):
        layout = _make_mock_layout()
        dates_section = MagicMock()
        dates_section.years_data = {
            2025: [{"month": 1}, {"month": 6}],
            2024: [{"month": 12}],
        }
        layout.accordion_sidebar.section_logic = {"dates": dates_section}
        layout._sync_shell_date_tree()
        layout.google_shell_sidebar.set_date_tree.assert_called_once()

    def test_sync_payload_structure(self):
        layout = _make_mock_layout()
        dates_section = MagicMock()
        dates_section.years_data = {
            2025: [{"month": 3}],
        }
        layout.accordion_sidebar.section_logic = {"dates": dates_section}
        layout._sync_shell_date_tree()
        payload = layout.google_shell_sidebar.set_date_tree.call_args[0][0]
        assert len(payload) == 1
        assert payload[0]["label"] == "2025"
        assert payload[0]["value"] == "2025"
        assert len(payload[0]["months"]) == 1
        assert payload[0]["months"][0]["value"] == "2025-03"

    def test_sync_no_sidebar_does_not_crash(self):
        layout = _make_mock_layout()
        layout.google_shell_sidebar = None
        layout._sync_shell_date_tree = functools.partial(
            GooglePhotosLayout._sync_shell_date_tree, layout
        )
        layout._sync_shell_date_tree()

    def test_sync_empty_section_logic(self):
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        layout._sync_shell_date_tree()
        layout.google_shell_sidebar.set_date_tree.assert_called_once_with([])


# ===========================================================================
# Test Class: _sync_shell_folder_tree
# ===========================================================================

@pytest.mark.unit
class TestSyncShellFolderTree:
    """_sync_shell_folder_tree should read from accordion section_logic."""

    def test_sync_calls_set_folder_tree(self):
        layout = _make_mock_layout()
        folders_section = MagicMock()
        folders_section._folder_tree_data = [
            {"label": "Photos", "id": 1, "children": []}
        ]
        layout.accordion_sidebar.section_logic = {"folders": folders_section}
        layout._sync_shell_folder_tree()
        layout.google_shell_sidebar.set_folder_tree.assert_called_once_with(
            [{"label": "Photos", "id": 1, "children": []}]
        )

    def test_sync_no_sidebar_does_not_crash(self):
        layout = _make_mock_layout()
        layout.google_shell_sidebar = None
        layout._sync_shell_folder_tree = functools.partial(
            GooglePhotosLayout._sync_shell_folder_tree, layout
        )
        layout._sync_shell_folder_tree()

    def test_sync_empty_section_logic(self):
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        layout._sync_shell_folder_tree()
        layout.google_shell_sidebar.set_folder_tree.assert_called_once_with([])


# ===========================================================================
# Test Class: _sync_shell_location_tree
# ===========================================================================

@pytest.mark.unit
class TestSyncShellLocationTree:
    """_sync_shell_location_tree should read from accordion section_logic."""

    def test_sync_calls_set_location_tree(self):
        layout = _make_mock_layout()
        loc_section = MagicMock()
        loc_section.location_clusters = [
            {"name": "Paris", "count": 10},
            {"name": "Tokyo", "count": 5},
        ]
        layout.accordion_sidebar.section_logic = {"locations": loc_section}
        layout._sync_shell_location_tree()
        payload = layout.google_shell_sidebar.set_location_tree.call_args[0][0]
        assert len(payload) == 2
        assert payload[0]["label"] == "Paris"
        assert payload[1]["label"] == "Tokyo"

    def test_sync_no_sidebar_does_not_crash(self):
        layout = _make_mock_layout()
        layout.google_shell_sidebar = None
        layout._sync_shell_location_tree = functools.partial(
            GooglePhotosLayout._sync_shell_location_tree, layout
        )
        layout._sync_shell_location_tree()

    def test_sync_empty_section_logic(self):
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        layout._sync_shell_location_tree()
        layout.google_shell_sidebar.set_location_tree.assert_called_once_with([])


# ===========================================================================
# Test Class: MainWindow Phase 10C router (simplified)
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase10CRouter:
    """Verify MainWindow router is Phase 10C labeled and simplified."""

    def test_router_has_phase_10c_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "10C" in doc or "10c" in doc

    def test_router_still_delegates_people(self):
        mw = MagicMock()
        mw._handle_people_branch = MagicMock()
        _mw_search_branch_router(mw, "people_tools")
        mw._handle_people_branch.assert_called_once_with("people_tools")

    def test_router_year_routes_to_layout(self):
        """year_ branches now route through layout, not MW special case."""
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "year_2025")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("year_2025")

    def test_video_branches_route_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "videos_duration_short")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("videos_duration_short")

    def test_similar_shots_routes_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "similar_shots")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("similar_shots")

    def test_folder_id_routes_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "folder_id:42")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("folder_id:42")

    def test_location_name_routes_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "location_name:Paris")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("location_name:Paris")

    def test_month_routes_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "month_2025-06")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("month_2025-06")
