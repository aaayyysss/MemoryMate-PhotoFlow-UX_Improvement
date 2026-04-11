# tests/test_phase6b_routing.py
"""
Phase 6B routing unit tests.

Tests shell-first routing logic in GooglePhotosLayout._on_passive_shell_branch_clicked()
and MainWindow._handle_search_sidebar_branch_request() using pure mocks —
no PySide6 or display server required.

Covers:
- All Photos: clears filters when active, skips when already at all-photos view
- Quick dates: delegates to _on_shell_quick_date_clicked()
- People branches: delegates to MainWindow._handle_people_branch()
- Legacy sections: falls through to accordion._expand_section()
- MainWindow router: Google layout gets first chance, legacy fallback retained

Run with:
    pytest tests/test_phase6b_routing.py -v
    pytest tests/test_phase6b_routing.py -v -m unit
"""

import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# PySide6 mock bootstrap — inject fake PySide6 modules into sys.modules
# BEFORE any application code is imported, so google_layout.py and
# main_window_qt.py can be loaded without a real Qt installation.
# ---------------------------------------------------------------------------

class _MockImportFinder:
    """
    Import hook that auto-mocks any module that fails to import normally.
    This lets us load google_layout.py and main_window_qt.py without
    PySide6, numpy, or any other heavy dependency.

    Key difference from a blanket mock: we try the real import first,
    so actual .py files on disk load normally. Only truly missing
    packages (PySide6, numpy, etc.) get mocked.
    """

    # Modules we WANT to load from disk (never mock these)
    _REAL_MODULES = {
        "layouts.google_layout", "main_window_qt",
        "layouts.base_layout",
    }

    def __init__(self):
        self._active = True

    def find_module(self, name, path=None):
        if not self._active:
            return None
        if name in sys.modules:
            return None
        return self

    def load_module(self, name):
        if name in sys.modules:
            return sys.modules[name]

        # Try real import (remove ourselves to avoid recursion)
        self._active = False
        try:
            import importlib
            mod = importlib.import_module(name)
            return mod
        except Exception:
            # Real import failed — provide a mock
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


# Pre-seed the layouts package as a namespace so google_layout.py loads from disk
import importlib
_finder = _MockImportFinder()
sys.meta_path.insert(0, _finder)

# Force layouts package to be a real namespace package pointing to disk
import importlib.util
import os

_layouts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "layouts")

# Create a minimal 'layouts' package module manually so sub-imports work
_layouts_pkg = types.ModuleType("layouts")
_layouts_pkg.__path__ = [_layouts_dir]
_layouts_pkg.__file__ = os.path.join(_layouts_dir, "__init__.py")
_layouts_pkg.__package__ = "layouts"
sys.modules["layouts"] = _layouts_pkg

# Now import the actual google_layout module from disk
_gl_spec = importlib.util.spec_from_file_location(
    "layouts.google_layout",
    os.path.join(_layouts_dir, "google_layout.py"),
    submodule_search_locations=[],
)
_gl_mod = importlib.util.module_from_spec(_gl_spec)
sys.modules["layouts.google_layout"] = _gl_mod
_gl_spec.loader.exec_module(_gl_mod)
GooglePhotosLayout = _gl_mod.GooglePhotosLayout

# main_window_qt.py: MainWindow subclasses QMainWindow (a mock), so the class
# can't be introspected normally. Use AST to extract the method source, compile
# it, and create a standalone function we can test.
import ast
import textwrap

_mw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "main_window_qt.py")

def _extract_method_from_file(filepath, class_name, method_name):
    """Parse a .py file and extract a method as a standalone function."""
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
                            # Dedent and compile as standalone function
                            method_src = textwrap.dedent(method_src)
                            local_ns = {}
                            exec(compile(method_src, filepath, "exec"), local_ns)
                            return local_ns[method_name]
    return None

_mw_search_branch_router = _extract_method_from_file(
    _mw_path, "MainWindow", "_handle_search_sidebar_branch_request"
)

# Clean up: remove the proxy
sys.meta_path = [p for p in sys.meta_path if not isinstance(p, _MockImportFinder)]


# ---------------------------------------------------------------------------
# Helpers: build a minimal mock layout / main_window
# ---------------------------------------------------------------------------

def _make_mock_layout(**overrides):
    """
    Build a mock object that quacks like GooglePhotosLayout for routing tests.
    All filter fields default to None (no active filters).
    """
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()
    layout.google_shell_sidebar.set_shell_state_text = MagicMock()

    # Bind real helpers
    import functools
    layout._set_shell_active_branch = functools.partial(
        GooglePhotosLayout._set_shell_active_branch, layout
    )
    layout._clear_shell_active_branch = functools.partial(
        GooglePhotosLayout._clear_shell_active_branch, layout
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
    layout._current_view_mode = "all"
    layout._retired_legacy_sections = overrides.get(
        "_retired_legacy_sections",
        {"find", "devices", "videos", "locations", "duplicates"}
    )

    # Accordion sidebar mock
    layout.accordion_sidebar = MagicMock()
    layout.accordion_sidebar._expand_section = MagicMock()
    layout.accordion_sidebar.section_logic = {}

    # MainWindow mock
    layout.main_window = MagicMock()
    layout.main_window._handle_people_branch = MagicMock()

    # Project state
    layout.project_id = overrides.get("project_id", 1)

    # Filter state — all None by default (no active filters)
    layout.current_filter_year = overrides.get("current_filter_year", None)
    layout.current_filter_month = overrides.get("current_filter_month", None)
    layout.current_filter_day = overrides.get("current_filter_day", None)
    layout.current_filter_folder = overrides.get("current_filter_folder", None)
    layout.current_filter_person = overrides.get("current_filter_person", None)
    layout.current_filter_paths = overrides.get("current_filter_paths", None)
    layout.current_filter_group_id = overrides.get("current_filter_group_id", None)
    layout.current_filter_group_mode = overrides.get("current_filter_group_mode", None)

    # Reload signature (None = grid never loaded)
    layout._last_reload_signature = overrides.get("_last_reload_signature", "some_sig")

    # Passive dedupe state
    layout._last_passive_section = None
    layout._last_passive_section_ts = 0.0

    # Thumb size for _request_load
    layout.current_thumb_size = 180

    return layout


def _call_shell_branch(layout, branch):
    """Call the real _on_passive_shell_branch_clicked on our mock layout."""
    GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)


def _call_shell_quick_date(layout, key):
    """Call the real _on_shell_quick_date_clicked on our mock layout."""
    GooglePhotosLayout._on_shell_quick_date_clicked(layout, key)


# ===========================================================================
# Test Class: All Photos routing
# ===========================================================================

@pytest.mark.unit
class TestAllPhotosRouting:
    """Test All Photos (branch='all') shell-first routing."""

    def test_all_photos_clears_filters_when_year_active(self):
        """All Photos should call _clear_filter() when year filter is set."""
        layout = _make_mock_layout(current_filter_year=2025)
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()
        layout.request_reload.assert_not_called()

    def test_all_photos_clears_filters_when_folder_active(self):
        """All Photos should call _clear_filter() when folder filter is set."""
        layout = _make_mock_layout(current_filter_folder="/photos/vacation")
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_clears_filters_when_person_active(self):
        """All Photos should call _clear_filter() when person filter is set."""
        layout = _make_mock_layout(current_filter_person="person_123")
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_clears_filters_when_group_id_active(self):
        """All Photos should call _clear_filter() when group_id filter is set."""
        layout = _make_mock_layout(current_filter_group_id="group_42")
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_clears_filters_when_group_mode_active(self):
        """All Photos should call _clear_filter() when group_mode filter is set."""
        layout = _make_mock_layout(current_filter_group_mode="duplicates")
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_clears_filters_when_paths_active(self):
        """All Photos should call _clear_filter() when paths filter is set."""
        layout = _make_mock_layout(current_filter_paths=["/a.jpg", "/b.jpg"])
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_clears_filters_when_month_active(self):
        """All Photos should call _clear_filter() when month filter is set."""
        layout = _make_mock_layout(current_filter_month=3)
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_clears_filters_when_day_active(self):
        """All Photos should call _clear_filter() when day filter is set."""
        layout = _make_mock_layout(current_filter_day=15)
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_called_once()

    def test_all_photos_skips_when_already_at_all_view(self):
        """All Photos should skip reload when no filters and grid already loaded."""
        layout = _make_mock_layout(_last_reload_signature="existing_sig")
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_not_called()
        layout.request_reload.assert_not_called()

    def test_all_photos_reloads_when_grid_never_loaded(self):
        """All Photos should reload when grid has never loaded."""
        layout = _make_mock_layout(_last_reload_signature=None)
        _call_shell_branch(layout, "all")
        layout.request_reload.assert_called_once_with(reason="browse_all")

    def test_all_photos_no_op_without_project(self):
        """All Photos should do nothing when no project is loaded."""
        layout = _make_mock_layout(project_id=None)
        _call_shell_branch(layout, "all")
        layout._clear_filter.assert_not_called()
        layout.request_reload.assert_not_called()

    def test_all_photos_does_not_expand_accordion(self):
        """All Photos is a direct grid action — should NOT expand any accordion section."""
        layout = _make_mock_layout(current_filter_year=2025)
        _call_shell_branch(layout, "all")
        layout.accordion_sidebar._expand_section.assert_not_called()


# ===========================================================================
# Test Class: Quick Dates routing
# ===========================================================================

@pytest.mark.unit
class TestQuickDatesRouting:
    """Test quick-date shell-first routing."""

    QUICK_DATE_BRANCHES = [
        "today", "yesterday", "last_7_days", "last_30_days",
        "this_month", "last_month", "this_year", "last_year",
    ]

    @pytest.mark.parametrize("branch", QUICK_DATE_BRANCHES)
    def test_quick_date_calls_shell_handler(self, branch):
        """Each quick date branch should call _on_shell_quick_date_clicked."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout._on_shell_quick_date_clicked.assert_called_once_with(branch)

    @pytest.mark.parametrize("branch", QUICK_DATE_BRANCHES)
    def test_quick_date_expands_legacy_section(self, branch):
        """Quick dates should also expand legacy dates/quick section for visual continuity."""
        layout = _make_mock_layout()
        layout.accordion_sidebar._expand_section = MagicMock()

        _call_shell_branch(layout, branch)

        expand_calls = layout.accordion_sidebar._expand_section.call_args_list
        assert len(expand_calls) >= 1
        first_call_arg = expand_calls[0][0][0]
        assert first_call_arg in ("quick", "dates")

    @pytest.mark.parametrize("branch", QUICK_DATE_BRANCHES)
    def test_quick_date_does_not_fall_through_to_legacy_map(self, branch):
        """Quick dates should NOT fall through to the legacy section_only_map."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout._on_shell_quick_date_clicked.assert_called_once()


# ===========================================================================
# Test Class: _on_shell_quick_date_clicked helper
# ===========================================================================

@pytest.mark.unit
class TestShellQuickDateClicked:
    """Test the _on_shell_quick_date_clicked() helper method."""

    def test_today_calls_date_clicked_with_today(self):
        """'today' should call _on_accordion_date_clicked with today's ISO date."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "today")
        layout._on_accordion_date_clicked.assert_called_once_with(
            date.today().isoformat()
        )

    def test_yesterday_calls_date_clicked(self):
        """'yesterday' should call _on_accordion_date_clicked with yesterday's date."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "yesterday")
        expected = (date.today() - timedelta(days=1)).isoformat()
        layout._on_accordion_date_clicked.assert_called_once_with(expected)

    def test_this_year_calls_date_clicked_with_year(self):
        """'this_year' should call _on_accordion_date_clicked with current year string."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "this_year")
        layout._on_accordion_date_clicked.assert_called_once_with(
            str(date.today().year)
        )

    def test_last_year_calls_date_clicked(self):
        """'last_year' should call _on_accordion_date_clicked with previous year."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "last_year")
        layout._on_accordion_date_clicked.assert_called_once_with(
            str(date.today().year - 1)
        )

    def test_this_month_calls_date_clicked(self):
        """'this_month' should call _on_accordion_date_clicked with YYYY-MM."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "this_month")
        today = date.today()
        expected = f"{today.year:04d}-{today.month:02d}"
        layout._on_accordion_date_clicked.assert_called_once_with(expected)

    def test_range_dates_use_request_load(self):
        """Range-style quick dates (last_7_days etc.) should use _request_load."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "last_7_days")
        layout._request_load.assert_called_once()
        call_kwargs = layout._request_load.call_args[1]
        assert call_kwargs["quick_date"] == "last_7_days"
        assert call_kwargs["reset"] is True

    def test_last_30_days_uses_request_load(self):
        """last_30_days should use _request_load with correct params."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "last_30_days")
        layout._request_load.assert_called_once()
        call_kwargs = layout._request_load.call_args[1]
        assert call_kwargs["quick_date"] == "last_30_days"

    def test_last_month_uses_request_load(self):
        """last_month should use _request_load."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_shell_quick_date(layout, "last_month")
        layout._request_load.assert_called_once()
        call_kwargs = layout._request_load.call_args[1]
        assert call_kwargs["quick_date"] == "last_month"

    def test_prefers_quick_section_api(self):
        """Should prefer accordion quick section's _on_quick_date_clicked if available."""
        layout = _make_mock_layout()
        quick_section = MagicMock()
        quick_section._on_quick_date_clicked = MagicMock()
        layout.accordion_sidebar.section_logic = {"quick": quick_section}

        _call_shell_quick_date(layout, "today")

        quick_section._on_quick_date_clicked.assert_called_once_with("today")
        layout._on_accordion_date_clicked.assert_not_called()

    def test_invalid_key_is_no_op(self):
        """An unknown key should be silently ignored."""
        layout = _make_mock_layout()
        _call_shell_quick_date(layout, "unknown_key")
        layout._on_accordion_date_clicked.assert_not_called()
        layout._request_load.assert_not_called()


# ===========================================================================
# Test Class: People branches routing
# ===========================================================================

@pytest.mark.unit
class TestPeopleBranchRouting:
    """Test People branch delegation."""

    PEOPLE_BRANCHES = [
        "people_merge_review",
        "people_unnamed",
        "people_show_all",
        "people_tools",
        "people_merge_history",
        "people_undo_merge",
        "people_redo_merge",
        "people_expand",
        "people_person:abc123",
    ]

    @pytest.mark.parametrize("branch", PEOPLE_BRANCHES)
    def test_people_delegates_to_main_window(self, branch):
        """All people_ branches should delegate to MainWindow._handle_people_branch."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.main_window._handle_people_branch.assert_called_once_with(branch)

    @pytest.mark.parametrize("branch", PEOPLE_BRANCHES)
    def test_people_does_not_expand_accordion(self, branch):
        """People branches should NOT expand any accordion section directly."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.accordion_sidebar._expand_section.assert_not_called()

    @pytest.mark.parametrize("branch", PEOPLE_BRANCHES)
    def test_people_does_not_reload_grid(self, branch):
        """People branches should NOT trigger grid reload."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout._clear_filter.assert_not_called()
        layout.request_reload.assert_not_called()


# ===========================================================================
# Test Class: Legacy section fallback routing
# ===========================================================================

@pytest.mark.unit
class TestLegacySectionRouting:
    """Test legacy-detailed section accordion fallback."""

    # Non-retired sections still expand accordion (Phase 8 kept these live)
    LIVE_SECTION_MAP = {
        "dates": "dates",
        "years": "dates",
        "months": "dates",
        "days": "dates",
        "folders": "folders",
    }

    # Phase 9: retired branches that skip accordion entirely
    # Phase 10C fix pack v3: Discover presets now expand the find section as
    # a visible outcome (they seed a smart-find query and execute it).
    RETIRED_SKIP_ACCORDION = [
        "videos", "duplicates",
        "favorites", "documents", "screenshots", "find",
    ]

    # Phase 9: retired branches that still expand accordion as visible outcome
    RETIRED_EXPAND_ACCORDION = [
        ("devices", "devices"),
        ("locations", "locations"),
        # Phase 10C fix pack v3: Discover presets expand find to show results
        ("discover_beach", "find"),
        ("discover_mountains", "find"),
        ("discover_city", "find"),
    ]

    @pytest.mark.parametrize("branch,expected_section", list(LIVE_SECTION_MAP.items()))
    def test_legacy_branch_expands_correct_section(self, branch, expected_section):
        """Non-retired legacy branches should expand the correct accordion section."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.accordion_sidebar._expand_section.assert_called_once_with(expected_section)

    @pytest.mark.parametrize("branch", RETIRED_SKIP_ACCORDION)
    def test_retired_branch_skips_accordion(self, branch):
        """Phase 9 retired branches that skip accordion entirely."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.accordion_sidebar._expand_section.assert_not_called()

    @pytest.mark.parametrize("branch,expected_section", RETIRED_EXPAND_ACCORDION)
    def test_retired_branch_expands_accordion_as_visible_outcome(self, branch, expected_section):
        """Phase 9 retired branches that expand accordion as visible outcome."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.accordion_sidebar._expand_section.assert_called_once_with(expected_section)

    @pytest.mark.parametrize("branch,expected_section", list(LIVE_SECTION_MAP.items()))
    def test_legacy_branch_does_not_reload_grid(self, branch, expected_section):
        """Legacy branches should NOT trigger grid reload — accordion owns the action."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout._clear_filter.assert_not_called()
        layout.request_reload.assert_not_called()

    def test_unknown_branch_is_no_op(self):
        """An unknown branch should silently do nothing."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, "totally_unknown_branch")
        layout.accordion_sidebar._expand_section.assert_not_called()
        layout._clear_filter.assert_not_called()
        layout.request_reload.assert_not_called()
        layout.main_window._handle_people_branch.assert_not_called()

    def test_dedupe_guard_skips_repeated_same_section(self):
        """Expanding the same section within 1s should be deduped."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, "folders")
        assert layout.accordion_sidebar._expand_section.call_count == 1
        # Second call immediately should be deduped
        _call_shell_branch(layout, "folders")
        assert layout.accordion_sidebar._expand_section.call_count == 1

    def test_different_sections_not_deduped(self):
        """Expanding different non-retired sections should NOT be deduped."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, "folders")
        assert layout.accordion_sidebar._expand_section.call_count == 1
        _call_shell_branch(layout, "dates")
        assert layout.accordion_sidebar._expand_section.call_count == 2


# ===========================================================================
# Test Class: No accordion sidebar (guard)
# ===========================================================================

@pytest.mark.unit
class TestNoAccordionGuard:
    """Test that routing handles missing accordion gracefully."""

    def test_no_accordion_is_no_op(self):
        """Should silently return when accordion_sidebar is None."""
        layout = _make_mock_layout()
        layout.accordion_sidebar = None

        # Should not raise
        _call_shell_branch(layout, "all")
        _call_shell_branch(layout, "folders")
        _call_shell_branch(layout, "people_merge_review")
        _call_shell_branch(layout, "today")


# ===========================================================================
# Test Class: MainWindow _handle_search_sidebar_branch_request
# ===========================================================================

@pytest.mark.unit
class TestMainWindowSearchBranchRouter:
    """Test MainWindow._handle_search_sidebar_branch_request Phase 6B router."""

    def _make_mock_main_window(self, has_google_layout=True):
        """Build a mock MainWindow with layout_manager."""
        mw = MagicMock()
        mw.__class__.__name__ = "MainWindow"
        mw._handle_people_branch = MagicMock()
        mw.sidebar = MagicMock()

        layout = MagicMock()
        if not has_google_layout:
            del layout._on_passive_shell_branch_clicked

        mw.layout_manager = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        mw._layout = layout
        return mw, layout

    def _call_router(self, mw, branch):
        assert _mw_search_branch_router is not None, \
            "Could not extract _handle_search_sidebar_branch_request from MainWindow"
        _mw_search_branch_router(mw, branch)

    def test_people_branch_delegates_to_people_handler(self):
        """People branches should go through _handle_people_branch, not layout."""
        mw, layout = self._make_mock_main_window()
        self._call_router(mw, "people_merge_review")
        mw._handle_people_branch.assert_called_once_with("people_merge_review")
        layout._on_passive_shell_branch_clicked.assert_not_called()

    def test_non_people_branch_delegates_to_google_layout(self):
        """Non-people branches should go to Google layout's shell-first handler."""
        mw, layout = self._make_mock_main_window()
        self._call_router(mw, "folders")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("folders")

    def test_all_photos_delegates_to_google_layout(self):
        """'all' branch should go to Google layout."""
        mw, layout = self._make_mock_main_window()
        self._call_router(mw, "all")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("all")

    def test_quick_date_delegates_to_google_layout(self):
        """Quick date branches should go to Google layout."""
        mw, layout = self._make_mock_main_window()
        self._call_router(mw, "today")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("today")

    def test_non_google_layout_falls_back_to_sidebar(self):
        """Non-Google layouts should fall back to sidebar.selectBranch.emit."""
        mw, layout = self._make_mock_main_window(has_google_layout=False)
        self._call_router(mw, "folders")
        mw.sidebar.selectBranch.emit.assert_called_once_with("folders")

    def test_people_branch_does_not_reach_sidebar_fallback(self):
        """People branches should return early, never hitting sidebar fallback."""
        mw, layout = self._make_mock_main_window()
        self._call_router(mw, "people_tools")
        mw._handle_people_branch.assert_called_once_with("people_tools")
        mw.sidebar.selectBranch.emit.assert_not_called()
