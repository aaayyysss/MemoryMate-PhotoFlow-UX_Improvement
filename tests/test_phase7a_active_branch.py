# tests/test_phase7a_active_branch.py
"""
Phase 7A active-branch sync unit tests.

Tests that shell active-branch highlighting syncs correctly from:
- shell clicks (via _on_passive_shell_branch_clicked)
- accordion date selection
- accordion folder execution
- accordion branch/person selection
- accordion location selection
- quick-date actions
- clear-filter / All Photos reset

Also tests GoogleShellSidebar.set_active_branch / clear_active_branch.

No PySide6 or display server required — uses the same mock strategy
as test_phase6b_routing.py.

Run with:
    pytest tests/test_phase7a_active_branch.py -v
    pytest tests/test_phase7a_active_branch.py -v -m unit
"""

import sys
import types
import os
import ast
import textwrap
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Mock import bootstrap (same strategy as test_phase6b_routing.py)
# ---------------------------------------------------------------------------

class _MockImportFinder:
    """Auto-mocks any module that fails to import normally."""

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

# Extract MainWindow._handle_search_sidebar_branch_request via AST
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
    """Build a mock GooglePhotosLayout for active-branch sync tests.

    Binds the real _set_shell_active_branch / _clear_shell_active_branch
    methods so that calls like self._set_shell_active_branch() in the
    production code actually execute the real logic and reach our
    google_shell_sidebar mock.
    """
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()

    # Bind real helper methods so production code's self._set_shell_active_branch()
    # actually calls through to google_shell_sidebar.set_active_branch()
    import functools
    layout._set_shell_active_branch = functools.partial(
        GooglePhotosLayout._set_shell_active_branch, layout
    )
    layout._clear_shell_active_branch = functools.partial(
        GooglePhotosLayout._clear_shell_active_branch, layout
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

    # Filter state
    layout.current_filter_year = overrides.get("current_filter_year", None)
    layout.current_filter_month = overrides.get("current_filter_month", None)
    layout.current_filter_day = overrides.get("current_filter_day", None)
    layout.current_filter_folder = overrides.get("current_filter_folder", None)
    layout.current_filter_person = overrides.get("current_filter_person", None)
    layout.current_filter_paths = overrides.get("current_filter_paths", None)
    layout.current_filter_group_id = overrides.get("current_filter_group_id", None)
    layout.current_filter_group_mode = overrides.get("current_filter_group_mode", None)
    layout._last_reload_signature = overrides.get("_last_reload_signature", "some_sig")
    layout._last_passive_section = None
    layout._last_passive_section_ts = 0.0
    layout.current_thumb_size = 180

    return layout


def _call_shell_branch(layout, branch):
    GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)


def _call_set_shell_active(layout, branch):
    GooglePhotosLayout._set_shell_active_branch(layout, branch)


def _call_clear_shell_active(layout):
    GooglePhotosLayout._clear_shell_active_branch(layout)


def _call_clear_filter(layout):
    GooglePhotosLayout._clear_filter(layout)


def _call_quick_date(layout, key):
    GooglePhotosLayout._on_shell_quick_date_clicked(layout, key)


# ===========================================================================
# Test Class: _set_shell_active_branch / _clear_shell_active_branch
# ===========================================================================

@pytest.mark.unit
class TestShellActiveHelpers:
    """Test the _set_shell_active_branch / _clear_shell_active_branch helpers."""

    def test_set_active_branch_calls_sidebar(self):
        layout = _make_mock_layout()
        _call_set_shell_active(layout, "folders")
        layout.google_shell_sidebar.set_active_branch.assert_called_once_with("folders")

    def test_clear_active_branch_calls_sidebar(self):
        layout = _make_mock_layout()
        _call_clear_shell_active(layout)
        layout.google_shell_sidebar.clear_active_branch.assert_called_once()

    def test_set_active_branch_none_is_valid(self):
        layout = _make_mock_layout()
        _call_set_shell_active(layout, None)
        layout.google_shell_sidebar.set_active_branch.assert_called_once_with(None)

    def test_no_sidebar_is_safe(self):
        layout = _make_mock_layout()
        layout.google_shell_sidebar = None
        # Should not raise
        _call_set_shell_active(layout, "all")
        _call_clear_shell_active(layout)


# ===========================================================================
# Test Class: Shell click sets active branch
# ===========================================================================

@pytest.mark.unit
class TestShellClickSetsActiveBranch:
    """Test that shell clicks correctly set the active branch highlight."""

    BRANCH_TO_ACTIVE = {
        "all": "all",
        "dates": "dates",
        "years": "dates",
        "months": "dates",
        "days": "dates",
        "today": "today",
        "yesterday": "yesterday",
        "last_7_days": "last_7_days",
        "last_30_days": "last_30_days",
        "this_month": "this_month",
        "last_month": "last_month",
        "this_year": "this_year",
        "last_year": "last_year",
        "folders": "folders",
        "devices": "devices",
        "favorites": "favorites",
        "videos": "videos",
        "documents": "documents",
        "screenshots": "screenshots",
        "duplicates": "duplicates",
        "locations": "locations",
        "discover_beach": "discover_beach",
        "discover_mountains": "discover_mountains",
        "discover_city": "discover_city",
        "find": "find",
        "people_merge_review": "people_merge_review",
        "people_unnamed": "people_unnamed",
        "people_show_all": "people_show_all",
    }

    @pytest.mark.parametrize("branch,expected_active", list(BRANCH_TO_ACTIVE.items()))
    def test_shell_click_sets_active_branch(self, branch, expected_active):
        """Shell click should set the correct active branch on the sidebar."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_active_branch.assert_called_with(expected_active)

    def test_unknown_branch_sets_none(self):
        """Unknown branches should set active branch to None."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, "totally_unknown")
        layout.google_shell_sidebar.set_active_branch.assert_called_with(None)


# ===========================================================================
# Test Class: Accordion actions sync shell highlight
# ===========================================================================

@pytest.mark.unit
class TestAccordionSyncsShellHighlight:
    """Test that accordion interactions sync the shell active branch."""

    def test_accordion_date_sets_dates(self):
        """Accordion date click should set shell to 'dates'."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_date_clicked(layout, "2025-06-15")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("dates")

    def test_accordion_branch_sets_people_show_all(self):
        """Accordion branch click should set shell to 'people_show_all'."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_branch_clicked(layout, "branch:someid")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("people_show_all")

    def test_accordion_person_sets_people_show_all(self):
        """Accordion person click should set shell to 'people_show_all'."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_person_clicked(layout, "cluster_123")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("people_show_all")

    def test_accordion_person_clear_sets_all(self):
        """Clearing person filter should set shell to 'all'."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_person_clicked(layout, "")
        # First call is "people_show_all", second is "all" for the clear path
        calls = layout.google_shell_sidebar.set_active_branch.call_args_list
        assert len(calls) >= 2
        assert calls[0][0][0] == "people_show_all"
        assert calls[1][0][0] == "all"

    def test_accordion_location_sets_locations(self):
        """Accordion location click should set shell to 'locations'."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_location_clicked(
            layout, {"name": "Beach", "count": 5, "paths": ["/a.jpg"]}
        )
        layout.google_shell_sidebar.set_active_branch.assert_called_with("locations")


# ===========================================================================
# Test Class: Clear filter syncs shell highlight
# ===========================================================================

@pytest.mark.unit
class TestClearFilterSyncsShell:
    """Test that _clear_filter sets shell active branch to 'all'."""

    def test_clear_filter_sets_all(self):
        layout = _make_mock_layout()
        _call_clear_filter(layout)
        layout.google_shell_sidebar.set_active_branch.assert_called_with("all")


# ===========================================================================
# Test Class: Quick date syncs shell highlight
# ===========================================================================

@pytest.mark.unit
class TestQuickDateSyncsShell:
    """Test that _on_shell_quick_date_clicked sets shell active branch."""

    QUICK_DATES = [
        "today", "yesterday", "last_7_days", "last_30_days",
        "this_month", "last_month", "this_year", "last_year",
    ]

    @pytest.mark.parametrize("key", QUICK_DATES)
    def test_quick_date_sets_active_branch(self, key):
        """Quick date click should set shell active to the date key."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        _call_quick_date(layout, key)
        layout.google_shell_sidebar.set_active_branch.assert_called_with(key)


# ===========================================================================
# Test Class: All Photos via shell sets active branch
# ===========================================================================

@pytest.mark.unit
class TestAllPhotosShellActive:
    """Test that All Photos correctly sets shell active to 'all'."""

    def test_all_with_filters_sets_all(self):
        layout = _make_mock_layout(current_filter_year=2025)
        _call_shell_branch(layout, "all")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("all")

    def test_all_without_filters_sets_all(self):
        layout = _make_mock_layout(_last_reload_signature="sig")
        _call_shell_branch(layout, "all")
        layout.google_shell_sidebar.set_active_branch.assert_called_with("all")


# ===========================================================================
# Test Class: MainWindow Phase 7A router docstring check
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase7ARouter:
    """Verify MainWindow router is Phase 7A labeled."""

    def test_router_has_phase_7_or_later_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "7A" in doc or "7B" in doc or "7a" in doc or "7b" in doc or "8" in doc

    def test_router_delegates_people_to_handler(self):
        mw = MagicMock()
        mw._handle_people_branch = MagicMock()
        _mw_search_branch_router(mw, "people_tools")
        mw._handle_people_branch.assert_called_once_with("people_tools")

    def test_router_delegates_non_people_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "folders")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("folders")
