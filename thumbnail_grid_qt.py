# thumbnail_grid_qt.py
# Version 09.18.01.13 dated 20251101
#
# Updated: optimized thumbnail worker integration, reuse of thread pool,
# shared placeholders, pass shared cache into workers, respect thumbnail_workers setting,
# and safer worker token handling to avoid stale emissions.
#
# Previous behavior preserved; changes are focused on performance and memory.


import io
import os
import re
import sys
import time
import uuid
import hashlib
import collections
from datetime import datetime
from typing import Optional

# === Global Decoder Warning Policy ===
from settings_manager_qt import SettingsManager
from thumb_cache_db import get_cache
from services import get_thumbnail_service
from translation_manager import tr

# create module-level settings instance (used in __init__ safely)
settings = SettingsManager()
if not settings.get("show_decoder_warnings", False):
    # Silence Qt and Pillow warnings
    os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio.warning=false"
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    print("🔇 Decoder warnings suppressed per settings.")
else:
    print("⚠️ Decoder warnings enabled (developer mode).")




from PySide6.QtWidgets import (
    QWidget, QListView,
    QVBoxLayout, QMessageBox,
    QHBoxLayout, QSlider,
    QPushButton, QStyledItemDelegate,
    QStyle, QMenu, QAbstractItemView, QStyleOptionViewItem, QApplication
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QSize,
    QThreadPool,
    QRunnable,
    Signal,
    QObject,
    QEvent, QPropertyAnimation,
    QEasingCurve,
    QPoint, QModelIndex, QTimer, QItemSelectionModel, QMimeData
)

 

from PySide6.QtGui import (
    QStandardItemModel, 
    QStandardItem,
    QPixmap,
    QImage,
    QPainter,
    QPen,
    QBrush,
    QColor,
    QFont,
    QAction,
    QCursor,
    QIcon, QImageReader
) 

    
from reference_db import ReferenceDB
from app_services import (
    get_project_images,
    get_thumbnail
)
from services.tag_service import get_tag_service

from PIL import Image



def make_placeholder_pixmap(size=QSize(160, 160), text="😊"):
    """
    Create a transparent placeholder so thumbnails with different aspect
    ratios won't show large opaque blocks. Draw a soft rounded rect and center the icon.
    Ensures QPainter is properly ended to avoid leaving paint device active.
    """
    pm = QPixmap(size)
    pm.fill(Qt.transparent)
    p = QPainter()
    try:
        p.begin(pm)
        # use Antialiasing + TextAntialiasing + SmoothPixmapTransform for high-quality output
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        rect = pm.rect().adjusted(4, 4, -4, -4)
        bg = QColor("#F3F4F6")
        border = QColor("#E0E0E0")
        p.setBrush(bg)
        p.setPen(border)
        p.drawRoundedRect(rect, 10, 10)

        font = QFont()
        font.setPointSize(int(max(10, size.height() * 0.28)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#9AA0A6"))
        p.drawText(pm.rect(), Qt.AlignCenter, text)
    finally:
        try:
            p.end()
        except Exception:
            pass
    return pm

def _pil_to_qimage(pil_img):
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGB888)
    return qimg

# === Enhanced safe thumbnail loader ===


def load_thumbnail_safe(path: str, height: int, cache: dict, timeout: float, placeholder: QPixmap):
    """
    Safe loader with ThumbnailService.

    NOTE: The 'cache' parameter is kept for backward compatibility but is now unused
    as ThumbnailService manages its own L1+L2 caching internally.

    Args:
        path: Image file path
        height: Target thumbnail height
        cache: Legacy parameter (unused, kept for compatibility)
        timeout: Decode timeout in seconds
        placeholder: Fallback pixmap on error

    Returns:
        QPixmap thumbnail
    """
    try:
        # Use ThumbnailService which handles all caching internally
        thumb_service = get_thumbnail_service()
        pm = thumb_service.get_thumbnail(path, height, timeout=timeout)

        if pm and not pm.isNull():
            return pm

        return placeholder

    except Exception as e:
        print(f"[ThumbnailSafe] Failed to load {path}: {e}")
        return placeholder


def load_thumbnail_image_safe(path: str, height: int, timeout: float, placeholder_image: QImage = None):
    """
    Safe thumbnail loader that returns QImage (THREAD-SAFE).

    FIX 2026-02-08: New function for thread-safe thumbnail loading.
    Use this from worker threads instead of load_thumbnail_safe().

    Args:
        path: Image file path
        height: Target thumbnail height
        timeout: Decode timeout in seconds
        placeholder_image: Fallback QImage on error (optional)

    Returns:
        QImage thumbnail (thread-safe)
    """
    try:
        # Use ThumbnailService's thread-safe get_thumbnail_image method
        thumb_service = get_thumbnail_service()
        qimage = thumb_service.get_thumbnail_image(path, height, timeout=timeout)

        if qimage and not qimage.isNull():
            return qimage

        return placeholder_image or QImage()

    except Exception as e:
        print(f"[ThumbnailImageSafe] Failed to load {path}: {e}")
        return placeholder_image or QImage()

# --- Worker signal bridge ---
def get_thumbnail_safe(path, height, use_disk_cache=True):
    pm = get_thumbnail(path, height, use_disk_cache=True)
    if pm and not pm.isNull():
        return pm

    # --- fallback for TIFF with unsupported compression ---
    if path.lower().endswith((".tif", ".tiff")):
        try:
            with Image.open(path) as im:
                im.thumbnail((height * 2, height), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                qimg = QImage.fromData(buf.getvalue())
                return QPixmap.fromImage(qimg)
        except Exception as e:
            print(f"[TIFF fallback] Could not read {path}: {e}")

    return pm

# --- Worker signal bridge ---
class ThumbSignal(QObject):
    # FIX 2026-02-08: Changed from QPixmap to object (QImage) for thread-safety
    # QImage is CPU-backed and thread-safe, QPixmap is GPU-backed and NOT thread-safe
    preview = Signal(str, object, int)  # quick low-res QImage
    loaded = Signal(str, object, int)  # path, QImage, row index


# --- Worker for background thumbnail loading ---

class ThumbWorker(QRunnable):
    """
    Background worker for thumbnail loading.

    FIX 2026-02-08: CRITICAL THREAD-SAFETY FIX
    - Now uses QImage (thread-safe) instead of QPixmap (NOT thread-safe on Windows)
    - Emits QImage via signals, UI thread converts to QPixmap
    - Uses get_thumbnail_image() instead of get_thumbnail()
    """
    def __init__(self, real_path, norm_path, height, row, signal_obj, cache, reload_token, placeholder):
        super().__init__()
        # real_path = on-disk path to open; norm_path = unified key used in model/cache
        self.real_path = str(real_path)
        self.norm_path = str(norm_path)
        self.height = int(height)
        self.row = int(row)
        self.signals = signal_obj
        self.cache = cache
        self.reload_token = reload_token
        self.placeholder = placeholder
        # FIX 2026-02-08: Convert placeholder QPixmap to QImage for thread-safety
        self.placeholder_image = placeholder.toImage() if placeholder and not placeholder.isNull() else QImage()

    def run(self):
        """
        Load thumbnail in background thread.

        FIX 2026-02-08: Emits QImage (thread-safe) instead of QPixmap.
        """
        try:
            quick_h = max(64, min(128, max(32, self.height // 2)))
            img_preview = None
            try:
                # Try QImageReader fast scaled read first - already returns QImage (thread-safe!)
                try:
                    reader = QImageReader(self.real_path)
                    reader.setAutoTransform(True)
                    reader.setScaledSize(QSize(quick_h, quick_h))
                    img = reader.read()
                    if img is not None and not img.isNull():
                        # FIX 2026-02-08: Keep as QImage, don't convert to QPixmap
                        img_preview = img
                except Exception:
                    img_preview = None
                if img_preview is None:
                    # FIX 2026-02-08: Use thread-safe image loader
                    img_preview = load_thumbnail_image_safe(self.real_path, quick_h, timeout=2.0, placeholder_image=self.placeholder_image)
            except Exception as e:
                print(f"[ThumbWorker] preview failed {self.real_path}: {e}")
                img_preview = self.placeholder_image

            try:
                # FIX 2026-02-08: Emit QImage (thread-safe)
                self.signals.preview.emit(self.norm_path, img_preview, self.row)
            except Exception:
                return

            # Full quality thumbnail
            try:
                # FIX 2026-02-08: Use get_thumbnail_image() which returns QImage (thread-safe!)
                from app_services import get_thumbnail_image
                img_full = get_thumbnail_image(self.real_path, self.height, timeout=5.0)
                if img_full is None or img_full.isNull():
                    img_full = load_thumbnail_image_safe(self.real_path, self.height, timeout=5.0, placeholder_image=self.placeholder_image)
            except Exception:
                img_full = self.placeholder_image

            try:
                # FIX 2026-02-08: Emit QImage (thread-safe)
                self.signals.loaded.emit(self.norm_path, img_full, self.row)
            except Exception:
                return

        except Exception as e:
            print(f"[ThumbWorker] Error for {self.real_path}: {e}")


def is_video_file(path: str) -> bool:
    """Check if file is a video based on extension."""
    if not path:
        return False
    ext = os.path.splitext(path)[1].lower()
    video_exts = {'.mp4', '.m4v', '.mov', '.mpeg', '.mpg', '.mpe', '.wmv',
                  '.asf', '.avi', '.mkv', '.webm', '.flv', '.f4v', '.3gp',
                  '.3g2', '.ogv'}
    return ext in video_exts


def format_duration(seconds: float) -> str:
    """Format duration in seconds to MM:SS or H:MM:SS format."""
    if seconds is None or seconds < 0:
        return "0:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


class CenteredThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_row = -1
        self.icon_size = 22
        self.icon_margin = 6

    def set_hover_row(self, row: int):
        self.hover_row = row



    def paint(self, painter: QPainter, option, index):
        # ✅ Get icon/pixmap data properly first
        icon_data = index.data(Qt.DecorationRole)
        rect = option.rect
        
        # 📅 Date group header
        header_label = index.data(Qt.UserRole + 10)
        if header_label:
            painter.save()
            header_rect = QRect(rect.left(), rect.top(), rect.width(), 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(240, 240, 240))
            painter.drawRect(header_rect)
            painter.setPen(QPen(Qt.gray))
            painter.drawText(header_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, header_label)
            painter.restore()
            # shift drawing rect down below header
            rect = rect.adjusted(0, 24, 0, 0)

        # ✅ Guard against invalid or zero rect sizes (e.g., before layout settles)
        cell_h = rect.height()
        if cell_h <= 6:
            QStyledItemDelegate.paint(self, painter, option, index)
            return

        target_h = cell_h - 6

        # 🟡 Selection border
        if option.state & QStyle.State_Selected:
            painter.save()
            pen = QPen(QColor(30, 144, 255))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
            painter.restore()

        # 🖼 Draw scaled thumbnail with fixed height and aspect ratio
        pm = None
        if isinstance(icon_data, QIcon):
            pm = icon_data.pixmap(QSize(int(target_h * 2), target_h))
        elif isinstance(icon_data, QPixmap):
            pm = icon_data

        # === Tag Mask Overlay - Color-coded visual distinction ===
        # 🎨 FEATURE: Color-coded masks for different tag types
        # Each tag type gets its own distinct color mask for easy visual identification
        tags = index.data(Qt.UserRole + 2) or []
        if tags:
            painter.save()
            
            # Define tag color scheme - user can customize these
            TAG_COLORS = {
                'favorite': QColor(255, 215, 0, 80),      # Gold/Yellow mask for favorites
                'face': QColor(70, 130, 180, 60),        # Steel Blue for faces
                'important': QColor(255, 69, 0, 70),      # Orange-Red for important
                'archive': QColor(128, 128, 128, 60),     # Gray for archived
                'work': QColor(0, 128, 255, 60),          # Blue for work
                'personal': QColor(255, 20, 147, 60),     # Deep Pink for personal
                'travel': QColor(34, 139, 34, 60),        # Forest Green for travel
                'family': QColor(255, 140, 0, 70),        # Dark Orange for family
            }
            
            # Apply colored mask based on tag priority (first matching tag)
            mask_applied = False
            for tag in tags:
                tag_lower = tag.lower().strip()
                if tag_lower in TAG_COLORS:
                    # Draw colored mask overlay on entire thumbnail
                    painter.fillRect(rect, TAG_COLORS[tag_lower])
                    mask_applied = True
                    break  # Only apply one mask (highest priority tag)
            
            # If no predefined color, use default subtle mask for any tagged photo
            if not mask_applied:
                painter.fillRect(rect, QColor(100, 100, 100, 40))  # Subtle gray for other tags
            
            # Draw stacked tag badges in top-right corner (up to 4), with '+n' overflow
            from settings_manager_qt import SettingsManager
            sm = SettingsManager()
            if not sm.get("badge_overlays_enabled", True):
                painter.restore()
                # ... keep existing code ...
                
                painter.restore()
            badge_size = int(sm.get("badge_size_px", 22))
            max_badges_setting = int(sm.get("badge_max_count", 4))
            badge_shape = str(sm.get("badge_shape", "circle")).lower()
            badge_margin = 4
            x_right = rect.right() - badge_margin - badge_size
            y_top = rect.top() + badge_margin

            # Map tags to icons and colors
            icons = []
            for t in (tags or []):
                tl = str(t).lower().strip()
                if tl == 'favorite':
                    icons.append(('★', QColor(255, 215, 0, 230), Qt.black))
                elif tl == 'face':
                    icons.append(('👤', QColor(70, 130, 180, 220), Qt.white))
                elif tl in ('important', 'flag'):
                    icons.append(('⚑', QColor(255, 69, 0, 220), Qt.white))
                elif tl in ('work',):
                    icons.append(('💼', QColor(0, 128, 255, 220), Qt.white))
                elif tl in ('travel',):
                    icons.append(('✈', QColor(34, 139, 34, 220), Qt.white))
                else:
                    icons.append(('🏷', QColor(150, 150, 150, 230), Qt.white))

            painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            max_badges = min(len(icons), max_badges_setting)
            for i in range(max_badges):
                by = y_top + i * (badge_size + 4)
                badge_rect = QRect(x_right, by, badge_size, badge_size)
                # subtle shadow
                painter.setPen(Qt.NoPen)
                if sm.get("badge_shadow", True):
                    painter.setBrush(QColor(0, 0, 0, 100))
                    painter.drawEllipse(badge_rect.adjusted(2, 2, 2, 2))
                ch, bg, fg = icons[i]
                painter.setBrush(bg)
                if badge_shape == 'square':
                    painter.drawRect(badge_rect)
                elif badge_shape == 'rounded':
                    painter.drawRoundedRect(badge_rect, 4, 4)
                else:
                    painter.drawEllipse(badge_rect)
                painter.setPen(QPen(fg))
                f = QFont()
                f.setPointSize(11)
                f.setBold(True)
                painter.setFont(f)
                painter.drawText(badge_rect, Qt.AlignCenter, ch)

            if len(icons) > max_badges:
                by = y_top + max_badges * (badge_size + 4)
                more_rect = QRect(x_right, by, badge_size, badge_size)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(60, 60, 60, 220))
                if badge_shape == 'square':
                    painter.drawRect(more_rect)
                elif badge_shape == 'rounded':
                    painter.drawRoundedRect(more_rect, 4, 4)
                else:
                    painter.drawEllipse(more_rect)
                painter.setPen(QPen(Qt.white))
                f2 = QFont()
                f2.setPointSize(10)
                f2.setBold(True)
                painter.setFont(f2)
                painter.drawText(more_rect, Qt.AlignCenter, f"+{len(icons) - max_badges}")

            painter.restore()


        if pm and not pm.isNull():
            orig_w = pm.width()
            orig_h = pm.height()
            if orig_h > 0:
                scale = target_h / orig_h
                target_w = int(orig_w * scale)
                scaled = pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = rect.x() + (rect.width() - scaled.width()) // 2
                y = rect.y() + (rect.height() - scaled.height()) // 2
                painter.drawPixmap(QRect(x, y, scaled.width(), scaled.height()), scaled)

                # 🏷️ PERMANENT TAG INDICATOR BADGES - Top-left corner (always visible)
                # This makes tagged photos instantly recognizable in the grid
                tags = index.data(Qt.UserRole + 2) or []
                if tags:
                    painter.save()
                    
                    # Define tag badge styling - matches color mask scheme
                    TAG_BADGE_CONFIG = {
                        'favorite': {
                            'bg_color': QColor(255, 215, 0, 240),    # Bright yellow
                            'icon': '★',
                            'icon_color': Qt.black,
                            'size': 28
                        },
                        'important': {
                            'bg_color': QColor(255, 69, 0, 240),     # Orange-red
                            'icon': '!',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                        'work': {
                            'bg_color': QColor(0, 128, 255, 240),    # Blue
                            'icon': '💼',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                        'travel': {
                            'bg_color': QColor(34, 139, 34, 240),    # Green
                            'icon': '✈',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                        'personal': {
                            'bg_color': QColor(255, 20, 147, 240),   # Pink
                            'icon': '♥',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                        'family': {
                            'bg_color': QColor(255, 140, 0, 240),    # Orange
                            'icon': '👨\u200d👩\u200d👧',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                        'archive': {
                            'bg_color': QColor(128, 128, 128, 240),  # Gray
                            'icon': '📦',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                        'face': {
                            'bg_color': QColor(70, 130, 180, 240),   # Steel blue
                            'icon': '👤',
                            'icon_color': Qt.white,
                            'size': 28
                        },
                    }
                    
                    # Find which tag to display (priority order)
                    badge_config = None
                    displayed_tag = None
                    for tag in tags:
                        tag_lower = tag.lower().strip()
                        if tag_lower in TAG_BADGE_CONFIG:
                            badge_config = TAG_BADGE_CONFIG[tag_lower]
                            displayed_tag = tag_lower
                            break
                    
                    # If no predefined tag, show generic tag badge
                    if not badge_config:
                        badge_config = {
                            'bg_color': QColor(150, 150, 150, 240),
                            'icon': '🏷',
                            'icon_color': Qt.white,
                            'size': 28
                        }
                    
                    # Draw tag badge in top-left corner
                    badge_size = badge_config['size']
                    badge_margin = 4
                    badge_rect = QRect(
                        rect.left() + badge_margin,
                        rect.top() + badge_margin,
                        badge_size,
                        badge_size
                    )
                    
                    painter.restore()

                    painter.save()
                    from settings_manager_qt import SettingsManager
                    sm = SettingsManager()
                    if not sm.get("badge_overlays_enabled", True):
                        painter.restore()
                    else:
                        badge_size = int(sm.get("badge_size_px", 22))
                        max_badges_setting = int(sm.get("badge_max_count", 4))
                        badge_shape = str(sm.get("badge_shape", "circle")).lower()
                        badge_margin = 4
                        x_right = rect.right() - badge_margin - badge_size
                        y_top = rect.top() + badge_margin
                        icons = []
                        for t in (tags or []):
                            tl = str(t).lower().strip()
                            if tl == 'favorite':
                                icons.append(('★', QColor(255, 215, 0, 230), Qt.black))
                            elif tl == 'face':
                                icons.append(('👤', QColor(70, 130, 180, 220), Qt.white))
                            elif tl in ('important', 'flag'):
                                icons.append(('⚑', QColor(255, 69, 0, 220), Qt.white))
                            elif tl in ('work',):
                                icons.append(('💼', QColor(0, 128, 255, 220), Qt.white))
                            elif tl in ('travel',):
                                icons.append(('✈', QColor(34, 139, 34, 220), Qt.white))
                            else:
                                icons.append(('🏷', QColor(150, 150, 150, 230), Qt.white))
                        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
                        max_badges = min(len(icons), max_badges_setting)
                        for i in range(max_badges):
                            by = y_top + i * (badge_size + 4)
                            badge_rect = QRect(x_right, by, badge_size, badge_size)
                            painter.setPen(Qt.NoPen)
                            if sm.get("badge_shadow", True):
                                painter.setBrush(QColor(0, 0, 0, 100))
                                painter.drawEllipse(badge_rect.adjusted(2, 2, 2, 2))
                            ch, bg, fg = icons[i]
                            painter.setBrush(bg)
                            if badge_shape == 'square':
                                painter.drawRect(badge_rect)
                            elif badge_shape == 'rounded':
                                painter.drawRoundedRect(badge_rect, 4, 4)
                            else:
                                painter.drawEllipse(badge_rect)
                            painter.setPen(QPen(fg))
                            f = QFont()
                            f.setPointSize(11)
                            f.setBold(True)
                            painter.setFont(f)
                            painter.drawText(badge_rect, Qt.AlignCenter, ch)
                        if len(icons) > max_badges:
                            by = y_top + max_badges * (badge_size + 4)
                            more_rect = QRect(x_right, by, badge_size, badge_size)
                            painter.setPen(Qt.NoPen)
                            painter.setBrush(QColor(60, 60, 60, 220))
                            if badge_shape == 'square':
                                painter.drawRect(more_rect)
                            elif badge_shape == 'rounded':
                                painter.drawRoundedRect(more_rect, 4, 4)
                            else:
                                painter.drawEllipse(more_rect)
                            painter.setPen(QPen(Qt.white))
                            f2 = QFont()
                            f2.setPointSize(10)
                            f2.setBold(True)
                            painter.setFont(f2)
                            painter.drawText(more_rect, Qt.AlignCenter, f"+{len(icons) - max_badges}")
                        painter.restore()

                # 🔹 Hover action strip (Favorite, Info, Delete) + checkbox
                try:
                    if hasattr(self, 'hover_row') and index.row() == getattr(self, 'hover_row', -1):
                        s = self.icon_size if hasattr(self, 'icon_size') else 22
                        m = self.icon_margin if hasattr(self, 'icon_margin') else 6
                        # Top-left checkbox
                        cb_rect = QRect(rect.left() + m, rect.top() + m, s, s)
                        painter.save()
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QColor(0, 0, 0, 110))
                        painter.drawRoundedRect(cb_rect, 4, 4)
                        painter.setPen(QPen(Qt.white))
                        painter.drawText(cb_rect, Qt.AlignCenter, "☐")
                        painter.restore()
                        # Top-right action icons
                        a1 = QRect(rect.right() - m - s, rect.top() + m, s, s)        # delete
                        a2 = QRect(rect.right() - m - 2*s - 4, rect.top() + m, s, s)   # info
                        a3 = QRect(rect.right() - m - 3*s - 8, rect.top() + m, s, s)   # favorite
                        
                        # 🌟 CRITICAL FIX: Color the favorite star based on tag state
                        # Yellow if tagged, white if untagged
                        tags = index.data(Qt.UserRole + 2) or []
                        is_favorited = 'favorite' in tags
                        
                        for r, ch in ((a3, "★"), (a2, "ℹ"), (a1, "🗑")):
                            painter.save()
                            painter.setPen(Qt.NoPen)
                            painter.setBrush(QColor(0, 0, 0, 140))
                            painter.drawRoundedRect(r, 6, 6)
                            
                            # Special coloring for favorite star
                            if ch == "★" and is_favorited:
                                painter.setPen(QPen(QColor(255, 215, 0)))  # Gold/yellow for favorited
                            else:
                                painter.setPen(QPen(Qt.white))  # White for others or unfavorited star
                            
                            painter.drawText(r, Qt.AlignCenter, ch)
                            painter.restore()
                except Exception:
                    pass

                # 🎬 Video duration badge (Phase 4.3)
                file_path = index.data(Qt.UserRole)
                if file_path and is_video_file(file_path):
                    # Get duration from video metadata (stored in UserRole + 3)
                    duration_seconds = index.data(Qt.UserRole + 3)

                    # Draw semi-transparent background for badge
                    painter.save()
                    badge_width = 50
                    badge_height = 20
                    badge_rect = QRect(
                        x + scaled.width() - badge_width - 4,
                        y + scaled.height() - badge_height - 4,
                        badge_width,
                        badge_height
                    )

                    # Draw rounded background
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(0, 0, 0, 180))
                    painter.drawRoundedRect(badge_rect, 3, 3)

                    # Draw duration text or play icon
                    painter.setPen(QPen(Qt.white))
                    font = QFont()
                    font.setPointSize(9)
                    font.setBold(True)
                    painter.setFont(font)

                    if duration_seconds and duration_seconds > 0:
                        duration_text = format_duration(duration_seconds)
                    else:
                        duration_text = "🎬"  # Show play icon if no duration

                    painter.drawText(badge_rect, Qt.AlignCenter, duration_text)
                    painter.restore()

                    # 🎯 Phase 3: Status indicators for video processing (top-left corner)
                    metadata_status = index.data(Qt.UserRole + 7)
                    thumbnail_status = index.data(Qt.UserRole + 8)

                    # Only show indicators if status is not 'ok' (show pending/error states)
                    if metadata_status and metadata_status != 'ok':
                        painter.save()
                        # Metadata status indicator (left side)
                        status_size = 18
                        status_x = x + 4
                        status_y = y + 4

                        # Color and icon based on status
                        if metadata_status == 'pending':
                            status_color = QColor(255, 165, 0, 200)  # Orange
                            status_icon = "⏳"
                        elif metadata_status == 'error':
                            status_color = QColor(255, 0, 0, 200)  # Red
                            status_icon = "❌"
                        else:  # unknown
                            status_color = QColor(128, 128, 128, 200)  # Gray
                            status_icon = "❓"

                        # Draw background circle
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(status_color)
                        painter.drawEllipse(status_x, status_y, status_size, status_size)

                        # Draw icon
                        painter.setPen(QPen(Qt.white))
                        icon_font = QFont()
                        icon_font.setPointSize(10)
                        painter.setFont(icon_font)
                        painter.drawText(QRect(status_x, status_y, status_size, status_size),
                                       Qt.AlignCenter, status_icon)
                        painter.restore()

                    if thumbnail_status and thumbnail_status != 'ok':
                        painter.save()
                        # Thumbnail status indicator (right side of metadata indicator)
                        status_size = 18
                        status_x = x + 4 + 20  # Offset from metadata indicator
                        status_y = y + 4

                        # Color and icon based on status
                        if thumbnail_status == 'pending':
                            status_color = QColor(255, 165, 0, 200)  # Orange
                            status_icon = "🖼"
                        elif thumbnail_status == 'error':
                            status_color = QColor(255, 0, 0, 200)  # Red
                            status_icon = "🚫"
                        else:  # unknown
                            status_color = QColor(128, 128, 128, 200)  # Gray
                            status_icon = "❓"

                        # Draw background circle
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(status_color)
                        painter.drawEllipse(status_x, status_y, status_size, status_size)

                        # Draw icon
                        painter.setPen(QPen(Qt.white))
                        icon_font = QFont()
                        icon_font.setPointSize(10)
                        painter.setFont(icon_font)
                        painter.drawText(QRect(status_x, status_y, status_size, status_size),
                                       Qt.AlignCenter, status_icon)
                        painter.restore()


        # 🟢 Focus glow
        if option.state & QStyle.State_HasFocus:
            painter.save()
            pen = QPen(QColor(30, 144, 255, 160))
            pen.setWidth(4)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            focus_rect = rect.adjusted(2, 2, -2, -2)
            painter.drawRoundedRect(focus_rect, 6, 6)
            painter.restore()
 

# === Phase 3: Drag & Drop Support ===
class DraggableThumbnailModel(QStandardItemModel):
    """
    Custom model that provides photo paths as MIME data for drag and drop.
    Enables dragging photos from the grid to sidebar folders/tags.
    """
    def mimeTypes(self):
        """Return list of MIME types this model supports for drag operations."""
        return ['text/uri-list', 'application/x-photo-paths']

    def mimeData(self, indexes):
        """
        Create MIME data from selected items.
        Extracts photo paths from Qt.UserRole and provides them in two formats:
        - text/uri-list: Standard file URIs for external apps
        - application/x-photo-paths: Custom format with newline-separated paths
        """
        mime_data = QMimeData()

        # Get unique photo paths from selected indexes
        paths = []
        for index in indexes:
            if index.isValid():
                path = index.data(Qt.UserRole)
                if path and path not in paths:
                    paths.append(path)

        if not paths:
            return mime_data

        # Format 1: text/uri-list (standard file URLs)
        from PySide6.QtCore import QUrl
        urls = [QUrl.fromLocalFile(str(p)) for p in paths]
        mime_data.setUrls(urls)

        # Format 2: application/x-photo-paths (custom format with paths separated by newlines)
        paths_text = '\n'.join(str(p) for p in paths)
        mime_data.setData('application/x-photo-paths', paths_text.encode('utf-8'))

        print(f"[DragDrop] Created MIME data for {len(paths)} photo(s)")
        return mime_data


class ThumbnailGridQt(QWidget):
    # inside class ThumbnailGridQt(QWidget):
    selectionChanged = Signal(int)# count of selected items
    deleteRequested = Signal(list)# list[str] paths to delete
    openRequested = Signal(str)#path to open (double-click/lightbox)
    gridReloaded = Signal()  # Phase 2.3: emitted after grid data is reloaded
    # 🏷️ ENHANCEMENT: Signal emitted when tags are modified (for details panel real-time update)
    tagsChanged = Signal()
        
    def __init__(self, project_id=None):
        super().__init__()        
        self.settings = settings  # use module-level settings instance
        
        self.db = ReferenceDB()  # new
        self.load_mode = "branch"  # or "folder" or "date"          
        self.project_id = project_id

        self.thumb_height = 160  # 👈 default thumbnail height
        
       # ✅ Unified navigation state
        self.navigation_mode = None        # 'folder', 'date', 'branch'
        self.navigation_key = None         # id or key (folder_id, date_key, etc.) depending on mode
        self.active_tag_filter = None      # current tag string: 'favorite', 'face', etc. or None

        # GPS Copy/Paste clipboard (stores copied location for quick reuse)
        # Format: {'lat': float, 'lon': float, 'location_name': str} or None
        self.copied_gps_location = None

        # legacy vars for backward compatibility
        self.load_mode = None
        self.current_folder_id = None
        self.date_key = None               # 'YYYY' or 'YYYY-MM-DD'
        self.branch_key = None
        
        # --- Reload debounce timer (coalesces rapid navigation clicks) ---
        self._reload_debounce_timer = QTimer(self)
        self._reload_debounce_timer.setSingleShot(True)
        self._reload_debounce_timer.setInterval(150)  # 150ms window
        self._reload_debounce_timer.timeout.connect(self._debounced_reload)
        self._pending_reload = False

        # --- Thumbnail pipeline safety ---
        self._reload_token = uuid.uuid4()
        # NOTE: _thumb_cache kept for backward compatibility but no longer used
        # ThumbnailService manages its own L1+L2 cache internally
        self._thumb_cache = {}        # Deprecated: use ThumbnailService instead
        self._thumbnail_service = get_thumbnail_service()
        self._decode_timeout = 5.0    # seconds for watchdog
        # shared placeholder pixmap (reuse to avoid many allocations)
        self._placeholder_pixmap = make_placeholder_pixmap(QSize(self.thumb_height, self.thumb_height))
        # P0 Fix #7: Cache scaled placeholder pixmaps by size to prevent memory leak
        self._placeholder_cache = {}  # key: (width, height), value: QPixmap
        self._current_reload_token = self._reload_token  # initialize for safety

        # P1-5 FIX: Track thumbnail load requests with timestamps to prevent stale flags
        self._thumb_request_timestamps = {}  # key: path, value: timestamp
        self._thumb_request_timeout = 30.0  # seconds - clear requests older than this


        # --- Thumbnail grid spacing (scales with zoom)
        self._base_spacing = self.settings.get("thumb_padding", 8)
        self._spacing = self._base_spacing
        self.cell_width_factor = 1.25

        # P2-26 FIX: Use dedicated thread pool instead of global instance
        # This prevents thumbnail operations from interfering with unrelated threaded tasks
        # Global pool may have contention from face detection, photo scan, device imports, etc.
        self.thread_pool = QThreadPool()
        # Respect user setting for worker count
        try:
            workers = int(self.settings.get("thumbnail_workers", 4))
        except Exception:
            workers = 4

        # P2-26 FIX: Apply reasonable cap and configure dedicated pool
        workers = max(1, min(workers, 8))
        self.thread_pool.setMaxThreadCount(workers)
        print(f"[GRID] P2-26: Created dedicated thumbnail thread pool with {workers} workers")

        self.thumb_signal = ThumbSignal()
        self.thumb_signal.preview.connect(self._on_thumb_loaded)  # show asap
        self.thumb_signal.loaded.connect(self._on_thumb_loaded)   # then refine
        self._paths = []
        
        # prefetch radius (number of items ahead/behind), configurable
        try:
            self._prefetch_radius = int(self.settings.get("thumbnail_prefetch", 8))
        except Exception:
            self._prefetch_radius = 8

        # --- Toolbar (Face Grouping + Zoom controls)
        # Phase 8: Face grouping buttons (moved from People tab for global access)
        self.btn_detect_and_group = QPushButton(tr('toolbar.detect_group_faces'))
        self.btn_detect_and_group.setToolTip("Automatically detect faces and group them into person albums")
        self.btn_detect_and_group.setStyleSheet("QPushButton{padding:5px 12px; font-weight:bold;}")
        # Handler will be connected from main_window_qt.py after grid is created

        self.btn_recluster = QPushButton(tr('toolbar.recluster'))
        self.btn_recluster.setToolTip("Re-group detected faces (without re-detecting)")
        self.btn_recluster.setStyleSheet("QPushButton{padding:5px 12px;}")
        # Handler will be connected from main_window_qt.py after grid is created

        # Zoom controls
        self.zoom_out_btn = QPushButton("-")
        self.zoom_out_btn.setFixedWidth(30)
        self.zoom_out_btn.clicked.connect(self.zoom_out)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(30)
        self.zoom_in_btn.clicked.connect(self.zoom_in)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 100)  # min and max height
        self.zoom_slider.setValue(50)

        self.zoom_slider.sliderPressed.connect(self._on_slider_pressed)
        self.zoom_slider.sliderReleased.connect(self._on_slider_released)
        self.zoom_slider.valueChanged.connect(self._on_slider_value_changed)

        # --- List view ---
        self.list_view = QListView()
        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setMovement(QListView.Static)
        self.list_view.setSelectionMode(QListView.ExtendedSelection)
        self.list_view.setWrapping(True)        
        self.list_view.setSpacing(self._spacing)
        self.list_view.setUniformItemSizes(True)

        # ✅ Enable touch gestures after list_view is created
        self.list_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        
        self.list_view.viewport().setMouseTracking(True)
        self.list_view.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.PinchGesture)

        # === Phase 3: Drag & Drop Support ===
        self.list_view.setDragEnabled(True)
        self.list_view.setDragDropMode(QAbstractItemView.DragOnly)
        self.list_view.setDefaultDropAction(Qt.CopyAction)

        # Delegates
        self.delegate = CenteredThumbnailDelegate(self.list_view)
        self.list_view.setItemDelegate(self.delegate)

        # Phase 3: Use draggable model for drag & drop support
        self.model = DraggableThumbnailModel(self.list_view)
        self.list_view.setModel(self.model)

        # --- Context menu ---
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._on_context_menu)
        
        # selection behavior & key handling
        self.list_view.setSelectionBehavior(QListView.SelectItems)
        self.list_view.setSelectionRectVisible(True)
        self.list_view.installEventFilter(self)  # capture keyboard in the view

        # notify selection count
        self.list_view.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionChanged.emit(len(self.get_selected_paths()))
        )

        # double-click = open in lightbox
        self.list_view.doubleClicked.connect(self._on_double_clicked)
        
        # --- 📸 Initialize new zoom system here ---
        self._init_zoom()  # 👈 important!

        # Toolbar
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        # Face grouping buttons (left side)
        toolbar_layout.addWidget(self.btn_detect_and_group)
        toolbar_layout.addWidget(self.btn_recluster)
        toolbar_layout.addSpacing(20)  # Add space between face buttons and zoom controls

        # Zoom controls (right side)
        toolbar_layout.addStretch()  # Push zoom controls to the right
        toolbar_layout.addWidget(self.zoom_out_btn)
        toolbar_layout.addWidget(self.zoom_slider)
        toolbar_layout.addWidget(self.zoom_in_btn)

        # --- Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar_layout)   # 👈 add toolbar on top
        layout.addWidget(self.list_view)

        # debounce timer for requests
        self._rv_timer = QTimer(self)
        self._rv_timer.setSingleShot(True)
        self._rv_timer.timeout.connect(self.request_visible_thumbnails)

        # ── Batched model population ──────────────────────────────
        from services.photo_query_service import (
            SMALL_THRESHOLD as _SVC_SMALL_THRESHOLD,
            PAGE_SIZE as _SVC_PAGE_SIZE,
        )
        self._grid_small_threshold = _SVC_SMALL_THRESHOLD
        self._grid_page_size = _SVC_PAGE_SIZE
        self._grid_batch_state = None   # dict with remaining batch data
        self._grid_batch_timer = QTimer(self)
        self._grid_batch_timer.setSingleShot(True)
        self._grid_batch_timer.timeout.connect(self._process_grid_batch)
        

        # 🔁 Hook scrollbars AFTER timer exists (debounced incremental scheduling)
        def _on_scroll():
            self._rv_timer.start(50)
            # PHASE 4: Save scroll position to session state (debounced)
            self._save_scroll_position()
            # Batched population: trigger next batch when near bottom
            if self._grid_batch_state and not self._grid_batch_timer.isActive():
                sb = self.list_view.verticalScrollBar()
                if sb and sb.maximum() > 0:
                    remaining = sb.maximum() - sb.value()
                    if remaining < self.list_view.viewport().height() * 2:
                        self._grid_batch_timer.start(0)

        self.list_view.verticalScrollBar().valueChanged.connect(_on_scroll)
        self.list_view.horizontalScrollBar().valueChanged.connect(_on_scroll)

        # PHASE 4: Timer for debouncing scroll position saves
        self._scroll_save_timer = QTimer(self)
        self._scroll_save_timer.setSingleShot(True)
        self._scroll_save_timer.timeout.connect(self._do_save_scroll_position)
        
# ===================================================
    # --- Normalization helper used everywhere (model key, cache key, worker emits) ---
    def _norm_path(self, p: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(str(p).strip())))
        except Exception:
            return str(p).strip().lower()

    # ===================================================
    # PHASE 4: Scroll position persistence
    # ===================================================

    def _save_scroll_position(self):
        """
        PHASE 4: Debounced scroll position save.
        Starts a timer to delay the actual save (prevents save spam during scrolling).
        """
        # Restart timer on every scroll (debounce)
        self._scroll_save_timer.start(500)  # Save 500ms after scrolling stops

    def _do_save_scroll_position(self):
        """
        PHASE 4: Actually save scroll position to session state.
        Called after scrolling stops (debounced).
        """
        try:
            scrollbar = self.list_view.verticalScrollBar()
            scroll_max = scrollbar.maximum()

            if scroll_max > 0:
                # Calculate scroll position as percentage (0.0 to 1.0)
                scroll_value = scrollbar.value()
                position = scroll_value / scroll_max
            else:
                position = 0.0

            # Save to session state
            from session_state_manager import get_session_state
            get_session_state().set_scroll_position(position)

        except Exception:
            pass  # Silently ignore errors (scroll position is non-critical)

    def restore_scroll_position(self):
        """
        PHASE 4: Restore scroll position from session state.
        Should be called after photos are loaded into grid.
        """
        try:
            from session_state_manager import get_session_state
            position = get_session_state().get_scroll_position()

            if position > 0:
                scrollbar = self.list_view.verticalScrollBar()
                scroll_max = scrollbar.maximum()

                if scroll_max > 0:
                    # Convert percentage back to scroll value
                    scroll_value = int(position * scroll_max)
                    scrollbar.setValue(scroll_value)
                    print(f"[GRID] PHASE 4: Restored scroll position: {position:.1%} ({scroll_value}/{scroll_max})")

        except Exception as e:
            print(f"[GRID] PHASE 4: Failed to restore scroll position: {e}")


    def request_visible_thumbnails(self):
        """
        Compute visible rows in the list_view and submit workers only for those,
        plus a small prefetch radius. Prevents scheduling workers for the entire dataset.

        Uses scrollbar position for reliable viewport calculation in IconMode.
        """
        try:
            viewport = self.list_view.viewport()
            rect = viewport.rect()
            if rect.isNull() or self.model.rowCount() == 0:
                # reschedule if viewport not yet fully laid out
                QTimer.singleShot(50, self.request_visible_thumbnails)
                return
            
            # CRITICAL FIX: Ensure layout is complete before calculating visible range
            # This prevents white thumbnails on first branch click
            if rect.width() <= 1 or rect.height() <= 1:
                print(f"[GRID] Viewport not ready (size: {rect.width()}x{rect.height()}), rescheduling...")
                QTimer.singleShot(100, self.request_visible_thumbnails)
                return

            # P2-27 FIX: Cache indexAt() results using scroll position as key
            # This avoids expensive layout calculations on every scroll pixel
            scrollbar = self.list_view.verticalScrollBar()
            scroll_value = scrollbar.value()
            scroll_max = scrollbar.maximum()

            # P2-27 FIX: Use cached viewport range if scroll position unchanged
            cache_key = (scroll_value, rect.height(), self.model.rowCount())
            if not hasattr(self, '_viewport_range_cache'):
                self._viewport_range_cache = {}

            if cache_key in self._viewport_range_cache:
                start, end = self._viewport_range_cache[cache_key]
                # Silently use cache (no print to avoid spam)
            else:
                # Calculate viewport range with indexAt() fallback
                # Calculate approximate start position based on scroll percentage
                if scroll_max > 0:
                    scroll_fraction = scroll_value / scroll_max
                    approx_start = int(scroll_fraction * self.model.rowCount())
                else:
                    approx_start = 0

                # Try indexAt() first, but fall back to scroll-based calculation
                top_index = self.list_view.indexAt(QPoint(rect.left(), rect.top()))
                if top_index.isValid() and top_index.row() > approx_start - 50:
                    # indexAt() is working and gives reasonable result
                    start = top_index.row()
                else:
                    # indexAt() failed or unreliable, use scroll-based estimate
                    start = max(0, approx_start - 20)  # Start a bit before scroll position
                    print(f"[GRID] Using scroll-based start position: {start} (scroll: {scroll_value}/{scroll_max})")

                # Calculate end position
                bottom_index = self.list_view.indexAt(QPoint(rect.left(), rect.bottom() - 1))

                if bottom_index.isValid() and bottom_index.row() > start:
                    end = bottom_index.row()
                else:
                    # Calculate based on grid layout
                    first_item = self.model.item(max(0, start))
                    if first_item:
                        item_width = first_item.sizeHint().width() + self._spacing
                        item_height = first_item.sizeHint().height() + self._spacing
                        if item_width > 0 and item_height > 0:
                            items_per_row = max(1, rect.width() // item_width)
                            visible_rows = (rect.height() // item_height) + 2  # +2 for partial rows
                            visible_items = visible_rows * items_per_row
                            end = min(self.model.rowCount() - 1, start + visible_items)
                        else:
                            end = min(self.model.rowCount() - 1, start + 150)
                    else:
                        end = min(self.model.rowCount() - 1, start + 150)

                # P2-27 FIX: Store calculated range in cache
                # (Store before prefetch expansion for accurate caching)
                self._viewport_range_cache[cache_key] = (start, end)
                # P2-27 FIX: Limit cache size to prevent unbounded growth
                if len(self._viewport_range_cache) > 20:
                    # Remove oldest entries (FIFO)
                    oldest_key = next(iter(self._viewport_range_cache))
                    del self._viewport_range_cache[oldest_key]

            # Expand range by prefetch radius
            start = max(0, start - self._prefetch_radius)
            end = min(self.model.rowCount() - 1, end + self._prefetch_radius)

            # If near bottom, load all remaining
            remaining = self.model.rowCount() - end - 1
            if remaining > 0 and remaining < 100:
                end = self.model.rowCount() - 1
                print(f"[GRID] Near bottom, loading all remaining {remaining} items")

            print(f"[GRID] Loading viewport range: {start}-{end} of {self.model.rowCount()}")

            # CRASH FIX: Validate placeholder pixmap before starting workers
            if not self._placeholder_pixmap or self._placeholder_pixmap.isNull():
                print(f"[GRID] ⚠️ Placeholder pixmap is invalid, skipping thumbnail loading")
                return

            token = self._reload_token
            loaded_count = 0
            for row in range(start, end + 1):
                try:
                    item = self.model.item(row)
                    if not item:
                        continue

                    npath = item.data(Qt.UserRole)        # normalized key
                    rpath = item.data(Qt.UserRole + 6)    # real path
                    if not npath or not rpath:
                        continue

                    # avoid resubmitting while already scheduled
                    if item.data(Qt.UserRole + 5):
                        continue

                    # schedule worker
                    item.setData(True, Qt.UserRole + 5)  # mark scheduled
                    thumb_h = int(self._thumb_base * self._zoom_factor)

                    # CRASH FIX: Validate parameters before creating worker
                    if thumb_h <= 0 or thumb_h > 4000:
                        print(f"[GRID] ⚠️ Invalid thumb_h={thumb_h}, skipping row {row}")
                        continue

                    w = ThumbWorker(rpath, npath, thumb_h, row, self.thumb_signal,
                                    self._thumb_cache, token, self._placeholder_pixmap)

                    self.thread_pool.start(w)
                    loaded_count += 1

                except Exception as row_error:
                    print(f"[GRID] ⚠️ Error processing row {row}: {row_error}")
                    continue

            if loaded_count > 0:
                print(f"[GRID] Queued {loaded_count} new thumbnail workers")

        except Exception as e:
            print(f"[GRID] request_visible_thumbnails error: {e}")
            import traceback
            traceback.print_exc()

    def event(self, ev):
        if ev.type() == QEvent.Gesture:
            gesture = ev.gesture(Qt.PinchGesture)
            if gesture is not None:
                # NOTE: No need to cast — gesture has scaleFactor()
                scale = gesture.scaleFactor()
                self._apply_pinch_zoom(scale)
                return True
        return super().event(ev)

    def _apply_pinch_zoom(self, scale):
        # You can map scale to your zoom slider or directly adjust thumb size
        new_val = max(50, min(400, self.zoom_slider.value() * scale))
        self.zoom_slider.setValue(int(new_val))

    def _normalize_date_key(self, val: str) -> str | None:
        """
        Normalize a 'date' payload to one of:
          • 'YYYY'
          • 'YYYY-MM'  (zero-padded month)
          • 'YYYY-MM-DD'
        Returns None if not a recognized format.
        """
        s = (val or "").strip()

        # Year
        if re.fullmatch(r"\d{4}", s):
            return s

        # Year-Month (allow 1 or 2 digits for month)
        m = re.match(r"^(\d{4})-(\d{1,2})$", s)
        if m:
            y, mo = m.groups()
            try:
                mo_i = int(mo)
                if 1 <= mo_i <= 12:
                    return f"{y}-{mo_i:02d}"
            except Exception:
                pass
            return None

        # Day
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s

        return None


    def load_custom_paths(self, paths, content_type="auto"):
        """
        Directly load an arbitrary list of image/video paths (used by tag filters and video filters).

        Args:
            paths: List of file paths to display
            content_type: "auto" (detect from paths), "photos", or "videos"
        """
        import os

        # Auto-detect content type if not specified
        if content_type == "auto" and paths:
            # Check first few paths to determine content type
            sample_size = min(10, len(paths))
            video_count = sum(1 for p in paths[:sample_size] if is_video_file(p))
            if video_count > sample_size / 2:
                content_type = "videos"
            else:
                content_type = "photos"

        # Set appropriate mode based on content type
        if content_type == "videos":
            self.load_mode = "videos"
        else:
            self.load_mode = "tag"

        self.branch_key = None
        self.current_folder_id = None
        self.date_key = None
        
        def norm(p):
            return os.path.normcase(os.path.normpath(p.strip()))

        self.model.clear()

        # ✅ normalize paths to match how they're stored in DB
        self._paths = [norm(p) for p in (paths or [])]

        # 🐞 FIX: Fetch tags for BOTH photos AND videos (not just photos)
        # The database query now supports video_metadata table too!
        tag_map = {}
        try:
            tag_map = self.db.get_tags_for_paths(self._paths, self.project_id)
            paths_with_tags = sum(1 for v in tag_map.values() if v)
            print(f"[GRID] Queried tags for {len(self._paths)} paths ({content_type}), {paths_with_tags} have tags")
        except Exception as e:
            print(f"[GRID] Warning: Could not fetch tags: {e}")

        # FIX (2026-02-08): Preload aspect ratios from DB instead of PIL.Image.open on UI thread
        # This prevents UI freezes when loading thumbnails (Google Photos best practice)
        aspect_map = {}
        try:
            if hasattr(self.db, 'get_aspect_ratios_for_paths'):
                aspect_map = self.db.get_aspect_ratios_for_paths(self._paths, self.project_id)
        except Exception as e:
            print(f"[GRID] Warning: Could not fetch aspect ratios: {e}")

        # Use current reload token snapshot so workers can be tied to this load
        token = self._reload_token

        # 🗓️ Date-based sorting and grouping only for photos (not videos)
        use_date_headers = (content_type != "videos")
        if use_date_headers:
            def _safe_date(path):
                try:
                    meta = self.db.get_photo_metadata_by_path(path)
                    dt_str = meta.get('date_taken') if meta else None
                    if dt_str:
                        dt = datetime.fromisoformat(dt_str)
                        return dt.timestamp()
                except Exception:
                    pass
                try:
                    return os.path.getmtime(path)
                except Exception:
                    return 0.0

            date_map = {p: _safe_date(p) for p in self._paths}
            sorted_paths = sorted(self._paths, key=lambda x: date_map.get(x, 0.0), reverse=True)
        else:
            date_map = {}
            sorted_paths = list(self._paths)

        def _group_label(ts: float):
            d = datetime.fromtimestamp(ts).date()
            today = datetime.now().date()
            delta = (today - d).days
            if delta == 0: return "Today"
            if delta == 1: return "Yesterday"
            if 0 < delta < 7: return "This Week"
            if today.year == d.year and today.month == d.month: return "This Month"
            return datetime.fromtimestamp(ts).strftime("%B %Y")

        for i, p in enumerate(sorted_paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)  # 🏷️ store tags for paint()
            item.setToolTip(", ".join([t for t in (tag_map.get(p, []) or []) if t]))

            # 🗓️ Date-group header (only for photos)
            if use_date_headers:
                ts = date_map.get(p, 0.0)
                lbl = _group_label(ts) if ts else None
                if i == 0:
                    header_label = lbl
                else:
                    prev_ts = date_map.get(sorted_paths[i-1], 0.0)
                    prev_lbl = _group_label(prev_ts) if prev_ts else None
                    header_label = lbl if (lbl != prev_lbl) else None
            else:
                header_label = None
            item.setData(header_label, Qt.UserRole + 10)

            # --- Set placeholder size based on DB-stored aspect ratio (no UI-thread I/O)
            # FIX (2026-02-08): Use preloaded aspect_map instead of PIL.Image.open
            aspect_ratio = aspect_map.get(p, 1.5)
            item.setData(aspect_ratio, Qt.UserRole + 1)
            
            size0 = self._thumb_size_for_aspect(aspect_ratio)
            if use_date_headers and header_label:
                size0 = QSize(size0.width(), size0.height() + 24)
            item.setSizeHint(size0)

            self.model.appendRow(item)
            thumb_h = int(self._thumb_base * self._zoom_factor)
            # ThumbWorker signature: real_path, norm_path, height, row, signal_obj, cache, reload_token, placeholder
            worker = ThumbWorker(p, p, thumb_h, i, self.thumb_signal, self._thumb_cache, token, self._placeholder_pixmap)

            # P1-5 FIX: Track request timestamp
            import time
            self._thumb_request_timestamps[p] = time.time()

            self.thread_pool.start(worker)

        # P1-5 FIX: Clean up stale thumbnail requests after scheduling new ones
        self._cleanup_stale_thumb_requests()

        # Trigger thumbnail loading
        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()

        # 🔧 FIX: Force complete geometry update
        def _force_geometry_update():
            self.list_view.setSpacing(self._spacing)
            self.list_view.updateGeometry()
            self.list_view.doItemsLayout()
            self.list_view.repaint()
        
        QTimer.singleShot(100, _force_geometry_update)
        QTimer.singleShot(200, _force_geometry_update)
        
        self.list_view.viewport().update()

        mode_label = "videos" if content_type == "videos" else "tag"
        print(f"[GRID] Loaded {len(self._paths)} thumbnails in {mode_label}-mode.")


    def _cleanup_stale_thumb_requests(self):
        """
        P1-5 FIX: Remove stale thumbnail request timestamps.
        Clears requests older than timeout to allow retries of failed loads.
        """
        import time
        current_time = time.time()
        stale_keys = [
            key for key, timestamp in self._thumb_request_timestamps.items()
            if current_time - timestamp > self._thumb_request_timeout
        ]
        for key in stale_keys:
            del self._thumb_request_timestamps[key]
        if stale_keys:
            print(f"[GRID] Cleaned up {len(stale_keys)} stale thumbnail requests")

    def shutdown_threads(self):
        """Stop accepting new tasks and wait for current ones to finish."""
        if self.thread_pool:
            # global threadpool has no waitForDone in some contexts; try to be graceful
            try:
                self.thread_pool.waitForDone(2000)  # wait max 2 seconds
            except Exception:
                pass


    def apply_sorting(self, field: str, descending: bool = False):
        """
        Sort current _paths list and rebuild model.
        """
        if not self._paths:
            return
        reverse = descending
        if field == "filename":
            self._paths.sort(key=lambda p: str(p).lower(), reverse=reverse)
        elif field == "date":
            import os
            self._paths.sort(key=lambda p: os.path.getmtime(p), reverse=reverse)
        elif field == "size":
            import os
            self._paths.sort(key=lambda p: os.path.getsize(p), reverse=reverse)

        # rebuild
        self.model.clear()
        token = self._reload_token
        thumb_h = int(self._thumb_base * self._zoom_factor) if hasattr(self, '_thumb_base') else self.thumb_height
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setSizeHint(QSize(self.thumb_height, self.thumb_height + self._spacing))
            self.model.appendRow(item)
            # FIX: ThumbWorker requires 8 args: real_path, norm_path, height, row, signal_obj, cache, reload_token, placeholder
            worker = ThumbWorker(p, p, thumb_h, i, self.thumb_signal, self._thumb_cache, token, self._placeholder_pixmap)
            self.thread_pool.start(worker)


    def _on_thumb_loaded(self, path: str, qimage, row: int):
        """
        Called asynchronously when a thumbnail has been loaded.

        FIX 2026-02-08: Now receives QImage (thread-safe) and converts to QPixmap
        on the UI thread where it's safe to do so.
        """
        # --- Token safety check ---
        if getattr(self, "_current_reload_token", None) != self._reload_token:
            print(f"[GRID] Discarded stale thumbnail: {path}")
            return

        # path here is ALWAYS normalized; match by key
        item = self.model.item(row) if (0 <= row < self.model.rowCount()) else None
        if (not item) or (item.data(Qt.UserRole) != path):
            item = None
            for r in range(self.model.rowCount()):
                it = self.model.item(r)
                if it and it.data(Qt.UserRole) == path:
                    item = it
                    row = r
                    break
        if not item:
            return

        # FIX 2026-02-08: Convert QImage to QPixmap on UI thread (safe!)
        # The worker now sends QImage which is thread-safe
        pm = None
        if qimage is not None and not qimage.isNull():
            pm = QPixmap.fromImage(qimage)

        # 🧠 Use cached pixmap if invalid
        if pm is None or pm.isNull():
            real_path = item.data(Qt.UserRole + 6) or path
            pm = load_thumbnail_safe(real_path,
                                     int(self._thumb_base * self._zoom_factor),
                                     self._thumb_cache, self._decode_timeout, self._placeholder_pixmap)

        # 🧮 Update metadata and UI
        aspect_ratio = pm.width() / pm.height() if pm and pm.height() > 0 else 1.5
        item.setData(aspect_ratio, Qt.UserRole + 1)
        item.setSizeHint(self._thumb_size_for_aspect(aspect_ratio))
        item.setIcon(QIcon(pm))

        item.setData(False, Qt.UserRole + 5)   # allow future requeue   
        
        # allow future rescheduling after zoom/scroll
        item.setData(False, Qt.UserRole + 5)

        # NOTE: ThumbnailService handles all cache updates internally
        # No need to manually update memory cache here

        # ✅ Redraw the updated thumbnail cell
        try:
            rect = self.list_view.visualRect(self.model.indexFromItem(item))
            self.list_view.viewport().update(rect)
        except Exception:
            self.list_view.viewport().update()


    def clear(self):
        self.model.clear()
        self._paths.clear()
        self.branch_key = None


    def get_selected_paths(self):
        selection = self.list_view.selectionModel().selectedIndexes()
        return [i.data(Qt.UserRole) for i in selection]


    def _on_context_menu(self, pos: QPoint):
        idx = self.list_view.indexAt(pos)
        paths = self.get_selected_paths()
        if not idx.isValid() and not paths:
            return
        if not paths and idx.isValid():
            paths = [idx.data(Qt.UserRole)]

        db = self.db

        # Build dynamic tag info
        all_tags = []
        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            all_tags = tag_service.get_all_tags(self.project_id)
        except Exception:
            pass

        # tags present across selection (use TagService for consistency)
        present_map = {}
        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            present_map = tag_service.get_tags_for_paths(paths, self.project_id)
            print(f"[ContextMenu] Got tags for {len(paths)} path(s): {present_map}")
        except Exception as e:
            print(f"[ContextMenu] Error getting tags: {e}")
            present_map = {}
        present_tags = set()
        for tlist in present_map.values():
            present_tags.update([t.strip() for t in tlist if t.strip()])

        print(f"[ContextMenu] present_tags = {present_tags}")

        # Menu
        m = QMenu(self)
        act_open = m.addAction(tr('context_menu.open'))
        act_reveal = m.addAction(tr('context_menu.reveal_explorer'))
        m.addSeparator()

        # Single unified Tags submenu with toggle behavior
        tag_menu = m.addMenu(tr('context_menu.tags'))

        # Quick presets (favorite and face) - always shown
        act_fav = tag_menu.addAction(tr('context_menu.favorite'))
        act_fav.setCheckable(True)
        if "favorite" in present_tags:
            act_fav.setChecked(True)

        act_face = tag_menu.addAction(tr('context_menu.face'))
        act_face.setCheckable(True)
        if "face" in present_tags:
            act_face.setChecked(True)

        tag_menu.addSeparator()

        # All existing tags with checkmarks for present ones
        toggle_actions = {}
        for t in sorted(all_tags):
            # Skip favorite and face since they're already in quick presets
            if t.lower() in ["favorite", "face"]:
                continue

            act = tag_menu.addAction(t)
            act.setCheckable(True)
            if t in present_tags:
                act.setChecked(True)
            toggle_actions[act] = t

        tag_menu.addSeparator()
        act_new_tag = tag_menu.addAction(tr('context_menu.new_tag'))

        # Clear All Tags - top level for visibility
        act_clear_all = m.addAction(tr('context_menu.clear_all_tags'))
        if not present_tags:
            act_clear_all.setEnabled(False)  # Disable if no tags present

        # Edit Location - manual GPS editing
        # CRITICAL FIX: Add batch editing support (Google Photos pattern)
        if len(paths) > 1:
            # Show both batch and single edit options for clarity
            act_edit_location_batch = m.addAction(f"📍 Edit Location ({len(paths)} selected photos)...")
            act_edit_location_single = m.addAction(f"📍 Edit Location (this photo only)...")
        else:
            # Single photo mode
            act_edit_location = m.addAction("📍 Edit Location...")

        # Copy/Paste Location actions (inspired by Google Photos & iPhone Photos)
        act_copy_location = m.addAction("📍 Copy Location")

        # Show paste option only if we have copied GPS data
        act_paste_location = None
        if self.copied_gps_location:
            paste_text = f"📍 Paste Location ({self.copied_gps_location.get('location_name', 'Location')})"
            if len(paths) > 1:
                paste_text = f"📍 Paste Location to {len(paths)} photos ({self.copied_gps_location.get('location_name', 'Location')})"
            act_paste_location = m.addAction(paste_text)

        m.addSeparator()

        # Edit Metadata action - opens metadata editor dock for this photo
        act_edit_metadata = m.addAction("✏️ Edit Metadata")

        # Find Similar - Excire-style visual similarity search
        act_find_similar = None
        if len(paths) == 1:
            act_find_similar = m.addAction("\U0001f3af Find Similar Photos...")

        m.addSeparator()
        act_export = m.addAction(tr('context_menu.export'))
        act_delete = m.addAction(tr('context_menu.delete'))

        chosen = m.exec(self.list_view.viewport().mapToGlobal(pos))
        if not chosen:
            return

        # Actions
        if chosen is act_open:
            self.openRequested.emit(paths[-1])

        elif chosen is act_reveal:
            try:
                import os
                for p in paths[:1]:
                    os.startfile(p)
            except Exception:
                pass

        elif chosen is act_export:
            self.deleteRequested.emit([])

        elif chosen is act_delete:
            self.deleteRequested.emit(paths)

        elif chosen is act_edit_metadata:
            # Open metadata editor dock for the first selected photo
            self._show_metadata_editor_for_photo(paths[0] if paths else None)

        elif act_find_similar and chosen is act_find_similar:
            self._open_find_similar_for_path(paths[0])

        elif chosen is act_fav:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    tr('message_boxes.no_selection_title'),
                    tr('message_boxes.no_selection_message')
                )
                return

            # TOGGLE: Remove if present, add if absent
            tag_service = get_tag_service()
            if "favorite" in present_tags:
                # Remove from all selected photos
                for p in paths:
                    tag_service.remove_tag(p, "favorite", self.project_id)
                print(f"[Tag] Removed 'favorite' → {len(paths)} photo(s)")
            else:
                # Add to all selected photos
                count = tag_service.assign_tags_bulk(paths, "favorite", self.project_id)
                print(f"[Tag] Added 'favorite' → {count} photo(s)")

            # CRITICAL: Wrap post-tag operations in try/except to prevent crashes
            try:
                self._refresh_tags_for_paths(paths)
            except Exception as e:
                print(f"[Tag] Warning: Failed to refresh tag overlays: {e}")

            # 🪄 Refresh sidebar tags
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Tag] Warning: Failed to reload sidebar tags: {e}")

            # 🔄 Reload grid if we removed the active tag filter
            if "favorite" in present_tags:
                active_tag = getattr(self, "context", {}).get("tag_filter")
                if active_tag and active_tag.lower() == "favorite":
                    print(f"[Tag] Reloading grid - removed tag matches active filter 'favorite'")
                    try:
                        self.reload()
                    except Exception as e:
                        print(f"[Tag] Warning: Failed to reload grid: {e}")
                        # Clear the tag filter to prevent showing stale data
                        if hasattr(self, "context") and "tag_filter" in self.context:
                            self.context["tag_filter"] = None
                            self.reload()  # Try again without filter

        elif chosen is act_face:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    tr('message_boxes.no_selection_title'),
                    tr('message_boxes.no_selection_message')
                )
                return

            # TOGGLE: Remove if present, add if absent
            tag_service = get_tag_service()
            if "face" in present_tags:
                # Remove from all selected photos
                for p in paths:
                    tag_service.remove_tag(p, "face", self.project_id)
                print(f"[Tag] Removed 'face' → {len(paths)} photo(s)")
            else:
                # Add to all selected photos
                count = tag_service.assign_tags_bulk(paths, "face", self.project_id)
                print(f"[Tag] Added 'face' → {count} photo(s)")

            # CRITICAL: Wrap post-tag operations in try/except to prevent crashes
            try:
                self._refresh_tags_for_paths(paths)
            except Exception as e:
                print(f"[Tag] Warning: Failed to refresh tag overlays: {e}")

            # 🪄 Refresh sidebar tags
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Tag] Warning: Failed to reload sidebar tags: {e}")

            # 🔄 Reload grid if we removed the active tag filter
            if "face" in present_tags:
                active_tag = getattr(self, "context", {}).get("tag_filter")
                if active_tag and active_tag.lower() == "face":
                    print(f"[Tag] Reloading grid - removed tag matches active filter 'face'")
                    try:
                        self.reload()
                    except Exception as e:
                        print(f"[Tag] Warning: Failed to reload grid: {e}")
                        # Clear the tag filter to prevent showing stale data
                        if hasattr(self, "context") and "tag_filter" in self.context:
                            self.context["tag_filter"] = None
                            self.reload()  # Try again without filter

        elif chosen in toggle_actions:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    tr('message_boxes.no_selection_title'),
                    tr('message_boxes.no_selection_message')
                )
                return

            # TOGGLE: Remove if present, add if absent
            tagname = toggle_actions[chosen]
            tag_service = get_tag_service()

            if tagname in present_tags:
                # Remove from all selected photos
                for p in paths:
                    tag_service.remove_tag(p, tagname, self.project_id)
                print(f"[Tag] Removed '{tagname}' → {len(paths)} photo(s)")
            else:
                # Add to all selected photos
                count = tag_service.assign_tags_bulk(paths, tagname, self.project_id)
                print(f"[Tag] Added '{tagname}' → {count} photo(s)")

            # CRITICAL: Wrap post-tag operations in try/except to prevent crashes
            try:
                self._refresh_tags_for_paths(paths)
            except Exception as e:
                print(f"[Tag] Warning: Failed to refresh tag overlays: {e}")

            # 🪄 Refresh sidebar tags
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Tag] Warning: Failed to reload sidebar tags: {e}")

            # 🔄 Reload grid if we removed the active tag filter
            if tagname in present_tags:
                active_tag = getattr(self, "context", {}).get("tag_filter")
                if active_tag and active_tag.lower() == tagname.lower():
                    print(f"[Tag] Reloading grid - removed tag matches active filter '{active_tag}'")
                    try:
                        self.reload()
                    except Exception as e:
                        print(f"[Tag] Warning: Failed to reload grid: {e}")
                        # Clear the tag filter to prevent showing stale data
                        if hasattr(self, "context") and "tag_filter" in self.context:
                            self.context["tag_filter"] = None
                            self.reload()  # Try again without filter

        elif chosen is act_new_tag:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    "No Photos Selected",
                    "Please select one or more photos before creating and assigning a tag."
                )
                return

            # ARCHITECTURE: UI Layer → TagService → TagRepository → Database
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
            if ok and name.strip():
                tname = name.strip()
                tag_service = get_tag_service()
                # Ensure tag exists and assign to photos (Schema v3.1.0)
                tag_service.ensure_tag_exists(tname, self.project_id)
                count = tag_service.assign_tags_bulk(paths, tname, self.project_id)
                print(f"[Tag] Created and assigned '{tname}' → {count} photo(s)")
                self._refresh_tags_for_paths(paths)

                # 🪄 Refresh sidebar tags
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()

        elif chosen is act_clear_all:
            # ARCHITECTURE: UI Layer → TagService → TagRepository → Database
            # Remove all present tags from selection
            tag_service = get_tag_service()
            for p in paths:
                for t in list(present_tags):
                    tag_service.remove_tag(p, t, self.project_id)
            print(f"[Tag] Cleared all tags → {len(paths)} photo(s)")
            self._refresh_tags_for_paths(paths)

            # 🪄 Refresh sidebar tags
            mw = self.window()
            if hasattr(mw, "sidebar"):
                if hasattr(mw.sidebar, "reload_tags_only"):
                    mw.sidebar.reload_tags_only()
                else:
                    mw.sidebar.reload()

            # 🔄 Reload grid if viewing a tag branch that was just cleared
            active_tag = getattr(self, "context", {}).get("tag_filter")
            if active_tag and active_tag.lower() in [t.lower() for t in present_tags]:
                print(f"[Tag] Reloading grid - cleared tags include active filter '{active_tag}'")
                self.reload()

        # CRITICAL FIX: Handle both single and batch location editing
        elif (len(paths) == 1 and 'act_edit_location' in locals() and chosen is act_edit_location):
            # Single photo mode
            self._edit_photo_location(paths[0])

        elif (len(paths) > 1 and 'act_edit_location_batch' in locals() and chosen is act_edit_location_batch):
            # Batch editing mode
            self._edit_photos_location_batch(paths)

        elif (len(paths) > 1 and 'act_edit_location_single' in locals() and chosen is act_edit_location_single):
            # Single photo from multi-selection (edit just the clicked photo)
            self._edit_photo_location(paths[0])

        # Copy/Paste Location handlers
        elif chosen is act_copy_location:
            self._copy_location(paths[0] if paths else None)

        elif act_paste_location and chosen is act_paste_location:
            self._paste_location(paths)


    def _open_find_similar_for_path(self, path: str):
        """
        Open Find Similar dialog for a photo (Excire-style).

        Looks up the photo_id from the path and opens SimilarPhotosDialog.
        """
        if not path:
            return
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?",
                    (path, self.project_id)
                ).fetchone()
                if not row:
                    row = conn.execute(
                        "SELECT id FROM photo_metadata WHERE LOWER(path) = LOWER(?) AND project_id = ?",
                        (path, self.project_id)
                    ).fetchone()
                if not row:
                    print(f"[FindSimilar] Photo not found in DB: {path}")
                    return
                photo_id = row['id']

            from ui.similar_photos_dialog import SimilarPhotosDialog
            dialog = SimilarPhotosDialog(
                reference_photo_id=photo_id,
                project_id=self.project_id,
                parent=self,
            )
            dialog.exec()

        except Exception as e:
            print(f"[FindSimilar] Error: {e}")
            import traceback
            traceback.print_exc()

    def _show_metadata_editor_for_photo(self, path: str):
        """Show the metadata editor dock for a specific photo (triggered from right-click menu)."""
        print(f"[ThumbnailGrid] Opening metadata editor for: {path}")
        if not path:
            print("[ThumbnailGrid] ⚠️ Cannot open metadata editor: path is empty")
            return
        try:
            main_window = self.window()
            if not main_window:
                print("[ThumbnailGrid] ⚠️ Cannot open metadata editor: window() returned None")
                return
            dock = getattr(main_window, 'metadata_editor_dock', None)
            if not dock:
                print("[ThumbnailGrid] ⚠️ Cannot open metadata editor: metadata_editor_dock not found on main window")
                return

            # Look up photo_id from database
            from reference_db import ReferenceDB
            db = ReferenceDB()
            with db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?",
                    (path, self.project_id))
                row = cursor.fetchone()
                photo_id = row['id'] if row else None

            if not photo_id:
                # Try case-insensitive match as fallback
                with db.get_connection() as conn:
                    cursor = conn.execute(
                        "SELECT id FROM photo_metadata WHERE LOWER(path) = LOWER(?) AND project_id = ?",
                        (path, self.project_id))
                    row = cursor.fetchone()
                    photo_id = row['id'] if row else None
                if photo_id:
                    print(f"[ThumbnailGrid] Found photo_id via case-insensitive match")

            if not photo_id:
                print(f"[ThumbnailGrid] ⚠️ Cannot open metadata editor: no photo_id found for path: {path}")
                return

            print(f"[ThumbnailGrid] ✓ Opening metadata editor for photo_id={photo_id}")
            main_window.show_metadata_for_photo(photo_id, path)
        except Exception as e:
            print(f"[ThumbnailGrid] Error opening metadata editor: {e}")
            import traceback
            traceback.print_exc()

    def _edit_photo_location(self, path: str):
        """
        Edit GPS location for a single photo (manual location editing).

        Args:
            path: Path to photo file
        """
        import os
        from PySide6.QtWidgets import QMessageBox

        print(f"[Location] 📍 Opening location editor for: {path}")

        try:
            from ui.location_editor_integration import edit_photo_location

            # Show location editor dialog
            location_changed = edit_photo_location(path, parent=self)

            # If location was changed, refresh the Locations section
            if location_changed:
                print(f"[Location] ✓ Location updated for {os.path.basename(path)}")

                # Reload Locations section in sidebar
                try:
                    mw = self.window()
                    if hasattr(mw, "sidebar"):
                        # Check if it's an accordion sidebar with reload_section method
                        if hasattr(mw.sidebar, "reload_section"):
                            print("[Location] Reloading Locations section...")
                            mw.sidebar.reload_section("locations")
                        elif hasattr(mw.sidebar, "reload"):
                            print("[Location] Reloading sidebar...")
                            mw.sidebar.reload()
                except Exception as e:
                    print(f"[Location] Warning: Failed to reload sidebar: {e}")

        except ImportError as e:
            QMessageBox.critical(
                self,
                "Import Error",
                f"Failed to load location editor:\n{e}\n\nPlease ensure ui/location_editor_integration.py exists."
            )
        except Exception as e:
            print(f"[Location] Error opening location editor: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open location editor:\n{e}"
            )

    def _edit_photos_location_batch(self, paths: list[str]):
        """
        Edit GPS location for multiple photos (batch editing).

        This applies the same location to all selected photos at once,
        following the Google Photos pattern.

        Args:
            paths: List of photo file paths
        """
        from PySide6.QtWidgets import QMessageBox

        print(f"[Location] 📍 Opening batch location editor for {len(paths)} photos")

        try:
            from ui.location_editor_integration import edit_photos_location_batch

            # Show batch location editor dialog
            location_changed = edit_photos_location_batch(paths, parent=self)

            # If location was changed, refresh the Locations section
            if location_changed:
                print(f"[Location] ✓ Location updated for {len(paths)} photos")

                # Reload Locations section in sidebar
                try:
                    mw = self.window()
                    if hasattr(mw, "sidebar"):
                        if hasattr(mw.sidebar, "reload_section"):
                            print("[Location] Reloading Locations section...")
                            mw.sidebar.reload_section("locations")
                        elif hasattr(mw.sidebar, "reload"):
                            print("[Location] Reloading sidebar...")
                            mw.sidebar.reload()
                except Exception as e:
                    print(f"[Location] Warning: Failed to reload sidebar: {e}")

        except ImportError as e:
            QMessageBox.critical(
                self,
                "Import Error",
                f"Failed to load location editor:\n{e}\n\nPlease ensure ui/location_editor_integration.py exists."
            )
        except Exception as e:
            print(f"[Location] Error opening batch location editor: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
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
        import os
        from PySide6.QtWidgets import QMessageBox
        from reference_db import ReferenceDB

        if not path:
            QMessageBox.warning(
                self,
                "No Photo Selected",
                "Please select a photo to copy location from."
            )
            return

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
                    self,
                    "No GPS Data",
                    f"Photo '{os.path.basename(path)}' has no GPS location data.\n\n"
                    "Use 'Edit Location...' to add GPS coordinates first."
                )
                return

            lat, lon, location_name = row

            if lat is None or lon is None:
                QMessageBox.warning(
                    self,
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
                self,
                "Location Copied",
                f"✓ Copied GPS location:\n\n{location_display}\n\n"
                f"Use 'Paste Location' to apply this location to other photos."
            )

            print(f"[Location] ✓ Copied GPS location: {location_display} ({lat:.6f}, {lon:.6f})")

        except Exception as e:
            print(f"[Location] Error copying location: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
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
        import os
        from PySide6.QtWidgets import QMessageBox

        if not self.copied_gps_location:
            QMessageBox.warning(
                self,
                "No Location Copied",
                "No GPS location has been copied.\n\n"
                "Use 'Copy Location' on a photo with GPS data first."
            )
            return

        if not paths:
            QMessageBox.warning(
                self,
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
                self,
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
                    print(f"[Location] ✓ Pasted location to: {os.path.basename(photo_path)}")

                except Exception as e:
                    fail_count += 1
                    print(f"[Location] ✗ Failed to paste location to {os.path.basename(photo_path)}: {e}")

            # Show results
            if fail_count == 0:
                QMessageBox.information(
                    self,
                    "Success",
                    f"✓ GPS location pasted to {success_count} {photo_word}!\n\n"
                    f"Location: {location_name}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Partially Complete",
                    f"GPS location pasted to {success_count} photo(s).\n"
                    f"Failed: {fail_count} photo(s).\n\n"
                    f"Check logs for details."
                )

            # Refresh Locations section in sidebar
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_section"):
                        print("[Location] Reloading Locations section...")
                        mw.sidebar.reload_section("locations")
                    elif hasattr(mw.sidebar, "reload"):
                        print("[Location] Reloading sidebar...")
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Location] Warning: Failed to reload sidebar: {e}")

            print(f"[Location] ✓ Paste complete: {success_count} success, {fail_count} failures")

        except Exception as e:
            print(f"[Location] Error pasting location: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to paste location:\n{e}"
            )

    def _refresh_tags_for_paths(self, paths: list[str]):
        """
        Refresh tag overlay (Qt.UserRole+2) for given paths only.
        Avoids full grid reload and keeps UI snappy.

        ARCHITECTURE: UI Layer → TagService → TagRepository → Database

        P2-17 FIX: Optimized to avoid iterating through all rows on large datasets.
        Instead of O(N*M) complexity, uses O(M) path lookup with batch updates.
        """
        if not paths:
            return
        try:
            # Use TagService for proper layered architecture
            tag_service = get_tag_service()
            tags_map = tag_service.get_tags_for_paths(paths, self.project_id)
            print(f"[TagCache] Refreshing tags for {len(paths)} paths, got {len(tags_map)} paths with tags")
        except Exception as e:
            print(f"[TagCache] ❌ Failed to fetch tags: {e}")
            return

        # P2-17 FIX: Build path-to-row mapping for O(1) lookups
        # Only map rows for paths that need updating (not all 10K+ rows)
        path_to_rows = {}
        paths_set = set(paths)  # O(1) membership check

        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if not item:
                continue
            p = item.data(Qt.UserRole)
            if p in paths_set:  # Only process relevant paths
                path_to_rows[p] = (row, item)
                if len(path_to_rows) >= len(paths):
                    break  # Early exit once all paths found

        # P2-17 FIX: Batch update with signals blocked
        updated_count = 0
        if path_to_rows:
            # Block model signals during batch update
            self.model.blockSignals(True)
            try:
                for path, new_tags in tags_map.items():
                    if path in path_to_rows:
                        row, item = path_to_rows[path]
                        item.setData(new_tags, Qt.UserRole + 2)
                        item.setToolTip(", ".join([t for t in (new_tags or []) if t]))
                        updated_count += 1
            finally:
                self.model.blockSignals(False)

        if updated_count > 0:
            print(f"[TagCache] ✅ P2-17: Updated {updated_count}/{len(paths)} items (optimized)")

        # P2-17 FIX: Single viewport repaint after all updates
        self.list_view.viewport().update()
        
        # 🏷️ ENHANCEMENT: Emit signal to update details panel in real-time
        self.tagsChanged.emit()

    # ==========================================================
    # 📸 Zoom Handling with Fixed Height & Aspect Ratio
    # ==========================================================
    def _init_zoom(self):
        """Initialize zoom state and event handling."""
        self._thumb_base = 120
        self._zoom_factor = 1.0
        self._min_zoom = 0.5
        self._max_zoom = 3.0

        from settings_manager_qt import SettingsManager
        self.settings = SettingsManager()
        # spacing follows zoom factor
        self._spacing = self._compute_step_spacing()

        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setSpacing(self._spacing)
        self.list_view.setUniformItemSizes(True)  # allow dynamic width
        self.list_view.setMovement(QListView.Static)
        self.list_view.setWrapping(True)

        if hasattr(self, "zoom_slider"):
            self.zoom_slider.setMinimum(0)
            self.zoom_slider.setMaximum(100)
            self.zoom_slider.setValue(50)
            self.zoom_slider.valueChanged.connect(self._on_slider_changed)

        self.list_view.viewport().installEventFilter(self)

    def _thumb_size_for_aspect(self, aspect_ratio: float) -> QSize:
        """
        Compute size for a given aspect ratio based on current zoom factor.
        Height is fixed, width varies.
        """
        thumb_h = int(self._thumb_base * self._zoom_factor)
        if aspect_ratio <= 0:
            aspect_ratio = 1.5  # fallback default
        thumb_w = int(thumb_h * aspect_ratio)
        return QSize(thumb_w, thumb_h)

    def _compute_step_spacing(self) -> int:
        """Discrete spacing: min 1, +1 per 10 slider units (0-100)."""
        try:
            val = self.zoom_slider.value()
        except Exception:
            val = 50
        return max(1, 1 + (int(val) // 10))
    def _set_zoom_factor(self, factor: float):
        """Clamp and apply zoom factor, update all items."""
        factor = max(self._min_zoom, min(self._max_zoom, factor))
        self._zoom_factor = factor
        self._spacing = self._compute_step_spacing()
        self._apply_zoom_geometry()

    def _apply_zoom_geometry(self):
        """
        Recalculate grid sizes for all items based on current zoom and
        their stored aspect ratios.
        """
        for i in range(self.model.rowCount()):
            idx = self.model.index(i, 0)
            aspect_ratio = idx.data(Qt.UserRole + 1) or 1.5
            size = self._thumb_size_for_aspect(aspect_ratio)
            self.model.setData(idx, size, Qt.SizeHintRole)

        self.list_view.setSpacing(self._spacing)
        self.list_view.updateGeometry()
        self.list_view.repaint()
        
        # 🔧 Warm-up: start with uniform item sizes to avoid initial overlap
        if getattr(self, '_warmup_layout', True):
            QTimer.singleShot(150, lambda: self.list_view.setUniformItemSizes(False))
            QTimer.singleShot(160, lambda: self.list_view.doItemsLayout())
            QTimer.singleShot(170, lambda: setattr(self, '_warmup_layout', False))


    def _animate_zoom_to(self, target_factor: float, duration: int = 200):
        """Smoothly animate zoom factor between current and target value."""
        target_factor = max(self._min_zoom, min(self._max_zoom, target_factor))

        # Kill existing animation if still running
        if hasattr(self, "_zoom_anim") and self._zoom_anim is not None:
            self._zoom_anim.stop()

        # PropertyAnimation on a dynamic property
        self.setProperty("_zoom_factor_prop", self._zoom_factor)
        self._zoom_anim = QPropertyAnimation(self, b"_zoom_factor_prop", self)
        self._zoom_anim.setDuration(duration)
        self._zoom_anim.setStartValue(self._zoom_factor)
        self._zoom_anim.setEndValue(target_factor)
        self._zoom_anim.setEasingCurve(QEasingCurve.InOutQuad)

#        self._zoom_anim.valueChanged.connect(lambda val: self._set_zoom_factor(float(val)))
#        self._zoom_anim.start()

        def _on_zoom_anim_val(val):
            self._set_zoom_factor(float(val))
            # sync slider position (inverse mapping)
            norm = (float(val) - self._min_zoom) / (self._max_zoom - self._min_zoom)
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(int(norm * 100))
            self.zoom_slider.blockSignals(False)

        self._zoom_anim.valueChanged.connect(_on_zoom_anim_val)
        # Phase 2.3: Emit gridReloaded when zoom animation finishes (for status bar update)
        self._zoom_anim.finished.connect(lambda: self.gridReloaded.emit())
        self._zoom_anim.start()


    def zoom_in(self):
        self._animate_zoom_to(self._zoom_factor * 1.1)

    def zoom_out(self):
        self._animate_zoom_to(self._zoom_factor / 1.1)

    def _on_slider_changed(self, value: int):
        """Animate slider-driven zoom as well."""
        norm = value / 100.0
        new_factor = self._min_zoom + (self._max_zoom - self._min_zoom) * norm
        self._animate_zoom_to(new_factor)

    def _on_slider_pressed(self):
        # Stop any running animation while dragging
        if hasattr(self, "_zoom_anim") and self._zoom_anim is not None:
            self._zoom_anim.stop()
        self._is_slider_dragging = True

    def _on_slider_value_changed(self, value: int):
        # Live preview during drag — immediate resize without animation
        if getattr(self, "_is_slider_dragging", False):
            norm = value / 100.0
            new_factor = self._min_zoom + (self._max_zoom - self._min_zoom) * norm
            self._set_zoom_factor(new_factor)

    def _on_slider_released(self):
        self._is_slider_dragging = False
        value = self.zoom_slider.value()
        norm = value / 100.0
        new_factor = self._min_zoom + (self._max_zoom - self._min_zoom) * norm
        # ✨ smooth animate to final position for polish
        self._animate_zoom_to(new_factor)


    def _on_double_clicked(self, index):
        path = index.data(Qt.UserRole)
        print(f"[ThumbnailGridQt_on_double_clicked] index: {index.data}")
        if path:
            self.openRequested.emit(path)

    def eventFilter(self, obj, event):
        """
        Phase 2.1: Unified event filter for keyboard shortcuts and mouse events.

        Handles:
        - Ctrl+Wheel: Zoom in/out
        - Arrow keys: Navigate grid
        - Ctrl+A: Select all
        - Escape: Clear selection
        - Space/Enter: Open lightbox
        - Delete: Delete selected
        """
        # Ctrl+Wheel zoom (merged from previous eventFilter)
        if obj is self.list_view.viewport() and event.type() == QEvent.MouseMove:
            # P1-6 FIX: Update only affected cells, not entire viewport
            idx = self.list_view.indexAt(event.pos())
            new_row = idx.row() if idx.isValid() else -1
            old_row = getattr(self.delegate, '_current_hover_row', -1)

            if new_row != old_row:
                self.delegate.set_hover_row(new_row)
                # P1-6 FIX: Update only the old and new hovered cells
                if old_row >= 0 and old_row < self.model.rowCount():
                    old_idx = self.model.index(old_row, 0)
                    old_rect = self.list_view.visualRect(old_idx)
                    self.list_view.viewport().update(old_rect)
                if new_row >= 0:
                    new_rect = self.list_view.visualRect(idx)
                    self.list_view.viewport().update(new_rect)
            return False
        if obj is self.list_view.viewport() and event.type() == QEvent.Leave:
            # P1-6 FIX: Update only the previously hovered cell
            old_row = getattr(self.delegate, '_current_hover_row', -1)
            self.delegate.set_hover_row(-1)
            if old_row >= 0 and old_row < self.model.rowCount():
                old_idx = self.model.index(old_row, 0)
                old_rect = self.list_view.visualRect(old_idx)
                self.list_view.viewport().update(old_rect)
            return False
        if obj is self.list_view.viewport() and event.type() == QEvent.MouseButtonPress:
            pos = event.pos()
            idx = self.list_view.indexAt(pos)
            if idx.isValid():
                rect = self.list_view.visualRect(idx)
                s = getattr(self.delegate, 'icon_size', 22)
                m = getattr(self.delegate, 'icon_margin', 6)
                # Rects
                cb_rect = QRect(rect.left() + m, rect.top() + m, s, s)
                del_rect = QRect(rect.right() - m - s, rect.top() + m, s, s)
                info_rect = QRect(rect.right() - m - 2*s - 4, rect.top() + m, s, s)
                fav_rect = QRect(rect.right() - m - 3*s - 8, rect.top() + m, s, s)
                p = idx.data(Qt.UserRole)
                if cb_rect.contains(pos):
                    sm = self.list_view.selectionModel()
                    if sm.isSelected(idx):
                        sm.select(idx, QItemSelectionModel.Deselect)
                    else:
                        sm.select(idx, QItemSelectionModel.Select)
                    return True
                if fav_rect.contains(pos) and p:
                    try:
                        tag_service = get_tag_service()
                        tags = idx.data(Qt.UserRole + 2) or []
                        if 'favorite' in tags:
                            tag_service.remove_tag(p, 'favorite', self.project_id)
                            # Update item tags immediately
                            new_tags = [t for t in tags if t != 'favorite']
                        else:
                            tag_service.assign_tags_bulk([p], 'favorite', self.project_id)
                            # Update item tags immediately
                            new_tags = list(tags) + ['favorite']
                        
                        # Update the model item with new tags
                        item = self.model.item(idx.row())
                        if item:
                            item.setData(new_tags, Qt.UserRole + 2)
                        item.setToolTip(", ".join([t for t in (new_tags or []) if t]))
                        
                        # Refresh sidebar tags without full grid reload
                        try:
                            mw = self.window()
                            if hasattr(mw, "sidebar"):
                                if hasattr(mw.sidebar, "reload_tags_only"):
                                    mw.sidebar.reload_tags_only()
                                else:
                                    mw.sidebar.reload()
                        except Exception:
                            pass
                        
                        # Repaint only this cell - preserves scroll/zoom
                        self.list_view.viewport().update(rect)
                        print(f"[HoverActions] Favorite {'removed' if 'favorite' in tags else 'added'} for {p}")
                    except Exception as e:
                        print(f"[HoverActions] Favorite toggle failed: {e}")
                    return True
                if info_rect.contains(pos) and p:
                    self.openRequested.emit(p)
                    return True
                if del_rect.contains(pos) and p:
                    self.deleteRequested.emit([p])
                    return True
            # fall-through
        if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            return True

        # Keyboard shortcuts
        if obj is self.list_view and event.type() == QEvent.KeyPress:
            key = event.key()
            mods = event.modifiers()

            # Ctrl+A -> select all
            if key == Qt.Key_A and (mods & Qt.ControlModifier):
                self.list_view.selectAll()
                return True

            # Esc -> clear selection
            if key == Qt.Key_Escape:
                self.list_view.clearSelection()
                return True

            # Delete -> request deletion of selected paths
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                paths = self.get_selected_paths()
                if paths:
                    self.deleteRequested.emit(paths)
                return True

            # Space or Enter -> open lightbox for current/selected item
            if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                current = self.list_view.currentIndex()
                if current.isValid():
                    path = current.data(Qt.UserRole)
                    if path:
                        self.openRequested.emit(path)
                return True

            # Arrow key navigation (Up/Down/Left/Right)
            if key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
                return self._handle_arrow_navigation(key, mods)

        return super().eventFilter(obj, event)

    def _handle_arrow_navigation(self, key, mods):
        """
        Phase 2.1: Handle arrow key navigation in grid view.

        Moves selection up/down/left/right based on grid layout.
        Supports Shift for range selection.
        """
        current_index = self.list_view.currentIndex()
        if not current_index.isValid():
            # No current selection - select first item
            if self.model.rowCount() > 0:
                first_index = self.model.index(0, 0)
                self.list_view.setCurrentIndex(first_index)
                if not (mods & Qt.ShiftModifier):
                    self.list_view.selectionModel().select(first_index, QItemSelectionModel.ClearAndSelect)
            return True

        current_row = current_index.row()
        total_rows = self.model.rowCount()

        # Calculate items per row based on current grid layout
        viewport_width = self.list_view.viewport().width()
        item_width = self.delegate.sizeHint(QStyleOptionViewItem(), current_index).width()
        spacing = self.list_view.spacing()
        items_per_row = max(1, viewport_width // (item_width + spacing))

        # Determine target row based on key
        target_row = current_row
        if key == Qt.Key_Left:
            target_row = max(0, current_row - 1)
        elif key == Qt.Key_Right:
            target_row = min(total_rows - 1, current_row + 1)
        elif key == Qt.Key_Up:
            target_row = max(0, current_row - items_per_row)
        elif key == Qt.Key_Down:
            target_row = min(total_rows - 1, current_row + items_per_row)

        # Apply navigation
        if target_row != current_row:
            target_index = self.model.index(target_row, 0)
            self.list_view.setCurrentIndex(target_index)

            # Handle selection based on modifiers
            if mods & Qt.ShiftModifier:
                # Shift: Extend selection (range select)
                self.list_view.selectionModel().select(target_index, QItemSelectionModel.Select)
            else:
                # No modifier: Move selection (clear previous)
                self.list_view.selectionModel().select(target_index, QItemSelectionModel.ClearAndSelect)

            # Ensure target item is visible
            self.list_view.scrollTo(target_index, QAbstractItemView.EnsureVisible)

        return True

    def get_selected_paths(self):
        selection = self.list_view.selectionModel().selectedIndexes()
        return [i.data(Qt.UserRole) for i in selection if i.isValid()]


    def set_project(self, project_id: int):
        self.project_id = project_id
        self._project_has_tags = None  # invalidate tag cache on project switch
        self._reload_disabled = False  # reset circuit breaker on project switch
        self._reload_fail_count = 0
        self.clear()


# ============================================================
    # 🧭 Navigation handlers
    # ============================================================
    def set_folder(self, folder_id: int):
        """Called when a folder node is clicked."""
        self.navigation_mode = "folder"
        self.navigation_key = folder_id
        self.active_tag_filter = None

        self.load_mode = "folder"
        self.current_folder_id = folder_id
        self._schedule_reload()
        
        self._apply_zoom_geometry()
        

    def set_branch(self, branch_key: str):
        """Called when a branch node is clicked."""
        print(f"\n[GRID] >>>>>> set_branch('{branch_key}') CALLED")
        print(f"[GRID]   Current state: project_id={self.project_id}, load_mode={self.load_mode}")

        try:
            self.navigation_mode = "branch"
            self.navigation_key = branch_key
            self.active_tag_filter = None

            self.load_mode = "branch"
            self.branch_key = branch_key
            print(f"[GRID]   State updated, scheduling debounced reload...")
            self._schedule_reload()
            print(f"[GRID] <<<<<< set_branch('{branch_key}') COMPLETED\n")
        except Exception as e:
            print(f"[GRID] !!!!! set_branch('{branch_key}') CRASHED: {e}")
            import traceback
            traceback.print_exc()
            print(f"[GRID] <<<<<< set_branch('{branch_key}') FAILED\n")
            raise

    def set_date(self, date_key: str):
        """Called when a date node (YYYY / YYYY-MM / YYYY-MM-DD) is clicked."""
        self.navigation_mode = "date"
        self.navigation_key = date_key
        self.active_tag_filter = None

        self.load_mode = "date"
        self.date_key = date_key
        self._schedule_reload()

    def set_videos(self):
        """
        Called when Videos tab is selected - show all videos for current project.
        """
        self.navigation_mode = "videos"
        self.navigation_key = None
        self.active_tag_filter = None

        self.load_mode = "videos"
        self._schedule_reload()

    def set_group(self, group_id: int, paths: list[str] = None):
        """
        Display photos for a person group (Together/AND matching).

        When a user clicks a group in the Groups sub-section, this method
        is called to show only photos where ALL group members appear together.

        Args:
            group_id: ID of the selected group
            paths: Optional pre-computed list of photo paths. If not provided,
                   paths will be fetched from GroupService.
        """
        print(f"\n[GRID] >>>>>> set_group({group_id}) CALLED")

        self.navigation_mode = "group"
        self.navigation_key = group_id
        self.active_tag_filter = None
        self.load_mode = "group"

        # Store group context for potential re-filtering
        self._current_group_id = group_id

        # Get paths from GroupService if not provided
        if paths is None:
            try:
                from services.group_service import GroupService
                from reference_db import ReferenceDB
                db = ReferenceDB()
                paths = GroupService.get_cached_match_paths(db, self.project_id, group_id)
                db.close()
                print(f"[GRID] Fetched {len(paths)} photos for group {group_id}")
            except Exception as e:
                print(f"[GRID] Error fetching group photos: {e}")
                paths = []

        if not paths:
            print(f"[GRID] No photos found for group {group_id}")
            self.clear()
            return

        # Load the paths using _load_paths (reuses existing batching logic)
        self._load_paths(paths)

        print(f"[GRID] <<<<<< set_group({group_id}) COMPLETED - {len(paths)} photos\n")

    def set_custom_paths(self, paths: list[str], context_label: str = "Custom"):
        """
        Display a custom list of photo paths with optional context label.

        This is a convenience method for group-based filtering and other
        custom photo collections that don't fit standard navigation modes.

        Args:
            paths: List of photo file paths to display
            context_label: Label for status bar display (e.g., "Group: Family")
        """
        print(f"\n[GRID] >>>>>> set_custom_paths() CALLED - {len(paths)} paths")

        self.navigation_mode = "custom"
        self.navigation_key = context_label
        self.active_tag_filter = None
        self.load_mode = "custom"

        if not paths:
            print(f"[GRID] No paths provided")
            self.clear()
            return

        # Load using existing batched path loading
        self._load_paths(paths)

        print(f"[GRID] <<<<<< set_custom_paths() COMPLETED\n")

    def load_paths(self, paths: list[str]):
        """
        Load arbitrary list of photo paths (e.g., from search results).

        This is used for search results or custom photo collections that
        don't fit into the folder/branch/date navigation paradigm.

        Args:
            paths: List of photo file paths to display
        """
        self.navigation_mode = "custom"
        self.navigation_key = None
        self.active_tag_filter = None
        self.load_mode = "custom"

        # Store paths and reload
        self._paths = list(paths)
        print(f"[GRID] Loading {len(self._paths)} custom paths (e.g., search results)")

        # Clear and reload grid
        self.model.clear()
        self._reload_token = uuid.uuid4()  # Generate new UUID token
        self._current_reload_token = self._reload_token
        token = self._reload_token

        # Get tags for all paths
        tag_map = {}
        try:
            if hasattr(self.db, 'get_tags_for_paths'):
                tag_map = self.db.get_tags_for_paths(self._paths, self.project_id)
        except Exception as e:
            print(f"[GRID] Warning: Could not fetch tags: {e}")

        # Load thumbnails
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setData(p, Qt.UserRole)  # normalized path
            item.setData(p, Qt.UserRole + 6)  # real path
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)  # tags

            # Set placeholder size
            aspect_ratio = 1.5
            item.setData(aspect_ratio, Qt.UserRole + 1)
            item.setSizeHint(self._thumb_size_for_aspect(aspect_ratio))

            self.model.appendRow(item)

        # Trigger thumbnail loading
        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()
        
        # 🔧 FIX: Force complete geometry update
        def _force_geometry_update():
            self.list_view.setSpacing(self._spacing)
            self.list_view.updateGeometry()
            self.list_view.doItemsLayout()
            self.list_view.repaint()
        
        QTimer.singleShot(100, _force_geometry_update)
        QTimer.singleShot(200, _force_geometry_update)
        
        self.list_view.viewport().update()

        # Request visible thumbnails
        if hasattr(self, 'request_visible_thumbnails'):
            QTimer.singleShot(100, self.request_visible_thumbnails)

        print(f"[GRID] Loaded {len(self._paths)} thumbnails in custom mode")

        # Phase 2.3: Emit signal for status bar update
        self.gridReloaded.emit()

        # PHASE 4: Restore scroll position after photos are loaded (defer 200ms for layout)
        QTimer.singleShot(200, self.restore_scroll_position)

    def reload_priortoContext_driven(self):
        """
        Load image paths based on current load_mode and refresh thumbnail grid.
        Prevents duplicate reloads for the same context.
        """
#        # --- Prevent duplicate reloads ---
        
        self.model.clear()
        self._paths.clear()

        # ✅ Handle tag overlay mode explicitly
        if getattr(self, "load_mode", None) == "tag":
            if getattr(self, "active_tag_filter", None):
                print(f"[GRID] Reload requested under tag filter '{self.active_tag_filter}' – skipping DB context reload.")
                # keep showing current filtered paths
                self._apply_zoom_layout()
                self.list_view.doItemsLayout()
                self.list_view.viewport().update()
                return
            else:
                # tag cleared → fall back to previous context
                print("[GRID] Tag filter cleared – restoring previous context.")
                self.load_mode = getattr(self, "last_nav_mode", "branch")

        # --- Load from DB
           
        if self.load_mode == "branch":
            if not self.branch_key:
                return
            # Support virtual date branches ("date:")
            if self.branch_key.startswith("date:"):
                paths = self.db.get_images_for_quick_key(self.branch_key)
            else:
                if not self.project_id:
                    return
                paths = self.db.get_images_by_branch(self.project_id, self.branch_key)
                                    
        elif self.load_mode == "folder":
            if not self.current_folder_id:
                return
            paths = self.db.get_images_by_folder(self.current_folder_id, project_id=self.project_id)

        elif self.load_mode == "date":
            if not self.date_key:
                return
            dk = self.date_key  # already normalized to YYYY / YYYY-MM / YYYY-MM-DD
            if len(dk) == 4 and dk.isdigit():
                paths = self.db.get_images_by_year(int(dk), self.project_id)

            elif len(dk) == 7 and dk[4] == "-" and dk[5:7].isdigit():
                year, month = dk.split("-", 1)
                paths = self.db.get_images_by_month(year, month, self.project_id)
                # fallback: if no results, maybe dates have timestamps—try prefix search
                if not paths:
                    paths = self.db.get_images_for_quick_key(f"date:{dk}")

            elif len(dk) == 10 and dk[4] == "-" and dk[7] == "-":
                paths = self.db.get_images_by_date(dk, self.project_id)
            else:
                # fallback for quick keys (rare)
                paths = self.db.get_images_for_quick_key(f"date:{dk}")
                        
        else:
            return

        # Normalize to list[str]
        self._paths = [
            r[0] if isinstance(r, (tuple, list)) else
            r.get("path") if isinstance(r, dict) and "path" in r else
            str(r)
            for r in paths
        ]

        tag_map = self.db.get_tags_for_paths(self._paths, self.project_id)

        # --- Build items
        token = self._reload_token
        thumb_h = int(self._thumb_base * self._zoom_factor) if hasattr(self, '_thumb_base') else self.thumb_height
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)  # store tags list

            # initial placeholder size (consistent with default aspect)
            item.setSizeHint(self._thumb_size_for_aspect(1.5))
            self.model.appendRow(item)

            # FIX: ThumbWorker requires 8 args: real_path, norm_path, height, row, signal_obj, cache, reload_token, placeholder
            worker = ThumbWorker(p, p, thumb_h, i, self.thumb_signal, self._thumb_cache, token, self._placeholder_pixmap)
            self.thread_pool.start(worker)

        # --- Trigger UI update
        self._apply_zoom_layout()
        self.list_view.doItemsLayout()
        self.list_view.viewport().update()
        print(f"[GRID] Reloaded {len(self._paths)} thumbnails in {self.load_mode}-mode.")
 
    # ============================================================
    # 🔄 Debounced reload helpers
    # ============================================================
    def _schedule_reload(self):
        """Schedule a debounced reload.  Rapid calls within the 150 ms window
        are coalesced so only the *last* navigation request fires a reload."""
        self._pending_reload = True
        self._reload_debounce_timer.start()  # (re)starts the 150 ms timer

    def _debounced_reload(self):
        """Executed when the debounce timer fires."""
        if self._pending_reload:
            self._pending_reload = False
            self.reload()

    # ============================================================
    # 🌍 Context-driven navigation & reload (Enhanced with user feedback)
    # ============================================================
    def set_context(self, mode: str, key: str | int | None):
        """
        Sets navigation context (folder, branch, date, people, videos) and triggers reload.
        Clears any active tag overlay.
        Also syncs the legacy navigation_mode / navigation_key fields so that
        breadcrumb and other code that reads those stays in sync.
        """
        self.context = getattr(self, "context", {
            "mode": None, "key": None, "tag_filter": None
        })
        self.context["mode"] = mode
        self.context["key"] = key
        self.context["tag_filter"] = None

        # Sync legacy fields used by breadcrumb and status bar
        self.navigation_mode = mode
        self.navigation_key = key

        self._schedule_reload()

    # ============================================================
    def apply_tag_filter(self, tag: str | None):
        """
        Overlay a tag filter on top of the current navigation context.
        Passing None or 'all' clears the filter.
        """
        if not hasattr(self, "context"):
            self.context = {"mode": None, "key": None, "tag_filter": None}
        self.context["tag_filter"] = tag if tag not in (None, "", "all") else None
        self._schedule_reload()

    # ============================================================
    def reload(self):
        """
        Centralized reload logic combining navigation context + optional tag overlay.
        Includes user feedback via status bar and detailed console logs.
        """
        print(f"\n[GRID] ====== reload() CALLED ======")
        print(f"[GRID] project_id={self.project_id}, load_mode={self.load_mode}")
        
        # Circuit breaker: stop exception loops from cascading reloads
        if getattr(self, '_reload_disabled', False):
            print("[GRID] reload() blocked — circuit breaker active (previous exceptions)")
            return

        # CRITICAL: Prevent concurrent reloads that cause crashes
        # Similar to sidebar._refreshing flag pattern
        if getattr(self, '_reloading', False):
            print("[GRID] reload() blocked - already reloading (prevents concurrent reload crash)")
            print(f"[GRID] ====== reload() BLOCKED ======\n")
            return
        
        # CRITICAL FIX: Validate project_id before database operations
        if self.project_id is None:
            print("[GRID] ⚠️ Warning: project_id is None, skipping reload to prevent crash")
            print("[GRID] This usually means the project hasn't been initialized yet")
            print(f"[GRID] ====== reload() ABORTED (no project_id) ======\n")
            return

        try:
            self._reloading = True
            print(f"[GRID] Step 1: Setting _reloading=True")

            import os
            from PySide6.QtCore import QSize
            from PySide6.QtGui import QStandardItem

            db = self.db
            ctx = getattr(self, "context", {"mode": None, "key": None, "tag_filter": None})
            mode, key, tag = ctx["mode"], ctx["key"], ctx["tag_filter"]

            # CRITICAL FIX: Update load_mode to match context mode
            # This ensures grid state stays synchronized when switching between photo/video navigation
            if mode in ("folder", "branch", "date", "videos", "tag", "people"):
                self.load_mode = mode
            elif mode is None and tag:
                # Tag filter without specific navigation context
                self.load_mode = "tag"

            # --- 1️+2️: Determine base photo paths by navigation mode AND tag filter ---
            # CRITICAL FIX: Use efficient database queries instead of in-memory intersection
            # OLD (SLOW): Load all 2856 photos → filter in memory → UI freeze
            # NEW (FAST): SQL JOIN returns only matching photos → instant response

            if tag:
                # Tag filter is active - use efficient JOIN queries
                if mode == "folder" and key:
                    paths = db.get_images_by_folder_and_tag(self.project_id, key, tag, include_subfolders=True)
                    print(f"[TAG FILTER] Folder {key} + tag '{tag}' → {len(paths)} photos (efficient query)")
                elif mode == "branch" and key:
                    paths = db.get_images_by_branch_and_tag(self.project_id, key, tag)
                    print(f"[TAG FILTER] Branch {key} + tag '{tag}' → {len(paths)} photos (efficient query)")
                elif mode == "people" and key:
                    # 👥 Face cluster + tag filter
                    paths = db.get_images_by_branch_and_tag(self.project_id, key, tag)
                    print(f"[TAG FILTER] People {key} + tag '{tag}' → {len(paths)} photos (efficient query)")
                elif mode == "date" and key:
                    dk = str(key)
                    paths = db.get_images_by_date_and_tag(self.project_id, dk, tag)
                    print(f"[TAG FILTER] Date {dk} + tag '{tag}' → {len(paths)} photos (efficient query)")
                else:
                    # No navigation context - show all tagged photos
                    paths = db.get_image_paths_for_tag(tag, self.project_id)
                    self.context["mode"] = "tag"
                    print(f"[TAG FILTER] Showing all tagged photos for '{tag}' ({len(paths)})")

            else:
                # No tag filter - use normal navigation queries
                if mode == "folder" and key:
                    paths = db.get_images_by_folder(key, project_id=self.project_id)
                elif mode == "branch" and key:
                    paths = db.get_images_by_branch(self.project_id, key)
                elif mode == "people" and key:
                    # 👥 Face cluster navigation - load photos containing faces from this cluster
                    paths = db.get_images_by_branch(self.project_id, key)
                    print(f"[GRID] Loaded {len(paths)} photos for face cluster {key}")
                elif mode == "date" and key:
                    dk = str(key)
                    if len(dk) == 4 and dk.isdigit():
                        paths = db.get_images_by_year(int(dk), self.project_id)
                    elif len(dk) == 7 and dk[4] == "-" and dk[5:7].isdigit():
                        paths = db.get_images_by_month_str(dk, self.project_id)
                    elif len(dk) == 10 and dk[4] == "-" and dk[7] == "-":
                        paths = db.get_images_by_date(dk, self.project_id)
                    else:
                        # fallback for quick keys (e.g. date:this-week)
                        paths = db.get_images_for_quick_key(f"date:{dk}", self.project_id)
                elif mode == "videos":
                    # 🎬 Phase 4.3: Load all videos for project
                    try:
                        from services.video_service import VideoService
                        video_service = VideoService()
                        videos = video_service.get_videos_by_project(self.project_id)
                        paths = [v['path'] for v in videos]
                        print(f"[GRID] Loaded {len(paths)} videos for project {self.project_id}")
                    except Exception as e:
                        print(f"[GRID] Failed to load videos: {e}")
                        paths = []
                else:
                    # Default: show all photos for current project (branch='all')
                    paths = db.get_images_by_branch(self.project_id, 'all')
                    if not paths:
                        # Fallback: get all unique images regardless of branch
                        paths = db.get_project_images(self.project_id)
                    if paths:
                        ctx["mode"] = "branch"
                        ctx["key"] = "all"
                        self.context = ctx
                        self.load_mode = "branch"
                        print(f"[GRID] Default view: loaded {len(paths)} photos (mode=None -> branch/all)")

            final_count = len(paths)
            base_count = final_count  # For status message compatibility

            # --- 3️: Render grid ---
            self._load_paths(paths)

            # --- 4️: User feedback ---
            context_label = {
                "folder": "Folder",
                "branch": "Branch",
                "date": "Date",
                "tag": "Tag",
                "videos": "Videos",
                "people": "People"
            }.get(mode or "unknown", "Unknown")

            tag_label = f" [Tag: {tag}]" if tag else ""
            media_label = "video(s)" if mode == "videos" else "photo(s)"
            status_msg = (
                f"{context_label}: {key or '—'} → "
                f"{final_count} {media_label} shown"
                f"{' (filtered)' if tag else ''}"
            )

            # Status bar update (if parent has one)
            mw = self.window()
            if hasattr(mw, "statusBar"):
                try:
                    mw.statusBar().showMessage(status_msg)
                except Exception:
                    pass

            # Detailed console log
            if tag:
                print(f"[GRID] Reloaded {final_count}/{base_count} thumbnails in {mode}-mode (tag={tag})")
            else:
                print(f"[GRID] Reloaded {final_count} thumbnails in {mode}-mode (base={base_count})")

            print(f"[GRID] Step 5: Emitting gridReloaded signal...")
            # Phase 2.3: Emit signal for status bar update
            self.gridReloaded.emit()
            print(f"[GRID] Step 5: ✓ gridReloaded signal emitted")

            # PHASE 4: Restore scroll position after photos are loaded (defer 200ms for layout)
            QTimer.singleShot(200, self.restore_scroll_position)

            self._reload_fail_count = 0  # reset circuit breaker on success
            print(f"[GRID] ====== reload() COMPLETED SUCCESSFULLY ======\n")
        except Exception as reload_error:
            print(f"[GRID] ✗✗✗ EXCEPTION in reload(): {reload_error}")
            import traceback
            traceback.print_exc()
            # Circuit breaker: after 3 consecutive failures, disable reload
            # to prevent infinite exception loops from debounced retries.
            self._reload_fail_count = getattr(self, '_reload_fail_count', 0) + 1
            if self._reload_fail_count >= 3:
                self._reload_disabled = True
                print(f"[GRID] ⚠️ CIRCUIT BREAKER: reload disabled after {self._reload_fail_count} consecutive failures")
            print(f"[GRID] ====== reload() FAILED WITH EXCEPTION ======\n")
        finally:
            # Always reset flag even if exception occurs
            print(f"[GRID] Finally block: Setting _reloading=False")
            self._reloading = False

    # ============================================================
    def _load_paths(self, paths: list[str]):
        """
        Build and render thumbnail items from the given path list.
        """
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QStandardItem, QPixmap, QIcon

        # CRITICAL FIX: Clear old model data BEFORE generating new token
        # This ensures old thumbnails are released before loading new ones
        self.model.clear()
        
        # CRITICAL FIX: Clear deprecated thumbnail cache to prevent memory leak
        # The cache is deprecated but still holds references during rapid reloads
        if hasattr(self, '_thumb_cache'):
            self._thumb_cache.clear()
        
        # CRITICAL FIX: Invalidate old thumbnail workers with new token
        self._reload_token = uuid.uuid4()
        self._current_reload_token = self._reload_token

        self._paths = [str(p) for p in paths]
        
        # 🏷️ Tag query — skip the expensive JOIN when project has no tags.
        # Cache the "project has tags" boolean; invalidated on set_project().
        _has_tags = getattr(self, '_project_has_tags', None)
        if _has_tags is None:
            # One-time lightweight check: does ANY tag assignment exist?
            try:
                with self.db._connect() as _tc:
                    _tcur = _tc.cursor()
                    _tcur.execute(
                        "SELECT 1 FROM photo_tags pt "
                        "JOIN photo_metadata pm ON pm.id = pt.photo_id "
                        "WHERE pm.project_id = ? LIMIT 1",
                        (self.project_id,),
                    )
                    _has_tags = _tcur.fetchone() is not None
                self._project_has_tags = _has_tags
            except Exception:
                _has_tags = True  # safe fallback: query tags

        if _has_tags:
            tag_map = self.db.get_tags_for_paths(self._paths, self.project_id)
            paths_with_tags = sum(1 for v in tag_map.values() if v)
            print(f"[GRID] Queried tags for {len(self._paths)} paths, {paths_with_tags} have tags")
        else:
            tag_map = {p: [] for p in self._paths}
            print(f"[GRID] Skipped tag query — project has no tags")
        
        # 📅 Grouping: fetch date_taken and sort descending
        import os, time
        from datetime import datetime
        def _safe_date(p):
            # Return POSIX timestamp (float) for stable comparisons
            try:
                meta = self.db.get_photo_metadata_by_path(p)
                dt_str = meta.get('date_taken') if meta else None
                if dt_str:
                    try:
                        # Normalize to timestamp
                        dt = datetime.fromisoformat(dt_str)
                        return dt.timestamp()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                return os.path.getmtime(p)
            except Exception:
                return 0.0
        
        # 📅 Grouping available only in Date mode
        use_date_headers = (self.load_mode == "date")
        if use_date_headers:
            date_map = {p: _safe_date(p) for p in self._paths}
            sorted_paths = sorted(self._paths, key=lambda x: date_map.get(x, 0.0), reverse=True)
        else:
            date_map = {}
            sorted_paths = list(self._paths)
        
        def _group_label(ts: float):
            try:
                d = datetime.fromtimestamp(ts).date()
                today = datetime.now().date()
                delta = (today - d).days
                if delta == 0:
                    return "Today"
                if delta == 1:
                    return "Yesterday"
                if 0 < delta < 7:
                    return "This Week"
                if today.year == d.year and today.month == d.month:
                    return "This Month"
                # Month-Year label
                return datetime.fromtimestamp(ts).strftime("%B %Y")
            except Exception:
                return "Earlier"

        # 📏 Aspect ratios from metadata (cheap — no file I/O)
        aspect_map = {}
        try:
            with self.db._connect() as _ac:
                _acur = _ac.cursor()
                for p in self._paths:
                    _acur.execute(
                        "SELECT width, height FROM photo_metadata WHERE path = ? LIMIT 1",
                        (p,),
                    )
                    row = _acur.fetchone()
                    if row and row[0] and row[1] and row[1] > 0:
                        aspect_map[p] = row[0] / row[1]
        except Exception:
            pass  # aspect_map stays empty — placeholders use default_aspect

        # 📏 Default aspect ratio for placeholders
        default_aspect = 1.5
        placeholder_size = self._thumb_size_for_aspect(default_aspect)

        # optional placeholder pixmap (scale shared placeholder if needed)
        # P0 Fix #7: Check cache before scaling to prevent memory leak
        cache_key = (placeholder_size.width(), placeholder_size.height())
        placeholder_pix = self._placeholder_cache.get(cache_key)

        if placeholder_pix is None:
            # Not in cache - check if base placeholder matches size
            if self._placeholder_pixmap.size() == placeholder_size:
                placeholder_pix = self._placeholder_pixmap
            else:
                # Create and cache new scaled version
                try:
                    placeholder_pix = self._placeholder_pixmap.scaled(placeholder_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                except Exception:
                    placeholder_pix = QPixmap(placeholder_size)
                    placeholder_pix.fill(Qt.transparent)

            # Cache for future use
            self._placeholder_cache[cache_key] = placeholder_pix
        
        token = self._reload_token

        # ── Batched model population for large datasets ───────────
        # For > SMALL_THRESHOLD paths, process first batch immediately
        # and schedule remaining batches via QTimer to keep UI responsive.
        total_paths = len(sorted_paths)
        if total_paths > self._grid_small_threshold:
            first_batch = sorted_paths[:self._grid_page_size]
            self._grid_batch_state = {
                'sorted_paths': sorted_paths,
                'tag_map': tag_map,
                'date_map': date_map,
                'aspect_map': aspect_map,  # FIX: Add aspect ratios for batched loading
                'use_date_headers': use_date_headers,
                'token': token,
                'placeholder_size': placeholder_size,
                'placeholder_pix': placeholder_pix,
                'group_label': _group_label,
                'next_start': self._grid_page_size,
            }
            paths_to_process = first_batch
            print(f"[GRID] Large dataset ({total_paths} paths) — batching: first {len(first_batch)}")
        else:
            self._grid_batch_state = None
            paths_to_process = sorted_paths

        self._populate_model_items(
            paths_to_process, 0, sorted_paths,
            tag_map, date_map, aspect_map, use_date_headers,
            token, placeholder_size, placeholder_pix, _group_label,
        )

        # Force layout cycle
        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()

        def _force_geometry_update():
            self.list_view.setSpacing(self._spacing)
            self.list_view.updateGeometry()
            self.list_view.doItemsLayout()
            self.list_view.repaint()

        QTimer.singleShot(100, _force_geometry_update)
        QTimer.singleShot(200, _force_geometry_update)

        self.list_view.viewport().update()

        loaded_label = f"{len(paths_to_process)}/{total_paths}" if self._grid_batch_state else str(total_paths)
        print(f"[GRID] Loaded {loaded_label} thumbnails.")

        # Kick the incremental thumbnail scheduler
        QTimer.singleShot(50, self.request_visible_thumbnails)

        # Schedule remaining batches (if batching)
        if self._grid_batch_state:
            self._grid_batch_timer.start(16)  # ~1 frame delay

        # Optional next-folder/date prefetch
        try:
            if hasattr(self.window(), "sidebar") and hasattr(self.window().sidebar, "get_next_branch_paths"):
                next_paths = self.window().sidebar.get_next_branch_paths(self.navigation_mode, self.navigation_key)
                if next_paths:
                    self.preload_cache_warmup(next_paths[:50])
        except Exception as e:
            print(f"[WarmUp] Prefetch skipped: {e}")

    # ============================================================
    # ⚙️  Batched model population helpers
    # ============================================================

    def _populate_model_items(
        self, paths_batch, global_offset, all_sorted_paths,
        tag_map, date_map, aspect_map, use_date_headers,
        token, placeholder_size, placeholder_pix, group_label_fn,
    ):
        """
        Create QStandardItem entries for *paths_batch* and append to model.

        *global_offset* is the index of the first element of paths_batch
        within *all_sorted_paths*.  This is used for date-header boundary
        detection across batches.

        FIX (2026-02-08): Added aspect_map parameter to use DB-stored dimensions
        instead of PIL.Image.open on UI thread (Google Photos best practice).
        """
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QStandardItem, QIcon

        for local_i, p in enumerate(paths_batch):
            i = global_offset + local_i  # index within all_sorted_paths
            item = QStandardItem()
            item.setEditable(False)

            np = self._norm_path(p)
            item.setData(np, Qt.UserRole)           # normalized model key
            item.setData(p,  Qt.UserRole + 6)       # real on-disk path

            item.setData(tag_map.get(p, []), Qt.UserRole + 2)
            item.setToolTip(", ".join([t for t in (tag_map.get(p, []) or []) if t]))

            # Date-group header boundary
            if use_date_headers:
                try:
                    ts = date_map.get(p, 0.0)
                    lbl = group_label_fn(ts) if ts else None
                except Exception:
                    lbl = None
                if i == 0:
                    header_label = lbl
                else:
                    prev_ts = date_map.get(all_sorted_paths[i - 1], 0.0)
                    prev_lbl = group_label_fn(prev_ts) if prev_ts else None
                    header_label = lbl if (lbl != prev_lbl) else None
            else:
                header_label = None
            item.setData(header_label, Qt.UserRole + 10)

            # FIX (2026-02-08): Use preloaded aspect_map instead of PIL.Image.open
            # This prevents UI freezes (Google Photos/iOS Photos best practice)
            initial_aspect = aspect_map.get(p, 1.5)
            item.setData(initial_aspect, Qt.UserRole + 1)
            item.setData(False, Qt.UserRole + 5)   # not scheduled yet

            size0 = self._thumb_size_for_aspect(initial_aspect)
            if use_date_headers and header_label:
                size0 = QSize(size0.width(), size0.height() + 24)
            item.setSizeHint(size0)

            # Video metadata
            if is_video_file(p):
                self._populate_video_metadata(item, p)

            # Placeholder icon
            item.setSizeHint(placeholder_size)
            item.setIcon(QIcon(placeholder_pix))

            self.model.appendRow(item)

    def _populate_video_metadata(self, item, p):
        """Set video-specific data roles on *item* for path *p*."""
        from PySide6.QtCore import Qt
        try:
            if self.project_id is None:
                item.setData(None, Qt.UserRole + 3)
                item.setData('unknown', Qt.UserRole + 7)
                item.setData('unknown', Qt.UserRole + 8)
                return
            video_meta = self.db.get_video_by_path(p, self.project_id)
            if video_meta:
                if 'duration_seconds' in video_meta:
                    item.setData(video_meta['duration_seconds'], Qt.UserRole + 3)
                item.setData(video_meta.get('metadata_status', 'pending'), Qt.UserRole + 7)
                item.setData(video_meta.get('thumbnail_status', 'pending'), Qt.UserRole + 8)
                tooltip_parts = [f"<b>{os.path.basename(p)}</b>"]
                if video_meta.get('duration_seconds'):
                    tooltip_parts.append(f"Duration: {format_duration(video_meta['duration_seconds'])}")
                w, h = video_meta.get('width'), video_meta.get('height')
                if w and h:
                    tooltip_parts.append(f"Resolution: {w}x{h}")
                if video_meta.get('fps'):
                    tooltip_parts.append(f"Frame Rate: {video_meta['fps']:.1f} fps")
                item.setToolTip("<br>".join(tooltip_parts))
            else:
                item.setData(None, Qt.UserRole + 3)
                item.setData('unknown', Qt.UserRole + 7)
                item.setData('unknown', Qt.UserRole + 8)
        except Exception:
            item.setData(None, Qt.UserRole + 3)
            item.setData('error', Qt.UserRole + 7)
            item.setData('error', Qt.UserRole + 8)

    def _process_grid_batch(self):
        """Process the next batch of paths from _grid_batch_state."""
        state = self._grid_batch_state
        if not state:
            return

        sorted_paths = state['sorted_paths']
        start = state['next_start']
        end = min(start + self._grid_page_size, len(sorted_paths))

        if start >= len(sorted_paths):
            # All batches processed
            self._grid_batch_state = None
            print(f"[GRID] All batches processed ({len(sorted_paths)} items)")
            return

        batch = sorted_paths[start:end]

        # FIX (2026-02-08): Pass aspect_map to avoid PIL.Image.open on UI thread
        self._populate_model_items(
            batch, start, sorted_paths,
            state['tag_map'], state['date_map'], state.get('aspect_map', {}),
            state['use_date_headers'],
            state['token'], state['placeholder_size'], state['placeholder_pix'],
            state['group_label'],
        )

        state['next_start'] = end
        remaining = len(sorted_paths) - end
        print(f"[GRID] Batch {start}-{end} added ({remaining} remaining)")

        if remaining > 0:
            # Schedule next batch
            self._grid_batch_timer.start(16)
        else:
            self._grid_batch_state = None
            print(f"[GRID] All batches processed ({len(sorted_paths)} items)")

        # Update layout and trigger visible thumbnail loading
        self.list_view.doItemsLayout()
        QTimer.singleShot(50, self.request_visible_thumbnails)

    # ============================================================
    # ⚙️  Optional Cache Warm-Up Prefetcher
    # ============================================================
    def preload_cache_warmup(self, next_paths: list[str]):
        """
        Prefetch thumbnails for the next folder/date in background.
        Does not display them, only decodes + stores in cache.
        """
        if not next_paths:
            return

        print(f"[WarmUp] Starting prefetch for {len(next_paths)} upcoming images...")

        # Avoid blocking UI
        from PySide6.QtCore import QRunnable, Slot

        class WarmupWorker(QRunnable):
            def __init__(self, paths, thumb_base, zoom_factor, cache, decode_timeout, placeholder):
                super().__init__()
                self.paths = paths
                self.thumb_base = thumb_base
                self.zoom_factor = zoom_factor
                self.cache = cache
                self.decode_timeout = decode_timeout
                self.placeholder = placeholder

            @Slot()
            def run(self):
                from thumb_cache_db import get_cache
                cache_db = get_cache()
                height = int(self.thumb_base * self.zoom_factor)
                count = 0

                for path in self.paths:
                    try:
                        if not os.path.exists(path):
                            continue
                        st = os.stat(path)
                        mtime = st.st_mtime

                        # skip if already cached
                        if path in self.cache and abs(self.cache[path]["mtime"] - mtime) < 0.1:
                            continue
                        if cache_db.has_entry(path, mtime):
                            continue

                        # decode quietly
                        pm = load_thumbnail_safe(path, height, self.cache, self.decode_timeout, self.placeholder)
                        if pm and not pm.isNull():
                            count += 1

                    except Exception as e:
                        print(f"[WarmUp] Skip {path}: {e}")

                print(f"[WarmUp] Prefetch complete: {count}/{len(self.paths)} thumbnails cached.")

        worker = WarmupWorker(
            next_paths, self._thumb_base, self._zoom_factor,
            self._thumb_cache, self._decode_timeout, self._placeholder_pixmap
        )
        self.thread_pool.start(worker)


    def _load_paths_later(self, paths: list[str]):
        """
        Build and render thumbnail items with tag overlay badges (⭐ 🧍 etc.)
        and dynamic placeholder sizing (fixed height, variable width).
        """
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QStandardItem, QPixmap, QIcon, QPainter, QColor, QFont
        import os

        self.model.clear()
        self._paths = [str(p) for p in paths]
        tag_map = self.db.get_tags_for_paths(self._paths, self.project_id)

        default_aspect = 1.5
        placeholder_size = self._thumb_size_for_aspect(default_aspect)

        # P0 Fix #7: Check cache before scaling to prevent memory leak
        cache_key = (placeholder_size.width(), placeholder_size.height())
        placeholder_pix = self._placeholder_cache.get(cache_key)

        if placeholder_pix is None:
            # Not in cache - check if base placeholder matches size
            if self._placeholder_pixmap.size() == placeholder_size:
                placeholder_pix = self._placeholder_pixmap
            else:
                # Create and cache new scaled version
                try:
                    placeholder_pix = self._placeholder_pixmap.scaled(placeholder_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                except Exception:
                    placeholder_pix = QPixmap(placeholder_size)
                    placeholder_pix.fill(Qt.transparent)

            # Cache for future use
            self._placeholder_cache[cache_key] = placeholder_pix

        active_tag = self.context.get("tag_filter") if isinstance(self.context, dict) else None

        token = self._reload_token
        for i, p in enumerate(sorted_paths):
            item = QStandardItem()
            item.setEditable(False)
            
            # Normalize path for tag lookup
            np = self._norm_path(p)
            
            item.setData(p, Qt.UserRole)
            item.setData(tag_map.get(np, []), Qt.UserRole + 2)
            item.setToolTip(", ".join([t for t in (tag_map.get(np, []) or []) if t]))
            item.setData(default_aspect, Qt.UserRole + 1)

            # --- Tag badge overlay on placeholder
            pix_with_badge = QPixmap(placeholder_pix)
            if active_tag:
                painter = QPainter(pix_with_badge)
                painter.setRenderHint(QPainter.Antialiasing)
                badge_color = QColor(255, 215, 0, 180)
                badge_icon = "⭐"

                if "face" in active_tag.lower():
                    badge_color = QColor(70, 130, 180, 180)
                    badge_icon = "🧍"
                elif "fav" in active_tag.lower():
                    badge_color = QColor(255, 215, 0, 180)
                    badge_icon = "⭐"
                else:
                    badge_color = QColor(144, 238, 144, 180)
                    badge_icon = "🏷"

                r = 22
                painter.setBrush(badge_color)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(
                    placeholder_pix.width() - r - 6,
                    placeholder_pix.height() - r - 6,
                    r, r
                )

                font = QFont("Segoe UI Emoji", 14, QFont.Bold)
                painter.setFont(font)
                painter.setPen(Qt.white)
                painter.drawText(
                    QRect(placeholder_pix.width() - r - 6, placeholder_pix.height() - r - 6, r, r),
                    Qt.AlignCenter, badge_icon
                )
                painter.end()

            item.setIcon(QIcon(pix_with_badge))
            item.setSizeHint(placeholder_size)
            self.model.appendRow(item)

            # ⚡ PERFORMANCE FIX: Don't start workers for all photos upfront!
            # Let request_visible_thumbnails() handle viewport-based loading
            # (Same fix as in _load_paths method)

        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()
        
        # 🔧 FIX: Force complete geometry update
        def _force_geometry_update():
            self.list_view.setSpacing(self._spacing)
            self.list_view.updateGeometry()
            self.list_view.doItemsLayout()
            self.list_view.repaint()
        
        QTimer.singleShot(100, _force_geometry_update)
        QTimer.singleShot(200, _force_geometry_update)
        
        self.list_view.viewport().update()
        print(f"[GRID] Loaded {len(self._paths)} thumbnails with tag badges.")

        # Trigger viewport-based loading
        QTimer.singleShot(0, self.request_visible_thumbnails)

    # --- ADD inside class ThumbnailGridQt (near other public helpers) ---

    def get_visible_paths(self) -> list[str]:
        """
        Return the paths that are currently in the view/model, in order.
        This reflects any sorting and filtering that has been applied.
        """
        out = []
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item:
                p = item.data(Qt.UserRole)
                if p:
                    out.append(p)
        return out

    def get_all_paths(self) -> list[str]:
        """
        Return the internal list of paths as last loaded by reload().
        Useful when the view/model hasn't been populated yet.
        """
        return list(getattr(self, "_paths", []))


    # >>> FIX: Add size and dimension calculation for metadata panel
    def _file_metadata_info(self, path: str) -> dict:
        """
        Return a metadata dict with file size, width, height and mtime.
        Uses cached thumbnails where possible for performance.
        """
        info = {"size_kb": None, "width": None, "height": None, "modified": None}
        try:
            if not path or not os.path.exists(path):
                return info
            st = os.stat(path)
            info["size_kb"] = round(st.st_size / 1024.0, 3)
            info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))

            # Try cached thumbnail for dimensions first
            pm_entry = self._thumb_cache.get(self._norm_path(path))
            if pm_entry and pm_entry.get("pixmap"):
                pm = pm_entry["pixmap"]
                info["width"], info["height"] = pm.width(), pm.height()
            else:
                # Fallback to reading image header only (fast)
                reader = QImageReader(path)
                sz = reader.size()
                if sz and sz.width() > 0 and sz.height() > 0:
                    info["width"], info["height"] = sz.width(), sz.height()
        except Exception as e:
            print(f"[MetaInfo] Could not extract info for {path}: {e}")
        return info
    # <<< FIX
