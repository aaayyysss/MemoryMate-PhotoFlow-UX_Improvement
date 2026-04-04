"""
ProjectController - Project Switching Logic

Extracted from main_window_qt.py (Phase 1, Step 1.3)

Responsibilities:
- Project combo box change handling
- Project switching coordination
- Thumbnail cache clearing
- Sidebar/grid project updates

Version: 09.20.00.00
"""

# PHASE 3 Task 3.1: Import BaseLayout for type hints
from typing import Optional
from layouts.base_layout import BaseLayout


class ProjectController:
    """Owns project switching & persistence logic."""
    def __init__(self, main):
        self.main = main

    def on_project_changed(self, idx: int):
        """
        Handle project selection change from combo box.

        Args:
            idx: Index of selected project in combo box

        PHASE 3 Task 3.1: Enhanced with type-safe layout interface.
        """
        pid = self.main.project_combo.itemData(idx)
        if pid is None:
            return
        if self.main.thumbnails:
            self.main.thumbnails.clear()

        # Update legacy sidebar and grid — only trigger full reload when
        # they are actually visible (avoids "reload blocked" warning when
        # Google Layout is active and these widgets are hidden).
        lm = getattr(self.main, 'layout_manager', None)
        is_google = lm and getattr(lm, '_current_layout_id', None) == "google"
        if is_google:
            # Just stash project_id; store subscriptions handle refresh
            # when the user switches back to CurrentLayout.
            if hasattr(self.main, 'sidebar') and self.main.sidebar:
                self.main.sidebar.project_id = pid
            if hasattr(self.main, 'grid') and self.main.grid:
                self.main.grid.project_id = pid
        else:
            self.main.sidebar.set_project(pid)
            self.main.grid.set_project(pid)

        # PHASE 3 Task 3.1: Type-safe layout update using BaseLayout interface
        if hasattr(self.main, 'layout_manager') and self.main.layout_manager:
            current_layout: Optional[BaseLayout] = self.main.layout_manager._current_layout
            if current_layout:
                # Type checker now knows current_layout has set_project() method
                # IDE provides autocomplete for all BaseLayout methods
                current_layout.set_project(pid)
                print(f"[ProjectController] Updated layout to project {pid}")
