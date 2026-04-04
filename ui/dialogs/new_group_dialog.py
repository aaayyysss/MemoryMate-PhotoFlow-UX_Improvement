# ui/dialogs/new_group_dialog.py
# Dialog for creating a new people group
# Version 1.0.0 dated 20260215

"""
NewGroupDialog - Dialog for creating people groups

Allows users to:
- Enter a group name
- Select 2+ people from existing face clusters
- Preview selected people with thumbnails
- Create the group with validation
"""

import io
import logging
from typing import Optional, List, Dict, Set

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
    QFrame,
    QGridLayout,
    QMessageBox,
    QCheckBox,
    QSizePolicy,
)
from shiboken6 import isValid

logger = logging.getLogger(__name__)


class NewGroupDialog(QDialog):
    """Dialog for creating a new people group."""

    groupCreated = Signal(dict)  # Emits group info on success

    def __init__(self, project_id: int, db, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.db = db

        self._selected_branch_keys: Set[str] = set()
        self._person_cards: Dict[str, "PersonSelectionCard"] = {}

        self.setWindowTitle("Create New Group")
        self.setMinimumSize(500, 600)
        self.setModal(True)

        self._setup_ui()
        self._load_people()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Title
        title = QLabel("Create a Group of People")
        title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "Select 2 or more people to find photos where they appear together."
        )
        desc.setStyleSheet("color: #5f6368;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Group name input
        name_layout = QVBoxLayout()
        name_layout.setSpacing(4)

        name_label = QLabel("Group Name")
        name_label.setStyleSheet("font-weight: 600;")
        name_layout.addWidget(name_label)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Family, Work Friends, etc.")
        self.name_input.setStyleSheet("""
            QLineEdit {
                padding: 10px;
                border: 1px solid #dadce0;
                border-radius: 8px;
                font-size: 11pt;
            }
            QLineEdit:focus {
                border: 2px solid #1a73e8;
            }
        """)
        name_layout.addWidget(self.name_input)

        layout.addLayout(name_layout)

        # Selection count
        self.selection_label = QLabel("Select at least 2 people (0 selected)")
        self.selection_label.setStyleSheet("color: #5f6368; padding: 8px 0;")
        layout.addWidget(self.selection_label)

        # People grid in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #f8f9fa; border-radius: 8px; }")

        self.people_container = QWidget()
        self.people_layout = QGridLayout(self.people_container)
        self.people_layout.setContentsMargins(8, 8, 8, 8)
        self.people_layout.setSpacing(8)

        scroll.setWidget(self.people_container)
        layout.addWidget(scroll, 1)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                border: 1px solid #dadce0;
                border-radius: 8px;
                font-weight: 600;
            }
            QPushButton:hover { background: #f1f3f4; }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        button_layout.addStretch()

        self.create_btn = QPushButton("Create Group")
        self.create_btn.setEnabled(False)
        self.create_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                background: #1a73e8;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: 600;
            }
            QPushButton:hover { background: #1557b0; }
            QPushButton:disabled {
                background: #dadce0;
                color: #80868b;
            }
        """)
        self.create_btn.clicked.connect(self._on_create_clicked)
        button_layout.addWidget(self.create_btn)

        layout.addLayout(button_layout)

    def _load_people(self):
        """Load people from face clusters."""
        try:
            people = self.db.get_face_clusters(self.project_id) or []

            logger.info(f"[NewGroupDialog] Loaded {len(people)} people")

            # Clear existing
            while self.people_layout.count():
                item = self.people_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            self._person_cards.clear()

            # Create cards
            cols = 3
            for idx, person in enumerate(people):
                branch_key = person.get("branch_key", "")
                display_name = person.get("display_name", f"Person {idx + 1}")
                photo_count = person.get("member_count", 0)
                rep_thumb = person.get("rep_thumb_png")
                rep_path = person.get("rep_path")

                # Load thumbnail
                pixmap = self._load_thumbnail(rep_path, rep_thumb)

                card = PersonSelectionCard(
                    branch_key=branch_key,
                    display_name=display_name,
                    photo_count=photo_count,
                    thumbnail=pixmap
                )
                card.selectionChanged.connect(self._on_selection_changed)

                row = idx // cols
                col = idx % cols
                self.people_layout.addWidget(card, row, col)
                self._person_cards[branch_key] = card

            # Add stretch to bottom
            if people:
                spacer = QWidget()
                spacer.setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Expanding)
                self.people_layout.addWidget(spacer, (len(people) // cols) + 1, 0, 1, cols)

        except Exception as e:
            logger.error(f"[NewGroupDialog] Failed to load people: {e}")

    def _load_thumbnail(self, rep_path: Optional[str], rep_thumb_png: Optional[bytes]) -> Optional[QPixmap]:
        """Load thumbnail from BLOB or file path."""
        try:
            THUMB_SIZE = 48

            # Try BLOB first
            if rep_thumb_png:
                try:
                    image_data = io.BytesIO(rep_thumb_png)
                    from PIL import Image

                    with Image.open(image_data) as img:
                        img_rgb = img.convert("RGB")
                        data = img_rgb.tobytes("raw", "RGB")
                        qimg = QImage(data, img_rgb.width, img_rgb.height,
                                     img_rgb.width * 3, QImage.Format_RGB888)
                        if not qimg.isNull():
                            pixmap = QPixmap.fromImage(qimg)
                            return pixmap.scaled(THUMB_SIZE, THUMB_SIZE,
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
                except Exception:
                    pass

            # Try file path
            if rep_path:
                import os
                if os.path.exists(rep_path):
                    try:
                        from PIL import Image

                        with Image.open(rep_path) as img:
                            img_rgb = img.convert("RGB")
                            img_rgb.thumbnail((THUMB_SIZE * 2, THUMB_SIZE * 2))
                            data = img_rgb.tobytes("raw", "RGB")
                            qimg = QImage(data, img_rgb.width, img_rgb.height,
                                         img_rgb.width * 3, QImage.Format_RGB888)
                            if not qimg.isNull():
                                pixmap = QPixmap.fromImage(qimg)
                                return pixmap.scaled(THUMB_SIZE, THUMB_SIZE,
                                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    except Exception:
                        pass

            return None

        except Exception as e:
            logger.warning(f"[NewGroupDialog] Thumbnail load failed: {e}")
            return None

    def _on_selection_changed(self, branch_key: str, is_selected: bool):
        """Handle person selection change."""
        if is_selected:
            self._selected_branch_keys.add(branch_key)
        else:
            self._selected_branch_keys.discard(branch_key)

        count = len(self._selected_branch_keys)
        self.selection_label.setText(f"Select at least 2 people ({count} selected)")

        # Enable create button if 2+ selected
        self.create_btn.setEnabled(count >= 2)

        # Update label color based on validity
        if count >= 2:
            self.selection_label.setStyleSheet("color: #188038; padding: 8px 0; font-weight: 600;")
        else:
            self.selection_label.setStyleSheet("color: #5f6368; padding: 8px 0;")

    def _on_create_clicked(self):
        """Handle create button click."""
        name = self.name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a name for the group.")
            self.name_input.setFocus()
            return

        if len(self._selected_branch_keys) < 2:
            QMessageBox.warning(self, "Not Enough People",
                              "Please select at least 2 people for the group.")
            return

        try:
            from services.group_service import GroupService

            # Use GroupService with canonical schema
            group_id = GroupService.create_group(
                db=self.db,
                project_id=self.project_id,
                name=name,
                branch_keys=list(self._selected_branch_keys)
            )

            # Build result dict for signal emission
            result = {
                'id': group_id,
                'name': name,
                'member_count': len(self._selected_branch_keys),
                'members': list(self._selected_branch_keys)
            }

            logger.info(f"[NewGroupDialog] Created group: {result}")

            self.groupCreated.emit(result)
            self.accept()

        except Exception as e:
            logger.error(f"[NewGroupDialog] Failed to create group: {e}")
            QMessageBox.critical(self, "Error",
                               f"Failed to create group:\n{str(e)}")


class PersonSelectionCard(QWidget):
    """
    Selectable person card with circular avatar, blue ring + checkmark badge.

    Matches Google Photos / Apple Photos selection pattern used in
    CreateGroupDialog for visual consistency.
    """

    selectionChanged = Signal(str, bool)  # (branch_key, is_selected)

    AVATAR_SIZE = 56

    def __init__(
        self,
        branch_key: str,
        display_name: str,
        photo_count: int,
        thumbnail: Optional[QPixmap] = None,
        parent=None
    ):
        super().__init__(parent)
        self.branch_key = branch_key
        self._is_selected = False
        self._base_thumbnail = thumbnail

        self.setFixedSize(110, 120)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignCenter)

        # Avatar (rendered with selection ring + checkmark)
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(self.AVATAR_SIZE, self.AVATAR_SIZE)
        self._avatar_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._avatar_label, alignment=Qt.AlignCenter)

        # Name
        name_label = QLabel(display_name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-weight: 600; font-size: 10px; color: #202124;")
        name_label.setWordWrap(True)
        name_label.setMaximumHeight(28)
        layout.addWidget(name_label)

        # Photo count
        count_label = QLabel(f"{photo_count} photos")
        count_label.setAlignment(Qt.AlignCenter)
        count_label.setStyleSheet("color: #5f6368; font-size: 9px;")
        layout.addWidget(count_label)

        self._render_avatar()
        self._update_card_style()

    def _render_avatar(self):
        """Render circular avatar with optional selection ring and checkmark badge."""
        from PySide6.QtGui import QPainter, QPainterPath, QColor, QPen, QFont
        from PySide6.QtCore import QRect

        size = self.AVATAR_SIZE
        result = QPixmap(size, size)
        result.fill(Qt.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._base_thumbnail and not self._base_thumbnail.isNull():
            scaled = self._base_thumbnail.scaled(
                size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            clip = QPainterPath()
            clip.addEllipse(2, 2, size - 4, size - 4)
            painter.setClipPath(clip)
            x_off = (scaled.width() - size) // 2
            y_off = (scaled.height() - size) // 2
            painter.drawPixmap(-x_off + 2, -y_off + 2, scaled)
            painter.setClipping(False)
        else:
            painter.setBrush(QColor("#e8eaed"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(2, 2, size - 4, size - 4)
            painter.setPen(QColor("#9aa0a6"))
            font = QFont()
            font.setPixelSize(24)
            painter.setFont(font)
            painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "\U0001F464")

        if self._is_selected:
            # Blue selection ring
            pen = QPen(QColor("#1a73e8"), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(1, 1, size - 2, size - 2)

            # Checkmark badge (bottom-right)
            badge_size = 18
            badge_x = size - badge_size - 1
            badge_y = size - badge_size - 1

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(badge_x - 1, badge_y - 1, badge_size + 2, badge_size + 2)

            painter.setBrush(QColor("#1a73e8"))
            painter.drawEllipse(badge_x, badge_y, badge_size, badge_size)

            painter.setPen(QPen(QColor("#ffffff"), 2.0))
            cx = badge_x + badge_size // 2
            cy = badge_y + badge_size // 2
            painter.drawLine(cx - 4, cy, cx - 1, cy + 3)
            painter.drawLine(cx - 1, cy + 3, cx + 4, cy - 3)

        painter.end()
        self._avatar_label.setPixmap(result)

    def _update_card_style(self):
        """Update card background based on selection state."""
        if self._is_selected:
            self.setStyleSheet("""
                PersonSelectionCard {
                    background: rgba(26, 115, 232, 0.08);
                    border: 2px solid #1a73e8;
                    border-radius: 10px;
                }
            """)
        else:
            self.setStyleSheet("""
                PersonSelectionCard {
                    background: #fff;
                    border: 1px solid transparent;
                    border-radius: 10px;
                }
                PersonSelectionCard:hover {
                    background: rgba(0, 0, 0, 0.04);
                    border: 1px solid #dadce0;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_selected = not self._is_selected
            self._render_avatar()
            self._update_card_style()
            self.selectionChanged.emit(self.branch_key, self._is_selected)
        super().mousePressEvent(event)
