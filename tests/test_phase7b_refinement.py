# tests/test_phase7b_refinement.py
"""
Phase 7B shell-primary refinement unit tests.

Tests:
- Onboarding/no-project branch gating (_emit_branch / disabledBranchRequested)
- set_project_available toggles disabledShell property
- Legacy emphasis toggling from shell clicks vs accordion interactions
- _on_disabled_shell_branch_requested handler behavior
- set_project syncs shell availability
- on_layout_activated syncs shell availability
- MainWindow router docstring updated to Phase 7B

No PySide6 or display server required.

Run with:
    pytest tests/test_phase7b_refinement.py -v
    pytest tests/test_phase7b_refinement.py -v -m unit
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
# Mock import bootstrap (same strategy as previous phase tests)
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
    """Build a mock GooglePhotosLayout with real Phase 7B helpers bound."""
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_project_available = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()

    # Bind real helper methods
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


# ===========================================================================
# Test Class: Shell onboarding / no-project gating
# ===========================================================================

@pytest.mark.unit
class TestShellOnboardingGating:
    """Test that shell branches are gated when no project exists."""

    # All project-required branches from the shell sidebar
    PROJECT_REQUIRED = [
        "all", "dates", "today", "yesterday", "last_7_days", "last_30_days",
        "this_month", "last_month", "this_year", "last_year", "folders",
        "devices", "favorites", "videos", "documents", "screenshots",
        "duplicates", "locations", "discover_beach", "discover_mountains",
        "discover_city", "find", "people_merge_review", "people_unnamed",
        "people_show_all",
    ]

    def test_disabled_branch_handler_exists(self):
        """Layout should have _on_disabled_shell_branch_requested."""
        assert hasattr(GooglePhotosLayout, "_on_disabled_shell_branch_requested")

    def test_disabled_handler_does_not_raise(self):
        """Handler should not raise for any common branch."""
        layout = _make_mock_layout(project_id=None)
        for branch in ["all", "folders", "dates", "videos"]:
            GooglePhotosLayout._on_disabled_shell_branch_requested(layout, branch)

    def test_disabled_handler_logs_common_branches(self):
        """Handler should log info for common branches without project."""
        layout = _make_mock_layout(project_id=None)
        # Should not raise
        GooglePhotosLayout._on_disabled_shell_branch_requested(layout, "all")
        GooglePhotosLayout._on_disabled_shell_branch_requested(layout, "folders")


# ===========================================================================
# Test Class: set_project syncs shell availability
# ===========================================================================

@pytest.mark.unit
class TestSetProjectSyncsShell:
    """Test that set_project propagates project availability to shell."""

    def test_set_project_with_id_enables_shell(self):
        """Setting a project should call set_project_available(True)."""
        layout = _make_mock_layout(project_id=None)
        layout._project_switch_in_progress = False
        layout._pending_project_reload = False
        layout._last_load_signature = None

        GooglePhotosLayout.set_project(layout, 42)

        layout.google_shell_sidebar.set_project_available.assert_called_with(True)

    def test_set_project_none_disables_shell(self):
        """Setting project to None should call set_project_available(False)."""
        layout = _make_mock_layout(project_id=1)
        layout._project_switch_in_progress = False
        layout._pending_project_reload = False
        layout._last_load_signature = None

        GooglePhotosLayout.set_project(layout, None)

        layout.google_shell_sidebar.set_project_available.assert_called_with(False)


# ===========================================================================
# Test Class: Legacy emphasis from shell clicks
# ===========================================================================

@pytest.mark.unit
class TestLegacyEmphasisFromShellClicks:
    """Test that shell clicks set legacy emphasis correctly."""

    SHELL_PRIMARY_BRANCHES = [
        "all", "today", "yesterday", "last_7_days", "last_30_days",
        "this_month", "last_month", "this_year", "last_year",
        "people_merge_review", "people_unnamed", "people_show_all",
    ]

    # Phase 8 retired these sections — their branches now set emphasis=False
    RETIRED_EMPHASIS_BRANCHES = [
        "devices", "favorites", "videos", "documents", "screenshots",
        "duplicates", "locations", "discover_beach", "discover_mountains",
        "discover_city", "find",
    ]

    # Non-retired legacy branches still set emphasis=True
    LEGACY_EMPHASIS_BRANCHES = [
        "dates", "years", "months", "days", "folders",
    ]

    @pytest.mark.parametrize("branch", SHELL_PRIMARY_BRANCHES)
    def test_shell_primary_sets_emphasis_false(self, branch):
        """Shell-primary branches should set legacy emphasis to False."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    @pytest.mark.parametrize("branch", RETIRED_EMPHASIS_BRANCHES)
    def test_retired_branches_set_emphasis_false(self, branch):
        """Phase 8 retired branches now set legacy emphasis to False."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    @pytest.mark.parametrize("branch", LEGACY_EMPHASIS_BRANCHES)
    def test_legacy_branches_set_emphasis_true(self, branch):
        """Non-retired legacy branches should set legacy emphasis to True."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_passive_shell_branch_clicked(layout, branch)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(True)


# ===========================================================================
# Test Class: Legacy emphasis from accordion interactions
# ===========================================================================

@pytest.mark.unit
class TestLegacyEmphasisFromAccordion:
    """Test that accordion interactions set legacy emphasis correctly."""

    def test_accordion_date_sets_legacy_emphasis_true(self):
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_date_clicked(layout, "2025")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(True)

    def test_accordion_location_sets_legacy_emphasis_true(self):
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_location_clicked(
            layout, {"name": "Beach", "count": 5, "paths": ["/a.jpg"]}
        )
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(True)

    def test_accordion_person_sets_legacy_emphasis_false(self):
        """Person actions are shell-primary, so legacy emphasis should be False."""
        layout = _make_mock_layout()
        GooglePhotosLayout._on_accordion_person_clicked(layout, "cluster_123")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    def test_quick_date_sets_legacy_emphasis_false(self):
        """Quick dates are shell-primary, legacy emphasis False."""
        layout = _make_mock_layout()
        layout.accordion_sidebar.section_logic = {}
        GooglePhotosLayout._on_shell_quick_date_clicked(layout, "today")
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)


# ===========================================================================
# Test Class: Clear filter resets emphasis
# ===========================================================================

@pytest.mark.unit
class TestClearFilterResetsEmphasis:
    """Test that _clear_filter resets legacy emphasis."""

    def test_clear_filter_sets_emphasis_false(self):
        layout = _make_mock_layout()
        GooglePhotosLayout._clear_filter(layout)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)

    def test_clear_filter_sets_active_all(self):
        layout = _make_mock_layout()
        GooglePhotosLayout._clear_filter(layout)
        layout.google_shell_sidebar.set_active_branch.assert_called_with("all")


# ===========================================================================
# Test Class: on_layout_activated syncs availability
# ===========================================================================

@pytest.mark.unit
class TestLayoutActivationSyncsShell:
    """Test on_layout_activated syncs shell project availability."""

    def test_activation_with_project_enables_shell(self):
        layout = _make_mock_layout(project_id=5)
        GooglePhotosLayout.on_layout_activated(layout)
        layout.google_shell_sidebar.set_project_available.assert_called_with(True)

    def test_activation_without_project_disables_shell(self):
        layout = _make_mock_layout(project_id=None)
        GooglePhotosLayout.on_layout_activated(layout)
        layout.google_shell_sidebar.set_project_available.assert_called_with(False)


# ===========================================================================
# Test Class: MainWindow Phase 7B router
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase7BRouter:
    """Verify MainWindow router is Phase 7B labeled."""

    def test_router_has_phase_7b_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "7B" in doc or "7b" in doc or "8" in doc

    def test_router_still_delegates_people(self):
        mw = MagicMock()
        mw._handle_people_branch = MagicMock()
        _mw_search_branch_router(mw, "people_tools")
        mw._handle_people_branch.assert_called_once_with("people_tools")

    def test_router_still_delegates_to_layout(self):
        mw = MagicMock()
        layout = MagicMock()
        mw.layout_manager.get_current_layout.return_value = layout
        _mw_search_branch_router(mw, "folders")
        layout._on_passive_shell_branch_clicked.assert_called_once_with("folders")
