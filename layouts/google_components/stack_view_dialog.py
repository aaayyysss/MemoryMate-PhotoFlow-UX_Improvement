# layouts/google_components/stack_view_dialog.py
# Version 01.03.00.00 dated 20260208
# Stack comparison dialog for Google Layout
# FIX 2026-02-08: Thread-safe thumbnail loading (QImage in workers, QPixmap on UI thread)

"""
StackViewDialog - Compare and manage stack members

This dialog shows a stack's members in a comparison view:
- Representative image highlighted
- Side-by-side thumbnails
- Metadata comparison table with similarity scores
- Actions: Keep All, Delete Selected, Set Representative, Unstack
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QGridLayout, QFrame, QCheckBox,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QSlider, QTabWidget, QSizePolicy
)
from PySide6.QtCore import Signal, Qt, Slot, QRunnable, QThreadPool, QObject, QTimer
from PySide6.QtGui import QFont, QColor, QPixmap, QImage
from typing import Optional, List, Dict, Any
from pathlib import Path
import os
from logging_config import get_logger
from utils.qt_guards import connect_guarded

logger = get_logger(__name__)

# FIX 2026-02-08: Removed global lock - using semaphore in thumbnail_service instead
# The lock was insufficient because QPixmap creation in worker threads is fundamentally
# not thread-safe on Windows. The fix is to use QImage (thread-safe) in workers,
# and only convert to QPixmap on the UI thread.


# ============================================================================
# PHASE 3: Progressive Loading - Async Thumbnail Loader
# ============================================================================

class ThumbnailLoadSignals(QObject):
    """
    Signals for async thumbnail loading (Qt cross-thread communication).

    FIX 2026-02-08: Changed to emit QImage instead of QPixmap.
    QImage is CPU-backed and thread-safe, QPixmap is GPU-backed and NOT thread-safe.
    The UI thread callback converts QImage -> QPixmap.
    """
    finished = Signal(object, object)  # QImage (not QPixmap!), thumbnail_label
    error = Signal(str, object)  # error_msg, thumbnail_label


class ThumbnailLoader(QRunnable):
    """
    Runnable task to load thumbnail in background thread.

    FIX 2026-02-08: CRITICAL THREAD-SAFETY FIX
    - Workers now use get_thumbnail_image() which returns QImage (thread-safe)
    - The signal emits QImage, NOT QPixmap
    - The UI thread callback converts QImage -> QPixmap
    - This follows Google Photos / Apple Photos best practice

    Based on iPhone Photos / Google Photos best practice:
    - Load thumbnails asynchronously to avoid UI freezing
    - Show placeholder immediately, load actual image in background
    - Update UI when thumbnail is ready
    - IMPORTANT: Only create QPixmap on UI thread!
    """

    def __init__(self, photo_path: str, thumbnail_label: QLabel, size: int = 200):
        super().__init__()
        self.photo_path = photo_path
        self.thumbnail_label = thumbnail_label
        self.size = size
        self.signals = ThumbnailLoadSignals()

    def run(self):
        """
        Load thumbnail in background thread.

        FIX 2026-02-08: Complete rewrite for thread-safety:
        - Uses get_thumbnail_image() which returns QImage (thread-safe)
        - Emits QImage via signal (not QPixmap!)
        - UI thread converts QImage -> QPixmap in the callback
        """
        logger.debug(f"[THUMBNAIL_LOADER] Starting thumbnail load for: {self.photo_path}")
        try:
            # Check if path exists first
            if not self.photo_path:
                logger.warning(f"[THUMBNAIL_LOADER] Empty photo path")
                self.signals.error.emit("No Path", self.thumbnail_label)
                return

            photo_path = Path(self.photo_path)

            # Check file existence with error handling for Unicode paths
            try:
                if not photo_path.exists():
                    logger.warning(f"[THUMBNAIL_LOADER] File not found: {self.photo_path}")
                    self.signals.error.emit("File Not Found", self.thumbnail_label)
                    return
            except OSError as e:
                logger.warning(f"[THUMBNAIL_LOADER] Cannot access path {self.photo_path}: {e}")
                self.signals.error.emit("Path Error", self.thumbnail_label)
                return

            # Skip extremely large files (> 100MB) to prevent memory issues
            try:
                file_size = photo_path.stat().st_size
                if file_size > 100 * 1024 * 1024:  # 100 MB
                    logger.warning(f"[THUMBNAIL_LOADER] File too large ({file_size / 1024 / 1024:.1f}MB): {self.photo_path}")
                    self.signals.error.emit("File Too Large", self.thumbnail_label)
                    return
            except OSError:
                pass  # If we can't stat, try to load anyway

            # FIX 2026-02-08: Use get_thumbnail_image() which returns QImage (thread-safe!)
            # This is the key fix - QPixmap was being created in worker thread (NOT safe)
            # Now we get QImage and convert to QPixmap only on UI thread
            logger.debug(f"[THUMBNAIL_LOADER] Loading thumbnail as QImage (thread-safe)...")
            from app_services import get_thumbnail_image

            qimage = get_thumbnail_image(self.photo_path, self.size, timeout=5.0)

            if qimage and not qimage.isNull():
                logger.debug(f"[THUMBNAIL_LOADER] QImage loaded successfully, emitting signal")
                # Emit QImage (thread-safe) - UI thread will convert to QPixmap
                self.signals.finished.emit(qimage, self.thumbnail_label)
            else:
                logger.warning(f"[THUMBNAIL_LOADER] get_thumbnail_image returned None or null")
                self.signals.error.emit("No Preview", self.thumbnail_label)

        except MemoryError:
            logger.error(f"[THUMBNAIL_LOADER] Out of memory loading: {self.photo_path}")
            self.signals.error.emit("Memory Error", self.thumbnail_label)
        except Exception as e:
            logger.error(f"[THUMBNAIL_LOADER] Exception during thumbnail load: {e}", exc_info=True)
            self.signals.error.emit("Error", self.thumbnail_label)


# ============================================================================
# Stack Member Widget
# ============================================================================

class StackMemberWidget(QWidget):
    """
    Widget displaying a single stack member with thumbnail and key metadata.

    Shows:
    - Thumbnail (larger than PhotoInstanceWidget)
    - Similarity score
    - Rank
    - Resolution
    - File size
    - Checkbox for selection
    - Representative indicator
    """

    selection_changed = Signal(int, bool)  # photo_id, is_selected
    thumbnail_clicked = Signal(str)  # photo_path - emitted on click to open lightbox

    def __init__(
        self,
        photo: Dict[str, Any],
        photo_id: int,  # CRITICAL: Pass photo_id explicitly
        similarity_score: Optional[float] = None,
        rank: Optional[int] = None,
        is_representative: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.photo = photo
        self.photo_id = photo_id  # Store photo_id explicitly
        self.similarity_score = similarity_score
        self.rank = rank
        self.is_representative = is_representative
        self._ui_generation = 0  # For stale update prevention

        self._init_ui()
        self._load_thumbnail()

    def _init_ui(self):
#        """Initialize UI components - compact layout."""
#        layout = QVBoxLayout(self)
#        layout.setSpacing(4)
#        layout.setContentsMargins(6, 6, 6, 6)
#
#        # Thumbnail (compact size)
#        self.thumbnail_label = QLabel()
#        self.thumbnail_label.setFixedSize(160, 160)  # Reduced from 180

        """Initialize UI components - compact layout with fixed height."""
        layout = QVBoxLayout(self)
        layout.setSpacing(2)  # Minimal spacing
        layout.setContentsMargins(4, 4, 4, 4)
 
        # Thumbnail - FIX 2026-02-09: Increased from 160 to 200 for better quality
        # Click to open in lightbox (iPhone/Google Photos pattern)
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(200, 200)

        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setCursor(Qt.PointingHandCursor)
        self.thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)
        self.thumbnail_label.setToolTip("Click to view in lightbox")
        self.thumbnail_label.setText("Loading...")
        self.thumbnail_label.mousePressEvent = self._on_thumbnail_clicked
        layout.addWidget(self.thumbnail_label, alignment=Qt.AlignCenter)

        # Compact metadata: one-liner with key info
        width = self.photo.get('width', 0)
        height = self.photo.get('height', 0)
        size_kb = self.photo.get('size_kb', 0)
        size_str = f"{size_kb/1024:.1f}MB" if size_kb >= 1024 else f"{size_kb:.0f}KB"

        # Build compact info line
        info_parts = []
        if self.is_representative:
            info_parts.append("⭐")
        if self.similarity_score is not None:
            info_parts.append(f"{int(self.similarity_score * 100)}%")
        info_parts.append(f"{width}×{height}")
        info_parts.append(size_str)

        info_label = QLabel(" • ".join(info_parts))
        info_label.setStyleSheet("font-size: 9px; color: #555;")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        # Filename (truncated, as tooltip)
        path = self.photo.get('path', '')
        filename = Path(path).name
        if len(filename) > 25:
            filename = filename[:22] + "..."
        path_label = QLabel(filename)
        path_label.setToolTip(path)
        path_label.setStyleSheet("font-size: 8px; color: #888;")
        path_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(path_label)

        # Selection checkbox (compact)
        self.checkbox = QCheckBox("Select")
        self.checkbox.setEnabled(not self.is_representative)
        if self.is_representative:
            self.checkbox.setToolTip("Cannot select representative")
        else:
            logger.debug(f"[CHECKBOX_INIT] Creating enabled checkbox for photo_id={self.photo_id}")

        self.checkbox.clicked.connect(lambda checked: self._on_selection_changed(Qt.Checked if checked else Qt.Unchecked))
        logger.debug(f"[CHECKBOX_INIT] Connected clicked signal for photo_id={self.photo_id}, enabled={self.checkbox.isEnabled()}, isCheckable={self.checkbox.isCheckable()}")
        layout.addWidget(self.checkbox)

        # Style
        border_color = "#FFA500" if self.is_representative else "#e0e0e0"
        self.setStyleSheet(f"""
            StackMemberWidget {{
                background-color: white;
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
        """)
        
        # Fix vertical stretching: set maximum height based on content
        # 200 (thumb) + 4*2 (margins) + 3*2 (spacing) + ~50 (labels+checkbox)
        self.setMaximumHeight(270)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def _load_thumbnail(self):
        """
        Load thumbnail asynchronously (Phase 3: Progressive Loading).

        FIX 2026-02-08: Updated to receive QImage from worker thread and convert
        to QPixmap on the UI thread. This fixes the access violation crash on Windows
        caused by creating QPixmap in worker threads.
        """
        logger.debug(f"[MEMBER_WIDGET] _load_thumbnail called for photo_id={self.photo_id}")
        try:
            path = self.photo.get('path', '')
            logger.debug(f"[MEMBER_WIDGET] Photo path: {path}")

            if path:
                # Load thumbnail in background thread
                logger.debug(f"[MEMBER_WIDGET] Creating ThumbnailLoader for {path}")

                # CRITICAL: Keep reference to prevent garbage collection
                # FIX 2026-02-09: Increased from 160 to 200 for better quality (same as photo grid)
                thumb_size = 200  # Higher quality thumbnails
                self._thumbnail_loader = ThumbnailLoader(path, self.thumbnail_label, size=thumb_size)

                # FIX 2026-02-08: Callback now receives QImage (thread-safe) and converts
                # to QPixmap here on the UI thread. This is the key fix!
                def on_loaded(qimage, label, size=thumb_size):
                    """
                    Process loaded QImage on UI thread.

                    FIX 2026-02-08: The worker now emits QImage (thread-safe), not QPixmap.
                    We convert to QPixmap here on the UI thread where it's safe.
                    """
                    logger.debug(f"[MEMBER_WIDGET] on_loaded callback: label={label}, self.thumbnail_label={self.thumbnail_label}")
                    if label == self.thumbnail_label:
                        logger.debug(f"[MEMBER_WIDGET] Converting QImage to QPixmap on UI thread")
                        # FIX 2026-02-08: Convert QImage -> QPixmap on UI thread (safe!)
                        pixmap = QPixmap.fromImage(qimage)

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
                        label.setPixmap(cropped)
                    else:
                        logger.warning(f"[MEMBER_WIDGET] Label mismatch in on_loaded!")

                def on_error(error_msg, label):
                    logger.debug(f"[MEMBER_WIDGET] on_error callback: error={error_msg}")
                    if label == self.thumbnail_label:
                        label.setText(error_msg)
                        if "Not Found" in error_msg:
                            label.setStyleSheet("""
                                QLabel {
                                    background-color: #fee;
                                    border: 1px solid #fcc;
                                    color: #c00;
                                }
                            """)

                gen = int(getattr(self.parent() or self.window(), "_ui_generation", self._ui_generation))
                connect_guarded(self._thumbnail_loader.signals.finished, self, on_loaded, generation=gen)
                connect_guarded(self._thumbnail_loader.signals.error, self, on_error, generation=gen)
                logger.debug(f"[MEMBER_WIDGET] Signals connected (guarded), starting loader in thread pool")

                # Start async loading
                QThreadPool.globalInstance().start(self._thumbnail_loader)
                logger.debug(f"[MEMBER_WIDGET] Loader started successfully")
            else:
                logger.warning(f"[MEMBER_WIDGET] No path for photo_id={self.photo_id}")
                self.thumbnail_label.setText("No path")

        except Exception as e:
            logger.error(f"[MEMBER_WIDGET] Failed to init thumbnail loader: {e}", exc_info=True)
            self.thumbnail_label.setText("Error")

    def _on_selection_changed(self, state):
        """Handle selection change."""
        is_selected = state == Qt.Checked
        # Use the explicitly stored photo_id instead of trying to extract from photo dict
        logger.debug(f"[SELECTION] StackMemberWidget checkbox changed: state={state}, is_selected={is_selected}, photo_id={self.photo_id}")
        logger.debug(f"[SELECTION] Emitting selection_changed signal: photo_id={self.photo_id}, is_selected={is_selected}")
        self.selection_changed.emit(self.photo_id, is_selected)

    def _on_thumbnail_clicked(self, event):
        """Handle thumbnail click - open photo in lightbox."""
        if event.button() == Qt.LeftButton:
            path = self.photo.get('path', '')
            if path:
                self.thumbnail_clicked.emit(path)

    def set_thumb_size(self, size: int):
        """Update thumbnail size dynamically for zoom controls."""
        size = int(size)
        if size == self.thumbnail_label.width():
            return
        self.thumbnail_label.setFixedSize(size, size)
        # Update max height to match new thumbnail size
        self.setMaximumHeight(size + 70)

        # Reload pixmap at new size
        try:
            path = self.photo.get('path', '')
            if path and Path(path).exists():
                from app_services import get_thumbnail
                pixmap = get_thumbnail(path, size)
                if pixmap and not pixmap.isNull():
                    scaled = pixmap.scaled(
                        size, size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    x = (scaled.width() - size) // 2
                    y = (scaled.height() - size) // 2
                    cropped = scaled.copy(x, y, size, size)
                    self.thumbnail_label.setPixmap(cropped)
        except Exception:
            pass

    def is_selected(self) -> bool:
        """Check if selected."""
        return self.checkbox.isChecked()


class StackViewDialog(QDialog):
    """
    Dialog for viewing and managing stack members.

    Displays:
    - Stack info (type, member count)
    - Representative image highlighted
    - Grid of all stack members with similarity scores
    - Metadata comparison table
    - Actions: Keep All, Delete Selected, Set Representative, Unstack

    Signals:
    - stack_action_taken: Emitted when user takes action on stack
    """

    # Signals
    stack_action_taken = Signal(str, int)  # action, stack_id

    def __init__(self, project_id: int, stack_id: int, parent=None):
        """
        Initialize StackViewDialog.

        Args:
            project_id: Project ID
            stack_id: Stack ID to display
            parent: Parent widget
        """
        super().__init__(parent)
        self._ui_generation: int = 0
        self.project_id = project_id
        self.stack_id = stack_id
        self.stack = None
        self.members = []
        self.photos = {}  # Map photo_id -> photo dict
        self.selected_photos = set()

        self.setWindowTitle("Stack Comparison")
        self.setMinimumSize(1100, 750)

        self._init_ui()
        self._load_stack()

    def _init_ui(self):
        """Initialize UI components - compact layout for media-first design."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)  # Reduced from 16
        layout.setContentsMargins(8, 8, 8, 8)  # Reduced from 16

        # Compact title row: Title + Info on same line
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)

        self.title_label = QLabel("📚 Stack Comparison")
        title_font = QFont()
        title_font.setPointSize(14)  # Reduced from 16
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        title_layout.addWidget(self.title_label)

        # Stack info (inline)
        self.info_label = QLabel("Loading...")
        self.info_label.setStyleSheet("color: #666; font-size: 11px;")
        title_layout.addWidget(self.info_label)

        title_layout.addStretch()

        # Zoom controls (Lightroom / Excire style thumbnail sizing)
        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedSize(24, 24)
        zoom_out_btn.setToolTip("Smaller thumbnails")
        zoom_out_btn.setStyleSheet("QPushButton { padding: 2px; background: #f0f0f0; border: 1px solid #ccc; border-radius: 3px; } QPushButton:hover { background: #e0e0e0; }")
        zoom_out_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() - 20))
        title_layout.addWidget(zoom_out_btn)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(100)
        self.zoom_slider.setMaximum(400)
        self.zoom_slider.setValue(200)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setToolTip("Adjust thumbnail size")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        title_layout.addWidget(self.zoom_slider)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedSize(24, 24)
        zoom_in_btn.setToolTip("Larger thumbnails")
        zoom_in_btn.setStyleSheet("QPushButton { padding: 2px; background: #f0f0f0; border: 1px solid #ccc; border-radius: 3px; } QPushButton:hover { background: #e0e0e0; }")
        zoom_in_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() + 20))
        title_layout.addWidget(zoom_in_btn)

        self.zoom_label = QLabel("200px")
        self.zoom_label.setStyleSheet("color: #888; font-size: 10px; min-width: 36px;")
        title_layout.addWidget(self.zoom_label)

        layout.addLayout(title_layout)

        # Members grid (no QGroupBox wrapper, direct scroll area)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
            }
        """)

        self.members_container = QWidget()
        self.members_grid = QGridLayout(self.members_container)
        self.members_grid.setSpacing(8)  # Reduced from 16
        self.members_grid.setContentsMargins(8, 8, 8, 8)  # Reduced from 16

        scroll.setWidget(self.members_container)
        layout.addWidget(scroll, stretch=3)


        # Comparison table (collapsible, hidden by default for more thumbnail space)
        table_header = QHBoxLayout()
        table_header.setContentsMargins(0, 2, 0, 0)

        self.table_toggle_btn = QPushButton("📊 Show Comparison Table")
        self.table_toggle_btn.setCheckable(True)
        self.table_toggle_btn.setChecked(False)
        self.table_toggle_btn.setStyleSheet("""
            QPushButton {
                padding: 2px 8px;
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 3px;
                font-size: 10px;
                color: #555;
            }
            QPushButton:checked {
                background-color: #e0e0e0;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
            }
        """)
        self.table_toggle_btn.clicked.connect(self._toggle_comparison_table)
        table_header.addWidget(self.table_toggle_btn)
        table_header.addStretch(1)
        layout.addLayout(table_header)





        self.comparison_table = QTableWidget()
        self.comparison_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                gridline-color: #eee;
            }
            QHeaderView::section {
                background-color: #f5f5f5;
                padding: 2px 4px;
                border: 1px solid #ddd;
                font-size: 10px;
            }
        """)
        
        self.comparison_table.setMaximumHeight(120)  # Compact height when shown
        self.comparison_table.hide()  # Hidden by default        
        
#        self.comparison_table.setMaximumHeight(150)  # Limit table height
        layout.addWidget(self.comparison_table)

        # Action buttons (compact)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 4, 0, 0)

        # Compact button style
        compact_btn_style = """
            QPushButton {{
                padding: 4px 10px;
                background-color: {bg};
                color: {fg};
                border: {border};
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:disabled {{
                background-color: #ccc;
                color: #999;
            }}
        """

        self.btn_unstack = QPushButton("🔓 Unstack")
        self.btn_unstack.setToolTip("Remove all members from this stack")
        self.btn_unstack.clicked.connect(self._on_unstack_all)
        self.btn_unstack.setStyleSheet(compact_btn_style.format(
            bg="#FF9800", fg="white", border="none", hover="#F57C00"
        ))
        button_layout.addWidget(self.btn_unstack)

        self.btn_unstack_selected = QPushButton("🔓 Unstack Sel.")
        self.btn_unstack_selected.setEnabled(False)
        self.btn_unstack_selected.setToolTip("Remove selected photos from stack")
        self.btn_unstack_selected.clicked.connect(self._on_unstack_selected)
        self.btn_unstack_selected.setStyleSheet(compact_btn_style.format(
            bg="#FF9800", fg="white", border="none", hover="#F57C00"
        ))
        button_layout.addWidget(self.btn_unstack_selected)

        self.btn_keep_best = QPushButton("⭐ Keep Best")
        self.btn_keep_best.clicked.connect(self._on_keep_best)
        self.btn_keep_best.setToolTip("Keep best quality, select others for deletion")
        self.btn_keep_best.setStyleSheet(compact_btn_style.format(
            bg="#4CAF50", fg="white", border="none", hover="#45a049"
        ))
        button_layout.addWidget(self.btn_keep_best)

        button_layout.addStretch()

        self.btn_delete_selected = QPushButton("🗑️ Delete")
        self.btn_delete_selected.setEnabled(False)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        self.btn_delete_selected.setStyleSheet(compact_btn_style.format(
            bg="#f44336", fg="white", border="none", hover="#d32f2f"
        ))
        logger.debug(f"[DELETE_BTN_INIT] Delete button created: {self.btn_delete_selected}, enabled={self.btn_delete_selected.isEnabled()}")
        button_layout.addWidget(self.btn_delete_selected)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet(compact_btn_style.format(
            bg="#f5f5f5", fg="#333", border="1px solid #ccc", hover="#e8e8e8"
        ))
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _load_stack(self):
        """Load stack and members from database."""
        try:
            from repository.stack_repository import StackRepository
            from repository.photo_repository import PhotoRepository
            from repository.base_repository import DatabaseConnection

            # Initialize repositories
            db_conn = DatabaseConnection()
            stack_repo = StackRepository(db_conn)
            photo_repo = PhotoRepository(db_conn)

            # Load stack
            self.stack = stack_repo.get_stack_by_id(self.project_id, self.stack_id)
            if not self.stack:
                logger.warning(f"Stack {self.stack_id} not found in project {self.project_id}")
                QMessageBox.warning(
                    self,
                    "Stack Not Found",
                    f"Stack #{self.stack_id} not found.\n\n"
                    f"This can happen if stacks were recently regenerated.\n"
                    f"Please close this dialog and refresh the similar photos view."
                )
                self.reject()
                return

            # Load members
            self.members = stack_repo.list_stack_members(self.project_id, self.stack_id)

            # Load photo details
            for member in self.members:
                photo_id = member['photo_id']
                photo = photo_repo.get_by_id(photo_id)
                if photo:
                    self.photos[photo_id] = photo

            logger.info(f"Loaded stack {self.stack_id} with {len(self.members)} members")

            # Update UI
            self._update_ui()

        except Exception as e:
            logger.error(f"Failed to load stack: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Error Loading Stack",
                f"Failed to load stack:\n{e}"
            )
            self.reject()

    def _update_ui(self):
        """Update UI with loaded stack data."""
        # Update title and info
        stack_type = self.stack.get('stack_type', 'unknown')
        member_count = len(self.members)
        self.title_label.setText(f"📚 {stack_type.replace('_', ' ').title()} Stack")
        self.info_label.setText(f"Stack #{self.stack_id} • {member_count} members")

        # Populate members grid (3 columns)
        rep_photo_id = self.stack.get('representative_photo_id')

        for idx, member in enumerate(self.members):
            photo_id = member['photo_id']
            photo = self.photos.get(photo_id)

            if not photo:
                continue

            is_representative = (photo_id == rep_photo_id)

            widget = StackMemberWidget(
                photo=photo,
                photo_id=photo_id,  # Pass photo_id explicitly
                similarity_score=member.get('similarity_score'),
                rank=member.get('rank'),
                is_representative=is_representative,
                parent=self
            )
            widget.selection_changed.connect(self._on_member_selection_changed)
            widget.thumbnail_clicked.connect(self._open_lightbox)
            logger.debug(f"[SIGNAL_CONNECT] Connected selection signal for photo {photo_id} (rep={is_representative})")

            row = idx // 3
            col = idx % 3

            # Use AlignTop to prevent vertical stretching
            self.members_grid.addWidget(widget, row, col, Qt.AlignTop)
            
#            self.members_grid.addWidget(widget, row, col)

        # Populate comparison table
        self._populate_comparison_table()

    def _populate_comparison_table(self):
        """Populate metadata comparison table."""
        if not self.photos:
            return

        # Define columns
        headers = ["Photo", "Resolution", "File Size", "Date Taken", "Similarity", "Rank"]
        self.comparison_table.setColumnCount(len(headers))
        self.comparison_table.setHorizontalHeaderLabels(headers)
        self.comparison_table.setRowCount(len(self.members))

        # Populate rows
        rep_photo_id = self.stack.get('representative_photo_id')

        for row_idx, member in enumerate(self.members):
            photo_id = member['photo_id']
            photo = self.photos.get(photo_id)

            if not photo:
                continue

            is_representative = (photo_id == rep_photo_id)

            # Photo name
            path = photo.get('path', '')
            filename = Path(path).name
            item = QTableWidgetItem(filename)
            if is_representative:
                item.setBackground(QColor(255, 245, 230))  # Light orange
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            item.setToolTip(path)
            self.comparison_table.setItem(row_idx, 0, item)

            # Resolution
            width = photo.get('width', 0)
            height = photo.get('height', 0)
            item = QTableWidgetItem(f"{width}×{height}")
            item.setTextAlignment(Qt.AlignCenter)
            self.comparison_table.setItem(row_idx, 1, item)

            # File size
            size_kb = photo.get('size_kb', 0)
            if size_kb >= 1024:
                size_str = f"{size_kb/1024:.2f} MB"
            else:
                size_str = f"{size_kb:.1f} KB"
            item = QTableWidgetItem(size_str)
            item.setTextAlignment(Qt.AlignCenter)
            self.comparison_table.setItem(row_idx, 2, item)

            # Date taken
            date_taken = photo.get('date_taken', 'Unknown')
            item = QTableWidgetItem(date_taken)
            item.setTextAlignment(Qt.AlignCenter)
            self.comparison_table.setItem(row_idx, 3, item)

            # Similarity score
            similarity = member.get('similarity_score')
            if similarity is not None:
                item = QTableWidgetItem(f"{similarity*100:.1f}%")
            else:
                item = QTableWidgetItem("N/A")
            item.setTextAlignment(Qt.AlignCenter)
            self.comparison_table.setItem(row_idx, 4, item)

            # Rank
            rank = member.get('rank')
            if rank is not None:
                item = QTableWidgetItem(f"#{rank}")
            else:
                item = QTableWidgetItem("N/A")
            item.setTextAlignment(Qt.AlignCenter)
            self.comparison_table.setItem(row_idx, 5, item)

        # Resize columns
        self.comparison_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(headers)):
            self.comparison_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
            
    def _toggle_comparison_table(self, checked: bool):
        """Toggle visibility of comparison table."""
        if checked:
            self.comparison_table.show()
            self.table_toggle_btn.setText("📊 Hide Comparison Table")
        else:
            self.comparison_table.hide()
            self.table_toggle_btn.setText("📊 Show Comparison Table")
            

    @Slot(int, bool)
    def _on_member_selection_changed(self, photo_id: int, is_selected: bool):
        """Handle member selection change."""
        logger.debug(f"[HANDLER] ====== RECEIVED SELECTION SIGNAL ======")
        logger.debug(f"[HANDLER] photo_id={photo_id}, is_selected={is_selected}, type(photo_id)={type(photo_id)}")
        logger.debug(f"[HANDLER] Current selected_photos before update: {self.selected_photos}")

        if is_selected:
            self.selected_photos.add(photo_id)
            logger.debug(f"[HANDLER] Added {photo_id} to selected_photos")
        else:
            self.selected_photos.discard(photo_id)
            logger.debug(f"[HANDLER] Removed {photo_id} from selected_photos")

        # Update button states
        is_enabled = len(self.selected_photos) > 0
        logger.debug(f"[HANDLER] Current selected_photos after update: {self.selected_photos}")
        logger.debug(f"[HANDLER] Buttons enabled: {is_enabled} (selected count: {len(self.selected_photos)})")
        logger.debug(f"[HANDLER] Delete button object: {self.btn_delete_selected}, current enabled state: {self.btn_delete_selected.isEnabled()}")

        # Force update both button states (delete and unstack selected)
        self.btn_delete_selected.setEnabled(is_enabled)
        self.btn_unstack_selected.setEnabled(is_enabled)

        # Verify state was actually set
        actual_state_delete = self.btn_delete_selected.isEnabled()
        actual_state_unstack = self.btn_unstack_selected.isEnabled()
        logger.debug(f"[HANDLER] Delete button enabled state AFTER setEnabled({is_enabled}): {actual_state_delete}")
        logger.debug(f"[HANDLER] Unstack Selected button enabled state AFTER setEnabled({is_enabled}): {actual_state_unstack}")

        if is_enabled and not actual_state_delete:
            logger.error(f"[HANDLER] CRITICAL: Delete button setEnabled(True) FAILED! Button still disabled!")
        if is_enabled and not actual_state_unstack:
            logger.error(f"[HANDLER] CRITICAL: Unstack Selected button setEnabled(True) FAILED! Button still disabled!")

        # Force repaint to ensure visual update
        self.btn_delete_selected.update()
        self.btn_unstack_selected.update()

        # Log button's current visual properties
        logger.debug(f"[HANDLER] Delete button visible={self.btn_delete_selected.isVisible()}, text={self.btn_delete_selected.text()}")
        logger.debug(f"[HANDLER] Unstack Selected button visible={self.btn_unstack_selected.isVisible()}, text={self.btn_unstack_selected.text()}")
        logger.debug(f"[HANDLER] ====== END SELECTION SIGNAL ======\n")

    def _on_keep_best(self):
        """
        Handle 'Keep Best' button click.

        Automatically selects all photos except the representative for deletion.
        This is a smart action based on Google Photos and iPhone Photos best practices.
        """
        rep_photo_id = self.stack.get('representative_photo_id')

        if not rep_photo_id:
            QMessageBox.warning(
                self,
                "No Representative",
                "Cannot use 'Keep Best' - no representative photo is set for this stack."
            )
            return

        # Find all member widgets and select them (except representative)
        selected_count = 0
        for i in range(self.members_grid.count()):
            item = self.members_grid.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, StackMemberWidget):
                    # Select if not representative
                    if not widget.is_representative:
                        widget.checkbox.setChecked(True)
                        selected_count += 1

        # Show confirmation
        if selected_count > 0:
            QMessageBox.information(
                self,
                "Photos Selected",
                f"Selected {selected_count} photo(s) for deletion.\n\n"
                f"The best quality photo (representative) will be kept.\n"
                f"Click 'Delete Selected' to proceed."
            )
        else:
            QMessageBox.information(
                self,
                "No Photos to Select",
                "All photos in this stack are already optimal (only representative exists)."
            )

    def _on_delete_selected(self):
        """Handle delete selected button click."""
        if not self.selected_photos:
            return

        photo_ids = list(self.selected_photos)

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Delete {len(photo_ids)} selected photo(s)?\n\n"
            "This will:\n"
            "• Delete photo files from disk\n"
            "• Remove photos from database\n"
            "• Remove from stack\n"
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
                logger.info(f"Deleting {len(photo_ids)} photos from stack {self.stack_id}: {photo_ids}")
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
                self.stack_action_taken.emit("delete", self.stack_id)

                # Reload the stack view
                self._load_stack()

            except Exception as e:
                logger.error(f"Failed to delete photos: {e}", exc_info=True)
                QMessageBox.critical(
                    self,
                    "Deletion Failed",
                    f"Failed to delete photos:\n{e}\n\nPlease check the log for details."
                )

    # ========================================================================
    # LIGHTBOX INTEGRATION (Google Photos / iPhone Photos pattern)
    # ========================================================================

    def _open_lightbox(self, path: str):
        """Open a photo in the media lightbox for full-size viewing."""
        try:
            from google_components.media_lightbox import MediaLightbox

            # Collect all photo paths from stack members
            all_paths = []
            for member in self.members:
                photo = self.photos.get(member['photo_id'])
                if photo:
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
    # ZOOM CONTROLS (Lightroom / Excire style thumbnail sizing)
    # ========================================================================

    def _on_zoom_changed(self, value: int):
        """Handle zoom slider change - resize thumbnails."""
        self.zoom_label.setText(f"{value}px")

        # Update all visible member widgets
        for i in range(self.members_grid.count()):
            item = self.members_grid.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), StackMemberWidget):
                item.widget().set_thumb_size(value)

    def _on_unstack_all(self):
        """Handle unstack all button click."""
        reply = QMessageBox.question(
            self,
            "Confirm Unstack All",
            f"Remove all {len(self.members)} photos from this stack?\n\n"
            "Photos will not be deleted, only unstacked.\n"
            "The stack will be completely dissolved.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                from repository.stack_repository import StackRepository
                from repository.base_repository import DatabaseConnection

                db_conn = DatabaseConnection()
                stack_repo = StackRepository(db_conn)

                # Delete the stack (CASCADE will remove all members automatically)
                deleted = stack_repo.delete_stack(self.project_id, self.stack_id)

                if deleted:
                    logger.info(f"Successfully unstacked all photos from stack {self.stack_id}")
                    QMessageBox.information(
                        self,
                        "Success",
                        f"Stack has been removed.\n"
                        f"All {len(self.members)} photos are now unstacked."
                    )
                    self.stack_action_taken.emit("unstack_all", self.stack_id)
                    self.accept()
                else:
                    QMessageBox.warning(
                        self,
                        "Not Found",
                        "Stack not found. It may have been already removed."
                    )

            except Exception as e:
                logger.error(f"Failed to unstack all: {e}", exc_info=True)
                QMessageBox.critical(self, "Error", f"Failed to unstack all:\n{e}")

    def _on_unstack_selected(self):
        """Handle unstack selected photos button click."""
        if not self.selected_photos:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select photos to unstack using the checkboxes."
            )
            return

        # Check if user is trying to unstack ALL photos
        if len(self.selected_photos) == len(self.members):
            reply = QMessageBox.question(
                self,
                "Unstack All?",
                f"You've selected all {len(self.members)} photos.\n\n"
                "This will dissolve the entire stack.\n"
                "Use 'Unstack All' button instead?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._on_unstack_all()
                return

        reply = QMessageBox.question(
            self,
            "Confirm Unstack Selected",
            f"Remove {len(self.selected_photos)} selected photo(s) from this stack?\n\n"
            "Photos will not be deleted, only unstacked.\n"
            f"The stack will still contain {len(self.members) - len(self.selected_photos)} photo(s).",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                from repository.stack_repository import StackRepository
                from repository.base_repository import DatabaseConnection

                db_conn = DatabaseConnection()
                stack_repo = StackRepository(db_conn)

                # Remove selected members from stack
                removed_count = stack_repo.remove_stack_members(
                    self.project_id,
                    self.stack_id,
                    list(self.selected_photos)
                )

                if removed_count > 0:
                    logger.info(f"Successfully unstacked {removed_count} photos from stack {self.stack_id}")

                    # Check if stack still has enough members (min 2)
                    remaining_count = stack_repo.count_stack_members(self.project_id, self.stack_id)

                    if remaining_count < 2:
                        # Stack no longer valid (less than 2 photos), delete it
                        logger.info(f"Stack {self.stack_id} now has {remaining_count} member(s), deleting stack")
                        stack_repo.delete_stack(self.project_id, self.stack_id)

                        QMessageBox.information(
                            self,
                            "Success",
                            f"{removed_count} photo(s) unstacked.\n\n"
                            f"Stack dissolved as it had less than 2 photos remaining."
                        )
                        self.stack_action_taken.emit("unstack_selected", self.stack_id)
                        self.accept()
                    else:
                        # Stack still valid, reload
                        QMessageBox.information(
                            self,
                            "Success",
                            f"{removed_count} photo(s) unstacked.\n\n"
                            f"Stack now contains {remaining_count} photo(s)."
                        )
                        self.stack_action_taken.emit("unstack_selected", self.stack_id)

                        # Clear selection and reload stack
                        self.selected_photos.clear()
                        self._load_stack()
                else:
                    QMessageBox.warning(
                        self,
                        "Not Found",
                        "Selected photos not found in stack."
                    )

            except Exception as e:
                logger.error(f"Failed to unstack selected: {e}", exc_info=True)
                QMessageBox.critical(self, "Error", f"Failed to unstack selected:\n{e}")


# =============================================================================
# STACK BROWSER DIALOG
# =============================================================================

class StackBrowserDialog(QDialog):
    """
    Dialog for browsing all similar shot stacks.

    Features:
    - Grid view of all stack thumbnails
    - Similarity threshold slider (50-100%)
    - Real-time filtering based on similarity
    - Click to open detailed StackViewDialog
    - Total count indicator

    Based on best practices from Google Photos and iPhone Photos.
    """

    def __init__(self, project_id: int, stack_type: str = "similar", parent=None):
        """
        Initialize StackBrowserDialog.

        Args:
            project_id: Project ID
            stack_type: Stack type ("similar" for time-based, ignored when tabs are used)
            parent: Parent widget
        """
        super().__init__(parent)
        self.project_id = project_id
        self.stack_type = stack_type

        # Similar Shots mode data
        self.all_stacks = []  # All stacks from DB
        self.filtered_stacks = []  # Filtered by similarity threshold

        # People mode data
        self.all_people = []  # All people from face detection
        self.selected_person = None  # Currently selected person for detail view

        self.similarity_threshold = 0.85  # Default 85% (matches StackGenParams)
        self.current_mode = "similar"  # "similar" or "people"

        # Track all threshold labels for updating when slider changes
        self.threshold_labels = []  # List of all QLabel widgets showing threshold

        self.setWindowTitle("Similar Photos & People")
        self.setMinimumSize(1000, 700)

        # Resize throttle timer for responsive grid relayout
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._relayout_grids)

        # Store current thumb size for responsive grid
        self._current_thumb_size = 200

        # UI generation counter for stale update prevention
        self._ui_generation = 0

        logger.debug("[__INIT__] Starting _init_ui()")
        self._init_ui()
        logger.debug("[__INIT__] Finished _init_ui(), starting _load_current_mode_data()")
        self._load_current_mode_data()
        logger.debug("[__INIT__] Finished initialization")

    def _init_ui(self):
        """Initialize UI components with tabs for Similar Shots and People."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)  # Reduced from 16 for compact layout
        layout.setContentsMargins(8, 8, 8, 8)  # Reduced from 16

        # Compact header row: Title + Count on same line
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        # Title (smaller, inline)
        title_label = QLabel("📸 Similar Photos & People")
        title_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #2196F3;")
        header_layout.addWidget(title_label)

        # Count indicator (inline with title)
        self.count_label = QLabel("Loading...")
        self.count_label.setStyleSheet("font-size: 10pt; color: #666;")
        header_layout.addWidget(self.count_label)

        header_layout.addStretch(1)

        layout.addLayout(header_layout)

        # Tabs for Similar Shots vs People (compact styling)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
            }
            QTabBar::tab {
                padding: 4px 12px;
                margin-right: 2px;
                background-color: #f0f0f0;
                border: 1px solid #ddd;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: white;
                border-bottom: 2px solid #2196F3;
            }
            QTabBar::tab:hover {
                background-color: #e8f4f8;
            }
        """)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Tab 1: Similar Shots (time-based visual similarity)
        similar_tab = QWidget()
        similar_layout = QVBoxLayout(similar_tab)
        similar_layout.setSpacing(4)  # Reduced from 12
        similar_layout.setContentsMargins(4, 4, 4, 4)  # Reduced from 12

        # Similarity threshold slider
        self.slider_container = self._create_similarity_slider()
        similar_layout.addWidget(self.slider_container)

        # Info banner showing generation parameters
        # Store layout reference for proper banner updates
        self.similar_layout = similar_layout
        logger.debug(f"[INIT_UI] Creating initial info banner, layout has {similar_layout.count()} widgets")
        self.info_banner = self._create_info_banner()
        similar_layout.addWidget(self.info_banner)
        logger.debug(f"[INIT_UI] Added info banner, layout now has {similar_layout.count()} widgets")

        # Stack grid (scroll area) - takes all remaining space
        self.similar_scroll_area = QScrollArea()
        self.similar_scroll_area.setWidgetResizable(True)
        self.similar_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
            }
        """)

        self.similar_grid_container = QWidget()
        self.similar_grid_layout = QGridLayout(self.similar_grid_container)
        self.similar_grid_layout.setSpacing(8)  # Reduced from 16
        self.similar_grid_layout.setContentsMargins(8, 8, 8, 8)  # Reduced from 16

        self.similar_scroll_area.setWidget(self.similar_grid_container)
        similar_layout.addWidget(self.similar_scroll_area, 1)

        self.tabs.addTab(similar_tab, "⏱️ Similar Shots")

        # Tab 2: People (face-based grouping)
        people_tab = QWidget()
        people_layout = QVBoxLayout(people_tab)
        people_layout.setSpacing(4)  # Reduced from 12
        people_layout.setContentsMargins(4, 4, 4, 4)  # Reduced from 12

        # People slider (reuse similarity slider concept)
        self.people_slider_container = self._create_people_slider()
        people_layout.addWidget(self.people_slider_container)

        # People grid (scroll area) - takes all remaining space
        self.people_scroll_area = QScrollArea()
        self.people_scroll_area.setWidgetResizable(True)
        self.people_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
            }
        """)

        self.people_grid_container = QWidget()
        self.people_grid_layout = QGridLayout(self.people_grid_container)
        self.people_grid_layout.setSpacing(8)  # Reduced from 16
        self.people_grid_layout.setContentsMargins(8, 8, 8, 8)  # Reduced from 16

        self.people_scroll_area.setWidget(self.people_grid_container)
        people_layout.addWidget(self.people_scroll_area, 1)

        self.tabs.addTab(people_tab, "👤 People")

        layout.addWidget(self.tabs, 1)

        # Bottom buttons (compact)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 4, 0, 0)
        button_layout.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 16px;
                background-color: #f5f5f5;
                color: #333333;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
            }
        """)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _create_similarity_slider(self) -> QWidget:
        """Create compact similarity threshold slider (single row)."""
        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
            }
        """)

        layout = QHBoxLayout(container)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 4, 8, 4)  # Minimal vertical padding

        # Label
        label = QLabel("🎚️ Similarity:")
        label.setStyleSheet("font-weight: bold; font-size: 10pt;")
        layout.addWidget(label)

        # Min label
        min_label = QLabel("50%")
        min_label.setStyleSheet("font-size: 9pt; color: #666;")
        layout.addWidget(min_label)

        # Slider
        self.similarity_slider = QSlider(Qt.Horizontal)
        self.similarity_slider.setMinimum(50)
        self.similarity_slider.setMaximum(100)
        self.similarity_slider.setValue(int(self.similarity_threshold * 100))
        self.similarity_slider.setFixedWidth(200)
        self.similarity_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #bbb;
                background: #e0e0e0;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #2196F3;
                border: 1px solid #1976D2;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #1976D2;
            }
        """)
        self.similarity_slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.similarity_slider)

        # Max label
        max_label = QLabel("100%")
        max_label.setStyleSheet("font-size: 9pt; color: #666;")
        layout.addWidget(max_label)

        # Current value
        threshold_value_label = QLabel(f"{int(self.similarity_threshold * 100)}%")
        threshold_value_label.setStyleSheet("font-size: 10pt; color: #2196F3; font-weight: bold; min-width: 40px;")
        layout.addWidget(threshold_value_label)
        self.threshold_labels.append(threshold_value_label)

        # Inline help (tooltip instead of taking space)
        help_label = QLabel("ℹ️")
        help_label.setToolTip("Lower = more photos (includes less similar)\nHigher = fewer photos (only very similar)")
        help_label.setStyleSheet("font-size: 10pt; color: #999;")
        layout.addWidget(help_label)

        layout.addStretch(1)

        return container

    def _on_slider_changed(self, value: int):
        """Handle slider value change."""
        self.similarity_threshold = value / 100.0

        # Update ALL threshold labels (both tabs have sliders)
        for label in self.threshold_labels:
            label.setText(f"{value}%")

        # Re-filter and display based on current mode
        if self.current_mode == "similar":
            self._filter_and_display_stacks()
        elif self.current_mode == "people":
            self._display_people()  # Re-filter people view

    def _create_info_banner(self) -> QWidget:
        """Create compact info banner (single row with regenerate button)."""
        logger.debug("[CREATE_BANNER] Creating new compact info banner widget")
        banner = QFrame()
        banner.setStyleSheet("""
            QFrame {
                background-color: #f0f7fa;
                border: 1px solid #c8e1eb;
                border-radius: 4px;
            }
        """)

        layout = QHBoxLayout(banner)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 4, 8, 4)  # Minimal vertical padding

        # Compact info with tooltip for details
        info_text = "💡 Slider filters photos within stacks"

        # Add generation threshold info if we can infer it
        if self.all_stacks:
            min_similarity = self._get_minimum_similarity_in_stacks()
            if min_similarity:
                generation_threshold = int(min_similarity * 100)
                info_text = f"💡 Stacks generated at ~{generation_threshold}%"

        info_label = QLabel(info_text)
        info_label.setStyleSheet("font-size: 9pt; color: #0277bd;")
        info_label.setToolTip(
            "How Similar Photos Work:\n\n"
            "• Stacks are created during photo scanning\n"
            "• The slider filters which photos to show\n"
            "• Lower = more photos | Higher = only very similar\n"
            "• Regenerate to include new photos or change threshold"
        )
        layout.addWidget(info_label)

        # Stale stack warning (inline, compact)
        if hasattr(self, 'stale_photo_count') and self.stale_photo_count > 0:
            warning_label = QLabel(f"⚠️ {self.stale_photo_count} new photo(s) not in stacks")
            warning_label.setStyleSheet("font-size: 9pt; color: #856404; font-weight: bold;")
            layout.addWidget(warning_label)

        layout.addStretch(1)

        # Regenerate button (compact)
        self.btn_regenerate = QPushButton("🔄 Regenerate")
        self.btn_regenerate.setToolTip("Re-scan photos and create new similarity stacks")
        self.btn_regenerate.setStyleSheet("""
            QPushButton {
                padding: 3px 8px;
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 3px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        self.btn_regenerate.clicked.connect(self._on_regenerate_clicked)
        layout.addWidget(self.btn_regenerate)

        return banner

    def _get_minimum_similarity_in_stacks(self) -> Optional[float]:
        """
        Get the minimum similarity score across all stacks.
        This helps infer what threshold was used during generation.
        """
        if not self.all_stacks:
            return None

        min_sim = 1.0
        for stack in self.all_stacks:
            members = stack.get('members', [])
            for member in members:
                similarity = member.get('similarity_score', 1.0)
                if similarity < min_sim and similarity > 0:  # Exclude 0 scores
                    min_sim = similarity

        return min_sim if min_sim < 1.0 else None

    def _check_stale_stacks(self):
        """
        Check if stacks are stale (new photos added since generation).
        Shows a warning banner if stale.
        """
        # Disable stale stack checking temporarily to prevent warnings
        # TODO: Re-enable once data type conversion issues are resolved
        logger.debug("[STALE_CHECK] Stale stack checking temporarily disabled")
        self.stale_photo_count = 0
        return
        
        # Original implementation (commented out for now)
        

    def _update_info_banner(self):
        """Update info banner with current stack information."""
        logger.debug("[UPDATE_BANNER] Called _update_info_banner()")
        logger.debug(f"[UPDATE_BANNER] Layout has {self.similar_layout.count()} widgets before update")

        if not hasattr(self, 'info_banner') or not hasattr(self, 'similar_layout'):
            logger.warning("[UPDATE_BANNER] Missing info_banner or similar_layout, skipping update")
            return

        # Find the index of the old banner
        index = self.similar_layout.indexOf(self.info_banner)
        logger.debug(f"[UPDATE_BANNER] Found old banner at index {index}")

        if index < 0:
            logger.warning("[UPDATE_BANNER] Could not find info banner in layout")
            return

        # Remove old banner
        logger.debug(f"[UPDATE_BANNER] Removing widget at index {index}")
        self.similar_layout.removeWidget(self.info_banner)
        logger.debug(f"[UPDATE_BANNER] Layout has {self.similar_layout.count()} widgets after removeWidget()")

        # CRITICAL: Must hide the widget before deleteLater() to prevent it from being visible
        # removeWidget() only removes from layout management, widget is still a child of parent widget
        self.info_banner.hide()
        self.info_banner.setParent(None)  # Remove from parent widget's children
        self.info_banner.deleteLater()

        # Create and insert new banner at same position
        logger.debug(f"[UPDATE_BANNER] Creating new banner and inserting at index {index}")
        self.info_banner = self._create_info_banner()
        self.similar_layout.insertWidget(index, self.info_banner)
        logger.debug(f"[UPDATE_BANNER] Layout now has {self.similar_layout.count()} widgets after insertWidget()")

    def _load_stacks(self):
        """Load all stacks from database."""
        try:
            from repository.stack_repository import StackRepository
            from repository.base_repository import DatabaseConnection

            db_conn = DatabaseConnection()
            stack_repo = StackRepository(db_conn)

            # Get all stacks of the specified type for this project
            stacks = stack_repo.list_stacks(
                project_id=self.project_id,
                stack_type=self.stack_type
            )

            # Load members for each stack
            self.all_stacks = []
            for stack in stacks:
                stack_id = stack['stack_id']
                members = stack_repo.list_stack_members(
                    project_id=self.project_id,
                    stack_id=stack_id
                )
                stack['members'] = members
                self.all_stacks.append(stack)

            logger.info(f"Loaded {len(self.all_stacks)} {self.stack_type} stacks")

            # Check for stale stacks (new photos added since generation)
            self._check_stale_stacks()

            # Update info banner with generation threshold info
            logger.debug("[LOAD_STACKS] About to call _update_info_banner()")
            self._update_info_banner()
            logger.debug("[LOAD_STACKS] Returned from _update_info_banner()")

            # Filter and display
            self._filter_and_display_stacks()

        except Exception as e:
            logger.error(f"Failed to load stacks: {e}", exc_info=True)
            self.count_label.setText("Error loading stacks")
            QMessageBox.critical(self, "Error", f"Failed to load stacks:\n{e}")

    def _filter_and_display_stacks(self):
        """
        Filter stacks by similarity threshold and display.

        Based on Google Photos / iPhone Photos best practices:
        - Always show all stack groups (don't hide groups)
        - Filter MEMBERS within each stack based on similarity threshold
        - Lower threshold = MORE photos visible (includes less similar)
        - Higher threshold = FEWER photos visible (only very similar)
        - Hide stacks that have no members after filtering
        """
        # Filter members within each stack based on similarity threshold
        self.filtered_stacks = []

        for stack in self.all_stacks:
            members = stack.get('members', [])
            if not members:
                continue

            # Filter members by similarity threshold
            filtered_members = []
            for member in members:
                similarity = member.get('similarity_score', 0.0)
                # Include member if similarity >= threshold OR if it's the representative
                # Representative should always be included regardless of score
                photo_id = member.get('photo_id')
                is_representative = (photo_id == stack.get('representative_photo_id'))

                if is_representative or similarity >= self.similarity_threshold:
                    filtered_members.append(member)

            # Only include stack if it has at least 2 photos after filtering
            # (representative + at least 1 similar photo)
            if len(filtered_members) >= 2:
                # Create a copy of the stack with filtered members
                filtered_stack = stack.copy()
                filtered_stack['members'] = filtered_members
                self.filtered_stacks.append(filtered_stack)

        # Update count label
        total_photos = sum(len(stack.get('members', [])) for stack in self.filtered_stacks)
        self.count_label.setText(
            f"{len(self.filtered_stacks)} groups • {total_photos} photos"
        )

        # Display stacks
        self._display_stacks()

    def _display_stacks(self):
        """Display filtered stacks in grid."""
        logger.debug(f"[DISPLAY_STACKS] Clearing grid with {self.similar_grid_layout.count()} widgets")

        # Clear existing widgets
        while self.similar_grid_layout.count():
            item = self.similar_grid_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                logger.debug(f"[DISPLAY_STACKS] Removing widget: {type(widget).__name__}")
                # CRITICAL: Must hide and remove from parent before deleteLater()
                # takeAt() only removes from layout, widget is still visible as child of parent
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        logger.debug(f"[DISPLAY_STACKS] Grid cleared, displaying {len(self.filtered_stacks)} stacks")

        # If no stacks, show message
        if not self.filtered_stacks:
            no_stacks_label = QLabel(
                "No similar photo groups found.\n\n"
                f"At {int(self.similarity_threshold * 100)}% threshold, groups need at least 2 photos.\n"
                "Try lowering the threshold to see more photos in each group."
            )
            no_stacks_label.setAlignment(Qt.AlignCenter)
            no_stacks_label.setStyleSheet("color: #999; font-size: 11pt; padding: 20px;")
            self.similar_grid_layout.addWidget(no_stacks_label, 0, 0)
            return

        # Calculate responsive grid metrics
        cols, thumb_size, spacing = self._grid_metrics()
        self._current_thumb_size = thumb_size
        self.similar_grid_layout.setSpacing(spacing)

        # Add stack cards to grid (responsive columns)
        for i, stack in enumerate(self.filtered_stacks):
            row = i // cols
            col = i % cols

            card = self._create_stack_card(stack, thumb_size)
            self.similar_grid_layout.addWidget(card, row, col)

    def _create_stack_card(self, stack: dict, thumb_size: int = 200) -> QWidget:
        """Create a clickable card for a stack with responsive thumbnail."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #ddd;
                border-radius: 6px;
            }
            QFrame:hover {
                border-color: #2196F3;
                background-color: #f5f9ff;
            }
        """)
        card.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # Store thumb_size on card for later reference
        card._thumb_size = thumb_size

        # Representative thumbnail - responsive size
        thumbnail_label = QLabel()
        thumbnail_label.setFixedSize(thumb_size, thumb_size)
        thumbnail_label.setAlignment(Qt.AlignCenter)
        thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)

        # Show placeholder immediately, load thumbnail in background
        thumbnail_label.setText("Loading...")

        # Load representative photo thumbnail asynchronously
        rep_photo_id = stack.get('representative_photo_id')
        if rep_photo_id:
            try:
                from repository.photo_repository import PhotoRepository
                from repository.base_repository import DatabaseConnection

                db_conn = DatabaseConnection()
                photo_repo = PhotoRepository(db_conn)
                photo = photo_repo.get_by_id(rep_photo_id)

                if photo:
                    path = photo.get('path', '')
                    if path:
                        logger.debug(f"[STACK_CARD] Creating async loader for stack {stack.get('stack_id')}, photo path: {path}")

                        # CRITICAL: Attach loader to card to prevent garbage collection
                        card._thumbnail_loader = ThumbnailLoader(path, thumbnail_label, size=thumb_size)

                        # FIX 2026-02-08: Callback now receives QImage and converts to QPixmap on UI thread
                        def on_thumbnail_loaded(qimage, label, size=thumb_size):
                            """Convert QImage to QPixmap on UI thread (thread-safe)."""
                            logger.debug(f"[STACK_CARD] Thumbnail loaded for label {label}")
                            if label == thumbnail_label:
                                # FIX 2026-02-08: Convert QImage -> QPixmap on UI thread (safe!)
                                pixmap = QPixmap.fromImage(qimage)
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
                                label.setPixmap(cropped)

                        def on_thumbnail_error(error_msg, label):
                            logger.warning(f"[STACK_CARD] Thumbnail error: {error_msg}")
                            if label == thumbnail_label:
                                label.setText(error_msg)

                        gen2 = int(getattr(self.parent() or self.window(), "_ui_generation", self._ui_generation))
                        connect_guarded(card._thumbnail_loader.signals.finished, self, on_thumbnail_loaded, generation=gen2)
                        connect_guarded(card._thumbnail_loader.signals.error, self, on_thumbnail_error, generation=gen2)

                        # Submit to thread pool (non-blocking)
                        QThreadPool.globalInstance().start(card._thumbnail_loader)
                        logger.debug(f"[STACK_CARD] Thumbnail loader started for stack {stack.get('stack_id')}")
                    else:
                        thumbnail_label.setText("No Path")
                else:
                    thumbnail_label.setText("No Photo")
            except Exception as e:
                logger.warning(f"Failed to init thumbnail loader: {e}")
                thumbnail_label.setText("Preview Error")
        else:
            thumbnail_label.setText("No Representative")

        layout.addWidget(thumbnail_label)

        # Compact info: count + similarity on one line
        members = stack.get('members', [])
        max_similarity = max((m.get('similarity_score', 0.0) for m in members), default=0.0)

        info_text = f"📸 {len(members)} photos"
        if max_similarity > 0:
            info_text += f" • {int(max_similarity * 100)}%"

        info_label = QLabel(info_text)
        info_label.setStyleSheet("font-size: 9pt; color: #555;")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        # Make clickable
        stack_id = stack.get('stack_id')
        card.mousePressEvent = lambda event: self._on_stack_clicked(stack_id)

        return card

    def _on_regenerate_clicked(self):
        """Handle regenerate stacks button click."""
        try:
            # Confirm with user
            from PySide6.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self,
                "Regenerate Stacks",
                "This will:\n"
                "• Delete all existing similar photo stacks\n"
                "• Re-analyze all photos with optimized settings\n"
                "• Use lower similarity threshold (50%) to capture more photos\n"
                "• Use larger time window (30s) for better grouping\n\n"
                "This may take several minutes for large photo collections.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Import necessary modules
            from services.stack_generation_service import StackGenerationService, StackGenParams
            from services.photo_similarity_service import PhotoSimilarityService
            from repository.photo_repository import PhotoRepository
            from repository.stack_repository import StackRepository
            from repository.base_repository import DatabaseConnection

            # Initialize services
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)
            stack_repo = StackRepository(db_conn)
            similarity_service = PhotoSimilarityService()

            stack_gen_service = StackGenerationService(
                photo_repo=photo_repo,
                stack_repo=stack_repo,
                similarity_service=similarity_service
            )

            # Create optimized parameters
            params = StackGenParams(
                rule_version="1",
                time_window_seconds=30,  # Larger time window
                min_stack_size=2,  # Smaller minimum
                similarity_threshold=0.50,  # Lower threshold
                top_k=30,
                candidate_limit_per_photo=300
            )

            # Show progress dialog
            from PySide6.QtWidgets import QProgressDialog
            progress = QProgressDialog(
                "Regenerating similar photo stacks...\nThis may take a few minutes.",
                "Cancel",
                0,
                0,
                self
            )
            progress.setWindowTitle("Processing")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.show()

            # Run regeneration
            stats = stack_gen_service.regenerate_similar_shot_stacks(
                project_id=self.project_id,
                params=params
            )

            progress.close()

            # Show results
            QMessageBox.information(
                self,
                "Regeneration Complete",
                f"Successfully regenerated similar photo stacks:\n\n"
                f"• Photos analyzed: {stats.photos_considered}\n"
                f"• Stacks created: {stats.stacks_created}\n"
                f"• Photo memberships: {stats.memberships_created}\n"
                f"• Errors: {stats.errors}\n\n"
                f"The slider now controls filtering from 50-100%."
            )

            # Reload stacks
            self._load_stacks()

        except Exception as e:
            logger.error(f"Failed to regenerate stacks: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to regenerate stacks:\n{e}\n\n"
                f"Check the log for details."
            )

    def _on_stack_clicked(self, stack_id: int):
        """Handle stack card click - open detailed view."""
        try:
            # Open detailed StackViewDialog
            dialog = StackViewDialog(
                project_id=self.project_id,
                stack_id=stack_id,
                parent=self
            )

            # Connect signal to refresh this browser
            dialog.stack_action_taken.connect(self._on_stack_action)

            dialog.exec()

        except Exception as e:
            logger.error(f"Failed to open stack view: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to open stack view:\n{e}")

    def _on_stack_action(self, action: str, stack_id: int):
        """Handle action taken in stack view dialog."""
        logger.info(f"Stack action: {action} on stack {stack_id}")

        # Reload stacks to reflect changes
        self._load_stacks()

    # =========================================================================
    # RESPONSIVE GRID LAYOUT (Google Photos / Lightroom style media-first)
    # =========================================================================

    def _grid_metrics(self):
        """
        Calculate responsive grid metrics based on container width.

        Returns: (cols, thumb_size, spacing)
        """
        # Get container width from current tab's scroll area
        if self.current_mode == "similar":
            w = self.similar_grid_container.width() if hasattr(self, "similar_grid_container") else 900
        else:
            w = self.people_grid_container.width() if hasattr(self, "people_grid_container") else 900
        w = max(w, 360)

        # Spacing and margins (compact)
        spacing = 8
        margins = 8 * 2  # left + right

        # Target card width: thumb + padding + info row
        target_card_w = 240

        cols = max(1, min(6, (w - margins) // target_card_w))

        # Compute thumb size from available width and columns
        # Card needs: thumb + padding (~24px total for card padding and info)
        available_per_col = (w - margins - spacing * (cols - 1)) / cols
        thumb_size = int(max(140, min(280, available_per_col - 24)))

        return cols, thumb_size, spacing

    def _relayout_grids(self):
        """Relayout the grids after resize (throttled callback)."""
        # Re-display based on current mode
        if self.current_mode == "similar" and self.filtered_stacks:
            self._display_stacks()
        elif self.current_mode == "people" and self.all_people:
            self._display_people()

    def resizeEvent(self, event):
        """Handle window resize with throttled grid relayout."""
        super().resizeEvent(event)
        # Throttle to avoid rebuild spam during live resize dragging
        if hasattr(self, "_relayout_timer"):
            self._relayout_timer.start(120)

    # =========================================================================
    # PEOPLE MODE (Face-based grouping)
    # =========================================================================

    def _create_people_slider(self) -> QWidget:
        """Create similarity threshold slider for people view."""
        # Reuse the same slider creation logic
        return self._create_similarity_slider()

    def _on_tab_changed(self, index: int):
        """Handle tab change between Similar Shots and People."""
        if index == 0:
            self.current_mode = "similar"
        elif index == 1:
            self.current_mode = "people"

        # Load data for the new mode
        self._load_current_mode_data()

    def _load_current_mode_data(self):
        """Load data based on current mode (similar or people)."""
        if self.current_mode == "similar":
            self._load_stacks()
        elif self.current_mode == "people":
            self._load_people()

    def _load_people(self):
        """Load all people from face detection."""
        try:
            from services.person_stack_service import PersonStackService
            from reference_db import ReferenceDB

            db = ReferenceDB()
            person_service = PersonStackService(db)

            # Get all people in project
            self.all_people = person_service.get_all_people(self.project_id)

            logger.info(f"Loaded {len(self.all_people)} people")

            # Update count
            self.count_label.setText(f"{len(self.all_people)} people detected")

            # Display people
            self._display_people()

            # Note: ReferenceDB uses connection pooling - no need to close

        except Exception as e:
            logger.error(f"Failed to load people: {e}", exc_info=True)
            self.count_label.setText("Error loading people")
            QMessageBox.critical(self, "Error", f"Failed to load people:\n{e}")

    def _display_people(self):
        """Display people in grid."""
        logger.debug(f"[DISPLAY_PEOPLE] Clearing grid with {self.people_grid_layout.count()} widgets")

        # Clear existing widgets
        while self.people_grid_layout.count():
            item = self.people_grid_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                logger.debug(f"[DISPLAY_PEOPLE] Removing widget: {type(widget).__name__}")
                # CRITICAL: Must hide and remove from parent before deleteLater()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        logger.debug(f"[DISPLAY_PEOPLE] Grid cleared, displaying {len(self.all_people)} people")

        # If no people, show message
        if not self.all_people:
            no_people_label = QLabel(
                "No people detected in this project.\n\n"
                "Run face detection first to enable person-based grouping."
            )
            no_people_label.setAlignment(Qt.AlignCenter)
            no_people_label.setStyleSheet("color: #999; font-size: 11pt; padding: 20px;")
            self.people_grid_layout.addWidget(no_people_label, 0, 0)
            return

        # Calculate responsive grid metrics
        cols, thumb_size, spacing = self._grid_metrics()
        self._current_thumb_size = thumb_size
        self.people_grid_layout.setSpacing(spacing)

        # Add person cards to grid (responsive columns)
        for i, person in enumerate(self.all_people):
            row = i // cols
            col = i % cols

            card = self._create_person_card(person, thumb_size)
            self.people_grid_layout.addWidget(card, row, col)

    def _create_person_card(self, person: dict, thumb_size: int = 200) -> QWidget:
        """Create a clickable card for a person with responsive thumbnail."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #ddd;
                border-radius: 6px;
            }
            QFrame:hover {
                border-color: #2196F3;
                background-color: #f5f9ff;
            }
        """)
        card.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # Store thumb_size on card
        card._thumb_size = thumb_size

        # Representative face thumbnail - responsive size
        thumbnail_label = QLabel()
        thumbnail_label.setFixedSize(thumb_size, thumb_size)
        thumbnail_label.setAlignment(Qt.AlignCenter)
        thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)

        # Load representative face thumbnail with center-crop
        rep_thumb_png = person.get('rep_thumb_png')
        if rep_thumb_png:
            try:
                from PySide6.QtCore import QByteArray
                from PySide6.QtGui import QImage, QPixmap

                # Convert blob to pixmap
                byte_array = QByteArray(rep_thumb_png)
                image = QImage()
                image.loadFromData(byte_array, "PNG")

                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    # Google Photos style: center-crop to fill square
                    scaled = pixmap.scaled(
                        thumb_size, thumb_size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    # Center-crop to exact square
                    x = (scaled.width() - thumb_size) // 2
                    y = (scaled.height() - thumb_size) // 2
                    cropped = scaled.copy(x, y, thumb_size, thumb_size)
                    thumbnail_label.setPixmap(cropped)
                else:
                    thumbnail_label.setText("No Preview")
            except Exception as e:
                logger.warning(f"Failed to load person thumbnail: {e}")
                thumbnail_label.setText("Preview Error")
        else:
            thumbnail_label.setText("No Photo")

        layout.addWidget(thumbnail_label)

        # Compact info: name + count on one line
        display_name = person.get('display_name', 'Unknown')
        member_count = person.get('member_count', 0)

        info_label = QLabel(f"{display_name} • {member_count} 📸")
        info_label.setStyleSheet("font-size: 9pt; color: #555;")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        # Make clickable - opens person detail view
        branch_key = person.get('branch_key')
        card.mousePressEvent = lambda event: self._on_person_clicked(branch_key, display_name)

        return card

    def _on_person_clicked(self, branch_key: str, display_name: str):
        """Handle person card click - open photos of this person with similarity filtering."""
        try:
            from services.person_stack_service import PersonStackService
            from reference_db import ReferenceDB

            db = ReferenceDB()
            person_service = PersonStackService(db)

            # Get person photos with similarity filtering
            person_data = person_service.get_person_photos(
                project_id=self.project_id,
                branch_key=branch_key,
                similarity_threshold=self.similarity_threshold
            )

            # Note: ReferenceDB uses connection pooling - no need to close

            # Open PersonPhotosDialog (simplified - show in message for now)
            photos = person_data.get('photos', [])
            if not photos:
                QMessageBox.information(
                    self,
                    f"No Photos - {display_name}",
                    f"No photos found for {display_name} at {int(self.similarity_threshold * 100)}% similarity threshold.\n\n"
                    f"Try lowering the slider to see more photos."
                )
                return

            # TODO: Open a detail dialog showing all photos of this person
            # For now, show count
            QMessageBox.information(
                self,
                f"Photos of {display_name}",
                f"Found {len(photos)} photos of {display_name} at {int(self.similarity_threshold * 100)}% similarity.\n\n"
                f"Detail view coming in next update!"
            )

        except Exception as e:
            logger.error(f"Failed to open person photos: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to load person photos:\n{e}")
