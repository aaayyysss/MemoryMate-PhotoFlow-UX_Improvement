#!/usr/bin/env python3
"""
Face Crop Editor - Manual face detection review and correction tool.

Allows users to:
- View original photos with detected face rectangles overlaid
- See which faces were automatically detected
- Manually draw rectangles around missed faces
- Correct or delete incorrect face detections
- Save new face crops to database

Best practice: Allow users to review and correct automated detections.

Author: Claude Code
Date: December 17, 2025
"""

import logging
import os
import io
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from PIL import Image, ImageOps
import numpy as np
import cv2

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QMessageBox, QCheckBox, QSpinBox,
    QGroupBox, QTextEdit, QFrame, QRadioButton, QProgressDialog,
    QApplication
)
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QFont
from PySide6.QtCore import Qt, QRect, QPoint, Signal

from reference_db import ReferenceDB

logger = logging.getLogger(__name__)


class FaceCropEditor(QDialog):
    """
    Dialog for reviewing and manually correcting face detections.

    Shows original photo with face rectangles and allows manual additions/corrections.
    """

    faceCropsUpdated = Signal()  # Emitted when face crops are modified

    def __init__(self, photo_path: str, project_id: int, parent=None):
        """
        Initialize face crop editor.

        Args:
            photo_path: Path to the photo to review
            project_id: Current project ID
            parent: Parent widget
        """
        try:
            logger.info(f"[FaceCropEditor] Initializing Face Crop Editor for: {photo_path}")

            # CRITICAL VALIDATION: Check if user is trying to open a face crop instead of original photo
            # Face crops are stored in /face_crops/ directory and should not be manually cropped again
            if '/face_crops/' in photo_path.replace('\\', '/'):
                error_msg = (
                    "Cannot open Face Crop Editor on a face crop image.\n\n"
                    "The Manual Face Crop Editor is designed to work with original photos only.\n\n"
                    "To manually crop faces:\n"
                    "1. Go to the main photo timeline\n"
                    "2. Right-click on an original photo\n"
                    "3. Select 'Manual Face Crop'\n\n"
                    f"Current path (face crop): {os.path.basename(photo_path)}"
                )
                logger.error(f"[FaceCropEditor] Attempted to open face crop image: {photo_path}")
                raise ValueError(error_msg)

            super().__init__(parent)
            logger.info(f"[FaceCropEditor] ✓ QDialog parent class initialized")

            self.photo_path = photo_path
            self.project_id = project_id
            self.detected_faces = []  # Existing face detections
            self.manual_faces = []  # Manually added faces
            self.faces_were_saved = False  # Flag to indicate if manual faces were successfully saved

            photo_name = os.path.basename(photo_path)
            self.setWindowTitle(f"Face Crop Editor - {photo_name}")
            self.setModal(True)
            self.resize(1200, 800)
            logger.info(f"[FaceCropEditor] ✓ Dialog properties set")

            logger.info(f"[FaceCropEditor] Loading existing faces from database...")
            self._load_existing_faces()
            logger.info(f"[FaceCropEditor] ✓ Existing faces loaded ({len(self.detected_faces)} faces)")

            logger.info(f"[FaceCropEditor] Creating UI...")
            self._create_ui()
            logger.info(f"[FaceCropEditor] ✓ UI created successfully")

            logger.info(f"[FaceCropEditor] ✓ Face Crop Editor initialized successfully")

        except Exception as e:
            logger.error(f"[FaceCropEditor] CRITICAL ERROR during initialization: {e}", exc_info=True)
            import traceback
            traceback.print_exc()
            raise  # Re-raise to prevent partial initialization

    def _load_existing_faces(self):
        """Load existing face detections with bounding boxes for this photo (supports both old and new schema)."""
        db = ReferenceDB()

        try:
            with db._connect() as conn:
                cur = conn.cursor()

                # Check which columns exist in face_crops table
                cur.execute("PRAGMA table_info(face_crops)")
                columns = {row[1] for row in cur.fetchall()}

                # Support both schema versions:
                # - Old schema: bbox_x, bbox_y, bbox_w, bbox_h (4 separate INTEGER columns)
                # - New schema: bbox (single TEXT column with comma-separated values)
                # - quality_score may or may not exist
                has_bbox_text = 'bbox' in columns
                has_bbox_separate = all(col in columns for col in ['bbox_x', 'bbox_y', 'bbox_w', 'bbox_h'])
                has_quality_score = 'quality_score' in columns

                # Build query based on available columns
                if has_bbox_text:
                    # New schema: single bbox TEXT column
                    quality_select = "fc.quality_score" if has_quality_score else "0.0 as quality_score"
                    query = f"""
                        SELECT
                            fc.id,
                            fc.branch_key,
                            fc.crop_path,
                            fc.bbox,
                            {quality_select},
                            fbr.label as person_name,
                            'text' as bbox_format
                        FROM face_crops fc
                        LEFT JOIN face_branch_reps fbr ON fc.branch_key = fbr.branch_key
                            AND fc.project_id = fbr.project_id
                        WHERE fc.image_path = ? AND fc.project_id = ?
                    """
                elif has_bbox_separate:
                    # Old schema: 4 separate bbox columns
                    quality_select = "fc.quality_score" if has_quality_score else "0.0 as quality_score"
                    query = f"""
                        SELECT
                            fc.id,
                            fc.branch_key,
                            fc.crop_path,
                            (CAST(fc.bbox_x AS TEXT) || ',' ||
                             CAST(fc.bbox_y AS TEXT) || ',' ||
                             CAST(fc.bbox_w AS TEXT) || ',' ||
                             CAST(fc.bbox_h AS TEXT)) as bbox,
                            {quality_select},
                            fbr.label as person_name,
                            'separate' as bbox_format
                        FROM face_crops fc
                        LEFT JOIN face_branch_reps fbr ON fc.branch_key = fbr.branch_key
                            AND fc.project_id = fbr.project_id
                        WHERE fc.image_path = ? AND fc.project_id = ?
                            AND fc.bbox_x IS NOT NULL
                    """
                else:
                    # No bbox columns: fallback mode
                    quality_select = "fc.quality_score" if has_quality_score else "0.0 as quality_score"
                    query = f"""
                        SELECT
                            fc.id,
                            fc.branch_key,
                            fc.crop_path,
                            NULL as bbox,
                            {quality_select},
                            fbr.label as person_name,
                            'none' as bbox_format
                        FROM face_crops fc
                        LEFT JOIN face_branch_reps fbr ON fc.branch_key = fbr.branch_key
                            AND fc.project_id = fbr.project_id
                        WHERE fc.image_path = ? AND fc.project_id = ?
                    """

                cur.execute(query, (self.photo_path, self.project_id))
                rows = cur.fetchall()
                self.detected_faces = []

                for row in rows:
                    face_id, branch_key, crop_path, bbox_str, quality_score, person_name, bbox_format = row

                    # Parse bounding box if available
                    bbox = None
                    if bbox_str:
                        try:
                            parts = bbox_str.split(',')
                            if len(parts) == 4:
                                bbox = tuple(map(float, parts))  # (x, y, w, h)
                        except Exception as e:
                            logger.debug(f"[FaceCropEditor] Failed to parse bbox '{bbox_str}': {e}")

                    self.detected_faces.append({
                        'id': face_id,
                        'branch_key': branch_key,
                        'crop_path': crop_path,
                        'bbox': bbox,
                        'quality_score': quality_score or 0.0,
                        'person_name': person_name or "Unnamed",
                        'is_existing': True
                    })

                faces_with_bbox = sum(1 for f in self.detected_faces if f['bbox'])

                if faces_with_bbox > 0:
                    logger.info(f"[FaceCropEditor] Found {len(self.detected_faces)} existing face(s), {faces_with_bbox} with bounding boxes (schema: {bbox_format})")
                else:
                    logger.warning(f"[FaceCropEditor] Found {len(self.detected_faces)} existing face(s), but no bbox data available (green rectangles will not be shown)")

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to load existing faces: {e}")

    def _create_ui(self):
        """Create the editor UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header
        header = QLabel(f"Face Detection Review - {os.path.basename(self.photo_path)}")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Info panel
        info_layout = QHBoxLayout()

        # Instructions
        instructions = QGroupBox("ℹ️ Instructions")
        instructions_layout = QVBoxLayout()

        # Check if we can show detected face rectangles
        has_bboxes = any(f.get('bbox') for f in self.detected_faces)

        if has_bboxes:
            instruction_text = (
                "• GREEN rectangles = Auto-detected faces\n"
                "• RED rectangles = Manually added faces\n"
                "• Click 'Add Manual Face' to draw new rectangles\n"
                "• Drag on the photo to draw a rectangle\n"
                "• Review detected faces in gallery below\n"
                "• Save when done to update the database"
            )
        else:
            instruction_text = (
                "• RED rectangles = Manually added faces\n"
                "• Click 'Add Manual Face' to draw new rectangles\n"
                "• Drag on the photo to draw a rectangle\n"
                "• Review detected faces in gallery below\n"
                "• Save when done to update the database\n"
                "ℹ️ Note: Green rectangles not available (bbox data missing)"
            )

        instructions_text = QLabel(instruction_text)
        instructions_text.setStyleSheet("color: #5f6368; font-size: 9pt;")
        instructions_layout.addWidget(instructions_text)
        instructions.setLayout(instructions_layout)
        info_layout.addWidget(instructions)

        # Statistics
        stats_group = QGroupBox("📊 Detection Stats")
        stats_layout = QVBoxLayout()

        detected_count = len(self.detected_faces)
        stats_layout.addWidget(QLabel(f"Already Detected: {detected_count} face(s)"))

        if self.detected_faces:
            people_list = set(f['person_name'] for f in self.detected_faces)
            stats_layout.addWidget(QLabel(f"People: {', '.join(list(people_list)[:3])}..."))

        self.manual_count_label = QLabel(f"Manual Additions: 0")
        stats_layout.addWidget(self.manual_count_label)

        stats_group.setLayout(stats_layout)
        info_layout.addWidget(stats_group)

        layout.addLayout(info_layout)

        # Photo viewer with face rectangles
        self.photo_viewer = FacePhotoViewer(
            self.photo_path,
            self.detected_faces,
            self.manual_faces
        )
        self.photo_viewer.manualFaceAdded.connect(self._on_manual_face_added)
        layout.addWidget(self.photo_viewer, 1)

        # Face gallery - show all detected faces below the photo
        if self.detected_faces:
            gallery_group = QGroupBox(f"📸 Detected Faces ({len(self.detected_faces)})")
            gallery_layout = QVBoxLayout()

            # Scrollable area for face thumbnails
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(140)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setFrameShape(QScrollArea.NoFrame)

            # Container for face cards in horizontal layout
            gallery_container = QWidget()
            gallery_h_layout = QHBoxLayout(gallery_container)
            gallery_h_layout.setContentsMargins(5, 5, 5, 5)
            gallery_h_layout.setSpacing(10)

            # Create face card for each detected face
            for face in self.detected_faces:
                face_card = self._create_face_card(face)
                gallery_h_layout.addWidget(face_card)

            gallery_h_layout.addStretch()
            scroll.setWidget(gallery_container)
            gallery_layout.addWidget(scroll)

            gallery_group.setLayout(gallery_layout)
            layout.addWidget(gallery_group)

        # Action buttons
        button_layout = QHBoxLayout()

        # Drawing mode buttons
        add_manual_btn = QPushButton("➕ Start Drawing")
        add_manual_btn.setToolTip("Click to enable drawing mode, then drag on the photo to draw face rectangles")
        add_manual_btn.setStyleSheet("""
            QPushButton {
                background: #34a853;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2d8b47;
            }
        """)
        add_manual_btn.clicked.connect(self.photo_viewer.enable_drawing_mode)
        button_layout.addWidget(add_manual_btn)

        # ENHANCEMENT #1: Done Drawing button (hidden until drawing mode enabled)
        self.done_drawing_btn = QPushButton("✓ Done Drawing")
        self.done_drawing_btn.setToolTip("Exit drawing mode")
        self.done_drawing_btn.setStyleSheet("""
            QPushButton {
                background: #5f6368;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #3c4043;
            }
        """)
        self.done_drawing_btn.clicked.connect(self.photo_viewer.disable_drawing_mode)
        self.done_drawing_btn.setVisible(False)  # Hidden initially
        button_layout.addWidget(self.done_drawing_btn)

        # ENHANCEMENT #5: Undo button
        self.undo_btn = QPushButton("↶ Undo")
        self.undo_btn.setToolTip("Remove last manual face rectangle")
        self.undo_btn.setStyleSheet("""
            QPushButton {
                background: #fbbc04;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #f9ab00;
            }
            QPushButton:disabled {
                background: #dadce0;
                color: #80868b;
            }
        """)
        self.undo_btn.clicked.connect(self._undo_last_face)
        self.undo_btn.setEnabled(False)  # Disabled until faces drawn
        button_layout.addWidget(self.undo_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("💾 Save Changes")
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
        save_btn.clicked.connect(self._save_changes)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

        # Connect drawing mode signal to update button visibility
        self.photo_viewer.drawingModeChanged.connect(self._on_drawing_mode_changed)

    def _create_face_card(self, face: Dict) -> QWidget:
        """Create a thumbnail card for a detected face."""
        card = QFrame()
        card.setFixedSize(100, 120)
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
            }
            QFrame:hover {
                border: 2px solid #1a73e8;
            }
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        # Face thumbnail
        thumb_label = QLabel()
        thumb_label.setFixedSize(90, 90)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setStyleSheet("background: #f8f9fa; border-radius: 3px;")

        # Load thumbnail from crop_path
        if os.path.exists(face['crop_path']):
            try:
                # Load with PIL to apply EXIF rotation and ensure correct color mode
                with Image.open(face['crop_path']) as img:
                    # Apply EXIF auto-rotation
                    img = ImageOps.exif_transpose(img)

                    # Convert to RGB if needed (fixes grey/wrong color issues)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    # Convert PIL image to QPixmap
                    data = img.tobytes("raw", "RGB")
                    qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(qimg)

                    if not pixmap.isNull():
                        # Scale with aspect ratio preserved (fixes stretching)
                        thumb_label.setPixmap(pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    else:
                        thumb_label.setText("👤")
            except Exception as e:
                logger.debug(f"[FaceCropEditor] Failed to load thumbnail: {e}")
                thumb_label.setText("👤")
        else:
            thumb_label.setText("👤")

        layout.addWidget(thumb_label)

        # Person name
        name = face['person_name']
        if len(name) > 12:
            name = name[:9] + "..."
        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-size: 9pt; font-weight: 600; color: #202124;")
        layout.addWidget(name_label)

        # Quality score badge
        quality = face.get('quality_score', 0.0)
        if quality >= 0.75:
            quality_badge = QLabel(f"✅ {int(quality*100)}%")
            quality_badge.setStyleSheet("color: #34a853; font-size: 8pt;")
        elif quality >= 0.5:
            quality_badge = QLabel(f"⚠️ {int(quality*100)}%")
            quality_badge.setStyleSheet("color: #fbbc04; font-size: 8pt;")
        else:
            quality_badge = QLabel(f"❓ {int(quality*100)}%")
            quality_badge.setStyleSheet("color: #ea4335; font-size: 8pt;")

        quality_badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(quality_badge)

        return card

    def _on_manual_face_added(self, bbox: Tuple[int, int, int, int]):
        """Handle a manually added face rectangle."""
        self.manual_faces.append({
            'bbox': bbox,
            'is_existing': False
        })

        self.manual_count_label.setText(f"Manual Faces: {len(self.manual_faces)}")

        # ENHANCEMENT #5: Enable undo button when faces are added
        self.undo_btn.setEnabled(True)

        logger.info(f"[FaceCropEditor] Added manual face: {bbox}")

    def _on_drawing_mode_changed(self, enabled: bool):
        """Handle drawing mode state changes."""
        # ENHANCEMENT #1: Show/hide Done Drawing button based on drawing mode
        self.done_drawing_btn.setVisible(enabled)
        logger.debug(f"[FaceCropEditor] Drawing mode changed: {enabled}")

    def _undo_last_face(self):
        """ENHANCEMENT #5: Remove the last manually added face."""
        if self.manual_faces:
            removed_face = self.manual_faces.pop()
            self.manual_count_label.setText(f"Manual Faces: {len(self.manual_faces)}")

            # Disable undo button if no more manual faces
            if not self.manual_faces:
                self.undo_btn.setEnabled(False)

            # Refresh the photo viewer to remove the rectangle overlay
            self.photo_viewer.manual_faces = self.manual_faces
            self.photo_viewer.update()

            logger.info(f"[FaceCropEditor] Undid manual face: {removed_face['bbox']}")
        else:
            logger.debug("[FaceCropEditor] No manual faces to undo")

    def _save_changes(self):
        """Save manually added face crops to database."""
        if not self.manual_faces:
            QMessageBox.information(
                self,
                "No Changes",
                "No manual face rectangles were added.\n\nClick '➕ Start Drawing' to draw rectangles around missed faces."
            )
            return

        # ENHANCEMENT #2: Show progress dialog
        from PySide6.QtWidgets import QProgressDialog, QApplication

        progress = QProgressDialog(
            "Preparing to save faces...",
            None,  # No cancel button
            0,
            len(self.manual_faces) + 1,  # +1 for completion step
            self
        )
        progress.setWindowTitle("Saving Faces")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)  # Show immediately
        progress.setValue(0)

        try:
            # Create face crops from manual rectangles
            saved_count = 0
            saved_crop_paths = []  # Store for later display

            for i, manual_face in enumerate(self.manual_faces):
                progress.setLabelText(f"Saving face {i+1}/{len(self.manual_faces)}...")
                progress.setValue(i)
                QApplication.processEvents()  # Update UI

                bbox = manual_face['bbox']
                x, y, w, h = bbox

                # ENHANCEMENT: Refine manual bbox using face detection (Best Practice)
                # User's rectangle defines search region, detection refines for consistent padding
                refined_result = self._refine_manual_bbox_with_detection(x, y, w, h)
                (refined_x, refined_y, refined_w, refined_h), face_data = refined_result

                # Check if refinement actually changed the bbox
                was_refined = (x, y, w, h) != (refined_x, refined_y, refined_w, refined_h)

                # ENHANCEMENT #4: Show before/after preview for user feedback
                user_accepts = self._show_refinement_preview(
                    original_bbox=(x, y, w, h),
                    refined_bbox=(refined_x, refined_y, refined_w, refined_h),
                    was_refined=was_refined
                )

                # Skip this face if user rejected the preview
                if not user_accepts:
                    logger.info(f"[FaceCropEditor] User skipped face {i+1}/{len(self.manual_faces)}")
                    continue

                # Crop face from original image (using refined coordinates)
                crop_path = self._create_face_crop(refined_x, refined_y, refined_w, refined_h)

                if crop_path:
                    # ENHANCEMENT #3: Quality Assessment
                    # Only assess quality if we have face detection data
                    if face_data:
                        logger.debug(f"[FaceCropEditor] Assessing quality for face {i+1}/{len(self.manual_faces)}")
                        quality_report = self._assess_face_quality(crop_path, face_data)

                        # Show warning if quality is low
                        if not quality_report['is_acceptable']:
                            logger.warning(f"[FaceCropEditor] Low quality detected (score: {quality_report['overall_score']:.0f}/100)")
                            user_accepts_quality = self._show_quality_warning(quality_report, crop_path)

                            if not user_accepts_quality:
                                logger.info(f"[FaceCropEditor] User skipped low-quality face {i+1}/{len(self.manual_faces)}")
                                # Delete the crop file
                                try:
                                    os.unlink(crop_path)
                                    logger.debug(f"[FaceCropEditor] Deleted rejected crop: {crop_path}")
                                except Exception as del_err:
                                    logger.warning(f"[FaceCropEditor] Failed to delete crop: {del_err}")
                                continue  # Skip to next face
                            else:
                                logger.info(f"[FaceCropEditor] User accepted low-quality face (score: {quality_report['overall_score']:.0f}/100)")
                        else:
                            logger.info(f"[FaceCropEditor] Quality check passed (score: {quality_report['overall_score']:.0f}/100)")
                    else:
                        logger.debug(f"[FaceCropEditor] Skipping quality assessment (no face detection data)")

                    # Add to database (with refined bbox, not original manual bbox)
                    refined_bbox = (refined_x, refined_y, refined_w, refined_h)
                    branch_key = self._add_face_to_database(crop_path, refined_bbox)

                    # ENHANCEMENT #5: Auto-Clustering
                    # Check if this face looks similar to existing faces
                    merged_with_existing = False

                    # Extract embedding for similarity comparison
                    logger.debug(f"[FaceCropEditor] Extracting embedding for similarity check...")
                    embedding = self._extract_embedding(crop_path)

                    if embedding is not None:
                        # Find similar existing faces
                        similar_faces = self._find_similar_faces(embedding, threshold=0.6)

                        if similar_faces:
                            logger.info(f"[FaceCropEditor] Found {len(similar_faces)} similar faces - showing merge suggestion")

                            # Show merge suggestion dialog
                            target_branch_key = self._show_merge_suggestion_dialog(crop_path, similar_faces)

                            if target_branch_key:
                                # User chose to merge with existing person
                                merge_success = self._merge_face_with_existing(branch_key, target_branch_key)

                                if merge_success:
                                    merged_with_existing = True
                                    logger.info(f"[FaceCropEditor] ✅ Face merged with existing person: {target_branch_key}")

                                    # Note: Don't add to saved_crop_paths since it's already merged
                                    # The naming dialog won't be shown for this face
                                else:
                                    logger.warning(f"[FaceCropEditor] Merge failed - will show naming dialog instead")
                            else:
                                logger.info(f"[FaceCropEditor] User chose to keep as new person")
                        else:
                            logger.debug(f"[FaceCropEditor] No similar faces found - keeping as new person")

                        # Only add to saved_crop_paths if NOT merged (will show naming dialog)
                        if not merged_with_existing:
                            saved_crop_paths.append({
                                'crop_path': crop_path,
                                'branch_key': branch_key
                            })

                        saved_count += 1

                    else:
                        # CRITICAL FIX: If embedding extraction fails, DELETE the face crop
                        # and show error to user. Do NOT save face without embedding!
                        logger.error(f"[FaceCropEditor] ❌ Failed to extract embedding for {crop_path}")
                        logger.error(f"[FaceCropEditor] ❌ Face crop quality too low or no face detected - discarding")

                        # Delete the corrupted face from database
                        try:
                            from reference_db import ReferenceDB
                            db = ReferenceDB()
                            db.delete_face_crop(branch_key)
                            logger.info(f"[FaceCropEditor] ✓ Deleted corrupted face crop: {branch_key}")

                            # Delete the crop file
                            if os.path.exists(crop_path):
                                os.remove(crop_path)
                                logger.info(f"[FaceCropEditor] ✓ Deleted crop file: {crop_path}")
                        except Exception as cleanup_err:
                            logger.error(f"[FaceCropEditor] Failed to clean up corrupted face: {cleanup_err}")

                        # Show error to user
                        from PySide6.QtWidgets import QMessageBox
                        QMessageBox.warning(
                            self,
                            "Face Crop Failed",
                            f"Failed to process face crop:\n\n"
                            f"• No face detected in the cropped region\n"
                            f"• The crop quality is too low\n"
                            f"• Try drawing a tighter rectangle around the face\n\n"
                            f"This face was NOT saved."
                        )

                        # Do NOT increment saved_count
                        # Do NOT add to saved_crop_paths
                        logger.warning(f"[FaceCropEditor] ⚠️ Skipped saving face without embedding")

            progress.setLabelText("Finalizing...")
            progress.setValue(len(self.manual_faces))
            QApplication.processEvents()

            progress.close()

            if saved_count > 0:
                # Set flag to indicate faces were saved
                # Caller can check this flag after exec() returns to refresh UI
                self.faces_were_saved = True

                # ENHANCEMENT #4: Enhanced success dialog with thumbnails
                # Extract just the crop paths for display
                crop_paths_only = [item['crop_path'] for item in saved_crop_paths]
                self._show_success_dialog(saved_count, crop_paths_only)

                # ENHANCEMENT #3: Show naming dialog after success
                # saved_crop_paths now contains dicts with 'crop_path' and 'branch_key'
                if saved_crop_paths:
                    from ui.face_naming_dialog import FaceNamingDialog

                    naming_dialog = FaceNamingDialog(
                        face_data=saved_crop_paths,  # Already has the right format
                        project_id=self.project_id,
                        parent=self.parent()
                    )
                    naming_dialog.exec()

                # CRITICAL FIX: Don't emit signal - let caller check faces_were_saved flag
                # Emitting signal causes threading issues when dialog is being destroyed
                # Caller will refresh UI after dialog closes by checking the flag
                try:
                    self.accept()
                except RuntimeError as e:
                    logger.debug(f"[FaceCropEditor] Dialog already closed: {e}")

                logger.info(f"[FaceCropEditor] Saved {saved_count} manual face(s), set faces_were_saved=True")
            else:
                QMessageBox.warning(
                    self,
                    "Save Failed",
                    "Failed to save face crops. Please try again."
                )

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to save changes: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save face crops:\n{e}"
            )

    def _show_success_dialog(self, saved_count: int, crop_paths: List[str]):
        """
        ENHANCEMENT #4: Show enhanced success dialog with face thumbnails.

        Args:
            saved_count: Number of faces saved
            crop_paths: Paths to saved face crop images
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Faces Saved Successfully")
        dialog.setModal(True)
        dialog.setMinimumWidth(500)

        layout = QVBoxLayout(dialog)

        # Success header
        header = QLabel(f"✅ Successfully saved {saved_count} face(s)!")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; color: #34a853; padding: 10px;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Show thumbnails (max 5)
        if crop_paths:
            thumbs_container = QWidget()
            thumbs_layout = QHBoxLayout(thumbs_container)
            thumbs_layout.setSpacing(10)

            for crop_path in crop_paths[:5]:  # Show max 5 thumbnails
                if os.path.exists(crop_path):
                    try:
                        thumb_label = QLabel()
                        thumb_label.setFixedSize(80, 80)
                        thumb_label.setStyleSheet("""
                            QLabel {
                                background: #f8f9fa;
                                border: 2px solid #dadce0;
                                border-radius: 4px;
                            }
                        """)

                        pixmap = QPixmap(crop_path)
                        if not pixmap.isNull():
                            thumb_label.setPixmap(pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                            thumb_label.setAlignment(Qt.AlignCenter)
                            thumbs_layout.addWidget(thumb_label)
                    except Exception as e:
                        logger.debug(f"Failed to load thumbnail: {e}")

            thumbs_layout.addStretch()
            layout.addWidget(thumbs_container)

        # Info message
        info_text = (
            "These faces will appear in the People section.\n\n"
            "💡 Tip: Drag and drop faces in the People section to merge them into one person."
        )
        info_label = QLabel(info_text)
        info_label.setStyleSheet("color: #5f6368; padding: 10px;")
        info_label.setWordWrap(True)
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("""
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
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        dialog.exec()

    def _show_refinement_preview(self, original_bbox: Tuple[int, int, int, int],
                                  refined_bbox: Tuple[int, int, int, int],
                                  was_refined: bool) -> bool:
        """
        ENHANCEMENT #4: Show before/after preview of face refinement.

        Displays visual comparison to educate user and build confidence in AI refinement.

        Args:
            original_bbox: User's manually drawn rectangle (x, y, w, h)
            refined_bbox: AI-refined bbox (x, y, w, h)
            was_refined: Whether refinement actually changed the bbox

        Returns:
            True if user accepts, False to skip this face
        """
        from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap, QImage

        try:
            # Load image
            with Image.open(self.photo_path) as img:
                img = ImageOps.exif_transpose(img)

                # Create dialog
                dialog = QDialog(self)
                dialog.setWindowTitle("Face Crop Preview")
                dialog.setModal(True)
                dialog.setMinimumWidth(600)

                layout = QVBoxLayout(dialog)
                layout.setSpacing(16)
                layout.setContentsMargins(20, 20, 20, 20)

                # Header
                if was_refined:
                    header = QLabel("✨ AI refined your selection for better recognition")
                    header.setStyleSheet("font-size: 12pt; font-weight: bold; color: #1a73e8; padding: 8px;")
                else:
                    header = QLabel("ℹ️ Preview of your face selection")
                    header.setStyleSheet("font-size: 12pt; font-weight: bold; color: #5f6368; padding: 8px;")
                header.setAlignment(Qt.AlignCenter)
                layout.addWidget(header)

                # Before/After containers
                comparison_layout = QHBoxLayout()
                comparison_layout.setSpacing(20)

                # BEFORE (original rectangle)
                before_container = QWidget()
                before_layout = QVBoxLayout(before_container)
                before_layout.setSpacing(8)

                before_label = QLabel("Your Rectangle:")
                before_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #5f6368;")
                before_label.setAlignment(Qt.AlignCenter)
                before_layout.addWidget(before_label)

                x1, y1, w1, h1 = original_bbox
                before_crop = img.crop((x1, y1, x1 + w1, y1 + h1))
                before_pixmap = self._pil_to_qpixmap(before_crop)
                before_img = QLabel()
                before_img.setPixmap(before_pixmap.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                before_img.setAlignment(Qt.AlignCenter)
                before_img.setStyleSheet("border: 2px solid #dadce0; background: #f8f9fa; padding: 8px;")
                before_layout.addWidget(before_img)

                before_size = QLabel(f"{w1} × {h1} pixels")
                before_size.setStyleSheet("font-size: 9pt; color: #5f6368;")
                before_size.setAlignment(Qt.AlignCenter)
                before_layout.addWidget(before_size)

                comparison_layout.addWidget(before_container)

                # Arrow
                if was_refined:
                    arrow = QLabel("→")
                    arrow.setStyleSheet("font-size: 24pt; color: #1a73e8; font-weight: bold;")
                    arrow.setAlignment(Qt.AlignCenter)
                    comparison_layout.addWidget(arrow)

                # AFTER (refined with smart padding)
                after_container = QWidget()
                after_layout = QVBoxLayout(after_container)
                after_layout.setSpacing(8)

                if was_refined:
                    after_label = QLabel("AI Refined + Smart Padding:")
                    after_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #1a73e8;")
                else:
                    after_label = QLabel("With Smart Padding:")
                    after_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #5f6368;")
                after_label.setAlignment(Qt.AlignCenter)
                after_layout.addWidget(after_label)

                # Apply smart padding to refined bbox for preview
                x2, y2, w2, h2 = refined_bbox
                pad_w = int(w2 * 0.30)
                pad_h = int(h2 * 0.30)
                crop_x1 = max(0, x2 - pad_w)
                crop_y1 = max(0, y2 - pad_h)
                crop_x2 = min(img.width, x2 + w2 + pad_w)
                crop_y2 = min(img.height, y2 + h2 + int(pad_h * 1.5))

                after_crop = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                after_pixmap = self._pil_to_qpixmap(after_crop)
                after_img = QLabel()
                after_img.setPixmap(after_pixmap.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                after_img.setAlignment(Qt.AlignCenter)

                if was_refined:
                    after_img.setStyleSheet("border: 2px solid #1a73e8; background: #e8f0fe; padding: 8px;")
                else:
                    after_img.setStyleSheet("border: 2px solid #dadce0; background: #f8f9fa; padding: 8px;")
                after_layout.addWidget(after_img)

                after_size = QLabel(f"{crop_x2-crop_x1} × {crop_y2-crop_y1} pixels (+30% padding)")
                after_size.setStyleSheet("font-size: 9pt; color: #5f6368;")
                after_size.setAlignment(Qt.AlignCenter)
                after_layout.addWidget(after_size)

                comparison_layout.addWidget(after_container)

                layout.addLayout(comparison_layout)

                # Info message
                info_text = QLabel()
                if was_refined:
                    info_text.setText(
                        "💡 AI detected the face and adjusted the crop for:\n"
                        "   • Better face alignment\n"
                        "   • Consistent padding (includes shoulders)\n"
                        "   • Improved recognition accuracy"
                    )
                    info_text.setStyleSheet("background: #e8f0fe; padding: 12px; border-radius: 6px; color: #1967d2; font-size: 9pt;")
                else:
                    info_text.setText(
                        "💡 Smart padding added (industry standard):\n"
                        "   • 30% padding around face\n"
                        "   • Extra space below for shoulders\n"
                        "   • More professional appearance"
                    )
                    info_text.setStyleSheet("background: #f8f9fa; padding: 12px; border-radius: 6px; color: #5f6368; font-size: 9pt;")
                layout.addWidget(info_text)

                # Buttons
                button_layout = QHBoxLayout()
                button_layout.addStretch()

                accept_btn = QPushButton("✓ Looks Good")
                accept_btn.setStyleSheet("""
                    QPushButton {
                        background: #1a73e8;
                        color: white;
                        border: none;
                        padding: 10px 24px;
                        border-radius: 6px;
                        font-size: 10pt;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background: #1557b0;
                    }
                """)
                accept_btn.setDefault(True)
                accept_btn.clicked.connect(dialog.accept)
                button_layout.addWidget(accept_btn)

                skip_btn = QPushButton("Skip This Face")
                skip_btn.setStyleSheet("""
                    QPushButton {
                        background: #f1f3f4;
                        color: #5f6368;
                        border: 1px solid #dadce0;
                        padding: 10px 24px;
                        border-radius: 6px;
                        font-size: 10pt;
                    }
                    QPushButton:hover {
                        background: #e8eaed;
                    }
                """)
                skip_btn.clicked.connect(dialog.reject)
                button_layout.addWidget(skip_btn)

                button_layout.addStretch()
                layout.addLayout(button_layout)

                # Show dialog
                result = dialog.exec()
                return result == QDialog.Accepted

        except Exception as e:
            logger.error(f"[FaceCropEditor] Error showing refinement preview: {e}", exc_info=True)
            # If preview fails, accept by default (don't block the save)
            return True

    def _pil_to_qpixmap(self, pil_image: Image.Image) -> QPixmap:
        """Convert PIL Image to QPixmap for Qt display."""
        from PySide6.QtGui import QPixmap, QImage

        # Convert to RGB if needed
        if pil_image.mode != 'RGB':
            pil_image = pil_image.convert('RGB')

        # Convert PIL to QImage
        data = pil_image.tobytes("raw", "RGB")
        qimg = QImage(data, pil_image.width, pil_image.height, pil_image.width * 3, QImage.Format_RGB888)

        # Convert QImage to QPixmap
        return QPixmap.fromImage(qimg)

    def _align_face_with_landmarks(self, img: Image.Image, face_data: dict, search_x: int, search_y: int) -> Tuple[int, int, int, int]:
        """
        ENHANCEMENT #1: Align face using facial landmarks (eye positions).

        Rotates image to make eyes horizontal for better recognition accuracy.
        This is industry standard used by Google Photos, Apple Photos, etc.

        Args:
            img: PIL Image (already EXIF-rotated)
            face_data: Detection result with 'kps' (keypoints/landmarks)
            search_x, search_y: Offset of search region in original image

        Returns:
            (x, y, w, h): Aligned face bbox in original image coordinates
        """
        import numpy as np
        import cv2

        try:
            kps = face_data.get('kps')
            if not kps or len(kps) < 2:
                logger.debug("[FaceCropEditor] No landmarks available - skipping alignment")
                # Fall back to bbox-only (no alignment)
                return (
                    search_x + face_data['bbox_x'],
                    search_y + face_data['bbox_y'],
                    face_data['bbox_w'],
                    face_data['bbox_h']
                )

            # Extract eye positions (landmarks 0 and 1)
            left_eye = np.array(kps[0])   # [x, y]
            right_eye = np.array(kps[1])  # [x, y]

            # Convert to original image coordinates
            left_eye_abs = left_eye + np.array([search_x, search_y])
            right_eye_abs = right_eye + np.array([search_x, search_y])

            # Calculate angle between eyes
            dx = right_eye_abs[0] - left_eye_abs[0]
            dy = right_eye_abs[1] - left_eye_abs[1]
            angle = np.degrees(np.arctan2(dy, dx))

            logger.debug(f"[FaceCropEditor] Eye angle: {angle:.2f}° (0° = perfectly horizontal)")

            # If angle is within ±3°, no rotation needed
            if abs(angle) < 3.0:
                logger.info(f"[FaceCropEditor] Face already aligned ({angle:.2f}°) - skipping rotation")
                return (
                    search_x + face_data['bbox_x'],
                    search_y + face_data['bbox_y'],
                    face_data['bbox_w'],
                    face_data['bbox_h']
                )

            # Calculate center point between eyes for rotation
            center_x = (left_eye_abs[0] + right_eye_abs[0]) / 2
            center_y = (left_eye_abs[1] + right_eye_abs[1]) / 2

            # Convert PIL to CV2 for rotation
            img_array = np.array(img)
            img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

            # Create rotation matrix
            M = cv2.getRotationMatrix2D((center_x, center_y), angle, 1.0)

            # Calculate new image bounds to avoid cropping
            cos = np.abs(M[0, 0])
            sin = np.abs(M[0, 1])
            new_w = int((img.height * sin) + (img.width * cos))
            new_h = int((img.height * cos) + (img.width * sin))

            # Adjust rotation matrix for new bounds
            M[0, 2] += (new_w / 2) - center_x
            M[1, 2] += (new_h / 2) - center_y

            # Rotate image
            rotated = cv2.warpAffine(img_cv, M, (new_w, new_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

            # Convert back to PIL
            rotated_rgb = cv2.cvtColor(rotated, cv2.COLOR_BGR2RGB)
            rotated_pil = Image.fromarray(rotated_rgb)

            # Save rotated image temporarily for detection
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                rotated_pil.save(tmp.name, "JPEG", quality=95)
                temp_path = tmp.name

            # Re-run detection on rotated image to get aligned bbox
            from services.face_detection_service import FaceDetectionService
            detector = FaceDetectionService()
            detected_faces = detector.detect_faces(temp_path, project_id=self.project_id)

            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass

            if len(detected_faces) == 1:
                # Get bbox from aligned detection
                aligned_face = detected_faces[0]
                aligned_x = aligned_face['bbox_x']
                aligned_y = aligned_face['bbox_y']
                aligned_w = aligned_face['bbox_w']
                aligned_h = aligned_face['bbox_h']

                # Transform back to original image coordinates
                # We need to reverse the rotation transformation
                # For simplicity, we'll use the bbox from the rotated image
                # and map it back to the original image space

                # Calculate the corners of the aligned bbox
                corners = np.array([
                    [aligned_x, aligned_y],
                    [aligned_x + aligned_w, aligned_y],
                    [aligned_x, aligned_y + aligned_h],
                    [aligned_x + aligned_w, aligned_y + aligned_h]
                ], dtype=np.float32)

                # Reverse rotation matrix
                M_inv = cv2.invertAffineTransform(M)

                # Transform corners back to original space
                corners_homogeneous = np.hstack([corners, np.ones((4, 1))])
                original_corners = corners_homogeneous @ M_inv.T

                # Get bounding box of transformed corners
                min_x = int(np.min(original_corners[:, 0]))
                min_y = int(np.min(original_corners[:, 1]))
                max_x = int(np.max(original_corners[:, 0]))
                max_y = int(np.max(original_corners[:, 1]))

                # Ensure within image bounds
                min_x = max(0, min(min_x, img.width - 1))
                min_y = max(0, min(min_y, img.height - 1))
                max_x = max(0, min(max_x, img.width))
                max_y = max(0, min(max_y, img.height))

                final_w = max_x - min_x
                final_h = max_y - min_y

                logger.info(f"[FaceCropEditor] ✅ Face aligned: rotated {angle:.2f}° for horizontal eyes")
                logger.debug(f"[FaceCropEditor] Aligned bbox: ({min_x}, {min_y}, {final_w}, {final_h})")

                return (min_x, min_y, final_w, final_h)
            else:
                logger.warning(f"[FaceCropEditor] Re-detection after rotation found {len(detected_faces)} faces - using original bbox")
                return (
                    search_x + face_data['bbox_x'],
                    search_y + face_data['bbox_y'],
                    face_data['bbox_w'],
                    face_data['bbox_h']
                )

        except Exception as e:
            logger.error(f"[FaceCropEditor] Face alignment failed: {e}", exc_info=True)
            # Fall back to original bbox
            return (
                search_x + face_data['bbox_x'],
                search_y + face_data['bbox_y'],
                face_data['bbox_w'],
                face_data['bbox_h']
            )

    def _assess_face_quality(self, face_crop_path: str, face_data: dict) -> dict:
        """
        ENHANCEMENT #3: Assess face crop quality and detect issues.

        Analyzes blur, brightness, contrast, and size to prevent low-quality crops
        that hurt recognition accuracy.

        Args:
            face_crop_path: Path to saved face crop image
            face_data: Detection data with confidence score

        Returns:
            dict with 'overall_score' (0-100), 'issues' (list), 'is_acceptable' (bool)
        """
        import cv2
        import numpy as np

        quality_report = {
            'overall_score': 50.0,  # Start at 50
            'issues': [],
            'confidence': face_data.get('confidence', 0.0),
            'is_acceptable': True,
            'metrics': {}
        }

        try:
            # Load crop
            img = cv2.imread(face_crop_path)
            if img is None:
                quality_report['issues'].append("Could not load image")
                quality_report['is_acceptable'] = False
                return quality_report

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # METRIC #1: Blur detection (Laplacian variance)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            quality_report['metrics']['blur_score'] = laplacian_var

            if laplacian_var < 50:
                quality_report['issues'].append(f"⚠️ Very blurry (sharpness: {laplacian_var:.0f})")
                quality_report['overall_score'] -= 35
            elif laplacian_var < 100:
                quality_report['issues'].append(f"⚠️ Slightly blurry (sharpness: {laplacian_var:.0f})")
                quality_report['overall_score'] -= 15
            else:
                quality_report['overall_score'] += 10  # Bonus for sharp image

            # METRIC #2: Brightness (too dark/bright)
            brightness = np.mean(gray)
            quality_report['metrics']['brightness'] = brightness

            if brightness < 40:
                quality_report['issues'].append(f"⚠️ Too dark (brightness: {brightness:.0f}/255)")
                quality_report['overall_score'] -= 25
            elif brightness < 60:
                quality_report['issues'].append(f"⚠️ Quite dark (brightness: {brightness:.0f}/255)")
                quality_report['overall_score'] -= 10
            elif brightness > 220:
                quality_report['issues'].append(f"⚠️ Too bright (brightness: {brightness:.0f}/255)")
                quality_report['overall_score'] -= 20
            elif brightness > 200:
                quality_report['issues'].append(f"⚠️ Quite bright (brightness: {brightness:.0f}/255)")
                quality_report['overall_score'] -= 10
            else:
                quality_report['overall_score'] += 5  # Bonus for good brightness

            # METRIC #3: Contrast
            contrast = gray.std()
            quality_report['metrics']['contrast'] = contrast

            if contrast < 15:
                quality_report['issues'].append(f"⚠️ Very low contrast ({contrast:.0f})")
                quality_report['overall_score'] -= 20
            elif contrast < 25:
                quality_report['issues'].append(f"⚠️ Low contrast ({contrast:.0f})")
                quality_report['overall_score'] -= 10
            else:
                quality_report['overall_score'] += 5  # Bonus for good contrast

            # METRIC #4: Detection confidence
            conf_score = face_data.get('confidence', 0.0) * 100
            quality_report['metrics']['detection_confidence'] = conf_score

            if conf_score < 50:
                quality_report['issues'].append(f"⚠️ Low detection confidence ({conf_score:.0f}%)")
                quality_report['overall_score'] -= 15
            else:
                quality_report['overall_score'] += conf_score * 0.2  # Up to 20 points bonus

            # METRIC #5: Size (faces too small are low quality)
            face_pixels = face_data.get('bbox_w', 0) * face_data.get('bbox_h', 0)
            quality_report['metrics']['face_size_pixels'] = face_pixels

            if face_pixels < 40 * 40:  # Smaller than 40x40
                quality_report['issues'].append(f"⚠️ Face too small ({face_data.get('bbox_w', 0)}x{face_data.get('bbox_h', 0)})")
                quality_report['overall_score'] -= 30
            elif face_pixels < 60 * 60:  # Smaller than 60x60
                quality_report['issues'].append(f"⚠️ Face quite small ({face_data.get('bbox_w', 0)}x{face_data.get('bbox_h', 0)})")
                quality_report['overall_score'] -= 15

            # Clamp score 0-100
            quality_report['overall_score'] = max(0, min(100, quality_report['overall_score']))

            # Mark as unacceptable if score < 35 (very poor quality)
            if quality_report['overall_score'] < 35:
                quality_report['is_acceptable'] = False

            logger.debug(f"[FaceCropEditor] Quality assessment: score={quality_report['overall_score']:.0f}, issues={len(quality_report['issues'])}")

        except Exception as e:
            logger.error(f"[FaceCropEditor] Quality assessment failed: {e}", exc_info=True)
            # If assessment fails, accept by default (don't block)
            quality_report['issues'].append("Quality check failed")

        return quality_report

    def _show_quality_warning(self, quality_report: dict, face_crop_path: str) -> bool:
        """Show warning dialog for low-quality face crops."""
        from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("⚠️ Low Quality Face Detected")
            dialog.setModal(True)
            dialog.setMinimumWidth(500)

            layout = QVBoxLayout(dialog)
            layout.setSpacing(16)
            layout.setContentsMargins(20, 20, 20, 20)

            # Score header
            score = quality_report['overall_score']
            if score < 20:
                score_color = "#d93025"  # Red
                score_emoji = "❌"
            elif score < 35:
                score_color = "#ea8600"  # Orange
                score_emoji = "⚠️"
            else:
                score_color = "#f9ab00"  # Yellow
                score_emoji = "⚠️"

            header = QLabel(f"{score_emoji} Quality Score: {score:.0f}/100")
            header.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {score_color}; padding: 8px;")
            header.setAlignment(Qt.AlignCenter)
            layout.addWidget(header)

            # Preview image
            preview_container = QWidget()
            preview_layout = QHBoxLayout(preview_container)
            preview_layout.addStretch()

            if os.path.exists(face_crop_path):
                preview_label = QLabel()
                pixmap = QPixmap(face_crop_path)
                preview_label.setPixmap(pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                preview_label.setStyleSheet("border: 2px solid #ea8600; background: #fef7e0; padding: 8px;")
                preview_layout.addWidget(preview_label)

            preview_layout.addStretch()
            layout.addWidget(preview_container)

            # Issues list
            if quality_report['issues']:
                issues_label = QLabel("Quality Issues Detected:")
                issues_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
                layout.addWidget(issues_label)

                issues_text = "\n".join([f"  {issue}" for issue in quality_report['issues']])
                issues_display = QTextEdit()
                issues_display.setPlainText(issues_text)
                issues_display.setReadOnly(True)
                issues_display.setMaximumHeight(100)
                issues_display.setStyleSheet("""
                    QTextEdit {
                        background: #fef7e0;
                        border: 1px solid #ea8600;
                        border-radius: 4px;
                        padding: 8px;
                        font-size: 9pt;
                        color: #5f6368;
                    }
                """)
                layout.addWidget(issues_display)

            # Recommendations
            recommendations = QLabel(
                "💡 Recommendations for better quality:\n"
                "   • Use a clearer, sharper photo\n"
                "   • Ensure good lighting (not too dark or bright)\n"
                "   • Draw a larger rectangle around the face\n"
                "   • Avoid blurry or out-of-focus images"
            )
            recommendations.setStyleSheet("""
                QLabel {
                    background: #e8f0fe;
                    padding: 12px;
                    border-radius: 6px;
                    color: #1967d2;
                    font-size: 9pt;
                }
            """)
            layout.addWidget(recommendations)

            # Warning message
            warning = QLabel(
                "⚠️ Face recognition may not work well with this crop.\n"
                "You can still save it, but consider using a better photo."
            )
            warning.setStyleSheet("color: #ea8600; font-size: 9pt; padding: 8px;")
            warning.setWordWrap(True)
            layout.addWidget(warning)

            # Buttons
            button_layout = QHBoxLayout()
            button_layout.addStretch()

            accept_btn = QPushButton("Save Anyway")
            accept_btn.setStyleSheet("""
                QPushButton {
                    background: #ea8600;
                    color: white;
                    border: none;
                    padding: 10px 24px;
                    border-radius: 6px;
                    font-size: 10pt;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #c77400;
                }
            """)
            accept_btn.clicked.connect(dialog.accept)
            button_layout.addWidget(accept_btn)

            skip_btn = QPushButton("Skip This Face")
            skip_btn.setStyleSheet("""
                QPushButton {
                    background: #f1f3f4;
                    color: #5f6368;
                    border: 1px solid #dadce0;
                    padding: 10px 24px;
                    border-radius: 6px;
                    font-size: 10pt;
                }
                QPushButton:hover {
                    background: #e8eaed;
                }
            """)
            skip_btn.setDefault(True)
            skip_btn.clicked.connect(dialog.reject)
            button_layout.addWidget(skip_btn)

            button_layout.addStretch()
            layout.addLayout(button_layout)

            # Show dialog
            result = dialog.exec()
            return result == QDialog.Accepted

        except Exception as e:
            logger.error(f"[FaceCropEditor] Error showing quality warning: {e}", exc_info=True)
            # If warning fails, accept by default (don't block)
            return True

    def _refine_manual_bbox_with_detection(self, x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
        """
        ENHANCEMENT: Refine manually drawn bbox using face detection (Best Practice).

        Strategy:
        1. User's manual rectangle defines the SEARCH REGION (they know where the face is)
        2. Expand by 20% to ensure full face is captured
        3. Run face detection on that region
        4. If exactly 1 face detected: Use refined coordinates (consistent padding like auto-detected faces)
        5. If 0 or multiple faces: Keep original manual bbox (user knows better - profile faces, unusual angles, etc.)

        Benefits:
        - Consistent padding/margins like auto-detected faces
        - Automatic face alignment
        - Ensures crop actually contains a detectable face
        - Falls back gracefully if detection fails

        Args:
            x, y, w, h: User's manual bounding box

        Returns:
            tuple: ((x, y, w, h), face_data) where:
                - (x, y, w, h): Refined bbox or original if detection fails
                - face_data: Dict with 'confidence', 'bbox_w', 'bbox_h' for quality assessment
                            (None if detection failed)
        """
        try:
            logger.debug(f"[FaceCropEditor] Refining manual bbox: ({x}, {y}, {w}, {h})")

            # Load original image
            with Image.open(self.photo_path) as img:
                # Apply EXIF rotation
                img = ImageOps.exif_transpose(img)
                img_width, img_height = img.size

                # Expand bbox by 20% to ensure full face is in frame
                padding_pct = 0.20
                pad_x = int(w * padding_pct)
                pad_y = int(h * padding_pct)

                search_x = max(0, x - pad_x)
                search_y = max(0, y - pad_y)
                search_w = min(img_width - search_x, w + 2 * pad_x)
                search_h = min(img_height - search_y, h + 2 * pad_y)

                # Crop search region
                search_region = img.crop((search_x, search_y, search_x + search_w, search_y + search_h))

                # Save search region temporarily
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    if search_region.mode != 'RGB':
                        search_region = search_region.convert('RGB')
                    search_region.save(tmp.name, "JPEG", quality=95)
                    temp_path = tmp.name

                # Run face detection on search region
                from services.face_detection_service import FaceDetectionService
                detector = FaceDetectionService()
                detected_faces = detector.detect_faces(temp_path, project_id=self.project_id)

                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except:
                    pass

                # If exactly 1 face detected, use refined coordinates
                if len(detected_faces) == 1:
                    face = detected_faces[0]

                    # ENHANCEMENT #1: Try face alignment if landmarks available
                    if face.get('kps') and len(face.get('kps', [])) >= 2:
                        logger.info("[FaceCropEditor] Landmarks detected - attempting face alignment")
                        aligned_bbox = self._align_face_with_landmarks(img, face, search_x, search_y)
                        if aligned_bbox:
                            refined_x, refined_y, refined_w, refined_h = aligned_bbox
                            logger.info(f"[FaceCropEditor] ✅ Refined manual bbox with alignment: ({x},{y},{w},{h}) → ({refined_x},{refined_y},{refined_w},{refined_h})")
                            # Return bbox and face_data for quality assessment
                            face_data = {
                                'confidence': face.get('confidence', 0.0),
                                'bbox_w': refined_w,
                                'bbox_h': refined_h
                            }
                            return (refined_x, refined_y, refined_w, refined_h), face_data

                    # Fallback: No landmarks or alignment failed - use bbox only
                    logger.debug("[FaceCropEditor] Using bbox-only refinement (no alignment)")

                    # Get bbox from detection (relative to search region)
                    det_x = face['bbox_x']
                    det_y = face['bbox_y']
                    det_w = face['bbox_w']
                    det_h = face['bbox_h']

                    # Convert to original image coordinates
                    refined_x = search_x + det_x
                    refined_y = search_y + det_y
                    refined_w = det_w
                    refined_h = det_h

                    # Ensure within image bounds
                    refined_x = max(0, min(refined_x, img_width - 1))
                    refined_y = max(0, min(refined_y, img_height - 1))
                    refined_w = max(1, min(refined_w, img_width - refined_x))
                    refined_h = max(1, min(refined_h, img_height - refined_y))

                    logger.info(f"[FaceCropEditor] ✅ Refined manual bbox: ({x},{y},{w},{h}) → ({refined_x},{refined_y},{refined_w},{refined_h}) (confidence: {face.get('confidence', 0):.2f})")
                    # Return bbox and face_data for quality assessment
                    face_data = {
                        'confidence': face.get('confidence', 0.0),
                        'bbox_w': refined_w,
                        'bbox_h': refined_h
                    }
                    return (refined_x, refined_y, refined_w, refined_h), face_data

                elif len(detected_faces) == 0:
                    logger.info(f"[FaceCropEditor] ⚠️ No face detected in manual region - keeping original bbox (may be profile/unusual angle)")
                    # No face_data available when detection fails
                    return (x, y, w, h), None

                else:
                    logger.info(f"[FaceCropEditor] ⚠️ Multiple faces ({len(detected_faces)}) detected in manual region - keeping original bbox")
                    # No face_data available when multiple faces detected
                    return (x, y, w, h), None

        except Exception as e:
            logger.warning(f"[FaceCropEditor] Face detection refinement failed: {e} - keeping original bbox")
            # No face_data available when exception occurs
            return (x, y, w, h), None

    def _extract_embedding(self, crop_path: str) -> Optional[np.ndarray]:
        """
        Extract face embedding from saved crop for similarity comparison.

        Args:
            crop_path: Path to saved face crop

        Returns:
            512-dimensional embedding vector, or None if extraction fails
        """
        try:
            from services.face_detection_service import FaceDetectionService

            detector = FaceDetectionService()
            faces = detector.detect_faces(crop_path, project_id=self.project_id)

            if len(faces) == 1:
                embedding = faces[0].get('embedding')
                if embedding is not None:
                    logger.debug(f"[FaceCropEditor] Extracted embedding from crop: {crop_path}")
                    return np.array(embedding)
                else:
                    logger.warning(f"[FaceCropEditor] No embedding in detection result")
                    return None
            elif len(faces) == 0:
                logger.warning(f"[FaceCropEditor] No face detected in crop for embedding extraction")
                return None
            else:
                logger.warning(f"[FaceCropEditor] Multiple faces ({len(faces)}) detected in crop - using first")
                embedding = faces[0].get('embedding')
                return np.array(embedding) if embedding is not None else None

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to extract embedding: {e}")
            return None

    def _find_similar_faces(self, embedding: np.ndarray, threshold: float = 0.6) -> List[dict]:
        """
        ENHANCEMENT #5: Find existing faces similar to the new face.

        Uses cosine similarity to compare embeddings.
        Similarity > threshold suggests same person.

        Args:
            embedding: 512-dimensional embedding of new face
            threshold: Similarity threshold (0-1), default 0.6
                      0.6 = possible match (suggest merge)
                      0.7 = likely match
                      0.8+ = very likely same person

        Returns:
            List of similar faces sorted by similarity (highest first):
            [
                {
                    'branch_key': str,
                    'label': str,
                    'similarity': float (0-1),
                    'count': int,
                    'rep_path': str
                },
                ...
            ]
        """
        try:
            from reference_db import ReferenceDB

            similar_faces = []

            # Query all existing face branch representatives
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()

                # Get all branch reps with centroids (embeddings)
                cur.execute("""
                    SELECT branch_key, label, centroid, count, rep_path
                    FROM face_branch_reps
                    WHERE project_id = ?
                    AND centroid IS NOT NULL
                """, (self.project_id,))

                rows = cur.fetchall()
                logger.debug(f"[FaceCropEditor] Comparing with {len(rows)} existing face clusters")

                for row in rows:
                    branch_key, label, centroid_blob, count, rep_path = row

                    # Deserialize centroid embedding
                    try:
                        # CRITICAL FIX: Centroids are stored as numpy tobytes(), NOT pickle
                        # Must use frombuffer() to deserialize, not pickle.loads()
                        centroid = np.frombuffer(centroid_blob, dtype=np.float32)

                        # Calculate cosine similarity
                        # Cosine similarity = dot(A, B) / (||A|| * ||B||)
                        # Range: -1 to 1, where 1 = identical, 0 = orthogonal
                        similarity = np.dot(embedding, centroid) / (
                            np.linalg.norm(embedding) * np.linalg.norm(centroid)
                        )

                        # Only include if above threshold
                        if similarity >= threshold:
                            similar_faces.append({
                                'branch_key': branch_key,
                                'label': label or 'Unknown',
                                'similarity': float(similarity),
                                'count': count or 0,
                                'rep_path': rep_path
                            })
                            logger.debug(f"[FaceCropEditor] Similar: {label or branch_key} (similarity: {similarity:.3f})")

                    except Exception as deserialize_err:
                        logger.warning(f"[FaceCropEditor] Failed to deserialize centroid for {branch_key}: {deserialize_err}")
                        continue

            # Sort by similarity (highest first)
            similar_faces.sort(key=lambda x: x['similarity'], reverse=True)

            if similar_faces:
                logger.info(f"[FaceCropEditor] Found {len(similar_faces)} similar faces (threshold: {threshold})")
            else:
                logger.debug(f"[FaceCropEditor] No similar faces found (threshold: {threshold})")

            return similar_faces

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to find similar faces: {e}", exc_info=True)
            return []

    def _show_merge_suggestion_dialog(self, new_crop_path: str, similar_faces: List[dict]) -> Optional[str]:
        """
        ENHANCEMENT #5: Show dialog suggesting merge with similar existing faces.

        Args:
            new_crop_path: Path to newly created face crop
            similar_faces: List of similar existing faces (from _find_similar_faces)

        Returns:
            branch_key to merge with, or None if user wants to keep as new person
        """
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Similar Face Detected")
            dialog.setModal(True)
            dialog.setMinimumWidth(600)

            layout = QVBoxLayout(dialog)

            # Header
            header = QLabel("🔍 We found faces that might be the same person")
            header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
            layout.addWidget(header)

            info = QLabel(
                "The face you just added looks similar to existing faces in your library.\n"
                "Would you like to merge them together?"
            )
            info.setWordWrap(True)
            info.setStyleSheet("color: #666; padding: 5px 10px;")
            layout.addWidget(info)

            # Scroll area for face previews
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(400)

            scroll_widget = QWidget()
            scroll_layout = QVBoxLayout(scroll_widget)

            # Show new face at top
            new_face_group = QGroupBox("New Face (Just Added)")
            new_face_layout = QHBoxLayout()

            try:
                pixmap = QPixmap(new_crop_path)
                if not pixmap.isNull():
                    preview_label = QLabel()
                    preview_label.setPixmap(pixmap.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    preview_label.setFixedSize(150, 150)
                    preview_label.setStyleSheet("border: 2px solid #2196F3; background: white;")
                    new_face_layout.addWidget(preview_label)
            except Exception as e:
                logger.warning(f"[FaceCropEditor] Failed to load new face preview: {e}")

            new_face_group.setLayout(new_face_layout)
            scroll_layout.addWidget(new_face_group)

            # Show similar existing faces (top 5)
            similar_group = QGroupBox("Similar Existing Faces")
            similar_layout = QVBoxLayout()

            # Store radio buttons for selection
            radio_buttons = []
            selected_branch_key = [None]  # Use list to allow modification in closure

            for idx, face in enumerate(similar_faces[:5]):  # Show top 5 matches
                face_row = QHBoxLayout()

                # Radio button for selection
                radio = QRadioButton()
                radio.branch_key = face['branch_key']  # Store branch_key on radio button
                radio_buttons.append(radio)
                face_row.addWidget(radio)

                # Face preview
                try:
                    if face.get('rep_path') and os.path.exists(face['rep_path']):
                        pixmap = QPixmap(face['rep_path'])
                        if not pixmap.isNull():
                            preview_label = QLabel()
                            preview_label.setPixmap(pixmap.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                            preview_label.setFixedSize(100, 100)
                            preview_label.setStyleSheet("border: 1px solid #ccc; background: white;")
                            face_row.addWidget(preview_label)
                except Exception as e:
                    logger.warning(f"[FaceCropEditor] Failed to load similar face preview: {e}")

                # Face info
                info_layout = QVBoxLayout()

                # Label (name)
                label = face.get('label', 'Unknown')
                label_widget = QLabel(f"<b>{label}</b>")
                info_layout.addWidget(label_widget)

                # Similarity score
                similarity = face.get('similarity', 0.0)
                confidence_text = "Very Likely Match" if similarity >= 0.8 else "Likely Match" if similarity >= 0.7 else "Possible Match"
                similarity_widget = QLabel(f"Similarity: {similarity:.1%} ({confidence_text})")
                similarity_widget.setStyleSheet("color: #4CAF50;" if similarity >= 0.7 else "color: #FF9800;")
                info_layout.addWidget(similarity_widget)

                # Photo count
                count = face.get('count', 0)
                count_widget = QLabel(f"Photos: {count}")
                count_widget.setStyleSheet("color: #666; font-size: 11px;")
                info_layout.addWidget(count_widget)

                face_row.addLayout(info_layout)
                face_row.addStretch()

                similar_layout.addLayout(face_row)

            similar_group.setLayout(similar_layout)
            scroll_layout.addWidget(similar_group)

            scroll_layout.addStretch()
            scroll.setWidget(scroll_widget)
            layout.addWidget(scroll)

            # Buttons
            button_layout = QHBoxLayout()

            # "Keep as New Person" button
            keep_new_btn = QPushButton("Keep as New Person")
            keep_new_btn.setStyleSheet("padding: 8px 16px; background: #f5f5f5;")
            keep_new_btn.clicked.connect(lambda: [
                selected_branch_key.__setitem__(0, None),
                dialog.accept()
            ])
            button_layout.addWidget(keep_new_btn)

            button_layout.addStretch()

            # "Merge with Selected" button
            merge_btn = QPushButton("Merge with Selected Person")
            merge_btn.setStyleSheet("padding: 8px 16px; background: #2196F3; color: white; font-weight: bold;")
            merge_btn.setEnabled(False)  # Disabled until selection made

            def on_radio_changed():
                # Enable merge button when any radio is selected
                for radio in radio_buttons:
                    if radio.isChecked():
                        merge_btn.setEnabled(True)
                        selected_branch_key[0] = radio.branch_key
                        return
                merge_btn.setEnabled(False)
                selected_branch_key[0] = None

            # Connect all radio buttons
            for radio in radio_buttons:
                radio.toggled.connect(on_radio_changed)

            merge_btn.clicked.connect(dialog.accept)
            button_layout.addWidget(merge_btn)

            layout.addLayout(button_layout)

            # Show dialog
            result = dialog.exec()

            if result == QDialog.Accepted:
                if selected_branch_key[0]:
                    logger.info(f"[FaceCropEditor] User chose to merge with: {selected_branch_key[0]}")
                    return selected_branch_key[0]
                else:
                    logger.info(f"[FaceCropEditor] User chose to keep as new person")
                    return None
            else:
                # Dialog cancelled - keep as new
                logger.debug(f"[FaceCropEditor] Merge dialog cancelled - keeping as new")
                return None

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to show merge suggestion dialog: {e}", exc_info=True)
            return None  # On error, keep as new person

    def _merge_face_with_existing(self, new_branch_key: str, target_branch_key: str) -> bool:
        """
        Merge newly created face with existing person.

        Updates the new face's branch_key to match the target,
        effectively adding it to that person's cluster.

        Args:
            new_branch_key: Branch key of newly created face
            target_branch_key: Branch key of existing person to merge with

        Returns:
            True if merge successful, False otherwise
        """
        try:
            from reference_db import ReferenceDB

            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()

                # Update face_crops: Change branch_key to target
                cur.execute("""
                    UPDATE face_crops
                    SET branch_key = ?
                    WHERE project_id = ?
                    AND branch_key = ?
                """, (target_branch_key, self.project_id, new_branch_key))

                rows_updated = cur.rowcount
                logger.info(f"[FaceCropEditor] Updated {rows_updated} face_crops rows: {new_branch_key} → {target_branch_key}")

                # Delete the temporary branch rep (new_branch_key)
                cur.execute("""
                    DELETE FROM face_branch_reps
                    WHERE project_id = ?
                    AND branch_key = ?
                """, (self.project_id, new_branch_key))

                # Increment count for target branch
                cur.execute("""
                    UPDATE face_branch_reps
                    SET count = count + 1
                    WHERE project_id = ?
                    AND branch_key = ?
                """, (self.project_id, target_branch_key))

                conn.commit()

                logger.info(f"[FaceCropEditor] ✅ Successfully merged {new_branch_key} into {target_branch_key}")
                return True

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to merge faces: {e}", exc_info=True)
            return False

    def _apply_post_processing(self, face_crop: Image.Image) -> Image.Image:
        """
        ENHANCEMENT #6: Apply subtle post-processing to improve face crop appearance.

        Applies:
        - Auto-brightness adjustment (if too dark or too bright)
        - Auto-contrast enhancement (if low contrast)
        - Subtle sharpening (improves clarity without over-sharpening)

        Args:
            face_crop: PIL Image of the face crop

        Returns:
            Enhanced PIL Image
        """
        try:
            from PIL import ImageEnhance, ImageFilter

            # Convert to RGB if needed
            if face_crop.mode != 'RGB':
                face_crop = face_crop.convert('RGB')

            # Convert to numpy for analysis
            crop_array = np.array(face_crop)
            gray = cv2.cvtColor(crop_array, cv2.COLOR_RGB2GRAY)

            # METRIC #1: Analyze brightness (mean pixel value)
            brightness = np.mean(gray)
            logger.debug(f"[FaceCropEditor] Post-processing - brightness: {brightness:.1f}/255")

            # METRIC #2: Analyze contrast (standard deviation)
            contrast = gray.std()
            logger.debug(f"[FaceCropEditor] Post-processing - contrast: {contrast:.1f}")

            enhancements_applied = []

            # AUTO-BRIGHTNESS: Adjust if too dark or too bright
            # Target brightness: 100-160 (comfortable viewing range)
            if brightness < 80:
                # Too dark - brighten
                factor = 1.3 + ((80 - brightness) / 100)  # Adaptive brightening
                factor = min(factor, 1.8)  # Cap at 1.8x
                enhancer = ImageEnhance.Brightness(face_crop)
                face_crop = enhancer.enhance(factor)
                enhancements_applied.append(f"brighten {factor:.2f}x")
                logger.info(f"[FaceCropEditor] Applied brightness enhancement: {factor:.2f}x (was {brightness:.0f}/255)")
            elif brightness > 180:
                # Too bright - darken slightly
                factor = 0.85
                enhancer = ImageEnhance.Brightness(face_crop)
                face_crop = enhancer.enhance(factor)
                enhancements_applied.append(f"darken {factor:.2f}x")
                logger.info(f"[FaceCropEditor] Applied brightness reduction: {factor:.2f}x (was {brightness:.0f}/255)")

            # AUTO-CONTRAST: Enhance if low contrast
            # Target contrast: > 30 (good definition)
            if contrast < 30:
                # Low contrast - enhance
                factor = 1.2 + ((30 - contrast) / 40)  # Adaptive enhancement
                factor = min(factor, 1.6)  # Cap at 1.6x
                enhancer = ImageEnhance.Contrast(face_crop)
                face_crop = enhancer.enhance(factor)
                enhancements_applied.append(f"contrast {factor:.2f}x")
                logger.info(f"[FaceCropEditor] Applied contrast enhancement: {factor:.2f}x (was {contrast:.0f})")

            # SHARPENING: Always apply subtle sharpening for clarity
            # Use SHARPEN filter (gentle) or UnsharpMask (more control)
            # SHARPEN is simpler and safer - just enhances edges slightly
            face_crop = face_crop.filter(ImageFilter.SHARPEN)
            enhancements_applied.append("sharpen")
            logger.debug(f"[FaceCropEditor] Applied sharpening filter")

            if enhancements_applied:
                logger.info(f"[FaceCropEditor] ✅ Post-processing applied: {', '.join(enhancements_applied)}")
            else:
                logger.debug(f"[FaceCropEditor] No post-processing needed (already optimal)")

            return face_crop

        except Exception as e:
            logger.warning(f"[FaceCropEditor] Post-processing failed: {e} - using original crop")
            return face_crop  # Return original if enhancement fails

    def _create_face_crop(self, x: int, y: int, w: int, h: int) -> Optional[str]:
        """
        Crop face from original image and save to centralized directory.

        Args:
            x, y, w, h: Bounding box coordinates

        Returns:
            Path to saved crop, or None if failed
        """
        try:
            # Load original image
            with Image.open(self.photo_path) as img:
                # CRITICAL: Apply EXIF rotation before cropping
                # Without this, crops from rotated photos appear sideways
                img = ImageOps.exif_transpose(img)

                # ENHANCEMENT #2: Smart Padding (Industry Standard)
                # Add 30% padding around face for professional appearance
                # Asymmetric: more padding below to include shoulders (Google Photos style)
                padding_factor = 0.30  # 30% padding (industry standard)
                pad_w = int(w * padding_factor)
                pad_h = int(h * padding_factor)

                # Asymmetric padding: 50% more below for shoulders
                pad_top = pad_h
                pad_bottom = int(pad_h * 1.5)  # Include shoulders
                pad_left = pad_w
                pad_right = pad_w

                # Calculate crop coordinates with smart padding
                crop_x1 = max(0, x - pad_left)
                crop_y1 = max(0, y - pad_top)
                crop_x2 = min(img.width, x + w + pad_right)
                crop_y2 = min(img.height, y + h + pad_bottom)

                # Log padding details
                original_size = f"{w}x{h}"
                padded_size = f"{crop_x2-crop_x1}x{crop_y2-crop_y1}"
                logger.info(f"[FaceCropEditor] Smart padding applied: {original_size} → {padded_size} (+30% with shoulders)")

                # Crop face region with smart padding
                face_crop = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))

                # ENHANCEMENT #6: Post-Processing (Better Thumbnails)
                # Apply subtle enhancements: brightness, contrast, sharpening
                face_crop = self._apply_post_processing(face_crop)

                # Use centralized face_crops directory (not cluttering photo directories)
                # Create .memorymate/face_crops/ in user's home or project directory
                home_dir = Path.home()
                crop_dir = home_dir / ".memorymate" / "face_crops"
                crop_dir.mkdir(parents=True, exist_ok=True)

                # Generate unique crop filename using uuid
                photo_name = os.path.splitext(os.path.basename(self.photo_path))[0]
                unique_id = uuid.uuid4().hex[:8]
                crop_filename = f"{photo_name}_manual_{unique_id}.jpg"
                crop_path = crop_dir / crop_filename

                # Save crop (convert to RGB if needed)
                if face_crop.mode != 'RGB':
                    face_crop = face_crop.convert('RGB')
                # CRITICAL FIX (2026-01-08): Save without EXIF to prevent double-rotation
                # We already applied exif_transpose above, so saved image should have no rotation tag
                face_crop.save(str(crop_path), "JPEG", quality=95, exif=b'')

                logger.info(f"[FaceCropEditor] Saved face crop: {crop_path}")
                return str(crop_path)

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to create face crop: {e}")
            return None

    def _add_face_to_database(self, crop_path: str, bbox: Tuple[int, int, int, int]):
        """
        Add manually cropped face to database.

        Args:
            crop_path: Path to saved face crop
            bbox: Bounding box (x, y, w, h)

        Note:
            quality_score is set to 0.5 (medium quality) by default for manual crops.
            This indicates human verification but acknowledges potential quality issues
            that prompted manual addition.
        """
        try:
            # Generate a new branch_key for this face
            # It will be clustered later and potentially merged with existing people
            branch_key = f"manual_{uuid.uuid4().hex[:8]}"

            # Add to database using direct DB operations
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()

                # CRITICAL: Detect database schema to support both old and new versions
                # Check which columns exist in face_crops table
                cur.execute("PRAGMA table_info(face_crops)")
                columns = {row[1] for row in cur.fetchall()}

                has_bbox_text = 'bbox' in columns
                has_bbox_separate = all(col in columns for col in ['bbox_x', 'bbox_y', 'bbox_w', 'bbox_h'])
                has_quality_score = 'quality_score' in columns

                # Prepare INSERT based on schema
                if has_bbox_text:
                    # New schema: single bbox TEXT column
                    bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"

                    if has_quality_score:
                        # New schema with quality_score
                        cur.execute("""
                            INSERT INTO face_crops
                            (project_id, image_path, crop_path, bbox, branch_key, is_representative, quality_score)
                            VALUES (?, ?, ?, ?, ?, 1, 0.5)
                        """, (self.project_id, self.photo_path, crop_path, bbox_str, branch_key))
                    else:
                        # New schema without quality_score
                        cur.execute("""
                            INSERT INTO face_crops
                            (project_id, image_path, crop_path, bbox, branch_key, is_representative)
                            VALUES (?, ?, ?, ?, ?, 1)
                        """, (self.project_id, self.photo_path, crop_path, bbox_str, branch_key))

                elif has_bbox_separate:
                    # Old schema: separate bbox_x, bbox_y, bbox_w, bbox_h columns
                    x, y, w, h = bbox

                    if has_quality_score:
                        # Old schema with quality_score
                        cur.execute("""
                            INSERT INTO face_crops
                            (project_id, image_path, crop_path, bbox_x, bbox_y, bbox_w, bbox_h,
                             branch_key, is_representative, quality_score)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0.5)
                        """, (self.project_id, self.photo_path, crop_path, x, y, w, h, branch_key))
                    else:
                        # Old schema without quality_score
                        cur.execute("""
                            INSERT INTO face_crops
                            (project_id, image_path, crop_path, bbox_x, bbox_y, bbox_w, bbox_h,
                             branch_key, is_representative)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """, (self.project_id, self.photo_path, crop_path, x, y, w, h, branch_key))

                else:
                    # Fallback: No bbox columns (very old schema or corrupted database)
                    raise ValueError("Database schema is missing bbox columns (both TEXT and separate columns)")

                # Create face_branch_reps entry with count=1 (one unique photo)
                cur.execute("""
                    INSERT OR REPLACE INTO face_branch_reps
                    (project_id, branch_key, label, count, rep_path, rep_thumb_png)
                    VALUES (?, ?, ?, 1, ?, NULL)
                """, (self.project_id, branch_key, None, crop_path))

                conn.commit()

                logger.info(f"[FaceCropEditor] Added manual face to database: {branch_key} (schema: {'bbox_text' if has_bbox_text else 'bbox_separate'})")

                return branch_key

        except Exception as e:
            logger.error(f"[FaceCropEditor] Failed to add face to database: {e}")
            raise


class FacePhotoViewer(QWidget):
    """
    Widget for viewing photo with face rectangles overlay.
    Allows drawing new rectangles for manual face additions.
    """

    manualFaceAdded = Signal(tuple)  # (x, y, w, h)
    drawingModeChanged = Signal(bool)  # True when drawing mode enabled, False when disabled

    # Safety limits to prevent memory issues
    MAX_PHOTO_SIZE_MB = 50  # Maximum photo file size (50MB)
    MAX_DIMENSION = 12000  # Maximum width or height (12000 pixels)

    def __init__(self, photo_path: str, detected_faces: List[Dict], manual_faces: List[Dict], parent=None):
        try:
            logger.info(f"[FacePhotoViewer] Initializing photo viewer for: {photo_path}")
            super().__init__(parent)

            self.photo_path = photo_path
            self.detected_faces = detected_faces
            self.manual_faces = manual_faces

            self.drawing_mode = False
            self.keep_drawing_mode = False  # NEW: Flag to keep drawing mode enabled after each draw
            self.draw_start = None
            self.draw_end = None

            self.setMinimumHeight(400)
            logger.info(f"[FacePhotoViewer] Loading photo from disk...")
            self._load_photo()
            logger.info(f"[FacePhotoViewer] ✓ Photo viewer initialized successfully")

        except Exception as e:
            logger.error(f"[FacePhotoViewer] CRITICAL ERROR during initialization: {e}", exc_info=True)
            import traceback
            traceback.print_exc()
            raise  # Re-raise to prevent partial initialization

    def _load_photo(self):
        """
        Load and display the photo with safety checks and EXIF auto-rotation.

        Validates:
        - File size (< 50MB)
        - Image dimensions (< 12000×12000 pixels)
        - Auto-rotates based on EXIF orientation data
        """
        try:
            logger.info(f"[FacePhotoViewer] Step 1: Checking file existence...")
            # Check file size first (before loading into memory)
            if not os.path.exists(self.photo_path):
                logger.error(f"[FacePhotoViewer] Photo not found: {self.photo_path}")
                self.pixmap = None
                return

            logger.info(f"[FacePhotoViewer] Step 2: File exists, getting file size...")
            file_size_mb = os.path.getsize(self.photo_path) / (1024 * 1024)
            logger.info(f"[FacePhotoViewer] File size: {file_size_mb:.2f}MB")

            if file_size_mb > self.MAX_PHOTO_SIZE_MB:
                logger.warning(f"[FacePhotoViewer] Photo too large: {file_size_mb:.1f}MB (max {self.MAX_PHOTO_SIZE_MB}MB)")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Photo Too Large",
                    f"This photo is too large to display safely ({file_size_mb:.1f}MB).\n\n"
                    f"Maximum size: {self.MAX_PHOTO_SIZE_MB}MB\n\n"
                    "Please use a smaller photo or compress the image first."
                )
                self.pixmap = None
                return

            logger.info(f"[FacePhotoViewer] Step 3: Opening image with PIL...")
            # Load photo with PIL for EXIF auto-rotation
            pil_image = Image.open(self.photo_path)
            logger.info(f"[FacePhotoViewer] ✓ PIL image opened successfully")

            logger.info(f"[FacePhotoViewer] Step 4: Getting original dimensions...")
            # Store original dimensions BEFORE EXIF rotation
            original_width, original_height = pil_image.size
            logger.info(f"[FacePhotoViewer] Original dimensions: {original_width}×{original_height}")

            logger.info(f"[FacePhotoViewer] Step 5: Applying EXIF auto-rotation...")
            # Auto-rotate based on EXIF orientation (FIX #1)
            pil_image = ImageOps.exif_transpose(pil_image)
            logger.info(f"[FacePhotoViewer] ✓ EXIF rotation applied")

            logger.info(f"[FacePhotoViewer] Step 6: Getting rotated dimensions...")
            # Get dimensions AFTER EXIF rotation
            rotated_width, rotated_height = pil_image.size
            logger.info(f"[FacePhotoViewer] Rotated dimensions: {rotated_width}×{rotated_height}")

            # CRITICAL FIX: Transform existing face bbox coordinates if EXIF rotation occurred
            # Bbox coordinates were stored based on original (pre-rotation) dimensions
            # but we're displaying the rotated image, so coordinates need to be transformed
            if (original_width, original_height) != (rotated_width, rotated_height):
                logger.info(f"[FacePhotoViewer] EXIF rotation detected: {original_width}×{original_height} → {rotated_width}×{rotated_height}")
                logger.info(f"[FacePhotoViewer] Step 7: Transforming bbox coordinates...")
                self._transform_bbox_coordinates(original_width, original_height, rotated_width, rotated_height)
                logger.info(f"[FacePhotoViewer] ✓ Bbox coordinates transformed")

            logger.info(f"[FacePhotoViewer] Step 8: Checking dimensions...")

            # Performance fix: downscale large images for display instead of converting full-res
            # on the UI thread. Keep original coordinates in image space; only display is scaled.
            DISPLAY_MAX = 2400
            self.display_scale = 1.0

            if pil_image.width > DISPLAY_MAX or pil_image.height > DISPLAY_MAX:
                logger.info(
                    f"[FacePhotoViewer] Downscaling display image from {pil_image.width}×{pil_image.height} to fit max {DISPLAY_MAX}"
                )

                scale = min(DISPLAY_MAX / pil_image.width, DISPLAY_MAX / pil_image.height)
                new_w = max(1, int(pil_image.width * scale))
                new_h = max(1, int(pil_image.height * scale))
                self.display_scale = scale

                pil_image = pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)

                # Scale displayed face rectangles accordingly
                for face in self.detected_faces:
                    if face.get("bbox"):
                        x, y, w, h = face["bbox"]
                        face["bbox"] = (x * scale, y * scale, w * scale, h * scale)

                for face in self.manual_faces:
                    if face.get("bbox"):
                        x, y, w, h = face["bbox"]
                        face["bbox"] = (x * scale, y * scale, w * scale, h * scale)

                logger.info(
                    f"[FacePhotoViewer] Display image downscaled to {new_w}×{new_h} (scale={scale:.4f})"
                )

            logger.info(f"[FacePhotoViewer] Step 9: Converting to RGB mode...")
            # Convert PIL image to QPixmap
            if pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
            logger.info(f"[FacePhotoViewer] ✓ Image in RGB mode")

            logger.info(f"[FacePhotoViewer] Step 10: Converting to QImage...")
            # CRITICAL FIX: Store data as instance variable to prevent garbage collection
            # QImage references the data but doesn't own it, so if Python GC collects it,
            # QPixmap.fromImage() will crash with a segfault when trying to read deallocated memory
            self._image_data = pil_image.tobytes("raw", "RGB")
            logger.info(f"[FacePhotoViewer] ✓ Got image bytes ({len(self._image_data)} bytes)")

            # Calculate bytes per line explicitly (width × 3 bytes per RGB pixel)
            bytes_per_line = pil_image.width * 3
            logger.info(f"[FacePhotoViewer] Bytes per line: {bytes_per_line}")

            # Create QImage with explicit stride to prevent alignment issues
            qimg = QImage(self._image_data, pil_image.width, pil_image.height, bytes_per_line, QImage.Format_RGB888)
            logger.info(f"[FacePhotoViewer] ✓ QImage created")

            # CRITICAL FIX: Make a deep copy so QImage owns its own data
            # This prevents crashes if the original data gets modified or deallocated
            qimg = qimg.copy()
            logger.info(f"[FacePhotoViewer] ✓ QImage copied (owns its own data)")

            logger.info(f"[FacePhotoViewer] Step 11: Converting to QPixmap...")
            self.pixmap = QPixmap.fromImage(qimg)
            logger.info(f"[FacePhotoViewer] ✓ QPixmap created")

            if self.pixmap.isNull():
                logger.error(f"[FacePhotoViewer] Failed to convert photo to QPixmap")
                self.pixmap = None
                return

            logger.info(f"[FacePhotoViewer] ✓ Photo loaded successfully: {pil_image.width}×{pil_image.height}, {file_size_mb:.2f}MB")

        except Exception as e:
            logger.error(f"[FacePhotoViewer] CRITICAL ERROR loading photo: {e}", exc_info=True)
            import traceback
            traceback.print_exc()
            self.pixmap = None
            raise  # Re-raise to prevent partial initialization

    def _transform_bbox_coordinates(self, orig_w: int, orig_h: int, rot_w: int, rot_h: int):
        """
        Transform bbox coordinates from original (pre-EXIF-rotation) to rotated coordinate system.

        When EXIF rotation is applied, the coordinate system changes:
        - 90° CW rotation: width↔height swap, x and y transform
        - 90° CCW rotation: width↔height swap, different x/y transform
        - 180° rotation: no dimension swap, but x/y flip

        Args:
            orig_w, orig_h: Original image dimensions (before EXIF rotation)
            rot_w, rot_h: Rotated image dimensions (after EXIF rotation)
        """
        try:
            # Determine rotation type based on dimension changes
            if orig_w == rot_h and orig_h == rot_w:
                # Dimensions swapped: 90° or 270° rotation
                if orig_w < orig_h:
                    # Portrait → Landscape: 90° CW rotation
                    rotation_type = "90cw"
                else:
                    # Landscape → Portrait: 90° CCW (or 270° CW)
                    rotation_type = "90ccw"
            elif orig_w == rot_w and orig_h == rot_h:
                # Dimensions unchanged: 180° or no rotation
                # This shouldn't happen since we check if dimensions differ
                return
            else:
                logger.warning(f"[FacePhotoViewer] Unexpected dimension change: {orig_w}×{orig_h} → {rot_w}×{rot_h}")
                return

            logger.info(f"[FacePhotoViewer] Applying {rotation_type} bbox coordinate transformation")

            # Transform each detected face's bbox
            for face in self.detected_faces:
                if not face.get('bbox'):
                    continue

                x, y, w, h = face['bbox']

                if rotation_type == "90cw":
                    # 90° clockwise: (x, y) in orig → (orig_h - y - h, x) in rotated
                    new_x = orig_h - y - h
                    new_y = x
                    new_w = h
                    new_h = w
                elif rotation_type == "90ccw":
                    # 90° counter-clockwise: (x, y) in orig → (y, orig_w - x - w) in rotated
                    new_x = y
                    new_y = orig_w - x - w
                    new_w = h
                    new_h = w
                else:
                    continue

                # Update bbox with transformed coordinates
                face['bbox'] = (new_x, new_y, new_w, new_h)
                logger.debug(f"[FacePhotoViewer] Transformed bbox: ({x}, {y}, {w}, {h}) → ({new_x}, {new_y}, {new_w}, {new_h})")

        except Exception as e:
            logger.error(f"[FacePhotoViewer] Failed to transform bbox coordinates: {e}")

    def enable_drawing_mode(self, keep_enabled=True):
        """
        Enable drawing mode for manual face rectangle.

        Args:
            keep_enabled: If True, drawing mode stays enabled after each rectangle.
                         If False, drawing mode disabled after each rectangle (old behavior).
        """
        self.drawing_mode = True
        self.keep_drawing_mode = keep_enabled
        self.setCursor(Qt.CrossCursor)
        self.update()
        self.drawingModeChanged.emit(True)

        logger.info(f"[FacePhotoViewer] Drawing mode enabled (keep_enabled={keep_enabled})")

    def disable_drawing_mode(self):
        """Disable drawing mode."""
        self.drawing_mode = False
        self.keep_drawing_mode = False
        self.draw_start = None
        self.draw_end = None
        self.setCursor(Qt.ArrowCursor)
        self.update()
        self.drawingModeChanged.emit(False)

        logger.info("[FacePhotoViewer] Drawing mode disabled")

    def mousePressEvent(self, event):
        """Handle mouse press to start drawing."""
        if self.drawing_mode and event.button() == Qt.LeftButton:
            self.draw_start = event.position().toPoint()
            self.draw_end = None

    def mouseMoveEvent(self, event):
        """Handle mouse move while drawing."""
        if self.drawing_mode and self.draw_start:
            self.draw_end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        """Handle mouse release to finish drawing."""
        if self.drawing_mode and event.button() == Qt.LeftButton and self.draw_start:
            self.draw_end = event.position().toPoint()

            # Calculate rectangle
            rect = QRect(self.draw_start, self.draw_end).normalized()

            if rect.width() > 20 and rect.height() > 20:
                # Convert from widget coordinates to image coordinates
                if self.pixmap:
                    # CRITICAL FIX: Calculate scaled pixmap and offsets
                    # (same calculation as paintEvent to ensure coordinate consistency)
                    scaled_pixmap = self.pixmap.scaled(
                        self.size(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )

                    # Calculate centering offsets
                    x_offset = (self.width() - scaled_pixmap.width()) // 2
                    y_offset = (self.height() - scaled_pixmap.height()) // 2

                    # Calculate scale factor (image pixels per display pixel)
                    scale = self.pixmap.width() / scaled_pixmap.width()

                    # Convert widget coords to image coords
                    # CRITICAL: Subtract offsets BEFORE scaling!
                    x = int((rect.x() - x_offset) * scale)
                    y = int((rect.y() - y_offset) * scale)
                    w = int(rect.width() * scale)
                    h = int(rect.height() * scale)

                    # Clamp to image bounds
                    x = max(0, min(x, self.pixmap.width() - w))
                    y = max(0, min(y, self.pixmap.height() - h))
                    w = min(w, self.pixmap.width() - x)
                    h = min(h, self.pixmap.height() - y)

                    # Convert to original image coordinates if downscaled
                    if hasattr(self, "display_scale") and self.display_scale < 1.0:
                        inv_scale = 1.0 / self.display_scale
                        x = int(x * inv_scale)
                        y = int(y * inv_scale)
                        w = int(w * inv_scale)
                        h = int(h * inv_scale)

                    # Emit signal
                    self.manualFaceAdded.emit((x, y, w, h))

                    logger.info(f"[FacePhotoViewer] Manual face drawn: {(x, y, w, h)} (offsets: {x_offset}, {y_offset}, scale: {scale:.2f})")

            # ENHANCEMENT #1: Keep drawing mode enabled if flag is set
            # This allows user to draw multiple faces without re-clicking "Add Manual Face"
            if not self.keep_drawing_mode:
                # Old behavior: disable after each draw
                self.drawing_mode = False
                self.setCursor(Qt.ArrowCursor)
                self.drawingModeChanged.emit(False)

            # Always reset drawing start/end for next rectangle
            self.draw_start = None
            self.draw_end = None
            self.update()

    def paintEvent(self, event):
        """Paint the photo with face rectangles overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.pixmap:
            painter.drawText(self.rect(), Qt.AlignCenter, "Failed to load photo")
            return

        # Draw photo scaled to fit
        scaled_pixmap = self.pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        # Center the pixmap
        x_offset = (self.width() - scaled_pixmap.width()) // 2
        y_offset = (self.height() - scaled_pixmap.height()) // 2

        painter.drawPixmap(x_offset, y_offset, scaled_pixmap)

        # Calculate scale factor for rectangles
        scale = scaled_pixmap.width() / self.pixmap.width()

        # Draw detected face rectangles (GREEN) - FIX #2
        pen = QPen(QColor(52, 168, 83), 3)  # Green
        painter.setPen(pen)

        for face in self.detected_faces:
            bbox = face.get('bbox')
            if bbox:
                x, y, w, h = bbox

                rect = QRect(
                    int(x * scale) + x_offset,
                    int(y * scale) + y_offset,
                    int(w * scale),
                    int(h * scale)
                )
                painter.drawRect(rect)

                # Draw person name label
                painter.setFont(QFont("Arial", 10, QFont.Bold))
                painter.fillRect(
                    rect.x(), rect.y() - 20,
                    len(face['person_name']) * 7, 18,
                    QColor(52, 168, 83, 200)
                )
                painter.setPen(QColor(255, 255, 255))  # White text
                painter.drawText(rect.x() + 3, rect.y() - 6, face['person_name'])
                painter.setPen(QColor(52, 168, 83))  # Back to green

        # Draw manual face rectangles (RED)
        pen = QPen(QColor(234, 67, 53), 3)  # Red
        painter.setPen(pen)

        for face in self.manual_faces:
            bbox = face['bbox']
            x, y, w, h = bbox

            rect = QRect(
                int(x * scale) + x_offset,
                int(y * scale) + y_offset,
                int(w * scale),
                int(h * scale)
            )
            painter.drawRect(rect)

            # Draw "Manual" label
            painter.setFont(QFont("Arial", 10, QFont.Bold))
            painter.fillRect(rect.x(), rect.y() - 20, 55, 18, QColor(234, 67, 53, 200))
            painter.setPen(QColor(255, 255, 255))  # White text
            painter.drawText(rect.x() + 3, rect.y() - 6, "Manual")

        # Draw current drawing rectangle (BLUE dashed)
        if self.drawing_mode and self.draw_start and self.draw_end:
            pen = QPen(QColor(26, 115, 232), 2, Qt.DashLine)  # Blue dashed
            painter.setPen(pen)
            rect = QRect(self.draw_start, self.draw_end).normalized()
            painter.drawRect(rect)


if __name__ == '__main__':
    # Test dialog
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Example usage (requires valid photo path)
    dialog = FaceCropEditor(
        photo_path="/path/to/photo.jpg",
        project_id=1
    )
    dialog.faceCropsUpdated.connect(lambda: print("Face crops updated!"))

    if dialog.exec():
        print("✅ Changes saved")
    else:
        print("❌ Cancelled")

    sys.exit(0)

