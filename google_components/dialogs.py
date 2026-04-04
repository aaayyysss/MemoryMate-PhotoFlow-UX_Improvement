"""
Google Photos Layout - Dialog Classes
Extracted from google_layout.py for better organization.

Contains:
- PersonPickerDialog: Visual person/face picker dialog for merging faces

Phase 3E extraction - Dialog Classes
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QWidget,
    QGridLayout, QPushButton, QLineEdit
)
from PySide6.QtGui import QPixmap, QPainter, QPainterPath
from PySide6.QtCore import Qt, QObject, QEvent
import base64
import os


class PersonPickerDialog(QDialog):
    """
    Visual person picker dialog with face previews.

    Allows user to select a person/face cluster from a grid of face previews,
    typically used for merging face clusters.
    """

    def __init__(self, project_id: int, parent=None, exclude_branch=None):
        """
        Initialize person picker dialog.

        Args:
            project_id: Project ID to fetch people from
            parent: Parent widget
            exclude_branch: Optional branch_key to exclude from list
        """
        super().__init__(parent)
        self.project_id = project_id
        self.exclude_branch = exclude_branch
        self.selected_branch = None
        self.current_focus_index = 0

        self.setWindowTitle("Select Merge Target")
        self.resize(700, 600)

        self._setup_ui()
        self._load_people()

    def _setup_ui(self):
        """Setup the dialog UI."""
        # Main layout
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(16, 16, 16, 16)
        self.outer_layout.setSpacing(12)

        # Header
        header = QLabel("<b>Select a person to merge into:</b>")
        header.setStyleSheet("font-size: 12pt;")
        self.outer_layout.addWidget(header)

        # Search box
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search people...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setStyleSheet("""
            QLineEdit {
                padding: 8px 12px;
                border: 1px solid #dadce0;
                border-radius: 20px;
                background: #f8f9fa;
                font-size: 10pt;
            }
            QLineEdit:focus {
                border: 2px solid #1a73e8;
                background: white;
            }
        """)
        self.outer_layout.addWidget(self.search_box)

        # Scrollable grid
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: white; }")

        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setSpacing(12)

        self.scroll.setWidget(self.container)
        self.outer_layout.addWidget(self.scroll, 1)

        # Person cards list (populated later)
        self.person_cards = []

        # Actions
        actions = QHBoxLayout()
        actions.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)
        self.outer_layout.addLayout(actions)

        # Install keyboard navigation event filter
        self.key_filter = self._KeyNavFilter(self)
        self.installEventFilter(self.key_filter)

        # Connect search filter
        self.search_box.textChanged.connect(self._filter_cards)

    def _load_people(self):
        """Load people from database and populate grid."""
        from reference_db import ReferenceDB

        db = ReferenceDB()
        people = []
        face_samples = {}

        with db._connect() as conn:
            cur = conn.cursor()

            # Fetch all people
            query = "SELECT branch_key, label, count, rep_path, rep_thumb_png FROM face_branch_reps WHERE project_id = ?"
            params = [self.project_id]

            if self.exclude_branch:
                query += " AND branch_key != ?"
                params.append(self.exclude_branch)

            query += " ORDER BY count DESC"
            cur.execute(query, params)
            people = cur.fetchall() or []

            # Fetch additional face samples for preview grid (top 3 per person)
            for branch_key, _, _, _, _ in people:
                cur.execute(
                    "SELECT crop_path FROM face_crops WHERE project_id = ? AND branch_key = ? LIMIT 3",
                    (self.project_id, branch_key)
                )
                face_samples[branch_key] = [r[0] for r in cur.fetchall()]

        # Populate grid with person cards
        for i, (branch_key, label, count, rep_path, rep_thumb) in enumerate(people):
            card = self._create_person_card(branch_key, label, count, rep_path, rep_thumb, face_samples)
            self.person_cards.append(card)
            row = i // 4
            col = i % 4
            self.grid.addWidget(card, row, col)

        # Set initial focus
        if self.person_cards:
            self.person_cards[0].setFocus()

    def _create_person_card(self, branch_key, label, count, rep_path, rep_thumb, face_samples):
        """Create a person card widget."""
        card = QPushButton()
        card.setFixedSize(140, 180)  # Larger to fit multiple faces
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet("""
            QPushButton {
                background: white;
                border: 2px solid #dadce0;
                border-radius: 8px;
                text-align: center;
            }
            QPushButton:hover {
                border: 2px solid #1a73e8;
                background: #f8f9fa;
            }
            QPushButton:pressed {
                background: #e8eaed;
            }
            QPushButton:focus {
                border: 3px solid #1a73e8;
                outline: none;
            }
        """)

        # Build card content
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(4)

        # Multiple face previews (grid of 2-3 faces)
        samples = face_samples.get(branch_key, [])
        if len(samples) > 1:
            faces_container = self._create_multi_face_preview(samples)
            card_layout.addWidget(faces_container, 0, Qt.AlignCenter)
        else:
            face_label = self._create_single_face_preview(rep_path, rep_thumb)
            card_layout.addWidget(face_label, 0, Qt.AlignCenter)

        # Name
        name_label = QLabel(label or "Unnamed")
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #202124;")
        card_layout.addWidget(name_label)

        # Count with confidence badge
        conf_badge = "✅" if count >= 10 else ("⚠️" if count >= 5 else "❓")
        count_label = QLabel(f"{conf_badge} {count} photos")
        count_label.setAlignment(Qt.AlignCenter)
        count_label.setStyleSheet("font-size: 8pt; color: #5f6368;")
        card_layout.addWidget(count_label)

        # Store data
        card.branch_key = branch_key
        card.display_name = label or "Unnamed"
        card.setFocusPolicy(Qt.StrongFocus)  # Enable keyboard focus

        # Click handler
        card.clicked.connect(lambda: self._on_card_clicked(branch_key))

        return card

    def _create_multi_face_preview(self, samples):
        """Create widget showing multiple face previews."""
        faces_container = QWidget()
        faces_layout = QHBoxLayout(faces_container)
        faces_layout.setContentsMargins(0, 0, 0, 0)
        faces_layout.setSpacing(4)

        for idx, sample_path in enumerate(samples[:3]):
            mini_face = QLabel()
            size = 38 if len(samples) >= 3 else 50  # Smaller if showing 3
            mini_face.setFixedSize(size, size)
            mini_face.setAlignment(Qt.AlignCenter)

            try:
                pix = QPixmap(sample_path) if sample_path and os.path.exists(sample_path) else None
                if pix and not pix.isNull():
                    # Make circular
                    scaled = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    if scaled.width() > size or scaled.height() > size:
                        x = (scaled.width() - size) // 2
                        y = (scaled.height() - size) // 2
                        scaled = scaled.copy(x, y, size, size)

                    output = QPixmap(size, size)
                    output.fill(Qt.transparent)
                    painter = QPainter(output)
                    painter.setRenderHint(QPainter.Antialiasing)
                    path = QPainterPath()
                    path.addEllipse(0, 0, size, size)
                    painter.setClipPath(path)
                    painter.drawPixmap(0, 0, scaled)
                    painter.end()
                    mini_face.setPixmap(output)
                else:
                    mini_face.setStyleSheet(f"background: #e8eaed; border-radius: {size//2}px; font-size: {size//2}pt;")
                    mini_face.setText("👤")
            except Exception:
                mini_face.setStyleSheet(f"background: #e8eaed; border-radius: {size//2}px; font-size: {size//2}pt;")
                mini_face.setText("👤")

            faces_layout.addWidget(mini_face)

        return faces_container

    def _create_single_face_preview(self, rep_path, rep_thumb):
        """Create widget showing single face preview (circular 80x80)."""
        face_label = QLabel()
        face_label.setFixedSize(80, 80)
        face_label.setAlignment(Qt.AlignCenter)

        try:
            pix = None
            if rep_thumb:
                data = base64.b64decode(rep_thumb) if isinstance(rep_thumb, str) else rep_thumb
                pix = QPixmap()
                pix.loadFromData(data)
            if (pix is None or pix.isNull()) and rep_path and os.path.exists(rep_path):
                pix = QPixmap(rep_path)

            if pix and not pix.isNull():
                # Make circular
                scaled = pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                if scaled.width() > 80 or scaled.height() > 80:
                    x = (scaled.width() - 80) // 2
                    y = (scaled.height() - 80) // 2
                    scaled = scaled.copy(x, y, 80, 80)

                output = QPixmap(80, 80)
                output.fill(Qt.transparent)
                painter = QPainter(output)
                painter.setRenderHint(QPainter.Antialiasing)
                path = QPainterPath()
                path.addEllipse(0, 0, 80, 80)
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, scaled)
                painter.end()
                face_label.setPixmap(output)
            else:
                face_label.setStyleSheet("background: #e8eaed; border-radius: 40px; font-size: 24pt;")
                face_label.setText("👤")
        except Exception:
            face_label.setStyleSheet("background: #e8eaed; border-radius: 40px; font-size: 24pt;")
            face_label.setText("👤")

        return face_label

    def _on_card_clicked(self, branch_key):
        """Handle person card click."""
        self.selected_branch = branch_key
        self.accept()

    def _filter_cards(self, text):
        """Filter person cards based on search text."""
        query = text.lower().strip()
        for card in self.person_cards:
            if not query or query in card.display_name.lower():
                card.setVisible(True)
            else:
                card.setVisible(False)

    def _navigate_cards(self, direction):
        """Navigate through person cards with arrow keys."""
        visible_cards = [c for c in self.person_cards if c.isVisible()]
        if not visible_cards:
            return

        cols = 4
        current_idx = self.current_focus_index

        if direction == "right" and current_idx < len(visible_cards) - 1:
            current_idx += 1
        elif direction == "left" and current_idx > 0:
            current_idx -= 1
        elif direction == "down":
            next_idx = current_idx + cols
            if next_idx < len(visible_cards):
                current_idx = next_idx
        elif direction == "up":
            prev_idx = current_idx - cols
            if prev_idx >= 0:
                current_idx = prev_idx

        self.current_focus_index = current_idx
        visible_cards[current_idx].setFocus()
        # Ensure card is visible in scroll area
        self.scroll.ensureWidgetVisible(visible_cards[current_idx])

    def _select_focused_card(self):
        """Select the currently focused card with Enter/Return."""
        visible_cards = [c for c in self.person_cards if c.isVisible()]
        if visible_cards and 0 <= self.current_focus_index < len(visible_cards):
            focused_card = visible_cards[self.current_focus_index]
            self.selected_branch = focused_card.branch_key
            self.accept()

    class _KeyNavFilter(QObject):
        """Event filter for keyboard navigation in person picker."""

        def __init__(self, dialog):
            super().__init__()
            self.dialog = dialog

        def eventFilter(self, obj, event):
            """Handle keyboard navigation events."""
            if event.type() == QEvent.KeyPress:
                key = event.key()
                if key == Qt.Key_Right:
                    self.dialog._navigate_cards("right")
                    return True
                elif key == Qt.Key_Left:
                    self.dialog._navigate_cards("left")
                    return True
                elif key == Qt.Key_Down:
                    self.dialog._navigate_cards("down")
                    return True
                elif key == Qt.Key_Up:
                    self.dialog._navigate_cards("up")
                    return True
                elif key in (Qt.Key_Return, Qt.Key_Enter):
                    self.dialog._select_focused_card()
                    return True
            return False
