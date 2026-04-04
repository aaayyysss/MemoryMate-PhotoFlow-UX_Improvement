from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class EmptyStateView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.label = QLabel("No results")
        self.label.setWordWrap(True)
        self.label.setObjectName("EmptyStateLabel")

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def set_message(self, text: str):
        self.label.setText(text or "No results")
