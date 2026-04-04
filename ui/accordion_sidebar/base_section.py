# ui/accordion_sidebar/base_section.py
# Abstract base class for accordion sidebar sections

from abc import ABC, ABCMeta, abstractmethod
from PySide6.QtCore import QObject, Signal, QThread
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


# CRITICAL FIX: Combine QObject metaclass with ABCMeta to avoid metaclass conflict
# QObject has its own metaclass, ABC uses ABCMeta - we need both
class QABCMeta(type(QObject), ABCMeta):
    """
    Combined metaclass for QObject and ABC compatibility.

    This resolves the metaclass conflict when inheriting from both QObject and ABC.
    Required for classes that need both Qt Signals (QObject) and abstract methods (ABC).
    """
    pass


class BaseSection(QObject, ABC, metaclass=QABCMeta):
    """
    Abstract base class for accordion sidebar sections.

    IMPORTANT: Inherits from QObject to support Qt Signals.
    All sections (Folders, Dates, Videos, etc.) must implement this interface
    to ensure consistent behavior and enable independent testing.

    Responsibilities:
    - Load data from database in background thread
    - Emit signals with loaded data
    - Handle project switching
    - Track generation for staleness checking
    - Manage thread-safe database access

    Each section should:
    1. Create per-thread database instances (thread-safe)
    2. Use generation tokens to discard stale results
    3. Emit signals for UI updates (thread-safe Qt signals)
    4. Clean up resources properly
    """

    def __init__(self, parent: Optional[QObject] = None):
        """
        Initialize base section.

        Args:
            parent: Parent QObject (typically the main AccordionSidebar)
        """
        # CRITICAL: Initialize QObject first
        super().__init__(parent)

        self.project_id: Optional[int] = None
        self.db_path: str = "reference_data.db"
        self._generation: int = 0  # Generation counter for staleness checking
        self._loading: bool = False
        self._worker_thread: Optional[QThread] = None

    @abstractmethod
    def get_section_id(self) -> str:
        """
        Get unique section identifier.

        Returns:
            str: Section ID (e.g., "people", "dates", "folders")
        """
        pass

    @abstractmethod
    def get_title(self) -> str:
        """
        Get display title for section header.

        Returns:
            str: Human-readable section title (e.g., "People", "Dates")
        """
        pass

    @abstractmethod
    def get_icon(self) -> str:
        """
        Get icon emoji for section header.

        Returns:
            str: Unicode emoji character (e.g., "👤", "📅", "📁")
        """
        pass

    @abstractmethod
    def load_section(self) -> None:
        """
        Load section data from database (async).

        Implementation Requirements:
        - Increment generation counter
        - Start background worker thread
        - Query database with per-thread ReferenceDB instance
        - Emit loaded signal with generation number
        - Handle errors gracefully

        Thread Safety:
        - Create ReferenceDB() instance inside worker thread
        - Use try/finally to ensure cleanup
        - Emit signals with Qt.QueuedConnection (default for cross-thread)

        Example:
            def load_section(self):
                self._generation += 1
                current_gen = self._generation
                self._loading = True

                def worker():
                    db = ReferenceDB()
                    try:
                        data = db.query(...)
                        self.dataLoaded.emit(current_gen, data)
                    finally:
                        db.close()

                thread = QThread()
                thread.run = worker
                thread.start()
        """
        pass

    @abstractmethod
    def create_content_widget(self, data: Any) -> Optional[Any]:
        """
        Create UI widget for section content.

        Args:
            data: Loaded data from database (format depends on section)

        Returns:
            QWidget: Widget to display in section content area, or None if no data

        Implementation Notes:
        - Called in main thread after data loaded
        - Should create fresh widget each time
        - Return None to show empty state
        """
        pass

    # --- Common Methods (optional to override) ---

    def set_project(self, project_id: int) -> None:
        """
        Update section for new project.

        Args:
            project_id: ID of project to display

        Default Implementation:
        - Stores project_id
        - Increments generation to invalidate pending loads
        - Does NOT trigger reload (accordion does that on expansion)

        Override if you need custom behavior (e.g., clear UI immediately).
        """
        self.project_id = project_id
        self._generation += 1  # Invalidate any pending loads

    def is_loading(self) -> bool:
        """
        Check if section is currently loading.

        Returns:
            bool: True if loading in progress
        """
        return self._loading

    def cancel_pending_load(self) -> None:
        """
        Cancel any pending load operation.

        Increments generation to cause pending workers to discard results.
        """
        self._generation += 1
        self._loading = False

    def cleanup(self) -> None:
        """
        Clean up resources before section is destroyed.

        Default Implementation:
        - Cancels pending loads
        - Cleans up worker thread if exists

        Override to add custom cleanup (e.g., close connections).
        """
        self.cancel_pending_load()

        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(1000)  # Wait up to 1 second

    def _on_data_loaded(self, generation: int, data: Any) -> Any:
        """
        Internal callback when data is loaded.

        Always resets _loading (the background thread is done regardless of
        whether the data is stale).  Without this, a generation bump during
        loading permanently locks the section in ``is_loading() == True``.

        NOTE: This method should NOT call create_content_widget() because
        AccordionSidebar._on_section_loaded() already handles widget creation
        when it receives the loaded signal. Calling it here would cause
        duplicate widget creation (and double "Groups sub-section created" logs).
        """
        self._loading = False

        if generation != self._generation:
            logger.debug(f"[{self.get_section_id()}] Discarding stale data (gen {generation} vs {self._generation})")
            return None

        # AccordionSidebar handles create_content_widget via _on_section_loaded
        return data


class SectionLoadSignals(QObject):
    """
    Signals for section data loading.

    Separated from BaseSection since QObject+ABC don't mix well.
    Each section should create an instance of this.
    """

    # (generation, data) - Emitted when data is loaded
    loaded = Signal(int, object)

    # (generation, error_message) - Emitted on error
    error = Signal(int, str)
