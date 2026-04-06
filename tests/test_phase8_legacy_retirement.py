# tests/test_phase8_legacy_retirement.py
"""
Phase 8 gradual legacy retirement unit tests.

Tests:
- Retired legacy sections (find, devices, videos, locations, duplicates)
  no longer expand accordion — shell handles them directly
- Non-retired sections (dates, folders, people) still expand accordion
- _is_legacy_section_retired helper
- _refresh_legacy_visibility_state helper
- Shell-primary emphasis expanded to cover retired sections
- MainWindow router docstring updated to Phase 8

No PySide6 or display server required.

Run with:
    pytest tests/test_phase8_legacy_retirement.py -v
    pytest tests/test_phase8_legacy_retirement.py -v -m unit
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
    """Build a mock GooglePhotosLayout with Phase 8 retired sections."""
    layout = MagicMock()
    layout.__class__.__name__ = "GooglePhotosLayout"

    # Shell sidebar mock
    layout.google_shell_sidebar = MagicMock()
    layout.google_shell_sidebar.set_active_branch = MagicMock()
    layout.google_shell_sidebar.clear_active_branch = MagicMock()
    layout.google_shell_sidebar.set_project_available = MagicMock()
    layout.google_shell_sidebar.set_legacy_emphasis = MagicMock()
    layout.google_shell_sidebar.set_retired_legacy_sections = MagicMock()

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

    # Retired sections (Phase 8 Wave 1)
    layout._retired_legacy_sections = overrides.get(
        "_retired_legacy_sections",
        {"find", "devices", "videos", "locations", "duplicates"}
    )

    # Accordion sidebar mock
    layout.accordion_sidebar = MagicMock()
    layout.accordion_sidebar._expand_section = MagicMock()
    layout.accordion_sidebar.section_logic = {}

    # Legacy tools group mock
    layout.legacy_tools_group = MagicMock()

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


# ===========================================================================
# Test Class: _is_legacy_section_retired helper
# ===========================================================================

@pytest.mark.unit
class TestIsLegacySectionRetired:
    """Test the _is_legacy_section_retired helper."""

    RETIRED = ["find", "devices", "videos", "locations", "duplicates"]
    NOT_RETIRED = ["dates", "folders", "people"]

    @pytest.mark.parametrize("section", RETIRED)
    def test_retired_sections_return_true(self, section):
        layout = _make_mock_layout()
        assert layout._is_legacy_section_retired(section) is True

    @pytest.mark.parametrize("section", NOT_RETIRED)
    def test_non_retired_sections_return_false(self, section):
        layout = _make_mock_layout()
        assert layout._is_legacy_section_retired(section) is False

    def test_unknown_section_returns_false(self):
        layout = _make_mock_layout()
        assert layout._is_legacy_section_retired("unknown") is False


# ===========================================================================
# Test Class: Retired sections skip accordion expand
# ===========================================================================

@pytest.mark.unit
class TestRetiredSectionsSkipAccordion:
    """Retired-section branches should NOT expand accordion."""

    RETIRED_BRANCHES = [
        "find", "discover_beach", "discover_mountains", "discover_city",
        "favorites", "documents", "screenshots",
        "devices", "videos", "locations", "duplicates",
    ]

    @pytest.mark.parametrize("branch", RETIRED_BRANCHES)
    def test_retired_branch_does_not_expand_accordion(self, branch):
        """Retired branches should not call accordion._expand_section."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.accordion_sidebar._expand_section.assert_not_called()

    @pytest.mark.parametrize("branch", RETIRED_BRANCHES)
    def test_retired_branch_sets_active_branch(self, branch):
        """Retired branches should still set shell active branch."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_active_branch.assert_called()


# ===========================================================================
# Test Class: Non-retired sections still expand accordion
# ===========================================================================

@pytest.mark.unit
class TestNonRetiredStillExpandAccordion:
    """Non-retired sections should still expand accordion as before."""

    NON_RETIRED_BRANCHES = {
        "dates": "dates",
        "years": "dates",
        "months": "dates",
        "days": "dates",
        "folders": "folders",
    }

    @pytest.mark.parametrize("branch,expected_section", list(NON_RETIRED_BRANCHES.items()))
    def test_non_retired_branch_expands_accordion(self, branch, expected_section):
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.accordion_sidebar._expand_section.assert_called_once_with(expected_section)


# ===========================================================================
# Test Class: Shell-primary emphasis covers retired sections
# ===========================================================================

@pytest.mark.unit
class TestShellPrimaryEmphasisExpanded:
    """Shell-primary emphasis should include retired sections."""

    NEWLY_SHELL_PRIMARY = [
        "find", "discover_beach", "discover_mountains", "discover_city",
        "favorites", "documents", "screenshots",
        "duplicates", "videos", "locations", "devices",
    ]

    @pytest.mark.parametrize("branch", NEWLY_SHELL_PRIMARY)
    def test_retired_section_sets_emphasis_false(self, branch):
        """Retired sections should set legacy emphasis to False (shell is primary)."""
        layout = _make_mock_layout()
        _call_shell_branch(layout, branch)
        layout.google_shell_sidebar.set_legacy_emphasis.assert_called_with(False)


# ===========================================================================
# Test Class: _refresh_legacy_visibility_state
# ===========================================================================

@pytest.mark.unit
class TestRefreshLegacyVisibility:
    """Test _refresh_legacy_visibility_state helper."""

    def test_with_live_sections_sets_fallback_title(self):
        """When dates/folders/people are still live, title should say fallback."""
        layout = _make_mock_layout()
        layout._refresh_legacy_visibility_state()
        layout.legacy_tools_group.setTitle.assert_called_with("Legacy Tools, fallback")

    def test_without_legacy_group_does_not_raise(self):
        """Should not raise if legacy_tools_group is None."""
        layout = _make_mock_layout()
        layout.legacy_tools_group = None
        layout._refresh_legacy_visibility_state()  # should not raise

    def test_all_sections_retired_shows_plain_title(self):
        """If all sections are retired, title should be plain."""
        layout = _make_mock_layout(
            _retired_legacy_sections={"find", "devices", "videos", "locations",
                                       "duplicates", "dates", "folders", "people"}
        )
        layout._refresh_legacy_visibility_state()
        layout.legacy_tools_group.setTitle.assert_called_with("Legacy Tools")


# ===========================================================================
# Test Class: MainWindow Phase 8 router
# ===========================================================================

@pytest.mark.unit
class TestMainWindowPhase8Router:
    """Verify MainWindow router is Phase 8 labeled."""

    def test_router_has_phase_8_docstring(self):
        assert _mw_search_branch_router is not None
        doc = _mw_search_branch_router.__doc__ or ""
        assert "Phase 8" in doc or "phase 8" in doc

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
