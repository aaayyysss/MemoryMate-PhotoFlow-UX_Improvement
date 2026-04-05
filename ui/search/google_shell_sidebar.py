# ui/search/google_shell_sidebar.py
# Phase 6A: Visual polish pass on the passive shell sidebar
# Visual-only — legacy accordion remains the action owner

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy,
)


class _ShellSection(QFrame):
    """Collapsible card-style section for the shell sidebar."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        self.setObjectName("ShellSection")
        self.setFrameShape(QFrame.NoFrame)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header_btn = QPushButton(title)
        self.header_btn.setObjectName("ShellSectionHeader")
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(expanded)
        self.header_btn.clicked.connect(self._toggle)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 0, 10, 8)
        self.body_layout.setSpacing(4)
        self.body.setVisible(expanded)

        root.addWidget(self.header_btn)
        root.addWidget(self.body)

    def _toggle(self):
        self._expanded = self.header_btn.isChecked()
        self.body.setVisible(self._expanded)

    def add_widget(self, w: QWidget):
        self.body_layout.addWidget(w)


class GoogleShellSidebar(QWidget):
    """
    Phase 6A passive shell sidebar.

    Displays the future navigation structure above the legacy accordion.
    Clicks emit selectBranch / openActivityCenterRequested but do NOT
    own any routing — the layout's passive handler bridges to the
    legacy accordion.
    """

    selectBranch = Signal(str)
    openActivityCenterRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._branch_buttons = {}
        self._active_branch = None
        self.setObjectName("GoogleShellSidebar")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # ── Search Hub ────────────────────────────────────────────
        self.search_hub = _ShellSection("Search Hub", expanded=True)
        self.search_hub.add_widget(self._hint("Search, recent searches, scopes"))
        self.search_hub.add_widget(self._nav("Open Search", "find"))

        # ── Discover ──────────────────────────────────────────────
        self.discover = _ShellSection("Discover", expanded=True)
        self.discover.add_widget(self._hint("Smart Find, scenes, presets"))
        self.discover.add_widget(self._nav("Beach", "discover_beach"))
        self.discover.add_widget(self._nav("Mountains", "discover_mountains"))
        self.discover.add_widget(self._nav("City", "discover_city"))

        # ── People ────────────────────────────────────────────────
        self.people = _ShellSection("People", expanded=False)
        self.people.add_widget(self._hint("Top people and review tools"))
        self.people.add_widget(self._nav("Review Possible Merges", "people_merge_review"))
        self.people.add_widget(self._nav("Show Unnamed Clusters", "people_unnamed"))
        self.people.add_widget(self._nav("Show All People", "people_show_all"))

        # ── Browse ────────────────────────────────────────────────
        self.browse = _ShellSection("Browse", expanded=True)
        self.browse.add_widget(self._hint("Library, sources, collections"))

        self.browse.add_widget(self._subhead("Library"))
        self.browse.add_widget(self._nav("All Photos", "all"))
        self.browse.add_widget(self._nav("Dates", "dates"))

        self.browse.add_widget(self._subhead("Sources"))
        self.browse.add_widget(self._nav("Folders", "folders"))
        self.browse.add_widget(self._nav("Devices", "devices"))

        self.browse.add_widget(self._subhead("Collections"))
        self.browse.add_widget(self._nav("Favorites", "favorites"))
        self.browse.add_widget(self._nav("Videos", "videos"))
        self.browse.add_widget(self._nav("Documents", "documents"))
        self.browse.add_widget(self._nav("Screenshots", "screenshots"))
        self.browse.add_widget(self._nav("Duplicates", "duplicates"))

        self.browse.add_widget(self._subhead("Places"))
        self.browse.add_widget(self._nav("Locations", "locations"))

        self.browse.add_widget(self._subhead("Quick Dates"))
        self.browse.add_widget(self._nav("Today", "today"))
        self.browse.add_widget(self._nav("Yesterday", "yesterday"))
        self.browse.add_widget(self._nav("Last 7 days", "last_7_days"))
        self.browse.add_widget(self._nav("Last 30 days", "last_30_days"))
        self.browse.add_widget(self._nav("This month", "this_month"))
        self.browse.add_widget(self._nav("Last month", "last_month"))
        self.browse.add_widget(self._nav("This year", "this_year"))
        self.browse.add_widget(self._nav("Last year", "last_year"))

        # ── Filters ───────────────────────────────────────────────
        self.filters = _ShellSection("Filters", expanded=False)
        self.filters.add_widget(self._hint("People, dates, types, favorites"))

        # ── Activity ──────────────────────────────────────────────
        self.activity = _ShellSection("Activity", expanded=False)
        self.activity.add_widget(self._hint("Jobs, indexing, AI processing"))
        btn_act = QPushButton("Open Activity Center")
        btn_act.setObjectName("ShellNavBtn")
        btn_act.clicked.connect(self.openActivityCenterRequested.emit)
        self.activity.add_widget(btn_act)

        for section in (
            self.search_hub,
            self.discover,
            self.people,
            self.browse,
            self.filters,
            self.activity,
        ):
            lay.addWidget(section)

        lay.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.setStyleSheet(_SHELL_STYLE)

    # ── helpers ───────────────────────────────────────────────────

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("ShellHint")
        lbl.setWordWrap(True)
        return lbl

    def _subhead(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("ShellSubhead")
        return lbl

    def _nav(self, label: str, branch: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ShellNavBtn")
        btn.setProperty("active", False)
        btn.clicked.connect(lambda _, b=branch: self.selectBranch.emit(b))
        self._branch_buttons[branch] = btn
        return btn

    def set_active_branch(self, branch: str | None):
        self._active_branch = branch

        for key, btn in self._branch_buttons.items():
            btn.setProperty("active", key == branch)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

    def clear_active_branch(self):
        self.set_active_branch(None)


_SHELL_STYLE = """
QWidget#GoogleShellSidebar {
    background: transparent;
}
QScrollArea {
    background: transparent;
    border: none;
}
QFrame#ShellSection {
    background: #ffffff;
    border: 1px solid #e7eaee;
    border-radius: 12px;
}
QPushButton#ShellSectionHeader {
    text-align: left;
    font-weight: 600;
    font-size: 13px;
    color: #202124;
    border: none;
    background: transparent;
    padding: 9px 12px 7px 12px;
}
QPushButton#ShellSectionHeader:hover {
    background: #f6f8fb;
    border-radius: 12px;
}
QPushButton#ShellSectionHeader:checked {
    color: #1a73e8;
}
QLabel#ShellHint {
    color: #80868b;
    font-size: 11px;
    padding: 0 2px 4px 2px;
}
QLabel#ShellSubhead {
    color: #5f6368;
    font-size: 11px;
    font-weight: 600;
    padding: 6px 2px 2px 2px;
}
QPushButton#ShellNavBtn {
    text-align: left;
    font-size: 12px;
    color: #202124;
    border: none;
    background: transparent;
    padding: 6px 8px;
    border-radius: 8px;
}
QPushButton#ShellNavBtn:hover {
    background: #eef3ff;
}
QPushButton#ShellNavBtn[active="true"] {
    background: #e8f0fe;
    color: #1a73e8;
    font-weight: 600;
}
"""
