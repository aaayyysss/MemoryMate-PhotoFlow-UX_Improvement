"""
SelectionToolbar - Selection toolbar with batch operations.

Extracted from main_window_qt.py (Phase 2, Step 2.3)

Responsibilities:
- Always visible, buttons disabled when no selection
- Provides quick access to: Favorite, Delete, Export, Move, Tag, Clear Selection
- Shows selection count
- Updates button states based on selection

Version: 09.20.00.00
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from translation_manager import tr


class SelectionToolbar(QWidget):
    """
    Phase 2.3: Selection toolbar with batch operations.
    Always visible, buttons disabled when no selection.
    Provides quick access to: Favorite, Delete, Clear Selection
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(44)
        self.setStyleSheet("""
            SelectionToolbar {
                background-color: #E8F4FD;
                border: 1px solid #B3D9F2;
                border-radius: 4px;
                padding: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        # Selection count label
        self.label_count = QLabel("No photos selected")
        self.label_count.setStyleSheet("color: #333; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.label_count)

        layout.addStretch()

        # Action buttons (black text, disabled when no selection)
        self.btn_favorite = QPushButton(tr('toolbar.favorite'))
        self.btn_favorite.setStyleSheet("""
            QPushButton {
                background-color: #4A90E2;
                color: black;
                border: 1px solid #3A7BC8;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover:enabled {
                background-color: #357ABD;
            }
            QPushButton:disabled {
                background-color: #D0D0D0;
                color: #888;
                border: 1px solid #B0B0B0;
            }
        """)
        layout.addWidget(self.btn_favorite)

        self.btn_delete = QPushButton(tr('toolbar.delete'))
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #DC3545;
                color: black;
                border: 1px solid #C82333;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover:enabled {
                background-color: #C82333;
            }
            QPushButton:disabled {
                background-color: #D0D0D0;
                color: #888;
                border: 1px solid #B0B0B0;
            }
        """)
        layout.addWidget(self.btn_delete)

        self.btn_export = QPushButton(tr('toolbar.export'))
        self.btn_export.setStyleSheet(self.btn_delete.styleSheet())
        layout.addWidget(self.btn_export)

        self.btn_move = QPushButton(tr('toolbar.move'))
        self.btn_move.setStyleSheet(self.btn_delete.styleSheet())
        layout.addWidget(self.btn_move)

        self.btn_tag = QPushButton(tr('toolbar.tag'))
        self.btn_tag.setStyleSheet(self.btn_favorite.styleSheet())
        layout.addWidget(self.btn_tag)

        self.btn_clear = QPushButton(tr('toolbar.clear_selection'))
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #6C757D;
                color: black;
                border: 1px solid #5A6268;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover:enabled {
                background-color: #5A6268;
            }
            QPushButton:disabled {
                background-color: #D0D0D0;
                color: #888;
                border: 1px solid #B0B0B0;
            }
        """)
        layout.addWidget(self.btn_clear)

        # Start with buttons disabled
        self.btn_favorite.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_move.setEnabled(False)
        self.btn_tag.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.setVisible(False)

    def update_selection(self, count: int):
        """Update selection count and enable/disable buttons."""
        self.setVisible(count > 0)
        if count > 0:
            self.label_count.setText(f"{count} photo{'s' if count > 1 else ''} selected")
            for b in (self.btn_favorite, self.btn_delete, self.btn_export, self.btn_move, self.btn_tag, self.btn_clear):
                b.setEnabled(True)
        else:
            self.label_count.setText("No photos selected")
            for b in (self.btn_favorite, self.btn_delete, self.btn_export, self.btn_move, self.btn_tag, self.btn_clear):
                b.setEnabled(False)
