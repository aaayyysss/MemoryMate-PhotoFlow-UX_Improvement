"""
BackgroundActivityPanel - Background Jobs Status UI

Version: 1.0.0
Date: 2026-02-01

A compact, non-intrusive panel for displaying background job status.
Inspired by Lightroom/Google Photos background activity indicators.

Features:
- Collapsible panel (minimal when collapsed)
- Multiple job tracking with progress bars
- Pause/Resume/Cancel controls
- Recent activity history
- ETA and rate display

Usage:
    from ui.background_activity_panel import BackgroundActivityPanel

    # Create panel (usually docked at bottom of main window)
    panel = BackgroundActivityPanel()
    main_layout.addWidget(panel)

    # Panel auto-connects to JobManager signals
"""

import json
from datetime import datetime
from typing import Dict, Optional, List, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QScrollArea, QSizePolicy, QToolButton
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont

from services.job_manager import get_job_manager, JobType
from logging_config import get_logger

logger = get_logger(__name__)


class JobCard(QFrame):
    """
    Individual job progress card.

    Shows job type, progress bar, ETA, and control buttons.
    """

    # Signals
    pause_clicked = Signal(int)   # job_id
    resume_clicked = Signal(int)  # job_id
    cancel_clicked = Signal(int)  # job_id

    def __init__(self, job_id: int, job_type: str, total: int = 0, parent=None):
        super().__init__(parent)
        self.job_id = job_id
        self.job_type = job_type
        self.total = total
        self.processed = 0
        self.rate = 0.0
        self.eta_seconds = 0.0
        self.is_paused = False
        self.started_at = datetime.now()

        self._init_ui()

    def _init_ui(self):
        """Initialize the job card UI."""
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            JobCard {
                background-color: #2D2D30;
                border: 1px solid #3E3E42;
                border-radius: 4px;
                padding: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 6, 8, 6)

        # Top row: Job type + controls
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        # Job type icon and label
        self.type_label = QLabel(self._get_job_icon() + " " + self._get_job_name())
        self.type_label.setStyleSheet("color: #CCCCCC; font-size: 12px; font-weight: bold;")
        top_row.addWidget(self.type_label)

        top_row.addStretch()

        # Progress text
        self.progress_text = QLabel("0 / 0")
        self.progress_text.setStyleSheet("color: #888888; font-size: 11px;")
        top_row.addWidget(self.progress_text)

        # Pause/Resume button
        self.pause_btn = QToolButton()
        self.pause_btn.setText("⏸")
        self.pause_btn.setToolTip("Pause")
        self.pause_btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: #AAAAAA;
                font-size: 14px;
                padding: 2px 4px;
            }
            QToolButton:hover {
                background: #3E3E42;
                border-radius: 2px;
            }
        """)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        top_row.addWidget(self.pause_btn)

        # Cancel button
        self.cancel_btn = QToolButton()
        self.cancel_btn.setText("✕")
        self.cancel_btn.setToolTip("Cancel")
        self.cancel_btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: #AA6666;
                font-size: 14px;
                padding: 2px 4px;
            }
            QToolButton:hover {
                background: #4E3E3E;
                border-radius: 2px;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        top_row.addWidget(self.cancel_btn)

        layout.addLayout(top_row)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1E1E1E;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #0078D4;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)

        # Bottom row: ETA and rate
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("color: #666666; font-size: 10px;")
        bottom_row.addWidget(self.eta_label)

        bottom_row.addStretch()

        self.rate_label = QLabel("")
        self.rate_label.setStyleSheet("color: #666666; font-size: 10px;")
        bottom_row.addWidget(self.rate_label)

        layout.addLayout(bottom_row)

    def _get_job_icon(self) -> str:
        """Get icon for job type."""
        icons = {
            JobType.FACE_SCAN: "👤",
            JobType.FACE_EMBED: "🧠",
            JobType.FACE_CLUSTER: "👥",
            JobType.EMBEDDING: "🔍",
            JobType.DUPLICATE_HASH: "🔗",
            JobType.DUPLICATE_GROUP: "📁",
            'embed': "🔍",
            'semantic_embedding': "🔍",
        }
        return icons.get(self.job_type, "⚙️")

    def _get_job_name(self) -> str:
        """Get display name for job type."""
        names = {
            JobType.FACE_SCAN: "Face Detection",
            JobType.FACE_EMBED: "Face Embeddings",
            JobType.FACE_CLUSTER: "People Grouping",
            JobType.EMBEDDING: "Visual Search",
            JobType.DUPLICATE_HASH: "Duplicate Scan",
            JobType.DUPLICATE_GROUP: "Duplicate Groups",
            'embed': "Embeddings",
            'semantic_embedding': "Semantic Search",
        }
        return names.get(self.job_type, self.job_type.replace('_', ' ').title())

    def update_progress(self, processed: int, total: int, rate: float, eta_seconds: float, message: str = ""):
        """Update progress display."""
        self.processed = processed
        self.total = total
        self.rate = rate
        self.eta_seconds = eta_seconds

        # Update progress bar
        pct = int((processed / total * 100) if total > 0 else 0)
        self.progress_bar.setValue(pct)

        # Update progress text
        self.progress_text.setText(f"{processed:,} / {total:,}")

        # Update ETA
        if eta_seconds > 0:
            if eta_seconds < 60:
                eta_str = f"{int(eta_seconds)}s"
            elif eta_seconds < 3600:
                eta_str = f"{int(eta_seconds / 60)}m"
            else:
                eta_str = f"{int(eta_seconds / 3600)}h {int((eta_seconds % 3600) / 60)}m"
            self.eta_label.setText(f"ETA: {eta_str}")
        else:
            self.eta_label.setText("")

        # Update rate
        if rate > 0:
            if rate >= 1:
                rate_str = f"{rate:.1f}/s"
            else:
                rate_str = f"{1/rate:.1f}s/item"
            self.rate_label.setText(rate_str)
        else:
            self.rate_label.setText("")

    def set_paused(self, paused: bool):
        """Update paused state."""
        self.is_paused = paused
        if paused:
            self.pause_btn.setText("▶")
            self.pause_btn.setToolTip("Resume")
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #1E1E1E;
                    border: none;
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background-color: #666666;
                    border-radius: 3px;
                }
            """)
        else:
            self.pause_btn.setText("⏸")
            self.pause_btn.setToolTip("Pause")
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #1E1E1E;
                    border: none;
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background-color: #0078D4;
                    border-radius: 3px;
                }
            """)

    def _on_pause_clicked(self):
        if self.is_paused:
            self.resume_clicked.emit(self.job_id)
        else:
            self.pause_clicked.emit(self.job_id)

    def _on_cancel_clicked(self):
        self.cancel_clicked.emit(self.job_id)


class BackgroundActivityPanel(QWidget):
    """
    Collapsible panel showing background job activity.

    Displays active jobs with progress, pause/resume/cancel controls,
    and recent activity history.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._job_cards: Dict[int, JobCard] = {}
        self._activity_history: List[str] = []
        self._is_expanded = True

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        """Initialize the panel UI."""
        self.setMinimumHeight(40)
        self.setStyleSheet("""
            BackgroundActivityPanel {
                background-color: #252526;
                border-top: 1px solid #3E3E42;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header bar (always visible)
        self.header = QFrame()
        self.header.setFixedHeight(32)
        self.header.setStyleSheet("""
            QFrame {
                background-color: #2D2D30;
                border-bottom: 1px solid #3E3E42;
            }
        """)

        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(8)

        # Expand/collapse toggle
        self.toggle_btn = QToolButton()
        self.toggle_btn.setText("▼")
        self.toggle_btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: #888888;
                font-size: 10px;
            }
        """)
        self.toggle_btn.clicked.connect(self._toggle_expanded)
        header_layout.addWidget(self.toggle_btn)

        # Status icon (animated when active)
        self.status_icon = QLabel("⚙️")
        self.status_icon.setStyleSheet("font-size: 14px;")
        header_layout.addWidget(self.status_icon)

        # Title
        self.title_label = QLabel("Background Activity")
        self.title_label.setStyleSheet("color: #CCCCCC; font-size: 12px; font-weight: bold;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        # Active count badge
        self.active_count = QLabel("")
        self.active_count.setStyleSheet("""
            QLabel {
                background-color: #0078D4;
                color: white;
                font-size: 10px;
                font-weight: bold;
                border-radius: 8px;
                padding: 2px 6px;
            }
        """)
        self.active_count.hide()
        header_layout.addWidget(self.active_count)

        # Global pause/resume
        self.global_pause_btn = QToolButton()
        self.global_pause_btn.setText("⏸ Pause All")
        self.global_pause_btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: #888888;
                font-size: 11px;
                padding: 4px 8px;
            }
            QToolButton:hover {
                background: #3E3E42;
                border-radius: 4px;
            }
        """)
        self.global_pause_btn.clicked.connect(self._on_global_pause)
        header_layout.addWidget(self.global_pause_btn)

        layout.addWidget(self.header)

        # Content area (collapsible)
        self.content = QFrame()
        self.content.setStyleSheet("background-color: #252526;")

        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(8)

        # Jobs scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
        """)

        self.jobs_container = QWidget()
        self.jobs_layout = QVBoxLayout(self.jobs_container)
        self.jobs_layout.setSpacing(8)
        self.jobs_layout.setContentsMargins(0, 0, 0, 0)
        self.jobs_layout.addStretch()

        self.scroll_area.setWidget(self.jobs_container)
        content_layout.addWidget(self.scroll_area)

        # No jobs message
        self.no_jobs_label = QLabel("No background tasks running")
        self.no_jobs_label.setAlignment(Qt.AlignCenter)
        self.no_jobs_label.setStyleSheet("color: #666666; font-size: 11px; padding: 20px;")
        content_layout.addWidget(self.no_jobs_label)

        layout.addWidget(self.content)

        # Start with panel visible
        self._update_visibility()

    def _connect_signals(self):
        """Connect to JobManager signals."""
        try:
            manager = get_job_manager()

            manager.signals.progress.connect(self._on_progress)
            manager.signals.job_started.connect(self._on_job_started)
            manager.signals.job_completed.connect(self._on_job_completed)
            manager.signals.job_failed.connect(self._on_job_failed)
            manager.signals.job_canceled.connect(self._on_job_canceled)
            manager.signals.job_paused.connect(self._on_job_paused)
            manager.signals.job_resumed.connect(self._on_job_resumed)
            manager.signals.active_jobs_changed.connect(self._on_active_jobs_changed)

            logger.debug("[BackgroundActivityPanel] Connected to JobManager signals")
        except Exception as e:
            logger.warning(f"[BackgroundActivityPanel] Could not connect to JobManager: {e}")

    def _toggle_expanded(self):
        """Toggle panel expanded/collapsed state."""
        self._is_expanded = not self._is_expanded
        self.toggle_btn.setText("▼" if self._is_expanded else "▶")
        self.content.setVisible(self._is_expanded)

    def _update_visibility(self):
        """Update visibility based on active jobs."""
        has_jobs = len(self._job_cards) > 0
        self.no_jobs_label.setVisible(not has_jobs)
        self.scroll_area.setVisible(has_jobs)

        if has_jobs:
            self.active_count.setText(str(len(self._job_cards)))
            self.active_count.show()
            self.status_icon.setText("⚙️")  # Could animate this
        else:
            self.active_count.hide()
            self.status_icon.setText("✓")

    def _add_job_card(self, job_id: int, job_type: str, total: int = 0):
        """Add a new job card."""
        if job_id in self._job_cards:
            return

        card = JobCard(job_id, job_type, total)
        card.pause_clicked.connect(self._on_card_pause)
        card.resume_clicked.connect(self._on_card_resume)
        card.cancel_clicked.connect(self._on_card_cancel)

        self._job_cards[job_id] = card

        # Insert before the stretch
        self.jobs_layout.insertWidget(self.jobs_layout.count() - 1, card)
        self._update_visibility()

    def _remove_job_card(self, job_id: int):
        """Remove a job card."""
        if job_id not in self._job_cards:
            return

        card = self._job_cards.pop(job_id)
        self.jobs_layout.removeWidget(card)
        card.deleteLater()
        self._update_visibility()

    # ─────────────────────────────────────────────────────────────────────────
    # Signal Handlers
    # ─────────────────────────────────────────────────────────────────────────

    @Slot(int, int, int, float, float, str)
    def _on_progress(self, job_id: int, processed: int, total: int, rate: float, eta: float, message: str):
        """Handle progress update."""
        if job_id in self._job_cards:
            self._job_cards[job_id].update_progress(processed, total, rate, eta, message)

    @Slot(int, str, int)
    def _on_job_started(self, job_id: int, job_type: str, total: int):
        """Handle job started."""
        self._add_job_card(job_id, job_type, total)
        self._add_activity(f"Started: {job_type}")

    @Slot(int, str, bool, str)
    def _on_job_completed(self, job_id: int, job_type: str, success: bool, stats_json: str):
        """Handle job completed."""
        self._remove_job_card(job_id)
        try:
            stats = json.loads(stats_json)
            count = stats.get('success_count', stats.get('total_count', 0))
            self._add_activity(f"Completed: {job_type} ({count} items)")
        except Exception:
            self._add_activity(f"Completed: {job_type}")

    @Slot(int, str, str)
    def _on_job_failed(self, job_id: int, job_type: str, error: str):
        """Handle job failed."""
        self._remove_job_card(job_id)
        self._add_activity(f"Failed: {job_type} - {error[:50]}")

    @Slot(int, str)
    def _on_job_canceled(self, job_id: int, job_type: str):
        """Handle job canceled."""
        self._remove_job_card(job_id)
        self._add_activity(f"Canceled: {job_type}")

    @Slot(int, str)
    def _on_job_paused(self, job_id: int, job_type: str):
        """Handle job paused."""
        if job_id in self._job_cards:
            self._job_cards[job_id].set_paused(True)

    @Slot(int, str)
    def _on_job_resumed(self, job_id: int, job_type: str):
        """Handle job resumed."""
        if job_id in self._job_cards:
            self._job_cards[job_id].set_paused(False)

    @Slot(int)
    def _on_active_jobs_changed(self, count: int):
        """Handle active job count change."""
        self._update_visibility()

    def _on_card_pause(self, job_id: int):
        """Handle pause from job card."""
        try:
            get_job_manager().pause(job_id)
        except Exception as e:
            logger.error(f"Failed to pause job {job_id}: {e}")

    def _on_card_resume(self, job_id: int):
        """Handle resume from job card."""
        try:
            get_job_manager().resume(job_id)
        except Exception as e:
            logger.error(f"Failed to resume job {job_id}: {e}")

    def _on_card_cancel(self, job_id: int):
        """Handle cancel from job card."""
        try:
            get_job_manager().cancel(job_id)
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")

    def _on_global_pause(self):
        """Handle global pause/resume."""
        try:
            manager = get_job_manager()
            stats = manager.get_job_stats()

            if stats.get('global_pause'):
                manager.resume_all()
                self.global_pause_btn.setText("⏸ Pause All")
            else:
                manager.pause_all()
                self.global_pause_btn.setText("▶ Resume All")
        except Exception as e:
            logger.error(f"Failed to toggle global pause: {e}")

    def _add_activity(self, message: str):
        """Add to activity history."""
        timestamp = datetime.now().strftime("%H:%M")
        self._activity_history.append(f"[{timestamp}] {message}")
        # Keep last 20 items
        if len(self._activity_history) > 20:
            self._activity_history = self._activity_history[-20:]


class MinimalActivityIndicator(QWidget):
    """
    Minimal activity indicator for status bar.

    Shows a small spinning icon when background jobs are running.
    Click to expand the full activity panel.
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_active = False
        self._job_count = 0

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self.icon = QLabel("⚙️")
        self.icon.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.icon)

        self.label = QLabel("")
        self.label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self.label)

        self.setCursor(Qt.PointingHandCursor)
        self.hide()

    def _connect_signals(self):
        try:
            manager = get_job_manager()
            manager.signals.active_jobs_changed.connect(self._on_jobs_changed)
            manager.signals.progress.connect(self._on_progress)
        except Exception:
            pass

    @Slot(int)
    def _on_jobs_changed(self, count: int):
        self._job_count = count
        if count > 0:
            self.label.setText(f"{count} task{'s' if count > 1 else ''}")
            self.show()
        else:
            self.hide()

    @Slot(int, int, int, float, float, str)
    def _on_progress(self, job_id: int, processed: int, total: int, rate: float, eta: float, message: str):
        if total > 0:
            pct = int(processed / total * 100)
            self.label.setText(f"{self._job_count} task{'s' if self._job_count > 1 else ''} ({pct}%)")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)
