"""
Google Photos Layout - UI Widgets
Extracted widget classes for better organization and reusability.

Contains:
- FlowLayout: Custom layout for flowing grid items
- CollapsibleSection: Animated collapsible section widget
- PersonCard: Individual person card with face thumbnail
- PeopleGridView: Grid view for displaying people cards
"""

from PySide6.QtWidgets import (
    QWidget, QLayout, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QMenu, QMessageBox, QApplication, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize, QRect, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPixmap, QPainterPath, QDrag
from PySide6.QtCore import QMimeData


class FlowLayout(QLayout):
    """
    Flow layout that arranges items left-to-right, wrapping to next row when needed.
    Perfect for grid views where items should flow naturally.

    Based on Qt's Flow Layout example, adapted for sidebar people grid.
    """
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.itemList = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.itemList.append(item)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margin, _, _, _ = self.getContentsMargins()
        size += QSize(2 * margin, 2 * margin)
        return size

    def _do_layout(self, rect, test_only):
        """Arrange items in flow layout."""
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self.itemList:
            widget = item.widget()
            space_x = spacing + widget.style().layoutSpacing(
                QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal
            )
            space_y = spacing + widget.style().layoutSpacing(
                QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Vertical
            )

            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()


class CollapsibleSection(QWidget):
    """
    Collapsible section with smooth expand/collapse animation.

    Features:
    - Click header to toggle expand/collapse
    - Smooth QPropertyAnimation (200ms)
    - Shows item count badge
    - Visual indicators (▼ expanded, ▶ collapsed)
    - Content area can contain any widget
    """
    def __init__(self, title, icon, count=0, parent=None):
        super().__init__(parent)
        self.is_expanded = True
        self.title = title
        self.icon = icon
        self.count = count

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar with actions area
        self.header_bar = QWidget()
        hb = QHBoxLayout(self.header_bar)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(4)

        self.header_btn = QPushButton(f"▼ {icon} {title}  ({count})")
        self.header_btn.setFlat(True)
        self.header_btn.setCursor(Qt.PointingHandCursor)
        self.header_btn.setStyleSheet("""
            QPushButton {
                text-align: left;
                font-size: 11pt;
                font-weight: bold;
                color: #202124;
                border: none;
                padding: 8px 4px;
                background: transparent;
            }
            QPushButton:hover {
                color: #1a73e8;
                background: rgba(26, 115, 232, 0.08);
                border-radius: 4px;
            }
        """)
        self.header_btn.clicked.connect(self.toggle)
        hb.addWidget(self.header_btn, 1)

        # Actions container on the right
        self._header_actions_container = QWidget()
        self.header_actions = QHBoxLayout(self._header_actions_container)
        self.header_actions.setContentsMargins(0, 0, 0, 0)
        self.header_actions.setSpacing(4)
        hb.addWidget(self._header_actions_container, 0)

        main_layout.addWidget(self.header_bar)

        # Content widget (collapsible)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        main_layout.addWidget(self.content_widget)

        # Animation for smooth expand/collapse
        self.animation = QPropertyAnimation(self.content_widget, b"maximumHeight")
        self.animation.setDuration(200)  # 200ms smooth
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)

    def toggle(self):
        """Toggle expand/collapse."""
        if self.is_expanded:
            self.collapse()
        else:
            self.expand()

    def collapse(self):
        """Collapse section (hide content)."""
        self.animation.setStartValue(self.content_widget.height())
        self.animation.setEndValue(0)
        self.animation.start()
        self.is_expanded = False
        self.header_btn.setText(f"▶ {self.icon} {self.title}  ({self.count})")
        print(f"[CollapsibleSection] Collapsed: {self.title}")

    def expand(self):
        """Expand section (show content)."""
        self.content_widget.setMaximumHeight(16777215)  # Remove max height limit
        content_height = self.content_widget.sizeHint().height()

        # CRITICAL FIX: Ensure minimum visible height for content
        # If sizeHint() returns tiny value (e.g., <100px), use reasonable default
        # This prevents People grid from being too tiny to see faces
        if content_height < 100:
            content_height = 250  # Reasonable default for ~2 rows of face cards

        self.animation.setStartValue(0)
        self.animation.setEndValue(content_height)
        self.animation.start()
        self.is_expanded = True
        self.header_btn.setText(f"▼ {self.icon} {self.title}  ({self.count})")
        print(f"[CollapsibleSection] Expanded: {self.title}")

    def update_count(self, count):
        """Update count badge."""
        self.count = count
        arrow = "▼" if self.is_expanded else "▶"
        self.header_btn.setText(f"{arrow} {self.icon} {self.title}  ({count})")

    def add_widget(self, widget):
        """Add widget to content area."""
        self.content_layout.addWidget(widget)

    def add_header_action(self, widget):
        """Add a small action widget to the header right side."""
        try:
            self.header_actions.addWidget(widget)
        except Exception:
            pass

    def cleanup(self):
        """
        CRITICAL FIX: Clean up animation and signals to prevent memory leaks.
        Addresses Issue #8 from audit: Animation continues after widget deletion.
        """
        # Disconnect header button signal
        if hasattr(self, 'header_btn'):
            try:
                self.header_btn.clicked.disconnect(self.toggle)
            except:
                pass

        # Stop and clean up animation
        if hasattr(self, 'animation'):
            try:
                self.animation.stop()
                self.animation.setTargetObject(None)  # Break reference to widget
                self.animation.deleteLater()
            except:
                pass


class PersonCard(QWidget):
    """
    Single person card with circular face thumbnail and name.

    Features:
    - 80x100px compact card size
    - Circular face thumbnail (64px diameter)
    - Name label (truncated if long)
    - Photo count badge
    - Hover effect
    - Click to filter by person
    - Context menu for rename/merge/delete
    - Drag-and-drop merge support
    """
    clicked = Signal(str)  # Emits branch_key when clicked
    context_menu_requested = Signal(str, str)  # Emits (branch_key, display_name)
    drag_merge_requested = Signal(str, str)  # Emits (source_branch, target_branch)

    def __init__(self, branch_key, display_name, face_pixmap, photo_count, parent=None):
        """
        Args:
            branch_key: Unique identifier for this person (e.g., "cluster_0")
            display_name: Human-readable name to display (e.g., "John" or "Unnamed")
            face_pixmap: QPixmap with face thumbnail
            photo_count: Number of photos with this person
        """
        super().__init__(parent)
        self.branch_key = branch_key
        self.display_name = display_name
        self.person_name = branch_key  # Keep for backward compatibility
        self.setFixedSize(80, 100)
        self.setCursor(Qt.PointingHandCursor)

        # Enable drag-and-drop
        self.setAcceptDrops(True)

        self.setStyleSheet("""
            PersonCard {
                background: transparent;
                border-radius: 6px;
            }
            PersonCard:hover {
                background: rgba(26, 115, 232, 0.08);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        # Circular face thumbnail
        self.face_label = QLabel()
        if face_pixmap and not face_pixmap.isNull():
            # Make circular mask
            circular_pixmap = self._make_circular(face_pixmap, 64)
            self.face_label.setPixmap(circular_pixmap)
        else:
            # Placeholder if no face image
            self.face_label.setPixmap(QPixmap())
            self.face_label.setFixedSize(64, 64)
            self.face_label.setStyleSheet("""
                QLabel {
                    background: #e8eaed;
                    border-radius: 32px;
                    font-size: 24pt;
                }
            """)
            self.face_label.setText("👤")
            self.face_label.setAlignment(Qt.AlignCenter)

        self.face_label.setFixedSize(64, 64)
        self.face_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.face_label)

        # Name label
        self.name_label = QLabel(display_name if len(display_name) <= 10 else display_name[:9] + "…")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(False)
        self.name_label.setStyleSheet("""
            QLabel {
                font-size: 9pt;
                color: #202124;
                font-weight: 500;
            }
        """)
        self.name_label.setToolTip(f"{display_name} ({photo_count} photos)")
        layout.addWidget(self.name_label)

        # Count badge with confidence icon
        conf = "✅" if photo_count >= 10 else ("⚠️" if photo_count >= 5 else "❓")
        self.count_label = QLabel(f"{conf} ({photo_count})")
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet("""
            QLabel {
                font-size: 8pt;
                color: #5f6368;
            }
        """)
        layout.addWidget(self.count_label)

    def _make_circular(self, pixmap, size):
        """Convert pixmap to circular thumbnail."""
        # Scale to size while maintaining aspect ratio
        scaled = pixmap.scaled(
            size, size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        )

        # Crop to square
        if scaled.width() > size or scaled.height() > size:
            x = (scaled.width() - size) // 2
            y = (scaled.height() - size) // 2
            scaled = scaled.copy(x, y, size, size)

        # Create circular mask
        output = QPixmap(size, size)
        output.fill(Qt.transparent)

        painter = QPainter(output)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Draw circle path
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        # Draw image
        painter.drawPixmap(0, 0, scaled)
        painter.end()

        return output

    def mousePressEvent(self, event):
        """Handle click and drag initiation on person card."""
        if event.button() == Qt.LeftButton:
            # Store drag start position for drag detection
            self.drag_start_pos = event.pos()
        elif event.button() == Qt.RightButton:
            # Show context menu
            self._show_context_menu(event.globalPos())

    def mouseMoveEvent(self, event):
        """Handle drag operation."""
        if not (event.buttons() & Qt.LeftButton):
            return
        if not hasattr(self, 'drag_start_pos'):
            return

        # Check if drag threshold exceeded
        if (event.pos() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        # Start drag operation
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(f"person_branch:{self.branch_key}:{self.display_name}")
        drag.setMimeData(mime_data)

        # Create drag pixmap (semi-transparent face)
        if self.face_label.pixmap() and not self.face_label.pixmap().isNull():
            drag_pixmap = QPixmap(self.face_label.pixmap())
        else:
            # Create placeholder
            drag_pixmap = QPixmap(64, 64)
            drag_pixmap.fill(Qt.transparent)
            painter = QPainter(drag_pixmap)
            painter.drawText(drag_pixmap.rect(), Qt.AlignCenter, "👤")
            painter.end()

        drag.setPixmap(drag_pixmap)
        drag.setHotSpot(QPoint(32, 32))

        # Execute drag
        drag.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, event):
        """Handle click after mouse release (if not dragged)."""
        if event.button() == Qt.LeftButton:
            # Only emit click if we didn't drag
            if hasattr(self, 'drag_start_pos'):
                if (event.pos() - self.drag_start_pos).manhattanLength() < 5:
                    self.clicked.emit(self.branch_key)
                    print(f"[PersonCard] Clicked: {self.display_name} (branch: {self.branch_key})")
                delattr(self, 'drag_start_pos')

    def dragEnterEvent(self, event):
        """Handle drag enter (highlight as drop target)."""
        if event.mimeData().hasText() and event.mimeData().text().startswith("person_branch:"):
            # Extract source branch
            parts = event.mimeData().text().split(":")
            if len(parts) >= 2:
                source_branch = parts[1]
                # Don't allow dropping onto self
                if source_branch != self.branch_key:
                    event.acceptProposedAction()
                    self.setStyleSheet("""
                        PersonCard {
                            background: rgba(26, 115, 232, 0.2);
                            border: 2px dashed #1a73e8;
                            border-radius: 6px;
                        }
                    """)

    def dragLeaveEvent(self, event):
        """Handle drag leave (remove highlight)."""
        self.setStyleSheet("""
            PersonCard {
                background: transparent;
                border-radius: 6px;
            }
            PersonCard:hover {
                background: rgba(26, 115, 232, 0.08);
            }
        """)

    def dropEvent(self, event):
        """Handle drop (initiate merge)."""
        if event.mimeData().hasText() and event.mimeData().text().startswith("person_branch:"):
            parts = event.mimeData().text().split(":")
            if len(parts) >= 3:
                source_branch = parts[1]
                source_name = parts[2]

                # Confirm merge
                reply = QMessageBox.question(
                    self,
                    "Confirm Drag-Drop Merge",
                    f"🔄 Merge '{source_name}' into '{self.display_name}'?\n\n"
                    f"This will move all faces from '{source_name}' to '{self.display_name}'.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    event.acceptProposedAction()
                    self.drag_merge_requested.emit(source_branch, self.branch_key)

                # Reset style
                self.setStyleSheet("""
                    PersonCard {
                        background: transparent;
                        border-radius: 6px;
                    }
                    PersonCard:hover {
                        background: rgba(26, 115, 232, 0.08);
                    }
                """)

    def _show_context_menu(self, global_pos):
        """Show context menu for rename/merge/delete."""
        menu = QMenu(self)

        # Rename action
        rename_action = menu.addAction("✏️ Rename Person")
        rename_action.triggered.connect(lambda: self.context_menu_requested.emit(self.branch_key, "rename"))

        # Merge action
        merge_action = menu.addAction("🔗 Merge with Another Person")
        merge_action.triggered.connect(lambda: self.context_menu_requested.emit(self.branch_key, "merge"))

        # Suggest merge action
        suggest_action = menu.addAction("🤝 Suggest Merge…")
        suggest_action.triggered.connect(lambda: self.context_menu_requested.emit(self.branch_key, "suggest_merge"))

        # View details action
        details_action = menu.addAction("👁️ View Details…")
        details_action.triggered.connect(lambda: self.context_menu_requested.emit(self.branch_key, "details"))

        menu.addSeparator()

        # Delete action
        delete_action = menu.addAction("🗑️ Delete Person")
        delete_action.triggered.connect(lambda: self.context_menu_requested.emit(self.branch_key, "delete"))

        menu.addSeparator()
        review_action = menu.addAction("📝 Review Unnamed People…")
        review_action.triggered.connect(lambda: self.context_menu_requested.emit(self.branch_key, "review_unnamed"))

        menu.exec(global_pos)

    def cleanup(self):
        """
        CRITICAL FIX: Disconnect signals to prevent memory leaks.
        Addresses Issue #1 from audit: Signals never disconnected.
        """
        # Disconnect all signals
        try:
            self.clicked.disconnect()
        except:
            pass

        try:
            self.context_menu_requested.disconnect()
        except:
            pass

        try:
            self.drag_merge_requested.disconnect()
        except:
            pass


class PeopleGridView(QWidget):
    """
    Grid view for displaying people with face thumbnails.

    Replaces tree view for better space utilization.
    Uses FlowLayout to arrange PersonCards in responsive grid.

    Features:
    - Flow layout (wraps to next row automatically)
    - Scrollable (can handle 100+ people)
    - Circular face thumbnails
    - Click to filter by person
    - Empty state message
    - Drag-and-drop merge support
    """
    person_clicked = Signal(str)  # Emits branch_key when clicked
    context_menu_requested = Signal(str, str)  # Emits (branch_key, action)
    drag_merge_requested = Signal(str, str)  # Emits (source_branch, target_branch)

    def __init__(self, parent=None):
        super().__init__(parent)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # CRITICAL FIX: Set minimum height so faces are visible (not tiny!)
        # With 80x100px cards + spacing, 3 rows = ~340px minimum
        self.scroll_area.setMinimumHeight(340)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)

        # Container with flow layout
        self.grid_container = QWidget()
        self.flow_layout = FlowLayout(self.grid_container, margin=4, spacing=8)

        # Empty state label (hidden when people added)
        self.empty_label = QLabel("No people detected yet\n\nRun face detection to see people here")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("""
            QLabel {
                color: #5f6368;
                font-size: 10pt;
                padding: 20px;
            }
        """)
        self.empty_label.hide()

        # Add to scroll
        self.scroll_area.setWidget(self.grid_container)
        main_layout.addWidget(self.scroll_area)
        main_layout.addWidget(self.empty_label)

    def add_person(self, branch_key, display_name, face_pixmap, photo_count):
        """
        Add person to grid.

        Args:
            branch_key: Unique identifier (e.g., "cluster_0")
            display_name: Display name (e.g., "John" or "Unnamed")
            face_pixmap: Face thumbnail
            photo_count: Number of photos
        """
        card = PersonCard(branch_key, display_name, face_pixmap, photo_count)
        card.clicked.connect(self._on_person_clicked)
        card.context_menu_requested.connect(self._on_context_menu_requested)
        card.drag_merge_requested.connect(self._on_drag_merge_requested)
        self.flow_layout.addWidget(card)
        self.empty_label.hide()

    def _on_person_clicked(self, branch_key):
        """Forward person click signal."""
        self.person_clicked.emit(branch_key)

    def _on_context_menu_requested(self, branch_key, action):
        """Forward context menu request."""
        self.context_menu_requested.emit(branch_key, action)

    def _on_drag_merge_requested(self, source_branch, target_branch):
        """Forward drag-drop merge request."""
        self.drag_merge_requested.emit(source_branch, target_branch)

    def clear(self):
        """Remove all person cards."""
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.empty_label.show()

    def count(self):
        """Return number of people in grid."""
        return self.flow_layout.count()

    def sizeHint(self):
        """
        Return recommended size for the grid.

        CRITICAL: CollapsibleSection uses this to determine expand height.
        Without this, section collapses to tiny ~50px area showing only 2 faces!

        Returns:
            QSize: Recommended size (width flexible, height based on content)
        """
        # Calculate based on number of cards and card size
        card_count = self.flow_layout.count()
        if card_count == 0:
            # Empty state - small height
            return QSize(200, 100)

        # Card size: 80x100px per PersonCard + spacing
        card_height = 100
        spacing = 8
        cards_per_row = 2  # Sidebar width ~240px / 80px cards = ~2 per row

        # Calculate rows needed
        rows = (card_count + cards_per_row - 1) // cards_per_row

        # Total height: rows * (card_height + spacing) + margins
        # Cap at 400px to allow scrolling for many faces
        content_height = min(rows * (card_height + spacing) + 20, 400)

        return QSize(200, content_height)
