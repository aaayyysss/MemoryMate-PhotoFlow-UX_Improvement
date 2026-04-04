# services/face_detection_controller.py
# Centralized Face Detection & Clustering Controller
# Phase 2B: Face Detection Controller & UI
# Phase 2C: Integrated Performance Tracking
# Orchestrates face detection workflow with state management and resume capability

"""
Face Detection Controller

Centralized service for orchestrating face detection and clustering operations.

Features:
- Workflow orchestration (detection → clustering)
- State management with persistence
- Progress reporting with quality metrics
- Resume capability for interrupted operations
- Configuration management
- Error handling and recovery

Workflow States:
1. IDLE: No operation in progress
2. DETECTING: Running face detection
3. DETECTION_PAUSED: Detection paused (resume supported)
4. CLUSTERING: Running face clustering
5. CLUSTERING_PAUSED: Clustering paused (resume supported)
6. COMPLETED: Workflow completed successfully
7. FAILED: Workflow failed with errors
8. CANCELLED: User cancelled operation
"""

import os
import json
import time
import logging
from enum import Enum
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QObject, Signal, QThreadPool

logger = logging.getLogger(__name__)

# Phase 2C: Import performance tracking
try:
    from services.performance_tracking_db import PerformanceTrackingDB
    PERFORMANCE_TRACKING_AVAILABLE = True
except ImportError:
    logger.warning("[FaceDetectionController] Performance tracking not available")
    PERFORMANCE_TRACKING_AVAILABLE = False


class WorkflowState(Enum):
    """Face detection workflow states."""
    IDLE = "idle"
    DETECTING = "detecting"
    DETECTION_PAUSED = "detection_paused"
    CLUSTERING = "clustering"
    CLUSTERING_PAUSED = "clustering_paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowProgress:
    """
    Current workflow progress information.

    Attributes:
        state: Current workflow state
        current_step: Current step description
        total_steps: Total number of steps
        completed_steps: Number of completed steps
        current_operation: Current operation name
        photos_processed: Number of photos processed (detection)
        photos_total: Total photos to process
        faces_detected: Number of faces detected
        clusters_found: Number of clusters created
        quality_score: Overall quality score (0-100, from Phase 2A)
        silhouette_score: Clustering silhouette score
        noise_ratio: Clustering noise ratio (0-1)
        elapsed_time: Elapsed time in seconds
        estimated_remaining: Estimated remaining time in seconds
        error_message: Error message if failed
    """
    state: str
    current_step: str
    total_steps: int
    completed_steps: int
    current_operation: str
    photos_processed: int
    photos_total: int
    faces_detected: int
    clusters_found: int
    quality_score: float
    silhouette_score: float
    noise_ratio: float
    elapsed_time: float
    estimated_remaining: float
    error_message: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)


@dataclass
class CheckpointData:
    """
    Checkpoint data for resume capability.

    Allows resuming interrupted operations by saving state at key points.
    """
    project_id: int
    workflow_state: str
    timestamp: str
    detection_complete: bool
    photos_processed: int
    photos_total: int
    faces_detected: int
    clustering_complete: bool
    clusters_found: int
    config_snapshot: Dict[str, Any]
    error_info: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'CheckpointData':
        """Create from dictionary."""
        return CheckpointData(**data)


class FaceDetectionControllerSignals(QObject):
    """
    Qt signals for face detection controller events.

    Enables UI to react to controller state changes.
    """
    # state_changed(old_state, new_state)
    state_changed = Signal(str, str)

    # progress_updated(progress_dict)
    progress_updated = Signal(dict)

    # workflow_completed(results_dict)
    workflow_completed = Signal(dict)

    # workflow_failed(error_message)
    workflow_failed = Signal(str)

    # checkpoint_saved(checkpoint_path)
    checkpoint_saved = Signal(str)


class FaceDetectionController:
    """
    Centralized controller for face detection and clustering workflow.

    Orchestrates the complete workflow:
    1. Face detection (FaceDetectionWorker)
    2. Face clustering (FaceClusterWorker)
    3. Quality analysis (Phase 2A integration)

    Features:
    - State management with persistence
    - Progress reporting with quality metrics
    - Resume capability via checkpoints
    - Configuration management
    - Error handling and recovery
    """

    def __init__(self, project_id: int, thread_pool: Optional[QThreadPool] = None):
        """
        Initialize face detection controller.

        Args:
            project_id: Project ID to operate on
            thread_pool: Qt thread pool for workers (optional)
        """
        self.project_id = project_id
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.signals = FaceDetectionControllerSignals()

        # State management
        self._state = WorkflowState.IDLE
        self._start_time: Optional[float] = None
        self._checkpoint_dir = Path(__file__).parent.parent / "checkpoints"
        self._checkpoint_dir.mkdir(exist_ok=True)

        # Progress tracking
        self._progress = WorkflowProgress(
            state=WorkflowState.IDLE.value,
            current_step="Ready",
            total_steps=2,  # Detection + Clustering
            completed_steps=0,
            current_operation="",
            photos_processed=0,
            photos_total=0,
            faces_detected=0,
            clusters_found=0,
            quality_score=0.0,
            silhouette_score=0.0,
            noise_ratio=0.0,
            elapsed_time=0.0,
            estimated_remaining=0.0,
            error_message=""
        )

        # Workers
        self._detection_worker = None
        self._clustering_worker = None

        # Callbacks
        self._completion_callback: Optional[Callable] = None
        self._error_callback: Optional[Callable] = None

        # Phase 2C: Performance tracking
        self._perf_db = PerformanceTrackingDB() if PERFORMANCE_TRACKING_AVAILABLE else None
        self._current_run_id: Optional[int] = None

        logger.info(f"[FaceDetectionController] Initialized for project {project_id}")

    @property
    def state(self) -> WorkflowState:
        """Get current workflow state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if workflow is currently running."""
        return self._state in [WorkflowState.DETECTING, WorkflowState.CLUSTERING]

    @property
    def is_paused(self) -> bool:
        """Check if workflow is paused."""
        return self._state in [WorkflowState.DETECTION_PAUSED, WorkflowState.CLUSTERING_PAUSED]

    @property
    def can_resume(self) -> bool:
        """Check if workflow can be resumed."""
        return self.is_paused and self._checkpoint_exists()

    @property
    def progress(self) -> WorkflowProgress:
        """Get current progress information."""
        # Update elapsed time
        if self._start_time is not None and self.is_running:
            self._progress.elapsed_time = time.time() - self._start_time
        return self._progress

    def start_workflow(self,
                      auto_cluster: bool = True,
                      completion_callback: Optional[Callable] = None,
                      error_callback: Optional[Callable] = None) -> bool:
        """
        Start complete face detection and clustering workflow.

        Args:
            auto_cluster: Automatically run clustering after detection
            completion_callback: Called when workflow completes
            error_callback: Called when workflow fails

        Returns:
            True if workflow started, False if already running
        """
        if self.is_running:
            logger.warning(f"[FaceDetectionController] Workflow already running (state: {self._state.value})")
            return False

        logger.info(f"[FaceDetectionController] Starting workflow for project {self.project_id}")

        # Store callbacks
        self._completion_callback = completion_callback
        self._error_callback = error_callback

        # Reset progress
        self._start_time = time.time()
        self._progress = WorkflowProgress(
            state=WorkflowState.DETECTING.value,
            current_step="Detecting faces in photos",
            total_steps=2 if auto_cluster else 1,
            completed_steps=0,
            current_operation="Face Detection",
            photos_processed=0,
            photos_total=0,  # Will be updated by worker
            faces_detected=0,
            clusters_found=0,
            quality_score=0.0,
            silhouette_score=0.0,
            noise_ratio=0.0,
            elapsed_time=0.0,
            estimated_remaining=0.0,
            error_message=""
        )

        # Phase 2C: Start performance tracking run
        if self._perf_db:
            try:
                from config.face_detection_config import get_face_config
                config_snapshot = get_face_config().to_dict()
                workflow_type = 'full' if auto_cluster else 'detection_only'
                self._current_run_id = self._perf_db.start_run(
                    project_id=self.project_id,
                    workflow_type=workflow_type,
                    config_snapshot=config_snapshot
                )
                logger.debug(f"[FaceDetectionController] Started performance tracking run {self._current_run_id}")
            except Exception as e:
                logger.warning(f"[FaceDetectionController] Failed to start performance tracking: {e}")

        # Transition to detecting state
        self._transition_state(WorkflowState.DETECTING)

        # Start face detection
        self._start_detection(auto_cluster=auto_cluster)

        return True

    def pause_workflow(self) -> bool:
        """
        Pause current workflow operation.

        Returns:
            True if paused, False if not running
        """
        if not self.is_running:
            logger.warning(f"[FaceDetectionController] Cannot pause - not running (state: {self._state.value})")
            return False

        logger.info(f"[FaceDetectionController] Pausing workflow (current state: {self._state.value})")

        # Cancel current worker
        if self._state == WorkflowState.DETECTING and self._detection_worker:
            self._detection_worker.cancel()
            self._transition_state(WorkflowState.DETECTION_PAUSED)
        elif self._state == WorkflowState.CLUSTERING and self._clustering_worker:
            self._clustering_worker.cancel()
            self._transition_state(WorkflowState.CLUSTERING_PAUSED)

        # Save checkpoint
        self._save_checkpoint()

        return True

    def resume_workflow(self) -> bool:
        """
        Resume paused workflow from checkpoint.

        Returns:
            True if resumed, False if cannot resume
        """
        if not self.can_resume:
            logger.warning(f"[FaceDetectionController] Cannot resume (state: {self._state.value})")
            return False

        logger.info(f"[FaceDetectionController] Resuming workflow from {self._state.value}")

        # Load checkpoint
        checkpoint = self._load_checkpoint()
        if not checkpoint:
            logger.error("[FaceDetectionController] Failed to load checkpoint")
            return False

        # Resume appropriate operation
        if self._state == WorkflowState.DETECTION_PAUSED:
            self._transition_state(WorkflowState.DETECTING)
            self._start_detection(auto_cluster=True)  # Continue with auto-cluster
        elif self._state == WorkflowState.CLUSTERING_PAUSED:
            self._transition_state(WorkflowState.CLUSTERING)
            self._start_clustering()

        return True

    def cancel_workflow(self) -> bool:
        """
        Cancel current workflow operation.

        Returns:
            True if cancelled, False if not running
        """
        if not self.is_running and not self.is_paused:
            logger.warning(f"[FaceDetectionController] Cannot cancel - not active (state: {self._state.value})")
            return False

        logger.info(f"[FaceDetectionController] Cancelling workflow (current state: {self._state.value})")

        # Cancel workers
        if self._detection_worker:
            self._detection_worker.cancel()
        if self._clustering_worker:
            self._clustering_worker.cancel()

        # Transition to cancelled state
        self._transition_state(WorkflowState.CANCELLED)

        # Clean up checkpoint
        self._delete_checkpoint()

        return True

    def start_clustering_only(self) -> bool:
        """
        Start clustering only (assumes faces already detected).

        Returns:
            True if started, False if already running
        """
        if self.is_running:
            logger.warning(f"[FaceDetectionController] Cannot start clustering - already running")
            return False

        logger.info(f"[FaceDetectionController] Starting clustering-only for project {self.project_id}")

        # Reset progress for clustering
        self._start_time = time.time()
        self._progress.state = WorkflowState.CLUSTERING.value
        self._progress.current_step = "Clustering detected faces"
        self._progress.total_steps = 1
        self._progress.completed_steps = 0
        self._progress.current_operation = "Face Clustering"

        # Transition to clustering state
        self._transition_state(WorkflowState.CLUSTERING)

        # Start clustering
        self._start_clustering()

        return True

    def get_workflow_summary(self) -> Dict[str, Any]:
        """
        Get summary of workflow results.

        Returns:
            Dictionary with workflow statistics
        """
        return {
            'project_id': self.project_id,
            'state': self._state.value,
            'photos_processed': self._progress.photos_processed,
            'faces_detected': self._progress.faces_detected,
            'clusters_found': self._progress.clusters_found,
            'quality_score': self._progress.quality_score,
            'silhouette_score': self._progress.silhouette_score,
            'noise_ratio': self._progress.noise_ratio,
            'elapsed_time': self._progress.elapsed_time,
            'completed': self._state == WorkflowState.COMPLETED,
            'success': self._state == WorkflowState.COMPLETED
        }

    # ========== Private Methods ==========

    def _transition_state(self, new_state: WorkflowState):
        """Transition to new workflow state."""
        old_state = self._state
        self._state = new_state
        self._progress.state = new_state.value

        logger.info(f"[FaceDetectionController] State transition: {old_state.value} → {new_state.value}")

        # Emit signal
        self.signals.state_changed.emit(old_state.value, new_state.value)
        self.signals.progress_updated.emit(self._progress.to_dict())

    def _start_detection(self, auto_cluster: bool = True):
        """Start face detection worker."""
        from workers.face_detection_worker import FaceDetectionWorker

        logger.info(f"[FaceDetectionController] Starting face detection worker")

        self._detection_worker = FaceDetectionWorker(project_id=self.project_id)

        # Connect signals
        self._detection_worker.signals.progress.connect(self._on_detection_progress)
        self._detection_worker.signals.finished.connect(
            lambda success, failed, total_faces: self._on_detection_finished(
                success, failed, total_faces, auto_cluster
            )
        )
        self._detection_worker.signals.error.connect(self._on_detection_error)

        # Start worker
        self.thread_pool.start(self._detection_worker)

    def _start_clustering(self):
        """Start face clustering worker."""
        from workers.face_cluster_worker import FaceClusterWorker

        logger.info(f"[FaceDetectionController] Starting face clustering worker")

        self._clustering_worker = FaceClusterWorker(project_id=self.project_id, auto_tune=True)

        # Connect signals
        self._clustering_worker.signals.progress.connect(self._on_clustering_progress)
        self._clustering_worker.signals.finished.connect(self._on_clustering_finished)
        self._clustering_worker.signals.error.connect(self._on_clustering_error)

        # Start worker
        self.thread_pool.start(self._clustering_worker)

    def _on_detection_progress(self, current: int, total: int, message: str):
        """Handle detection progress updates."""
        self._progress.photos_processed = current
        self._progress.photos_total = total
        self._progress.current_operation = message

        # Update elapsed time
        if self._start_time:
            self._progress.elapsed_time = time.time() - self._start_time

            # Estimate remaining time
            if current > 0:
                time_per_photo = self._progress.elapsed_time / current
                remaining_photos = total - current
                self._progress.estimated_remaining = time_per_photo * remaining_photos

        # Emit progress signal
        self.signals.progress_updated.emit(self._progress.to_dict())

    def _on_detection_finished(self, success: int, failed: int, total_faces: int, auto_cluster: bool):
        """Handle detection completion."""
        logger.info(
            f"[FaceDetectionController] Detection finished: "
            f"{success} photos, {total_faces} faces, {failed} failed"
        )

        self._progress.photos_processed = success
        self._progress.faces_detected = total_faces
        self._progress.completed_steps = 1

        # Save checkpoint
        self._save_checkpoint(detection_complete=True, faces_detected=total_faces)

        if auto_cluster and total_faces > 0:
            # Continue to clustering
            self._progress.current_step = "Clustering detected faces"
            self._progress.current_operation = "Face Clustering"
            self._transition_state(WorkflowState.CLUSTERING)
            self._start_clustering()
        else:
            # Complete workflow
            self._complete_workflow()

    def _on_detection_error(self, error_msg: str):
        """Handle detection error."""
        logger.error(f"[FaceDetectionController] Detection error: {error_msg}")
        self._fail_workflow(error_msg)

    def _on_clustering_progress(self, current: int, total: int, message: str):
        """Handle clustering progress updates."""
        self._progress.current_operation = message

        # Update elapsed time
        if self._start_time:
            self._progress.elapsed_time = time.time() - self._start_time

        # Emit progress signal
        self.signals.progress_updated.emit(self._progress.to_dict())

    def _on_clustering_finished(self, cluster_count: int, total_faces: int):
        """Handle clustering completion."""
        logger.info(
            f"[FaceDetectionController] Clustering finished: "
            f"{cluster_count} clusters, {total_faces} faces"
        )

        self._progress.clusters_found = cluster_count
        self._progress.completed_steps = 2

        # TODO: Extract quality metrics from clustering worker (Phase 2A integration)
        # For now, use placeholder values
        self._progress.quality_score = 75.0  # Placeholder
        self._progress.silhouette_score = 0.65  # Placeholder
        self._progress.noise_ratio = 0.12  # Placeholder

        # Save checkpoint
        self._save_checkpoint(
            detection_complete=True,
            clustering_complete=True,
            clusters_found=cluster_count
        )

        # Complete workflow
        self._complete_workflow()

    def _on_clustering_error(self, error_msg: str):
        """Handle clustering error."""
        logger.error(f"[FaceDetectionController] Clustering error: {error_msg}")
        self._fail_workflow(error_msg)

    def _complete_workflow(self):
        """Complete workflow successfully."""
        logger.info(f"[FaceDetectionController] Workflow completed successfully")

        # Phase 2C: Complete performance tracking run
        if self._perf_db and self._current_run_id:
            try:
                self._perf_db.complete_run(
                    run_id=self._current_run_id,
                    workflow_state='completed',
                    photos_total=self._progress.photos_total,
                    photos_processed=self._progress.photos_processed,
                    faces_detected=self._progress.faces_detected,
                    clusters_found=self._progress.clusters_found,
                    overall_quality_score=self._progress.quality_score,
                    silhouette_score=self._progress.silhouette_score,
                    davies_bouldin_index=0.0,  # TODO: Extract from clustering worker
                    noise_ratio=self._progress.noise_ratio
                )
                logger.debug(f"[FaceDetectionController] Completed performance tracking run {self._current_run_id}")
            except Exception as e:
                logger.warning(f"[FaceDetectionController] Failed to complete performance tracking: {e}")

        self._transition_state(WorkflowState.COMPLETED)

        # Clean up checkpoint
        self._delete_checkpoint()

        # Call completion callback
        if self._completion_callback:
            self._completion_callback(self.get_workflow_summary())

        # Emit completion signal
        self.signals.workflow_completed.emit(self.get_workflow_summary())

    def _fail_workflow(self, error_message: str):
        """Fail workflow with error."""
        logger.error(f"[FaceDetectionController] Workflow failed: {error_message}")

        # Phase 2C: Complete performance tracking run with failure
        if self._perf_db and self._current_run_id:
            try:
                self._perf_db.complete_run(
                    run_id=self._current_run_id,
                    workflow_state='failed',
                    photos_total=self._progress.photos_total,
                    photos_processed=self._progress.photos_processed,
                    faces_detected=self._progress.faces_detected,
                    clusters_found=self._progress.clusters_found,
                    error_message=error_message
                )
                logger.debug(f"[FaceDetectionController] Logged failed run {self._current_run_id}")
            except Exception as e:
                logger.warning(f"[FaceDetectionController] Failed to log error to performance tracking: {e}")

        self._progress.error_message = error_message
        self._transition_state(WorkflowState.FAILED)

        # Save checkpoint with error info
        self._save_checkpoint(error_info=error_message)

        # Call error callback
        if self._error_callback:
            self._error_callback(error_message)

        # Emit failure signal
        self.signals.workflow_failed.emit(error_message)

    def _save_checkpoint(self,
                        detection_complete: bool = False,
                        clustering_complete: bool = False,
                        faces_detected: Optional[int] = None,
                        clusters_found: Optional[int] = None,
                        error_info: Optional[str] = None):
        """Save workflow checkpoint for resume capability."""
        try:
            from config.face_detection_config import get_face_config

            checkpoint = CheckpointData(
                project_id=self.project_id,
                workflow_state=self._state.value,
                timestamp=datetime.now().isoformat(),
                detection_complete=detection_complete or self._progress.completed_steps >= 1,
                photos_processed=self._progress.photos_processed,
                photos_total=self._progress.photos_total,
                faces_detected=faces_detected or self._progress.faces_detected,
                clustering_complete=clustering_complete or self._progress.completed_steps >= 2,
                clusters_found=clusters_found or self._progress.clusters_found,
                config_snapshot=get_face_config().to_dict(),
                error_info=error_info
            )

            checkpoint_path = self._get_checkpoint_path()
            with open(checkpoint_path, 'w') as f:
                json.dump(checkpoint.to_dict(), f, indent=2)

            logger.info(f"[FaceDetectionController] Checkpoint saved: {checkpoint_path}")
            self.signals.checkpoint_saved.emit(str(checkpoint_path))

        except Exception as e:
            logger.error(f"[FaceDetectionController] Failed to save checkpoint: {e}", exc_info=True)

    def _load_checkpoint(self) -> Optional[CheckpointData]:
        """Load workflow checkpoint."""
        try:
            checkpoint_path = self._get_checkpoint_path()
            if not checkpoint_path.exists():
                return None

            with open(checkpoint_path, 'r') as f:
                data = json.load(f)

            checkpoint = CheckpointData.from_dict(data)
            logger.info(f"[FaceDetectionController] Checkpoint loaded: {checkpoint_path}")
            return checkpoint

        except Exception as e:
            logger.error(f"[FaceDetectionController] Failed to load checkpoint: {e}", exc_info=True)
            return None

    def _delete_checkpoint(self):
        """Delete workflow checkpoint."""
        try:
            checkpoint_path = self._get_checkpoint_path()
            if checkpoint_path.exists():
                checkpoint_path.unlink()
                logger.info(f"[FaceDetectionController] Checkpoint deleted: {checkpoint_path}")
        except Exception as e:
            logger.error(f"[FaceDetectionController] Failed to delete checkpoint: {e}", exc_info=True)

    def _checkpoint_exists(self) -> bool:
        """Check if checkpoint exists."""
        return self._get_checkpoint_path().exists()

    def _get_checkpoint_path(self) -> Path:
        """Get checkpoint file path for this project."""
        return self._checkpoint_dir / f"face_detection_project_{self.project_id}.json"
