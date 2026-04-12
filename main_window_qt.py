# main_window_qt.py
# Version 10.01.01.08 dated 20260217
# Added PhotoDeletionService with comprehensive delete functionality
# Enhanced repositories with utility methods for future migrations
# Current LOC: ~2,640 (added photo deletion feature)

# [ Tree View  ]
#     │
#     ▼
#  photo_folders  ⇨  loads folder → retrieves photos
#     │
#     ▼
#  photo_metadata → thumbnail grid, details panel
#
#
# 🧭 3. Suggested Database Schema
#  🗃️ photo_folders
#  id	parent_id	path	name
#  1	NULL	/repo/2022	2022
#  2	1	/repo/2022/family	family
#  3	1	/repo/2022/vacation	vacation
#  🗃️ photo_metadata
#  path	folder_id	size_kb	modified	width	height	embedding	date_taken	tags
#  /repo/2022/family/img1.jpg	2	3200	2024-12-05 12:34	4000	3000	… blob …	2024-05-04	family,baby
#
#  👉 folder_id gives fast tree navigation
#  👉 tags, embedding, and date_taken allow smart sorting later.
#
#  🧭 2. Recommended Directory Scanning Strategy
#  👉 We don't want to re-scan the entire repository every time.
#  👉 We should scan once and index the structure in the database.
#
#  🧰 Step-by-step:
#  Recursive scan using os.walk() or pathlib.Path.rglob('*')
#
#  For each file:
#  Check file type (image formats: .jpg, .png, .heic, .webp, .tif, etc.)
#  Get basic metadata (size, modified date, dimensions if needed)
#  Save to photo_metadata table
#  For each folder:
#  Save a reference to photo_folders table (for tree view)
#  Mark parent–child relationship for UI navigation
#  Store a hash or last_modified so we can incrementally update later.

from splash_qt import SplashScreen, StartupWorker
import os, platform, traceback, time as _time, logging, json
from app_env import APP_DIR, app_path
from thumb_cache_db import get_cache

from db_writer import DBWriter
from typing import Iterable, Optional, Dict, Tuple

# ✅ NEW: Import service-based ScanWorker
from services.scan_worker_adapter import ScanWorkerAdapter as ScanWorker

logger = logging.getLogger(__name__)

# Add imports near top if not present:

from PySide6.QtCore import Qt, QThread, QSize, QThreadPool, Signal, QObject, QRunnable, QEvent, QTimer, QProcess, QItemSelectionModel, QRect

from PySide6.QtGui import QPixmap, QImage, QImageReader, QAction, QActionGroup, QIcon, QTransform, QPalette, QColor, QGuiApplication, QShortcut, QKeySequence

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter,
    QHBoxLayout, QVBoxLayout, QLabel,
    QComboBox, QSizePolicy, QToolBar, QMessageBox,
    QDialog, QPushButton, QFileDialog, QScrollArea,
    QCheckBox, QComboBox as QSortComboBox,
    QProgressDialog, QProgressBar, QApplication, QStyle,
    QDialogButtonBox, QMenu, QGroupBox, QFrame,
    QSlider, QFormLayout, QTextEdit, QButtonGroup, QLineEdit
)


from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from collections import deque


from PIL import Image, ImageEnhance, ImageQt, ImageOps, ExifTags
# Optional HEIF/HEIC support (non-fatal)
try:
    import pillow_heif  # if installed, Pillow can open HEIC/HEIF
    print("[Startup] pillow_heif available — HEIC/HEIF support enabled.")
except Exception:
    # fine if missing; HEIC files will be skipped unless plugin installed
    pass

from sidebar_qt import SidebarQt

from thumbnail_grid_qt import ThumbnailGridQt

# 🎬 Phase 4.4: Video player support
from video_player_qt import VideoPlayerPanel
from thumbnail_grid_qt import is_video_file

# Layout system for UI/UX switching
from layouts.layout_manager import LayoutManager

# Phase 1 Refactoring: Extracted UI panels
from ui.panels.details_panel import DetailsPanel
from ui.panels.backfill_status_panel import BackfillStatusPanel

# Phase 1 Refactoring: Extracted controllers
from controllers import ScanController, SidebarController, ProjectController, PhotoOperationsController

# Phase 2 Refactoring: Extracted UI widgets
from ui.widgets.breadcrumb_navigation import BreadcrumbNavigation
from ui.widgets.backfill_indicator import CompactBackfillIndicator
from ui.widgets.selection_toolbar import SelectionToolbar
from ui.ui_builder import UIBuilder

# Phase 2 Refactoring: Extracted services
from services.thumbnail_manager import ThumbnailManager

from app_services import (
    list_projects, get_default_project_id,
    scan_signals, scan_repository,
    clear_thumbnail_cache
)

from reference_db import ReferenceDB
from reference_db import (
    # NOTE: ensure_created_date_fields no longer needed - handled by migration system
    count_missing_created_fields,
    single_pass_backfill_created_fields,
)

from settings_manager_qt import SettingsManager
# --- Apply decoder warning policy early ---
from settings_manager_qt import apply_decoder_warning_policy
apply_decoder_warning_policy()

from preview_panel_qt import LightboxDialog

# --- Search UI imports ---
from ui.search.search_state_store import SearchStateStore
from ui.search.search_controller import SearchController
from ui.search.top_search_bar import TopSearchBar
from ui.search.search_results_header import SearchResultsHeader
from ui.search.active_chips_bar import ActiveChipsBar
from ui.search.search_sidebar import SearchSidebar
from ui.search.sections.discover_section import DiscoverSection

# --- Video backfill dialog ---
from video_backfill_dialog import VideoBackfillDialog

# --- Preferences dialog (new version with i18n and sidebar navigation) ---
from preferences_dialog import PreferencesDialog
from translation_manager import get_translation_manager, tr

# --- Backfill / process management imports ---
import subprocess, shlex, sys
from pathlib import Path
from threading import Thread, Event

# When double-clicking a thumbnail:
def _on_thumb_double_click(self, path):
    dlg = LightboxDialog(path, self)
    dlg.exec()


# --- Simple file logger for debugging frozen builds ---
def safe_log(msg: str):
    """Append a message to app_log.txt (UTF-8)."""
    try:
        log_path = app_path("app_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception as e:
        # In worst case, just print if file writing fails
        print(f"[LOGGING ERROR] {e}: {msg}")

# Small helper: clamp integer percent
def _clamp_pct(v):
    try:
        return max(0, min(100, int(v or 0)))
    except Exception:
        return 0


def _get_default_ignore_folders():
    """
    Get platform-specific default ignore folders for scanning.
    Returns a list of folder names to skip during repository scans.
    """
    common = ["__pycache__", "node_modules", ".git", ".svn", ".hg",
              "venv", ".venv", "env", ".env"]

    if platform.system() == "Windows":
        return common + [
            "AppData", "Program Files", "Program Files (x86)", "Windows",
            "$Recycle.Bin", "System Volume Information", "Temp", "Cache",
            "Microsoft", "Installer", "Recovery", "Logs",
            "ThumbCache", "ActionCenterCache"
        ]
    elif platform.system() == "Darwin":  # macOS
        return common + ["Library", ".Trash", "Caches", "Logs",
                        "Application Support"]
    else:  # Linux and others
        return common + [".cache", ".local/share/Trash", "tmp"]


# ---------------------------
# Note: Old embedded ScanWorker class removed - now using services/photo_scan_service.py


# === Phase 1, Step 1.3: Controllers Extracted ================================
# NOTE: ScanController, SidebarController, and ProjectController have been
#       extracted to controllers/ package for better modularity.
#       See: controllers/scan_controller.py
#            controllers/sidebar_controller.py
#            controllers/project_controller.py
#
# Reduction: ~600 LOC moved to separate controller files


#======================================

# === Phase 2, Step 2.4: ThumbnailManager Extracted (Pipeline A) =============
# NOTE: ThumbnailManager and related classes (_ThumbLoaded, _ThumbTask) have been
#       extracted to services/thumbnail_manager.py for better modularity.
#       See: services/thumbnail_manager.py
#
# This is Pipeline A - used for zoom integration in MainWindow.
# Pipeline C (ThumbWorker in thumbnail_grid_qt.py) is the preferred pipeline
# for viewport-based lazy loading in the Current Layout.
#
# Reduction: ~164 LOC moved to separate service file
# =============================================================================



# === Phase 2, Step 2.5: UIBuilder Extracted =================================
# NOTE: UIBuilder helper class has been extracted to ui/ui_builder.py
#       for better modularity.
#       See: ui/ui_builder.py
#
# Reduction: ~73 LOC moved to separate UI helper file
# =============================================================================


# ======================================================
# OLD PREFERENCES DIALOG REMOVED
# The old PreferencesDialog class has been replaced with preferences_dialog.py
# New features: Left sidebar navigation, i18n support, 900x600 layout
# ======================================================

# ======================================================


# =============================================================================
# REFACTORING NOTE (Phase 1, Step 1.2 - Thumbnail Consolidation):
#
# Pipeline B (ThumbnailTask/ThumbnailResult) REMOVED - Dead code!
# - Was defined but never used anywhere in the codebase
# - Removed 54 lines of unused code
#
# Thumbnail handling now uses:
# - Pipeline A (ThumbnailManager below) - for MainWindow integration
# - Pipeline C (thumbnail_grid_qt.py: ThumbWorker) - for Current Layout grid
#   ↳ This is the proven, stable pipeline with viewport-based lazy loading
# =============================================================================


# =============================================================================
# REFACTORING NOTE (Phase 1, Step 1.1 - UI Panel Extraction):
#
# DetailsPanel class EXTRACTED to ui/panels/details_panel.py (1,006 lines)
# - Rich metadata display for photos and videos
# - EXIF parsing, GPS reverse geocoding, thumbnail preview
# - Now imported from ui/panels/details_panel.py
#
# See: ui/panels/details_panel.py for implementation
# =============================================================================

# === Phase 2, Step 2.1: BreadcrumbNavigation Extracted =======================
# NOTE: BreadcrumbNavigation widget has been extracted to ui/widgets/ package
#       for better modularity.
#       See: ui/widgets/breadcrumb_navigation.py
#
# Reduction: ~248 LOC moved to separate widget file
# =============================================================================

# === Phase 2, Step 2.3: SelectionToolbar Extracted ==========================
# NOTE: SelectionToolbar widget has been extracted to ui/widgets/ package
#       for better modularity.
#       See: ui/widgets/selection_toolbar.py
#
# Reduction: ~126 LOC moved to separate widget file
# =============================================================================

# === Phase 2, Step 2.2: CompactBackfillIndicator Extracted ==================
# NOTE: CompactBackfillIndicator widget has been extracted to ui/widgets/ package
#       for better modularity.
#       See: ui/widgets/backfill_indicator.py
#
# Reduction: ~228 LOC moved to separate widget file
# =============================================================================

# =============================================================================
# REFACTORING NOTE (Phase 1, Step 1.1 - UI Panel Extraction):
#
# BackfillStatusPanel class EXTRACTED to ui/panels/backfill_status_panel.py (149 lines)
# - Metadata backfill status display and control
# - Background/foreground worker management
# - Now imported from ui/panels/backfill_status_panel.py
#
# See: ui/panels/backfill_status_panel.py for implementation
# =============================================================================



class MainWindow(QMainWindow):
    PROGRESS_DIALOG_THRESHOLD = 10  # 👈 only show dialog if photo count >= X
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MemoryMate - PhotoFlow")

        # Lifecycle flags — set True in closeEvent/request_restart before teardown.
        # Every worker callback must check _closing before touching widgets.
        self._closing = False
        self._restart_requested = False

        # Generation token for guarding worker -> UI callbacks.
        # Increment via bump_ui_generation() when the UI is logically
        # restarted/replaced so stale signals are silently dropped.
        self._ui_generation: int = 0

        # keep rest of initializer logic, but ensure some attributes exist
        self.settings = SettingsManager()

        # UX-1 search state + controller
        self.search_state_store = SearchStateStore()
        self.search_controller = SearchController(
            store=self.search_state_store,
            parent=self,
        )

        self._committed_total = 0
        self._scan_result = (0, 0)  # folders, photos

        # CRITICAL FIX: Load language from settings and apply to TranslationManager
        # This ensures the entire app (not just preferences dialog) uses the selected language
        saved_language = self.settings.get("language", "en")
        tm = get_translation_manager()
        tm.set_language(saved_language)
        print(f"[MainWindow] Language loaded from settings: {saved_language}")

        # Initialize layout manager (for UI/UX layout switching)
        self.layout_manager = LayoutManager(self)
        print("[MainWindow] Layout manager initialized")

        # Phase 1A MainWindow decomposition: startup lifecycle extracted to
        # WorkspaceStartupCoordinator. MainWindow retains thin wrapper methods
        # (_after_first_paint, _deferred_initialization, etc.) that delegate
        # here so external references and showEvent scheduling stay intact.
        from services.workspace_startup_coordinator import WorkspaceStartupCoordinator
        self._workspace_startup_coordinator = WorkspaceStartupCoordinator(self)

        # Phase 1B MainWindow decomposition: project bootstrap / switching /
        # session state restoration extracted to ProjectSwitchService.
        # MainWindow retains thin wrapper methods that delegate here so
        # existing references (menu actions, layout manager, breadcrumb,
        # ScanController post-scan hooks) keep working untouched.
        from services.project_switch_service import ProjectSwitchService
        self._project_switch_service = ProjectSwitchService(self)

        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        QApplication.instance().setAttribute(Qt.AA_SynthesizeMouseForUnhandledTouchEvents, True)
        QApplication.instance().setAttribute(Qt.AA_SynthesizeTouchForUnhandledMouseEvents, True)
        QApplication.instance().setAttribute(Qt.AA_CompressTabletEvents, False)

        # ADAPTIVE WINDOW SIZING: Smart sizing based on screen resolution and DPI scale
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            print("[MainWindow] ⚠️ WARNING: Could not detect primary screen, using defaults")
            # Fallback to safe default size
            self.resize(1200, 800)
            print("[MainWindow] Using fallback size: 1200x800")
        else:
            screen_geometry = screen.availableGeometry()  # Exclude taskbar
            screen_size = screen.size()  # Full screen size
            dpi_scale = screen.devicePixelRatio()  # Windows scale setting

            # Calculate logical pixels (accounts for DPI scaling)
            logical_width = screen_geometry.width()
            logical_height = screen_geometry.height()

            print(f"[MainWindow] 🖥️ Screen detected: {logical_width}x{logical_height} (DPI: {dpi_scale}x)")
            print(f"[MainWindow] Screen geometry: x={screen_geometry.x()}, y={screen_geometry.y()}, w={screen_geometry.width()}, h={screen_geometry.height()}")

            # Adaptive margin based on screen size
            # Larger screens = larger margins for better aesthetics
            if logical_width >= 2560:  # 4K or ultra-wide
                margin = 80
            elif logical_width >= 1920:  # Full HD
                margin = 60
            elif logical_width >= 1366:  # HD/Laptop
                margin = 40
            else:  # Small screens (1280x720 or below)
                margin = 20

            # Calculate window size with margins
            window_width = logical_width - (margin * 2)
            window_height = logical_height - (margin * 2)

            # CRITICAL FIX: Set window size FIRST (not position yet)
            self.resize(window_width, window_height)

            print(f"[MainWindow] 📐 Window size: {window_width}x{window_height} (margins: {margin}px)")

            # CRITICAL FIX: Position window AFTER resize, ensuring it's on visible screen
            # Calculate centered position
            window_x = screen_geometry.x() + margin
            window_y = screen_geometry.y() + margin

            # Ensure position is within screen bounds
            if window_x < screen_geometry.x() or window_x + window_width > screen_geometry.x() + screen_geometry.width():
                window_x = screen_geometry.x() + margin
                print(f"[MainWindow] ⚠️ X position corrected to: {window_x}")

            if window_y < screen_geometry.y() or window_y + window_height > screen_geometry.y() + screen_geometry.height():
                window_y = screen_geometry.y() + margin
                print(f"[MainWindow] ⚠️ Y position corrected to: {window_y}")

            # Move window to calculated position
            self.move(window_x, window_y)

            print(f"[MainWindow] 📍 Window position: x={window_x}, y={window_y}")
            print(f"[MainWindow] ✓ Window geometry: {self.geometry()}")


        if not self.settings.get("show_decoder_warnings", False):
            print("🔇 Qt/Pillow decoder warnings silenced (per user settings).")
        else:
            print("⚠️ Decoder warnings ENABLED (verbose mode).")

        self.active_tag_filter = "all"

        # === Toolbar & Menus via UIBuilder ===
        ui = UIBuilder(self)
        tb = ui.make_toolbar("Tools")
        # Tag main toolbar so LayoutManager can toggle it per layout
        tb.setObjectName("main_toolbar")
        # Defer connecting handlers until grid exists
        act_select_all = ui.action(tr('toolbar.select_all'))
        act_clear_sel = ui.action(tr('toolbar.clear'))
        act_open = ui.action(tr('toolbar.open'))
        act_delete = ui.action(tr('toolbar.delete'))
        ui.separator()

        folded = bool(self.settings.get("sidebar_folded", False))

        # Create action early — connect it later when sidebar exists
        self.act_fold_unfold = ui.action(
            tr('toolbar.fold_unfold_sidebar'),
            shortcut="Ctrl+Shift+F",
            tooltip="Toggle collapse/expand of the sidebar (Ctrl+Shift+F)",
            checkable=True
        )
        self.act_fold_unfold.setChecked(folded)
        ui.separator()

        # === Menu Bar ===
        # === Phase 3: Enhanced Menus (Modern Structure) ===
        menu_bar = self.menuBar()

        # ========== FILE MENU ==========
        menu_file = menu_bar.addMenu(tr("menu.file"))

        act_scan_repo_menu = QAction(tr("menu.file_scan"), self)
        act_scan_repo_menu.setShortcut("Ctrl+O")
        act_scan_repo_menu.setToolTip("Scan a directory to add photos to the current project")
        menu_file.addAction(act_scan_repo_menu)

        menu_file.addSeparator()

        act_preferences = QAction(tr("menu.file_preferences"), self)
        act_preferences.setShortcut("Ctrl+,")
        act_preferences.setIcon(QIcon.fromTheme("preferences-system"))
        menu_file.addAction(act_preferences)
        act_preferences.triggered.connect(self._open_preferences)

        # ========== VIEW MENU ==========
        menu_view = menu_bar.addMenu(tr("menu.view"))

        # Zoom controls
        act_zoom_in = QAction(tr("menu.view_zoom_in"), self)
        act_zoom_in.setShortcut("Ctrl++")
        act_zoom_in.setToolTip("Increase thumbnail size")
        menu_view.addAction(act_zoom_in)

        act_zoom_out = QAction(tr("menu.view_zoom_out"), self)
        act_zoom_out.setShortcut("Ctrl+-")
        act_zoom_out.setToolTip("Decrease thumbnail size")
        menu_view.addAction(act_zoom_out)

        menu_view.addSeparator()

        # Grid Size submenu
        menu_grid_size = menu_view.addMenu(tr("menu.view_grid_size"))

        act_grid_small_menu = QAction(tr("menu.view_grid_small"), self)
        act_grid_small_menu.setCheckable(True)
        menu_grid_size.addAction(act_grid_small_menu)

        act_grid_medium_menu = QAction(tr("menu.view_grid_medium"), self)
        act_grid_medium_menu.setCheckable(True)
        act_grid_medium_menu.setChecked(True)  # Default
        menu_grid_size.addAction(act_grid_medium_menu)

        act_grid_large_menu = QAction(tr("menu.view_grid_large"), self)
        act_grid_large_menu.setCheckable(True)
        menu_grid_size.addAction(act_grid_large_menu)

        act_grid_xl_menu = QAction(tr("menu.view_grid_xl"), self)
        act_grid_xl_menu.setCheckable(True)
        menu_grid_size.addAction(act_grid_xl_menu)

        # Group grid size actions for exclusive selection
        self.grid_size_menu_group = QActionGroup(self)
        self.grid_size_menu_group.addAction(act_grid_small_menu)
        self.grid_size_menu_group.addAction(act_grid_medium_menu)
        self.grid_size_menu_group.addAction(act_grid_large_menu)
        self.grid_size_menu_group.addAction(act_grid_xl_menu)

        menu_view.addSeparator()

        # Sort By submenu
        menu_sort = menu_view.addMenu(tr("menu.view_sort"))

        act_sort_date = QAction(tr("menu.view_sort_date"), self)
        act_sort_date.setCheckable(True)
        menu_sort.addAction(act_sort_date)

        act_sort_filename = QAction(tr("menu.view_sort_filename"), self)
        act_sort_filename.setCheckable(True)
        act_sort_filename.setChecked(True)  # Default
        menu_sort.addAction(act_sort_filename)

        act_sort_size = QAction(tr("menu.view_sort_size"), self)
        act_sort_size.setCheckable(True)
        menu_sort.addAction(act_sort_size)

        # Group sort actions for exclusive selection
        self.sort_menu_group = QActionGroup(self)
        self.sort_menu_group.addAction(act_sort_date)
        self.sort_menu_group.addAction(act_sort_filename)
        self.sort_menu_group.addAction(act_sort_size)

        menu_view.addSeparator()

        # Sidebar submenu
        menu_sidebar = menu_view.addMenu(tr("menu.view_sidebar"))

        act_toggle_sidebar = QAction(tr("menu.view_sidebar"), self)
        act_toggle_sidebar.setShortcut("Ctrl+B")
        act_toggle_sidebar.setCheckable(True)
        act_toggle_sidebar.setChecked(True)  # Default visible
        menu_sidebar.addAction(act_toggle_sidebar)

        act_toggle_sidebar_mode = QAction(tr("menu.view_sidebar_mode"), self)
        act_toggle_sidebar_mode.setShortcut("Ctrl+Alt+S")
        act_toggle_sidebar_mode.setToolTip("Toggle Sidebar between List and Tabs (Ctrl+Alt+S)")
        menu_sidebar.addAction(act_toggle_sidebar_mode)

        menu_view.addSeparator()

        # Layout submenu (UI/UX switching)
        menu_layout = menu_view.addMenu("Layout")
        menu_layout.setToolTip("Switch between different UI layouts")

        # Create action group for exclusive layout selection
        self.layout_action_group = QActionGroup(self)
        self.layout_action_group.setExclusive(True)

        # Get available layouts from manager and create menu actions
        available_layouts = self.layout_manager.get_available_layouts()

        preferred_layout = "google"
        if hasattr(self, "settings") and self.settings:
            preferred_layout = self.settings.get("current_layout", "google")
        if preferred_layout not in available_layouts:
            preferred_layout = "google"

        for layout_id, layout_name in available_layouts.items():
            action = QAction(layout_name, self)
            action.setCheckable(True)
            action.setData(layout_id)


            # Set Current Layout as checked by default
            if layout_id == preferred_layout:
                action.setChecked(True)

            # Connect to layout switching handler
            action.triggered.connect(lambda checked, lid=layout_id: self._switch_layout(lid))

            self.layout_action_group.addAction(action)
            menu_layout.addAction(action)

        menu_view.addSeparator()

        # Activity Center toggle
        self._act_toggle_activity = QAction("Activity Center", self)
        self._act_toggle_activity.setShortcut("Ctrl+Shift+A")
        self._act_toggle_activity.setCheckable(True)
        self._act_toggle_activity.setChecked(False)
        self._act_toggle_activity.setToolTip(
            "Show/hide the Activity Center panel (background jobs)")
        self._act_toggle_activity.triggered.connect(
            self._toggle_activity_center)
        menu_view.addAction(self._act_toggle_activity)

        # ========== FILTERS MENU ==========
        menu_filters = menu_bar.addMenu("Filters")

        self.btn_all = QAction(tr("sidebar.all_photos"), self)
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        menu_filters.addAction(self.btn_all)

        self.btn_fav = QAction(tr("sidebar.favorites"), self)
        self.btn_fav.setCheckable(True)
        menu_filters.addAction(self.btn_fav)

        self.btn_faces = QAction(tr("sidebar.people_label"), self)
        self.btn_faces.setCheckable(True)
        menu_filters.addAction(self.btn_faces)

        # Group filter actions for exclusive selection
        self.filter_menu_group = QActionGroup(self)
        self.filter_menu_group.addAction(self.btn_all)
        self.filter_menu_group.addAction(self.btn_fav)
        self.filter_menu_group.addAction(self.btn_faces)

        # ========== TOOLS MENU ==========
        menu_tools = menu_bar.addMenu(tr("menu.tools"))

        act_scan_repo_tools = QAction(tr("menu.tools_scan_repo"), self)
        act_scan_repo_tools.setToolTip("Scan a directory to add photos to the current project")
        menu_tools.addAction(act_scan_repo_tools)

        menu_tools.addSeparator()

        # Metadata Backfill submenu
        menu_backfill = menu_tools.addMenu("Metadata Backfill")

        act_meta_start = menu_backfill.addAction("Start Background Backfill (Photos)")
        act_meta_single = menu_backfill.addAction("Run Foreground Backfill (Photos)")
        menu_backfill.addSeparator()
        act_video_backfill = menu_backfill.addAction("🎬 Video Metadata Backfill...")
        act_video_backfill.setToolTip("Re-extract metadata (dates, duration, resolution) for all videos")
        menu_backfill.addSeparator()
        act_meta_auto = menu_backfill.addAction("Auto-run after scan (Photos & Videos)")
        act_meta_auto.setCheckable(True)
        act_meta_auto.setChecked(self.settings.get("auto_run_backfill_after_scan", False))
        act_meta_auto.setToolTip("Automatically backfill metadata for both photos and videos after scanning")

        menu_tools.addSeparator()

        # AI / Embeddings submenu
        menu_ai = menu_tools.addMenu("🤖 AI & Semantic Search")

        act_extract_embeddings = menu_ai.addAction("Extract Embeddings...")
        act_extract_embeddings.setToolTip("Extract AI embeddings for semantic search (one-time setup)")

        menu_ai.addSeparator()

        act_ai_status = menu_ai.addAction("Show Embedding Status")
        act_ai_status.setToolTip("Check how many photos have embeddings extracted")

        menu_ai.addSeparator()

        act_embedding_dashboard = menu_ai.addAction("📊 Embedding Statistics Dashboard")
        act_embedding_dashboard.setToolTip("View detailed embedding coverage, storage, and performance stats")

        act_gpu_info = menu_ai.addAction("⚙️ GPU & Performance Info")
        act_gpu_info.setToolTip("Show GPU device, memory, and optimal batch size for embeddings")

        act_migrate_float16 = menu_ai.addAction("⚡ Migrate to Float16")
        act_migrate_float16.setToolTip("Convert float32 embeddings to float16 (50% space savings)")

        # Store actions as instance attributes for signal connections
        self.act_embedding_dashboard = act_embedding_dashboard
        self.act_gpu_info = act_gpu_info
        self.act_migrate_float16 = act_migrate_float16

        menu_tools.addSeparator()

        # Duplicate Detection submenu
        menu_duplicates = menu_tools.addMenu("🔍 Duplicate Detection")

        act_detect_duplicates = menu_duplicates.addAction("Detect Duplicates...")
        act_detect_duplicates.setToolTip("Find exact and similar duplicates in your collection")

        act_find_similar = menu_duplicates.addAction("Find Similar Photos...")
        act_find_similar.setToolTip("Discover visually similar photos using AI")

        menu_duplicates.addSeparator()

        act_dup_status = menu_duplicates.addAction("Show Duplicate Status")
        act_dup_status.setToolTip("Check current duplicate detection status")

        act_clear_cache = QAction(tr("menu.tools_clear_cache"), self)
        menu_tools.addAction(act_clear_cache)
        act_clear_cache.triggered.connect(self._on_clear_thumbnail_cache)

        menu_tools.addSeparator()

        # Database submenu (advanced operations)
        menu_db = menu_tools.addMenu(tr("menu.tools_database"))

        act_db_fresh = QAction(tr("menu.tools_db_fresh"), self)
        menu_db.addAction(act_db_fresh)

        act_db_check = QAction(tr("menu.tools_db_check"), self)
        menu_db.addAction(act_db_check)

        menu_db.addSeparator()

        act_db_rebuild_dates = QAction(tr("menu.tools_db_rebuild_dates"), self)
        menu_db.addAction(act_db_rebuild_dates)

        act_migrate = QAction(tr("menu.tools_db_migrate"), self)
        menu_db.addAction(act_migrate)

        act_optimize = QAction(tr("menu.tools_db_optimize"), self)
        menu_db.addAction(act_optimize)

        # ========== HELP MENU ==========
        menu_help = menu_bar.addMenu(tr("menu.help"))

        act_about = QAction(tr("menu.help_about"), self)
        menu_help.addAction(act_about)

        act_shortcuts = QAction(tr("menu.help_shortcuts"), self)
        act_shortcuts.setShortcut("F1")
        menu_help.addAction(act_shortcuts)

        menu_help.addSeparator()

        act_report_bug = QAction("Report Bug…", self)
        menu_help.addAction(act_report_bug)

        # ========== MENU ACTION CONNECTIONS ==========

        # File menu connections
        act_scan_repo_menu.triggered.connect(lambda: self._on_scan_repository() if hasattr(self, '_on_scan_repository') else None)

        # View menu connections
        act_zoom_in.triggered.connect(self._on_zoom_in)
        act_zoom_out.triggered.connect(self._on_zoom_out)

        act_grid_small_menu.triggered.connect(lambda: self._set_grid_preset("small"))
        act_grid_medium_menu.triggered.connect(lambda: self._set_grid_preset("medium"))
        act_grid_large_menu.triggered.connect(lambda: self._set_grid_preset("large"))
        act_grid_xl_menu.triggered.connect(lambda: self._set_grid_preset("xl"))

        act_sort_date.triggered.connect(lambda: self._apply_menu_sort("Date"))
        act_sort_filename.triggered.connect(lambda: self._apply_menu_sort("Filename"))
        act_sort_size.triggered.connect(lambda: self._apply_menu_sort("Size"))

        act_toggle_sidebar.toggled.connect(self._on_toggle_sidebar_visibility)
        # act_toggle_sidebar_mode connection happens later (line ~2430)

        # Filter menu connections
        self.btn_all.triggered.connect(lambda: self._apply_tag_filter("all"))
        self.btn_fav.triggered.connect(lambda: self._apply_tag_filter("favorite"))
        self.btn_faces.triggered.connect(lambda: self._apply_tag_filter("face"))

        # Tools menu connections
        act_scan_repo_tools.triggered.connect(lambda: self._on_scan_repository() if hasattr(self, '_on_scan_repository') else None)

        act_meta_start.triggered.connect(lambda: self.backfill_panel._on_start_background())
        act_meta_single.triggered.connect(lambda: self.backfill_panel._on_run_foreground())
        act_video_backfill.triggered.connect(self._on_video_backfill)
        act_meta_auto.toggled.connect(lambda v: self.settings.set("auto_run_backfill_after_scan", bool(v)))

        act_extract_embeddings.triggered.connect(self._on_extract_embeddings)
        act_ai_status.triggered.connect(self._on_show_embedding_status)
        self.act_embedding_dashboard.triggered.connect(self._on_open_embedding_dashboard)
        self.act_gpu_info.triggered.connect(self._on_show_gpu_info)
        self.act_migrate_float16.triggered.connect(self._on_migrate_embeddings_float16)

        # Duplicate detection connections
        act_detect_duplicates.triggered.connect(self._on_detect_duplicates)
        act_find_similar.triggered.connect(self._on_find_similar_photos)
        act_dup_status.triggered.connect(self._on_show_duplicate_status)

        act_db_fresh.triggered.connect(self._db_fresh_start)
        act_db_check.triggered.connect(self._db_self_check)
        act_db_rebuild_dates.triggered.connect(self._db_rebuild_date_index)
        act_migrate.triggered.connect(self._run_date_migration)

        def _optimize_db():
            try:
                ReferenceDB().optimize_indexes()
                QMessageBox.information(self, tr('message_boxes.database_title'), tr('message_boxes.database_success_message'))
            except Exception as e:
                QMessageBox.critical(self, tr('message_boxes.database_error_title'), str(e))

        act_optimize.triggered.connect(_optimize_db)

        # Help menu connections
        act_about.triggered.connect(lambda: QMessageBox.information(self, "About", "MemoryMate PhotoFlow (Alpha)\n© 2025"))
        act_shortcuts.triggered.connect(self._show_keyboard_shortcuts)
        act_report_bug.triggered.connect(lambda: self._open_url("https://github.com/anthropics/memorymate-photoflow/issues"))

        # 📂 Scan Repository Action
        act_scan_repo = tb.addAction(tr('toolbar.scan_repository'))
        # Phase 1: Promote Scan Repository with a primary button
        self.btn_scan_primary = QPushButton(tr('toolbar.scan_repository'))
        self.btn_scan_primary.setCursor(Qt.PointingHandCursor)
        self.btn_scan_primary.setStyleSheet("""
            QPushButton {
                background-color: #4A90E2;
                color: white;
                border: 1px solid #3A7BC8;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #357ABD;
            }
        """)
        self.btn_scan_primary.clicked.connect(self._on_scan_repository)
        tb.addWidget(self.btn_scan_primary)

        # Toggle action: show/hide the BackfillStatusPanel
        self.act_toggle_backfill = QAction(QIcon.fromTheme("view-sidebar"), tr('toolbar.backfill_panel'), self)
        self.act_toggle_backfill.setCheckable(True)

        # read persisted preference (fallback True)
        _visible_default = bool(self.settings.get("show_backfill_panel", True))

        self.act_toggle_backfill.setChecked(_visible_default)
        self.act_toggle_backfill.setToolTip("Show / hide Metadata Backfill status panel")

        tb.addAction(self.act_toggle_backfill)

        # Connect toggle signal to a handler that safely creates/shows/hides the panel and persists the choice
        self.act_toggle_backfill.toggled.connect(self._on_toggle_backfill_panel)

        # Ensure panel initial visibility matches saved preference (if panel already created)
        try:
            if hasattr(self, "backfill_panel") and self.backfill_panel is not None:
                self.backfill_panel.setVisible(_visible_default)
        except Exception:
            pass

        # 🛑 Cancel Scan button (programmatic cancel trigger)
        self.act_cancel_scan = ui.action(
            tr('toolbar.cancel_scan'),
            icon="process-stop",
            tooltip="Abort an ongoing repository scan immediately",
            handler=self._on_cancel_scan_clicked
        )
        self.act_cancel_scan.setEnabled(False)

        # Activity Center toggle in main toolbar
        self.act_toolbar_activity = ui.action(
            "Activity",
            icon=None,
            tooltip="Show/hide Activity Center (background jobs) (Ctrl+Shift+A)",
            handler=self._toggle_activity_center,
        )
        tb.addAction(self.act_toolbar_activity)

        # Incremental vs full scan
        self.chk_incremental = ui.checkbox(tr('toolbar.incremental'), checked=True)
        ui.separator()



        # 🔽 Sorting and filtering controls
        self.sort_combo = ui.combo_sort(tr('toolbar.sort'), ["Filename", "Date", "Size"], self._apply_sort_filter)
        self.sort_order_combo = QSortComboBox()
        self.sort_order_combo.addItems([tr('toolbar.ascending'), tr('toolbar.descending')])
        self.sort_order_combo.currentIndexChanged.connect(lambda *_: self._apply_sort_filter())
        tb.addWidget(self.sort_order_combo)
        ui.separator()

        # Phase 2.3: Grid Size Presets (Google Photos style)
        # Quick resize buttons: Small / Medium / Large / XL
        tb.addWidget(QLabel(tr('toolbar.grid')))

        # Create button group for exclusive selection
        self.grid_size_group = QButtonGroup(self)

        # Small preset
        self.btn_grid_small = QPushButton("S")
        self.btn_grid_small.setFixedSize(28, 28)
        self.btn_grid_small.setCheckable(True)
        self.btn_grid_small.setToolTip("Small thumbnails (90px)")
        self.btn_grid_small.setStyleSheet("font-weight: bold;")
        self.btn_grid_small.clicked.connect(lambda: self._set_grid_preset("small"))
        self.grid_size_group.addButton(self.btn_grid_small, 0)
        tb.addWidget(self.btn_grid_small)

        # Medium preset
        self.btn_grid_medium = QPushButton("M")
        self.btn_grid_medium.setFixedSize(28, 28)
        self.btn_grid_medium.setCheckable(True)
        self.btn_grid_medium.setChecked(True)  # Default
        self.btn_grid_medium.setToolTip("Medium thumbnails (120px)")
        self.btn_grid_medium.setStyleSheet("font-weight: bold;")
        self.btn_grid_medium.clicked.connect(lambda: self._set_grid_preset("medium"))
        self.grid_size_group.addButton(self.btn_grid_medium, 1)
        tb.addWidget(self.btn_grid_medium)

        # Large preset
        self.btn_grid_large = QPushButton("L")
        self.btn_grid_large.setFixedSize(28, 28)
        self.btn_grid_large.setCheckable(True)
        self.btn_grid_large.setToolTip("Large thumbnails (200px)")
        self.btn_grid_large.setStyleSheet("font-weight: bold;")
        self.btn_grid_large.clicked.connect(lambda: self._set_grid_preset("large"))
        self.grid_size_group.addButton(self.btn_grid_large, 2)
        tb.addWidget(self.btn_grid_large)

        # XL preset
        self.btn_grid_xl = QPushButton("XL")
        self.btn_grid_xl.setFixedSize(32, 28)
        self.btn_grid_xl.setCheckable(True)
        self.btn_grid_xl.setToolTip("Extra large thumbnails (280px)")
        self.btn_grid_xl.setStyleSheet("font-weight: bold;")
        self.btn_grid_xl.clicked.connect(lambda: self._set_grid_preset("xl"))
        self.grid_size_group.addButton(self.btn_grid_xl, 3)
        tb.addWidget(self.btn_grid_xl)

        # --- Central container
        container = QWidget()
        self.setCentralWidget(container)
        main_layout = QVBoxLayout(container)

        # Phase 2.3: Removed huge BackfillStatusPanel (120-240px)
        # Replaced with compact indicator in top bar

        # --- Top bar (with breadcrumb navigation + compact backfill indicator)
        topbar = QWidget()
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(6, 6, 6, 6)

        # Phase 2 (High Impact): Breadcrumb Navigation replaces project dropdown
        self.breadcrumb_nav = BreadcrumbNavigation(self)
        top_layout.addWidget(self.breadcrumb_nav, 1)

        # Keep project data for backwards compatibility
        self._projects = list_projects()

        # Phase 2.3: Add compact backfill indicator (right-aligned)
        try:
            self.backfill_indicator = CompactBackfillIndicator(self)
            top_layout.addWidget(self.backfill_indicator)
            # Keep reference to old panel for compatibility with menu actions
            self.backfill_panel = self.backfill_indicator
        except Exception as e:
            print(f"[MainWindow] Could not create backfill indicator: {e}")

        main_layout.addWidget(topbar)

        # --- UX-1 Search Chrome (keeps existing toolbar/mobile-sync area untouched)
        self.top_search_bar = TopSearchBar(self)
        self.search_results_header = SearchResultsHeader(
            store=self.search_state_store,
            controller=self.search_controller,
            parent=self
        )
        self.active_chips_bar = ActiveChipsBar(
            store=self.search_state_store,
            parent=self
        )

        main_layout.addWidget(self.top_search_bar)
        self.top_search_bar.querySubmitted.connect(self.search_controller.submit_query)
        self.top_search_bar.queryChanged.connect(lambda text: self.search_controller.set_query_text(text, debounce=True))
        self.top_search_bar.searchCleared.connect(self.search_controller.clear_search)

        main_layout.addWidget(self.search_results_header)
        main_layout.addWidget(self.active_chips_bar)

        # --- Main layout (Sidebar + Grid + Details)
        self.splitter = QSplitter(Qt.Horizontal)

        # PHASE 1: Bootstrap Policy (last-used -> exactly one -> onboarding)
        default_pid = self._bootstrap_active_project()
        self.active_project_id = default_pid
        if hasattr(self, "search_controller"):
            self.search_controller.set_active_project(default_pid)

        self.sidebar = SidebarQt(project_id=default_pid)

        # Gap 1: Force onboarding state if no project is active
        if default_pid is None:
            logger.info("[Bootstrap] Entering explicit onboarding state")

        # UX-1 search/discovery sidebar shell.
        # Existing SidebarQt remains present below it for compatibility.
        self.search_sidebar = SearchSidebar(
            store=self.search_state_store,
            controller=self.search_controller,
            parent=self
        )

        self.left_sidebar_container = QWidget()
        self.left_sidebar_layout = QVBoxLayout(self.left_sidebar_container)
        self.left_sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.left_sidebar_layout.setSpacing(8)
        self.left_sidebar_layout.addWidget(self.search_sidebar, 0)
        self.left_sidebar_layout.addWidget(self.sidebar, 1)

        # Connect Search Controller to bridge
        self.search_controller.searchRequested.connect(self._on_ux1_search_requested)

        # UX-1 signal wiring (moved up to top_search_bar initialization)

        self.active_chips_bar.chipRemoved.connect(self.search_controller.remove_chip)
        self.active_chips_bar.clearAllRequested.connect(self.search_controller.clear_search)

        # === Lazy wiring for sidebar actions (now sidebar exists) ===
        def _on_fold_toggle(checked):
            try:
                if hasattr(self, "sidebar") and self.sidebar:
                    self.sidebar.toggle_fold(bool(checked))
                self.settings.set("sidebar_folded", bool(checked))
            except Exception as e:
                print(f"[MainWindow] fold toggle failed: {e}")

        self.act_fold_unfold.toggled.connect(_on_fold_toggle)
        try:
            self.sidebar.toggle_fold(folded)
        except Exception:
            pass

        def _on_toggle_sidebar_mode():
            try:
                current = self.sidebar._effective_display_mode()
                new_mode = "tabs" if current == "list" else "list"
                self.sidebar.switch_display_mode(new_mode)
                self.settings.set("sidebar_mode", new_mode)
            except Exception as e:
                print(f"[MainWindow] toggle sidebar mode failed: {e}")

        act_toggle_sidebar_mode.triggered.connect(_on_toggle_sidebar_mode)

        # === Controllers ===
        self.scan_controller = ScanController(self)
        self.sidebar_controller = SidebarController(self)
        self.project_controller = ProjectController(self)
        self.photo_ops_controller = PhotoOperationsController(self)  # Phase 3

        # === UI Refresh Mediator (debounced, visibility-safe) ===
        from services.ui_refresh_mediator import UIRefreshMediator
        self._ui_refresh_mediator = UIRefreshMediator(self, parent=self)

        # === Central Face Pipeline Service wiring ===
        try:
            from services.face_pipeline_service import FacePipelineService
            _face_svc = FacePipelineService.instance()
            # Progress → status bar (guarded against shutdown)
            _face_svc.progress.connect(
                lambda step, msg, pid: (
                    self.statusBar().showMessage(msg, 0)
                    if not self._closing else None
                )
            )
            # Incremental batch commits → People refresh via mediator
            _face_svc.batch_committed.connect(
                lambda proc, total, faces, pid: (
                    self._ui_refresh_mediator.request_refresh(
                        {"people"}, "faces_batch", pid
                    ) if not self._closing else None
                )
            )
            # Interim/final clustering passes → dispatch store action for People refresh
            def _on_interim_clusters(cluster_count, total_faces, is_final, pid):
                if self._closing:
                    return
                label = "final" if is_final else "interim"
                self.statusBar().showMessage(
                    f"Faces: {cluster_count} people found ({total_faces} faces, {label})",
                    5000 if not is_final else 0,
                )
                try:
                    from core.state_bus import get_bridge, FacesCompleted, ActionMeta
                    bridge = get_bridge()
                    bridge.dispatch_async(FacesCompleted(
                        meta=ActionMeta(source="face_pipeline"),
                        detected=total_faces,
                        clustered=cluster_count,
                    ))
                except Exception:
                    pass
            _face_svc.interim_clusters_ready.connect(_on_interim_clusters)
            self._on_interim_clusters = _on_interim_clusters  # prevent GC
            # Finished → final People refresh + status bar
            def _on_face_svc_finished(results, pid):
                if self._closing:
                    return
                faces = results.get("faces_detected", 0)
                clusters = results.get("clusters_created", 0)
                if faces > 0:
                    self.statusBar().showMessage(
                        f"Face pipeline complete: {faces} faces, {clusters} clusters", 8000
                    )
                else:
                    self.statusBar().showMessage(
                        "Face pipeline complete — no faces detected", 5000
                    )
                # Rebuild search_asset_features so face_count is available
                # to SearchOrchestrator, then invalidate its metadata cache.
                try:
                    from repository.search_feature_repository import SearchFeatureRepository
                    repo = SearchFeatureRepository()
                    if repo.table_exists():
                        repo.refresh_project(pid)
                except Exception as e:
                    print(f"[MainWindow] search_asset_features rebuild after face pipeline failed: {e}")
                try:
                    from services.search_orchestrator import get_search_orchestrator
                    orch = get_search_orchestrator(pid)
                    orch.invalidate_meta_cache()
                except Exception:
                    pass
                self._ui_refresh_mediator.request_refresh({"people"}, "pipeline_done", pid)
            _face_svc.finished.connect(_on_face_svc_finished)
            # Error → status bar (guarded against shutdown)
            _face_svc.error.connect(
                lambda msg, pid: (
                    self.statusBar().showMessage(f"Face pipeline error: {msg}", 8000)
                    if not self._closing else None
                )
            )
        except Exception as e:
            print(f"[MainWindow] FacePipelineService wiring failed: {e}")

        self.sidebar.on_branch_selected = self.sidebar_controller.on_branch_selected
        self.sidebar.folderSelected.connect(self.sidebar_controller.on_folder_selected)
        # People Groups: connect selectGroup signal
        if hasattr(self.sidebar, 'selectGroup'):
            self.sidebar.selectGroup.connect(self.sidebar_controller.on_group_selected)
        # 🎬 Phase 4: Videos support
        if hasattr(self.sidebar, 'selectVideos'):
            self.sidebar.selectVideos.connect(self.sidebar_controller.on_videos_selected)

        self.splitter.addWidget(self.left_sidebar_container)

        # Ctrl+F → focus Smart Find search input
        shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        shortcut_find.activated.connect(self._focus_smart_find)

        # Session state restoration is handled by _deferred_initialization()
        # (post first-paint). Removed early QTimer(100) that could fire before
        # the window has painted and trigger sidebar expansion prematurely.

        # Phase 2.3: Grid container with selection toolbar
        self.grid_container = QWidget()
        grid_layout = QVBoxLayout(self.grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(4)

        # Selection toolbar (hidden by default, shows when items selected)
        self.selection_toolbar = SelectionToolbar(self)
        grid_layout.addWidget(self.selection_toolbar)

        # Thumbnail grid
        self.grid = ThumbnailGridQt(project_id=default_pid)
        grid_layout.addWidget(self.grid)

        # 🎬 Phase 4.4: Video player panel (hidden by default)
        self.video_player = VideoPlayerPanel(self)
        self.video_player.hide()
        self.video_player.closed.connect(self._on_video_player_closed)
        grid_layout.addWidget(self.video_player)

        self.splitter.addWidget(self.grid_container)

        # === Metadata Editor Dock (FIX 2026-02-08) ===
        # Initialize metadata editor dock for photo metadata viewing/editing
        try:
            from ui.metadata_editor_dock import MetadataEditorDock
            self.metadata_editor_dock = MetadataEditorDock(self)
            self.metadata_editor_dock.metadataChanged.connect(self._on_metadata_changed)
            self.addDockWidget(Qt.RightDockWidgetArea, self.metadata_editor_dock)
            self.metadata_editor_dock.hide()  # Hidden by default
            print("[MainWindow] ✓ MetadataEditorDock initialized")
        except Exception as e:
            print(f"[MainWindow] ⚠️ MetadataEditorDock init failed: {e}")
            self.metadata_editor_dock = None

        # 🔗 Now that grid exists — connect toolbar actions safely
        act_select_all.triggered.connect(self.grid.list_view.selectAll)
        act_clear_sel.triggered.connect(self.grid.list_view.clearSelection)
        act_open.triggered.connect(lambda: self._open_lightbox_from_selection())
        act_delete.triggered.connect(lambda: self._request_delete_from_selection())

       # === Thumbnail Manager wiring ===
        self.thumb_cache = {}
        try:
            self.thumbnails = ThumbnailManager(
                grid=self.grid,
                cache=self.thumb_cache,
                log=getattr(self, "gui_log", None),
                initial_size=160
            )
        except Exception as e:
            print("[MainWindow] ⚠️ ThumbnailManager init failed:", e)
            self.thumbnails = None

        # Hook zoom slider to ThumbnailManager if available
        if hasattr(self.grid, "zoom_slider"):
            self.grid.zoom_slider.valueChanged.connect(
                lambda val: self.thumbnails.update_zoom(val) if self.thumbnails else None
            )

        # --- Details panel on the right
        self.details = DetailsPanel(self)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        # Phase 3: Set initial splitter sizes for better usability
        # Sidebar: 250px, Grid: takes remaining, Details: 300px
        self.splitter.setSizes([250, 1000, 300])
        # Make splitter handle more visible and easier to grab
        self.splitter.setHandleWidth(3)

        main_layout.addWidget(self.splitter, 1)

        # NOTE: Background Activity Panel removed - was too bulky and ate photo grid space
        # Background jobs run silently via JobManager with progress in status bar
        self.activity_panel = None

        # --- Wire toolbar actions
        act_select_all.triggered.connect(self.grid.list_view.selectAll)
        act_clear_sel.triggered.connect(self.grid.list_view.clearSelection)
        act_open.triggered.connect(lambda: self._open_lightbox_from_selection())
        act_delete.triggered.connect(lambda: self._request_delete_from_selection())

        act_scan_repo.triggered.connect(self._on_scan_repository)

        self.sort_combo.currentIndexChanged.connect(lambda *_: self._apply_sort_filter())
        self.sort_order_combo.currentIndexChanged.connect(lambda *_: self._apply_sort_filter())

        # --- Grid signals
        # Phase 2.3: Use rich status bar instead of simple message
        self.grid.selectionChanged.connect(lambda n: self._update_status_bar(selection_count=n))
        self.grid.openRequested.connect(lambda p: self._open_lightbox(p))
        self.grid.deleteRequested.connect(lambda paths: self._confirm_delete(paths))
        # Phase 2.3: Update status bar when grid data is reloaded
        self.grid.gridReloaded.connect(lambda: self._update_status_bar())
        # Phase 2: Update breadcrumb navigation when grid changes
        self.grid.gridReloaded.connect(lambda: self._update_breadcrumb())
        # Phase 2.3: Update selection toolbar when selection changes
        self.grid.selectionChanged.connect(lambda n: self.selection_toolbar.update_selection(n))

        # --- Wire selection toolbar buttons
        self.selection_toolbar.btn_favorite.clicked.connect(self._toggle_favorite_selection)
        self.selection_toolbar.btn_delete.clicked.connect(self._request_delete_from_selection)
        self.selection_toolbar.btn_export.clicked.connect(self._export_selection_to_folder)
        self.selection_toolbar.btn_move.clicked.connect(self._move_selection_to_folder)
        self.selection_toolbar.btn_tag.clicked.connect(self._add_tag_to_selection)
        self.selection_toolbar.btn_clear.clicked.connect(self.grid.list_view.clearSelection)

        # --- Phase 8: Wire face grouping buttons (moved to grid toolbar)
        if hasattr(self.grid, "btn_detect_and_group"):
            self.grid.btn_detect_and_group.clicked.connect(self._on_detect_and_group_faces)
        if hasattr(self.grid, "btn_recluster"):
            self.grid.btn_recluster.clicked.connect(self._on_recluster_faces)

        # --- Auto-update details panel on selection change
        self._selection_tag_cache = {}
        self._details_update_timer = QTimer(self)
        self._details_update_timer.setSingleShot(True)
        def _update_details_from_selection():
            paths = self.grid.get_selected_paths()
            # 📜 ENHANCEMENT: Show placeholder when no selection or empty grid click
            if paths:
                try:
                    from services.tag_service import get_tag_service
                    svc = get_tag_service()
                    # Batch fetch tags for selection and cache
                    pid = self.grid.project_id
                    tag_map = svc.get_tags_for_paths(paths, pid) if pid is not None else {}
                    self._selection_tag_cache = tag_map or {}
                except Exception:
                    self._selection_tag_cache = {}
                self.details.update_path(paths[-1])
            else:
                # Clear and show elegant placeholder
                self.details.clear()
                self.details.thumb.setMinimumHeight(220)
                self.details.thumb.setToolTip('')
                self.details.thumb.setText("🖼️")
                self.details.thumb.setStyleSheet("""
                    QLabel {
                        color: #999;
                        font-size: 64px;
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                   stop:0 #f5f5f5, stop:1 #e8e8e8);
                        border: 2px dashed #ccc;
                        border-radius: 8px;
                    }
                """)
                self.details.meta.setText("""
                    <div style='text-align:center; padding:20px; color:#999;'>
                        <p style='font-size:14pt; font-weight:600;'>No Selection</p>
                        <p style='font-size:10pt;'>Select a photo or video to view details</p>
                    </div>
                """)
        self.grid.selectionChanged.connect(lambda *_: self._details_update_timer.start(100))
        self._details_update_timer.timeout.connect(_update_details_from_selection)

        # 🏷️ ENHANCEMENT: Refresh details panel when tags change
        # This ensures tag overlay updates in real-time
        if hasattr(self.grid, 'tagsChanged'):
            self.grid.tagsChanged.connect(lambda *_: _update_details_from_selection())

        # 📜 Initialize details panel with placeholder on startup
        QTimer.singleShot(100, _update_details_from_selection)

        # === Initialize periodic progress pollers (for detached workers) ===
        try:
            self.app_root = APP_DIR  # base path for 'status/' folder
            status_dir = os.path.join(self.app_root, "status")

            # More defensive directory creation (fixes WinError 183 on Windows)
            if not os.path.exists(status_dir):
                os.makedirs(status_dir)
            elif not os.path.isdir(status_dir):
                raise ValueError(f"'status' exists but is not a directory: {status_dir}")

            self._init_progress_pollers()
        except Exception as e:
            print(f"[MainWindow] ⚠️ Progress pollers init failed: {e}")

        # === Initialize embedding status bar indicator ===
        try:
            self._init_embedding_status_indicator()
        except Exception as e:
            print(f"[MainWindow] ⚠️ Embedding status indicator init failed: {e}")

        # === Store subscription for CurrentLayout grid/sidebar refresh ===
        try:
            self._init_current_layout_store_sub()
        except Exception as e:
            print(f"[MainWindow] ⚠️ CurrentLayout store subscription init failed: {e}")

        # Phase 2: Initialize breadcrumb navigation
        QTimer.singleShot(100, self._update_breadcrumb)

        # === Initialize default layout (UI/UX system) ===
        # Load user's preferred layout or default to "current"
        # This must happen after all UI components are created
        try:
            # Propagate Search State to Layout Manager
            self.layout_manager.search_state_store = self.search_state_store
            self.layout_manager.search_controller = self.search_controller

            self.layout_manager.initialize_default_layout()
            print("[Startup] Layout system initialized successfully")
        except Exception as e:
            print(f"[Startup] ⚠️ Layout initialization failed: {e}")
            import traceback
            traceback.print_exc()

        # NOTE: Database schema initialization is now handled in splash_qt.py startup worker
        # to avoid duplicate initialization. ReferenceDB() calls DatabaseConnection(auto_init=True)
        # which handles schema creation and migrations.

        # Deferred init is now triggered by showEvent → _after_first_paint,
        # NOT by a blind QTimer in __init__.  This guarantees the window has
        # painted at least once before any heavy background work starts.

        # DIAGNOSTIC: Confirm __init__() is completing
        print("[MainWindow] __init__() COMPLETED - returning to main_qt.py")
        print(f"[MainWindow] Window object: {self}")
        print(f"[MainWindow] Window valid: {self.isValid() if hasattr(self, 'isValid') else 'N/A'}")

    # ------------------------------------------------------------------
    # Guardrail 1: First-render gate
    # ------------------------------------------------------------------
    def showEvent(self, event):
        """Never start deferred init before the window is shown and has painted."""
        super().showEvent(event)
        if getattr(self, "_startup_after_show_scheduled", False):
            return
        self._startup_after_show_scheduled = True
        # Let Qt complete a paint cycle, then start deferred work.
        QTimer.singleShot(0, self._after_first_paint)

    def _after_first_paint(self):
        """Runs right after the first paint — schedules heavy background work.

        Phase 1A: delegates to WorkspaceStartupCoordinator.
        """
        self._workspace_startup_coordinator.after_first_paint()

    def _bootstrap_active_project(self) -> Optional[int]:
        """Canonical project bootstrap (Phase 1B: delegates to ProjectSwitchService)."""
        return self._project_switch_service.bootstrap_active_project()

    def _deferred_initialization(self):
        """Heavy post-first-paint init (Phase 1A: delegates to coordinator)."""
        self._workspace_startup_coordinator.deferred_initialization()

    def _init_minimal_db_handle(self):
        """Fast DB handle init (Phase 1A: delegates to coordinator)."""
        self._workspace_startup_coordinator.init_minimal_db_handle()

    def _enqueue_startup_maintenance_job(self):
        """Startup maintenance job dispatch (Phase 1A: delegates to coordinator)."""
        self._workspace_startup_coordinator.enqueue_startup_maintenance_job()

    def _warmup_clip_in_background(self):
        """CLIP model warmup (Phase 1A: delegates to coordinator)."""
        self._workspace_startup_coordinator.warmup_clip_in_background()

    def _deferred_cache_purge(self):
        """Thumbnail cache purge (Phase 1A: delegates to coordinator)."""
        self._workspace_startup_coordinator.deferred_cache_purge()

    def ensureOnScreen(self):
        """
        CRITICAL FIX: Ensure window is positioned on a visible screen.
        Call this after show() if window doesn't appear.

        This method checks if the window is off-screen and repositions it
        to the center of the primary screen if needed.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            print("[MainWindow] ⚠️ Cannot verify screen - no primary screen detected")
            return

        screen_geometry = screen.availableGeometry()
        window_geometry = self.geometry()

        # Check if window is completely off-screen
        window_center_x = window_geometry.center().x()
        window_center_y = window_geometry.center().y()

        is_on_screen = (
            screen_geometry.contains(window_geometry.center()) or
            screen_geometry.intersects(window_geometry)
        )

        if not is_on_screen:
            print(f"[MainWindow] ⚠️ WARNING: Window is OFF-SCREEN!")
            print(f"[MainWindow] Window center: ({window_center_x}, {window_center_y})")
            print(f"[MainWindow] Screen bounds: {screen_geometry}")
            print(f"[MainWindow] 🔧 Moving window to center of primary screen...")

            # Force window to center of primary screen
            new_x = screen_geometry.x() + (screen_geometry.width() - self.width()) // 2
            new_y = screen_geometry.y() + (screen_geometry.height() - self.height()) // 2

            self.move(new_x, new_y)
            print(f"[MainWindow] ✓ Window repositioned to: ({new_x}, {new_y})")
        else:
            print(f"[MainWindow] ✓ Window is on-screen (center at {window_center_x}, {window_center_y})")


    # _init_db_and_sidebar() removed in v9.3.0 - replaced by:
    # - _init_minimal_db_handle() for fast DB handle creation
    # - _enqueue_startup_maintenance_job() for background heavy work

    # ============================================================
    # 🏷️ Tag filter handler
    # ============================================================
    def _apply_tag_filter(self, tag: str | None):
        """
        Apply or clear a tag filter without changing navigation context.
        """
        if not hasattr(self, "grid"):
            return

        if tag in (None, "", "all"):
            self.grid.apply_tag_filter(None)
            print("[TAG FILTER] Cleared.")
        else:
            self.grid.apply_tag_filter(tag)
            print(f"[TAG FILTER] Applied: {tag}")

        # Phase 2.3: Update rich status bar after filter change
        self._update_status_bar()


    def _clear_tag_filter(self):
        """Clear active tag overlay and restore previous grid navigation mode."""
        if getattr(self, "active_tag_filter", None):
            print("[TAG FILTER] Cleared by navigation.")
        self.active_tag_filter = None
        self.grid.active_tag_filter = None

        # ✅ Restore the last navigation mode and key
        prev_mode = getattr(self, "last_nav_mode", "branch")
        if self.grid.load_mode == "tag":
            self.grid.load_mode = prev_mode

            # Restore keys based on last mode
            if prev_mode == "branch":
                self.grid.branch_key = getattr(self, "last_nav_key", None)
            elif prev_mode == "folder":
                self.grid.current_folder_id = getattr(self, "last_nav_key", None)
            elif prev_mode == "date":
                self.grid.date_key = getattr(self, "last_nav_key", None)

        # Force refresh
        if hasattr(self.grid, "reload"):
            self.grid.reload()

    # ============================================================
    # 🔍 Search handlers
    # ============================================================
    def _on_quick_search(self, query: str):
        """Handle quick search from search bar."""
        try:
            from app_services import get_search_service
            search_service = get_search_service()
            paths = search_service.quick_search(query, limit=100)

            # Display results in grid
            if paths:
                self.grid.load_paths(paths)
                self.statusBar().showMessage(f"🔍 Found {len(paths)} photos matching '{query}'")
                print(f"[SEARCH] Quick search found {len(paths)} results for '{query}'")
            else:
                self.statusBar().showMessage(f"🔍 No photos found matching '{query}'")
                QMessageBox.information(self, tr('search.error_title'), tr('search.no_results').format(query=query))
        except Exception as e:
            logging.getLogger(__name__).error(f"Quick search failed: {e}")
            QMessageBox.critical(self, tr('search.error_title'), tr('search.error_message').format(error=e))

    def _on_advanced_search(self):
        """Show advanced search dialog."""
        # UX-1: Bridging to new shell or disabling until UX-2
        QMessageBox.information(self, "Coming Soon", "Advanced search is being integrated into the new Search Hub.")

    def _on_semantic_search(self, photo_ids: list, query: str, scores: list):
        """
        Handle semantic search results.

        Args:
            photo_ids: List of photo IDs
            query: Search query text
            scores: List of (photo_id, similarity_score) tuples
        """
        try:
            from repository.photo_repository import PhotoRepository


            logger.info(f"[SEMANTIC_SEARCH] Got {len(photo_ids)} results for '{query}'")

            # Create score lookup dictionary
            score_map = {photo_id: score for photo_id, score in scores}

            # Convert photo IDs to paths with scores
            photo_repo = PhotoRepository()
            paths_with_scores = []

            with photo_repo.connection() as conn:
                placeholders = ','.join('?' * len(photo_ids))
                cursor = conn.execute(
                    f"SELECT id, path FROM photo_metadata WHERE id IN ({placeholders})",
                    photo_ids
                )
                for row in cursor.fetchall():
                    photo_id = row["id"]
                    path = row["path"]
                    score = score_map.get(photo_id, 0.0)
                    paths_with_scores.append((path, score))

            # Display results in grid
            if paths_with_scores:
                # Extract just paths for now (grid doesn't support scores yet)
                paths = [path for path, score in paths_with_scores]
                self.grid.load_paths(paths)

                # Store scores for later use (e.g., overlay display)
                self.grid._semantic_search_scores = score_map

                # Show summary with score range
                min_score = min(score for _, score in paths_with_scores)
                max_score = max(score for _, score in paths_with_scores)
                avg_score = sum(score for _, score in paths_with_scores) / len(paths_with_scores)

                self.statusBar().showMessage(
                    f"✨ Semantic search: {len(paths)} photos matching '{query}' "
                    f"(scores: {min_score:.1%} - {max_score:.1%}, avg: {avg_score:.1%})"
                )
                logger.info(
                    f"[SEMANTIC_SEARCH] Displayed {len(paths)} results - "
                    f"score range: {min_score:.3f} to {max_score:.3f}"
                )
            else:
                self.statusBar().showMessage(f"✨ No photos found for '{query}'")
                logger.warning(f"[SEMANTIC_SEARCH] No paths found for {len(photo_ids)} photo IDs")

        except Exception as e:

            logger.error(f"[SEMANTIC_SEARCH] Failed to display results: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Display Error",
                f"Failed to display search results:\n{e}"
            )

    def _on_semantic_search_cleared(self):
        """Handle semantic search cleared - reload all photos."""
        try:

            logger.info("[SEMANTIC_SEARCH] Search cleared, reloading all photos")

            # Reload the current view (all photos)
            self.grid.reload()
            self.statusBar().showMessage("Showing all photos")

        except Exception as e:

            logger.error(f"[SEMANTIC_SEARCH] Failed to clear search: {e}", exc_info=True)
            QMessageBox.critical(self, tr('search.error_title'), tr('search.error_message').format(error=e))


    def _on_video_backfill(self):
        """Open the video metadata backfill dialog."""
        try:
            # Get project_id from grid or sidebar
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            # Fallback to default project if not found
            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(
                    self,
                    "No Project",
                    "No project is currently active.\n"
                    "Please create a project or scan a folder first."
                )
                return

            dialog = VideoBackfillDialog(self, project_id=project_id)
            result = dialog.exec()

            # If backfill completed successfully, refresh sidebar to update video counts
            if result == QDialog.Accepted and dialog.stats:
                if dialog.stats.get('updated', 0) > 0:
                    print(f"✓ Video backfill completed: {dialog.stats['updated']} videos updated")
                    # Refresh sidebar to show updated video date counts
                    if hasattr(self, 'sidebar') and hasattr(self.sidebar, 'refresh_sidebar'):
                        self.sidebar.refresh_sidebar()
                        print("✓ Sidebar refreshed with new video metadata")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Video Backfill Error",
                f"Failed to open video backfill dialog:\n{str(e)}"
            )
            print(f"✗ Video backfill error: {e}")
            import traceback
            traceback.print_exc()

    def _on_extract_embeddings(self):
        """Launch the embedding extraction worker for semantic search."""
        try:
            # Get project_id
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(
                    self,
                    "No Project",
                    "No project is currently active.\n"
                    "Please create a project or scan a folder first."
                )
                return

            # Get all photos in the project
            from repository.photo_repository import PhotoRepository
            photo_repo = PhotoRepository()
            all_photos = photo_repo.find_all(where_clause="project_id = ?", params=(project_id,))

            if not all_photos:
                QMessageBox.information(
                    self,
                    "No Photos",
                    "No photos found in the current project.\n"
                    "Please scan a folder first."
                )
                return

            photo_ids = [p['id'] for p in all_photos]

            # Check for large model and prompt user if not available
            from utils.model_selection_helper import check_and_select_model, open_model_download_preferences

            model_variant, should_continue = check_and_select_model(self)

            if not should_continue:
                # User cancelled
                return

            if model_variant == 'OPEN_DOWNLOAD_DIALOG':
                # User wants to download the large model
                open_model_download_preferences(self)
                QMessageBox.information(
                    self,
                    "Download Required",
                    "Please download the large CLIP model from the preferences dialog.\n\n"
                    "After download completes, run 'Extract Embeddings' again."
                )
                return

            # Get model info for confirmation dialog
            model_info = {
                'openai/clip-vit-base-patch32': 'base model (~600MB, fast, 19-26% quality)',
                'openai/clip-vit-base-patch16': 'base-16 model (~600MB, better, 30-40% quality)',
                'openai/clip-vit-large-patch14': 'large model (~1.7GB, best, 40-60% quality)',
            }
            model_desc = model_info.get(model_variant, model_variant)

            # Show confirmation dialog
            result = QMessageBox.question(
                self,
                "Extract Embeddings",
                f"This will extract AI embeddings for {len(photo_ids)} photos.\n\n"
                f"Using: {model_desc}\n"
                f"Processing may take a while depending on your hardware.\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No
            )

            if result != QMessageBox.Yes:
                return

            # Launch the embedding worker with progress dialog
            from workers.embedding_worker import EmbeddingWorker
            from services.job_service import get_job_service
            from ui.embedding_progress_dialog import EmbeddingProgressDialog
            from PySide6.QtCore import QThreadPool

            # Create progress dialog
            progress_dialog = EmbeddingProgressDialog(len(photo_ids), self)

            # Enqueue job
            job_service = get_job_service()
            job_id = job_service.enqueue_job(
                kind='embed',
                payload={
                    'photo_ids': photo_ids,
                    'model_variant': model_variant
                },
                backend='auto'
            )

            # Get current project_id for canonical model enforcement
            current_project_id = getattr(self.grid, 'project_id', None) if hasattr(self, 'grid') else None

            # Create worker
            worker = EmbeddingWorker(
                job_id=job_id,
                photo_ids=photo_ids,
                model_variant=model_variant,
                device='auto',
                project_id=current_project_id
            )

            # Connect worker signals to progress dialog
            def _on_emb_progress(curr, total, path, msg):
                # Update progress dialog with filename (from msg)
                progress_dialog.update_progress(curr, total, path, msg)
                # Show same detailed msg in MainWindow status bar
                if not self._closing:
                    self.statusBar().showMessage(msg, 0)

            worker.signals.progress.connect(_on_emb_progress)
            worker.signals.finished.connect(progress_dialog.on_finished)
            worker.signals.error.connect(progress_dialog.on_error)

            # Connect dialog cancel to worker cancel
            progress_dialog.cancelled.connect(worker.cancel)

            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._embedding_worker = worker

            # Start worker
            QThreadPool.globalInstance().start(worker)

            # Show progress dialog
            progress_dialog.exec()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Embedding Extraction Error",
                f"Failed to start embedding extraction:\n{str(e)}"
            )
            print(f"✗ Embedding extraction error: {e}")
            import traceback
            traceback.print_exc()

    def _start_embedding_extraction(self, photo_ids: list):
        """
        Start embedding extraction for a specific list of photo IDs.

        This is a helper method for folder/selection-specific extraction.

        Args:
            photo_ids: List of photo IDs to process
        """
        try:
            if not photo_ids:
                QMessageBox.information(self, "No Photos", "No photos to process.")
                return

            # Get model selection
            from utils.model_selection_helper import check_and_select_model

            model_variant, should_continue = check_and_select_model(self)

            if not should_continue:
                return

            if model_variant == 'OPEN_DOWNLOAD_DIALOG':
                QMessageBox.information(
                    self,
                    "Download Required",
                    "Please download the CLIP model from Preferences > Visual Embeddings."
                )
                return

            # Launch the embedding worker with progress dialog
            from workers.embedding_worker import EmbeddingWorker
            from services.job_service import get_job_service
            from ui.embedding_progress_dialog import EmbeddingProgressDialog
            from PySide6.QtCore import QThreadPool

            # Create progress dialog
            progress_dialog = EmbeddingProgressDialog(len(photo_ids), self)

            # Enqueue job
            job_service = get_job_service()
            job_id = job_service.enqueue_job(
                kind='embed',
                payload={
                    'photo_ids': photo_ids,
                    'model_variant': model_variant
                },
                backend='auto'
            )

            # Get current project_id for canonical model enforcement
            current_project_id = getattr(self.grid, 'project_id', None) if hasattr(self, 'grid') else None

            # Create worker
            worker = EmbeddingWorker(
                job_id=job_id,
                photo_ids=photo_ids,
                model_variant=model_variant,
                device='auto',
                project_id=current_project_id
            )

            # Connect worker signals to progress dialog
            def _on_emb_progress_reindex(curr, total, path, msg):
                # Update progress dialog with filename (from msg)
                progress_dialog.update_progress(curr, total, path, msg)
                # Show same detailed msg in MainWindow status bar
                if not self._closing:
                    self.statusBar().showMessage(msg, 0)

            worker.signals.progress.connect(_on_emb_progress_reindex)
            worker.signals.finished.connect(progress_dialog.on_finished)
            worker.signals.error.connect(progress_dialog.on_error)

            # Connect dialog cancel to worker cancel
            progress_dialog.cancelled.connect(worker.cancel)

            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._embedding_worker = worker

            # Start worker
            QThreadPool.globalInstance().start(worker)

            # Show progress dialog
            progress_dialog.exec()

            # Update embedding status bar after extraction
            if hasattr(self, '_update_embedding_status_bar'):
                self._update_embedding_status_bar()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Extraction Error",
                f"Failed to start embedding extraction:\n{str(e)}"
            )

    def _on_show_embedding_status(self):
        """Show embedding extraction status."""
        try:
            # Get project_id
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(self, "No Project", "No project is currently active.")
                return

            # Query database for stats
            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db._connect() as conn:
                cur = conn.cursor()

                # Total photos
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata WHERE project_id = ?
                """, (project_id,))
                total_photos = cur.fetchone()[0]

                # Photos with embeddings
                cur.execute("""
                    SELECT COUNT(DISTINCT pe.photo_id)
                    FROM photo_embedding pe
                    JOIN photo_metadata p ON pe.photo_id = p.id
                    WHERE p.project_id = ?
                """, (project_id,))
                photos_with_embeddings = cur.fetchone()[0]

                # Active/pending jobs
                cur.execute("""
                    SELECT COUNT(*) FROM ml_job
                    WHERE status IN ('pending', 'running')
                """)
                active_jobs = cur.fetchone()[0]

            percent = (photos_with_embeddings / total_photos * 100) if total_photos > 0 else 0

            QMessageBox.information(
                self,
                "Embedding Status",
                f"Photos with embeddings: {photos_with_embeddings:,} / {total_photos:,} ({percent:.1f}%)\n"
                f"Active/pending jobs: {active_jobs}\n\n"
                f"{'✓ Ready for semantic search!' if photos_with_embeddings > 0 else 'Run Extract Embeddings to enable semantic search.'}"
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Status Error",
                f"Failed to get embedding status:\n{str(e)}"
            )
            print(f"✗ Embedding status error: {e}")
            import traceback
            traceback.print_exc()

    def _on_open_embedding_dashboard(self):
        """Open the embedding statistics dashboard."""
        try:
            from ui.embedding_stats_dashboard import show_embedding_stats_dashboard

            # Get project_id
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(self, "No Project", "Please select a project first.")
                return

            # Keep reference to prevent garbage collection
            self._embedding_dashboard = show_embedding_stats_dashboard(project_id, self)

        except Exception as e:
            QMessageBox.critical(
                self,
                "Dashboard Error",
                f"Failed to open embedding dashboard:\n{str(e)}"
            )

    def _on_show_gpu_info(self):
        """Show GPU and performance information dialog."""
        try:
            from services.semantic_embedding_service import get_semantic_embedding_service

            service = get_semantic_embedding_service()
            gpu_info = service.get_gpu_memory_info()

            device = gpu_info.get('device', 'cpu')
            device_name = gpu_info.get('device_name', 'Unknown')
            total_mb = gpu_info.get('total_mb', 0)
            available_mb = gpu_info.get('available_mb', 0)

            # Get optimal batch size
            try:
                batch_size = service.get_optimal_batch_size()
            except Exception:
                batch_size = "N/A"

            # Check FAISS
            try:
                import faiss
                faiss_status = "✓ Available (Fast ANN search)"
            except ImportError:
                faiss_status = "✗ Not installed (using numpy fallback)"

            # Build message
            if device == 'cuda':
                device_info = f"CUDA GPU: {device_name}"
                mem_info = f"Memory: {available_mb:.0f} MB available / {total_mb:.0f} MB total"
            elif device == 'mps':
                device_info = "Apple Metal (MPS)"
                mem_info = "Memory: Shared with system RAM"
            else:
                device_info = "CPU (No GPU detected)"
                mem_info = "Memory: Using system RAM"

            QMessageBox.information(
                self,
                "GPU & Performance Info",
                f"Device: {device_info}\n"
                f"{mem_info}\n\n"
                f"Optimal Batch Size: {batch_size} photos\n"
                f"FAISS: {faiss_status}\n\n"
                f"💡 Batch size is auto-tuned based on available GPU memory."
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "GPU Info Error",
                f"Failed to get GPU info:\n{str(e)}"
            )

    def _on_migrate_embeddings_float16(self):
        """Migrate float32 embeddings to float16 format."""
        try:
            from services.semantic_embedding_service import get_semantic_embedding_service

            # First check if there are any float32 embeddings
            service = get_semantic_embedding_service()

            # Get stats to check float32 count
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            stats = service.get_project_embedding_stats(project_id) if project_id else {}
            float32_count = stats.get('float32_count', 0)

            if float32_count == 0:
                QMessageBox.information(
                    self,
                    "No Migration Needed",
                    "All embeddings are already in float16 format.\n"
                    "No migration is necessary."
                )
                return

            reply = QMessageBox.question(
                self,
                "Migrate to Float16",
                f"Found {float32_count} embeddings in legacy float32 format.\n\n"
                "Converting to float16 will save ~50% storage space.\n"
                "This is safe and reversible (embeddings can be regenerated).\n\n"
                "Proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if reply != QMessageBox.Yes:
                return

            migrated = 0
            while True:
                batch_migrated = service.migrate_to_half_precision(batch_size=500)
                if batch_migrated == 0:
                    break
                migrated += batch_migrated

            QMessageBox.information(
                self,
                "Migration Complete",
                f"Successfully migrated {migrated} embeddings to float16 format."
            )

            # Update status bar if we have embedding coverage indicator
            if hasattr(self, '_update_embedding_status_bar'):
                self._update_embedding_status_bar()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Migration Error",
                f"Failed to migrate embeddings:\n{str(e)}"
            )

    def _on_detect_duplicates(self):
        """Launch duplicate detection scope dialog."""
        try:
            # Get project_id
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(self, "No Project", "No project is currently active.")
                return

            # Import and show the scope selection dialog
            from ui.duplicate_scope_dialog import DuplicateScopeDialog
            dialog = DuplicateScopeDialog(project_id=project_id, parent=self)

            # Connect signal to run detection when user clicks Proceed
            dialog.scopeSelected.connect(
                lambda photo_ids, options: self._run_duplicate_detection(project_id, photo_ids, options)
            )

            dialog.exec()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Duplicate Detection Error",
                f"Failed to launch duplicate detection:\n{str(e)}"
            )
            print(f"✗ Duplicate detection error: {e}")
            import traceback
            traceback.print_exc()

    def _run_duplicate_detection(self, project_id: int, photo_ids: list, options: dict):
        """Run duplicate detection with the selected scope and options."""
        try:
            from PySide6.QtWidgets import QProgressDialog
            from PySide6.QtCore import Qt, QCoreApplication
            from services.asset_service import AssetService
            from repository.photo_repository import PhotoRepository
            from repository.asset_repository import AssetRepository
            from repository.base_repository import DatabaseConnection

            # Show progress dialog
            progress = QProgressDialog(
                "Running duplicate detection...",
                "Cancel",
                0, 100,
                self
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.setWindowTitle("Duplicate Detection")
            progress.setMinimumDuration(0)
            progress.show()

            # Initialize services
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)
            asset_repo = AssetRepository(db_conn)
            asset_service = AssetService(photo_repo, asset_repo)

            results = {
                'exact_duplicates': 0,
                'similar_stacks': 0,
                'photos_processed': len(photo_ids)
            }

            # Step 1: Exact duplicate detection (hash-based)
            if options.get('detect_exact', False):
                progress.setLabelText("Computing photo hashes...")
                progress.setValue(20)
                QCoreApplication.processEvents()

                # Run hash backfill and link assets
                backfill_stats = asset_service.backfill_hashes_and_link_assets(
                    project_id=project_id,
                    photo_ids=photo_ids
                )

                progress.setLabelText("Finding exact duplicates...")
                progress.setValue(40)
                QCoreApplication.processEvents()

                # Get duplicate assets
                duplicates = asset_repo.list_duplicate_assets(project_id, min_instances=2)
                results['exact_duplicates'] = len(duplicates)

            # Step 2: Similar photo detection (AI-powered)
            if options.get('detect_similar', False):
                progress.setLabelText("Generating AI embeddings...")
                progress.setValue(60)
                QCoreApplication.processEvents()

                # Generate embeddings for photos that need them
                from services.embedding_service import EmbeddingService
                embedding_service = EmbeddingService()

                try:
                    model_id = embedding_service.load_clip_model()
                    photos_needing_embeddings = photo_repo.get_photos_needing_embeddings(project_id)

                    # Filter to only selected photos
                    selected_set = set(photo_ids)
                    photos_to_process = [
                        p for p in photos_needing_embeddings
                        if (p.get('id') or p.get('photo_id')) in selected_set
                    ]

                    for i, photo in enumerate(photos_to_process):
                        if progress.wasCanceled():
                            break

                        photo_id = photo.get('id') or photo.get('photo_id')
                        file_path = photo.get('path') or photo.get('file_path')

                        if file_path and photo_id:
                            try:
                                embedding = embedding_service.extract_image_embedding(file_path)
                                embedding_service.store_embedding(photo_id, embedding, model_id)
                            except Exception as e:
                                print(f"Failed to process photo {photo_id}: {e}")

                        # Update progress
                        embed_progress = 60 + int((i / max(len(photos_to_process), 1)) * 30)
                        progress.setValue(embed_progress)
                        progress.setLabelText(f"Generating embeddings... ({i+1}/{len(photos_to_process)})")
                        QCoreApplication.processEvents()

                except Exception as e:
                    print(f"Embedding generation failed: {e}")

                # Run similar shot detection
                progress.setLabelText("Finding similar photos...")
                progress.setValue(95)
                QCoreApplication.processEvents()

                # TODO: Integrate similar shot stacking here
                # For now, count existing similar stacks
                from repository.stack_repository import StackRepository
                stack_repo = StackRepository(db_conn)
                similar_stacks = stack_repo.list_stacks(project_id, stack_type="similar")
                results['similar_stacks'] = len(similar_stacks)

            # Close progress
            progress.setValue(100)
            progress.close()

            # Show results
            QMessageBox.information(
                self,
                "Duplicate Detection Complete",
                f"Duplicate detection completed!\n\n"
                f"Photos processed: {results['photos_processed']:,}\n"
                f"Exact duplicate groups: {results['exact_duplicates']:,}\n"
                f"Similar photo stacks: {results['similar_stacks']:,}\n\n"
                f"You can view and manage duplicates from the Duplicates section."
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Detection Error",
                f"Failed to run duplicate detection:\n{str(e)}"
            )
            print(f"✗ Duplicate detection error: {e}")
            import traceback
            traceback.print_exc()

    def _on_find_similar_photos(self):
        """Launch similar photo detection dialog."""
        try:
            # Get project_id
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(self, "No Project", "No project is currently active.")
                return

            # Import and show dialog
            from ui.similar_photo_dialog import SimilarPhotoDetectionDialog
            dialog = SimilarPhotoDetectionDialog(project_id=project_id, parent=self)
            dialog.exec()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Similar Photo Detection Error",
                f"Failed to launch similar photo detection:\n{str(e)}"
            )
            print(f"✗ Similar photo detection error: {e}")
            import traceback
            traceback.print_exc()

    def _on_show_duplicate_status(self):
        """Show duplicate detection status."""
        try:
            # Get project_id
            project_id = None
            if hasattr(self, 'grid') and hasattr(self.grid, 'project_id'):
                project_id = self.grid.project_id
            elif hasattr(self, 'sidebar') and hasattr(self.sidebar, 'project_id'):
                project_id = self.sidebar.project_id

            if project_id is None:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            if project_id is None:
                QMessageBox.warning(self, "No Project", "No project is currently active.")
                return

            # Query database for duplicate stats
            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db._connect() as conn:
                cur = conn.cursor()

                # Exact duplicates (media assets)
                cur.execute("""
                    SELECT COUNT(*) FROM media_asset
                    WHERE project_id = ? AND content_hash IS NOT NULL
                """, (project_id,))
                total_assets = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(DISTINCT content_hash) FROM media_asset
                    WHERE project_id = ? AND content_hash IS NOT NULL
                """, (project_id,))
                unique_hashes = cur.fetchone()[0]

                exact_duplicates = total_assets - unique_hashes

                # Similar stacks
                cur.execute("""
                    SELECT COUNT(*) FROM media_stack
                    WHERE project_id = ?
                """, (project_id,))
                similar_stacks = cur.fetchone()[0]

                # Photos with embeddings
                cur.execute("""
                    SELECT COUNT(DISTINCT se.photo_id)
                    FROM semantic_embeddings se
                    JOIN photo_metadata p ON se.photo_id = p.id
                    WHERE p.project_id = ?
                """, (project_id,))
                photos_with_embeddings = cur.fetchone()[0]

                # Total photos
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata WHERE project_id = ?
                """, (project_id,))
                total_photos = cur.fetchone()[0]

            embed_percent = (photos_with_embeddings / total_photos * 100) if total_photos > 0 else 0

            QMessageBox.information(
                self,
                "Duplicate Detection Status",
                f"=== Duplicate Detection Status ===\n\n"
                f"Exact Duplicates: {exact_duplicates:,}\n"
                f"Similar Photo Stacks: {similar_stacks:,}\n\n"
                f"=== AI Readiness ===\n"
                f"Photos with embeddings: {photos_with_embeddings:,} / {total_photos:,} ({embed_percent:.1f}%)\n\n"
                f"{'✓ Ready for similarity detection!' if photos_with_embeddings > 10 else 'Need more embeddings for similarity detection.'}"
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Status Error",
                f"Failed to get duplicate status:\n{str(e)}"
            )
            print(f"✗ Duplicate status error: {e}")
            import traceback
            traceback.print_exc()

    def _on_clear_thumbnail_cache(self):
        if QMessageBox.question(
            self,
            "Clear Thumbnail Cache",
            "This will delete all generated thumbnail cache files.\n"
            "They will be rebuilt automatically as needed.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            ok = clear_thumbnail_cache()
            if ok:
                QMessageBox.information(self, tr('message_boxes.cache_cleared_title'), tr('message_boxes.cache_cleared_message'))
            else:
                QMessageBox.warning(self, tr('message_boxes.cache_title'), tr('message_boxes.cache_not_found'))


    def _open_preferences(self):
        dlg = PreferencesDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            # Apply live UI updates if needed
            if self.settings.get("dark_mode", False):
                self._apply_dark_mode()
            else:
                self._apply_light_mode()


    def _switch_layout(self, layout_id: str):
        """
        Switch to a different UI layout.

        Args:
            layout_id: ID of the layout to switch to (e.g., "current", "google", "apple")
        """
        try:
            success = self.layout_manager.switch_layout(layout_id)
            if success:
                print(f"[MainWindow] ✓ Switched to layout: {layout_id}")
            else:
                print(f"[MainWindow] ✗ Failed to switch to layout: {layout_id}")
                QMessageBox.warning(
                    self,
                    "Layout Switch Failed",
                    f"Could not switch to layout: {layout_id}"
                )
        except Exception as e:
            print(f"[MainWindow] ✗ Error switching layout: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Layout Error",
                f"An error occurred while switching layouts:\n{e}"
            )


    def _apply_dark_mode(self):
        """Switch the app palette to dark mode (safe for PySide6)."""
        app = QApplication.instance()
        dark = QPalette()
        dark.setColor(QPalette.Window, QColor(30, 30, 30))
        dark.setColor(QPalette.WindowText, Qt.white)
        dark.setColor(QPalette.Base, QColor(25, 25, 25))
        dark.setColor(QPalette.AlternateBase, QColor(35, 35, 35))
        dark.setColor(QPalette.ToolTipBase, QColor(0, 0, 0))
        dark.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
        dark.setColor(QPalette.Text, Qt.white)
        dark.setColor(QPalette.Button, QColor(45, 45, 45))
        dark.setColor(QPalette.ButtonText, Qt.white)
        dark.setColor(QPalette.BrightText, Qt.red)
        dark.setColor(QPalette.Link, QColor(42, 130, 218))
        dark.setColor(QPalette.Highlight, QColor(42, 130, 218))
        dark.setColor(QPalette.HighlightedText, Qt.black)
        app.setPalette(dark)
        app.setStyleSheet("""
            QToolTip {
                color: #f0f0f0;
                background-color: rgba(20,20,20,0.96);
                border: 1px solid #444;
                padding: 6px 10px;
                border-radius: 6px;
            }
            QMessageBox {
                background-color: #121212;
                color: #f0f0f0;
            }
            QMessageBox QLabel { color: #f0f0f0; }
            QMessageBox QPushButton {
                background: rgba(255,255,255,0.15);
                color: #f0f0f0;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QMessageBox QPushButton:hover {
                background: rgba(255,255,255,0.25);
            }
        """)


    def _run_date_migration(self):
        """
        Manual, idempotent migration:
          1) Ensure created_* columns + indexes
          2) Backfill in chunks with progress dialog
        """
        # Step 1: Get database reference
        # NOTE: Schema columns are automatically created via migration system
        db = ReferenceDB()

        # Step 2: how much to do? (check for data to backfill)
        total = db.count_missing_created_fields()
        if total == 0:
            QMessageBox.information(self, tr('message_boxes.migration_title'), tr('message_boxes.migration_nothing'))
            return

        progress = QProgressDialog("Backfilling created_* fields…", "Cancel", 0, total, self)
        progress.setWindowTitle("Database Migration")
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        processed = 0
        CHUNK = 1000
        while processed < total:
            if progress.wasCanceled():
                break
            n = db.single_pass_backfill_created_fields(CHUNK)
            if n <= 0:
                break
            processed += n
            progress.setValue(min(processed, total))
            QApplication.processEvents()

        progress.setValue(total)
        progress.close()
        # Optional: refresh sidebar, in case you already render date branches
        try:
            self.sidebar.reload()
        except Exception:
            pass
        QMessageBox.information(self, "Migration", f"Completed. Updated ~{processed} rows.")


    def _db_fresh_start(self):
        """
        Delete/backup the current DB file and recreate an empty schema.
        Then clear UI and let the user run a new scan.
        """
        ret = QMessageBox.warning(
            self,
            "Fresh Start (delete DB)",
            "This will erase the current database (a backup .bak_YYYYMMDD_HHMMSS will be created if possible).\n\n"
            "Are you sure you want to continue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        try:
            # --- 🛑 Stop anything that might hold the DB ---
            if hasattr(self, "thumb_grid"):
                self.thumb_grid.shutdown_threads()  # stop background loaders

            if hasattr(self, "sidebar"):
                self.sidebar.db = None  # release ReferenceDB handle

            # --- 🧹 Reset DB ---
            db = ReferenceDB()
            db.fresh_reset()

            # --- 🧼 Clear UI ---
            if hasattr(self, "grid"):
                self.grid.clear()

            if hasattr(self, "search_controller"):
                self.search_controller.set_active_project(None)

            if hasattr(self, "sidebar"):
                # rebind fresh DB after reset
                self.sidebar.db = ReferenceDB()
                self.sidebar.reload()

            QMessageBox.information(
                self,
                "Database Reset",
                "✅ A fresh empty database was created.\n\nNow run: 📂 Scan Repository…"
            )

        except Exception as e:
            QMessageBox.critical(self, "Reset Failed", f"❌ {e}")


    def _db_self_check(self):
        """
        Run integrity PRAGMA and show basic counts to confirm a healthy scan.
        """
        try:
            db = ReferenceDB()
            rep = db.integrity_report()
            counts = rep.get("counts", {})
            ok = rep.get("ok", False)
            errors = rep.get("errors", [])
            msg = []
            msg.append("Integrity: " + ("OK ✅" if ok else "FAIL ❌"))
            msg.append("")
            msg.append("Counts:")
            msg.append(f"  • photo_folders:   {counts.get('photo_folders', 0)}")
            msg.append(f"  • photo_metadata:  {counts.get('photo_metadata', 0)}")
            msg.append(f"  • projects:        {counts.get('projects', 0)}")
            msg.append(f"  • branches:        {counts.get('branches', 0)}")
            msg.append(f"  • project_images:  {counts.get('project_images', 0)}")
            if errors:
                msg.append("")
                msg.append("Warnings/Errors:")
                for e in errors:
                    msg.append(f"  - {e}")
            QMessageBox.information(self, "DB Self-Check", "\n".join(msg))
        except Exception as e:
            QMessageBox.critical(self, "Self-Check Failed", str(e))


    def _db_rebuild_date_index(self):
        """
        Optional hook: if you implemented a date index builder (e.g., build_date_index_by_day),
        call it here. Otherwise, show a friendly message.
        """
        try:
            db = ReferenceDB()
            # If you already added: db.build_date_index_by_day()
            if hasattr(db, "build_date_index_by_day"):
                n = db.build_date_index_by_day()  # hypothetical return: rows added/updated
                QMessageBox.information(self, "Date Index", f"Date index rebuilt.\nUpdated rows: {n}")
            else:
                QMessageBox.information(
                    self, "Date Index",
                    "Date index not implemented yet in ReferenceDB.\n"
                    "You can safely ignore this or wire it later."
                )
        except Exception as e:
            QMessageBox.critical(self, "Date Index Failed", str(e))


    def _exif_to_dict(self, path: str) -> dict:
        """Read EXIF using PIL. Return a flat dict of tag_name -> value (best-effort)."""
        try:
            from PIL import Image, ExifTags
            with Image.open(path) as img:
                raw = img.getexif() or {}
                tagmap = {ExifTags.TAGS.get(k, k): raw.get(k) for k in raw.keys()}
                # Flatten MakerNote garbage
                if "MakerNote" in tagmap:
                    tagmap.pop("MakerNote", None)
                return tagmap
        except Exception:
            return {}

    def _fmt_rational(self, v):
        """Handle PIL rational/tuples (e.g., (1, 125)) → float or friendly str."""
        try:
            # fractions
            if hasattr(v, "numerator") and hasattr(v, "denominator"):
                return float(v.numerator) / float(v.denominator) if v.denominator else 0.0
            # (num, den) tuple
            if isinstance(v, (tuple, list)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
                return float(v[0]) / float(v[1]) if v[1] else 0.0
            return v
        except Exception:
            return v

    def _parse_exif_exposure(self, exif: dict) -> dict:
        """Return prettified ISO / shutter / aperture / focal."""
        out = {}
        # ISO
        iso = exif.get("ISOSpeedRatings") or exif.get("PhotographicSensitivity")
        if isinstance(iso, (list, tuple)):
            iso = iso[0] if iso else None
        if iso:
            out["ISO"] = f"{iso}"
        # Shutter
        shutter = exif.get("ExposureTime")
        if shutter:
            r = self._fmt_rational(shutter)
            if isinstance(r, (int, float)) and r > 0:
                # pretty as 1/xxx for small values
                out["Shutter"] = f"1/{int(round(1 / r))}" if r < 1 else f"{r:.2f}s"
            else:
                out["Shutter"] = f"{shutter}"
        # Aperture
        fnum = exif.get("FNumber")
        if fnum:
            f = self._fmt_rational(fnum)
            out["Aperture"] = f"f/{f:.1f}" if isinstance(f, (int, float)) else f"f/{f}"
        # Focal length
        fl = exif.get("FocalLength")
        if fl:
            f = self._fmt_rational(fl)
            out["Focal Length"] = f"{f:.0f} mm" if isinstance(f, (int, float)) else f"{f} mm"
        return out

    def _gps_to_degrees(self, gps):
        """Convert GPS IFD to (lat, lon) in decimal degrees if possible."""
        try:
            def _conv(ref, vals):
                def _rat(x):
                    if hasattr(x, "numerator") and hasattr(x, "denominator"):
                        return float(x.numerator) / float(x.denominator) if x.denominator else 0.0
                    if isinstance(x, (tuple, list)) and len(x) == 2:
                        return float(x[0]) / float(x[1]) if x[1] else 0.0
                    return float(x)
                d, m, s = (_rat(vals[0]), _rat(vals[1]), _rat(vals[2]))
                deg = d + m/60.0 + s/3600.0
                if ref in ("S", "W"):
                    deg = -deg
                return deg

            lat = lon = None
            gps_lat = gps.get("GPSLatitude")
            gps_lat_ref = gps.get("GPSLatitudeRef")
            gps_lon = gps.get("GPSLongitude")
            gps_lon_ref = gps.get("GPSLongitudeRef")
            if gps_lat and gps_lat_ref and gps_lon and gps_lon_ref:
                lat = _conv(gps_lat_ref, gps_lat)
                lon = _conv(gps_lon_ref, gps_lon)
            return (lat, lon)
        except Exception:
            return (None, None)

    def _build_meta_table(self, rows: list[tuple[str, str]]) -> str:
        safe = []
        for k, v in rows:
            val = "-" if v is None or v == "" else str(v)
            safe.append(f"<tr><td><b>{k}</b></td><td>{val}</td></tr>")
        return "<table cellspacing='2' cellpadding='2'>" + "".join(safe) + "</table>"


    def _apply_light_mode(self):
        """Revert to the default (light) palette."""
        app = QApplication.instance()
        app.setPalette(QApplication.style().standardPalette())


    def _on_toggle_backfill_panel(self, visible: bool):
        """
        Handler for the toolbar toggle action. Ensures the BackfillStatusPanel exists,
        inserts it into the main layout if necessary, toggles visibility and persists the choice.
        """
        try:
            # Ensure we have a BackfillStatusPanel instance (lazy-create if necessary)
            if not hasattr(self, "backfill_panel") or self.backfill_panel is None:
                try:
                    # Try to create and insert at the top of the central layout
                    self.backfill_panel = BackfillStatusPanel(self)
                    # Insert at top if possible
                    try:
                        container = self.centralWidget()
                        if container is not None:
                            layout = container.layout()
                            if layout is not None:
                                layout.insertWidget(0, self.backfill_panel)
                    except Exception:
                        # If insertion fails, fallback to adding to layout end (best-effort)
                        try:
                            main_layout = getattr(self, "main_layout", None)
                            if main_layout is not None and hasattr(main_layout, "addWidget"):
                                main_layout.addWidget(self.backfill_panel)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[MainWindow] Failed to create BackfillStatusPanel dynamically: {e}")
                    return

            # Apply requested visibility
            try:
                self.backfill_panel.setVisible(bool(visible))
            except Exception as e:
                print(f"[MainWindow] Could not set BackfillStatusPanel visibility: {e}")

            # Persist the choice
            try:
                self.settings.set("show_backfill_panel", bool(visible))
            except Exception:
                pass

        except Exception as e:
            print(f"[MainWindow] _on_toggle_backfill_panel error: {e}")

    def _on_project_changed(self, idx: int):
        self.project_controller.on_project_changed(idx)

    def _on_project_changed_by_id(self, project_id: int):
        """Switch to a project by ID (Phase 1B: delegates to ProjectSwitchService)."""
        self._project_switch_service.on_project_changed_by_id(project_id)

    # ── Phase 5: People shell helpers ─────────────────────────────

    def _refresh_people_quick_section(self):
        """
        Populate the passive People shell from legacy People section data.
        """
        try:
            payload = {
                "top_people": [],
                "merge_candidates": 0,
                "unnamed_count": 0,
            }

            people_section = None

            if hasattr(self, "layout_manager"):
                layout = self.layout_manager.get_current_layout()
                if layout and hasattr(layout, "accordion_sidebar"):
                    if hasattr(layout.accordion_sidebar, "section_logic"):
                        people_section = layout.accordion_sidebar.section_logic.get("people")

            if not people_section and hasattr(self, "sidebar") and hasattr(self.sidebar, "accordion"):
                if hasattr(self.sidebar.accordion, "section_logic"):
                    people_section = self.sidebar.accordion.section_logic.get("people")

            if people_section:
                if hasattr(people_section, "get_people_quick_payload"):
                    raw = people_section.get_people_quick_payload() or {}
                    payload["top_people"] = raw.get("top_people", []) or []
                    payload["merge_candidates"] = raw.get("merge_candidates", 0) or 0
                    payload["unnamed_count"] = raw.get("unnamed_count", 0) or 0

                if not payload["top_people"] and hasattr(people_section, "get_merge_suggestions"):
                    try:
                        suggestions = people_section.get_merge_suggestions() or []
                        payload["merge_candidates"] = len(suggestions)
                    except Exception:
                        pass

                if hasattr(people_section, "get_unnamed_clusters"):
                    try:
                        unnamed = people_section.get_unnamed_clusters() or []
                        payload["unnamed_count"] = len(unnamed)
                    except Exception:
                        pass

            # Push payload to SearchSidebar (non-google layouts)
            if hasattr(self, "sidebar") and hasattr(self.sidebar, "set_people_quick_payload"):
                self.sidebar.set_people_quick_payload(payload)

            # Also push to google layout's SearchSidebar if present
            layout = self.layout_manager.get_current_layout() if hasattr(self, "layout_manager") else None
            if layout and hasattr(layout, "search_sidebar") and layout.search_sidebar:
                if hasattr(layout.search_sidebar, "set_people_quick_payload"):
                    layout.search_sidebar.set_people_quick_payload(payload)

        except Exception as e:
            print(f"[MainWindow] _refresh_people_quick_section failed: {e}")

    def _handle_people_branch(self, branch: str):
        """
        Phase 5: Handle People branch requests from the passive shell.
        Routes to the layout's own handler methods.
        """
        try:
            layout = None
            try:
                if hasattr(self, "layout_manager") and self.layout_manager:
                    layout = self.layout_manager.get_current_layout()
            except Exception:
                layout = None

            if branch == "people_merge_review":
                self._open_people_merge_review()
                return

            if branch == "people_unnamed":
                self._open_unnamed_cluster_review()
                return

            if branch == "people_show_all":
                try:
                    if layout and hasattr(layout, "accordion_sidebar"):
                        if hasattr(layout.accordion_sidebar, "_expand_section"):
                            layout.accordion_sidebar._expand_section("people")
                            return
                except Exception as e:
                    print(f"[Routing] people_show_all failed: {e}")

            if branch == "people_tools":
                try:
                    if layout and hasattr(layout, "_on_people_tools_requested"):
                        layout._on_people_tools_requested()
                        return
                except Exception as e:
                    print(f"[Routing] people_tools failed: {e}")

            if branch == "people_merge_history":
                try:
                    if layout and hasattr(layout, "_on_people_merge_history_requested"):
                        layout._on_people_merge_history_requested()
                        return
                except Exception as e:
                    print(f"[Routing] people_merge_history failed: {e}")

            if branch == "people_undo_merge":
                try:
                    if layout and hasattr(layout, "_on_people_undo_requested"):
                        layout._on_people_undo_requested()
                        return
                except Exception as e:
                    print(f"[Routing] people_undo_merge failed: {e}")

            if branch == "people_redo_merge":
                try:
                    if layout and hasattr(layout, "_on_people_redo_requested"):
                        layout._on_people_redo_requested()
                        return
                except Exception as e:
                    print(f"[Routing] people_redo_merge failed: {e}")

            if branch == "people_expand":
                try:
                    if layout and hasattr(layout, "accordion_sidebar"):
                        if hasattr(layout.accordion_sidebar, "_expand_section"):
                            layout.accordion_sidebar._expand_section("people")
                            return
                except Exception as e:
                    print(f"[Routing] people_expand failed: {e}")

            if branch.startswith("people_person:"):
                person_id = branch.split(":", 1)[1]
                try:
                    if layout and hasattr(layout, "_on_accordion_person_clicked"):
                        layout._on_accordion_person_clicked(person_id)
                        return
                except Exception as e:
                    print(f"[Routing] people_person failed: {e}")

        except Exception as e:
            print(f"[MainWindow] _handle_people_branch failed: {branch} → {e}")

    def _open_people_merge_review(self):
        """Phase 5: Open the merge review dialog via layout's stable handler."""
        try:
            layout = self.layout_manager.get_current_layout() if hasattr(self, "layout_manager") else None
            if layout and hasattr(layout, "_show_merge_suggestions_dialog"):
                if hasattr(layout, "_suggest_cluster_merges"):
                    try:
                        from app_services import get_face_service
                        face_service = get_face_service()
                        project_id = getattr(layout, "project_id", None)
                        if face_service and project_id:
                            clusters = face_service.list_clusters(project_id) or []
                            named = [c for c in clusters if c.get("display_name")]
                            suggestions = layout._suggest_cluster_merges(named)
                            if suggestions:
                                layout._show_merge_suggestions_dialog(suggestions)
                                return
                    except Exception:
                        pass
            # Fallback: expand people accordion section
            if layout and hasattr(layout, "accordion_sidebar"):
                if hasattr(layout.accordion_sidebar, "_expand_section"):
                    layout.accordion_sidebar._expand_section("people")
        except Exception as e:
            print(f"[MainWindow] _open_people_merge_review failed: {e}")

    def _open_unnamed_cluster_review(self):
        """Phase 5: Open unnamed cluster review via legacy People section."""
        try:
            layout = self.layout_manager.get_current_layout() if hasattr(self, "layout_manager") else None
            # Expand people accordion to show unnamed clusters
            if layout and hasattr(layout, "accordion_sidebar"):
                if hasattr(layout.accordion_sidebar, "_expand_section"):
                    layout.accordion_sidebar._expand_section("people")
        except Exception as e:
            print(f"[MainWindow] _open_unnamed_cluster_review failed: {e}")

    def _handle_search_sidebar_branch_request(self, branch: str):
        """
        Phase 10C:
        Shell dynamic tree routing for dates, folders, videos, review, and locations.
        """
        try:
            if branch.startswith("people_"):
                if hasattr(self, "_handle_people_branch"):
                    self._handle_people_branch(branch)
                    return

            layout = None
            try:
                if hasattr(self, "layout_manager") and self.layout_manager:
                    layout = self.layout_manager.get_current_layout()
            except Exception:
                layout = None

            if layout and hasattr(layout, "_on_passive_shell_branch_clicked"):
                layout._on_passive_shell_branch_clicked(branch)
                return

            if hasattr(self, "sidebar"):
                try:
                    self.sidebar.selectBranch.emit(branch)
                except Exception:
                    pass

        except Exception as e:
            print(f"[MainWindow] _handle_search_sidebar_branch_request failed: {branch} → {e}")

    # ── end Phase 10 ─────────────────────────────────────────────────

    def _on_ux1_search_requested(self, payload: dict):
        """
        UX-1 bridge: connect the new search shell to the existing search pipeline.
        This keeps UX-1 low-risk while establishing proper ownership and state flow.
        """
        query_text = payload.get("query_text", "") or ""
        preset_id = payload.get("preset_id")

        try:
            if preset_id and hasattr(self, "_execute_smart_find_preset"):
                self._execute_smart_find_preset(preset_id)
                result_paths = list(getattr(self.grid, "all_photo_paths", []) or [])
                self.search_controller.apply_result_summary(
                    result_paths=result_paths,
                    result_count=len(result_paths),
                    family=None,
                    result_facets={},
                    warnings=[],
                )
                return

            if query_text and hasattr(self, "_on_quick_search"):
                self._on_quick_search(query_text)
                result_paths = list(getattr(self.grid, "all_photo_paths", []) or [])
                self.search_controller.apply_result_summary(
                    result_paths=result_paths,
                    result_count=len(result_paths),
                    family=None,
                    result_facets={},
                    warnings=[],
                )
                return

            self.search_controller.apply_result_summary(
                result_paths=[],
                result_count=0,
                family=None,
                result_facets={},
                warnings=[],
            )

        except Exception as e:
            self.search_controller.apply_result_summary(
                result_paths=[],
                result_count=0,
                family=None,
                result_facets={},
                warnings=[str(e)],
            )

    def _execute_smart_find_preset(self, preset_id: str):
        """
        UX-1 bridge: executes a smart find preset via the sidebar accordion.
        """
        try:
            layout = self.layout_manager.get_current_layout()
            # Try GooglePhotosLayout path
            if layout and hasattr(layout, "accordion_sidebar"):
                find_section = layout.accordion_sidebar.section_logic.get("find")
                if find_section and hasattr(find_section, "_on_preset_clicked"):
                    print(f"[UX-1 Bridge] Triggering preset {preset_id} on layout {type(layout).__name__}")
                    find_section._on_preset_clicked(preset_id)
                    return

            # Try SidebarQt path (Current Layout)
            if hasattr(self, "sidebar") and hasattr(self.sidebar, "accordion"):
                find_section = self.sidebar.accordion.section_logic.get("find")
                if find_section and hasattr(find_section, "_on_preset_clicked"):
                    print(f"[UX-1 Bridge] Triggering preset {preset_id} on SidebarQt")
                    find_section._on_preset_clicked(preset_id)

        except Exception as e:
            print(f"[UX-1 Bridge] Error: {e}")

    def _refresh_project_list(self):
        """Project list cache refresh (Phase 1B: delegates to ProjectSwitchService)."""
        self._project_switch_service.refresh_project_list()

    def _restore_session_state(self):
        """Session state restore (Phase 1B: delegates to ProjectSwitchService)."""
        self._project_switch_service.restore_session_state()

    def _restore_selection(self, session_state, accordion_sidebar):
        """Accordion selection restore (Phase 1B: delegates to ProjectSwitchService)."""
        self._project_switch_service.restore_selection(session_state, accordion_sidebar)

    def _restore_selection_sidebarqt(self, session_state):
        """SidebarQt selection restore (Phase 1B: delegates to ProjectSwitchService)."""
        self._project_switch_service.restore_selection_sidebarqt(session_state)

    def _on_folder_selected(self, folder_id: int):
        # DELEGATED to SidebarController (legacy stub kept for compatibility)
        self.sidebar_controller.on_folder_selected(folder_id)


    def _on_branch_selected(self, branch_key: str):
        # DELEGATED to SidebarController (legacy stub kept for compatibility)
        self.sidebar_controller.on_branch_selected(branch_key)


    def _on_scan_repository(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Photo Repository")
        if not folder:
            return
        incremental = self.chk_incremental.isChecked()
        self.scan_controller.start_scan(folder, incremental)

    # 👇 Optional: keep cancel button behavior simple
    def _on_cancel_scan_clicked(self):
        self.scan_controller.cancel()

    def _apply_sort_filter(self):
        """
        Apply sorting/filtering to the grid based on toolbar combo boxes.
        """
        sort_field = self.sort_combo.currentText().lower()
        descending = self.sort_order_combo.currentText() == "Descending"
        self.grid.apply_sorting(sort_field, descending)

        # Phase 2.3: Update rich status bar after sorting
        self._update_status_bar()

    def _set_grid_preset(self, size: str):
        """
        Phase 2.3: Set grid thumbnail size using preset (Google Photos style).
        Instantly resizes grid to Small (90px), Medium (120px), Large (200px), or XL (280px).

        Args:
            size: One of "small", "medium", "large", "xl"
        """
        if not hasattr(self, "grid") or not self.grid:
            return

        # Map presets to zoom factors
        # Formula: zoom_factor = target_height / _thumb_base (where _thumb_base = 120)
        presets = {
            "small": 0.75,    # 90px
            "medium": 1.0,    # 120px (default)
            "large": 1.67,    # 200px
            "xl": 2.33        # 280px
        }

        zoom_factor = presets.get(size, 1.0)

        # Apply zoom with animation
        if hasattr(self.grid, "_animate_zoom_to"):
            self.grid._animate_zoom_to(zoom_factor, duration=150)
        elif hasattr(self.grid, "_set_zoom_factor"):
            self.grid._set_zoom_factor(zoom_factor)

        print(f"[Grid Preset] Set to {size} (zoom: {zoom_factor})")

    # === Phase 3: Enhanced Menu Handlers ===

    def _on_zoom_in(self):
        """Phase 3: Zoom in (increase thumbnail size)."""
        if not hasattr(self, "grid") or not self.grid:
            return

        current_zoom = getattr(self.grid, "_zoom_factor", 1.0)
        new_zoom = min(current_zoom + 0.25, 3.0)  # Max zoom 3.0

        if hasattr(self.grid, "_animate_zoom_to"):
            self.grid._animate_zoom_to(new_zoom, duration=150)
        elif hasattr(self.grid, "_set_zoom_factor"):
            self.grid._set_zoom_factor(new_zoom)

        print(f"[Menu Zoom] Zoom In: {current_zoom:.2f} → {new_zoom:.2f}")

    def _on_zoom_out(self):
        """Phase 3: Zoom out (decrease thumbnail size)."""
        if not hasattr(self, "grid") or not self.grid:
            return

        current_zoom = getattr(self.grid, "_zoom_factor", 1.0)
        new_zoom = max(current_zoom - 0.25, 0.5)  # Min zoom 0.5

        if hasattr(self.grid, "_animate_zoom_to"):
            self.grid._animate_zoom_to(new_zoom, duration=150)
        elif hasattr(self.grid, "_set_zoom_factor"):
            self.grid._set_zoom_factor(new_zoom)

        print(f"[Menu Zoom] Zoom Out: {current_zoom:.2f} → {new_zoom:.2f}")

    def _apply_menu_sort(self, sort_type: str):
        """Phase 3: Apply sorting from View menu."""
        if not hasattr(self, "sort_combo"):
            return

        # Map menu sort type to combo box index
        sort_map = {
            "Filename": 0,
            "Date": 1,
            "Size": 2
        }

        index = sort_map.get(sort_type, 0)
        self.sort_combo.setCurrentIndex(index)
        self._apply_sort_filter()

        print(f"[Menu Sort] Applied: {sort_type}")

    def _on_toggle_sidebar_visibility(self, checked: bool):
        """Phase 3: Show/hide sidebar from View menu."""
        if hasattr(self, "sidebar") and self.sidebar:
            self.sidebar.setVisible(checked)
            print(f"[Menu Sidebar] Visibility: {checked}")

    def _show_keyboard_shortcuts(self):
        """Phase 3: Show keyboard shortcuts help dialog."""
        shortcuts_text = """
<h2>Keyboard Shortcuts</h2>

<h3>Navigation</h3>
<table>
<tr><td><b>Arrow Keys</b></td><td>Navigate grid</td></tr>
<tr><td><b>Space / Enter</b></td><td>Open lightbox</td></tr>
<tr><td><b>Escape</b></td><td>Clear selection / Close lightbox</td></tr>
</table>

<h3>Selection</h3>
<table>
<tr><td><b>Ctrl+Click</b></td><td>Toggle selection</td></tr>
<tr><td><b>Shift+Click</b></td><td>Range selection</td></tr>
<tr><td><b>Ctrl+A</b></td><td>Select all</td></tr>
</table>

<h3>View</h3>
<table>
<tr><td><b>Ctrl++</b></td><td>Zoom in</td></tr>
<tr><td><b>Ctrl+-</b></td><td>Zoom out</td></tr>
<tr><td><b>Ctrl+B</b></td><td>Toggle sidebar</td></tr>
<tr><td><b>Ctrl+Alt+S</b></td><td>Toggle sidebar mode (List/Tabs)</td></tr>
</table>

<h3>Actions</h3>
<table>
<tr><td><b>Ctrl+F</b></td><td>Focus Smart Find search</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Scan repository</td></tr>
<tr><td><b>Ctrl+,</b></td><td>Preferences</td></tr>
<tr><td><b>F1</b></td><td>Show keyboard shortcuts</td></tr>
</table>
        """

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Keyboard Shortcuts")
        msg_box.setTextFormat(Qt.TextFormat.RichText)
        msg_box.setText(shortcuts_text)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()

    def _focus_smart_find(self):
        """Focus Smart Find search input via Ctrl+F shortcut."""
        try:
            accordion = None
            if hasattr(self, 'sidebar'):
                if hasattr(self.sidebar, '_expand_section'):
                    accordion = self.sidebar
                elif hasattr(self.sidebar, 'accordion_controller') and self.sidebar.accordion_controller:
                    accordion = self.sidebar.accordion_controller
                elif hasattr(self.sidebar, 'accordion') and self.sidebar.accordion:
                    accordion = self.sidebar.accordion
            if accordion and hasattr(accordion, 'section_logic'):
                accordion._expand_section("find")
                find_section = accordion.section_logic.get("find")
                if find_section and hasattr(find_section, 'focus_search'):
                    QTimer.singleShot(150, find_section.focus_search)
        except Exception as e:
            print(f"[MainWindow] Ctrl+F focus_smart_find failed: {e}")

    def _open_url(self, url: str):
        """Phase 3: Open URL in default browser."""
        try:
            import webbrowser
            webbrowser.open(url)
            print(f"[Menu] Opened URL: {url}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open URL:\n{url}\n\nError: {e}")

    def _open_lightbox_from_selection(self):
        """Open the last selected image in lightbox."""
        paths = self.grid.get_selected_paths()
        print(f"[MAIN_open_lightbox_from_selection] paths: {paths}")
        if paths:
            self._open_lightbox(paths[-1])

    # === Phase 3, Step 3.1: Photo Operations Extracted =========================
    # NOTE: Photo operations (favorite, tag, export, move, delete) have been
    #       extracted to controllers/photo_operations_controller.py.
    #       Methods below delegate to PhotoOperationsController.
    #       See: controllers/photo_operations_controller.py
    #
    # Reduction: ~143 LOC moved to separate controller
    # ===========================================================================

    def _toggle_favorite_selection(self):
        """Delegate to PhotoOperationsController."""
        self.photo_ops_controller.toggle_favorite_selection()

    def _add_tag_to_selection(self):
        """Delegate to PhotoOperationsController."""
        self.photo_ops_controller.add_tag_to_selection()

    def _export_selection_to_folder(self):
        """Delegate to PhotoOperationsController."""
        self.photo_ops_controller.export_selection_to_folder()

    def _move_selection_to_folder(self):
        """Delegate to PhotoOperationsController."""
        self.photo_ops_controller.move_selection_to_folder()

    # ============================================================
    # Phase 8: Face Grouping Handlers (moved from People tab)
    # ============================================================

    def _on_detect_and_group_faces(self):
        """
        Launch automatic face grouping pipeline via FacePipelineService.

        Pipeline: Detection -> Clustering -> UI Refresh (all background).
        Progress shown in status bar.  No modal dialogs.
        """
        try:
            from PySide6.QtWidgets import QMessageBox, QDialog

            # Get current project ID
            project_id = None

            for attr in ("current_project_id", "_current_project_id", "project_id"):
                project_id = getattr(self, attr, None)
                if project_id:
                    break

            if not project_id:
                store = getattr(self, "store", None)
                if store:
                    project_id = store.get_state().get("project_id")

            if not project_id:
                # Fallback to grid project_id
                project_id = getattr(self.grid, "project_id", None)

            if not project_id:
                QMessageBox.warning(self, "No Project", "No active project selected.")
                return

            # Scope selection dialog (user-initiated, OK to be modal)
            from ui.face_detection_scope_dialog import FaceDetectionScopeDialog
            scope_dialog = FaceDetectionScopeDialog(project_id, parent=self)
            selected_paths = []
            selected_policy = "detect_only"
            selected_include_all = False

            def on_scope_selected(paths, policy, include_all):
                nonlocal selected_paths, selected_policy, selected_include_all
                selected_paths = paths
                selected_policy = policy
                selected_include_all = include_all

            scope_dialog.scopeSelected.connect(on_scope_selected)
            if scope_dialog.exec() != QDialog.Accepted or not selected_paths:
                return

            # Launch via central service (no modal progress, no inline workers)
            from services.face_pipeline_service import FacePipelineService
            svc = FacePipelineService.instance()

            if svc.is_running(project_id):
                self.statusBar().showMessage("Face pipeline already running for this project", 5000)
                return

            started = svc.start(
                project_id=project_id,
                photo_paths=selected_paths,
                screenshot_policy=selected_policy,
                include_all_screenshot_faces=selected_include_all,
            )
            if started:
                self.statusBar().showMessage(
                    f"Face pipeline started: {len(selected_paths)} photos, screenshot policy={selected_policy}", 0
                )
            else:
                self.statusBar().showMessage("Failed to start face pipeline", 5000)

        except ImportError as e:
            logger.error(f"[MainWindow] ImportError in _on_detect_and_group_faces: {e}", exc_info=True)
            from PySide6.QtWidgets import QMessageBox

            error_msg = str(e)
            if "insightface" in error_msg.lower():
                title = "Missing Library: InsightFace"
                text = (
                    "InsightFace library not installed.\n\n"
                    "Install with:\npip install insightface onnxruntime"
                )
            else:
                title = "Import Error"
                text = f"A required component could not be loaded.\n\nError: {error_msg}"

            QMessageBox.critical(self, title, text)

        except Exception as e:
            logger.error(f"[MainWindow] Unexpected error in _on_detect_and_group_faces: {e}", exc_info=True)
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Face Grouping Failed", f"An unexpected error occurred:\n{e}")

    def _on_recluster_faces(self):
        """
        Re-run clustering on existing face detections (no re-detection).

        Non-blocking: dispatches to QThreadPool, progress via status bar,
        People refresh via UIRefreshMediator.
        """
        try:
            from PySide6.QtCore import QThreadPool
            from PySide6.QtWidgets import QMessageBox
            from workers.face_cluster_worker import FaceClusterWorker
            from reference_db import ReferenceDB

            project_id = getattr(self.grid, "project_id", None)
            if not project_id:
                QMessageBox.warning(self, "No Project", "Please select a project first.")
                return

            # Quick validation — are there faces to cluster?
            db = ReferenceDB()
            with db._connect() as conn:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM face_crops WHERE project_id = ?", (project_id,)
                )
                face_count = cur.fetchone()[0]

            if face_count == 0:
                QMessageBox.warning(
                    self, "No Faces Detected",
                    "No faces have been detected yet.\n\n"
                    "Click Detect & Group Faces first to scan your photos."
                )
                return

            self.statusBar().showMessage(f"Re-clustering {face_count} faces...", 0)

            worker = FaceClusterWorker(project_id=project_id)

            def on_progress(current, total, message):
                if self._closing:
                    return
                try:
                    self.statusBar().showMessage(f"[Clustering] {message}", 0)
                except Exception:
                    pass

            def on_finished(cluster_count, total_faces):
                if self._closing:
                    return
                self.statusBar().showMessage(
                    f"Clustering complete: {total_faces} faces into {cluster_count} groups", 8000
                )
                # Refresh People section via mediator
                if hasattr(self, '_ui_refresh_mediator'):
                    self._ui_refresh_mediator.request_refresh(
                        {"people"}, "recluster_done", project_id
                    )

            def on_error(error_msg):
                if self._closing:
                    return
                self.statusBar().showMessage(f"Clustering failed: {error_msg}", 8000)

            worker.signals.progress.connect(on_progress)
            worker.signals.finished.connect(on_finished)
            worker.signals.error.connect(on_error)

            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._cluster_worker = worker

            QThreadPool.globalInstance().start(worker)

        except Exception as e:
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Re-Cluster Failed", str(e))

    def _request_delete_from_selection(self):
        """Delegate to PhotoOperationsController."""
        self.photo_ops_controller.request_delete_from_selection()

    def _confirm_delete(self, paths: list[str]):
        """Delegate to PhotoOperationsController."""
        self.photo_ops_controller.confirm_delete(paths)

    def _open_lightbox(self, path: str):
        """
        Open the LightboxDialog for the clicked path, and pass the full list of
        paths based on the current navigation context (folder / branch / date).
        """
        if not path:
            return

        from reference_db import ReferenceDB
        db = ReferenceDB()

        # 1) Build the "context paths" in this priority:
        #    folder -> branch -> date -> visible paths -> just [path]
        paths = []
        context = "unknown"

        # Folder context
        folder_id = getattr(self.grid, "current_folder_id", None)
        print(f"[open_lightbox] self.grid={self.grid}")
        print(f"[open_lightbox] folder_id={folder_id}")
        if folder_id is not None:
            try:
                project_id = getattr(self.grid, "project_id", None)
                paths = db.get_images_by_folder(folder_id, project_id=project_id)
                context = f"folder({folder_id})"

            except Exception as e:
                print(f"[open_lightbox] folder fetch failed: {e}")


        # Branch context (if no folder result)
        print(f"[open_lightbox] paths={paths}")
        branch_key = getattr(self.grid, "branch_key", None)
        if not paths and branch_key:
            try:
                paths = db.get_images_by_branch(self.grid.project_id, branch_key)
                context = f"branch({branch_key})"
            except Exception as e:
                print(f"[open_lightbox] branch fetch failed: {e}")
        print(f"[open_lightbox] branch_key={branch_key}")

        # Date context (if no folder/branch result)
        date_key = getattr(self.grid, "date_key", None)
        if not paths and date_key:
            try:
                if len(date_key) == 4 and date_key.isdigit():
                    paths = db.get_images_by_year(int(date_key))
                    context = f"year({date_key})"
                elif len(date_key) == 7 and date_key[4] == "-" and date_key[5:7].isdigit():
                    # format "YYYY-MM"
                    paths = db.get_images_by_month_str(date_key)
                    context = f"month({date_key})"
                elif len(date_key) == 10 and date_key[4] == "-" and date_key[7] == "-":
                    # format "YYYY-MM-DD"
                    # SURGICAL FIX C: Load both photos and videos for date nodes
                    paths = db.get_media_by_date(date_key, project_id=self.db_handler.project_id)
                    context = f"day({date_key})"
            except Exception as e:
                print(f"[open_lightbox] date fetch failed: {e}")
        print(f"[open_lightbox] date_key={date_key}")
        print(f"[open_lightbox] paths={paths}")

        # --- Fallback 1: what's visible on the grid right now?
        if not paths and hasattr(self.grid, "get_visible_paths"):
            try:
                paths = self.grid.get_visible_paths()
                context = "visible_paths"
            except Exception as e:
                print(f"[open_lightbox] get_visible_paths() failed: {e}")

        # --- Fallback 2: internal loaded model (even if view isn't built yet)
        if not paths and hasattr(self.grid, "get_all_paths"):
            try:
                paths = self.grid.get_all_paths()
                context = "all_paths"
            except Exception as e:
                print(f"[open_lightbox] get_all_paths() failed: {e}")

        # --- Final fallback: this single image only
        if not paths:
            paths = [path]
            context = "single"

        # Locate index of the clicked photo in `paths`
        try:
            idx = paths.index(path)
        except ValueError:
            # Different path normalization? try os.path.normcase/normpath
            try:
                norm = lambda p: os.path.normcase(os.path.normpath(p))
                idx = [norm(p) for p in paths].index(norm(path))
            except Exception:
                idx = 0

        print(f"[open_lightbox] context={context}, total={len(paths)}")
        print(f"[open_lightbox] paths={paths}")
        print(f"[open_lightbox] path={path}")

        # 🎬 UNIFIED MEDIA PREVIEW: Open LightboxDialog for BOTH photos AND videos
        # The LightboxDialog now handles both media types with adaptive controls
        print(f"[UnifiedPreview] Opening media in unified dialog: {path}")
        print(f"[UnifiedPreview] Media type: {'video' if is_video_file(path) else 'photo'}")

        # No filtering - pass ALL media paths (mixed photos and videos)
        # LightboxDialog will detect media type and show appropriate controls
        dlg = LightboxDialog(path, self)
        dlg.set_image_list(paths, idx)  # Pass full mixed media list
        dlg.resize(1200, 800)  # Larger default size for video viewing
        dlg.exec()

    def _open_video_player(self, video_path: str, video_list: list = None, start_index: int = 0):
        """
        Open video player for the given video path with navigation support.
        🎬 Phase 4.4: Video playback support
        🎬 Enhanced: Added video list and navigation support

        Args:
            video_path: Path to the video file
            video_list: List of video paths for navigation (optional)
            start_index: Index of current video in the list (optional)
        """
        if not video_path:
            return

        # Get video metadata from database
        metadata = None
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            project_id = getattr(self.grid, 'project_id', None)
            if project_id:
                metadata = db.get_video_by_path(video_path, project_id)
        except Exception as e:
            print(f"[VideoPlayer] Failed to load metadata: {e}")

        # Hide grid, show video player
        self.grid.hide()
        self.video_player.show()

        # Load and play video with navigation support
        # BUG FIX #5: Pass project_id explicitly to support tagging
        self.video_player.load_video(video_path, metadata, project_id=project_id)

        # BUG FIX: Set video list for next/previous navigation
        if video_list:
            self.video_player.set_video_list(video_list, start_index)
            print(f"[VideoPlayer] Opened: {video_path} ({start_index+1}/{len(video_list)})")
        else:
            print(f"[VideoPlayer] Opened: {video_path}")

    def _on_video_player_closed(self):
        """
        Handle video player close event.
        🎬 Phase 4.4: Return to grid view when player closes
        """
        if self._closing:
            return
        self.video_player.hide()
        self.grid.show()
        print("[VideoPlayer] Closed, returning to grid")

    def resizeEvent(self, event):
        """
        Clamp the main window to the primary screen available geometry so that layout
        changes (triggered by sidebar clicks or widget reflows) won't push parts of the
        window off-screen (bottom clipped).
        """
        try:
            super().resizeEvent(event)
        except Exception:
            pass

        try:
            avail = QGuiApplication.primaryScreen().availableGeometry()
            geo = self.geometry()
            tx = geo.x()
            ty = geo.y()
            tw = geo.width()
            th = geo.height()

            # Ensure window does not extend below the available bottom
            if geo.bottom() > avail.bottom():
                ty = max(avail.top(), avail.bottom() - th)
            # Ensure window does not extend above top
            if ty < avail.top():
                ty = avail.top()
            # Ensure window does not extend right of available area
            if geo.right() > avail.right():
                tx = max(avail.left(), avail.right() - tw)
            if tx < avail.left():
                tx = avail.left()

            # move if changed
            if (tx, ty) != (geo.x(), geo.y()):
                try:
                    self.move(tx, ty)
                except Exception:
                    pass
        except Exception:
            pass


    def ui_generation(self) -> int:
        """Monotonic generation used to ignore stale callbacks from workers."""
        return self._ui_generation

    def bump_ui_generation(self, reason: str = "") -> int:
        """Increment and return the current UI generation token.

        Called during shutdown/restart so that in-flight worker signals
        see a stale generation and silently drop themselves.

        Args:
            reason: Optional debug string explaining why generation was bumped
        """
        old_gen = self._ui_generation
        self._ui_generation += 1
        if reason:
            print(f"[UI] Generation bumped {old_gen} -> {self._ui_generation}: {reason}")
        # Also bump JobManager's generation for consistency.
        try:
            from services.job_manager import get_job_manager
            get_job_manager().bump_generation()
        except Exception:
            pass
        return self._ui_generation

    def _shutdown_barrier(self, *, timeout_ms: int = 10_000) -> None:
        """Best-effort barrier: cancel jobs, wait for pools, invalidate stale callbacks.

        Idempotent — safe to call from both closeEvent and request_restart.
        """
        if self._closing:
            return
        self._closing = True
        self.bump_ui_generation(reason="shutdown barrier")
        print(f"[Shutdown] _closing flag set, ui_generation={self._ui_generation}, beginning teardown...")
        self._do_shutdown_teardown(timeout_ms=timeout_ms)

    def _do_shutdown_teardown(self, *, timeout_ms: int = 10_000) -> None:
        """Perform actual teardown work. Called by _shutdown_barrier and request_restart.

        This is separated from _shutdown_barrier so that request_restart can
        set _closing=True and bump generation FIRST, then call teardown.
        """

        # Dispatch ShutdownRequested to ProjectState store
        try:
            from core.state_bus import get_store, ShutdownRequested, ActionMeta
            get_store().dispatch(ShutdownRequested(
                meta=ActionMeta(source="main_window"),
            ))
        except Exception:
            pass

        # 1. Use JobManager's coordinated shutdown (cancel + bump generation + drain)
        try:
            from services.job_manager import get_job_manager
            jm = get_job_manager()
            drained = jm.shutdown_barrier(timeout_ms=timeout_ms)
            print(f"[Shutdown] JobManager barrier complete (drained={drained}).")
        except Exception as e:
            print(f"[Shutdown] JobManager shutdown error: {e}")

        # 1b. ScanController barrier (scan thread, db_writer)
        try:
            if hasattr(self, "scan_controller") and self.scan_controller:
                self.scan_controller.shutdown_barrier(timeout_ms=timeout_ms // 2)
                print("[Shutdown] ScanController barrier complete.")
        except Exception as e:
            print(f"[Shutdown] ScanController shutdown error: {e}")

        # 2. Shut down thumbnail/grid thread pools
        try:
            if hasattr(self, "grid") and hasattr(self.grid, "shutdown_threads"):
                self.grid.shutdown_threads()
                print("[Shutdown] Grid threads shut down.")
        except Exception as e:
            print(f"[Shutdown] Grid thread error: {e}")

        try:
            if hasattr(self, "thumbnails") and hasattr(self.thumbnails, "shutdown_threads"):
                self.thumbnails.shutdown_threads()
                print("[Shutdown] ThumbnailManager threads shut down.")
        except Exception as e:
            print(f"[Shutdown] ThumbnailManager shutdown error: {e}")

        # 3. Stop any pending QTimers from firing into deleted widgets
        try:
            for timer_attr in ('_fade_out_animation', '_fade_in_animation'):
                timer = getattr(self, timer_attr, None)
                if timer and hasattr(timer, 'stop'):
                    timer.stop()
        except Exception:
            pass

        # 3a. Close any lingering QProgressDialog children to prevent
        # "External WM_DESTROY" warnings on Windows during shutdown
        try:
            from PySide6.QtWidgets import QProgressDialog
            for dlg in self.findChildren(QProgressDialog):
                dlg.close()
                dlg.deleteLater()
        except Exception:
            pass

        # 4. Clear thumbnail cache
        try:
            if hasattr(self, "thumb_cache"):
                self.thumb_cache.clear()
                print("[Shutdown] Thumbnail cache cleared.")
        except Exception as e:
            print(f"[Shutdown] Thumb cache clear error: {e}")

        # 5. Close DB connections
        try:
            db = getattr(self, "db", None)
            if db and hasattr(db, "close"):
                db.close()
                print("[Shutdown] Database connections closed.")
        except Exception as e:
            print(f"[Shutdown] DB close error: {e}")

        print("[Shutdown] Teardown complete.")

    def request_restart(self) -> None:
        """Relaunch the app in a detached process, then shutdown current instance.

        Called from PreferencesDialog when a restart-requiring setting changes.

        CRITICAL: Generation is bumped IMMEDIATELY when restart is requested,
        BEFORE starting the detached process. This ensures:
        1. All worker callbacks see stale generation and drop themselves
        2. No state mutations can occur between "restart requested" and "exit"
        3. Deterministic restart semantics (Chrome/Lightroom pattern)
        """
        if self._restart_requested:
            return
        self._restart_requested = True

        # CRITICAL FIX: Bump generation IMMEDIATELY to freeze state transitions.
        # This must happen BEFORE starting the detached process so that any
        # in-flight worker signals are blocked from mutating UI state.
        print("[Restart] Requested — setting _closing flag and bumping generation...")

        # Early generation bump BEFORE process spawn (prevents late callbacks
        # from old UI running while new process is being spawned)
        self._closing = True
        self.bump_ui_generation(reason="restart requested (pre-spawn)")
        print(f"[Restart] _closing=True, generation={self._ui_generation}, spawning new process...")

        # Start detached process AFTER generation bump and _closing flag
        try:
            import sys
            exe = sys.executable
            args = sys.argv[:]
            from PySide6.QtCore import QProcess
            ok = QProcess.startDetached(exe, args, APP_DIR)
            if not ok:
                QMessageBox.critical(self, "Restart failed",
                                     "Could not relaunch the application process.")
                self._restart_requested = False
                # Don't reset _closing — we're in an inconsistent state anyway
                return
        except Exception as e:
            QMessageBox.critical(self, "Restart failed",
                                 f"Could not relaunch the application process.\n\n{e}")
            self._restart_requested = False
            return

        # Perform teardown (barrier already set _closing=True, so this does cleanup only)
        self._do_shutdown_teardown(timeout_ms=15_000)
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.quit()

    def closeEvent(self, event):
        """Controlled shutdown barrier — ensures all background work stops
        before Qt tears down widgets.
        """
        self._shutdown_barrier(timeout_ms=10_000)
        super().closeEvent(event)

    def _update_breadcrumb(self):
        """
        Phase 2 (High Impact): Update breadcrumb navigation based on current grid state.
        Shows: Project > Folder/Date/Branch path
        """
        print(f"\n[Breadcrumb] _update_breadcrumb() CALLED")
        try:
            if not hasattr(self, "breadcrumb_nav") or not hasattr(self, "grid"):
                print(f"[Breadcrumb] Missing breadcrumb_nav or grid, aborting")
                return

            print(f"[Breadcrumb] Grid state: navigation_mode={getattr(self.grid, 'navigation_mode', 'None')}, project_id={getattr(self.grid, 'project_id', 'None')}")

            segments = []

            # CRITICAL FIX: Get CURRENT project name from grid.project_id, NOT default project!
            project_name = "My Photos"
            if hasattr(self, "_projects") and self._projects and hasattr(self.grid, 'project_id'):
                current_pid = self.grid.project_id  # ← Use ACTUAL current project, not default!
                print(f"[Breadcrumb] Looking for CURRENT project_id={current_pid} in {len(self._projects)} projects")
                for p in self._projects:
                    if p.get("id") == current_pid:
                        project_name = p.get("name", "My Photos")
                        print(f"[Breadcrumb] Found CURRENT project name: {project_name}")
                        break
                else:
                    # If current project not found in list (shouldn't happen), log warning
                    print(f"[Breadcrumb] ⚠️ WARNING: Current project_id={current_pid} not found in project list!")

            # Always start with project
            segments.append((project_name, None))  # No callback for current project
            print(f"[Breadcrumb] Added project segment: {project_name}")

            # Add navigation context
            if self.grid.navigation_mode == "folder" and hasattr(self.grid, "navigation_key"):
                # For folder mode, show folder path
                folder_id = self.grid.navigation_key
                # Get folder name from DB
                folder_name = f"Folder #{folder_id}"  # Fallback
                try:
                    with self.db._connect() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT name FROM photo_folders WHERE id = ?", (folder_id,))
                        row = cur.fetchone()
                        if row:
                            folder_name = row[0]
                except Exception as e:
                    self.logger.warning(f"Failed to get folder name for ID {folder_id}: {e}")

                # CRITICAL FIX: Use functools.partial instead of lambda to avoid closure issues
                from functools import partial
                segments.append(("Folder View", partial(self.grid.set_branch, "all")))
                segments.append((folder_name, None))
                print(f"[Breadcrumb] Added folder segments: Folder View > {folder_name}")
            elif self.grid.navigation_mode == "date" and hasattr(self.grid, "navigation_key"):
                # For date mode, show date path
                date_key = str(self.grid.navigation_key)
                from functools import partial
                segments.append(("Timeline", partial(self.grid.set_branch, "all")))
                segments.append((date_key, None))
                print(f"[Breadcrumb] Added date segments: Timeline > {date_key}")
            elif self.grid.navigation_mode == "people" and hasattr(self.grid, "navigation_key"):
                # For people/face mode, show person name
                face_key = str(self.grid.navigation_key or "")
                person_label = face_key
                # Try to resolve a display name from DB
                try:
                    with self.db._connect() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT COALESCE(label, branch_key) FROM face_branch_reps WHERE branch_key = ? AND project_id = ?",
                            (face_key, self.grid.project_id),
                        )
                        row = cur.fetchone()
                        if row and row[0]:
                            person_label = row[0]
                        elif face_key == "face_unidentified":
                            person_label = "Unidentified"
                except Exception:
                    pass
                from functools import partial
                segments.append(("People", partial(self.grid.set_branch, "all")))
                segments.append((person_label, None))
                print(f"[Breadcrumb] Added people segments: People > {person_label}")
            elif self.grid.navigation_mode == "branch":
                # For branch mode, show branch label
                branch_key = getattr(self.grid, "navigation_key", "all")
                if branch_key and branch_key != "all":
                    from functools import partial
                    segments.append(("All Photos", partial(self.grid.set_branch, "all")))
                    segments.append((str(branch_key), None))
                    print(f"[Breadcrumb] Added branch segment: All Photos > {branch_key}")
                else:
                    segments.append(("All Photos", None))
                    print(f"[Breadcrumb] Added branch segment: All Photos")
            elif hasattr(self.grid, "active_tag_filter") and self.grid.active_tag_filter:
                # Tag filter mode
                tag = self.grid.active_tag_filter
                from functools import partial
                segments.append(("Tags", partial(self._apply_tag_filter, "all")))
                if tag == "favorite":
                    segments.append(("Favorites", None))
                elif tag == "face":
                    segments.append(("Faces", None))
                else:
                    segments.append((tag, None))
                print(f"[Breadcrumb] Added tag segments: Tags > {tag}")
            else:
                segments.append(("All Photos", None))
                print(f"[Breadcrumb] Added fallback segment: All Photos")

            print(f"[Breadcrumb] Calling set_path() with {len(segments)} segments")
            self.breadcrumb_nav.set_path(segments)
            print(f"[Breadcrumb] set_path() completed successfully")
            print(f"[Breadcrumb] _update_breadcrumb() COMPLETED\n")
        except Exception as e:
            print(f"[Breadcrumb] ✗✗✗ ERROR in _update_breadcrumb(): {e}")
            import traceback
            traceback.print_exc()
            print(f"[Breadcrumb] _update_breadcrumb() FAILED\n")

    def _update_status_bar(self, selection_count=None):
        """
        Phase 2.3: Rich status bar with context-aware information.

        Shows: Total photos | Current view | Selection count | Zoom level | Filter status

        Similar to Google Photos / iPhone Photos status bars.
        """
        try:
            parts = []

            # 1. Total photo count
            if hasattr(self, "grid") and self.grid:
                total = self.grid.model.rowCount() if hasattr(self.grid, "model") else 0
                parts.append(f"📸 {total:,} photo{'' if total == 1 else 's'}")

            # 2. Current view/context
            current_view = None
            if hasattr(self, "grid") and self.grid:
                # Determine what's being shown
                if hasattr(self.grid, "navigation_mode") and self.grid.navigation_mode:
                    mode = self.grid.navigation_mode
                    if mode == "folder":
                        current_view = "Folder view"
                    elif mode == "date":
                        key = getattr(self.grid, "navigation_key", None)
                        current_view = f"📅 {key}" if key else "Date view"
                    elif mode == "branch":
                        current_view = "All Photos"
                elif hasattr(self.grid, "active_tag_filter") and self.grid.active_tag_filter:
                    tag = self.grid.active_tag_filter
                    if tag == "favorite":
                        current_view = "⭐ Favorites"
                    elif tag == "face":
                        current_view = "👥 Faces"
                    else:
                        current_view = f"🏷️ {tag}"
                else:
                    current_view = "All Photos"

            if current_view:
                parts.append(current_view)

            # 3. Selection count (only if > 0)
            if selection_count is not None and selection_count > 0:
                parts.append(f"Selected: {selection_count}")

            # 4. Zoom level (if grid has zoom info)
            if hasattr(self, "grid") and hasattr(self.grid, "thumb_height"):
                height = self.grid.thumb_height
                if height <= 100:
                    zoom = "Small"
                elif height <= 160:
                    zoom = "Medium"
                elif height <= 220:
                    zoom = "Large"
                else:
                    zoom = "XL"
                parts.append(f"Zoom: {zoom}")

            # Combine all parts with separator
            message = " • ".join(parts) if parts else "Ready"
            self.statusBar().showMessage(message)

        except Exception as e:
            print(f"[MainWindow] _update_status_bar error: {e}")
            # Fallback to simple message
            self.statusBar().showMessage(tr('status_messages.ready'))


    def _init_current_layout_store_sub(self):
        """Subscribe to store so CurrentLayout grid/sidebar refresh on version changes.

        This replaces the direct widget calls in _finalize_scan_refresh().
        Google Layout has its own subscription; this handles CurrentLayout only.
        Tracked versions: media_v (scan done), stacks_v (stack badges changed).
        """
        self._cl_store_unsub = None
        try:
            from core.state_bus import get_store
            store = get_store()
            s = store.state
            self._cl_store_versions = {
                "media_v": s.media_v,
                "stacks_v": s.stacks_v,
            }

            def _on_current_layout_state(state, action):
                if getattr(self, '_closing', False):
                    return
                need_refresh = False
                for v_key in ("media_v", "stacks_v"):
                    old_v = self._cl_store_versions.get(v_key)
                    new_v = getattr(state, v_key)
                    if old_v is not None and old_v != new_v:
                        need_refresh = True
                    self._cl_store_versions[v_key] = new_v
                if need_refresh:
                    self._refresh_current_layout_from_store()

            self._cl_store_callback = _on_current_layout_state
            self._cl_store_unsub = store.subscribe(_on_current_layout_state)
        except Exception:
            pass

    def _refresh_current_layout_from_store(self):
        """Refresh CurrentLayout grid and sidebar when media_v changes.

        Only acts when the active layout is NOT google (Google Layout
        handles its own refresh via its own store subscription).
        """
        if getattr(self, '_closing', False):
            return
        try:
            lm = getattr(self, 'layout_manager', None)
            if lm and getattr(lm, '_current_layout_id', None) in ("google", "google_legacy"):
                return  # Google layout handles its own refresh

            # Sidebar
            sidebar = getattr(self, 'sidebar', None)
            if sidebar and not getattr(sidebar, '_disposed', False):
                if hasattr(sidebar, 'reload') and sidebar.isVisible():
                    sidebar.reload()

            # Grid — use debounced _schedule_reload() to coalesce rapid
            # store changes (media_v + stacks_v can bump within ms of each
            # other, causing redundant full reloads + thumbnail re-queues)
            grid = getattr(self, 'grid', None)
            if grid and getattr(grid, 'project_id', None) is not None:
                if hasattr(grid, '_schedule_reload'):
                    grid._schedule_reload()
                elif hasattr(grid, 'reload'):
                    grid.reload()

            # Thumbnails
            thumbnails = getattr(self, 'thumbnails', None)
            if thumbnails and grid and hasattr(grid, 'get_visible_paths'):
                thumbnails.load_thumbnails(grid.get_visible_paths())
        except Exception as e:
            print(f"[MainWindow] CurrentLayout store refresh error: {e}")

    def _init_progress_pollers(self):
        # Replaced 2s polling timers with event-driven QFileSystemWatcher.
        # cluster_timer removed — FaceClusterWorker already reports progress
        # via Qt signals (progress/finished). JSON polling was legacy.
        # backfill_timer replaced — subprocess writes JSON, watcher triggers
        # read only when the file actually changes.
        from PySide6.QtCore import QFileSystemWatcher
        self._status_watcher = QFileSystemWatcher(self)
        status_dir = os.path.join(self.app_root, "status")
        # Watch the directory so we catch file creation/deletion
        if os.path.isdir(status_dir):
            self._status_watcher.addPath(status_dir)
        # Also watch individual files if they already exist
        for fname in ("backfill_status.json", "cluster_status.json"):
            fpath = os.path.join(status_dir, fname)
            if os.path.exists(fpath):
                self._status_watcher.addPath(fpath)
        self._status_watcher.fileChanged.connect(self._on_status_file_changed)
        self._status_watcher.directoryChanged.connect(self._on_status_dir_changed)

    # ------------------------------------------------------------------
    # Scan progress widgets (non-modal, in status bar)
    # ------------------------------------------------------------------
    def _ensure_scan_status_widgets(self):
        """Lazily create the permanent status-bar widgets for scan progress."""
        if getattr(self, "_scan_progress_bar", None) is not None:
            return

        from PySide6.QtWidgets import QProgressBar, QLabel

        self._scan_status_label = QLabel("")
        self._scan_progress_bar = QProgressBar()
        self._scan_progress_bar.setRange(0, 100)
        self._scan_progress_bar.setValue(0)
        self._scan_progress_bar.setTextVisible(False)
        self._scan_progress_bar.setFixedWidth(160)
        self._scan_progress_bar.setFixedHeight(14)
        self._scan_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                background: #f0f0f0;
            }
            QProgressBar::chunk {
                background: #1a73e8;
                border-radius: 3px;
            }
        """)
        self._scan_progress_bar.hide()

        sb = self.statusBar()
        sb.addPermanentWidget(self._scan_status_label)
        sb.addPermanentWidget(self._scan_progress_bar)

    def scan_ui_begin(self, text: str = "Scanning..."):
        """Show the non-modal scan progress indicator in the status bar."""
        self._ensure_scan_status_widgets()
        self._scan_status_label.setText(text)
        self._scan_progress_bar.setValue(0)
        self._scan_progress_bar.show()

    def scan_ui_update(self, percent: int, text: str | None = None):
        """Update scan progress (called from main thread via QueuedConnection)."""
        self._ensure_scan_status_widgets()
        if text:
            self._scan_status_label.setText(text)
        self._scan_progress_bar.setValue(max(0, min(100, int(percent))))

    def scan_ui_finish(self, text: str = "Scan complete", timeout_ms: int = 4000):
        """Mark scan complete, auto-hide after *timeout_ms*."""
        self._ensure_scan_status_widgets()
        self._scan_status_label.setText(text)
        self._scan_progress_bar.setValue(100)

        def _hide():
            if getattr(self, "_scan_progress_bar", None):
                self._scan_progress_bar.hide()
                self._scan_status_label.setText("")

        QTimer.singleShot(timeout_ms, _hide)

    # ------------------------------------------------------------------
    # Activity Center toggle
    # ------------------------------------------------------------------
    def _toggle_activity_center(self, checked: bool = None):
        """Toggle the Activity Center dock widget visibility."""
        ac = getattr(self, "activity_center", None)
        if not ac:
            return
        if checked is None:
            checked = not ac.isVisible()
        ac.setVisible(checked)
        # Sync the View menu checkbox
        act = getattr(self, "_act_toggle_activity", None)
        if act and act.isChecked() != checked:
            act.setChecked(checked)

    def _on_activity_center_visibility_changed(self, visible: bool):
        """Keep the View > Activity Center menu checkbox in sync."""
        act = getattr(self, "_act_toggle_activity", None)
        if act and act.isChecked() != visible:
            act.setChecked(visible)

    # ------------------------------------------------------------------
    # Metadata Editor Dock
    # ------------------------------------------------------------------
    def _toggle_metadata_editor(self, checked: bool = None):
        """Toggle the Metadata Editor dock widget visibility."""
        dock = getattr(self, "metadata_editor_dock", None)
        if not dock:
            return
        if checked is None:
            checked = not dock.isVisible()
        dock.setVisible(checked)
        # Sync the View menu checkbox
        act = getattr(self, "_act_toggle_metadata_editor", None)
        if act and act.isChecked() != checked:
            act.setChecked(checked)

    def _on_metadata_changed(self, photo_id: int, field: str, value):
        """Handle metadata field changes from the dock editor."""
        if self._closing:
            return
        print(f"[MainWindow] Metadata changed: photo={photo_id}, {field}={value}")
        # Refresh grid if rating/flag changed (may affect visual indicators)
        if field in ("rating", "flag"):
            grid = getattr(self, "grid", None)
            if grid and hasattr(grid, "refresh_thumbnail"):
                grid.refresh_thumbnail(photo_id)

    def show_metadata_for_photo(self, photo_id: int, photo_path: str, metadata: dict = None):
        """
        Show the metadata editor dock for a specific photo.

        Called from lightbox or grid when user wants to edit metadata.

        Args:
            photo_id: Database photo ID
            photo_path: File path
            metadata: Optional pre-loaded metadata dict
        """
        dock = getattr(self, "metadata_editor_dock", None)
        if dock:
            dock.load_photo(photo_id, photo_path, metadata)
            if not dock.isVisible():
                dock.show()

    def _init_embedding_status_indicator(self):
        """
        Initialize the embedding coverage status bar indicator.

        Shows a compact indicator like: "🧠 85% embeddings"
        Clicking it opens the full embedding dashboard.
        """
        from PySide6.QtWidgets import QLabel

        # Create clickable label for status bar
        self.embedding_status_label = QLabel("🧠 —%")
        self.embedding_status_label.setStyleSheet("""
            QLabel {
                color: #888;
                padding: 0 8px;
                font-size: 10pt;
            }
            QLabel:hover {
                color: #4A90E2;
                text-decoration: underline;
            }
        """)
        self.embedding_status_label.setCursor(Qt.PointingHandCursor)
        self.embedding_status_label.setToolTip("Embedding coverage. Click to view details.")

        # Make label clickable
        self.embedding_status_label.mousePressEvent = lambda e: self._on_open_embedding_dashboard()

        # Add to status bar as permanent widget (right side)
        self.statusBar().addPermanentWidget(self.embedding_status_label)

        # Subscribe to store embeddings_v changes instead of 30s polling timer.
        # The status updates reactively when embeddings are generated.
        self._emb_status_unsub = None
        try:
            from core.state_bus import get_store
            store = get_store()
            self._emb_status_versions = {"embeddings_v": store.state.embeddings_v}

            def _on_embeddings_changed(state, action):
                if getattr(self, '_closing', False):
                    return
                old_v = self._emb_status_versions.get("embeddings_v")
                new_v = state.embeddings_v
                if old_v is not None and old_v != new_v:
                    self._update_embedding_status_bar()
                self._emb_status_versions["embeddings_v"] = new_v

            self._emb_status_callback = _on_embeddings_changed  # prevent GC (weakref store)
            self._emb_status_unsub = store.subscribe(_on_embeddings_changed)
        except Exception:
            pass  # Store not initialized

        # Initial update after short delay
        QTimer.singleShot(2000, self._update_embedding_status_bar)

    def _update_embedding_status_bar(self):
        """Update the embedding coverage indicator in the status bar."""
        try:
            # Get current project_id
            project_id = None
            if hasattr(self, "grid") and hasattr(self.grid, "project_id"):
                project_id = self.grid.project_id
            elif hasattr(self, "sidebar") and hasattr(self.sidebar, "project_id"):
                project_id = self.sidebar.project_id

            if project_id is None:
                self.embedding_status_label.setText("🧠 —%")
                self.embedding_status_label.setToolTip("No project selected")
                return

            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db.get_connection() as conn:
                # Get total photos
                cursor = conn.execute(
                    "SELECT COUNT(*) as count FROM photo_metadata WHERE project_id = ?",
                    (project_id,)
                )
                total = cursor.fetchone()["count"]

                # Get photos with embeddings
                cursor = conn.execute("""
                    SELECT COUNT(DISTINCT pm.id) as count
                    FROM photo_metadata pm
                    JOIN semantic_embeddings se ON pm.id = se.photo_id
                    WHERE pm.project_id = ?
                """, (project_id,))
                with_emb = cursor.fetchone()["count"]

            coverage = (with_emb / total * 100) if total > 0 else 0

            # Model Upgrade Check
            model_warning = ""
            try:
                from repository.project_repository import ProjectRepository
                from repository.base_repository import DatabaseConnection
                proj_repo = ProjectRepository(DatabaseConnection())
                current_model = proj_repo.get_semantic_model(project_id)
                best_model = proj_repo._get_best_available_model()

                if current_model != best_model:
                    from utils.clip_model_registry import model_display_label
                    model_warning = f"\n\n⚠️ Better model available: {model_display_label(best_model)}"
                    model_warning += "\nConsider re-extracting embeddings for better search quality."
            except Exception:
                pass

            # Update label
            if coverage >= 100:
                self.embedding_status_label.setText("🧠 100%")
                self.embedding_status_label.setStyleSheet("""
                    QLabel {
                        color: #4CAF50;
                        padding: 0 8px;
                        font-size: 10pt;
                    }
                    QLabel:hover {
                        color: #66BB6A;
                        text-decoration: underline;
                    }
                """)
            elif coverage >= 80:
                self.embedding_status_label.setText(f"🧠 {coverage:.0f}%")
                self.embedding_status_label.setStyleSheet("""
                    QLabel {
                        color: #8BC34A;
                        padding: 0 8px;
                        font-size: 10pt;
                    }
                    QLabel:hover {
                        color: #9CCC65;
                        text-decoration: underline;
                    }
                """)
            elif coverage > 0:
                self.embedding_status_label.setText(f"🧠 {coverage:.0f}%")
                self.embedding_status_label.setStyleSheet("""
                    QLabel {
                        color: #FFC107;
                        padding: 0 8px;
                        font-size: 10pt;
                    }
                    QLabel:hover {
                        color: #FFCA28;
                        text-decoration: underline;
                    }
                """)
            else:
                self.embedding_status_label.setText("🧠 0%")
                self.embedding_status_label.setStyleSheet("""
                    QLabel {
                        color: #888;
                        padding: 0 8px;
                        font-size: 10pt;
                    }
                    QLabel:hover {
                        color: #4A90E2;
                        text-decoration: underline;
                    }
                """)

            # Update tooltip with details
            tooltip = f"Embedding Coverage: {with_emb}/{total} photos ({coverage:.1f}%) {model_warning}\n"
            tooltip += "Click to view detailed statistics."
            self.embedding_status_label.setToolTip(tooltip)

        except Exception as e:
            # Silently fail - don't spam console with errors
            self.embedding_status_label.setText("🧠 —%")
            self.embedding_status_label.setToolTip(f"Could not load stats: {str(e)[:30]}")

    def _on_status_file_changed(self, path: str):
        """Event-driven handler: a status JSON file was modified."""
        fname = os.path.basename(path)
        if fname == "cluster_status.json":
            self._poll_cluster_status()
        elif fname == "backfill_status.json":
            self._poll_backfill_status()
        # QFileSystemWatcher may drop the watch after file replacement;
        # re-add if the file still exists.
        if os.path.exists(path) and path not in self._status_watcher.files():
            self._status_watcher.addPath(path)

    def _on_status_dir_changed(self, dir_path: str):
        """Event-driven handler: a file was created/deleted in status dir."""
        for fname in ("backfill_status.json", "cluster_status.json"):
            fpath = os.path.join(dir_path, fname)
            if os.path.exists(fpath) and fpath not in self._status_watcher.files():
                self._status_watcher.addPath(fpath)
                # Read immediately since the file was just created
                self._on_status_file_changed(fpath)

    def _poll_cluster_status(self):
        path = os.path.join(self.app_root, "status", "cluster_status.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            phase = data.get("phase")
            pct = data.get("percent", 0)

#            self.status_bar.showMessage(f"👥 Clustering {pct:.1f}% ({phase})")
            self.statusBar().showMessage(f"👥 Clustering {pct:.1f}% ({phase})")

            if phase == "done":
                self.statusBar().showMessage(tr('status_messages.clustering_complete'))
                os.remove(path)
        except Exception as e:
            print(f"[Status] cluster poll failed: {e}")

    def _poll_backfill_status(self):
        path = os.path.join(self.app_root, "status", "backfill_status.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pct = data.get("percent", 0)

#            self.status_bar.showMessage(f"📸 Backfill {pct:.1f}%")
            phase = data.get("phase", "")
            self.statusBar().showMessage(f"📸 Backfill {pct:.1f}% ({phase})")
#            self.statusBar().showMessage(f"👥 Clustering {pct:.1f}% ({phase})")

            if data.get("phase") == "done":
#                self.status_bar.showMessage("✅ Metadata backfill complete")
                self.statusBar().showMessage(tr('status_messages.backfill_complete'))
                os.remove(path)
        except Exception as e:
            print(f"[Status] backfill poll failed: {e}")

    def _maybe_prompt_clip_upgrade(self):
        """Phase: Prompt user if a better CLIP model is available than currently used."""
        project_id = self.active_project_id
        if project_id is None:
            return

        try:
            from services.semantic_embedding_service import get_semantic_embedding_service
            service = get_semantic_embedding_service()
            stats = service.get_project_embedding_stats(project_id)

            if stats.get('can_upgrade_model'):
                rec_model = stats.get('recommended_model', 'Large model')
                # Prettify internal names
                if 'large-patch14' in rec_model: rec_model = "CLIP ViT-L/14"

                QMessageBox.information(
                    self,
                    "Better Search Model Available",
                    f"A superior AI model ({rec_model}) is installed, "
                    "but this project still uses legacy embeddings.\n\n"
                    "To significantly improve scenic and semantic search quality, run:\n"
                    "Tools → Extract Embeddings"
                )
        except Exception as e:
            logger.warning("[MainWindow] CLIP upgrade prompt failed: %s", e)
