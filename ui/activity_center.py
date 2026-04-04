"""
ActivityCenter - Non-Modal QDockWidget for Background Job Management

Version: 1.0.0
Date: 2026-02-04

A QDockWidget that displays all background jobs (Scan, Face Detection,
Embeddings, Duplicates) as a unified Activity panel.  Inspired by
Lightroom / Google Photos background activity patterns.

Features:
- Per-job progress bar, cancel button, expandable log viewer
- Cancel All button
- Safe, throttled UI updates (200 ms debounce so the event loop is never
  flooded)
- Tiny API surface: controllers call ``start_job()`` and get back an
  ``ActivityHandle`` they use to push updates from any thread.
- Auto-connects to the existing ``JobManager`` signals so managed jobs
  appear automatically.
- Completed jobs fade out after a short timeout.

The status bar stays as the "headline progress" — this dock is the
detailed view.

Usage from a controller::

    handle = self.main.activity_center.start_job(
        job_id="scan_1706000000",
        job_type="scan",
        description="Repository Scan",
        on_cancel=self.cancel,
    )
    handle.update(42, "Scanning 420/1000 files...")
    handle.log("Found 3 new photos in /vacation/2024")
    handle.complete("1000 photos indexed")
"""

import json
import time
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Optional, Dict, Callable

from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame, QScrollArea, QToolButton,
    QPlainTextEdit, QSizePolicy, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QAbstractItemView,
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QObject
from PySide6.QtGui import QTextCursor

from logging_config import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal signals — thread-safe bridge from any thread to UI thread
# ─────────────────────────────────────────────────────────────────────────────

class _ActivitySignals(QObject):
    """Qt signals for marshalling updates from worker threads to the UI."""
    progress = Signal(str, int, str)          # job_id, percent, message
    log_line = Signal(str, str)               # job_id, line
    completed = Signal(str, str)              # job_id, summary
    failed = Signal(str, str)                 # job_id, error
    cancel_requested = Signal(str)            # job_id
    job_registered = Signal(str, str, str)    # job_id, job_type, description


# ─────────────────────────────────────────────────────────────────────────────
# ActivityHandle — the tiny object callers hold to drive their job
# ─────────────────────────────────────────────────────────────────────────────

class ActivityHandle:
    """Lightweight handle returned by ``ActivityCenter.start_job()``.

    Controllers hold this and call ``update`` / ``log`` / ``complete``.
    All calls are thread-safe (marshalled via Qt signals).
    """

    def __init__(self, job_id: str, signals: _ActivitySignals):
        self.job_id = job_id
        self._sig = signals

    def update(self, percent: int, message: str = ""):
        """Update progress (0-100) and optional one-line status message."""
        self._sig.progress.emit(self.job_id, int(percent), message)

    def log(self, message: str):
        """Append a line to this job's log viewer."""
        self._sig.log_line.emit(self.job_id, message)

    def complete(self, summary: str = ""):
        """Mark job as successfully completed."""
        self._sig.completed.emit(self.job_id, summary)

    def fail(self, error: str = ""):
        """Mark job as failed."""
        self._sig.failed.emit(self.job_id, error)


# ─────────────────────────────────────────────────────────────────────────────
# ActivityJobCard — one card per active job
# ─────────────────────────────────────────────────────────────────────────────

class ActivityJobCard(QFrame):
    """Visual card for a single background job."""

    cancel_clicked = Signal(str)  # job_id

    JOB_ICONS = {
        "scan": "📂",
        "face": "👤",
        "face_scan": "👤",
        "face_pipeline": "👥",
        "embedding": "🧠",
        "embeddings": "🧠",
        "duplicates": "🔗",
        "duplicate_hash": "🔗",
        "post_scan": "⚙️",
        "post_scan_pipeline": "⚙️",
    }

    JOB_NAMES = {
        "scan": "Repository Scan",
        "face": "Face Detection",
        "face_scan": "Face Detection",
        "face_pipeline": "Face Pipeline",
        "embedding": "Embeddings",
        "embeddings": "Embeddings",
        "duplicates": "Duplicate Detection",
        "duplicate_hash": "Duplicate Detection",
        "post_scan": "Post-Scan Pipeline",
        "post_scan_pipeline": "Post-Scan Pipeline",
    }

    def __init__(self, job_id: str, job_type: str, description: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.job_id = job_id
        self.job_type = job_type
        self._description = description
        self._log_expanded = False
        self._log_lines: deque = deque(maxlen=200)
        self._is_complete = False
        self._started_at = time.time()
        self._build_ui()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            ActivityJobCard {
                background-color: #2D2D30;
                border: 1px solid #3E3E42;
                border-radius: 4px;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        # Row 1: icon + name + status + log-toggle + cancel
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        icon = self.JOB_ICONS.get(self.job_type, "⚙️")
        name = (self._description
                or self.JOB_NAMES.get(self.job_type,
                                      self.job_type.replace("_", " ").title()))
        self._title = QLabel(f"{icon} {name}")
        self._title.setStyleSheet(
            "color: #CCCCCC; font-size: 12px; font-weight: bold;")
        row1.addWidget(self._title)

        row1.addStretch()

        self._status_label = QLabel("Starting\u2026")
        self._status_label.setStyleSheet("color: #888888; font-size: 11px;")
        row1.addWidget(self._status_label)

        # Log toggle
        self._log_btn = QToolButton()
        self._log_btn.setText("\U0001F4CB")  # clipboard emoji
        self._log_btn.setToolTip("Toggle log")
        self._log_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; "
            "color: #AAAAAA; font-size: 13px; padding: 2px 4px; }"
            "QToolButton:hover { background: #3E3E42; border-radius: 2px; }")
        self._log_btn.clicked.connect(self._toggle_log)
        row1.addWidget(self._log_btn)

        # Cancel
        self._cancel_btn = QToolButton()
        self._cancel_btn.setText("\u2715")
        self._cancel_btn.setToolTip("Cancel")
        self._cancel_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; "
            "color: #AA6666; font-size: 14px; padding: 2px 4px; }"
            "QToolButton:hover { background: #4E3E3E; border-radius: 2px; }")
        self._cancel_btn.clicked.connect(
            lambda: self.cancel_clicked.emit(self.job_id))
        row1.addWidget(self._cancel_btn)

        root.addLayout(row1)

        # Row 2: progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setStyleSheet(
            "QProgressBar { background-color: #1E1E1E; border: none; "
            "border-radius: 3px; }"
            "QProgressBar::chunk { background-color: #0078D4; "
            "border-radius: 3px; }")
        root.addWidget(self._progress)

        # Row 3: elapsed time
        self._elapsed_label = QLabel("")
        self._elapsed_label.setStyleSheet(
            "color: #666666; font-size: 10px;")
        root.addWidget(self._elapsed_label)

        # Row 4: log viewer (hidden by default)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(140)
        self._log_view.setStyleSheet(
            "QPlainTextEdit { background-color: #1E1E1E; color: #B0B0B0; "
            "border: 1px solid #333; border-radius: 2px; "
            "font-family: 'Consolas', 'Courier New', monospace; "
            "font-size: 10px; }")
        self._log_view.hide()
        root.addWidget(self._log_view)

    # ── Public update methods (called from Activity Center slots) ───────

    def update_progress(self, percent: int, message: str = ""):
        if self._is_complete:
            return
        self._progress.setValue(max(0, min(100, percent)))
        if message:
            self._status_label.setText(message)
        elapsed = time.time() - self._started_at
        self._elapsed_label.setText(self._fmt_elapsed(elapsed))

    def append_log(self, line: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        self._log_lines.append(entry)
        if self._log_expanded:
            self._log_view.appendPlainText(entry)
            self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    def mark_complete(self, summary: str = ""):
        self._is_complete = True
        self._progress.setValue(100)
        self._status_label.setText(summary or "Complete")
        self._status_label.setStyleSheet("color: #4EC9B0; font-size: 11px;")
        self._cancel_btn.setEnabled(False)
        self._progress.setStyleSheet(
            "QProgressBar { background-color: #1E1E1E; border: none; "
            "border-radius: 3px; }"
            "QProgressBar::chunk { background-color: #4EC9B0; "
            "border-radius: 3px; }")
        elapsed = time.time() - self._started_at
        self._elapsed_label.setText(f"Finished in {self._fmt_elapsed(elapsed)}")

    def mark_failed(self, error: str = ""):
        self._is_complete = True
        self._status_label.setText(error or "Failed")
        self._status_label.setStyleSheet("color: #F44747; font-size: 11px;")
        self._cancel_btn.setEnabled(False)
        self._progress.setStyleSheet(
            "QProgressBar { background-color: #1E1E1E; border: none; "
            "border-radius: 3px; }"
            "QProgressBar::chunk { background-color: #F44747; "
            "border-radius: 3px; }")

    # ── Private helpers ─────────────────────────────────────────────────

    def _toggle_log(self):
        self._log_expanded = not self._log_expanded
        self._log_view.setVisible(self._log_expanded)
        if self._log_expanded:
            self._log_view.setPlainText("\n".join(self._log_lines))
            self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _fmt_elapsed(secs: float) -> str:
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"


# ─────────────────────────────────────────────────────────────────────────────
# ActivityCenter — the QDockWidget
# ─────────────────────────────────────────────────────────────────────────────

class ActivityCenter(QDockWidget):
    """Non-modal QDockWidget showing all background jobs.

    Attach to your ``QMainWindow`` with::

        self.activity_center = ActivityCenter(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,
                           self.activity_center)
    """

    # Emitted when a user clicks Cancel on any job
    cancel_requested = Signal(str)   # job_id

    # Emitted whenever the "headline" text changes (for status-bar sync)
    headline_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Activity", parent)

        self._cards: Dict[str, ActivityJobCard] = {}
        self._handles: Dict[str, ActivityHandle] = {}
        self._cancel_callbacks: Dict[str, Callable] = {}
        self._signals = _ActivitySignals()

        # Throttle: buffer progress updates, flush every 200 ms
        self._pending_updates: Dict[str, tuple] = {}
        self._update_lock = Lock()
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(200)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._flush_timer.start()

        # Auto-remove completed job cards after timeout
        self._remove_timer = QTimer(self)
        self._remove_timer.setInterval(2000)
        self._remove_timer.timeout.connect(self._cleanup_completed)
        self._remove_timer.start()
        self._completed_at: Dict[str, float] = {}

        # JobManager bridge state — prevent duplicate connects, allow cleanup
        self._jm_connected_id: Optional[int] = None
        self._jm_connections: list = []  # (signal, slot) pairs

        self._build_ui()
        self._connect_internal_signals()
        self._try_connect_job_manager()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        header = QHBoxLayout()
        header.setSpacing(6)

        self._badge = QLabel("No jobs")
        self._badge.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self._badge)

        header.addStretch()

        self._cancel_all_btn = QPushButton("Cancel All")
        self._cancel_all_btn.setFixedHeight(22)
        self._cancel_all_btn.setStyleSheet(
            "QPushButton { background: #3E3E42; color: #CCCCCC; "
            "border: 1px solid #555; border-radius: 3px; "
            "padding: 2px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #4E3E3E; "
            "border-color: #AA6666; }"
            "QPushButton:disabled { color: #555; }")
        self._cancel_all_btn.clicked.connect(self._on_cancel_all)
        self._cancel_all_btn.setEnabled(False)
        header.addWidget(self._cancel_all_btn)

        layout.addLayout(header)

        # Tabs: Active jobs and History
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet("QTabWidget::pane { border: none; }")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # --- Active tab ---
        active_tab = QWidget()
        active_layout = QVBoxLayout(active_tab)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")

        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()

        self._scroll.setWidget(self._cards_container)
        active_layout.addWidget(self._scroll, 1)

        # Empty state
        self._empty_label = QLabel("No background tasks running")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #666; font-size: 11px; padding: 20px;")
        active_layout.addWidget(self._empty_label)

        self._tabs.addTab(active_tab, "Active")

        # --- History tab ---
        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(6)

        hist_actions = QHBoxLayout()
        self._refresh_history_btn = QPushButton("Refresh")
        self._refresh_history_btn.setFixedHeight(22)
        self._refresh_history_btn.setStyleSheet(
            "QPushButton { background: #3E3E42; color: #CCCCCC; "
            "border: 1px solid #555; border-radius: 3px; "
            "padding: 2px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #505050; }")
        self._refresh_history_btn.clicked.connect(self.refresh_history)
        hist_actions.addWidget(self._refresh_history_btn)
        self._clear_history_btn = QPushButton("Clear")
        self._clear_history_btn.setFixedHeight(22)
        self._clear_history_btn.setStyleSheet(
            "QPushButton { background: #3E3E42; color: #CCCCCC; "
            "border: 1px solid #555; border-radius: 3px; "
            "padding: 2px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #4E3E3E; border-color: #AA6666; }")
        self._clear_history_btn.clicked.connect(self._on_clear_history)
        hist_actions.addWidget(self._clear_history_btn)
        hist_actions.addStretch(1)
        history_layout.addLayout(hist_actions)

        self._history_tree = QTreeWidget()
        self._history_tree.setHeaderLabels(
            ["Time", "Type", "Title", "Status", "Duration"])
        self._history_tree.setRootIsDecorated(False)
        self._history_tree.setAlternatingRowColors(True)
        self._history_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._history_tree.setUniformRowHeights(True)
        self._history_tree.header().setStretchLastSection(True)
        self._history_tree.setStyleSheet(
            "QTreeWidget { background-color: #1E1E1E; color: #CCCCCC; "
            "border: none; font-size: 11px; }"
            "QTreeWidget::item:alternate { background-color: #252526; }"
            "QHeaderView::section { background-color: #2D2D30; color: #CCC; "
            "border: 1px solid #3E3E42; padding: 2px 6px; font-size: 11px; }")
        history_layout.addWidget(self._history_tree, 1)

        self._tabs.addTab(history_tab, "History")

        layout.addWidget(self._tabs, 1)

        self.setWidget(container)
        self._update_empty_state()
        self.refresh_history()

    # ── History tab helpers ─────────────────────────────────────────────

    _STATUS_COLORS = {
        "succeeded": "#4EC9B0",
        "failed": "#F44747",
        "canceled": "#DCDCAA",
        "running": "#569CD6",
    }

    def refresh_history(self, limit: int = 200) -> None:
        """Populate the History tab from JobManager's persisted job history."""
        try:
            from services.job_manager import get_job_manager
            rows = get_job_manager().get_history(limit=limit)
        except Exception:
            rows = []

        self._history_tree.clear()
        for r in rows:
            created = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(r.created_ts))
            dur = ""
            if r.started_ts and r.finished_ts:
                dur_s = max(0.0, float(r.finished_ts) - float(r.started_ts))
                dur = self._fmt_duration(dur_s)
            item = QTreeWidgetItem(
                [created, r.job_type, r.title, r.status, dur])
            item.setData(0, Qt.ItemDataRole.UserRole, r.job_id)
            # Color-code the status column
            color = self._STATUS_COLORS.get(r.status, "#CCCCCC")
            from PySide6.QtGui import QBrush, QColor
            item.setForeground(3, QBrush(QColor(color)))
            self._history_tree.addTopLevelItem(item)

        for col in (0, 1, 3, 4):
            self._history_tree.resizeColumnToContents(col)

    def _on_clear_history(self) -> None:
        try:
            from services.job_manager import get_job_manager
            get_job_manager().clear_history()
        except Exception:
            pass
        self.refresh_history()

    def _on_tab_changed(self, index: int) -> None:
        """Auto-refresh History tab when the user switches to it."""
        if index == 1:  # History tab
            self.refresh_history()

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"

    # ── Signal wiring ───────────────────────────────────────────────────

    def _connect_internal_signals(self):
        """Wire _ActivitySignals to UI mutations (always QueuedConnection)."""
        conn = Qt.ConnectionType.QueuedConnection
        self._signals.job_registered.connect(self._create_card, conn)
        self._signals.progress.connect(self._handle_progress, conn)
        self._signals.log_line.connect(self._handle_log, conn)
        self._signals.completed.connect(self._handle_completed, conn)
        self._signals.failed.connect(self._handle_failed, conn)
        self._signals.cancel_requested.connect(self._handle_cancel, conn)

    def _try_connect_job_manager(self):
        """Auto-bridge the existing JobManager so its jobs appear here.

        Guards against duplicate connects via ``_jm_connected_id``.
        Stores (signal, slot) pairs so ``closeEvent`` can disconnect
        deterministically even though Qt *usually* cleans up.
        """
        try:
            from services.job_manager import get_job_manager
            mgr = get_job_manager()

            # Guard: skip if already connected to this exact manager instance
            mgr_id = id(mgr)
            if self._jm_connected_id == mgr_id:
                return
            # If connected to a stale manager, disconnect first
            if self._jm_connected_id is not None:
                self._disconnect_job_manager()

            pairs = [
                (mgr.signals.job_started, self._on_jm_started),
                (mgr.signals.progress, self._on_jm_progress),
                (mgr.signals.job_completed, self._on_jm_completed),
                (mgr.signals.job_failed, self._on_jm_failed),
                (mgr.signals.job_canceled, self._on_jm_canceled),
                (mgr.signals.job_log, self._on_jm_log),
            ]
            for sig, slot in pairs:
                sig.connect(slot)
            self._jm_connections = pairs
            self._jm_connected_id = mgr_id
            logger.info("[ActivityCenter] Connected to JobManager signals")
        except Exception as e:
            logger.info(f"[ActivityCenter] JobManager not available: {e}")

    def _disconnect_job_manager(self):
        """Disconnect all stored JobManager signal connections."""
        for sig, slot in self._jm_connections:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._jm_connections.clear()
        self._jm_connected_id = None

    def closeEvent(self, event):
        """Defensive disconnect on close to prevent stale signal delivery."""
        self._disconnect_job_manager()
        self._flush_timer.stop()
        self._remove_timer.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        """Reconnect JobManager signals when the dock is re-shown."""
        super().showEvent(event)
        self._try_connect_job_manager()
        self._flush_timer.start()
        self._remove_timer.start()

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def start_job(self, job_id: str, job_type: str, description: str = "",
                  on_cancel: Optional[Callable] = None) -> ActivityHandle:
        """Register a new background job and return a handle.

        Args:
            job_id:      Unique string (e.g. ``"scan_1706000000"``).
            job_type:    Category key (``scan``, ``face_pipeline``,
                         ``embedding``, ``duplicates``, ``post_scan_pipeline``).
            description: Human-readable label shown in the card title.
            on_cancel:   Callback invoked if the user clicks Cancel.

        Returns:
            ``ActivityHandle`` — the caller uses this to push
            ``update`` / ``log`` / ``complete`` / ``fail``.
        """
        handle = ActivityHandle(job_id, self._signals)
        self._handles[job_id] = handle
        if on_cancel:
            self._cancel_callbacks[job_id] = on_cancel

        # Create the card via signal (thread-safe)
        self._signals.job_registered.emit(job_id, job_type, description)
        return handle

    def get_headline(self) -> str:
        """One-line summary suitable for the status bar."""
        active = [c for c in self._cards.values() if not c._is_complete]
        if not active:
            return ""
        if len(active) == 1:
            return active[0]._status_label.text()
        return f"{len(active)} background tasks running"

    # ─────────────────────────────────────────────────────────────────────
    # Internal slot handlers
    # ─────────────────────────────────────────────────────────────────────

    @Slot(str, str, str)
    def _create_card(self, job_id: str, job_type: str, description: str):
        if job_id in self._cards:
            return
        card = ActivityJobCard(job_id, job_type, description)
        card.cancel_clicked.connect(self._handle_cancel)
        self._cards[job_id] = card
        # Insert before the trailing stretch
        self._cards_layout.insertWidget(
            self._cards_layout.count() - 1, card)
        self._update_empty_state()
        self._emit_headline()
        # Auto-show the dock when a job is registered
        if not self.isVisible():
            self.show()
        logger.info(f"[ActivityCenter] Job card created: {job_id} ({job_type})")

    @Slot(str, int, str)
    def _handle_progress(self, job_id: str, percent: int, message: str):
        # Buffer for throttled flush
        with self._update_lock:
            self._pending_updates[job_id] = (percent, message)

    @Slot(str, str)
    def _handle_log(self, job_id: str, line: str):
        card = self._cards.get(job_id)
        if card:
            card.append_log(line)

    @Slot(str, str)
    def _handle_completed(self, job_id: str, summary: str):
        card = self._cards.get(job_id)
        if card:
            card.mark_complete(summary)
            self._completed_at[job_id] = time.time()
        logger.info(f"[ActivityCenter] Job completed: {job_id} — {summary}")
        self._emit_headline()

    @Slot(str, str)
    def _handle_failed(self, job_id: str, error: str):
        card = self._cards.get(job_id)
        if card:
            card.mark_failed(error)
            self._completed_at[job_id] = time.time()
        logger.info(f"[ActivityCenter] Job failed: {job_id} — {error}")
        self._emit_headline()

    @Slot(str)
    def _handle_cancel(self, job_id: str):
        cb = self._cancel_callbacks.get(job_id)
        if cb:
            try:
                cb()
            except Exception as e:
                logger.warning(
                    f"[ActivityCenter] Cancel callback error for {job_id}: {e}")
        self.cancel_requested.emit(job_id)

    # ── Throttled flush ─────────────────────────────────────────────────

    def _flush_pending(self):
        """Push buffered progress updates to cards (main-thread timer)."""
        with self._update_lock:
            updates = self._pending_updates.copy()
            self._pending_updates.clear()

        for job_id, (pct, msg) in updates.items():
            card = self._cards.get(job_id)
            if card:
                card.update_progress(pct, msg)

        if updates:
            self._emit_headline()

    # ── Auto-remove completed cards ─────────────────────────────────────

    def _cleanup_completed(self):
        now = time.time()
        to_remove = [
            jid for jid, t in self._completed_at.items()
            if now - t > 10.0
        ]
        for jid in to_remove:
            self._completed_at.pop(jid, None)
            self._remove_card(jid)

    def _remove_card(self, job_id: str):
        card = self._cards.pop(job_id, None)
        self._handles.pop(job_id, None)
        self._cancel_callbacks.pop(job_id, None)
        if card:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._update_empty_state()
        self._emit_headline()

    # ── Visual helpers ──────────────────────────────────────────────────

    def _update_empty_state(self):
        has_cards = len(self._cards) > 0
        self._empty_label.setVisible(not has_cards)
        self._scroll.setVisible(has_cards)
        active = sum(1 for c in self._cards.values() if not c._is_complete)
        if active:
            self._badge.setText(
                f"{active} job{'s' if active != 1 else ''}")
        else:
            self._badge.setText("No jobs")
        self._cancel_all_btn.setEnabled(active > 0)

    def _emit_headline(self):
        self.headline_changed.emit(self.get_headline())

    def _on_cancel_all(self):
        for jid, card in list(self._cards.items()):
            if not card._is_complete:
                self._handle_cancel(jid)

    # ─────────────────────────────────────────────────────────────────────
    # JobManager bridge — auto-display managed jobs
    # ─────────────────────────────────────────────────────────────────────

    @Slot(int, str, int)
    def _on_jm_started(self, job_id: int, job_type: str, total: int):
        jid = f"jm_{job_id}"
        if jid not in self._cards:
            # Fetch description from JobManager (tracked jobs store it)
            desc = ""
            try:
                from services.job_manager import get_job_manager
                desc = get_job_manager().get_job_description(job_id)
            except Exception:
                pass

            def _cancel_jm():
                try:
                    from services.job_manager import get_job_manager
                    get_job_manager().cancel(job_id)
                except Exception:
                    pass
            self.start_job(jid, job_type, description=desc, on_cancel=_cancel_jm)

    @Slot(int, int, int, float, float, str)
    def _on_jm_progress(self, job_id: int, processed: int, total: int,
                        rate: float, eta: float, message: str):
        jid = f"jm_{job_id}"
        pct = int(processed / total * 100) if total > 0 else 0
        status = message or f"{processed:,}/{total:,}"
        if eta > 0:
            if eta < 60:
                status += f" (ETA {int(eta)}s)"
            elif eta < 3600:
                status += f" (ETA {int(eta / 60)}m)"
            else:
                status += f" (ETA {int(eta / 3600)}h {int((eta % 3600) / 60)}m)"
        with self._update_lock:
            self._pending_updates[jid] = (pct, status)

    @Slot(int, str, bool, str)
    def _on_jm_completed(self, job_id: int, job_type: str, success: bool,
                         stats_json: str):
        jid = f"jm_{job_id}"
        try:
            stats = json.loads(stats_json)
            count = stats.get("success_count", stats.get("total_count", 0))
            summary = f"Done ({count:,} items)" if success else "Failed"
        except Exception:
            summary = "Complete" if success else "Failed"

        card = self._cards.get(jid)
        if card:
            if success:
                card.mark_complete(summary)
            else:
                card.mark_failed(summary)
            self._completed_at[jid] = time.time()
        self._emit_headline()

    @Slot(int, str, str)
    def _on_jm_failed(self, job_id: int, job_type: str, error: str):
        jid = f"jm_{job_id}"
        card = self._cards.get(jid)
        if card:
            card.mark_failed(error[:80])
            self._completed_at[jid] = time.time()
        self._emit_headline()

    @Slot(int, str)
    def _on_jm_canceled(self, job_id: int, job_type: str):
        jid = f"jm_{job_id}"
        card = self._cards.get(jid)
        if card:
            card.mark_failed("Cancelled")
            self._completed_at[jid] = time.time()
        self._emit_headline()

    @Slot(int, str, str)
    def _on_jm_log(self, job_id: int, job_type: str, message: str):
        jid = f"jm_{job_id}"
        card = self._cards.get(jid)
        if card:
            card.append_log(message)
