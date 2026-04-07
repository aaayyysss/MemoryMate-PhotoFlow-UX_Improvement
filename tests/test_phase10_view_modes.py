# tests/test_phase10_view_modes.py
"""
Phase 10 shell-native result surfaces & view mode tests.

Tests:
- View mode state (_current_view_mode, _set_view_mode)
- Retired sections set correct view modes (search, videos, locations, review, devices)
- All Photos sets view mode "all"
- View mode state text format: "MODE \u2022 description"
- Shell-level year shortcuts in MainWindow router
- Dates Overview quick-access buttons exist in sidebar
- _clear_shell_state_text now sets "Ready"
- MainWindow router docstring updated to Phase 10

No PySide6 or display server required.

Run with:
    pytest tests/test_phase10_view_modes.py -v
    pytest tests/test_phase10_view_modes.py -v -m unit
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
    """Build a mock GooglePhotosLayout with Phase 10 view modes."""
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_project_available = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()
    layout.google_shell_sidebar.set_shell_state_text = MagicMock()
    layout.google_shell_sidebar.clear_shell_state_text = MagicMock()

    # Bind real helper methods
    layout._set_shell_active_branch = functools.partial(
        GooglePhotosLayout._set_shell_active_branch, layout
    )
    layout._clear_shell_active_branch = functools.partial(
        GooglePhotosLayout._clear_shell_active_branch, layout
    )
    layout._is_legacy_section_retired = functools.partial(
        GooglePhotosLayout._is_legacy_section_retired, layout
    )
    layout._refresh_legacy_visibility_state = functools.partial(
        GooglePhotosLayout._refresh_legacy_visibility_state, layout
    )
    layout._set_shell_state_text = functools.partial(
        GooglePhotosLayout._set_shell_state_text, layout
    )
    layout._clear_shell_state_text = functools.partial(
        GooglePhotosLayout._clear_shell_state_text, layout
    )
    layout._set_view_mode = functools.partial(
        GooglePhotosLayout._set_view_mode, layout
    )

    # View mode state
    layout._current_view_mode = "all"

    # Retired sections
    layout._retired_legacy_sections = overrides.get(
        "_retired_legacy_sections",
        {"find", "devices", "videos", "locations", "duplicates"}
    )

    # Accordion sidebar mock
    layout.accordion_sidebar = MagicMock()
    layout.accordion_sidebar._expand_section = MagicMock()

    # Main window mock
    layout.main_window = MagicMock()
    layout.main_window.search_bar = MagicMock()
    layout.main_window.top_search_bar = MagicMock()

    # Deduplication state
    layout._last_passive_section = None
    layout._last_passive_section_ts = 0.0

    # Legacy tools group mock
    layout.legacy_tools_group = MagicMock()

    return layout


def _call_shell_branch(layout, branch):
    GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)


# ===========================================================================
# Test Class: _set_view_mode
# ===========================================================================

@pytest.mark.unit
class TestSetViewMode:
    """_set_view_mode should update state and format state text."""

    def test_sets_current_view_mode(self):
        layout = _make_mock_layout()
        layout._set_view_mode("videos", "Showing video files")
        assert layout._current_view_mode == "videos"

    def test_state_text_with_description(self):
        layout = _make_mock_layout()
        layout._set_view_mode("search", "Type to search")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "SEARCH \u2022 Type to search"
        )

    def test_state_text_without_description(self):
        layout = _make_mock_layout()
        layout._set_view_mode("videos")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "VIDEOS view"
        )

    def test_all_mode(self):
        layout = _make_mock_layout()
        layout._set_view_mode("all", "All photos")
        assert layout._current_view_mode == "all"
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 All photos"
        )


# ===========================================================================
# Test Class: Find sets search mode
# ===========================================================================

@pytest.mark.unit
class TestFindSearchMode:
    """Find should set view mode to 'search'."""

    def test_find_sets_search_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "find")
        assert layout._current_view_mode == "search"

    def test_find_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "find")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "SEARCH \u2022 Type to search your library"
        )

    def test_find_focuses_top_search_bar(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "find")
        layout.main_window.top_search_bar.setFocus.assert_called_once()

    def test_find_falls_back_to_search_bar(self):
        layout = _make_mock_layout()
        layout.main_window.top_search_bar = None
        _call_shell_branch(layout, "find")
        layout.main_window.search_bar.setFocus.assert_called_once()

    def test_find_falls_back_to_accordion_when_no_search_bars(self):
        layout = _make_mock_layout()
        layout.main_window.top_search_bar = None
        layout.main_window.search_bar = None
        _call_shell_branch(layout, "find")
        layout.accordion_sidebar._expand_section.assert_called_once_with("find")

    def test_videos_calls_real_video_filter(self):
        """Phase 10B: Videos must use _on_accordion_video_clicked, not request_reload."""
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, "videos")
        layout._on_accordion_video_clicked.assert_called_once_with("all")

    def test_videos_falls_back_to_load_photos(self):
        """If _on_accordion_video_clicked is missing, fall back to _load_photos."""
        layout = _make_mock_layout()
        # Remove the video click method
        del layout._on_accordion_video_clicked
        _call_shell_branch(layout, "videos")
        layout._load_photos.assert_called()


# ===========================================================================
# Test Class: Videos sets videos mode
# ===========================================================================

@pytest.mark.unit
class TestVideosMode:
    """Videos should set view mode to 'videos'."""

    def test_videos_sets_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "videos")
        assert layout._current_view_mode == "videos"

    def test_videos_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "videos")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "VIDEOS \u2022 Showing video files"
        )

    def test_videos_requests_real_video_filter(self):
        layout = _make_mock_layout()
        layout._on_accordion_video_clicked = MagicMock()
        _call_shell_branch(layout, "videos")
        layout._on_accordion_video_clicked.assert_called_once_with("all")


# ===========================================================================
# Test Class: Locations sets locations mode
# ===========================================================================

@pytest.mark.unit
class TestLocationsMode:
    """Locations should set view mode to 'locations'."""

    def test_locations_sets_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "locations")
        assert layout._current_view_mode == "locations"

    def test_locations_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "locations")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "LOCATIONS \u2022 Pick a location cluster below"
        )

    def test_locations_expands_accordion(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "locations")
        layout.accordion_sidebar._expand_section.assert_called_once_with("locations")


# ===========================================================================
# Test Class: Duplicates sets review mode
# ===========================================================================

@pytest.mark.unit
class TestDuplicatesReviewMode:
    """Duplicates should set view mode to 'review'."""

    def test_duplicates_sets_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "duplicates")
        assert layout._current_view_mode == "review"

    def test_duplicates_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "duplicates")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "REVIEW \u2022 Duplicates & similar shots"
        )

    def test_duplicates_opens_dialog(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "duplicates")
        layout._open_duplicates_dialog.assert_called_once()


# ===========================================================================
# Test Class: Devices sets devices mode
# ===========================================================================

@pytest.mark.unit
class TestDevicesMode:
    """Devices should set view mode to 'devices'."""

    def test_devices_sets_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "devices")
        assert layout._current_view_mode == "devices"

    def test_devices_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "devices")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "DEVICES \u2022 Pick a device source below"
        )

    def test_devices_expands_accordion(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "devices")
        layout.accordion_sidebar._expand_section.assert_called_once_with("devices")


# ===========================================================================
# Test Class: All Photos sets all mode
# ===========================================================================

@pytest.mark.unit
class TestAllPhotosMode:
    """All Photos should set view mode to 'all'."""

    def test_all_sets_mode(self):
        layout = _make_mock_layout()
        layout.project_id = "test-project"
        _call_shell_branch(layout, "all")
        assert layout._current_view_mode == "all"

    def test_all_state_text(self):
        layout = _make_mock_layout()
        layout.project_id = "test-project"
        _call_shell_branch(layout, "all")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 All photos"
        )


# ===========================================================================
# Test Class: Discover presets use search mode
# ===========================================================================

@pytest.mark.unit
class TestDiscoverPresetsMode:
    """Discover presets should set view mode to 'search'."""

    @pytest.mark.parametrize("branch,preset_name", [
        ("discover_beach", "Beach"),
        ("discover_mountains", "Mountains"),
        ("discover_city", "City"),
    ])
    def test_discover_sets_search_mode(self, branch, preset_name):
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        assert layout._current_view_mode == "search"

    @pytest.mark.parametrize("branch,preset_name", [
        ("discover_beach", "Beach"),
        ("discover_mountains", "Mountains"),
        ("discover_city", "City"),
    ])
    def test_discover_state_text(self, branch, preset_name):
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            f"SEARCH \u2022 Discover preset, {preset_name}"
        )


# ===========================================================================
# Test Class: Favorites and documents use all mode with description
# ===========================================================================

@pytest.mark.unit
class TestFavoritesDocumentsMode:
    """Favorites, documents, screenshots should use 'all' mode with label."""

    def test_favorites_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "favorites")
        assert layout._current_view_mode == "all"

    def test_documents_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "documents")
        assert layout._current_view_mode == "all"

    def test_screenshots_mode(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "screenshots")
        assert layout._current_view_mode == "all"


# ===========================================================================
# Test Class: _clear_shell_state_text sets "Ready"
# ===========================================================================

@pytest.mark.unit
class TestClearShellStateTextReady:
    """_clear_shell_state_text should now set 'Ready' instead of default."""

    def test_clear_sets_ready(self):
        layout = _make_mock_layout()
        layout._clear_shell_state_text()
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with("Ready")


# ===========================================================================
# Test Class: MainWindow year shortcut routing
# ===========================================================================

@pytest.mark.unit
class TestMainWindowYearShortcuts:
    """Year shortcuts from shell should route to layout reload."""

    def test_year_2026_routes_to_reload(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "year_2026")
        layout.request_reload.assert_called_once_with(reason="year_filter", year=2026)

    def test_year_2025_routes_to_reload(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "year_2025")
        layout.request_reload.assert_called_once_with(reason="year_filter", year=2025)

    def test_year_2024_routes_to_reload(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "year_2024")
        layout.request_reload.assert_called_once_with(reason="year_filter", year=2024)

    def test_year_does_not_fall_through_to_people(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "year_2025")
        mw._handle_people_branch.assert_not_called()


# ===========================================================================
# Test Class: MainWindow Phase 10 router
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase10Router:
    """Verify MainWindow router is Phase 10 labeled."""

    def test_router_has_phase_10_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "10" in doc

    def test_router_still_delegates_people(self):
        mw = MagicMock()
        mw._handle_people_branch = MagicMock()
        _mw_search_branch_router(mw, "people_tools")
        mw._handle_people_branch.assert_called_once_with("people_tools")

    def test_router_still_delegates_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        layout._on_passive_shell_branch_clicked = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "dates")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("dates")
