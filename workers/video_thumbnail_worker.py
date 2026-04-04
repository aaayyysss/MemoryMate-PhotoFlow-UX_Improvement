# workers/video_thumbnail_worker.py
# Version 1.0.0 dated 2025-11-09
# Background worker for generating video thumbnails

import sys
import os
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QObject, Signal, QRunnable, Slot
from services.video_thumbnail_service import VideoThumbnailService
from repository.video_repository import VideoRepository
from logging_config import get_logger

logger = get_logger(__name__)


class VideoThumbnailWorkerSignals(QObject):
    """
    Signals for video thumbnail generation worker.

    Signals:
        progress: (current, total, video_path) - Progress update
        finished: (success_count, failed_count) - Completion signal
        error: (video_path, error_message) - Error signal for individual video
        thumbnail_ready: (video_path, thumbnail_data) - Thumbnail generated successfully
    """
    progress = Signal(int, int, str)     # current, total, video_path
    finished = Signal(int, int)          # success_count, failed_count
    error = Signal(str, str)             # video_path, error_message
    thumbnail_ready = Signal(str, bytes) # video_path, thumbnail_data


class VideoThumbnailWorker(QRunnable):
    """
    Background worker for generating video thumbnails.

    Generates thumbnail images for videos with pending thumbnail_status.
    Extracts frame at 10% of video duration and stores in cache.

    Usage:
        worker = VideoThumbnailWorker(project_id=1)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)
    """

    def __init__(self, project_id: int, video_paths: list = None, thumbnail_height: int = 200):
        """
        Initialize thumbnail generation worker.

        Args:
            project_id: Project ID to process videos for
            video_paths: Optional list of specific video paths to process.
                        If None, processes all videos with pending thumbnails.
            thumbnail_height: Target height for thumbnails in pixels
        """
        super().__init__()
        self.project_id = project_id
        self.video_paths = video_paths
        self.thumbnail_height = thumbnail_height
        self.signals = VideoThumbnailWorkerSignals()
        self.cancelled = False

        self.thumbnail_service = VideoThumbnailService()
        self.video_repo = VideoRepository()

    def cancel(self):
        """Request cancellation of worker."""
        self.cancelled = True
        logger.info("[VideoThumbnailWorker] Cancellation requested")

    def _generate_thumbnail_for_video(self, video: dict) -> bool:
        """
        Generate thumbnail for a single video (called by worker threads).

        Args:
            video: Video dict from repository with 'id', 'path', etc.

        Returns:
            bool: True if successful, False otherwise
        """
        video_path = video['path']
        video_id = video['id']

        try:
            # Check if file exists
            if not os.path.exists(video_path):
                logger.warning(f"File not found: {video_path}")
                self.signals.error.emit(video_path, "File not found")
                return False

            # Generate thumbnail
            # generate_thumbnail() returns file path string, not bytes
            # It already saves the thumbnail to disk (no need for separate caching)
            thumbnail_path = self.thumbnail_service.generate_thumbnail(
                video_path,
                width=int(self.thumbnail_height * 4/3),  # Maintain 4:3 aspect ratio
                height=self.thumbnail_height
            )

            if not thumbnail_path:
                # Thumbnail generation failed
                self.video_repo.update(
                    video_id=video_id,
                    thumbnail_status='error'
                )
                error_msg = "Failed to generate thumbnail"
                self.signals.error.emit(video_path, error_msg)
                return False

            # Update database status
            self.video_repo.update(
                video_id=video_id,
                thumbnail_status='ok'
            )

            # Read thumbnail file as bytes for signal emission
            try:
                with open(thumbnail_path, 'rb') as f:
                    thumbnail_data = f.read()
                self.signals.thumbnail_ready.emit(video_path, thumbnail_data)
            except Exception as read_error:
                logger.warning(f"Generated thumbnail but couldn't read: {read_error}")
                # Still count as success since thumbnail file exists

            return True

        except Exception as e:
            # Error during generation
            error_msg = str(e)
            logger.error(f"Error processing {video_path}: {error_msg}")

            try:
                self.video_repo.update(
                    video_id=video_id,
                    thumbnail_status='error'
                )
            except Exception:
                pass

            self.signals.error.emit(video_path, error_msg)
            return False

    @Slot()
    def run(self):
        """
        Generate thumbnails for pending videos.

        Processes videos in project with thumbnail_status = 'pending' or 'error'.
        Generates thumbnails and caches them.
        """
        logger.info(f"[VideoThumbnailWorker] Starting for project_id={self.project_id}")

        success_count = 0
        failed_count = 0

        try:
            # Get list of videos to process
            if self.video_paths:
                # Process specific videos
                videos_to_process = []
                for path in self.video_paths:
                    video = self.video_repo.get_by_path(path, self.project_id)
                    if video:
                        videos_to_process.append(video)
            else:
                # Get all videos with pending thumbnails
                all_videos = self.video_repo.get_by_project(self.project_id)
                videos_to_process = [
                    v for v in all_videos
                    if v.get('thumbnail_status') in ('pending', 'error', None)
                ]

            total = len(videos_to_process)
            logger.info(f"[VideoThumbnailWorker] Found {total} videos to process")

            if total == 0:
                self.signals.finished.emit(0, 0)
                return

            # PERFORMANCE OPTIMIZATION: Process videos in parallel for 8x speedup
            # ffmpeg is I/O bound (waiting for subprocess), so threads work well
            from concurrent.futures import ThreadPoolExecutor, as_completed

            import os as _os
            max_workers = min(4, _os.cpu_count() or 4)
            logger.info(f"[VideoThumbnailWorker] Processing with {max_workers} parallel workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all thumbnail generation tasks
                futures = {}
                for video in videos_to_process:
                    if self.cancelled:
                        break
                    future = executor.submit(self._generate_thumbnail_for_video, video)
                    futures[future] = video

                # Process results as they complete
                for idx, future in enumerate(as_completed(futures), 1):
                    if self.cancelled:
                        logger.info("[VideoThumbnailWorker] Cancelled, shutting down workers")
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    video = futures[future]
                    video_path = video['path']

                    # Emit progress
                    self.signals.progress.emit(idx, total, video_path)

                    try:
                        success = future.result()
                        if success:
                            success_count += 1
                            logger.info(f"[VideoThumbnailWorker] ✓ {video_path}")
                        else:
                            failed_count += 1
                            logger.error(f"[VideoThumbnailWorker] ✗ {video_path}")

                    except Exception as e:
                        logger.error(f"[VideoThumbnailWorker] Error: {video_path}: {e}")
                        failed_count += 1

        except Exception as e:
            logger.error(f"[VideoThumbnailWorker] Fatal error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Emit completion signal
            self.signals.finished.emit(success_count, failed_count)
            logger.info(
                f"[VideoThumbnailWorker] Finished: {success_count} success, "
                f"{failed_count} failed"
            )


def main():
    """
    Standalone entry point for running worker as separate process.

    Usage:
        python workers/video_thumbnail_worker.py <project_id>
    """
    import sys
    from PySide6.QtCore import QCoreApplication, QThreadPool

    if len(sys.argv) < 2:
        print("Usage: python video_thumbnail_worker.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    app = QCoreApplication(sys.argv)

    # Create and run worker
    worker = VideoThumbnailWorker(project_id=project_id)

    def on_progress(current, total, path):
        print(f"[{current}/{total}] Processing: {os.path.basename(path)}")

    def on_finished(success, failed):
        print(f"\n✓ Completed: {success} success, {failed} failed")
        app.quit()

    def on_error(path, error):
        print(f"✗ Error: {os.path.basename(path)} - {error}")

    def on_thumbnail_ready(path, data):
        print(f"✓ Thumbnail ready: {os.path.basename(path)} ({len(data)} bytes)")

    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.error.connect(on_error)
    worker.signals.thumbnail_ready.connect(on_thumbnail_ready)

    # Start worker
    QThreadPool.globalInstance().start(worker)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
