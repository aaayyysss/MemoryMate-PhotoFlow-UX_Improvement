# similar_photo_dialog.py
# Version 02.01.00.00 dated 20260130
"""
Similar Photo Detection Dialog
Specialized dialog for finding visually similar photos using AI embeddings.

Follows best practices from:
- Google Photos: Advanced visual similarity with adjustable thresholds
- Lightroom: Professional clustering and grouping controls
- iPhone Photos: Intuitive similarity adjustment

Version 2.0:
- Fixed header + scrollable content layout (consistent with DuplicateDetectionDialog)
- Persists results to database using StackGenerationService
- Project isolation support
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QSpinBox, QDoubleSpinBox, QSlider,
    QFrame, QProgressBar, QMessageBox, QComboBox,
    QListWidget, QListWidgetItem, QWidget, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer
from PySide6.QtGui import QFont
from typing import Optional, List, Dict
import numpy as np
import time

from services.library_detector import check_system_readiness
from repository.photo_repository import PhotoRepository
from repository.base_repository import DatabaseConnection
from repository.stack_repository import StackRepository
from services.embedding_service import EmbeddingService
from services.stack_generation_service import StackGenerationService, StackGenParams
from logging_config import get_logger

logger = get_logger(__name__)


class SimilarPhotoWorker(QObject):
    """Background worker for similar photo detection with database persistence."""

    progress_updated = Signal(int, str)  # percentage, message
    preview_updated = Signal(list)  # list of photo groups for preview
    finished = Signal(dict)  # results
    error = Signal(str)

    def __init__(self, project_id: int, options: dict):
        super().__init__()
        self.project_id = project_id
        self.options = options
        self._running = True

    def run(self):
        """Run similar photo detection with database persistence."""
        try:
            start_time = time.time()

            results = {
                'groups_found': 0,
                'photos_grouped': 0,
                'processing_time': 0,
                'stacks_created': 0,
                'memberships_created': 0
            }

            self.progress_updated.emit(5, "Initializing services...")

            # Initialize services
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)
            stack_repo = StackRepository(db_conn)

            # Use EmbeddingService which reads from photo_embedding table
            # (same table where DuplicateDetectionWorker stores embeddings)
            embedding_service = EmbeddingService(db_connection=db_conn)

            # Load the CLIP model to get the model_id
            self.progress_updated.emit(8, "Loading CLIP model...")
            embedding_service.load_clip_model()

            # Create stack generation service
            stack_gen_service = StackGenerationService(
                photo_repo=photo_repo,
                stack_repo=stack_repo,
                similarity_service=embedding_service
            )

            self.progress_updated.emit(10, "Checking embeddings...")

            # Get all embeddings for the project from photo_embedding table
            embeddings_dict = embedding_service.get_all_embeddings_for_project(self.project_id)

            if len(embeddings_dict) < 2:
                self.error.emit(
                    "Not enough photos with embeddings.\n\n"
                    "Please generate embeddings first using:\n"
                    "Tools → Duplicate Detection → Detect Duplicates\n"
                    "(Enable 'Generate AI Embeddings' option)"
                )
                return

            self.progress_updated.emit(20, f"Found {len(embeddings_dict)} photos with embeddings...")

            if not self._running:
                return

            # Get parameters from options
            similarity_threshold = self.options.get('similarity_threshold', 0.85)
            min_group_size = self.options.get('min_group_size', 3)
            time_window = self.options.get('time_window_seconds', 60)

            self.progress_updated.emit(30, "Generating similar shot stacks...")

            # Use StackGenerationService to persist results
            params = StackGenParams(
                similarity_threshold=similarity_threshold,
                min_stack_size=min_group_size,
                time_window_seconds=time_window
            )

            # Regenerate similar shot stacks
            stats = stack_gen_service.regenerate_similar_shot_stacks(
                project_id=self.project_id,
                params=params
            )

            if not self._running:
                return

            self.progress_updated.emit(80, "Retrieving results...")

            # Get created stacks for preview
            stacks = stack_repo.list_stacks(self.project_id, stack_type="similar", limit=50)
            preview_groups = []

            for stack in stacks:
                members = stack_repo.list_stack_members(self.project_id, stack['stack_id'])
                if members:
                    photo_ids = [m['photo_id'] for m in members]
                    preview_groups.append(photo_ids)

            # Update results
            results['stacks_created'] = stats.stacks_created
            results['memberships_created'] = stats.memberships_created
            results['groups_found'] = stats.stacks_created
            results['photos_grouped'] = stats.memberships_created
            results['processing_time'] = round(time.time() - start_time, 1)
            results['errors'] = stats.errors

            # Emit preview of first 10 groups
            self.preview_updated.emit(preview_groups[:10])

            self.progress_updated.emit(100, "Detection complete!")
            self.finished.emit(results)

        except Exception as e:
            logger.error(f"Similar photo detection failed: {e}", exc_info=True)
            self.error.emit(str(e))


class SimilarPhotoDetectionDialog(QDialog):
    """
    Professional similar photo detection dialog.

    Features:
    - Visual similarity clustering using AI embeddings
    - Adjustable sensitivity controls
    - Real-time preview of groups
    - Multiple clustering algorithms
    - Persists results to database for sidebar display

    Layout: Fixed header with buttons + scrollable content area.
    """

    def __init__(self, project_id: int, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.worker_thread = None
        self.worker = None
        self.preview_groups = []

        self.setWindowTitle("📸 Find Similar Photos")
        self.setModal(True)
        self.resize(700, 600)
        self.setMinimumSize(600, 450)

        self._build_ui()
        self._connect_signals()
        self._check_system_readiness()
        self._load_existing_stats()

    def _build_ui(self):
        """Build dialog UI with fixed header and scrollable content."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # === FIXED HEADER (Title + Buttons) ===
        header_widget = QWidget()
        header_widget.setStyleSheet("background-color: white; border-bottom: 1px solid #e0e0e0;")
        header_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 12, 20, 12)
        header_layout.setSpacing(16)

        # Title section
        title_section = QVBoxLayout()
        title_section.setSpacing(2)

        title_label = QLabel("Find Similar Photos")
        title_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #333;")
        title_section.addWidget(title_label)

        subtitle_label = QLabel("Discover visually similar photos using AI-powered analysis")
        subtitle_label.setStyleSheet("color: #666; font-size: 9pt;")
        title_section.addWidget(subtitle_label)

        header_layout.addLayout(title_section)
        header_layout.addStretch()

        # Action buttons in header
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(self._secondary_button_style())
        self.btn_cancel.setFixedWidth(80)
        self.btn_cancel.clicked.connect(self.reject)
        header_layout.addWidget(self.btn_cancel)

        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setStyleSheet(self._secondary_button_style())
        self.btn_preview.setFixedWidth(80)
        self.btn_preview.clicked.connect(self._preview_groups)
        header_layout.addWidget(self.btn_preview)

        self.btn_start = QPushButton("▶ Find Similar")
        self.btn_start.setStyleSheet(self._primary_button_style())
        self.btn_start.setFixedWidth(120)
        self.btn_start.clicked.connect(self._start_detection)
        header_layout.addWidget(self.btn_start)

        main_layout.addWidget(header_widget)

        # === SCROLLABLE CONTENT AREA ===
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background-color: #fafafa; }")
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #fafafa;")
        scroll_content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(16, 12, 16, 12)

        # === COLLECTION STATISTICS ===
        stats_group = QGroupBox("📊 Collection Statistics")
        stats_group.setStyleSheet(self._groupbox_style())
        stats_layout = QVBoxLayout(stats_group)
        stats_layout.setContentsMargins(12, 16, 12, 12)

        self.stats_label = QLabel("Loading statistics...")
        self.stats_label.setStyleSheet("font-size: 9pt;")
        stats_layout.addWidget(self.stats_label)

        content_layout.addWidget(stats_group)

        # === CLUSTERING ALGORITHM ===
        algo_group = QGroupBox("🔧 Clustering Algorithm")
        algo_group.setStyleSheet(self._groupbox_style())
        algo_layout = QHBoxLayout(algo_group)
        algo_layout.setContentsMargins(12, 16, 12, 12)

        algo_layout.addWidget(QLabel("Method:"))

        self.combo_algorithm = QComboBox()
        self.combo_algorithm.addItem("Hierarchical Clustering", "hierarchical")
        self.combo_algorithm.addItem("Complete Linkage", "complete")
        self.combo_algorithm.setCurrentIndex(0)
        self.combo_algorithm.setToolTip(
            "Hierarchical: Best for general use, groups by visual similarity\n"
            "Complete Linkage: Stricter - requires all members be similar"
        )
        algo_layout.addWidget(self.combo_algorithm)
        algo_layout.addStretch()

        content_layout.addWidget(algo_group)

        # === SENSITIVITY CONTROLS ===
        sensitivity_group = QGroupBox("🎚️ Sensitivity Controls")
        sensitivity_group.setStyleSheet(self._groupbox_style())
        sensitivity_layout = QVBoxLayout(sensitivity_group)
        sensitivity_layout.setSpacing(8)
        sensitivity_layout.setContentsMargins(12, 16, 12, 12)

        # Similarity threshold with slider
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(QLabel("Similarity Threshold:"))

        self.slider_threshold = QSlider(Qt.Horizontal)
        self.slider_threshold.setRange(50, 99)
        self.slider_threshold.setValue(85)
        self.slider_threshold.setToolTip(
            "Higher = stricter matching (fewer groups)\n"
            "Lower = more aggressive grouping (more groups)"
        )
        self.slider_threshold.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #ddd;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 16px;
                height: 16px;
                background: #1a73e8;
                border-radius: 8px;
                margin: -5px 0;
            }
        """)
        threshold_layout.addWidget(self.slider_threshold)

        self.label_threshold = QLabel("0.85")
        self.label_threshold.setFixedWidth(40)
        self.label_threshold.setStyleSheet("font-weight: bold;")
        threshold_layout.addWidget(self.label_threshold)

        sensitivity_layout.addLayout(threshold_layout)

        # Min group size
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("Minimum Group Size:"))

        self.spin_min_size = QSpinBox()
        self.spin_min_size.setRange(2, 20)
        self.spin_min_size.setValue(3)
        self.spin_min_size.setToolTip("Minimum photos required to form a group")
        self.spin_min_size.setFixedWidth(60)
        size_layout.addWidget(self.spin_min_size)
        size_layout.addStretch()

        sensitivity_layout.addLayout(size_layout)

        # Time window
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Time Proximity Window:"))

        self.spin_time_window = QSpinBox()
        self.spin_time_window.setRange(0, 600)
        self.spin_time_window.setValue(60)
        self.spin_time_window.setSuffix(" seconds")
        self.spin_time_window.setToolTip(
            "0 = Ignore timing\n"
            ">0 = Only group photos taken within this time window"
        )
        self.spin_time_window.setFixedWidth(100)
        time_layout.addWidget(self.spin_time_window)
        time_layout.addStretch()

        sensitivity_layout.addLayout(time_layout)

        content_layout.addWidget(sensitivity_group)

        # === PREVIEW GROUPS ===
        preview_group = QGroupBox("👁️ Preview Groups")
        preview_group.setStyleSheet(self._groupbox_style())
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(12, 16, 12, 12)

        self.preview_list = QListWidget()
        self.preview_list.setMaximumHeight(100)
        self.preview_list.setStyleSheet("font-size: 9pt;")
        self.preview_list.addItem("Click 'Preview' or run detection to see groups...")
        preview_layout.addWidget(self.preview_list)

        content_layout.addWidget(preview_group)

        # === REQUIREMENTS CHECK ===
        requirements_group = QGroupBox("✅ Requirements Check")
        requirements_group.setStyleSheet(self._groupbox_style())
        requirements_layout = QVBoxLayout(requirements_group)
        requirements_layout.setContentsMargins(12, 16, 12, 12)

        self.status_label = QLabel("Checking system...")
        self.status_label.setStyleSheet("font-weight: bold;")
        requirements_layout.addWidget(self.status_label)

        self.requirements_detail = QLabel("")
        self.requirements_detail.setWordWrap(True)
        self.requirements_detail.setStyleSheet("color: #666; font-size: 9pt;")
        requirements_layout.addWidget(self.requirements_detail)

        content_layout.addWidget(requirements_group)

        content_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area, 1)

        # === PROGRESS BAR (hidden initially) ===
        self.progress_widget = QWidget()
        self.progress_widget.setStyleSheet("background-color: white; border-top: 1px solid #e0e0e0;")
        self.progress_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.progress_widget.setVisible(False)
        progress_layout = QVBoxLayout(self.progress_widget)
        progress_layout.setContentsMargins(16, 10, 16, 10)
        progress_layout.setSpacing(4)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ddd;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #1a73e8;
                border-radius: 3px;
            }
        """)
        progress_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("Ready...")
        self.progress_label.setStyleSheet("color: #666; font-size: 9pt;")
        progress_layout.addWidget(self.progress_label)

        main_layout.addWidget(self.progress_widget)

    def _groupbox_style(self) -> str:
        """Return compact GroupBox styling."""
        return """
            QGroupBox {
                font-weight: bold;
                font-size: 9pt;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 2px 6px;
                background-color: #fafafa;
            }
        """

    def _primary_button_style(self) -> str:
        """Return primary button styling (blue)."""
        return """
            QPushButton {
                background-color: #1a73e8;
                color: white;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 9pt;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1557b0;
            }
            QPushButton:pressed {
                background-color: #0d47a1;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #888;
            }
        """

    def _secondary_button_style(self) -> str:
        """Return secondary button styling (outlined)."""
        return """
            QPushButton {
                background-color: white;
                color: #333;
                padding: 8px 12px;
                font-size: 9pt;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #f5f5f5;
                border-color: #ccc;
            }
            QPushButton:pressed {
                background-color: #e8e8e8;
            }
        """

    def _connect_signals(self):
        """Connect signals."""
        self.slider_threshold.valueChanged.connect(self._on_threshold_changed)

    def _check_system_readiness(self):
        """Check system readiness and update UI."""
        ready, summary, recommendations = check_system_readiness()

        self.status_label.setText(summary)

        if ready:
            self.status_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
            self.requirements_detail.setText("✓ System ready for similarity detection!")
            self.requirements_detail.setStyleSheet("color: #2e7d32;")
            self.btn_start.setEnabled(True)
        else:
            self.status_label.setStyleSheet("color: #c62828; font-weight: bold;")
            rec_text = " | ".join(recommendations[:2]) if recommendations else "System check failed"
            self.requirements_detail.setText(f"⚠ {rec_text}")
            self.requirements_detail.setStyleSheet("color: #c62828;")
            self.btn_start.setEnabled(False)

    def _load_existing_stats(self):
        """Load statistics about existing embeddings."""
        try:
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)

            total_photos = photo_repo.count(
                where_clause="project_id = ?",
                params=(self.project_id,)
            )

            # Query photo_embedding table directly for accurate count
            # (embeddings are stored here by DuplicateDetectionWorker)
            with db_conn.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT COUNT(DISTINCT pe.photo_id) as cnt
                    FROM photo_embedding pe
                    JOIN photo_metadata p ON pe.photo_id = p.id
                    WHERE p.project_id = ? AND pe.embedding_type = 'visual_semantic'
                """, (self.project_id,))
                row = cursor.fetchone()
                photos_with_embeddings = row['cnt'] if row else 0

            coverage = int((photos_with_embeddings / max(total_photos, 1)) * 100)

            self.stats_label.setText(
                f"Total Photos: {total_photos} | "
                f"With Embeddings: {photos_with_embeddings} | "
                f"Coverage: {coverage}%"
            )

        except Exception as e:
            logger.error(f"Failed to load stats: {e}")
            self.stats_label.setText(f"Error loading statistics")

    def _on_threshold_changed(self, value: int):
        """Handle threshold slider change."""
        threshold = value / 100.0
        self.label_threshold.setText(f"{threshold:.2f}")

    def _preview_groups(self):
        """Show preview of existing similar groups."""
        try:
            db_conn = DatabaseConnection()
            stack_repo = StackRepository(db_conn)

            stacks = stack_repo.list_stacks(self.project_id, stack_type="similar", limit=10)

            self.preview_list.clear()

            if not stacks:
                self.preview_list.addItem("No existing similar groups found")
                self.preview_list.addItem("Run 'Find Similar' to detect groups")
                return

            for i, stack in enumerate(stacks):
                members = stack_repo.list_stack_members(self.project_id, stack['stack_id'])
                count = len(members) if members else 0
                item = QListWidgetItem(f"Group {i+1}: {count} photos")
                self.preview_list.addItem(item)

            if len(stacks) == 10:
                self.preview_list.addItem("... (showing first 10 groups)")

        except Exception as e:
            logger.error(f"Preview failed: {e}")
            self.preview_list.clear()
            self.preview_list.addItem(f"Error: {str(e)}")

    def _start_detection(self):
        """Start similar photo detection process."""
        # Prepare options
        options = {
            'algorithm': self.combo_algorithm.currentData(),
            'similarity_threshold': self.slider_threshold.value() / 100.0,
            'min_group_size': self.spin_min_size.value(),
            'time_window_seconds': self.spin_time_window.value()
        }

        logger.info(f"Starting similar photo detection: {options}")

        # Show progress mode
        self._show_progress_mode()

        # Start worker
        self.worker = SimilarPhotoWorker(self.project_id, options)
        self.worker_thread = QThread()

        self.worker.moveToThread(self.worker_thread)
        self.worker.progress_updated.connect(self._update_progress)
        self.worker.preview_updated.connect(self._update_preview)
        self.worker.finished.connect(self._on_detection_finished)
        self.worker.error.connect(self._on_detection_error)
        self.worker_thread.started.connect(self.worker.run)

        self.worker_thread.start()

    def _show_progress_mode(self):
        """Switch to progress display mode."""
        self.combo_algorithm.setEnabled(False)
        self.slider_threshold.setEnabled(False)
        self.spin_min_size.setEnabled(False)
        self.spin_time_window.setEnabled(False)
        self.btn_preview.setEnabled(False)

        self.progress_widget.setVisible(True)
        self.btn_start.setText("Processing...")
        self.btn_start.setEnabled(False)
        self.btn_cancel.setText("Stop")

    def _update_progress(self, percentage: int, message: str):
        """Update progress display."""
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(message)

    def _update_preview(self, groups: list):
        """Update preview with detected groups."""
        self.preview_groups = groups
        self.preview_list.clear()

        if not groups:
            self.preview_list.addItem("No groups found")
            return

        for i, group in enumerate(groups):
            item = QListWidgetItem(f"Group {i+1}: {len(group)} photos")
            self.preview_list.addItem(item)

    def _on_detection_finished(self, results: dict):
        """Handle detection completion."""
        if self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait()

        # Show results
        message = (
            f"<b>Similar Photo Detection Complete!</b><br><br>"
            f"<b>Results:</b><br>"
            f"• Similar groups found: {results.get('stacks_created', 0):,}<br>"
            f"• Photos grouped: {results.get('memberships_created', 0):,}<br>"
            f"• Processing time: {results.get('processing_time', 0)} seconds<br>"
            f"• Errors: {results.get('errors', 0)}<br><br>"
            f"<i>View groups in the sidebar under 'Duplicates → Similar'.</i>"
        )

        QMessageBox.information(self, "Detection Complete", message)
        self.accept()

    def _on_detection_error(self, error_message: str):
        """Handle detection error."""
        if self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait()

        QMessageBox.critical(
            self,
            "Detection Failed",
            f"Similar photo detection failed:\n\n{error_message}"
        )

        self._show_configuration_mode()

    def _show_configuration_mode(self):
        """Switch back to configuration mode."""
        self.combo_algorithm.setEnabled(True)
        self.slider_threshold.setEnabled(True)
        self.spin_min_size.setEnabled(True)
        self.spin_time_window.setEnabled(True)
        self.btn_preview.setEnabled(True)

        self.progress_widget.setVisible(False)
        self.btn_start.setText("▶ Find Similar")
        self.btn_start.setEnabled(True)
        self.btn_cancel.setText("Cancel")

    def reject(self):
        """Handle dialog rejection."""
        if self.worker_thread and self.worker_thread.isRunning():
            if self.worker:
                self.worker._running = False
            self.worker_thread.quit()
            self.worker_thread.wait()

        super().reject()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    dialog = SimilarPhotoDetectionDialog(project_id=1)
    dialog.exec()
