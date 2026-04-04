# layouts/video_editor_mixin.py
# Video editing functionality mixin for MediaLightbox
# Phase 1: EDITOR-ONLY features (Trim, Rotate, Export)
# REUSES existing viewer video controls (no duplicates)

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QFileDialog, QMessageBox, QProgressDialog, QApplication,
    QCheckBox, QGroupBox
)
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QPixmap, QImage
import os
import shutil


class VideoEditorMixin:
    """
    Mixin class providing video EDITING capabilities for MediaLightbox.
    
    EDITOR-ONLY Features (does NOT duplicate viewer controls):
    - Trim controls (set start/end points)
    - Rotate 90° buttons
    - Export pipeline with moviepy
    - Extended speed range (upgrade 4→8 speeds)
    
    REUSES from MediaLightbox:
    - self.video_player (QMediaPlayer) - Already initialized in viewer
    - self.video_widget (QVideoWidget) - Already created
    - self.audio_output (QAudioOutput) - Already exists
    - Existing playback controls (play/pause, seek, volume)
    """
    
    # ========== TRIM CONTROLS (Editor-Only) ==========
    
    def _create_video_trim_controls(self) -> QWidget:
        """Create trim controls (Set Start/End buttons). Reuses existing seek_slider from viewer."""
        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background: rgba(0, 0, 0, 0.85);
                border-radius: 8px;
            }
        """)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(10)
        
        # Header
        trim_label = QLabel("✂️ Trim")
        trim_label.setStyleSheet("color: white; font-weight: bold; font-size: 10pt;")
        outer.addWidget(trim_label)
        
        # Row: frame navigation
        row_nav = QHBoxLayout()
        prev_frame_btn = QPushButton("◀")
        prev_frame_btn.setToolTip("Previous frame (or use ← key)")
        prev_frame_btn.clicked.connect(self._previous_frame)
        prev_frame_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        row_nav.addWidget(prev_frame_btn)
        
        next_frame_btn = QPushButton("▶")
        next_frame_btn.setToolTip("Next frame (or use → key)")
        next_frame_btn.clicked.connect(self._next_frame)
        next_frame_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        row_nav.addWidget(next_frame_btn)
        outer.addLayout(row_nav)
        
        # Row: set start
        row_start = QHBoxLayout()
        self.trim_start_btn = QPushButton("[ Set Start")
        self.trim_start_btn.clicked.connect(self._set_trim_start)
        self.trim_start_btn.setToolTip("Set Trim Start (Shortcut: I)")
        self.trim_start_btn.setStyleSheet("""
            QPushButton {
                background: rgba(76, 175, 80, 0.8);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(76, 175, 80, 1.0);
            }
        """)
        row_start.addWidget(self.trim_start_btn)
        self.trim_start_label = QLabel("00:00")
        self.trim_start_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 10pt;")
        row_start.addWidget(self.trim_start_label)
        row_start.addStretch()
        outer.addLayout(row_start)
        
        # Row: set end
        row_end = QHBoxLayout()
        self.trim_end_btn = QPushButton("Set End ]")
        self.trim_end_btn.clicked.connect(self._set_trim_end)
        self.trim_end_btn.setToolTip("Set Trim End (Shortcut: O)")
        self.trim_end_btn.setStyleSheet("""
            QPushButton {
                background: rgba(244, 67, 54, 0.8);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(244, 67, 54, 1.0);
            }
        """)
        self.trim_end_label = QLabel("00:00")
        self.trim_end_label.setStyleSheet("color: #F44336; font-weight: bold; font-size: 10pt;")
        row_end.addWidget(self.trim_end_btn)
        row_end.addWidget(self.trim_end_label)
        row_end.addStretch()
        outer.addLayout(row_end)
        
        # Row: reset + preview
        row_actions = QHBoxLayout()
        reset_trim_btn = QPushButton("↺ Reset")
        reset_trim_btn.clicked.connect(self._reset_trim)
        reset_trim_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        row_actions.addWidget(reset_trim_btn)
        preview_trim_btn = QPushButton("▶ Preview Trim")
        preview_trim_btn.setToolTip("Play only the trimmed region")
        preview_trim_btn.clicked.connect(self._preview_trim)
        preview_trim_btn.setStyleSheet("""
            QPushButton {
                background: rgba(66, 133, 244, 0.8);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(66, 133, 244, 1.0);
            }
        """)
        row_actions.addWidget(preview_trim_btn)
        row_actions.addStretch()
        outer.addLayout(row_actions)
        
        # Row: duration and warning
        self.trim_duration_label = QLabel("Duration: 00:00 / 00:00")
        self.trim_duration_label.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 10pt;")
        self.trim_duration_label.setToolTip("Trimmed length / Original length")
        outer.addWidget(self.trim_duration_label)
        
        self.trim_warning_label = QLabel("⚠️ Invalid trim range!")
        self.trim_warning_label.setStyleSheet("""
            QLabel {
                color: #FF5252;
                font-weight: bold;
                font-size: 10pt;
                background: rgba(255, 82, 82, 0.2);
                border-radius: 4px;
                padding: 4px 8px;
            }
        """)
        self.trim_warning_label.setToolTip("Start time must be before end time")
        self.trim_warning_label.hide()
        outer.addWidget(self.trim_warning_label)
        
        return container
    
    # ========== ROTATE CONTROLS (Editor-Only) ==========
    
    def _create_video_rotate_controls(self) -> QWidget:
        """Create rotate buttons for video with status label."""
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        
        # Row: rotation buttons
        row_rotate = QHBoxLayout()
        rotate_left_btn = QPushButton("↶ 90°")
        rotate_left_btn.setToolTip("Rotate 90° Left (Counterclockwise)\nNote: Rotation applies during export, not in preview")
        rotate_left_btn.clicked.connect(lambda: self._rotate_video(-90))
        rotate_left_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        row_rotate.addWidget(rotate_left_btn)
        
        rotate_right_btn = QPushButton("↷ 90°")
        rotate_right_btn.setToolTip("Rotate 90° Right (Clockwise)\nNote: Rotation applies during export, not in preview")
        rotate_right_btn.clicked.connect(lambda: self._rotate_video(90))
        rotate_right_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        row_rotate.addWidget(rotate_right_btn)
        outer.addLayout(row_rotate)
        
        # Row: rotation status
        self.rotation_status_label = QLabel("Original")
        self.rotation_status_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 10pt;
                font-weight: bold;
                background: rgba(66, 133, 244, 0.8);
                border-radius: 4px;
                padding: 8px 16px;
            }
        """)
        outer.addWidget(self.rotation_status_label)
        
        # Row: export quality
        row_quality = QHBoxLayout()
        quality_label = QLabel("Quality:")
        quality_label.setStyleSheet("color: white; font-size: 10pt; font-weight: bold;")
        row_quality.addWidget(quality_label)
        self.export_quality_combo = QComboBox()
        self.export_quality_combo.addItems(["High (Original)", "Medium (Balanced)", "Low (Small File)"])
        self.export_quality_combo.setCurrentIndex(0)
        self.export_quality_combo.setToolTip("Select export quality/file size")
        self.export_quality_combo.setStyleSheet("""
            QComboBox {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 10pt;
            }
            QComboBox:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid white;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background: rgba(40, 40, 40, 0.95);
                color: white;
                selection-background-color: rgba(66, 133, 244, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
        """)
        row_quality.addWidget(self.export_quality_combo)
        row_quality.addStretch()
        outer.addLayout(row_quality)
        
        # Row: export speed
        row_speed = QHBoxLayout()
        speed_label = QLabel("Speed:")
        speed_label.setStyleSheet("color: white; font-size: 10pt; font-weight: bold;")
        row_speed.addWidget(speed_label)
        self.export_speed_combo = QComboBox()
        self.export_speed_combo.addItems(["0.5x (Slow)", "1.0x (Normal)", "1.5x (Fast)", "2.0x (Very Fast)"])
        self.export_speed_combo.setCurrentIndex(1)
        self.export_speed_combo.setToolTip("Playback speed (applies to export)")
        self.export_speed_combo.setStyleSheet("""
            QComboBox {
                background: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 10pt;
            }
            QComboBox:hover { background: rgba(255, 255, 255, 0.25); }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid white;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background: rgba(40, 40, 40, 0.95);
                color: white;
                selection-background-color: rgba(66, 133, 244, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
        """)
        row_speed.addWidget(self.export_speed_combo)
        row_speed.addStretch()
        outer.addLayout(row_speed)
        
        # Row: audio mute
        self.mute_audio_checkbox = QCheckBox("Mute Audio")
        self.mute_audio_checkbox.setStyleSheet("""
            QCheckBox {
                color: white;
                font-size: 10pt;
                font-weight: bold;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid rgba(255, 255, 255, 0.5);
                border-radius: 3px;
                background: rgba(255, 255, 255, 0.1);
            }
            QCheckBox::indicator:checked {
                background: rgba(66, 133, 244, 0.8);
                border-color: rgba(66, 133, 244, 1.0);
            }
        """)
        self.mute_audio_checkbox.setToolTip("Remove audio from exported video")
        outer.addWidget(self.mute_audio_checkbox)
        
        return container
    
    # ========== TRIM/ROTATE/EXPORT METHODS (Editor-Only) ==========
    
    def _set_trim_start(self):
        """Set trim start point to current position. REUSES existing video_player."""
        try:
            if not hasattr(self, 'video_player') or not self.video_player:
                print("[VideoEditor] ⚠️ Cannot set trim start: video_player not available")
                return

            self.video_trim_start = self.video_player.position()

            # Phase 3: Auto-adjust trim end if start is beyond current end
            if self.video_trim_start >= self.video_trim_end:
                duration = getattr(self, '_video_duration', 0)
                self.video_trim_end = duration
                if hasattr(self, 'trim_end_label'):
                    self.trim_end_label.setText(self._format_time(self.video_trim_end))
                print(f"[VideoEditor] Auto-adjusted trim end to full duration")

            # Update label if it exists
            if hasattr(self, 'trim_start_label'):
                self.trim_start_label.setText(self._format_time(self.video_trim_start))

            print(f"[VideoEditor] Trim start: {self._format_time(self.video_trim_start)}")

            # Update visual trim markers on seek slider
            if hasattr(self, 'seek_slider') and hasattr(self.seek_slider, 'set_trim_markers'):
                duration = getattr(self, '_video_duration', 0)
                self.seek_slider.set_trim_markers(self.video_trim_start, self.video_trim_end, duration)

            # Phase 3: Update duration display and validation
            self._update_trim_duration()
            self._validate_trim_range()
        except Exception as e:
            print(f"[VideoEditor] ⚠️ Error setting trim start: {e}")
            import traceback
            traceback.print_exc()

    def _set_trim_end(self):
        """Set trim end point to current position. REUSES existing video_player."""
        try:
            if not hasattr(self, 'video_player') or not self.video_player:
                print("[VideoEditor] ⚠️ Cannot set trim end: video_player not available")
                return

            self.video_trim_end = self.video_player.position()

            # Phase 3: Auto-adjust trim start if end is before current start
            if self.video_trim_end <= self.video_trim_start:
                self.video_trim_start = 0
                if hasattr(self, 'trim_start_label'):
                    self.trim_start_label.setText("00:00")
                print(f"[VideoEditor] Auto-adjusted trim start to beginning")

            # Update label if it exists
            if hasattr(self, 'trim_end_label'):
                self.trim_end_label.setText(self._format_time(self.video_trim_end))

            print(f"[VideoEditor] Trim end: {self._format_time(self.video_trim_end)}")

            # Update visual trim markers on seek slider
            if hasattr(self, 'seek_slider') and hasattr(self.seek_slider, 'set_trim_markers'):
                duration = getattr(self, '_video_duration', 0)
                self.seek_slider.set_trim_markers(self.video_trim_start, self.video_trim_end, duration)

            # Phase 3: Update duration display and validation
            self._update_trim_duration()
            self._validate_trim_range()
        except Exception as e:
            print(f"[VideoEditor] ⚠️ Error setting trim end: {e}")
            import traceback
            traceback.print_exc()
    
    def _reset_trim(self):
        """Reset trim points to full video duration."""
        if not hasattr(self, 'video_player') or not self.video_player:
            return

        self.video_trim_start = 0
        # Get duration from existing player (stored in MediaLightbox as _video_duration)
        duration = getattr(self, '_video_duration', 0)
        self.video_trim_end = duration

        self.trim_start_label.setText("00:00")
        self.trim_end_label.setText(self._format_time(duration))
        print(f"[VideoEditor] Trim reset to full duration: {self._format_time(duration)}")

        # Clear visual trim markers (or set to full range)
        if hasattr(self, 'seek_slider') and hasattr(self.seek_slider, 'clear_trim_markers'):
            self.seek_slider.clear_trim_markers()

    def _preview_trim(self):
        """Preview only the trimmed region (Phase 2 Feature 5)."""
        if not hasattr(self, 'video_player') or not self.video_player:
            return

        # Validate trim points
        if self.video_trim_start >= self.video_trim_end:
            print("[VideoEditor] Cannot preview: trim start >= trim end")
            return

        # Seek to trim start
        self.video_player.setPosition(self.video_trim_start)

        # Start playback
        self.video_player.play()
        print(f"[VideoEditor] Previewing trim: {self._format_time(self.video_trim_start)} - {self._format_time(self.video_trim_end)}")

        # Create monitor timer if not exists
        if not hasattr(self, '_trim_preview_timer'):
            self._trim_preview_timer = QTimer(self)
            self._trim_preview_timer.timeout.connect(self._check_trim_preview_position)

        # Start monitoring (check every 100ms)
        self._trim_preview_timer.start(100)

    def _check_trim_preview_position(self):
        """Monitor video position during trim preview and stop at trim end."""
        if not hasattr(self, 'video_player') or not self.video_player:
            if hasattr(self, '_trim_preview_timer'):
                self._trim_preview_timer.stop()
            return

        current_pos = self.video_player.position()

        # Stop if reached trim end (with 100ms tolerance)
        if current_pos >= self.video_trim_end - 100:
            self.video_player.pause()
            # Seek back to trim start for easy replay
            self.video_player.setPosition(self.video_trim_start)
            if hasattr(self, '_trim_preview_timer'):
                self._trim_preview_timer.stop()
            print(f"[VideoEditor] Trim preview complete")

    def _previous_frame(self):
        """Go to previous frame (Phase 2 Feature 6)."""
        if not hasattr(self, 'video_player') or not self.video_player:
            return

        current_pos = self.video_player.position()
        frame_ms = 1000 / 30  # Assume 30 fps (~33ms per frame)
        new_pos = max(0, current_pos - frame_ms)
        self.video_player.setPosition(int(new_pos))
        print(f"[VideoEditor] Previous frame: {self._format_time(int(new_pos))}")

    def _next_frame(self):
        """Go to next frame (Phase 2 Feature 6)."""
        if not hasattr(self, 'video_player') or not self.video_player:
            return

        current_pos = self.video_player.position()
        duration = getattr(self, '_video_duration', 0)
        frame_ms = 1000 / 30  # Assume 30 fps (~33ms per frame)
        new_pos = min(duration, current_pos + frame_ms)
        self.video_player.setPosition(int(new_pos))
        print(f"[VideoEditor] Next frame: {self._format_time(int(new_pos))}")

    def _rotate_video(self, degrees):
        """Rotate video by degrees (90, -90). Visual rotation applied during export."""
        self.video_rotation_angle = (self.video_rotation_angle + degrees) % 360
        print(f"[VideoEditor] Rotation: {self.video_rotation_angle}°")

        # Update rotation status label
        if hasattr(self, 'rotation_status_label'):
            if self.video_rotation_angle == 0:
                label_text = "Original"
            elif self.video_rotation_angle == 90:
                label_text = "↷ 90° (Clockwise)"
            elif self.video_rotation_angle == 180:
                label_text = "↕ 180° (Upside Down)"
            elif self.video_rotation_angle == 270:
                label_text = "↶ 90° (Counterclockwise)"
            else:
                label_text = f"{self.video_rotation_angle}°"

            self.rotation_status_label.setText(label_text)
            print(f"[VideoEditor] Rotation status: {label_text}")

        # Note: QVideoWidget doesn't support rotation - applied during export
        # Apply preview rotation when using QGraphicsVideoItem
        if hasattr(self, '_apply_preview_rotation'):
            self._apply_preview_rotation()
    
    def _format_time(self, milliseconds):
        """Format time from milliseconds to MM:SS."""
        if milliseconds <= 0:
            return "00:00"
        seconds = milliseconds // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _apply_preview_rotation(self):
        """Apply current rotation to preview if QGraphicsVideoItem is used."""
        try:
            if not hasattr(self, 'video_item') or self.video_item is None:
                return
            angle = getattr(self, 'video_rotation_angle', 0)
            br = self.video_item.boundingRect()
            self.video_item.setTransformOriginPoint(br.center())
            self.video_item.setRotation(angle)
            if hasattr(self, 'video_graphics_view') and hasattr(self, 'video_scene') and self.video_graphics_view and self.video_scene:
                rect = self.video_scene.itemsBoundingRect()
                if rect.isValid():
                    from PySide6.QtWidgets import QGraphicsView
                    # Fit using center anchor to avoid drift, then restore under-mouse for interactive zoom
                    prev_trans = self.video_graphics_view.transformationAnchor()
                    prev_resize = self.video_graphics_view.resizeAnchor()
                    self.video_graphics_view.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
                    self.video_graphics_view.setResizeAnchor(QGraphicsView.AnchorViewCenter)
                    self.video_graphics_view.resetTransform()
                    self.video_graphics_view.fitInView(rect, Qt.KeepAspectRatio)
                    # Record base fit scale for consistent zooming
                    try:
                        self.video_base_scale = self.video_graphics_view.transform().m11()
                    except Exception:
                        self.video_base_scale = 1.0
                    self.edit_zoom_level = 1.0
                    self.video_graphics_view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
                    self.video_graphics_view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        except Exception as e:
            print(f"[VideoEditor] Preview rotation apply failed: {e}")

    def _fit_video_view(self):
        """Fit video content to the view at 100% initial scale."""
        try:
            if hasattr(self, 'video_graphics_view') and hasattr(self, 'video_scene') and self.video_graphics_view and self.video_scene:
                rect = self.video_scene.itemsBoundingRect()
                rect = self.video_scene.itemsBoundingRect()
                if rect.isValid():
                    from PySide6.QtWidgets import QGraphicsView
                    # Fit using center anchor to avoid drift, then restore under-mouse for interactive zoom
                    prev_trans = self.video_graphics_view.transformationAnchor()
                    prev_resize = self.video_graphics_view.resizeAnchor()
                    self.video_graphics_view.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
                    self.video_graphics_view.setResizeAnchor(QGraphicsView.AnchorViewCenter)
                    self.video_graphics_view.resetTransform()
                    self.video_graphics_view.fitInView(rect, Qt.KeepAspectRatio)
                    # Record base fit scale for consistent zooming
                    try:
                        self.video_base_scale = self.video_graphics_view.transform().m11()
                    except Exception:
                        self.video_base_scale = 1.0
                    self.edit_zoom_level = 1.0
                    self.video_graphics_view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
                    self.video_graphics_view.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        except Exception as e:
            print(f"[VideoEditor] Fit video view failed: {e}")

    # ========== ZOOM & EDITOR ACTIONS ==========
    def _apply_video_zoom(self):
        """Apply zoom to video preview using QGraphicsVideoItem/QGraphicsView."""
        try:
            if not hasattr(self, 'video_item') or self.video_item is None:
                return
            # Clamp scale
            self.edit_zoom_level = max(0.25, min(getattr(self, 'edit_zoom_level', 1.0), 4.0))
            # Origin at center prevents drift
            br = self.video_item.boundingRect()
            self.video_item.setTransformOriginPoint(br.center())
            # Use view transform for zoom to support AnchorUnderMouse wheel zoom
            if hasattr(self, 'video_graphics_view') and self.video_graphics_view:
                self.video_graphics_view.resetTransform()
                from PySide6.QtGui import QTransform
                base = getattr(self, 'video_base_scale', 1.0)
                t = QTransform()
                t.scale(base * self.edit_zoom_level, base * self.edit_zoom_level)
                self.video_graphics_view.setTransform(t)
                from PySide6.QtWidgets import QGraphicsView
                self.video_graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)
        except Exception as e:
            print(f"[VideoEditor] Apply video zoom failed: {e}")

    def _editor_zoom_in(self):
        self.edit_zoom_level = getattr(self, 'edit_zoom_level', 1.0) * 1.15
        self._apply_video_zoom()
        print(f"[VideoEditor] Zoom In → {self.edit_zoom_level:.2f}")

    def _editor_zoom_out(self):
        self.edit_zoom_level = getattr(self, 'edit_zoom_level', 1.0) / 1.15
        self._apply_video_zoom()
        print(f"[VideoEditor] Zoom Out → {self.edit_zoom_level:.2f}")

    def _editor_zoom_reset(self):
        self.edit_zoom_level = 1.0
        self._apply_video_zoom()
        print("[VideoEditor] Zoom Reset → 1.00")

    def _toggle_crop_mode(self):
        self.crop_mode_active = not getattr(self, 'crop_mode_active', False)
        print(f"[VideoEditor] Crop mode: {'ON' if self.crop_mode_active else 'OFF'}")

    def _toggle_filters_panel(self):
        self.filters_panel_visible = not getattr(self, 'filters_panel_visible', False)
        print(f"[VideoEditor] Filters panel: {'Visible' if self.filters_panel_visible else 'Hidden'}")

    def _toggle_before_after(self):
        self.before_after_active = not getattr(self, 'before_after_active', False)
        print(f"[VideoEditor] Before/After: {'ON' if self.before_after_active else 'OFF'}")

    def _editor_undo(self):
        print("[VideoEditor] Undo clicked (not yet implemented for video adjustments)")

    def _editor_redo(self):
        print("[VideoEditor] Redo clicked (not yet implemented for video adjustments)")

    def _copy_adjustments(self):
        try:
            self.copied_adjustments = getattr(self, 'adjustments', {}).copy()
            print("[VideoEditor] Adjustments copied")
        except Exception as e:
            print(f"[VideoEditor] Copy adjustments failed: {e}")

    def _paste_adjustments(self):
        try:
            if hasattr(self, 'copied_adjustments') and self.copied_adjustments:
                self.adjustments = self.copied_adjustments.copy()
                print("[VideoEditor] Adjustments pasted")
            else:
                print("[VideoEditor] No adjustments to paste")
        except Exception as e:
            print(f"[VideoEditor] Paste adjustments failed: {e}")

    def eventFilter(self, obj, event):
        """Mouse-wheel zoom for videos: scale around cursor using view transform."""
        try:
            from PySide6.QtCore import QEvent, Qt
            # Intercept wheel on the video view (Ctrl+Wheel to zoom)
            if hasattr(self, 'video_graphics_view') and (obj == self.video_graphics_view.viewport() or obj == self.video_graphics_view) and event.type() == QEvent.Wheel:
                # Zoom only when Ctrl is pressed
                if not (event.modifiers() & Qt.ControlModifier):
                    return True
                delta = event.angleDelta().y()
                requested_factor = 1.15 if delta > 0 else 1/1.15
                current = getattr(self, 'edit_zoom_level', 1.0)
                target = max(0.25, min(current * requested_factor, 4.0))
                # Compute effective factor to respect bounds
                effective_factor = target / (current if current != 0 else 1.0)
                if hasattr(self, 'video_graphics_view') and self.video_graphics_view:
                    self.video_graphics_view.scale(effective_factor, effective_factor)
                self.edit_zoom_level = target
                try:
                    from PySide6.QtWidgets import QGraphicsView
                    self.video_graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)
                except Exception:
                    pass
                if hasattr(self, '_update_zoom_status'):
                    self._update_zoom_status()
                return True
            # Swallow wheel on parent scroll area to prevent unintended scrolling
            if hasattr(self, 'scroll_area') and obj == self.scroll_area.viewport() and event.type() == QEvent.Wheel:
                return True
        except Exception as e:
            print(f"[VideoEditor] Wheel zoom failed: {e}")
        return super().eventFilter(obj, event)

    # ========== PHASE 3: VALIDATION & FEEDBACK METHODS ==========


    def _update_trim_duration(self):
        """Update trim duration display showing trimmed length vs original (Phase 3)."""
        try:
            if not hasattr(self, 'trim_duration_label'):
                return

            duration = getattr(self, '_video_duration', 0)
            trim_length = max(0, self.video_trim_end - self.video_trim_start)

            trimmed_time = self._format_time(trim_length)
            original_time = self._format_time(duration)

            self.trim_duration_label.setText(f"Duration: {trimmed_time} / {original_time}")
            print(f"[VideoEditor] Duration: {trimmed_time} trimmed / {original_time} original")
        except Exception as e:
            print(f"[VideoEditor] Error updating duration: {e}")

    def _validate_trim_range(self):
        """Validate trim range and show/hide warning (Phase 3)."""
        try:
            if not hasattr(self, 'trim_warning_label'):
                return

            # Check if trim range is valid
            is_valid = self.video_trim_start < self.video_trim_end

            if is_valid:
                self.trim_warning_label.hide()
            else:
                self.trim_warning_label.show()
                print(f"[VideoEditor] ⚠️ Invalid trim range: start={self._format_time(self.video_trim_start)}, end={self._format_time(self.video_trim_end)}")

            return is_valid
        except Exception as e:
            print(f"[VideoEditor] Error validating trim: {e}")
            return True  # Assume valid on error

    def _estimate_output_size(self, input_path, trim_start_ms, trim_end_ms, quality_preset):
        """Estimate output file size based on input and settings (Phase 3)."""
        try:
            # Get input file size
            input_size_bytes = os.path.getsize(input_path)
            input_size_mb = input_size_bytes / (1024 * 1024)

            # Calculate trim ratio
            duration = getattr(self, '_video_duration', 0)
            if duration > 0:
                trim_length = trim_end_ms - trim_start_ms
                trim_ratio = trim_length / duration
            else:
                trim_ratio = 1.0

            # Quality multipliers (approximate)
            quality_multipliers = {
                0: 1.0,   # High: ~same size
                1: 0.4,   # Medium: ~40% of original
                2: 0.15   # Low: ~15% of original
            }

            multiplier = quality_multipliers.get(quality_preset, 1.0)

            # Estimate output size
            estimated_mb = input_size_mb * trim_ratio * multiplier

            return estimated_mb
        except Exception as e:
            print(f"[VideoEditor] Error estimating file size: {e}")
            return None

    def _check_disk_space(self, output_path, estimated_size_mb):
        """Check if there's enough disk space for export (Phase 3)."""
        try:
            # Get disk space for output directory
            output_dir = os.path.dirname(os.path.abspath(output_path))
            stat = shutil.disk_usage(output_dir)
            free_space_mb = stat.free / (1024 * 1024)

            # Add 10% safety margin
            required_mb = estimated_size_mb * 1.1

            has_space = free_space_mb >= required_mb

            print(f"[VideoEditor] Disk space check: {free_space_mb:.1f} MB available, {required_mb:.1f} MB required")

            return has_space, free_space_mb, required_mb
        except Exception as e:
            print(f"[VideoEditor] Error checking disk space: {e}")
            return True, 0, 0  # Assume OK on error

    # ========== EXPORT PIPELINE (Editor-Only) ==========
    
    def _export_edited_video(self):
        """Show export dialog and export video with all edits (trim, rotate, speed)."""
        try:
            # Phase 3: Pre-export validation
            if not self._validate_trim_range():
                QMessageBox.warning(
                    self,
                    "Invalid Trim Range",
                    "Trim start must be before trim end.\n\nPlease adjust your trim markers."
                )
                return

            # Phase 3: Estimate output file size
            quality_index = 0
            if hasattr(self, 'export_quality_combo'):
                quality_index = self.export_quality_combo.currentIndex()

            estimated_size = self._estimate_output_size(
                self.media_path,
                self.video_trim_start,
                self.video_trim_end,
                quality_index
            )

            # Show file size estimate to user
            if estimated_size:
                size_info = f"\n\nEstimated output size: ~{estimated_size:.1f} MB"
            else:
                size_info = ""

            # Get output path from user
            default_name = os.path.splitext(os.path.basename(self.media_path))[0] + "_edited.mp4"
            initial_path = os.path.join(os.path.dirname(self.media_path), default_name)
            output_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Edited Video" + size_info,
                initial_path,
                "MP4 Video (*.mp4);;All Files (*)"
            )

            if not output_path:
                return  # User cancelled

            # Overwrite confirmation if file exists
            if os.path.exists(output_path):
                reply = QMessageBox.question(
                    self,
                    "Overwrite File",
                    f"The file already exists:\n\n{output_path}\n\nDo you want to overwrite it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return

            # Phase 3: Check disk space
            if estimated_size:
                has_space, free_mb, required_mb = self._check_disk_space(output_path, estimated_size)
                if not has_space:
                    reply = QMessageBox.warning(
                        self,
                        "Low Disk Space",
                        f"Warning: Low disk space!\n\n"
                        f"Available: {free_mb:.1f} MB\n"
                        f"Required: ~{required_mb:.1f} MB\n\n"
                        f"Continue anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return

            # Perform export
            success = self._export_video_with_edits(output_path)

            if success:
                # Show actual output file size
                actual_size_mb = os.path.getsize(output_path) / (1024 * 1024)
                QMessageBox.information(
                    self,
                    "Export Successful",
                    f"Video exported successfully!\n\n"
                    f"Location: {output_path}\n"
                    f"File size: {actual_size_mb:.1f} MB"
                )

                # Offer to open containing folder
                open_reply = QMessageBox.question(
                    self,
                    "Open Folder",
                    "Open the exported video's folder?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if open_reply == QMessageBox.Yes:
                    try:
                        import platform, subprocess
                        system = platform.system()
                        folder = os.path.dirname(output_path)
                        if system == "Windows":
                            subprocess.run(['explorer', os.path.normpath(folder)])
                        elif system == "Darwin":
                            subprocess.run(['open', folder])
                        else:
                            subprocess.run(['xdg-open', folder])
                    except Exception as e:
                        print(f"[VideoEditor] Failed to open folder: {e}")
            else:
                QMessageBox.warning(
                    self,
                    "Export Failed",
                    "Failed to export video. Check console for errors."
                )

        except Exception as e:
            import traceback
            print(f"[VideoEditor] Error in export dialog: {e}")
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Export error: {e}")
    
    def _export_video_with_edits(self, output_path):
        """Export video with all edits applied (trim, rotate). REUSES existing video state."""
        progress_dialog = None
        try:
            print(f"[VideoEditor] Exporting video to: {output_path}")

            # Check if moviepy is available
            try:
                from moviepy.editor import VideoFileClip
            except ImportError:
                print("[VideoEditor] moviepy not available - install with: pip install moviepy")
                QMessageBox.warning(
                    self,
                    "Missing Dependency",
                    "moviepy library not installed.\n\nInstall with: pip install moviepy"
                )
                return False

            # Use self.media_path (existing video file)
            if not hasattr(self, 'media_path') or not self.media_path:
                print("[VideoEditor] No video loaded")
                return False

            # Load video with moviepy
            clip = VideoFileClip(self.media_path)

            # Apply trim (if set)
            duration_ms = getattr(self, '_video_duration', clip.duration * 1000)
            if self.video_trim_start > 0 or self.video_trim_end < duration_ms:
                start_sec = self.video_trim_start / 1000.0
                end_sec = self.video_trim_end / 1000.0 if self.video_trim_end > 0 else clip.duration
                clip = clip.subclip(start_sec, end_sec)
                print(f"[VideoEditor] Trimmed: {start_sec:.2f}s - {end_sec:.2f}s")

            # Apply rotation
            if self.video_rotation_angle != 0:
                if self.video_rotation_angle == 90:
                    clip = clip.rotate(90)
                elif self.video_rotation_angle == 180:
                    clip = clip.rotate(180)
                elif self.video_rotation_angle == 270:
                    clip = clip.rotate(270)
                print(f"[VideoEditor] Rotated: {self.video_rotation_angle}°")

            # Phase 3: Apply speed change
            speed_index = 1  # Default to 1.0x
            if hasattr(self, 'export_speed_combo'):
                speed_index = self.export_speed_combo.currentIndex()

            speed_factors = [0.5, 1.0, 1.5, 2.0]
            speed_factor = speed_factors[speed_index]

            if speed_factor != 1.0:
                clip = clip.fx(lambda c: c.speedx(speed_factor))
                print(f"[VideoEditor] Speed: {speed_factor}x")

            # Phase 3: Handle audio muting
            mute_audio = False
            if hasattr(self, 'mute_audio_checkbox'):
                mute_audio = self.mute_audio_checkbox.isChecked()

            if mute_audio:
                clip = clip.without_audio()
                print(f"[VideoEditor] Audio muted")

            # Create progress dialog
            progress_dialog = QProgressDialog("Initializing export...", "Cancel", 0, 100, self)
            progress_dialog.setWindowTitle("Exporting Video")
            progress_dialog.setWindowModality(Qt.WindowModal)
            progress_dialog.setMinimumDuration(0)  # Show immediately
            progress_dialog.setValue(0)
            progress_dialog.show()
            QApplication.processEvents()

            # Track if user cancelled
            export_cancelled = False

            # Custom progress logger for moviepy
            class QtProgressLogger:
                def __init__(self, progress_dialog, duration):
                    self.progress_dialog = progress_dialog
                    self.duration = duration
                    self.last_progress = 0

                def __call__(self, message):
                    """Called by moviepy with progress messages."""
                    # Check if user cancelled
                    if self.progress_dialog.wasCanceled():
                        nonlocal export_cancelled
                        export_cancelled = True
                        raise Exception("Export cancelled by user")

                    # Parse moviepy progress message (format: "t: 1.23s")
                    if message.startswith('t:'):
                        try:
                            # Extract time value
                            time_str = message.split(':')[1].strip().rstrip('s')
                            current_time = float(time_str)

                            # Calculate percentage
                            progress = int((current_time / self.duration) * 100)
                            progress = min(progress, 100)  # Cap at 100%

                            # Update dialog only if progress changed significantly
                            if progress > self.last_progress:
                                self.last_progress = progress
                                self.progress_dialog.setValue(progress)

                                # Calculate time remaining (rough estimate)
                                if progress > 0:
                                    elapsed_per_percent = current_time / progress
                                    remaining_time = elapsed_per_percent * (100 - progress)
                                    remaining_mins = int(remaining_time // 60)
                                    remaining_secs = int(remaining_time % 60)

                                    self.progress_dialog.setLabelText(
                                        f"Exporting video: {progress}%\n"
                                        f"Time remaining: {remaining_mins:02d}:{remaining_secs:02d}"
                                    )
                                else:
                                    self.progress_dialog.setLabelText(f"Exporting video: {progress}%")

                                QApplication.processEvents()
                        except (ValueError, IndexError):
                            # Ignore malformed messages
                            pass

            # Create progress logger
            logger = QtProgressLogger(progress_dialog, clip.duration)

            # Get quality preset settings (Phase 2 Feature 7)
            quality_index = 0  # Default to High
            if hasattr(self, 'export_quality_combo'):
                quality_index = self.export_quality_combo.currentIndex()

            # Quality presets: [bitrate, preset, fps]
            quality_presets = {
                0: {'bitrate': None, 'preset': 'medium', 'fps': None},  # High: Original quality
                1: {'bitrate': '2000k', 'preset': 'fast', 'fps': None},  # Medium: 2 Mbps, faster encode
                2: {'bitrate': '500k', 'preset': 'faster', 'fps': 24}   # Low: 500 Kbps, reduce fps
            }

            preset = quality_presets.get(quality_index, quality_presets[0])
            print(f"[VideoEditor] Export quality: {['High', 'Medium', 'Low'][quality_index]} (bitrate={preset['bitrate']}, preset={preset['preset']})")

            # Build write_videofile parameters
            write_params = {
                'filename': output_path,
                'codec': 'libx264',
                'audio_codec': 'aac',
                'temp_audiofile': 'temp-audio.m4a',
                'remove_temp': True,
                'verbose': True,
                'logger': logger,
                'preset': preset['preset']
            }

            # Add optional parameters
            if preset['bitrate']:
                write_params['bitrate'] = preset['bitrate']
            if preset['fps']:
                write_params['fps'] = preset['fps']

            # Export video with progress tracking and quality preset
            clip.write_videofile(**write_params)

            # Close progress dialog
            if progress_dialog:
                progress_dialog.setValue(100)
                progress_dialog.close()

            # Cleanup
            clip.close()

            print(f"[VideoEditor] ✓ Video exported successfully!")
            return True

        except Exception as e:
            # Close progress dialog on error
            if progress_dialog:
                progress_dialog.close()

            # Don't show error if user cancelled
            if export_cancelled:
                print(f"[VideoEditor] Export cancelled by user")
                return False

            import traceback
            print(f"[VideoEditor] Error exporting video: {e}")
            traceback.print_exc()
            return False
