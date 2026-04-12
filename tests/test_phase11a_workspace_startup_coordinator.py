# tests/test_phase11a_workspace_startup_coordinator.py
"""
Phase 11A MainWindow decomposition — WorkspaceStartupCoordinator extraction tests.

Tests:
- Coordinator can be instantiated with a mock MainWindow
- after_first_paint respects _deferred_init_started re-entry guard
- after_first_paint sets _deferred_init_started True and schedules deferred_initialization
- after_first_paint schedules layout _on_startup_ready only when active project exists
- deferred_initialization short-circuits when _closing is True
- deferred_initialization short-circuits when no project (onboarding)
- deferred_initialization happy path chains init_minimal_db_handle + 4 QTimer schedules
- init_minimal_db_handle creates db handle and reloads sidebar date tree
- init_minimal_db_handle skips reload when project_id is None
- enqueue_startup_maintenance_job short-circuits on _closing
- enqueue_startup_maintenance_job registers tracked job on JobManager
- warmup_clip_in_background short-circuits on _closing
- warmup_clip_in_background respects enable_semantic_embeddings setting
- deferred_cache_purge short-circuits on _closing
- deferred_cache_purge respects cache_auto_cleanup setting
- MainWindow thin-wrapper methods exist and delegate to coordinator
- MainWindow.__init__ constructs coordinator under _workspace_startup_coordinator

No PySide6 or display server required — uses mock import finder pattern.

Run with:
    pytest tests/test_phase11a_workspace_startup_coordinator.py -v
    pytest tests/test_phase11a_workspace_startup_coordinator.py -v -m unit
"""

import sys
import types
import os
import ast
import textwrap
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

# Load the coordinator directly from its file path so we get the real class
# even when services/__init__.py does eager PySide6/numpy imports.
#
# IMPORTANT: we deliberately DO NOT leave `services` sitting in sys.modules
# after load — existing services/__init__.py eagerly imports modules that
# need PySide6/numpy, and if we left a namespace `services` module behind,
# subsequent test collections that do `from services.X import Y` would pick
# up stale mocks (polluting test_photo_scan_service, test_thumbnail_service).
# We register a namespace `services` only for the duration of the
# exec_module call and then restore whatever was there before.
_services_dir = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "services"
)

_prev_services_module = sys.modules.get("services")
_services_pkg = types.ModuleType("services")
_services_pkg.__path__ = [_services_dir]
_services_pkg.__file__ = os.path.join(_services_dir, "__init__.py")
_services_pkg.__package__ = "services"
sys.modules["services"] = _services_pkg

_wsc_spec = importlib.util.spec_from_file_location(
    "services.workspace_startup_coordinator",
    os.path.join(_services_dir, "workspace_startup_coordinator.py"),
    submodule_search_locations=[],
)
_wsc_mod = importlib.util.module_from_spec(_wsc_spec)
sys.modules["services.workspace_startup_coordinator"] = _wsc_mod
_wsc_spec.loader.exec_module(_wsc_mod)
WorkspaceStartupCoordinator = _wsc_mod.WorkspaceStartupCoordinator

# Clean up temporary sys.modules entries so we don't poison subsequent test
# module collections.
del sys.modules["services.workspace_startup_coordinator"]
if _prev_services_module is not None:
    sys.modules["services"] = _prev_services_module
else:
    del sys.modules["services"]

# MainWindow source is parsed via AST — we don't instantiate the class
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
    """Return the AST FunctionDef for a method on a class, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == method_name:
                        return item
    return None


def _method_source(tree, source, class_name, method_name):
    node = _find_method_in_class(tree, class_name, method_name)
    if node is None:
        return None
    return ast.get_source_segment(source, node)


def _make_mock_main_window(**overrides):
    """Build a mock MainWindow sufficient for coordinator delegation."""
    mw = MagicMock()
    mw._closing = overrides.get("_closing", False)
    mw._deferred_init_started = overrides.get("_deferred_init_started", False)
    mw.active_project_id = overrides.get("active_project_id", 1)

    # grid with project_id — default to 1 (active project)
    grid = MagicMock()
    grid.project_id = overrides.get("grid_project_id", 1)
    mw.grid = grid

    # sidebar with reload_date_tree
    sidebar = MagicMock()
    sidebar.reload_date_tree = MagicMock()
    mw.sidebar = sidebar

    # layout manager that returns a layout with _on_startup_ready
    layout = MagicMock()
    layout._on_startup_ready = MagicMock()
    layout_manager = MagicMock()
    layout_manager.get_current_layout = MagicMock(return_value=layout)
    mw.layout_manager = layout_manager
    mw._mock_layout = layout

    mw._update_status_bar = MagicMock()
    mw._restore_session_state = MagicMock()
    return mw


# ===========================================================================
# Test Class: Instantiation
# ===========================================================================

@pytest.mark.unit
class TestWorkspaceStartupCoordinatorInstantiation:
    def test_construct_with_main_window(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        assert coord.mw is mw

    def test_has_all_extracted_methods(self):
        coord = WorkspaceStartupCoordinator(_make_mock_main_window())
        for name in [
            "after_first_paint",
            "deferred_initialization",
            "init_minimal_db_handle",
            "enqueue_startup_maintenance_job",
            "warmup_clip_in_background",
            "deferred_cache_purge",
        ]:
            assert callable(getattr(coord, name)), f"missing {name}"


# ===========================================================================
# Test Class: after_first_paint
# ===========================================================================

@pytest.mark.unit
class TestAfterFirstPaint:
    def test_reentry_guard_short_circuits(self):
        mw = _make_mock_main_window(_deferred_init_started=True)
        coord = WorkspaceStartupCoordinator(mw)
        with patch.object(_wsc_mod, "QTimer") as _qt:
            coord.after_first_paint()
            _qt.singleShot.assert_not_called()

    def test_sets_deferred_init_started(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        with patch.object(_wsc_mod, "QTimer"), patch.object(_wsc_mod, "QThreadPool"):
            coord.after_first_paint()
        assert mw._deferred_init_started is True

    def test_schedules_deferred_initialization(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        with patch.object(_wsc_mod, "QTimer") as qt, \
             patch.object(_wsc_mod, "QThreadPool"):
            coord.after_first_paint()
            # Expect at least one singleShot(250, coord.deferred_initialization)
            delays = [c.args[0] for c in qt.singleShot.call_args_list]
            assert 250 in delays

    def test_schedules_layout_startup_ready_when_project_active(self):
        mw = _make_mock_main_window(active_project_id=7)
        coord = WorkspaceStartupCoordinator(mw)
        with patch.object(_wsc_mod, "QTimer") as qt, \
             patch.object(_wsc_mod, "QThreadPool"):
            coord.after_first_paint()
            # Expect singleShot(50, layout._on_startup_ready) call
            found = False
            for c in qt.singleShot.call_args_list:
                if c.args[0] == 50 and c.args[1] is mw._mock_layout._on_startup_ready:
                    found = True
            assert found, "expected singleShot(50, layout._on_startup_ready)"

    def test_skips_layout_startup_ready_when_no_project(self):
        mw = _make_mock_main_window(active_project_id=None)
        coord = WorkspaceStartupCoordinator(mw)
        with patch.object(_wsc_mod, "QTimer") as qt, \
             patch.object(_wsc_mod, "QThreadPool"):
            coord.after_first_paint()
            # No call with delay 50 targeting the layout
            for c in qt.singleShot.call_args_list:
                if c.args[0] == 50:
                    assert c.args[1] is not mw._mock_layout._on_startup_ready


# ===========================================================================
# Test Class: deferred_initialization
# ===========================================================================

@pytest.mark.unit
class TestDeferredInitialization:
    def test_short_circuits_on_closing(self):
        mw = _make_mock_main_window(_closing=True)
        coord = WorkspaceStartupCoordinator(mw)
        coord.init_minimal_db_handle = MagicMock()
        with patch.object(_wsc_mod, "QTimer") as qt:
            coord.deferred_initialization()
        coord.init_minimal_db_handle.assert_not_called()
        mw._update_status_bar.assert_not_called()

    def test_onboarding_path_calls_update_status_bar_only(self):
        mw = _make_mock_main_window(grid_project_id=None)
        coord = WorkspaceStartupCoordinator(mw)
        coord.init_minimal_db_handle = MagicMock()
        with patch.object(_wsc_mod, "QTimer") as qt:
            coord.deferred_initialization()
        coord.init_minimal_db_handle.assert_not_called()
        mw._update_status_bar.assert_called_once()
        qt.singleShot.assert_not_called()

    def test_happy_path_schedules_full_chain(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        coord.init_minimal_db_handle = MagicMock()
        with patch.object(_wsc_mod, "QTimer") as qt:
            coord.deferred_initialization()
        coord.init_minimal_db_handle.assert_called_once()
        delays = [c.args[0] for c in qt.singleShot.call_args_list]
        # Expect 300 (restore session), 2000 (maintenance), 3000 (warmup), 5000 (cache purge)
        for d in (300, 2000, 3000, 5000):
            assert d in delays, f"expected QTimer.singleShot delay {d}"

    def test_happy_path_schedules_restore_session_on_mainwindow(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        coord.init_minimal_db_handle = MagicMock()
        with patch.object(_wsc_mod, "QTimer") as qt:
            coord.deferred_initialization()
        found = False
        for c in qt.singleShot.call_args_list:
            if c.args[0] == 300 and c.args[1] is mw._restore_session_state:
                found = True
        assert found, "deferred_initialization must schedule mw._restore_session_state"


# ===========================================================================
# Test Class: init_minimal_db_handle
# ===========================================================================

@pytest.mark.unit
class TestInitMinimalDbHandle:
    def test_assigns_db_and_reloads_sidebar(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        with patch.dict(sys.modules, {"reference_db": MagicMock(ReferenceDB=MagicMock())}):
            coord.init_minimal_db_handle()
        assert mw.db is not None
        mw.sidebar.reload_date_tree.assert_called_once()

    def test_skips_reload_when_project_none(self):
        mw = _make_mock_main_window(grid_project_id=None)
        coord = WorkspaceStartupCoordinator(mw)
        with patch.dict(sys.modules, {"reference_db": MagicMock(ReferenceDB=MagicMock())}):
            coord.init_minimal_db_handle()
        mw.sidebar.reload_date_tree.assert_not_called()

    def test_reload_failure_does_not_raise(self):
        mw = _make_mock_main_window()
        mw.sidebar.reload_date_tree.side_effect = RuntimeError("boom")
        coord = WorkspaceStartupCoordinator(mw)
        with patch.dict(sys.modules, {"reference_db": MagicMock(ReferenceDB=MagicMock())}):
            coord.init_minimal_db_handle()  # should swallow error


# ===========================================================================
# Test Class: enqueue_startup_maintenance_job
# ===========================================================================

@pytest.mark.unit
class TestEnqueueStartupMaintenanceJob:
    def test_short_circuits_on_closing(self):
        mw = _make_mock_main_window(_closing=True)
        coord = WorkspaceStartupCoordinator(mw)
        fake_jm_mod = MagicMock()
        fake_jm_mod.get_job_manager = MagicMock()
        with patch.dict(sys.modules, {"services.job_manager": fake_jm_mod}):
            coord.enqueue_startup_maintenance_job()
        fake_jm_mod.get_job_manager.assert_not_called()

    def test_registers_tracked_job(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        jm = MagicMock()
        jm.register_tracked_job = MagicMock(return_value="job-1")
        fake_jm_mod = MagicMock()
        fake_jm_mod.get_job_manager = MagicMock(return_value=jm)
        # Patch threading.Thread to avoid actually spawning
        with patch.dict(sys.modules, {"services.job_manager": fake_jm_mod}), \
             patch.object(_wsc_mod.threading, "Thread") as thread_cls:
            coord.enqueue_startup_maintenance_job()
        jm.register_tracked_job.assert_called_once()
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()


# ===========================================================================
# Test Class: warmup_clip_in_background
# ===========================================================================

@pytest.mark.unit
class TestWarmupClipInBackground:
    def test_short_circuits_on_closing(self):
        mw = _make_mock_main_window(_closing=True)
        coord = WorkspaceStartupCoordinator(mw)
        fake_settings_mod = MagicMock()
        with patch.dict(sys.modules, {"settings_manager_qt": fake_settings_mod}):
            coord.warmup_clip_in_background()
        fake_settings_mod.SettingsManager.assert_not_called()

    def test_skips_when_semantic_embeddings_disabled(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        settings = MagicMock()
        settings.get = MagicMock(return_value=False)  # enable_semantic_embeddings=False
        fake_settings_mod = MagicMock()
        fake_settings_mod.SettingsManager = MagicMock(return_value=settings)
        fake_worker_mod = MagicMock()
        with patch.dict(sys.modules, {
            "settings_manager_qt": fake_settings_mod,
            "workers.model_warmup_worker": fake_worker_mod,
        }):
            coord.warmup_clip_in_background()
        fake_worker_mod.launch_model_warmup.assert_not_called()

    def test_launches_warmup_when_enabled(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        settings = MagicMock()
        settings.get = MagicMock(return_value=True)
        fake_settings_mod = MagicMock()
        fake_settings_mod.SettingsManager = MagicMock(return_value=settings)
        fake_worker_mod = MagicMock()
        fake_worker_mod.launch_model_warmup = MagicMock(return_value="WORKER_HANDLE")
        with patch.dict(sys.modules, {
            "settings_manager_qt": fake_settings_mod,
            "workers.model_warmup_worker": fake_worker_mod,
        }):
            coord.warmup_clip_in_background()
        fake_worker_mod.launch_model_warmup.assert_called_once()
        # Worker handle should be stashed on MainWindow for GC lifetime
        assert mw._clip_warmup_worker == "WORKER_HANDLE"


# ===========================================================================
# Test Class: deferred_cache_purge
# ===========================================================================

@pytest.mark.unit
class TestDeferredCachePurge:
    def test_short_circuits_on_closing(self):
        mw = _make_mock_main_window(_closing=True)
        coord = WorkspaceStartupCoordinator(mw)
        fake_settings_mod = MagicMock()
        with patch.dict(sys.modules, {"settings_manager_qt": fake_settings_mod}):
            coord.deferred_cache_purge()
        fake_settings_mod.SettingsManager.assert_not_called()

    def test_skips_when_cache_auto_cleanup_disabled(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        settings = MagicMock()
        settings.get = MagicMock(return_value=False)
        fake_settings_mod = MagicMock()
        fake_settings_mod.SettingsManager = MagicMock(return_value=settings)
        with patch.dict(sys.modules, {"settings_manager_qt": fake_settings_mod}), \
             patch.object(_wsc_mod.threading, "Thread") as thread_cls:
            coord.deferred_cache_purge()
        thread_cls.assert_not_called()

    def test_spawns_purge_thread_when_enabled(self):
        mw = _make_mock_main_window()
        coord = WorkspaceStartupCoordinator(mw)
        settings = MagicMock()
        settings.get = MagicMock(return_value=True)
        fake_settings_mod = MagicMock()
        fake_settings_mod.SettingsManager = MagicMock(return_value=settings)
        with patch.dict(sys.modules, {"settings_manager_qt": fake_settings_mod}), \
             patch.object(_wsc_mod.threading, "Thread") as thread_cls:
            coord.deferred_cache_purge()
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()


# ===========================================================================
# Test Class: MainWindow thin wrappers remain
# ===========================================================================

@pytest.mark.unit
class TestMainWindowThinWrappers:
    """After Phase 1A, MainWindow keeps delegator methods that call the coordinator."""

    WRAPPER_METHODS = [
        "_after_first_paint",
        "_deferred_initialization",
        "_init_minimal_db_handle",
        "_enqueue_startup_maintenance_job",
        "_warmup_clip_in_background",
        "_deferred_cache_purge",
    ]

    @pytest.mark.parametrize("name", WRAPPER_METHODS)
    def test_wrapper_method_exists(self, name):
        node = _find_method_in_class(_mw_tree, "MainWindow", name)
        assert node is not None, f"{name} should still exist on MainWindow"

    @pytest.mark.parametrize("name", WRAPPER_METHODS)
    def test_wrapper_is_thin(self, name):
        """Wrapper body should not contain legacy startup logic — only delegate."""
        node = _find_method_in_class(_mw_tree, "MainWindow", name)
        src = ast.get_source_segment(_mw_source, node)
        assert "_workspace_startup_coordinator" in src, \
            f"{name} must delegate to _workspace_startup_coordinator"
        # No leftover heavy imports/calls
        assert "launch_model_warmup" not in src
        assert "register_tracked_job" not in src
        assert "single_pass_backfill_created_fields" not in src

    def test_mainwindow_init_constructs_coordinator(self):
        init_node = _find_method_in_class(_mw_tree, "MainWindow", "__init__")
        src = ast.get_source_segment(_mw_source, init_node)
        assert "WorkspaceStartupCoordinator" in src
        assert "_workspace_startup_coordinator" in src
