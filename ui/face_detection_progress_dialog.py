# ui/face_detection_progress_dialog.py
# Enhanced Face Detection Progress Dialog
# Phase 2B: Face Detection Controller & UI
# Real-time progress reporting with quality metrics visualization

"""
Face Detection Progress Dialog

Enhanced progress dialog with:
- Real-time workflow state display
- Quality metrics visualization (Phase 2A integration)
- Pause/Resume/Cancel controls
- Estimated time remaining
- Detailed operation logging
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QGroupBox, QTextEdit, QFrame
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QColor
import logging
from typing import Optional

from services.face_detection_controller import FaceDetectionController, WorkflowState

logger = logging.getLogger(__name__)


class QualityMetricsWidget(QFrame):
    """Widget for displaying quality metrics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Title
        title = QLabel("<b>Quality Metrics</b>")
        title.setStyleSheet("color: #495057; font-size: 13px; border: none; background: transparent; padding: 0;")
        layout.addWidget(title)

        # Overall quality
        self.overall_label = QLabel("Overall Quality: --")
        self.overall_label.setStyleSheet("color: #212529; font-size: 12px; border: none; background: transparent; padding: 0;")
        layout.addWidget(self.overall_label)

        # Silhouette score
        self.silhouette_label = QLabel("Silhouette Score: --")
        self.silhouette_label.setStyleSheet("color: #212529; font-size: 12px; border: none; background: transparent; padding: 0;")
        layout.addWidget(self.silhouette_label)

        # Noise ratio
        self.noise_label = QLabel("Noise Ratio: --")
        self.noise_label.setStyleSheet("color: #212529; font-size: 12px; border: none; background: transparent; padding: 0;")
        layout.addWidget(self.noise_label)

        # Faces detected
        self.faces_label = QLabel("Faces Detected: 0")
        self.faces_label.setStyleSheet("color: #212529; font-size: 12px; border: none; background: transparent; padding: 0;")
        layout.addWidget(self.faces_label)

        # Clusters found
        self.clusters_label = QLabel("Clusters Found: 0")
        self.clusters_label.setStyleSheet("color: #212529; font-size: 12px; border: none; background: transparent; padding: 0;")
        layout.addWidget(self.clusters_label)

    def update_metrics(self, progress_dict: dict):
        """Update metrics from progress dictionary."""
        # Overall quality
        quality = progress_dict.get('quality_score', 0.0)
        quality_label = self._get_quality_label(quality)
        self.overall_label.setText(f"Overall Quality: {quality:.1f}/100 ({quality_label})")
        self.overall_label.setStyleSheet(
            f"color: {self._get_quality_color(quality)}; font-size: 12px; font-weight: bold; "
            f"border: none; background: transparent; padding: 0;"
        )

        # Silhouette score
        silhouette = progress_dict.get('silhouette_score', 0.0)
        if silhouette > 0:
            silhouette_label = self._get_silhouette_label(silhouette)
            self.silhouette_label.setText(f"Silhouette Score: {silhouette:.3f} ({silhouette_label})")
        else:
            self.silhouette_label.setText("Silhouette Score: --")

        # Noise ratio
        noise_ratio = progress_dict.get('noise_ratio', 0.0)
        if noise_ratio > 0:
            self.noise_label.setText(f"Noise Ratio: {noise_ratio:.1%}")
        else:
            self.noise_label.setText("Noise Ratio: --")

        # Faces detected
        faces = progress_dict.get('faces_detected', 0)
        self.faces_label.setText(f"Faces Detected: {faces}")

        # Clusters found
        clusters = progress_dict.get('clusters_found', 0)
        self.clusters_label.setText(f"Clusters Found: {clusters}")

    def _get_quality_label(self, quality: float) -> str:
        """Get quality label from score."""
        if quality >= 80:
            return "Excellent"
        elif quality >= 60:
            return "Good"
        elif quality >= 40:
            return "Fair"
        else:
            return "Poor" if quality > 0 else "--"

    def _get_quality_color(self, quality: float) -> str:
        """Get color for quality score."""
        if quality >= 80:
            return "#28a745"  # Green
        elif quality >= 60:
            return "#20c997"  # Teal
        elif quality >= 40:
            return "#ffc107"  # Yellow
        else:
            return "#dc3545" if quality > 0 else "#6c757d"  # Red or gray

    def _get_silhouette_label(self, score: float) -> str:
        """Get silhouette score label."""
        if score >= 0.7:
            return "Excellent"
        elif score >= 0.5:
            return "Good"
        elif score >= 0.25:
            return "Fair"
        else:
            return "Poor"


class FaceDetectionProgressDialog(QDialog):
    """
    Enhanced progress dialog for face detection workflow.

    Features:
    - Real-time workflow state display
    - Quality metrics visualization
    - Pause/Resume/Cancel controls
    - Progress bars with estimated time
    - Operation logging
    """

    def __init__(self, parent=None, controller: Optional[FaceDetectionController] = None):
        super().__init__(parent)
        self.controller = controller
        self._setup_ui()
        self._connect_controller()
        self.setWindowTitle("Face Detection Progress")
        self.setModal(True)
        self.resize(600, 500)

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        self.title_label = QLabel("<h2>Detecting and Grouping Faces</h2>")
        self.title_label.setStyleSheet("color: #212529;")
        header_layout.addWidget(self.title_label)

        self.state_label = QLabel("State: Initializing...")
        self.state_label.setStyleSheet("color: #6c757d; font-size: 12px;")
        header_layout.addWidget(self.state_label)

        layout.addLayout(header_layout)

        # Overall progress
        progress_group = QGroupBox("Overall Progress")
        progress_layout = QVBoxLayout(progress_group)

        self.overall_progress = QProgressBar()
        self.overall_progress.setTextVisible(True)
        self.overall_progress.setFormat("%p% - %v/%m steps")
        progress_layout.addWidget(self.overall_progress)

        self.operation_label = QLabel("Current Operation: Ready")
        self.operation_label.setStyleSheet("color: #495057; font-size: 11px;")
        progress_layout.addWidget(self.operation_label)

        self.time_label = QLabel("Elapsed: 0s | Remaining: --")
        self.time_label.setStyleSheet("color: #6c757d; font-size: 11px;")
        progress_layout.addWidget(self.time_label)

        layout.addWidget(progress_group)

        # Current step progress
        step_group = QGroupBox("Current Step")
        step_layout = QVBoxLayout(step_group)

        self.step_progress = QProgressBar()
        self.step_progress.setTextVisible(True)
        step_layout.addWidget(self.step_progress)

        self.step_label = QLabel("Processing...")
        self.step_label.setStyleSheet("color: #495057; font-size: 11px;")
        step_layout.addWidget(self.step_label)

        layout.addWidget(step_group)

        # Quality metrics
        self.metrics_widget = QualityMetricsWidget()
        layout.addWidget(self.metrics_widget)

        # Operation log
        log_group = QGroupBox("Operation Log")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)

        # Control buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        button_layout.addWidget(self.pause_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(self.cancel_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setEnabled(False)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

    def _connect_controller(self):
        """Connect to controller signals."""
        if not self.controller:
            return

        self.controller.signals.state_changed.connect(self._on_state_changed)
        self.controller.signals.progress_updated.connect(self._on_progress_updated)
        self.controller.signals.workflow_completed.connect(self._on_workflow_completed)
        self.controller.signals.workflow_failed.connect(self._on_workflow_failed)

    @Slot(str, str)
    def _on_state_changed(self, old_state: str, new_state: str):
        """Handle workflow state changes."""
        self.state_label.setText(f"State: {new_state.replace('_', ' ').title()}")
        self._log(f"State changed: {old_state} → {new_state}")

        # Update button states
        is_running = new_state in ['detecting', 'clustering']
        is_paused = new_state in ['detection_paused', 'clustering_paused']
        is_finished = new_state in ['completed', 'failed', 'cancelled']

        self.pause_btn.setEnabled(is_running)
        self.pause_btn.setText("Resume" if is_paused else "Pause")
        self.cancel_btn.setEnabled(is_running or is_paused)
        self.close_btn.setEnabled(is_finished)

    @Slot(dict)
    def _on_progress_updated(self, progress_dict: dict):
        """Handle progress updates."""
        # Overall progress
        total_steps = progress_dict.get('total_steps', 2)
        completed_steps = progress_dict.get('completed_steps', 0)
        self.overall_progress.setMaximum(total_steps)
        self.overall_progress.setValue(completed_steps)

        # Current operation
        operation = progress_dict.get('current_operation', '')
        self.operation_label.setText(f"Current Operation: {operation}")

        # Time estimates
        elapsed = progress_dict.get('elapsed_time', 0.0)
        remaining = progress_dict.get('estimated_remaining', 0.0)
        self.time_label.setText(
            f"Elapsed: {self._format_time(elapsed)} | "
            f"Remaining: {self._format_time(remaining) if remaining > 0 else '--'}"
        )

        # Step progress
        photos_processed = progress_dict.get('photos_processed', 0)
        photos_total = progress_dict.get('photos_total', 0)
        if photos_total > 0:
            self.step_progress.setMaximum(photos_total)
            self.step_progress.setValue(photos_processed)
            self.step_label.setText(f"{photos_processed}/{photos_total} photos processed")
        else:
            self.step_progress.setMaximum(100)
            self.step_progress.setValue(0)
            self.step_label.setText("Preparing...")

        # Quality metrics
        self.metrics_widget.update_metrics(progress_dict)

        # Log progress
        current_step = progress_dict.get('current_step', '')
        if current_step:
            self._log(f"{current_step}: {photos_processed}/{photos_total}")

    @Slot(dict)
    def _on_workflow_completed(self, results: dict):
        """Handle workflow completion."""
        self._log(
            f"✅ Workflow completed successfully!\n"
            f"   Photos: {results.get('photos_processed', 0)}\n"
            f"   Faces: {results.get('faces_detected', 0)}\n"
            f"   Clusters: {results.get('clusters_found', 0)}\n"
            f"   Quality: {results.get('quality_score', 0.0):.1f}/100"
        )

        self.title_label.setText("<h2>Face Detection Completed ✅</h2>")
        self.title_label.setStyleSheet("color: #28a745;")

    @Slot(str)
    def _on_workflow_failed(self, error_message: str):
        """Handle workflow failure."""
        self._log(f"❌ Workflow failed: {error_message}")
        self.title_label.setText("<h2>Face Detection Failed ❌</h2>")
        self.title_label.setStyleSheet("color: #dc3545;")

    def _on_pause_clicked(self):
        """Handle pause button click."""
        if not self.controller:
            return

        if self.controller.is_paused:
            # Resume
            if self.controller.resume_workflow():
                self._log("Resuming workflow...")
        else:
            # Pause
            if self.controller.pause_workflow():
                self._log("Pausing workflow...")

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        if not self.controller:
            return

        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Cancel Workflow",
            "Are you sure you want to cancel the face detection workflow?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.controller.cancel_workflow()
            self._log("Workflow cancelled by user")

    def _log(self, message: str):
        """Append message to operation log."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def _format_time(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


def show_face_detection_progress(parent=None, controller: Optional[FaceDetectionController] = None):
    """
    Show face detection progress dialog.

    Args:
        parent: Parent widget
        controller: FaceDetectionController instance

    Returns:
        Dialog result (QDialog.Accepted or QDialog.Rejected)
    """
    dialog = FaceDetectionProgressDialog(parent, controller)
    return dialog.exec()
