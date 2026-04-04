#!/usr/bin/env python3
"""
Face Quality Dashboard - Analytics and quality review for face detection.

Provides insights into face detection coverage, quality distribution, and
tools to review and correct face detections.

Features:
- Statistics overview (total faces, people count, quality distribution)
- List of low-quality faces for review
- List of photos with no faces detected
- Manual face crop editor for corrections
- Best practice: Make data quality transparent and actionable

Author: Claude Code
Date: December 17, 2025
"""

import logging
import os
from typing import List, Dict, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QWidget, QProgressBar, QMessageBox, QScrollArea
)
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt, Signal

from reference_db import ReferenceDB

logger = logging.getLogger(__name__)


class FaceQualityDashboard(QDialog):
    """
    Dashboard showing face detection quality metrics and review tools.

    Follows best practice: Make data quality transparent and actionable.
    """

    # Signals for launching review tools
    reviewLowQualityRequested = Signal()
    reviewPhotosWithoutFacesRequested = Signal()
    manualCropRequested = Signal(str)  # photo_path

    def __init__(self, project_id: int, parent=None):
        """
        Initialize face quality dashboard.

        Args:
            project_id: Current project ID
            parent: Parent widget
        """
        super().__init__(parent)

        self.project_id = project_id
        self.stats = {}

        self.setWindowTitle("Face Detection Quality Dashboard")
        self.setModal(False)
        self.resize(900, 700)

        self._load_statistics()
        self._create_ui()

    def _load_statistics(self):
        """Load face detection statistics from database."""
        db = ReferenceDB()

        try:
            with db._connect() as conn:
                cur = conn.cursor()

                # Check if quality_score column exists
                cur.execute("PRAGMA table_info(face_crops)")
                columns = {row[1] for row in cur.fetchall()}
                has_quality_score = 'quality_score' in columns

                # Total photos in project
                cur.execute("""
                    SELECT COUNT(DISTINCT path)
                    FROM photo_metadata
                    WHERE project_id = ?
                """, (self.project_id,))
                total_photos = cur.fetchone()[0] or 0

                # Photos with faces detected
                cur.execute("""
                    SELECT COUNT(DISTINCT fc.image_path)
                    FROM face_crops fc
                    WHERE fc.project_id = ?
                """, (self.project_id,))
                photos_with_faces = cur.fetchone()[0] or 0

                # Total faces detected
                cur.execute("""
                    SELECT COUNT(*)
                    FROM face_crops
                    WHERE project_id = ?
                """, (self.project_id,))
                total_faces = cur.fetchone()[0] or 0

                # Unique people (face clusters)
                cur.execute("""
                    SELECT COUNT(DISTINCT branch_key)
                    FROM face_branch_reps
                    WHERE project_id = ?
                """, (self.project_id,))
                unique_people = cur.fetchone()[0] or 0

                # Unnamed people clusters
                cur.execute("""
                    SELECT COUNT(*)
                    FROM face_branch_reps
                    WHERE project_id = ? AND (label IS NULL OR label = '')
                """, (self.project_id,))
                unnamed_people = cur.fetchone()[0] or 0

                # Quality distribution (if quality_score exists)
                quality_stats = None
                if has_quality_score:
                    cur.execute("""
                        SELECT
                            COUNT(CASE WHEN quality_score >= 0.8 THEN 1 END) as excellent,
                            COUNT(CASE WHEN quality_score >= 0.6 AND quality_score < 0.8 THEN 1 END) as good,
                            COUNT(CASE WHEN quality_score >= 0.4 AND quality_score < 0.6 THEN 1 END) as fair,
                            COUNT(CASE WHEN quality_score < 0.4 THEN 1 END) as poor,
                            AVG(quality_score) as avg_quality
                        FROM face_crops
                        WHERE project_id = ? AND quality_score IS NOT NULL AND quality_score > 0
                    """, (self.project_id,))
                    row = cur.fetchone()
                    if row:
                        quality_stats = {
                            'excellent': row[0] or 0,
                            'good': row[1] or 0,
                            'fair': row[2] or 0,
                            'poor': row[3] or 0,
                            'avg_quality': row[4] or 0.0
                        }

                self.stats = {
                    'total_photos': total_photos,
                    'photos_with_faces': photos_with_faces,
                    'photos_without_faces': total_photos - photos_with_faces,
                    'total_faces': total_faces,
                    'unique_people': unique_people,
                    'unnamed_people': unnamed_people,
                    'quality_stats': quality_stats,
                    'has_quality_score': has_quality_score,
                    'coverage_percent': (photos_with_faces / total_photos * 100) if total_photos > 0 else 0
                }

                logger.info(f"[FaceQualityDashboard] Loaded statistics: {self.stats}")

        except Exception as e:
            logger.error(f"[FaceQualityDashboard] Failed to load statistics: {e}")
            QMessageBox.critical(self, "Error", f"Failed to load statistics:\n{e}")

    def _create_ui(self):
        """Create the dashboard UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header
        header = QLabel("Face Detection Quality Dashboard")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Tab widget for different views
        tabs = QTabWidget()
        tabs.addTab(self._create_overview_tab(), "📊 Overview")
        tabs.addTab(self._create_quality_tab(), "⭐ Quality Review")
        tabs.addTab(self._create_missing_faces_tab(), "🔍 Missing Faces")
        layout.addWidget(tabs, 1)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignRight)

    def _create_overview_tab(self) -> QWidget:
        """Create overview statistics tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Coverage statistics
        coverage_group = QGroupBox("📸 Face Detection Coverage")
        coverage_layout = QVBoxLayout()

        total_photos = self.stats['total_photos']
        photos_with_faces = self.stats['photos_with_faces']
        photos_without_faces = self.stats['photos_without_faces']
        coverage_percent = self.stats['coverage_percent']

        coverage_layout.addWidget(self._create_stat_label(
            f"Total Photos: {total_photos:,}", "Total number of photos in project"
        ))
        coverage_layout.addWidget(self._create_stat_label(
            f"Photos with Faces: {photos_with_faces:,} ({coverage_percent:.1f}%)",
            "Photos where at least one face was detected"
        ))
        coverage_layout.addWidget(self._create_stat_label(
            f"Photos without Faces: {photos_without_faces:,}",
            "Photos with no faces detected (may need manual review)",
            color="#ea4335" if photos_without_faces > 0 else "#5f6368"
        ))

        # Coverage progress bar
        progress = QProgressBar()
        progress.setMaximum(100)
        progress.setValue(int(coverage_percent))
        progress.setFormat(f"{coverage_percent:.1f}% Coverage")
        progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #dadce0;
                border-radius: 4px;
                text-align: center;
                height: 24px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1a73e8, stop:1 #34a853);
            }
        """)
        coverage_layout.addWidget(progress)

        coverage_group.setLayout(coverage_layout)
        layout.addWidget(coverage_group)

        # Face statistics
        faces_group = QGroupBox("👥 Face Statistics")
        faces_layout = QVBoxLayout()

        total_faces = self.stats['total_faces']
        unique_people = self.stats['unique_people']
        unnamed_people = self.stats['unnamed_people']

        faces_layout.addWidget(self._create_stat_label(
            f"Total Faces Detected: {total_faces:,}",
            "Total number of face crops in database"
        ))
        faces_layout.addWidget(self._create_stat_label(
            f"Unique People: {unique_people:,}",
            "Number of distinct person clusters"
        ))
        faces_layout.addWidget(self._create_stat_label(
            f"Named People: {unique_people - unnamed_people:,}",
            "People with names assigned"
        ))
        faces_layout.addWidget(self._create_stat_label(
            f"Unnamed People: {unnamed_people:,}",
            "People clusters without names (consider naming them)",
            color="#ea4335" if unnamed_people > 0 else "#5f6368"
        ))

        if total_faces > 0 and unique_people > 0:
            avg_faces_per_person = total_faces / unique_people
            faces_layout.addWidget(self._create_stat_label(
                f"Avg Faces per Person: {avg_faces_per_person:.1f}",
                "Average number of face crops per person cluster"
            ))

        faces_group.setLayout(faces_layout)
        layout.addWidget(faces_group)

        # Quality statistics (if available)
        if self.stats['has_quality_score'] and self.stats['quality_stats']:
            quality_group = QGroupBox("⭐ Quality Distribution")
            quality_layout = QVBoxLayout()

            qs = self.stats['quality_stats']
            total_scored = qs['excellent'] + qs['good'] + qs['fair'] + qs['poor']

            if total_scored > 0:
                quality_layout.addWidget(self._create_quality_bar(
                    "Excellent (≥80%)", qs['excellent'], total_scored, "#34a853"
                ))
                quality_layout.addWidget(self._create_quality_bar(
                    "Good (60-80%)", qs['good'], total_scored, "#fbbc04"
                ))
                quality_layout.addWidget(self._create_quality_bar(
                    "Fair (40-60%)", qs['fair'], total_scored, "#ff9800"
                ))
                quality_layout.addWidget(self._create_quality_bar(
                    "Poor (<40%)", qs['poor'], total_scored, "#ea4335"
                ))

                quality_layout.addWidget(self._create_stat_label(
                    f"Average Quality: {qs['avg_quality']*100:.1f}%",
                    "Overall average quality score"
                ))

            quality_group.setLayout(quality_layout)
            layout.addWidget(quality_group)

        layout.addStretch()
        return widget

    def _create_quality_tab(self) -> QWidget:
        """Create quality review tab showing low-quality faces."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Info label
        info = QLabel("Review low-quality face detections and correct them if needed.")
        info.setStyleSheet("color: #5f6368; padding: 8px;")
        layout.addWidget(info)

        # Load low-quality faces
        low_quality_faces = self._load_low_quality_faces()

        if not low_quality_faces:
            placeholder = QLabel("✅ No low-quality faces found!\n\nAll face detections have good quality scores.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("padding: 40px; color: #34a853; font-size: 12pt;")
            layout.addWidget(placeholder)
        else:
            # Table showing low-quality faces
            table = QTableWidget()
            table.setColumnCount(5)
            table.setHorizontalHeaderLabels(["Photo", "Person", "Quality", "Size", "Action"])
            table.setRowCount(len(low_quality_faces))
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table.setStyleSheet("""
                QTableWidget {
                    gridline-color: #dadce0;
                    background: white;
                }
                QHeaderView::section {
                    background: #f8f9fa;
                    padding: 8px;
                    border: none;
                    border-bottom: 2px solid #dadce0;
                    font-weight: bold;
                }
            """)

            for row_idx, face in enumerate(low_quality_faces):
                # Photo path
                photo_name = os.path.basename(face['image_path'])
                table.setItem(row_idx, 0, QTableWidgetItem(photo_name))

                # Person name
                person_name = face['display_name'] or "Unnamed"
                table.setItem(row_idx, 1, QTableWidgetItem(person_name))

                # Quality score
                quality = face['quality_score'] or 0.0
                quality_item = QTableWidgetItem(f"{quality*100:.1f}%")
                if quality < 0.3:
                    quality_item.setForeground(Qt.red)
                table.setItem(row_idx, 2, quality_item)

                # Face size
                bbox = face['bbox']
                if bbox:
                    parts = bbox.split(',')
                    if len(parts) == 4:
                        try:
                            w = int(float(parts[2]))
                            h = int(float(parts[3]))
                            size_item = QTableWidgetItem(f"{w}×{h}px")
                            table.setItem(row_idx, 3, size_item)
                        except:
                            table.setItem(row_idx, 3, QTableWidgetItem("—"))
                else:
                    table.setItem(row_idx, 3, QTableWidgetItem("—"))

                # Action button
                # CRITICAL FIX: Check if image_path is a face crop (corrupted data)
                # Face crops cannot be manually cropped - need original photo
                is_face_crop = '/face_crops/' in face['image_path'].replace('\\', '/')

                if is_face_crop:
                    # Disable button for face crops (data issue)
                    review_btn = QPushButton("⚠️ Invalid")
                    review_btn.setEnabled(False)
                    review_btn.setToolTip("Cannot review: image_path points to face crop instead of original photo.\nThis is a data corruption issue.")
                    logger.warning(f"[FaceQualityDashboard] Face {face['id']} has corrupted image_path (points to face crop): {face['image_path']}")
                else:
                    review_btn = QPushButton("Review")
                    review_btn.setProperty("photo_path", face['image_path'])
                    review_btn.clicked.connect(lambda checked, path=face['image_path']: self.manualCropRequested.emit(path))
                review_btn.setStyleSheet("""
                    QPushButton {
                        background: #1a73e8;
                        color: white;
                        border: none;
                        padding: 6px 12px;
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        background: #1557b0;
                    }
                """)
                table.setCellWidget(row_idx, 4, review_btn)

            layout.addWidget(table, 1)

            # Summary label
            summary = QLabel(f"Found {len(low_quality_faces)} low-quality face(s) that may need review.")
            summary.setStyleSheet("color: #5f6368; padding: 8px;")
            layout.addWidget(summary)

        return widget

    def _create_missing_faces_tab(self) -> QWidget:
        """Create tab showing photos with no faces detected."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Info label
        info = QLabel("Photos where no faces were detected. These may need manual review.")
        info.setStyleSheet("color: #5f6368; padding: 8px;")
        layout.addWidget(info)

        # Load photos without faces
        photos_without_faces = self._load_photos_without_faces()

        if not photos_without_faces:
            placeholder = QLabel("✅ All photos have faces detected!\n\nNo photos need review.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("padding: 40px; color: #34a853; font-size: 12pt;")
            layout.addWidget(placeholder)
        else:
            # Table showing photos
            table = QTableWidget()
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["Photo", "Date", "Size", "Action"])
            table.setRowCount(len(photos_without_faces))
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table.setStyleSheet("""
                QTableWidget {
                    gridline-color: #dadce0;
                    background: white;
                }
                QHeaderView::section {
                    background: #f8f9fa;
                    padding: 8px;
                    border: none;
                    border-bottom: 2px solid #dadce0;
                    font-weight: bold;
                }
            """)

            for row_idx, photo in enumerate(photos_without_faces):
                # Photo name
                photo_name = os.path.basename(photo['path'])
                table.setItem(row_idx, 0, QTableWidgetItem(photo_name))

                # Date
                date = photo['date_taken'] or "Unknown"
                table.setItem(row_idx, 1, QTableWidgetItem(date))

                # Size
                w = photo['width'] or 0
                h = photo['height'] or 0
                table.setItem(row_idx, 2, QTableWidgetItem(f"{w}×{h}px"))

                # Action button
                # CRITICAL FIX: Check if path is a face crop (should never happen, but defensive)
                is_face_crop = '/face_crops/' in photo['path'].replace('\\', '/')

                if is_face_crop:
                    # Disable button for face crops (data issue)
                    review_btn = QPushButton("⚠️ Invalid")
                    review_btn.setEnabled(False)
                    review_btn.setToolTip("Cannot crop: photo path points to face crop instead of original photo.\nThis is a data corruption issue.")
                    logger.warning(f"[FaceQualityDashboard] Photo has corrupted path (points to face crop): {photo['path']}")
                else:
                    review_btn = QPushButton("Manual Crop")
                    review_btn.setProperty("photo_path", photo['path'])
                    review_btn.clicked.connect(lambda checked, path=photo['path']: self.manualCropRequested.emit(path))
                review_btn.setStyleSheet("""
                    QPushButton {
                        background: #1a73e8;
                        color: white;
                        border: none;
                        padding: 6px 12px;
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        background: #1557b0;
                    }
                """)
                table.setCellWidget(row_idx, 3, review_btn)

            layout.addWidget(table, 1)

            # Summary label
            summary = QLabel(f"Found {len(photos_without_faces)} photo(s) with no faces detected.")
            summary.setStyleSheet("color: #5f6368; padding: 8px;")
            layout.addWidget(summary)

        return widget

    def _create_stat_label(self, main_text: str, description: str, color: str = "#202124") -> QWidget:
        """Create a styled statistics label."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        main_label = QLabel(main_text)
        main_label.setStyleSheet(f"color: {color}; font-size: 11pt; font-weight: 600;")
        layout.addWidget(main_label)

        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #5f6368; font-size: 9pt;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        return container

    def _create_quality_bar(self, label: str, count: int, total: int, color: str) -> QWidget:
        """Create a quality distribution bar."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        # Label
        label_widget = QLabel(label)
        label_widget.setFixedWidth(120)
        label_widget.setStyleSheet("color: #202124; font-size: 10pt;")
        layout.addWidget(label_widget)

        # Progress bar
        progress = QProgressBar()
        progress.setMaximum(total)
        progress.setValue(count)
        percent = (count / total * 100) if total > 0 else 0
        progress.setFormat(f"{count} ({percent:.1f}%)")
        progress.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #dadce0;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background: {color};
            }}
        """)
        layout.addWidget(progress, 1)

        return container

    def _load_low_quality_faces(self, threshold: float = 0.4) -> List[Dict]:
        """Load faces with quality scores below threshold."""
        db = ReferenceDB()

        try:
            with db._connect() as conn:
                cur = conn.cursor()

                # Check if quality_score exists
                cur.execute("PRAGMA table_info(face_crops)")
                columns = {row[1] for row in cur.fetchall()}
                if 'quality_score' not in columns:
                    return []

                # Get low-quality faces
                cur.execute("""
                    SELECT
                        fc.id,
                        fc.image_path,
                        fc.bbox,
                        fc.quality_score,
                        fc.branch_key,
                        fbr.label as display_name
                    FROM face_crops fc
                    LEFT JOIN face_branch_reps fbr ON fc.branch_key = fbr.branch_key
                        AND fc.project_id = fbr.project_id
                    WHERE fc.project_id = ?
                        AND fc.quality_score IS NOT NULL
                        AND fc.quality_score < ?
                    ORDER BY fc.quality_score ASC
                    LIMIT 100
                """, (self.project_id, threshold))

                rows = cur.fetchall()
                return [
                    {
                        'id': row[0],
                        'image_path': row[1],
                        'bbox': row[2],
                        'quality_score': row[3],
                        'branch_key': row[4],
                        'display_name': row[5]
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error(f"[FaceQualityDashboard] Failed to load low-quality faces: {e}")
            return []

    def _load_photos_without_faces(self, limit: int = 100) -> List[Dict]:
        """Load photos where no faces were detected."""
        db = ReferenceDB()

        try:
            with db._connect() as conn:
                cur = conn.cursor()

                # Get photos without faces
                cur.execute("""
                    SELECT
                        pm.path,
                        pm.date_taken,
                        pm.width,
                        pm.height
                    FROM photo_metadata pm
                    WHERE pm.project_id = ?
                        AND NOT EXISTS (
                            SELECT 1 FROM face_crops fc
                            WHERE fc.image_path = pm.path
                        )
                    ORDER BY pm.date_taken DESC
                    LIMIT ?
                """, (self.project_id, limit))

                rows = cur.fetchall()
                return [
                    {
                        'path': row[0],
                        'date_taken': row[1],
                        'width': row[2],
                        'height': row[3]
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error(f"[FaceQualityDashboard] Failed to load photos without faces: {e}")
            return []


if __name__ == '__main__':
    # Test dialog
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    dialog = FaceQualityDashboard(project_id=1)
    dialog.manualCropRequested.connect(lambda path: print(f"Manual crop requested for: {path}"))
    dialog.show()

    sys.exit(app.exec())
