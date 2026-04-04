"""
BackfillStatusPanel - Metadata backfill status and control panel.

Extracted from main_window_qt.py as part of Phase 1 refactoring.
"""

import sys
import os
import subprocess
from pathlib import Path
from threading import Thread

from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QMessageBox
)

from translation_manager import tr


class BackfillStatusPanel(QWidget):
    """
    Simple panel that shows last lines of app_log.txt related to backfill and offers
    quick start/foreground-run buttons.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMaximumHeight(240)
        layout = QVBoxLayout(self)
        lbl = QLabel("<b>Metadata Backfill Status</b>")
        layout.addWidget(lbl)
        self.txt = QLabel(tr('status_messages.no_log_yet'))
        self.txt.setWordWrap(True)
        self.txt.setStyleSheet("font-family: monospace;")
        layout.addWidget(self.txt, 1)
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start (background)")
        self.btn_fore = QPushButton("Run (foreground)")
        self.btn_stop = QPushButton("Stop (not implemented)")
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_fore)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)
        self.btn_start.clicked.connect(self._on_start_background)
        self.btn_fore.clicked.connect(self._on_run_foreground)
        self.btn_stop.clicked.connect(self._on_stop)
        self._tail_log()

    def _get_config(self):
        """
        Safely obtain the settings object:
         - Prefer the top-level MainWindow (self.window()) if it exposes .settings
         - Fallback to module SettingsManager()
        """
        try:
            top = self.window()
            if top is not None and hasattr(top, "settings"):
                return top.settings
        except Exception:
            pass
        # Fallback: import SettingsManager and return a new instance (read-only defaults)
        try:
            from settings_manager_qt import SettingsManager
            return SettingsManager()
        except Exception:
            return None

    def _launch_detached(self):
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

        # On Windows, try to suppress console windows:
        if os.name == "nt":
            # Prefer pythonw.exe if available (no console)
            pythonw = None
            try:
                import shutil
                pythonw = shutil.which("pythonw")
            except Exception:
                pythonw = None

            if pythonw:
                args[0] = pythonw
            else:
                # If pythonw not available, use CREATE_NO_WINDOW and startupinfo to hide window
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
            # For POSIX, detach the child so it doesn't hold terminal (start new session)
            kwargs["start_new_session"] = True

        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            QMessageBox.information(self, tr('message_boxes.backfill_started_title'), tr('message_boxes.backfill_started_message'))
        except Exception as e:
            QMessageBox.critical(self, tr('message_boxes.backfill_error_title'), str(e))

    def _on_start_background(self):
        # Quick guard: spawn detached in main thread (fire-and-forget)
        try:
            self._launch_detached()
        except Exception as e:
            QMessageBox.critical(self, tr('message_boxes.backfill_error_title'), str(e))

    def _on_run_foreground(self):
        """
        Run backfill in a background Python thread (foreground process run),
        so the GUI thread doesn't block. Use the same config resolution helper.
        """
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
            except Exception as e:
                QMessageBox.critical(self, tr('message_boxes.backfill_finished_title'), str(e))

        Thread(target=run, daemon=True).start()

    def _on_stop(self):
        QMessageBox.information(self, tr('message_boxes.stop_title'), tr('message_boxes.stop_message'))

    def _tail_log(self):
        try:
            p = Path.cwd() / "app_log.txt"
            if not p.exists():
                self.txt.setText(tr('status_messages.no_log_yet'))
                return
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-12:]
            filtered = [l for l in lines if "meta_backfill" in l or "worker" in l or "supervisor" in l]
            if not filtered:
                filtered = lines[-6:]
            self.txt.setText("\n".join(filtered[-6:]))
        except Exception as e:
            self.txt.setText(str(e))

