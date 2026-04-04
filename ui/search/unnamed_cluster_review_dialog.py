from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QHBoxLayout, QDialogButtonBox
)


class UnnamedClusterReviewDialog(QDialog):
    """UX-10C: review unnamed clusters with assign/keep/ignore actions."""

    assignRequested = Signal(str, str)
    keepSeparateRequested = Signal(str)
    markDistinctRequested = Signal(str)  # alias for keepSeparateRequested (UX-9 compat)
    ignoreRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unnamed Cluster Review")
        self.resize(680, 420)

        self.cluster_id = None

        self.lbl_title = QLabel("Unnamed cluster review")
        self.cluster_samples = QListWidget()
        self.people_candidates = QListWidget()

        self.btn_assign = QPushButton("Assign to Selected Person")
        self.btn_keep = QPushButton("Keep as Separate Person")
        self.btn_ignore = QPushButton("Ignore for Now")

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_assign)
        btn_row.addWidget(self.btn_keep)
        btn_row.addWidget(self.btn_ignore)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_title)
        layout.addWidget(QLabel("Cluster Samples"))
        layout.addWidget(self.cluster_samples)
        layout.addWidget(QLabel("Candidate People"))
        layout.addWidget(self.people_candidates)
        layout.addLayout(btn_row)
        layout.addWidget(buttons)

        self.btn_assign.clicked.connect(self._assign_selected)
        self.btn_keep.clicked.connect(self._keep_separate)
        self.btn_ignore.clicked.connect(self._ignore)

    def set_payload(self, payload: dict):
        self.cluster_id = payload.get("cluster_id")
        self.lbl_title.setText(payload.get("label", f"Unnamed Cluster {self.cluster_id}"))

        self.cluster_samples.clear()
        for s in payload.get("samples", []):
            self.cluster_samples.addItem(QListWidgetItem(str(s)))

        self.people_candidates.clear()
        for p in payload.get("candidate_people", []):
            item = QListWidgetItem(f"{p.get('label')} ({p.get('count', 0)})")
            item.setData(Qt.UserRole, str(p.get("id")))
            self.people_candidates.addItem(item)

    def _assign_selected(self):
        item = self.people_candidates.currentItem()
        if not item or not self.cluster_id:
            return
        target_id = item.data(Qt.UserRole)
        self.assignRequested.emit(str(self.cluster_id), str(target_id))

    def _keep_separate(self):
        if self.cluster_id:
            self.keepSeparateRequested.emit(str(self.cluster_id))
            self.markDistinctRequested.emit(str(self.cluster_id))

    def _ignore(self):
        if self.cluster_id:
            self.ignoreRequested.emit(str(self.cluster_id))
