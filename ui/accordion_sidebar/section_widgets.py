# ui/accordion_sidebar/section_widgets.py
# Shared widgets for accordion sections (SectionHeader and AccordionSection)

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QSizePolicy,
)
from PySide6.QtCore import Signal, Qt, QSize
from shiboken6 import isValid


class SectionHeader(QFrame):
    """
    Clickable header for accordion section.
    Shows: Icon + Title + Count (optional) + Chevron

    States:
    - Active (expanded): Bold text, highlighted background, chevron down (▼)
    - Inactive (collapsed): Normal text, default background, chevron right (▶)
    """

    clicked = Signal()  # Emitted when header is clicked

    def __init__(self, section_id: str, title: str, icon: str = "", parent=None):
        super().__init__(parent)
        self.section_id = section_id
        self.title = title
        self.icon = icon
        self.is_active = False
        self.item_count = 0

        # Make the frame clickable
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)

        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        # Icon + Title
        self.icon_label = QLabel(icon)
        self.icon_label.setFixedWidth(24)
        font = self.icon_label.font()
        font.setPointSize(14)
        self.icon_label.setFont(font)

        self.title_label = QLabel(title)
        self.title_font = self.title_label.font()

        # Count badge (optional)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #666; font-size: 11px;")
        self.count_label.setVisible(False)

        # Chevron (indicates expand/collapse state)
        self.chevron_label = QLabel("▶")  # Right arrow for collapsed
        self.chevron_label.setFixedWidth(20)
        chevron_font = self.chevron_label.font()
        chevron_font.setPointSize(10)
        self.chevron_label.setFont(chevron_font)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)

        # Optional extra controls (e.g., people actions)
        self.extra_container = QWidget()
        self.extra_container.setVisible(False)
        self.extra_layout = QHBoxLayout(self.extra_container)
        self.extra_layout.setContentsMargins(0, 0, 0, 0)
        self.extra_layout.setSpacing(4)

        layout.addStretch()
        layout.addWidget(self.extra_container)
        layout.addWidget(self.count_label)
        layout.addWidget(self.chevron_label)

        # Initial styling
        self.set_active(False)

    def set_active(self, active: bool):
        """Set header to active (expanded) or inactive (collapsed) state."""
        self.is_active = active

        if active:
            # Active state: Bold, highlighted, chevron down
            self.title_font.setBold(True)
            self.title_label.setFont(self.title_font)
            self.chevron_label.setText("▼")  # Down arrow
            self.setStyleSheet("""
                SectionHeader {
                    background-color: #e8f0fe;
                    border: none;
                    border-radius: 6px;
                }
                SectionHeader:hover {
                    background-color: #d2e3fc;
                }
            """)
        else:
            # Inactive state: Normal, default background, chevron right
            self.title_font.setBold(False)
            self.title_label.setFont(self.title_font)
            self.chevron_label.setText("▶")  # Right arrow
            self.setStyleSheet("""
                SectionHeader {
                    background-color: #f8f9fa;
                    border: 1px solid #e8eaed;
                    border-radius: 6px;
                }
                SectionHeader:hover {
                    background-color: #f1f3f4;
                }
            """)

    def set_count(self, count: int):
        """Update the count badge."""
        self.item_count = count
        if count > 0:
            self.count_label.setText(f"({count})")
            self.count_label.setVisible(True)
        else:
            self.count_label.setVisible(False)

    def set_extra_widget(self, widget: QWidget):
        """Mount a custom widget inside the header (to the right of the title)."""
        # Remove any existing extra widget
        while self.extra_layout.count():
            item = self.extra_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if widget:
            widget.setParent(self)
            self.extra_layout.addWidget(widget)
            self.extra_container.setVisible(True)
        else:
            self.extra_container.setVisible(False)

    def set_extra_widget(self, widget: QWidget):
        """Mount a custom widget inside the header (to the right of the title)."""
        # Remove any existing extra widget
        while self.extra_layout.count():
            item = self.extra_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if widget:
            widget.setParent(self)
            self.extra_layout.addWidget(widget)
            self.extra_container.setVisible(True)
        else:
            self.extra_container.setVisible(False)

    def set_extra_widget(self, widget: QWidget):
        """Mount a custom widget inside the header (to the right of the title)."""
        # Remove any existing extra widget
        while self.extra_layout.count():
            item = self.extra_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if widget:
            widget.setParent(self)
            self.extra_layout.addWidget(widget)
            self.extra_container.setVisible(True)
        else:
            self.extra_container.setVisible(False)

    def mousePressEvent(self, event):
        """Handle mouse click on header."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class AccordionSection(QWidget):
    """
    Individual accordion section.
    Contains:
    - Header (always visible)
    - Content widget (visible only when expanded)

    Can be expanded (shows content) or collapsed (header only).
    """

    # Signals
    expandRequested = Signal(str)  # section_id - Request to expand this section

    def __init__(self, section_id: str, title: str, icon: str = "", parent=None):
        super().__init__(parent)
        self.section_id = section_id
        self.title = title
        self.is_expanded = False

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header (always visible)
        self.header = SectionHeader(section_id, title, icon)
        self.header.clicked.connect(self._on_header_clicked)
        layout.addWidget(self.header, stretch=0)

        # Content area (visible only when expanded)
        self.content_container = QWidget()
        self.content_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(0)

        # Scroll area for content (ONE scrollbar here)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.content_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setVisible(False)  # Hidden by default
        self.scroll_area.setMinimumHeight(300)  # CRITICAL: Ensure minimum content height

        layout.addWidget(self.scroll_area, stretch=100)  # Takes all available space when expanded

        # Set size policy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_header_extra(self, widget: QWidget):
        """Expose header slot for section-specific controls (e.g., people actions)."""
        self.header.set_extra_widget(widget)

    def set_header_extra(self, widget: QWidget):
        """Expose header slot for section-specific controls (e.g., people actions)."""
        self.header.set_extra_widget(widget)

    def set_header_extra(self, widget: QWidget):
        """Expose header slot for section-specific controls (e.g., people actions)."""
        self.header.set_extra_widget(widget)

    def _on_header_clicked(self):
        """Handle header click - request expansion."""
        self.expandRequested.emit(self.section_id)

    def set_expanded(self, expanded: bool):
        """Expand or collapse this section."""
        self.is_expanded = expanded
        self.header.set_active(expanded)
        self.scroll_area.setVisible(expanded)

        if expanded:
            # Expanded: Allow vertical expansion and remove height constraints
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMaximumHeight(16777215)  # Remove any maximum height constraint
            self.setMinimumHeight(400)  # Ensure expanded section has substantial height
        else:
            # Collapsed: Fixed height (header only)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMaximumHeight(50)  # Compact header
            self.setMinimumHeight(50)

    def set_content_widget(self, widget: QWidget):
        """Set the content widget for this section."""
        if not self._ensure_content_layout():
            return

        # CRITICAL FIX: If the widget is already in the layout, don't delete it
        # This prevents RuntimeError when reusing PeopleListView across reloads
        try:
            existing_widget = self.content_layout.itemAt(0).widget() if self.content_layout.count() > 0 else None
        except RuntimeError:
            # Layout was deleted mid-flight; rebuild and bail to avoid crashes.
            self._ensure_content_layout(force_rebuild=True)
            return

        if existing_widget is widget:
            # Widget is already set - no need to remove/re-add
            return

        # Clear existing content WITH SIGNAL CLEANUP
        while self.content_layout.count():
            try:
                item = self.content_layout.takeAt(0)
            except RuntimeError:
                # Layout vanished while iterating; rebuild safely.
                self._ensure_content_layout(force_rebuild=True)
                break

            if item and item.widget():
                w = item.widget()

                # CRITICAL: Cleanup before deletion to prevent signal/slot leaks
                if hasattr(w, '_cleanup') and callable(w._cleanup):
                    try:
                        w._cleanup()
                    except Exception as e:
                        print(f"[AccordionSection] Cleanup failed for {type(w).__name__}: {e}")

                # Disconnect all signals to prevent crashes
                try:
                    w.blockSignals(True)
                    w.setParent(None)
                except RuntimeError:
                    pass  # Widget already deleted by Qt

                w.deleteLater()

        # Add new content
        if widget:
            # CRITICAL: Ensure widget has proper size policy for expansion
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            widget.setVisible(True)  # Ensure widget is visible
            self.content_layout.addWidget(widget, stretch=1)

    def _ensure_content_layout(self, force_rebuild: bool = False) -> bool:
        """
        Ensure the content container/layout are still valid. A rapid flurry of
        async reloads can result in Qt deleting the layout while signals are
        still being processed; this guard rebuilds the structure as needed.
        Returns True if the layout is usable.
        """

        if not isValid(self):
            return False

        if not isValid(self.scroll_area):
            return False

        rebuild_container = force_rebuild or not isValid(self.content_container)
        rebuild_layout = force_rebuild or not isValid(self.content_layout)

        if rebuild_container:
            self.content_container = QWidget()
            self.content_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            rebuild_layout = True

        if rebuild_layout:
            self.content_layout = QVBoxLayout(self.content_container)
            self.content_layout.setContentsMargins(8, 8, 8, 8)
            self.content_layout.setSpacing(0)

        if self.scroll_area.widget() is not self.content_container and isValid(self.scroll_area):
            self.scroll_area.setWidget(self.content_container)

        return isValid(self.content_layout)

    def _ensure_content_layout(self, force_rebuild: bool = False) -> bool:
        """
        Ensure the content container/layout are still valid. A rapid flurry of
        async reloads can result in Qt deleting the layout while signals are
        still being processed; this guard rebuilds the structure as needed.
        Returns True if the layout is usable.
        """

        if not isValid(self):
            return False

        if not isValid(self.scroll_area):
            return False

        rebuild_container = force_rebuild or not isValid(self.content_container)
        rebuild_layout = force_rebuild or not isValid(self.content_layout)

        if rebuild_container:
            self.content_container = QWidget()
            self.content_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            rebuild_layout = True

        if rebuild_layout:
            self.content_layout = QVBoxLayout(self.content_container)
            self.content_layout.setContentsMargins(8, 8, 8, 8)
            self.content_layout.setSpacing(0)

        if self.scroll_area.widget() is not self.content_container and isValid(self.scroll_area):
            self.scroll_area.setWidget(self.content_container)

        return isValid(self.content_layout)

    def _ensure_content_layout(self, force_rebuild: bool = False) -> bool:
        """
        Ensure the content container/layout are still valid. A rapid flurry of
        async reloads can result in Qt deleting the layout while signals are
        still being processed; this guard rebuilds the structure as needed.
        Returns True if the layout is usable.
        """

        if not isValid(self):
            return False

        if not isValid(self.scroll_area):
            return False

        rebuild_container = force_rebuild or not isValid(self.content_container)
        rebuild_layout = force_rebuild or not isValid(self.content_layout)

        if rebuild_container:
            self.content_container = QWidget()
            self.content_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            rebuild_layout = True

        if rebuild_layout:
            self.content_layout = QVBoxLayout(self.content_container)
            self.content_layout.setContentsMargins(8, 8, 8, 8)
            self.content_layout.setSpacing(0)

        if self.scroll_area.widget() is not self.content_container and isValid(self.scroll_area):
            self.scroll_area.setWidget(self.content_container)

        return isValid(self.content_layout)

    def set_count(self, count: int):
        """Update the count badge in header."""
        self.header.set_count(count)
