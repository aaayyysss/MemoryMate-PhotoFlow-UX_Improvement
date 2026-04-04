"""
Advanced Filters Widget - Combine Semantic Search with Traditional Filters

Version: 1.0.0
Date: 2026-01-01

Widget for advanced photo filtering that combines:
- Semantic search (natural language)
- Date range filters
- Folder/location filters
- File type filters
- Size filters

Features:
- Collapsible filter sections
- Real-time filter preview
- Saved filter presets
- AND/OR logic combinations

Usage:
    widget = AdvancedFiltersWidget(parent)
    widget.filtersApplied.connect(on_filters_applied)
    dialog.addWidget(widget)
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QGroupBox, QScrollArea,
    QDateEdit, QSpinBox, QDoubleSpinBox, QButtonGroup, QRadioButton,
    QFrame, QTreeWidget, QTreeWidgetItem, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QFont
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import json

from services.embedding_service import get_embedding_service
from repository.photo_repository import PhotoRepository
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FilterCriteria:
    """Container for all filter criteria."""
    # Semantic search
    semantic_query: Optional[str] = None
    semantic_enabled: bool = False

    # Date filters
    date_from: Optional[QDate] = None
    date_to: Optional[QDate] = None
    date_enabled: bool = False

    # Folder filters
    folders: List[str] = None
    folder_recursive: bool = True
    folder_enabled: bool = False

    # File type filters
    file_types: List[str] = None  # ['jpg', 'png', 'mp4', ...]
    filetype_enabled: bool = False

    # Size filters (in bytes)
    size_min: Optional[int] = None
    size_max: Optional[int] = None
    size_enabled: bool = False

    # Similarity threshold (for semantic search)
    similarity_threshold: float = 0.0  # 0.0 - 1.0

    # Combination logic
    combination_mode: str = 'AND'  # 'AND' or 'OR'

    def __post_init__(self):
        if self.folders is None:
            self.folders = []
        if self.file_types is None:
            self.file_types = []


class AdvancedFiltersWidget(QWidget):
    """
    Widget for advanced photo filtering with multiple criteria.

    Allows combining semantic search with traditional filters.
    """

    # Signal: emitted when filters are applied
    # Emits: (photo_ids, criteria_dict)
    filtersApplied = Signal(list, dict)

    # Signal: emitted when filters are cleared
    filtersCleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.embedding_service = None
        self.photo_repo = PhotoRepository()
        self.current_criteria = FilterCriteria()

        self._setup_ui()

    def _setup_ui(self):
        """Setup the advanced filters UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # Title
        title = QLabel("🔍 Advanced Filters")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        main_layout.addWidget(title)

        # Combination mode
        combo_layout = QHBoxLayout()
        combo_layout.addWidget(QLabel("Combine filters using:"))

        self.combo_mode_group = QButtonGroup(self)
        self.radio_and = QRadioButton("AND (all must match)")
        self.radio_or = QRadioButton("OR (any can match)")
        self.radio_and.setChecked(True)
        self.combo_mode_group.addButton(self.radio_and, 0)
        self.combo_mode_group.addButton(self.radio_or, 1)

        combo_layout.addWidget(self.radio_and)
        combo_layout.addWidget(self.radio_or)
        combo_layout.addStretch()
        main_layout.addLayout(combo_layout)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(separator)

        # Create scroll area for filter sections
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(12)

        # 1. Semantic Search Filter
        self.semantic_group = self._create_semantic_filter_section()
        scroll_layout.addWidget(self.semantic_group)

        # 2. Date Range Filter
        self.date_group = self._create_date_filter_section()
        scroll_layout.addWidget(self.date_group)

        # 3. Folder Filter
        self.folder_group = self._create_folder_filter_section()
        scroll_layout.addWidget(self.folder_group)

        # 4. File Type Filter
        self.filetype_group = self._create_filetype_filter_section()
        scroll_layout.addWidget(self.filetype_group)

        # 5. File Size Filter
        self.size_group = self._create_size_filter_section()
        scroll_layout.addWidget(self.size_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll, 1)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self._on_clear)
        button_layout.addWidget(self.clear_btn)

        self.apply_btn = QPushButton("Apply Filters")
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self._on_apply)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 8px 24px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(self.apply_btn)

        main_layout.addLayout(button_layout)

    def _create_semantic_filter_section(self) -> QGroupBox:
        """Create semantic search filter section."""
        group = QGroupBox("✨ Semantic Search")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)

        # Query input
        query_layout = QHBoxLayout()
        query_layout.addWidget(QLabel("Search query:"))

        self.semantic_input = QLineEdit()
        self.semantic_input.setPlaceholderText("Describe what you're looking for...")
        query_layout.addWidget(self.semantic_input, 1)
        layout.addLayout(query_layout)

        # Similarity threshold
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(QLabel("Minimum similarity:"))

        self.similarity_threshold = QDoubleSpinBox()
        self.similarity_threshold.setRange(0.0, 1.0)
        self.similarity_threshold.setSingleStep(0.05)
        self.similarity_threshold.setValue(0.0)
        self.similarity_threshold.setDecimals(2)
        self.similarity_threshold.setSuffix(" (0% = show all)")
        threshold_layout.addWidget(self.similarity_threshold)
        threshold_layout.addStretch()
        layout.addLayout(threshold_layout)

        return group

    def _create_date_filter_section(self) -> QGroupBox:
        """Create date range filter section."""
        group = QGroupBox("📅 Date Range")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)

        # From date
        from_layout = QHBoxLayout()
        from_layout.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addYears(-1))
        from_layout.addWidget(self.date_from)
        from_layout.addStretch()
        layout.addLayout(from_layout)

        # To date
        to_layout = QHBoxLayout()
        to_layout.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        to_layout.addWidget(self.date_to)
        to_layout.addStretch()
        layout.addLayout(to_layout)

        return group

    def _create_folder_filter_section(self) -> QGroupBox:
        """Create folder filter section."""
        group = QGroupBox("📁 Folders")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)

        # Folder tree (simplified for now)
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Enter folder paths (comma-separated)...")
        layout.addWidget(self.folder_input)

        # Recursive checkbox
        self.folder_recursive = QCheckBox("Include subfolders")
        self.folder_recursive.setChecked(True)
        layout.addWidget(self.folder_recursive)

        return group

    def _create_filetype_filter_section(self) -> QGroupBox:
        """Create file type filter section."""
        group = QGroupBox("📄 File Types")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)

        # Common file types
        types_layout = QHBoxLayout()

        self.type_checkboxes = {}
        for file_type in ['JPG', 'PNG', 'HEIC', 'MP4', 'MOV', 'AVI']:
            cb = QCheckBox(file_type)
            cb.setChecked(True)  # Default: all selected
            self.type_checkboxes[file_type.lower()] = cb
            types_layout.addWidget(cb)

        layout.addLayout(types_layout)

        # Custom types
        custom_layout = QHBoxLayout()
        custom_layout.addWidget(QLabel("Custom:"))
        self.custom_types = QLineEdit()
        self.custom_types.setPlaceholderText("e.g., gif, webp")
        custom_layout.addWidget(self.custom_types)
        layout.addLayout(custom_layout)

        return group

    def _create_size_filter_section(self) -> QGroupBox:
        """Create file size filter section."""
        group = QGroupBox("📏 File Size")
        group.setCheckable(True)
        group.setChecked(False)
        layout = QVBoxLayout(group)

        # Min size
        min_layout = QHBoxLayout()
        min_layout.addWidget(QLabel("Minimum:"))
        self.size_min = QDoubleSpinBox()
        self.size_min.setRange(0, 10000)
        self.size_min.setSuffix(" MB")
        self.size_min.setValue(0)
        min_layout.addWidget(self.size_min)
        min_layout.addStretch()
        layout.addLayout(min_layout)

        # Max size
        max_layout = QHBoxLayout()
        max_layout.addWidget(QLabel("Maximum:"))
        self.size_max = QDoubleSpinBox()
        self.size_max.setRange(0, 10000)
        self.size_max.setSuffix(" MB")
        self.size_max.setValue(1000)  # Default 1GB max
        max_layout.addWidget(self.size_max)
        max_layout.addStretch()
        layout.addLayout(max_layout)

        return group

    def _gather_criteria(self) -> FilterCriteria:
        """Gather all filter criteria from UI."""
        criteria = FilterCriteria()

        # Combination mode
        criteria.combination_mode = 'AND' if self.radio_and.isChecked() else 'OR'

        # Semantic search
        if self.semantic_group.isChecked():
            criteria.semantic_enabled = True
            criteria.semantic_query = self.semantic_input.text().strip()
            criteria.similarity_threshold = self.similarity_threshold.value()

        # Date range
        if self.date_group.isChecked():
            criteria.date_enabled = True
            criteria.date_from = self.date_from.date()
            criteria.date_to = self.date_to.date()

        # Folders
        if self.folder_group.isChecked():
            criteria.folder_enabled = True
            folder_text = self.folder_input.text().strip()
            if folder_text:
                criteria.folders = [f.strip() for f in folder_text.split(',')]
            criteria.folder_recursive = self.folder_recursive.isChecked()

        # File types
        if self.filetype_group.isChecked():
            criteria.filetype_enabled = True
            criteria.file_types = []

            # Get checked types
            for file_type, cb in self.type_checkboxes.items():
                if cb.isChecked():
                    criteria.file_types.append(file_type)

            # Add custom types
            custom = self.custom_types.text().strip()
            if custom:
                criteria.file_types.extend([t.strip().lower() for t in custom.split(',')])

        # File size
        if self.size_group.isChecked():
            criteria.size_enabled = True
            criteria.size_min = int(self.size_min.value() * 1024 * 1024)  # MB to bytes
            criteria.size_max = int(self.size_max.value() * 1024 * 1024)

        return criteria

    def _apply_filters(self, criteria: FilterCriteria) -> List[int]:
        """
        Apply filters and return matching photo IDs.

        Returns:
            List of photo IDs that match the criteria
        """
        logger.info(f"[AdvancedFilters] Applying filters with mode: {criteria.combination_mode}")

        result_sets = []

        # 1. Semantic search filter
        if criteria.semantic_enabled and criteria.semantic_query:
            logger.info(f"[AdvancedFilters] Applying semantic filter: '{criteria.semantic_query}'")

            # Get embedding service
            if self.embedding_service is None:
                self.embedding_service = get_embedding_service()

            # Load model if needed
            if self.embedding_service._clip_model is None:
                self.embedding_service.load_clip_model()

            # Extract query embedding
            query_embedding = self.embedding_service.extract_text_embedding(
                criteria.semantic_query
            )

            # Search
            results = self.embedding_service.search_similar(
                query_embedding,
                top_k=1000,  # Get more results, filter by threshold later
                model_id=self.embedding_service._clip_model_id
            )

            # Filter by similarity threshold
            semantic_ids = [
                photo_id for photo_id, score in results
                if score >= criteria.similarity_threshold
            ]

            result_sets.append(set(semantic_ids))
            logger.info(f"[AdvancedFilters] Semantic search: {len(semantic_ids)} matches")

        # 2. Date range filter
        if criteria.date_enabled:
            logger.info(f"[AdvancedFilters] Applying date filter: {criteria.date_from} to {criteria.date_to}")

            date_ids = []
            with self.photo_repo.connection() as conn:
                cursor = conn.execute("""
                    SELECT id FROM photo_metadata
                    WHERE date(capture_time) BETWEEN date(?) AND date(?)
                """, (
                    criteria.date_from.toString(Qt.ISODate),
                    criteria.date_to.toString(Qt.ISODate)
                ))
                date_ids = [row[0] for row in cursor.fetchall()]

            result_sets.append(set(date_ids))
            logger.info(f"[AdvancedFilters] Date filter: {len(date_ids)} matches")

        # 3. Folder filter
        if criteria.folder_enabled and criteria.folders:
            logger.info(f"[AdvancedFilters] Applying folder filter: {criteria.folders}")

            folder_ids = []
            with self.photo_repo.connection() as conn:
                for folder in criteria.folders:
                    if criteria.folder_recursive:
                        # Match folder and all subfolders
                        cursor = conn.execute("""
                            SELECT id FROM photo_metadata
                            WHERE path LIKE ?
                        """, (f"{folder}%",))
                    else:
                        # Match exact folder only
                        cursor = conn.execute("""
                            SELECT id FROM photo_metadata
                            WHERE path LIKE ? AND path NOT LIKE ?
                        """, (f"{folder}/%", f"{folder}/%/%"))

                    folder_ids.extend([row[0] for row in cursor.fetchall()])

            result_sets.append(set(folder_ids))
            logger.info(f"[AdvancedFilters] Folder filter: {len(folder_ids)} matches")

        # 4. File type filter
        if criteria.filetype_enabled and criteria.file_types:
            logger.info(f"[AdvancedFilters] Applying file type filter: {criteria.file_types}")

            type_ids = []
            with self.photo_repo.connection() as conn:
                # Build condition for each file type
                conditions = []
                for file_type in criteria.file_types:
                    conditions.append(f"path LIKE '%.{file_type}'")

                if conditions:
                    query = f"""
                        SELECT id FROM photo_metadata
                        WHERE {' OR '.join(conditions)}
                    """
                    cursor = conn.execute(query)
                    type_ids = [row[0] for row in cursor.fetchall()]

            result_sets.append(set(type_ids))
            logger.info(f"[AdvancedFilters] File type filter: {len(type_ids)} matches")

        # 5. File size filter
        if criteria.size_enabled:
            logger.info(f"[AdvancedFilters] Applying size filter: {criteria.size_min} - {criteria.size_max} bytes")

            size_ids = []
            with self.photo_repo.connection() as conn:
                cursor = conn.execute("""
                    SELECT id FROM photo_metadata
                    WHERE file_size BETWEEN ? AND ?
                """, (criteria.size_min, criteria.size_max))
                size_ids = [row[0] for row in cursor.fetchall()]

            result_sets.append(set(size_ids))
            logger.info(f"[AdvancedFilters] Size filter: {len(size_ids)} matches")

        # Combine results based on mode
        if not result_sets:
            logger.warning("[AdvancedFilters] No filters enabled")
            return []

        if criteria.combination_mode == 'AND':
            # Intersection: all filters must match
            final_ids = result_sets[0]
            for result_set in result_sets[1:]:
                final_ids = final_ids.intersection(result_set)
        else:
            # Union: any filter can match
            final_ids = set()
            for result_set in result_sets:
                final_ids = final_ids.union(result_set)

        logger.info(f"[AdvancedFilters] Final result: {len(final_ids)} photos")
        return list(final_ids)

    def _on_apply(self):
        """Apply filters and emit results."""
        try:
            # Gather criteria
            criteria = self._gather_criteria()
            self.current_criteria = criteria

            # Validate at least one filter is enabled
            if not any([
                criteria.semantic_enabled,
                criteria.date_enabled,
                criteria.folder_enabled,
                criteria.filetype_enabled,
                criteria.size_enabled
            ]):
                QMessageBox.warning(
                    self,
                    "No Filters Selected",
                    "Please enable at least one filter section."
                )
                return

            # Apply filters
            photo_ids = self._apply_filters(criteria)

            # Convert to dict for signal
            criteria_dict = {
                'semantic_query': criteria.semantic_query,
                'semantic_enabled': criteria.semantic_enabled,
                'date_from': criteria.date_from.toString(Qt.ISODate) if criteria.date_from else None,
                'date_to': criteria.date_to.toString(Qt.ISODate) if criteria.date_to else None,
                'folders': criteria.folders,
                'file_types': criteria.file_types,
                'combination_mode': criteria.combination_mode
            }

            # Emit results
            self.filtersApplied.emit(photo_ids, criteria_dict)

        except Exception as e:
            logger.error(f"[AdvancedFilters] Failed to apply filters: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Filter Error",
                f"Failed to apply filters:\n{e}"
            )

    def _on_clear(self):
        """Clear all filters."""
        # Uncheck all groups
        self.semantic_group.setChecked(False)
        self.date_group.setChecked(False)
        self.folder_group.setChecked(False)
        self.filetype_group.setChecked(False)
        self.size_group.setChecked(False)

        # Clear inputs
        self.semantic_input.clear()
        self.folder_input.clear()
        self.custom_types.clear()

        # Reset to defaults
        self.radio_and.setChecked(True)
        self.similarity_threshold.setValue(0.0)
        self.date_from.setDate(QDate.currentDate().addYears(-1))
        self.date_to.setDate(QDate.currentDate())

        # Emit cleared signal
        self.filtersCleared.emit()
        logger.info("[AdvancedFilters] Filters cleared")
