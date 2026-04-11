# ui/search/google_shell_sidebar.py
# Phase 6A: Visual polish pass on the passive shell sidebar
# Visual-only — legacy accordion remains the action owner

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy, QTreeWidget, QTreeWidgetItem,
    QLineEdit
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
    Phase 9 shell sidebar.

    Shell is now the preferred visible interaction surface for stable,
    retired sections. Legacy accordion remains visible and alive as
    fallback, while shell clicks should produce direct, obvious outcomes
    for retired sections such as Find, Videos, Locations, Duplicates,
    and Devices.
    """

    selectBranch = Signal(str)
    openActivityCenterRequested = Signal()
    disabledBranchRequested = Signal(str)
    # Phase 10C fix pack v3: shell-native search input
    searchQuerySubmitted = Signal(str)  # emitted when user types and submits a query

    def __init__(self, parent=None):
        super().__init__(parent)
        self._branch_buttons = {}
        self._active_branch = None
        self._project_available = False
        self._retired_legacy_sections = set()
        self._shell_status_label = None
        self._shell_state_text = ""
        self._date_tree = None
        self._folder_tree = None
        self._location_tree = None
        self._video_filter_buttons = {}
        self._review_buttons = {}
        self._project_required_branches = {
            "all",
            "dates",
            "today",
            "yesterday",
            "last_7_days",
            "last_30_days",
            "this_month",
            "last_month",
            "this_year",
            "last_year",
            "folders",
            "devices",
            "favorites",
            "videos",
            "documents",
            "screenshots",
            "duplicates",
            "locations",
            "discover_beach",
            "discover_mountains",
            "discover_city",
            "find",
            "people_merge_review",
            "people_unnamed",
            "people_show_all",
            "filter_photos_only",
            "filter_favorites",
            "filter_documents",
            "filter_screenshots",
        }
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
        self.search_hub.add_widget(self._hint("Type a query or open full search"))
        self.search_hub.add_widget(self._status("No active shell result"))
        # Inline search field (legacy parity with Find section's text input)
        self._search_input = QLineEdit()
        self._search_input.setObjectName("ShellSearchInput")
        self._search_input.setPlaceholderText("Search your library...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.returnPressed.connect(self._on_search_submitted)
        self.search_hub.add_widget(self._search_input)
        self.search_hub.add_widget(self._nav("Open Full Search", "find"))

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

        self._dates_subhead = self._subhead("Dates")
        self.browse.add_widget(self._dates_subhead)

        self._date_tree = QTreeWidget()
        self._date_tree.setHeaderHidden(True)
        self._date_tree.setRootIsDecorated(True)
        self._date_tree.setIndentation(14)
        self._date_tree.setObjectName("ShellTree")
        self._date_tree.itemClicked.connect(self._on_date_tree_item_clicked)
        self.browse.add_widget(self._date_tree)

        self.browse.add_widget(self._subhead("Sources"))
        self.browse.add_widget(self._nav("Folders", "folders"))

        self._folders_subhead = self._subhead("Folder Tree")
        self.browse.add_widget(self._folders_subhead)

        self._folder_tree = QTreeWidget()
        self._folder_tree.setHeaderHidden(True)
        self._folder_tree.setRootIsDecorated(True)
        self._folder_tree.setIndentation(14)
        self._folder_tree.setObjectName("ShellTree")
        self._folder_tree.itemClicked.connect(self._on_folder_tree_item_clicked)
        self.browse.add_widget(self._folder_tree)

        self.browse.add_widget(self._nav("Devices", "devices"))

        self.browse.add_widget(self._subhead("Collections"))
        self.browse.add_widget(self._nav("Favorites", "favorites"))
        self.browse.add_widget(self._nav("Documents", "documents"))
        self.browse.add_widget(self._nav("Screenshots", "screenshots"))

        self.browse.add_widget(self._subhead("Places"))
        self.browse.add_widget(self._nav("Locations", "locations"))

        self._locations_subhead = self._subhead("Locations")
        self.browse.add_widget(self._locations_subhead)

        self._location_tree = QTreeWidget()
        self._location_tree.setHeaderHidden(True)
        self._location_tree.setRootIsDecorated(False)
        self._location_tree.setIndentation(14)
        self._location_tree.setObjectName("ShellTree")
        self._location_tree.itemClicked.connect(self._on_location_tree_item_clicked)
        self.browse.add_widget(self._location_tree)

        self.browse.add_widget(self._subhead("Quick Dates"))
        self.browse.add_widget(self._nav("Today", "today"))
        self.browse.add_widget(self._nav("Yesterday", "yesterday"))
        self.browse.add_widget(self._nav("Last 7 days", "last_7_days"))
        self.browse.add_widget(self._nav("Last 30 days", "last_30_days"))
        self.browse.add_widget(self._nav("This month", "this_month"))
        self.browse.add_widget(self._nav("Last month", "last_month"))
        self.browse.add_widget(self._nav("This year", "this_year"))
        self.browse.add_widget(self._nav("Last year", "last_year"))

        # ── Videos ───────────────────────────────────────────────
        self.videos = _ShellSection("Videos", expanded=False)
        self.videos.add_widget(self._hint("Filter by type, duration, quality"))

        self._video_filter_buttons["videos_all"] = self._nav("All Videos", "videos")
        self.videos.add_widget(self._video_filter_buttons["videos_all"])

        self.videos.add_widget(self._subhead("Duration"))
        self._video_filter_buttons["videos_duration_short"] = self._nav("Short (< 30s)", "videos_duration_short")
        self._video_filter_buttons["videos_duration_medium"] = self._nav("Medium (30s - 5m)", "videos_duration_medium")
        self._video_filter_buttons["videos_duration_long"] = self._nav("Long (> 5m)", "videos_duration_long")
        self.videos.add_widget(self._video_filter_buttons["videos_duration_short"])
        self.videos.add_widget(self._video_filter_buttons["videos_duration_medium"])
        self.videos.add_widget(self._video_filter_buttons["videos_duration_long"])

        self.videos.add_widget(self._subhead("Resolution"))
        self._video_filter_buttons["videos_resolution_sd"] = self._nav("SD (< 720p)", "videos_resolution_sd")
        self._video_filter_buttons["videos_resolution_hd"] = self._nav("HD (720p+)", "videos_resolution_hd")
        self._video_filter_buttons["videos_resolution_fhd"] = self._nav("Full HD (1080p+)", "videos_resolution_fhd")
        self._video_filter_buttons["videos_resolution_4k"] = self._nav("4K (2160p+)", "videos_resolution_4k")
        self.videos.add_widget(self._video_filter_buttons["videos_resolution_sd"])
        self.videos.add_widget(self._video_filter_buttons["videos_resolution_hd"])
        self.videos.add_widget(self._video_filter_buttons["videos_resolution_fhd"])
        self.videos.add_widget(self._video_filter_buttons["videos_resolution_4k"])

        self.videos.add_widget(self._subhead("Codec"))
        self._video_filter_buttons["videos_codec_h264"] = self._nav("H.264 / AVC", "videos_codec_h264")
        self._video_filter_buttons["videos_codec_hevc"] = self._nav("H.265 / HEVC", "videos_codec_hevc")
        self._video_filter_buttons["videos_codec_vp9"] = self._nav("VP9", "videos_codec_vp9")
        self._video_filter_buttons["videos_codec_av1"] = self._nav("AV1", "videos_codec_av1")
        self._video_filter_buttons["videos_codec_mpeg4"] = self._nav("MPEG-4", "videos_codec_mpeg4")
        self.videos.add_widget(self._video_filter_buttons["videos_codec_h264"])
        self.videos.add_widget(self._video_filter_buttons["videos_codec_hevc"])
        self.videos.add_widget(self._video_filter_buttons["videos_codec_vp9"])
        self.videos.add_widget(self._video_filter_buttons["videos_codec_av1"])
        self.videos.add_widget(self._video_filter_buttons["videos_codec_mpeg4"])

        self.videos.add_widget(self._subhead("File Size"))
        self._video_filter_buttons["videos_size_small"] = self._nav("Small (< 100 MB)", "videos_size_small")
        self._video_filter_buttons["videos_size_medium"] = self._nav("Medium (100 MB - 1 GB)", "videos_size_medium")
        self._video_filter_buttons["videos_size_large"] = self._nav("Large (1 - 5 GB)", "videos_size_large")
        self._video_filter_buttons["videos_size_xlarge"] = self._nav("Extra Large (> 5 GB)", "videos_size_xlarge")
        self.videos.add_widget(self._video_filter_buttons["videos_size_small"])
        self.videos.add_widget(self._video_filter_buttons["videos_size_medium"])
        self.videos.add_widget(self._video_filter_buttons["videos_size_large"])
        self.videos.add_widget(self._video_filter_buttons["videos_size_xlarge"])

        # ── Review ───────────────────────────────────────────────
        self.review = _ShellSection("Review", expanded=False)
        self.review.add_widget(self._hint("Cleanup, dedup, and quality review"))

        self._review_buttons["review_duplicates"] = self._nav("Duplicates", "duplicates")
        self._review_buttons["review_similar"] = self._nav("Similar Shots", "similar_shots")

        self.review.add_widget(self._review_buttons["review_duplicates"])
        self.review.add_widget(self._review_buttons["review_similar"])

        # ── Filters ───────────────────────────────────────────────
        # Phase 10C fix pack v3: fill previously empty Filters section with
        # concrete media-type and collection shortcuts (legacy parity).
        self.filters = _ShellSection("Filters", expanded=False)
        self.filters.add_widget(self._hint("Media type, collections, favorites"))

        self.filters.add_widget(self._subhead("Media Type"))
        self.filters.add_widget(self._nav("All Media", "all"))
        self.filters.add_widget(self._nav("Photos Only", "filter_photos_only"))
        self.filters.add_widget(self._nav("Videos Only", "videos"))

        self.filters.add_widget(self._subhead("Collections"))
        self.filters.add_widget(self._nav("Favorites", "filter_favorites"))
        self.filters.add_widget(self._nav("Documents", "filter_documents"))
        self.filters.add_widget(self._nav("Screenshots", "filter_screenshots"))

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
            self.videos,
            self.review,
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

    def _status(self, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("ShellStatus")
        lbl.setWordWrap(True)
        self._shell_status_label = lbl
        return lbl

    def _subhead(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("ShellSubhead")
        return lbl

    def _nav(self, label: str, branch: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ShellNavBtn")
        btn.setProperty("active", False)
        btn.setProperty("disabledShell", False)
        btn.clicked.connect(lambda _, b=branch: self._emit_branch(b))
        self._branch_buttons[branch] = btn
        return btn

    def _emit_branch(self, branch: str):
        if not self._project_available and branch in self._project_required_branches:
            self.disabledBranchRequested.emit(branch)
            return
        self.selectBranch.emit(branch)

    def _on_search_submitted(self):
        """Emit searchQuerySubmitted when the inline search field is submitted."""
        try:
            if not self._project_available:
                self.disabledBranchRequested.emit("find")
                return
            text = (self._search_input.text() or "").strip()
            if not text:
                return
            self.searchQuerySubmitted.emit(text)
        except Exception:
            pass

    def set_search_query(self, text: str):
        """Programmatically set the inline search field's text (e.g. from presets)."""
        try:
            if hasattr(self, "_search_input") and self._search_input is not None:
                self._search_input.setText(text or "")
        except Exception:
            pass

    def set_active_branch(self, branch: str | None):
        self._active_branch = branch

        for key, btn in self._branch_buttons.items():
            btn.setProperty("active", key == branch)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

    def clear_active_branch(self):
        self.set_active_branch(None)

    def set_project_available(self, available: bool):
        self._project_available = bool(available)

        for branch, btn in self._branch_buttons.items():
            disabled_shell = (not self._project_available and branch in self._project_required_branches)
            btn.setProperty("disabledShell", disabled_shell)
            btn.setEnabled(True)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

    def set_retired_legacy_sections(self, sections):
        self._retired_legacy_sections = set(sections or [])

    def is_legacy_section_retired(self, section_name: str) -> bool:
        return section_name in self._retired_legacy_sections

    def set_shell_state_text(self, text: str):
        self._shell_state_text = text or ""
        if self._shell_status_label is not None:
            self._shell_status_label.setText(self._shell_state_text)

    def clear_shell_state_text(self):
        self.set_shell_state_text("No active shell result")

    def set_date_years(self, years_with_counts):
        """Rebuild Dates Overview from actual project data (legacy compat)."""
        # Delegate to set_date_tree with simple payload
        if years_with_counts:
            payload = [{"label": f"{y} ({c})" if c else str(y), "value": str(y)} for y, c in years_with_counts]
            self.set_date_tree(payload)

    def set_date_tree(self, years_payload):
        if self._date_tree is None:
            return
        self._date_tree.clear()

        for year_item in years_payload or []:
            year_label = str(year_item.get("label", ""))
            year_value = str(year_item.get("value", ""))
            months = year_item.get("months", []) or []

            year_node = QTreeWidgetItem([year_label])
            year_node.setData(0, Qt.UserRole, ("date_year", year_value))
            self._date_tree.addTopLevelItem(year_node)

            for month_item in months:
                month_label = str(month_item.get("label", ""))
                month_value = str(month_item.get("value", ""))
                month_node = QTreeWidgetItem([month_label])
                month_node.setData(0, Qt.UserRole, ("date_month", month_value))
                year_node.addChild(month_node)

    def set_folder_tree(self, folder_payload):
        if self._folder_tree is None:
            return
        self._folder_tree.clear()

        def add_nodes(parent_widget, items):
            for item in items or []:
                node = QTreeWidgetItem([str(item.get("label", ""))])
                node.setData(0, Qt.UserRole, ("folder", item.get("id")))
                parent_widget.addChild(node)
                add_nodes(node, item.get("children", []))

        for top in folder_payload or []:
            node = QTreeWidgetItem([str(top.get("label", ""))])
            node.setData(0, Qt.UserRole, ("folder", top.get("id")))
            self._folder_tree.addTopLevelItem(node)
            add_nodes(node, top.get("children", []))

    def set_location_tree(self, location_payload):
        if self._location_tree is None:
            return
        self._location_tree.clear()

        for item in location_payload or []:
            node = QTreeWidgetItem([str(item.get("label", ""))])
            node.setData(0, Qt.UserRole, ("location", item.get("value")))
            self._location_tree.addTopLevelItem(node)

    def _on_date_tree_item_clicked(self, item, _column):
        payload = item.data(0, Qt.UserRole)
        if not payload:
            return
        kind, value = payload
        if kind == "date_year":
            self.selectBranch.emit(f"year_{value}")
        elif kind == "date_month":
            self.selectBranch.emit(f"month_{value}")

    def _on_folder_tree_item_clicked(self, item, _column):
        payload = item.data(0, Qt.UserRole)
        if not payload:
            return
        kind, value = payload
        if kind == "folder":
            self.selectBranch.emit(f"folder_id:{value}")

    def _on_location_tree_item_clicked(self, item, _column):
        payload = item.data(0, Qt.UserRole)
        if not payload:
            return
        kind, value = payload
        if kind == "location":
            self.selectBranch.emit(f"location_name:{value}")

    def set_legacy_emphasis(self, emphasized: bool):
        self.setProperty("legacySoft", not emphasized)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


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
QLabel#ShellStatus {
    color: #1a73e8;
    background: #eef3ff;
    border: 1px solid #d2e3fc;
    border-radius: 8px;
    font-size: 11px;
    padding: 6px 8px;
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
QPushButton#ShellNavBtn[disabledShell="true"] {
    color: #a0a4ab;
}
QPushButton#ShellNavBtn[disabledShell="true"]:hover {
    background: #f8f9fb;
}
QTreeWidget#ShellTree {
    border: none;
    background: transparent;
    outline: none;
    font-size: 11px;
    color: #202124;
}
QTreeWidget#ShellTree::item {
    padding: 4px 6px;
    border-radius: 6px;
}
QTreeWidget#ShellTree::item:hover {
    background: #eef3ff;
}
QTreeWidget#ShellTree::item:selected {
    background: #e8f0fe;
    color: #1a73e8;
}
"""
