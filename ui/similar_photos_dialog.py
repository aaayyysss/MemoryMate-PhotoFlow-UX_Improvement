"""
SimilarPhotosDialog - Visual Similarity Browser

Version: 2.0.0
Date: 2026-02-12

Show visually similar photos with threshold control.

v2.0.0 Changes:
- Uses SafeImageLoader for memory-safe, capped-size thumbnail loading
- Grid thumbnails capped at 256px max edge (never full resolution)
- Prevents RAM blow-up from decoding 20+ full-res images simultaneously
- Follows Google Photos / Lightroom pattern: thumbnails only in grid

Features:
- Grid view of similar photos (256px thumbnails)
- Similarity score display
- Threshold slider (0.0 to 1.0)
- Real-time filtering
- Double-click to open photo
- Project-aware canonical model support

Usage:
    dialog = SimilarPhotosDialog(
        reference_photo_id=123,
        project_id=current_project_id,
        parent=parent_widget
    )
    dialog.exec()
"""

from typing import Optional, List
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QPushButton, QWidget, QScrollArea,
    QFrame, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QPixmap, QMouseEvent, QCursor

from services.photo_similarity_service import (
    get_photo_similarity_service,
    get_photo_similarity_service_for_project,
    PhotoSimilarityService,
    SimilarPhoto,
    EmbeddingNotReadyError
)
from logging_config import get_logger

logger = get_logger(__name__)

# Hard cap for grid thumbnails — NEVER decode full resolution for a grid cell
GRID_THUMBNAIL_MAX_DIM = 256

# Display size for thumbnail widget
THUMBNAIL_DISPLAY_SIZE = 150


class PhotoThumbnail(QFrame):
    """
    Thumbnail widget for similar photo.

    Shows thumbnail, similarity score, and handles click events.

    IMPORTANT: Uses SafeImageLoader to decode at GRID_THUMBNAIL_MAX_DIM max,
    preventing RAM blow-up from full-resolution decodes.
    """

    clicked = Signal(int)  # photo_id

    def __init__(self, similar_photo: SimilarPhoto, display_size: int = THUMBNAIL_DISPLAY_SIZE, parent=None):
        super().__init__(parent)
        self.photo_id = similar_photo.photo_id
        self.similarity_score = similar_photo.similarity_score
        self.file_path = similar_photo.file_path or similar_photo.thumbnail_path or ''
        self._display_size = display_size

        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setLineWidth(1)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to view in lightbox")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Thumbnail
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setFixedSize(self._display_size, self._display_size)
        self.thumbnail_label.setStyleSheet("background-color: #f0f0f0;")

        # Load thumbnail via SafeImageLoader (capped at 256px, never full resolution)
        image_path = similar_photo.thumbnail_path or similar_photo.file_path
        if image_path and Path(image_path).exists():
            try:
                from services.safe_image_loader import safe_decode_qimage

                # Decode at capped size — this is the key RAM saver
                qimage = safe_decode_qimage(
                    str(image_path),
                    max_dim=GRID_THUMBNAIL_MAX_DIM,
                    enable_retry_ladder=True,
                )

                if not qimage.isNull():
                    pixmap = QPixmap.fromImage(qimage)
                    pixmap = pixmap.scaled(
                        THUMBNAIL_DISPLAY_SIZE, THUMBNAIL_DISPLAY_SIZE,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    self.thumbnail_label.setPixmap(pixmap)
                else:
                    self.thumbnail_label.setText("No Preview")
            except Exception as e:
                logger.warning(f"[PhotoThumbnail] Failed to load thumbnail: {e}")
                self.thumbnail_label.setText("Load Error")
        else:
            self.thumbnail_label.setText("No Thumbnail")

        # Score label
        score_percent = int(self.similarity_score * 100)
        self.score_label = QLabel(f"{score_percent}% similar")
        self.score_label.setAlignment(Qt.AlignCenter)

        # Color-code by similarity
        if self.similarity_score >= 0.9:
            color = "#2ecc71"  # Green
        elif self.similarity_score >= 0.8:
            color = "#3498db"  # Blue
        elif self.similarity_score >= 0.7:
            color = "#f39c12"  # Orange
        else:
            color = "#95a5a6"  # Gray

        self.score_label.setStyleSheet(f"color: {color}; font-weight: bold;")

        layout.addWidget(self.thumbnail_label)
        layout.addWidget(self.score_label)

    def mousePressEvent(self, event: QMouseEvent):
        """Handle click to open photo in lightbox (single click, Google Photos style)."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.photo_id)
        super().mousePressEvent(event)

    def set_display_size(self, size: int):
        """Update display size for zoom controls."""
        self._display_size = size
        self.thumbnail_label.setFixedSize(size, size)

        # Reload thumbnail at new size
        if self.file_path and Path(self.file_path).exists():
            try:
                from services.safe_image_loader import safe_decode_qimage
                qimage = safe_decode_qimage(
                    str(self.file_path),
                    max_dim=max(size, GRID_THUMBNAIL_MAX_DIM),
                    enable_retry_ladder=True,
                )
                if not qimage.isNull():
                    pixmap = QPixmap.fromImage(qimage)
                    pixmap = pixmap.scaled(
                        size, size,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    self.thumbnail_label.setPixmap(pixmap)
            except Exception:
                pass


class SimilarPhotosDialog(QDialog):
    """
    Dialog for browsing visually similar photos.

    Shows grid of similar photos with threshold control.

    DESIGN RULE (Google Photos / Lightroom pattern):
    - Grid uses thumbnails only (256px max edge), never full resolution
    - Full resolution decode is NEVER allowed for UI display in this dialog
    - This prevents the dialog from decoding 20+ full-res images at once

    IMPORTANT: Always provide project_id to ensure correct canonical model is used.
    Without project_id, the dialog falls back to default model which may not match
    the embeddings in the database.
    """

    photo_clicked = Signal(int)  # photo_id

    def __init__(self, reference_photo_id: int, project_id: Optional[int] = None, parent=None):
        """
        Initialize similar photos dialog.

        Args:
            reference_photo_id: ID of the reference photo to find similar photos for
            project_id: Project ID (REQUIRED for correct canonical model usage)
            parent: Parent widget
        """
        super().__init__(parent)
        self.reference_photo_id = reference_photo_id
        self.project_id = project_id
        self.all_results: List[SimilarPhoto] = []
        self.current_threshold = 0.7

        # Use project-aware service if project_id is provided
        # This ensures we use the correct canonical model for embeddings
        if project_id is not None:
            self.similarity_service = get_photo_similarity_service_for_project(project_id)
            logger.info(
                f"[SimilarPhotosDialog] Using project-aware service for project {project_id}"
            )
        else:
            # Fallback to default service (may cause model mismatch!)
            self.similarity_service = get_photo_similarity_service()
            logger.warning(
                f"[SimilarPhotosDialog] No project_id provided! "
                f"Using default service - may cause model mismatch."
            )

        self.setWindowTitle(f"Similar Photos (Reference: Photo #{reference_photo_id})")
        self.resize(900, 700)

        self._init_ui()
        self._load_similar_photos()

    def _init_ui(self):
        """Initialize UI components."""
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()

        self.title_label = QLabel("Finding similar photos...")
        self.title_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        # Coverage info
        self.coverage_label = QLabel()
        header_layout.addWidget(self.coverage_label)

        layout.addLayout(header_layout)

        # Threshold control
        threshold_layout = QHBoxLayout()

        threshold_layout.addWidget(QLabel("Similarity Threshold:"))

        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(50)  # 0.5
        self.threshold_slider.setMaximum(100)  # 1.0
        self.threshold_slider.setValue(70)  # 0.7
        self.threshold_slider.setTickPosition(QSlider.TicksBelow)
        self.threshold_slider.setTickInterval(10)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        threshold_layout.addWidget(self.threshold_slider, 1)

        self.threshold_value_label = QLabel("70%")
        self.threshold_value_label.setMinimumWidth(50)
        threshold_layout.addWidget(self.threshold_value_label)

        layout.addLayout(threshold_layout)

        # Zoom controls (Lightroom / Excire style)
        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("Zoom:"))

        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedSize(24, 24)
        zoom_out_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() - 20))
        zoom_layout.addWidget(zoom_out_btn)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(80)
        self.zoom_slider.setMaximum(300)
        self.zoom_slider.setValue(THUMBNAIL_DISPLAY_SIZE)
        self.zoom_slider.setFixedWidth(100)
        self.zoom_slider.setToolTip("Adjust thumbnail size")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_layout.addWidget(self.zoom_slider)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedSize(24, 24)
        zoom_in_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() + 20))
        zoom_layout.addWidget(zoom_in_btn)

        self.zoom_value_label = QLabel(f"{THUMBNAIL_DISPLAY_SIZE}px")
        self.zoom_value_label.setMinimumWidth(40)
        zoom_layout.addWidget(self.zoom_value_label)

        zoom_layout.addStretch()
        layout.addLayout(zoom_layout)

        # Results info
        self.results_label = QLabel()
        layout.addWidget(self.results_label)

        # Scroll area for thumbnails
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll.setWidget(self.grid_widget)
        layout.addWidget(scroll, 1)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _load_similar_photos(self):
        """Load similar photos from service."""
        try:
            # Check coverage
            coverage = self.similarity_service.get_embedding_coverage()
            self.coverage_label.setText(
                f"{coverage['embedded_photos']}/{coverage['total_photos']} photos embedded "
                f"({coverage['coverage_percent']:.1f}%) - Model: {coverage['model']}"
            )

            if coverage['embedded_photos'] < 2:
                self.title_label.setText("Not enough photos with embeddings")
                self.results_label.setText(
                    "Run embedding extraction to enable similarity search"
                )
                return

            # Load all results (we'll filter by threshold in UI)
            # CRITICAL: Pass project_id to enable:
            # - Exact duplicate exclusion (asset siblings)
            # - Project-specific embedding filtering
            # - Canonical model validation
            self.all_results = self.similarity_service.find_similar(
                photo_id=self.reference_photo_id,
                top_k=100,  # Get more results for threshold filtering
                threshold=0.5,  # Lower threshold to get more candidates
                include_metadata=True,
                project_id=self.project_id,  # Required for correct filtering
                exclude_exact_duplicates=True,  # Don't show asset siblings
                strict_model_check=True  # Ensure embedding model matches canonical
            )

            self.title_label.setText(f"Similar Photos (Photo #{self.reference_photo_id})")

            # Display filtered results
            self._update_display()

        except EmbeddingNotReadyError as e:
            # Specific error when embedding model doesn't match canonical model
            logger.warning(f"[SimilarPhotosDialog] Embedding not ready: {e}")
            self.title_label.setText("Embedding Index Required")
            self.results_label.setText(
                f"{str(e)}\n\n"
                f"The reference photo's embedding was created with a different model.\n"
                f"Please run 'Regenerate Semantic Index' from the Tools menu to fix this."
            )
            self.results_label.setStyleSheet("color: #e67e22;")  # Orange warning color

        except Exception as e:
            logger.error(f"[SimilarPhotosDialog] Failed to load similar photos: {e}", exc_info=True)
            self.title_label.setText("Error loading similar photos")
            self.results_label.setText(str(e))
            self.results_label.setStyleSheet("color: #e74c3c;")  # Red error color

    def _on_threshold_changed(self, value: int):
        """Handle threshold slider change."""
        self.current_threshold = value / 100.0
        self.threshold_value_label.setText(f"{value}%")
        self._update_display()

    def _update_display(self):
        """Update thumbnail grid based on current threshold."""
        # Clear existing thumbnails
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Filter by threshold
        filtered_results = [
            photo for photo in self.all_results
            if photo.similarity_score >= self.current_threshold
        ]

        # Update results label
        self.results_label.setText(
            f"Showing {len(filtered_results)} similar photos "
            f"(threshold >= {int(self.current_threshold * 100)}%)"
        )

        if not filtered_results:
            no_results_label = QLabel("No photos meet the similarity threshold")
            no_results_label.setAlignment(Qt.AlignCenter)
            no_results_label.setStyleSheet("color: #7f8c8d; font-size: 12pt;")
            self.grid_layout.addWidget(no_results_label, 0, 0)
            return

        # Determine display size from zoom slider
        display_size = self.zoom_slider.value() if hasattr(self, 'zoom_slider') else THUMBNAIL_DISPLAY_SIZE

        # Calculate columns based on zoom level
        cols = max(1, min(8, (self.grid_widget.width() or 800) // (display_size + 20)))

        # Add thumbnails to grid
        for i, photo in enumerate(filtered_results):
            row = i // cols
            col = i % cols

            thumbnail = PhotoThumbnail(photo, display_size=display_size)
            thumbnail.clicked.connect(self._on_photo_clicked)
            self.grid_layout.addWidget(thumbnail, row, col)

        # Store filtered results for lightbox navigation
        self._current_filtered = filtered_results

    def _on_photo_clicked(self, photo_id: int):
        """Handle photo thumbnail click - open in lightbox."""
        self.photo_clicked.emit(photo_id)
        logger.info(f"[SimilarPhotosDialog] Photo {photo_id} clicked")

        # Open in lightbox
        self._open_lightbox_for_photo(photo_id)

    def _open_lightbox_for_photo(self, photo_id: int):
        """Open a photo in the media lightbox for full-size viewing."""
        try:
            from google_components.media_lightbox import MediaLightbox

            # Build path list from filtered results
            all_paths = []
            target_path = None
            for photo in getattr(self, '_current_filtered', self.all_results):
                if photo.file_path and Path(photo.file_path).exists():
                    all_paths.append(photo.file_path)
                    if photo.photo_id == photo_id:
                        target_path = photo.file_path

            if not target_path or not all_paths:
                logger.warning(f"[SimilarPhotosDialog] Could not find path for photo {photo_id}")
                return

            lightbox = MediaLightbox(
                target_path, all_paths, parent=self,
                project_id=self.project_id,
            )
            lightbox.exec()
        except Exception as e:
            logger.error(f"Failed to open lightbox: {e}", exc_info=True)

    def _on_zoom_changed(self, value: int):
        """Handle zoom slider change - resize thumbnails."""
        self.zoom_value_label.setText(f"{value}px")
        # Rebuild grid with new size
        self._update_display()
