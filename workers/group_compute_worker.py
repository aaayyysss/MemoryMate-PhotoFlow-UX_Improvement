# workers/group_compute_worker.py
# Version 1.0.0 dated 20260215
# Background worker for computing people group matches

"""
GroupComputeWorker - Background worker for group match computation

Runs group match computation in background thread with:
- Progress signals for UI updates
- Support for both 'together' and 'event_window' modes
- Integration with Activity Center
- Cancelable operation
"""

import logging
import time
from typing import Optional

from PySide6.QtCore import QRunnable, QObject, Signal, Slot

from reference_db import ReferenceDB
from services.people_group_service import PeopleGroupService

logger = logging.getLogger(__name__)


class GroupComputeSignals(QObject):
    """Signals for group computation progress."""

    # progress(current, total, message)
    progress = Signal(int, int, str)

    # finished(success, result_dict)
    finished = Signal(bool, dict)

    # error(error_message)
    error = Signal(str)


class GroupComputeWorker(QRunnable):
    """
    Background worker for computing group matches.

    Supports both match modes:
    - 'together': Find photos where ALL group members appear
    - 'event_window': Find photos within a time window where all members appear

    Usage:
        worker = GroupComputeWorker(project_id=1, group_id=5, match_mode='together')
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)
    """

    def __init__(
        self,
        project_id: int,
        group_id: int,
        match_mode: str = 'together',
        min_confidence: float = 0.5,
        window_seconds: int = 30,
        include_videos: bool = False
    ):
        """
        Initialize group compute worker.

        Args:
            project_id: Project ID
            group_id: Group ID to compute matches for
            match_mode: 'together' or 'event_window'
            min_confidence: Minimum face detection confidence
            window_seconds: Time window for event_window mode
            include_videos: Include video frames (future)
        """
        super().__init__()
        self.project_id = project_id
        self.group_id = group_id
        self.match_mode = match_mode
        self.min_confidence = min_confidence
        self.window_seconds = window_seconds
        self.include_videos = include_videos

        self.signals = GroupComputeSignals()
        self.cancelled = False

    def cancel(self):
        """Cancel the computation."""
        self.cancelled = True
        logger.info(f"[GroupComputeWorker] Cancellation requested for group {self.group_id}")

    @Slot()
    def run(self):
        """Execute the group match computation."""
        import threading
        _thread = threading.current_thread()
        logger.info(
            f"[GroupComputeWorker] Starting computation for group {self.group_id} "
            f"(mode={self.match_mode}, thread={_thread.name})"
        )

        start_time = time.time()

        try:
            db = ReferenceDB()
            service = PeopleGroupService(db)

            # Progress callback
            def on_progress(current, total, message):
                if not self.cancelled:
                    self.signals.progress.emit(current, total, message)

            # Run computation based on mode
            if self.match_mode == 'together':
                result = service.compute_together_matches(
                    project_id=self.project_id,
                    group_id=self.group_id,
                    min_confidence=self.min_confidence,
                    include_videos=self.include_videos,
                    progress_callback=on_progress
                )
            elif self.match_mode == 'event_window':
                result = service.compute_event_window_matches(
                    project_id=self.project_id,
                    group_id=self.group_id,
                    window_seconds=self.window_seconds,
                    min_confidence=self.min_confidence,
                    include_videos=self.include_videos,
                    progress_callback=on_progress
                )
            else:
                raise ValueError(f"Unknown match mode: {self.match_mode}")

            if self.cancelled:
                logger.info(f"[GroupComputeWorker] Cancelled for group {self.group_id}")
                self.signals.finished.emit(False, {'cancelled': True})
                return

            duration = time.time() - start_time
            result['duration_s'] = duration

            logger.info(
                f"[GroupComputeWorker] Completed for group {self.group_id}: "
                f"{result.get('match_count', 0)} matches in {duration:.2f}s"
            )

            self.signals.progress.emit(100, 100, "Complete")
            self.signals.finished.emit(result.get('success', False), result)

        except Exception as e:
            logger.error(f"[GroupComputeWorker] Error: {e}", exc_info=True)
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False, {'error': str(e)})


class BatchGroupComputeWorker(QRunnable):
    """
    Worker for computing matches for multiple groups.

    Useful for recomputing all stale groups after face detection completes.
    """

    def __init__(
        self,
        project_id: int,
        group_ids: Optional[list] = None,
        match_mode: str = 'together',
        min_confidence: float = 0.5,
        window_seconds: int = 30
    ):
        """
        Initialize batch compute worker.

        Args:
            project_id: Project ID
            group_ids: List of group IDs, or None for all stale groups
            match_mode: Match mode to use
            min_confidence: Minimum confidence
            window_seconds: Window for event mode
        """
        super().__init__()
        self.project_id = project_id
        self.group_ids = group_ids
        self.match_mode = match_mode
        self.min_confidence = min_confidence
        self.window_seconds = window_seconds

        self.signals = GroupComputeSignals()
        self.cancelled = False

    def cancel(self):
        """Cancel batch computation."""
        self.cancelled = True

    @Slot()
    def run(self):
        """Execute batch computation."""
        logger.info(f"[BatchGroupComputeWorker] Starting batch computation for project {self.project_id}")

        try:
            db = ReferenceDB()
            service = PeopleGroupService(db)

            # Get groups to process
            if self.group_ids is None:
                # Get all stale groups
                self.group_ids = service.get_stale_groups(self.project_id)

            if not self.group_ids:
                logger.info("[BatchGroupComputeWorker] No groups to process")
                self.signals.finished.emit(True, {'groups_processed': 0})
                return

            total_groups = len(self.group_ids)
            total_matches = 0
            errors = []

            for idx, group_id in enumerate(self.group_ids):
                if self.cancelled:
                    break

                self.signals.progress.emit(
                    idx + 1, total_groups,
                    f"Computing group {idx + 1}/{total_groups}..."
                )

                try:
                    if self.match_mode == 'together':
                        result = service.compute_together_matches(
                            project_id=self.project_id,
                            group_id=group_id,
                            min_confidence=self.min_confidence
                        )
                    else:
                        result = service.compute_event_window_matches(
                            project_id=self.project_id,
                            group_id=group_id,
                            window_seconds=self.window_seconds,
                            min_confidence=self.min_confidence
                        )

                    total_matches += result.get('match_count', 0)

                except Exception as e:
                    errors.append({'group_id': group_id, 'error': str(e)})
                    logger.error(f"[BatchGroupComputeWorker] Error for group {group_id}: {e}")

            result = {
                'groups_processed': len(self.group_ids),
                'total_matches': total_matches,
                'errors': errors
            }

            logger.info(f"[BatchGroupComputeWorker] Batch complete: {result}")
            self.signals.finished.emit(len(errors) == 0, result)

        except Exception as e:
            logger.error(f"[BatchGroupComputeWorker] Batch error: {e}", exc_info=True)
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False, {'error': str(e)})
