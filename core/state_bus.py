# core/state_bus.py
# Version 01.01.01.02 dated 20260214
# Canonical ProjectState Store with version-based UI invalidation.
#
# Design principles (revised from audit):
#   1. DB is the source of truth for data.  The store is the source of
#      truth for UI invalidation tokens and job lifecycle.
#   2. ui_epoch guards widget lifecycle (shutdown / restart).  Domain
#      version counters (media_v, people_v, ...) guard data freshness.
#      They are INDEPENDENT: bumping ui_epoch does NOT drop data actions.
#   3. Workers dispatch actions via QtActionBridge (single queued hop).
#      No panel refreshes itself; panels subscribe and check versions.
#   4. Subscribers are held via weakref to prevent dead-widget leaks.
#   5. Every dispatch is logged with version deltas for observability.
#
# Migration: coexists with UIRefreshMediator during incremental adoption.
# The store does not replace QRunnable / QThreadPool infrastructure; it
# only standardises how results flow back to the UI.
#
# References:
#   Qt thread affinity:  https://doc.qt.io/qt-6/qthreadpool.html
#   Python weakref:      https://docs.python.org/3/library/weakref.html

from __future__ import annotations

import logging
import time
import threading
import weakref
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Type,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain State
# ---------------------------------------------------------------------------

@dataclass
class JobSnapshot:
    """Lightweight summary of a tracked job."""
    job_id: int
    kind: str        # "scan", "post_scan", "faces", "embeddings", ...
    title: str
    status: str      # "queued" | "running" | "done" | "canceled" | "failed"
    progress: float = 0.0
    message: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class ProjectState:
    """
    Authoritative UI-coordination state.

    This is NOT the data store (SQLite is).  This holds:
      - identity: which project / folder the UI points at
      - version counters: monotonic tokens per domain
      - ui_epoch: widget-lifecycle gate (bumped on shutdown / restart only)
      - job registry: lightweight progress tracking

    Panels check version counters to decide whether to re-query the DB.
    Workers ALWAYS increment the relevant version after a DB commit;
    ``ui_epoch`` never gates data acceptance.
    """
    # Identity
    project_id: Optional[int] = None
    selected_folder_id: Optional[int] = None
    selected_branch_key: str = "all"

    # Domain version counters (monotonic, only ever incremented)
    media_v: int = 0
    tags_v: int = 0
    people_v: int = 0
    faces_v: int = 0
    duplicates_v: int = 0
    embeddings_v: int = 0
    stacks_v: int = 0
    videos_v: int = 0
    groups_v: int = 0
    settings_v: int = 0
    jobs_v: int = 0

    # Widget-lifecycle epoch.  Bumped ONLY on shutdown, restart, or
    # project-context reset that destroys widgets.  NOT used to reject
    # data actions from workers.
    ui_epoch: int = 0
    closing: bool = False

    # Job registry (lightweight snapshots, not bulk data)
    jobs: Dict[int, JobSnapshot] = field(default_factory=dict)

    # Last error (for status bar / toast)
    last_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@dataclass
class ActionMeta:
    """Provenance tag carried by every action."""
    source: str = ""          # e.g. "scan_worker", "ui", "post_scan"
    project_id: Optional[int] = None  # action's target project
    ts: float = field(default_factory=time.perf_counter)  # monotonic


# --- core lifecycle ---

@dataclass
class ShutdownRequested:
    meta: ActionMeta
    reason: str = "shutdown"


@dataclass
class AppRelaunchRequested:
    meta: ActionMeta
    reason: str = "relaunch"


@dataclass
class ProjectSelected:
    meta: ActionMeta
    project_id: int


@dataclass
class FolderSelected:
    meta: ActionMeta
    folder_id: Optional[int]


# --- scan pipeline ---

@dataclass
class ScanStarted:
    meta: ActionMeta
    job_id: int
    folder_path: str = ""
    incremental: bool = True


@dataclass
class ScanProgress:
    meta: ActionMeta
    job_id: int
    progress: float
    message: str = ""


@dataclass
class ScanCompleted:
    meta: ActionMeta
    job_id: int
    photos_indexed: int = 0
    videos_indexed: int = 0


# --- post-scan sub-stages ---

@dataclass
class EmbeddingsCompleted:
    meta: ActionMeta
    job_id: int
    generated: int = 0


@dataclass
class StacksCompleted:
    meta: ActionMeta
    job_id: int
    stacks_created: int = 0


@dataclass
class DuplicatesCompleted:
    meta: ActionMeta
    job_id: int
    exact_groups: int = 0
    similar_stacks: int = 0


@dataclass
class FacesCompleted:
    meta: ActionMeta
    job_id: int
    detected: int = 0
    clustered: int = 0


@dataclass
class GroupsChanged:
    """Dispatched when people groups are created, updated, deleted, or re-indexed."""
    meta: ActionMeta
    group_id: Optional[int] = None       # specific group, or None for all
    reason: str = ""                      # "created", "updated", "deleted", "reindexed"


@dataclass
class GroupIndexCompleted:
    """Dispatched when a group's materialized match cache finishes computing."""
    meta: ActionMeta
    group_id: int = 0
    match_count: int = 0
    scope: str = "same_photo"


@dataclass
class TagsChanged:
    meta: ActionMeta
    photo_ids: List[int] = field(default_factory=list)


@dataclass
class SettingsChanged:
    meta: ActionMeta
    key: str = ""


# --- job lifecycle ---

@dataclass
class JobRegistered:
    meta: ActionMeta
    job: JobSnapshot = field(default_factory=lambda: JobSnapshot(0, "", "", "queued"))


@dataclass
class JobProgress:
    meta: ActionMeta
    job_id: int
    progress: float = 0.0
    message: str = ""


@dataclass
class JobFinished:
    meta: ActionMeta
    job_id: int
    status: str = "done"   # "done" | "canceled" | "failed"
    message: str = ""


# --- error (non-recursive, see design notes) ---

@dataclass
class ErrorRaised:
    meta: ActionMeta
    message: str = ""
    where: str = ""


# Union of all action types for type hints
AnyAction = (
    ShutdownRequested | AppRelaunchRequested | ProjectSelected
    | FolderSelected | ScanStarted | ScanProgress | ScanCompleted
    | EmbeddingsCompleted | StacksCompleted | DuplicatesCompleted
    | FacesCompleted | GroupsChanged | GroupIndexCompleted
    | TagsChanged | SettingsChanged
    | JobRegistered | JobProgress | JobFinished | ErrorRaised
)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

# Subscriber signature: (new_state, action) -> None
Subscriber = Callable[[ProjectState, AnyAction], None]

# Handler signature: (state, action) -> state  (pure mutation in-place is fine)
Handler = Callable[[ProjectState, Any], None]

# WeakRef wrapper type
_WeakSub = weakref.ref


class Store:
    """
    Single, thread-safe dispatch point for all state transitions.

    Thread safety model:
      - State mutations happen under ``_lock``.
      - Subscribers are notified outside the lock to prevent deadlocks.
      - Background threads MUST use ``QtActionBridge.dispatch_async()``
        to deliver actions on the GUI thread.
      - Direct ``store.dispatch()`` is only safe from the GUI thread.
    """

    def __init__(self, initial_state: Optional[ProjectState] = None) -> None:
        self._state: ProjectState = initial_state or ProjectState()
        self._lock = threading.RLock()
        self._subscribers: List[_WeakSub] = []
        self._handlers: Dict[Type, List[Handler]] = {}
        self._log_enabled: bool = True

    # -- state access -------------------------------------------------------

    @property
    def state(self) -> ProjectState:
        with self._lock:
            # Return a reference; state is mutable but only store mutates it.
            return self._state

    # -- handler registration -----------------------------------------------

    def on(self, action_type: Type) -> Callable[[Handler], Handler]:
        """
        Decorator to register a handler for an action type.

        Handlers mutate ``state`` in-place.  They must NOT perform IO,
        DB calls, or touch Qt widgets.

        Example::

            @store.on(ScanCompleted)
            def handle_scan_completed(state, action):
                state.media_v += 1
                if action.videos_indexed:
                    state.videos_v += 1
        """
        def _wrap(fn: Handler) -> Handler:
            self._handlers.setdefault(action_type, []).append(fn)
            return fn
        return _wrap

    # -- subscription -------------------------------------------------------

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        """
        Register a subscriber (called after every dispatch).

        Uses weakref internally: if the callable's owner (bound method's
        ``__self__``) is garbage-collected or the PySide6 C++ wrapper is
        deleted, the subscription is silently pruned.

        Returns an explicit unsubscribe callable for deterministic cleanup.
        """
        ref = _make_weak_sub(fn)
        with self._lock:
            self._subscribers.append(ref)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(ref)
                except ValueError:
                    pass

        return unsubscribe

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, action: AnyAction) -> None:
        """
        Apply *action* to state, notify subscribers.

        Must be called from the GUI thread (or under ``_lock`` during
        startup before any widgets exist).
        """
        action_name = type(action).__name__

        with self._lock:
            old_versions = self._snapshot_versions()

            # Run registered handlers
            handlers = self._handlers.get(type(action), [])
            for h in handlers:
                try:
                    h(self._state, action)
                except Exception:
                    logger.exception(
                        "[Store] Handler %s crashed on %s", h.__name__, action_name
                    )

            new_versions = self._snapshot_versions()
            new_state = self._state

            # Copy live subscribers (prune dead refs)
            live: List[Subscriber] = []
            pruned = 0
            new_subs: List[_WeakSub] = []
            for ref in self._subscribers:
                fn = _resolve_weak_sub(ref)
                if fn is not None:
                    live.append(fn)
                    new_subs.append(ref)
                else:
                    pruned += 1
            if pruned:
                self._subscribers = new_subs

        # Log outside lock
        if self._log_enabled:
            self._log_dispatch(action_name, action, old_versions, new_versions, len(live))

        # Notify subscribers outside lock (prevents re-entrant deadlocks)
        for fn in live:
            try:
                fn(new_state, action)
            except Exception:
                logger.exception(
                    "[Store] Subscriber %s crashed on %s",
                    getattr(fn, "__name__", repr(fn)), action_name,
                )

    # -- helpers ------------------------------------------------------------

    def make_meta(self, source: str = "") -> ActionMeta:
        """Create ActionMeta with current project_id."""
        return ActionMeta(source=source, project_id=self._state.project_id)

    def _snapshot_versions(self) -> Dict[str, int]:
        s = self._state
        return {
            "media_v": s.media_v,
            "tags_v": s.tags_v,
            "people_v": s.people_v,
            "faces_v": s.faces_v,
            "duplicates_v": s.duplicates_v,
            "embeddings_v": s.embeddings_v,
            "stacks_v": s.stacks_v,
            "videos_v": s.videos_v,
            "groups_v": s.groups_v,
            "settings_v": s.settings_v,
            "jobs_v": s.jobs_v,
            "ui_epoch": s.ui_epoch,
        }

    def _log_dispatch(
        self,
        action_name: str,
        action: AnyAction,
        old_v: Dict[str, int],
        new_v: Dict[str, int],
        n_subscribers: int,
    ) -> None:
        deltas = {
            k: f"{old_v[k]}→{new_v[k]}"
            for k in old_v
            if old_v[k] != new_v[k]
        }
        job_id = getattr(action, "job_id", "")
        extra = f" job={job_id}" if job_id else ""
        source = getattr(action, "meta", None)
        src = f" src={source.source}" if source and source.source else ""
        delta_str = f" deltas={deltas}" if deltas else ""

        logger.info(
            "[Store] %s%s%s%s  →  %d subscriber(s)",
            action_name, extra, src, delta_str, n_subscribers,
        )


# ---------------------------------------------------------------------------
# Default handlers (register on a Store instance)
# ---------------------------------------------------------------------------

def register_default_handlers(store: Store) -> None:
    """
    Register the built-in state-transition handlers.

    These are pure state mutations — no IO, no DB, no widgets.
    """

    @store.on(ShutdownRequested)
    def _(state: ProjectState, action: ShutdownRequested) -> None:
        state.closing = True
        state.ui_epoch += 1

    @store.on(AppRelaunchRequested)
    def _(state: ProjectState, action: AppRelaunchRequested) -> None:
        state.ui_epoch += 1
        state.closing = False
        state.jobs = {}
        state.last_error = None

    @store.on(ProjectSelected)
    def _(state: ProjectState, action: ProjectSelected) -> None:
        state.project_id = action.project_id
        state.selected_folder_id = None
        state.selected_branch_key = "all"
        # NOTE: we do NOT bump ui_epoch here.  Switching projects should
        # not invalidate in-flight workers.  We bump media_v so panels
        # know to re-query for the new project.
        state.media_v += 1
        state.people_v += 1
        state.faces_v += 1
        state.duplicates_v += 1
        state.embeddings_v += 1
        state.stacks_v += 1
        state.videos_v += 1
        state.groups_v += 1

    @store.on(FolderSelected)
    def _(state: ProjectState, action: FolderSelected) -> None:
        state.selected_folder_id = action.folder_id

    # --- scan pipeline ---

    @store.on(ScanStarted)
    def _(state: ProjectState, action: ScanStarted) -> None:
        state.jobs[action.job_id] = JobSnapshot(
            job_id=action.job_id,
            kind="scan",
            title="Scanning images",
            status="running",
            started_at=time.perf_counter(),
        )
        state.jobs_v += 1

    @store.on(ScanProgress)
    def _(state: ProjectState, action: ScanProgress) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.progress = action.progress
            j.message = action.message
            j.status = "running"

    @store.on(ScanCompleted)
    def _(state: ProjectState, action: ScanCompleted) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.status = "done"
            j.progress = 1.0
            j.finished_at = time.perf_counter()
        state.media_v += 1
        if action.videos_indexed:
            state.videos_v += 1
        state.jobs_v += 1

    @store.on(EmbeddingsCompleted)
    def _(state: ProjectState, action: EmbeddingsCompleted) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.status = "done"
            j.progress = 1.0
            j.finished_at = time.perf_counter()
        state.embeddings_v += 1
        state.jobs_v += 1

    @store.on(StacksCompleted)
    def _(state: ProjectState, action: StacksCompleted) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.status = "done"
            j.progress = 1.0
            j.finished_at = time.perf_counter()
        state.stacks_v += 1
        state.jobs_v += 1

    @store.on(DuplicatesCompleted)
    def _(state: ProjectState, action: DuplicatesCompleted) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.status = "done"
            j.progress = 1.0
            j.finished_at = time.perf_counter()
        state.duplicates_v += 1
        state.jobs_v += 1

    @store.on(FacesCompleted)
    def _(state: ProjectState, action: FacesCompleted) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.status = "done"
            j.progress = 1.0
            j.finished_at = time.perf_counter()
        state.people_v += 1
        state.faces_v += 1
        state.jobs_v += 1

    @store.on(GroupsChanged)
    def _(state: ProjectState, action: GroupsChanged) -> None:
        state.groups_v += 1

    @store.on(GroupIndexCompleted)
    def _(state: ProjectState, action: GroupIndexCompleted) -> None:
        state.groups_v += 1

    @store.on(TagsChanged)
    def _(state: ProjectState, action: TagsChanged) -> None:
        state.tags_v += 1

    @store.on(SettingsChanged)
    def _(state: ProjectState, action: SettingsChanged) -> None:
        state.settings_v += 1

    # --- job lifecycle ---

    @store.on(JobRegistered)
    def _(state: ProjectState, action: JobRegistered) -> None:
        state.jobs[action.job.job_id] = action.job
        state.jobs_v += 1

    @store.on(JobProgress)
    def _(state: ProjectState, action: JobProgress) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.progress = action.progress
            j.message = action.message

    @store.on(JobFinished)
    def _(state: ProjectState, action: JobFinished) -> None:
        j = state.jobs.get(action.job_id)
        if j:
            j.status = action.status
            j.message = action.message
            j.finished_at = time.perf_counter()
        state.jobs_v += 1

    # --- error (non-recursive: handler never raises or dispatches) ---

    @store.on(ErrorRaised)
    def _(state: ProjectState, action: ErrorRaised) -> None:
        state.last_error = f"{action.where}: {action.message}" if action.where else action.message


# ---------------------------------------------------------------------------
# Qt Bridge (single queued hop, GUI thread delivery)
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QObject, Signal, Slot, Qt

    class QtActionBridge(QObject):
        """
        Thread-safe action dispatcher.

        Workers call ``bridge.dispatch_async(action)`` from any thread.
        The action is delivered to ``Store.dispatch()`` on the GUI thread
        via a single ``QueuedConnection`` hop.
        """
        _queued = Signal(object)

        def __init__(self, store: Store, parent: Optional[QObject] = None) -> None:
            super().__init__(parent)
            self._store = store
            self._queued.connect(self._on_queued, Qt.QueuedConnection)

        def dispatch_async(self, action: AnyAction) -> None:
            """Dispatch *action* on the GUI thread (safe from any thread)."""
            self._queued.emit(action)

        @Slot(object)
        def _on_queued(self, action: AnyAction) -> None:
            self._store.dispatch(action)

except ImportError:
    # Allow importing state_bus for unit tests without PySide6.
    QtActionBridge = None  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# VersionedPanelMixin — for panels that subscribe to store
# ---------------------------------------------------------------------------

class VersionedPanelMixin:
    """
    Mixin for Qt panels that subscribe to the store.

    Tracks which domain versions the panel has already rendered.
    ``changed(key, new_value)`` returns True only when the version
    actually changed, preventing redundant reloads.

    Example::

        class MyPanel(QWidget, VersionedPanelMixin):
            def __init__(self, store):
                QWidget.__init__(self)
                VersionedPanelMixin.__init__(self)
                self._unsub = store.subscribe(self._on_state_changed)

            def _on_state_changed(self, state, action):
                if self.changed("media_v", state.media_v):
                    self._reload_media()
                if self.changed("duplicates_v", state.duplicates_v):
                    self._reload_duplicates()
    """

    def __init__(self) -> None:
        self._last_seen_versions: Dict[str, int] = {}

    def changed(self, key: str, new_value: int) -> bool:
        """Return True if *key* version advanced since last check."""
        old = self._last_seen_versions.get(key)
        if old is None or old != new_value:
            self._last_seen_versions[key] = new_value
            return True
        return False

    def reset_versions(self) -> None:
        """Force all versions to re-trigger on next check."""
        self._last_seen_versions.clear()


# ---------------------------------------------------------------------------
# Weakref helpers (bound-method safe)
# ---------------------------------------------------------------------------

def _make_weak_sub(fn: Subscriber) -> _WeakSub:
    """
    Create a weak reference to *fn*.

    For bound methods, uses a custom weak reference that releases when
    either the function or the instance is collected.  This prevents the
    Store from preventing garbage collection of deleted Qt widgets.
    """
    if hasattr(fn, "__self__") and hasattr(fn, "__func__"):
        # Bound method — prevent preventing GC of the owner
        return _WeakMethod(fn)
    return weakref.ref(fn)


def _resolve_weak_sub(ref: _WeakSub) -> Optional[Subscriber]:
    """Resolve a weak subscription reference.  Returns None if dead."""
    result = ref()
    return result


class _WeakMethod:
    """
    Weak reference to a bound method.

    ``weakref.ref(bound_method)`` dies immediately because bound methods
    are ephemeral objects.  This stores weak refs to the instance and
    function separately, and reconstitutes the bound method on resolve.
    """
    __slots__ = ("_obj_ref", "_func_ref", "__weakref__")

    def __init__(self, method: Callable) -> None:
        self._obj_ref = weakref.ref(method.__self__)
        self._func_ref = weakref.ref(method.__func__)

    def __call__(self) -> Optional[Callable]:
        obj = self._obj_ref()
        func = self._func_ref()
        if obj is None or func is None:
            return None
        # Extra safety for PySide6 C++ wrappers
        try:
            import shiboken6
            if not shiboken6.isValid(obj):
                return None
        except (ImportError, Exception):
            pass
        return func.__get__(obj, type(obj))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _WeakMethod):
            return self._obj_ref == other._obj_ref and self._func_ref == other._func_ref
        return NotImplemented

    def __hash__(self) -> int:
        return hash((id(self._obj_ref), id(self._func_ref)))


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_store_instance: Optional[Store] = None
_bridge_instance: Optional[Any] = None  # QtActionBridge or None


def init_store(initial_state: Optional[ProjectState] = None) -> Store:
    """
    Create the global Store singleton and register default handlers.

    Call once during app startup (before any dispatches).
    """
    global _store_instance
    store = Store(initial_state)
    register_default_handlers(store)
    _store_instance = store
    logger.info("[Store] Initialized (ui_epoch=%d)", store.state.ui_epoch)
    return store


def get_store() -> Store:
    """Return the global Store singleton.  Raises if not initialized."""
    if _store_instance is None:
        raise RuntimeError(
            "Store not initialized.  Call core.state_bus.init_store() during app startup."
        )
    return _store_instance


def init_bridge(store: Optional[Store] = None, parent: Optional[Any] = None) -> Any:
    """
    Create the global QtActionBridge singleton.

    Call once after QApplication is created (requires event loop).
    """
    global _bridge_instance
    if QtActionBridge is None:
        raise RuntimeError("PySide6 is required for QtActionBridge")
    s = store or get_store()
    bridge = QtActionBridge(s, parent=parent)
    _bridge_instance = bridge
    logger.info("[Store] QtActionBridge initialized")
    return bridge


def get_bridge() -> Any:
    """Return the global QtActionBridge singleton."""
    if _bridge_instance is None:
        raise RuntimeError(
            "Bridge not initialized.  Call core.state_bus.init_bridge() after QApplication."
        )
    return _bridge_instance
