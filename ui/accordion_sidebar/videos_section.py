# ui/accordion_sidebar/videos_section.py
# Videos section for accordion sidebar

import threading
import traceback
import logging
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QSizePolicy, QLabel
from PySide6.QtCore import Signal, Qt, QObject
from PySide6.QtGui import QColor
from translation_manager import tr
from utils.qt_role import role_set_json, role_get_json
from .base_section import BaseSection

logger = logging.getLogger(__name__)


class VideosSectionSignals(QObject):
    """Signals for videos section loading."""
    loaded = Signal(int, list)  # (generation, videos_list)
    error = Signal(int, str)    # (generation, error_message)


class VideosSection(BaseSection):
    """
    Videos section implementation.

    Displays video filtering options:
    - All Videos
    - By Duration (Short/Medium/Long)
    - By Quality (HD/4K)
    """

    videoFilterSelected = Signal(str)  # filter_type (e.g., "all", "short", "hd")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = VideosSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)
        self.signals.error.connect(self._on_error)
        self._loaded_project_id = None
        self._tree_built = False

    def get_section_id(self) -> str:
        return "videos"

    def get_title(self) -> str:
        return "Videos"

    def get_icon(self) -> str:
        return "🎬"

    def load_section(self) -> None:
        """Load videos from database in background thread."""
        if not self.project_id:
            logger.warning("[VideosSection] No project_id set")
            return

        if self._loaded_project_id == self.project_id and self._tree_built and not self._loading:
            logger.info("[VideosSection] Skipping reload, already current for project %s", self.project_id)
            return

        # Increment generation
        self._generation += 1
        current_gen = self._generation
        self._loading = True

        logger.info(f"[VideosSection] Loading videos (generation {current_gen})...")

        # Background worker
        def work():
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                logger.info(f"[VideosSection] Loaded {len(videos)} videos (gen {current_gen})")
                return videos
            except Exception as e:
                error_msg = f"Error loading videos: {e}"
                logger.error(f"[VideosSection] {error_msg}")
                traceback.print_exc()
                return []

        # Run in thread
        def on_complete():
            try:
                videos = work()
                self.signals.loaded.emit(current_gen, videos)
            except Exception as e:
                logger.error(f"[VideosSection] Error in worker thread: {e}")
                traceback.print_exc()
                self.signals.error.emit(current_gen, str(e))

        threading.Thread(target=on_complete, daemon=True).start()

    def create_content_widget(self, data):
        """Create videos tree widget."""
        videos = data or []  # List of video dictionaries from VideoService

        def _resolution_bucket(video: dict) -> str:
            width = video.get("width") or 0
            height = video.get("height") or 0
            resolution = max(width, height)
            if resolution >= 2160:
                return "4k"
            if resolution >= 1080:
                return "fhd"
            if resolution >= 720:
                return "hd"
            if resolution > 0:
                return "sd"
            return "unknown"

        def _size_bucket_mb(video: dict) -> str:
            size_mb = (video.get("size_kb") or 0) / 1024
            if size_mb <= 0:
                return "unknown"
            if size_mb < 100:
                return "small"
            if size_mb < 1024:
                return "medium"
            if size_mb < 5120:
                return "large"
            return "xlarge"

        def _year_for_video(video: dict) -> int | None:
            year = video.get("created_year")
            if year:
                return int(year)

            created_date = video.get("created_date") or ""
            if isinstance(created_date, str) and len(created_date) >= 4:
                try:
                    return int(created_date[:4])
                except ValueError:
                    pass

            date_taken = video.get("date_taken") or ""
            if isinstance(date_taken, str) and len(date_taken) >= 4:
                try:
                    return int(date_taken[:4])
                except ValueError:
                    pass
            return None

        # Create tree widget
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setIndentation(16)
        tree.setMinimumHeight(200)
        tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tree.setStyleSheet("""
            QTreeWidget {
                border: none;
                background: transparent;
            }
            QTreeWidget::item {
                padding: 4px;
            }
            QTreeWidget::item:hover {
                background: #f1f3f4;
            }
            QTreeWidget::item:selected {
                background: #e8f0fe;
                color: #1a73e8;
            }
        """)

        total_videos = len(videos)

        if total_videos == 0:
            # No videos - show message
            no_videos_item = QTreeWidgetItem([f"  ({tr('sidebar.loading')})"])
            no_videos_item.setForeground(0, QColor("#888888"))
            tree.addTopLevelItem(no_videos_item)
            return tree

        # All Videos
        all_item = QTreeWidgetItem([f"{tr('sidebar.all_videos')} ({total_videos})"])
        role_set_json(all_item, {"type": "all_videos"}, role=Qt.UserRole)
        tree.addTopLevelItem(all_item)

        # By Duration
        short_videos = [v for v in videos if v.get("duration_seconds") and v["duration_seconds"] < 30]
        medium_videos = [v for v in videos if v.get("duration_seconds") and 30 <= v["duration_seconds"] < 300]
        long_videos = [v for v in videos if v.get("duration_seconds") and v["duration_seconds"] >= 300]

        videos_with_duration = [v for v in videos if v.get("duration_seconds")]
        duration_parent = QTreeWidgetItem([f"⏱️ {tr('sidebar.by_duration')} ({len(videos_with_duration)})"])
        role_set_json(duration_parent, {"type": "duration_header"}, role=Qt.UserRole)
        tree.addTopLevelItem(duration_parent)

        short_item = QTreeWidgetItem([f"{tr('sidebar.duration_short')} ({len(short_videos)})"])
        role_set_json(short_item, {"type": "duration", "filter": "short"}, role=Qt.UserRole)
        duration_parent.addChild(short_item)

        medium_item = QTreeWidgetItem([f"{tr('sidebar.duration_medium')} ({len(medium_videos)})"])
        role_set_json(medium_item, {"type": "duration", "filter": "medium"}, role=Qt.UserRole)
        duration_parent.addChild(medium_item)

        long_item = QTreeWidgetItem([f"{tr('sidebar.duration_long')} ({len(long_videos)})"])
        role_set_json(long_item, {"type": "duration", "filter": "long"}, role=Qt.UserRole)
        duration_parent.addChild(long_item)

        # By Resolution (use max dimension height/width)
        resolution_buckets = {"sd": 0, "hd": 0, "fhd": 0, "4k": 0}
        videos_with_resolution = []
        for video in videos:
            bucket = _resolution_bucket(video)
            if bucket != "unknown":
                videos_with_resolution.append(video)
            if bucket in resolution_buckets:
                resolution_buckets[bucket] += 1

        resolution_parent = QTreeWidgetItem([f"📺 {tr('sidebar.by_resolution')} ({len(videos_with_resolution)})"])
        role_set_json(resolution_parent, {"type": "resolution_header"}, role=Qt.UserRole)
        tree.addTopLevelItem(resolution_parent)

        sd_item = QTreeWidgetItem([f"{tr('sidebar.resolution_sd')} ({resolution_buckets['sd']})"])
        role_set_json(sd_item, {"type": "resolution", "filter": "sd"}, role=Qt.UserRole)
        resolution_parent.addChild(sd_item)

        hd_item = QTreeWidgetItem([f"{tr('sidebar.resolution_hd')} ({resolution_buckets['hd']})"])
        role_set_json(hd_item, {"type": "resolution", "filter": "hd"}, role=Qt.UserRole)
        resolution_parent.addChild(hd_item)

        fhd_item = QTreeWidgetItem([f"{tr('sidebar.resolution_fhd')} ({resolution_buckets['fhd']})"])
        role_set_json(fhd_item, {"type": "resolution", "filter": "fhd"}, role=Qt.UserRole)
        resolution_parent.addChild(fhd_item)

        uhd_item = QTreeWidgetItem([f"{tr('sidebar.resolution_4k')} ({resolution_buckets['4k']})"])
        role_set_json(uhd_item, {"type": "resolution", "filter": "4k"}, role=Qt.UserRole)
        resolution_parent.addChild(uhd_item)

        # By Codec
        codec_counts = {
            "h264": 0,
            "hevc": 0,
            "vp9": 0,
            "av1": 0,
            "mpeg4": 0,
        }
        videos_with_codec = []
        for video in videos:
            codec = (video.get("codec") or "").lower()
            if codec:
                videos_with_codec.append(video)
            if codec in ["h264", "avc"]:
                codec_counts["h264"] += 1
            elif codec in ["hevc", "h265"]:
                codec_counts["hevc"] += 1
            elif codec == "vp9":
                codec_counts["vp9"] += 1
            elif codec == "av1":
                codec_counts["av1"] += 1
            elif codec in ["mpeg4", "xvid", "divx"]:
                codec_counts["mpeg4"] += 1

        codec_parent = QTreeWidgetItem([f"🎞️ By Codec ({len(videos_with_codec)})"])
        role_set_json(codec_parent, {"type": "codec_header"}, role=Qt.UserRole)
        tree.addTopLevelItem(codec_parent)

        h264_item = QTreeWidgetItem([f"H.264 / AVC ({codec_counts['h264']})"])
        role_set_json(h264_item, {"type": "codec", "filter": "h264"}, role=Qt.UserRole)
        codec_parent.addChild(h264_item)

        hevc_item = QTreeWidgetItem([f"H.265 / HEVC ({codec_counts['hevc']})"])
        role_set_json(hevc_item, {"type": "codec", "filter": "hevc"}, role=Qt.UserRole)
        codec_parent.addChild(hevc_item)

        vp9_item = QTreeWidgetItem([f"VP9 ({codec_counts['vp9']})"])
        role_set_json(vp9_item, {"type": "codec", "filter": "vp9"}, role=Qt.UserRole)
        codec_parent.addChild(vp9_item)

        av1_item = QTreeWidgetItem([f"AV1 ({codec_counts['av1']})"])
        role_set_json(av1_item, {"type": "codec", "filter": "av1"}, role=Qt.UserRole)
        codec_parent.addChild(av1_item)

        mpeg4_item = QTreeWidgetItem([f"MPEG-4 ({codec_counts['mpeg4']})"])
        role_set_json(mpeg4_item, {"type": "codec", "filter": "mpeg4"}, role=Qt.UserRole)
        codec_parent.addChild(mpeg4_item)

        # By File Size
        size_counts = {"small": 0, "medium": 0, "large": 0, "xlarge": 0}
        videos_with_size = []
        for video in videos:
            bucket = _size_bucket_mb(video)
            if bucket != "unknown":
                videos_with_size.append(video)
            if bucket in size_counts:
                size_counts[bucket] += 1

        size_parent = QTreeWidgetItem([f"📦 {tr('sidebar.by_size')} ({len(videos_with_size)})"])
        role_set_json(size_parent, {"type": "size_header"}, role=Qt.UserRole)
        tree.addTopLevelItem(size_parent)

        small_item = QTreeWidgetItem([f"{tr('sidebar.size_small')} ({size_counts['small']})"])
        role_set_json(small_item, {"type": "size", "filter": "small"}, role=Qt.UserRole)
        size_parent.addChild(small_item)

        medium_item = QTreeWidgetItem([f"{tr('sidebar.size_medium')} ({size_counts['medium']})"])
        role_set_json(medium_item, {"type": "size", "filter": "medium"}, role=Qt.UserRole)
        size_parent.addChild(medium_item)

        large_item = QTreeWidgetItem([f"{tr('sidebar.size_large')} ({size_counts['large']})"])
        role_set_json(large_item, {"type": "size", "filter": "large"}, role=Qt.UserRole)
        size_parent.addChild(large_item)

        xlarge_item = QTreeWidgetItem([f"{tr('sidebar.size_xlarge')} ({size_counts['xlarge']})"])
        role_set_json(xlarge_item, {"type": "size", "filter": "xlarge"}, role=Qt.UserRole)
        size_parent.addChild(xlarge_item)

        # By Date (years)
        year_counts: dict[int, int] = {}
        for video in videos:
            year = _year_for_video(video)
            if year:
                year_counts[year] = year_counts.get(year, 0) + 1

        if year_counts:
            date_parent = QTreeWidgetItem([f"📅 {tr('sidebar.by_date')} ({sum(year_counts.values())})"])
            role_set_json(date_parent, {"type": "date_header"}, role=Qt.UserRole)
            tree.addTopLevelItem(date_parent)

            for year in sorted(year_counts.keys(), reverse=True):
                year_item = QTreeWidgetItem([f"{year} ({year_counts[year]})"])
                role_set_json(year_item, {"type": "date", "filter": str(year)}, role=Qt.UserRole)
                date_parent.addChild(year_item)

        # Search shortcut
        search_item = QTreeWidgetItem([tr("sidebar.search_videos")])
        role_set_json(search_item, {"type": "search"}, role=Qt.UserRole)
        tree.addTopLevelItem(search_item)

        # Connect double-click to emit filter selection
        tree.itemDoubleClicked.connect(
            lambda item, col: self._on_item_double_clicked(item)
        )

        logger.info(f"[VideosSection] Tree built with {total_videos} videos")
        return tree

    def _on_item_double_clicked(self, item: QTreeWidgetItem):
        """Handle double-click on video filter item."""
        data = role_get_json(item, role=Qt.UserRole)
        if not data:
            return
        filter_type = data.get("type")
        if filter_type == "all_videos":
            self.videoFilterSelected.emit("all")
        elif filter_type in ["duration", "resolution", "codec", "size", "date"]:
            filter_value = data.get("filter", "")
            if filter_value:
                self.videoFilterSelected.emit(f"{filter_type}:{filter_value}")
        elif filter_type == "search":
            self.videoFilterSelected.emit("search")

    def _on_data_loaded(self, generation: int, videos: list):
        """Callback when videos data is loaded."""
        self._loading = False
        if generation != self._generation:
            logger.debug(f"[VideosSection] Discarding stale data (gen {generation} vs {self._generation})")
            return
        self._loaded_project_id = self.project_id
        self._tree_built = True
        logger.info(f"[VideosSection] Data loaded successfully (gen {generation}, {len(videos)} videos)")

    def _on_error(self, generation: int, error_msg: str):
        """Callback when videos loading fails."""
        self._loading = False
        if generation != self._generation:
            return
        logger.error(f"[VideosSection] Load failed: {error_msg}")
