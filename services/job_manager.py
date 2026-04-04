"""
JobManager - Central Background Jobs Orchestration Service

Version: 1.0.0
Date: 2026-02-01

Best-practice background jobs system inspired by Google Photos, iPhone Photos, and Lightroom.
Provides non-blocking UI, resumable jobs, prioritization, and incremental results.

Key Principles:
1. Never run heavy work on UI thread
2. Work in small chunks with checkpoints
3. Make jobs resumable + cancelable
4. Prioritize user-visible content
5. Surface partial results immediately

Architecture:
    JobManager (singleton)
    ├── WorkerPool (QThreadPool)
    ├── ActiveJobs (tracking running workers)
    ├── Signals (progress, partial_results, completed)
    └── Database (ml_job table via JobService)

Usage:
    from services.job_manager import get_job_manager

    job_manager = get_job_manager()

    # Enqueue a face detection job
    job_id = job_manager.enqueue(
        job_type='face_scan',
        project_id=1,
        priority=JobPriority.NORMAL
    )

    # Connect to signals for UI updates
    job_manager.signals.progress.connect(on_progress)
    job_manager.signals.partial_results.connect(on_partial_results)
    job_manager.signals.job_completed.connect(on_completed)

    # Pause/Resume/Cancel
    job_manager.pause(job_id)
    job_manager.resume(job_id)
    job_manager.cancel(job_id)

    # Pause all background work
    job_manager.pause_all()
"""

import os
import time
import uuid
import json
import functools
from enum import IntEnum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Set
from threading import Lock

from PySide6.QtCore import QObject, Signal, QThreadPool, QTimer, QRunnable, Slot

from services.job_service import get_job_service, Job
from repository.job_history_repository import JobHistoryRepository
from logging_config import get_logger

logger = get_logger(__name__)


class JobPriority(IntEnum):
    """
    Job priority levels.

    Higher priority jobs run first. Use CRITICAL for user-initiated
    actions and LOW for background library scanning.
    """
    LOW = 0           # Deep archive scanning
    NORMAL = 50       # Standard background processing
    HIGH = 100        # Recent imports, visible folders
    CRITICAL = 200    # User-initiated action (e.g., clicked "Scan Faces")


class JobType:
    """Job type constants."""
    SCAN = 'scan'
    FACE_SCAN = 'face_scan'
    FACE_EMBED = 'face_embed'
    FACE_CLUSTER = 'face_cluster'
    FACE_PIPELINE = 'face_pipeline'
    EMBEDDING = 'embedding'
    DUPLICATE_HASH = 'duplicate_hash'
    DUPLICATE_GROUP = 'duplicate_group'
    POST_SCAN = 'post_scan'
    MODEL_WARMUP = 'model_warmup'  # FIX 2026-02-08: Async model loading to prevent UI freeze


class JobStatus:
    """Job status constants."""
    QUEUED = 'queued'
    RUNNING = 'running'
    PAUSED = 'paused'
    COMPLETED = 'done'
    FAILED = 'failed'
    CANCELED = 'canceled'


@dataclass
class JobProgress:
    """Progress information for a job."""
    job_id: int
    job_type: str
    processed: int
    total: int
    rate: float = 0.0  # items per second
    eta_seconds: float = 0.0
    message: str = ""
    started_at: Optional[float] = None


@dataclass
class PartialResults:
    """Partial results emitted during job execution."""
    job_id: int
    job_type: str
    # Counts
    new_items_count: int = 0
    total_items_count: int = 0
    # Recent items (for UI preview)
    recent_items: List[Dict[str, Any]] = field(default_factory=list)
    # Type-specific data
    extra_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActiveJob:
    """Tracks an active job and its worker."""
    job_id: int
    job_type: str
    project_id: int
    worker: Optional[QRunnable] = None
    worker_id: str = ""
    started_at: float = 0.0
    processed: int = 0
    total: int = 0
    paused: bool = False
    cancel_requested: bool = False
    # Stored (signal, slot) pairs for deterministic disconnect on cleanup
    _connections: List[tuple] = field(default_factory=list)


class JobManagerSignals(QObject):
    """
    Qt signals for job manager events.

    Connect to these signals for UI updates.
    """
    # progress(job_id, processed, total, rate, eta, message)
    progress = Signal(int, int, int, float, float, str)

    # partial_results(job_type, new_count, total_count, recent_items_json)
    partial_results = Signal(str, int, int, str)

    # job_started(job_id, job_type, total_items)
    job_started = Signal(int, str, int)

    # job_completed(job_id, job_type, success, stats_json)
    job_completed = Signal(int, str, bool, str)

    # job_failed(job_id, job_type, error_message)
    job_failed = Signal(int, str, str)

    # job_canceled(job_id, job_type)
    job_canceled = Signal(int, str)

    # job_paused(job_id, job_type)
    job_paused = Signal(int, str)

    # job_resumed(job_id, job_type)
    job_resumed = Signal(int, str)

    # job_log(job_id, job_type, message) — log line for Activity Center
    job_log = Signal(int, str, str)

    # all_jobs_completed()
    all_jobs_completed = Signal()

    # active_jobs_changed(active_count)
    active_jobs_changed = Signal(int)


class JobManager(QObject):
    """
    Central job orchestration service.

    Manages background workers, provides pause/resume/cancel,
    tracks progress, and emits signals for UI updates.
    """

    # Singleton instance
    _instance: Optional['JobManager'] = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        super().__init__()

        # Initialize signals
        self.signals = JobManagerSignals()

        # Worker pool (bounded concurrency)
        self._thread_pool = QThreadPool.globalInstance()
        # Generation counter used to ignore stale worker callbacks after restart/shutdown.
        self._generation: int = 0
        # Set during application shutdown to prevent new job scheduling.
        self._shutdown_requested: bool = False
        self._max_workers = min(4, self._thread_pool.maxThreadCount())

        # Active jobs tracking
        self._active_jobs: Dict[int, ActiveJob] = {}
        self._jobs_lock = Lock()

        # Paused jobs (waiting to resume)
        self._paused_jobs: Set[int] = set()

        # Global pause flag
        self._global_pause = False

        # Job service (persistent queue)
        self._job_service = get_job_service()

        # Heartbeat timer (keep leases alive)
        self._heartbeat_timer = QTimer()
        self._heartbeat_timer.timeout.connect(self._send_heartbeats)
        self._heartbeat_timer.start(30000)  # Every 30 seconds

        # User activity throttling (reduce work when user is scrolling)
        self._user_active = False
        self._user_activity_timer = QTimer()
        self._user_activity_timer.timeout.connect(self._on_user_inactive)
        self._user_activity_timer.setSingleShot(True)

        # Progress debounce timer (avoid UI flood)
        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._emit_debounced_progress)
        self._progress_timer.start(250)  # 4 updates per second max

        # Pending progress updates (debounced)
        self._pending_progress: Dict[int, JobProgress] = {}
        self._progress_lock = Lock()

        # Tracked (externally-managed) jobs use negative IDs
        self._tracked_counter = 0
        self._tracked_cancel_callbacks: Dict[int, Callable] = {}
        self._tracked_descriptions: Dict[int, str] = {}

        # Persistent job history (graceful fallback if DB init fails)
        self._history_repo: Optional[JobHistoryRepository] = None
        self._last_hist_progress_write: Dict[int, float] = {}
        try:
            self._history_repo = JobHistoryRepository()
        except Exception as e:
            logger.warning(f"[JobManager] Job history disabled (DB init failed): {e}")

        self._initialized = True
        logger.info(f"[JobManager] Initialized with max {self._max_workers} workers")

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: Job Operations
    # ─────────────────────────────────────────────────────────────────────────

    def enqueue(
        self,
        job_type: str,
        project_id: int,
        priority: JobPriority = JobPriority.NORMAL,
        payload: Optional[Dict[str, Any]] = None,
        start_immediately: bool = True
    ) -> int:
        """
        Enqueue a new background job.

        Args:
            job_type: Type of job (face_scan, embedding, etc.)
            project_id: Project ID to process
            priority: Job priority (higher = runs first)
            payload: Additional job parameters
            start_immediately: Start job now (False = just queue)

        Returns:
            int: Job ID

        Example:
            job_id = job_manager.enqueue(
                job_type=JobType.FACE_SCAN,
                project_id=1,
                priority=JobPriority.HIGH
            )
        """
        # Build payload
        job_payload = payload or {}
        job_payload['project_id'] = project_id
        job_payload['job_type'] = job_type

        # Enqueue in persistent queue
        job_id = self._job_service.enqueue_job(
            kind=job_type,
            payload=job_payload,
            priority=int(priority),
            project_id=project_id
        )

        logger.info(
            f"[JobManager] Enqueued job {job_id}: {job_type} "
            f"(project={project_id}, priority={priority.name})"
        )

        # Start immediately unless paused or deferred
        if start_immediately and not self._global_pause:
            self._try_start_next_job()

        return job_id

    def pause(self, job_id: int) -> bool:
        """
        Pause a running job.

        The job will stop at the next checkpoint and save its progress.
        Resume with resume(job_id).

        Args:
            job_id: Job ID to pause

        Returns:
            bool: True if paused, False if not running
        """
        with self._jobs_lock:
            if job_id not in self._active_jobs:
                logger.warning(f"[JobManager] Cannot pause job {job_id}: not running")
                return False

            active = self._active_jobs[job_id]
            active.paused = True
            self._paused_jobs.add(job_id)

            # Signal worker to pause (if it supports it)
            if hasattr(active.worker, 'pause'):
                active.worker.pause()

            logger.info(f"[JobManager] Paused job {job_id}")
            self.signals.job_paused.emit(job_id, active.job_type)
            return True

    def resume(self, job_id: int) -> bool:
        """
        Resume a paused job.

        Args:
            job_id: Job ID to resume

        Returns:
            bool: True if resumed, False if not paused
        """
        if job_id not in self._paused_jobs:
            logger.warning(f"[JobManager] Cannot resume job {job_id}: not paused")
            return False

        self._paused_jobs.discard(job_id)

        with self._jobs_lock:
            if job_id in self._active_jobs:
                active = self._active_jobs[job_id]
                active.paused = False

                # Signal worker to resume (if it supports it)
                if hasattr(active.worker, 'resume'):
                    active.worker.resume()

        logger.info(f"[JobManager] Resumed job {job_id}")

        # Get job type from database if not in active jobs
        job = self._job_service.get_job(job_id)
        job_type = job.kind if job else 'unknown'
        self.signals.job_resumed.emit(job_id, job_type)

        # Try to start next job in case we have capacity
        self._try_start_next_job()
        return True

    def cancel(self, job_id: int) -> bool:
        """
        Cancel a queued or running job.

        Running jobs will stop at the next checkpoint.
        Progress is saved and can be resumed later.

        Args:
            job_id: Job ID to cancel

        Returns:
            bool: True if canceled
        """
        # Handle tracked (externally-managed) jobs — negative IDs have no DB record
        if job_id < 0:
            cb = self._tracked_cancel_callbacks.pop(job_id, None)
            if cb:
                try:
                    cb()
                except Exception as e:
                    logger.warning(
                        f"[JobManager] Cancel callback error for tracked job {job_id}: {e}")
            with self._jobs_lock:
                active = self._active_jobs.pop(job_id, None)
            self._tracked_descriptions.pop(job_id, None)
            if active:
                logger.info(f"[JobManager] Canceled tracked job {job_id}")
                if self._history_repo:
                    try:
                        self._history_repo.finish(
                            job_id=str(job_id), status='canceled', canceled=1)
                    except Exception:
                        pass
                self._last_hist_progress_write.pop(job_id, None)
                self.signals.job_canceled.emit(job_id, active.job_type)
                self.signals.active_jobs_changed.emit(len(self._active_jobs))
            return True

        # Mark in database
        self._job_service.cancel_job(job_id)

        # Remove from paused set
        self._paused_jobs.discard(job_id)

        with self._jobs_lock:
            if job_id in self._active_jobs:
                active = self._active_jobs[job_id]
                active.cancel_requested = True

                # Signal worker to cancel
                if hasattr(active.worker, 'cancel'):
                    active.worker.cancel()

                # Disconnect worker signals
                self._disconnect_worker(job_id, active)

                logger.info(f"[JobManager] Canceled running job {job_id}")
                if self._history_repo:
                    try:
                        self._history_repo.finish(
                            job_id=str(job_id), status='canceled', canceled=1)
                    except Exception:
                        pass
                self.signals.job_canceled.emit(job_id, active.job_type)
            else:
                logger.info(f"[JobManager] Canceled queued job {job_id}")
                # Get job type from database
                job = self._job_service.get_job(job_id)
                job_type = job.kind if job else 'unknown'
                if self._history_repo:
                    try:
                        self._history_repo.finish(
                            job_id=str(job_id), status='canceled', canceled=1)
                    except Exception:
                        pass
                self.signals.job_canceled.emit(job_id, job_type)

        return True

    def pause_all(self):
        """Pause all background processing."""
        self._global_pause = True
        with self._jobs_lock:
            for job_id, active in self._active_jobs.items():
                active.paused = True
                self._paused_jobs.add(job_id)
                if hasattr(active.worker, 'pause'):
                    active.worker.pause()

        logger.info("[JobManager] Paused all background jobs")

    def resume_all(self):
        """Resume all background processing."""
        self._global_pause = False

        # Resume paused jobs
        for job_id in list(self._paused_jobs):
            self.resume(job_id)

        # Start any queued jobs
        self._try_start_next_job()
        logger.info("[JobManager] Resumed all background jobs")

    def cancel_all(self, project_id: Optional[int] = None):
        """
        Cancel all jobs (optionally filtered by project).

        Args:
            project_id: If specified, only cancel jobs for this project
        """
        with self._jobs_lock:
            jobs_to_cancel = [
                job_id for job_id, active in self._active_jobs.items()
                if project_id is None or active.project_id == project_id
            ]

        for job_id in jobs_to_cancel:
            self.cancel(job_id)

        logger.info(
            f"[JobManager] Canceled {len(jobs_to_cancel)} jobs"
            + (f" for project {project_id}" if project_id else "")
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: Tracked (Externally-Managed) Jobs
    # ─────────────────────────────────────────────────────────────────────────

    def register_tracked_job(
        self,
        job_type: str,
        project_id: int = 0,
        total: int = 0,
        description: str = "",
        cancel_callback: Optional[Callable] = None,
    ) -> int:
        """
        Register an externally-managed job for unified signal flow.

        Use this for jobs whose threads/workers are managed outside JobManager
        (e.g. ScanController's QThread, FacePipelineService).  The job appears
        in the Activity Center and status bar like any other managed job.

        Returns a negative job_id to avoid clashing with DB auto-increment IDs.

        Args:
            job_type:         Category key (scan, face_pipeline, post_scan, …)
            project_id:       Project this job belongs to (informational)
            total:            Expected total work items (0 = indeterminate)
            description:      Human-readable label for the Activity Center card
            cancel_callback:  Called if the user clicks Cancel on this job

        Returns:
            int: Negative job_id
        """
        with self._jobs_lock:
            self._tracked_counter -= 1
            job_id = self._tracked_counter

        active = ActiveJob(
            job_id=job_id,
            job_type=job_type,
            project_id=project_id,
            worker=None,
            started_at=time.time(),
            total=total,
        )

        with self._jobs_lock:
            self._active_jobs[job_id] = active

        if cancel_callback:
            self._tracked_cancel_callbacks[job_id] = cancel_callback
        if description:
            self._tracked_descriptions[job_id] = description

        if self._history_repo:
            try:
                self._history_repo.upsert_start(
                    job_id=str(job_id), job_type=job_type, title=description)
            except Exception as e:
                logger.debug(f"[JobManager] Failed to persist job start {job_id}: {e}")

        logger.info(f"[JobManager] Registered tracked job {job_id}: {job_type} — {description}")
        self.signals.job_started.emit(job_id, job_type, total)
        self.signals.active_jobs_changed.emit(len(self._active_jobs))
        return job_id

    def report_progress(self, job_id: int, current: int, total: int, message: str = ""):
        """
        Report progress for a tracked (or managed) job.

        Progress flows through the same 250 ms debounce as managed jobs.

        Args:
            job_id:  Job ID (negative for tracked, positive for managed)
            current: Items processed so far
            total:   Total items expected
            message: One-line status message
        """
        with self._jobs_lock:
            active = self._active_jobs.get(job_id)
            if not active:
                return
            active.processed = current
            active.total = total

            elapsed = time.time() - active.started_at
            rate = current / elapsed if elapsed > 0 else 0
            remaining = total - current
            eta = remaining / rate if rate > 0 else 0

        with self._progress_lock:
            self._pending_progress[job_id] = JobProgress(
                job_id=job_id,
                job_type=active.job_type,
                processed=current,
                total=total,
                rate=rate,
                eta_seconds=eta,
                message=message,
                started_at=active.started_at,
            )

        # Throttled persistence (at most once per second per job)
        if self._history_repo:
            try:
                frac = float(current) / float(total) if total else 0.0
                now = time.time()
                last = self._last_hist_progress_write.get(job_id, 0.0)
                if now - last >= 1.0 or (total and current >= total):
                    self._history_repo.update_progress(
                        job_id=str(job_id), progress=frac)
                    self._last_hist_progress_write[job_id] = now
            except Exception:
                pass

    def report_log(self, job_id: int, message: str):
        """
        Emit a log line for any job (tracked or managed).

        The Activity Center picks this up and appends it to the
        job's expandable log viewer.

        Args:
            job_id:  Job ID
            message: Log line text
        """
        with self._jobs_lock:
            active = self._active_jobs.get(job_id)
            if not active:
                return
            job_type = active.job_type
        self.signals.job_log.emit(job_id, job_type, message)

    def complete_tracked_job(
        self,
        job_id: int,
        success: bool = True,
        stats: Optional[Dict[str, Any]] = None,
        error: str = "",
    ):
        """
        Complete a tracked (externally-managed) job.

        Emits ``job_completed`` (success) or ``job_failed``, then removes the
        job from the active set.

        Args:
            job_id:  Negative job ID returned by ``register_tracked_job``
            success: True if job succeeded
            stats:   Optional stats dict (serialised to JSON in signal)
            error:   Error message (used when success=False)
        """
        with self._jobs_lock:
            active = self._active_jobs.pop(job_id, None)
        self._tracked_cancel_callbacks.pop(job_id, None)
        self._tracked_descriptions.pop(job_id, None)

        if not active:
            logger.warning(f"[JobManager] complete_tracked_job: job {job_id} not found")
            return

        stats_json = json.dumps(stats or {})

        # Persist to history DB
        if self._history_repo:
            try:
                if success:
                    self._history_repo.finish(
                        job_id=str(job_id), status='succeeded', result=stats or {})
                else:
                    self._history_repo.finish(
                        job_id=str(job_id), status='failed', error=error or 'error')
            except Exception:
                pass
        self._last_hist_progress_write.pop(job_id, None)

        if success:
            logger.info(f"[JobManager] Tracked job {job_id} ({active.job_type}) completed: {stats}")
            self.signals.job_completed.emit(job_id, active.job_type, True, stats_json)
        else:
            logger.info(f"[JobManager] Tracked job {job_id} ({active.job_type}) failed: {error}")
            self.signals.job_failed.emit(job_id, active.job_type, error)

        self.signals.active_jobs_changed.emit(len(self._active_jobs))

        if len(self._active_jobs) == 0:
            self.signals.all_jobs_completed.emit()

    def get_job_description(self, job_id: int) -> str:
        """Return the human-readable description for a tracked job, or ``""``."""
        return self._tracked_descriptions.get(job_id, "")

    def get_history(self, limit: int = 200):
        """Return recent tracked-job runs from persistent history, most recent first."""
        if not self._history_repo:
            return []
        try:
            return self._history_repo.list_recent(limit=limit)
        except Exception:
            return []

    def clear_history(self) -> None:
        """Clear persisted job history."""
        if not self._history_repo:
            return
        try:
            self._history_repo.clear_all()
        except Exception:
            pass

    def notify_user_active(self):
        """
        Notify that user is actively interacting (scrolling, clicking).

        Call this from scroll handlers to temporarily reduce background
        work and prioritize UI responsiveness.
        """
        self._user_active = True
        # Reset timer - mark inactive after 2 seconds of no activity
        self._user_activity_timer.start(2000)

    def _on_user_inactive(self):
        """Called when user stops interacting."""
        self._user_active = False

    def is_user_active(self) -> bool:
        """Check if user is currently actively interacting."""
        return self._user_active

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: Priority Management
    # ─────────────────────────────────────────────────────────────────────────

    def boost_priority(self, job_id: int, priority: JobPriority = JobPriority.HIGH):
        """
        Boost priority of a queued job.

        Use when user focuses on content that needs this job's results.

        Args:
            job_id: Job ID
            priority: New priority level
        """
        try:
            from repository.base_repository import DatabaseConnection
            db = DatabaseConnection()
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE ml_job SET priority = ? WHERE job_id = ?",
                    (int(priority), job_id)
                )
                conn.commit()
            logger.info(f"[JobManager] Boosted job {job_id} to priority {priority.name}")
        except Exception as e:
            logger.error(f"[JobManager] Failed to boost priority: {e}")

    def prioritize_paths(self, paths: List[str], project_id: int):
        """
        Prioritize processing for specific paths.

        Use when user views a folder - its content should be processed first.

        Args:
            paths: File paths to prioritize
            project_id: Project ID
        """
        # TODO: Implement path-based prioritization
        # This would require tracking which paths each job covers
        # and reordering the queue accordingly
        logger.debug(f"[JobManager] Prioritize {len(paths)} paths (not yet implemented)")

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience Methods: Launch Common Jobs
    # ─────────────────────────────────────────────────────────────────────────

    def start_face_scan(
        self,
        project_id: int,
        photo_paths: Optional[List[str]] = None,
        priority: JobPriority = JobPriority.NORMAL,
        skip_processed: bool = True
    ) -> int:
        """
        Start a face detection scan job.

        Args:
            project_id: Project to scan
            photo_paths: Optional list of specific paths (None = all photos)
            priority: Job priority (use HIGH for user-initiated)
            skip_processed: Skip photos already processed

        Returns:
            int: Job ID

        Example:
            # Scan all photos in project
            job_id = manager.start_face_scan(project_id=1, priority=JobPriority.HIGH)

            # Scan specific photos
            job_id = manager.start_face_scan(
                project_id=1,
                photo_paths=['/path/to/photo1.jpg', '/path/to/photo2.jpg']
            )
        """
        return self.enqueue(
            job_type=JobType.FACE_SCAN,
            project_id=project_id,
            priority=priority,
            payload={
                'photo_paths': photo_paths,
                'skip_processed': skip_processed
            }
        )

    def start_embedding_extraction(
        self,
        project_id: int,
        photo_ids: Optional[List[int]] = None,
        model_variant: Optional[str] = None,
        priority: JobPriority = JobPriority.NORMAL
    ) -> int:
        """
        Start an embedding extraction job.

        Args:
            project_id: Project to process
            photo_ids: Optional list of photo IDs (None = all photos)
            model_variant: CLIP model variant (None = use project canonical)
            priority: Job priority

        Returns:
            int: Job ID

        Example:
            job_id = manager.start_embedding_extraction(
                project_id=1,
                priority=JobPriority.HIGH
            )
        """
        return self.enqueue(
            job_type=JobType.EMBEDDING,
            project_id=project_id,
            priority=priority,
            payload={
                'photo_ids': photo_ids,
                'model_variant': model_variant
            }
        )

    def start_duplicate_scan(
        self,
        project_id: int,
        priority: JobPriority = JobPriority.LOW
    ) -> int:
        """
        Start a duplicate detection scan.

        Args:
            project_id: Project to scan
            priority: Job priority (default LOW for background)

        Returns:
            int: Job ID
        """
        return self.enqueue(
            job_type=JobType.DUPLICATE_HASH,
            project_id=project_id,
            priority=priority
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: Status & Info
    # ─────────────────────────────────────────────────────────────────────────

    def get_active_jobs(self) -> List[Dict[str, Any]]:
        """
        Get list of active jobs.

        Returns:
            List of job info dicts with progress
        """
        with self._jobs_lock:
            return [
                {
                    'job_id': active.job_id,
                    'job_type': active.job_type,
                    'project_id': active.project_id,
                    'processed': active.processed,
                    'total': active.total,
                    'paused': active.paused,
                    'started_at': active.started_at,
                    'progress_pct': (active.processed / active.total * 100) if active.total > 0 else 0
                }
                for active in self._active_jobs.values()
            ]

    def get_queued_jobs(self, limit: int = 20) -> List[Job]:
        """Get queued jobs ordered by priority."""
        return self._job_service.get_jobs(status='queued', limit=limit)

    def get_job_stats(self) -> Dict[str, Any]:
        """
        Get job statistics.

        Returns:
            Dict with counts by status, active count, etc.
        """
        stats = self._job_service.get_job_stats()
        stats['active_count'] = len(self._active_jobs)
        stats['paused_count'] = len(self._paused_jobs)
        stats['global_pause'] = self._global_pause
        return stats

    def is_job_running(self, job_type: str, project_id: int) -> bool:
        """Check if a job of given type is running for a project."""
        with self._jobs_lock:
            for active in self._active_jobs.values():
                if active.job_type == job_type and active.project_id == project_id:
                    return True
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Worker Management (Internal)
    # ─────────────────────────────────────────────────────────────────────────

    def _try_start_next_job(self):
        """Try to start the next queued job if we have capacity."""
        if self._global_pause:
            return

        with self._jobs_lock:
            active_count = len([j for j in self._active_jobs.values() if not j.paused])
            if active_count >= self._max_workers:
                return

        # Get next queued job
        queued_jobs = self._job_service.get_jobs(status='queued', limit=1)
        if not queued_jobs:
            return

        job = queued_jobs[0]
        self._start_job(job)

    def _start_job(self, job: Job):
        """Start a job with its appropriate worker."""
        worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        # Claim the job
        if not self._job_service.claim_job(job.job_id, worker_id):
            logger.warning(f"[JobManager] Failed to claim job {job.job_id}")
            return

        # Create appropriate worker
        worker = self._create_worker(job)
        if not worker:
            logger.error(f"[JobManager] No worker available for job type: {job.kind}")
            self._job_service.complete_job(job.job_id, success=False, error="No worker for job type")
            return

        # Track active job
        payload = job.payload
        active = ActiveJob(
            job_id=job.job_id,
            job_type=job.kind,
            project_id=job.project_id or payload.get('project_id', 0),
            worker=worker,
            worker_id=worker_id,
            started_at=time.time(),
            total=payload.get('total', 0)
        )

        with self._jobs_lock:
            self._active_jobs[job.job_id] = active

        # Connect worker signals
        self._connect_worker_signals(job.job_id, worker)

        # Start worker
        self._thread_pool.start(worker)

        # Persist to job history
        if self._history_repo:
            try:
                desc = job.kind.replace('_', ' ').title()
                self._history_repo.upsert_start(
                    job_id=str(job.job_id), job_type=job.kind, title=desc)
            except Exception:
                pass

        logger.info(f"[JobManager] Started job {job.job_id}: {job.kind}")
        self.signals.job_started.emit(job.job_id, job.kind, active.total)
        self.signals.active_jobs_changed.emit(len(self._active_jobs))

    def _create_worker(self, job: Job) -> Optional[QRunnable]:
        """Create appropriate worker for job type."""
        payload = job.payload
        project_id = job.project_id or payload.get('project_id')

        if job.kind == JobType.FACE_SCAN:
            from workers.face_detection_worker import FaceDetectionWorker
            return FaceDetectionWorker(
                project_id=project_id,
                skip_processed=payload.get('skip_processed', True),
                photo_paths=payload.get('photo_paths')
            )

        elif job.kind == JobType.EMBEDDING:
            from workers.embedding_worker import EmbeddingWorker
            return EmbeddingWorker(
                job_id=job.job_id,
                photo_ids=payload.get('photo_ids', []),
                model_variant=payload.get('model_variant'),
                device=payload.get('device', 'auto'),
                project_id=project_id
            )

        elif job.kind in ('embed', 'semantic_embedding'):
            from workers.semantic_embedding_worker import SemanticEmbeddingWorker
            return SemanticEmbeddingWorker(
                photo_ids=payload.get('photo_ids', []),
                model_name=payload.get('model_name'),
                project_id=project_id,
                force_recompute=payload.get('force_recompute', False)
            )

        # Add more job types as needed
        return None

    def _connect_worker_signals(self, job_id: int, worker: QRunnable):
        """Connect worker signals to job manager handlers.

        Uses functools.partial instead of lambdas so slots are storable,
        disconnectable, and don't accidentally capture mutable state.
        All connections use Qt.QueuedConnection because workers fire
        from the QThreadPool, and downstream code may touch UI state.
        """
        if not hasattr(worker, 'signals'):
            return

        signals = worker.signals
        conn_type = Qt.ConnectionType.QueuedConnection

        with self._jobs_lock:
            active = self._active_jobs.get(job_id)
        if not active:
            return

        def _store(sig, slot):
            """Connect and remember (signal, slot) for deterministic disconnect."""
            sig.connect(slot, conn_type)
            active._connections.append((sig, slot))

        # Progress signal
        if hasattr(signals, 'progress'):
            _store(signals.progress,
                   functools.partial(self._on_worker_progress, job_id))

        # Finished signal
        if hasattr(signals, 'finished'):
            _store(signals.finished,
                   functools.partial(self._on_worker_finished_adapter, job_id))

        # Error signal
        if hasattr(signals, 'error'):
            _store(signals.error,
                   functools.partial(self._on_worker_error_adapter, job_id))

        # Face detected signal (partial results)
        if hasattr(signals, 'face_detected'):
            _store(signals.face_detected,
                   functools.partial(self._on_face_detected, job_id))

    def _on_worker_finished_adapter(self, job_id: int, *args):
        """Adapter for worker.signals.finished which may emit varying arg counts."""
        self._on_worker_finished(job_id, True, args)

    def _on_worker_error_adapter(self, job_id: int, *args):
        """Adapter for worker.signals.error which may emit varying arg counts."""
        self._on_worker_error(job_id, args)

    def _disconnect_worker(self, job_id: int, active: 'ActiveJob'):
        """Deterministically disconnect all worker signal connections for a job."""
        for sig, slot in active._connections:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # already disconnected or object deleted
        active._connections.clear()

    def _on_worker_progress(self, job_id: int, current: int, total: int, message: str):
        """Handle worker progress update."""
        with self._jobs_lock:
            if job_id in self._active_jobs:
                active = self._active_jobs[job_id]
                active.processed = current
                active.total = total

        # Calculate rate and ETA
        with self._jobs_lock:
            active = self._active_jobs.get(job_id)
            if active:
                elapsed = time.time() - active.started_at
                rate = current / elapsed if elapsed > 0 else 0
                remaining = total - current
                eta = remaining / rate if rate > 0 else 0
            else:
                rate = 0
                eta = 0

        # Debounce progress updates
        with self._progress_lock:
            self._pending_progress[job_id] = JobProgress(
                job_id=job_id,
                job_type=active.job_type if active else 'unknown',
                processed=current,
                total=total,
                rate=rate,
                eta_seconds=eta,
                message=message,
                started_at=active.started_at if active else None
            )

    def _on_worker_finished(self, job_id: int, success: bool, args: tuple):
        """Handle worker completion."""
        with self._jobs_lock:
            active = self._active_jobs.pop(job_id, None)

        if active:
            # Deterministic disconnect before anything else
            self._disconnect_worker(job_id, active)

            # Complete in database
            self._job_service.complete_job(job_id, success=success)

            # Build stats
            stats = {}
            if len(args) >= 3:
                stats = {
                    'success_count': args[0],
                    'failed_count': args[1],
                    'total_count': args[2]
                }

            # Persist to job history (managed jobs, positive IDs)
            if self._history_repo:
                try:
                    self._history_repo.finish(
                        job_id=str(job_id), status='succeeded' if success else 'failed',
                        result=stats if stats else None)
                except Exception:
                    pass

            logger.info(f"[JobManager] Job {job_id} completed: {stats}")
            self.signals.job_completed.emit(job_id, active.job_type, success, json.dumps(stats))
            self.signals.active_jobs_changed.emit(len(self._active_jobs))

            # Check if all jobs are done
            if len(self._active_jobs) == 0:
                self.signals.all_jobs_completed.emit()

        # Try to start next job
        self._try_start_next_job()

    def _on_worker_error(self, job_id: int, args: tuple):
        """Handle worker error."""
        error_msg = str(args[0]) if args else "Unknown error"

        with self._jobs_lock:
            active = self._active_jobs.pop(job_id, None)

        if active:
            # Deterministic disconnect before anything else
            self._disconnect_worker(job_id, active)

            self._job_service.complete_job(job_id, success=False, error=error_msg)

            # Persist to job history
            if self._history_repo:
                try:
                    self._history_repo.finish(
                        job_id=str(job_id), status='failed', error=error_msg)
                except Exception:
                    pass

            logger.error(f"[JobManager] Job {job_id} failed: {error_msg}")
            self.signals.job_failed.emit(job_id, active.job_type, error_msg)
            self.signals.active_jobs_changed.emit(len(self._active_jobs))

        # Try to start next job
        self._try_start_next_job()

    def _on_face_detected(self, job_id: int, path: str, count: int):
        """Handle face detection partial result."""
        with self._jobs_lock:
            active = self._active_jobs.get(job_id)
            if not active:
                return

        # Emit partial results for UI update
        recent_items = [{'path': path, 'face_count': count}]
        self.signals.partial_results.emit(
            JobType.FACE_SCAN,
            count,  # new count
            0,      # total count (not tracked here)
            json.dumps(recent_items)
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Timers & Maintenance
    # ─────────────────────────────────────────────────────────────────────────

    def _send_heartbeats(self):
        """Send heartbeats for all active jobs."""
        with self._jobs_lock:
            for job_id, active in self._active_jobs.items():
                if job_id < 0:
                    continue  # tracked jobs have no DB record
                if not active.paused:
                    progress = active.processed / active.total if active.total > 0 else 0
                    self._job_service.heartbeat(job_id, progress)

    def _emit_debounced_progress(self):
        """Emit debounced progress updates to avoid flooding UI."""
        with self._progress_lock:
            pending = self._pending_progress.copy()
            self._pending_progress.clear()

        for job_id, progress in pending.items():
            self.signals.progress.emit(
                job_id,
                progress.processed,
                progress.total,
                progress.rate,
                progress.eta_seconds,
                progress.message
            )

    # ------------------------------------------------------------------
    # Startup Throttle (Guardrail 2)
    # ------------------------------------------------------------------
    def enable_startup_throttle(self, max_threads: int = 1) -> None:
        """Temporarily reduce shared thread pool concurrency during startup.

        Prevents background jobs (maintenance, embedding warmups, etc.) from
        competing with initial layout stabilization and first paint.
        """
        try:
            if not hasattr(self, "_startup_throttle_prev"):
                self._startup_throttle_prev = self._thread_pool.maxThreadCount()
            self._thread_pool.setMaxThreadCount(max(1, int(max_threads)))
            logger.info(f"[JobManager] Startup throttle enabled (max_threads={max_threads})")
        except Exception as e:
            logger.warning(f"[JobManager] Failed to enable startup throttle: {e}")

    def disable_startup_throttle(self) -> None:
        """Restore shared thread pool concurrency after startup."""
        try:
            prev = getattr(self, "_startup_throttle_prev", None)
            if prev is None:
                return
            self._thread_pool.setMaxThreadCount(int(prev))
            delattr(self, "_startup_throttle_prev")
            logger.info(f"[JobManager] Startup throttle disabled (restored max_threads={prev})")
        except Exception as e:
            logger.warning(f"[JobManager] Failed to disable startup throttle: {e}")


    # --- Shutdown, restart, generation guards ---
    def current_generation(self) -> int:
        """Return the current generation counter."""
        return self._generation

    def bump_generation(self) -> int:
        """Increment generation and return new value.

        Any UI callback from long-running workers should compare against this
        generation to avoid touching deleted Qt objects after restart/shutdown.
        """
        self._generation += 1
        return self._generation

    def request_shutdown(self) -> None:
        """Prevent new jobs from being scheduled."""
        self._shutdown_requested = True

    def can_schedule(self) -> bool:
        """Return False once shutdown has been requested."""
        return not self._shutdown_requested

    def shutdown_barrier(self, *, timeout_ms: int = 10_000) -> bool:
        """Best-effort barrier to stop accepting new jobs, request cancellation, and wait.

        Returns True if the thread pool drained before timeout, else False.
        """
        # Stop future jobs and invalidate callbacks.
        self.request_shutdown()
        self.bump_generation()
        # Ask running jobs to cancel.
        try:
            self.cancel_all(reason="shutdown")
        except TypeError:
            # Older signature without reason kwarg.
            try:
                self.cancel_all()
            except Exception:
                pass
        except Exception:
            pass
        # Drain QThreadPool. QRunnables must cooperate with cancel flags.
        try:
            return bool(self._thread_pool.waitForDone(timeout_ms))
        except Exception:
            return False

# ─────────────────────────────────────────────────────────────────────────────
# Singleton Accessor
# ─────────────────────────────────────────────────────────────────────────────

_job_manager_instance: Optional[JobManager] = None
_job_manager_lock = Lock()


def get_job_manager() -> JobManager:
    """
    Get the singleton JobManager instance.

    Returns:
        JobManager: Singleton instance

    Example:
        from services.job_manager import get_job_manager

        manager = get_job_manager()
        job_id = manager.enqueue(JobType.FACE_SCAN, project_id=1)
    """
    global _job_manager_instance
    with _job_manager_lock:
        if _job_manager_instance is None:
            _job_manager_instance = JobManager()
        return _job_manager_instance
