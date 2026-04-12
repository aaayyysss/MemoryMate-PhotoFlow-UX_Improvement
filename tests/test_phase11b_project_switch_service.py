# tests/test_phase11b_project_switch_service.py
"""
Phase 11B MainWindow decomposition — ProjectSwitchService extraction tests.

Tests:
- Service can be instantiated with a mock MainWindow
- bootstrap_active_project: last-used project still exists → returned
- bootstrap_active_project: last-used project deleted → cleared and falls through
- bootstrap_active_project: exactly one project exists → auto-selected
- bootstrap_active_project: multiple projects → None (explicit onboarding)
- bootstrap_active_project: no projects → None (onboarding)
- on_project_changed_by_id: skips when already on that project
- on_project_changed_by_id: persists to session state
- on_project_changed_by_id: sets mw.active_project_id
- on_project_changed_by_id: delegates to layout.set_project
- on_project_changed_by_id: current-layout fallback resets grid branch to 'all'
- on_project_changed_by_id: google layout does not reset grid branch
- on_project_changed_by_id: schedules CLIP upgrade prompt
- on_project_changed_by_id: refreshes People shell
- on_project_changed_by_id: propagates to search_controller.set_active_project
- refresh_project_list: updates mw._projects from app_services.list_projects
- refresh_project_list: swallows errors
- restore_session_state: one-shot guard prevents double execution
- restore_session_state: no last section → early return
- restore_session_state: accordion found in google layout path
- restore_session_state: accordion found via sidebar.accordion
- restore_session_state: SidebarQt path defers to restore_selection_sidebarqt
- restore_selection: no selection → early return
- restore_selection: folder/date/person/video → correct signal emitted
- restore_selection_sidebarqt: missing sidebar → early return
- restore_selection_sidebarqt: folder/date/person/video routing
- MainWindow thin wrappers exist and delegate
- MainWindow.__init__ constructs ProjectSwitchService

No PySide6 or display server required.

Run with:
    pytest tests/test_phase11b_project_switch_service.py -v
"""

import sys
import types
import os
import ast
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Mock import bootstrap (matches other phase tests)
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

# Load the service directly from file — same pattern as Phase 11A, and we
# restore sys.modules["services"] after load so we don't poison downstream.
_services_dir = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "services"
)

_prev_services_module = sys.modules.get("services")
_services_pkg = types.ModuleType("services")
_services_pkg.__path__ = [_services_dir]
_services_pkg.__file__ = os.path.join(_services_dir, "__init__.py")
_services_pkg.__package__ = "services"
sys.modules["services"] = _services_pkg

_pss_spec = importlib.util.spec_from_file_location(
    "services.project_switch_service",
    os.path.join(_services_dir, "project_switch_service.py"),
    submodule_search_locations=[],
)
_pss_mod = importlib.util.module_from_spec(_pss_spec)
sys.modules["services.project_switch_service"] = _pss_mod
_pss_spec.loader.exec_module(_pss_mod)
ProjectSwitchService = _pss_mod.ProjectSwitchService

del sys.modules["services.project_switch_service"]
if _prev_services_module is not None:
    sys.modules["services"] = _prev_services_module
else:
    del sys.modules["services"]

_mw_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "main_window_qt.py"
)

with open(_mw_path, "r") as _fh:
    _mw_source = _fh.read()
_mw_tree = ast.parse(_mw_source)

sys.meta_path = [p for p in sys.meta_path if not isinstance(p, _MockImportFinder)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_method_in_class(tree, class_name, method_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == method_name:
                        return item
    return None


def _make_mock_main_window(**overrides):
    mw = MagicMock()
    # search controller
    mw.search_controller = MagicMock()

    # grid with project_id (default current project = 1 so the switch logic
    # sees a state transition unless the test overrides grid_project_id)
    grid = MagicMock()
    grid.project_id = overrides.get("grid_project_id", 1)
    grid.set_branch = MagicMock()
    mw.grid = grid

    sidebar = MagicMock()
    mw.sidebar = sidebar

    layout = MagicMock()
    layout.set_project = MagicMock()
    layout.__class__.__name__ = overrides.get("layout_class_name", "GooglePhotosLayout")
    layout_manager = MagicMock()
    layout_manager.get_current_layout = MagicMock(return_value=layout)
    layout_manager.get_current_layout_id = MagicMock(
        return_value=overrides.get("layout_id", "google")
    )
    layout_manager.current_layout = layout
    mw.layout_manager = layout_manager
    mw._mock_layout = layout

    mw.active_project_id = overrides.get("active_project_id", None)
    mw._session_restored = overrides.get("_session_restored", False)
    mw._maybe_prompt_clip_upgrade = MagicMock()
    mw._refresh_people_quick_section = MagicMock()
    return mw


# ===========================================================================
# Test Class: bootstrap_active_project
# ===========================================================================

@pytest.mark.unit
class TestBootstrapActiveProject:
    def _patch_session_and_repo(self, last_pid, proj_exists, projects_list):
        session_state = MagicMock()
        session_state.get_project_id = MagicMock(return_value=last_pid)
        session_state.set_project = MagicMock()

        session_mod = MagicMock()
        session_mod.get_session_state = MagicMock(return_value=session_state)

        proj_repo = MagicMock()
        proj_repo.get_by_id = MagicMock(return_value=(MagicMock() if proj_exists else None))
        proj_repo_mod = MagicMock()
        proj_repo_mod.ProjectRepository = MagicMock(return_value=proj_repo)

        base_repo_mod = MagicMock()
        base_repo_mod.DatabaseConnection = MagicMock()

        app_services_mod = MagicMock()
        app_services_mod.list_projects = MagicMock(return_value=projects_list)

        return {
            "session_state_manager": session_mod,
            "repository.project_repository": proj_repo_mod,
            "repository.base_repository": base_repo_mod,
            "app_services": app_services_mod,
        }, session_state

    def test_last_used_still_exists_returns_last_pid(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        mods, session_state = self._patch_session_and_repo(
            last_pid=5, proj_exists=True, projects_list=[]
        )
        with patch.dict(sys.modules, mods):
            result = svc.bootstrap_active_project()
        assert result == 5
        session_state.set_project.assert_not_called()

    def test_last_used_deleted_clears_and_falls_through_to_single(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        mods, session_state = self._patch_session_and_repo(
            last_pid=5, proj_exists=False, projects_list=[{"id": 7}]
        )
        with patch.dict(sys.modules, mods):
            result = svc.bootstrap_active_project()
        assert result == 7
        session_state.set_project.assert_any_call(None)
        session_state.set_project.assert_any_call(7)

    def test_single_project_auto_selected(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        mods, session_state = self._patch_session_and_repo(
            last_pid=None, proj_exists=False, projects_list=[{"id": 3}]
        )
        with patch.dict(sys.modules, mods):
            result = svc.bootstrap_active_project()
        assert result == 3
        session_state.set_project.assert_called_with(3)

    def test_multiple_projects_returns_none(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session_and_repo(
            last_pid=None, proj_exists=False,
            projects_list=[{"id": 1}, {"id": 2}],
        )
        with patch.dict(sys.modules, mods):
            result = svc.bootstrap_active_project()
        assert result is None

    def test_no_projects_returns_none(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session_and_repo(
            last_pid=None, proj_exists=False, projects_list=[]
        )
        with patch.dict(sys.modules, mods):
            result = svc.bootstrap_active_project()
        assert result is None


# ===========================================================================
# Test Class: on_project_changed_by_id
# ===========================================================================

@pytest.mark.unit
class TestOnProjectChangedById:
    def _patch_session(self):
        session_state = MagicMock()
        session_mod = MagicMock()
        session_mod.get_session_state = MagicMock(return_value=session_state)
        return {"session_state_manager": session_mod}, session_state

    def test_skips_when_already_on_project(self):
        mw = _make_mock_main_window(grid_project_id=7)
        svc = ProjectSwitchService(mw)
        mods, session_state = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(7)
        session_state.set_project.assert_not_called()
        mw._mock_layout.set_project.assert_not_called()

    def test_persists_to_session_state(self):
        mw = _make_mock_main_window(grid_project_id=1)
        svc = ProjectSwitchService(mw)
        mods, session_state = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        session_state.set_project.assert_called_once_with(9)

    def test_sets_active_project_id_on_mainwindow(self):
        mw = _make_mock_main_window(grid_project_id=1)
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        assert mw.active_project_id == 9

    def test_delegates_to_layout_set_project(self):
        mw = _make_mock_main_window(grid_project_id=1)
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        mw._mock_layout.set_project.assert_called_once_with(9)

    def test_current_layout_resets_grid_branch_to_all(self):
        mw = _make_mock_main_window(grid_project_id=1, layout_id="current")
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        mw.grid.set_branch.assert_called_once_with("all")

    def test_google_layout_does_not_reset_grid_branch(self):
        mw = _make_mock_main_window(grid_project_id=1, layout_id="google")
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        mw.grid.set_branch.assert_not_called()

    def test_google_legacy_layout_does_not_reset_grid_branch(self):
        mw = _make_mock_main_window(grid_project_id=1, layout_id="google_legacy")
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        mw.grid.set_branch.assert_not_called()

    def test_schedules_clip_upgrade_prompt(self):
        mw = _make_mock_main_window(grid_project_id=1)
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer") as qt:
            svc.on_project_changed_by_id(9)
        found = False
        for c in qt.singleShot.call_args_list:
            if c.args[0] == 1500 and c.args[1] is mw._maybe_prompt_clip_upgrade:
                found = True
        assert found, "expected QTimer.singleShot(1500, _maybe_prompt_clip_upgrade)"

    def test_refreshes_people_shell(self):
        mw = _make_mock_main_window(grid_project_id=1)
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        mw._refresh_people_quick_section.assert_called_once()

    def test_propagates_to_search_controller(self):
        mw = _make_mock_main_window(grid_project_id=1)
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session()
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer"):
            svc.on_project_changed_by_id(9)
        mw.search_controller.set_active_project.assert_called_once_with(9)


# ===========================================================================
# Test Class: refresh_project_list
# ===========================================================================

@pytest.mark.unit
class TestRefreshProjectList:
    def test_updates_mw_projects(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        fake_mod = MagicMock()
        fake_mod.list_projects = MagicMock(return_value=[{"id": 1}, {"id": 2}])
        with patch.dict(sys.modules, {"app_services": fake_mod}):
            svc.refresh_project_list()
        assert mw._projects == [{"id": 1}, {"id": 2}]

    def test_swallows_errors(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        fake_mod = MagicMock()
        fake_mod.list_projects = MagicMock(side_effect=RuntimeError("boom"))
        with patch.dict(sys.modules, {"app_services": fake_mod}):
            svc.refresh_project_list()  # should not raise


# ===========================================================================
# Test Class: restore_session_state
# ===========================================================================

@pytest.mark.unit
class TestRestoreSessionState:
    def _patch_session(self, section=None, selection=(None, None, None)):
        session_state = MagicMock()
        session_state.get_section = MagicMock(return_value=section)
        session_state.get_selection = MagicMock(return_value=selection)
        session_mod = MagicMock()
        session_mod.get_session_state = MagicMock(return_value=session_state)
        return {"session_state_manager": session_mod}, session_state

    def test_one_shot_guard_blocks_second_call(self):
        mw = _make_mock_main_window(_session_restored=True)
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session(section="folders")
        with patch.dict(sys.modules, mods):
            svc.restore_session_state()
        # session_state_manager shouldn't even be imported when guard trips

    def test_no_last_section_early_returns(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session(section=None)
        # Mock sidebar has no relevant attributes
        mw.sidebar = MagicMock(spec=[])
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer") as qt:
            svc.restore_session_state()
        qt.singleShot.assert_not_called()

    def test_google_layout_path_expands_section(self):
        mw = _make_mock_main_window()
        accordion = MagicMock()
        accordion._expand_section = MagicMock()
        accordion.section_logic = {}
        mw.layout_manager.current_layout = MagicMock()
        mw.layout_manager.current_layout.sidebar = accordion
        # Kill SidebarQt hints so we don't fall into that case
        mw.sidebar = MagicMock(spec=[])
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session(section="folders")
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer") as qt:
            svc.restore_session_state()
        accordion._expand_section.assert_called_once_with("folders")
        qt.singleShot.assert_called()  # schedules restore_selection

    def test_sidebarqt_path_defers_to_sidebarqt_restore(self):
        mw = _make_mock_main_window()
        # No accordion, but sidebar has .tree
        sidebar = MagicMock()
        sidebar.tree = MagicMock()
        # ensure no .accordion attribute
        del sidebar.accordion
        mw.sidebar = sidebar
        # Kill layout_manager.current_layout.sidebar accordion path
        mw.layout_manager.current_layout = MagicMock(spec=[])
        svc = ProjectSwitchService(mw)
        mods, _ = self._patch_session(section="folders")
        with patch.dict(sys.modules, mods), patch.object(_pss_mod, "QTimer") as qt:
            svc.restore_session_state()
        # Expect singleShot(300, <restore_selection_sidebarqt wrapper>)
        delays = [c.args[0] for c in qt.singleShot.call_args_list]
        assert 300 in delays


# ===========================================================================
# Test Class: restore_selection (accordion)
# ===========================================================================

@pytest.mark.unit
class TestRestoreSelection:
    def _make_accordion(self):
        acc = MagicMock()
        # Each section has the appropriate signal
        folders = MagicMock()
        folders.folderSelected = MagicMock()
        dates = MagicMock()
        dates.dateSelected = MagicMock()
        people = MagicMock()
        people.personSelected = MagicMock()
        videos = MagicMock()
        videos.videoFilterSelected = MagicMock()
        acc.section_logic = {
            "folders": folders,
            "dates": dates,
            "people": people,
            "videos": videos,
        }
        return acc

    def test_no_selection_early_returns(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=(None, None, None))
        acc = self._make_accordion()
        svc.restore_selection(ss, acc)
        acc.section_logic["folders"].folderSelected.emit.assert_not_called()

    def test_folder_selection_emits_folder_signal(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("folder", 42, "MyFolder"))
        acc = self._make_accordion()
        svc.restore_selection(ss, acc)
        acc.section_logic["folders"].folderSelected.emit.assert_called_once_with(42)

    def test_date_selection_emits_date_signal(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("date", "2024-01", "Jan 2024"))
        acc = self._make_accordion()
        svc.restore_selection(ss, acc)
        acc.section_logic["dates"].dateSelected.emit.assert_called_once_with("2024-01")

    def test_person_selection_emits_person_signal(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("person", 7, "Alice"))
        acc = self._make_accordion()
        svc.restore_selection(ss, acc)
        acc.section_logic["people"].personSelected.emit.assert_called_once_with(7)

    def test_video_selection_emits_video_signal(self):
        mw = _make_mock_main_window()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("video", "codec:h264", "H.264"))
        acc = self._make_accordion()
        svc.restore_selection(ss, acc)
        acc.section_logic["videos"].videoFilterSelected.emit.assert_called_once_with("codec:h264")


# ===========================================================================
# Test Class: restore_selection_sidebarqt
# ===========================================================================

@pytest.mark.unit
class TestRestoreSelectionSidebarQt:
    def _make_mw_with_sidebar(self):
        mw = _make_mock_main_window()
        sidebar = MagicMock()
        sidebar._on_item_clicked = MagicMock()
        sidebar.selectVideos = MagicMock()
        sidebar.selectFolder = MagicMock()
        sidebar.selectDate = MagicMock()
        sidebar.selectBranch = MagicMock()
        mw.sidebar = sidebar
        return mw, sidebar

    def test_missing_sidebar_early_returns(self):
        mw = _make_mock_main_window()
        mw.sidebar = MagicMock(spec=[])  # no _on_item_clicked
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("folder", 1, "X"))
        svc.restore_selection_sidebarqt(ss)  # no crash

    def test_folder_emits_select_folder(self):
        mw, sidebar = self._make_mw_with_sidebar()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("folder", 42, "MyFolder"))
        svc.restore_selection_sidebarqt(ss)
        sidebar.selectFolder.emit.assert_called_once_with(42)

    def test_date_emits_select_date(self):
        mw, sidebar = self._make_mw_with_sidebar()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("date", "2024-01", "Jan 2024"))
        svc.restore_selection_sidebarqt(ss)
        sidebar.selectDate.emit.assert_called_once_with("2024-01")

    def test_person_emits_select_branch(self):
        mw, sidebar = self._make_mw_with_sidebar()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("person", "person_7", "Alice"))
        svc.restore_selection_sidebarqt(ss)
        sidebar.selectBranch.emit.assert_called_once_with("person_7")

    def test_video_all_emits_all(self):
        mw, sidebar = self._make_mw_with_sidebar()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("video", "all", "All Videos"))
        svc.restore_selection_sidebarqt(ss)
        sidebar.selectVideos.emit.assert_called_once_with("all")

    def test_video_year_emits_year_prefix(self):
        mw, sidebar = self._make_mw_with_sidebar()
        svc = ProjectSwitchService(mw)
        ss = MagicMock()
        ss.get_selection = MagicMock(return_value=("video", "year:2024", "2024"))
        svc.restore_selection_sidebarqt(ss)
        sidebar.selectVideos.emit.assert_called_once_with("year:2024")


# ===========================================================================
# Test Class: MainWindow thin wrappers remain
# ===========================================================================

@pytest.mark.unit
class TestMainWindowThinWrappers:
    WRAPPER_METHODS = [
        "_bootstrap_active_project",
        "_on_project_changed_by_id",
        "_refresh_project_list",
        "_restore_session_state",
        "_restore_selection",
        "_restore_selection_sidebarqt",
    ]

    @pytest.mark.parametrize("name", WRAPPER_METHODS)
    def test_wrapper_method_exists(self, name):
        node = _find_method_in_class(_mw_tree, "MainWindow", name)
        assert node is not None, f"{name} should still exist on MainWindow"

    @pytest.mark.parametrize("name", WRAPPER_METHODS)
    def test_wrapper_is_thin(self, name):
        node = _find_method_in_class(_mw_tree, "MainWindow", name)
        src = ast.get_source_segment(_mw_source, node)
        assert "_project_switch_service" in src, \
            f"{name} must delegate to _project_switch_service"
        # No leftover heavy logic
        assert "session_state_manager" not in src
        assert "list_projects" not in src
        assert "_expand_section" not in src
        assert "selectVideos.emit" not in src

    def test_mainwindow_init_constructs_project_switch_service(self):
        init_node = _find_method_in_class(_mw_tree, "MainWindow", "__init__")
        src = ast.get_source_segment(_mw_source, init_node)
        assert "ProjectSwitchService" in src
        assert "_project_switch_service" in src
