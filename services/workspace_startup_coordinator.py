"""
Workspace startup coordinator — Phase 1A MainWindow decomposition extraction.

Owns the post-first-paint / deferred-initialization lifecycle previously living
directly on MainWindow. This coordinator is held by MainWindow and delegated to
from thin wrappers so behavior is byte-for-byte identical — only ownership has
moved.

Responsibilities extracted from main_window_qt.MainWindow:
- _after_first_paint              → after_first_paint
- _deferred_initialization        → deferred_initialization
- _init_minimal_db_handle         → init_minimal_db_handle
- _enqueue_startup_maintenance_job → enqueue_startup_maintenance_job
- _warmup_clip_in_background      → warmup_clip_in_background
- _deferred_cache_purge           → deferred_cache_purge

Non-responsibilities (still owned by MainWindow, referenced via self.mw):
- _restore_session_state (Module B — project_switch_service)
- _update_status_bar (shell concern)
- grid, sidebar, layout_manager attribute ownership
- _closing lifecycle flag
"""

import logging
import threading

from PySide6.QtCore import QTimer, QThreadPool

logger = logging.getLogger(__name__)


class WorkspaceStartupCoordinator:
    """Owns post-first-paint startup + deferred init chain for MainWindow."""

    def __init__(self, main_window):
        self.mw = main_window

    # ------------------------------------------------------------------
    # First-paint entry point — scheduled by MainWindow.showEvent
    # ------------------------------------------------------------------
    def after_first_paint(self):
        """Runs right after the first paint — schedules heavy background work."""
        mw = self.mw
        if getattr(mw, "_deferred_init_started", False):
            return
        mw._deferred_init_started = True
        import time as _time
        t0 = _time.perf_counter()
        print(f"[Startup] _after_first_paint fired at {t0:.3f}s")

        # ----------------------------------------------------------
        # Guardrail 1 (startup scheduling gate): Throttle background
        # workers so initial layout + thumbnails aren't starved.
        # ----------------------------------------------------------

        # 1a. Throttle JobManager's private thread pool.
        try:
            from services.job_manager import get_job_manager
            jm = get_job_manager()
            if hasattr(jm, "enable_startup_throttle"):
                jm.enable_startup_throttle(max_threads=1)
                QTimer.singleShot(5000, jm.disable_startup_throttle)
        except Exception:
            pass  # Throttle is best-effort, never block startup for it.

        # 1b. Throttle global QThreadPool (used by GoogleLayout's
        #     PhotoPageWorker / GroupingWorker for initial photo load).
        try:
            pool = QThreadPool.globalInstance()
            mw._startup_global_pool_prev = pool.maxThreadCount()
            pool.setMaxThreadCount(max(2, mw._startup_global_pool_prev // 2))

            def _restore_global_pool():
                prev = getattr(mw, '_startup_global_pool_prev', None)
                if prev is not None:
                    QThreadPool.globalInstance().setMaxThreadCount(prev)
                    print(f"[Startup] Global QThreadPool restored to {prev} threads")

            QTimer.singleShot(5000, _restore_global_pool)
        except Exception:
            pass

        # ----------------------------------------------------------
        # Guardrail 2 (first-render fence): Notify the active layout
        # that first paint is done so it can start its initial load.
        # A short 50 ms delay lets the event loop flush pending
        # paint events before the load kicks in.
        # ----------------------------------------------------------
        try:
            layout = mw.layout_manager.get_current_layout() if hasattr(mw, 'layout_manager') else None
            if layout and hasattr(layout, '_on_startup_ready'):
                if mw.active_project_id is not None:
                    QTimer.singleShot(50, layout._on_startup_ready)
                    print(f"[Startup] Scheduled _on_startup_ready for {type(layout).__name__}")
                else:
                    logger.info("[Startup] Suppressing initial project-bound layout load because no active project exists")
        except Exception:
            pass

        # Start deferred init after a short delay to let initial render settle.
        QTimer.singleShot(250, self.deferred_initialization)

    # ------------------------------------------------------------------
    # Deferred initialization chain
    # ------------------------------------------------------------------
    def deferred_initialization(self):
        """
        CRITICAL FIX: Perform heavy initialization operations after window is shown.

        v9.3.0 FIX: Moved heavy DB operations (backfill, index optimization) to
        background jobs. Only minimal DB handle creation happens in GUI thread.

        This follows Material Design principle: App should be responsive immediately,
        heavy work happens visibly in the background via Activity Center.
        """
        mw = self.mw
        if mw._closing:
            return

        # Gap 1 fix: Check if we have an active project. If not, we are in onboarding.
        active_pid = getattr(mw.grid, 'project_id', None) if hasattr(mw, 'grid') else None
        if active_pid is None:
            print("[MainWindow] No active project (onboarding) — suppressing auto-load and heavy maintenance")
            mw._update_status_bar()
            return

        print(f"[MainWindow] Starting deferred initialization for project_id={active_pid}...")

        try:
            # Step 1: Fast - create minimal DB handle (no heavy operations)
            self.init_minimal_db_handle()
            print("[MainWindow] ✅ Database handle initialized (fast)")

            # Step 2: Restore session state (still owned by MainWindow — Module B)
            QTimer.singleShot(300, mw._restore_session_state)
            print("[MainWindow] ✅ Session state restoration scheduled")

            # Step 3: Update status bar
            mw._update_status_bar()
            print("[MainWindow] ✅ Status bar updated")

            # Step 4: Enqueue heavy DB maintenance as background job (delayed 2s)
            # This runs visibly in Activity Center, doesn't block UI.
            # The 2s delay lets initial thumbnails and grouping finish first.
            QTimer.singleShot(2000, self.enqueue_startup_maintenance_job)
            print("[MainWindow] Database maintenance job scheduled (2s delay)")

            # Step 5: CLIP model warmup (3s delay — after initial photo load)
            # Uses ModelWarmupWorker (QRunnable) on QThreadPool. Searches also
            # run in background threads, so even if warmup hasn't finished the
            # UI stays responsive (no main-thread freeze).
            QTimer.singleShot(3000, self.warmup_clip_in_background)
            print("[MainWindow] CLIP background warmup scheduled (3s delay)")

            # Step 6: Deferred thumbnail cache purge (delayed 5s)
            QTimer.singleShot(5000, self.deferred_cache_purge)
            print("[MainWindow] Cache purge scheduled (5s delay)")

            print("[MainWindow] ✅ Deferred initialization completed successfully")

        except Exception as e:
            print(f"[MainWindow] ⚠️ Deferred initialization error: {e}")
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Fast DB handle init — runs on GUI thread
    # ------------------------------------------------------------------
    def init_minimal_db_handle(self):
        """
        Fast DB initialization - only creates handle, no heavy operations.

        Heavy operations (backfill, index optimization) are moved to
        enqueue_startup_maintenance_job() which runs in background.
        """
        mw = self.mw
        from reference_db import ReferenceDB
        mw.db = ReferenceDB()

        # Gap 1 fix: Do not reload sidebar if project_id is None
        active_pid = getattr(mw.grid, 'project_id', None) if hasattr(mw, 'grid') else None
        if active_pid is None:
            return

        # Reload sidebar date tree (fast operation, uses cached data)
        try:
            if hasattr(mw, 'sidebar') and hasattr(mw.sidebar, 'reload_date_tree'):
                mw.sidebar.reload_date_tree()
                print("[Sidebar] Date tree reloaded.")
        except Exception as e:
            print(f"[Sidebar] Failed to reload date tree: {e}")

    # ------------------------------------------------------------------
    # Background maintenance job
    # ------------------------------------------------------------------
    def enqueue_startup_maintenance_job(self):
        """
        Enqueue heavy DB maintenance as a tracked background job.

        Uses the global JobManager singleton so the job always appears in the
        Activity Center.  The worker thread gets its own ReferenceDB connection
        (per-thread pool) and never touches Qt widgets.
        """
        mw = self.mw
        if mw._closing:
            return
        try:
            from services.job_manager import get_job_manager
            jm = get_job_manager()

            job_id = jm.register_tracked_job(
                job_type="maintenance",
                description="Database maintenance (backfill & index)",
            )
            print(f"[MainWindow] Maintenance job registered: job_id={job_id}")

            def _maintenance():
                try:
                    from reference_db import ReferenceDB
                    db = ReferenceDB()
                    db.single_pass_backfill_created_fields()
                    db.optimize_indexes()
                    jm.complete_tracked_job(job_id, success=True)
                    print("[MainWindow] Background maintenance completed")
                except Exception as e:
                    jm.complete_tracked_job(job_id, success=False, error=str(e))
                    print(f"[MainWindow] Background maintenance failed: {e}")

            thread = threading.Thread(target=_maintenance, name="startup_maintenance", daemon=True)
            thread.start()

        except Exception as e:
            print(f"[MainWindow] Failed to enqueue maintenance job: {e}")
            # Non-fatal — app can continue without optimization

    # ------------------------------------------------------------------
    # CLIP warmup on background QRunnable
    # ------------------------------------------------------------------
    def warmup_clip_in_background(self):
        """
        Warm up CLIP model via ModelWarmupWorker (QRunnable on QThreadPool).

        Runs 3 seconds after startup. Searches also run in background
        threads (via _SmartFindWorker), so even if warmup hasn't
        finished when the user clicks a preset, the UI stays
        responsive.
        """
        mw = self.mw
        if mw._closing:
            return
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()

            # Only warmup if semantic embeddings are enabled
            if not settings.get("enable_semantic_embeddings", True):
                print("[MainWindow] CLIP warmup skipped (semantic embeddings disabled)")
                return

            # Resolve current project ID for canonical model selection
            project_id = getattr(mw.grid, 'project_id', None) if hasattr(mw, 'grid') else None

            from workers.model_warmup_worker import launch_model_warmup
            mw._clip_warmup_worker = launch_model_warmup(
                project_id=project_id,
                on_finished=lambda mid, variant: print(
                    f"[MainWindow] ✅ CLIP model warmed up in background: {variant}"
                ),
                on_error=lambda err: print(
                    f"[MainWindow] ⚠️ CLIP background warmup failed (non-fatal): {err}"
                ),
            )
            print("[MainWindow] CLIP background warmup started (ModelWarmupWorker)")

        except Exception as e:
            print(f"[MainWindow] ⚠️ Could not start CLIP warmup: {e}")

    # ------------------------------------------------------------------
    # Deferred thumbnail cache purge
    # ------------------------------------------------------------------
    def deferred_cache_purge(self):
        """FIX #6: Run thumbnail cache purge in a background thread after startup."""
        mw = self.mw
        if mw._closing:
            return
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            if not settings.get("cache_auto_cleanup", True):
                print("[MainWindow] Cache auto-cleanup disabled, skipping purge")
                return

            def _purge():
                try:
                    from thumb_cache_db import get_cache
                    cache = get_cache()
                    cache.purge_stale(max_age_days=30)
                    print("[MainWindow] Deferred cache purge completed")
                except Exception as e:
                    print(f"[MainWindow] Cache purge error (non-fatal): {e}")

            thread = threading.Thread(target=_purge, name="cache_purge", daemon=True)
            thread.start()
        except Exception as e:
            print(f"[MainWindow] Could not start cache purge: {e}")
