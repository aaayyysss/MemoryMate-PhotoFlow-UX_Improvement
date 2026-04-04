"""
Deep Scan Dialog for MTP Devices

Shows progress during recursive deep scan of MTP device to find all media folders.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton,
    QMessageBox, QHBoxLayout
)
from PySide6.QtCore import QThread, Signal, Qt
from typing import List


class DeepScanWorker(QThread):
    """Background worker for deep scanning MTP device"""

    # Signals
    progress = Signal(str, int)  # (current_path, folders_found_count)
    finished = Signal(list)  # List[DeviceFolder]
    error = Signal(str)

    def __init__(self, scanner, storage_item, device_type, max_depth=8):
        super().__init__()
        self.scanner = scanner
        self.storage_item = storage_item
        self.device_type = device_type
        self.max_depth = max_depth
        self._cancelled = False

    def run(self):
        """Execute deep scan in background thread"""
        try:
            print(f"[DeepScanWorker] Starting deep scan (max_depth={self.max_depth})")

            # Progress callback to update UI
            def progress_callback(current_path, folders_found):
                if self._cancelled:
                    return True  # Signal cancellation
                self.progress.emit(current_path, folders_found)
                return False  # Continue scanning

            # Run deep scan
            new_folders = self.scanner.deep_scan_mtp_device(
                storage_item=self.storage_item,
                device_type=self.device_type,
                max_depth=self.max_depth,
                progress_callback=progress_callback
            )

            if not self._cancelled:
                print(f"[DeepScanWorker] Deep scan complete: {len(new_folders)} folders found")
                self.finished.emit(new_folders)
            else:
                print(f"[DeepScanWorker] Deep scan cancelled by user")
                self.finished.emit([])

        except Exception as e:
            print(f"[DeepScanWorker] Deep scan failed: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))

    def cancel(self):
        """Cancel the deep scan"""
        print(f"[DeepScanWorker] Cancellation requested")
        self._cancelled = True


class MTPDeepScanDialog(QDialog):
    """
    Progress dialog for deep scanning MTP device.

    Shows:
    - Current folder being scanned
    - Number of media folders found so far
    - Cancel button
    """

    def __init__(self, device_name, scanner, storage_item, device_type, max_depth=8, parent=None):
        super().__init__(parent)

        self.device_name = device_name
        self.new_folders = []
        self.cancelled = False

        self.setWindowTitle(f"Deep Scan: {device_name}")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(200)

        # Layout
        layout = QVBoxLayout()

        # Title
        title_label = QLabel(f"<b>Scanning device for media folders...</b>")
        layout.addWidget(title_label)

        # Info text
        info_label = QLabel(
            "This will recursively scan the entire device to find media folders\n"
            "in deep paths (WhatsApp, Telegram, Instagram, etc.).\n\n"
            "This may take several minutes depending on device size."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Progress label (current folder being scanned)
        self.progress_label = QLabel("Starting scan...")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        # Folders found label
        self.folders_label = QLabel("Folders found: 0")
        self.folders_label.setStyleSheet("font-weight: bold; color: #0066CC;")
        layout.addWidget(self.folders_label)

        # Indeterminate progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0)  # Indeterminate mode
        layout.addWidget(self.progress_bar)

        # Button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # Cancel button
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._on_cancel)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Start worker thread
        self.worker = DeepScanWorker(scanner, storage_item, device_type, max_depth)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, current_path, folders_found):
        """Update progress display"""
        # Truncate long paths for display
        display_path = current_path
        if len(display_path) > 60:
            display_path = "..." + display_path[-57:]

        self.progress_label.setText(f"Scanning: {display_path}")
        self.folders_label.setText(f"Folders found: {folders_found}")

    def _on_finished(self, new_folders):
        """Scan completed successfully"""
        self.new_folders = new_folders

        if self.cancelled:
            # User cancelled
            self.reject()
            return

        # Show summary
        if new_folders:
            QMessageBox.information(
                self,
                "Deep Scan Complete",
                f"✓ Found {len(new_folders)} media folder(s)!\n\n"
                f"New folders will be added to the sidebar.\n\n"
                f"You can now import photos from these folders."
            )
        else:
            QMessageBox.information(
                self,
                "Deep Scan Complete",
                f"No additional media folders found.\n\n"
                f"The quick scan already detected all available media folders."
            )

        self.accept()

    def _on_error(self, error_msg):
        """Scan failed with error"""
        QMessageBox.critical(
            self,
            "Deep Scan Failed",
            f"Failed to scan device:\n\n{error_msg}\n\n"
            f"The device may have been disconnected or locked."
        )
        self.reject()

    def _on_cancel(self):
        """User clicked Cancel button"""
        # Confirm cancellation
        reply = QMessageBox.question(
            self,
            "Cancel Deep Scan?",
            "Are you sure you want to cancel the deep scan?\n\n"
            "Any folders found so far will not be added.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.cancelled = True
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Cancelling...")
            self.progress_label.setText("Cancelling scan...")

            # Signal worker to stop
            self.worker.cancel()
