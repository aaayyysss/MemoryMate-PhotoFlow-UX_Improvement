from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QComboBox
from shiboken6 import isValid


class SearchResultsHeader(QWidget):
    def __init__(self, store, controller=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.controller = controller

        self.lbl_summary = QLabel("All Photos")
        self.lbl_count = QLabel("0 results")
        self.cmb_sort = QComboBox()
        self.cmb_sort.addItem("Relevance", "relevance")
        self.cmb_sort.addItem("Newest", "newest")
        self.cmb_sort.addItem("Oldest", "oldest")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.lbl_summary, 1)
        layout.addWidget(self.lbl_count)
        layout.addWidget(self.cmb_sort)

        self.store.stateChanged.connect(self._on_state_changed)
        self.cmb_sort.currentIndexChanged.connect(self._on_sort_changed)

    def _on_state_changed(self, state):
        if not isValid(self):
            return

        if state.onboarding_mode:
            self.lbl_summary.setText("No active project")
            self.lbl_count.setText("")
            return

        if state.search_in_progress:
            self.lbl_summary.setText(state.intent_summary or "Searching...")
        else:
            self.lbl_summary.setText(state.intent_summary or "All Photos")

        self.lbl_count.setText(f"{state.result_count} result(s)")

    def _on_sort_changed(self):
        if not self.controller:
            return

        state = self.store.get_state()
        state.sort_mode = self.cmb_sort.currentData()
        self.store.stateChanged.emit(state)
        self.controller.run_search()
