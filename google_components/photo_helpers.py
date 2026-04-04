"""
Google Photos Layout - Photo Helper Classes
Extracted from google_layout.py for better organization.

Contains:
- PhotoButton: Custom button with tag badge overlay painting
- ThumbnailSignals, ThumbnailLoader: Async thumbnail loading workers
- PhotoLoadSignals, PhotoLoadWorker: Async database photo query workers
- GooglePhotosEventFilter: Event filter for search and drag-select
- AutocompleteEventFilter: Event filter for people search autocomplete

Phase 3D extraction - Photo Workers & Helper Classes
"""

from PySide6.QtWidgets import QPushButton
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QEvent, Qt
from PySide6.QtGui import QPixmap, QColor, QImage
import os


class PhotoButton(QPushButton):
    """
    Custom button that paints tag badges directly on the thumbnail.
    Matches Current layout's delegate painting approach for stable badge rendering.
    """
    def __init__(self, photo_path: str, project_id: int, parent=None):
        super().__init__(parent)
        self.photo_path = photo_path
        self.project_id = project_id
        self._tags = []

    def set_tags(self, tags: list):
        """Update tags and trigger repaint."""
        self._tags = tags or []
        self.update()  # Trigger repaint

    def paintEvent(self, event):
        """Paint button with tag badges overlay."""
        # Paint base button first
        super().paintEvent(event)

        # Paint tag badges on top (only if tags exist)
        if not self._tags:
            return

        # Import Qt classes needed for painting
        from PySide6.QtGui import QPainter, QPen, QFont, QColor
        from PySide6.QtCore import QRect, Qt

        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        try:
            from settings_manager_qt import SettingsManager
            sm = SettingsManager()

            if not sm.get("badge_overlays_enabled", True):
                return  # Badges disabled

            badge_size = int(sm.get("badge_size_px", 22))
            max_badges = int(sm.get("badge_max_count", 4))
            badge_shape = str(sm.get("badge_shape", "circle")).lower()
            badge_margin = 4

            # Position badges in top-right corner
            x_right = self.width() - badge_margin - badge_size
            y_top = badge_margin

            # Map tags to icons and colors
            TAG_BADGE_CONFIG = {
                'favorite': ('★', QColor(255, 215, 0, 230), Qt.black),
                'face': ('👤', QColor(70, 130, 180, 220), Qt.white),
                'important': ('⚑', QColor(255, 69, 0, 220), Qt.white),
                'work': ('💼', QColor(0, 128, 255, 220), Qt.white),
                'travel': ('✈', QColor(34, 139, 34, 220), Qt.white),
                'personal': ('♥', QColor(255, 20, 147, 220), Qt.white),
                'family': ('👨‍👩‍👧', QColor(255, 140, 0, 220), Qt.white),
                'archive': ('📦', QColor(128, 128, 128, 220), Qt.white),
            }

            # Draw badges
            badge_count = 0
            for tag in self._tags:
                if badge_count >= max_badges:
                    break

                tag_lower = str(tag).lower().strip()

                # Get badge config
                if tag_lower in TAG_BADGE_CONFIG:
                    icon, bg_color, fg_color = TAG_BADGE_CONFIG[tag_lower]
                else:
                    icon, bg_color, fg_color = ('🏷', QColor(150, 150, 150, 230), Qt.white)

                # Calculate position
                y_pos = y_top + (badge_count * (badge_size + 4))
                badge_rect = QRect(x_right, y_pos, badge_size, badge_size)

                # Draw shadow
                if sm.get("badge_shadow", True):
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(0, 0, 0, 100))
                    painter.drawEllipse(badge_rect.adjusted(2, 2, 2, 2))

                # Draw badge background
                painter.setPen(Qt.NoPen)
                painter.setBrush(bg_color)
                if badge_shape == 'square':
                    painter.drawRect(badge_rect)
                elif badge_shape == 'rounded':
                    painter.drawRoundedRect(badge_rect, 4, 4)
                else:  # circle
                    painter.drawEllipse(badge_rect)

                # Draw icon
                painter.setPen(QPen(fg_color))
                font = QFont()
                font.setPointSize(11)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(badge_rect, Qt.AlignCenter, icon)

                badge_count += 1

            # Draw overflow indicator if more tags exist
            if len(self._tags) > max_badges:
                y_pos = y_top + (max_badges * (badge_size + 4))
                more_rect = QRect(x_right, y_pos, badge_size, badge_size)

                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(60, 60, 60, 220))
                if badge_shape == 'square':
                    painter.drawRect(more_rect)
                elif badge_shape == 'rounded':
                    painter.drawRoundedRect(more_rect, 4, 4)
                else:
                    painter.drawEllipse(more_rect)

                painter.setPen(QPen(Qt.white))
                font2 = QFont()
                font2.setPointSize(10)
                font2.setBold(True)
                painter.setFont(font2)
                painter.drawText(more_rect, Qt.AlignCenter, f"+{len(self._tags) - max_badges}")

        except Exception as e:
            print(f"[PhotoButton] Error painting badges: {e}")
        finally:
            painter.end()


class ThumbnailSignals(QObject):
    """
    Signals for async thumbnail loading (shared by all workers).

    FIX 2026-02-08: Changed to emit QImage instead of QPixmap.
    QImage is thread-safe, QPixmap is NOT thread-safe on Windows.
    The UI thread callback must convert QImage -> QPixmap.
    """
    loaded = Signal(str, object, int)  # (path, QImage, size) - use 'object' for QImage


class ThumbnailLoader(QRunnable):
    """
    Async thumbnail loader using QThreadPool.

    FIX 2026-02-08: CRITICAL THREAD-SAFETY FIX
    - Now uses get_thumbnail_image() which returns QImage (thread-safe)
    - Emits QImage via signal, NOT QPixmap
    - The UI thread callback must convert QImage -> QPixmap

    Based on Google Photos / Apple Photos best practice:
    - Worker threads generate QImage (CPU-backed, thread-safe)
    - UI thread converts QImage -> QPixmap (GPU-backed, UI-thread only)
    """

    def __init__(self, path: str, size: int, signals: ThumbnailSignals):
        super().__init__()
        self.path = path
        self.size = size
        self.signals = signals  # Use shared signal object

    def run(self):
        """
        Load thumbnail in background thread.

        FIX 2026-02-08: Uses get_thumbnail_image() which returns QImage (thread-safe).
        """
        try:
            # Check if it's a video
            # CRITICAL FIX: Include ALL video extensions (was missing .wmv, .flv, .mpg, .mpeg)
            # Must match _is_video_file() in media_lightbox.py for consistent behavior
            video_extensions = {
                '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp',
                '.flv', '.wmv', '.mpg', '.mpeg', '.mts', '.m2ts', '.ts',
                '.vob', '.ogv', '.divx', '.asf', '.rm', '.rmvb'
            }
            is_video = os.path.splitext(self.path)[1].lower() in video_extensions

            if is_video:
                # Generate or load video thumbnail using VideoThumbnailService
                # FIX 2026-02-08: Load as QImage for thread safety
                try:
                    from services.video_thumbnail_service import get_video_thumbnail_service
                    from PySide6.QtGui import QImageReader
                    from PySide6.QtCore import Qt

                    service = get_video_thumbnail_service()

                    # Prefer existing thumbnail; generate if missing
                    if service.thumbnail_exists(self.path):
                        thumb_path = service.get_thumbnail_path(self.path)
                    else:
                        thumb_path = service.generate_thumbnail(self.path, width=self.size, height=self.size)

                    if thumb_path and os.path.exists(thumb_path):
                        # FIX 2026-02-08: Use QImageReader to get QImage (thread-safe)
                        reader = QImageReader(str(thumb_path))
                        qimage = reader.read()
                        if not qimage.isNull():
                            scaled = qimage.scaled(self.size, self.size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            # Emit QImage (thread-safe) - UI thread will convert to QPixmap
                            self.signals.loaded.emit(self.path, scaled, self.size)
                            print(f"[ThumbnailLoader] ✓ Video thumbnail: {os.path.basename(self.path)}")
                        else:
                            # Fallback to placeholder
                            self._emit_video_placeholder()
                    else:
                        # Fallback to placeholder
                        self._emit_video_placeholder()
                except Exception as video_err:
                    print(f"[ThumbnailLoader] Video thumbnail error: {video_err}")
                    self._emit_video_placeholder()
            else:
                # Regular photo thumbnail
                # FIX 2026-02-08: Use get_thumbnail_image() which returns QImage (thread-safe)
                from app_services import get_thumbnail_image
                qimage = get_thumbnail_image(self.path, self.size, timeout=5.0)

                if qimage and not qimage.isNull():
                    # Emit QImage (thread-safe) - UI thread will convert to QPixmap
                    self.signals.loaded.emit(self.path, qimage, self.size)
        except Exception as e:
            print(f"[ThumbnailLoader] Error loading {self.path}: {e}")

    def _emit_video_placeholder(self):
        """Emit a video placeholder icon using Qt drawing primitives.

        Draws a dark background with a centered play-triangle and a
        "VIDEO" label.  Does NOT rely on emoji (unreliable cross-platform).

        FIX 2026-02-08: Now emits QImage (thread-safe) instead of QPixmap.
        """
        from PySide6.QtGui import QPainter, QFont, QPen, QBrush, QPolygonF
        from PySide6.QtCore import Qt, QPointF, QRectF

        sz = self.size
        # FIX 2026-02-08: Create QImage instead of QPixmap (thread-safe)
        # QImage.Format_ARGB32 is the standard format for 32-bit images
        qimage = QImage(sz, sz, QImage.Format_ARGB32)
        qimage.fill(QColor(38, 38, 38))  # dark background

        painter = QPainter(qimage)
        painter.setRenderHint(QPainter.Antialiasing)

        # --- play triangle (centred, white, 40 % of size) ---
        tri_sz = sz * 0.35
        cx, cy = sz / 2.0, sz / 2.0 - sz * 0.06
        left_x = cx - tri_sz * 0.4
        right_x = cx + tri_sz * 0.5
        top_y = cy - tri_sz * 0.5
        bot_y = cy + tri_sz * 0.5
        triangle = QPolygonF([
            QPointF(left_x, top_y),
            QPointF(right_x, cy),
            QPointF(left_x, bot_y),
        ])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.drawPolygon(triangle)

        # --- "VIDEO" label below the triangle ---
        painter.setPen(QPen(QColor(180, 180, 180)))
        font = QFont()
        font.setPixelSize(max(10, sz // 10))
        font.setBold(True)
        painter.setFont(font)
        label_rect = QRectF(0, cy + tri_sz * 0.45, sz, sz * 0.2)
        painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, "VIDEO")

        painter.end()
        # FIX 2026-02-08: Emit QImage (thread-safe) - UI thread will convert to QPixmap
        self.signals.loaded.emit(self.path, qimage, self.size)


# PHASE 2 Task 2.1: Photo loading worker (move database queries off GUI thread)
class PhotoLoadSignals(QObject):
    """Signals for async photo database queries."""
    loaded = Signal(int, list)  # (generation, rows)  # rows = [(path, date_taken, width, height), ...]
    error = Signal(int, str)  # (generation, error_message)


class PhotoLoadWorker(QRunnable):
    """
    PHASE 2 Task 2.1: Background worker for loading photos from database.
    Prevents GUI freezes with large datasets (10,000+ photos).
    """
    def __init__(self, project_id, filter_params, generation, signals):
        super().__init__()
        self.project_id = project_id
        self.filter_params = filter_params  # Dict with year, month, day, folder, person
        self.generation = generation
        self.signals = signals

    def run(self):
        """Query database in background thread."""
        db = None
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()  # Per-thread instance (thread-safe)

            # Build photo query
            photo_query_parts = ["""
                SELECT DISTINCT pm.path, pm.created_date as date_taken, pm.width, pm.height
                FROM photo_metadata pm
                JOIN project_images pi ON pm.path = pi.image_path
                WHERE pi.project_id = ?
            """]
            photo_params = [self.project_id]

            # Add filters
            filter_year = self.filter_params.get('year')
            filter_month = self.filter_params.get('month')
            filter_day = self.filter_params.get('day')
            filter_folder = self.filter_params.get('folder')
            filter_person = self.filter_params.get('person')

            if filter_year is not None:
                photo_query_parts.append("AND strftime('%Y', pm.created_date) = ?")
                photo_params.append(str(filter_year))

            if filter_month is not None and filter_year is not None:
                photo_query_parts.append("AND strftime('%m', pm.created_date) = ?")
                photo_params.append(f"{filter_month:02d}")

            if filter_day is not None and filter_year is not None and filter_month is not None:
                photo_query_parts.append("AND strftime('%d', pm.created_date) = ?")
                photo_params.append(f"{filter_day:02d}")

            if filter_folder is not None:
                import platform
                normalized_folder = filter_folder.replace('\\', '/')
                if platform.system() == 'Windows':
                    normalized_folder = normalized_folder.lower()
                photo_query_parts.append("AND pm.path LIKE ?")
                photo_params.append(f"{normalized_folder}%")

            if filter_person is not None:
                photo_query_parts.append("""
                    AND pm.path IN (
                        SELECT DISTINCT image_path
                        FROM face_crops
                        WHERE project_id = ? AND branch_key = ?
                    )
                """)
                photo_params.append(self.project_id)
                photo_params.append(filter_person)

            # Filter by specific paths (location filtering)
            filter_paths = self.filter_params.get('paths')
            if filter_paths is not None and len(filter_paths) > 0:
                # Normalize paths for consistent matching
                import platform
                normalized_paths = []
                for p in filter_paths:
                    normalized = p.replace('\\', '/')
                    if platform.system() == 'Windows':
                        normalized = normalized.lower()
                    normalized_paths.append(normalized)

                # Create placeholders for SQL IN clause
                placeholders = ','.join('?' * len(normalized_paths))
                photo_query_parts.append(f"AND pm.path IN ({placeholders})")
                photo_params.extend(normalized_paths)

            # Build video query (mirror photo query structure)
            video_query_parts = ["""
                SELECT DISTINCT vm.path, vm.created_date as date_taken, vm.width, vm.height
                FROM video_metadata vm
                WHERE vm.project_id = ?
            """]
            video_params = [self.project_id]

            if filter_year is not None:
                video_query_parts.append("AND strftime('%Y', vm.created_date) = ?")
                video_params.append(str(filter_year))

            if filter_month is not None and filter_year is not None:
                video_query_parts.append("AND strftime('%m', vm.created_date) = ?")
                video_params.append(f"{filter_month:02d}")

            if filter_day is not None and filter_year is not None and filter_month is not None:
                video_query_parts.append("AND strftime('%d', vm.created_date) = ?")
                video_params.append(f"{filter_day:02d}")

            if filter_folder is not None:
                import platform
                normalized_folder = filter_folder.replace('\\', '/')
                if platform.system() == 'Windows':
                    normalized_folder = normalized_folder.lower()
                video_query_parts.append("AND vm.path LIKE ?")
                video_params.append(f"{normalized_folder}%")

            # Combine queries
            photo_query = "\n".join(photo_query_parts)
            video_query = "\n".join(video_query_parts)

            # Only include videos if NOT filtering by person or location
            # (videos don't have GPS data, so they shouldn't appear in location-filtered views)
            if filter_person is None and filter_paths is None:
                query = f"{photo_query}\nUNION ALL\n{video_query}\nORDER BY date_taken DESC"
                params = photo_params + video_params
            else:
                query = f"{photo_query}\nORDER BY date_taken DESC"
                params = photo_params

            # Execute query
            with db._connect() as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                cur = conn.cursor()
                cur.execute(query, tuple(params))
                rows = cur.fetchall()

            # Emit results with generation number
            self.signals.loaded.emit(self.generation, rows)

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.signals.error.emit(self.generation, error_msg)
        # NOTE: No finally block needed - ReferenceDB uses connection pooling
        # The 'with db._connect() as conn:' context manager handles cleanup automatically



class GooglePhotosEventFilter(QObject):
    """
    Event filter for GooglePhotosLayout.

    Handles keyboard navigation in search suggestions and mouse events for drag-select.
    """
    def __init__(self, layout):
        super().__init__()
        self.layout = layout

    def eventFilter(self, obj, event):
        """Handle events for search box, timeline viewport, and search suggestions popup."""
        # BUGFIX: Defensive check - ensure layout and search_box exist
        if not hasattr(self, 'layout'):
            return super().eventFilter(obj, event)

        # NUCLEAR FIX: Block Show events on search_suggestions popup during layout changes
        if hasattr(self.layout, 'search_suggestions') and obj == self.layout.search_suggestions:
            if event.type() == QEvent.Show:
                # Check if popup is blocked due to layout changes
                if hasattr(self.layout, '_popup_blocked') and self.layout._popup_blocked:
                    # Block the show event - popup will not appear
                    return True

        # Search box keyboard navigation - check search_box exists
        if hasattr(self.layout, 'search_box') and obj == self.layout.search_box and event.type() == QEvent.KeyPress:
            if hasattr(self.layout, 'search_suggestions') and self.layout.search_suggestions.isVisible():
                key = event.key()

                # Arrow keys navigate suggestions
                if key == Qt.Key_Down:
                    current = self.layout.search_suggestions.currentRow()
                    if current < self.layout.search_suggestions.count() - 1:
                        self.layout.search_suggestions.setCurrentRow(current + 1)
                    return True
                elif key == Qt.Key_Up:
                    current = self.layout.search_suggestions.currentRow()
                    if current > 0:
                        self.layout.search_suggestions.setCurrentRow(current - 1)
                    return True
                elif key == Qt.Key_Return or key == Qt.Key_Enter:
                    # Enter key selects highlighted suggestion
                    current_item = self.layout.search_suggestions.currentItem()
                    if current_item:
                        self.layout._on_suggestion_clicked(current_item)
                        return True
                elif key == Qt.Key_Escape:
                    self.layout.search_suggestions.hide()
                    return True

        # Timeline viewport drag-select
        # CRITICAL FIX: Check if timeline_scroll still exists before accessing viewport
        # RuntimeError occurs when switching layouts - Qt C++ object gets deleted
        try:
            if hasattr(self.layout, 'timeline_scroll') and obj == self.layout.timeline_scroll.viewport():
                if event.type() == QEvent.MouseButtonPress:
                    if self.layout._handle_drag_select_press(event.pos()):
                        return True
                elif event.type() == QEvent.MouseMove:
                    self.layout._handle_drag_select_move(event.pos())
                    return self.layout.is_dragging  # Consume event if dragging
                elif event.type() == QEvent.MouseButtonRelease:
                    if self.layout.is_dragging:
                        self.layout._handle_drag_select_release(event.pos())
                        return True
        except RuntimeError:
            # QScrollArea was deleted - safe to ignore
            pass

        return False




class AutocompleteEventFilter(QObject):
    """Event filter for people search autocomplete keyboard navigation."""

    def __init__(self, search_widget, autocomplete_widget, parent_layout):
        super().__init__()
        self.search_widget = search_widget
        self.autocomplete_widget = autocomplete_widget
        self.parent_layout = parent_layout

    def eventFilter(self, obj, event):
        """Handle keyboard events for autocomplete navigation."""
        if obj == self.search_widget and event.type() == QEvent.KeyPress:
            if self.autocomplete_widget.isVisible():
                key = event.key()
                if key == Qt.Key_Down:
                    # Move to autocomplete list
                    self.autocomplete_widget.setFocus()
                    self.autocomplete_widget.setCurrentRow(0)
                    return True
                elif key == Qt.Key_Escape:
                    self.autocomplete_widget.hide()
                    return True
                elif key == Qt.Key_Return or key == Qt.Key_Enter:
                    # Select first item if autocomplete is visible
                    if self.autocomplete_widget.count() > 0:
                        first_item = self.autocomplete_widget.item(0)
                        self.parent_layout._on_autocomplete_selected(first_item)
                        return True

        return super().eventFilter(obj, event)
