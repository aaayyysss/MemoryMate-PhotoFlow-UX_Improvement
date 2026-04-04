"""
Similar Shot Progress Dialog

Displays real-time progress for similar shot stack generation with:
- Current status
- Percentage complete
- Statistics (photos considered, stacks created, memberships, errors)
- Estimated time remaining
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
import time


class SimilarShotProgressDialog(QDialog):
    """
    Progress dialog for similar shot stack generation.

    Shows:
    - Current progress
    - Progress bar with percentage
    - Statistics: photos considered, stacks created, memberships, errors
    - Completion message
    """

    # Signal emitted when user clicks cancel (not implemented yet)
    cancelled = Signal()

    def __init__(self, total_photos: int, parent=None):
        """
        Initialize progress dialog.

        Args:
            total_photos: Estimated number of photos to analyze
            parent: Parent widget
        """
        super().__init__(parent)
        self.total_photos = total_photos
        self.start_time = time.time()
        self.is_completed = False

        # Stats
        self.photos_considered = 0
        self.stacks_created = 0
        self.memberships_created = 0
        self.error_count = 0

        self.setWindowTitle("Generating Similar Shot Stacks")
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
        title = QLabel("🔍 Generating Similar Shot Stacks")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("Clustering photos by time proximity and visual similarity...")
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
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
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

        # Photos considered
        photos_label = QLabel("Photos Considered:")
        photos_label.setStyleSheet("color: #666; font-size: 11px;")
        self.photos_value = QLabel("0")
        self.photos_value.setStyleSheet("font-weight: bold; font-size: 11px;")
        stats_grid_layout.addWidget(photos_label, 0, 0)
        stats_grid_layout.addWidget(self.photos_value, 0, 1)

        # Stacks created
        stacks_label = QLabel("Stacks Created:")
        stacks_label.setStyleSheet("color: #666; font-size: 11px;")
        self.stacks_value = QLabel("0")
        self.stacks_value.setStyleSheet("font-weight: bold; font-size: 11px;")
        stats_grid_layout.addWidget(stacks_label, 1, 0)
        stats_grid_layout.addWidget(self.stacks_value, 1, 1)

        # Memberships created
        memberships_label = QLabel("Memberships:")
        memberships_label.setStyleSheet("color: #666; font-size: 11px;")
        self.memberships_value = QLabel("0")
        self.memberships_value.setStyleSheet("font-weight: bold; font-size: 11px;")
        stats_grid_layout.addWidget(memberships_label, 0, 2)
        stats_grid_layout.addWidget(self.memberships_value, 0, 3)

        # Errors
        errors_label = QLabel("Errors:")
        errors_label.setStyleSheet("color: #666; font-size: 11px;")
        self.errors_value = QLabel("0")
        self.errors_value.setStyleSheet("font-weight: bold; font-size: 11px;")
        stats_grid_layout.addWidget(errors_label, 1, 2)
        stats_grid_layout.addWidget(self.errors_value, 1, 3)

        layout.addLayout(stats_grid_layout)

        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.HLine)
        separator2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator2)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # OK button (initially hidden)
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setVisible(False)
        self.ok_button.setStyleSheet("""
            QPushButton {
                padding: 8px 24px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(self.ok_button)

        layout.addLayout(button_layout)

    @Slot(int, int, str)
    def update_progress(self, current: int, total: int, message: str):
        """
        Update progress bar and status.

        Args:
            current: Current item count
            total: Total items
            message: Status message
        """
        self.status_label.setText(message)

        # Update progress bar
        if total > 0:
            progress_pct = int((current / total) * 100)
        else:
            progress_pct = 0

        self.progress_bar.setValue(progress_pct)
        self.percentage_label.setText(f"{progress_pct}%")

        # Estimate time remaining
        if current > 0 and total > 0:
            elapsed = time.time() - self.start_time
            rate = current / elapsed  # items per second
            remaining_items = total - current
            if rate > 0:
                eta_seconds = remaining_items / rate
                if eta_seconds < 60:
                    eta_str = f"{int(eta_seconds)}s remaining"
                else:
                    eta_minutes = int(eta_seconds / 60)
                    eta_str = f"{eta_minutes}m remaining"
                self.time_label.setText(eta_str)

    @Slot(int, int, int, int)
    def on_finished(self, photos_considered: int, stacks_created: int, memberships_created: int, errors: int):
        """
        Handle worker finished signal.

        Args:
            photos_considered: Number of photos analyzed
            stacks_created: Number of stacks created
            memberships_created: Number of memberships created
            errors: Number of errors
        """
        self.is_completed = True
        self.photos_considered = photos_considered
        self.stacks_created = stacks_created
        self.memberships_created = memberships_created
        self.error_count = errors

        # Update stats
        self.photos_value.setText(str(photos_considered))
        self.stacks_value.setText(str(stacks_created))
        self.memberships_value.setText(str(memberships_created))
        self.errors_value.setText(str(errors))

        # Update progress bar to 100%
        self.progress_bar.setValue(100)
        self.percentage_label.setText("100%")

        # Update status
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            time_str = f"{int(elapsed)}s"
        else:
            time_str = f"{int(elapsed / 60)}m {int(elapsed % 60)}s"

        if errors > 0:
            status_msg = f"✓ Completed with {errors} errors in {time_str}"
            self.status_label.setStyleSheet("color: #ff9800; font-size: 12px; font-weight: bold;")
        else:
            status_msg = f"✓ Completed successfully in {time_str}"
            self.status_label.setStyleSheet("color: #4CAF50; font-size: 12px; font-weight: bold;")

        self.status_label.setText(status_msg)
        self.time_label.setText(f"Total time: {time_str}")

        # Show OK button
        self.ok_button.setVisible(True)

    @Slot(str)
    def on_error(self, error_message: str):
        """
        Handle worker error signal.

        Args:
            error_message: Error description
        """
        self.is_completed = True
        self.status_label.setText(f"✗ Error: {error_message}")
        self.status_label.setStyleSheet("color: #f44336; font-size: 12px; font-weight: bold;")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: #f44336;
                border-radius: 3px;
            }
        """)
        self.ok_button.setVisible(True)

    def get_stats(self) -> dict:
        """
        Get final statistics.

        Returns:
            Dictionary with photos_considered, stacks_created, memberships_created, errors
        """
        return {
            'photos_considered': self.photos_considered,
            'stacks_created': self.stacks_created,
            'memberships_created': self.memberships_created,
            'errors': self.error_count
        }
