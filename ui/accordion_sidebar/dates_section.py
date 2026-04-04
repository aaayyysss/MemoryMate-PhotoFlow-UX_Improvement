# ui/accordion_sidebar/dates_section.py
# Dates section for accordion sidebar - Hierarchical Year > Month > Day view

import threading
import traceback
import logging
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView, QSizePolicy, QLabel
from PySide6.QtCore import Signal, Qt, QObject
from PySide6.QtGui import QColor
from reference_db import ReferenceDB
from translation_manager import tr
from .base_section import BaseSection

logger = logging.getLogger(__name__)


class DatesSectionSignals(QObject):
    """Signals for dates section loading."""
    loaded = Signal(int, dict)  # (generation, date_data)
    error = Signal(int, str)     # (generation, error_message)


class DatesSection(BaseSection):
    """
    Dates section implementation.

    Displays hierarchical date tree: Year > Month > Day
    Shows photo/video counts for each level.
    """

    # Signal emitted when date is selected (double-click)
    dateSelected = Signal(str)  # date_string (e.g., "2024", "2024-10", "2024-10-15")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = DatesSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)
        self.signals.error.connect(self._on_error)

    def get_section_id(self) -> str:
        return "dates"

    def get_title(self) -> str:
        return tr('sidebar.header_year_month_day')

    def get_icon(self) -> str:
        return "📅"

    def load_section(self) -> None:
        """Load date hierarchy from database in background thread."""
        if not self.project_id:
            logger.warning("[DatesSection] No project_id set")
            return

        # Increment generation
        self._generation += 1
        current_gen = self._generation
        self._loading = True

        logger.info(f"[DatesSection] Loading dates (generation {current_gen})...")

        # Background worker
        def work():
            db = None
            try:
                db = ReferenceDB()  # Per-thread instance

                # Get hierarchical date data: {year: {month: [days]}}
                hier = {}
                year_counts = {}
                month_counts = {}
                day_counts = {}

                if hasattr(db, "get_date_hierarchy"):
                    hier = db.get_date_hierarchy(self.project_id) or {}

                if hasattr(db, "list_years_with_counts"):
                    year_list = db.list_years_with_counts(self.project_id) or []
                    year_counts = {str(y): c for y, c in year_list}

                # Derive month/day counts so the tree can show nested totals
                for year, months in hier.items():
                    for month, days in months.items():
                        month_key = f"{int(month):02d}"
                        ym = f"{year}-{month_key}"
                        try:
                            month_counts[ym] = db.count_for_month(year, month_key, self.project_id)
                        except Exception:
                            month_counts[ym] = len(days) if isinstance(days, list) else 0

                        for day in days:
                            try:
                                day_counts[day] = db.count_for_day(day, self.project_id)
                            except Exception:
                                day_counts[day] = 0

                logger.info(f"[DatesSection] Loaded {len(hier)} years (gen {current_gen})")
                return {
                    "hierarchy": hier,
                    "year_counts": year_counts,
                    "month_counts": month_counts,
                    "day_counts": day_counts,
                }
            except Exception as e:
                error_msg = f"Error loading dates: {e}"
                logger.error(f"[DatesSection] {error_msg}")
                traceback.print_exc()
                return {"hierarchy": {}, "year_counts": {}, "month_counts": {}, "day_counts": {}}
            finally:
                if db:
                    try:
                        db.close()
                    except Exception:
                        pass

        # Run in thread
        def on_complete():
            try:
                result = work()
                self.signals.loaded.emit(current_gen, result)
            except Exception as e:
                logger.error(f"[DatesSection] Error in worker thread: {e}")
                traceback.print_exc()
                self.signals.error.emit(current_gen, str(e))

        threading.Thread(target=on_complete, daemon=True).start()

    def create_content_widget(self, data):
        """Create dates tree widget."""
        data = data or {}
        hier = data.get("hierarchy", {})
        year_counts = data.get("year_counts", {})
        month_counts = data.get("month_counts", {})
        day_counts = data.get("day_counts", {})

        if not hier:
            placeholder = QLabel("No dates found")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("padding: 20px; color: #666;")
            return placeholder

        # Create tree widget
        tree = QTreeWidget()
        tree.setHeaderLabels([self.get_title(), "Count"])
        tree.setColumnCount(2)
        tree.setSelectionMode(QTreeWidget.SingleSelection)
        tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        tree.setAlternatingRowColors(True)
        tree.setMinimumHeight(200)
        tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        tree.setStyleSheet("""
            QTreeWidget {
                border: none;
                background: transparent;
            }
            QTreeWidget::item {
                padding: 4px;
            }
            QTreeWidget::item:hover {
                background: #f1f3f4;
            }
            QTreeWidget::item:selected {
                background: #e8f0fe;
                color: #1a73e8;
            }
        """)

        # Month names
        month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        # Populate tree: Years > Months > Days
        for year in sorted(hier.keys(), reverse=True):
            year_count = year_counts.get(str(year), 0)
            year_item = QTreeWidgetItem([str(year), str(year_count)])
            year_item.setData(0, Qt.UserRole, str(year))
            year_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            year_item.setForeground(1, QColor("#888888"))
            tree.addTopLevelItem(year_item)

            months = hier.get(year, {}) or {}
            for month in sorted(months.keys(), key=lambda m: int(m), reverse=True):
                month_key = f"{int(month):02d}"
                ym = f"{year}-{month_key}"
                month_label = month_names[int(month)] if str(month).isdigit() else str(month)
                month_count = month_counts.get(ym, 0)

                month_item = QTreeWidgetItem([f"{month_label}", str(month_count)])
                month_item.setData(0, Qt.UserRole, ym)
                month_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                month_item.setForeground(1, QColor("#888888"))
                year_item.addChild(month_item)

                days = months.get(month, []) or []
                for day in sorted(days, reverse=True):
                    day_count = day_counts.get(day, 0)
                    try:
                        day_label = day.split("-")[-1]
                    except Exception:
                        day_label = day

                    day_item = QTreeWidgetItem([day_label, str(day_count)])
                    day_item.setData(0, Qt.UserRole, day)
                    day_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    day_item.setForeground(1, QColor("#888888"))
                    month_item.addChild(day_item)

        # Connect double-click
        tree.itemDoubleClicked.connect(
            lambda item, col: self.dateSelected.emit(item.data(0, Qt.UserRole))
            if item.data(0, Qt.UserRole) else None
        )

        logger.info(f"[DatesSection] Tree built with {tree.topLevelItemCount()} years")
        return tree

    def _on_data_loaded(self, generation: int, data: dict):
        """Callback when dates data is loaded."""
        self._loading = False
        if generation != self._generation:
            logger.debug(f"[DatesSection] Discarding stale data (gen {generation} vs {self._generation})")
            return
        logger.info(f"[DatesSection] Data loaded successfully (gen {generation})")

    def _on_error(self, generation: int, error_msg: str):
        """Callback when dates loading fails."""
        self._loading = False
        if generation != self._generation:
            return
        logger.error(f"[DatesSection] Load failed: {error_msg}")
