from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton


class TopSearchBar(QWidget):
    querySubmitted = Signal(str)
    queryChanged = Signal(str)
    searchCleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search photos, people, places, screenshots...")
        self.btn_clear = QPushButton("✕")
        self.btn_clear.setFixedWidth(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.search_input, 1)
        layout.addWidget(self.btn_clear)

        self.search_input.returnPressed.connect(self._emit_submit)
        self.search_input.textChanged.connect(self.queryChanged.emit)
        self.btn_clear.clicked.connect(self._clear)

    def _emit_submit(self):
        self.querySubmitted.emit(self.search_input.text().strip())

    def _clear(self):
        self.search_input.clear()
        self.searchCleared.emit()

    def set_query_text(self, text: str):
        if self.search_input.text() != text:
            self.search_input.setText(text)
