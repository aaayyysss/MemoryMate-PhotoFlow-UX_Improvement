from PySide6.QtWidgets import QWidget, QGridLayout, QLabel
from PySide6.QtCore import QTimer


class SkeletonGrid(QWidget):
    def __init__(self, parent=None, columns=6, rows=3):
        super().__init__(parent)

        self.layout = QGridLayout(self)
        self.layout.setSpacing(8)

        self.cells = []

        for r in range(rows):
            for c in range(columns):
                cell = QLabel()
                cell.setFixedSize(120, 120)
                cell.setStyleSheet("""
                    background-color: #e0e0e0;
                    border-radius: 8px;
                """)
                self.layout.addWidget(cell, r, c)
                self.cells.append(cell)

        self._pulse = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(600)

    def _animate(self):
        self._pulse = not self._pulse
        color = "#e0e0e0" if self._pulse else "#f0f0f0"

        for cell in self.cells:
            cell.setStyleSheet(f"""
                background-color: {color};
                border-radius: 8px;
            """)
