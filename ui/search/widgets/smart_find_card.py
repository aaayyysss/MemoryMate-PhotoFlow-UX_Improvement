from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap

class SmartFindCard(QFrame):
    clicked = Signal(str) # preset_id

    def __init__(self, preset_id: str, title: str, icon: str, count: int = 0, parent=None):
        super().__init__(parent)
        self.preset_id = preset_id
        self.title = title
        self.setObjectName("SmartFindCard")
        self._setup_ui(title, icon, count)
        self.setCursor(Qt.PointingHandCursor)

    def _setup_ui(self, title, icon, count):
        self.setStyleSheet("""
            #SmartFindCard {
                background-color: white;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 8px;
            }
            #SmartFindCard:hover {
                background-color: #f1f3f4;
                border-color: #dcdcdc;
            }
            QLabel#Title {
                font-weight: 500;
                color: #3c4043;
            }
            QLabel#Count {
                color: #70757a;
                font-size: 9pt;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        header = QHBoxLayout()
        lbl_icon = QLabel(icon)
        lbl_icon.setStyleSheet("font-size: 16pt;")

        lbl_title = QLabel(title)
        lbl_title.setObjectName("Title")

        header.addWidget(lbl_icon)
        header.addWidget(lbl_title, 1)

        self.lbl_count = QLabel(f"({count})" if count > 0 else "")
        self.lbl_count.setObjectName("Count")

        layout.addLayout(header)
        layout.addWidget(self.lbl_count, 0, Qt.AlignRight)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.preset_id)
        super().mousePressEvent(event)

    def set_count(self, count):
        self.lbl_count.setText(f"({count})" if count > 0 else "")
