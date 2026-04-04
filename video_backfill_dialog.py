"""
Video Metadata Backfill Dialog

Provides a UI for running video metadata backfill with progress indicator.
"""

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QTextEdit, QCheckBox
)
from PySide6.QtGui import QFont
from backfill_video_dates import backfill_video_dates


class BackfillWorker(QThread):
    """Background worker for running video metadata backfill."""

    progress = Signal(int, int, str)  # current, total, message
    finished = Signal(dict)  # stats dict
    error = Signal(str)  # error message

    def __init__(self, project_id: int, dry_run: bool = False):
        super().__init__()
        self.project_id = project_id
        self.dry_run = dry_run
        self._cancelled = False

    def run(self):
        """Run backfill in background thread."""
        try:
            def progress_callback(current, total, message):
                if self._cancelled:
                    raise InterruptedError("Backfill cancelled by user")
                self.progress.emit(current, total, message)

            stats = backfill_video_dates(
                project_id=self.project_id,
                dry_run=self.dry_run,
                progress_callback=progress_callback
            )

            self.finished.emit(stats)

        except InterruptedError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"Error during backfill: {str(e)}")

    def cancel(self):
        """Cancel the backfill operation."""
        self._cancelled = True


class VideoBackfillDialog(QDialog):
    """
    Dialog for running video metadata backfill with progress indicator.

    Shows:
    - Progress bar with current/total count
    - Log of processed videos
    - Statistics summary when complete
    """

    def __init__(self, parent=None, project_id: int = 1):
        super().__init__(parent)
        self.project_id = project_id
        self.worker = None
        self.stats = None

        self.setWindowTitle("Video Metadata Backfill")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        self._setup_ui()

    def _setup_ui(self):
        """Setup dialog UI."""
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("🎬 Video Metadata Backfill")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "This will re-extract metadata (dates, duration, resolution, codec) "
            "for all videos missing date information.\n"
            "The process runs in the background and may take a few minutes for large collections."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc)

        # Dry run checkbox
        self.chk_dry_run = QCheckBox("Dry Run (preview without making changes)")
        self.chk_dry_run.setChecked(False)
        layout.addWidget(self.chk_dry_run)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m - %p%")
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("Ready to start")
        self.status_label.setStyleSheet("color: #0066cc; font-weight: bold;")
        layout.addWidget(self.status_label)

        # Log area
        log_label = QLabel("Processing Log:")
        layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        self.log_text.setStyleSheet("font-family: monospace; font-size: 9pt;")
        layout.addWidget(self.log_text)

        # Buttons
        button_layout = QHBoxLayout()

        self.btn_start = QPushButton("Start Backfill")
        self.btn_start.clicked.connect(self.start_backfill)
        self.btn_start.setDefault(True)
        button_layout.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.cancel_backfill)
        self.btn_cancel.setEnabled(False)
        button_layout.addWidget(self.btn_cancel)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        self.btn_close.setEnabled(False)
        button_layout.addWidget(self.btn_close)

        button_layout.addStretch()
        layout.addLayout(button_layout)

    def start_backfill(self):
        """Start the backfill process."""
        dry_run = self.chk_dry_run.isChecked()

        # Disable controls during processing
        self.btn_start.setEnabled(False)
        self.chk_dry_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_close.setEnabled(False)

        # Clear log
        self.log_text.clear()
        self.log_append("Starting backfill...")
        self.log_append(f"Project ID: {self.project_id}")
        if dry_run:
            self.log_append("Mode: DRY RUN (no changes will be made)")
        else:
            self.log_append("Mode: LIVE (database will be updated)")
        self.log_append("-" * 60)

        # Create and start worker
        self.worker = BackfillWorker(self.project_id, dry_run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

        self.status_label.setText("Processing...")
        self.status_label.setStyleSheet("color: #0066cc; font-weight: bold;")

    def cancel_backfill(self):
        """Cancel the backfill process."""
        if self.worker and self.worker.isRunning():
            self.log_append("\n⚠️ Cancelling backfill...")
            self.status_label.setText("Cancelling...")
            self.worker.cancel()
            self.worker.wait()  # Wait for thread to finish

            self.btn_cancel.setEnabled(False)
            self.btn_start.setEnabled(True)
            self.chk_dry_run.setEnabled(True)
            self.btn_close.setEnabled(True)

            self.status_label.setText("Cancelled")
            self.status_label.setStyleSheet("color: #ff6600; font-weight: bold;")

    def on_progress(self, current: int, total: int, message: str):
        """Handle progress update from worker."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(message)

        # Log every 10th video or special messages
        if message.startswith("✓") or message.startswith("⚠") or message.startswith("Processing {"):
            self.log_append(message)
        elif current % 10 == 0 or current == total:
            self.log_append(f"[{current}/{total}] {message}")

    def on_finished(self, stats: dict):
        """Handle backfill completion."""
        self.stats = stats

        # Update UI
        self.btn_cancel.setEnabled(False)
        self.btn_start.setEnabled(True)
        self.chk_dry_run.setEnabled(True)
        self.btn_close.setEnabled(True)

        self.status_label.setText("✓ Backfill Complete!")
        self.status_label.setStyleSheet("color: #00aa00; font-weight: bold;")

        # Show summary
        self.log_append("\n" + "=" * 60)
        self.log_append("SUMMARY")
        self.log_append("=" * 60)
        self.log_append(f"Total videos:          {stats['total']}")
        self.log_append(f"Missing dates:         {stats['missing_dates']}")
        self.log_append(f"Successfully updated:  {stats['updated']}")
        self.log_append(f"Failed:                {stats['failed']}")
        self.log_append(f"Skipped (not found):   {stats['skipped']}")

        if self.chk_dry_run.isChecked():
            self.log_append("\nThis was a DRY RUN - no changes were made.")
        else:
            self.log_append("\n✓ Backfill complete! Video dates have been updated.")

    def on_error(self, error_msg: str):
        """Handle backfill error."""
        self.btn_cancel.setEnabled(False)
        self.btn_start.setEnabled(True)
        self.chk_dry_run.setEnabled(True)
        self.btn_close.setEnabled(True)

        self.status_label.setText("✗ Error occurred")
        self.status_label.setStyleSheet("color: #cc0000; font-weight: bold;")

        self.log_append(f"\n✗ ERROR: {error_msg}")

    def log_append(self, message: str):
        """Append message to log."""
        self.log_text.append(message)

        # Auto-scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        """Handle dialog close - ensure worker is stopped."""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
        event.accept()
