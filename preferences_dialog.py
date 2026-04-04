# preferences_dialog.py
# Version 10.01.01.03 dated 20260115

"""
Modern Preferences Dialog with Left Sidebar Navigation

Features:
- Apple/VS Code style left sidebar navigation
- 6 organized sections (General, Appearance, Scanning, Face Detection, Video, Advanced)
- Full i18n translation support
- Responsive layout with minimum 900x600 size
- Top-right Save/Cancel buttons
- Dark mode adaptive styling
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QWidget, QLabel, QCheckBox, QComboBox, QLineEdit,
    QTextEdit, QPushButton, QSpinBox, QFormLayout, QGroupBox, QMessageBox,
    QDialogButtonBox, QScrollArea, QFileDialog, QFrame
)
from PySide6.QtCore import Qt, QSize, QProcess, QRect
from PySide6.QtGui import QGuiApplication, QPainter, QColor, QPen, QFont
import sys
from pathlib import Path

from translation_manager import get_translation_manager, tr
from utils.qt_guards import connect_guarded
from config.face_detection_config import get_face_config
from config.search_config import SearchConfig, SearchDefaults
from config.ranking_config import RankingConfig, RankingDefaults


class BadgePreviewWidget(QWidget):
    """Live preview widget showing badge samples with current settings."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 120)
        self.setMaximumHeight(120)
        self.badge_size = 22
        self.badge_shape = "circle"
        self.badge_max = 4
        self.badge_shadow = True
        self.badge_enabled = True
    
    def update_settings(self, size, shape, max_count, shadow, enabled):
        """Update preview with new settings."""
        self.badge_size = size
        self.badge_shape = shape
        self.badge_max = max_count
        self.badge_shadow = shadow
        self.badge_enabled = enabled
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        
        # Background
        painter.fillRect(self.rect(), QColor(240, 240, 245))
        
        if not self.badge_enabled:
            painter.setPen(QColor(150, 150, 150))
            font = QFont()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, "Badge overlays disabled")
            return
        
        # Draw sample thumbnail background
        thumb_rect = QRect(10, 10, 100, 100)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(200, 200, 200))
        painter.drawRoundedRect(thumb_rect, 4, 4)
        
        # Sample badges (favorite, face, tag)
        sample_badges = [
            ('★', QColor(255, 215, 0, 230), Qt.black),
            ('👤', QColor(70, 130, 180, 220), Qt.white),
            ('🏷', QColor(150, 150, 150, 230), Qt.white),
            ('⚑', QColor(255, 69, 0, 220), Qt.white),
            ('💼', QColor(0, 128, 255, 220), Qt.white)
        ]
        
        margin = 4
        x_right = thumb_rect.right() - margin - self.badge_size
        y_top = thumb_rect.top() + margin
        
        max_display = min(len(sample_badges), self.badge_max)
        
        for i in range(max_display):
            by = y_top + i * (self.badge_size + 4)
            badge_rect = QRect(x_right, by, self.badge_size, self.badge_size)
            
            # Shadow
            if self.badge_shadow:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, 100))
                shadow_rect = badge_rect.adjusted(2, 2, 2, 2)
                if self.badge_shape == 'square':
                    painter.drawRect(shadow_rect)
                elif self.badge_shape == 'rounded':
                    painter.drawRoundedRect(shadow_rect, 4, 4)
                else:
                    painter.drawEllipse(shadow_rect)
            
            # Badge
            ch, bg, fg = sample_badges[i]
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            if self.badge_shape == 'square':
                painter.drawRect(badge_rect)
            elif self.badge_shape == 'rounded':
                painter.drawRoundedRect(badge_rect, 4, 4)
            else:
                painter.drawEllipse(badge_rect)
            
            # Icon
            painter.setPen(QPen(fg))
            font = QFont()
            font.setPointSize(max(8, int(self.badge_size * 0.5)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge_rect, Qt.AlignCenter, ch)
        
        # Overflow indicator
        if len(sample_badges) > self.badge_max:
            by = y_top + max_display * (self.badge_size + 4)
            more_rect = QRect(x_right, by, self.badge_size, self.badge_size)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(60, 60, 60, 220))
            if self.badge_shape == 'square':
                painter.drawRect(more_rect)
            elif self.badge_shape == 'rounded':
                painter.drawRoundedRect(more_rect, 4, 4)
            else:
                painter.drawEllipse(more_rect)
            painter.setPen(QPen(Qt.white))
            font2 = QFont()
            font2.setPointSize(max(7, int(self.badge_size * 0.45)))
            font2.setBold(True)
            painter.setFont(font2)
            painter.drawText(more_rect, Qt.AlignCenter, f"+{len(sample_badges) - self.badge_max}")
        
        # Info text
        painter.setPen(QColor(100, 100, 100))
        info_font = QFont()
        info_font.setPointSize(9)
        painter.setFont(info_font)
        info_text = f"Preview: {self.badge_shape} • {self.badge_size}px • max {self.badge_max}"
        painter.drawText(QRect(120, 10, 160, 100), Qt.AlignLeft | Qt.AlignVCenter, info_text)


class PreferencesDialog(QDialog):
    """Modern preferences dialog with sidebar navigation and i18n support."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.face_config = get_face_config()
        self.tm = get_translation_manager()

        # Load current language from settings
        current_lang = self.settings.get("language", "en")
        self.tm.set_language(current_lang)

        self.setWindowTitle(tr("preferences.title"))
        self.setMinimumSize(900, 600)

        # Track original settings for change detection
        self.original_settings = self._capture_settings()

        self._setup_ui()
        self._load_settings()
        self._apply_styling()

        # Defer blocking status checks to avoid freezing the UI on dialog open
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self._check_model_status)
        QTimer.singleShot(100, self._update_backfill_status)
        QTimer.singleShot(150, self._update_similar_shot_status)

    def _setup_ui(self):
        """Create the main UI layout with sidebar navigation."""
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Left sidebar navigation
        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(180)
        self.sidebar.setSpacing(2)
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        # NOTE: Signal connection moved after content_stack creation to prevent AttributeError

        # Add navigation items
        nav_items = [
            ("preferences.nav.general", "⚙️"),
            ("preferences.nav.appearance", "🎨"),
            ("preferences.nav.scanning", "📁"),
            ("preferences.nav.gps_location", "🗺️"),
            ("preferences.nav.face_detection", "👤"),
            ("preferences.nav.groups", "👥"),
            ("preferences.nav.visual_embeddings", "🔍"),
            ("preferences.nav.search_discovery", "🔎"),
            ("preferences.nav.video", "🎬"),
            ("preferences.nav.advanced", "🔧")
        ]

        for key, icon in nav_items:
            item = QListWidgetItem(f"{icon}  {tr(key)}")
            item.setSizeHint(QSize(160, 40))
            self.sidebar.addItem(item)

        self.sidebar.setCurrentRow(0)

        main_layout.addWidget(self.sidebar)

        # Right side: content area with top button bar
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(20, 10, 20, 10)
        right_layout.setSpacing(10)

        # Top button bar (Save/Cancel)
        button_bar = QHBoxLayout()
        button_bar.addStretch()

        self.btn_cancel = QPushButton(tr("common.cancel"))
        self.btn_cancel.clicked.connect(self._on_cancel)

        self.btn_save = QPushButton(tr("common.save"))
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)

        button_bar.addWidget(self.btn_cancel)
        button_bar.addWidget(self.btn_save)

        right_layout.addLayout(button_bar)

        # Stacked widget for content panels
        self.content_stack = QStackedWidget()

        # Create all content panels
        self.content_stack.addWidget(self._create_general_panel())
        self.content_stack.addWidget(self._create_appearance_panel())
        self.content_stack.addWidget(self._create_scanning_panel())
        self.content_stack.addWidget(self._create_gps_location_panel())
        self.content_stack.addWidget(self._create_face_detection_panel())
        self.content_stack.addWidget(self._create_groups_panel())
        self.content_stack.addWidget(self._create_visual_embeddings_panel())
        self.content_stack.addWidget(self._create_search_discovery_panel())
        self.content_stack.addWidget(self._create_video_panel())
        self.content_stack.addWidget(self._create_advanced_panel())

        right_layout.addWidget(self.content_stack)

        main_layout.addWidget(right_widget, 1)

        # Connect sidebar signal AFTER content_stack is created (prevents AttributeError)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_changed)

    def _create_scrollable_panel(self, content_widget: QWidget) -> QScrollArea:
        """Wrap content in a scrollable area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content_widget)
        return scroll

    def _create_general_panel(self) -> QWidget:
        """Create General Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.general.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Skip unchanged photos
        self.chk_skip = QCheckBox(tr("preferences.general.skip_unchanged"))
        self.chk_skip.setToolTip(tr("preferences.general.skip_unchanged_hint"))
        layout.addWidget(self.chk_skip)

        # Use EXIF dates
        self.chk_exif = QCheckBox(tr("preferences.general.use_exif_dates"))
        self.chk_exif.setToolTip(tr("preferences.general.use_exif_dates_hint"))
        layout.addWidget(self.chk_exif)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_appearance_panel(self) -> QWidget:
        """Create Appearance panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.appearance.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Dark mode
        self.chk_dark = QCheckBox(tr("preferences.appearance.dark_mode"))
        self.chk_dark.setToolTip(tr("preferences.appearance.dark_mode_hint"))
        layout.addWidget(self.chk_dark)

        # Language selector
        lang_group = QGroupBox()
        lang_layout = QFormLayout(lang_group)
        lang_layout.setSpacing(10)

        self.cmb_language = QComboBox()
        self.cmb_language.setToolTip(tr("preferences.appearance.language_hint"))

        # Populate available languages
        for lang_code, lang_name in self.tm.get_available_languages():
            self.cmb_language.addItem(lang_name, lang_code)

        # Set current language
        current_index = self.cmb_language.findData(self.tm.current_language)
        if current_index >= 0:
            self.cmb_language.setCurrentIndex(current_index)

        lang_layout.addRow(tr("preferences.appearance.language") + ":", self.cmb_language)
        layout.addWidget(lang_group)

        # Badge overlays
        badge_group = QGroupBox(tr("preferences.appearance.badge_overlays"))
        badge_form = QFormLayout(badge_group)
        badge_form.setSpacing(10)

        self.chk_badge_overlays = QCheckBox(tr("preferences.appearance.badge_enable"))
        badge_form.addRow(self.chk_badge_overlays)

        self.spin_badge_size = QSpinBox()
        self.spin_badge_size.setRange(12, 64)
        self.spin_badge_size.setSuffix(" px")
        badge_form.addRow(tr("preferences.appearance.badge_size") + ":", self.spin_badge_size)

        self.cmb_badge_shape = QComboBox()
        self.cmb_badge_shape.addItems(["circle", "rounded", "square"])
        badge_form.addRow(tr("preferences.appearance.badge_shape") + ":", self.cmb_badge_shape)

        self.spin_badge_max = QSpinBox()
        self.spin_badge_max.setRange(1, 9)
        badge_form.addRow(tr("preferences.appearance.badge_max_count") + ":", self.spin_badge_max)

        self.chk_badge_shadow = QCheckBox(tr("preferences.appearance.badge_shadow"))
        badge_form.addRow(self.chk_badge_shadow)

        # Live preview widget
        self.badge_preview = BadgePreviewWidget()
        badge_form.addRow("", self.badge_preview)
        
        # Wire live updates
        self.chk_badge_overlays.toggled.connect(self._update_badge_preview)
        self.spin_badge_size.valueChanged.connect(self._update_badge_preview)
        self.cmb_badge_shape.currentIndexChanged.connect(self._update_badge_preview)
        self.spin_badge_max.valueChanged.connect(self._update_badge_preview)
        self.chk_badge_shadow.toggled.connect(self._update_badge_preview)

        layout.addWidget(badge_group)
        # Cache settings group
        cache_group = QGroupBox(tr("preferences.cache.title"))
        cache_layout = QVBoxLayout(cache_group)
        cache_layout.setSpacing(10)

        self.chk_cache = QCheckBox(tr("preferences.cache.enabled"))
        self.chk_cache.setToolTip(tr("preferences.cache.enabled_hint"))
        cache_layout.addWidget(self.chk_cache)

        cache_size_layout = QFormLayout()
        self.cmb_cache_size = QComboBox()
        self.cmb_cache_size.setEditable(True)
        self.cmb_cache_size.setToolTip(tr("preferences.cache.size_mb_hint"))
        for size in ["100", "250", "500", "1000", "2000"]:
            self.cmb_cache_size.addItem(size)

        cache_size_layout.addRow(tr("preferences.cache.size_mb") + ":", self.cmb_cache_size)
        cache_layout.addLayout(cache_size_layout)

        self.chk_cache_cleanup = QCheckBox(tr("preferences.cache.auto_cleanup"))
        self.chk_cache_cleanup.setToolTip(tr("preferences.cache.auto_cleanup_hint"))
        cache_layout.addWidget(self.chk_cache_cleanup)

        # Cache management buttons
        cache_btn_row = QWidget()
        cache_btn_layout = QHBoxLayout(cache_btn_row)
        cache_btn_layout.setContentsMargins(0, 8, 0, 0)

        btn_cache_stats = QPushButton("📊 Show Cache Stats")
        btn_cache_stats.setToolTip("View detailed thumbnail cache statistics")
        btn_cache_stats.setMaximumWidth(150)
        btn_cache_stats.clicked.connect(self._show_cache_stats)

        btn_purge_cache = QPushButton("🗑️ Purge Old Entries")
        btn_purge_cache.setToolTip("Remove thumbnails older than 7 days")
        btn_purge_cache.setMaximumWidth(150)
        btn_purge_cache.clicked.connect(self._purge_cache)

        cache_btn_layout.addWidget(btn_cache_stats)
        cache_btn_layout.addWidget(btn_purge_cache)
        cache_btn_layout.addStretch()

        cache_layout.addWidget(cache_btn_row)

        layout.addWidget(cache_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_scanning_panel(self) -> QWidget:
        """Create Scanning Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.scanning.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Ignore folders
        ignore_group = QGroupBox(tr("preferences.scanning.ignore_folders"))
        ignore_layout = QVBoxLayout(ignore_group)

        hint_label = QLabel(tr("preferences.scanning.ignore_folders_hint"))
        hint_label.setStyleSheet("color: gray; font-size: 9pt;")
        ignore_layout.addWidget(hint_label)

        self.txt_ignore_folders = QTextEdit()
        self.txt_ignore_folders.setPlaceholderText(tr("preferences.scanning.ignore_folders_placeholder"))
        self.txt_ignore_folders.setMaximumHeight(150)
        ignore_layout.addWidget(self.txt_ignore_folders)

        layout.addWidget(ignore_group)

        # Devices
        devices_group = QGroupBox("Devices")
        devices_layout = QVBoxLayout(devices_group)
        self.chk_device_auto_refresh = QCheckBox("Auto-detect device connections")
        self.chk_device_auto_refresh.setToolTip(
            "Automatically detect when mobile devices are connected/disconnected.\n\n"
            "• Windows: Instant detection via system events + 30s polling backup\n"
            "• Other platforms: 30s polling only\n"
            "• Disabled: Manual refresh only (click refresh button)"
        )
        devices_layout.addWidget(self.chk_device_auto_refresh)
        layout.addWidget(devices_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_gps_location_panel(self) -> QWidget:
        """Create GPS & Location Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel("GPS & Location Settings")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Location Clustering
        cluster_group = QGroupBox("🗺️ Location Clustering")
        cluster_layout = QFormLayout(cluster_group)
        cluster_layout.setSpacing(10)

        self.spin_cluster_radius = QSpinBox()
        self.spin_cluster_radius.setRange(1, 50)
        self.spin_cluster_radius.setSuffix(" km")
        self.spin_cluster_radius.setToolTip(
            "Photos within this radius will be grouped together.\n"
            "Smaller values = more precise grouping\n"
            "Larger values = fewer, broader location groups"
        )
        cluster_layout.addRow("Clustering Radius:", self.spin_cluster_radius)

        layout.addWidget(cluster_group)

        # Reverse Geocoding
        geocoding_group = QGroupBox("🌍 Reverse Geocoding")
        geocoding_layout = QVBoxLayout(geocoding_group)
        geocoding_layout.setSpacing(10)

        self.chk_reverse_geocoding = QCheckBox("Enable automatic location name lookup")
        self.chk_reverse_geocoding.setToolTip(
            "When enabled, GPS coordinates will be converted to location names\n"
            "(e.g., 'San Francisco, California, USA')\n"
            "Uses OpenStreetMap Nominatim API (free, no key required)"
        )
        geocoding_layout.addWidget(self.chk_reverse_geocoding)

        timeout_row = QWidget()
        timeout_layout = QHBoxLayout(timeout_row)
        timeout_layout.setContentsMargins(0, 0, 0, 0)
        
        timeout_label = QLabel("API Timeout:")
        self.spin_geocoding_timeout = QSpinBox()
        self.spin_geocoding_timeout.setRange(1, 10)
        self.spin_geocoding_timeout.setSuffix(" seconds")
        self.spin_geocoding_timeout.setToolTip(
            "Maximum time to wait for location name lookup.\n"
            "Lower = faster but may fail on slow connections\n"
            "Higher = more reliable but may slow down metadata display"
        )
        timeout_layout.addWidget(timeout_label)
        timeout_layout.addWidget(self.spin_geocoding_timeout)
        timeout_layout.addStretch()
        geocoding_layout.addWidget(timeout_row)

        self.chk_cache_location_names = QCheckBox("Cache location names (reduces API calls)")
        self.chk_cache_location_names.setToolTip(
            "Store location names in database to avoid repeated API lookups.\n"
            "Recommended for better performance and to respect API rate limits."
        )
        geocoding_layout.addWidget(self.chk_cache_location_names)

        layout.addWidget(geocoding_group)

        # Info box
        info_label = QLabel(
            "💡 <b>How it works:</b><br>"
            "• Photos with GPS EXIF data are automatically detected<br>"
            "• Locations are grouped by proximity using the clustering radius<br>"
            "• Click GPS coordinates in photo metadata to view on OpenStreetMap<br>"
            "• Location names are fetched using free Nominatim API (no key needed)"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "QLabel { font-size: 10pt; color: #666; padding: 8px; "
            "background: #f0f0f0; border-radius: 4px; border-left: 4px solid #0078d4; }"
        )
        layout.addWidget(info_label)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_face_detection_panel(self) -> QWidget:
        """Create Face Detection panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.face_detection.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # InsightFace Model Selection
        model_group = QGroupBox("InsightFace Model")
        model_layout = QFormLayout(model_group)
        model_layout.setSpacing(10)

        self.cmb_insightface_model = QComboBox()
        self.cmb_insightface_model.addItem("buffalo_s (Fast, smaller memory)", "buffalo_s")
        self.cmb_insightface_model.addItem("buffalo_l (Balanced, recommended)", "buffalo_l")
        self.cmb_insightface_model.addItem("antelopev2 (Most accurate)", "antelopev2")
        self.cmb_insightface_model.setToolTip(
            "Choose the face detection model:\n"
            "• buffalo_s: Faster, uses less memory\n"
            "• buffalo_l: Best balance (recommended)\n"
            "• antelopev2: Most accurate but slower"
        )
        model_layout.addRow("Model:", self.cmb_insightface_model)

        layout.addWidget(model_group)

        # InsightFace Model Path Configuration
        model_path_group = QGroupBox("Model Installation")
        model_path_layout = QVBoxLayout(model_path_group)
        model_path_layout.setSpacing(8)

        # Custom model path row
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)

        path_label = QLabel("Custom Models Path:")
        path_label.setToolTip(
            "Path to buffalo_l model directory (offline use).\n"
            "Leave empty to use default locations:\n"
            "  1. ./models/buffalo_l/\n"
            "  2. ~/.insightface/models/buffalo_l/\n\n"
            "For offline use, point to a folder containing buffalo_l models."
        )

        self.txt_model_path = QLineEdit()
        self.txt_model_path.setPlaceholderText("Leave empty to use default locations")

        btn_browse_models = QPushButton("Browse...")
        btn_browse_models.setMaximumWidth(80)
        btn_browse_models.clicked.connect(self._browse_models)

        btn_test_models = QPushButton("Test")
        btn_test_models.setMaximumWidth(60)
        btn_test_models.clicked.connect(self._test_model_path)

        path_layout.addWidget(path_label)
        path_layout.addWidget(self.txt_model_path, 1)
        path_layout.addWidget(btn_browse_models)
        path_layout.addWidget(btn_test_models)

        model_path_layout.addWidget(path_row)

        # Model status display
        self.lbl_model_status = QLabel("Checking model status...")
        self.lbl_model_status.setWordWrap(True)
        self.lbl_model_status.setStyleSheet("QLabel { padding: 6px; background-color: #f0f0f0; border-radius: 4px; }")
        model_path_layout.addWidget(self.lbl_model_status)

        # Model management buttons
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_download_models = QPushButton("📥 Download Models")
        self.btn_download_models.setToolTip("Download buffalo_l face detection models (~200MB)")
        self.btn_download_models.setMaximumWidth(150)
        self.btn_download_models.clicked.connect(self._download_models)

        self.btn_check_models = QPushButton("🔍 Check Status")
        self.btn_check_models.setToolTip("Check if models are properly installed")
        self.btn_check_models.setMaximumWidth(120)
        self.btn_check_models.clicked.connect(self._check_model_status)

        btn_layout.addWidget(self.btn_download_models)
        btn_layout.addWidget(self.btn_check_models)
        btn_layout.addStretch()

        model_path_layout.addWidget(btn_row)

        # Help text
        help_label = QLabel(
            "💡 <b>Note:</b> Face detection requires InsightFace library and buffalo_l models.<br>"
            "<b>Option 1 (Online):</b> Click 'Download Models' to download ~200MB to ./models/buffalo_l/<br>"
            "<b>Option 2 (Offline):</b> Use 'Browse' to select a folder containing pre-downloaded models"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("QLabel { font-size: 10pt; color: #666; padding: 4px; }")
        model_path_layout.addWidget(help_label)

        layout.addWidget(model_path_group)

        # Detection Settings
        detection_group = QGroupBox("Detection Settings")
        detection_layout = QFormLayout(detection_group)
        detection_layout.setSpacing(10)

        self.spin_min_face_size = QSpinBox()
        self.spin_min_face_size.setRange(10, 100)
        self.spin_min_face_size.setSuffix(" px")
        self.spin_min_face_size.setToolTip("Minimum face size in pixels (smaller = detect smaller/distant faces)")
        detection_layout.addRow("Min Face Size:", self.spin_min_face_size)

        self.spin_confidence = QSpinBox()
        self.spin_confidence.setRange(30, 95)
        self.spin_confidence.setSuffix(" %")
        self.spin_confidence.setToolTip("Minimum confidence threshold (higher = fewer false positives)")
        detection_layout.addRow("Confidence:", self.spin_confidence)

        # Quality Filtering (ENHANCEMENT 2026-01-07)
        self.spin_min_quality = QSpinBox()
        self.spin_min_quality.setRange(0, 100)
        self.spin_min_quality.setSuffix(" /100")
        self.spin_min_quality.setToolTip(
            "Minimum quality score for faces (0 = disabled)\n"
            "0 = All faces (default)\n"
            "40 = Fair quality and above\n"
            "60 = Good quality (recommended for cleaner clusters)\n"
            "80 = Excellent quality only\n\n"
            "Quality based on: blur, lighting, size, aspect ratio, confidence"
        )
        detection_layout.addRow("Min Quality:", self.spin_min_quality)

        layout.addWidget(detection_group)

        # Clustering Settings
        cluster_group = QGroupBox("Face Clustering")
        cluster_layout = QFormLayout(cluster_group)
        cluster_layout.setSpacing(10)

        self.spin_cluster_eps = QSpinBox()
        self.spin_cluster_eps.setRange(20, 60)
        self.spin_cluster_eps.setSuffix(" %")
        self.spin_cluster_eps.setToolTip(
            "Clustering threshold (lower = stricter grouping):\n"
            "• 30-35%: Recommended (prevents grouping different people)\n"
            "• <30%: Very strict (may split same person)\n"
            "• >40%: Loose (may group different people)"
        )
        cluster_layout.addRow("Threshold (eps):", self.spin_cluster_eps)

        self.spin_min_samples = QSpinBox()
        self.spin_min_samples.setRange(1, 10)
        self.spin_min_samples.setToolTip("Minimum faces needed to form a cluster")
        cluster_layout.addRow("Min Samples:", self.spin_min_samples)

        self.chk_auto_cluster = QCheckBox("Auto-cluster after face detection scan")
        self.chk_auto_cluster.setToolTip("Automatically group faces after detection completes")
        cluster_layout.addRow("", self.chk_auto_cluster)

        layout.addWidget(cluster_group)

        # Screenshot Face Handling
        screenshot_group = QGroupBox("Screenshot Face Handling")
        screenshot_layout = QFormLayout(screenshot_group)
        screenshot_layout.setSpacing(10)

        self.cmb_screenshot_face_policy = QComboBox()
        self.cmb_screenshot_face_policy.addItem("Exclude screenshots", "exclude")
        self.cmb_screenshot_face_policy.addItem("Detect only, exclude from clustering", "detect_only")
        self.cmb_screenshot_face_policy.addItem("Detect and cluster screenshots", "include_cluster")
        self.cmb_screenshot_face_policy.setToolTip(
            "Default screenshot handling policy for face detection and clustering.\n"
            "This can still be overridden in the Face Detection dialog for a specific run."
        )
        screenshot_layout.addRow("Default Screenshot Policy:", self.cmb_screenshot_face_policy)

        self.chk_include_all_screenshot_faces = QCheckBox(
            "When clustering screenshots, keep all detected screenshot faces"
        )
        self.chk_include_all_screenshot_faces.setToolTip(
            "If enabled, screenshot-origin faces will not be capped before clustering.\n"
            "Warning: this may increase noise and singleton clusters."
        )
        screenshot_layout.addRow("", self.chk_include_all_screenshot_faces)

        self.lbl_screenshot_cluster_warning = QLabel(
            "Warning: enabling full screenshot-face inclusion may increase false splits, singleton clusters, and People clutter."
        )
        self.lbl_screenshot_cluster_warning.setWordWrap(True)
        self.lbl_screenshot_cluster_warning.setStyleSheet("color: #aa5500; font-size: 8.5pt;")
        screenshot_layout.addRow("", self.lbl_screenshot_cluster_warning)

        layout.addWidget(screenshot_group)

        # Per-Project Overrides
        project_group = QGroupBox("Per-Project Overrides")
        project_form = QFormLayout(project_group)
        project_form.setSpacing(10)

        from app_services import get_default_project_id
        self.current_project_id = get_default_project_id() or 1
        self.lbl_project_info = QLabel(f"Current project ID: {self.current_project_id}")
        project_form.addRow(self.lbl_project_info)

        self.chk_project_overrides = QCheckBox("Enable per-project overrides for this project")
        project_form.addRow("", self.chk_project_overrides)

        self.spin_proj_min_face = QSpinBox()
        self.spin_proj_min_face.setRange(10, 100)
        self.spin_proj_min_face.setSuffix(" px")
        project_form.addRow("Min Face Size (project):", self.spin_proj_min_face)

        self.spin_proj_confidence = QSpinBox()
        self.spin_proj_confidence.setRange(10, 95)
        self.spin_proj_confidence.setSuffix(" %")
        project_form.addRow("Confidence (project):", self.spin_proj_confidence)

        self.spin_proj_eps = QSpinBox()
        self.spin_proj_eps.setRange(20, 60)
        self.spin_proj_eps.setSuffix(" %")
        project_form.addRow("Threshold eps (project):", self.spin_proj_eps)

        self.spin_proj_min_samples = QSpinBox()
        self.spin_proj_min_samples.setRange(1, 10)
        project_form.addRow("Min Samples (project):", self.spin_proj_min_samples)

        self.chk_show_low_conf = QCheckBox("Show low-confidence detections in UI")
        project_form.addRow("", self.chk_show_low_conf)

        layout.addWidget(project_group)

        # Performance Settings
        perf_group = QGroupBox("Performance")
        perf_layout = QFormLayout(perf_group)
        perf_layout.setSpacing(10)

        self.spin_max_workers = QSpinBox()
        self.spin_max_workers.setRange(1, 16)
        self.spin_max_workers.setToolTip("Number of parallel face detection workers")
        perf_layout.addRow("Max Workers:", self.spin_max_workers)

        self.spin_batch_size = QSpinBox()
        self.spin_batch_size.setRange(10, 200)
        self.spin_batch_size.setToolTip("Number of images to process before saving to database")
        perf_layout.addRow("Batch Size:", self.spin_batch_size)

        # GPU Batch Processing (ENHANCEMENT 2026-01-07)
        self.chk_gpu_batch = QCheckBox("Enable GPU batch processing (2-5x speedup)")
        self.chk_gpu_batch.setToolTip("Process multiple images in parallel on GPU (requires CUDA)")
        perf_layout.addRow("", self.chk_gpu_batch)

        self.spin_gpu_batch_size = QSpinBox()
        self.spin_gpu_batch_size.setRange(1, 16)
        self.spin_gpu_batch_size.setToolTip("Number of images to process in single GPU call (4 optimal for 6-8GB VRAM)")
        perf_layout.addRow("GPU Batch Size:", self.spin_gpu_batch_size)

        self.spin_gpu_batch_min = QSpinBox()
        self.spin_gpu_batch_min.setRange(1, 100)
        self.spin_gpu_batch_min.setToolTip("Minimum photos to enable batch processing (overhead not worth it for small jobs)")
        perf_layout.addRow("GPU Batch Threshold:", self.spin_gpu_batch_min)

        layout.addWidget(perf_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_groups_panel(self) -> QWidget:
        """Create Person Groups settings panel.

        Configures global defaults for the Groups feature:
        - Default match scope (Same Photo vs Event Window)
        - Auto-indexing behavior
        - Cache/performance settings
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.groups.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Description
        desc = QLabel(tr("preferences.groups.description"))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc)

        # Match Scope Settings
        scope_group = QGroupBox(tr("preferences.groups.match_scope"))
        scope_layout = QFormLayout(scope_group)
        scope_layout.setSpacing(10)

        self.cmb_group_match_scope = QComboBox()
        self.cmb_group_match_scope.addItem(tr("preferences.groups.scope_same_photo"), "same_photo")
        self.cmb_group_match_scope.addItem(tr("preferences.groups.scope_event_window"), "event_window")
        self.cmb_group_match_scope.setToolTip(tr("preferences.groups.scope_tooltip"))
        scope_layout.addRow(tr("preferences.groups.default_scope") + ":", self.cmb_group_match_scope)

        self.spin_event_window_hours = QSpinBox()
        self.spin_event_window_hours.setRange(1, 168)  # 1 hour to 1 week
        self.spin_event_window_hours.setValue(24)
        self.spin_event_window_hours.setSuffix(" hours")
        self.spin_event_window_hours.setToolTip(tr("preferences.groups.event_window_tooltip"))
        scope_layout.addRow(tr("preferences.groups.event_window_size") + ":", self.spin_event_window_hours)

        layout.addWidget(scope_group)

        # Indexing Settings
        index_group = QGroupBox(tr("preferences.groups.indexing"))
        index_layout = QFormLayout(index_group)
        index_layout.setSpacing(10)

        self.chk_auto_index_groups = QCheckBox(tr("preferences.groups.auto_index"))
        self.chk_auto_index_groups.setToolTip(tr("preferences.groups.auto_index_tooltip"))
        index_layout.addRow(self.chk_auto_index_groups)

        self.chk_incremental_index = QCheckBox(tr("preferences.groups.incremental_index"))
        self.chk_incremental_index.setToolTip(tr("preferences.groups.incremental_index_tooltip"))
        index_layout.addRow(self.chk_incremental_index)

        layout.addWidget(index_group)

        # Display Settings
        display_group = QGroupBox(tr("preferences.groups.display"))
        display_layout = QFormLayout(display_group)
        display_layout.setSpacing(10)

        self.spin_group_avatar_count = QSpinBox()
        self.spin_group_avatar_count.setRange(2, 6)
        self.spin_group_avatar_count.setValue(4)
        self.spin_group_avatar_count.setToolTip(tr("preferences.groups.avatar_count_tooltip"))
        display_layout.addRow(tr("preferences.groups.max_avatars") + ":", self.spin_group_avatar_count)

        self.chk_show_group_photo_count = QCheckBox(tr("preferences.groups.show_photo_count"))
        self.chk_show_group_photo_count.setChecked(True)
        display_layout.addRow(self.chk_show_group_photo_count)

        layout.addWidget(display_group)

        # Cache Settings
        cache_group = QGroupBox(tr("preferences.groups.cache"))
        cache_layout = QFormLayout(cache_group)
        cache_layout.setSpacing(10)

        self.chk_cache_group_matches = QCheckBox(tr("preferences.groups.cache_matches"))
        self.chk_cache_group_matches.setChecked(True)
        self.chk_cache_group_matches.setToolTip(tr("preferences.groups.cache_matches_tooltip"))
        cache_layout.addRow(self.chk_cache_group_matches)

        self.btn_clear_group_cache = QPushButton(tr("preferences.groups.clear_cache"))
        self.btn_clear_group_cache.clicked.connect(self._on_clear_group_cache)
        cache_layout.addRow(self.btn_clear_group_cache)

        layout.addWidget(cache_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _on_clear_group_cache(self):
        """Clear the materialized group matches cache."""
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            tr("preferences.groups.clear_cache_confirm_title"),
            tr("preferences.groups.clear_cache_confirm_message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                from services.group_service import GroupService
                from reference_db import ReferenceDB
                db = ReferenceDB()
                # Clear all cached matches and recompute
                results = GroupService.reindex_all_groups(db, self.current_project_id)
                db.close()
                total = sum(results.values()) if results else 0
                QMessageBox.information(
                    self,
                    tr("preferences.groups.cache_cleared_title"),
                    tr("preferences.groups.cache_cleared_message")
                )
            except Exception as e:
                QMessageBox.warning(
                    self,
                    tr("common.error"),
                    f"Failed to clear cache: {e}"
                )

    def _create_visual_embeddings_panel(self) -> QWidget:
        """Create Visual Embeddings / CLIP Model panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel("🔍 Visual Embeddings")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "Visual embeddings enable semantic image search using AI vision models. "
            "Search photos by describing what you're looking for (e.g., 'sunset at beach', 'cat on sofa')."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; font-size: 10pt; padding: 4px;")
        layout.addWidget(desc)

        # CLIP Model Installation
        model_group = QGroupBox("CLIP Model Installation")
        model_layout = QVBoxLayout(model_group)
        model_layout.setSpacing(8)

        # Model location row
        location_row = QWidget()
        location_layout = QHBoxLayout(location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)

        location_label = QLabel("Model Location:")
        location_label.setToolTip(
            "Path to CLIP model directory.\n"
            "Default: ./models/clip-vit-base-patch32/\n\n"
            "This is where CLIP model files are stored (~600MB)."
        )

        self.txt_clip_model_path = QLineEdit()
        self.txt_clip_model_path.setPlaceholderText("Default: ./models/clip-vit-base-patch32/")
        self.txt_clip_model_path.setReadOnly(True)  # Set by system

        btn_browse_clip = QPushButton("Browse...")
        btn_browse_clip.setMaximumWidth(80)
        btn_browse_clip.clicked.connect(self._browse_clip_models)

        btn_open_folder = QPushButton("Open Folder")
        btn_open_folder.setMaximumWidth(100)
        btn_open_folder.clicked.connect(self._open_clip_model_folder)

        location_layout.addWidget(location_label)
        location_layout.addWidget(self.txt_clip_model_path, 1)
        location_layout.addWidget(btn_browse_clip)
        location_layout.addWidget(btn_open_folder)

        model_layout.addWidget(location_row)

        # Model status display
        self.lbl_clip_status = QLabel("Checking CLIP model status...")
        self.lbl_clip_status.setWordWrap(True)
        self.lbl_clip_status.setStyleSheet("QLabel { padding: 8px; background-color: #f0f0f0; border-radius: 4px; }")
        model_layout.addWidget(self.lbl_clip_status)

        # Model management buttons
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_download_clip = QPushButton("📥 Download CLIP Model")
        self.btn_download_clip.setToolTip("Run download script to get CLIP model files (~600MB)")
        self.btn_download_clip.setMaximumWidth(180)
        self.btn_download_clip.clicked.connect(self._download_clip_model)

        self.btn_check_clip = QPushButton("🔍 Check Status")
        self.btn_check_clip.setToolTip("Check if CLIP model is properly installed")
        self.btn_check_clip.setMaximumWidth(120)
        self.btn_check_clip.clicked.connect(self._check_clip_status)

        btn_layout.addWidget(self.btn_download_clip)
        btn_layout.addWidget(self.btn_check_clip)
        btn_layout.addStretch()

        model_layout.addWidget(btn_row)

        # Help text
        help_label = QLabel(
            "💡 <b>Getting Started:</b><br>"
            "1. Click 'Download CLIP Model' to download OpenAI CLIP ViT-B/32 (~600MB)<br>"
            "2. Files will be saved to <code>./models/clip-vit-base-patch32/</code> (next to face detection models)<br>"
            "3. After download, restart the app or retry embedding extraction<br>"
            "4. Use 'Extract Embeddings' in the main window to process your photos"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("QLabel { font-size: 10pt; color: #666; padding: 6px; background-color: #f9f9f9; border-radius: 4px; }")
        model_layout.addWidget(help_label)

        layout.addWidget(model_group)

        # Model Configuration
        config_group = QGroupBox("Model Configuration")
        config_layout = QFormLayout(config_group)
        config_layout.setSpacing(10)

        self.cmb_clip_variant = QComboBox()
        self.cmb_clip_variant.addItem("CLIP ViT-B/32 (512-D, Fast, Recommended)", "openai/clip-vit-base-patch32")
        self.cmb_clip_variant.addItem("CLIP ViT-B/16 (512-D, Better Quality)", "openai/clip-vit-base-patch16")
        self.cmb_clip_variant.addItem("CLIP ViT-L/14 (768-D, Best Quality)", "openai/clip-vit-large-patch14")
        self.cmb_clip_variant.setToolTip(
            "Choose CLIP model variant:\n"
            "• ViT-B/32: Fastest, balanced quality (recommended)\n"
            "• ViT-B/16: Slower, better image understanding\n"
            "• ViT-L/14: Slowest, best quality (requires more memory)"
        )
        self.cmb_clip_variant.setEnabled(False)  # Only ViT-B/32 supported for now
        config_layout.addRow("Model Variant:", self.cmb_clip_variant)

        self.cmb_clip_device = QComboBox()
        self.cmb_clip_device.addItem("Auto (Use GPU if available)", "auto")
        self.cmb_clip_device.addItem("CPU Only", "cpu")
        self.cmb_clip_device.addItem("CUDA GPU", "cuda")
        self.cmb_clip_device.addItem("Apple Metal (MPS)", "mps")
        self.cmb_clip_device.setToolTip(
            "Select compute device:\n"
            "• Auto: Automatically use GPU if available, else CPU\n"
            "• CPU: Always use CPU (slower but works everywhere)\n"
            "• CUDA: Use NVIDIA GPU (requires CUDA toolkit)\n"
            "• MPS: Use Apple Silicon GPU (M1/M2 Macs)"
        )
        config_layout.addRow("Compute Device:", self.cmb_clip_device)

        layout.addWidget(config_group)

        # Extraction Settings
        extraction_group = QGroupBox("Extraction Settings")
        extraction_layout = QFormLayout(extraction_group)
        extraction_layout.setSpacing(10)

        self.chk_auto_extract = QCheckBox("Automatically extract embeddings after photo scan")
        self.chk_auto_extract.setToolTip("Auto-extract visual embeddings when new photos are imported")
        extraction_layout.addRow("", self.chk_auto_extract)

        self.spin_extraction_batch = QSpinBox()
        self.spin_extraction_batch.setRange(10, 500)
        self.spin_extraction_batch.setValue(50)
        self.spin_extraction_batch.setSuffix(" photos")
        self.spin_extraction_batch.setToolTip("Number of photos to process in each batch")
        extraction_layout.addRow("Batch Size:", self.spin_extraction_batch)

        layout.addWidget(extraction_group)

        # GPU & Performance Section
        gpu_group = QGroupBox("GPU & Performance")
        gpu_layout = QFormLayout(gpu_group)
        gpu_layout.setSpacing(10)

        # GPU Device Status
        self.lbl_gpu_device = QLabel("Detecting...")
        self.lbl_gpu_device.setStyleSheet("font-weight: bold;")
        gpu_layout.addRow("GPU Device:", self.lbl_gpu_device)

        # GPU Memory
        self.lbl_gpu_memory = QLabel("—")
        gpu_layout.addRow("Available Memory:", self.lbl_gpu_memory)

        # Optimal Batch Size
        self.lbl_optimal_batch = QLabel("—")
        self.lbl_optimal_batch.setToolTip("Auto-tuned batch size based on GPU memory")
        gpu_layout.addRow("Optimal Batch Size:", self.lbl_optimal_batch)

        # FAISS Status
        self.lbl_faiss_status = QLabel("Checking...")
        self.lbl_faiss_status.setToolTip("FAISS enables fast approximate nearest neighbor search")
        gpu_layout.addRow("FAISS (Fast Search):", self.lbl_faiss_status)

        # Refresh GPU info button
        btn_refresh_gpu = QPushButton("🔄 Refresh")
        btn_refresh_gpu.setMaximumWidth(100)
        btn_refresh_gpu.clicked.connect(self._refresh_gpu_info)
        gpu_layout.addRow("", btn_refresh_gpu)

        layout.addWidget(gpu_group)

        # Statistics & Storage Section
        stats_group = QGroupBox("Statistics & Storage")
        stats_layout = QVBoxLayout(stats_group)
        stats_layout.setSpacing(10)

        # Quick coverage summary
        self.lbl_embedding_coverage = QLabel("Loading statistics...")
        self.lbl_embedding_coverage.setStyleSheet("font-size: 11pt;")
        stats_layout.addWidget(self.lbl_embedding_coverage)

        # Storage format info
        self.lbl_storage_format = QLabel("")
        self.lbl_storage_format.setStyleSheet("color: #666;")
        stats_layout.addWidget(self.lbl_storage_format)

        # Action buttons row
        stats_btn_row = QWidget()
        stats_btn_layout = QHBoxLayout(stats_btn_row)
        stats_btn_layout.setContentsMargins(0, 4, 0, 0)

        btn_open_dashboard = QPushButton("📊 Open Statistics Dashboard")
        btn_open_dashboard.setToolTip("View detailed embedding statistics, coverage, and storage info")
        btn_open_dashboard.clicked.connect(self._open_embedding_stats_dashboard)
        stats_btn_layout.addWidget(btn_open_dashboard)

        btn_migrate_float16 = QPushButton("⚡ Migrate to Float16")
        btn_migrate_float16.setToolTip("Convert legacy float32 embeddings to float16 (50% space savings)")
        btn_migrate_float16.clicked.connect(self._migrate_embeddings_to_float16)
        self.btn_migrate_float16 = btn_migrate_float16
        stats_btn_layout.addWidget(btn_migrate_float16)

        stats_btn_layout.addStretch()
        stats_layout.addWidget(stats_btn_row)

        layout.addWidget(stats_group)

        layout.addStretch()

        # Check CLIP status after UI is ready
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._check_clip_status)
        QTimer.singleShot(200, self._refresh_gpu_info)
        QTimer.singleShot(300, self._refresh_embedding_stats)

        return self._create_scrollable_panel(widget)

    def _create_search_discovery_panel(self) -> QWidget:
        """Create Search & Discovery settings panel.

        Configures all search parameters:
        - Smart Find CLIP threshold, top_k, cache TTL
        - Semantic search min similarity, top_k
        - NLP parsing toggle
        - Confidence display settings
        - Search debounce timing
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel("🔎 Search & Discovery")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "Tune search sensitivity, result limits, and behavior for Smart Find "
            "presets and free-text semantic search. Settings inspired by Google Photos, "
            "Apple Photos, Lightroom, and Excire best practices."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; font-size: 10pt; padding: 4px;")
        layout.addWidget(desc)

        # ── Smart Find Settings ──
        smart_find_group = QGroupBox("Smart Find (Presets & Text Search)")
        sf_layout = QFormLayout(smart_find_group)
        sf_layout.setSpacing(10)

        # CLIP Threshold slider (0.05 - 0.50, shown as percentage)
        clip_row = QWidget()
        clip_row_layout = QHBoxLayout(clip_row)
        clip_row_layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QSlider
        self.slider_clip_threshold = QSlider(Qt.Horizontal)
        self.slider_clip_threshold.setRange(5, 50)  # 0.05 to 0.50
        self.slider_clip_threshold.setTickPosition(QSlider.TicksBelow)
        self.slider_clip_threshold.setTickInterval(5)
        self.lbl_clip_threshold_val = QLabel("0.22")
        self.lbl_clip_threshold_val.setMinimumWidth(40)
        self.slider_clip_threshold.valueChanged.connect(
            lambda v: self.lbl_clip_threshold_val.setText(f"{v / 100:.2f}")
        )
        clip_row_layout.addWidget(self.slider_clip_threshold, 1)
        clip_row_layout.addWidget(self.lbl_clip_threshold_val)

        sf_layout.addRow("CLIP Sensitivity:", clip_row)

        clip_hint = QLabel(
            "Lower = more results (broader matching). Higher = fewer results (stricter).\n"
            "Default: 0.22. Range: 0.10 (very broad) to 0.40 (very strict)."
        )
        clip_hint.setWordWrap(True)
        clip_hint.setStyleSheet("color: #888; font-size: 9pt; padding-left: 4px;")
        sf_layout.addRow("", clip_hint)

        # Max Results
        self.spin_search_top_k = QSpinBox()
        self.spin_search_top_k.setRange(10, 2000)
        self.spin_search_top_k.setSingleStep(50)
        self.spin_search_top_k.setSuffix(" photos")
        self.spin_search_top_k.setToolTip(
            "Maximum number of results returned per Smart Find query.\n"
            "Higher values show more results but may be slower.\n"
            "Default: 200"
        )
        sf_layout.addRow("Max Results:", self.spin_search_top_k)

        # Cache TTL
        self.spin_cache_ttl = QSpinBox()
        self.spin_cache_ttl.setRange(0, 3600)
        self.spin_cache_ttl.setSingleStep(30)
        self.spin_cache_ttl.setSuffix(" sec")
        self.spin_cache_ttl.setToolTip(
            "How long search results are cached before re-querying.\n"
            "0 = no caching (always fresh results).\n"
            "Default: 300 seconds (5 minutes)"
        )
        sf_layout.addRow("Cache Duration:", self.spin_cache_ttl)

        # Search Debounce
        self.spin_debounce = QSpinBox()
        self.spin_debounce.setRange(100, 2000)
        self.spin_debounce.setSingleStep(50)
        self.spin_debounce.setSuffix(" ms")
        self.spin_debounce.setToolTip(
            "Delay before executing search after typing stops.\n"
            "Lower = faster response, Higher = fewer intermediate queries.\n"
            "Default: 500ms"
        )
        sf_layout.addRow("Input Debounce:", self.spin_debounce)

        # NLP Parsing toggle
        self.chk_nlp_enabled = QCheckBox("Enable NLP query parsing")
        self.chk_nlp_enabled.setToolTip(
            "Parse natural language queries to extract dates, ratings, and media types\n"
            "before CLIP search. Example: 'sunset from 2024' extracts year filter.\n"
            "Disable to always use raw CLIP text search."
        )
        sf_layout.addRow("", self.chk_nlp_enabled)

        layout.addWidget(smart_find_group)

        # ── Semantic Search Settings (Toolbar) ──
        semantic_group = QGroupBox("Semantic Search (Toolbar)")
        sem_layout = QFormLayout(semantic_group)
        sem_layout.setSpacing(10)

        # Min similarity slider
        sem_row = QWidget()
        sem_row_layout = QHBoxLayout(sem_row)
        sem_row_layout.setContentsMargins(0, 0, 0, 0)

        self.slider_semantic_sim = QSlider(Qt.Horizontal)
        self.slider_semantic_sim.setRange(5, 60)  # 0.05 to 0.60
        self.slider_semantic_sim.setTickPosition(QSlider.TicksBelow)
        self.slider_semantic_sim.setTickInterval(5)
        self.lbl_semantic_sim_val = QLabel("0.30")
        self.lbl_semantic_sim_val.setMinimumWidth(40)
        self.slider_semantic_sim.valueChanged.connect(
            lambda v: self.lbl_semantic_sim_val.setText(f"{v / 100:.2f}")
        )
        sem_row_layout.addWidget(self.slider_semantic_sim, 1)
        sem_row_layout.addWidget(self.lbl_semantic_sim_val)

        sem_layout.addRow("Min Similarity:", sem_row)

        sem_hint = QLabel(
            "Minimum relevance score for toolbar semantic search results.\n"
            "Default: 0.30. Lower shows more results, higher is stricter."
        )
        sem_hint.setWordWrap(True)
        sem_hint.setStyleSheet("color: #888; font-size: 9pt; padding-left: 4px;")
        sem_layout.addRow("", sem_hint)

        # Semantic top_k
        self.spin_semantic_top_k = QSpinBox()
        self.spin_semantic_top_k.setRange(5, 500)
        self.spin_semantic_top_k.setSingleStep(5)
        self.spin_semantic_top_k.setSuffix(" results")
        self.spin_semantic_top_k.setToolTip(
            "Maximum results for toolbar semantic search.\n"
            "Default: 20"
        )
        sem_layout.addRow("Max Results:", self.spin_semantic_top_k)

        layout.addWidget(semantic_group)

        # ── Score Fusion & Scoring ──
        fusion_group = QGroupBox("Score Fusion & Hybrid Search")
        fus_layout = QFormLayout(fusion_group)
        fus_layout.setSpacing(10)

        # Fusion mode combo
        self.combo_fusion_mode = QComboBox()
        self.combo_fusion_mode.addItems(["max", "weighted_max", "soft_or"])
        self.combo_fusion_mode.setToolTip(
            "How multi-prompt CLIP scores are combined:\n"
            "• max — highest single-prompt score wins (fast, simple)\n"
            "• weighted_max — best prompt gets 70%, second-best 30%\n"
            "• soft_or — probabilistic union (rewards matching multiple prompts)\n"
            "Default: max"
        )
        fus_layout.addRow("Fusion Mode:", self.combo_fusion_mode)

        # Semantic weight slider (0.0 - 1.0)
        sw_row = QWidget()
        sw_row_layout = QHBoxLayout(sw_row)
        sw_row_layout.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QSlider
        self.slider_semantic_weight = QSlider(Qt.Horizontal)
        self.slider_semantic_weight.setRange(0, 100)  # 0.00 to 1.00
        self.slider_semantic_weight.setTickPosition(QSlider.TicksBelow)
        self.slider_semantic_weight.setTickInterval(10)
        self.lbl_semantic_weight_val = QLabel("0.80")
        self.lbl_semantic_weight_val.setMinimumWidth(40)
        self.slider_semantic_weight.valueChanged.connect(
            lambda v: self.lbl_semantic_weight_val.setText(f"{v / 100:.2f}")
        )
        sw_row_layout.addWidget(self.slider_semantic_weight, 1)
        sw_row_layout.addWidget(self.lbl_semantic_weight_val)
        fus_layout.addRow("Semantic Weight:", sw_row)

        sw_hint = QLabel(
            "Balance between CLIP semantic score and metadata boost.\n"
            "1.0 = pure semantic. 0.0 = pure metadata. Default: 0.80"
        )
        sw_hint.setWordWrap(True)
        sw_hint.setStyleSheet("color: #888; font-size: 9pt; padding-left: 4px;")
        fus_layout.addRow("", sw_hint)

        # Threshold backoff toggle
        self.chk_threshold_backoff = QCheckBox("Enable dynamic threshold backoff")
        self.chk_threshold_backoff.setToolTip(
            "When a query returns 0 results, automatically retry with\n"
            "a lower CLIP threshold (up to 2 retries, step -0.04).\n"
            "Prevents empty result screens for borderline queries."
        )
        fus_layout.addRow("", self.chk_threshold_backoff)

        layout.addWidget(fusion_group)

        # ── Ranking Weights (Default / Scenic Profile) ──
        ranking_group = QGroupBox("Ranking Weights (Default Profile)")
        rank_layout = QFormLayout(ranking_group)
        rank_layout.setSpacing(8)

        rank_desc = QLabel(
            "Controls how different signals are weighted when scoring search results.\n"
            "Weights should sum to 1.0 — they are auto-normalized on save.\n"
            "Applies to the default (scenic/general) search profile."
        )
        rank_desc.setWordWrap(True)
        rank_desc.setStyleSheet("color: #888; font-size: 9pt; padding: 2px;")
        rank_layout.addRow("", rank_desc)

        # Helper to create a weight slider row (0-100 mapped to 0.00-1.00)
        def _make_weight_slider(default_val, tooltip):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(10)
            slider.setValue(int(default_val * 100))
            slider.setToolTip(tooltip)
            lbl = QLabel(f"{default_val:.2f}")
            lbl.setMinimumWidth(40)
            slider.valueChanged.connect(lambda v, l=lbl: l.setText(f"{v / 100:.2f}"))
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(lbl)
            return row, slider, lbl

        from PySide6.QtWidgets import QSlider

        row, self.slider_rank_clip, self.lbl_rank_clip = _make_weight_slider(
            0.75, "CLIP semantic similarity weight.\nHigher = rely more on visual/text match. Default: 0.75")
        rank_layout.addRow("CLIP Weight:", row)

        row, self.slider_rank_recency, self.lbl_rank_recency = _make_weight_slider(
            0.05, "Recency weight — boost newer photos.\nDefault: 0.05")
        rank_layout.addRow("Recency Weight:", row)

        row, self.slider_rank_favorite, self.lbl_rank_favorite = _make_weight_slider(
            0.08, "Favorite/rating weight — boost favorited photos.\nDefault: 0.08")
        rank_layout.addRow("Favorite Weight:", row)

        row, self.slider_rank_location, self.lbl_rank_location = _make_weight_slider(
            0.04, "Location weight — boost photos with GPS data.\nDefault: 0.04")
        rank_layout.addRow("Location Weight:", row)

        row, self.slider_rank_face, self.lbl_rank_face = _make_weight_slider(
            0.08, "Face match weight — boost photos with detected faces.\nDefault: 0.08")
        rank_layout.addRow("Face Match Weight:", row)

        row, self.slider_rank_structural, self.lbl_rank_structural = _make_weight_slider(
            0.00, "Structural weight — reserved for document/screenshot signals.\nDefault: 0.00")
        rank_layout.addRow("Structural Weight:", row)

        # Weight sum indicator
        self.lbl_weight_sum = QLabel("Sum: 1.00")
        self.lbl_weight_sum.setStyleSheet("font-weight: bold; padding-left: 4px;")

        def _update_weight_sum():
            total = sum(s.value() for s in [
                self.slider_rank_clip, self.slider_rank_recency, self.slider_rank_favorite,
                self.slider_rank_location, self.slider_rank_face, self.slider_rank_structural
            ]) / 100.0
            color = "#2ecc71" if abs(total - 1.0) < 0.02 else "#e74c3c"
            self.lbl_weight_sum.setText(f"Sum: {total:.2f}")
            self.lbl_weight_sum.setStyleSheet(f"font-weight: bold; padding-left: 4px; color: {color};")

        for s in [self.slider_rank_clip, self.slider_rank_recency, self.slider_rank_favorite,
                  self.slider_rank_location, self.slider_rank_face, self.slider_rank_structural]:
            s.valueChanged.connect(lambda _: _update_weight_sum())
        rank_layout.addRow("", self.lbl_weight_sum)

        # Guardrails sub-section
        guard_label = QLabel("Guardrails:")
        guard_label.setStyleSheet("font-weight: bold; padding-top: 6px;")
        rank_layout.addRow(guard_label, QWidget())

        row, self.slider_max_recency_boost, self.lbl_max_recency_boost = _make_weight_slider(
            0.10, "Maximum recency boost cap.\nDefault: 0.10")
        rank_layout.addRow("Max Recency Boost:", row)

        row, self.slider_max_favorite_boost, self.lbl_max_favorite_boost = _make_weight_slider(
            0.15, "Maximum favorite boost cap.\nDefault: 0.15")
        rank_layout.addRow("Max Favorite Boost:", row)

        self.spin_recency_halflife = QSpinBox()
        self.spin_recency_halflife.setRange(1, 730)
        self.spin_recency_halflife.setSingleStep(10)
        self.spin_recency_halflife.setSuffix(" days")
        self.spin_recency_halflife.setToolTip(
            "Half-life for recency decay.\n"
            "Photos older than this get ~50% of the recency boost.\n"
            "Default: 90 days"
        )
        rank_layout.addRow("Recency Half-life:", self.spin_recency_halflife)

        layout.addWidget(ranking_group)

        # ── Metadata Boosts ──
        meta_group = QGroupBox("Metadata Soft-Boosts")
        meta_layout = QFormLayout(meta_group)
        meta_layout.setSpacing(8)

        meta_desc = QLabel(
            "Small additive bonuses applied to results that have matching metadata.\n"
            "These are applied on top of the main score to reward completeness."
        )
        meta_desc.setWordWrap(True)
        meta_desc.setStyleSheet("color: #888; font-size: 9pt; padding: 2px;")
        meta_layout.addRow("", meta_desc)

        # Helper for boost sliders (0-50 mapped to 0.00-0.50)
        def _make_boost_slider(default_val, tooltip):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 50)
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(5)
            slider.setValue(int(default_val * 100))
            slider.setToolTip(tooltip)
            lbl = QLabel(f"{default_val:.2f}")
            lbl.setMinimumWidth(40)
            slider.valueChanged.connect(lambda v, l=lbl: l.setText(f"{v / 100:.2f}"))
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(lbl)
            return row, slider, lbl

        row, self.slider_boost_gps, self.lbl_boost_gps = _make_boost_slider(
            0.05, "Bonus for photos with GPS coordinates.\nDefault: 0.05")
        meta_layout.addRow("GPS Boost:", row)

        row, self.slider_boost_rating, self.lbl_boost_rating = _make_boost_slider(
            0.10, "Bonus for photos with star ratings/favorites.\nDefault: 0.10")
        meta_layout.addRow("Rating Boost:", row)

        row, self.slider_boost_date, self.lbl_boost_date = _make_boost_slider(
            0.03, "Bonus for photos with date metadata.\nDefault: 0.03")
        meta_layout.addRow("Date Boost:", row)

        layout.addWidget(meta_group)

        # ── Threshold Backoff Parameters ──
        backoff_group = QGroupBox("Threshold Backoff Parameters")
        bo_layout = QFormLayout(backoff_group)
        bo_layout.setSpacing(8)

        bo_desc = QLabel(
            "When a search returns too few results, the threshold is automatically\n"
            "lowered by this step for up to N retries to find more matches."
        )
        bo_desc.setWordWrap(True)
        bo_desc.setStyleSheet("color: #888; font-size: 9pt; padding: 2px;")
        bo_layout.addRow("", bo_desc)

        # Backoff step slider (0.01 to 0.20)
        bo_step_row = QWidget()
        bo_step_layout = QHBoxLayout(bo_step_row)
        bo_step_layout.setContentsMargins(0, 0, 0, 0)
        self.slider_backoff_step = QSlider(Qt.Horizontal)
        self.slider_backoff_step.setRange(1, 20)  # 0.01 to 0.20
        self.slider_backoff_step.setTickPosition(QSlider.TicksBelow)
        self.slider_backoff_step.setTickInterval(2)
        self.lbl_backoff_step = QLabel("0.04")
        self.lbl_backoff_step.setMinimumWidth(40)
        self.slider_backoff_step.valueChanged.connect(
            lambda v: self.lbl_backoff_step.setText(f"{v / 100:.2f}")
        )
        bo_step_layout.addWidget(self.slider_backoff_step, 1)
        bo_step_layout.addWidget(self.lbl_backoff_step)
        bo_layout.addRow("Backoff Step:", bo_step_row)

        # Max retries
        self.spin_backoff_retries = QSpinBox()
        self.spin_backoff_retries.setRange(0, 5)
        self.spin_backoff_retries.setToolTip(
            "Maximum number of backoff retries.\n"
            "0 = disable backoff entirely. Default: 2"
        )
        bo_layout.addRow("Max Retries:", self.spin_backoff_retries)

        layout.addWidget(backoff_group)

        # ── Display Settings ──
        display_group = QGroupBox("Result Display")
        disp_layout = QFormLayout(display_group)
        disp_layout.setSpacing(10)

        self.chk_show_confidence = QCheckBox("Show confidence scores on results")
        self.chk_show_confidence.setToolTip(
            "Display relevance/confidence percentage overlay on search result thumbnails.\n"
            "Useful for understanding search quality."
        )
        disp_layout.addRow("", self.chk_show_confidence)

        # Min display confidence slider
        conf_row = QWidget()
        conf_row_layout = QHBoxLayout(conf_row)
        conf_row_layout.setContentsMargins(0, 0, 0, 0)

        self.slider_min_confidence = QSlider(Qt.Horizontal)
        self.slider_min_confidence.setRange(0, 50)  # 0.00 to 0.50
        self.slider_min_confidence.setTickPosition(QSlider.TicksBelow)
        self.slider_min_confidence.setTickInterval(5)
        self.lbl_min_confidence_val = QLabel("0.15")
        self.lbl_min_confidence_val.setMinimumWidth(40)
        self.slider_min_confidence.valueChanged.connect(
            lambda v: self.lbl_min_confidence_val.setText(f"{v / 100:.2f}")
        )
        conf_row_layout.addWidget(self.slider_min_confidence, 1)
        conf_row_layout.addWidget(self.lbl_min_confidence_val)

        disp_layout.addRow("Min Display Score:", conf_row)

        conf_hint = QLabel(
            "Filter out results below this confidence from display.\n"
            "0.00 = show all matches. Default: 0.15"
        )
        conf_hint.setWordWrap(True)
        conf_hint.setStyleSheet("color: #888; font-size: 9pt; padding-left: 4px;")
        disp_layout.addRow("", conf_hint)

        layout.addWidget(display_group)

        # ── OCR Text Recognition ──
        ocr_group = QGroupBox("OCR Text Recognition (Text in Photos)")
        ocr_layout = QFormLayout(ocr_group)
        ocr_layout.setSpacing(10)

        self.chk_ocr_enabled = QCheckBox("Enable OCR text extraction during scan")
        self.chk_ocr_enabled.setToolTip(
            "Extract text from photos using OCR after scanning.\n"
            "Enables searching for text visible in photos (signs, documents, etc.).\n"
            "Requires: pip install easyocr\n"
            "Note: First run downloads ~200MB model. Processing is CPU/GPU intensive."
        )
        self.chk_ocr_enabled.setChecked(self.settings.get("ocr_enabled", False))
        ocr_layout.addRow("", self.chk_ocr_enabled)

        self.combo_ocr_languages = QComboBox()
        self.combo_ocr_languages.addItem("English", "en")
        self.combo_ocr_languages.addItem("English + Arabic", "en,ar")
        self.combo_ocr_languages.addItem("English + German", "en,de")
        self.combo_ocr_languages.addItem("English + French", "en,fr")
        self.combo_ocr_languages.addItem("English + Spanish", "en,es")
        self.combo_ocr_languages.addItem("English + Chinese", "en,ch_sim")
        self.combo_ocr_languages.addItem("English + Japanese", "en,ja")
        self.combo_ocr_languages.addItem("English + Korean", "en,ko")
        self.combo_ocr_languages.setToolTip(
            "Language(s) for OCR text recognition.\n"
            "Adding more languages may reduce accuracy and speed."
        )
        # Set current selection from settings
        saved_langs = self.settings.get("ocr_languages", "en")
        for i in range(self.combo_ocr_languages.count()):
            if self.combo_ocr_languages.itemData(i) == saved_langs:
                self.combo_ocr_languages.setCurrentIndex(i)
                break
        ocr_layout.addRow("Languages:", self.combo_ocr_languages)

        ocr_hint = QLabel(
            "Once enabled, text is extracted after each scan. Use has:text in search\n"
            "to find photos with recognized text, or just type the text to search for it."
        )
        ocr_hint.setWordWrap(True)
        ocr_hint.setStyleSheet("color: #888; font-size: 9pt; padding-left: 4px;")
        ocr_layout.addRow("", ocr_hint)

        layout.addWidget(ocr_group)

        # ── Reset Button ──
        btn_reset = QPushButton("Reset Search Settings to Defaults")
        btn_reset.setMaximumWidth(280)
        btn_reset.setToolTip("Restore all search parameters to their default values")
        btn_reset.clicked.connect(self._reset_search_defaults)
        layout.addWidget(btn_reset)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _reset_search_defaults(self):
        """Reset all search and ranking settings to defaults."""
        d = SearchDefaults()
        r = RankingDefaults()
        self.slider_clip_threshold.setValue(int(d.CLIP_THRESHOLD * 100))
        self.lbl_clip_threshold_val.setText(f"{d.CLIP_THRESHOLD:.2f}")
        self.spin_search_top_k.setValue(d.DEFAULT_TOP_K)
        self.spin_cache_ttl.setValue(d.CACHE_TTL)
        self.spin_debounce.setValue(d.SEARCH_DEBOUNCE_MS)
        self.chk_nlp_enabled.setChecked(d.NLP_ENABLED)
        self.slider_semantic_sim.setValue(int(d.SEMANTIC_MIN_SIMILARITY * 100))
        self.lbl_semantic_sim_val.setText(f"{d.SEMANTIC_MIN_SIMILARITY:.2f}")
        self.spin_semantic_top_k.setValue(d.SEMANTIC_TOP_K)
        self.chk_show_confidence.setChecked(d.SHOW_CONFIDENCE_SCORES)
        self.slider_min_confidence.setValue(int(d.MIN_DISPLAY_CONFIDENCE * 100))
        self.lbl_min_confidence_val.setText(f"{d.MIN_DISPLAY_CONFIDENCE:.2f}")
        self.combo_fusion_mode.setCurrentText(d.FUSION_MODE)
        self.slider_semantic_weight.setValue(int(d.SEMANTIC_WEIGHT * 100))
        self.lbl_semantic_weight_val.setText(f"{d.SEMANTIC_WEIGHT:.2f}")
        self.chk_threshold_backoff.setChecked(d.THRESHOLD_BACKOFF_ENABLED)

        # Ranking weights
        self.slider_rank_clip.setValue(int(r.W_CLIP * 100))
        self.lbl_rank_clip.setText(f"{r.W_CLIP:.2f}")
        self.slider_rank_recency.setValue(int(r.W_RECENCY * 100))
        self.lbl_rank_recency.setText(f"{r.W_RECENCY:.2f}")
        self.slider_rank_favorite.setValue(int(r.W_FAVORITE * 100))
        self.lbl_rank_favorite.setText(f"{r.W_FAVORITE:.2f}")
        self.slider_rank_location.setValue(int(r.W_LOCATION * 100))
        self.lbl_rank_location.setText(f"{r.W_LOCATION:.2f}")
        self.slider_rank_face.setValue(int(r.W_FACE_MATCH * 100))
        self.lbl_rank_face.setText(f"{r.W_FACE_MATCH:.2f}")
        self.slider_rank_structural.setValue(int(r.W_STRUCTURAL * 100))
        self.lbl_rank_structural.setText(f"{r.W_STRUCTURAL:.2f}")
        self.slider_max_recency_boost.setValue(int(r.MAX_RECENCY_BOOST * 100))
        self.lbl_max_recency_boost.setText(f"{r.MAX_RECENCY_BOOST:.2f}")
        self.slider_max_favorite_boost.setValue(int(r.MAX_FAVORITE_BOOST * 100))
        self.lbl_max_favorite_boost.setText(f"{r.MAX_FAVORITE_BOOST:.2f}")
        self.spin_recency_halflife.setValue(r.RECENCY_HALFLIFE_DAYS)

        # Metadata boosts
        self.slider_boost_gps.setValue(int(d.META_BOOST_GPS * 100))
        self.lbl_boost_gps.setText(f"{d.META_BOOST_GPS:.2f}")
        self.slider_boost_rating.setValue(int(d.META_BOOST_RATING * 100))
        self.lbl_boost_rating.setText(f"{d.META_BOOST_RATING:.2f}")
        self.slider_boost_date.setValue(int(d.META_BOOST_DATE * 100))
        self.lbl_boost_date.setText(f"{d.META_BOOST_DATE:.2f}")

        # Backoff parameters
        self.slider_backoff_step.setValue(int(d.THRESHOLD_BACKOFF_STEP * 100))
        self.lbl_backoff_step.setText(f"{d.THRESHOLD_BACKOFF_STEP:.2f}")
        self.spin_backoff_retries.setValue(d.THRESHOLD_BACKOFF_MAX_RETRIES)

    def _create_video_panel(self) -> QWidget:
        """Create Video Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.video.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # FFprobe path
        ffprobe_group = QGroupBox(tr("preferences.video.ffprobe_path"))
        ffprobe_layout = QVBoxLayout(ffprobe_group)

        hint_label = QLabel(tr("preferences.video.ffprobe_path_hint"))
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: gray; font-size: 9pt; padding-bottom: 5px;")
        ffprobe_layout.addWidget(hint_label)

        path_layout = QHBoxLayout()
        self.txt_ffprobe_path = QLineEdit()
        self.txt_ffprobe_path.setPlaceholderText(tr("preferences.video.ffprobe_path_placeholder"))
        path_layout.addWidget(self.txt_ffprobe_path, 1)

        btn_browse = QPushButton(tr("common.browse"))
        btn_browse.clicked.connect(self._browse_ffprobe)
        path_layout.addWidget(btn_browse)

        btn_test = QPushButton(tr("common.test"))
        btn_test.clicked.connect(self._test_ffprobe)
        path_layout.addWidget(btn_test)

        ffprobe_layout.addLayout(path_layout)

        # Help note
        note_label = QLabel(tr("preferences.video.ffmpeg_note"))
        note_label.setWordWrap(True)
        note_label.setStyleSheet("font-size: 10pt; color: #666; padding: 8px; background: #f0f0f0; border-radius: 4px;")
        ffprobe_layout.addWidget(note_label)

        layout.addWidget(ffprobe_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_advanced_panel(self) -> QWidget:
        """Create Advanced Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.developer.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Diagnostics
        diag_group = QGroupBox(tr("preferences.diagnostics.title"))
        diag_layout = QVBoxLayout(diag_group)

        self.chk_decoder_warnings = QCheckBox(tr("preferences.diagnostics.decoder_warnings"))
        self.chk_decoder_warnings.setToolTip(tr("preferences.diagnostics.decoder_warnings_hint"))
        diag_layout.addWidget(self.chk_decoder_warnings)

        layout.addWidget(diag_group)

        # Developer tools
        dev_group = QGroupBox(tr("preferences.developer.title"))
        dev_layout = QVBoxLayout(dev_group)

        self.chk_db_debug = QCheckBox(tr("preferences.developer.db_debug"))
        self.chk_db_debug.setToolTip(tr("preferences.developer.db_debug_hint"))
        dev_layout.addWidget(self.chk_db_debug)

        self.chk_sql_echo = QCheckBox(tr("preferences.developer.sql_queries"))
        self.chk_sql_echo.setToolTip(tr("preferences.developer.sql_queries_hint"))
        dev_layout.addWidget(self.chk_sql_echo)

        layout.addWidget(dev_group)

        # Metadata extraction
        meta_group = QGroupBox(tr("preferences.metadata.title"))
        meta_layout = QFormLayout(meta_group)
        meta_layout.setSpacing(10)

        self.spin_workers = QComboBox()
        self.spin_workers.setEditable(True)
        self.spin_workers.setToolTip(tr("preferences.metadata.workers_hint"))
        for workers in ["2", "4", "6", "8", "12"]:
            self.spin_workers.addItem(workers)
        meta_layout.addRow(tr("preferences.metadata.workers") + ":", self.spin_workers)

        self.txt_meta_timeout = QComboBox()
        self.txt_meta_timeout.setEditable(True)
        self.txt_meta_timeout.setToolTip(tr("preferences.metadata.timeout_hint"))
        for timeout in ["4.0", "6.0", "8.0", "12.0"]:
            self.txt_meta_timeout.addItem(timeout)
        meta_layout.addRow(tr("preferences.metadata.timeout") + ":", self.txt_meta_timeout)

        self.txt_meta_batch = QComboBox()
        self.txt_meta_batch.setEditable(True)
        self.txt_meta_batch.setToolTip(tr("preferences.metadata.batch_size_hint"))
        for batch in ["50", "100", "200", "500"]:
            self.txt_meta_batch.addItem(batch)
        meta_layout.addRow(tr("preferences.metadata.batch_size") + ":", self.txt_meta_batch)

        self.chk_meta_auto = QCheckBox(tr("preferences.metadata.auto_run"))
        self.chk_meta_auto.setToolTip(tr("preferences.metadata.auto_run_hint"))
        meta_layout.addRow("", self.chk_meta_auto)

        layout.addWidget(meta_group)

        # Duplicate Management
        dup_group = QGroupBox(tr("preferences.duplicate.title"))
        dup_layout = QVBoxLayout(dup_group)
        dup_layout.setSpacing(10)

        # Description
        dup_desc = QLabel(tr("preferences.duplicate.description"))
        dup_desc.setWordWrap(True)
        dup_desc.setStyleSheet("color: #666; font-size: 11px;")
        dup_layout.addWidget(dup_desc)

        # Backfill status
        self.lbl_backfill_status = QLabel(tr("preferences.duplicate.status_checking"))
        self.lbl_backfill_status.setStyleSheet("color: #666; font-size: 11px;")
        dup_layout.addWidget(self.lbl_backfill_status)

        # Backfill button
        self.btn_run_backfill = QPushButton(tr("preferences.duplicate.run_backfill"))
        self.btn_run_backfill.setToolTip(tr("preferences.duplicate.run_backfill_hint"))
        self.btn_run_backfill.clicked.connect(self._on_run_hash_backfill)
        self.btn_run_backfill.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #666;
            }
        """)
        dup_layout.addWidget(self.btn_run_backfill)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet("background-color: #ddd;")
        dup_layout.addWidget(separator)

        # Similar Shot Stacks section
        similar_title = QLabel("<b>Similar Shot Detection</b>")
        similar_title.setStyleSheet("font-size: 12px; margin-top: 10px;")
        dup_layout.addWidget(similar_title)

        similar_desc = QLabel(
            "Groups burst photos, photo series, and similar poses using AI.\n"
            "Requires semantic embeddings to be generated first."
        )
        similar_desc.setWordWrap(True)
        similar_desc.setStyleSheet("color: #666; font-size: 11px;")
        dup_layout.addWidget(similar_desc)

        # Similar shot status
        self.lbl_similar_shot_status = QLabel("Checking status...")
        self.lbl_similar_shot_status.setStyleSheet("color: #666; font-size: 11px;")
        dup_layout.addWidget(self.lbl_similar_shot_status)

        # Similar shot button
        self.btn_run_similar_shots = QPushButton("🔍 Generate Similar Shot Stacks")
        self.btn_run_similar_shots.setToolTip(
            "Generate stacks for burst photos and similar shots using time proximity + visual similarity"
        )
        self.btn_run_similar_shots.clicked.connect(self._on_run_similar_shot_stacks)
        self.btn_run_similar_shots.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #666;
            }
        """)
        dup_layout.addWidget(self.btn_run_similar_shots)

        layout.addWidget(dup_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _apply_styling(self):
        """Apply dark/light mode adaptive styling."""
        is_dark = self.settings.get("dark_mode", False)

        if is_dark:
            sidebar_bg = "#2b2b2b"
            sidebar_item_bg = "#3c3c3c"
            sidebar_selected = "#4a90e2"
            content_bg = "#1e1e1e"
            text_color = "#e0e0e0"
        else:
            sidebar_bg = "#f5f5f5"
            sidebar_item_bg = "#ffffff"
            sidebar_selected = "#0078d4"
            content_bg = "#ffffff"
            text_color = "#000000"

        self.sidebar.setStyleSheet(f"""
            QListWidget {{
                background: {sidebar_bg};
                border: none;
                border-right: 1px solid #ccc;
                outline: none;
            }}
            QListWidget::item {{
                background: {sidebar_item_bg};
                color: {text_color};
                padding: 8px;
                margin: 2px 4px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background: {sidebar_selected};
                color: white;
            }}
            QListWidget::item:hover:!selected {{
                background: {sidebar_item_bg if is_dark else '#e8e8e8'};
            }}
        """)

        self.setStyleSheet(f"""
            QDialog {{
                background: {content_bg};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
        """)

    def _on_sidebar_changed(self, index: int):
        """Handle sidebar navigation changes."""
        self.content_stack.setCurrentIndex(index)

    def _load_settings(self):
        """Load current settings into UI controls."""
        # General
        self.chk_skip.setChecked(self.settings.get("skip_unchanged_photos", False))
        self.chk_exif.setChecked(self.settings.get("use_exif_for_date", True))

        # Appearance
        self.chk_dark.setChecked(self.settings.get("dark_mode", False))
        self.chk_cache.setChecked(self.settings.get("thumbnail_cache_enabled", True))
        self.cmb_cache_size.setCurrentText(str(self.settings.get("cache_size_mb", 500)))
        self.chk_cache_cleanup.setChecked(self.settings.get("cache_auto_cleanup", True))

        # Scanning
        ignore_folders = self.settings.get("ignore_folders", [])
        self.txt_ignore_folders.setPlainText("\n".join(ignore_folders))
        self.chk_device_auto_refresh.setChecked(self.settings.get("device_auto_refresh", True))

        # Face Detection
        model = self.face_config.get("insightface_model", "buffalo_l")
        index = self.cmb_insightface_model.findData(model)
        if index >= 0:
            self.cmb_insightface_model.setCurrentIndex(index)

        self.spin_min_face_size.setValue(self.face_config.get("min_face_size", 20))
        self.spin_confidence.setValue(int(self.face_config.get("confidence_threshold", 0.6) * 100))
        self.spin_min_quality.setValue(int(self.face_config.get("min_quality_score", 0.0)))
        self.spin_cluster_eps.setValue(int(self.face_config.get("clustering_eps", 0.35) * 100))
        self.spin_min_samples.setValue(self.face_config.get("clustering_min_samples", 2))
        self.chk_auto_cluster.setChecked(self.face_config.get("auto_cluster_after_scan", True))
        self.spin_max_workers.setValue(self.face_config.get("max_workers", 4))
        self.spin_batch_size.setValue(self.face_config.get("batch_size", 50))
        self.chk_gpu_batch.setChecked(self.face_config.get("enable_gpu_batch", True))
        self.spin_gpu_batch_size.setValue(self.face_config.get("gpu_batch_size", 4))
        self.spin_gpu_batch_min.setValue(self.face_config.get("gpu_batch_min_photos", 10))
        po = self.face_config.get("project_overrides", {})
        ov = po.get(str(self.current_project_id), {})
        self.chk_project_overrides.setChecked(bool(ov))
        self.spin_proj_min_face.setValue(int(ov.get("min_face_size", self.face_config.get("min_face_size", 20))))
        self.spin_proj_confidence.setValue(int((ov.get("confidence_threshold", self.face_config.get("confidence_threshold", 0.6))) * 100))
        self.spin_proj_eps.setValue(int(ov.get("clustering_eps", self.face_config.get("clustering_eps", 0.35)) * 100))
        self.spin_proj_min_samples.setValue(int(ov.get("clustering_min_samples", self.face_config.get("clustering_min_samples", 2))))
        self.chk_show_low_conf.setChecked(self.face_config.get("show_low_confidence", False))

        # Screenshot policy
        policy = self.settings.get("screenshot_face_policy", "detect_only")
        idx = self.cmb_screenshot_face_policy.findData(policy)
        if idx >= 0:
            self.cmb_screenshot_face_policy.setCurrentIndex(idx)

        self.chk_include_all_screenshot_faces.setChecked(
            self.settings.get("include_all_screenshot_faces", False)
        )

        # InsightFace model path
        self.txt_model_path.setText(self.settings.get("insightface_model_path", ""))

        # Groups settings
        group_scope = self.settings.get("groups_default_scope", "same_photo")
        scope_index = self.cmb_group_match_scope.findData(group_scope)
        if scope_index >= 0:
            self.cmb_group_match_scope.setCurrentIndex(scope_index)
        self.spin_event_window_hours.setValue(self.settings.get("groups_event_window_hours", 24))
        self.chk_auto_index_groups.setChecked(self.settings.get("groups_auto_index", True))
        self.chk_incremental_index.setChecked(self.settings.get("groups_incremental_index", True))
        self.spin_group_avatar_count.setValue(self.settings.get("groups_max_avatars", 4))
        self.chk_show_group_photo_count.setChecked(self.settings.get("groups_show_photo_count", True))
        self.chk_cache_group_matches.setChecked(self.settings.get("groups_cache_matches", True))

        # CLIP / Visual Embeddings
        clip_variant = self.settings.get("clip_model_variant", "openai/clip-vit-base-patch32")
        clip_index = self.cmb_clip_variant.findData(clip_variant)
        if clip_index >= 0:
            self.cmb_clip_variant.setCurrentIndex(clip_index)

        clip_device = self.settings.get("clip_device", "auto")
        device_index = self.cmb_clip_device.findData(clip_device)
        if device_index >= 0:
            self.cmb_clip_device.setCurrentIndex(device_index)

        self.chk_auto_extract.setChecked(self.settings.get("clip_auto_extract", False))
        self.spin_extraction_batch.setValue(self.settings.get("clip_batch_size", 50))
        self.txt_clip_model_path.setText(self.settings.get("clip_model_path", ""))

        # Search & Discovery
        clip_thresh = SearchConfig.get_clip_threshold()
        self.slider_clip_threshold.setValue(int(clip_thresh * 100))
        self.lbl_clip_threshold_val.setText(f"{clip_thresh:.2f}")
        self.spin_search_top_k.setValue(SearchConfig.get_default_top_k())
        self.spin_cache_ttl.setValue(SearchConfig.get_cache_ttl())
        self.spin_debounce.setValue(SearchConfig.get_search_debounce_ms())
        self.chk_nlp_enabled.setChecked(SearchConfig.get_nlp_enabled())
        sem_sim = SearchConfig.get_semantic_min_similarity()
        self.slider_semantic_sim.setValue(int(sem_sim * 100))
        self.lbl_semantic_sim_val.setText(f"{sem_sim:.2f}")
        self.spin_semantic_top_k.setValue(SearchConfig.get_semantic_top_k())
        self.chk_show_confidence.setChecked(SearchConfig.get_show_confidence_scores())
        min_conf = SearchConfig.get_min_display_confidence()
        self.slider_min_confidence.setValue(int(min_conf * 100))
        self.lbl_min_confidence_val.setText(f"{min_conf:.2f}")
        self.combo_fusion_mode.setCurrentText(SearchConfig.get_fusion_mode())
        sem_w = SearchConfig.get_semantic_weight()
        self.slider_semantic_weight.setValue(int(sem_w * 100))
        self.lbl_semantic_weight_val.setText(f"{sem_w:.2f}")
        self.chk_threshold_backoff.setChecked(SearchConfig.get_threshold_backoff_enabled())

        # Ranking Weights
        self.slider_rank_clip.setValue(int(RankingConfig.get_w_clip() * 100))
        self.lbl_rank_clip.setText(f"{RankingConfig.get_w_clip():.2f}")
        self.slider_rank_recency.setValue(int(RankingConfig.get_w_recency() * 100))
        self.lbl_rank_recency.setText(f"{RankingConfig.get_w_recency():.2f}")
        self.slider_rank_favorite.setValue(int(RankingConfig.get_w_favorite() * 100))
        self.lbl_rank_favorite.setText(f"{RankingConfig.get_w_favorite():.2f}")
        self.slider_rank_location.setValue(int(RankingConfig.get_w_location() * 100))
        self.lbl_rank_location.setText(f"{RankingConfig.get_w_location():.2f}")
        self.slider_rank_face.setValue(int(RankingConfig.get_w_face_match() * 100))
        self.lbl_rank_face.setText(f"{RankingConfig.get_w_face_match():.2f}")
        self.slider_rank_structural.setValue(int(RankingConfig.get_w_structural() * 100))
        self.lbl_rank_structural.setText(f"{RankingConfig.get_w_structural():.2f}")
        self.slider_max_recency_boost.setValue(int(RankingConfig.get_max_recency_boost() * 100))
        self.lbl_max_recency_boost.setText(f"{RankingConfig.get_max_recency_boost():.2f}")
        self.slider_max_favorite_boost.setValue(int(RankingConfig.get_max_favorite_boost() * 100))
        self.lbl_max_favorite_boost.setText(f"{RankingConfig.get_max_favorite_boost():.2f}")
        self.spin_recency_halflife.setValue(RankingConfig.get_recency_halflife_days())

        # Metadata Boosts
        self.slider_boost_gps.setValue(int(SearchConfig.get_meta_boost_gps() * 100))
        self.lbl_boost_gps.setText(f"{SearchConfig.get_meta_boost_gps():.2f}")
        self.slider_boost_rating.setValue(int(SearchConfig.get_meta_boost_rating() * 100))
        self.lbl_boost_rating.setText(f"{SearchConfig.get_meta_boost_rating():.2f}")
        self.slider_boost_date.setValue(int(SearchConfig.get_meta_boost_date() * 100))
        self.lbl_boost_date.setText(f"{SearchConfig.get_meta_boost_date():.2f}")

        # Backoff Parameters
        bo_step = SearchConfig.get_threshold_backoff_step()
        self.slider_backoff_step.setValue(int(bo_step * 100))
        self.lbl_backoff_step.setText(f"{bo_step:.2f}")
        self.spin_backoff_retries.setValue(SearchConfig.get_threshold_backoff_max_retries())

        # Badge overlay settings
        self.chk_badge_overlays.setChecked(self.settings.get("badge_overlays_enabled", True))
        self.spin_badge_size.setValue(int(self.settings.get("badge_size_px", 22)))
        shape = str(self.settings.get("badge_shape", "circle")).lower()
        idx = self.cmb_badge_shape.findText(shape)
        if idx >= 0:
            self.cmb_badge_shape.setCurrentIndex(idx)
        self.spin_badge_max.setValue(int(self.settings.get("badge_max_count", 4)))
        self.chk_badge_shadow.setChecked(self.settings.get("badge_shadow", True))
        
        # Update preview with initial values
        self._update_badge_preview()
        
        # GPS & Location
        self.spin_cluster_radius.setValue(int(self.settings.get("gps_clustering_radius_km", 5)))
        self.chk_reverse_geocoding.setChecked(self.settings.get("gps_reverse_geocoding_enabled", True))
        self.spin_geocoding_timeout.setValue(int(self.settings.get("gps_geocoding_timeout_sec", 2)))
        self.chk_cache_location_names.setChecked(self.settings.get("gps_cache_location_names", True))

        # Video - FFprobe path
        self.txt_ffprobe_path.setText(self.settings.get("ffprobe_path", ""))

        # Advanced
        self.chk_decoder_warnings.setChecked(self.settings.get("show_decoder_warnings", False))
        self.chk_db_debug.setChecked(self.settings.get("db_debug_logging", False))
        self.chk_sql_echo.setChecked(self.settings.get("show_sql_queries", False))
        self.spin_workers.setCurrentText(str(self.settings.get("meta_workers", 4)))
        self.txt_meta_timeout.setCurrentText(str(self.settings.get("meta_timeout_secs", 8.0)))
        self.txt_meta_batch.setCurrentText(str(self.settings.get("meta_batch", 200)))
        self.chk_meta_auto.setChecked(self.settings.get("auto_run_backfill_after_scan", False))

    def _capture_settings(self) -> dict:
        """Capture current settings for change detection."""
        return {
            "skip_unchanged_photos": self.settings.get("skip_unchanged_photos", False),
            "use_exif_for_date": self.settings.get("use_exif_for_date", True),
            "dark_mode": self.settings.get("dark_mode", False),
            "language": self.settings.get("language", "en"),
            "thumbnail_cache_enabled": self.settings.get("thumbnail_cache_enabled", True),
            "cache_size_mb": self.settings.get("cache_size_mb", 500),
            "cache_auto_cleanup": self.settings.get("cache_auto_cleanup", True),
            "ignore_folders": self.settings.get("ignore_folders", []),
            "device_auto_refresh": self.settings.get("device_auto_refresh", True),
            "insightface_model": self.face_config.get("insightface_model", "buffalo_l"),
            "min_face_size": self.face_config.get("min_face_size", 20),
            "confidence_threshold": self.face_config.get("confidence_threshold", 0.6),
            "clustering_eps": self.face_config.get("clustering_eps", 0.35),
            "clustering_min_samples": self.face_config.get("clustering_min_samples", 2),
            "auto_cluster_after_scan": self.face_config.get("auto_cluster_after_scan", True),
            "face_max_workers": self.face_config.get("max_workers", 4),
            "face_batch_size": self.face_config.get("batch_size", 50),
            "min_quality_score": self.face_config.get("min_quality_score", 0.0),
            "enable_gpu_batch": self.face_config.get("enable_gpu_batch", True),
            "gpu_batch_size": self.face_config.get("gpu_batch_size", 4),
            "gpu_batch_min_photos": self.face_config.get("gpu_batch_min_photos", 10),
            "insightface_model_path": self.settings.get("insightface_model_path", ""),
            "ffprobe_path": self.settings.get("ffprobe_path", ""),
            "show_decoder_warnings": self.settings.get("show_decoder_warnings", False),
            "db_debug_logging": self.settings.get("db_debug_logging", False),
            "show_sql_queries": self.settings.get("show_sql_queries", False),
            "meta_workers": self.settings.get("meta_workers", 4),
            "meta_timeout_secs": self.settings.get("meta_timeout_secs", 8.0),
            "meta_batch": self.settings.get("meta_batch", 200),
            "auto_run_backfill_after_scan": self.settings.get("auto_run_backfill_after_scan", False),
            "search_clip_threshold": SearchConfig.get_clip_threshold(),
            "search_default_top_k": SearchConfig.get_default_top_k(),
            "search_cache_ttl": SearchConfig.get_cache_ttl(),
            "search_debounce_ms": SearchConfig.get_search_debounce_ms(),
            "search_nlp_enabled": SearchConfig.get_nlp_enabled(),
            "search_semantic_min_similarity": SearchConfig.get_semantic_min_similarity(),
            "search_semantic_top_k": SearchConfig.get_semantic_top_k(),
            "search_show_confidence": SearchConfig.get_show_confidence_scores(),
            "search_min_display_confidence": SearchConfig.get_min_display_confidence(),
            "search_fusion_mode": SearchConfig.get_fusion_mode(),
            "search_semantic_weight": SearchConfig.get_semantic_weight(),
            "search_threshold_backoff": SearchConfig.get_threshold_backoff_enabled(),
            # Ranking weights
            "ranking_w_clip": RankingConfig.get_w_clip(),
            "ranking_w_recency": RankingConfig.get_w_recency(),
            "ranking_w_favorite": RankingConfig.get_w_favorite(),
            "ranking_w_location": RankingConfig.get_w_location(),
            "ranking_w_face_match": RankingConfig.get_w_face_match(),
            "ranking_w_structural": RankingConfig.get_w_structural(),
            "ranking_max_recency_boost": RankingConfig.get_max_recency_boost(),
            "ranking_max_favorite_boost": RankingConfig.get_max_favorite_boost(),
            "ranking_recency_halflife": RankingConfig.get_recency_halflife_days(),
            # Metadata boosts
            "search_meta_boost_gps": SearchConfig.get_meta_boost_gps(),
            "search_meta_boost_rating": SearchConfig.get_meta_boost_rating(),
            "search_meta_boost_date": SearchConfig.get_meta_boost_date(),
            # Backoff params
            "search_backoff_step": SearchConfig.get_threshold_backoff_step(),
            "search_backoff_retries": SearchConfig.get_threshold_backoff_max_retries(),
        }

    def _on_run_hash_backfill(self):
        """Handle hash backfill button click."""
        try:
            # Import required modules
            from services.job_service import get_job_service
            from services.asset_service import AssetService
            from repository.photo_repository import PhotoRepository
            from repository.asset_repository import AssetRepository
            from repository.base_repository import DatabaseConnection
            from workers.hash_backfill_worker import create_hash_backfill_worker
            from ui.hash_backfill_progress_dialog import HashBackfillProgressDialog
            from PySide6.QtCore import QThreadPool

            # Get current project_id (try to get from main window)
            project_id = 1  # Default to project 1
            if hasattr(self.parent(), 'sidebar') and hasattr(self.parent().sidebar, 'project_id'):
                project_id = self.parent().sidebar.project_id
            elif hasattr(self.parent(), 'grid') and hasattr(self.parent().grid, 'project_id'):
                project_id = self.parent().grid.project_id

            # Initialize services to check status
            db_conn = DatabaseConnection()
            asset_repo = AssetRepository(db_conn)

            # Check how many photos need backfill
            total_without_instance = asset_repo.count_photos_without_instance(project_id)

            if total_without_instance == 0:
                QMessageBox.information(
                    self,
                    "Duplicate Detection Ready",
                    "All photos are already hashed and linked to assets.\n\n"
                    "Duplicate detection is ready to use!",
                    QMessageBox.Ok
                )
                return

            # Confirm with user
            reply = QMessageBox.question(
                self,
                "Prepare Duplicate Detection",
                f"Found {total_without_instance} photos that need processing.\n\n"
                f"This will compute SHA256 hashes and link photos to assets.\n"
                f"Estimated time: ~{int(total_without_instance / 1000) + 1} minutes\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Create progress dialog
            progress_dialog = HashBackfillProgressDialog(total_without_instance, self)

            # Enqueue job
            job_service = get_job_service()
            job_id = job_service.enqueue_job(
                kind='hash_backfill',
                payload={
                    'project_id': project_id
                },
                backend='local'
            )

            # Create worker
            worker = create_hash_backfill_worker(
                job_id=job_id,
                project_id=project_id,
                batch_size=500
            )

            # Connect worker signals to progress dialog (guarded against teardown)
            gen = int(getattr(self.parent() or self.window(), "_ui_generation", 0))
            connect_guarded(worker.signals.progress, self.parent() or self.window(), progress_dialog.update_progress, generation=gen, extra_valid=[progress_dialog])
            connect_guarded(worker.signals.finished, self.parent() or self.window(), progress_dialog.on_finished, generation=gen, extra_valid=[progress_dialog])
            connect_guarded(worker.signals.error, self.parent() or self.window(), progress_dialog.on_error, generation=gen, extra_valid=[progress_dialog])

            # Connect dialog cancel to worker (if needed - not implemented in worker yet)
            # progress_dialog.cancelled.connect(worker.cancel)

            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._hash_backfill_worker = worker

            # Start worker
            QThreadPool.globalInstance().start(worker)

            # Show progress dialog
            result = progress_dialog.exec()

            # Update status label
            self._update_backfill_status()

            if result == QDialog.Accepted:
                QMessageBox.information(
                    self,
                    "Backfill Complete",
                    f"Successfully processed {total_without_instance} photos!\n\n"
                    f"Duplicate detection is now ready to use.",
                    QMessageBox.Ok
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Backfill Error",
                f"Failed to start hash backfill:\n{str(e)}"
            )
            print(f"✗ Hash backfill error: {e}")
            import traceback
            traceback.print_exc()

    def _on_run_similar_shot_stacks(self):
        """Handle similar shot stack generation button click."""
        try:
            # Import required modules
            from services.job_service import get_job_service
            from services.semantic_embedding_service import get_semantic_embedding_service
            from repository.stack_repository import StackRepository
            from repository.base_repository import DatabaseConnection
            from workers.similar_shot_stack_worker import create_similar_shot_stack_worker
            from ui.similar_shot_progress_dialog import SimilarShotProgressDialog
            from PySide6.QtCore import QThreadPool

            # Get current project_id
            project_id = 1
            if hasattr(self.parent(), 'sidebar') and hasattr(self.parent().sidebar, 'project_id'):
                project_id = self.parent().sidebar.project_id
            elif hasattr(self.parent(), 'grid') and hasattr(self.parent().grid, 'project_id'):
                project_id = self.parent().grid.project_id

            # Check prerequisites - use getter to avoid duplicate instances
            db_conn = DatabaseConnection()
            embedding_service = get_semantic_embedding_service()
            stack_repo = StackRepository(db_conn)

            # Check if embeddings exist
            embedding_count = embedding_service.get_embedding_count()
            if embedding_count == 0:
                QMessageBox.warning(
                    self,
                    "Embeddings Required",
                    "Similar shot detection requires semantic embeddings.\n\n"
                    "Please generate embeddings first:\n"
                    "1. Go to Search → Semantic Search\n"
                    "2. Click 'Generate Embeddings'\n"
                    "3. Wait for completion\n"
                    "4. Return here to generate similar shot stacks",
                    QMessageBox.Ok
                )
                return

            # Check for existing stacks
            existing_stacks = stack_repo.count_stacks(project_id, stack_type="similar")

            # Confirm with user
            if existing_stacks > 0:
                reply = QMessageBox.question(
                    self,
                    "Regenerate Similar Shot Stacks",
                    f"Found {existing_stacks} existing similar shot stacks.\n\n"
                    f"This will clear and regenerate all similar shot stacks.\n"
                    f"Photos with embeddings: {embedding_count}\n"
                    f"Estimated time: ~{int(embedding_count / 500) + 1} minutes\n\n"
                    f"Continue?",
                    QMessageBox.Yes | QMessageBox.No
                )
            else:
                reply = QMessageBox.question(
                    self,
                    "Generate Similar Shot Stacks",
                    f"This will analyze photos and group similar shots.\n\n"
                    f"Photos with embeddings: {embedding_count}\n"
                    f"Time window: ±10 seconds\n"
                    f"Similarity threshold: 92%\n"
                    f"Minimum stack size: 3 photos\n\n"
                    f"Estimated time: ~{int(embedding_count / 500) + 1} minutes\n\n"
                    f"Continue?",
                    QMessageBox.Yes | QMessageBox.No
                )

            if reply != QMessageBox.Yes:
                return

            # Create progress dialog
            progress_dialog = SimilarShotProgressDialog(embedding_count, self)

            # Enqueue job
            job_service = get_job_service()
            job_id = job_service.enqueue_job(
                kind='similar_shot_stacks',
                payload={
                    'project_id': project_id
                },
                backend='cpu'
            )

            # Create worker with default parameters
            worker = create_similar_shot_stack_worker(
                job_id=job_id,
                project_id=project_id,
                time_window_seconds=10,      # ±10 seconds
                similarity_threshold=0.92,    # 92% similarity
                min_stack_size=3,             # At least 3 photos
                rule_version="1"
            )

            # Connect worker signals to progress dialog (guarded against teardown)
            gen = int(getattr(self.parent() or self.window(), "_ui_generation", 0))
            connect_guarded(worker.signals.progress, self.parent() or self.window(), progress_dialog.update_progress, generation=gen, extra_valid=[progress_dialog])
            connect_guarded(worker.signals.finished, self.parent() or self.window(), progress_dialog.on_finished, generation=gen, extra_valid=[progress_dialog])
            connect_guarded(worker.signals.error, self.parent() or self.window(), progress_dialog.on_error, generation=gen, extra_valid=[progress_dialog])

            # Store reference to prevent premature GC (QRunnable safety)
            worker.setAutoDelete(False)
            self._similar_shot_worker = worker

            # Start worker
            QThreadPool.globalInstance().start(worker)

            # Show progress dialog
            result = progress_dialog.exec()

            # Update status label
            self._update_similar_shot_status()

            if result == QDialog.Accepted:
                stats = progress_dialog.get_stats()
                QMessageBox.information(
                    self,
                    "Similar Shot Stacks Generated",
                    f"Successfully processed {stats['photos_considered']} photos!\n\n"
                    f"• {stats['stacks_created']} stacks created\n"
                    f"• {stats['memberships_created']} photo memberships\n"
                    f"• {stats['errors']} errors\n\n"
                    f"View stacks in the Duplicates dialog.",
                    QMessageBox.Ok
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Similar Shot Error",
                f"Failed to start similar shot generation:\n{str(e)}"
            )

    def _update_similar_shot_status(self):
        """Update similar shot status label (DB-only, no ML model loading).

        IMPORTANT: This must NEVER import/instantiate the semantic embedding
        service — that triggers CLIP model loading which freezes the UI thread
        for 5-15 seconds on dialog open.  Use a direct DB count instead.
        """
        if not hasattr(self, "lbl_similar_shot_status") or not hasattr(self, "btn_run_similar_shots"):
            return
        try:
            from repository.stack_repository import StackRepository
            from repository.base_repository import DatabaseConnection

            # Get current project_id
            project_id = 1
            if hasattr(self.parent(), 'sidebar') and hasattr(self.parent().sidebar, 'project_id'):
                project_id = self.parent().sidebar.project_id
            elif hasattr(self.parent(), 'grid') and hasattr(self.parent().grid, 'project_id'):
                project_id = self.parent().grid.project_id

            # DB-only embedding count — no CLIP model load
            db_conn = DatabaseConnection()
            with db_conn.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM semantic_embeddings"
                )
                embedding_count = cursor.fetchone()["cnt"]

            stack_repo = StackRepository(db_conn)
            similar_stacks = stack_repo.count_stacks(project_id, stack_type="similar")

            if embedding_count == 0:
                self.lbl_similar_shot_status.setText("⚠ No embeddings - generate embeddings first")
                self.lbl_similar_shot_status.setStyleSheet("color: #ff9800; font-size: 11px;")
                self.btn_run_similar_shots.setEnabled(False)
            elif similar_stacks == 0:
                self.lbl_similar_shot_status.setText(f"Ready - {embedding_count} photos with embeddings")
                self.lbl_similar_shot_status.setStyleSheet("color: #666; font-size: 11px;")
                self.btn_run_similar_shots.setEnabled(True)
            else:
                self.lbl_similar_shot_status.setText(
                    f"✓ {similar_stacks} similar shot stacks generated ({embedding_count} photos analyzed)"
                )
                self.lbl_similar_shot_status.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")
                self.btn_run_similar_shots.setEnabled(True)

        except Exception as e:
            self.lbl_similar_shot_status.setText(f"Status unavailable: {str(e)}")
            self.lbl_similar_shot_status.setStyleSheet("color: #f44336; font-size: 11px;")
            print(f"Error checking similar shot status: {e}")

    def _update_backfill_status(self):
        """Update backfill status label."""
        try:
            from repository.asset_repository import AssetRepository
            from repository.photo_repository import PhotoRepository
            from repository.base_repository import DatabaseConnection

            # Get current project_id
            project_id = 1
            if hasattr(self.parent(), 'sidebar') and hasattr(self.parent().sidebar, 'project_id'):
                project_id = self.parent().sidebar.project_id
            elif hasattr(self.parent(), 'grid') and hasattr(self.parent().grid, 'project_id'):
                project_id = self.parent().grid.project_id

            # Check status
            db_conn = DatabaseConnection()
            photo_repo = PhotoRepository(db_conn)
            asset_repo = AssetRepository(db_conn)

            total_photos = photo_repo.count(where_clause="project_id = ?", params=(project_id,))
            photos_without_instance = asset_repo.count_photos_without_instance(project_id)

            if total_photos == 0:
                self.lbl_backfill_status.setText("No photos in project")
                self.lbl_backfill_status.setStyleSheet("color: #666; font-size: 11px;")
                self.btn_run_backfill.setEnabled(False)
            elif photos_without_instance == 0:
                self.lbl_backfill_status.setText("✓ All photos ready for duplicate detection")
                self.lbl_backfill_status.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")
                self.btn_run_backfill.setEnabled(False)
            else:
                progress_pct = ((total_photos - photos_without_instance) / total_photos * 100) if total_photos > 0 else 0
                self.lbl_backfill_status.setText(
                    f"{photos_without_instance} photos need processing ({progress_pct:.1f}% complete)"
                )
                self.lbl_backfill_status.setStyleSheet("color: #ff9800; font-size: 11px;")
                self.btn_run_backfill.setEnabled(True)

        except Exception as e:
            self.lbl_backfill_status.setText(f"Status unavailable: {str(e)}")
            self.lbl_backfill_status.setStyleSheet("color: #f44336; font-size: 11px;")
            print(f"Error checking backfill status: {e}")

    def _on_cancel(self):
        """Handle cancel button - check for unsaved changes."""
        if self._has_changes():
            reply = QMessageBox.question(
                self,
                tr("preferences.unsaved_changes"),
                tr("preferences.unsaved_changes_message"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )

            if reply == QMessageBox.Yes:
                self._on_save()
            elif reply == QMessageBox.No:
                self.reject()
            # Cancel = do nothing
        else:
            self.reject()

    def _on_save(self):
        """Save all settings and close dialog."""
        # General
        self.settings.set("skip_unchanged_photos", self.chk_skip.isChecked())
        self.settings.set("use_exif_for_date", self.chk_exif.isChecked())

        # Appearance
        self.settings.set("dark_mode", self.chk_dark.isChecked())
        self.settings.set("thumbnail_cache_enabled", self.chk_cache.isChecked())

        try:
            cache_size = int(self.cmb_cache_size.currentText())
        except ValueError:
            cache_size = 500
        self.settings.set("cache_size_mb", cache_size)

        self.settings.set("cache_auto_cleanup", self.chk_cache_cleanup.isChecked())

        # Language
        selected_lang = self.cmb_language.currentData()
        old_lang = self.settings.get("language", "en")
        if selected_lang != old_lang:
            self.settings.set("language", selected_lang)
            QMessageBox.information(
                self,
                tr("preferences.appearance.restart_required"),
                tr("preferences.appearance.restart_required_message")
            )

        # Scanning
        ignore_list = [x.strip() for x in self.txt_ignore_folders.toPlainText().splitlines() if x.strip()]
        self.settings.set("ignore_folders", ignore_list)
        self.settings.set("device_auto_refresh", self.chk_device_auto_refresh.isChecked())

        # Face Detection - Batch save to prevent repeated saves
        self.face_config.set("insightface_model", self.cmb_insightface_model.currentData(), save_now=False)
        self.face_config.set("min_face_size", self.spin_min_face_size.value(), save_now=False)
        self.face_config.set("confidence_threshold", self.spin_confidence.value() / 100.0, save_now=False)
        self.face_config.set("clustering_eps", self.spin_cluster_eps.value() / 100.0, save_now=False)
        self.face_config.set("clustering_min_samples", self.spin_min_samples.value(), save_now=False)
        self.face_config.set("auto_cluster_after_scan", self.chk_auto_cluster.isChecked(), save_now=False)
        self.face_config.set("max_workers", self.spin_max_workers.value(), save_now=False)
        self.face_config.set("batch_size", self.spin_batch_size.value(), save_now=False)
        self.face_config.set("min_quality_score", float(self.spin_min_quality.value()), save_now=False)
        self.face_config.set("enable_gpu_batch", self.chk_gpu_batch.isChecked(), save_now=False)
        self.face_config.set("gpu_batch_size", self.spin_gpu_batch_size.value(), save_now=False)
        self.face_config.set("gpu_batch_min_photos", self.spin_gpu_batch_min.value(), save_now=False)
        # Per-project overrides
        if self.chk_project_overrides.isChecked():
            self.face_config.set_project_overrides(self.current_project_id, {
                "min_face_size": self.spin_proj_min_face.value(),
                "confidence_threshold": self.spin_proj_confidence.value() / 100.0,
                "clustering_eps": self.spin_proj_eps.value() / 100.0,
                "clustering_min_samples": self.spin_proj_min_samples.value(),
            })
        else:
            po = self.face_config.get("project_overrides", {})
            if str(self.current_project_id) in po:
                del po[str(self.current_project_id)]
                self.face_config.set("project_overrides", po)
        # UI low-confidence toggle
        self.face_config.set("show_low_confidence", self.chk_show_low_conf.isChecked(), save_now=False)

        # Screenshot policy defaults
        self.settings.set("screenshot_face_policy", self.cmb_screenshot_face_policy.currentData())
        self.settings.set("include_all_screenshot_faces", self.chk_include_all_screenshot_faces.isChecked())
        
        # Save all face detection settings at once
        self.face_config.save()
        print(f"✅ Face detection settings saved: model={self.cmb_insightface_model.currentData()}, "
              f"eps={self.spin_cluster_eps.value()}%, min_samples={self.spin_min_samples.value()}")

        # Groups settings
        self.settings.set("groups_default_scope", self.cmb_group_match_scope.currentData())
        self.settings.set("groups_event_window_hours", self.spin_event_window_hours.value())
        self.settings.set("groups_auto_index", self.chk_auto_index_groups.isChecked())
        self.settings.set("groups_incremental_index", self.chk_incremental_index.isChecked())
        self.settings.set("groups_max_avatars", self.spin_group_avatar_count.value())
        self.settings.set("groups_show_photo_count", self.chk_show_group_photo_count.isChecked())
        self.settings.set("groups_cache_matches", self.chk_cache_group_matches.isChecked())
        print(f"✅ Groups settings saved: scope={self.cmb_group_match_scope.currentData()}, "
              f"auto_index={self.chk_auto_index_groups.isChecked()}")

        # InsightFace Model Path
        model_path = self.txt_model_path.text().strip()
        old_model_path = self.settings.get("insightface_model_path", "")
        self.settings.set("insightface_model_path", model_path)

        if model_path != old_model_path:
            # Clear InsightFace check flag
            flag_file = Path('.insightface_check_done')
            if flag_file.exists():
                try:
                    flag_file.unlink()
                    print("🔄 InsightFace check flag cleared - will re-check on next startup")
                except Exception as e:
                    print(f"⚠️ Failed to clear InsightFace check flag: {e}")

            print(f"🧑 InsightFace model path configured: {model_path or '(using default locations)'}")

        # CLIP / Visual Embeddings
        self.settings.set("clip_model_variant", self.cmb_clip_variant.currentData())
        self.settings.set("clip_device", self.cmb_clip_device.currentData())
        self.settings.set("clip_auto_extract", self.chk_auto_extract.isChecked())
        self.settings.set("clip_batch_size", self.spin_extraction_batch.value())
        clip_path = self.txt_clip_model_path.text().strip()
        self.settings.set("clip_model_path", clip_path)
        print(f"🔍 CLIP settings saved: variant={self.cmb_clip_variant.currentData()}, device={self.cmb_clip_device.currentData()}")

        # Search & Discovery
        SearchConfig.set_clip_threshold(self.slider_clip_threshold.value() / 100.0)
        SearchConfig.set_default_top_k(self.spin_search_top_k.value())
        SearchConfig.set_cache_ttl(self.spin_cache_ttl.value())
        SearchConfig.set_search_debounce_ms(self.spin_debounce.value())
        SearchConfig.set_nlp_enabled(self.chk_nlp_enabled.isChecked())
        SearchConfig.set_semantic_min_similarity(self.slider_semantic_sim.value() / 100.0)
        SearchConfig.set_semantic_top_k(self.spin_semantic_top_k.value())
        SearchConfig.set_show_confidence_scores(self.chk_show_confidence.isChecked())
        SearchConfig.set_min_display_confidence(self.slider_min_confidence.value() / 100.0)
        SearchConfig.set_fusion_mode(self.combo_fusion_mode.currentText())
        SearchConfig.set_semantic_weight(self.slider_semantic_weight.value() / 100.0)
        SearchConfig.set_threshold_backoff_enabled(self.chk_threshold_backoff.isChecked())
        print(f"🔎 Search settings saved: clip_threshold={self.slider_clip_threshold.value() / 100:.2f}, "
              f"top_k={self.spin_search_top_k.value()}, cache_ttl={self.spin_cache_ttl.value()}s, "
              f"fusion={self.combo_fusion_mode.currentText()}, sem_weight={self.slider_semantic_weight.value() / 100:.2f}")

        # Ranking Weights
        RankingConfig.set_w_clip(self.slider_rank_clip.value() / 100.0)
        RankingConfig.set_w_recency(self.slider_rank_recency.value() / 100.0)
        RankingConfig.set_w_favorite(self.slider_rank_favorite.value() / 100.0)
        RankingConfig.set_w_location(self.slider_rank_location.value() / 100.0)
        RankingConfig.set_w_face_match(self.slider_rank_face.value() / 100.0)
        RankingConfig.set_w_structural(self.slider_rank_structural.value() / 100.0)
        RankingConfig.set_max_recency_boost(self.slider_max_recency_boost.value() / 100.0)
        RankingConfig.set_max_favorite_boost(self.slider_max_favorite_boost.value() / 100.0)
        RankingConfig.set_recency_halflife_days(self.spin_recency_halflife.value())
        print(f"📊 Ranking weights saved: clip={self.slider_rank_clip.value() / 100:.2f}, "
              f"recency={self.slider_rank_recency.value() / 100:.2f}, "
              f"fav={self.slider_rank_favorite.value() / 100:.2f}, "
              f"halflife={self.spin_recency_halflife.value()}d")

        # Metadata Boosts
        SearchConfig.set_meta_boost_gps(self.slider_boost_gps.value() / 100.0)
        SearchConfig.set_meta_boost_rating(self.slider_boost_rating.value() / 100.0)
        SearchConfig.set_meta_boost_date(self.slider_boost_date.value() / 100.0)

        # Backoff Parameters
        SearchConfig.set_threshold_backoff_step(self.slider_backoff_step.value() / 100.0)
        SearchConfig.set_threshold_backoff_max_retries(self.spin_backoff_retries.value())

        # OCR Text Recognition
        self.settings.set("ocr_enabled", self.chk_ocr_enabled.isChecked())
        self.settings.set("ocr_languages", self.combo_ocr_languages.currentData())

        # Badge overlays
        self.settings.set("badge_overlays_enabled", self.chk_badge_overlays.isChecked())
        self.settings.set("badge_size_px", self.spin_badge_size.value())
        self.settings.set("badge_shape", self.cmb_badge_shape.currentText())
        self.settings.set("badge_max_count", self.spin_badge_max.value())
        self.settings.set("badge_shadow", self.chk_badge_shadow.isChecked())
        
        # GPS & Location
        self.settings.set("gps_clustering_radius_km", float(self.spin_cluster_radius.value()))
        self.settings.set("gps_reverse_geocoding_enabled", self.chk_reverse_geocoding.isChecked())
        self.settings.set("gps_geocoding_timeout_sec", float(self.spin_geocoding_timeout.value()))
        self.settings.set("gps_cache_location_names", self.chk_cache_location_names.isChecked())

        # Video FFprobe path
        ffprobe_path = self.txt_ffprobe_path.text().strip()
        old_ffprobe_path = self.settings.get("ffprobe_path", "")
        self.settings.set("ffprobe_path", ffprobe_path)
        if ffprobe_path != old_ffprobe_path:
            # Clear FFmpeg check flag
            flag_file = Path('.ffmpeg_check_done')
            if flag_file.exists():
                try:
                    flag_file.unlink()
                    print(tr("preferences.video.ffmpeg_path_changed"))
                except Exception as e:
                    print(f"⚠️ Failed to clear FFmpeg check flag: {e}")

            # Invalidate FFmpeg detection cache so the new path is used on next launch
            try:
                from workers.ffmpeg_detection_worker import invalidate_cache
                invalidate_cache()
            except Exception as e:
                print(f"⚠️ Failed to invalidate FFmpeg cache: {e}")

            path_display = ffprobe_path if ffprobe_path else tr("preferences.video.ffmpeg_path_system")
            print(tr("preferences.video.ffmpeg_path_configured", path=path_display))

            # Offer to restart
            reply = QMessageBox.question(
                self,
                tr("preferences.video.restart_required"),
                tr("preferences.video.restart_required_message"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if reply == QMessageBox.Yes:
                self.accept()
                print("🔄 Restarting application...")
                # Use centralized restart with proper shutdown barrier.
                # self.window() returns the dialog itself (it's top-level),
                # so walk QApplication.topLevelWidgets() to find MainWindow.
                try:
                    from PySide6.QtWidgets import QApplication
                    main_win = None
                    for w in QApplication.topLevelWidgets():
                        if hasattr(w, "request_restart"):
                            main_win = w
                            break
                    if main_win is not None:
                        main_win.request_restart()
                        return
                    else:
                        print("[Preferences] ERROR: MainWindow.request_restart not available!")
                        QGuiApplication.quit()
                        return
                except Exception as e:
                    print(f"[Preferences] ERROR: Restart failed: {e}")
                    QGuiApplication.quit()
                return

        # Advanced
        old_decoder_warnings = self.settings.get("show_decoder_warnings", False)
        new_decoder_warnings = self.chk_decoder_warnings.isChecked()
        self.settings.set("show_decoder_warnings", new_decoder_warnings)

        self.settings.set("db_debug_logging", self.chk_db_debug.isChecked())
        self.settings.set("show_sql_queries", self.chk_sql_echo.isChecked())

        if self.chk_db_debug.isChecked():
            print(tr("preferences.developer.developer_mode_enabled"))

        # Metadata
        self.settings.set("meta_workers", int(self.spin_workers.currentText()))
        self.settings.set("meta_timeout_secs", float(self.txt_meta_timeout.currentText()))
        self.settings.set("meta_batch", int(self.txt_meta_batch.currentText()))
        self.settings.set("auto_run_backfill_after_scan", self.chk_meta_auto.isChecked())

        # Offer restart if any settings actually changed.
        # The ffprobe_path restart is handled above (returns early).
        # For all other changes, prompt the user so the app picks up the
        # new configuration — matching the previous-version behaviour.
        if self._has_changes():
            reply = QMessageBox.question(
                self,
                "Restart Required",
                "Settings have been changed.\n\n"
                "Restart the application now for changes to take effect?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.accept()
                print("🔄 Restarting application after settings change...")
                try:
                    from PySide6.QtWidgets import QApplication
                    main_win = None
                    for w in QApplication.topLevelWidgets():
                        if hasattr(w, "request_restart"):
                            main_win = w
                            break
                    if main_win is not None:
                        main_win.request_restart()
                        return
                    else:
                        print("[Preferences] ERROR: MainWindow.request_restart not available!")
                        from PySide6.QtGui import QGuiApplication
                        QGuiApplication.quit()
                        return
                except Exception as e:
                    print(f"[Preferences] ERROR: Restart failed: {e}")
                    from PySide6.QtGui import QGuiApplication
                    QGuiApplication.quit()
                    return

        self.accept()

    def _has_changes(self) -> bool:
        """Check if any settings have been modified."""
        current = {
            "skip_unchanged_photos": self.chk_skip.isChecked(),
            "use_exif_for_date": self.chk_exif.isChecked(),
            "dark_mode": self.chk_dark.isChecked(),
            "language": self.cmb_language.currentData(),
            "thumbnail_cache_enabled": self.chk_cache.isChecked(),
            "cache_size_mb": int(self.cmb_cache_size.currentText()) if self.cmb_cache_size.currentText().isdigit() else 500,
            "cache_auto_cleanup": self.chk_cache_cleanup.isChecked(),
            "ignore_folders": [x.strip() for x in self.txt_ignore_folders.toPlainText().splitlines() if x.strip()],
            "device_auto_refresh": self.chk_device_auto_refresh.isChecked(),
            "insightface_model": self.cmb_insightface_model.currentData(),
            "min_face_size": self.spin_min_face_size.value(),
            "confidence_threshold": self.spin_confidence.value() / 100.0,
            "clustering_eps": self.spin_cluster_eps.value() / 100.0,
            "clustering_min_samples": self.spin_min_samples.value(),
            "auto_cluster_after_scan": self.chk_auto_cluster.isChecked(),
            "face_max_workers": self.spin_max_workers.value(),
            "face_batch_size": self.spin_batch_size.value(),
            "insightface_model_path": self.txt_model_path.text().strip(),
            "ffprobe_path": self.txt_ffprobe_path.text().strip(),
            "show_decoder_warnings": self.chk_decoder_warnings.isChecked(),
            "db_debug_logging": self.chk_db_debug.isChecked(),
            "show_sql_queries": self.chk_sql_echo.isChecked(),
            "meta_workers": int(self.spin_workers.currentText()) if self.spin_workers.currentText().isdigit() else 4,
            "meta_timeout_secs": float(self.txt_meta_timeout.currentText()) if self.txt_meta_timeout.currentText().replace('.', '').isdigit() else 8.0,
            "meta_batch": int(self.txt_meta_batch.currentText()) if self.txt_meta_batch.currentText().isdigit() else 200,
            "auto_run_backfill_after_scan": self.chk_meta_auto.isChecked(),
            "search_clip_threshold": self.slider_clip_threshold.value() / 100.0,
            "search_default_top_k": self.spin_search_top_k.value(),
            "search_cache_ttl": self.spin_cache_ttl.value(),
            "search_debounce_ms": self.spin_debounce.value(),
            "search_nlp_enabled": self.chk_nlp_enabled.isChecked(),
            "search_semantic_min_similarity": self.slider_semantic_sim.value() / 100.0,
            "search_semantic_top_k": self.spin_semantic_top_k.value(),
            "search_show_confidence": self.chk_show_confidence.isChecked(),
            "search_min_display_confidence": self.slider_min_confidence.value() / 100.0,
            "search_fusion_mode": self.combo_fusion_mode.currentText(),
            "search_semantic_weight": self.slider_semantic_weight.value() / 100.0,
            "search_threshold_backoff": self.chk_threshold_backoff.isChecked(),
        }

        return current != self.original_settings

    def _browse_ffprobe(self):
        """Browse for ffprobe executable."""
        import platform

        if platform.system() == "Windows":
            filter_str = "Executable Files (*.exe);;All Files (*.*)"
        else:
            filter_str = "All Files (*)"

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select FFprobe Executable",
            "",
            filter_str
        )

        if path:
            self.txt_ffprobe_path.setText(path)

    def _test_ffprobe(self):
        """Test ffprobe executable."""
        import subprocess

        path = self.txt_ffprobe_path.text().strip()
        if not path:
            path = "ffprobe"  # Test system PATH

        try:
            result = subprocess.run(
                [path, '-version'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                version_line = result.stdout.split('\n')[0] if result.stdout else 'Version info unavailable'
                QMessageBox.information(
                    self,
                    tr("preferences.video.ffprobe_test_success"),
                    tr("preferences.video.ffprobe_test_success_message", version=version_line)
                )
            else:
                QMessageBox.warning(
                    self,
                    tr("preferences.video.ffprobe_test_failed"),
                    tr("preferences.video.ffprobe_test_failed_message",
                       code=result.returncode, error=result.stderr)
                )
        except FileNotFoundError:
            QMessageBox.critical(
                self,
                tr("preferences.video.ffprobe_not_found"),
                tr("preferences.video.ffprobe_not_found_message", path=path)
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                tr("preferences.video.ffprobe_test_error"),
                tr("preferences.video.ffprobe_test_error_message", error=str(e))
            )

    def _browse_models(self):
        """Browse for InsightFace models directory."""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select InsightFace Models Directory (buffalo_l)",
            "",
            QFileDialog.ShowDirsOnly
        )
        if path:
            self.txt_model_path.setText(path)

    def _test_model_path(self):
        """Test InsightFace model path."""
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import QThread, Signal

        path = self.txt_model_path.text().strip()

        if not path:
            QMessageBox.information(
                self,
                "Model Path Test",
                "No custom path specified.\n\n"
                "App will use default locations:\n"
                "  1. ./models/buffalo_l/\n"
                "  2. ~/.insightface/models/buffalo_l/"
            )
            return

        # Verify path exists
        if not Path(path).exists():
            QMessageBox.critical(
                self,
                "Model Path Test - Not Found",
                f"✗ Path does not exist:\n{path}\n\n"
                "Please check the path and try again."
            )
            return

        # Run comprehensive test
        class TestThread(QThread):
            finished_signal = Signal(bool, str)

            def __init__(self, test_path):
                super().__init__()
                self.test_path = test_path

            def run(self):
                try:
                    from utils.test_insightface_models import test_model_path
                    success, message = test_model_path(self.test_path)
                    self.finished_signal.emit(success, message)
                except Exception as e:
                    self.finished_signal.emit(False, f"Test error: {str(e)}")

        progress_dlg = QProgressDialog(
            "Testing InsightFace model loading...\nThis may take a moment...",
            None, 0, 0, self
        )
        progress_dlg.setWindowTitle("Model Test")
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.setCancelButton(None)
        progress_dlg.setMinimumDuration(0)

        test_thread = TestThread(path)

        def on_test_finished(success, message):
            progress_dlg.close()
            if success:
                QMessageBox.information(
                    self,
                    "Model Test - SUCCESS ✅",
                    message + "\n\n💡 Remember to click Save to save settings, then restart the app."
                )
            else:
                QMessageBox.critical(
                    self,
                    "Model Test - FAILED ❌",
                    message
                )

        test_thread.finished_signal.connect(on_test_finished)
        test_thread.start()
        progress_dlg.exec()

    def _check_model_status(self):
        """Check and display current model status."""
        try:
            from utils.insightface_check import get_model_download_status
            status = get_model_download_status()

            if not status['library_installed']:
                self.lbl_model_status.setText(
                    "❌ InsightFace library not installed\n"
                    "Install with: pip install insightface onnxruntime"
                )
                self.lbl_model_status.setStyleSheet(
                    "QLabel { padding: 6px; background-color: #ffe0e0; border-radius: 4px; color: #d00; }"
                )
                self.btn_download_models.setEnabled(False)
            elif status['models_available']:
                self.lbl_model_status.setText(
                    f"✅ Models installed and ready\n"
                    f"Location: {status['model_path']}"
                )
                self.lbl_model_status.setStyleSheet(
                    "QLabel { padding: 6px; background-color: #e0ffe0; border-radius: 4px; color: #060; }"
                )
                self.btn_download_models.setEnabled(False)
            else:
                self.lbl_model_status.setText(
                    "⚠️ Models not found\n"
                    "Click 'Download Models' to install buffalo_l face detection models"
                )
                self.lbl_model_status.setStyleSheet(
                    "QLabel { padding: 6px; background-color: #fff4e0; border-radius: 4px; color: #840; }"
                )
                self.btn_download_models.setEnabled(True)
        except Exception as e:
            self.lbl_model_status.setText(f"⚠️ Error checking status: {str(e)}")
            self.lbl_model_status.setStyleSheet(
                "QLabel { padding: 6px; background-color: #fff4e0; border-radius: 4px; color: #840; }"
            )

    def _download_models(self):
        """Download InsightFace models with progress dialog."""
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import QThread, Signal
        import subprocess

        class DownloadThread(QThread):
            progress = Signal(str)
            finished_signal = Signal(bool, str)

            def run(self):
                try:
                    self.progress.emit("Initializing download...")

                    # Run download_face_models.py script
                    script_path = Path("download_face_models.py")
                    if not script_path.exists():
                        self.finished_signal.emit(False, "download_face_models.py not found")
                        return

                    self.progress.emit("Downloading buffalo_l models (~200MB)...")
                    result = subprocess.run(
                        [sys.executable, str(script_path)],
                        capture_output=True,
                        text=True,
                        timeout=600  # 10 minute timeout
                    )

                    if result.returncode == 0:
                        self.finished_signal.emit(True, "Models downloaded successfully!")
                    else:
                        error_msg = result.stderr or result.stdout or "Unknown error"
                        self.finished_signal.emit(False, f"Download failed:\n{error_msg}")

                except subprocess.TimeoutExpired:
                    self.finished_signal.emit(False, "Download timed out (>10 minutes)")
                except Exception as e:
                    self.finished_signal.emit(False, f"Error: {str(e)}")

        progress_dlg = QProgressDialog("Downloading InsightFace models...", "Cancel", 0, 0, self)
        progress_dlg.setWindowTitle("Model Download")
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.setCancelButton(None)  # Disable cancel during download
        progress_dlg.setMinimumDuration(0)

        download_thread = DownloadThread()

        def on_progress(msg):
            progress_dlg.setLabelText(msg)

        def on_finished(success, message):
            progress_dlg.close()
            if success:
                QMessageBox.information(
                    self,
                    "Download Complete",
                    f"✅ {message}\n\n"
                    "Face detection models are now installed.\n"
                    "Restart the application to use face detection."
                )
                self._check_model_status()  # Update status display
            else:
                QMessageBox.critical(
                    self,
                    "Download Failed",
                    f"❌ {message}\n\n"
                    "You can try manually running:\n"
                    "python download_face_models.py"
                )

        download_thread.progress.connect(on_progress)
        download_thread.finished_signal.connect(on_finished)
        download_thread.start()
        progress_dlg.exec()

    def _check_clip_status(self):
        """Check and display CLIP model status."""
        try:
            from utils.clip_check import get_clip_download_status
            status = get_clip_download_status()

            # Update model path display
            app_root = Path(__file__).parent.absolute()
            default_path = app_root / 'models' / 'clip-vit-base-patch32'
            self.txt_clip_model_path.setText(str(default_path))

            if status['models_available']:
                size_mb = status.get('total_size_mb', 0)
                self.lbl_clip_status.setText(
                    f"✅ CLIP model installed and ready ({size_mb} MB)\n"
                    f"Location: {status['model_path']}"
                )
                self.lbl_clip_status.setStyleSheet(
                    "QLabel { padding: 8px; background-color: #e0ffe0; border-radius: 4px; color: #060; }"
                )
                self.btn_download_clip.setEnabled(False)
            elif status['missing_files']:
                missing_count = len(status['missing_files'])
                self.lbl_clip_status.setText(
                    f"⚠️ CLIP model partially installed ({missing_count} files missing)\n"
                    f"Click 'Download CLIP Model' to complete installation"
                )
                self.lbl_clip_status.setStyleSheet(
                    "QLabel { padding: 8px; background-color: #fff4e0; border-radius: 4px; color: #840; }"
                )
                self.btn_download_clip.setEnabled(True)
            else:
                self.lbl_clip_status.setText(
                    "⚠️ CLIP model not installed\n"
                    "Click 'Download CLIP Model' to install OpenAI CLIP ViT-B/32 (~600MB)"
                )
                self.lbl_clip_status.setStyleSheet(
                    "QLabel { padding: 8px; background-color: #fff4e0; border-radius: 4px; color: #840; }"
                )
                self.btn_download_clip.setEnabled(True)
        except Exception as e:
            self.lbl_clip_status.setText(f"⚠️ Error checking CLIP status: {str(e)}")
            self.lbl_clip_status.setStyleSheet(
                "QLabel { padding: 8px; background-color: #fff4e0; border-radius: 4px; color: #840; }"
            )

    def _download_clip_model(self):
        """Download CLIP model with progress dialog."""
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import QThread, Signal
        import subprocess

        class CLIPDownloadThread(QThread):
            progress = Signal(str)
            finished_signal = Signal(bool, str)

            def run(self):
                try:
                    self.progress.emit("Initializing CLIP model download...")

                    # Run download_clip_model_offline.py script
                    script_path = Path("download_clip_model_offline.py")
                    if not script_path.exists():
                        self.finished_signal.emit(False, "download_clip_model_offline.py not found")
                        return

                    self.progress.emit("Downloading CLIP ViT-B/32 model (~600MB)...")
                    result = subprocess.run(
                        [sys.executable, str(script_path)],
                        capture_output=True,
                        text=True,
                        timeout=1800  # 30 minute timeout (large download)
                    )

                    if result.returncode == 0:
                        self.finished_signal.emit(True, "CLIP model downloaded successfully!")
                    else:
                        error_msg = result.stderr or result.stdout or "Unknown error"
                        self.finished_signal.emit(False, f"Download failed:\n{error_msg}")

                except subprocess.TimeoutExpired:
                    self.finished_signal.emit(False, "Download timed out (>30 minutes)")
                except Exception as e:
                    self.finished_signal.emit(False, f"Error: {str(e)}")

        progress_dlg = QProgressDialog("Downloading CLIP model...", "Cancel", 0, 0, self)
        progress_dlg.setWindowTitle("CLIP Model Download")
        progress_dlg.setWindowModality(Qt.WindowModal)
        progress_dlg.setCancelButton(None)  # Disable cancel during download
        progress_dlg.setMinimumDuration(0)

        download_thread = CLIPDownloadThread()

        def on_progress(msg):
            progress_dlg.setLabelText(msg)

        def on_finished(success, message):
            progress_dlg.close()
            if success:
                QMessageBox.information(
                    self,
                    "Download Complete",
                    f"✅ {message}\n\n"
                    "CLIP model files are now installed in ./models/clip-vit-base-patch32/\n"
                    "You can now use visual embedding extraction."
                )
                self._check_clip_status()  # Update status display
            else:
                QMessageBox.critical(
                    self,
                    "Download Failed",
                    f"❌ {message}\n\n"
                    "You can try manually running:\n"
                    "python download_clip_model_offline.py"
                )

        download_thread.progress.connect(on_progress)
        download_thread.finished_signal.connect(on_finished)
        download_thread.start()
        progress_dlg.exec()

    def _browse_clip_models(self):
        """Browse for custom CLIP model directory."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select CLIP Model Directory",
            str(Path.home()),
            QFileDialog.ShowDirsOnly
        )
        if folder:
            self.txt_clip_model_path.setText(folder)
            self.settings.set("clip_model_path", folder)
            self._check_clip_status()

    def _open_clip_model_folder(self):
        """Open CLIP model folder in file explorer."""
        try:
            app_root = Path(__file__).parent.absolute()
            model_folder = app_root / 'models' / 'clip-vit-base-patch32'

            if not model_folder.exists():
                model_folder.mkdir(parents=True, exist_ok=True)

            # Open folder in system file explorer
            import platform
            system = platform.system()

            if system == 'Windows':
                os.startfile(str(model_folder))
            elif system == 'Darwin':  # macOS
                subprocess.run(['open', str(model_folder)])
            else:  # Linux and others
                subprocess.run(['xdg-open', str(model_folder)])

        except Exception as e:
            QMessageBox.warning(
                self,
                "Open Folder Failed",
                f"Could not open model folder:\n{str(e)}"
            )

    def _get_current_project_id(self):
        """
        Get the current project ID from the main window.

        Checks multiple sources in order:
        1. Main window's grid.project_id
        2. Main window's sidebar.project_id
        3. Default project from app_services

        Returns:
            Project ID or None if not found
        """
        try:
            parent = self.parent()

            # Try grid.project_id
            if parent and hasattr(parent, 'grid') and hasattr(parent.grid, 'project_id'):
                pid = parent.grid.project_id
                if pid is not None:
                    return pid

            # Try sidebar.project_id
            if parent and hasattr(parent, 'sidebar') and hasattr(parent.sidebar, 'project_id'):
                pid = parent.sidebar.project_id
                if pid is not None:
                    return pid

            # Fallback to default project
            from app_services import get_default_project_id
            return get_default_project_id()

        except Exception as e:
            print(f"[PreferencesDialog] Error getting project_id: {e}")
            return None

    def _refresh_gpu_info(self):
        """Refresh GPU and performance information in a background thread.

        CRITICAL: get_semantic_embedding_service() triggers torch import
        which takes 15-20s on first call. Running on main thread freezes
        the entire Preferences dialog. Use a daemon thread + QTimer callback.
        """
        import threading

        # Show loading state immediately
        self.lbl_gpu_device.setText("Detecting...")
        self.lbl_gpu_memory.setText("...")
        self.lbl_optimal_batch.setText("...")

        def _bg_work():
            """Heavy work in background thread."""
            try:
                from services.semantic_embedding_service import get_semantic_embedding_service
                service = get_semantic_embedding_service()
                gpu_info = service.get_gpu_memory_info()
                try:
                    batch_size = service.get_optimal_batch_size()
                except Exception:
                    batch_size = None
                try:
                    import faiss
                    faiss_ok = True
                except ImportError:
                    faiss_ok = False
                return gpu_info, batch_size, faiss_ok, None
            except Exception as e:
                return None, None, False, str(e)

        def _apply(result):
            """Apply results on main thread."""
            gpu_info, batch_size, faiss_ok, error = result
            try:
                if error or not gpu_info:
                    self.lbl_gpu_device.setText("Error detecting GPU")
                    self.lbl_gpu_memory.setText("—")
                    self.lbl_optimal_batch.setText("—")
                    self.lbl_faiss_status.setText("Unknown")
                    return

                device = gpu_info.get('device', 'cpu')
                total_mb = gpu_info.get('total_mb', 0)
                available_mb = gpu_info.get('available_mb', 0)

                device_text = device.upper()
                if device == 'cuda':
                    device_text = f"CUDA ({gpu_info.get('device_name', 'NVIDIA GPU')})"
                elif device == 'mps':
                    device_text = "Apple Metal (MPS)"
                elif device == 'cpu':
                    device_text = "CPU (No GPU detected)"
                self.lbl_gpu_device.setText(device_text)

                if total_mb > 0:
                    self.lbl_gpu_memory.setText(f"{available_mb:.0f} MB / {total_mb:.0f} MB")
                else:
                    self.lbl_gpu_memory.setText("N/A (CPU mode)")

                self.lbl_optimal_batch.setText(
                    f"{batch_size} photos/batch" if batch_size else "N/A"
                )

                if faiss_ok:
                    self.lbl_faiss_status.setText("✓ Available")
                    self.lbl_faiss_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
                else:
                    self.lbl_faiss_status.setText("Not installed (using numpy)")
                    self.lbl_faiss_status.setStyleSheet("color: #666;")
            except RuntimeError:
                pass  # Dialog may have been closed

        _result_holder = [None]

        def _thread_target():
            _result_holder[0] = _bg_work()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: _apply(_result_holder[0]))

        threading.Thread(target=_thread_target, daemon=True).start()

    def _refresh_embedding_stats(self):
        """Refresh embedding statistics summary in a background thread.

        Uses background thread to avoid freezing the Preferences dialog
        when get_semantic_embedding_service() triggers heavy torch import.
        """
        import threading

        project_id = self._get_current_project_id()
        if project_id is None:
            self.lbl_embedding_coverage.setText("No project selected")
            self.lbl_storage_format.setText("")
            self.btn_migrate_float16.setEnabled(False)
            return

        self.lbl_embedding_coverage.setText("Loading...")

        def _bg_work():
            try:
                from services.semantic_embedding_service import get_semantic_embedding_service
                service = get_semantic_embedding_service()
                return service.get_project_embedding_stats(project_id), None
            except Exception as e:
                return None, str(e)

        def _apply(result):
            stats, error = result
            try:
                if error or not stats:
                    self.lbl_embedding_coverage.setText("Could not load statistics")
                    self.lbl_storage_format.setText(f"Error: {str(error)[:50]}" if error else "")
                    self.btn_migrate_float16.setEnabled(False)
                    return

                total = stats.get('total_photos', 0)
                with_emb = stats.get('photos_with_embeddings', 0)
                coverage = stats.get('coverage_percent', 0)
                self.lbl_embedding_coverage.setText(
                    f"📊 Coverage: {with_emb}/{total} photos ({coverage:.1f}%)"
                )

                float16 = stats.get('float16_count', 0)
                float32 = stats.get('float32_count', 0)
                storage_mb = stats.get('storage_mb', 0)

                if float16 > 0 or float32 > 0:
                    self.lbl_storage_format.setText(
                        f"Storage: {storage_mb:.2f} MB ({float16} float16, {float32} float32)"
                    )
                    self.btn_migrate_float16.setEnabled(float32 > 0)
                else:
                    self.lbl_storage_format.setText("No embeddings yet")
                    self.btn_migrate_float16.setEnabled(False)
            except RuntimeError:
                pass  # Dialog may have been closed

        _result_holder = [None]

        def _thread_target():
            _result_holder[0] = _bg_work()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: _apply(_result_holder[0]))

        threading.Thread(target=_thread_target, daemon=True).start()

    def _open_embedding_stats_dashboard(self):
        """Open the full embedding statistics dashboard."""
        try:
            from ui.embedding_stats_dashboard import show_embedding_stats_dashboard

            # Get current project ID
            project_id = self._get_current_project_id()

            if project_id is None:
                QMessageBox.warning(
                    self,
                    "No Project Selected",
                    "Please select a project first to view embedding statistics."
                )
                return

            # Show the dashboard (non-modal)
            self._stats_dashboard = show_embedding_stats_dashboard(project_id, self)
            # Connect refresh signal to update our summary
            self._stats_dashboard.refreshRequested.connect(self._refresh_embedding_stats)

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Opening Dashboard",
                f"Could not open embedding statistics dashboard:\n{str(e)}"
            )

    def _migrate_embeddings_to_float16(self):
        """Migrate legacy float32 embeddings to float16."""
        try:
            from services.semantic_embedding_service import get_semantic_embedding_service

            reply = QMessageBox.question(
                self,
                "Migrate to Float16",
                "This will convert legacy float32 embeddings to half-precision format, "
                "saving approximately 50% storage space.\n\n"
                "This is safe and reversible (embeddings can be regenerated).\n\n"
                "Proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if reply != QMessageBox.Yes:
                return

            service = get_semantic_embedding_service()
            migrated = 0

            # Migrate in batches
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

            # Refresh stats
            self._refresh_embedding_stats()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Migration Failed",
                f"Error during migration:\n{str(e)}"
            )

    def _show_cache_stats(self):
        """Show thumbnail cache statistics."""
        try:
            from thumb_cache_db import get_cache
            cache = get_cache()
            stats = cache.get_stats()

            if "error" in stats:
                QMessageBox.warning(self, "Thumbnail Cache Stats", f"Error: {stats['error']}")
                return

            msg = (
                f"Entries: {stats['entries']}\n"
                f"Size: {stats['size_mb']} MB\n"
                f"Last Updated: {stats['last_updated']}\n"
                f"Path: {stats['path']}"
            )
            QMessageBox.information(self, "Thumbnail Cache Stats", msg)
        except Exception as e:
            QMessageBox.warning(self, "Cache Stats", f"Error retrieving cache stats:\n{str(e)}")

    def _purge_cache(self):
        """Purge old cache entries."""
        try:
            from thumb_cache_db import get_cache
            cache = get_cache()

            reply = QMessageBox.question(
                self,
                "Purge Cache",
                "Remove thumbnails older than 7 days?\n\nThis will free up disk space.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                cache.purge_stale(max_age_days=7)
                QMessageBox.information(
                    self,
                    "Purge Complete",
                    "Old thumbnails (older than 7 days) have been purged."
                )
        except Exception as e:
            QMessageBox.warning(self, "Purge Cache", f"Error purging cache:\n{str(e)}")

    def _update_badge_preview(self):
        """Update the badge preview widget with current settings."""
        try:
            self.badge_preview.update_settings(
                size=self.spin_badge_size.value(),
                shape=self.cmb_badge_shape.currentText(),
                max_count=self.spin_badge_max.value(),
                shadow=self.chk_badge_shadow.isChecked(),
                enabled=self.chk_badge_overlays.isChecked()
            )
        except Exception as e:
            print(f"[PreferencesDialog] Badge preview update error: {e}")
