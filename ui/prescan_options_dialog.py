"""
Pre-Scan Options Dialog
Phase 3B: Scan Integration

Shows options before starting a repository scan:
- Scan type (incremental vs full)
- Duplicate detection toggle
- Similar shot detection settings
- Quick pre-scan statistics (photo/video/folder counts)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QSpinBox, QDoubleSpinBox, QRadioButton,
    QButtonGroup, QFrame, QFormLayout, QScrollArea, QWidget,
    QToolButton, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QGuiApplication
from typing import Optional


# ---------------------------------------------------------------------------
# Background worker: fast file-count scan (no metadata, no hashes)
# ---------------------------------------------------------------------------
class RepoStatsWorker(QThread):
    statsReady = Signal(dict)
    statsError = Signal(str)

    def __init__(self, scan_service, root_folder: str, options: dict):
        super().__init__()
        self._scan_service = scan_service
        self._root_folder = root_folder
        self._options = options
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            stats = self._scan_service.estimate_repository_stats(
                self._root_folder,
                options=self._options,
                should_cancel=lambda: self._stop,
            )
            self.statsReady.emit(stats)
        except Exception as e:
            self.statsError.emit(str(e))


class PreScanOptions:
    """Data class for pre-scan options."""
    def __init__(self):
        self.incremental = True
        self.detect_duplicates = True
        self.detect_exact = True
        self.detect_similar = True
        self.generate_embeddings = True  # New: Auto-generate embeddings for similar detection
        self.time_window_seconds = 30  # Increased from 10 to catch more candidates
        self.similarity_threshold = 0.50  # Lowered from 0.92 to match UI minimum
        self.min_stack_size = 2  # Lowered from 3 to allow smaller stacks


class PreScanOptionsDialog(QDialog):
    """
    Pre-scan options dialog.

    Allows user to configure:
    - Scan mode (incremental vs full)
    - Duplicate detection settings
    - Similar shot detection parameters

    Optionally runs a quick background file-count so the user sees how many
    photos / videos are about to be processed before clicking Start.
    """

    def __init__(self, parent=None, default_incremental: bool = True,
                 scan_service=None):
        super().__init__(parent)
        self.options = PreScanOptions()
        self.options.incremental = default_incremental
        self._scan_service = scan_service  # may be None
        self._stats_worker = None

        self.setWindowTitle("Scan Options")
        self.setModal(True)
        self.setSizeGripEnabled(True)  # Allow resize

        self._build_ui()
        self._apply_styles()
        self._connect_signals()
        self._fit_to_screen()  # Adaptive sizing

    def _fit_to_screen(self):
        """
        Ensure dialog fits on screen and is reasonably sized.

        UX Fix: Prevents buttons being hidden on small screens or high DPI.
        """
        # Get available screen geometry
        screen = None
        if self.parent():
            screen = self.parent().screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(600, 700)
            return

        avail = screen.availableGeometry()

        # Size to 80% of screen, with reasonable limits
        target_w = min(700, max(500, int(avail.width() * 0.5)))
        target_h = min(800, max(500, int(avail.height() * 0.8)))

        self.resize(target_w, target_h)

    def _build_ui(self):
        """
        Build dialog UI with scrollable content and sticky header.

        UX Fix v9.3.0: Uses QScrollArea so content is always accessible
        on small screens. Header with primary action stays visible.
        """
        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(0)
        root_layout.setContentsMargins(0, 0, 0, 0)

        # ══════════════════════════════════════════════════════════════
        # STICKY HEADER BAR (always visible, contains primary action)
        # ══════════════════════════════════════════════════════════════
        header_bar = QFrame()
        header_bar.setObjectName("header_bar")
        header_bar.setStyleSheet("""
            #header_bar {
                background-color: #f8f9fa;
                border-bottom: 1px solid #dee2e6;
                padding: 12px 16px;
            }
        """)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 12, 16, 12)

        # Title
        header_title = QLabel("<b style='font-size: 16px;'>Scan Repository</b>")
        header_layout.addWidget(header_title)
        header_layout.addStretch(1)

        # Cancel button (secondary)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        header_layout.addWidget(btn_cancel)

        # Start button (primary) - ALWAYS visible in header
        self.btn_start = QPushButton("Start Scan")
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_start.setObjectName("btn_start")
        header_layout.addWidget(self.btn_start)

        root_layout.addWidget(header_bar)

        # ══════════════════════════════════════════════════════════════
        # SCROLLABLE CONTENT AREA
        # ══════════════════════════════════════════════════════════════
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 16, 20, 20)

        # Info text
        info = QLabel("Configure scanning options before starting the scan.")
        info.setStyleSheet("color: #666; margin-bottom: 8px;")
        layout.addWidget(info)

        # ── Quick Statistics section ──────────────────────────────
        self._stats_box = QGroupBox("Quick Statistics")
        self._stats_form = QFormLayout(self._stats_box)

        self._lbl_photos = QLabel("--")
        self._lbl_videos = QLabel("--")
        self._lbl_folders = QLabel("--")
        self._lbl_total = QLabel("--")
        self._lbl_size = QLabel("--")

        self._stats_form.addRow("Photos:", self._lbl_photos)
        self._stats_form.addRow("Videos:", self._lbl_videos)
        self._stats_form.addRow("Folders:", self._lbl_folders)
        self._stats_form.addRow("Total media:", self._lbl_total)
        self._stats_form.addRow("Estimated size:", self._lbl_size)

        layout.addWidget(self._stats_box)

        # Scan Mode Section
        scan_mode_group = QGroupBox("Scan Mode")
        scan_mode_layout = QVBoxLayout(scan_mode_group)
        scan_mode_layout.setSpacing(8)

        self.radio_incremental = QRadioButton("Incremental (recommended)")
        self.radio_incremental.setToolTip("Only scan new or modified files")
        self.radio_incremental.setChecked(self.options.incremental)

        incremental_desc = QLabel("    Skip unchanged files (faster)")
        incremental_desc.setStyleSheet("color: #666; font-size: 9pt;")

        self.radio_full = QRadioButton("Full rescan")
        self.radio_full.setToolTip("Scan all files, even if unchanged")
        self.radio_full.setChecked(not self.options.incremental)

        full_desc = QLabel("    Re-index all files from scratch")
        full_desc.setStyleSheet("color: #666; font-size: 9pt;")

        scan_mode_layout.addWidget(self.radio_incremental)
        scan_mode_layout.addWidget(incremental_desc)
        scan_mode_layout.addWidget(self.radio_full)
        scan_mode_layout.addWidget(full_desc)

        layout.addWidget(scan_mode_group)

        # Duplicate Detection Section
        dup_group = QGroupBox("Duplicate Detection")
        dup_layout = QVBoxLayout(dup_group)
        dup_layout.setSpacing(12)

        self.chk_detect_duplicates = QCheckBox("Enable duplicate detection")
        self.chk_detect_duplicates.setToolTip("Detect duplicate photos during scan")
        self.chk_detect_duplicates.setChecked(self.options.detect_duplicates)
        dup_layout.addWidget(self.chk_detect_duplicates)

        # Duplicate types container (indented)
        dup_types_widget = QFrame()
        dup_types_layout = QVBoxLayout(dup_types_widget)
        dup_types_layout.setContentsMargins(24, 8, 0, 0)
        dup_types_layout.setSpacing(8)

        self.chk_exact = QCheckBox("Exact duplicates (identical content)")
        self.chk_exact.setToolTip("Detect photos with identical file content (SHA256)")
        self.chk_exact.setChecked(self.options.detect_exact)
        dup_types_layout.addWidget(self.chk_exact)

        self.chk_similar = QCheckBox("Similar shots (burst photos, series)")
        self.chk_similar.setToolTip("Detect visually similar photos using AI")
        self.chk_similar.setChecked(self.options.detect_similar)
        dup_types_layout.addWidget(self.chk_similar)

        # Embedding generation option (indented)
        self.chk_generate_embeddings = QCheckBox("Generate AI embeddings (required for similar detection)")
        self.chk_generate_embeddings.setToolTip(
            "Extract visual embeddings using CLIP model.\n"
            "Required for similar shot detection.\n"
            "May add 2-5 seconds per photo depending on hardware."
        )
        self.chk_generate_embeddings.setChecked(self.options.generate_embeddings)
        dup_types_layout.addWidget(self.chk_generate_embeddings)

        # Similar shot settings (indented further)
        similar_settings_widget = QFrame()
        similar_settings_layout = QVBoxLayout(similar_settings_widget)
        similar_settings_layout.setContentsMargins(24, 8, 0, 0)
        similar_settings_layout.setSpacing(8)

        # Time window
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("Time window:"))
        self.spin_time_window = QSpinBox()
        self.spin_time_window.setRange(5, 120)  # Expanded from 60 to 120 for event photography
        self.spin_time_window.setValue(self.options.time_window_seconds)
        self.spin_time_window.setSuffix(" seconds")
        self.spin_time_window.setToolTip("Only compare photos within this time range\nDefault 30s for normal shooting, up to 120s for events")
        time_row.addWidget(self.spin_time_window)
        time_row.addStretch(1)
        similar_settings_layout.addLayout(time_row)

        # Similarity threshold
        sim_row = QHBoxLayout()
        sim_row.addWidget(QLabel("Similarity:"))
        self.spin_similarity = QDoubleSpinBox()
        self.spin_similarity.setRange(0.50, 0.99)  # Lowered from 0.80 to match UI slider
        self.spin_similarity.setSingleStep(0.01)
        self.spin_similarity.setValue(self.options.similarity_threshold)
        self.spin_similarity.setToolTip("Minimum visual similarity (0.50-0.99)\nLower = more photos grouped, Higher = only very similar")
        sim_row.addWidget(self.spin_similarity)
        sim_row.addStretch(1)
        similar_settings_layout.addLayout(sim_row)

        # Min stack size
        stack_row = QHBoxLayout()
        stack_row.addWidget(QLabel("Min stack size:"))
        self.spin_stack_size = QSpinBox()
        self.spin_stack_size.setRange(2, 10)
        self.spin_stack_size.setValue(self.options.min_stack_size)
        self.spin_stack_size.setSuffix(" photos")
        self.spin_stack_size.setToolTip("Minimum photos to create a stack")
        stack_row.addWidget(self.spin_stack_size)
        stack_row.addStretch(1)
        similar_settings_layout.addLayout(stack_row)

        similar_settings_widget.setLayout(similar_settings_layout)
        dup_types_layout.addWidget(similar_settings_widget)
        self.similar_settings_widget = similar_settings_widget

        dup_types_widget.setLayout(dup_types_layout)
        dup_layout.addWidget(dup_types_widget)
        self.dup_types_widget = dup_types_widget

        layout.addWidget(dup_group)

        # Info message
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #e8f4f8;
                border: 1px solid #b3d9e6;
                border-radius: 4px;
                padding: 12px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(4)

        info_title = QLabel("Note:")
        info_title.setStyleSheet("font-weight: bold; color: #1a73e8;")
        info_layout.addWidget(info_title)

        info_text = QLabel(
            "Duplicate detection will run automatically after the scan completes. "
            "Exact duplicate detection is fast, but embedding generation and similar "
            "shot detection may take 2-5 seconds per photo depending on hardware (GPU vs CPU)."
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #444;")
        info_layout.addWidget(info_text)

        layout.addWidget(info_frame)

        # Add stretch at end of scroll content
        layout.addStretch(1)

        # Set content widget and add scroll area to root layout
        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)  # stretch=1 so scroll takes remaining space

    def _apply_styles(self):
        """Apply custom styles."""
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ddd;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                background-color: white;
            }
            QPushButton#btn_start {
                background-color: #1a73e8;
                color: white;
                border: none;
                padding: 8px 24px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton#btn_start:hover {
                background-color: #1557b0;
            }
            QPushButton#btn_start:pressed {
                background-color: #0d47a1;
            }
        """)
        self.btn_start.setObjectName("btn_start")

    def _connect_signals(self):
        """Connect signals."""
        # Enable/disable duplicate types based on main checkbox
        self.chk_detect_duplicates.toggled.connect(self._on_duplicates_toggled)

        # Enable/disable similar settings based on similar checkbox
        self.chk_similar.toggled.connect(self._on_similar_toggled)

        # Initial state
        self._on_duplicates_toggled(self.chk_detect_duplicates.isChecked())
        self._on_similar_toggled(self.chk_similar.isChecked())

    def _on_duplicates_toggled(self, checked: bool):
        """Handle duplicate detection toggle."""
        self.dup_types_widget.setEnabled(checked)

    def _on_similar_toggled(self, checked: bool):
        """Handle similar shots toggle."""
        self.similar_settings_widget.setEnabled(checked)

    def _on_start_clicked(self):
        """Handle start button click."""
        # Stop any running stats worker
        if self._stats_worker is not None:
            try:
                self._stats_worker.request_stop()
            except Exception:
                pass

        # Save options
        self.options.incremental = self.radio_incremental.isChecked()
        self.options.detect_duplicates = self.chk_detect_duplicates.isChecked()
        self.options.detect_exact = self.chk_exact.isChecked()
        self.options.detect_similar = self.chk_similar.isChecked()
        self.options.generate_embeddings = self.chk_generate_embeddings.isChecked()
        self.options.time_window_seconds = self.spin_time_window.value()
        self.options.similarity_threshold = self.spin_similarity.value()
        self.options.min_stack_size = self.spin_stack_size.value()

        self.accept()

    def get_options(self) -> PreScanOptions:
        """Get the configured options."""
        return self.options

    # ------------------------------------------------------------------
    # Quick Statistics helpers
    # ------------------------------------------------------------------
    def _set_stats_loading(self, text: str = "Counting..."):
        self._lbl_photos.setText(text)
        self._lbl_videos.setText(text)
        self._lbl_folders.setText(text)
        self._lbl_total.setText(text)
        self._lbl_size.setText(text)

    @staticmethod
    def _format_bytes(n: int) -> str:
        if not n:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(n)
        idx = 0
        while size >= 1024.0 and idx < len(units) - 1:
            size /= 1024.0
            idx += 1
        return f"{size:.1f} {units[idx]}"

    def start_stats_count(self, root_folder: str):
        """Kick off a background file-count for *root_folder*.

        Safe to call multiple times (cancels previous worker).
        Requires a *scan_service* that implements
        ``estimate_repository_stats(root, options, should_cancel)``.
        """
        if not root_folder or self._scan_service is None:
            return

        # Cancel any previous run
        if self._stats_worker is not None:
            try:
                self._stats_worker.request_stop()
            except Exception:
                pass

        self._set_stats_loading()

        options = {
            "ignore_hidden": True,
        }
        self._stats_worker = RepoStatsWorker(
            self._scan_service, root_folder, options,
        )
        self._stats_worker.statsReady.connect(self._on_stats_ready)
        self._stats_worker.statsError.connect(self._on_stats_error)
        self._stats_worker.start()

    def _on_stats_ready(self, stats: dict):
        photos = int(stats.get("photos", 0))
        videos = int(stats.get("videos", 0))
        folders = int(stats.get("folders", 0))
        total = photos + videos
        size_b = int(stats.get("bytes", 0))

        self._lbl_photos.setText(f"{photos:,}")
        self._lbl_videos.setText(f"{videos:,}")
        self._lbl_folders.setText(f"{folders:,}")
        self._lbl_total.setText(f"{total:,}")
        self._lbl_size.setText(self._format_bytes(size_b))

    def _on_stats_error(self, msg: str):
        self._set_stats_loading("Error")
