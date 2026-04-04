# services/video_service.py
# Version 1.0.0 dated 2025-11-09
# Business logic layer for video operations

from typing import Optional, List, Dict, Any
from pathlib import Path
from logging_config import get_logger

logger = get_logger(__name__)


class VideoService:
    """
    Business logic layer for video operations (Schema v3.2.0).

    Coordinates between VideoRepository (data access), VideoMetadataService (metadata extraction),
    and VideoThumbnailService (thumbnail generation).

    This service provides high-level video operations with business logic,
    error handling, and coordination between multiple services.
    """

    def __init__(self):
        """Initialize VideoService with repository and helper services."""
        from repository.video_repository import VideoRepository

        self._video_repo = VideoRepository()
        self.logger = logger

    # ========================================================================
    # VIDEO CRUD OPERATIONS
    # ========================================================================

    def get_video_by_path(self, path: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get video metadata by file path.

        Args:
            path: Video file path
            project_id: Project ID

        Returns:
            Video metadata dict, or None if not found

        Example:
            >>> service.get_video_by_path("/videos/clip.mp4", project_id=1)
            {'id': 1, 'path': '/videos/clip.mp4', 'duration_seconds': 45.2, ...}
        """
        try:
            return self._video_repo.get_by_path(path, project_id)
        except Exception as e:
            self.logger.error(f"Failed to get video by path {path}: {e}")
            return None

    def get_videos_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos in a project.

        Args:
            project_id: Project ID

        Returns:
            List of video metadata dicts

        Example:
            >>> service.get_videos_by_project(project_id=1)
            [{'id': 1, 'path': '/vid1.mp4', ...}, {'id': 2, 'path': '/vid2.mp4', ...}]
        """
        try:
            return self._video_repo.get_by_project(project_id)
        except Exception as e:
            self.logger.error(f"Failed to get videos for project {project_id}: {e}")
            return []

    def get_videos_by_folder(self, folder_id: int, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos in a folder.

        Args:
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            List of video metadata dicts

        Example:
            >>> service.get_videos_by_folder(folder_id=5, project_id=1)
            [{'id': 1, 'path': '/videos/clip.mp4', ...}]
        """
        try:
            return self._video_repo.get_by_folder(folder_id, project_id)
        except Exception as e:
            self.logger.error(f"Failed to get videos for folder {folder_id}: {e}")
            return []

    def create_video(self, path: str, folder_id: int, project_id: int, **metadata) -> Optional[int]:
        """
        Create a new video metadata entry.

        Args:
            path: Video file path
            folder_id: Folder ID
            project_id: Project ID
            **metadata: Optional metadata fields

        Returns:
            Video ID, or None if creation failed

        Example:
            >>> service.create_video("/videos/clip.mp4", folder_id=5, project_id=1,
            ...                      size_kb=102400, duration_seconds=45.2)
            123
        """
        try:
            video_id = self._video_repo.create(path, folder_id, project_id, **metadata)
            self.logger.info(f"Created video {path} (id={video_id})")
            return video_id
        except Exception as e:
            self.logger.error(f"Failed to create video {path}: {e}")
            return None

    def update_video(self, video_id: int, **metadata) -> bool:
        """
        Update video metadata fields.

        Args:
            video_id: Video ID
            **metadata: Fields to update

        Returns:
            True if updated, False if failed

        Example:
            >>> service.update_video(123, duration_seconds=45.2, fps=30.0)
            True
        """
        try:
            success = self._video_repo.update(video_id, **metadata)
            if success:
                self.logger.info(f"Updated video {video_id}: {list(metadata.keys())}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to update video {video_id}: {e}")
            return False

    def delete_video(self, video_id: int) -> bool:
        """
        Delete a video (CASCADE removes associations).

        Args:
            video_id: Video ID

        Returns:
            True if deleted, False if failed

        Example:
            >>> service.delete_video(123)
            True
        """
        try:
            success = self._video_repo.delete(video_id)
            if success:
                self.logger.info(f"Deleted video {video_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to delete video {video_id}: {e}")
            return False

    def index_video(self, path: str, project_id: int, folder_id: int = None,
                   size_kb: float = None, modified: str = None,
                   created_ts: int = None, created_date: str = None,
                   created_year: int = None) -> Optional[int]:
        """
        Index a video file during scanning.

        Creates a video metadata entry with 'pending' status for background processing.
        This method is called by PhotoScanService during repository scans.

        Args:
            path: Video file path
            project_id: Project ID
            folder_id: Folder ID (optional)
            size_kb: File size in KB (optional)
            modified: Modified timestamp (optional)
            created_ts: Created timestamp (optional, for immediate date hierarchy)
            created_date: Created date YYYY-MM-DD (optional, for immediate date hierarchy)
            created_year: Created year (optional, for immediate date hierarchy)

        Returns:
            Video ID, or None if indexing failed

        Example:
            >>> service.index_video("/videos/clip.mp4", project_id=1, folder_id=5,
            ...                     size_kb=102400, modified="2025-01-01 12:00:00",
            ...                     created_ts=1735689600, created_date="2025-01-01",
            ...                     created_year=2025)
            123

        Note:
            created_* fields are populated from file modified date during scan for immediate
            sidebar display. Background workers will UPDATE these with proper date_taken
            extracted from video metadata.
        """
        try:
            # Check if video already exists
            existing = self.get_video_by_path(path, project_id)
            if existing:
                self.logger.debug(f"Video already indexed: {path}")
                return existing.get('id')

            # Create new video entry with pending status AND date fields
            video_id = self._video_repo.create(
                path=path,
                folder_id=folder_id,
                project_id=project_id,
                size_kb=size_kb,
                modified=modified,
                created_ts=created_ts,
                created_date=created_date,
                created_year=created_year,
                metadata_status='pending',
                thumbnail_status='pending'
            )

            if video_id:
                self.logger.info(f"Indexed video {path} (id={video_id}, status=pending)")
            return video_id

        except Exception as e:
            self.logger.error(f"Failed to index video {path}: {e}")
            return None

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def bulk_create_videos(self, video_paths: List[str], folder_id: int, project_id: int) -> int:
        """
        Bulk create video metadata entries.

        Args:
            video_paths: List of video file paths
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            Number of videos created

        Example:
            >>> paths = ['/vid1.mp4', '/vid2.mp4', '/vid3.mp4']
            >>> service.bulk_create_videos(paths, folder_id=5, project_id=1)
            3
        """
        if not video_paths:
            return 0

        rows = []
        for path in video_paths:
            # Check if file exists
            if not Path(path).exists():
                self.logger.warning(f"Video file not found: {path}")
                continue

            # Get file size
            try:
                size_kb = Path(path).stat().st_size / 1024
            except Exception as e:
                self.logger.warning(f"Failed to get size for {path}: {e}")
                size_kb = None

            rows.append({
                'path': path,
                'folder_id': folder_id,
                'size_kb': size_kb,
                'metadata_status': 'pending',
                'thumbnail_status': 'pending'
            })

        try:
            count = self._video_repo.bulk_upsert(rows, project_id)
            self.logger.info(f"Bulk created {count} videos for project {project_id}")
            return count
        except Exception as e:
            self.logger.error(f"Failed to bulk create videos: {e}")
            return 0

    def get_unprocessed_videos(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get videos that need metadata extraction.

        Args:
            limit: Maximum number of videos to return

        Returns:
            List of video metadata dicts with pending status

        Example:
            >>> service.get_unprocessed_videos(limit=50)
            [{'id': 1, 'path': '/vid1.mp4', 'metadata_status': 'pending', ...}]
        """
        try:
            return self._video_repo.get_unprocessed_videos(limit)
        except Exception as e:
            self.logger.error(f"Failed to get unprocessed videos: {e}")
            return []

    # ========================================================================
    # PROJECT-VIDEO ASSOCIATIONS
    # ========================================================================

    def add_to_branch(self, project_id: int, branch_key: str, video_path: str, label: str = None) -> bool:
        """
        Add video to a project branch.

        Args:
            project_id: Project ID
            branch_key: Branch key (e.g., 'all', date, folder name)
            video_path: Video file path
            label: Optional label

        Returns:
            True if added, False if already exists

        Example:
            >>> service.add_to_branch(project_id=1, branch_key='all', video_path='/vid1.mp4')
            True
        """
        try:
            success = self._video_repo.add_to_project_branch(project_id, branch_key, video_path, label)
            if success:
                self.logger.debug(f"Added video to branch {project_id}/{branch_key}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to add video to branch: {e}")
            return False

    def get_videos_by_branch(self, project_id: int, branch_key: str) -> List[str]:
        """
        Get all video paths in a project branch.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            List of video file paths

        Example:
            >>> service.get_videos_by_branch(project_id=1, branch_key='all')
            ['/vid1.mp4', '/vid2.mp4', '/vid3.mp4']
        """
        try:
            return self._video_repo.get_videos_by_branch(project_id, branch_key)
        except Exception as e:
            self.logger.error(f"Failed to get videos for branch {project_id}/{branch_key}: {e}")
            return []

    # ========================================================================
    # VIDEO TAGGING
    # ========================================================================

    def add_tag_to_video(self, video_id: int, tag_id: int) -> bool:
        """
        Add a tag to a video.

        Args:
            video_id: Video ID
            tag_id: Tag ID

        Returns:
            True if added, False if already existed

        Example:
            >>> service.add_tag_to_video(video_id=123, tag_id=5)
            True
        """
        try:
            success = self._video_repo.add_tag(video_id, tag_id)
            if success:
                self.logger.info(f"Tagged video {video_id} with tag {tag_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to tag video {video_id}: {e}")
            return False

    def remove_tag_from_video(self, video_id: int, tag_id: int) -> bool:
        """
        Remove a tag from a video.

        Args:
            video_id: Video ID
            tag_id: Tag ID

        Returns:
            True if removed, False if didn't exist

        Example:
            >>> service.remove_tag_from_video(video_id=123, tag_id=5)
            True
        """
        try:
            success = self._video_repo.remove_tag(video_id, tag_id)
            if success:
                self.logger.info(f"Removed tag {tag_id} from video {video_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to remove tag from video {video_id}: {e}")
            return False

    def get_tags_for_video(self, video_id: int) -> List[Dict[str, Any]]:
        """
        Get all tags for a video.

        Args:
            video_id: Video ID

        Returns:
            List of tag dicts with 'id' and 'name'

        Example:
            >>> service.get_tags_for_video(video_id=123)
            [{'id': 1, 'name': 'vacation'}, {'id': 2, 'name': 'family'}]
        """
        try:
            return self._video_repo.get_tags_for_video(video_id)
        except Exception as e:
            self.logger.error(f"Failed to get tags for video {video_id}: {e}")
            return []

    def get_videos_by_tag(self, tag_id: int) -> List[int]:
        """
        Get all video IDs that have a specific tag.

        Args:
            tag_id: Tag ID

        Returns:
            List of video IDs

        Example:
            >>> service.get_videos_by_tag(tag_id=5)
            [123, 124, 125]
        """
        try:
            return self._video_repo.get_videos_by_tag(tag_id)
        except Exception as e:
            self.logger.error(f"Failed to get videos for tag {tag_id}: {e}")
            return []

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def is_video_file(self, path: str) -> bool:
        """
        Check if a file is a supported video format.

        Args:
            path: File path

        Returns:
            True if file is a supported video format

        Example:
            >>> service.is_video_file('/videos/clip.mp4')
            True
            >>> service.is_video_file('/photos/image.jpg')
            False
        """
        VIDEO_EXTENSIONS = {
            '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm',
            '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv'
        }

        ext = Path(path).suffix.lower()
        return ext in VIDEO_EXTENSIONS

    def get_video_info(self, video_id: int) -> Optional[Dict[str, Any]]:
        """
        Get complete video information including metadata and tags.

        Args:
            video_id: Video ID

        Returns:
            Dict with video metadata and tags, or None if not found

        Example:
            >>> service.get_video_info(video_id=123)
            {
                'id': 123,
                'path': '/vid1.mp4',
                'duration_seconds': 45.2,
                'width': 1920,
                'height': 1080,
                'tags': [{'id': 1, 'name': 'vacation'}]
            }
        """
        try:
            # Get video metadata (we need project_id, but we can get it from the video record)
            # For now, we'll need to modify this to work properly
            # This is a simplified version

            # We'll need to update this method once we have a way to get video by ID
            # For now, return None
            self.logger.warning("get_video_info not fully implemented yet")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get video info for {video_id}: {e}")
            return None

    # ========================================================================
    # VIDEO FILTERING & SEARCH (Option 3)
    # ========================================================================

    def filter_by_duration(self, videos: List[Dict[str, Any]],
                          min_seconds: float = None,
                          max_seconds: float = None) -> List[Dict[str, Any]]:
        """
        Filter videos by duration range.

        Args:
            videos: List of video metadata dicts
            min_seconds: Minimum duration in seconds (None = no minimum)
            max_seconds: Maximum duration in seconds (None = no maximum)

        Returns:
            Filtered list of videos

        Example:
            >>> # Get short videos (< 30 seconds)
            >>> videos = service.get_videos_by_project(1)
            >>> short = service.filter_by_duration(videos, max_seconds=30)

            >>> # Get long videos (> 5 minutes)
            >>> long = service.filter_by_duration(videos, min_seconds=300)
        """
        filtered = []
        for video in videos:
            duration = video.get('duration_seconds')
            if duration is None:
                continue

            if min_seconds is not None and duration < min_seconds:
                continue
            if max_seconds is not None and duration > max_seconds:
                continue

            filtered.append(video)

        return filtered

    def filter_by_resolution(self, videos: List[Dict[str, Any]],
                           min_width: int = None,
                           min_height: int = None,
                           quality: str = None) -> List[Dict[str, Any]]:
        """
        Filter videos by resolution.

        Args:
            videos: List of video metadata dicts
            min_width: Minimum width in pixels (None = no minimum)
            min_height: Minimum height in pixels (None = no minimum)
            quality: Quality preset: 'sd' (480p), 'hd' (720p), 'fhd' (1080p), '4k' (2160p)

        Returns:
            Filtered list of videos

        Example:
            >>> # Get HD videos (720p+)
            >>> videos = service.get_videos_by_project(1)
            >>> hd = service.filter_by_resolution(videos, quality='hd')

            >>> # Get 4K videos
            >>> uhd = service.filter_by_resolution(videos, quality='4k')
        """
        # Quality presets
        presets = {
            'sd': (640, 480),
            'hd': (1280, 720),
            'fhd': (1920, 1080),
            '4k': (3840, 2160)
        }

        if quality and quality.lower() in presets:
            min_width, min_height = presets[quality.lower()]

        filtered = []
        for video in videos:
            width = video.get('width')
            height = video.get('height')

            if width is None or height is None:
                continue

            if min_width is not None and width < min_width:
                continue
            if min_height is not None and height < min_height:
                continue

            filtered.append(video)

        return filtered

    def search_videos(self, videos: List[Dict[str, Any]],
                     query: str,
                     search_path: bool = True,
                     search_tags: bool = True) -> List[Dict[str, Any]]:
        """
        Search videos by filename or tags.

        Args:
            videos: List of video metadata dicts
            query: Search query (case-insensitive)
            search_path: Search in file paths
            search_tags: Search in tags (if available)

        Returns:
            Filtered list of videos matching query

        Example:
            >>> videos = service.get_videos_by_project(1)
            >>> vacation = service.search_videos(videos, "vacation")
            >>> birthday = service.search_videos(videos, "birthday")
        """
        if not query:
            return videos

        query_lower = query.lower()
        filtered = []

        for video in videos:
            matched = False

            # Search in path
            if search_path:
                path = video.get('path', '')
                if query_lower in path.lower():
                    matched = True

            # Search in tags (if available)
            if search_tags and not matched:
                tags = video.get('tags', [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(',')]
                for tag in tags:
                    if query_lower in tag.lower():
                        matched = True
                        break

            if matched:
                filtered.append(video)

        return filtered

    # ========================================================================
    # ADVANCED FILTERING (Option 7)
    # ========================================================================

    def filter_by_codec(self, videos: List[Dict[str, Any]],
                       codecs: List[str] = None) -> List[Dict[str, Any]]:
        """
        Filter videos by codec.

        Args:
            videos: List of video metadata dicts
            codecs: List of codec names (case-insensitive, e.g., ['h264', 'hevc', 'vp9'])
                   None returns all videos with codec metadata

        Returns:
            Filtered list of videos

        Example:
            >>> # Get H.264 and H.265 videos
            >>> videos = service.get_videos_by_project(1)
            >>> h26x = service.filter_by_codec(videos, codecs=['h264', 'hevc'])

            >>> # Get VP9 videos
            >>> vp9 = service.filter_by_codec(videos, codecs=['vp9'])
        """
        if codecs is None:
            # Return all videos that have codec metadata
            return [v for v in videos if v.get('codec')]

        # Normalize codec names to lowercase
        codecs_lower = [c.lower() for c in codecs]

        filtered = []
        for video in videos:
            codec = video.get('codec')
            if codec is None:
                continue

            # Match codec (case-insensitive)
            if codec.lower() in codecs_lower:
                filtered.append(video)

        return filtered

    def filter_by_file_size(self, videos: List[Dict[str, Any]],
                           min_mb: float = None,
                           max_mb: float = None,
                           size_range: str = None) -> List[Dict[str, Any]]:
        """
        Filter videos by file size.

        Args:
            videos: List of video metadata dicts
            min_mb: Minimum file size in MB (None = no minimum)
            max_mb: Maximum file size in MB (None = no maximum)
            size_range: Size range preset: 'small' (<100MB), 'medium' (100MB-1GB),
                       'large' (1GB-5GB), 'xlarge' (>5GB)

        Returns:
            Filtered list of videos

        Example:
            >>> # Get small files (<100MB)
            >>> videos = service.get_videos_by_project(1)
            >>> small = service.filter_by_file_size(videos, size_range='small')

            >>> # Get files between 500MB and 2GB
            >>> mid = service.filter_by_file_size(videos, min_mb=500, max_mb=2000)
        """
        # Size range presets
        presets = {
            'small': (None, 100),      # < 100MB
            'medium': (100, 1024),     # 100MB - 1GB
            'large': (1024, 5120),     # 1GB - 5GB
            'xlarge': (5120, None)     # > 5GB
        }

        if size_range and size_range.lower() in presets:
            min_mb, max_mb = presets[size_range.lower()]

        filtered = []
        for video in videos:
            size_kb = video.get('size_kb')
            if size_kb is None:
                continue

            # Convert to float if string (defensive programming)
            try:
                size_kb = float(size_kb) if isinstance(size_kb, str) else size_kb
                size_mb = size_kb / 1024
            except (ValueError, TypeError):
                # Skip videos with invalid size data
                continue

            if min_mb is not None and size_mb < min_mb:
                continue
            if max_mb is not None and size_mb > max_mb:
                continue

            filtered.append(video)

        # 🐞 DEBUG: Log filter results  
        filter_desc = size_range if size_range else f"{min_mb}-{max_mb}MB"
        self.logger.debug(f"File size filter '{filter_desc}': {len(filtered)}/{len(videos)} videos")
        return filtered

    def filter_by_duration_key(self, videos: List[Dict[str, Any]], duration_key: str) -> List[Dict[str, Any]]:
        """
        Filter videos by duration range key.

        Args:
            videos: List of video metadata dicts
            duration_key: Duration range key ('short', 'medium', 'long')

        Returns:
            Filtered list of videos
        """
        # 🐞 FIX: Duration ranges MUST match sidebar counting logic (sidebar_qt.py lines 2907-2909)
        # Short: < 30s
        # Medium: 30s - 5min (300s)
        # Long: >= 5min (300s)
        ranges = {
            'short': (0, 30),           # 0-30 seconds
            'medium': (30, 300),        # 30s - 5min
            'long': (300, None)         # 5min+
        }

        if duration_key not in ranges:
            self.logger.warning(f"Unknown duration key: {duration_key}")
            return videos

        min_duration, max_duration = ranges[duration_key]

        filtered = []
        for video in videos:
            # 🐞 FIX: Field name is 'duration_seconds' not 'duration_sec'
            duration = video.get('duration_seconds')
            if duration is None:
                continue

            if min_duration is not None and duration < min_duration:
                continue
            # P2-19 FIX: Changed >= to > for correct boundary handling
            # Videos at exact max_duration should be INCLUDED, not excluded
            if max_duration is not None and duration > max_duration:
                continue

            filtered.append(video)

        # 🐞 DEBUG: Log filter results
        self.logger.debug(f"Duration filter '{duration_key}': {len(filtered)}/{len(videos)} videos (range: {min_duration}-{max_duration}s)")
        return filtered

    def filter_by_resolution_key(self, videos: List[Dict[str, Any]], resolution_key: str) -> List[Dict[str, Any]]:
        """
        Filter videos by resolution range key.

        Uses max(width, height) to handle both portrait and landscape videos correctly.
        This matches the sidebar bucketing logic in videos_section.py.

        Args:
            videos: List of video metadata dicts
            resolution_key: Resolution key ('sd', 'hd', 'fhd', '4k', '8k')

        Returns:
            Filtered list of videos
        """
        # Resolution definitions (max dimension in pixels)
        # Uses max(width, height) to handle portrait/landscape videos
        resolutions = {
            'sd': (0, 720),         # < 720p
            'hd': (720, 1080),      # 720p - 1079p
            'fhd': (1080, 2160),    # 1080p - 2159p (Full HD)
            '4k': (2160, 4320),     # 4K (2160p)
            '8k': (4320, None)      # 8K+ (4320p+)
        }

        if resolution_key not in resolutions:
            self.logger.warning(f"Unknown resolution key: {resolution_key}")
            return videos

        min_res, max_res = resolutions[resolution_key]

        filtered = []
        for video in videos:
            width = video.get('width') or 0
            height = video.get('height') or 0
            # Use max dimension to handle portrait/landscape videos
            resolution = max(width, height)

            if resolution <= 0:
                continue

            if min_res is not None and resolution < min_res:
                continue
            if max_res is not None and resolution >= max_res:
                continue

            filtered.append(video)

        # 🐞 DEBUG: Log filter results
        self.logger.debug(f"Resolution filter '{resolution_key}': {len(filtered)}/{len(videos)} videos (max_dim: {min_res}-{max_res}px)")
        return filtered

    def filter_by_codec_key(self, videos: List[Dict[str, Any]], codec_key: str) -> List[Dict[str, Any]]:
        """
        Filter videos by codec.

        Args:
            videos: List of video metadata dicts
            codec_key: Codec name (e.g., 'h264', 'hevc', 'vp9', 'av1', 'mpeg4')

        Returns:
            Filtered list of videos
        """
        # 🐞 FIX: Normalize codec names and use correct field 'codec' (not 'video_codec')
        codec_key = codec_key.lower()

        # 🐞 FIX: Match sidebar counting logic (sidebar_qt.py lines 3025-3029)
        # h264: ['h264', 'avc']
        # hevc: ['hevc', 'h265']
        # vp9: ['vp9']
        # av1: ['av1']
        # mpeg4: ['mpeg4', 'xvid', 'divx']
        
        filtered = []
        for video in videos:
            # 🐞 FIX: Field name is 'codec' not 'video_codec'
            video_codec = video.get('codec', '').lower()
            
            if not video_codec:
                continue

            # Match based on codec_key
            if codec_key == 'h264':
                if video_codec in ['h264', 'avc']:
                    filtered.append(video)
            elif codec_key == 'hevc':
                if video_codec in ['hevc', 'h265']:
                    filtered.append(video)
            elif codec_key == 'vp9':
                if video_codec == 'vp9':
                    filtered.append(video)
            elif codec_key == 'av1':
                if video_codec == 'av1':
                    filtered.append(video)
            elif codec_key == 'mpeg4':
                if video_codec in ['mpeg4', 'xvid', 'divx']:
                    filtered.append(video)

        # 🐞 DEBUG: Log filter results
        self.logger.debug(f"Codec filter '{codec_key}': {len(filtered)}/{len(videos)} videos")
        return filtered

    def filter_by_date(self, videos: List[Dict[str, Any]],
                      start_date: str = None,
                      end_date: str = None,
                      year: int = None,
                      month: int = None,
                      use_modified: bool = False) -> List[Dict[str, Any]]:
        """
        Filter videos by date taken or modified.

        Args:
            videos: List of video metadata dicts
            start_date: Start date (YYYY-MM-DD format, inclusive)
            end_date: End date (YYYY-MM-DD format, inclusive)
            year: Year filter (shortcut for start_date=YYYY-01-01, end_date=YYYY-12-31)
            month: Month filter (1-12) - requires year parameter
            use_modified: Use modified date instead of date_taken (default: False)

        Returns:
            Filtered list of videos

        Example:
            >>> # Get videos from 2024
            >>> videos = service.get_videos_by_project(1)
            >>> y2024 = service.filter_by_date(videos, year=2024)

            >>> # Get videos from November 2024
            >>> nov2024 = service.filter_by_date(videos, year=2024, month=11)

            >>> # Get videos from Jan-Mar 2024
            >>> q1 = service.filter_by_date(videos, start_date='2024-01-01', end_date='2024-03-31')
        """
        from datetime import datetime
        import calendar

        # Month filter shortcut (requires year)
        if month is not None:
            if year is None:
                self.logger.warning("Month filter requires year parameter, ignoring month")
            else:
                # Calculate first and last day of month
                start_date = f"{year}-{month:02d}-01"
                last_day = calendar.monthrange(year, month)[1]
                end_date = f"{year}-{month:02d}-{last_day:02d}"

        # Year shortcut
        elif year is not None:
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"

        # Parse dates
        start_dt = None
        end_dt = None

        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            except ValueError:
                self.logger.warning(f"Invalid start_date format: {start_date}")

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            except ValueError:
                self.logger.warning(f"Invalid end_date format: {end_date}")

        # 🐞 BUG FIX: Use created_date (populated during scan) as fallback for date_taken (populated by background workers)
        # This ensures date filtering works immediately after scan without waiting for metadata extraction
        date_field = 'modified' if use_modified else 'date_taken'
        fallback_field = 'created_date'  # Always populated during scan

        filtered = []
        for video in videos:
            # 🐞 BUG FIX: Try primary field first, fallback to created_date if not available
            date_str = video.get(date_field) or video.get(fallback_field)
            if not date_str:
                # Skip videos with no date information at all
                continue

            try:
                # Parse date (handle both YYYY-MM-DD and YYYY-MM-DD HH:MM:SS formats)
                date_only = date_str.split(' ')[0]  # Extract YYYY-MM-DD part
                video_dt = datetime.strptime(date_only, '%Y-%m-%d')

                # Apply filters
                if start_dt and video_dt < start_dt:
                    continue
                if end_dt and video_dt > end_dt:
                    continue

                filtered.append(video)
            except (ValueError, IndexError):
                self.logger.debug(f"Failed to parse date {date_str} for video {video.get('path')}")

        return filtered

    def filter_combined(self, videos: List[Dict[str, Any]],
                       filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Apply multiple filters in combination.

        Args:
            videos: List of video metadata dicts
            filters: Dict of filter criteria with keys:
                    - duration_min: Minimum duration in seconds
                    - duration_max: Maximum duration in seconds
                    - resolution_quality: Quality preset ('sd', 'hd', 'fhd', '4k')
                    - codecs: List of codec names
                    - size_min_mb: Minimum file size in MB
                    - size_max_mb: Maximum file size in MB
                    - start_date: Start date (YYYY-MM-DD)
                    - end_date: End date (YYYY-MM-DD)
                    - year: Year filter
                    - query: Search query for path/tags

        Returns:
            Filtered list of videos matching all criteria

        Example:
            >>> # Get HD H.264 videos from 2024 longer than 5 minutes
            >>> videos = service.get_videos_by_project(1)
            >>> filtered = service.filter_combined(videos, {
            ...     'resolution_quality': 'hd',
            ...     'codecs': ['h264'],
            ...     'year': 2024,
            ...     'duration_min': 300
            ... })
        """
        result = videos

        # Apply duration filter
        if 'duration_min' in filters or 'duration_max' in filters:
            result = self.filter_by_duration(
                result,
                min_seconds=filters.get('duration_min'),
                max_seconds=filters.get('duration_max')
            )

        # Apply resolution filter
        if 'resolution_quality' in filters:
            result = self.filter_by_resolution(
                result,
                quality=filters.get('resolution_quality')
            )

        # Apply codec filter
        if 'codecs' in filters:
            result = self.filter_by_codec(
                result,
                codecs=filters.get('codecs')
            )

        # Apply file size filter
        if 'size_min_mb' in filters or 'size_max_mb' in filters or 'size_range' in filters:
            result = self.filter_by_file_size(
                result,
                min_mb=filters.get('size_min_mb'),
                max_mb=filters.get('size_max_mb'),
                size_range=filters.get('size_range')
            )

        # Apply date filter
        if 'start_date' in filters or 'end_date' in filters or 'year' in filters:
            result = self.filter_by_date(
                result,
                start_date=filters.get('start_date'),
                end_date=filters.get('end_date'),
                year=filters.get('year'),
                use_modified=filters.get('use_modified', False)
            )

        # Apply search query
        if 'query' in filters:
            result = self.search_videos(
                result,
                query=filters.get('query')
            )

        return result


# ========================================================================
# SINGLETON PATTERN
# ========================================================================

_video_service_instance = None


def get_video_service() -> VideoService:
    """
    Get singleton VideoService instance.

    Returns:
        VideoService instance

    Example:
        >>> from services.video_service import get_video_service
        >>> video_service = get_video_service()
        >>> videos = video_service.get_videos_by_project(project_id=1)
    """
    global _video_service_instance
    if _video_service_instance is None:
        _video_service_instance = VideoService()
    return _video_service_instance
