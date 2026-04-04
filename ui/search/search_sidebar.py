from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QFrame, QGroupBox, QLabel

from ui.search.sections.browse_section import BrowseSection
from ui.search.sections.discover_section import DiscoverSection
from ui.search.sections.people_quick_section import PeopleQuickSection


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
        self.people_section = PeopleQuickSection()
        self.placeholder_search = self._make_placeholder_group("Search Hub", "UX-2")
        self.placeholder_filters = self._make_placeholder_group("Filters", "UX-3")

        self._build_ui()
        self._wire_signals()

        # React to search state changes
        self.store.stateChanged.connect(self._on_state_changed)

    def _on_state_changed(self, state):
        enabled = state.has_active_project
        self.discover_section.setEnabled(enabled)
        self.browse_section.setEnabled(enabled)
        self.people_section.setEnabled(enabled)
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
        self.content_layout.addWidget(self.people_section)
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

    def set_people_quick_payload(self, payload: dict | None):
        payload = payload or {}

        rows = payload.get("top_people", []) or []
        merge_count = payload.get("merge_candidates", 0) or 0
        unnamed_count = payload.get("unnamed_count", 0) or 0

        if hasattr(self, "people_section"):
            self.people_section.set_people_rows(rows)
            self.people_section.set_counts(merge_count, unnamed_count)
            self.people_section.set_legacy_actions_enabled(True)

    def _wire_signals(self):
        # Browse signals
        self.browse_section.browseNodeSelected.connect(
            lambda key, payload=None: self.selectBranch.emit(key)
        )

        # People signals
        self.people_section.mergeReviewRequested.connect(
            lambda: self.selectBranch.emit("people_merge_review")
        )
        self.people_section.unnamedRequested.connect(
            lambda: self.selectBranch.emit("people_unnamed")
        )
        self.people_section.showAllPeopleRequested.connect(
            lambda: self.selectBranch.emit("people_show_all")
        )
        self.people_section.peopleToolsRequested.connect(
            lambda: self.selectBranch.emit("people_tools")
        )
        self.people_section.mergeHistoryRequested.connect(
            lambda: self.selectBranch.emit("people_merge_history")
        )
        self.people_section.undoMergeRequested.connect(
            lambda: self.selectBranch.emit("people_undo_merge")
        )
        self.people_section.redoMergeRequested.connect(
            lambda: self.selectBranch.emit("people_redo_merge")
        )
        self.people_section.expandPeopleRequested.connect(
            lambda: self.selectBranch.emit("people_expand")
        )
        self.people_section.personRequested.connect(
            lambda person_id: self.selectBranch.emit(f"people_person:{person_id}")
        )

        # Discover signals
        if self.controller:
            self.discover_section.presetSelected.connect(self.controller.set_preset)
