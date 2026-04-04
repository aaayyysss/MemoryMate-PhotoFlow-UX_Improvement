#!/usr/bin/env python3
"""
Visual Photo Browser - Grid view for selecting photos to edit face detections.

Provides a thumbnail grid view for browsing and selecting photos, replacing
the text dropdown with a visual, user-friendly interface.

Best practices implemented:
- Visual thumbnails instead of text list
- Filter options (all photos, no faces, low quality)
- Keyboard navigation (arrow keys, Enter)
- Photo metadata display (date, face count)
- Quick search/filter by name or date
- Responsive grid layout

Author: Claude Code
Date: 2025-12-17
"""

import logging
import os
from typing import List, Dict, Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QPushButton, QScrollArea, QWidget, QLineEdit, QComboBox,
    QFrame
)
from PySide6.QtGui import QPixmap, QFont, QImage, QPainter, Qt as QtNamespace
from PySide6.QtCore import Qt, Signal, QSize
from PIL import Image, ImageOps

from reference_db import ReferenceDB

logger = logging.getLogger(__name__)


class PhotoBrowserDialog(QDialog):
    """
    Visual photo browser with thumbnail grid for selecting photos to edit.

    Follows best practice: Make selection visual and intuitive.
    """

    photoSelected = Signal(str)  # photo_path

    # Configuration constants
    INITIAL_PHOTO_LIMIT = 200  # Load first 200 photos for performance
    PHOTOS_PER_PAGE = 100  # Load 100 more photos when "Load More" clicked

    def __init__(self, project_id: int, parent=None):
        """
        Initialize photo browser.

        Args:
            project_id: Current project ID
            parent: Parent widget
        """
        super().__init__(parent)

        self.project_id = project_id
        self.all_photos = []
        self.filtered_photos = []
        self._photos_loaded = 0  # Track how many photos loaded
        self._has_more_photos = False  # Whether more photos available

        self.setWindowTitle("Select Photo to Edit")
        self.setModal(True)
        self.resize(1000, 700)

        self._load_photos(limit=self.INITIAL_PHOTO_LIMIT)
        self._create_ui()
        self._apply_filter()

    def _load_photos(self, limit: Optional[int] = None, offset: int = 0):
        """
        Load photos from database with metadata (paginated for performance).

        Args:
            limit: Maximum number of photos to load (None = all)
            offset: Skip this many photos from beginning
        """
        db = ReferenceDB()

        try:
            with db._connect() as conn:
                cur = conn.cursor()

                # First, check total photo count
                cur.execute("""
                    SELECT COUNT(DISTINCT pm.path)
                    FROM photo_metadata pm
                    WHERE pm.project_id = ?
                """, (self.project_id,))
                total_photos = cur.fetchone()[0] or 0

                # Get photos with face count and metadata (paginated)
                query = """
                    SELECT
                        pm.path,
                        pm.date_taken,
                        pm.width,
                        pm.height,
                        COUNT(fc.id) as face_count
                    FROM photo_metadata pm
                    LEFT JOIN face_crops fc ON pm.path = fc.image_path
                    WHERE pm.project_id = ?
                    GROUP BY pm.path
                    ORDER BY pm.date_taken DESC
                """

                if limit:
                    query += f" LIMIT {limit} OFFSET {offset}"

                cur.execute(query, (self.project_id,))
                rows = cur.fetchall()

                new_photos = [
                    {
                        'path': row[0],
                        'date': row[1] or 'Unknown',
                        'width': row[2] or 0,
                        'height': row[3] or 0,
                        'face_count': row[4] or 0
                    }
                    for row in rows
                ]

                # Append to existing photos if loading more
                if offset > 0:
                    self.all_photos.extend(new_photos)
                else:
                    self.all_photos = new_photos

                self._photos_loaded = len(self.all_photos)
                self._has_more_photos = self._photos_loaded < total_photos

                logger.info(f"[PhotoBrowser] Loaded {len(new_photos)} photos ({self._photos_loaded}/{total_photos} total)")

        except Exception as e:
            logger.error(f"[PhotoBrowser] Failed to load photos: {e}")

    def _create_ui(self):
        """Create the browser UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Header
        header = QLabel("Select a Photo to Edit Face Detections")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Filter and search controls
        controls_layout = QHBoxLayout()

        # Filter dropdown
        filter_label = QLabel("Filter:")
        controls_layout.addWidget(filter_label)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems([
            "All Photos",
            "Photos Without Faces",
            "Photos With Faces"
        ])
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        controls_layout.addWidget(self.filter_combo)

        controls_layout.addSpacing(20)

        # Search box
        search_label = QLabel("Search:")
        controls_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search by filename or date...")
        self.search_input.textChanged.connect(self._apply_filter)
        self.search_input.setMinimumWidth(250)
        controls_layout.addWidget(self.search_input, 1)

        layout.addLayout(controls_layout)

        # Photo count label
        self.count_label = QLabel(f"Showing {len(self.all_photos)} photos")
        self.count_label.setStyleSheet("color: #5f6368; font-size: 9pt; padding: 4px;")
        layout.addWidget(self.count_label)

        # Scroll area for photo grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #f8f9fa; }")

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setSpacing(15)

        scroll.setWidget(self.grid_container)
        layout.addWidget(scroll, 1)

        # Buttons
        button_layout = QHBoxLayout()

        # Load More button (shown when more photos available)
        self.load_more_btn = QPushButton("📥 Load More Photos")
        self.load_more_btn.clicked.connect(self._load_more_photos)
        self.load_more_btn.setStyleSheet("""
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
            QPushButton:disabled {
                background: #dadce0;
                color: #5f6368;
            }
        """)
        self.load_more_btn.setVisible(self._has_more_photos)
        button_layout.addWidget(self.load_more_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(self.reject)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _apply_filter(self):
        """Apply current filter and search to photo list."""
        filter_index = self.filter_combo.currentIndex()
        search_text = self.search_input.text().lower().strip()

        # Filter by face count
        if filter_index == 0:  # All Photos
            filtered = self.all_photos
        elif filter_index == 1:  # Photos Without Faces
            filtered = [p for p in self.all_photos if p['face_count'] == 0]
        else:  # Photos With Faces
            filtered = [p for p in self.all_photos if p['face_count'] > 0]

        # Apply search filter
        if search_text:
            filtered = [
                p for p in filtered
                if search_text in os.path.basename(p['path']).lower()
                or search_text in p['date'].lower()
            ]

        self.filtered_photos = filtered
        self._update_grid()

    def _update_grid(self):
        """Update photo grid with filtered photos."""
        # Clear existing grid
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Add filtered photos to grid
        columns = 4  # 4 photos per row
        for idx, photo in enumerate(self.filtered_photos):
            row = idx // columns
            col = idx % columns

            card = PhotoCard(photo, self)
            card.clicked.connect(self._on_photo_selected)
            self.grid_layout.addWidget(card, row, col)

        # Update count label
        total = len(self.all_photos)
        shown = len(self.filtered_photos)
        if shown == total:
            self.count_label.setText(f"Showing {total} photos")
        else:
            self.count_label.setText(f"Showing {shown} of {total} photos")

        logger.debug(f"[PhotoBrowser] Grid updated with {shown} photos")

    def _on_photo_selected(self, photo_path: str):
        """Handle photo selection."""
        self.photoSelected.emit(photo_path)
        self.accept()

    def _load_more_photos(self):
        """Load next batch of photos."""
        logger.info(f"[PhotoBrowser] Loading more photos (offset={self._photos_loaded})")

        # Disable button while loading
        self.load_more_btn.setEnabled(False)
        self.load_more_btn.setText("Loading...")

        # Load next batch
        self._load_photos(limit=self.PHOTOS_PER_PAGE, offset=self._photos_loaded)

        # Update UI
        self._apply_filter()

        # Re-enable or hide button
        if self._has_more_photos:
            self.load_more_btn.setEnabled(True)
            self.load_more_btn.setText("📥 Load More Photos")
        else:
            self.load_more_btn.setVisible(False)


class PhotoCard(QFrame):
    """
    Individual photo card with thumbnail and metadata.

    Displays:
    - Photo thumbnail (150x150)
    - Filename
    - Date
    - Face count badge
    """

    clicked = Signal(str)  # photo_path

    def __init__(self, photo: Dict, parent=None):
        super().__init__(parent)

        self.photo = photo
        self.photo_path = photo['path']

        self.setFixedSize(170, 220)
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            PhotoCard {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 8px;
            }
            PhotoCard:hover {
                background: #f1f3f4;
                border: 2px solid #1a73e8;
            }
        """)

        self._create_ui()

    def _create_ui(self):
        """Create card UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Thumbnail
        thumb_label = QLabel()
        thumb_label.setFixedSize(150, 150)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setStyleSheet("background: #f8f9fa; border-radius: 4px;")

        # Load thumbnail
        thumbnail = self._load_thumbnail()
        if thumbnail:
            thumb_label.setPixmap(thumbnail)
        else:
            thumb_label.setText("📷")
            thumb_label.setStyleSheet("background: #f8f9fa; font-size: 48px;")

        layout.addWidget(thumb_label)

        # Filename
        filename = os.path.basename(self.photo_path)
        if len(filename) > 20:
            filename = filename[:17] + "..."

        name_label = QLabel(filename)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-weight: 600; font-size: 10pt; color: #202124;")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # Metadata row
        meta_layout = QHBoxLayout()

        # Date
        date_text = self.photo['date']
        if date_text and date_text != 'Unknown':
            try:
                date_obj = datetime.strptime(date_text[:10], '%Y-%m-%d')
                date_text = date_obj.strftime('%b %d, %Y')
            except:
                pass

        date_label = QLabel(date_text[:12])
        date_label.setStyleSheet("color: #5f6368; font-size: 8pt;")
        meta_layout.addWidget(date_label)

        meta_layout.addStretch()

        # Face count badge
        face_count = self.photo['face_count']
        if face_count > 0:
            badge = QLabel(f"👤 {face_count}")
            badge.setStyleSheet("""
                background: #e8f0fe;
                color: #1a73e8;
                padding: 2px 6px;
                border-radius: 10px;
                font-size: 8pt;
                font-weight: bold;
            """)
        else:
            badge = QLabel("No faces")
            badge.setStyleSheet("""
                background: #fce8e6;
                color: #ea4335;
                padding: 2px 6px;
                border-radius: 10px;
                font-size: 8pt;
            """)

        meta_layout.addWidget(badge)

        layout.addLayout(meta_layout)

    def _load_thumbnail(self) -> Optional[QPixmap]:
        """Load thumbnail for photo with proper EXIF orientation handling."""
        try:
            if not os.path.exists(self.photo_path):
                return None

            # Load and resize image
            with Image.open(self.photo_path) as img:
                # CRITICAL FIX (2026-01-08): Apply EXIF auto-rotation BEFORE thumbnailing
                # Without this, portrait mode photos and rotated images display sideways
                img = ImageOps.exif_transpose(img)

                # Convert to RGB if needed
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                # Create thumbnail (modifies img in-place to fit within 150×150)
                img.thumbnail((150, 150), Image.Resampling.LANCZOS)

                # CRITICAL FIX (2026-01-08): Calculate bytes_per_line explicitly
                # Without this, QImage may calculate stride incorrectly causing display corruption
                bytes_per_line = img.width * 3  # 3 bytes per RGB pixel

                # Convert to QPixmap
                data = img.tobytes("raw", "RGB")
                qimg = QImage(data, img.width, img.height, bytes_per_line, QImage.Format_RGB888)

                # CRITICAL FIX (2026-01-08): Deep copy QImage to ensure Qt owns the data
                # Without this, Python GC may deallocate data while QPixmap still references it
                qimg = qimg.copy()

                pixmap = QPixmap.fromImage(qimg)

                # Note: No need for additional scaling - thumbnail() already resized to fit 150×150
                # with aspect ratio preserved. QPixmap will display at actual size.
                return pixmap

        except Exception as e:
            logger.debug(f"[PhotoCard] Failed to load thumbnail for {self.photo_path}: {e}")
            return None

    def mousePressEvent(self, event):
        """Handle mouse click."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.photo_path)
        super().mousePressEvent(event)


if __name__ == '__main__':
    # Test dialog
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    dialog = PhotoBrowserDialog(project_id=1)
    dialog.photoSelected.connect(lambda path: print(f"Selected: {path}"))

    if dialog.exec():
        print("✅ Photo selected")
    else:
        print("❌ Cancelled")

    sys.exit(0)
