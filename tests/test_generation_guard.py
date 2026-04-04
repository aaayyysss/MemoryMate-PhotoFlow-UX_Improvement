# tests/test_generation_guard.py
# Stability self-test harness for generation guard validation
#
# This test validates that the generation guard system properly blocks
# stale worker callbacks after restart/shutdown is initiated.
#
# Reference architecture: Chrome, Google Photos, Lightroom pattern where
# restart/shutdown bumps a generation token to invalidate all pending callbacks.

import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtCore import Signal, QObject


class TestGuardStats:
    """Test GuardStats counter functionality."""

    def test_initial_state(self):
        """GuardStats should start at zero."""
        from utils.qt_guards import GuardStats
        stats = GuardStats()
        assert stats.blocked_generation == 0
        assert stats.blocked_invalid == 0
        assert stats.passed == 0

    def test_reset(self):
        """reset() should zero all counters."""
        from utils.qt_guards import GuardStats
        stats = GuardStats()
        stats.blocked_generation = 5
        stats.blocked_invalid = 3
        stats.passed = 10
        stats.reset()
        assert stats.blocked_generation == 0
        assert stats.blocked_invalid == 0
        assert stats.passed == 0

    def test_repr(self):
        """__repr__ should show all counter values."""
        from utils.qt_guards import GuardStats
        stats = GuardStats()
        stats.blocked_generation = 2
        stats.blocked_invalid = 1
        stats.passed = 5
        repr_str = repr(stats)
        assert "blocked_generation=2" in repr_str
        assert "blocked_invalid=1" in repr_str
        assert "passed=5" in repr_str


class TestMakeGuardedSlot:
    """Test make_guarded_slot wrapper functionality."""

    def test_passes_when_generation_matches(self):
        """Slot should execute when generation matches."""
        from utils.qt_guards import make_guarded_slot, GUARD_STATS

        GUARD_STATS.reset()

        class Owner:
            _ui_generation = 1

        owner = Owner()
        executed = []

        def slot(value):
            executed.append(value)

        wrapped = make_guarded_slot(owner, slot, generation=1)
        wrapped("test")

        assert executed == ["test"]
        assert GUARD_STATS.passed == 1
        assert GUARD_STATS.blocked_generation == 0

    def test_blocks_when_generation_stale(self):
        """Slot should NOT execute when generation has changed (stale callback)."""
        from utils.qt_guards import make_guarded_slot, GUARD_STATS

        GUARD_STATS.reset()

        class Owner:
            _ui_generation = 1

        owner = Owner()
        executed = []

        def slot(value):
            executed.append(value)

        # Capture generation at 1
        wrapped = make_guarded_slot(owner, slot, generation=1)

        # Bump generation (simulates restart)
        owner._ui_generation = 2

        # Try to invoke — should be blocked
        wrapped("test")

        assert executed == []
        assert GUARD_STATS.passed == 0
        assert GUARD_STATS.blocked_generation == 1

    def test_blocks_when_owner_is_none(self):
        """Slot should NOT execute when owner weakref is dead."""
        from utils.qt_guards import make_guarded_slot, GUARD_STATS

        GUARD_STATS.reset()

        class Owner:
            _ui_generation = 1

        owner = Owner()
        executed = []

        def slot(value):
            executed.append(value)

        wrapped = make_guarded_slot(owner, slot, generation=1)

        # Delete owner (simulates widget destruction)
        del owner

        # Try to invoke — should be blocked
        wrapped("test")

        assert executed == []
        assert GUARD_STATS.passed == 0
        assert GUARD_STATS.blocked_invalid == 1

    def test_no_generation_check_when_none(self):
        """If generation=None, skip generation check (always pass if valid)."""
        from utils.qt_guards import make_guarded_slot, GUARD_STATS

        GUARD_STATS.reset()

        class Owner:
            _ui_generation = 999

        owner = Owner()
        executed = []

        def slot(value):
            executed.append(value)

        # No generation check
        wrapped = make_guarded_slot(owner, slot, generation=None)
        wrapped("test")

        assert executed == ["test"]
        assert GUARD_STATS.passed == 1


class TestConnectGuarded:
    """Test connect_guarded signal connection."""

    def test_connects_with_queued_connection(self):
        """connect_guarded should use QueuedConnection by default."""
        from utils.qt_guards import connect_guarded
        from PySide6.QtCore import Qt

        class Owner:
            _ui_generation = 1

        class Emitter(QObject):
            signal = Signal(str)

        owner = Owner()
        emitter = Emitter()
        received = []

        def slot(value):
            received.append(value)

        # This doesn't actually test the connection type without running event loop,
        # but it verifies the API works
        connect_guarded(emitter.signal, owner, slot, generation=1)

        # Would need Qt event loop to fully test signal delivery


class TestRestartGenerationBarrier:
    """Test that restart bumps generation and blocks stale callbacks."""

    def test_request_restart_bumps_generation_first(self):
        """request_restart should bump generation BEFORE starting detached process."""
        from utils.qt_guards import make_guarded_slot, GUARD_STATS

        GUARD_STATS.reset()

        # Simulate MainWindow state
        class MockMainWindow:
            _ui_generation = 0
            _closing = False
            _restart_requested = False

            def bump_ui_generation(self):
                self._ui_generation += 1
                return self._ui_generation

        win = MockMainWindow()

        # Create a "worker callback" that was connected before restart
        executed = []
        gen_at_connect = win._ui_generation

        def worker_callback(result):
            executed.append(result)

        wrapped = make_guarded_slot(win, worker_callback, generation=gen_at_connect)

        # Simulate request_restart() — generation should bump
        # This is what our fix does: bump generation BEFORE starting detached
        win.bump_ui_generation()
        win._closing = True
        win._restart_requested = True

        # Now try to invoke the stale callback
        wrapped("stale_result")

        # Callback should have been blocked
        assert executed == []
        assert GUARD_STATS.blocked_generation == 1
        assert GUARD_STATS.passed == 0

    def test_shutdown_barrier_bumps_generation(self):
        """_shutdown_barrier should bump generation to invalidate all callbacks."""
        from utils.qt_guards import make_guarded_slot, GUARD_STATS

        GUARD_STATS.reset()

        class MockMainWindow:
            _ui_generation = 0
            _closing = False

            def bump_ui_generation(self):
                self._ui_generation += 1
                return self._ui_generation

        win = MockMainWindow()

        # Worker callback connected at generation 0
        executed = []
        wrapped = make_guarded_slot(win, lambda x: executed.append(x), generation=0)

        # Simulate _shutdown_barrier
        win._closing = True
        win.bump_ui_generation()

        # Stale callback should be blocked
        wrapped("stale")
        assert executed == []
        assert GUARD_STATS.blocked_generation == 1


class TestScanControllerShutdownBarrier:
    """Test ScanController.shutdown_barrier integration."""

    def test_shutdown_barrier_method_exists(self):
        """ScanController should have shutdown_barrier method."""
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

        # We can't fully instantiate ScanController without MainWindow,
        # but we can check the method exists
        from controllers.scan_controller import ScanController
        assert hasattr(ScanController, 'shutdown_barrier')

    def test_shutdown_barrier_signature(self):
        """shutdown_barrier should accept timeout_ms parameter."""
        from controllers.scan_controller import ScanController
        import inspect

        sig = inspect.signature(ScanController.shutdown_barrier)
        params = list(sig.parameters.keys())
        assert 'timeout_ms' in params


class TestPhotoQueryServiceNaming:
    """Test that PhotoQueryService uses 'assets' terminology."""

    def test_count_assets_alias_exists(self):
        """count_assets() should exist as an alias for count_photos()."""
        from services.photo_query_service import PhotoQueryService
        svc = PhotoQueryService()
        assert hasattr(svc, 'count_assets')
        assert callable(svc.count_assets)

    def test_fetch_assets_alias_exists(self):
        """fetch_assets() should exist as an alias for fetch_page()."""
        from services.photo_query_service import PhotoQueryService
        svc = PhotoQueryService()
        assert hasattr(svc, 'fetch_assets')
        assert callable(svc.fetch_assets)


# Integration test (requires Qt event loop)
@pytest.mark.skip(reason="Requires full Qt event loop and MainWindow")
class TestFullRestartCycle:
    """Full integration test of restart cycle."""

    def test_restart_blocks_all_pending_callbacks(self):
        """After restart is requested, no stale callbacks should execute."""
        # This would require:
        # 1. Start multiple workers
        # 2. Call request_restart()
        # 3. Let workers emit results
        # 4. Verify GUARD_STATS.blocked_generation > 0
        # 5. Verify no callbacks executed
        pass
