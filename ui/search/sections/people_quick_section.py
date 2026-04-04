from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QPushButton, QLabel, QWidget, QHBoxLayout


class PeopleQuickSection(QGroupBox):
    personSelected = Signal(str)
    showAllPeopleRequested = Signal()
    mergeReviewRequested = Signal()
    unnamedRequested = Signal()

    def __init__(self, parent=None):
        super().__init__("People", parent)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(8)

        self.lbl_empty = QLabel("People will appear after face clustering.")
        self.layout.addWidget(self.lbl_empty)

        self.people_host = QWidget()
        self.people_layout = QVBoxLayout(self.people_host)
        self.people_layout.setContentsMargins(0, 0, 0, 0)
        self.people_layout.setSpacing(6)
        self.layout.addWidget(self.people_host)

        self.btn_merge_review = QPushButton("Review Possible Merges")
        self.btn_merge_review.setStyleSheet("""
            QPushButton {
                background: #e8f0fe; color: #1a73e8;
                border: 1px solid #d2e3fc; border-radius: 6px;
                padding: 6px 10px; font-weight: 500;
            }
            QPushButton:hover { background: #d2e3fc; }
        """)
        self.btn_merge_review.clicked.connect(self.mergeReviewRequested.emit)
        self.layout.addWidget(self.btn_merge_review)

        self.btn_unnamed = QPushButton("Show Unnamed Clusters")
        self.btn_unnamed.setStyleSheet("""
            QPushButton {
                background: #fef7e0; color: #795548;
                border: 1px solid #f9ab00; border-radius: 6px;
                padding: 6px 10px; font-weight: 500;
            }
            QPushButton:hover { background: #fcefc7; }
        """)
        self.btn_unnamed.clicked.connect(self.unnamedRequested.emit)
        self.layout.addWidget(self.btn_unnamed)

        self.btn_show_all = QPushButton("Show All People")
        self.btn_show_all.clicked.connect(self.showAllPeopleRequested.emit)
        self.layout.addWidget(self.btn_show_all)

        self.setVisible(False)

    def _clear_people(self):
        while self.people_layout.count():
            item = self.people_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_people(self, payload):
        self._clear_people()

        payload = payload or {}
        people_items = list(payload.get("top_people", []))
        merge_candidates = int(payload.get("merge_candidates", 0) or 0)
        unnamed_count = int(payload.get("unnamed_count", 0) or 0)

        has_any = bool(people_items or merge_candidates or unnamed_count)

        self.lbl_empty.setVisible(not has_any)
        self.people_host.setVisible(bool(people_items))
        self.btn_merge_review.setVisible(merge_candidates > 0)
        self.btn_merge_review.setText(f"Review Possible Merges ({merge_candidates})")
        self.btn_unnamed.setVisible(unnamed_count > 0)
        self.btn_unnamed.setText(f"Review Unnamed Clusters ({unnamed_count})")
        self.btn_show_all.setVisible(has_any)
        self.setVisible(has_any)

        for item in people_items[:8]:
            person_id = item.get("id")
            label = item.get("label", str(person_id))
            count = item.get("count", 0)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            btn = QPushButton(f"{label} ({count})")
            btn.clicked.connect(lambda checked=False, pid=person_id: self.personSelected.emit(str(pid)))
            row_layout.addWidget(btn)

            self.people_layout.addWidget(row)
