from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QFrame, QGroupBox, QLabel

from ui.search.sections.browse_section import BrowseSection
from ui.search.sections.discover_section import DiscoverSection


class SearchSidebar(QWidget):
    # Parity signals for MainWindow/Controller integration
    folderSelected = Signal(int)
    selectBranch = Signal(str)
    selectDate = Signal(str)
    selectVideos = Signal(str)
    selectGroup = Signal(int)

    def __init__(self, store, controller=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.controller = controller

        self.discover_section = DiscoverSection()
        self.browse_section = BrowseSection()
        self.placeholder_search = self._make_placeholder_group("Search Hub", "UX-2")
        self.placeholder_filters = self._make_placeholder_group("Filters", "UX-3")
        self.placeholder_people = self._make_placeholder_group("People", "UX-4")

        self._build_ui()
        self._wire_signals()

        # React to search state changes
        self.store.stateChanged.connect(self._on_state_changed)

    def _on_state_changed(self, state):
        enabled = state.has_active_project
        self.discover_section.setEnabled(enabled)
        self.browse_section.setEnabled(enabled)
        self.placeholder_people.setEnabled(enabled)
        self.placeholder_filters.setEnabled(enabled)

    def _make_placeholder_group(self, title: str, subtitle: str):
        grp = QGroupBox(title)
        lay = QVBoxLayout(grp)
        lay.addWidget(QLabel(f"Coming in {subtitle}"))
        return grp

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QFrame()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(6, 6, 6, 6)
        self.content_layout.setSpacing(10)

        self.content_layout.addWidget(self.placeholder_search)
        self.content_layout.addWidget(self.discover_section)
        self.content_layout.addWidget(self.browse_section)
        self.content_layout.addWidget(self.placeholder_people)
        self.content_layout.addWidget(self.placeholder_filters)
        self.content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def reload_date_tree(self):
        """Parity method for MainWindow deferred init."""
        pass

    def set_project(self, project_id: int):
        """Update sidebar context for new project."""
        pass

    def toggle_fold(self, folded: bool):
        """Handle sidebar collapse/expand."""
        self.setVisible(not folded)

    def _effective_display_mode(self):
        """Parity method for MainWindow."""
        return "list"

    def switch_display_mode(self, mode: str):
        """Parity method for MainWindow."""
        pass

    def set_browse_payload(self, payload: dict | None):
        payload = payload or {}
        counts = payload.get("counts", {}) or {}
        if hasattr(self, "browse_section") and hasattr(self.browse_section, "set_counts"):
            self.browse_section.set_counts(counts)

    def _wire_signals(self):
        self.browse_section.browseNodeSelected.connect(
            lambda key, payload=None: self.selectBranch.emit(key)
        )
        if self.controller:
            self.discover_section.presetSelected.connect(self.controller.set_preset)
