# utils/face_detection_logger.py
# Structured Logging System for Face Detection Operations
# Enhancement #2: Critical Pre-Deployment (2026-01-07)
# Provides comprehensive logging for production troubleshooting
# ------------------------------------------------------

"""
Face Detection Structured Logger

Provides structured JSON logging for face detection operations to enable:
- Production troubleshooting
- Performance analysis
- Error tracking and debugging
- User support diagnostics

Log Format:
- JSON lines format (one JSON object per line)
- Timestamped entries with event types
- Includes full context (photo paths, parameters, errors)
- Separate log files per detection session

Usage:
    logger = FaceDetectionLogger(project_id=1)
    logger.log_detection_start(params)
    logger.log_photo_processed(photo_path, faces_found, duration_ms)
    logger.log_error(photo_path, error_type, error_message, traceback)
    logger.log_detection_complete(stats)
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class FaceDetectionLogger:
    """
    Structured JSON logger for face detection operations.

    Creates separate log files for each detection session with full context
    for troubleshooting and performance analysis.
    """

    def __init__(self, project_id: int, log_dir: Optional[str] = None):
        """
        Initialize face detection logger.

        Args:
            project_id: Project ID being processed
            log_dir: Optional custom log directory (default: .memorymate/logs/)
        """
        self.project_id = project_id
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Setup log directory
        if log_dir is None:
            from app_env import app_path
            log_dir = app_path(".memorymate", "logs", "face_detection")

        os.makedirs(log_dir, exist_ok=True)

        # Log file path
        self.log_file = os.path.join(
            log_dir,
            f"face_detection_p{project_id}_{self.session_id}.jsonl"
        )

        # Session stats
        self._session_start = None
        self._photos_processed = 0
        self._faces_detected = 0
        self._errors_count = 0

        logger.info(f"[FaceDetectionLogger] Logging to: {self.log_file}")

    def log_detection_start(self, params: Dict[str, Any]):
        """
        Log detection session start with parameters.

        Args:
            params: Detection parameters (model, confidence_threshold, etc.)
        """
        self._session_start = datetime.now()

        log_entry = {
            "timestamp": self._session_start.isoformat(),
            "event": "detection_start",
            "session_id": self.session_id,
            "project_id": self.project_id,
            "parameters": params,
            "hardware": self._get_hardware_info()
        }

        self._write_log(log_entry)
        logger.info(f"[FaceDetectionLogger] Session started: {self.session_id}")

    def log_photo_processed(self, photo_path: str, faces_found: int,
                           duration_ms: float, success: bool = True):
        """
        Log individual photo processing result.

        Args:
            photo_path: Path to processed photo
            faces_found: Number of faces detected
            duration_ms: Processing time in milliseconds
            success: Whether processing succeeded
        """
        self._photos_processed += 1
        if success:
            self._faces_detected += faces_found

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "photo_processed",
            "session_id": self.session_id,
            "photo_path": photo_path,
            "photo_basename": os.path.basename(photo_path),
            "faces_found": faces_found,
            "duration_ms": duration_ms,
            "success": success,
            "cumulative_photos": self._photos_processed,
            "cumulative_faces": self._faces_detected
        }

        self._write_log(log_entry)

    def log_error(self, photo_path: str, error_type: str,
                 error_message: str, traceback_str: Optional[str] = None):
        """
        Log errors with full context.

        Args:
            photo_path: Path to photo that caused error
            error_type: Type of error (e.g., 'ImageLoadError', 'DetectionError')
            error_message: Error message
            traceback_str: Optional full traceback string
        """
        self._errors_count += 1

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "error",
            "session_id": self.session_id,
            "photo_path": photo_path,
            "photo_basename": os.path.basename(photo_path),
            "error_type": error_type,
            "error_message": error_message,
            "traceback": traceback_str,
            "cumulative_errors": self._errors_count
        }

        self._write_log(log_entry)
        logger.error(f"[FaceDetectionLogger] Error #{self._errors_count}: {error_type} - {error_message}")

    def log_detection_complete(self, stats: Dict[str, Any]):
        """
        Log detection session completion with summary stats.

        Args:
            stats: Final statistics (photos_processed, faces_detected, etc.)
        """
        if self._session_start is None:
            logger.warning("[FaceDetectionLogger] Session start not logged, cannot calculate duration")
            duration_seconds = 0
        else:
            duration_seconds = (datetime.now() - self._session_start).total_seconds()

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "detection_complete",
            "session_id": self.session_id,
            "project_id": self.project_id,
            "duration_seconds": duration_seconds,
            "statistics": {
                **stats,
                "photos_processed": self._photos_processed,
                "faces_detected": self._faces_detected,
                "errors_count": self._errors_count,
                "photos_per_second": self._photos_processed / duration_seconds if duration_seconds > 0 else 0,
                "faces_per_second": self._faces_detected / duration_seconds if duration_seconds > 0 else 0
            }
        }

        self._write_log(log_entry)
        logger.info(
            f"[FaceDetectionLogger] Session complete: "
            f"{self._photos_processed} photos, "
            f"{self._faces_detected} faces, "
            f"{self._errors_count} errors, "
            f"{duration_seconds:.1f}s"
        )

    def log_clustering_start(self, face_count: int, params: Dict[str, Any]):
        """
        Log clustering operation start.

        Args:
            face_count: Number of faces to cluster
            params: Clustering parameters (eps, min_samples, etc.)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "clustering_start",
            "session_id": self.session_id,
            "project_id": self.project_id,
            "face_count": face_count,
            "parameters": params
        }

        self._write_log(log_entry)

    def log_clustering_complete(self, cluster_count: int, noise_count: int,
                               duration_ms: float, quality_metrics: Optional[Dict] = None):
        """
        Log clustering operation completion.

        Args:
            cluster_count: Number of clusters created
            noise_count: Number of unclustered faces
            duration_ms: Clustering time in milliseconds
            quality_metrics: Optional quality metrics (silhouette score, etc.)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "clustering_complete",
            "session_id": self.session_id,
            "project_id": self.project_id,
            "cluster_count": cluster_count,
            "noise_count": noise_count,
            "duration_ms": duration_ms,
            "quality_metrics": quality_metrics or {}
        }

        self._write_log(log_entry)

    def _write_log(self, entry: Dict[str, Any]):
        """
        Write log entry to JSON lines file.

        Args:
            entry: Log entry dictionary
        """
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"[FaceDetectionLogger] Failed to write log: {e}")

    def _get_hardware_info(self) -> Dict[str, Any]:
        """
        Get hardware information for logging.

        Returns:
            Dictionary with hardware details
        """
        try:
            from services.face_detection_service import get_hardware_info
            hw_info = get_hardware_info()
            return {
                "type": hw_info.get('type', 'Unknown'),
                "cuda_available": hw_info.get('cuda_available', False),
                "providers": hw_info.get('providers', [])
            }
        except Exception as e:
            logger.debug(f"Failed to get hardware info: {e}")
            return {"type": "Unknown", "cuda_available": False, "providers": []}

    def get_log_file_path(self) -> str:
        """Get path to current log file."""
        return self.log_file

    def get_session_stats(self) -> Dict[str, Any]:
        """
        Get current session statistics.

        Returns:
            Dictionary with session stats
        """
        duration_seconds = 0
        if self._session_start:
            duration_seconds = (datetime.now() - self._session_start).total_seconds()

        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "photos_processed": self._photos_processed,
            "faces_detected": self._faces_detected,
            "errors_count": self._errors_count,
            "duration_seconds": duration_seconds,
            "photos_per_second": self._photos_processed / duration_seconds if duration_seconds > 0 else 0,
            "faces_per_second": self._faces_detected / duration_seconds if duration_seconds > 0 else 0
        }


def parse_log_file(log_file_path: str) -> list:
    """
    Parse a face detection log file.

    Args:
        log_file_path: Path to .jsonl log file

    Returns:
        List of log entry dictionaries
    """
    entries = []
    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
    except Exception as e:
        logger.error(f"Failed to parse log file {log_file_path}: {e}")

    return entries


def get_session_summary(log_file_path: str) -> Optional[Dict[str, Any]]:
    """
    Get summary from a completed detection session log.

    Args:
        log_file_path: Path to .jsonl log file

    Returns:
        Summary dictionary or None if parsing fails
    """
    entries = parse_log_file(log_file_path)
    if not entries:
        return None

    # Find start and complete events
    start_event = next((e for e in entries if e.get('event') == 'detection_start'), None)
    complete_event = next((e for e in entries if e.get('event') == 'detection_complete'), None)

    if not start_event or not complete_event:
        return None

    return {
        "session_id": start_event.get('session_id'),
        "project_id": start_event.get('project_id'),
        "start_time": start_event.get('timestamp'),
        "end_time": complete_event.get('timestamp'),
        "statistics": complete_event.get('statistics', {}),
        "hardware": start_event.get('hardware', {}),
        "parameters": start_event.get('parameters', {})
    }
