"""
People Manager Dialog
Enterprise-grade face management UI inspired by Google Photos, Apple Photos, and Microsoft Photos.

Features:
- Grid view with face thumbnails
- Name labeling and editing
- Merge/split clusters
- Add/remove faces from clusters
- Search by person name
- Face count badges
- Representative face selection
"""

import os

## Fix: runtime crash: logger is used but never defined ###
import logging
logger = logging.getLogger(__name__)

from pathlib import Path
from typing import List, Dict, Any, Optional

from PIL import Image, ImageOps

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QWidget, QFrame,
    QMessageBox, QInputDialog, QMenu, QSizePolicy, QToolBar,
    QComboBox, QSpinBox, QProgressDialog, QApplication  # FEATURE #3: Added for keyboard modifiers
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QThreadPool, Slot, QPoint, QMimeData
from PySide6.QtGui import QPixmap, QImage, QAction, QIcon
from PySide6.QtGui import QDrag

from reference_db import ReferenceDB
from translation_manager import tr
from utils.qt_guards import connect_guarded


class FaceClusterCard(QFrame):
    """Card widget displaying a face cluster (person)."""

    clicked = Signal(str)  # Emits branch_key
    renamed = Signal(str, str)  # Emits (branch_key, new_name)
    merge_requested = Signal(str)  # Emits branch_key
    delete_requested = Signal(str)  # Emits branch_key

    def __init__(self, cluster_data: Dict[str, Any], parent=None, thumbnail_size=192):
        super().__init__(parent)
        self.cluster_data = cluster_data
        self.branch_key = cluster_data["branch_key"]
        self.thumbnail_size = thumbnail_size

        # CRITICAL FIX: Store reference to dialog explicitly
        # When card is added to grid_layout, Qt will reparent it to grid_widget,
        # so self.parent() won't return the PeopleManagerDialog anymore.
        # We need to store the dialog reference before that happens.
        self.dialog = parent

        self._press_pos = None
        self._drag_active = False
        self.setAcceptDrops(True)

        self.setup_ui(thumbnail_size)
        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(1)
        self.setCursor(Qt.PointingHandCursor)

        ### Fix (move checkmark positioning into FaceClusterCard.resizeEvent) ###
        self.checkmark_label = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "checkmark_label", None) and self.checkmark_label.isVisible():
            self.checkmark_label.move(self.width() - 30, 6)

    def setup_ui(self, thumbnail_size=192):
        """Setup the card UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Face thumbnail (size can now be controlled via zoom slider)
        self.thumbnail_label = QLabel()

        self.thumbnail_label.setFixedSize(self.thumbnail_size, self.thumbnail_size)

        self.thumbnail_label.setScaledContents(True)
        self.thumbnail_label.setStyleSheet("QLabel { background-color: #f0f0f0; border: 1px solid #ccc; }")

        # Load thumbnail
        self.load_thumbnail()

        layout.addWidget(self.thumbnail_label, alignment=Qt.AlignCenter)

        # Name label (editable on double-click)
        name = self.cluster_data.get("display_name", "Unknown")
        self.name_label = QLabel(name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet("QLabel { font-weight: bold; }")
        layout.addWidget(self.name_label)

        # Count badge
        count = self.cluster_data.get("member_count", 0)
        self.count_label = QLabel(f"{count} photo{'s' if count != 1 else ''}")
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet("QLabel { color: #666; font-size: 11px; }")
        layout.addWidget(self.count_label)

        # Make card slightly elevated on hover
        self.setStyleSheet("""
            FaceClusterCard {
                background-color: white;
                border-radius: 8px;
            }
            FaceClusterCard:hover {
                background-color: #f8f8f8;
                border: 2px solid #4CAF50;
            }
        """)

    def load_thumbnail(self):
        """Load face thumbnail from crop path or representative with EXIF orientation correction."""
        rep_path = self.cluster_data.get("rep_path")

        if rep_path and os.path.exists(rep_path):
            try:
                # BUG-C2 FIX: Use context manager to prevent resource leak
                with Image.open(rep_path) as pil_image:
                    pil_image = ImageOps.exif_transpose(pil_image)  # Auto-rotate based on EXIF

                    # Convert PIL Image to QPixmap
                    if pil_image.mode != 'RGB':
                        pil_image = pil_image.convert('RGB')

                    # Convert to bytes and load into QImage
                    from io import BytesIO
                    buffer = BytesIO()
                    pil_image.save(buffer, format='PNG')
                    image = QImage.fromData(buffer.getvalue())

                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)

                    pixmap = pixmap.scaled(
                        self.thumbnail_size, self.thumbnail_size,

                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    self.thumbnail_label.setPixmap(pixmap)
                    return
            except Exception as e:
                print(f"[FaceClusterCard] Failed to load thumbnail with EXIF correction: {e}")
                # Fallback to direct QPixmap loading
                pixmap = QPixmap(rep_path)
                if not pixmap.isNull():

                    pixmap = pixmap.scaled(
                        self.thumbnail_size, self.thumbnail_size,

                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    self.thumbnail_label.setPixmap(pixmap)
                    return

        # Use PNG blob if available
        rep_thumb_png = self.cluster_data.get("rep_thumb_png")
        if rep_thumb_png:
            image = QImage.fromData(rep_thumb_png)
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)

                pixmap = pixmap.scaled(
                    self.thumbnail_size, self.thumbnail_size,

                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.thumbnail_label.setPixmap(pixmap)
                return

        # Fallback: show placeholder
        self.thumbnail_label.setText("No\nImage")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._press_pos is not None:
            if (event.position().toPoint() - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                self._begin_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._drag_active:
            self.clicked.emit(self.branch_key)
        self._press_pos = None
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def _begin_drag(self):
        self._drag_active = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"person:{self.branch_key}")
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text().startswith("person:"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasText():
            data = event.mimeData().text()
            if data.startswith("person:") and self.dialog:
                source_branch = data.split(":", 1)[1]
                if source_branch != self.branch_key:
                    self.dialog._handle_drag_merge(source_branch, self.branch_key)
                    event.acceptProposedAction()

    def mouseDoubleClickEvent(self, event):
        """Handle double-click to rename."""
        if event.button() == Qt.LeftButton:
            self.rename_person()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """
        FEATURE #3: Show context menu with multi-merge support.

        If multiple faces are selected (via Shift+Click), shows multi-merge option.
        """
        menu = QMenu(self)

        # CRITICAL FIX: Use self.dialog instead of self.parent()
        # Qt reparents the card to grid_widget when added to layout,
        # so self.parent() no longer returns the PeopleManagerDialog
        parent_dialog = self.dialog

        # DEEP DEBUG: Log all context menu detection details
        print(f"\n[ContextMenu] ========== CONTEXT MENU OPENED ==========")
        print(f"[ContextMenu] DEBUG: Right-clicked card branch_key = {self.branch_key}")
        print(f"[ContextMenu] DEBUG: self.parent() type = {type(self.parent())}")
        print(f"[ContextMenu] DEBUG: self.dialog type = {type(self.dialog)}")
        print(f"[ContextMenu] DEBUG: parent_dialog (using self.dialog) type = {type(parent_dialog)}")
        print(f"[ContextMenu] DEBUG: parent_dialog instance = {parent_dialog}")
        print(f"[ContextMenu] DEBUG: hasattr(parent_dialog, 'selected_clusters') = {hasattr(parent_dialog, 'selected_clusters')}")

        if hasattr(parent_dialog, 'selected_clusters'):
            print(f"[ContextMenu] DEBUG: parent_dialog.selected_clusters = {parent_dialog.selected_clusters}")
            print(f"[ContextMenu] DEBUG: len(parent_dialog.selected_clusters) = {len(parent_dialog.selected_clusters)}")
        else:
            print(f"[ContextMenu] DEBUG: parent_dialog does NOT have 'selected_clusters' attribute")

        has_multi_selection = (
            hasattr(parent_dialog, 'selected_clusters') and
            len(parent_dialog.selected_clusters) > 1
        )

        print(f"[ContextMenu] DEBUG: has_multi_selection = {has_multi_selection}")
        print(f"[ContextMenu] DEBUG: Will show {'MULTI-MERGE' if has_multi_selection else 'SINGLE-MERGE'} menu")
        print(f"[ContextMenu] ===========================================\n")

        if has_multi_selection:
            # FEATURE #3: Multi-merge menu - user will choose target from dialog
            merge_count = len(parent_dialog.selected_clusters)

            # FIX: Don't show target in menu - user chooses target in dialog
            multi_merge_action = QAction(
                f"🔗 Merge Selected People ({merge_count})...",
                self
            )
            multi_merge_action.triggered.connect(parent_dialog._merge_selected_clusters)
            menu.addAction(multi_merge_action)

            menu.addSeparator()

            clear_action = QAction("✕ Clear Selection", self)
            clear_action.triggered.connect(parent_dialog._clear_selection)
            menu.addAction(clear_action)

        else:
            # Single-selection menu (existing functionality)
            rename_action = QAction("✏️ Rename", self)
            rename_action.triggered.connect(self.rename_person)
            menu.addAction(rename_action)

            merge_action = QAction("🔗 Merge with...", self)
            merge_action.triggered.connect(lambda: self.merge_requested.emit(self.branch_key))
            menu.addAction(merge_action)

            menu.addSeparator()

            delete_action = QAction("🗑️ Delete", self)
            delete_action.triggered.connect(lambda: self.delete_requested.emit(self.branch_key))
            menu.addAction(delete_action)

        menu.exec(event.globalPos())

    def rename_person(self):
        """Rename this person."""
        current_name = self.cluster_data.get("display_name", "")

        new_name, ok = QInputDialog.getText(
            self,
            "Rename Person",
            "Enter person's name:",
            text=current_name
        )

        if ok and new_name and new_name != current_name:
            self.name_label.setText(new_name)
            self.cluster_data["display_name"] = new_name
            self.renamed.emit(self.branch_key, new_name)


class PeopleManagerDialog(QDialog):
    """Main dialog for managing face clusters (people)."""

    def __init__(self, project_id: int, parent=None):
        super().__init__(parent)
        self._ui_generation: int = 0
        self.project_id = project_id
        self.db = ReferenceDB()
        self.clusters: List[Dict[str, Any]] = []
        self.filtered_clusters: List[Dict[str, Any]] = []
        self.cards: Dict[str, FaceClusterCard] = {}

        # FEATURE #3: Multi-selection state for batch merging
        self.selected_clusters: List[str] = []  # Ordered list of selected branch_keys
        self.selection_mode = False  # True when Shift is held

        # Zoom/thumbnail size control
        self.thumbnail_size = 192  # Default size (was hardcoded before)

        # Face detection worker tracking
        self.face_detection_worker = None
        self.face_detection_progress_dialog = None

        self.setWindowTitle(f"People - Project {project_id}")

        # ADAPTIVE DIALOG SIZING: Based on screen resolution
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        screen_width = screen.geometry().width()
        screen_height = screen.geometry().height()

        # Adaptive size based on screen resolution
        if screen_width >= 2560:  # 4K
            self.resize(1200, 900)
        elif screen_width >= 1920:  # Full HD
            self.resize(1000, 800)
        elif screen_width >= 1366:  # HD
            self.resize(900, 700)
        else:  # Small screens
            width = int(screen_width * 0.75)
            height = int(screen_height * 0.70)
            self.resize(width, height)

        self.setup_ui()
        self.load_clusters()

    def setup_ui(self):
        """Setup the user interface."""
        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = self.create_toolbar()
        layout.addWidget(toolbar)

        # Search bar
        search_layout = QHBoxLayout()

        search_label = QLabel("🔍 Search:")
        search_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr('search.placeholder_filter_people'))
        self.search_input.textChanged.connect(self.filter_clusters)

        ### Fixes with biggest ROI
        ### 1. Debounce search input (150 to 250 ms)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self.filter_clusters(self.search_input.text()))
        self.search_input.textChanged.disconnect()
        self.search_input.textChanged.connect(lambda: self._search_timer.start(200))

        search_layout.addWidget(self.search_input)

        # Sort dropdown
        sort_label = QLabel("Sort by:")
        search_layout.addWidget(sort_label)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Most photos", "Name (A-Z)", "Recently added"])
        self.sort_combo.currentTextChanged.connect(self.sort_clusters)
        search_layout.addWidget(self.sort_combo)

        layout.addLayout(search_layout)

        # Scroll area with grid of face cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(12)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)

        scroll.setWidget(self.grid_widget)
        layout.addWidget(scroll)

        # Status bar
        status_layout = QHBoxLayout()

        self.status_label = QLabel()
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        status_layout.addWidget(close_btn)

        layout.addLayout(status_layout)

    def create_toolbar(self) -> QToolBar:
        """Create toolbar with actions."""
        from PySide6.QtWidgets import QSlider

        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))

        # Refresh action
        refresh_action = QAction("🔄 Refresh", self)
        refresh_action.triggered.connect(self.load_clusters)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        # Run face detection action
        detect_action = QAction("🔍 Detect Faces", self)
        detect_action.triggered.connect(self.run_face_detection)
        toolbar.addAction(detect_action)

        # Recluster action
        cluster_action = QAction("🔗 Recluster", self)
        cluster_action.triggered.connect(self.recluster_faces)
        toolbar.addAction(cluster_action)

        toolbar.addSeparator()

        # Settings action
        settings_action = QAction("⚙️ Settings", self)
        settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(settings_action)

        toolbar.addSeparator()

        # Zoom slider (FEATURE #3 POLISH: User requested zoom control)
        zoom_label = QLabel("🔍 Zoom:")
        toolbar.addWidget(zoom_label)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(128)  # Minimum thumbnail size
        self.zoom_slider.setMaximum(384)  # Maximum thumbnail size
        self.zoom_slider.setValue(192)    # Default size
        self.zoom_slider.setFixedWidth(150)
        self.zoom_slider.setToolTip("Adjust thumbnail size (128-384px)")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)

        ### Fixes with biggest ROI
        ### 2. Debounce zoom slider
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self.update_grid)

        toolbar.addWidget(self.zoom_slider)

        self.zoom_value_label = QLabel("192px")
        toolbar.addWidget(self.zoom_value_label)

        return toolbar

    def load_clusters(self):
        """Load face clusters from database."""
        try:
            self.clusters = self.db.get_face_clusters(self.project_id)
            self.filtered_clusters = self.clusters.copy()

            self.sort_clusters(rebuild=False)
            self.update_grid()
            self.update_status()
            QTimer.singleShot(0, self._force_repaint)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load face clusters:\n{str(e)}")

    def _force_repaint(self):
        """Force UI repaint after mass deleteLater/rebuild."""
        try:
            self.grid_widget.updateGeometry()
            self.grid_widget.adjustSize()
            self.grid_widget.update()
            self.update()
            QApplication.processEvents()
        except Exception:
            pass

    def update_grid(self):
        """Update the grid with face cards."""
        # Clear existing cards
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        self.cards.clear()

        # Add cards in grid
        cols = 4  # Number of columns
        for i, cluster in enumerate(self.filtered_clusters):
            row = i // cols
            col = i % cols

            card = FaceClusterCard(cluster, self, thumbnail_size=self.thumbnail_size)
            card.clicked.connect(self.on_cluster_clicked)
            card.renamed.connect(self.on_cluster_renamed)
            card.merge_requested.connect(self.on_merge_requested)
            card.delete_requested.connect(self.on_delete_requested)

            self.grid_layout.addWidget(card, row, col)
            self.cards[cluster["branch_key"]] = card

        ### Fix (reapply selection after rebuilding) ###
        # Re-apply selection highlight after rebuild
        for key in self.selected_clusters:
            if key in self.cards:
                self._update_card_highlight(key, True)

        # Update layout
        self.grid_widget.updateGeometry()

    def filter_clusters(self, query: str):
        """Filter clusters by search query."""
        query = query.lower().strip()

        if not query:
            self.filtered_clusters = self.clusters.copy()
        else:
            self.filtered_clusters = [
                c for c in self.clusters
                if query in c.get("display_name", "").lower()
            ]

        self.update_grid()
        self.update_status()

    def sort_clusters(self, rebuild: bool = True):
        """Sort clusters based on selected criterion."""
        sort_by = self.sort_combo.currentText()

        if sort_by == "Most photos":
            self.filtered_clusters.sort(key=lambda c: c.get("member_count", 0), reverse=True)
        elif sort_by == "Name (A-Z)":
            self.filtered_clusters.sort(key=lambda c: c.get("display_name", "").lower())
        elif sort_by == "Recently added":
            # Assuming branch_key contains timestamp info or use id
            self.filtered_clusters.sort(key=lambda c: c.get("branch_key", ""), reverse=True)

        if rebuild:
            self.update_grid()
            self.update_status()

    def update_status(self):
        """Update status label."""
        total = len(self.clusters)
        shown = len(self.filtered_clusters)

        if shown == total:
            self.status_label.setText(f"👥 {total} people")
        else:
            self.status_label.setText(f"👥 Showing {shown} of {total} people")

    def _on_zoom_changed(self, value: int):
        """
        Handle zoom slider changes - update thumbnail size and rebuild grid.

        FEATURE #3 POLISH: User-requested zoom control for face thumbnails.
        Range: 128px (small) to 384px (large), default 192px.
        """
        self.thumbnail_size = value
        self.zoom_value_label.setText(f"{value}px")

        # Rebuild grid with new thumbnail size
        self._zoom_timer.start(80)  # Fixes with biggest ROI: Debounce zoom slider


    def on_cluster_clicked(self, branch_key: str):
        """
        FEATURE #3: Handle cluster card click with Shift+Click multi-selection support.

        Behavior:
        - Normal click: Show photos for this person
        - Shift+Click: Toggle selection for batch merge
        """
        modifiers = QApplication.keyboardModifiers()

        # DEEP DEBUG: Log click event details
        print(f"\n[ClusterClick] ========== CLUSTER CLICKED ==========")
        print(f"[ClusterClick] DEBUG: Clicked branch_key = {branch_key}")
        print(f"[ClusterClick] DEBUG: Shift modifier active? {bool(modifiers & Qt.ShiftModifier)}")
        print(f"[ClusterClick] DEBUG: selected_clusters BEFORE = {self.selected_clusters}")

        if modifiers & Qt.ShiftModifier:
            # FEATURE #3: Shift+Click toggles selection
            if branch_key in self.selected_clusters:
                self.selected_clusters.remove(branch_key)
                self._update_card_highlight(branch_key, False)
                print(f"[ClusterClick] DEBUG: Removed {branch_key} from selection")
            else:
                self.selected_clusters.append(branch_key)
                self._update_card_highlight(branch_key, True)
                print(f"[ClusterClick] DEBUG: Added {branch_key} to selection")

            print(f"[ClusterClick] DEBUG: selected_clusters AFTER = {self.selected_clusters}")
            print(f"[ClusterClick] DEBUG: Total selected = {len(self.selected_clusters)}")

            # Update status to show selection count
            if self.selected_clusters:
                self.status_label.setText(f"✓ {len(self.selected_clusters)} people selected (Shift+Click to select more, Right-click to merge)")
            else:
                self.update_status()

            print(f"[ClusterClick] =========================================\n")

        else:
            # Normal click: Clear selection and show photos for this person
            self._clear_selection()

            try:
                paths = self.db.get_paths_for_cluster(self.project_id, branch_key)

                if paths:
                    # Load photos in main grid
                    if self.parent() and hasattr(self.parent(), "grid"):
                        grid = self.parent().grid
                        grid.model.clear()
                        grid.load_custom_paths(paths, content_type="photos")

                        # Update status
                        cluster_name = next(
                            (c["display_name"] for c in self.clusters if c["branch_key"] == branch_key),
                            "Unknown"
                        )
                        self.parent().statusBar().showMessage(f"👤 Showing {len(paths)} photos of {cluster_name}")

                    # FIX: Don't close dialog - let user continue working with People Manager
                    # User can explicitly close with Close button or X
                    # self.accept()  # REMOVED: Dialog was closing on every single click
                else:
                    QMessageBox.information(self, "No Photos", f"No photos found for this person.")

            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load photos:\n{str(e)}")

    def on_cluster_renamed(self, branch_key: str, new_name: str):
        """Handle cluster rename."""
        try:
            # Update database
            with self.db._connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE face_branch_reps
                    SET label = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (new_name, self.project_id, branch_key))

                # Also update branches table
                cur.execute("""
                    UPDATE branches
                    SET display_name = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (new_name, self.project_id, branch_key))

                conn.commit()

            # Update local data
            for cluster in self.clusters:
                if cluster["branch_key"] == branch_key:
                    cluster["display_name"] = new_name
                    break

            print(f"[PeopleManager] Renamed {branch_key} to '{new_name}'")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to rename person:\n{str(e)}")

    def _handle_drag_merge(self, dragged_source: str, target_branch: str):
        # If user multi-selected and dragged one of the selected cards,
        # merge all selected (excluding target) into target.
        if self.selected_clusters and dragged_source in self.selected_clusters:
            source_keys = [k for k in self.selected_clusters if k != target_branch]
        else:
            source_keys = [dragged_source]

        if not source_keys:
            return

        target_name = self._get_cluster_name(target_branch)
        reply = QMessageBox.question(
            self,
            "Confirm Merge",
            f"Merge {len(source_keys)} into '{target_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.db.merge_face_clusters(
            project_id=self.project_id,
            target_branch=target_branch,
            source_branches=source_keys,
            log_undo=True
        )
        self._clear_selection()
        QTimer.singleShot(0, self.load_clusters)

    def on_merge_requested(self, source_branch_key: str):
        """Handle merge request."""
        # Get list of other clusters
        other_clusters = [
            c for c in self.clusters
            if c["branch_key"] != source_branch_key
        ]

        if not other_clusters:
            QMessageBox.information(self, "Merge", "No other people to merge with.")
            return

        # Show selection dialog
        names = [c.get("display_name", c["branch_key"]) for c in other_clusters]
        target_name, ok = QInputDialog.getItem(
            self,
            "Merge People",
            "Select person to merge with:",
            names,
            editable=False
        )

        if ok and target_name:
            # Find target cluster
            target_cluster = next(
                (c for c in other_clusters if c.get("display_name") == target_name),
                None
            )

            if target_cluster:
                self.merge_clusters(source_branch_key, target_cluster["branch_key"])

    def merge_clusters(self, source_key: str, target_key: str):
        """Merge two face clusters."""
        try:
            # Confirm merge
            source_name = next((c["display_name"] for c in self.clusters if c["branch_key"] == source_key), "Unknown")
            target_name = next((c["display_name"] for c in self.clusters if c["branch_key"] == target_key), "Unknown")

            reply = QMessageBox.question(
                self,
                "Confirm Merge",
                f"Merge '{source_name}' into '{target_name}'?\n\n"
                f"This will move all faces from '{source_name}' to '{target_name}'.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Perform merge in database
            print(f"[PeopleManager] Single merge: {source_key} → {target_key}")

            result = self.db.merge_face_clusters(
                project_id=self.project_id,
                target_branch=target_key,
                source_branches=[source_key],
                log_undo=True
            )

            # Reload in next tick so UI reliably refreshes
            QTimer.singleShot(0, self.load_clusters)

            moved_faces = result.get("moved_faces", 0)
            QMessageBox.information(self, "Merge Complete", f"Merged {moved_faces} face crops into '{target_name}'.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to merge clusters:\n{str(e)}")

    def _update_card_highlight(self, branch_key: str, selected: bool):
        """
        FEATURE #3: Update visual highlight for selected face card.

        Args:
            branch_key: Branch key of the face cluster
            selected: True to highlight, False to remove highlight
        """
        card = self.cards.get(branch_key)
        if not card:
            return

        if selected:
            # Apply blue border and checkmark (Google Photos pattern)
            card.setStyleSheet("""
                FaceClusterCard {
                    background-color: #e8f0fe;
                    border: 3px solid #1a73e8;
                    border-radius: 8px;
                }
            """)
            # Add checkmark overlay
            if not hasattr(card, 'checkmark_label') or card.checkmark_label is None:
                card.checkmark_label = QLabel("✓", card)
                card.checkmark_label.setStyleSheet("""
                    QLabel {
                        background-color: #1a73e8;
                        color: white;
                        border-radius: 12px;
                        font-size: 16px;
                        font-weight: bold;
                        padding: 2px;
                    }
                """)
                card.checkmark_label.setFixedSize(24, 24)
                card.checkmark_label.setAlignment(Qt.AlignCenter)

            # Position checkmark in top-right corner
            card.checkmark_label.move(card.width() - 30, 6)
            card.checkmark_label.show()
        else:
            # Remove highlight
            card.setStyleSheet("""
                FaceClusterCard {
                    background-color: white;
                    border-radius: 8px;
                }
                FaceClusterCard:hover {
                    background-color: #f8f8f8;
                    border: 2px solid #4CAF50;
                }
            """)
            # Hide checkmark
            if hasattr(card, 'checkmark_label') and card.checkmark_label:
                card.checkmark_label.hide()

    def _clear_selection(self):
        """FEATURE #3: Clear all selected clusters."""
        for branch_key in list(self.selected_clusters):
            self._update_card_highlight(branch_key, False)
        self.selected_clusters.clear()
        self.update_status()

    def _merge_selected_clusters(self):
        """
        FEATURE #3 POLISH: Merge all selected clusters - user chooses target.

        IMPROVEMENT: Instead of automatically using first selected as target,
        show dialog with list of selected faces so user can choose which one
        should be the target (requested by user feedback).
        """
        if len(self.selected_clusters) < 2:
            QMessageBox.warning(
                self,
                "Selection Required",
                "Please select at least 2 people to merge.\n\n"
                "Hold Shift and click on face cards to select multiple people."
            )
            return

        # FEATURE #3 POLISH: Let user choose target from selected faces
        # Build list of selected cluster names
        selected_names = []
        selected_mapping = {}  # Map display names to branch_keys
        for key in self.selected_clusters:
            name = self._get_cluster_name(key)
            selected_names.append(name)
            selected_mapping[name] = key

        # Show selection dialog to choose target
        target_name, ok = QInputDialog.getItem(
            self,
            "Choose Merge Target",
            f"You have selected {len(self.selected_clusters)} people.\n\n"
            f"Choose which person should be the TARGET\n"
            f"(all other selected faces will be merged into this one):",
            selected_names,
            editable=False
        )

        if not ok or not target_name:
            return

        # Get target key and determine sources
        target_key = selected_mapping[target_name]
        source_keys = [key for key in self.selected_clusters if key != target_key]

        if not source_keys:
            QMessageBox.information(self, "Merge", "No other people selected to merge.")
            return

        merge_count = len(source_keys)

        # Build list of source names for confirmation
        source_names = [self._get_cluster_name(key) for key in source_keys]
        source_list = "\n".join([f"  • {name}" for name in source_names])

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Confirm Multi-Merge",
            f"Merge {merge_count} people into '{target_name}'?\n\n"
            f"Source people:\n{source_list}\n\n"
            f"All faces from these {merge_count} people will be moved to '{target_name}'.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        # Perform multi-merge
        try:
            print(f"[PeopleManager] Starting multi-merge: {len(source_keys)} sources → {target_key}")
            print(f"[PeopleManager] Source keys: {source_keys}")
            print(f"[PeopleManager] Target key: {target_key}")

            result = self.db.merge_face_clusters(
                project_id=self.project_id,
                target_branch=target_key,
                source_branches=source_keys,
                log_undo=True
            )

            self._clear_selection()
            QTimer.singleShot(0, self.load_clusters)

            moved_faces = result.get("moved_faces", 0)
            print(f"[PeopleManager] Multi-merge complete: {moved_faces} face crops moved")

            QMessageBox.information(
                self,
                "Multi-Merge Complete",
                f"Successfully merged {moved_faces} face crops from {merge_count} people into '{target_name}'."
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Merge Failed",
                f"Failed to merge clusters:\n{str(e)}"
            )

    def _get_cluster_name(self, branch_key: str) -> str:
        """Helper to get display name for a cluster."""
        cluster = next(
            (c for c in self.clusters if c["branch_key"] == branch_key),
            None
        )
        return cluster.get("display_name", "Unknown") if cluster else "Unknown"

    def on_delete_requested(self, branch_key: str):
        """Handle delete request."""
        cluster_name = next((c["display_name"] for c in self.clusters if c["branch_key"] == branch_key), "Unknown")

        reply = QMessageBox.question(
            self,
            "Delete Person",
            f"Delete '{cluster_name}'?\n\n"
            f"This will remove all face crops and clustering data for this person.\n"
            f"Original photos will not be affected.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                self.db.delete_branch(self.project_id, branch_key)
                self.load_clusters()
                QMessageBox.information(self, "Deleted", f"Deleted '{cluster_name}'.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete person:\n{str(e)}")

    def run_face_detection(self):
        """
        FEATURE #1: Run face detection with scope selection dialog.

        Shows FaceDetectionScopeDialog first to let users choose which photos to process.
        Uses QThreadPool for non-blocking execution with progress dialog.
        """
        try:
            from config.face_detection_config import get_face_config
            from workers.face_detection_worker import FaceDetectionWorker
            from ui.face_detection_scope_dialog import FaceDetectionScopeDialog

            config = get_face_config()

            if not config.is_enabled():
                reply = QMessageBox.question(
                    self,
                    "Face Detection Disabled",
                    "Face detection is currently disabled.\n\nWould you like to enable it and continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    config.set("enabled", True)
                else:
                    return

            # FEATURE #1: Show scope selection dialog
            scope_dialog = FaceDetectionScopeDialog(self.project_id, parent=self)

            # Connect scope selection signal
            selected_paths = []
            selected_policy = "detect_only"
            selected_include_all = False

            def on_scope_selected(paths, policy, include_all):
                nonlocal selected_paths, selected_policy, selected_include_all
                selected_paths = paths
                selected_policy = policy
                selected_include_all = include_all

            scope_dialog.scopeSelected.connect(on_scope_selected)

            # Show dialog and wait for user selection
            if scope_dialog.exec() != QDialog.Accepted or not selected_paths:
                # User canceled or no photos selected
                return

            logger.info(
                f"[FaceDetection] User selected {len(selected_paths)} photos "
                f"for detection (policy={selected_policy}, include_all={selected_include_all})"
            )

            # Create worker with selected paths and policy
            self.face_detection_worker = FaceDetectionWorker(
                self.project_id,
                photo_paths=selected_paths,
                screenshot_policy=selected_policy,
                include_all_screenshot_faces=selected_include_all
            )

            # Connect signals for progress tracking (guarded against teardown)
            gen = int(getattr(self.parent() or self.window(), "_ui_generation", self._ui_generation))
            connect_guarded(self.face_detection_worker.signals.progress, self, self._on_face_detection_progress, generation=gen)
            connect_guarded(self.face_detection_worker.signals.finished, self, self._on_face_detection_finished, generation=gen)
            connect_guarded(self.face_detection_worker.signals.error, self, self._on_face_detection_error, generation=gen)

            # Create progress dialog
            self.face_detection_progress_dialog = QProgressDialog(
                "Detecting faces...",
                "Cancel",
                0,
                100,
                self
            )
            self.face_detection_progress_dialog.setWindowTitle("Face Detection")
            self.face_detection_progress_dialog.setWindowModality(Qt.WindowModal)
            self.face_detection_progress_dialog.setMinimumDuration(0)  # Show immediately
            self.face_detection_progress_dialog.canceled.connect(self._on_face_detection_canceled)

            # Start worker on thread pool (non-blocking!)
            QThreadPool.globalInstance().start(self.face_detection_worker)

        except Exception as e:
            logger.error(f"Failed to start face detection: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to start face detection:\n{str(e)}")

    @Slot(int, int, str)
    def _on_face_detection_progress(self, current: int, total: int, message: str):
        """
        Handle face detection progress updates.

        Args:
            current: Current progress value (0-based index)
            total: Total items to process
            message: Progress message (e.g., filename being processed)
        """
        if self.face_detection_progress_dialog is None:
            return

        # Update progress dialog
        if total > 0:
            percentage = int((current / total) * 100)
            self.face_detection_progress_dialog.setValue(percentage)

        # Update message with current file and progress
        progress_text = f"Processing photo {current + 1} of {total}\n\n{message}"
        self.face_detection_progress_dialog.setLabelText(progress_text)

    @Slot(int, int, int)
    def _on_face_detection_finished(self, success_count: int, failed_count: int, total_faces: int):
        """
        Handle face detection completion.

        Args:
            success_count: Number of successfully processed images
            failed_count: Number of failed images
            total_faces: Total faces detected
        """
        # Close progress dialog
        if self.face_detection_progress_dialog:
            self.face_detection_progress_dialog.close()
            self.face_detection_progress_dialog = None

        # Show completion message
        total_processed = success_count + failed_count
        message = f"Face detection completed!\n\n"
        message += f"• Images processed: {success_count}/{total_processed}\n"
        message += f"• Faces detected: {total_faces}\n"

        if failed_count > 0:
            message += f"• Failed: {failed_count}\n"

        QMessageBox.information(self, "Face Detection Complete", message)

        # Reload clusters to show new faces
        self.load_clusters()

        # Clear worker reference
        self.face_detection_worker = None

    @Slot(str, str)
    def _on_face_detection_error(self, image_path: str, error_message: str):
        """
        Handle face detection errors for individual images.

        Args:
            image_path: Path to image that failed
            error_message: Error description
        """
        # Log error (don't show dialog for each error, would be too disruptive)
        print(f"[PeopleManager] Face detection error for {image_path}: {error_message}")

    @Slot()
    def _on_face_detection_canceled(self):
        """Handle face detection cancellation by user."""
        if self.face_detection_worker:
            print("[PeopleManager] User requested face detection cancellation")
            self.face_detection_worker.cancel()

    def recluster_faces(self):
        """Re-run face clustering."""
        try:
            from config.face_detection_config import get_face_config
            from workers.face_cluster_worker import cluster_faces

            config = get_face_config()
            params = config.get_clustering_params()

            # Show progress
            QMessageBox.information(
                self,
                "Reclustering",
                f"Reclustering faces...\n\n"
                f"This may take a few moments.\n\n"
                f"Parameters:\n"
                f"• Epsilon: {params['eps']}\n"
                f"• Min samples: {params['min_samples']}"
            )

            # Run clustering
            cluster_faces(self.project_id, eps=params["eps"], min_samples=params["min_samples"])

            # Reload clusters
            self.load_clusters()

            QMessageBox.information(self, "Reclustering Complete", "Face clustering has been updated.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Reclustering failed:\n{str(e)}")

    def open_settings(self):
        """Open face detection settings."""
        try:
            from ui.face_settings_dialog import FaceSettingsDialog

            dialog = FaceSettingsDialog(self)
            if dialog.exec():
                # Reload if settings changed
                self.load_clusters()

        except ImportError:
            QMessageBox.warning(self, "Settings", "Face settings dialog not available.")
