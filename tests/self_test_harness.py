"""Self-test harness for UI generation gate and restart safety.

Validates that:
1. Generation guards block stale callbacks after bump
2. connect_guarded_dynamic properly captures and compares generations
3. GUARD_STATS tracks blocked vs passed callbacks

Run:
    python -m tests.self_test_harness
"""

import sys
import tempfile
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _wait_until(predicate, timeout_s=20.0, tick_ms=50):
    """Wait until predicate returns True or timeout expires."""
    from PySide6.QtCore import QEventLoop, QTimer

    deadline = time.time() + timeout_s
    loop = QEventLoop()

    def _tick():
        if predicate() or time.time() >= deadline:
            loop.quit()

    t = QTimer()
    t.setInterval(tick_ms)
    t.timeout.connect(_tick)
    t.start()
    _tick()  # Check immediately
    loop.exec()
    t.stop()
    return predicate()


def _make_sample_images(folder: Path, n=6):
    """Create sample test images using PIL if available."""
    try:
        from PIL import Image
    except ImportError:
        print("[SelfTest] PIL not available, skipping image creation")
        return False

    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = Image.new("RGB", (256, 256), (i * 30 % 255, i * 60 % 255, i * 90 % 255))
        img.save(folder / f"sample_{i}.jpg", quality=85)
    return True


def test_generation_guard_basics():
    """Test that GUARD_STATS tracks blocked callbacks correctly."""
    from utils.qt_guards import GUARD_STATS, connect_guarded_dynamic
    from PySide6.QtCore import Signal, QObject

    class TestEmitter(QObject):
        test_signal = Signal(str)

    GUARD_STATS.reset()
    emitter = TestEmitter()

    # Track if callback was called
    called = {"count": 0, "args": []}

    def callback(msg):
        called["count"] += 1
        called["args"].append(msg)

    # Simulate a generation getter that starts at 0
    current_gen = [0]

    def get_gen():
        return current_gen[0]

    # Connect with generation captured at 0
    connect_guarded_dynamic(
        emitter.test_signal,
        callback,
        get_gen,
        name="test_basic",
    )

    # Emit while generation matches - should pass
    emitter.test_signal.emit("msg1")
    _wait_until(lambda: called["count"] >= 1, timeout_s=1.0)
    assert called["count"] == 1, f"Expected 1 call, got {called['count']}"
    assert GUARD_STATS.passed >= 1, "Expected at least 1 passed"
    initial_blocked = GUARD_STATS.blocked_generation

    # Bump generation
    current_gen[0] = 1

    # Emit after bump - should be blocked
    emitter.test_signal.emit("msg2")
    _wait_until(lambda: True, timeout_s=0.3)  # Give time for signal delivery
    assert called["count"] == 1, f"Expected 1 call after bump, got {called['count']}"
    assert GUARD_STATS.blocked_generation > initial_blocked, "Expected blocked_generation to increase"

    print("[SelfTest] test_generation_guard_basics PASSED")
    return True


def test_video_path_normalization():
    """Test that video paths are normalized like photos (forward slashes, lowercase on Windows)."""
    import platform
    from repository.video_repository import VideoRepository

    # Test the static method directly
    if platform.system() == 'Windows':
        # On Windows, backslashes should become forward slashes and lowercase
        test_path = r"C:\Users\Test\Videos\clip.mp4"
        normalized = VideoRepository._normalize_path(test_path)
        expected = "c:/users/test/videos/clip.mp4"
        assert normalized == expected, f"Expected '{expected}', got '{normalized}'"
        print(f"[SelfTest] Windows path normalization: '{test_path}' -> '{normalized}'")
    else:
        # On Unix, paths should be unchanged
        test_path = "/home/user/Videos/clip.mp4"
        normalized = VideoRepository._normalize_path(test_path)
        assert normalized == test_path, f"Expected unchanged '{test_path}', got '{normalized}'"
        print(f"[SelfTest] Unix path normalization: '{test_path}' -> '{normalized}' (unchanged)")

    print("[SelfTest] test_video_path_normalization PASSED")
    return True


def test_scan_controller_has_generation_getter():
    """Test that ScanController has _get_ui_generation helper."""
    # This is a structural test - just verify the attribute exists
    # Full integration test requires MainWindow

    from unittest.mock import MagicMock

    # Create mock main window with ui_generation
    mock_main = MagicMock()
    mock_main.ui_generation.return_value = 42

    from controllers.scan_controller import ScanController
    controller = ScanController(mock_main)

    assert hasattr(controller, '_get_ui_generation'), "ScanController missing _get_ui_generation"
    gen = controller._get_ui_generation()
    assert gen == 42, f"Expected 42, got {gen}"

    print("[SelfTest] test_scan_controller_has_generation_getter PASSED")
    return True


def main():
    """Run all self-tests."""
    try:
        from PySide6.QtWidgets import QApplication
        HAS_QT = True
    except ImportError:
        HAS_QT = False
        print("[SelfTest] PySide6 not available - running non-Qt tests only")

    if HAS_QT:
        # Create/get QApplication (required for Qt signals)
        app = QApplication.instance() or QApplication(sys.argv)

    print("=" * 60)
    print("MemoryMate-PhotoFlow Self-Test Harness")
    print("=" * 60)

    # Tests that require Qt
    qt_tests = [
        ("Generation Guard Basics", test_generation_guard_basics),
        ("ScanController Generation Getter", test_scan_controller_has_generation_getter),
    ]

    # Tests that don't require Qt
    non_qt_tests = [
        ("Video Path Normalization", test_video_path_normalization),
    ]

    tests = non_qt_tests + (qt_tests if HAS_QT else [])
    if not HAS_QT:
        print(f"[SelfTest] Skipping {len(qt_tests)} Qt-dependent tests")

    passed = 0
    failed = 0
    skipped = 0

    for name, test_fn in tests:
        print(f"\n[Test] Running: {name}")
        try:
            if test_fn():
                passed += 1
            else:
                failed += 1
                print(f"[Test] FAILED: {name}")
        except ImportError as e:
            skipped += 1
            print(f"[Test] SKIPPED: {name} - missing dependency: {e}")
        except Exception as e:
            failed += 1
            print(f"[Test] FAILED: {name} - {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)

    if failed == 0:
        print("\nSELFTEST OK")
        return 0
    else:
        print("\nSELFTEST FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
