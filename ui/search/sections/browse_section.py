from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QFrame, QToolButton


class _ExpandableSubsection(QFrame):
    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setText(title)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(expanded)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle_btn.clicked.connect(self._on_toggled)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 2, 0, 2)
        self.content_layout.setSpacing(2)
        self.content.setVisible(expanded)

        root.addWidget(self.toggle_btn)
        root.addWidget(self.content)

    def _on_toggled(self):
        expanded = self.toggle_btn.isChecked()
        self.toggle_btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)

    def addWidget(self, widget: QWidget):
        self.content_layout.addWidget(widget)


class BrowseSection(QWidget):
    browseNodeSelected = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        intro = QLabel("Library, sources, collections")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.library_group = _ExpandableSubsection("Library", expanded=True)
        self.sources_group = _ExpandableSubsection("Sources", expanded=True)
        self.collections_group = _ExpandableSubsection("Collections", expanded=False)
        self.places_group = _ExpandableSubsection("Places", expanded=False)
        self.quick_group = _ExpandableSubsection("Quick Access", expanded=False)

        # Library
        self.btn_all = self._make_button("All Photos", "all")
        self.btn_years = self._make_button("Years", "years")
        self.btn_months = self._make_button("Months", "months")
        self.btn_days = self._make_button("Days", "days")
        self.library_group.addWidget(self.btn_all)
        self.library_group.addWidget(self.btn_years)
        self.library_group.addWidget(self.btn_months)
        self.library_group.addWidget(self.btn_days)

        # Sources
        self.btn_folders = self._make_button("Folders", "folders")
        self.btn_devices = self._make_button("Devices", "devices")
        self.sources_group.addWidget(self.btn_folders)
        self.sources_group.addWidget(self.btn_devices)

        # Collections
        self.btn_favorites = self._make_button("Favorites", "favorites")
        self.btn_videos = self._make_button("Videos", "videos")
        self.btn_documents = self._make_button("Documents", "documents")
        self.btn_screenshots = self._make_button("Screenshots", "screenshots")
        self.btn_duplicates = self._make_button("Duplicates", "duplicates")
        self.collections_group.addWidget(self.btn_favorites)
        self.collections_group.addWidget(self.btn_videos)
        self.collections_group.addWidget(self.btn_documents)
        self.collections_group.addWidget(self.btn_screenshots)
        self.collections_group.addWidget(self.btn_duplicates)

        # Places
        self.btn_locations = self._make_button("Locations", "locations")
        self.places_group.addWidget(self.btn_locations)

        # Quick Access
        self.btn_today = self._make_button("Today", "today")
        self.btn_yesterday = self._make_button("Yesterday", "yesterday")
        self.btn_last7 = self._make_button("Last 7 days", "last_7_days")
        self.btn_last30 = self._make_button("Last 30 days", "last_30_days")
        self.btn_this_month = self._make_button("This month", "this_month")
        self.btn_last_month = self._make_button("Last month", "last_month")
        self.btn_this_year = self._make_button("This year", "this_year")
        self.btn_last_year = self._make_button("Last year", "last_year")
        self.quick_group.addWidget(self.btn_today)
        self.quick_group.addWidget(self.btn_yesterday)
        self.quick_group.addWidget(self.btn_last7)
        self.quick_group.addWidget(self.btn_last30)
        self.quick_group.addWidget(self.btn_this_month)
        self.quick_group.addWidget(self.btn_last_month)
        self.quick_group.addWidget(self.btn_this_year)
        self.quick_group.addWidget(self.btn_last_year)

        root.addWidget(self.library_group)
        root.addWidget(self.sources_group)
        root.addWidget(self.collections_group)
        root.addWidget(self.places_group)
        root.addWidget(self.quick_group)

    def _make_button(self, text: str, key: str) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(lambda: self.browseNodeSelected.emit(key, None))
        return btn

    def set_counts(self, counts: dict | None):
        self._counts = counts or {}

        def fmt(label: str, key: str) -> str:
            val = self._counts.get(key)
            return f"{label} ({val})" if val is not None else label

        self.btn_all.setText(fmt("All Photos", "all"))
        self.btn_years.setText(fmt("Years", "years"))
        self.btn_months.setText(fmt("Months", "months"))
        self.btn_days.setText(fmt("Days", "days"))
        self.btn_folders.setText(fmt("Folders", "folders"))
        self.btn_devices.setText(fmt("Devices", "devices"))
        self.btn_favorites.setText(fmt("Favorites", "favorites"))
        self.btn_videos.setText(fmt("Videos", "videos"))
        self.btn_documents.setText(fmt("Documents", "documents"))
        self.btn_screenshots.setText(fmt("Screenshots", "screenshots"))
        self.btn_duplicates.setText(fmt("Duplicates", "duplicates"))
        self.btn_locations.setText(fmt("Locations", "locations"))
