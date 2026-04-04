# layouts/google_components/duplicate_badge_widget.py
# Version 01.00.00.00 dated 20260126
# Badge overlay widget for duplicate photos in Google Layout

"""
DuplicateBadgeWidget - Overlay badge showing duplicate count

This widget renders as an overlay on thumbnail images to indicate
that the photo has duplicates (exact copies based on content hash).

Visual design:
- Rounded rectangle badge in bottom-left corner
- Orange/amber background (distinct from similar stacks)
- White text showing "D" and count (e.g., "D 3")
- Click to open duplicate management dialog

Based on Google Photos / Apple Photos patterns where duplicates
are clearly indicated with a badge.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QFontMetrics
from typing import Optional
from logging_config import get_logger

logger = get_logger(__name__)


class DuplicateBadgeWidget(QWidget):
    """
    Overlay badge widget showing duplicate count.

    This widget is designed to be overlaid on top of thumbnail images
    to indicate that the photo has duplicates (exact copies).

    Features:
    - Rounded rectangle badge with "D" prefix and count
    - Orange/amber background (distinct from similar photo stacks)
    - Positioned in bottom-left corner (stacks use bottom-right)
    - Click to emit duplicate_clicked signal

    Signals:
    - duplicate_clicked: Emitted when badge is clicked (passes asset_id)
    """

    # Signals
    duplicate_clicked = Signal(int)  # asset_id

    def __init__(self, count: int, asset_id: int, parent: Optional[QWidget] = None):
        """
        Initialize DuplicateBadgeWidget.

        Args:
            count: Number of duplicate instances (including original)
            asset_id: Asset ID for click handling
            parent: Parent widget (typically thumbnail widget)
        """
        super().__init__(parent)
        self.count = count
        self.asset_id = asset_id

        # Badge configuration
        self.badge_color = QColor(255, 152, 0, 220)  # Orange/amber semi-transparent
        self.text_color = QColor(255, 255, 255)  # White
        self.border_color = QColor(230, 126, 34, 255)  # Darker orange border

        # Calculate badge size based on text
        self._update_size()

        # Enable mouse tracking for click
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        # Tooltip
        self.setToolTip(f"Duplicate: {count} copies found. Click to manage.")

    def _update_size(self):
        """Calculate and set badge size based on count text."""
        font = QFont()
        font.setPixelSize(10)
        font.setBold(True)
        metrics = QFontMetrics(font)

        # Text format: "D n" where n is count
        text = self._get_display_text()
        text_width = metrics.horizontalAdvance(text)

        # Badge dimensions
        padding_h = 6  # Horizontal padding
        padding_v = 3  # Vertical padding
        self.badge_width = text_width + (padding_h * 2)
        self.badge_height = metrics.height() + (padding_v * 2)

        # Minimum width
        self.badge_width = max(self.badge_width, 28)

        self.setFixedSize(int(self.badge_width), int(self.badge_height))

    def _get_display_text(self) -> str:
        """Get display text for badge."""
        if self.count < 10:
            return f"D {self.count}"
        elif self.count < 100:
            return f"D{self.count}"
        else:
            return "D99+"

    def paintEvent(self, event):
        """
        Paint the badge overlay.

        Draws:
        1. Rounded rectangle background (orange)
        2. Border
        3. "D n" text centered
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw rounded rectangle background
        painter.setBrush(QBrush(self.badge_color))
        painter.setPen(QPen(self.border_color, 1))

        radius = 4  # Corner radius
        painter.drawRoundedRect(0, 0, self.width(), self.height(), radius, radius)

        # Draw text
        painter.setPen(QPen(self.text_color))
        font = QFont()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)

        text = self._get_display_text()
        rect = QRect(0, 0, self.width(), self.height())
        painter.drawText(rect, Qt.AlignCenter, text)

    def mousePressEvent(self, event):
        """Handle mouse click on badge."""
        if event.button() == Qt.LeftButton:
            logger.info(f"[DuplicateBadge] Clicked, emitting asset_id={self.asset_id}")
            self.duplicate_clicked.emit(self.asset_id)
            event.accept()
        else:
            super().mousePressEvent(event)

    def position_bottom_left(self, parent_width: int, parent_height: int, margin: int = 4):
        """
        Position badge in bottom-left corner of parent widget.

        Args:
            parent_width: Parent widget width (not used, but kept for consistency)
            parent_height: Parent widget height
            margin: Margin from edges (default: 4px)
        """
        x = margin
        y = parent_height - self.height() - margin
        self.move(x, y)


def create_duplicate_badge(
    count: int,
    asset_id: int,
    parent: QWidget
) -> DuplicateBadgeWidget:
    """
    Factory function to create and position a duplicate badge.

    Args:
        count: Number of duplicate instances
        asset_id: Asset ID
        parent: Parent widget (thumbnail)

    Returns:
        DuplicateBadgeWidget positioned in bottom-left corner
    """
    badge = DuplicateBadgeWidget(count, asset_id, parent)

    # Position in bottom-left corner
    parent_rect = parent.rect()
    badge.position_bottom_left(parent_rect.width(), parent_rect.height())

    return badge
