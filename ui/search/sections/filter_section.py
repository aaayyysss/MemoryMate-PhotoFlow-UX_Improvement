from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QLabel, QPushButton, QWidget, QHBoxLayout


class FilterSection(QGroupBox):
    filterChanged = Signal(str, object)
    filterRemoved = Signal(str, object)
    clearAllFiltersRequested = Signal()

    def __init__(self, parent=None):
        super().__init__("Filters", parent)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(8)

        self.lbl_empty = QLabel("Filters appear after search or preset selection.")
        self.layout.addWidget(self.lbl_empty)

        self.filter_rows_host = QWidget()
        self.filter_rows_layout = QVBoxLayout(self.filter_rows_host)
        self.filter_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_rows_layout.setSpacing(6)
        self.layout.addWidget(self.filter_rows_host)

        self.btn_clear_all = QPushButton("Clear All Filters")
        self.btn_clear_all.clicked.connect(self.clearAllFiltersRequested.emit)
        self.layout.addWidget(self.btn_clear_all)

        self.setVisible(False)

    def _clear_rows(self):
        while self.filter_rows_layout.count():
            item = self.filter_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_facets(self, facets: dict, active_filters: dict, visible_keys=None):
        self._clear_rows()

        has_any = False
        active_filters = active_filters or {}
        facets = facets or {}
        visible_keys = list(visible_keys or [])

        if "people" in visible_keys:
            has_any |= self._add_facet_row("people", "People", facets.get("people", []), active_filters)

        if "location" in visible_keys:
            has_any |= self._add_facet_row("location", "Location", facets.get("locations", []), active_filters)

        if "year" in visible_keys:
            has_any |= self._add_facet_row("year", "Year", facets.get("years", []), active_filters)

        if "type" in visible_keys:
            has_any |= self._add_facet_row("type", "Type", facets.get("types", []), active_filters)

        self.lbl_empty.setVisible(not has_any)
        self.filter_rows_host.setVisible(has_any)
        self.btn_clear_all.setVisible(has_any or bool(active_filters))
        self.setVisible(bool(visible_keys) and (has_any or bool(active_filters)))

    def _add_facet_row(self, key: str, label: str, items, active_filters: dict):
        if not items:
            return False

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        row_layout.addWidget(QLabel(f"{label}:"))

        active_value = active_filters.get(key)

        for item in items[:10]:
            if isinstance(item, dict):
                value = item.get("id", item.get("name", item.get("value")))
                text = item.get("label") or item.get("name") or str(item.get("value") or item.get("id"))
                count = item.get("count")
            else:
                value = item
                text = str(item)
                count = None

            caption = f"{text} ({count})" if count is not None else text
            btn = QPushButton(caption)
            if active_value == value:
                btn.setObjectName("ActiveFacetButton")
            btn.clicked.connect(lambda checked=False, k=key, v=value: self.filterChanged.emit(k, v))
            row_layout.addWidget(btn)

        row_layout.addStretch(1)
        self.filter_rows_layout.addWidget(row)
        return True

    def set_enabled_for_project(self, enabled: bool):
        self.setEnabled(enabled)

    def set_active_filters(self, active_filters: dict):
        """Backward compatibility stub."""
        pass
