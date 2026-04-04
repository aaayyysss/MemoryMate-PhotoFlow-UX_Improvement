"""
UX-9A: People Merge Suggestions Panel — rationale-aware review UI.

Shows ranked merge candidates with detailed scoring breakdown.
Supports accept/reject actions that persist through PeopleMergeReviewRepository.
"""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QHBoxLayout, QDialog, QDialogButtonBox, QTextEdit,
)


class PeopleMergeSuggestionsPanel(QWidget):
    mergeAccepted = Signal(str, str)
    mergeRejected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.lbl_title = QLabel("Possible People Merges")
        self.list_widget = QListWidget()
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(120)

        self.btn_accept = QPushButton("Merge Selected")
        self.btn_reject = QPushButton("Reject Selected")

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_accept)
        btn_row.addWidget(self.btn_reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.details)
        layout.addLayout(btn_row)

        self._items = []

        self.btn_accept.clicked.connect(self._accept_selected)
        self.btn_reject.clicked.connect(self._reject_selected)
        self.list_widget.currentRowChanged.connect(self._update_details)

    def set_suggestions(self, suggestions):
        self._items = list(suggestions or [])
        self.list_widget.clear()
        self.details.clear()

        for item in self._items:
            left_id = str(item.get("left_id", ""))
            right_id = str(item.get("right_id", ""))
            score = item.get("score")
            score_txt = f"{score:.2f}" if isinstance(score, (float, int)) else "?"
            title = item.get("label") or f"{left_id} \u2194 {right_id}  (score={score_txt})"

            list_item = QListWidgetItem(title)
            list_item.setData(256, (left_id, right_id))
            self.list_widget.addItem(list_item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _update_details(self, row: int):
        if row < 0 or row >= len(self._items):
            self.details.clear()
            return

        item = self._items[row]
        rationale = dict(item.get("rationale", {}) or {})

        lines = [
            f'Left ID: {item.get("left_id", "")}',
            f'Right ID: {item.get("right_id", "")}',
            f'Score: {item.get("score", "")}',
            "",
            "Rationale:",
        ]
        for k, v in rationale.items():
            lines.append(f"  {k}: {v}")

        # Also show legacy reasons if present
        reasons = item.get("reasons", [])
        if reasons:
            lines.append("")
            lines.append("Reasons: " + ", ".join(reasons))

        self.details.setPlainText("\n".join(lines))

    def _accept_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        left_id, right_id = item.data(256)
        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)
        self.details.clear()
        self.mergeAccepted.emit(left_id, right_id)

    def _reject_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        left_id, right_id = item.data(256)
        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)
        self.details.clear()
        self.mergeRejected.emit(left_id, right_id)


class PeopleMergeSuggestionsDialog(QDialog):
    mergeAccepted = Signal(str, str)
    mergeRejected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("People Merge Review")
        self.resize(640, 480)

        self.panel = PeopleMergeSuggestionsPanel(self)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.panel)
        layout.addWidget(buttons)

        self.panel.mergeAccepted.connect(self.mergeAccepted.emit)
        self.panel.mergeRejected.connect(self.mergeRejected.emit)

    def set_suggestions(self, suggestions):
        self.panel.set_suggestions(suggestions)
