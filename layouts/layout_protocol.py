# layouts/layout_protocol.py
# Type protocols for layout components
# Enables static type checking without inheritance (PEP 544)

from typing import Protocol, Optional, runtime_checkable
from PySide6.QtCore import Signal


@runtime_checkable
class LayoutProtocol(Protocol):
    """
    Protocol for layout implementations (for type hints).

    This protocol defines the interface that all layouts should implement,
    allowing for type checking without inheritance using structural subtyping.

    Usage:
        def switch_layout(layout: LayoutProtocol):
            layout.set_project(project_id)
            layout.refresh_after_scan()
    """

    # --- Project Management ---

    def set_project(self, project_id: int) -> None:
        """Switch to a different project."""
        ...

    def get_current_project(self) -> Optional[int]:
        """Get currently displayed project ID."""
        ...

    # --- Data Refresh ---

    def refresh_after_scan(self) -> None:
        """Reload data after scan completes."""
        ...

    def refresh_thumbnails(self) -> None:
        """Reload thumbnails without requerying database."""
        ...

    # --- Filtering ---

    def filter_by_date(self, year: Optional[int] = None,
                      month: Optional[int] = None,
                      day: Optional[int] = None) -> None:
        """Filter displayed items by date."""
        ...

    def filter_by_folder(self, folder_path: str) -> None:
        """Filter displayed items by folder."""
        ...

    def filter_by_person(self, person_branch_key: str) -> None:
        """Filter displayed items by person (face cluster)."""
        ...

    def clear_filters(self) -> None:
        """Remove all active filters and show all items."""
        ...

    # --- Selection ---

    def get_selected_paths(self) -> list:
        """Get list of currently selected file paths."""
        ...

    def clear_selection(self) -> None:
        """Deselect all items."""
        ...

    # --- Layout Management ---

    def cleanup(self) -> None:
        """Clean up resources before layout is destroyed."""
        ...


@runtime_checkable
class SidebarProtocol(Protocol):
    """
    Protocol for sidebar implementations.

    This protocol defines the interface that sidebar components should implement,
    enabling type-safe access to sidebar functionality.
    """

    # Signals (Qt signals for user interactions)
    folder_selected: Signal
    date_selected: Signal
    person_selected: Signal

    def set_project(self, project_id: int) -> None:
        """Update sidebar for new project."""
        ...

    def reload(self) -> None:
        """Reload all sidebar sections from database."""
        ...

    def reload_all_sections(self) -> None:
        """Reload all sidebar sections (alias for reload)."""
        ...


@runtime_checkable
class GridProtocol(Protocol):
    """
    Protocol for thumbnail grid implementations.

    This protocol defines the interface for grid/timeline components that
    display photo/video thumbnails.
    """

    def set_project(self, project_id: int) -> None:
        """Update grid for new project."""
        ...

    def reload(self) -> None:
        """Reload grid contents from database."""
        ...

    def clear_selection(self) -> None:
        """Clear all selected items in grid."""
        ...

    def get_selected_paths(self) -> list:
        """Get list of selected file paths."""
        ...


# Type aliases for convenience
Layout = LayoutProtocol
Sidebar = SidebarProtocol
Grid = GridProtocol
