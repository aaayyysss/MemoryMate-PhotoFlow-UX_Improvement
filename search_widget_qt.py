# search_widget_qt.py
# Version 01.00.00.00 dated 20251105
# Comprehensive search UI for MemoryMate-PhotoFlow

from PySide6.QtWidgets import (
    QWidget, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QDialog, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QDateEdit, QGroupBox, QFormLayout, QDialogButtonBox,
    QListWidget, QListWidgetItem, QMessageBox, QCompleter
)
from PySide6.QtCore import Qt, Signal, QDate, QTimer
from PySide6.QtGui import QIcon, QAction

from typing import Optional, List
from datetime import datetime, timedelta

from services import SearchService, SearchCriteria
from logging_config import get_logger
from translation_manager import tr

logger = get_logger(__name__)


class SearchBarWidget(QWidget):
    """
    Quick search bar with autocomplete.

    Provides instant search as user types.
    """

    searchTriggered = Signal(str)  # Emitted when search is triggered
    advancedSearchRequested = Signal()  # Emitted when advanced search button clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        from app_services import get_search_service
        self.search_service = get_search_service()
        self._setup_ui()
        self._setup_autocomplete()

    def _setup_ui(self):
        """Setup the search bar UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Search icon/label
        search_label = QLabel("🔍")
        layout.addWidget(search_label)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr('search.placeholder_main'))
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(self._on_search)
        self.search_input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.search_input, 1)

        # Search button
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._on_search)
        layout.addWidget(self.search_btn)

        # Advanced search button
        self.advanced_btn = QPushButton("Advanced...")
        self.advanced_btn.clicked.connect(self.advancedSearchRequested.emit)
        layout.addWidget(self.advanced_btn)

        # Debounce timer for live search
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._on_search)

    def _setup_autocomplete(self):
        """Setup autocomplete for search input."""
        self.completer = QCompleter()
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(self.completer)

    def _on_text_changed(self, text: str):
        """Handle text change with debouncing."""
        # Restart timer on each keystroke (debounce)
        self.search_timer.stop()
        if len(text) >= 3:  # Only search if 3+ characters
            self.search_timer.start(300)  # 300ms debounce

    def _on_search(self):
        """Trigger search."""
        query = self.search_input.text().strip()
        if query:
            self.searchTriggered.emit(query)
            logger.info(f"Quick search: {query}")

    def clear(self):
        """Clear the search input."""
        self.search_input.clear()


class AdvancedSearchDialog(QDialog):
    """
    Advanced search dialog with multiple criteria.

    Allows users to build complex search queries.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Photo Search")
        self.setModal(True)
        
        # ADAPTIVE DIALOG SIZING: Based on screen resolution
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        screen_width = screen.geometry().width()
        
        # Adaptive dialog size
        if screen_width >= 2560:  # 4K
            self.resize(800, 900)
        elif screen_width >= 1920:  # Full HD
            self.resize(700, 800)
        elif screen_width >= 1366:  # HD
            self.resize(600, 700)
        else:  # Small screens
            self.resize(500, 600)

        self.criteria = SearchCriteria()
        self._setup_ui()

    def _setup_ui(self):
        """Setup the advanced search UI."""
        layout = QVBoxLayout(self)

        # === Filename/Path Group ===
        file_group = QGroupBox("File Information")
        file_layout = QFormLayout()

        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText(tr('search.placeholder_filename'))
        file_layout.addRow("Filename contains:", self.filename_input)

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText(tr('search.placeholder_path'))
        file_layout.addRow("Path contains:", self.path_input)

        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # === Date Group ===
        date_group = QGroupBox("Date Range")
        date_layout = QFormLayout()

        self.date_from_enabled = QCheckBox()
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.setEnabled(False)
        self.date_from_enabled.toggled.connect(self.date_from.setEnabled)

        date_from_layout = QHBoxLayout()
        date_from_layout.addWidget(self.date_from_enabled)
        date_from_layout.addWidget(self.date_from, 1)
        file_layout_widget = QWidget()
        file_layout_widget.setLayout(date_from_layout)
        date_layout.addRow("From:", file_layout_widget)

        self.date_to_enabled = QCheckBox()
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setEnabled(False)
        self.date_to_enabled.toggled.connect(self.date_to.setEnabled)

        date_to_layout = QHBoxLayout()
        date_to_layout.addWidget(self.date_to_enabled)
        date_to_layout.addWidget(self.date_to, 1)
        date_to_widget = QWidget()
        date_to_widget.setLayout(date_to_layout)
        date_layout.addRow("To:", date_to_widget)

        # Quick date presets
        preset_layout = QHBoxLayout()
        btn_today = QPushButton("Today")
        btn_today.clicked.connect(lambda: self._set_date_preset(0))
        btn_week = QPushButton("This Week")
        btn_week.clicked.connect(lambda: self._set_date_preset(7))
        btn_month = QPushButton("This Month")
        btn_month.clicked.connect(lambda: self._set_date_preset(30))
        btn_year = QPushButton("This Year")
        btn_year.clicked.connect(lambda: self._set_date_preset(365))

        preset_layout.addWidget(btn_today)
        preset_layout.addWidget(btn_week)
        preset_layout.addWidget(btn_month)
        preset_layout.addWidget(btn_year)
        preset_layout.addStretch()

        preset_widget = QWidget()
        preset_widget.setLayout(preset_layout)
        date_layout.addRow("Presets:", preset_widget)

        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        # === Size Group ===
        size_group = QGroupBox("File Size (MB)")
        size_layout = QFormLayout()

        self.size_min_enabled = QCheckBox()
        self.size_min = QDoubleSpinBox()
        self.size_min.setRange(0, 10000)
        self.size_min.setSingleStep(0.1)
        self.size_min.setEnabled(False)
        self.size_min_enabled.toggled.connect(self.size_min.setEnabled)

        size_min_layout = QHBoxLayout()
        size_min_layout.addWidget(self.size_min_enabled)
        size_min_layout.addWidget(self.size_min, 1)
        size_min_widget = QWidget()
        size_min_widget.setLayout(size_min_layout)
        size_layout.addRow("Minimum:", size_min_widget)

        self.size_max_enabled = QCheckBox()
        self.size_max = QDoubleSpinBox()
        self.size_max.setRange(0, 10000)
        self.size_max.setSingleStep(0.1)
        self.size_max.setValue(10)
        self.size_max.setEnabled(False)
        self.size_max_enabled.toggled.connect(self.size_max.setEnabled)

        size_max_layout = QHBoxLayout()
        size_max_layout.addWidget(self.size_max_enabled)
        size_max_layout.addWidget(self.size_max, 1)
        size_max_widget = QWidget()
        size_max_widget.setLayout(size_max_layout)
        size_layout.addRow("Maximum:", size_max_widget)

        size_group.setLayout(size_layout)
        layout.addWidget(size_group)

        # === Dimensions Group ===
        dim_group = QGroupBox("Dimensions (pixels)")
        dim_layout = QFormLayout()

        self.width_min_enabled = QCheckBox()
        self.width_min = QSpinBox()
        self.width_min.setRange(0, 100000)
        self.width_min.setSingleStep(100)
        self.width_min.setEnabled(False)
        self.width_min_enabled.toggled.connect(self.width_min.setEnabled)

        width_min_layout = QHBoxLayout()
        width_min_layout.addWidget(self.width_min_enabled)
        width_min_layout.addWidget(self.width_min, 1)
        width_min_widget = QWidget()
        width_min_widget.setLayout(width_min_layout)
        dim_layout.addRow("Min Width:", width_min_widget)

        self.height_min_enabled = QCheckBox()
        self.height_min = QSpinBox()
        self.height_min.setRange(0, 100000)
        self.height_min.setSingleStep(100)
        self.height_min.setEnabled(False)
        self.height_min_enabled.toggled.connect(self.height_min.setEnabled)

        height_min_layout = QHBoxLayout()
        height_min_layout.addWidget(self.height_min_enabled)
        height_min_layout.addWidget(self.height_min, 1)
        height_min_widget = QWidget()
        height_min_widget.setLayout(height_min_layout)
        dim_layout.addRow("Min Height:", height_min_widget)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["Any", "Landscape", "Portrait", "Square"])
        dim_layout.addRow("Orientation:", self.orientation_combo)

        dim_group.setLayout(dim_layout)
        layout.addWidget(dim_group)

        # === Camera Group ===
        camera_group = QGroupBox("Camera & Location")
        camera_layout = QFormLayout()

        self.camera_input = QLineEdit()
        self.camera_input.setPlaceholderText("e.g., Canon, iPhone 13")
        camera_layout.addRow("Camera Model:", self.camera_input)

        self.gps_combo = QComboBox()
        self.gps_combo.addItems(["Any", "With GPS", "Without GPS"])
        camera_layout.addRow("Location:", self.gps_combo)

        camera_group.setLayout(camera_layout)
        layout.addWidget(camera_group)

        # === Results Group ===
        results_group = QGroupBox("Results")
        results_layout = QFormLayout()

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(10, 10000)
        self.limit_spin.setSingleStep(50)
        self.limit_spin.setValue(100)
        results_layout.addRow("Max Results:", self.limit_spin)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Date Taken (Newest First)",
            "Date Taken (Oldest First)",
            "Filename (A-Z)",
            "Filename (Z-A)",
            "Size (Largest First)",
            "Size (Smallest First)"
        ])
        results_layout.addRow("Sort By:", self.sort_combo)

        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Reset).clicked.connect(self._reset_form)

        layout.addWidget(button_box)

    def _set_date_preset(self, days_ago: int):
        """Set date range based on preset."""
        self.date_to_enabled.setChecked(True)
        self.date_to.setDate(QDate.currentDate())

        if days_ago == 0:
            # Today only
            self.date_from_enabled.setChecked(True)
            self.date_from.setDate(QDate.currentDate())
        else:
            # Range
            self.date_from_enabled.setChecked(True)
            self.date_from.setDate(QDate.currentDate().addDays(-days_ago))

    def _reset_form(self):
        """Reset all form fields to defaults."""
        self.filename_input.clear()
        self.path_input.clear()

        self.date_from_enabled.setChecked(False)
        self.date_to_enabled.setChecked(False)

        self.size_min_enabled.setChecked(False)
        self.size_max_enabled.setChecked(False)

        self.width_min_enabled.setChecked(False)
        self.height_min_enabled.setChecked(False)

        self.orientation_combo.setCurrentIndex(0)
        self.camera_input.clear()
        self.gps_combo.setCurrentIndex(0)

        self.limit_spin.setValue(100)
        self.sort_combo.setCurrentIndex(0)

    def get_search_criteria(self) -> SearchCriteria:
        """
        Build SearchCriteria from form inputs.

        Returns:
            SearchCriteria object
        """
        criteria = SearchCriteria()

        # Filename/Path
        if self.filename_input.text().strip():
            criteria.filename_pattern = self.filename_input.text().strip()

        if self.path_input.text().strip():
            criteria.path_contains = self.path_input.text().strip()

        # Dates
        if self.date_from_enabled.isChecked():
            criteria.date_from = self.date_from.date().toString("yyyy-MM-dd")

        if self.date_to_enabled.isChecked():
            criteria.date_to = self.date_to.date().toString("yyyy-MM-dd")

        # Size (convert MB to KB)
        if self.size_min_enabled.isChecked():
            criteria.size_min = self.size_min.value() * 1024

        if self.size_max_enabled.isChecked():
            criteria.size_max = self.size_max.value() * 1024

        # Dimensions
        if self.width_min_enabled.isChecked():
            criteria.width_min = self.width_min.value()

        if self.height_min_enabled.isChecked():
            criteria.height_min = self.height_min.value()

        # Orientation
        orientation_map = {
            "Landscape": "landscape",
            "Portrait": "portrait",
            "Square": "square"
        }
        orientation_text = self.orientation_combo.currentText()
        if orientation_text in orientation_map:
            criteria.orientation = orientation_map[orientation_text]

        # Camera
        if self.camera_input.text().strip():
            criteria.camera_model = self.camera_input.text().strip()

        # GPS
        gps_text = self.gps_combo.currentText()
        if gps_text == "With GPS":
            criteria.has_gps = True
        elif gps_text == "Without GPS":
            criteria.has_gps = False

        # Results
        criteria.limit = self.limit_spin.value()

        # Sorting
        sort_map = {
            "Date Taken (Newest First)": ("date_taken", "DESC"),
            "Date Taken (Oldest First)": ("date_taken", "ASC"),
            "Filename (A-Z)": ("filename", "ASC"),
            "Filename (Z-A)": ("filename", "DESC"),
            "Size (Largest First)": ("size", "DESC"),
            "Size (Smallest First)": ("size", "ASC"),
        }
        sort_text = self.sort_combo.currentText()
        if sort_text in sort_map:
            criteria.sort_by, criteria.sort_order = sort_map[sort_text]

        return criteria
