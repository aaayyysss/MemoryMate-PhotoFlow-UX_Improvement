"""
JobService - Job Queue and Worker Bridge
Version: 1.0.0
Date: 2025-12-29

This service provides crash-safe job orchestration for ML workloads:
- Persistent job queue (survives app crashes)
- Lease/heartbeat mechanism (prevents zombie jobs)
- QRunnable worker integration (existing pattern)
- Progress tracking and error handling

Architecture:
    UI → JobService.enqueue_job() → INSERT INTO ml_job
       → Create QRunnable worker
       → Worker claims lease, updates progress, completes
       → Signal UI on completion

Usage:
    from services.job_service import get_job_service

    # Enqueue a job
    job_service = get_job_service()
    job_id = job_service.enqueue_job(
        kind='embed',
        payload={'photo_ids': [1, 2, 3], 'model_id': 1},
        backend='cpu'
    )

    # Worker claims and processes
    job_service.claim_job(job_id, worker_id='worker-123')
    job_service.heartbeat(job_id, progress=0.5)
    job_service.complete_job(job_id, success=True)
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import logging

from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Job:
    """Represents a job in the ml_job queue."""
    job_id: int
    kind: str                          # 'embed', 'caption', 'tag_suggest', 'detect', 'event_propose'
    status: str                        # 'queued', 'running', 'paused', 'failed', 'done', 'canceled'
    priority: int
    backend: str                       # 'cpu', 'gpu_local', 'gpu_remote'
    payload_json: str
    progress: float
    error: Optional[str]
    worker_id: Optional[str]
    lease_expires_at: Optional[str]
    last_heartbeat_at: Optional[str]
    created_at: str
    updated_at: Optional[str]
    project_id: Optional[int]

    @property
    def payload(self) -> Dict[str, Any]:
        """Parse JSON payload."""
        return json.loads(self.payload_json)

    def is_lease_expired(self) -> bool:
        """Check if job lease has expired."""
        if not self.lease_expires_at:
            return True  # No lease = expired

        expires = datetime.fromisoformat(self.lease_expires_at)
        return datetime.now() > expires


class JobService:
    """
    Job orchestration service for ML workloads.

    Provides:
    - Persistent job queue (ml_job table)
    - Crash recovery (lease/heartbeat)
    - Progress tracking
    - Error handling
    """

    # Singleton instance
    _instance: Optional['JobService'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.db = DatabaseConnection()
        self._initialized = True
        self._table_exists = None  # Cache for table existence check

        # Recover zombie jobs on startup (with defensive check)
        self._recover_zombie_jobs_on_startup()

        logger.info("JobService initialized")

    def _check_ml_job_table_exists(self) -> bool:
        """
        Check if ml_job table exists in database.

        Returns:
            bool: True if table exists, False otherwise

        Note: Result is cached after first check.
        """
        if self._table_exists is not None:
            return self._table_exists

        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='ml_job'
                """)
                self._table_exists = cursor.fetchone() is not None
                return self._table_exists

        except Exception as e:
            logger.error(f"Failed to check ml_job table existence: {e}")
            self._table_exists = False
            return False

    def _recover_zombie_jobs_on_startup(self):
        """
        Recover jobs left in 'running' state after a crash.

        Called automatically on service initialization.
        Note: If ml_job table doesn't exist, migrations will create it automatically.
        """
        # Defensive check: skip silently if table doesn't exist (migration system will create it)
        if not self._check_ml_job_table_exists():
            logger.debug("ml_job table not yet created - will be created by auto-migration")
            return

        try:
            with self.db.get_connection() as conn:
                current_ts = datetime.now().isoformat()

                # Find zombie jobs (running but lease expired)
                zombie_count = conn.execute("""
                    UPDATE ml_job
                    SET status = 'failed',
                        error = 'Crash recovery: job was running when app crashed',
                        updated_at = ?
                    WHERE status = 'running'
                      AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                """, (current_ts, current_ts)).rowcount

                conn.commit()

                if zombie_count > 0:
                    logger.warning(f"⚠️  Recovered {zombie_count} zombie jobs from crash")
                else:
                    logger.debug("No zombie jobs found")

        except Exception as e:
            logger.error(f"Failed to recover zombie jobs: {e}")

    def enqueue_job(
        self,
        kind: str,
        payload: Dict[str, Any],
        backend: str = 'cpu',
        priority: int = 0,
        project_id: Optional[int] = None
    ) -> int:
        """
        Enqueue a new job.

        Args:
            kind: Job type ('embed', 'caption', 'tag_suggest', 'detect', 'event_propose')
            payload: Job parameters (e.g., {'photo_ids': [1,2,3], 'model_id': 1})
            backend: Compute backend ('cpu', 'gpu_local', 'gpu_remote')
            priority: Job priority (higher = sooner, default 0)
            project_id: Optional project ID for isolation

        Returns:
            int: Job ID of created job

        Raises:
            RuntimeError: If ml_job table doesn't exist (database needs migration)

        Example:
            job_id = job_service.enqueue_job(
                kind='embed',
                payload={'photo_ids': [1, 2, 3], 'model_id': 1},
                backend='cpu'
            )
        """
        try:
            with self.db.get_connection() as conn:
                payload_json = json.dumps(payload)
                created_at = datetime.now().isoformat()

                cursor = conn.execute("""
                    INSERT INTO ml_job (kind, status, priority, backend, payload_json, created_at, project_id)
                    VALUES (?, 'queued', ?, ?, ?, ?, ?)
                """, (kind, priority, backend, payload_json, created_at, project_id))

                conn.commit()
                job_id = cursor.lastrowid

                logger.info(f"✓ Enqueued job {job_id}: {kind} ({len(payload.get('photo_ids', []))} items)")
                return job_id

        except Exception as e:
            logger.error(f"Failed to enqueue job: {e}")
            raise

    def claim_job(self, job_id: int, worker_id: str, lease_seconds: int = 300) -> bool:
        """
        Claim a job before starting work.

        This transitions the job from 'queued' to 'running' and acquires a lease.

        Args:
            job_id: Job ID to claim
            worker_id: Unique worker identifier (e.g., "worker-pid-tid")
            lease_seconds: Lease duration in seconds (default 5 minutes)

        Returns:
            bool: True if job was claimed successfully, False if already claimed

        Example:
            worker_id = f"worker-{os.getpid()}-{id(self)}"
            if job_service.claim_job(job_id, worker_id):
                # Start work
                pass
        """
        try:
            with self.db.get_connection() as conn:
                now = datetime.now()
                lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
                now_iso = now.isoformat()

                # Atomically claim job (only if still queued)
                rowcount = conn.execute("""
                    UPDATE ml_job
                    SET status = 'running',
                        worker_id = ?,
                        lease_expires_at = ?,
                        last_heartbeat_at = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'queued'
                """, (worker_id, lease_expires, now_iso, now_iso, job_id)).rowcount

                conn.commit()

                if rowcount > 0:
                    logger.info(f"✓ Job {job_id} claimed by worker {worker_id}")
                    return True
                else:
                    logger.warning(f"⚠️  Job {job_id} already claimed or not queued")
                    return False

        except Exception as e:
            logger.error(f"Failed to claim job {job_id}: {e}")
            return False

    def heartbeat(self, job_id: int, progress: float, lease_seconds: int = 300):
        """
        Send heartbeat to keep lease alive and update progress.

        Workers should call this periodically (e.g., every 30 seconds) to prevent
        the job from being marked as crashed.

        Args:
            job_id: Job ID
            progress: Progress as float (0.0 to 1.0)
            lease_seconds: Extend lease by this many seconds (default 5 minutes)

        Example:
            for i, photo_id in enumerate(photo_ids):
                # Process photo
                process_photo(photo_id)

                # Heartbeat every photo
                progress = (i + 1) / len(photo_ids)
                job_service.heartbeat(job_id, progress)
        """
        try:
            with self.db.get_connection() as conn:
                now = datetime.now()
                lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
                now_iso = now.isoformat()

                conn.execute("""
                    UPDATE ml_job
                    SET last_heartbeat_at = ?,
                        lease_expires_at = ?,
                        progress = ?,
                        updated_at = ?
                    WHERE job_id = ?
                """, (now_iso, lease_expires, progress, now_iso, job_id))

                conn.commit()
                logger.debug(f"Heartbeat: job {job_id} progress={progress:.1%}")

        except Exception as e:
            logger.warning(f"Failed to send heartbeat for job {job_id}: {e}")

    def complete_job(self, job_id: int, success: bool, error: Optional[str] = None):
        """
        Mark job as completed (done or failed).

        Args:
            job_id: Job ID
            success: True if job completed successfully, False if failed
            error: Optional error message if failed

        Example:
            try:
                # Do work
                job_service.complete_job(job_id, success=True)
            except Exception as e:
                job_service.complete_job(job_id, success=False, error=str(e))
        """
        try:
            with self.db.get_connection() as conn:
                status = 'done' if success else 'failed'
                now_iso = datetime.now().isoformat()

                conn.execute("""
                    UPDATE ml_job
                    SET status = ?,
                        error = ?,
                        progress = 1.0,
                        updated_at = ?
                    WHERE job_id = ?
                """, (status, error, now_iso, job_id))

                conn.commit()

                if success:
                    logger.info(f"✓ Job {job_id} completed successfully")
                else:
                    logger.error(f"✗ Job {job_id} failed: {error}")

        except Exception as e:
            logger.error(f"Failed to complete job {job_id}: {e}")

    def cancel_job(self, job_id: int):
        """
        Cancel a queued or running job.

        Args:
            job_id: Job ID to cancel

        Note:
            - Queued jobs: immediately marked as canceled
            - Running jobs: marked for cancellation (worker must check and stop)
        """
        try:
            with self.db.get_connection() as conn:
                now_iso = datetime.now().isoformat()

                conn.execute("""
                    UPDATE ml_job
                    SET status = 'canceled',
                        updated_at = ?
                    WHERE job_id = ? AND status IN ('queued', 'running')
                """, (now_iso, job_id))

                conn.commit()
                logger.info(f"✓ Job {job_id} canceled")

        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")

    def get_job(self, job_id: int) -> Optional[Job]:
        """
        Get job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job object if found, None otherwise
        """
        try:
            with self.db.get_connection() as conn:
                row = conn.execute("""
                    SELECT * FROM ml_job WHERE job_id = ?
                """, (job_id,)).fetchone()

                if row:
                    return Job(**dict(row))
                return None

        except Exception as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None

    def get_jobs(
        self,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        project_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Job]:
        """
        Get jobs matching criteria.

        Args:
            status: Filter by status ('queued', 'running', 'done', 'failed', etc.)
            kind: Filter by kind ('embed', 'caption', etc.)
            project_id: Filter by project ID
            limit: Maximum number of jobs to return

        Returns:
            List of Job objects
        """
        try:
            with self.db.get_connection() as conn:
                where_clauses = []
                params = []

                if status:
                    where_clauses.append("status = ?")
                    params.append(status)

                if kind:
                    where_clauses.append("kind = ?")
                    params.append(kind)

                if project_id is not None:
                    where_clauses.append("project_id = ?")
                    params.append(project_id)

                where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

                rows = conn.execute(f"""
                    SELECT * FROM ml_job
                    WHERE {where_sql}
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (*params, limit)).fetchall()

                return [Job(**dict(row)) for row in rows]

        except Exception as e:
            logger.error(f"Failed to get jobs: {e}")
            return []

    def get_job_stats(self) -> Dict[str, int]:
        """
        Get job statistics (counts by status).

        Returns:
            Dict mapping status to count (e.g., {'queued': 5, 'running': 2, 'done': 100})
        """
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT status, COUNT(*) as count
                    FROM ml_job
                    GROUP BY status
                """).fetchall()

                return {row['status']: row['count'] for row in rows}

        except Exception as e:
            logger.error(f"Failed to get job stats: {e}")
            return {}


# Singleton accessor
_job_service_instance: Optional[JobService] = None


def get_job_service() -> JobService:
    """
    Get the singleton JobService instance.

    Returns:
        JobService: Singleton instance

    Example:
        from services.job_service import get_job_service

        job_service = get_job_service()
        job_id = job_service.enqueue_job('embed', {...})
    """
    global _job_service_instance
    if _job_service_instance is None:
        _job_service_instance = JobService()
    return _job_service_instance
