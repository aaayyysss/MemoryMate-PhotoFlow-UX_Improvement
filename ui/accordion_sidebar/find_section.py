# ui/accordion_sidebar/find_section.py
# Smart Find Section - Intelligent photo discovery
# iPhone/Google Photos/Lightroom/Excire inspired three-layer UX

"""
Find Section for AccordionSidebar

Three-layer discovery pattern (Apple/Google/Lightroom best practice):

Layer 1 - Quick Finds: Preset category chips (Beach, Mountains, Wedding, etc.)
    One click -> instant results in the grid. No thinking required.

Layer 2 - Refine: Metadata facets (date, people, location, media type, rating)
    Additive filters applied ON TOP of the concept search.

Layer 3 - Custom Search: Free-text CLIP search field with NLP parsing
    User types their own query (e.g., "dog playing on grass").
    NLP extracts dates, ratings, media types before CLIP fallback.

Phase 2 additions:
    - Custom presets (save/edit/delete)
    - "Save current search" button
    - Manage Presets panel with preset editor dialog
    - Combinable filter chips

Phase 3 additions:
    - Confidence score display on results
    - "Not this" exclusion on thumbnails
    - Search suggestions

Signals:
    smartFindTriggered(list, str): Emitted with (paths, query_label) for grid filtering
    smartFindCleared(): Emitted when Find filter is cleared
    smartFindScores(object): Emitted with {path: score} dict for confidence overlay
    smartFindExclude(str): Emitted when user excludes a path ("Not this")
"""

import logging
from typing import Optional, Dict, List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QComboBox, QCheckBox,
    QGridLayout, QSizePolicy, QDialog, QDialogButtonBox,
    QTextEdit, QSlider, QMessageBox, QMenu
)
from PySide6.QtCore import Signal, Qt, QTimer, QRunnable, QObject, QThreadPool
from PySide6.QtGui import QAction
from .base_section import BaseSection

logger = logging.getLogger(__name__)


# Category display order and labels
CATEGORY_ORDER = [
    ("custom", "My Presets"),
    ("places", "Places & Scenes"),
    ("events", "Events & Activities"),
    ("subjects", "Subjects & Things"),
    ("media", "Media Types"),
    ("quality", "Quality & Flags"),
]

# Common icons for preset editor picker
PRESET_ICONS = [
    "\U0001f516", "\U0001f50d", "\u2b50", "\u2764\ufe0f", "\U0001f3af",
    "\U0001f4f8", "\U0001f30d", "\U0001f3e0", "\U0001f333", "\U0001f30a",
    "\U0001f525", "\U0001f4a1", "\U0001f3a8", "\U0001f381", "\U0001f4cc",
    "\U0001f3b5", "\U0001f30c", "\U0001f697", "\U0001f36d", "\U0001f4d6",
]


def _find_parent_widget(obj):
    """Walk up the QObject parent chain to find the nearest QWidget ancestor."""
    while obj is not None:
        if isinstance(obj, QWidget):
            return obj
        obj = obj.parent() if hasattr(obj, 'parent') else None
    return None


class PresetEditorDialog(QDialog):
    """
    Dialog for creating/editing custom Smart Find presets.

    Lightroom/Excire-inspired: name, icon, prompts, threshold, optional filters.
    """

    def __init__(self, parent=None, preset: Optional[Dict] = None):
        # QDialog requires a QWidget parent; walk up if given a QObject
        widget_parent = parent if isinstance(parent, QWidget) else _find_parent_widget(parent)
        super().__init__(widget_parent)
        self.setWindowTitle("Edit Preset" if preset else "New Preset")
        self.setMinimumSize(420, 480)
        self._preset = preset

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Name
        layout.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., Family Vacations")
        self._name_edit.setStyleSheet(
            "QLineEdit { padding: 8px; border: 1px solid #dadce0; "
            "border-radius: 4px; font-size: 13px; }"
        )
        layout.addWidget(self._name_edit)

        # Icon picker
        layout.addWidget(QLabel("Icon:"))
        icon_widget = QWidget()
        icon_layout = QGridLayout(icon_widget)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(4)
        self._icon_buttons = {}
        self._selected_icon = PRESET_ICONS[0]
        for i, icon in enumerate(PRESET_ICONS):
            btn = QPushButton(icon)
            btn.setFixedSize(36, 36)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { font-size: 18px; border: 2px solid transparent; "
                "border-radius: 4px; background: #f1f3f4; }"
                "QPushButton:hover { background: #e8f0fe; }"
            )
            btn.clicked.connect(lambda _, ic=icon: self._select_icon(ic))
            icon_layout.addWidget(btn, i // 10, i % 10)
            self._icon_buttons[icon] = btn
        layout.addWidget(icon_widget)

        # Prompts (CLIP search terms)
        layout.addWidget(QLabel("Search Prompts (one per line):"))
        self._prompts_edit = QTextEdit()
        self._prompts_edit.setPlaceholderText(
            "beach sunset\ncoastal scenery\nsandy shore\n\n"
            "(Multiple prompts improve results — use synonyms)"
        )
        self._prompts_edit.setMaximumHeight(120)
        self._prompts_edit.setStyleSheet(
            "QTextEdit { padding: 6px; border: 1px solid #dadce0; "
            "border-radius: 4px; font-size: 12px; }"
        )
        layout.addWidget(self._prompts_edit)

        # Threshold slider
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(QLabel("Sensitivity:"))
        self._threshold_slider = QSlider(Qt.Horizontal)
        self._threshold_slider.setRange(10, 40)  # 0.10 to 0.40
        self._threshold_slider.setValue(22)
        self._threshold_slider.setTickPosition(QSlider.TicksBelow)
        self._threshold_slider.setTickInterval(5)
        self._threshold_label = QLabel("0.22")
        self._threshold_slider.valueChanged.connect(
            lambda v: self._threshold_label.setText(f"{v / 100:.2f}")
        )
        threshold_layout.addWidget(self._threshold_slider, 1)
        threshold_layout.addWidget(self._threshold_label)
        layout.addLayout(threshold_layout)

        hint = QLabel("Lower = more results but less precise. Higher = fewer but more relevant.")
        hint.setStyleSheet("color: #5f6368; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Optional metadata filters
        layout.addWidget(QLabel("Optional Filters:"))
        filter_widget = QWidget()
        filter_layout = QGridLayout(filter_widget)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)

        self._filter_gps = QCheckBox("Has GPS location")
        filter_layout.addWidget(self._filter_gps, 0, 0, 1, 2)

        filter_layout.addWidget(QLabel("Min rating:"), 1, 0)
        self._filter_rating = QComboBox()
        self._filter_rating.addItem("Any", None)
        for i in range(1, 6):
            self._filter_rating.addItem("\u2605" * i, str(i))
        filter_layout.addWidget(self._filter_rating, 1, 1)

        filter_layout.addWidget(QLabel("Media:"), 2, 0)
        self._filter_media = QComboBox()
        self._filter_media.addItem("All", None)
        self._filter_media.addItem("Photos only", "photo")
        self._filter_media.addItem("Videos only", "video")
        filter_layout.addWidget(self._filter_media, 2, 1)

        layout.addWidget(filter_widget)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Pre-fill if editing
        if preset:
            self._name_edit.setText(preset.get("name", ""))
            icon = preset.get("icon", PRESET_ICONS[0])
            self._select_icon(icon)
            prompts = preset.get("prompts", [])
            self._prompts_edit.setPlainText("\n".join(prompts))
            threshold = preset.get("threshold", 0.22)
            self._threshold_slider.setValue(int(threshold * 100))
            filters = preset.get("filters", {})
            if filters.get("has_gps"):
                self._filter_gps.setChecked(True)
            if filters.get("rating_min"):
                for i in range(self._filter_rating.count()):
                    if self._filter_rating.itemData(i) == str(filters["rating_min"]):
                        self._filter_rating.setCurrentIndex(i)
                        break
            if filters.get("media_type"):
                for i in range(self._filter_media.count()):
                    if self._filter_media.itemData(i) == filters["media_type"]:
                        self._filter_media.setCurrentIndex(i)
                        break
        else:
            self._select_icon(PRESET_ICONS[0])

    def _select_icon(self, icon: str):
        """Highlight selected icon."""
        self._selected_icon = icon
        for ic, btn in self._icon_buttons.items():
            if ic == icon:
                btn.setStyleSheet(
                    "QPushButton { font-size: 18px; border: 2px solid #1a73e8; "
                    "border-radius: 4px; background: #e8f0fe; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { font-size: 18px; border: 2px solid transparent; "
                    "border-radius: 4px; background: #f1f3f4; }"
                    "QPushButton:hover { background: #e8f0fe; }"
                )

    def _on_save(self):
        """Validate and accept."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a preset name.")
            return
        self.accept()

    def get_preset_data(self) -> Dict:
        """Get the preset data from the dialog."""
        prompts_text = self._prompts_edit.toPlainText().strip()
        prompts = [line.strip() for line in prompts_text.split("\n") if line.strip()]

        filters = {}
        if self._filter_gps.isChecked():
            filters["has_gps"] = True
        rating = self._filter_rating.currentData()
        if rating:
            filters["rating_min"] = int(rating)
        media = self._filter_media.currentData()
        if media:
            filters["media_type"] = media

        return {
            "name": self._name_edit.text().strip(),
            "icon": self._selected_icon,
            "prompts": prompts,
            "threshold": self._threshold_slider.value() / 100.0,
            "filters": filters,
        }


class _SmartFindSignals(QObject):
    """Signals for async Smart Find search worker."""
    finished = Signal(object)  # dict with search results


class _SmartFindWorker(QRunnable):
    """
    Background worker for Smart Find searches.

    Runs find_by_preset() or find_by_text() off the main thread
    so that heavy CLIP model loading (torch/transformers import +
    weight deserialization) never freezes the UI.
    """

    def __init__(self, service, preset_id: str = None,
                 query: str = None, extra_filters: dict = None,
                 display_label: str = ""):
        super().__init__()
        self.setAutoDelete(False)  # prevent C++ deletion before signal delivery
        self.signals = _SmartFindSignals()
        self._service = service
        self._preset_id = preset_id
        self._query = query
        self._extra_filters = extra_filters
        self._display_label = display_label

    def run(self):
        try:
            if self._preset_id:
                result = self._service.find_by_preset(
                    self._preset_id,
                    extra_filters=self._extra_filters or None,
                )
            else:
                result = self._service.find_by_text(
                    self._query,
                    extra_filters=self._extra_filters or None,
                )

            # Compute facets in this same background thread
            facets = {}
            try:
                orch = self._service.orchestrator
                if hasattr(orch, '_get_project_meta'):
                    from services.search_orchestrator import FacetComputer
                    project_meta = orch._get_project_meta()
                    facets = FacetComputer.compute(result.paths, project_meta)
            except Exception as e:
                logger.debug(f"[FindSection] Facet computation failed: {e}")

            self.signals.finished.emit({
                'preset_id': self._preset_id,
                'query': self._query,
                'display_label': self._display_label,
                'result': result,
                'facets': facets,
            })
        except Exception as e:
            logger.error(f"[FindSection] Async search failed: {e}")
            self.signals.finished.emit({
                'preset_id': self._preset_id,
                'query': self._query,
                'display_label': self._display_label,
                'result': None,
                'facets': {},
            })


class FindSection(BaseSection):
    """
    Smart Find section for the AccordionSidebar.

    Provides intelligent photo discovery with preset categories,
    refinement facets, free-text CLIP search, custom presets,
    combinable filters, and confidence scoring.
    """

    # Emitted when a Smart Find query produces results
    # Args: (list_of_paths, query_label_string)
    smartFindTriggered = Signal(list, str)

    # Emitted when the user clears the Find filter
    smartFindCleared = Signal()

    # Emitted with {path: score} dict for confidence overlay on grid thumbnails
    smartFindScores = Signal(object)

    # Emitted when user wants to exclude a photo ("Not this")
    smartFindExclude = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._smart_find_service = None
        self._active_preset_id = None
        self._active_query_label = None
        self._active_text_query = None
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        try:
            from config.search_config import SearchConfig
            self._search_debounce.setInterval(SearchConfig.get_search_debounce_ms())
        except Exception:
            self._search_debounce.setInterval(500)
        self._search_debounce.timeout.connect(self._execute_text_search)
        self._pending_text_query = ""
        self._recent_searches: List[Dict] = []  # [{label, preset_id_or_query, count}]
        self._preset_buttons: Dict[str, QPushButton] = {}
        self._refine_filters: Dict[str, any] = {}
        # Combinable filter state
        self._active_filters: List[Dict] = []  # [{type, id, label, icon}]
        # Async search worker reference (for cancellation)
        self._search_worker: Optional[_SmartFindWorker] = None

    def get_section_id(self) -> str:
        return "find"

    def get_title(self) -> str:
        return "Find"

    def get_icon(self) -> str:
        return "\U0001f50d"

    def set_project(self, project_id: int) -> None:
        """Override to reinitialize service on project switch."""
        super().set_project(project_id)
        self._smart_find_service = None
        self._active_preset_id = None
        self._active_filters = []

    def _get_service(self):
        """Get or create SmartFindService for current project."""
        if self._smart_find_service is None and self.project_id:
            from services.smart_find_service import get_smart_find_service
            self._smart_find_service = get_smart_find_service(self.project_id)
        return self._smart_find_service

    def load_section(self):
        """Load section (synchronous - no DB needed for presets)."""
        self._loading = False
        service = self._get_service()
        if service:
            return service.get_presets_by_category()
        return {}

    def create_content_widget(self, data) -> Optional[QWidget]:
        """
        Create the three-layer Find UI with Phase 2/3 enhancements.

        Layer 1: Quick Find preset chips (categorized, including custom)
        Layer 2: Refine facets + active filter chips
        Layer 3: Custom text search + recent searches
        """
        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(8, 6, 8, 8)
        main_layout.setSpacing(8)

        # -- Active Query Indicator (shown when a find is active) --
        self._active_indicator = QWidget()
        indicator_layout = QHBoxLayout(self._active_indicator)
        indicator_layout.setContentsMargins(8, 6, 8, 6)
        indicator_layout.setSpacing(6)

        self._active_label = QLabel("")
        self._active_label.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #1a73e8;"
        )
        self._active_label.setWordWrap(True)
        indicator_layout.addWidget(self._active_label, 1)

        save_btn = QPushButton("\U0001f4be")
        save_btn.setFixedSize(26, 26)
        save_btn.setToolTip("Save as custom preset")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton { background: #e8f0fe; border: none; border-radius: 4px; font-size: 14px; }"
            "QPushButton:hover { background: #d2e3fc; }"
        )
        save_btn.clicked.connect(self._save_current_search)
        indicator_layout.addWidget(save_btn)

        clear_btn = QPushButton("\u2715 Clear")
        clear_btn.setFixedHeight(26)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: #fce8e6; color: #c5221f; border: none;
                border-radius: 4px; padding: 2px 10px; font-size: 11px;
            }
            QPushButton:hover { background: #f8d7da; }
        """)
        clear_btn.clicked.connect(self._clear_find)
        indicator_layout.addWidget(clear_btn)

        self._active_indicator.setStyleSheet(
            "background: #e8f0fe; border-radius: 6px;"
        )
        self._active_indicator.hide()
        main_layout.addWidget(self._active_indicator)

        # -- Active Filter Chips (combinable filters) --
        self._filter_chips_container = QWidget()
        self._filter_chips_layout = QHBoxLayout(self._filter_chips_container)
        self._filter_chips_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_chips_layout.setSpacing(4)
        self._filter_chips_container.hide()
        main_layout.addWidget(self._filter_chips_container)

        # -- Layer 3: Custom Text Search (at top for quick access) --
        search_frame = QFrame()
        search_frame.setStyleSheet(
            "QFrame { background: white; border: 1px solid #dadce0; border-radius: 8px; }"
        )
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(10, 4, 10, 4)
        search_layout.setSpacing(6)

        search_icon = QLabel("\U0001f50d")
        search_icon.setFixedWidth(20)
        search_layout.addWidget(search_icon)

        self._search_field = QLineEdit()
        self._search_field.setPlaceholderText("Describe what you're looking for...")
        self._search_field.setStyleSheet("""
            QLineEdit {
                border: none; background: transparent;
                font-size: 13px; color: #202124; padding: 6px 0;
            }
        """)
        self._search_field.textChanged.connect(self._on_search_text_changed)
        self._search_field.returnPressed.connect(self._execute_text_search)
        search_layout.addWidget(self._search_field, 1)

        main_layout.addWidget(search_frame)

        # Search syntax hint (NLP + structured tokens)
        nlp_hint = QLabel(
            'Try: "sunset from 2024", "beach is:fav", '
            '"type:video date:2024", "3 star portraits"'
        )
        nlp_hint.setStyleSheet("color: #9aa0a6; font-size: 10px; padding: 0 2px;")
        nlp_hint.setWordWrap(True)
        main_layout.addWidget(nlp_hint)

        # -- CLIP availability indicator --
        service = self._get_service()
        if service and not service.clip_available:
            no_clip = QLabel(
                "\u26a0 AI Search unavailable \u2014 CLIP model not loaded.\n"
                "Metadata-only presets still work."
            )
            no_clip.setWordWrap(True)
            no_clip.setStyleSheet(
                "color: #e67c00; font-size: 11px; padding: 4px 8px; "
                "background: #fff3e0; border-radius: 4px;"
            )
            main_layout.addWidget(no_clip)

        # -- Layer 1: Quick Find Preset Chips (categorized) --
        presets_by_cat = data if isinstance(data, dict) else {}
        self._preset_buttons = {}

        for cat_id, cat_label in CATEGORY_ORDER:
            presets = presets_by_cat.get(cat_id, [])
            if not presets:
                continue

            # Category header with action button for custom category
            cat_header_widget = QWidget()
            cat_header_layout = QHBoxLayout(cat_header_widget)
            cat_header_layout.setContentsMargins(2, 4, 2, 2)
            cat_header_layout.setSpacing(4)

            cat_header = QLabel(cat_label)
            cat_header.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #5f6368;"
            )
            cat_header_layout.addWidget(cat_header, 1)

            if cat_id == "custom":
                manage_btn = QPushButton("+ New")
                manage_btn.setFixedHeight(22)
                manage_btn.setCursor(Qt.PointingHandCursor)
                manage_btn.setStyleSheet(
                    "QPushButton { background: transparent; border: none; "
                    "color: #1a73e8; font-size: 11px; font-weight: bold; }"
                    "QPushButton:hover { color: #1765cc; }"
                )
                manage_btn.clicked.connect(self._create_new_preset)
                cat_header_layout.addWidget(manage_btn)

            main_layout.addWidget(cat_header_widget)

            # Preset chips in a flow grid (3 columns)
            chip_widget = QWidget()
            chip_layout = self._create_flow_layout(chip_widget)

            for i, preset in enumerate(presets):
                chip = self._create_preset_chip(preset)
                chip_layout.addWidget(chip, i // 3, i % 3)
                self._preset_buttons[preset["id"]] = chip

            main_layout.addWidget(chip_widget)

        # Add "Create First Preset" button if no custom presets yet
        if "custom" not in presets_by_cat or not presets_by_cat.get("custom"):
            create_first = QPushButton("+ Create Custom Preset")
            create_first.setMinimumHeight(34)
            create_first.setCursor(Qt.PointingHandCursor)
            create_first.setStyleSheet("""
                QPushButton {
                    background: transparent; border: 1px dashed #dadce0;
                    border-radius: 17px; padding: 5px 14px;
                    font-size: 12px; color: #1a73e8;
                }
                QPushButton:hover { background: #e8f0fe; border-color: #1a73e8; }
            """)
            create_first.clicked.connect(self._create_new_preset)
            main_layout.addWidget(create_first)

        # -- Layer 2: Refine Facets --
        refine_header = QLabel("Refine Results")
        refine_header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #5f6368; "
            "padding: 6px 0 2px 2px;"
        )
        main_layout.addWidget(refine_header)

        refine_widget = self._create_refine_panel()
        main_layout.addWidget(refine_widget)

        # -- Recent Searches --
        self._recent_container = QWidget()
        self._recent_layout = QVBoxLayout(self._recent_container)
        self._recent_layout.setContentsMargins(0, 4, 0, 0)
        self._recent_layout.setSpacing(2)
        self._update_recent_ui()
        main_layout.addWidget(self._recent_container)

        # -- Suggested Searches (from library stats) --
        self._suggestions_container = QWidget()
        self._suggestions_layout = QVBoxLayout(self._suggestions_container)
        self._suggestions_layout.setContentsMargins(0, 4, 0, 0)
        self._suggestions_layout.setSpacing(2)
        self._load_suggestions()
        main_layout.addWidget(self._suggestions_container)

        # Stretch at bottom
        main_layout.addStretch()

        return container

    def _create_flow_layout(self, parent: QWidget) -> QGridLayout:
        """Create a grid layout that simulates a chip flow (3 columns)."""
        layout = QGridLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return layout

    def _create_preset_chip(self, preset: Dict) -> QPushButton:
        """
        Create a Google Photos-style preset chip button.

        Custom presets get a context menu for edit/delete.
        """
        icon = preset.get("icon", "\U0001f50d")
        name = preset.get("name", "Unknown")
        preset_id = preset.get("id", "")
        is_custom = preset.get("is_custom", False)

        btn = QPushButton(f" {icon}  {name}")
        btn.setMinimumHeight(34)
        btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(self._chip_default_style())
        btn.setProperty("preset_id", preset_id)
        btn.clicked.connect(lambda _, pid=preset_id: self._on_preset_clicked(pid))

        # Context menu for custom presets
        if is_custom:
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, pid=preset_id, b=btn: self._show_preset_context_menu(b, pid)
            )

        return btn

    def _chip_default_style(self) -> str:
        return """
            QPushButton {
                background: #f1f3f4; border: 1px solid #e0e0e0;
                border-radius: 17px; padding: 5px 14px;
                font-size: 12px; color: #202124; text-align: left;
            }
            QPushButton:hover {
                background: #e8f0fe; border-color: #1a73e8; color: #1a73e8;
            }
            QPushButton:pressed { background: #d2e3fc; }
        """

    def _chip_active_style(self) -> str:
        return """
            QPushButton {
                background: #1a73e8; border: 1px solid #1a73e8;
                border-radius: 17px; padding: 5px 14px;
                font-size: 12px; color: white; text-align: left; font-weight: bold;
            }
            QPushButton:hover { background: #1765cc; }
        """

    def _set_chip_active(self, preset_id: str):
        """Highlight the active preset chip, reset others."""
        for pid, btn in self._preset_buttons.items():
            btn.setStyleSheet(
                self._chip_active_style() if pid == preset_id
                else self._chip_default_style()
            )

    def _show_preset_context_menu(self, button: QPushButton, preset_id: str):
        """Show edit/delete context menu for custom presets."""
        menu = QMenu(button)
        edit_action = menu.addAction("Edit Preset")
        delete_action = menu.addAction("Delete Preset")
        delete_action.setIcon(button.style().standardIcon(button.style().SP_TrashIcon))

        action = menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))
        if action == edit_action:
            self._edit_custom_preset(preset_id)
        elif action == delete_action:
            self._delete_custom_preset(preset_id)

    def _create_refine_panel(self) -> QWidget:
        """Create Layer 2: Metadata refinement facets."""
        panel = QWidget()
        layout = QGridLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        combo_style = """
            QComboBox {
                background: white; border: 1px solid #dadce0;
                border-radius: 4px; padding: 4px 8px;
                font-size: 12px; color: #202124;
            }
            QComboBox:hover { border-color: #1a73e8; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background: white; selection-background-color: #e8f0fe;
                color: #202124;
            }
        """

        # Date filter
        layout.addWidget(QLabel("Date:"), 0, 0)
        self._date_combo = QComboBox()
        self._date_combo.addItem("Any time", None)
        self._date_combo.addItem("Today", "today")
        self._date_combo.addItem("Yesterday", "yesterday")
        self._date_combo.addItem("This week", "week")
        self._date_combo.addItem("Last week", "last_week")
        self._date_combo.addItem("This month", "month")
        self._date_combo.addItem("Last month", "last_month")
        self._date_combo.addItem("This year", "year")
        self._date_combo.addItem("Last year", "last_year")
        self._date_combo.setStyleSheet(combo_style)
        self._date_combo.currentIndexChanged.connect(self._on_refine_changed)
        layout.addWidget(self._date_combo, 0, 1)

        # Media type
        layout.addWidget(QLabel("Type:"), 1, 0)
        self._type_combo = QComboBox()
        self._type_combo.addItem("All types", None)
        self._type_combo.addItem("Photos only", "photo")
        self._type_combo.addItem("Videos only", "video")
        self._type_combo.setStyleSheet(combo_style)
        self._type_combo.currentIndexChanged.connect(self._on_refine_changed)
        layout.addWidget(self._type_combo, 1, 1)

        # Location checkbox
        self._gps_check = QCheckBox("Has location (GPS)")
        self._gps_check.setStyleSheet("QCheckBox { font-size: 12px; color: #202124; }")
        self._gps_check.stateChanged.connect(self._on_refine_changed)
        layout.addWidget(self._gps_check, 2, 0, 1, 2)

        # Rating
        layout.addWidget(QLabel("Rating:"), 3, 0)
        self._rating_combo = QComboBox()
        self._rating_combo.addItem("Any rating", None)
        self._rating_combo.addItem("\u2605 and above", "1")
        self._rating_combo.addItem("\u2605\u2605 and above", "2")
        self._rating_combo.addItem("\u2605\u2605\u2605 and above", "3")
        self._rating_combo.addItem("\u2605\u2605\u2605\u2605 and above", "4")
        self._rating_combo.addItem("\u2605\u2605\u2605\u2605\u2605 only", "5")
        self._rating_combo.setStyleSheet(combo_style)
        self._rating_combo.currentIndexChanged.connect(self._on_refine_changed)
        layout.addWidget(self._rating_combo, 3, 1)

        # Style labels
        for label in panel.findChildren(QLabel):
            label.setStyleSheet("font-size: 12px; color: #5f6368;")

        return panel

    def _get_refine_filters(self) -> Dict:
        """Collect current refine facet values into a filters dict."""
        filters = {}

        # Date
        from datetime import datetime, timedelta
        date_val = self._date_combo.currentData()
        if date_val:
            today = datetime.now().date()
            if date_val == "today":
                filters["date_from"] = today.strftime("%Y-%m-%d")
                filters["date_to"] = today.strftime("%Y-%m-%d")
            elif date_val == "yesterday":
                yesterday = today - timedelta(days=1)
                filters["date_from"] = yesterday.strftime("%Y-%m-%d")
                filters["date_to"] = yesterday.strftime("%Y-%m-%d")
            elif date_val == "week":
                start = today - timedelta(days=today.weekday())
                filters["date_from"] = start.strftime("%Y-%m-%d")
                filters["date_to"] = today.strftime("%Y-%m-%d")
            elif date_val == "last_week":
                start = today - timedelta(days=today.weekday() + 7)
                end = start + timedelta(days=6)
                filters["date_from"] = start.strftime("%Y-%m-%d")
                filters["date_to"] = end.strftime("%Y-%m-%d")
            elif date_val == "month":
                start = today.replace(day=1)
                filters["date_from"] = start.strftime("%Y-%m-%d")
                filters["date_to"] = today.strftime("%Y-%m-%d")
            elif date_val == "last_month":
                first_of_month = today.replace(day=1)
                last_month_end = first_of_month - timedelta(days=1)
                last_month_start = last_month_end.replace(day=1)
                filters["date_from"] = last_month_start.strftime("%Y-%m-%d")
                filters["date_to"] = last_month_end.strftime("%Y-%m-%d")
            elif date_val == "year":
                filters["date_from"] = f"{today.year}-01-01"
                filters["date_to"] = today.strftime("%Y-%m-%d")
            elif date_val == "last_year":
                filters["date_from"] = f"{today.year - 1}-01-01"
                filters["date_to"] = f"{today.year - 1}-12-31"

        # Media type
        type_val = self._type_combo.currentData()
        if type_val:
            filters["media_type"] = type_val

        # GPS
        if self._gps_check.isChecked():
            filters["has_gps"] = True

        # Rating
        rating_val = self._rating_combo.currentData()
        if rating_val:
            filters["rating_min"] = int(rating_val)

        return filters

    # ── Event Handlers ──

    def _on_preset_clicked(self, preset_id: str):
        """Handle preset chip click - run Smart Find."""
        # Toggle: clicking active preset clears it
        if self._active_preset_id == preset_id:
            self._clear_find()
            return

        self._active_preset_id = preset_id
        self._active_text_query = None
        self._set_chip_active(preset_id)

        service = self._get_service()
        if not service:
            logger.warning("[FindSection] SmartFindService not available")
            return

        extra_filters = self._get_refine_filters()

        # Run in background to avoid freezing UI
        QTimer.singleShot(0, lambda: self._run_preset_find(preset_id, extra_filters))

    def _retire_search_worker(self):
        """
        Disconnect and release the previous search worker.

        Workers use setAutoDelete(False) to prevent C++ deletion before
        signal delivery.  Without explicit cleanup the old QRunnable leaks
        every time a new search is started (17+ preset searches in a row
        leaked 16+ workers).  Disconnecting the signal prevents stale
        results from being delivered after a new search has started, and
        calling setAutoDelete(True) lets the thread-pool reclaim the
        QRunnable once it finishes.
        """
        old = self._search_worker
        if old is not None:
            try:
                old.signals.finished.disconnect(self._on_search_result)
            except (RuntimeError, TypeError):
                pass  # already disconnected or object deleted
            # Allow thread-pool to delete when done (was False to keep alive
            # until signal delivery — now that we disconnected, safe to GC).
            try:
                old.setAutoDelete(True)
            except RuntimeError:
                pass  # C++ object already deleted
            self._search_worker = None

    def _run_preset_find(self, preset_id: str, extra_filters: Dict):
        """Execute preset find asynchronously in a background thread."""
        service = self._get_service()
        if not service:
            return

        worker = _SmartFindWorker(
            service, preset_id=preset_id, extra_filters=extra_filters,
        )
        worker.signals.finished.connect(self._on_search_result)
        self._retire_search_worker()
        self._search_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_search_result(self, data: dict):
        """Handle search results delivered from background worker."""
        result = data.get('result')
        if result is None:
            return

        preset_id = data.get('preset_id')
        query = data.get('query')
        facets = data.get('facets', {})
        display_label = data.get('display_label') or result.query_label

        # Guard: if user started a different search, discard stale results
        if preset_id and self._active_preset_id != preset_id:
            return
        if query and self._active_text_query != query:
            return

        if result.paths:
            self._active_query_label = display_label
            self._show_active_indicator(display_label, result.total_matches)
            self._show_facet_chips(facets)
            key = preset_id or query
            self._add_recent(display_label, key, result.total_matches)
            self.smartFindTriggered.emit(result.paths, display_label)
            if result.scores:
                self.smartFindScores.emit(result.scores)
        else:
            # Friendly empty-state messages for known presets
            empty_hints = {
                "favorites": "No favorites yet \u2014 flag photos as Pick to see them here",
            }
            hint = empty_hints.get(preset_id, "") if preset_id else ""
            if hint:
                self._show_active_indicator(display_label, 0, hint=hint)
            else:
                self._show_active_indicator(display_label, 0)
            self._show_facet_chips({})
            self.smartFindTriggered.emit([], display_label)

    def _on_search_text_changed(self, text: str):
        """Debounced text input handler."""
        self._pending_text_query = text.strip()
        if len(self._pending_text_query) >= 3:
            self._search_debounce.start()
        elif not self._pending_text_query:
            self._search_debounce.stop()

    def _execute_text_search(self):
        """
        Execute free-text search with progressive results.

        Phase 1 (immediate): Metadata-only results from structured tokens.
        Phase 2 (async): Full semantic + metadata results replace phase 1.

        Both phases share a single stable display_label generated once from
        the raw query so the UI never jitters between "🔍 date:2025" and
        "🔍 date:2025 [2025]".
        """
        query = self._pending_text_query
        if not query or len(query) < 3:
            return

        service = self._get_service()
        if not service:
            logger.warning("[FindSection] SmartFindService not available")
            return

        # Clear preset selection
        self._active_preset_id = None
        self._active_text_query = query
        self._set_chip_active("")

        extra_filters = self._get_refine_filters()

        # Generate a stable display label once — reused by both phases.
        display_label = f"\U0001f50d {query}"

        # Phase 1: Metadata-only results (instant, <50ms)
        try:
            orch = service.orchestrator
            meta_result = orch.search_metadata_only(
                query, extra_filters=extra_filters or None
            )
            if meta_result.paths:
                self._active_query_label = display_label
                self._show_active_indicator(display_label, meta_result.total_matches)
                self._show_facet_chips(meta_result.facets)
                self.smartFindTriggered.emit(meta_result.paths, display_label)
                if meta_result.scores:
                    self.smartFindScores.emit(meta_result.scores)
        except Exception as e:
            logger.debug(f"[FindSection] Phase 1 metadata search failed: {e}")

        # Phase 2: Full search — runs in background thread to avoid freezing UI
        # during CLIP model loading (import torch + transformers can take 20+ seconds)
        self._execute_full_search(query, extra_filters, display_label)

    def _execute_full_search(self, query: str, extra_filters: Dict,
                             display_label: str = ""):
        """Phase 2 of progressive search: full semantic + metadata results (async)."""
        if self._active_text_query != query:
            return

        service = self._get_service()
        if not service:
            return

        worker = _SmartFindWorker(
            service, query=query, extra_filters=extra_filters,
            display_label=display_label,
        )
        worker.signals.finished.connect(self._on_search_result)
        self._retire_search_worker()
        self._search_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_refine_changed(self, _=None):
        """Re-run current find with updated refine filters."""
        if self._active_preset_id:
            extra_filters = self._get_refine_filters()
            pid = self._active_preset_id  # capture by value before deferred call
            QTimer.singleShot(0, lambda: self._run_preset_find(
                pid, extra_filters))
        elif self._active_text_query and len(self._active_text_query) >= 3:
            self._pending_text_query = self._active_text_query
            self._execute_text_search()

    def _clear_find(self):
        """Clear all Smart Find state and restore full photo grid."""
        self._active_preset_id = None
        self._active_query_label = None
        self._active_text_query = None
        self._set_chip_active("")

        if hasattr(self, '_active_indicator'):
            self._active_indicator.hide()

        # Clear facet chips
        self._show_facet_chips({})

        # Reset refine facets
        if hasattr(self, '_date_combo'):
            self._date_combo.setCurrentIndex(0)
        if hasattr(self, '_type_combo'):
            self._type_combo.setCurrentIndex(0)
        if hasattr(self, '_gps_check'):
            self._gps_check.setChecked(False)
        if hasattr(self, '_rating_combo'):
            self._rating_combo.setCurrentIndex(0)

        # Clear search field
        if hasattr(self, '_search_field'):
            self._search_field.clear()

        # Clear exclusions
        service = self._get_service()
        if service:
            service.clear_exclusions()

        self.smartFindCleared.emit()

    def _show_active_indicator(self, label: str, count: int, hint: str = ""):
        """Show the active query indicator at top of section."""
        if hasattr(self, '_active_indicator') and hasattr(self, '_active_label'):
            if count > 0:
                self._active_label.setText(f"{label}  \u2014  {count} photos found")
            elif hint:
                self._active_label.setText(hint)
            else:
                self._active_label.setText(f"{label}  \u2014  No matches")
            self._active_indicator.show()

    def _show_facet_chips(self, facets: Dict):
        """
        Show result-set facet chips below the active query indicator.

        Facets are computed from the current result set (not global).
        This is the Google Photos / Lightroom filter refinement pattern.
        """
        if not hasattr(self, '_filter_chips_layout'):
            return

        # Clear existing chips
        while self._filter_chips_layout.count():
            child = self._filter_chips_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not facets:
            self._filter_chips_container.hide()
            return

        chip_style = (
            "QPushButton { background: #f1f3f4; border: 1px solid #e0e0e0; "
            "border-radius: 12px; padding: 3px 10px; font-size: 10px; color: #5f6368; }"
            "QPushButton:hover { background: #e8f0fe; border-color: #1a73e8; color: #1a73e8; }"
        )

        for facet_type, distribution in facets.items():
            for label, count in distribution.items():
                chip = QPushButton(f"{label} ({count})")
                chip.setFixedHeight(24)
                chip.setCursor(Qt.PointingHandCursor)
                chip.setStyleSheet(chip_style)
                # Facet chips are informational (refinement via Layer 2 combos)
                self._filter_chips_layout.addWidget(chip)

        self._filter_chips_layout.addStretch()
        self._filter_chips_container.show()

    def focus_search(self):
        """Focus the search input field (called by Ctrl+F shortcut)."""
        if hasattr(self, '_search_field'):
            self._search_field.setFocus()
            self._search_field.selectAll()

    # ── Custom Preset Management ──

    def _create_new_preset(self):
        """Open the preset editor dialog to create a new custom preset."""
        dialog = PresetEditorDialog(self)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_preset_data()
            service = self._get_service()
            if service:
                service.save_custom_preset(
                    name=data["name"],
                    icon=data["icon"],
                    prompts=data["prompts"],
                    filters=data.get("filters"),
                    threshold=data.get("threshold", 0.22),
                )
                # Refresh the section to show new preset
                self._reload_section()

    def _edit_custom_preset(self, preset_id: str):
        """Open the preset editor to edit an existing custom preset."""
        service = self._get_service()
        if not service:
            return

        preset = service._lookup_preset(preset_id)
        if not preset or not preset.get("is_custom"):
            return

        dialog = PresetEditorDialog(self, preset=preset)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_preset_data()
            service.update_custom_preset(
                db_id=preset["db_id"],
                name=data["name"],
                icon=data["icon"],
                prompts=data["prompts"],
                filters=data.get("filters"),
                threshold=data.get("threshold", 0.22),
            )
            self._reload_section()

    def _delete_custom_preset(self, preset_id: str):
        """Delete a custom preset after confirmation."""
        service = self._get_service()
        if not service:
            return

        preset = service._lookup_preset(preset_id)
        if not preset or not preset.get("is_custom"):
            return

        reply = QMessageBox.question(
            _find_parent_widget(self), "Delete Preset",
            f"Delete custom preset \"{preset['name']}\"?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            service.delete_custom_preset(preset["db_id"])
            if self._active_preset_id == preset_id:
                self._clear_find()
            self._reload_section()

    def _save_current_search(self):
        """Save the current active search as a custom preset."""
        service = self._get_service()
        if not service:
            return

        # Pre-fill dialog with current search info
        current_preset = None
        if self._active_preset_id:
            source = service._lookup_preset(self._active_preset_id)
            if source:
                current_preset = {
                    "name": f"My {source.get('name', 'Search')}",
                    "icon": source.get("icon", PRESET_ICONS[0]),
                    "prompts": source.get("prompts", []),
                    "filters": {**source.get("filters", {}), **self._get_refine_filters()},
                    "threshold": source.get("threshold", 0.22),
                }
        elif self._active_text_query:
            current_preset = {
                "name": self._active_text_query[:30],
                "icon": PRESET_ICONS[1],  # search icon
                "prompts": [self._active_text_query],
                "filters": self._get_refine_filters(),
                "threshold": 0.22,
            }

        dialog = PresetEditorDialog(self, preset=current_preset)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_preset_data()
            service.save_custom_preset(
                name=data["name"],
                icon=data["icon"],
                prompts=data["prompts"],
                filters=data.get("filters"),
                threshold=data.get("threshold", 0.22),
            )
            self._reload_section()

    def _reload_section(self):
        """Reload section content to reflect preset changes."""
        # Invalidate custom presets cache
        service = self._get_service()
        if service:
            service._custom_presets = None

        # Re-trigger load and update widget through the AccordionSidebar
        data = self.load_section()
        sidebar = self.parent()
        if sidebar and hasattr(sidebar, 'section_widgets'):
            section_widget = sidebar.section_widgets.get("find")
            if section_widget:
                new_widget = self.create_content_widget(data)
                if new_widget:
                    section_widget.set_content_widget(new_widget)

    # ── Recent Searches ──

    def _add_recent(self, label: str, query_or_id: str, count: int):
        """Add a search to recent history (max 8)."""
        # Remove duplicate
        self._recent_searches = [
            r for r in self._recent_searches if r["query"] != query_or_id
        ]
        self._recent_searches.insert(0, {
            "label": label, "query": query_or_id, "count": count
        })
        self._recent_searches = self._recent_searches[:8]
        self._update_recent_ui()

    def _update_recent_ui(self):
        """Refresh the recent searches UI."""
        if not hasattr(self, '_recent_layout'):
            return

        # Clear
        while self._recent_layout.count():
            child = self._recent_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self._recent_searches:
            return

        header = QLabel("Recent")
        header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #5f6368; "
            "padding: 4px 0 2px 2px;"
        )
        self._recent_layout.addWidget(header)

        for item in self._recent_searches:
            btn = QPushButton(f"  {item['label']}  ({item['count']})")
            btn.setMinimumHeight(30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none;
                    text-align: left; font-size: 12px;
                    color: #202124; padding: 4px 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background: #f1f3f4;
                }
            """)
            query = item["query"]
            btn.clicked.connect(lambda _, q=query: self._on_recent_clicked(q))
            self._recent_layout.addWidget(btn)

    def _on_recent_clicked(self, query_or_id: str):
        """Re-run a recent search."""
        # Check if it's a preset ID (builtin or custom)
        service = self._get_service()
        if service:
            preset = service._lookup_preset(query_or_id)
            if preset:
                self._on_preset_clicked(query_or_id)
                return

        # Otherwise treat as text query
        self._search_field.setText(query_or_id)
        self._pending_text_query = query_or_id
        self._execute_text_search()

    # ── Suggested Searches (from library stats) ──

    def _load_suggestions(self):
        """Load library-aware search suggestions."""
        if not hasattr(self, '_suggestions_layout'):
            return

        # Clear existing
        while self._suggestions_layout.count():
            child = self._suggestions_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.project_id:
            self._suggestions_container.hide()
            return

        try:
            from services.search_orchestrator import LibraryAnalyzer
            suggestions = LibraryAnalyzer.suggest(self.project_id)
        except Exception:
            suggestions = []

        if not suggestions:
            self._suggestions_container.hide()
            return

        header = QLabel("Suggested")
        header.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #5f6368; "
            "padding: 4px 0 2px 2px;"
        )
        self._suggestions_layout.addWidget(header)

        # Flow layout for suggestion chips
        chips_widget = QWidget()
        chips_layout = QHBoxLayout(chips_widget)
        chips_layout.setContentsMargins(0, 0, 0, 0)
        chips_layout.setSpacing(4)

        chip_style = (
            "QPushButton { background: #e8f5e9; border: 1px solid #c8e6c9; "
            "border-radius: 12px; padding: 3px 10px; font-size: 10px; color: #2e7d32; }"
            "QPushButton:hover { background: #c8e6c9; border-color: #4caf50; color: #1b5e20; }"
        )

        for suggestion in suggestions:
            icon = suggestion.get("icon", "")
            label = suggestion.get("label", "")
            query = suggestion.get("query", "")

            chip = QPushButton(f"{icon} {label}")
            chip.setFixedHeight(24)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(chip_style)
            chip.clicked.connect(
                lambda _, q=query: self._on_suggestion_clicked(q)
            )
            chips_layout.addWidget(chip)

        chips_layout.addStretch()
        self._suggestions_layout.addWidget(chips_widget)
        self._suggestions_container.show()

    def _on_suggestion_clicked(self, query: str):
        """Run a suggested search query."""
        if hasattr(self, '_search_field'):
            self._search_field.setText(query)
        self._pending_text_query = query
        self._execute_text_search()

    def cleanup(self):
        """Clean up resources."""
        self._search_debounce.stop()
        self._retire_search_worker()
        super().cleanup()
        logger.debug("[FindSection] Cleanup")
