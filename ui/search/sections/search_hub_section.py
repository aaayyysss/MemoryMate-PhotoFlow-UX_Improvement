from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QListWidget, QListWidgetItem, QPushButton, QLabel, QHBoxLayout


class SearchHubSection(QGroupBox):
    recentSearchClicked = Signal(str)
    suggestionClicked = Signal(str)
    clearRecentRequested = Signal()

    def __init__(self, parent=None):
        super().__init__("Search Hub", parent)
        self.setObjectName("SearchHubSection")

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(8)

        # Recent Searches Header
        h_recent = QHBoxLayout()
        self.lbl_recent = QLabel("<b>Recent Searches</b>")
        self.btn_clear_recent = QPushButton("Clear")
        self.btn_clear_recent.setObjectName("SearchHubClearButton")
        self.btn_clear_recent.setFixedWidth(50)
        self.btn_clear_recent.setStyleSheet("QPushButton { border: none; color: #1a73e8; text-decoration: underline; }")
        h_recent.addWidget(self.lbl_recent)
        h_recent.addStretch()
        h_recent.addWidget(self.btn_clear_recent)
        self.layout.addLayout(h_recent)

        self.list_recent = QListWidget()
        self.list_recent.setMaximumHeight(120)
        self.list_recent.setStyleSheet("QListWidget { border: none; background: transparent; } QListWidget::item { padding: 4px; }")
        self.layout.addWidget(self.list_recent)

        # Suggestions Header
        self.lbl_suggestions = QLabel("<b>Suggestions</b>")
        self.layout.addWidget(self.lbl_suggestions)

        self.list_suggestions = QListWidget()
        self.list_suggestions.setMaximumHeight(180)
        self.list_suggestions.setStyleSheet("QListWidget { border: none; background: transparent; } QListWidget::item { padding: 4px; color: #5f6368; }")
        self.layout.addWidget(self.list_suggestions)

        self.layout.addStretch(1)

        # Signal connections
        self.list_recent.itemClicked.connect(self._on_recent_clicked)
        self.list_suggestions.itemClicked.connect(self._on_suggestion_clicked)
        self.btn_clear_recent.clicked.connect(self.clearRecentRequested.emit)

    def _on_recent_clicked(self, item: QListWidgetItem):
        clean_text = item.data(Qt.UserRole) or item.text().strip()
        self.recentSearchClicked.emit(str(clean_text))

    def _on_suggestion_clicked(self, item: QListWidgetItem):
        clean_text = item.data(Qt.UserRole) or item.text().strip()
        self.suggestionClicked.emit(str(clean_text))

    def set_recent_queries(self, queries):
        self.list_recent.clear()
        for q in list(queries or [])[:10]:
            item = QListWidgetItem(f"🕒 {q}")
            item.setData(Qt.UserRole, q)
            self.list_recent.addItem(item)

        visible = self.list_recent.count() > 0
        self.lbl_recent.setVisible(visible)
        self.list_recent.setVisible(visible)
        self.btn_clear_recent.setVisible(visible)

    def set_suggestions(self, suggestions):
        self.list_suggestions.clear()
        for s in list(suggestions or [])[:12]:
            item = QListWidgetItem(f"💡 {s}")
            item.setData(Qt.UserRole, s)
            self.list_suggestions.addItem(item)

        visible = self.list_suggestions.count() > 0
        self.lbl_suggestions.setVisible(visible)
        self.list_suggestions.setVisible(visible)

    def set_enabled_for_project(self, enabled: bool):
        self.setEnabled(enabled)
        if not enabled:
            self.list_recent.clear()
            self.list_suggestions.clear()
