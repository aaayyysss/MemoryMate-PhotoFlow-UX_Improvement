# workers/video_metadata_worker.py
# Version 1.0.0 dated 2025-11-09
# Background worker for extracting video metadata

import sys
import os
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QObject, Signal, QRunnable, Slot
from services.video_metadata_service import VideoMetadataService
from repository.video_repository import VideoRepository
from logging_config import get_logger

logger = get_logger(__name__)


class VideoMetadataWorkerSignals(QObject):
    """
    Signals for video metadata extraction worker.

    Signals:
        progress: (current, total, video_path) - Progress update
        finished: (success_count, failed_count) - Completion signal
        error: (video_path, error_message) - Error signal for individual video
    """
    progress = Signal(int, int, str)  # current, total, video_path
    finished = Signal(int, int)        # success_count, failed_count
    error = Signal(str, str)           # video_path, error_message


class VideoMetadataWorker(QRunnable):
    """
    Background worker for extracting video metadata.

    Extracts metadata (duration, resolution, codecs, etc.) for videos
    with pending metadata_status. Runs in background thread pool.

    Usage:
        worker = VideoMetadataWorker(project_id=1)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)
    """

    def __init__(self, project_id: int, video_paths: list = None):
        """
        Initialize metadata extraction worker.

        Args:
            project_id: Project ID to process videos for
            video_paths: Optional list of specific video paths to process.
                        If None, processes all videos with pending metadata.
        """
        super().__init__()
        self.project_id = project_id
        self.video_paths = video_paths
        self.signals = VideoMetadataWorkerSignals()
        self.cancelled = False

        self.metadata_service = VideoMetadataService()
        self.video_repo = VideoRepository()

    def cancel(self):
        """Request cancellation of worker."""
        self.cancelled = True
        logger.info("[VideoMetadataWorker] Cancellation requested")

    def _extract_video_metadata(self, video: dict) -> bool:
        """
        Extract metadata for a single video (called by worker threads).

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

            # Extract metadata
            metadata = self.metadata_service.extract_metadata(video_path)

            if not metadata:
                # Metadata extraction failed
                self.video_repo.update(
                    video_id=video_id,
                    metadata_status='error'
                )
                error_msg = "Failed to extract metadata"
                self.signals.error.emit(video_path, error_msg)
                return False

            # Update database with extracted metadata
            # BUG FIX #6: Compute created_date, created_year, created_ts from date_taken
            # This enables efficient date hierarchy queries (matching photo metadata pattern)
            update_data = {
                'duration_seconds': metadata.get('duration_seconds'),
                'width': metadata.get('width'),
                'height': metadata.get('height'),
                'fps': metadata.get('fps'),
                'codec': metadata.get('codec'),
                'bitrate': metadata.get('bitrate'),
                'date_taken': metadata.get('date_taken'),
                'metadata_status': 'ok'
            }

            # Compute created_* fields from date_taken for date hierarchy
            date_taken = metadata.get('date_taken')
            if date_taken:
                try:
                    from datetime import datetime
                    # Parse date_taken (format: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD')
                    date_str = date_taken.split(' ')[0]  # Extract YYYY-MM-DD part
                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                    update_data['created_ts'] = int(dt.timestamp())
                    update_data['created_date'] = date_str  # YYYY-MM-DD
                    update_data['created_year'] = dt.year
                except (ValueError, AttributeError, IndexError):
                    # If date parsing fails, these fields will remain NULL
                    logger.debug(f"Failed to parse date_taken: {date_taken}")

            self.video_repo.update(video_id=video_id, **update_data)
            return True

        except Exception as e:
            # Error during extraction
            error_msg = str(e)
            logger.error(f"Error processing {video_path}: {error_msg}")

            try:
                self.video_repo.update(
                    video_id=video_id,
                    metadata_status='error'
                )
            except Exception:
                pass

            self.signals.error.emit(video_path, error_msg)
            return False

    @Slot()
    def run(self):
        """
        Extract metadata for pending videos.

        Processes videos in project with metadata_status = 'pending' or 'error'.
        Updates video_metadata table with extracted information.
        """
        logger.info(f"[VideoMetadataWorker] Starting for project_id={self.project_id}")

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
                # Get all videos with pending metadata
                all_videos = self.video_repo.get_by_project(self.project_id)
                videos_to_process = [
                    v for v in all_videos
                    if v.get('metadata_status') in ('pending', 'error', None)
                ]

            total = len(videos_to_process)
            logger.info(f"[VideoMetadataWorker] Found {total} videos to process")

            if total == 0:
                self.signals.finished.emit(0, 0)
                return

            # PERFORMANCE OPTIMIZATION: Process videos in parallel for 8x speedup
            # ffprobe is I/O bound (waiting for subprocess), so threads work well
            from concurrent.futures import ThreadPoolExecutor, as_completed

            import os as _os
            max_workers = min(4, _os.cpu_count() or 4)
            logger.info(f"[VideoMetadataWorker] Processing with {max_workers} parallel workers")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all extraction tasks
                futures = {}
                for video in videos_to_process:
                    if self.cancelled:
                        break
                    future = executor.submit(self._extract_video_metadata, video)
                    futures[future] = video

                # Process results as they complete
                for idx, future in enumerate(as_completed(futures), 1):
                    if self.cancelled:
                        logger.info("[VideoMetadataWorker] Cancelled, shutting down workers")
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
                            logger.info(f"[VideoMetadataWorker] ✓ {video_path}")
                        else:
                            failed_count += 1
                            logger.error(f"[VideoMetadataWorker] ✗ {video_path}")

                    except Exception as e:
                        logger.error(f"[VideoMetadataWorker] Error: {video_path}: {e}")
                        failed_count += 1

        except Exception as e:
            logger.error(f"[VideoMetadataWorker] Fatal error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Emit completion signal
            self.signals.finished.emit(success_count, failed_count)
            logger.info(
                f"[VideoMetadataWorker] Finished: {success_count} success, "
                f"{failed_count} failed"
            )


def main():
    """
    Standalone entry point for running worker as separate process.

    Usage:
        python workers/video_metadata_worker.py <project_id>
    """
    import sys
    from PySide6.QtCore import QCoreApplication, QThreadPool

    if len(sys.argv) < 2:
        print("Usage: python video_metadata_worker.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    app = QCoreApplication(sys.argv)

    # Create and run worker
    worker = VideoMetadataWorker(project_id=project_id)

    def on_progress(current, total, path):
        print(f"[{current}/{total}] Processing: {os.path.basename(path)}")

    def on_finished(success, failed):
        print(f"\n✓ Completed: {success} success, {failed} failed")
        app.quit()

    def on_error(path, error):
        print(f"✗ Error: {os.path.basename(path)} - {error}")

    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.error.connect(on_error)

    # Start worker
    QThreadPool.globalInstance().start(worker)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
