# video_player_qt.py
# Version 2.0.0 dated 2025-11-10
# Enhanced video player with metadata panel and tagging support

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider,
    QLabel, QStyle, QSizePolicy, QScrollArea, QFrame, QLineEdit,
    QToolButton, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, QUrl, Signal, QTimer, QPoint
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from pathlib import Path
import os
from translation_manager import tr


class VideoPlayerPanel(QWidget):
    """
    Enhanced video player panel with playback controls, metadata display, and tagging.

    Features:
    - Video playback with QMediaPlayer
    - Play/pause, seek, volume controls
    - Detailed metadata panel (resolution, fps, codec, bitrate, file info)
    - Video tagging support (add/remove tags)
    - Keyboard shortcuts (Space = play/pause, Left/Right = seek, I = toggle info)

    Signals:
    - closed: Emitted when player is closed
    """

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_video_path = None
        self.current_video_id = None
        self.current_metadata = None
        self.current_project_id = None  # BUG FIX #5: Store project_id explicitly
        self.is_seeking = False
        self.info_panel_visible = False

        # Navigation support
        self._video_list = []
        self._current_index = 0

        self._setup_ui()
        self._setup_player()
        self._setup_connections()

    def _setup_ui(self):
        """Create UI layout with video widget, controls, and metadata panel."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === Main Content Area (Video + Info Panel) ===
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # === Video Widget ===
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(300)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout.addWidget(self.video_widget, 1)

        # === Info Panel (Initially Hidden) ===
        self.info_panel = self._create_info_panel()
        self.info_panel.hide()
        content_layout.addWidget(self.info_panel)

        main_layout.addLayout(content_layout, 1)

        # === Control Bar ===
        control_bar = QWidget()
        control_bar.setMaximumHeight(80)
        control_layout = QVBoxLayout(control_bar)
        control_layout.setContentsMargins(10, 5, 10, 5)
        control_layout.setSpacing(5)

        # --- Timeline Slider ---
        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.setEnabled(False)
        control_layout.addWidget(self.timeline_slider)
        # Keyframe preview overlay
        self.preview_label = QLabel(self)
        self.preview_label.setStyleSheet("QLabel{border:1px solid #333; background:#000;}")
        self.preview_label.setVisible(False)
        self.preview_label.setFixedSize(160, 90)
        self._keyframes = []  # list[(ms, QPixmap)]

        # --- Time Labels ---
        time_layout = QHBoxLayout()
        self.time_label = QLabel("0:00")
        self.time_label.setMinimumWidth(50)
        time_layout.addWidget(self.time_label)
        time_layout.addStretch()
        self.duration_label = QLabel("0:00")
        self.duration_label.setMinimumWidth(50)
        self.duration_label.setAlignment(Qt.AlignRight)
        time_layout.addWidget(self.duration_label)
        control_layout.addLayout(time_layout)

        # --- Playback Controls ---
        controls_layout = QHBoxLayout()

        # Play/Pause button
        self.play_button = QPushButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setFixedSize(40, 40)
        self.play_button.setToolTip("Play/Pause (Space)")
        controls_layout.addWidget(self.play_button)

        # Stop button
        self.stop_button = QPushButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setFixedSize(40, 40)
        self.stop_button.setToolTip("Stop")
        controls_layout.addWidget(self.stop_button)

        controls_layout.addSpacing(20)

        # Navigation buttons (Previous/Next video)
        self.prev_button = QPushButton()
        self.prev_button.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipBackward))
        self.prev_button.setFixedSize(40, 40)
        self.prev_button.setToolTip("Previous Video")
        self.prev_button.setEnabled(False)  # Disabled until video list is set
        controls_layout.addWidget(self.prev_button)

        self.next_button = QPushButton()
        self.next_button.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        self.next_button.setFixedSize(40, 40)
        self.next_button.setToolTip("Next Video")
        self.next_button.setEnabled(False)  # Disabled until video list is set
        controls_layout.addWidget(self.next_button)

        controls_layout.addSpacing(20)

        # Volume controls
        volume_label = QLabel("🔊")
        controls_layout.addWidget(volume_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setMaximumWidth(100)
        self.volume_slider.setToolTip("Volume")
        controls_layout.addWidget(self.volume_slider)

        controls_layout.addStretch()

        # Metadata label
        self.metadata_label = QLabel("No video loaded")
        self.metadata_label.setStyleSheet("color: #888; font-size: 11px;")
        controls_layout.addWidget(self.metadata_label)

        controls_layout.addSpacing(10)

        # Info toggle button
        self.info_button = QPushButton("ℹ️")
        self.info_button.setFixedSize(30, 30)
        self.info_button.setToolTip("Toggle Info Panel (I)")
        self.info_button.setCheckable(True)
        self.info_button.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border-radius: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:checked {
                background-color: #27ae60;
            }
        """)
        controls_layout.addWidget(self.info_button)

        controls_layout.addSpacing(5)

        # Close button
        self.close_button = QPushButton("✕")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setToolTip("Close Player")
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border-radius: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
        """)
        controls_layout.addWidget(self.close_button)

        control_layout.addLayout(controls_layout)
        main_layout.addWidget(control_bar)

    def _setup_player(self):
        """Initialize QMediaPlayer and audio output."""
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(0.7)

        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        # Update timer for smooth timeline updates
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(100)  # Update every 100ms

    def _setup_connections(self):
        """Connect signals and slots."""
        # Player signals
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_error)

        # Control signals
        self.play_button.clicked.connect(self._toggle_play_pause)
        self.stop_button.clicked.connect(self._stop)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.valueChanged.connect(self._on_slider_value_changed)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.info_button.toggled.connect(self._toggle_info_panel)
        self.close_button.clicked.connect(self._close_player)

        # Navigation signals
        self.prev_button.clicked.connect(self._go_prev)
        self.next_button.clicked.connect(self._go_next)

        # Update timer
        self.update_timer.timeout.connect(self._update_position)

    def _schedule_keyframes(self, video_path: str):
        """Extract ~10 keyframes using ffmpeg if available (non-blocking)."""
        import threading, tempfile, subprocess
        def worker():
            try:
                # Clear previous
                self._keyframes = []
                # Determine ffmpeg path
                ffmpeg = 'ffmpeg'
                try:
                    from settings_manager_qt import SettingsManager
                    sm = SettingsManager()
                    p = sm.get('ffprobe_path', '')
                    if p:
                        ffmpeg = os.path.join(os.path.dirname(p), 'ffmpeg')
                except Exception:
                    pass
                # Duration in ms
                dur = self.timeline_slider.maximum() or 0
                if dur <= 0:
                    return
                # Ten evenly spaced positions
                positions = [int(dur * i / 10) for i in range(1, 10)]
                tmpdir = tempfile.mkdtemp()
                for i, ms in enumerate(positions):
                    sec = ms / 1000.0
                    out = os.path.join(tmpdir, f'kf_{i}.jpg')
                    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-ss', str(sec), '-i', video_path, '-frames:v', '1', '-q:v', '3', out]
                    try:
                        subprocess.run(cmd, timeout=5)
                        if os.path.exists(out):
                            from PySide6.QtGui import QPixmap
                            pm = QPixmap(out)
                            if not pm.isNull():
                                self._keyframes.append((ms, pm))
                    except Exception:
                        continue
                # Sort by position
                self._keyframes.sort(key=lambda t: t[0])
                print(f"[VideoPlayer] Keyframes ready: {len(self._keyframes)}")
            except Exception as e:
                print(f"[VideoPlayer] Keyframe worker failed: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _find_keyframe_for_ms(self, ms: int):
        """Return nearest keyframe pixmap for position (or None)."""
        if not self._keyframes:
            return None
        # Nearest by absolute diff
        best = min(self._keyframes, key=lambda t: abs(t[0]-ms))
        return best[1] if best else None
        try:
            # Player signals
            self.player.durationChanged.disconnect()
            self.player.positionChanged.disconnect()
            self.player.playbackStateChanged.disconnect()
            self.player.errorOccurred.disconnect()
        except (RuntimeError, TypeError):
            pass

        try:
            # Control signals
            self.play_button.clicked.disconnect()
            self.stop_button.clicked.disconnect()
            self.timeline_slider.sliderPressed.disconnect()
            self.timeline_slider.sliderReleased.disconnect()
            self.timeline_slider.valueChanged.disconnect()
            self.volume_slider.valueChanged.disconnect()
            self.info_button.toggled.disconnect()
            self.close_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass

        try:
            # Navigation signals
            self.prev_button.clicked.disconnect()
            self.next_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass

        try:
            # Update timer
            self.update_timer.timeout.disconnect()
        except (RuntimeError, TypeError):
            pass

    def load_video(self, video_path: str, metadata: dict = None, project_id: int = None):
        """
        Load and prepare video for playback.

        Args:
            video_path: Path to video file
            metadata: Optional video metadata dict (duration, resolution, etc.)
            project_id: Optional project ID (for tagging support)
        """
        if not video_path or not Path(video_path).exists():
            print(f"[VideoPlayer] Video file not found: {video_path}")
            return

        # BUG #4 FIX: Stop previous playback and release resources before loading new video
        self.player.stop()
        self.player.setSource(QUrl())  # Clear previous source to release file handles

        self.current_video_path = video_path
        self.current_metadata = metadata
        self.current_video_id = metadata.get('id') if metadata else None

        # BUG FIX #5: Store project_id explicitly for tagging operations
        # Priority: explicit parameter > metadata > None (will cause error if needed)
        if project_id is not None:
            self.current_project_id = project_id
        elif metadata and 'project_id' in metadata:
            self.current_project_id = metadata['project_id']
        else:
            self.current_project_id = None

        # Load video
        video_url = QUrl.fromLocalFile(str(video_path))
        self.player.setSource(video_url)

        # Update metadata label
        if metadata:
            duration = metadata.get('duration_seconds', 0)
            width = metadata.get('width', 0)
            height = metadata.get('height', 0)
            codec = metadata.get('codec', 'unknown')

            meta_text = f"📹 {width}x{height} | {codec}"
            if duration:
                mins = int(duration // 60)
                secs = int(duration % 60)
                meta_text += f" | {mins}:{secs:02d}"
            self.metadata_label.setText(meta_text)
        else:
            filename = Path(video_path).name
            self.metadata_label.setText(f"📹 {filename}")

        # Enable controls
        self.timeline_slider.setEnabled(True)

        # Update info panel if visible
        if self.info_panel_visible:
            self._update_info_panel()

        # Start playback automatically
        self.player.play()
        self.update_timer.start()
        # Generate lightweight keyframes for scrub preview
        try:
            self._schedule_keyframes(video_path)
        except Exception as e:
            print(f"[VideoPlayer] Keyframe scheduling failed: {e}")

        print(f"[VideoPlayer] Loaded: {video_path}")

    def _toggle_play_pause(self):
        """Toggle between play and pause."""
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _stop(self):
        """Stop playback and reset position."""
        self.player.stop()
        self.update_timer.stop()

    def _on_duration_changed(self, duration):
        """Update slider range when video duration is known."""
        self.timeline_slider.setRange(0, duration)
        self.duration_label.setText(self._format_time(duration))
        try:
            if not self._keyframes:
                self._schedule_keyframes(self.current_video_path or '')
        except Exception:
            pass

    def _on_position_changed(self, position):
        """Update timeline slider when playback position changes."""
        if not self.is_seeking:
            self.timeline_slider.setValue(position)
            self.time_label.setText(self._format_time(position))

    def _update_position(self):
        """Manual position update from timer (smoother than signal alone)."""
        if not self.is_seeking and self.player.playbackState() == QMediaPlayer.PlayingState:
            position = self.player.position()
            self.timeline_slider.setValue(position)
            self.time_label.setText(self._format_time(position))

    def _on_playback_state_changed(self, state):
        """Update play button icon based on playback state."""
        if state == QMediaPlayer.PlayingState:
            self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
            self.update_timer.start()
        else:
            self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            if state == QMediaPlayer.StoppedState:
                self.update_timer.stop()

    def _on_slider_pressed(self):
        """User started dragging timeline slider."""
        self.is_seeking = True

    def _on_slider_released(self):
        """User finished dragging timeline slider - seek to position."""
        self.is_seeking = False
        position = self.timeline_slider.value()
        self.player.setPosition(position)

    def _on_slider_value_changed(self, value):
        """Update time label while dragging slider."""
        if self.is_seeking:
            self.time_label.setText(self._format_time(value))
            # Show preview near slider if keyframes available
            pm = self._find_keyframe_for_ms(value)
            if pm:
                self.preview_label.setPixmap(pm.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                # Position above the slider thumb
                try:
                    posx = int((value / max(1, self.timeline_slider.maximum())) * self.timeline_slider.width())
                    gp = self.timeline_slider.mapToGlobal(self.timeline_slider.rect().topLeft())
                    self.preview_label.move(self.mapFromGlobal(gp + QPoint(max(0, posx-80), -100)))
                except Exception:
                    pass
                self.preview_label.setVisible(True)
            else:
                self.preview_label.setVisible(False)

    def _on_volume_changed(self, value):
        """Update audio volume."""
        volume = value / 100.0
        self.audio_output.setVolume(volume)

    def _on_error(self, error, error_string):
        """Handle playback errors."""
        print(f"[VideoPlayer] Error: {error_string}")
        self.metadata_label.setText(f"❌ Error: {error_string}")

    def _close_player(self):
        """Close the video player."""
        # BUG FIX #4: Properly release all media resources
        self.player.stop()
        self.update_timer.stop()
        
        # CRITICAL: Disconnect all signals before releasing resources
        self._disconnect_all_signals()
        
        # Release media resources to prevent memory leak
        self.player.setSource(QUrl())  # Clear source to release file handles
        
        # Clear metadata references
        self.current_video_path = None
        self.current_metadata = None
        self.current_video_id = None
        self.current_project_id = None
        
        self.closed.emit()

    def _format_time(self, milliseconds):
        """Format milliseconds to MM:SS or H:MM:SS."""
        if milliseconds < 0:
            return "0:00"

        total_seconds = milliseconds // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key_Space:
            self._toggle_play_pause()
            event.accept()
        elif event.key() == Qt.Key_I:
            # Toggle info panel
            self.info_button.setChecked(not self.info_button.isChecked())
            event.accept()
        elif event.key() == Qt.Key_Left:
            # Seek backward 5 seconds
            position = max(0, self.player.position() - 5000)
            self.player.setPosition(position)
            event.accept()
        elif event.key() == Qt.Key_Right:
            # Seek forward 5 seconds
            duration = self.player.duration()
            position = min(duration, self.player.position() + 5000)
            self.player.setPosition(position)
            event.accept()
        elif event.key() == Qt.Key_Escape:
            self._close_player()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """Clean up when widget is closed."""
        # BUG FIX #4: Properly release all media resources on widget close
        self.player.stop()
        self.update_timer.stop()
        
        # CRITICAL: Disconnect all signals before releasing resources
        self._disconnect_all_signals()
        
        # Release media resources to prevent memory leak
        self.player.setSource(QUrl())  # Clear source to release file handles
        
        # Clear metadata references
        self.current_video_path = None
        self.current_metadata = None
        self.current_video_id = None
        self.current_project_id = None
        
        super().closeEvent(event)
    # ========================================================================
    # INFO PANEL METHODS
    # ========================================================================

    def _create_info_panel(self):
        """Create the detailed information panel."""
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setFixedWidth(350)
        panel.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border-left: 2px solid #dee2e6;
            }
        """)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # === Title ===
        title_label = QLabel("📹 Video Information")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        # === Metadata Section ===
        self.info_metadata_widget = QWidget()
        self.info_metadata_layout = QVBoxLayout(self.info_metadata_widget)
        self.info_metadata_layout.setContentsMargins(0, 0, 0, 0)
        self.info_metadata_layout.setSpacing(8)
        layout.addWidget(self.info_metadata_widget)

        # === Tags Section ===
        tags_label = QLabel("🏷️ Tags")
        tags_font = QFont()
        tags_font.setPointSize(10)
        tags_font.setBold(True)
        tags_label.setFont(tags_font)
        layout.addWidget(tags_label)

        # Tag input
        tag_input_layout = QHBoxLayout()
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText(tr('search.placeholder_add_tag'))
        self.tag_input.returnPressed.connect(self._add_tag)
        tag_input_layout.addWidget(self.tag_input)

        add_tag_btn = QPushButton("Add")
        add_tag_btn.clicked.connect(self._add_tag)
        add_tag_btn.setFixedWidth(60)
        tag_input_layout.addWidget(add_tag_btn)
        layout.addLayout(tag_input_layout)

        # Tag display
        self.tags_widget = QWidget()
        self.tags_layout = QVBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(5)
        layout.addWidget(self.tags_widget)

        layout.addStretch()

        return scroll

    def _toggle_info_panel(self, show: bool):
        """Toggle the info panel visibility."""
        self.info_panel_visible = show
        if show:
            self._update_info_panel()
            self.info_panel.show()
        else:
            self.info_panel.hide()

    def _update_info_panel(self):
        """Update the info panel with current video metadata."""
        # Clear existing metadata
        while self.info_metadata_layout.count():
            item = self.info_metadata_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.current_metadata:
            no_data_label = QLabel("No metadata available")
            no_data_label.setStyleSheet("color: #6c757d; font-style: italic;")
            self.info_metadata_layout.addWidget(no_data_label)
            return

        meta = self.current_metadata

        # Helper function to add metadata rows
        def add_meta_row(label, value, copyable=False):
            row = QHBoxLayout()
            row.setSpacing(10)

            label_widget = QLabel(f"<b>{label}:</b>")
            label_widget.setMinimumWidth(100)
            label_widget.setStyleSheet("color: #495057;")
            row.addWidget(label_widget)

            value_widget = QLabel(str(value))
            value_widget.setWordWrap(True)
            value_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_widget.setStyleSheet("color: #212529;")
            row.addWidget(value_widget, 1)

            if copyable:
                copy_btn = QToolButton()
                copy_btn.setText("📋")
                copy_btn.setToolTip("Copy to clipboard")
                copy_btn.setFixedSize(24, 24)
                copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(str(value)))
                row.addWidget(copy_btn)

            self.info_metadata_layout.addLayout(row)

        # === Video Specifications ===
        if meta.get('width') and meta.get('height'):
            resolution = f"{meta['width']}×{meta['height']}"
            add_meta_row("Resolution", resolution)

        if meta.get('duration_seconds'):
            duration = meta['duration_seconds']
            mins = int(duration // 60)
            secs = int(duration % 60)
            add_meta_row("Duration", f"{mins}:{secs:02d}")

        if meta.get('fps'):
            add_meta_row("Frame Rate", f"{meta['fps']:.2f} fps")

        if meta.get('codec'):
            add_meta_row("Codec", meta['codec'])

        if meta.get('bitrate'):
            # BUG FIX: bitrate is stored in kbps, not bps
            bitrate_mbps = meta['bitrate'] / 1000
            add_meta_row("Bitrate", f"{bitrate_mbps:.2f} Mbps")

        # === File Information ===
        add_meta_row("", "")  # Spacer

        if self.current_video_path:
            add_meta_row("Filename", os.path.basename(self.current_video_path), copyable=True)
            add_meta_row("Path", os.path.dirname(self.current_video_path), copyable=True)

        if meta.get('size_kb'):
            size_mb = meta['size_kb'] / 1024
            if size_mb > 1024:
                size_str = f"{size_mb/1024:.2f} GB"
            else:
                size_str = f"{size_mb:.2f} MB"
            add_meta_row("File Size", size_str)

        # === Date Information ===
        if meta.get('date_taken'):
            add_meta_row("Date Taken", meta['date_taken'])

        if meta.get('modified'):
            add_meta_row("Modified", meta['modified'])

        if meta.get('created_date'):
            add_meta_row("Created", meta['created_date'])

        # === Status ===
        if meta.get('metadata_status'):
            # BUG FIX: Worker sets status to 'ok', not 'completed'
            status_emoji = "✅" if meta['metadata_status'] == 'ok' else "⏳"
            add_meta_row("Status", f"{status_emoji} {meta['metadata_status']}")

        # Refresh tags
        self._refresh_tags()

    def _refresh_tags(self):
        """Refresh the tags display."""
        # Clear existing tags
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.current_video_id:
            no_tags_label = QLabel("No video ID available for tagging")
            no_tags_label.setStyleSheet("color: #6c757d; font-style: italic;")
            self.tags_layout.addWidget(no_tags_label)
            return

        # Get tags from database
        try:
            from services.video_service import VideoService
            video_service = VideoService()
            tags = video_service.get_tags_for_video(self.current_video_id)

            if not tags:
                no_tags_label = QLabel("No tags yet")
                no_tags_label.setStyleSheet("color: #6c757d; font-style: italic;")
                self.tags_layout.addWidget(no_tags_label)
                return

            # Display each tag as a chip with remove button
            for tag in tags:
                tag_widget = QWidget()
                tag_layout = QHBoxLayout(tag_widget)
                tag_layout.setContentsMargins(8, 4, 8, 4)
                tag_layout.setSpacing(5)

                tag_label = QLabel(tag.get('name', 'Unknown'))
                tag_label.setStyleSheet("""
                    QLabel {
                        background-color: #007bff;
                        color: white;
                        padding: 4px 8px;
                        border-radius: 3px;
                    }
                """)
                tag_layout.addWidget(tag_label)

                remove_btn = QToolButton()
                remove_btn.setText("✕")
                remove_btn.setFixedSize(20, 20)
                remove_btn.setStyleSheet("""
                    QToolButton {
                        background-color: #dc3545;
                        color: white;
                        border-radius: 10px;
                        font-weight: bold;
                    }
                    QToolButton:hover {
                        background-color: #c82333;
                    }
                """)
                tag_id = tag.get('id')
                remove_btn.clicked.connect(lambda checked=False, tid=tag_id: self._remove_tag(tid))
                tag_layout.addWidget(remove_btn)

                tag_layout.addStretch()
                self.tags_layout.addWidget(tag_widget)

        except Exception as e:
            error_label = QLabel(f"Error loading tags: {e}")
            error_label.setStyleSheet("color: #dc3545;")
            error_label.setWordWrap(True)
            self.tags_layout.addWidget(error_label)

    # ========================================================================
    # TAGGING METHODS
    # ========================================================================

    def _add_tag(self):
        """Add a new tag to the current video."""
        if not self.current_video_id:
            QMessageBox.warning(self, "No Video", "No video loaded for tagging.")
            return

        # BUG FIX #5: Check if project_id is available before tagging
        if not self.current_project_id:
            QMessageBox.warning(self, "Error", "Cannot add tags: Project ID not available.")
            print("[VideoPlayer] ERROR: current_project_id is None - cannot add tags")
            return

        tag_name = self.tag_input.text().strip()
        if not tag_name:
            return

        try:
            from services.video_service import VideoService
            from services.tag_service import TagService

            # BUG FIX #5: Use explicitly stored project_id instead of hardcoded fallback
            project_id = self.current_project_id

            # Get or create tag
            tag_service = TagService()
            tag = tag_service.get_or_create_tag(tag_name, project_id)

            if not tag:
                QMessageBox.warning(self, "Error", f"Failed to create tag: {tag_name}")
                return

            tag_id = tag.get('id')

            # Add tag to video
            video_service = VideoService()
            success = video_service.add_tag_to_video(self.current_video_id, tag_id)

            if success:
                self.tag_input.clear()
                self._refresh_tags()
                print(f"[VideoPlayer] Added tag '{tag_name}' to video {self.current_video_id}")
            else:
                QMessageBox.information(self, "Already Tagged", f"Video already has tag: {tag_name}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add tag: {e}")
            print(f"[VideoPlayer] Error adding tag: {e}")

    def _remove_tag(self, tag_id: int):
        """Remove a tag from the current video."""
        if not self.current_video_id:
            return

        try:
            from services.video_service import VideoService
            video_service = VideoService()

            success = video_service.remove_tag_from_video(self.current_video_id, tag_id)

            if success:
                self._refresh_tags()
                print(f"[VideoPlayer] Removed tag {tag_id} from video {self.current_video_id}")
            else:
                QMessageBox.warning(self, "Error", "Failed to remove tag.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to remove tag: {e}")
            print(f"[VideoPlayer] Error removing tag: {e}")

    # === Navigation Methods ===

    def set_video_list(self, video_list: list, start_index: int = 0):
        """
        Set the list of videos for navigation.

        Args:
            video_list: List of video file paths
            start_index: Index of the current video in the list
        """
        self._video_list = list(video_list or [])
        self._current_index = max(0, min(start_index, len(self._video_list) - 1))
        self._update_nav_buttons()
        print(f"[VideoPlayer] Video list set: {len(self._video_list)} videos, starting at index {self._current_index}")

    def _update_nav_buttons(self):
        """Update the enabled state of prev/next buttons based on current index."""
        has_list = len(self._video_list) > 0

        # Enable/disable buttons based on position in list
        self.prev_button.setEnabled(has_list and self._current_index > 0)
        self.next_button.setEnabled(has_list and self._current_index < len(self._video_list) - 1)

        # Update metadata label to show position in list
        if has_list and len(self._video_list) > 1:
            position_text = f"Video {self._current_index + 1}/{len(self._video_list)}"
            current_text = self.metadata_label.text()
            # Append position if not already shown
            if "/" not in current_text or "Video" not in current_text:
                separator = " | " if current_text and current_text != "No video loaded" else ""
                self.metadata_label.setText(f"{current_text}{separator}{position_text}")

    def _go_prev(self):
        """Navigate to the previous video in the list."""
        if not self._video_list or self._current_index <= 0:
            return

        self._current_index -= 1
        video_path = self._video_list[self._current_index]
        print(f"[VideoPlayer] Loading previous video ({self._current_index + 1}/{len(self._video_list)}): {video_path}")

        # Load the previous video
        self._load_video_from_list(video_path)

    def _go_next(self):
        """Navigate to the next video in the list."""
        if not self._video_list or self._current_index >= len(self._video_list) - 1:
            return

        self._current_index += 1
        video_path = self._video_list[self._current_index]
        print(f"[VideoPlayer] Loading next video ({self._current_index + 1}/{len(self._video_list)}): {video_path}")

        # Load the next video
        self._load_video_from_list(video_path)

    def _load_video_from_list(self, video_path: str):
        """
        Load a video from the navigation list.

        Args:
            video_path: Path to the video file
        """
        if not video_path or not Path(video_path).exists():
            QMessageBox.warning(self, "Video Not Found", f"Video file not found:\n{video_path}")
            return

        # Get video metadata from database
        metadata = None
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            if self.current_project_id:
                metadata = db.get_video_by_path(video_path, self.current_project_id)
        except Exception as e:
            print(f"[VideoPlayer] Failed to load metadata for {video_path}: {e}")

        # Load the video (reuse existing load_video method)
        self.load_video(video_path, metadata, project_id=self.current_project_id)

        # Update navigation buttons
        self._update_nav_buttons()
