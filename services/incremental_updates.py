"""
IncrementalUpdates - Debounced UI Updates from Background Jobs

Version: 1.0.0
Date: 2026-02-01

Provides debounced UI refresh signals for background job partial results.
Prevents UI flooding while ensuring responsive updates.

Features:
1. Debounced refresh (configurable interval)
2. Coalesced updates (multiple updates → single refresh)
3. Type-specific handlers (faces, duplicates, embeddings)
4. Incremental sidebar updates

Usage:
    from services.incremental_updates import get_update_manager

    # Connect to type-specific signals
    update_manager = get_update_manager()
    update_manager.faces_updated.connect(on_faces_changed)
    update_manager.duplicates_updated.connect(on_duplicates_changed)
    update_manager.embeddings_updated.connect(on_embeddings_changed)

    # Trigger updates (automatically debounced)
    update_manager.notify_faces_changed(project_id=1, new_count=10)
"""

import json
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from PySide6.QtCore import QObject, Signal, QTimer, Slot

from services.job_manager import get_job_manager, JobType
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PendingUpdate:
    """Tracks pending update information."""
    update_type: str
    project_id: Optional[int] = None
    new_count: int = 0
    total_count: int = 0
    last_updated: float = 0.0
    extra_data: Dict[str, Any] = field(default_factory=dict)


class IncrementalUpdateManager(QObject):
    """
    Manages debounced UI updates from background jobs.

    Coalesces rapid updates into periodic refreshes to avoid
    UI flooding while maintaining responsiveness.
    """

    # Type-specific signals
    # faces_updated(project_id, new_count, total_count, recent_items_json)
    faces_updated = Signal(int, int, int, str)

    # duplicates_updated(project_id, new_groups, total_groups, recent_items_json)
    duplicates_updated = Signal(int, int, int, str)

    # embeddings_updated(project_id, new_count, total_count)
    embeddings_updated = Signal(int, int, int)

    # Generic refresh signal
    # refresh_requested(update_type, project_id)
    refresh_requested = Signal(str, int)

    # Singleton
    _instance: Optional['IncrementalUpdateManager'] = None
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

        # Pending updates by type
        self._pending: Dict[str, PendingUpdate] = {}
        self._pending_lock = Lock()

        # Debounce interval (milliseconds)
        self._debounce_ms = 500

        # Debounce timer
        self._timer = QTimer()
        self._timer.timeout.connect(self._flush_pending)
        self._timer.start(self._debounce_ms)

        # Connect to JobManager partial results
        self._connect_job_manager()

        # Recent items cache (for UI preview)
        self._recent_faces: Dict[int, list] = {}  # project_id -> recent items
        self._recent_duplicates: Dict[int, list] = {}

        self._initialized = True
        logger.info("[IncrementalUpdateManager] Initialized")

    def _connect_job_manager(self):
        """Connect to JobManager partial results signal."""
        try:
            manager = get_job_manager()
            manager.signals.partial_results.connect(self._on_partial_results)
            manager.signals.job_completed.connect(self._on_job_completed)
            logger.debug("[IncrementalUpdateManager] Connected to JobManager")
        except Exception as e:
            logger.warning(f"[IncrementalUpdateManager] Could not connect to JobManager: {e}")

    @Slot(str, int, int, str)
    def _on_partial_results(self, job_type: str, new_count: int, total_count: int, recent_json: str):
        """Handle partial results from JobManager."""
        try:
            recent_items = json.loads(recent_json) if recent_json else []
        except Exception:
            recent_items = []

        if job_type == JobType.FACE_SCAN:
            # Extract project_id from recent items if available
            project_id = 0
            if recent_items and 'project_id' in recent_items[0]:
                project_id = recent_items[0]['project_id']

            self.notify_faces_changed(
                project_id=project_id,
                new_count=new_count,
                total_count=total_count,
                recent_items=recent_items
            )

        elif job_type in (JobType.DUPLICATE_HASH, JobType.DUPLICATE_GROUP):
            project_id = 0
            if recent_items and 'project_id' in recent_items[0]:
                project_id = recent_items[0]['project_id']

            self.notify_duplicates_changed(
                project_id=project_id,
                new_groups=new_count,
                total_groups=total_count,
                recent_items=recent_items
            )

        elif job_type in (JobType.EMBEDDING, 'embed', 'semantic_embedding'):
            project_id = 0
            self.notify_embeddings_changed(
                project_id=project_id,
                new_count=new_count,
                total_count=total_count
            )

    @Slot(int, str, bool, str)
    def _on_job_completed(self, job_id: int, job_type: str, success: bool, stats_json: str):
        """Handle job completion - trigger final refresh."""
        if not success:
            return

        try:
            stats = json.loads(stats_json) if stats_json else {}
        except Exception:
            stats = {}

        # Trigger immediate refresh for completed job
        if job_type == JobType.FACE_SCAN:
            self._emit_faces_update(force=True)
        elif job_type in (JobType.DUPLICATE_HASH, JobType.DUPLICATE_GROUP):
            self._emit_duplicates_update(force=True)
        elif job_type in (JobType.EMBEDDING, 'embed', 'semantic_embedding'):
            self._emit_embeddings_update(force=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: Notify Changes
    # ─────────────────────────────────────────────────────────────────────────

    def notify_faces_changed(
        self,
        project_id: int,
        new_count: int = 0,
        total_count: int = 0,
        recent_items: Optional[list] = None
    ):
        """
        Notify that faces have changed (debounced).

        Args:
            project_id: Project ID
            new_count: Number of new faces
            total_count: Total faces
            recent_items: Recent face items for preview
        """
        with self._pending_lock:
            key = f"faces_{project_id}"
            if key not in self._pending:
                self._pending[key] = PendingUpdate(
                    update_type='faces',
                    project_id=project_id
                )

            update = self._pending[key]
            update.new_count += new_count
            update.total_count = total_count
            update.last_updated = datetime.now().timestamp()

            # Track recent items
            if recent_items:
                if project_id not in self._recent_faces:
                    self._recent_faces[project_id] = []
                self._recent_faces[project_id].extend(recent_items)
                # Keep last 10
                self._recent_faces[project_id] = self._recent_faces[project_id][-10:]

    def notify_duplicates_changed(
        self,
        project_id: int,
        new_groups: int = 0,
        total_groups: int = 0,
        recent_items: Optional[list] = None
    ):
        """
        Notify that duplicates have changed (debounced).

        Args:
            project_id: Project ID
            new_groups: Number of new duplicate groups
            total_groups: Total duplicate groups
            recent_items: Recent items for preview
        """
        with self._pending_lock:
            key = f"duplicates_{project_id}"
            if key not in self._pending:
                self._pending[key] = PendingUpdate(
                    update_type='duplicates',
                    project_id=project_id
                )

            update = self._pending[key]
            update.new_count += new_groups
            update.total_count = total_groups
            update.last_updated = datetime.now().timestamp()

            if recent_items:
                if project_id not in self._recent_duplicates:
                    self._recent_duplicates[project_id] = []
                self._recent_duplicates[project_id].extend(recent_items)
                self._recent_duplicates[project_id] = self._recent_duplicates[project_id][-10:]

    def notify_embeddings_changed(
        self,
        project_id: int,
        new_count: int = 0,
        total_count: int = 0
    ):
        """
        Notify that embeddings have changed (debounced).

        Args:
            project_id: Project ID
            new_count: Number of new embeddings
            total_count: Total embeddings
        """
        with self._pending_lock:
            key = f"embeddings_{project_id}"
            if key not in self._pending:
                self._pending[key] = PendingUpdate(
                    update_type='embeddings',
                    project_id=project_id
                )

            update = self._pending[key]
            update.new_count += new_count
            update.total_count = total_count
            update.last_updated = datetime.now().timestamp()

    def notify_generic_refresh(self, update_type: str, project_id: int = 0):
        """Request a generic refresh (debounced)."""
        with self._pending_lock:
            key = f"{update_type}_{project_id}"
            self._pending[key] = PendingUpdate(
                update_type=update_type,
                project_id=project_id,
                last_updated=datetime.now().timestamp()
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Timer Handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _flush_pending(self):
        """Flush pending updates (called by timer)."""
        with self._pending_lock:
            if not self._pending:
                return

            pending = dict(self._pending)
            self._pending.clear()

        for key, update in pending.items():
            if update.update_type == 'faces':
                self._emit_faces_update(update)
            elif update.update_type == 'duplicates':
                self._emit_duplicates_update(update)
            elif update.update_type == 'embeddings':
                self._emit_embeddings_update(update)
            else:
                self.refresh_requested.emit(update.update_type, update.project_id or 0)

    def _emit_faces_update(self, update: Optional[PendingUpdate] = None, force: bool = False):
        """Emit faces_updated signal."""
        if update:
            project_id = update.project_id or 0
            recent = self._recent_faces.get(project_id, [])
            self.faces_updated.emit(
                project_id,
                update.new_count,
                update.total_count,
                json.dumps(recent)
            )
            self.refresh_requested.emit('faces', project_id)
        elif force:
            # Force refresh all projects
            for project_id in self._recent_faces.keys():
                self.refresh_requested.emit('faces', project_id)

    def _emit_duplicates_update(self, update: Optional[PendingUpdate] = None, force: bool = False):
        """Emit duplicates_updated signal."""
        if update:
            project_id = update.project_id or 0
            recent = self._recent_duplicates.get(project_id, [])
            self.duplicates_updated.emit(
                project_id,
                update.new_count,
                update.total_count,
                json.dumps(recent)
            )
            self.refresh_requested.emit('duplicates', project_id)
        elif force:
            for project_id in self._recent_duplicates.keys():
                self.refresh_requested.emit('duplicates', project_id)

    def _emit_embeddings_update(self, update: Optional[PendingUpdate] = None, force: bool = False):
        """Emit embeddings_updated signal."""
        if update:
            project_id = update.project_id or 0
            self.embeddings_updated.emit(
                project_id,
                update.new_count,
                update.total_count
            )
            self.refresh_requested.emit('embeddings', project_id)
        elif force:
            self.refresh_requested.emit('embeddings', 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Configuration
    # ─────────────────────────────────────────────────────────────────────────

    def set_debounce_interval(self, ms: int):
        """Set debounce interval in milliseconds."""
        self._debounce_ms = ms
        self._timer.setInterval(ms)

    def get_recent_faces(self, project_id: int) -> list:
        """Get recent face items for a project."""
        return self._recent_faces.get(project_id, [])

    def get_recent_duplicates(self, project_id: int) -> list:
        """Get recent duplicate items for a project."""
        return self._recent_duplicates.get(project_id, [])

    def clear_cache(self, project_id: Optional[int] = None):
        """Clear recent items cache."""
        if project_id is not None:
            self._recent_faces.pop(project_id, None)
            self._recent_duplicates.pop(project_id, None)
        else:
            self._recent_faces.clear()
            self._recent_duplicates.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton Accessor
# ─────────────────────────────────────────────────────────────────────────────

_update_manager_instance: Optional[IncrementalUpdateManager] = None
_update_manager_lock = Lock()


def get_update_manager() -> IncrementalUpdateManager:
    """
    Get the singleton IncrementalUpdateManager instance.

    Returns:
        IncrementalUpdateManager: Singleton instance
    """
    global _update_manager_instance
    with _update_manager_lock:
        if _update_manager_instance is None:
            _update_manager_instance = IncrementalUpdateManager()
        return _update_manager_instance
