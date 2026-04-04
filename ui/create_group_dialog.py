# ui/create_group_dialog.py
# Dialog for creating/editing person groups
# Version: 1.1.0

"""
CreateGroupDialog - Create/Edit person groups

Multi-select dialog for choosing people to include in a group:
- Grid of people with circular thumbnails and file-path fallback
- Visual selection with blue ring + checkmark overlay (Google Photos style)
- Group name input with auto-suggestion
- Edit mode: loads existing members pre-selected, saves via update_group
- Pinned option
"""

import io
import logging
import os
from typing import Optional, List, Dict, Set

from PySide6.QtCore import Signal, Qt, QSize, QRect
from PySide6.QtGui import QPixmap, QImage, QPainter, QPainterPath, QColor, QPen, QFont, QBrush
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
    QGridLayout,
    QCheckBox,
    QFrame,
    QSizePolicy,
)

from translation_manager import tr

logger = logging.getLogger(__name__)

AVATAR_SIZE = 64
CARD_WIDTH = 100
CARD_HEIGHT = 136


class PersonSelectCard(QWidget):
    """
    Selectable person card with circular avatar and checkmark overlay.

    Selection style follows Google Photos / Apple Photos pattern:
    - Unselected: subtle border, neutral background
    - Selected: blue ring around avatar, blue checkmark badge, tinted background
    """

    toggled = Signal(str, bool)  # (branch_key, is_selected)

    def __init__(
        self,
        branch_key: str,
        display_name: str,
        thumbnail: Optional[QPixmap],
        selected: bool = False,
        member_count: int = 0,
        parent=None
    ):
        super().__init__(parent)
        self.branch_key = branch_key
        self.display_name = display_name
        self._selected = selected
        self._base_thumbnail = thumbnail  # keep original for re-rendering

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignCenter)

        # Avatar container (holds the rendered circular pixmap + overlay)
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        self._avatar_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._avatar_label, alignment=Qt.AlignCenter)

        # Name
        name_label = QLabel(display_name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setMaximumHeight(32)
        name_label.setStyleSheet("font-size: 10px; color: #202124;")
        layout.addWidget(name_label)

        # Face count badge (e.g. "12 photos")
        if member_count > 0:
            count_text = f"{member_count} photo{'s' if member_count != 1 else ''}"
            count_label = QLabel(count_text)
            count_label.setAlignment(Qt.AlignCenter)
            count_label.setStyleSheet("font-size: 9px; color: #5f6368;")
            layout.addWidget(count_label)

        self._render_avatar()
        self._update_card_style()

    def _render_avatar(self):
        """Render circular avatar with selection ring and checkmark."""
        size = AVATAR_SIZE
        result = QPixmap(size, size)
        result.fill(Qt.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._base_thumbnail and not self._base_thumbnail.isNull():
            # Clip to circle and draw thumbnail
            scaled = self._base_thumbnail.scaled(
                size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            clip = QPainterPath()
            clip.addEllipse(2, 2, size - 4, size - 4)
            painter.setClipPath(clip)
            # Center the scaled image
            x_off = (scaled.width() - size) // 2
            y_off = (scaled.height() - size) // 2
            painter.drawPixmap(-x_off + 2, -y_off + 2, scaled)
            painter.setClipping(False)
        else:
            # Placeholder circle
            painter.setBrush(QColor("#e8eaed"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(2, 2, size - 4, size - 4)
            painter.setPen(QColor("#9aa0a6"))
            font = QFont()
            font.setPixelSize(28)
            painter.setFont(font)
            painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "\U0001F464")

        if self._selected:
            # Blue selection ring
            pen = QPen(QColor("#1a73e8"), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(1, 1, size - 2, size - 2)

            # Checkmark badge (bottom-right)
            badge_size = 20
            badge_x = size - badge_size - 1
            badge_y = size - badge_size - 1

            # White circle behind badge
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(badge_x - 1, badge_y - 1, badge_size + 2, badge_size + 2)

            # Blue circle
            painter.setBrush(QColor("#1a73e8"))
            painter.drawEllipse(badge_x, badge_y, badge_size, badge_size)

            # White checkmark
            painter.setPen(QPen(QColor("#ffffff"), 2.0))
            cx = badge_x + badge_size // 2
            cy = badge_y + badge_size // 2
            painter.drawLine(cx - 4, cy, cx - 1, cy + 3)
            painter.drawLine(cx - 1, cy + 3, cx + 5, cy - 3)

        painter.end()
        self._avatar_label.setPixmap(result)

    def _update_card_style(self):
        """Update card background based on selection state."""
        if self._selected:
            self.setStyleSheet("""
                PersonSelectCard {
                    background: rgba(26, 115, 232, 0.08);
                    border: 2px solid #1a73e8;
                    border-radius: 10px;
                }
            """)
        else:
            self.setStyleSheet("""
                PersonSelectCard {
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 10px;
                }
                PersonSelectCard:hover {
                    background: rgba(0, 0, 0, 0.04);
                    border: 1px solid #dadce0;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._selected = not self._selected
            self._render_avatar()
            self._update_card_style()
            self.toggled.emit(self.branch_key, self._selected)
        super().mousePressEvent(event)

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool):
        self._selected = selected
        self._render_avatar()
        self._update_card_style()


class CreateGroupDialog(QDialog):
    """
    Unified dialog for creating or editing a person group.

    Handles both create and edit DB operations internally:
    - Create mode: calls GroupService.create_group(), exposes created_group_id
    - Edit mode: calls GroupService.update_group()

    Cover photo selection (Google/Apple pattern):
    - Shows selected members' face thumbnails as selectable cover options
    - If user picks one, stores the rep_path as cover_asset_path via set_group_cover
    - If none selected, auto-derives from first match photo (default fallback)
    """

    groupCreated = Signal(dict)   # Emitted on successful creation with group info
    groupUpdated = Signal(int)    # Emitted on successful edit with group_id

    def __init__(
        self,
        project_id: int,
        edit_group_id: Optional[int] = None,
        parent=None
    ):
        super().__init__(parent)
        self.project_id = project_id
        self.edit_group_id = edit_group_id
        self.is_edit_mode = edit_group_id is not None

        # Results (read by caller after exec)
        self.group_name: str = ""
        self.selected_people: List[str] = []
        self.is_pinned: bool = False
        self.created_group_id: Optional[int] = None  # Set after creation

        # Internal state
        self._people_cards: Dict[str, PersonSelectCard] = {}
        self._selected_branch_keys: Set[str] = set()
        self._cover_branch_key: Optional[str] = None  # Selected cover member
        self._member_rep_paths: Dict[str, str] = {}    # branch_key -> rep_path

        self._setup_ui()
        self._load_people()

        if self.is_edit_mode:
            self._load_existing_group()

    def _setup_ui(self):
        """Setup dialog UI."""
        title = "Edit Group" if self.is_edit_mode else "Create Group"
        self.setWindowTitle(title)
        self.setMinimumSize(520, 600)
        self.resize(620, 720)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # Header
        header_label = QLabel(
            "Edit group members" if self.is_edit_mode
            else "Select 2 or more people to create a group"
        )
        header_label.setStyleSheet("font-size: 13pt; color: #202124; font-weight: 500;")
        main_layout.addWidget(header_label)

        # Group name input
        name_container = QWidget()
        name_layout = QHBoxLayout(name_container)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)

        name_label = QLabel("Group name:")
        name_label.setStyleSheet("font-size: 11pt;")
        name_layout.addWidget(name_label)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g., Family, Trip Buddies")
        self._name_input.setStyleSheet("""
            QLineEdit {
                padding: 8px 12px;
                border: 1px solid #dadce0;
                border-radius: 6px;
                font-size: 11pt;
            }
            QLineEdit:focus {
                border: 1px solid #1a73e8;
            }
        """)
        name_layout.addWidget(self._name_input, 1)

        main_layout.addWidget(name_container)

        # Selection count and hint
        self._selection_label = QLabel("Select at least 2 people")
        self._selection_label.setStyleSheet("color: #5f6368; font-size: 10pt;")
        main_layout.addWidget(self._selection_label)

        # People grid in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #fafafa; border-radius: 8px; }")

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        self._grid_layout.setSpacing(8)

        scroll.setWidget(self._grid_container)
        main_layout.addWidget(scroll, 1)

        # Cover photo selection row (hidden until 2+ members selected)
        self._cover_section = QWidget()
        cover_layout = QVBoxLayout(self._cover_section)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        cover_layout.setSpacing(6)

        cover_header = QLabel("Group thumbnail (optional)")
        cover_header.setStyleSheet("font-size: 10pt; color: #202124; font-weight: 500;")
        cover_layout.addWidget(cover_header)

        cover_hint = QLabel("Pick a face to use as the group cover, or leave blank for auto.")
        cover_hint.setStyleSheet("font-size: 9pt; color: #5f6368;")
        cover_hint.setWordWrap(True)
        cover_layout.addWidget(cover_hint)

        self._cover_scroll = QScrollArea()
        self._cover_scroll.setWidgetResizable(True)
        self._cover_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._cover_scroll.setFixedHeight(80)
        self._cover_scroll.setFrameShape(QFrame.NoFrame)

        self._cover_container = QWidget()
        self._cover_layout = QHBoxLayout(self._cover_container)
        self._cover_layout.setContentsMargins(4, 4, 4, 4)
        self._cover_layout.setSpacing(8)
        self._cover_layout.addStretch()
        self._cover_scroll.setWidget(self._cover_container)
        cover_layout.addWidget(self._cover_scroll)

        self._cover_section.setVisible(False)
        main_layout.addWidget(self._cover_section)

        # Options row
        options_container = QWidget()
        options_layout = QHBoxLayout(options_container)
        options_layout.setContentsMargins(0, 0, 0, 0)

        self._pinned_checkbox = QCheckBox("Pin this group")
        self._pinned_checkbox.setStyleSheet("font-size: 10pt;")
        options_layout.addWidget(self._pinned_checkbox)
        options_layout.addStretch()

        main_layout.addWidget(options_container)

        # Buttons row
        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(12)

        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                border: 1px solid #dadce0;
                border-radius: 6px;
                background: white;
                font-size: 11pt;
            }
            QPushButton:hover { background: #f1f3f4; }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        btn_text = "Save" if self.is_edit_mode else "Create Group"
        self._create_btn = QPushButton(btn_text)
        self._create_btn.setEnabled(False)
        self._create_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                border: none;
                border-radius: 6px;
                background: #1a73e8;
                color: white;
                font-size: 11pt;
                font-weight: 600;
            }
            QPushButton:hover { background: #1557b0; }
            QPushButton:disabled { background: #dadce0; color: #9aa0a6; }
        """)
        self._create_btn.clicked.connect(self._on_create)
        button_layout.addWidget(self._create_btn)

        main_layout.addWidget(button_container)

    def _load_people(self):
        """Load people from database with thumbnail fallback to file path."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            people = db.get_face_clusters(self.project_id) or []
            db.close()

            logger.info(f"[CreateGroupDialog] Loaded {len(people)} people")

            columns = 5
            for idx, person in enumerate(people):
                branch_key = person["branch_key"]
                display_name = person["display_name"]
                member_count = person.get("member_count", 0) or 0
                thumb_blob = person.get("rep_thumb_png")
                rep_path = person.get("rep_path")

                # Try BLOB first, then file path
                thumbnail = None
                if thumb_blob:
                    thumbnail = self._load_thumbnail_blob(thumb_blob)
                if thumbnail is None and rep_path:
                    thumbnail = self._load_thumbnail_file(rep_path)

                # Store rep_path for cover selection
                if rep_path:
                    self._member_rep_paths[branch_key] = rep_path

                card = PersonSelectCard(
                    branch_key=branch_key,
                    display_name=display_name,
                    thumbnail=thumbnail,
                    member_count=member_count,
                )
                card.toggled.connect(self._on_person_toggled)

                row = idx // columns
                col = idx % columns
                self._grid_layout.addWidget(card, row, col)
                self._people_cards[branch_key] = card

        except Exception as e:
            logger.error(f"[CreateGroupDialog] Failed to load people: {e}", exc_info=True)

    def _load_thumbnail_blob(self, thumb_blob: bytes) -> Optional[QPixmap]:
        """Load thumbnail from in-DB PNG blob."""
        try:
            from PIL import Image

            image_data = io.BytesIO(thumb_blob)
            with Image.open(image_data) as img:
                img_rgb = img.convert("RGB")
                data = img_rgb.tobytes("raw", "RGB")
                qimg = QImage(data, img_rgb.width, img_rgb.height, img_rgb.width * 3, QImage.Format_RGB888)
                if qimg.isNull():
                    return None
                return QPixmap.fromImage(qimg)
        except Exception as e:
            logger.warning(f"[CreateGroupDialog] Failed to load thumbnail blob: {e}")
            return None

    def _load_thumbnail_file(self, rep_path: str) -> Optional[QPixmap]:
        """Load thumbnail from file path (fallback when blob is unavailable)."""
        try:
            if not os.path.exists(rep_path):
                return None

            from PIL import Image

            with Image.open(rep_path) as img:
                img_rgb = img.convert("RGB")
                # Resize large images to avoid memory issues
                if img_rgb.width > 256 or img_rgb.height > 256:
                    img_rgb.thumbnail((256, 256), Image.Resampling.LANCZOS)

                data = img_rgb.tobytes("raw", "RGB")
                stride = img_rgb.width * 3
                qimg = QImage(data, img_rgb.width, img_rgb.height, stride, QImage.Format_RGB888)
                if qimg.isNull():
                    return None
                return QPixmap.fromImage(qimg)
        except Exception as e:
            logger.warning(f"[CreateGroupDialog] Failed to load thumbnail file {rep_path}: {e}")
            return None

    def _load_existing_group(self):
        """Load existing group data for edit mode."""
        try:
            from services.group_service import GroupService
            from reference_db import ReferenceDB
            db = ReferenceDB()
            group = GroupService.get_group(db, self.edit_group_id, self.project_id)
            db.close()

            if not group:
                logger.warning(f"[CreateGroupDialog] Group {self.edit_group_id} not found")
                return

            self._name_input.setText(group.get("name", ""))
            self._pinned_checkbox.setChecked(group.get("is_pinned", False))

            members = group.get("members", [])
            logger.info(f"[CreateGroupDialog] Edit mode: group has {len(members)} members")

            for member in members:
                branch_key = member.get("branch_key", "")
                if branch_key in self._people_cards:
                    self._people_cards[branch_key].set_selected(True)
                    self._selected_branch_keys.add(branch_key)
                else:
                    logger.warning(f"[CreateGroupDialog] Member {branch_key} not found in people cards")

            self._update_selection_ui()

        except Exception as e:
            logger.error(f"[CreateGroupDialog] Failed to load group: {e}", exc_info=True)

    def _on_person_toggled(self, branch_key: str, is_selected: bool):
        """Handle person selection change."""
        if is_selected:
            self._selected_branch_keys.add(branch_key)
        else:
            self._selected_branch_keys.discard(branch_key)

        self._update_selection_ui()

    def _update_selection_ui(self):
        """Update selection count and button state."""
        count = len(self._selected_branch_keys)

        if count == 0:
            self._selection_label.setText("Select at least 2 people")
            self._selection_label.setStyleSheet("color: #5f6368; font-size: 10pt;")
        elif count == 1:
            self._selection_label.setText("1 person selected (need at least 2)")
            self._selection_label.setStyleSheet("color: #ea4335; font-size: 10pt;")
        else:
            self._selection_label.setText(f"{count} people selected")
            self._selection_label.setStyleSheet("color: #1a73e8; font-size: 10pt; font-weight: 600;")

        # Enable create/save button when >= 2 people selected
        self._create_btn.setEnabled(count >= 2)

        # Show/rebuild cover selection when 2+ people selected
        self._cover_section.setVisible(count >= 2)
        if count >= 2:
            self._rebuild_cover_options()

        # Auto-suggest group name if empty
        if not self._name_input.text().strip() and count >= 2:
            names = []
            for branch_key in list(self._selected_branch_keys)[:3]:
                if branch_key in self._people_cards:
                    names.append(self._people_cards[branch_key].display_name)
            suggested = " + ".join(names)
            if count > 3:
                suggested += f" + {count - 3} more"
            self._name_input.setPlaceholderText(f"Suggested: {suggested}")

    def _rebuild_cover_options(self):
        """Rebuild the cover photo selection row from currently selected members."""
        # Clear existing cover widgets
        while self._cover_layout.count():
            item = self._cover_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Add a "None (auto)" option
        none_btn = QPushButton("Auto")
        none_btn.setFixedSize(56, 56)
        none_btn.setCursor(Qt.PointingHandCursor)
        is_none_selected = self._cover_branch_key is None
        none_btn.setStyleSheet(f"""
            QPushButton {{
                border: 2px solid {"#1a73e8" if is_none_selected else "#dadce0"};
                border-radius: 28px;
                background: {"#e8f0fe" if is_none_selected else "#f8f9fa"};
                font-size: 9px; color: #5f6368;
            }}
            QPushButton:hover {{ border-color: #1a73e8; }}
        """)
        none_btn.clicked.connect(lambda: self._on_cover_selected(None))
        self._cover_layout.addWidget(none_btn)

        # Add selected members' face thumbnails
        for bk in self._selected_branch_keys:
            card = self._people_cards.get(bk)
            if not card:
                continue

            thumb = card._base_thumbnail
            btn = QPushButton()
            btn.setFixedSize(56, 56)
            btn.setCursor(Qt.PointingHandCursor)

            if thumb and not thumb.isNull():
                # Render circular thumbnail on button
                icon_pix = QPixmap(56, 56)
                icon_pix.fill(Qt.transparent)
                p = QPainter(icon_pix)
                p.setRenderHint(QPainter.Antialiasing)
                scaled = thumb.scaled(52, 52, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                clip = QPainterPath()
                clip.addEllipse(2, 2, 52, 52)
                p.setClipPath(clip)
                xo = (scaled.width() - 52) // 2
                yo = (scaled.height() - 52) // 2
                p.drawPixmap(2 - xo, 2 - yo, scaled)
                p.setClipping(False)
                # Selection ring
                if bk == self._cover_branch_key:
                    p.setPen(QPen(QColor("#1a73e8"), 3))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(1, 1, 54, 54)
                p.end()
                btn.setIcon(icon_pix)
                btn.setIconSize(QSize(56, 56))

            is_selected = bk == self._cover_branch_key
            btn.setStyleSheet(f"""
                QPushButton {{
                    border: 2px solid {"#1a73e8" if is_selected else "transparent"};
                    border-radius: 28px;
                    background: transparent;
                    padding: 0;
                }}
                QPushButton:hover {{ border-color: #1a73e8; }}
            """)
            btn.setToolTip(card.display_name)
            _bk = bk  # capture for lambda
            btn.clicked.connect(lambda checked=False, b=_bk: self._on_cover_selected(b))
            self._cover_layout.addWidget(btn)

        self._cover_layout.addStretch()

    def _on_cover_selected(self, branch_key: Optional[str]):
        """Handle cover thumbnail selection."""
        self._cover_branch_key = branch_key
        self._rebuild_cover_options()  # Refresh selection state

    def _on_create(self):
        """Handle create/save button click."""
        # Resolve group name
        name = self._name_input.text().strip()
        if not name:
            names = []
            for branch_key in list(self._selected_branch_keys)[:3]:
                if branch_key in self._people_cards:
                    names.append(self._people_cards[branch_key].display_name)
            name = " + ".join(names)
            if len(self._selected_branch_keys) > 3:
                name += f" + {len(self._selected_branch_keys) - 3} more"

        self.group_name = name
        self.selected_people = list(self._selected_branch_keys)
        self.is_pinned = self._pinned_checkbox.isChecked()

        from services.group_service import GroupService
        from reference_db import ReferenceDB

        if self.is_edit_mode:
            # Edit mode: update existing group
            try:
                db = ReferenceDB()
                GroupService.update_group(
                    db,
                    group_id=self.edit_group_id,
                    name=self.group_name,
                    branch_keys=self.selected_people,
                    is_pinned=self.is_pinned,
                )
                # Set cover if user picked one
                if self._cover_branch_key:
                    rep_path = self._member_rep_paths.get(self._cover_branch_key)
                    if rep_path:
                        GroupService.set_group_cover(db, self.edit_group_id, rep_path)
                db.close()
                logger.info(
                    f"[CreateGroupDialog] Updated group {self.edit_group_id}: "
                    f"name='{self.group_name}', members={len(self.selected_people)}"
                )
                self.groupUpdated.emit(self.edit_group_id)
            except Exception as e:
                logger.error(f"[CreateGroupDialog] Failed to update group: {e}", exc_info=True)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Update Failed", f"Failed to update group:\n{e}")
                return
        else:
            # Create mode: create new group
            try:
                db = ReferenceDB()
                group_id = GroupService.create_group(
                    db=db,
                    project_id=self.project_id,
                    name=self.group_name,
                    branch_keys=self.selected_people,
                    is_pinned=self.is_pinned,
                )
                self.created_group_id = group_id
                # Set cover if user picked one
                if self._cover_branch_key:
                    rep_path = self._member_rep_paths.get(self._cover_branch_key)
                    if rep_path:
                        GroupService.set_group_cover(db, group_id, rep_path)
                db.close()
                logger.info(
                    f"[CreateGroupDialog] Created group {group_id}: "
                    f"name='{self.group_name}', members={len(self.selected_people)}"
                )
                self.groupCreated.emit({
                    'id': group_id,
                    'name': self.group_name,
                    'member_count': len(self.selected_people),
                    'members': self.selected_people,
                })
            except Exception as e:
                logger.error(f"[CreateGroupDialog] Failed to create group: {e}", exc_info=True)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Create Failed", f"Failed to create group:\n{e}")
                return

        self.accept()
