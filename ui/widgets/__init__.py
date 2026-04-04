"""
UI Widgets Package

Contains reusable UI widgets extracted from MainWindow for better modularity.

Phase 2, Step 2.1-2.3 - Widget Extraction
Extracted from main_window_qt.py to improve maintainability and reduce file size.
"""

from ui.widgets.breadcrumb_navigation import BreadcrumbNavigation
from ui.widgets.backfill_indicator import CompactBackfillIndicator

__all__ = [
    'BreadcrumbNavigation',
    'CompactBackfillIndicator',
]
