# services/face_pipeline_service.py
# Central orchestrator for face detection + clustering.
#
# All UI entry points (scan controller, main window button, sidebar button)
# call this service instead of creating workers directly.
# The service guarantees:
#   - Only one pipeline runs per project at a time
#   - project_id is validated before workers start
#   - Progress is forwarded through unified Qt signals
#   - Cancellation is clean and idempotent
#   - Incremental batch_committed events drive progressive People refresh
#   - All runs are registered as tracked jobs with JobManager for history

import logging
import threading
from enum import Enum
from typing import Optional, List, Dict, Any

from PySide6.QtCore import QObject, Signal, QThreadPool

logger = logging.getLogger(__name__)


class ScreenshotFacePolicy(str, Enum):
    EXCLUDE = "exclude"
    DETECT_ONLY = "detect_only"
    INCLUDE_CLUSTER = "include_cluster"


class FacePipelineService(QObject):
    """
    Central face-pipeline orchestrator.

    Usage (from any UI entry point):
        svc = FacePipelineService.instance()
        svc.start(project_id=1)
        svc.start(project_id=1, photo_paths=[...])  # scoped run
        svc.cancel(project_id=1)
    """

    # ── Signals ──────────────────────────────────────────────
    # (step_name: str, message: str, project_id: int)
    progress = Signal(str, str, int)

    # (processed: int, total: int, faces_so_far: int, project_id: int)
    batch_committed = Signal(int, int, int, int)

    # (cluster_count: int, total_faces: int, is_final: bool, project_id: int)
    # Emitted after each interim clustering pass and after the final cluster.
    # is_final=False means detection is still running (partial results).
    interim_clusters_ready = Signal(int, int, bool, int)

    # (results: dict, project_id: int)
    finished = Signal(dict, int)

    # (message: str, project_id: int)
    error = Signal(str, int)

    # Emitted when pipeline starts (project_id)
    started = Signal(int)

    # ── Singleton ────────────────────────────────────────────
    _instance: Optional["FacePipelineService"] = None

    @classmethod
    def instance(cls) -> "FacePipelineService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        # project_id → worker reference (for cancellation / duplicate guard)
        self._running: dict[int, object] = {}
        # project_id → tracked job_id in JobManager
        self._tracked_job_ids: Dict[int, int] = {}
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────

    def _validate_scope_paths(self, project_id, photo_paths):
        if not photo_paths:
            return []

        import os
        from reference_db import ReferenceDB

        normalized = []
        seen = set()

        for p in photo_paths:
            if not p:
                continue
            np = os.path.normcase(os.path.normpath(p))
            if np in seen:
                continue
            seen.add(np)
            normalized.append(p)

        # Video file extensions to exclude from face detection
        VIDEO_EXTENSIONS = (
            '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm',
            '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv'
        )
        video_filter = " AND " + " AND ".join(
            [f"LOWER(pm.path) NOT LIKE '%{ext}'" for ext in VIDEO_EXTENSIONS]
        )

        with ReferenceDB()._connect() as conn:
            placeholders = ",".join(["?"] * len(normalized))
            rows = conn.execute(f"""
                SELECT pm.path
                FROM photo_metadata pm
                WHERE pm.project_id = ?
                  AND pm.path IN ({placeholders})
                  {video_filter}
                  AND EXISTS (
                      SELECT 1 FROM project_images pi
                      WHERE pi.project_id = ?
                        AND pi.image_path = pm.path
                  )
            """, (project_id, *normalized, project_id)).fetchall()

        allowed = {r["path"] for r in rows}
        validated = [p for p in normalized if p in allowed]

        dropped = len(normalized) - len(validated)
        if dropped:
            logger.warning(
                "[PROJECT_SCOPE_SEAL][SERVICE] requested=%d validated=%d dropped=%d project=%d",
                len(normalized), len(validated), dropped, project_id
            )

        return validated

    def is_running(self, project_id: int) -> bool:
        with self._lock:
            return project_id in self._running

    def start(
        self,
        project_id: int,
        photo_paths: Optional[List[str]] = None,
        model: str = "buffalo_l",
        screenshot_policy: str = "detect_only",
        include_all_screenshot_faces: bool = False,
    ) -> bool:
        """
        Launch face detection + clustering for *project_id*.

        Args:
            project_id:        Must be a valid, non-None project id.
            photo_paths:       Optional scope — subset of photos to process.
            model:             InsightFace model name.
            screenshot_policy: "exclude", "detect_only", or "include_cluster".

        Returns True if pipeline was started, False if already running or
        project_id is invalid.
        """
        if not project_id:
            logger.warning("[FacePipelineService] Refusing to start — project_id is None/0")
            self.error.emit("No project selected", 0)
            return False

        with self._lock:
            if project_id in self._running:
                logger.info(
                    "[FacePipelineService] Pipeline already running for project %d, ignoring",
                    project_id,
                )
                return False

        # Validate project exists in DB
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                if not row:
                    logger.error("[FacePipelineService] project_id=%d not found", project_id)
                    self.error.emit(f"Project {project_id} not found", project_id)
                    return False
        except Exception as e:
            logger.error("[FacePipelineService] DB check failed: %s", e)
            self.error.emit(str(e), project_id)
            return False

        valid_policies = {"exclude", "detect_only", "include_cluster"}
        if screenshot_policy not in valid_policies:
            screenshot_policy = "detect_only"

        from workers.face_pipeline_worker import FacePipelineWorker
        from services.job_manager import get_job_manager

        validated_scope = self._validate_scope_paths(project_id, photo_paths)

        logger.info(
            "[FacePipelineService] Starting pipeline for project %d (scope=%s, model=%s, screenshot_policy=%s)",
            project_id,
            f"{len(validated_scope)} photos" if photo_paths is not None else "full project",
            model,
            screenshot_policy,
        )

        worker = FacePipelineWorker(
            project_id=project_id,
            model=model,
            screenshot_policy=screenshot_policy,
            include_all_screenshot_faces=include_all_screenshot_faces,
        )
        # If scoped paths were given, pass them through to the inner detection worker
        if photo_paths is not None:
            worker._scoped_photo_paths = validated_scope

        # ── Register as tracked job with JobManager for Activity Center + History ─────
        scope_desc = f"{len(photo_paths)} photos" if photo_paths else "all photos"
        description = f"Face detection ({scope_desc})"
        tracked_job_id: Optional[int] = None
        try:
            jm = get_job_manager()
            tracked_job_id = jm.register_tracked_job(
                job_type="face_pipeline",
                total=0,  # indeterminate until worker reports
                cancel_callback=lambda: self.cancel(project_id),
                description=description,
            )
            with self._lock:
                self._tracked_job_ids[project_id] = tracked_job_id
            logger.debug(
                "[FacePipelineService] Registered tracked job %d for project %d",
                tracked_job_id, project_id,
            )
        except Exception as e:
            logger.warning("[FacePipelineService] Could not register tracked job: %s", e)

        # ── Connect worker signals → service signals ─────────
        def _on_progress(step_name, message):
            self.progress.emit(step_name, message, project_id)
            # Forward to JobManager for Activity Center updates
            if tracked_job_id is not None:
                try:
                    jm = get_job_manager()
                    jm.report_progress(tracked_job_id, 0, 0, message=f"{step_name}: {message}")
                except Exception:
                    pass

        def _on_finished(results):
            with self._lock:
                self._running.pop(project_id, None)
                job_id = self._tracked_job_ids.pop(project_id, None)
            # Report completion to JobManager
            if job_id is not None:
                try:
                    jm = get_job_manager()
                    stats = results if isinstance(results, dict) else {"result": str(results)}
                    jm.complete_tracked_job(job_id, success=True, stats=stats)
                except Exception:
                    pass
            self.finished.emit(results, project_id)

        def _on_error(msg):
            with self._lock:
                self._running.pop(project_id, None)
                job_id = self._tracked_job_ids.pop(project_id, None)
            # Report failure to JobManager
            if job_id is not None:
                try:
                    jm = get_job_manager()
                    jm.complete_tracked_job(job_id, success=False, error=msg)
                except Exception:
                    pass
            self.error.emit(msg, project_id)

        def _on_interim_clusters(cluster_count, total_faces, is_final):
            self.interim_clusters_ready.emit(cluster_count, total_faces, is_final, project_id)

        worker.signals.progress.connect(_on_progress)
        worker.signals.finished.connect(_on_finished)
        worker.signals.error.connect(_on_error)
        worker.signals.interim_clusters_ready.connect(_on_interim_clusters)

        with self._lock:
            self._running[project_id] = worker

        self.started.emit(project_id)
        QThreadPool.globalInstance().start(worker)
        return True

    def cancel(self, project_id: int):
        """Cancel the running pipeline for *project_id* (idempotent)."""
        with self._lock:
            worker = self._running.get(project_id)
            job_id = self._tracked_job_ids.get(project_id)
        if worker and hasattr(worker, "cancel"):
            logger.info("[FacePipelineService] Cancelling pipeline for project %d", project_id)
            worker.cancel()
            # Note: _on_error or _on_finished callback will handle JobManager cleanup
        else:
            logger.debug("[FacePipelineService] No running pipeline for project %d", project_id)
            # Clean up orphaned tracked job if worker already gone
            if job_id is not None:
                with self._lock:
                    self._tracked_job_ids.pop(project_id, None)
                try:
                    from services.job_manager import get_job_manager
                    get_job_manager().cancel_tracked_job(job_id)
                except Exception:
                    pass
