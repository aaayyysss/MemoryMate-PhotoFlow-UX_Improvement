# ui/accordion_sidebar/quick_section.py
# Quick dates section - full implementation

"""
Quick Dates Section

Provides quick access to common date filters without navigating the date hierarchy.
Inspired by Google Photos quick filters for temporal navigation.

Quick date options:
- Today
- Yesterday
- Last 7 days
- Last 30 days
- This month
- Last month
- This year
- Last year

Each option calculates the appropriate date range and emits a signal for filtering.
"""

import logging
from datetime import datetime, timedelta
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, Qt
from .base_section import BaseSection

logger = logging.getLogger(__name__)


class QuickSection(BaseSection):
    """
    Quick dates section implementation.

    Provides quick access to common date filters:
    - Today: Current day
    - Yesterday: Previous day
    - Last 7 days: Rolling 7-day window
    - Last 30 days: Rolling 30-day window
    - This month: Current calendar month
    - Last month: Previous calendar month
    - This year: Current calendar year
    - Last year: Previous calendar year

    Signals:
        quickDateSelected(str): Emitted when a quick date is selected.
                                Format matches date filtering expectations.
    """

    quickDateSelected = Signal(str)  # date range string for filtering

    def __init__(self, parent=None):
        super().__init__(parent)

        # Define quick date options
        # Each tuple: (date_id, display_label)
        self._quick_dates = [
            ("today", "Today"),
            ("yesterday", "Yesterday"),
            ("last_7_days", "Last 7 days"),
            ("last_30_days", "Last 30 days"),
            ("this_month", "This month"),
            ("last_month", "Last month"),
            ("this_year", "This year"),
            ("last_year", "Last year"),
        ]

    def get_section_id(self) -> str:
        """Return section identifier."""
        return "quick"

    def get_title(self) -> str:
        """Return section display title."""
        return "Quick Dates"

    def get_icon(self) -> str:
        """Return section icon emoji."""
        return "⚡"

    def load_section(self) -> None:
        """
        Load quick dates section data.

        Quick dates are static (no database query needed),
        so we just return the predefined list.
        """
        logger.info("[QuickSection] Loading quick dates")
        self._loading = False

        # Return quick dates config for widget creation
        # Note: Some sections emit loaded signal; this one completes synchronously
        return self._quick_dates

    def create_content_widget(self, data):
        """
        Create quick dates section widget.

        Args:
            data: Quick dates configuration (list of tuples)

        Returns:
            QWidget: Container with styled date buttons
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Use provided data or fall back to default
        quick_dates = data if data else self._quick_dates

        # Create button for each quick date option
        for date_id, label in quick_dates:
            btn = self._create_date_button(date_id, label)
            layout.addWidget(btn)

        # Add stretch to push buttons to top
        layout.addStretch()

        return container

    def _create_date_button(self, date_id: str, label: str) -> QPushButton:
        """
        Create a styled button for a quick date option.

        Args:
            date_id: Internal date identifier (e.g., "today", "last_7_days")
            label: Display text (e.g., "Today", "Last 7 days")

        Returns:
            QPushButton: Configured button with click handler
        """
        btn = QPushButton(label)

        # Google Photos-inspired styling
        btn.setStyleSheet("""
            QPushButton {
                background-color: #f8f9fa;
                border: 1px solid #e8eaed;
                border-radius: 4px;
                padding: 10px 12px;
                text-align: left;
                font-size: 13px;
                color: #202124;
            }
            QPushButton:hover {
                background-color: #e8f0fe;
                border-color: #1a73e8;
                color: #1a73e8;
            }
            QPushButton:pressed {
                background-color: #d2e3fc;
            }
        """)

        # Set minimum height for better touch targets
        btn.setMinimumHeight(36)

        # Connect click handler using lambda to capture date_id
        btn.clicked.connect(lambda checked, d=date_id: self._on_quick_date_clicked(d))

        return btn

    def _on_quick_date_clicked(self, date_id: str):
        """
        Handle quick date button click.

        Args:
            date_id: Date identifier (e.g., "today", "last_7_days")
        """
        logger.info(f"[QuickSection] Quick date selected: {date_id}")

        # Calculate actual date range based on date_id
        date_range = self._calculate_date_range(date_id)

        logger.debug(f"[QuickSection] Date range calculated: {date_range}")

        # Emit signal with date range string for filtering
        self.quickDateSelected.emit(date_range)

    def _calculate_date_range(self, date_id: str) -> str:
        """
        Calculate date range string for quick date filter.

        Date string formats:
        - Single day: "YYYY-MM-DD" (e.g., "2025-12-16")
        - Date range: "YYYY-MM-DD:YYYY-MM-DD" (e.g., "2025-12-10:2025-12-16")
        - Month: "YYYY-MM" (e.g., "2025-12")
        - Year: "YYYY" (e.g., "2025")

        Args:
            date_id: Date identifier (e.g., "today", "this_month")

        Returns:
            str: Date range string compatible with date filtering
        """
        today = datetime.now().date()

        if date_id == "today":
            # Single day: today
            return today.strftime("%Y-%m-%d")

        elif date_id == "yesterday":
            # Single day: yesterday
            yesterday = today - timedelta(days=1)
            return yesterday.strftime("%Y-%m-%d")

        elif date_id == "last_7_days":
            # Date range: 7 days ago (inclusive) to today
            start = today - timedelta(days=6)  # 6 days ago + today = 7 days
            return f"{start.strftime('%Y-%m-%d')}:{today.strftime('%Y-%m-%d')}"

        elif date_id == "last_30_days":
            # Date range: 30 days ago (inclusive) to today
            start = today - timedelta(days=29)  # 29 days ago + today = 30 days
            return f"{start.strftime('%Y-%m-%d')}:{today.strftime('%Y-%m-%d')}"

        elif date_id == "this_month":
            # Month: current calendar month
            return today.strftime("%Y-%m")

        elif date_id == "last_month":
            # Month: previous calendar month
            # Handle year rollover (e.g., Jan 2025 -> Dec 2024)
            first_of_month = today.replace(day=1)
            last_month_last_day = first_of_month - timedelta(days=1)
            return last_month_last_day.strftime("%Y-%m")

        elif date_id == "this_year":
            # Year: current calendar year
            return today.strftime("%Y")

        elif date_id == "last_year":
            # Year: previous calendar year
            last_year = today.year - 1
            return str(last_year)

        else:
            # Unknown date_id - log warning and default to today
            logger.warning(f"[QuickSection] Unknown date_id: {date_id}, defaulting to today")
            return today.strftime("%Y-%m-%d")

    def cleanup(self):
        """Clean up resources (override from BaseSection)."""
        logger.debug("[QuickSection] Cleanup")
        # Quick section has no resources to clean up
        # (no database connections, no background threads)
        pass
