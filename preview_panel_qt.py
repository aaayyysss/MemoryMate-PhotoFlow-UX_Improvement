# preview_panel_qt.py
# Version 09.33.01.01 dated 2025.11.22
# 
# PHASE 1 PRO TOOLS: Enhanced professional photo viewer/editor
# - Microsoft Photos-style crop with presets (16:9, 4:3, 1:1, freeform)
# - Live histogram panel (RGB channels)
# - Before/After comparison (split-view + toggle)
# - Full undo/redo stack for all adjustments
# - 5-star rating system (saved to database)
# - Pick/Reject workflow flags (Lightroom-style)
#
# Photo-App/
# ├─ main_window_qt.py
# ├─ thumbnail_grid_qt.py
# ├─ thumb_cache_db.py
# ├─ settings_manager_qt.py
# ├─ preview_panel_qt.py   👈 Enhanced with Pro Tools
#  └─ assets/
#     └─ icons/
#
# Final Layout Hierarchy Recap
# QStackedWidget (mode_stack)
# ├─ viewer_page
# │  ├─ top_bar_viewer (arrows, edit, rotate, rating stars, pick/reject)
# │  ├─ image_area (shared)
# │  ├─ bottom_bar_viewer (zoom, info, tag_box, info button)
# │  └─ right_info_panel
# └─ editor_page
#   ├─ top_bar_editor_row1 (back, undo/redo)
#   ├─ top_bar_editor_row2 (crop presets, before/after, save options)
#   ├─ image_area (shared with split-view support)
#   ├─ bottom_bar_editor (context control slider)
#   └─ right_editor_panel (adjustments + histogram)
#

import os, time, subprocess
import numpy as np
from collections import deque

from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageQt, ExifTags, ImageDraw
from datetime import datetime

from PySide6.QtCore import (
    Qt, QParallelAnimationGroup, QPropertyAnimation,
    QEasingCurve, QEvent, QTimer, QSize,
    Signal, QRect, QPoint, QPointF, QUrl, Slot
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QVBoxLayout, QHBoxLayout, QStackedWidget, QGridLayout,
    QScrollArea, QSlider, QToolButton, QWidget, QPushButton, QMenu, QComboBox, QGraphicsDropShadowEffect,
    QTextEdit, QGraphicsOpacityEffect, QFileDialog, QMessageBox, QRubberBand, QFrame, QStyle, QSizePolicy, QLineEdit
)

from PySide6.QtGui import (
    QStandardItemModel, QStandardItem, QPixmap, QImage,
    QPainter, QPen, QBrush, QColor, QFont, QAction, QCursor,
    QIcon, QTransform, QDesktopServices, QPainterPath, QPolygonF
) 

from PySide6.QtSvg import QSvgRenderer

from reference_db import ReferenceDB
from translation_manager import tr




# ================================================================
# Histogram Widget (Live RGB histogram for professional editing)
# ================================================================
class HistogramWidget(QWidget):
    """
    Live RGB histogram display (Lightroom/Excire style).
    Shows separate R/G/B channel distributions with luminosity overlay.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(150)
        self.setMinimumWidth(200)
        self._hist_data = None  # (r_hist, g_hist, b_hist, lum_hist)
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
    
    def set_image(self, pil_image: Image.Image):
        """Calculate and display histogram for given PIL image."""
        if pil_image is None:
            self._hist_data = None
            self.update()
            return
        
        try:
            # Convert to RGB if needed
            if pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
            
            # Get image as numpy array
            img_array = np.array(pil_image)
            
            # Calculate histograms for each channel (256 bins, 0-255 range)
            r_hist, _ = np.histogram(img_array[:, :, 0], bins=256, range=(0, 256))
            g_hist, _ = np.histogram(img_array[:, :, 1], bins=256, range=(0, 256))
            b_hist, _ = np.histogram(img_array[:, :, 2], bins=256, range=(0, 256))
            
            # Calculate luminosity (weighted average: 0.299R + 0.587G + 0.114B)
            lum = (0.299 * img_array[:, :, 0] + 
                   0.587 * img_array[:, :, 1] + 
                   0.114 * img_array[:, :, 2]).astype(np.uint8)
            lum_hist, _ = np.histogram(lum, bins=256, range=(0, 256))
            
            self._hist_data = (r_hist, g_hist, b_hist, lum_hist)
            self.update()
        except Exception as e:
            print(f"[Histogram] Error: {e}")
            self._hist_data = None
            self.update()
    
    def paintEvent(self, event):
        if self._hist_data is None:
            # Draw empty state
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(26, 26, 26))
            p.setPen(QColor(100, 100, 100))
            p.drawText(self.rect(), Qt.AlignCenter, "No image")
            p.end()
            return
        
        r_hist, g_hist, b_hist, lum_hist = self._hist_data
        
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(26, 26, 26))
        
        # Normalize histograms to widget height
        max_val = max(r_hist.max(), g_hist.max(), b_hist.max(), 1)
        w = self.width()
        h = self.height() - 20  # Leave space for labels
        
        def draw_channel(hist, color, alpha=180):
            """Draw one channel histogram with given color."""
            pen = QPen(QColor(*color, alpha), 1)
            p.setPen(pen)
            brush = QBrush(QColor(*color, alpha // 3))
            p.setBrush(brush)
            
            # Build polygon path
            points = [QPointF(0, h)]  # Start at bottom-left
            for i in range(256):
                x = i * w / 256
                y_val = hist[i] / max_val * h
                y = h - y_val
                points.append(QPointF(x, y))
            points.append(QPointF(w, h))  # Close at bottom-right
            
            # Draw filled polygon
            from PySide6.QtGui import QPolygonF
            polygon = QPolygonF(points)
            p.drawPolygon(polygon)
        
        # Draw RGB channels (red, green, blue)
        draw_channel(r_hist, (255, 50, 50), alpha=150)
        draw_channel(g_hist, (50, 255, 50), alpha=150)
        draw_channel(b_hist, (50, 100, 255), alpha=150)
        
        # Draw luminosity as overlay (white)
        draw_channel(lum_hist, (200, 200, 200), alpha=100)
        
        # Draw grid lines
        p.setPen(QPen(QColor(50, 50, 50), 1, Qt.DashLine))
        for i in range(1, 4):
            y = i * h / 4
            p.drawLine(0, int(y), w, int(y))
        
        # Draw labels
        p.setPen(QColor(150, 150, 150))
        font = QFont("Segoe UI", 8)
        p.setFont(font)
        p.drawText(5, h + 15, "Shadows")
        p.drawText(w - 60, h + 15, "Highlights")
        
        p.end()


# ================================================================
# Rating Widget (5-star rating system)
# ================================================================
class RatingWidget(QWidget):
    """
    5-star rating widget (Lightroom-style).
    Click to set rating, keyboard 0-5 to rate.
    """
    ratingChanged = Signal(int)  # Emits rating (0-5)
    
    def __init__(self, rating=0, parent=None):
        super().__init__(parent)
        self._rating = rating
        self._hover_rating = -1
        self.setFixedHeight(24)
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to rate (0-5 stars)")
    
    def rating(self):
        return self._rating
    
    def setRating(self, rating):
        self._rating = max(0, min(5, rating))
        self.update()
        self.ratingChanged.emit(self._rating)
    
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        star_size = 20
        spacing = 4
        total_width = 5 * star_size + 4 * spacing
        start_x = (self.width() - total_width) // 2
        y = (self.height() - star_size) // 2
        
        # Determine display rating (hover or actual)
        display_rating = self._hover_rating if self._hover_rating >= 0 else self._rating
        
        for i in range(5):
            x = start_x + i * (star_size + spacing)
            filled = (i < display_rating)
            
            # Draw star
            if filled:
                p.setBrush(QColor(255, 200, 0))  # Gold
                p.setPen(QPen(QColor(200, 150, 0), 1))
            else:
                p.setBrush(QColor(80, 80, 80))  # Gray
                p.setPen(QPen(QColor(100, 100, 100), 1))
            
            # Star polygon (5-pointed)
            self._draw_star(p, x + star_size / 2, y + star_size / 2, star_size / 2)
        
        p.end()
    
    def _draw_star(self, painter, cx, cy, radius):
        """Draw a 5-pointed star centered at (cx, cy)."""
        import math
        points = []
        for i in range(10):
            angle = math.pi / 2 + i * math.pi / 5
            r = radius if i % 2 == 0 else radius * 0.4
            x = cx + r * math.cos(angle)
            y = cy - r * math.sin(angle)
            points.append(QPointF(x, y))
        
        from PySide6.QtGui import QPolygonF
        polygon = QPolygonF(points)
        painter.drawPolygon(polygon)
    
    def mouseMoveEvent(self, event):
        # Update hover rating based on mouse position
        star_size = 20
        spacing = 4
        total_width = 5 * star_size + 4 * spacing
        start_x = (self.width() - total_width) // 2
        
        x = event.pos().x()
        if x < start_x:
            self._hover_rating = 0
        elif x > start_x + total_width:
            self._hover_rating = 5
        else:
            self._hover_rating = min(5, max(1, int((x - start_x) / (star_size + spacing)) + 1))
        
        self.update()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setRating(self._hover_rating)
    
    def leaveEvent(self, event):
        self._hover_rating = -1
        self.update()


# ================================================================
# Edit History Stack (Undo/Redo support)
# ================================================================
class EditHistoryStack:
    """
    Manages undo/redo stack for image edits.
    Each state includes: adjustments dict + edit_base PIL image + crop state.
    """
    def __init__(self, max_history=50):
        self._undo_stack = deque(maxlen=max_history)
        self._redo_stack = deque(maxlen=max_history)
        self._max_history = max_history
    
    def push_state(self, state: dict):
        """
        Push current state to undo stack.
        State should contain: {'adjustments': dict, 'edit_base_pil': Image, 'description': str}
        """
        self._undo_stack.append(state.copy())
        self._redo_stack.clear()  # Clear redo stack when new edit is made
    
    def can_undo(self):
        return len(self._undo_stack) > 0
    
    def can_redo(self):
        return len(self._redo_stack) > 0
    
    def undo(self, current_state: dict):
        """
        Undo last edit. Returns previous state, or None if can't undo.
        Current state is pushed to redo stack.
        """
        if not self.can_undo():
            return None
        
        # Save current state to redo stack
        self._redo_stack.append(current_state.copy())
        
        # Pop and return previous state
        return self._undo_stack.pop()
    
    def redo(self, current_state: dict):
        """
        Redo last undone edit. Returns next state, or None if can't redo.
        Current state is pushed back to undo stack.
        """
        if not self.can_redo():
            return None
        
        # Save current state to undo stack
        self._undo_stack.append(current_state.copy())
        
        # Pop and return next state
        return self._redo_stack.pop()
    
    def clear(self):
        """Clear all history."""
        self._undo_stack.clear()
        self._redo_stack.clear()


# ================================================================
# Rotation Slider (Microsoft Photos style)
# ================================================================
class RotationSlider(QWidget):
    """
    Microsoft Photos-style rotation slider with degree scale.
    Shows horizontal slider with degree markers (-45° to +45°).
    Appears underneath photo in crop mode.
    """
    rotationChanged = Signal(float)  # Emits rotation angle in degrees
    rotate90LeftRequested = Signal()  # Rotate 90° counter-clockwise
    rotate90RightRequested = Signal()  # Rotate 90° clockwise
    flipHorizontalRequested = Signal()  # Mirror horizontally
    flipVerticalRequested = Signal()  # Mirror vertically
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rotation_angle = 0.0
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 15)  # More bottom margin to avoid cutoff
        layout.setSpacing(8)
        
        # Center container to limit slider width
        center_container = QWidget()
        center_container.setStyleSheet("background-color: transparent;")
        center_layout = QHBoxLayout(center_container)
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        # Add spacers to center the slider
        center_layout.addStretch(1)
        
        # Slider container with degree markers (limited width)
        slider_container = QWidget()
        slider_container.setFixedWidth(500)  # Limit width to match photo width
        slider_container.setStyleSheet("background-color: transparent;")
        slider_layout = QVBoxLayout(slider_container)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(4)
        
        # Slider (-45 to +45 degrees)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(-180)
        self.slider.setMaximum(180)
        self.slider.setValue(0)
        self.slider.setFixedHeight(30)
        self.slider.setStyleSheet("""
            QSlider {
                background: transparent;
            }
            QSlider::groove:horizontal {
                background: rgba(255, 255, 255, 0.3);
                height: 3px;
                border-radius: 1px;
                margin: 0px;
            }
            QSlider::handle:horizontal {
                background: white;
                border: 2px solid #888;
                width: 16px;
                height: 16px;
                margin: -7px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #f0f0f0;
                border: 2px solid #666;
            }
        """)
        self.slider.valueChanged.connect(self._on_slider_changed)
        slider_layout.addWidget(self.slider)
        
        # Degree scale with tick marks
        scale_container = QWidget()
        scale_container.setFixedHeight(15)
        scale_container.setStyleSheet("background-color: transparent;")
        scale_layout = QHBoxLayout(scale_container)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(0)
        
        # Add tick marks evenly distributed
        num_ticks = 13  # -180 to +180 every 30 degrees
        for i in range(num_ticks):
            if i > 0:
                scale_layout.addStretch(1)
            
            tick = QLabel("|")
            tick.setAlignment(Qt.AlignCenter)
            tick.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 10px; background: transparent;")
            scale_layout.addWidget(tick)
        
        slider_layout.addWidget(scale_container)
        
        # Angle label (centered below slider)
        self.angle_label = QLabel("0°")
        self.angle_label.setAlignment(Qt.AlignCenter)
        self.angle_label.setFixedHeight(20)
        self.angle_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 13px;
                font-weight: normal;
                background-color: transparent;
            }
        """)
        slider_layout.addWidget(self.angle_label)
        
        center_layout.addWidget(slider_container)
        center_layout.addStretch(1)
        
        layout.addWidget(center_container)
        
        # Rotation and flip buttons (with more spacing)
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(15)
        buttons_layout.setContentsMargins(0, 8, 0, 0)  # More top margin
        
        buttons_layout.addStretch(1)
        
        # Left side: Rotation buttons
        self.btn_rotate_left = self._create_icon_button("↶", "Rotate 90° left")
        self.btn_rotate_left.clicked.connect(self.rotate90LeftRequested.emit)
        buttons_layout.addWidget(self.btn_rotate_left)
        
        self.btn_rotate_right = self._create_icon_button("↷", "Rotate 90° right")
        self.btn_rotate_right.clicked.connect(self.rotate90RightRequested.emit)
        buttons_layout.addWidget(self.btn_rotate_right)
        
        buttons_layout.addStretch(2)
        
        # Right side: Flip buttons
        self.btn_flip_horizontal = self._create_icon_button("⇄", "Flip horizontal")
        self.btn_flip_horizontal.clicked.connect(self.flipHorizontalRequested.emit)
        buttons_layout.addWidget(self.btn_flip_horizontal)
        
        self.btn_flip_vertical = self._create_icon_button("⇅", "Flip vertical")
        self.btn_flip_vertical.clicked.connect(self.flipVerticalRequested.emit)
        buttons_layout.addWidget(self.btn_flip_vertical)
        
        buttons_layout.addStretch(1)
        
        layout.addLayout(buttons_layout)
        
        self.setFixedHeight(125)  # Slightly taller to accommodate margins
        self.setStyleSheet("background-color: #000000; border-top: 1px solid #444;")
    
    def _create_icon_button(self, icon_text: str, tooltip: str) -> QPushButton:
        """Create a round icon button (dark mode style)."""
        btn = QPushButton(icon_text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(40, 40)
        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.15);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.5);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.35);
            }
        """)
        return btn
    
    def _on_slider_changed(self, value: int):
        self._rotation_angle = float(value)
        self.angle_label.setText(f"{value}°")
        self.rotationChanged.emit(self._rotation_angle)
    
    def get_rotation(self) -> float:
        return self._rotation_angle
    
    def reset(self):
        self.slider.setValue(0)


# ================================================================
# Collapsible Panel
# ================================================================
class CollapsiblePanel(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.toggle_button = QToolButton(text=title, checkable=True, checked=True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.DownArrow)
        self.toggle_button.clicked.connect(self._toggle)

        self.content_area = QWidget()
        self.content_area.setMaximumHeight(0)
        self.content_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.toggle_animation = QParallelAnimationGroup(self)
        self.content_anim = QPropertyAnimation(self.content_area, b"maximumHeight")
        self.content_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.content_anim.setDuration(200)
        self.toggle_animation.addAnimation(self.content_anim)

        self._content_layout = QVBoxLayout(self.content_area)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content_area)

    def addWidget(self, widget):
        self._content_layout.addWidget(widget)
        self.content_area.setMaximumHeight(self._content_layout.sizeHint().height())

    def _toggle(self):
        checked = self.toggle_button.isChecked()
        self.toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        target_height = self._content_layout.sizeHint().height() if checked else 0
        self.content_anim.setStartValue(self.content_area.maximumHeight())
        self.content_anim.setEndValue(target_height)
        self.toggle_animation.start()


# ================================================================
# Labeled Slider (Icon + Label + Slider + % Value)
# ================================================================
class LabeledSlider(QWidget):
    valueChanged = Signal(int)

    def __init__(self, icon_path: str, label_text: str, min_val=-100, max_val=100, parent=None):
        super().__init__(parent)

        # --- Icon + label
        icon_label = QLabel()
        if icon_path:
            icon_label.setPixmap(QIcon(icon_path).pixmap(16, 16))
        self.text_label = QLabel(label_text)
        self.text_label.setMinimumWidth(70)

        # --- Slider and value
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(0)

        self.value_label = QLabel("0")
        # make the value label a bit wider so it remains visible
        self.value_label.setFixedWidth(52)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # --- Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)

        # Signal: slider → valueChanged proxy
        self.slider.valueChanged.connect(self._update_value_label)
        self.slider.valueChanged.connect(self.valueChanged.emit)

    def _update_value_label(self, val: int):
        self.value_label.setText(str(val))

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int):
        self.slider.setValue(v)

    def connect_value_changed(self, fn: callable):
        if callable(fn):
            self.slider.valueChanged.connect(fn)
        else:
            raise TypeError(f"LabeledSlider.connect_value_changed expected a callable, got {type(fn)}")

# ================================================================
# LightboxDialog
# ================================================================                
class LightboxDialog(QDialog):
    """
    Minimal, reliable photo viewer built from scratch with a simple editor:
    - Central canvas
    - Right-side editor panel (Light / Color)
    - Live preview using Pillow
    """

    # ---------- Icon helper ----------
    def _icon(self, name: str) -> QIcon:
        base_dir = os.path.join(os.path.dirname(__file__), "assets", "icons")
        path = os.path.join(base_dir, f"{name}.svg")
        theme = getattr(self, "_theme", "light")

        if os.path.exists(path):
            pm = QPixmap(48, 48)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            renderer = QSvgRenderer(path)
            renderer.render(p)
            tint = QColor("#000000" if theme == "light" else "#ffffff")
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), tint)
            p.end()
            return QIcon(pm)

        emoji_map = {
            "edit": "🖉", "rotate": "↻", "info": "ℹ️", "next": "▶", "prev": "◀",
            "save": "💾", "crop": "✂️", "reset": "⟳", "zoom_in": "➕",
            "zoom_out": "➖", "cancel": "✖", "brightness": "☀️", "contrast": "⚖️",
            "copy": "📋"
        }
        ch = emoji_map.get(name, "⬜")
        pm = QPixmap(48, 48)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        font = QFont("Segoe UI Emoji", 28)
        p.setFont(font)
        p.drawText(pm.rect(), Qt.AlignCenter, ch)
        p.end()
        return QIcon(pm)

    # ---------- Theme ----------
    def _apply_theme(self, theme: str = "light"):
        self._theme = theme
        if theme == "dark":
            bg = "#161616"; fg = "#ffffff"; panel = "#1f1f1f"; accent_start = "#0a84ff"
            button_border = "#2f2f2f"; hover_bg = "#1a73e8"; disabled = "#555555"
        else:
            bg = "#ffffff"; fg = "#000000"; panel = "#ffffff"; accent_start = "#0078d4"
            button_border = "#cccccc"; hover_bg = "#cde6ff"; disabled = "#bbbbbb"

        self._palette_tokens = {
            "bg": bg, "fg": fg, "panel": panel,
            "accent_start": accent_start, "button_border": button_border,
            "hover_bg": hover_bg, "disabled": disabled
        }
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(bg))
        pal.setColor(self.foregroundRole(), QColor(fg))
        self.setPalette(pal)
        self._toolbar_style = f"""
        QToolButton, QPushButton {{
            color: {fg};
            background-color: {panel};
            border: 1px solid {button_border};
            border-radius: 8px;
            padding: 6px 8px;
        }}
        QToolButton:hover, QPushButton:hover {{
            background-color: {hover_bg};
        }}
        """

    def _button_style(self) -> str:
        t = getattr(self, "_palette_tokens", {"fg":"#000","accent_start":"#0078d4","button_border":"#ccc","hover_bg":"#cde6ff","disabled":"#bbb"})
        return f"""
        QToolButton, QPushButton {{
            color: {t['fg']};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {t['accent_start']},stop:1 #999);
            border: 1px solid {t['button_border']};
            border-radius: 8px;
            padding: 8px 14px;
        }}
        QToolButton:hover, QPushButton:hover {{
            background: {t['hover_bg']};
        }}
        QToolButton:disabled, QPushButton:disabled {{
            background: {t['disabled']};
            color: #888;
        }}
        """

    # ---------- Inner canvas ----------
    class _ImageCanvas(QWidget):
        # emit whenever absolute scale changes
        scaleChanged = Signal(float)
        
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.setMouseTracking(True)
            self._pixmap = None
            self._img_size = QSize(0,0)
            self._scale = 1.0
            self._fit_scale = 1.0
            self._offset = QPointF(0,0)
            self._dragging = False
            self._drag_start_pos = QPoint()
            self._drag_start_offset = QPointF(0,0)
            self._bg = QColor(16,16,16)
            # crop
            self._crop_mode = False
            self._crop_rect = None
            self._crop_dragging = False
            self._crop_start = QPoint()
            # ENHANCEMENT: Microsoft Photos-style aspect ratio support
            self._crop_aspect_ratio = None  # None = freeform, or (w, h) tuple for fixed aspect
            self._crop_preset = "freeform"  # Current preset: "freeform", "16:9", "4:3", "1:1", "3:2", "original"
            
            # PHASE 2: Before/After split-view
            self._before_pm = None
            self._before_after_mode = False
            self._divider_rel = 0.5  # fraction of widget width
            self._divider_dragging = False
            self._divider_hit_px = 10

        def set_pixmap(self, pm: QPixmap):
            self._pixmap = pm
            self._img_size = pm.size()
            self._offset = QPointF(0,0)
            self._recompute_fit_scale()
            self._scale = self._fit_scale
            # notify about the new scale
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass            
            self.update()
        
        def set_before_pixmap(self, pm: QPixmap):
            self._before_pm = pm
            self.update()
        
        def set_before_after_mode(self, enabled: bool):
            self._before_after_mode = bool(enabled)
            # center divider at middle when enabling
            if enabled:
                self._divider_rel = 0.5
            self.update()

        def set_pixmap_preserve_view(self, pm: QPixmap):
            """Set pixmap but preserve current zoom and center if possible."""
            prev_scale = self._scale
            self._pixmap = pm
            self._img_size = pm.size()
            self._recompute_fit_scale()
            # Keep previous scale if it's above fit; otherwise use fit
            self._scale = max(prev_scale, self._fit_scale)
            # Center image in view
            self._offset = QPointF(
                (self.width() - self._img_size.width() * self._scale) / 2.0,
                (self.height() - self._img_size.height() * self._scale) / 2.0
            )
            self._clamp_offset()
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass
            self.update()

        def reset_view(self):
            self._offset = QPointF(0,0)
            self._recompute_fit_scale()
            self._scale = self._fit_scale
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass            
            self.update()
        
        def _center_image(self):
            """Center the image in the canvas after transformations."""
            if self._pixmap is None:
                return
            self._offset = QPointF(
                (self.width() - self._img_size.width() * self._scale) / 2.0,
                (self.height() - self._img_size.height() * self._scale) / 2.0
            )
            self.update()
 
        def zoom_to(self, scale: float, anchor_px: QPointF = None):
            """Set absolute scale (not delta). Enforce min = fit. Keep anchor under cursor."""            
            if self._pixmap is None:
                return
            new_scale = max(scale, self._fit_scale * 0.25)
            
            if anchor_px is None:
                anchor_px = QPointF(self.width() / 2.0, self.height() / 2.0)

            img_w, img_h = self._img_size.width(), self._img_size.height()
            if img_w == 0 or img_h == 0:
                return

            ax = (anchor_px.x() - self._offset.x()) / self._scale
            ay = (anchor_px.y() - self._offset.y()) / self._scale

            # Update scale
            self._scale = new_scale
            
            # Recompute offset so the same image point stays under the anchor
            self._offset.setX(anchor_px.x() - ax * self._scale)
            self._offset.setY(anchor_px.y() - ay * self._scale)

            self._clamp_offset()
            # notify subscriber(s) about scale change
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass            
            self.update()


        def relative_zoom(self, factor: float, anchor_px: QPointF):
            self.zoom_to(self._scale * factor, anchor_px)

        def set_scale_from_slider(self, slider_value: int):
            """
            Slider contract:
              0  -> fit scale
              1..200 -> fit * (1 + value/100 * 4)  (up to 5x fit)
              negative not used (kept simple)
            """            
            if self._pixmap is None:
                return
            if slider_value <= 0:
                self.zoom_to(self._fit_scale)
            else:
                # max ~5x the fit scale
                mult = 1.0 + (slider_value / 100.0) * 4.0
                self.zoom_to(self._fit_scale * mult)


        def paintEvent(self, ev):
            p = QPainter(self)
            p.fillRect(self.rect(), self._bg)
            if self._pixmap and not self._pixmap.isNull():
                if self._before_after_mode and self._before_pm:
                    divider_x = int(self.width() * self._divider_rel)
                    # Draw BEFORE on the left side
                    p.setClipRect(QRect(0, 0, divider_x, self.height()))
                    p.translate(self._offset)
                    p.scale(self._scale, self._scale)
                    p.drawPixmap(0, 0, self._before_pm.width(), self._before_pm.height(), self._before_pm)
                    p.resetTransform(); p.setClipping(False)
                    
                    # Draw AFTER on the right side
                    p.setClipRect(QRect(divider_x, 0, self.width() - divider_x, self.height()))
                    p.translate(self._offset)
                    p.scale(self._scale, self._scale)
                    p.drawPixmap(0, 0, self._pixmap.width(), self._pixmap.height(), self._pixmap)
                    p.resetTransform(); p.setClipping(False)
                    
                    # Divider line
                    p.setPen(QPen(QColor(255, 255, 255, 180), 2, Qt.SolidLine))
                    p.drawLine(divider_x, 0, divider_x, self.height())
                else:
                    p.translate(self._offset)
                    p.scale(self._scale, self._scale)
                    p.drawPixmap(0,0,self._pixmap.width(), self._pixmap.height(), self._pixmap)
            if self._crop_mode and self._crop_rect:
                p.resetTransform()
                p.setRenderHint(QPainter.Antialiasing)
                
                # MICROSOFT PHOTOS STYLE: Professional crop overlay
                # 1. Darken everything outside crop area (semi-transparent black)
                crop_path = QPainterPath()
                crop_path.addRect(self._crop_rect)
                
                full_path = QPainterPath()
                full_path.addRect(self.rect())
                
                overlay_path = full_path.subtracted(crop_path)
                p.fillPath(overlay_path, QColor(0, 0, 0, 140))  # Darker overlay
                
                # 2. Draw bright white border (2px solid)
                p.setPen(QPen(QColor(255, 255, 255), 3, Qt.SolidLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(self._crop_rect)
                
                # 3. Draw corner handles (larger, more visible)
                handle_size = 12
                handle_thickness = 3
                handle_length = 30
                corners = [
                    (self._crop_rect.topLeft(), 'TL'),
                    (self._crop_rect.topRight(), 'TR'),
                    (self._crop_rect.bottomLeft(), 'BL'),
                    (self._crop_rect.bottomRight(), 'BR')
                ]
                
                p.setPen(QPen(QColor(255, 255, 255), handle_thickness, Qt.SolidLine))
                for corner, pos in corners:
                    x, y = corner.x(), corner.y()
                    
                    if pos == 'TL':
                        # Top-left: L-shaped handle
                        p.drawLine(x, y, x + handle_length, y)  # Horizontal
                        p.drawLine(x, y, x, y + handle_length)  # Vertical
                    elif pos == 'TR':
                        # Top-right: L-shaped handle
                        p.drawLine(x, y, x - handle_length, y)  # Horizontal
                        p.drawLine(x, y, x, y + handle_length)  # Vertical
                    elif pos == 'BL':
                        # Bottom-left: L-shaped handle
                        p.drawLine(x, y, x + handle_length, y)  # Horizontal
                        p.drawLine(x, y, x, y - handle_length)  # Vertical
                    elif pos == 'BR':
                        # Bottom-right: L-shaped handle
                        p.drawLine(x, y, x - handle_length, y)  # Horizontal
                        p.drawLine(x, y, x, y - handle_length)  # Vertical
                
                # 4. Draw rule of thirds grid (subtle white lines)
                p.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.SolidLine))
                rect_w = self._crop_rect.width()
                rect_h = self._crop_rect.height()
                x1 = self._crop_rect.left() + rect_w // 3
                x2 = self._crop_rect.left() + 2 * rect_w // 3
                y1 = self._crop_rect.top() + rect_h // 3
                y2 = self._crop_rect.top() + 2 * rect_h // 3
                
                p.drawLine(x1, self._crop_rect.top(), x1, self._crop_rect.bottom())
                p.drawLine(x2, self._crop_rect.top(), x2, self._crop_rect.bottom())
                p.drawLine(self._crop_rect.left(), y1, self._crop_rect.right(), y1)
                p.drawLine(self._crop_rect.left(), y2, self._crop_rect.right(), y2)
                
                # 5. Draw preset label (Microsoft Photos style - top center)
                if self._crop_preset != "freeform":
                    label_text = self._crop_preset.upper()
                    p.setFont(QFont("Segoe UI", 11, QFont.Bold))
                    
                    # Measure text to center it
                    fm = p.fontMetrics()
                    text_width = fm.horizontalAdvance(label_text)
                    text_height = fm.height()
                    
                    # Position at top center of crop rect
                    label_x = self._crop_rect.center().x() - text_width // 2
                    label_y = self._crop_rect.top() - text_height - 8
                    
                    # Ensure label stays on screen
                    if label_y < 10:
                        label_y = self._crop_rect.top() + 10
                    
                    label_rect = QRect(
                        label_x - 8,
                        label_y - 4,
                        text_width + 16,
                        text_height + 8
                    )
                    
                    # Draw semi-transparent background
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(0, 0, 0, 180))
                    p.drawRoundedRect(label_rect, 4, 4)
                    
                    # Draw text
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(label_rect, Qt.AlignCenter, label_text)
            p.end()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            prev_fit = self._fit_scale
            self._recompute_fit_scale()
            if self._scale <= prev_fit + 1e-6:
                self._scale = self._fit_scale
                self._offset = QPointF((self.width()-self._img_size.width()*self._scale)/2.0,
                                       (self.height()-self._img_size.height()*self._scale)/2.0)
            self._clamp_offset()
            self.update()

        def mousePressEvent(self, ev):
            if self._crop_mode:
                if ev.button() == Qt.LeftButton:
                    self._crop_dragging = True
                    self._crop_start = ev.pos()
                    self._crop_rect = QRect(self._crop_start, QSize())
                    self.update()
                return
            # PHASE 2: Divider drag in before/after mode
            if self._before_after_mode and ev.button() == Qt.LeftButton:
                divider_x = int(self.width() * self._divider_rel)
                if abs(ev.pos().x() - divider_x) <= self._divider_hit_px:
                    self._divider_dragging = True
                    ev.accept()
                    return
            if ev.button() == Qt.LeftButton and self._pixmap:
                self._dragging = True
                self._drag_start_pos = ev.pos()
                self._drag_start_offset = QPointF(self._offset)
                self.setCursor(Qt.ClosedHandCursor)

        def mouseMoveEvent(self, ev):
            # PHASE 2: Divider drag
            if self._before_after_mode and self._divider_dragging:
                nx = ev.pos().x() / max(1, self.width())
                self._divider_rel = max(0.1, min(0.9, nx))
                self.update()
                return
            if self._crop_mode and self._crop_dragging:
                # Create crop rect from drag start to current position
                raw_rect = QRect(self._crop_start, ev.pos()).normalized()
                
                # Apply aspect ratio constraint if set
                if self._crop_aspect_ratio:
                    aspect_w, aspect_h = self._crop_aspect_ratio
                    aspect = aspect_w / aspect_h
                    
                    # Calculate constrained dimensions
                    width = raw_rect.width()
                    height = raw_rect.height()
                    
                    # Determine which dimension to fix based on drag direction
                    current_aspect = width / max(height, 1)
                    
                    if current_aspect > aspect:
                        # Too wide - fix width, adjust height
                        height = int(width / aspect)
                    else:
                        # Too tall - fix height, adjust width
                        width = int(height * aspect)
                    
                    # Create constrained rect from drag start point
                    self._crop_rect = QRect(
                        raw_rect.left(),
                        raw_rect.top(),
                        width,
                        height
                    )
                else:
                    # Freeform - use raw rect
                    self._crop_rect = raw_rect
                
                self.update()
                return
            if self._dragging and self._pixmap:
                delta = ev.pos() - self._drag_start_pos
                self._offset = QPointF(self._drag_start_offset.x()+delta.x(), self._drag_start_offset.y()+delta.y())
                self._clamp_offset()
                self.update()

        def mouseReleaseEvent(self, ev):
            if ev.button() == Qt.LeftButton:
                self._divider_dragging = False
            if self._crop_mode and ev.button() == Qt.LeftButton:
                self._crop_dragging = False
                self.update()
                return
            if ev.button() == Qt.LeftButton and self._dragging:
                self._dragging = False
                self.setCursor(Qt.OpenHandCursor)

        def wheelEvent(self, ev):
            if not self._pixmap:
                return
            steps = ev.angleDelta().y()/120.0
            if steps == 0:
                return
            factor = 1.15 ** steps
            self.relative_zoom(factor, QPointF(ev.position()))
            ev.accept()

        def _recompute_fit_scale(self):
            if not self._pixmap or self._img_size.isEmpty() or self.width()<2 or self.height()<2:
                self._fit_scale = 1.0
                return
            vw, vh = float(self.width()), float(self.height())
            iw, ih = float(self._img_size.width()), float(self._img_size.height())
            self._fit_scale = min(vw/iw, vh/ih)
            self._offset = QPointF((vw - iw*self._fit_scale)/2.0, (vh - ih*self._fit_scale)/2.0)

        def _clamp_offset(self):
            if not self._pixmap:
                return
            vw, vh = float(self.width()), float(self.height())
            iw, ih = float(self._img_size.width())*self._scale, float(self._img_size.height())*self._scale
            if iw <= vw:
                self._offset.setX((vw - iw)/2.0)
            else:
                min_x = vw - iw; max_x = 0.0
                self._offset.setX(min(max(self._offset.x(), min_x), max_x))
            if ih <= vh:
                self._offset.setY((vh - ih)/2.0)
            else:
                min_y = vh - ih; max_y = 0.0
                self._offset.setY(min(max(self._offset.y(), min_y), max_y))

        def enter_crop_mode(self, aspect_ratio=None, preset="freeform"):
            """
            Enter crop mode with optional aspect ratio constraint.
            
            Args:
                aspect_ratio: None for freeform, or (width, height) tuple for fixed aspect
                preset: "freeform", "16:9", "4:3", "1:1", "3:2", "original"
            """
            self._crop_mode = True
            self._crop_aspect_ratio = aspect_ratio
            self._crop_preset = preset
            
            # MICROSOFT PHOTOS BEHAVIOR: Create initial crop rectangle
            # - HEIGHT MATCHES PHOTO HEIGHT (fills vertical space)
            # - Width is calculated based on aspect ratio
            if self._pixmap and not self._crop_rect:
                img_w = self._img_size.width()
                img_h = self._img_size.height()
                
                # MICROSOFT PHOTOS: Crop height = full image height
                crop_h = img_h
                
                # Calculate width based on aspect ratio
                if aspect_ratio:
                    aspect = aspect_ratio[0] / aspect_ratio[1]
                    crop_w = int(crop_h * aspect)
                    
                    # If calculated width exceeds image width, constrain by width
                    if crop_w > img_w:
                        crop_w = img_w
                        crop_h = int(crop_w / aspect)
                else:
                    # Freeform: use 80% of width
                    crop_w = int(img_w * 0.8)
                
                # Center the crop rectangle horizontally
                crop_x = (img_w - crop_w) // 2
                crop_y = (img_h - crop_h) // 2
                
                # Convert to screen coordinates
                screen_x = int(self._offset.x() + crop_x * self._scale)
                screen_y = int(self._offset.y() + crop_y * self._scale)
                screen_w = int(crop_w * self._scale)
                screen_h = int(crop_h * self._scale)
                
                self._crop_rect = QRect(screen_x, screen_y, screen_w, screen_h)
            
            self.setCursor(Qt.CrossCursor)
            self.update()

        def exit_crop_mode(self):
            self._crop_mode = False
            self._crop_rect = None
            self._crop_dragging = False
            self._crop_aspect_ratio = None
            self._crop_preset = "freeform"
            self.setCursor(Qt.OpenHandCursor)
            self.update()
        
        def set_crop_aspect_ratio(self, aspect_ratio, preset="custom"):
            """
            Update crop aspect ratio and constrain existing crop rect.
            
            Args:
                aspect_ratio: None for freeform, or (width, height) tuple
                preset: Name of preset (for UI display)
            """
            self._crop_aspect_ratio = aspect_ratio
            self._crop_preset = preset
            
            # If crop rect exists, constrain it to new aspect ratio
            if self._crop_rect and aspect_ratio:
                self._constrain_crop_to_aspect()
            
            self.update()
        
        def _constrain_crop_to_aspect(self):
            """
            Adjust crop rect to match current aspect ratio.
            Maintains center point and scales to fit.
            """
            if not self._crop_rect or not self._crop_aspect_ratio:
                return
            
            aspect_w, aspect_h = self._crop_aspect_ratio
            aspect = aspect_w / aspect_h
            
            # Get current crop center
            center_x = self._crop_rect.center().x()
            center_y = self._crop_rect.center().y()
            
            # Calculate new dimensions maintaining aspect ratio
            current_w = self._crop_rect.width()
            current_h = self._crop_rect.height()
            
            # Determine which dimension to constrain
            current_aspect = current_w / max(current_h, 1)
            
            if current_aspect > aspect:
                # Too wide - constrain width
                new_h = current_h
                new_w = int(new_h * aspect)
            else:
                # Too tall - constrain height
                new_w = current_w
                new_h = int(new_w / aspect)
            
            # Create new rect centered on same point
            new_rect = QRect(
                int(center_x - new_w / 2),
                int(center_y - new_h / 2),
                new_w,
                new_h
            )
            
            self._crop_rect = new_rect

        def mouseReleaseEvent(self, ev):
            if ev.button() == Qt.LeftButton:
                self._divider_dragging = False
                self._crop_dragging = False
                if self._dragging:
                    self._dragging = False
                    self.setCursor(Qt.OpenHandCursor)

    # ---------- Dialog ----------
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Photo Viewer")
        self.resize(1200,800)

        self._path = image_path
        self._image_list = []
        self._current_index = 0
        
        # 🔧 FIX: Store database reference and project_id from parent
        # This fixes the "DB instance: None" issue
        import logging
        from reference_db import ReferenceDB
        logger = logging.getLogger(__name__)
        
        self._shared_db_instance = None
        self._project_id = None
        
        logger.info(f"[LightboxDialog.__init__] parent={parent}")
        logger.info(f"[LightboxDialog.__init__] parent type={type(parent).__name__ if parent else 'None'}")
        
        if parent:
            # Check for 'db' attribute
            has_db = hasattr(parent, 'db')
            has_ref_db = hasattr(parent, 'reference_db')
            has_project_id = hasattr(parent, 'project_id')
            
            logger.info(f"[LightboxDialog.__init__] parent.db exists: {has_db}")
            logger.info(f"[LightboxDialog.__init__] parent.reference_db exists: {has_ref_db}")
            logger.info(f"[LightboxDialog.__init__] parent.project_id exists: {has_project_id}")
            
            # MainWindow uses 'db' attribute (not 'reference_db')
            if has_db:
                self._shared_db_instance = parent.db
                logger.info(f"[LightboxDialog.__init__] Got db from parent.db: {self._shared_db_instance}")
            elif has_ref_db:
                self._shared_db_instance = parent.reference_db
                logger.info(f"[LightboxDialog.__init__] Got db from parent.reference_db: {self._shared_db_instance}")
            
            if has_project_id:
                self._project_id = parent.project_id
                logger.info(f"[LightboxDialog.__init__] Got project_id: {self._project_id}")
            
            # If parent doesn't have db, create our own instance
            if not self._shared_db_instance:
                logger.warning(f"[LightboxDialog.__init__] Parent has NO db - creating new ReferenceDB instance")
                self._shared_db_instance = ReferenceDB()
                logger.info(f"[LightboxDialog.__init__] Created new db instance: {self._shared_db_instance}")
            
            # Get project_id from grid if not in parent
            if not self._project_id:
                if hasattr(parent, 'grid') and hasattr(parent.grid, 'project_id'):
                    self._project_id = parent.grid.project_id
                    logger.info(f"[LightboxDialog.__init__] Got project_id from parent.grid: {self._project_id}")
                elif hasattr(parent, 'sidebar') and hasattr(parent.sidebar, 'project_id'):
                    self._project_id = parent.sidebar.project_id
                    logger.info(f"[LightboxDialog.__init__] Got project_id from parent.sidebar: {self._project_id}")
        else:
            logger.warning(f"[LightboxDialog.__init__] Parent is None - creating new ReferenceDB instance")
            self._shared_db_instance = ReferenceDB()
            logger.info(f"[LightboxDialog.__init__] Created new db instance: {self._shared_db_instance}")

        # default light theme (metadata requested black text)
        self._apply_theme("light")
        
        # --- Edit staging state (non-destructive editing) ---
        # _orig_pil: preview-resolution PIL Image (max 2560px) for editing.
        # _edit_base_pil: editable base (copy of _orig_pil).
        # _working_pil: last processed PIL Image (preview).
        # _orig_file_dimensions: original file dimensions for full-res save.
        self._orig_pil = None
        self._edit_base_pil = None
        self._working_pil = None
        self._orig_file_dimensions = None
        self._is_dirty = False

        # metadata cache + preload control
        self._meta_cache = {}
        self._meta_preloader = None
        self._preload_stop = False
        self.adjustments = {       # slider values -100..100
            "brightness": 0,
            "exposure": 0,
            "contrast": 0,
            "highlights": 0,
            "shadows": 0,
            "vignette": 0,
            "saturation": 0,
            "warmth": 0
        }
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(50)
        self._apply_timer.timeout.connect(self._apply_adjustments)
        
        # PHASE 1: Initialize edit history for undo/redo
        self._edit_history = EditHistoryStack(max_history=50)

        # UI skeleton
        self.stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        outer.addWidget(self.stack)

        # === Viewer page ===
        viewer = QWidget()
        vbox = QVBoxLayout(viewer)
        vbox.setContentsMargins(8,8,8,8)
        vbox.setSpacing(8)

        # top bar
        self._top = self._build_top_bar()
        vbox.addWidget(self._top, 0)

        # central area
        center = QWidget()
        hbox = QHBoxLayout(center)
        hbox.setContentsMargins(0,0,0,0)
        hbox.setSpacing(0)

        # === PHASE 1: Content stack for unified photo/video display ===
        self.content_stack = QStackedWidget()

        # Page 0: Image canvas (for photos) with rotation slider
        image_page = QWidget()
        image_page.setStyleSheet("background-color: #0f0f0f;")
        image_layout = QVBoxLayout(image_page)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(0)
        
        self.canvas = LightboxDialog._ImageCanvas(self)
        self.canvas.setCursor(Qt.OpenHandCursor)
        self.canvas.setToolTip("Before/After: Press B to toggle. Drag divider in split-view.")
        # keep viewer & editor zoom controls synchronized whenever canvas scale changes
        try:
            self.canvas.scaleChanged.connect(self._on_canvas_scale_changed)
        except Exception:
            pass
        image_layout.addWidget(self.canvas, 1)
        
        # MICROSOFT PHOTOS: Rotation slider (hidden by default, shown in crop mode)
        self.rotation_slider_widget = RotationSlider(parent=self)
        self.rotation_slider_widget.rotationChanged.connect(self._on_rotation_changed)
        self.rotation_slider_widget.rotate90LeftRequested.connect(self._rotate_90_left)
        self.rotation_slider_widget.rotate90RightRequested.connect(self._rotate_90_right)
        self.rotation_slider_widget.flipHorizontalRequested.connect(self._flip_horizontal)
        self.rotation_slider_widget.flipVerticalRequested.connect(self._flip_vertical)
        self.rotation_slider_widget.hide()
        image_layout.addWidget(self.rotation_slider_widget, 0)
        
        self.content_stack.addWidget(image_page)  # index 0

        # Page 1: Video player (for videos)
        video_container = QWidget()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(0)

        # Video widget with proper sizing
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(300)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setStyleSheet("background-color: black;")
        self.video_widget.installEventFilter(self)  # PHASE 2: Capture double-click for fullscreen
        video_layout.addWidget(self.video_widget)

        self.content_stack.addWidget(video_container)  # index 1

        # Initialize media player for video playback
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)

        # Connect signals for video playback
        try:
            self.media_player.playbackStateChanged.connect(self._on_video_playback_state_changed)
            self.media_player.errorOccurred.connect(self._on_video_error)
            self.media_player.positionChanged.connect(self._on_video_position_changed)
            self.media_player.durationChanged.connect(self._on_video_duration_changed)
            print("[LightboxDialog] Video player signals connected successfully")
        except Exception as e:
            print(f"[LightboxDialog] WARNING: Failed to connect video signals: {e}")

        # Video position update timer
        self._video_duration = 0
        self._timeline_seeking = False  # Track if user is dragging timeline
        self._video_error_count = 0  # Track consecutive errors for recovery
        self._last_video_path = None  # Track current video for recovery
        self._gpu_warning_shown = False  # Track if we've shown GPU warning (show once)
        self._gpu_error_timer = QTimer()  # Debounce GPU errors
        self._gpu_error_timer.setSingleShot(True)
        self._gpu_error_timer.timeout.connect(self._show_gpu_warning_once)
        
        # PHASE 2: Deferred histogram updates (debounce)
        self._hist_timer = QTimer()
        self._hist_timer.setSingleShot(True)
        try:
            self._hist_timer.timeout.connect(lambda: self.histogram_widget and self._working_pil and self.histogram_widget.set_image(self._working_pil))
        except Exception:
            pass

        # Track current media type
        self._current_media_type = "photo"  # "photo" or "video"
        self._is_video = False

        # === PHASE 2: Fullscreen mode support ===
        self._is_fullscreen = False
        self._pre_fullscreen_geometry = None
        self._pre_fullscreen_state = None

        hbox.addWidget(self.content_stack, 1)

        # install event filter for canvas (if needed downstream)
        self.canvas.installEventFilter(self)
        
        # meta placeholder (exists independently from editor panel)
        self._meta_placeholder = QWidget()
        self._meta_placeholder.setFixedWidth(0)
        hbox.addWidget(self._meta_placeholder, 0)

        vbox.addWidget(center, 1)

        # bottom bar
        self._bottom = self._build_bottom_bar()
        vbox.addWidget(self._bottom, 0)

        self.stack.addWidget(viewer)

        # editor page
        self.editor_page = self._build_edit_page()
        self.stack.addWidget(self.editor_page)

        # right editor panel (created but not added until edit mode)
        self.right_editor_panel = self._build_right_editor_panel()

        # load media file (photo or video)
        if image_path:
            self._load_media(image_path)

        self._init_navigation_arrows()
        self.stack.setCurrentIndex(0)

    # -------------------------------
    # Build Right-side editor panel
    # -------------------------------
    def _build_right_editor_panel(self) -> QWidget:
        """Construct right-hand editor panel for Light & Color adjustments (collapsible)."""
        panel = QWidget()
        panel.setFixedWidth(400)  # Slightly wider for histogram
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(12)
        
        # PHASE 1: Add histogram widget at the top
        histogram_label = QLabel("Histogram")
        histogram_label.setStyleSheet("color:#000;font-size:12px;font-weight:bold;")
        layout.addWidget(histogram_label)
        
        self.histogram_widget = HistogramWidget(parent=self)
        layout.addWidget(self.histogram_widget)

        # Light section
        light_group = CollapsiblePanel("Light")
        # brightness, exposure, contrast, highlights, shadows, vignette
        sliders = [
            ("brightness", "Brightness", "brightness", -100, 100),
            ("exposure", "Exposure", "brightness", -100, 100),
            ("contrast", "Contrast", "contrast", -100, 100),
            ("highlights", "Highlights", "brightness", -100, 100),
            ("shadows", "Shadows", "brightness", -100, 100),
            ("vignette", "Vignette", "crop", -100, 100)
        ]
        for key, label, icon, lo, hi in sliders:
            s = LabeledSlider("", label, lo, hi)
            s.text_label.setText(label)
            s.valueChanged.connect(lambda v, k=key: self._on_adjustment_changed(k, v))
            light_group.addWidget(s)
            # store widget for reset/sync
            setattr(self, f"slider_{key}", s)

        layout.addWidget(light_group)

        # Color section
        color_group = CollapsiblePanel("Color")
        sliders_c = [
            ("saturation", "Saturation", "brightness", -100, 100),
            ("warmth", "Warmth", "brightness", -100, 100)
        ]
        for key,label,icon,lo,hi in sliders_c:
            s = LabeledSlider("", label, lo, hi)
            s.text_label.setText(label)
            s.valueChanged.connect(lambda v, k=key: self._on_adjustment_changed(k, v))
            color_group.addWidget(s)
            setattr(self, f"slider_{key}", s)

        layout.addWidget(color_group)

        # Reset button
        btn_reset = QPushButton("Reset All")
        btn_reset.setStyleSheet(self._button_style())
        btn_reset.clicked.connect(self._reset_adjustments)
        layout.addWidget(btn_reset)

        layout.addStretch(1)
        return panel

    def _build_filters_panel(self) -> QWidget:
        """Build a scrollable filters panel with thumbnails and preset filter actions."""
        t = getattr(self, "_palette_tokens", {"panel": "#ffffff", "fg": "#000000", "button_border": "#ddd"})
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        # Auto Enhance button
        auto_btn = QPushButton("Auto Enhance")
        auto_btn.setStyleSheet(self._button_style())
        auto_btn.clicked.connect(lambda: self._apply_filter_preset("Auto Enhance"))
        v.addWidget(auto_btn)

        # Grid of presets (thumbnail buttons)
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
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        thumb_size = QSize(96, 72)
        for i, (name, adj) in enumerate(presets):
            btn = QPushButton(name)
            btn.setFixedSize(thumb_size.width() + 24, thumb_size.height() + 24)
            # attach closure with preset data
            btn.clicked.connect(lambda _, n=name, a=adj: self._apply_filter_preset(n, preset_adjustments=a))
            grid.addWidget(btn, i // 2, i % 2)

        v.addLayout(grid)
        v.addStretch(1)

        scroll.setWidget(container)
        w = QWidget()
        w_layout = QVBoxLayout(w)
        w_layout.setContentsMargins(0, 0, 0, 0)
        w_layout.addWidget(scroll)
        w.setMinimumWidth(360)
        return w

    def _on_adjustment_changed(self, key: str, val: int):
        """Store slider value and schedule re-apply of adjustments (debounced)."""
        self.adjustments[key] = int(val)
        # schedule apply (debounced)
        self._apply_timer.start()

    def _reset_adjustments(self):
        for k in self.adjustments.keys():
            self.adjustments[k] = 0
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.setValue(0)
        # restore original
        self._apply_adjustments()

    # -------------------------------
    # Image processing (Pillow)
    # -------------------------------
    
    def _toggle_right_editor_panel(self):
        """Show or hide the right-side adjustments panel inside the editor placeholder; hides filters if open."""
        try:
            if not hasattr(self, "_editor_right_placeholder"):
                return
            ph = self._editor_right_placeholder
            layout = ph.layout()
            visible = ph.width() > 0 and getattr(self, "_current_right_panel", None) == "adjustments"

            if visible:
                # hide
                if layout:
                    while layout.count():
                        item = layout.takeAt(0)
                        w = item.widget()
                        if w:
                            w.setParent(None)
                ph.setFixedWidth(0)
                self._current_right_panel = None
                if hasattr(self, "btn_adjust") and getattr(self.btn_adjust, "setChecked", None):
                    self.btn_adjust.setChecked(False)
            else:
                # if filters currently open, close them first
                if getattr(self, "_current_right_panel", None) == "filters":
                    self._toggle_filters_panel()

                if layout is None:
                    layout = QVBoxLayout(ph)
                    layout.setContentsMargins(0, 0, 0, 0)
                    layout.setSpacing(0)

                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        item.widget().setParent(None)

                # attach the panel widget (use existing instance)
                if not hasattr(self, "right_editor_panel") or self.right_editor_panel is None:
                    self.right_editor_panel = self._build_right_editor_panel()
                layout.addWidget(self.right_editor_panel)
                ph.setFixedWidth(self.right_editor_panel.minimumWidth() or 360)
                self._current_right_panel = "adjustments"
                if hasattr(self, "btn_adjust") and getattr(self.btn_adjust, "setChecked", None):
                    self.btn_adjust.setChecked(True)
        except Exception as e:
            print(f"[toggle_right_editor_panel] error: {e}")    

    @staticmethod
    def _apply_adjustment_pipeline(src_img, adjustments):
        """Apply the full adjustment pipeline to a PIL image.

        Extracted so it can be reused for both preview (capped) and
        full-resolution save/export.  All operations use Pillow's C
        routines except vignette, which builds the mask at a small
        fixed size and up-scales (O(1) in image resolution).

        Args:
            src_img: PIL Image (RGBA) to process.
            adjustments: dict with keys brightness, exposure, contrast,
                         highlights, shadows, saturation, warmth, vignette.

        Returns:
            Processed PIL Image (RGBA).
        """
        img = src_img.copy().convert("RGBA")

        # Exposure
        exp = adjustments.get("exposure", 0)
        if exp != 0:
            img = ImageEnhance.Brightness(img).enhance(1.0 + (exp / 100.0) * 0.5)

        # Brightness
        bri = adjustments.get("brightness", 0)
        if bri != 0:
            img = ImageEnhance.Brightness(img).enhance(1.0 + (bri / 100.0) * 1.0)

        # Contrast
        ctr = adjustments.get("contrast", 0)
        if ctr != 0:
            img = ImageEnhance.Contrast(img).enhance(1.0 + (ctr / 100.0) * 1.2)

        # Highlights / Shadows
        try:
            luma = img.convert("L")
            highlights_val = adjustments.get("highlights", 0)
            if highlights_val != 0:
                mask_h = luma.point(lambda v: max(0, min(255, int((v - 128) * 2))) if v > 128 else 0)
                bright_img = ImageEnhance.Brightness(img).enhance(1.0 + (highlights_val / 100.0) * 0.8)
                img = Image.composite(bright_img, img, mask_h)
            shadows_val = adjustments.get("shadows", 0)
            if shadows_val != 0:
                mask_s = luma.point(lambda v: max(0, min(255, int((128 - v) * 2))) if v < 128 else 0)
                dark_img = ImageEnhance.Brightness(img).enhance(1.0 + (shadows_val / 100.0) * 0.6)
                img = Image.composite(dark_img, img, mask_s)
        except Exception:
            pass

        # Saturation
        sat = adjustments.get("saturation", 0)
        if sat != 0:
            img = ImageEnhance.Color(img).enhance(1.0 + (sat / 100.0) * 1.5)

        # Warmth
        warmth = adjustments.get("warmth", 0)
        if warmth != 0:
            w = warmth / 100.0
            r_mult = 1.0 + (0.6 * w)
            b_mult = 1.0 - (0.6 * w)
            try:
                r, g, b, a = img.split()
                r = r.point(lambda v: max(0, min(255, int(v * r_mult))))
                b = b.point(lambda v: max(0, min(255, int(v * b_mult))))
                img = Image.merge("RGBA", (r, g, b, a))
            except Exception:
                pass

        # Vignette — build mask at small fixed size, upscale (fast at any resolution)
        vign = adjustments.get("vignette", 0)
        if vign != 0:
            strength = abs(vign) / 100.0
            target_w, target_h = img.size
            # Generate vignette mask at 256x256, then resize to image dims
            ms = 256
            mask_small = Image.new("L", (ms, ms), 0)
            cx = ms / 2.0
            cy = ms / 2.0
            maxrad = (cx * cx + cy * cy) ** 0.5
            pix = mask_small.load()
            inner = maxrad * (1.0 - 0.6 * strength)
            outer = maxrad * (0.6 * strength + 1e-6)
            for yi in range(ms):
                for xi in range(ms):
                    d = ((xi - cx) ** 2 + (yi - cy) ** 2) ** 0.5
                    v = int(255 * min(1.0, max(0.0, (d - inner) / outer)))
                    pix[xi, yi] = v
            mask = mask_small.resize((target_w, target_h), Image.BILINEAR)
            dark = Image.new("RGBA", img.size, (0, 0, 0, int(255 * 0.5 * strength)))
            img = Image.composite(dark, img, mask)

        return img

    def _apply_adjustments(self):
        """Apply adjustments to the current edit-base (non-destructive) and update canvas preview."""
        src = self._edit_base_pil or self._orig_pil
        if src is None:
            return

        img = self._apply_adjustment_pipeline(src, self.adjustments)

        # store working preview and flag dirty if different from edit base
        self._working_pil = img
        # mark as dirty if any adjustment non-zero OR if edit_base differs from orig
        self._is_dirty = any(v != 0 for v in self.adjustments.values()) or (self._edit_base_pil is not None and getattr(self._edit_base_pil, "mode", None) is not None and self._edit_base_pil.tobytes() != self._orig_pil.tobytes() if self._orig_pil is not None else False)

        # update Save action enablement
        if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
            self._save_action_overwrite.setEnabled(self._is_dirty and bool(self._path))

        # update canvas preview
        try:
            pm = self._pil_to_qpixmap(self._working_pil)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)
            # Reset rotation preview state on new photo
            self._rotation_preview_base = None
            if hasattr(self, 'rotation_slider_widget'):
                self.rotation_slider_widget.reset()

            # PHASE 2: Deferred histogram update (debounced)
            if hasattr(self, '_hist_timer') and hasattr(self, 'histogram_widget') and self.histogram_widget:
                try:
                    self._hist_timer.start(200)
                except Exception:
                    self.histogram_widget.set_image(self._working_pil)
        except Exception as e:
            print("[_apply_adjustments] error:", e)

    def _apply_filter_preset(self, name: str, preset_adjustments: dict = None):
        """Apply a filter preset: set adjustments and re-run pipeline. Name visible in status/log."""
        # For Auto Enhance do a simple heuristic: small contrast + brightness + saturation bump
        if name == "Auto Enhance":
            presets = {"contrast": 10, "brightness": 5, "saturation": 8}
            self.adjustments.update({k: presets.get(k, 0) for k in self.adjustments.keys()})
        elif preset_adjustments is None:
            # "Original" -> reset adjustments to 0
            for k in self.adjustments.keys():
                self.adjustments[k] = 0
        else:
            # update only keys provided
            for k in self.adjustments.keys():
                self.adjustments[k] = int(preset_adjustments.get(k, 0))

        # reflect on sliders if available
        for k, v in self.adjustments.items():
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(int(v))
                slider.blockSignals(False)

        # schedule reapply
        self._apply_timer.start()
        # mark dirty
        self._is_dirty = True
        if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
            self._save_action_overwrite.setEnabled(bool(self._path))

    def _toggle_filters_panel(self):
        """Show/hide filters panel in the right placeholder; ensure adjustments panel hidden when filters shown."""
        # if right panel placeholder missing, do nothing
        if not hasattr(self, "_editor_right_placeholder"):
            return
        ph = self._editor_right_placeholder
        layout = ph.layout()
        visible = ph.width() > 0 and getattr(self, "_current_right_panel", None) == "filters"

        if visible:
            # hide
            if layout:
                while layout.count():
                    item = layout.takeAt(0)
                    w = item.widget()
                    if w:
                        w.setParent(None)
            ph.setFixedWidth(0)
            self._current_right_panel = None
            if hasattr(self, "btn_filter") and getattr(self.btn_filter, "setChecked", None):
                self.btn_filter.setChecked(False)
        else:
            # ensure adjustments panel is hidden first
            if getattr(self, "_current_right_panel", None) == "adjustments":
                self._toggle_right_editor_panel()
            # mount filters
            if layout is None:
                layout = QVBoxLayout(ph)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            # create panel if needed
            if not hasattr(self, "right_filters_panel") or self.right_filters_panel is None:
                self.right_filters_panel = self._build_filters_panel()
            layout.addWidget(self.right_filters_panel)
            ph.setFixedWidth(self.right_filters_panel.minimumWidth() or 360)
            self._current_right_panel = "filters"
            if hasattr(self, "btn_filter") and getattr(self.btn_filter, "setChecked", None):
                self.btn_filter.setChecked(True)
            
    # -------------------------------
    # Top/bottom/editor builders (unchanged style but integrated)
    # -------------------------------

    def _build_edit_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10,10,10,10)
        layout.setSpacing(8)

        # === Row 1: Back + Filename ===
        row1 = QHBoxLayout()
        self.btn_back = QToolButton()
        self.btn_back.setIcon(self._icon("prev"))
        self.btn_back.setText("Back")
        self.btn_back.setToolTip("Return to viewer")
        self.btn_back.setStyleSheet(self._button_style())
        self.btn_back.clicked.connect(self._return_to_viewer)

        self.lbl_edit_title = QLabel(os.path.basename(self._path) if self._path else "")
        self.lbl_edit_title.setStyleSheet("color:#333;font-weight:bold;")
        
        row1.addWidget(self.btn_back)
        row1.addWidget(self.lbl_edit_title)
        row1.addStretch(1)
        # Undo/Redo buttons will be added after make_btn is defined
        layout.addLayout(row1)
        
        # === Row 2: Edit toolbar ===
        row2 = QHBoxLayout()
        
        def make_btn(txt, tip, icon_name=None) -> QToolButton:
            b = QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.setStyleSheet(self._button_style())
            if icon_name:
                b.setIcon(self._icon(icon_name))
            return b
        
        # PHASE 1: Create Undo/Redo buttons (after make_btn is defined)
        self.btn_undo = make_btn("↺", "Undo (Ctrl+Z)", "reset")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self._undo_edit)
        
        self.btn_redo = make_btn("↻", "Redo (Ctrl+Y)", "reset")
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(self._redo_edit)
        
        # Add undo/redo to row1
        row1.addWidget(self.btn_undo)
        row1.addWidget(self.btn_redo)

        self.btn_zoom_in = make_btn("+","Zoom in")
        self.btn_zoom_out = make_btn("−","Zoom out")

        # wire the editor toolbar zoom buttons to the shared nudge (adjusts the shared slider)
        self.btn_zoom_in.clicked.connect(lambda: (self._nudge_zoom(+1), self._sync_zoom_controls()))
        self.btn_zoom_out.clicked.connect(lambda: (self._nudge_zoom(-1), self._sync_zoom_controls()))        

        self.edit_zoom_field = QLineEdit("100%")
        self.edit_zoom_field.setFixedWidth(60)
        self.edit_zoom_field.setAlignment(Qt.AlignCenter)
        self.edit_zoom_field.setStyleSheet("color:#000;background:#eee;border:1px solid #ccc;")

        self.btn_reset = make_btn("","Reset","reset")

        # Adjustments toggle (replaces brightness quick button)
        self.btn_adjust = make_btn("", "Adjustments", "brightness")
        self.btn_adjust.setToolTip("Open adjustments panel")
        self.btn_adjust.setCheckable(True)
        self.btn_adjust.clicked.connect(self._toggle_right_editor_panel)
                
        # Filters toggle (replaces contrast quick button)
        self.btn_filter = make_btn("", "Filters", "contrast")
        self.btn_filter.setToolTip("Open filters panel")
        self.btn_filter.setCheckable(True)
        self.btn_filter.clicked.connect(self._toggle_filters_panel)
    
        self.btn_crop = make_btn("","Crop","crop")
        self.btn_crop.setCheckable(True)
        self.btn_crop.toggled.connect(self._toggle_crop_mode)
        
        # PHASE 1: Crop aspect ratio preset buttons (Microsoft Photos style)
        self.crop_preset_widget = QWidget()
        crop_preset_layout = QHBoxLayout(self.crop_preset_widget)
        crop_preset_layout.setContentsMargins(0, 0, 0, 0)
        crop_preset_layout.setSpacing(2)
        
        self.btn_crop_free = make_btn("Free", "Freeform crop")
        self.btn_crop_free.setCheckable(True)
        self.btn_crop_free.setChecked(True)
        self.btn_crop_free.clicked.connect(lambda: self._set_crop_aspect(None, "freeform"))
        
        self.btn_crop_1_1 = make_btn("1:1", "Square (1:1)")
        self.btn_crop_1_1.setCheckable(True)
        self.btn_crop_1_1.clicked.connect(lambda: self._set_crop_aspect((1, 1), "1:1"))
        
        self.btn_crop_4_3 = make_btn("4:3", "Standard (4:3)")
        self.btn_crop_4_3.setCheckable(True)
        self.btn_crop_4_3.clicked.connect(lambda: self._set_crop_aspect((4, 3), "4:3"))
        
        self.btn_crop_16_9 = make_btn("16:9", "Widescreen (16:9)")
        self.btn_crop_16_9.setCheckable(True)
        self.btn_crop_16_9.clicked.connect(lambda: self._set_crop_aspect((16, 9), "16:9"))
        
        self.btn_crop_3_2 = make_btn("3:2", "Photo (3:2)")
        self.btn_crop_3_2.setCheckable(True)
        self.btn_crop_3_2.clicked.connect(lambda: self._set_crop_aspect((3, 2), "3:2"))
        
        crop_preset_layout.addWidget(self.btn_crop_free)
        crop_preset_layout.addWidget(self.btn_crop_1_1)
        crop_preset_layout.addWidget(self.btn_crop_4_3)
        crop_preset_layout.addWidget(self.btn_crop_16_9)
        crop_preset_layout.addWidget(self.btn_crop_3_2)
        
        self.crop_preset_widget.hide()  # Hidden until crop mode is activated

        # Crop apply/cancel buttons (initially hidden)
        self.btn_crop_apply = make_btn("Apply","Apply crop")
        self.btn_crop_cancel = make_btn("Cancel","Cancel crop")
        self.btn_crop_apply.hide()
        self.btn_crop_cancel.hide()
        self.btn_crop_apply.clicked.connect(self._apply_crop)
        self.btn_crop_cancel.clicked.connect(lambda: (self.canvas.exit_crop_mode(), self._show_crop_controls(False)))

        self.btn_rotate = make_btn("","Rotate clockwise","rotate")
#        self.btn_brightness = make_btn("", "Brightness", "brightness")  # kept for compatibility
#        self.btn_contrast = make_btn("", "Contrast", "contrast")        


        # Save menu (updated elsewhere to provide Save as copy / Save / Copy to clipboard)
        self.btn_save = make_btn("Save options", "Save options", "save")
        self.btn_save.setPopupMode(QToolButton.InstantPopup)
        # menu wiring is done during page construction in previous code
        # Cancel button will both cancel edits and return to viewer
        menu = QMenu(self.btn_save)

        act_save_copy = QAction("Save as copy...", self)
        act_save_over = QAction("Save", self)
        act_copy_clip = QAction("Copy to clipboard", self)

        act_save_copy.triggered.connect(self._save_as_copy)
        act_save_over.triggered.connect(self._save_overwrite)
        act_copy_clip.triggered.connect(self._copy_working_to_clipboard)

        # "Save" should only be enabled when we have an existing path and a dirty working image
        act_save_over.setEnabled(False)

        menu.addAction(act_save_copy)
        menu.addAction(act_save_over)
        menu.addAction(act_copy_clip)
        self.btn_save.setMenu(menu)
        self._save_action_overwrite = act_save_over  # keep reference to enable/disable based on state


        self.btn_cancel = make_btn("","Cancel","cancel")
#        self.btn_cancel.clicked.connect(self._return_to_viewer)
        # Cancel: discard edits and return
        self.btn_cancel.clicked.connect(lambda: (self._cancel_edits(), self._return_to_viewer()))

        # Assemble toolbar
        for w in [
            self.btn_zoom_in,
            self.btn_zoom_out,
            self.edit_zoom_field,
            self.btn_reset,
            self.btn_adjust,
            self.btn_filter,
            self.btn_crop,
            self.crop_preset_widget,  # PHASE 1: Crop presets
            self.btn_crop_apply,
            self.btn_crop_cancel,
            self.btn_rotate,
            self.btn_save,
            self.btn_cancel
        ]:
            row2.addWidget(w)
        row2.addStretch(1)
        layout.addLayout(row2)

        # === Canvas container (shared) + right-side placeholder for editor/filter panels ===
        # Host both canvas container and right placeholder side-by-side so right panel never underlaps canvas.
        self.edit_canvas_container = QWidget()
        edit_lay = QVBoxLayout(self.edit_canvas_container)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(0)
        
        # create a placeholder widget on the right where the adjustments / filters panel will be attached
        self._editor_right_placeholder = QWidget()
        self._editor_right_placeholder.setFixedWidth(0)  # hidden by default
        self._editor_right_placeholder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        
        # host both in a horizontal row so the right panel sits to the right of the canvas
        content_row = QWidget()
        content_row_layout = QHBoxLayout(content_row)
        content_row_layout.setContentsMargins(0, 0, 0, 0)
        content_row_layout.setSpacing(16)
        content_row_layout.addWidget(self.edit_canvas_container, 1)
        content_row_layout.addWidget(self._editor_right_placeholder, 0)

        layout.addWidget(content_row, 1)
        
        # Create edit_canvas_container inner layout now (this is where the canvas will be reparented on mode switch)
        inner = QVBoxLayout()
        inner.setContentsMargins(8, 8, 8, 8)   # add a small right margin so canvas content doesn't touch placeholder
        inner.setSpacing(0)
        self.edit_canvas_container.setLayout(inner)
    
               
        return page


    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(50)  # Increased height for rating stars
        h = QHBoxLayout(bar)
        h.setContentsMargins(8,0,8,0)
        h.setSpacing(10)

        self.btn_edit = QPushButton("Edit Photo")
        self.btn_edit.setIcon(self._icon("edit"))
        self.btn_edit.setToolTip("Edit photo")
        self.btn_edit.setStyleSheet(self._button_style())
        self.btn_edit.clicked.connect(self._enter_edit_mode)

        self.btn_rotate = QToolButton()
        self.btn_rotate.setIcon(self._icon("rotate"))
        self.btn_rotate.setToolTip("Rotate clockwise")
        self.btn_rotate.setStyleSheet(self._button_style())
        self.btn_rotate.clicked.connect(self._rotate_image)

        # PHASE 1: Add rating widget to top bar
        rating_label = QLabel("Rating:")
        rating_label.setStyleSheet("color:#000;font-size:12px;font-weight:bold;")
        self.rating_widget = RatingWidget(rating=0, parent=self)
        self.rating_widget.ratingChanged.connect(self._on_rating_changed)
        
        # PHASE 1: Add pick/reject buttons
        self.btn_pick = QToolButton()
        self.btn_pick.setText("⭐ Pick")
        self.btn_pick.setToolTip("Mark as Pick (P)")
        self.btn_pick.setCheckable(True)
        self.btn_pick.setStyleSheet(self._button_style())
        self.btn_pick.clicked.connect(lambda: self._on_flag_changed('pick' if self.btn_pick.isChecked() else None))
        
        self.btn_reject = QToolButton()
        self.btn_reject.setText("❌ Reject")
        self.btn_reject.setToolTip("Mark as Reject (X)")
        self.btn_reject.setCheckable(True)
        self.btn_reject.setStyleSheet(self._button_style())
        self.btn_reject.clicked.connect(lambda: self._on_flag_changed('reject' if self.btn_reject.isChecked() else None))

        self.btn_more = QToolButton()
        self.btn_more.setText("...")
        self.btn_more.setToolTip("More actions")
        self.btn_more.setStyleSheet(self._button_style())
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.btn_more)
        act_save = QAction("💾 Save As...", self)
        act_copy = QAction("📋 Copy to Clipboard", self)
        act_open = QAction("📂 Open in Explorer", self)
        menu.addActions([act_save, act_copy, act_open])
        act_save.triggered.connect(self._save_as)
        act_copy.triggered.connect(self._copy_to_clipboard)
        act_open.triggered.connect(self._open_in_explorer)
        self.btn_more.setMenu(menu)

        self.title_label = QLabel(os.path.basename(self._path) if self._path else "")
        self.title_label.setStyleSheet("color:#000;font-size:14px;")

        h.addWidget(self.btn_edit)
        h.addWidget(self.btn_rotate)
        h.addWidget(rating_label)
        h.addWidget(self.rating_widget)
        h.addWidget(self.btn_pick)
        h.addWidget(self.btn_reject)
        h.addWidget(self.btn_more)
        h.addStretch(1)
        h.addWidget(self.title_label)
        h.addStretch(2)

        bar.setStyleSheet(self._button_style() + """
            QWidget {
                background-color: rgba(240,240,240,220);
                border-radius: 8px;
            }
        """)
        bar.setGraphicsEffect(QGraphicsDropShadowEffect(blurRadius=12, offset=QPointF(0,2)))
        return bar

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(46)
        h = QHBoxLayout(bar)
        h.setContentsMargins(0,0,0,0)
        h.setSpacing(6)

        # Photo zoom controls
        self.btn_zoom_minus = QToolButton()
        self.btn_zoom_minus.setText("−")
        self.btn_zoom_minus.setStyleSheet("color:#000; font-size:16px; font-weight:bold;")
        self.btn_zoom_minus.setToolTip("Zoom out")
        self.btn_zoom_minus.clicked.connect(lambda: self._nudge_zoom(-1))

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(-100,200)
        self.zoom_slider.setValue(0)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.valueChanged.connect(self._on_slider_changed)

        self.btn_zoom_plus = QToolButton()
        self.btn_zoom_plus.setText("+")
        self.btn_zoom_plus.setStyleSheet("color:#000; font-size:16px; font-weight:bold;")
        self.btn_zoom_plus.setToolTip("Zoom in")
        self.btn_zoom_plus.clicked.connect(lambda: self._nudge_zoom(+1))

        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setFixedWidth(80)
        for z in [10,25,50,75,100,200,300,400,500,600,700,800]:
            self.zoom_combo.addItem(f"{z}%")
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.editTextChanged.connect(self._on_zoom_combo_changed)
        self.zoom_combo.activated.connect(lambda _: self._on_zoom_combo_changed())

        # === PHASE 1: Professional Video Controls ===

        # Video playback button
        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setStyleSheet(self._button_style())
        self.btn_play_pause.clicked.connect(self._toggle_video_playback)
        self.btn_play_pause.setFixedWidth(80)
        self.btn_play_pause.hide()

        # Video timeline slider (for seeking)
        self.video_timeline_slider = QSlider(Qt.Horizontal)
        self.video_timeline_slider.setRange(0, 1000)  # 0-1000 for smooth seeking
        self.video_timeline_slider.setValue(0)
        self.video_timeline_slider.setFixedWidth(300)
        self.video_timeline_slider.setToolTip("Seek through video")
        try:
            self.video_timeline_slider.sliderMoved.connect(self._on_timeline_slider_moved)
            self.video_timeline_slider.sliderPressed.connect(self._on_timeline_slider_pressed)
            self.video_timeline_slider.sliderReleased.connect(self._on_timeline_slider_released)
        except Exception as e:
            print(f"[LightboxDialog] WARNING: Failed to connect timeline slider signals: {e}")
        self.video_timeline_slider.hide()

        # Video time label
        self.video_position_label = QLabel("0:00 / 0:00")
        self.video_position_label.setStyleSheet("color:#000;font-size:12px;")
        self.video_position_label.setMinimumWidth(90)
        self.video_position_label.hide()

        # Volume mute button
        self.btn_mute = QToolButton()
        self.btn_mute.setText("🔊")
        self.btn_mute.setToolTip("Mute/Unmute (M)")
        self.btn_mute.setCheckable(True)
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_mute.setStyleSheet(self._button_style())
        self.btn_mute.hide()

        # Volume slider
        self.video_volume_slider = QSlider(Qt.Horizontal)
        self.video_volume_slider.setRange(0, 100)
        self.video_volume_slider.setValue(100)  # Default 100%
        self.video_volume_slider.setFixedWidth(80)
        self.video_volume_slider.setToolTip("Volume")
        self.video_volume_slider.valueChanged.connect(self._on_volume_changed)
        self.video_volume_slider.hide()

        # === PHASE 2: Playback Speed Controls ===
        self.btn_playback_speed = QToolButton()
        self.btn_playback_speed.setText("1x")
        self.btn_playback_speed.setToolTip("Playback speed")
        self.btn_playback_speed.setStyleSheet(self._button_style())
        self.btn_playback_speed.setPopupMode(QToolButton.InstantPopup)
        self.btn_playback_speed.setFixedWidth(50)
        self.btn_playback_speed.hide()

        # Speed menu
        speed_menu = QMenu(self.btn_playback_speed)
        self._playback_speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        for speed in self._playback_speeds:
            action = QAction(f"{speed}x", self)
            action.setCheckable(True)
            action.setChecked(speed == 1.0)  # Default 1x
            action.triggered.connect(lambda checked, s=speed: self._set_playback_speed(s))
            speed_menu.addAction(action)
        self.btn_playback_speed.setMenu(speed_menu)
        self._speed_menu = speed_menu
        self._current_playback_speed = 1.0

        # Common info controls
        self.info_label = QLabel()
        self.info_label.setStyleSheet("color:#000;font-size:12px;")
        self.info_label.setText("🖼️ — × —   💾 — KB")

        self.btn_info_toggle = QToolButton()
        self.btn_info_toggle.setIcon(self._icon("info"))
        self.btn_info_toggle.setToolTip("Show detailed info")
        self.btn_info_toggle.setCheckable(True)
        self.btn_info_toggle.toggled.connect(self._toggle_metadata_panel)
        self.btn_info_toggle.setStyleSheet(self._button_style())

        # Add photo controls
        h.addWidget(self.btn_zoom_minus)
        h.addWidget(self.zoom_slider)
        h.addWidget(self.btn_zoom_plus)
        h.addWidget(self.zoom_combo)

        # Add video controls (Apple/Google Photos style layout)
        h.addWidget(self.btn_play_pause)
        h.addWidget(self.video_timeline_slider)
        h.addWidget(self.video_position_label)
        h.addWidget(self.btn_mute)
        h.addWidget(self.video_volume_slider)
        h.addWidget(self.btn_playback_speed)  # PHASE 2

        h.addStretch(1)
        h.addWidget(self.info_label, 0)
        h.addWidget(self.btn_info_toggle, 0)

        bar.setStyleSheet(self._button_style() + """
            QWidget {
                background-color: rgba(240,240,240,220);
                border-radius: 8px;
            }
        """)
        bar.setGraphicsEffect(QGraphicsDropShadowEffect(blurRadius=12, offset=QPointF(0,2)))
        return bar

    # ---------- Metadata panel (polished) ----------
    def _build_metadata_panel(self, data_dict: dict) -> QWidget:
        """
        Metadata panel:
         - black text (as requested)
         - file path wraps over multiple lines
         - copy button on right side for path-like values
        """
        t = getattr(self, "_palette_tokens", {
            "panel": "#ffffff", "fg": "#000000",
            "accent_start": "#0078d4", "button_border": "#dddddd"
        })

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("metaContainer")
        vlayout = QVBoxLayout(container)
        vlayout.setContentsMargins(12, 12, 12, 12)
        vlayout.setSpacing(10)

        container.setStyleSheet(f"""
            QWidget#metaContainer {{ background-color: {t['panel']}; }}
            QLabel.metaTitle {{ color: {t['fg']}; font-weight: 700; font-size: 14px; }}
            QLabel.metaKey {{ color: {t['fg']}; font-weight: 600; font-size: 12px; }}
            QLabel.metaVal {{ color: {t['fg']}; font-size: 12px; }}
            QToolButton.metaCopy {{ border: none; padding: 4px; }}
        """)

        # Header
        header = QWidget()
        hh = QHBoxLayout(header)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(8)
        title = QLabel("Info")
        title.setObjectName("metaTitle")
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        close_btn = QToolButton()
        close_btn.setAutoRaise(True)
        close_btn.setToolTip("Close")
        try:
            close_btn.setIcon(self._icon("cancel"))
        except Exception:
            close_btn.setText("✖")
        close_btn.clicked.connect(lambda: self.btn_info_toggle.setChecked(False))
        hh.addWidget(title)
        hh.addStretch(1)
        hh.addWidget(close_btn)
        vlayout.addWidget(header)

        # === Metadata Editing Section ===
        self._build_metadata_edit_section(vlayout, t)

        # Thumbnail + filename
        top_row = QWidget()
        top_row_layout = QHBoxLayout(top_row)
        top_row_layout.setContentsMargins(0, 0, 0, 0)
        top_row_layout.setSpacing(8)
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(120, 90)  # Larger for video thumbnails
        thumb_lbl.setStyleSheet("background: rgba(0,0,0,0.03); border-radius:4px;")
        thumb_lbl.setAlignment(Qt.AlignCenter)
        
        # 🎬 ENHANCEMENT: Load video thumbnail or photo preview
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            logger.debug(f"Building thumbnail - is_video: {self._is_video}, path: {os.path.basename(self._path) if self._path else 'None'}")
            
            if self._is_video:
                # 🎬 Load video thumbnail using VideoThumbnailService
                # (thumbnails are in .thumb_cache, NOT in database thumbnail_path field)
                try:
                    from services.video_thumbnail_service import get_video_thumbnail_service
                    thumb_service = get_video_thumbnail_service()
                    
                    # Get expected thumbnail path (doesn't query DB)
                    thumb_path = thumb_service.get_thumbnail_path(self._path)
                    
                    logger.info(f"Expected thumbnail path: {thumb_path}")
                    logger.info(f"Thumbnail exists: {thumb_path.exists()}")
                    
                    if thumb_path.exists():
                        logger.info(f"Loading video thumbnail from cache: {thumb_path.name}")
                        # CRITICAL FIX: Use context manager to prevent file handle leaks
                        with Image.open(str(thumb_path)) as thumb_img:
                            # Convert PIL Image to QPixmap
                            from PIL.ImageQt import ImageQt as pil_to_qimage
                            qimg = pil_to_qimage(thumb_img)
                            thumb_pm = QPixmap.fromImage(qimg)
                            if not thumb_pm.isNull():
                                scaled_pm = thumb_pm.scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                thumb_lbl.setPixmap(scaled_pm)
                                # Add play icon overlay style
                                thumb_lbl.setStyleSheet(
                                    "background: rgba(0,0,0,0.8); border-radius:4px; "
                                    "border: 2px solid rgba(255,255,255,0.1);"
                                )
                                logger.debug("✅ Video thumbnail loaded successfully")
                            else:
                                logger.warning("QPixmap is null after loading thumbnail")
                                # Show placeholder
                                thumb_lbl.setText("🎬")
                                thumb_lbl.setStyleSheet(
                                    "background: rgba(50,50,50,0.9); border-radius:4px; "
                                    "border: 2px solid rgba(100,100,100,0.5); "
                                    "color: white; font-size: 48px;"
                                )
                                thumb_lbl.setAlignment(Qt.AlignCenter)
                    else:
                        # No thumbnail - show generic video icon
                        logger.info(f"No thumbnail file found - showing placeholder")
                        thumb_lbl.setText("🎬")
                        thumb_lbl.setStyleSheet(
                            "background: rgba(50,50,50,0.9); border-radius:4px; "
                            "border: 2px solid rgba(100,100,100,0.5); "
                            "color: white; font-size: 48px;"
                        )
                        thumb_lbl.setAlignment(Qt.AlignCenter)
                except Exception as e:
                    logger.error(f"Failed to load video thumbnail: {e}")
                    # Show placeholder
                    thumb_lbl.setText("🎬")
                    thumb_lbl.setStyleSheet(
                        "background: rgba(50,50,50,0.9); border-radius:4px; "
                        "border: 2px solid rgba(100,100,100,0.5); "
                        "color: white; font-size: 48px;"
                    )
                    thumb_lbl.setAlignment(Qt.AlignCenter)
            else:
                # Load photo preview
                if getattr(self.canvas, "_pixmap", None):
                    pm = self.canvas._pixmap
                    if not pm.isNull():
                        tpm = pm.scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        thumb_lbl.setPixmap(tpm)
                        logger.debug("Photo thumbnail loaded from canvas")
        except Exception as e:
            logger.error(f"Failed to load thumbnail: {e}", exc_info=True)
        name_val = data_dict.get("File", os.path.basename(self._path) if self._path else "")
        name_widget = QWidget()
        nw = QVBoxLayout(name_widget)
        nw.setContentsMargins(0, 0, 0, 0)
        nw.setSpacing(4)
        name_lbl = QLabel(str(name_val))
        name_lbl.setObjectName("metaKey")
        name_lbl.setWordWrap(False)
        name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        desc_field = QLineEdit()
        desc_field.setPlaceholderText("Add a description")
        desc_field.setFixedHeight(28)
        desc_field.setStyleSheet(f"color: {t['fg']}; background: rgba(0,0,0,0.02); border: 1px solid rgba(0,0,0,0.06); padding-left:6px;")
        nw.addWidget(name_lbl)
        nw.addWidget(desc_field)
        top_row_layout.addWidget(thumb_lbl)
        top_row_layout.addWidget(name_widget, 1)
        vlayout.addWidget(top_row)

        # Grid rows
        grid = QGridLayout()
        grid.setColumnStretch(0, 0)
        grid.setColumnMinimumWidth(0, 28)
        grid.setColumnMinimumWidth(1, 120)
        grid.setColumnStretch(2, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(12)
        grid.setContentsMargins(0, 8, 0, 0)

        icons = {
            "File": "📄", "Folder": "📁", "Size": "📏", "Modified": "🕓",
            "Created": "🗓️", "Captured": "🕰️", "Camera Make": "🏭",
            "Camera Model": "📷", "Lens": "🔍", "Aperture": "ƒ",
            "Exposure": "⏱", "ISO": "ISO", "Focal Length": "🔭",
            "Software": "💻", "Tags": "🏷️", "Database": "💾", "Info": "•",
            # 🎬 Video metadata icons
            "Duration": "⏱️", "Resolution": "📺", "Frame Rate": "🎬",
            "Codec": "🎬", "Bitrate": "📊", "Date Taken": "📅",
            "Video Metadata": "🎬", "Metadata": "✅", "Thumbnail": "🖼️"
        }

        row = 0
        for key, val in data_dict.items():
            if key == "File":
                continue
            icon_text = icons.get(key, "•")
            icon_lbl = QLabel(icon_text)
            icon_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            icon_lbl.setFixedWidth(28)
            key_lbl = QLabel(f"{key}")
            key_lbl.setObjectName("metaKey")
            key_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            val_widget = QWidget()
            vbox = QHBoxLayout(val_widget)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(6)
            val_lbl = QLabel(str(val))
            val_lbl.setObjectName("metaVal")
            val_lbl.setWordWrap(True)  # enable wrapping for long paths
            val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            vbox.addWidget(val_lbl, 1)
            val_str = str(val)
            if "\\" in val_str or "/" in val_str or ":" in val_str:
                vbox.addStretch(1)
                copy_btn = QToolButton()
                copy_btn.setAutoRaise(True)
                copy_btn.setObjectName("metaCopy")
                copy_btn.setToolTip("Copy to clipboard")
                try:
                    copy_btn.setIcon(self._icon("copy"))
                except Exception:
                    copy_btn.setText("📋")
                copy_btn.clicked.connect(lambda _, s=val_str: QApplication.clipboard().setText(s))
                vbox.addWidget(copy_btn)
            grid.addWidget(icon_lbl, row, 0, Qt.AlignTop)
            grid.addWidget(key_lbl, row, 1, Qt.AlignTop)
            grid.addWidget(val_widget, row, 2, Qt.AlignTop)
            row += 1

        vlayout.addLayout(grid)
        vlayout.addStretch(1)

        scroll.setWidget(container)
        scroll.setMinimumWidth(360)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {t['panel']}; border-left: 1px solid {t['button_border']}; }}")

        return scroll

    # ---------- Metadata editing helpers ----------

    def _get_photo_id_for_current_path(self) -> int:
        """Get photo ID from database for the current media path."""
        if not self._path:
            return None
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id FROM photo_metadata WHERE path = ?", (self._path,))
                row = cursor.fetchone()
                if row:
                    return row['id']
                # Case-insensitive fallback
                cursor = conn.execute(
                    "SELECT id FROM photo_metadata WHERE LOWER(path) = LOWER(?)", (self._path,))
                row = cursor.fetchone()
                return row['id'] if row else None
        except Exception as e:
            print(f"[LightboxDialog] Error getting photo ID for {self._path}: {e}")
            return None

    def _build_metadata_edit_section(self, parent_layout, tokens: dict):
        """Build the metadata editing section (rating, flag, title, caption, keywords) inside the metadata panel."""
        from PySide6.QtWidgets import QGroupBox, QToolButton, QLineEdit, QTextEdit

        self._meta_edit_loading = True  # Prevent save during population

        fg = tokens.get('fg', '#000000')
        accent = tokens.get('accent_start', '#0078d4')
        border = tokens.get('button_border', '#dddddd')

        group = QGroupBox("Edit Metadata")
        group.setStyleSheet(f"""
            QGroupBox {{
                font-weight: bold; font-size: 12px;
                border: 1px solid {border}; border-radius: 6px;
                margin-top: 8px; padding: 12px 8px 8px 8px;
                color: {fg};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 10px;
                padding: 0 4px; color: {fg};
            }}
        """)
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        input_style = f"""
            color: {fg};
            background: rgba(0,0,0,0.03);
            border: 1px solid {border};
            border-radius: 4px;
            padding: 4px 6px;
            font-size: 11px;
        """

        # Rating row
        rating_row = QHBoxLayout()
        rating_row.setSpacing(2)
        rating_lbl = QLabel("Rating:")
        rating_lbl.setStyleSheet(f"color: {fg}; font-size: 11px; font-weight: bold;")
        rating_row.addWidget(rating_lbl)
        self._meta_edit_rating_buttons = []
        self._meta_edit_current_rating = 0
        for i in range(5):
            btn = QToolButton()
            btn.setFixedSize(24, 24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setText("☆")
            btn.setStyleSheet(f"""
                QToolButton {{ border: none; background: transparent; font-size: 14px; color: #ccc; }}
                QToolButton:hover {{ background: rgba(255,193,7,0.15); border-radius: 4px; }}
            """)
            btn.clicked.connect(lambda checked, idx=i: self._on_meta_edit_star_clicked(idx))
            self._meta_edit_rating_buttons.append(btn)
            rating_row.addWidget(btn)

        clear_btn = QToolButton()
        clear_btn.setText("✕")
        clear_btn.setFixedSize(18, 18)
        clear_btn.setToolTip("Clear rating")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"QToolButton {{ border: none; color: #999; font-size: 10px; }} QToolButton:hover {{ color: {fg}; }}")
        clear_btn.clicked.connect(lambda: self._on_meta_edit_set_rating(0, save=True))
        rating_row.addWidget(clear_btn)
        rating_row.addStretch()
        group_layout.addLayout(rating_row)

        # Flag row
        flag_row = QHBoxLayout()
        flag_row.setSpacing(4)
        flag_lbl = QLabel("Flag:")
        flag_lbl.setStyleSheet(f"color: {fg}; font-size: 11px; font-weight: bold;")
        flag_row.addWidget(flag_lbl)
        self._meta_edit_current_flag = "none"

        self._meta_edit_flag_pick = QPushButton("⬆ Pick")
        self._meta_edit_flag_pick.setCheckable(True)
        self._meta_edit_flag_pick.setFixedHeight(24)
        self._meta_edit_flag_pick.setStyleSheet(f"""
            QPushButton {{ border: 1px solid {border}; border-radius: 4px; padding: 2px 8px; font-size: 10px; color: {fg}; background: transparent; }}
            QPushButton:hover {{ background: rgba(76,175,80,0.1); }}
            QPushButton:checked {{ background: rgba(76,175,80,0.2); color: #4CAF50; border-color: #4CAF50; }}
        """)
        self._meta_edit_flag_pick.clicked.connect(lambda: self._on_meta_edit_flag_clicked("pick"))
        flag_row.addWidget(self._meta_edit_flag_pick)

        self._meta_edit_flag_reject = QPushButton("⬇ Reject")
        self._meta_edit_flag_reject.setCheckable(True)
        self._meta_edit_flag_reject.setFixedHeight(24)
        self._meta_edit_flag_reject.setStyleSheet(f"""
            QPushButton {{ border: 1px solid {border}; border-radius: 4px; padding: 2px 8px; font-size: 10px; color: {fg}; background: transparent; }}
            QPushButton:hover {{ background: rgba(244,67,54,0.1); }}
            QPushButton:checked {{ background: rgba(244,67,54,0.2); color: #F44336; border-color: #F44336; }}
        """)
        self._meta_edit_flag_reject.clicked.connect(lambda: self._on_meta_edit_flag_clicked("reject"))
        flag_row.addWidget(self._meta_edit_flag_reject)
        flag_row.addStretch()
        group_layout.addLayout(flag_row)

        # Title
        title_lbl = QLabel("Title:")
        title_lbl.setStyleSheet(f"color: {fg}; font-size: 11px; font-weight: bold;")
        group_layout.addWidget(title_lbl)
        self._meta_edit_title = QLineEdit()
        self._meta_edit_title.setPlaceholderText("Add a title...")
        self._meta_edit_title.setStyleSheet(input_style)
        self._meta_edit_title.editingFinished.connect(
            lambda: self._on_meta_edit_field_changed("title", self._meta_edit_title.text()))
        group_layout.addWidget(self._meta_edit_title)

        # Caption
        caption_lbl = QLabel("Caption:")
        caption_lbl.setStyleSheet(f"color: {fg}; font-size: 11px; font-weight: bold;")
        group_layout.addWidget(caption_lbl)
        self._meta_edit_caption = QTextEdit()
        self._meta_edit_caption.setPlaceholderText("Add a description...")
        self._meta_edit_caption.setMaximumHeight(60)
        self._meta_edit_caption.setStyleSheet(input_style)
        self._meta_edit_caption.textChanged.connect(
            lambda: self._on_meta_edit_field_changed("caption", self._meta_edit_caption.toPlainText()))
        group_layout.addWidget(self._meta_edit_caption)

        # Keywords
        kw_lbl = QLabel("Keywords:")
        kw_lbl.setStyleSheet(f"color: {fg}; font-size: 11px; font-weight: bold;")
        group_layout.addWidget(kw_lbl)
        self._meta_edit_tags = QLineEdit()
        self._meta_edit_tags.setPlaceholderText("tag1, tag2, tag3...")
        self._meta_edit_tags.setStyleSheet(input_style)
        self._meta_edit_tags.editingFinished.connect(
            lambda: self._on_meta_edit_field_changed("tags", self._meta_edit_tags.text()))
        group_layout.addWidget(self._meta_edit_tags)

        # Save status
        self._meta_edit_save_status = QLabel("")
        self._meta_edit_save_status.setStyleSheet(f"color: #4CAF50; font-size: 10px;")
        self._meta_edit_save_status.setAlignment(Qt.AlignCenter)
        group_layout.addWidget(self._meta_edit_save_status)

        parent_layout.addWidget(group)

        # Load current photo's metadata into fields
        self._refresh_meta_edit_fields()

    def _refresh_meta_edit_fields(self):
        """Load the current photo's editable metadata into the edit fields."""
        self._meta_edit_loading = True
        try:
            photo_id = self._get_photo_id_for_current_path()
            self._meta_edit_photo_id = photo_id

            if not photo_id:
                self._on_meta_edit_set_rating(0)
                self._on_meta_edit_set_flag("none")
                if hasattr(self, '_meta_edit_title'):
                    self._meta_edit_title.clear()
                if hasattr(self, '_meta_edit_caption'):
                    self._meta_edit_caption.clear()
                if hasattr(self, '_meta_edit_tags'):
                    self._meta_edit_tags.clear()
                return

            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT rating, flag, title, caption, tags
                    FROM photo_metadata WHERE id = ?
                """, (photo_id,))
                row = cursor.fetchone()

            if row:
                self._on_meta_edit_set_rating(row['rating'] or 0)
                self._on_meta_edit_set_flag(row['flag'] or 'none')
                if hasattr(self, '_meta_edit_title'):
                    self._meta_edit_title.setText(row['title'] or '')
                if hasattr(self, '_meta_edit_caption'):
                    self._meta_edit_caption.setPlainText(row['caption'] or '')
                if hasattr(self, '_meta_edit_tags'):
                    self._meta_edit_tags.setText(row['tags'] or '')
            else:
                self._on_meta_edit_set_rating(0)
                self._on_meta_edit_set_flag("none")
        except Exception as e:
            print(f"[LightboxDialog] Error loading editable metadata: {e}")
        finally:
            self._meta_edit_loading = False

    def _on_meta_edit_star_clicked(self, idx: int):
        """Handle star click in rating widget."""
        new_rating = idx + 1
        if getattr(self, '_meta_edit_current_rating', 0) == new_rating:
            new_rating = 0
        self._on_meta_edit_set_rating(new_rating, save=True)

    def _on_meta_edit_set_rating(self, rating: int, save: bool = False):
        """Set the rating display."""
        self._meta_edit_current_rating = rating
        if hasattr(self, '_meta_edit_rating_buttons'):
            for i, btn in enumerate(self._meta_edit_rating_buttons):
                if i < rating:
                    btn.setText("★")
                    btn.setStyleSheet("""
                        QToolButton { border: none; background: transparent; font-size: 14px; color: #FFC107; }
                        QToolButton:hover { background: rgba(255,193,7,0.15); border-radius: 4px; }
                    """)
                else:
                    btn.setText("☆")
                    btn.setStyleSheet("""
                        QToolButton { border: none; background: transparent; font-size: 14px; color: #ccc; }
                        QToolButton:hover { background: rgba(255,193,7,0.15); border-radius: 4px; }
                    """)
        if save and not getattr(self, '_meta_edit_loading', False):
            self._on_meta_edit_field_changed("rating", rating)

    def _on_meta_edit_flag_clicked(self, flag: str):
        """Handle flag button click."""
        current = getattr(self, '_meta_edit_current_flag', 'none')
        if current == flag:
            flag = "none"
        self._on_meta_edit_set_flag(flag)
        if not getattr(self, '_meta_edit_loading', False):
            self._on_meta_edit_field_changed("flag", flag)

    def _on_meta_edit_set_flag(self, flag: str):
        """Set the flag display."""
        self._meta_edit_current_flag = flag
        if hasattr(self, '_meta_edit_flag_pick'):
            self._meta_edit_flag_pick.setChecked(flag == "pick")
        if hasattr(self, '_meta_edit_flag_reject'):
            self._meta_edit_flag_reject.setChecked(flag == "reject")

    def _on_meta_edit_field_changed(self, field: str, value):
        """Handle metadata field change - save to database."""
        if getattr(self, '_meta_edit_loading', False):
            return
        photo_id = getattr(self, '_meta_edit_photo_id', None)
        if not photo_id:
            return

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            column_map = {
                "rating": "rating", "flag": "flag",
                "title": "title", "caption": "caption", "tags": "tags",
            }
            column = column_map.get(field)
            if not column:
                return

            with db.get_connection() as conn:
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

            if hasattr(self, '_meta_edit_save_status'):
                self._meta_edit_save_status.setText("✓ Saved")
                QTimer.singleShot(2000, lambda: self._meta_edit_save_status.setText("")
                                  if hasattr(self, '_meta_edit_save_status') else None)
            print(f"[LightboxDialog] Saved {field}={value} for photo_id={photo_id}")
        except Exception as e:
            print(f"[LightboxDialog] Error saving metadata {field}: {e}")
            if hasattr(self, '_meta_edit_save_status'):
                self._meta_edit_save_status.setText("⚠ Save failed")

    def _toggle_metadata_panel(self, show: bool):
        """Toggle right metadata panel (polished) with video support."""
        import logging
        logger = logging.getLogger(__name__)
        
        if show:
            meta_dict = self._parse_metadata_to_dict(self._get_metadata_text())
            panel = self._build_metadata_panel(meta_dict)
            panel.setMinimumWidth(360)
            panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self._meta_panel = panel

            layout = self._meta_placeholder.layout()
            if layout is None:
                layout = QVBoxLayout(self._meta_placeholder)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)

            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)

            layout.addWidget(panel)
            self._meta_placeholder.setFixedWidth(360)
            self.btn_info_toggle.setToolTip("Hide detailed info")
            
            logger.info(f"Showing metadata panel (is_video={self._is_video}, {len(meta_dict)} fields)")
        else:
            if hasattr(self, "_meta_placeholder"):
                self._meta_placeholder.setFixedWidth(0)
            self.btn_info_toggle.setToolTip("Show detailed info")

    def _get_metadata_text(self):
        if self._path in self._meta_cache:
            return self._meta_cache[self._path]
        text = self._load_metadata(self._path)
        self._meta_cache[self._path] = text
        self._preload_metadata_async()
        return text

    def _parse_metadata_to_dict(self, text: str) -> dict:
        """Parse metadata text into dictionary, handling video metadata correctly."""
        import logging
        logger = logging.getLogger(__name__)
        
        meta = {}
        logger.debug(f"Parsing metadata text ({len(text)} chars)")
        logger.debug(f"First 500 chars: {text[:500]}")
        
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("⚠️"):
                continue
            
            # Skip section headers and category labels (no colon) and indented notes
            if not ":" in line or line.startswith("   "):
                continue
            
            line = line.lstrip("•").strip()
            key, val = line.split(":", 1)
            key, val = key.strip(), val.strip()
            
            # Clean up emoji prefixes from keys
            key = (
                key.replace("📄", "File")
                   .replace("📁", "Folder")
                   .replace("📏", "Size")
                   .replace("🕓", "Modified")
                   .replace("🗓️", "Created")
                   .replace("🕰️", "Captured")
                   .replace("🏭", "Camera Make")
                   .replace("📷", "Camera Model")
                   .replace("🔍", "Lens")
                   .replace("ƒ", "Aperture")
                   .replace("⏱", "Duration")  # Video duration
                   .replace("ISO", "ISO")
                   .replace("🔭", "Focal Length")
                   .replace("💻", "Software")
                   .replace("💾", "Database")
                   .replace("🏷️", "Tags")
                   .replace("🎬", "Video")
                   .replace("📐", "Resolution")
                   .replace("🎞️", "Frame Rate")
                   .replace("🎥", "Codec")
                   .replace("📊", "Bitrate")
                   .replace("📅", "Date Taken")
                   .replace("✅", "")
                   .replace("⏳", "")
                   .replace("❌", "")
                   .replace("❓", "")
                   .strip()
            )
            
            # Deduplicate words in key
            words = key.split()
            key = " ".join(sorted(set(words), key=words.index))
            
            meta[key] = val
        
        logger.debug(f"Final metadata dict has {len(meta)} entries: {list(meta.keys())}")
        return meta

    def _load_metadata(self, path: str) -> str:
        lines = []
        base = os.path.basename(path) if path else ""
        folder = os.path.dirname(path) if path else ""
        lines.append(f"📄 {base}")
        lines.append(f"📁 {folder}")
        lines.append("")

        # Check if this is a video file
        is_video = self._is_video_file(path)

        try:
            st = os.stat(path)
            dt_mod = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            dt_crt = datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            lines += [
                f"📏 File size: {st.st_size/1024:.1f} KB",
                f"🕓 Modified: {dt_mod}",
                f"🗓️ Created: {dt_crt}"
            ]
        except Exception as e:
            lines.append(f"⚠️ File info error: {e}")
        lines.append("")

        if is_video:
            # 🎬 Load COMPREHENSIVE video metadata from database
            import logging
            logger = logging.getLogger(__name__)
            
            try:
                db = self._shared_db_instance
                if not db and self.parent():
                    # Try 'db' first (MainWindow), then 'reference_db' (fallback)
                    db = getattr(self.parent(), "db", None) or getattr(self.parent(), "reference_db", None)
                
                logger.info(f"Video detected: {os.path.basename(path)}")
                logger.debug(f"DB instance: {db}")
                
                if db:
                    # Get project_id from stored reference or parent
                    project_id = self._project_id
                    if not project_id and self.parent():
                        project_id = getattr(self.parent(), "project_id", None)
                    logger.debug(f"Project ID: {project_id}")
                    
                    if project_id:
                        video_meta = db.get_video_by_path(path, project_id)
                        logger.info(f"Video metadata from DB: {bool(video_meta)}")
                        logger.debug(f"Video metadata keys: {list(video_meta.keys()) if video_meta else None}")
                        
                        if video_meta:
                            lines.append("🎬 Video Metadata:")

                            # Duration (formatted as MM:SS or H:MM:SS)
                            if video_meta.get('duration_seconds'):
                                duration_str = self._format_duration(video_meta['duration_seconds'])
                                lines.append(f"⏱ Duration: {duration_str}")

                            # Resolution
                            if video_meta.get('width') and video_meta.get('height'):
                                res = f"{video_meta['width']}×{video_meta['height']}"
                                lines.append(f"📐 Resolution: {res}")
                                
                                # Add resolution category
                                w, h = video_meta['width'], video_meta['height']
                                if w >= 3840 and h >= 2160:
                                    lines.append(f"   (4K Ultra HD)")
                                elif w >= 1920 and h >= 1080:
                                    lines.append(f"   (Full HD 1080p)")
                                elif w >= 1280 and h >= 720:
                                    lines.append(f"   (HD 720p)")
                                else:
                                    lines.append(f"   (SD)")

                            # FPS
                            if video_meta.get('fps'):
                                lines.append(f"🎞️ Frame Rate: {video_meta['fps']:.2f} fps")

                            # Codec
                            if video_meta.get('codec'):
                                lines.append(f"🎥 Codec: {video_meta['codec']}")

                            # Bitrate
                            if video_meta.get('bitrate'):
                                bitrate_mbps = video_meta['bitrate'] / 1_000_000
                                lines.append(f"📊 Bitrate: {bitrate_mbps:.2f} Mbps")

                            # Date taken
                            if video_meta.get('date_taken'):
                                lines.append(f"📅 Date Taken: {video_meta['date_taken']}")
                            
                            # 📂 Folder info
                            if video_meta.get('folder_id'):
                                lines.append(f"📂 Folder ID: {video_meta['folder_id']}")
                            
                            # 🔖 Metadata status
                            if video_meta.get('metadata_status'):
                                status_emoji = {'ready': '✅', 'pending': '⏳', 'error': '❌'}.get(video_meta['metadata_status'], '❓')
                                lines.append(f"{status_emoji} Metadata: {video_meta['metadata_status']}")
                            
                            # 🖼️ Thumbnail status
                            if video_meta.get('thumbnail_status'):
                                thumb_emoji = {'ready': '✅', 'pending': '⏳', 'error': '❌'}.get(video_meta['thumbnail_status'], '❓')
                                lines.append(f"{thumb_emoji} Thumbnail: {video_meta['thumbnail_status']}")
                            
                            logger.info(f"Added {len(lines)-7} video metadata lines")  # -7 for file info + empty
                        else:
                            logger.warning(f"No video metadata found in database for: {os.path.basename(path)}")
                            lines.append("⚠️ No video metadata found in database.")
                            lines.append("   (Video may need to be scanned)")
                    else:
                        logger.warning("No project context available")
                        lines.append("⚠️ No project context available.")
                else:
                    logger.warning("No database instance available")
                    lines.append("⚠️ No database connection available.")
            except Exception as e:
                logger.error(f"ERROR loading video metadata: {e}", exc_info=True)
                lines.append(f"⚠️ Video metadata error: {e}")
        else:
            # Load photo EXIF metadata
            try:
                img = Image.open(path)
                exif = img._getexif() or {}
                if exif:
                    exif_tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                    wanted = [
                        ("DateTimeOriginal", "🕰️ Captured"),
                        ("Make", "🏭 Camera Make"),
                        ("Model", "📷 Camera Model"),
                        ("LensModel", "🔍 Lens"),
                        ("FNumber", "ƒ Aperture"),
                        ("ExposureTime", "⏱ Exposure"),
                        ("ISOSpeedRatings", "ISO"),
                        ("FocalLength", "🔭 Focal Length"),
                        ("Software", "💻 Processed by"),
                    ]
                    for key, label in wanted:
                        if key in exif_tags:
                            lines.append(f"{label}: {exif_tags[key]}")
                else:
                    lines.append("⚠️ No EXIF metadata found.")
            except Exception:
                lines.append("⚠️ Failed to read EXIF info.")

        lines.append("")
        try:
            db = getattr(self, "_shared_db_instance", None)
            if not db and hasattr(self.parent(), "reference_db"):
                db = self.parent().reference_db
            if db and not is_video:  # Only for photos (videos handled above)
                record = db.get_photo_metadata_by_path(path)
                if record:
                    lines.append("💾 Database Metadata:")
                    for k, v in record.items():
                        lines.append(f"   • {k}: {v}")
                else:
                    lines.append("⚠️ No record found in database.")
        except Exception as e:
            lines.append(f"⚠️ DB error: {e}")

        return "\n".join(lines)

    def _is_video_file(self, path: str) -> bool:
        """Check if file is a video based on extension."""
        if not path:
            return False
        ext = os.path.splitext(path)[1].lower()
        video_exts = {'.mp4', '.m4v', '.mov', '.mpeg', '.mpg', '.mpe', '.wmv',
                      '.avi', '.mkv', '.flv', '.webm', '.3gp', '.ogv', '.ts', '.mts'}
        return ext in video_exts

    def _format_duration(self, seconds: float) -> str:
        """Format duration as MM:SS or H:MM:SS."""
        if not seconds or seconds < 0:
            return "0:00"

        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def _preload_metadata_async(self):
        if self._meta_preloader and self._meta_preloader.is_alive():
            return
        from threading import Thread
        def worker():
            try:
                radius = 5
                total = len(self._image_list)
                for offset in range(-radius, radius + 1):
                    if self._preload_stop:
                        break
                    idx = self._current_index + offset
                    if idx < 0 or idx >= total:
                        continue
                    path = self._image_list[idx]
                    if path not in self._meta_cache:
                        text = self._load_metadata(path)
                        self._meta_cache[path] = text
                if len(self._meta_cache) > 2000:
                    to_remove = list(self._meta_cache.keys())[:200]
                    for k in to_remove:
                        self._meta_cache.pop(k, None)
            except Exception as e:
                print(f"[Preload] Error: {e}")
        self._meta_preloader = Thread(target=worker, daemon=True)
        self._meta_preloader.start()

    # -------------------------------
    # Load image and store original PIL image
    # -------------------------------
    def _load_media(self, path: str):
        """Unified media loader: detects media type and routes to appropriate handler."""
        if self._is_video_file(path):
            self._is_video = True
            self._current_media_type = "video"
            self._load_video(path)
            self._update_controls_visibility()
        else:
            self._is_video = False
            self._current_media_type = "photo"
            self._load_photo(path)
            self._update_controls_visibility()

    def _load_video(self, path: str):
        """Load and display video in the unified preview panel with thumbnail preview."""
        try:
            print(f"[LightboxDialog] Loading video: {path}")

            # Ensure path is valid
            if not os.path.exists(path):
                raise FileNotFoundError(f"Video file not found: {path}")

            # 🔧 Store video path for GPU crash recovery
            self._last_video_path = path
            self._video_error_count = 0  # Reset error count for new video
            self._gpu_warning_shown = False  # Reset warning flag for new video

            # 🎬 IMPROVEMENT 1: Show thumbnail preview while video loads
            self._show_video_loading_state(path)

            # Switch to video widget page
            self.content_stack.setCurrentIndex(1)
            print(f"[LightboxDialog] Switched to video widget (index 1)")

            # Stop any currently playing video
            if hasattr(self, 'media_player'):
                self.media_player.stop()

            # Load video - use absolute path
            abs_path = os.path.abspath(path)
            video_url = QUrl.fromLocalFile(abs_path)
            print(f"[LightboxDialog] Video URL: {video_url.toString()}")

            self.media_player.setSource(video_url)

            # Update window title
            filename = os.path.basename(path)
            if self._image_list:
                idx_display = self._current_index + 1
                total = len(self._image_list)
                self.setWindowTitle(f"📹 {filename} ({idx_display} of {total})")
            else:
                self.setWindowTitle(f"📹 {filename}")

            # 🎬 IMPROVEMENT 3: Enhanced video info display with metadata
            self._update_video_info_label(path)

            # Auto-play video
            self.media_player.play()
            print(f"[LightboxDialog] Video playback started")

        except Exception as e:
            print(f"[LightboxDialog] Error loading video: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Load failed", f"Couldn't load video: {e}")

    # Max dimension for preview decode — matches SafeImageLoader contract.
    # Never decode full resolution into RAM; originals only at export/save time.
    _PREVIEW_MAX_DIM = 2560

    def _load_photo(self, path: str):
        """Load and display photo at capped preview resolution.

        Follows the Smart Preview model (like Lightroom):
        - Display/edit at capped resolution (2560px max edge)
        - Original file only re-opened at full res for save/export
        - Uses PIL draft() for JPEG pre-decode downscaling
        """
        try:
            print(f"[PhotoLoad] Loading photo: {path}")
            # Switch to image canvas page
            self.content_stack.setCurrentIndex(0)

            img = Image.open(path)
            orig_size = img.size

            # Pre-decode downscale: tell JPEG decoder to output smaller
            # (draft is a no-op for non-JPEG but never errors)
            if max(orig_size) > self._PREVIEW_MAX_DIM:
                try:
                    img.draft('RGB', (self._PREVIEW_MAX_DIM, self._PREVIEW_MAX_DIM))
                except Exception:
                    pass

            img = ImageOps.exif_transpose(img)

            # Ensure final size is capped (draft gives approximate sizes)
            if max(img.size) > self._PREVIEW_MAX_DIM:
                img.thumbnail((self._PREVIEW_MAX_DIM, self._PREVIEW_MAX_DIM), Image.LANCZOS)

            img = img.convert("RGBA")
            self._orig_pil = img
            self._orig_file_dimensions = orig_size  # track original for save

            print(f"[PhotoLoad] Preview loaded: {self._orig_pil.size} "
                  f"(original {orig_size[0]}x{orig_size[1]})")

            qimg = ImageQt.ImageQt(img)
            pm = QPixmap.fromImage(qimg)
            self.canvas.set_pixmap(pm)
            self.canvas.set_before_pixmap(pm)
            self._update_info(pm)
            # Reset rotation preview state on new photo
            self._rotation_preview_base = None
            if hasattr(self, 'rotation_slider_widget'):
                self.rotation_slider_widget.reset()
        except Exception as e:
            print(f"[PhotoLoad] Failed to load photo: {e}")
            self._orig_pil = None
            self._orig_file_dimensions = None
            QMessageBox.warning(self, "Load failed", f"Couldn't load image: {e}")

    def _load_image(self, path: str):
        """Legacy wrapper for backwards compatibility - calls _load_photo()."""
        self._load_photo(path)

    def _update_controls_visibility(self):
        """Hide/show controls based on current media type."""
        is_photo = self._current_media_type == "photo"
        is_video = self._current_media_type == "video"

        # Top bar controls (photo-only)
        if hasattr(self, 'btn_edit'):
            self.btn_edit.setVisible(is_photo)
        if hasattr(self, 'btn_rotate'):
            self.btn_rotate.setVisible(is_photo)

        # Bottom bar zoom controls (photo-only)
        if hasattr(self, 'btn_zoom_minus'):
            self.btn_zoom_minus.setVisible(is_photo)
        if hasattr(self, 'zoom_slider'):
            self.zoom_slider.setVisible(is_photo)
        if hasattr(self, 'btn_zoom_plus'):
            self.btn_zoom_plus.setVisible(is_photo)
        if hasattr(self, 'zoom_combo'):
            self.zoom_combo.setVisible(is_photo)

        # Bottom bar video controls (video-only) - PHASE 1 & 2 ENHANCED
        if hasattr(self, 'btn_play_pause'):
            self.btn_play_pause.setVisible(is_video)
        if hasattr(self, 'video_timeline_slider'):
            self.video_timeline_slider.setVisible(is_video)
        if hasattr(self, 'video_position_label'):
            self.video_position_label.setVisible(is_video)
        if hasattr(self, 'btn_mute'):
            self.btn_mute.setVisible(is_video)
        if hasattr(self, 'video_volume_slider'):
            self.video_volume_slider.setVisible(is_video)
        if hasattr(self, 'btn_playback_speed'):  # PHASE 2
            self.btn_playback_speed.setVisible(is_video)

    def _toggle_video_playback(self):
        """Toggle video play/pause."""
        if not hasattr(self, 'media_player'):
            return

        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.btn_play_pause.setText("▶ Play")
        else:
            self.media_player.play()
            self.btn_play_pause.setText("⏸ Pause")

    def _on_video_playback_state_changed(self, state):
        """Handle video playback state changes."""
        state_names = {
            QMediaPlayer.PlaybackState.StoppedState: "Stopped",
            QMediaPlayer.PlaybackState.PlayingState: "Playing",
            QMediaPlayer.PlaybackState.PausedState: "Paused"
        }
        state_name = state_names.get(state, "Unknown")
        print(f"[LightboxDialog] Video playback state: {state_name}")

        # Update button text based on state
        if hasattr(self, 'btn_play_pause'):
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.btn_play_pause.setText("⏸ Pause")
            else:
                self.btn_play_pause.setText("▶ Play")

    def _on_video_error(self, error, error_string):
        """Handle media player errors with GPU crash recovery."""
        
        # 🔧 GPU CRASH RECOVERY: Detect GPU device loss
        is_gpu_crash = (
            "GPU device instance has been suspended" in error_string or
            "Device loss detected" in error_string or
            "COM error 0x887a0005" in error_string or
            "Unable to copy frame from decoder pool" in error_string or
            "failed to get textures for frame" in error_string or
            "Failed to create QRhiTexture" in error_string
        )
        
        if is_gpu_crash:
            # 🔇 Suppress Qt spam - only log once and show clean message
            if self._video_error_count == 0:
                print(f"[LightboxDialog] ⚠️ GPU device error detected - attempting recovery...")
            
            self._video_error_count += 1
            
            # Debounce: wait for errors to stop before showing message
            if not self._gpu_warning_shown:
                self._gpu_error_timer.start(1000)  # Wait 1 second of silence
            
            # Try to recover by recreating the media player
            if self._video_error_count <= 3 and self._last_video_path:
                # Silent recovery attempt (no spam)
                if self._video_error_count == 1:
                    QTimer.singleShot(500, lambda: self._recover_from_gpu_crash(self._last_video_path))
                return
        else:
            # Regular playback error (not GPU)
            print(f"[LightboxDialog] Video error: {error} - {error_string}")
            QMessageBox.critical(
                self,
                "Video Playback Error",
                f"Failed to play video:\n{error_string}\n\nError code: {error}"
            )
    
    def _show_gpu_warning_once(self):
        """Show GPU warning once after errors have stopped (debounced)."""
        if self._gpu_warning_shown:
            return
        
        self._gpu_warning_shown = True
        
        if self._video_error_count > 3:
            # Multiple errors - show warning
            print(f"[LightboxDialog] 🚫 GPU recovery failed after {self._video_error_count} errors")
            QMessageBox.warning(
                self,
                "GPU Performance Warning",
                f"⚠️ Video playback experienced GPU issues.\n\n"
                f"💡 Possible solutions:\n"
                f"  • Update your GPU drivers\n"
                f"  • Close other GPU-intensive applications\n"
                f"  • Reduce video quality settings\n"
                f"  • Try a different video codec\n\n"
                f"🔄 The player attempted automatic recovery.\n"
                f"If issues persist, please restart the application."
            )
        else:
            # Few errors - silent recovery was likely successful
            print(f"[LightboxDialog] ✅ GPU recovered after {self._video_error_count} errors (silent recovery)")
    
    def _recover_from_gpu_crash(self, video_path: str):
        """Attempt to recover from GPU crash by recreating the media player."""
        try:
            print(f"[LightboxDialog] 🔧 Recreating media player for GPU recovery...")
            
            # Remember playback state
            was_playing = hasattr(self, 'media_player') and \
                         self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            current_position = self.media_player.position() if hasattr(self, 'media_player') else 0
            
            # Stop and cleanup old player
            if hasattr(self, 'media_player'):
                try:
                    self.media_player.stop()
                    self.media_player.setSource(QUrl())
                except Exception as e:
                    print(f"[LightboxDialog] Error stopping player: {e}")
            
            # Small delay to let GPU driver recover
            QTimer.singleShot(100, lambda: self._recreate_media_player(video_path, current_position, was_playing))
            
        except Exception as e:
            print(f"[LightboxDialog] GPU recovery failed: {e}")
            import traceback
            traceback.print_exc()
    
    def _recreate_media_player(self, video_path: str, position: int, was_playing: bool):
        """Recreate media player and resume playback."""
        try:
            # Recreate media player
            self.media_player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.media_player.setAudioOutput(self.audio_output)
            self.media_player.setVideoOutput(self.video_widget)
            
            # Reconnect signals
            self.media_player.playbackStateChanged.connect(self._on_video_playback_state_changed)
            self.media_player.errorOccurred.connect(self._on_video_error)
            self.media_player.positionChanged.connect(self._on_video_position_changed)
            self.media_player.durationChanged.connect(self._on_video_duration_changed)
            
            # Reload video
            abs_path = os.path.abspath(video_path)
            video_url = QUrl.fromLocalFile(abs_path)
            self.media_player.setSource(video_url)
            
            # Restore position and playback state
            def restore_state():
                try:
                    if position > 0:
                        self.media_player.setPosition(position)
                    if was_playing:
                        self.media_player.play()
                    print(f"[LightboxDialog] ✅ GPU recovery successful!")
                    self._video_error_count = 0  # Reset error count on success
                except Exception as e:
                    print(f"[LightboxDialog] Failed to restore playback state: {e}")
            
            QTimer.singleShot(200, restore_state)
            
        except Exception as e:
            print(f"[LightboxDialog] Failed to recreate media player: {e}")
            import traceback
            traceback.print_exc()

    def _on_video_position_changed(self, position):
        """Update video position display and timeline slider (Apple/Google-style)."""
        if not self._is_video:
            return

        # Update time label
        if hasattr(self, 'video_position_label'):
            current = self._format_time(position)
            total = self._format_time(self._video_duration)
            self.video_position_label.setText(f"{current} / {total}")

        # Update timeline slider position (only if not being dragged by user)
        if hasattr(self, 'video_timeline_slider') and not getattr(self, '_timeline_seeking', False):
            if self._video_duration > 0:
                # Map position to slider range (0-1000)
                slider_position = int((position / self._video_duration) * 1000)
                self.video_timeline_slider.blockSignals(True)
                self.video_timeline_slider.setValue(slider_position)
                self.video_timeline_slider.blockSignals(False)

    def _on_video_duration_changed(self, duration):
        """Store video duration when loaded."""
        self._video_duration = duration
        print(f"[LightboxDialog] Video duration: {self._format_time(duration)}")

    # === PHASE 1: Timeline Slider (Seeking) Functionality ===

    def _on_timeline_slider_pressed(self):
        """User started dragging timeline slider."""
        self._timeline_seeking = True
        print("[LightboxDialog] Timeline seeking started")

    def _on_timeline_slider_moved(self, value):
        """User is dragging timeline slider - show preview time."""
        if not self._is_video or self._video_duration <= 0:
            return

        # Calculate target position in milliseconds
        target_position = int((value / 1000.0) * self._video_duration)

        # Update time label to show where we'll seek to
        if hasattr(self, 'video_position_label'):
            current = self._format_time(target_position)
            total = self._format_time(self._video_duration)
            self.video_position_label.setText(f"{current} / {total}")

    def _on_timeline_slider_released(self):
        """User released timeline slider - perform seek."""
        self._timeline_seeking = False

        if not self._is_video or not hasattr(self, 'media_player'):
            return

        # Get slider value (0-1000) and map to video position
        slider_value = self.video_timeline_slider.value()
        target_position = int((slider_value / 1000.0) * self._video_duration)

        # Seek to target position
        self.media_player.setPosition(target_position)
        print(f"[LightboxDialog] Seeked to {self._format_time(target_position)}")

    # === PHASE 1: Volume Control Functionality ===

    def _toggle_mute(self):
        """Toggle audio mute (Apple/Google Photos style)."""
        if not hasattr(self, 'audio_output'):
            return

        is_muted = self.audio_output.isMuted()
        self.audio_output.setMuted(not is_muted)

        # Update button icon
        if hasattr(self, 'btn_mute'):
            self.btn_mute.setText("🔇" if not is_muted else "🔊")
            self.btn_mute.setChecked(not is_muted)

        print(f"[LightboxDialog] Audio {'muted' if not is_muted else 'unmuted'}")

    def _on_volume_changed(self, value):
        """Update video volume (0-100)."""
        if not hasattr(self, 'audio_output'):
            return

        # Convert 0-100 to 0.0-1.0
        volume = value / 100.0
        self.audio_output.setVolume(volume)

        # Auto-unmute if volume increased from 0
        if value > 0 and self.audio_output.isMuted():
            self.audio_output.setMuted(False)
            if hasattr(self, 'btn_mute'):
                self.btn_mute.setText("🔊")
                self.btn_mute.setChecked(False)

        print(f"[LightboxDialog] Volume set to {value}%")

    # === PHASE 2: Playback Speed Control Functionality ===

    def _set_playback_speed(self, speed: float):
        """Set video playback speed (0.25x to 2x)."""
        if not hasattr(self, 'media_player'):
            return

        self._current_playback_speed = speed
        self.media_player.setPlaybackRate(speed)

        # Update button text
        if hasattr(self, 'btn_playback_speed'):
            self.btn_playback_speed.setText(f"{speed}x")

        # Update menu checkmarks
        if hasattr(self, '_speed_menu'):
            for action in self._speed_menu.actions():
                # Extract speed from action text (e.g., "1.5x" -> 1.5)
                action_speed = float(action.text().replace('x', ''))
                action.setChecked(abs(action_speed - speed) < 0.01)

        print(f"[LightboxDialog] Playback speed set to {speed}x")

    def _format_time(self, milliseconds):
        """Format milliseconds to M:SS or H:MM:SS."""
        if milliseconds <= 0:
            return "0:00"

        seconds = int(milliseconds / 1000)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    # === PHASE 2: Fullscreen Mode Functionality ===

    def eventFilter(self, obj, event):
        """Capture double-click on video widget for fullscreen toggle."""
        if obj == self.video_widget and event.type() == QEvent.MouseButtonDblClick:
            self._toggle_fullscreen()
            return True  # Event handled
        return super().eventFilter(obj, event)

    def _toggle_fullscreen(self):
        """Toggle fullscreen mode (F key or double-click on video)."""
        if self._is_fullscreen:
            # Exit fullscreen - restore window state
            self.showNormal()

            # Restore previous geometry if available
            if self._pre_fullscreen_geometry:
                self.setGeometry(self._pre_fullscreen_geometry)

            # Show all UI elements
            if hasattr(self, '_top_bar'):
                self._top_bar.show()
            if hasattr(self, '_bottom'):
                self._bottom.show()

            self._is_fullscreen = False
            print("[LightboxDialog] Exited fullscreen mode")
        else:
            # Enter fullscreen - save current state
            self._pre_fullscreen_geometry = self.geometry()

            # Hide UI elements for immersive experience
            if hasattr(self, '_top_bar'):
                self._top_bar.hide()
            if hasattr(self, '_bottom'):
                self._bottom.hide()

            # Go fullscreen
            self.showFullScreen()
            self._is_fullscreen = True
            print("[LightboxDialog] Entered fullscreen mode")

    # === PHASE 2: Frame-by-frame Navigation ===

    def _step_frame(self, direction: int):
        """Step forward (+1) or backward (-1) by one frame when video is paused."""
        if not self._is_video or not hasattr(self, 'media_player'):
            return

        # Only allow frame stepping when paused
        if self.media_player.playbackState() != QMediaPlayer.PlaybackState.PausedState:
            return

        # Estimate frame duration (assuming 30fps = ~33ms per frame)
        # For more accuracy, could extract from video metadata
        frame_duration_ms = 33  # ~30 FPS

        current_pos = self.media_player.position()
        new_pos = current_pos + (direction * frame_duration_ms)

        # Clamp to valid range
        new_pos = max(0, min(self._video_duration, new_pos))

        self.media_player.setPosition(new_pos)
        print(f"[LightboxDialog] Frame step {direction}: {self._format_time(new_pos)}")

    # === IMPROVEMENT 1: Thumbnail Preview While Loading ===

    def _show_video_loading_state(self, path: str):
        """Show thumbnail preview with loading indicator while video loads."""
        try:
            # Get database instance
            db = self._shared_db_instance
            if not db and self.parent():
                # Try 'db' first (MainWindow), then 'reference_db' (fallback)
                db = getattr(self.parent(), "db", None) or getattr(self.parent(), "reference_db", None)
            if not db:
                return

            # Get video metadata from database
            project_id = self._project_id
            if not project_id and self.parent():
                project_id = getattr(self.parent(), 'project_id', None)
            if not project_id:
                return

            # Try to get thumbnail from database
            video_meta = db.get_video_by_path(path, project_id)
            if not video_meta:
                return

            thumbnail_status = video_meta.get('thumbnail_status')
            thumbnail_path = video_meta.get('thumbnail_path')

            if thumbnail_status == 'ok' and thumbnail_path and os.path.exists(thumbnail_path):
                # Load thumbnail and show it temporarily
                thumb_img = Image.open(thumbnail_path)
                qimg = ImageQt(thumb_img)
                thumb_pixmap = QPixmap.fromImage(qimg)

                # Temporarily switch to image canvas to show thumbnail
                self.content_stack.setCurrentIndex(0)
                self.canvas.set_pixmap(thumb_pixmap)

                # Show "Loading video..." in info label
                if hasattr(self, 'info_label'):
                    self.info_label.setText("🎬 Loading video...")

                print(f"[LightboxDialog] Showing video thumbnail preview: {thumbnail_path}")

        except Exception as e:
            print(f"[LightboxDialog] Could not show thumbnail preview: {e}")

    # === IMPROVEMENT 3: Enhanced Video Info Display ===

    def _update_video_info_label(self, path: str):
        """Update info label with enhanced video metadata (resolution, codec, bitrate, etc.)."""
        try:
            if not hasattr(self, 'info_label'):
                return

            # Get database instance
            db = self._shared_db_instance
            if not db and self.parent():
                # Try 'db' first (MainWindow), then 'reference_db' (fallback)
                db = getattr(self.parent(), "db", None) or getattr(self.parent(), "reference_db", None)

            # Get video metadata from database
            project_id = self._project_id
            if not project_id and self.parent():
                project_id = getattr(self.parent(), 'project_id', None)
            
            if not project_id or not db:
                # Fallback: show basic file size only
                file_size = os.path.getsize(path) / (1024 * 1024)  # MB
                self.info_label.setText(f"🎬 Video   💾 {file_size:.1f} MB")
                return

            video_meta = db.get_video_by_path(path, project_id)
            if not video_meta:
                # Fallback: show basic file size only
                file_size = os.path.getsize(path) / (1024 * 1024)  # MB
                self.info_label.setText(f"🎬 Video   💾 {file_size:.1f} MB")
                return

            # Build enhanced info string with metadata
            info_parts = []

            # Resolution with quality badge
            width = video_meta.get('width')
            height = video_meta.get('height')
            if width and height:
                # Determine quality label
                if height >= 2160:
                    quality = "4K"
                elif height >= 1080:
                    quality = "FHD"
                elif height >= 720:
                    quality = "HD"
                else:
                    quality = "SD"
                info_parts.append(f"📺 {width}×{height} ({quality})")

            # Codec
            codec = video_meta.get('codec')
            if codec:
                codec_display = codec.upper()
                info_parts.append(f"🎬 {codec_display}")

            # FPS
            fps = video_meta.get('fps')
            if fps:
                info_parts.append(f"🎬 {fps:.0f}fps")

            # Bitrate
            bitrate = video_meta.get('bitrate')
            if bitrate:
                bitrate_mbps = bitrate / 1_000_000
                info_parts.append(f"📊 {bitrate_mbps:.1f}Mbps")

            # File size
            size_kb = video_meta.get('size_kb')
            if size_kb:
                size_mb = size_kb / 1024
                if size_mb >= 1024:
                    size_str = f"{size_mb / 1024:.1f}GB"
                else:
                    size_str = f"{size_mb:.0f}MB"
                info_parts.append(f"💾 {size_str}")

            # Combine all parts
            if info_parts:
                self.info_label.setText("   ".join(info_parts))
            else:
                # Fallback if no metadata available
                file_size = os.path.getsize(path) / (1024 * 1024)  # MB
                self.info_label.setText(f"🎬 Video   💾 {file_size:.1f} MB")

            print(f"[LightboxDialog] Updated video info: {self.info_label.text()}")

        except Exception as e:
            print(f"[LightboxDialog] Error updating video info: {e}")
            # Fallback: show basic file size
            try:
                file_size = os.path.getsize(path) / (1024 * 1024)  # MB
                self.info_label.setText(f"🎬 Video   💾 {file_size:.1f} MB")
            except Exception:
                pass

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts - Apple/Google Photos style."""
        # PHASE 1: Enhanced keyboard shortcuts for video
        if self._is_video:
            # Space bar: Play/Pause
            if event.key() == Qt.Key_Space:
                self._toggle_video_playback()
                event.accept()
                return

            # M key: Mute/Unmute
            if event.key() == Qt.Key_M:
                self._toggle_mute()
                event.accept()
                return

            # PHASE 2: F key - Fullscreen toggle
            if event.key() == Qt.Key_F:
                self._toggle_fullscreen()
                event.accept()
                return

            # Up/Down arrows: Volume control
            if event.key() == Qt.Key_Up and hasattr(self, 'video_volume_slider'):
                current = self.video_volume_slider.value()
                self.video_volume_slider.setValue(min(100, current + 5))
                event.accept()
                return

            if event.key() == Qt.Key_Down and hasattr(self, 'video_volume_slider'):
                current = self.video_volume_slider.value()
                self.video_volume_slider.setValue(max(0, current - 5))
                event.accept()
                return

            # 🎬 IMPROVEMENT 2: Enhanced keyboard shortcuts (YouTube-style)
            
            # J key: Rewind 10 seconds (YouTube-style)
            if event.key() == Qt.Key_J:
                if hasattr(self, 'media_player'):
                    pos = self.media_player.position()
                    self.media_player.setPosition(max(0, pos - 10000))
                    event.accept()
                    return
            
            # K key: Play/Pause (YouTube-style)
            if event.key() == Qt.Key_K:
                self._toggle_video_playback()
                event.accept()
                return
            
            # L key: Fast forward 10 seconds (YouTube-style)
            if event.key() == Qt.Key_L:
                if hasattr(self, 'media_player'):
                    pos = self.media_player.position()
                    self.media_player.setPosition(min(self._video_duration, pos + 10000))
                    event.accept()
                    return
            
            # Comma (,): Previous frame when paused
            if event.key() == Qt.Key_Comma:
                if hasattr(self, 'media_player') and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                    self._step_frame(-1)
                    event.accept()
                    return
            
            # Period (.): Next frame when paused
            if event.key() == Qt.Key_Period:
                if hasattr(self, 'media_player') and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                    self._step_frame(+1)
                    event.accept()
                    return
            
            # Number keys 0-9: Jump to 0%-90% of video
            if Qt.Key_0 <= event.key() <= Qt.Key_9:
                digit = event.key() - Qt.Key_0
                if hasattr(self, 'media_player') and self._video_duration > 0:
                    target_position = int((digit / 10.0) * self._video_duration)
                    self.media_player.setPosition(target_position)
                    event.accept()
                    return

            # PHASE 2: Left/Right arrows - Frame-by-frame when paused, seek when playing
            if event.key() == Qt.Key_Left:
                if event.modifiers() == Qt.ShiftModifier:
                    # Shift+Left: Seek backward 5 seconds (always)
                    if hasattr(self, 'media_player'):
                        pos = self.media_player.position()
                        self.media_player.setPosition(max(0, pos - 5000))
                        event.accept()
                        return
                else:
                    # Plain Left: Frame-by-frame backward when paused, or navigate to prev media
                    if hasattr(self, 'media_player') and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                        self._step_frame(-1)
                        event.accept()
                        return
                    # Otherwise let parent handle navigation

            if event.key() == Qt.Key_Right:
                if event.modifiers() == Qt.ShiftModifier:
                    # Shift+Right: Seek forward 5 seconds (always)
                    if hasattr(self, 'media_player'):
                        pos = self.media_player.position()
                        self.media_player.setPosition(min(self._video_duration, pos + 5000))
                        event.accept()
                        return
                else:
                    # Plain Right: Frame-by-frame forward when paused, or navigate to next media
                    if hasattr(self, 'media_player') and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                        self._step_frame(+1)
                        event.accept()
                        return
                    # Otherwise let parent handle navigation

        # PHASE 2: Photo shortcuts
        if not self._is_video:
            # B key: Toggle Before/After split-view
            if event.key() == Qt.Key_B:
                self._toggle_before_after()
                event.accept()
                return
        
        # Call parent implementation for other keys (arrow navigation, etc.)
        super().keyPressEvent(event)

    def _toggle_before_after(self):
        """Toggle before/after split-view mode (split divider draggable)."""
        enabled = not getattr(self.canvas, "_before_after_mode", False)
        self.canvas.set_before_after_mode(enabled)
        # Provide subtle status text
        if hasattr(self, 'info_label'):
            self.info_label.setText("🌓 Before/After — drag divider • Press B to toggle" if enabled else "")
        # Ensure before pixmap is available
        if enabled and self._orig_pil is not None:
            pm_before = self._pil_to_qpixmap(self._orig_pil)
            self.canvas.set_before_pixmap(pm_before)
            
    def _pil_to_qpixmap(self, pil_img):
        """Convert a Pillow Image (RGBA) to QPixmap safely."""
        try:
            qimg = ImageQt.ImageQt(pil_img.convert("RGBA"))
            return QPixmap.fromImage(qimg)
        except Exception as e:
            print("[_pil_to_qpixmap] conversion failed:", e)
            return QPixmap()
            
    # ---------- Fit / Zoom ----------
    def _on_zoom_combo_changed(self, *_):
        text = self.zoom_combo.currentText().strip().replace("%", "")
        try:
            pct = max(1, min(800, int(text)))
        except ValueError:
            return
        new_scale = self.canvas._fit_scale * (pct / 100.0)
        self.canvas.zoom_to(new_scale)
        self._sync_zoom_controls()

    def _fit_to_window(self):
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(0)
        self.zoom_slider.blockSignals(False)
        self.canvas.reset_view()

    def _on_slider_changed(self, v: int):
        if not self.canvas._pixmap:
            return
        if v == 0:
            self.canvas.zoom_to(self.canvas._fit_scale)
        elif v > 0:
            mult = 1.0 + (v / 200.0) * 4.0
            self.canvas.zoom_to(self.canvas._fit_scale * mult)
        else:
            mult = 1.0 + (v / 100.0) * 0.9
            self.canvas.zoom_to(self.canvas._fit_scale * mult)
        self._sync_zoom_controls()

    def _nudge_zoom(self, direction: int):
        val = self.zoom_slider.value()
        step = 10 * direction
        val = max(self.zoom_slider.minimum(), min(self.zoom_slider.maximum(), val + step))
        self.zoom_slider.setValue(val)
        self._sync_zoom_controls()

    def _sync_zoom_controls(self):
        if not self.canvas._pixmap:
            return
        zoom_pct = int(round(self.canvas._scale / self.canvas._fit_scale * 100))
        zoom_pct = max(1, min(zoom_pct, 800))
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setCurrentText(f"{zoom_pct}%")
        self.zoom_combo.blockSignals(False)
        rel = self.canvas._scale / self.canvas._fit_scale
        if rel >= 1.0:
            slider_val = int((rel - 1.0) / 4.0 * 200.0)
        else:
            slider_val = int((rel - 1.0) / 0.9 * 100.0)
        slider_val = max(-100, min(200, slider_val))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(slider_val)
        self.zoom_slider.blockSignals(False)

    def _rotate_image(self):
        """Rotate image clockwise and refresh all metadata + info."""
        if not self.canvas._pixmap:
            return
        tr = QTransform().rotate(90)
        pm = self.canvas._pixmap.transformed(tr)
        self.canvas.set_pixmap(pm)
        self._update_info(pm)
        self._update_titles_and_meta()  # 🔄 refresh title + meta
        self.canvas.update()
        self._refresh_metadata_panel()

    # -------------------------------
    # Navigation arrows (simplified)
    # -------------------------------
    def _init_navigation_arrows(self):
        self.btn_prev = QToolButton(self)
        self.btn_next = QToolButton(self)
        for b,name,tip in [(self.btn_prev,"prev","Previous photo"),(self.btn_next,"next","Next photo")]:
            b.setAutoRaise(True)
            b.setIcon(self._icon(name))
            b.setIconSize(QSize(40,40))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet("""
                QToolButton { color: black; background-color: rgba(0,0,0,0.06); border-radius:20px; border:1px solid rgba(0,0,0,0.06); }
                QToolButton:hover { background-color: rgba(0,0,0,0.12); }
            """)
        self.btn_prev.clicked.connect(self._go_prev)
        self.btn_next.clicked.connect(self._go_next)
        QTimer.singleShot(0, self._position_nav_buttons)

    def _position_nav_buttons(self):
        if not hasattr(self, "btn_prev") or not hasattr(self, "canvas"):
            return
        if self.canvas.width()==0 or self.canvas.height()==0:
            QTimer.singleShot(50, self._position_nav_buttons)
            return
        try:
            canvas_tl = self.canvas.mapTo(self, QPoint(0,0))
        except Exception:
            canvas_tl = QPoint(8, self._top.height()+8)
        cw = self.canvas.width(); ch = self.canvas.height()
        btn_w = self.btn_prev.width() or 48; btn_h = self.btn_prev.height() or 48
        y = canvas_tl.y() + (ch//2) - (btn_h//2)
        left_x = canvas_tl.x() + 12
        right_x = canvas_tl.x() + cw - btn_w - 12
        if left_x < 0: left_x = 12
        if right_x + btn_w > self.width(): right_x = max(12, self.width()-btn_w-12)
        self.btn_prev.move(left_x, max(8,y))
        self.btn_next.move(right_x, max(8,y))
        self.btn_prev.show(); self.btn_next.show()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._position_nav_buttons()

    # -------------------------------
    # Enter edit mode -> attach right panel
    # -------------------------------
    def _enter_edit_mode(self):
        """Switch UI to edit mode (reuse main canvas). Prepare edit staging but do not auto-open the panel."""
        print(f"[EditMode] 🔍 DIAGNOSTIC: Entering edit mode, _orig_pil={'exists' if self._orig_pil else 'None'}")

        # If _orig_pil is None, reload from current path at preview resolution
        if not self._orig_pil and hasattr(self, 'current_path') and self.current_path:
            print(f"[EditMode] _orig_pil is None, reloading from {self.current_path}")
            try:
                img = Image.open(self.current_path)
                orig_size = img.size
                if max(orig_size) > self._PREVIEW_MAX_DIM:
                    try:
                        img.draft('RGB', (self._PREVIEW_MAX_DIM, self._PREVIEW_MAX_DIM))
                    except Exception:
                        pass
                img = ImageOps.exif_transpose(img)
                if max(img.size) > self._PREVIEW_MAX_DIM:
                    img.thumbnail((self._PREVIEW_MAX_DIM, self._PREVIEW_MAX_DIM), Image.LANCZOS)
                img = img.convert("RGBA")
                self._orig_pil = img
                self._orig_file_dimensions = orig_size
                print(f"[EditMode] Reloaded at preview res: {self._orig_pil.size}")
            except Exception as e:
                print(f"[EditMode] Failed to reload image: {e}")
                QMessageBox.warning(self, "Edit Error", f"Cannot enter edit mode: Image not loaded.\n\nError: {e}")
                return

        # Prepare edit staging
        if self._orig_pil:
            self._edit_base_pil = self._orig_pil.copy()
            print(f"[EditMode] ✅ DIAGNOSTIC: Created _edit_base_pil copy")
        else:
            print(f"[EditMode] ❌ DIAGNOSTIC: Cannot enter edit mode - no image loaded")
            QMessageBox.warning(self, "Edit Error", "Cannot enter edit mode: No image loaded.")
            return
        self._working_pil = self._edit_base_pil.copy() if self._edit_base_pil else None

        # Reset adjustments values & sliders
        for k in self.adjustments.keys():
            self.adjustments[k] = 0
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)

        # Show editor view and move content_stack into editor container
        # CRITICAL FIX: Reparent content_stack (not just canvas) to avoid Qt visibility inheritance bug
        self.stack.setCurrentIndex(1)
        if hasattr(self, "edit_canvas_container"):
            container_layout = self.edit_canvas_container.layout()
            # Reparent the entire content_stack so canvas becomes visible in editor page
            if self.content_stack.parent() is not self.edit_canvas_container:
                container_layout.addWidget(self.content_stack)
        self.canvas.reset_view()

        # Make Save & Cancel visible in toolbar row (they are already created in page)
        if hasattr(self, "btn_save"):
            self.btn_save.show()
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.show()

        # Ensure placeholder exists but keep it collapsed (panel shown via Adjustments button)
        if not hasattr(self, "_editor_right_placeholder"):
            # placeholder will have been created by _build_edit_page; if not, nothing to do
            pass

        # Render the working base as the initial preview
        if self._edit_base_pil:
            pm = self._pil_to_qpixmap(self._edit_base_pil)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)
            # Reset rotation preview state on new photo
            self._rotation_preview_base = None
            if hasattr(self, 'rotation_slider_widget'):
                self.rotation_slider_widget.reset()

    def _return_to_viewer(self):
        reply = QMessageBox.question(self, "Return to viewer", "Do you want to save changes before returning?",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if reply == QMessageBox.Cancel:
            return
        if reply == QMessageBox.Yes:
            QMessageBox.information(self, "Saved", "Changes saved.")
        # CRITICAL FIX: Reparent content_stack back to viewer page
        viewer_page = self.stack.widget(0)
        viewer_layout = viewer_page.layout()
        center_widget = viewer_layout.itemAt(1).widget() if viewer_layout.count() > 1 else None
        if center_widget:
            hbox_layout = center_widget.layout()
        else:
            hbox_layout = None

        # Reparent content_stack back to viewer page (not just canvas)
        if hbox_layout and self.content_stack.parent() is not center_widget:
            hbox_layout.insertWidget(0, self.content_stack, 1)

        self.stack.setCurrentIndex(0)
        self.canvas.reset_view()

    # ---------- Navigation / list ----------
    def set_image_list(self, image_list, start_index=0):
        self._image_list = list(image_list or [])
        self._current_index = max(0, min(start_index, len(self._image_list) - 1))
        if self._image_list:
            new_path = self._image_list[self._current_index]
            # Skip reload if already showing this path (prevents double-load on open)
            if new_path == self._path and self._orig_pil is not None:
                return
            self._path = new_path
            self._load_media(self._path)

    def _update_titles_and_meta(self):
        base = os.path.basename(self._path) if self._path else ""

        # Update title based on media type
        if self._is_video:
            self.setWindowTitle(f"📹 {base}")
        else:
            self.setWindowTitle(f"Photo Viewer – {base}")

        if hasattr(self, "title_label"):
            self.title_label.setText(base)
        if hasattr(self, "lbl_edit_title"):
            self.lbl_edit_title.setText(base)

        # Only update info for photos (videos don't have pixmaps)
        if not self._is_video and hasattr(self, "canvas") and self.canvas._pixmap:
            self._update_info(self.canvas._pixmap)
        if hasattr(self, "_meta_panel") and self._meta_panel.isVisible():
            meta_dict = self._parse_metadata_to_dict(self._get_metadata_text())
            new_panel = self._build_metadata_panel(meta_dict)
            layout = self._meta_placeholder.layout()
            if layout:
                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        item.widget().setParent(None)
                layout.addWidget(new_panel)
                self._meta_panel = new_panel
        if self.stack.currentIndex() == 1 and hasattr(self, "edit_canvas_container"):
            parent_layout = self.edit_canvas_container.layout()
            if self.canvas.parent() is not self.edit_canvas_container:
                parent_layout.addWidget(self.canvas)
            self.canvas.reset_view()

    def _go_prev(self):
        if self._image_list and self._current_index > 0:
            # PHASE 2: Stop video playback when navigating
            if self._is_video and hasattr(self, 'media_player'):
                self.media_player.stop()

            self._current_index -= 1
            self._path = self._image_list[self._current_index]
            self._load_media(self._path)  # Unified loader handles both photos and videos
            self._update_titles_and_meta()

            # Only fit to window for photos
            if self._current_media_type == "photo":
                self._fit_to_window()

            self._refresh_metadata_panel()

    def _go_next(self):
        if self._image_list and self._current_index < len(self._image_list) - 1:
            # PHASE 2: Stop video playback when navigating
            if self._is_video and hasattr(self, 'media_player'):
                self.media_player.stop()

            self._current_index += 1
            self._path = self._image_list[self._current_index]
            self._load_media(self._path)  # Unified loader handles both photos and videos
            self._update_titles_and_meta()

            # Only fit to window for photos
            if self._current_media_type == "photo":
                self._fit_to_window()

            self._refresh_metadata_panel()
    
    # ---------- Utilities ----------
    def _update_info(self, pm: QPixmap):
        kb = round(pm.width() * pm.height() * 4 / 1024)
        self.info_label.setText(f"🖼️ {pm.width()}×{pm.height()}   💾 {kb:,} KB")

    def _toggle_crop_mode(self, enabled):
        if not hasattr(self, "canvas"):
            return
        if enabled:
            # MICROSOFT PHOTOS: Default preset is 1:1 (not freeform)
            self.canvas.enter_crop_mode(aspect_ratio=(1, 1), preset="1:1")
            self._show_crop_controls(True)
            
            # PHASE 1: Show crop preset buttons
            if hasattr(self, 'crop_preset_widget'):
                self.crop_preset_widget.show()
                
                # MICROSOFT PHOTOS: Set '1:1' as default selected preset
                self._set_crop_aspect((1, 1), "1:1")
            
            # MICROSOFT PHOTOS: Show rotation slider underneath photo
            if hasattr(self, 'rotation_slider_widget'):
                self.rotation_slider_widget.show()
                self.rotation_slider_widget.reset()
        else:
            self.canvas.exit_crop_mode()
            self._show_crop_controls(False)
            
            # PHASE 1: Hide crop preset buttons
            if hasattr(self, 'crop_preset_widget'):
                self.crop_preset_widget.hide()
            
            # Hide rotation slider and clear preview base
            if hasattr(self, 'rotation_slider_widget'):
                self.rotation_slider_widget.hide()
            self._rotation_preview_base = None

    # -------------------------------
    # Crop functions (use canvas methods)
    # -------------------------------
    def get_crop_box(self):
        rect = getattr(self.canvas, "_crop_rect", None)
        if not rect or not getattr(self.canvas, "_pixmap", None): return None
        left = rect.left(); top = rect.top(); w = rect.width(); h = rect.height()
        ox = self.canvas._offset.x(); oy = self.canvas._offset.y(); scale = self.canvas._scale
        rx = (left - ox) / scale; ry = (top - oy) / scale
        rw = w / scale; rh = h / scale
        iw = max(1, self.canvas._img_size.width()); ih = max(1, self.canvas._img_size.height())
        x = int(max(0, min(iw-1, round(rx))))
        y = int(max(0, min(ih-1, round(ry))))
        w2 = int(max(1, min(iw-x, round(rw))))
        h2 = int(max(1, min(ih-y, round(rh))))
        return (x,y,w2,h2)

    def _apply_crop(self):
        """Apply crop to the edit base (staged); do NOT write file. Mark dirty."""
        if not hasattr(self.canvas, "_crop_rect") or not self.canvas._crop_rect:
            QMessageBox.warning(self, "Crop", "No crop area selected.")
            return
        if self._edit_base_pil is None:
            QMessageBox.warning(self, "Crop", "No image loaded for editing.")
            return
        box = self.get_crop_box()
        if not box:
            QMessageBox.warning(self, "Crop", "Invalid crop selection.")
            return
        try:
            x, y, w, h = box
            cropped = self._edit_base_pil.crop((x, y, x + w, y + h))
            self._edit_base_pil = cropped
            # after changing base, reapply adjustments to produce preview
            self._apply_adjustments()
            self._is_dirty = True
            # keep crop UI state: exit crop mode and hide buttons
            if hasattr(self.canvas, "exit_crop_mode"):
                self.canvas.exit_crop_mode()
            self._show_crop_controls(False)
        except Exception as e:
            QMessageBox.warning(self, "Crop failed", str(e))

    def _show_crop_controls(self, show: bool):
        if show:
            if hasattr(self, "btn_crop_apply") and hasattr(self, "btn_crop_cancel"):
                self.btn_crop_apply.show(); self.btn_crop_cancel.show()
        else:
            if hasattr(self, "btn_crop_apply"): self.btn_crop_apply.hide()
            if hasattr(self, "btn_crop_cancel"): self.btn_crop_cancel.hide()
            if hasattr(self, "btn_crop") and hasattr(self.btn_crop, "setChecked"):
                self.btn_crop.setChecked(False)

    # -------------------------------
    # Save / other helpers
    # -------------------------------
    def _save_as(self):
        if not self._path: return
        dest, _ = QFileDialog.getSaveFileName(self, "Save As", self._path, "Images (*.png *.jpg *.jpeg *.bmp)")
        if not dest: return
        try:
            img = Image.open(self._path)
            img.save(dest)
            self._path = dest
            QMessageBox.information(self, "Saved", f"Photo saved as:\n{dest}")
            self._update_titles_and_meta()
            self._refresh_metadata_panel()
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Error: {e}")

    def _copy_to_clipboard(self):
        if self.canvas._pixmap:
            QApplication.clipboard().setPixmap(self.canvas._pixmap)
            QMessageBox.information(self, "Copied", "Photo copied to clipboard.")

    def _render_full_res_for_save(self):
        """Re-open the original file at full resolution and apply current adjustments.

        Smart Preview model: preview is capped at 2560px for responsiveness,
        but saves re-process from the original for full quality output.
        Returns the adjusted full-res PIL image, or falls back to _working_pil.
        """
        if not self._path or not os.path.isfile(self._path):
            return self._working_pil

        has_edits = any(v != 0 for v in self.adjustments.values())
        orig_dims = getattr(self, '_orig_file_dimensions', None)
        preview_is_smaller = (orig_dims and self._orig_pil and
                              max(orig_dims) > max(self._orig_pil.size))

        if not has_edits and not preview_is_smaller:
            return self._working_pil

        try:
            print(f"[Save] Re-opening original at full resolution: {self._path}")
            full_img = Image.open(self._path)
            full_img = ImageOps.exif_transpose(full_img).convert("RGBA")
            if has_edits:
                full_img = self._apply_adjustment_pipeline(full_img, self.adjustments)
            print(f"[Save] Full-res render complete: {full_img.size}")
            return full_img
        except Exception as e:
            print(f"[Save] Full-res render failed, using preview: {e}")
            return self._working_pil

    def _save_as_copy(self):
        """Save edited image as a new file at full resolution."""
        if not self._working_pil:
            QMessageBox.information(self, "Save as copy", "Nothing to save.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save As Copy", self._path or "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not dest:
            return
        try:
            save_img = self._render_full_res_for_save()
            save_img.save(dest)
            QMessageBox.information(self, "Saved", f"Saved copy to:\n{dest}")
            self._is_dirty = False
            if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
                self._save_action_overwrite.setEnabled(False)
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Error: {e}")

    def _save_overwrite(self):
        """Overwrite original file with edits applied at full resolution."""
        if not self._path:
            QMessageBox.warning(self, "Save", "No original path to save to.")
            return
        if not self._working_pil:
            QMessageBox.information(self, "Save", "Nothing to save.")
            return
        try:
            save_img = self._render_full_res_for_save()
            save_img.save(self._path)
            # Reload the saved file as new preview-quality baseline
            self._load_photo(self._path)
            self._edit_base_pil = self._orig_pil.copy() if self._orig_pil else None
            self._is_dirty = False
            if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
                self._save_action_overwrite.setEnabled(False)
            QMessageBox.information(self, "Saved", f"Saved changes to:\n{self._path}")
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Error: {e}")

    def _copy_working_to_clipboard(self):
        """Copy the working preview to clipboard as a pixmap."""
        if self._working_pil is None:
            QMessageBox.information(self, "Copy", "Nothing to copy.")
            return
        try:
            pm = self._pil_to_qpixmap(self._working_pil)
            QApplication.clipboard().setPixmap(pm)
            QMessageBox.information(self, "Copied", "Working image copied to clipboard.")
        except Exception as e:
            QMessageBox.warning(self, "Copy failed", f"Error: {e}")

    def _cancel_edits(self):
        """Reset staged edits and restore original image in viewer/editor."""
        # discard staged images
        self._edit_base_pil = None
        self._working_pil = None
        # reset adjustments and sliders
        for k in self.adjustments.keys():
            self.adjustments[k] = 0
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
        # restore original image on canvas
        if self._orig_pil:
            pm = self._pil_to_qpixmap(self._orig_pil)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)
            # Reset rotation preview state on new photo
            self._rotation_preview_base = None
            if hasattr(self, 'rotation_slider_widget'):
                self.rotation_slider_widget.reset()
        # reset dirty flag and save action
        self._is_dirty = False
        if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
            self._save_action_overwrite.setEnabled(False)
        # hide crop controls and uncheck crop toggle
        self._show_crop_controls(False)
        if hasattr(self, "btn_crop") and getattr(self.btn_crop, "setChecked", None):
            self.btn_crop.setChecked(False)
    
    def _open_in_explorer(self):
        if self._path and os.path.exists(self._path):
            subprocess.run(["explorer", "/select,", os.path.normpath(self._path)])
    
    # ================================================================
    # PHASE 1: Professional Tools - Handler Methods
    # ================================================================
    
    def _on_rating_changed(self, rating: int):
        """Handle rating change - save to database."""
        print(f"[Rating] Photo rated: {rating} stars")
        # TODO: Save rating to database
        # self._shared_db_instance.update_photo_rating(self._path, self._project_id, rating)
    
    def _on_flag_changed(self, flag: str):
        """Handle pick/reject flag change - save to database."""
        print(f"[Flag] Photo flagged as: {flag}")
        # Update button states
        if flag == 'pick':
            self.btn_reject.setChecked(False)
        elif flag == 'reject':
            self.btn_pick.setChecked(False)
        # TODO: Save flag to database
        # self._shared_db_instance.update_photo_flag(self._path, self._project_id, flag)
    
    def _set_crop_aspect(self, aspect_ratio, preset_name):
        """Set crop aspect ratio and update canvas (Microsoft Photos style)."""
        if hasattr(self, 'canvas') and self.canvas._crop_mode:
            self.canvas.set_crop_aspect_ratio(aspect_ratio, preset_name)
            
            # MICROSOFT PHOTOS STYLE: Update button visual states
            # Define pressed/selected style
            selected_style = self._button_style() + """
                QToolButton {
                    background-color: #0078d4;
                    color: white;
                    border: 2px solid #005a9e;
                }
            """
            normal_style = self._button_style()
            
            # Update all preset button styles
            for btn, preset in [(self.btn_crop_free, "freeform"),
                               (self.btn_crop_1_1, "1:1"),
                               (self.btn_crop_4_3, "4:3"),
                               (self.btn_crop_16_9, "16:9"),
                               (self.btn_crop_3_2, "3:2")]:
                if preset == preset_name:
                    btn.setStyleSheet(selected_style)
                    btn.setChecked(True)
                else:
                    btn.setStyleSheet(normal_style)
                    btn.setChecked(False)
    
    def _on_rotation_changed(self, angle: float):
        """Handle rotation slider change - rotate a lightweight preview and preserve zoom."""
        if not self._edit_base_pil or not getattr(self.canvas, "_pixmap", None):
            return
        
        # Build a small preview base once (to avoid out-of-memory on huge images)
        if not hasattr(self, "_rotation_preview_base") or self._rotation_preview_base is None:
            base_pm = self.canvas._pixmap
            # Target width: min(view width * 0.9, original width, 1600px)
            target_w = int(min(self.canvas.width() * 0.9, base_pm.width(), 1600))
            target_h = int(base_pm.height() * (target_w / base_pm.width()))
            print(f"[RotationPreviewBase] path={getattr(self, '_path', '')}, base_pm={base_pm.width()}x{base_pm.height()}, target={target_w}x{target_h}")
            self._rotation_preview_base = base_pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        # Rotate preview base by the requested angle
        transform = QTransform()
        transform.rotate(-angle)
        rotated_preview = self._rotation_preview_base.transformed(transform, Qt.SmoothTransformation)
        print(f"[RotationPreview] angle={angle}, rotated={rotated_preview.width()}x{rotated_preview.height()}, canvas_scale={getattr(self.canvas, '_scale', 0):.3f}")
        
        # Preserve current zoom/center while updating the pixmap
        self.canvas.set_pixmap_preserve_view(rotated_preview)
        
        # Remember pending rotation to apply on commit
        self._pending_rotation = angle
    
    def _rotate_90_left(self):
        """Rotate image 90° counter-clockwise (left)."""
        print("[Rotation] Rotating 90° left")
        if not self._edit_base_pil:
            return
        
        # Rotate 90° counter-clockwise
        rotated = self._edit_base_pil.rotate(90, expand=True)
        self._edit_base_pil = rotated
        self._working_pil = rotated.copy()
        
        # Update canvas
        pm = self._pil_to_qpixmap(rotated)
        self.canvas.set_pixmap(pm)
        
        # Reset slider to 0
        if hasattr(self, 'rotation_slider_widget'):
            self.rotation_slider_widget.reset()
        
        # Update histogram
        if hasattr(self, '_hist_timer') and hasattr(self, 'histogram_widget') and self.histogram_widget:
            try:
                self._hist_timer.start(200)
            except Exception:
                self.histogram_widget.set_image(self._working_pil)
        
        self._is_dirty = True
    
    def _rotate_90_right(self):
        """Rotate image 90° clockwise (right)."""
        print("[Rotation] Rotating 90° right")
        if not self._edit_base_pil:
            return
        
        # Rotate 90° clockwise (= -90°)
        rotated = self._edit_base_pil.rotate(-90, expand=True)
        self._edit_base_pil = rotated
        self._working_pil = rotated.copy()
        
        # Update canvas
        pm = self._pil_to_qpixmap(rotated)
        self.canvas.set_pixmap(pm)
        
        # Reset slider to 0
        if hasattr(self, 'rotation_slider_widget'):
            self.rotation_slider_widget.reset()
        
        # Update histogram
        if hasattr(self, '_hist_timer') and hasattr(self, 'histogram_widget') and self.histogram_widget:
            try:
                self._hist_timer.start(200)
            except Exception:
                self.histogram_widget.set_image(self._working_pil)
        
        self._is_dirty = True
    
    def _flip_horizontal(self):
        """Flip image horizontally (mirror left-right)."""
        print("[Flip] Flipping horizontally")
        if not self._edit_base_pil:
            return
        
        from PIL import Image
        flipped = self._edit_base_pil.transpose(Image.FLIP_LEFT_RIGHT)
        self._edit_base_pil = flipped
        self._working_pil = flipped.copy()
        
        # Update canvas
        pm = self._pil_to_qpixmap(flipped)
        self.canvas.set_pixmap(pm)
        
        # Update histogram
        if hasattr(self, '_hist_timer') and hasattr(self, 'histogram_widget') and self.histogram_widget:
            try:
                self._hist_timer.start(200)
            except Exception:
                self.histogram_widget.set_image(self._working_pil)
        
        self._is_dirty = True
    
    def _flip_vertical(self):
        """Flip image vertically (mirror top-bottom)."""
        print("[Flip] Flipping vertically")
        if not self._edit_base_pil:
            return
        
        from PIL import Image
        flipped = self._edit_base_pil.transpose(Image.FLIP_TOP_BOTTOM)
        self._edit_base_pil = flipped
        self._working_pil = flipped.copy()
        
        # Update canvas
        pm = self._pil_to_qpixmap(flipped)
        self.canvas.set_pixmap(pm)
        
        # Update histogram
        if hasattr(self, '_hist_timer') and hasattr(self, 'histogram_widget') and self.histogram_widget:
            try:
                self._hist_timer.start(200)
            except Exception:
                self.histogram_widget.set_image(self._working_pil)
        
        self._is_dirty = True
    
    def _undo_edit(self):
        """Undo last edit operation."""
        if not hasattr(self, '_edit_history'):
            self._edit_history = EditHistoryStack()
        
        if not self._edit_history.can_undo():
            print("[Undo] No more undos available")
            return
        
        # Get current state
        current_state = {
            'adjustments': self.adjustments.copy(),
            'edit_base_pil': self._edit_base_pil.copy() if self._edit_base_pil else None,
            'description': 'Current state'
        }
        
        # Undo to previous state
        prev_state = self._edit_history.undo(current_state)
        if prev_state:
            # Restore previous state
            self.adjustments = prev_state['adjustments'].copy()
            if prev_state['edit_base_pil']:
                self._edit_base_pil = prev_state['edit_base_pil'].copy()
            
            # Update sliders
            for k, v in self.adjustments.items():
                slider = getattr(self, f"slider_{k}", None)
                if slider:
                    slider.blockSignals(True)
                    slider.setValue(v)
                    slider.blockSignals(False)
            
            # Reapply adjustments
            self._apply_adjustments()
            
            # Update undo/redo button states
            self.btn_undo.setEnabled(self._edit_history.can_undo())
            self.btn_redo.setEnabled(self._edit_history.can_redo())
            
            print(f"[Undo] Restored to: {prev_state.get('description', 'previous state')}")
    
    def _redo_edit(self):
        """Redo last undone edit operation."""
        if not hasattr(self, '_edit_history'):
            self._edit_history = EditHistoryStack()
        
        if not self._edit_history.can_redo():
            print("[Redo] No more redos available")
            return
        
        # Get current state
        current_state = {
            'adjustments': self.adjustments.copy(),
            'edit_base_pil': self._edit_base_pil.copy() if self._edit_base_pil else None,
            'description': 'Current state'
        }
        
        # Redo to next state
        next_state = self._edit_history.redo(current_state)
        if next_state:
            # Restore next state
            self.adjustments = next_state['adjustments'].copy()
            if next_state['edit_base_pil']:
                self._edit_base_pil = next_state['edit_base_pil'].copy()
            
            # Update sliders
            for k, v in self.adjustments.items():
                slider = getattr(self, f"slider_{k}", None)
                if slider:
                    slider.blockSignals(True)
                    slider.setValue(v)
                    slider.blockSignals(False)
            
            # Reapply adjustments
            self._apply_adjustments()
            
            # Update undo/redo button states
            self.btn_undo.setEnabled(self._edit_history.can_undo())
            self.btn_redo.setEnabled(self._edit_history.can_redo())
            
            print(f"[Redo] Restored to: {next_state.get('description', 'next state')}")

    def closeEvent(self, ev):
        """Clean up resources when closing - professional resource management."""
        self._preload_stop = True

        # Stop video playback and release media player resources
        if hasattr(self, 'media_player') and self._is_video:
            self.media_player.stop()
            self.media_player.setSource(QUrl())  # Release file handle
            print("[LightboxDialog] Video playback stopped and resources released")

        super().closeEvent(ev)

    # -------------------------------
    # Metadata helpers (unchanged)
    # -------------------------------
    def _refresh_metadata_panel(self):
        if hasattr(self, "_meta_panel") and self._meta_panel.isVisible():
            try:
                meta_dict = self._parse_metadata_to_dict(self._get_metadata_text())
                new_panel = self._build_metadata_panel(meta_dict)
                layout = self._meta_placeholder.layout()
                if layout:
                    while layout.count():
                        item = layout.takeAt(0)
                        if item.widget(): item.widget().setParent(None)
                    layout.addWidget(new_panel)
                self._meta_panel = new_panel
            except Exception as e:
                print(f"[Metadata Refresh Error] {e}")
                
    def _on_canvas_scale_changed(self, new_scale: float):
        """
        Called when the canvas scale changes (wheel / programmatic).
        Ensure the zoom slider and combo reflect the new scale.
        """
        try:
            # _sync_zoom_controls reads current canvas._scale, so just call it
            self._sync_zoom_controls()
        except Exception:
            pass