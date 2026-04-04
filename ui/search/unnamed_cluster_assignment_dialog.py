"""
UX-9C: Unnamed cluster assignment dialog.

Lets the user:
- assign unnamed cluster to existing identity
- promote unnamed cluster to new named identity
- ignore low-quality cluster
"""

import base64
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QComboBox, QLineEdit, QDialogButtonBox
)


class UnnamedClusterAssignmentDialog(QDialog):
    assignRequested = Signal(str, str)
    promoteRequested = Signal(str, str)
    ignoreRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unnamed Cluster Review")
        self.resize(900, 560)

        self._clusters = []
        self._identity_choices = []

        self.list_clusters = QListWidget()
        self.list_clusters.setMaximumWidth(300)

        self.lbl_preview = QLabel("No preview")
        self.lbl_preview.setFixedSize(260, 260)
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setStyleSheet("border: 1px solid #ccc; background: #fafafa;")

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)

        self.cmb_existing = QComboBox()
        self.edit_new_name = QLineEdit()
        self.edit_new_name.setPlaceholderText("New identity name")

        self.btn_assign = QPushButton("Assign to Existing Identity")
        self.btn_promote = QPushButton("Promote to New Named Identity")
        self.btn_ignore = QPushButton("Ignore Low-Quality Cluster")

        right = QVBoxLayout()
        right.addWidget(self.lbl_preview)
        right.addWidget(self.lbl_info)
        right.addWidget(QLabel("Assign to existing identity"))
        right.addWidget(self.cmb_existing)
        right.addWidget(self.btn_assign)
        right.addSpacing(12)
        right.addWidget(QLabel("Or create a new named identity"))
        right.addWidget(self.edit_new_name)
        right.addWidget(self.btn_promote)
        right.addSpacing(12)
        right.addWidget(self.btn_ignore)
        right.addStretch(1)

        row = QHBoxLayout()
        row.addWidget(self.list_clusters, 0)
        row.addLayout(right, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)

        outer = QVBoxLayout(self)
        outer.addLayout(row, 1)
        outer.addWidget(buttons)

        self.list_clusters.currentItemChanged.connect(self._on_selection_changed)
        self.btn_assign.clicked.connect(self._assign_existing)
        self.btn_promote.clicked.connect(self._promote_new)
        self.btn_ignore.clicked.connect(self._ignore_cluster)

    def set_clusters(self, clusters):
        self._clusters = list(clusters or [])
        self.list_clusters.clear()

        for item in self._clusters:
            branch_key = str(item.get("branch_key", ""))
            count = int(item.get("count", 0))
            label = item.get("label") or f"Unnamed ({count})"
            list_item = QListWidgetItem(f"{label} [{branch_key}]")
            list_item.setData(256, branch_key)
            self.list_clusters.addItem(list_item)

        if self.list_clusters.count() > 0:
            self.list_clusters.setCurrentRow(0)

    def set_identity_choices(self, items):
        self._identity_choices = list(items or [])
        self.cmb_existing.clear()

        for item in self._identity_choices:
            label = item.get("label", "")
            value = item.get("id", label)
            self.cmb_existing.addItem(label, value)

    def _on_selection_changed(self, current, previous):
        if not current:
            self.lbl_preview.setText("No preview")
            self.lbl_info.setText("")
            return

        branch_key = current.data(256)
        payload = next((x for x in self._clusters if str(x.get("branch_key")) == str(branch_key)), None)
        if not payload:
            return

        self.lbl_info.setText(
            f"Cluster: {payload.get('branch_key')}\n"
            f"Photos: {payload.get('count', 0)}\n"
            f"Time hint: {payload.get('time_hint', 'Unavailable')}"
        )

        pix = self._load_pixmap(payload)
        if pix and not pix.isNull():
            self.lbl_preview.setPixmap(pix.scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.lbl_preview.setText("No preview")

    def _load_pixmap(self, payload):
        rep_thumb = payload.get("rep_thumb_png")
        rep_path = payload.get("rep_path")

        try:
            if rep_thumb:
                data = base64.b64decode(rep_thumb) if isinstance(rep_thumb, str) else rep_thumb
                pix = QPixmap()
                pix.loadFromData(data)
                if not pix.isNull():
                    return pix
        except Exception:
            pass

        try:
            if rep_path and os.path.exists(rep_path):
                pix = QPixmap(rep_path)
                if not pix.isNull():
                    return pix
        except Exception:
            pass

        return None

    def _selected_branch_key(self):
        item = self.list_clusters.currentItem()
        if not item:
            return None
        return str(item.data(256))

    def _assign_existing(self):
        branch_key = self._selected_branch_key()
        target = self.cmb_existing.currentData()
        if branch_key and target:
            self.assignRequested.emit(branch_key, str(target))

    def _promote_new(self):
        branch_key = self._selected_branch_key()
        name = self.edit_new_name.text().strip()
        if branch_key and name:
            self.promoteRequested.emit(branch_key, name)

    def _ignore_cluster(self):
        branch_key = self._selected_branch_key()
        if branch_key:
            self.ignoreRequested.emit(branch_key)
