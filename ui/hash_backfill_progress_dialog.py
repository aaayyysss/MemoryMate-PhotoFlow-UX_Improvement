"""
Hash Backfill Progress Dialog

Displays real-time progress for hash computation and asset linking with:
- Current photo being processed
- Percentage complete
- Statistics (hashed, linked, errors)
- Estimated time remaining
- Cancel button
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from pathlib import Path
import time


class HashBackfillProgressDialog(QDialog):
    """
    Progress dialog for hash backfill and asset linking.

    Shows:
    - Current progress (photos processed)
    - Progress bar with percentage
    - Statistics: hashed, linked, errors
    - Estimated time remaining
    - Cancel button to abort backfill
    """

    # Signal emitted when user clicks cancel
    cancelled = Signal()

    def __init__(self, total_photos: int, parent=None):
        """
        Initialize progress dialog.

        Args:
            total_photos: Total number of photos to process
            parent: Parent widget
        """
        super().__init__(parent)
        self.total_photos = total_photos
        self.current_index = 0
        self.start_time = time.time()
        self.is_cancelled = False

        # Stats
        self.hashed_count = 0
        self.linked_count = 0
        self.error_count = 0

        self.setWindowTitle("Preparing Duplicate Detection")
        self.setModal(True)
        self.setMinimumWidth(550)
        self.setMinimumHeight(280)

        self._init_ui()

    def _init_ui(self):
        """Initialize UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title
        title = QLabel("🔍 Preparing Duplicate Detection")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("Computing file hashes and linking photos to assets...")
        subtitle.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(subtitle)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Status section
        status_layout = QVBoxLayout()
        status_layout.setSpacing(8)

        # Current status label
        self.status_label = QLabel("Initializing...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("""
            QLabel {
                color: #555;
                font-size: 12px;
                padding: 4px;
            }
        """)
        status_layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(self.total_photos)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m (%p%)")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
        """)
        status_layout.addWidget(self.progress_bar)

        # Stats row: percentage | time remaining
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        # Percentage label
        self.percentage_label = QLabel("0%")
        self.percentage_label.setStyleSheet("font-size: 11px; color: #666;")
        stats_layout.addWidget(self.percentage_label)

        stats_layout.addStretch()

        # Time remaining label
        self.time_label = QLabel("Estimating time...")
        self.time_label.setStyleSheet("font-size: 11px; color: #666;")
        stats_layout.addWidget(self.time_label)

        status_layout.addLayout(stats_layout)

        layout.addLayout(status_layout)

        # Statistics grid
        stats_grid_layout = QGridLayout()
        stats_grid_layout.setSpacing(12)

        # Hashed
        hashed_label = QLabel("Hashed:")
        hashed_label.setStyleSheet("color: #666; font-size: 11px;")
        self.hashed_value = QLabel("0")
        self.hashed_value.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")
        stats_grid_layout.addWidget(hashed_label, 0, 0)
        stats_grid_layout.addWidget(self.hashed_value, 0, 1)

        # Linked
        linked_label = QLabel("Linked:")
        linked_label.setStyleSheet("color: #666; font-size: 11px;")
        self.linked_value = QLabel("0")
        self.linked_value.setStyleSheet("color: #2196F3; font-size: 11px; font-weight: bold;")
        stats_grid_layout.addWidget(linked_label, 0, 2)
        stats_grid_layout.addWidget(self.linked_value, 0, 3)

        # Errors
        errors_label = QLabel("Errors:")
        errors_label.setStyleSheet("color: #666; font-size: 11px;")
        self.errors_value = QLabel("0")
        self.errors_value.setStyleSheet("color: #f44336; font-size: 11px; font-weight: bold;")
        stats_grid_layout.addWidget(errors_label, 0, 4)
        stats_grid_layout.addWidget(self.errors_value, 0, 5)

        stats_grid_layout.setColumnStretch(6, 1)

        layout.addLayout(stats_grid_layout)

        layout.addStretch()

        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.HLine)
        separator2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator2)

        # Button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumWidth(100)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                padding: 6px 16px;
                background-color: #f5f5f5;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
            }
            QPushButton:pressed {
                background-color: #ddd;
            }
        """)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

    @Slot(int, int, str)
    def update_progress(self, current: int, total: int, message: str):
        """
        Update progress from worker.

        Args:
            current: Current photo index
            total: Total photos
            message: Status message
        """
        self.current_index = current
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

        # Update status
        self.status_label.setText(message)

        # Update percentage
        if total > 0:
            percentage = (current / total) * 100
            self.percentage_label.setText(f"{percentage:.1f}%")

        # Estimate time remaining
        if current > 0:
            elapsed = time.time() - self.start_time
            rate = current / elapsed
            if rate > 0:
                remaining = (total - current) / rate
                if remaining < 60:
                    time_str = f"{int(remaining)}s remaining"
                elif remaining < 3600:
                    time_str = f"{int(remaining/60)}m {int(remaining%60)}s remaining"
                else:
                    time_str = f"{int(remaining/3600)}h {int((remaining%3600)/60)}m remaining"
                self.time_label.setText(time_str)
        else:
            self.time_label.setText("Estimating time...")

    @Slot(int, int, int, int)
    def on_finished(self, scanned: int, hashed: int, linked: int, errors: int):
        """
        Handle completion from worker.

        Args:
            scanned: Total photos scanned
            hashed: Photos hashed
            linked: Photos linked to assets
            errors: Number of errors
        """
        self.hashed_count = hashed
        self.linked_count = linked
        self.error_count = errors

        # Update stats
        self.hashed_value.setText(str(hashed))
        self.linked_value.setText(str(linked))
        self.errors_value.setText(str(errors))

        # Update status
        if errors > 0:
            self.status_label.setText(f"✓ Completed with {errors} error(s)")
            self.status_label.setStyleSheet("color: #f44336; font-weight: bold;")
        else:
            self.status_label.setText(f"✓ Successfully processed {scanned} photos")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")

        # Change button to "Close"
        self.cancel_button.setText("Close")
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.accept)

        # Update progress bar to 100%
        self.progress_bar.setValue(scanned)
        self.percentage_label.setText("100%")
        self.time_label.setText("Complete")

    @Slot(str)
    def on_error(self, error_message: str):
        """
        Handle error from worker.

        Args:
            error_message: Error description
        """
        self.status_label.setText(f"✗ Error: {error_message}")
        self.status_label.setStyleSheet("color: #f44336; font-weight: bold;")

        # Change button to "Close"
        self.cancel_button.setText("Close")
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.reject)

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        self.is_cancelled = True
        self.status_label.setText("Cancelling...")
        self.cancel_button.setEnabled(False)
        self.cancelled.emit()
