# services/performance_tracking_db.py
# Historical Performance Tracking Database
# Phase 2C: Historical Performance Tracking
# Stores and analyzes face detection/clustering performance over time

"""
Performance Tracking Database

Stores historical performance metrics for:
- Face detection operations
- Clustering operations
- Quality metrics over time
- Configuration history
- Performance trends

Schema:
- performance_runs: Individual workflow executions
- performance_metrics: Detailed metrics per run
- quality_history: Quality scores over time
- config_history: Configuration snapshots
"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class PerformanceTrackingDB:
    """
    Database for tracking face detection/clustering performance over time.

    Stores:
    - Workflow execution history
    - Performance metrics (time, throughput, etc.)
    - Quality metrics (Phase 2A scores)
    - Configuration snapshots
    - Resource usage

    Enables:
    - Performance trend analysis
    - Quality tracking over time
    - Configuration impact analysis
    - Regression detection
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize performance tracking database.

        Args:
            db_path: Optional custom database path
        """
        if db_path is None:
            db_path = Path(__file__).parent.parent / "data" / "performance_tracking.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database schema
        self._initialize_schema()

        logger.info(f"[PerformanceTrackingDB] Initialized: {self.db_path}")

    @contextmanager
    def _connect(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _initialize_schema(self):
        """Initialize database schema."""
        with self._connect() as conn:
            cur = conn.cursor()

            # Table: performance_runs
            # Stores high-level information about each workflow execution
            cur.execute("""
                CREATE TABLE IF NOT EXISTS performance_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    run_timestamp TEXT NOT NULL,
                    workflow_type TEXT NOT NULL,  -- 'full', 'detection_only', 'clustering_only'
                    workflow_state TEXT NOT NULL,  -- Final state: 'completed', 'failed', 'cancelled'

                    -- Timing metrics
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    duration_seconds REAL,

                    -- Operation counts
                    photos_total INTEGER DEFAULT 0,
                    photos_processed INTEGER DEFAULT 0,
                    photos_failed INTEGER DEFAULT 0,
                    faces_detected INTEGER DEFAULT 0,
                    clusters_found INTEGER DEFAULT 0,

                    -- Quality metrics (Phase 2A)
                    overall_quality_score REAL DEFAULT 0.0,
                    silhouette_score REAL DEFAULT 0.0,
                    davies_bouldin_index REAL DEFAULT 0.0,
                    noise_ratio REAL DEFAULT 0.0,
                    avg_cluster_size REAL DEFAULT 0.0,

                    -- Performance metrics
                    photos_per_second REAL DEFAULT 0.0,
                    faces_per_second REAL DEFAULT 0.0,

                    -- Configuration snapshot (JSON)
                    config_snapshot TEXT,  -- JSON string

                    -- Error information
                    error_message TEXT,

                    -- Metadata
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    INDEX idx_project_timestamp (project_id, run_timestamp),
                    INDEX idx_workflow_type (workflow_type),
                    INDEX idx_workflow_state (workflow_state)
                )
            """)

            # Table: performance_metrics
            # Stores detailed metrics per operation within a run
            cur.execute("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,

                    -- Operation details
                    operation_name TEXT NOT NULL,  -- 'load_embeddings', 'dbscan_clustering', etc.
                    operation_timestamp TEXT NOT NULL,

                    -- Timing
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,

                    -- Metadata (JSON)
                    metadata TEXT,  -- JSON string with operation-specific data

                    -- Resource usage (optional)
                    cpu_percent REAL,
                    memory_mb REAL,

                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (run_id) REFERENCES performance_runs(id) ON DELETE CASCADE,
                    INDEX idx_run_operation (run_id, operation_name)
                )
            """)

            # Table: quality_history
            # Tracks quality metrics over time for trend analysis
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quality_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,

                    -- Timestamp
                    measured_at TEXT NOT NULL,

                    -- Quality metrics (Phase 2A)
                    overall_quality REAL NOT NULL,
                    silhouette_score REAL,
                    davies_bouldin_index REAL,
                    noise_ratio REAL,

                    -- Cluster statistics
                    cluster_count INTEGER,
                    avg_cluster_size REAL,
                    min_cluster_size INTEGER,
                    max_cluster_size INTEGER,

                    -- Face quality averages
                    avg_blur_score REAL,
                    avg_lighting_score REAL,
                    avg_face_size_score REAL,

                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (run_id) REFERENCES performance_runs(id) ON DELETE CASCADE,
                    INDEX idx_project_measured (project_id, measured_at)
                )
            """)

            # Table: config_history
            # Stores configuration snapshots for each run
            cur.execute("""
                CREATE TABLE IF NOT EXISTS config_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,

                    -- Configuration details
                    config_type TEXT NOT NULL,  -- 'quality_thresholds', 'clustering_params'
                    config_data TEXT NOT NULL,  -- JSON string

                    -- Impact tracking
                    quality_before REAL,
                    quality_after REAL,
                    impact_score REAL,  -- quality_after - quality_before

                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (run_id) REFERENCES performance_runs(id) ON DELETE CASCADE,
                    INDEX idx_config_type (config_type)
                )
            """)

            conn.commit()
            logger.debug("[PerformanceTrackingDB] Schema initialized")

    def start_run(self,
                  project_id: int,
                  workflow_type: str,
                  config_snapshot: Optional[Dict] = None) -> int:
        """
        Start tracking a new workflow run.

        Args:
            project_id: Project ID
            workflow_type: Type of workflow ('full', 'detection_only', 'clustering_only')
            config_snapshot: Configuration snapshot

        Returns:
            run_id for this execution
        """
        with self._connect() as conn:
            cur = conn.cursor()

            now = datetime.now().isoformat()
            config_json = json.dumps(config_snapshot) if config_snapshot else None

            cur.execute("""
                INSERT INTO performance_runs (
                    project_id, run_timestamp, workflow_type, workflow_state,
                    start_time, config_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                project_id,
                now,
                workflow_type,
                'running',
                now,
                config_json
            ))

            run_id = cur.lastrowid
            logger.info(f"[PerformanceTrackingDB] Started run {run_id} for project {project_id}")
            return run_id

    def update_run(self,
                   run_id: int,
                   **kwargs):
        """
        Update run with metrics.

        Args:
            run_id: Run ID
            **kwargs: Fields to update (workflow_state, photos_processed, faces_detected, etc.)
        """
        if not kwargs:
            return

        with self._connect() as conn:
            cur = conn.cursor()

            # Build SET clause dynamically
            fields = []
            values = []
            for key, value in kwargs.items():
                fields.append(f"{key} = ?")
                values.append(value)

            values.append(run_id)

            query = f"UPDATE performance_runs SET {', '.join(fields)} WHERE id = ?"
            cur.execute(query, values)

            logger.debug(f"[PerformanceTrackingDB] Updated run {run_id}: {kwargs}")

    def complete_run(self,
                     run_id: int,
                     workflow_state: str,
                     **metrics):
        """
        Complete a run with final metrics.

        Args:
            run_id: Run ID
            workflow_state: Final state ('completed', 'failed', 'cancelled')
            **metrics: Final metrics (photos_processed, faces_detected, quality scores, etc.)
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # Get start time to calculate duration
            cur.execute("SELECT start_time FROM performance_runs WHERE id = ?", (run_id,))
            row = cur.fetchone()
            if not row:
                logger.error(f"[PerformanceTrackingDB] Run {run_id} not found")
                return

            start_time = datetime.fromisoformat(row['start_time'])
            end_time = datetime.now()
            duration_seconds = (end_time - start_time).total_seconds()

            # Calculate throughput metrics
            photos_per_second = 0.0
            faces_per_second = 0.0
            if duration_seconds > 0:
                photos_processed = metrics.get('photos_processed', 0)
                faces_detected = metrics.get('faces_detected', 0)
                photos_per_second = photos_processed / duration_seconds
                faces_per_second = faces_detected / duration_seconds

            # Update run
            update_data = {
                'workflow_state': workflow_state,
                'end_time': end_time.isoformat(),
                'duration_seconds': duration_seconds,
                'photos_per_second': photos_per_second,
                'faces_per_second': faces_per_second,
                **metrics
            }

            self.update_run(run_id, **update_data)

            logger.info(
                f"[PerformanceTrackingDB] Completed run {run_id}: {workflow_state}, "
                f"duration={duration_seconds:.1f}s, throughput={photos_per_second:.2f} photos/s"
            )

    def log_operation_metric(self,
                            run_id: int,
                            operation_name: str,
                            duration_seconds: float,
                            metadata: Optional[Dict] = None):
        """
        Log a performance metric for an operation within a run.

        Args:
            run_id: Run ID
            operation_name: Operation name
            duration_seconds: Operation duration
            metadata: Optional metadata (JSON-serializable dict)
        """
        with self._connect() as conn:
            cur = conn.cursor()

            now = datetime.now().isoformat()
            metadata_json = json.dumps(metadata) if metadata else None

            cur.execute("""
                INSERT INTO performance_metrics (
                    run_id, operation_name, operation_timestamp,
                    start_time, end_time, duration_seconds, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                operation_name,
                now,
                now,  # Approximate start time
                now,
                duration_seconds,
                metadata_json
            ))

            logger.debug(
                f"[PerformanceTrackingDB] Logged metric: run={run_id}, "
                f"operation={operation_name}, duration={duration_seconds:.3f}s"
            )

    def log_quality_metrics(self,
                           run_id: int,
                           project_id: int,
                           quality_metrics: Dict[str, Any]):
        """
        Log quality metrics for a run.

        Args:
            run_id: Run ID
            project_id: Project ID
            quality_metrics: Quality metrics dict (from Phase 2A)
        """
        with self._connect() as conn:
            cur = conn.cursor()

            now = datetime.now().isoformat()

            cur.execute("""
                INSERT INTO quality_history (
                    run_id, project_id, measured_at,
                    overall_quality, silhouette_score, davies_bouldin_index, noise_ratio,
                    cluster_count, avg_cluster_size, min_cluster_size, max_cluster_size,
                    avg_blur_score, avg_lighting_score, avg_face_size_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                project_id,
                now,
                quality_metrics.get('overall_quality', 0.0),
                quality_metrics.get('silhouette_score', None),
                quality_metrics.get('davies_bouldin_index', None),
                quality_metrics.get('noise_ratio', None),
                quality_metrics.get('cluster_count', None),
                quality_metrics.get('avg_cluster_size', None),
                quality_metrics.get('min_cluster_size', None),
                quality_metrics.get('max_cluster_size', None),
                quality_metrics.get('avg_blur_score', None),
                quality_metrics.get('avg_lighting_score', None),
                quality_metrics.get('avg_face_size_score', None)
            ))

            logger.debug(
                f"[PerformanceTrackingDB] Logged quality metrics: run={run_id}, "
                f"quality={quality_metrics.get('overall_quality', 0.0):.1f}"
            )

    def log_config_change(self,
                         run_id: int,
                         project_id: int,
                         config_type: str,
                         config_data: Dict,
                         quality_before: Optional[float] = None,
                         quality_after: Optional[float] = None):
        """
        Log a configuration change and its impact.

        Args:
            run_id: Run ID
            project_id: Project ID
            config_type: Type of configuration ('quality_thresholds', 'clustering_params')
            config_data: Configuration data
            quality_before: Quality score before change
            quality_after: Quality score after change
        """
        with self._connect() as conn:
            cur = conn.cursor()

            impact_score = None
            if quality_before is not None and quality_after is not None:
                impact_score = quality_after - quality_before

            cur.execute("""
                INSERT INTO config_history (
                    run_id, project_id, config_type, config_data,
                    quality_before, quality_after, impact_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                project_id,
                config_type,
                json.dumps(config_data),
                quality_before,
                quality_after,
                impact_score
            ))

            logger.debug(
                f"[PerformanceTrackingDB] Logged config change: run={run_id}, "
                f"type={config_type}, impact={impact_score}"
            )

    def get_recent_runs(self,
                       project_id: Optional[int] = None,
                       limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent workflow runs.

        Args:
            project_id: Optional project filter
            limit: Maximum number of runs to return

        Returns:
            List of run dictionaries
        """
        with self._connect() as conn:
            cur = conn.cursor()

            if project_id is not None:
                cur.execute("""
                    SELECT * FROM performance_runs
                    WHERE project_id = ?
                    ORDER BY run_timestamp DESC
                    LIMIT ?
                """, (project_id, limit))
            else:
                cur.execute("""
                    SELECT * FROM performance_runs
                    ORDER BY run_timestamp DESC
                    LIMIT ?
                """, (limit,))

            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def get_quality_trend(self,
                         project_id: int,
                         days: int = 30) -> List[Dict[str, Any]]:
        """
        Get quality trend over time for a project.

        Args:
            project_id: Project ID
            days: Number of days to look back

        Returns:
            List of quality measurements over time
        """
        with self._connect() as conn:
            cur = conn.cursor()

            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

            cur.execute("""
                SELECT * FROM quality_history
                WHERE project_id = ? AND measured_at >= ?
                ORDER BY measured_at ASC
            """, (project_id, cutoff_date))

            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def get_performance_stats(self,
                             project_id: Optional[int] = None,
                             days: int = 30) -> Dict[str, Any]:
        """
        Get performance statistics.

        Args:
            project_id: Optional project filter
            days: Number of days to look back

        Returns:
            Statistics dictionary
        """
        with self._connect() as conn:
            cur = conn.cursor()

            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

            where_clause = "WHERE run_timestamp >= ?"
            params: List[Any] = [cutoff_date]

            if project_id is not None:
                where_clause += " AND project_id = ?"
                params.append(project_id)

            # Get aggregate statistics
            cur.execute(f"""
                SELECT
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN workflow_state = 'completed' THEN 1 ELSE 0 END) as completed_runs,
                    SUM(CASE WHEN workflow_state = 'failed' THEN 1 ELSE 0 END) as failed_runs,
                    AVG(duration_seconds) as avg_duration,
                    AVG(photos_per_second) as avg_throughput,
                    AVG(overall_quality_score) as avg_quality,
                    MAX(overall_quality_score) as max_quality,
                    MIN(overall_quality_score) as min_quality
                FROM performance_runs
                {where_clause}
            """, params)

            row = cur.fetchone()

            return {
                'total_runs': row['total_runs'] or 0,
                'completed_runs': row['completed_runs'] or 0,
                'failed_runs': row['failed_runs'] or 0,
                'success_rate': (row['completed_runs'] or 0) / max(row['total_runs'] or 1, 1),
                'avg_duration_seconds': row['avg_duration'] or 0.0,
                'avg_throughput_photos_per_second': row['avg_throughput'] or 0.0,
                'avg_quality_score': row['avg_quality'] or 0.0,
                'max_quality_score': row['max_quality'] or 0.0,
                'min_quality_score': row['min_quality'] or 0.0
            }

    def get_operation_breakdown(self, run_id: int) -> List[Dict[str, Any]]:
        """
        Get operation timing breakdown for a run.

        Args:
            run_id: Run ID

        Returns:
            List of operation metrics
        """
        with self._connect() as conn:
            cur = conn.cursor()

            cur.execute("""
                SELECT * FROM performance_metrics
                WHERE run_id = ?
                ORDER BY operation_timestamp ASC
            """, (run_id,))

            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def cleanup_old_data(self, days_to_keep: int = 90):
        """
        Clean up old performance data.

        Args:
            days_to_keep: Number of days of data to retain
        """
        with self._connect() as conn:
            cur = conn.cursor()

            cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).isoformat()

            # Delete old runs (cascade deletes metrics and quality history)
            cur.execute("""
                DELETE FROM performance_runs
                WHERE run_timestamp < ?
            """, (cutoff_date,))

            deleted_count = cur.rowcount

            logger.info(
                f"[PerformanceTrackingDB] Cleaned up {deleted_count} runs older than {days_to_keep} days"
            )
