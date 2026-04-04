from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QPushButton


class BrowseSection(QGroupBox):
    browseNodeSelected = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__("Browse", parent)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(6)

        self.btn_all = QPushButton("All Photos")
        self.btn_favorites = QPushButton("Favorites")
        self.btn_videos = QPushButton("Videos")
        self.btn_location = QPushButton("With Location")
        self.btn_albums = QPushButton("Albums")
        self.btn_folders = QPushButton("Folders")
        self.btn_dates = QPushButton("Dates")

        self.layout.addWidget(self.btn_all)
        self.layout.addWidget(self.btn_favorites)
        self.layout.addWidget(self.btn_videos)
        self.layout.addWidget(self.btn_location)
        self.layout.addWidget(self.btn_albums)
        self.layout.addWidget(self.btn_folders)
        self.layout.addWidget(self.btn_dates)
        self.layout.addStretch(1)

        self.btn_all.clicked.connect(lambda: self.browseNodeSelected.emit("all_photos", None))
        self.btn_favorites.clicked.connect(lambda: self.browseNodeSelected.emit("favorites", True))
        self.btn_videos.clicked.connect(lambda: self.browseNodeSelected.emit("videos", True))
        self.btn_location.clicked.connect(lambda: self.browseNodeSelected.emit("with_location", True))
        self.btn_albums.clicked.connect(lambda: self.browseNodeSelected.emit("albums", None))
        self.btn_folders.clicked.connect(lambda: self.browseNodeSelected.emit("folders", None))
        self.btn_dates.clicked.connect(lambda: self.browseNodeSelected.emit("dates", None))

    def set_enabled_for_project(self, enabled: bool):
        self.setEnabled(enabled)

    def set_active_mode(self, browse_key):
        mapping = {
            "all_photos": self.btn_all,
            "favorites": self.btn_favorites,
            "videos": self.btn_videos,
            "with_location": self.btn_location,
            "albums": self.btn_albums,
            "folders": self.btn_folders,
            "dates": self.btn_dates,
        }

        for key, btn in mapping.items():
            if key == browse_key:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #d2e3fc;
                        border: 1px solid #8ab4f8;
                        border-radius: 6px;
                        padding: 6px 10px;
                        font-weight: 600;
                    }
                """)
            else:
                btn.setStyleSheet("")
