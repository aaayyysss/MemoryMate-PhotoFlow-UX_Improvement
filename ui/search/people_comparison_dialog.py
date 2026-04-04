from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QDialogButtonBox, QWidget
)


class PeopleComparisonDialog(QDialog):
    """UX-10B: text-based side-by-side people cluster comparison dialog."""

    mergeAccepted = Signal(str, str)
    mergeRejected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare People Clusters")
        self.resize(760, 480)

        self.left_id = None
        self.right_id = None

        self.lbl_title = QLabel("Compare possible duplicate identities")

        self.left_title = QLabel("Left Cluster")
        self.right_title = QLabel("Right Cluster")

        self.left_list = QListWidget()
        self.right_list = QListWidget()

        self.lbl_meta = QLabel("")
        self.lbl_meta.setWordWrap(True)

        lists_row = QHBoxLayout()
        left_box = QVBoxLayout()
        right_box = QVBoxLayout()

        left_box.addWidget(self.left_title)
        left_box.addWidget(self.left_list)

        right_box.addWidget(self.right_title)
        right_box.addWidget(self.right_list)

        lists_row.addLayout(left_box, 1)
        lists_row.addLayout(right_box, 1)

        self.btn_merge = QPushButton("Merge These People")
        self.btn_reject = QPushButton("Keep Separate")

        action_row = QHBoxLayout()
        action_row.addWidget(self.btn_merge)
        action_row.addWidget(self.btn_reject)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_title)
        layout.addLayout(lists_row)
        layout.addWidget(self.lbl_meta)
        layout.addLayout(action_row)
        layout.addWidget(buttons)

        self.btn_merge.clicked.connect(self._accept_merge)
        self.btn_reject.clicked.connect(self._reject_merge)

    def set_comparison(self, payload: dict):
        self.left_id = payload.get("left_id")
        self.right_id = payload.get("right_id")

        self.left_title.setText(payload.get("left_label", str(self.left_id)))
        self.right_title.setText(payload.get("right_label", str(self.right_id)))

        self.left_list.clear()
        self.right_list.clear()

        for item in payload.get("left_samples", []):
            self.left_list.addItem(QListWidgetItem(str(item)))

        for item in payload.get("right_samples", []):
            self.right_list.addItem(QListWidgetItem(str(item)))

        score = payload.get("score")
        reason = payload.get("reason", "")
        score_txt = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
        self.lbl_meta.setText(f"Score: {score_txt}\n{reason}".strip())

    def _accept_merge(self):
        if self.left_id and self.right_id:
            self.mergeAccepted.emit(str(self.left_id), str(self.right_id))

    def _reject_merge(self):
        if self.left_id and self.right_id:
            self.mergeRejected.emit(str(self.left_id), str(self.right_id))
