from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QLabel, QProgressBar, QPushButton


class ActivityMiniSection(QGroupBox):
    openActivityCenterRequested = Signal()

    def __init__(self, parent=None):
        super().__init__("Activity", parent)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(6)

        self.lbl_job = QLabel("No active background tasks")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)

        self.btn_open = QPushButton("Open Activity Center")
        self.btn_open.clicked.connect(self.openActivityCenterRequested.emit)

        self.layout.addWidget(self.lbl_job)
        self.layout.addWidget(self.progress)
        self.layout.addWidget(self.btn_open)

        self.setVisible(True)

    def set_activity(self, activity: dict | None):
        activity = activity or {}

        label = activity.get("label") or "No active background tasks"
        percent = activity.get("progress")

        # UX-10: Progress label polish with task name and percentage
        if percent is not None:
            self.lbl_job.setText(f"\u23f3 {label} \u2022 {int(percent)}%")
            self.progress.setVisible(True)
            self.progress.setValue(max(0, min(100, int(percent))))
        else:
            self.lbl_job.setText(str(label))
            self.progress.setVisible(False)
