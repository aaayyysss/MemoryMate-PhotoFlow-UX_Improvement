# layouts/base_layout.py
# Abstract base class for UI layouts
# Defines the interface that all layouts must implement

from abc import ABC, abstractmethod
from PySide6.QtWidgets import QWidget, QSplitter
from typing import Optional, Dict, Any


class BaseLayout(ABC):
    """
    Abstract base class for MemoryMate-PhotoFlow UI layouts.

    All layout implementations must inherit from this class and implement
    the required abstract methods.

    Responsibilities:
    - Create and manage the main UI layout
    - Handle component placement (sidebar, grid, inspector, etc.)
    - Manage layout-specific settings
    - Provide consistent interface for MainWindow
    """

    def __init__(self, main_window):
        """
        Initialize the layout.

        Args:
            main_window: Reference to MainWindow instance (provides access to settings, db, etc.)
        """
        self.main_window = main_window
        self.settings = main_window.settings if hasattr(main_window, 'settings') else None
        self.db = main_window.db if hasattr(main_window, 'db') else None

        # Components (to be created by subclasses)
        self.sidebar = None
        self.grid = None
        self.inspector = None
        self.main_splitter = None

    @abstractmethod
    def get_name(self) -> str:
        """
        Get the display name of this layout.

        Returns:
            str: Human-readable layout name (e.g., "Current Layout", "Google Photos")
        """
        pass

    @abstractmethod
    def get_id(self) -> str:
        """
        Get the unique identifier for this layout.

        Returns:
            str: Unique layout ID (e.g., "current", "google", "apple")
        """
        pass

    @abstractmethod
    def create_layout(self) -> QWidget:
        """
        Create and return the main layout widget.

        This method should:
        1. Create all UI components (sidebar, grid, etc.)
        2. Arrange them in the desired layout
        3. Connect signals/slots
        4. Return the root widget

        Returns:
            QWidget: The root widget containing the entire layout
        """
        pass

    @abstractmethod
    def get_sidebar(self):
        """
        Get the sidebar component.

        Returns:
            Sidebar widget instance (or None if layout doesn't have a sidebar)
        """
        pass

    @abstractmethod
    def get_grid(self):
        """
        Get the thumbnail grid component.

        Returns:
            ThumbnailGrid widget instance
        """
        pass

    def get_inspector(self):
        """
        Get the inspector/preview panel component.

        Returns:
            Inspector widget instance (or None if layout doesn't have one)
        """
        return self.inspector

    def save_state(self) -> Dict[str, Any]:
        """
        Save layout-specific state (splitter positions, panel visibility, etc.).

        Returns:
            dict: Layout state that can be saved to settings
        """
        state = {}

        # Save splitter positions if main_splitter exists
        if self.main_splitter and isinstance(self.main_splitter, QSplitter):
            state['splitter_sizes'] = self.main_splitter.sizes()

        return state

    def restore_state(self, state: Dict[str, Any]):
        """
        Restore layout-specific state from saved settings.

        Args:
            state: Layout state dictionary from settings
        """
        if not state:
            return

        # Restore splitter positions if available
        if 'splitter_sizes' in state and self.main_splitter:
            try:
                self.main_splitter.setSizes(state['splitter_sizes'])
            except Exception as e:
                print(f"[Layout] Failed to restore splitter sizes: {e}")

    def cleanup(self):
        """
        Clean up resources when switching away from this layout.

        Override this method if your layout needs to do cleanup
        (e.g., stop timers, disconnect signals, etc.)
        """
        pass

    def on_layout_activated(self):
        """
        Called when this layout becomes active.

        Override this method if your layout needs to do initialization
        when it's activated (e.g., start timers, refresh data, etc.)
        """
        pass

    def _on_startup_ready(self):
        """Called by MainWindow after the first paint completes.

        Layouts that defer heavy initialization (DB queries, thumbnail
        loading) until after first paint should override this method.
        Default implementation is a no-op.
        """
        pass

    # ========== PHASE 3 Task 3.1: Project Management ==========

    @abstractmethod
    def set_project(self, project_id: int) -> None:
        """
        Switch to a different project.

        Args:
            project_id: ID of project to display (from projects table)

        Implementation Requirements:
            - Clear existing UI state
            - Update internal project_id
            - Reload data for new project
            - Update sidebar/grid components
            - Emit signals if needed
        """
        pass

    @abstractmethod
    def get_current_project(self) -> Optional[int]:
        """
        Get currently displayed project ID.

        Returns:
            int: Current project ID, or None if no project loaded
        """
        pass

    # ========== PHASE 3 Task 3.1: Data Refresh ==========

    @abstractmethod
    def refresh_after_scan(self) -> None:
        """
        Reload data after scan completes.

        Called by ScanController when:
            - Photo scan finishes
            - Video metadata extraction completes
            - Face detection finishes

        Implementation Requirements:
            - Reload photo/video list from database
            - Update sidebar sections (dates, folders, people)
            - Refresh thumbnail cache
            - Keep current filters active if possible
        """
        pass

    @abstractmethod
    def refresh_thumbnails(self) -> None:
        """
        Reload thumbnails without requerying database.

        Called when:
            - Thumbnail cache is cleared
            - Window is resized
            - User changes thumbnail size setting
        """
        pass

    # ========== PHASE 3 Task 3.1: Filtering ==========

    @abstractmethod
    def filter_by_date(self, year: Optional[int] = None,
                      month: Optional[int] = None,
                      day: Optional[int] = None) -> None:
        """
        Filter displayed items by date.

        Args:
            year: Year filter (e.g., 2024), or None for all years
            month: Month filter (1-12), requires year
            day: Day filter (1-31), requires year and month

        Implementation Requirements:
            - Update timeline/grid to show only matching items
            - Keep sidebar showing all available dates
            - Update "Clear Filter" button visibility
        """
        pass

    @abstractmethod
    def filter_by_folder(self, folder_path: str) -> None:
        """
        Filter displayed items by folder.

        Args:
            folder_path: Folder path string from folder_hierarchy table

        Implementation Requirements:
            - Show only items in specified folder (and subfolders)
            - Keep sidebar showing all folders
            - Highlight active folder in sidebar
        """
        pass

    @abstractmethod
    def filter_by_person(self, person_branch_key: str) -> None:
        """
        Filter displayed items by person (face cluster).

        Args:
            person_branch_key: Person identifier from face_crops table

        Implementation Requirements:
            - Show only photos containing this person
            - Exclude videos (no face detection on videos)
            - Keep sidebar showing all people
        """
        pass

    @abstractmethod
    def clear_filters(self) -> None:
        """
        Remove all active filters and show all items.

        Implementation Requirements:
            - Reset date/folder/person filters to None
            - Reload full photo/video list
            - Hide "Clear Filter" button
            - Update UI to reflect unfiltered state
        """
        pass

    # ========== PHASE 3 Task 3.1: Selection ==========

    @abstractmethod
    def get_selected_paths(self) -> list:
        """
        Get list of currently selected file paths.

        Returns:
            list[str]: Absolute paths to selected photos/videos

        Used by:
            - Delete operation
            - Export operation
            - Bulk tagging
        """
        pass

    @abstractmethod
    def clear_selection(self) -> None:
        """
        Deselect all items.

        Implementation Requirements:
            - Clear internal selection state
            - Update UI to show no selection
            - Emit selection_changed signal if applicable
        """
        pass
