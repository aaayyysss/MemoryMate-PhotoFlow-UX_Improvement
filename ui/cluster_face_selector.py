#!/usr/bin/env python3
"""
Cluster Face Selector Dialog - Choose best representative face for a person cluster.

Allows users to select which face should represent a person cluster in the sidebar.
Shows all faces in the cluster sorted by quality score with metrics displayed.

Author: Claude Code
Date: December 16, 2025
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QPushButton, QWidget, QScrollArea, QRadioButton, QButtonGroup,
    QMessageBox
)
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt
import base64
import os
from typing import List, Dict, Optional


class ClusterFaceSelector(QDialog):
    """
    Dialog for selecting the best representative face for a person cluster.

    Features:
    - Shows all faces in cluster sorted by quality
    - Displays quality metrics for each face
    - Highlights current representative
    - Updates database when new representative is selected
    """

    def __init__(
        self,
        project_id: int,
        branch_key: str,
        cluster_name: str,
        current_rep_path: str,
        parent=None
    ):
        """
        Initialize face selector dialog.

        Args:
            project_id: Current project ID
            branch_key: Face cluster branch key
            cluster_name: Display name for the cluster (or "Unnamed")
            current_rep_path: Path to current representative face
            parent: Parent widget
        """
        super().__init__(parent)

        self.project_id = project_id
        self.branch_key = branch_key
        self.cluster_name = cluster_name
        self.current_rep_path = current_rep_path
        self.selected_face_id = None
        self.faces = []

        self.setWindowTitle(f"Choose Representative Face - {cluster_name}")
        self.setModal(True)
        self.resize(900, 700)

        self._load_faces()
        self._create_ui()

    def _load_faces(self):
        """Load all faces for this cluster from database, sorted by quality."""
        from reference_db import ReferenceDB

        db = ReferenceDB()
        with db._connect() as conn:
            cur = conn.cursor()

            # Check if quality_score column exists
            cur.execute("PRAGMA table_info(face_crops)")
            columns = {row[1] for row in cur.fetchall()}
            has_quality_score = 'quality_score' in columns

            # Get all face crops for this cluster with quality scores if available
            if has_quality_score:
                cur.execute("""
                    SELECT
                        fc.id,
                        fc.crop_path,
                        fc.quality_score,
                        fc.is_representative,
                        pm.date_taken,
                        pm.width,
                        pm.height
                    FROM face_crops fc
                    LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
                    WHERE fc.project_id = ? AND fc.branch_key = ?
                    ORDER BY fc.quality_score DESC, fc.id DESC
                """, (self.project_id, self.branch_key))
            else:
                # Fallback: sort by is_representative and id
                cur.execute("""
                    SELECT
                        fc.id,
                        fc.crop_path,
                        0.0 as quality_score,
                        fc.is_representative,
                        pm.date_taken,
                        pm.width,
                        pm.height
                    FROM face_crops fc
                    LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
                    WHERE fc.project_id = ? AND fc.branch_key = ?
                    ORDER BY fc.is_representative DESC, fc.id DESC
                """, (self.project_id, self.branch_key))

            rows = cur.fetchall()

            # If quality scores are missing and column exists, calculate them
            if has_quality_score and rows and all(row[2] is None or row[2] == 0.0 for row in rows):
                self._calculate_missing_quality_scores()
                # Reload after calculation
                cur.execute("""
                    SELECT
                        fc.id,
                        fc.crop_path,
                        fc.quality_score,
                        fc.is_representative,
                        pm.date_taken,
                        pm.width,
                        pm.height
                    FROM face_crops fc
                    LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
                    WHERE fc.project_id = ? AND fc.branch_key = ?
                    ORDER BY fc.quality_score DESC, fc.id DESC
                """, (self.project_id, self.branch_key))
                rows = cur.fetchall()

            self.faces = [
                {
                    'id': row[0],
                    'crop_path': row[1],
                    'quality_score': row[2] or 0.0,
                    'is_representative': bool(row[3]),
                    'date_taken': row[4],
                    'width': row[5] or 0,
                    'height': row[6] or 0
                }
                for row in rows
            ]

    def _calculate_missing_quality_scores(self):
        """Calculate quality scores for faces that don't have them yet."""
        from reference_db import ReferenceDB
        from ui.face_quality_scorer import FaceQualityScorer

        db = ReferenceDB()
        with db._connect() as conn:
            cur = conn.cursor()

            # Get faces without quality scores
            cur.execute("""
                SELECT fc.id, fc.crop_path, pm.date_taken, pm.width, pm.height
                FROM face_crops fc
                LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
                WHERE fc.project_id = ? AND fc.branch_key = ?
                  AND (fc.quality_score IS NULL OR fc.quality_score = 0.0)
            """, (self.project_id, self.branch_key))

            faces_to_score = cur.fetchall()

            for face_id, crop_path, date_taken, width, height in faces_to_score:
                if not os.path.exists(crop_path):
                    continue

                try:
                    # Calculate quality score
                    result = FaceQualityScorer.calculate_overall_quality(
                        image_path=crop_path,
                        width=width or 100,
                        height=height or 100,
                        photo_date=date_taken
                    )

                    # Update database
                    cur.execute("""
                        UPDATE face_crops
                        SET quality_score = ?
                        WHERE id = ?
                    """, (result['overall'], face_id))

                except Exception as e:
                    print(f"[ClusterFaceSelector] Failed to calculate quality for {crop_path}: {e}")
                    continue

            conn.commit()

    def _create_ui(self):
        """Create the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header
        header = QLabel(f"Select the best face to represent {self.cluster_name}")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Info text
        info = QLabel(f"Showing {len(self.faces)} faces sorted by quality (best first)")
        info.setStyleSheet("color: #5f6368;")
        layout.addWidget(info)

        # Scroll area for faces
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #dadce0;
                border-radius: 8px;
                background: white;
            }
        """)

        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(15)

        # Radio button group for selection
        self.button_group = QButtonGroup(self)

        # Create face cards
        for idx, face in enumerate(self.faces):
            card = self._create_face_card(face, idx)
            row = idx // 3
            col = idx % 3
            grid.addWidget(card, row, col)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Set as Representative")
        save_btn.setDefault(True)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #1a73e8;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1557b0;
            }
        """)
        save_btn.clicked.connect(self._save_selection)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def _create_face_card(self, face: Dict, index: int) -> QWidget:
        """Create a card widget for a face."""
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)

        # Highlight current representative
        is_current = face['is_representative'] or face['crop_path'] == self.current_rep_path

        if is_current:
            card.setStyleSheet("""
                QWidget {
                    background: #e8f0fe;
                    border: 2px solid #1a73e8;
                    border-radius: 8px;
                }
            """)
        else:
            card.setStyleSheet("""
                QWidget {
                    background: #f8f9fa;
                    border: 1px solid #dadce0;
                    border-radius: 8px;
                }
                QWidget:hover {
                    background: #f1f3f4;
                }
            """)

        # Face preview
        face_label = QLabel()
        face_label.setFixedSize(200, 200)
        face_label.setAlignment(Qt.AlignCenter)

        if os.path.exists(face['crop_path']):
            pixmap = QPixmap(face['crop_path'])
            if not pixmap.isNull():
                face_label.setPixmap(
                    pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )

        card_layout.addWidget(face_label)

        # Quality score
        quality_score = face['quality_score']
        from ui.face_quality_scorer import FaceQualityScorer
        icon, percent_text = FaceQualityScorer.get_quality_badge_text(quality_score)

        quality_label = QLabel(f"{icon} Quality: {percent_text}")
        quality_label.setAlignment(Qt.AlignCenter)
        quality_font = QFont()
        quality_font.setPointSize(10)
        quality_font.setBold(True)
        quality_label.setFont(quality_font)
        card_layout.addWidget(quality_label)

        # Current representative indicator
        if is_current:
            current_label = QLabel("★ Current Representative")
            current_label.setAlignment(Qt.AlignCenter)
            current_label.setStyleSheet("color: #1a73e8; font-weight: bold;")
            card_layout.addWidget(current_label)

        # Radio button for selection
        radio = QRadioButton(f"Use this face (#{index + 1})")
        radio.setProperty("face_id", face['id'])
        radio.setProperty("crop_path", face['crop_path'])

        if is_current:
            radio.setChecked(True)
            self.selected_face_id = face['id']

        self.button_group.addButton(radio)
        radio.toggled.connect(lambda checked, fid=face['id']: self._on_selection_changed(fid, checked))
        card_layout.addWidget(radio)

        return card

    def _on_selection_changed(self, face_id: int, checked: bool):
        """Handle radio button selection change."""
        if checked:
            self.selected_face_id = face_id

    def _save_selection(self):
        """Save the selected face as the new representative."""
        if self.selected_face_id is None:
            QMessageBox.warning(self, "No Selection", "Please select a face first.")
            return

        try:
            from reference_db import ReferenceDB

            # Find selected face
            selected_face = next((f for f in self.faces if f['id'] == self.selected_face_id), None)
            if not selected_face:
                QMessageBox.warning(self, "Error", "Selected face not found.")
                return

            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()

                # Clear old representative flag
                cur.execute("""
                    UPDATE face_crops
                    SET is_representative = 0
                    WHERE project_id = ? AND branch_key = ?
                """, (self.project_id, self.branch_key))

                # Set new representative
                cur.execute("""
                    UPDATE face_crops
                    SET is_representative = 1
                    WHERE id = ?
                """, (self.selected_face_id,))

                # Update face_branch_reps with new representative
                # Load thumbnail
                rep_thumb_png = None
                if os.path.exists(selected_face['crop_path']):
                    try:
                        with open(selected_face['crop_path'], 'rb') as f:
                            rep_thumb_png = f.read()
                    except Exception:
                        pass

                cur.execute("""
                    UPDATE face_branch_reps
                    SET rep_path = ?, rep_thumb_png = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (selected_face['crop_path'], rep_thumb_png, self.project_id, self.branch_key))

                conn.commit()

            QMessageBox.information(
                self,
                "Success",
                f"Representative face updated for {self.cluster_name}"
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to update representative face:\n{e}"
            )


if __name__ == '__main__':
    # Test dialog
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Example usage (requires valid database)
    dialog = ClusterFaceSelector(
        project_id=1,
        branch_key="face_001",
        cluster_name="John Smith",
        current_rep_path="/path/to/current/face.jpg"
    )

    if dialog.exec():
        print("✅ Representative face updated")
    else:
        print("❌ Selection cancelled")

    sys.exit(0)
