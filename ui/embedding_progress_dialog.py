"""
Embedding Extraction Progress Dialog

Displays real-time progress for visual embedding extraction with:
- Current photo being processed
- Percentage complete
- Estimated time remaining
- Cancel button
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from pathlib import Path
import time


class EmbeddingProgressDialog(QDialog):
    """
    Progress dialog for embedding extraction.

    Shows:
    - Current photo being processed
    - Progress bar with percentage
    - Estimated time remaining
    - Cancel button to abort extraction
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

        self.setWindowTitle("Extracting Visual Embeddings")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(200)

        self._init_ui()

    def _init_ui(self):
        """Initialize UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Title
        title = QLabel("🔍 Extracting Visual Embeddings")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Status section
        status_layout = QVBoxLayout()
        status_layout.setSpacing(8)

        # Current photo label
        self.current_photo_label = QLabel("Preparing...")
        self.current_photo_label.setWordWrap(True)
        self.current_photo_label.setStyleSheet("""
            QLabel {
                color: #555;
                font-size: 12px;
                padding: 4px;
            }
        """)
        status_layout.addWidget(self.current_photo_label)

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
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f8f8f8;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
                border-color: #999;
            }
            QPushButton:pressed {
                background-color: #ddd;
            }
        """)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

    @Slot(int, int, str, str)
    def update_progress(self, current: int, total: int, photo_path: str, message: str = ""):
        """
        Update progress display.

        Args:
            current: Current photo index (1-based)
            total: Total number of photos
            photo_path: Path to current photo being processed
            message: Optional message
        """
        if self.is_cancelled:
            return

        self.current_index = current

        # Update current photo label
        photo_name = Path(photo_path).name if photo_path else "Processing..."
        display_text = f"Processing: {photo_name}"
        if message:
            display_text = f"{message}: {photo_name}"
        self.current_photo_label.setText(display_text)

        # Update progress bar
        self.progress_bar.setValue(current)

        # Update percentage
        percentage = (current / total * 100) if total > 0 else 0
        self.percentage_label.setText(f"{percentage:.1f}%")

        # Calculate and update ETA
        self._update_eta(current, total)

    def _update_eta(self, current: int, total: int):
        """
        Calculate and display estimated time remaining.

        Args:
            current: Current photo index
            total: Total number of photos
        """
        if current <= 0:
            self.time_label.setText("Estimating time...")
            return

        # Calculate elapsed time
        elapsed = time.time() - self.start_time

        # Calculate average time per photo
        avg_time_per_photo = elapsed / current

        # Estimate remaining time
        remaining_photos = total - current
        eta_seconds = avg_time_per_photo * remaining_photos

        # Format ETA
        if eta_seconds < 60:
            eta_str = f"{int(eta_seconds)}s remaining"
        elif eta_seconds < 3600:
            minutes = int(eta_seconds / 60)
            seconds = int(eta_seconds % 60)
            eta_str = f"{minutes}m {seconds}s remaining"
        else:
            hours = int(eta_seconds / 3600)
            minutes = int((eta_seconds % 3600) / 60)
            eta_str = f"{hours}h {minutes}m remaining"

        self.time_label.setText(eta_str)

    @Slot()
    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        self.is_cancelled = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling...")
        self.current_photo_label.setText("Cancelling extraction...")
        self.cancelled.emit()

    @Slot(int, int)
    def on_finished(self, success_count: int, failed_count: int):
        """
        Handle extraction completion.

        Args:
            success_count: Number of successfully processed photos
            failed_count: Number of failed photos
        """
        # Update display
        self.progress_bar.setValue(self.total_photos)
        self.percentage_label.setText("100%")
        self.time_label.setText("Complete!")

        if failed_count > 0:
            self.current_photo_label.setText(
                f"✓ Completed: {success_count} succeeded, {failed_count} failed"
            )
        else:
            self.current_photo_label.setText(
                f"✓ Completed: {success_count} photos processed successfully"
            )

        # Change cancel button to close
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.accept)

    @Slot(str)
    def on_error(self, error_message: str):
        """
        Handle extraction error.

        Args:
            error_message: Error message to display
        """
        self.current_photo_label.setText(f"❌ Error: {error_message}")
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.reject)

    def closeEvent(self, event):
        """Handle dialog close event."""
        if not self.is_cancelled and self.current_index < self.total_photos:
            # User trying to close during processing
            self._on_cancel_clicked()
        event.accept()
