# face_detection_worker.py
# Phase 5: Face Detection Worker
# Background worker for detecting faces and generating embeddings
# Populates face_crops table with detected faces
# ------------------------------------------------------

import os
import time
import numpy as np
from typing import Optional, List  # FEATURE #1: Added List for photo_paths type hint
from PySide6.QtCore import QRunnable, QObject, Signal, Slot
import logging

from reference_db import ReferenceDB
from services.face_detection_service import get_face_detection_service
from config.face_detection_config import get_face_config
from services.performance_monitor import PerformanceMonitor
from utils.face_detection_logger import FaceDetectionLogger

logger = logging.getLogger(__name__)


class FaceDetectionSignals(QObject):
    """
    Signals for face detection worker progress reporting.
    """
    # progress(current, total, message)
    progress = Signal(int, int, str)

    # face_detected(image_path, face_count)
    face_detected = Signal(str, int)

    # Emitted after each batch DB commit so the UI can refresh incrementally
    # (processed, total, faces_so_far, project_id)
    batch_committed = Signal(int, int, int, int)

    # finished(success_count, failed_count, total_faces)
    finished = Signal(int, int, int)

    # error(image_path, error_message)
    error = Signal(str, str)


class FaceDetectionWorker(QRunnable):
    """
    Background worker for detecting faces in photos.

    Processes all photos in a project, detects faces, generates embeddings,
    and saves results to face_crops table.

    Usage:
        worker = FaceDetectionWorker(project_id=1)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)

    Performance:
        - Uses InsightFace buffalo_l model (RetinaFace detector + ArcFace embeddings)
        - Processes ~1-2 photos/second (depends on CPU/GPU)
        - Parallel processing NOT recommended (CPU-intensive)
        - For 1000 photos: ~10-15 minutes

    Features:
        - Skips photos already processed
        - Saves face crops to .memorymate/faces/
        - Generates 512-dim ArcFace embeddings (vs 128-dim dlib)
        - Error handling and progress reporting
    """

    def __init__(self, project_id: int, model: str = "buffalo_l",
                 skip_processed: bool = True, max_faces_per_photo: int = 10,
                 photo_paths: Optional[List[str]] = None,
                 screenshot_policy: str = "detect_only",
                 include_all_screenshot_faces: bool = False):
        """
        Initialize face detection worker.

        Args:
            project_id: Project ID to process photos for
            model: InsightFace model name ("buffalo_l", "buffalo_s", "antelopev2")
                   Default: "buffalo_l" (high accuracy, 512-D ArcFace embeddings)
            skip_processed: Skip photos already in face_crops table
            max_faces_per_photo: Maximum faces to detect per photo (prevent memory issues)
            photo_paths: FEATURE #1: Optional list of specific photo paths to process
                        If None, processes all photos in project
        """
        super().__init__()
        self.project_id = project_id
        self.model = model
        self.skip_processed = skip_processed
        self.max_faces_per_photo = max_faces_per_photo
        self.photo_paths = photo_paths  # FEATURE #1: Store selected photo paths
        self.screenshot_policy = screenshot_policy
        self.include_all_screenshot_faces = include_all_screenshot_faces
        self.signals = FaceDetectionSignals()
        self.cancelled = False

        # Statistics
        self._stats = {
            'photos_processed': 0,
            'photos_skipped': 0,
            'photos_failed': 0,
            'faces_detected': 0,
            'images_with_faces': 0,
            'videos_excluded': 0  # Track videos excluded from face detection
        }

    def cancel(self):
        """Cancel the detection process."""
        self.cancelled = True
        logger.info("[FaceDetectionWorker] Cancellation requested")

    @Slot()
    def run(self):
        """Main worker execution."""
        import threading
        _thread = threading.current_thread()
        _is_main = _thread is threading.main_thread()
        logger.info(
            "[FaceDetectionWorker] Starting face detection for project %d "
            "(thread=%s, is_main=%s)",
            self.project_id, _thread.name, _is_main,
        )
        self.start_time = time.time()

        # Initialize performance monitoring
        monitor = PerformanceMonitor(f"face_detection_project_{self.project_id}")

        # ENHANCEMENT (2026-01-07): Initialize structured logging
        structured_logger = FaceDetectionLogger(self.project_id)

        try:
            # Initialize services
            db = ReferenceDB()

            # CRITICAL FIX: Wrap face service initialization in try/except to prevent app crash
            # If InsightFace fails to load, we should fail gracefully instead of crashing
            metric_init = monitor.record_operation("initialize_face_service", {
                "model": self.model
            })
            try:
                face_service = get_face_detection_service(model=self.model)
                metric_init.finish()
            except Exception as init_error:
                metric_init.finish(success=False, error=str(init_error))
                logger.error(f"❌ Failed to initialize face detection service: {init_error}")
                logger.error("Face detection cannot proceed. Please check:")
                logger.error("  1. InsightFace model files are present and valid")
                logger.error("  2. InsightFace library is properly installed")
                logger.error("  3. Model path is correctly configured in Preferences")
                self.signals.finished.emit(0, 0, 0)
                return

            # FEATURE #1: Get photos to process (either from scope selection or all project photos)
            metric_get_photos = monitor.record_operation("get_photos_to_process")
            if self.photo_paths is not None:
                # PROJECT_SCOPE_SEAL: Validate provided paths belong to this project and are NOT videos.
                # This prevents processing paths from another project if the UI state is stale.
                VIDEO_EXTENSIONS = (
                    '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm',
                    '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv'
                )
                video_filter = " AND " + " AND ".join(
                    [f"LOWER(image_path) NOT LIKE '%{ext}'" for ext in VIDEO_EXTENSIONS]
                )

                valid_paths = []
                with db._connect() as conn:
                    batch_size = 500
                    for i in range(0, len(self.photo_paths), batch_size):
                        batch = self.photo_paths[i:i + batch_size]
                        placeholders = ','.join(['?'] * len(batch))
                        # Use project_images to ensure project membership
                        cur = conn.execute(f"""
                            SELECT DISTINCT image_path FROM project_images
                            WHERE project_id = ? AND image_path IN ({placeholders})
                            {video_filter}
                        """, (self.project_id, *batch))
                        valid_paths.extend([row[0] for row in cur.fetchall()])

                import os
                normalized = []
                seen = set()
                for p in valid_paths:
                    np = os.path.normcase(os.path.normpath(p))
                    if np in seen:
                        continue
                    seen.add(np)
                    normalized.append(p)
                valid_paths = normalized

                dropped = len(self.photo_paths) - len(valid_paths)
                log_fn = logger.warning if dropped > 0 else logger.info
                log_fn(
                    "[PROJECT_SCOPE_SEAL][WORKER] requested=%d validated=%d dropped=%d project=%d",
                    len(self.photo_paths), len(valid_paths), dropped, self.project_id
                )

                photos = [{"path": path} for path in valid_paths]
                logger.info(f"[FaceDetectionWorker] Using validated scoped photos: {len(photos)} photos")
            else:
                # Query all photos for this project
                photos = self._get_photos_to_process(db)
                logger.info(f"[FaceDetectionWorker] Processing all project photos")
            metric_get_photos.finish()
            total_photos = len(photos)

            if total_photos == 0:
                logger.info("[FaceDetectionWorker] No photos to process")
                self.signals.finished.emit(0, 0, 0)
                return

            logger.info(f"[FaceDetectionWorker] Processing {total_photos} photos")

            # Create face crops directory
            from app_env import app_path
            face_crops_dir = app_path(".memorymate", "faces")
            os.makedirs(face_crops_dir, exist_ok=True)

            # ENHANCEMENT (2026-01-07): Log detection start with parameters
            cfg = get_face_config()
            structured_logger.log_detection_start({
                "model": self.model,
                "skip_processed": self.skip_processed,
                "max_faces_per_photo": self.max_faces_per_photo,
                "total_photos": total_photos,
                "confidence_threshold": cfg.get('confidence_threshold', 0.65),
                "min_face_size": cfg.get('min_face_size', 20)
            })

            # ENHANCEMENT (2026-01-07): GPU Batch Processing
            # Determine if we should use GPU batch processing
            enable_batch = (
                cfg.get('enable_gpu_batch', True) and
                face_service.has_gpu() and
                total_photos >= cfg.get('gpu_batch_min_photos', 10)
            )

            if enable_batch:
                batch_size = cfg.get('gpu_batch_size', 4)
                logger.info(
                    f"[FaceDetectionWorker] 🚀 GPU batch processing enabled: "
                    f"{total_photos} photos, batch_size={batch_size}"
                )
            else:
                logger.info(
                    f"[FaceDetectionWorker] 💻 Sequential processing: "
                    f"GPU={'available' if face_service.has_gpu() else 'not available'}, "
                    f"photos={total_photos}, "
                    f"batch_enabled={cfg.get('enable_gpu_batch', True)}"
                )

            # Process photos using batch or sequential processing
            metric_process = monitor.record_operation("process_all_photos", {
                "total_photos": total_photos,
                "skip_processed": self.skip_processed,
                "batch_processing": enable_batch
            })

            if enable_batch:
                # GPU BATCH PROCESSING PATH
                self._process_photos_batch(
                    photos, face_service, db, face_crops_dir,
                    structured_logger, monitor, batch_size
                )
            else:
                # SEQUENTIAL PROCESSING PATH (original code)
                self._process_photos_sequential(
                    photos, face_service, db, face_crops_dir,
                    structured_logger, monitor
                )

            metric_process.finish()

            # Cleanup and emit completion
            self._finalize_processing(db, monitor, structured_logger)

        except Exception as e:
            logger.error(f"[FaceDetectionWorker] Fatal error: {e}", exc_info=True)
            self.signals.finished.emit(0, 0, 0)

    def _process_photos_sequential(self, photos, face_service, db, face_crops_dir,
                                   structured_logger, monitor):
        """
        Sequential photo processing with batch DB writes.

        BEST PRACTICE (2026-02-01):
        - Keeps single DB connection open across all photos
        - Commits in batches (every N photos) instead of per-face
        - Micro-yields between photos for UI responsiveness
        - Reduces DB lock churn and improves throughput
        """
        cfg = get_face_config()
        batch_size = int(cfg.get('batch_size', 50))
        ui_yield_ms = cfg.get('ui_yield_ms', 1)  # Micro-yield for UI responsiveness

        total_photos = len(photos)
        pending_rows = []  # Accumulate face rows for batch insert

        # Throttle progress emissions — at most every 0.25 s or every 5 photos
        _PROGRESS_INTERVAL_S = 0.25
        _PROGRESS_INTERVAL_N = 5
        _last_progress_t = 0.0

        def _should_emit_progress(idx):
            nonlocal _last_progress_t
            now = time.time()
            if (now - _last_progress_t >= _PROGRESS_INTERVAL_S
                    or idx % _PROGRESS_INTERVAL_N == 0
                    or idx == total_photos):
                _last_progress_t = now
                return True
            return False

        # Keep single connection open for all batches
        with db._connect() as conn:
            for idx, photo in enumerate(photos, 1):
                if self.cancelled:
                    # Flush pending rows before exit
                    if pending_rows:
                        self._save_faces_batch(conn, pending_rows)
                        pending_rows.clear()
                    logger.info("[FaceDetectionWorker] Cancelled by user")
                    break

                photo_path = photo['path']
                photo_filename = os.path.basename(photo_path)

                # Detect faces
                photo_start_time = time.time()
                try:
                    # Always classify screenshot status.
                    # Policy controls handling, not classification.
                    is_screenshot = self._is_photo_screenshot(photo_path, photo_filename, conn)

                    if is_screenshot and self.screenshot_policy == "exclude":
                        self._stats['photos_processed'] += 1
                        self._stats['photos_skipped'] += 1
                        logger.debug(f"[FaceDetectionWorker] Skipping screenshot: {photo_path}")
                        continue

                    faces = face_service.detect_faces(photo_path, project_id=self.project_id)
                    photo_duration_ms = (time.time() - photo_start_time) * 1000

                    if not faces:
                        self._stats['photos_processed'] += 1
                        structured_logger.log_photo_processed(photo_path, 0, photo_duration_ms, success=True)
                    else:
                        # Limit faces per photo with screenshot-policy awareness.
                        limit = self.max_faces_per_photo

                        if is_screenshot:
                            if self.screenshot_policy == "exclude":
                                limit = 0
                            elif self.screenshot_policy == "detect_only":
                                limit = min(limit, 4)
                            elif self.screenshot_policy == "include_cluster":
                                # ZERO TRUNCATION: if include_all is on, we take everything
                                if getattr(self, "include_all_screenshot_faces", False):
                                    limit = len(faces)
                                else:
                                    limit = min(limit, 8)
                            else:
                                limit = min(limit, 4)

                        if limit == 0:
                            faces = []
                        elif len(faces) > limit:
                            log_msg = (
                                f"[FaceDetectionWorker] {photo_path} has {len(faces)} faces "
                                f"(screenshot={is_screenshot}, policy={self.screenshot_policy}, "
                                f"include_all={getattr(self, 'include_all_screenshot_faces', False)}), "
                                f"keeping largest {limit}"
                            )
                            if is_screenshot and len(faces) > 10:
                                logger.warning(f"Screenshot-heavy image: {log_msg}")
                            else:
                                logger.info(log_msg)

                            faces = sorted(
                                faces,
                                key=lambda f: f['bbox_w'] * f['bbox_h'],
                                reverse=True
                            )
                            faces = faces[:limit]

                        # Prepare face rows for batch insert (saves crops to disk)
                        faces_saved = 0
                        for face_idx, face in enumerate(faces):
                            row = self._prepare_face_row(photo_path, face, face_idx, face_crops_dir)
                            if row:
                                pending_rows.append(row)
                                faces_saved += 1

                        self._stats['photos_processed'] += 1
                        self._stats['faces_detected'] += faces_saved
                        if faces_saved > 0:
                            self._stats['images_with_faces'] += 1

                        self.signals.face_detected.emit(photo_path, faces_saved)
                        structured_logger.log_photo_processed(photo_path, faces_saved, photo_duration_ms, success=True)
                        logger.debug(f"[FaceDetectionWorker] {photo_path}: {faces_saved} faces")

                except Exception as e:
                    self._stats['photos_failed'] += 1
                    error_msg = str(e)
                    photo_duration_ms = (time.time() - photo_start_time) * 1000

                    import traceback
                    traceback_str = traceback.format_exc()
                    structured_logger.log_error(
                        photo_path,
                        type(e).__name__,
                        error_msg,
                        traceback_str
                    )

                    logger.error(f"[FaceDetectionWorker] {photo_path}: {error_msg}")
                    self.signals.error.emit(photo_path, error_msg)

                # Throttled progress emission (max every 0.25 s / 5 photos)
                if _should_emit_progress(idx):
                    percentage = int((idx / total_photos) * 100)
                    self.signals.progress.emit(
                        idx, total_photos,
                        f"[{idx}/{total_photos}] ({percentage}%) "
                        f"Detecting faces: {photo_filename} | "
                        f"Found: {self._stats['faces_detected']} faces"
                    )

                # Batch commit every N photos (not every face!)
                if idx % batch_size == 0 and pending_rows:
                    saved = self._save_faces_batch(conn, pending_rows)
                    logger.debug(f"[FaceDetectionWorker] Batch commit: {saved} faces saved")
                    pending_rows.clear()
                    # Signal for incremental UI refresh
                    self.signals.batch_committed.emit(
                        idx, total_photos, self._stats['faces_detected'], self.project_id
                    )

                # Micro-yield for UI responsiveness (prevents UI freeze on CPU-heavy workloads)
                if ui_yield_ms > 0:
                    time.sleep(ui_yield_ms / 1000.0)

            # Final flush of any remaining rows
            if pending_rows:
                saved = self._save_faces_batch(conn, pending_rows)
                logger.debug(f"[FaceDetectionWorker] Final batch commit: {saved} faces saved")
                self.signals.batch_committed.emit(
                    total_photos, total_photos, self._stats['faces_detected'], self.project_id
                )

    def _process_photos_batch(self, photos, face_service, db, face_crops_dir,
                             structured_logger, monitor, batch_size):
        """
        GPU batch processing (ENHANCEMENT 2026-01-07).

        Processes multiple photos in parallel GPU batches for 2-5x speedup.
        """
        total_photos = len(photos)
        photo_paths = [photo['path'] for photo in photos]

        # Process photos in batches
        for batch_start in range(0, total_photos, batch_size):
            if self.cancelled:
                logger.info("[FaceDetectionWorker] Cancelled by user")
                break

            batch_end = min(batch_start + batch_size, total_photos)
            batch_paths = photo_paths[batch_start:batch_end]
            batch_idx = batch_start // batch_size + 1

            # Calculate batch progress
            idx = batch_end
            percentage = int((idx / total_photos) * 100)

            # Emit batch progress
            progress_msg = (
                f"[Batch {batch_idx}] Processing {len(batch_paths)} photos "
                f"({percentage}% complete) | "
                f"Found: {self._stats['faces_detected']} faces so far"
            )
            self.signals.progress.emit(idx, total_photos, progress_msg)

            # Batch detect faces
            batch_start_time = time.time()
            try:
                # Apply screenshot policy before batch detection.
                candidate_paths = batch_paths
                effective_batch_paths = []
                screenshot_status = {}  # path -> is_screenshot

                with db._connect() as conn:
                    for p in candidate_paths:
                        is_screen = self._is_photo_screenshot(p, os.path.basename(p), conn)
                        screenshot_status[p] = is_screen

                        if is_screen and self.screenshot_policy == "exclude":
                            self._stats['photos_processed'] += 1
                            self._stats['photos_skipped'] += 1
                            logger.debug(f"[FaceDetectionWorker] Skipping screenshot (batch): {p}")
                            continue

                        effective_batch_paths.append(p)

                if not effective_batch_paths:
                    continue

                results = face_service.batch_detect_faces(
                    effective_batch_paths,
                    batch_size=len(effective_batch_paths),
                    project_id=self.project_id
                )
                batch_duration_ms = (time.time() - batch_start_time) * 1000

                # Process results for each photo
                for photo_path in batch_paths:
                    faces = results.get(photo_path, [])
                    photo_filename = os.path.basename(photo_path)

                    if not faces:
                        self._stats["photos_processed"] += 1
                        structured_logger.log_photo_processed(photo_path, 0, batch_duration_ms / len(batch_paths), success=True)
                        continue

                    # Limit faces per photo with screenshot-policy awareness
                    is_screenshot = screenshot_status.get(photo_path, False)
                    limit = self.max_faces_per_photo

                    if is_screenshot:
                        if self.screenshot_policy == "exclude":
                            limit = 0
                        elif self.screenshot_policy == "detect_only":
                            limit = min(limit, 4)
                        elif self.screenshot_policy == "include_cluster":
                            if getattr(self, "include_all_screenshot_faces", False):
                                limit = len(faces)
                            else:
                                limit = min(limit, 8)
                        else:
                            limit = min(limit, 4)

                    if limit == 0:
                        faces = []
                    elif len(faces) > limit:
                        log_msg = (
                            f"[FaceDetectionWorker] {photo_path} has {len(faces)} faces "
                            f"(screenshot={is_screenshot}, policy={self.screenshot_policy}, "
                            f"include_all={getattr(self, 'include_all_screenshot_faces', False)}), "
                            f"keeping largest {limit}"
                        )
                        if is_screenshot and len(faces) > 10:
                            logger.warning(f"Screenshot-heavy image: {log_msg}")
                        else:
                            logger.info(log_msg)

                        faces = sorted(
                            faces,
                            key=lambda f: f['bbox_w'] * f['bbox_h'],
                            reverse=True
                        )
                        faces = faces[:limit]

                    # Save faces to database
                    for face_idx, face in enumerate(faces):
                        self._save_face(db, photo_path, face, face_idx, face_crops_dir)

                    self._stats['photos_processed'] += 1
                    self._stats['faces_detected'] += len(faces)
                    self._stats['images_with_faces'] += 1

                    self.signals.face_detected.emit(photo_path, len(faces))
                    structured_logger.log_photo_processed(photo_path, len(faces), batch_duration_ms / len(batch_paths), success=True)

                    logger.info(f"[FaceDetectionWorker] ✓ {photo_filename}: {len(faces)} faces")

                # Update progress after batch complete
                faces_in_batch = sum(len(results.get(path, [])) for path in batch_paths)
                self.signals.progress.emit(
                    idx, total_photos,
                    f"[Batch {batch_idx}] Complete: {faces_in_batch} faces found | Total: {self._stats['faces_detected']} faces"
                )

            except Exception as e:
                # Fall back to sequential processing for this batch
                logger.warning(f"[FaceDetectionWorker] Batch {batch_idx} failed, falling back to sequential: {e}")
                for photo_path in batch_paths:
                    if self.cancelled:
                        break
                    try:
                        faces = face_service.detect_faces(photo_path, project_id=self.project_id)
                        # Process as in sequential method
                        if faces:
                            if len(faces) > self.max_faces_per_photo:
                                faces = sorted(faces, key=lambda f: f['bbox_w'] * f['bbox_h'], reverse=True)[:self.max_faces_per_photo]
                            for face_idx, face in enumerate(faces):
                                self._save_face(db, photo_path, face, face_idx, face_crops_dir)
                            self._stats['photos_processed'] += 1
                            self._stats['faces_detected'] += len(faces)
                            self._stats['images_with_faces'] += 1
                        else:
                            self._stats['photos_processed'] += 1
                    except Exception as photo_error:
                        self._stats['photos_failed'] += 1
                        logger.error(f"[FaceDetectionWorker] ✗ {photo_path}: {photo_error}")

    def _is_photo_screenshot(self, photo_path, photo_filename, conn):
        """
        Detect if a photo is a screenshot using filename and metadata.
        """
        # Basic screenshot detection by filename
        basename = photo_filename.lower()
        SCREENSHOT_MARKERS = ["screenshot", "screen shot", "screen_shot", "screen-shot", "bildschirmfoto"]
        if any(m in basename for m in SCREENSHOT_MARKERS):
            return True

        # Check metadata for dimensions/flag
        try:
            cur = conn.cursor()
            cur.execute("SELECT is_screenshot, width, height FROM photo_metadata WHERE path = ?", (photo_path,))
            row = cur.fetchone()
            if row:
                if row[0]:  # is_screenshot flag from metadata
                    return True
                else:
                    w, h = row[1], row[2]
                    if w and h:
                        # Aspect ratio and size typical for phone screenshots
                        aspect = max(w, h) / max(1, min(w, h))
                        if 1.5 <= aspect <= 2.4 and 700 <= min(w, h) <= 1800:
                            return True
        except Exception:
            pass
        return False

    def _finalize_processing(self, db, monitor, structured_logger):
        """Finalize face detection processing and emit completion signals."""
        monitor.finish_monitoring()
        duration = time.time() - self.start_time
        logger.info(
            f"[FaceDetectionWorker] Complete in {duration:.1f}s: "
            f"{self._stats['photos_processed']} processed "
            f"({self._stats['images_with_faces']} with faces), "
            f"{self._stats['photos_skipped']} skipped, "
            f"{self._stats['faces_detected']} faces detected, "
            f"{self._stats['photos_failed']} failed"
        )

        # Log videos excluded (if any)
        if self._stats.get('videos_excluded', 0) > 0:
            logger.info(
                f"[FaceDetectionWorker] Note: {self._stats['videos_excluded']} video files were excluded "
                f"(face detection only scans photos)"
            )

        # Print performance summary
        print("\n")
        monitor.print_summary()

        # ENHANCEMENT (2026-01-07): Log detection completion
        structured_logger.log_detection_complete(self._stats)
        logger.info(f"[FaceDetectionWorker] Session log saved: {structured_logger.get_log_file_path()}")

        self.signals.finished.emit(
            self._stats['photos_processed'],
            self._stats['photos_failed'],
            self._stats['faces_detected']
        )

    def _get_photos_to_process(self, db: ReferenceDB) -> list:
        """
        Get list of photos to process.

        Returns photos that haven't been processed yet (if skip_processed=True).

        CRITICAL FIX: Uses project_images table to respect project hierarchy.
        Only processes photos that are actually linked to this project via project_images.

        IMPORTANT: Excludes video files (.mp4, .mov, .avi, .mkv, etc.) from face detection.
        Videos are stored in separate tables and should not be processed as photos.
        """
        # Video file extensions to exclude from face detection
        VIDEO_EXTENSIONS = (
            '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm',
            '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv'
        )

        with db._connect() as conn:
            cur = conn.cursor()

            # Count total items in project (including videos)
            cur.execute("""
                SELECT COUNT(DISTINCT pi.image_path)
                FROM project_images pi
                WHERE pi.project_id = ?
            """, (self.project_id,))
            total_items = cur.fetchone()[0]

            # SOLID FIX: Get total photo count from project_images (not photo_metadata.project_id)
            # FILTER: Exclude video files by extension
            video_filter = " AND " + " AND ".join(
                [f"LOWER(pi.image_path) NOT LIKE '%{ext}'" for ext in VIDEO_EXTENSIONS]
            )

            cur.execute(f"""
                SELECT COUNT(DISTINCT pi.image_path)
                FROM project_images pi
                WHERE pi.project_id = ?
                {video_filter}
            """, (self.project_id,))
            total_count = cur.fetchone()[0]

            # Calculate videos excluded
            videos_excluded = total_items - total_count
            if videos_excluded > 0:
                self._stats['videos_excluded'] = videos_excluded

                # ENHANCEMENT (2026-01-07): User-visible notification
                # Inform users why videos are excluded from face detection
                notification_msg = (
                    f"ℹ️  Note: {videos_excluded} video file(s) excluded from face detection. "
                    f"Face detection currently supports photos only. Processing {total_count} photo(s)..."
                )

                # Emit progress signal for UI notification
                self.signals.progress.emit(0, max(1, total_count), notification_msg)

                # Also log for debugging
                logger.info(
                    f"[FaceDetectionWorker] Excluding {videos_excluded} video files from face detection "
                    f"(processing {total_count} photos only)"
                )

            if self.skip_processed:
                # SOLID FIX: Get photos not in face_crops table, using project_images JOIN
                # FILTER: Exclude video files
                cur.execute(f"""
                    SELECT DISTINCT pi.image_path
                    FROM project_images pi
                    WHERE pi.project_id = ?
                      {video_filter}
                      AND pi.image_path NOT IN (
                          SELECT DISTINCT image_path
                          FROM face_crops
                          WHERE project_id = ?
                      )
                    ORDER BY pi.image_path
                """, (self.project_id, self.project_id))

                photos = [{'path': row[0], 'project_id': self.project_id} for row in cur.fetchall()]
                skipped_count = total_count - len(photos)

                if skipped_count > 0:
                    self._stats['photos_skipped'] = skipped_count
                    logger.info(
                        f"[FaceDetectionWorker] Skipping {skipped_count} photos already in database "
                        f"(processing {len(photos)}/{total_count})"
                    )

                return photos
            else:
                # SOLID FIX: Get all photos from project_images (not photo_metadata)
                # FILTER: Exclude video files
                cur.execute(f"""
                    SELECT DISTINCT pi.image_path
                    FROM project_images pi
                    WHERE pi.project_id = ?
                    {video_filter}
                    ORDER BY pi.image_path
                """, (self.project_id,))

                photos = [{'path': row[0], 'project_id': self.project_id} for row in cur.fetchall()]
                logger.info(f"[FaceDetectionWorker] Processing all {len(photos)} photos in project {self.project_id} (videos excluded)")
                return photos

    def _prepare_face_row(self, image_path: str, face: dict, face_idx: int,
                          face_crops_dir: str) -> Optional[tuple]:
        """
        Prepare face data for batch insert (saves crop, returns DB row tuple).

        BEST PRACTICE: Separates disk I/O from DB writes for batch efficiency.
        Called for each face, accumulates rows, then batch-commits to DB.

        Quality gate: Rejects faces that are too small to produce useful
        embeddings, reducing singleton clusters during DBSCAN.  Faces smaller
        than MIN_FACE_AREA_PX (default 40x40 = 1600 px²) are skipped since
        their embeddings carry insufficient signal for reliable clustering.

        Args:
            image_path: Original photo path
            face: Face dictionary with bbox and embedding
            face_idx: Face index in photo (for naming)
            face_crops_dir: Directory to save face crops

        Returns:
            tuple: Row data for INSERT, or None if face cannot be saved
        """
        # Validate embedding exists
        if face.get('embedding') is None:
            logger.warning(
                f"⚠️  Skipping face for {os.path.basename(image_path)} face#{face_idx}: "
                f"No embedding (detection-only mode)."
            )
            return None

        # ── Quality gate: reject tiny / low-confidence faces ──────────
        # Tiny faces produce noisy embeddings → DBSCAN singletons.
        # Threshold: 40x40 px = 1600 px² (configurable).
        MIN_FACE_AREA_PX = 1600  # 40x40 pixels minimum
        MIN_EMBEDDING_CONFIDENCE = 0.50  # below this, embedding is unreliable

        bbox_w = face.get('bbox_w', 0)
        bbox_h = face.get('bbox_h', 0)
        face_area = bbox_w * bbox_h
        confidence = face.get('confidence', 0.0)

        if face_area < MIN_FACE_AREA_PX:
            logger.debug(
                f"[FaceDetectionWorker] Skipping tiny face in {os.path.basename(image_path)} "
                f"face#{face_idx}: {bbox_w}x{bbox_h}={face_area}px² < {MIN_FACE_AREA_PX}px²"
            )
            return None

        if confidence < MIN_EMBEDDING_CONFIDENCE:
            logger.debug(
                f"[FaceDetectionWorker] Skipping low-confidence face in {os.path.basename(image_path)} "
                f"face#{face_idx}: conf={confidence:.2f} < {MIN_EMBEDDING_CONFIDENCE}"
            )
            return None

        # Validate embedding size (must be 512-dim float32 = 2048 bytes)
        embedding = face.get('embedding')
        if embedding is not None and hasattr(embedding, '__len__') and len(embedding) != 512:
            logger.warning(
                f"[FaceDetectionWorker] Skipping face with invalid embedding size in "
                f"{os.path.basename(image_path)} face#{face_idx}: "
                f"got {len(embedding)} dims, expected 512"
            )
            return None

        try:
            # Generate crop path
            image_basename = os.path.splitext(os.path.basename(image_path))[0]
            crop_filename = f"{image_basename}_face{face_idx}.jpg"
            crop_path = os.path.join(face_crops_dir, crop_filename)

            # Save face crop to disk
            face_service = get_face_detection_service()
            if get_face_config().get('save_face_crops', True):
                if not face_service.save_face_crop(image_path, face, crop_path):
                    logger.warning(f"Failed to save face crop: {crop_path}")
                    return None
            else:
                os.makedirs(os.path.dirname(crop_path), exist_ok=True)

            # Convert embedding to bytes
            embedding_bytes = face['embedding'].astype(np.float32).tobytes()

            # Return row tuple for batch insert
            return (
                self.project_id,
                image_path,
                crop_path,
                embedding_bytes,
                face['bbox_x'],
                face['bbox_y'],
                face['bbox_w'],
                face['bbox_h'],
                face['confidence']
            )
        except Exception as e:
            logger.error(f"Failed to prepare face row: {e}")
            return None

    def _save_faces_batch(self, conn, rows: list) -> int:
        """
        Batch insert face rows in a single transaction.

        BEST PRACTICE: Short transaction, predictable commit cadence.
        Much faster than per-face commits and reduces lock churn.

        Args:
            conn: Database connection (keep open across batches)
            rows: List of row tuples from _prepare_face_row()

        Returns:
            int: Number of rows successfully inserted
        """
        if not rows:
            return 0

        try:
            cur = conn.cursor()
            cur.executemany("""
                INSERT OR REPLACE INTO face_crops (
                    project_id, image_path, crop_path, embedding,
                    bbox_x, bbox_y, bbox_w, bbox_h, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
            return len(rows)
        except Exception as e:
            logger.error(f"Batch insert failed: {e}")
            conn.rollback()
            return 0

    def _save_face(self, db: ReferenceDB, image_path: str, face: dict,
                   face_idx: int, face_crops_dir: str):
        """
        Save detected face to database and disk using transactional approach.

        LEGACY METHOD: Kept for compatibility. New code should use
        _prepare_face_row() + _save_faces_batch() for better performance.

        CRITICAL ENHANCEMENT (2026-01-07):
        Uses atomic transaction to prevent orphaned face crops.
        If database save fails, crop file is rolled back (deleted).

        Args:
            db: Database instance
            image_path: Original photo path
            face: Face dictionary with bbox and embedding
            face_idx: Face index in photo (for naming)
            face_crops_dir: Directory to save face crops

        Returns:
            bool: True if saved successfully, False otherwise
        """
        crop_path = None
        try:
            # CRITICAL FIX: Validate embedding exists before saving
            # In detection-only mode (PyInstaller fallback), embeddings may be None
            # This prevents crash: 'NoneType' object has no attribute 'astype'
            if face.get('embedding') is None:
                logger.warning(
                    f"⚠️  Skipping face save for {os.path.basename(image_path)} face#{face_idx}: "
                    f"No embedding available (detection-only mode). "
                    f"Face was detected but cannot be saved for clustering."
                )
                # Cannot save to database without embedding
                # Face clustering requires embeddings for grouping
                return False

            # Generate crop filename
            image_basename = os.path.splitext(os.path.basename(image_path))[0]
            crop_filename = f"{image_basename}_face{face_idx}.jpg"
            crop_path = os.path.join(face_crops_dir, crop_filename)

            # STEP 1: Save face crop to disk
            face_service = get_face_detection_service()
            if get_face_config().get('save_face_crops', True):
                if not face_service.save_face_crop(image_path, face, crop_path):
                    logger.warning(f"Failed to save face crop: {crop_path}")
                    return False
            else:
                os.makedirs(os.path.dirname(crop_path), exist_ok=True)

            # STEP 2: Convert embedding to bytes for storage
            # At this point, we've validated that embedding is not None
            embedding_bytes = face['embedding'].astype(np.float32).tobytes()

            # STEP 3: Save to database with transactional rollback
            try:
                with db._connect() as conn:
                    cur = conn.cursor()
                    # Begin explicit transaction
                    cur.execute("BEGIN TRANSACTION")
                    try:
                        cur.execute("""
                            INSERT OR REPLACE INTO face_crops (
                                project_id, image_path, crop_path, embedding,
                                bbox_x, bbox_y, bbox_w, bbox_h, confidence
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            self.project_id,
                            image_path,
                            crop_path,
                            embedding_bytes,
                            face['bbox_x'],
                            face['bbox_y'],
                            face['bbox_w'],
                            face['bbox_h'],
                            face['confidence']
                        ))
                        conn.commit()
                        return True
                    except Exception as db_error:
                        # Rollback database transaction
                        conn.rollback()
                        logger.error(f"Database save failed, rolling back: {db_error}")

                        # CRITICAL: Delete orphaned crop file
                        if crop_path and os.path.exists(crop_path):
                            try:
                                os.remove(crop_path)
                                logger.info(f"✓ Rolled back face crop: {crop_path}")
                            except Exception as cleanup_error:
                                logger.error(f"Failed to cleanup orphaned crop: {cleanup_error}")

                        raise db_error
            except Exception as transaction_error:
                logger.error(f"Transaction failed: {transaction_error}")
                return False

        except Exception as e:
            logger.error(f"Failed to save face: {e}")
            # Final safety cleanup: remove crop if it exists
            if crop_path and os.path.exists(crop_path):
                try:
                    os.remove(crop_path)
                    logger.debug(f"Cleanup: Removed orphaned crop {crop_path}")
                except:
                    pass
            return False


# Standalone script support
if __name__ == "__main__":
    import sys
    from PySide6.QtCore import QCoreApplication, QThreadPool

    if len(sys.argv) < 2:
        print("Usage: python face_detection_worker.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    app = QCoreApplication(sys.argv)

    def on_progress(current, total, message):
        print(f"[{current}/{total}] {message}")

    def on_finished(success, failed, total_faces):
        print(f"\nFinished: {success} photos processed, {failed} failed, {total_faces} faces detected")
        app.quit()

    worker = FaceDetectionWorker(project_id=project_id)
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)

    QThreadPool.globalInstance().start(worker)

    sys.exit(app.exec())
