# layouts/current_layout.py
# Current/Classic layout - the existing MemoryMate-PhotoFlow layout
# 2-panel design: Sidebar (left) | Grid+ChipBar (right)

from PySide6.QtWidgets import QWidget, QSplitter, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt
from typing import Optional
from .base_layout import BaseLayout


class CurrentLayout(BaseLayout):
    """
    Current/Classic MemoryMate-PhotoFlow layout.

    Structure:
    ┌────────────────────────────────────────────┐
    │  [Toolbar]                                 │
    ├──────────┬─────────────────────────────────┤
    │          │  [Chip Bar: ⭐👤🎬📅]          │
    │ Sidebar  │  ┌───────────────────────────┐ │
    │  ├─Tree  │  │                           │ │
    │  ├─Tags  │  │    Thumbnail Grid         │ │
    │  ├─Date  │  │                           │ │
    │  └─Videos│  └───────────────────────────┘ │
    └──────────┴─────────────────────────────────┘

    Features:
    - Collapsible sidebar (tree/tabs)
    - Chip filter bar (favorites, people, videos, etc.)
    - Thumbnail grid with variable sizes
    - No inspector panel (double-click for preview)
    """

    def get_name(self) -> str:
        return "Current Layout"

    def get_id(self) -> str:
        return "current"

    def create_layout(self) -> QWidget:
        """
        Create the current/classic layout.

        NOTE: For now, this returns None and the MainWindow uses its existing
        layout code. In a future refactoring, we'll move the actual layout
        creation code here.
        """
        # TODO: Refactor MainWindow's layout code into this method
        # For now, signal that MainWindow should use its existing layout
        return None

    def get_sidebar(self):
        """Get sidebar component from MainWindow."""
        return self.main_window.sidebar if hasattr(self.main_window, 'sidebar') else None

    def get_grid(self):
        """Get grid component from MainWindow."""
        return self.main_window.grid if hasattr(self.main_window, 'grid') else None

    def on_layout_activated(self):
        """
        Called when Current layout becomes active.

        CRITICAL FIX: Refresh sidebar and grid to ensure all branches and sections
        show updated data after scanning in other layouts (e.g., Google layout).

        Uses QTimer.singleShot to defer reload, allowing widget visibility to update
        before reload() is called (prevents "widget not visible" blocking).
        """
        print("[CurrentLayout] Layout activated - scheduling deferred refresh")

        # CRITICAL FIX: Defer reload to allow widget visibility to update
        # The sidebar.reload() checks isVisible(), which might be False during
        # layout switching. Deferring by 100ms ensures widget is fully shown.
        from PySide6.QtCore import QTimer

        def deferred_reload():
            print("[CurrentLayout] Executing deferred reload...")

            # Refresh sidebar to show updated folder/date/tag counts
            sidebar = self.get_sidebar()
            if sidebar and hasattr(sidebar, 'reload'):
                try:
                    print("[CurrentLayout] Reloading sidebar...")
                    sidebar.reload()
                    print("[CurrentLayout] ✓ Sidebar reload completed")
                except Exception as e:
                    print(f"[CurrentLayout] ⚠️ Error reloading sidebar: {e}")

            # Refresh grid to show updated thumbnails
            grid = self.get_grid()
            if grid and hasattr(grid, 'reload'):
                try:
                    print("[CurrentLayout] Reloading grid...")
                    grid.reload()
                    print("[CurrentLayout] ✓ Grid reload completed")
                except Exception as e:
                    print(f"[CurrentLayout] ⚠️ Error reloading grid: {e}")

        # Schedule deferred reload (100ms delay to allow widget to become visible)
        QTimer.singleShot(100, deferred_reload)

    # ========== PHASE 3 Task 3.1: BaseLayout Interface Implementation ==========

    def set_project(self, project_id: int) -> None:
        """Delegate to MainWindow's sidebar and grid."""
        sidebar = self.get_sidebar()
        if sidebar and hasattr(sidebar, 'set_project'):
            sidebar.set_project(project_id)

        grid = self.get_grid()
        if grid and hasattr(grid, 'set_project'):
            grid.set_project(project_id)

    def get_current_project(self) -> Optional[int]:
        """Get current project from MainWindow."""
        if hasattr(self.main_window, 'current_project_id'):
            return self.main_window.current_project_id
        return None

    def refresh_after_scan(self) -> None:
        """Delegate to sidebar and grid reload."""
        sidebar = self.get_sidebar()
        if sidebar and hasattr(sidebar, 'reload'):
            sidebar.reload()

        grid = self.get_grid()
        if grid and hasattr(grid, 'reload'):
            grid.reload()

    def refresh_thumbnails(self) -> None:
        """Delegate to grid reload."""
        grid = self.get_grid()
        if grid and hasattr(grid, 'reload'):
            grid.reload()

    def filter_by_date(self, year: Optional[int] = None,
                      month: Optional[int] = None,
                      day: Optional[int] = None) -> None:
        """Not implemented for Current layout."""
        print(f"[CurrentLayout] filter_by_date not implemented (year={year}, month={month}, day={day})")

    def filter_by_folder(self, folder_path: str) -> None:
        """Not implemented for Current layout."""
        print(f"[CurrentLayout] filter_by_folder not implemented (folder={folder_path})")

    def filter_by_person(self, person_branch_key: str) -> None:
        """Not implemented for Current layout."""
        print(f"[CurrentLayout] filter_by_person not implemented (person={person_branch_key})")

    def clear_filters(self) -> None:
        """Not implemented for Current layout."""
        print("[CurrentLayout] clear_filters not implemented")

    def get_selected_paths(self) -> list:
        """Get selected paths from grid."""
        grid = self.get_grid()
        if grid and hasattr(grid, 'get_selected_paths'):
            return grid.get_selected_paths()
        return []

    def clear_selection(self) -> None:
        """Delegate to grid clear_selection."""
        grid = self.get_grid()
        if grid and hasattr(grid, 'clear_selection'):
            grid.clear_selection()
