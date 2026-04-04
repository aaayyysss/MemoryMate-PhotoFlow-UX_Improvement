from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem, QFrame


class PeopleQuickSection(QWidget):
    mergeReviewRequested = Signal()
    unnamedRequested = Signal()
    showAllPeopleRequested = Signal()
    peopleToolsRequested = Signal()

    mergeHistoryRequested = Signal()
    undoMergeRequested = Signal()
    redoMergeRequested = Signal()
    expandPeopleRequested = Signal()

    personRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        intro = QLabel("Top people and review tools")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.top_people_list = QListWidget()
        self.top_people_list.setMaximumHeight(120)
        self.top_people_list.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self.top_people_list)

        self.btn_merge_review = QPushButton("Review Possible Merges (0)")
        self.btn_unnamed = QPushButton("Show Unnamed Clusters (0)")
        self.btn_show_all = QPushButton("Show All People")
        self.btn_tools = QPushButton("People Tools")

        self.btn_merge_review.clicked.connect(self.mergeReviewRequested.emit)
        self.btn_unnamed.clicked.connect(self.unnamedRequested.emit)
        self.btn_show_all.clicked.connect(self.showAllPeopleRequested.emit)
        self.btn_tools.clicked.connect(self.peopleToolsRequested.emit)

        root.addWidget(self.btn_merge_review)
        root.addWidget(self.btn_unnamed)
        root.addWidget(self.btn_show_all)
        root.addWidget(self.btn_tools)

        legacy_label = QLabel("Legacy Actions")
        root.addWidget(legacy_label)

        self.btn_history = QPushButton("History")
        self.btn_undo = QPushButton("Undo")
        self.btn_redo = QPushButton("Redo")
        self.btn_expand = QPushButton("Expand")

        self.btn_history.clicked.connect(self.mergeHistoryRequested.emit)
        self.btn_undo.clicked.connect(self.undoMergeRequested.emit)
        self.btn_redo.clicked.connect(self.redoMergeRequested.emit)
        self.btn_expand.clicked.connect(self.expandPeopleRequested.emit)

        root.addWidget(self.btn_history)
        root.addWidget(self.btn_undo)
        root.addWidget(self.btn_redo)
        root.addWidget(self.btn_expand)

    def _on_item_clicked(self, item: QListWidgetItem):
        person_id = item.data(Qt.UserRole)
        if person_id:
            self.personRequested.emit(str(person_id))

    def set_people_rows(self, rows):
        self._rows = list(rows or [])
        self.top_people_list.clear()

        for row in self._rows[:10]:
            label = row.get("label") or row.get("display_name") or row.get("id") or "Unknown"
            count = int(row.get("count", 0) or 0)
            text = f"{label} ({count})" if count else str(label)

            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, row.get("id") or row.get("branch_key") or row.get("person_id"))
            self.top_people_list.addItem(item)

    def set_counts(self, merge_count: int = 0, unnamed_count: int = 0):
        self.btn_merge_review.setText(f"Review Possible Merges ({int(merge_count or 0)})")
        self.btn_unnamed.setText(f"Show Unnamed Clusters ({int(unnamed_count or 0)})")

    def set_legacy_actions_enabled(self, enabled: bool):
        self.btn_history.setEnabled(enabled)
        self.btn_undo.setEnabled(enabled)
        self.btn_redo.setEnabled(enabled)
        self.btn_expand.setEnabled(enabled)
