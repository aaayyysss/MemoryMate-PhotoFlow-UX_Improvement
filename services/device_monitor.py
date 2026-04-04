"""
Windows Device Change Monitor

Detects mobile device connection/disconnection events using native Windows WM_DEVICECHANGE messages.
More efficient than timer-based polling - instant detection with zero CPU overhead when idle.

Platform Support:
- Windows: Native WM_DEVICECHANGE event monitoring
- macOS/Linux: Falls back to timer-based polling (not implemented yet)

Usage:
    from services.device_monitor import DeviceMonitor

    monitor = DeviceMonitor()
    monitor.deviceChanged.connect(on_device_changed)
    monitor.start()  # Start monitoring (Windows only)

    # Later...
    monitor.stop()
"""

import platform
from PySide6.QtCore import QObject, Signal, QAbstractNativeEventFilter
from PySide6.QtWidgets import QWidget


class DeviceMonitor(QObject):
    """
    Cross-platform device change monitor.

    Emits deviceChanged signal when a device is connected or disconnected.
    On Windows, uses native WM_DEVICECHANGE messages for instant detection.
    On other platforms, timer-based polling should be used instead.
    """

    # Signal emitted when a device is connected or disconnected
    deviceChanged = Signal(str)  # event_type: "connected" or "disconnected"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._platform = platform.system()
        self._event_filter = None
        self._hidden_window = None

        print(f"[DeviceMonitor] Initialized for platform: {self._platform}")

    def start(self):
        """Start monitoring for device changes."""
        if self._active:
            print(f"[DeviceMonitor] Already monitoring")
            return

        if self._platform == "Windows":
            self._start_windows_monitoring()
        else:
            print(f"[DeviceMonitor] Native monitoring not available for {self._platform}")
            print(f"[DeviceMonitor] Use timer-based polling instead")

        self._active = True

    def stop(self):
        """Stop monitoring for device changes."""
        if not self._active:
            return

        if self._platform == "Windows":
            self._stop_windows_monitoring()

        self._active = False
        print(f"[DeviceMonitor] Monitoring stopped")

    def is_active(self) -> bool:
        """Check if monitoring is currently active."""
        return self._active

    def _start_windows_monitoring(self):
        """Start Windows-specific device change monitoring."""
        try:
            # Create a hidden native window to receive WM_DEVICECHANGE messages
            # Qt's native event filter doesn't work reliably without a native window
            self._hidden_window = WindowsDeviceWindow()
            self._hidden_window.deviceChanged.connect(self._on_windows_device_event)

            print(f"[DeviceMonitor] Windows device monitoring started")
            print(f"[DeviceMonitor] Listening for WM_DEVICECHANGE messages")

        except Exception as e:
            print(f"[DeviceMonitor] Failed to start Windows monitoring: {e}")
            import traceback
            traceback.print_exc()

    def _stop_windows_monitoring(self):
        """Stop Windows-specific device change monitoring."""
        if self._hidden_window:
            try:
                self._hidden_window.close()
                self._hidden_window.deleteLater()
                self._hidden_window = None
                print(f"[DeviceMonitor] Windows monitoring stopped")
            except Exception as e:
                print(f"[DeviceMonitor] Error stopping Windows monitoring: {e}")

    def _on_windows_device_event(self, event_type: str):
        """Handle Windows device change event."""
        print(f"[DeviceMonitor] Device event detected: {event_type}")
        self.deviceChanged.emit(event_type)


class WindowsDeviceWindow(QWidget):
    """
    Hidden native Windows window to receive WM_DEVICECHANGE messages.

    Qt doesn't expose device change notifications directly, so we create
    a native window and override nativeEvent() to intercept WM_DEVICECHANGE.
    """

    deviceChanged = Signal(str)  # event_type: "connected" or "disconnected"

    # Windows message constants
    WM_DEVICECHANGE = 0x0219
    DBT_DEVICEARRIVAL = 0x8000  # Device connected
    DBT_DEVICEREMOVECOMPLETE = 0x8004  # Device disconnected
    DBT_DEVTYP_VOLUME = 0x00000002  # Logical volume (drive letter)
    DBT_DEVTYP_PORT = 0x00000003  # Serial/parallel port

    def __init__(self):
        super().__init__()

        # Make window invisible and non-interactive
        self.setWindowTitle("Device Monitor")
        self.resize(1, 1)
        self.hide()

        # CRITICAL: Create native window handle to receive Windows messages
        # Without this, nativeEvent() won't be called
        self.winId()

        print(f"[WindowsDeviceWindow] Native window created (hidden)")

    def nativeEvent(self, eventType, message):
        """
        Override Qt's native event handler to intercept Windows messages.

        Args:
            eventType: Event type (e.g., b"windows_generic_MSG")
            message: Native message pointer (MSG structure)

        Returns:
            (handled, result) tuple
        """
        # Only process Windows messages
        if eventType != b"windows_generic_MSG":
            return False, 0

        try:
            import ctypes
            from ctypes import wintypes

            # Cast message pointer to MSG structure
            msg = ctypes.wintypes.MSG.from_address(int(message))

            # Check if this is a WM_DEVICECHANGE message
            if msg.message == self.WM_DEVICECHANGE:
                event = msg.wParam

                if event == self.DBT_DEVICEARRIVAL:
                    # Device connected
                    print(f"[WindowsDeviceWindow] WM_DEVICECHANGE: Device connected (DBT_DEVICEARRIVAL)")
                    self.deviceChanged.emit("connected")

                elif event == self.DBT_DEVICEREMOVECOMPLETE:
                    # Device disconnected
                    print(f"[WindowsDeviceWindow] WM_DEVICECHANGE: Device disconnected (DBT_DEVICEREMOVECOMPLETE)")
                    self.deviceChanged.emit("disconnected")

                # Don't block the event - let other handlers process it too
                return False, 0

        except Exception as e:
            print(f"[WindowsDeviceWindow] Error processing native event: {e}")
            import traceback
            traceback.print_exc()

        # Let Qt handle the event normally
        return False, 0


# Singleton instance for easy access
_device_monitor = None

def get_device_monitor() -> DeviceMonitor:
    """Get or create singleton DeviceMonitor instance."""
    global _device_monitor
    if _device_monitor is None:
        _device_monitor = DeviceMonitor()
    return _device_monitor
