# services/photo_scan_service.py
# Version 10.01.01.04 dated 20260127
# Photo scanning service - Uses MetadataService for extraction

import os
import platform
import time
import sys
import shutil
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Dict, Any, Set
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

from repository import PhotoRepository, FolderRepository, ProjectRepository, DatabaseConnection
from logging_config import get_logger
from .metadata_service import MetadataService

logger = get_logger(__name__)


@dataclass
class ScanResult:
    """Results from a photo repository scan."""
    folders_found: int
    photos_indexed: int
    photos_skipped: int
    photos_failed: int
    videos_indexed: int  # 🎬 NEW: video count
    duration_seconds: float
    interrupted: bool = False
    # Phase 3B: Duplicate detection stats
    duplicates_detected: int = 0
    exact_duplicates: int = 0
    similar_stacks: int = 0


@dataclass
class ScanProgress:
    """Progress information during scanning."""
    current: int
    total: int
    percent: int
    message: str
    current_file: Optional[str] = None


class PhotoScanService:
    """
    Service for scanning photo repositories and indexing metadata.

    Responsibilities:
    - File system traversal with ignore patterns
    - Basic metadata extraction (size, dimensions, EXIF date)
    - Folder hierarchy management
    - Batched database writes
    - Progress reporting
    - Cancellation support
    - Incremental scanning (skip unchanged files)

    Does NOT handle:
    - Advanced EXIF parsing (use MetadataService)
    - Thumbnail generation (use ThumbnailService)
    - Face detection (separate service)

    Metadata Extraction Approach:
    - Uses MetadataService.extract_basic_metadata() for ALL photos (BUG FIX #8)
    - This avoids hangs from corrupted/malformed images
    - created_ts/created_date/created_year are computed inline from date_taken
    - Consistent across entire service - do not mix with extract_metadata()
    """

    # Supported image extensions
    # Common formats
    IMAGE_EXTENSIONS = {
        # JPEG family
        '.jpg', '.jpeg', '.jpe', '.jfif',
        # PNG
        '.png',
        # WEBP
        '.webp',
        # TIFF
        '.tif', '.tiff',
        # HEIF/HEIC (Apple/modern)
        '.heic', '.heif',  # ✅ iPhone photos, Live Photos (still image part)
        # BMP
        '.bmp', '.dib',
        # GIF
        '.gif',
        # Modern formats
        '.avif',  # AV1 Image File
        '.jxl',   # JPEG XL
        # RAW formats (may require extra plugins)
        '.cr2', '.cr3',  # Canon RAW
        '.nef', '.nrw',  # Nikon RAW
        '.arw', '.srf', '.sr2',  # Sony RAW
        '.dng',  # Adobe Digital Negative (includes Apple ProRAW)
        '.orf',  # Olympus RAW
        '.rw2',  # Panasonic RAW
        '.pef',  # Pentax RAW
        '.raf',  # Fujifilm RAW
    }

    # Video file extensions
    VIDEO_EXTENSIONS = {
        # Apple/iPhone formats
        '.mov',   # ✅ QuickTime, Live Photos (video part), Cinematic mode, ProRes
        '.m4v',   # ✅ iTunes video, iPhone recordings
        # Common video formats
        '.mp4',   # MPEG-4
        # MPEG family
        '.mpeg', '.mpg', '.mpe',
        # Windows Media
        '.wmv', '.asf',
        # AVI
        '.avi',
        # Matroska
        '.mkv', '.webm',
        # Flash
        '.flv', '.f4v',
        # Mobile/Other
        '.3gp', '.3g2',  # Mobile phones
        '.ogv'           # Ogg Video
    }

    # Combined: all supported media files (photos + videos)
    SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

    # Default ignore patterns (OS-specific to avoid irrelevant exclusions)
    # Common folders to ignore across all platforms
    _COMMON_IGNORE_FOLDERS = {
        "__pycache__", "node_modules", ".git", ".svn", ".hg",
        "venv", ".venv", "env", ".env"
    }

    # Platform-specific ignore folders
    if platform.system() == "Windows":
        DEFAULT_IGNORE_FOLDERS = _COMMON_IGNORE_FOLDERS | {
            "AppData", "Program Files", "Program Files (x86)", "Windows",
            "$Recycle.Bin", "System Volume Information", "Temp", "Cache",
            "Microsoft", "Installer", "Recovery", "Logs",
            "ThumbCache", "ActionCenterCache"
        }
    elif platform.system() == "Darwin":  # macOS
        DEFAULT_IGNORE_FOLDERS = _COMMON_IGNORE_FOLDERS | {
            "Library", ".Trash", "Caches", "Logs",
            "Application Support"
        }
    else:  # Linux and others
        DEFAULT_IGNORE_FOLDERS = _COMMON_IGNORE_FOLDERS | {
            ".cache", ".local/share/Trash", "tmp"
        }

    def __init__(self,
                 project_id: int | None = None,
                 photo_repo: Optional[PhotoRepository] = None,
                 folder_repo: Optional[FolderRepository] = None,
                 project_repo: Optional[ProjectRepository] = None,
                 metadata_service: Optional[MetadataService] = None,
                 batch_size: int = 200,
                 stat_timeout: float = 3.0):
        """
        Initialize scan service.

        Args:
            photo_repo: Photo repository (creates default if None)
            folder_repo: Folder repository (creates default if None)
            project_repo: Project repository (creates default if None)
            metadata_service: Metadata extraction service (creates default if None)
            batch_size: Number of photos to batch before writing (default: 200)
                       NOTE: Could be made configurable via SettingsManager in the future
            stat_timeout: Timeout for os.stat calls in seconds (default: 3.0)
                         NOTE: Could be made configurable via SettingsManager in the future
        """
        self.project_id = project_id
        
        self.photo_repo = photo_repo or PhotoRepository()
        self.folder_repo = folder_repo or FolderRepository()
        self.project_repo = project_repo or ProjectRepository()
        self.metadata_service = metadata_service or MetadataService()

        self.batch_size = batch_size
        self.stat_timeout = stat_timeout

        self._cancelled = False
        self._stats = {
            'photos_indexed': 0,
            'photos_skipped': 0,
            'photos_failed': 0,
            'folders_found': 0
        }

        # Tracking for richer progress feedback
        self._total_photos = 0
        self._total_videos = 0
        self._total_media_files = 0
        self._photos_processed = 0
        self._videos_processed = 0
        self._scan_start_time = time.time()
        self._scan_root = None
        self._last_progress_emit = 0.0

        # Track detailed file processing status for progress dialog
        self._last_file_details = {
            'filename': '',
            'size_kb': 0,
            'width': None,
            'height': None,
            'date_taken': None,
            'folder_id': None,
            'status': ''  # 'starting', 'extracting', 'complete', 'failed'
        }

        # Video workers (initialized when videos are processed)
        self.video_metadata_worker = None
        self.video_thumbnail_worker = None

    def _emit_progress_event(self,
                             progress_callback: Callable[[ScanProgress], None],
                             file_path: Path,
                             file_index: int,
                             total_files: int,
                             row: Optional[Tuple],
                             now: Optional[float] = None,
                             update_last_emit: bool = True) -> None:
        """Emit a formatted progress event using the latest file details."""
        file_name = file_path.name

        # CRITICAL FIX: Get file size safely without blocking
        # BUG: file_path.stat() can HANG on slow/network drives or permission issues
        # SOLUTION: Use size from already-processed row, or skip size if unavailable
        file_size_kb = 0
        if row is not None and len(row) > 2:
            # Row format: (path, folder_id, size_kb, ...)
            file_size_kb = round(row[2], 1) if row[2] else 0

        processed_media = self._photos_processed + self._videos_processed
        percentage = int((processed_media / max(1, self._total_media_files)) * 100)

        # Build detailed progress message using captured processing details
        details = self._last_file_details
        if details['status'] == 'complete' and details['filename']:
            # File was just processed - show detailed status
            meta_info = f"[w={details['width']}, h={details['height']}, date={details['date_taken']}]"
            status_line = f"✓ Processed: {details['filename']} ({details['size_kb']:.1f} KB) {meta_info}"
        elif details['status'] == 'extracting' and details['filename']:
            # Currently extracting metadata
            status_line = f"📷 Extracting metadata: {details['filename']} ({details['size_kb']:.1f} KB)"
        elif details['status'] == 'starting' and details['filename']:
            # Just started processing this file
            status_line = f"Starting file {file_index}/{total_files}: {details['filename']}"
        elif details['status'] == 'failed' and details['filename']:
            # Processing failed
            status_line = f"✗ Failed: {details['filename']}"
        else:
            # Fallback to generic message
            status_line = f"📷 {file_name} ({file_size_kb} KB)"

        progress = ScanProgress(
            current=processed_media,
            total=self._total_media_files,
            percent=percentage,
            message=status_line,
            current_file=str(file_path)
        )

        try:
            progress_callback(progress)
            if update_last_emit:
                self._last_progress_emit = now if now is not None else time.time()
        except Exception as e:
            logger.error(f"Progress callback error: {e}", exc_info=True)
            print(f"[SCAN] ⚠️ Progress callback failed: {e}")
            sys.stdout.flush()

    # ------------------------------------------------------------------
    # Quick pre-scan statistics (no metadata, no hashes — just counting)
    # ------------------------------------------------------------------
    def estimate_repository_stats(
        self,
        root_folder: str,
        options: dict | None = None,
        should_cancel=None,
    ) -> dict:
        """Walk *root_folder* and return fast file-count statistics.

        This is intentionally "dumb but fast" — no EXIF, no hashes, only
        ``os.walk`` + ``os.stat``.  Used by the PreScanOptionsDialog to show
        a quick preflight summary before the real scan starts.
        """
        options = options or {}
        ignore_hidden = bool(options.get("ignore_hidden", True))

        photo_ext = {
            ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
            ".tif", ".tiff", ".bmp", ".gif",
        }
        video_ext = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv"}

        photos = 0
        videos = 0
        folders = 0
        bytes_total = 0

        for dirpath, dirnames, filenames in os.walk(root_folder):
            if should_cancel and should_cancel():
                break

            if ignore_hidden and os.path.basename(dirpath).startswith("."):
                dirnames.clear()  # prune hidden subtrees
                continue

            folders += 1

            for fn in filenames:
                if should_cancel and should_cancel():
                    break

                if ignore_hidden and fn.startswith("."):
                    continue

                ext = os.path.splitext(fn)[1].lower()
                full = os.path.join(dirpath, fn)

                try:
                    bytes_total += os.stat(full).st_size
                except OSError:
                    pass

                if ext in photo_ext:
                    photos += 1
                elif ext in video_ext:
                    videos += 1

        return {
            "photos": photos,
            "videos": videos,
            "folders": folders,
            "bytes": bytes_total,
        }

    def scan_repository(self,
                       root_folder: str,
                       project_id: int,
                       incremental: bool = True,
                       skip_unchanged: Optional[bool] = None,
                       extract_exif_date: bool = True,
                       ignore_folders: Optional[Set[str]] = None,
                       progress_callback: Optional[Callable[[ScanProgress], None]] = None,
                       on_video_metadata_finished: Optional[Callable[[int, int], None]] = None) -> ScanResult:
        """
        Scan a photo repository and index all photos.

        Args:
            root_folder: Root folder to scan
            project_id: Project ID to associate scanned photos with
            incremental: If True, skip files that haven't changed (default: True)
            skip_unchanged: Skip files with matching mtime (default: None, uses incremental value)
            extract_exif_date: Extract EXIF DateTimeOriginal
            ignore_folders: Folders to skip (uses defaults if None)
            progress_callback: Optional callback for progress updates

        Returns:
            ScanResult with statistics

        Raises:
            ValueError: If root_folder doesn't exist
            Exception: For other errors (with logging)
        """
        start_time = time.time()
        self._cancelled = False
        self._stats = {'photos_indexed': 0, 'photos_skipped': 0, 'photos_failed': 0, 'videos_indexed': 0, 'folders_found': 0}
        self._photos_processed = 0
        self._videos_processed = 0
        self._scan_start_time = start_time
        self._last_progress_emit = 0.0

        # FIX: If skip_unchanged not specified, use incremental value
        if skip_unchanged is None:
            skip_unchanged = incremental

        root_path = Path(root_folder).resolve()
        self._scan_root = root_path
        if not root_path.exists():
            raise ValueError(f"Root folder does not exist: {root_folder}")

        logger.info(f"Starting scan: {root_folder} (incremental={incremental}, skip_unchanged={skip_unchanged})")

        # CRITICAL FIX: Ensure project exists before any operations that reference it
        # The photo_folders table has a FOREIGN KEY constraint on project_id -> project(id)
        # Without this, folder creation fails with FOREIGN KEY constraint failed
        self._ensure_project_exists(project_id, root_folder)

        try:
            # Step 1: Discover all media files (photos + videos)
            # Priority: explicit parameter > settings > platform-specific defaults
            if ignore_folders is not None:
                ignore_set = ignore_folders
            else:
                # Check settings for custom exclusions
                ignore_set = self._get_ignore_folders_from_settings()

            # _discover_files/_discover_videos already dedup internally
            # via _deduplicate_paths() using resolved canonical paths.
            all_files = self._discover_files(root_path, ignore_set)
            all_videos = self._discover_videos(root_path, ignore_set)

            total_files = len(all_files)
            total_videos = len(all_videos)
            self._total_photos = total_files
            self._total_videos = total_videos
            self._total_media_files = total_files + total_videos

            logger.info(f"Discovered {total_files} candidate image files and {total_videos} video files")

            # CRITICAL FIX: Send discovery message to progress callback for dialog threshold check
            # scan_controller.py needs this message to parse file count and decide whether to show dialog
            if progress_callback:
                discovery_msg = self._build_progress_message(
                    status_line="Preparing scan…",
                    current_path=root_path,
                    processed_count=0,
                    total_count=self._total_media_files,
                    discovery=True
                )
                progress = ScanProgress(
                    current=0,
                    total=self._total_media_files,
                    percent=0,
                    message=discovery_msg,
                    current_file=None
                )
                try:
                    progress_callback(progress)
                except Exception as e:
                    logger.warning(f"Progress callback error during discovery: {e}")

            if total_files == 0 and total_videos == 0:
                logger.warning("No media files found")
                return ScanResult(0, 0, 0, 0, 0, time.time() - start_time)

            # Step 2: Load existing metadata for incremental scan
            existing_metadata = {}
            existing_video_metadata = {}
            if skip_unchanged:
                try:
                    logger.info("Loading existing metadata for incremental scan...")
                    existing_metadata = self._load_existing_metadata()
                    existing_video_metadata = self._load_existing_video_metadata()
                    logger.info(f"✓ Loaded {len(existing_metadata)} existing photo records and {len(existing_video_metadata)} video records")
                except Exception as e:
                    logger.warning(f"Failed to load existing metadata (continuing with full scan): {e}")
                    # Continue with full scan if metadata loading fails
                    existing_metadata = {}
                    existing_video_metadata = {}

            # Step 3: Process files in batches
            batch_rows = []
            folders_seen: Set[str] = set()

            # DEADLOCK FIX v2: Use single executor for entire scan
            # PROBLEM v1: Fresh executor per file = 105 executors, massive overhead, thread leaks
            # PROBLEM v2: Batch approach (every 5 files) = shutdown(wait=True) DEADLOCKS at boundaries
            #   - Root cause: Main thread blocks on executor.shutdown(wait=True)
            #   - While blocking, database/Qt operations can't proceed → circular wait
            #   - Observed: Freeze at file 10/15 (66%) when recreating executor
            # SOLUTION v2: Single executor for entire scan, shutdown only at end
            #   - No mid-scan shutdown calls = no deadlock opportunities
            #   - All futures properly awaited via .result() calls
            #   - Clean shutdown in finally block when scan completes
            executor = ThreadPoolExecutor(max_workers=2)
            print(f"[SCAN] Created single executor for scan ({total_files} files)")

            try:
                for i, file_path in enumerate(all_files, 1):
                    if self._cancelled:
                        logger.info("Scan cancelled by user")
                        break

                    # Update progress details for UI — mark as starting
                    self._last_file_details['filename'] = file_path.name
                    self._last_file_details['status'] = 'starting'
                    self._last_file_details['width'] = None
                    self._last_file_details['height'] = None
                    self._last_file_details['date_taken'] = None

                    # NOTE: Removed per-file "starting" emit to prevent UI stutter.
                    # The main throttled emit below (every 0.35 s / 25 files) is sufficient.

                    try:
                        # Process file
                        row = self._process_file(
                            file_path=file_path,
                            root_path=root_path,
                            project_id=project_id,
                            existing_metadata=existing_metadata,
                            skip_unchanged=skip_unchanged,
                            extract_exif_date=extract_exif_date,
                            executor=executor
                        )
                    except Exception as file_error:
                        logger.error(f"File processing error: {file_error}")
                        self._stats['photos_failed'] += 1
                        continue

                    if row is None:
                        # Skipped or failed
                        continue

                    # Track folder
                    folder_path = os.path.dirname(str(file_path))
                    folders_seen.add(folder_path)

                    batch_rows.append(row)

                    # Flush batch if needed
                    if len(batch_rows) >= self.batch_size:
                        logger.info(f"Writing batch of {len(batch_rows)} photos to database")
                        self._write_batch(batch_rows, project_id)
                        batch_rows.clear()

                    self._photos_processed = i

                    # Report progress (check cancellation here too for responsiveness)
                    if progress_callback:
                        now = time.time()
                        should_emit = (
                            i == total_files
                            or i <= 5  # show early feedback immediately
                            or (now - self._last_progress_emit) >= 0.35
                            or i % 25 == 0
                        )

                        if should_emit:
                            # RESPONSIVE CANCEL: Check during progress reporting
                            if self._cancelled:
                                logger.info("Scan cancelled during progress reporting")
                                break

                            self._emit_progress_event(
                                progress_callback=progress_callback,
                                file_path=file_path,
                                file_index=i,
                                total_files=total_files,
                                row=row,
                                now=now
                            )

                # Final batch flush
                if batch_rows and not self._cancelled:
                    print(f"[SCAN] ⚡ Writing final batch to database: {len(batch_rows)} photos")
                    logger.info(f"Writing final batch of {len(batch_rows)} photos to database")
                    self._write_batch(batch_rows, project_id)
                    print(f"[SCAN] ✓ Final batch write complete")

            finally:
                # DEADLOCK FIX v2: Shutdown executor with wait=False to avoid blocking
                # All futures are already awaited via .result() calls, so wait=False is safe
                if executor is not None:
                    try:
                        print(f"[SCAN] Shutting down executor")
                        executor.shutdown(wait=False, cancel_futures=True)
                        logger.info(f"Executor shutdown complete")
                    except Exception as e:
                        logger.warning(f"Final executor shutdown error: {e}")

            # Step 4: Process videos
            print(f"\n[SCAN] === STEP 4: VIDEO PROCESSING ===")
            print(f"[SCAN] total_videos={total_videos}")
            print(f"[SCAN] self._cancelled={self._cancelled}")
            print(f"[SCAN] Condition check: {total_videos} > 0 and not {self._cancelled} = {total_videos > 0 and not self._cancelled}")
            sys.stdout.flush()

            if total_videos > 0 and not self._cancelled:
                print(f"[SCAN] Condition TRUE - calling _process_videos()")
                sys.stdout.flush()
                logger.info(f"Processing {total_videos} videos...")
                self._process_videos(all_videos, root_path, project_id, folders_seen, skip_unchanged, existing_video_metadata, progress_callback)
            else:
                print(f"[SCAN] Condition FALSE - skipping video processing!")
                if total_videos == 0:
                    print(f"[SCAN]   Reason: No videos found (total_videos=0)")
                if self._cancelled:
                    print(f"[SCAN]   Reason: Scan was cancelled")
                sys.stdout.flush()

            # Step 5: Create default project and branch if needed
            self._ensure_default_project(root_folder)

            # Step 6: Launch background workers for video processing
            if self._stats['videos_indexed'] > 0:
                self.video_metadata_worker, self.video_thumbnail_worker = self._launch_video_workers(
                    project_id,
                    on_metadata_finished_callback=on_video_metadata_finished
                )

            # Finalize
            duration = time.time() - start_time
            self._stats['folders_found'] = len(folders_seen)

            logger.info(
                f"Scan complete: {self._stats['photos_indexed']} photos indexed, "
                f"{self._stats['videos_indexed']} videos indexed, "
                f"{self._stats['photos_skipped']} skipped, "
                f"{self._stats['photos_failed']} failed in {duration:.1f}s"
            )

            return ScanResult(
                folders_found=self._stats['folders_found'],
                photos_indexed=self._stats['photos_indexed'],
                photos_skipped=self._stats['photos_skipped'],
                photos_failed=self._stats['photos_failed'],
                videos_indexed=self._stats['videos_indexed'],
                duration_seconds=duration,
                interrupted=self._cancelled
            )

        except Exception as e:
            logger.error(f"Scan failed: {e}", exc_info=True)
            raise

    def cancel(self):
        """Request cancellation of current scan."""
        self._cancelled = True
        logger.info("Scan cancellation requested")

    def _deduplicate_paths(self, paths: List[Path]) -> List[Path]:
        """De-duplicate candidate paths while preserving order.

        Symlinks, NTFS junctions, and case-insensitive filesystems can make
        os.walk() yield the same physical file under different paths, causing
        doubled DB rows and wasted background work.

        Uses os.path.normcase (platform-aware) instead of .lower() so the
        behaviour is correct on both Windows (case-insensitive) and Linux
        (case-sensitive).
        """
        seen: set = set()
        unique: List[Path] = []
        for p in paths or []:
            try:
                key = os.path.normcase(str(p.resolve()))
            except OSError:
                key = os.path.normcase(str(p))
            if key not in seen:
                seen.add(key)
                unique.append(p)
        removed = len(paths) - len(unique)
        if removed > 0:
            logger.info(f"De-duplicated {removed} duplicate path(s) from {len(paths)} candidates")
        return unique

    def _discover_files(self, root_path: Path, ignore_folders: Set[str]) -> List[Path]:
        """
        Discover all image files in directory tree.

        Args:
            root_path: Root directory
            ignore_folders: Folder names to skip

        Returns:
            List of image file paths
        """
        image_files = []

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Check cancellation during discovery (responsive cancel)
            if self._cancelled:
                logger.info("File discovery cancelled by user")
                return image_files

            # Filter ignored directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in ignore_folders and not d.startswith(".")
            ]

            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in self.IMAGE_EXTENSIONS:  # CRITICAL FIX: Use IMAGE_EXTENSIONS, not SUPPORTED_EXTENSIONS
                    image_files.append(Path(dirpath) / filename)

        return self._deduplicate_paths(image_files)

    def _discover_videos(self, root_path: Path, ignore_folders: Set[str]) -> List[Path]:
        """
        Discover all video files in directory tree.

        Args:
            root_path: Root directory
            ignore_folders: Folder names to skip

        Returns:
            List of video file paths
        """
        video_files = []

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Check cancellation during discovery (responsive cancel)
            if self._cancelled:
                logger.info("Video discovery cancelled by user")
                return video_files

            # Filter ignored directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in ignore_folders and not d.startswith(".")
            ]

            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in self.VIDEO_EXTENSIONS:
                    video_files.append(Path(dirpath) / filename)

        return self._deduplicate_paths(video_files)

    def _get_ignore_folders_from_settings(self) -> Set[str]:
        """
        Get ignore folders from settings, with fallback to platform-specific defaults.

        Returns:
            Set of folder names to ignore during scanning

        Priority:
            1. Custom exclusions from settings (if non-empty)
            2. Platform-specific defaults (DEFAULT_IGNORE_FOLDERS)
        """
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            # CRITICAL FIX: Use "ignore_folders" which is the key used by preferences_dialog.py
            # The previous key "scan_exclude_folders" was never populated by the UI
            custom_exclusions = settings.get("ignore_folders", [])

            if custom_exclusions:
                # User has configured custom exclusions - use them
                logger.info(f"Using custom scan exclusions from settings: {len(custom_exclusions)} folders")
                return set(custom_exclusions)
            else:
                # No custom exclusions - use platform-specific defaults
                logger.debug(f"Using platform-specific default exclusions ({platform.system()})")
                return self.DEFAULT_IGNORE_FOLDERS
        except Exception as e:
            logger.warning(f"Could not load scan exclusions from settings: {e}")
            logger.debug("Falling back to platform-specific default exclusions")
            return self.DEFAULT_IGNORE_FOLDERS

    def _load_existing_metadata(self) -> Dict[str, str]:
        """
        Load existing file metadata for incremental scanning.

        Returns:
            Dictionary mapping path -> mtime string
        """
        try:
            # Use repository to get all photos
            with self.photo_repo.connection(read_only=True) as conn:
                cur = conn.cursor()
                # CRITICAL BUG FIX: Filter by project_id to avoid cross-project duplicate detection
                # Without this, photos from other projects are considered duplicates and skipped
                cur.execute("SELECT path, modified FROM photo_metadata WHERE project_id = ?", (self.project_id,))
                return {row['path']: row['modified'] for row in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Could not load existing metadata: {e}")
            return {}

    def _load_existing_video_metadata(self) -> Dict[str, str]:
        """
        Load existing video metadata for incremental scanning.

        Returns:
            Dictionary mapping path -> mtime string
        """
        try:
            with self.photo_repo.connection(read_only=True) as conn:
                cur = conn.cursor()
                # CRITICAL BUG FIX: Filter by project_id to avoid cross-project duplicate detection
                # Without this, videos from other projects are considered duplicates and skipped
                cur.execute("SELECT path, modified FROM video_metadata WHERE project_id = ?", (self.project_id,))
                return {row['path']: row['modified'] for row in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Could not load existing video metadata: {e}")
            return {}

    def _compute_created_fields(self, date_str: str = None, modified: str = None) -> tuple:
        """
        Compute created_ts, created_date, created_year from date or modified time.

        This helper is used for both photos and videos to ensure consistent date handling.

        Args:
            date_str: Date string in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format
            modified: Modified timestamp in YYYY-MM-DD HH:MM:SS format (fallback)

        Returns:
            Tuple of (created_ts, created_date, created_year) or (None, None, None)

        Example:
            >>> _compute_created_fields("2024-11-12", None)
            (1699747200, "2024-11-12", 2024)

            >>> _compute_created_fields(None, "2024-11-12 15:30:00")
            (1699747200, "2024-11-12", 2024)
        """
        from datetime import datetime

        # Try parsing date_str first, fall back to modified
        date_to_parse = date_str if date_str else modified

        if not date_to_parse:
            return (None, None, None)

        try:
            # Extract YYYY-MM-DD part
            date_only = date_to_parse.split(' ')[0]
            dt = datetime.strptime(date_only, '%Y-%m-%d')

            return (
                int(dt.timestamp()),  # created_ts
                date_only,             # created_date (YYYY-MM-DD)
                dt.year                # created_year
            )
        except (ValueError, AttributeError, IndexError) as e:
            logger.debug(f"Failed to parse date '{date_to_parse}': {e}")
            return (None, None, None)

    def _quick_extract_video_date(self, video_path: Path, timeout: float = 2.0) -> Optional[str]:
        """
        Quickly extract video creation date during scan with timeout.

        Uses ffprobe to extract creation_time from video metadata. This is faster
        and more accurate than using file modified date.

        Args:
            video_path: Path to video file
            timeout: Maximum time to wait for ffprobe (default: 2.0 seconds)

        Returns:
            Date string in YYYY-MM-DD format, or None if extraction fails/timeouts

        Note:
            This method prioritizes speed over completeness:
            - Uses short timeout to avoid blocking scan
            - Only extracts creation date (not duration, resolution, etc.)
            - Falls back to None if extraction fails (caller uses modified date)
            - Background workers will extract full metadata later
        """
        import subprocess
        import json  # CRITICAL FIX: Import outside try block to avoid "referenced before assignment" error

        try:
            # Check if ffprobe is available
            if not shutil.which('ffprobe'):
                return None

            # Quick ffprobe extraction with timeout
            # Only extract creation_time tag, not full metadata
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_entries', 'format_tags=creation_time',
                    str(video_path)
                ],
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode != 0:
                return None

            # Parse JSON output
            data = json.loads(result.stdout)

            # Extract creation_time from format tags
            creation_time = data.get('format', {}).get('tags', {}).get('creation_time')

            if not creation_time:
                return None

            # Parse ISO 8601 timestamp: 2024-11-12T10:30:45.000000Z
            # Extract YYYY-MM-DD part
            from datetime import datetime
            dt = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d')

        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, Exception) as e:
            logger.debug(f"Quick video date extraction failed for {video_path}: {e}")
            return None

    def _process_file(self,
                     file_path: Path,
                     root_path: Path,
                     project_id: int,
                     existing_metadata: Dict[str, str],
                     skip_unchanged: bool,
                     extract_exif_date: bool,
                     executor: ThreadPoolExecutor) -> Optional[Tuple]:
        """
        Process a single image file.

        Returns:
            Tuple for database insert, or None if skipped/failed
        """
        # RESPONSIVE CANCEL: Check before processing each file
        if self._cancelled:
            return None

        path_str = str(file_path)
        print(f"[SCAN] _process_file started for: {path_str}")
        sys.stdout.flush()

        # Step 1: Get file stats with timeout protection
        try:
            print(f"[SCAN] Getting file stats...")
            sys.stdout.flush()
            future = executor.submit(os.stat, path_str)
            stat_result = future.result(timeout=self.stat_timeout)
            print(f"[SCAN] File stats retrieved successfully")
            sys.stdout.flush()
        except FuturesTimeoutError:
            logger.warning(f"os.stat timeout for {path_str}")
            self._stats['photos_failed'] += 1
            try:
                future.cancel()
            except Exception:
                pass
            return None
        except FileNotFoundError:
            logger.debug(f"File not found: {path_str}")
            self._stats['photos_failed'] += 1
            return None
        except Exception as e:
            logger.warning(f"os.stat failed for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            return None

        # Step 2: Extract basic metadata from stat
        try:
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat_result.st_mtime))
            size_kb = stat_result.st_size / 1024.0
        except Exception as e:
            logger.error(f"Failed to process stat result for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            return None

        # Step 3: Skip if unchanged (incremental scan)
        # CRITICAL FIX: Normalize path before lookup (database stores normalized paths)
        normalized_path = self.photo_repo._normalize_path(path_str)
        if skip_unchanged and existing_metadata.get(normalized_path) == mtime:
            self._stats['photos_skipped'] += 1
            return None

        # RESPONSIVE CANCEL: Check before expensive metadata extraction
        if self._cancelled:
            return None

        # Step 4: Extract dimensions, EXIF date, and GPS using MetadataService
        # CRITICAL FIX: Wrap metadata extraction with timeout to prevent hangs
        # PIL/Pillow can hang on corrupted images, malformed TIFF/EXIF, or files with infinite loops
        # BUG FIX #8: Use fast extract_basic_metadata() to avoid hangs, compute created_* inline
        # LONG-TERM FIX (2026-01-08): Now also extracts GPS coordinates for Locations section
        width = height = date_taken = gps_lat = gps_lon = None
        created_ts = created_date = created_year = None
        metadata_timeout = 5.0  # 5 seconds per image

        if extract_exif_date:
            # Use fast basic metadata extraction (BUG FIX #8: Reverted from extract_metadata)
            try:
                # DIAGNOSTIC: Always log which file is being processed (can help identify freeze cause)
                logger.info(f"📷 Processing: {os.path.basename(path_str)} ({size_kb:.1f} KB)")
                print(f"[SCAN] Processing: {os.path.basename(path_str)}")
                sys.stdout.flush()

                # Update progress details for UI
                self._last_file_details['filename'] = os.path.basename(path_str)
                self._last_file_details['size_kb'] = size_kb
                self._last_file_details['status'] = 'extracting'

                future = executor.submit(self.metadata_service.extract_basic_metadata, str(file_path))
                width, height, date_taken, gps_lat, gps_lon, image_content_hash = future.result(timeout=metadata_timeout)

                # Store extracted metadata for progress updates
                self._last_file_details['width'] = width
                self._last_file_details['height'] = height
                self._last_file_details['date_taken'] = date_taken

                # Log GPS extraction if found
                if gps_lat is not None and gps_lon is not None:
                    logger.info(f"[Scan] ✓ GPS extracted: {os.path.basename(path_str)} ({gps_lat:.4f}, {gps_lon:.4f})")
                    print(f"[SCAN] ✓ GPS: ({gps_lat:.4f}, {gps_lon:.4f})")
                    sys.stdout.flush()

                print(f"[SCAN] ✓ Metadata extracted: {os.path.basename(path_str)} [w={width}, h={height}, date={date_taken}]")
                sys.stdout.flush()
                logger.info(f"[Scan] Metadata extracted successfully: {os.path.basename(path_str)} [w={width}, h={height}, date={date_taken}]")
            except FuturesTimeoutError:
                logger.warning(f"Metadata extraction timeout for {path_str} (5s limit) - continuing without metadata")
                # Continue without dimensions/EXIF - photo will still be indexed
                try:
                    future.cancel()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"Could not extract image metadata from {path_str}: {e}")
                # Continue without dimensions/EXIF
        else:
            # Just get dimensions without EXIF (with timeout)
            try:
                future = executor.submit(self.metadata_service.extract_basic_metadata, str(file_path))
                width, height, _, gps_lat, gps_lon, image_content_hash = future.result(timeout=metadata_timeout)
            except FuturesTimeoutError:
                logger.warning(f"Dimension extraction timeout for {path_str} (5s limit)")
                try:
                    future.cancel()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"Could not extract dimensions from {path_str}: {e}")

        # BUG FIX #7 + #8: Compute created_* fields from date_taken inline (no heavy extract_metadata call)
        if date_taken:
            try:
                from datetime import datetime
                # Parse date_taken (format: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD')
                date_str = date_taken.split(' ')[0]  # Extract YYYY-MM-DD part
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                created_ts = int(dt.timestamp())
                created_date = date_str  # YYYY-MM-DD
                created_year = dt.year
            except (ValueError, AttributeError, IndexError) as e:
                # If date parsing fails, these fields will remain NULL
                logger.debug(f"[Scan] Failed to parse date_taken '{date_taken}': {e}")

        # Step 5: Ensure folder hierarchy exists
        try:
            print(f"[SCAN] Creating folder hierarchy for: {os.path.basename(path_str)}")
            sys.stdout.flush()
            folder_id = self._ensure_folder_hierarchy(file_path.parent, root_path, project_id)

            # Store folder_id for progress updates
            self._last_file_details['folder_id'] = folder_id

            print(f"[SCAN] ✓ Folder hierarchy created: folder_id={folder_id}")
            sys.stdout.flush()
        except Exception as e:
            logger.error(f"Failed to create folder hierarchy for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            self._last_file_details['status'] = 'failed'
            return None

        # Success
        self._stats['photos_indexed'] += 1
        self._last_file_details['status'] = 'complete'
        print(f"[SCAN] ✓ File processed successfully: {os.path.basename(path_str)}")
        sys.stdout.flush()

        # Return row tuple for batch insert
        # BUG FIX #7: Include created_ts, created_date, created_year for date hierarchy
        # LONG-TERM FIX (2026-01-08): Include gps_latitude, gps_longitude for Locations section
        # v9.3.0: Include image_content_hash for pixel-based embedding staleness detection
        # (path, folder_id, size_kb, modified, width, height, date_taken, tags,
        #  created_ts, created_date, created_year, gps_latitude, gps_longitude, image_content_hash)
        return (path_str, folder_id, size_kb, mtime, width, height, date_taken, None,
                created_ts, created_date, created_year, gps_lat, gps_lon, image_content_hash)

    def _ensure_folder_hierarchy(self, folder_path: Path, root_path: Path, project_id: int) -> int:
        """
        Ensure folder and all parent folders exist in database.

        Args:
            folder_path: Current folder path
            root_path: Repository root path
            project_id: Project ID for folder ownership

        Returns:
            Folder ID
        """
        # Ensure root folder exists
        root_id = self.folder_repo.ensure_folder(
            path=str(root_path),
            name=root_path.name,
            parent_id=None,
            project_id=project_id
        )

        # If folder is root, return root_id
        if folder_path == root_path:
            return root_id

        # Build parent chain
        try:
            rel_path = folder_path.relative_to(root_path)
            parts = list(rel_path.parts)

            current_parent_id = root_id
            current_path = root_path

            for part in parts:
                current_path = current_path / part
                current_parent_id = self.folder_repo.ensure_folder(
                    path=str(current_path),
                    name=part,
                    parent_id=current_parent_id,
                    project_id=project_id
                )

            return current_parent_id

        except ValueError:
            # folder_path not under root_path (shouldn't happen)
            logger.warning(f"Folder {folder_path} is not under root {root_path}")
            return self.folder_repo.ensure_folder(
                path=str(folder_path),
                name=folder_path.name,
                parent_id=root_id,
                project_id=project_id
            )

    def _write_batch(self, rows: List[Tuple], project_id: int):
        """
        Write a batch of photo rows to database.

        Args:
            rows: List of tuples (path, folder_id, size_kb, modified, width, height, date_taken, tags,
                                   created_ts, created_date, created_year, gps_latitude, gps_longitude,
                                   image_content_hash)
            project_id: Project ID for photo ownership
        """
        if not rows:
            return

        # RESPONSIVE CANCEL: Check before database write
        if self._cancelled:
            logger.info("Batch write skipped due to cancellation")
            return

        try:
            print(f"[SCAN] 💾 Starting bulk_upsert for {len(rows)} photos...")
            logger.info(f"[DB] Starting bulk_upsert for {len(rows)} photos")
            affected = self.photo_repo.bulk_upsert(rows, project_id)
            print(f"[SCAN] ✓ Bulk_upsert completed: {affected} photos written")
            logger.info(f"[DB] Bulk_upsert completed: {affected} photos written")
        except Exception as e:
            print(f"[SCAN] ⚠️ Batch write failed: {e}")
            logger.error(f"Failed to write batch: {e}", exc_info=True)
            # Try individual writes as fallback
            print(f"[SCAN] Attempting individual writes as fallback...")
            for idx, row in enumerate(rows, 1):
                try:
                    # BUG FIX #7: Unpack row with created_* fields
                    # LONG-TERM FIX (2026-01-08): Include GPS coordinates
                    # v9.3.0: Include image_content_hash for pixel-based staleness
                    path, folder_id, size_kb, modified, width, height, date_taken, tags, created_ts, created_date, created_year, gps_lat, gps_lon, image_content_hash = row
                    print(f"[SCAN] Writing individual photo {idx}/{len(rows)}: {os.path.basename(path)}")
                    self.photo_repo.upsert(path, folder_id, project_id, size_kb, modified, width, height,
                                          date_taken, tags, created_ts, created_date, created_year, gps_lat, gps_lon, image_content_hash)
                except Exception as e2:
                    print(f"[SCAN] ⚠️ Failed to write photo {idx}/{len(rows)}: {e2}")
                    logger.error(f"Failed to write individual photo {row[0]}: {e2}")

    def _ensure_default_project(self, root_folder: str):
        """
        Ensure a default project exists and has an 'all' branch.

        Args:
            root_folder: Repository root folder
        """
        try:
            projects = self.project_repo.find_all(limit=1)

            if not projects:
                # Create default project
                project_id = self.project_repo.create(
                    name="Default Project",
                    folder=root_folder,
                    mode="date"
                )
                logger.info(f"Created default project (id={project_id})")
            else:
                project_id = projects[0]['id']

            # Ensure 'all' branch exists
            self.project_repo.ensure_branch(
                project_id=project_id,
                branch_key="all",
                display_name="📁 All Photos"
            )

            # Add all photos to 'all' branch
            # TODO: This should be done more efficiently
            logger.debug(f"Project {project_id} ready with 'all' branch")

        except Exception as e:
            logger.warning(f"Could not create default project: {e}")

    def _ensure_project_exists(self, project_id: int, root_folder: str):
        """
        Ensure the specified project exists in the database.

        CRITICAL: This must be called BEFORE any operations that reference project_id
        (folder creation, photo insertion, etc.) because the database has FOREIGN KEY
        constraints that require the project to exist.

        Args:
            project_id: Project ID to verify
            root_folder: Repository root folder (used for default project creation)

        Raises:
            ValueError: If project_id is None or invalid
        """
        if project_id is None:
            raise ValueError("project_id cannot be None for scan operations")

        try:
            # Check if project exists
            project = self.project_repo.get_by_id(project_id)

            if project is None:
                # Project doesn't exist - create it
                logger.warning(f"Project {project_id} not found, creating default project...")
                created_id = self.project_repo.create(
                    name="Default Project",
                    folder=root_folder,
                    mode="date"
                )
                logger.info(f"Created project: Default Project (id={created_id}, semantic_model=clip-vit-b32)")

                # Verify the created project ID matches expected (it should if DB is empty)
                if created_id != project_id:
                    logger.warning(
                        f"Created project has different ID ({created_id}) than requested ({project_id}). "
                        f"This may cause issues if caller expects specific project_id."
                    )
            else:
                logger.debug(f"Project {project_id} exists: {project.get('name', 'Unknown')}")

        except Exception as e:
            logger.error(f"Failed to ensure project exists: {e}")
            raise ValueError(f"Cannot verify project {project_id}: {e}")

    def _process_videos(self, video_files: List[Path], root_path: Path, project_id: int,
                       folders_seen: Set[str], skip_unchanged: bool, existing_video_metadata: Dict[str, str],
                       progress_callback: Optional[Callable] = None):
        """
        Process discovered video files and index them.

        Args:
            video_files: List of video file paths
            root_path: Root directory of scan
            project_id: Project ID
            folders_seen: Set of folder paths already seen
            skip_unchanged: Skip videos with matching mtime
            existing_video_metadata: Dict mapping normalized path -> mtime
            progress_callback: Optional progress callback
        """
        try:
            from services.video_service import VideoService
            video_service = VideoService()

            for i, video_path in enumerate(video_files, 1):
                if self._cancelled:
                    logger.info("Video processing cancelled by user")
                    break

                # Initialize size_kb before try block to avoid UnboundLocalError
                # if exception occurs before it's assigned
                size_kb = 0

                try:
                    # Track folder
                    folder_path = os.path.dirname(str(video_path))
                    folders_seen.add(folder_path)

                    # Ensure folder exists and get folder_id (PROPER FIX)
                    folder_id = self._ensure_folder_hierarchy(video_path.parent, root_path, project_id)

                    # Get file stats
                    stat = os.stat(video_path)
                    size_kb = stat.st_size / 1024
                    modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                    # INCREMENTAL SCAN: Skip if unchanged (same logic as photos)
                    path_str = str(video_path)
                    normalized_path = self.photo_repo._normalize_path(path_str)
                    if skip_unchanged and existing_video_metadata.get(normalized_path) == modified:
                        self._stats['videos_skipped'] = self._stats.get('videos_skipped', 0) + 1
                        logger.debug(f"Skipping unchanged video: {video_path.name}")
                        continue

                    # CRITICAL FIX: Extract video creation date quickly during scan
                    # Try to get date_taken from video metadata (with timeout), fall back to modified
                    video_date_taken = self._quick_extract_video_date(video_path)
                    created_ts, created_date, created_year = self._compute_created_fields(video_date_taken, modified)

                    # Index video WITH date fields (using modified as fallback until workers extract date_taken)
                    print(f"[VIDEO_INDEX] Attempting to index: {os.path.basename(str(video_path))}")
                    print(f"[VIDEO_INDEX]   project_id={project_id}, folder_id={folder_id}")
                    print(f"[VIDEO_INDEX]   size_kb={size_kb}, modified={modified}")
                    print(f"[VIDEO_INDEX]   created_ts={created_ts}, created_date={created_date}, created_year={created_year}")
                    sys.stdout.flush()

                    video_id = video_service.index_video(
                        path=str(video_path),
                        project_id=project_id,
                        folder_id=folder_id,
                        size_kb=size_kb,
                        modified=modified,
                        created_ts=created_ts,
                        created_date=created_date,
                        created_year=created_year
                    )

                    if video_id:
                        print(f"[VIDEO_INDEX] SUCCESS: video_id={video_id}")
                        self._stats['videos_indexed'] += 1
                    else:
                        print(f"[VIDEO_INDEX] FAILED: video_service.index_video returned None")
                        logger.error(f"Video indexing returned None for {video_path}")
                    sys.stdout.flush()

                except Exception as e:
                    print(f"[VIDEO_INDEX] EXCEPTION: {type(e).__name__}: {e}")
                    logger.warning(f"Failed to index video {video_path}: {e}")
                    import traceback
                    traceback.print_exc()
                    sys.stdout.flush()

                # Report progress
                if progress_callback:
                    self._videos_processed = i
                    processed_media = self._photos_processed + self._videos_processed
                    percent = int((processed_media / max(1, self._total_media_files)) * 100)

                    progress = ScanProgress(
                        current=processed_media,
                        total=self._total_media_files,
                        percent=percent,
                        message=self._build_progress_message(
                            status_line=f"🎬 {video_path.name} ({size_kb:.0f} KB)",
                            current_path=video_path,
                            processed_count=processed_media,
                            total_count=self._total_media_files
                        ),
                        current_file=str(video_path)
                    )
                    self._last_progress_emit = time.time()
                    progress_callback(progress)

            logger.info(f"Indexed {self._stats['videos_indexed']} videos (metadata extraction pending)")

        except ImportError:
            logger.warning("VideoService not available, skipping video indexing")
        except Exception as e:
            logger.error(f"Error processing videos: {e}", exc_info=True)

    def _launch_video_workers(self, project_id: int, on_metadata_finished_callback=None):
        """
        Launch background workers for video metadata extraction and thumbnail generation.

        Args:
            project_id: Project ID for which to process videos
            on_metadata_finished_callback: Optional callback(success, failed) to call when metadata extraction finishes

        Returns:
            Tuple of (metadata_worker, thumbnail_worker) or (None, None) if failed
        """
        try:
            from PySide6.QtCore import QThreadPool
            from workers.video_metadata_worker import VideoMetadataWorker
            from workers.video_thumbnail_worker import VideoThumbnailWorker

            logger.info(f"Launching background workers for {self._stats['videos_indexed']} videos...")

            # Launch metadata extraction worker
            metadata_worker = VideoMetadataWorker(project_id=project_id)

            # Connect progress signals for UI feedback
            metadata_worker.signals.progress.connect(
                lambda curr, total, path: logger.info(f"[Metadata] Processing {curr}/{total}: {path}")
            )
            metadata_worker.signals.finished.connect(
                lambda success, failed: logger.info(f"[Metadata] Complete: {success} successful, {failed} failed")
            )

            # CRITICAL: Connect callback BEFORE starting worker to avoid race condition
            if on_metadata_finished_callback:
                metadata_worker.signals.finished.connect(on_metadata_finished_callback)
                logger.info("Connected metadata finished callback for sidebar refresh")

            QThreadPool.globalInstance().start(metadata_worker)
            logger.info("✓ Video metadata extraction worker started")

            # Launch thumbnail generation worker
            thumbnail_worker = VideoThumbnailWorker(project_id=project_id, thumbnail_height=200)

            # Connect progress signals for UI feedback
            thumbnail_worker.signals.progress.connect(
                lambda curr, total, path: logger.info(f"[Thumbnails] Generating {curr}/{total}: {path}")
            )
            thumbnail_worker.signals.finished.connect(
                lambda success, failed: logger.info(f"[Thumbnails] Complete: {success} successful, {failed} failed")
            )

            QThreadPool.globalInstance().start(thumbnail_worker)
            logger.info("✓ Video thumbnail generation worker started")

            # Store worker count for status
            logger.info(f"🎬 Processing {self._stats['videos_indexed']} videos in background (check logs for progress)")

            # Return workers so callers can connect to their signals
            return metadata_worker, thumbnail_worker

        except ImportError as e:
            logger.warning(f"Video workers not available: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Error launching video workers: {e}", exc_info=True)
            return None, None

    def _build_progress_message(self,
                                status_line: str,
                                current_path: Optional[Path],
                                processed_count: int,
                                total_count: int,
                                discovery: bool = False) -> str:
        """Generate a rich, multi-line progress string for the scan dialog."""
        safe_total = max(1, total_count or 0)
        percent = max(0, min(100, int((processed_count / safe_total) * 100)))
        elapsed = max(0.0, time.time() - self._scan_start_time) if self._scan_start_time else 0.0

        lines = []
        if self._scan_root:
            lines.append(f"📂 {self._scan_root}")

        if self._total_media_files:
            lines.append(
                f"Discovered: {self._total_photos} photos • {self._total_videos} videos (total {self._total_media_files})"
            )

        if discovery:
            lines.append("Preparing file system walk and metadata extraction…")

        lines.append(f"Progress: {processed_count}/{safe_total} files ({percent}%)")

        if status_line:
            lines.append(status_line)

        if current_path:
            lines.append(f"Path: {current_path}")

        lines.append(
            f"Indexed → Photos: {self._stats['photos_indexed']} | Videos: {self._stats['videos_indexed']}"
        )
        lines.append(
            f"Skipped: {self._stats['photos_skipped']} | Failed: {self._stats['photos_failed']}"
        )
        lines.append(f"Elapsed: {elapsed:.1f}s")
        lines.append("Tip: You can cancel safely; completed items stay indexed.")

        return "\n".join(lines)
