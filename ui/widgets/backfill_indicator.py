"""
CompactBackfillIndicator - Compact progress indicator for metadata backfill.

Extracted from main_window_qt.py (Phase 2, Step 2.2)

Responsibilities:
- Shows an 8px progress bar with percentage and icon when backfilling is active
- Auto-hides when not backfilling
- Click to show details dialog
- Similar to Google Photos / iPhone Photos subtle progress indicators

Version: 09.20.00.00
"""

import sys
import os
import subprocess
from pathlib import Path
from threading import Thread
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QProgressBar, QLabel,
    QPushButton, QDialog, QTextEdit, QVBoxLayout, QMessageBox
)
from PySide6.QtCore import Qt, QTimer
from translation_manager import tr


class CompactBackfillIndicator(QWidget):
    """
    Phase 2.3: Compact progress indicator for metadata backfill.
    Shows an 8px progress bar with percentage and icon when backfilling is active.
    Auto-hides when not backfilling. Click to show details dialog.
    Similar to Google Photos / iPhone Photos subtle progress indicators.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Progress bar (8px tall, compact)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f0f0f0;
            }
            QProgressBar::chunk {
                background-color: #4A90E2;
                border-radius: 3px;
            }
        """)

        # Status label with icon
        self.label = QLabel("Backfilling metadata... 0%")
        self.label.setStyleSheet("font-size: 11px; color: #666;")

        # Icon label (animated when active)
        self.icon_label = QLabel("⚡")
        self.icon_label.setStyleSheet("font-size: 14px;")

        layout.addStretch()
        layout.addWidget(self.label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.icon_label)

        # Set fixed width for progress bar
        self.progress_bar.setFixedWidth(150)

        # Click to show details
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to view backfill details")

        # Hide by default
        self.hide()

        # Animation timer for pulsing icon
        self._pulse_state = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_icon)

    def _pulse_icon(self):
        """Animate the icon to show activity."""
        self._pulse_state = not self._pulse_state
        if self._pulse_state:
            self.icon_label.setStyleSheet("font-size: 16px; color: #4A90E2;")
        else:
            self.icon_label.setStyleSheet("font-size: 14px; color: #666;")

    def start_backfill(self):
        """Show indicator and start animation."""
        self.show()
        self.progress_bar.setValue(0)
        self.label.setText("Backfilling metadata... 0%")
        self._pulse_timer.start(500)  # Pulse every 500ms

    def update_progress(self, processed: int, total: int):
        """Update progress bar and label."""
        if total > 0:
            percent = int((processed / total) * 100)
            self.progress_bar.setValue(percent)
            self.label.setText(f"Backfilling metadata... {percent}%")

    def finish_backfill(self):
        """Hide indicator when backfill is complete."""
        self._pulse_timer.stop()
        self.progress_bar.setValue(100)
        self.label.setText("Backfill complete!")
        # Auto-hide after 2 seconds
        QTimer.singleShot(2000, self.hide)

    def mousePressEvent(self, event):
        """Show details dialog when clicked."""
        if event.button() == Qt.LeftButton:
            self._show_details()
        super().mousePressEvent(event)

    def _show_details(self):
        """Show a dialog with detailed backfill status."""
        try:
            p = Path.cwd() / "app_log.txt"
            if not p.exists():
                log_text = "(no log file yet)"
            else:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-20:]
                filtered = [l for l in lines if "meta_backfill" in l or "worker" in l or "supervisor" in l]
                if not filtered:
                    filtered = lines[-10:]
                log_text = "\n".join(filtered[-10:])

            dialog = QDialog(self)
            dialog.setWindowTitle("Metadata Backfill Details")
            dialog.setMinimumSize(600, 300)

            layout = QVBoxLayout(dialog)
            text_edit = QTextEdit()
            text_edit.setPlainText(log_text)
            text_edit.setReadOnly(True)
            text_edit.setStyleSheet("font-family: monospace; font-size: 10px;")
            layout.addWidget(text_edit)

            # Buttons
            btn_layout = QHBoxLayout()
            btn_close = QPushButton("Close")
            btn_close.clicked.connect(dialog.accept)
            btn_layout.addStretch()
            btn_layout.addWidget(btn_close)
            layout.addLayout(btn_layout)

            dialog.exec()
        except Exception as e:
            QMessageBox.warning(self, tr('message_boxes.log_error_title'), tr('message_boxes.log_error_message').format(error=e))

    # ===== Compatibility methods for menu actions =====
    def _get_config(self):
        """Get settings from parent MainWindow or create new instance."""
        try:
            top = self.window()
            if top is not None and hasattr(top, "settings"):
                return top.settings
        except Exception:
            pass
        try:
            from settings_manager_qt import SettingsManager
            return SettingsManager()
        except Exception:
            return None

    def _launch_detached(self):
        """Launch backfill process in detached mode."""
        script = Path(__file__).resolve().parent.parent.parent / "workers" / "meta_backfill_pool.py"
        if not script.exists():
            QMessageBox.warning(self, tr('message_boxes.backfill_script_missing_title'), tr('message_boxes.backfill_script_missing_message').format(script=script))
            return

        settings = self._get_config()
        workers = settings.get("meta_workers", 4) if settings else 4
        timeout = settings.get("meta_timeout_secs", 8.0) if settings else 8.0
        batch = settings.get("meta_batch", 200) if settings else 200

        args = [sys.executable, str(script),
                "--workers", str(int(workers)),
                "--timeout", str(float(timeout)),
                "--batch", str(int(batch))]
        kwargs = {"close_fds": True}

        if os.name == "nt":
            import shutil
            pythonw = shutil.which("pythonw")
            if pythonw:
                args[0] = pythonw
            else:
                try:
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                except Exception:
                    kwargs["creationflags"] = 0x08000000
                try:
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = subprocess.SW_HIDE
                    kwargs["startupinfo"] = si
                except Exception:
                    pass
        else:
            kwargs["start_new_session"] = True

        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            QMessageBox.information(self, tr('message_boxes.backfill_started_title'), tr('message_boxes.backfill_started_message'))
            self.start_backfill()  # Show progress indicator
        except Exception as e:
            QMessageBox.critical(self, tr('message_boxes.backfill_error_title'), str(e))

    def _on_start_background(self):
        """Start backfill in background (menu action)."""
        try:
            self._launch_detached()
        except Exception as e:
            QMessageBox.critical(self, tr('message_boxes.backfill_error_title'), str(e))

    def _on_run_foreground(self):
        """Run backfill in foreground (menu action)."""
        def run():
            script = Path(__file__).resolve().parent.parent.parent / "workers" / "meta_backfill_pool.py"
            settings = self._get_config()
            workers = settings.get("meta_workers", 4) if settings else 4
            timeout = settings.get("meta_timeout_secs", 8.0) if settings else 8.0
            batch = settings.get("meta_batch", 200) if settings else 200

            cmd = [sys.executable, str(script),
                   "--workers", str(int(workers)),
                   "--timeout", str(float(timeout)),
                   "--batch", str(int(batch))]
            try:
                subprocess.run(cmd)
                QMessageBox.information(self, tr('message_boxes.backfill_finished_title'), tr('message_boxes.backfill_finished_message'))
                self.finish_backfill()  # Hide progress indicator
            except Exception as e:
                QMessageBox.critical(self, tr('message_boxes.backfill_finished_title'), str(e))

        self.start_backfill()  # Show progress indicator
        Thread(target=run, daemon=True).start()
