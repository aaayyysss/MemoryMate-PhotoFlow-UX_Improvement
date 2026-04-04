from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QWidget, QScrollArea
)
import os
import base64


class PersonComparisonDialog(QDialog):
    mergeAccepted = Signal(str, str)
    mergeRejected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review Possible Merge")
        self.resize(900, 620)

        self._payload = None

        self.lbl_header = QLabel("Compare these two people clusters")
        self.lbl_header.setStyleSheet("font-size: 14px; font-weight: bold;")

        self.left_group = QGroupBox("Left Cluster")
        self.right_group = QGroupBox("Right Cluster")

        self.left_layout = QVBoxLayout(self.left_group)
        self.right_layout = QVBoxLayout(self.right_group)

        compare_row = QHBoxLayout()
        compare_row.addWidget(self.left_group, 1)
        compare_row.addWidget(self.right_group, 1)

        self.lbl_meta = QLabel("")
        self.lbl_meta.setWordWrap(True)

        self.btn_reject = QPushButton("Not the Same Person")
        self.btn_accept = QPushButton("Merge These Clusters")
        self.btn_close = QPushButton("Close")

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.btn_reject)
        btn_row.addWidget(self.btn_accept)
        btn_row.addWidget(self.btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_header)
        layout.addLayout(compare_row)
        layout.addWidget(self.lbl_meta)
        layout.addLayout(btn_row)

        self.btn_accept.clicked.connect(self._accept_merge)
        self.btn_reject.clicked.connect(self._reject_merge)
        self.btn_close.clicked.connect(self.close)

    def set_payload(self, payload: dict):
        self._payload = dict(payload or {})

        self._populate_side(
            self.left_layout,
            self._payload.get("left_label", "Left"),
            self._payload.get("left_count", 0),
            self._payload.get("left_preview_paths", []),
            self._payload.get("left_preview_thumbs", []),
        )
        self._populate_side(
            self.right_layout,
            self._payload.get("right_label", "Right"),
            self._payload.get("right_count", 0),
            self._payload.get("right_preview_paths", []),
            self._payload.get("right_preview_thumbs", []),
        )

        score = self._payload.get("score")
        score_txt = f"{score:.3f}" if isinstance(score, (float, int)) else "?"
        self.lbl_meta.setText(
            f"Similarity score: {score_txt}\n"
            f"Reason: {self._payload.get('reason', 'No explanation available')}"
        )

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _populate_side(self, layout, label, count, preview_paths, preview_thumbs):
        self._clear_layout(layout)

        title = QLabel(f"<b>{label}</b><br>{count} photo(s)")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(8)

        paths = list(preview_paths or [])
        thumbs = list(preview_thumbs or [])

        max_items = max(len(paths), len(thumbs), 0)
        for i in range(min(max_items, 6)):
            lbl = QLabel()
            lbl.setFixedSize(96, 96)
            pix = self._load_pixmap(
                thumbs[i] if i < len(thumbs) else None,
                paths[i] if i < len(paths) else None,
            )
            if pix and not pix.isNull():
                lbl.setPixmap(pix.scaled(96, 96, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            lbl.setStyleSheet("border: 1px solid #d0d0d0; background: white;")
            row.addWidget(lbl)

        row.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _load_pixmap(self, thumb_data, path):
        try:
            pix = QPixmap()
            if thumb_data:
                data = base64.b64decode(thumb_data) if isinstance(thumb_data, str) else thumb_data
                pix.loadFromData(data)
                if not pix.isNull():
                    return pix
            if path and os.path.exists(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    return pix
        except Exception:
            pass
        return None

    def _accept_merge(self):
        if not self._payload:
            return
        self.mergeAccepted.emit(str(self._payload.get("left_id")), str(self._payload.get("right_id")))
        self.accept()

    def _reject_merge(self):
        if not self._payload:
            return
        self.mergeRejected.emit(str(self._payload.get("left_id")), str(self._payload.get("right_id")))
        self.reject()
