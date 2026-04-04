# layouts/google_components/duplicates_dialog.py
# Version 02.01.00.01 dated 20260126
# Duplicate review and management dialog for Google Layout

"""
DuplicatesDialog - Review and manage exact duplicates

This dialog shows a list of duplicate assets and allows users to:
- Review duplicate groups
- Compare instances side-by-side
- Keep/delete specific instances
- Set representative photo
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QWidget, QScrollArea,
    QGridLayout, QFrame, QCheckBox, QMessageBox, QSplitter,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSlider
)
from PySide6.QtCore import Signal, Qt, QSize, Slot, QThreadPool, QTimer
from PySide6.QtGui import QPixmap, QFont, QColor, QCursor
from typing import Optional, List, Dict, Any
from pathlib import Path
from logging_config import get_logger

logger = get_logger(__name__)


class PhotoInstanceWidget(QWidget):
    """
    Widget displaying a single photo instance with thumbnail and metadata.

    Shows:
    - Thumbnail (responsive size, click to open lightbox)
    - Resolution
    - File size
    - Date taken
    - File path
    - Checkbox for selection
    - Representative indicator
    """

    selection_changed = Signal(int, bool)  # photo_id, is_selected
    thumbnail_clicked = Signal(str)  # photo_path - emitted on single click to open lightbox

    def __init__(self, photo: Dict[str, Any], is_representative: bool = False, thumb_size: int = 280, parent=None):
        super().__init__(parent)
        self.photo = photo
        self.is_representative = is_representative
        self.thumb_size = int(thumb_size)

        self._init_ui()
        self._load_thumbnail_async()

    def _init_ui(self):
        """Initialize UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # Thumbnail placeholder - Responsive size for better comparison (Google Photos style)
        # Click to open in lightbox (iPhone/Google Photos pattern)
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(self.thumb_size, self.thumb_size)
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setCursor(QCursor(Qt.PointingHandCursor))
        self.thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 8px;
            }
        """)
        self.thumbnail_label.setToolTip("Click to view in lightbox")
        self.thumbnail_label.setText("Loading...")
        self.thumbnail_label.mousePressEvent = self._on_thumbnail_clicked
        layout.addWidget(self.thumbnail_label, alignment=Qt.AlignCenter)

        # Metadata - Compact single-line format for media-first layout
        metadata_layout = QVBoxLayout()
        metadata_layout.setSpacing(2)

        # Representative badge
        if self.is_representative:
            rep_label = QLabel("⭐ Representative")
            rep_label.setStyleSheet("color: #FFA500; font-weight: bold;")
            metadata_layout.addWidget(rep_label)

        # Compact metadata: Resolution • Size • Date (Google Photos style)
        width = self.photo.get('width', 0)
        height = self.photo.get('height', 0)
        size_kb = self.photo.get('size_kb', 0)
        if size_kb >= 1024:
            size_str = f"{size_kb/1024:.1f} MB"
        else:
            size_str = f"{size_kb:.0f} KB"
        date_taken = self.photo.get('date_taken', '')
        if date_taken and len(date_taken) > 10:
            date_taken = date_taken[:10]  # Just the date part

        compact_info = f"{width}×{height} • {size_str}"
        if date_taken:
            compact_info += f" • {date_taken}"

        info_label = QLabel(compact_info)
        info_label.setStyleSheet("color: #666;")
        metadata_layout.addWidget(info_label)

        # File name (truncated) with full path in tooltip
        path = self.photo.get('path', '')
        filename = Path(path).name
        path_label = QLabel(f"📄 {filename}")
        path_label.setToolTip(path)
        path_label.setStyleSheet("color: #888;")
        path_label.setWordWrap(True)
        metadata_layout.addWidget(path_label)

        layout.addLayout(metadata_layout)

        # Selection checkbox (disabled for representative)
        self.checkbox = QCheckBox("Select for deletion")
        self.checkbox.setEnabled(not self.is_representative)
        if self.is_representative:
            self.checkbox.setToolTip("Cannot delete representative photo")
        self.checkbox.stateChanged.connect(self._on_selection_changed)
        layout.addWidget(self.checkbox)

        # Add border
        self.setStyleSheet("""
            PhotoInstanceWidget {
                background-color: white;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
            }
        """)

    def _load_thumbnail_async(self):
        """Load thumbnail asynchronously."""
        try:
            from app_services import get_thumbnail
            path = self.photo.get('path', '')

            if path and Path(path).exists():
                pixmap = get_thumbnail(path, self.thumb_size)
                if pixmap and not pixmap.isNull():
                    # Google Photos style: center-crop to fill square
                    scaled = pixmap.scaled(
                        self.thumb_size, self.thumb_size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    # Center-crop to exact square
                    x = (scaled.width() - self.thumb_size) // 2
                    y = (scaled.height() - self.thumb_size) // 2
                    cropped = scaled.copy(x, y, self.thumb_size, self.thumb_size)
                    self.thumbnail_label.setPixmap(cropped)
                    
#                    self.thumbnail_label.setPixmap(scaled)
                else:
                    self.thumbnail_label.setText("No preview")
            else:
                self.thumbnail_label.setText("File not found")
                self.thumbnail_label.setStyleSheet("""
                    QLabel {
                        background-color: #fee;
                        border: 1px solid #fcc;
                        border-radius: 4px;
                        color: #c00;
                    }
                """)
        except Exception as e:
            logger.error(f"Failed to load thumbnail for {self.photo.get('id')}: {e}")
            self.thumbnail_label.setText("Error loading")

    def set_thumb_size(self, size: int):
        """Update thumbnail size dynamically (for responsive resizing)."""
        size = int(size)
        if size == getattr(self, "thumb_size", None):
            return
        self.thumb_size = size
        self.thumbnail_label.setFixedSize(size, size)

        # Reload pixmap at new size (cache-friendly if get_thumbnail caches by size)
        try:
            from app_services import get_thumbnail
            path = self.photo.get('path', '')
            if path and Path(path).exists():
                pixmap = get_thumbnail(path, size)
                if pixmap and not pixmap.isNull():
                    # Google Photos style: center-crop to fill square
                    scaled = pixmap.scaled(
                        size, size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    # Center-crop to exact square
                    x = (scaled.width() - size) // 2
                    y = (scaled.height() - size) // 2
                    cropped = scaled.copy(x, y, size, size)
                    self.thumbnail_label.setPixmap(cropped)
                    
#                    self.thumbnail_label.setPixmap(scaled)
        except Exception:
            pass

    def _on_selection_changed(self, state):
        """Handle selection change."""
        # stateChanged signal passes int: 0=Unchecked, 2=Checked
        # Compare with Qt.CheckState.Checked.value or just check if state == 2
        is_selected = (state == Qt.CheckState.Checked.value) or (state == 2)
        photo_id = self.photo['id']
        logger.info(f"[PhotoInstanceWidget] Checkbox state changed: photo_id={photo_id}, state={state}, is_selected={is_selected}, Qt.CheckState.Checked.value={Qt.CheckState.Checked.value}")
        logger.info(f"[PhotoInstanceWidget] Emitting selection_changed signal: photo_id={photo_id}, is_selected={is_selected}")
        self.selection_changed.emit(photo_id, is_selected)
        logger.info(f"[PhotoInstanceWidget] Signal emitted successfully")

    def _on_thumbnail_clicked(self, event):
        """Handle thumbnail click - open photo in lightbox."""
        if event.button() == Qt.LeftButton:
            path = self.photo.get('path', '')
            if path:
                self.thumbnail_clicked.emit(path)

    def is_selected(self) -> bool:
        """Check if this instance is selected for deletion."""
        return self.checkbox.isChecked()


class DuplicatesDialog(QDialog):
    """
    Dialog for reviewing and managing exact duplicates.

    Displays:
    - List of duplicate assets (left panel)
    - Instance details for selected asset (right panel)
    - Actions: Keep All, Delete Selected, Set Representative

    Signals:
    - duplicate_action_taken: Emitted when user takes action on duplicates
    """

    # Signals
    duplicate_action_taken = Signal(str, int)  # action, asset_id

    def __init__(self, project_id: int, parent=None):
        """
        Initialize DuplicatesDialog.
            
        Args:
            project_id: Project ID
            parent: Parent widget
        """
        super().__init__(parent)
        self.project_id = project_id
        self.duplicates = []
        self.selected_asset_id = None
        self.selected_photos = set()  # Set of photo_ids selected for deletion
        self.instance_widgets = []  # Phase 3C: Track instance widgets for batch operations
            
        # Async loading infrastructure (similar to photo grid pattern)
        self._load_generation = 0
        self._details_generation = 0
        self._loading_in_progress = False
            
        # Create signals for async operations
        from workers.duplicate_loading_worker import DuplicateLoadSignals
        self.duplicate_signals = DuplicateLoadSignals()
        self.duplicate_signals.duplicates_loaded.connect(self._on_duplicates_loaded)
        self.duplicate_signals.details_loaded.connect(self._on_details_loaded)
        self.duplicate_signals.error.connect(self._on_load_error)
            
        self.setWindowTitle("Review Duplicates")
        self.setMinimumSize(1200, 700)

        # Resize throttle timer for responsive grid relayout
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._relayout_instances_grid)

        # Store current asset for resize rebuilds
        self._current_asset = None

        self._init_ui()
        self._load_duplicates_async()

    def _init_ui(self):
        """Initialize UI components - compact layout for media-first design."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)  # Minimal spacing between sections
        layout.setContentsMargins(8, 8, 8, 8)  # Reduced margins

        # Compact header row: Title + Subtitle + Counter (all on one line)
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        title = QLabel("📸 Duplicate Photo Review")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        header_row.addWidget(title)

        # Subtitle/loading indicator (inline with title)
        self.subtitle = QLabel("Loading...")
        self.subtitle.setStyleSheet("color: #666;")
        header_row.addWidget(self.subtitle)

        header_row.addStretch()

        # Items loaded counter (moved to header)
        self.items_counter = QLabel("")
        self.items_counter.setStyleSheet("color: #888;")
        header_row.addWidget(self.items_counter)

        layout.addLayout(header_row)

        # Loading indicator (hidden initially, shown below header when loading)
        self.loading_spinner = QLabel("⏳ Loading duplicate data...")
        self.loading_spinner.setStyleSheet("color: #444; font-style: italic;")
        self.loading_spinner.hide()
        layout.addWidget(self.loading_spinner)

        # Compact toolbar with batch operations (no group boxes - just buttons)
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        # Main content: Splitter with list and details (takes most space)
        self.splitter = QSplitter(Qt.Horizontal)

        # Left panel: Duplicate assets list (narrower for media-first layout)
        left_panel = self._create_assets_list_panel()
        left_panel.setMinimumWidth(260)
        left_panel.setMaximumWidth(340)
        self.splitter.addWidget(left_panel)

        # Right panel: Instance details (dominant, media-first)
        right_panel = self._create_instance_details_panel()
        self.splitter.addWidget(right_panel)

        # Give more space to the right panel (Lightroom filmstrip + main area pattern)
        self.splitter.setStretchFactor(0, 0)  # Left panel doesn't stretch
        self.splitter.setStretchFactor(1, 1)  # Right panel takes all extra space
        self.splitter.setSizes([300, 900])  # Initial sizes: left fixed-ish, right dominant

        layout.addWidget(self.splitter, 1)  # Stretch factor 1 to take available space

        # Bottom action buttons (compact)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 4, 0, 0)
        button_layout.addStretch()

        self.btn_delete_selected = QPushButton("🗑️ Delete Selected")
        self.btn_delete_selected.setEnabled(False)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        self.btn_delete_selected.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #999;
            }
        """)
        button_layout.addWidget(self.btn_delete_selected)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #f5f5f5;
                color: #333333;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
            }
        """)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _create_toolbar(self) -> QWidget:
        """Create compact toolbar with batch operations (no group boxes)."""
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 2, 0, 2)  # Minimal vertical padding
        toolbar_layout.setSpacing(6)

        # Compact button style
        toolbar_btn_style = """
            QPushButton {
                padding: 4px 10px;
                background-color: #f5f5f5;
                color: #333333;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
            }
        """

        # Batch selection buttons (no group box wrapper)
        btn_select_all = QPushButton("Select All")
        btn_select_all.setToolTip("Select all duplicates for deletion")
        btn_select_all.setStyleSheet(toolbar_btn_style)
        btn_select_all.clicked.connect(self._on_select_all)
        toolbar_layout.addWidget(btn_select_all)

        btn_select_none = QPushButton("Select None")
        btn_select_none.setToolTip("Deselect all duplicates")
        btn_select_none.setStyleSheet(toolbar_btn_style)
        btn_select_none.clicked.connect(self._on_select_none)
        toolbar_layout.addWidget(btn_select_none)

        btn_invert = QPushButton("Invert")
        btn_invert.setToolTip("Invert current selection")
        btn_invert.setStyleSheet(toolbar_btn_style)
        btn_invert.clicked.connect(self._on_invert_selection)
        toolbar_layout.addWidget(btn_invert)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet("color: #ccc;")
        toolbar_layout.addWidget(sep)

        # Auto-select button
        btn_auto_select = QPushButton("🎯 Auto-Select Lower Quality")
        btn_auto_select.setToolTip("Automatically select lower quality duplicates for deletion")
        btn_auto_select.clicked.connect(self._on_auto_select_duplicates)
        btn_auto_select.setStyleSheet("""
            QPushButton {
                padding: 4px 10px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        toolbar_layout.addWidget(btn_auto_select)

        # Load more button (moved here from pagination widget)
        self.load_more_btn = QPushButton("Load More")
        self.load_more_btn.clicked.connect(self._load_more_duplicates)
        self.load_more_btn.setStyleSheet("""
            QPushButton {
                padding: 4px 10px;
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:disabled {
                background-color: #BBDEFB;
                color: #90CAF9;
            }
        """)
        self.load_more_btn.hide()  # Hidden initially
        toolbar_layout.addWidget(self.load_more_btn)

        toolbar_layout.addStretch()

        # Zoom controls (Google Photos / Lightroom style thumbnail sizing)
        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #ccc;")
        toolbar_layout.addWidget(sep2)

        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedSize(24, 24)
        zoom_out_btn.setToolTip("Smaller thumbnails")
        zoom_out_btn.setStyleSheet(toolbar_btn_style)
        zoom_out_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() - 10))
        toolbar_layout.addWidget(zoom_out_btn)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(80)
        self.zoom_slider.setMaximum(400)
        self.zoom_slider.setValue(200)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setToolTip("Adjust thumbnail size")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        toolbar_layout.addWidget(self.zoom_slider)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedSize(24, 24)
        zoom_in_btn.setToolTip("Larger thumbnails")
        zoom_in_btn.setStyleSheet(toolbar_btn_style)
        zoom_in_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() + 10))
        toolbar_layout.addWidget(zoom_in_btn)

        self.zoom_label = QLabel("200px")
        self.zoom_label.setStyleSheet("color: #888; font-size: 10px; min-width: 36px;")
        toolbar_layout.addWidget(self.zoom_label)

        return toolbar

    def _create_assets_list_panel(self) -> QWidget:
        """Create left panel with duplicate assets list."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Title
        title = QLabel("Duplicate Groups")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        # List widget
        self.assets_list = QListWidget()
        self.assets_list.setIconSize(QSize(80, 80))
        self.assets_list.itemClicked.connect(self._on_asset_selected)
        self.assets_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
                color: black;
            }
            QListWidget::item:hover {
                background-color: #f5f5f5;
            }
        """)
        layout.addWidget(self.assets_list)

        return panel

    def _create_instance_details_panel(self) -> QWidget:
        """Create right panel with instance details."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header with title and navigation (Google Photos / Lightroom style)
        header_layout = QHBoxLayout()

        # Title
        self.details_title = QLabel("Select a duplicate group")
        self.details_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        header_layout.addWidget(self.details_title)

        header_layout.addStretch()

        # Navigation buttons (compact style)
        nav_btn_style = """
            QPushButton {
                padding: 4px 8px;
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-weight: bold;
                min-width: 36px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:disabled {
                color: #aaa;
                background-color: #f5f5f5;
            }
        """

        self.btn_prev_group = QPushButton("◀ Prev")
        self.btn_prev_group.setToolTip("Previous duplicate group (↑)")
        self.btn_prev_group.setStyleSheet(nav_btn_style)
        self.btn_prev_group.clicked.connect(self._on_prev_group)
        self.btn_prev_group.setEnabled(False)
        header_layout.addWidget(self.btn_prev_group)

        self.group_counter = QLabel("0 / 0")
        self.group_counter.setStyleSheet("color: #666; margin: 0 8px;")
        header_layout.addWidget(self.group_counter)

        self.btn_next_group = QPushButton("Next ▶")
        self.btn_next_group.setToolTip("Next duplicate group (↓)")
        self.btn_next_group.setStyleSheet(nav_btn_style)
        self.btn_next_group.clicked.connect(self._on_next_group)
        self.btn_next_group.setEnabled(False)
        header_layout.addWidget(self.btn_next_group)

        layout.addLayout(header_layout)

        # Quick action bar (Google Photos style - per-group actions)
        quick_actions = QHBoxLayout()
        quick_actions.setContentsMargins(0, 2, 0, 2)

        self.btn_keep_best = QPushButton("⭐ Keep Best Quality")
        self.btn_keep_best.setToolTip("Auto-select lower quality copies for deletion\n(Keeps largest file with best resolution)")
        self.btn_keep_best.setStyleSheet("""
            QPushButton {
                padding: 4px 10px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #ccc;
            }
        """)
        self.btn_keep_best.clicked.connect(self._on_keep_best_in_group)
        self.btn_keep_best.setEnabled(False)
        quick_actions.addWidget(self.btn_keep_best)

        # Space savings indicator
        self.savings_label = QLabel("")
        self.savings_label.setStyleSheet("color: #666; font-style: italic; margin-left: 16px;")
        quick_actions.addWidget(self.savings_label)

        quick_actions.addStretch()
        layout.addLayout(quick_actions)

        # Scroll area for instances
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
            }
        """)

        # Container for instance widgets
        self.instances_container = QWidget()
        self.instances_layout = QGridLayout(self.instances_container)
        self.instances_layout.setSpacing(8)
        self.instances_layout.setContentsMargins(8, 8, 8, 8)

        scroll.setWidget(self.instances_container)
        layout.addWidget(scroll)

        return panel

    def _load_duplicates_async(self):
        """Load duplicate assets asynchronously using background worker."""
        # Increment generation counter to track this load operation
        self._load_generation += 1
        current_generation = self._load_generation
        
        # Show loading state
        self._loading_in_progress = True
        self.subtitle.setText("Loading duplicates...")
        self.loading_spinner.show()
        self.assets_list.setEnabled(False)
        
        # Start async worker (non-blocking!)
        from workers.duplicate_loading_worker import load_duplicates_async
        load_duplicates_async(
            project_id=self.project_id,
            generation=current_generation,
            signals=self.duplicate_signals
        )
        
        logger.info(f"Started async duplicate loading (generation {current_generation})")
    
    def _on_duplicates_loaded(self, generation: int, duplicates: list):
        """Callback when async duplicate loading completes."""
        # Check if this is stale data
        if generation != self._load_generation:
            logger.info(f"Discarding stale duplicate data (gen {generation} vs current {self._load_generation})")
            return
        
        # Store results
        self.duplicates = duplicates
        
        # Clear loading state
        self._loading_in_progress = False
        self.loading_spinner.hide()
        
        # Update UI
        if not self.duplicates:
            self.subtitle.setText("No duplicates found. All photos are unique!")
            self.assets_list.setEnabled(False)
            self.load_more_btn.hide()
        else:
            self.subtitle.setText(f"Found {len(self.duplicates)} duplicate groups affecting {sum(d['instance_count'] for d in self.duplicates)} photos")
            self.assets_list.setEnabled(True)
            self._populate_assets_list()
            
            # Show pagination controls if we might have more data
            # (this is a simplified check - in reality we"d want to check total count)
            if len(duplicates) >= 50:  # Assuming batch size of 50
                self.load_more_btn.show()
            else:
                self.load_more_btn.hide()
            
            # Update counter
            self.items_counter.setText(f"{len(self.duplicates)} items loaded")
        
        # Handle deferred asset selection (from badge click)
        pending_id = getattr(self, '_pending_select_asset_id', None)
        if pending_id is not None:
            QTimer.singleShot(0, lambda: self._try_select_asset(pending_id))

        logger.info(f"Async duplicate loading complete: {len(duplicates)} groups loaded")
    
    def _load_more_duplicates(self):
        """Load additional duplicates (pagination)."""
        # Disable button during loading
        self.load_more_btn.setEnabled(False)
        self.load_more_btn.setText("Loading...")
        
        # Load next batch (this would need to track offset)
        # For now, we"ll just reload everything but this demonstrates the concept
        self._load_duplicates_async()
        
        logger.info("Loading more duplicates...")
    
    def _on_details_loaded(self, generation: int, details: dict):
        """Callback when async details loading completes."""
        # Check if this is stale data
        if generation != self._details_generation:
            logger.info(f"Discarding stale details data (gen {generation} vs current {self._details_generation})")
            return
        
        # Display the loaded details
        self._display_asset_details(details)
        
        logger.info(f"Async details loading complete for asset")
    
    def _on_load_error(self, generation: int, error_message: str):
        """Callback when async loading encounters an error."""
        logger.error(f"Async loading failed (gen {generation}): {error_message}")
        
        # Clear loading state
        self._loading_in_progress = False
        self.loading_spinner.hide()
        
        # Show error to user
        self.subtitle.setText(f"❌ Error loading duplicates: {error_message}")
        self.assets_list.setEnabled(False)
        
        QMessageBox.critical(
            self,
            "Error Loading Duplicates",
            f"Failed to load duplicate assets:\n{error_message}\n\n"
            "Please check the log for details."
        )

    def _populate_assets_list(self):
        """Populate the assets list with duplicate groups."""
        self.assets_list.clear()

        for asset in self.duplicates:
            asset_id = asset['asset_id']
            instance_count = asset['instance_count']
            content_hash = asset.get('content_hash', '')[:16]  # First 16 chars

            # Create list item
            item = QListWidgetItem()
            item.setText(f"Asset #{asset_id}\n{instance_count} copies\nHash: {content_hash}...")
            item.setData(Qt.UserRole, asset_id)

            # Try to load representative photo thumbnail
            rep_photo_id = asset.get('representative_photo_id')
            if rep_photo_id:
                try:
                    from repository.photo_repository import PhotoRepository
                    from repository.base_repository import DatabaseConnection
                    from app_services import get_thumbnail

                    db_conn = DatabaseConnection()
                    photo_repo = PhotoRepository(db_conn)
                    photo = photo_repo.get_by_id(rep_photo_id)

                    if photo:
                        path = photo.get('path', '')
                        if path and Path(path).exists():
                            pixmap = get_thumbnail(path, 80)
                            if pixmap and not pixmap.isNull():
                                item.setIcon(pixmap)
                except Exception as e:
                    logger.warning(f"Failed to load thumbnail for asset {asset_id}: {e}")

            self.assets_list.addItem(item)

    @Slot(QListWidgetItem)
    def _on_asset_selected(self, item: QListWidgetItem):
        """Handle asset selection with async details loading."""
        asset_id = item.data(Qt.UserRole)
        self.selected_asset_id = asset_id
        self.selected_photos.clear()
        self.btn_delete_selected.setEnabled(False)

        # Load asset details asynchronously
        self._load_asset_details_async(asset_id)
    
    def _load_asset_details_async(self, asset_id: int):
        """Load asset details asynchronously using background worker."""
        # Increment generation counter
        self._details_generation += 1
        current_generation = self._details_generation
        
        # Show loading state in details panel
        self.details_title.setText("Loading details...")
        
        # Clear existing instances
        while self.instances_layout.count():
            item = self.instances_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Start async worker
        from workers.duplicate_loading_worker import load_duplicate_details_async
        load_duplicate_details_async(
            project_id=self.project_id,
            asset_id=asset_id,
            generation=current_generation,
            signals=self.duplicate_signals
        )
        
        logger.info(f"Started async details loading for asset {asset_id} (generation {current_generation})")
    
    def _display_asset_details(self, details: dict):
        """Display loaded asset details in UI with responsive grid layout."""
        if not details:
            return

        # Store for resize rebuilds
        self._current_asset = details

        asset = details['asset']
        photos = details['photos']
        instance_count = details['instance_count']
        asset_id = asset['asset_id']

        # Update title
        self.details_title.setText(f"Asset #{asset_id} - {instance_count} Copies")

        # Clear existing instances safely
        self._clear_instances_grid()

        # Calculate responsive grid metrics
        cols, thumb_size, spacing = self._grid_metrics()

        # Update layout spacing and margins
        self.instances_layout.setSpacing(spacing)
        self.instances_layout.setContentsMargins(8, 8, 8, 8)

        # Add instance widgets in responsive grid
        rep_photo_id = asset.get('representative_photo_id')

        for idx, photo in enumerate(photos):
            is_representative = (photo['id'] == rep_photo_id)

            widget = PhotoInstanceWidget(
                photo,
                is_representative,
                thumb_size=thumb_size,
                parent=self.instances_container
            )
            widget.selection_changed.connect(self._on_instance_selection_changed)
            widget.thumbnail_clicked.connect(self._open_lightbox)

            # Add to tracking list
            self.instance_widgets.append(widget)

            # Calculate row/col based on responsive column count
            row = idx // cols
            col = idx % cols
            self.instances_layout.addWidget(widget, row, col)

        # Update navigation controls and savings indicator
        self._update_navigation_controls()
        self._update_savings_indicator()

    @Slot(int, bool)
    def _on_instance_selection_changed(self, photo_id: int, is_selected: bool):
        """Handle instance selection change."""
        logger.info(f"[DuplicatesDialog] Selection changed: photo_id={photo_id}, is_selected={is_selected}")

        if is_selected:
            self.selected_photos.add(photo_id)
            logger.info(f"[DuplicatesDialog] Added photo {photo_id} to selection")
        else:
            self.selected_photos.discard(photo_id)
            logger.info(f"[DuplicatesDialog] Removed photo {photo_id} from selection")

        # Enable delete button if any photos selected
        enabled = len(self.selected_photos) > 0
        logger.info(f"[DuplicatesDialog] Setting delete button enabled={enabled}, selected_photos count={len(self.selected_photos)}, ids={self.selected_photos}")
        self.btn_delete_selected.setEnabled(enabled)

        # Update savings indicator
        self._update_savings_indicator()

    def _on_delete_selected(self):
        """Handle delete selected button click."""
        if not self.selected_photos:
            return

        photo_ids = list(self.selected_photos)

        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete {len(photo_ids)} selected photo(s)?\n\n"
            "This will:\n"
            "• Delete photo files from disk\n"
            "• Remove photos from database\n"
            "• Update asset representatives if needed\n\n"
            "This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                # Import services
                from services.asset_service import AssetService
                from repository.asset_repository import AssetRepository
                from repository.photo_repository import PhotoRepository
                from repository.base_repository import DatabaseConnection

                # Initialize services
                db_conn = DatabaseConnection()
                photo_repo = PhotoRepository(db_conn)
                asset_repo = AssetRepository(db_conn)
                asset_service = AssetService(photo_repo, asset_repo)

                # Perform deletion
                logger.info(f"Deleting {len(photo_ids)} photos: {photo_ids}")
                result = asset_service.delete_duplicate_photos(
                    project_id=self.project_id,
                    photo_ids=photo_ids,
                    delete_files=True
                )

                # Check for errors
                if not result.get('success', False):
                    error_msg = result.get('error', 'Unknown error')
                    raise Exception(error_msg)

                # Show success message
                photos_deleted = result.get('photos_deleted', 0)
                files_deleted = result.get('files_deleted', 0)
                updated_reps = result.get('updated_representatives', [])

                success_msg = f"Successfully deleted {photos_deleted} photo(s).\n\n"
                success_msg += f"• {files_deleted} file(s) removed from disk\n"

                if updated_reps:
                    success_msg += f"• Updated {len(updated_reps)} asset representative(s)\n"

                errors = result.get('errors', [])
                if errors:
                    success_msg += f"\n⚠️ {len(errors)} error(s) occurred:\n"
                    for error in errors[:3]:  # Show first 3 errors
                        success_msg += f"  • {error}\n"

                QMessageBox.information(
                    self,
                    "Deletion Complete",
                    success_msg
                )

                logger.info(f"Deletion complete: {result}")

                # Emit signal
                if self.selected_asset_id:
                    self.duplicate_action_taken.emit("delete", self.selected_asset_id)

                # Reload the view
                self._load_duplicates_async()

            except Exception as e:
                logger.error(f"Failed to delete photos: {e}", exc_info=True)
                QMessageBox.critical(
                    self,
                    "Deletion Failed",
                    f"Failed to delete photos:\n{e}\n\nPlease check the log for details."
                )

    # Phase 3C: Batch Selection Handlers
    def _on_select_all(self):
        """Select all non-representative photos for deletion."""
        for widget in self.instance_widgets:
            if not widget.is_representative:
                widget.checkbox.setChecked(True)

    def _on_select_none(self):
        """Deselect all photos."""
        for widget in self.instance_widgets:
            widget.checkbox.setChecked(False)

    def _on_invert_selection(self):
        """Invert current selection."""
        for widget in self.instance_widgets:
            if not widget.is_representative:
                widget.checkbox.setChecked(not widget.checkbox.isChecked())

    # Phase 3C: Smart Cleanup Handler
    def _on_auto_select_duplicates(self):
        """
        Automatically select lower quality duplicates for deletion.

        Algorithm:
        1. For each duplicate group, keep the best photo (highest resolution, largest file size)
        2. Select all other photos in the group for deletion
        3. Never select the representative photo
        """
        if not self.selected_asset_id or not self.instance_widgets:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select a duplicate group first."
            )
            return

        # Get all photos in current group
        photos = [widget.photo for widget in self.instance_widgets]

        # Find the best photo (highest resolution, then largest file size)
        best_photo = max(photos, key=lambda p: (
            p.get('width', 0) * p.get('height', 0),  # Resolution
            p.get('size_kb', 0)  # File size
        ))

        best_photo_id = best_photo['id']

        # Select all photos except the best one and the representative
        selected_count = 0
        for widget in self.instance_widgets:
            photo_id = widget.photo['id']
            is_representative = widget.is_representative

            # Don't select if it's the best photo or the representative
            if photo_id != best_photo_id and not is_representative:
                widget.checkbox.setChecked(True)
                selected_count += 1
            else:
                widget.checkbox.setChecked(False)

        # Show info message
        QMessageBox.information(
            self,
            "Smart Selection Complete",
            f"✅ Selected {selected_count} lower quality photo(s) for deletion.\n\n"
            f"🎯 Kept best photo: {best_photo['width']}×{best_photo['height']}, "
            f"{best_photo.get('size_kb', 0):.1f} KB\n\n"
            f"Review the selection and click 'Delete Selected' to proceed."
        )

    # ========================================================================
    # NAVIGATION METHODS (Google Photos / Lightroom style quick browsing)
    # ========================================================================

    def _on_prev_group(self):
        """Navigate to previous duplicate group."""
        if not self.duplicates or not self.selected_asset_id:
            return

        # Find current index
        current_idx = self._get_current_group_index()
        if current_idx > 0:
            self._select_group_by_index(current_idx - 1)

    def _on_next_group(self):
        """Navigate to next duplicate group."""
        if not self.duplicates or not self.selected_asset_id:
            return

        # Find current index
        current_idx = self._get_current_group_index()
        if current_idx < len(self.duplicates) - 1:
            self._select_group_by_index(current_idx + 1)

    def _get_current_group_index(self) -> int:
        """Get index of currently selected group."""
        for idx, asset in enumerate(self.duplicates):
            if asset['asset_id'] == self.selected_asset_id:
                return idx
        return -1

    def _select_group_by_index(self, idx: int):
        """Select group by index and update UI."""
        if 0 <= idx < len(self.duplicates):
            # Select in list widget
            self.assets_list.setCurrentRow(idx)
            # Trigger selection handler
            item = self.assets_list.item(idx)
            if item:
                self._on_asset_selected(item)

    def _update_navigation_controls(self):
        """Update navigation button states and counter."""
        if not self.duplicates:
            self.btn_prev_group.setEnabled(False)
            self.btn_next_group.setEnabled(False)
            self.btn_keep_best.setEnabled(False)
            self.group_counter.setText("0 / 0")
            return

        current_idx = self._get_current_group_index()
        total = len(self.duplicates)

        # Update navigation buttons
        self.btn_prev_group.setEnabled(current_idx > 0)
        self.btn_next_group.setEnabled(current_idx < total - 1)
        self.btn_keep_best.setEnabled(current_idx >= 0)

        # Update counter
        if current_idx >= 0:
            self.group_counter.setText(f"{current_idx + 1} / {total}")
        else:
            self.group_counter.setText(f"0 / {total}")

    def _update_savings_indicator(self):
        """Calculate and display potential storage savings."""
        if not self.instance_widgets:
            self.savings_label.setText("")
            return

        # Calculate total size of selected photos
        total_savings_kb = 0
        for widget in self.instance_widgets:
            if widget.checkbox.isChecked():
                total_savings_kb += widget.photo.get('size_kb', 0)

        if total_savings_kb > 0:
            if total_savings_kb >= 1024:
                savings_str = f"{total_savings_kb / 1024:.1f} MB"
            else:
                savings_str = f"{total_savings_kb:.0f} KB"
            self.savings_label.setText(f"💾 Potential savings: {savings_str}")
        else:
            self.savings_label.setText("")

    # ========================================================================
    # LIGHTBOX INTEGRATION (Google Photos / iPhone Photos pattern)
    # ========================================================================

    def _open_lightbox(self, path: str):
        """Open a photo in the media lightbox for full-size viewing."""
        try:
            from google_components.media_lightbox import MediaLightbox

            # Collect all photo paths from current duplicate group
            all_paths = []
            if self._current_asset:
                for photo in self._current_asset.get('photos', []):
                    p = photo.get('path', '')
                    if p and Path(p).exists():
                        all_paths.append(p)

            if not all_paths:
                all_paths = [path]

            if path not in all_paths:
                all_paths.insert(0, path)

            lightbox = MediaLightbox(
                path, all_paths, parent=self,
                project_id=self.project_id,
            )
            lightbox.exec()
        except Exception as e:
            logger.error(f"Failed to open lightbox: {e}", exc_info=True)

    # ========================================================================
    # FOCUS / SELECT ASSET (open dialog pre-focused on a specific duplicate)
    # ========================================================================

    def select_asset(self, asset_id: int):
        """
        Pre-select and focus on a specific asset in the duplicate groups list.

        Called when opening the dialog from a duplicate badge click in the
        photo grid - scrolls to and highlights the matching duplicate group.

        Args:
            asset_id: Asset ID to focus on
        """
        # Store for deferred selection (data may not be loaded yet)
        self._pending_select_asset_id = asset_id

        # Try immediate selection if data is already loaded
        self._try_select_asset(asset_id)

    def _try_select_asset(self, asset_id: int):
        """Attempt to select a specific asset in the list widget."""
        for i in range(self.assets_list.count()):
            item = self.assets_list.item(i)
            if item and item.data(Qt.UserRole) == asset_id:
                self.assets_list.setCurrentItem(item)
                self.assets_list.scrollToItem(item)
                self._on_asset_selected(item)
                self._pending_select_asset_id = None
                return True
        return False

    # ========================================================================
    # ZOOM CONTROLS (Lightroom / Excire style thumbnail sizing)
    # ========================================================================

    def _on_zoom_changed(self, value: int):
        """Handle zoom slider change - resize thumbnails."""
        self.zoom_label.setText(f"{value}px")

        # Throttle full relayout (column count may change at different zoom levels)
        if hasattr(self, '_relayout_timer'):
            self._relayout_timer.start(150)

    # ========================================================================
    # RESPONSIVE GRID LAYOUT (Google Photos / Lightroom style media-first)
    # ========================================================================

    def _grid_metrics(self):
        """
        Calculate responsive grid metrics based on container width and zoom level.

        Returns: (cols, thumb_size, spacing)
        """
        # Get container width
        w = self.instances_container.width() if hasattr(self, "instances_container") else 900
        w = max(w, 360)

        # Spacing and margins should match grid layout (compact values)
        spacing = 8
        margins = 8 * 2  # left + right

        # Use zoom slider value as thumb size when available
        if hasattr(self, 'zoom_slider'):
            thumb_size = self.zoom_slider.value()
        else:
            thumb_size = 200

        # Calculate columns based on thumb size + card padding
        target_card_w = thumb_size + 56  # thumb + metadata padding
        cols = max(1, min(6, (w - margins) // target_card_w))

        return cols, thumb_size, spacing

    def _clear_instances_grid(self):
        """Safely clear all widgets from the instances grid."""
        if not hasattr(self, "instances_layout"):
            return
        while self.instances_layout.count():
            item = self.instances_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.instance_widgets = []

    def _relayout_instances_grid(self):
        """Relayout the instances grid after resize (throttled callback)."""
        # Only relayout if a group is currently shown
        asset = getattr(self, "_current_asset", None)
        if not asset:
            return

        # Re-render with new cols/thumb size
        self._display_asset_details(asset)

    def resizeEvent(self, event):
        """Handle window resize with throttled grid relayout."""
        super().resizeEvent(event)
        # Throttle to avoid rebuild spam during live resize dragging
        if hasattr(self, "_relayout_timer"):
            self._relayout_timer.start(120)

    def _on_keep_best_in_group(self):
        """
        Quick action: Keep only the best quality photo in current group.

        This is the same as _on_auto_select_duplicates but without the message box,
        for faster workflow when reviewing multiple groups.
        """
        if not self.selected_asset_id or not self.instance_widgets:
            return

        # Get all photos in current group
        photos = [widget.photo for widget in self.instance_widgets]

        # Find the best photo (highest resolution, then largest file size)
        best_photo = max(photos, key=lambda p: (
            p.get('width', 0) * p.get('height', 0),  # Resolution
            p.get('size_kb', 0)  # File size
        ))

        best_photo_id = best_photo['id']

        # Select all photos except the best one and the representative
        for widget in self.instance_widgets:
            photo_id = widget.photo['id']
            is_representative = widget.is_representative

            # Don't select if it's the best photo or the representative
            if photo_id != best_photo_id and not is_representative:
                widget.checkbox.setChecked(True)
            else:
                widget.checkbox.setChecked(False)

        # Update savings indicator
        self._update_savings_indicator()
