# scan_controller.py
# Version 10.01.01.05 dated 20260127

"""
ScanController - Photo/Video Scanning Orchestration

Extracted from main_window_qt.py (Phase 1, Step 1.3)

Responsibilities:
- Start/cancel scan operations
- Progress tracking and UI updates
- Post-scan cleanup and processing
- Database schema initialization
- Face detection integration
- Sidebar/grid refresh coordination

Version: 10.01.01.03
"""

import logging
import os
import time
from datetime import datetime
from typing import List
from PySide6.QtCore import QThread, Qt, QTimer, QThreadPool, Slot, QObject, Signal
from PySide6.QtWidgets import (
    QMessageBox, QDialog, QApplication
)
from translation_manager import tr
from utils.ui_safety import is_alive, generation_ok


def _dispatch_store_action(action):
    """Best-effort dispatch to ProjectState store (no-op if store not initialized)."""
    try:
        from core.state_bus import get_bridge
        get_bridge().dispatch_async(action)
    except Exception:
        pass  # Store not initialized yet or shutting down


class ScanController(QObject):
    """
    Wraps scan orchestration: start, cancel, cleanup, progress wiring.
    Keeps MainWindow slimmer.
    """
    # Signal for cross-thread progress updates
    progress_update_signal = Signal(int, str)

    def __init__(self, main):
        super().__init__()  # CRITICAL: Initialize QObject
        self.main = main

        # Helper to get current UI generation for guarded callbacks
        # Returns 0 if main doesn't have ui_generation (defensive)
        self._get_ui_generation = lambda: (
            main.ui_generation() if hasattr(main, 'ui_generation') else 0
        )

        # Connect signal to handler with QueuedConnection for thread safety
        # Note: Generation checking happens inside _on_progress via _expected_generation
        # which is captured at scan START time, not at connect time
        self.progress_update_signal.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.thread = None
        self.worker = None
        self.db_writer = None
        self.cancel_requested = False
        self._expected_generation = None
        self.logger = logging.getLogger(__name__)

        # CRITICAL FIX: Progress dialog threshold to prevent UI freeze on tiny scans
        # Only show progress dialog if file count exceeds this threshold
        # Threshold of 20 shows dialog for medium/large scans
        self.PROGRESS_DIALOG_THRESHOLD = 20
        self._total_files_found = 0
        self._progress_events: List[str] = []

        # PHASE 2 Task 2.2: Debounce reload operations
        # Track pending async operations to coordinate single refresh after ALL complete
        self._scan_operations_pending = set()
        self._scan_refresh_scheduled = False
        self._scan_result_cached = None  # Cache scan results for final refresh

    def _get_ocr_enabled(self) -> bool:
        """Check if OCR is enabled in settings."""
        try:
            from settings_manager_qt import SettingsManager
            return SettingsManager().get("ocr_enabled", False)
        except Exception:
            return False

    def _get_ocr_languages(self):
        """Get OCR language list from settings."""
        try:
            from settings_manager_qt import SettingsManager
            langs = SettingsManager().get("ocr_languages", "en") or "en"
            return langs.split(",")
        except Exception:
            return ["en"]

    # ------------------------------------------------------------------
    # Helper: resolve project_id from the *active* layout, not from the
    # hidden CurrentLayout grid/sidebar which may still have project_id=None
    # when Google Layout is active.
    # ------------------------------------------------------------------
    def _get_active_project_id(self):
        """Return project_id from the active layout, grid, or DB default."""
        # 1. Active layout (works for both Google and Current)
        if hasattr(self.main, 'layout_manager') and self.main.layout_manager:
            layout = self.main.layout_manager.get_current_layout()
            if layout and hasattr(layout, 'get_current_project'):
                pid = layout.get_current_project()
                if pid is not None:
                    return pid
        # 2. Grid fallback
        if hasattr(self.main, 'grid') and self.main.grid:
            pid = getattr(self.main.grid, 'project_id', None)
            if pid is not None:
                return pid
        # 3. DB default
        from app_services import get_default_project_id
        return get_default_project_id()

    def _test_progress_slot(self, pct: int, msg: str):
        """Test slot to verify Qt signal delivery is working."""
        # Removed verbose debug logging - signal delivery confirmed working
        pass

    @Slot(int, str)
    def update_progress_safe(self, pct: int, msg: str):
        """
        Thread-safe progress update method.

        Can be called from any thread - automatically marshals to main thread if needed.
        """
        if not is_alive(self.main) or not generation_ok(self.main, self._expected_generation):
            return

        from PySide6.QtCore import QThread, QMetaObject, Qt
        from PySide6.QtWidgets import QApplication

        # Check if we're in the main thread
        # CRITICAL: Use QThread.currentThread(), NOT self.thread (which is the worker thread object!)
        current_thread = QThread.currentThread()
        main_thread = QApplication.instance().thread()

        if current_thread != main_thread:
            # Called from worker thread - marshal to main thread via signal
            # Emit signal - QueuedConnection ensures it runs in main thread
            self.progress_update_signal.emit(pct, msg)
        else:
            # Already in main thread - direct call
            self._on_progress(pct, msg)

    @Slot(int, str)
    def _on_progress_main_thread(self, pct: int, msg: str):
        """Helper to ensure we're in main thread when calling _on_progress."""
        if not is_alive(self.main) or not generation_ok(self.main, self._expected_generation):
            return
        self._on_progress(pct, msg)

    def start_scan(self, folder, incremental: bool):
        """Entry point called from MainWindow toolbar action."""
        # Phase 3B: Show pre-scan options dialog with quick stats
        from ui.prescan_options_dialog import PreScanOptionsDialog
        from services.photo_scan_service import PhotoScanService

        scan_service = PhotoScanService()
        options_dialog = PreScanOptionsDialog(
            parent=self.main,
            default_incremental=incremental,
            scan_service=scan_service,
        )
        # Kick off background file-count while user reviews options
        options_dialog.start_stats_count(folder)

        if options_dialog.exec() != QDialog.Accepted:
            # User cancelled
            self.main.statusBar().showMessage("Scan cancelled")
            return

        # Get user-selected options
        scan_options = options_dialog.get_options()
        incremental = scan_options.incremental

        # Store duplicate detection options for post-scan processing
        self._duplicate_detection_enabled = scan_options.detect_duplicates
        self._detect_exact = scan_options.detect_exact
        self._detect_similar = scan_options.detect_similar
        self._generate_embeddings = scan_options.generate_embeddings
        self._time_window_seconds = scan_options.time_window_seconds
        self._similarity_threshold = scan_options.similarity_threshold
        self._min_stack_size = scan_options.min_stack_size

        self.cancel_requested = False
        # Capture generation at scan start so callbacks can detect stale results
        self._expected_generation = self.main.ui_generation() if hasattr(self.main, 'ui_generation') else None
        self.main.statusBar().showMessage(f"📸 Scanning repository: {folder} (incremental={incremental})")
        self.main._committed_total = 0

        # PHASE 2 Task 2.2: Initialize pending operations tracker
        # Main scan will mark these complete as each operation finishes
        self._scan_operations_pending = {"main_scan", "date_branches"}
        self._scan_refresh_scheduled = False
        self._scan_result_cached = None
        self._progress_events = []

        # Non-modal progress via status-bar widgets (replaces old QProgressDialog)
        self.main.scan_ui_begin(tr("messages.scan_preparing"))
        self._last_progress_ui_ts = 0.0

        # Register scan with JobManager as a tracked job
        self._scan_job_id = None
        try:
            from services.job_manager import get_job_manager
            self._scan_job_id = get_job_manager().register_tracked_job(
                job_type='scan',
                description=f"Scanning {os.path.basename(folder)}",
                cancel_callback=self.cancel,
            )
        except Exception as e:
            self.logger.debug(f"JobManager tracked-job registration failed: {e}")

        # Dispatch ScanStarted to ProjectState store
        from core.state_bus import ActionMeta, ScanStarted
        _dispatch_store_action(ScanStarted(
            meta=ActionMeta(source="scan_controller"),
            job_id=self._scan_job_id or -1,
            folder_path=str(folder),
            incremental=incremental,
        ))

        # DB writer
        # NOTE: Schema creation handled automatically by repository layer
        from db_writer import DBWriter
        self.db_writer = DBWriter(batch_size=200, poll_interval_ms=150)
        self.db_writer.error.connect(lambda msg: self.logger.error(f"DBWriter error: {msg}"))
        self.db_writer.committed.connect(self._on_committed)
        self.db_writer.start()

        # Verify database connection (schema already initialized at startup)
        try:
            from repository.base_repository import DatabaseConnection
            db_conn = DatabaseConnection("reference_data.db", auto_init=False)
            self.logger.info("Database connection verified for scan")
        except Exception as e:
            self.logger.error(f"Failed to verify database connection: {e}", exc_info=True)
            self.main.statusBar().showMessage(f"Database connection failed: {e}")
            return

        # Get current project_id from the active layout (not the hidden grid)
        current_project_id = self._get_active_project_id()
        if current_project_id is None:
            current_project_id = 1  # Default to first project (may be created during scan)
        self.logger.debug(f"Using project_id: {current_project_id}")

        # Scan worker
        try:
            from services.scan_worker_adapter import ScanWorkerAdapter as ScanWorker

            try:
                self.thread = QThread(self.main)
            except Exception as qthread_err:
                self.logger.error(f"Failed to create QThread: {qthread_err}", exc_info=True)
                raise

            # CRITICAL: Define callback for video metadata extraction completion
            # PHASE 2 Task 2.2: This now marks operation complete instead of refreshing immediately
            def on_video_metadata_finished(success, failed):
                """Mark video metadata extraction complete and trigger coordinated refresh."""
                self.logger.info(f"Video metadata extraction complete ({success} success, {failed} failed)")

                # CRITICAL FIX: ALWAYS run video date backfill after scan (not conditional)
                # Without this, video date branches show 0 count and no dates appear
                if success > 0:
                    # PHASE 2 Task 2.2: Track video backfill as separate operation
                    self._scan_operations_pending.add("video_backfill")
                    self.logger.info("Auto-running video metadata backfill...")
                    # Run backfill in background to populate date fields
                    from backfill_video_dates import backfill_video_dates
                    try:
                        stats = backfill_video_dates(
                            project_id=current_project_id,
                            dry_run=False,
                            progress_callback=lambda c, t, m: self.logger.info(f"[Backfill] {c}/{t}: {m}")
                        )
                        self.logger.info(f"✓ Video backfill complete: {stats['updated']} videos updated")
                    except Exception as e:
                        self.logger.error(f"Video backfill failed: {e}", exc_info=True)
                    finally:
                        # PHASE 2 Task 2.2: Mark backfill complete
                        self._scan_operations_pending.discard("video_backfill")
                        self._check_and_trigger_final_refresh()

                # PHASE 2 Task 2.2: Mark video metadata complete and check for final refresh
                # DON'T refresh immediately - let coordinator handle it
                self._scan_operations_pending.discard("video_metadata")
                self.logger.info(f"Video metadata operation complete. Remaining: {self._scan_operations_pending}")
                self._check_and_trigger_final_refresh()

            try:
                self.worker = ScanWorker(folder, current_project_id, incremental, self.main.settings,
                                        db_writer=self.db_writer,
                                        on_video_metadata_finished=on_video_metadata_finished,
                                        progress_receiver=self)  # CRITICAL: Pass self for thread-safe progress updates
            except Exception as worker_err:
                self.logger.error(f"Failed to create ScanWorker: {worker_err}", exc_info=True)
                raise

            try:
                self.worker.moveToThread(self.thread)
            except Exception as move_err:
                self.logger.error(f"Failed to move worker to thread: {move_err}", exc_info=True)
                raise

            try:
                # CRITICAL FIX: Use Qt.QueuedConnection explicitly to prevent deadlock
                # When progress is emitted from worker thread via synchronous callback,
                # we need to ensure the emit() returns immediately without blocking

                # Connect test slot to verify Qt signal delivery (using proper class method to avoid GC issues)
                self.worker.progress.connect(self._test_progress_slot, Qt.QueuedConnection)

                # Connect actual handler
                self.worker.progress.connect(self._on_progress, Qt.QueuedConnection)

                self.worker.finished.connect(self._on_finished, Qt.QueuedConnection)
                self.worker.error.connect(self._on_error, Qt.QueuedConnection)
                self.thread.started.connect(self.worker.run)
                self.worker.finished.connect(lambda f, p, v=0: self.thread.quit())
                self.thread.finished.connect(self._cleanup)
            except Exception as signal_err:
                self.logger.error(f"Failed to connect signals: {signal_err}", exc_info=True)
                import traceback
                traceback.print_exc()
                raise

            # Start scan thread immediately - DBWriter is already running from line 178
            try:
                self.thread.start()
            except Exception as start_err:
                self.logger.error(f"Failed to start scan thread: {start_err}", exc_info=True)
                raise

            self.main.act_cancel_scan.setEnabled(True)
        except Exception as e:
            self.logger.error(f"Critical error creating scan worker: {e}", exc_info=True)
            self.main.statusBar().showMessage(f"❌ Failed to create scan worker: {e}")
#            from translations import tr

            QMessageBox.critical(self.main, tr("messages.scan_error"), tr("messages.scan_error_worker", error=str(e)))
            return

    def cancel(self):
        """Cancel triggered from toolbar."""
        self.cancel_requested = True
        if self.worker:
            try:
                self.worker.stop()
            except Exception:
                pass
#        from translations import tr

        self.main.statusBar().showMessage(tr('status_messages.scan_cancel_requested'))
        self.main.act_cancel_scan.setEnabled(False)

    def shutdown_barrier(self, timeout_ms: int = 5000) -> bool:
        """Stop scan thread and DBWriter, waiting up to timeout_ms.

        Called by MainWindow._do_shutdown_teardown() to ensure scan workers
        are cleanly stopped before the app exits.

        Returns True if all workers drained within the timeout.
        """
        drained = True
        self.cancel_requested = True

        # 1. Stop worker if running
        if self.worker:
            try:
                self.worker.stop()
            except Exception as e:
                self.logger.warning(f"[ScanController] Worker stop error: {e}")

        # 2. Quit and wait for QThread
        if self.thread and self.thread.isRunning():
            try:
                self.thread.quit()
                if not self.thread.wait(timeout_ms):
                    self.logger.warning(f"[ScanController] Thread did not finish in {timeout_ms}ms")
                    drained = False
                else:
                    self.logger.info("[ScanController] Scan thread stopped")
            except Exception as e:
                self.logger.warning(f"[ScanController] Thread wait error: {e}")
                drained = False

        # 3. Shutdown DBWriter
        if self.db_writer:
            try:
                self.db_writer.shutdown(wait=True)
                self.logger.info("[ScanController] DBWriter shut down")
            except Exception as e:
                self.logger.warning(f"[ScanController] DBWriter shutdown error: {e}")
                drained = False

        # 4. Clear pending operations
        self._scan_operations_pending.clear()
        self._scan_refresh_scheduled = False

        self.logger.info(f"[ScanController] Shutdown barrier complete (drained={drained})")
        return drained

    def _on_committed(self, n: int):
        if not is_alive(self.main) or not generation_ok(self.main, self._expected_generation):
            return
        self.main._committed_total += n
        self._maybe_refresh_grid_incremental()

    def _maybe_refresh_grid_incremental(self):
        """Trigger a lightweight grid reload at most once every 1.2 s so the
        user sees thumbnails filling in while the scan is still running."""
        now = time.time()
        last = getattr(self, "_last_incremental_refresh_ts", 0.0)
        if now - last < 1.2:
            return
        self._last_incremental_refresh_ts = now

        try:
            if hasattr(self.main.grid, "_schedule_reload"):
                self.main.grid._schedule_reload()
            elif hasattr(self.main.grid, "reload"):
                self.main.grid.reload()
        except Exception:
            pass  # grid may not be ready yet

    def _log_progress_event(self, message: str) -> str:
        """Track recent progress lines so the dialog can show contextual history."""
        if not message:
            return "\n".join(self._progress_events)

        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._progress_events.append(entry)
        # Keep the most recent handful to avoid bloating the dialog
        self._progress_events = self._progress_events[-8:]
        return "\n".join(self._progress_events)

    def _on_progress(self, pct: int, msg: str):
        """Handle progress updates from scan worker thread.

        Uses throttling (80 ms) to avoid flooding the event loop with
        status-bar repaints while still showing crisp 0 % / 100 % updates.
        """
        if not is_alive(self.main) or not generation_ok(self.main, self._expected_generation):
            return
        now = time.time()
        last = getattr(self, "_last_progress_ui_ts", 0.0)

        pct_i = max(0, min(100, int(pct or 0)))

        # Throttle intermediate updates to ~12 fps
        if now - last < 0.08 and pct_i not in (0, 100):
            return
        self._last_progress_ui_ts = now

        if msg:
            self._log_progress_event(msg)

        committed = getattr(self.main, "_committed_total", 0)
        short_msg = msg or f"Scanning... {pct_i}%"
        # Keep status-bar text compact
        if committed:
            short_msg = f"{short_msg}  ({committed} rows)"

        self.main.scan_ui_update(pct_i, short_msg)

        # Report to JobManager (routes to Activity Center & status bar)
        if self._scan_job_id is not None:
            try:
                from services.job_manager import get_job_manager
                mgr = get_job_manager()
                mgr.report_progress(self._scan_job_id, pct_i, 100, short_msg)
                if msg:
                    mgr.report_log(self._scan_job_id, msg)
            except Exception:
                pass

    def _on_finished(self, folders, photos, videos=0):
        if not is_alive(self.main) or not generation_ok(self.main, self._expected_generation):
            return
        self.logger.info(f"Scan finished: {folders} folders, {photos} photos, {videos} videos")
        self.main._scan_result = (folders, photos, videos)

        if self._scan_job_id is not None:
            try:
                from services.job_manager import get_job_manager
                get_job_manager().report_log(
                    self._scan_job_id,
                    f"Scan finished: {folders} folders, {photos} photos, {videos} videos",
                )
            except Exception:
                pass

        # Update status bar progress to 100 %
        try:
            self.main.scan_ui_update(
                100,
                f"Indexed {photos} photos, {videos} videos in {folders} folders",
            )
        except Exception:
            pass

        # NOTE: ScanCompleted dispatch is deferred to _finalize_scan_refresh()
        # so that project_images is populated (via build_date_branches) BEFORE
        # the store bump triggers GoogleLayout to re-query the database.

    def _on_error(self, err_text: str):
        if not is_alive(self.main) or not generation_ok(self.main, self._expected_generation):
            return
        self.logger.error(f"Scan error: {err_text}")
        if self._scan_job_id is not None:
            try:
                from services.job_manager import get_job_manager
                get_job_manager().complete_tracked_job(
                    self._scan_job_id, success=False, error=err_text[:80],
                )
            except Exception:
                pass
            self._scan_job_id = None
        try:
            QMessageBox.critical(self.main, tr("messages.scan_error"), err_text)
        except Exception:
            QMessageBox.critical(self.main, "Scan Error", err_text)
        if self.thread and self.thread.isRunning():
            self.thread.quit()

    def _cleanup(self):
        """
        Cleanup after scan completes.
        P1-7 FIX: Ensure cleanup runs in main thread to avoid Qt thread violations.
        """
        # P1-7 FIX: Check if we're in the main thread
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        if self.main.thread() != QApplication.instance().thread():
            # Called from worker thread - marshal to main thread
            QTimer.singleShot(0, self._cleanup_impl)
        else:
            # Already in main thread
            self._cleanup_impl()

    def _cleanup_impl(self):
        """Actual cleanup implementation - must run in main thread."""
        try:
            self.main.act_cancel_scan.setEnabled(False)
            if self.db_writer:
                # FIX #1: Never block the UI thread waiting for the writer
                # to drain.  The signal-driven _check_and_trigger_final_refresh
                # path handles post-scan coordination asynchronously.
                self.db_writer.shutdown(wait=False)
        except Exception as e:
            self.logger.error(f"Cleanup error: {e}", exc_info=True)

        # Get scan results BEFORE heavy operations
        f, p, v = self.main._scan_result if len(self.main._scan_result) == 3 else (*self.main._scan_result, 0)

        # Report post-scan progress via status bar (no modal dialogs)
        self.main._scan_complete_msgbox = None  # no blocking msgbox
        self.main.statusBar().showMessage(tr("messages.progress_building_branches"), 0)

        # Lightweight progress tracker (no dialog — just a counter for _finalize_scan_refresh)
        class _ProgressStub:
            """Minimal stub so downstream code that calls progress.setValue/setLabelText/close
            still works but routes everything to the status bar."""
            def __init__(self, status_bar, logger):
                self._bar = status_bar
                self._log = logger
            def setValue(self, v):
                pass
            def setLabelText(self, text):
                try:
                    self._bar.showMessage(text, 0)
                except Exception:
                    pass
            def close(self):
                pass
            def show(self):
                pass

        progress = _ProgressStub(self.main.statusBar(), self.logger)

        # Build date branches after scan completes
        sidebar_was_updated = False
        try:
            progress.setLabelText(tr("messages.progress_building_photo_branches"))
            progress.setValue(1)

            self.logger.info("Building date branches...")
            from reference_db import ReferenceDB
            from app_services import get_default_project_id
            db = ReferenceDB()

            # Get project_id from the active layout (not the hidden CurrentLayout grid)
            current_project_id = self._get_active_project_id()
            if current_project_id is None:
                self.logger.warning("No active project_id found, using DB default")
                current_project_id = get_default_project_id()

            if current_project_id is None:
                self.logger.error("No project found! Cannot build date branches.")
                raise ValueError("No project available to associate scanned photos")

            self.logger.info(f"Building date branches for project_id={current_project_id}")
            branch_count = db.build_date_branches(current_project_id)
            self.logger.info(f"Created {branch_count} photo date branch entries for project {current_project_id}")

            # CRITICAL FIX: Build video date branches too (videos need branches like photos!)
            self.logger.info(f"Building video date branches for project_id={current_project_id}")
            video_branch_count = db.build_video_date_branches(current_project_id)
            self.logger.info(f"Created {video_branch_count} video date branch entries for project {current_project_id}")

            progress.setLabelText(tr("messages.progress_backfilling_metadata"))
            progress.setValue(2)

            # CRITICAL: Backfill created_date field immediately after scan
            # This populates created_date from date_taken so get_date_hierarchy() works
            # Without this, the "By Date" section won't appear until app restart
            self.logger.info("Backfilling created_date fields for photos...")
            backfilled = db.single_pass_backfill_created_fields()
            if backfilled:
                self.logger.info(f"Backfilled {backfilled} photo rows with created_date")

            # SURGICAL FIX E: Backfill video created_date fields too
            self.logger.info("Backfilling created_date fields for videos...")
            video_backfilled = db.single_pass_backfill_created_fields_videos()
            if video_backfilled:
                self.logger.info(f"Backfilled {video_backfilled} video rows with created_date")

            # PHASE 3B: Duplicate Detection — dispatched to background thread
            # Instead of blocking the UI with QEventLoop / QProgressDialog,
            # we fire a PostScanPipelineWorker and let it run asynchronously.
            # IDEMPOTENCY: Skip when 0 new photos/videos indexed (rescan with
            # no changes).  Existing stacks and duplicates are already valid.
            _has_new_media = (p > 0 or v > 0)
            if not _has_new_media and hasattr(self, '_duplicate_detection_enabled') and self._duplicate_detection_enabled:
                self.logger.info(
                    "Skipping post-scan pipeline: 0 new photos/videos indexed "
                    "(existing stacks and duplicates are up-to-date)"
                )
            if _has_new_media and hasattr(self, '_duplicate_detection_enabled') and self._duplicate_detection_enabled:
                self._scan_operations_pending.add("post_scan_pipeline")
                self.logger.info("Enqueueing duplicate detection pipeline as background job...")

                try:
                    from workers.post_scan_pipeline_worker import PostScanPipelineWorker

                    pipeline_options = {
                        "detect_exact": getattr(self, '_detect_exact', False),
                        "detect_similar": getattr(self, '_detect_similar', False),
                        "generate_embeddings": getattr(self, '_generate_embeddings', False),
                        "time_window_seconds": getattr(self, '_time_window_seconds', None),
                        "similarity_threshold": getattr(self, '_similarity_threshold', None),
                        "min_stack_size": getattr(self, '_min_stack_size', None),
                        "run_ocr": self._get_ocr_enabled(),
                        "ocr_languages": self._get_ocr_languages(),
                    }

                    self._post_scan_worker = PostScanPipelineWorker(
                        project_id=current_project_id,
                        options=pipeline_options,
                    )

                    # Register with JobManager as tracked job
                    self._pspl_job_id = None
                    try:
                        from services.job_manager import get_job_manager
                        self._pspl_job_id = get_job_manager().register_tracked_job(
                            job_type='post_scan',
                            project_id=current_project_id,
                            description="Duplicate Detection",
                            cancel_callback=lambda: (
                                self._post_scan_worker.cancel()
                                if hasattr(self._post_scan_worker, "cancel") else None),
                        )
                    except Exception:
                        pass

                    # Progress updates go to status bar + JobManager
                    def _on_pipeline_progress(step_name, step_num, total, message):
                        try:
                            # Step labeling prefix for status bar
                            prefix = f"[{step_num}/{total}] "
                            self.main.statusBar().showMessage(f"{prefix}{message}", 0)
                        except Exception:
                            pass
                        if self._pspl_job_id is not None:
                            try:
                                from services.job_manager import get_job_manager
                                mgr = get_job_manager()
                                mgr.report_progress(self._pspl_job_id, step_num, total,
                                                    f"[{step_num}/{total}] {message}")
                                mgr.report_log(self._pspl_job_id, message)
                            except Exception:
                                pass

                    def _on_pipeline_finished(results):
                        errors = results.get("errors", [])
                        exact = results.get("exact_duplicates", 0)
                        similar = results.get("similar_stacks", 0)
                        total = exact + similar

                        if total > 0:
                            parts = []
                            if exact > 0:
                                parts.append(f"{exact} exact duplicate groups")
                            if similar > 0:
                                parts.append(f"{similar} similar shot stacks")
                            summary = f"Found {', '.join(parts)}"
                            self.main.statusBar().showMessage(
                                f"Duplicate detection complete: {', '.join(parts)}", 8000
                            )
                        else:
                            summary = "No duplicates found"
                            self.main.statusBar().showMessage(
                                "Duplicate detection complete — no duplicates found", 5000
                            )

                        if errors:
                            self.logger.warning("Pipeline completed with errors: %s", errors)

                        if self._pspl_job_id is not None:
                            try:
                                from services.job_manager import get_job_manager
                                get_job_manager().complete_tracked_job(
                                    self._pspl_job_id, success=True,
                                    stats={"exact": exact, "similar": similar},
                                )
                            except Exception:
                                pass
                            self._pspl_job_id = None

                        # Dispatch EmbeddingsCompleted + DuplicatesCompleted to ProjectState store
                        from core.state_bus import (
                            ActionMeta,
                            DuplicatesCompleted as DupAction,
                            EmbeddingsCompleted as EmbAction,
                        )
                        emb_count = results.get("embeddings_generated", 0)
                        if emb_count > 0:
                            _dispatch_store_action(EmbAction(
                                meta=ActionMeta(source="post_scan_pipeline"),
                                job_id=self._pspl_job_id or -1,
                                generated=emb_count,
                            ))
                        _dispatch_store_action(DupAction(
                            meta=ActionMeta(source="post_scan_pipeline"),
                            job_id=self._pspl_job_id or -1,
                            exact_groups=exact,
                            similar_stacks=similar,
                        ))

                        # Duplicates section now self-refreshes via AccordionSidebar's
                        # store subscription (duplicates_v change → reload_section).

                        # Mark post-scan pipeline complete and check final refresh
                        self._scan_operations_pending.discard("post_scan_pipeline")
                        self.logger.info(f"Post-scan pipeline complete. Remaining: {self._scan_operations_pending}")
                        self._check_and_trigger_final_refresh()

                    def _on_pipeline_error(msg):
                        self.logger.error("Post-scan pipeline error: %s", msg)
                        self.main.statusBar().showMessage(
                            f"Duplicate detection failed: {msg}", 8000
                        )
                        if self._pspl_job_id is not None:
                            try:
                                from services.job_manager import get_job_manager
                                get_job_manager().complete_tracked_job(
                                    self._pspl_job_id, success=False,
                                    error=str(msg)[:80],
                                )
                            except Exception:
                                pass
                            self._pspl_job_id = None
                        # Still mark complete on error so final refresh isn't blocked
                        self._scan_operations_pending.discard("post_scan_pipeline")
                        self._check_and_trigger_final_refresh()

                    self._post_scan_worker.signals.progress.connect(_on_pipeline_progress)
                    self._post_scan_worker.signals.finished.connect(_on_pipeline_finished)
                    self._post_scan_worker.signals.error.connect(_on_pipeline_error)

                    QThreadPool.globalInstance().start(self._post_scan_worker)
                    self.logger.info("Post-scan pipeline dispatched to background thread pool")

                except Exception as e:
                    self.logger.error(f"Failed to start post-scan pipeline: {e}", exc_info=True)
                    self._scan_operations_pending.discard("post_scan_pipeline")

            # PHASE 3: Face Detection — via central FacePipelineService
            # The service validates project_id, prevents duplicate runs,
            # and the UIRefreshMediator handles incremental People refresh.
            # IDEMPOTENCY: Skip when 0 new photos indexed — existing face
            # data (detections, clusters, merges) is already valid.
            if not _has_new_media:
                self.logger.info(
                    "Skipping face pipeline: 0 new photos indexed "
                    "(existing face data and merges preserved)"
                )
            try:
                from config.face_detection_config import get_face_config
                face_config = get_face_config()

                if _has_new_media and face_config.is_enabled() and face_config.get("auto_cluster_after_scan", True):
                    from services.face_detection_service import FaceDetectionService
                    availability = FaceDetectionService.check_backend_availability()
                    backend = face_config.get_backend()

                    if availability.get(backend, False):
                        self.logger.info("Enqueueing face pipeline via FacePipelineService (backend=%s)...", backend)
                        from services.face_pipeline_service import FacePipelineService
                        svc = FacePipelineService.instance()

                        # Track face pipeline in pending operations
                        self._scan_operations_pending.add("face_pipeline")

                        # Register with JobManager as tracked job
                        self._face_job_id = None
                        try:
                            from services.job_manager import get_job_manager
                            self._face_job_id = get_job_manager().register_tracked_job(
                                job_type='face_pipeline',
                                project_id=current_project_id,
                                description="Face Detection & Clustering",
                                cancel_callback=lambda: svc.cancel(current_project_id),
                            )
                        except Exception:
                            pass

                        # Wire FacePipelineService progress to JobManager
                        def _on_face_progress(step_name, message, pid):
                            if self._face_job_id is not None:
                                try:
                                    from services.job_manager import get_job_manager
                                    mgr = get_job_manager()
                                    mgr.report_progress(self._face_job_id, 50, 100,
                                                        f"{step_name}: {message}")
                                    mgr.report_log(self._face_job_id, message)
                                except Exception:
                                    pass

                        if self._face_job_id is not None:
                            try:
                                svc.progress.connect(_on_face_progress)
                            except Exception:
                                pass

                        def _on_face_pipeline_done(results, pid):
                            # Disconnect to prevent accumulation across scans
                            try:
                                svc.finished.disconnect(_on_face_pipeline_done)
                                svc.error.disconnect(_on_face_pipeline_error)
                            except (RuntimeError, TypeError):
                                pass
                            try:
                                svc.progress.disconnect(_on_face_progress)
                            except (RuntimeError, TypeError):
                                pass
                            self._scan_operations_pending.discard("face_pipeline")
                            self.logger.info(f"Face pipeline complete for project {pid}. Remaining: {self._scan_operations_pending}")

                            faces = results.get("faces_detected", 0) if isinstance(results, dict) else 0
                            clusters = results.get("clusters_created", 0) if isinstance(results, dict) else 0
                            if self._face_job_id is not None:
                                try:
                                    from services.job_manager import get_job_manager
                                    get_job_manager().complete_tracked_job(
                                        self._face_job_id, success=True,
                                        stats={"faces": faces, "clusters": clusters},
                                    )
                                except Exception:
                                    pass
                                self._face_job_id = None

                            # Dispatch FacesCompleted to ProjectState store
                            from core.state_bus import ActionMeta, FacesCompleted as FacesAction
                            _dispatch_store_action(FacesAction(
                                meta=ActionMeta(source="face_pipeline"),
                                job_id=self._face_job_id or -1,
                                detected=faces,
                                clustered=clusters,
                            ))

                            # Rebuild search_asset_features so face_count is
                            # visible to SearchOrchestrator (fixes 0% coverage).
                            try:
                                from repository.search_feature_repository import SearchFeatureRepository
                                repo = SearchFeatureRepository()
                                if repo.table_exists():
                                    repo.refresh_project(pid)
                            except Exception:
                                pass
                            try:
                                from services.search_orchestrator import get_search_orchestrator
                                get_search_orchestrator(pid).invalidate_meta_cache()
                            except Exception:
                                pass

                            # People section now self-refreshes via AccordionSidebar's
                            # store subscription (people_v change → reload_section).
                            self._check_and_trigger_final_refresh()

                        def _on_face_pipeline_error(msg, pid):
                            try:
                                svc.finished.disconnect(_on_face_pipeline_done)
                                svc.error.disconnect(_on_face_pipeline_error)
                            except (RuntimeError, TypeError):
                                pass
                            try:
                                svc.progress.disconnect(_on_face_progress)
                            except (RuntimeError, TypeError):
                                pass
                            self._scan_operations_pending.discard("face_pipeline")
                            self.logger.error(f"Face pipeline error for project {pid}: {msg}")
                            if self._face_job_id is not None:
                                try:
                                    from services.job_manager import get_job_manager
                                    get_job_manager().complete_tracked_job(
                                        self._face_job_id, success=False,
                                        error=str(msg)[:80],
                                    )
                                except Exception:
                                    pass
                                self._face_job_id = None
                            self._check_and_trigger_final_refresh()

                        svc.finished.connect(_on_face_pipeline_done)
                        svc.error.connect(_on_face_pipeline_error)

                        started = svc.start(
                            project_id=current_project_id,
                            model=face_config.get("model", "buffalo_l"),
                        )
                        if started:
                            self.logger.info("Face pipeline dispatched via FacePipelineService")
                        else:
                            # Pipeline didn't start (already running or invalid)
                            self._scan_operations_pending.discard("face_pipeline")
                            self.logger.info("Face pipeline already running or project invalid")
                    else:
                        self.logger.warning("Face backend '%s' not available (available: %s)",
                                            backend, [k for k, v in availability.items() if v])
                else:
                    self.logger.debug("Face detection disabled or auto-clustering off")

            except ImportError as e:
                self.logger.debug("Face detection modules not available: %s", e)
            except Exception as e:
                self.logger.error("Face detection setup error: %s", e, exc_info=True)

            # Delegate project_id propagation to the active layout instead
            # of directly poking hidden sidebar/grid widgets that may belong
            # to an inactive layout (e.g. CurrentLayout sidebar when Google is active).
            active_pid = self._get_active_project_id()
            if active_pid is None:
                from app_services import get_default_project_id
                active_pid = get_default_project_id()

            if active_pid is not None:
                if hasattr(self.main, 'layout_manager') and self.main.layout_manager:
                    layout = self.main.layout_manager.get_current_layout()
                    if layout:
                        cur = layout.get_current_project() if hasattr(layout, 'get_current_project') else None
                        if cur is None:
                            layout.set_project(active_pid)
                            self.logger.info(f"Propagated project_id={active_pid} to active layout")
                            sidebar_was_updated = True

                # Also ensure CurrentLayout's grid/sidebar are in sync
                # (they may still be None if Google Layout was active at startup)
                if hasattr(self.main, 'grid') and self.main.grid:
                    if getattr(self.main.grid, 'project_id', None) is None:
                        self.main.grid.project_id = active_pid
                if hasattr(self.main, 'sidebar') and self.main.sidebar:
                    if getattr(self.main.sidebar, 'project_id', None) is None:
                        # Only set the attribute — avoid triggering a full
                        # reload when Google Layout is active (the sidebar
                        # widget may be hidden, causing a "blocked" warning).
                        # Store subscriptions handle the actual reload when
                        # the user switches back to CurrentLayout.
                        self.main.sidebar.project_id = active_pid
                        sidebar_was_updated = True
        except Exception as e:
            self.logger.error(f"Error building date branches: {e}", exc_info=True)

        # PHASE 2 Task 2.2: Mark main_scan and date_branches as complete
        # This will check if all operations are done and trigger final refresh
        self._scan_operations_pending.discard("main_scan")
        self._scan_operations_pending.discard("date_branches")
        self._scan_result_cached = (f, p, v, sidebar_was_updated, progress)
        self.logger.info(f"Main scan operations complete. Remaining: {self._scan_operations_pending}")
        self._check_and_trigger_final_refresh()

        # PHASE 2 Task 2.2: OLD refresh_ui() moved to _finalize_scan_refresh()
        # This ensures only ONE refresh happens after ALL async operations complete

    def _check_and_trigger_final_refresh(self):
        """
        PHASE 2 Task 2.2: Check if all scan operations are complete.
        If yes, trigger final debounced refresh. If no, wait for other operations.
        """
        if not self._scan_operations_pending and not self._scan_refresh_scheduled:
            self._scan_refresh_scheduled = True
            self.logger.info("✓ All scan operations complete. Triggering final refresh...")
            # Debounce with 100ms delay to ensure all signals propagate
            QTimer.singleShot(100, self._finalize_scan_refresh)
        elif self._scan_operations_pending:
            self.logger.info(f"⏳ Waiting for operations: {self._scan_operations_pending}")

    def _finalize_scan_refresh(self):
        """
        Bookkeeping after ALL scan operations complete.

        All UI refresh is now handled by store subscriptions:
        - GoogleLayout subscribes to media_v → refresh_after_scan()
        - AccordionSidebar subscribes to media_v/duplicates_v/people_v
        - CurrentLayout grid/sidebar subscribe via MainWindow store sub
        This method only handles progress dialog, status bar, and cleanup.
        """
        if not self._scan_result_cached:
            self.logger.warning("No cached scan results - cannot refresh")
            return

        f, p, v, sidebar_was_updated, progress = self._scan_result_cached
        self.logger.info("🔄 Final scan bookkeeping (UI refresh handled by store subscribers)...")

        # Close progress stub / status bar
        try:
            progress.close()
        except Exception as e:
            self.logger.error(f"Error closing progress dialog: {e}")

        # Final status message — auto-hides after 5 s
        self.main.scan_ui_finish(f"Scan complete: {p} photos, {v} videos indexed", 5000)
        self.logger.info(f"Final refresh complete: {p} photos, {v} videos")

        # Dispatch ScanCompleted NOW — after build_date_branches() has populated
        # project_images, so the media_v bump triggers layout queries against a
        # fully populated database (fixes "0 rows total" race condition).
        from core.state_bus import ActionMeta, ScanCompleted as ScanCompletedAction
        _dispatch_store_action(ScanCompletedAction(
            meta=ActionMeta(source="scan_controller"),
            job_id=self._scan_job_id or -1,
            photos_indexed=p,
            videos_indexed=v,
        ))

        # Mark scan complete via JobManager
        if self._scan_job_id is not None:
            try:
                from services.job_manager import get_job_manager
                get_job_manager().complete_tracked_job(
                    self._scan_job_id, success=True,
                    stats={"photos": p, "videos": v},
                )
            except Exception:
                pass
            self._scan_job_id = None

        # PHASE 2 Task 2.2: Reset state for next scan
        self._scan_refresh_scheduled = False
        self._scan_result_cached = None

        # Note: Video metadata worker callback is now connected at worker creation time
        # in start_scan() to avoid race conditions with worker finishing before cleanup runs

    @Slot(int, str)
    def _on_stacks_updated(self, project_id: int, stack_type: str):
        """Handle stack updates from StackGenerationService.

        Dispatches StacksCompleted to the store, which bumps stacks_v.
        UI components (GoogleLayout, CurrentLayout grid) react via
        their own store subscriptions.
        """
        self.logger.info(f"Stacks updated: project={project_id}, type={stack_type}")
        from core.state_bus import ActionMeta, StacksCompleted as StacksAction
        _dispatch_store_action(StacksAction(
            meta=ActionMeta(source="stack_generation"),
            job_id=-1,
            stacks_created=0,
        ))


