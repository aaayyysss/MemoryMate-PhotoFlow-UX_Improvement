from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class ResultContextBar(QWidget):
    """UX-10A: thin visual layer between header and grid showing result explanation and scope."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.lbl_explanation = QLabel("")
        self.lbl_explanation.setObjectName("ResultContextExplanation")
        self.lbl_explanation.setWordWrap(True)
        self.lbl_explanation.setVisible(False)

        self.lbl_scope = QLabel("")
        self.lbl_scope.setObjectName("ResultContextScope")
        self.lbl_scope.setWordWrap(True)
        self.lbl_scope.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.lbl_explanation)
        layout.addWidget(self.lbl_scope)

    def set_context(self, explanation: str = "", scope_label: str = ""):
        self.lbl_explanation.setText(explanation or "")
        self.lbl_explanation.setVisible(bool(explanation))

        self.lbl_scope.setText(scope_label or "")
        self.lbl_scope.setVisible(bool(scope_label))
