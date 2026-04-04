# ui/accordion_sidebar/duplicates_section.py
# Duplicates section - Phase 3A implementation

"""
Duplicates Section

Provides duplicate photo management interface in the accordion sidebar.
Shows counts for exact duplicates and similar shots with clickable cards.

Features:
- Real-time duplicate counts (exact duplicates, similar shots)
- Clickable cards that open DuplicatesDialog
- Refresh button to update counts
- Settings button to configure detection

Signals:
    loaded(int, dict): Emitted when duplicate counts are loaded (generation, counts_dict)
"""

import logging
import threading
import time
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Signal, Qt, QObject
from .base_section import BaseSection

logger = logging.getLogger(__name__)

# FIX 2026-02-08: Debounce dialog opening to prevent accidental double-clicks
_last_dialog_open_time = 0
_DIALOG_DEBOUNCE_MS = 500  # 500ms debounce


class DuplicatesSectionSignals(QObject):
    """Signals for duplicates section loading."""
    loaded = Signal(int, dict)  # (generation, counts_dict)
    error = Signal(int, str)    # (generation, error_message)


class DuplicatesSection(BaseSection):
    """
    Duplicates section implementation.

    Displays duplicate photo statistics and provides quick access to duplicate management.

    Signals:
        loaded(int, dict): Emitted when counts are loaded from database
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = DuplicatesSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)

    def get_section_id(self) -> str:
        """Return section identifier."""
        return "duplicates"

    def get_title(self) -> str:
        """Return section display title."""
        return "Duplicates"

    def get_icon(self) -> str:
        """Return section icon emoji."""
        return "⚡"

    def load_section(self) -> None:
        """
        Load duplicate counts from database (async).

        Queries:
        - Exact duplicate count from AssetRepository
        - Similar shot stack count from StackRepository

        Emits signals.loaded with counts.
        """
        if not self.project_id:
            logger.warning("[DuplicatesSection] Cannot load - no project_id")
            return

        # Increment generation
        self._generation += 1
        current_gen = self._generation
        self._loading = True
        logger.debug(f"[DuplicatesSection] Loading (gen={current_gen})")

        def work():
            """Background worker to load duplicate counts."""
            try:
                from repository.asset_repository import AssetRepository
                from repository.stack_repository import StackRepository
                from repository.base_repository import DatabaseConnection

                # Initialize repositories
                db_conn = DatabaseConnection()
                asset_repo = AssetRepository(db_conn)
                stack_repo = StackRepository(db_conn)

                # Get exact duplicate counts (assets with 2+ instances)
                exact_assets = asset_repo.list_duplicate_assets(self.project_id, min_instances=2)
                exact_photo_count = sum(asset['instance_count'] for asset in exact_assets)
                exact_group_count = len(exact_assets)

                # Get similar shot stacks (type="similar")
                similar_stacks = stack_repo.list_stacks(self.project_id, stack_type="similar", limit=10000)
                similar_photo_count = 0
                for stack in similar_stacks:
                    member_count = stack_repo.count_stack_members(self.project_id, stack['stack_id'])
                    similar_photo_count += member_count
                similar_group_count = len(similar_stacks)

                counts = {
                    'exact_photos': exact_photo_count,
                    'exact_groups': exact_group_count,
                    'similar_photos': similar_photo_count,
                    'similar_groups': similar_group_count
                }

                return counts

            except Exception as e:
                logger.error(f"[DuplicatesSection] Error loading duplicates: {e}", exc_info=True)
                # Return empty counts on error
                return {
                    'exact_photos': 0,
                    'exact_groups': 0,
                    'similar_photos': 0,
                    'similar_groups': 0
                }

        # Run in thread
        def on_complete():
            try:
                counts = work()
                # Only emit if generation still matches
                if current_gen == self._generation:
                    self.signals.loaded.emit(current_gen, counts)
                else:
                    logger.debug(f"[DuplicatesSection] Discarding stale data (gen={current_gen} vs {self._generation})")
            except Exception as e:
                logger.error(f"[DuplicatesSection] Error in worker thread: {e}", exc_info=True)
                self.signals.error.emit(current_gen, str(e))

        threading.Thread(target=on_complete, daemon=True).start()

    def _on_data_loaded(self, generation: int, data: dict):
        """Handle data loaded signal (called from main thread via signal)."""
        self._loading = False
        # Data is already checked for staleness by signal emission code
        # Just store it for later use if needed
        self._counts = data

    def create_content_widget(self, counts: dict) -> QWidget:
        """
        Create content widget with duplicate cards and action buttons.

        Args:
            counts: Dictionary with duplicate counts

        Returns:
            QWidget: Content widget with duplicate statistics
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(12)

        # Info text
        info_label = QLabel("Manage and organize duplicate photos")
        info_label.setStyleSheet("color: #666; font-size: 9pt; padding-bottom: 8px;")
        layout.addWidget(info_label)

        # Create cards with actual counts
        exact_card = self._create_duplicate_card(
            icon="🔍",
            title="Exact Duplicates",
            photo_count=counts.get('exact_photos', 0),
            group_count=counts.get('exact_groups', 0),
            duplicate_type="exact"
        )
        layout.addWidget(exact_card)

        similar_card = self._create_duplicate_card(
            icon="📸",
            title="Similar Shots",
            photo_count=counts.get('similar_photos', 0),
            group_count=counts.get('similar_groups', 0),
            duplicate_type="similar"
        )
        layout.addWidget(similar_card)

        layout.addStretch(1)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        btn_refresh = QPushButton("🔄")
        btn_refresh.setToolTip("Refresh duplicate counts")
        btn_refresh.setFixedSize(32, 32)
        btn_refresh.clicked.connect(self._on_refresh_clicked)
        button_layout.addWidget(btn_refresh)

        btn_settings = QPushButton("⚙️")
        btn_settings.setToolTip("Configure duplicate detection")
        btn_settings.setFixedSize(32, 32)
        btn_settings.clicked.connect(self._on_settings_clicked)
        button_layout.addWidget(btn_settings)

        button_layout.addStretch(1)

        layout.addLayout(button_layout)

        return container

    def _create_duplicate_card(self, icon: str, title: str, photo_count: int, group_count: int, duplicate_type: str) -> QWidget:
        """Create a clickable card for a duplicate type."""
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
                border: 1px solid #ddd;
                border-radius: 6px;
                padding: 8px;
            }
            QWidget:hover {
                background-color: #e9ecef;
                border-color: #2196F3;
            }
        """)
        card.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Title row
        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 16pt;")
        title_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold; font-size: 10pt;")
        title_layout.addWidget(title_label)
        title_layout.addStretch(1)

        layout.addLayout(title_layout)

        # Count label with actual counts
        if group_count > 0:
            text = f"{photo_count} photos • {group_count} groups"
            style = "color: #555; font-size: 9pt; padding-left: 32px;"
        else:
            text = "No duplicates found"
            style = "color: #999; font-size: 9pt; padding-left: 32px;"

        count_label = QLabel(text)
        count_label.setStyleSheet(style)
        layout.addWidget(count_label)

        # Store properties for click handler
        card.setProperty("duplicate_type", duplicate_type)
        card.setProperty("has_duplicates", group_count > 0)

        # Make clickable
        card.mousePressEvent = lambda event: self._on_card_clicked(duplicate_type, group_count > 0)

        return card

    def _on_card_clicked(self, duplicate_type: str, has_duplicates: bool):
        """
        Handle click on duplicate card.

        FIX 2026-02-08: Added debouncing and changed from exec() to show().
        - Debouncing prevents accidental double-clicks from opening multiple dialogs
        - show() is non-blocking, allowing the UI to remain responsive
        - Dialog's finished signal triggers section refresh
        """
        global _last_dialog_open_time

        if not has_duplicates:
            return

        # FIX 2026-02-08: Debounce to prevent rapid double-clicks
        current_time = time.time() * 1000  # Convert to milliseconds
        if current_time - _last_dialog_open_time < _DIALOG_DEBOUNCE_MS:
            logger.debug("[DuplicatesSection] Ignoring click - debounce active")
            return
        _last_dialog_open_time = current_time

        # Get main window through parent chain
        main_window = None
        if self.parent():
            # AccordionSidebar is our parent
            accordion = self.parent()
            if hasattr(accordion, 'window'):
                main_window = accordion.window()
            elif hasattr(accordion, 'parent') and accordion.parent():
                main_window = accordion.parent()

        # Route to appropriate dialog based on duplicate type
        if duplicate_type == "exact":
            # Import DuplicatesDialog for exact duplicates
            from layouts.google_components.duplicates_dialog import DuplicatesDialog

            # Open duplicates dialog
            dialog = DuplicatesDialog(
                project_id=self.project_id,
                parent=main_window
            )

            # FIX 2026-02-08: Use show() instead of exec() for non-blocking UI
            # Connect finished signal to refresh section when dialog closes
            dialog.finished.connect(lambda: self.load_section())
            dialog.show()

        elif duplicate_type == "similar":
            # Import StackBrowserDialog for similar shots
            from layouts.google_components.stack_view_dialog import StackBrowserDialog

            # Open stack browser dialog for similar shots
            dialog = StackBrowserDialog(
                project_id=self.project_id,
                stack_type="similar",
                parent=main_window
            )

            # FIX 2026-02-08: Use show() instead of exec() for non-blocking UI
            # Connect finished signal to refresh section when dialog closes
            dialog.finished.connect(lambda: self.load_section())
            dialog.show()

    def _on_refresh_clicked(self):
        """Handle refresh button click."""
        self.load_section()

    def _on_settings_clicked(self):
        """Handle settings button click."""
        try:
            # Get main window through parent chain
            main_window = None
            if self.parent():
                # AccordionSidebar is our parent
                accordion = self.parent()
                if hasattr(accordion, 'window'):
                    main_window = accordion.window()
                elif hasattr(accordion, 'parent') and accordion.parent():
                    main_window = accordion.parent()

            # Import preferences dialog
            from preferences_dialog import PreferencesDialog
            from settings_manager_qt import SettingsManager

            # Get settings manager
            settings = SettingsManager()

            # Open preferences at Duplicate Management tab
            dialog = PreferencesDialog(settings=settings, parent=main_window)

            # Try to switch to duplicate management tab (tab index 4)
            if hasattr(dialog, 'tabs'):
                dialog.tabs.setCurrentIndex(4)

            dialog.exec()

            # Refresh duplicates section after settings change
            self.load_section()

        except Exception as e:
            logger.error(f"Failed to open duplicate settings: {e}", exc_info=True)

    def set_project(self, project_id: int | None) -> None:
        """
        Set the project ID and reload section.

        Args:
            project_id: Project ID to load duplicates for
        """
        if self.project_id != project_id:
            self.project_id = project_id
            logger.debug(f"[DuplicatesSection] Project changed to {project_id}")

    def set_db(self, db) -> None:
        """Set database instance (compatibility with other sections)."""
        # Not used - we create per-thread database instances
        pass
