# main_qt.py
# Version 10.01.01.05 dated 202601022
# Added centralized logging initialization

import sys
import os
from app_env import APP_DIR, app_path

# ========================================================================
# CRITICAL: Qt WebEngine D3D11 Fix
# ========================================================================
# Must be set BEFORE QApplication is created to prevent D3D11 errors:
# "D3D11 smoke test: Failed to create vertex shader"
#
# This issue occurs on:
# - Systems with older graphics drivers
# - Virtual machines or remote desktop sessions
# - Some Intel integrated graphics
# - Systems with incompatible DirectX versions
#
# Solution: Force software rendering using ANGLE WARP backend
# ========================================================================
os.environ['QT_ANGLE_PLATFORM'] = 'warp'  # Use WARP (Windows Advanced Rasterization Platform)

# Additional flags to suppress GLES3/GPU context errors in embedded maps
# These prevent the "Failed to create GLES3 context" errors seen in location editor
os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
    '--disable-gpu '
    '--disable-software-rasterizer '
    '--disable-gpu-compositing '
    '--disable-gpu-sandbox '
    '--disable-gpu-vsync '
    '--disable-accelerated-video-decode '
    '--disable-accelerated-video-encode '
    '--disable-accelerated-2d-canvas '
    '--disable-webgl '
    '--disable-webgl2 '
    '--num-raster-threads=1 '
    '--disable-features=VizDisplayCompositor,TranslateUI,BlinkGenPropertyTrees '
    '--renderer-process-limit=1 '
    '--in-process-gpu '
    '--ignore-gpu-blocklist'
)

# Additional Qt WebEngine environment variables for better compatibility
os.environ['QTWEBENGINE_DISABLE_SANDBOX'] = '1'  # Disable Chromium sandboxing (can cause issues in packaged apps)
os.environ['QT_WEBENGINE_CHROMIUM_BINARIES_PATH'] = ''  # Use bundled Chromium binaries
os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] += ' --no-sandbox'  # Extra sandbox disable flag

# Suppress Qt OpenGL/GPU related warnings and errors
os.environ['QT_DEBUG_PLUGINS'] = '0'  # Disable plugin debug output
os.environ['QSG_RENDER_LOOP'] = 'basic'  # Use basic render loop to avoid OpenGL issues
os.environ['QT_OPENGL'] = 'software'  # Force software OpenGL rendering
os.environ['QT_QUICK_BACKEND'] = 'software'  # Force software backend for Qt Quick

# ========================================================================
# Cap ML library native thread pools to prevent silent oversubscription.
# NumPy/SciPy (via OpenBLAS/MKL), ONNX Runtime, and OpenMP each spawn
# their own thread pools. Without caps, a single InsightFace call can
# create 8-16 native threads on top of our Python thread pools.
# Must be set BEFORE importing numpy/torch/onnxruntime.
#
# CRITICAL FIX 2026-03-09: Force OMP/MKL/OpenBLAS to 1 thread.
# Previous value of min(4, cpu_count) caused MKL to initialise its
# multi-thread pool.  When CLIP inference later calls set_num_threads(1)
# from QThreadPool worker threads, MKL's internal pool gets
# reconfigured across threads → native access violation (0xC0000005).
# Setting to 1 at process startup ensures MKL never creates a
# multi-thread pool in the first place.
# ========================================================================
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['VECLIB_MAXIMUM_THREADS'] = '1'
os.environ['NUMEXPR_NUM_THREADS'] = '1'

# Pre-pin torch if possible to prevent MKL from ever spawning threads
try:
    import torch
    torch.set_num_threads(1)
except ImportError:
    pass

# ONNX Runtime manages its own thread pool separately from MKL
_ml_threads = str(min(4, os.cpu_count() or 4))
os.environ.setdefault('ONNXRUNTIME_SESSION_THREAD_POOL_SIZE', _ml_threads)

# ========================================================================
# NumPy Version Compatibility Check
# ========================================================================
# Several ML libraries (onnxruntime, torch, transformers) are compiled
# against NumPy 1.x. Installing packages like EasyOCR or PySide6 can
# inadvertently upgrade NumPy to 2.x, breaking binary compatibility.
# Detect this early and warn clearly instead of cryptic crashes later.
# ========================================================================
try:
    import numpy as _np
    _np_version = tuple(int(x) for x in _np.__version__.split('.')[:2])
    if _np_version >= (2, 0):
        print("=" * 70)
        print(f"WARNING: NumPy {_np.__version__} detected (NumPy 2.x)")
        print("=" * 70)
        print("Several ML libraries (onnxruntime, torch, transformers) may")
        print("have been compiled against NumPy 1.x and will crash.")
        print()
        print("Symptoms: '_ARRAY_API not found', 'Could not infer dtype',")
        print("          'numpy.dtype size changed'")
        print()
        print("FIX:  pip install \"numpy<2\"")
        print("=" * 70)
    del _np, _np_version
except Exception:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from utils.qt_guards import connect_guarded
from main_window_qt import MainWindow

# ✅ Logging setup (must be first!)
from logging_config import setup_logging, get_logger, disable_external_logging
from settings_manager_qt import SettingsManager
from app_env import is_portable_python

print(f"[Environment] APP_DIR = {APP_DIR}")
print(f"[Environment] Portable Python: {is_portable_python()}")
print(f"[Environment] sys.executable = {sys.executable}")

# P2-25 FIX: Initialize settings once (will be reused at line 67)
# This prevents potential state inconsistencies from multiple instantiations
settings = SettingsManager()

# ✅ Initialize translation manager early with language from settings
from translation_manager import TranslationManager
language = settings.get("language", "en")
TranslationManager.get_instance(language)
print(f"🌍 Language initialized: {language}")

log_level = settings.get("log_level", "INFO")
log_to_console = settings.get("log_to_console", True)
log_colored = settings.get("log_colored_output", True)

# Setup logging before any other imports that might log
setup_logging(
    log_level=log_level,
    console=log_to_console,
    use_colors=log_colored
)
disable_external_logging()  # Reduce Qt/PIL noise

logger = get_logger(__name__)

# ✅ Other imports
from splash_qt import SplashScreen, StartupWorker

# ✅ Global exception hook to catch unhandled exceptions
import traceback
import datetime
import atexit
import faulthandler

# Enable faulthandler to write native crash info (segfaults, aborts) to stderr
# and to a persistent crash log.  This makes Qt/onnxruntime/InsightFace native
# crashes visible — they kill the process without a Python traceback.
try:
    _fault_log = open(app_path("crash_fault.log"), "a", encoding="utf-8")
    faulthandler.enable(file=_fault_log, all_threads=True)
    faulthandler.enable(file=sys.stderr, all_threads=True)
except Exception:
    faulthandler.enable()  # fallback to stderr only

def exception_hook(exctype, value, tb):
    """Global exception handler to catch and log unhandled exceptions"""
    print("=" * 80)
    print("UNHANDLED EXCEPTION CAUGHT:")
    print("=" * 80)
    traceback.print_exception(exctype, value, tb)
    logger.error("Unhandled exception", exc_info=(exctype, value, tb))
    print("=" * 80)

    # DIAGNOSTIC: Log stack trace to file for post-mortem analysis
    try:
        with open(app_path("crash_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"CRASH at {datetime.datetime.now()}\n")
            f.write(f"Exception Type: {exctype.__name__}\n")
            f.write(f"Exception Value: {value}\n")
            f.write(f"{'='*80}\n")
            traceback.print_exception(exctype, value, tb, file=f)
            f.write(f"{'='*80}\n\n")
    except:
        pass

    sys.__excepthook__(exctype, value, tb)

def log_shutdown():
    """Log when app shuts down normally (helps identify crashes vs normal exits)"""
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        with open(app_path('app_log.txt'), 'a', encoding='utf-8') as f:
            f.write(f"\n[{timestamp}] [SHUTDOWN] Normal exit with code 0\n")
        with open(app_path('crash_log.txt'), 'a', encoding='utf-8') as f:
            f.write(f"\n[{timestamp}] Normal exit with code 0\n\n")
    except:
        pass

def log_startup():
    """Log app startup"""
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        with open(app_path('app_log.txt'), 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] [STARTUP] MemoryMate-PhotoFlow starting...\n")
            f.write(f"{'='*80}\n")
    except:
        pass

# Install exception hook immediately
sys.excepthook = exception_hook

# Register shutdown handler to detect crashes vs normal exits
atexit.register(log_shutdown)


if __name__ == "__main__":

    # CRITICAL: Qt 6 has built-in high-DPI support enabled by default
    # The AA_EnableHighDpiScaling and AA_UseHighDpiPixmaps attributes are deprecated
    # and no longer needed in Qt 6 (they are automatically enabled)
    
    # Qt app
    app = QApplication(sys.argv)
    app.setApplicationName("Memory Mate - Photo Flow")

    # Log startup (helps distinguish crashes from normal exits)
    log_startup()

    # Print DPI/resolution information for debugging
    try:
        from utils.dpi_helper import DPIHelper
        DPIHelper.print_screen_info()
    except Exception as e:
        print(f"[Startup] Could not print screen info: {e}")

    # Install Qt message handler IMMEDIATELY after QApplication creation
    # This must happen before any image loading to suppress TIFF warnings
    from services import install_qt_message_handler
    install_qt_message_handler()
    logger.info("Qt message handler installed to suppress TIFF warnings")

    # Initialize ProjectState store (before any widgets or workers)
    from core.state_bus import init_store, init_bridge, get_store
    store = init_store()
    logger.info("[Startup] ProjectState store initialized")

    # 1️: Show splash screen immediately
    splash = SplashScreen()
    splash.show()

    # 2️: Initialize settings and startup worker
    # P2-25 FIX: Reuse the global settings instance created at line 15
    # (settings is already initialized above for logging configuration)

    worker = StartupWorker(settings)
    worker.progress.connect(splash.update_progress)
    worker.detail.connect(splash.add_detail)  # Connect detailed messages

    # 3️: Handle cancel button gracefully
    def on_cancel():
        logger.info("Startup cancelled by user")
        worker.cancel()
        splash.close()
        sys.exit(0)

    splash.cancel_btn.clicked.connect(on_cancel)    
    
    # 4️: When startup finishes
    def on_finished(ok: bool):
        # DON'T close splash yet - MainWindow creation still needs to happen
        if not ok:
            splash.close()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Startup Error", "Failed to initialize the app.")
            sys.exit(1)

        # Keep splash visible while creating MainWindow (heavy initialization)
        splash.update_progress(85, "Building user interface…")
        QApplication.processEvents()

        # Launch main window after worker completes
        print("[Startup] ⚠️ CREATING MainWindow instance...")
        win = MainWindow()
        print("[Startup] ✅ MainWindow instance created successfully")
        print(f"[Startup] MainWindow type: {type(win)}")
        print(f"[Startup] MainWindow is valid: {win is not None}")

        # Initialize Qt action bridge (requires QObject parent on GUI thread)
        bridge = init_bridge(store, parent=win)
        win._store = store
        win._bridge = bridge
        logger.info("[Startup] QtActionBridge attached to MainWindow")

        # Update progress while MainWindow initializes
        print("[Startup] Updating splash progress to 95%...")
        splash.update_progress(95, "Finalizing…")
        print("[Startup] Processing events...")
        QApplication.processEvents()
        print("[Startup] Events processed, ready to show window")

        # Show window and close splash
        print(f"[Startup] Showing main window...")
        print(f"[Startup] Window geometry before show(): {win.geometry()}")
        print(f"[Startup] Window visible before show(): {win.isVisible()}")

        win.show()

        print(f"[Startup] Window visible after show(): {win.isVisible()}")
        print(f"[Startup] Window geometry after show(): {win.geometry()}")
        print(f"[Startup] Window position: x={win.x()}, y={win.y()}, w={win.width()}, h={win.height()}")
        print(f"[Startup] Window on screen: {win.screen().name() if win.screen() else 'UNKNOWN'}")

        # CRITICAL FIX: Ensure window is on visible screen
        win.ensureOnScreen()

        # Ensure window is raised and activated
        win.raise_()
        win.activateWindow()
        print(f"[Startup] Window raised and activated")

        splash.update_progress(100, "Ready!")
        QApplication.processEvents()

        # Close splash after a brief delay
        QTimer.singleShot(300, splash.close)

        print(f"[Startup] ✅ Main window should now be visible")
        print(f"[Startup] If window is not visible, check:")
        print(f"[Startup]   1. Window position: ({win.x()}, {win.y()})")
        print(f"[Startup]   2. Window size: {win.width()}x{win.height()}")
        print(f"[Startup]   3. Screen geometry: {win.screen().availableGeometry() if win.screen() else 'N/A'}")
        print(f"[Startup]   4. Check if window is off-screen or on disconnected monitor")

        # Check FFmpeg availability asynchronously to prevent UI freezing
        def check_ffmpeg_async():
            """Launch async FFmpeg detection worker."""
            try:
                from workers.ffmpeg_detection_worker import FFmpegDetectionWorker
                from PySide6.QtCore import QThreadPool
                
                def on_detection_complete(ffmpeg_ok, ffprobe_ok, message):
                    """Handle async detection results."""
                    print(message)  # Always log the result
                    
                    # Only show dialog if there are issues
                    if not (ffmpeg_ok and ffprobe_ok):
                        from PySide6.QtWidgets import QMessageBox
                        msg_box = QMessageBox(win)
                        msg_box.setIcon(QMessageBox.Warning)
                        
                        # Check if it's a configuration issue
                        if "configured at" in message and "not working" in message:
                            msg_box.setWindowTitle("Video Support - FFprobe Configuration Issue")
                            msg_box.setText("The configured FFprobe path is not working.")
                            msg_box.setInformativeText(
                                "Please verify the path in Preferences:\n"
                                "  1. Press Ctrl+, to open Preferences\n"
                                "  2. Go to '🎬 Video Settings'\n"
                                "  3. Use 'Browse' to select ffprobe.exe (not ffmpeg.exe)\n"
                                "  4. Click 'Test' to verify it works\n"
                                "  5. Click OK and restart the app"
                            )
                        else:
                            msg_box.setWindowTitle("Video Support - FFmpeg Not Found")
                            msg_box.setText("FFmpeg and/or FFprobe are not installed on your system.")
                            msg_box.setInformativeText(
                                "Video features will be limited:\n"
                                "  • Videos can be indexed and played\n"
                                "  • Video thumbnails won't be generated\n"
                                "  • Duration/resolution won't be extracted\n\n"
                                "Options:\n"
                                "  1. Install FFmpeg system-wide (requires admin)\n"
                                "  2. Configure custom path in Preferences (Ctrl+,)"
                            )
                        
                        msg_box.setDetailedText(message)
                        msg_box.setStandardButtons(QMessageBox.Ok)
                        msg_box.exec()
                
                def on_detection_error(error_msg):
                    """Handle detection errors."""
                    logger.warning(f"FFmpeg detection failed: {error_msg}")
                    # Silently fail - don't bother user with detection errors
                
                # Create and configure worker
                worker = FFmpegDetectionWorker()
                worker.setAutoDelete(False)  # prevent C++ double-free
                gen = int(getattr(win, "_ui_generation", 0))
                connect_guarded(worker.signals.detection_complete, win, on_detection_complete, generation=gen)
                connect_guarded(worker.signals.error, win, on_detection_error, generation=gen)

                # Store reference on MainWindow to prevent premature GC.
                # Without this, Python may garbage-collect the worker before
                # QThreadPool finishes with it, causing a use-after-free
                # access violation in the native pool thread (crash on restart).
                win._ffmpeg_worker = worker

                # Launch in thread pool
                thread_pool = QThreadPool.globalInstance()
                thread_pool.start(worker)

                logger.info("[Main] Async FFmpeg detection worker launched")
                
            except Exception as e:
                logger.warning(f"Failed to launch async FFmpeg detection: {e}")
                # Fall back to simple logging
                print("⚠️ FFmpeg detection skipped due to initialization error")
        
        # Launch async FFmpeg detection after window is shown
        check_ffmpeg_async()

        # Check InsightFace availability in background thread (deferred 10s).
        # Importing insightface/onnxruntime can be slow and starve the GUI
        # thread via GIL contention. We run the check in a daemon thread and
        # only show a dialog once the UI is settled.
        from PySide6 import QtCore
        import threading

        def _show_insightface_warning_on_ui(message: str) -> None:
            try:
                from PySide6.QtWidgets import QMessageBox
                msg_box = QMessageBox(win)
                msg_box.setIcon(QMessageBox.Warning)

                if "Library Not Found" in message:
                    msg_box.setWindowTitle("Face Detection - InsightFace Not Found")
                    msg_box.setText("InsightFace library is not installed.")
                    msg_box.setInformativeText(
                        "Face detection features will be disabled:\n"
                        "  - Face detection won't work\n"
                        "  - People sidebar will be empty\n"
                        "  - Cannot group photos by faces\n\n"
                        "To enable face detection:\n"
                        "  1. Install InsightFace: pip install insightface onnxruntime\n"
                        "  2. Restart the application\n"
                        "  3. Go to Preferences (Ctrl+,) > Face Detection\n"
                        "  4. Click 'Download Models' to get face detection models"
                    )
                else:
                    msg_box.setWindowTitle("Face Detection - Models Not Found")
                    msg_box.setText("InsightFace models (buffalo_l) are not installed.")
                    msg_box.setInformativeText(
                        "Face detection is ready but needs models:\n"
                        "  - InsightFace library is installed\n"
                        "  - Models need to be downloaded (~200MB)\n\n"
                        "To download models:\n"
                        "  1. Go to Preferences (Ctrl+,)\n"
                        "  2. Navigate to 'Face Detection Models'\n"
                        "  3. Click 'Download Models'\n\n"
                        "Or run: python download_face_models.py"
                    )

                msg_box.setDetailedText(message)
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.exec()
            except Exception as e:
                logger.warning(f"Failed to show InsightFace warning dialog: {e}")

        def _insightface_check_bg() -> None:
            try:
                from utils.insightface_check import show_insightface_status_once
                message = show_insightface_status_once()
                if not message:
                    return
                print(message)
                if "⚠️" in message:
                    QtCore.QTimer.singleShot(0, lambda m=message: _show_insightface_warning_on_ui(m))
            except Exception as e:
                logger.warning(f"Failed to check InsightFace availability: {e}")

        # Give the UI 10s to stabilize, then run the check in background.
        QtCore.QTimer.singleShot(
            10000,
            lambda: threading.Thread(target=_insightface_check_bg, daemon=True).start()
        )

    worker.finished.connect(on_finished)
    
    # 5️: Start the background initialization thread
    worker.start()
    
    # 6️: Run the app
    print("[Main] Starting Qt event loop...")
    exit_code = app.exec()
    print(f"[Main] Qt event loop exited with code: {exit_code}")
    
    # DIAGNOSTIC: Log normal exit
    try:
        with open(app_path("crash_log.txt"), "a", encoding="utf-8") as f:
            import datetime
            f.write(f"\n[{datetime.datetime.now()}] Normal exit with code {exit_code}\n")
    except:
        pass
    
    sys.exit(exit_code)
