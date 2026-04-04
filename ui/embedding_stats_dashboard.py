#!/usr/bin/env python3
"""
Embedding Statistics Dashboard - Analytics for semantic embeddings.

Provides insights into embedding coverage, storage usage, staleness,
and GPU/performance information.

Features:
- Coverage statistics (X/Y photos have embeddings)
- Storage breakdown (float16 vs float32, space savings)
- Staleness detection (stale embeddings needing refresh)
- GPU information and optimal batch size
- Job progress for interrupted jobs
- FAISS availability status

Author: Claude Code
Date: January 2026
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QProgressBar, QMessageBox, QGridLayout,
    QFrame, QSpacerItem, QSizePolicy
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, Signal

from services.semantic_embedding_service import get_semantic_embedding_service

logger = logging.getLogger(__name__)


class StatCard(QFrame):
    """A styled card widget for displaying a single statistic."""

    def __init__(self, title: str, value: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setStyleSheet("""
            StatCard {
                background-color: #2d2d2d;
                border-radius: 8px;
                padding: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(title_label)

        # Value
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("color: #fff; font-size: 24px; font-weight: bold;")
        layout.addWidget(self.value_label)

        # Subtitle
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setStyleSheet("color: #666; font-size: 10px;")
            layout.addWidget(subtitle_label)

    def set_value(self, value: str):
        self.value_label.setText(value)


class EmbeddingStatsDashboard(QDialog):
    """
    Dashboard showing semantic embedding statistics and metrics.

    Provides transparency into embedding coverage, storage, and performance.
    """

    # Signals
    refreshRequested = Signal()
    migrateRequested = Signal()
    invalidateStaleRequested = Signal()

    def __init__(self, project_id: int, parent=None):
        """
        Initialize embedding statistics dashboard.

        Args:
            project_id: Current project ID
            parent: Parent widget
        """
        super().__init__(parent)

        self.project_id = project_id
        self.stats = {}

        self.setWindowTitle("Embedding Statistics Dashboard")
        self.setModal(False)
        self.resize(700, 600)

        self._load_statistics()
        self._create_ui()

    def _load_statistics(self):
        """Load embedding statistics from service."""
        try:
            service = get_semantic_embedding_service()
            self.stats = service.get_project_embedding_stats(self.project_id)
        except Exception as e:
            logger.error(f"Failed to load embedding stats: {e}")
            self.stats = {}

    def _create_ui(self):
        """Create the dashboard UI."""
        # Reuse existing layout if present (for refresh), otherwise create new
        if self.layout():
            layout = self.layout()
        else:
            layout = QVBoxLayout(self)
            layout.setSpacing(16)
            layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Semantic Embedding Statistics")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        layout.addWidget(title)

        # Upgrade Section (Proactive Quality Policy)
        upgrade_group = self._create_upgrade_section()
        if upgrade_group:
            layout.addWidget(upgrade_group)

        # Coverage Section
        coverage_group = self._create_coverage_section()
        layout.addWidget(coverage_group)

        # Storage Section
        storage_group = self._create_storage_section()
        layout.addWidget(storage_group)

        # Performance Section
        performance_group = self._create_performance_section()
        layout.addWidget(performance_group)

        # Status Section
        status_group = self._create_status_section()
        layout.addWidget(status_group)

        # Actions
        actions_layout = QHBoxLayout()
        actions_layout.addStretch()

        refresh_btn = QPushButton("Refresh Stats")
        refresh_btn.clicked.connect(self._on_refresh)
        actions_layout.addWidget(refresh_btn)

        migrate_btn = QPushButton("Migrate to Float16")
        migrate_btn.setToolTip("Convert legacy float32 embeddings to float16 (50% space savings)")
        migrate_btn.clicked.connect(self._on_migrate)
        migrate_btn.setEnabled(self.stats.get('float32_count', 0) > 0)
        actions_layout.addWidget(migrate_btn)

        invalidate_btn = QPushButton("Invalidate Stale")
        invalidate_btn.setToolTip("Delete stale embeddings so they will be regenerated")
        invalidate_btn.clicked.connect(self._on_invalidate_stale)
        invalidate_btn.setEnabled(self.stats.get('stale_embeddings', 0) > 0)
        actions_layout.addWidget(invalidate_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions_layout.addWidget(close_btn)

        layout.addLayout(actions_layout)

    def _create_upgrade_section(self) -> Optional[QGroupBox]:
        """Create the model upgrade section if an upgrade is available."""
        if not self.stats.get('can_upgrade_model', False):
            return None

        group = QGroupBox("Model Upgrade Assistant")
        group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #1a73e8;
                border-radius: 8px;
                margin-top: 1ex;
                font-weight: bold;
                color: #1a73e8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 3px;
            }
        """)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(15, 20, 15, 15)

        from utils.clip_model_registry import model_display_label
        current = model_display_label(self.stats.get('current_project_model', 'unknown'))
        recommended = model_display_label(self.stats.get('recommended_model', 'unknown'))

        info_lbl = QLabel(
            f"🚀 <b>A better CLIP model is available!</b><br><br>"
            f"Your project is currently using: <i>{current}</i><br>"
            f"Recommended for best quality: <b>{recommended}</b><br><br>"
            f"Upgrading to the larger model significantly improves search quality for scenic, "
            f"lifestyle, and complex semantic queries."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #fff; line-height: 1.4;")
        layout.addWidget(info_lbl)

        upgrade_btn = QPushButton(f"✨ Upgrade Project to {recommended}")
        upgrade_btn.setMinimumHeight(40)
        upgrade_btn.setCursor(Qt.PointingHandCursor)
        upgrade_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a73e8;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 6px;
                padding: 8px;
            }
            QPushButton:hover { background-color: #1557b0; }
        """)
        upgrade_btn.clicked.connect(self._on_upgrade_model)
        layout.addWidget(upgrade_btn)

        return group

    def _create_coverage_section(self) -> QGroupBox:
        """Create the coverage statistics section."""
        group = QGroupBox("Coverage")
        layout = QGridLayout(group)
        layout.setSpacing(12)

        total = self.stats.get('total_photos', 0)
        with_emb = self.stats.get('photos_with_embeddings', 0)
        without_emb = self.stats.get('photos_without_embeddings', 0)
        coverage = self.stats.get('coverage_percent', 0)

        # Coverage progress bar
        progress_layout = QVBoxLayout()
        progress_label = QLabel(f"Embedding Coverage: {coverage:.1f}%")
        progress_layout.addWidget(progress_label)

        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(int(coverage))
        progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
        """)
        progress_layout.addWidget(progress_bar)
        layout.addLayout(progress_layout, 0, 0, 1, 3)

        # Stats cards
        layout.addWidget(StatCard("Total Photos", str(total)), 1, 0)
        layout.addWidget(StatCard("With Embeddings", str(with_emb), "Ready for similarity search"), 1, 1)
        layout.addWidget(StatCard("Without Embeddings", str(without_emb), "Need processing"), 1, 2)

        return group

    def _create_storage_section(self) -> QGroupBox:
        """Create the storage statistics section."""
        group = QGroupBox("Storage")
        layout = QGridLayout(group)
        layout.setSpacing(12)

        storage_mb = self.stats.get('storage_mb', 0)
        float16 = self.stats.get('float16_count', 0)
        float32 = self.stats.get('float32_count', 0)
        saved = self.stats.get('space_saved_percent', 0)
        dim = self.stats.get('embedding_dimension', 512)

        layout.addWidget(StatCard("Storage Used", f"{storage_mb:.2f} MB"), 0, 0)
        layout.addWidget(StatCard("Float16 (New)", str(float16), "50% smaller"), 0, 1)
        layout.addWidget(StatCard("Float32 (Legacy)", str(float32), "Can be migrated"), 0, 2)

        # Space savings
        if saved > 0:
            savings_label = QLabel(f"Space Saved: {saved:.1f}% by using half-precision storage")
            savings_label.setStyleSheet("color: #4CAF50; font-style: italic;")
            layout.addWidget(savings_label, 1, 0, 1, 3)

        # Dimension info
        dim_label = QLabel(f"Embedding Dimension: {dim}-D vectors ({self.stats.get('model_name', 'unknown')})")
        dim_label.setStyleSheet("color: #888;")
        layout.addWidget(dim_label, 2, 0, 1, 3)

        return group

    def _create_performance_section(self) -> QGroupBox:
        """Create the performance/GPU section."""
        group = QGroupBox("Performance")
        layout = QGridLayout(group)
        layout.setSpacing(12)

        gpu_device = self.stats.get('gpu_device', 'unknown')
        gpu_memory = self.stats.get('gpu_memory_mb', 0)
        faiss = self.stats.get('faiss_available', False)

        # GPU info
        gpu_text = gpu_device.upper()
        if gpu_memory > 0:
            gpu_text += f" ({gpu_memory:.0f} MB)"

        layout.addWidget(StatCard("GPU Device", gpu_text), 0, 0)
        layout.addWidget(StatCard("FAISS", "Available" if faiss else "Not Installed",
                                   "Fast similarity search" if faiss else "Using numpy fallback"), 0, 1)

        # Optimal batch size
        try:
            service = get_semantic_embedding_service()
            batch_size = service.get_optimal_batch_size()
            layout.addWidget(StatCard("Optimal Batch Size", str(batch_size), "Auto-tuned for GPU"), 0, 2)
        except Exception:
            layout.addWidget(StatCard("Optimal Batch Size", "N/A"), 0, 2)

        return group

    def _create_status_section(self) -> QGroupBox:
        """Create the status/staleness section."""
        group = QGroupBox("Data Quality")
        layout = QGridLayout(group)
        layout.setSpacing(12)

        fresh = self.stats.get('fresh_embeddings', 0)
        stale = self.stats.get('stale_embeddings', 0)
        missing_mtime = self.stats.get('missing_mtime', 0)
        has_job = self.stats.get('has_incomplete_job', False)
        job_progress = self.stats.get('job_progress_percent', 0)

        layout.addWidget(StatCard("Fresh", str(fresh), "Up to date"), 0, 0)
        layout.addWidget(StatCard("Stale", str(stale), "Source file changed"), 0, 1)
        layout.addWidget(StatCard("Legacy", str(missing_mtime), "No mtime tracking"), 0, 2)

        # Job status
        if has_job:
            job_layout = QVBoxLayout()
            job_label = QLabel(f"Incomplete Job: {job_progress:.1f}% complete")
            job_label.setStyleSheet("color: #FFC107;")
            job_layout.addWidget(job_label)

            job_progress_bar = QProgressBar()
            job_progress_bar.setRange(0, 100)
            job_progress_bar.setValue(int(job_progress))
            job_layout.addWidget(job_progress_bar)

            layout.addLayout(job_layout, 1, 0, 1, 3)

        return group

    def _on_refresh(self):
        """Refresh statistics."""
        self._load_statistics()
        # Rebuild UI
        # Clear existing widgets
        while self.layout().count():
            item = self.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        self._create_ui()
        self.refreshRequested.emit()

    def _clear_layout(self, layout):
        """Recursively clear a layout."""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _on_migrate(self):
        """Migrate float32 embeddings to float16."""
        reply = QMessageBox.question(
            self,
            "Migrate to Float16",
            f"This will convert {self.stats.get('float32_count', 0)} legacy embeddings "
            "to half-precision format, saving ~50% storage space.\n\n"
            "This is safe and reversible (embeddings can be regenerated).\n\n"
            "Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            try:
                service = get_semantic_embedding_service()
                migrated = 0
                # Migrate in batches
                while True:
                    batch_migrated = service.migrate_to_half_precision(batch_size=500)
                    if batch_migrated == 0:
                        break
                    migrated += batch_migrated

                QMessageBox.information(
                    self,
                    "Migration Complete",
                    f"Successfully migrated {migrated} embeddings to float16 format."
                )

                self._on_refresh()
                self.migrateRequested.emit()

            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Migration Failed",
                    f"Error during migration: {e}"
                )

    def _on_upgrade_model(self):
        """Execute the model upgrade path."""
        from utils.clip_model_registry import model_display_label
        recommended = self.stats.get('recommended_model')

        reply = QMessageBox.question(
            self,
            "Upgrade CLIP Model",
            f"This will upgrade your project to use the <b>{model_display_label(recommended)}</b> model.<br><br>"
            "<b>What happens next:</b><br>"
            "1. The project's default model will be changed.<br>"
            "2. Existing embeddings will be marked as legacy (kept for safety).<br>"
            "3. You will be prompted to re-extract embeddings for the entire project.<br><br>"
            "Proceed with upgrade?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            try:
                from repository.project_repository import ProjectRepository
                from repository.base_repository import DatabaseConnection
                repo = ProjectRepository(DatabaseConnection())

                # Perform the model change in DB
                result = repo.change_semantic_model(self.project_id, recommended, keep_old_embeddings=True)

                QMessageBox.information(
                    self,
                    "Upgrade Initialized",
                    f"Project successfully upgraded to {model_display_label(recommended)}.<br><br>"
                    f"Found {result['photos_to_reindex']} photos that now need re-indexing with the new model."
                )

                # Close dashboard and trigger extraction in MainWindow
                self.accept()

                # If parent is MainWindow, trigger its extraction handler
                parent = self.parent()
                if parent and hasattr(parent, '_on_extract_embeddings'):
                    # Small delay to let this dialog close fully
                    QTimer.singleShot(150, parent._on_extract_embeddings)

            except Exception as e:
                logger.error(f"Upgrade failed: {e}")
                QMessageBox.critical(self, "Upgrade Failed", f"Failed to upgrade model: {e}")

    def _on_invalidate_stale(self):
        """Invalidate stale embeddings."""
        stale_count = self.stats.get('stale_embeddings', 0)

        reply = QMessageBox.question(
            self,
            "Invalidate Stale Embeddings",
            f"This will delete {stale_count} stale embeddings.\n\n"
            "They will be automatically regenerated on the next scan.\n\n"
            "Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            try:
                service = get_semantic_embedding_service()
                invalidated = service.invalidate_stale_embeddings(self.project_id)

                QMessageBox.information(
                    self,
                    "Invalidation Complete",
                    f"Deleted {invalidated} stale embeddings.\n"
                    "They will be regenerated on the next folder scan."
                )

                self._on_refresh()
                self.invalidateStaleRequested.emit()

            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Invalidation Failed",
                    f"Error: {e}"
                )


def show_embedding_stats_dashboard(project_id: int, parent=None) -> Optional[EmbeddingStatsDashboard]:
    """
    Show the embedding statistics dashboard.

    Args:
        project_id: Project ID to show stats for
        parent: Parent widget

    Returns:
        The dialog instance (non-modal)
    """
    dialog = EmbeddingStatsDashboard(project_id, parent)
    dialog.show()
    return dialog
