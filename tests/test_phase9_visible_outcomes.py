# tests/test_phase9_visible_outcomes.py
"""
Phase 9 shell-native visible outcomes unit tests.

Tests:
- Shell status text helpers (_set_shell_state_text, _clear_shell_state_text)
- Retired sections produce visible shell state messages
- Find focuses search bar
- Videos, Locations, Duplicates, Devices produce visible outcomes
- Quick dates set shell state text
- Accordion date/folder/location/person clicks set shell state text
- Clear filter sets shell state text
- Activity center sets shell state text
- No-project shell clicks set onboarding state text
- Project set clears shell state text
- MainWindow router docstring updated to Phase 9

No PySide6 or display server required.

Run with:
    pytest tests/test_phase9_visible_outcomes.py -v
    pytest tests/test_phase9_visible_outcomes.py -v -m unit
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
    """Build a mock GooglePhotosLayout with Phase 9 shell-native outcomes."""
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_project_available = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()
    layout.google_shell_sidebar.set_retired_legacy_sections = MagicMock()
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

    # View mode state (Phase 10)
    layout._current_view_mode = "all"

    # Retired sections (Phase 8 Wave 1)
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
    layout.main_window._handle_people_branch = MagicMock()
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
# Test Class: Shell state text helpers
# ===========================================================================

@pytest.mark.unit
class TestShellStateTextHelpers:
    """Test _set_shell_state_text and _clear_shell_state_text."""

    def test_set_shell_state_text_calls_sidebar(self):
        layout = _make_mock_layout()
        layout._set_shell_state_text("Hello")
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with("Hello")

    def test_clear_shell_state_text_calls_sidebar(self):
        layout = _make_mock_layout()
        layout._clear_shell_state_text()
        # Phase 10: clear now sets "Ready" via set_shell_state_text
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with("Ready")

    def test_set_state_text_no_sidebar_no_crash(self):
        layout = _make_mock_layout()
        layout.google_shell_sidebar = None
        layout._set_shell_state_text = functools.partial(
            GooglePhotosLayout._set_shell_state_text, layout
        )
        # Should not raise
        layout._set_shell_state_text("Test")

    def test_clear_state_text_no_sidebar_no_crash(self):
        layout = _make_mock_layout()
        layout.google_shell_sidebar = None
        layout._clear_shell_state_text = functools.partial(
            GooglePhotosLayout._clear_shell_state_text, layout
        )
        # Should not raise
        layout._clear_shell_state_text()


# ===========================================================================
# Test Class: Find produces visible outcome
# ===========================================================================

@pytest.mark.unit
class TestFindVisibleOutcome:
    """Find should set state text and focus search bar."""

    def test_find_sets_shell_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "find")
        # Phase 10: uses view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "SEARCH \u2022 Type to search your library"
        )

    def test_find_focuses_search_bar(self):
        layout = _make_mock_layout()
        # Phase 10: prefers top_search_bar, falls back to search_bar
        layout.main_window.top_search_bar = MagicMock()
        _call_shell_branch(layout, "find")
        layout.main_window.top_search_bar.setFocus.assert_called_once()

    def test_find_focuses_fallback_search_bar(self):
        layout = _make_mock_layout()
        layout.main_window.top_search_bar = None
        _call_shell_branch(layout, "find")
        layout.main_window.search_bar.setFocus.assert_called_once()

    def test_find_sets_emphasis_false(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "find")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)


# ===========================================================================
# Test Class: Videos produces visible outcome
# ===========================================================================

@pytest.mark.unit
class TestVideosVisibleOutcome:
    """Videos should set state text and attempt visible action."""

    def test_videos_sets_shell_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "videos")
        # Phase 10: view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "VIDEOS \u2022 All videos"
        )

    def test_videos_sets_emphasis_false(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "videos")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    def test_videos_sets_active_branch(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "videos")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("videos")


# ===========================================================================
# Test Class: Locations produces visible outcome
# ===========================================================================

@pytest.mark.unit
class TestLocationsVisibleOutcome:
    """Locations should set state text and expand accordion section."""

    def test_locations_sets_shell_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "locations")
        # Phase 10B: actionable description
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "LOCATIONS \u2022 Pick a location cluster below"
        )

    def test_locations_expands_accordion(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "locations")
        layout.accordion_sidebar._expand_section.assert_called_once_with("locations")

    def test_locations_sets_emphasis_false(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "locations")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)


# ===========================================================================
# Test Class: Duplicates produces visible outcome
# ===========================================================================

@pytest.mark.unit
class TestDuplicatesVisibleOutcome:
    """Duplicates should set state text and try to open dialog."""

    def test_duplicates_sets_shell_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "duplicates")
        # Phase 10: view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "REVIEW \u2022 Duplicates"
        )

    def test_duplicates_sets_emphasis_false(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "duplicates")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    def test_duplicates_tries_open_dialog(self):
        layout = _make_mock_layout()
        layout._open_duplicates_dialog = MagicMock()
        _call_shell_branch(layout, "duplicates")
        layout._open_duplicates_dialog.assert_called_once()


# ===========================================================================
# Test Class: Devices produces visible outcome
# ===========================================================================

@pytest.mark.unit
class TestDevicesVisibleOutcome:
    """Devices should set state text and expand accordion section."""

    def test_devices_sets_shell_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "devices")
        # Phase 10B: actionable description
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "DEVICES \u2022 Pick a device source below"
        )

    def test_devices_expands_accordion(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "devices")
        layout.accordion_sidebar._expand_section.assert_called_once_with("devices")

    def test_devices_sets_emphasis_false(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "devices")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)


# ===========================================================================
# Test Class: Discover presets set visible state text
# ===========================================================================

@pytest.mark.unit
class TestDiscoverPresetsVisibleOutcome:
    """Discover presets should set descriptive shell state text."""

    @pytest.mark.parametrize("branch,expected_text", [
        ("discover_beach", "SEARCH \u2022 Discover preset, Beach"),
        ("discover_mountains", "SEARCH \u2022 Discover preset, Mountains"),
        ("discover_city", "SEARCH \u2022 Discover preset, City"),
    ])
    def test_discover_preset_sets_state_text(self, branch, expected_text):
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        # Phase 10: view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(expected_text)

    @pytest.mark.parametrize("branch", ["discover_beach", "discover_mountains", "discover_city"])
    def test_discover_preset_sets_emphasis_false(self, branch):
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)


# ===========================================================================
# Test Class: Favorites and document branches visible outcome
# ===========================================================================

@pytest.mark.unit
class TestFavoritesDocumentsScreenshots:
    """Favorites, documents, screenshots should set visible state text."""

    def test_favorites_sets_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "favorites")
        # Phase 10: view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 Showing favorites"
        )

    def test_favorites_calls_filter_if_available(self):
        layout = _make_mock_layout()
        layout._filter_favorites = MagicMock()
        _call_shell_branch(layout, "favorites")
        layout._filter_favorites.assert_called_once()

    def test_documents_sets_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "documents")
        # Phase 10: view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 Showing documents"
        )

    def test_screenshots_sets_state_text(self):
        layout = _make_mock_layout()
        _call_shell_branch(layout, "screenshots")
        # Phase 10: view mode format
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "ALL \u2022 Showing screenshots"
        )


# ===========================================================================
# Test Class: Quick dates set shell state text
# ===========================================================================

@pytest.mark.unit
class TestQuickDatesShellStateText:
    """Quick date clicks should set visible shell state text."""

    @pytest.mark.parametrize("key", [
        "today", "yesterday", "last_7_days", "last_30_days",
        "this_month", "last_month", "this_year", "last_year",
    ])
    def test_quick_date_sets_state_text(self, key):
        layout = _make_mock_layout()
        layout._on_shell_quick_date_clicked = functools.partial(
            GooglePhotosLayout._on_shell_quick_date_clicked, layout
        )
        layout._on_shell_quick_date_clicked(key)
        expected = f"Quick date, {key.replace('_', ' ')}"
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(expected)


# ===========================================================================
# Test Class: Clear filter sets shell state text
# ===========================================================================

@pytest.mark.unit
class TestClearFilterShellStateText:
    """Clear filter should show 'Showing all photos'."""

    def test_clear_filter_sets_showing_all(self):
        layout = _make_mock_layout()
        layout.current_thumb_size = 200
        layout._clear_filter = functools.partial(
            GooglePhotosLayout._clear_filter, layout
        )
        layout._clear_filter()
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "Showing all photos"
        )


# ===========================================================================
# Test Class: Activity center sets shell state text
# ===========================================================================

@pytest.mark.unit
class TestActivityCenterShellStateText:
    """Toggle activity center should set visible state text."""

    def test_toggle_sets_state_text(self):
        layout = _make_mock_layout()
        layout._on_toggle_activity_center = functools.partial(
            GooglePhotosLayout._on_toggle_activity_center, layout
        )
        layout._on_toggle_activity_center()
        layout.google_shell_sidebar.set_shell_state_text.assert_called_with(
            "Opening Activity Center"
        )


# ===========================================================================
# Test Class: MainWindow router Phase 9
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase9Router:
    """Verify MainWindow router is Phase 9 labeled."""

    def test_router_has_phase_9_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "9" in doc or "10" in doc

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
