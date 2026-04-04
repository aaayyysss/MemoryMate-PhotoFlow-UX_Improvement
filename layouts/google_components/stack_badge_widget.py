# layouts/google_components/stack_badge_widget.py
# Version 01.00.00.00 dated 20260115
# Badge overlay widget for stacked thumbnails in Google Layout

"""
StackBadgeWidget - Overlay badge showing stack count

This widget renders as an overlay on thumbnail images to indicate
that the photo is part of a stack (duplicate or similar group).

Visual design:
- Small circular badge in bottom-right corner
- Semi-transparent background
- White text showing count (e.g., "5")
- Click to expand stack
"""

from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from typing import Optional
from logging_config import get_logger

logger = get_logger(__name__)


class StackBadgeWidget(QWidget):
    """
    Overlay badge widget showing stack member count.

    This widget is designed to be overlaid on top of thumbnail images
    to indicate that the photo belongs to a stack.

    Features:
    - Circular badge with count
    - Semi-transparent background
    - Positioned in bottom-right corner
    - Click to emit stack_clicked signal

    Signals:
    - stack_clicked: Emitted when badge is clicked
    """

    # Signals
    stack_clicked = Signal(int)  # stack_id

    def __init__(self, count: int, stack_id: int, parent: Optional[QWidget] = None):
        """
        Initialize StackBadgeWidget.

        Args:
            count: Number of items in stack
            stack_id: Stack ID for click handling
            parent: Parent widget (typically thumbnail widget)
        """
        super().__init__(parent)
        self.count = count
        self.stack_id = stack_id

        # Badge configuration
        self.badge_radius = 16  # Radius of circular badge
        self.badge_color = QColor(0, 0, 0, 180)  # Semi-transparent black
        self.text_color = QColor(255, 255, 255)  # White

        # Set fixed size for badge
        badge_size = self.badge_radius * 2
        self.setFixedSize(badge_size, badge_size)

        # Enable mouse tracking for click
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        """
        Paint the badge overlay.

        Draws:
        1. Semi-transparent circular background
        2. White count text centered
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw circular background
        painter.setBrush(QBrush(self.badge_color))
        painter.setPen(QPen(Qt.NoPen))
        painter.drawEllipse(0, 0, self.badge_radius * 2, self.badge_radius * 2)

        # Draw count text
        painter.setPen(QPen(self.text_color))
        font = QFont()
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)

        # Center text in badge
        text = str(self.count) if self.count < 100 else "99+"
        rect = QRect(0, 0, self.badge_radius * 2, self.badge_radius * 2)
        painter.drawText(rect, Qt.AlignCenter, text)

    def mousePressEvent(self, event):
        """Handle mouse click on badge."""
        if event.button() == Qt.LeftButton:
            self.stack_clicked.emit(self.stack_id)
            event.accept()
        else:
            super().mousePressEvent(event)

    def position_bottom_right(self, parent_width: int, parent_height: int, margin: int = 4):
        """
        Position badge in bottom-right corner of parent widget.

        Args:
            parent_width: Parent widget width
            parent_height: Parent widget height
            margin: Margin from edges (default: 4px)
        """
        x = parent_width - self.width() - margin
        y = parent_height - self.height() - margin
        self.move(x, y)


def create_stack_badge(
    count: int,
    stack_id: int,
    parent: QWidget
) -> StackBadgeWidget:
    """
    Factory function to create and position a stack badge.

    Args:
        count: Number of items in stack
        stack_id: Stack ID
        parent: Parent widget (thumbnail)

    Returns:
        StackBadgeWidget positioned in bottom-right corner
    """
    badge = StackBadgeWidget(count, stack_id, parent)

    # Position in bottom-right corner
    parent_rect = parent.rect()
    badge.position_bottom_right(parent_rect.width(), parent_rect.height())

    return badge
