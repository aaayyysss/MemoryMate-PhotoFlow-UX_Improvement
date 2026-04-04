"""
DetailsPanel - Rich metadata display for photos and videos.

Extracted from main_window_qt.py as part of Phase 1 refactoring.
"""

import os
import time as _time

from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QScrollArea,
    QSizePolicy, QApplication, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPixmap, QColor, QPainter, QFont, QImageReader
from services.safe_image_loader import safe_decode_qimage

#from i18n import tr
from translation_manager import tr


class DetailsPanel(QWidget):
    """
    Rich metadata panel:
    - Preview (auto-rotated)
    - DB row (photo_metadata)
    - Filesystem stats
    - EXIF (camera, lens, ISO, shutter, aperture, focal length, date taken, GPS)
    - 'Copy' button to copy all metadata as plain text
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)

        # Widgets
        self.thumb = QLabel(alignment=Qt.AlignCenter)
        self.thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.meta = QLabel(alignment=Qt.AlignTop)
        self.meta.setWordWrap(True)

        self.btn_copy = QPushButton(tr('metadata.copy_all'))
        self.btn_copy.setToolTip(tr('metadata.copy_all'))
        self.btn_copy.clicked.connect(self._copy_all)

        # Style
        self.meta.setStyleSheet("""
            QLabel {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 10pt;
                color: #333;
            }
            table {
                border-collapse: collapse;
            }
            td {
                padding: 2px 6px;
                vertical-align: top;
            }
            b {
                color: #004A7F;
            }
            .hdr {
                color: #004A7F; font-weight: 600; margin-top: 6px;
            }
        """)

        # Layout
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self.thumb, 3)
        self.meta_scroll = QScrollArea()
        self.meta_scroll.setWidget(self.meta)
        self.meta_scroll.setWidgetResizable(True)
        lay.addWidget(self.meta_scroll, 2)
        lay.addWidget(self.btn_copy, 0, alignment=Qt.AlignRight)

        # Internal: last plain-text metadata
        self._last_plaintext = ""

    def clear(self):
        self.thumb.clear()
        self.meta.setText("")
        self._last_plaintext = ""

    # ---------- Public API ----------
    def update_path(self, path: str):
        """Update preview and metadata for selected image or video."""
        if not path:
            self.clear()
            return

        import os, time
        base = os.path.basename(path)

        # 🎬 Check if this is a video file
        video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg')
        is_video = path.lower().endswith(video_extensions)

        if is_video:
            # 🎬 VIDEO: Load metadata from database FIRST
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Get project_id from parent MainWindow
            project_id = None
            if hasattr(self.parent(), 'grid') and hasattr(self.parent().grid, 'project_id'):
                project_id = self.parent().grid.project_id
            elif hasattr(self.parent(), 'sidebar') and hasattr(self.parent().sidebar, 'project_id'):
                project_id = self.parent().sidebar.project_id

            if not project_id:
                from app_services import get_default_project_id
                project_id = get_default_project_id()

            video_meta = db.get_video_by_path(path, project_id) if project_id else None

            # 🎬 VIDEO: Load thumbnail from VideoThumbnailService
            try:
                from services.video_thumbnail_service import get_video_thumbnail_service
                thumb_service = get_video_thumbnail_service()
                thumb_path = thumb_service.get_thumbnail_path(path)

                if thumb_path.exists():
                    from PIL import Image
                    from PIL.ImageQt import ImageQt as pil_to_qimage
                    # BUG-C1 FIX: Use context manager to prevent resource leak
                    with Image.open(str(thumb_path)) as thumb_img:
                        qimg = pil_to_qimage(thumb_img)
                        thumb_pm = QPixmap.fromImage(qimg)
                    if not thumb_pm.isNull():
                        # 🎬 ENHANCEMENT: Larger thumbnail for videos (280x210)
                        scaled_pm = thumb_pm.scaled(280, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                        # 🏷️ Add tag icon overlay if video has tags
                        from services.tag_service import get_tag_service
                        svc = get_tag_service()
                        try:
                            tags = []
                            if project_id is not None:
                                tags = (svc.get_tags_for_paths([path], project_id) or {}).get(path, [])
                        except Exception:
                            tags = []
                        if (not tags) and video_meta and video_meta.get('tags'):
                            tags_str = video_meta.get('tags', '')
                            if tags_str:
                                tags = [t.strip() for t in tags_str.split(',') if t.strip()]
                        # Fallback: read from grid model if still empty
                        if not tags:
                            try:
                                parent = self.parent()
                                if hasattr(parent, 'grid') and hasattr(parent.grid, 'model'):
                                    import os
                                    target_norm = os.path.normcase(os.path.abspath(os.path.normpath(path)))
                                    mdl = parent.grid.model
                                    for r in range(mdl.rowCount()):
                                        it = mdl.item(r)
                                        if not it:
                                            continue
                                        p0 = it.data(Qt.UserRole)
                                        p1 = it.data(Qt.UserRole + 6)
                                        match0 = p0 and (os.path.normcase(os.path.abspath(os.path.normpath(p0))) == target_norm)
                                        match1 = p1 and (os.path.normcase(os.path.abspath(os.path.normpath(p1))) == target_norm)
                                        if match0 or match1:
                                            t = it.data(Qt.UserRole + 2) or []
                                            if t:
                                                tags = t
                                            break
                            except Exception:
                                pass

                        if tags:
                            # Draw tag icon overlay on thumbnail
                            from PySide6.QtGui import QPainter, QFont
                            overlay_pm = QPixmap(scaled_pm)
                            painter = QPainter(overlay_pm)

                            # Draw semi-transparent tag badge in top-right corner
                            badge_size = max(24, min(36, int(overlay_pm.width() * 0.10)))
                            x = overlay_pm.width() - badge_size - 6
                            y = 6

                            # Background circle
                            painter.setRenderHint(QPainter.Antialiasing, True)
                            painter.setRenderHint(QPainter.TextAntialiasing, True)
                            painter.setBrush(QColor(102, 126, 234, 200))  # Purple with transparency
                            painter.setPen(Qt.NoPen)
                            painter.drawEllipse(x, y, badge_size, badge_size)

                            # Draw stacked badges for each tag (top-right column)
                            from settings_manager_qt import SettingsManager
                            sm = SettingsManager()
                            if sm.get("badge_overlays_enabled", True):
                                icons = []
                                for t in tags:
                                    tl = t.lower().strip()
                                    if tl == 'favorite':
                                        icons.append('★')
                                    elif tl == 'face':
                                        icons.append('👤')
                                    else:
                                        icons.append('🏷️')
                                badge_size = int(sm.get("badge_size_px", badge_size))
                                max_badges = min(len(icons), int(sm.get("badge_max_count", 4)))
                                for i in range(max_badges):
                                    by = y + i * (badge_size + 4)
                                    painter.setBrush(QColor(102, 126, 234, 200))
                                    painter.setPen(Qt.NoPen)
                                    shape = str(sm.get("badge_shape", "circle")).lower()
                                    rect_i = QRect(x, by, badge_size, badge_size)
                                    if shape == 'square':
                                        painter.drawRect(rect_i)
                                    elif shape == 'rounded':
                                        painter.drawRoundedRect(rect_i, 4, 4)
                                    else:
                                        painter.drawEllipse(x, by, badge_size, badge_size)
                                    painter.setPen(QColor(255, 255, 255))
                                    painter.drawText(x, by, badge_size, badge_size, Qt.AlignCenter, icons[i])
                            painter.end()
                            scaled_pm = overlay_pm

                        # Replace thumb widget with container
                        self.thumb.setPixmap(scaled_pm)
                        self.thumb.setToolTip(', '.join(tags) if tags else '')
                        from PySide6.QtWidgets import QGraphicsDropShadowEffect
                        _eff = QGraphicsDropShadowEffect()
                        _eff.setBlurRadius(12)
                        _eff.setOffset(0, 2)
                        self.thumb.setGraphicsEffect(_eff)
                        self.thumb.setStyleSheet("""
                            QLabel {
                                background: #000;
                                border: 2px solid #0078d4;
                                border-radius: 8px;
                                padding: 4px;
                            }
                        """)
                    else:
                        self.thumb.setText("🎬 (video)")
                        self.thumb.setStyleSheet("font-size: 48px;")
                else:
                    self.thumb.setText("🎬 (video)")
                    self.thumb.setStyleSheet("font-size: 48px;")
            except Exception as e:
                print(f"[DetailsPanel] Failed to load video thumbnail: {e}")
                self.thumb.setText("🎬 (video)")
                self.thumb.setStyleSheet("font-size: 48px;")

            # filesystem
            try:
                st = os.stat(path)
                fs_size_kb = f"{st.st_size/1024:,.1f} KB"
                fs_mtime = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(st.st_mtime))
            except Exception:
                fs_size_kb, fs_mtime = "-", "-"

            # Build HTML for video with enhanced styling
            def r(k, v):
                v = "-" if v in (None, "", []) else v
                return f"<tr><td style='padding:4px 8px; color:#555; font-weight:500;'>{k}</td><td style='padding:4px 8px; color:#000; font-weight:600;'>{v}</td></tr>"

            video_rows = []
            if video_meta:
                # Format duration
                duration = video_meta.get('duration_seconds')
                if duration:
                    mins, secs = divmod(int(duration), 60)
                    duration_str = f"⏱️ {mins}:{secs:02d}"
                else:
                    duration_str = "-"

                # Format resolution with quality indicator
                width = video_meta.get('width', 0)
                height = video_meta.get('height', 0)
                if height >= 2160:
                    quality = "4K"
                elif height >= 1080:
                    quality = "Full HD"
                elif height >= 720:
                    quality = "HD"
                else:
                    quality = "SD"
                resolution_str = f"📺 {width}×{height} ({quality})"

                video_rows.append(r(tr('metadata.field_duration'), duration_str))
                video_rows.append(r(tr('metadata.field_resolution'), resolution_str))
                fps = video_meta.get('fps')
                if fps:
                    try:
                        fps_val = float(fps)
                        fps_str = f"{int(round(fps_val))}" if abs(fps_val - round(fps_val)) < 0.05 else f"{fps_val:.2f}"
                    except Exception:
                        fps_str = str(fps)
                else:
                    fps_str = "-"
                video_rows.append(r(tr('metadata.field_frame_rate'), f"🎬 {fps_str} fps"))
                video_rows.append(r(tr('metadata.field_codec'), f"💿 {video_meta.get('codec', '-')}"))

                bitrate = video_meta.get('bitrate_kbps')
                if bitrate:
                    video_rows.append(r(tr('metadata.field_bitrate'), f"📊 {bitrate/1000:.2f} Mbps"))

                file_size = video_meta.get('file_size_mb', 0)
                video_rows.append(r(tr('metadata.field_file_size'), f"💾 {file_size:.1f} MB"))

                # Status with emoji indicators
                meta_status = video_meta.get('metadata_status', 'pending')
                meta_icon = "✅" if meta_status == "completed" else "⏳" if meta_status == "pending" else "❌"
                video_rows.append(r(tr('metadata.field_metadata_status'), f"{meta_icon} {meta_status}"))

                thumb_status = video_meta.get('thumbnail_status', 'pending')
                thumb_icon = "✅" if thumb_status == "ok" else "⏳" if thumb_status == "pending" else "❌"
                video_rows.append(r(tr('metadata.field_thumbnail_status'), f"{thumb_icon} {thumb_status}"))

            fs_rows = [
                r(tr('metadata.field_file'), base),
                r(tr('metadata.field_path'), path),
                r(tr('metadata.field_size_fs'), fs_size_kb),
                r(tr('metadata.field_modified_fs'), fs_mtime),
            ]

            sections = []
            if video_rows:
                sections.append(f"""
                    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                color: white; padding: 8px 12px; border-radius: 6px;
                                font-weight: 700; font-size: 11pt; margin-bottom: 8px;'>
                        🎬 {tr('metadata.section_video_info')}
                    </div>
                    <table style='width:100%; margin-bottom:12px;'>{''.join(video_rows)}</table>
                """)
            sections.append(f"""
                <div style='background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                            color: white; padding: 8px 12px; border-radius: 6px;
                            font-weight: 700; font-size: 11pt; margin-bottom: 8px;'>
                    📁 {tr('metadata.section_file_info')}
                </div>
                <table style='width:100%;'>{''.join(fs_rows)}</table>
            """)

            html = f"""
                <div style='padding: 8px;'>
                    <div style='font-size: 12pt; font-weight: 700; color: #333; margin-bottom: 12px;
                                padding: 8px; background: #f0f0f0; border-radius: 6px;
                                border-left: 4px solid #0078d4;'>
                        🎬 {base}
                    </div>
                    {''.join(sections)}
                </div>
            """
            self.meta.setText(html)

            # Prepare plain text for copy
            lines = [f"File: {base}"]
            if video_meta:
                lines.append("[Video Metadata]")
                lines.append(f"  Duration: {duration_str}")
                lines.append(f"  Resolution: {video_meta.get('width', '-')}×{video_meta.get('height', '-')}")
                lines.append(f"  Frame Rate: {video_meta.get('fps', '-')} fps")
                lines.append(f"  Codec: {video_meta.get('codec', '-')}")
                if bitrate:
                    lines.append(f"  Bitrate: {bitrate/1000:.2f} Mbps")
            lines.append("[File]")
            lines.append(f"  Size (FS): {fs_size_kb}")
            lines.append(f"  Modified (FS): {fs_mtime}")
            self._last_plaintext = "\n".join(lines)

        else:
            # 🖼️ PHOTO: Original logic
            # --- Collect data from DB + FS + EXIF ---
            from reference_db import ReferenceDB
            db = ReferenceDB()
            row = db.get_photo_metadata_by_path(path) or {}

            from services.tag_service import get_tag_service
            svc = get_tag_service()
            try:
                tags = []
                # Prefer project-aware TagService lookup
                project_id = None
                if hasattr(self.parent(), 'grid') and hasattr(self.parent().grid, 'project_id'):
                    project_id = self.parent().grid.project_id
                elif hasattr(self.parent(), 'sidebar') and hasattr(self.parent().sidebar, 'project_id'):
                    project_id = self.parent().sidebar.project_id
                if project_id is None:
                    from app_services import get_default_project_id
                    project_id = get_default_project_id()
                if project_id is not None:
                    tags = (svc.get_tags_for_paths([path], project_id) or {}).get(path, [])
                    # Also try tags for the currently selected items in the grid
                    try:
                        parent = self.parent()
                        if hasattr(parent, 'grid') and hasattr(parent.grid, 'get_selected_paths'):
                            sel_paths = parent.grid.get_selected_paths()
                            if sel_paths:
                                sel_map = svc.get_tags_for_paths(sel_paths, project_id) or {}
                                import os
                                target_norm = os.path.normcase(os.path.abspath(os.path.normpath(path)))
                                # Direct key or normalized match
                                if path in sel_map and sel_map[path]:
                                    tags = sel_map[path]
                                else:
                                    for k, v in sel_map.items():
                                        kn = os.path.normcase(os.path.abspath(os.path.normpath(k)))
                                        if kn == target_norm and v:
                                            tags = v
                                            break
                    except Exception:
                        pass
            except Exception:
                tags = []
            try:
                parent = self.parent()
                if hasattr(parent, '_selection_tag_cache') and parent._selection_tag_cache:
                    cache_tags = parent._selection_tag_cache.get(path)
                    if cache_tags:
                        tags = [str(t).strip() for t in cache_tags if str(t).strip()]
            except Exception:
                pass

            # Fallback: use DB row tags if service returns none
            if (not tags) and row and row.get('tags'):
                tags_str = row.get('tags')
                if isinstance(tags_str, str):
                    tags = [t.strip() for t in tags_str.split(',') if t.strip()]
            # Fallback: read tags from grid model for the selected path
            if not tags:
                try:
                    parent = self.parent()
                    if hasattr(parent, 'grid') and hasattr(parent.grid, 'model'):
                        import os
                        target_norm = os.path.normcase(os.path.abspath(os.path.normpath(path)))
                        mdl = parent.grid.model
                        for r in range(mdl.rowCount()):
                            it = mdl.item(r)
                            if not it:
                                continue
                            p0 = it.data(Qt.UserRole)
                            p1 = it.data(Qt.UserRole + 6)
                            match0 = p0 and (os.path.normcase(os.path.abspath(os.path.normpath(p0))) == target_norm)
                            match1 = p1 and (os.path.normcase(os.path.abspath(os.path.normpath(p1))) == target_norm)
                            if match0 or match1:
                                t = it.data(Qt.UserRole + 2) or []
                                t = [str(x).strip() for x in t if str(x).strip()]
                                if t:
                                    tags = t
                                break
                except Exception:
                    pass
            # Fallback: use DB row tags if service returns none
            if (not tags) and row and row.get('tags'):
                tags_str = row.get('tags')
                if isinstance(tags_str, str):
                    tags = [t.strip() for t in tags_str.split(',') if t.strip()]
            # Fallback: read tags from grid model for the selected path
            if not tags:
                try:
                    parent = self.parent()
                    if hasattr(parent, 'grid') and hasattr(parent.grid, 'model'):
                        import os
                        target_norm = os.path.normcase(os.path.abspath(os.path.normpath(path)))
                        mdl = parent.grid.model
                        for r in range(mdl.rowCount()):
                            it = mdl.item(r)
                            if not it:
                                continue
                            p0 = it.data(Qt.UserRole)
                            p1 = it.data(Qt.UserRole + 6)
                            match0 = p0 and (os.path.normcase(os.path.abspath(os.path.normpath(p0))) == target_norm)
                            match1 = p1 and (os.path.normcase(os.path.abspath(os.path.normpath(p1))) == target_norm)
                            if match0 or match1:
                                t = it.data(Qt.UserRole + 2) or []
                                if t:
                                    tags = t
                                break
                except Exception:
                    pass

            # --- Preview (memory-safe; SafeImageLoader caps decode size) ---
            img = safe_decode_qimage(path, max_dim=512)
            if not img.isNull():
                pm = QPixmap.fromImage(img).scaledToWidth(260, Qt.SmoothTransformation)

                # 🏷️ Add tag icon overlay if photo has tags
                if tags:
                    from PySide6.QtGui import QPainter, QFont
                    overlay_pm = QPixmap(pm)
                    painter = QPainter(overlay_pm)

                    # Draw semi-transparent tag badge in top-right corner
                    badge_size = max(22, min(34, int(overlay_pm.width() * 0.10)))
                    x = overlay_pm.width() - badge_size - 6
                    y = 6

                    # Background circle
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.setRenderHint(QPainter.TextAntialiasing, True)
                    painter.setBrush(QColor(102, 126, 234, 200))  # Purple with transparency
                    painter.setPen(Qt.NoPen)
                    painter.drawEllipse(x, y, badge_size, badge_size)

                    # Tag emoji
                    painter.setPen(QColor(255, 255, 255))
                    font = QFont("Segoe UI Emoji", 14, QFont.Bold)
                    painter.setFont(font)

                    # Draw stacked badges for each tag (top-right column)
                    icons = []
                    for t in tags:
                        tl = t.lower().strip()
                        if tl == 'favorite':
                            icons.append('★')
                        elif tl == 'face':
                            icons.append('👤')
                        else:
                            icons.append('🏷️')
                    max_badges = min(len(icons), 4)
                    for i in range(max_badges):
                        by = y + i * (badge_size + 4)
                        painter.setBrush(QColor(102, 126, 234, 200))
                        painter.setPen(Qt.NoPen)
                        painter.drawEllipse(x, by, badge_size, badge_size)
                        painter.setPen(QColor(255, 255, 255))
                        painter.drawText(x, by, badge_size, badge_size, Qt.AlignCenter, icons[i])
                    painter.end()
                    pm = overlay_pm

                self.thumb.setPixmap(pm)
                self.thumb.setToolTip(', '.join(tags) if tags else '')
                from PySide6.QtWidgets import QGraphicsDropShadowEffect
                _eff = QGraphicsDropShadowEffect()
                _eff.setBlurRadius(12)
                _eff.setOffset(0, 2)
                self.thumb.setGraphicsEffect(_eff)
                self.thumb.setStyleSheet("""
                            QLabel {
                                background: #000;
                                border: 2px solid #0078d4;
                                border-radius: 8px;
                                padding: 4px;
                            }
                        """)
            else:
                self.thumb.setText("(no preview)")

            # filesystem
            try:
                import os, time
                st = os.stat(path)
                fs_size_kb = f"{st.st_size/1024:,.1f} KB"
    #            fs_mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
                fs_mtime = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(st.st_mtime))
            except Exception:
                fs_size_kb, fs_mtime = "-", "-"

            # >>> FIX: merge extra metadata (dimensions, safe size, modified)
            extra = self._file_metadata_info(path)
            if extra:
                if extra.get("width") and extra.get("height"):
                    row["width"] = extra["width"]
                    row["height"] = extra["height"]
                if fs_size_kb in ("-", None):
                    fs_size_kb = f"{extra.get('size_kb', 0):,.1f} KB"
                if fs_mtime in ("-", None):
                    fs_mtime = extra.get("modified", "-")
            # <<< FIX

            # EXIF
            exif = self._read_exif(path)  # dict with safe keys

            # prefer DB date_taken, then EXIF, then fs modified
            date_taken = row.get("date_taken") or exif.get("Date Taken") or fs_mtime

            # --- Build HTML table (compact) ---
            def r(k, v):
                v = "-" if v in (None, "", []) else v
                return f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"


            db_rows = []
            for k in ("folder_id", "width", "height", "size_kb", "modified", "tags"):
                if k in row:
                    db_rows.append(r(k, row.get(k)))
            # >>> FIX (nice to have): show a humanized DB size if available
            try:
                sk = row.get("size_kb")
                if sk not in (None, "", "-"):
                    skf = float(sk)
                    if skf >= 1024:
                        db_rows.append(r("size_db_pretty", f"{skf/1024:,.2f} MB"))
                    else:
                        db_rows.append(r("size_db_pretty", f"{skf:,.1f} KB"))
            except Exception:
                pass

            exif_rows = []
            # EXIF field mapping: (English key in dict, translated label)
            exif_fields = [
                ("Camera", tr('metadata.field_camera')),
                ("Lens", tr('metadata.field_lens')),
                ("ISO", tr('metadata.field_iso')),
                ("Shutter", tr('metadata.field_shutter')),
                ("Aperture", tr('metadata.field_aperture')),
                ("Focal Length", tr('metadata.field_focal_length')),
                ("Orientation", tr('metadata.field_orientation')),
                ("Date Taken", tr('metadata.field_date_taken_exif'))
            ]
            for eng_key, label in exif_fields:
                if exif.get(eng_key) not in (None, "", []):
                    exif_rows.append(r(label, exif[eng_key]))

            # GPS with enhanced display and map preview
            gps_str = exif.get("GPS")
            if gps_str:
                lat, lon = self._parse_gps_coords(gps_str)
                if lat is not None and lon is not None:
                    # Create clickable map link
                    map_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=15"
                    gps_display = f"""
                        <div style='background: #f8f9fa; padding: 6px; border-radius: 4px; margin: 4px 0;'>
                            <div style='font-weight: 600; color: #0078d4;'>📍 {gps_str}</div>
                            <div style='font-size: 9pt; color: #666; margin-top: 2px;'>
                                <a href='{map_url}' style='color: #0078d4; text-decoration: none;'>🗺️ {tr('metadata.view_on_map')}</a>
                            </div>
                        </div>
                    """
                    exif_rows.append((tr('metadata.field_gps'), gps_display))

                    # Try to get location name (non-blocking)
                    location_name = self._get_location_name(lat, lon)
                    if location_name:
                        exif_rows.append(r(tr('metadata.field_location'), f"📌 {location_name}"))
                else:
                    exif_rows.append(r(tr('metadata.field_gps'), gps_str))

            # Convert exif_rows to HTML (handle tuples for GPS)
            exif_html_rows = []
            for item in exif_rows:
                if isinstance(item, tuple):
                    key, val = item
                    exif_html_rows.append(f"<tr><td><b>{key}</b></td><td>{val}</td></tr>")
                else:
                    exif_html_rows.append(item)

            fs_rows = [
                r(tr('metadata.field_file'), base),
                r(tr('metadata.field_path'), path),
                r(tr('metadata.field_size_fs'), fs_size_kb),
                r(tr('metadata.field_modified_fs'), fs_mtime),
    #            r("Date Taken (final)", date_taken),
                r(tr('metadata.field_dimensions'), f"{row.get('width','-')} × {row.get('height','-')}"),
                r(tr('metadata.field_date_taken'), date_taken),
            ]

            sections = []
            if db_rows:
                sections.append(f"<div class='hdr'>{tr('metadata.section_database')}</div><table>{''.join(db_rows)}</table>")
            if exif_rows:
                sections.append(f"<div class='hdr'>{tr('metadata.section_exif')}</div><table>{''.join(exif_rows)}</table>")
            sections.append(f"<div class='hdr'>{tr('metadata.section_file_info')}</div><table>{''.join(fs_rows)}</table>")

            html = f"""
                <div style='padding: 8px;'>
                    <div style='font-size: 12pt; font-weight: 700; color: #333; margin-bottom: 12px;
                                padding: 8px; background: #f0f0f0; border-radius: 6px;
                                border-left: 4px solid #0078d4;'>
                        🖼️ {base}
                    </div>
                    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                color: white; padding: 8px 12px; border-radius: 6px;
                                font-weight: 700; font-size: 11pt; margin-bottom: 8px;'>
                        {tr('metadata.section_database')}
                    </div>
                    <table style='width:100%; margin-bottom:12px;'>{''.join(db_rows)}</table>
                    <div style='background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                                color: white; padding: 8px 12px; border-radius: 6px;
                                font-weight: 700; font-size: 11pt; margin-bottom: 8px;'>
                        {tr('metadata.section_exif')}
                    </div>
                    <table style='width:100%; margin-bottom:12px;'>{''.join(exif_html_rows)}</table>
                    <div style='background: linear-gradient(135deg, #42e695 0%, #3bb2b8 100%);
                                color: white; padding: 8px 12px; border-radius: 6px;
                                font-weight: 700; font-size: 11pt; margin-bottom: 8px;'>
                        {tr('metadata.section_file_info')}
                    </div>
                    <table style='width:100%;'>{''.join(fs_rows)}</table>
                </div>
            """
            self.meta.setText(html)

            # Prepare plain text for copy
            self._last_plaintext = self._to_plaintext(base, row, fs_size_kb, fs_mtime, date_taken, exif)

    # ---------- Helpers ----------

    def _file_metadata_info(self, path: str) -> dict:
        """Return file size, modification time, and image dimensions safely."""
        info = {"size_kb": None, "width": None, "height": None, "modified": None}
        try:
            if not path or not os.path.exists(path):
                return info
            st = os.stat(path)
#            info["size_kb"] = round(st.st_size / 1024.0, 3)
#            info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))

            info["size_kb"] = round(st.st_size / 1024.0, 3)
            info["modified"] = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(st.st_mtime))
            # Use QImageReader for dimensions only (fast, no full decode)
            reader = QImageReader(path)
            sz = reader.size()
            if sz and sz.width() > 0 and sz.height() > 0:
                info["width"], info["height"] = sz.width(), sz.height()
        except Exception as e:
            print(f"[DetailsPanel] metadata info failed for {path}: {e}")
        return info

    def _copy_all(self):
        if not self._last_plaintext:
            return
        QApplication.clipboard().setText(self._last_plaintext)

    def _to_plaintext(self, base, row, fs_size_kb, fs_mtime, date_taken, exif):
        # minimal, readable copy
        lines = [f"File: {base}"]
        if row:
            lines.append("[Database]")
            for k in ("folder_id", "width", "height", "size_kb", "modified", "tags"):
                if k in row:
                    lines.append(f"  {k}: {row.get(k)}")
        if exif:
            lines.append("[EXIF]")
            for k in ("Camera", "Lens", "ISO", "Shutter", "Aperture",
                      "Focal Length", "Orientation", "Date Taken", "GPS"):
                v = exif.get(k)
                if v not in (None, "", []):
                    lines.append(f"  {k}: {v}")
        lines.append("[File]")
        lines.append(f"  Size (FS): {fs_size_kb}")
        lines.append(f"  Modified (FS): {fs_mtime}")
        lines.append(f"  Date Taken (final): {date_taken}")
        return "\n".join(lines)

    def _read_exif(self, path: str) -> dict:
        """
        Extract a curated set of EXIF fields. Safe for images without EXIF.
        Returns: {
          "Camera": "...", "Lens": "...", "ISO": 100,
          "Shutter": "1/125 s", "Aperture": "f/2.8",
          "Focal Length": "50 mm", "Orientation": "Rotate 90 CW",
          "Date Taken": "YYYY-MM-DD HH:MM:SS",
          "GPS": "12.3456, -98.7654"
        }
        """
        result = {}
        try:
            from PIL import Image, ExifTags
            # Try enabling HEIC/HEIF support if Pillow plugin is present.
            try:
                import pillow_heif  # noqa
                # pillow_heif.register_heif_opener()  # recent versions auto-register
            except Exception:
                pass

            with Image.open(path) as im:
                exif = im.getexif()
                if not exif:
                    return result
                # Build reverse tag map
                TAGS = ExifTags.TAGS
                # Simple fields
                def get_by_name(name):
                    for k, v in exif.items():
                        if TAGS.get(k) == name:
                            return v
                    return None

                make = get_by_name("Make")
                model = get_by_name("Model")
                lens = get_by_name("LensModel") or get_by_name("LensMake")
                iso = get_by_name("ISOSpeedRatings") or get_by_name("PhotographicSensitivity")
                dt = (get_by_name("DateTimeOriginal") or
                      get_by_name("DateTimeDigitized") or
                      get_by_name("DateTime"))
                orientation = get_by_name("Orientation")
                focal = get_by_name("FocalLength")
                fnum = get_by_name("FNumber")
                exposure = get_by_name("ExposureTime")

                # Camera
                cam = None
                if make and model:
                    cam = f"{str(make).strip()} {str(model).strip()}".strip()
                elif model:
                    cam = str(model).strip()
                elif make:
                    cam = str(make).strip()
                if cam:
                    result["Camera"] = cam
                if lens:
                    result["Lens"] = str(lens)

                # ISO
                if isinstance(iso, (list, tuple)) and iso:
                    iso = iso[0]
                if iso:
                    result["ISO"] = int(iso) if str(iso).isdigit() else str(iso)

                # Aperture
                if fnum:
                    result["Aperture"] = self._format_fnumber(fnum)

                # Focal length
                if focal:
                    result["Focal Length"] = self._format_rational_mm(focal)

                # Shutter
                if exposure:
                    result["Shutter"] = self._format_exposure(exposure)

                # Orientation (human text)
                if orientation:
                    orient_map = {
                        1: "Normal",
                        3: "Rotate 180",
                        6: "Rotate 90 CW",
                        8: "Rotate 90 CCW",
                    }
                    result["Orientation"] = orient_map.get(int(orientation), str(orientation))

                # Date Taken
                if dt:
                    result["Date Taken"] = str(dt).replace(":", "-", 2)  # "YYYY:MM:DD ..." -> "YYYY-MM-DD ..."

                # GPS
                gps_ifd = None
                for k, v in exif.items():
                    if TAGS.get(k) == "GPSInfo":
                        gps_ifd = v
                        break
                if gps_ifd:
                    gps = self._extract_gps(gps_ifd)
                    if gps:
                        result["GPS"] = gps
        except Exception:
            # Silent: keep panel resilient
            pass
        return result

    # ---------- EXIF format helpers ----------
    def _format_fnumber(self, fnum):
        # fnum can be Rational, (num, den), or float
        try:
            v = self._to_float(fnum)
            return f"f/{v:.1f}"
        except Exception:
            return str(fnum)

    def _format_rational_mm(self, value):
        try:
            v = self._to_float(value)
            return f"{v:.0f} mm"
        except Exception:
            return str(value)

    def _format_exposure(self, exp):
        """
        exp may be a float seconds (e.g., 0.008) or a fraction (num, den).
        Render as a nice '1/125 s' or '0.5 s'.
        """
        try:
            v = self._to_float(exp)
            if v <= 0:
                return str(exp)
            if v < 1:
                # show as 1/x
                denom = round(1.0 / v)
                # avoid things like 1/1
                if denom > 1:
                    return f"1/{denom} s"
            # >= 1s
            if v.is_integer():
                return f"{int(v)} s"
            return f"{v:.2f} s"
        except Exception:
            return str(exp)

    def _to_float(self, val):
        # Rational or tuple
        if isinstance(val, tuple) and len(val) == 2:
            num, den = val
            return float(num) / float(den) if den else float(num)
        # PIL may expose its own Rational type
        try:
            return float(val)
        except Exception:
            # Some EXIF types (e.g., IFDRational) have .numerator/.denominator
            num = getattr(val, "numerator", None)
            den = getattr(val, "denominator", None)
            if num is not None and den not in (None, 0):
                return float(num) / float(den)
            raise

    def _extract_gps(self, gps_ifd) -> str | None:
        """
        gps_ifd is a dict keyed by numeric GPS tags. Convert to "lat, lon".
        """
        try:
            from PIL.ExifTags import GPSTAGS
            # Map numeric keys to names
            gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
            lat = self._gps_to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
            lon = self._gps_to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
            if lat is not None and lon is not None:
                return f"{lat:.6f}, {lon:.6f}"
        except Exception:
            pass
        return None

    def _gps_to_deg(self, value, ref):
        """
        value: [(deg_num,deg_den),(min_num,min_den),(sec_num,sec_den)] or rationals
        ref: 'N'/'S' or 'E'/'W'
        """
        if not value or not ref:
            return None
        try:
            def rf(x):
                return self._to_float(x)
            d = rf(value[0]); m = rf(value[1]); s = rf(value[2])
            deg = d + (m / 60.0) + (s / 3600.0)
            if str(ref).upper() in ("S", "W"):
                deg = -deg
            return deg
        except Exception:
            return None

    def _parse_gps_coords(self, gps_str: str) -> tuple:
        """Parse GPS string 'lat, lon' to (lat, lon) floats."""
        try:
            if not gps_str or ',' not in gps_str:
                return None, None
            parts = gps_str.split(',')
            if len(parts) != 2:
                return None, None
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            return lat, lon
        except Exception:
            return None, None

    def _get_location_name(self, lat: float, lon: float, timeout: float = None) -> str | None:
        """Get human-readable location name from coordinates using Nominatim reverse geocoding."""
        try:
            # Check if reverse geocoding is enabled in settings
            from settings_manager_qt import SettingsManager
            sm = SettingsManager()
            if not sm.get("gps_reverse_geocoding_enabled", True):
                return None

            # Use timeout from settings if not provided
            if timeout is None:
                timeout = float(sm.get("gps_geocoding_timeout_sec", 2.0))

            # Check cache first if enabled
            cache_key = f"{lat:.6f},{lon:.6f}"
            if sm.get("gps_cache_location_names", True):
                from reference_db import ReferenceDB
                db = ReferenceDB()
                cached = db.get_cached_location_name(lat, lon)
                if cached:
                    return cached

            import urllib.request
            import urllib.parse
            import json

            # Nominatim API (OpenStreetMap)
            url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10"
            headers = {'User-Agent': 'MemoryMate-PhotoFlow/1.0'}

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode())

                # Try to build a nice location string
                addr = data.get('address', {})
                parts = []

                # City/Town/Village
                city = (addr.get('city') or addr.get('town') or
                       addr.get('village') or addr.get('municipality'))
                if city:
                    parts.append(city)

                # State/Province
                state = addr.get('state') or addr.get('province')
                if state and state != city:
                    parts.append(state)

                # Country
                country = addr.get('country')
                if country and len(parts) < 2:
                    parts.append(country)

                location_name = None
                if parts:
                    location_name = ', '.join(parts[:3])  # Max 3 components
                else:
                    # Fallback to display_name
                    location_name = data.get('display_name', '').split(',')[0]

                # Cache the result if enabled
                if location_name and sm.get("gps_cache_location_names", True):
                    try:
                        db.cache_location_name(lat, lon, location_name)
                    except Exception:
                        pass

                return location_name
        except Exception as e:
            # Silent fail - don't block metadata display
            print(f"[GPS] Reverse geocoding failed: {e}")
            return None

