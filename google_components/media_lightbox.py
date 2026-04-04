# google_components/media_lightbox.py
# Version 10.01.01.05 dated 20260207

"""
Google Photos Layout - Media Lightbox Component
Extracted from google_layout.py for better organization.

Contains:
- PreloadImageSignals, PreloadImageWorker: Async image preloading
- ProgressiveImageSignals, ProgressiveImageWorker: Progressive image loading
- MediaLightbox: Full-screen media viewer with edit capabilities
- TrimMarkerSlider: Custom slider for video trimming

Phase 3C extraction - MediaLightbox and related components
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDialog, QSlider, QFrame, QGraphicsOpacityEffect, QSizePolicy,
    QScrollArea, QGridLayout, QStackedWidget, QMessageBox, QSpinBox,
    QTextEdit, QRadioButton, QButtonGroup, QLineEdit, QGroupBox, QToolButton
)
from PySide6.QtCore import (
    Qt, Signal, QSize, QEvent, QRunnable, QThreadPool, QObject, QTimer,
    QPropertyAnimation, QEasingCurve, QRect, QPoint
)
from PySide6.QtGui import (
    QPixmap, QIcon, QKeyEvent, QImage, QColor, QPainter, QPen, QPainterPath,
    QTransform, QFont, QBrush, QCursor
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from layouts.video_editor_mixin import VideoEditorMixin
from translation_manager import tr as t
from typing import List, Optional
import os


class PreloadImageSignals(QObject):
    """Signals for async image preloading."""
    loaded = Signal(str, object)  # (path, QImage or None)


class PreloadImageWorker(QRunnable):
    """
    PHASE A #1: Background worker for preloading images.

    Preloads next 2 photos in background for instant navigation.
    Uses SafeImageLoader for memory-safe, capped-size decoding.
    """
    # Max dimension for preloaded images (screen-fit quality, not full resolution)
    PRELOAD_MAX_DIM = 2560

    def __init__(self, path: str, signals: PreloadImageSignals):
        super().__init__()
        self.path = path
        self.signals = signals

    def run(self):
        """Load image in background thread — emits QImage (thread-safe)."""
        try:
            from services.safe_image_loader import safe_decode_qimage

            # Decode at capped size — never full resolution
            qimage = safe_decode_qimage(
                self.path,
                max_dim=self.PRELOAD_MAX_DIM,
                enable_retry_ladder=True,
            )

            # Emit loaded signal with QImage (always non-null from safe_decode)
            self.signals.loaded.emit(self.path, qimage)
            print(f"[PreloadImageWorker] ✓ Preloaded: {os.path.basename(self.path)} "
                  f"({qimage.width()}x{qimage.height()})")

        except Exception as e:
            print(f"[PreloadImageWorker] ⚠️ Error preloading {self.path}: {e}")
            self.signals.loaded.emit(self.path, None)

class ProgressiveImageSignals(QObject):
    """Signals for progressive image loading (generation token + QImage)."""
    thumbnail_loaded = Signal(int, object)  # (generation, QImage)
    full_loaded = Signal(int, object)  # (generation, QImage)


class ProgressiveImageWorker(QRunnable):
    """
    PHASE A #2: Progressive image loader.

    Loads thumbnail-quality first (instant), then viewport-fit quality in background.
    Uses SafeImageLoader for memory-safe, capped-size decoding.
    Emits QImage (thread-safe) with generation token for staleness detection.

    IMPORTANT: "Full quality" means "full quality for the current viewport",
    NOT "decode the original 8000x6000 into RAM". This is how Google Photos
    and Lightroom handle large photos — they never decode full resolution for display.
    """
    # Max edge for "full quality" viewport display (not raw resolution)
    FULL_QUALITY_MAX_DIM = 2560

    def __init__(self, path: str, signals: ProgressiveImageSignals, viewport_size, generation: int):
        super().__init__()
        self.path = path
        self.signals = signals
        self.viewport_size = viewport_size
        self.generation = generation

    def run(self):
        """Load image progressively: thumbnail → viewport-fit quality (emits QImage)."""
        try:
            from services.safe_image_loader import safe_decode_qimage, create_placeholder

            # STEP 1: Quick thumbnail decode (small size = fast)
            thumb_max_dim = max(
                self.viewport_size.width() // 4,
                self.viewport_size.height() // 4,
                400  # minimum useful thumbnail
            )
            thumb_qimage = safe_decode_qimage(
                self.path,
                max_dim=thumb_max_dim,
                enable_retry_ladder=True,
            )

            # Emit thumbnail with generation token
            self.signals.thumbnail_loaded.emit(self.generation, thumb_qimage)
            print(f"[ProgressiveImageWorker] ✓ Thumbnail loaded: {os.path.basename(self.path)} "
                  f"({thumb_qimage.width()}x{thumb_qimage.height()})")

            # STEP 2: Viewport-fit "full quality" decode (capped, NOT raw resolution)
            viewport_max = max(
                self.viewport_size.width(),
                self.viewport_size.height(),
            )
            # Cap at FULL_QUALITY_MAX_DIM — this is the key RAM saver
            full_max_dim = min(viewport_max, self.FULL_QUALITY_MAX_DIM)

            full_qimage = safe_decode_qimage(
                self.path,
                max_dim=full_max_dim,
                enable_retry_ladder=True,
            )

            # Emit full quality with generation token
            self.signals.full_loaded.emit(self.generation, full_qimage)
            print(f"[ProgressiveImageWorker] ✓ Full quality loaded: {os.path.basename(self.path)} "
                  f"({full_qimage.width()}x{full_qimage.height()})")

        except Exception as e:
            print(f"[ProgressiveImageWorker] ⚠️ Error loading {self.path}: {e}")
            import traceback
            traceback.print_exc()

            # FALLBACK: Create placeholder via SafeImageLoader (always non-null)
            from services.safe_image_loader import create_placeholder
            placeholder = create_placeholder(400, f"Image Error\n{os.path.basename(self.path)[:40]}")

            # Emit placeholder for both stages with generation token
            self.signals.thumbnail_loaded.emit(self.generation, placeholder)
            self.signals.full_loaded.emit(self.generation, placeholder)
            print(f"[ProgressiveImageWorker] ✓ Emitted error placeholder for: {os.path.basename(self.path)}")

class MediaLightbox(QDialog, VideoEditorMixin):
    """
    Full-screen media lightbox/preview dialog supporting photos AND videos.

    ✨ ENHANCED FEATURES:
    - Mixed photo/video navigation
    - Video playback with controls
    - Zoom controls for photos (Ctrl+Wheel, +/- keys)
    - Slideshow mode (Space to toggle)
    - Keyboard shortcuts (Arrow keys, Space, Delete, F, R, etc.)
    - Quick actions (Delete, Favorite, Rate)
    - Metadata panel (EXIF, date, dimensions, video info)
    - Fullscreen toggle (F11)
    - Close button and ESC key
    - VIDEO EDITING: Trim, rotate, speed, adjustments, export
    """

    def __init__(self, media_path: str, all_media: List[str], parent=None,
                 *, project_id: int = None):
        """
        Initialize media lightbox.

        Args:
            media_path: Path to photo/video to display
            all_media: List of all media paths (photos + videos) in timeline order
            parent: Parent widget
            project_id: Explicit project ID (preferred over parent introspection)
        """
        super().__init__(parent)

        self.media_path = media_path
        self.all_media = all_media
        self.current_index = all_media.index(media_path) if media_path in all_media else 0
        self._media_loaded = False  # Track if media has been loaded
        self._explicit_project_id = project_id  # Preferred source of project_id

        # Zoom state (for photos) - SMOOTH CONTINUOUS ZOOM
        # Like Current Layout's LightboxDialog - smooth zoom with mouse wheel
        self.zoom_level = 1.0  # Current zoom scale
        self.fit_zoom_level = 1.0  # Zoom level for "fit to window" mode
        self.zoom_mode = "fit"  # "fit", "fill", "actual", or "custom"
        self.original_pixmap = None  # Store original for zoom
        self.zoom_factor = 1.15  # Zoom increment per wheel step (smooth like Current Layout)

        # Slideshow state
        self.slideshow_active = False
        self.slideshow_timer = None
        self.slideshow_interval = 3000  # 3 seconds default
        self.slideshow_loop = True  # Loop back to start (like iPhone/Google Photos)
        self.slideshow_ken_burns = True  # Ken Burns effect (slow pan+zoom)
        self._ken_burns_direction = 0  # Cycles through pan directions
        self._kb_h_anim = None  # Horizontal pan animation
        self._kb_v_anim = None  # Vertical pan animation
        self.slideshow_music_player = None  # Separate QMediaPlayer for music
        self.slideshow_music_output = None  # Separate QAudioOutput for music
        self.slideshow_music_path = None  # Path to music file
        self.slideshow_music_volume = 0.5  # Music volume (0.0 - 1.0)

        # Slideshow editor (photo selection for curated slideshow)
        self.slideshow_selected_indices = set()  # Indices selected for slideshow
        self._slideshow_playlist = None  # Ordered list of (all_media_index, path) when curated
        self._slideshow_playlist_pos = 0  # Position within curated playlist
        self.slideshow_editor_visible = False

        # Rating state
        self.current_rating = 0  # 0-5 stars

        # PHASE 2 #10: Swipe gesture state
        self.swipe_start_pos = None
        self.swipe_start_time = None
        self.is_swiping = False

        # PHASE A #1: Image Preloading & Caching
        self.preload_cache = {}  # Map path -> {pixmap, timestamp, byte_est}
        self.preload_count = 2  # Preload next 2 photos
        self.cache_limit = 5  # Keep max 5 photos in cache
        self.cache_byte_limit = 150 * 1024 * 1024  # 150 MB hard cap
        self._cache_bytes_used = 0  # Running total of estimated bytes
        self.preload_thread_pool = QThreadPool()
        self.preload_thread_pool.setMaxThreadCount(2)  # 2 background threads for preloading
        self.preload_signals = PreloadImageSignals()
        self.preload_signals.loaded.connect(self._on_preload_complete)

        # PHASE A #2: Progressive Loading State
        self.progressive_loading = True  # Enable progressive load (thumbnail → full)
        self.thumbnail_quality_loaded = False  # Track if thumbnail loaded
        self.full_quality_loaded = False  # Track if full quality loaded
        self.progressive_load_worker = None  # Current progressive load worker
        self._lb_media_generation = 0  # Generation token: bumped each navigation, stale workers discarded
        self.progressive_signals = ProgressiveImageSignals()
        self.progressive_signals.thumbnail_loaded.connect(self._on_thumbnail_loaded)
        self.progressive_signals.full_loaded.connect(self._on_full_quality_loaded)

        # PHASE A #3: Zoom to Mouse Cursor
        self.last_mouse_pos = None  # Track mouse position for zoom centering
        self.zoom_mouse_tracking = True  # Enable cursor-centered zoom

        # PHASE A #4: Loading Indicators
        self.is_loading = False  # Track if currently loading
        self.loading_start_time = None  # Track load start for timeout detection

        # PHASE A #5: Keyboard Shortcut Help Overlay
        self.help_overlay = None  # Help overlay widget
        self.help_visible = False  # Track help visibility

        # PHASE B #1: Thumbnail Filmstrip
        self.filmstrip_enabled = True  # Enable thumbnail filmstrip at bottom
        self.filmstrip_thumbnail_size = 80  # 80x80px thumbnails
        self.filmstrip_visible_count = 9  # Show 9 thumbnails at once
        self.filmstrip_thumbnails = {}  # Map index -> QPixmap thumbnail
        self.filmstrip_buttons = {}  # Map index -> QPushButton

        # PHASE B #2: Enhanced Touch Gestures
        self.double_tap_enabled = True  # Enable double-tap to zoom
        self.last_tap_time = None  # Track for double-tap detection
        self.last_tap_pos = None  # Track tap position
        self.two_finger_pan_enabled = True  # Enable two-finger pan when zoomed
        self.inertial_scroll_enabled = True  # Enable inertial scrolling

        # PHASE B #3: Video Scrubbing Preview
        self.video_scrubbing_enabled = True  # Enable hover frame preview
        self.scrubbing_preview_widget = None  # Preview widget for frame

        # PHASE B #4: Contextual Toolbars
        self.contextual_toolbars = True  # Enable contextual toolbar display
        self.video_only_buttons = []  # Buttons only shown for videos
        self.photo_only_buttons = []  # Buttons only shown for photos

        # PHASE B #5: Zoom State Persistence
        self.zoom_persistence_enabled = True  # Remember zoom across photos
        self.saved_zoom_level = 1.0  # Saved zoom level
        self.saved_zoom_mode = "fit"  # Saved zoom mode
        self.apply_zoom_to_all = False  # Apply saved zoom to all photos

        # PHASE C #1: RAW/HDR Support
        self.raw_support_enabled = True  # Enable RAW file rendering
        self.exposure_adjustment = 0.0  # Exposure adjustment (-2.0 to +2.0)

        # PHASE C #2: Share/Export
        self.share_dialog_enabled = True  # Enable share/export dialog

        # PHASE C #3: Quick Edit Tools
        self.rotation_angle = 0  # Current rotation (0, 90, 180, 270)
        self.crop_mode_active = False  # Crop mode state
        self.crop_rect = None  # Crop rectangle (x, y, w, h)
        self.current_preset = None  # 'dynamic' | 'warm' | 'cool' | None
        self.preset_cache = {}  # (path, preset) -> QPixmap

        # Filter strength control
        self.filter_intensity = 100  # Default 100% (full strength)
        self.current_preset_adjustments = {}  # Store current preset for intensity changes
        
        # Edit state persistence
        self.edit_states_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.edit_states')
        os.makedirs(self.edit_states_dir, exist_ok=True)
        
        # Copy/Paste adjustments clipboard
        self.copied_adjustments = None  # Stores copied adjustment state
        self.copied_filter_intensity = None
        self.copied_preset = None

        # Editor undo/redo stack
        self.edit_history = []  # List of (pixmap, adjustments_dict) tuples
        self.edit_history_index = -1  # Current position in history
        self.max_history = 20  # Max undo steps

        # Editor adjustments state
        self.adjustments = {
            'brightness': 0,
            'exposure': 0,
            'contrast': 0,
            'highlights': 0,
            'shadows': 0,
            'vignette': 0,
            'sharpen': 0,  # New: Sharpen/Clarity adjustment
            'saturation': 0,
            'warmth': 0,
            # RAW-specific adjustments
            'white_balance_temp': 0,  # White balance temperature (-100 to +100)
            'white_balance_tint': 0,  # White balance tint (green/magenta, -100 to +100)
            'exposure_recovery': 0,   # Highlight recovery (0 to 100)
            'lens_correction': 0,     # Lens distortion correction (0 to 100)
            'chromatic_aberration': 0  # Chromatic aberration removal (0 to 100)
        }
        self.is_raw_file = False  # Track if current file is RAW
        self.raw_image = None  # Store rawpy image object
        self.edit_zoom_level = 1.0  # Zoom level in editor mode
        self.before_after_active = False  # Before/After comparison toggle
        self._original_pixmap = None  # Original pixmap for editing
        self._edit_pixmap = None  # Current edited pixmap
        self._preview_pixmap = None  # Normal preview (2048px) for high quality
        self._preview_ultralow = None  # Ultra-low preview (512px) for SMOOTH real-time drag
        self._using_preview = False  # Whether using preview resolution
        self._preview_normal_pil = None  # Cached PIL version of normal preview
        self._preview_ultralow_pil = None  # Cached PIL version of ultra-low preview
        self._is_dragging_slider = False  # Track if user is actively dragging slider
        self._crop_rect_norm = None  # Normalized crop rectangle (0-1 coords)

        # VIDEO EDITING STATE (Phase 1)
        self.is_video_file = False  # Track if current file is video
        self.video_player = None  # QMediaPlayer instance
        self.video_widget = None  # QVideoWidget for display
        self.audio_output = None  # QAudioOutput for audio
        self.video_duration = 0  # Video duration in milliseconds
        self.video_position = 0  # Current playback position
        self.video_trim_start = 0  # Trim start point (ms)
        self.video_trim_end = 0  # Trim end point (ms)
        self.video_is_playing = False  # Playback state
        self.video_is_muted = False  # Mute state
        self.video_playback_speed = 1.0  # Speed multiplier (0.5x, 1x, 2x)
        self.video_rotation_angle = 0  # Video rotation (0, 90, 180, 270)
        self._video_original_path = None  # Original video path for export
        self._video_gen = 0  # Generation counter for discarding stale video callbacks
        self._video_fit_timer = QTimer()  # Debounce timer for _fit_video_view
        self._video_fit_timer.setSingleShot(True)
        self._video_fit_timer.setInterval(16)  # ~1 frame
        self._video_fit_timer.timeout.connect(self._do_fit_video_view)
        self._video_fit_ready = False  # True after first valid fit — gates zoom
        self._video_min_viewport_px = 160  # Minimum viewport size for valid fit

        # PHASE C #4: Compare Mode
        self.compare_mode_active = False  # Compare mode state
        self.compare_media_path = None  # Second media for comparison

        # PHASE C #5: Motion Photos
        self.motion_photo_enabled = True  # Enable motion photo detection
        self.is_motion_photo = False  # Current media is motion photo
        self.motion_video_path = None  # Path to paired video

        # Metadata editing state (for Edit tab in info panel)
        self._lb_loading = False  # Prevents saving during metadata population
        self._lb_current_photo_id = None  # Current photo's database ID

        # ============================================================
        # RESPONSIVE DESIGN: Debounce timers for performance
        # ============================================================
        self._resize_debounce_timer = QTimer()
        self._resize_debounce_timer.setSingleShot(True)
        self._resize_debounce_timer.setInterval(150)  # 150ms debounce
        self._resize_debounce_timer.timeout.connect(self._enhanced_responsive_behavior)

        self._last_resize_log_time = 0  # Throttle resize logging
        self._position_retry_count = 0  # Track retry attempts

        self._setup_ui()
        # Don't load media here - wait for showEvent when window has proper size

        # PHASE 2 #10: Enable touch/gesture events
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.SwipeGesture)
        self.grabGesture(Qt.PinchGesture)

    def __del__(self):
        """Cleanup when layout is destroyed to prevent memory leaks."""
        try:
            # Remove event filters to prevent RuntimeError on deleted widgets
            if hasattr(self, 'event_filter') and self.event_filter:
                if hasattr(self, 'search_box') and self.search_box:
                    try:
                        self.search_box.removeEventFilter(self.event_filter)
                    except RuntimeError:
                        pass  # Widget already deleted
                if hasattr(self, 'timeline_scroll') and self.timeline_scroll:
                    try:
                        self.timeline_scroll.viewport().removeEventFilter(self.event_filter)
                    except RuntimeError:
                        pass  # Widget already deleted
                        
            # Disconnect thumbnail loading signals
            if hasattr(self, 'thumbnail_signals'):
                try:
                    self.thumbnail_signals.loaded.disconnect()
                except (RuntimeError, TypeError):
                    pass
                    
            # Clear preload cache to free memory
            if hasattr(self, 'preload_cache'):
                self.preload_cache.clear()
                
            print("[GooglePhotosLayout] Cleanup completed")
        except Exception as e:
            print(f"[GooglePhotosLayout] Cleanup error: {e}")

    def _setup_ui(self):
        """Setup Google Photos-style lightbox UI with overlay controls."""
        from PySide6.QtWidgets import QApplication, QScrollArea, QWidget, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QHBoxLayout, QStackedWidget, QFrame
        from PySide6.QtCore import QPropertyAnimation, QTimer, QRect

        # Window settings - ADAPTIVE SIZING: Based on screen resolution and DPI
        self.setWindowTitle(t('google_layout.lightbox.window_title'))

        # ============================================================
        # FIX A: Use the correct screen (parent's screen or cursor's screen)
        # instead of always using primaryScreen()
        # This fixes multi-monitor setups where the app is on a secondary monitor
        # ============================================================
        screen = None
        if self.parent():
            screen = self.parent().screen()

        if screen is None:
            screen = QApplication.screenAt(QCursor.pos())

        if screen is None:
            screen = QApplication.primaryScreen()

        # Use available geometry (excludes taskbar) for all sizing
        available_geometry = screen.availableGeometry()
        dpi_scale = screen.devicePixelRatio()  # Windows scale (1.0, 1.25, 1.5, 2.0)

        # Calculate from available geometry (not full screen)
        avail_width = available_geometry.width()
        avail_height = available_geometry.height()
        
        # ============================================================
        # OPTIMIZED 5-TIER PROFESSIONAL BREAKPOINT SYSTEM
        # Following Google Photos/iPhone Photos/Lightroom best practices
        # Industry standard: Maximize media viewing area, minimize UI overhead
        # ============================================================

        # ============================================================
        # PROFESSIONAL LIGHTBOX SIZING SYSTEM
        # Industry-standard sizing following Google Photos/iPhone/Lightroom best practices
        # Goal: 85-90% screen utilization with proper breathing room for window management
        # ============================================================

        # Breakpoint categories with professional sizing (industry-standard approach)
        # Now uses avail_width from available_geometry for proper multi-monitor support
        if avail_width >= 3840:  # 4K+ / 8K (3840px+) - Ultra-wide displays
            size_percent = 0.85  # Conservative sizing for professional workflow
            self.responsive_tier = "4K+ Professional"
            self.toolbar_height = 64  # Standard professional toolbar height
            self.button_size = 48  # Standard button size
            self.button_size_sm = 28  # Standard small button size
            self.margin_size = 12  # Standard margins
            self.spacing_size = 8  # Standard spacing
            self.font_size_title = 12  # Standard font sizes
            self.font_size_body = 10
            self.font_size_caption = 8
            self.panel_width = 320  # Professional panel width
        elif avail_width >= 2560:  # QHD / 2K (2560-3839px) - High-end monitors
            size_percent = 0.87  # Slightly larger for bigger screens
            self.responsive_tier = "QHD/2K Professional"
            self.toolbar_height = 60
            self.button_size = 46
            self.button_size_sm = 26
            self.margin_size = 10
            self.spacing_size = 7
            self.font_size_title = 11
            self.font_size_body = 10
            self.font_size_caption = 8
            self.panel_width = 300
        elif avail_width >= 1920:  # Full HD (1920-2559px) - Standard desktop
            size_percent = 0.88  # Balanced sizing for standard desktop
            self.responsive_tier = "FullHD Professional"
            self.toolbar_height = 56
            self.button_size = 44
            self.button_size_sm = 24
            self.margin_size = 8
            self.spacing_size = 6
            self.font_size_title = 11
            self.font_size_body = 9
            self.font_size_caption = 7
            self.panel_width = 280
        elif avail_width >= 1366:  # HD / Laptop (1366-1919px) - Laptops/small screens
            size_percent = 0.90  # Maximum for smaller screens
            self.responsive_tier = "HD/Laptop Professional"
            self.toolbar_height = 52
            self.button_size = 40
            self.button_size_sm = 22
            self.margin_size = 6
            self.spacing_size = 5
            self.font_size_title = 10
            self.font_size_body = 9
            self.font_size_caption = 7
            self.panel_width = 260
        else:  # Small screens (<1366px) - Compact devices
            size_percent = 0.92  # Nearly fullscreen for tiny screens
            self.responsive_tier = "Small Screen Professional"
            self.toolbar_height = 48
            self.button_size = 36
            self.button_size_sm = 20
            self.margin_size = 4
            self.spacing_size = 4
            self.font_size_title = 10
            self.font_size_body = 8
            self.font_size_caption = 6
            self.panel_width = 240

        # Calculate window size from available geometry (respects taskbar, etc.)
        width = int(avail_width * size_percent)
        height = int(avail_height * size_percent)

        # FIX B: Set min/max constraints to keep window usable but never oversized
        self.setMinimumSize(900, 600)
        self.setMaximumSize(available_geometry.size())

        # Center the window within available geometry
        x = available_geometry.x() + (avail_width - width) // 2
        y = available_geometry.y() + (avail_height - height) // 2

        self.setGeometry(QRect(x, y, width, height))

        # Log sizing and responsive tier for debugging
        print(f"[MediaLightbox] Available: {avail_width}x{avail_height} (DPI: {dpi_scale}x)")
        print(f"[MediaLightbox] Responsive Tier: {self.responsive_tier}")
        print(f"[MediaLightbox] Window: {width}x{height} ({int(size_percent*100)}% of available)")
        print(f"[MediaLightbox] UI Scaling: Toolbar={self.toolbar_height}px, Buttons={self.button_size}px, Margins={self.margin_size}px")

        self.setStyleSheet("background: #000000; QToolTip { color: white; background-color: rgba(0,0,0,0.92); border: 1px solid #555; padding: 6px 10px; border-radius: 6px; } QMessageBox { background-color: #121212; color: white; } QMessageBox QLabel { color: white; } QMessageBox QPushButton { background: rgba(255,255,255,0.15); color: white; border: none; border-radius: 6px; padding: 6px 12px; } QMessageBox QPushButton:hover { background: rgba(255,255,255,0.25); }")  # Dark theme + tooltip/messagebox styling

        # FIX C: Removed self.show() from _setup_ui()
        # Let the caller control display via exec() or show() to avoid flashing at odd sizes

        # Main layout (vertical with toolbars + media)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === TOP TOOLBAR (Overlay with gradient) ===
        self.top_toolbar = self._create_top_toolbar()
        main_layout.addWidget(self.top_toolbar)

        # === MIDDLE SECTION: Media + Info Panel (Horizontal) ===
        middle_layout = QHBoxLayout()
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)

        # Media display area (left side, expands)
        self.scroll_area = QScrollArea()
        self.scroll_area.setStyleSheet("QScrollArea { background: #000000; border: none; }")
        self.scroll_area.setWidgetResizable(False)  # Don't auto-resize (needed for zoom)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setAlignment(Qt.AlignCenter)

        # === MEDIA CAPTION (Overlay at bottom of media, like Google Photos) ===
        self.media_caption = QLabel()
        self.media_caption.setParent(self)
        self.media_caption.setAlignment(Qt.AlignCenter)
        self.media_caption.setStyleSheet("""
            QLabel {
                background: rgba(0, 0, 0, 0.75);
                color: white;
                font-size: 11pt;
                padding: 8px 16px;
                border-radius: 4px;
            }
        """)
        self.media_caption.setWordWrap(False)
        self.media_caption.hide()  # Hidden initially, shown after load
        
        # Caption auto-hide timer (like Google Photos - fades after 3 seconds)
        self.caption_hide_timer = QTimer()
        self.caption_hide_timer.setSingleShot(True)
        self.caption_hide_timer.setInterval(3000)  # 3 seconds
        self.caption_hide_timer.timeout.connect(self._fade_out_caption)
        
        # Caption opacity effect for smooth fade
        self.caption_opacity = QGraphicsOpacityEffect()
        self.media_caption.setGraphicsEffect(self.caption_opacity)
        self.caption_opacity.setOpacity(0.0)  # Start hidden

        # CRITICAL FIX: Create container widget to hold both image and video
        # This prevents Qt from deleting widgets when switching with setWidget()
        self.media_container = QWidget()
        self.media_container.setStyleSheet("background: #000000;")
        media_container_layout = QVBoxLayout(self.media_container)
        media_container_layout.setContentsMargins(0, 0, 0, 0)
        media_container_layout.setSpacing(0)

        # Image display (for photos)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background: transparent;")
        self.image_label.setScaledContents(False)
        media_container_layout.addWidget(self.image_label)

        # PHASE A #4: Loading indicator (overlaid on media container)
        self.loading_indicator = QLabel(self.media_container)
        self.loading_indicator.setAlignment(Qt.AlignCenter)
        self.loading_indicator.setStyleSheet("""
            QLabel {
                background: rgba(0, 0, 0, 0.7);
                color: white;
                font-size: 14pt;
                padding: 20px 30px;
                border-radius: 10px;
            }
        """)
        self.loading_indicator.setText(t('google_layout.lightbox.loading'))
        self.loading_indicator.hide()
        self.loading_indicator.raise_()  # Ensure it's on top

        # PHASE C #5: Motion photo indicator (top-right corner)
        self.motion_indicator = QLabel(self)
        self.motion_indicator.setText("🎬")  # Motion icon
        self.motion_indicator.setFixedSize(48, 48)
        self.motion_indicator.setAlignment(Qt.AlignCenter)
        self.motion_indicator.setStyleSheet("""
            QLabel {
                background: rgba(0, 0, 0, 0.7);
                color: white;
                font-size: 20pt;
                border-radius: 24px;
            }
        """)
        self.motion_indicator.setToolTip(t('google_layout.lightbox.motion_photo_tooltip'))
        self.motion_indicator.hide()

        # Video display will be added to container on first video load

        # Set container as scroll area widget (never replace it!)
        self.scroll_area.setWidget(self.media_container)

        middle_layout.addWidget(self.scroll_area, 1)  # Expands to fill space

        # === OVERLAY NAVIGATION BUTTONS (Google Photos style) ===
        # Create as direct children of MediaLightbox, positioned on left/right sides
        self._create_overlay_nav_buttons()

        # Info panel (right side, toggleable)
        self.info_panel = self._create_info_panel()
        self.info_panel.hide()  # Hidden by default
        middle_layout.addWidget(self.info_panel)

        # Enhance panel (right side, toggleable)
        self.enhance_panel = self._create_enhance_panel()
        self.enhance_panel.hide()  # Hidden by default
        middle_layout.addWidget(self.enhance_panel)

        # Add middle section to main layout
        middle_widget = QWidget()
        middle_widget.setLayout(middle_layout)
        # Viewer/Editor stacked container (non-destructive editor stub)
        self.mode_stack = QStackedWidget()
        # Page 0: Viewer (existing middle_widget)
        self.mode_stack.addWidget(middle_widget)
        # Page 1: Editor (stub page - preserves viewer behavior)
        self.editor_page = QWidget()
        editor_vlayout = QVBoxLayout(self.editor_page)
        # Top row: Save/Cancel (PROMINENT STYLING)
        editor_topbar = QWidget()
        editor_topbar.setStyleSheet("background: rgba(0,0,0,0.5);")
        editor_topbar_layout = QHBoxLayout(editor_topbar)
        editor_topbar_layout.setContentsMargins(12, 8, 12, 8)
        save_btn = QPushButton()
        cancel_btn = QPushButton()
        save_btn.setText(t('google_layout.lightbox.save_button'))
        save_btn.setToolTip(t('google_layout.lightbox.save_tooltip'))
        save_btn.setStyleSheet("""
            QPushButton {
                background: rgba(34, 139, 34, 0.9);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
            }
            QPushButton:hover {
                background: rgba(34, 139, 34, 1.0);
            }
        """)
        save_btn.clicked.connect(self._save_edits)
        self._register_caption_btn(save_btn, "primary")
        cancel_btn.setText(t('google_layout.lightbox.cancel_button'))
        cancel_btn.setToolTip(t('google_layout.lightbox.cancel_tooltip'))
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(220, 53, 69, 0.9);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
            }
            QPushButton:hover {
                background: rgba(220, 53, 69, 1.0);
            }
        """)
        cancel_btn.clicked.connect(self._cancel_edits)
        self._register_caption_btn(cancel_btn, "primary")
        editor_topbar_layout.addWidget(save_btn)
        editor_topbar_layout.addWidget(cancel_btn)
        editor_topbar_layout.addSpacing(20)
        # Editor zoom controls
        self.edit_zoom_level = 1.0
        zoom_out_btn_edit = QPushButton("−")
        zoom_out_btn_edit.setToolTip(t('google_layout.lightbox.zoom_out_tooltip'))
        zoom_out_btn_edit.clicked.connect(self._editor_zoom_out)
        zoom_in_btn_edit = QPushButton("+")
        zoom_in_btn_edit.setToolTip(t('google_layout.lightbox.zoom_in_tooltip'))
        zoom_in_btn_edit.clicked.connect(self._editor_zoom_in)
        zoom_reset_btn_edit = QPushButton("100%")
        zoom_reset_btn_edit.setToolTip(t('google_layout.lightbox.zoom_reset_tooltip'))
        zoom_reset_btn_edit.clicked.connect(self._editor_zoom_reset)
        editor_topbar_layout.addSpacing(12)
        editor_topbar_layout.addWidget(zoom_out_btn_edit)
        editor_topbar_layout.addWidget(zoom_in_btn_edit)
        editor_topbar_layout.addWidget(zoom_reset_btn_edit)
        # Crop toggle (STYLED) - No hardcoded font-size (uses typography system)
        self.crop_btn = QPushButton()
        self.crop_btn.setText(t('google_layout.lightbox.crop_button'))
        self.crop_btn.setCheckable(True)
        self.crop_btn.setToolTip(t('google_layout.lightbox.crop_tooltip'))
        self.crop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QPushButton:checked {
                background: rgba(66, 133, 244, 0.8);
                border: 1px solid rgba(66, 133, 244, 1.0);
            }
        """)
        self.crop_btn.clicked.connect(self._toggle_crop_mode)
        self._register_caption_btn(self.crop_btn, "secondary")
        editor_topbar_layout.addWidget(self.crop_btn)
        # Filters toggle (STYLED) - No hardcoded font-size (uses typography system)
        self.filters_btn = QPushButton()
        self.filters_btn.setText(t('google_layout.lightbox.filters_button'))
        self.filters_btn.setCheckable(True)
        self.filters_btn.setToolTip(t('google_layout.lightbox.filters_tooltip'))
        self.filters_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QPushButton:checked {
                background: rgba(66, 133, 244, 0.8);
                border: 1px solid rgba(66, 133, 244, 1.0);
            }
        """)
        self.filters_btn.clicked.connect(self._toggle_filters_panel)
        self._register_caption_btn(self.filters_btn, "secondary")
        editor_topbar_layout.addWidget(self.filters_btn)
        # Before/After toggle (STYLED) - No hardcoded font-size (uses typography system)
        self.before_after_btn = QPushButton()
        self.before_after_btn.setText(t('google_layout.lightbox.before_after_button'))
        self.before_after_btn.setCheckable(True)
        self.before_after_btn.setToolTip(t('google_layout.lightbox.before_after_tooltip'))
        self.before_after_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QPushButton:checked {
                background: rgba(66, 133, 244, 0.8);
                border: 1px solid rgba(66, 133, 244, 1.0);
            }
        """)
        self.before_after_btn.clicked.connect(self._toggle_before_after)
        self._register_caption_btn(self.before_after_btn, "secondary")
        editor_topbar_layout.addWidget(self.before_after_btn)
        
        # Tools panel toggle (show/hide right-side editing tools) - No hardcoded font-size
        self.tools_toggle_btn = QPushButton()
        self.tools_toggle_btn.setText(t('google_layout.lightbox.tools_button'))
        self.tools_toggle_btn.setCheckable(True)
        self.tools_toggle_btn.setChecked(True)
        self.tools_toggle_btn.setToolTip(t('google_layout.lightbox.tools_tooltip'))
        self.tools_toggle_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QPushButton:checked {
                background: rgba(66, 133, 244, 0.8);
                border: 1px solid rgba(66, 133, 244, 1.0);
            }
        """)
        self.tools_toggle_btn.toggled.connect(lambda v: (self.editor_right_scroll.setVisible(v), self.editor_right_panel.setVisible(v)) if hasattr(self, 'editor_right_scroll') else (self.editor_right_panel.setVisible(v) if hasattr(self, 'editor_right_panel') else None))
        self._register_caption_btn(self.tools_toggle_btn, "secondary")
        editor_topbar_layout.addWidget(self.tools_toggle_btn)
        
        editor_topbar_layout.addStretch()  # Push buttons to left, Undo/Redo/Export to right
        # Undo/Redo buttons (MORE PROMINENT) - No hardcoded font-size (uses typography system)
        self.undo_btn = QPushButton()
        self.redo_btn = QPushButton()
        self.undo_btn.setText(t('google_layout.lightbox.undo_button'))
        self.undo_btn.setToolTip(t('google_layout.lightbox.undo_tooltip'))
        self.undo_btn.setEnabled(False)
        self.undo_btn.setStyleSheet("""
            QPushButton {
                background: rgba(66, 133, 244, 0.8);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(66, 133, 244, 1.0);
            }
            QPushButton:disabled {
                background: rgba(128, 128, 128, 0.3);
                color: rgba(255, 255, 255, 0.4);
            }
        """)
        self.undo_btn.clicked.connect(self._editor_undo)
        self._register_caption_btn(self.undo_btn, "primary")
        editor_topbar_layout.addWidget(self.undo_btn)
        self.redo_btn.setText(t('google_layout.lightbox.redo_button'))
        self.redo_btn.setToolTip(t('google_layout.lightbox.redo_tooltip'))
        self.redo_btn.setEnabled(False)
        self.redo_btn.setStyleSheet("""
            QPushButton {
                background: rgba(66, 133, 244, 0.8);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(66, 133, 244, 1.0);
            }
            QPushButton:disabled {
                background: rgba(128, 128, 128, 0.3);
                color: rgba(255, 255, 255, 0.4);
            }
        """)
        self.redo_btn.clicked.connect(self._editor_redo)
        self._register_caption_btn(self.redo_btn, "primary")
        editor_topbar_layout.addWidget(self.redo_btn)
        editor_topbar_layout.addSpacing(16)  # Visual separator
        
        # Copy/Paste buttons (BATCH EDITING) - No hardcoded font-size (uses typography system)
        self.copy_adj_btn = QPushButton()
        self.paste_adj_btn = QPushButton()
        self.copy_adj_btn.setText(t('google_layout.lightbox.copy_button'))
        self.copy_adj_btn.setToolTip(t('google_layout.lightbox.copy_tooltip'))
        self.copy_adj_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 193, 7, 0.8);
                color: black;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(255, 193, 7, 1.0);
            }
        """)
        self.copy_adj_btn.clicked.connect(self._copy_adjustments)
        self._register_caption_btn(self.copy_adj_btn, "secondary")
        editor_topbar_layout.addWidget(self.copy_adj_btn)

        self.paste_adj_btn.setText(t('google_layout.lightbox.paste_button'))
        self.paste_adj_btn.setToolTip(t('google_layout.lightbox.paste_tooltip'))
        self.paste_adj_btn.setEnabled(False)  # Disabled until something is copied
        self.paste_adj_btn.setStyleSheet("""
            QPushButton {
                background: rgba(156, 39, 176, 0.8);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(156, 39, 176, 1.0);
            }
            QPushButton:disabled {
                background: rgba(128, 128, 128, 0.3);
                color: rgba(255, 255, 255, 0.4);
            }
        """)
        self.paste_adj_btn.clicked.connect(self._paste_adjustments)
        self._register_caption_btn(self.paste_adj_btn, "secondary")
        editor_topbar_layout.addWidget(self.paste_adj_btn)
        editor_topbar_layout.addSpacing(16)  # Visual separator
        
        # Export button (MORE PROMINENT) - No hardcoded font-size (uses typography system)
        self.export_btn = QPushButton()
        self.export_btn.setText(t('google_layout.lightbox.export_button'))
        self.export_btn.setToolTip(t('google_layout.lightbox.export_tooltip'))
        self.export_btn.setStyleSheet("""
            QPushButton {
                background: rgba(34, 139, 34, 0.9);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background: rgba(34, 139, 34, 1.0);
            }
        """)
        self.export_btn.clicked.connect(self._export_current_media)
        self._register_caption_btn(self.export_btn, "primary")
        editor_topbar_layout.addWidget(self.export_btn)
        editor_vlayout.addWidget(editor_topbar)
        # Crop toolbar (hidden by default)
        self.crop_toolbar = self._build_crop_toolbar()
        self.crop_toolbar.hide()
        editor_vlayout.addWidget(self.crop_toolbar)
        # Content row: canvas + right panel
        editor_row = QWidget()
        editor_row_layout = QHBoxLayout(editor_row)
        self.editor_canvas = self._create_edit_canvas()
        
        # Right tools panel wrapped in scroll area (always accessible)
        # FIX E: Use min/max width instead of fixed, with proportional sizing (Lightroom pattern)
        from PySide6.QtWidgets import QScrollArea
        self.editor_right_panel = QWidget()
        # Slim tools column like Lightroom mobile/Photos - responsive width
        self.editor_right_panel.setMinimumWidth(260)
        self.editor_right_panel.setMaximumWidth(340)
        self.editor_right_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.editor_right_scroll = QScrollArea()
        self.editor_right_scroll.setWidget(self.editor_right_panel)
        self.editor_right_scroll.setWidgetResizable(True)
        self.editor_right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.editor_right_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        # Toggle right tools panel visibility and resize splitter proportionally
        if hasattr(self, 'tools_toggle_btn'):
            def _toggle_tools_panel(checked):
                self.editor_right_scroll.setVisible(checked)
                if checked:
                    # 75% canvas / 25% tools, clamped to panel limits
                    tools_w = min(340, max(260, int(self.width() * 0.25)))
                    self.editor_splitter.setSizes([self.width() - tools_w, tools_w])
                else:
                    self.editor_splitter.setSizes([self.width(), 0])
            self.tools_toggle_btn.toggled.connect(_toggle_tools_panel)

        from PySide6.QtWidgets import QSplitter
        self.editor_splitter = QSplitter(Qt.Horizontal)
        self.editor_splitter.addWidget(self.editor_canvas)
        self.editor_splitter.addWidget(self.editor_right_scroll)
        # Initial split: 75% canvas / 25% tools, clamped
        tools_w = min(340, max(260, int(self.width() * 0.25)))
        self.editor_splitter.setSizes([self.width() - tools_w, tools_w])
        editor_row_layout.addWidget(self.editor_splitter)
        editor_vlayout.addWidget(editor_row, 1)
        # Build adjustments panel in right placeholder
        self._init_adjustments_panel()
        # Add editor page to stack
        self.mode_stack.addWidget(self.editor_page)
        # Add stacked to main layout
        main_layout.addWidget(self.mode_stack, 1)
        self.mode_stack.setCurrentIndex(0)

        # === SLIDESHOW EDITOR PANEL (select photos for curated slideshow) ===
        self.slideshow_editor_panel = self._create_slideshow_editor()
        main_layout.addWidget(self.slideshow_editor_panel)
        self.slideshow_editor_panel.hide()

        # === BOTTOM TOOLBAR (Overlay with gradient) ===
        self.bottom_toolbar = self._create_bottom_toolbar()
        main_layout.addWidget(self.bottom_toolbar)
        self.bottom_toolbar.hide()  # Hide by default, show for videos

        # === PHASE B #1: THUMBNAIL FILMSTRIP ===
        self.filmstrip_widget = self._create_filmstrip()
        self.filmstrip_widget.hide()  # Hide by default for maximum photo area

        # Track info/enhance panel state
        self.info_panel_visible = False
        self.enhance_panel_visible = False

        # === MOUSE PANNING SUPPORT ===
        # Enable mouse tracking for hand cursor and panning
        self.setMouseTracking(True)
        self.scroll_area.setMouseTracking(True)
        self.image_label.setMouseTracking(True)

        # Panning state
        self.is_panning = False
        self.pan_start_pos = None
        self.scroll_start_x = 0
        self.scroll_start_y = 0

        # Button positioning retry counter (safety limit)
        self._position_retry_count = 0

        # === PROFESSIONAL AUTO-HIDE SYSTEM ===
        # Create opacity effects for smooth fade animations
        self.top_toolbar_opacity = QGraphicsOpacityEffect()
        self.top_toolbar.setGraphicsEffect(self.top_toolbar_opacity)
        self.top_toolbar_opacity.setOpacity(0.0)  # Hidden by default

        self.bottom_toolbar_opacity = QGraphicsOpacityEffect()
        self.bottom_toolbar.setGraphicsEffect(self.bottom_toolbar_opacity)
        self.bottom_toolbar_opacity.setOpacity(0.0)  # Hidden by default

        # Auto-hide timer (2 seconds)
        self.toolbar_hide_timer = QTimer()
        self.toolbar_hide_timer.setSingleShot(True)
        self.toolbar_hide_timer.setInterval(2000)  # 2 seconds
        self.toolbar_hide_timer.timeout.connect(self._hide_toolbars)

        # Toolbar visibility state
        self.toolbars_visible = False

        # PHASE A #5: Create keyboard shortcut help overlay
        self._create_help_overlay()

    def showEvent(self, event):
        """Ensure media loads and overlays position when the window first shows."""
        try:
            super().showEvent(event)
            from PySide6.QtCore import QTimer
            # Load current media after the widget has a valid size
            QTimer.singleShot(0, self._load_media_safe)
            # Make top toolbar visible on first show
            if hasattr(self, 'top_toolbar_opacity'):
                self.top_toolbar_opacity.setOpacity(1.0)
            # Bottom toolbar opacity reflects visibility (videos only)
            if hasattr(self, 'bottom_toolbar_opacity') and hasattr(self, 'bottom_toolbar'):
                self.bottom_toolbar_opacity.setOpacity(1.0 if self.bottom_toolbar.isVisible() else 0.0)
            # Position overlay buttons and caption shortly after layout
            QTimer.singleShot(10, self._position_nav_buttons)
            QTimer.singleShot(10, self._position_media_caption)
        except Exception as e:
            print(f"[MediaLightbox] showEvent error: {e}")

    def closeEvent(self, event):
        """Clean up resources when lightbox closes."""
        print("[MediaLightbox] Closing - cleaning up resources...")
        
        try:
            # Stop video fit debounce timer
            if hasattr(self, '_video_fit_timer'):
                self._video_fit_timer.stop()

            # Disconnect video signals before cleanup
            self._disconnect_video_signals()

            # Stop and cleanup video player
            if hasattr(self, 'video_player') and self.video_player is not None:
                try:
                    self.video_player.stop()
                    if hasattr(self, 'position_timer') and self.position_timer:
                        self.position_timer.stop()
                    # Clear source to release decoder
                    from PySide6.QtCore import QUrl
                    self.video_player.setSource(QUrl())
                    print("[MediaLightbox] ✓ Video player cleaned up")
                except Exception as video_cleanup_err:
                    print(f"[MediaLightbox] Warning during video cleanup: {video_cleanup_err}")
            
            # PHASE 2 FIX: Cleanup audio output
            if hasattr(self, 'audio_output') and self.audio_output is not None:
                try:
                    if hasattr(self, 'video_player') and self.video_player is not None:
                        self.video_player.setAudioOutput(None)  # Detach first
                    self.audio_output.deleteLater()
                    self.audio_output = None
                    print("[MediaLightbox] ✓ Audio output cleaned up")
                except Exception as audio_cleanup_err:
                    print(f"[MediaLightbox] Warning during audio cleanup: {audio_cleanup_err}")
            
            # Stop slideshow timer and Ken Burns
            if hasattr(self, 'slideshow_timer') and self.slideshow_timer:
                self.slideshow_timer.stop()
            self._stop_ken_burns()

            # Stop slideshow music
            if hasattr(self, 'slideshow_music_player') and self.slideshow_music_player:
                self.slideshow_music_player.stop()
                self.slideshow_music_player.deleteLater()
                self.slideshow_music_player = None
            if hasattr(self, 'slideshow_music_output') and self.slideshow_music_output:
                self.slideshow_music_output.deleteLater()
                self.slideshow_music_output = None
            
            # Clear preload cache to free memory
            if hasattr(self, 'preload_cache'):
                self.preload_cache.clear()
            
            # PHASE 2 FIX: Cancel and stop thread pools
            if hasattr(self, 'preload_thread_pool'):
                # Set cancellation flag for running tasks
                if hasattr(self, 'preload_cancelled'):
                    self.preload_cancelled = True
                
                # Cancel pending tasks
                self.preload_thread_pool.clear()
                
                # Wait for completion with timeout
                if not self.preload_thread_pool.waitForDone(1000):
                    print("[MediaLightbox] ⚠️ Preload tasks didn't finish in time")
                else:
                    print("[MediaLightbox] ✓ Preload thread pool stopped")
            
            print("[MediaLightbox] ✓ All resources cleaned up")
        except Exception as e:
            print(f"[MediaLightbox] Error during cleanup: {e}")
        
        # Accept the close event
        event.accept()
    
    def _disconnect_video_signals(self):
        """PHASE 2: Safely disconnect all video player signals to prevent memory leaks.
        
        This prevents signal accumulation when navigating through multiple videos.
        Without this, each video load adds new connections, causing:
        - Callback storms (slot called 50x after 50 videos)
        - Memory leaks from stale slot references
        - Performance degradation
        """
        if not hasattr(self, 'video_player') or self.video_player is None:
            return
        
        try:
            self.video_player.durationChanged.disconnect(self._on_duration_changed)
        except (TypeError, RuntimeError):
            pass  # Not connected or already disconnected
        
        try:
            self.video_player.positionChanged.disconnect(self._on_position_changed)
        except (TypeError, RuntimeError):
            pass
        
        try:
            self.video_player.errorOccurred.disconnect(self._on_video_error)
        except (TypeError, RuntimeError):
            pass
        
        try:
            self.video_player.mediaStatusChanged.disconnect(self._on_media_status_changed)
        except (TypeError, RuntimeError):
            pass
        
        print("[MediaLightbox] ✓ Video signals disconnected")
    
    def resizeEvent(self, event):
        """
        Enhanced resize event handler with dynamic responsive behavior.

        ⚡ RESPONSIVE FEATURES:
        - Recalculates breakpoint tier if screen size changed significantly
        - Updates UI element sizes dynamically (buttons, toolbars, fonts)
        - Repositions overlay elements (nav buttons, caption, filmstrip)
        - Adjusts zoom modes intelligently (fit/fill)
        - Performance-optimized with debounce timer (150ms)

        Industry Best Practices:
        - Google Photos: Smooth resize with auto-repositioning
        - iPhone Photos: Dynamic UI scaling
        - Adobe Lightroom: Professional breakpoint system
        """
        super().resizeEvent(event)

        # Immediate updates (non-debounced for smooth UX)
        # Reposition nav buttons immediately for smooth resize
        if hasattr(self, 'prev_btn') and hasattr(self, 'next_btn'):
            self._position_nav_buttons()

        # Reposition caption immediately
        if hasattr(self, 'media_caption'):
            self._position_media_caption()

        # ============================================================
        # PROFESSIONAL PANEL AUTO-HIDING
        # Automatically hide panels when window becomes too narrow
        # Following Google Photos/iPhone/Lightroom best practices
        # ============================================================
        self._handle_panel_visibility()

        # FIX D: Update panel max widths dynamically (Lightroom drawer pattern)
        max_panel_width = max(240, int(self.width() * 0.30))
        if hasattr(self, 'info_panel') and self.info_panel:
            self.info_panel.setMaximumWidth(max_panel_width)
        if hasattr(self, 'enhance_panel') and self.enhance_panel:
            self.enhance_panel.setMaximumWidth(max_panel_width)

        # ============================================================
        # DEBOUNCED UPDATES: Performance-critical operations
        # ============================================================
        # Restart debounce timer - only execute once user stops resizing
        if hasattr(self, '_resize_debounce_timer'):
            self._resize_debounce_timer.stop()
            self._resize_debounce_timer.start()

        # Throttled logging (every 500ms max) to avoid log spam
        import time
        current_time = time.perf_counter()
        if current_time - self._last_resize_log_time > 0.5:
            new_size = event.size()
            print(f"[MediaLightbox] Resize: {new_size.width()}x{new_size.height()}")
            self._last_resize_log_time = current_time

    def _handle_panel_visibility(self):
        """
        Professional panel auto-hiding system.
        
        Industry Best Practice: Automatically hide panels when window becomes too narrow
        to maintain optimal media viewing area, following Google Photos/iPhone/Lightroom standards.
        
        Thresholds:
        - Window width < 1200px: Auto-hide panels for better media focus
        - Window width >= 1200px: Allow panels to be visible
        - Respects user's last panel state when restoring
        """
        current_width = self.width()
        
        # Store original panel states if this is the first call
        if not hasattr(self, '_original_info_visible'):
            self._original_info_visible = getattr(self, 'info_panel_visible', False)
            self._original_enhance_visible = getattr(self, 'enhance_panel_visible', False)
        
        # Define minimum width for panels to remain visible
        MIN_PANEL_WIDTH = 1200  # Industry standard threshold
        
        # Auto-hide panels when window becomes too narrow
        if current_width < MIN_PANEL_WIDTH:
            # Hide panels but remember their original state
            if hasattr(self, 'info_panel') and self.info_panel.isVisible():
                self.info_panel.hide()
                self.info_panel_visible = False
                print(f"[MediaLightbox] Auto-hidden info panel (window width: {current_width}px < {MIN_PANEL_WIDTH}px)")
            
            if hasattr(self, 'enhance_panel') and self.enhance_panel.isVisible():
                self.enhance_panel.hide()
                self.enhance_panel_visible = False
                print(f"[MediaLightbox] Auto-hidden enhance panel (window width: {current_width}px < {MIN_PANEL_WIDTH}px)")
        else:
            # Restore panels if they were originally visible
            # But only restore one at a time to avoid crowding
            if (hasattr(self, '_original_info_visible') and self._original_info_visible and 
                hasattr(self, 'info_panel') and not self.info_panel.isVisible()):
                # Only show info panel if enhance panel isn't visible
                if not (hasattr(self, '_original_enhance_visible') and self._original_enhance_visible):
                    self.info_panel.show()
                    self.info_panel_visible = True
                    print(f"[MediaLightbox] Restored info panel (window width: {current_width}px >= {MIN_PANEL_WIDTH}px)")
            
            # Note: We don't auto-show enhance panel to avoid conflicts
            # User can manually toggle it if needed

    # ============================================================
    # CENTRALIZED TYPOGRAPHY SYSTEM (Google Photos / Lightroom style)
    # ============================================================
    # Single scaling source of truth for all caption button fonts.
    # No hardcoded font sizes scattered across styles.
    # Uses Qt the intended way: stylesheet for colors/padding/borders; QFont for typography

    def _ui_font_pt(self, kind: str) -> int:
        """
        Centralized typography sizes (pt), derived from responsive tier variables.

        kind: 'primary', 'secondary', 'small', 'nav', 'label'
        """
        # Base from responsive system
        body = int(getattr(self, "font_size_body", 10))
        cap = int(getattr(self, "font_size_caption", 9))

        if kind == "primary":
            return max(10, body + 1)  # e.g. Save/Cancel/Undo/Redo/Export
        if kind == "secondary":
            return max(9, body)  # e.g. Crop/Filters/Tools/Copy/Paste
        if kind == "small":
            return max(8, cap)  # e.g. crop toolbar rotate/flip/aspect buttons
        if kind == "nav":
            # Nav arrows scale with button size (not fixed 18pt)
            btn_sm = int(getattr(self, "button_size_sm", 28))
            return max(12, int(btn_sm * 0.55))
        if kind == "label":
            return max(8, cap)  # e.g. toolbar labels like "Rotate:", "Flip:"
        return body

    def _register_caption_btn(self, btn, kind: str):
        """Register a caption button for responsive font scaling."""
        if not hasattr(self, "_caption_buttons"):
            self._caption_buttons = []
        self._caption_buttons.append((btn, kind))

    def _apply_caption_fonts(self):
        """Apply scaled fonts to all registered caption buttons."""
        from PySide6.QtGui import QFont
        if not hasattr(self, "_caption_buttons"):
            return
        for btn, kind in self._caption_buttons:
            if btn is None:
                continue
            try:
                pt = self._ui_font_pt(kind)
                font = QFont()
                font.setPointSize(pt)
                # Keep bold for primary buttons
                if kind == "primary":
                    font.setBold(True)
                btn.setFont(font)
            except Exception as e:
                print(f"[Typography] Error setting font: {e}")

    def _enhanced_responsive_behavior(self):
        """
        Enhanced responsive behavior triggered after resize debounce.

        ⚡ DYNAMIC UPDATES (triggered 150ms after user stops resizing):
        - Recalculates responsive tier based on new window size
        - Updates toolbar heights, button sizes, margins, fonts
        - Adjusts filmstrip positioning and thumbnail sizes
        - Repositions motion photo indicator
        - Recalculates zoom levels (fit/fill modes)

        Best Practices:
        - Google Photos: Auto-adjust zoom on resize
        - Lightroom: Dynamic toolbar scaling
        - iPhone Photos: Smooth filmstrip repositioning
        """
        from PySide6.QtWidgets import QApplication

        # Get current window size
        current_width = self.width()
        current_height = self.height()

        # ============================================================
        # STEP 1: Recalculate responsive tier based on new size
        # ============================================================
        old_tier = self.responsive_tier if hasattr(self, 'responsive_tier') else None

        # Determine new breakpoint tier
        if current_width >= 3840:  # 4K+ / 8K
            new_tier = "4K+"
            self.responsive_tier = new_tier
            self.toolbar_height = 80
            self.button_size = 56
            self.button_size_sm = 32
            self.margin_size = 20
            self.spacing_size = 12
            self.font_size_title = 13
            self.font_size_body = 11
            self.font_size_caption = 9
        elif current_width >= 2560:  # QHD / 2K
            new_tier = "QHD/2K"
            self.responsive_tier = new_tier
            self.toolbar_height = 76
            self.button_size = 54
            self.button_size_sm = 30
            self.margin_size = 18
            self.spacing_size = 11
            self.font_size_title = 12
            self.font_size_body = 10
            self.font_size_caption = 9
        elif current_width >= 1920:  # Full HD
            new_tier = "FullHD"
            self.responsive_tier = new_tier
            self.toolbar_height = 72
            self.button_size = 52
            self.button_size_sm = 28
            self.margin_size = 16
            self.spacing_size = 10
            self.font_size_title = 11
            self.font_size_body = 10
            self.font_size_caption = 9
        elif current_width >= 1366:  # HD / Laptop
            new_tier = "HD"
            self.responsive_tier = new_tier
            self.toolbar_height = 68
            self.button_size = 48
            self.button_size_sm = 26
            self.margin_size = 12
            self.spacing_size = 8
            self.font_size_title = 11
            self.font_size_body = 10
            self.font_size_caption = 9
        else:  # Small screens
            new_tier = "Small"
            self.responsive_tier = new_tier
            self.toolbar_height = 60
            self.button_size = 44
            self.button_size_sm = 24
            self.margin_size = 8
            self.spacing_size = 6
            self.font_size_title = 10
            self.font_size_body = 9
            self.font_size_caption = 8

        # Log tier change if it changed
        if old_tier != new_tier:
            print(f"[MediaLightbox] 🔄 Responsive Tier Changed: {old_tier} → {new_tier}")
            print(f"[MediaLightbox]    New Scaling: Toolbar={self.toolbar_height}px, Buttons={self.button_size}px")

        # ============================================================
        # STEP 2: Update UI element sizes dynamically
        # ============================================================
        tier_changed = (old_tier != new_tier)

        if tier_changed and hasattr(self, 'top_toolbar'):
            # Update toolbar height
            self.top_toolbar.setFixedHeight(self.toolbar_height)

            # Update button sizes
            btn_radius = self.button_size // 2
            icon_font_size = int(self.button_size * 0.32)

            # Update main action buttons
            for btn_name in ['close_btn', 'delete_btn', 'favorite_btn', 'share_btn',
                             'slideshow_btn', 'info_btn', 'edit_btn']:
                if hasattr(self, btn_name):
                    btn = getattr(self, btn_name)
                    btn.setFixedSize(self.button_size, self.button_size)
                    # Update stylesheet to match new border radius
                    current_style = btn.styleSheet()
                    # Simple approach: just update size, keep existing style logic
                    # Full style update would require reconstructing the entire stylesheet

            # Update zoom buttons (smaller size)
            zoom_font_size = int(self.button_size_sm * 0.56)
            for btn_name in ['zoom_out_btn', 'zoom_in_btn']:
                if hasattr(self, btn_name):
                    btn = getattr(self, btn_name)
                    btn.setFixedSize(self.button_size_sm, self.button_size_sm)

            # Update label font sizes
            if hasattr(self, 'counter_label'):
                self.counter_label.setStyleSheet(
                    f"color: white; font-size: {self.font_size_body}pt; background: transparent;"
                )
            if hasattr(self, 'status_label'):
                self.status_label.setStyleSheet(
                    f"color: rgba(255,255,255,0.7); font-size: {self.font_size_caption}pt; background: transparent;"
                )

            print(f"[MediaLightbox] ✓ UI elements resized for tier: {new_tier}")

        # ============================================================
        # STEP 3: Adjust zoom mode if photo is loaded
        # ============================================================
        if self.zoom_mode in ["fit", "fill"]:
            if self.is_video_file:
                # Refit video to new viewport size (only in fit mode)
                if self.zoom_mode == "fit":
                    self._fit_video_view()
            else:
                # Recalculate zoom for fit/fill modes after resize
                if self.zoom_mode == "fit":
                    self._zoom_to_fit()
                elif self.zoom_mode == "fill":
                    self._zoom_to_fill()

        # ============================================================
        # STEP 4: Reposition filmstrip and motion indicator
        # ============================================================
        if hasattr(self, 'filmstrip_scroll') and self.filmstrip_scroll.isVisible():
            self._adjust_filmstrip_position()

        if hasattr(self, 'motion_indicator') and self.is_motion_photo:
            self._position_motion_indicator()

        # ============================================================
        # STEP 5: Update caption button fonts (centralized typography)
        # ============================================================
        self._apply_caption_fonts()

        # Update nav arrow font sizes (style-based since they're simple)
        if tier_changed:
            self._update_nav_button_styles()

        print(f"[MediaLightbox] ✓ Responsive behavior update completed")

    def _update_nav_button_styles(self):
        """Update navigation button styles with scaled font sizes."""
        nav_pt = self._ui_font_pt("nav")
        nav_style = f"""
            QPushButton {{
                background: rgba(0, 0, 0, 0.5);
                color: white;
                border: none;
                border-radius: 24px;
                font-size: {nav_pt}pt;
            }}
            QPushButton:hover {{
                background: rgba(0, 0, 0, 0.7);
            }}
            QPushButton:pressed {{
                background: rgba(0, 0, 0, 0.9);
            }}
            QPushButton:disabled {{
                background: rgba(0, 0, 0, 0.2);
                color: rgba(255, 255, 255, 0.3);
            }}
        """
        if hasattr(self, 'prev_btn') and self.prev_btn:
            self.prev_btn.setStyleSheet(nav_style)
        if hasattr(self, 'next_btn') and self.next_btn:
            self.next_btn.setStyleSheet(nav_style)

    def _adjust_filmstrip_position(self):
        """Adjust filmstrip position and size based on window dimensions."""
        if not hasattr(self, 'filmstrip_scroll'):
            return

        # Filmstrip positioning logic - can be enhanced based on window size
        # For now, just ensure it's properly positioned at the bottom
        # This is a placeholder for more sophisticated positioning logic
        pass

    def _position_motion_indicator(self):
        """Position motion photo indicator overlay."""
        if not hasattr(self, 'motion_indicator') or not self.is_motion_photo:
            return

        # Position indicator in bottom-left corner with responsive margin
        indicator_margin = self.margin_size
        if hasattr(self, 'scroll_area'):
            viewport = self.scroll_area.viewport()
            viewport_pos = viewport.mapTo(self, QPoint(0, 0))
            x = viewport_pos.x() + indicator_margin
            y = viewport_pos.y() + viewport.height() - self.motion_indicator.height() - indicator_margin

            # Account for bottom toolbar if visible
            if hasattr(self, 'bottom_toolbar') and self.bottom_toolbar.isVisible():
                y -= (self.bottom_toolbar.height() + 10)

            self.motion_indicator.move(x, y)
            self.motion_indicator.raise_()

    def _create_top_toolbar(self) -> QWidget:
        """Create top overlay toolbar with close, info, zoom, slideshow, and action buttons.

        ⚡ RESPONSIVE: Toolbar height, button sizes, and spacing dynamically scale based on screen size.
        """
        toolbar = QWidget()
        # Use responsive toolbar height from breakpoint system
        toolbar.setFixedHeight(self.toolbar_height)
        toolbar.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 0, 0, 0.9),
                    stop:1 rgba(0, 0, 0, 0));
            }
        """)

        layout = QHBoxLayout(toolbar)
        # Use responsive margins and spacing
        layout.setContentsMargins(self.margin_size, self.margin_size // 2, self.margin_size, self.margin_size // 2)
        layout.setSpacing(self.spacing_size)

        # PROFESSIONAL Button style - Dynamic sizing based on screen resolution
        # Border radius is half of button size for perfect circles
        btn_radius = self.button_size // 2
        icon_font_size = int(self.button_size * 0.32)  # Icons scale with button size
        btn_style = f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: {btn_radius}px;
                font-size: {icon_font_size}pt;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.25);
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.35);
            }}
        """

        # === LEFT SIDE: Close + Quick Actions ===
        # Close button - RESPONSIVE SIZE
        self.close_btn = QPushButton("✕")
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setFixedSize(self.button_size, self.button_size)
        self.close_btn.setStyleSheet(btn_style)
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)

        layout.addSpacing(self.spacing_size)

        # Delete button - RESPONSIVE SIZE
        self.delete_btn = QPushButton("🗑️")
        self.delete_btn.setFocusPolicy(Qt.NoFocus)
        self.delete_btn.setFixedSize(self.button_size, self.button_size)
        self.delete_btn.setStyleSheet(btn_style)
        self.delete_btn.clicked.connect(self._delete_current_media)
        self.delete_btn.setToolTip(t('google_layout.lightbox.delete_tooltip'))
        layout.addWidget(self.delete_btn)

        # Favorite button - RESPONSIVE SIZE
        self.favorite_btn = QPushButton("♡")
        self.favorite_btn.setFocusPolicy(Qt.NoFocus)
        self.favorite_btn.setFixedSize(self.button_size, self.button_size)
        self.favorite_btn.setStyleSheet(btn_style)
        self.favorite_btn.clicked.connect(self._toggle_favorite)
        self.favorite_btn.setToolTip(t('google_layout.lightbox.favorite_tooltip'))
        layout.addWidget(self.favorite_btn)

        # PHASE C #2: Share/Export button - RESPONSIVE SIZE
        self.share_btn = QPushButton("📤")
        self.share_btn.setFocusPolicy(Qt.NoFocus)
        self.share_btn.setFixedSize(self.button_size, self.button_size)
        self.share_btn.setStyleSheet(btn_style)
        self.share_btn.clicked.connect(self._show_share_dialog)
        self.share_btn.setToolTip(t('google_layout.lightbox.share_tooltip'))
        layout.addWidget(self.share_btn)

        layout.addStretch()

        # === CENTER: Counter + Zoom Indicator + Rating ===
        center_widget = QWidget()
        center_widget.setStyleSheet("background: transparent;")
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(2)

        # Counter label - RESPONSIVE FONT SIZE
        self.counter_label = QLabel()
        self.counter_label.setAlignment(Qt.AlignCenter)
        self.counter_label.setStyleSheet(f"color: white; font-size: {self.font_size_body}pt; background: transparent;")
        center_layout.addWidget(self.counter_label)

        # Zoom/Status indicator - RESPONSIVE FONT SIZE
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(f"color: rgba(255,255,255,0.7); font-size: {self.font_size_caption}pt; background: transparent;")
        center_layout.addWidget(self.status_label)

        layout.addWidget(center_widget)

        layout.addStretch()

        # === RIGHT SIDE: Zoom + Slideshow + Info ===
        # Zoom out button - RESPONSIVE SIZE (smaller buttons)
        zoom_font_size = int(self.button_size_sm * 0.56)
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFocusPolicy(Qt.NoFocus)
        self.zoom_out_btn.setFixedSize(self.button_size_sm, self.button_size_sm)
        self.zoom_out_btn.setStyleSheet(btn_style + f"QPushButton {{ font-size: {zoom_font_size}pt; font-weight: bold; }}")
        self.zoom_out_btn.clicked.connect(self._zoom_out)
        self.zoom_out_btn.setToolTip(t('google_layout.lightbox.zoom_out_tooltip'))
        layout.addWidget(self.zoom_out_btn)

        # Zoom in button - RESPONSIVE SIZE (smaller buttons)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFocusPolicy(Qt.NoFocus)
        self.zoom_in_btn.setFixedSize(self.button_size_sm, self.button_size_sm)
        self.zoom_in_btn.setStyleSheet(btn_style + f"QPushButton {{ font-size: {zoom_font_size}pt; font-weight: bold; }}")
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        self.zoom_in_btn.setToolTip(t('google_layout.lightbox.zoom_in_tooltip'))
        layout.addWidget(self.zoom_in_btn)

        layout.addSpacing(self.spacing_size // 2)

        # Slideshow button - RESPONSIVE SIZE
        self.slideshow_btn = QPushButton("▶")
        self.slideshow_btn.setFocusPolicy(Qt.NoFocus)
        self.slideshow_btn.setFixedSize(self.button_size, self.button_size)
        self.slideshow_btn.setStyleSheet(btn_style)
        self.slideshow_btn.clicked.connect(self._toggle_slideshow)
        self.slideshow_btn.setToolTip(t('google_layout.lightbox.slideshow_tooltip'))
        layout.addWidget(self.slideshow_btn)

        # Slideshow settings button (speed, Ken Burns, music)
        self.slideshow_settings_btn = QPushButton("♫")
        self.slideshow_settings_btn.setFocusPolicy(Qt.NoFocus)
        self.slideshow_settings_btn.setFixedSize(self.button_size_sm, self.button_size_sm)
        self.slideshow_settings_btn.setStyleSheet(
            btn_style + f"QPushButton {{ font-size: {zoom_font_size}pt; }}"
        )
        self.slideshow_settings_btn.clicked.connect(self._show_slideshow_settings)
        self.slideshow_settings_btn.setToolTip("Slideshow Settings & Music")
        layout.addWidget(self.slideshow_settings_btn)

        # Slideshow editor button (select photos for slideshow)
        self.slideshow_editor_btn = QPushButton("🎞")
        self.slideshow_editor_btn.setFocusPolicy(Qt.NoFocus)
        self.slideshow_editor_btn.setFixedSize(self.button_size_sm, self.button_size_sm)
        self.slideshow_editor_btn.setStyleSheet(
            btn_style + f"QPushButton {{ font-size: {zoom_font_size}pt; }}"
        )
        self.slideshow_editor_btn.clicked.connect(self._toggle_slideshow_editor)
        self.slideshow_editor_btn.setToolTip("Edit Slideshow (select photos)")
        layout.addWidget(self.slideshow_editor_btn)

        # Info toggle button - RESPONSIVE SIZE
        self.info_btn = QPushButton("ℹ️")
        self.info_btn.setFocusPolicy(Qt.NoFocus)
        self.info_btn.setFixedSize(self.button_size, self.button_size)
        self.info_btn.setStyleSheet(btn_style)
        self.info_btn.clicked.connect(self._toggle_info_panel)
        self.info_btn.setToolTip(t('google_layout.lightbox.info_tooltip'))
        layout.addWidget(self.info_btn)

        # Edit/Enhance panel toggle (photos only) - RESPONSIVE SIZE
        self.edit_btn = QPushButton("✨")
        self.edit_btn.setFocusPolicy(Qt.NoFocus)
        self.edit_btn.setFixedSize(self.button_size, self.button_size)
        self.edit_btn.setStyleSheet(btn_style)
        self.edit_btn.setToolTip(t('google_layout.lightbox.edit_tooltip'))
        self.edit_btn.clicked.connect(self._enter_edit_mode)
        layout.addWidget(self.edit_btn)
        self.photo_only_buttons.append(self.edit_btn)

        # Hide inline enhance/preset buttons (moved to panel)
        # Use pill-style for caption buttons (Lightroom/iOS style) - not circular icon style
        pill_btn_style = """
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.35);
            }
        """
        self.enhance_btn = QPushButton("✨ Enhance")
        self.enhance_btn.setFocusPolicy(Qt.NoFocus)
        self.enhance_btn.setFixedHeight(32)
        self.enhance_btn.setStyleSheet(pill_btn_style)
        self.enhance_btn.setToolTip("Auto-Enhance (Improve brightness/contrast/color)")
        self.enhance_btn.clicked.connect(self._toggle_auto_enhance)
        self._register_caption_btn(self.enhance_btn, "secondary")
        self.enhance_btn.hide()

        self.dynamic_btn = QPushButton("Dynamic")
        self.dynamic_btn.setFocusPolicy(Qt.NoFocus)
        self.dynamic_btn.setFixedHeight(32)
        self.dynamic_btn.setStyleSheet(pill_btn_style)
        self.dynamic_btn.setToolTip("Dynamic: vivid colors & contrast")
        self.dynamic_btn.clicked.connect(lambda: self._set_preset("dynamic"))
        self._register_caption_btn(self.dynamic_btn, "secondary")
        self.dynamic_btn.hide()

        self.warm_btn = QPushButton("Warm")
        self.warm_btn.setFocusPolicy(Qt.NoFocus)
        self.warm_btn.setFixedHeight(32)
        self.warm_btn.setStyleSheet(pill_btn_style)
        self.warm_btn.setToolTip("Warm: cozy tones")
        self.warm_btn.clicked.connect(lambda: self._set_preset("warm"))
        self._register_caption_btn(self.warm_btn, "secondary")
        self.warm_btn.hide()

        self.cool_btn = QPushButton("Cool")
        self.cool_btn.setFocusPolicy(Qt.NoFocus)
        self.cool_btn.setFixedHeight(32)
        self.cool_btn.setStyleSheet(pill_btn_style)
        self.cool_btn.setToolTip("Cool: crisp bluish look")
        self.cool_btn.clicked.connect(lambda: self._set_preset("cool"))
        self._register_caption_btn(self.cool_btn, "secondary")
        self.cool_btn.hide()

        return toolbar

    def _create_bottom_toolbar(self) -> QWidget:
        """Create bottom overlay toolbar with navigation and video controls."""
        toolbar = QWidget()
        # FIX F: Make toolbar height responsive based on tier (not hardcoded 80px)
        # On smaller screens, 80px eats precious vertical space making content feel cramped
        toolbar_h = max(56, int(self.toolbar_height * 1.1))  # Tier-based, min 56px
        toolbar.setFixedHeight(toolbar_h)
        toolbar.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 0, 0, 0),
                    stop:1 rgba(0, 0, 0, 0.8));
            }
        """)

        layout = QVBoxLayout(toolbar)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        # Video controls container (hidden by default, shown for videos)
        self.video_controls_widget = self._create_video_controls()
        layout.addWidget(self.video_controls_widget)

        # Navigation controls moved to overlay (see _create_overlay_nav_buttons)

        return toolbar

    def _create_overlay_nav_buttons(self):
        """Create Google Photos-style overlay navigation buttons on left/right sides."""
        from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve
        from PySide6.QtGui import QCursor

        print("[MediaLightbox] Creating overlay navigation buttons...")

        # Get dynamic nav font size from typography system (scales with button_size_sm)
        nav_pt = self._ui_font_pt("nav")
        nav_style = f"""
            QPushButton {{
                background: rgba(0, 0, 0, 0.5);
                color: white;
                border: none;
                border-radius: 24px;
                font-size: {nav_pt}pt;
            }}
            QPushButton:hover {{
                background: rgba(0, 0, 0, 0.7);
            }}
            QPushButton:pressed {{
                background: rgba(0, 0, 0, 0.9);
            }}
            QPushButton:disabled {{
                background: rgba(0, 0, 0, 0.2);
                color: rgba(255, 255, 255, 0.3);
            }}
        """

        # Previous button (left side)
        self.prev_btn = QPushButton("◄", self)
        self.prev_btn.setFocusPolicy(Qt.NoFocus)
        self.prev_btn.setFixedSize(48, 48)
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.setStyleSheet(nav_style)
        self.prev_btn.clicked.connect(self._previous_media)

        # Next button (right side)
        self.next_btn = QPushButton("►", self)
        self.next_btn.setFocusPolicy(Qt.NoFocus)
        self.next_btn.setFixedSize(48, 48)
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.setStyleSheet(nav_style)
        self.next_btn.clicked.connect(self._next_media)

        # CRITICAL: Show buttons explicitly
        self.prev_btn.show()
        self.next_btn.show()

        # Raise buttons above other widgets (overlay effect)
        self.prev_btn.raise_()
        self.next_btn.raise_()

        # CRITICAL FIX: Use QGraphicsOpacityEffect instead of setWindowOpacity
        # (windowOpacity only works on top-level windows, not child widgets)
        self.prev_btn_opacity = QGraphicsOpacityEffect()
        self.prev_btn.setGraphicsEffect(self.prev_btn_opacity)
        self.prev_btn_opacity.setOpacity(1.0)  # Start visible

        self.next_btn_opacity = QGraphicsOpacityEffect()
        self.next_btn.setGraphicsEffect(self.next_btn_opacity)
        self.next_btn_opacity.setOpacity(1.0)  # Start visible

        self.nav_buttons_visible = True  # Start visible

        # Auto-hide timer
        self.nav_hide_timer = QTimer()
        self.nav_hide_timer.setSingleShot(True)
        self.nav_hide_timer.timeout.connect(self._hide_nav_buttons)

        # Position buttons (will be called in resizeEvent)
        QTimer.singleShot(0, self._position_nav_buttons)

        print(f"[MediaLightbox] ✓ Nav buttons created and shown")

    # === PROFESSIONAL AUTO-HIDE TOOLBAR SYSTEM ===

    def _show_toolbars(self):
        """Show toolbars with smooth fade-in animation."""
        if not self.toolbars_visible:
            self.toolbars_visible = True

            # Fade in both toolbars (smooth 200ms animation)
            self.top_toolbar_opacity.setOpacity(1.0)
            self.bottom_toolbar_opacity.setOpacity(1.0)

        # Only auto-hide in fullscreen mode
        if self.isFullScreen():
            self.toolbar_hide_timer.stop()
            self.toolbar_hide_timer.start()  # Restart 2-second timer

    def _hide_toolbars(self):
        """Hide toolbars with smooth fade-out animation (fullscreen only)."""
        # Only hide if in fullscreen
        if self.isFullScreen() and self.toolbars_visible:
            self.toolbars_visible = False

            # Fade out both toolbars (smooth 200ms animation)
            self.top_toolbar_opacity.setOpacity(0.0)
            self.bottom_toolbar_opacity.setOpacity(0.0)

    # === END AUTO-HIDE SYSTEM ===

    def eventFilter(self, obj, event):
        try:
            from PySide6.QtCore import QEvent, Qt

            # Identify if event source is an editor zoom target
            is_editor_canvas = hasattr(self, 'editor_canvas') and obj == self.editor_canvas
            is_video_view = hasattr(self, 'video_graphics_view') and (
                obj == self.video_graphics_view or
                obj == self.video_graphics_view.viewport()
            )
            is_zoom_target = is_editor_canvas or is_video_view

            # EVENT-DRIVEN VIDEO FIT: React to Show/Resize on video viewport
            # This replaces timer-based guessing with proper Qt lifecycle events.
            # When the video view becomes visible or gets resized, we schedule a fit.
            # This is how Google Photos / Lightroom ensure the video fills the canvas.
            if is_video_view and event.type() in (QEvent.Show, QEvent.Resize):
                if getattr(self, 'is_video_file', False):
                    self._fit_video_view()

            # Handle wheel zoom on any zoom target
            if is_zoom_target and event.type() == QEvent.Wheel:
                # Check if in editor mode
                if hasattr(self, 'mode_stack') and self.mode_stack.currentIndex() == 1:
                    # Ctrl+Wheel = zoom, plain wheel = scroll
                    try:
                        modifiers = event.modifiers()
                        ctrl_pressed = bool(modifiers & Qt.ControlModifier)
                    except:
                        ctrl_pressed = False

                    if ctrl_pressed:
                        delta = event.angleDelta().y()
                        if delta > 0:
                            self._editor_zoom_in()
                        else:
                            self._editor_zoom_out()
                        return True  # Consume event

            return super().eventFilter(obj, event)
        except Exception as e:
            import traceback
            print(f"[EventFilter] Error: {e}")
            traceback.print_exc()
            return False

    def _editor_zoom_in(self):
        self.edit_zoom_level = min(4.0, getattr(self, 'edit_zoom_level', 1.0) * 1.15)
        if hasattr(self, '_apply_video_zoom'):
            self._apply_video_zoom()
        if hasattr(self, '_update_zoom_status'):
            self._update_zoom_status()

    def _editor_zoom_out(self):
        self.edit_zoom_level = max(0.25, getattr(self, 'edit_zoom_level', 1.0) / 1.15)
        if hasattr(self, '_apply_video_zoom'):
            self._apply_video_zoom()
        if hasattr(self, '_update_zoom_status'):
            self._update_zoom_status()

    def _editor_zoom_reset(self):
        self.edit_zoom_level = 1.0
        if hasattr(self, '_apply_video_zoom'):
            self._apply_video_zoom()
        if hasattr(self, '_update_zoom_status'):
            self._update_zoom_status()

    def _apply_editor_zoom(self):
        try:
            if hasattr(self, '_apply_video_zoom'):
                self._apply_video_zoom()
            if getattr(self, 'editor_canvas', None):
                self.editor_canvas.update()
        except Exception as e:
            print(f"[EditZoom] Error applying editor zoom: {e}")

    def _toggle_info_panel(self):
        try:
            if not hasattr(self, 'info_panel_visible'):
                self.info_panel_visible = False
            self.info_panel_visible = not self.info_panel_visible
            if hasattr(self, 'info_panel') and self.info_panel:
                self.info_panel.setVisible(self.info_panel_visible)
                # When showing, refresh editable metadata for current photo
                if self.info_panel_visible:
                    try:
                        self._load_editable_metadata()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[InfoPanel] Toggle error: {e}")

    def _toggle_raw_group(self):
        try:
            visible = self.raw_toggle.isChecked()
            self.raw_group_container.setVisible(visible)
            self.raw_toggle.setText("RAW Development ▾" if visible else "RAW Development ▸")
        except Exception:
            pass
    
    def _toggle_light_group(self):
        try:
            visible = self.light_toggle.isChecked()
            self.light_group_container.setVisible(visible)
            self.light_toggle.setText("Light ▾" if visible else "Light ▸")
        except Exception:
            pass

    def _toggle_color_group(self):
        try:
            visible = self.color_toggle.isChecked()
            self.color_group_container.setVisible(visible)
            self.color_toggle.setText("Color ▾" if visible else "Color ▸")
        except Exception:
            pass

    def _init_adjustments_panel(self):
        """Initialize adjustments panel in the editor right placeholder."""
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSlider, QPushButton, QSpinBox, QHBoxLayout
        # Create container layout on right panel if missing
        if not hasattr(self, 'adjustments_layout') or self.adjustments_layout is None:
            self.adjustments_layout = QVBoxLayout(self.editor_right_panel)
            self.adjustments_layout.setContentsMargins(12, 12, 12, 12)
            self.adjustments_layout.setSpacing(8)
        # ============================================================
        # PERFORMANCE: Debounce timer for smooth real-time preview
        # ============================================================
        if not hasattr(self, '_adjust_debounce_timer') or self._adjust_debounce_timer is None:
            self._adjust_debounce_timer = QTimer(self)
            self._adjust_debounce_timer.setSingleShot(True)
            self._adjust_debounce_timer.setInterval(50)  # 50ms debounce (faster response)
            # Debounced updates use normal preview (2048px)
            self._adjust_debounce_timer.timeout.connect(
                lambda: self._apply_adjustments(preview_quality='normal', update_histogram=False, push_history=False)
            )
        # Initialize adjustments dict
        self.adjustments = {
            'brightness': 0,
            'exposure': 0,
            'contrast': 0,
            'highlights': 0,
            'shadows': 0,
            'vignette': 0,
            'saturation': 0,
            'warmth': 0,
        }
        # Header
        header = QLabel(t('google_layout.lightbox.adjustments_header'))
        header.setStyleSheet("color: white; font-size: 11pt;")
        self.adjustments_layout.addWidget(header)
        # Histogram at top
        self.histogram_label = QLabel()
        self.histogram_label.setFixedHeight(120)
        self.histogram_label.setMinimumWidth(360)
        self.adjustments_layout.addWidget(self.histogram_label)
        # Light group
        self.light_toggle = QPushButton("Light ▾")
        self.light_toggle.setCheckable(True)
        self.light_toggle.setChecked(True)
        self.light_toggle.setStyleSheet("color: rgba(255,255,255,0.9); font-size: 10pt; background: transparent; border: none; text-align: left;")
        self.light_toggle.clicked.connect(self._toggle_light_group)
        self.adjustments_layout.addWidget(self.light_toggle)
        self.light_group_container = QWidget()
        self.light_group_layout = QVBoxLayout(self.light_group_container)
        self.light_group_layout.setContentsMargins(0, 0, 0, 0)
        self.light_group_layout.setSpacing(6)
        self.adjustments_layout.addWidget(self.light_group_container)
        
        # Helper to create slider row with spin box
        def add_slider_row(name, label_text):
            # Label row: name + value spinbox
            label_row = QHBoxLayout()
            label = QLabel(label_text)
            label.setStyleSheet("color: rgba(255,255,255,0.85);")
            label_row.addWidget(label)
            label_row.addStretch()
            spinbox = QSpinBox()
            spinbox.setRange(-100, 100)
            spinbox.setValue(0)
            spinbox.setFixedWidth(60)
            spinbox.setStyleSheet("""
                QSpinBox {
                    background: rgba(255,255,255,0.1);
                    color: white;
                    border: 1px solid rgba(255,255,255,0.2);
                    border-radius: 4px;
                    padding: 2px 4px;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    background: transparent;
                    width: 12px;
                }
            """)
            spinbox.valueChanged.connect(lambda v: self._on_spinbox_change(name, v))
            setattr(self, f"spinbox_{name}", spinbox)
            label_row.addWidget(spinbox)
            # Slider
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(0)
            # Track drag state for ultra-low preview
            slider.sliderPressed.connect(lambda: self._on_slider_pressed(name))
            # Real-time preview during drag (ULTRA-LOW for smooth feedback)
            slider.valueChanged.connect(lambda v: self._on_slider_change(name, v))
            # Final quality when released (NORMAL preview + histogram + history)
            slider.sliderReleased.connect(lambda: self._on_slider_released(name))
            setattr(self, f"slider_{name}", slider)
            return label_row, slider
        
        # Light adjustments with spin boxes
        bright_label_row, self.slider_brightness = add_slider_row('brightness', 'Brightness')
        self.light_group_layout.addLayout(bright_label_row)
        self.light_group_layout.addWidget(self.slider_brightness)
        
        exp_label_row, self.slider_exposure = add_slider_row('exposure', 'Exposure')
        self.light_group_layout.addLayout(exp_label_row)
        self.light_group_layout.addWidget(self.slider_exposure)
        
        cont_label_row, self.slider_contrast = add_slider_row('contrast', 'Contrast')
        self.light_group_layout.addLayout(cont_label_row)
        self.light_group_layout.addWidget(self.slider_contrast)
        
        high_label_row, self.slider_highlights = add_slider_row('highlights', 'Highlights')
        self.light_group_layout.addLayout(high_label_row)
        self.light_group_layout.addWidget(self.slider_highlights)
        
        shad_label_row, self.slider_shadows = add_slider_row('shadows', 'Shadows')
        self.light_group_layout.addLayout(shad_label_row)
        self.light_group_layout.addWidget(self.slider_shadows)
        
        vig_label_row, self.slider_vignette = add_slider_row('vignette', 'Vignette')
        self.light_group_layout.addLayout(vig_label_row)
        self.light_group_layout.addWidget(self.slider_vignette)
        
        sharp_label_row, self.slider_sharpen = add_slider_row('sharpen', 'Sharpen')
        self.light_group_layout.addLayout(sharp_label_row)
        self.light_group_layout.addWidget(self.slider_sharpen)
        
        # Color group
        self.color_toggle = QPushButton("Color ▾")
        self.color_toggle.setCheckable(True)
        self.color_toggle.setChecked(True)
        self.color_toggle.setStyleSheet("color: rgba(255,255,255,0.9); font-size: 10pt; background: transparent; border: none; text-align: left;")
        self.color_toggle.clicked.connect(self._toggle_color_group)
        self.adjustments_layout.addWidget(self.color_toggle)
        self.color_group_container = QWidget()
        self.color_group_layout = QVBoxLayout(self.color_group_container)
        self.color_group_layout.setContentsMargins(0, 0, 0, 0)
        self.color_group_layout.setSpacing(6)
        self.adjustments_layout.addWidget(self.color_group_container)
        
        sat_label_row, self.slider_saturation = add_slider_row('saturation', 'Saturation')
        self.color_group_layout.addLayout(sat_label_row)
        self.color_group_layout.addWidget(self.slider_saturation)
        
        warm_label_row, self.slider_warmth = add_slider_row('warmth', 'Warmth')
        self.color_group_layout.addLayout(warm_label_row)
        self.color_group_layout.addWidget(self.slider_warmth)
        
        # RAW Development group (only shown for RAW files)
        self.raw_toggle = QPushButton("RAW Development ▸")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.setChecked(False)
        self.raw_toggle.clicked.connect(self._toggle_raw_group)
        self.raw_toggle.setVisible(False)  # Hidden by default, shown for RAW files
        self.adjustments_layout.addWidget(self.raw_toggle)
        
        self.raw_group_container = QWidget()
        self.raw_group_layout = QVBoxLayout(self.raw_group_container)
        self.raw_group_layout.setContentsMargins(0, 0, 0, 0)
        self.raw_group_layout.setSpacing(6)
        self.raw_group_container.setVisible(False)
        self.adjustments_layout.addWidget(self.raw_group_container)
        
        # RAW adjustments with spin boxes
        wb_temp_label_row, self.slider_white_balance_temp = add_slider_row('white_balance_temp', 'WB Temperature')
        self.raw_group_layout.addLayout(wb_temp_label_row)
        self.raw_group_layout.addWidget(self.slider_white_balance_temp)
        
        wb_tint_label_row, self.slider_white_balance_tint = add_slider_row('white_balance_tint', 'WB Tint (G/M)')
        self.raw_group_layout.addLayout(wb_tint_label_row)
        self.raw_group_layout.addWidget(self.slider_white_balance_tint)
        
        # Note: Exposure recovery, lens correction, chromatic aberration use 0-100 range
        def add_slider_row_0_100(name, label_text):
            label_row = QHBoxLayout()
            label = QLabel(label_text)
            label.setStyleSheet("color: rgba(255,255,255,0.85);")
            label_row.addWidget(label)
            label_row.addStretch()
            spinbox = QSpinBox()
            spinbox.setRange(0, 100)
            spinbox.setValue(0)
            spinbox.setFixedWidth(60)
            spinbox.setStyleSheet("""
                QSpinBox {
                    background: rgba(255,255,255,0.1);
                    color: white;
                    border: 1px solid rgba(255,255,255,0.2);
                    border-radius: 4px;
                    padding: 2px 4px;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    background: transparent;
                    width: 12px;
                }
            """)
            spinbox.valueChanged.connect(lambda v: self._on_spinbox_change(name, v))
            setattr(self, f"spinbox_{name}", spinbox)
            label_row.addWidget(spinbox)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(0)
            slider.valueChanged.connect(lambda v: self._on_slider_change(name, v))
            setattr(self, f"slider_{name}", slider)
            return label_row, slider
        
        exp_rec_label_row, self.slider_exposure_recovery = add_slider_row_0_100('exposure_recovery', 'Highlight Recovery')
        self.raw_group_layout.addLayout(exp_rec_label_row)
        self.raw_group_layout.addWidget(self.slider_exposure_recovery)
        
        lens_corr_label_row, self.slider_lens_correction = add_slider_row_0_100('lens_correction', 'Lens Correction')
        self.raw_group_layout.addLayout(lens_corr_label_row)
        self.raw_group_layout.addWidget(self.slider_lens_correction)
        
        ca_label_row, self.slider_chromatic_aberration = add_slider_row_0_100('chromatic_aberration', 'CA Removal')
        self.raw_group_layout.addLayout(ca_label_row)
        self.raw_group_layout.addWidget(self.slider_chromatic_aberration)
        
        # Reset button
        reset_btn = QPushButton("Reset All")
        reset_btn.clicked.connect(self._reset_adjustments)
        self.adjustments_layout.addWidget(reset_btn)
        # Build filters panel and add to right panel (hidden by default)
        self.filters_container = self._build_filters_panel()
        self.filters_container.hide()
        self.adjustments_layout.addWidget(self.filters_container)

    def _save_edit_state(self):
        """Save current edit state to JSON file for persistence."""
        try:
            if not hasattr(self, 'media_path') or not self.media_path:
                return
            
            import json
            import hashlib
            from datetime import datetime

            # Create unique filename based on image path hash
            path_hash = hashlib.md5(self.media_path.encode()).hexdigest()
            state_file = os.path.join(self.edit_states_dir, f"{path_hash}.json")
            
            # Collect edit state
            edit_state = {
                'media_path': self.media_path,
                'adjustments': self.adjustments.copy(),
                'filter_intensity': getattr(self, 'filter_intensity', 100),
                'current_preset': getattr(self, 'current_preset_adjustments', {}),
                'timestamp': str(datetime.now())
            }
            
            # Save to file
            with open(state_file, 'w') as f:
                json.dump(edit_state, f, indent=2)
            
            print(f"[EditState] Saved edit state for {os.path.basename(self.media_path)}")
            return True
        except Exception as e:
            import traceback
            print(f"[EditState] Error saving edit state: {e}")
            traceback.print_exc()
            return False
    
    def _load_edit_state(self):
        """Load saved edit state from JSON file if exists."""
        try:
            if not hasattr(self, 'media_path') or not self.media_path:
                return False
            
            import json
            import hashlib
            
            # Find state file
            path_hash = hashlib.md5(self.media_path.encode()).hexdigest()
            state_file = os.path.join(self.edit_states_dir, f"{path_hash}.json")
            
            if not os.path.exists(state_file):
                return False
            
            # Load state
            with open(state_file, 'r') as f:
                edit_state = json.load(f)
            
            # Verify it's for the correct image
            if edit_state.get('media_path') != self.media_path:
                return False
            
            # Restore adjustments
            adjustments = edit_state.get('adjustments', {})
            for key, val in adjustments.items():
                if key in self.adjustments:
                    self.adjustments[key] = val
                    # Update sliders and spinboxes
                    slider = getattr(self, f"slider_{key}", None)
                    spinbox = getattr(self, f"spinbox_{key}", None)
                    if slider:
                        slider.blockSignals(True)
                        slider.setValue(val)
                        slider.blockSignals(False)
                    if spinbox:
                        spinbox.blockSignals(True)
                        spinbox.setValue(val)
                        spinbox.blockSignals(False)
            
            # Restore filter intensity
            filter_intensity = edit_state.get('filter_intensity', 100)
            self.filter_intensity = filter_intensity
            if hasattr(self, 'filter_intensity_slider'):
                self.filter_intensity_slider.blockSignals(True)
                self.filter_intensity_slider.setValue(filter_intensity)
                self.filter_intensity_slider.blockSignals(False)
            if hasattr(self, 'intensity_value_label'):
                self.intensity_value_label.setText(f"{filter_intensity}%")
            
            # Restore current preset
            self.current_preset_adjustments = edit_state.get('current_preset', {})

            # Apply the loaded adjustments - normal preview with histogram
            self._apply_adjustments(preview_quality='normal', update_histogram=True, push_history=False)

            print(f"[EditState] Restored edit state for {os.path.basename(self.media_path)}")
            return True
        except Exception as e:
            import traceback
            print(f"[EditState] Error loading edit state: {e}")
            traceback.print_exc()
            return False
    
    def _show_raw_notification(self, message: str, is_warning: bool = False):
        """Show temporary notification for RAW file status."""
        try:
            from PySide6.QtWidgets import QLabel
            from PySide6.QtCore import QTimer
            
            # Create notification label
            if not hasattr(self, '_raw_notification_label'):
                self._raw_notification_label = QLabel(self)
                self._raw_notification_label.setAlignment(Qt.AlignCenter)
                self._raw_notification_label.setStyleSheet("""
                    QLabel {
                        background: rgba(33, 150, 243, 0.9);
                        color: white;
                        padding: 12px 24px;
                        border-radius: 8px;
                        font-size: 11pt;
                        font-weight: bold;
                    }
                """)
                self._raw_notification_label.setVisible(False)
            
            # Update style if warning
            if is_warning:
                self._raw_notification_label.setStyleSheet("""
                    QLabel {
                        background: rgba(255, 152, 0, 0.9);
                        color: white;
                        padding: 12px 24px;
                        border-radius: 8px;
                        font-size: 11pt;
                        font-weight: bold;
                    }
                """)
            
            # Set message and show
            self._raw_notification_label.setText(message)
            self._raw_notification_label.adjustSize()
            
            # Position at top center
            parent_width = self.width()
            label_width = self._raw_notification_label.width()
            self._raw_notification_label.move((parent_width - label_width) // 2, 80)
            self._raw_notification_label.raise_()
            self._raw_notification_label.setVisible(True)
            
            # Auto-hide after 5 seconds
            QTimer.singleShot(5000, lambda: self._raw_notification_label.setVisible(False) if hasattr(self, '_raw_notification_label') else None)
            
        except Exception as e:
            print(f"[RAW] Error showing notification: {e}")
    
    def _is_raw_file(self, file_path: str) -> bool:
        """Check if file is a RAW image format."""
        if not file_path:
            return False
        raw_extensions = [
            '.cr2', '.cr3',  # Canon
            '.nef', '.nrw',  # Nikon
            '.arw', '.srf', '.sr2',  # Sony
            '.orf',  # Olympus
            '.rw2',  # Panasonic
            '.pef', '.ptx',  # Pentax
            '.raf',  # Fujifilm
            '.dng',  # Adobe Digital Negative
            '.x3f',  # Sigma
            '.3fr',  # Hasselblad
            '.fff',  # Imacon
            '.dcr', '.kdc',  # Kodak
            '.mrw',  # Minolta
            '.raw', '.rwl',  # Leica
            '.iiq',  # Phase One
        ]
        ext = os.path.splitext(file_path)[1].lower()
        return ext in raw_extensions
    
    def _is_video_file(self, file_path: str) -> bool:
        """Check if file is a video format."""
        if not file_path:
            return False
        # Keep in sync with _is_video() for consistent behavior
        video_extensions = {
            '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp',
            '.flv', '.wmv', '.mpg', '.mpeg', '.mts', '.m2ts', '.ts',
            '.vob', '.ogv', '.divx', '.asf', '.rm', '.rmvb'
        }
        ext = os.path.splitext(file_path)[1].lower()
        return ext in video_extensions
    
    def _check_rawpy_available(self) -> bool:
        """Check if rawpy library is available."""
        try:
            import rawpy
            return True
        except ImportError:
            return False
    
    def _load_raw_image(self, file_path: str):
        """Load RAW image using rawpy."""
        try:
            if not self._check_rawpy_available():
                print("[RAW] rawpy library not available - install with: pip install rawpy")
                return None
            
            import rawpy
            raw = rawpy.imread(file_path)
            print(f"[RAW] Loaded RAW file: {os.path.basename(file_path)}")
            print(f"[RAW] Camera: {getattr(raw.color_desc, 'decode', lambda: 'Unknown')()}")
            print(f"[RAW] Size: {raw.sizes.raw_width}x{raw.sizes.raw_height}")
            return raw
        except Exception as e:
            import traceback
            print(f"[RAW] Error loading RAW file: {e}")
            traceback.print_exc()
            return None
    
    def _process_raw_to_pixmap(self, raw_image, adjustments: dict = None):
        """Process RAW image with adjustments and convert to QPixmap."""
        try:
            if not raw_image:
                return None
            
            import rawpy
            from PIL import Image
            import numpy as np
            
            # Get adjustments or use defaults
            adj = adjustments or self.adjustments
            
            # Prepare rawpy processing parameters
            params = rawpy.Params(
                use_camera_wb=True,  # Use camera white balance as starting point
                use_auto_wb=False,
                output_color=rawpy.ColorSpace.sRGB,
                output_bps=8,  # 8-bit output
                no_auto_bright=True,  # Disable auto brightness (we'll handle it)
                exp_shift=1.0,  # Exposure adjustment
                bright=1.0,  # Brightness multiplier
                user_wb=None,  # Custom white balance
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # High quality
                median_filter_passes=0
            )
            
            # Apply RAW-specific adjustments
            # White balance temperature
            temp = adj.get('white_balance_temp', 0)
            tint = adj.get('white_balance_tint', 0)
            if temp != 0 or tint != 0:
                # Adjust white balance multipliers
                # This is a simplified approach - real WB is more complex
                wb_mult = list(raw_image.camera_whitebalance)
                # Temperature: affects red/blue balance
                if temp > 0:  # Warmer (more red)
                    wb_mult[0] *= (1.0 + temp / 200.0)  # Increase red
                    wb_mult[2] *= (1.0 - temp / 200.0)  # Decrease blue
                else:  # Cooler (more blue)
                    wb_mult[0] *= (1.0 + temp / 200.0)  # Decrease red
                    wb_mult[2] *= (1.0 - temp / 200.0)  # Increase blue
                # Tint: affects green/magenta balance
                if tint != 0:
                    wb_mult[1] *= (1.0 + tint / 200.0)  # Adjust green
                params.user_wb = wb_mult
            
            # Exposure recovery (preserve highlights)
            exp_recovery = adj.get('exposure_recovery', 0)
            if exp_recovery > 0:
                # Reduce exp_shift to preserve highlights
                params.exp_shift = 1.0 - (exp_recovery / 200.0)  # 0 to 100 -> 1.0 to 0.5
            
            # Process RAW to RGB array
            rgb = raw_image.postprocess(params)
            
            # Convert to PIL Image
            pil_img = Image.fromarray(rgb)
            
            # Apply lens correction (simple barrel/pincushion distortion)
            lens_corr = adj.get('lens_correction', 0)
            if lens_corr > 0:
                # This is a placeholder - real lens correction requires specific lens profiles
                print(f"[RAW] Lens correction: {lens_corr}% (simplified implementation)")
            
            # Apply chromatic aberration removal
            ca_removal = adj.get('chromatic_aberration', 0)
            if ca_removal > 0:
                # Simplified CA removal - real implementation would shift color channels
                print(f"[RAW] Chromatic aberration removal: {ca_removal}% (simplified)")
            
            # Convert PIL to QPixmap
            pixmap = self._pil_to_qpixmap(pil_img)
            
            print(f"[RAW] Processed RAW image with adjustments")
            return pixmap
            
        except Exception as e:
            import traceback
            print(f"[RAW] Error processing RAW image: {e}")
            traceback.print_exc()
            return None
    
    def _copy_adjustments(self):
        """Copy current adjustments to clipboard for batch editing."""
        try:
            # Copy all adjustment values
            self.copied_adjustments = self.adjustments.copy()
            self.copied_filter_intensity = getattr(self, 'filter_intensity', 100)
            self.copied_preset = getattr(self, 'current_preset_adjustments', {}).copy()
            
            # Enable paste button
            if hasattr(self, 'paste_adj_btn'):
                self.paste_adj_btn.setEnabled(True)
            
            # Visual feedback
            from PySide6.QtWidgets import QMessageBox
            msg = f"Copied adjustments:\n"
            non_zero = {k: v for k, v in self.copied_adjustments.items() if v != 0}
            if non_zero:
                for key, val in non_zero.items():
                    msg += f"  {key.capitalize()}: {val:+d}\n"
            else:
                msg += "  (No adjustments set)\n"
            msg += f"\nFilter Intensity: {self.copied_filter_intensity}%"
            
            # Create temporary label to show feedback
            if hasattr(self, 'copy_adj_btn'):
                original_text = self.copy_adj_btn.text()
                self.copy_adj_btn.setText("✓ Copied!")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(1500, lambda: self.copy_adj_btn.setText(original_text) if hasattr(self, 'copy_adj_btn') else None)
            
            print(f"[Copy/Paste] ✓ Copied adjustments")
            print(msg)
            return True
        except Exception as e:
            import traceback
            print(f"[Copy/Paste] Error copying adjustments: {e}")
            traceback.print_exc()
            return False
    
    def _paste_adjustments(self):
        """Paste copied adjustments to current photo."""
        try:
            if not self.copied_adjustments:
                print("[Copy/Paste] Nothing to paste")
                return False
            
            # Apply copied adjustments
            for key, val in self.copied_adjustments.items():
                if key in self.adjustments:
                    self.adjustments[key] = val
                    # Update sliders and spinboxes
                    slider = getattr(self, f"slider_{key}", None)
                    spinbox = getattr(self, f"spinbox_{key}", None)
                    if slider:
                        slider.blockSignals(True)
                        slider.setValue(val)
                        slider.blockSignals(False)
                    if spinbox:
                        spinbox.blockSignals(True)
                        spinbox.setValue(val)
                        spinbox.blockSignals(False)
            
            # Apply copied filter intensity
            if self.copied_filter_intensity is not None:
                self.filter_intensity = self.copied_filter_intensity
                if hasattr(self, 'filter_intensity_slider'):
                    self.filter_intensity_slider.blockSignals(True)
                    self.filter_intensity_slider.setValue(self.copied_filter_intensity)
                    self.filter_intensity_slider.blockSignals(False)
                if hasattr(self, 'intensity_value_label'):
                    self.intensity_value_label.setText(f"{self.copied_filter_intensity}%")
            
            # Apply copied preset
            if self.copied_preset:
                self.current_preset_adjustments = self.copied_preset.copy()

            # Re-render with pasted adjustments - normal preview with histogram+history
            self._apply_adjustments(preview_quality='normal', update_histogram=True, push_history=True)

            # Visual feedback
            if hasattr(self, 'paste_adj_btn'):
                original_text = self.paste_adj_btn.text()
                self.paste_adj_btn.setText("✓ Pasted!")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(1500, lambda: self.paste_adj_btn.setText(original_text) if hasattr(self, 'paste_adj_btn') else None)
            
            print(f"[Copy/Paste] ✓ Pasted adjustments to current photo")
            return True
        except Exception as e:
            import traceback
            print(f"[Copy/Paste] Error pasting adjustments: {e}")
            traceback.print_exc()
            return False
    
    def _clear_edit_state(self):
        """Clear saved edit state for current image."""
        try:
            if not hasattr(self, 'media_path') or not self.media_path:
                return
            
            import hashlib
            path_hash = hashlib.md5(self.media_path.encode()).hexdigest()
            state_file = os.path.join(self.edit_states_dir, f"{path_hash}.json")
            
            if os.path.exists(state_file):
                os.remove(state_file)
                print(f"[EditState] Cleared edit state for {os.path.basename(self.media_path)}")
        except Exception as e:
            print(f"[EditState] Error clearing edit state: {e}")
    
    def _enter_edit_mode(self):
        """Switch to editor page and prepare non-destructive edit state."""
        try:
            # Check if current file is VIDEO
            if hasattr(self, 'media_path') and self.media_path:
                self.is_video_file = self._is_video_file(self.media_path)
                
                if self.is_video_file:
                    print(f"[Editor] VIDEO file detected: {os.path.basename(self.media_path)}")
                    
                    # Initialize trim points from existing player
                    self.video_trim_start = 0
                    duration = getattr(self, '_video_duration', 0)
                    self.video_trim_end = duration
                    self.video_rotation_angle = 0
                    
                    # Show trim/rotate controls in right-side tools panel
                    if not hasattr(self, 'video_tools_container'):
                        from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
                        self.video_tools_container = QWidget()
                        self.video_tools_container.setStyleSheet("background: rgba(0,0,0,0.6);")
                        self.video_tools_layout = QVBoxLayout(self.video_tools_container)
                        self.video_tools_layout.setContentsMargins(12, 12, 12, 12)
                        self.video_tools_layout.setSpacing(8)
                        header = QLabel("Video Tools")
                        header.setStyleSheet("color: white; font-size: 11pt; font-weight: bold;")
                        self.video_tools_layout.addWidget(header)
                        # Mount into right panel
                        if hasattr(self, 'editor_right_panel') and self.editor_right_panel.layout():
                            self.editor_right_panel.layout().addWidget(self.video_tools_container)
                        elif hasattr(self, 'editor_right_panel'):
                            from PySide6.QtWidgets import QVBoxLayout
                            rp_layout = QVBoxLayout(self.editor_right_panel)
                            rp_layout.setContentsMargins(12, 12, 12, 12)
                            rp_layout.setSpacing(8)
                            rp_layout.addWidget(self.video_tools_container)
                    
                    if not hasattr(self, 'video_trim_controls'):
                        self.video_trim_controls = self._create_video_trim_controls()
                        from PySide6.QtWidgets import QGroupBox, QVBoxLayout
                        trim_group = QGroupBox("Trim")
                        trim_group.setStyleSheet("QGroupBox { color: white; font-weight: bold; }")
                        trim_layout = QVBoxLayout(trim_group)
                        trim_layout.setContentsMargins(8, 8, 8, 8)
                        trim_layout.addWidget(self.video_trim_controls)
                        self.video_tools_layout.addWidget(trim_group)
                    
                    if not hasattr(self, 'video_rotate_controls'):
                        self.video_rotate_controls = self._create_video_rotate_controls()
                        from PySide6.QtWidgets import QGroupBox, QVBoxLayout
                        rotate_group = QGroupBox("Rotate / Output")
                        rotate_group.setStyleSheet("QGroupBox { color: white; font-weight: bold; }")
                        rotate_layout = QVBoxLayout(rotate_group)
                        rotate_layout.setContentsMargins(8, 8, 8, 8)
                        rotate_layout.addWidget(self.video_rotate_controls)
                        self.video_tools_layout.addWidget(rotate_group)
                    
                    # Show video controls, hide photo controls
                    self.video_tools_container.show()
                    if hasattr(self, 'video_trim_controls'):
                        self.video_trim_controls.show()
                    if hasattr(self, 'video_rotate_controls'):
                        self.video_rotate_controls.show()
                    self.crop_btn.hide()  # Hide crop for videos
                    
                    # Hide photo editing controls to prevent toolbar overflow
                    if hasattr(self, 'straighten_slider'):
                        self.straighten_slider.parent().hide()
                    if hasattr(self, 'rotate_left_btn'):
                        self.rotate_left_btn.hide()
                    if hasattr(self, 'rotate_right_btn'):
                        self.rotate_right_btn.hide()
                    for widget in ['aspect_original_btn', 'aspect_square_btn', 'aspect_169_btn',
                                   'aspect_43_btn', 'aspect_916_btn', 'aspect_free_btn']:
                        if hasattr(self, widget):
                            getattr(self, widget).hide()
                    
                    # Hide crop toolbar entirely for videos
                    if hasattr(self, 'crop_toolbar'):
                        self.crop_toolbar.hide()
                    
                    # Ensure bottom video controls are visible
                    if hasattr(self, 'bottom_toolbar'):
                        self.bottom_toolbar.show()
                    if hasattr(self, 'video_controls_widget'):
                        self.video_controls_widget.show()
                    self._show_toolbars() if hasattr(self, '_show_toolbars') else None
                    
                    # Make media area adapt so controls aren't hidden
                    if hasattr(self, 'scroll_area'):
                        try:
                            self._original_scroll_resizable = self.scroll_area.widgetResizable()
                        except Exception:
                            self._original_scroll_resizable = False
                        self.scroll_area.setWidgetResizable(True)
                    
                    # Update trim labels with current duration
                    if hasattr(self, 'trim_end_label'):
                        self.trim_end_label.setText(self._format_time(duration))
                    
                    # Hide photo adjustments groups in right panel for videos
                    for w in [getattr(self, "histogram_label", None), getattr(self, "light_toggle", None), getattr(self, "light_group_container", None), getattr(self, "color_toggle", None), getattr(self, "color_group_container", None), getattr(self, "raw_toggle", None), getattr(self, "raw_group_container", None), getattr(self, "filters_container", None)]:
                        (w.hide() if w else None)
                    print("[Editor] Video tools shown on right panel")

                    # Update trim labels with current duration
                    if hasattr(self, 'trim_end_label'):
                        self.trim_end_label.setText(self._format_time(duration))
                    
                    print("[Editor] Video trim/rotate controls shown")

                    # CRITICAL FIX: Reparent video widget to editor canvas for edit mode
                    # The editor page (page 1) has the crop_toolbar with video controls
                    # We need to move the video_widget from viewer page to editor page

                    if hasattr(self, 'video_widget') and self.video_widget:
                        # Remove from viewer page layout
                        if self.video_widget.parent():
                            current_layout = self.video_widget.parent().layout()
                            if current_layout:
                                current_layout.removeWidget(self.video_widget)

                        # Add to editor canvas
                        if hasattr(self, 'editor_canvas'):
                            # Create layout for editor canvas if not exists
                            if not self.editor_canvas.layout():
                                from PySide6.QtWidgets import QVBoxLayout
                                canvas_layout = QVBoxLayout(self.editor_canvas)
                                canvas_layout.setContentsMargins(0, 0, 0, 0)

                            # Add video widget to editor canvas
                            self.editor_canvas.layout().addWidget(self.video_widget)
                            self.video_widget.show()
                            if hasattr(self, '_fit_video_view'):
                                self._fit_video_view()
                            print("[Editor] ✓ Video widget reparented to editor canvas")

                            # FOCUS FIX: Set focus to video_graphics_view for wheel events
                            if hasattr(self, 'video_graphics_view') and self.video_graphics_view:
                                self.video_graphics_view.setFocus()

                    # Switch to editor page to show video controls
                    if hasattr(self, 'mode_stack'):
                        self.mode_stack.setCurrentIndex(1)
                        print("[Editor] ✓ Switched to editor page for video editing")

                    # Hide nav buttons during edit mode
                    if hasattr(self, 'prev_btn'):
                        self.prev_btn.hide()
                    if hasattr(self, 'next_btn'):
                        self.next_btn.hide()

                    print("[Editor] Video edit mode active (video on editor page)")
                    return  # Skip photo editing setup
                
                else:
                    # Hide video controls for non-video files
                    if hasattr(self, 'video_trim_controls'):
                        self.video_trim_controls.hide()
                    if hasattr(self, 'video_rotate_controls'):
                        self.video_rotate_controls.hide()
                    self.crop_btn.show()  # Show crop for photos
            
            # Check if current file is RAW (photos only)
            if hasattr(self, 'media_path') and self.media_path:
                self.is_raw_file = self._is_raw_file(self.media_path)
                if self.is_raw_file:
                    print(f"[Editor] RAW file detected: {os.path.basename(self.media_path)}")
                    # Show RAW controls
                    if hasattr(self, 'raw_toggle'):
                        self.raw_toggle.setVisible(True)
                    # Try to load RAW image
                    if self._check_rawpy_available():
                        self.raw_image = self._load_raw_image(self.media_path)
                        if self.raw_image:
                            # Process RAW to initial pixmap
                            raw_pixmap = self._process_raw_to_pixmap(self.raw_image, self.adjustments)
                            if raw_pixmap:
                                self.original_pixmap = raw_pixmap
                                print("[Editor] RAW image processed successfully")
                                # Show notification
                                self._show_raw_notification("RAW file loaded - use RAW Development controls")
                    else:
                        print("[Editor] rawpy not available - install with: pip install rawpy")
                        print("[Editor] Falling back to embedded JPEG preview")
                        self._show_raw_notification("RAW preview only - Install rawpy for full RAW editing", is_warning=True)
                else:
                    # Hide RAW controls for non-RAW files
                    if hasattr(self, 'raw_toggle'):
                        self.raw_toggle.setVisible(False)
                        self.raw_toggle.setChecked(False)
                        self.raw_group_container.setVisible(False)
            
            # Copy current original pixmap for editing if available
            if getattr(self, 'original_pixmap', None) and not self.original_pixmap.isNull():
                self._original_pixmap = self.original_pixmap
                self._edit_pixmap = self.original_pixmap.copy()

                # ============================================================
                # PERFORMANCE OPTIMIZATION: TWO-TIER Preview System
                # ============================================================
                # For SMOOTH real-time adjustments without lag/stutter:
                # - ULTRA-LOW (512px): Real-time drag, INSTANT feedback
                # - NORMAL (2048px): Slider release, high quality
                # - FULL (original): Export/save only

                PREVIEW_NORMAL_SIZE = 2048  # High quality preview (slider release)
                PREVIEW_ULTRALOW_SIZE = 512  # Ultra-low for real-time drag (SMOOTH!)

                orig_width = self._original_pixmap.width()
                orig_height = self._original_pixmap.height()
                max_dim = max(orig_width, orig_height)

                # Create NORMAL preview (2048px)
                if max_dim > PREVIEW_NORMAL_SIZE:
                    scale_factor = PREVIEW_NORMAL_SIZE / max_dim
                    preview_width = int(orig_width * scale_factor)
                    preview_height = int(orig_height * scale_factor)

                    self._preview_pixmap = self._original_pixmap.scaled(
                        preview_width, preview_height,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    self._using_preview = True
                    print(f"[Editor] Normal preview: {preview_width}x{preview_height}")
                else:
                    # Image small enough, use original
                    self._preview_pixmap = self._original_pixmap
                    self._using_preview = False
                    print(f"[Editor] Using original: {orig_width}x{orig_height}")

                # Create ULTRA-LOW preview (512px) for real-time drag
                if max_dim > PREVIEW_ULTRALOW_SIZE:
                    scale_factor_ultra = PREVIEW_ULTRALOW_SIZE / max_dim
                    ultra_width = int(orig_width * scale_factor_ultra)
                    ultra_height = int(orig_height * scale_factor_ultra)

                    self._preview_ultralow = self._original_pixmap.scaled(
                        ultra_width, ultra_height,
                        Qt.KeepAspectRatio, Qt.FastTransformation  # Use FAST for ultra-low
                    )
                    print(f"[Editor] ⚡ ULTRA-LOW preview: {ultra_width}x{ultra_height} (for smooth drag)")
                else:
                    # Image already small
                    self._preview_ultralow = self._preview_pixmap

                # Cache PIL conversions for performance
                self._preview_normal_pil = None  # Cached PIL version of normal preview
                self._preview_ultralow_pil = None  # Cached PIL version of ultra-low preview

                # Initialize edit history
                self.edit_history = [(self._edit_pixmap.copy(), self.adjustments.copy())]
                self.edit_history_index = 0
                self._update_undo_redo_buttons()
                # IMPORTANT: Reset editor zoom to match viewer zoom
                self.edit_zoom_level = self.zoom_level  # Inherit current zoom from viewer
                
                # AUTO-RESTORE: Load saved edit state if exists
                restored = self._load_edit_state()
                if restored:
                    print("[Editor] ✓ Restored previous edit state")
                else:
                    print("[Editor] Starting fresh (no saved state)")
                
                self._update_editor_canvas_pixmap()
                self._apply_editor_zoom()
            else:
                # Clear canvas if no image loaded yet
                self._original_pixmap = None
                self._edit_pixmap = None
                if hasattr(self, 'editor_canvas'):
                    self.editor_canvas.update()
            # Show editor page
            if hasattr(self, 'mode_stack'):
                self.mode_stack.setCurrentIndex(1)
            # Optionally hide overlay navigation in editor mode
            if hasattr(self, 'prev_btn'):
                self.prev_btn.hide()
            if hasattr(self, 'next_btn'):
                self.next_btn.hide()
            # Install event filter for editor zoom (CRITICAL)
            if hasattr(self, 'editor_canvas'):
                self.editor_canvas.installEventFilter(self)
                # Enable mouse tracking for wheel events
                self.editor_canvas.setMouseTracking(True)
                # CRITICAL: Set focus to canvas to receive wheel events
                self.editor_canvas.setFocus()
                print("[EDITOR] ========================================")
                print("[EDITOR] Event filter installed on editor canvas")
                print("[EDITOR] Canvas has focus for wheel events")
                print("[EDITOR] ")
                print("[EDITOR] KEYBOARD SHORTCUTS:")
                print("[EDITOR]   E - Enter/Exit Editor Mode")
                print("[EDITOR]   C - Toggle Crop Mode (in editor)")
                print("[EDITOR]   Ctrl + Z - Undo")
                print("[EDITOR]   Ctrl + Y - Redo")
                print("[EDITOR]   Ctrl + Mouse Wheel - Zoom In/Out")
                print("[EDITOR] ")
                print("[EDITOR] MOUSE CONTROLS:")
                print("[EDITOR]   - Hold Ctrl + Mouse Wheel to zoom in/out")
                print("[EDITOR]   - Drag crop handles (corners/edges) to resize")
                print("[EDITOR] ")
                print("[EDITOR] BUTTONS:")
                print("[EDITOR]   - Click '✂ Crop' button to toggle crop mode")
                print("[EDITOR]   - Adjust sliders on right panel")
                print("[EDITOR]   - Click '✔ Save' to apply, '✖ Cancel' to discard")
                print("[EDITOR] ========================================")
        except Exception as e:
            print(f"[EditMode] Error entering editor mode: {e}")

    def _save_edits(self):
        try:
            # Handle video edit mode - move video back to viewer page
            if getattr(self, 'is_video_file', False) and hasattr(self, 'video_widget') and self.video_widget:
                # Remove from editor canvas
                if self.video_widget.parent():
                    current_layout = self.video_widget.parent().layout()
                    if current_layout:
                        current_layout.removeWidget(self.video_widget)

                # Add back to viewer page media_container
                if hasattr(self, 'media_container'):
                    container_layout = self.media_container.layout()
                    if container_layout:
                        container_layout.addWidget(self.video_widget)
                        self.video_widget.show()
                        print("[Editor] ✓ Video widget moved back to viewer page")

                # Reparenting invalidates geometry — schedule refit
                self._video_fit_ready = False
                self._last_video_fit_sig = None
                self._fit_video_view()

                # Hide crop_toolbar when exiting video edit mode
                if hasattr(self, 'crop_toolbar'):
                    self.crop_toolbar.hide()

            # Handle photo edit mode
            if getattr(self, '_edit_pixmap', None) and not self._edit_pixmap.isNull():
                # AUTO-SAVE: Save current edit state before applying
                self._save_edit_state()

                self.original_pixmap = self._edit_pixmap
                if hasattr(self, 'image_label'):
                    self.image_label.setPixmap(self.original_pixmap)

            # Switch back to viewer page
            if hasattr(self, 'mode_stack'):
                self.mode_stack.setCurrentIndex(0)

            # Restore overlay navigation in viewer mode
            if hasattr(self, 'prev_btn'):
                self.prev_btn.show()
            if hasattr(self, 'next_btn'):
                self.next_btn.show()
            if hasattr(self, '_position_nav_buttons'):
                self._position_nav_buttons()

            print("[Editor] ✓ Edits saved and returned to viewer mode")
        except Exception as e:
            print(f"[EditMode] Error saving edits: {e}")

    def _cancel_edits(self):
        try:
            # Handle video edit mode - move video back to viewer page
            if getattr(self, 'is_video_file', False) and hasattr(self, 'video_widget') and self.video_widget:
                # Remove from editor canvas
                if self.video_widget.parent():
                    current_layout = self.video_widget.parent().layout()
                    if current_layout:
                        current_layout.removeWidget(self.video_widget)

                # Add back to viewer page media_container
                if hasattr(self, 'media_container'):
                    container_layout = self.media_container.layout()
                    if container_layout:
                        container_layout.addWidget(self.video_widget)
                        self.video_widget.show()
                        print("[Editor] ✓ Video widget moved back to viewer page")

                # Reparenting invalidates geometry — schedule refit
                self._video_fit_ready = False
                self._last_video_fit_sig = None
                self._fit_video_view()

                # Reset video edits
                self.video_trim_start = 0
                duration = getattr(self, '_video_duration', 0)
                self.video_trim_end = duration
                self.video_rotation_angle = 0

                # Clear trim markers
                if hasattr(self, 'seek_slider') and hasattr(self.seek_slider, 'clear_trim_markers'):
                    self.seek_slider.clear_trim_markers()

                # Reset rotation status label
                if hasattr(self, 'rotation_status_label'):
                    self.rotation_status_label.setText("Original")

                # Hide crop_toolbar when exiting video edit mode
                if hasattr(self, 'crop_toolbar'):
                    self.crop_toolbar.hide()

                print("[Editor] ✓ Video edits cancelled and reset")

            # Switch back to viewer page
            if hasattr(self, 'mode_stack'):
                self.mode_stack.setCurrentIndex(0)

            # Restore overlay navigation in viewer mode
            if hasattr(self, 'prev_btn'):
                self.prev_btn.show()
            if hasattr(self, 'next_btn'):
                self.next_btn.show()
            if hasattr(self, '_position_nav_buttons'):
                self._position_nav_buttons()

            print("[Editor] ✓ Edits cancelled and returned to viewer mode")
        except Exception as e:
            print(f"[EditMode] Error cancelling edits: {e}")

    def _on_adjustment_change(self, key: str, value: int):
        """DEPRECATED - use _on_slider_change instead."""
        self._on_slider_change(key, value)

    def _on_slider_pressed(self, key: str):
        """
        Handle slider press - start drag mode.

        ⚡ SMOOTH PREVIEW: Sets flag to use ultra-low resolution during drag
        """
        self._is_dragging_slider = True
        print(f"[Editor] ⚡ Drag started: {key} (ultra-low mode)")

    def _on_slider_change(self, key: str, value: int):
        """
        Handle slider value change during drag.

        ⚡ SMOOTH PREVIEW: Uses ultra-low resolution (512px) for INSTANT feedback
        """
        self.adjustments[key] = int(value)
        # Update corresponding spinbox
        spinbox = getattr(self, f"spinbox_{key}", None)
        if spinbox:
            spinbox.blockSignals(True)
            spinbox.setValue(value)
            spinbox.blockSignals(False)

        # ============================================================
        # SMOOTH PREVIEW: Immediate rendering with ultra-low resolution
        # ============================================================
        if self._is_dragging_slider:
            # During drag: Process IMMEDIATELY with ultra-low res (no debounce!)
            # This gives instant feedback without lag/stutter
            self._apply_adjustments(
                preview_quality='ultralow',  # 512px for smooth drag
                update_histogram=False,      # Skip expensive histogram
                push_history=False           # Skip history during drag
            )
        else:
            # Not dragging (spinbox change): Use debounced normal preview
            if hasattr(self, '_adjust_debounce_timer') and self._adjust_debounce_timer:
                self._adjust_debounce_timer.stop()
                self._adjust_debounce_timer.start()
            else:
                self._apply_adjustments(preview_quality='normal', update_histogram=False, push_history=False)

        # AUTO-SAVE: Debounced save of edit state (every 3 seconds after changes)
        if not hasattr(self, '_autosave_timer'):
            from PySide6.QtCore import QTimer
            self._autosave_timer = QTimer(self)
            self._autosave_timer.setSingleShot(True)
            self._autosave_timer.timeout.connect(self._save_edit_state)
        self._autosave_timer.stop()
        self._autosave_timer.start(3000)  # 3 second debounce

    def _on_spinbox_change(self, key: str, value: int):
        """Handle spinbox value change, update slider."""
        self.adjustments[key] = int(value)
        # Update corresponding slider
        slider = getattr(self, f"slider_{key}", None)
        if slider:
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        # Trigger debounced render
        if hasattr(self, '_adjust_debounce_timer') and self._adjust_debounce_timer:
            self._adjust_debounce_timer.stop()
            self._adjust_debounce_timer.start()
        else:
            self._apply_adjustments(preview_quality='normal', update_histogram=False, push_history=False)

    def _on_slider_released(self, key: str):
        """
        Handle slider release - apply final quality with histogram and history.

        ⚡ SMOOTH PREVIEW: Exit drag mode, use normal preview (2048px)
        - During drag: Ultra-low (512px) - INSTANT, smooth
        - On release: Normal (2048px) - High quality + histogram + history
        """
        # Exit drag mode
        self._is_dragging_slider = False

        # Stop debounce timer - no need for another preview
        if hasattr(self, '_adjust_debounce_timer') and self._adjust_debounce_timer:
            self._adjust_debounce_timer.stop()

        # Apply with normal preview + histogram + history
        self._apply_adjustments(
            preview_quality='normal',  # 2048px for high quality
            update_histogram=True,     # Update histogram
            push_history=True          # Push to history for undo/redo
        )
        print(f"[Editor] ✓ Slider released: {key} = {self.adjustments.get(key, 0)} (normal preview)")

    def _reset_adjustments(self):
        """Reset all adjustments to 0."""
        for k in self.adjustments:
            self.adjustments[k] = 0
        # Reset sliders and spinboxes
        for key in ['brightness', 'exposure', 'contrast', 'highlights', 'shadows', 'vignette', 'sharpen', 'saturation', 'warmth',
                    'white_balance_temp', 'white_balance_tint', 'exposure_recovery', 'lens_correction', 'chromatic_aberration']:
            slider = getattr(self, f"slider_{key}", None)
            spinbox = getattr(self, f"spinbox_{key}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
            if spinbox:
                spinbox.blockSignals(True)
                spinbox.setValue(0)
                spinbox.blockSignals(False)
        # Reset is a complete operation - use normal preview with histogram+history
        self._apply_adjustments(preview_quality='normal', update_histogram=True, push_history=True)

    def _apply_adjustments(self, preview_quality='normal', update_histogram=False, push_history=False):
        """
        Apply adjustments to edit pixmap with TWO-TIER preview system.

        ⚡ SMOOTH PREVIEW SYSTEM (Best Practice from Lightroom/Photoshop):
        - preview_quality='ultralow': 512px, INSTANT feedback during drag (16ms target)
        - preview_quality='normal': 2048px, high quality on release
        - preview_quality='full': Original resolution, export/save only

        Args:
            preview_quality: 'ultralow' (drag), 'normal' (release), 'full' (export)
            update_histogram: Update histogram (expensive, skip during drag)
            push_history: Push to edit history (skip during drag)
        """
        try:
            # RAW FILE HANDLING: If RAW adjustments changed, reprocess from RAW
            if getattr(self, 'is_raw_file', False) and getattr(self, 'raw_image', None):
                raw_adj_keys = ['white_balance_temp', 'white_balance_tint', 'exposure_recovery']
                raw_adj_changed = any(self.adjustments.get(k, 0) != 0 for k in raw_adj_keys)

                if raw_adj_changed:
                    print("[RAW] Reprocessing RAW image with adjustments...")
                    # Reprocess RAW with current adjustments
                    raw_pixmap = self._process_raw_to_pixmap(self.raw_image, self.adjustments)
                    if raw_pixmap:
                        self._original_pixmap = raw_pixmap
                        # Also update preview if using it
                        if use_preview and self._using_preview:
                            PREVIEW_MAX_SIZE = 2048
                            orig_width = self._original_pixmap.width()
                            orig_height = self._original_pixmap.height()
                            max_dim = max(orig_width, orig_height)
                            if max_dim > PREVIEW_MAX_SIZE:
                                scale_factor = PREVIEW_MAX_SIZE / max_dim
                                preview_width = int(orig_width * scale_factor)
                                preview_height = int(orig_height * scale_factor)
                                self._preview_pixmap = self._original_pixmap.scaled(
                                    preview_width, preview_height,
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                                )
                        print("[RAW] RAW reprocessed successfully")

            # ============================================================
            # STEP 1: Select source pixmap and cached PIL based on quality
            # ============================================================
            source_pixmap = None
            cached_pil = None

            if preview_quality == 'ultralow':
                # ULTRA-LOW preview for smooth drag (512px)
                if hasattr(self, '_preview_ultralow') and not self._preview_ultralow.isNull():
                    source_pixmap = self._preview_ultralow
                    cached_pil = self._preview_ultralow_pil
                else:
                    # Fallback to normal preview
                    preview_quality = 'normal'

            if preview_quality == 'normal':
                # NORMAL preview for high quality (2048px)
                if hasattr(self, '_preview_pixmap') and not self._preview_pixmap.isNull():
                    source_pixmap = self._preview_pixmap
                    cached_pil = self._preview_normal_pil
                else:
                    # Fallback to full
                    preview_quality = 'full'

            if preview_quality == 'full':
                # FULL resolution for export/save
                if hasattr(self, '_original_pixmap') and not self._original_pixmap.isNull():
                    source_pixmap = self._original_pixmap
                    cached_pil = None  # Don't cache full resolution
                else:
                    return

            if not source_pixmap or source_pixmap.isNull():
                return

            # ============================================================
            # STEP 2: Convert QPixmap -> PIL (use cache if available)
            # ============================================================
            if cached_pil is not None:
                # Use cached PIL image (avoids expensive conversion!)
                pil_img = cached_pil.copy()
                print(f"[Editor] ⚡ Using cached PIL ({preview_quality})")
            else:
                # Convert QPixmap -> PIL
                pil_img = self._qpixmap_to_pil(source_pixmap)

                # Cache PIL conversion for future use (only for preview modes)
                if preview_quality == 'ultralow':
                    self._preview_ultralow_pil = pil_img.copy()
                elif preview_quality == 'normal':
                    self._preview_normal_pil = pil_img.copy()

            from PIL import ImageEnhance, Image, ImageDraw
            # Brightness (mid-tone)
            b = self.adjustments.get('brightness', 0)
            if b != 0:
                pil_img = ImageEnhance.Brightness(pil_img).enhance(1.0 + (b / 100.0))
            # Exposure (stops)
            e = self.adjustments.get('exposure', 0)
            if e != 0:
                expo_factor = pow(2.0, e / 100.0)
                pil_img = ImageEnhance.Brightness(pil_img).enhance(expo_factor)
            # Contrast
            c = self.adjustments.get('contrast', 0)
            if c != 0:
                pil_img = ImageEnhance.Contrast(pil_img).enhance(1.0 + (c / 100.0))
            # Highlights (compress bright tones)
            h = self.adjustments.get('highlights', 0)
            if h != 0:
                factor = (h / 100.0) * 0.6
                lut = []
                for x in range(256):
                    if x > 128:
                        if factor >= 0:
                            nx = 255 - int((255 - x) * (1.0 - factor))
                        else:
                            nx = 255 - int((255 - x) * (1.0 + abs(factor)))
                    else:
                        nx = x
                    lut.append(max(0, min(255, nx)))
                pil_img = pil_img.point(lut * len(pil_img.getbands()))
            # Shadows (lift/darken dark tones)
            s = self.adjustments.get('shadows', 0)
            if s != 0:
                factor = (s / 100.0) * 0.6
                lut = []
                for x in range(256):
                    if x < 128:
                        if factor >= 0:
                            nx = int(x + (128 - x) * factor)
                        else:
                            nx = int(x - x * abs(factor))
                    else:
                        nx = x
                    lut.append(max(0, min(255, nx)))
                pil_img = pil_img.point(lut * len(pil_img.getbands()))
            # Saturation
            sat = self.adjustments.get('saturation', 0)
            if sat != 0:
                sat_factor = max(0.0, 1.0 + (sat / 100.0))
                pil_img = ImageEnhance.Color(pil_img).enhance(sat_factor)
            # Warmth (temperature)
            w = self.adjustments.get('warmth', 0)
            if w != 0:
                w_factor = w / 200.0
                r, g, bch = pil_img.split()
                from PIL import ImageEnhance as IE
                r = IE.Brightness(r).enhance(1.0 + w_factor)
                bch = IE.Brightness(bch).enhance(1.0 - w_factor)
                pil_img = Image.merge('RGB', (r, g, bch))
                # slight saturation coupling with warmth
                sat_couple = 1.0 + (abs(w) / 100.0) * 0.1
                pil_img = ImageEnhance.Color(pil_img).enhance(sat_couple)
            # Vignette (darken/lighten edges)
            v = self.adjustments.get('vignette', 0)
            if v != 0:
                width, height = pil_img.size
                margin = int(min(width, height) * 0.1)
                mask = Image.new('L', (width, height), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((margin, margin, width - margin, height - margin), fill=255)
                # invert mask to target outside area
                mask = Image.eval(mask, lambda px: 255 - px)
                try:
                    from PIL import ImageFilter
                    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, min(width, height) // 50)))
                except Exception:
                    pass
                alpha = int(min(255, max(0, abs(v) * 2.0)))
                mask = mask.point(lambda x: int(x * (alpha / 255.0)))
                if v > 0:
                    dark = Image.new('RGB', (width, height), (0, 0, 0))
                    pil_img = Image.composite(dark, pil_img, mask)
                else:
                    light = Image.new('RGB', (width, height), (255, 255, 255))
                    pil_img = Image.composite(light, pil_img, mask)
            
            # Sharpen/Clarity (enhance edge details)
            shp = self.adjustments.get('sharpen', 0)
            if shp != 0:
                from PIL import ImageFilter
                # Positive values = sharpen, Negative values = blur (smooth)
                if shp > 0:
                    # Sharpen: Use UnsharpMask for professional results
                    # Map 0-100 to radius 0.5-3.0, percent 50-200, threshold 0-3
                    intensity = shp / 100.0
                    radius = 0.5 + (intensity * 2.5)  # 0.5 to 3.0
                    percent = 50 + (intensity * 150)  # 50 to 200
                    threshold = int(intensity * 3)     # 0 to 3
                    pil_img = pil_img.filter(ImageFilter.UnsharpMask(
                        radius=radius,
                        percent=int(percent),
                        threshold=threshold
                    ))
                else:
                    # Negative sharpen = Blur/Smooth
                    # Map -100 to -1 -> blur radius 5.0 to 0.5
                    intensity = abs(shp) / 100.0
                    blur_radius = 0.5 + (intensity * 4.5)  # 0.5 to 5.0
                    pil_img = pil_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            
            # Convert back to QPixmap
            self._edit_pixmap = self._pil_to_qpixmap(pil_img)
            self._update_editor_canvas_pixmap()
            self._apply_editor_zoom()

            # ============================================================
            # PERFORMANCE: Conditional expensive operations
            # ============================================================
            # Only push to history if requested (not during real-time slider moves)
            if push_history:
                self._push_edit_history()

            # Only update histogram if requested (expensive, skip during real-time)
            if update_histogram and hasattr(self, 'histogram_label'):
                hist_img = self._render_histogram_image(pil_img, width=360, height=120)
                self.histogram_label.setPixmap(self._pil_to_qpixmap(hist_img))

            # Log performance mode (throttled to avoid spam)
            if not hasattr(self, '_last_perf_log'):
                self._last_perf_log = {}
            import time
            current_time = time.time()
            last_log_time = self._last_perf_log.get(preview_quality, 0)

            if current_time - last_log_time > 2.0:
                quality_labels = {
                    'ultralow': '⚡ ULTRA-LOW (drag)',
                    'normal': '📊 NORMAL (release)',
                    'full': '🎯 FULL (export)'
                }
                label = quality_labels.get(preview_quality, preview_quality)
                print(f"[Editor] {label}: {pil_img.width}x{pil_img.height}")
                self._last_perf_log[preview_quality] = current_time

        except Exception as e:
            print(f"[Adjustments] Error applying adjustments: {e}")
            import traceback
            traceback.print_exc()

    def _qpixmap_to_pil(self, pixmap: QPixmap):
        """Robust conversion QPixmap -> PIL.Image using PNG buffer."""
        from PySide6.QtCore import QBuffer, QIODevice
        import io
        from PIL import Image
        buffer = QBuffer()
        buffer.open(QIODevice.ReadWrite)
        pixmap.save(buffer, 'PNG')
        data = bytes(buffer.data())
        buffer.close()
        return Image.open(io.BytesIO(data)).convert('RGB')

    def _pil_to_qpixmap(self, img):
        """Convert PIL.Image -> QPixmap using bytes buffer."""
        import io
        from PySide6.QtGui import QPixmap
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        qpix = QPixmap()
        qpix.loadFromData(buffer.read())
        return qpix

    def _to_pixmap(self, image):
        """Normalize worker outputs to QPixmap.

        Workers may emit QImage (preferred for cross-thread safety) or QPixmap.
        This helper converts supported inputs to a QPixmap, returning a null pixmap on failure.
        """
        try:
            from PySide6.QtGui import QPixmap, QImage
        except Exception:
            return None

        if image is None:
            return QPixmap()

        # Already a pixmap
        if isinstance(image, QPixmap):
            return image

        # QImage from worker thread
        if isinstance(image, QImage):
            if image.isNull():
                return QPixmap()
            return QPixmap.fromImage(image)

        # PIL Image
        try:
            from PIL import Image as PILImage  # type: ignore
            if isinstance(image, PILImage.Image):
                return self._pil_to_qpixmap(image)
        except Exception:
            pass

        # Unknown type
        return QPixmap()

    def _render_histogram_image(self, img, width=360, height=120):
        """Render an RGB histogram image using Pillow and return PIL.Image (smoothed, with clipping markers)."""
        from PIL import Image, ImageDraw
        if img.mode != 'RGB':
            img = img.convert('RGB')
        hist = img.histogram()
        r = hist[0:256]
        g = hist[256:512]
        b = hist[512:768]
        # Smoothing (moving average window=4)
        def smooth(arr, w=4):
            out = []
            for i in range(256):
                s = 0
                c = 0
                for k in range(-w, w+1):
                    j = min(255, max(0, i+k))
                    s += arr[j]
                    c += 1
                out.append(s / c)
            return out
        r_s = smooth(r)
        g_s = smooth(g)
        b_s = smooth(b)
        max_val = max(max(r_s), max(g_s), max(b_s)) or 1
        canvas = Image.new('RGB', (width, height), (40, 40, 40))
        draw = ImageDraw.Draw(canvas)
        def draw_channel(vals, color):
            scaled = [v / max_val for v in vals]
            for i in range(255):
                x1 = int(i * (width / 256.0))
                x2 = int((i + 1) * (width / 256.0))
                y1 = height - int(scaled[i] * height)
                y2 = height - int(scaled[i+1] * height)
                draw.line([(x1, y1), (x2, y2)], fill=color, width=1)
        draw_channel(r_s, (255, 0, 0))
        draw_channel(g_s, (0, 255, 0))
        draw_channel(b_s, (0, 0, 255))
        # Clipping markers
        clip_thresh = max_val * 0.05
        if r[0] > clip_thresh or g[0] > clip_thresh or b[0] > clip_thresh:
            draw.rectangle([(0, 0), (4, height)], fill=(255, 0, 0))
        if r[255] > clip_thresh or g[255] > clip_thresh or b[255] > clip_thresh:
            draw.rectangle([(width-4, 0), (width, height)], fill=(255, 0, 0))
        return canvas

    # === Editor crop, filters, and comparison helpers ===

    def _create_edit_canvas(self):
        from PySide6.QtWidgets import QWidget
        from PySide6.QtCore import Qt, QPoint
        class _EditCanvas(QWidget):
            def __init__(self, parent):
                super().__init__(parent)
                self.parent = parent
                self.setStyleSheet("background: #000;")
                self.setMinimumSize(200, 200)
                self.setMouseTracking(True)
                # CRITICAL: Enable focus to receive wheel events
                self.setFocusPolicy(Qt.WheelFocus)
                self.setFocus()
                # Crop drag state
                self._crop_dragging = False
                self._crop_handle = None  # 'TL','TR','BL','BR','L','R','T','B','move'
                self._drag_start_pos = None
                self._crop_start_rect = None
                
                # PROFESSIONAL FIX: Consistent sizing for smooth preview transitions
                self._consistent_pixmap = None  # Stores consistently scaled pixmap
                self._consistent_size = None    # Reference size for consistency

            def set_pixmap_consistent(self, pixmap):
                """
                Set pixmap with consistent sizing to eliminate zoom effects during preview transitions.

                ✨ PROFESSIONAL APPROACH (Like Lightroom/Photoshop):
                - Store consistently scaled pixmap
                - Maintain aspect ratio during quality transitions
                - Eliminate jarring zoom-in/out effects
                """
                if not pixmap or pixmap.isNull():
                    self._consistent_pixmap = None
                    self._consistent_size = None
                    self.update()
                    return

                # Store the consistently scaled pixmap
                self._consistent_pixmap = pixmap
                self._consistent_size = pixmap.size()

                # Trigger repaint with consistent sizing
                self.update()

                # Debug logging
                if hasattr(self.parent, '_debug_size_consistency') and self.parent._debug_size_consistency:
                    print(f"[EditCanvas] Set consistent pixmap: {pixmap.width()}x{pixmap.height()}")

            def clear(self):
                """Clear the canvas - reset pixmap and trigger repaint."""
                self._consistent_pixmap = None
                self._consistent_size = None
                self.update()

            def wheelEvent(self, event):
                """Handle wheel events for zoom - DIRECT implementation."""
                try:
                    from PySide6.QtCore import Qt
                    # Check if Ctrl is pressed
                    if event.modifiers() & Qt.ControlModifier:
                        delta = event.angleDelta().y()
                        print(f"[EditCanvas] Ctrl+Wheel detected: delta={delta}")
                        if delta > 0:
                            self.parent._editor_zoom_in()
                            print(f"[EditCanvas] Zoomed IN to {self.parent.edit_zoom_level:.2f}x")
                        else:
                            self.parent._editor_zoom_out()
                            print(f"[EditCanvas] Zoomed OUT to {self.parent.edit_zoom_level:.2f}x")
                        event.accept()  # Consume the event
                    else:
                        print("[EditCanvas] Wheel without Ctrl - passing to parent")
                        event.ignore()  # Let parent handle scrolling
                except Exception as e:
                    import traceback
                    print(f"[EditCanvas] wheelEvent error: {e}")
                    traceback.print_exc()
                    event.ignore()

            def paintEvent(self, ev):
                from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QTransform
                from PySide6.QtCore import QRect, QRectF
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing)
                
                # ============================================================
                # PROFESSIONAL FIX: Use consistent pixmap first to eliminate zoom effects
                # ============================================================
                # Check for consistently scaled pixmap (from quality transitions)
                if hasattr(self, '_consistent_pixmap') and self._consistent_pixmap and not self._consistent_pixmap.isNull():
                    pix_to_draw = self._consistent_pixmap
                    if hasattr(self.parent, '_debug_size_consistency') and self.parent._debug_size_consistency:
                        print(f"[EditCanvas] Using consistent pixmap: {pix_to_draw.width()}x{pix_to_draw.height()}")
                else:
                    # Fall back to normal pixmap selection
                    pix_to_draw = None
                    if getattr(self.parent, 'before_after_active', False) and getattr(self.parent, '_original_pixmap', None):
                        pix_to_draw = self.parent._original_pixmap
                    elif getattr(self.parent, '_edit_pixmap', None):
                        pix_to_draw = self.parent._edit_pixmap
                
                # FAST ROTATION: Use Qt QTransform instead of PIL (GPU accelerated!)
                if getattr(self.parent, 'crop_mode_active', False) and hasattr(self.parent, 'rotation_angle') and self.parent.rotation_angle != 0 and pix_to_draw:
                    # Create rotation transform
                    transform = QTransform()
                    transform.rotate(-self.parent.rotation_angle)  # Negative for counterclockwise
                    # Apply transform with smooth rendering
                    pix_to_draw = pix_to_draw.transformed(transform, Qt.SmoothTransformation)
                
                # Draw centered scaled pixmap
                if pix_to_draw and not pix_to_draw.isNull():
                    w = max(1, int(pix_to_draw.width() * self.parent.edit_zoom_level))
                    h = max(1, int(pix_to_draw.height() * self.parent.edit_zoom_level))
                    scaled = pix_to_draw.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    x = (self.width() - scaled.width()) // 2
                    y = (self.height() - scaled.height()) // 2
                    p.drawPixmap(x, y, scaled)
                    # Crop overlay
                    if getattr(self.parent, 'crop_mode_active', False) and getattr(self.parent, '_crop_rect_norm', None):
                        nx, ny, nw, nh = self.parent._crop_rect_norm
                        rx = x + int(nx * scaled.width())
                        ry = y + int(ny * scaled.height())
                        rw = int(nw * scaled.width())
                        rh = int(nh * scaled.height())
                        rect = QRect(rx, ry, rw, rh)
                        
                        # GOOGLE PHOTOS STYLE: Darken outside with stronger overlay
                        from PySide6.QtGui import QPainterPath
                        full = QPainterPath()
                        full.addRect(self.rect())
                        crop = QPainterPath()
                        crop.addRect(rect)
                        outside = full.subtracted(crop)
                        p.fillPath(outside, QColor(0, 0, 0, 180))  # Darker overlay
                        
                        # GOOGLE PHOTOS STYLE: White border with shadow
                        p.setPen(QPen(QColor(255, 255, 255, 255), 3))  # Thicker white border
                        p.drawRect(rect)
                        
                        # GOOGLE PHOTOS STYLE: Rule of thirds grid (thinner, semi-transparent)
                        p.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.SolidLine))  # Subtle solid lines
                        x1 = rect.left() + rect.width() // 3
                        x2 = rect.left() + 2 * rect.width() // 3
                        y1 = rect.top() + rect.height() // 3
                        y2 = rect.top() + 2 * rect.height() // 3
                        p.drawLine(x1, rect.top(), x1, rect.bottom())
                        p.drawLine(x2, rect.top(), x2, rect.bottom())
                        p.drawLine(rect.left(), y1, rect.right(), y1)
                        p.drawLine(rect.left(), y2, rect.right(), y2)
                        
                        # GOOGLE PHOTOS STYLE: Corner handles (larger, with border)
                        p.setBrush(QBrush(QColor(255, 255, 255, 255)))
                        p.setPen(QPen(QColor(66, 133, 244), 2))  # Blue border
                        handle_size = 10
                        corner_length = 20  # L-shaped corner handles
                        
                        # Draw L-shaped corners (Google Photos style)
                        p.setPen(QPen(QColor(255, 255, 255, 255), 3))
                        # Top-left corner
                        p.drawLine(rect.left(), rect.top(), rect.left() + corner_length, rect.top())
                        p.drawLine(rect.left(), rect.top(), rect.left(), rect.top() + corner_length)
                        # Top-right corner
                        p.drawLine(rect.right(), rect.top(), rect.right() - corner_length, rect.top())
                        p.drawLine(rect.right(), rect.top(), rect.right(), rect.top() + corner_length)
                        # Bottom-left corner
                        p.drawLine(rect.left(), rect.bottom(), rect.left() + corner_length, rect.bottom())
                        p.drawLine(rect.left(), rect.bottom(), rect.left(), rect.bottom() - corner_length)
                        # Bottom-right corner
                        p.drawLine(rect.right(), rect.bottom(), rect.right() - corner_length, rect.bottom())
                        p.drawLine(rect.right(), rect.bottom(), rect.right(), rect.bottom() - corner_length)
                        
                        # Edge handles (small circles)
                        p.setBrush(QBrush(QColor(255, 255, 255, 255)))
                        p.setPen(QPen(QColor(66, 133, 244), 2))
                        handle_r = 5
                        for hx, hy in [(rect.center().x(), rect.top()), (rect.center().x(), rect.bottom()),
                                       (rect.left(), rect.center().y()), (rect.right(), rect.center().y())]:
                            p.drawEllipse(QPoint(hx, hy), handle_r, handle_r)
                p.end()

            def mousePressEvent(self, ev):
                from PySide6.QtCore import Qt, QRect
                if not getattr(self.parent, 'crop_mode_active', False):
                    return
                if ev.button() != Qt.LeftButton:
                    return
                # CRITICAL: Check if _crop_rect_norm exists before accessing
                if not hasattr(self.parent, '_crop_rect_norm') or self.parent._crop_rect_norm is None:
                    return
                # Find handle or move
                pix = getattr(self.parent, '_edit_pixmap', None) or getattr(self.parent, '_original_pixmap', None)
                if not pix or pix.isNull():
                    return
                w = max(1, int(pix.width() * self.parent.edit_zoom_level))
                h = max(1, int(pix.height() * self.parent.edit_zoom_level))
                x_off = (self.width() - w) // 2
                y_off = (self.height() - h) // 2
                nx, ny, nw, nh = self.parent._crop_rect_norm
                rx = x_off + int(nx * w)
                ry = y_off + int(ny * h)
                rw = int(nw * w)
                rh = int(nh * h)
                rect = QRect(rx, ry, rw, rh)
                mx = ev.pos().x()
                my = ev.pos().y()
                handle_r = 15  # Increased handle size for easier grabbing
                # Check corners/edges
                if abs(mx - rect.left()) < handle_r and abs(my - rect.top()) < handle_r:
                    self._crop_handle = 'TL'
                elif abs(mx - rect.right()) < handle_r and abs(my - rect.top()) < handle_r:
                    self._crop_handle = 'TR'
                elif abs(mx - rect.left()) < handle_r and abs(my - rect.bottom()) < handle_r:
                    self._crop_handle = 'BL'
                elif abs(mx - rect.right()) < handle_r and abs(my - rect.bottom()) < handle_r:
                    self._crop_handle = 'BR'
                elif abs(my - rect.top()) < handle_r and rect.left() < mx < rect.right():
                    self._crop_handle = 'T'
                elif abs(my - rect.bottom()) < handle_r and rect.left() < mx < rect.right():
                    self._crop_handle = 'B'
                elif abs(mx - rect.left()) < handle_r and rect.top() < my < rect.bottom():
                    self._crop_handle = 'L'
                elif abs(mx - rect.right()) < handle_r and rect.top() < my < rect.bottom():
                    self._crop_handle = 'R'
                elif rect.contains(ev.pos()):
                    self._crop_handle = 'move'
                else:
                    self._crop_handle = None
                if self._crop_handle:
                    self._crop_dragging = True
                    self._drag_start_pos = ev.pos()
                    self._crop_start_rect = (nx, ny, nw, nh)

            def mouseMoveEvent(self, ev):
                from PySide6.QtCore import Qt, QRect
                if not self._crop_dragging or not self._drag_start_pos:
                    # Cursor feedback
                    if getattr(self.parent, 'crop_mode_active', False):
                        # CRITICAL: Check if _crop_rect_norm exists before accessing
                        if not hasattr(self.parent, '_crop_rect_norm') or self.parent._crop_rect_norm is None:
                            self.setCursor(Qt.ArrowCursor)
                            return
                        pix = getattr(self.parent, '_edit_pixmap', None) or getattr(self.parent, '_original_pixmap', None)
                        if pix and not pix.isNull():
                            w = max(1, int(pix.width() * self.parent.edit_zoom_level))
                            h = max(1, int(pix.height() * self.parent.edit_zoom_level))
                            x_off = (self.width() - w) // 2
                            y_off = (self.height() - h) // 2
                            nx, ny, nw, nh = self.parent._crop_rect_norm
                            rx = x_off + int(nx * w)
                            ry = y_off + int(ny * h)
                            rw = int(nw * w)
                            rh = int(nh * h)
                            rect = QRect(rx, ry, rw, rh)
                            mx = ev.pos().x()
                            my = ev.pos().y()
                            hr = 15  # Increased handle size for easier grabbing
                            # Corner handles - highest priority
                            if (abs(mx-rect.left())<hr and abs(my-rect.top())<hr):
                                self.setCursor(Qt.SizeFDiagCursor)
                            elif (abs(mx-rect.right())<hr and abs(my-rect.top())<hr):
                                self.setCursor(Qt.SizeBDiagCursor)
                            elif (abs(mx-rect.left())<hr and abs(my-rect.bottom())<hr):
                                self.setCursor(Qt.SizeBDiagCursor)
                            elif (abs(mx-rect.right())<hr and abs(my-rect.bottom())<hr):
                                self.setCursor(Qt.SizeFDiagCursor)
                            # Edge handles
                            elif abs(my-rect.top())<hr:
                                self.setCursor(Qt.SizeVerCursor)
                            elif abs(my-rect.bottom())<hr:
                                self.setCursor(Qt.SizeVerCursor)
                            elif abs(mx-rect.left())<hr:
                                self.setCursor(Qt.SizeHorCursor)
                            elif abs(mx-rect.right())<hr:
                                self.setCursor(Qt.SizeHorCursor)
                            # Move handle (inside rect)
                            elif rect.contains(ev.pos()):
                                self.setCursor(Qt.SizeAllCursor)
                            else:
                                self.setCursor(Qt.ArrowCursor)
                    return
                
                # Compute delta in normalized coords
                pix = getattr(self.parent, '_edit_pixmap', None) or getattr(self.parent, '_original_pixmap', None)
                if not pix or pix.isNull():
                    return
                w = max(1, int(pix.width() * self.parent.edit_zoom_level))
                h = max(1, int(pix.height() * self.parent.edit_zoom_level))
                dx_pix = ev.pos().x() - self._drag_start_pos.x()
                dy_pix = ev.pos().y() - self._drag_start_pos.y()
                dx_norm = dx_pix / w
                dy_norm = dy_pix / h
                nx, ny, nw, nh = self._crop_start_rect
                
                # Get current aspect ratio constraint
                aspect_locked = self.parent._get_active_aspect_ratio()
                
                # Apply delta based on handle
                if self._crop_handle == 'move':
                    nx += dx_norm
                    ny += dy_norm
                    nx = max(0, min(1.0 - nw, nx))
                    ny = max(0, min(1.0 - nh, ny))
                elif self._crop_handle in ['TL', 'TR', 'BL', 'BR', 'T', 'B', 'L', 'R']:
                    # Resize handles
                    if self._crop_handle == 'TL':
                        nx += dx_norm; ny += dy_norm; nw -= dx_norm; nh -= dy_norm
                    elif self._crop_handle == 'TR':
                        ny += dy_norm; nw += dx_norm; nh -= dy_norm
                    elif self._crop_handle == 'BL':
                        nx += dx_norm; nw -= dx_norm; nh += dy_norm
                    elif self._crop_handle == 'BR':
                        nw += dx_norm; nh += dy_norm
                    elif self._crop_handle == 'T':
                        ny += dy_norm; nh -= dy_norm
                    elif self._crop_handle == 'B':
                        nh += dy_norm
                    elif self._crop_handle == 'L':
                        nx += dx_norm; nw -= dx_norm
                    elif self._crop_handle == 'R':
                        nw += dx_norm
                    
                    # Apply aspect ratio constraint if locked
                    if aspect_locked:
                        target_ratio = aspect_locked
                        current_ratio = nw / max(1e-6, nh)
                        if abs(current_ratio - target_ratio) > 0.01:  # Need adjustment
                            # Adjust based on which dimension changed more
                            if self._crop_handle in ['TL', 'TR', 'BL', 'BR']:
                                # Corner - adjust height to match width
                                nh = nw / target_ratio
                            elif self._crop_handle in ['L', 'R']:
                                # Width changed - adjust height
                                nh = nw / target_ratio
                            elif self._crop_handle in ['T', 'B']:
                                # Height changed - adjust width
                                nw = nh * target_ratio
                
                # Clamp to valid range
                nw = max(0.05, min(1.0, nw))
                nh = max(0.05, min(1.0, nh))
                nx = max(0.0, min(1.0 - nw, nx))
                ny = max(0.0, min(1.0 - nh, ny))
                
                self.parent._crop_rect_norm = (nx, ny, nw, nh)
                self.update()

            def mouseReleaseEvent(self, ev):
                if ev.button() == Qt.LeftButton:
                    self._crop_dragging = False
                    self._crop_handle = None
                    self._drag_start_pos = None
                    self.setCursor(Qt.ArrowCursor)
        return _EditCanvas(self)

    def _build_crop_toolbar(self):
        from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel
        bar = QWidget()
        bar.setStyleSheet("""
            QWidget {
                background: rgba(30, 30, 30, 0.95);
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        
        # Straighten slider (LEFT SIDE - primary control)
        straighten_container = QWidget()
        straighten_layout = QHBoxLayout(straighten_container)
        straighten_layout.setContentsMargins(0, 0, 0, 0)
        straighten_layout.setSpacing(8)
        
        straighten_icon = QLabel("↻")
        straighten_icon.setStyleSheet("color: white; font-weight: bold;")
        self._register_caption_btn(straighten_icon, "nav")  # Use nav size for icon
        straighten_layout.addWidget(straighten_icon)

        straighten_lbl = QLabel("Straighten:")
        straighten_lbl.setStyleSheet("color: rgba(255,255,255,0.9);")
        self._register_caption_btn(straighten_lbl, "secondary")
        straighten_layout.addWidget(straighten_lbl)
        
        self.straighten_slider = QSlider(Qt.Horizontal)
        self.straighten_slider.setRange(-1800, 1800)  # -180° to +180° with 0.1° precision
        self.straighten_slider.setValue(0)
        self.straighten_slider.setFixedWidth(200)
        self.straighten_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255, 255, 255, 0.2);
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: rgba(66, 133, 244, 1.0);
                border: 2px solid white;
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: rgba(66, 133, 244, 1.0);
                width: 18px;
                height: 18px;
                margin: -7px 0;
                border-radius: 9px;
            }
        """)
        # Use timer for smooth rotation (debounced)
        self.straighten_slider.valueChanged.connect(self._on_straighten_slider_change)
        straighten_layout.addWidget(self.straighten_slider)
        
        self.straighten_label = QLabel("0.0°")
        self.straighten_label.setStyleSheet("""
            color: white;
            min-width: 50px;
            padding: 4px 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        """)
        self.straighten_label.setAlignment(Qt.AlignCenter)
        self._register_caption_btn(self.straighten_label, "secondary")
        straighten_layout.addWidget(self.straighten_label)
        
        lay.addWidget(straighten_container)
        lay.addSpacing(20)
        
        # 90° Rotation buttons (LEFT-MIDDLE) - No hardcoded font-size (uses typography system)
        rotate_label = QLabel("Rotate:")
        rotate_label.setStyleSheet("color: rgba(255,255,255,0.7);")
        self._register_caption_btn(rotate_label, "label")
        lay.addWidget(rotate_label)

        rotate_left_btn = QPushButton("↶ 90°")
        rotate_left_btn.setToolTip("Rotate 90° counter-clockwise")
        rotate_left_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
            QPushButton:pressed {
                background: rgba(66, 133, 244, 0.6);
            }
        """)
        rotate_left_btn.clicked.connect(self._rotate_90_left)
        self._register_caption_btn(rotate_left_btn, "small")
        lay.addWidget(rotate_left_btn)

        rotate_right_btn = QPushButton("↷ 90°")
        rotate_right_btn.setToolTip("Rotate 90° clockwise")
        rotate_right_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
            QPushButton:pressed {
                background: rgba(66, 133, 244, 0.6);
            }
        """)
        rotate_right_btn.clicked.connect(self._rotate_90_right)
        self._register_caption_btn(rotate_right_btn, "small")
        lay.addWidget(rotate_right_btn)
        
        lay.addSpacing(20)
        
        # Flip buttons (MIDDLE) - No hardcoded font-size (uses typography system)
        flip_label = QLabel("Flip:")
        flip_label.setStyleSheet("color: rgba(255,255,255,0.7);")
        self._register_caption_btn(flip_label, "label")
        lay.addWidget(flip_label)

        flip_h_btn = QPushButton("↔ Horizontal")
        flip_h_btn.setToolTip("Flip horizontally (mirror)")
        flip_h_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
            QPushButton:pressed {
                background: rgba(66, 133, 244, 0.6);
            }
        """)
        flip_h_btn.clicked.connect(self._flip_horizontal)
        self._register_caption_btn(flip_h_btn, "small")
        lay.addWidget(flip_h_btn)

        flip_v_btn = QPushButton("↕ Vertical")
        flip_v_btn.setToolTip("Flip vertically")
        flip_v_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.2);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
            QPushButton:pressed {
                background: rgba(66, 133, 244, 0.6);
            }
        """)
        flip_v_btn.clicked.connect(self._flip_vertical)
        self._register_caption_btn(flip_v_btn, "small")
        lay.addWidget(flip_v_btn)
        
        lay.addSpacing(20)
        
        # Aspect ratio presets (MIDDLE) - No hardcoded font-size (uses typography system)
        aspect_label = QLabel("Aspect:")
        aspect_label.setStyleSheet("color: rgba(255,255,255,0.7);")
        self._register_caption_btn(aspect_label, "label")
        lay.addWidget(aspect_label)

        # Create button group for exclusive selection
        self.aspect_button_group = []

        for label, ratio in [("Free", "free"), ("1:1", (1,1)), ("4:3", (4,3)), ("16:9", (16,9)), ("Original", None)]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 0.1);
                    color: rgba(255, 255, 255, 0.8);
                    border: 1px solid rgba(255, 255, 255, 0.2);
                    border-radius: 4px;
                    padding: 6px 12px;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.15);
                    color: white;
                }
                QPushButton:checked {
                    background: rgba(66, 133, 244, 0.8);
                    color: white;
                    border: 1px solid rgba(66, 133, 244, 1.0);
                }
            """)
            # Connect with lambda that unchecks other buttons
            btn.clicked.connect(lambda checked, r=ratio, b=btn: self._on_aspect_preset_clicked(r, b))
            self._register_caption_btn(btn, "small")
            lay.addWidget(btn)
            self.aspect_button_group.append(btn)

            # Check 'Free' by default
            if label == "Free":
                btn.setChecked(True)
        
        lay.addStretch()
        
        # Apply/Cancel buttons (RIGHT SIDE) - No hardcoded font-size (uses typography system)
        apply_btn = QPushButton("✓ Apply Crop")
        apply_btn.setStyleSheet("""
            QPushButton {
                background: rgba(34, 139, 34, 0.9);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background: rgba(34, 139, 34, 1.0);
            }
        """)
        apply_btn.clicked.connect(self._apply_crop)
        self._register_caption_btn(apply_btn, "secondary")
        lay.addWidget(apply_btn)

        cancel_btn = QPushButton("✕ Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 6px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
            }
        """)
        cancel_btn.clicked.connect(self._cancel_crop)
        self._register_caption_btn(cancel_btn, "secondary")
        lay.addWidget(cancel_btn)
        
        return bar

    def _rotate_90_left(self):
        """Rotate image 90° counter-clockwise (left)."""
        try:
            if not getattr(self, '_edit_pixmap', None) or self._edit_pixmap.isNull():
                return
            
            from PySide6.QtGui import QTransform
            
            # Create 90° counter-clockwise rotation transform
            transform = QTransform()
            transform.rotate(-90)  # Negative = counter-clockwise
            
            # Apply to edit pixmap
            self._edit_pixmap = self._edit_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Also apply to original if in crop mode
            if hasattr(self, '_original_pixmap') and self._original_pixmap:
                self._original_pixmap = self._original_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Update display
            self._update_editor_canvas_pixmap()
            self._apply_editor_zoom()
            self._push_edit_history()
            
            print("[Editor] Rotated 90° counter-clockwise")
        except Exception as e:
            import traceback
            print(f"[Editor] Error rotating left: {e}")
            traceback.print_exc()
    
    def _rotate_90_right(self):
        """Rotate image 90° clockwise (right)."""
        try:
            if not getattr(self, '_edit_pixmap', None) or self._edit_pixmap.isNull():
                return
            
            from PySide6.QtGui import QTransform
            
            # Create 90° clockwise rotation transform
            transform = QTransform()
            transform.rotate(90)  # Positive = clockwise
            
            # Apply to edit pixmap
            self._edit_pixmap = self._edit_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Also apply to original if in crop mode
            if hasattr(self, '_original_pixmap') and self._original_pixmap:
                self._original_pixmap = self._original_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Update display
            self._update_editor_canvas_pixmap()
            self._apply_editor_zoom()
            self._push_edit_history()
            
            print("[Editor] Rotated 90° clockwise")
        except Exception as e:
            import traceback
            print(f"[Editor] Error rotating right: {e}")
            traceback.print_exc()
    
    def _flip_horizontal(self):
        """Flip image horizontally (mirror)."""
        try:
            if not getattr(self, '_edit_pixmap', None) or self._edit_pixmap.isNull():
                return
            
            from PySide6.QtGui import QTransform
            
            # Create horizontal flip transform
            transform = QTransform()
            transform.scale(-1, 1)  # Mirror on X axis
            
            # Apply to edit pixmap
            self._edit_pixmap = self._edit_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Also apply to original if in crop mode
            if hasattr(self, '_original_pixmap') and self._original_pixmap:
                self._original_pixmap = self._original_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Update display
            self._update_editor_canvas_pixmap()
            self._apply_editor_zoom()
            self._push_edit_history()
            
            print("[Editor] Flipped horizontally")
        except Exception as e:
            import traceback
            print(f"[Editor] Error flipping horizontal: {e}")
            traceback.print_exc()
    
    def _flip_vertical(self):
        """Flip image vertically."""
        try:
            if not getattr(self, '_edit_pixmap', None) or self._edit_pixmap.isNull():
                return
            
            from PySide6.QtGui import QTransform
            
            # Create vertical flip transform
            transform = QTransform()
            transform.scale(1, -1)  # Mirror on Y axis
            
            # Apply to edit pixmap
            self._edit_pixmap = self._edit_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Also apply to original if in crop mode
            if hasattr(self, '_original_pixmap') and self._original_pixmap:
                self._original_pixmap = self._original_pixmap.transformed(transform, Qt.SmoothTransformation)
            
            # Update display
            self._update_editor_canvas_pixmap()
            self._apply_editor_zoom()
            self._push_edit_history()
            
            print("[Editor] Flipped vertically")
        except Exception as e:
            import traceback
            print(f"[Editor] Error flipping vertical: {e}")
            traceback.print_exc()
    
    def _get_active_aspect_ratio(self):
        """Get the currently active aspect ratio (or None if freeform)."""
        if not hasattr(self, 'aspect_button_group'):
            return None
        
        # Find which button is checked
        aspect_map = {
            "Free": None,
            "1:1": 1.0,
            "4:3": 4.0/3.0,
            "16:9": 16.0/9.0,
            "Original": None  # Will be calculated from image
        }
        
        for btn in self.aspect_button_group:
            if btn.isChecked():
                label = btn.text()
                if label == "Original":
                    # Calculate from current image
                    pix = getattr(self, '_edit_pixmap', None) or getattr(self, 'original_pixmap', None)
                    if pix and not pix.isNull():
                        return pix.width() / max(1, pix.height())
                return aspect_map.get(label)
        
        return None  # Freeform by default
    
    def _on_aspect_preset_clicked(self, ratio, clicked_btn):
        """Handle aspect ratio preset button click - ensure only one is checked."""
        # Uncheck all other buttons
        if hasattr(self, 'aspect_button_group'):
            for btn in self.aspect_button_group:
                if btn != clicked_btn:
                    btn.setChecked(False)
        
        # Apply the selected aspect ratio
        self._set_crop_aspect(ratio)
    
    def _on_straighten_slider_change(self, value):
        """Handle straighten slider - instant update with Qt QTransform (no lag!)."""
        # Convert to degrees with 0.1 precision
        degrees = value / 10.0
        self.straighten_label.setText(f"{degrees:.1f}°")
        self.rotation_angle = degrees
        
        # INSTANT UPDATE: Qt QTransform is so fast we don't need debouncing!
        if hasattr(self, 'editor_canvas'):
            self.editor_canvas.update()
    
    def _apply_rotation_preview(self):
        """Apply rotation preview (called after debounce)."""
        if hasattr(self, 'editor_canvas'):
            self.editor_canvas.update()

    def _on_straighten_changed(self, value):
        """DEPRECATED - use _on_straighten_slider_change instead."""
        self._on_straighten_slider_change(value)

    def _toggle_crop_mode(self):
        """Toggle crop mode in EDITOR (not viewer)."""
        self.crop_mode_active = not getattr(self, 'crop_mode_active', False)
        print(f"[EDITOR] Crop mode toggled: {'ON' if self.crop_mode_active else 'OFF'}")
        if self.crop_mode_active:
            # Init normalized crop rect centered (80% of image)
            self._crop_rect_norm = (0.1, 0.1, 0.8, 0.8)
            if hasattr(self, 'crop_toolbar'):
                self.crop_toolbar.show()  # SHOW crop toolbar
                print("[EDITOR] ✓ Crop toolbar SHOWN")
            else:
                print("[EDITOR] ✗ crop_toolbar not found!")
        else:
            if hasattr(self, 'crop_toolbar'):
                self.crop_toolbar.hide()
                print("[EDITOR] ✓ Crop toolbar HIDDEN")
            self._crop_rect_norm = None
        # Refresh canvas
        if hasattr(self, 'editor_canvas'):
            self.editor_canvas.update()
            print("[EDITOR] Canvas updated")
        # Update toggle state
        if hasattr(self, 'crop_btn'):
            self.crop_btn.setChecked(self.crop_mode_active)
            print(f"[EDITOR] Crop button checked: {self.crop_mode_active}")

    def _editor_undo(self):
        try:
            if self.edit_history_index > 0:
                self.edit_history_index -= 1
                pixmap, adj_dict = self.edit_history[self.edit_history_index]
                self._edit_pixmap = pixmap.copy()
                self.adjustments = adj_dict.copy()
                # Update sliders
                for key, val in adj_dict.items():
                    slider = getattr(self, f"slider_{key}", None)
                    label = getattr(self, f"{key}_label", None)
                    if slider:
                        slider.setValue(val)
                    if label:
                        label.setText(f"{key.capitalize()}: {val}")
                self._update_editor_canvas_pixmap()
                self._apply_editor_zoom()
                self._update_undo_redo_buttons()
        except Exception as e:
            print(f"[Undo] Error: {e}")

    def _editor_redo(self):
        try:
            if self.edit_history_index < len(self.edit_history) - 1:
                self.edit_history_index += 1
                pixmap, adj_dict = self.edit_history[self.edit_history_index]
                self._edit_pixmap = pixmap.copy()
                self.adjustments = adj_dict.copy()
                # Update sliders
                for key, val in adj_dict.items():
                    slider = getattr(self, f"slider_{key}", None)
                    label = getattr(self, f"{key}_label", None)
                    if slider:
                        slider.setValue(val)
                    if label:
                        label.setText(f"{key.capitalize()}: {val}")
                self._update_editor_canvas_pixmap()
                self._apply_editor_zoom()
                self._update_undo_redo_buttons()
        except Exception as e:
            print(f"[Redo] Error: {e}")

    def _push_edit_history(self):
        try:
            if not getattr(self, '_edit_pixmap', None):
                return
            # Truncate forward history if we're in the middle
            if self.edit_history_index < len(self.edit_history) - 1:
                self.edit_history = self.edit_history[:self.edit_history_index + 1]
            # Add current state
            self.edit_history.append((self._edit_pixmap.copy(), self.adjustments.copy()))
            # Limit history size
            if len(self.edit_history) > self.max_history:
                self.edit_history.pop(0)
            else:
                self.edit_history_index += 1
            self._update_undo_redo_buttons()
        except Exception as e:
            print(f"[History] Push error: {e}")

    def _update_undo_redo_buttons(self):
        try:
            if hasattr(self, 'undo_btn'):
                self.undo_btn.setEnabled(self.edit_history_index > 0)
            if hasattr(self, 'redo_btn'):
                self.redo_btn.setEnabled(self.edit_history_index < len(self.edit_history) - 1)
        except Exception:
            pass

    def _export_current_media(self):
        """Export current media (photo or video) based on file type."""
        try:
            # Check if current file is video
            if getattr(self, 'is_video_file', False):
                # Export video with trim/rotate
                self._export_edited_video()
            else:
                # Export photo with adjustments
                self._export_edited_image()
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export Error", f"Error exporting media:\n{e}")
    
    def _export_edited_image(self):
        try:
            if not getattr(self, '_edit_pixmap', None) or self._edit_pixmap.isNull():
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Export Error", "No edited image to export.")
                return
            from PySide6.QtWidgets import QFileDialog, QMessageBox
            import os
            # Suggest filename
            original_path = getattr(self, 'media_path', '')
            if original_path:
                base, ext = os.path.splitext(os.path.basename(original_path))
                suggested = f"{base}_edited{ext}"
            else:
                suggested = "edited_image.jpg"
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Edited Image",
                suggested,
                "Images (*.jpg *.jpeg *.png *.tiff *.bmp);;All Files (*.*)"
            )
            if file_path:
                success = self._edit_pixmap.save(file_path, quality=95)
                if success:
                    QMessageBox.information(self, "Export Success", f"Image exported to:\n{file_path}")
                else:
                    QMessageBox.warning(self, "Export Error", "Failed to save image.")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export Error", f"Error exporting image:\n{e}")

    def keyPressEvent(self, event):
        try:
            from PySide6.QtCore import Qt

            # Video editing shortcuts (only when video is loaded and in edit mode)
            is_video_loaded = hasattr(self, 'video_player') and self.video_player is not None
            in_edit_mode = hasattr(self, 'mode_stack') and self.mode_stack.currentIndex() == 1

            if is_video_loaded and in_edit_mode:
                # I key: Set trim IN point (start)
                if event.key() == Qt.Key_I:
                    if hasattr(self, '_set_trim_start'):
                        self._set_trim_start()
                        print("[MediaLightbox] Keyboard: Set trim start (I key)")
                    return

                # O key: Set trim OUT point (end)
                elif event.key() == Qt.Key_O:
                    if hasattr(self, '_set_trim_end'):
                        self._set_trim_end()
                        print("[MediaLightbox] Keyboard: Set trim end (O key)")
                    return

                # J key: Rewind
                elif event.key() == Qt.Key_J:
                    current_pos = self.video_player.position()
                    new_pos = max(0, current_pos - 5000)  # Rewind 5 seconds
                    self.video_player.setPosition(new_pos)
                    print(f"[MediaLightbox] Keyboard: Rewind to {new_pos}ms (J key)")
                    return

                # K key: Play/Pause toggle
                elif event.key() == Qt.Key_K:
                    if self.video_player.playbackState() == QMediaPlayer.PlayingState:
                        self.video_player.pause()
                        print("[MediaLightbox] Keyboard: Pause (K key)")
                    else:
                        self.video_player.play()
                        print("[MediaLightbox] Keyboard: Play (K key)")
                    return

                # L key: Fast forward
                elif event.key() == Qt.Key_L:
                    current_pos = self.video_player.position()
                    duration = getattr(self, '_video_duration', 0)
                    new_pos = min(duration, current_pos + 5000)  # Forward 5 seconds
                    self.video_player.setPosition(new_pos)
                    print(f"[MediaLightbox] Keyboard: Fast forward to {new_pos}ms (L key)")
                    return

                # Left arrow: Previous frame
                elif event.key() == Qt.Key_Left:
                    current_pos = self.video_player.position()
                    frame_ms = 1000 / 30  # Assume 30 fps (~33ms per frame)
                    new_pos = max(0, current_pos - frame_ms)
                    self.video_player.setPosition(int(new_pos))
                    print(f"[MediaLightbox] Keyboard: Previous frame (← key)")
                    return

                # Right arrow: Next frame
                elif event.key() == Qt.Key_Right:
                    current_pos = self.video_player.position()
                    duration = getattr(self, '_video_duration', 0)
                    frame_ms = 1000 / 30  # Assume 30 fps (~33ms per frame)
                    new_pos = min(duration, current_pos + frame_ms)
                    self.video_player.setPosition(int(new_pos))
                    print(f"[MediaLightbox] Keyboard: Next frame (→ key)")
                    return

                # M key: Toggle mute
                elif event.key() == Qt.Key_M:
                    if hasattr(self, '_on_mute_clicked'):
                        self._on_mute_clicked()
                        print("[MediaLightbox] Keyboard: Toggle mute (M key)")
                    return

                # S key: Cycle playback speed
                elif event.key() == Qt.Key_S:
                    if hasattr(self, '_on_speed_clicked'):
                        self._on_speed_clicked()
                        print("[MediaLightbox] Keyboard: Cycle speed (S key)")
                    return

            # M key for mute also works outside edit mode for video
            if is_video_loaded and event.key() == Qt.Key_M:
                if hasattr(self, '_on_mute_clicked'):
                    self._on_mute_clicked()
                    print("[MediaLightbox] Keyboard: Toggle mute (M key)")
                return

            # S key: Toggle slideshow (when not in video edit mode)
            if event.key() == Qt.Key_S and not (is_video_loaded and in_edit_mode):
                self._toggle_slideshow()
                print("[MediaLightbox] Keyboard: Toggle slideshow (S key)")
                return

            # Undo/Redo shortcuts (for photo editing)
            if event.modifiers() & Qt.ControlModifier:
                if event.key() == Qt.Key_Z:
                    self._editor_undo()
                    return
                elif event.key() == Qt.Key_Y:
                    self._editor_redo()
                    return

            # Pass to parent
            super().keyPressEvent(event)
        except Exception as e:
            print(f"[MediaLightbox] Error in keyPressEvent: {e}")
            super().keyPressEvent(event)

    def _set_crop_aspect(self, ratio):
        """Adjust normalized crop rect to selected aspect ratio, maintaining zoom."""
        if not getattr(self, '_crop_rect_norm', None):
            return
        
        nx, ny, nw, nh = self._crop_rect_norm
        
        if ratio == "free":
            # Free form - no constraint
            print("[Crop] Aspect: Freeform (no constraint)")
            return
        
        try:
            # Get current displayed image size (after rotation and zoom)
            pix = getattr(self, '_edit_pixmap', None) or getattr(self, 'original_pixmap', None)
            if not pix or pix.isNull():
                return
            
            # If rotated, account for rotation
            if hasattr(self, 'rotation_angle') and self.rotation_angle != 0:
                from PySide6.QtGui import QTransform
                transform = QTransform()
                transform.rotate(-self.rotation_angle)
                pix = pix.transformed(transform, Qt.SmoothTransformation)
            
            img_w = pix.width()
            img_h = pix.height()
            
            # Calculate target aspect ratio
            if ratio is None:
                # Original aspect ratio of the image
                target_ratio = img_w / max(1, img_h)
                print(f"[Crop] Aspect: Original ({img_w}x{img_h} = {target_ratio:.2f})")
            else:
                # Specified aspect ratio (e.g., 16:9, 4:3, 1:1)
                target_ratio = ratio[0] / ratio[1]
                print(f"[Crop] Aspect: {ratio[0]}:{ratio[1]} = {target_ratio:.2f}")
            
            # Get current crop rectangle dimensions
            current_ratio = nw / max(1e-6, nh)
            
            # Adjust crop to match target ratio while keeping it centered
            if current_ratio > target_ratio:
                # Current crop is too wide - reduce width
                new_w = nh * target_ratio
                new_h = nh
            else:
                # Current crop is too tall - reduce height
                new_w = nw
                new_h = nw / target_ratio
            
            # Ensure new dimensions don't exceed image bounds
            new_w = min(new_w, 1.0)
            new_h = min(new_h, 1.0)
            
            # Center the new crop rectangle
            cx = nx + nw / 2  # Current center X
            cy = ny + nh / 2  # Current center Y
            
            new_nx = cx - new_w / 2
            new_ny = cy - new_h / 2
            
            # Clamp to image bounds
            new_nx = max(0.0, min(1.0 - new_w, new_nx))
            new_ny = max(0.0, min(1.0 - new_h, new_ny))
            
            self._crop_rect_norm = (new_nx, new_ny, new_w, new_h)
            
            if hasattr(self, 'editor_canvas'):
                self.editor_canvas.update()
            
            print(f"[Crop] Adjusted crop: ({new_nx:.2f}, {new_ny:.2f}, {new_w:.2f}, {new_h:.2f})")
            
        except Exception as e:
            import traceback
            print(f"[Crop] Error setting aspect: {e}")
            traceback.print_exc()

    def _apply_crop(self):
        try:
            if not getattr(self, '_crop_rect_norm', None) or not getattr(self, '_original_pixmap', None):
                return
            
            # Start with the base pixmap
            base_pixmap = self._original_pixmap
            
            # FAST ROTATION: Apply straighten rotation using Qt QTransform (GPU accelerated)
            if hasattr(self, 'rotation_angle') and self.rotation_angle != 0:
                from PySide6.QtGui import QTransform
                transform = QTransform()
                transform.rotate(-self.rotation_angle)
                base_pixmap = base_pixmap.transformed(transform, Qt.SmoothTransformation)
                print(f"[Crop] Applied {self.rotation_angle}° rotation using QTransform")
            
            # Convert to PIL for cropping
            from PIL import Image
            pil_img = self._qpixmap_to_pil(base_pixmap)
            w, h = pil_img.size
            
            # Calculate crop rectangle
            nx, ny, nw, nh = self._crop_rect_norm
            x = int(nx * w); y = int(ny * h); cw = int(nw * w); ch = int(nh * h)
            
            # Apply crop
            cropped = pil_img.crop((x, y, x+cw, y+ch))
            
            # Convert back to QPixmap
            self._edit_pixmap = self._pil_to_qpixmap(cropped)
            
            # Reset crop state
            self._crop_rect_norm = None
            self.crop_mode_active = False
            self.rotation_angle = 0
            if hasattr(self, 'crop_toolbar'):
                self.crop_toolbar.hide()
            if hasattr(self, 'straighten_slider'):
                self.straighten_slider.setValue(0)
            
            # Update display
            self._update_editor_canvas_pixmap()
            self._apply_editor_zoom()
            self._push_edit_history()
            
            print(f"[Crop] Successfully cropped to {cw}x{ch}")
        except Exception as e:
            import traceback
            print(f"[Crop] Error applying crop: {e}")
            traceback.print_exc()

    def _cancel_crop(self):
        self.crop_mode_active = False
        self._crop_rect_norm = None
        if hasattr(self, 'crop_toolbar'):
            self.crop_toolbar.hide()
        if hasattr(self, 'editor_canvas'):
            self.editor_canvas.update()
        if hasattr(self, 'crop_btn'):
            self.crop_btn.setChecked(False)

    def _build_filters_panel(self):
        from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout, QPushButton, QGridLayout, QLabel, QSlider, QHBoxLayout
        from PySide6.QtGui import QPixmap, QPainter
        from PySide6.QtCore import Qt
        scroll = QScrollArea()
        container = QWidget()
        layout = QVBoxLayout(container)
        
        # Filter Intensity Control (at top)
        intensity_container = QWidget()
        intensity_container.setStyleSheet("""
            QWidget {
                background: rgba(40, 40, 40, 0.8);
                border-radius: 6px;
                padding: 8px;
            }
        """)
        intensity_layout = QVBoxLayout(intensity_container)
        intensity_layout.setContentsMargins(12, 8, 12, 8)
        intensity_layout.setSpacing(6)
        
        # Header row: label + value
        intensity_header = QHBoxLayout()
        intensity_label = QLabel("Filter Intensity")
        intensity_label.setStyleSheet("color: rgba(255,255,255,0.9); font-size: 10pt; font-weight: bold;")
        intensity_header.addWidget(intensity_label)
        intensity_header.addStretch()
        
        self.intensity_value_label = QLabel("100%")
        self.intensity_value_label.setStyleSheet("""
            color: white;
            font-size: 10pt;
            font-weight: bold;
            background: rgba(66, 133, 244, 0.3);
            border-radius: 4px;
            padding: 4px 8px;
        """)
        intensity_header.addWidget(self.intensity_value_label)
        intensity_layout.addLayout(intensity_header)
        
        # Intensity slider (0-100%)
        self.filter_intensity_slider = QSlider(Qt.Horizontal)
        self.filter_intensity_slider.setRange(0, 100)
        self.filter_intensity_slider.setValue(100)
        self.filter_intensity_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255, 255, 255, 0.2);
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: rgba(66, 133, 244, 1.0);
                border: 2px solid white;
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: rgba(66, 133, 244, 1.0);
                width: 20px;
                height: 20px;
                margin: -7px 0;
                border-radius: 10px;
            }
        """)
        self.filter_intensity_slider.valueChanged.connect(self._on_filter_intensity_change)
        intensity_layout.addWidget(self.filter_intensity_slider)
        
        # Helper text
        help_text = QLabel("Adjust the strength of the applied filter")
        help_text.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 8pt; font-style: italic;")
        help_text.setAlignment(Qt.AlignCenter)
        intensity_layout.addWidget(help_text)
        
        layout.addWidget(intensity_container)
        layout.addSpacing(12)
        
        # Filter presets grid
        presets = [
            ("Original", {}),
            ("Punch", {"contrast": 25, "saturation": 20}),
            ("Golden", {"warmth": 30, "saturation": 10}),
            ("Radiate", {"highlights": 20, "contrast": 15}),
            ("Warm Contrast", {"warmth": 20, "contrast": 15}),
            ("Calm", {"saturation": -10, "contrast": -5}),
            ("Cool Light", {"warmth": -15}),
            ("Vivid Cool", {"saturation": 30, "contrast": 20, "warmth": -10}),
            ("Dramatic Cool", {"contrast": 35, "saturation": 10, "warmth": -20}),
            ("B&W", {"saturation": -100}),
            ("B&W Cool", {"saturation": -100, "contrast": 20}),
            ("Film", {"contrast": 10, "saturation": -5, "vignette": 10}),
        ]
        grid = QGridLayout()
        for i, (name, adj) in enumerate(presets):
            # Container for thumbnail + label
            preset_widget = QWidget()
            preset_layout = QVBoxLayout(preset_widget)
            preset_layout.setContentsMargins(4, 4, 4, 4)
            preset_layout.setSpacing(4)
            # Thumbnail preview button
            btn = QPushButton()
            btn.setFixedSize(120, 90)
            btn.setStyleSheet("QPushButton { border: 2px solid rgba(255,255,255,0.3); border-radius: 4px; } QPushButton:hover { border: 2px solid rgba(66,133,244,0.8); }")
            btn.clicked.connect(lambda _, a=adj: self._apply_preset_adjustments(a))
            # Generate thumbnail preview (simple placeholder for now)
            thumb_pixmap = self._generate_preset_thumbnail(adj)
            btn.setIcon(QIcon(thumb_pixmap))
            btn.setIconSize(btn.size())
            preset_layout.addWidget(btn)
            # Label
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: white; font-size: 9pt;")
            preset_layout.addWidget(lbl)
            grid.addWidget(preset_widget, i // 2, i % 2)
        layout.addLayout(grid)
        scroll.setWidget(container)
        return scroll

    def _generate_preset_thumbnail(self, adj: dict):
        from PySide6.QtGui import QPixmap, QPainter, QLinearGradient, QColor
        from PySide6.QtCore import QRect, Qt
        # Try to generate real-time thumbnail from current image
        if getattr(self, '_original_pixmap', None) and not self._original_pixmap.isNull():
            try:
                # Use PIL to apply preset adjustments to a small thumbnail
                from PIL import Image, ImageEnhance as IE
                pil_img = self._qpixmap_to_pil(self._original_pixmap)
                # Resize to thumbnail size for performance
                pil_img.thumbnail((120, 90), Image.Resampling.LANCZOS)
                
                # Apply preset adjustments (simplified version)
                # Brightness
                b = adj.get('brightness', 0)
                if b != 0:
                    pil_img = IE.Brightness(pil_img).enhance(1.0 + b / 100.0)
                # Exposure
                e = adj.get('exposure', 0)
                if e != 0:
                    expo_factor = pow(2.0, e / 100.0)
                    pil_img = IE.Brightness(pil_img).enhance(expo_factor)
                # Contrast
                c = adj.get('contrast', 0)
                if c != 0:
                    pil_img = IE.Contrast(pil_img).enhance(1.0 + c / 100.0)
                # Saturation
                s = adj.get('saturation', 0)
                if s != 0:
                    pil_img = IE.Color(pil_img).enhance(1.0 + s / 100.0)
                # Warmth (simplified)
                w = adj.get('warmth', 0)
                if w != 0:
                    w_factor = w / 200.0
                    r, g, bch = pil_img.split()
                    r = IE.Brightness(r).enhance(1.0 + w_factor)
                    bch = IE.Brightness(bch).enhance(1.0 - w_factor)
                    pil_img = Image.merge('RGB', (r, g, bch))
                
                # Convert back to QPixmap
                thumb_pixmap = self._pil_to_qpixmap(pil_img)
                # Center crop to 120x90
                final_pix = QPixmap(120, 90)
                final_pix.fill(QColor(0, 0, 0))
                p = QPainter(final_pix)
                x_off = (120 - thumb_pixmap.width()) // 2
                y_off = (90 - thumb_pixmap.height()) // 2
                p.drawPixmap(x_off, y_off, thumb_pixmap)
                p.end()
                return final_pix
            except Exception as e:
                print(f"[PresetThumb] Error generating real-time thumbnail: {e}")
                # Fall back to gradient placeholder
        
        # Fallback: Generate a simple gradient thumbnail representing the preset
        pix = QPixmap(120, 90)
        pix.fill(QColor(60, 60, 60))
        p = QPainter(pix)
        # Base gradient
        grad = QLinearGradient(0, 0, 120, 90)
        # Color based on warmth
        warmth = adj.get('warmth', 0)
        sat = adj.get('saturation', 0)
        contrast = adj.get('contrast', 0)
        if warmth > 0:
            grad.setColorAt(0, QColor(255, 200, 150))
            grad.setColorAt(1, QColor(200, 150, 100))
        elif warmth < 0:
            grad.setColorAt(0, QColor(150, 200, 255))
            grad.setColorAt(1, QColor(100, 150, 200))
        elif sat == -100:
            grad.setColorAt(0, QColor(200, 200, 200))
            grad.setColorAt(1, QColor(80, 80, 80))
        else:
            grad.setColorAt(0, QColor(180, 180, 200))
            grad.setColorAt(1, QColor(100, 100, 120))
        p.fillRect(pix.rect(), grad)
        # Text overlay
        p.setPen(QColor(255, 255, 255, 180))
        p.drawText(pix.rect(), Qt.AlignCenter, "Preview")
        p.end()
        return pix

    def _on_filter_intensity_change(self, value):
        """Handle filter intensity slider change."""
        self.filter_intensity = value
        if hasattr(self, 'intensity_value_label'):
            self.intensity_value_label.setText(f"{value}%")
        
        # Reapply current preset with new intensity
        if hasattr(self, 'current_preset_adjustments') and self.current_preset_adjustments:
            self._apply_preset_with_intensity(self.current_preset_adjustments, value)
    
    def _apply_preset_with_intensity(self, preset_adj: dict, intensity: int):
        """Apply preset adjustments scaled by intensity (0-100%)."""
        # Reset all first
        for key in self.adjustments:
            self.adjustments[key] = 0
        
        # Apply preset values scaled by intensity
        intensity_factor = intensity / 100.0
        for key, val in preset_adj.items():
            scaled_val = int(val * intensity_factor)
            self.adjustments[key] = scaled_val
            slider = getattr(self, f"slider_{key}", None)
            spinbox = getattr(self, f"spinbox_{key}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(scaled_val)
                slider.blockSignals(False)
            if spinbox:
                spinbox.blockSignals(True)
                spinbox.setValue(scaled_val)
                spinbox.blockSignals(False)

        # Re-render - normal preview with histogram+history
        self._apply_adjustments(preview_quality='normal', update_histogram=True, push_history=True)
        print(f"[Filter] Applied preset with {intensity}% intensity")
    
    def _apply_preset_adjustments(self, preset_adj: dict):
        """Apply filter preset with current intensity."""
        # Store preset for intensity adjustments
        self.current_preset_adjustments = preset_adj.copy()
        
        # Apply with current intensity
        intensity = getattr(self, 'filter_intensity', 100)
        self._apply_preset_with_intensity(preset_adj, intensity)

    def _toggle_filters_panel(self):
        try:
            show = self.filters_btn.isChecked()
            if hasattr(self, 'filters_container'):
                self.filters_container.setVisible(show)
            # Hide groups when showing filters (UX parity)
            if hasattr(self, 'light_group_container'):
                self.light_group_container.setVisible(not show)
            if hasattr(self, 'color_group_container'):
                self.color_group_container.setVisible(not show)
        except Exception:
            pass

    def _toggle_before_after(self):
        self.before_after_active = getattr(self, 'before_after_active', False) ^ True
        self._update_editor_canvas_pixmap()
        self._apply_editor_zoom()
        if hasattr(self, 'before_after_btn'):
            self.before_after_btn.setChecked(self.before_after_active)

    def _update_editor_canvas_pixmap(self):
        """
        Update editor canvas pixmap with CONSISTENT SIZING to eliminate zoom effects.
        
        ✨ PROFESSIONAL FIX (Like Lightroom/Photoshop):
        - Scale all previews to consistent viewport size
        - Maintain aspect ratio during quality transitions
        - Eliminate jarring zoom-in/out effects
        """
        try:
            pix = None
            if getattr(self, 'before_after_active', False) and getattr(self, '_original_pixmap', None):
                pix = self._original_pixmap
            else:
                pix = getattr(self, '_edit_pixmap', None)
            
            if pix and not pix.isNull():
                # ============================================================
                # PROFESSIONAL FIX: Consistent Sizing During Quality Transitions
                # ============================================================
                # Store the target display size to maintain consistency
                if not hasattr(self, '_consistent_display_size') or self._consistent_display_size is None:
                    # First time: Store the size of the normal preview as reference
                    self._consistent_display_size = pix.size()
                    print(f"[Editor] Setting consistent display size: {pix.width()}x{pix.height()}")
                
                # Get canvas viewport size
                canvas_size = self.editor_canvas.size()
                
                # Calculate scale factors to maintain consistent appearance
                target_width = self._consistent_display_size.width()
                target_height = self._consistent_display_size.height()
                
                # Scale the pixmap to fit consistently within canvas
                scaled_pix = pix.scaled(
                    target_width, target_height,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation  # Always use smooth for quality
                )
                
                # Store the consistently scaled pixmap
                self._consistent_scaled_pixmap = scaled_pix
                
                # Update canvas with consistent sizing
                self.editor_canvas.set_pixmap_consistent(scaled_pix)
                
                # Debug logging for size consistency
                if hasattr(self, '_debug_size_consistency') and self._debug_size_consistency:
                    print(f"[Editor] Consistent sizing - Source: {pix.width()}x{pix.height()}, Display: {scaled_pix.width()}x{scaled_pix.height()}")
            else:
                self.editor_canvas.clear()
                # Clear consistent size reference when no pixmap
                if hasattr(self, '_consistent_display_size'):
                    self._consistent_display_size = None
        except Exception as e:
            print(f"[Editor] Error in _update_editor_canvas_pixmap: {e}")
            import traceback
            traceback.print_exc()

    def _create_video_controls(self) -> QWidget:
        """Create video playback controls (play/pause, seek, volume, time)."""
        controls = QWidget()
        controls.setStyleSheet("background: transparent;")
        controls.hide()  # Hidden by default, shown for videos

        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Play/Pause button
        self.play_pause_btn = QPushButton("▶")
        self.play_pause_btn.setFocusPolicy(Qt.NoFocus)
        self.play_pause_btn.setFixedSize(56, 56)
        self.play_pause_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 18px;
                font-size: 12pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        layout.addWidget(self.play_pause_btn)

        # Time label (current)
        self.time_current_label = QLabel("0:00")
        self.time_current_label.setStyleSheet("color: white; font-size: 9pt; background: transparent;")
        layout.addWidget(self.time_current_label)

        # Seek slider (custom with trim markers)
        self.seek_slider = TrimMarkerSlider(Qt.Horizontal)
        self.seek_slider.setFocusPolicy(Qt.NoFocus)
        self.seek_slider.setMouseTracking(True)  # PHASE B #3: Enable hover detection
        self.seek_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255, 255, 255, 0.2);
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: white;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: rgba(66, 133, 244, 0.8);
                border-radius: 2px;
            }
        """)
        self.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)

        # PHASE B #3: Install event filter for hover preview
        self.seek_slider.installEventFilter(self)

        layout.addWidget(self.seek_slider, 1)

        # Time label (total)
        self.time_total_label = QLabel("0:00")
        self.time_total_label.setStyleSheet("color: white; font-size: 9pt; background: transparent;")
        layout.addWidget(self.time_total_label)

        # Mute button (clickable volume icon)
        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFocusPolicy(Qt.NoFocus)
        self.mute_btn.setFixedSize(32, 32)
        self.mute_btn.setCursor(Qt.PointingHandCursor)
        self.mute_btn.setToolTip("Toggle Mute (M)")
        self.mute_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 12pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                border-radius: 4px;
            }
        """)
        self.mute_btn.clicked.connect(self._on_mute_clicked)
        layout.addWidget(self.mute_btn)

        # Volume slider
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFocusPolicy(Qt.NoFocus)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(80)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255, 255, 255, 0.2);
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: white;
                width: 10px;
                height: 10px;
                margin: -3px 0;
                border-radius: 5px;
            }
            QSlider::sub-page:horizontal {
                background: white;
                border-radius: 2px;
            }
        """)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        layout.addWidget(self.volume_slider)

        # Playback speed button
        self.speed_btn = QPushButton("1.0x")
        self.speed_btn.setFocusPolicy(Qt.NoFocus)
        self.speed_btn.setFixedHeight(32)
        self.speed_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        # Start at normal speed
        self.current_speed_index = 1  # 0.5x, 1.0x, 1.5x, 2.0x -> index 1 = 1.0x
        self.speed_btn.clicked.connect(self._on_speed_clicked)
        layout.addWidget(self.speed_btn)

        # Screenshot button
        self.screenshot_btn = QPushButton("📷")
        self.screenshot_btn.setFocusPolicy(Qt.NoFocus)
        self.screenshot_btn.setFixedHeight(32)
        self.screenshot_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        self.screenshot_btn.clicked.connect(self._on_screenshot_clicked)
        layout.addWidget(self.screenshot_btn)

        # Loop toggle button
        self.loop_enabled = False
        self.loop_btn = QPushButton("Loop Off")
        self.loop_btn.setFocusPolicy(Qt.NoFocus)
        self.loop_btn.setFixedHeight(32)
        self.loop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        self.loop_btn.clicked.connect(self._on_loop_clicked)
        layout.addWidget(self.loop_btn)

        return controls

    def _create_info_panel(self) -> QWidget:
        """Create toggleable info panel with tabbed metadata (on right side)."""
        panel = QWidget()
        # FIX D: Make panel adaptive (not fixed width) like Lightroom drawers
        # Cap relative to window width so it never dominates the canvas
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(max(240, int(self.width() * 0.30)))  # <= 30% of window
        panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        panel.setStyleSheet("""
            QWidget {
                background: rgba(32, 33, 36, 0.95);
                border-left: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)

        panel_layout = QVBoxLayout(panel)
        # Professional margins: Balanced for readability and space efficiency
        panel_layout.setContentsMargins(12, 16, 12, 16)
        panel_layout.setSpacing(8)

        # Panel header
        header = QLabel("Media Information")
        header.setStyleSheet("color: white; font-size: 12pt; font-weight: bold; background: transparent;")
        panel_layout.addWidget(header)

        # Create tabbed metadata view
        from PySide6.QtWidgets import QTabWidget
        self.metadata_tabs = QTabWidget()
        self.metadata_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background: transparent;
            }
            QTabBar::tab {
                background: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.7);
                padding: 8px 12px;
                border: none;
                border-radius: 6px 6px 0 0;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: rgba(255, 255, 255, 0.15);
                color: white;
            }
            QTabBar::tab:hover {
                background: rgba(255, 255, 255, 0.12);
            }
        """)

        # Create tabs with scrollable content
        self.edit_tab_content = self._create_metadata_edit_tab()
        self.basic_tab_content = self._create_scrollable_tab()
        self.camera_tab_content = self._create_scrollable_tab()
        self.location_tab_content = self._create_scrollable_tab()
        self.technical_tab_content = self._create_scrollable_tab()

        self.metadata_tabs.addTab(self.edit_tab_content['scroll'], "✏️ Edit")
        self.metadata_tabs.addTab(self.basic_tab_content['scroll'], "📄 Basic")
        self.metadata_tabs.addTab(self.camera_tab_content['scroll'], "📷 Camera")
        self.metadata_tabs.addTab(self.location_tab_content['scroll'], "🌍 Location")
        self.metadata_tabs.addTab(self.technical_tab_content['scroll'], "⚙️ Technical")

        panel_layout.addWidget(self.metadata_tabs)

        # For backward compatibility, keep reference to basic layout
        self.metadata_layout = self.basic_tab_content['layout']
        self.metadata_content = self.basic_tab_content['widget']

        return panel

    def _create_scrollable_tab(self) -> dict:
        """Create a scrollable tab content area."""
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        widget = QWidget()
        layout = QVBoxLayout(widget)
        # Ultra-optimize tab content spacing: Reduce from 12px to 4px
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignTop)

        scroll.setWidget(widget)

        return {'scroll': scroll, 'widget': widget, 'layout': layout}

    def _create_metadata_edit_tab(self) -> dict:
        """Create the metadata editing tab with rating, flag, title, caption, keywords."""
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        # Dark theme style for labels and inputs inside the lightbox
        label_style = "color: rgba(255,255,255,0.7); font-size: 9pt; font-weight: bold; background: transparent;"
        input_style = """
            color: white;
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 4px;
            padding: 4px 6px;
            font-size: 9pt;
        """

        # === Rating ===
        rating_label = QLabel("Rating")
        rating_label.setStyleSheet(label_style)
        layout.addWidget(rating_label)

        rating_row = QHBoxLayout()
        rating_row.setSpacing(2)
        self._lb_rating_buttons = []
        self._lb_current_rating = 0
        for i in range(5):
            btn = QToolButton()
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QToolButton {
                    border: none;
                    background: transparent;
                    font-size: 16px;
                    color: rgba(255,255,255,0.3);
                }
                QToolButton:hover {
                    background: rgba(255, 193, 7, 0.2);
                    border-radius: 4px;
                }
            """)
            btn.setText("☆")
            btn.clicked.connect(lambda checked, idx=i: self._on_lb_star_clicked(idx))
            self._lb_rating_buttons.append(btn)
            rating_row.addWidget(btn)

        clear_rating_btn = QToolButton()
        clear_rating_btn.setText("✕")
        clear_rating_btn.setToolTip("Clear rating")
        clear_rating_btn.setFixedSize(20, 20)
        clear_rating_btn.setCursor(Qt.PointingHandCursor)
        clear_rating_btn.setStyleSheet("""
            QToolButton { border: none; color: rgba(255,255,255,0.4); font-size: 12px; background: transparent; }
            QToolButton:hover { color: white; }
        """)
        clear_rating_btn.clicked.connect(lambda: self._on_lb_set_rating(0))
        rating_row.addWidget(clear_rating_btn)
        rating_row.addStretch()
        layout.addLayout(rating_row)

        # === Flag ===
        flag_label = QLabel("Flag")
        flag_label.setStyleSheet(label_style)
        layout.addWidget(flag_label)

        flag_row = QHBoxLayout()
        flag_row.setSpacing(4)
        self._lb_current_flag = "none"

        flag_btn_style = """
            QPushButton {{
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 9pt;
                color: rgba(255,255,255,0.7);
                background: rgba(255,255,255,0.05);
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.12);
            }}
            QPushButton:checked {{
                background: {bg};
                color: {fg};
                border-color: {fg};
            }}
        """

        self._lb_flag_pick = QPushButton("⬆ Pick")
        self._lb_flag_pick.setCheckable(True)
        self._lb_flag_pick.setStyleSheet(flag_btn_style.format(bg="rgba(76,175,80,0.3)", fg="#4CAF50"))
        self._lb_flag_pick.clicked.connect(lambda: self._on_lb_flag_clicked("pick"))
        flag_row.addWidget(self._lb_flag_pick)

        self._lb_flag_reject = QPushButton("⬇ Reject")
        self._lb_flag_reject.setCheckable(True)
        self._lb_flag_reject.setStyleSheet(flag_btn_style.format(bg="rgba(244,67,54,0.3)", fg="#F44336"))
        self._lb_flag_reject.clicked.connect(lambda: self._on_lb_flag_clicked("reject"))
        flag_row.addWidget(self._lb_flag_reject)

        flag_row.addStretch()
        layout.addLayout(flag_row)

        # === Title ===
        title_label = QLabel("Title")
        title_label.setStyleSheet(label_style)
        layout.addWidget(title_label)

        self._lb_edit_title = QLineEdit()
        self._lb_edit_title.setPlaceholderText("Add a title...")
        self._lb_edit_title.setStyleSheet(input_style)
        self._lb_edit_title.editingFinished.connect(
            lambda: self._on_lb_metadata_changed("title", self._lb_edit_title.text()))
        layout.addWidget(self._lb_edit_title)

        # === Caption ===
        caption_label = QLabel("Caption")
        caption_label.setStyleSheet(label_style)
        layout.addWidget(caption_label)

        self._lb_edit_caption = QTextEdit()
        self._lb_edit_caption.setPlaceholderText("Add a description...")
        self._lb_edit_caption.setMaximumHeight(70)
        self._lb_edit_caption.setStyleSheet(input_style + " QTextEdit { min-height: 50px; }")
        self._lb_edit_caption.textChanged.connect(
            lambda: self._on_lb_metadata_changed("caption", self._lb_edit_caption.toPlainText()))
        layout.addWidget(self._lb_edit_caption)

        # === Keywords ===
        keywords_label = QLabel("Keywords")
        keywords_label.setStyleSheet(label_style)
        layout.addWidget(keywords_label)

        self._lb_edit_tags = QLineEdit()
        self._lb_edit_tags.setPlaceholderText("tag1, tag2, tag3...")
        self._lb_edit_tags.setStyleSheet(input_style)
        self._lb_edit_tags.editingFinished.connect(
            lambda: self._on_lb_metadata_changed("tags", self._lb_edit_tags.text()))
        layout.addWidget(self._lb_edit_tags)

        # === Save status ===
        self._lb_save_status = QLabel("")
        self._lb_save_status.setStyleSheet("color: #4CAF50; font-size: 9pt; background: transparent;")
        self._lb_save_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lb_save_status)

        layout.addStretch()

        scroll.setWidget(widget)
        return {'scroll': scroll, 'widget': widget, 'layout': layout}

    # ---- Lightbox metadata editing helpers ----

    def _get_photo_id_for_path(self, path: str):
        """Get photo ID from database for a given path."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id FROM photo_metadata WHERE path = ?", (path,))
                row = cursor.fetchone()
                if row:
                    return row['id']
                # Try case-insensitive match as fallback
                cursor = conn.execute(
                    "SELECT id FROM photo_metadata WHERE LOWER(path) = LOWER(?)", (path,))
                row = cursor.fetchone()
                if row:
                    print(f"[MediaLightbox] Found photo_id via case-insensitive match for: {os.path.basename(path)}")
                    return row['id']
                if self._is_video(path):
                    print(f"[MediaLightbox] No photo_metadata row for video: {os.path.basename(path)} (expected)")
                else:
                    print(f"[MediaLightbox] No photo_metadata row found for: {os.path.basename(path)}")
                return None
        except Exception as e:
            print(f"[MediaLightbox] Error getting photo ID: {e}")
            return None

    def _load_editable_metadata(self):
        """Load editable metadata fields for the current media into the Edit tab."""
        self._lb_loading = True
        try:
            photo_id = self._get_photo_id_for_path(self.media_path)
            self._lb_current_photo_id = photo_id

            if photo_id is None:
                # No DB record - clear fields
                self._on_lb_set_rating(0)
                self._on_lb_set_flag("none")
                self._lb_edit_title.clear()
                self._lb_edit_caption.clear()
                self._lb_edit_tags.clear()
                self._lb_save_status.setText("")
                self._lb_loading = False
                return

            # Load from DB
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT rating, flag, title, caption, tags
                    FROM photo_metadata WHERE id = ?
                """, (photo_id,))
                row = cursor.fetchone()

            if row:
                self._on_lb_set_rating(row['rating'] or 0)
                self._on_lb_set_flag(row['flag'] or 'none')
                self._lb_edit_title.setText(row['title'] or '')
                self._lb_edit_caption.setPlainText(row['caption'] or '')
                self._lb_edit_tags.setText(row['tags'] or '')
            else:
                self._on_lb_set_rating(0)
                self._on_lb_set_flag("none")
                self._lb_edit_title.clear()
                self._lb_edit_caption.clear()
                self._lb_edit_tags.clear()

            self._lb_save_status.setText("")
        except Exception as e:
            print(f"[MediaLightbox] Error loading editable metadata: {e}")
        finally:
            self._lb_loading = False

    def _on_lb_star_clicked(self, idx: int):
        """Handle star click in lightbox rating widget."""
        new_rating = idx + 1
        if self._lb_current_rating == new_rating:
            new_rating = 0  # Toggle off if clicking same star
        self._on_lb_set_rating(new_rating)
        if not getattr(self, '_lb_loading', False):
            self._on_lb_metadata_changed("rating", new_rating)

    def _on_lb_set_rating(self, rating: int):
        """Set the rating display in lightbox."""
        self._lb_current_rating = rating
        for i, btn in enumerate(self._lb_rating_buttons):
            if i < rating:
                btn.setText("★")
                btn.setStyleSheet("""
                    QToolButton {
                        border: none; background: transparent;
                        font-size: 16px; color: #FFC107;
                    }
                    QToolButton:hover { background: rgba(255,193,7,0.2); border-radius: 4px; }
                """)
            else:
                btn.setText("☆")
                btn.setStyleSheet("""
                    QToolButton {
                        border: none; background: transparent;
                        font-size: 16px; color: rgba(255,255,255,0.3);
                    }
                    QToolButton:hover { background: rgba(255,193,7,0.2); border-radius: 4px; }
                """)

    def _on_lb_flag_clicked(self, flag: str):
        """Handle flag button click in lightbox."""
        if self._lb_current_flag == flag:
            flag = "none"  # Toggle off
        self._on_lb_set_flag(flag)
        if not getattr(self, '_lb_loading', False):
            self._on_lb_metadata_changed("flag", flag)

    def _on_lb_set_flag(self, flag: str):
        """Set the flag display in lightbox."""
        self._lb_current_flag = flag
        self._lb_flag_pick.setChecked(flag == "pick")
        self._lb_flag_reject.setChecked(flag == "reject")

    def _on_lb_metadata_changed(self, field: str, value):
        """Handle metadata field change - save to database."""
        if getattr(self, '_lb_loading', False):
            return
        photo_id = getattr(self, '_lb_current_photo_id', None)
        if photo_id is None:
            return

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            column_map = {
                "rating": "rating",
                "flag": "flag",
                "title": "title",
                "caption": "caption",
                "tags": "tags",
            }
            column = column_map.get(field)
            if column is None:
                return

            with db.get_connection() as conn:
                # Ensure column exists
                cursor = conn.execute("PRAGMA table_info(photo_metadata)")
                existing_cols = {r["name"] for r in cursor.fetchall()}
                if column not in existing_cols:
                    col_type = "INTEGER" if field == "rating" else "TEXT"
                    conn.execute(f"ALTER TABLE photo_metadata ADD COLUMN {column} {col_type}")

                conn.execute(f"""
                    UPDATE photo_metadata SET {column} = ?, updated_at = datetime('now')
                    WHERE id = ?
                """, (value, photo_id))
                conn.commit()

            self._lb_save_status.setText("✓ Saved")
            QTimer.singleShot(2000, lambda: self._lb_save_status.setText("")
                              if hasattr(self, '_lb_save_status') else None)
        except Exception as e:
            print(f"[MediaLightbox] Error saving metadata field {field}: {e}")
            self._lb_save_status.setText("⚠ Save failed")

    def _create_enhance_panel(self) -> QWidget:
        panel = QWidget()
        # FIX D: Make panel adaptive (not fixed width) like Lightroom drawers
        # Cap relative to window width so it never dominates the canvas
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(max(240, int(self.width() * 0.30)))  # <= 30% of window
        panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        panel.setStyleSheet("""
            QWidget {
                background: rgba(32, 33, 36, 0.95);
                border-left: 1px solid rgba(255, 255, 255, 0.1);
            }
        """)
        panel_layout = QVBoxLayout(panel)
        # Professional margins: Consistent with info panel
        panel_layout.setContentsMargins(12, 16, 12, 16)
        panel_layout.setSpacing(6)

        header = QLabel("Suggestions")
        header.setStyleSheet("color: white; font-size: 12pt; font-weight: bold; background: transparent;")
        panel_layout.addWidget(header)

        def add_suggestion(label, tooltip, on_click):
            btn = QPushButton(label)
            btn.setFixedHeight(44)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e1e1e;
                    color: white;
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 12px;
                    padding: 12px 16px;
                    text-align: left;
                    font-size: 11pt;
                }
                QPushButton:hover { background: #252525; border-color: rgba(255,255,255,0.18); }
                QPushButton:pressed { background: #2d2d2d; }
            """)
            btn.setToolTip(tooltip)
            btn.clicked.connect(on_click)
            panel_layout.addWidget(btn)
            return btn

        # Buttons inside panel
        self.suggestion_enhance_btn = add_suggestion("✨ Enhance", "Auto-Enhance", self._toggle_auto_enhance)
        self.suggestion_dynamic_btn = add_suggestion("⚡ Dynamic", "Vivid colors & contrast", lambda: self._set_preset("dynamic"))
        self.suggestion_warm_btn = add_suggestion("🌤 Warm", "Cozy tones", lambda: self._set_preset("warm"))
        self.suggestion_cool_btn = add_suggestion("❄️ Cool", "Crisp bluish look", lambda: self._set_preset("cool"))

        return panel

    def _toggle_enhance_panel(self):
        if self.enhance_panel_visible:
            self.enhance_panel.hide()
            self.enhance_panel_visible = False
        else:
            # Hide info panel if visible to avoid crowding
            if getattr(self, 'info_panel_visible', False):
                self.info_panel.hide()
                self.info_panel_visible = False
            self.enhance_panel.show()
            self.enhance_panel_visible = True

        # Reposition UI overlays and media
        self._position_nav_buttons()
        self._position_media_caption()
        QTimer.singleShot(10, self._reposition_media_for_panel)


    def _toggle_play_pause(self):
        """Toggle video playback (play/pause)."""
        if hasattr(self, 'video_player'):
            from PySide6.QtMultimedia import QMediaPlayer
            if self.video_player.playbackState() == QMediaPlayer.PlayingState:
                self.video_player.pause()
                self.play_pause_btn.setText("▶")
            else:
                self.video_player.play()
                self.play_pause_btn.setText("⏸")

    def _position_media_caption(self):
        """Position media caption overlay at bottom center (like Google Photos/Lightroom)."""
        from PySide6.QtCore import QPoint
        
        if not hasattr(self, 'media_caption') or not self.media_caption:
            return
        
        # Get scroll area viewport position and size
        viewport = self.scroll_area.viewport()
        viewport_pos = viewport.mapTo(self, QPoint(0, 0))
        viewport_width = viewport.width()
        viewport_height = viewport.height()
        
        # Adjust caption width
        caption_width = min(500, viewport_width - 40)  # Max 500px, leave 20px margins
        self.media_caption.setMaximumWidth(caption_width)
        self.media_caption.adjustSize()  # Resize to content
        
        # Position at BOTTOM CENTER (like Google Photos)
        caption_x = viewport_pos.x() + (viewport_width - self.media_caption.width()) // 2
        
        # Calculate Y position from bottom
        # If video controls are visible, position above them; otherwise use bottom margin
        bottom_offset = 20  # Default 20px from bottom
        if hasattr(self, 'bottom_toolbar') and self.bottom_toolbar.isVisible():
            bottom_offset = self.bottom_toolbar.height() + 10  # 10px above video controls
        
        caption_y = viewport_pos.y() + viewport_height - self.media_caption.height() - bottom_offset
        
        self.media_caption.move(caption_x, caption_y)
        self.media_caption.raise_()  # Ensure it's on top
    
    def _update_media_caption(self, filename: str):
        """Update and show media caption with filename (Google Photos style - auto-fade)."""
        if not hasattr(self, 'media_caption'):
            return
        
        self.media_caption.setText(filename)
        self.media_caption.show()
        self._position_media_caption()
        
        # Fade in caption
        self._fade_in_caption()
        
        # Start auto-hide timer (3 seconds)
        self.caption_hide_timer.stop()
        self.caption_hide_timer.start()
    
    def _fade_in_caption(self):
        """Fade in the caption smoothly."""
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        
        if not hasattr(self, 'caption_opacity'):
            return
        
        # Stop any existing animation
        if hasattr(self, '_caption_fade_anim'):
            self._caption_fade_anim.stop()
        
        # Create fade-in animation
        self._caption_fade_anim = QPropertyAnimation(self.caption_opacity, b"opacity")
        self._caption_fade_anim.setDuration(300)  # 300ms fade in
        self._caption_fade_anim.setStartValue(0.0)
        self._caption_fade_anim.setEndValue(1.0)
        self._caption_fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._caption_fade_anim.start()
    
    def _fade_out_caption(self):
        """Fade out the caption smoothly (auto-hide after 3 seconds)."""
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        
        if not hasattr(self, 'caption_opacity'):
            return
        
        # Stop any existing animation
        if hasattr(self, '_caption_fade_anim'):
            self._caption_fade_anim.stop()
        
        # Create fade-out animation
        self._caption_fade_anim = QPropertyAnimation(self.caption_opacity, b"opacity")
        self._caption_fade_anim.setDuration(500)  # 500ms fade out (slower)
        self._caption_fade_anim.setStartValue(1.0)
        self._caption_fade_anim.setEndValue(0.0)
        self._caption_fade_anim.setEasingCurve(QEasingCurve.InCubic)
        self._caption_fade_anim.start()
    
    def _reposition_media_for_panel(self):
        """Reposition/resize media when info panel toggles."""
        if self._is_video(self.media_path):
            # Reapply video zoom to adjust to new viewport size
            if hasattr(self, 'video_widget') and self.video_widget:
                self._apply_video_zoom()
        else:
            # Reapply photo zoom to adjust to new viewport size
            if self.original_pixmap and not self.original_pixmap.isNull():
                if self.zoom_mode == "fit":
                    self._fit_to_window()
                elif self.zoom_mode == "fill":
                    self._fill_window()
                else:
                    self._apply_zoom()

    def _on_volume_changed(self, value: int):
        """Handle volume slider change."""
        if hasattr(self, 'audio_output'):
            volume = value / 100.0
            self.audio_output.setVolume(volume)
            # Update mute state based on volume
            if value == 0:
                self.video_is_muted = True
                if hasattr(self, 'mute_btn'):
                    self.mute_btn.setText("🔇")
            elif self.video_is_muted:
                # Un-mute when volume is raised
                self.video_is_muted = False
                self.audio_output.setMuted(False)
                if hasattr(self, 'mute_btn'):
                    self.mute_btn.setText("🔊")

    def _on_mute_clicked(self):
        """Toggle audio mute state."""
        self.video_is_muted = not self.video_is_muted

        if hasattr(self, 'audio_output') and self.audio_output is not None:
            self.audio_output.setMuted(self.video_is_muted)

        # Update button icon
        if hasattr(self, 'mute_btn'):
            self.mute_btn.setText("🔇" if self.video_is_muted else "🔊")

        # Show status toast
        status = "Muted" if self.video_is_muted else "Unmuted"
        self._show_toast(status)

    def _on_seek_pressed(self):
        """Handle seek slider press (pause position updates)."""
        if hasattr(self, 'position_timer'):
            self.position_timer.stop()

    def _on_seek_released(self):
        """Handle seek slider release (seek to position)."""
        if hasattr(self, 'video_player'):
            position = self.seek_slider.value()
            self.video_player.setPosition(position)
            if hasattr(self, 'position_timer'):
                self.position_timer.start()

    def _is_video(self, path: str) -> bool:
        """Check if file is a video based on extension."""
        # CRITICAL FIX: Include ALL video extensions (was missing .wmv, .flv, .mpg, .mpeg)
        # Must match _is_video_file() for consistent behavior
        video_extensions = {
            '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp',
            '.flv', '.wmv', '.mpg', '.mpeg', '.mts', '.m2ts', '.ts',
            '.vob', '.ogv', '.divx', '.asf', '.rm', '.rmvb'
        }
        return os.path.splitext(path)[1].lower() in video_extensions

    def _is_raw(self, path: str) -> bool:
        """PHASE C #1: Check if file is a RAW photo based on extension."""
        raw_extensions = {
            '.cr2', '.cr3',  # Canon
            '.nef', '.nrw',  # Nikon
            '.arw', '.srf', '.sr2',  # Sony
            '.dng',  # Adobe/Universal
            '.raf',  # Fujifilm
            '.orf',  # Olympus
            '.rw2',  # Panasonic
            '.pef',  # Pentax
            '.3fr',  # Hasselblad
            '.ari',  # ARRI
            '.bay',  # Casio
            '.crw',  # Canon (old)
            '.erf',  # Epson
            '.kdc',  # Kodak
            '.mef',  # Mamiya
            '.mos',  # Leaf
            '.mrw',  # Minolta
            '.raw',  # Generic
        }
        return os.path.splitext(path)[1].lower() in raw_extensions

    def _show_toast(self, message: str, duration: int = 1500):
        """Show a transient toast message overlay for feedback."""
        from PySide6.QtCore import QTimer

        # Create or reuse toast label
        if not hasattr(self, '_toast_label') or self._toast_label is None:
            self._toast_label = QLabel(self)
            self._toast_label.setStyleSheet("""
                QLabel {
                    background: rgba(0, 0, 0, 0.75);
                    color: white;
                    padding: 12px 24px;
                    border-radius: 8px;
                    font-size: 11pt;
                    font-weight: bold;
                }
            """)
            self._toast_label.setAlignment(Qt.AlignCenter)

        self._toast_label.setText(message)
        self._toast_label.adjustSize()

        # Center the toast in the lightbox
        x = (self.width() - self._toast_label.width()) // 2
        y = self.height() - 150  # Position above bottom toolbar
        self._toast_label.move(x, y)
        self._toast_label.raise_()
        self._toast_label.show()

        # Auto-hide after duration
        QTimer.singleShot(duration, lambda: self._toast_label.hide() if hasattr(self, '_toast_label') and self._toast_label else None)

    def _detect_motion_photo(self, photo_path: str) -> str:
        """
        PHASE C #5: Detect if photo has paired video (Motion Photo / Live Photo).

        Returns path to paired video, or None if not found.

        Common patterns:
        - IMG_1234.JPG + IMG_1234.MP4
        - IMG_1234.JPG + IMG_1234_MOTION.MP4
        - IMG_1234.JPG + MVIMG_1234.MP4 (Google Motion)
        """
        if not self.motion_photo_enabled:
            return None

        if self._is_video(photo_path):
            return None  # Only check for photos, not videos

        # Get base name and directory
        photo_dir = os.path.dirname(photo_path)
        photo_name = os.path.basename(photo_path)
        photo_base, photo_ext = os.path.splitext(photo_name)

        # Patterns to check
        video_patterns = [
            f"{photo_base}.mp4",           # IMG_1234.MP4
            f"{photo_base}.MP4",
            f"{photo_base}_MOTION.mp4",    # IMG_1234_MOTION.MP4
            f"{photo_base}_MOTION.MP4",
            f"MVIMG_{photo_base}.mp4",     # MVIMG_IMG_1234.mp4 (Google)
            f"MVIMG_{photo_base}.MP4",
            f"{photo_base}.mov",           # IMG_1234.MOV (iPhone Live Photo)
            f"{photo_base}.MOV",
        ]

        # Check each pattern
        for pattern in video_patterns:
            video_path = os.path.join(photo_dir, pattern)
            if os.path.exists(video_path):
                print(f"[MediaLightbox] ✓ Motion photo detected: {photo_name} + {pattern}")
                return video_path

        return None

    def _load_media_safe(self):
        """Safe wrapper for _load_media that sets the loaded flag."""
        if not self._media_loaded:
            self._media_loaded = True
            self._load_media()

    def _load_media(self):
        """Load and display current media (photo or video)."""
        print(f"[MediaLightbox] _load_media called for: {os.path.basename(self.media_path)}")
        if self._is_video(self.media_path):
            self._load_video()
        else:
            self._load_photo()

    def _load_video(self):
        """Load and display video with playback controls."""
        print(f"[MediaLightbox] Loading video: {os.path.basename(self.media_path)}")

        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
            from PySide6.QtCore import QUrl

            # Bump generation counter — stale nativeSizeChanged / fit callbacks are discarded
            self._video_gen += 1
            gen = self._video_gen

            # Cancel any pending fit timer from previous video
            self._video_fit_timer.stop()

            # Stop and cleanup previous video BEFORE loading new one (non-blocking)
            if hasattr(self, 'video_player') and self.video_player is not None:
                print(f"[MediaLightbox] Stopping previous video...")
                try:
                    self.video_player.stop()
                    if hasattr(self, 'position_timer') and self.position_timer:
                        self.position_timer.stop()
                    self.video_player.setSource(QUrl())
                    print(f"[MediaLightbox] Previous video stopped and cleaned up")
                except Exception as cleanup_err:
                    print(f"[MediaLightbox] Warning during video cleanup: {cleanup_err}")

            # Clear previous content
            self.image_label.clear()
            self.image_label.setStyleSheet("")
            self.image_label.hide()

            # Create video player if not exists
            if not hasattr(self, 'video_player') or self.video_player is None:
                self.video_player = QMediaPlayer(self)
                self.audio_output = QAudioOutput(self)
                self.video_player.setAudioOutput(self.audio_output)

                # Create video view (QGraphicsView + QGraphicsVideoItem) to support rotation in preview
                from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QFrame, QSizePolicy
                from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

                self.video_graphics_view = QGraphicsView()
                self.video_graphics_view.setStyleSheet("background: black;")
                self.video_graphics_view.setFrameShape(QFrame.NoFrame)
                self.video_graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                self.video_graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                # Enable smooth zoom/pan behavior
                self.video_graphics_view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
                self.video_graphics_view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
                self.video_graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)
                self.video_graphics_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                self.video_scene = QGraphicsScene(self.video_graphics_view)
                self.video_graphics_view.setScene(self.video_scene)
                self.video_graphics_view.viewport().installEventFilter(self)
                self.video_graphics_view.installEventFilter(self)
                self.video_graphics_view.setFocusPolicy(Qt.WheelFocus)
                self.video_graphics_view.setFocus()

                self.video_item = QGraphicsVideoItem()
                self.video_scene.addItem(self.video_item)

                # Use QGraphicsVideoItem as the video output
                self.video_player.setVideoOutput(self.video_item)

                # For compatibility, keep using video_widget name for sizing/grab
                self.video_widget = self.video_graphics_view

                # Add video view to container
                container_layout = self.media_container.layout()
                if container_layout:
                    container_layout.addWidget(self.video_graphics_view)
                    self.scroll_area.viewport().installEventFilter(self)

                # Disconnect old signals before connecting new ones
                self._disconnect_video_signals()

                # Connect video player signals with error handling
                try:
                    self.video_player.durationChanged.connect(self._on_duration_changed)
                    self.video_player.positionChanged.connect(self._on_position_changed)
                    self.video_player.errorOccurred.connect(self._on_video_error)
                    self.video_player.mediaStatusChanged.connect(self._on_media_status_changed)
                    print("[MediaLightbox] Video signals connected")
                except Exception as signal_err:
                    print(f"[MediaLightbox] Warning: Could not connect video signals: {signal_err}")

                # Create position update timer
                if not hasattr(self, 'position_timer'):
                    self.position_timer = QTimer(self)
                    self.position_timer.timeout.connect(self._update_video_position)
                    self.position_timer.setInterval(100)

            # Connect nativeSizeChanged with generation guard (reconnect each load)
            try:
                self.video_item.nativeSizeChanged.disconnect()
            except (TypeError, RuntimeError):
                pass

            def _on_native_size(size, _gen=gen):
                if _gen != self._video_gen:
                    return  # Stale callback from previous video
                if size.isEmpty() or size.width() <= 0 or size.height() <= 0:
                    return
                # Schedule a debounced fit (coalesces multiple signals)
                self._fit_video_view()

            self.video_item.nativeSizeChanged.connect(_on_native_size)
            # prevent GC of the closure
            self._native_size_callback = _on_native_size

            # CORE GEOMETRY FIX: Make scroll area auto-resize the container
            # so video_graphics_view gets real geometry via its Expanding policy.
            # Without this, the container stays tiny → viewport stays tiny → dot.
            # (Restored to False in _load_photo for photo zoom/scroll behavior.)
            if hasattr(self, 'scroll_area'):
                self.scroll_area.setWidgetResizable(True)

            # Show video widget with minimum size safety net
            if hasattr(self, 'video_widget'):
                self.video_widget.setMinimumSize(320, 240)
                self.video_widget.show()

            # Show video controls in bottom toolbar
            if hasattr(self, 'video_controls_widget'):
                self.video_controls_widget.show()
            if hasattr(self, 'bottom_toolbar'):
                self.bottom_toolbar.show()
                if hasattr(self, 'bottom_toolbar_opacity'):
                    self.bottom_toolbar_opacity.setOpacity(1.0)

            # Set volume
            if hasattr(self, 'volume_slider') and hasattr(self, 'audio_output'):
                volume = self.volume_slider.value() / 100.0
                self.audio_output.setVolume(volume)

            # Verify file exists
            if not os.path.exists(self.media_path):
                raise FileNotFoundError(f"Video file not found: {self.media_path}")

            # Reset zoom and fit state for new media load
            self.edit_zoom_level = 1.0
            self.zoom_mode = "fit"
            self._last_video_fit_sig = None  # Force re-fit for new video
            self._video_fit_ready = False  # Block zoom until valid fit
            # Reset transform to prevent stale "dot" scale from previous video
            self.video_graphics_view.resetTransform()
            self.video_base_scale = 1.0

            # Load and play video
            video_url = QUrl.fromLocalFile(self.media_path)
            self.video_player.setSource(video_url)
            self.video_player.play()

            # Update play/pause button
            if hasattr(self, 'play_pause_btn'):
                self.play_pause_btn.setText("⏸")

            # Start position timer
            if hasattr(self, 'position_timer'):
                self.position_timer.start()

            # Update counter and navigation
            if hasattr(self, 'counter_label'):
                self.counter_label.setText(f"{self.current_index + 1} of {len(self.all_media)}")
            if hasattr(self, 'prev_btn'):
                self.prev_btn.setEnabled(self.current_index > 0)
            if hasattr(self, 'next_btn'):
                self.next_btn.setEnabled(self.current_index < len(self.all_media) - 1)

            # Load metadata
            self._load_metadata()

            print(f"[MediaLightbox] Video player started: {os.path.basename(self.media_path)}")

            # Apply preview rotation to QGraphicsVideoItem (if available)
            if hasattr(self, '_apply_preview_rotation'):
                self._apply_preview_rotation()

            # Update and show caption
            self._update_media_caption(os.path.basename(self.media_path))

        except Exception as e:
            print(f"[MediaLightbox] Error loading video: {e}")
            import traceback
            traceback.print_exc()

            # Fallback to placeholder
            self.image_label.show()
            if hasattr(self, 'video_widget'):
                self.video_widget.hide()
            if hasattr(self, 'video_controls_widget'):
                self.video_controls_widget.hide()
            self.image_label.setText(f"VIDEO\n\n{os.path.basename(self.media_path)}\n\nPlayback error\n{str(e)}")
            self.image_label.setStyleSheet("color: white; font-size: 16pt; background: #2a2a2a; border-radius: 8px; padding: 40px;")

            # Update counter even on error
            if hasattr(self, 'counter_label'):
                self.counter_label.setText(f"{self.current_index + 1} of {len(self.all_media)}")
            if hasattr(self, 'prev_btn'):
                self.prev_btn.setEnabled(self.current_index > 0)
            if hasattr(self, 'next_btn'):
                self.next_btn.setEnabled(self.current_index < len(self.all_media) - 1)

    def _on_video_error(self, error):
        """Handle video playback errors."""
        from PySide6.QtMultimedia import QMediaPlayer
        error_string = "Unknown error"
        if hasattr(self, 'video_player'):
            error_string = self.video_player.errorString()
        print(f"[MediaLightbox] Video error: {error} - {error_string}")
        
        # Show error in UI
        if hasattr(self, 'image_label'):
            self.image_label.show()
            self.image_label.setText(f"🎬 VIDEO ERROR\n\n{os.path.basename(self.media_path)}\n\n{error_string}")
            self.image_label.setStyleSheet("color: #ff6b6b; font-size: 14pt; background: #2a2a2a; border-radius: 8px; padding: 40px;")
        if hasattr(self, 'video_widget'):
            self.video_widget.hide()
        if hasattr(self, 'video_controls_widget'):
            self.video_controls_widget.hide()

    def _fit_video_view(self):
        """Debounced scheduler — coalesces multiple fit requests into one frame."""
        if not hasattr(self, '_video_fit_timer'):
            # Fallback for edge case during init
            self._do_fit_video_view()
            return
        # Restart the single-shot timer; only the last call within 16ms fires
        self._video_fit_timer.stop()
        self._video_fit_timer.start()

    def _do_fit_video_view(self):
        """
        Fit video to view and calculate base scale for zoom operations.

        EVENT-DRIVEN DESIGN (Google Photos / Lightroom pattern):
        - Triggered by nativeSizeChanged AND by Show/Resize events on the
          video viewport (via eventFilter). No timer-based guessing.
        - Does NOT retry via QTimer.singleShot — if the viewport is too small,
          the next Resize/Show event will re-trigger us automatically.
        - Sets _video_fit_ready=True on success, gating zoom operations.
        - Does NOT reset edit_zoom_level — that is only done on media switch.
        """
        try:
            if not hasattr(self, 'video_item') or not hasattr(self, 'video_graphics_view'):
                return

            native_size = self.video_item.nativeSize()
            if native_size.isEmpty() or native_size.width() <= 0 or native_size.height() <= 0:
                return  # nativeSizeChanged will fire again when size becomes valid

            # Only fit when the view is actually visible (not hidden in stacked widget)
            if not self.video_graphics_view.isVisible():
                return

            view_rect = self.video_graphics_view.viewport().rect()
            view_w = view_rect.width()
            view_h = view_rect.height()

            if view_w <= 0 or view_h <= 0:
                return

            # Guard: viewport must have real geometry (not a tiny interim size).
            # No timer retry — the next Resize/Show event from Qt will call us again.
            min_vp = getattr(self, '_video_min_viewport_px', 160)
            if view_w < min_vp or view_h < min_vp:
                return

            video_w = native_size.width()
            video_h = native_size.height()

            # Dedup: skip if geometry + zoom haven't changed (prevents micro-stutter)
            fit_sig = (view_w, view_h, video_w, video_h, getattr(self, 'edit_zoom_level', 1.0))
            if getattr(self, '_last_video_fit_sig', None) == fit_sig:
                return
            self._last_video_fit_sig = fit_sig

            scale_w = view_w / video_w
            scale_h = view_h / video_h
            self.video_base_scale = min(scale_w, scale_h) * 0.95  # 5% padding

            self.video_scene.setSceneRect(0, 0, video_w, video_h)

            # Apply the transform (uses current edit_zoom_level, preserving user zoom)
            # _apply_video_zoom checks _video_fit_ready, so set it BEFORE calling
            self._video_fit_ready = True
            self._apply_video_zoom()

            self.video_graphics_view.centerOn(self.video_item)

            print(f"[MediaLightbox] Video fitted: native={video_w}x{video_h}, view={view_w}x{view_h}, base_scale={self.video_base_scale:.2f}")

        except Exception as e:
            print(f"[MediaLightbox] _fit_video_view error: {e}")
            import traceback
            traceback.print_exc()

    def _on_duration_changed(self, duration: int):
        """Handle video duration change (set seek slider range)."""
        self.seek_slider.setMaximum(duration)
        # Format duration as mm:ss
        minutes = duration // 60000
        seconds = (duration % 60000) // 1000
        self.time_total_label.setText(f"{minutes}:{seconds:02d}")

    def _on_position_changed(self, position: int):
        """Handle video position change (update seek slider and time)."""
        # Update seek slider (only if not being dragged)
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position)

    def _update_video_position(self):
        """Update video position display."""
        if hasattr(self, 'video_player'):
            position = self.video_player.position()
            # Format position as mm:ss
            minutes = position // 60000
            seconds = (position % 60000) // 1000
            self.time_current_label.setText(f"{minutes}:{seconds:02d}")

    def _load_photo(self):
        """
        Load and display the current photo with EXIF orientation correction.

        PHASE A ENHANCEMENTS:
        - Checks preload cache first (instant load)
        - Uses progressive loading (thumbnail → full quality)
        - Shows loading indicators
        - Triggers background preloading of next photos
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        try:
            # CRITICAL FIX: Ensure mode_stack is on viewer page (0), not editor page (1)
            # Bug: If user was in edit mode, photos load but aren't visible
            if hasattr(self, 'mode_stack'):
                if self.mode_stack.currentIndex() != 0:
                    print(f"[MediaLightbox] ⚠️ Mode stack was on page {self.mode_stack.currentIndex()}, switching to viewer (0)")
                    self.mode_stack.setCurrentIndex(0)

            # Restore scroll area to non-resizable mode for photo zoom/scroll
            # (Video mode sets widgetResizable=True for auto-geometry)
            if hasattr(self, 'scroll_area'):
                self.scroll_area.setWidgetResizable(False)

            # Clear video fit state
            self._video_fit_ready = False

            # Hide video widget and controls if they exist AND are not None
            if hasattr(self, 'video_widget') and self.video_widget is not None:
                self.video_widget.hide()
                if hasattr(self, 'video_player') and self.video_player is not None:
                    self.video_player.stop()
                    if hasattr(self, 'position_timer') and self.position_timer is not None:
                        self.position_timer.stop()
                    # Clear source to release decoder resources
                    from PySide6.QtCore import QUrl
                    self.video_player.setSource(QUrl())

            # Hide video controls
            if hasattr(self, 'video_controls_widget') and self.video_controls_widget is not None:
                self.video_controls_widget.hide()
            if hasattr(self, 'bottom_toolbar') and self.bottom_toolbar is not None:
                self.bottom_toolbar.hide()  # Hide bottom toolbar when showing photos

            # Show image label (simple show/hide, no widget replacement!)
            self.image_label.show()
            self.image_label.setStyleSheet("")  # Reset any custom styling

            print(f"[MediaLightbox] Loading photo: {os.path.basename(self.media_path)}")

            # PHASE A #1: Check preload cache first (instant load!)
            if self.media_path in self.preload_cache:
                print(f"[MediaLightbox] ✓ Loading from cache (INSTANT!)")
                cached_data = self.preload_cache[self.media_path]
                pixmap = cached_data['pixmap']

                # Use cached pixmap directly
                self.original_pixmap = pixmap

                # Scale to fit
                viewport_size = self.scroll_area.viewport().size()
                scaled_pixmap = pixmap.scaled(
                    viewport_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )

                self.image_label.setPixmap(scaled_pixmap)
                self.image_label.resize(scaled_pixmap.size())
                self.media_container.resize(scaled_pixmap.size())

                # Calculate zoom level
                self.zoom_level = scaled_pixmap.width() / pixmap.width()
                self.fit_zoom_level = self.zoom_level
                self.zoom_mode = "fit"

                print(f"[MediaLightbox] ✓ Loaded from cache instantly!")

            # PHASE A #2: Progressive loading (thumbnail → full quality)
            elif self.progressive_loading:
                print(f"[MediaLightbox] Starting progressive load...")

                # Bump generation — any in-flight worker with an older
                # generation will have its results silently discarded.
                self._lb_media_generation += 1

                # Reset progressive load state
                self.thumbnail_quality_loaded = False
                self.full_quality_loaded = False

                # PHASE A #4: Show loading indicator
                self._show_loading_indicator("⏳ Loading...")

                # Start progressive load worker with current generation
                viewport_size = self.scroll_area.viewport().size()
                worker = ProgressiveImageWorker(
                    self.media_path,
                    self.progressive_signals,
                    viewport_size,
                    self._lb_media_generation
                )
                self.preload_thread_pool.start(worker)

            # Fallback: Direct load (old method)
            else:
                print(f"[MediaLightbox] Direct load (progressive loading disabled)")
                self._load_photo_direct()

            # Update counter
            self.counter_label.setText(
                f"{self.current_index + 1} of {len(self.all_media)}"
            )

            # Update navigation buttons
            self.prev_btn.setEnabled(self.current_index > 0)
            self.next_btn.setEnabled(self.current_index < len(self.all_media) - 1)

            # Load metadata
            self._load_metadata()

            # Update status label (zoom indicator)
            self._update_status_label()

            # PHASE A #1: Start preloading next photos in background
            self._start_preloading()

            # PHASE B: Integrate all Phase B features
            self._update_filmstrip()  # B #1: Update filmstrip thumbnails
            self._update_contextual_toolbars()  # B #4: Show/hide contextual buttons
            self._restore_zoom_state()  # B #5: Restore saved zoom if enabled

            # PHASE C #5: Detect motion photo
            self.motion_video_path = self._detect_motion_photo(self.media_path)
            self.is_motion_photo = (self.motion_video_path is not None)

            # Update motion photo indicator
            if self.is_motion_photo:
                self._show_motion_indicator()
            else:
                self._hide_motion_indicator()
            
            # Update and show caption
            self._update_media_caption(os.path.basename(self.media_path))

        except Exception as e:
            print(f"[MediaLightbox] Error loading photo: {e}")
            self.image_label.setText(f"❌ Error loading image\n\n{str(e)}")
            self.image_label.setStyleSheet("color: white; font-size: 12pt;")

    def _load_photo_direct(self):
        """
        Direct photo loading (fallback when progressive loading disabled).

        Uses SafeImageLoader for memory-safe, capped-size decoding.
        Decodes at viewport-fit size (max 2560px), not full resolution.
        PHASE C #1: RAW support handled by SafeImageLoader.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap
        from services.safe_image_loader import safe_decode_qimage

        try:
            # Calculate target size: viewport-fit, capped at 2560
            viewport_size = self.scroll_area.viewport().size()
            viewport_max = max(viewport_size.width(), viewport_size.height())
            max_dim = min(viewport_max, 2560)

            print(f"[MediaLightbox] Direct load via SafeImageLoader: "
                  f"{os.path.basename(self.media_path)} (max_dim={max_dim})")

            # Memory-safe decode with retry ladder
            qimage = safe_decode_qimage(
                self.media_path,
                max_dim=max_dim,
                enable_retry_ladder=True,
            )

            if qimage.isNull():
                print(f"[MediaLightbox] ⚠️ SafeImageLoader returned null for direct load")
                self.image_label.setText("❌ Cannot load image")
                self.image_label.setStyleSheet("color: white; font-size: 12pt;")
                return

            # Convert QImage → QPixmap on main thread
            pixmap = QPixmap.fromImage(qimage)

        except Exception as e:
            print(f"[MediaLightbox] Direct loading failed: {e}")
            self.image_label.setText(f"❌ Error loading image\n\n{str(e)}")
            self.image_label.setStyleSheet("color: white; font-size: 12pt;")
            return

        if pixmap and not pixmap.isNull():
            # Store original
            self.original_pixmap = pixmap

            # Scale to fit
            scaled_pixmap = pixmap.scaled(
                viewport_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            self.image_label.setPixmap(scaled_pixmap)
            self.image_label.resize(scaled_pixmap.size())
            self.media_container.resize(scaled_pixmap.size())

            # Calculate zoom level
            self.zoom_level = scaled_pixmap.width() / pixmap.width()
            self.fit_zoom_level = self.zoom_level
            self.zoom_mode = "fit"

            print(f"[MediaLightbox] ✓ Direct load complete: "
                  f"{pixmap.width()}x{pixmap.height()} -> "
                  f"{scaled_pixmap.width()}x{scaled_pixmap.height()}")

    def _load_metadata(self):
        """Load and display comprehensive metadata in tabbed view."""
        # Clear all tabs
        self._clear_tab(self.basic_tab_content)
        self._clear_tab(self.camera_tab_content)
        self._clear_tab(self.location_tab_content)
        self._clear_tab(self.technical_tab_content)

        try:
            is_video = self._is_video(self.media_path)

            if is_video:
                self._load_video_metadata()
            else:
                self._load_photo_metadata()

        except Exception as e:
            print(f"[MediaLightbox] Error loading metadata: {e}")
            import traceback
            traceback.print_exc()
            self._add_metadata_field("⚠️ Error", str(e))

        # Load editable metadata into the Edit tab
        try:
            self._load_editable_metadata()
        except Exception as e:
            print(f"[MediaLightbox] Error loading editable metadata: {e}")

    def _load_photo_metadata(self):
        """Load comprehensive photo metadata into all tabs."""
        from services.exif_parser import EXIFParser
        from PySide6.QtWidgets import QPushButton, QCheckBox, QWidget, QHBoxLayout

        exif_parser = EXIFParser()
        data = exif_parser.parse_all_exif_fields(self.media_path)

        basic = data['basic']
        datetime_data = data['datetime']
        camera = data['camera']
        exposure = data['exposure']
        image = data['image']
        gps = data['gps']
        technical = data['technical']

        # CRITICAL FIX: Check database for GPS (manual edits override EXIF)
        try:
            from ui.location_editor_integration import get_photo_location
            db_lat, db_lon, db_location_name = get_photo_location(self.media_path)
            if db_lat is not None and db_lon is not None:
                # Database GPS exists (from manual edit or scan) - use it
                gps['latitude'] = db_lat
                gps['longitude'] = db_lon
                if db_location_name:
                    gps['location_name'] = db_location_name
                print(f"[MediaLightbox] Using GPS from database: ({db_lat:.6f}, {db_lon:.6f})")
        except Exception as e:
            print(f"[MediaLightbox] Could not load GPS from database: {e}")
            # Fall back to EXIF GPS data

        # === BASIC TAB ===
        layout = self.basic_tab_content['layout']

        # File info
        if 'filename' in basic:
            self._add_metadata_field("📄 Filename", basic['filename'], layout=layout)
        if 'file_size' in basic:
            size_mb = basic['file_size'] / (1024 * 1024)
            self._add_metadata_field("💾 File Size", f"{size_mb:.2f} MB", layout=layout)
        if 'format' in basic:
            self._add_metadata_field("📝 Format", basic['format'], layout=layout)
        if 'width' in basic and 'height' in basic:
            self._add_metadata_field("📐 Dimensions", f"{basic['width']} × {basic['height']} px", layout=layout)
        if 'mode' in basic:
            self._add_metadata_field("🎨 Color Mode", basic['mode'], layout=layout)

        # Dates
        if 'taken' in datetime_data:
            from datetime import datetime
            try:
                dt = datetime.strptime(datetime_data['taken'], "%Y:%m:%d %H:%M:%S")
                date_str = dt.strftime("%B %d, %Y at %I:%M %p")
                self._add_metadata_field("📅 Date Taken", date_str, layout=layout)
            except:
                self._add_metadata_field("📅 Date Taken", datetime_data['taken'], layout=layout)

        # Color palette
        try:
            from PIL import Image
            img = Image.open(self.media_path).convert('RGB')
            img.thumbnail((200, 200))
            palette = img.quantize(colors=5).getpalette()
            colors = []
            if palette:
                for i in range(0, min(15, len(palette)), 3):
                    colors.append((palette[i], palette[i+1], palette[i+2]))
            if colors:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(6)
                label_widget = QLabel("🎨 Color Palette")
                label_widget.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 9pt; font-weight: bold;")
                layout.addWidget(label_widget)
                for r,g,b in colors:
                    swatch = QLabel()
                    swatch.setFixedSize(24, 24)
                    swatch.setStyleSheet(f"background: rgb({r},{g},{b}); border: 1px solid #444; border-radius: 4px;")
                    row_layout.addWidget(swatch)
                layout.addWidget(row)
        except Exception as e:
            print(f"[MediaLightbox] Color palette error: {e}")

        # Motion photo indicator
        if hasattr(self, 'is_motion_photo') and self.is_motion_photo:
            self._add_metadata_field("🎬 Motion Photo", "Video paired (long-press to play)", layout=layout)

        # === CAMERA TAB ===
        layout = self.camera_tab_content['layout']

        if camera.get('make') or camera.get('model'):
            camera_str = f"{camera.get('make', '')} {camera.get('model', '')}".strip()
            self._add_metadata_field("📷 Camera", camera_str, layout=layout)
        if camera.get('body_serial'):
            self._add_metadata_field("🔢 Serial Number", camera['body_serial'], layout=layout)
        if camera.get('lens_make') or camera.get('lens_model'):
            lens_str = f"{camera.get('lens_make', '')} {camera.get('lens_model', '')}".strip()
            self._add_metadata_field("🔭 Lens", lens_str, layout=layout)
        if camera.get('lens_serial'):
            self._add_metadata_field("🔢 Lens Serial", camera['lens_serial'], layout=layout)

        # Exposure settings
        if exposure.get('iso'):
            self._add_metadata_field("🌟 ISO", str(exposure['iso']), layout=layout)
        if exposure.get('aperture'):
            if isinstance(exposure['aperture'], tuple):
                f_val = exposure['aperture'][0] / exposure['aperture'][1] if exposure['aperture'][1] != 0 else 0
                self._add_metadata_field("⚫ Aperture", f"f/{f_val:.1f}", layout=layout)
            else:
                self._add_metadata_field("⚫ Aperture", f"f/{exposure['aperture']}", layout=layout)
        if exposure.get('shutter_speed'):
            if isinstance(exposure['shutter_speed'], tuple):
                speed = exposure['shutter_speed'][0] / exposure['shutter_speed'][1] if exposure['shutter_speed'][1] != 0 else 0
                if speed < 1:
                    self._add_metadata_field("⏱️ Shutter Speed", f"1/{int(1/speed)}s", layout=layout)
                else:
                    self._add_metadata_field("⏱️ Shutter Speed", f"{speed}s", layout=layout)
            else:
                self._add_metadata_field("⏱️ Shutter Speed", str(exposure['shutter_speed']), layout=layout)
        if exposure.get('focal_length'):
            if isinstance(exposure['focal_length'], tuple):
                fl = exposure['focal_length'][0] / exposure['focal_length'][1] if exposure['focal_length'][1] != 0 else 0
                self._add_metadata_field("🔍 Focal Length", f"{fl:.0f}mm", layout=layout)
            else:
                self._add_metadata_field("🔍 Focal Length", f"{exposure['focal_length']}mm", layout=layout)
        if exposure.get('focal_length_35mm'):
            self._add_metadata_field("🔍 Focal Length (35mm)", f"{exposure['focal_length_35mm']}mm", layout=layout)
        if exposure.get('exposure_compensation'):
            if isinstance(exposure['exposure_compensation'], tuple):
                ev = exposure['exposure_compensation'][0] / exposure['exposure_compensation'][1] if exposure['exposure_compensation'][1] != 0 else 0
                self._add_metadata_field("📊 Exposure Comp.", f"{ev:+.1f} EV", layout=layout)
            else:
                self._add_metadata_field("📊 Exposure Comp.", str(exposure['exposure_compensation']), layout=layout)

        # Metering/Flash/WB
        if exposure.get('metering_mode'):
            modes = {0: "Unknown", 1: "Average", 2: "Center-weighted", 3: "Spot", 4: "Multi-spot", 5: "Pattern", 6: "Partial"}
            mode_str = modes.get(exposure['metering_mode'], str(exposure['metering_mode']))
            self._add_metadata_field("📏 Metering Mode", mode_str, layout=layout)
        if exposure.get('flash'):
            flash_val = exposure['flash']
            flash_str = "Fired" if (flash_val & 0x01) else "No Flash"
            self._add_metadata_field("⚡ Flash", flash_str, layout=layout)
        if exposure.get('white_balance'):
            wb = {0: "Auto", 1: "Manual"}
            self._add_metadata_field("⚪ White Balance", wb.get(exposure['white_balance'], str(exposure['white_balance'])), layout=layout)

        # Scene settings
        if exposure.get('exposure_program'):
            programs = {0: "Not defined", 1: "Manual", 2: "Program AE", 3: "Aperture priority", 4: "Shutter priority", 5: "Creative", 6: "Action", 7: "Portrait", 8: "Landscape"}
            self._add_metadata_field("📸 Exposure Program", programs.get(exposure['exposure_program'], str(exposure['exposure_program'])), layout=layout)
        if exposure.get('scene_type'):
            self._add_metadata_field("🎬 Scene Type", str(exposure['scene_type']), layout=layout)
        if exposure.get('contrast'):
            contrast_vals = {0: "Normal", 1: "Low", 2: "High"}
            self._add_metadata_field("🔲 Contrast", contrast_vals.get(exposure['contrast'], str(exposure['contrast'])), layout=layout)
        if exposure.get('saturation'):
            sat_vals = {0: "Normal", 1: "Low", 2: "High"}
            self._add_metadata_field("🌈 Saturation", sat_vals.get(exposure['saturation'], str(exposure['saturation'])), layout=layout)
        if exposure.get('sharpness'):
            sharp_vals = {0: "Normal", 1: "Soft", 2: "Hard"}
            self._add_metadata_field("🔪 Sharpness", sharp_vals.get(exposure['sharpness'], str(exposure['sharpness'])), layout=layout)

        # === LOCATION TAB ===
        layout = self.location_tab_content['layout']

        if gps.get('latitude') and gps.get('longitude'):
            lat = gps['latitude']
            lon = gps['longitude']

            self._add_metadata_field("📍 Coordinates", f"{lat:.6f}, {lon:.6f}", layout=layout)

            # Reverse geocoding toggle
            geocode_checkbox = QCheckBox("Show Address")
            geocode_checkbox.setStyleSheet("color: white; font-size: 10pt;")
            geocode_address_label = QLabel("")
            geocode_address_label.setStyleSheet("color: white; font-size: 10pt; padding-left: 8px;")
            geocode_address_label.setWordWrap(True)
            geocode_address_label.setVisible(False)

            def toggle_geocoding(checked):
                if checked:
                    geocode_address_label.setText("Loading address...")
                    geocode_address_label.setVisible(True)
                    address = self._reverse_geocode(lat, lon)
                    geocode_address_label.setText(address if address else "Address not found")
                else:
                    geocode_address_label.setVisible(False)

            geocode_checkbox.stateChanged.connect(lambda state: toggle_geocoding(state == 2))
            layout.addWidget(geocode_checkbox)
            layout.addWidget(geocode_address_label)

            # Open in maps button
            maps_btn = QPushButton("🗺️ Open in Google Maps")
            maps_btn.setStyleSheet("background: rgba(255,255,255,0.15); color: white; border: none; border-radius: 6px; padding: 6px 10px;")
            def _open_maps():
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                url = QUrl(f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}")
                QDesktopServices.openUrl(url)
            maps_btn.clicked.connect(_open_maps)
            layout.addWidget(maps_btn)

            # GPS Altitude
            if gps.get('altitude'):
                alt = gps['altitude']
                alt_ref = gps.get('altitude_ref', 0)
                alt_str = f"{alt:.1f}m {'below' if alt_ref == 1 else 'above'} sea level"
                self._add_metadata_field("⛰️ Altitude", alt_str, layout=layout)

            # GPS timestamp
            if gps.get('datestamp') and gps.get('timestamp'):
                self._add_metadata_field("🕐 GPS Time", f"{gps['datestamp']} {gps['timestamp']}", layout=layout)

            # GPS direction/speed
            if gps.get('image_direction'):
                self._add_metadata_field("🧭 Direction", f"{gps['image_direction']}°", layout=layout)
            if gps.get('speed'):
                speed_ref = gps.get('speed_ref', 'K')
                unit = {'K': 'km/h', 'M': 'mph', 'N': 'knots'}.get(speed_ref, speed_ref)
                self._add_metadata_field("🚗 Speed", f"{gps['speed']} {unit}", layout=layout)

            # GPS satellites
            if gps.get('satellites'):
                self._add_metadata_field("🛰️ Satellites", gps['satellites'], layout=layout)
        else:
            self._add_metadata_field("ℹ️ No GPS Data", "This photo has no location information", layout=layout)

        # === TECHNICAL TAB ===
        layout = self.technical_tab_content['layout']

        # All datetime fields
        if datetime_data.get('taken'):
            self._add_metadata_field("📅 Date Taken", datetime_data['taken'], layout=layout)
        if datetime_data.get('digitized'):
            self._add_metadata_field("📅 Date Digitized", datetime_data['digitized'], layout=layout)
        if datetime_data.get('modified'):
            self._add_metadata_field("📅 Date Modified", datetime_data['modified'], layout=layout)

        # Image properties
        if image.get('orientation'):
            orientations = {1: "Normal", 2: "Mirrored", 3: "Rotated 180°", 4: "Mirrored + Rotated 180°", 5: "Mirrored + Rotated 90° CCW", 6: "Rotated 90° CW", 7: "Mirrored + Rotated 90° CW", 8: "Rotated 90° CCW"}
            self._add_metadata_field("🔄 Orientation", orientations.get(image['orientation'], str(image['orientation'])), layout=layout)
        if image.get('color_space'):
            color_spaces = {1: "sRGB", 65535: "Uncalibrated"}
            self._add_metadata_field("🎨 Color Space", color_spaces.get(image['color_space'], str(image['color_space'])), layout=layout)
        if image.get('x_resolution') and image.get('y_resolution'):
            unit = {1: "None", 2: "inches", 3: "cm"}.get(image.get('resolution_unit', 2), "")
            self._add_metadata_field("📏 Resolution", f"{image['x_resolution']} × {image['y_resolution']} dpi", layout=layout)
        if image.get('compression'):
            self._add_metadata_field("🗜️ Compression", str(image['compression']), layout=layout)

        # Software/Attribution
        if technical.get('software'):
            self._add_metadata_field("💻 Software", technical['software'], layout=layout)
        if technical.get('artist'):
            self._add_metadata_field("👤 Artist", technical['artist'], layout=layout)
        if technical.get('copyright'):
            self._add_metadata_field("©️ Copyright", technical['copyright'], word_wrap=True, layout=layout)
        if technical.get('description'):
            self._add_metadata_field("📝 Description", technical['description'], word_wrap=True, layout=layout)
        if technical.get('user_comment'):
            self._add_metadata_field("💬 Comment", technical['user_comment'], word_wrap=True, layout=layout)

        # File path
        self._add_metadata_field("📁 File Path", self.media_path, word_wrap=True, layout=layout)

        # RAW exposure slider
        if self._is_raw(self.media_path) and hasattr(self, 'raw_support_enabled') and self.raw_support_enabled:
            self._add_exposure_slider()

    def _load_video_metadata(self):
        """Load comprehensive video metadata into all tabs."""
        from services.exif_parser import EXIFParser
        from PySide6.QtWidgets import QPushButton

        exif_parser = EXIFParser()
        data = exif_parser.extract_video_metadata_full(self.media_path)

        basic = data['basic']
        video = data['video']
        audio = data['audio']
        technical = data['technical']

        # === BASIC TAB ===
        layout = self.basic_tab_content['layout']

        if 'filename' in basic:
            self._add_metadata_field("📄 Filename", basic['filename'], layout=layout)
        if 'file_size' in basic:
            size_mb = basic['file_size'] / (1024 * 1024)
            self._add_metadata_field("💾 File Size", f"{size_mb:.2f} MB", layout=layout)
        if 'format' in basic:
            self._add_metadata_field("📦 Container", basic['format'], layout=layout)
        if 'format_long' in basic:
            self._add_metadata_field("📝 Format", basic['format_long'], layout=layout)

        if video.get('duration'):
            duration = video['duration']
            mins, secs = divmod(int(duration), 60)
            hrs, mins = divmod(mins, 60)
            if hrs > 0:
                dur_str = f"{hrs}:{mins:02d}:{secs:02d}"
            else:
                dur_str = f"{mins}:{secs:02d}"
            self._add_metadata_field("⏱️ Duration", dur_str, layout=layout)

        if video.get('width') and video.get('height'):
            res_str = f"{video['width']} × {video['height']} px"
            # Add quality label
            height = video['height']
            if height >= 2160:
                res_str += " (4K UHD)"
            elif height >= 1440:
                res_str += " (2K QHD)"
            elif height >= 1080:
                res_str += " (Full HD)"
            elif height >= 720:
                res_str += " (HD)"
            self._add_metadata_field("📐 Resolution", res_str, layout=layout)

        if technical.get('creation_time'):
            self._add_metadata_field("📅 Recorded", technical['creation_time'], layout=layout)

        # === CAMERA TAB (Video settings) ===
        layout = self.camera_tab_content['layout']

        if video.get('codec'):
            self._add_metadata_field("🎥 Video Codec", video['codec'], layout=layout)
        if video.get('codec_long'):
            self._add_metadata_field("📝 Codec Name", video['codec_long'], layout=layout)
        if video.get('fps'):
            self._add_metadata_field("🎬 Frame Rate", f"{video['fps']:.2f} fps", layout=layout)
        if video.get('bitrate'):
            bitrate_mbps = video['bitrate'] / 1000000
            self._add_metadata_field("📊 Bitrate", f"{bitrate_mbps:.2f} Mbps", layout=layout)
        if video.get('profile'):
            self._add_metadata_field("🎯 Profile", video['profile'], layout=layout)
        if video.get('pixel_format'):
            self._add_metadata_field("🎨 Pixel Format", video['pixel_format'], layout=layout)
        if video.get('color_space'):
            self._add_metadata_field("🌈 Color Space", video['color_space'], layout=layout)
        if video.get('rotation'):
            self._add_metadata_field("🔄 Rotation", f"{video['rotation']}°", layout=layout)

        # Audio info
        if audio.get('codec'):
            self._add_metadata_field("🔊 Audio Codec", audio['codec'], layout=layout)
        if audio.get('sample_rate'):
            sample_khz = int(audio['sample_rate']) / 1000
            self._add_metadata_field("🎵 Sample Rate", f"{sample_khz:.1f} kHz", layout=layout)
        if audio.get('channels'):
            channel_str = f"{audio['channels']} ({'Stereo' if audio['channels'] == 2 else 'Mono' if audio['channels'] == 1 else 'Multichannel'})"
            self._add_metadata_field("🔉 Channels", channel_str, layout=layout)
        if audio.get('bitrate'):
            audio_kbps = audio['bitrate'] / 1000
            self._add_metadata_field("📊 Audio Bitrate", f"{audio_kbps:.0f} kbps", layout=layout)

        # === LOCATION TAB ===
        layout = self.location_tab_content['layout']
        self._add_metadata_field("ℹ️ No GPS Data", "Videos don't typically contain GPS information", layout=layout)

        # === TECHNICAL TAB ===
        layout = self.technical_tab_content['layout']

        if technical.get('encoder'):
            self._add_metadata_field("⚙️ Encoder", technical['encoder'], layout=layout)
        if technical.get('title'):
            self._add_metadata_field("📌 Title", technical['title'], layout=layout)
        if technical.get('artist'):
            self._add_metadata_field("👤 Artist", technical['artist'], layout=layout)
        if technical.get('copyright'):
            self._add_metadata_field("©️ Copyright", technical['copyright'], word_wrap=True, layout=layout)
        if technical.get('comment'):
            self._add_metadata_field("💬 Comment", technical['comment'], word_wrap=True, layout=layout)

        # File path
        self._add_metadata_field("📁 File Path", self.media_path, word_wrap=True, layout=layout)

    def _add_metadata_field(self, label: str, value: str, word_wrap: bool = False, layout=None):
        """Add a metadata field to the specified layout (defaults to basic tab)."""
        if layout is None:
            layout = self.metadata_layout

        # Label
        label_widget = QLabel(label)
        label_widget.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7);
            font-size: 9pt;
            font-weight: bold;
        """)
        layout.addWidget(label_widget)

        # Value
        value_widget = QLabel(value)
        value_widget.setStyleSheet("""
            color: white;
            font-size: 10pt;
            padding-left: 8px;
        """)
        if word_wrap:
            value_widget.setWordWrap(True)
        layout.addWidget(value_widget)

    def _clear_tab(self, tab_info: dict):
        """Clear all widgets from a tab."""
        layout = tab_info['layout']
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _reverse_geocode(self, lat: float, lon: float) -> str:
        """
        Reverse geocode coordinates to address using Nominatim (OpenStreetMap).

        Returns formatted address or empty string on failure.
        """
        try:
            import urllib.request
            import json
            import urllib.parse

            # Use Nominatim API (free, no API key required)
            url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&addressdetails=1"

            req = urllib.request.Request(url, headers={
                'User-Agent': 'MemoryMate-PhotoFlow/1.0'
            })

            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())

                if 'display_name' in data:
                    return data['display_name']
                elif 'address' in data:
                    addr = data['address']
                    parts = []
                    for key in ['city', 'town', 'village', 'state', 'country']:
                        if key in addr:
                            parts.append(addr[key])
                    return ', '.join(parts) if parts else ''

        except Exception as e:
            print(f"[MediaLightbox] Reverse geocoding error: {e}")

        return ""

    def _add_exposure_slider(self):
        """
        PHASE C #1: Add exposure adjustment slider for RAW files.

        Range: -2.0 to +2.0 EV (stops)
        """
        from PySide6.QtWidgets import QSlider, QHBoxLayout
        from PySide6.QtCore import Qt

        # Section label
        label_widget = QLabel("☀️ Exposure Adjustment")
        label_widget.setStyleSheet("""
            color: rgba(255, 255, 255, 0.7);
            font-size: 9pt;
            font-weight: bold;
        """)
        self.metadata_layout.addWidget(label_widget)

        # Slider container
        slider_container = QWidget()
        slider_container.setStyleSheet("background: transparent;")
        slider_layout = QHBoxLayout(slider_container)
        slider_layout.setContentsMargins(8, 4, 8, 4)
        slider_layout.setSpacing(8)

        # Exposure slider (-2.0 to +2.0, in steps of 0.1)
        self.exposure_slider = QSlider(Qt.Horizontal)
        self.exposure_slider.setMinimum(-20)  # -2.0 * 10
        self.exposure_slider.setMaximum(20)   # +2.0 * 10
        self.exposure_slider.setValue(int(self.exposure_adjustment * 10))
        self.exposure_slider.setTickPosition(QSlider.TicksBelow)
        self.exposure_slider.setTickInterval(10)  # Tick every 1.0 EV
        self.exposure_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255, 255, 255, 0.2);
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: white;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #4CAF50;
            }
        """)
        self.exposure_slider.valueChanged.connect(self._on_exposure_changed)
        slider_layout.addWidget(self.exposure_slider)

        # Value label
        self.exposure_value_label = QLabel(f"{self.exposure_adjustment:+.1f} EV")
        self.exposure_value_label.setStyleSheet("color: white; font-size: 9pt; min-width: 50px;")
        slider_layout.addWidget(self.exposure_value_label)

        self.metadata_layout.addWidget(slider_container)

        print(f"[MediaLightbox] Exposure slider added (current: {self.exposure_adjustment:+.1f} EV)")

    def _on_exposure_changed(self, value: int):
        """
        PHASE C #1: Handle exposure slider change.

        Reloads the RAW file with new exposure.
        """
        # Convert slider value to EV (-2.0 to +2.0)
        new_exposure = value / 10.0

        if new_exposure != self.exposure_adjustment:
            self.exposure_adjustment = new_exposure

            # Update label
            if hasattr(self, 'exposure_value_label'):
                self.exposure_value_label.setText(f"{self.exposure_adjustment:+.1f} EV")

            # Reload photo with new exposure
            print(f"[MediaLightbox] Exposure changed to {self.exposure_adjustment:+.1f} EV, reloading...")
            self._load_photo_direct()

            # Reapply zoom after reload
            if hasattr(self, 'zoom_level') and hasattr(self, 'original_pixmap'):
                self._apply_zoom()

    def _position_nav_buttons(self):
        """Position navigation buttons relative to media area with responsive margins.
        
        Professional approach: Buttons positioned relative to scroll area (media content)
        rather than window edges, ensuring they stay visible regardless of panel state.
        """
        if not hasattr(self, 'prev_btn') or not hasattr(self, 'scroll_area'):
            print(f"[MediaLightbox] _position_nav_buttons: Missing attributes")
            return

        # Check if scroll area has valid size
        if self.scroll_area.width() == 0 or self.scroll_area.height() == 0:
            # Safety limit: stop retrying after 20 attempts (1 second total)
            if self._position_retry_count < 20:
                self._position_retry_count += 1
                print(f"[MediaLightbox] Scroll area not ready (retry {self._position_retry_count}/20)")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, self._position_nav_buttons)
            else:
                print(f"[MediaLightbox] ⚠️ Scroll area still not ready after 20 retries!")
            return

        # Reset retry counter on success
        self._position_retry_count = 0

        # Get scroll area viewport coordinates relative to dialog
        try:
            from PySide6.QtCore import QPoint
            viewport = self.scroll_area.viewport()
            # Get the top-left corner of scroll area viewport in dialog coordinates
            scroll_tl = viewport.mapTo(self, QPoint(0, 0))
        except Exception as e:
            print(f"[MediaLightbox] ⚠️ mapTo() failed: {e}, using fallback positioning")
            # Fallback: position relative to dialog with reasonable defaults
            scroll_tl = QPoint(0, self.top_toolbar.height() if hasattr(self, 'top_toolbar') else 0)

        scroll_w = viewport.width()
        scroll_h = viewport.height()

        # Button dimensions (responsive)
        btn_w = self.button_size_sm  # Use responsive button size
        btn_h = self.button_size_sm
        
        # Responsive margins based on screen size
        base_margin = max(8, self.margin_size)  # Minimum 8px, or responsive margin
        
        # Calculate vertical center position within scroll area
        center_y = scroll_tl.y() + (scroll_h // 2) - (btn_h // 2)
        
        # Position buttons relative to scroll area edges with responsive margins
        left_x = scroll_tl.x() + base_margin
        right_x = scroll_tl.x() + scroll_w - btn_w - base_margin
        
        # Ensure buttons stay within dialog bounds
        left_x = max(base_margin, left_x)
        right_x = min(self.width() - btn_w - base_margin, right_x)
        center_y = max(base_margin, center_y)
        
        # Apply positioning
        self.prev_btn.move(int(left_x), int(center_y))
        self.next_btn.move(int(right_x), int(center_y))

        # Ensure buttons are visible and on top
        self.prev_btn.show()
        self.next_btn.show()
        self.prev_btn.raise_()
        self.next_btn.raise_()

        print(f"[MediaLightbox] ✓ Nav buttons positioned: left={int(left_x)}, right={int(right_x)}, y={int(center_y)}")

    def _show_nav_buttons(self):
        """Show navigation buttons with instant visibility (always visible for usability)."""
        if not hasattr(self, 'nav_buttons_visible'):
            return
        if not self.nav_buttons_visible:
            self.nav_buttons_visible = True
            if hasattr(self, 'prev_btn_opacity'):
                self.prev_btn_opacity.setOpacity(1.0)
            if hasattr(self, 'next_btn_opacity'):
                self.next_btn_opacity.setOpacity(1.0)

        # Cancel any pending hide
        if hasattr(self, 'nav_hide_timer'):
            self.nav_hide_timer.stop()

    def _hide_nav_buttons(self):
        """Hide navigation buttons (auto-hide disabled for better UX)."""
        # PROFESSIONAL UX: Keep navigation buttons always visible
        # Users need immediate access to navigation, especially in photo galleries
        pass

    def enterEvent(self, event):
        """Show navigation buttons on mouse enter."""
        self._show_nav_buttons()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Hide navigation buttons after delay on mouse leave."""
        if hasattr(self, 'nav_hide_timer'):
            self.nav_hide_timer.start(500)  # Hide after 500ms
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        """
        Handle mouse press for panning and double-tap detection.

        NOTE: Nav buttons handle their own clicks - they're raised above this widget
        so button clicks go directly to buttons, not through this handler.
        """
        from PySide6.QtCore import Qt

        # PHASE B #2: Check for double-tap first
        if event.button() == Qt.LeftButton:
            if self._handle_double_tap(event.pos()):
                event.accept()
                return

        # Only pan with left mouse button on photos
        if event.button() == Qt.LeftButton and not self._is_video(self.media_path):
            # Check if we're over the scroll area and content is larger than viewport
            if self._is_content_panneable():
                self.is_panning = True
                self.pan_start_pos = event.pos()
                self.scroll_start_x = self.scroll_area.horizontalScrollBar().value()
                self.scroll_start_y = self.scroll_area.verticalScrollBar().value()
                self.scroll_area.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move for panning, cursor updates, toolbar reveal, and caption."""
        from PySide6.QtCore import Qt
        
        # Re-show caption on mouse movement (Google Photos behavior)
        if hasattr(self, 'media_caption') and self.media_caption.text():
            # Cancel auto-hide timer
            if hasattr(self, 'caption_hide_timer'):
                self.caption_hide_timer.stop()
            
            # Fade in if currently faded out
            if hasattr(self, 'caption_opacity') and self.caption_opacity.opacity() < 0.5:
                self._fade_in_caption()
            
            # Restart auto-hide timer
            if hasattr(self, 'caption_hide_timer'):
                self.caption_hide_timer.start()

        # PHASE A #3: Track mouse position for cursor-centered zoom
        self.last_mouse_pos = event.pos()

        # PROFESSIONAL AUTO-HIDE: Show toolbars on mouse movement
        self._show_toolbars()

        # Update cursor based on content size
        if not self._is_video(self.media_path) and self._is_content_panneable():
            if not self.is_panning:
                self.scroll_area.setCursor(Qt.OpenHandCursor)
        else:
            self.scroll_area.setCursor(Qt.ArrowCursor)

        # Perform panning if active
        if self.is_panning and self.pan_start_pos:
            delta = event.pos() - self.pan_start_pos

            # Update scroll bars
            self.scroll_area.horizontalScrollBar().setValue(self.scroll_start_x - delta.x())
            self.scroll_area.verticalScrollBar().setValue(self.scroll_start_y - delta.y())

            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release to stop panning."""
        from PySide6.QtCore import Qt

        if event.button() == Qt.LeftButton and self.is_panning:
            self.is_panning = False
            self.pan_start_pos = None

            # Restore cursor
            if self._is_content_panneable():
                self.scroll_area.setCursor(Qt.OpenHandCursor)
            else:
                self.scroll_area.setCursor(Qt.ArrowCursor)

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def _is_content_panneable(self) -> bool:
        """Check if content is larger than viewport (can be panned)."""
        if self._is_video(self.media_path):
            return False

        # Check if image is larger than scroll area viewport
        viewport = self.scroll_area.viewport()
        content = self.media_container

        return (content.width() > viewport.width() or
                content.height() > viewport.height())

    def _previous_media(self):
        """
        Navigate to previous media (photo or video).

        Phase 3 #5: Added smooth cross-fade transition.
        PHASE B #5: Save zoom state before navigating.
        """
        print(f"[MediaLightbox] Prev clicked at index={self.current_index} of {len(self.all_media)}")
        # PHASE B #5: Save current zoom state
        self._save_zoom_state()

        if self.current_index > 0:
            self.current_index -= 1
            self.media_path = self.all_media[self.current_index]
            print(f"[MediaLightbox] → Loading previous: {os.path.basename(self.media_path)} (idx={self.current_index})")
            self._load_media_with_transition()
        else:
            print("[MediaLightbox] Prev at start — no action")
    def _next_media(self):
        """
        Navigate to next media (photo or video).

        Phase 3 #5: Added smooth cross-fade transition.
        PHASE B #5: Save zoom state before navigating.
        """
        print(f"[MediaLightbox] Next clicked at index={self.current_index} of {len(self.all_media)}")
        # PHASE B #5: Save current zoom state
        self._save_zoom_state()

        if self.current_index < len(self.all_media) - 1:
            self.current_index += 1
            self.media_path = self.all_media[self.current_index]
            print(f"[MediaLightbox] → Loading next: {os.path.basename(self.media_path)} (idx={self.current_index})")
            self._load_media_with_transition()
        else:
            print("[MediaLightbox] Next at end — no action")
    def event(self, event):
        """
        PHASE 2 #10: Handle gesture events (swipe, pinch).
        """
        if event.type() == QEvent.Gesture:
            return self._handle_gesture(event)
        return super().event(event)

    def _handle_gesture(self, event):
        """PHASE 2 #10: Handle swipe and pinch gestures."""
        from PySide6.QtWidgets import QGestureEvent
        from PySide6.QtCore import Qt

        swipe = event.gesture(Qt.SwipeGesture)
        pinch = event.gesture(Qt.PinchGesture)

        if swipe:
            from PySide6.QtWidgets import QGesture
            if swipe.state() == Qt.GestureFinished:
                # Horizontal swipe for navigation
                if swipe.horizontalDirection() == QSwipeGesture.Left:
                    print("[MediaLightbox] Swipe left - next photo")
                    self._next_media()
                    return True
                elif swipe.horizontalDirection() == QSwipeGesture.Right:
                    print("[MediaLightbox] Swipe right - previous photo")
                    self._previous_media()
                    return True

        if pinch:
            if pinch.state() == Qt.GestureUpdated:
                # Pinch to zoom
                scale_factor = pinch.scaleFactor()
                if scale_factor > 1.0:
                    self._zoom_in()
                elif scale_factor < 1.0:
                    self._zoom_out()
                return True

        return False

    def _load_media_with_transition(self):
        """
        PHASE 3 #5: Load media with smooth fade transition.

        Cross-fades from current image to new image for professional feel.
        """
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve

        # If current content is video, bypass image fade and load directly
        if self._is_video(self.media_path):
            self._load_media()
            return

        # Ensure an opacity effect exists on the image label
        opacity_effect = self.image_label.graphicsEffect()
        if not opacity_effect:
            opacity_effect = QGraphicsOpacityEffect()
            self.image_label.setGraphicsEffect(opacity_effect)
            opacity_effect.setOpacity(1.0)

        # Fade out current image
        fade_out = QPropertyAnimation(opacity_effect, b"opacity")
        fade_out.setDuration(150)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.InCubic)
        fade_out.setParent(self)  # Keep object alive
        self._fade_out_animation = fade_out  # Strong reference

        # Load new media after fade-out completes
        def load_and_fade_in():
            self._load_media()
            # Fade in new image
            fade_in = QPropertyAnimation(opacity_effect, b"opacity")
            fade_in.setDuration(200)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)
            fade_in.setParent(self)  # Keep object alive
            self._fade_in_animation = fade_in  # Strong reference

            # Start Ken Burns after fade-in completes (slideshow only)
            if self.slideshow_active and self.slideshow_ken_burns:
                fade_in.finished.connect(self._start_ken_burns)

            fade_in.start()

        fade_out.finished.connect(load_and_fade_in)
        fade_out.start()

    def wheelEvent(self, event):
        """Handle mouse wheel for smooth continuous zoom (photos and videos)."""
        # PROFESSIONAL UX: Smooth zoom for both photos and videos
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            super().wheelEvent(event)
            return

        # Calculate zoom factor (1.15 per step - smooth and natural)
        factor = self.zoom_factor ** steps

        # Apply smooth zoom (works for both photos and videos)
        self._smooth_zoom(factor)
        event.accept()

    def _smooth_zoom(self, factor):
        """
        Apply smooth continuous zoom with animation (photos and videos).

        Phase 3 #5: Enhanced with smooth zoom animation instead of instant zoom.
        PHASE A #3: Cursor-centered zoom keeps point under mouse fixed.
        """
        # Check if we have content to zoom
        is_video = self._is_video(self.media_path)
        if not is_video and not self.original_pixmap:
            return

        # Block video zoom until a valid fit has established a real base_scale
        if is_video and not getattr(self, '_video_fit_ready', False):
            return

        # PHASE A #3: Store old zoom for cursor-centered calculation
        old_zoom = self.zoom_level

        # Calculate new zoom level
        new_zoom = self.zoom_level * factor

        # Enforce minimum and maximum zoom
        if is_video:
            min_zoom = 0.5  # Videos can zoom down to 50%
            max_zoom = 3.0  # Videos can zoom up to 300%
        else:
            min_zoom = max(0.1, self.fit_zoom_level * 0.25)  # Allow 25% of fit as minimum
            max_zoom = 10.0  # Maximum 1000% zoom

        new_zoom = max(min_zoom, min(new_zoom, max_zoom))

        # PHASE 3 #5: Animated zoom transition
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QVariantAnimation

        # Stop any existing zoom animation
        if hasattr(self, '_zoom_animation') and self._zoom_animation:
            self._zoom_animation.stop()

        # Create animation for zoom level
        self._zoom_animation = QVariantAnimation()
        self._zoom_animation.setDuration(200)  # 200ms smooth zoom
        self._zoom_animation.setStartValue(self.zoom_level)
        self._zoom_animation.setEndValue(new_zoom)
        self._zoom_animation.setEasingCurve(QEasingCurve.OutCubic)

        # Update zoom level during animation
        def update_zoom(value):
            self.zoom_level = value
            if self._is_video(self.media_path):
                # For video: keep edit_zoom_level in sync
                self.edit_zoom_level = self.zoom_level
                # Switch to custom zoom if user zoomed away from fit
                if abs(self.edit_zoom_level - 1.0) > 0.01:
                    self.zoom_mode = "custom"
                else:
                    self.zoom_mode = "fit"
                self._apply_video_zoom()
            else:
                # Switch to custom zoom mode if zooming from fit/fill (photos)
                if self.zoom_level > self.fit_zoom_level * 1.01:
                    self.zoom_mode = "custom"
                elif abs(self.zoom_level - self.fit_zoom_level) < 0.01:
                    self.zoom_mode = "fit"
                self._apply_zoom()

            self._update_zoom_status()

        self._zoom_animation.valueChanged.connect(update_zoom)

        # PHASE A #3: Apply cursor-centered scroll adjustment when zoom completes
        def on_zoom_complete():
            if not is_video:
                self._calculate_zoom_scroll_adjustment(old_zoom, new_zoom)

        self._zoom_animation.finished.connect(on_zoom_complete)
        self._zoom_animation.start()

    def _zoom_in(self):
        """Zoom in by one step (keyboard shortcut: +)."""
        self._smooth_zoom(self.zoom_factor)

    def _zoom_out(self):
        """Zoom out by one step (keyboard shortcut: -)."""
        self._smooth_zoom(1.0 / self.zoom_factor)

    def _apply_zoom(self):
        """Apply current zoom level to displayed photo."""
        from PySide6.QtCore import Qt  # Import at top to avoid UnboundLocalError

        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        # Calculate zoomed size
        zoomed_width = int(self.original_pixmap.width() * self.zoom_level)
        zoomed_height = int(self.original_pixmap.height() * self.zoom_level)

        # Scale pixmap
        scaled_pixmap = self.original_pixmap.scaled(
            zoomed_width, zoomed_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.resize(scaled_pixmap.size())  # CRITICAL: Size label to match pixmap for QScrollArea
        # CRITICAL: Also resize container to fit the image (QScrollArea needs this!)
        self.media_container.resize(scaled_pixmap.size())

        # Update cursor based on new zoom level
        if self._is_content_panneable():
            self.scroll_area.setCursor(Qt.OpenHandCursor)
        else:
            self.scroll_area.setCursor(Qt.ArrowCursor)

    def _apply_video_zoom(self):
        """Apply current zoom level to video preview using view transform.

        GUARD: Blocked until _video_fit_ready is True. This prevents
        multiplying a "dot" base_scale before a valid fit has happened,
        which is the root cause of the "video appears as a dot" bug.
        """
        try:
            if not hasattr(self, 'video_graphics_view') or not self.video_graphics_view:
                return
            # Block zoom until a valid fit has computed a real base_scale
            if not getattr(self, '_video_fit_ready', False):
                return
            # Clamp and apply using base fit scale
            self.edit_zoom_level = max(0.25, min(getattr(self, 'edit_zoom_level', 1.0), 4.0))
            base = getattr(self, 'video_base_scale', 1.0)
            from PySide6.QtGui import QTransform
            t = QTransform()
            t.scale(base * self.edit_zoom_level, base * self.edit_zoom_level)
            self.video_graphics_view.setTransform(t)
            from PySide6.QtWidgets import QGraphicsView
            self.video_graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)
            # Mirror level into generic zoom_level for status labels
            self.zoom_level = self.edit_zoom_level
            if hasattr(self, '_update_zoom_status'):
                self._update_zoom_status()
            print(f"[MediaLightbox] Video zoom applied: {int(self.edit_zoom_level * 100)}%")
        except Exception as e:
            print(f"[MediaLightbox] Video zoom apply failed: {e}")

    def _zoom_to_fit(self):
        """Zoom to fit window (Keyboard: 0) - Letterboxing if needed."""
        if self._is_video(self.media_path):
            self.edit_zoom_level = 1.0
            self.zoom_mode = "fit"
            self._last_video_fit_sig = None  # Force recompute through dedup
            self._do_fit_video_view()  # Recalculate base_scale for current viewport
            self._update_zoom_status()
            return

        self.zoom_mode = "fit"
        self._fit_to_window()
        self._update_zoom_status()

    def _zoom_to_actual(self):
        """Zoom to 100% actual size (Keyboard: 1) - 1:1 pixel mapping."""
        if self._is_video(self.media_path):
            base = getattr(self, 'video_base_scale', 1.0)
            # 1:1 pixel mapping: total scale should be 1.0, so edit_zoom = 1/base
            self.edit_zoom_level = 1.0 / base if base > 0 else 1.0
            self.zoom_mode = "actual"
            self._apply_video_zoom()
            self._update_zoom_status()
            return

        self.zoom_mode = "actual"
        self.zoom_level = 1.0
        self._apply_zoom()
        self._update_zoom_status()

    def _zoom_to_fill(self):
        """Zoom to fill window (may crop edges to avoid letterboxing)."""
        if self._is_video(self.media_path):
            return

        self.zoom_mode = "fill"
        self._fill_window()
        self._update_zoom_status()

    def _fit_to_window(self):
        """Fit entire image to window (letterboxing if needed)."""
        from PySide6.QtCore import Qt  # Import at top to avoid UnboundLocalError

        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        # Get viewport size
        viewport_size = self.scroll_area.viewport().size()

        # Scale to fit (maintains aspect ratio)
        scaled_pixmap = self.original_pixmap.scaled(
            viewport_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.resize(scaled_pixmap.size())
        self.media_container.resize(scaled_pixmap.size())

        # Calculate actual zoom level for display
        self.zoom_level = scaled_pixmap.width() / self.original_pixmap.width()
        self.fit_zoom_level = self.zoom_level  # Store for smooth zoom minimum

    def _fill_window(self):
        """Fill window completely (may crop edges to avoid letterboxing)."""
        from PySide6.QtCore import Qt  # Import at top to avoid UnboundLocalError

        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        # Get viewport size
        viewport_size = self.scroll_area.viewport().size()

        # Calculate zoom to fill (crops edges if needed)
        width_ratio = viewport_size.width() / self.original_pixmap.width()
        height_ratio = viewport_size.height() / self.original_pixmap.height()
        fill_ratio = max(width_ratio, height_ratio)  # Use larger ratio to fill

        zoomed_width = int(self.original_pixmap.width() * fill_ratio)
        zoomed_height = int(self.original_pixmap.height() * fill_ratio)

        scaled_pixmap = self.original_pixmap.scaled(
            zoomed_width, zoomed_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.resize(scaled_pixmap.size())
        self.media_container.resize(scaled_pixmap.size())

        self.zoom_level = fill_ratio

    def _update_zoom_status(self):
        """Update status label with professional zoom indicators."""
        status_parts = []

        # Zoom indicator (for both photos and videos)
        if self._is_video(self.media_path):
            # Show zoom percentage for videos
            zoom_pct = int(self.zoom_level * 100)
            status_parts.append(f"🔍 {zoom_pct}%")
        else:
            # Show mode or percentage for photos
            if self.zoom_mode == "fit":
                status_parts.append("🔍 Fit to Window")
            elif self.zoom_mode == "fill":
                status_parts.append("🔍 Fill Window")
            elif self.zoom_mode == "actual":
                status_parts.append("🔍 100% (Actual Size)")
            else:
                zoom_pct = int(self.zoom_level * 100)
                status_parts.append(f"🔍 {zoom_pct}%")

        # Slideshow indicator
        if self.slideshow_active:
            interval_s = self.slideshow_interval / 1000
            kb_label = " | Ken Burns" if self.slideshow_ken_burns else ""
            pl = getattr(self, '_slideshow_playlist', None)
            pl_label = f" | {len(pl)} photos" if pl else ""
            status_parts.append(f"⏵ Slideshow ({interval_s:.0f}s{kb_label}{pl_label})")
        # Slideshow music indicator
        if (getattr(self, 'slideshow_music_player', None) and
                self.slideshow_music_player.playbackState() == QMediaPlayer.PlayingState):
            music_name = os.path.basename(self.slideshow_music_path or "")
            status_parts.append(f"♫ {music_name}")
        # Auto-Enhance indicator
        if getattr(self, 'auto_enhance_on', False):
            status_parts.append("✨ Enhance")
        # Preset indicator
        if getattr(self, 'current_preset', None):
            status_parts.append(f"🎨 {self.current_preset.title()}")

        self.status_label.setText(" | ".join(status_parts) if status_parts else "")

    def _update_status_label(self):
        """Update status label with zoom level or slideshow status."""
        status_parts = []

        # Zoom indicator (for both photos and videos)
        zoom_pct = int(self.zoom_level * 100)
        if not self._is_video(self.media_path):
            if self.zoom_mode == "fit":
                status_parts.append("Fit")
            elif self.zoom_mode == "fill":
                status_parts.append("Fill")
            else:
                status_parts.append(f"{zoom_pct}%")
        else:
            # Show zoom percentage for videos
            status_parts.append(f"{zoom_pct}%")

        # Slideshow indicator
        if self.slideshow_active:
            interval_s = self.slideshow_interval / 1000
            kb_label = " | Ken Burns" if self.slideshow_ken_burns else ""
            pl = getattr(self, '_slideshow_playlist', None)
            pl_label = f" | {len(pl)} photos" if pl else ""
            status_parts.append(f"⏵ Slideshow ({interval_s:.0f}s{kb_label}{pl_label})")
        # Slideshow music indicator
        if (getattr(self, 'slideshow_music_player', None) and
                self.slideshow_music_player.playbackState() == QMediaPlayer.PlayingState):
            music_name = os.path.basename(self.slideshow_music_path or "")
            status_parts.append(f"♫ {music_name}")
        # Auto-Enhance indicator
        if getattr(self, 'auto_enhance_on', False):
            status_parts.append("✨ Enhance")
        # Preset indicator
        if getattr(self, 'current_preset', None):
            status_parts.append(f"🎨 {self.current_preset.title()}")

        self.status_label.setText(" | ".join(status_parts) if status_parts else "")

    def _toggle_slideshow(self):
        """Toggle slideshow mode (iPhone/Google Photos style)."""
        if self.slideshow_active:
            # Stop slideshow
            self.slideshow_active = False
            if self.slideshow_timer:
                self.slideshow_timer.stop()
            self._stop_ken_burns()
            self._stop_slideshow_music()
            self._slideshow_playlist = None
            self._slideshow_playlist_pos = 0
            self.slideshow_btn.setText("▶")
            self.slideshow_btn.setToolTip("Slideshow (S)")

            # Restore fit-to-window after Ken Burns
            if not self._is_video(self.media_path):
                self.zoom_mode = "fit"
                self._fit_to_window()

            # Show scrollbars again
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            # Build curated playlist if photos are selected
            if self.slideshow_selected_indices:
                sorted_indices = sorted(self.slideshow_selected_indices)
                self._slideshow_playlist = [
                    (idx, self.all_media[idx]) for idx in sorted_indices
                    if idx < len(self.all_media)
                ]
                if not self._slideshow_playlist:
                    self._slideshow_playlist = None
                else:
                    # Jump to first selected photo
                    first_idx, first_path = self._slideshow_playlist[0]
                    self._slideshow_playlist_pos = 0
                    self.current_index = first_idx
                    self.media_path = first_path
                    self._load_media()
                    print(f"[MediaLightbox] Curated slideshow: {len(self._slideshow_playlist)} photos")
            else:
                self._slideshow_playlist = None

            # Start slideshow
            self.slideshow_active = True
            self._ken_burns_direction = 0
            from PySide6.QtCore import QTimer
            if not self.slideshow_timer:
                self.slideshow_timer = QTimer()
                self.slideshow_timer.timeout.connect(self._slideshow_advance)
            self.slideshow_timer.start(self.slideshow_interval)
            self.slideshow_btn.setText("⏸")
            self.slideshow_btn.setToolTip("Pause Slideshow (S)")

            # Hide slideshow editor if visible
            if self.slideshow_editor_visible:
                self._toggle_slideshow_editor()

            # Hide scrollbars for clean cinematic look
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            # Start Ken Burns on current photo
            if self.slideshow_ken_burns and not self._is_video(self.media_path):
                QTimer.singleShot(100, self._start_ken_burns)

            # Start music if configured
            self._start_slideshow_music()

        self._update_status_label()

    def _toggle_auto_enhance(self):
        """Toggle auto-enhance for photos; non-destructive preview."""
        # Disable for videos
        if self._is_video(self.media_path):
            self.auto_enhance_on = False
            return
        self.auto_enhance_on = not self.auto_enhance_on
        if hasattr(self, 'enhance_btn'):
            self.enhance_btn.setText("✨ Enhanced" if self.auto_enhance_on else "✨ Enhance")
        if hasattr(self, 'suggestion_enhance_btn'):
            self.suggestion_enhance_btn.setText(("✓ " if self.auto_enhance_on else "") + "✨ Enhance")
        # Clear preset selection text when enabling enhance
        if self.auto_enhance_on:
            for btn_attr, label in [("suggestion_dynamic_btn", "⚡ Dynamic"), ("suggestion_warm_btn", "🌤 Warm"), ("suggestion_cool_btn", "❄️ Cool")]:
                if hasattr(self, btn_attr):
                    getattr(self, btn_attr).setText(label)

    def _apply_enhance_render(self):
        """Apply current enhance state to the displayed photo (fit to viewport)."""
        from PySide6.QtCore import Qt
        if not hasattr(self, 'image_label'):
            return
        if self.current_preset:
            pixmap = self._get_preset_pixmap(self.media_path, self.current_preset)
        elif self.auto_enhance_on:
            pixmap = self._get_enhanced_pixmap(self.media_path)
        else:
            pixmap = self.original_pixmap if (hasattr(self, 'original_pixmap') and self.original_pixmap) else None
        if pixmap is None or pixmap.isNull():
            # Fallback: reload basic image
            try:
                from app_services import get_thumbnail
                pixmap = get_thumbnail(self.media_path, max(self.scroll_area.viewport().width(), self.scroll_area.viewport().height()))
            except Exception:
                return
        viewport_size = self.scroll_area.viewport().size()
        scaled = pixmap.scaled(viewport_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())
        self.media_container.resize(scaled.size())
        base = pixmap
        self.zoom_level = scaled.width() / base.width()
        self.fit_zoom_level = self.zoom_level
        self.zoom_mode = "fit"

    def _get_enhanced_pixmap(self, path: str):
        """Return cached enhanced pixmap or generate via PIL."""
        if path in getattr(self, 'enhanced_cache', {}):
            return self.enhanced_cache[path]
        from PIL import Image, ImageOps, ImageEnhance
        import io
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            # Gentle, safe enhancements
            img = ImageEnhance.Brightness(img).enhance(1.06)
            img = ImageEnhance.Contrast(img).enhance(1.08)
            img = ImageEnhance.Color(img).enhance(1.07)
            img = ImageEnhance.Sharpness(img).enhance(1.10)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            from PySide6.QtGui import QPixmap
            px = QPixmap()
            px.loadFromData(buf.read())
            buf.close()
            img.close()
            self.enhanced_cache[path] = px
            return px
        except Exception as e:
            print(f"[MediaLightbox] Enhance generate error: {e}")
            return self.original_pixmap if hasattr(self, 'original_pixmap') else None

    def _set_preset(self, preset: str):
        """Set or clear a preset and re-render."""
        if self._is_video(self.media_path):
            return
        if getattr(self, 'current_preset', None) == preset:
            self.current_preset = None
        else:
            self.current_preset = preset
        # Presets are exclusive with auto-enhance
        self.auto_enhance_on = False
        if hasattr(self, 'enhance_btn'):
            self.enhance_btn.setText("✨ Enhance")
        # Update button labels to show selection
        names = {"dynamic": "Dynamic", "warm": "Warm", "cool": "Cool"}
        for key, attr in [("dynamic", "dynamic_btn"), ("warm", "warm_btn"), ("cool", "cool_btn")]:
            if hasattr(self, attr):
                btn = getattr(self, attr)
                btn.setText(("✓ " if self.current_preset == key else "") + names[key])
        # Update panel button labels to show selection
        panel_names = {"dynamic": "⚡ Dynamic", "warm": "🌤 Warm", "cool": "❄️ Cool"}
        for key, attr in [("dynamic", "suggestion_dynamic_btn"), ("warm", "suggestion_warm_btn"), ("cool", "suggestion_cool_btn")]:
            if hasattr(self, attr):
                btn = getattr(self, attr)
                btn.setText(("✓ " if self.current_preset == key else "") + panel_names[key])
        if hasattr(self, 'suggestion_enhance_btn'):
            self.suggestion_enhance_btn.setText("✨ Enhance")
        try:
            self._apply_enhance_render()
        except Exception as e:
            print(f"[MediaLightbox] Preset apply error: {e}")
        self._update_status_label()

    def _get_preset_pixmap(self, path: str, preset: str):
        key = (path, preset)
        cache = getattr(self, 'preset_cache', {})
        if key in cache:
            return cache[key]
        px = self._generate_preset_pixmap(path, preset)
        if px is not None:
            self.preset_cache[key] = px
        return px

    def _generate_preset_pixmap(self, path: str, preset: str):
        from PIL import Image, ImageOps, ImageEnhance
        import io
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            if preset == 'dynamic':
                img = ImageEnhance.Brightness(img).enhance(1.05)
                img = ImageEnhance.Contrast(img).enhance(1.12)
                img = ImageEnhance.Color(img).enhance(1.15)
                img = ImageEnhance.Sharpness(img).enhance(1.15)
            elif preset == 'warm':
                img = ImageEnhance.Brightness(img).enhance(1.03)
                img = ImageEnhance.Contrast(img).enhance(1.06)
                img = ImageEnhance.Color(img).enhance(1.10)
                from PIL import Image as PILImage
                overlay = PILImage.new('RGB', img.size, (255, 140, 0))  # orange
                img = Image.blend(img, overlay, 0.08)
            elif preset == 'cool':
                img = ImageEnhance.Brightness(img).enhance(1.02)
                img = ImageEnhance.Contrast(img).enhance(1.06)
                img = ImageEnhance.Color(img).enhance(1.03)
                from PIL import Image as PILImage
                overlay = PILImage.new('RGB', img.size, (58, 139, 255))  # blue
                img = Image.blend(img, overlay, 0.08)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            from PySide6.QtGui import QPixmap
            px = QPixmap()
            px.loadFromData(buf.read())
            buf.close()
            img.close()
            return px
        except Exception as e:
            print(f"[MediaLightbox] Preset gen error: {e}")
            return None

    def _slideshow_advance(self):
        """Advance to next media in slideshow (with playlist and loop support)."""
        if not self.slideshow_active:
            return

        # Stop Ken Burns before transitioning
        self._stop_ken_burns()
        self._save_zoom_state()

        if self._slideshow_playlist:
            # Curated playlist mode: navigate through selected photos only
            self._slideshow_playlist_pos += 1
            if self._slideshow_playlist_pos < len(self._slideshow_playlist):
                idx, path = self._slideshow_playlist[self._slideshow_playlist_pos]
                self.current_index = idx
                self.media_path = path
                self._load_media_with_transition()
            elif self.slideshow_loop and len(self._slideshow_playlist) > 1:
                # Loop back to first selected photo
                self._slideshow_playlist_pos = 0
                idx, path = self._slideshow_playlist[0]
                self.current_index = idx
                self.media_path = path
                self._load_media_with_transition()
            else:
                self._toggle_slideshow()
        else:
            # Normal mode: navigate through all media
            if self.current_index < len(self.all_media) - 1:
                self._next_media()
            elif self.slideshow_loop and len(self.all_media) > 1:
                self.current_index = 0
                self.media_path = self.all_media[0]
                self._load_media_with_transition()
            else:
                self._toggle_slideshow()

    # ── Ken Burns Effect (iPhone Photos style slow pan + zoom) ──

    def _start_ken_burns(self):
        """Start Ken Burns effect: scale to fill + gentle pan animation."""
        if not self.slideshow_active or not self.slideshow_ken_burns:
            return
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        if self._is_video(self.media_path):
            return

        from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt

        self._stop_ken_burns()

        viewport = self.scroll_area.viewport().size()
        img_w = self.original_pixmap.width()
        img_h = self.original_pixmap.height()

        if img_w == 0 or img_h == 0:
            return

        # Calculate fill ratio (image fills viewport, edges may be cropped)
        width_ratio = viewport.width() / img_w
        height_ratio = viewport.height() / img_h
        fill_ratio = max(width_ratio, height_ratio)

        # Scale to fill + 10% extra for pan room
        kb_zoom = fill_ratio * 1.10
        new_w = int(img_w * kb_zoom)
        new_h = int(img_h * kb_zoom)

        scaled = self.original_pixmap.scaled(
            new_w, new_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())
        self.media_container.resize(scaled.size())
        self.zoom_level = kb_zoom
        self.zoom_mode = "custom"

        # Get scroll ranges for panning
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()

        # Force layout update so scrollbar ranges are correct
        self.scroll_area.viewport().update()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        h_max = h_bar.maximum()
        v_max = v_bar.maximum()

        # Cycle through 4 pan directions for variety
        direction = self._ken_burns_direction % 4
        self._ken_burns_direction += 1

        # Set start/end positions based on direction
        if direction == 0:
            # Top-left → bottom-right
            h_start, v_start = 0, 0
            h_end, v_end = h_max, v_max
        elif direction == 1:
            # Bottom-right → top-left
            h_start, v_start = h_max, v_max
            h_end, v_end = 0, 0
        elif direction == 2:
            # Center → top-right (gentle drift)
            h_start, v_start = h_max // 2, v_max // 2
            h_end, v_end = h_max, 0
        else:
            # Top-right → bottom-left
            h_start, v_start = h_max, 0
            h_end, v_end = 0, v_max

        # Set initial position
        h_bar.setValue(h_start)
        v_bar.setValue(v_start)

        # Animation duration: slideshow interval minus transition time
        duration = max(1000, self.slideshow_interval - 400)

        # Animate horizontal pan
        if h_max > 0:
            self._kb_h_anim = QPropertyAnimation(h_bar, b"value")
            self._kb_h_anim.setDuration(duration)
            self._kb_h_anim.setStartValue(h_start)
            self._kb_h_anim.setEndValue(h_end)
            self._kb_h_anim.setEasingCurve(QEasingCurve.InOutSine)
            self._kb_h_anim.start()

        # Animate vertical pan
        if v_max > 0:
            self._kb_v_anim = QPropertyAnimation(v_bar, b"value")
            self._kb_v_anim.setDuration(duration)
            self._kb_v_anim.setStartValue(v_start)
            self._kb_v_anim.setEndValue(v_end)
            self._kb_v_anim.setEasingCurve(QEasingCurve.InOutSine)
            self._kb_v_anim.start()

    def _stop_ken_burns(self):
        """Stop any running Ken Burns animation."""
        if self._kb_h_anim:
            self._kb_h_anim.stop()
            self._kb_h_anim = None
        if self._kb_v_anim:
            self._kb_v_anim.stop()
            self._kb_v_anim = None

    # ── Slideshow Music (background audio during slideshow) ──

    def _start_slideshow_music(self):
        """Start playing slideshow background music if configured."""
        if not self.slideshow_music_path:
            return

        from PySide6.QtCore import QUrl

        if not self.slideshow_music_player:
            self.slideshow_music_player = QMediaPlayer(self)
            self.slideshow_music_output = QAudioOutput(self)
            self.slideshow_music_player.setAudioOutput(self.slideshow_music_output)
            # Loop music continuously
            self.slideshow_music_player.setLoops(QMediaPlayer.Infinite)

        self.slideshow_music_output.setVolume(self.slideshow_music_volume)
        self.slideshow_music_player.setSource(QUrl.fromLocalFile(self.slideshow_music_path))
        self.slideshow_music_player.play()
        print(f"[MediaLightbox] Slideshow music started: {os.path.basename(self.slideshow_music_path)}")
        self._update_status_label()

    def _stop_slideshow_music(self):
        """Stop slideshow background music."""
        if self.slideshow_music_player:
            self.slideshow_music_player.stop()
            from PySide6.QtCore import QUrl
            self.slideshow_music_player.setSource(QUrl())
            print("[MediaLightbox] Slideshow music stopped")
            self._update_status_label()

    def _pick_slideshow_music(self):
        """Open file picker to select music file for slideshow."""
        from PySide6.QtWidgets import QFileDialog

        music_filter = (
            "Audio Files (*.mp3 *.wav *.aac *.ogg *.flac *.wma *.m4a *.opus);;"
            "MP3 Files (*.mp3);;"
            "All Files (*)"
        )

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Slideshow Music",
            "",
            music_filter
        )

        if file_path:
            self.slideshow_music_path = file_path
            print(f"[MediaLightbox] Music selected: {file_path}")

            # If slideshow is already running, start the music immediately
            if self.slideshow_active:
                self._stop_slideshow_music()
                self._start_slideshow_music()

            return True
        return False

    # ── Slideshow Settings Panel ──

    def _show_slideshow_settings(self):
        """Show slideshow settings popup (speed, Ken Burns, loop, music)."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel,
            QSlider, QCheckBox, QPushButton, QGroupBox, QComboBox
        )
        from PySide6.QtCore import Qt

        dialog = QDialog(self)
        dialog.setWindowTitle("Slideshow Settings")
        dialog.setFixedWidth(380)
        dialog.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: white; }
            QLabel { color: white; }
            QGroupBox { color: white; border: 1px solid #444; border-radius: 6px;
                        margin-top: 8px; padding-top: 14px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; }
            QPushButton { background: rgba(255,255,255,0.15); color: white;
                          border: none; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background: rgba(255,255,255,0.25); }
            QSlider::groove:horizontal { background: #444; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: white; width: 14px; height: 14px;
                                          border-radius: 7px; margin: -5px 0; }
            QCheckBox { color: white; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QComboBox { background: rgba(255,255,255,0.15); color: white;
                        border: 1px solid #555; border-radius: 4px; padding: 4px 8px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #2d2d2d; color: white;
                                           selection-background-color: #4a90d9; }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        # ── Speed Group ──
        speed_group = QGroupBox("Speed")
        speed_layout = QVBoxLayout(speed_group)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Interval:"))

        speed_combo = QComboBox()
        speed_options = [
            ("2 seconds (Fast)", 2000),
            ("3 seconds", 3000),
            ("5 seconds", 5000),
            ("7 seconds", 7000),
            ("10 seconds (Slow)", 10000),
        ]
        current_idx = 1  # Default to 3s
        for i, (label, ms) in enumerate(speed_options):
            speed_combo.addItem(label, ms)
            if ms == self.slideshow_interval:
                current_idx = i
        speed_combo.setCurrentIndex(current_idx)
        speed_row.addWidget(speed_combo, 1)
        speed_layout.addLayout(speed_row)

        layout.addWidget(speed_group)

        # ── Effects Group ──
        effects_group = QGroupBox("Effects")
        effects_layout = QVBoxLayout(effects_group)

        ken_burns_cb = QCheckBox("Ken Burns Effect (slow pan + zoom)")
        ken_burns_cb.setChecked(self.slideshow_ken_burns)
        effects_layout.addWidget(ken_burns_cb)

        loop_cb = QCheckBox("Loop Slideshow")
        loop_cb.setChecked(self.slideshow_loop)
        effects_layout.addWidget(loop_cb)

        layout.addWidget(effects_group)

        # ── Music Group ──
        music_group = QGroupBox("Background Music")
        music_layout = QVBoxLayout(music_group)

        # Current music file
        music_file_row = QHBoxLayout()
        if self.slideshow_music_path:
            music_name = os.path.basename(self.slideshow_music_path)
            music_file_label = QLabel(f"♫ {music_name}")
        else:
            music_file_label = QLabel("No music selected")
        music_file_label.setStyleSheet("color: #aaa;")
        music_file_row.addWidget(music_file_label, 1)

        pick_btn = QPushButton("Browse...")
        music_file_row.addWidget(pick_btn)
        music_layout.addLayout(music_file_row)

        # Volume slider
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Volume:"))

        vol_slider = QSlider(Qt.Horizontal)
        vol_slider.setMinimum(0)
        vol_slider.setMaximum(100)
        vol_slider.setValue(int(self.slideshow_music_volume * 100))
        vol_row.addWidget(vol_slider, 1)

        vol_label = QLabel(f"{int(self.slideshow_music_volume * 100)}%")
        vol_label.setMinimumWidth(35)
        vol_row.addWidget(vol_label)
        music_layout.addLayout(vol_row)

        def on_vol_changed(val):
            vol_label.setText(f"{val}%")

        vol_slider.valueChanged.connect(on_vol_changed)

        # Clear music button
        clear_row = QHBoxLayout()
        clear_row.addStretch()
        clear_music_btn = QPushButton("Clear Music")
        clear_music_btn.setStyleSheet(
            "QPushButton { background: rgba(231,76,60,0.3); }"
            "QPushButton:hover { background: rgba(231,76,60,0.5); }"
        )
        clear_row.addWidget(clear_music_btn)
        music_layout.addLayout(clear_row)

        layout.addWidget(music_group)

        # File picker callback
        def on_pick_music():
            if self._pick_slideshow_music():
                music_file_label.setText(f"♫ {os.path.basename(self.slideshow_music_path)}")
                music_file_label.setStyleSheet("color: #2ecc71;")

        pick_btn.clicked.connect(on_pick_music)

        # Clear music callback
        def on_clear_music():
            self._stop_slideshow_music()
            self.slideshow_music_path = None
            music_file_label.setText("No music selected")
            music_file_label.setStyleSheet("color: #aaa;")

        clear_music_btn.clicked.connect(on_clear_music)

        # ── OK / Cancel buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Apply")
        ok_btn.setStyleSheet(
            "QPushButton { background: rgba(52,152,219,0.5); }"
            "QPushButton:hover { background: rgba(52,152,219,0.7); }"
        )
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

        def on_apply():
            # Apply settings
            self.slideshow_interval = speed_combo.currentData()
            self.slideshow_ken_burns = ken_burns_cb.isChecked()
            self.slideshow_loop = loop_cb.isChecked()
            self.slideshow_music_volume = vol_slider.value() / 100.0

            # Update running timer if slideshow is active
            if self.slideshow_active and self.slideshow_timer:
                self.slideshow_timer.setInterval(self.slideshow_interval)

            # Update music volume if playing
            if self.slideshow_music_output:
                self.slideshow_music_output.setVolume(self.slideshow_music_volume)

            self._update_status_label()
            print(
                f"[MediaLightbox] Slideshow settings: interval={self.slideshow_interval}ms, "
                f"ken_burns={self.slideshow_ken_burns}, loop={self.slideshow_loop}, "
                f"music={'yes' if self.slideshow_music_path else 'no'}"
            )
            dialog.accept()

        ok_btn.clicked.connect(on_apply)

        dialog.exec()

    # ── Slideshow Editor (curated photo selection) ──

    def _create_slideshow_editor(self) -> QWidget:
        """Create the slideshow editor panel with selectable thumbnails."""
        from PySide6.QtWidgets import QScrollArea, QHBoxLayout

        panel = QWidget()
        panel.setFixedHeight(160)
        panel.setStyleSheet("""
            QWidget#slideshow_editor_panel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 0, 0, 0.95),
                    stop:1 rgba(0, 0, 0, 0.98));
                border-top: 1px solid rgba(255, 255, 255, 0.15);
            }
        """)
        panel.setObjectName("slideshow_editor_panel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(6)

        # Top row: action buttons + selection count
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        toolbar_btn_style = """
            QPushButton {
                background: rgba(255, 255, 255, 0.12);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11pt;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.22); }
        """

        select_all_btn = QPushButton("Select All")
        select_all_btn.setStyleSheet(toolbar_btn_style)
        select_all_btn.setFocusPolicy(Qt.NoFocus)
        select_all_btn.clicked.connect(self._slideshow_select_all)
        top_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setStyleSheet(toolbar_btn_style)
        deselect_all_btn.setFocusPolicy(Qt.NoFocus)
        deselect_all_btn.clicked.connect(self._slideshow_deselect_all)
        top_row.addWidget(deselect_all_btn)

        top_row.addStretch()

        self._slideshow_count_label = QLabel("")
        self._slideshow_count_label.setStyleSheet(
            "color: rgba(255, 255, 255, 0.7); font-size: 11pt;"
        )
        top_row.addWidget(self._slideshow_count_label)

        top_row.addStretch()

        start_btn = QPushButton("▶  Start Slideshow")
        start_btn.setStyleSheet("""
            QPushButton {
                background: rgba(66, 133, 244, 0.6);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px 16px;
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover { background: rgba(66, 133, 244, 0.85); }
        """)
        start_btn.setFocusPolicy(Qt.NoFocus)
        start_btn.clicked.connect(self._toggle_slideshow)
        top_row.addWidget(start_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: white; border: none; border-radius: 14px;
                font-size: 12pt;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.25); }
        """)
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.clicked.connect(self._toggle_slideshow_editor)
        top_row.addWidget(close_btn)

        layout.addLayout(top_row)

        # Thumbnail scroll area
        self._editor_scroll = QScrollArea()
        self._editor_scroll.setFrameShape(QFrame.NoFrame)
        self._editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._editor_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._editor_scroll.setWidgetResizable(False)
        self._editor_scroll.setStyleSheet("background: transparent;")
        self._editor_scroll.setFixedHeight(105)

        # Container for thumbnail buttons
        self._editor_container = QWidget()
        self._editor_thumb_layout = QHBoxLayout(self._editor_container)
        self._editor_thumb_layout.setContentsMargins(0, 0, 0, 0)
        self._editor_thumb_layout.setSpacing(6)
        self._editor_thumb_layout.setAlignment(Qt.AlignLeft)

        self._editor_scroll.setWidget(self._editor_container)

        # Enable mouse wheel for horizontal scrolling on the filmstrip
        self._editor_scroll.wheelEvent = self._editor_wheel_event

        layout.addWidget(self._editor_scroll)

        return panel

    def _editor_wheel_event(self, event):
        """Convert vertical wheel to horizontal scroll in editor filmstrip."""
        delta = event.angleDelta().y()
        h_bar = self._editor_scroll.horizontalScrollBar()
        h_bar.setValue(h_bar.value() - delta)
        event.accept()

    def _toggle_slideshow_editor(self):
        """Show/hide the slideshow editor panel."""
        self.slideshow_editor_visible = not self.slideshow_editor_visible

        if self.slideshow_editor_visible:
            # Default: select all photos
            if not self.slideshow_selected_indices:
                self.slideshow_selected_indices = set(range(len(self.all_media)))
            self._populate_slideshow_editor()
            self.slideshow_editor_panel.show()
            self.slideshow_editor_btn.setStyleSheet(
                self.slideshow_editor_btn.styleSheet()
                + "QPushButton { background: rgba(66, 133, 244, 0.5); }"
            )
        else:
            self.slideshow_editor_panel.hide()
            # Reset button style
            self.slideshow_editor_btn.setStyleSheet(
                self.slideshow_editor_btn.styleSheet()
                .replace("QPushButton { background: rgba(66, 133, 244, 0.5); }", "")
            )

    def _populate_slideshow_editor(self):
        """Populate the editor filmstrip with all thumbnails (lazy load visible range)."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon

        # Clear existing
        while self._editor_thumb_layout.count():
            child = self._editor_thumb_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self._editor_thumb_buttons = {}
        thumb_size = 90

        for i in range(len(self.all_media)):
            media_path = self.all_media[i]
            is_selected = i in self.slideshow_selected_indices

            btn = QPushButton()
            btn.setFixedSize(thumb_size, thumb_size)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(os.path.basename(media_path))
            btn.setProperty("media_index", i)

            self._apply_editor_thumb_style(btn, is_selected, i == self.current_index)

            # Load thumbnail
            try:
                from app_services import get_thumbnail
                pixmap = get_thumbnail(media_path, thumb_size)
                if pixmap and not pixmap.isNull():
                    btn.setIcon(QIcon(pixmap))
                    btn.setIconSize(QSize(thumb_size - 8, thumb_size - 8))
                else:
                    btn.setText("📷")
            except Exception:
                btn.setText("📷")

            # Click to toggle selection
            btn.clicked.connect(lambda checked, idx=i: self._toggle_slideshow_selection(idx))

            self._editor_thumb_layout.addWidget(btn)
            self._editor_thumb_buttons[i] = btn

        # Resize container to fit all thumbnails
        total_w = len(self.all_media) * (thumb_size + 6) + 20
        self._editor_container.setFixedSize(total_w, thumb_size + 4)

        self._update_selection_count()

        # Scroll to current photo
        QTimer.singleShot(50, self._scroll_editor_to_current)

    def _apply_editor_thumb_style(self, btn, is_selected, is_current):
        """Apply visual style to editor thumbnail based on selection state."""
        if is_selected and is_current:
            btn.setStyleSheet("""
                QPushButton {
                    border: 3px solid #4285f4;
                    border-radius: 5px;
                    background: rgba(66, 133, 244, 0.25);
                    opacity: 1.0;
                }
            """)
        elif is_selected:
            btn.setStyleSheet("""
                QPushButton {
                    border: 2px solid #4285f4;
                    border-radius: 5px;
                    background: rgba(66, 133, 244, 0.15);
                    opacity: 1.0;
                }
                QPushButton:hover { border: 3px solid #5a9cf5; }
            """)
        elif is_current:
            btn.setStyleSheet("""
                QPushButton {
                    border: 2px solid rgba(255, 255, 255, 0.4);
                    border-radius: 5px;
                    background: rgba(255, 255, 255, 0.05);
                    opacity: 0.4;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 5px;
                    background: rgba(0, 0, 0, 0.3);
                    opacity: 0.4;
                }
                QPushButton:hover {
                    border: 2px solid rgba(255, 255, 255, 0.35);
                    opacity: 0.7;
                }
            """)

    def _toggle_slideshow_selection(self, index):
        """Toggle selection of a photo in the slideshow editor."""
        if index in self.slideshow_selected_indices:
            self.slideshow_selected_indices.discard(index)
        else:
            self.slideshow_selected_indices.add(index)

        # Update button visual
        if index in self._editor_thumb_buttons:
            btn = self._editor_thumb_buttons[index]
            is_selected = index in self.slideshow_selected_indices
            self._apply_editor_thumb_style(btn, is_selected, index == self.current_index)

        self._update_selection_count()

    def _slideshow_select_all(self):
        """Select all photos for slideshow."""
        self.slideshow_selected_indices = set(range(len(self.all_media)))
        self._refresh_editor_styles()
        self._update_selection_count()

    def _slideshow_deselect_all(self):
        """Deselect all photos from slideshow."""
        self.slideshow_selected_indices.clear()
        self._refresh_editor_styles()
        self._update_selection_count()

    def _refresh_editor_styles(self):
        """Refresh all editor thumbnail styles after bulk operation."""
        for idx, btn in self._editor_thumb_buttons.items():
            is_selected = idx in self.slideshow_selected_indices
            self._apply_editor_thumb_style(btn, is_selected, idx == self.current_index)

    def _update_selection_count(self):
        """Update the selection count label in the editor."""
        count = len(self.slideshow_selected_indices)
        total = len(self.all_media)
        if hasattr(self, '_slideshow_count_label'):
            self._slideshow_count_label.setText(f"{count} of {total} selected")

    def _scroll_editor_to_current(self):
        """Scroll editor filmstrip to center on current photo."""
        if not hasattr(self, '_editor_thumb_buttons'):
            return
        if self.current_index not in self._editor_thumb_buttons:
            return

        btn = self._editor_thumb_buttons[self.current_index]
        scroll_w = self._editor_scroll.width()
        btn_center = btn.x() + btn.width() // 2
        scroll_to = btn_center - scroll_w // 2

        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        h_bar = self._editor_scroll.horizontalScrollBar()

        if hasattr(self, '_editor_scroll_anim'):
            self._editor_scroll_anim.stop()

        self._editor_scroll_anim = QPropertyAnimation(h_bar, b"value")
        self._editor_scroll_anim.setDuration(300)
        self._editor_scroll_anim.setStartValue(h_bar.value())
        self._editor_scroll_anim.setEndValue(max(0, scroll_to))
        self._editor_scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._editor_scroll_anim.start()

    def _delete_current_media(self):
        """Delete current media file."""
        from PySide6.QtWidgets import QMessageBox

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Delete Media")
        msg.setText(f"Are you sure you want to delete this file?\n\n{os.path.basename(self.media_path)}")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.setStyleSheet("""
            QMessageBox { background-color: #121212; color: white; }
            QMessageBox QLabel { color: white; }
            QMessageBox QPushButton { background: rgba(255,255,255,0.15); color: white; border: none; border-radius: 6px; padding: 6px 12px; }
            QMessageBox QPushButton:hover { background: rgba(255,255,255,0.25); }
        """)
        reply = msg.exec()

        if reply == QMessageBox.Yes:
            try:
                # os module imported at top
                # Remove from database first
                # TODO: Add database deletion logic here

                # Delete file
                # os.remove(self.media_path)
                # os module imported at top
                trash_dir = os.path.join(os.path.dirname(self.media_path), "_Trash")
                try:
                    os.makedirs(trash_dir, exist_ok=True)
                except Exception as mkerr:
                    print(f"[MediaLightbox] ⚠️ Could not create Trash folder: {mkerr}")
                new_path = os.path.join(trash_dir, os.path.basename(self.media_path))
                try:
                    os.replace(self.media_path, new_path)
                    print(f"[MediaLightbox] Moved to Trash: {new_path}")
                except Exception as mverr:
                    print(f"[MediaLightbox] ⚠️ Move to Trash failed, deleting: {mverr}")
                    os.remove(self.media_path)
                    print(f"[MediaLightbox] Deleted: {self.media_path}")

                # Remove from list
                self.all_media.remove(self.media_path)

                # Load next or previous
                if self.all_media:
                    if self.current_index >= len(self.all_media):
                        self.current_index = len(self.all_media) - 1
                    self.media_path = self.all_media[self.current_index]
                    self._load_media()
                else:
                    # No more media, close lightbox
                    self.close()

            except Exception as e:
                err = QMessageBox(self)
                err.setIcon(QMessageBox.Critical)
                err.setWindowTitle("Delete Error")
                err.setText(f"Failed to delete file:\n{str(e)}")
                err.setStandardButtons(QMessageBox.Ok)
                err.setStyleSheet("""
                    QMessageBox { background-color: #121212; color: white; }
                    QMessageBox QLabel { color: white; }
                    QMessageBox QPushButton { background: rgba(255,255,255,0.15); color: white; border: none; border-radius: 6px; padding: 6px 12px; }
                    QMessageBox QPushButton:hover { background: rgba(255,255,255,0.25); }
                """)
                err.exec()

    def _resolve_project_id(self):
        """Resolve project_id: prefer explicit, fall back to parent introspection."""
        if self._explicit_project_id is not None:
            return self._explicit_project_id
        # Legacy fallback for callers that don't pass project_id yet
        if self.parent():
            p = self.parent()
            if hasattr(p, 'grid') and hasattr(p.grid, 'project_id'):
                return p.grid.project_id
            if hasattr(p, 'layout_manager'):
                layout = p.layout_manager.get_active_layout()
                if layout and hasattr(layout, 'project_id'):
                    return layout.project_id
        return None

    def _toggle_favorite(self):
        """Toggle favorite status of current media (DB-backed)."""
        try:
            project_id = self._resolve_project_id()
            if project_id is None:
                print("[MediaLightbox] Cannot toggle favorite: project_id not available")
                return
            
            # Check current favorite status from database
            from reference_db import ReferenceDB
            db = ReferenceDB()
            current_tags = db.get_tags_for_photo(self.media_path, project_id)
            is_favorited = "favorite" in current_tags
            
            # Toggle in database
            if is_favorited:
                # Remove favorite
                db.remove_tag(self.media_path, "favorite", project_id)
                self.favorite_btn.setText("♡")
                self.favorite_btn.setStyleSheet(self.favorite_btn.styleSheet().replace("\nQPushButton { color: #ff4444; }", ""))
                status_msg = f"⭐ Removed from favorites: {os.path.basename(self.media_path)}"
                print(f"[MediaLightbox] Unfavorited: {os.path.basename(self.media_path)}")
            else:
                # Add favorite
                db.add_tag(self.media_path, "favorite", project_id)
                self.favorite_btn.setText("♥")
                self.favorite_btn.setStyleSheet(self.favorite_btn.styleSheet() + "\nQPushButton { color: #ff4444; }")
                status_msg = f"⭐ Added to favorites: {os.path.basename(self.media_path)}"
                print(f"[MediaLightbox] Favorited: {os.path.basename(self.media_path)}")
            
            # Show status message in parent window's status bar
            if hasattr(self, 'parent') and self.parent():
                parent = self.parent()
                if hasattr(parent, 'statusBar'):
                    try:
                        parent.statusBar().showMessage(status_msg, 3000)
                    except Exception as sb_err:
                        print(f"[MediaLightbox] Could not update status bar: {sb_err}")
        
        except Exception as e:
            print(f"[MediaLightbox] ⚠️ Error toggling favorite: {e}")
            import traceback
            traceback.print_exc()

    def _rate_media(self, rating: int):
        """Rate current media with 1-5 stars."""
        self.current_rating = rating
        stars = "★" * rating + "☆" * (5 - rating)
        print(f"[MediaLightbox] Rated {rating}/5: {os.path.basename(self.media_path)}")
        # TODO: Save to database

        # Update status label to show rating
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Rating",
            f"Rated {stars} ({rating}/5)",
            QMessageBox.Ok
        )

    def _toggle_fullscreen(self):
        """Toggle fullscreen mode with distraction-free viewing."""
        if self.isFullScreen():
            # Exit fullscreen
            self.showMaximized()

            # Show toolbars again
            self._show_toolbars()
            self.toolbar_hide_timer.stop()  # Don't auto-hide when not fullscreen

            print("[MediaLightbox] Exited fullscreen")
        else:
            # Enter fullscreen
            self.showFullScreen()

            # Hide toolbars for distraction-free viewing
            self._hide_toolbars()

            # Enable auto-hide in fullscreen
            self.toolbar_hide_timer.start()

            print("[MediaLightbox] Entered fullscreen (toolbars auto-hide)")

    # ==================== PHASE A IMPROVEMENTS ====================

    def _create_help_overlay(self):
        """
        PHASE A #5: Create keyboard shortcut help overlay.

        Press ? to show/hide shortcuts.
        """
        from PySide6.QtWidgets import QTextEdit

        self.help_overlay = QWidget(self)
        self.help_overlay.setStyleSheet("""
            QWidget {
                background: rgba(0, 0, 0, 0.9);
            }
        """)
        self.help_overlay.hide()

        overlay_layout = QVBoxLayout(self.help_overlay)
        overlay_layout.setContentsMargins(50, 50, 50, 50)

        # Title
        title = QLabel("⌨️ Keyboard Shortcuts")
        title.setStyleSheet("color: white; font-size: 24pt; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        overlay_layout.addWidget(title)

        # Shortcuts content
        shortcuts_text = """
<div style='color: white; font-size: 12pt; line-height: 1.8;'>
<table cellpadding='8' cellspacing='0' width='100%'>
<tr><td colspan='2' style='font-size: 14pt; font-weight: bold; padding-top: 12px;'>Navigation</td></tr>
<tr><td width='40%'><b>← / →</b> or <b>↑ / ↓</b></td><td>Previous / Next photo</td></tr>
<tr><td><b>Space</b></td><td>Next photo (slideshow style)</td></tr>
<tr><td><b>Home / End</b></td><td>First / Last photo</td></tr>
<tr><td><b>Swipe Left/Right</b></td><td>Navigate on touch devices</td></tr>

<tr><td colspan='2' style='font-size: 14pt; font-weight: bold; padding-top: 12px;'>Zoom & View</td></tr>
<tr><td><b>Mouse Wheel</b></td><td>Zoom in / out (cursor-centered)</td></tr>
<tr><td><b>+ / -</b></td><td>Zoom in / out</td></tr>
<tr><td><b>0</b></td><td>Fit to window</td></tr>
<tr><td><b>Pinch Gesture</b></td><td>Zoom on touch devices</td></tr>
<tr><td><b>Click + Drag</b></td><td>Pan when zoomed</td></tr>

<tr><td colspan='2' style='font-size: 14pt; font-weight: bold; padding-top: 12px;'>Actions</td></tr>
<tr><td><b>I</b></td><td>Toggle info panel</td></tr>
<tr><td><b>S</b></td><td>Toggle slideshow</td></tr>
<tr><td><b>F</b></td><td>Toggle favorite</td></tr>
<tr><td><b>D</b></td><td>Delete photo</td></tr>
<tr><td><b>1-5</b></td><td>Rate photo (1-5 stars)</td></tr>

<tr><td colspan='2' style='font-size: 14pt; font-weight: bold; padding-top: 12px;'>Quick Edit</td></tr>
<tr><td><b>R</b></td><td>Rotate image clockwise (90°)</td></tr>
<tr><td><b>E</b></td><td>Auto-enhance (brightness + contrast)</td></tr>
<tr><td><b>C</b></td><td>Toggle crop mode</td></tr>
<tr><td><b>M</b></td><td>Compare mode (side-by-side)</td></tr>
<tr><td><b>Ctrl+Shift+S</b></td><td>Share / Export dialog</td></tr>

<tr><td colspan='2' style='font-size: 14pt; font-weight: bold; padding-top: 12px;'>Video Controls</td></tr>
<tr><td><b>Space / K</b></td><td>Play / Pause video</td></tr>
<tr><td><b>Shift + →</b></td><td>Skip forward +10 seconds</td></tr>
<tr><td><b>Shift + ←</b></td><td>Skip backward -10 seconds</td></tr>
<tr><td><b>Hover seek bar</b></td><td>Preview timestamp</td></tr>

<tr><td colspan='2' style='font-size: 14pt; font-weight: bold; padding-top: 12px;'>General</td></tr>
<tr><td><b>F11</b></td><td>Toggle fullscreen</td></tr>
<tr><td><b>ESC</b></td><td>Close lightbox</td></tr>
<tr><td><b>?</b></td><td>Show/hide this help</td></tr>
</table>
</div>
        """

        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setHtml(shortcuts_text)
        help_text.setStyleSheet("""
            QTextEdit {
                background: transparent;
                border: none;
                color: white;
            }
        """)
        overlay_layout.addWidget(help_text)

        # Close instruction
        close_label = QLabel("Press ESC or ? to close")
        close_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 11pt;")
        close_label.setAlignment(Qt.AlignCenter)
        overlay_layout.addWidget(close_label)

    def _toggle_help_overlay(self):
        """PHASE A #5: Toggle keyboard shortcuts help overlay."""
        if self.help_visible:
            self.help_overlay.hide()
            self.help_visible = False
        else:
            # Resize to fill window
            self.help_overlay.setGeometry(self.rect())
            self.help_overlay.show()
            self.help_overlay.raise_()  # Bring to front
            self.help_visible = True

    def _show_loading_indicator(self, message: str = "⏳ Loading..."):
        """PHASE A #4: Show loading indicator with message."""
        self.loading_indicator.setText(message)

        # Position in center of scroll area
        scroll_center_x = self.scroll_area.width() // 2
        scroll_center_y = self.scroll_area.height() // 2

        # Calculate position (center the indicator)
        indicator_width = 200
        indicator_height = 80
        x = scroll_center_x - (indicator_width // 2)
        y = scroll_center_y - (indicator_height // 2)

        self.loading_indicator.setGeometry(x, y, indicator_width, indicator_height)
        self.loading_indicator.show()
        self.loading_indicator.raise_()
        self.is_loading = True

        # Track load start time
        from PySide6.QtCore import QDateTime
        self.loading_start_time = QDateTime.currentMSecsSinceEpoch()

    def _hide_loading_indicator(self):
        """PHASE A #4: Hide loading indicator."""
        self.loading_indicator.hide()
        self.is_loading = False

    def _start_preloading(self):
        """
        PHASE A #1: Start preloading next photos in background.

        Preloads next 2 photos for instant navigation.
        """
        if not self.all_media:
            return

        # Preload next N photos
        for i in range(1, self.preload_count + 1):
            next_index = self.current_index + i

            if next_index >= len(self.all_media):
                break  # No more photos to preload

            next_path = self.all_media[next_index]

            # Skip if already cached
            if next_path in self.preload_cache:
                continue

            # Skip videos (only preload photos)
            if self._is_video(next_path):
                continue

            # Start background preload
            worker = PreloadImageWorker(next_path, self.preload_signals)
            self.preload_thread_pool.start(worker)
            print(f"[MediaLightbox] Preloading: {os.path.basename(next_path)}")

    @staticmethod
    def _estimate_pixmap_bytes(pixmap):
        """Estimate memory footprint of a QPixmap (width * height * 4 bytes * 1.2 overhead)."""
        return int(pixmap.width() * pixmap.height() * 4 * 1.2)

    def _on_preload_complete(self, path: str, qimage):
        """PHASE A #1: Handle preload completion — promotes QImage→QPixmap on main thread."""
        if qimage and not qimage.isNull():
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import QDateTime
            # Convert QImage → QPixmap on the main thread (the only safe place)
            pixmap = QPixmap.fromImage(qimage)
            byte_est = self._estimate_pixmap_bytes(pixmap)
            self.preload_cache[path] = {
                'pixmap': pixmap,
                'timestamp': QDateTime.currentMSecsSinceEpoch(),
                'byte_est': byte_est,
            }
            self._cache_bytes_used += byte_est
            mb_used = self._cache_bytes_used / (1024 * 1024)
            print(f"[MediaLightbox] ✓ Cached: {os.path.basename(path)} "
                  f"(~{byte_est // 1024}KB, cache: {len(self.preload_cache)} items / {mb_used:.1f}MB)")

            # Clean cache if too large (count or byte budget)
            self._clean_preload_cache()

    def _clean_preload_cache(self):
        """PHASE A #1: Clean preload cache (evict by count limit AND byte budget)."""
        # Sort by timestamp (oldest first) for LRU eviction
        sorted_paths = sorted(
            self.preload_cache.keys(),
            key=lambda p: self.preload_cache[p]['timestamp']
        )

        # Evict until both count and byte limits are satisfied
        while (len(self.preload_cache) > self.cache_limit
               or self._cache_bytes_used > self.cache_byte_limit):
            if not sorted_paths:
                break
            path = sorted_paths.pop(0)
            evicted = self.preload_cache.pop(path, None)
            if evicted:
                self._cache_bytes_used -= evicted.get('byte_est', 0)
                print(f"[MediaLightbox] Evicted from cache: {os.path.basename(path)} "
                      f"(~{evicted.get('byte_est', 0) // 1024}KB)")

        # Safety: clamp to zero
        if self._cache_bytes_used < 0:
            self._cache_bytes_used = 0

    def _on_thumbnail_loaded(self, generation: int, qimage):
        """PHASE A #2: Handle progressive loading - thumbnail quality loaded.

        Discards stale results from previous navigations via generation check.
        Promotes QImage→QPixmap on the main thread (Qt requirement).
        """
        # Generation guard: discard if user has already navigated away
        if generation != self._lb_media_generation:
            print(f"[SIGNAL] _on_thumbnail_loaded: discarding stale result (gen {generation} != {self._lb_media_generation})")
            return

        print(f"[SIGNAL] _on_thumbnail_loaded called, qimage={'valid' if qimage and not qimage.isNull() else 'NULL'}")

        if not qimage or qimage.isNull():
            print(f"[ERROR] ⚠️ Thumbnail QImage is null or invalid! Photo won't display.")
            self._hide_loading_indicator()
            return

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        try:
            # Convert QImage → QPixmap on main thread
            pixmap = QPixmap.fromImage(qimage)

            # Store as original for zoom operations
            self.original_pixmap = pixmap

            # Scale to fit viewport
            viewport_size = self.scroll_area.viewport().size()
            scaled_pixmap = pixmap.scaled(
                viewport_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            # Display thumbnail (instant!)
            self.image_label.setPixmap(scaled_pixmap)
            self.image_label.resize(scaled_pixmap.size())
            self.media_container.resize(scaled_pixmap.size())

            self.thumbnail_quality_loaded = True

            # Update status
            self._show_loading_indicator("Loading full resolution...")

            print(f"[MediaLightbox] ✓ Thumbnail displayed (progressive load)")
        except Exception as e:
            print(f"[ERROR] ⚠️ Failed to display thumbnail: {e}")
            import traceback
            traceback.print_exc()
            self._hide_loading_indicator()

    def _on_full_quality_loaded(self, generation: int, qimage):
        """PHASE A #2: Handle progressive loading - full quality loaded.

        Discards stale results from previous navigations via generation check.
        Promotes QImage→QPixmap on the main thread (Qt requirement).
        """
        # Generation guard: discard if user has already navigated away
        if generation != self._lb_media_generation:
            print(f"[SIGNAL] _on_full_quality_loaded: discarding stale result (gen {generation} != {self._lb_media_generation})")
            return

        print(f"[SIGNAL] _on_full_quality_loaded called, qimage={'valid' if qimage and not qimage.isNull() else 'NULL'}")

        if not qimage or qimage.isNull():
            print(f"[ERROR] ⚠️ Full quality QImage is null or invalid!")
            # Show brief non-modal toast: keep thumbnail visible, inform user
            self._show_loading_indicator("Using optimized preview")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2500, self._hide_loading_indicator)
            return

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        try:
            # Convert QImage → QPixmap on main thread
            pixmap = QPixmap.fromImage(qimage)

            # Store as original for zoom operations
            self.original_pixmap = pixmap

            # Scale to fit viewport
            viewport_size = self.scroll_area.viewport().size()
            scaled_pixmap = pixmap.scaled(
                viewport_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            # Swap with subtle fade
            from PySide6.QtCore import QPropertyAnimation, QEasingCurve

            # Create fade animation if not exists
            if not self.image_label.graphicsEffect():
                opacity_effect = QGraphicsOpacityEffect()
                self.image_label.setGraphicsEffect(opacity_effect)

            opacity_effect = self.image_label.graphicsEffect()

            # Quick fade out/in
            fade = QPropertyAnimation(opacity_effect, b"opacity")
            fade.setDuration(150)
            fade.setStartValue(0.7)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.OutCubic)

            # Update pixmap
            self.image_label.setPixmap(scaled_pixmap)
            self.image_label.resize(scaled_pixmap.size())
            self.media_container.resize(scaled_pixmap.size())

            fade.start()
            self.setProperty("quality_fade", fade)  # Prevent GC

            self.full_quality_loaded = True

            # Calculate zoom level
            self.zoom_level = scaled_pixmap.width() / pixmap.width()
            self.fit_zoom_level = self.zoom_level
            self.zoom_mode = "fit"

            # Hide loading indicator
            self._hide_loading_indicator()

            print(f"[MediaLightbox] ✓ Full quality displayed (progressive load complete)")
        except Exception as e:
            print(f"[ERROR] ⚠️ Failed to display full quality: {e}")
            import traceback
            traceback.print_exc()
            self._hide_loading_indicator()

    def _calculate_zoom_scroll_adjustment(self, old_zoom: float, new_zoom: float):
        """
        PHASE A #3: Calculate scroll position adjustment for cursor-centered zoom.

        Keeps the point under the mouse cursor fixed during zoom.
        """
        if not self.last_mouse_pos or not self.zoom_mouse_tracking:
            return  # No adjustment needed

        # Get scroll area viewport position
        viewport = self.scroll_area.viewport()

        # Convert mouse position to viewport coordinates
        mouse_viewport_pos = viewport.mapFromGlobal(self.mapToGlobal(self.last_mouse_pos))

        # Calculate position in image space before zoom
        scroll_x = self.scroll_area.horizontalScrollBar().value()
        scroll_y = self.scroll_area.verticalScrollBar().value()

        image_x_before = scroll_x + mouse_viewport_pos.x()
        image_y_before = scroll_y + mouse_viewport_pos.y()

        # Calculate new scroll position to keep same point under cursor
        zoom_ratio = new_zoom / old_zoom

        new_scroll_x = int(image_x_before * zoom_ratio - mouse_viewport_pos.x())
        new_scroll_y = int(image_y_before * zoom_ratio - mouse_viewport_pos.y())

        # Apply after zoom is complete (in next event loop)
        def apply_scroll():
            self.scroll_area.horizontalScrollBar().setValue(new_scroll_x)
            self.scroll_area.verticalScrollBar().setValue(new_scroll_y)

        QTimer.singleShot(10, apply_scroll)

    # ==================== PHASE B IMPROVEMENTS ====================

    def _create_filmstrip(self) -> QWidget:
        """
        PHASE B #1: Create thumbnail filmstrip at bottom.

        Shows 7-10 thumbnails with current photo highlighted.
        Click to jump, auto-scroll to keep current centered.
        """
        from PySide6.QtWidgets import QScrollArea, QHBoxLayout

        filmstrip = QWidget()
        filmstrip.setFixedHeight(120)  # 80px thumbnails + 40px padding
        filmstrip.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0, 0, 0, 0),
                    stop:1 rgba(0, 0, 0, 0.9));
            }
        """)

        layout = QVBoxLayout(filmstrip)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)

        # Horizontal scroll area for thumbnails
        self.filmstrip_scroll = QScrollArea()
        self.filmstrip_scroll.setFrameShape(QFrame.NoFrame)
        self.filmstrip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.filmstrip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.filmstrip_scroll.setWidgetResizable(False)
        self.filmstrip_scroll.setStyleSheet("background: transparent;")

        # Container for thumbnail buttons
        filmstrip_container = QWidget()
        self.filmstrip_layout = QHBoxLayout(filmstrip_container)
        self.filmstrip_layout.setContentsMargins(10, 0, 10, 0)
        self.filmstrip_layout.setSpacing(8)
        self.filmstrip_layout.setAlignment(Qt.AlignLeft)

        self.filmstrip_scroll.setWidget(filmstrip_container)
        layout.addWidget(self.filmstrip_scroll)

        # Initialize filmstrip on first show
        QTimer.singleShot(100, self._update_filmstrip)

        return filmstrip

    def _update_filmstrip(self):
        """
        PHASE B #1: Update filmstrip thumbnails for current media list.

        FIX: Lazy loading - only load thumbnails for visible range (current ± 10)
        to prevent UI freeze with large photo collections.
        """
        if not self.filmstrip_enabled or not hasattr(self, 'filmstrip_layout'):
            return

        # Clear existing thumbnails
        while self.filmstrip_layout.count():
            child = self.filmstrip_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.filmstrip_buttons.clear()

        # LAZY LOADING: Only create buttons for visible range
        # Show current ± 10 photos (21 total max)
        visible_range = 10
        start_idx = max(0, self.current_index - visible_range)
        end_idx = min(len(self.all_media), self.current_index + visible_range + 1)

        print(f"[MediaLightbox] Filmstrip: Showing {end_idx - start_idx} thumbnails (range {start_idx}-{end_idx} of {len(self.all_media)})")

        # Create thumbnail buttons ONLY for visible range
        for i in range(start_idx, end_idx):
            media_path = self.all_media[i]
            btn = QPushButton()
            btn.setFixedSize(80, 80)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(os.path.basename(media_path))

            # Highlight current photo
            if i == self.current_index:
                btn.setStyleSheet("""
                    QPushButton {
                        border: 3px solid #4285f4;
                        border-radius: 4px;
                        background: #1a1a1a;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        border: 1px solid rgba(255, 255, 255, 0.2);
                        border-radius: 4px;
                        background: #2a2a2a;
                    }
                    QPushButton:hover {
                        border: 2px solid rgba(255, 255, 255, 0.5);
                    }
                """)

            # Load thumbnail (still synchronous but only for visible range)
            self._load_filmstrip_thumbnail(i, media_path, btn)

            # Click handler
            btn.clicked.connect(lambda checked, idx=i: self._jump_to_media(idx))

            self.filmstrip_layout.addWidget(btn)
            self.filmstrip_buttons[i] = btn

        # Auto-scroll to keep current centered
        QTimer.singleShot(50, self._scroll_filmstrip_to_current)

    def _load_filmstrip_thumbnail(self, index: int, media_path: str, button: QPushButton):
        """PHASE B #1: Load thumbnail for filmstrip button."""
        try:
            from app_services import get_thumbnail
            pixmap = get_thumbnail(media_path, 80)

            if pixmap and not pixmap.isNull():
                button.setIcon(QIcon(pixmap))
                button.setIconSize(QSize(76, 76))

                # Add video indicator for videos
                if self._is_video(media_path):
                    button.setText("▶")
                    button.setStyleSheet(button.styleSheet() + """
                        QPushButton {
                            color: white;
                            font-size: 20pt;
                        }
                    """)
            else:
                button.setText("📷")

        except Exception as e:
            print(f"[MediaLightbox] Error loading filmstrip thumbnail: {e}")
            button.setText("📷")

    def _jump_to_media(self, index: int):
        """PHASE B #1: Jump to specific media from filmstrip click."""
        print(f"[MediaLightbox] Filmstrip jump to index: {index}")
        if 0 <= index < len(self.all_media):
            self.current_index = index
            self.media_path = self.all_media[index]
            self._load_media_with_transition()
            self._update_filmstrip()

    def _scroll_filmstrip_to_current(self):
        """PHASE B #1: Auto-scroll filmstrip to keep current thumbnail centered."""
        if not hasattr(self, 'filmstrip_scroll') or self.current_index not in self.filmstrip_buttons:
            return

        current_btn = self.filmstrip_buttons[self.current_index]
        filmstrip_width = self.filmstrip_scroll.width()

        # Calculate position to center current thumbnail
        btn_center_x = current_btn.x() + (current_btn.width() // 2)
        scroll_to = btn_center_x - (filmstrip_width // 2)

        # Animate scroll
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve

        if hasattr(self, '_filmstrip_scroll_anim'):
            self._filmstrip_scroll_anim.stop()

        self._filmstrip_scroll_anim = QPropertyAnimation(
            self.filmstrip_scroll.horizontalScrollBar(),
            b"value"
        )
        self._filmstrip_scroll_anim.setDuration(300)
        self._filmstrip_scroll_anim.setStartValue(self.filmstrip_scroll.horizontalScrollBar().value())
        self._filmstrip_scroll_anim.setEndValue(max(0, scroll_to))
        self._filmstrip_scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._filmstrip_scroll_anim.start()

    def _update_contextual_toolbars(self):
        """
        PHASE B #4: Update toolbar visibility based on media type.

        Show video controls only for videos, zoom controls only for photos.
        """
        if not self.contextual_toolbars:
            return

        is_video = self._is_video(self.media_path)

        # Update button visibility
        for btn in self.video_only_buttons:
            btn.setVisible(is_video)

        for btn in self.photo_only_buttons:
            btn.setVisible(not is_video)

    def _save_zoom_state(self):
        """Save current zoom state for persistence (photos and videos)."""
        if self.zoom_persistence_enabled:
            self.saved_zoom_level = self.zoom_level
            self.saved_zoom_mode = self.zoom_mode
            print(f"[MediaLightbox] Zoom state saved: {self.zoom_mode} @ {int(self.zoom_level * 100)}%")

    def _restore_zoom_state(self):
        """Restore saved zoom state to current media (photos and videos)."""
        if self.zoom_persistence_enabled:
            self.zoom_level = getattr(self, 'saved_zoom_level', 1.0)
            self.zoom_mode = getattr(self, 'saved_zoom_mode', 'fit')
            if self._is_video(self.media_path):
                self._apply_video_zoom()
            else:
                self._apply_zoom()
            self._update_zoom_status()
            print(f"[MediaLightbox] Zoom state restored: {self.zoom_mode} @ {int(self.zoom_level * 100)}%")

    def _reset_zoom_state(self):
        """PHASE B #5: Reset to default fit-to-window zoom."""
        self.apply_zoom_to_all = False
        self.zoom_mode = "fit"
        self._fit_to_window()
        self._update_zoom_status()
        print(f"[MediaLightbox] Zoom reset to fit mode")

    def _handle_double_tap(self, pos):
        """
        PHASE B #2: Handle double-tap gesture for zoom in/out.

        Tap once: Track tap time/position
        Tap twice quickly: Toggle between fit and 2x zoom
        """
        from PySide6.QtCore import QDateTime

        if not self.double_tap_enabled or self._is_video(self.media_path):
            return False

        current_time = QDateTime.currentMSecsSinceEpoch()

        # Check if this is second tap
        if self.last_tap_time and self.last_tap_pos:
            time_diff = current_time - self.last_tap_time
            pos_diff = (pos - self.last_tap_pos).manhattanLength()

            # Double-tap detected: within 300ms and 50px
            if time_diff < 300 and pos_diff < 50:
                # Toggle zoom
                if self.zoom_mode == "fit":
                    # Zoom to 2x
                    self.zoom_level = 2.0
                    self.zoom_mode = "custom"
                else:
                    # Reset to fit
                    self.zoom_mode = "fit"
                    self._fit_to_window()

                self._apply_zoom()
                self._update_zoom_status()

                # Reset tap tracking
                self.last_tap_time = None
                self.last_tap_pos = None

                print(f"[MediaLightbox] Double-tap zoom: {self.zoom_mode}")
                return True

        # Track this tap
        self.last_tap_time = current_time
        self.last_tap_pos = pos

        return False

    def _skip_video_forward(self):
        """PHASE B #3: Skip video forward by 10 seconds."""
        if hasattr(self, 'video_player') and self._is_video(self.media_path):
            current_pos = self.video_player.position()
            new_pos = min(current_pos + 10000, self.seek_slider.maximum())  # +10s (10000ms)
            self.video_player.setPosition(new_pos)
            print(f"[MediaLightbox] Video skip +10s: {new_pos // 1000}s")

    def _skip_video_backward(self):
        """PHASE B #3: Skip video backward by 10 seconds."""
        if hasattr(self, 'video_player') and self._is_video(self.media_path):
            current_pos = self.video_player.position()
            new_pos = max(current_pos - 10000, 0)  # -10s (10000ms)
            self.video_player.setPosition(new_pos)
            print(f"[MediaLightbox] Video skip -10s: {new_pos // 1000}s")

    def _on_speed_clicked(self):
        """Cycle playback speed among 0.5x, 1.0x, 1.5x, 2.0x."""
        if not hasattr(self, 'video_player') or not self._is_video(self.media_path):
            return
        speeds = [0.5, 1.0, 1.5, 2.0]
        idx = getattr(self, 'current_speed_index', 1)
        idx = (idx + 1) % len(speeds)
        self.current_speed_index = idx
        rate = speeds[idx]
        try:
            self.video_player.setPlaybackRate(rate)
        except Exception as e:
            print(f"[MediaLightbox] PlaybackRate not supported: {e}")
        if hasattr(self, 'speed_btn'):
            self.speed_btn.setText(f"{rate:.1f}x")
        # Show toast feedback
        self._show_toast(f"Speed: {rate:.1f}x")
        print(f"[MediaLightbox] Playback speed set to {rate:.1f}x")

    # ==================== PHASE C IMPROVEMENTS ====================

    def _on_media_status_changed(self, status):
        """Handle media status changes — controls readiness and looping.

        Geometry fitting is handled by nativeSizeChanged, NOT here.
        This handler manages: looping, control enable/disable, loading indicators.
        """
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            # Looping behavior at end of media
            if status == QMediaPlayer.EndOfMedia and getattr(self, 'loop_enabled', False):
                self.video_player.setPosition(0)
                self.video_player.play()
                print("[MediaLightbox] Looping video to start")
            if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                print(f"[MediaLightbox] Media ready (status={status})")
        except Exception as e:
            print(f"[MediaLightbox] mediaStatusChanged handler error: {e}")

    def _on_screenshot_clicked(self):
        """Capture current video frame as image and save to Screenshots folder."""
        if not hasattr(self, 'video_widget') or not self._is_video(self.media_path):
            return
        try:
            pix = self.video_widget.grab()
            if pix and not pix.isNull():
                import os, time
                shots_dir = os.path.join(os.path.dirname(self.media_path), "_Screenshots")
                os.makedirs(shots_dir, exist_ok=True)
                fname = f"screenshot_{int(time.time())}.png"
                out_path = os.path.join(shots_dir, fname)
                pix.save(out_path)
                print(f"[MediaLightbox] Screenshot saved: {out_path}")
            else:
                print("[MediaLightbox] Screenshot failed: No frame")
        except Exception as e:
            print(f"[MediaLightbox] Screenshot error: {e}")

    def _on_loop_clicked(self):
        """Toggle loop playback on/off."""
        self.loop_enabled = not getattr(self, 'loop_enabled', False)
        if hasattr(self, 'loop_btn'):
            self.loop_btn.setText("Loop On" if self.loop_enabled else "Loop Off")
        print(f"[MediaLightbox] Loop {'enabled' if self.loop_enabled else 'disabled'}")

    def _step_frame_forward(self):
        """Advance video by ~1 frame (approx 33ms at 30fps)."""
        if hasattr(self, 'video_player') and self._is_video(self.media_path):
            pos = self.video_player.position()
            self.video_player.setPosition(pos + 33)
            print(f"[MediaLightbox] Frame +1 (pos={pos+33}ms)")

    def _step_frame_backward(self):
        """Step video back by ~1 frame (approx 33ms)."""
        if hasattr(self, 'video_player') and self._is_video(self.media_path):
            pos = self.video_player.position()
            self.video_player.setPosition(max(pos - 33, 0))
            print(f"[MediaLightbox] Frame -1 (pos={max(pos-33,0)}ms)")

    def _rotate_image(self):
        """
        PHASE C #3: Rotate image clockwise by 90 degrees.

        R key cycles: 0° → 90° → 180° → 270° → 0°
        """
        if self._is_video(self.media_path):
            return  # Don't rotate videos

        # Cycle rotation
        self.rotation_angle = (self.rotation_angle + 90) % 360

        # Apply rotation to current pixmap
        if self.original_pixmap and not self.original_pixmap.isNull():
            from PySide6.QtGui import QTransform

            # Create rotation transform
            transform = QTransform().rotate(self.rotation_angle)
            rotated_pixmap = self.original_pixmap.transformed(transform, Qt.SmoothTransformation)

            # Update original pixmap with rotated version
            self.original_pixmap = rotated_pixmap

            # Reapply zoom
            self._apply_zoom()

            print(f"[MediaLightbox] Image rotated: {self.rotation_angle}°")

    def _toggle_crop_mode_OLD_DISABLED(self):
        """
        PHASE C #3: OLD crop mode stub - DISABLED.
        
        This has been replaced by the editor crop mode.
        Use 'E' key to enter editor, then 'C' to toggle crop.
        """
        print("[MediaLightbox] OLD crop mode disabled - use Editor mode instead (press E, then C)")
        return  # Disabled - use editor crop mode instead

    def _auto_enhance(self):
        """
        PHASE C #3: Apply automatic enhancement to photo.

        Basic brightness/contrast adjustment.
        """
        if self._is_video(self.media_path) or not self.original_pixmap:
            return

        try:
            from PySide6.QtGui import QImage
            from PIL import Image, ImageEnhance
            import io

            # Convert QPixmap to PIL Image
            qimage = self.original_pixmap.toImage()
            buffer = qimage.bits().tobytes()
            pil_image = Image.frombytes('RGBA', (qimage.width(), qimage.height()), buffer)

            # Auto-enhance
            enhancer = ImageEnhance.Contrast(pil_image)
            pil_image = enhancer.enhance(1.2)  # +20% contrast

            enhancer = ImageEnhance.Brightness(pil_image)
            pil_image = enhancer.enhance(1.1)  # +10% brightness

            # Convert back to QPixmap
            buffer = io.BytesIO()
            pil_image.save(buffer, format='PNG')
            buffer.seek(0)

            enhanced_pixmap = QPixmap()
            enhanced_pixmap.loadFromData(buffer.read())

            self.original_pixmap = enhanced_pixmap
            self._apply_zoom()

            print("[MediaLightbox] Auto-enhance applied: +20% contrast, +10% brightness")

        except Exception as e:
            print(f"[MediaLightbox] Auto-enhance error: {e}")

    def _show_share_dialog(self):
        """
        PHASE C #2: Show share/export dialog.

        Options: Small/Medium/Large/Original, Copy to clipboard
        """
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QButtonGroup, QRadioButton

        dialog = QDialog(self)
        dialog.setWindowTitle("Share / Export")
        dialog.setMinimumWidth(400)
        dialog.setStyleSheet("QDialog { background-color: #1e1e1e; } QLabel { color: white; } QRadioButton { color: white; } QPushButton { background: rgba(255,255,255,0.15); color: white; border: none; border-radius: 6px; padding: 8px 12px; } QPushButton:hover { background: rgba(255,255,255,0.25); }")

        layout = QVBoxLayout(dialog)

        # Title
        title = QLabel("📤 Share or Export Photo")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 10px;")
        layout.addWidget(title)

        # Size options
        size_label = QLabel("Export Size:")
        size_label.setStyleSheet("font-weight: bold; padding-top: 10px;")
        layout.addWidget(size_label)

        size_group = QButtonGroup(dialog)
        sizes = [
            ("Small (800px)", "small"),
            ("Medium (1920px)", "medium"),
            ("Large (3840px)", "large"),
            ("Original Size", "original")
        ]

        for text, value in sizes:
            radio = QRadioButton(text)
            radio.setProperty("size_value", value)
            size_group.addButton(radio)
            layout.addWidget(radio)

        size_group.buttons()[2].setChecked(True)  # Default: Large

        # Action buttons
        button_layout = QHBoxLayout()

        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.setStyleSheet("")
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard())
        button_layout.addWidget(copy_btn)

        save_btn = QPushButton("💾 Save As...")
        save_btn.setStyleSheet("")
        save_btn.clicked.connect(lambda: self._export_photo(size_group.checkedButton().property("size_value")))
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("")
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

        dialog.exec()

    def _copy_to_clipboard(self):
        """PHASE C #2: Copy current photo to clipboard."""
        if self.original_pixmap and not self.original_pixmap.isNull():
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setPixmap(self.original_pixmap)
            print("[MediaLightbox] Photo copied to clipboard")

    def _export_photo(self, size_option: str):
        """PHASE C #2: Export photo with size options."""
        from PySide6.QtWidgets import QFileDialog

        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        # Get save location
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Photo",
            f"photo_{size_option}.jpg",
            "JPEG Images (*.jpg);;PNG Images (*.png)"
        )

        if not file_path:
            return

        # Resize based on option
        pixmap = self.original_pixmap

        if size_option == "small":
            pixmap = pixmap.scaledToWidth(800, Qt.SmoothTransformation)
        elif size_option == "medium":
            pixmap = pixmap.scaledToWidth(1920, Qt.SmoothTransformation)
        elif size_option == "large":
            pixmap = pixmap.scaledToWidth(3840, Qt.SmoothTransformation)

        # Save
        pixmap.save(file_path, quality=95)
        print(f"[MediaLightbox] Photo exported: {file_path} ({size_option})")

    def _toggle_compare_mode(self):
        """
        PHASE C #4: Toggle compare mode (split-screen composite).
        
        Shows current photo side-by-side with previous/next for comparison.
        """
        self.compare_mode_active = not self.compare_mode_active
        
        if self.compare_mode_active:
            # Only support photos for compare (skip videos)
            if self._is_video(self.media_path):
                print("[MediaLightbox] Compare mode not supported for videos")
                self.compare_mode_active = False
                return
            
            # Select comparison photo (previous or next)
            if self.current_index > 0:
                self.compare_media_path = self.all_media[self.current_index - 1]
            elif self.current_index < len(self.all_media) - 1:
                self.compare_media_path = self.all_media[self.current_index + 1]
            else:
                print("[MediaLightbox] No other photos to compare")
                self.compare_mode_active = False
                return
            
            try:
                from PySide6.QtGui import QPixmap, QPainter
                from PySide6.QtCore import Qt
                
                # Load both images via SafeImageLoader (capped, never full resolution)
                from services.safe_image_loader import safe_decode_qimage
                viewport = self.scroll_area.viewport().size()
                compare_max_dim = min(max(viewport.width(), viewport.height()), 2560)

                base_qimg = safe_decode_qimage(self.media_path, max_dim=compare_max_dim)
                cmp_qimg = safe_decode_qimage(self.compare_media_path, max_dim=compare_max_dim)
                base_pix = QPixmap.fromImage(base_qimg)
                cmp_pix = QPixmap.fromImage(cmp_qimg)
                if base_pix.isNull() or cmp_pix.isNull():
                    print("[MediaLightbox] Compare mode: failed to load pixmaps")
                    self.compare_mode_active = False
                    return

                # Determine viewport size
                target_h = viewport.height()
                half_w = viewport.width() // 2
                
                # Scale each to fit half-width, full height
                base_scaled = base_pix.scaled(half_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                cmp_scaled = cmp_pix.scaled(half_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                
                # Composite side-by-side
                composite_w = base_scaled.width() + cmp_scaled.width()
                composite_h = max(base_scaled.height(), cmp_scaled.height())
                composite = QPixmap(composite_w, composite_h)
                composite.fill(Qt.black)
                painter = QPainter(composite)
                painter.drawPixmap(0, (composite_h - base_scaled.height()) // 2, base_scaled)
                painter.drawPixmap(base_scaled.width(), (composite_h - cmp_scaled.height()) // 2, cmp_scaled)
                painter.end()
                
                # Save original to restore later
                self._saved_original_pixmap = self.original_pixmap
                
                # Display composite
                self.original_pixmap = composite
                self.zoom_mode = "fit"
                self._fit_to_window()
                self._update_zoom_status()
                print(f"[MediaLightbox] Compare mode ENABLED: {os.path.basename(self.media_path)} vs {os.path.basename(self.compare_media_path)}")
            except Exception as e:
                print(f"[MediaLightbox] Compare mode error: {e}")
                self.compare_mode_active = False
                # Restore state
                if hasattr(self, '_saved_original_pixmap') and self._saved_original_pixmap:
                    self.original_pixmap = self._saved_original_pixmap
                    self._fit_to_window()
                return
        else:
            # Restore original view
            if hasattr(self, '_saved_original_pixmap') and self._saved_original_pixmap:
                self.original_pixmap = self._saved_original_pixmap
                self._fit_to_window()
                self._update_zoom_status()
                self._saved_original_pixmap = None
            self.compare_media_path = None
            print("[MediaLightbox] Compare mode DISABLED")

    def _show_motion_indicator(self):
        """
        PHASE C #5: Show motion photo indicator in top-right corner.

        Indicates that this photo has a paired video (motion/live photo).
        """
        if not hasattr(self, 'motion_indicator'):
            return

        # Position in top-right corner (with margin)
        margin = 20
        x = self.width() - self.motion_indicator.width() - margin
        y = margin + 80  # Below toolbar

        self.motion_indicator.move(x, y)
        self.motion_indicator.show()
        self.motion_indicator.raise_()

        print(f"[MediaLightbox] Motion indicator shown at ({x}, {y})")

    def _hide_motion_indicator(self):
        """PHASE C #5: Hide motion photo indicator."""
        if hasattr(self, 'motion_indicator'):
            self.motion_indicator.hide()


# === CUSTOM SEEK SLIDER WITH TRIM MARKERS ===
class TrimMarkerSlider(QSlider):
    """Custom QSlider that displays visual trim markers for video editing."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.trim_start = 0  # Start trim position (0-100 scale)
        self.trim_end = 100  # End trim position (0-100 scale)
        self.video_duration_ms = 0  # Total video duration in milliseconds
        self.show_markers = False  # Only show markers in edit mode

    def set_trim_markers(self, start_ms, end_ms, duration_ms):
        """Set trim marker positions in milliseconds."""
        self.video_duration_ms = duration_ms
        slider_max = self.maximum()  # Get slider max BEFORE if/else blocks

        if duration_ms > 0:
            # Convert milliseconds to slider range (0-100 or 0-max)
            self.trim_start = int((start_ms / duration_ms) * slider_max)
            self.trim_end = int((end_ms / duration_ms) * slider_max)
        else:
            self.trim_start = 0
            self.trim_end = slider_max
        self.show_markers = True
        self.update()  # Trigger repaint

    def clear_trim_markers(self):
        """Hide trim markers."""
        self.show_markers = False
        self.update()

    def paintEvent(self, event):
        """Override paint event to draw trim markers."""
        # First, draw the standard slider
        super().paintEvent(event)

        # If markers enabled and in valid range, draw them
        if not self.show_markers or self.video_duration_ms == 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate marker positions in pixels
        slider_width = self.width()
        handle_width = 12  # From stylesheet
        usable_width = slider_width - handle_width

        # Convert trim positions to pixel coordinates
        slider_max = self.maximum()
        if slider_max > 0:
            start_x = int((self.trim_start / slider_max) * usable_width) + (handle_width // 2)
            end_x = int((self.trim_end / slider_max) * usable_width) + (handle_width // 2)
        else:
            return

        # Draw shaded regions OUTSIDE trim range (semi-transparent gray)
        painter.fillRect(0, 0, start_x, self.height(), QColor(0, 0, 0, 80))
        painter.fillRect(end_x, 0, slider_width - end_x, self.height(), QColor(0, 0, 0, 80))

        # Draw green marker for trim start (🟢)
        painter.setPen(QPen(QColor(76, 175, 80), 3))  # Green, 3px thick
        painter.drawLine(start_x, 0, start_x, self.height())

        # Draw red marker for trim end (🔴)
        painter.setPen(QPen(QColor(244, 67, 54), 3))  # Red, 3px thick
        painter.drawLine(end_x, 0, end_x, self.height())

        painter.end()
