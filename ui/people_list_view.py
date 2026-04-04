"""
PeopleListView - Apple-style people/face cluster list widget

A dedicated widget for displaying face clusters with:
- Large circular thumbnails (96x96 px)
- Apple/Google Photos-style hover effects
- Search filtering
- Context menu (Rename, Export)
- Sortable columns

Signals:
- personActivated(branch_key) - When person is double-clicked
- personRenameRequested(branch_key) - When rename is requested
- personExportRequested(branch_key) - When export is requested
"""

import os
from io import BytesIO
from PySide6.QtCore import Qt, Signal, QSize, QEvent
from PySide6.QtGui import (
    QPixmap, QIcon, QImage, QColor, QPainter, QPainterPath,
    QPen, QBrush
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QLabel, QHBoxLayout, QMenu,
    QInputDialog, QMessageBox, QStyledItemDelegate, QStyle
)
from PIL import Image, ImageOps
from translation_manager import tr


# =====================================================================
# Circular Thumbnail Helper
# =====================================================================

def make_circular_pixmap(pixmap: QPixmap, size: int = 96) -> QPixmap:
    """
    Convert a square pixmap to a circular (round-masked) pixmap.

    Args:
        pixmap: Source pixmap (will be scaled to size x size)
        size: Diameter of the circular thumbnail

    Returns:
        Circular pixmap with transparent background outside the circle
    """
    # Scale to target size
    scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    # Create circular mask
    circular = QPixmap(size, size)
    circular.fill(Qt.transparent)

    painter = QPainter(circular)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)

    # Create circular clip path
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)

    # Draw the image within the circle
    # Center the image if it's larger than the circle
    x_offset = (scaled.width() - size) // 2
    y_offset = (scaled.height() - size) // 2
    painter.drawPixmap(-x_offset, -y_offset, scaled)

    painter.end()

    return circular


# =====================================================================
# Custom Delegate for Hover Effects
# =====================================================================

class PeopleListDelegate(QStyledItemDelegate):
    """
    Custom delegate for Apple-style hover effects and styling.

    Features:
    - Subtle hover highlighting
    - Rounded selection rectangles
    - Centered content alignment
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_row = -1

    def paint(self, painter, option, index):
        """Custom paint with rounded rectangles and hover effects"""
        painter.save()

        try:
            painter.setRenderHint(QPainter.Antialiasing)

            # Check if this row is hovered or selected
            is_selected = option.state & QStyle.StateFlag.State_Selected
            is_hovered = index.row() == self.hover_row

            # Draw background with rounded corners for selection/hover
            if is_selected or is_hovered:
                rect = option.rect
                painter.setPen(Qt.NoPen)

                if is_selected:
                    # Selected: More prominent blue background
                    painter.setBrush(QBrush(QColor(0, 122, 255, 40)))  # Subtle blue
                elif is_hovered:
                    # Hovered: Very subtle grey
                    painter.setBrush(QBrush(QColor(0, 0, 0, 10)))

                painter.drawRoundedRect(rect, 6, 6)  # Rounded corners

            painter.restore()

            # Let default delegate handle the actual content
            super().paint(painter, option, index)
        except Exception as e:
            # Ensure painter is restored even if an exception occurs
            painter.restore()
            raise


# =====================================================================
# PeopleListView Widget
# =====================================================================

class PeopleListView(QWidget):
    """
    Apple-style people/face cluster list widget.

    Displays face clusters with large circular thumbnails, search,
    sorting, and context menu operations.
    """

    # Signals
    personActivated = Signal(str)        # branch_key
    personRenameRequested = Signal(str)  # branch_key
    personExportRequested = Signal(str)  # branch_key

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.project_id = None
        self._all_rows = []  # For search filtering
        self._is_being_deleted = False  # Track deletion state
        self.delegate = None  # Initialize early to avoid AttributeError
        self.table = None  # Initialize early to avoid AttributeError
        self._init_ui()

    def __del__(self):
        """Cleanup when widget is being deleted"""
        self._cleanup()

    def _cleanup(self):
        """Remove event filter, disconnect signals, and cleanup resources"""
        if self._is_being_deleted:
            return
        self._is_being_deleted = True

        # CRITICAL FIX: Disconnect all signals before widget deletion
        # This prevents signals from firing after deleteLater() is called
        # which was causing crashes when processEvents() processed pending signals
        try:
            if hasattr(self, 'search_box') and self.search_box:
                # Disconnect textChanged signal to prevent crash on pending text changes
                try:
                    self.search_box.textChanged.disconnect()
                except (RuntimeError, AttributeError, TypeError):
                    pass
        except (RuntimeError, AttributeError):
            pass

        try:
            if hasattr(self, 'table') and self.table:
                # Disconnect table signals
                try:
                    self.table.customContextMenuRequested.disconnect()
                except (RuntimeError, AttributeError, TypeError):
                    pass
                try:
                    self.table.cellDoubleClicked.disconnect()
                except (RuntimeError, AttributeError, TypeError):
                    pass

                # Remove event filter from viewport
                if hasattr(self.table, 'viewport'):
                    try:
                        viewport = self.table.viewport()
                        if viewport:
                            viewport.removeEventFilter(self)
                    except (RuntimeError, AttributeError):
                        pass
        except (RuntimeError, AttributeError):
            # Widget already deleted or C++ object gone
            pass

    def closeEvent(self, event):
        """Handle widget close event"""
        self._cleanup()
        super().closeEvent(event)

    def _init_ui(self):
        """Initialize the UI layout"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Search bar
        search_container = QWidget()
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(8)

        search_label = QLabel("🔍")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr('search.placeholder_filter_people'))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_changed)

        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_box, 1)
        layout.addWidget(search_container)

        # Table widget
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Face", "Person", "Photos"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)

        # Column sizing: Face (110px fixed) | Person (stretch) | Photos (fit)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 110)  # Face thumbnail column
        self.table.setIconSize(QSize(96, 96))  # Large thumbnails

        # Row height for 96px thumbnails
        self.table.verticalHeader().setDefaultSectionSize(110)  # 96px + padding

        # Enable sorting
        self.table.setSortingEnabled(True)

        # Apple-style delegate for hover effects
        self.delegate = PeopleListDelegate(self.table)
        self.table.setItemDelegate(self.delegate)

        # Context menu
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click to activate person
        self.table.cellDoubleClicked.connect(self._on_person_double_clicked)

        # Hover tracking (for custom delegate)
        self.table.setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

        layout.addWidget(self.table, 1)

        # Apply Apple-style stylesheet
        self._apply_stylesheet()

    def _apply_stylesheet(self):
        """Apply Apple/Google Photos-style stylesheet"""
        self.table.setStyleSheet("""
            QTableWidget {
                border: none;
                background-color: transparent;
                gridline-color: transparent;
                outline: none;
            }

            QTableWidget::item {
                border: none;
                padding: 4px;
                border-radius: 6px;
            }

            QTableWidget::item:selected {
                background-color: rgba(0, 122, 255, 40);
                color: inherit;
            }

            QTableWidget::item:hover {
                background-color: rgba(0, 0, 0, 10);
            }

            QHeaderView::section {
                background-color: transparent;
                border: none;
                border-bottom: 1px solid #E0E0E0;
                padding: 6px;
                font-weight: 600;
                font-size: 11px;
                color: #666666;
            }

            QScrollBar:vertical {
                border: none;
                background-color: transparent;
                width: 10px;
            }

            QScrollBar::handle:vertical {
                background-color: rgba(0, 0, 0, 0.2);
                border-radius: 5px;
                min-height: 20px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: rgba(0, 0, 0, 0.3);
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

    def eventFilter(self, obj, event):
        """Track mouse hover for custom delegate"""
        # Skip if widget is being deleted or not fully initialized
        if self._is_being_deleted or not hasattr(self, 'delegate') or not hasattr(self, 'table'):
            return False

        try:
            if obj == self.table.viewport():
                if event.type() == QEvent.Type.MouseMove:
                    pos = event.pos()
                    row = self.table.rowAt(pos.y())
                    if row != self.delegate.hover_row:
                        self.delegate.hover_row = row
                        self.table.viewport().update()
                elif event.type() == QEvent.Type.Leave:
                    self.delegate.hover_row = -1
                    self.table.viewport().update()
        except (RuntimeError, AttributeError):
            # Widget being deleted or C++ object gone
            return False

        return super().eventFilter(obj, event)

    def set_database(self, db, project_id):
        """Set the database connection and project ID"""
        self.db = db
        self.project_id = project_id

    def load_people(self, rows: list):
        """
        Load people/face clusters into the table.

        Args:
            rows: List of dicts from get_face_clusters(), each with:
                  - branch_key: str
                  - display_name: str (optional)
                  - member_count: int
                  - rep_path: str (path to representative face crop)
                  - rep_thumb_png: bytes (optional PNG thumbnail)
        """
        # Disable sorting while populating
        was_sorting = self.table.isSortingEnabled()
        if was_sorting:
            self.table.setSortingEnabled(False)

        self.table.setRowCount(len(rows))
        self._all_rows = []

        for row_idx, row in enumerate(rows):
            branch_key = row['branch_key']
            raw_name = row.get("display_name") or row.get("branch_key")
            count = row.get("member_count", 0)
            rep_path = row.get("rep_path", "")
            rep_thumb_png = row.get("rep_thumb_png")

            # Humanize unnamed clusters: "face_003" → "Unnamed #3"
            if raw_name.startswith("face_"):
                try:
                    cluster_num = int(raw_name.split("_")[1])
                    display_name = f"Unnamed #{cluster_num}"
                except (IndexError, ValueError):
                    display_name = raw_name
            else:
                display_name = raw_name

            # Column 0: Face thumbnail (circular)
            item_thumb = QTableWidgetItem()
            item_thumb.setData(Qt.UserRole, f"facecluster:{branch_key}")

            # Load thumbnail with EXIF correction and circular masking
            pixmap = self._load_thumbnail(rep_path, rep_thumb_png)
            if pixmap and not pixmap.isNull():
                # Make it circular
                circular_pixmap = make_circular_pixmap(pixmap, 96)
                item_thumb.setIcon(QIcon(circular_pixmap))

            self.table.setItem(row_idx, 0, item_thumb)

            # Column 1: Person name
            item_name = QTableWidgetItem(display_name)
            item_name.setData(Qt.UserRole, f"facecluster:{branch_key}")
            item_name.setData(Qt.UserRole + 1, branch_key)  # Store branch_key for operations
            if rep_path:
                item_name.setToolTip(f"{display_name}\n{count} photo(s)")
            self.table.setItem(row_idx, 1, item_name)

            # Column 2: Photo count (right-aligned, grey)
            item_count = QTableWidgetItem()
            item_count.setData(Qt.DisplayRole, count)  # Use int for proper sorting
            item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_count.setForeground(QColor("#888888"))
            self.table.setItem(row_idx, 2, item_count)

            # Store for search filtering
            self._all_rows.append({
                'row_idx': row_idx,
                'name': display_name.lower(),
                'branch_key': branch_key
            })

        # Re-enable sorting
        if was_sorting:
            self.table.setSortingEnabled(True)

        # Sort by count descending by default
        self.table.sortItems(2, Qt.DescendingOrder)

    def _load_thumbnail(self, rep_path: str, rep_thumb_png: bytes = None) -> QPixmap:
        """
        Load thumbnail from PNG bytes or file path with EXIF correction.

        Args:
            rep_path: File path to representative face crop
            rep_thumb_png: Optional PNG bytes from database

        Returns:
            QPixmap or None if loading failed
        """
        pixmap = None

        # Try loading from PNG bytes first (faster)
        if rep_thumb_png:
            try:
                from PySide6.QtCore import QByteArray
                pixmap = QPixmap()
                if pixmap.loadFromData(QByteArray(rep_thumb_png)):
                    return pixmap
            except Exception as e:
                print(f"[PeopleListView] Failed to load PNG thumbnail: {e}")

        # Try loading from file path with EXIF correction
        if rep_path and os.path.exists(rep_path):
            try:
                # BUG-C2 FIX: Use context manager to prevent resource leak
                with Image.open(rep_path) as pil_image:
                    pil_image = ImageOps.exif_transpose(pil_image)  # Auto-rotate based on EXIF

                    # Convert PIL Image to QPixmap
                    if pil_image.mode != 'RGB':
                        pil_image = pil_image.convert('RGB')

                    # Convert to bytes and load into QImage
                    buffer = BytesIO()
                    pil_image.save(buffer, format='PNG')
                    image = QImage.fromData(buffer.getvalue())

                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    return pixmap
            except Exception as e:
                print(f"[PeopleListView] Failed to load face thumbnail from {rep_path}: {e}")
                # Fallback to direct QPixmap loading
                try:
                    pixmap = QPixmap(rep_path)
                    if not pixmap.isNull():
                        return pixmap
                except Exception as e:
                    # BUG-H7 FIX: Log QPixmap loading failures
                    print(f"[PeopleListView] Failed to load QPixmap from {rep_path}: {e}")

        return pixmap

    def _on_search_changed(self, text: str):
        """Filter table rows based on search text"""
        search_term = text.lower().strip()

        # Disable sorting temporarily for performance
        was_sorting = self.table.isSortingEnabled()
        if was_sorting:
            self.table.setSortingEnabled(False)

        for row_data in self._all_rows:
            row_idx = row_data['row_idx']
            if not search_term or search_term in row_data['name']:
                self.table.setRowHidden(row_idx, False)
            else:
                self.table.setRowHidden(row_idx, True)

        # Re-enable sorting
        if was_sorting:
            self.table.setSortingEnabled(True)

    def _on_person_double_clicked(self, row: int, col: int):
        """Handle person double-click to activate"""
        item = self.table.item(row, 1)  # Get name item
        if item:
            branch_data = item.data(Qt.UserRole)  # "facecluster:face_xxx"
            if branch_data:
                self.personActivated.emit(branch_data)

    def _show_context_menu(self, pos):
        """Show context menu with rename and export options"""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return

        item = self.table.item(row, 1)  # Get name item
        if not item:
            return

        branch_key = item.data(Qt.UserRole + 1)
        current_name = item.text()

        menu = QMenu(self.table)
        act_rename = menu.addAction("✏️ Rename Person…")
        menu.addSeparator()
        act_export = menu.addAction("📁 Export Photos to Folder…")

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))

        if chosen is act_rename:
            self._handle_rename(row, branch_key, current_name)
        elif chosen is act_export:
            self.personExportRequested.emit(branch_key)

    def _handle_rename(self, row: int, branch_key: str, current_name: str):
        """Handle rename person action"""
        new_name, ok = QInputDialog.getText(
            self.table, "Rename Person",
            "Person name:",
            text=current_name if not current_name.startswith("Unnamed #") else ""
        )

        if ok and new_name.strip() and new_name.strip() != current_name:
            try:
                # Emit signal for parent to handle database update
                self.personRenameRequested.emit(branch_key)

                # If db is available, update directly
                if self.db and self.project_id:
                    if hasattr(self.db, 'rename_branch_display_name'):
                        self.db.rename_branch_display_name(self.project_id, branch_key, new_name.strip())
                    else:
                        # Fallback: direct SQL update
                        with self.db._connect() as conn:
                            conn.execute("""
                                UPDATE branches SET display_name = ?
                                WHERE project_id = ? AND branch_key = ?
                            """, (new_name.strip(), self.project_id, branch_key))
                            conn.execute("""
                                UPDATE face_branch_reps SET label = ?
                                WHERE project_id = ? AND branch_key = ?
                            """, (new_name.strip(), self.project_id, branch_key))
                            conn.commit()

                    # Update UI immediately
                    item = self.table.item(row, 1)
                    if item:
                        item.setText(new_name.strip())

                    # CRITICAL FIX: Notify parent sidebar to update list view tree model
                    # This ensures rename in tabs view syncs to list view
                    # SAFETY: Wrapped in try-except to prevent crashes from widget access
                    try:
                        parent_widget = self.parent()
                        if parent_widget and hasattr(parent_widget, 'parent'):
                            sidebar = parent_widget.parent()

                            # Check if sidebar is still valid before calling method
                            if sidebar and hasattr(sidebar, '_update_person_name_in_tree'):
                                try:
                                    # Test sidebar validity
                                    _ = sidebar.isVisible()
                                    print(f"[PeopleListView] Syncing rename to list view: {branch_key} → {new_name.strip()}")
                                    sidebar._update_person_name_in_tree(branch_key, new_name.strip())
                                except (RuntimeError, AttributeError) as err:
                                    # Sidebar widget deleted or C++ object gone
                                    print(f"[PeopleListView] Sidebar deleted, skipping sync: {err}")
                    except Exception as sync_err:
                        print(f"[PeopleListView] Failed to sync rename to list view: {sync_err}")

                    QMessageBox.information(self.table, "Renamed", f"Person renamed to '{new_name.strip()}'")
            except Exception as e:
                QMessageBox.critical(self.table, "Rename Failed", str(e))

    def get_total_faces(self) -> int:
        """Get total number of faces across all clusters"""
        total = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)  # Count column
            if item:
                count_data = item.data(Qt.DisplayRole)
                if count_data is not None:
                    # Handle different types that Qt might return
                    if isinstance(count_data, (int, float)):
                        total += int(count_data)
                    elif isinstance(count_data, bytes):
                        # Convert bytes to int if needed
                        try:
                            total += int.from_bytes(count_data, byteorder='little')
                        except (ValueError, OverflowError):
                            pass  # Skip invalid bytes
                    elif isinstance(count_data, str):
                        # Convert string to int if needed
                        try:
                            total += int(count_data)
                        except ValueError:
                            pass  # Skip invalid strings
        return total

    def get_people_count(self) -> int:
        """Get number of people/clusters"""
        return self.table.rowCount()
