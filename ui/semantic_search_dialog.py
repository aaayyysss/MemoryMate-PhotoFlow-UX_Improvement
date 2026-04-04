"""
SemanticSearchDialog - Natural Language Photo Search

Version: 1.0.1
Date: 2026-01-29

Search photos using natural language queries.

Features:
- Text query input
- Query presets (common searches)
- Results grid with relevance scores
- Threshold slider (0.0 to 1.0)
- Real-time search
- Score visualization (color-coded)
- Project-aware canonical model support (v1.0.1)

Usage:
    # RECOMMENDED: Use with project_id for correct canonical model
    dialog = SemanticSearchDialog(
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
    QFrame, QLineEdit, QComboBox
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QMouseEvent

from services.semantic_search_service import (
    get_semantic_search_service,
    get_semantic_search_service_for_project,
    SearchResult
)
from logging_config import get_logger

logger = get_logger(__name__)


class ResultThumbnail(QFrame):
    """
    Thumbnail widget for search result.

    Shows thumbnail, relevance score, and handles click events.
    """

    clicked = Signal(int)  # photo_id

    def __init__(self, result: SearchResult, parent=None):
        super().__init__(parent)
        self.photo_id = result.photo_id
        self.relevance_score = result.relevance_score

        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setLineWidth(1)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Thumbnail
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setFixedSize(150, 150)
        self.thumbnail_label.setStyleSheet("background-color: #f0f0f0;")

        # Load thumbnail via SafeImageLoader (capped at 256px, never full resolution)
        image_path = result.thumbnail_path or getattr(result, 'file_path', None)
        if image_path and Path(image_path).exists():
            try:
                from services.safe_image_loader import safe_decode_qimage
                qimage = safe_decode_qimage(str(image_path), max_dim=256)
                if not qimage.isNull():
                    pixmap = QPixmap.fromImage(qimage)
                    pixmap = pixmap.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.thumbnail_label.setPixmap(pixmap)
                else:
                    self.thumbnail_label.setText("No Preview")
            except Exception:
                self.thumbnail_label.setText("No Preview")
        else:
            self.thumbnail_label.setText("No Thumbnail")

        # Score label
        score_percent = int(self.relevance_score * 100)
        self.score_label = QLabel(f"{score_percent}% relevant")
        self.score_label.setAlignment(Qt.AlignCenter)

        # Color-code by relevance
        # Note: Text-image similarity is typically lower than image-image
        if self.relevance_score >= 0.35:
            color = "#2ecc71"  # Green (high relevance)
        elif self.relevance_score >= 0.28:
            color = "#3498db"  # Blue (good relevance)
        elif self.relevance_score >= 0.20:
            color = "#f39c12"  # Orange (moderate relevance)
        else:
            color = "#95a5a6"  # Gray (low relevance)

        self.score_label.setStyleSheet(f"color: {color}; font-weight: bold;")

        layout.addWidget(self.thumbnail_label)
        layout.addWidget(self.score_label)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Handle double-click to open photo."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.photo_id)
        super().mouseDoubleClickEvent(event)


class SemanticSearchDialog(QDialog):
    """
    Dialog for natural language photo search.

    Enables searching photos with queries like:
    - "sunset over ocean"
    - "dog playing in park"
    - "mountain landscape with snow"
    """

    photo_clicked = Signal(int)  # photo_id

    # Query presets
    PRESET_QUERIES = [
        "Custom query...",
        "sunset",
        "sunrise",
        "beach",
        "ocean",
        "mountain",
        "forest",
        "city",
        "night sky",
        "flowers",
        "animals",
        "dog",
        "cat",
        "bird",
        "food",
        "people smiling",
        "landscape",
        "architecture",
        "car",
        "snow",
    ]

    def __init__(self, project_id: Optional[int] = None, parent=None):
        """
        Initialize semantic search dialog.

        Args:
            project_id: Project ID (REQUIRED for correct canonical model usage)
            parent: Parent widget
        """
        super().__init__(parent)
        self.project_id = project_id
        self.all_results: List[SearchResult] = []
        self.current_threshold = 0.25
        self.current_query = ""

        # Use project-aware service if project_id is provided
        # This ensures we use the correct canonical model for embeddings
        if project_id is not None:
            self.search_service = get_semantic_search_service_for_project(project_id)
            logger.info(
                f"[SemanticSearchDialog] Using project-aware service for project {project_id}"
            )
        else:
            # Fallback to default service (may cause model mismatch!)
            self.search_service = get_semantic_search_service()
            logger.warning(
                f"[SemanticSearchDialog] No project_id provided! "
                f"Using default service - may cause model mismatch."
            )

        self.setWindowTitle("Semantic Photo Search")
        self.resize(900, 700)

        self._init_ui()
        self._update_statistics()

    def _init_ui(self):
        """Initialize UI components."""
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()

        self.title_label = QLabel("Search photos with natural language")
        self.title_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        # Statistics
        self.stats_label = QLabel()
        header_layout.addWidget(self.stats_label)

        layout.addLayout(header_layout)

        # Query input
        query_layout = QHBoxLayout()

        query_layout.addWidget(QLabel("Query:"))

        # Preset dropdown
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(self.PRESET_QUERIES)
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        query_layout.addWidget(self.preset_combo, 0)

        # Text input
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Enter search query (e.g., 'sunset over ocean')")
        self.query_input.returnPressed.connect(self._on_search_clicked)
        query_layout.addWidget(self.query_input, 1)

        # Search button
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._on_search_clicked)
        query_layout.addWidget(search_btn)

        layout.addLayout(query_layout)

        # Threshold control
        threshold_layout = QHBoxLayout()

        threshold_layout.addWidget(QLabel("Relevance Threshold:"))

        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(0)  # 0.0
        self.threshold_slider.setMaximum(50)  # 0.5
        self.threshold_slider.setValue(25)  # 0.25
        self.threshold_slider.setTickPosition(QSlider.TicksBelow)
        self.threshold_slider.setTickInterval(5)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        threshold_layout.addWidget(self.threshold_slider, 1)

        self.threshold_value_label = QLabel("25%")
        self.threshold_value_label.setMinimumWidth(50)
        threshold_layout.addWidget(self.threshold_value_label)

        threshold_layout.addWidget(QLabel("(Text-image similarity is typically lower)"))

        layout.addLayout(threshold_layout)

        # Results info
        self.results_label = QLabel("Enter a query to search")
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

    def _update_statistics(self):
        """Update search readiness statistics."""
        try:
            stats = self.search_service.get_search_statistics()
            self.stats_label.setText(
                f"{stats['embedded_photos']}/{stats['total_photos']} photos indexed "
                f"({stats['coverage_percent']:.1f}%)"
            )

            if not stats['search_ready']:
                self.results_label.setText(
                    "⚠️ No photo embeddings found. Run embedding extraction first."
                )
        except Exception as e:
            logger.error(f"[SemanticSearchDialog] Failed to get statistics: {e}")

    def _on_preset_changed(self, text: str):
        """Handle preset selection."""
        if text != "Custom query...":
            self.query_input.setText(text)

    def _on_search_clicked(self):
        """Handle search button click."""
        query = self.query_input.text().strip()

        if not query:
            self.results_label.setText("⚠️ Please enter a search query")
            return

        if not self.search_service.available:
            self.results_label.setText("⚠️ Search service not available (PyTorch/Transformers missing)")
            return

        self.current_query = query
        self._perform_search()

    def _perform_search(self):
        """Perform semantic search."""
        try:
            self.results_label.setText(f"Searching for '{self.current_query}'...")

            # Search with lower threshold to get more candidates
            self.all_results = self.search_service.search(
                query=self.current_query,
                top_k=100,
                threshold=0.0,  # Get all results, filter in UI
                include_metadata=True
            )

            # Update display with current threshold
            self._update_display()

        except Exception as e:
            logger.error(f"[SemanticSearchDialog] Search failed: {e}", exc_info=True)
            self.results_label.setText(f"⚠️ Search failed: {str(e)}")

    def _on_threshold_changed(self, value: int):
        """Handle threshold slider change."""
        self.current_threshold = value / 100.0
        self.threshold_value_label.setText(f"{value}%")

        # Update display if we have results
        if self.all_results:
            self._update_display()

    def _update_display(self):
        """Update result grid based on current threshold."""
        # Clear existing thumbnails
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Filter by threshold
        filtered_results = [
            result for result in self.all_results
            if result.relevance_score >= self.current_threshold
        ]

        # Update results label
        self.results_label.setText(
            f"Found {len(filtered_results)} matches for '{self.current_query}' "
            f"(threshold ≥ {int(self.current_threshold * 100)}%)"
        )

        if not filtered_results:
            no_results_label = QLabel("No photos meet the relevance threshold")
            no_results_label.setAlignment(Qt.AlignCenter)
            no_results_label.setStyleSheet("color: #7f8c8d; font-size: 12pt;")
            self.grid_layout.addWidget(no_results_label, 0, 0)
            return

        # Add thumbnails to grid (4 columns)
        for i, result in enumerate(filtered_results):
            row = i // 4
            col = i % 4

            thumbnail = ResultThumbnail(result)
            thumbnail.clicked.connect(self._on_photo_clicked)
            self.grid_layout.addWidget(thumbnail, row, col)

    def _on_photo_clicked(self, photo_id: int):
        """Handle photo thumbnail click."""
        self.photo_clicked.emit(photo_id)
        logger.info(f"[SemanticSearchDialog] Photo {photo_id} clicked")
