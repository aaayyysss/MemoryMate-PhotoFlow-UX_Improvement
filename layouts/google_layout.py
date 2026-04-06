# layouts/google_layout.py
# Version 10.01.01.14 dated 20260219
# Google Photos-style layout - Timeline-based, date-grouped, minimalist design

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSplitter, QToolBar, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QFrame, QGridLayout, QStackedWidget, QSizePolicy, QDialog,
    QGraphicsOpacityEffect, QMenu, QListWidget, QListWidgetItem, QDialogButtonBox,
    QInputDialog, QMessageBox, QSlider, QSpinBox, QComboBox, QLayout, QTabBar
)
from PySide6.QtCore import (
    Qt, Signal, QSize, QEvent, QRunnable, QThreadPool, QObject, QTimer, QUrl,
    QPropertyAnimation, QEasingCurve, QRect, QPoint
)
from PySide6.QtGui import (
    QPixmap, QIcon, QKeyEvent, QImage, QColor, QAction, QPainter, QPen, QPainterPath, QDesktopServices
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from .base_layout import BaseLayout
from logging_config import get_logger

from PySide6.QtWidgets import QApplication

logger = get_logger(__name__)
from .video_editor_mixin import VideoEditorMixin

# Import extracted components from google_components module
from ui.search.empty_state_view import EmptyStateView
from ui.search.google_shell_sidebar import GoogleShellSidebar

from google_components import (
    # Phase 3A: UI Widgets
    FlowLayout, CollapsibleSection, PersonCard, PeopleGridView,
    # Phase 3C: Media Lightbox
    MediaLightbox, TrimMarkerSlider,
    PreloadImageSignals, PreloadImageWorker,
    ProgressiveImageSignals, ProgressiveImageWorker,
    # Phase 3D: Photo Workers & Helpers
    PhotoButton, ThumbnailSignals, ThumbnailLoader,
    PhotoLoadSignals, PhotoLoadWorker,
    GooglePhotosEventFilter, AutocompleteEventFilter,
    # Phase 3E: Dialog Classes
    PersonPickerDialog
)

from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from datetime import datetime
import json
from utils.qt_role import role_set_json, role_get_json
import os
import subprocess
from translation_manager import tr as t
from utils.qt_guards import connect_guarded_dynamic

# Activity Center is now a QDockWidget managed by MainWindow.
# Google layout no longer creates its own activity panel.





class GooglePhotosLayout(BaseLayout):
    """
    Google Photos-style layout.

    Structure:
    ┌─────────────────────────────────────────────┐
    │  Toolbar (Scan, Faces, Search, etc.)       │
    ├───────────┬─────────────────────────────────┤
    │ Sidebar   │  Timeline (Date Groups)         │
    │ • Search  │  • December 2024 (15 photos)    │
    │ • Years   │  • November 2024 (32 photos)    │
    │ • Albums  │  • October 2024 (28 photos)     │
    └───────────┴─────────────────────────────────┘

    Features:
    - Timeline-based view (grouped by date)
    - Minimal sidebar (search + timeline navigation)
    - Large zoomable thumbnails
    - Layout-specific toolbar with Scan/Faces
    """

    # Badge overlay configuration (Google Photos style)
    # PERFORMANCE FIX: Extracted to class constant (was recreated on every badge render)
    TAG_BADGE_CONFIG = {
        'favorite': ('★', QColor(255, 215, 0, 230), Qt.black),
        'face': ('👤', QColor(70, 130, 180, 220), Qt.white),
        'important': ('⚑', QColor(255, 69, 0, 220), Qt.white),
        'work': ('💼', QColor(0, 128, 255, 220), Qt.white),
        'travel': ('✈', QColor(34, 139, 34, 220), Qt.white),
        'personal': ('♥', QColor(255, 20, 147, 220), Qt.white),
        'family': ('👨\u200d👩\u200d👧', QColor(255, 140, 0, 220), Qt.white),
        'archive': ('📦', QColor(128, 128, 128, 220), Qt.white),
    }
    DEFAULT_BADGE_CONFIG = ('🏷', QColor(150, 150, 150, 230), Qt.white)

    def get_name(self) -> str:
        return "Google Photos Style"

    def get_id(self) -> str:
        return "google"

    def create_layout(self) -> QWidget:
        """
        Create Google Photos-style layout.
        """
        self._ensure_tooltip_style()

        
        # Face merge undo/redo stacks (CRITICAL FIX 2026-01-07)
        self.redo_stack = []  # Stack for redo operations after undo

        # Phase 2: Selection tracking
        self.selected_photos = set()  # Set of selected photo paths
        self.selection_mode = False  # Whether selection mode is active
        self.last_selected_path = None  # For Shift range selection
        self.all_displayed_paths = []  # Track all photos in current view for range selection

        # GPS Copy/Paste clipboard (stores copied location for quick reuse)
        # Format: {'lat': float, 'lon': float, 'location_name': str} or None
        self.copied_gps_location = None

        # Async thumbnail loading (copied from Current Layout's proven pattern)
        self.thumbnail_thread_pool = QThreadPool()
        self.thumbnail_thread_pool.setMaxThreadCount(4)  # REDUCED: Limit concurrent loads
        self.thumbnail_buttons = {}  # Map path -> button widget for async updates
        self.thumbnail_load_count = 0  # Track how many thumbnails we've queued

        # QUICK WIN #1: Track unloaded thumbnails for scroll-triggered loading
        self.unloaded_thumbnails = {}  # Map path -> (button, size) for lazy loading
        self.initial_load_limit = 50  # Load first 50 immediately (increased from 30)
        self._thumb_inflight = set()  # Paths currently being loaded — prevents re-queuing

        # QUICK WIN #3: Virtual scrolling - render only visible date groups
        self.date_groups_metadata = []  # List of {date_str, photos, thumb_size, index}
        self.date_group_widgets = {}  # Map index -> widget (rendered or placeholder)
        self.rendered_date_groups = set()  # Set of indices that are currently rendered
        self.virtual_scroll_enabled = True  # Enable virtual scrolling
        self.initial_render_count = 5  # Render first 5 date groups immediately

        # QUICK WIN #4: Collapsible date groups
        self.date_group_collapsed = {}  # Map date_str -> bool (collapsed state)
        self.date_group_grids = {}  # Map date_str -> grid widget for toggle visibility

        # QUICK WIN #5: Smooth scroll performance (60 FPS)
        self.scroll_debounce_timer = QTimer()
        self.scroll_debounce_timer.setSingleShot(True)
        self.scroll_debounce_timer.timeout.connect(self._on_scroll_debounced)
        self.scroll_debounce_delay = 150  # ms - debounce scroll events

        # PHASE 2 #4: Date scroll indicator hide timer
        self.date_indicator_hide_timer = QTimer()
        self.date_indicator_hide_timer.setSingleShot(True)
        self.date_indicator_hide_timer.timeout.connect(self._hide_date_indicator)
        self.date_indicator_delay = 800  # ms - hide after scrolling stops

        # FIX 2026-02-08: Folder click debounce timer (prevent double-loads)
        self._folder_click_debounce_timer = QTimer()
        self._folder_click_debounce_timer.setSingleShot(True)
        self._folder_click_debounce_timer.timeout.connect(self._execute_folder_click)
        self._folder_click_debounce_delay = 250  # ms - debounce folder clicks
        self._pending_folder_id = None  # Pending folder ID for debounced click

        # ── Reload coalescing + state signature dedupe ──────────
        # Prevents redundant sequential reloads of identical state.
        # All load requests funnel through _request_load() which sets
        # _pending_load_params and starts a 50ms coalesce timer.
        self._load_coalesce_timer = QTimer()
        self._load_coalesce_timer.setSingleShot(True)
        self._load_coalesce_timer.timeout.connect(self._execute_coalesced_load)
        self._pending_load_params = None   # dict of params for next load
        self._last_load_signature = None   # signature of last executed load

        # PHASE 2 #5: Thumbnail aspect ratio mode
        self.thumbnail_aspect_ratio = "square"  # "square", "original", "16:9"

        # CRITICAL FIX: Create ONE shared signal object for ALL workers (like Current Layout)
        # Problem: Each worker was creating its own signal → signals got garbage collected
        # Solution: Share one signal object, connect it once
        self.thumbnail_signals = ThumbnailSignals()
        self.thumbnail_signals.loaded.connect(self._on_thumbnail_loaded)

        # PHASE 2 Task 2.1: Shared signal for async photo loading
        self.photo_load_signals = PhotoLoadSignals()
        self.photo_load_signals.loaded.connect(self._on_photos_loaded)
        self.photo_load_signals.error.connect(self._on_photos_load_error)

        # Connect to Search State Store
        self.main_window.search_state_store.stateChanged.connect(self._on_search_state_changed)

        # Initialize filter state
        self.current_thumb_size = 200
        self.current_filter_year = None
        self.current_filter_month = None
        self.current_filter_day = None
        self.current_filter_folder = None
        self.current_filter_person = None
        self.current_filter_paths = None

        # --- Groups filter state ---
        self.current_filter_group_id = None
        self.current_filter_group_mode = None        

        # PHASE 2 Task 2.1: Async photo loading (move queries off GUI thread)
        # Generation counter prevents stale results from overwriting newer data
        self._photo_load_generation = 0
        self._photo_load_in_progress = False
        self._loading_indicator = None  # Will be created in _create_timeline()

        # ── Paged loading state ──────────────────────────────────
        from workers.photo_page_worker import PhotoPageSignals
        from services.photo_query_service import (
            SMALL_THRESHOLD, PAGE_SIZE, PREFETCH_PAGES, MAX_IN_MEMORY_ROWS,
        )

        # Helper to get MainWindow's UI generation for guarded callbacks.
        # Protects against callbacks arriving after shutdown/restart.
        self._get_ui_generation = lambda: (
            self.main_window.ui_generation()
            if hasattr(self.main_window, 'ui_generation') else 0
        )

        self._page_signals = PhotoPageSignals()
        self._page_signals.count_ready.connect(self._on_page_count_ready)
        # Guard page_ready with MainWindow generation to prevent stale callbacks
        # after app restart. Note: handler also checks _photo_load_generation
        # for load-level staleness.
        connect_guarded_dynamic(
            self._page_signals.page_ready,
            self._on_page_ready,
            self._get_ui_generation,
            name='page_ready',
        )
        self._page_signals.error.connect(self._on_page_error)
        self._paging_total = 0          # total rows from count query
        self._paging_loaded = 0         # rows received so far
        self._paging_offset = 0         # next offset to fetch
        self._paging_active = False     # True while paged loading is in progress
        self._paging_fetching = False   # True while a page worker is running
        self._paging_all_rows = []      # accumulated rows for incremental merge
        self._paging_filters = {}       # current filter dict for paged loading
        self._page_size = PAGE_SIZE
        self._small_threshold = SMALL_THRESHOLD
        self._prefetch_pages = PREFETCH_PAGES
        self._max_in_memory = MAX_IN_MEMORY_ROWS

        # Get current project ID (CRITICAL: Photos are organized by project)
        from app_services import get_default_project_id, list_projects
        self.project_id = get_default_project_id()

        # Fallback to first project if no default
        if self.project_id is None:
            projects = list_projects()
            if projects:
                self.project_id = projects[0]["id"]
                print(f"[GooglePhotosLayout] Using first project: {self.project_id}")
            else:
                print("[GooglePhotosLayout] ⚠️ WARNING: No projects found! Please create a project first.")
        else:
            print(f"[GooglePhotosLayout] Using default project: {self.project_id}")

        # PERFORMANCE FIX: Cache badge overlay settings (read once vs per-photo)
        # Previously: SettingsManager read on every _create_tag_badge_overlay call
        # Now: Cache at initialization, improving performance with large photo libraries
        from settings_manager_qt import SettingsManager
        sm = SettingsManager()
        self._badge_settings = {
            'enabled': sm.get("badge_overlays_enabled", True),
            'size': int(sm.get("badge_size_px", 22)),
            'max_count': int(sm.get("badge_max_count", 4))
        }

        # Main container
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create toolbar
        toolbar = self._create_toolbar()
        main_layout.addWidget(toolbar)



        # Phase 3: Main view tabs (Photos, People, Folders, Videos, Favorites)
        self.view_tabs = QTabBar()
        self.view_tabs.addTab("📸 Photos")
        self.view_tabs.addTab("👥 People")
        self.view_tabs.addTab("📁 Folders")
        self.view_tabs.addTab("🎬 Videos")
        self.view_tabs.addTab("⭐ Favorites")
        self.view_tabs.currentChanged.connect(self._on_view_tab_changed)
        main_layout.addWidget(self.view_tabs)

        # Create horizontal splitter (Sidebar | Timeline)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(3)

        # Create sidebar (legacy accordion)
        self.sidebar = self._create_sidebar()

        # Phase 2B: Build passive shell container above the legacy accordion
        self.left_panel = self._build_left_panel_with_shell(self.sidebar)
        self.splitter.addWidget(self.left_panel)

        # Search Components (Integrated from SearchState)
        self.empty_state = EmptyStateView()

        # Create timeline
        self.timeline = self._create_timeline()

        # Build Results Container (Timeline/Empty)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(0)

        self.results_stack = QStackedWidget()
        self.results_stack.addWidget(self.timeline)
        self.results_stack.addWidget(self.empty_state)

        self.results_layout.addWidget(self.results_stack, 1)

        self.splitter.addWidget(self.results_container)

        # Debounced zoom handling (prevents repeated reloads while dragging)
        self._pending_zoom_value = None
        self._pending_scroll_restore = None
        # Parent the timer to the top-level widget (QObject) to avoid
        # passing this layout helper, which is not a QObject subclass.
        self.zoom_change_timer = QTimer(main_widget)
        self.zoom_change_timer.setSingleShot(True)
        self.zoom_change_timer.setInterval(120)  # Match Google Photos-like feel
        self.zoom_change_timer.timeout.connect(self._commit_zoom_change)

        # Set splitter sizes (280px sidebar initially, rest for timeline)
        self.splitter.setSizes([280, 1000])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self.splitter)

        # Background Activity Panel - shows background job progress (face detection, embeddings, etc.)
        # Activity Center is now a QDockWidget owned by MainWindow;
        # no per-layout activity panel needed.
        self.activity_panel = None

        # QUICK WIN #6: Create floating selection toolbar (initially hidden)
        self.floating_toolbar = self._create_floating_toolbar(main_widget)
        self.floating_toolbar.hide()

        # PHASE 2 #4: Create floating date scroll indicator (initially hidden)
        self.date_scroll_indicator = self._create_date_scroll_indicator(main_widget)
        self.date_scroll_indicator.hide()

        # ── Phase 2A: Load-ownership gate ─────────────────────────────────
        self._project_switch_in_progress = False
        self._pending_project_reload = False
        self._reload_debounce_timer = QTimer()
        self._reload_debounce_timer.setSingleShot(True)
        self._reload_debounce_timer.setInterval(120)
        self._reload_debounce_timer.timeout.connect(self._execute_debounced_reload)
        self._pending_reload_kwargs = {}
        self._pending_reload_reason = None
        self._last_reload_signature = None
        self._reload_in_progress = False

        # ── Phase 5 perf: Passive section-expand dedupe ──────────────────
        self._last_passive_section = None
        self._last_passive_section_ts = 0.0

        # Phase 10: explicit view mode state
        self._current_view_mode = "all"  # all | videos | locations | search | duplicates | devices

        # Defer initial photo load until MainWindow signals first paint is done.
        # Previously _load_photos() fired here during __init__(), before show(),
        # so the DB query + grouping + widget creation competed with first paint.
        # MainWindow._after_first_paint() calls _on_startup_ready() to begin.
        if getattr(self.main_window, '_deferred_init_started', False):
            # Post-startup layout switch: first paint already done, load now.
            self._startup_load_pending = False
            if getattr(self, 'project_id', None):
                self.request_reload(reason="layout_switch_post_startup")
            else:
                logger.info("[GooglePhotosLayout] Suppressing initial load — no active project")
        else:
            # Initial startup: defer until first paint completes.
            self._startup_load_pending = True
            if self._loading_indicator:
                self._loading_indicator.show()

        # Subscribe to ProjectState store for version-based refresh.
        # media_v  → full photo grid reload (scan completed)
        # stacks_v → photo grid reload (stack badges changed)
        self._store_unsub = None
        try:
            from core.state_bus import get_store
            store = get_store()
            s = store.state
            self._store_versions = {
                "media_v": s.media_v,
                "stacks_v": s.stacks_v,
            }

            def _on_state_changed(state, action):
                if getattr(self, '_disposed', False):
                    return
                need_refresh = False
                for v_key in ("media_v", "stacks_v"):
                    old_v = self._store_versions.get(v_key)
                    new_v = getattr(state, v_key)
                    if old_v is not None and old_v != new_v:
                        need_refresh = True
                    self._store_versions[v_key] = new_v
                if need_refresh:
                    self.refresh_after_scan()

            self._store_callback = _on_state_changed  # prevent GC (weakref store)
            self._store_unsub = store.subscribe(_on_state_changed)
        except Exception:
            pass  # Store not initialized (e.g. unit tests)

        return main_widget

    def _on_search_state_changed(self, state):
        """Respond to global SearchState changes."""
        if getattr(self, '_disposed', False):
            return

        from shiboken6 import isValid
        if not isValid(self.results_stack) or not isValid(self.empty_state) or not isValid(self.timeline):
            logger.debug("[GooglePhotosLayout] Skipping SearchState update: UI objects already deleted")
            return

        # Handle empty state
        if state.empty_state_reason:
            self.empty_state.set_state(state.empty_state_reason)
            self.results_stack.setCurrentWidget(self.empty_state)
        else:
            self.results_stack.setCurrentWidget(self.timeline)

        # UX-1: Syncing search box in layout is no longer needed as search is centralized in MainWindow

        # Phase 2A: suppress grid reload when no project is active
        if not getattr(self, 'project_id', None):
            return

        # Trigger photo grid update if result paths changed
        # We use a signature-based check to avoid redundant work
        sig = (tuple(state.result_paths), state.active_project_id)
        if getattr(self, "_last_result_sig", None) != sig:
            self._last_result_sig = sig
            if state.result_paths or not state.query_text:
                # If we have results, or the search is empty (show all), reload timeline
                # Note: We convert paths to Orchestrator-style ScoredResults if needed,
                # but here we just pass them to the existing path-based loader.
                self._load_photos(filter_paths=state.result_paths if state.query_text or state.preset_id else None)

    # ------------------------------------------------------------------
    # Startup fence: called by MainWindow after first paint completes
    # ------------------------------------------------------------------
    def _on_startup_ready(self):
        """Begin initial photo load after MainWindow's first paint.

        This method is called by MainWindow._after_first_paint() so the
        heavy DB query, grouping, and widget-chunk creation don't compete
        with the first paint cycle.
        """
        if not getattr(self, '_startup_load_pending', False):
            return
        self._startup_load_pending = False
        if not getattr(self, 'project_id', None):
            logger.info("[GooglePhotosLayout] Startup ready but no project — suppressing load")
            return
        print("[GooglePhotosLayout] First paint done — starting initial photo load")
        self._load_photos()

    def _ensure_tooltip_style(self):
        app = QApplication.instance()
        if not app:
            return
        # Prevent duplicating the stylesheet if layouts are switched repeatedly
        if getattr(app, "_mm_tooltip_style_applied", False):
            return

        app.setStyleSheet((app.styleSheet() or "") + """
        QToolTip {
            background-color: #000000;
            color: #ffffff;
            border: 1px solid #444444;
            padding: 6px;
            border-radius: 4px;
            font-size: 10pt;
        }
        """)
        app._mm_tooltip_style_applied = True

    def _create_toolbar(self) -> QToolBar:
        """
        Create Google Photos-specific toolbar.
        """
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setStyleSheet("""
            QToolBar {
                background: #f8f9fa;
                border-bottom: 1px solid #dadce0;
                padding: 6px;
                spacing: 8px;
            }
            QPushButton {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
            }
            QPushButton:hover {
                background: #f1f3f4;
                border-color: #bdc1c6;
            }
            QPushButton:pressed {
                background: #e8eaed;
            }
        """)

        # Project selector (compact, no label - Google Photos style)
        from PySide6.QtWidgets import QComboBox, QLabel

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(150)
        self.project_combo.setStyleSheet("""
            QComboBox {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
            }
            QComboBox:hover {
                border-color: #bdc1c6;
            }
            QComboBox::drop-down {
                border: none;
            }
        """)
        self.project_combo.setToolTip("Select project to view")
        toolbar.addWidget(self.project_combo)

        # Populate project selector
        self._populate_project_selector()

        toolbar.addSeparator()



        toolbar.addSeparator()

        # Clear Filter button (initially hidden, Google Photos style)
        self.btn_clear_filter = QPushButton("✕ Clear Filter")
        self.btn_clear_filter.setToolTip("Show all photos (remove date/folder filters)")
        self.btn_clear_filter.clicked.connect(self._clear_filter)
        self.btn_clear_filter.setVisible(False)
        self.btn_clear_filter.setStyleSheet("""
            QPushButton {
                background: #fff3cd;
                border: 1px solid #ffc107;
                color: #856404;
            }
            QPushButton:hover {
                background: #ffeaa7;
            }
        """)
        toolbar.addWidget(self.btn_clear_filter)

        toolbar.addSeparator()

        # Phase 2: Selection mode toggle
        self.btn_select = QPushButton("☑️ Select")
        self.btn_select.setToolTip("Enable selection mode (Ctrl+A to select all)")
        self.btn_select.setCheckable(True)
        self.btn_select.clicked.connect(self._toggle_selection_mode)
        toolbar.addWidget(self.btn_select)

        # Duplicates button - Open duplicate photo review dialog
        self.btn_duplicates = QPushButton("🔍 Duplicates")
        self.btn_duplicates.setToolTip("Review and manage duplicate photos")
        self.btn_duplicates.clicked.connect(self._open_duplicates_dialog)
        toolbar.addWidget(self.btn_duplicates)

        # Similar button - Open similar photos detection dialog
        self.btn_similar = QPushButton("🎯 Similar")
        self.btn_similar.setToolTip("Find visually similar photos using AI")
        self.btn_similar.clicked.connect(self._open_similar_photos_dialog)
        toolbar.addWidget(self.btn_similar)

        toolbar.addSeparator()

        # Zoom controls (Google Photos style - +/- buttons with slider)
        from PySide6.QtWidgets import QLabel, QSlider

        # Zoom out button
        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.setToolTip(t('google_layout.zoom_out_tooltip'))
        self.btn_zoom_out.setFixedSize(28, 28)
        self.btn_zoom_out.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() - 50))
        self.btn_zoom_out.setStyleSheet("""
            QPushButton {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
                font-size: 14pt;
            }
            QPushButton:hover {
                background: #f1f3f4;
            }
        """)
        toolbar.addWidget(self.btn_zoom_out)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(100)  # 100px thumbnails
        self.zoom_slider.setMaximum(400)  # 400px thumbnails
        self.zoom_slider.setValue(200)    # Default 200px
        self.zoom_slider.setTracking(False)  # Emit on release to avoid reload storms
        self.zoom_slider.setFixedWidth(100)
        self.zoom_slider.setToolTip(t('google_layout.zoom_slider_tooltip'))
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        toolbar.addWidget(self.zoom_slider)

        # Zoom in button
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.setToolTip(t('google_layout.zoom_in_tooltip'))
        self.btn_zoom_in.setFixedSize(28, 28)
        self.btn_zoom_in.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() + 50))
        self.btn_zoom_in.setStyleSheet("""
            QPushButton {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
                font-size: 14pt;
            }
            QPushButton:hover {
                background: #f1f3f4;
            }
        """)
        toolbar.addWidget(self.btn_zoom_in)

        # Zoom value label (smaller, optional)
        self.zoom_value_label = QLabel("200")
        self.zoom_value_label.setFixedWidth(35)
        self.zoom_value_label.setStyleSheet("padding: 0 4px; font-size: 9pt; color: #5f6368;")
        self.zoom_value_label.setToolTip("Current thumbnail size")
        toolbar.addWidget(self.zoom_value_label)

        # PHASE 2 #5: Aspect ratio toggle buttons (icons only, no label)
        self.btn_aspect_square = QPushButton("⬜")
        self.btn_aspect_square.setToolTip("Square thumbnails (1:1)")
        self.btn_aspect_square.setCheckable(True)
        self.btn_aspect_square.setChecked(True)
        self.btn_aspect_square.setFixedSize(24, 24)
        self.btn_aspect_square.clicked.connect(lambda: self._set_aspect_ratio("square"))
        self.btn_aspect_square.setStyleSheet("""
            QPushButton {
                background: white;
                border: 2px solid #dadce0;
                border-radius: 4px;
            }
            QPushButton:checked {
                background: #e8f0fe;
                border-color: #1a73e8;
            }
            QPushButton:hover {
                border-color: #1a73e8;
            }
        """)
        toolbar.addWidget(self.btn_aspect_square)

        self.btn_aspect_original = QPushButton("🖼️")
        self.btn_aspect_original.setToolTip("Original aspect ratio")
        self.btn_aspect_original.setCheckable(True)
        self.btn_aspect_original.setFixedSize(24, 24)
        self.btn_aspect_original.clicked.connect(lambda: self._set_aspect_ratio("original"))
        self.btn_aspect_original.setStyleSheet("""
            QPushButton {
                background: white;
                border: 2px solid #dadce0;
                border-radius: 4px;
            }
            QPushButton:checked {
                background: #e8f0fe;
                border-color: #1a73e8;
            }
            QPushButton:hover {
                border-color: #1a73e8;
            }
        """)
        toolbar.addWidget(self.btn_aspect_original)

        self.btn_aspect_16_9 = QPushButton("▬")
        self.btn_aspect_16_9.setToolTip("16:9 widescreen")
        self.btn_aspect_16_9.setCheckable(True)
        self.btn_aspect_16_9.setFixedSize(24, 24)
        self.btn_aspect_16_9.clicked.connect(lambda: self._set_aspect_ratio("16:9"))
        self.btn_aspect_16_9.setStyleSheet("""
            QPushButton {
                background: white;
                border: 2px solid #dadce0;
                border-radius: 4px;
            }
            QPushButton:checked {
                background: #e8f0fe;
                border-color: #1a73e8;
            }
            QPushButton:hover {
                border-color: #1a73e8;
            }
        """)
        toolbar.addWidget(self.btn_aspect_16_9)

        toolbar.addSeparator()

        # Settings button (Google Photos pattern - before spacer)
        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setToolTip(t('google_layout.settings_tooltip'))
        self.btn_settings.setFixedSize(32, 32)
        self.btn_settings.clicked.connect(self._show_settings_menu)
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 16px;
                font-size: 14pt;
            }
            QPushButton:hover {
                background: #f1f3f4;
            }
            QPushButton:pressed {
                background: #e8eaed;
            }
        """)
        toolbar.addWidget(self.btn_settings)

        # Activity Center toggle button
        self.btn_activity = QPushButton("Activity")
        self.btn_activity.setToolTip("Show/hide background tasks (Ctrl+Shift+A)")
        self.btn_activity.setFixedHeight(28)
        self.btn_activity.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                color: #5f6368;
            }
            QPushButton:hover {
                background: #f1f3f4;
            }
            QPushButton:pressed {
                background: #e8eaed;
            }
        """)
        self.btn_activity.clicked.connect(self._on_toggle_activity_center)
        toolbar.addWidget(self.btn_activity)

        # Spacer (push remaining items to the right)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        # Selection actions (will show/hide based on selection)
        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.setToolTip("Delete selected photos")
        self.btn_delete.setVisible(False)
        self.btn_delete.clicked.connect(self._on_delete_selected)
        toolbar.addWidget(self.btn_delete)

        self.btn_favorite = QPushButton("⭐ Favorite")
        self.btn_favorite.setToolTip("Mark selected as favorites")
        self.btn_favorite.setVisible(False)
        self.btn_favorite.clicked.connect(self._on_favorite_selected)
        toolbar.addWidget(self.btn_favorite)

        # PHASE 3 #7: Share/Export button
        self.btn_share = QPushButton("📤 Share")
        self.btn_share.setToolTip("Share or export selected photos")
        self.btn_share.setVisible(False)
        self.btn_share.clicked.connect(self._on_share_selected)
        toolbar.addWidget(self.btn_share)

        # Store toolbar reference
        self._toolbar = toolbar

        return toolbar



    def _show_settings_menu(self):
        """Show Settings menu (Google Photos pattern) - Phase 2."""
        from PySide6.QtWidgets import QMenu, QMessageBox
        from PySide6.QtGui import QAction

        # Create menu with proper parent (main_window is a QWidget)
        menu = QMenu(self.main_window)
        menu.setStyleSheet("""
            QMenu {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
                padding: 8px 0;
            }
            QMenu::item {
                padding: 8px 24px;
                font-size: 11pt;
            }
            QMenu::item:selected {
                background: #f1f3f4;
            }
            QMenu::separator {
                height: 1px;
                background: #dadce0;
                margin: 4px 0;
            }
        """)

        # QUICK ACTIONS section
        menu.addSection(t('google_layout.settings_menu.quick_actions_section'))

        scan_action = QAction(t('google_layout.settings_menu.scan_repository'), menu)
        scan_action.setToolTip(t('google_layout.settings_menu.scan_repository'))
        if hasattr(self, '_scan_repository_handler'):
            scan_action.triggered.connect(self._scan_repository_handler)
        menu.addAction(scan_action)

        faces_action = QAction(t('google_layout.settings_menu.detect_faces'), menu)
        faces_action.setToolTip(t('google_layout.settings_menu.detect_faces'))
        if hasattr(self, '_detect_faces_handler'):
            faces_action.triggered.connect(self._detect_faces_handler)
        menu.addAction(faces_action)

        refresh_action = QAction(t('google_layout.settings_menu.refresh_view'), menu)
        refresh_action.setToolTip(t('google_layout.settings_menu.refresh_view'))
        refresh_action.triggered.connect(self._load_photos)
        menu.addAction(refresh_action)

        menu.addSeparator()

        # TOOLS section
        menu.addSection(t('google_layout.settings_menu.tools_section'))

        db_action = QAction(t('google_layout.settings_menu.database_maintenance'), menu)
        if hasattr(self.main_window, '_on_database_maintenance'):
            db_action.triggered.connect(self.main_window._on_database_maintenance)
        else:
            db_action.triggered.connect(lambda: QMessageBox.information(
                self.main_window, "Tools", "Database Maintenance not available"))
        menu.addAction(db_action)

        clear_cache_action = QAction(t('google_layout.settings_menu.clear_cache'), menu)
        if hasattr(self.main_window, '_on_clear_thumbnail_cache'):
            clear_cache_action.triggered.connect(self.main_window._on_clear_thumbnail_cache)
        else:
            clear_cache_action.triggered.connect(lambda: QMessageBox.information(
                self.main_window, "Tools", "Clear Thumbnail Cache not available"))
        menu.addAction(clear_cache_action)

        # Duplicate Detection and Similar Photos
        menu.addSeparator()
        menu.addSection("🔍 Media Analysis")
        
        # Duplicate Detection
        duplicate_action = QAction("🔍 Detect Duplicates...", menu)
        duplicate_action.setToolTip("Find exact and similar duplicates in your collection")
        duplicate_action.triggered.connect(self._on_detect_duplicates)
        menu.addAction(duplicate_action)
        
        # Similar Photos
        similar_action = QAction("📸 Find Similar Photos...", menu)
        similar_action.setToolTip("Discover visually similar photos using AI embeddings")
        similar_action.triggered.connect(self._on_find_similar_photos)
        menu.addAction(similar_action)
        
        # Duplicate Status
        dup_status_action = QAction("📊 Show Duplicate Status", menu)
        dup_status_action.setToolTip("View current duplicate detection statistics")
        dup_status_action.triggered.connect(self._on_show_duplicate_status)
        menu.addAction(dup_status_action)

        menu.addSeparator()

        # VIEW section
        menu.addSection(t('google_layout.settings_menu.view_section'))

        dark_mode_action = QAction(t('google_layout.settings_menu.toggle_dark_mode'), menu)
        dark_mode_action.setCheckable(True)
        try:
            dark_mode_action.setChecked(bool(self.main_window.is_dark_mode_enabled()))
        except Exception:
            dark_mode_action.setChecked(False)
        if hasattr(self.main_window, 'toggle_dark_mode'):
            dark_mode_action.triggered.connect(self.main_window.toggle_dark_mode)
        else:
            dark_mode_action.triggered.connect(lambda: QMessageBox.information(
                self.main_window, "View", "Dark mode toggle not available"))
        menu.addAction(dark_mode_action)

        sidebar_mode_action = QAction(t('google_layout.settings_menu.sidebar_mode'), menu)
        if hasattr(self.main_window, 'toggle_sidebar_mode'):
            sidebar_mode_action.triggered.connect(self.main_window.toggle_sidebar_mode)
        else:
            sidebar_mode_action.triggered.connect(lambda: QMessageBox.information(
                self.main_window, "View", "Sidebar mode toggle not available"))
        menu.addAction(sidebar_mode_action)

        menu.addSeparator()

        # HELP section
        menu.addSection(t('google_layout.settings_menu.help_section'))

        shortcuts_action = QAction(t('google_layout.settings_menu.keyboard_shortcuts'), menu)
        if hasattr(self.main_window, 'show_keyboard_shortcuts_dialog'):
            shortcuts_action.triggered.connect(self.main_window.show_keyboard_shortcuts_dialog)
        else:
            shortcuts_action.triggered.connect(lambda: QMessageBox.information(
                self.main_window,
                "Keyboard Shortcuts",
                "Ctrl+F: Search\nCtrl+A: Select all\nCtrl+D: Deselect\nEscape: Clear\nDelete: Delete\nEnter: Open\nSpace: Quick preview\nS: Toggle selection\n+/-: Zoom\nG: Grid\nT: Timeline\nE: Single"
            ))
        menu.addAction(shortcuts_action)

        menu.addSeparator()

        # ABOUT section
        menu.addSection("ℹ️ About")

        about_action = QAction("ℹ️  About MemoryMate", menu)
        about_action.triggered.connect(lambda: QMessageBox.information(
            self.main_window,
            "About MemoryMate",
            "MemoryMate PhotoFlow\nVersion 1.0\n\nPhoto management with AI-powered face detection"
        ))
        menu.addAction(about_action)

        # Show menu below the Settings button
        menu.exec(self.btn_settings.mapToGlobal(self.btn_settings.rect().bottomLeft()))

    def _create_floating_toolbar(self, parent: QWidget) -> QWidget:
        """
        QUICK WIN #6: Create floating selection toolbar (Google Photos style).

        Appears at bottom of screen when photos are selected.
        Shows selection count and action buttons.

        Args:
            parent: Parent widget for positioning

        Returns:
            QWidget: Floating toolbar (initially hidden)
        """
        toolbar = QWidget(parent)
        toolbar.setStyleSheet("""
            QWidget {
                background: #202124;
                border-radius: 8px;
                border: 1px solid #5f6368;
            }
        """)

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # Selection count label
        self.selection_count_label = QLabel("0 selected")
        self.selection_count_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 10pt;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.selection_count_label)

        layout.addStretch()

        # Action buttons
        # Select All button (shortened caption)
        btn_select_all = QPushButton("All")
        btn_select_all.setToolTip("Select all photos")
        btn_select_all.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8ab4f8;
                border: none;
                padding: 6px 10px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background: #3c4043;
                border-radius: 4px;
            }
        """)
        btn_select_all.setCursor(Qt.PointingHandCursor)
        btn_select_all.clicked.connect(self._on_select_all)
        layout.addWidget(btn_select_all)

        # CRITICAL FIX: Add batch Edit Location button (Sprint 2 enhancement)
        # This makes batch GPS editing discoverable - users don't need to hunt in context menus
        btn_edit_location = QPushButton("📍 GPS")
        btn_edit_location.setStyleSheet("""
            QPushButton {
                background: #1a73e8;
                color: white;
                border: none;
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1557b0;
            }
            QPushButton:disabled {
                background: #5f6368;
                color: #9aa0a6;
            }
        """)
        btn_edit_location.setToolTip("Edit GPS location for all selected photos")
        btn_edit_location.setCursor(Qt.PointingHandCursor)
        btn_edit_location.clicked.connect(self._on_batch_edit_location_clicked)
        layout.addWidget(btn_edit_location)

        # GPS-FOCUSED WORKFLOW: Copy GPS button
        # Copies GPS location from first selected photo for quick reuse
        btn_copy_gps = QPushButton("📍 Copy")
        btn_copy_gps.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8ab4f8;
                border: none;
                padding: 6px 10px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background: #3c4043;
                border-radius: 4px;
            }
        """)
        btn_copy_gps.setToolTip("Copy GPS location from selected photo")
        btn_copy_gps.setCursor(Qt.PointingHandCursor)
        btn_copy_gps.clicked.connect(self._on_copy_gps_from_toolbar)
        layout.addWidget(btn_copy_gps)

        # DESELECTION WORKFLOW: Invert Selection button
        # Useful for "select all except these" workflow
        btn_invert = QPushButton("⇄ Invert")
        btn_invert.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8ab4f8;
                border: none;
                padding: 6px 10px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background: #3c4043;
                border-radius: 4px;
            }
        """)
        btn_invert.setToolTip("Invert selection (select unselected, deselect selected)")
        btn_invert.setCursor(Qt.PointingHandCursor)
        btn_invert.clicked.connect(self._on_invert_selection)
        layout.addWidget(btn_invert)

        # Clear Selection button
        btn_clear = QPushButton("✕")
        btn_clear.setToolTip("Clear selection")
        btn_clear.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8ab4f8;
                border: none;
                padding: 6px 10px;
                font-size: 11pt;
            }
            QPushButton:hover {
                background: #3c4043;
                border-radius: 4px;
            }
        """)
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.clicked.connect(self._on_clear_selection)
        layout.addWidget(btn_clear)

        # Position toolbar at bottom center (will be repositioned on resize)
        # UPDATED: Width increased from 350 to 450 to accommodate 5 buttons (All, GPS, Copy, Invert, Clear)
        toolbar.setFixedHeight(48)
        toolbar.setFixedWidth(450)

        return toolbar

    def _create_date_scroll_indicator(self, parent: QWidget) -> QWidget:
        """
        PHASE 2 #4: Create floating date scroll indicator.

        Shows current date when scrolling through timeline.
        Appears on right side, fades out after scrolling stops.

        Args:
            parent: Parent widget for positioning

        Returns:
            QWidget: Floating date indicator (initially hidden)
        """
        indicator = QLabel(parent)
        indicator.setStyleSheet("""
            QLabel {
                background: rgba(32, 33, 36, 0.9);
                color: white;
                font-size: 14pt;
                font-weight: bold;
                padding: 12px 20px;
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        indicator.setAlignment(Qt.AlignCenter)
        indicator.setText("Loading...")
        indicator.adjustSize()

        return indicator

    def _create_sidebar(self) -> QWidget:
        """
        Create Google Photos-style accordion sidebar.

        Phase 3 Implementation:
        - AccordionSidebar with all 6 sections (People, Dates, Folders, Tags, Branches, Quick)
        - One section expanded at a time (full height)
        - Other sections collapsed to headers
        - ONE universal scrollbar per section
        - Clean, modern Google Photos UX
        """
        # Import and instantiate AccordionSidebar (PHASE 3: Using modular version)
        from ui.accordion_sidebar import AccordionSidebar

        # CRITICAL FIX: GooglePhotosLayout is NOT a QWidget, so pass None as parent
        sidebar = AccordionSidebar(project_id=self.project_id, parent=None)
        sidebar.setMinimumWidth(240)
        sidebar.setMaximumWidth(500)

        # CRITICAL: Don't set generic QWidget stylesheet - it overrides accordion's internal styling
        # AccordionSidebar handles its own styling internally (nav bar, headers, content areas)
        # Only set border on the container itself
        sidebar.setStyleSheet("""
            AccordionSidebar {
                border-right: 1px solid #dadce0;
            }
        """)

        # Connect accordion signals to grid filtering
        sidebar.selectBranch.connect(self._on_accordion_branch_clicked)
        sidebar.selectFolder.connect(self._on_accordion_folder_clicked)
        sidebar.selectDate.connect(self._on_accordion_date_clicked)
        sidebar.selectTag.connect(self._on_accordion_tag_clicked)
        sidebar.selectVideo.connect(self._on_accordion_video_clicked)  # NEW: Video filtering
        sidebar.selectPerson.connect(self._on_accordion_person_clicked)
        sidebar.selectLocation.connect(self._on_accordion_location_clicked)  # GPS location filtering
        sidebar.selectDevice.connect(self._on_accordion_device_selected)
        sidebar.personMerged.connect(self._on_accordion_person_merged)
        sidebar.personDeleted.connect(self._on_accordion_person_deleted)
        sidebar.mergeHistoryRequested.connect(self._on_people_merge_history_requested)
        sidebar.undoLastMergeRequested.connect(self._on_people_undo_requested)
        sidebar.redoLastUndoRequested.connect(self._on_people_redo_requested)
        sidebar.peopleToolsRequested.connect(self._on_people_tools_requested)

        # Groups section signals (Person Groups feature)
        sidebar.selectGroup.connect(self._on_accordion_group_clicked)
        sidebar.editGroupRequested.connect(self._on_group_edit_requested)
        sidebar.deleteGroupRequested.connect(self._on_group_deleted)

        # Smart Find signals
        sidebar.selectSmartFind.connect(self._on_smart_find_results)
        sidebar.smartFindCleared.connect(self._on_smart_find_cleared)
        sidebar.smartFindScores.connect(self._on_smart_find_scores)
        sidebar.smartFindExclude.connect(self._on_smart_find_exclude)

        # FIX: Connect section expansion signal to hide search suggestions popup
        sidebar.sectionExpanding.connect(self._on_accordion_section_expanding)

        # Store reference for refreshing
        self.accordion_sidebar = sidebar

        return sidebar

    # ── Phase 2B: Passive shell ───────────────────────────────────────

    def _build_left_panel_with_shell(self, accordion_widget: QWidget) -> QWidget:
        """Build the left panel: new shell on top, legacy accordion below.

        Phase 2B — the shell is visual only; the accordion remains the
        action owner.  The accordion is placed inside a collapsible
        group box so it can be collapsed once the shell is functional.
        """
        from PySide6.QtWidgets import QGroupBox

        container = QWidget()
        container.setObjectName("GoogleLeftShell")
        container.setMinimumWidth(280)
        container.setMaximumWidth(300)
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # New shell (Phase 2B passive)
        self.google_shell_sidebar = GoogleShellSidebar(container)
        self.google_shell_sidebar.selectBranch.connect(
            self._on_passive_shell_branch_clicked)
        self.google_shell_sidebar.disabledBranchRequested.connect(
            self._on_disabled_shell_branch_requested)
        self.google_shell_sidebar.openActivityCenterRequested.connect(
            self._on_passive_activity_requested)
        self.google_shell_sidebar.set_project_available(bool(getattr(self, "project_id", None)))
        self.google_shell_sidebar.set_legacy_emphasis(False)
        self._retired_legacy_sections = {"find", "devices", "videos", "locations", "duplicates"}
        self.google_shell_sidebar.set_retired_legacy_sections(self._retired_legacy_sections)

        # Legacy accordion in collapsible group
        self.legacy_tools_group = QGroupBox("Legacy Tools")
        self.legacy_tools_group.setObjectName("LegacyToolsGroup")
        self.legacy_tools_group.setCheckable(True)
        self.legacy_tools_group.setChecked(False)

        grp_lay = QVBoxLayout(self.legacy_tools_group)
        grp_lay.setContentsMargins(0, 0, 0, 0)
        grp_lay.setSpacing(0)
        accordion_widget.setMaximumHeight(160)
        grp_lay.addWidget(accordion_widget)

        lay.addWidget(self.google_shell_sidebar, 1)
        lay.addWidget(self.legacy_tools_group, 0)
        self._refresh_legacy_visibility_state()

        container.setStyleSheet("""
            QWidget#GoogleLeftShell {
                background: #f8f9fb;
                border-right: 1px solid #e7eaee;
            }
            QGroupBox#LegacyToolsGroup {
                font-weight: 600;
                font-size: 11px;
                border: 1px solid #e7eaee;
                border-radius: 12px;
                margin: 6px 6px 6px 6px;
                padding-top: 10px;
                background: #ffffff;
            }
            QGroupBox#LegacyToolsGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #5f6368;
            }
        """)

        self._refresh_passive_browse_payload()

        return container

    def _set_shell_active_branch(self, branch: str | None):
        try:
            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                if hasattr(self.google_shell_sidebar, "set_active_branch"):
                    self.google_shell_sidebar.set_active_branch(branch)
        except Exception:
            pass

    def _clear_shell_active_branch(self):
        try:
            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                if hasattr(self.google_shell_sidebar, "clear_active_branch"):
                    self.google_shell_sidebar.clear_active_branch()
        except Exception:
            pass

    def _on_disabled_shell_branch_requested(self, branch: str):
        """
        Phase 7B:
        Shell item clicked while no project exists.
        Keep shell primary, but soften behavior and guide the user.
        """
        try:
            if branch in {"all", "dates", "folders", "devices", "videos", "locations", "duplicates", "find"}:
                logger.info("[GooglePhotosLayout] Ignoring shell branch without project: %s", branch)
                self._set_shell_state_text("Create or select a project to use shell actions")

            if hasattr(self, "main_window") and self.main_window:
                try:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.information(
                        self.main_window,
                        "Create or select a project",
                        "This action becomes available after you create or select a project."
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _is_legacy_section_retired(self, section_name: str) -> bool:
        return section_name in getattr(self, "_retired_legacy_sections", set())

    def _refresh_legacy_visibility_state(self):
        """
        Phase 8:
        Keep legacy visible, but make it clearly secondary once enough shell
        paths are stable. Dates/People/Folders remain meaningful fallback.
        """
        try:
            if not hasattr(self, "legacy_tools_group") or self.legacy_tools_group is None:
                return

            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                self.google_shell_sidebar.set_legacy_emphasis(True)

            remaining_live_sections = {"dates", "folders", "people"}
            retired = getattr(self, "_retired_legacy_sections", set())

            if remaining_live_sections - retired:
                self.legacy_tools_group.setTitle("Legacy Tools, fallback")
            else:
                self.legacy_tools_group.setTitle("Legacy Tools")
        except Exception:
            pass

    def _set_view_mode(self, mode: str, description: str = ""):
        self._current_view_mode = mode

        # Stronger shell state text
        if description:
            self._set_shell_state_text(f"{mode.upper()} \u2022 {description}")
        else:
            self._set_shell_state_text(f"{mode.upper()} view")

        print(f"[{self.__class__.__name__}] View mode \u2192 {mode}")

    def _set_shell_state_text(self, text: str):
        try:
            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                if hasattr(self.google_shell_sidebar, "set_shell_state_text"):
                    self.google_shell_sidebar.set_shell_state_text(text)
        except Exception:
            pass

    def _clear_shell_state_text(self):
        try:
            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                if hasattr(self.google_shell_sidebar, "set_shell_state_text"):
                    self.google_shell_sidebar.set_shell_state_text("Ready")
        except Exception:
            pass

    def _on_passive_shell_branch_clicked(self, branch: str):
        """
        Phase 6B:
        Shell-first routing with legacy fallback retained.

        Rules:
        - People branches delegate to MainWindow people router
        - All Photos is a true grid reset action
        - Quick dates are direct grid actions
        - Legacy-detailed sections (dates/folders/devices/videos/locations/find/etc.)
          still delegate to accordion fallback
        - Legacy block remains alive and visible
        """
        import time

        try:
            if not hasattr(self, "accordion_sidebar") or self.accordion_sidebar is None:
                return

            shell_active_map = {
                "all": "all",
                "dates": "dates",
                "years": "dates",
                "months": "dates",
                "days": "dates",
                "today": "today",
                "yesterday": "yesterday",
                "last_7_days": "last_7_days",
                "last_30_days": "last_30_days",
                "this_month": "this_month",
                "last_month": "last_month",
                "this_year": "this_year",
                "last_year": "last_year",
                "folders": "folders",
                "devices": "devices",
                "favorites": "favorites",
                "videos": "videos",
                "documents": "documents",
                "screenshots": "screenshots",
                "duplicates": "duplicates",
                "locations": "locations",
                "discover_beach": "discover_beach",
                "discover_mountains": "discover_mountains",
                "discover_city": "discover_city",
                "find": "find",
                "people_merge_review": "people_merge_review",
                "people_unnamed": "people_unnamed",
                "people_show_all": "people_show_all",
            }
            self._set_shell_active_branch(shell_active_map.get(branch))

            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                if branch in {
                    "all",
                    "today",
                    "yesterday",
                    "last_7_days",
                    "last_30_days",
                    "this_month",
                    "last_month",
                    "this_year",
                    "last_year",
                    "people_merge_review",
                    "people_unnamed",
                    "people_show_all",
                    "find",
                    "discover_beach",
                    "discover_mountains",
                    "discover_city",
                    "favorites",
                    "documents",
                    "screenshots",
                    "duplicates",
                    "videos",
                    "locations",
                    "devices",
                }:
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                else:
                    self.google_shell_sidebar.set_legacy_emphasis(True)

            # ── People branches: MainWindow handler first, no removal of legacy fallback ──
            if branch.startswith("people_"):
                if hasattr(self, "main_window") and self.main_window:
                    if hasattr(self.main_window, "_handle_people_branch"):
                        self.main_window._handle_people_branch(branch)
                return

            # ── All Photos: shell-first direct grid reset ─────────────────────────────
            if branch == "all":
                self._set_view_mode("all", "All photos")
                if not getattr(self, "project_id", None):
                    return

                has_active_filters = (
                    getattr(self, "current_filter_year", None) is not None
                    or getattr(self, "current_filter_month", None) is not None
                    or getattr(self, "current_filter_day", None) is not None
                    or getattr(self, "current_filter_folder", None) is not None
                    or getattr(self, "current_filter_person", None) is not None
                    or getattr(self, "current_filter_paths", None) is not None
                    or getattr(self, "current_filter_group_id", None) is not None
                    or getattr(self, "current_filter_group_mode", None) is not None
                )

                if has_active_filters:
                    print(f"[{self.__class__.__name__}] browse_all: clearing filters and reloading grid")
                    if hasattr(self, "_clear_filter"):
                        self._clear_filter()
                    else:
                        self.request_reload(reason="browse_all")
                    return

                last_sig = getattr(self, "_last_reload_signature", None)
                if last_sig is None:
                    self.request_reload(reason="browse_all")
                else:
                    print(f"[{self.__class__.__name__}] browse_all: grid already at all-photos view, skipping")
                return

            # ── Quick dates: shell-first direct grid actions, legacy Dates remains owner ──
            quick_map = {
                "today": "today",
                "yesterday": "yesterday",
                "last_7_days": "last_7_days",
                "last_30_days": "last_30_days",
                "this_month": "this_month",
                "last_month": "last_month",
                "this_year": "this_year",
                "last_year": "last_year",
            }
            if branch in quick_map:
                # First expand legacy Quick/Date area for visual continuity
                now = time.time()
                target = "dates"
                if target != self._last_passive_section or (now - self._last_passive_section_ts) >= 1.0:
                    self._last_passive_section = target
                    self._last_passive_section_ts = now
                    if hasattr(self.accordion_sidebar, "_expand_section"):
                        # Prefer quick if available, else dates
                        try:
                            self.accordion_sidebar._expand_section("quick")
                        except Exception:
                            self.accordion_sidebar._expand_section("dates")

                # Then execute the actual direct grid action
                if hasattr(self, "_on_shell_quick_date_clicked"):
                    self._on_shell_quick_date_clicked(branch)
                return

            # ── Legacy-detailed sections: accordion fallback retained ─────────────────
            section_only_map = {
                "dates": "dates",
                "years": "dates",
                "months": "dates",
                "days": "dates",
                "folders": "folders",
                "devices": "devices",
                "videos": "videos",
                "locations": "locations",
                "duplicates": "duplicates",
                "favorites": "find",
                "documents": "find",
                "screenshots": "find",
                "find": "find",
                "discover_beach": "find",
                "discover_mountains": "find",
                "discover_city": "find",
            }

            target = section_only_map.get(branch)
            if not target:
                return

            # Phase 10: retired sections produce visible mode transitions
            if self._is_legacy_section_retired(target):
                if branch == "find":
                    self._set_shell_active_branch("find")
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("search", "Type to search your library")
                    # Focus search bar if available
                    try:
                        if hasattr(self.main_window, "top_search_bar") and self.main_window.top_search_bar:
                            self.main_window.top_search_bar.setFocus()
                        elif hasattr(self.main_window, "search_bar") and self.main_window.search_bar:
                            self.main_window.search_bar.setFocus()
                    except Exception:
                        pass
                    return

                if branch == "videos":
                    self._set_shell_active_branch("videos")
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("videos", "Showing video files")
                    try:
                        self.request_reload(reason="videos_only", video_only=True)
                    except Exception:
                        try:
                            self._load_photos()
                        except Exception:
                            pass
                    return

                if branch == "locations":
                    self._set_shell_active_branch("locations")
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("locations", "Grouped by location")
                    try:
                        if hasattr(self.accordion_sidebar, "_expand_section"):
                            self.accordion_sidebar._expand_section("locations")
                    except Exception:
                        pass
                    return

                if branch == "duplicates":
                    self._set_shell_active_branch("duplicates")
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("review", "Duplicates & similar shots")
                    try:
                        if hasattr(self, "_open_duplicates_dialog"):
                            self._open_duplicates_dialog()
                    except Exception:
                        pass
                    return

                if branch == "devices":
                    self._set_shell_active_branch("devices")
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("devices", "External sources")
                    try:
                        if hasattr(self.accordion_sidebar, "_expand_section"):
                            self.accordion_sidebar._expand_section("devices")
                    except Exception:
                        pass
                    return

                if branch in {"discover_beach", "discover_mountains", "discover_city"}:
                    self._set_shell_active_branch(branch)
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    preset = branch.replace("discover_", "").title()
                    self._set_view_mode("search", f"Discover preset, {preset}")
                    return

                if branch == "favorites":
                    self._set_shell_active_branch("favorites")
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("all", "Showing favorites")
                    if hasattr(self, "_filter_favorites"):
                        self._filter_favorites()
                    return

                if branch in {"documents", "screenshots"}:
                    self._set_shell_active_branch(branch)
                    self.google_shell_sidebar.set_legacy_emphasis(False)
                    self._set_view_mode("all", f"Showing {branch}")
                    self._load_photos()
                    return

                # Catch-all for any other retired section
                self._set_shell_active_branch(branch)
                self.google_shell_sidebar.set_legacy_emphasis(False)
                return

            now = time.time()
            if target == self._last_passive_section and (now - self._last_passive_section_ts) < 1.0:
                print(f"[{self.__class__.__name__}] Skipping duplicate passive section expand: {target}")
                return

            self._last_passive_section = target
            self._last_passive_section_ts = now

            if hasattr(self.accordion_sidebar, "_expand_section"):
                self.accordion_sidebar._expand_section(target)

        except Exception as e:
            print(f"[{self.__class__.__name__}] Passive shell click failed: {branch} → {e}")

    def _on_passive_activity_requested(self):
        """Phase 2B: open Activity Center via MainWindow toggle."""
        try:
            if hasattr(self, "main_window") and self.main_window:
                if hasattr(self.main_window, "_toggle_activity_center"):
                    self.main_window._toggle_activity_center()
        except Exception as e:
            print(f"[GooglePhotosLayout] Passive activity request failed: {e}")

    def _on_shell_quick_date_clicked(self, key: str):
        """
        Phase 6B:
        Direct grid action for shell quick-date clicks, while legacy Dates/Quick
        remains the detailed subsection owner.
        """
        try:
            self._set_shell_active_branch(key)
            self._set_shell_state_text(f"Quick date, {key.replace('_', ' ')}")
            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                self.google_shell_sidebar.set_legacy_emphasis(False)
            mapping = {
                "today": ("quick", "today"),
                "yesterday": ("quick", "yesterday"),
                "last_7_days": ("quick", "last_7_days"),
                "last_30_days": ("quick", "last_30_days"),
                "this_month": ("quick", "this_month"),
                "last_month": ("quick", "last_month"),
                "this_year": ("quick", "this_year"),
                "last_year": ("quick", "last_year"),
            }

            token = mapping.get(key)
            if not token:
                return

            # Reuse existing date-click path where possible
            section, quick_value = token

            # If accordion exposes a quick-selection API, prefer it
            if hasattr(self, "accordion_sidebar") and self.accordion_sidebar is not None:
                quick_section = None
                try:
                    quick_section = self.accordion_sidebar.section_logic.get("quick")
                except Exception:
                    quick_section = None

                if quick_section and hasattr(quick_section, "_on_quick_date_clicked"):
                    quick_section._on_quick_date_clicked(quick_value)
                    return

            # Fallback: synthesize to existing date-click logic
            from datetime import datetime, timedelta

            today = datetime.now().date()

            if key == "today":
                self._on_accordion_date_clicked(today.isoformat())
                return
            if key == "yesterday":
                self._on_accordion_date_clicked((today - timedelta(days=1)).isoformat())
                return
            if key == "this_year":
                self._on_accordion_date_clicked(str(today.year))
                return
            if key == "last_year":
                self._on_accordion_date_clicked(str(today.year - 1))
                return
            if key == "this_month":
                self._on_accordion_date_clicked(f"{today.year:04d}-{today.month:02d}")
                return

            # For range-style quick dates, use request_load path if needed
            if hasattr(self, "_request_load"):
                self._request_load(
                    thumb_size=self.current_thumb_size,
                    quick_date=key,
                    reset=True,
                    view_context=("quick_date", key),
                )
        except Exception as e:
            logger.debug("[GooglePhotosLayout] shell quick date failed: %s", e)

    # ── Phase 4: Browse payload helpers ─────────────────────────────

    def _refresh_passive_browse_payload(self):
        """
        Phase 4:
        lightweight parity counts only, no ownership changes.
        """
        try:
            if not hasattr(self, "google_shell_sidebar") or self.google_shell_sidebar is None:
                return

            counts = {
                "all": 0,
                "folders": None,
                "videos": None,
                "locations": None,
                "duplicates": None,
            }

            # Conservative seed values from current project state
            if getattr(self, "project_id", None):
                counts["all"] = 0

        except Exception as e:
            print(f"[{self.__class__.__name__}] Browse payload refresh failed: {e}")

    # ── end Phase 4 ──────────────────────────────────────────────────

    def _create_timeline(self) -> QWidget:
        """
        Create timeline scroll area with date groups.
        """
        # Scroll area
        self.timeline_scroll = QScrollArea()  # Store reference for scroll events
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setFrameShape(QFrame.NoFrame)
        self.timeline_scroll.setStyleSheet("""
            QScrollArea {
                background: white;
                border: none;
            }
        """)

        # Timeline container (holds date groups)
        self.timeline_container = QWidget()
        self.timeline_layout = QVBoxLayout(self.timeline_container)
        self.timeline_layout.setContentsMargins(20, 20, 20, 20)
        self.timeline_layout.setSpacing(30)
        self.timeline_layout.setAlignment(Qt.AlignTop)

        # PHASE 2 Task 2.1: Create loading indicator (initially hidden)
        # This shows while async photo query runs in background thread
        self._loading_indicator = QLabel("Loading photos...")
        self._loading_indicator.setAlignment(Qt.AlignCenter)
        self._loading_indicator.setStyleSheet("""
            QLabel {
                font-size: 14pt;
                color: #666;
                padding: 60px;
                background: white;
            }
        """)
        self._loading_indicator.hide()  # Initially hidden
        self.timeline_layout.addWidget(self._loading_indicator)

        self.timeline_scroll.setWidget(self.timeline_container)

        # QUICK WIN #1: Connect scroll event for lazy thumbnail loading
        # This enables ALL photos to load as user scrolls (removes 30-photo limit)
        self.timeline_scroll.verticalScrollBar().valueChanged.connect(
            self._on_timeline_scrolled
        )
        print("[GooglePhotosLayout] ✅ Scroll-triggered lazy loading enabled")

        # PHASE 2 #2: Setup drag-to-select rubber band
        self._setup_drag_select()

        return self.timeline_scroll

    # ── Reload coalescing helpers ──────────────────────────────

    def _compute_load_signature(self, params: dict) -> tuple:
        """Compute a hashable signature for a set of load parameters.

        If the signature matches the last executed load, the reload is
        skipped (no work done). This eliminates redundant sequential
        reloads that occur during mode switches, accordion clicks, etc.
        
        Important: include a view_context in the signature so two different
        UI sources (for example different People Groups) that currently map
        to the same set of paths still trigger a UI refresh.        
        """
        paths_sig = (
            tuple(sorted(params['paths'])) if params.get('paths') else None
        )

        view_ctx = params.get('view_context')
        if isinstance(view_ctx, dict):
            view_ctx = tuple(sorted(view_ctx.items()))
        elif isinstance(view_ctx, list):
            view_ctx = tuple(view_ctx)
        
        return (
            self.project_id,
            params.get('thumb_size'),
            params.get('year'),
            params.get('month'),
            params.get('day'),
            params.get('folder'),
            params.get('person'),
            paths_sig,
            view_ctx,
        )

    def _request_load(self, **params):
        """Schedule a coalesced photo load.
        """

        # Multiple rapid calls (e.g. accordion expand + tab switch) are
        # collapsed into a single load executed after a 50ms quiet period.
        # Freeze mutable collections at request time to prevent the
        # coalescing signature from collapsing when the caller mutates
        # the original list before _execute_coalesced_load fires.
        if params.get("paths") is not None:
            params["paths"] = list(params["paths"])
        self._pending_load_params = params
        self._load_coalesce_timer.start(50)

    def _execute_coalesced_load(self):
        """Fire the coalesced load if the state actually changed."""
        params = self._pending_load_params
        if params is None:
            return
        self._pending_load_params = None
        sig = self._compute_load_signature(params)
        if sig == self._last_load_signature:
            print("[GooglePhotosLayout] Skipping redundant reload (same state signature)")
            return
        self._last_load_signature = sig
        self._load_photos(
            thumb_size=params.get('thumb_size', 200),
            filter_year=params.get('year'),
            filter_month=params.get('month'),
            filter_day=params.get('day'),
            filter_folder=params.get('folder'),
            filter_person=params.get('person'),
            filter_paths=params.get('paths'),
        )

    def _load_photos(self, thumb_size: int = 200, filter_year: int = None, filter_month: int = None, filter_day: int = None, filter_folder: str = None, filter_person: str = None, filter_paths: list = None):
        """
        Load photos from database and populate timeline.

        STALE-WHILE-REVALIDATE PATTERN (v9.3.0):
        - Keeps existing content visible while loading new data
        - Shows subtle "refreshing" indicator instead of blank screen
        - Only clears timeline when new data is ready to display
        - Provides perceived performance improvement

        Args:
            thumb_size: Thumbnail size in pixels (default 200)
            filter_year: Optional year filter (e.g., 2024)
            filter_month: Optional month filter (1-12, requires filter_year)
            filter_day: Optional day filter (1-31, requires filter_year and filter_month)
            filter_folder: Optional folder path filter
            filter_person: Optional person/face cluster filter (branch_key)

        CRITICAL: Wrapped in comprehensive error handling to prevent crashes
        during/after scan operations when database might be in inconsistent state.
        """
        # Store current thumbnail size and filters
        self.current_thumb_size = thumb_size
        self.current_filter_year = filter_year
        self.current_filter_month = filter_month
        self.current_filter_day = filter_day
        self.current_filter_folder = filter_folder
        self.current_filter_person = filter_person
        # Freeze to avoid mutations from callers while worker is running.
        # IMPORTANT: preserve the distinction between [] (empty search results)
        # and None (no path filter at all).  Only convert truthy lists.
        if filter_paths is not None:
            self.current_filter_paths = list(filter_paths)
        else:
            self.current_filter_paths = None

        filter_desc = []
        if filter_year:
            filter_desc.append(f"year={filter_year}")
        if filter_month:
            filter_desc.append(f"month={filter_month}")
        if filter_day:
            filter_desc.append(f"day={filter_day}")
        if filter_folder:
            filter_desc.append(f"folder={filter_folder}")
        if filter_person:
            filter_desc.append(f"person={filter_person}")
        if filter_paths:
            filter_desc.append(f"paths={len(filter_paths)} photos")

        filter_str = f" [{', '.join(filter_desc)}]" if filter_desc else ""
        print(f"[GooglePhotosLayout] 📷 Loading photos from database (thumb size: {thumb_size}px){filter_str}...")

        # Show/hide Clear Filter button based on whether filters are active
        has_filters = filter_year is not None or filter_month is not None or filter_day is not None or filter_folder is not None or filter_person is not None or filter_paths is not None
        self.btn_clear_filter.setVisible(has_filters)

        # ═══════════════════════════════════════════════════════════════════
        # STALE-WHILE-REVALIDATE: Don't clear existing content!
        # Keep showing current thumbnails while new data loads in background.
        # Only clear when new data is ready (in _display_photos_in_timeline)
        # ═══════════════════════════════════════════════════════════════════

        # Show subtle "refreshing" indicator OVER existing content
        self._show_refresh_indicator()

        # Reset inflight tracking for new load
        self._thumb_inflight.clear()

        # PHASE 2 Task 2.1: Increment generation (discard stale results)
        self._photo_load_generation += 1
        current_gen = self._photo_load_generation
        self._photo_load_in_progress = True

        print(f"[GooglePhotosLayout] 🔍 Starting async photo load (generation {current_gen}) - existing content preserved...")

        # CRITICAL: Check if we have a valid project
        if self.project_id is None:
            # No project - show empty state with instructions
            self._hide_refresh_indicator()
            self._clear_timeline_for_new_content()
            empty_label = QLabel("📂 No project selected\n\nClick '➕ New Project' to create your first project")
            empty_label.setAlignment(Qt.AlignCenter)
            empty_label.setStyleSheet("font-size: 12pt; color: #888; padding: 60px;")
            self.timeline_layout.addWidget(empty_label)
            print("[GooglePhotosLayout] ⚠️ No project selected")
            return

        # Build filter params (legacy format for PhotoLoadWorker)
        filter_params = {
            'year': filter_year,
            'month': filter_month,
            'day': filter_day,
            'folder': filter_folder,
            'person': filter_person,
            'paths': filter_paths
        }

        # ── Decide: paged loading vs legacy single-query ──────────
        # filter_paths (location filter) is not supported by PhotoQueryService
        # so fall back to the legacy all-at-once PhotoLoadWorker for that case.

        # Guard: empty path list means "zero search results" — short-circuit
        # to display the empty state immediately instead of querying the DB
        # (which would return ALL photos since no IN-clause is built).
        if filter_paths is not None and len(filter_paths) == 0:
            self._hide_refresh_indicator()
            self._photo_load_in_progress = False
            self._clear_timeline_for_new_content()
            empty_widget = self._create_empty_state(
                icon="\U0001f50d",
                title="No matching photos",
                message="Try a different search or filter.",
                action_text=""
            )
            self.timeline_layout.addWidget(empty_widget)
            print(f"[GooglePhotosLayout] Empty path list → empty state (generation {current_gen})")
            return

        if filter_paths is not None:
            # Legacy path: load everything in one shot
            worker = PhotoLoadWorker(
                project_id=self.project_id,
                filter_params=filter_params,
                generation=current_gen,
                signals=self.photo_load_signals
            )
            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._photo_load_worker = worker
            QThreadPool.globalInstance().start(worker)
            print(f"[GooglePhotosLayout] Photo load worker started (generation {current_gen}, legacy/paths)")
        else:
            # Paged loading path via PhotoPageWorker + PhotoQueryService
            from workers.photo_page_worker import PhotoPageWorker

            # Map filter names → PhotoQueryService filter dict
            pq_filters = {}
            if filter_year is not None:
                pq_filters["year"] = filter_year
            if filter_month is not None:
                pq_filters["month"] = filter_month
            if filter_day is not None:
                pq_filters["day"] = filter_day
            if filter_folder is not None:
                pq_filters["folder"] = filter_folder
            if filter_person is not None:
                pq_filters["person_branch_key"] = filter_person

            # Reset paging state
            self._paging_total = 0
            self._paging_loaded = 0
            self._paging_offset = 0
            self._paging_active = True
            self._paging_fetching = True
            self._paging_all_rows = []
            self._paging_filters = pq_filters

            # Dispatch first page with count
            worker = PhotoPageWorker(
                project_id=self.project_id,
                generation=current_gen,
                offset=0,
                limit=self._page_size,
                filters=pq_filters,
                signals=self._page_signals,
                include_count=True,
            )
            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._page_worker = worker
            QThreadPool.globalInstance().start(worker)
            print(f"[GooglePhotosLayout] Paged load started (generation {current_gen}, page_size={self._page_size})")

    def _show_refresh_indicator(self):
        """Show subtle refresh indicator over existing content."""
        try:
            if not hasattr(self, '_refresh_overlay') or self._refresh_overlay is None:
                self._refresh_overlay = QLabel("↻ Refreshing...")
                self._refresh_overlay.setAlignment(Qt.AlignCenter)
                self._refresh_overlay.setStyleSheet("""
                    QLabel {
                        background: rgba(255, 255, 255, 0.9);
                        color: #1976D2;
                        font-size: 12px;
                        padding: 8px 16px;
                        border-radius: 16px;
                        border: 1px solid #e0e0e0;
                    }
                """)
                self._refresh_overlay.setFixedSize(120, 36)

            # Position at top center of timeline
            if hasattr(self, 'scroll_area') and self.scroll_area:
                self._refresh_overlay.setParent(self.scroll_area)
                self._refresh_overlay.move(
                    (self.scroll_area.width() - 120) // 2,
                    10
                )
                self._refresh_overlay.raise_()
                self._refresh_overlay.show()
        except Exception as e:
            logger.debug(f"[GooglePhotosLayout] Could not show refresh indicator: {e}")

    def _hide_refresh_indicator(self):
        """Hide the refresh indicator."""
        try:
            if hasattr(self, '_refresh_overlay') and self._refresh_overlay:
                self._refresh_overlay.hide()
        except Exception:
            pass

    def _clear_timeline_for_new_content(self):
        """Clear timeline only when new content is ready to display."""
        try:
            while self.timeline_layout.count():
                child = self.timeline_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # Clear thumbnail button cache and reset load counter
            self.thumbnail_buttons.clear()
            self.thumbnail_load_count = 0
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error clearing timeline: {e}")

    def _group_photos_by_date(self, rows) -> Dict[str, List[Tuple]]:
        """
        Group photos by date (YYYY-MM-DD).

        Uses created_date which is ALWAYS populated (never NULL).
        created_date = date_taken if available, otherwise file modified date.

        Returns:
            dict: {date_str: [(path, date_taken, width, height), ...]}
        """
        groups = defaultdict(list)

        for row in rows:
            path, date_taken, width, height = row

            # created_date is always in YYYY-MM-DD format, so we can use it directly
            # No need to parse or handle NULL values
            if date_taken:  # Should always be true since created_date is never NULL
                groups[date_taken].append((path, date_taken, width, height))
            else:
                # Fallback (should never happen with created_date)
                print(f"[GooglePhotosLayout] ⚠️ WARNING: Photo has no created_date: {path}")

        return dict(groups)

    def _build_timeline_tree(self, photos_by_date: Dict[str, List[Tuple]]):
        """
        Build timeline tree in sidebar (Years > Months with counts).

        Uses created_date which is always in YYYY-MM-DD format.
        NOTE: With AccordionSidebar, this is handled internally - this method is a no-op.
        """
        # Old sidebar implementation - no longer needed with AccordionSidebar
        if not hasattr(self, 'timeline_tree'):
            return

        # Group by year and month
        years_months = defaultdict(lambda: defaultdict(int))

        for date_str in photos_by_date.keys():
            # created_date is always YYYY-MM-DD format, can parse directly
            try:
                date_obj = datetime.fromisoformat(date_str)
                year = date_obj.year
                month = date_obj.month
                count = len(photos_by_date[date_str])
                years_months[year][month] += count
            except Exception as e:
                print(f"[GooglePhotosLayout] ⚠️ Failed to parse date '{date_str}': {e}")
                continue

        # Build tree
        for year in sorted(years_months.keys(), reverse=True):
            year_item = QTreeWidgetItem([f"📅 {year}"])
            role_set_json(year_item, {"type": "year", "year": year}, role=Qt.UserRole)
            year_item.setExpanded(True)
            self.timeline_tree.addTopLevelItem(year_item)

            for month in sorted(years_months[year].keys(), reverse=True):
                count = years_months[year][month]
                month_name = datetime(year, month, 1).strftime("%B")
                month_item = QTreeWidgetItem([f"  • {month_name} ({count})"])
                role_set_json(month_item, {"type": "month", "year": year, "month": month}, role=Qt.UserRole)
                year_item.addChild(month_item)

    def _build_folders_tree(self, rows):
        """
        Build folders tree in sidebar (folder hierarchy with counts).

        Args:
            rows: List of (path, date_taken, width, height) tuples
        NOTE: With AccordionSidebar, this is handled internally - this method is a no-op.
        """
        # Old sidebar implementation - no longer needed with AccordionSidebar
        if not hasattr(self, 'folders_tree'):
            return

        # Group photos by parent folder
        folder_counts = defaultdict(int)

        for row in rows:
            path = row[0]
            parent_folder = os.path.dirname(path)
            folder_counts[parent_folder] += 1

        # Sort folders by count (most photos first)
        sorted_folders = sorted(folder_counts.items(), key=lambda x: x[1], reverse=True)

        # Build tree (show top 10 folders)
        for folder, count in sorted_folders[:10]:
            # Show only folder name, not full path
            folder_name = os.path.basename(folder) if folder else "(Root)"
            if not folder_name:
                folder_name = folder  # Show full path if basename is empty

            folder_item = QTreeWidgetItem([f"📁 {folder_name} ({count})"])
            role_set_json(folder_item, {"type": "folder", "path": folder}, role=Qt.UserRole)
            folder_item.setToolTip(0, folder)  # Show full path on hover
            self.folders_tree.addTopLevelItem(folder_item)
        # Update Folders section count (sum of all photos across folders)
        try:
            if hasattr(self, 'folders_section'):
                self.folders_section.update_count(sum(folder_counts.values()))
        except Exception:
            pass

    def _on_timeline_item_clicked(self, item: QTreeWidgetItem, column: int):
        """
        Handle timeline tree item click - filter by year or month.

        Args:
            item: Clicked tree item
            column: Column index (always 0)
        """
        data = role_get_json(item, role=Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")

        if item_type == "year":
            year = data.get("year")
            print(f"[GooglePhotosLayout] Filtering by year: {year}")
            self._load_photos(
                thumb_size=self.current_thumb_size,
                filter_year=year,
                filter_month=None,
                filter_day=None,
                filter_folder=None,
                filter_person=None
            )
        elif item_type == "month":
            year = data.get("year")
            month = data.get("month")
            month_name = datetime(year, month, 1).strftime("%B %Y")
            print(f"[GooglePhotosLayout] Filtering by month: {month_name}")
            self._load_photos(
                thumb_size=self.current_thumb_size,
                filter_year=year,
                filter_month=month,
                filter_day=None,
                filter_folder=None,
                filter_person=None
            )

    def _on_folder_item_clicked(self, item: QTreeWidgetItem, column: int):
        """
        Handle folder tree item click - filter by folder.

        Args:
            item: Clicked tree item
            column: Column index (always 0)
        """
        data = role_get_json(item, role=Qt.UserRole)
        if not data:
            return

        folder_path = data.get("path")
        if folder_path:
            folder_name = os.path.basename(folder_path) if folder_path else "(Root)"
            print(f"[GooglePhotosLayout] Filtering by folder: {folder_name}")
            self._load_photos(
                thumb_size=self.current_thumb_size,
                filter_year=None,
                filter_month=None,
                filter_day=None,
                filter_folder=folder_path,
                filter_person=None
            )

    def _build_tags_tree(self):
        """
        Build tags tree in sidebar (shows all tags with counts).
        NOTE: With AccordionSidebar, this is handled internally - this method is a no-op.
        """
        # Old sidebar implementation - no longer needed with AccordionSidebar
        if not hasattr(self, 'tags_tree'):
            return

        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            tag_rows = tag_service.get_all_tags_with_counts(self.project_id) or []
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error loading tags: {e}")
            import traceback
            traceback.print_exc()
            return

        # Clear and update count
        self.tags_tree.clear()
        total_count = sum(int(c or 0) for _, c in tag_rows)
        if hasattr(self, 'tags_section'):
            self.tags_section.update_count(total_count)

        # Icon mapping for common tags
        ICONS = {
            'favorite': '⭐',
            'face': '👤',
            'important': '⚑',
            'work': '💼',
            'travel': '✈',
            'personal': '♥',
            'family': '👨‍👩‍👧',
            'archive': '📦',
        }

        # Populate tree
        for tag_name, count in tag_rows:
            icon = ICONS.get(tag_name.lower(), '🏷️')
            count_text = f" ({count})" if count else ""
            display = f"{icon} {tag_name}{count_text}"
            item = QTreeWidgetItem([display])
            item.setData(0, Qt.UserRole, tag_name)
            self.tags_tree.addTopLevelItem(item)

        print(f"[GooglePhotosLayout] ✓ Built tags tree: {len(tag_rows)} tags")
    
    def _on_tags_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle tags tree item click - filter timeline by tag."""
        tag_name = item.data(0, Qt.UserRole)
        if not tag_name:
            return
        self._filter_by_tag(tag_name)


    def _build_people_tree(self):
        """
        Build people grid/tree in sidebar (face clusters with counts).

        Phase 1+2: Now populates both grid view AND tree (tree hidden, kept for compatibility).
        Queries face_branch_reps table for detected faces/people.
        NOTE: With AccordionSidebar, this is handled internally - this method is a no-op.
        """
        # Old sidebar implementation - no longer needed with AccordionSidebar
        if not hasattr(self, 'people_grid') and not hasattr(self, 'people_tree'):
            return

        print("[GooglePhotosLayout] 🔍 _build_people_tree() called")
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Query ALL face clusters for current project (removed LIMIT 10)
            # Grid view can handle many more people than tree view!
            query = """
                SELECT branch_key, label, count, rep_path, rep_thumb_png
                FROM face_branch_reps
                WHERE project_id = ?
                ORDER BY count DESC
            """

            print(f"[GooglePhotosLayout] 👥 Querying face_branch_reps for project_id={self.project_id}")

            with db._connect() as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                cur = conn.cursor()
                cur.execute(query, (self.project_id,))
                rows = cur.fetchall()

            print(f"[GooglePhotosLayout] 👥 Found {len(rows)} face clusters in database")

            # Update section count badge
            if hasattr(self, 'people_section'):
                total_photos = sum(int(c or 0) for _, _, c, _, _ in rows)
                self.people_section.update_count(total_photos)
                print(f"[GooglePhotosLayout] ✓ Updated people section count badge: {total_photos}")
            else:
                print("[GooglePhotosLayout] ⚠️ people_section not found!")

            # Clear existing grid
            if hasattr(self, 'people_grid'):
                self.people_grid.clear()
                print(f"[GooglePhotosLayout] ✓ Cleared people grid")
            else:
                print("[GooglePhotosLayout] ⚠️ people_grid not found!")
                return

            if not rows:
                # No face clusters found
                print("[GooglePhotosLayout] No faces found - grid will show empty state")
                # Grid shows its own empty state message
                return

            # Populate GRID VIEW (Phase 1+2)
            added_count = 0
            for branch_key, label, count, rep_path, rep_thumb_png in rows:
                # Use label if set, otherwise use "Unnamed"
                display_name = label if label else "Unnamed"

                # Load face thumbnail as pixmap
                face_pixmap = None
                if rep_thumb_png:
                    try:
                        from PySide6.QtGui import QPixmap
                        import base64
                        img_data = base64.b64decode(rep_thumb_png)
                        face_pixmap = QPixmap()
                        success = face_pixmap.loadFromData(img_data)
                        if success:
                            print(f"[GooglePhotosLayout]   ✓ Loaded pixmap for {display_name}: {face_pixmap.width()}x{face_pixmap.height()}")
                        else:
                            print(f"[GooglePhotosLayout]   ⚠️ Failed to load pixmap for {display_name}")
                            face_pixmap = None
                    except Exception as e:
                        print(f"[GooglePhotosLayout] ⚠️ Error loading face pixmap for {display_name}: {e}")
                        face_pixmap = None
                # Fallback: if no BLOB thumbnail, try loading from representative file path
                if face_pixmap is None and rep_path:
                    try:
                        from PySide6.QtGui import QPixmap
                        import os
                        if os.path.exists(rep_path):
                            file_pixmap = QPixmap(rep_path)
                            if not file_pixmap.isNull():
                                face_pixmap = file_pixmap
                                print(f"[GooglePhotosLayout]   ✓ Loaded pixmap from file for {display_name}")
                            else:
                                print(f"[GooglePhotosLayout]   ⚠️ Pixmap from file is null for {display_name}")
                        else:
                            print(f"[GooglePhotosLayout]   ⚠️ rep_path not found for {display_name}: {rep_path}")
                    except Exception as e:
                        print(f"[GooglePhotosLayout] ⚠️ Error loading face pixmap from file for {display_name}: {e}")

                # Add to grid with both branch_key and display_name
                if hasattr(self, 'people_grid'):
                    self.people_grid.add_person(branch_key, display_name, face_pixmap, count)
                    added_count += 1
                    print(f"[GooglePhotosLayout]   ✓ Added to grid [{added_count}/{len(rows)}]: {display_name} ({count} photos)")

            print(f"[GooglePhotosLayout] ✅ Populated people grid with {added_count} faces")

            # Also populate old tree (hidden, for backward compatibility)
            # This ensures any code that references self.people_tree still works
            for branch_key, label, count, rep_path, rep_thumb_png in rows:
                display_name = label if label else f"Unnamed Person"
                person_item = QTreeWidgetItem([f"{display_name} ({count})"])
                role_set_json(person_item, {"type": "person", "branch_key": branch_key, "label": label}, role=Qt.UserRole)

                icon = self._load_face_thumbnail(rep_path, rep_thumb_png)
                if icon:
                    person_item.setIcon(0, icon)
                else:
                    person_item.setText(0, f"👤 {display_name} ({count})")

                self.people_tree.addTopLevelItem(person_item)

            print(f"[GooglePhotosLayout] ✅ _build_people_tree() completed successfully")

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error building people grid: {e}")
            import traceback
            traceback.print_exc()

    def _make_circular_face_icon(self, pixmap: QPixmap, size: int = 64) -> QIcon:
        """
        Create circular face icon (Google Photos / iPhone Photos style).

        Args:
            pixmap: Source pixmap
            size: Diameter of circular icon

        Returns:
            QIcon with circular face thumbnail
        """
        from PySide6.QtGui import QPainter, QPainterPath

        # Scale to target size
        scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

        # Create circular mask
        circular = QPixmap(size, size)
        circular.fill(Qt.transparent)

        painter = QPainter(circular)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Create circular clip path
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        # Draw the image within the circle (centered)
        x_offset = (scaled.width() - size) // 2
        y_offset = (scaled.height() - size) // 2
        painter.drawPixmap(-x_offset, -y_offset, scaled)

        painter.end()

        return QIcon(circular)

    def _on_accordion_date_clicked(self, date_key: str):
        """
        Handle accordion sidebar date selection.

        Args:
            date_key: Date in format "YYYY", "YYYY-MM", or "YYYY-MM-DD"
        """
        print(f"[GooglePhotosLayout] Accordion date clicked: {date_key}")

        self._set_shell_active_branch("dates")
        self._set_shell_state_text(f"Date filter, {date_key}")
        if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
            self.google_shell_sidebar.set_legacy_emphasis(True)

        # Parse date_key to extract year, month, day
        parts = date_key.split("-")
        year = None
        month = None
        day = None

        if len(parts) >= 1:
            try:
                year = int(parts[0])
            except ValueError:
                pass

        if len(parts) >= 2:
            try:
                month = int(parts[1])
            except ValueError:
                pass
        
        # BUG FIX: Parse day from date_key (was missing!)
        if len(parts) >= 3:
            try:
                day = int(parts[2])
            except ValueError:
                pass

        # Filter by year, month, or day (coalesced)
        self._request_load(
            thumb_size=self.current_thumb_size,
            year=year, month=month, day=day,
        )

    def _on_accordion_folder_clicked(self, folder_id: int):
        """
        Handle accordion sidebar folder selection with debouncing.

        FIX 2026-02-08: Added debouncing to prevent double-clicks from triggering
        multiple expensive _load_photos() calls.

        Args:
            folder_id: Folder ID from database
        """
        # Skip if same folder already pending or currently displayed
        if self._pending_folder_id == folder_id:
            print(f"[GooglePhotosLayout] Folder {folder_id} already pending, skipping")
            return

        print(f"[GooglePhotosLayout] ========================================")
        print(f"[GooglePhotosLayout] Accordion folder clicked: folder_id={folder_id} (debouncing...)")

        # Store pending folder and start debounce timer
        self._pending_folder_id = folder_id

        # Cancel any pending timer and restart
        if self._folder_click_debounce_timer.isActive():
            self._folder_click_debounce_timer.stop()
        self._folder_click_debounce_timer.start(self._folder_click_debounce_delay)

    def _execute_folder_click(self):
        """
        Execute the actual folder load after debounce delay.

        FIX 2026-02-08: Separated from _on_accordion_folder_clicked for debouncing.
        """
        folder_id = self._pending_folder_id
        if folder_id is None:
            return

        print(f"[GooglePhotosLayout] Executing debounced folder click: folder_id={folder_id}")

        # Get folder path from database
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()
                # CRITICAL FIX: Use photo_folders table instead of non-existent folders table
                cur.execute("SELECT path FROM photo_folders WHERE id = ?", (folder_id,))
                row = cur.fetchone()
                if row:
                    folder_path = row[0]
                    print(f"[GooglePhotosLayout] Found folder path: {folder_path}")
                    print(f"[GooglePhotosLayout] Calling _load_photos with filter_folder={folder_path}")
                    self._set_shell_active_branch("folders")
                    self._set_shell_state_text("Folder filter active")
                    if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                        self.google_shell_sidebar.set_legacy_emphasis(True)
                    self._load_photos(
                        thumb_size=self.current_thumb_size,
                        filter_year=None,
                        filter_month=None,
                        filter_day=None,
                        filter_folder=folder_path,
                        filter_person=None
                    )
                    print(f"[GooglePhotosLayout] _load_photos call completed")
                else:
                    print(f"[GooglePhotosLayout] ERROR: No folder found with id={folder_id}")
        except Exception as e:
            print(f"[GooglePhotosLayout] ERROR loading folder: {e}")
            import traceback
            traceback.print_exc()
        print(f"[GooglePhotosLayout] ========================================")

    def _on_accordion_tag_clicked(self, tag_name: str):
        """
        Handle accordion sidebar tag selection.

        Args:
            tag_name: Tag name to filter by
        """
        print(f"[GooglePhotosLayout] Accordion tag clicked: {tag_name}")
        self._filter_by_tag(tag_name)

    def _on_accordion_branch_clicked(self, branch_key: str):
        """
        Handle accordion sidebar branch/person selection.

        Args:
            branch_key: Branch key, may include "branch:" prefix or "facecluster:" prefix
        """
        print(f"[GooglePhotosLayout] Accordion branch clicked: {branch_key}")

        self._set_shell_active_branch("people_show_all")

        # Remove prefixes if present
        if branch_key.startswith("branch:"):
            branch_key = branch_key[7:]
        elif branch_key.startswith("facecluster:"):
            branch_key = branch_key[12:]

        # Filter by person/branch (coalesced)
        self._request_load(
            thumb_size=self.current_thumb_size,
            person=branch_key,
        )

    def _on_accordion_person_clicked(self, person_branch_key: str):
        """
        Handle people selection from the accordion sidebar.

        Args:
            person_branch_key: Identifier for the face cluster to filter by.
        """
        self._set_shell_active_branch("people_show_all")
        if person_branch_key:
            self._set_shell_state_text("People filter active")
        if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
            self.google_shell_sidebar.set_legacy_emphasis(False)

        # Empty string signals clearing the active person filter (toggle off)
        if person_branch_key == "":
            logger.info("[GooglePhotosLayout] Clearing person filter from accordion toggle")
            self._set_shell_active_branch("all")
            self._set_shell_state_text("Showing all photos")
            self._request_load(
                thumb_size=self.current_thumb_size,
                year=self.current_filter_year,
                month=self.current_filter_month,
                day=self.current_filter_day,
                folder=self.current_filter_folder,
            )
            return

        if not person_branch_key:
            return

        logger.info(
            "[GooglePhotosLayout] Accordion person clicked: %s", person_branch_key
        )

        self._request_load(
            thumb_size=self.current_thumb_size,
            person=person_branch_key,
        )

    # --- Groups sub-section handlers (Person Groups feature) ---

    def _on_accordion_group_clicked(self, group_id: int, match_mode: str = "together"):
        """
        Handle group selection from the Groups sub-section.

        When user clicks a group, filters photos to show only photos
        where ALL group members appear together (AND matching).

        Args:
            group_id: ID of the selected group (-1 for deselection)
            match_mode: Matching mode ('together', 'any', etc.). Defaults to 'together'.
        """
        # Handle invalid/deselection group IDs (None, 0, or negative values like -1)
        if group_id is None or group_id < 1:
            logger.info(f"[GooglePhotosLayout] Group deselected or invalid (group_id={group_id})")

            # Clear group state
            self.current_filter_group_id = None
            self.current_filter_group_mode = None

            # Force reload to ALL photos context
            self._request_load(
                thumb_size=self.current_thumb_size,
                reset=True,
                view_context=None,
            )
            return


        logger.info(f"[GooglePhotosLayout] Group clicked: {group_id} (mode={match_mode})")

        try:
            from services.group_service import GroupService
            from reference_db import ReferenceDB

            db = ReferenceDB()
            scope = "same_photo" if match_mode == "together" else "event_window"
            # Get matching photo paths — falls back to live AND query if no cache
            paths = GroupService.get_cached_match_paths(db, self.project_id, group_id, scope)
            db.close()

            if not paths:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self.main_window,
                    "No Photos Found",
                    "No photos found where all group members appear together.\n\n"
                    "Try: Right-click the group → Recompute (Together).\n"
                    "If faces were recently re-scanned, group members may need "
                    "to be updated."
                )

                # Reset group state
                self.current_filter_group_id = None
                self.current_filter_group_mode = None
    
                # optional, revert to All Photos if group has no results
                self._request_load(
                    thumb_size=self.current_thumb_size, 
                    reset=True, 
                    view_context=None
                )                  
                return

            logger.info(f"[GooglePhotosLayout] Group {group_id} has {len(paths)} matching photos")
            
            # Track active group context for later refreshes and correct UI state
            self.current_filter_group_id = int(group_id)
            self.current_filter_group_mode = str(match_mode)            

            # Load photos filtered by group paths
            self._request_load(
                thumb_size=self.current_thumb_size,
                paths=paths,
                view_context=("group", int(group_id), str(match_mode)),
                reset=True,
            )

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to load group photos: {e}", exc_info=True)

    def _on_group_created(self):
        """Handle new group creation - refresh sidebar groups."""
        logger.info("[GooglePhotosLayout] Group created, refreshing People section")
        self._refresh_people_sidebar()

    def _on_group_edit_requested(self, group_id: int):
        """Handle group edit request - open edit dialog, then recompute matches.

        Fix: The old flow called _compute_group_matches which silently failed
        to refresh the groups list (callback was delivered on a non-main thread).
        Now _compute_group_matches marshals the UI refresh via QTimer.singleShot,
        so the group card will update with the correct photo count after edit.
        """
        logger.info(f"[GooglePhotosLayout] Edit group requested: {group_id}")

        try:
            from ui.create_group_dialog import CreateGroupDialog

            dialog = CreateGroupDialog(self.project_id, edit_group_id=group_id, parent=self.main_window)
            if dialog.exec():
                # Recompute group matches (members may have changed) — the
                # on_finished callback in _compute_group_matches now correctly
                # marshals reload_groups() onto the main thread.
                if hasattr(self, "accordion_sidebar") and hasattr(self.accordion_sidebar, '_compute_group_matches'):
                    self.accordion_sidebar._compute_group_matches(group_id, 'together')
                else:
                    self._refresh_people_sidebar()
                logger.info(f"[GooglePhotosLayout] Group {group_id} edited, recomputing matches")
        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to open edit group dialog: {e}", exc_info=True)


    def _on_group_deleted(self, group_id: int):
        logger.info(f"[GooglePhotosLayout] Group deleted: {group_id}")

        # If currently viewing this group, clear filter
        if getattr(self, "current_filter_group_id", None) == group_id:
            self.current_filter_group_id = None
            self.current_filter_group_mode = None

            self._request_load(
                thumb_size=self.current_thumb_size,
                reset=True,
                view_context=None,
            )


    def _on_accordion_location_clicked(self, location_data: dict):
        """
        Handle location selection from the accordion sidebar (GPS filtering).

        When user clicks a location cluster, filters photos to show only
        photos from that geographic location.

        Args:
            location_data: Dict with {name, lat, lon, count, paths}
        """
        logger.info(
            "[GooglePhotosLayout] Accordion location clicked: %s (%d photos)",
            location_data.get('name', 'Unknown'),
            location_data.get('count', 0)
        )

        self._set_shell_active_branch("locations")
        self._set_shell_state_text(
            f"Location, {location_data.get('name', 'Unknown Location')}"
        )
        if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
            self.google_shell_sidebar.set_legacy_emphasis(True)

        # Extract paths from location cluster
        paths = location_data.get('paths', [])

        if not paths:
            logger.warning("[GooglePhotosLayout] Location has no photos")
            return

        # Load photos filtered by this location's paths (coalesced)
        self._request_load(
            thumb_size=self.current_thumb_size,
            paths=paths,
            reset=True,
            view_context=("location", location_data.get("name"), int(location_data.get("count", 0))),
            
        )

    def _on_accordion_person_merged(self, source_branch: str, target_branch: str):
        """Keep active person filters in sync after a merge in the sidebar."""
        active_person = getattr(self, "current_filter_person", None)
        if active_person not in (source_branch, target_branch):
            return

        logger.info(
            "[GooglePhotosLayout] Person merge detected (%s -> %s); refreshing grid",
            source_branch,
            target_branch,
        )

        # If we were filtered on the source, switch to the target; if already on target, refresh
        new_person = target_branch if active_person == source_branch else active_person
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=self.current_filter_year,
            filter_month=self.current_filter_month,
            filter_day=self.current_filter_day,
            filter_folder=self.current_filter_folder,
            filter_person=new_person,
        )

    # --- People tools surfaced from accordion ---
    def _refresh_people_sidebar(self):
        try:
            if hasattr(self, "accordion_sidebar"):
                self.accordion_sidebar.reload_people_section()
        except Exception as e:
            logger.debug("[GooglePhotosLayout] Failed to refresh people section after tool action: %s", e)

    def _on_people_merge_history_requested(self):
        try:
            self._show_merge_history()
            self._refresh_people_sidebar()
        except Exception as e:
            logger.debug("[GooglePhotosLayout] Merge history request failed: %s", e, exc_info=True)

    def _on_people_undo_requested(self):
        try:
            self._undo_last_merge()
            self._refresh_people_sidebar()
        except Exception as e:
            logger.debug("[GooglePhotosLayout] Undo merge request failed: %s", e, exc_info=True)

    def _on_people_redo_requested(self):
        try:
            self._redo_last_undo()
            self._refresh_people_sidebar()
        except Exception as e:
            logger.debug("[GooglePhotosLayout] Redo merge request failed: %s", e, exc_info=True)

    def _on_people_tools_requested(self):
        """Show People Tools menu with advanced face detection options."""
        try:
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction, QCursor

            # Create menu with tools
            menu = QMenu()

            # Bulk Review action
            bulk_review_action = QAction("🧰 Bulk Face Review & Naming", menu)
            bulk_review_action.setToolTip("Review all detected people and assign names")
            bulk_review_action.triggered.connect(self._prompt_bulk_face_review if hasattr(self, "_prompt_bulk_face_review") else self._open_people_tools)
            menu.addAction(bulk_review_action)

            # Quality Dashboard action
            quality_dashboard_action = QAction("📊 Face Quality Dashboard", menu)
            quality_dashboard_action.setToolTip("View face detection statistics and quality metrics")
            quality_dashboard_action.triggered.connect(self._open_face_quality_dashboard)
            menu.addAction(quality_dashboard_action)

            menu.addSeparator()

            # Manual Face Crop action
            manual_crop_action = QAction("✏️ Manual Face Crop Editor", menu)
            manual_crop_action.setToolTip("Review and manually correct face detections")
            manual_crop_action.triggered.connect(self._open_manual_face_crop_selector)
            menu.addAction(manual_crop_action)

            # Show menu at cursor
            menu.exec(QCursor.pos())

        except Exception as e:
            logger.debug("[GooglePhotosLayout] People tools request failed: %s", e, exc_info=True)

    def _open_face_quality_dashboard(self):
        """Open Face Quality Dashboard showing statistics and review tools."""
        try:
            from ui.face_quality_dashboard import FaceQualityDashboard

            dashboard = FaceQualityDashboard(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Connect signals to handle manual crop requests
            dashboard.manualCropRequested.connect(self._open_manual_face_crop_editor)

            dashboard.show()
            logger.info("[GooglePhotosLayout] Opened Face Quality Dashboard")

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to open Face Quality Dashboard: {e}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error",
                f"Failed to open Face Quality Dashboard:\n{e}"
            )

    def _open_manual_face_crop_selector(self):
        """Show visual photo browser to select a photo for manual face cropping."""
        try:
            from ui.visual_photo_browser import PhotoBrowserDialog

            # Show visual photo browser
            browser = PhotoBrowserDialog(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Connect signal to open editor when photo is selected
            browser.photoSelected.connect(self._open_manual_face_crop_editor)

            browser.exec()

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to open photo selector: {e}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error",
                f"Failed to open photo selector:\n{e}"
            )

    def _open_manual_face_crop_editor(self, photo_path: str):
        """Open Manual Face Crop Editor for the specified photo."""
        try:
            logger.info(f"[GooglePhotosLayout] Preparing to open Face Crop Editor for: {photo_path}")

            from ui.face_crop_editor import FaceCropEditor
            logger.info(f"[GooglePhotosLayout] ✓ FaceCropEditor imported")

            if not os.path.exists(photo_path):
                logger.warning(f"[GooglePhotosLayout] Photo not found: {photo_path}")
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "Photo Not Found",
                    f"Photo not found:\n{photo_path}"
                )
                return

            logger.info(f"[GooglePhotosLayout] Creating FaceCropEditor instance...")
            logger.info(f"[GooglePhotosLayout]   - photo_path: {photo_path}")
            logger.info(f"[GooglePhotosLayout]   - project_id: {self.project_id}")
            logger.info(f"[GooglePhotosLayout]   - parent: {self.main_window if hasattr(self, 'main_window') else None}")

            editor = FaceCropEditor(
                photo_path=photo_path,
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            logger.info(f"[GooglePhotosLayout] ✓ FaceCropEditor instance created successfully")

            # CRITICAL FIX: Don't use signal connection - check flag after dialog closes
            # Signal connection causes "Signal source has been deleted" error because
            # dialog is deleted before QTimer fires in the old implementation
            # editor.faceCropsUpdated.connect(self._refresh_people_sidebar)  # REMOVED

            logger.info(f"[GooglePhotosLayout] Showing Face Crop Editor dialog...")

            # CRITICAL FIX 2: Capture flag value BEFORE dialog closes
            # Accessing editor.faces_were_saved after exec() can cause Qt object deletion crashes
            # Store the flag in a local variable before the dialog is destroyed
            result = editor.exec()

            # IMMEDIATELY capture the flag before Qt deletes the dialog object
            faces_were_saved = False
            try:
                faces_were_saved = getattr(editor, 'faces_were_saved', False)
                logger.info(f"[GooglePhotosLayout] Face Crop Editor closed (result={result}, faces_saved={faces_were_saved})")
            except RuntimeError as e:
                # Dialog object already deleted - this is the crash we're trying to avoid!
                logger.warning(f"[GooglePhotosLayout] Could not access editor.faces_were_saved: {e}")
                logger.warning(f"[GooglePhotosLayout] Dialog was deleted too quickly - assuming no faces saved")

            # Now use the LOCALLY STORED flag to decide if we should refresh
            # This happens AFTER editor object is safely deleted
            if faces_were_saved:
                logger.info(f"[GooglePhotosLayout] Manual faces were saved, scheduling People section refresh...")

                # CRITICAL FIX 3: Use QTimer to delay refresh until after dialog is fully destroyed
                # This prevents "Signal source deleted" crashes
                QTimer.singleShot(100, self._refresh_people_sidebar_after_face_save)
                logger.info(f"[GooglePhotosLayout] ✓ People section refresh scheduled (delayed 100ms)")

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to open Face Crop Editor: {e}", exc_info=True)
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error",
                f"Failed to open Face Crop Editor:\n{e}"
            )

    def _refresh_people_sidebar_after_face_save(self):
        """
        Delayed refresh of People section after Face Crop Editor closes.

        CRITICAL: This method is called via QTimer.singleShot() to ensure
        the Face Crop Editor dialog is fully destroyed before we refresh.
        This prevents "Signal source has been deleted" Qt crashes.
        """
        try:
            logger.info("[GooglePhotosLayout] Executing delayed People section refresh...")
            if hasattr(self, "accordion_sidebar"):
                self.accordion_sidebar.reload_people_section()
                logger.info("[GooglePhotosLayout] ✓ People section refreshed successfully after manual face save")
            else:
                logger.warning("[GooglePhotosLayout] No accordion_sidebar found - cannot refresh People section")
        except RuntimeError as e:
            # Qt object might still be deleted - log but don't crash
            logger.error(f"[GooglePhotosLayout] Qt object deleted during People refresh: {e}", exc_info=True)
            logger.error(f"[GooglePhotosLayout] This indicates a Qt lifecycle bug - please report")
        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to refresh People section: {e}", exc_info=True)

    def _on_accordion_device_selected(self, device_root: str):
        """Open the selected device in the system file browser for quick import."""
        try:
            if not device_root:
                return

            opened = False

            # Windows MTP/namespace paths ("::{GUID}\\?\\usb#...") cannot be opened via
            # QDesktopServices. Try native shell handling first when we detect them.
            is_windows_namespace = os.name == "nt" and device_root.strip().startswith("::")

            if is_windows_namespace:
                opened = self._open_windows_device(device_root)
            else:
                # Regular file-system paths can use Qt's URL handling.
                url = QUrl.fromUserInput(device_root)
                if url.isValid():
                    opened = QDesktopServices.openUrl(url)

                if not opened and os.name == "nt":
                    opened = self._open_windows_device(device_root)

            if not opened:
                raise RuntimeError(f"Could not open device path: {device_root}")
        except Exception as e:
            QMessageBox.information(
                self.main_window if hasattr(self, "main_window") else None,
                "Devices",
                f"Unable to open device location:\n{device_root}\n\n{e}",
            )

    def _open_windows_device(self, device_root: str) -> bool:
        """Best-effort attempts to open a Windows device/namespace path."""
        try:
            try:
                os.startfile(device_root)  # type: ignore[attr-defined]
                return True
            except Exception:
                pass

            try:
                subprocess.Popen(["explorer", device_root])
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _open_people_tools(self):
        """Open the post-face-detection tools reference for quick access."""
        from PySide6.QtWidgets import QMessageBox

        workflow_path = os.path.abspath("POST_FACE_DETECTION_WORKFLOW.md")

        if not os.path.exists(workflow_path):
            QMessageBox.information(
                self.main_window if hasattr(self, "main_window") else None,
                "People Tools",
                "Workflow guide not found. Please make sure POST_FACE_DETECTION_WORKFLOW.md exists.",
            )
            return

        try:
            url = QUrl.fromLocalFile(workflow_path)
            if not QDesktopServices.openUrl(url):
                raise RuntimeError("Failed to open People Tools guide")
        except Exception:
            # Fallback: show a simple helper message with the path
            QMessageBox.information(
                self.main_window if hasattr(self, "main_window") else None,
                "People Tools",
                f"Open the post-face-detection toolkit at:\n{workflow_path}",
            )

    def _on_accordion_person_deleted(self, branch_key: str):
        """Clear any active person filter when that person is removed."""
        if getattr(self, "current_filter_person", None) != branch_key:
            return

        logger.info(
            "[GooglePhotosLayout] Active person '%s' deleted; clearing filter", branch_key
        )

        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=self.current_filter_year,
            filter_month=self.current_filter_month,
            filter_day=self.current_filter_day,
            filter_folder=self.current_filter_folder,
            filter_person=None,
        )

    def _on_accordion_video_clicked(self, filter_spec: str):
        """
        Handle accordion sidebar video selection.

        Args:
            filter_spec: Video filter specification (e.g., "all", "duration:short", "resolution:hd", "codec:h264", "size:small")
        """
        print(f"[GooglePhotosLayout] Accordion video clicked: {filter_spec}")

        # For now, just show all videos by clearing filters
        # Future enhancement: implement duration/resolution filtering
        # Videos are mixed with photos, so filter by video extensions
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Get all videos from database
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                    FROM video_metadata vm
                    JOIN project_videos pv ON vm.path = pv.video_path
                    WHERE pv.project_id = ?
                    ORDER BY vm.created_date DESC
                """, (self.project_id,))
                video_rows = cur.fetchall()

            # Apply duration filter if specified
            if ":" in filter_spec:
                filter_type, filter_value = filter_spec.split(":", 1)

                if filter_type == "duration":
                    # Filter by duration: short, medium, long
                    with db._connect() as conn:
                        cur = conn.cursor()
                        if filter_value == "short":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.duration_seconds > 0 AND vm.duration_seconds < 30
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "medium":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.duration_seconds >= 30 AND vm.duration_seconds < 300
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "long":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.duration_seconds >= 300
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        video_rows = cur.fetchall()

                elif filter_type == "resolution":
                    # Filter by resolution: sd, hd, fhd, 4k
                    # Use MAX(width, height) to match sidebar bucketing logic (handles portrait/landscape videos)
                    with db._connect() as conn:
                        cur = conn.cursor()
                        if filter_value == "sd":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ?
                                  AND COALESCE(vm.width, 0) > 0
                                  AND COALESCE(vm.height, 0) > 0
                                  AND MAX(COALESCE(vm.width, 0), COALESCE(vm.height, 0)) < 720
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "hd":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ?
                                  AND MAX(COALESCE(vm.width, 0), COALESCE(vm.height, 0)) >= 720
                                  AND MAX(COALESCE(vm.width, 0), COALESCE(vm.height, 0)) < 1080
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "fhd":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ?
                                  AND MAX(COALESCE(vm.width, 0), COALESCE(vm.height, 0)) >= 1080
                                  AND MAX(COALESCE(vm.width, 0), COALESCE(vm.height, 0)) < 2160
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "4k":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ?
                                  AND MAX(COALESCE(vm.width, 0), COALESCE(vm.height, 0)) >= 2160
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        video_rows = cur.fetchall()
                        # Debug: Log resolution filter results with dimensions
                        if video_rows:
                            for row in video_rows[:5]:  # Log first 5 for debugging
                                path, _, w, h = row
                                max_dim = max(w or 0, h or 0)
                                import os
                                fname = os.path.basename(path)
                                print(f"[GooglePhotosLayout] 📐 {filter_value.upper()}: {fname} ({w}x{h}, max={max_dim})")
                            if len(video_rows) > 5:
                                print(f"[GooglePhotosLayout] 📐 ... and {len(video_rows) - 5} more {filter_value.upper()} videos")

                elif filter_type == "codec":
                    # NEW: Filter by codec: h264, hevc, vp9, av1, mpeg4
                    with db._connect() as conn:
                        cur = conn.cursor()
                        if filter_value == "h264":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.codec IS NOT NULL AND LOWER(vm.codec) IN ('h264', 'avc')
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "hevc":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.codec IS NOT NULL AND LOWER(vm.codec) IN ('hevc', 'h265')
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "vp9":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.codec IS NOT NULL AND LOWER(vm.codec) = 'vp9'
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "av1":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.codec IS NOT NULL AND LOWER(vm.codec) = 'av1'
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "mpeg4":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.codec IS NOT NULL AND LOWER(vm.codec) IN ('mpeg4', 'xvid', 'divx')
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        video_rows = cur.fetchall()

                elif filter_type == "size":
                    # NEW: Filter by file size: small, medium, large, xlarge
                    with db._connect() as conn:
                        cur = conn.cursor()
                        if filter_value == "small":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.size_kb IS NOT NULL AND vm.size_kb / 1024 < 100
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "medium":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.size_kb IS NOT NULL AND vm.size_kb / 1024 >= 100 AND vm.size_kb / 1024 < 1024
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "large":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.size_kb IS NOT NULL AND vm.size_kb / 1024 >= 1024 AND vm.size_kb / 1024 < 5120
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        elif filter_value == "xlarge":
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND vm.size_kb IS NOT NULL AND vm.size_kb / 1024 >= 5120
                                ORDER BY vm.created_date DESC
                            """, (self.project_id,))
                        video_rows = cur.fetchall()

                elif filter_type == "date":
                    # Filter by year: extract year from created_date
                    with db._connect() as conn:
                        cur = conn.cursor()
                        try:
                            year = int(filter_value)
                            # Match videos where created_date starts with the year
                            # created_date format is typically YYYY-MM-DD HH:MM:SS or YYYY-MM-DD
                            cur.execute("""
                                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                                FROM video_metadata vm
                                JOIN project_videos pv ON vm.path = pv.video_path
                                WHERE pv.project_id = ? AND (
                                    CAST(SUBSTR(vm.created_date, 1, 4) AS INTEGER) = ?
                                    OR CAST(vm.created_year AS INTEGER) = ?
                                )
                                ORDER BY vm.created_date DESC
                            """, (self.project_id, year, year))
                            video_rows = cur.fetchall()
                        except ValueError:
                            # Invalid year value, show all videos
                            print(f"[GooglePhotosLayout] ⚠️ Invalid year filter: {filter_value}")

            # Rebuild timeline with video results
            self._rebuild_timeline_with_results(video_rows, f"Videos: {filter_spec}")

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error filtering videos: {e}")
            import traceback
            traceback.print_exc()

    def _on_accordion_section_expanding(self, section_id: str):
        """
        Handle accordion section expansion - hide search suggestions popup.

        This prevents the popup from briefly appearing during layout changes
        when accordion sections expand/collapse.

        Args:
            section_id: The section being expanded (e.g., "people", "dates", "folders")
        """
        # NUCLEAR FIX: Block popup from showing during layout changes
        self._popup_blocked = True

        # Hide search suggestions popup if visible
        if hasattr(self, 'search_suggestions') and self.search_suggestions.isVisible():
            self.search_suggestions.hide()

        # Unblock popup after layout changes complete (300ms delay)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: setattr(self, '_popup_blocked', False))

    def _filter_by_tag(self, tag_name: str):
        """Filter timeline to show photos by the given tag."""
        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            paths = tag_service.get_paths_by_tag(tag_name, self.project_id)
            if not paths:
                self._rebuild_timeline_with_results([], f"Tag: {tag_name}")
                return
            
            # Build rows with date information
            rows = []
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()
                for p in paths:
                    cur.execute(
                        """
                        SELECT path, COALESCE(date_taken, created_date) AS date_taken, width, height
                        FROM photo_metadata
                        WHERE path = ? AND project_id = ?
                        """,
                        (p, self.project_id)
                    )
                    r = cur.fetchone()
                    if r:
                        rows.append((r[0], r[1], r[2], r[3]))
            
            self._rebuild_timeline_with_results(rows, f"Tag: {tag_name}")
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error filtering by tag '{tag_name}': {e}")

    def _load_face_thumbnail(self, rep_path: str, rep_thumb_png: bytes) -> QIcon:
        """
        Load face thumbnail from rep_path or rep_thumb_png BLOB with circular masking.

        Args:
            rep_path: Path to representative face crop image
            rep_thumb_png: PNG thumbnail as BLOB data

        Returns:
            QIcon with circular face thumbnail, or None if unavailable
        """
        try:
            from PIL import Image
            import io

            FACE_ICON_SIZE = 64  # Increased from 32px for better visibility

            # Try loading from BLOB first (faster, already in DB)
            if rep_thumb_png:
                try:
                    # Load from BLOB
                    image_data = io.BytesIO(rep_thumb_png)
                    with Image.open(image_data) as img:
                        # Convert to QPixmap
                        img_rgb = img.convert('RGB')
                        data = img_rgb.tobytes('raw', 'RGB')
                        qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
                        pixmap = QPixmap.fromImage(qimg)

                        # ENHANCEMENT: Create circular icon (Google Photos / iPhone style)
                        return self._make_circular_face_icon(pixmap, FACE_ICON_SIZE)
                except Exception as blob_error:
                    print(f"[GooglePhotosLayout] Failed to load thumbnail from BLOB: {blob_error}")

            # Fallback: Try loading from file path
            if rep_path and os.path.exists(rep_path):
                try:
                    with Image.open(rep_path) as img:
                        # Convert to QPixmap
                        img_rgb = img.convert('RGB')
                        data = img_rgb.tobytes('raw', 'RGB')
                        qimg = QImage(data, img.width, img.height, QImage.Format_RGB888)
                        pixmap = QPixmap.fromImage(qimg)

                        # ENHANCEMENT: Create circular icon (Google Photos / iPhone style)
                        return self._make_circular_face_icon(pixmap, FACE_ICON_SIZE)
                except Exception as file_error:
                    print(f"[GooglePhotosLayout] Failed to load thumbnail from {rep_path}: {file_error}")

            return None

        except Exception as e:
            print(f"[GooglePhotosLayout] Error loading face thumbnail: {e}")
            return None

    def _on_people_item_clicked(self, item: QTreeWidgetItem, column: int):
        """
        Handle people tree item click - filter by person/face cluster.

        Args:
            item: Clicked tree item
            column: Column index (always 0)
        """
        data = role_get_json(item, role=Qt.UserRole)
        if not data:
            return

        branch_key = data.get("branch_key")
        if branch_key:
            label = data.get("label") or "Unnamed Person"
            print(f"[GooglePhotosLayout] Filtering by person: {label} (branch_key={branch_key})")
            self._load_photos(
                thumb_size=self.current_thumb_size,
                filter_year=None,
                filter_month=None,
                filter_folder=None,
                filter_person=branch_key
            )

    def _on_person_clicked_from_grid(self, person_name: str):
        """
        Handle person card click from grid view - filter by person.

        Args:
            person_name: Name of person clicked (branch_key format: "cluster_X" or name)
        """
        print(f"[GooglePhotosLayout] Filtering by person from grid: {person_name}")
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=None,
            filter_month=None,
            filter_folder=None,
            filter_person=person_name  # person_name is the branch_key
        )

    def _prompt_quick_name_dialog(self):
        """Show a quick naming dialog for unnamed face clusters (top 12)."""
        try:
            from PySide6.QtWidgets import (
                QDialog, QVBoxLayout, QLabel, QGridLayout, QPushButton, QLineEdit, QWidget, QScrollArea, QMessageBox
            )
            from PySide6.QtGui import QPixmap
            import base64, os
            from reference_db import ReferenceDB

            # Fetch unnamed clusters
            db = ReferenceDB()
            rows = []
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT branch_key, label, count, rep_path, rep_thumb_png
                    FROM face_branch_reps
                    WHERE project_id = ? AND (label IS NULL OR TRIM(label) = '')
                    ORDER BY count DESC
                    LIMIT 12
                    """,
                    (self.project_id,)
                )
                rows = cur.fetchall() or []

            if not rows:
                return  # Nothing to name

            dlg = QDialog(self.main_window)
            dlg.setWindowTitle("Review & Name People")
            outer = QVBoxLayout(dlg)
            outer.setContentsMargins(16, 16, 16, 16)
            outer.setSpacing(12)

            header = QLabel("Face detection complete – name these people")
            header.setStyleSheet("color: white; font-size: 12pt;")
            outer.addWidget(header)

            # Scrollable grid of cards
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setSpacing(12)

            editors = {}

            for i, row in enumerate(rows):
                branch_key, label, count, rep_path, rep_thumb = row
                card = QWidget()
                v = QVBoxLayout(card)
                v.setContentsMargins(8, 8, 8, 8)
                v.setSpacing(6)

                # Face preview
                face = QLabel()
                face.setFixedSize(200, 200)
                pix = None
                try:
                    if rep_thumb:
                        data = base64.b64decode(rep_thumb) if isinstance(rep_thumb, str) else rep_thumb
                        pix = QPixmap()
                        pix.loadFromData(data)
                    if (pix is None or pix.isNull()) and rep_path and os.path.exists(rep_path):
                        pix = QPixmap(rep_path)
                    if pix and not pix.isNull():
                        face.setPixmap(pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                except Exception:
                    pass
                v.addWidget(face)

                # Name editor
                name_edit = QLineEdit()
                name_edit.setPlaceholderText(f"Unnamed ({count} photos)")
                editors[branch_key] = name_edit
                v.addWidget(name_edit)

                # Place in grid
                row_i = i // 4
                col_i = i % 4
                grid.addWidget(card, row_i, col_i)

            scroll.setWidget(container)
            outer.addWidget(scroll, 1)

            # Actions
            actions = QWidget()
            ha = QHBoxLayout(actions)
            ha.setContentsMargins(0, 0, 0, 0)
            ha.addStretch()
            btn_skip = QPushButton("Review Later")
            btn_apply = QPushButton("Name Selected")
            ha.addWidget(btn_skip)
            ha.addWidget(btn_apply)
            outer.addWidget(actions)

            def apply_names():
                try:
                    updates = [(bk, editors[bk].text().strip()) for bk in editors]
                    updates = [(bk, nm) for bk, nm in updates if nm]
                    if not updates:
                        dlg.accept()
                        return
                    with db._connect() as conn:
                        cur = conn.cursor()
                        for bk, nm in updates:
                            cur.execute(
                                "UPDATE face_branch_reps SET label = ? WHERE project_id = ? AND branch_key = ?",
                                (nm, self.project_id, bk)
                            )
                            cur.execute(
                                "UPDATE branches SET display_name = ? WHERE project_id = ? AND branch_key = ?",
                                (nm, self.project_id, bk)
                            )
                        conn.commit()
                    # Refresh people UI
                    if hasattr(self, '_build_people_tree'):
                        self._build_people_tree()
                    # Refresh accordion sidebar people section
                    if hasattr(self, 'accordion_sidebar'):
                        self.accordion_sidebar.reload_section("people")
                    QMessageBox.information(dlg, "Saved", f"Named {len(updates)} people.")
                    dlg.accept()
                except Exception as e:
                    QMessageBox.critical(dlg, "Error", f"Failed to save names: {e}")

            btn_apply.clicked.connect(apply_names)
            btn_skip.clicked.connect(dlg.reject)

            dlg.exec()
        except Exception as e:
            print(f"[GooglePhotosLayout] Quick name dialog failed: {e}")

    def _prompt_bulk_face_review(self):
        """Bulk review grid for all unnamed clusters with simple filters."""
        try:
            from PySide6.QtWidgets import (
                QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QPushButton, QComboBox, QLineEdit, QWidget, QScrollArea, QMessageBox, QCompleter
            )
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import Qt
            import base64, os
            from reference_db import ReferenceDB

            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT branch_key, label, count, rep_path, rep_thumb_png
                    FROM face_branch_reps
                    WHERE project_id = ? AND (label IS NULL OR TRIM(label) = '')
                    ORDER BY count DESC
                    """,
                    (self.project_id,)
                )
                rows = cur.fetchall() or []

                # Get existing person names for autocomplete
                cur.execute(
                    """
                    SELECT DISTINCT label
                    FROM face_branch_reps
                    WHERE project_id = ? AND label IS NOT NULL AND TRIM(label) != ''
                    ORDER BY label
                    """,
                    (self.project_id,)
                )
                existing_names = [row[0] for row in cur.fetchall()]

            if not rows:
                QMessageBox.information(self.main_window, "No Unnamed People", "All people are already named.")
                return

            dlg = QDialog(self.main_window)
            dlg.setWindowTitle("Bulk Review: Name Unnamed People")
            outer = QVBoxLayout(dlg)
            outer.setContentsMargins(16, 16, 16, 16)
            outer.setSpacing(10)

            # Header + filter
            header_row = QHBoxLayout()
            header = QLabel("Review all unnamed people")
            header.setStyleSheet("color: white; font-size: 12pt;")
            header_row.addWidget(header)
            header_row.addStretch()
            filter_combo = QComboBox()
            filter_combo.addItems(["All", "Large groups (≥5)", "Uncertain (<5)"])
            header_row.addWidget(filter_combo)
            outer.addLayout(header_row)

            # Search box
            search_box = QLineEdit()
            search_box.setPlaceholderText("Filter by suggested name…")
            outer.addWidget(search_box)

            # Scrollable grid
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setSpacing(12)

            editors = {}
            cards = []

            def populate():
                # Clear existing
                while grid.count():
                    it = grid.takeAt(0)
                    if it and it.widget():
                        it.widget().deleteLater()
                cards.clear()

                # Filter function
                selected = filter_combo.currentIndex()
                text = search_box.text().strip().lower()
                def passes(count):
                    return (
                        selected == 0 or
                        (selected == 1 and count >= 5) or
                        (selected == 2 and count < 5)
                    )

                # Populate
                i = 0
                for row in rows:
                    branch_key, label, count, rep_path, rep_thumb = row
                    if not passes(count):
                        continue
                    # Build card
                    card = QWidget()
                    v = QVBoxLayout(card)
                    v.setContentsMargins(8, 8, 8, 8)
                    v.setSpacing(6)

                    # Face preview - show top 4 faces by quality
                    faces_layout = QHBoxLayout()
                    faces_layout.setSpacing(4)
                    faces_layout.setContentsMargins(0, 0, 0, 0)

                    # Query top 4 faces by quality score for this cluster
                    try:
                        db_faces = ReferenceDB()
                        with db_faces._connect() as conn_faces:
                            cur_faces = conn_faces.cursor()
                            cur_faces.execute("""
                                SELECT crop_path
                                FROM face_crops
                                WHERE project_id = ? AND branch_key = ?
                                ORDER BY quality_score DESC, id DESC
                                LIMIT 4
                            """, (self.project_id, branch_key))
                            face_paths = [r[0] for r in cur_faces.fetchall()]

                        # If no quality scores yet, fall back to representative
                        if not face_paths and rep_path:
                            face_paths = [rep_path]

                        # Create thumbnails (48x48 each, or 200x200 if only 1)
                        if len(face_paths) == 1:
                            # Single large thumbnail (original behavior)
                            face_label = QLabel()
                            face_label.setFixedSize(200, 200)
                            if os.path.exists(face_paths[0]):
                                pix = QPixmap(face_paths[0])
                                if not pix.isNull():
                                    face_label.setPixmap(pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                            faces_layout.addWidget(face_label)
                        else:
                            # Multiple small thumbnails (48x48 each)
                            for face_path in face_paths[:4]:
                                face_thumb = QLabel()
                                face_thumb.setFixedSize(48, 48)
                                face_thumb.setStyleSheet("""
                                    QLabel {
                                        border: 1px solid #dadce0;
                                        border-radius: 4px;
                                        background: #f8f9fa;
                                    }
                                """)
                                if os.path.exists(face_path):
                                    pix = QPixmap(face_path)
                                    if not pix.isNull():
                                        face_thumb.setPixmap(pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                                faces_layout.addWidget(face_thumb)

                            # Add stretch to left-align thumbnails
                            faces_layout.addStretch()

                    except Exception as e:
                        # Fallback to original single preview on error
                        face = QLabel()
                        face.setFixedSize(200, 200)
                        pix = None
                        try:
                            if rep_thumb:
                                data = base64.b64decode(rep_thumb) if isinstance(rep_thumb, str) else rep_thumb
                                pix = QPixmap()
                                pix.loadFromData(data)
                            if (pix is None or pix.isNull()) and rep_path and os.path.exists(rep_path):
                                pix = QPixmap(rep_path)
                            if pix and not pix.isNull():
                                face.setPixmap(pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        except Exception:
                            pass
                        faces_layout.addWidget(face)

                    v.addLayout(faces_layout)

                    # Confidence hint based on compactness (mean similarity to centroid)
                    try:
                        import numpy as np
                        from reference_db import ReferenceDB
                        db_local = ReferenceDB()
                        with db_local._connect() as conn2:
                            cur2 = conn2.cursor()
                            cur2.execute("SELECT centroid FROM face_branch_reps WHERE project_id = ? AND branch_key = ?", (self.project_id, branch_key))
                            r = cur2.fetchone()
                            centroid_vec = np.frombuffer(r[0], dtype=np.float32) if r and r[0] else None
                            mean_sim = 0.0
                            if centroid_vec is not None:
                                cur2.execute("SELECT embedding FROM face_crops WHERE project_id = ? AND branch_key = ? AND embedding IS NOT NULL LIMIT 30", (self.project_id, branch_key))
                                embs = [np.frombuffer(e[0], dtype=np.float32) for e in cur2.fetchall() if e and e[0]]
                                sims = []
                                for vec in embs:
                                    denom = (np.linalg.norm(centroid_vec) * np.linalg.norm(vec))
                                    if denom > 0:
                                        sims.append(float(np.dot(centroid_vec, vec) / denom))
                                if sims:
                                    mean_sim = float(np.mean(sims))
                            # Badge by compactness
                            conf = "✅" if mean_sim >= 0.85 else ("⚠️" if mean_sim >= 0.70 else "❓")
                            hint = QLabel(f"{conf} compactness: {int(mean_sim*100)}% • {count} photos")
                    except Exception:
                        conf = "✅" if count >= 10 else ("⚠️" if count >= 5 else "❓")
                        hint = QLabel(f"{conf} {count} photos")
                    hint.setStyleSheet("color: #5f6368;")
                    v.addWidget(hint)

                    # Name editor with autocomplete
                    name_edit = QLineEdit()
                    name_edit.setPlaceholderText(f"Unnamed ({count} photos)")

                    # Add autocomplete for existing names
                    if existing_names:
                        completer = QCompleter(existing_names)
                        completer.setCaseSensitivity(Qt.CaseInsensitive)
                        completer.setFilterMode(Qt.MatchContains)
                        completer.setCompletionMode(QCompleter.PopupCompletion)
                        name_edit.setCompleter(completer)

                    editors[branch_key] = name_edit
                    v.addWidget(name_edit)

                    # Choose Face button
                    choose_face_btn = QPushButton("Choose Face ▼")
                    choose_face_btn.setStyleSheet("""
                        QPushButton {
                            background: #f1f3f4;
                            border: 1px solid #dadce0;
                            border-radius: 4px;
                            padding: 4px 8px;
                            font-size: 9pt;
                        }
                        QPushButton:hover {
                            background: #e8eaed;
                        }
                    """)
                    # Use closure to capture branch_key and rep_path
                    def make_choose_face_handler(bk, rp, lbl):
                        def handler():
                            from ui.cluster_face_selector import ClusterFaceSelector
                            selector = ClusterFaceSelector(
                                project_id=self.project_id,
                                branch_key=bk,
                                cluster_name=lbl if lbl else f"Unnamed ({count} photos)",
                                current_rep_path=rp,
                                parent=dlg
                            )
                            if selector.exec():
                                # Refresh the grid to show updated representative
                                populate()
                                # Refresh accordion sidebar to show updated representative thumbnail
                                self._refresh_people_sidebar()
                        return handler

                    choose_face_btn.clicked.connect(make_choose_face_handler(branch_key, rep_path, label))
                    v.addWidget(choose_face_btn)

                    row_i = i // 4
                    col_i = i % 4
                    grid.addWidget(card, row_i, col_i)
                    cards.append(card)
                    i += 1

            populate()

            def apply_names():
                try:
                    updates = [(bk, editors[bk].text().strip()) for bk in editors]
                    updates = [(bk, nm) for bk, nm in updates if nm]
                    if not updates:
                        dlg.accept()
                        return
                    db = ReferenceDB()
                    with db._connect() as conn:
                        cur = conn.cursor()
                        for bk, nm in updates:
                            cur.execute("UPDATE face_branch_reps SET label = ? WHERE project_id = ? AND branch_key = ?", (nm, self.project_id, bk))
                            cur.execute("UPDATE branches SET display_name = ? WHERE project_id = ? AND branch_key = ?", (nm, self.project_id, bk))
                        conn.commit()
                    if hasattr(self, '_build_people_tree'):
                        self._build_people_tree()

                    # Phase 4: Suggest similar unnamed clusters for merge
                    merge_suggestions = self._suggest_cluster_merges(updates)
                    if merge_suggestions:
                        self._show_merge_suggestions_dialog(merge_suggestions, dlg)

                    QMessageBox.information(dlg, "Saved", f"Named {len(updates)} people.")
                    dlg.accept()
                except Exception as e:
                    QMessageBox.critical(dlg, "Error", f"Failed to save names: {e}")

            scroll.setWidget(container)
            outer.addWidget(scroll, 1)

            # Actions
            actions = QWidget()
            ha = QHBoxLayout(actions)
            ha.setContentsMargins(0, 0, 0, 0)
            ha.addStretch()
            btn_close = QPushButton("Close")
            btn_apply = QPushButton("Name Selected")
            ha.addWidget(btn_close)
            ha.addWidget(btn_apply)
            outer.addWidget(actions)

            btn_apply.clicked.connect(apply_names)
            btn_close.clicked.connect(dlg.reject)
            filter_combo.currentIndexChanged.connect(lambda _: populate())
            search_box.textChanged.connect(lambda _: populate())

            dlg.exec()
        except Exception as e:
            print(f"[GooglePhotosLayout] Bulk review dialog failed: {e}")

    def _suggest_cluster_merges(self, named_clusters, similarity_threshold=0.75):
        """
        Suggest unnamed clusters similar to newly named ones for potential merging.

        Args:
            named_clusters: List of (branch_key, person_name) tuples
            similarity_threshold: Minimum cosine similarity (0.0-1.0)

        Returns:
            Dict mapping person_name to list of similar unnamed clusters
        """
        try:
            import numpy as np
            from reference_db import ReferenceDB

            suggestions = {}
            db = ReferenceDB()

            with db._connect() as conn:
                cur = conn.cursor()

                for branch_key, person_name in named_clusters:
                    # Get centroid for newly named person
                    cur.execute("""
                        SELECT centroid FROM face_branch_reps
                        WHERE project_id = ? AND branch_key = ?
                    """, (self.project_id, branch_key))

                    row = cur.fetchone()
                    if not row or not row[0]:
                        continue

                    named_centroid = np.frombuffer(row[0], dtype=np.float32)

                    # Find similar unnamed clusters
                    cur.execute("""
                        SELECT branch_key, centroid, count, rep_thumb_png
                        FROM face_branch_reps
                        WHERE project_id = ? AND (label IS NULL OR TRIM(label) = '')
                    """, (self.project_id,))

                    similar_clusters = []
                    for urow in cur.fetchall():
                        if not urow[1]:
                            continue

                        cluster_centroid = np.frombuffer(urow[1], dtype=np.float32)

                        # Calculate cosine similarity
                        denom = np.linalg.norm(named_centroid) * np.linalg.norm(cluster_centroid)
                        if denom > 0:
                            similarity = float(np.dot(named_centroid, cluster_centroid) / denom)

                            if similarity >= similarity_threshold:
                                similar_clusters.append({
                                    'branch_key': urow[0],
                                    'similarity': similarity,
                                    'count': urow[2],
                                    'thumb': urow[3]
                                })

                    # Sort by similarity (highest first) and take top 5
                    similar_clusters.sort(key=lambda x: x['similarity'], reverse=True)
                    if similar_clusters[:5]:
                        suggestions[person_name] = similar_clusters[:5]

            return suggestions

        except Exception as e:
            print(f"[GooglePhotosLayout] Merge suggestion calculation failed: {e}")
            return {}

    def _show_merge_suggestions_dialog(self, suggestions, parent=None):
        """
        Show dialog with merge suggestions for review.

        Args:
            suggestions: Dict mapping person_name to list of similar clusters
            parent: Parent widget
        """
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QCheckBox, QScrollArea, QWidget
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import Qt
            import base64

            dlg = QDialog(parent)
            dlg.setWindowTitle("Merge Suggestions")
            dlg.resize(600, 500)

            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(20, 20, 20, 20)

            # Header
            header = QLabel("Similar unnamed clusters found! Would you like to merge them?")
            header.setWordWrap(True)
            header.setStyleSheet("font-size: 12pt; font-weight: bold;")
            layout.addWidget(header)

            # Scroll area for suggestions
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            scroll_layout = QVBoxLayout(container)

            checkboxes = {}  # {(person_name, branch_key): QCheckBox}

            for person_name, clusters in suggestions.items():
                # Section for each person
                section_label = QLabel(f"Merge into \"{person_name}\":")
                section_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
                scroll_layout.addWidget(section_label)

                for cluster in clusters:
                    row_layout = QHBoxLayout()

                    # Thumbnail
                    thumb_label = QLabel()
                    thumb_label.setFixedSize(48, 48)
                    if cluster['thumb']:
                        try:
                            data = base64.b64decode(cluster['thumb']) if isinstance(cluster['thumb'], str) else cluster['thumb']
                            pix = QPixmap()
                            pix.loadFromData(data)
                            if not pix.isNull():
                                thumb_label.setPixmap(pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        except Exception:
                            pass
                    row_layout.addWidget(thumb_label)

                    # Checkbox with similarity info
                    cb = QCheckBox(f"{int(cluster['similarity']*100)}% similar • {cluster['count']} photos")
                    cb.setChecked(True)  # Pre-select high-similarity matches
                    checkboxes[(person_name, cluster['branch_key'])] = cb
                    row_layout.addWidget(cb, 1)

                    scroll_layout.addLayout(row_layout)

            scroll.setWidget(container)
            layout.addWidget(scroll, 1)

            # Buttons
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()

            skip_btn = QPushButton("Skip")
            skip_btn.clicked.connect(dlg.reject)
            btn_layout.addWidget(skip_btn)

            merge_btn = QPushButton("Merge Selected")
            merge_btn.setDefault(True)
            merge_btn.setStyleSheet("""
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

            def do_merge():
                from reference_db import ReferenceDB
                db = ReferenceDB()
                merged_count = 0

                try:
                    with db._connect() as conn:
                        cur = conn.cursor()

                        for (person_name, branch_key), cb in checkboxes.items():
                            if cb.isChecked():
                                cur.execute("""
                                    UPDATE face_branch_reps
                                    SET label = ?
                                    WHERE project_id = ? AND branch_key = ?
                                """, (person_name, self.project_id, branch_key))

                                cur.execute("""
                                    UPDATE branches
                                    SET display_name = ?
                                    WHERE project_id = ? AND branch_key = ?
                                """, (person_name, self.project_id, branch_key))

                                merged_count += 1

                        conn.commit()

                    if hasattr(self, '_build_people_tree'):
                        self._build_people_tree()

                    QMessageBox.information(dlg, "Success", f"Merged {merged_count} clusters.")
                    dlg.accept()

                except Exception as e:
                    QMessageBox.critical(dlg, "Error", f"Merge failed: {e}")

            merge_btn.clicked.connect(do_merge)
            btn_layout.addWidget(merge_btn)

            layout.addLayout(btn_layout)

            dlg.exec()

        except Exception as e:
            print(f"[GooglePhotosLayout] Merge suggestions dialog failed: {e}")
    def _on_person_context_menu(self, branch_key: str, action: str):
        """Handle context menu action on person card."""
        print(f"[GooglePhotosLayout] Context menu action '{action}' for {branch_key}")

        # Get current display name from database
        from reference_db import ReferenceDB
        db = ReferenceDB()

        try:
            query = "SELECT label FROM face_branch_reps WHERE project_id = ? AND branch_key = ?"
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(query, (self.project_id, branch_key))
                row = cur.fetchone()
                current_name = row[0] if row and row[0] else "Unnamed"
        except Exception as e:
            print(f"[GooglePhotosLayout] Error fetching person name: {e}")
            current_name = "Unnamed"

        # Dispatch to appropriate handler
        if action == "rename":
            self._rename_person(None, branch_key, current_name)
        elif action == "merge":
            self._merge_person(branch_key, current_name)
        elif action == "delete":
            self._delete_person(branch_key, current_name)
        elif action == "suggest_merge":
            if hasattr(self, '_prompt_merge_suggestions'):
                self._prompt_merge_suggestions(branch_key)
        elif action == "details":
            if hasattr(self, '_open_person_detail'):
                self._open_person_detail(branch_key)

    def _filter_people_grid(self, text: str):
        """
        Filter people grid by search text.

        Args:
            text: Search query to filter by name
        """
        search_query = text.lower().strip()
        print(f"[GooglePhotosLayout] Filtering people grid: '{search_query}'")

        # Show/hide cards based on search
        if hasattr(self, 'people_grid') and hasattr(self.people_grid, 'flow_layout'):
            visible_count = 0
            for i in range(self.people_grid.flow_layout.count()):
                item = self.people_grid.flow_layout.itemAt(i)
                if item and item.widget():
                    card = item.widget()
                    if isinstance(card, PersonCard):
                        # Check if display name matches search
                        name_matches = search_query in card.display_name.lower()
                        card.setVisible(name_matches or not search_query)
                        if card.isVisible():
                            visible_count += 1

            print(f"[GooglePhotosLayout] Filter results: {visible_count} people visible")

            # Update section count badge to show filtered count
            if hasattr(self, 'people_section'):
                total_count = self.people_grid.flow_layout.count()
                if search_query:
                    self.people_section.update_count(f"{visible_count}/{total_count}")
                else:
                    self.people_section.update_count(total_count)
    
    def _on_people_search_OLD_REMOVED(self, text: str):
        """
        Filter people grid by search text (Phase 3).
        DEPRECATED: Replaced by enhanced version with autocomplete.

        Args:
            text: Search query to filter by name
        """
        search_query = text.lower().strip()
        print(f"[GooglePhotosLayout] Searching people: '{search_query}'")

        # Show/hide cards based on search
        if hasattr(self, 'people_grid') and hasattr(self.people_grid, 'flow_layout'):
            visible_count = 0
            for i in range(self.people_grid.flow_layout.count()):
                item = self.people_grid.flow_layout.itemAt(i)
                if item and item.widget():
                    card = item.widget()
                    if isinstance(card, PersonCard):
                        # Check if display name matches search
                        name_matches = search_query in card.display_name.lower()
                        card.setVisible(name_matches or not search_query)
                        if card.isVisible():
                            visible_count += 1

            print(f"[GooglePhotosLayout] Search results: {visible_count} people visible")

            # Update section count badge to show filtered count
            if hasattr(self, 'people_section'):
                total_count = self.people_grid.flow_layout.count()
                if search_query:
                    self.people_section.update_count(f"{visible_count}/{total_count}")
                else:
                    pass  # Old sidebar section count update - no longer needed with AccordionSidebar

    def _show_people_context_menu(self, pos):
        """
        Show context menu for people tree items (rename/merge/delete).

        Inspired by Google Photos / iPhone Photos face management.
        """
        item = self.people_tree.itemAt(pos)
        if not item:
            return

        data = role_get_json(item, role=Qt.UserRole)
        if not data:
            return
        if data.get("type") != "person":
            return

        branch_key = data.get("branch_key")
        current_label = data.get("label")
        current_name = current_label if current_label else "Unnamed Person"

        menu = QMenu(self.people_tree)

        # Rename action
        rename_action = QAction("✏️ Rename Person...", menu)
        rename_action.triggered.connect(lambda: self._rename_person(item, branch_key, current_name))
        menu.addAction(rename_action)

        # Merge action
        merge_action = QAction("🔗 Merge with Another Person...", menu)
        merge_action.triggered.connect(lambda: self._merge_person(branch_key, current_name))
        menu.addAction(merge_action)

        menu.addSeparator()

        # View all photos (already doing this on click)
        view_action = QAction("📸 View All Photos", menu)
        view_action.triggered.connect(lambda: self._on_people_item_clicked(item, 0))
        menu.addAction(view_action)

        menu.addSeparator()

        # Delete action
        delete_action = QAction("🗑️ Delete This Person", menu)
        delete_action.triggered.connect(lambda: self._delete_person(branch_key, current_name))
        menu.addAction(delete_action)

        menu.exec(self.people_tree.viewport().mapToGlobal(pos))

    def _rename_person(self, item: QTreeWidgetItem, branch_key: str, current_name: str):
        """
        Rename a person/face cluster.

        Works for both grid view (item=None) and tree view (item=QTreeWidgetItem).
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        new_name, ok = QInputDialog.getText(
            self.main_window,
            "Rename Person",
            f"Rename '{current_name}' to:",
            text=current_name if not current_name.startswith("Unnamed") else ""
        )

        if not ok or not new_name.strip():
            return

        new_name = new_name.strip()

        if new_name == current_name:
            return

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Update database
            with db._connect() as conn:
                conn.execute("""
                    UPDATE branches
                    SET display_name = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (new_name, self.project_id, branch_key))

                conn.execute("""
                    UPDATE face_branch_reps
                    SET label = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (new_name, self.project_id, branch_key))

                conn.commit()

            # Update UI based on view type
            if item is not None:
                # Tree view: Update tree item
                old_text = item.text(0)
                count_part = old_text.split('(')[-1] if '(' in old_text else "0)"
                item.setText(0, f"{new_name} ({count_part}")

                # Update data (safe writeback via JSON serialization)
                data = role_get_json(item, role=Qt.UserRole)
                if data:
                    data["label"] = new_name
                    role_set_json(item, data, role=Qt.UserRole)
            else:
                # Grid view: Refresh the entire people grid to show updated name
                self._build_people_tree()

            print(f"[GooglePhotosLayout] Person renamed: {current_name} → {new_name}")
            QMessageBox.information(self.main_window, "Renamed", f"Person renamed to '{new_name}'")

        except Exception as e:
            print(f"[GooglePhotosLayout] Rename failed: {e}")
            QMessageBox.critical(self.main_window, "Rename Failed", f"Error: {e}")

    def _merge_person(self, source_branch_key: str, source_name: str):
        """Merge this person with another person."""
        from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QDialogButtonBox, QVBoxLayout, QLabel, QMessageBox

        # Get all other persons
        from reference_db import ReferenceDB
        db = ReferenceDB()

        with db._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT branch_key, label, count
                FROM face_branch_reps
                WHERE project_id = ? AND branch_key != ?
                ORDER BY count DESC
            """, (self.project_id, source_branch_key))

            other_persons = cur.fetchall()

        if not other_persons:
            QMessageBox.information(self.main_window, "No Persons", "No other persons to merge with")
            return

        # Show selection dialog
        dialog = QDialog(self.main_window)
        dialog.setWindowTitle(f"Merge '{source_name}'")
        dialog.resize(450, 550)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Select person to merge '{source_name}' into:"))

        list_widget = QListWidget()
        for branch_key, label, count in other_persons:
            display = f"{label or 'Unnamed Person'} ({count} photos)"
            item_widget = QListWidgetItem(display)
            item_widget.setData(Qt.UserRole, branch_key)
            list_widget.addItem(item_widget)

        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.Accepted:
            selected_item = list_widget.currentItem()
            if selected_item:
                target_branch_key = selected_item.data(Qt.UserRole)
                self._perform_merge(source_branch_key, target_branch_key, source_name)

    def _perform_merge(self, source_key: str, target_key: str, source_name: str):
        """Perform the actual merge operation."""
        from PySide6.QtWidgets import QMessageBox

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Use the proper merge_face_clusters method that handles counts correctly
            result = db.merge_face_clusters(
                project_id=self.project_id,
                target_branch=target_key,
                source_branches=[source_key],
                log_undo=True
            )

            # Clear redo stack (new merge invalidates redo history)
            if hasattr(self, 'redo_stack'):
                self.redo_stack.clear()

            # Rebuild people tree to show updated counts
            self._build_people_tree()

            # Update undo/redo button states
            self._update_undo_redo_state()

            print(f"[GooglePhotosLayout] Merge successful: {source_name} merged into {target_key}")
            print(f"[GooglePhotosLayout] Merge result: {result}")

            # Build comprehensive merge notification following Google Photos pattern
            msg_lines = [f"✓ '{source_name}' merged successfully", ""]

            duplicates = result.get('duplicates_found', 0)
            unique_moved = result.get('unique_moved', 0)
            total_photos = result.get('total_photos', 0)
            moved_faces = result.get('moved_faces', 0)

            if duplicates > 0:
                msg_lines.append(f"⚠️ Found {duplicates} duplicate photo{'s' if duplicates != 1 else ''}")
                msg_lines.append("   (already in target, not duplicated)")
                msg_lines.append("")

            if unique_moved > 0:
                msg_lines.append(f"• Moved {unique_moved} unique photo{'s' if unique_moved != 1 else ''}")
            elif duplicates > 0:
                msg_lines.append(f"• No unique photos to move (all were duplicates)")

            msg_lines.append(f"• Reassigned {moved_faces} face crop{'s' if moved_faces != 1 else ''}")
            msg_lines.append("")
            msg_lines.append(f"Total: {total_photos} photo{'s' if total_photos != 1 else ''}")

            QMessageBox.information(
                self.main_window,
                "Merged",
                "\n".join(msg_lines)
            )

        except Exception as e:
            print(f"[GooglePhotosLayout] Merge failed: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "Merge Failed", f"Error: {e}")
    
    def _on_drag_merge(self, source_branch: str, target_branch: str):
        """Handle drag-and-drop merge from People grid."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            
            # Get source name for confirmation feedback
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT label FROM face_branch_reps WHERE project_id = ? AND branch_key = ?", (self.project_id, source_branch))
                row = cur.fetchone()
                source_name = row[0] if row and row[0] else source_branch
            
            # Perform merge using existing method
            self._perform_merge(source_branch, target_branch, source_name)
            
        except Exception as e:
            print(f"[GooglePhotosLayout] Drag-drop merge failed: {e}")
            import traceback
            traceback.print_exc()
    
    def _undo_last_merge(self):
        """Undo the last face merge operation."""
        from PySide6.QtWidgets import QMessageBox
        
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            
            # Get the last merge before undoing (for redo stack)
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, target_branch, source_branches, snapshot FROM face_merge_history WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                    (self.project_id,)
                )
                last_merge = cur.fetchone()
            
            # Perform undo
            result = db.undo_last_face_merge(self.project_id)
            
            if result:
                # Add to redo stack
                if last_merge:
                    self.redo_stack.append({
                        'id': last_merge[0],
                        'target': last_merge[1],
                        'sources': last_merge[2],
                        'snapshot': last_merge[3]
                    })
                
                # Rebuild people tree to show restored clusters
                self._build_people_tree()
                
                # Update undo/redo button states
                self._update_undo_redo_state()
                
                QMessageBox.information(
                    self.main_window,
                    "Undo Successful",
                    f"✅ Merge undone successfully\n\n"
                    f"Restored {result['clusters']} person(s)\n"
                    f"Moved {result['faces']} face(s) back"
                )
                print(f"[GooglePhotosLayout] Undo successful: {result}")
            else:
                QMessageBox.information(
                    self.main_window,
                    "No Undo Available",
                    "There are no recent merges to undo."
                )
                
        except Exception as e:
            print(f"[GooglePhotosLayout] Undo failed: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "Undo Failed", f"Error: {e}")
    
    def _redo_last_undo(self):
        """Redo the last undone merge operation."""
        from PySide6.QtWidgets import QMessageBox
        
        if not self.redo_stack:
            QMessageBox.information(
                self.main_window,
                "No Redo Available",
                "There are no undone operations to redo."
            )
            return
        
        try:
            from reference_db import ReferenceDB
            import json
            db = ReferenceDB()
            
            # Pop from redo stack
            redo_op = self.redo_stack.pop()
            snapshot = json.loads(redo_op['snapshot']) if isinstance(redo_op['snapshot'], str) else redo_op['snapshot']
            
            # Re-apply the merge by restoring snapshot state
            # Get source branches from snapshot
            branch_keys = snapshot.get('branch_keys', [])
            target = redo_op['target']
            sources = [k for k in branch_keys if k != target]
            
            if sources:
                # Re-merge using existing method
                result = db.merge_face_clusters(
                    project_id=self.project_id,
                    target_branch=target,
                    source_branches=sources,
                    log_undo=True
                )
                
                # Rebuild people tree
                self._build_people_tree()
                
                # Update button states
                self._update_undo_redo_state()

                # Build comprehensive redo notification
                msg_lines = ["✅ Merge re-applied successfully", ""]

                duplicates = result.get('duplicates_found', 0)
                unique_moved = result.get('unique_moved', 0)
                total_photos = result.get('total_photos', 0)
                moved_faces = result.get('moved_faces', 0)

                if duplicates > 0:
                    msg_lines.append(f"⚠️ Found {duplicates} duplicate photo{'s' if duplicates != 1 else ''}")
                    msg_lines.append("   (already in target, not duplicated)")
                    msg_lines.append("")

                if unique_moved > 0:
                    msg_lines.append(f"• Moved {unique_moved} unique photo{'s' if unique_moved != 1 else ''}")
                elif duplicates > 0:
                    msg_lines.append(f"• No unique photos to move (all were duplicates)")

                msg_lines.append(f"• Reassigned {moved_faces} face crop{'s' if moved_faces != 1 else ''}")
                msg_lines.append("")
                msg_lines.append(f"Total: {total_photos} photo{'s' if total_photos != 1 else ''}")

                QMessageBox.information(
                    self.main_window,
                    "Redo Successful",
                    "\n".join(msg_lines)
                )
                print(f"[GooglePhotosLayout] Redo successful: {result}")
            
        except Exception as e:
            print(f"[GooglePhotosLayout] Redo failed: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "Redo Failed", f"Error: {e}")
    
    def _update_undo_redo_state(self):
        """Update undo/redo button enabled/disabled states."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            
            # Check if there are any undo records
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM face_merge_history WHERE project_id = ?",
                    (self.project_id,)
                )
                undo_count = cur.fetchone()[0]
                
                # Update undo button
                if hasattr(self, 'people_undo_btn'):
                    self.people_undo_btn.setEnabled(undo_count > 0)
                    self.people_undo_btn.setToolTip(
                        f"Undo Last Merge ({undo_count} available)" if undo_count > 0 else "No merges to undo"
                    )
                
                # Update redo button
                if hasattr(self, 'people_redo_btn'):
                    redo_count = len(self.redo_stack)
                    self.people_redo_btn.setEnabled(redo_count > 0)
                    self.people_redo_btn.setToolTip(
                        f"Redo Last Undo ({redo_count} available)" if redo_count > 0 else "No undos to redo"
                    )
                    
        except Exception as e:
            print(f"[GooglePhotosLayout] Failed to update undo/redo buttons: {e}")
    
    def _show_merge_history(self):
        """Show merge history dialog with undo/redo options."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QMessageBox
        
        try:
            from reference_db import ReferenceDB
            import json
            from datetime import datetime
            db = ReferenceDB()
            
            # Fetch merge history
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """SELECT id, target_branch, source_branches, snapshot, created_at 
                       FROM face_merge_history 
                       WHERE project_id = ? 
                       ORDER BY id DESC
                       LIMIT 50""",
                    (self.project_id,)
                )
                history = cur.fetchall()
            
            if not history:
                QMessageBox.information(
                    self.main_window,
                    "No History",
                    "No merge operations have been performed yet."
                )
                return
            
            # Create dialog
            dlg = QDialog(self.main_window)
            dlg.setWindowTitle("📜 Merge History")
            dlg.resize(600, 500)
            layout = QVBoxLayout(dlg)
            
            # Header
            header = QLabel(f"<b>Merge History</b> ({len(history)} operations)")
            header.setStyleSheet("font-size: 12pt; padding: 8px;")
            layout.addWidget(header)
            
            # History list
            history_list = QListWidget()
            history_list.setStyleSheet("""
                QListWidget::item {
                    padding: 12px;
                    border-bottom: 1px solid #e8eaed;
                }
                QListWidget::item:hover {
                    background: #f8f9fa;
                }
            """)
            
            for merge_id, target, sources, snapshot_str, created_at in history:
                # Parse snapshot to get names
                try:
                    snapshot = json.loads(snapshot_str)
                    branch_keys = snapshot.get('branch_keys', [])
                    
                    # Get person names
                    with db._connect() as conn2:
                        cur2 = conn2.cursor()
                        cur2.execute(
                            f"SELECT branch_key, label FROM face_branch_reps WHERE project_id = ? AND branch_key IN ({','.join(['?']*len(branch_keys))})",
                            [self.project_id] + branch_keys
                        )
                        names = {row[0]: row[1] or row[0] for row in cur2.fetchall()}
                    
                    target_name = names.get(target, target)
                    source_names = [names.get(s, s) for s in sources.split(',')]
                    
                    # Format timestamp
                    try:
                        dt = datetime.fromisoformat(created_at)
                        time_str = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        time_str = created_at
                    
                    item_text = f"⏰ {time_str}\n🔗 Merged {', '.join(source_names)} → {target_name}"
                    
                except Exception as e:
                    print(f"Failed to parse merge history item: {e}")
                    item_text = f"🔗 Merge #{merge_id} ({created_at})"
                
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, merge_id)
                history_list.addItem(item)
            
            layout.addWidget(history_list)
            
            # Actions
            actions = QHBoxLayout()
            
            def undo_selected():
                selected = history_list.currentItem()
                if selected:
                    merge_id = selected.data(Qt.UserRole)
                    # Undo all operations up to and including this one
                    reply = QMessageBox.question(
                        dlg,
                        "Confirm Undo",
                        "Undo this merge and all subsequent merges?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.Yes:
                        # Perform undo
                        self._undo_last_merge()
                        dlg.accept()
                        self._show_merge_history()  # Refresh history
            
            undo_btn = QPushButton("↺ Undo Selected")
            undo_btn.clicked.connect(undo_selected)
            actions.addWidget(undo_btn)
            
            actions.addStretch()
            
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(dlg.accept)
            actions.addWidget(close_btn)
            
            layout.addLayout(actions)
            
            dlg.exec()
            
        except Exception as e:
            print(f"[GooglePhotosLayout] Failed to show merge history: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "Error", f"Failed to load history: {e}")

    def _prompt_merge_suggestions(self, target_branch_key: str):
        """Suggest similar people to merge into the target using centroid cosine similarity."""
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QWidget, QGridLayout, QCheckBox, QPushButton, QHBoxLayout
            from PySide6.QtGui import QPixmap
            import numpy as np, os, base64
            from reference_db import ReferenceDB
            db = ReferenceDB()
            # Fetch target centroid and face preview
            target = None
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT label, centroid, rep_path, rep_thumb_png FROM face_branch_reps WHERE project_id = ? AND branch_key = ?", (self.project_id, target_branch_key))
                row = cur.fetchone()
                if not row or not row[1]:
                    return
                target_name = row[0] or target_branch_key
                target = np.frombuffer(row[1], dtype=np.float32)
                target_rep_path = row[2]
                target_rep_thumb = row[3]
                # Fetch others with face previews
                cur.execute("SELECT branch_key, label, count, centroid, rep_path, rep_thumb_png FROM face_branch_reps WHERE project_id = ? AND branch_key != ?", (self.project_id, target_branch_key))
                others = cur.fetchall() or []
            # Compute similarities
            suggestions = []
            for bk, label, cnt, centroid, rep_path, rep_thumb in others:
                if not centroid:
                    continue
                vec = np.frombuffer(centroid, dtype=np.float32)
                denom = (np.linalg.norm(target) * np.linalg.norm(vec))
                if denom == 0:
                    continue
                sim = float(np.dot(target, vec) / denom)
                suggestions.append((bk, label or bk, cnt or 0, sim, rep_path, rep_thumb))
            suggestions.sort(key=lambda x: x[3], reverse=True)
            # Filter by threshold
            threshold = 0.80
            suggestions = [s for s in suggestions if s[3] >= threshold][:12]
            if not suggestions:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self.main_window, "No Suggestions", "No similar people found above threshold.")
                return
            # Build dialog with visual previews
            dlg = QDialog(self.main_window)
            dlg.setWindowTitle(f"Suggest Merge into '{target_name}'")
            dlg.resize(700, 600)
            outer = QVBoxLayout(dlg)
            
            # Header with target person preview
            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(8, 8, 8, 8)
            header_layout.setSpacing(12)
            
            # Target face preview
            target_face = QLabel()
            target_face.setFixedSize(80, 80)
            target_pix = None
            try:
                if target_rep_thumb:
                    data = base64.b64decode(target_rep_thumb) if isinstance(target_rep_thumb, str) else target_rep_thumb
                    target_pix = QPixmap()
                    target_pix.loadFromData(data)
                if (target_pix is None or target_pix.isNull()) and target_rep_path and os.path.exists(target_rep_path):
                    target_pix = QPixmap(target_rep_path)
                if target_pix and not target_pix.isNull():
                    # Make circular
                    from PySide6.QtGui import QPainter, QPainterPath
                    from PySide6.QtCore import QRect, QPoint
                    scaled = target_pix.scaled(80, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
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
                    target_face.setPixmap(output)
            except Exception as e:
                print(f"[GooglePhotosLayout] Failed to load target preview: {e}")
            target_face.setStyleSheet("border: 2px solid #1a73e8; border-radius: 40px;")
            header_layout.addWidget(target_face)
            
            # Target info
            info_label = QLabel(f"<b>Merge into: {target_name}</b><br><span style='color:#5f6368;'>Select similar people below (similarity ≥ {int(threshold*100)}%)</span>")
            info_label.setWordWrap(True)
            header_layout.addWidget(info_label, 1)
            outer.addWidget(header)
            
            # Recently merged section (if any)
            recent_merges = []
            try:
                with db._connect() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT target_branch, source_branches, created_at
                        FROM face_merge_history
                        WHERE project_id = ?
                        ORDER BY created_at DESC
                        LIMIT 5
                    """, (self.project_id,))
                    recent_merges = cur.fetchall() or []
            except Exception as e:
                print(f"[GooglePhotosLayout] Failed to load merge history: {e}")
            
            if recent_merges:
                recent_header = QLabel("🕒 <b>Recently Merged</b> <span style='color:#5f6368; font-size:9pt;'>(Quick undo available)</span>")
                recent_header.setStyleSheet("padding: 8px; background: #f8f9fa; border-radius: 4px; margin: 4px 0;")
                outer.addWidget(recent_header)
                
                recent_widget = QWidget()
                recent_layout = QVBoxLayout(recent_widget)
                recent_layout.setContentsMargins(8, 4, 8, 4)
                recent_layout.setSpacing(4)
                
                for target_bk, source_bks, created_at in recent_merges:
                    # Get target name
                    with db._connect() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT label FROM face_branch_reps WHERE project_id = ? AND branch_key = ?", (self.project_id, target_bk))
                        target_row = cur.fetchone()
                        target_label = (target_row[0] if target_row and target_row[0] else target_bk)
                    
                    merge_label = QLabel(f"• <b>{len(source_bks.split(','))} people</b> → <b>{target_label}</b> <span style='color:#5f6368; font-size:8pt;'>({created_at})</span>")
                    merge_label.setStyleSheet("font-size: 9pt; padding: 2px 8px;")
                    recent_layout.addWidget(merge_label)
                
                outer.addWidget(recent_widget)
            
            # Scrollable suggestions grid with face previews
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setSpacing(12)
            checks = {}
            
            for i, (bk, name, cnt, sim, rep_path, rep_thumb) in enumerate(suggestions):
                card = QWidget()
                card.setStyleSheet("""
                    QWidget {
                        background: white;
                        border: 1px solid #dadce0;
                        border-radius: 8px;
                    }
                    QWidget:hover {
                        border: 2px solid #1a73e8;
                        background: #f8f9fa;
                    }
                """)
                v = QVBoxLayout(card)
                v.setContentsMargins(8, 8, 8, 8)
                v.setSpacing(6)
                
                # Face preview (80x80 circular)
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
                        from PySide6.QtGui import QPainter, QPainterPath
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
                except Exception as e:
                    face_label.setStyleSheet("background: #e8eaed; border-radius: 40px; font-size: 24pt;")
                    face_label.setText("👤")
                    print(f"[GooglePhotosLayout] Failed to load suggestion preview: {e}")
                v.addWidget(face_label)
                
                # Name and similarity
                name_label = QLabel(f"<b>{name}</b>")
                name_label.setAlignment(Qt.AlignCenter)
                name_label.setWordWrap(True)
                v.addWidget(name_label)
                
                sim_label = QLabel(f"{int(sim*100)}% match • {cnt} photos")
                sim_label.setStyleSheet("color: #1a73e8; font-size: 9pt;")
                sim_label.setAlignment(Qt.AlignCenter)
                v.addWidget(sim_label)
                
                # Checkbox
                cb = QCheckBox("Select to merge")
                cb.setStyleSheet("font-size: 9pt;")
                checks[bk] = cb
                v.addWidget(cb, 0, Qt.AlignCenter)
                
                row = i // 3
                col = i % 3
                grid.addWidget(card, row, col)
            
            scroll.setWidget(container)
            outer.addWidget(scroll, 1)
            
            # Actions
            btns = QHBoxLayout()
            btns.addStretch()
            cancel_btn = QPushButton("Cancel")
            apply_btn = QPushButton("🔗 Merge Selected")
            apply_btn.setStyleSheet("""
                QPushButton {
                    background: #1a73e8;
                    color: white;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #1557b0;
                }
            """)
            btns.addWidget(cancel_btn)
            btns.addWidget(apply_btn)
            outer.addLayout(btns)
            
            def do_merge():
                selected = [bk for bk, cb in checks.items() if cb.isChecked()]
                if not selected:
                    dlg.accept()
                    return
                for src in selected:
                    # Use source name for message
                    from reference_db import ReferenceDB
                    db2 = ReferenceDB()
                    with db2._connect() as conn:
                        label_row = conn.execute("SELECT label FROM face_branch_reps WHERE project_id = ? AND branch_key = ?", (self.project_id, src)).fetchone()
                        src_name = (label_row[0] if label_row and label_row[0] else src)
                    self._perform_merge(src, target_branch_key, src_name)
                dlg.accept()
            
            apply_btn.clicked.connect(do_merge)
            cancel_btn.clicked.connect(dlg.reject)
            dlg.exec()
        except Exception as e:
            print(f"[GooglePhotosLayout] Merge suggestions failed: {e}")
            import traceback
            traceback.print_exc()

    def _open_person_detail(self, branch_key: str):
        """Person detail view with batch remove/merge and confidence filter."""
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QScrollArea, QWidget, QGridLayout, QCheckBox, QPushButton, QHBoxLayout
            import numpy as np, os
            from PySide6.QtGui import QPixmap
            from reference_db import ReferenceDB
            db = ReferenceDB()
            # Fetch centroid
            centroid_vec = None
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT label, centroid FROM face_branch_reps WHERE project_id = ? AND branch_key = ?", (self.project_id, branch_key))
                row = cur.fetchone()
                person_name = (row[0] if row and row[0] else branch_key)
                centroid_vec = np.frombuffer(row[1], dtype=np.float32) if row and row[1] else None
                # Fetch crops
                cur.execute("SELECT id, crop_path, embedding FROM face_crops WHERE project_id = ? AND branch_key = ?", (self.project_id, branch_key))
                crops = cur.fetchall() or []
            # Build list with confidence
            items = []
            for rid, crop_path, emb in crops:
                sim = 0.0
                if centroid_vec is not None and emb:
                    vec = np.frombuffer(emb, dtype=np.float32)
                    denom = (np.linalg.norm(centroid_vec) * np.linalg.norm(vec))
                    if denom > 0:
                        sim = float(np.dot(centroid_vec, vec) / denom)
                items.append((rid, crop_path, sim))
            # Dialog
            dlg = QDialog(self.main_window)
            dlg.setWindowTitle(f"Person Details: {person_name}")
            outer = QVBoxLayout(dlg)
            # Filter
            filter_combo = QComboBox(); filter_combo.addItems(["All", "High (≥0.85)", "Medium (0.70–0.85)", "Low (<0.70)"])
            outer.addWidget(filter_combo)
            # Grid
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            container = QWidget(); grid = QGridLayout(container); grid.setContentsMargins(8,8,8,8); grid.setSpacing(8)
            checks = {}
            def populate():
                # Clear
                while grid.count():
                    it = grid.takeAt(0)
                    if it and it.widget():
                        it.widget().deleteLater()
                idx = 0
                sel = filter_combo.currentIndex()
                for rid, path, sim in items:
                    if sel == 1 and sim < 0.85: continue
                    if sel == 2 and (sim < 0.70 or sim >= 0.85): continue
                    if sel == 3 and sim >= 0.70: continue
                    card = QWidget(); v = QVBoxLayout(card); v.setContentsMargins(4,4,4,4); v.setSpacing(4)
                    img = QLabel(); img.setFixedSize(140,140)
                    px = QPixmap(path) if path and os.path.exists(path) else QPixmap()
                    if not px.isNull(): img.setPixmap(px.scaled(140,140,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                    v.addWidget(img)
                    lbl = QLabel(f"Similarity: {int(sim*100)}%")
                    lbl.setStyleSheet("color:#5f6368;")
                    v.addWidget(lbl)
                    cb = QCheckBox("Select")
                    checks[rid] = cb
                    v.addWidget(cb)
                    grid.addWidget(card, idx//4, idx%4); idx += 1
            populate()
            scroll.setWidget(container)
            outer.addWidget(scroll)
            # Actions
            actions = QHBoxLayout(); actions.addStretch()
            remove_btn = QPushButton("Remove Selected")
            merge_btn = QPushButton("Merge Selected Into…")
            close_btn = QPushButton("Close")
            actions.addWidget(close_btn); actions.addWidget(remove_btn); actions.addWidget(merge_btn)
            outer.addLayout(actions)
            def remove_selected():
                """Move selected faces to unidentified cluster and update counts."""
                target = "face_unidentified"
                ids = [rid for rid,cb in checks.items() if cb.isChecked()]
                if not ids: return
                
                # Get source branch_key from the current person
                source_branch = branch_key
                
                with ReferenceDB()._connect() as conn:
                    cur = conn.cursor()
                    placeholders = ",".join(["?"]*len(ids))
                    cur.execute(f"UPDATE face_crops SET branch_key = ? WHERE project_id = ? AND id IN ({placeholders})", [target, self.project_id] + ids)
                    
                    # CRITICAL: Update counts for both source and target clusters
                    # Update source cluster count
                    cur.execute("""
                        UPDATE face_branch_reps
                        SET count = (
                            SELECT COUNT(DISTINCT image_path)
                            FROM face_crops
                            WHERE project_id = ? AND branch_key = ?
                        )
                        WHERE project_id = ? AND branch_key = ?
                    """, (self.project_id, source_branch, self.project_id, source_branch))
                    
                    # Update target (unidentified) cluster count
                    cur.execute("""
                        UPDATE face_branch_reps
                        SET count = (
                            SELECT COUNT(DISTINCT image_path)
                            FROM face_crops
                            WHERE project_id = ? AND branch_key = ?
                        )
                        WHERE project_id = ? AND branch_key = ?
                    """, (self.project_id, target, self.project_id, target))
                    
                    conn.commit()
                    print(f"[GooglePhotosLayout] Removed {len(ids)} faces from {source_branch} to {target}, counts updated")
                
                # Refresh people UI
                if hasattr(self, '_build_people_tree'):
                    self._build_people_tree()
                populate()
            def merge_selected():
                """Merge selected faces into another person with visual picker."""
                ids = [rid for rid, cb in checks.items() if cb.isChecked()]
                if not ids:
                    return
                
                # Get source branch_key from the current person
                source_branch = branch_key
                
                # Show visual person picker dialog
                picker_dlg = PersonPickerDialog(self.project_id, parent=self.main_window, exclude_branch=source_branch)
                if picker_dlg.exec() == QDialog.Accepted:
                    selected_target = picker_dlg.selected_branch
                    if not selected_target:
                        return
                    
                    # Move faces
                    with ReferenceDB()._connect() as conn:
                        cur = conn.cursor()
                        placeholders = ",".join(["?"]*len(ids))
                        cur.execute(f"UPDATE face_crops SET branch_key = ? WHERE project_id = ? AND id IN ({placeholders})", [selected_target, self.project_id] + ids)
                        
                        # CRITICAL: Update counts for both source and target clusters
                        # Update source cluster count
                        cur.execute("""
                            UPDATE face_branch_reps
                            SET count = (
                                SELECT COUNT(DISTINCT image_path)
                                FROM face_crops
                                WHERE project_id = ? AND branch_key = ?
                            )
                            WHERE project_id = ? AND branch_key = ?
                        """, (self.project_id, source_branch, self.project_id, source_branch))
                        
                        # Update target cluster count
                        cur.execute("""
                            UPDATE face_branch_reps
                            SET count = (
                                SELECT COUNT(DISTINCT image_path)
                                FROM face_crops
                                WHERE project_id = ? AND branch_key = ?
                            )
                            WHERE project_id = ? AND branch_key = ?
                        """, (self.project_id, selected_target, self.project_id, selected_target))
                        
                        conn.commit()
                        print(f"[GooglePhotosLayout] Merged {len(ids)} faces from {source_branch} to {selected_target}, counts updated")
                    
                    if hasattr(self, '_build_people_tree'):
                        self._build_people_tree()
                    populate()
            filter_combo.currentIndexChanged.connect(lambda _: populate())
            close_btn.clicked.connect(dlg.reject)
            remove_btn.clicked.connect(remove_selected)
            merge_btn.clicked.connect(merge_selected)
            dlg.exec()
        except Exception as e:
            print(f"[GooglePhotosLayout] Person detail failed: {e}")

    def _delete_person(self, branch_key: str, person_name: str):
        """Delete a person/face cluster."""
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self.main_window,
            "Delete Person",
            f"Are you sure you want to delete '{person_name}'?\n\n"
            f"This will remove all face data for this person.\n"
            f"Original photos will NOT be deleted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db._connect() as conn:
                # Delete face crops
                conn.execute("""
                    DELETE FROM face_crops
                    WHERE project_id = ? AND branch_key = ?
                """, (self.project_id, branch_key))

                # Delete branch representative
                conn.execute("""
                    DELETE FROM face_branch_reps
                    WHERE project_id = ? AND branch_key = ?
                """, (self.project_id, branch_key))

                # Delete branch
                conn.execute("""
                    DELETE FROM branches
                    WHERE project_id = ? AND branch_key = ?
                """, (self.project_id, branch_key))

                conn.commit()

            # Rebuild people tree
            self._build_people_tree()

            print(f"[GooglePhotosLayout] Person deleted: {person_name}")
            QMessageBox.information(self.main_window, "Deleted", f"'{person_name}' deleted successfully")

        except Exception as e:
            print(f"[GooglePhotosLayout] Delete failed: {e}")
            QMessageBox.critical(self.main_window, "Delete Failed", f"Error: {e}")

    def _on_section_header_clicked(self):
        """
        Handle section header click - clear all filters and show all photos.

        Based on Google Photos UX: Clicking section headers returns to "All Photos" view.
        """
        print("[GooglePhotosLayout] Section header clicked - clearing all filters")

        # Clear all filters
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=None,
            filter_month=None,
            filter_folder=None,
            filter_person=None
        )

        # Also clear search box
        pass

    def _build_videos_tree(self):
        """
        Build videos tree in sidebar with filters (copied from Current Layout).

        Features:
        - All Videos
        - By Duration (Short/Medium/Long)
        - By Resolution (SD/HD/FHD/4K)
        - By Date (Year/Month hierarchy)
        NOTE: With AccordionSidebar, this is handled internally - this method is a no-op.
        """
        # Old sidebar implementation - no longer needed with AccordionSidebar
        if not hasattr(self, 'videos_tree'):
            return

        try:
            from services.video_service import VideoService
            video_service = VideoService()

            print(f"[GoogleLayout] Loading videos for project_id={self.project_id}")
            videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
            total_videos = len(videos)
            print(f"[GoogleLayout] Found {total_videos} videos in project {self.project_id}")

            if not videos:
                # No videos - show message
                no_videos_item = QTreeWidgetItem(["  (No videos yet)"])
                no_videos_item.setForeground(0, QColor("#888888"))
                self.videos_tree.addTopLevelItem(no_videos_item)
                return

            # All Videos
            all_item = QTreeWidgetItem([f"All Videos ({total_videos})"])
            role_set_json(all_item, {"type": "all_videos"}, role=Qt.UserRole)
            self.videos_tree.addTopLevelItem(all_item)

            # By Duration
            short_videos = [v for v in videos if v.get('duration_seconds') and v['duration_seconds'] < 30]
            medium_videos = [v for v in videos if v.get('duration_seconds') and 30 <= v['duration_seconds'] < 300]
            long_videos = [v for v in videos if v.get('duration_seconds') and v['duration_seconds'] >= 300]

            if short_videos or medium_videos or long_videos:
                duration_parent = QTreeWidgetItem([f"⏱️ By Duration"])
                self.videos_tree.addTopLevelItem(duration_parent)

                if short_videos:
                    short_item = QTreeWidgetItem([f"  Short < 30s ({len(short_videos)})"])
                    role_set_json(short_item, {"type": "duration", "key": "short"}, role=Qt.UserRole)
                    duration_parent.addChild(short_item)

                if medium_videos:
                    medium_item = QTreeWidgetItem([f"  Medium 30s-5m ({len(medium_videos)})"])
                    role_set_json(medium_item, {"type": "duration", "key": "medium"}, role=Qt.UserRole)
                    duration_parent.addChild(medium_item)

                if long_videos:
                    long_item = QTreeWidgetItem([f"  Long > 5m ({len(long_videos)})"])
                    role_set_json(long_item, {"type": "duration", "key": "long"}, role=Qt.UserRole)
                    duration_parent.addChild(long_item)

            # By Resolution
            sd_videos = [v for v in videos if v.get('width') and v.get('height') and v['height'] < 720]
            hd_videos = [v for v in videos if v.get('width') and v.get('height') and 720 <= v['height'] < 1080]
            fhd_videos = [v for v in videos if v.get('width') and v.get('height') and 1080 <= v['height'] < 2160]
            uhd_videos = [v for v in videos if v.get('width') and v.get('height') and v['height'] >= 2160]

            if sd_videos or hd_videos or fhd_videos or uhd_videos:
                res_parent = QTreeWidgetItem([f"📺 By Resolution"])
                self.videos_tree.addTopLevelItem(res_parent)

                if sd_videos:
                    sd_item = QTreeWidgetItem([f"  SD < 720p ({len(sd_videos)})"])
                    role_set_json(sd_item, {"type": "resolution", "key": "sd"}, role=Qt.UserRole)
                    res_parent.addChild(sd_item)

                if hd_videos:
                    hd_item = QTreeWidgetItem([f"  HD 720p ({len(hd_videos)})"])
                    role_set_json(hd_item, {"type": "resolution", "key": "hd"}, role=Qt.UserRole)
                    res_parent.addChild(hd_item)

                if fhd_videos:
                    fhd_item = QTreeWidgetItem([f"  Full HD 1080p ({len(fhd_videos)})"])
                    role_set_json(fhd_item, {"type": "resolution", "key": "fhd"}, role=Qt.UserRole)
                    res_parent.addChild(fhd_item)

                if uhd_videos:
                    uhd_item = QTreeWidgetItem([f"  4K 2160p+ ({len(uhd_videos)})"])
                    role_set_json(uhd_item, {"type": "resolution", "key": "4k"}, role=Qt.UserRole)
                    res_parent.addChild(uhd_item)

            # By Date (Year/Month hierarchy)
            try:
                from reference_db import ReferenceDB
                db = ReferenceDB()
                video_hier = db.get_video_date_hierarchy(self.project_id) or {}

                if video_hier:
                    date_parent = QTreeWidgetItem([f"📅 By Date"])
                    self.videos_tree.addTopLevelItem(date_parent)

                    for year in sorted(video_hier.keys(), key=lambda y: int(str(y)), reverse=True):
                        year_count = db.count_videos_for_year(year, self.project_id)
                        year_item = QTreeWidgetItem([f"  {year} ({year_count})"])
                        role_set_json(year_item, {"type": "video_year", "year": year}, role=Qt.UserRole)
                        date_parent.addChild(year_item)

                        # Month nodes under year
                        months = video_hier[year]
                        for month in sorted(months.keys(), key=lambda m: int(str(m))):
                            month_label = f"{int(month):02d}"
                            month_count = db.count_videos_for_month(year, month, self.project_id)
                            month_item = QTreeWidgetItem([f"    {month_label} ({month_count})"])
                            role_set_json(month_item, {"type": "video_month", "year": year, "month": month_label}, role=Qt.UserRole)
                            year_item.addChild(month_item)
            except Exception as e:
                print(f"[GoogleLayout] Failed to build video date hierarchy: {e}")

            print(f"[GoogleLayout] Built videos tree with {total_videos} videos")

        except Exception as e:
            print(f"[GoogleLayout] ⚠️ Error building videos tree: {e}")
            import traceback
            traceback.print_exc()

    def _on_videos_header_clicked(self):
        """
        Handle videos header click - show all videos in timeline.
        """
        print("[GoogleLayout] Videos header clicked - loading all videos")

        try:
            from services.video_service import VideoService
            video_service = VideoService()

            videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
            print(f"[GoogleLayout] Loading {len(videos)} videos")

            if not videos:
                print("[GoogleLayout] No videos found")
                return

            # Show videos in timeline (will need to implement video display)
            self._show_videos_in_timeline(videos)

        except Exception as e:
            print(f"[GoogleLayout] ⚠️ Error loading videos: {e}")
            import traceback
            traceback.print_exc()

    def _on_videos_item_clicked(self, item: QTreeWidgetItem, column: int):
        """
        Handle videos tree item click - filter/show videos.

        Args:
            item: Clicked tree item
            column: Column index (always 0)
        """
        data = role_get_json(item, role=Qt.UserRole)
        if not data:
            return

        item_type = data.get("type")

        if item_type == "all_videos":
            print("[GoogleLayout] Showing all videos")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                self._show_videos_in_timeline(videos)
            except Exception as e:
                print(f"[GoogleLayout] Error loading all videos: {e}")

        elif item_type in ["duration", "resolution"]:
            # Re-query and filter on click (no embedded video lists)
            filter_key = data.get("key", "")
            print(f"[GoogleLayout] Filtering videos by {item_type}:{filter_key}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                all_videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                if item_type == "duration":
                    if filter_key == "short":
                        videos = [v for v in all_videos if v.get('duration_seconds') and v['duration_seconds'] < 30]
                    elif filter_key == "medium":
                        videos = [v for v in all_videos if v.get('duration_seconds') and 30 <= v['duration_seconds'] < 300]
                    elif filter_key == "long":
                        videos = [v for v in all_videos if v.get('duration_seconds') and v['duration_seconds'] >= 300]
                    else:
                        videos = all_videos
                elif item_type == "resolution":
                    if filter_key == "sd":
                        videos = [v for v in all_videos if v.get('height') and v['height'] < 720]
                    elif filter_key == "hd":
                        videos = [v for v in all_videos if v.get('height') and 720 <= v['height'] < 1080]
                    elif filter_key == "fhd":
                        videos = [v for v in all_videos if v.get('height') and 1080 <= v['height'] < 2160]
                    elif filter_key == "4k":
                        videos = [v for v in all_videos if v.get('height') and v['height'] >= 2160]
                    else:
                        videos = all_videos
                print(f"[GoogleLayout] Showing {len(videos)} videos filtered by {item_type}:{filter_key}")
                self._show_videos_in_timeline(videos)
            except Exception as e:
                print(f"[GoogleLayout] Error filtering videos: {e}")

        elif item_type == "video_year":
            year = data.get("year")
            print(f"[GoogleLayout] Showing videos from year {year}")
            try:
                from reference_db import ReferenceDB
                from services.video_service import VideoService
                db = ReferenceDB()
                video_service = VideoService()

                # Get all videos for this year
                all_videos = video_service.get_videos_by_project(self.project_id)
                year_videos = [v for v in all_videos if v.get('created_date', '').startswith(str(year))]
                self._show_videos_in_timeline(year_videos)
            except Exception as e:
                print(f"[GoogleLayout] Error loading videos for year {year}: {e}")

        elif item_type == "video_month":
            year = data.get("year")
            month = data.get("month")
            print(f"[GoogleLayout] Showing videos from {year}-{month}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()

                all_videos = video_service.get_videos_by_project(self.project_id)
                month_videos = [v for v in all_videos if v.get('created_date', '').startswith(f"{year}-{month}")]
                self._show_videos_in_timeline(month_videos)
            except Exception as e:
                print(f"[GoogleLayout] Error loading videos for {year}-{month}: {e}")

    def _show_videos_in_timeline(self, videos: list):
        """
        Display videos in the timeline (similar to photos).

        Args:
            videos: List of video dictionaries from VideoService
        """
        print(f"[GoogleLayout] Showing {len(videos)} videos in timeline")

        # Clear existing timeline
        try:
            while self.timeline_layout.count():
                child = self.timeline_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        except Exception as e:
            print(f"[GoogleLayout] Error clearing timeline: {e}")

        if not videos:
            # Show empty state
            empty_label = QLabel("🎬 No videos found")
            empty_label.setAlignment(Qt.AlignCenter)
            empty_label.setStyleSheet("font-size: 12pt; color: #888; padding: 60px;")
            self.timeline_layout.addWidget(empty_label)
            # Clear displayed paths when no videos
            self.all_displayed_paths = []
            return

        # Group videos by date
        videos_by_date = defaultdict(list)
        for video in videos:
            date = video.get('created_date', 'No Date')
            if date and date != 'No Date':
                # Extract just the date part (YYYY-MM-DD)
                date = date.split(' ')[0] if ' ' in date else date
            videos_by_date[date].append(video)

        # Track all displayed video paths for lightbox navigation
        self.all_displayed_paths = [video['path'] for video in videos]
        print(f"[GoogleLayout] Tracking {len(self.all_displayed_paths)} video paths for navigation")

        # Create date groups for videos
        for date_str in sorted(videos_by_date.keys(), reverse=True):
            date_videos = videos_by_date[date_str]
            date_group = self._create_video_date_group(date_str, date_videos)
            self.timeline_layout.addWidget(date_group)

        # Add spacer at bottom
        self.timeline_layout.addStretch()

    def _create_video_date_group(self, date_str: str, videos: list) -> QWidget:
        """
        Create a date group widget for videos (header + video grid).

        Args:
            date_str: Date string "YYYY-MM-DD"
            videos: List of video dictionaries
        """
        group = QFrame()
        group.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #e8eaed;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        # Header - Use smart date labels (like photo groups)
        try:
            date_obj = datetime.fromisoformat(date_str)
            formatted_date = self._get_smart_date_label(date_obj)
        except:
            formatted_date = date_str

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        date_label = QLabel(f"📅 {formatted_date}")
        date_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #202124;")
        header_layout.addWidget(date_label)

        count_label = QLabel(f"({len(videos)} video{'s' if len(videos) != 1 else ''})")
        count_label.setStyleSheet("font-size: 10pt; color: #5f6368; margin-left: 8px;")
        header_layout.addWidget(count_label)

        header_layout.addStretch()
        layout.addWidget(header)

        # Video grid (QUICK WIN #2: Also responsive)
        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setSpacing(2)  # GOOGLE PHOTOS STYLE: Minimal spacing
        grid.setContentsMargins(0, 0, 0, 0)

        # QUICK WIN #2: Responsive columns for videos too
        columns = self._calculate_responsive_columns(200)  # Use standard 200px thumb size

        for i, video in enumerate(videos):
            row = i // columns
            col = i % columns

            # Create video thumbnail widget
            video_thumb = self._create_video_thumbnail(video)
            grid.addWidget(video_thumb, row, col)

        layout.addWidget(grid_container)

        return group

    def _create_video_thumbnail(self, video: dict) -> QWidget:
        """
        Create a video thumbnail widget with play icon overlay.

        Args:
            video: Video dictionary with path, duration, etc.
        """
        thumb_widget = QLabel()
        thumb_widget.setFixedSize(200, 200)
        thumb_widget.setAlignment(Qt.AlignCenter)
        thumb_widget.setStyleSheet("""
            QLabel {
                background: #f8f9fa;
                border: 1px solid #e8eaed;
                border-radius: 4px;
                color: white;
            }
            QLabel:hover {
                border: 2px solid #1a73e8;
            }
        """)

        # Set mouse cursor programmatically (Qt doesn't support cursor in stylesheets)
        from PySide6.QtCore import Qt as QtCore
        thumb_widget.setCursor(QtCore.PointingHandCursor)

        # Load video thumbnail
        video_path = video.get('path', '')

        try:
            from services.video_thumbnail_service import get_video_thumbnail_service
            thumb_service = get_video_thumbnail_service()

            # Try existing thumbnail first, then generate
            if thumb_service.thumbnail_exists(video_path):
                thumb_path = str(thumb_service.get_thumbnail_path(video_path))
            else:
                thumb_path = thumb_service.generate_thumbnail(
                    video_path, width=200, height=200,
                )

            if thumb_path and os.path.exists(thumb_path):
                pixmap = QPixmap(str(thumb_path))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    thumb_widget.setPixmap(scaled)
                    # Duration badge overlay (bottom-right)
                    duration_secs = video.get('duration_seconds')
                    if duration_secs:
                        minutes = int(duration_secs) // 60
                        seconds = int(duration_secs) % 60
                        duration_text = f"{minutes}:{seconds:02d}"
                        badge = QLabel(duration_text, thumb_widget)
                        badge.setStyleSheet("background: rgba(0,0,0,0.6); color: white; font-size: 9pt; padding: 2px 6px; border-radius: 8px;")
                        badge.adjustSize()
                        bx = thumb_widget.width() - badge.width() - 6
                        by = thumb_widget.height() - badge.height() - 6
                        badge.move(bx, by)
                        badge.raise_()
                    return thumb_widget

            # Fallback: styled placeholder with play triangle
            self._apply_video_placeholder(thumb_widget, video)
        except Exception as e:
            print(f"[GoogleLayout] Error loading video thumbnail for {video_path}: {e}")
            self._apply_video_placeholder(thumb_widget, video)

        # FIXED: Open lightbox instead of video player directly
        thumb_widget.mousePressEvent = lambda event: self._open_photo_lightbox(video_path)

        return thumb_widget

    def _apply_video_placeholder(self, widget: QLabel, video: dict):
        """Apply a proper styled video placeholder with play triangle."""
        from PySide6.QtGui import QPainter, QBrush, QPolygonF, QPen, QFont as QFontG
        from PySide6.QtCore import QPointF, QRectF

        sz = 200
        pixmap = QPixmap(sz, sz)
        pixmap.fill(QColor(38, 38, 38))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Play triangle
        tri = sz * 0.30
        cx, cy = sz / 2.0, sz / 2.0 - 10
        triangle = QPolygonF([
            QPointF(cx - tri * 0.4, cy - tri * 0.5),
            QPointF(cx + tri * 0.5, cy),
            QPointF(cx - tri * 0.4, cy + tri * 0.5),
        ])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.drawPolygon(triangle)

        # Filename label
        fname = os.path.basename(video.get("path", ""))
        if len(fname) > 24:
            fname = fname[:21] + "..."
        painter.setPen(QPen(QColor(180, 180, 180)))
        font = QFontG()
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(QRectF(4, sz - 28, sz - 8, 24), Qt.AlignHCenter | Qt.AlignBottom, fname)

        # Duration badge if available
        dur = video.get("duration_seconds")
        if dur:
            mins, secs = int(dur) // 60, int(dur) % 60
            dur_text = f"{mins}:{secs:02d}"
            painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(sz - 52, 6, 46, 20), 4, 4)
            painter.setPen(QPen(QColor(255, 255, 255)))
            font.setPixelSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(sz - 52, 6, 46, 20), Qt.AlignCenter, dur_text)

        painter.end()
        widget.setPixmap(pixmap)

    def _open_video_player(self, video_path: str):
        """
        Open video player for the given video path with navigation support.

        Args:
            video_path: Path to video file
        """
        print(f"[GoogleLayout] 🎬 Opening video player for: {video_path}")

        try:
            # Get all videos for navigation
            from services.video_service import VideoService
            video_service = VideoService()

            all_videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
            video_paths = [v['path'] for v in all_videos]

            # Find current video index
            start_index = 0
            try:
                start_index = video_paths.index(video_path)
            except ValueError:
                print(f"[GoogleLayout] ⚠️ Video not found in list, using index 0")

            print(f"[GoogleLayout] Found {len(video_paths)} videos, current index: {start_index}")

            # Check if main_window is accessible
            if not hasattr(self, 'main_window') or self.main_window is None:
                print("[GoogleLayout] ⚠️ ERROR: main_window not accessible")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(None, "Video Player Error",
                    "Cannot open video player: Main window not accessible.\n\n"
                    "Try switching to Current Layout to play videos.")
                return

            # Check if _open_video_player method exists
            if not hasattr(self.main_window, '_open_video_player'):
                print("[GoogleLayout] ⚠️ ERROR: main_window doesn't have _open_video_player method")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(None, "Video Player Error",
                    "Video player not available in this layout.\n\n"
                    "Try switching to Current Layout to play videos.")
                return

            # Open video player with navigation support
            self.main_window._open_video_player(video_path, video_paths, start_index)
            print(f"[GoogleLayout] ✓ Video player opened successfully")

        except Exception as e:
            print(f"[GoogleLayout] ⚠️ ERROR opening video player: {e}")
            import traceback
            traceback.print_exc()

            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Video Player Error",
                f"Failed to open video player:\n\n{str(e)}\n\n"
                "Check console for details.")

    def _create_date_group(self, date_str: str, photos: List[Tuple], thumb_size: int = 200) -> QWidget:
        """
        Create a date group widget (header + photo grid).

        QUICK WIN #4: Now supports collapse/expand functionality.

        Args:
            date_str: Date string "YYYY-MM-DD"
            photos: List of (path, date_taken, width, height)
        """
        group = QFrame()
        group.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #e8eaed;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        # QUICK WIN #4: Initialize collapse state (default: expanded)
        if date_str not in self.date_group_collapsed:
            self.date_group_collapsed[date_str] = False  # False = expanded

        # Header (with collapse/expand button)
        header = self._create_date_header(date_str, len(photos))
        layout.addWidget(header)

        # Photo grid (pass thumb_size)
        grid = self._create_photo_grid(photos, thumb_size)
        layout.addWidget(grid)

        # QUICK WIN #4: Store grid reference for collapse/expand
        self.date_group_grids[date_str] = grid

        # Apply initial collapse state
        if self.date_group_collapsed.get(date_str, False):
            grid.hide()

        return group

    def _format_smart_date(self, date_str: str) -> str:
        """Format date with Google Photos-style smart labels (Today, Yesterday, etc.)."""
        try:
            from datetime import timedelta
            
            date_obj = datetime.fromisoformat(date_str)
            today = datetime.now().date()
            photo_date = date_obj.date()
            
            diff_days = (today - photo_date).days
            
            # Smart labels based on recency
            if diff_days == 0:
                return "Today"
            elif diff_days == 1:
                return "Yesterday"
            elif diff_days <= 6:
                # This week - show day name
                return date_obj.strftime("%A")  # e.g., "Monday"
            elif diff_days <= 13:
                # Last week
                return f"Last {date_obj.strftime('%A')}"
            elif diff_days <= 30:
                # This month - show date without year
                return date_obj.strftime("%B %d")  # e.g., "November 15"
            elif photo_date.year == today.year:
                # This year - show month and day
                return date_obj.strftime("%B %d")  # e.g., "March 22"
            else:
                # Previous years - show full date
                return date_obj.strftime("%B %d, %Y")  # e.g., "March 22, 2023"
        except:
            # Fallback to basic formatting
            try:
                date_obj = datetime.fromisoformat(date_str)
                return date_obj.strftime("%B %d, %Y")
            except:
                return date_str
    
    def _create_date_header(self, date_str: str, count: int) -> QWidget:
        """
        Create date group header with date and photo count.

        QUICK WIN #4: Now includes collapse/expand button.
        Google Photos Enhancement: Smart date labels (Today, Yesterday, etc.)
        """
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        # QUICK WIN #4: Collapse/Expand button (▼ = expanded, ► = collapsed)
        collapse_btn = QPushButton()
        is_collapsed = self.date_group_collapsed.get(date_str, False)
        collapse_btn.setText("►" if is_collapsed else "▼")
        collapse_btn.setFixedSize(24, 24)
        collapse_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 12pt;
                color: #5f6368;
                padding: 0;
            }
            QPushButton:hover {
                color: #202124;
                background: #f1f3f4;
                border-radius: 4px;
            }
        """)
        collapse_btn.setCursor(Qt.PointingHandCursor)
        collapse_btn.clicked.connect(lambda: self._toggle_date_group(date_str, collapse_btn))
        header_layout.addWidget(collapse_btn)

        # PHASE 3 #4: Smart date grouping with friendly labels
        try:
            date_obj = datetime.fromisoformat(date_str)
            formatted_date = self._get_smart_date_label(date_obj)
        except:
            formatted_date = date_str

        # Date label (clickable for collapse/expand)
        date_label = QLabel(f"📅 {formatted_date}")
        date_label.setStyleSheet("""
            font-size: 14pt;
            font-weight: bold;
            color: #202124;
            padding: 4px;
        """)
        date_label.setCursor(Qt.PointingHandCursor)
        date_label.mousePressEvent = lambda e: self._toggle_date_group(date_str, collapse_btn)
        header_layout.addWidget(date_label)

        # PHASE 2 #6: Photo count badge (visual pill instead of plain text)
        count_badge = QLabel(f"{count}")
        count_badge.setStyleSheet("""
            QLabel {
                background: #e8f0fe;
                color: #1a73e8;
                font-size: 10pt;
                font-weight: bold;
                padding: 4px 10px;
                border-radius: 12px;
                margin-left: 8px;
            }
        """)
        count_badge.setToolTip(f"{count} photo{'s' if count != 1 else ''}")
        header_layout.addWidget(count_badge)

        header_layout.addStretch()

        return header

    def _get_smart_date_label(self, date_obj: datetime) -> str:
        """
        ENHANCED: Google Photos-style smart date labels (Today, Yesterday, Monday, etc.).
        More concise and user-friendly than verbose labels.

        Args:
            date_obj: datetime object

        Returns:
            str: Friendly date label
        """
        from datetime import timedelta

        now = datetime.now()
        today = now.date()
        photo_date = date_obj.date()

        # Calculate difference in days
        delta = (today - photo_date).days

        # Today (no extra date info - it's today!)
        if delta == 0:
            return "Today"

        # Yesterday
        elif delta == 1:
            return "Yesterday"

        # This Week (show day name only)
        elif delta <= 6:
            return date_obj.strftime("%A")  # "Monday", "Tuesday", etc.

        # Last Week (show "Last Monday", etc.)
        elif delta <= 13:
            return f"Last {date_obj.strftime('%A')}"

        # This Month (show month + day)
        elif photo_date.month == today.month and photo_date.year == today.year:
            return date_obj.strftime("%B %d")  # "November 15"

        # This Year (show month + day without year)
        elif photo_date.year == today.year:
            return date_obj.strftime("%B %d")  # "March 22"

        # Previous Years (show full date)
        else:
            return date_obj.strftime("%B %d, %Y")  # "March 22, 2023"

    def _create_empty_state(self, icon: str, title: str, message: str, action_text: str = "") -> QWidget:
        """
        PHASE 2 #7: Create friendly empty state with illustration.

        Args:
            icon: Emoji icon
            title: Main title
            message: Descriptive message
            action_text: Optional action hint

        Returns:
            QWidget: Styled empty state widget
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 80, 40, 80)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignCenter)

        # Large icon
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 72pt;")
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet("""
            font-size: 18pt;
            font-weight: bold;
            color: #202124;
        """)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # Message
        message_label = QLabel(message)
        message_label.setStyleSheet("""
            font-size: 11pt;
            color: #5f6368;
            line-height: 1.6;
        """)
        message_label.setAlignment(Qt.AlignCenter)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        # Action hint
        if action_text:
            action_label = QLabel(action_text)
            action_label.setStyleSheet("""
                font-size: 10pt;
                color: #80868b;
                font-style: italic;
            """)
            action_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(action_label)

        return container

    def _toggle_date_group(self, date_str: str, collapse_btn: QPushButton):
        """
        QUICK WIN #4: Toggle collapse/expand state for a date group.

        Args:
            date_str: Date string "YYYY-MM-DD"
            collapse_btn: The collapse/expand button widget
        """
        try:
            # Get current state
            is_collapsed = self.date_group_collapsed.get(date_str, False)
            new_state = not is_collapsed

            # Update state
            self.date_group_collapsed[date_str] = new_state

            # Get grid widget
            grid = self.date_group_grids.get(date_str)
            if not grid:
                print(f"[GooglePhotosLayout] ⚠️ Grid not found for {date_str}")
                return

            # Toggle visibility
            if new_state:  # Collapsing
                grid.hide()
                collapse_btn.setText("►")
                print(f"[GooglePhotosLayout] ▲ Collapsed date group: {date_str}")
            else:  # Expanding
                grid.show()
                collapse_btn.setText("▼")
                print(f"[GooglePhotosLayout] ▼ Expanded date group: {date_str}")

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error toggling date group {date_str}: {e}")

    def _create_date_group_placeholder(self, metadata: dict) -> QWidget:
        """
        QUICK WIN #3: Create placeholder widget for virtual scrolling.

        Placeholder maintains scroll position by matching estimated group height.
        Will be replaced with actual rendered group when it enters viewport.

        Args:
            metadata: Dict with date_str, photos, thumb_size, index

        Returns:
            QWidget: Placeholder with estimated height
        """
        placeholder = QWidget()

        # Estimate height based on photo count
        estimated_height = self._estimate_date_group_height(
            len(metadata['photos']),
            metadata['thumb_size']
        )

        placeholder.setFixedHeight(estimated_height)
        placeholder.setStyleSheet("background: #f8f9fa;")  # Light gray placeholder

        # Store metadata on widget for lazy rendering
        placeholder.setProperty('date_group_metadata', metadata)
        placeholder.setProperty('is_placeholder', True)

        return placeholder

    def _estimate_date_group_height(self, photo_count: int, thumb_size: int) -> int:
        """
        QUICK WIN #3: Estimate date group height for placeholder sizing.

        Height = header + grid + margins
        - Header: ~60px (date label + spacing)
        - Grid: rows * (thumb_size + spacing)
        - Margins: 28px (16 top, 12 bottom from layout.setContentsMargins)

        Args:
            photo_count: Number of photos in group
            thumb_size: Thumbnail size in pixels

        Returns:
            int: Estimated height in pixels
        """
        # Calculate responsive columns (same as grid rendering)
        columns = self._calculate_responsive_columns(thumb_size)

        # Calculate number of rows needed
        rows = (photo_count + columns - 1) // columns  # Ceiling division

        # Component heights
        header_height = 60  # Date label + spacing
        spacing = 2  # GOOGLE PHOTOS STYLE
        grid_height = rows * (thumb_size + spacing)
        margins = 28  # 16 + 12 from setContentsMargins
        border = 2  # 1px border top + bottom

        total_height = header_height + grid_height + margins + border

        return total_height

    def _render_visible_date_groups(self, viewport, viewport_rect):
        """
        QUICK WIN #3: Render date groups that are visible in viewport.

        Checks which date groups intersect with the viewport and replaces
        placeholders with actual rendered groups.

        Args:
            viewport: Timeline viewport widget
            viewport_rect: Viewport rectangle
        """
        try:
            groups_to_render = []

            # Check each date group to see if it's visible
            for metadata in self.date_groups_metadata:
                index = metadata['index']

                # Skip if already rendered
                if index in self.rendered_date_groups:
                    continue

                # Get the widget (placeholder)
                widget = self.date_group_widgets.get(index)
                if not widget:
                    continue

                # Check if widget is visible in viewport
                try:
                    # Map widget position to viewport coordinates
                    widget_pos = widget.mapTo(viewport, widget.rect().topLeft())
                    widget_rect = widget.rect()
                    widget_rect.moveTo(widget_pos)

                    # If widget intersects viewport, it's visible
                    if viewport_rect.intersects(widget_rect):
                        groups_to_render.append((index, metadata))

                except Exception as e:
                    continue

            # Render visible groups
            if groups_to_render:
                logger.debug(f"Rendering {len(groups_to_render)} date groups that entered viewport...")

                for index, metadata in groups_to_render:
                    try:
                        # Create actual rendered group
                        rendered_group = self._create_date_group(
                            metadata['date_str'],
                            metadata['photos'],
                            metadata['thumb_size']
                        )

                        # Replace placeholder with rendered group in layout
                        old_widget = self.date_group_widgets[index]
                        layout_index = self.timeline_layout.indexOf(old_widget)

                        if layout_index != -1:
                            # Remove placeholder
                            self.timeline_layout.removeWidget(old_widget)
                            old_widget.deleteLater()

                            # Insert rendered group at same position
                            self.timeline_layout.insertWidget(layout_index, rendered_group)
                            self.date_group_widgets[index] = rendered_group
                            self.rendered_date_groups.add(index)

                    except Exception as e:
                        logger.warning(f"Error rendering date group {index}: {e}")
                        continue

                logger.debug(f"Now {len(self.rendered_date_groups)}/{len(self.date_groups_metadata)} groups rendered")

        except Exception as e:
            logger.warning(f"Error in virtual scrolling: {e}")

    def _create_photo_grid(self, photos: List[Tuple], thumb_size: int = 200) -> QWidget:
        """
        Create photo grid with thumbnails.

        QUICK WIN #2: Responsive grid that adapts to viewport width.
        Google Photos Style: Minimal spacing for dense, clean grid.
        """
        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setSpacing(2)  # GOOGLE PHOTOS STYLE: Minimal padding
        grid.setContentsMargins(0, 0, 0, 0)

        # QUICK WIN #2: Calculate responsive columns based on viewport width
        # This makes the grid perfect on 1080p, 4K, mobile, etc.
        columns = self._calculate_responsive_columns(thumb_size)

        # Store grid reference for resize handling (QUICK WIN #2)
        if not hasattr(self, '_photo_grids'):
            self._photo_grids = []
        self._photo_grids.append({
            'container': grid_container,
            'grid': grid,
            'photos': photos,
            'thumb_size': thumb_size,
            'columns': columns
        })

        # Add photo thumbnails
        for i, photo in enumerate(photos):
            path, date_taken, width, height = photo

            row = i // columns
            col = i % columns

            thumb = self._create_thumbnail(path, thumb_size)
            grid.addWidget(thumb, row, col)

        return grid_container

    def _calculate_responsive_columns(self, thumb_size: int) -> int:
        """
        QUICK WIN #2: Calculate optimal column count based on viewport width.

        Algorithm (matches Google Photos):
        - Get available width from timeline viewport
        - Calculate how many thumbnails fit
        - Enforce min/max constraints (2-8 columns)
        - Account for spacing and margins

        Args:
            thumb_size: Thumbnail width in pixels

        Returns:
            int: Optimal number of columns (2-8)
        """
        # Get viewport width (timeline scroll area)
        if hasattr(self, 'timeline_scroll'):
            viewport_width = self.timeline_scroll.viewport().width()
        else:
            # Fallback during initialization
            viewport_width = 1200  # Reasonable default

        # Account for margins (20px left + 20px right from timeline_layout)
        available_width = viewport_width - 40

        # Account for grid spacing (2px between each thumbnail)
        spacing = 2

        # Calculate how many thumbnails fit
        # Formula: (width - margins) / (thumb_size + spacing)
        cols = int(available_width / (thumb_size + spacing))

        # Enforce constraints
        # Min: 2 columns (prevents single-column on small screens)
        # Max: 8 columns (prevents tiny thumbnails on huge screens)
        cols = max(2, min(8, cols))

        # DEBUG: Only print if columns changed (reduce log spam)
        if not hasattr(self, '_last_column_count') or self._last_column_count != cols:
            print(f"[GooglePhotosLayout] 📐 Responsive grid: {cols} columns (viewport: {viewport_width}px, thumb: {thumb_size}px)")
            self._last_column_count = cols

        return cols

    def _on_thumbnail_loaded(self, path: str, qimage, size: int):
        """
        Callback when async thumbnail loading completes.

        FIX 2026-02-08: Changed parameter from QPixmap to QImage for thread safety.
        The worker now emits QImage (thread-safe), and we convert to QPixmap here
        on the UI thread where it's safe to do so.

        Phase 3 #1: Added smooth fade-in animation for loaded thumbnails.
        Phase 3 #2: Stops pulsing animation and shows cached thumbnail.
        """
        # Clear inflight guard so this path isn't blocked on future reloads
        self._thumb_inflight.discard(path)

        # Find the button for this path
        button = self.thumbnail_buttons.get(path)
        if not button:
            return  # Button was destroyed (e.g., during reload)

        try:
            # FIX 2026-02-08: Convert QImage -> QPixmap on UI thread (safe!)
            if qimage and not qimage.isNull():
                # Convert QImage to QPixmap on UI thread
                pixmap = QPixmap.fromImage(qimage)
                scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                button.setIcon(QIcon(scaled))
                button.setIconSize(QSize(size - 4, size - 4))
                button.setText("")  # Clear placeholder text

                # PHASE 3 #1: Smooth fade-in animation for thumbnail
                # PHASE 3 #2 FIX: Always create fresh graphics effect to avoid conflicts
                from PySide6.QtCore import QPropertyAnimation, QEasingCurve

                # Create new opacity effect (don't reuse to avoid animation conflicts)
                opacity_effect = QGraphicsOpacityEffect()
                button.setGraphicsEffect(opacity_effect)
                opacity_effect.setOpacity(0.0)

                # Animate fade-in from 0 to 1
                fade_in = QPropertyAnimation(opacity_effect, b"opacity")
                fade_in.setDuration(300)  # 300ms fade-in
                fade_in.setStartValue(0.0)
                fade_in.setEndValue(1.0)
                fade_in.setEasingCurve(QEasingCurve.OutCubic)
                fade_in.start()

                # Store animation to prevent garbage collection
                button.setProperty("fade_animation", fade_in)
            else:
                button.setText("📷")  # No thumbnail - show placeholder
        except Exception as e:
            print(f"[GooglePhotosLayout] Error updating thumbnail for {path}: {e}")
            button.setText("❌")

    # PHASE 2 Task 2.1: Async photo loading handlers
    def _on_photos_loaded(self, generation: int, rows: list):
        """
        Callback when async photo database query completes.
        Only display results if generation matches (discard stale results).

        STALE-WHILE-REVALIDATE: Now clears old content only when new data arrives.
        """
        logger.info(f"Photo query complete: generation={generation}, current={self._photo_load_generation}, rows={len(rows)}")

        # Check if this is stale data
        if generation != self._photo_load_generation:
            logger.debug(f"Discarding stale photo query results (gen {generation} vs current {self._photo_load_generation})")
            return

        # Clear loading state
        self._photo_load_in_progress = False

        # Hide refresh indicator (stale-while-revalidate)
        self._hide_refresh_indicator()

        # Hide loading indicator
        try:
            if self._loading_indicator:
                self._loading_indicator.hide()
        except RuntimeError:
            pass  # Already deleted

        # STALE-WHILE-REVALIDATE: Clear old content NOW, right before displaying new
        self._clear_timeline_for_new_content()

        # Display photos in timeline
        self._display_photos_in_timeline(rows)

    def _on_photos_load_error(self, generation: int, error_msg: str):
        """
        Callback when async photo database query fails.
        """
        logger.error(f"Photo query error (gen {generation}): {error_msg}")

        # Only show error if this is the current generation
        if generation != self._photo_load_generation:
            return

        # Clear loading state
        self._photo_load_in_progress = False

        # Hide refresh indicator (stale-while-revalidate)
        self._hide_refresh_indicator()

        # Hide loading indicator
        try:
            if self._loading_indicator:
                self._loading_indicator.hide()
        except RuntimeError:
            pass  # Already deleted

        # Clear timeline for error display
        self._clear_timeline_for_new_content()

        # Show error in timeline
        error_label = QLabel(
            f"⚠️ Failed to load photos\n\n"
            f"Error: {error_msg}\n\n"
            f"Try:\n"
            f"• Click Refresh button\n"
            f"• Switch to Current layout and back\n"
            f"• Restart the application"
        )
        error_label.setAlignment(Qt.AlignCenter)
        error_label.setStyleSheet("font-size: 11pt; color: #d32f2f; padding: 40px;")
        self.timeline_layout.addWidget(error_label)

    # ── Paged-loading handlers ────────────────────────────────────────

    def _on_page_count_ready(self, generation: int, total: int):
        """Receives total count from the first PhotoPageWorker."""
        if generation != self._photo_load_generation:
            return
        self._paging_total = total
        logger.info(
            "[GoogleLayout] Page count ready: gen=%d total=%d (threshold=%d)",
            generation, total, self._small_threshold,
        )

    def _on_page_ready(self, generation: int, offset: int, rows: list):
        """
        Receives a page of rows from PhotoPageWorker.

        First page (offset==0): full timeline rebuild.
        Subsequent pages: incremental merge into existing date groups.

        STALE-WHILE-REVALIDATE: First page clears old content, subsequent pages merge.
        """
        if generation != self._photo_load_generation:
            logger.debug("[GoogleLayout] Discarding stale page gen=%d", generation)
            return

        self._paging_fetching = False

        # Convert list[dict] → list[tuple] for compatibility with display code
        tuples = [
            (r["path"], r["date_taken"], r.get("width", 0), r.get("height", 0))
            for r in rows
        ]

        page_count = len(tuples)
        self._paging_loaded += page_count
        self._paging_offset = offset + page_count

        logger.info(
            "[GoogleLayout] Page ready: gen=%d offset=%d rows=%d loaded=%d/%d",
            generation, offset, page_count, self._paging_loaded, self._paging_total,
        )

        if offset == 0:
            # First page → full timeline build
            # STALE-WHILE-REVALIDATE: Hide refresh indicator and clear old content NOW
            self._hide_refresh_indicator()
            self._clear_timeline_for_new_content()

            self._paging_all_rows = list(tuples)
            self._photo_load_in_progress = False
            try:
                if self._loading_indicator:
                    self._loading_indicator.hide()
            except RuntimeError:
                pass
            self._display_photos_in_timeline(tuples)
        else:
            # Subsequent pages → incremental merge (no clearing needed)
            self._paging_all_rows.extend(tuples)
            self._merge_page_into_timeline(tuples)

        # If we got fewer rows than page_size, we've reached the end
        all_loaded = page_count < self._page_size
        if all_loaded:
            self._paging_active = False
            logger.info(
                "[GoogleLayout] All pages loaded: %d rows total", self._paging_loaded,
            )
        else:
            # Auto-prefetch next pages if within prefetch window
            self._maybe_prefetch_next_page()

    def _on_page_error(self, generation: int, error_msg: str):
        """Handle paged loading error — delegates to existing error handler."""
        self._paging_fetching = False
        self._paging_active = False
        self._on_photos_load_error(generation, error_msg)

    def _merge_page_into_timeline(self, new_rows: list):
        """
        Incrementally add *new_rows* (tuples) into the existing timeline.

        Rows arrive sorted by date_taken DESC, so new rows may extend the
        last existing date group and/or introduce entirely new (older) groups.
        """
        if not new_rows:
            return

        new_groups = self._group_photos_by_date(new_rows)

        # Remove trailing stretch so we can append
        last_idx = self.timeline_layout.count() - 1
        if last_idx >= 0:
            last_item = self.timeline_layout.itemAt(last_idx)
            if last_item and last_item.spacerItem():
                self.timeline_layout.removeItem(last_item)

        # Track all displayed paths for selection
        for photos in new_groups.values():
            for p in photos:
                self.all_displayed_paths.append(p[0])

        # Update section count
        try:
            if hasattr(self, 'timeline_section'):
                self.timeline_section.update_count(len(self.all_displayed_paths))
        except Exception:
            pass

        for date_str, photos in new_groups.items():
            # Check if this date group already exists
            existing_idx = None
            for i, meta in enumerate(self.date_groups_metadata):
                if meta['date_str'] == date_str:
                    existing_idx = i
                    break

            if existing_idx is not None:
                # Extend existing date group
                self.date_groups_metadata[existing_idx]['photos'].extend(photos)
                widget = self.date_group_widgets.get(existing_idx)
                if widget and existing_idx in self.rendered_date_groups:
                    # Re-render the group with all photos
                    try:
                        new_widget = self._create_date_group(
                            date_str,
                            self.date_groups_metadata[existing_idx]['photos'],
                            self.current_thumb_size,
                        )
                        idx_in_layout = self.timeline_layout.indexOf(widget)
                        if idx_in_layout >= 0:
                            self.timeline_layout.removeWidget(widget)
                            widget.deleteLater()
                            self.timeline_layout.insertWidget(idx_in_layout, new_widget)
                        else:
                            self.timeline_layout.addWidget(new_widget)
                        self.date_group_widgets[existing_idx] = new_widget
                    except RuntimeError:
                        pass  # Widget deleted during re-render
            else:
                # New date group — append at end
                new_idx = len(self.date_groups_metadata)
                self.date_groups_metadata.append({
                    'index': new_idx,
                    'date_str': date_str,
                    'photos': photos,
                    'thumb_size': self.current_thumb_size,
                })

                if self.virtual_scroll_enabled and new_idx >= self.initial_render_count:
                    widget = self._create_date_group_placeholder(
                        self.date_groups_metadata[-1]
                    )
                else:
                    widget = self._create_date_group(
                        date_str, photos, self.current_thumb_size,
                    )
                    self.rendered_date_groups.add(new_idx)

                self.date_group_widgets[new_idx] = widget
                self.timeline_layout.addWidget(widget)

        # Re-add bottom stretch
        self.timeline_layout.addStretch()

        logger.debug(
            "[GoogleLayout] Merged %d rows into timeline (%d date groups now)",
            len(new_rows), len(self.date_groups_metadata),
        )

    def _maybe_prefetch_next_page(self):
        """Dispatch the next page if within the prefetch window."""
        if not self._paging_active or self._paging_fetching:
            return
        if self._paging_loaded >= self._max_in_memory:
            logger.info("[GoogleLayout] Max in-memory cap reached (%d)", self._max_in_memory)
            self._paging_active = False
            return

        from workers.photo_page_worker import PhotoPageWorker

        self._paging_fetching = True
        worker = PhotoPageWorker(
            project_id=self.project_id,
            generation=self._photo_load_generation,
            offset=self._paging_offset,
            limit=self._page_size,
            filters=self._paging_filters,
            signals=self._page_signals,
        )
        # Store reference to prevent premature GC (QRunnable safety)
        worker.setAutoDelete(False)
        self._page_worker = worker
        QThreadPool.globalInstance().start(worker)
        logger.debug(
            "[GoogleLayout] Prefetch page offset=%d", self._paging_offset,
        )

    # ── FIX #5 helpers: background grouping + chunked widget creation ──

    class _GroupingSignals(QObject):
        """Signals for the background grouping worker."""
        done = Signal(int, dict, list)  # (generation, photos_by_date, rows)

    class _GroupingWorker(QRunnable):
        """Runs _group_photos_by_date off the UI thread."""
        def __init__(self, rows, generation, group_fn, signals):
            super().__init__()
            self.setAutoDelete(True)
            self._rows = rows
            self._gen = generation
            self._group_fn = group_fn
            self._signals = signals

        def run(self):
            grouped = self._group_fn(self._rows)
            self._signals.done.emit(self._gen, grouped, self._rows)

    def _display_photos_in_timeline(self, rows: list):
        """
        PHASE 2 Task 2.1: Display photos in timeline after async query completes.

        FIX #5: Grouping runs in a background QRunnable, and widget creation
        is chunked via QTimer.singleShot(0, ...) to keep the UI responsive.

        Args:
            rows: List of (path, date_taken, width, height) tuples from database
        """
        if not rows:
            empty_widget = self._create_empty_state(
                icon="📷",
                title="No photos yet",
                message="Your photo collection is waiting to be filled!\n\nClick 'Scan Repository' to import photos.",
                action_text="or drag and drop photos here"
            )
            self.timeline_layout.addWidget(empty_widget)
            print(f"[GooglePhotosLayout] No photos found in project {self.project_id}")
            return

        print(f"[GooglePhotosLayout] Dispatching {len(rows)} assets (photos+videos) to background grouping worker...")

        # Bump generation so stale results are discarded
        gen = self._photo_load_generation

        if not hasattr(self, '_grouping_signals'):
            self._grouping_signals = self._GroupingSignals()
            self._grouping_signals.done.connect(self._on_grouping_done)

        worker = self._GroupingWorker(rows, gen, self._group_photos_by_date, self._grouping_signals)
        worker.setAutoDelete(False)
        self._grouping_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_grouping_done(self, generation: int, photos_by_date: dict, rows: list):
        """Slot: background grouping finished — build metadata, then chunk widgets."""
        if generation != self._photo_load_generation:
            print(f"[GooglePhotosLayout] Discarding stale grouping result (gen {generation})")
            return

        print(f"[GooglePhotosLayout] Grouped into {len(photos_by_date)} date groups")

        # Update section counts
        try:
            if hasattr(self, 'timeline_section'):
                self.timeline_section.update_count(len(rows))
            if hasattr(self, 'videos_section'):
                video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp'}
                video_count = sum(1 for (p, _, _, _) in rows if os.path.splitext(p)[1].lower() in video_exts)
                self.videos_section.update_count(video_count)
        except Exception:
            pass

        # Track all displayed paths for Shift+Ctrl multi-selection
        self.all_displayed_paths = [photo[0] for photos_list in photos_by_date.values() for photo in photos_list]
        print(f"[GooglePhotosLayout] Tracking {len(self.all_displayed_paths)} paths for multi-selection")

        # Build metadata list for virtual scrolling
        self.date_groups_metadata.clear()
        self.date_group_widgets.clear()
        self.rendered_date_groups.clear()

        for index, (date_str, photos) in enumerate(photos_by_date.items()):
            self.date_groups_metadata.append({
                'index': index,
                'date_str': date_str,
                'photos': photos,
                'thumb_size': self.current_thumb_size
            })

        # Chunk widget creation: process CHUNK_SIZE groups per event-loop tick
        self._widget_chunk_idx = 0
        self._widget_chunk_gen = generation
        self._widget_chunk_total = len(self.date_groups_metadata)
        self._widget_chunk_photos_by_date = photos_by_date
        self._widget_chunk_rows = rows
        self._process_widget_chunk()

    _WIDGET_CHUNK_SIZE = 30  # groups per tick — tune for responsiveness

    def _process_widget_chunk(self):
        """Create the next batch of date-group widgets, then yield to the event loop."""
        if self._widget_chunk_gen != self._photo_load_generation:
            return  # stale

        start = self._widget_chunk_idx
        end = min(start + self._WIDGET_CHUNK_SIZE, self._widget_chunk_total)

        for metadata in self.date_groups_metadata[start:end]:
            index = metadata['index']
            if self.virtual_scroll_enabled and index >= self.initial_render_count:
                widget = self._create_date_group_placeholder(metadata)
            else:
                widget = self._create_date_group(
                    metadata['date_str'],
                    metadata['photos'],
                    metadata['thumb_size']
                )
                self.rendered_date_groups.add(index)
            self.date_group_widgets[index] = widget
            self.timeline_layout.addWidget(widget)

        self._widget_chunk_idx = end

        if end < self._widget_chunk_total:
            # Yield to event loop, then continue
            QTimer.singleShot(0, self._process_widget_chunk)
        else:
            # All chunks done — finalize
            self._finalize_timeline_display()

    def _finalize_timeline_display(self):
        """Called after all widget chunks are created."""
        photos_by_date = self._widget_chunk_photos_by_date
        rows = self._widget_chunk_rows

        self.timeline_layout.addStretch()

        # Restore pending scroll position
        if self._pending_scroll_restore is not None:
            pct = self._pending_scroll_restore
            self._pending_scroll_restore = None
            QTimer.singleShot(0, lambda: self._restore_scroll_percentage(pct))

        if self.virtual_scroll_enabled:
            print(f"[GooglePhotosLayout] Virtual scrolling enabled: {len(photos_by_date)} total date groups")
            print(f"[GooglePhotosLayout] Rendered: {len(self.rendered_date_groups)} groups | Placeholders: {len(photos_by_date) - len(self.rendered_date_groups)} groups")
        else:
            print(f"[GooglePhotosLayout] Loaded {len(rows)} photos in {len(photos_by_date)} date groups")

        print(f"[GooglePhotosLayout] Queued {self.thumbnail_load_count} thumbnails for loading (initial limit: {self.initial_load_limit})")
        print(f"[GooglePhotosLayout] Photo loading complete! Thumbnails will load progressively.")

    def _on_timeline_scrolled(self):
        """
        QUICK WIN #5: Debounced scroll handler for smooth 60 FPS performance.
        PHASE 2 #4: Also shows date scroll indicator during scrolling.

        Instead of processing every scroll event (which can be hundreds per second),
        we restart a timer on each scroll. Only when scrolling stops (or slows down)
        for 150ms do we actually process the heavy operations.

        This prevents lag and dropped frames during fast scrolling.
        """
        # PHASE 2 #4: Update date scroll indicator (lightweight operation)
        self._update_date_scroll_indicator()

        # Restart debounce timer - will trigger _on_scroll_debounced() after 150ms of no scrolling
        self.scroll_debounce_timer.stop()
        self.scroll_debounce_timer.start(self.scroll_debounce_delay)

        # PHASE 2 #4: Restart hide timer - indicator will hide 800ms after scrolling stops
        if hasattr(self, 'date_indicator_hide_timer'):
            self.date_indicator_hide_timer.stop()
            self.date_indicator_hide_timer.start(self.date_indicator_delay)

    def _update_date_scroll_indicator(self):
        """
        PHASE 2 #4: Update floating date indicator with current visible date.

        Finds the topmost visible date group and shows its date in the indicator.
        Lightweight operation - just checks viewport position.
        """
        if not hasattr(self, 'date_scroll_indicator') or not hasattr(self, 'date_groups_metadata'):
            return

        try:
            # Get viewport
            viewport = self.timeline_scroll.viewport()
            viewport_rect = viewport.rect()
            viewport_top = viewport_rect.top()

            # Find first visible date group
            current_date = None
            for metadata in self.date_groups_metadata:
                widget = self.date_group_widgets.get(metadata['index'])
                if not widget:
                    continue

                # Check if widget is visible
                try:
                    widget_pos = widget.mapTo(viewport, widget.rect().topLeft())
                    # If widget's top is in viewport, this is the current date
                    if widget_pos.y() >= viewport_top - 100 and widget_pos.y() <= viewport_top + 200:
                        current_date = metadata['date_str']
                        break
                except:
                    continue

            if current_date:
                # Format date for indicator
                try:
                    date_obj = datetime.fromisoformat(current_date)
                    label = self._get_smart_date_label(date_obj)
                except:
                    label = current_date

                # Update and show indicator
                self.date_scroll_indicator.setText(label)
                self.date_scroll_indicator.adjustSize()

                # Position at top-right of viewport
                parent = self.date_scroll_indicator.parent()
                if parent:
                    x = parent.width() - self.date_scroll_indicator.width() - 20
                    y = 80  # Below toolbar
                    self.date_scroll_indicator.move(x, y)

                # PHASE 3 #1: Smooth slide-in animation from right if not already visible
                if not self.date_scroll_indicator.isVisible():
                    from PySide6.QtCore import QPropertyAnimation, QPoint, QEasingCurve

                    # Start position (off-screen to the right)
                    start_x = parent.width()
                    end_x = x

                    # Move to start position
                    self.date_scroll_indicator.move(start_x, y)
                    self.date_scroll_indicator.show()
                    self.date_scroll_indicator.raise_()

                    # Animate slide-in from right
                    slide_in = QPropertyAnimation(self.date_scroll_indicator, b"pos")
                    slide_in.setDuration(250)  # 250ms slide
                    slide_in.setStartValue(QPoint(start_x, y))
                    slide_in.setEndValue(QPoint(end_x, y))
                    slide_in.setEasingCurve(QEasingCurve.OutCubic)
                    slide_in.start()

                    # Store animation to prevent garbage collection
                    self.date_scroll_indicator.setProperty("slide_animation", slide_in)
                else:
                    # Already visible, just update position
                    self.date_scroll_indicator.show()
                    self.date_scroll_indicator.raise_()

        except Exception as e:
            pass  # Silently fail to avoid disrupting scrolling

    def _hide_date_indicator(self):
        """
        PHASE 2 #4: Hide date scroll indicator after scrolling stops.
        Phase 3 #1: Added smooth fade-out animation.
        """
        if hasattr(self, 'date_scroll_indicator') and self.date_scroll_indicator.isVisible():
            from PySide6.QtCore import QPropertyAnimation, QEasingCurve

            # Create opacity effect if not already present
            if not self.date_scroll_indicator.graphicsEffect():
                opacity_effect = QGraphicsOpacityEffect()
                self.date_scroll_indicator.setGraphicsEffect(opacity_effect)
                opacity_effect.setOpacity(1.0)

            opacity_effect = self.date_scroll_indicator.graphicsEffect()

            # Animate fade-out
            fade_out = QPropertyAnimation(opacity_effect, b"opacity")
            fade_out.setDuration(200)  # 200ms fade-out
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.InCubic)
            fade_out.finished.connect(self.date_scroll_indicator.hide)
            fade_out.start()

            # Store animation to prevent garbage collection
            self.date_scroll_indicator.setProperty("fade_animation", fade_out)

    def _on_scroll_debounced(self):
        """
        QUICK WIN #1, #3, #5: Process scroll events after debouncing.

        This is called 150ms after scrolling stops/slows down.

        Three functions:
        1. Load thumbnails that are now visible (Quick Win #1)
        2. Render date groups that entered viewport (Quick Win #3)
        3. Prefetch next page when near bottom of scroll area
        """
        # Get viewport rectangle
        viewport = self.timeline_scroll.viewport()
        viewport_rect = viewport.rect()

        # QUICK WIN #3: Virtual scrolling - render date groups that entered viewport
        if self.virtual_scroll_enabled and self.date_groups_metadata:
            self._render_visible_date_groups(viewport, viewport_rect)

        # Paged loading: prefetch when scrolled near bottom
        if self._paging_active and not self._paging_fetching:
            scroll_bar = self.timeline_scroll.verticalScrollBar()
            if scroll_bar and scroll_bar.maximum() > 0:
                remaining = scroll_bar.maximum() - scroll_bar.value()
                threshold = viewport_rect.height() * self._prefetch_pages
                if remaining < threshold:
                    self._maybe_prefetch_next_page()

        # QUICK WIN #1: Lazy thumbnail loading
        if not self.unloaded_thumbnails:
            return  # All thumbnails already loaded

        # QUICK WIN #5: Limit checks to prevent lag with huge libraries
        # Only check first 200 unloaded items per scroll event
        # This balances responsiveness vs performance
        max_checks = 200
        items_to_check = list(self.unloaded_thumbnails.items())[:max_checks]

        # Find and load visible thumbnails
        paths_to_load = []
        for path, (button, size) in items_to_check:
            # Check if button is visible in viewport
            try:
                # Map button position to viewport coordinates
                button_pos = button.mapTo(viewport, button.rect().topLeft())
                button_rect = button.rect()
                button_rect.moveTo(button_pos)

                # If button intersects viewport, it's visible
                if viewport_rect.intersects(button_rect):
                    paths_to_load.append(path)

            except Exception as e:
                # Button might have been deleted
                continue

        # Load visible thumbnails (with inflight guard)
        if paths_to_load:
            actually_queued = 0
            for path in paths_to_load:
                if path in self._thumb_inflight:
                    continue  # Already being loaded
                button, size = self.unloaded_thumbnails.pop(path, (None, None))
                if button is None:
                    continue
                self._thumb_inflight.add(path)
                loader = ThumbnailLoader(path, size, self.thumbnail_signals)
                self.thumbnail_thread_pool.start(loader)
                actually_queued += 1

            if actually_queued > 0:
                print(f"[GooglePhotosLayout] 📜 Queued {actually_queued} thumbnails, {len(self.unloaded_thumbnails)} remaining")

    def _create_thumbnail(self, path: str, size: int) -> QWidget:
        """
        Create thumbnail widget for a photo with selection checkbox.

        Phase 2: Enhanced with checkbox overlay for batch selection.
        Phase 2 #5: Support for different aspect ratios (square, original, 16:9).
        Phase 3: ASYNC thumbnail loading to prevent UI freeze with large photo sets.
        """
        from PySide6.QtWidgets import QCheckBox, QVBoxLayout

        # PHASE 2 #5: Calculate container size based on aspect ratio mode
        if self.thumbnail_aspect_ratio == "square":
            # Square thumbnails (default)
            container_width = size
            container_height = size
        elif self.thumbnail_aspect_ratio == "16:9":
            # Widescreen 16:9 aspect ratio
            container_width = size
            container_height = int(size * 9 / 16)  # ~56% of width
        else:  # "original"
            # Original aspect ratio - try to get image dimensions
            try:
                from PIL import Image
                with Image.open(path) as img:
                    img_width, img_height = img.size
                    # Calculate scaled dimensions maintaining aspect ratio
                    if img_width > img_height:
                        container_width = size
                        container_height = int(size * img_height / img_width)
                    else:
                        container_height = size
                        container_width = int(size * img_width / img_height)
            except Exception as e:
                # Fallback to square if we can't read the image
                print(f"[GooglePhotosLayout] Warning: Could not read image dimensions for {os.path.basename(path)}: {e}")
                container_width = size
                container_height = size

        # Container widget
        container = QWidget()
        container.setFixedSize(container_width, container_height)

        # VISUAL SELECTION ENHANCEMENT: Blue border for selected photos
        # This provides clear visual feedback before batch GPS operations
        # Border is applied/removed dynamically based on selection state
        container.setStyleSheet("background: transparent;")

        # Store container reference for border updates
        if not hasattr(self, 'thumbnail_containers'):
            self.thumbnail_containers = {}  # Map path -> container
        self.thumbnail_containers[path] = container

        # Thumbnail button with placeholder
        thumb = PhotoButton(path, self.project_id, container)  # Use custom PhotoButton
        thumb.setGeometry(0, 0, container_width, container_height)
        # QUICK WIN #8: Modern hover effects with smooth transitions
        # QUICK WIN #9: Skeleton loading state with gradient
        thumb.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #e8eaed, stop:0.5 #f1f3f4, stop:1 #e8eaed);
                border: 2px solid #dadce0;
                border-radius: 4px;
                color: #5f6368;
                font-size: 9pt;
            }
            QPushButton:hover {
                background: #ffffff;
                border-color: #1a73e8;
                border-width: 2px;
            }
        """)
        thumb.setCursor(Qt.PointingHandCursor)

        # PHASE 2 #8: Photo metadata tooltip (lightweight - no image loading)
        # PERFORMANCE FIX: Don't load QImage here - it's too expensive during initialization!
        try:
            filename = os.path.basename(path)
            stat = os.stat(path)
            file_size = stat.st_size / (1024 * 1024)  # MB
            tooltip = f"{filename}\nSize: {file_size:.2f} MB"
            thumb.setToolTip(tooltip)
        except:
            thumb.setToolTip(os.path.basename(path))

        # QUICK WIN #9: Skeleton loading indicator (subtle, professional)
        # PHASE 3 #2: Simple skeleton state without animation (performance fix)
        thumb.setText("⏳")

        # Store button for async update
        self.thumbnail_buttons[path] = thumb
        
        # Load and set tags for badge painting
        # ARCHITECTURE: Use TagService layer (Schema v3.1.0) instead of direct ReferenceDB calls
        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            tags_map = tag_service.get_tags_for_paths([path], self.project_id)
            tags = tags_map.get(path, [])  # Extract tags for this photo
            thumb.set_tags(tags)  # Set tags on PhotoButton for painting
        except Exception as e:
            print(f"[GooglePhotosLayout] Warning: Could not load tags for {os.path.basename(path)}: {e}")

        # QUICK WIN #1: Load first 50 immediately, rest on scroll
        # This removes the 30-photo limit while maintaining initial performance
        if self.thumbnail_load_count < self.initial_load_limit:
            self.thumbnail_load_count += 1
            # Queue async thumbnail loading with SHARED signal object
            loader = ThumbnailLoader(path, size, self.thumbnail_signals)
            self.thumbnail_thread_pool.start(loader)
        else:
            # Store for lazy loading on scroll
            self.unloaded_thumbnails[path] = (thumb, size)
            # NOTE: Removed verbose print that fired for every deferred thumbnail
            # This was causing performance issues with large photo collections

        # Phase 2: Selection checkbox (overlay top-left corner)
        # QUICK WIN #8: Enhanced with modern hover effects
        checkbox = QCheckBox(container)
        checkbox.setGeometry(8, 8, 24, 24)
        checkbox.setStyleSheet("""
            QCheckBox {
                background: rgba(255, 255, 255, 0.9);
                border: 2px solid #dadce0;
                border-radius: 4px;
                padding: 2px;
            }
            QCheckBox:hover {
                background: rgba(255, 255, 255, 1.0);
                border-color: #1a73e8;
            }
            QCheckBox:checked {
                background: #1a73e8;
                border-color: #1a73e8;
            }
            QCheckBox:checked:hover {
                background: #1557b0;
                border-color: #1557b0;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        checkbox.setCursor(Qt.PointingHandCursor)
        checkbox.setVisible(self.selection_mode)  # Only visible in selection mode

        # Store references
        container.setProperty("photo_path", path)
        container.setProperty("thumbnail_button", thumb)
        container.setProperty("checkbox", checkbox)

        # PHASE 2: Stack badge overlay (bottom-right corner)
        # Shows count of duplicates/similar photos in stack
        try:
            from layouts.google_components.stack_badge_widget import create_stack_badge
            from repository.stack_repository import StackRepository
            from repository.photo_repository import PhotoRepository
            from repository.base_repository import DatabaseConnection

            # Get photo ID to check for stack membership
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)
            photo = photo_repo.get_by_path(path, self.project_id)

            if photo:
                photo_id = photo.get('id')
                stack_repo = StackRepository(db_conn)

                # Check if this photo is in any stack
                stack = stack_repo.get_stack_by_photo_id(self.project_id, photo_id)

                if stack:
                    stack_id = stack['stack_id']
                    member_count = stack_repo.count_stack_members(self.project_id, stack_id)

                    # Create stack badge
                    stack_badge = create_stack_badge(member_count, stack_id, container)

                    # Connect click signal to open stack view
                    stack_badge.stack_clicked.connect(self._on_stack_badge_clicked)

                    # Store reference
                    container.setProperty("stack_badge", stack_badge)

                    logger.debug(f"Added stack badge to {os.path.basename(path)}: {member_count} members")
        except Exception as e:
            # Don't fail thumbnail creation if stack badge fails
            logger.warning(f"Failed to create stack badge for {os.path.basename(path)}: {e}")

        # PHASE 3: Duplicate badge overlay (bottom-left corner)
        # Shows count of duplicate copies (exact matches based on content hash)
        try:
            from layouts.google_components.duplicate_badge_widget import create_duplicate_badge
            from repository.asset_repository import AssetRepository
            from repository.photo_repository import PhotoRepository
            from repository.base_repository import DatabaseConnection

            # Get photo to check for duplicates
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)
            photo = photo_repo.get_by_path(path, self.project_id)

            if photo:
                photo_id = photo.get('id')
                asset_repo = AssetRepository(db_conn)

                # Check if this photo belongs to an asset with duplicates
                asset_id = asset_repo.get_asset_id_by_photo_id(self.project_id, photo_id)

                if asset_id:
                    instance_count = asset_repo.count_instances_for_asset(self.project_id, asset_id)

                    # Only show badge if there are duplicates (more than 1 instance)
                    if instance_count > 1:
                        # Create duplicate badge
                        dup_badge = create_duplicate_badge(instance_count, asset_id, container)

                        # Connect click signal to open duplicates dialog
                        dup_badge.duplicate_clicked.connect(self._on_duplicate_badge_clicked)

                        # Store reference
                        container.setProperty("duplicate_badge", dup_badge)

                        logger.debug(f"Added duplicate badge to {os.path.basename(path)}: {instance_count} copies")
        except Exception as e:
            # Don't fail thumbnail creation if duplicate badge fails
            logger.warning(f"Failed to create duplicate badge for {os.path.basename(path)}: {e}")

        # NOTE: Tag badges are now painted directly on PhotoButton, not as QLabel overlays

        # Connect signals
        thumb.clicked.connect(lambda: self._on_photo_clicked(path))
        checkbox.stateChanged.connect(lambda state: self._on_selection_changed(path, state))

        # PHASE 2 #1: Context menu on right-click
        thumb.setContextMenuPolicy(Qt.CustomContextMenu)
        thumb.customContextMenuRequested.connect(lambda pos: self._show_photo_context_menu(path, thumb.mapToGlobal(pos)))

        # VISUAL SELECTION ENHANCEMENT: Set initial border based on selection state
        # This ensures photos that are already selected show blue border immediately
        is_selected = path in self.selected_photos
        self._update_photo_border(path, is_selected)

        return container

    def _create_tag_badge_overlay(self, container: QWidget, path: str, container_width: int):
        """
        Create tag badge overlays for photo thumbnail (Google Photos + Current layout pattern).
        
        Displays stacked badges in top-right corner for:
        - ★ Favorite (gold)
        - 👤 Face (blue)
        - 🏷 Custom tags (gray)
        
        Args:
            container: Parent container widget
            path: Photo path
            container_width: Actual width of the container widget (for correct badge positioning)
        """
        try:
            from services.tag_service import get_tag_service

            # Query tags for this photo using proper service layer
            tag_service = get_tag_service()
            tags = tag_service.get_tags_for_path(path, self.project_id) or []

            # Log tag query result (debug level to avoid spam)
            logger.debug(f"Badge overlay for {os.path.basename(path)}: tags={tags}")

            if not tags:
                return  # No tags to display

            # PERFORMANCE FIX: Use cached settings instead of reading SettingsManager every time
            if not self._badge_settings['enabled']:
                return  # Badges disabled by user

            badge_size = self._badge_settings['size']
            max_badges = self._badge_settings['max_count']
            badge_margin = 4

            # Calculate badge positions (top-right corner, stacked vertically)
            x_right = container_width - badge_margin - badge_size
            y_top = badge_margin

            # PERFORMANCE FIX: Use class constant (not recreated on every call)
            badge_config = self.TAG_BADGE_CONFIG

            # Create badge labels
            badge_count = 0
            for tag in tags:
                tag_lower = str(tag).lower().strip()

                # Get badge config or use default
                if tag_lower in badge_config:
                    icon, bg_color, fg_color = badge_config[tag_lower]
                else:
                    # Default badge for custom tags
                    icon, bg_color, fg_color = self.DEFAULT_BADGE_CONFIG
                
                if badge_count >= max_badges:
                    break  # Max badges reached
                
                # Create badge label
                badge = QLabel(icon, container)
                badge.setFixedSize(badge_size, badge_size)
                badge.setAlignment(Qt.AlignCenter)
                badge.setStyleSheet(f"""
                    QLabel {{
                        background-color: rgba({bg_color.red()}, {bg_color.green()}, {bg_color.blue()}, {bg_color.alpha()});
                        color: {'black' if fg_color == Qt.black else 'white'};
                        border-radius: {badge_size // 2}px;
                        font-size: 11pt;
                        font-weight: bold;
                    }}
                """)
                
                # Position badge (stacked vertically)
                y_pos = y_top + (badge_count * (badge_size + 4))
                badge.move(x_right, y_pos)
                badge.setToolTip(tag)  # Show tag name on hover
                badge.show()  # Explicitly show the badge
                badge.raise_()  # Bring to front
                
                # Store reference for updates
                if not hasattr(container, '_tag_badges'):
                    container.setProperty('_tag_badges', [])
                badges_list = container.property('_tag_badges') or []
                badges_list.append(badge)
                container.setProperty('_tag_badges', badges_list)
                
                badge_count += 1
            
            # Show "+n" indicator if more tags exist
            if len(tags) > max_badges:
                overflow_badge = QLabel(f"+{len(tags) - max_badges}", container)
                overflow_badge.setFixedSize(badge_size, badge_size)
                overflow_badge.setAlignment(Qt.AlignCenter)
                overflow_badge.setStyleSheet(f"""
                    QLabel {{
                        background-color: rgba(60, 60, 60, 220);
                        color: white;
                        border-radius: {badge_size // 2}px;
                        font-size: 9pt;
                        font-weight: bold;
                    }}
                """)
                y_pos = y_top + (max_badges * (badge_size + 4))
                overflow_badge.move(x_right, y_pos)
                overflow_badge.setToolTip(f"{len(tags) - max_badges} more tags: {', '.join(tags[max_badges:])}")
                overflow_badge.show()  # Explicitly show the overflow badge
                overflow_badge.raise_()
                
            # Log badge creation (debug level to avoid spam)
            if badge_count > 0:
                logger.debug(f"Created {badge_count} tag badge(s) for {os.path.basename(path)}: {tags[:max_badges]}")

        except Exception as e:
            logger.error(f"Error creating tag badges for {os.path.basename(path)}: {e}", exc_info=True)

    def _on_photo_clicked(self, path: str):
        """
        Handle photo thumbnail click with Shift+Ctrl multi-selection support.

        - Normal click: Open lightbox
        - Ctrl+Click: Add/remove from selection (toggle)
        - Shift+Click: Range select from last selected to current
        - Selection mode: Toggle selection
        """
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt

        print(f"[GooglePhotosLayout] Photo clicked: {path}")

        # Get keyboard modifiers
        modifiers = QApplication.keyboardModifiers()
        ctrl_pressed = bool(modifiers & Qt.ControlModifier)
        shift_pressed = bool(modifiers & Qt.ShiftModifier)

        # SHIFT+CLICK: Range selection (from last selected to current)
        if shift_pressed and self.last_selected_path and self.all_displayed_paths:
            print(f"[GooglePhotosLayout] Shift+Click range selection from {self.last_selected_path} to {path}")
            try:
                # Find indices of last selected and current photo
                last_idx = self.all_displayed_paths.index(self.last_selected_path)
                current_idx = self.all_displayed_paths.index(path)

                # Select all photos in range
                start_idx = min(last_idx, current_idx)
                end_idx = max(last_idx, current_idx)

                for idx in range(start_idx, end_idx + 1):
                    range_path = self.all_displayed_paths[idx]
                    if range_path not in self.selected_photos:
                        self.selected_photos.add(range_path)
                        self._update_checkbox_state(range_path, True)
                        # VISUAL SELECTION ENHANCEMENT: Show blue border
                        self._update_photo_border(range_path, True)

                self._update_selection_ui()
                print(f"[GooglePhotosLayout] ✓ Range selected: {end_idx - start_idx + 1} photos")
                return

            except (ValueError, IndexError) as e:
                print(f"[GooglePhotosLayout] ⚠️ Range selection error: {e}")
                # Fall through to normal selection

        # CTRL+CLICK: Toggle selection (add/remove)
        if ctrl_pressed:
            print(f"[GooglePhotosLayout] Ctrl+Click toggle selection: {path}")
            self._toggle_photo_selection(path)
            self.last_selected_path = path  # Update last selected for future Shift+Click
            return

        # NORMAL CLICK in selection mode: Toggle selection
        if self.selection_mode:
            self._toggle_photo_selection(path)
            self.last_selected_path = path
        else:
            # NORMAL CLICK: Open lightbox/preview
            self._open_photo_lightbox(path)

    def _open_photo_lightbox(self, path: str):
        """
        Open media lightbox/preview dialog (supports both photos AND videos).

        Args:
            path: Path to photo or video to display
        """
        print(f"[GooglePhotosLayout] 👁️ Opening lightbox for: {path}")

        # Collect all media paths (photos + videos) in timeline order
        all_media = self._get_all_media_paths()

        if not all_media:
            print("[GooglePhotosLayout] ⚠️ No media to display in lightbox")
            return

        # Create and show lightbox dialog
        try:
            lightbox = MediaLightbox(
                path, all_media, parent=self.main_window,
                project_id=self.project_id,
            )
            lightbox.exec()
            print("[GooglePhotosLayout] MediaLightbox closed")
            
            # PHASE 3: Refresh tag overlays after lightbox closes
            # (user may have favorited/unfavorited in lightbox)
            self._refresh_tag_overlays([path])

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error opening lightbox: {e}")
            import traceback
            traceback.print_exc()

    def _get_all_media_paths(self) -> List[str]:
        """
        Get all media paths (photos + videos) in timeline order (newest to oldest).

        Returns:
            List of media paths
        """
        # Prefer the currently displayed context (branch/day/group)
        try:
            if hasattr(self, 'all_displayed_paths') and self.all_displayed_paths:
                return list(self.all_displayed_paths)
        except Exception:
            pass
        # Fallback: ask grid for visible paths if available
        try:
            grid = getattr(self, 'grid', None)
            if grid and hasattr(grid, 'get_visible_paths'):
                paths = grid.get_visible_paths()
                if paths:
                    return paths
        except Exception:
            pass

        all_paths = []

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Query all photos for current project, ordered by date
            photo_query = """
                SELECT DISTINCT pm.path
                FROM photo_metadata pm
                JOIN project_images pi ON pm.path = pi.image_path
                WHERE pi.project_id = ?
                AND pm.date_taken IS NOT NULL
                ORDER BY pm.date_taken DESC
            """

            # Query all videos for current project, ordered by date
            video_query = """
                SELECT DISTINCT path
                FROM video_metadata
                WHERE project_id = ?
                AND created_date IS NOT NULL
                ORDER BY created_date DESC
            """

            with db._connect() as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                cur = conn.cursor()

                # Get photos
                cur.execute(photo_query, (self.project_id,))
                photo_rows = cur.fetchall()
                photo_paths = [row[0] for row in photo_rows]

                # Get videos
                cur.execute(video_query, (self.project_id,))
                video_rows = cur.fetchall()
                video_paths = [row[0] for row in video_rows]

                # Combine and sort by date (already sorted individually, merge them)
                # For now, just append videos after photos (both are sorted by date desc)
                # TODO: Could merge-sort by actual date if needed
                all_paths = photo_paths + video_paths

                print(f"[GooglePhotosLayout] Found {len(photo_paths)} photos + {len(video_paths)} videos = {len(all_paths)} total media")

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error fetching media paths: {e}")

        return all_paths

    def _on_selection_changed(self, path: str, state: int):
        """
        Handle checkbox selection change.

        Args:
            path: Photo path
            state: Qt.CheckState (0=unchecked, 2=checked)
        """
        from PySide6.QtCore import Qt

        if state == Qt.Checked:
            self.selected_photos.add(path)
            # VISUAL SELECTION ENHANCEMENT: Show blue border
            self._update_photo_border(path, True)
            print(f"[GooglePhotosLayout] ✓ Selected: {path}")
        else:
            self.selected_photos.discard(path)
            # VISUAL SELECTION ENHANCEMENT: Remove blue border
            self._update_photo_border(path, False)
            print(f"[GooglePhotosLayout] ✗ Deselected: {path}")

        # Update selection counter and action buttons
        self._update_selection_ui()

    def _toggle_photo_selection(self, path: str):
        """
        Toggle photo selection and update checkbox.
        """
        # Find checkbox for this photo
        container = self._find_thumbnail_container(path)
        if container:
            checkbox = container.property("checkbox")
            if checkbox:
                # Toggle checkbox (will trigger _on_selection_changed)
                checkbox.setChecked(not checkbox.isChecked())

    def _find_thumbnail_container(self, path: str) -> QWidget:
        """
        Find thumbnail container widget by photo path.
        """
        # Iterate through all date groups to find the thumbnail
        for i in range(self.timeline_layout.count()):
            date_group = self.timeline_layout.itemAt(i).widget()
            if not date_group:
                continue

            # Find grid inside date group
            group_layout = date_group.layout()
            if not group_layout:
                continue

            for j in range(group_layout.count()):
                item = group_layout.itemAt(j)
                if not item or not item.widget():
                    continue

                widget = item.widget()
                if hasattr(widget, 'layout') and widget.layout():
                    # This is a grid container
                    grid = widget.layout()
                    for k in range(grid.count()):
                        container = grid.itemAt(k).widget()
                        if container and container.property("photo_path") == path:
                            return container

        return None

    def _update_checkbox_state(self, path: str, checked: bool):
        """
        Update checkbox state for a specific photo (for multi-selection support).

        Args:
            path: Photo path
            checked: True to check, False to uncheck
        """
        container = self._find_thumbnail_container(path)
        if container:
            checkbox = container.property("checkbox")
            if checkbox:
                # Update checkbox state without triggering signal
                checkbox.blockSignals(True)
                checkbox.setChecked(checked)
                checkbox.blockSignals(False)

    def _update_photo_border(self, path: str, selected: bool):
        """
        VISUAL SELECTION ENHANCEMENT: Update blue border for photo based on selection state.

        This provides clear visual feedback showing which photos are selected,
        especially important before batch GPS operations where users need to
        verify their selection visually.

        Args:
            path: Photo path
            selected: True to show blue border, False to remove border

        Visual Design:
            Selected: 3px solid blue border (#1a73e8 - Google Blue)
            Unselected: 2px solid gray border (#dadce0 - default)
        """
        if not hasattr(self, 'thumbnail_containers'):
            return

        container = self.thumbnail_containers.get(path)
        if not container:
            return

        # Get the PhotoButton from the container (stored as property)
        thumb = container.property("thumbnail_button")
        if not thumb:
            return

        if selected:
            # Apply prominent blue border (Google Photos pattern)
            # FIX: Apply to PhotoButton, not container, since PhotoButton covers container
            thumb.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #e8eaed, stop:0.5 #f1f3f4, stop:1 #e8eaed);
                    border: 3px solid #1a73e8;
                    border-radius: 4px;
                    color: #5f6368;
                    font-size: 9pt;
                }
                QPushButton:hover {
                    background: #ffffff;
                    border-color: #1a73e8;
                    border-width: 3px;
                }
            """)
        else:
            # Restore default border (2px gray)
            thumb.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #e8eaed, stop:0.5 #f1f3f4, stop:1 #e8eaed);
                    border: 2px solid #dadce0;
                    border-radius: 4px;
                    color: #5f6368;
                    font-size: 9pt;
                }
                QPushButton:hover {
                    background: #ffffff;
                    border-color: #1a73e8;
                    border-width: 2px;
                }
            """)

    def _update_selection_ui(self):
        """
        Update selection counter and show/hide action buttons.

        QUICK WIN #6: Now also controls floating toolbar.
        """
        count = len(self.selected_photos)

        # Update toolbar selection counter (add if doesn't exist)
        if not hasattr(self, 'selection_label'):
            from PySide6.QtWidgets import QLabel
            self.selection_label = QLabel()
            self.selection_label.setStyleSheet("font-weight: bold; padding: 0 12px;")
            # Insert selection label in toolbar (after existing actions)
            toolbar = self._toolbar
            # Simply add to toolbar without complex index logic
            toolbar.addWidget(self.selection_label)

        # Update counter text
        if count > 0:
            self.selection_label.setText(f"✓ {count} selected")
            self.selection_label.setVisible(True)

            # Show action buttons
            self.btn_delete.setVisible(True)
            self.btn_favorite.setVisible(True)
            self.btn_share.setVisible(True)  # PHASE 3 #7: Show share button

            # QUICK WIN #6: Show and update floating toolbar
            if hasattr(self, 'floating_toolbar') and hasattr(self, 'selection_count_label'):
                self.selection_count_label.setText(f"{count} selected")
                self._position_floating_toolbar()
                self.floating_toolbar.show()
                self.floating_toolbar.raise_()  # Bring to front
        else:
            self.selection_label.setVisible(False)

            # Hide action buttons when nothing selected
            self.btn_delete.setVisible(False)
            self.btn_favorite.setVisible(False)
            self.btn_share.setVisible(False)  # PHASE 3 #7: Hide share button

            # QUICK WIN #6: Hide floating toolbar when no selection
            if hasattr(self, 'floating_toolbar'):
                self.floating_toolbar.hide()

        print(f"[GooglePhotosLayout] Selection updated: {count} photos selected")

    def _position_floating_toolbar(self):
        """
        QUICK WIN #6: Position floating toolbar at bottom center of viewport.
        """
        if not hasattr(self, 'floating_toolbar'):
            return

        # Get parent widget size
        parent = self.floating_toolbar.parent()
        if not parent:
            return

        parent_width = parent.width()
        parent_height = parent.height()

        toolbar_width = self.floating_toolbar.width()
        toolbar_height = self.floating_toolbar.height()

        # Position at bottom center
        x = (parent_width - toolbar_width) // 2
        y = parent_height - toolbar_height - 20  # 20px from bottom

        self.floating_toolbar.move(x, y)

    def _on_select_all(self):
        """
        QUICK WIN #6: Select all visible photos.
        """
        # Select all displayed photos
        for path in self.all_displayed_paths:
            if path not in self.selected_photos:
                self.selected_photos.add(path)
                self._update_checkbox_state(path, True)
                # VISUAL SELECTION ENHANCEMENT: Show blue border
                self._update_photo_border(path, True)

        self._update_selection_ui()
        print(f"[GooglePhotosLayout] ✓ Selected all {len(self.selected_photos)} photos")

    def _on_clear_selection(self):
        """
        QUICK WIN #6: Clear all selected photos.
        """
        # Deselect all photos
        for path in list(self.selected_photos):
            self._update_checkbox_state(path, False)
            # VISUAL SELECTION ENHANCEMENT: Remove blue border
            self._update_photo_border(path, False)

        self.selected_photos.clear()
        self._update_selection_ui()
        print("[GooglePhotosLayout] ✗ Cleared all selections")

    def _on_copy_gps_from_toolbar(self):
        """
        GPS-FOCUSED WORKFLOW: Copy GPS location from first selected photo.

        This provides quick access to Copy Location feature from the floating toolbar,
        making GPS workflow more discoverable and efficient.
        """
        from PySide6.QtWidgets import QMessageBox

        if not self.selected_photos:
            QMessageBox.information(
                self.main_window,
                "No Photos Selected",
                "Please select a photo with GPS data to copy its location."
            )
            return

        # Get first selected photo
        first_photo = next(iter(self.selected_photos))

        # Use existing _copy_location method
        self._copy_location(first_photo)

        print(f"[GooglePhotosLayout] 📍 Copied GPS from {os.path.basename(first_photo)} via toolbar")

    def _on_invert_selection(self):
        """
        DESELECTION WORKFLOW: Invert current selection.

        Selects all unselected photos and deselects all selected photos.
        Useful for "select all except these" workflows.

        Example:
        - User clicks 3 photos they DON'T want
        - User clicks "Invert" → All photos selected EXCEPT those 3
        """
        if not self.all_displayed_paths:
            print("[GooglePhotosLayout] ⚠️ No photos to invert selection")
            return

        # Get set of all displayed photos
        all_photos = set(self.all_displayed_paths)

        # Calculate inverse: all photos EXCEPT currently selected
        new_selection = all_photos - self.selected_photos

        # Deselect currently selected
        for path in list(self.selected_photos):
            self._update_checkbox_state(path, False)

        # Select the inverse
        self.selected_photos = new_selection.copy()
        for path in new_selection:
            self._update_checkbox_state(path, True)

        self._update_selection_ui()
        print(f"[GooglePhotosLayout] ⇄ Inverted selection: {len(new_selection)} photos now selected")

    def _show_photo_context_menu(self, path: str, global_pos):
        """
        Show comprehensive context menu for photo thumbnail (right-click).

        MERGED IMPLEMENTATION: Combines tag operations (using TagService) with
        file operations (Open, Delete, Properties, etc.)

        Actions available:
        - Open: View in lightbox
        - Checkable common tags (favorite, face, important, etc.)
        - New Tag/Remove All Tags
        - Select/Deselect: Toggle selection
        - Delete: Remove photo
        - Show in Explorer: Open file location
        - Copy Path: Copy file path to clipboard
        - Properties: Show photo details

        Args:
            path: Photo file path
            global_pos: Global position for menu

        Fixes:
        - Uses TagService instead of ReferenceDB (proper architecture)
        - Merges duplicate implementations (was also at line 15465)
        - Provides comprehensive functionality in single method
        """
        from PySide6.QtWidgets import QMenu, QMessageBox
        from PySide6.QtGui import QAction

        try:
            # Get current tags using proper service layer (not ReferenceDB)
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            current_tags = [t.lower() for t in (tag_service.get_tags_for_path(path, self.project_id) or [])]

            menu = QMenu(self.main_window)
            menu.setStyleSheet("""
                QMenu {
                    background: white;
                    border: 1px solid #dadce0;
                    border-radius: 4px;
                    padding: 4px;
                }
                QMenu::item {
                    padding: 6px 24px 6px 12px;
                    border-radius: 2px;
                }
                QMenu::item:selected {
                    background: #f1f3f4;
                }
                QMenu::separator {
                    height: 1px;
                    background: #e8eaed;
                    margin: 4px 0;
                }
            """)

            # Open action
            open_action = QAction("📂 Open", parent=menu)
            open_action.triggered.connect(lambda: self._on_photo_clicked(path))
            menu.addAction(open_action)

            menu.addSeparator()

            # Common tags (checkable items show ✓ when present)
            common_tags = [
                ("favorite", "⭐ Favorite"),
                ("face", "👤 Face"),
                ("important", "⚑ Important"),
                ("work", "💼 Work"),
                ("travel", "✈ Travel"),
                ("personal", "♥ Personal"),
                ("family", "👨‍👩‍👧 Family"),
                ("archive", "📦 Archive"),
            ]
            tag_actions = {}
            for key, label in common_tags:
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(key in current_tags)
                tag_actions[act] = key

            menu.addSeparator()

            # Tag management actions
            act_new_tag = menu.addAction("🏷️ New Tag…")
            act_remove_all_tags = menu.addAction("🗑️ Remove All Tags")

            menu.addSeparator()

            # Select/Deselect toggle
            is_selected = path in self.selected_photos
            if is_selected:
                select_action = QAction("✓ Deselect", parent=menu)
                select_action.triggered.connect(lambda: self._toggle_photo_selection(path))
            else:
                select_action = QAction("☐ Select", parent=menu)
                select_action.triggered.connect(lambda: self._toggle_photo_selection(path))
            menu.addAction(select_action)

            menu.addSeparator()

            # Delete photo action
            delete_action = QAction("🗑️ Delete Photo", parent=menu)
            delete_action.triggered.connect(lambda: self._delete_single_photo(path))
            menu.addAction(delete_action)

            menu.addSeparator()

            # File operations
            explorer_action = QAction("📁 Show in Explorer", parent=menu)
            explorer_action.triggered.connect(lambda: self._show_in_explorer(path))
            menu.addAction(explorer_action)

            copy_action = QAction("📋 Copy Path", parent=menu)
            copy_action.triggered.connect(lambda: self._copy_path_to_clipboard(path))
            menu.addAction(copy_action)

            # Edit Location action (manual GPS editing)
            # Support batch editing when multiple photos are selected
            selected_count = len(self.selected_photos)
            if selected_count > 1:
                # Show both batch and single edit options for clarity
                batch_action = QAction(f"📍 Edit Location ({selected_count} selected photos)...", parent=menu)
                batch_action.triggered.connect(lambda: self._edit_photos_location_batch(list(self.selected_photos)))
                menu.addAction(batch_action)

                single_action = QAction(f"📍 Edit Location (this photo only)...", parent=menu)
                single_action.triggered.connect(lambda: self._edit_photo_location(path))
                menu.addAction(single_action)
            else:
                # Single photo mode
                edit_location_action = QAction("📍 Edit Location...", parent=menu)
                edit_location_action.triggered.connect(lambda: self._edit_photo_location(path))
                menu.addAction(edit_location_action)

            # Copy/Paste Location actions (inspired by Google Photos & iPhone Photos)
            copy_location_action = QAction("📍 Copy Location", parent=menu)
            copy_location_action.triggered.connect(lambda: self._copy_location(path))
            menu.addAction(copy_location_action)

            if self.copied_gps_location:
                # Show paste option only if we have copied GPS data
                paste_text = f"📍 Paste Location ({self.copied_gps_location.get('location_name', 'Location')})"
                if selected_count > 1:
                    paste_text = f"📍 Paste Location to {selected_count} photos ({self.copied_gps_location.get('location_name', 'Location')})"
                paste_location_action = QAction(paste_text, parent=menu)
                paste_location_action.triggered.connect(lambda: self._paste_location(list(self.selected_photos) if selected_count > 1 else [path]))
                menu.addAction(paste_location_action)

            menu.addSeparator()

            # Edit Metadata action - opens metadata editor dock for this photo
            edit_metadata_action = QAction("✏️ Edit Metadata", parent=menu)
            edit_metadata_action.triggered.connect(lambda: self._show_metadata_editor_for_photo(path))
            menu.addAction(edit_metadata_action)

            # Properties action
            properties_action = QAction("ℹ️ Properties", parent=menu)
            properties_action.triggered.connect(lambda: self._show_photo_properties(path))
            menu.addAction(properties_action)

            # Show menu and handle selection
            chosen = menu.exec(global_pos)
            if not chosen:
                return

            # Handle tag actions
            if chosen is act_new_tag:
                self._add_tag_to_photo(path)
                return

            if chosen is act_remove_all_tags:
                # Remove all tags from this photo
                for tag_name in list(current_tags):
                    try:
                        tag_service.remove_tag(path, tag_name, self.project_id)
                    except Exception as e:
                        print(f"[GooglePhotosLayout] ⚠️ Failed to remove tag '{tag_name}': {e}")
                # Refresh overlays and tags section
                self._refresh_tag_overlays([path])
                try:
                    self._build_tags_tree()
                except Exception:
                    pass
                return

            # Handle checkable tag toggle
            tag_key = tag_actions.get(chosen)
            if tag_key:
                if tag_key in current_tags:
                    tag_service.remove_tag(path, tag_key, self.project_id)
                else:
                    tag_service.assign_tags_bulk([path], tag_key, self.project_id)
                # Refresh overlays and tags section
                self._refresh_tag_overlays([path])
                try:
                    self._build_tags_tree()
                except Exception:
                    pass

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Context menu error: {e}")
            import traceback
            traceback.print_exc()

    def _delete_single_photo(self, path: str):
        """Delete a single photo (context menu action)."""
        # Add to selection temporarily
        was_selected = path in self.selected_photos
        if not was_selected:
            self.selected_photos.add(path)

        # Call existing delete handler
        self._on_delete_selected()

        # Remove from selection if it wasn't originally selected
        if not was_selected:
            self.selected_photos.discard(path)
            self._update_selection_ui()

    def _show_in_explorer(self, path: str):
        """Open file location in system file explorer."""
        import subprocess
        import platform

        try:
            system = platform.system()
            if system == "Windows":
                subprocess.run(['explorer', '/select,', os.path.normpath(path)])
            elif system == "Darwin":  # macOS
                subprocess.run(['open', '-R', path])
            else:  # Linux
                subprocess.run(['xdg-open', os.path.dirname(path)])

            print(f"[GooglePhotosLayout] 📁 Opened location: {path}")
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error opening location: {e}")

    def _copy_path_to_clipboard(self, path: str):
        """Copy file path to clipboard."""
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        clipboard.setText(path)
        print(f"[GooglePhotosLayout] 📋 Copied to clipboard: {path}")

    def _edit_photo_location(self, path: str):
        """Edit GPS location for a photo (manual location editing)."""
        from PySide6.QtWidgets import QMessageBox

        print(f"[GooglePhotosLayout] 📍 Opening location editor for: {path}")

        try:
            from ui.location_editor_integration import edit_photo_location

            # Show location editor dialog
            location_changed = edit_photo_location(path, parent=self.main_window)

            # If location was changed, refresh the Locations section
            if location_changed:
                print(f"[GooglePhotosLayout] ✓ Location updated for {os.path.basename(path)}")

                # Reload Locations section in accordion sidebar
                try:
                    if hasattr(self, 'accordion_sidebar'):
                        print("[GooglePhotosLayout] Reloading Locations section...")
                        self.accordion_sidebar.reload_section("locations")
                    else:
                        print("[GooglePhotosLayout] Warning: No accordion_sidebar reference")
                except Exception as e:
                    print(f"[GooglePhotosLayout] Warning: Failed to reload Locations section: {e}")

                # Also refresh photo properties if properties panel is open
                # (so GPS info updates immediately)

        except ImportError as e:
            QMessageBox.critical(
                self.main_window,
                "Import Error",
                f"Failed to load location editor:\n{e}\n\nPlease ensure ui/location_editor_integration.py exists."
            )
        except Exception as e:
            print(f"[GooglePhotosLayout] Error opening location editor: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self.main_window,
                "Error",
                f"Failed to open location editor:\n{e}"
            )

    def _edit_photos_location_batch(self, paths: list[str]):
        """
        Edit GPS location for multiple photos (batch editing).

        Enhanced with better UX:
        - Confirmation dialog before opening editor
        - Shows photo count and filenames preview
        - Success feedback with count
        """
        from PySide6.QtWidgets import QMessageBox

        if not paths:
            QMessageBox.warning(
                self.main_window,
                "No Photos Selected",
                "Please select one or more photos to edit location."
            )
            return

        print(f"[GooglePhotosLayout] 📍 Opening batch location editor for {len(paths)} photos")

        # Show confirmation dialog with preview
        photo_count = len(paths)
        photo_names_preview = "\n".join([os.path.basename(p) for p in paths[:5]])
        if photo_count > 5:
            photo_names_preview += f"\n... and {photo_count - 5} more"

        confirm = QMessageBox.question(
            self.main_window,
            "Batch Edit Location",
            f"Edit GPS location for {photo_count} photo(s)?\n\n"
            f"Photos:\n{photo_names_preview}\n\n"
            f"The same location will be applied to all selected photos.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if confirm != QMessageBox.Yes:
            print("[GooglePhotosLayout] Batch location edit cancelled by user")
            return

        try:
            from ui.location_editor_integration import edit_photos_location_batch

            # Show batch location editor dialog
            location_changed = edit_photos_location_batch(paths, parent=self.main_window)

            # If location was changed, refresh the Locations section
            if location_changed:
                print(f"[GooglePhotosLayout] ✓ Location updated for {len(paths)} photos")

                # Show success message
                QMessageBox.information(
                    self.main_window,
                    "Success",
                    f"✓ GPS location updated for {photo_count} photo(s)!\n\n"
                    f"Location data saved to both database and photo files."
                )

                # Reload Locations section in accordion sidebar
                try:
                    if hasattr(self, 'accordion_sidebar'):
                        print("[GooglePhotosLayout] Reloading Locations section...")
                        self.accordion_sidebar.reload_section("locations")
                    else:
                        print("[GooglePhotosLayout] Warning: No accordion_sidebar reference")
                except Exception as e:
                    print(f"[GooglePhotosLayout] Warning: Failed to reload Locations section: {e}")

        except ImportError as e:
            QMessageBox.critical(
                self.main_window,
                "Import Error",
                f"Failed to load location editor:\n{e}\n\nPlease ensure ui/location_editor_integration.py exists."
            )
        except Exception as e:
            print(f"[GooglePhotosLayout] Error opening batch location editor: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self.main_window,
                "Error",
                f"Failed to open batch location editor:\n{e}"
            )

    def _copy_location(self, path: str):
        """
        Copy GPS location from photo to internal clipboard.

        Inspired by Google Photos and iPhone Photos copy/paste workflow.
        This allows quick reuse of GPS data across multiple photos without
        needing to search or type coordinates repeatedly.

        Args:
            path: Photo file path to copy GPS from
        """
        from PySide6.QtWidgets import QMessageBox
        from reference_db import ReferenceDB

        try:
            # Read GPS data from database
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT gps_latitude, gps_longitude, location_name
                    FROM photo_metadata
                    WHERE path = ? AND project_id = ?
                """, (path, self.project_id))

                row = cur.fetchone()

            if not row:
                QMessageBox.warning(
                    self.main_window,
                    "No GPS Data",
                    f"Photo '{os.path.basename(path)}' has no GPS location data.\n\n"
                    "Use 'Edit Location...' to add GPS coordinates first."
                )
                return

            lat, lon, location_name = row

            if lat is None or lon is None:
                QMessageBox.warning(
                    self.main_window,
                    "No GPS Data",
                    f"Photo '{os.path.basename(path)}' has no GPS location data.\n\n"
                    "Use 'Edit Location...' to add GPS coordinates first."
                )
                return

            # Store in internal clipboard
            self.copied_gps_location = {
                'lat': lat,
                'lon': lon,
                'location_name': location_name or f"({lat:.4f}, {lon:.4f})"
            }

            # Show success message
            location_display = location_name if location_name else f"({lat:.4f}, {lon:.4f})"
            QMessageBox.information(
                self.main_window,
                "Location Copied",
                f"✓ Copied GPS location:\n\n{location_display}\n\n"
                f"Use 'Paste Location' to apply this location to other photos."
            )

            print(f"[GooglePhotosLayout] ✓ Copied GPS location: {location_display} ({lat:.6f}, {lon:.6f})")

        except Exception as e:
            print(f"[GooglePhotosLayout] Error copying location: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self.main_window,
                "Error",
                f"Failed to copy location:\n{e}"
            )

    def _paste_location(self, paths: list[str]):
        """
        Paste copied GPS location to photo(s).

        Inspired by Google Photos and iPhone Photos copy/paste workflow.
        Applies the copied GPS data to selected photo(s) in a single action.

        Args:
            paths: List of photo file paths to paste GPS to
        """
        from PySide6.QtWidgets import QMessageBox

        if not self.copied_gps_location:
            QMessageBox.warning(
                self.main_window,
                "No Location Copied",
                "No GPS location has been copied.\n\n"
                "Use 'Copy Location' on a photo with GPS data first."
            )
            return

        if not paths:
            QMessageBox.warning(
                self.main_window,
                "No Photos Selected",
                "Please select one or more photos to paste the location to."
            )
            return

        try:
            lat = self.copied_gps_location['lat']
            lon = self.copied_gps_location['lon']
            location_name = self.copied_gps_location['location_name']

            # Confirm with user
            photo_word = "photo" if len(paths) == 1 else f"{len(paths)} photos"
            confirm = QMessageBox.question(
                self.main_window,
                "Paste Location",
                f"Paste GPS location to {photo_word}?\n\n"
                f"Location: {location_name}\n"
                f"Coordinates: ({lat:.6f}, {lon:.6f})\n\n"
                f"This will update GPS data in both the database and photo file EXIF.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if confirm != QMessageBox.Yes:
                return

            # Apply GPS to photo(s) using existing integration layer
            from ui.location_editor_integration import save_photo_location

            success_count = 0
            fail_count = 0

            for photo_path in paths:
                try:
                    # Save location using integration layer (handles both DB and EXIF)
                    save_photo_location(
                        photo_path=photo_path,
                        latitude=lat,
                        longitude=lon,
                        location_name=location_name
                    )
                    success_count += 1
                    print(f"[GooglePhotosLayout] ✓ Pasted location to: {os.path.basename(photo_path)}")

                except Exception as e:
                    fail_count += 1
                    print(f"[GooglePhotosLayout] ✗ Failed to paste location to {os.path.basename(photo_path)}: {e}")

            # Show results
            if fail_count == 0:
                QMessageBox.information(
                    self.main_window,
                    "Success",
                    f"✓ GPS location pasted to {success_count} {photo_word}!\n\n"
                    f"Location: {location_name}"
                )
            else:
                QMessageBox.warning(
                    self.main_window,
                    "Partially Complete",
                    f"GPS location pasted to {success_count} photo(s).\n"
                    f"Failed: {fail_count} photo(s).\n\n"
                    f"Check logs for details."
                )

            # Refresh Locations section
            try:
                if hasattr(self, 'accordion_sidebar'):
                    print("[GooglePhotosLayout] Reloading Locations section...")
                    self.accordion_sidebar.reload_section("locations")
            except Exception as e:
                print(f"[GooglePhotosLayout] Warning: Failed to reload Locations section: {e}")

            print(f"[GooglePhotosLayout] ✓ Paste complete: {success_count} success, {fail_count} failures")

        except Exception as e:
            print(f"[GooglePhotosLayout] Error pasting location: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self.main_window,
                "Error",
                f"Failed to paste location:\n{e}"
            )

    def _show_photo_properties(self, path: str):
        """Show photo properties dialog with EXIF data."""
        from PySide6.QtWidgets import QMessageBox

        try:
            # Get file info
            stat = os.stat(path)
            file_size = stat.st_size / (1024 * 1024)  # MB

            # Get image dimensions WITHOUT loading full image into RAM
            try:
                from PySide6.QtGui import QImageReader
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid():
                    dimensions = f"{size.width()} × {size.height()}px"
                else:
                    dimensions = "Unknown"
            except:
                dimensions = "Unknown"

            # Format info
            info = f"""
File: {os.path.basename(path)}
Path: {path}

Size: {file_size:.2f} MB
Dimensions: {dimensions}

Modified: {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}
            """.strip()

            QMessageBox.information(None, "Photo Properties", info)
            print(f"[GooglePhotosLayout] ℹ️ Showing properties: {path}")
        except Exception as e:
            QMessageBox.warning(None, "Error", f"Could not load properties:\n{e}")
            print(f"[GooglePhotosLayout] ⚠️ Error showing properties: {e}")

    def keyPressEvent(self, event: QKeyEvent):
        """
        Phase 0.1: Keyboard Shortcuts Foundation (Google Photos + Lightroom patterns).

        Shortcuts:
        - Ctrl+A: Select all photos
        - Ctrl+D: Deselect all photos
        - Escape: Clear selection/filter
        - Delete: Delete selected photos
        - Ctrl+F: Focus search box
        - Ctrl+N: New project
        - Enter: Open first selected photo in lightbox
        - Space: Quick preview (full screen)
        - S: Toggle selection mode
        - +/-: Zoom in/out thumbnail size

        Args:
            event: QKeyEvent
        """
        key = event.key()
        modifiers = event.modifiers()

        # Ctrl+A: Select All
        if key == Qt.Key_A and modifiers == Qt.ControlModifier:
            print("[GooglePhotosLayout] ⌨️ Ctrl+A - Select all")
            self._on_select_all()
            event.accept()

        # Ctrl+D: Deselect All
        elif key == Qt.Key_D and modifiers == Qt.ControlModifier:
            if len(self.selected_photos) > 0:
                print("[GooglePhotosLayout] ⌨️ Ctrl+D - Deselect all")
                self._on_clear_selection()
                event.accept()
            else:
                super().keyPressEvent(event)

        # Escape: Clear selection/filter
        elif key == Qt.Key_Escape:
            if len(self.selected_photos) > 0:
                print("[GooglePhotosLayout] ⌨️ ESC - Clear selection")
                self._on_clear_selection()
                event.accept()
            elif hasattr(self, 'active_person_filter') and self.active_person_filter:
                print("[GooglePhotosLayout] ⌨️ ESC - Clear person filter")
                self._clear_filter()
                event.accept()
            else:
                super().keyPressEvent(event)

        # Delete: Delete selected photos
        elif key == Qt.Key_Delete:
            if len(self.selected_photos) > 0:
                print(f"[GooglePhotosLayout] ⌨️ DELETE - Delete {len(self.selected_photos)} photos")
                self._on_delete_selected()
                event.accept()
            else:
                super().keyPressEvent(event)

        # UX-1: Ctrl+F handled by MainWindow

        # Ctrl+N: New project
        elif key == Qt.Key_N and modifiers == Qt.ControlModifier:
            print("[GooglePhotosLayout] ⌨️ Ctrl+N - New project")
            self._on_create_project_clicked()
            event.accept()

        # Enter: Open first selected photo
        elif key == Qt.Key_Return or key == Qt.Key_Enter:
            if len(self.selected_photos) > 0:
                first_photo = list(self.selected_photos)[0]
                print(f"[GooglePhotosLayout] ⌨️ ENTER - Open {first_photo}")
                self._on_photo_clicked(first_photo)
                event.accept()
            else:
                super().keyPressEvent(event)

        # Space: Quick preview (full screen)
        elif key == Qt.Key_Space:
            if len(self.selected_photos) > 0:
                first_photo = list(self.selected_photos)[0]
                print(f"[GooglePhotosLayout] ⌨️ SPACE - Quick preview {first_photo}")
                self._on_photo_clicked(first_photo)
                event.accept()
            else:
                super().keyPressEvent(event)

        # S: Toggle selection mode
        elif key == Qt.Key_S and not modifiers:
            print("[GooglePhotosLayout] ⌨️ S - Toggle selection mode")
            if hasattr(self, 'btn_select'):
                self.btn_select.setChecked(not self.btn_select.isChecked())
                self._toggle_selection_mode(self.btn_select.isChecked())
            event.accept()

        # +/=: Zoom in
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            print("[GooglePhotosLayout] ⌨️ + - Zoom in")
            if hasattr(self, 'zoom_slider'):
                current = self.zoom_slider.value()
                self.zoom_slider.setValue(min(current + 50, self.zoom_slider.maximum()))
            event.accept()

        # -: Zoom out
        elif key == Qt.Key_Minus:
            print("[GooglePhotosLayout] ⌨️ - - Zoom out")
            if hasattr(self, 'zoom_slider'):
                current = self.zoom_slider.value()
                self.zoom_slider.setValue(max(current - 50, self.zoom_slider.minimum()))
            event.accept()

        # G: Grid View
        elif key == Qt.Key_G and not modifiers:
            print("[GooglePhotosLayout] ⌨️ G - Grid view")
            if hasattr(self, '_show_grid_view'):
                self._show_grid_view()
            event.accept()

        # T: Timeline View
        elif key == Qt.Key_T and not modifiers:
            print("[GooglePhotosLayout] ⌨️ T - Timeline view")
            if hasattr(self, '_show_timeline_view'):
                self._show_timeline_view()
            event.accept()

        # E: Single View
        elif key == Qt.Key_E and not modifiers:
            print("[GooglePhotosLayout] ⌨️ E - Single view")
            if hasattr(self, '_show_single_view'):
                self._show_single_view()
            event.accept()
        
        # F: Toggle favorite for selected photos
        elif key == Qt.Key_F and not modifiers:
            if len(self.selected_photos) > 0:
                print(f"[GooglePhotosLayout] ⌨️ F - Toggle favorite for {len(self.selected_photos)} photos")
                self._on_favorite_selected()
                event.accept()
            else:
                super().keyPressEvent(event)

        else:
            # Pass to parent for other keys
            super().keyPressEvent(event)

    def _toggle_selection_mode(self, checked: bool):
        """
        Toggle selection mode on/off.

        Args:
            checked: Whether Select button is checked
        """
        self.selection_mode = checked
        print(f"[GooglePhotosLayout] Selection mode: {'ON' if checked else 'OFF'}")

        # Show/hide all checkboxes
        self._update_checkboxes_visibility()

        # Update button text
        if checked:
            self.btn_select.setText("☑️ Cancel")
            self.btn_select.setStyleSheet("QPushButton { background: #1a73e8; color: white; }")
        else:
            self.btn_select.setText("☑️ Select")
            self.btn_select.setStyleSheet("")

            # Clear selection when exiting selection mode
            self._clear_selection()

    def _update_checkboxes_visibility(self):
        """
        Show or hide all checkboxes based on selection mode.

        Phase 3 #1: Added smooth fade animations for checkboxes.
        """
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve

        # Iterate through all thumbnails
        for i in range(self.timeline_layout.count()):
            date_group = self.timeline_layout.itemAt(i).widget()
            if not date_group:
                continue

            group_layout = date_group.layout()
            if not group_layout:
                continue

            for j in range(group_layout.count()):
                item = group_layout.itemAt(j)
                if not item or not item.widget():
                    continue

                widget = item.widget()
                if hasattr(widget, 'layout') and widget.layout():
                    grid = widget.layout()
                    for k in range(grid.count()):
                        container = grid.itemAt(k).widget()
                        if container:
                            checkbox = container.property("checkbox")
                            if checkbox:
                                # PHASE 3 #1: Smooth fade animation for checkbox visibility
                                if self.selection_mode:
                                    # Fade in
                                    checkbox.setVisible(True)
                                    if not checkbox.graphicsEffect():
                                        opacity_effect = QGraphicsOpacityEffect()
                                        checkbox.setGraphicsEffect(opacity_effect)
                                        opacity_effect.setOpacity(0.0)

                                        fade_in = QPropertyAnimation(opacity_effect, b"opacity")
                                        fade_in.setDuration(200)  # 200ms fade-in
                                        fade_in.setStartValue(0.0)
                                        fade_in.setEndValue(1.0)
                                        fade_in.setEasingCurve(QEasingCurve.OutCubic)
                                        fade_in.start()

                                        # Store animation to prevent garbage collection
                                        checkbox.setProperty("fade_animation", fade_in)
                                else:
                                    # Fade out
                                    if checkbox.graphicsEffect():
                                        opacity_effect = checkbox.graphicsEffect()
                                        fade_out = QPropertyAnimation(opacity_effect, b"opacity")
                                        fade_out.setDuration(150)  # 150ms fade-out
                                        fade_out.setStartValue(1.0)
                                        fade_out.setEndValue(0.0)
                                        fade_out.setEasingCurve(QEasingCurve.InCubic)
                                        fade_out.finished.connect(lambda cb=checkbox: cb.setVisible(False))
                                        fade_out.start()
                                        checkbox.setProperty("fade_animation", fade_out)
                                    else:
                                        checkbox.setVisible(False)

    def _setup_drag_select(self):
        """
        PHASE 2 #2: Setup drag-to-select (rubber band) functionality.

        File Explorer-style rectangle selection.
        """
        from PySide6.QtWidgets import QRubberBand

        # Create rubber band for visual feedback
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self.timeline_scroll.viewport())
        self.rubber_band.hide()

        # Drag state
        self.is_dragging = False
        self.drag_start_pos = None

        # PHASE 2 #2: Create and install event filter (must be QObject)
        if not hasattr(self, 'event_filter'):
            self.event_filter = GooglePhotosEventFilter(self)

        # Install event filter on viewport to capture mouse events
        self.timeline_scroll.viewport().installEventFilter(self.event_filter)

    def _handle_drag_select_press(self, pos):
        """PHASE 2 #2: Start drag selection."""
        from PySide6.QtCore import QPoint

        # Only start drag if selection mode is active and not clicking on a thumbnail
        if not self.selection_mode:
            return False

        # Check if clicked on empty space (not on a thumbnail button)
        widget = self.timeline_scroll.viewport().childAt(pos)
        if widget and isinstance(widget, QPushButton):
            return False  # Clicked on thumbnail, don't start drag

        self.is_dragging = True
        self.drag_start_pos = pos
        self.rubber_band.setGeometry(pos.x(), pos.y(), 0, 0)
        self.rubber_band.show()
        return True

    def _handle_drag_select_move(self, pos):
        """PHASE 2 #2: Update rubber band during drag."""
        from PySide6.QtCore import QRect

        if not self.is_dragging or not self.drag_start_pos:
            return

        # Calculate rubber band rectangle
        x = min(self.drag_start_pos.x(), pos.x())
        y = min(self.drag_start_pos.y(), pos.y())
        width = abs(pos.x() - self.drag_start_pos.x())
        height = abs(pos.y() - self.drag_start_pos.y())

        self.rubber_band.setGeometry(x, y, width, height)

    def _handle_drag_select_release(self, pos):
        """PHASE 2 #2: Finish drag selection and select thumbnails in rectangle."""
        from PySide6.QtCore import QRect

        if not self.is_dragging or not self.drag_start_pos:
            return

        self.is_dragging = False
        self.rubber_band.hide()

        # Calculate selection rectangle in viewport coordinates
        x = min(self.drag_start_pos.x(), pos.x())
        y = min(self.drag_start_pos.y(), pos.y())
        width = abs(pos.x() - self.drag_start_pos.x())
        height = abs(pos.y() - self.drag_start_pos.y())

        selection_rect = QRect(x, y, width, height)

        # Find all thumbnails that intersect with selection rectangle
        viewport = self.timeline_scroll.viewport()
        selected_count = 0

        for i in range(self.timeline_layout.count()):
            date_group = self.timeline_layout.itemAt(i).widget()
            if not date_group:
                continue

            group_layout = date_group.layout()
            if not group_layout:
                continue

            for j in range(group_layout.count()):
                item = group_layout.itemAt(j)
                if not item or not item.widget():
                    continue

                widget = item.widget()
                if hasattr(widget, 'layout') and widget.layout():
                    grid = widget.layout()
                    for k in range(grid.count()):
                        container = grid.itemAt(k).widget()
                        if not container:
                            continue

                        # Get thumbnail button position relative to viewport
                        thumb_button = container.property("thumbnail_button")
                        if not thumb_button:
                            continue

                        try:
                            # Map thumbnail position to viewport coordinates
                            thumb_global = thumb_button.mapTo(viewport, thumb_button.rect().topLeft())
                            thumb_rect = QRect(thumb_global, thumb_button.size())

                            # Check if thumbnail intersects with selection rectangle
                            if selection_rect.intersects(thumb_rect):
                                # Select this thumbnail
                                photo_path = container.property("photo_path")
                                checkbox = container.property("checkbox")

                                if photo_path and checkbox:
                                    if photo_path not in self.selected_photos:
                                        self.selected_photos.add(photo_path)
                                        checkbox.setChecked(True)
                                        selected_count += 1
                        except:
                            pass  # Skip thumbnails that can't be mapped

        if selected_count > 0:
            print(f"[GooglePhotosLayout] Drag-selected {selected_count} photos")
            self._update_selection_ui()

        self.drag_start_pos = None

    def _clear_selection(self):
        """
        Clear all selected photos and uncheck checkboxes.
        """
        # Uncheck all checkboxes
        for path in list(self.selected_photos):
            container = self._find_thumbnail_container(path)
            if container:
                checkbox = container.property("checkbox")
                if checkbox:
                    checkbox.setChecked(False)

        self.selected_photos.clear()
        self._update_selection_ui()

    def _on_batch_edit_location_clicked(self):
        """
        Handle batch location edit button click from floating toolbar.

        This provides a prominent, discoverable way to batch edit GPS locations
        for selected photos without requiring context menu access.
        """
        from PySide6.QtWidgets import QMessageBox

        if not self.selected_photos:
            QMessageBox.information(
                self.main_window,
                "No Photos Selected",
                "Please select one or more photos to edit their location.\n\n"
                "Tip: Click photos to select them, then click the 📍 Location button."
            )
            return

        # Call existing batch edit method (reuses Sprint 1 implementation)
        print(f"[GooglePhotosLayout] 📍 Batch location edit triggered from toolbar for {len(self.selected_photos)} photos")
        self._edit_photos_location_batch(list(self.selected_photos))

    def _on_toggle_activity_center(self):
        """Toggle the Activity Center dock widget from the layout toolbar."""
        try:
            self._set_shell_state_text("Opening Activity Center")
            if hasattr(self.main_window, "_toggle_activity_center"):
                self.main_window._toggle_activity_center()
        except Exception:
            pass

    def _on_delete_selected(self):
        """
        Delete all selected photos.
        """
        from PySide6.QtWidgets import QMessageBox

        if not self.selected_photos:
            return

        count = len(self.selected_photos)

        # Confirm deletion
        reply = QMessageBox.question(
            self.main_window,
            "Delete Photos",
            f"Are you sure you want to delete {count} photo{'s' if count > 1 else ''}?\n\n"
            "This will remove them from the database but NOT delete the actual files.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        print(f"[GooglePhotosLayout] Deleting {count} photos...")

        # TODO Phase 2: Implement actual deletion from database
        # For now, just clear selection and show message
        QMessageBox.information(
            self.main_window,
            "Delete Photos",
            f"{count} photo{'s' if count > 1 else ''} deleted successfully!\n\n"
            "(Note: Actual deletion not yet implemented - Phase 2 placeholder)"
        )

        self._clear_selection()
        self._load_photos()  # Refresh timeline

    def _on_favorite_selected(self):
        """
        Toggle favorite tag for all selected photos (batch operation).
        
        Follows Current layout pattern:
        - Check if any photo is already favorited
        - If any favorited: unfavorite all
        - If none favorited: favorite all
        - Refresh tag overlays after operation
        - Show status message
        """
        if not self.selected_photos:
            return
        
        try:
            from reference_db import ReferenceDB
            
            paths = list(self.selected_photos)
            count = len(paths)
            
            # Check if any photo is already favorited
            db = ReferenceDB()
            has_favorite = False
            for path in paths:
                tags = db.get_tags_for_photo(path, self.project_id) or []
                if "favorite" in tags:
                    has_favorite = True
                    break
            
            # Toggle: if any is favorite, unfavorite all; otherwise favorite all
            if has_favorite:
                # Unfavorite all
                for path in paths:
                    db.remove_tag(path, "favorite", self.project_id)
                msg = f"⭐ Removed favorite from {count} photo{'s' if count > 1 else ''}"
                print(f"[GooglePhotosLayout] Unfavorited {count} photos")
            else:
                # Favorite all
                for path in paths:
                    db.add_tag(path, "favorite", self.project_id)
                msg = f"⭐ Added {count} photo{'s' if count > 1 else ''} to favorites"
                print(f"[GooglePhotosLayout] Favorited {count} photos")
            
            # Refresh tag overlays for affected photos
            self._refresh_tag_overlays(paths)
            
            # Show status message in parent window
            if hasattr(self.main_window, 'statusBar'):
                self.main_window.statusBar().showMessage(msg, 3000)
            
            # Clear selection after operation
            self._clear_selection()
            
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error toggling favorites: {e}")
            import traceback
            traceback.print_exc()

    def _on_share_selected(self):
        """
        PHASE 3 #7: Show share/export dialog for selected photos.

        Allows users to:
        - Copy file paths to clipboard
        - Export to a folder
        - Show in file explorer
        """
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog, QApplication
        from PySide6.QtGui import QClipboard

        if not self.selected_photos:
            return

        # Create share dialog
        dialog = QDialog(self.main_window)
        dialog.setWindowTitle("Share / Export Photos")
        dialog.setMinimumWidth(500)
        dialog.setStyleSheet("""
            QDialog {
                background: white;
            }
            QLabel {
                font-size: 11pt;
            }
            QPushButton {
                background: white;
                border: 1px solid #dadce0;
                border-radius: 4px;
                padding: 10px 20px;
                font-size: 11pt;
            }
            QPushButton:hover {
                background: #f1f3f4;
                border-color: #1a73e8;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        count = len(self.selected_photos)
        header = QLabel(f"📤 Share {count} photo{'s' if count > 1 else ''}")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(header)

        # Copy paths button
        copy_btn = QPushButton("📋 Copy File Paths to Clipboard")
        copy_btn.setToolTip("Copy all selected file paths to clipboard (one per line)")
        def copy_paths():
            paths_text = '\n'.join(sorted(self.selected_photos))
            clipboard = QApplication.clipboard()
            clipboard.setText(paths_text)
            copy_btn.setText("✓ Copied!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: copy_btn.setText("📋 Copy File Paths to Clipboard"))
        copy_btn.clicked.connect(copy_paths)
        layout.addWidget(copy_btn)

        # Export to folder button
        export_btn = QPushButton("💾 Export to Folder...")
        export_btn.setToolTip("Copy selected photos to a new folder")
        def export_to_folder():
            import shutil
            folder = QFileDialog.getExistingDirectory(
                dialog,
                "Select Export Destination",
                "",
                QFileDialog.ShowDirsOnly
            )
            if folder:
                try:
                    success_count = 0
                    for photo_path in self.selected_photos:
                        filename = os.path.basename(photo_path)
                        dest_path = os.path.join(folder, filename)
                        shutil.copy2(photo_path, dest_path)
                        success_count += 1

                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.information(
                        dialog,
                        "Export Complete",
                        f"✓ Exported {success_count} photo{'s' if success_count > 1 else ''} to:\n{folder}"
                    )
                    dialog.accept()
                except Exception as e:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.critical(
                        dialog,
                        "Export Failed",
                        f"Error exporting photos:\n{str(e)}"
                    )
        export_btn.clicked.connect(export_to_folder)
        layout.addWidget(export_btn)

        # Show in explorer button (for first selected file)
        first_photo = sorted(self.selected_photos)[0]
        explorer_btn = QPushButton(f"📂 Show in Explorer")
        explorer_btn.setToolTip("Open file explorer at first selected photo")
        def show_in_explorer():
            self._show_in_explorer(first_photo)
            dialog.accept()
        explorer_btn.clicked.connect(show_in_explorer)
        layout.addWidget(explorer_btn)

        # Close button
        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(dialog.reject)
        layout.addWidget(close_btn)

        dialog.exec()

    # ============ Phase 2: Search Functionality ============

    def _on_semantic_search(self, photo_ids: list, query: str, scores: list):
        """
        Handle semantic search results for Google Photos layout.

        Args:
            photo_ids: List of photo IDs from semantic search
            query: Search query text
            scores: List of (photo_id, similarity_score) tuples
        """
        try:
            from repository.photo_repository import PhotoRepository

            logger.info(f"[GooglePhotosLayout] 🔍✨ Semantic search: {len(photo_ids)} results for '{query}'")

            # Create score lookup
            score_map = {photo_id: score for photo_id, score in scores}

            # Get photo paths and metadata
            photo_repo = PhotoRepository()
            photo_data = []

            with photo_repo.connection() as conn:
                placeholders = ','.join('?' * len(photo_ids))
                cursor = conn.execute(f"""
                    SELECT id, path, date_taken, width, height
                    FROM photo_metadata
                    WHERE id IN ({placeholders})
                    AND date_taken IS NOT NULL
                    ORDER BY date_taken DESC
                """, photo_ids)

                for row in cursor.fetchall():
                    photo_id = row["id"]
                    path = row["path"]
                    date_taken = row["date_taken"]
                    width = row["width"]
                    height = row["height"]
                    score = score_map.get(photo_id, 0.0)

                    photo_data.append({
                        'path': path,
                        'date_taken': date_taken,
                        'width': width,
                        'height': height,
                        'score': score
                    })

            if photo_data:
                # Convert to row format expected by _rebuild_timeline_with_results
                rows = [(d['path'], d['date_taken'], d['width'], d['height']) for d in photo_data]

                # Rebuild timeline with results
                self._rebuild_timeline_with_results(rows, f"✨ {query}")

                # Calculate and show score stats
                min_score = min(d['score'] for d in photo_data)
                max_score = max(d['score'] for d in photo_data)
                avg_score = sum(d['score'] for d in photo_data) / len(photo_data)

                # Add search header with score info
                QTimer.singleShot(100, lambda: self._add_search_header(
                    f"✨ Semantic search: {len(photo_data)} photos matching '{query}' "
                    f"(similarity: {min_score:.1%} - {max_score:.1%}, avg: {avg_score:.1%})"
                ))

                logger.info(
                    f"[GooglePhotosLayout] Semantic search displayed {len(photo_data)} results - "
                    f"score range: {min_score:.3f} to {max_score:.3f}"
                )
            else:
                # No results - reload all photos
                self._load_photos()
                QTimer.singleShot(100, lambda: self._add_search_header(
                    f"✨ No photos found for '{query}'"
                ))

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Semantic search failed: {e}", exc_info=True)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.main_window,
                "Semantic Search Error",
                f"Failed to display semantic search results:\n{e}"
            )

    def _on_semantic_search_cleared(self):
        """Handle semantic search cleared - reload all photos."""
        try:
            logger.info("[GooglePhotosLayout] Semantic search cleared, reloading photos")
            self._load_photos()

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Failed to clear semantic search: {e}", exc_info=True)

    def _on_smart_find_results(self, paths: list, query_label: str):
        """
        Handle Smart Find results from sidebar Find section.

        Displays matching photos in the grid using the existing
        filter_by_paths mechanism, plus adds a search header.

        CRITICAL: paths=[] means "search returned zero results" and must
        show an empty state — NOT load the full library.  paths with
        items means "display exactly these photos".
        """
        try:
            logger.info(
                f"[GooglePhotosLayout] Smart Find: '{query_label}' → {len(paths)} results"
            )

            if paths:
                # Force the coalescing signature to miss so the UI always
                # refreshes for a new search, even when the underlying paths
                # happen to be identical to the previous result set (common
                # in small libraries where backoff pulls in the same photos).
                self._last_load_signature = None

                # Use existing path-based filtering
                self._request_load(paths=paths)

                # Add search header with result info
                QTimer.singleShot(100, lambda: self._add_search_header(
                    f"{query_label}  —  {len(paths)} photos"
                ))
            else:
                # ── Empty results: show clean empty state, NOT the full library ──
                # This matches Apple Photos / Google Photos behaviour:
                # zero matches → centered empty-state card, never a silent
                # fallback to showing everything.
                self._photo_load_generation += 1
                self._clear_timeline_for_new_content()
                self.btn_clear_filter.setVisible(True)  # allow user to clear

                empty_widget = self._create_empty_state(
                    icon="\U0001f50d",
                    title=f"{query_label}",
                    message="No matching photos found",
                    action_text="Try a different search or click Clear to return"
                )
                self.timeline_layout.addWidget(empty_widget)
                logger.info(
                    f"[GooglePhotosLayout] Smart Find empty state shown for '{query_label}'"
                )

        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Smart Find display failed: {e}", exc_info=True)

    def _on_smart_find_cleared(self):
        """Handle Smart Find cleared - restore full photo grid."""
        try:
            logger.info("[GooglePhotosLayout] Smart Find cleared, reloading all photos")
            self._clear_filter()
        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Smart Find clear failed: {e}", exc_info=True)

    def _on_smart_find_scores(self, scores: object):
        """
        Handle Smart Find confidence scores for overlay display.

        Stores scores dict so thumbnails can show confidence badges.
        """
        try:
            self._smart_find_scores = scores if isinstance(scores, dict) else {}
            logger.debug(
                f"[GooglePhotosLayout] Received {len(self._smart_find_scores)} confidence scores"
            )
        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Smart Find scores failed: {e}")

    def _on_smart_find_exclude(self, path: str):
        """
        Handle 'Not this' exclusion - remove photo from current results.

        Delegates to SmartFindService to track exclusion, then re-runs
        the current active search.
        """
        try:
            if hasattr(self, 'accordion_sidebar'):
                find = self.accordion_sidebar.section_logic.get("find")
                if find:
                    service = find._get_service()
                    if service:
                        service.exclude_path(path)
                        # Re-run the current search directly (don't use
                        # _on_preset_clicked which would toggle the search off)
                        if find._active_preset_id:
                            find._run_preset_find(
                                find._active_preset_id,
                                find._get_refine_filters()
                            )
                        elif find._active_text_query:
                            find._execute_text_search()
                        logger.info(f"[GooglePhotosLayout] Excluded photo from results: {path}")
        except Exception as e:
            logger.error(f"[GooglePhotosLayout] Smart Find exclude failed: {e}")

    def _filter_people_by_count(self, operator: str, threshold: int):
        """Filter people grid by photo count."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            
            # Build SQL condition
            if operator == '>':
                condition = "count > ?"
            elif operator == '>=':
                condition = "count >= ?"
            elif operator == '<':
                condition = "count < ?"
            elif operator == '<=':
                condition = "count <= ?"
            else:
                condition = "count = ?"
            
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT branch_key, label, count FROM face_branch_reps WHERE project_id = ? AND {condition} ORDER BY count DESC",
                    (self.project_id, threshold)
                )
                results = cur.fetchall()
            
            # Rebuild people grid with filtered results
            self.people_grid.clear()
            
            for branch_key, label, count in results:
                # Load face thumbnail
                with db._connect() as conn2:
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "SELECT rep_path, rep_thumb_png FROM face_branch_reps WHERE project_id = ? AND branch_key = ?",
                        (self.project_id, branch_key)
                    )
                    row = cur2.fetchone()
                    if row:
                        rep_path, rep_thumb = row
                        face_pix = None
                        if rep_thumb:
                            import base64
                            data = base64.b64decode(rep_thumb) if isinstance(rep_thumb, str) else rep_thumb
                            face_pix = QPixmap()
                            face_pix.loadFromData(data)
                        elif rep_path and os.path.exists(rep_path):
                            face_pix = QPixmap(rep_path)
                        
                        self.people_grid.add_person(branch_key, label or "Unnamed", face_pix, count)
            
            # Update People section count and show result message
            try:
                self.people_section.update_count(len(results))
            except Exception:
                pass
            op_text = {'>': 'more than', '>=': 'at least', '<': 'less than', '<=': 'at most'}.get(operator, '')
            QMessageBox.information(
                self.main_window,
                "Filtered Results",
                f"📊 Found {len(results)} people with {op_text} {threshold} photos"
            )
            
        except Exception as e:
            print(f"[GooglePhotosLayout] Failed to filter by count: {e}")
            import traceback
            traceback.print_exc()
    
    def _search_multi_person(self, person_names: list):
        """Search for photos containing multiple people (AND logic)."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            
            # Find branch keys for each person
            branch_keys = []
            found_names = []
            
            with db._connect() as conn:
                cur = conn.cursor()
                for name in person_names:
                    cur.execute(
                        "SELECT branch_key, label FROM face_branch_reps WHERE project_id = ? AND LOWER(label) LIKE ?",
                        (self.project_id, f"%{name.lower()}%")
                    )
                    result = cur.fetchone()
                    if result:
                        branch_keys.append(result[0])
                        found_names.append(result[1] or result[0])
            
            if len(branch_keys) < len(person_names):
                QMessageBox.warning(
                    self.main_window,
                    "Not All Found",
                    f"Could only find {len(branch_keys)} of {len(person_names)} people.\n\nFound: {', '.join(found_names)}"
                )
                if not branch_keys:
                    return
            
            # Filter People section to show only these people
            if len(found_names) == 1:
                self._filter_people_grid(found_names[0])
            elif len(found_names) > 1:
                # For multi-person, show all matched people
                # Clear all first, then show only matched ones
                search_pattern = "|".join([name.lower() for name in found_names])
                for i in range(self.people_grid.flow_layout.count()):
                    item = self.people_grid.flow_layout.itemAt(i)
                    if item and item.widget():
                        card = item.widget()
                        if isinstance(card, PersonCard):
                            matches = any(name.lower() in card.display_name.lower() for name in found_names)
                            card.setVisible(matches)
            
            # Find photos that contain ALL these people
            with db._connect() as conn:
                cur = conn.cursor()
                
                # Build query to find images with all branch keys
                # Use INTERSECT to find common images
                queries = []
                for bk in branch_keys:
                    queries.append(f"""
                        SELECT DISTINCT fc.image_path
                        FROM face_crops fc
                        WHERE fc.project_id = ? AND fc.branch_key = ?
                    """)
                
                full_query = " INTERSECT ".join(queries)
                params = []
                for bk in branch_keys:
                    params.extend([self.project_id, bk])
                
                cur.execute(full_query, params)
                image_paths = [row[0] for row in cur.fetchall()]
                
                # Get full photo metadata
                if image_paths:
                    placeholders = ','.join(['?'] * len(image_paths))
                    cur.execute(
                        f"SELECT DISTINCT path, date_taken, width, height FROM photo_metadata WHERE path IN ({placeholders}) ORDER BY date_taken DESC",
                        image_paths
                    )
                    rows = cur.fetchall()
                else:
                    rows = []
            
            # Rebuild timeline with results
            self._rebuild_timeline_with_results(
                rows,
                f"{' AND '.join(found_names)} (multi-person)"
            )
            
        except Exception as e:
            print(f"[GooglePhotosLayout] Multi-person search failed: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "Search Failed", f"Error: {e}")
    
    def _add_search_header(self, message: str):
        """Add a search result header to the timeline (for person filters)."""
        try:
            # Check if header already exists and remove it
            for i in range(self.timeline_layout.count()):
                widget = self.timeline_layout.itemAt(i).widget()
                if widget and hasattr(widget, 'objectName') and widget.objectName() == "search_header":
                    widget.deleteLater()
                    break
            
            # Add new header
            header = QLabel(message)
            header.setObjectName("search_header")
            header.setStyleSheet("font-size: 11pt; font-weight: bold; padding: 10px 20px; color: #1a73e8;")
            self.timeline_layout.insertWidget(0, header)
        except Exception as e:
            print(f"[GooglePhotosLayout] Failed to add search header: {e}")
    
    def _on_autocomplete_selected(self, item):
        """Handle autocomplete selection."""
        # Get the actual person name from stored data (not the display text with count)
        person_name = item.data(Qt.UserRole)
        if person_name:
            # Set search box to just the person name
            self.people_search.setText(person_name)
            self.people_autocomplete.hide()
            # Trigger search with person name
            self._perform_search(person_name)
        else:
            # Fallback to display text if no data stored
            self.people_search.setText(item.text())
            self.people_autocomplete.hide()
            self._perform_search(item.text())
    
    def _on_people_search(self, text: str):
        """Handle people search text change with autocomplete."""
        if not text or len(text) < 2:
            self.people_autocomplete.hide()
            # Filter people grid
            self._filter_people_grid(text)
            return
        
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            
            # Fetch matching people
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT label, count FROM face_branch_reps WHERE project_id = ? AND LOWER(label) LIKE ? ORDER BY count DESC LIMIT 10",
                    (self.project_id, f"%{text.lower()}%")
                )
                results = cur.fetchall()
            
            # Populate autocomplete
            self.people_autocomplete.clear()
            
            if results:
                for label, count in results:
                    if label:
                        item_text = f"{label} ({count} photos)"
                        item = QListWidgetItem(item_text)
                        item.setData(Qt.UserRole, label)  # Store actual name
                        self.people_autocomplete.addItem(item)
                
                # Position autocomplete below search box
                search_global = self.people_search.mapToGlobal(self.people_search.rect().bottomLeft())
                self.people_autocomplete.move(search_global)
                self.people_autocomplete.setFixedWidth(self.people_search.width())
                self.people_autocomplete.show()
                self.people_autocomplete.raise_()
            else:
                self.people_autocomplete.hide()
            
            # Also filter people grid
            self._filter_people_grid(text)
            
        except Exception as e:
            print(f"[GooglePhotosLayout] Autocomplete error: {e}")
            self.people_autocomplete.hide()

    def _rebuild_timeline_with_results(self, rows, search_text: str):
        """
        Rebuild timeline with search results.
        """
        # Clear existing timeline and trees for search results
        while self.timeline_layout.count():
            child = self.timeline_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # NOTE: With AccordionSidebar, clearing is handled internally - no action needed here

        if not rows:
            # No results
            empty_label = QLabel(f"🔍 No results for '{search_text}'\n\nTry different search terms")
            empty_label.setAlignment(Qt.AlignCenter)
            empty_label.setStyleSheet("font-size: 12pt; color: #888; padding: 60px;")
            self.timeline_layout.addWidget(empty_label)
            print(f"[GooglePhotosLayout] No search results for: '{search_text}'")
            return

        # Group and display results
        photos_by_date = self._group_photos_by_date(rows)
        self._build_timeline_tree(photos_by_date)

        # Add search results header
        header = QLabel(f"🔍 Found {len(rows)} results for '{search_text}'")
        header.setStyleSheet("font-size: 11pt; font-weight: bold; padding: 10px 20px; color: #1a73e8;")
        self.timeline_layout.insertWidget(0, header)

        # Create date groups (use current thumb size)
        thumb_size = getattr(self, 'current_thumb_size', 200)
        for date_str in sorted(photos_by_date.keys(), reverse=True):
            photos = photos_by_date.get(date_str, [])
            date_group = self._create_date_group(date_str, photos, thumb_size)
            self.timeline_layout.addWidget(date_group)

        self.timeline_layout.addStretch()

        print(f"[GooglePhotosLayout] Search results: {len(rows)} photos in {len(photos_by_date)} dates")

    # ============ Phase 2: Zoom Functionality ============

    def _on_zoom_changed(self, value: int):
        """
        Handle zoom slider change - adjust thumbnail size.

        BUG FIX: Use percentage-based scroll restoration to maintain viewport position.
        When thumbnail size changes, grid height changes proportionally.
        Restoring absolute pixel position causes viewport to jump.

        Args:
            value: New thumbnail size in pixels (100-400)
        """
        logger.info("[GooglePhotosLayout] 🔎 Zoom changed to: %spx", value)

        # Update label immediately (just the number, no "px")
        self.zoom_value_label.setText(f"{value}")

        # Debounce heavy reload work while dragging the slider
        self._pending_zoom_value = value
        if self.zoom_change_timer.isActive():
            self.zoom_change_timer.stop()
        self.zoom_change_timer.start()

    def _commit_zoom_change(self):
        """Apply the most recent zoom change after debounce."""
        value = self._pending_zoom_value
        if value is None:
            return

        timeline_scroll = getattr(self, "timeline_scroll", None)
        if not timeline_scroll:
            return

        scroll_bar = timeline_scroll.verticalScrollBar()
        max_scroll = scroll_bar.maximum()
        current_scroll = scroll_bar.value()

        # Calculate percentage (0.0 to 1.0)
        scroll_percentage = current_scroll / max_scroll if max_scroll > 0 else 0.0

        logger.info(
            "[GooglePhotosLayout] Saving scroll position: %s/%s (%0.2f%%)",
            current_scroll,
            max_scroll,
            scroll_percentage * 100,
        )

        # Reload with new size
        self._queue_scroll_restore(scroll_percentage)
        self._load_photos(
            thumb_size=value,
            filter_year=self.current_filter_year,
            filter_month=self.current_filter_month,
            filter_day=self.current_filter_day,
            filter_folder=self.current_filter_folder,
            filter_person=self.current_filter_person,
        )

        # Clear pending value once dispatched
        self._pending_zoom_value = None

    def _queue_scroll_restore(self, percentage: float):
        """Cache a pending scroll restoration percentage for the next render."""
        try:
            percentage = max(0.0, min(1.0, float(percentage)))
        except Exception:
            percentage = 0.0
        self._pending_scroll_restore = percentage

    def _restore_scroll_percentage(self, percentage: float):
        """
        Restore scroll position to a percentage of maximum scroll.

        BUG FIX: Maintains viewport position when grid height changes.
        Used after zoom or aspect ratio changes.

        Args:
            percentage: Scroll percentage (0.0 to 1.0)
        """
        timeline = getattr(self, "timeline", None)
        if not timeline:
            logger.debug("[GooglePhotosLayout] Timeline gone during scroll restore; skipping")
            return

        try:
            scrollbar = timeline.verticalScrollBar()
        except RuntimeError:
            logger.debug(
                "[GooglePhotosLayout] Timeline destroyed before scroll restore; skipping",
            )
            return
        new_max = scrollbar.maximum()
        new_position = int(new_max * percentage)

        logger.info(
            "[GooglePhotosLayout] Restoring scroll position: %s/%s (%0.2f%%)",
            new_position,
            new_max,
            percentage * 100,
        )

        scrollbar.setValue(new_position)

    def _set_aspect_ratio(self, mode: str):
        """
        PHASE 2 #5: Set thumbnail aspect ratio mode.

        BUG FIX: Use percentage-based scroll restoration (same as zoom).

        Args:
            mode: "square", "original", or "16:9"
        """
        logger.info("[GooglePhotosLayout] 📐 Aspect ratio changed to: %s", mode)

        # Update state
        self.thumbnail_aspect_ratio = mode

        # Update button states
        self.btn_aspect_square.setChecked(mode == "square")
        self.btn_aspect_original.setChecked(mode == "original")
        self.btn_aspect_16_9.setChecked(mode == "16:9")

        # BUG FIX: Calculate scroll PERCENTAGE before reload
        scrollbar = self.timeline.verticalScrollBar()
        max_scroll = scrollbar.maximum()
        current_scroll = scrollbar.value()
        scroll_percentage = current_scroll / max_scroll if max_scroll > 0 else 0.0

        # Reload with current thumb size
        current_size = self.zoom_slider.value()
        self._queue_scroll_restore(scroll_percentage)
        self._load_photos(
            thumb_size=current_size,
            filter_year=self.current_filter_year,
            filter_month=self.current_filter_month,
            filter_day=self.current_filter_day,
            filter_folder=self.current_filter_folder,
            filter_person=self.current_filter_person,
        )

    def _clear_filter(self):
        """
        Clear all date/folder/person filters and show all photos.
        """
        self._set_shell_active_branch("all")
        if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
            self.google_shell_sidebar.set_legacy_emphasis(False)
        self._refresh_legacy_visibility_state()
        self._set_shell_state_text("Showing all photos")
        print("[GooglePhotosLayout] Clearing all filters")

        # Reload without filters
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=None,
            filter_month=None,
            filter_day=None,
            filter_folder=None,
            filter_person=None
        )

        # UX-1: Search is now centralized. Controller handles clearing state.

    def _on_detect_duplicates(self):
        """
        Show duplicate detection configuration dialog.

        The dialog allows users to:
        1. Select scope (all photos, specific folders, date range, etc.)
        2. Choose detection methods (exact, similar, or both)
        3. Configure sensitivity parameters
        4. Run detection with progress tracking
        """
        try:
            from PySide6.QtWidgets import QMessageBox

            if self.project_id is None:
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project before detecting duplicates."
                )
                return

            # Show the duplicate detection configuration dialog
            from ui.duplicate_detection_dialog import DuplicateDetectionDialog
            dialog = DuplicateDetectionDialog(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # If user completed detection, show the results dialog
            if dialog.exec():
                # Detection completed - show results
                from layouts.google_components.duplicates_dialog import DuplicatesDialog
                results_dialog = DuplicatesDialog(
                    project_id=self.project_id,
                    parent=self.main_window if hasattr(self, 'main_window') else None
                )
                results_dialog.exec()

            # Refresh sidebar duplicates section after dialog closes
            if hasattr(self, 'accordion_sidebar') and self.accordion_sidebar:
                self.accordion_sidebar.reload_section("duplicates")

            # CRITICAL: Refresh photo grid to show duplicate/similar badges
            # Badges are only created when thumbnails are created, so we need to reload
            logger.info("[GooglePhotosLayout] Refreshing grid to show duplicate/similar badges...")
            self._load_photos()

        except Exception as e:
            print(f"[GooglePhotosLayout] Error opening duplicate detection dialog: {e}")
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error",
                f"Failed to open duplicate detection dialog:\n{str(e)}"
            )

    def _on_find_similar_photos(self):
        """
        Open SimilarPhotoDetectionDialog to find visually similar photos.

        This dialog allows users to:
        - Configure similarity threshold and clustering parameters
        - Find visually similar photos using AI embeddings
        - Results are persisted to database and shown in sidebar
        """
        try:
            from ui.similar_photo_dialog import SimilarPhotoDetectionDialog

            if self.project_id is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project before finding similar photos."
                )
                return

            # Open the dialog
            dialog = SimilarPhotoDetectionDialog(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Show the dialog
            result = dialog.exec()

            # Refresh sidebar duplicates section after dialog closes
            if hasattr(self, 'accordion_sidebar') and self.accordion_sidebar:
                self.accordion_sidebar.reload_section("duplicates")

            # CRITICAL: Refresh photo grid to show similar stack badges
            # Badges are only created when thumbnails are created, so we need to reload
            logger.info("[GooglePhotosLayout] Refreshing grid to show similar stack badges...")
            self._load_photos()

        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            import traceback
            error_msg = f"Failed to open similar photos dialog:\n{e}\n\n{traceback.format_exc()}"
            print(f"[GooglePhotosLayout] ERROR: {error_msg}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error Opening Similar Photos",
                f"Failed to open similar photos dialog:\n{e}"
            )

    def _on_show_duplicate_status(self):
        """
        Show duplicate detection status and statistics.

        Displays:
        - Exact duplicate counts
        - Similar stack counts
        - AI embedding coverage
        - Readiness for similarity detection
        """
        try:
            from PySide6.QtWidgets import QMessageBox
            from reference_db import ReferenceDB

            if self.project_id is None:
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project to view duplicate status."
                )
                return

            # Query database for duplicate stats
            db = ReferenceDB()

            with db._connect() as conn:
                cur = conn.cursor()

                # Exact duplicates (media assets)
                cur.execute("""
                    SELECT COUNT(*) FROM media_asset 
                    WHERE project_id = ? AND content_hash IS NOT NULL
                """, (self.project_id,))
                total_assets = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(DISTINCT content_hash) FROM media_asset 
                    WHERE project_id = ? AND content_hash IS NOT NULL
                """, (self.project_id,))
                unique_hashes = cur.fetchone()[0]

                exact_duplicates = total_assets - unique_hashes

                # Similar stacks
                cur.execute("""
                    SELECT COUNT(*) FROM media_stack 
                    WHERE project_id = ?
                """, (self.project_id,))
                similar_stacks = cur.fetchone()[0]

                # Photos with embeddings
                cur.execute("""
                    SELECT COUNT(DISTINCT se.photo_id)
                    FROM semantic_embeddings se
                    JOIN photo_metadata p ON se.photo_id = p.id
                    WHERE p.project_id = ?
                """, (self.project_id,))
                photos_with_embeddings = cur.fetchone()[0]

                # Total photos
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata WHERE project_id = ?
                """, (self.project_id,))
                total_photos = cur.fetchone()[0]

            embed_percent = (photos_with_embeddings / total_photos * 100) if total_photos > 0 else 0

            QMessageBox.information(
                self.main_window if hasattr(self, 'main_window') else None,
                "Duplicate Detection Status",
                f"=== Duplicate Detection Status ===\n\n"
                f"Exact Duplicates: {exact_duplicates:,}\n"
                f"Similar Photo Stacks: {similar_stacks:,}\n\n"
                f"=== AI Readiness ===\n"
                f"Photos with embeddings: {photos_with_embeddings:,} / {total_photos:,} ({embed_percent:.1f}%)\n\n"
                f"{'✓ Ready for similarity detection!' if photos_with_embeddings > 10 else 'Need more embeddings for similarity detection.'}"
            )

        except Exception as e:
            print(f"[GooglePhotosLayout] Error getting duplicate status: {e}")
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Status Error",
                f"Failed to get duplicate status:\n{str(e)}"
            )

    def _open_duplicates_dialog(self):
        """
        Open DuplicatesDialog to review and manage duplicate photos.

        This dialog allows users to:
        - View all duplicate photo groups
        - Compare instances side-by-side
        - Select duplicates for deletion
        - Set representative photos
        """
        try:
            from layouts.google_components.duplicates_dialog import DuplicatesDialog

            if self.project_id is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project before reviewing duplicates."
                )
                return

            # Open the dialog
            dialog = DuplicatesDialog(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Connect signal for refresh after actions
            dialog.duplicate_action_taken.connect(self._on_duplicate_action_taken)

            # Show the dialog
            dialog.exec()

        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            import traceback
            error_msg = f"Failed to open duplicates dialog:\n{e}\n\n{traceback.format_exc()}"
            print(f"[GooglePhotosLayout] ERROR: {error_msg}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error Opening Duplicates",
                f"Failed to open duplicates dialog:\n{e}"
            )

    def _open_similar_photos_dialog(self):
        """
        Open SimilarPhotoDetectionDialog to find visually similar photos.

        This dialog allows users to:
        - Configure similarity threshold and clustering parameters
        - Find visually similar photos using AI embeddings
        - Results are persisted to database and shown in sidebar
        """
        try:
            from ui.similar_photo_dialog import SimilarPhotoDetectionDialog

            if self.project_id is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project before finding similar photos."
                )
                return

            # Open the dialog
            dialog = SimilarPhotoDetectionDialog(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Show the dialog
            result = dialog.exec()

            # Refresh sidebar duplicates section after dialog closes
            if hasattr(self, 'accordion_sidebar') and self.accordion_sidebar:
                self.accordion_sidebar.reload_section("duplicates")

            # CRITICAL: Refresh photo grid to show similar stack badges
            logger.info("[GooglePhotosLayout] Refreshing grid to show similar stack badges...")
            self._load_photos()

        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            import traceback
            error_msg = f"Failed to open similar photos dialog:\n{e}\n\n{traceback.format_exc()}"
            print(f"[GooglePhotosLayout] ERROR: {error_msg}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error Opening Similar Photos",
                f"Failed to open similar photos dialog:\n{e}"
            )

    def _toggle_info_panel(self, checked: bool = None):
        """
        Toggle the Metadata Editor dock panel (Lightroom-style Info panel).

        This provides access to:
        - Rating (0-5 stars)
        - Flag (Pick/Reject)
        - Title and Caption
        - Keywords (Tags)
        - Date/Location (read-only with override)
        - File Info

        Changes are stored in database first (non-destructive).
        Optional XMP sidecar export available.
        """
        main_window = getattr(self, 'main_window', None)
        if not main_window:
            return

        dock = getattr(main_window, 'metadata_editor_dock', None)
        if not dock:
            return

        if checked is None:
            checked = not dock.isVisible()

        dock.setVisible(checked)

        # Sync button state
        if hasattr(self, 'btn_info') and self.btn_info.isChecked() != checked:
            self.btn_info.setChecked(checked)

        # If showing and a photo is selected, load its metadata
        if checked:
            selected_paths = self.get_selected_paths()
            if selected_paths:
                path = selected_paths[0]
                photo_id = self._get_photo_id_for_path(path)
                if photo_id:
                    main_window.show_metadata_for_photo(photo_id, path)

    def _get_photo_id_for_path(self, path: str) -> int:
        """Get photo ID from database for a given path."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?
                """, (path, self.project_id))
                row = cursor.fetchone()
                if row:
                    return row['id']
                # Try case-insensitive match as fallback
                cursor = conn.execute("""
                    SELECT id FROM photo_metadata WHERE LOWER(path) = LOWER(?) AND project_id = ?
                """, (path, self.project_id))
                row = cursor.fetchone()
                if row:
                    print(f"[GooglePhotosLayout] Found photo_id via case-insensitive match for: {path}")
                    return row['id']
                print(f"[GooglePhotosLayout] ⚠️ No photo_metadata row found for path: {path}")
                return None
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error getting photo ID for {path}: {e}")
            return None

    def _show_metadata_editor_for_photo(self, path: str):
        """Show the metadata editor dock for a specific photo (triggered from right-click menu)."""
        print(f"[GooglePhotosLayout] Opening metadata editor for: {path}")
        main_window = getattr(self, 'main_window', None)
        if not main_window:
            print("[GooglePhotosLayout] ⚠️ Cannot open metadata editor: main_window not found")
            return

        dock = getattr(main_window, 'metadata_editor_dock', None)
        if not dock:
            print("[GooglePhotosLayout] ⚠️ Cannot open metadata editor: metadata_editor_dock not found")
            return

        photo_id = self._get_photo_id_for_path(path)
        if not photo_id:
            print(f"[GooglePhotosLayout] ⚠️ Cannot open metadata editor: no photo_id found for path: {path}")
            return

        print(f"[GooglePhotosLayout] ✓ Opening metadata editor for photo_id={photo_id}")
        main_window.show_metadata_for_photo(photo_id, path)

    def _on_duplicate_action_taken(self, action: str, asset_id: int):
        """
        Handle actions taken in DuplicatesDialog.

        Args:
            action: Action type ("delete", "set_representative", etc.)
            asset_id: ID of asset that was modified
        """
        print(f"[GooglePhotosLayout] Duplicate action taken: {action} on asset {asset_id}")

        # Refresh the photo grid to remove deleted photos
        if action == "delete":
            print(f"[GooglePhotosLayout] Refreshing grid after deletion...")
            # Reload photos from database to remove deleted photos from view
            self._load_photos(thumb_size=self.current_thumb_size)

    def _on_stack_badge_clicked(self, stack_id: int):
        """
        Handle click on stack badge overlay.

        Opens StackViewDialog to show all members of the stack.

        Args:
            stack_id: Stack ID to display
        """
        try:
            from layouts.google_components.stack_view_dialog import StackViewDialog

            if self.project_id is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project before viewing stacks."
                )
                return

            # Open the stack view dialog
            dialog = StackViewDialog(
                project_id=self.project_id,
                stack_id=stack_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Connect signal for refresh after actions
            dialog.stack_action_taken.connect(self._on_stack_action_taken)

            # Show the dialog
            dialog.exec()

        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            import traceback
            error_msg = f"Failed to open stack view:\n{e}\n\n{traceback.format_exc()}"
            print(f"[GooglePhotosLayout] ERROR: {error_msg}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error Opening Stack View",
                f"Failed to open stack view:\n{e}"
            )

    def _on_stack_action_taken(self, action: str, stack_id: int):
        """
        Handle actions taken in StackViewDialog.

        Args:
            action: Action type ("delete", "unstack", etc.)
            stack_id: ID of stack that was modified
        """
        print(f"[GooglePhotosLayout] Stack action taken: {action} on stack {stack_id}")

        # Refresh view to show updated state
        if action in ["delete", "unstack"]:
            # Reload the current view to reflect deletions
            self._load_photos(thumb_size=self.current_thumb_size)

    def _on_duplicate_badge_clicked(self, asset_id: int):
        """
        Handle click on duplicate badge overlay.

        Opens DuplicatesDialog with the specific asset pre-selected.

        Args:
            asset_id: Asset ID to display duplicates for
        """
        try:
            from layouts.google_components.duplicates_dialog import DuplicatesDialog

            if self.project_id is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self.main_window if hasattr(self, 'main_window') else None,
                    "No Project Selected",
                    "Please select a project before viewing duplicates."
                )
                return

            print(f"[GooglePhotosLayout] Opening duplicates dialog for asset {asset_id}")

            # Open the duplicates dialog
            dialog = DuplicatesDialog(
                project_id=self.project_id,
                parent=self.main_window if hasattr(self, 'main_window') else None
            )

            # Connect signal for refresh after actions
            dialog.duplicate_action_taken.connect(self._on_duplicate_action_taken)

            # Pre-select the specific asset (scrolls to and highlights it)
            dialog.select_asset(asset_id)

            # Show the dialog
            dialog.exec()

        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            import traceback
            error_msg = f"Failed to open duplicates dialog:\n{e}\n\n{traceback.format_exc()}"
            print(f"[GooglePhotosLayout] ERROR: {error_msg}")
            QMessageBox.critical(
                self.main_window if hasattr(self, 'main_window') else None,
                "Error Opening Duplicates",
                f"Failed to open duplicates dialog:\n{e}"
            )

    def get_sidebar(self):
        """Get sidebar component."""
        return getattr(self, 'sidebar', None)

    def get_grid(self):
        """Grid is integrated into timeline view."""
        return None

    def _on_view_tab_changed(self, index: int):
        tab_text = self.view_tabs.tabText(index)
        if "Photos" in tab_text:
            self._show_timeline_view()
        else:
            if "Favorites" in tab_text:
                self._filter_by_tag("favorite")

    def _filter_favorites(self):
        """Filter timeline to show only photos tagged as favorites."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.cursor()
                query = """
                    SELECT DISTINCT pm.path, COALESCE(pm.date_taken, pm.created_date) as date_taken
                    FROM photo_metadata pm
                    JOIN project_images pi ON pm.path = pi.image_path
                    JOIN tags t ON t.name = ? AND t.project_id = ?
                    JOIN photo_tags pt ON pt.tag_id = t.id AND pt.photo_id = pm.id
                    WHERE pi.project_id = ?
                    ORDER BY date_taken DESC
                """
                cur.execute(query, ("favorite", self.project_id, self.project_id))
                rows = cur.fetchall()
            self._rebuild_timeline_with_results(rows, "Favorites")
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error filtering favorites: {e}")
    def _set_photos_mode(self, mode: str):
        self.view_mode = mode

    def _show_grid_view(self):
        self._set_photos_mode('grid')
        # TODO: Implement dedicated grid renderer; reload for now
        self._load_photos(thumb_size=getattr(self, 'current_thumb_size', 200))

    def _show_timeline_view(self):
        self._set_photos_mode('timeline')
        self._load_photos(thumb_size=getattr(self, 'current_thumb_size', 200))

    def _show_single_view(self):
        self._set_photos_mode('single')
        try:
            paths = self._get_all_media_paths()
            if not paths:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self.main_window, "Single View", "No media available.")
                return
            lightbox = MediaLightbox(
                paths[0], paths, parent=self.main_window,
                project_id=self.project_id,
            )
            lightbox.exec()
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error opening single view: {e}")
            
    def on_layout_activated(self):
        """Called when this layout becomes active."""
        print("[GooglePhotosLayout] Layout activated")

        if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
            self.google_shell_sidebar.set_project_available(bool(getattr(self, "project_id", None)))

        # Store main_window method references for Settings menu
        if hasattr(self.main_window, '_on_scan_repository'):
            self._scan_repository_handler = self.main_window._on_scan_repository

        if hasattr(self.main_window, '_on_detect_and_group_faces'):
            self._detect_faces_handler = self.main_window._on_detect_and_group_faces

        # Flush any pending UI refresh requests that were deferred while this
        # layout was hidden (e.g. face detection finished during layout switch)
        mediator = getattr(self.main_window, '_ui_refresh_mediator', None)
        if mediator:
            mediator.on_layout_activated("google")

        # Recompute grid columns after the widget geometry has settled.
        # During create_layout() the viewport is narrow (not yet shown);
        # by the time the event loop returns here the true width is known.
        QTimer.singleShot(0, self._recheck_column_count)

    def _recheck_column_count(self):
        """Recompute columns now that the viewport has its real width.

        If the column count changed from what was used during the initial
        _load_photos() call (widget not yet visible), re-trigger the load
        so grids are laid out correctly.
        """
        if not getattr(self, 'project_id', None):
            return
        thumb_size = getattr(self, 'current_thumb_size', 200)
        old_cols = getattr(self, '_last_column_count', None)
        new_cols = self._calculate_responsive_columns(thumb_size)
        if old_cols is not None and new_cols != old_cols:
            print(f"[GooglePhotosLayout] Column count changed {old_cols} -> {new_cols}, refreshing grid")
            self._load_photos(
                thumb_size=thumb_size,
                filter_year=getattr(self, 'current_filter_year', None),
                filter_month=getattr(self, 'current_filter_month', None),
                filter_day=getattr(self, 'current_filter_day', None),
                filter_folder=getattr(self, 'current_filter_folder', None),
                filter_person=getattr(self, 'current_filter_person', None),
                filter_paths=getattr(self, 'current_filter_paths', None),
            )

    def on_layout_deactivated(self):
        """
        CRITICAL FIX: Called when layout is being switched or destroyed.
        Cleans up all resources to prevent memory leaks.
        """
        print("[GooglePhotosLayout] 🧹 Layout deactivated - starting cleanup...")
        self.cleanup()
        print("[GooglePhotosLayout] ✓ Cleanup complete")

    def cleanup(self):
        """
        CRITICAL FIX: Comprehensive resource cleanup to prevent memory leaks.

        Addresses audit findings:
        - Issue #1: 173 signal connections never disconnected
        - Issue #2: 8 event filters never removed
        - Issue #3: 47 timers never stopped
        - Issue #4: Thread pool never cleaned up
        - Issue #7: Unbounded pixmap cache
        """
        print("[GooglePhotosLayout] Cleaning up resources...")

        # 0a. Unsubscribe from ProjectState store
        if hasattr(self, '_store_unsub') and self._store_unsub:
            self._store_unsub()
            self._store_unsub = None

        # 0b. Mark accordion sidebar as disposed so background workers skip stale refreshes
        if hasattr(self, 'accordion_sidebar') and self.accordion_sidebar:
            if hasattr(self.accordion_sidebar, 'cleanup'):
                self.accordion_sidebar.cleanup()

        # 1. Disconnect all signals (CRITICAL - prevents 173 connection leak)
        self._disconnect_all_signals()

        # 2. Remove event filters (CRITICAL - prevents 8 filter leak)
        self._remove_event_filters()

        # 3. Stop all timers (CRITICAL - prevents timer crash after deletion)
        self._stop_all_timers()

        # 4. Stop thread pools (CRITICAL - prevents background thread leak)
        self._cleanup_thread_pools()

        # 5. Clear caches (HIGH - prevents unbounded memory growth)
        self._clear_caches()

        # 6. Clean up child widgets with animations
        self._stop_animations()

        # 7. Call parent cleanup
        if hasattr(super(), 'cleanup'):
            super().cleanup()

    def _disconnect_all_signals(self):
        """Disconnect all signal connections to prevent memory leaks."""
        print("[GooglePhotosLayout]   ↳ Disconnecting signals...")

        # Thumbnail loading signals
        if hasattr(self, 'thumbnail_signals'):
            try:
                self.thumbnail_signals.loaded.disconnect(self._on_thumbnail_loaded)
            except:
                pass



        # Zoom slider signals
        if hasattr(self, 'zoom_slider'):
            try:
                self.zoom_slider.valueChanged.disconnect(self._on_zoom_changed)
            except:
                pass

        # Project combo signals
        if hasattr(self, 'project_combo'):
            try:
                self.project_combo.currentIndexChanged.disconnect(self._on_project_changed)
            except:
                pass

        # Search state signals
        if hasattr(self.main_window, 'search_state_store'):
            try:
                self.main_window.search_state_store.stateChanged.disconnect(self._on_search_state_changed)
            except:
                pass

        # Scroll area signals
        if hasattr(self, 'timeline_scroll'):
            try:
                self.timeline_scroll.verticalScrollBar().valueChanged.disconnect(self._on_scroll)
            except:
                pass

        print("[GooglePhotosLayout]   ✓ Signals disconnected")

    def _remove_event_filters(self):
        """Remove all event filters to prevent memory leaks."""
        print("[GooglePhotosLayout]   ↳ Removing event filters...")

        # Timeline scroll viewport filter
        if hasattr(self, 'timeline_scroll') and hasattr(self, 'event_filter'):
            try:
                self.timeline_scroll.viewport().removeEventFilter(self.event_filter)
            except:
                pass



        # People search filter
        if hasattr(self, 'people_search') and hasattr(self, 'autocomplete_event_filter'):
            try:
                self.people_search.removeEventFilter(self.autocomplete_event_filter)
            except:
                pass

        print("[GooglePhotosLayout]   ✓ Event filters removed")

    def _stop_all_timers(self):
        """Stop all QTimer instances to prevent crashes after widget deletion."""
        print("[GooglePhotosLayout]   ↳ Stopping timers...")

        timer_names = [
            'scroll_debounce_timer',
            'date_indicator_hide_timer',
            '_search_timer',
            '_autosave_timer',
            '_adjust_debounce_timer'
        ]

        for timer_name in timer_names:
            if hasattr(self, timer_name):
                timer = getattr(self, timer_name)
                if timer:
                    try:
                        timer.stop()
                        timer.deleteLater()
                    except:
                        pass

        print("[GooglePhotosLayout]   ✓ Timers stopped")

    def _cleanup_thread_pools(self):
        """Clean up thread pools to prevent background thread leaks."""
        print("[GooglePhotosLayout]   ↳ Cleaning up thread pools...")

        if hasattr(self, 'thumbnail_thread_pool'):
            try:
                self.thumbnail_thread_pool.clear()
                self.thumbnail_thread_pool.waitForDone(2000)  # Wait max 2 seconds
            except:
                pass

        print("[GooglePhotosLayout]   ✓ Thread pools cleaned")

    def _clear_caches(self):
        """Clear all caches to prevent unbounded memory growth."""
        print("[GooglePhotosLayout]   ↳ Clearing caches...")

        # Clear thumbnail button cache
        if hasattr(self, 'thumbnail_buttons'):
            for btn in list(self.thumbnail_buttons.values()):
                try:
                    btn.deleteLater()
                except:
                    pass
            self.thumbnail_buttons.clear()

        # Clear unloaded thumbnails cache
        if hasattr(self, 'unloaded_thumbnails'):
            self.unloaded_thumbnails.clear()

        print("[GooglePhotosLayout]   ✓ Caches cleared")

    def _stop_animations(self):
        """Stop all animations in child widgets (CollapsibleSection)."""
        print("[GooglePhotosLayout]   ↳ Stopping animations...")

        # Find all CollapsibleSection widgets and stop their animations
        section_names = ['timeline_section', 'folders_section', 'people_section', 'videos_section']

        for section_name in section_names:
            if hasattr(self, section_name):
                section = getattr(self, section_name)
                if hasattr(section, 'cleanup'):
                    try:
                        section.cleanup()
                    except:
                        pass

        print("[GooglePhotosLayout]   ✓ Animations stopped")

    def _on_create_project_clicked(self):
        """Handle Create Project button click."""
        print("[GooglePhotosLayout] 🆕🆕🆕 CREATE PROJECT BUTTON CLICKED! 🆕🆕🆕")

        # Debug: Check if main_window exists and has breadcrumb_nav
        if not hasattr(self, 'main_window'):
            print("[GooglePhotosLayout] ❌ ERROR: self.main_window does not exist!")
            return

        # CRITICAL FIX: _create_new_project is in BreadcrumbNavigation, not MainWindow!
        # MainWindow has self.breadcrumb_nav which contains the method
        if not hasattr(self.main_window, 'breadcrumb_nav'):
            print(f"[GooglePhotosLayout] ❌ ERROR: main_window does not have breadcrumb_nav!")
            return

        if not hasattr(self.main_window.breadcrumb_nav, '_create_new_project'):
            print(f"[GooglePhotosLayout] ❌ ERROR: breadcrumb_nav does not have _create_new_project method!")
            return

        print("[GooglePhotosLayout] ✓ Calling breadcrumb_nav._create_new_project()...")

        # Call BreadcrumbNavigation's project creation dialog
        self.main_window.breadcrumb_nav._create_new_project()

        print("[GooglePhotosLayout] ✓ Project creation dialog completed")

        # CRITICAL: Update project_id after creation
        from app_services import get_default_project_id
        new_project_id = get_default_project_id()
        print(f"[GooglePhotosLayout] Updated project_id: {new_project_id}")

        # Refresh project selector first
        self._populate_project_selector()

        # Delegate to set_project (single owner of project-bound loading)
        if new_project_id is not None:
            self.set_project(new_project_id)
        print("[GooglePhotosLayout] ✓ Layout refreshed after project creation")
        self._refresh_passive_browse_payload()

    def _populate_project_selector(self):
        """
        Populate the project selector combobox with available projects.
        Google Photos pattern: "+ New Project..." as first item.
        """
        try:
            from app_services import list_projects
            projects = list_projects()

            # Block signals while updating to prevent triggering change handler
            self.project_combo.blockSignals(True)
            self.project_combo.clear()

            # Google Photos pattern: Add "+ New Project..." as first item
            self.project_combo.addItem("➕ New Project...", userData="__new_project__")

            # Add separator after "New Project" option
            self.project_combo.insertSeparator(1)

            if not projects:
                self.project_combo.addItem("(No projects)", None)
                # Still enable dropdown so user can create new project
                self.project_combo.setEnabled(True)
            else:
                for proj in projects:
                    self.project_combo.addItem(proj["name"], proj["id"])
                self.project_combo.setEnabled(True)

                # Select current project (skip index 0 and 1 which are "+ New" and separator)
                if self.project_id:
                    for i in range(2, self.project_combo.count()):  # Start from index 2
                        if self.project_combo.itemData(i) == self.project_id:
                            self.project_combo.setCurrentIndex(i)
                            break
                else:
                    # If no project_id, select first actual project (index 2)
                    if self.project_combo.count() > 2:
                        self.project_combo.setCurrentIndex(2)

            # Unblock signals and connect change handler
            self.project_combo.blockSignals(False)
            try:
                self.project_combo.currentIndexChanged.disconnect()
            except:
                pass  # No previous connection
            self.project_combo.currentIndexChanged.connect(self._on_project_changed)

            print(f"[GooglePhotosLayout] Project selector populated with {len(projects)} projects (+ New Project option)")

        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error populating project selector: {e}")

    # ── Phase 2A: Reload gate helpers ────────────────────────────────────

    # Valid keyword arguments for _load_photos()
    _LOAD_PHOTOS_KWARGS = frozenset({
        'thumb_size', 'filter_year', 'filter_month', 'filter_day',
        'filter_folder', 'filter_person', 'filter_paths',
    })

    def request_reload(self, reason: str = "unknown", **kwargs):
        """Schedule a debounced photo reload, coalescing rapid requests.

        kwargs may include metadata (e.g. project_id) for the dedup
        signature.  Only _load_photos-compatible keys are forwarded.
        """
        self._pending_reload_reason = (reason, self.project_id)
        # Keep only kwargs that _load_photos actually accepts
        self._pending_reload_kwargs = {
            k: v for k, v in kwargs.items() if k in self._LOAD_PHOTOS_KWARGS
        }
        self._reload_debounce_timer.stop()
        self._reload_debounce_timer.start()

    def _execute_debounced_reload(self):
        """Fire the debounced reload if conditions are met."""
        if self._reload_in_progress:
            return

        signature = getattr(self, '_pending_reload_reason', None)
        if signature == self._last_reload_signature:
            print(f"[GooglePhotosLayout] Skipping duplicate reload: {signature}")
            return

        if not getattr(self, 'project_id', None):
            print("[GooglePhotosLayout] Suppressing reload — no active project")
            return

        self._reload_in_progress = True
        try:
            self._last_reload_signature = signature
            print(f"[GooglePhotosLayout] Executing debounced reload: {signature[0] if signature else 'unknown'}")
            self._load_photos(**self._pending_reload_kwargs)
        finally:
            self._reload_in_progress = False

    # ── Project lifecycle ─────────────────────────────────────────────────

    def _on_project_changed(self, index: int):
        """
        Handle project selection change in combobox.
        Detects "+ New Project..." selection and opens create dialog.
        """
        new_project_id = self.project_combo.itemData(index)

        # Check if user selected "+ New Project..." option
        if new_project_id == "__new_project__":
            print("[GooglePhotosLayout] ➕ New Project option selected")

            # Block signals to prevent recursion
            self.project_combo.blockSignals(True)

            # Restore previous selection (don't stay on "+ New Project")
            if self.project_id:
                for i in range(2, self.project_combo.count()):
                    if self.project_combo.itemData(i) == self.project_id:
                        self.project_combo.setCurrentIndex(i)
                        break
            else:
                # If no current project, select first actual project
                if self.project_combo.count() > 2:
                    self.project_combo.setCurrentIndex(2)

            # Unblock signals
            self.project_combo.blockSignals(False)

            # Open project creation dialog
            self._on_create_project_clicked()
            return

        # Normal project change: delegate to set_project
        if new_project_id is None or new_project_id == self.project_id:
            return
        self.set_project(new_project_id)

    def set_project(self, project_id):
        """
        Public API for external project switching.
        Called by ProjectController or combobox when user changes project.
        Phase 2A: This is the sole owner of project-bound loading.
        """
        if project_id is not None and project_id == self.project_id:
            print(f"[GooglePhotosLayout] set_project() called: already on project {project_id}, skipping")
            return

        print(f"[GooglePhotosLayout] set_project() called: {self.project_id} -> {project_id}")

        if self._project_switch_in_progress:
            self._pending_project_reload = True
            return

        self._project_switch_in_progress = True
        try:
            self.project_id = project_id
            self._last_load_signature = None  # invalidate on project change

            if hasattr(self, "google_shell_sidebar") and self.google_shell_sidebar:
                self.google_shell_sidebar.set_project_available(bool(project_id))

            if project_id:
                self._clear_shell_state_text()
            self._refresh_legacy_visibility_state()

            # Update accordion sidebar with new project
            if hasattr(self, 'accordion_sidebar') and self.accordion_sidebar is not None:
                try:
                    if hasattr(self.accordion_sidebar, 'set_project'):
                        self.accordion_sidebar.set_project(project_id)
                    elif hasattr(self.accordion_sidebar, 'set_project_id'):
                        self.accordion_sidebar.set_project_id(project_id)
                    elif hasattr(self.accordion_sidebar, 'switch_project'):
                        self.accordion_sidebar.switch_project(project_id)
                except Exception as e:
                    print(f"[GooglePhotosLayout] Accordion project sync failed: {e}")

            # Update project combo box to match (if it exists)
            if hasattr(self, 'project_combo'):
                self.project_combo.blockSignals(True)
                for i in range(self.project_combo.count()):
                    if self.project_combo.itemData(i) == project_id:
                        self.project_combo.setCurrentIndex(i)
                        break
                self.project_combo.blockSignals(False)

            if project_id is None:
                print("[GooglePhotosLayout] ⚠️ No project selected — skip loading")
                return

            self.request_reload(reason="project_switch", project_id=project_id)
            self._refresh_passive_browse_payload()

        finally:
            self._project_switch_in_progress = False

        if self._pending_project_reload:
            self._pending_project_reload = False
            self.request_reload(reason="project_switch_followup", project_id=self.project_id)

    # ========== PHASE 3 Task 3.1: BaseLayout Interface Implementation ==========

    def get_current_project(self) -> Optional[int]:
        """
        Get currently displayed project ID.

        Returns:
            int: Current project ID, or None if no project loaded
        """
        return self.project_id

    def refresh_after_scan(self) -> None:
        """
        Reload photos after scan completes.

        Called via store subscription when media_v changes.
        AccordionSidebar handles its own section reloads via
        its own store subscription (media_v, duplicates_v, people_v).
        """
        # ── Project-ID recovery ──────────────────────────────────
        # If layout was initialised before any project existed (project_id=None),
        # the sidebar sections all bail early.  Now that a scan has run, a
        # project must exist — resolve and propagate so sections can load.
        if self.project_id is None:
            from app_services import get_default_project_id, list_projects
            self.project_id = get_default_project_id()
            if self.project_id is None:
                projects = list_projects()
                if projects:
                    self.project_id = projects[0]["id"]
            if self.project_id is not None:
                print(f"[GooglePhotosLayout] project_id recovered: {self.project_id}")
                sidebar = getattr(self, 'sidebar', None)
                if sidebar and hasattr(sidebar, 'set_project'):
                    sidebar.set_project(self.project_id)

        if not getattr(self, 'project_id', None):
            print("[GooglePhotosLayout] refresh_after_scan: no project — suppressing load")
            return

        # Invalidate signature so scan refresh always executes
        self._last_load_signature = None
        self._last_reload_signature = None
        # Reload photos with ALL current filters (including filter_paths)
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=self.current_filter_year,
            filter_month=self.current_filter_month,
            filter_day=self.current_filter_day,
            filter_folder=self.current_filter_folder,
            filter_person=self.current_filter_person,
            filter_paths=getattr(self, 'current_filter_paths', None),
        )

    def refresh_thumbnails(self) -> None:
        """
        Reload thumbnails without requerying database.

        Called when thumbnail cache is cleared or window is resized.
        """
        # Clear thumbnail cache
        self.thumbnail_buttons.clear()

        # Reload with current filters
        self.refresh_after_scan()

    def filter_by_date(self, year: Optional[int] = None,
                      month: Optional[int] = None,
                      day: Optional[int] = None) -> None:
        """
        Filter displayed items by date.

        Args:
            year: Year filter (e.g., 2024), or None for all years
            month: Month filter (1-12), requires year
            day: Day filter (1-31), requires year and month
        """
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=year,
            filter_month=month,
            filter_day=day,
            filter_folder=None,  # Clear folder filter when filtering by date
            filter_person=None   # Clear person filter when filtering by date
        )

    def filter_by_folder(self, folder_path: str) -> None:
        """
        Filter displayed items by folder.

        Args:
            folder_path: Folder path string from folder_hierarchy table
        """
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=None,    # Clear date filters when filtering by folder
            filter_month=None,
            filter_day=None,
            filter_folder=folder_path,
            filter_person=None
        )

    def filter_by_person(self, person_branch_key: str) -> None:
        """
        Filter displayed items by person (face cluster).

        Args:
            person_branch_key: Person identifier from face_crops table
        """
        self._load_photos(
            thumb_size=self.current_thumb_size,
            filter_year=None,    # Clear date/folder filters when filtering by person
            filter_month=None,
            filter_day=None,
            filter_folder=None,
            filter_person=person_branch_key
        )

    def filter_by_paths(self, paths: list, navigation_mode: str = "group") -> None:
        """Filter the grid to show only the given photo paths.

        Used by SidebarController.on_group_selected() to display group
        match photos in the main grid.
        """
        if not paths:
            self._clear_filter()
            return
        self._request_load(paths=paths)

    def clear_filters(self) -> None:
        """
        Remove all active filters and show all items.

        Delegates to existing _clear_filter() method.
        """
        self._clear_filter()

    def get_selected_paths(self) -> list:
        """
        Get list of currently selected file paths.

        Returns:
            list[str]: Absolute paths to selected photos/videos
        """
        return list(self.selected_photos)

    def clear_selection(self) -> None:
        """
        Deselect all items.

        Delegates to existing _clear_selection() method.
        """
        self._clear_selection()

    # ============ PHASE 3: Tag Operations ============

    def _toggle_favorite_single(self, path: str):
        """
        Toggle favorite status for a single photo (context menu action).
        """
        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            
            current_tags = tag_service.get_tags_for_path(path, self.project_id) or []
            is_favorited = any(t.lower() == "favorite" for t in current_tags)
            
            if is_favorited:
                tag_service.remove_tag(path, "favorite", self.project_id)
                msg = f"⭐ Removed from favorites: {os.path.basename(path)}"
                print(f"[GooglePhotosLayout] Unfavorited: {os.path.basename(path)}")
            else:
                tag_service.assign_tags_bulk([path], "favorite", self.project_id)
                msg = f"⭐ Added to favorites: {os.path.basename(path)}"
                print(f"[GooglePhotosLayout] Favorited: {os.path.basename(path)}")
            
            # Refresh tag overlay for this photo
            self._refresh_tag_overlays([path])
            
            # Show status message
            if hasattr(self.main_window, 'statusBar'):
                self.main_window.statusBar().showMessage(msg, 3000)
        
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error toggling favorite: {e}")
            import traceback
            traceback.print_exc()
    
    def _add_tag_to_photo(self, path: str):
        """
        Add a custom tag to a single photo (context menu action).
        
        ARCHITECTURE: Uses TagService layer (Schema v3.1.0) instead of direct ReferenceDB calls.
        This ensures proper photo_metadata creation and tag isolation.
        
        Args:
            path: Photo file path
        """
        from PySide6.QtWidgets import QInputDialog
        
        tag_name, ok = QInputDialog.getText(
            self.main_window,
            "Add Tag",
            "Enter tag name:",
            QLineEdit.Normal,
            ""
        )
        
        if ok and tag_name.strip():
            try:
                # ARCHITECTURE: Use TagService layer (matches Current layout approach)
                from services.tag_service import get_tag_service
                tag_service = get_tag_service()
                
                # Ensure tag exists and assign to photo
                tag_service.ensure_tag_exists(tag_name.strip(), self.project_id)
                count = tag_service.assign_tags_bulk([path], tag_name.strip(), self.project_id)
                
                if count > 0:
                    msg = f"🏷️ Tagged '{tag_name.strip()}': {os.path.basename(path)}"
                    print(f"[GooglePhotosLayout] {msg}")
                    
                    # Refresh tag overlay
                    self._refresh_tag_overlays([path])
                    
                    # Show success message
                    if hasattr(self.main_window, 'statusBar'):
                        self.main_window.statusBar().showMessage(msg, 3000)
                else:
                    # Tag assignment failed
                    error_msg = f"⚠️ Failed to add tag '{tag_name.strip()}' to {os.path.basename(path)}"
                    print(f"[GooglePhotosLayout] {error_msg}")
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self.main_window,
                        "Tag Failed",
                        f"Failed to add tag '{tag_name.strip()}'.\n\nThe photo may not exist in the database or the tag could not be created."
                    )
            
            except Exception as e:
                print(f"[GooglePhotosLayout] ⚠️ Error adding tag: {e}")
                import traceback
                traceback.print_exc()
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    self.main_window,
                    "Tag Failed",
                    f"Failed to add tag:\n{str(e)}"
                )
    
    # NOTE: Duplicate _show_photo_context_menu removed - see line ~13711 for merged implementation
    # NOTE: Typo method _refresh_tag_ovverlays removed - use _refresh_tag_overlays instead

    def _on_tags_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu, QInputDialog, QMessageBox
        menu = QMenu(self.tags_tree)
        act_new = menu.addAction("➕ New Tag…")
        chosen = menu.exec(self.tags_tree.viewport().mapToGlobal(pos))
        if chosen is act_new:
            name, ok = QInputDialog.getText(self.main_window, "New Tag", "Tag name:")
            if ok and name.strip():
                try:
                    from services.tag_service import get_tag_service
                    tag_service = get_tag_service()
                    tag_service.ensure_tag_exists(name.strip(), self.project_id)
                    self._build_tags_tree()
                except Exception as e:
                    QMessageBox.critical(self.main_window, "Create Failed", str(e))

    def _refresh_tag_overlays(self, paths: List[str]):
        """
        Refresh tag badge overlays for given photos.
        
        ARCHITECTURE: Uses TagService layer (Schema v3.1.0) for proper data access.
        Updates PhotoButton's painted badges and triggers repaint.
        
        Args:
            paths: List of photo paths to refresh
        """
        try:
            # ARCHITECTURE: Use TagService layer (matches Current layout approach)
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            
            # Bulk query tags for all paths (more efficient than individual queries)
            tags_map = tag_service.get_tags_for_paths(paths, self.project_id)
            
            for path in paths:
                # Find the PhotoButton for this path
                button = self.thumbnail_buttons.get(path)
                if not button or not isinstance(button, PhotoButton):
                    continue
                
                # Get tags from the bulk query result
                tags = tags_map.get(path, [])
                
                # Update button's tags (triggers automatic repaint)
                button.set_tags(tags)
            
            print(f"[GooglePhotosLayout] ✓ Refreshed tag badges for {len(paths)} photos")
            
            # Also refresh Favorites sidebar section
            try:
                self._build_tags_tree()
            except Exception as e:
                print(f"[GooglePhotosLayout] ⚠️ Could not refresh tags section: {e}")
        
        except Exception as e:
            print(f"[GooglePhotosLayout] ⚠️ Error refreshing tag overlays: {e}")
            import traceback
            traceback.print_exc()
