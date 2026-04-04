#!/usr/bin/env python3
"""
Face Naming Dialog - Interactive dialog for naming manually added faces.

ENHANCEMENT #3: After saving manual faces, prompt user to name them immediately.
This eliminates confusion from generic names like "manual_37ebe45d".

Author: Claude Code
Date: December 17, 2025
"""

import logging
import os
from typing import List, Dict
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QScrollArea, QWidget, QCompleter, QMessageBox
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)


class FaceNamingDialog(QDialog):
    """
    Dialog for naming newly saved manual faces.

    Shows thumbnails of saved faces and allows user to enter names.
    Includes autocomplete from existing person names.
    """

    def __init__(self, face_data: List[Dict], project_id: int, parent=None):
        """
        Initialize face naming dialog.

        Args:
            face_data: List of dicts with 'branch_key' and 'crop_path'
            project_id: Current project ID
            parent: Parent widget
        """
        super().__init__(parent)

        self.face_data = face_data
        self.project_id = project_id
        self.name_inputs = []

        self.setWindowTitle(f"Name {len(face_data)} New Face(s)")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        self._create_ui()

    def _create_ui(self):
        """Create the UI layout."""
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(f"✅ Successfully saved {len(self.face_data)} face(s)!")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; color: #34a853; padding: 10px;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Instruction
        instruction = QLabel("Give each face a name to organize them:")
        instruction.setStyleSheet("color: #5f6368; font-size: 11pt; padding: 5px;")
        instruction.setAlignment(Qt.AlignCenter)
        layout.addWidget(instruction)

        # Scroll area for face cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        cards_layout = QVBoxLayout(container)
        cards_layout.setSpacing(10)

        for i, face in enumerate(self.face_data):
            card = self._create_face_card(i, face)
            cards_layout.addWidget(card)

        cards_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Tip
        tip = QLabel("💡 Tip: Start typing to autocomplete with existing names, or enter a new name.")
        tip.setStyleSheet("color: #5f6368; font-size: 9pt; padding: 5px; font-style: italic;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # Buttons
        button_layout = QHBoxLayout()

        skip_btn = QPushButton("Skip (Name Later)")
        skip_btn.setToolTip("Skip naming - you can rename faces later in the People section")
        skip_btn.setStyleSheet("""
            QPushButton {
                background: #5f6368;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #3c4043;
            }
        """)
        skip_btn.clicked.connect(self.reject)
        button_layout.addWidget(skip_btn)

        button_layout.addStretch()

        save_btn = QPushButton("💾 Save Names")
        save_btn.setDefault(True)
        save_btn.setToolTip("Save the names you entered")
        save_btn.setStyleSheet("""
            QPushButton {
                background: #1a73e8;
                color: white;
                padding: 8px 24px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1557b0;
            }
        """)
        save_btn.clicked.connect(self._save_names)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def _create_face_card(self, index: int, face: Dict) -> QWidget:
        """
        Create a card for one face with thumbnail and name input.

        Args:
            index: Face number (0-based)
            face: Dict with 'branch_key' and 'crop_path'

        Returns:
            QWidget card
        """
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 8px;
                padding: 12px;
            }
        """)

        layout = QHBoxLayout(card)
        layout.setSpacing(15)

        # Thumbnail
        thumb_label = QLabel()
        thumb_label.setFixedSize(100, 100)
        thumb_label.setStyleSheet("""
            QLabel {
                background: #f8f9fa;
                border: 2px solid #dadce0;
                border-radius: 4px;
            }
        """)

        if os.path.exists(face['crop_path']):
            try:
                pixmap = QPixmap(face['crop_path'])
                if not pixmap.isNull():
                    thumb_label.setPixmap(pixmap.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    thumb_label.setAlignment(Qt.AlignCenter)
                else:
                    thumb_label.setText("👤")
                    thumb_label.setAlignment(Qt.AlignCenter)
            except Exception as e:
                logger.debug(f"Failed to load thumbnail: {e}")
                thumb_label.setText("👤")
                thumb_label.setAlignment(Qt.AlignCenter)
        else:
            thumb_label.setText("👤")
            thumb_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(thumb_label)

        # Name input section
        name_section = QVBoxLayout()
        name_section.setSpacing(8)

        label = QLabel(f"Face {index + 1}:")
        label.setStyleSheet("font-weight: bold; font-size: 11pt; color: #202124;")
        name_section.addWidget(label)

        name_input = QLineEdit()
        name_input.setPlaceholderText("Enter person's name...")
        name_input.setStyleSheet("""
            QLineEdit {
                padding: 10px;
                border: 2px solid #dadce0;
                border-radius: 4px;
                font-size: 11pt;
                background: white;
            }
            QLineEdit:focus {
                border: 2px solid #1a73e8;
            }
        """)

        # Autocomplete with existing names
        self._setup_autocomplete(name_input)

        # Auto-focus first input
        if index == 0:
            name_input.setFocus()

        name_section.addWidget(name_input)
        self.name_inputs.append(name_input)

        layout.addLayout(name_section, 1)

        return card

    def _setup_autocomplete(self, line_edit: QLineEdit):
        """
        Setup autocomplete with existing person names from database.

        Args:
            line_edit: QLineEdit to attach completer to
        """
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db._connect() as conn:
                cur = conn.cursor()

                # Get distinct person names, excluding Unknown and manual_* entries
                cur.execute("""
                    SELECT DISTINCT label
                    FROM face_branch_reps
                    WHERE label IS NOT NULL
                    AND label != 'Unknown'
                    AND label NOT LIKE 'manual_%'
                    ORDER BY label
                """)
                names = [row[0] for row in cur.fetchall()]

            if names:
                completer = QCompleter(names)
                completer.setCaseSensitivity(Qt.CaseInsensitive)
                completer.setFilterMode(Qt.MatchContains)
                completer.setCompletionMode(QCompleter.PopupCompletion)
                line_edit.setCompleter(completer)

                logger.debug(f"[FaceNamingDialog] Autocomplete set up with {len(names)} existing names")

        except Exception as e:
            logger.debug(f"[FaceNamingDialog] Could not setup autocomplete: {e}")

    def _save_names(self):
        """Save the entered names to database."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            named_count = 0
            skipped_count = 0

            for i, face in enumerate(self.face_data):
                name = self.name_inputs[i].text().strip()

                if name:
                    # Update label (person name) in face_branch_reps table
                    with db._connect() as conn:
                        cur = conn.cursor()

                        cur.execute("""
                            UPDATE face_branch_reps
                            SET label = ?
                            WHERE branch_key = ?
                            AND project_id = ?
                        """, (name, face['branch_key'], self.project_id))

                        if cur.rowcount > 0:
                            named_count += 1
                            logger.info(f"[FaceNamingDialog] Named face '{face['branch_key']}' as '{name}'")
                        else:
                            logger.warning(f"[FaceNamingDialog] Failed to update name for {face['branch_key']}")

                        conn.commit()
                else:
                    skipped_count += 1

            if named_count > 0:
                msg = f"✅ Named {named_count} face(s) successfully!"
                if skipped_count > 0:
                    msg += f"\n\n({skipped_count} face(s) skipped - you can name them later in the People section)"

                QMessageBox.information(
                    self,
                    "Names Saved",
                    msg
                )

            self.accept()

        except Exception as e:
            logger.error(f"[FaceNamingDialog] Failed to save names: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save names:\n{e}"
            )
