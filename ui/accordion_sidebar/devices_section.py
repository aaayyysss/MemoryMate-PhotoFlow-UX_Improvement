# ui/accordion_sidebar/devices_section.py
# Mobile/external devices section for importing photos

import logging
import threading
from typing import List

from PySide6.QtCore import Signal, Qt, QObject
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QSizePolicy,
)

from services.device_sources import DeviceScanner, MobileDevice
from services.device_monitor import get_device_monitor
from translation_manager import tr
from .base_section import BaseSection


class DevicesSectionSignals(QObject):
    """Signals for async device loading."""

    loaded = Signal(int, list)
    error = Signal(int, str)

logger = logging.getLogger(__name__)


class DevicesSection(BaseSection):
    """List connected mobile/external devices and expose quick actions."""

    deviceSelected = Signal(str)  # emits root path or mount point

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = DevicesSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)
        self.signals.error.connect(lambda *args: None)
        self._scanner = DeviceScanner(register_devices=False)
        self._monitor = None

    def get_section_id(self) -> str:
        return "devices"

    def get_title(self) -> str:
        return tr("sidebar.header_devices") if callable(tr) else "Devices"

    def get_icon(self) -> str:
        return "📱"

    def load_section(self) -> None:
        self._start_monitor()

        self._generation += 1
        current_gen = self._generation
        self._loading = True

        logger.info(f"[DevicesSection] Scanning devices (generation {current_gen})…")

        def work():
            try:
                return self._scanner.scan_devices(force=True)
            except Exception as e:
                logger.error(f"[DevicesSection] Scan error: {e}")
                self.signals.error.emit(current_gen, str(e))
                return []

        def on_complete():
            devices = work()
            if current_gen != self._generation:
                logger.debug(
                    f"[DevicesSection] Discarding stale scan (gen {current_gen} vs {self._generation})"
                )
                return
            self._loading = False
            self._on_devices_loaded(current_gen, devices)

        threading.Thread(target=on_complete, daemon=True).start()

    def _on_devices_loaded(self, generation: int, devices: List[MobileDevice]):
        self.signals.loaded.emit(generation, devices)

    def create_content_widget(self, data):
        devices: List[MobileDevice] = data or []

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # Header actions
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        refresh_btn = QPushButton(
            f"⟳ {tr('sidebar.devices.refresh') if callable(tr) else 'Refresh devices'}"
        )
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        refresh_btn.clicked.connect(self.load_section)
        header_row.addWidget(refresh_btn)

        hint_label = QLabel(
            tr("sidebar.devices.help")
            if callable(tr)
            else "Connect your phone or camera to import photos"
        )
        hint_label.setStyleSheet("color:#5f6368;font-size:11px;")
        header_row.addWidget(hint_label)
        header_row.addStretch()

        layout.addLayout(header_row)

        if not devices:
            empty = QLabel(
                tr("sidebar.devices.empty")
                if callable(tr)
                else "No devices detected. Plug in a phone or camera."
            )
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            empty.setStyleSheet("color:#5f6368;padding:8px;")
            layout.addWidget(empty)
            layout.addStretch()
            return container

        for device in devices:
            layout.addWidget(self._build_device_row(device))

        layout.addStretch()
        return container

    def _build_device_row(self, device: MobileDevice) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(6, 6, 6, 6)
        row_layout.setSpacing(8)

        label = QLabel(f"{device.label} ({device.device_type})")
        label.setStyleSheet("font-weight:600;color:#202124;")
        row_layout.addWidget(label)

        if device.folders:
            counts = sum(f.photo_count or 0 for f in device.folders)
            folder_label = QLabel(
                tr("sidebar.devices.folder_count").format(counts)
                if callable(tr)
                else f"{counts} items detected"
            )
            folder_label.setStyleSheet("color:#5f6368;font-size:11px;")
            row_layout.addWidget(folder_label)

        row_layout.addStretch()

        open_btn = QPushButton(tr("sidebar.devices.open") if callable(tr) else "Open")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setStyleSheet(
            """
            QPushButton { padding:6px 10px; border:1px solid #dadce0; border-radius:6px; }
            QPushButton:hover { background:#eef3fd; }
            QPushButton:pressed { background:#e8f0fe; }
            """
        )
        open_btn.clicked.connect(lambda: self.deviceSelected.emit(device.root_path))
        row_layout.addWidget(open_btn)

        return row

    def _start_monitor(self):
        if self._monitor:
            return
        try:
            self._monitor = get_device_monitor()
            self._monitor.deviceChanged.connect(lambda evt: self.load_section())
            if not self._monitor.is_active():
                self._monitor.start()
        except Exception as e:
            logger.debug("[DevicesSection] Device monitor unavailable: %s", e)

    def cleanup(self):
        try:
            if self._monitor and self._monitor.is_active():
                self._monitor.stop()
        except Exception:
            logger.debug("[DevicesSection] Failed to stop monitor", exc_info=True)

    def set_db(self, db):
        """Optional hook so scanner can register devices if desired."""
        try:
            self._scanner.db = db
        except Exception:
            logger.debug("[DevicesSection] Could not attach DB", exc_info=True)
