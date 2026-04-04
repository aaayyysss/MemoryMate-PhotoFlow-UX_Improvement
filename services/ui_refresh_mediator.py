# services/ui_refresh_mediator.py
# Debounced, visibility-safe UI refresh coordinator.
#
# Instead of each worker/controller calling sidebar.reload() directly,
# everything goes through this mediator which:
#   1. Debounces rapid-fire requests (250 ms QTimer)
#   2. Checks widget visibility before refreshing
#   3. Stores pending refreshes for hidden widgets
#   4. Processes pending refreshes when layout is activated

import logging
from typing import Optional, Set

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


class UIRefreshMediator(QObject):
    """
    Central coordinator for all sidebar / people / duplicates refresh.

    Usage:
        mediator = UIRefreshMediator(main_window)
        mediator.request_refresh({"people"}, "faces_batch", project_id=1)
        # ... later, on layout switch ...
        mediator.on_layout_activated("google")
    """

    # Debounce interval in milliseconds
    DEBOUNCE_MS = 250

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        # pending[project_id] = set of section names
        self._pending: dict[int, Set[str]] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    # ── Public API ───────────────────────────────────────────

    def request_refresh(
        self,
        sections: Set[str],
        reason: str = "",
        project_id: Optional[int] = None,
    ):
        """
        Request a debounced refresh of *sections* (e.g. {"people", "duplicates"}).

        The actual refresh happens after DEBOUNCE_MS milliseconds of silence,
        or immediately if the timer was already waiting and more sections arrived.
        """
        if project_id is None:
            project_id = self._current_project_id()
        if project_id is None:
            logger.debug("[UIRefreshMediator] No project_id, skipping refresh request")
            return

        existing = self._pending.get(project_id, set())
        existing.update(sections)
        self._pending[project_id] = existing

        logger.debug(
            "[UIRefreshMediator] Queued refresh %s reason=%s pid=%d (debounce %dms)",
            sections, reason, project_id, self.DEBOUNCE_MS,
        )

        # Restart debounce timer
        self._timer.start(self.DEBOUNCE_MS)

    def on_layout_activated(self, layout_id: str):
        """
        Called by LayoutManager when a layout becomes visible.

        Processes any pending refreshes that were deferred while the
        layout was hidden.
        """
        logger.debug("[UIRefreshMediator] Layout activated: %s", layout_id)
        # Flush immediately (timer may not be running)
        if self._pending:
            self._flush()

    # ── Internal ─────────────────────────────────────────────

    def _flush(self):
        """Execute all pending refreshes that are safe to run now."""
        self._timer.stop()
        pending = dict(self._pending)
        self._pending.clear()

        for project_id, sections in pending.items():
            self._do_refresh(sections, project_id)

    def _do_refresh(self, sections: Set[str], project_id: int):
        """
        Perform the actual refresh for *sections* in whichever layout is active.

        Visibility-safe: if the target widget isn't visible, stores as pending.
        """
        lm = getattr(self.main, "layout_manager", None)
        if not lm:
            logger.debug("[UIRefreshMediator] No layout_manager, skipping")
            return

        layout_id = lm._current_layout_id
        layout = lm._current_layout

        if layout_id == "google" and layout:
            self._refresh_google(layout, sections, project_id)
        else:
            self._refresh_current(sections, project_id)

    def _refresh_google(self, layout, sections: Set[str], project_id: int):
        """Refresh sections in GooglePhotosLayout."""
        accordion = getattr(layout, "accordion_sidebar", None)
        if not accordion:
            logger.debug("[UIRefreshMediator] No accordion_sidebar on google layout")
            return

        try:
            # Check disposal flag first (reliable), then visibility
            if getattr(accordion, '_disposed', False):
                logger.debug("[UIRefreshMediator] Accordion disposed, skipping refresh")
                return
            if not accordion.isVisible():
                logger.debug("[UIRefreshMediator] Accordion not visible, deferring refresh")
                existing = self._pending.get(project_id, set())
                existing.update(sections)
                self._pending[project_id] = existing
                return

            for section in sections:
                if section == "people":
                    if hasattr(accordion, "reload_people_section"):
                        accordion.reload_people_section()
                    if hasattr(layout, "_build_people_tree"):
                        layout._build_people_tree()
                    logger.info("[UIRefreshMediator] Refreshed people (google, pid=%d)", project_id)
                elif section == "duplicates":
                    if hasattr(accordion, "reload_section"):
                        accordion.reload_section("duplicates")
                    logger.info("[UIRefreshMediator] Refreshed duplicates (google, pid=%d)", project_id)
                else:
                    if hasattr(accordion, "reload_section"):
                        accordion.reload_section(section)
        except RuntimeError:
            logger.debug("[UIRefreshMediator] Widget deleted during google refresh")
        except Exception as e:
            logger.warning("[UIRefreshMediator] Google refresh error: %s", e)

    def _refresh_current(self, sections: Set[str], project_id: int):
        """Refresh sections in CurrentLayout (SidebarQt)."""
        sidebar = getattr(self.main, "sidebar", None)
        if not sidebar:
            return

        try:
            if getattr(sidebar, '_disposed', False):
                logger.debug("[UIRefreshMediator] Sidebar disposed, skipping refresh")
                return
            if not sidebar.isVisible():
                logger.debug("[UIRefreshMediator] Sidebar not visible, deferring refresh")
                existing = self._pending.get(project_id, set())
                existing.update(sections)
                self._pending[project_id] = existing
                return

            # SidebarQt doesn't have per-section reload, do full reload
            if hasattr(sidebar, "reload"):
                sidebar.reload()
                logger.info("[UIRefreshMediator] Refreshed sidebar (current, pid=%d)", project_id)
        except RuntimeError:
            logger.debug("[UIRefreshMediator] Sidebar widget deleted during refresh")
        except Exception as e:
            logger.warning("[UIRefreshMediator] Current refresh error: %s", e)

    def _current_project_id(self) -> Optional[int]:
        """Best-effort project_id from window or default."""
        for attr in ("current_project_id", "_current_project_id", "project_id"):
            pid = getattr(self.main, attr, None)
            if pid:
                return pid

        store = getattr(self.main, "store", None)
        if store:
            try:
                return store.get_state().get("project_id")
            except:
                pass

        try:
            from app_services import get_default_project_id
            return get_default_project_id()
        except Exception:
            return None
