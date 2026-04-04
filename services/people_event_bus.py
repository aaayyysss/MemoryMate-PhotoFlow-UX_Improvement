# services/people_event_bus.py
"""
UX-11 Integration: Lightweight event bus bridging service-layer events to Qt signals.

The app uses Qt signals exclusively — there is no central message queue.
This module provides a minimal event bus that services can emit() into,
and MainWindow subscribes to via Qt signal connections.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class PeopleEventBus(QObject):
    """
    Lightweight event bus for UX-11 people/identity services.

    Services call: event_bus.emit("event_name", payload_dict)
    UI subscribes: event_bus.event_fired.connect(handler)

    The handler receives (event_name: str, payload: dict).
    """

    event_fired = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listeners: Dict[str, List[Callable]] = {}

    def emit(self, event_name: str, payload: dict | None = None) -> None:
        """Emit a named event with optional payload."""
        payload = payload or {}
        try:
            self.event_fired.emit(event_name, payload)
        except Exception:
            logger.debug("[PeopleEventBus] Qt signal emission failed: %s", event_name, exc_info=True)

        # Also call direct listeners if any
        for fn in self._listeners.get(event_name, []):
            try:
                fn(payload)
            except Exception:
                logger.debug("[PeopleEventBus] Listener failed for %s", event_name, exc_info=True)

    def subscribe(self, event_name: str, callback: Callable) -> None:
        """Subscribe a callback to a specific event name."""
        self._listeners.setdefault(event_name, []).append(callback)

    def unsubscribe(self, event_name: str, callback: Callable) -> None:
        """Remove a callback from a specific event name."""
        listeners = self._listeners.get(event_name, [])
        if callback in listeners:
            listeners.remove(callback)
