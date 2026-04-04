# ui/accordion_sidebar/groups_section.py
# Groups sub-section under People - user-defined groups of face clusters
#
# Architecture: Extends BaseSection (QObject) with signal-only context menu.
# create_content_widget() builds a FRESH QWidget each call (not self).
# AccordionSidebar handles group CRUD via its own handlers.

import logging
import threading
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Signal, Qt, QObject
from PySide6.QtGui import QPixmap, QImage, QPainter, QPainterPath, QPen, QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from reference_db import ReferenceDB
from translation_manager import tr
from .base_section import BaseSection

logger = logging.getLogger(__name__)


# ======================================================================
# Signals for async loading
# ======================================================================

class GroupsSectionSignals(QObject):
    """Signals for async groups loading."""
    loaded = Signal(int, list)   # (generation, groups_list)
    error = Signal(int, str)     # (generation, error_message)


# ======================================================================
# GroupCard — card widget for a single group (horizontal info-dense layout)
# ======================================================================

class GroupCard(QWidget):
    """
    Card widget representing a single group.

    Horizontal layout following Google Photos / Lightroom pattern:
    - Left: Stacked circular face avatars (or fallback icon)
    - Center: Group name + stats row (members, photos, match mode)
    - Right: Context menu button
    """

    clicked = Signal(int)                      # group_id
    context_menu_requested = Signal(int, str)  # (group_id, action)

    def __init__(
        self,
        group_id: int,
        display_name: str,
        member_count: int,
        result_count: int,
        is_stale: bool = False,
        member_pixmaps: Optional[List[QPixmap]] = None,
        icon: Optional[str] = None,
        match_mode: str = "together",
        is_pinned: bool = False,
        cover_pixmap: Optional[QPixmap] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.group_id = group_id
        self.group_name = display_name
        self.display_name = display_name
        self.member_count = member_count
        self.result_count = result_count
        self.is_stale = is_stale
        self.match_mode = match_mode
        self.is_pinned = is_pinned

        self.setMinimumHeight(60)
        self.setCursor(Qt.PointingHandCursor)

        # Main layout: icon/avatars | info | menu
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        # Left: Face avatars or fallback icon
        avatar_widget = self._build_avatar_area(member_pixmaps or [], icon)
        layout.addWidget(avatar_widget)

        # Center: Info column
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        # Name row with optional badges
        name_row = QHBoxLayout()
        name_row.setSpacing(6)

        name_label = QLabel(display_name)
        name_label.setStyleSheet("font-weight: 600; font-size: 12px; color: #202124;")
        name_row.addWidget(name_label)

        if is_pinned:
            pin_badge = QLabel("📌")
            pin_badge.setStyleSheet("font-size: 10px;")
            name_row.addWidget(pin_badge)

        if is_stale:
            stale_badge = QLabel("Stale")
            stale_badge.setStyleSheet("""
                background: #feefc3;
                color: #b06000;
                font-size: 9px;
                font-weight: 600;
                padding: 2px 6px;
                border-radius: 4px;
            """)
            name_row.addWidget(stale_badge)

        name_row.addStretch()
        info_layout.addLayout(name_row)

        # Stats row
        mode_text = "Together" if match_mode == "together" else "Same Event"
        stats_parts = []
        if member_count > 0:
            stats_parts.append(f"{member_count} people")
        if result_count >= 0:
            stats_parts.append(f"{result_count} photos")
        else:
            stats_parts.append("...")
        stats_parts.append(mode_text)
        stats_label = QLabel(" · ".join(stats_parts))
        stats_label.setStyleSheet("color: #5f6368; font-size: 10px;")
        info_layout.addWidget(stats_label)

        layout.addLayout(info_layout, 1)

        # Right: Menu button
        menu_btn = QToolButton()
        menu_btn.setText("⋮")
        menu_btn.setAutoRaise(True)
        menu_btn.setFixedSize(24, 24)
        menu_btn.setStyleSheet("""
            QToolButton {
                color: #5f6368;
                font-size: 16px;
                border: none;
            }
            QToolButton:hover { background: #e8eaed; border-radius: 4px; }
        """)
        menu_btn.clicked.connect(self._show_context_menu)
        layout.addWidget(menu_btn)

        # Card styling
        self.setStyleSheet("""
            GroupCard {
                background: #fff;
                border: 1px solid #e8eaed;
                border-radius: 8px;
            }
            GroupCard:hover {
                background: #f8f9fa;
                border-color: #dadce0;
            }
            GroupCard[selected="true"] {
                background: #e8f0fe;
                border-color: #1a73e8;
            }
        """)

    def _build_avatar_area(
        self, pixmaps: List[QPixmap], icon: Optional[str]
    ) -> QWidget:
        """Build the left area: stacked face avatars or fallback emoji icon."""
        container = QWidget()
        container.setFixedSize(56, 44)

        if pixmaps and any(p and not p.isNull() for p in pixmaps if p):
            # Stacked circular face avatars (Apple Photos / Google Photos style)
            label = QLabel(container)
            label.setFixedSize(container.size())

            avatar_size = 32
            overlap = 10
            max_show = min(len(pixmaps), 3)

            canvas = QPixmap(container.width(), container.height())
            canvas.fill(Qt.transparent)
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.Antialiasing)

            for i in range(max_show):
                x = i * (avatar_size - overlap)
                y = (container.height() - avatar_size) // 2

                if i < len(pixmaps) and pixmaps[i] and not pixmaps[i].isNull():
                    scaled = pixmaps[i].scaled(
                        avatar_size, avatar_size,
                        Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
                    )
                    path = QPainterPath()
                    path.addEllipse(x, y, avatar_size, avatar_size)
                    painter.setClipPath(path)
                    painter.drawPixmap(x, y, scaled)
                    painter.setClipping(False)
                else:
                    painter.setBrush(QColor("#e8eaed"))
                    painter.setPen(QPen(QColor("#dadce0"), 1))
                    painter.drawEllipse(x, y, avatar_size, avatar_size)
                    painter.setPen(QColor("#5f6368"))
                    painter.setFont(QFont("", 12))
                    painter.drawText(x, y, avatar_size, avatar_size, Qt.AlignCenter, "?")

                # White border ring
                painter.setPen(QPen(QColor("white"), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(x, y, avatar_size, avatar_size)

            painter.end()
            label.setPixmap(canvas)
        else:
            # Fallback: emoji icon
            icon_label = QLabel(icon or "👥", container)
            icon_label.setStyleSheet("font-size: 24px;")
            icon_label.setFixedSize(container.size())
            icon_label.setAlignment(Qt.AlignCenter)

        return container

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.group_id)
        super().mousePressEvent(event)

    def _show_context_menu(self):
        """Show context menu for group actions."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)

        edit_action = menu.addAction("✏️ Edit Group")
        edit_action.triggered.connect(
            lambda: self.context_menu_requested.emit(self.group_id, "edit_members")
        )

        rename_action = menu.addAction("📝 Rename")
        rename_action.triggered.connect(
            lambda: self.context_menu_requested.emit(self.group_id, "rename")
        )

        pin_text = "📌 Unpin" if self.is_pinned else "📌 Pin to Top"
        pin_action = menu.addAction(pin_text)
        pin_action.triggered.connect(
            lambda: self.context_menu_requested.emit(self.group_id, "toggle_pin")
        )

        menu.addSeparator()

        recompute_together = menu.addAction("🔄 Recompute (Together)")
        recompute_together.triggered.connect(
            lambda: self.context_menu_requested.emit(self.group_id, "recompute_together")
        )

        recompute_event = menu.addAction("🔄 Recompute (Same Event)")
        recompute_event.triggered.connect(
            lambda: self.context_menu_requested.emit(self.group_id, "recompute_event")
        )

        menu.addSeparator()

        delete_action = menu.addAction("🗑️ Delete Group")
        delete_action.triggered.connect(
            lambda: self.context_menu_requested.emit(self.group_id, "delete")
        )

        menu.exec_(self.mapToGlobal(self.rect().bottomRight()))


# ======================================================================
# GroupsSection — BaseSection subclass for Groups sub-tab under People
# ======================================================================

class GroupsSection(BaseSection):
    """
    Groups sub-section displayed under People > Groups tab.

    Architecture:
    - Extends BaseSection (QObject) — NOT QWidget
    - create_content_widget(data) returns a FRESH QWidget each call
    - Context menu emits signals only — AccordionSidebar handles CRUD
    - Simple ops (rename, pin) handled internally

    Signal contract (consumed by PeopleSection → AccordionSidebar):
        groupSelected(int, str)      — (group_id, match_mode)
        newGroupRequested()          — user clicked "+ New Group"
        editGroupRequested(int)      — (group_id)
        deleteGroupRequested(int)    — (group_id)
        recomputeRequested(int, str) — (group_id, match_mode)
    """

    # Signals forwarded through PeopleSection → AccordionSidebar → GoogleLayout
    groupSelected = Signal(int, str)        # (group_id, match_mode)
    newGroupRequested = Signal()
    editGroupRequested = Signal(int)
    deleteGroupRequested = Signal(int)
    recomputeRequested = Signal(int, str)   # (group_id, match_mode)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = GroupsSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)

        self._groups_data: List[Dict] = []
        self._cards: Dict[int, GroupCard] = {}
        self._active_group_id: int = -1

    # --- BaseSection abstract methods ---

    def get_section_id(self) -> str:
        return "groups"

    def get_title(self) -> str:
        return "Groups"

    def get_icon(self) -> str:
        return "👥"

    def load_section(self) -> None:
        """Load groups data in a background thread."""
        if not self.project_id:
            logger.warning("[GroupsSection] No project_id set")
            return

        self._generation += 1
        current_gen = self._generation
        self._loading = True

        logger.info(f"[GroupsSection] Loading groups (generation {current_gen})...")

        def work():
            try:
                from services.people_group_service import PeopleGroupService
                db = ReferenceDB()
                service = PeopleGroupService(db)
                groups = service.get_all_groups(self.project_id)
                db.close()
                self.signals.loaded.emit(current_gen, groups)
            except Exception as e:
                logger.error(f"[GroupsSection] Load failed: {e}")
                self.signals.error.emit(current_gen, str(e))

        threading.Thread(target=work, daemon=True).start()

    def create_content_widget(self, data) -> Optional[QWidget]:
        """
        Build a FRESH QWidget from loaded groups data.

        Returns a new widget each call — never returns self.
        Called by PeopleSection._ensure_groups_tab on_groups_loaded callback.
        """
        groups: List[Dict] = data if data else []
        self._groups_data = groups
        self._cards.clear()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        if not groups:
            # === Empty state with CTA ===
            empty = QWidget()
            empty_layout = QVBoxLayout(empty)
            empty_layout.setAlignment(Qt.AlignCenter)
            empty_layout.setSpacing(12)

            icon_label = QLabel("👥")
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setStyleSheet("font-size: 48px;")
            empty_layout.addWidget(icon_label)

            text_label = QLabel(
                "No groups yet.\n"
                "Create a group to find photos\n"
                "where people appear together."
            )
            text_label.setAlignment(Qt.AlignCenter)
            text_label.setWordWrap(True)
            text_label.setStyleSheet("color: #5f6368; font-size: 11pt;")
            empty_layout.addWidget(text_label)

            create_btn = QPushButton("➕ Create New Group")
            create_btn.setCursor(Qt.PointingHandCursor)
            create_btn.setStyleSheet("""
                QPushButton {
                    padding: 10px 20px; border: none; border-radius: 8px;
                    background: #1a73e8; color: white; font-weight: 600;
                    font-size: 11pt;
                }
                QPushButton:hover { background: #1557b0; }
            """)
            create_btn.clicked.connect(lambda _checked=False: self.newGroupRequested.emit())
            empty_layout.addWidget(create_btn, alignment=Qt.AlignCenter)

            layout.addStretch()
            layout.addWidget(empty)
            layout.addStretch()
            return container

        # === Has groups: header + search + card list ===

        # Header row: "+ New Group" button + count
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        btn_new = QPushButton("+ New Group")
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.setStyleSheet("""
            QPushButton {
                padding: 6px 14px; border: 1px solid #1a73e8;
                border-radius: 6px; background: #1a73e8;
                color: white; font-weight: 600; font-size: 10pt;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        btn_new.clicked.connect(lambda _checked=False: self.newGroupRequested.emit())
        header_layout.addWidget(btn_new)

        header_layout.addStretch()

        count_label = QLabel(
            f"{len(groups)} group{'s' if len(groups) != 1 else ''}"
        )
        count_label.setStyleSheet("color: #5f6368; font-size: 9pt;")
        header_layout.addWidget(count_label)

        layout.addWidget(header)

        # Search bar
        search_input = QLineEdit()
        search_input.setPlaceholderText("Search groups...")
        search_input.setClearButtonEnabled(True)
        search_input.setStyleSheet("""
            QLineEdit {
                padding: 6px 10px; border: 1px solid #dadce0;
                border-radius: 6px; background: #fff; font-size: 10pt;
            }
            QLineEdit:focus { border-color: #1a73e8; }
        """)
        search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(search_input)

        # Scroll area with group cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        scroll_layout.setSpacing(6)

        for g in groups:
            # Load member face thumbnails from rep_paths (max 3)
            member_pixmaps = []
            for rep_path in g.get("member_rep_paths", []):
                pix = self._load_rep_thumbnail(rep_path)
                if pix:
                    member_pixmaps.append(pix)

            card = GroupCard(
                group_id=g["id"],
                display_name=g.get("name", g.get("display_name", "Group")),
                member_count=g.get("member_count", 0),
                result_count=g.get("result_count", -1),
                is_stale=g.get("is_stale", False),
                match_mode=g.get("match_mode", "together"),
                is_pinned=g.get("is_pinned", False),
                member_pixmaps=member_pixmaps if member_pixmaps else None,
            )
            card.clicked.connect(self._on_group_clicked)
            card.context_menu_requested.connect(self._on_group_context_menu)

            # Apply active highlight
            if g["id"] == self._active_group_id:
                card.setProperty("selected", True)

            scroll_layout.addWidget(card)
            self._cards[g["id"]] = card

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        return container

    # --- Group interaction handlers ---

    def _on_group_clicked(self, group_id: int):
        """Handle group click with toggle-selection behavior."""
        if group_id == self._active_group_id:
            # Click again to deselect
            self._active_group_id = -1
            self.groupSelected.emit(-1, "")
        else:
            self._active_group_id = group_id
            match_mode = "together"
            for g in self._groups_data:
                if g.get("id") == group_id:
                    match_mode = g.get("match_mode", "together")
                    break
            self.groupSelected.emit(group_id, match_mode)

        # Update visual state on all cards
        for gid, card in self._cards.items():
            if isValid(card):
                card.setProperty("selected", gid == self._active_group_id)
                card.style().unpolish(card)
                card.style().polish(card)

    def _on_group_context_menu(self, group_id: int, action: str):
        """Route context menu actions.

        Signal-only for CRUD (AccordionSidebar handles the logic).
        Internal handling for simple ops (rename, pin toggle).
        """
        if action in ("edit_members", "edit"):
            self.editGroupRequested.emit(group_id)
        elif action == "delete":
            self.deleteGroupRequested.emit(group_id)
        elif action in ("reindex", "recompute_together"):
            self.recomputeRequested.emit(group_id, "together")
        elif action == "recompute_event":
            self.recomputeRequested.emit(group_id, "event_window")
        elif action == "rename":
            self._rename_group(group_id)
        elif action == "toggle_pin":
            self._toggle_pin(group_id)

    # --- Simple internal operations (no AccordionSidebar handler needed) ---

    def _rename_group(self, group_id: int):
        """Rename a group via input dialog."""
        from PySide6.QtWidgets import QInputDialog

        current_name = ""
        for g in self._groups_data:
            if g["id"] == group_id:
                current_name = g.get("name", "")
                break

        parent_widget = self.parent()
        if not isinstance(parent_widget, QWidget):
            parent_widget = None

        new_name, ok = QInputDialog.getText(
            parent_widget, "Rename Group", "New name:", text=current_name
        )
        if ok and new_name.strip():
            try:
                from services.group_service import GroupService
                db = ReferenceDB()
                GroupService.update_group(db, group_id, name=new_name.strip())
                db.close()
                self.load_section()  # Refresh list
            except Exception as e:
                logger.error(f"[GroupsSection] Rename failed: {e}")

    def _toggle_pin(self, group_id: int):
        """Toggle pinned state."""
        try:
            from services.group_service import GroupService
            current_pinned = False
            for g in self._groups_data:
                if g["id"] == group_id:
                    current_pinned = g.get("is_pinned", False)
                    break

            db = ReferenceDB()
            GroupService.update_group(db, group_id, is_pinned=not current_pinned)
            db.close()
            self.load_section()  # Refresh list
        except Exception as e:
            logger.error(f"[GroupsSection] Pin toggle failed: {e}")

    # --- Public helpers ---

    def set_active_group(self, group_id: int):
        """Highlight a group card externally (e.g., from GoogleLayout selection)."""
        self._active_group_id = group_id
        for gid, card in self._cards.items():
            if isValid(card):
                card.setProperty("selected", gid == group_id)
                card.style().unpolish(card)
                card.style().polish(card)

    def set_db(self, db):
        """Accept a DB reference (no-op — per-thread instances used)."""
        pass

    def _on_search_changed(self, text: str):
        """Filter group cards by name."""
        ft = text.strip().lower()
        for gid, card in self._cards.items():
            if isValid(card):
                card.setVisible(ft in card.group_name.lower() if ft else True)

    @staticmethod
    def _load_rep_thumbnail(rep_path: str) -> 'Optional[QPixmap]':
        """Load a face crop thumbnail from disk path for GroupCard avatar."""
        import os
        if not rep_path or not os.path.exists(rep_path):
            return None
        try:
            from PySide6.QtGui import QImage
            img = QImage(rep_path)
            if img.isNull():
                return None
            if img.width() > 64 or img.height() > 64:
                img = img.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return QPixmap.fromImage(img)
        except Exception:
            return None


# ======================================================================
# CreateGroupDialog — modal for creating/editing a group
# ======================================================================

class CreateGroupDialog(QDialog):
    """
    Dialog for creating or editing a people group.

    Shows a searchable list of all people in the project.
    User selects 2+ people, enters a name, and saves.
    """

    def __init__(
        self,
        project_id: int,
        existing_group: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.project_id = project_id
        self.existing_group = existing_group
        self._selected_branch_keys: List[str] = []
        self._people_data: List[Dict] = []

        self.setWindowTitle("Edit Group" if existing_group else "Create Group")
        self.setMinimumSize(400, 500)
        self.setModal(True)

        self._setup_ui()
        self._load_people()

        if existing_group:
            self._populate_existing()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Group name
        name_group = QWidget()
        name_layout = QHBoxLayout(name_group)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_label = QLabel("Group name:")
        name_label.setStyleSheet("font-weight: 600;")
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g. Family, Travel Buddies...")
        self._name_input.setStyleSheet("""
            QLineEdit {
                padding: 8px 12px; border: 1px solid #dadce0;
                border-radius: 6px; font-size: 11pt;
            }
            QLineEdit:focus { border-color: #1a73e8; }
        """)
        name_layout.addWidget(name_label)
        name_layout.addWidget(self._name_input, 1)
        layout.addWidget(name_group)

        # Selected members chip area
        self._chips_label = QLabel("Selected: (none)")
        self._chips_label.setWordWrap(True)
        self._chips_label.setStyleSheet("color: #1a73e8; font-size: 10pt; padding: 4px;")
        layout.addWidget(self._chips_label)

        # Search people
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 Search people to add...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setStyleSheet("""
            QLineEdit {
                padding: 6px 10px; border: 1px solid #dadce0;
                border-radius: 6px; font-size: 10pt;
            }
            QLineEdit:focus { border-color: #1a73e8; }
        """)
        self._search_input.textChanged.connect(self._filter_people)
        layout.addWidget(self._search_input)

        # People list (multi-select)
        self._people_list = QListWidget()
        self._people_list.setSelectionMode(QListWidget.MultiSelection)
        self._people_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #dadce0; border-radius: 6px;
                font-size: 10pt; padding: 4px;
            }
            QListWidget::item { padding: 6px 8px; border-radius: 4px; }
            QListWidget::item:selected { background: #e8f0fe; color: #1a73e8; }
            QListWidget::item:hover { background: #f1f3f4; }
        """)
        self._people_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._people_list, 1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setStyleSheet("""
            QPushButton {
                padding: 8px 20px; border: 1px solid #dadce0;
                border-radius: 6px; background: white;
            }
            QPushButton:hover { background: #f1f3f4; }
        """)
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self._btn_cancel)

        self._btn_save = QPushButton("Create Group" if not self.existing_group else "Save Changes")
        self._btn_save.setEnabled(False)
        self._btn_save.setStyleSheet("""
            QPushButton {
                padding: 8px 20px; border: none; border-radius: 6px;
                background: #1a73e8; color: white; font-weight: 600;
            }
            QPushButton:hover { background: #1557b0; }
            QPushButton:disabled { background: #dadce0; color: #80868b; }
        """)
        self._btn_save.clicked.connect(self.accept)
        btn_layout.addWidget(self._btn_save)

        layout.addLayout(btn_layout)

    def _load_people(self):
        """Load all people from the database."""
        try:
            db = ReferenceDB()
            rows = db.get_face_clusters(self.project_id) or []
            db.close()

            self._people_data = []
            for row in rows:
                if isinstance(row, dict):
                    bk = row.get("branch_key", "")
                    name = row.get("display_name", bk)
                    count = row.get("member_count", 0)
                else:
                    bk = row[0] if len(row) > 0 else ""
                    name = row[1] if len(row) > 1 else bk
                    count = row[2] if len(row) > 2 else 0

                # Skip unidentified cluster
                if bk == "face_unidentified":
                    continue

                self._people_data.append({
                    "branch_key": bk,
                    "display_name": name,
                    "count": count,
                })

            self._populate_list()
        except Exception as e:
            logger.error(f"[CreateGroupDialog] Failed to load people: {e}")

    def _populate_list(self, filter_text: str = ""):
        """Populate the list widget with people data."""
        self._people_list.clear()
        ft = filter_text.strip().lower()

        for p in self._people_data:
            name = p["display_name"]
            if ft and ft not in name.lower():
                continue

            item = QListWidgetItem(f"{name}  ({p['count']} photos)")
            item.setData(Qt.UserRole, p["branch_key"])
            self._people_list.addItem(item)

            # Re-select if was selected
            if p["branch_key"] in self._selected_branch_keys:
                item.setSelected(True)

    def _populate_existing(self):
        """Pre-fill dialog with existing group data."""
        if not self.existing_group:
            return
        self._name_input.setText(self.existing_group.get("name", ""))
        self._selected_branch_keys = [
            m["branch_key"] for m in self.existing_group.get("members", [])
        ]
        self._populate_list()
        self._update_chips()

    def _filter_people(self, text: str):
        self._populate_list(text)

    def _on_selection_changed(self):
        self._selected_branch_keys = []
        for item in self._people_list.selectedItems():
            bk = item.data(Qt.UserRole)
            if bk:
                self._selected_branch_keys.append(bk)
        self._update_chips()

    def _update_chips(self):
        """Update the selected members display and save button state."""
        if not self._selected_branch_keys:
            self._chips_label.setText("Selected: (none)")
            self._btn_save.setEnabled(False)
            return

        names = []
        for bk in self._selected_branch_keys:
            for p in self._people_data:
                if p["branch_key"] == bk:
                    names.append(p["display_name"])
                    break

        self._chips_label.setText(f"Selected ({len(names)}): {', '.join(names)}")
        self._btn_save.setEnabled(len(self._selected_branch_keys) >= 2)

        # Auto-suggest name if empty
        if not self._name_input.text().strip() and names:
            from services.group_service import GroupService
            self._name_input.setPlaceholderText(GroupService.suggest_group_name(names))

    def get_result(self) -> Dict[str, Any]:
        """Get the dialog result after accept."""
        name = self._name_input.text().strip()
        if not name:
            # Use auto-suggested name
            names = []
            for bk in self._selected_branch_keys:
                for p in self._people_data:
                    if p["branch_key"] == bk:
                        names.append(p["display_name"])
                        break
            from services.group_service import GroupService
            name = GroupService.suggest_group_name(names)

        return {
            "name": name,
            "branch_keys": list(self._selected_branch_keys),
        }


# ======================================================================
# GroupsSubsectionWidget — legacy QWidget wrapper for accordion_sidebar.py
# ======================================================================

class GroupsSubsectionWidget(QWidget):
    """
    Legacy QWidget wrapper around GroupsSection.

    Used by the legacy accordion_sidebar.py (SidebarQt / CurrentLayout)
    which expects a QWidget that can be added directly to a QStackedWidget.

    The modular path (PeopleSection in ui/accordion_sidebar/) uses
    GroupsSection (BaseSection) directly.
    """

    # Legacy signals (accordion_sidebar.py connects these)
    groupSelected = Signal(int, str)
    groupCreated = Signal(int)
    groupDeleted = Signal(int)
    groupUpdated = Signal(int)
    groupReindexRequested = Signal(int)

    # New signals (for forward compat if legacy sidebar is upgraded)
    newGroupRequested = Signal()
    editGroupRequested = Signal(int)
    deleteGroupRequested = Signal(int)
    recomputeRequested = Signal(int, str)

    def __init__(self, project_id=0, parent: Optional[QWidget] = None):
        # Handle GroupsSubsectionWidget(QWidget) call pattern
        if isinstance(project_id, QWidget):
            parent = project_id
            project_id = 0
        super().__init__(parent)
        self.project_id = int(project_id) if project_id else 0

        # Internal GroupsSection handles data loading + UI building
        self._gs = GroupsSection(self)
        if self.project_id:
            self._gs.set_project(self.project_id)

        # Forward signals from GroupsSection
        self._gs.groupSelected.connect(self.groupSelected.emit)
        self._gs.newGroupRequested.connect(self.newGroupRequested.emit)
        self._gs.editGroupRequested.connect(self.editGroupRequested.emit)
        self._gs.deleteGroupRequested.connect(self.deleteGroupRequested.emit)
        self._gs.recomputeRequested.connect(self.recomputeRequested.emit)

        # Signals proxy for PeopleSection compatibility
        self.signals = self._gs.signals

        # Layout for content widget insertion
        self._wrapper_layout = QVBoxLayout(self)
        self._wrapper_layout.setContentsMargins(0, 0, 0, 0)

        # When data loads, build and insert content widget
        self._gs.signals.loaded.connect(self._on_loaded)

    def _on_loaded(self, gen, data):
        if gen != self._gs._generation:
            return
        content = self._gs.create_content_widget(data)
        if content:
            # Clear old content
            while self._wrapper_layout.count():
                item = self._wrapper_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            self._wrapper_layout.addWidget(content)

    def load_groups(self):
        """Legacy API — load groups data."""
        self._gs.load_section()

    def load_section(self):
        """BaseSection-compatible API."""
        self._gs.load_section()

    def create_content_widget(self, data):
        """Delegate to GroupsSection."""
        return self._gs.create_content_widget(data)

    def set_project(self, project_id: int):
        """Update project and reload."""
        self.project_id = project_id
        self._gs.set_project(project_id)

    def set_db(self, db):
        """Accept a DB reference (no-op)."""
        pass
