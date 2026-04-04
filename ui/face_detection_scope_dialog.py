"""
Face Detection Scope Selection Dialog
Allows users to select which photos to process for face detection.

FEATURE #1: Comprehensive scope selection with:
- All photos
- Specific folders (checkbox tree)
- Date range picker
- Custom quantity slider
- Time estimation
- Skip already processed photos
"""

import logging
from typing import List, Dict, Any, Optional, Set
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QTreeWidget, QTreeWidgetItem,
    QDateEdit, QSlider, QCheckBox, QGroupBox, QWidget,
    QProgressBar, QMessageBox, QScrollArea, QFrame, QSizePolicy,
    QComboBox
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QIcon

from reference_db import ReferenceDB
from translation_manager import tr

logger = logging.getLogger(__name__)


class FaceDetectionScopeDialog(QDialog):
    """
    FEATURE #1: Dialog for selecting face detection scope.

    Allows users to choose which photos to process, estimate time,
    and see statistics about already-processed vs new photos.
    """

    # Signal emitted when user clicks "Start Detection"
    # Emits list of photo paths to process, chosen policy, and include_all_screenshot_faces flag
    scopeSelected = Signal(list, str, bool)

    def __init__(self, project_id: int, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.db = ReferenceDB()

        # Data
        self.all_photos: List[Dict[str, Any]] = []
        self.selected_paths: List[str] = []
        self.folders: List[Dict[str, Any]] = []

        # UI state
        self.scope_mode = "all"  # "all", "folders", "dates", "quantity"

        self.setWindowTitle("🎯 Face Detection - Select Scope")
        self.resize(700, 550)
        self.setMinimumSize(600, 400)

        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        """Setup the user interface with fixed header and scrollable content."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # === FIXED HEADER (Title + Buttons) ===
        header_widget = QWidget()
        header_widget.setStyleSheet("background-color: white; border-bottom: 1px solid #e0e0e0;")
        header_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 12, 20, 12)
        header_layout.setSpacing(16)

        # Title section
        title_section = QVBoxLayout()
        title_section.setSpacing(2)

        title = QLabel("Face Detection - Select Scope")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #333;")
        title_section.addWidget(title)

        subtitle = QLabel("Choose which photos to scan for faces")
        subtitle.setStyleSheet("color: #666; font-size: 9pt;")
        title_section.addWidget(subtitle)

        header_layout.addLayout(title_section)
        header_layout.addStretch()

        # Action buttons in header
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._secondary_button_style())
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        header_layout.addWidget(cancel_btn)

        start_btn = QPushButton("▶ Start")
        start_btn.setStyleSheet(self._primary_button_style())
        start_btn.setFixedWidth(100)
        start_btn.clicked.connect(self._start_detection)
        header_layout.addWidget(start_btn)

        main_layout.addWidget(header_widget)

        # === SCROLLABLE CONTENT AREA ===
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background-color: #fafafa; }")
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #fafafa;")
        scroll_content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(16, 12, 16, 12)

        # Scope selection group
        scope_group = self._create_scope_selection()
        content_layout.addWidget(scope_group)

        # Summary panel
        self.summary_panel = self._create_summary_panel()
        content_layout.addWidget(self.summary_panel)

        content_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area, 1)

    def _primary_button_style(self) -> str:
        """Return primary button styling (blue)."""
        return """
            QPushButton {
                background-color: #1a73e8;
                color: white;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 9pt;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1557b0;
            }
            QPushButton:pressed {
                background-color: #0d47a1;
            }
        """

    def _secondary_button_style(self) -> str:
        """Return secondary button styling (outlined)."""
        return """
            QPushButton {
                background-color: white;
                color: #333;
                padding: 8px 12px;
                font-size: 9pt;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #f5f5f5;
                border-color: #ccc;
            }
            QPushButton:pressed {
                background-color: #e8e8e8;
            }
        """

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

    def _create_scope_selection(self) -> QGroupBox:
        """Create scope selection radio buttons and options."""
        group = QGroupBox("📸 Detection Scope")
        group.setStyleSheet(self._groupbox_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 14, 10, 10)

        # Radio button group
        self.button_group = QButtonGroup(self)

        # Option 1: All Photos
        self.radio_all = QRadioButton("All Photos")
        self.radio_all.setChecked(True)
        self.radio_all.toggled.connect(lambda checked: self._on_scope_changed("all") if checked else None)
        self.button_group.addButton(self.radio_all)
        layout.addWidget(self.radio_all)

        self.label_all_count = QLabel()
        self.label_all_count.setStyleSheet("color: #666; margin-left: 25px;")
        layout.addWidget(self.label_all_count)

        # Option 2: Specific Folders
        self.radio_folders = QRadioButton("Specific Folders")
        self.radio_folders.toggled.connect(lambda checked: self._on_scope_changed("folders") if checked else None)
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
        self.radio_dates.toggled.connect(lambda checked: self._on_scope_changed("dates") if checked else None)
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

        # Option 4: Custom Quantity
        self.radio_quantity = QRadioButton("Custom Quantity")
        self.radio_quantity.toggled.connect(lambda checked: self._on_scope_changed("quantity") if checked else None)
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

        # Skip already processed checkbox
        self.chk_skip_processed = QCheckBox("Skip photos that already have face embeddings")
        self.chk_skip_processed.setChecked(True)
        self.chk_skip_processed.toggled.connect(self._update_summary)
        layout.addWidget(self.chk_skip_processed)

        # Screenshot Policy (3-mode)
        layout.addSpacing(10)
        policy_layout = QHBoxLayout()
        self.lbl_screenshot_policy = QLabel("Screenshots:")
        self.cmb_screenshot_policy = QComboBox()
        self.cmb_screenshot_policy.addItem("Exclude screenshots", "exclude")
        self.cmb_screenshot_policy.addItem("Detect only, exclude from clustering (recommended)", "detect_only")
        self.cmb_screenshot_policy.addItem("Detect and cluster screenshots", "include_cluster")
        self.cmb_screenshot_policy.setCurrentIndex(1)
        policy_layout.addWidget(self.lbl_screenshot_policy)
        policy_layout.addWidget(self.cmb_screenshot_policy, 1)
        layout.addLayout(policy_layout)

        self.lbl_screenshot_note = QLabel(
            "Note: 'Detect and cluster screenshots' makes screenshot faces eligible for People clustering, "
            "but crowded screenshots may still be capped or filtered for quality."
        )
        self.lbl_screenshot_note.setWordWrap(True)
        self.lbl_screenshot_note.setStyleSheet("color: #666; font-size: 8.5pt; margin-left: 2px;")
        layout.addWidget(self.lbl_screenshot_note)

        # Include all faces in dense screenshot collages (overrides cap)
        self.chk_include_all = QCheckBox("Include all faces in dense screenshot collages (overrides cap)")
        self.chk_include_all.setChecked(False)
        self.chk_include_all.setStyleSheet("margin-top: 5px; margin-left: 2px;")
        layout.addWidget(self.chk_include_all)

        return group

    def _create_summary_panel(self) -> QGroupBox:
        """Create summary panel showing selection statistics."""
        group = QGroupBox("📊 Summary")
        group.setStyleSheet(self._groupbox_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(2)
        layout.setContentsMargins(10, 14, 10, 10)

        self.summary_selected = QLabel()
        self.summary_selected.setStyleSheet("font-size: 11pt; font-weight: bold;")
        layout.addWidget(self.summary_selected)

        self.summary_processed = QLabel()
        layout.addWidget(self.summary_processed)

        self.summary_new = QLabel()
        layout.addWidget(self.summary_new)

        self.summary_time = QLabel()
        self.summary_time.setStyleSheet("color: #1a73e8; font-weight: bold;")
        layout.addWidget(self.summary_time)

        return group

    def load_data(self):
        """Load photos and folders from database."""
        try:
            # Load all photos
            self.all_photos = self.db.get_all_paths_with_dates(self.project_id) or []

            # Load folders
            self.folders = self.db.get_folders_with_counts(self.project_id) or []

            # Populate folder tree
            self._populate_folder_tree()

            # Update labels
            self.label_all_count.setText(f"{len(self.all_photos)} photos")

            # Update summary
            self._update_summary()

        except Exception as e:
            logger.error(f"Failed to load data: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load photos:\n{str(e)}")

    def _populate_folder_tree(self):
        """Populate folder tree with checkboxes in hierarchical structure."""
        self.folder_tree.clear()

        # Build folder lookup by ID
        folder_lookup = {f['id']: f for f in self.folders}

        # Build tree recursively
        def add_folder(parent_item, parent_id):
            # Find children of this parent
            children = [f for f in self.folders if f['parent_id'] == parent_id]

            for folder in children:
                item = QTreeWidgetItem(parent_item if parent_item else self.folder_tree)
                item.setText(0, f"{folder['name']} ({folder['count']} photos)")
                item.setCheckState(0, Qt.Unchecked)
                item.setData(0, Qt.UserRole, folder['id'])  # Store folder ID instead of path
                item.setExpanded(True)  # Expand by default

                # Recursively add children
                add_folder(item, folder['id'])

        # Start with root folders (parent_id is None)
        add_folder(None, None)

    def _on_scope_changed(self, mode: str):
        """Handle scope mode change."""
        self.scope_mode = mode

        # Show/hide relevant widgets
        self.folder_tree.setVisible(mode == "folders")
        self.date_widget.setVisible(mode == "dates")
        self.quantity_widget.setVisible(mode == "quantity")

        self._update_summary()

    def get_screenshot_policy(self) -> str:
        """Return the selected screenshot policy."""
        return self.cmb_screenshot_policy.currentData() or "detect_only"

    def get_include_all_faces(self) -> bool:
        """Return whether to include all faces in screenshot collages."""
        return self.chk_include_all.isChecked()

    def _on_folder_selection_changed(self, item: QTreeWidgetItem, column: int):
        """Handle folder checkbox change."""
        self._update_summary()

    def _update_summary(self):
        """Update summary panel with current selection statistics."""
        # Calculate selected photos based on mode
        if self.scope_mode == "all":
            selected_count = len(self.all_photos)
            self.selected_paths = [p['path'] for p in self.all_photos]

        elif self.scope_mode == "folders":
            # Get checked folder IDs recursively
            selected_folder_ids = []

            def get_checked_folders(item):
                """Recursively collect checked folder IDs."""
                if item.checkState(0) == Qt.Checked:
                    folder_id = item.data(0, Qt.UserRole)
                    if folder_id is not None:
                        selected_folder_ids.append(folder_id)

                # Check children
                for i in range(item.childCount()):
                    get_checked_folders(item.child(i))

            # Check all top-level items
            for i in range(self.folder_tree.topLevelItemCount()):
                get_checked_folders(self.folder_tree.topLevelItem(i))

            # Get photos for selected folders (including subfolders)
            if selected_folder_ids:
                self.selected_paths = self.db.get_photos_for_folders(self.project_id, selected_folder_ids)
            else:
                self.selected_paths = []
            selected_count = len(self.selected_paths)

        elif self.scope_mode == "dates":
            # Filter by date range
            start_date = self.date_from.date().toPython()  # datetime.date
            end_date = self.date_to.date().toPython()      # datetime.date

            self.selected_paths = [
                p['path'] for p in self.all_photos
                if p.get('date') and start_date <= p['date'].date() <= end_date  # Convert datetime to date for comparison
            ]
            selected_count = len(self.selected_paths)

        elif self.scope_mode == "quantity":
            # Use slider percentage
            total = len(self.all_photos)
            percentage = self.quantity_slider.value()
            selected_count = int(total * percentage / 100)
            self.selected_paths = [p['path'] for p in self.all_photos[:selected_count]]
            self.quantity_label.setText(f"{selected_count:,} photos ({percentage}%)")

        # Check which photos already have embeddings
        already_processed = 0
        if self.chk_skip_processed.isChecked():
            try:
                processed_paths = self.db.get_paths_with_embeddings(self.project_id)
                processed_set = set(processed_paths)
                already_processed = len([p for p in self.selected_paths if p in processed_set])
            except:
                pass

        new_detections = selected_count - already_processed

        # Estimate time (average 0.5s per photo)
        avg_time_per_photo = 0.5  # seconds
        estimated_seconds = new_detections * avg_time_per_photo

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
        self.summary_processed.setText(f"Already Processed: {already_processed:,} photos (skip)")
        self.summary_new.setText(f"New Detections: {new_detections:,} photos")
        self.summary_time.setText(f"Estimated Time: {time_str}")

    def _save_selection(self):
        """Save current selection as a preset."""
        # TODO: Implement preset saving to settings
        QMessageBox.information(self, "Save Selection", "Preset saving will be implemented in a future update.")

    def _start_detection(self):
        """Emit selected paths and close dialog."""
        if not self.selected_paths:
            QMessageBox.warning(
                self,
                "No Photos Selected",
                "Please select at least one photo for face detection."
            )
            return

        # Filter out already processed if checkbox is checked
        paths_to_process = self.selected_paths
        if self.chk_skip_processed.isChecked():
            try:
                processed_paths = self.db.get_paths_with_embeddings(self.project_id)
                processed_set = set(processed_paths)
                paths_to_process = [p for p in self.selected_paths if p not in processed_set]
            except Exception as e:
                logger.error(f"Failed to filter processed photos: {e}")

        if not paths_to_process:
            QMessageBox.information(
                self,
                "All Photos Processed",
                "All selected photos already have face embeddings.\n\n"
                "Uncheck 'Skip photos that already have face embeddings' to re-detect faces."
            )
            return

        # Emit signal with paths, policy, and include_all flag
        self.scopeSelected.emit(
            paths_to_process,
            self.get_screenshot_policy(),
            self.get_include_all_faces()
        )
        self.accept()
