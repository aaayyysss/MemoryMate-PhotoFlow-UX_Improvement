# embedding_scope_widget.py
# Version 1.01.00.00 dated 20260130

"""
Embedding Scope Selection Widget
Reusable widget for selecting which photos to process for embedding extraction.

FEATURE: Comprehensive scope selection with:
- All photos
- Specific folders (checkbox tree)
- Date range picker
- Recent photos (30/60/90 days)
- Custom quantity slider
- Skip already processed checkbox
- Processing order preference

Best practices from:
- Google Photos: Simple defaults, smart suggestions
- iPhone Photos: Automatic background processing
- Lightroom: Professional folder/collection selection
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton,
    QButtonGroup, QTreeWidget, QTreeWidgetItem, QDateEdit,
    QSlider, QCheckBox, QGroupBox, QComboBox, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QDate, Signal

from reference_db import ReferenceDB
from logging_config import get_logger

logger = get_logger(__name__)


class EmbeddingScopeWidget(QWidget):
    """
    Reusable widget for selecting embedding extraction scope.

    Can be embedded in:
    - Duplicate Detection Dialog
    - Similar Photos Dialog
    - Batch Embedding Dialog
    - Any future embedding-related feature

    Signals:
        scopeChanged: Emitted when selection changes, contains (photo_ids, photo_count)
    """

    # Signal emitted when scope selection changes
    # Emits tuple of (list of photo IDs, total count)
    scopeChanged = Signal(list, int)

    def __init__(self, project_id: int, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.db = ReferenceDB()

        # Data
        self.all_photos: List[Dict[str, Any]] = []
        self.folders: List[Dict[str, Any]] = []
        self.selected_photo_ids: List[int] = []

        # UI state
        self.scope_mode = "all"  # "all", "folders", "dates", "recent", "quantity"

        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        """Setup the user interface."""
        # CRITICAL: Size policy allows this widget to be scrollable within parent
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Scope selection group
        scope_group = self._create_scope_selection()
        scope_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        layout.addWidget(scope_group)

        # Options group
        options_group = self._create_options_group()
        options_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(options_group)

        # Summary panel
        self.summary_panel = self._create_summary_panel()
        self.summary_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.summary_panel)

    def _create_scope_selection(self) -> QGroupBox:
        """Create scope selection radio buttons and options."""
        group = QGroupBox("📸 Photo Selection")
        group.setStyleSheet(self._groupbox_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 14, 10, 10)

        # Radio button group
        self.button_group = QButtonGroup(self)

        # Option 1: All Photos
        self.radio_all = QRadioButton("All Photos")
        self.radio_all.setChecked(True)
        self.radio_all.toggled.connect(
            lambda checked: self._on_scope_changed("all") if checked else None
        )
        self.button_group.addButton(self.radio_all)
        layout.addWidget(self.radio_all)

        self.label_all_count = QLabel()
        self.label_all_count.setStyleSheet("color: #666; margin-left: 25px; font-size: 9pt;")
        layout.addWidget(self.label_all_count)

        # Option 2: Specific Folders
        self.radio_folders = QRadioButton("Specific Folders")
        self.radio_folders.toggled.connect(
            lambda checked: self._on_scope_changed("folders") if checked else None
        )
        self.button_group.addButton(self.radio_folders)
        layout.addWidget(self.radio_folders)

        # Folder tree (hidden by default) - compact height
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Select Folders")
        self.folder_tree.setMaximumHeight(120)
        self.folder_tree.setStyleSheet("font-size: 9pt;")
        self.folder_tree.hide()
        self.folder_tree.itemChanged.connect(self._on_folder_selection_changed)
        layout.addWidget(self.folder_tree)

        # Option 3: Date Range
        self.radio_dates = QRadioButton("Date Range")
        self.radio_dates.toggled.connect(
            lambda checked: self._on_scope_changed("dates") if checked else None
        )
        self.button_group.addButton(self.radio_dates)
        layout.addWidget(self.radio_dates)

        # Date range picker (hidden by default)
        self.date_widget = QWidget()
        date_layout = QHBoxLayout(self.date_widget)
        date_layout.setContentsMargins(25, 0, 0, 0)

        date_layout.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addYears(-1))
        self.date_from.dateChanged.connect(self._update_summary)
        date_layout.addWidget(self.date_from)

        date_layout.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(self._update_summary)
        date_layout.addWidget(self.date_to)

        date_layout.addStretch()
        self.date_widget.hide()
        layout.addWidget(self.date_widget)

        # Option 4: Recent Photos
        self.radio_recent = QRadioButton("Recent Photos")
        self.radio_recent.toggled.connect(
            lambda checked: self._on_scope_changed("recent") if checked else None
        )
        self.button_group.addButton(self.radio_recent)
        layout.addWidget(self.radio_recent)

        # Recent photos dropdown (hidden by default)
        self.recent_widget = QWidget()
        recent_layout = QHBoxLayout(self.recent_widget)
        recent_layout.setContentsMargins(25, 0, 0, 0)

        self.recent_combo = QComboBox()
        self.recent_combo.addItems([
            "Last 7 days",
            "Last 30 days",
            "Last 60 days",
            "Last 90 days",
            "Last 6 months",
            "Last year"
        ])
        self.recent_combo.setCurrentIndex(1)  # Default: 30 days
        self.recent_combo.currentIndexChanged.connect(self._update_summary)
        recent_layout.addWidget(self.recent_combo)
        recent_layout.addStretch()

        self.recent_widget.hide()
        layout.addWidget(self.recent_widget)

        # Option 5: Custom Quantity
        self.radio_quantity = QRadioButton("Custom Quantity")
        self.radio_quantity.toggled.connect(
            lambda checked: self._on_scope_changed("quantity") if checked else None
        )
        self.button_group.addButton(self.radio_quantity)
        layout.addWidget(self.radio_quantity)

        # Quantity slider (hidden by default)
        self.quantity_widget = QWidget()
        quantity_layout = QVBoxLayout(self.quantity_widget)
        quantity_layout.setContentsMargins(25, 0, 0, 0)

        self.quantity_slider = QSlider(Qt.Horizontal)
        self.quantity_slider.setMinimum(1)
        self.quantity_slider.setMaximum(100)
        self.quantity_slider.setValue(50)
        self.quantity_slider.valueChanged.connect(self._update_summary)
        quantity_layout.addWidget(self.quantity_slider)

        self.quantity_label = QLabel()
        self.quantity_label.setStyleSheet("color: #1a73e8; font-weight: bold;")
        quantity_layout.addWidget(self.quantity_label)

        self.quantity_widget.hide()
        layout.addWidget(self.quantity_widget)

        return group

    def _create_options_group(self) -> QGroupBox:
        """Create additional options group."""
        group = QGroupBox("⚙️ Options")
        group.setStyleSheet(self._groupbox_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 14, 10, 10)

        # Skip already processed checkbox
        self.chk_skip_processed = QCheckBox("Skip photos that already have embeddings")
        self.chk_skip_processed.setChecked(True)
        self.chk_skip_processed.setToolTip(
            "Skip photos that already have CLIP embeddings.\n"
            "Uncheck to regenerate all embeddings (slower)."
        )
        self.chk_skip_processed.toggled.connect(self._update_summary)
        layout.addWidget(self.chk_skip_processed)

        # Processing order
        order_layout = QHBoxLayout()
        order_layout.addWidget(QLabel("Process order:"))

        self.order_combo = QComboBox()
        self.order_combo.addItems([
            "Newest first",
            "Oldest first",
            "Random"
        ])
        self.order_combo.setToolTip(
            "Order in which photos will be processed.\n"
            "Newest first: Start with recent photos\n"
            "Oldest first: Start with oldest photos\n"
            "Random: Process in random order"
        )
        order_layout.addWidget(self.order_combo)
        order_layout.addStretch()

        layout.addLayout(order_layout)

        return group

    def _create_summary_panel(self) -> QGroupBox:
        """Create summary panel showing selection statistics."""
        group = QGroupBox("📊 Summary")
        group.setStyleSheet(self._groupbox_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(2)
        layout.setContentsMargins(10, 14, 10, 10)

        self.summary_selected = QLabel()
        self.summary_selected.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_selected)

        self.summary_processed = QLabel()
        self.summary_processed.setStyleSheet("color: #666;")
        layout.addWidget(self.summary_processed)

        self.summary_new = QLabel()
        self.summary_new.setStyleSheet("color: #1a73e8; font-weight: bold;")
        layout.addWidget(self.summary_new)

        self.summary_time = QLabel()
        self.summary_time.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.summary_time)

        return group

    def _load_data(self):
        """Load photos and folders from database."""
        try:
            # Try to load all photos with dates - use method that exists in ReferenceDB
            try:
                self.all_photos = self.db.get_all_paths_with_dates(self.project_id) or []
            except AttributeError:
                # Method doesn't exist, try alternative
                try:
                    from repository.photo_repository import PhotoRepository
                    photo_repo = PhotoRepository()
                    photos = photo_repo.get_all_photos_for_project(self.project_id)
                    self.all_photos = [{'id': p.get('id') or p.get('photo_id'),
                                        'path': p.get('file_path') or p.get('path'),
                                        'date': p.get('date_taken') or p.get('created_at')}
                                       for p in (photos or [])]
                except Exception as e:
                    logger.warning(f"Failed to load photos via repository: {e}")
                    self.all_photos = []

            # Load folders
            try:
                self.folders = self.db.get_folders_with_counts(self.project_id) or []
            except Exception as e:
                logger.debug(f"Could not load folders: {e}")
                self.folders = []

            # Populate folder tree
            self._populate_folder_tree()

            # Update labels
            self.label_all_count.setText(f"{len(self.all_photos):,} photos in library")

            # Update summary
            self._update_summary()

        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            self.label_all_count.setText("Error loading photos")

    def _groupbox_style(self) -> str:
        """Return compact GroupBox styling."""
        return """
            QGroupBox {
                font-weight: bold;
                font-size: 9pt;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 12px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 2px 6px;
                background-color: #fafafa;
            }
        """

    def _populate_folder_tree(self):
        """Populate folder tree with checkboxes in hierarchical structure."""
        self.folder_tree.clear()

        if not self.folders:
            item = QTreeWidgetItem(self.folder_tree)
            item.setText(0, "(No folders found)")
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            return

        # Build tree recursively
        def add_folder(parent_item, parent_id):
            children = [f for f in self.folders if f.get('parent_id') == parent_id]

            for folder in children:
                item = QTreeWidgetItem(parent_item if parent_item else self.folder_tree)
                count = folder.get('count', 0)
                name = folder.get('name', 'Unknown')
                item.setText(0, f"{name} ({count:,} photos)")
                item.setCheckState(0, Qt.Unchecked)
                item.setData(0, Qt.UserRole, folder.get('id'))
                item.setExpanded(True)

                # Recursively add children
                add_folder(item, folder.get('id'))

        # Start with root folders (parent_id is None)
        add_folder(None, None)

    def _on_scope_changed(self, mode: str):
        """Handle scope mode change."""
        self.scope_mode = mode

        # Show/hide relevant widgets
        self.folder_tree.setVisible(mode == "folders")
        self.date_widget.setVisible(mode == "dates")
        self.recent_widget.setVisible(mode == "recent")
        self.quantity_widget.setVisible(mode == "quantity")

        self._update_summary()

    def _on_folder_selection_changed(self, item: QTreeWidgetItem, column: int):
        """Handle folder checkbox change."""
        self._update_summary()

    def _get_recent_days(self) -> int:
        """Get number of days based on recent combo selection."""
        index = self.recent_combo.currentIndex()
        days_map = {
            0: 7,
            1: 30,
            2: 60,
            3: 90,
            4: 180,
            5: 365
        }
        return days_map.get(index, 30)

    def _update_summary(self):
        """Update summary panel with current selection statistics."""
        # Calculate selected photos based on mode
        if self.scope_mode == "all":
            self.selected_photo_ids = [p.get('id') or p.get('photo_id') for p in self.all_photos]

        elif self.scope_mode == "folders":
            # Get checked folder IDs recursively
            selected_folder_ids = []

            def get_checked_folders(item):
                if item.checkState(0) == Qt.Checked:
                    folder_id = item.data(0, Qt.UserRole)
                    if folder_id is not None:
                        selected_folder_ids.append(folder_id)

                for i in range(item.childCount()):
                    get_checked_folders(item.child(i))

            for i in range(self.folder_tree.topLevelItemCount()):
                get_checked_folders(self.folder_tree.topLevelItem(i))

            # Get photos for selected folders
            if selected_folder_ids:
                try:
                    photo_ids = self.db.get_photo_ids_for_folders(self.project_id, selected_folder_ids)
                    self.selected_photo_ids = photo_ids or []
                except:
                    # Fallback: filter all_photos by folder
                    self.selected_photo_ids = []
            else:
                self.selected_photo_ids = []

        elif self.scope_mode == "dates":
            start_date = self.date_from.date().toPython()
            end_date = self.date_to.date().toPython()

            self.selected_photo_ids = []
            for p in self.all_photos:
                photo_date = p.get('date') or p.get('date_taken')
                if photo_date:
                    if isinstance(photo_date, datetime):
                        photo_date = photo_date.date()
                    if start_date <= photo_date <= end_date:
                        self.selected_photo_ids.append(p.get('id') or p.get('photo_id'))

        elif self.scope_mode == "recent":
            days = self._get_recent_days()
            cutoff_date = datetime.now() - timedelta(days=days)

            self.selected_photo_ids = []
            for p in self.all_photos:
                photo_date = p.get('date') or p.get('date_taken')
                if photo_date:
                    if isinstance(photo_date, datetime):
                        if photo_date >= cutoff_date:
                            self.selected_photo_ids.append(p.get('id') or p.get('photo_id'))
                    else:
                        # date object
                        if photo_date >= cutoff_date.date():
                            self.selected_photo_ids.append(p.get('id') or p.get('photo_id'))

        elif self.scope_mode == "quantity":
            total = len(self.all_photos)
            percentage = self.quantity_slider.value()
            count = int(total * percentage / 100)

            # Get photo IDs based on order preference
            order = self.order_combo.currentIndex()
            sorted_photos = self.all_photos.copy()

            if order == 0:  # Newest first
                sorted_photos.sort(key=lambda p: p.get('date') or datetime.min, reverse=True)
            elif order == 1:  # Oldest first
                sorted_photos.sort(key=lambda p: p.get('date') or datetime.max)
            # order == 2: Random - keep original order

            self.selected_photo_ids = [
                p.get('id') or p.get('photo_id')
                for p in sorted_photos[:count]
            ]
            self.quantity_label.setText(f"{count:,} photos ({percentage}%)")

        selected_count = len(self.selected_photo_ids)

        # Check which photos already have embeddings
        already_processed = 0
        if self.chk_skip_processed.isChecked() and self.selected_photo_ids:
            try:
                processed_ids = self.db.get_photo_ids_with_embeddings(self.project_id)
                if processed_ids:
                    processed_set = set(processed_ids)
                    already_processed = len([pid for pid in self.selected_photo_ids if pid in processed_set])
            except Exception as e:
                logger.debug(f"Could not check processed photos: {e}")

        new_to_process = selected_count - already_processed

        # Estimate time (average 2-3 seconds per photo for CLIP)
        avg_time_per_photo = 2.5  # seconds
        estimated_seconds = new_to_process * avg_time_per_photo

        if estimated_seconds < 60:
            time_str = f"~{int(estimated_seconds)} seconds"
        elif estimated_seconds < 3600:
            time_str = f"~{int(estimated_seconds / 60)} minutes"
        else:
            hours = int(estimated_seconds / 3600)
            minutes = int((estimated_seconds % 3600) / 60)
            time_str = f"~{hours}h {minutes}m"

        # Update summary labels
        self.summary_selected.setText(f"Selected: {selected_count:,} photos")
        self.summary_processed.setText(f"Already processed: {already_processed:,} (will skip)")
        self.summary_new.setText(f"To process: {new_to_process:,} photos")
        self.summary_time.setText(f"Estimated time: {time_str}")

        # Emit signal
        self.scopeChanged.emit(self.selected_photo_ids, new_to_process)

    def get_selected_photo_ids(self) -> List[int]:
        """Get list of selected photo IDs for processing."""
        if self.chk_skip_processed.isChecked():
            try:
                processed_ids = self.db.get_photo_ids_with_embeddings(self.project_id)
                if processed_ids:
                    processed_set = set(processed_ids)
                    return [pid for pid in self.selected_photo_ids if pid not in processed_set]
            except:
                pass
        return self.selected_photo_ids

    def get_processing_order(self) -> str:
        """Get selected processing order."""
        orders = ["newest", "oldest", "random"]
        return orders[self.order_combo.currentIndex()]

    def get_scope_description(self) -> str:
        """Get human-readable description of current scope."""
        if self.scope_mode == "all":
            return "All photos"
        elif self.scope_mode == "folders":
            return "Selected folders"
        elif self.scope_mode == "dates":
            start = self.date_from.date().toString("yyyy-MM-dd")
            end = self.date_to.date().toString("yyyy-MM-dd")
            return f"Date range: {start} to {end}"
        elif self.scope_mode == "recent":
            return self.recent_combo.currentText()
        elif self.scope_mode == "quantity":
            return f"{self.quantity_slider.value()}% of library"
        return "Unknown"
