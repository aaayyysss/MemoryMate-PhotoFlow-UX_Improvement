# reference_db.py
# Version 10.03.01.02 dated 20260223
# FIX: Convert db_file to absolute path in __init__ for consistency with DatabaseConnection
# PHASE 4 CLEANUP: Removed unnecessary ensure_created_date_fields() calls
# UPDATED: Now uses repository layer for schema management
#
# Class-based SQLite wrapper for references, thresholds, labels, and sorting projects
#
# MIGRATION NOTE: Schema management has been moved to repository layer.
# The _ensure_db() method is now deprecated. Schema creation and migrations
# are handled automatically by repository.base_repository.DatabaseConnection.
#

import sqlite3
import os, time
import io
import shutil
import json
import argparse
import traceback
import warnings
import threading
import logging
from contextlib import contextmanager
from typing import List, Dict, Any  # FEATURE #1: Type hints for face detection scope methods

from datetime import datetime
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


from db_config import get_db_filename

DB_FILE = get_db_filename()

logger = logging.getLogger(__name__)


class ReferenceDB:
    # Patch E: Per-DB singleton pattern with thread-safe connection pooling
    # This prevents multiple instances for the same file while allowing separate instances
    # for different project databases (supporting multi-project portability).
    _instances = {}
    _lock = threading.Lock()
    _connection_pool = {}
    _pool_lock = threading.Lock()
    _max_pool_size = 10  # Maximum number of pooled connections
    
    def __new__(cls, db_file=None):
        """
        Thread-safe per-file singleton implementation.

        Returns the same ReferenceDB instance for the same db_file across all threads.
        This prevents connection proliferation and ensures thread safety.
        """
        if db_file is None:
            db_file = get_db_filename()

        # Normalize path to ensure absolute for consistent pool key
        db_file = os.path.abspath(db_file)

        with cls._lock:
            if db_file not in cls._instances:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instances[db_file] = inst

        return cls._instances[db_file]

    @classmethod
    def instance(cls, db_file=None):
        """
        Get singleton ReferenceDB instance.

        This is a convenience method for callers expecting an instance() pattern.
        Equivalent to calling ReferenceDB(db_file).

        Args:
            db_file: Optional database file path (default: reference_data.db)

        Returns:
            ReferenceDB: The singleton instance
        """
        return cls(db_file)
    
    def __init__(self, db_file=DB_FILE):
        """
        Initialize ReferenceDB instance (singleton pattern).

        MIGRATION NOTE: As of v09.19.00.00, schema management is handled by
        the repository layer. The database schema will be automatically created
        and migrated by repository.base_repository.DatabaseConnection.

        Args:
            db_file: Path to database file (default: reference_data.db)
        """
        # CRITICAL FIX: Prevent re-initialization of singleton
        if self._initialized:
            return
        
        # Initialize logger
        from logging_config import get_logger
        self.logger = get_logger(__name__)

        # CRITICAL FIX: Convert to absolute path BEFORE storing
        # This ensures _connect() uses the same database file as DatabaseConnection
        import os
        self.db_file = os.path.abspath(db_file)

        # NEW: Use repository layer for schema management
        # This automatically handles schema creation and migrations
        try:
            from repository.base_repository import DatabaseConnection
            self._db_connection = DatabaseConnection(self.db_file, auto_init=True)
        except ImportError:
            # Fallback for environments where repository layer isn't available
            warnings.warn(
                "Repository layer not available, falling back to legacy schema management. "
                "This fallback will be removed in a future version.",
                DeprecationWarning,
                stacklevel=2
            )
            self._db_connection = None
            self._ensure_db()  # Legacy fallback

        # Lazy cache to know if created_* columns exist (None = unknown)
        self._created_cols_present = None
        
        # Mark as initialized
        self._initialized = True        


    # --- Initialization ---
    def _ensure_db(self):
        """
        DEPRECATED: Schema management has moved to repository layer.

        This method is maintained ONLY as a fallback for environments where the
        repository layer is unavailable. It will be removed in v10.00.

        Schema creation and migrations are now handled automatically by:
        - repository/schema.py (schema definition)
        - repository/migrations.py (migration system)
        - repository/base_repository.py (automatic initialization)

        For normal operation, the repository layer handles all schema management.
        This legacy fallback provides minimal schema creation only.
        """
        warnings.warn(
            "_ensure_db() is deprecated. Schema management has moved to repository layer. "
            "This method will be removed in v10.00.",
            DeprecationWarning,
            stacklevel=2
        )

        # LEGACY FALLBACK: Only used if repository layer import failed
        # This provides minimal schema creation for backward compatibility
        # Full schema management should use repository.base_repository.DatabaseConnection

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        # Reference images
        c.execute('''
            CREATE TABLE IF NOT EXISTS reference_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL
            )
        ''')

        # Match audit logging
        c.execute('''
            CREATE TABLE IF NOT EXISTS match_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                matched_label TEXT,
                confidence REAL,
                match_mode TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Label thresholds
        c.execute('''
            CREATE TABLE IF NOT EXISTS reference_labels (
                label TEXT PRIMARY KEY,
                folder_path TEXT NOT NULL,
                threshold REAL DEFAULT 0.3
            )
        ''')

        # --- NEW TABLES: Projects ---
        c.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                folder TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                branch_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, branch_key)
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS project_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,                
                branch_key TEXT,
                image_path TEXT NOT NULL,
                label TEXT,   -- ✅ new: optional label (face-based grouping)
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        ''')

        # --- face crops, with idempotent uniqueness ---
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS face_crops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                branch_key TEXT NOT NULL,
                image_path TEXT NOT NULL,  -- original photo
                crop_path  TEXT NOT NULL,  -- saved face-crop (thumbnail-sized OK)
                is_representative INTEGER DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, branch_key, crop_path)
            )
        ''')
        

        # --- MIGRATION: singular → plural table name if an older DB exists ---
        # If 'face_crop' exists and 'face_crops' does not, rename it.
        has_old = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='face_crop'"
        ).fetchone()
        has_new = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='face_crops'"
        ).fetchone()
        if has_old and not has_new:
            c.execute("ALTER TABLE face_crop RENAME TO face_crops")

        # --- MIGRATION: Add quality_score column if missing (for representative face selection) ---
        c.execute("PRAGMA table_info(face_crops)")
        existing_cols_fc = {row[1] for row in c.fetchall()}
        if 'quality_score' not in existing_cols_fc:
            try:
                c.execute("ALTER TABLE face_crops ADD COLUMN quality_score REAL DEFAULT 0.0")
            except Exception:
                pass  # Ignore if fails (column might already exist due to race condition)


        # --- Face crops (per-branch thumbnails; DB is the source of truth) ---

        # helpful indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_proj ON face_crops(project_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_proj_branch ON face_crops(project_id, branch_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_proj_rep ON face_crops(project_id, is_representative)")

        # FIX #5: Prevent duplicate face detections for the same bounding box
        # on the same photo.  Without this, re-running detection can double
        # the face_crops rows and cause the "20 detected but 30 loaded" drift.
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_face_crops_unique_bbox
                     ON face_crops(project_id, image_path, bbox_x, bbox_y, bbox_w, bbox_h)""")

        # --- NEW: reps table you already upsert into elsewhere ---
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS face_branch_reps (
                project_id INTEGER NOT NULL,
                branch_key TEXT NOT NULL,
                label TEXT,
                count INTEGER DEFAULT 0,
                centroid BLOB,
                rep_path TEXT,          -- path to chosen rep crop on disk
                rep_thumb_png BLOB,     -- optional in-DB PNG
                PRIMARY KEY (project_id, branch_key),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        ''')


        # --------------------------------------------------
        # Face merge history (for undo)
        # --------------------------------------------------
        c.execute("""
            CREATE TABLE IF NOT EXISTS face_merge_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                target_branch TEXT NOT NULL,
                source_branches TEXT NOT NULL,
                snapshot TEXT NOT NULL,         -- JSON blob of pre-merge state
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute('''
            CREATE TABLE IF NOT EXISTS export_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                branch_key TEXT,
                photo_count INTEGER,
                source_paths TEXT,
                dest_paths TEXT,
                dest_folder TEXT,
                timestamp TEXT
            )
        ''')    
                
        c.execute('''
            CREATE TABLE IF NOT EXISTS photo_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT UNIQUE NOT NULL,
                parent_id INTEGER NULL,
                FOREIGN KEY(parent_id) REFERENCES photo_folders(id)
            )
        ''')
       
        # photo_metadata: add metadata_status / metadata_fail_count columns at creation time for fresh DBs.

        c.execute('''
            CREATE TABLE IF NOT EXISTS photo_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                folder_id INTEGER NOT NULL,
                size_kb REAL,
                modified TEXT,
                width INTEGER,
                height INTEGER,
                embedding BLOB,
                date_taken TEXT,
                tags TEXT,
                updated_at TEXT,
                metadata_status TEXT DEFAULT 'pending',
                metadata_fail_count INTEGER DEFAULT 0,
                created_ts INTEGER,
                created_date TEXT,
                created_year INTEGER,
                FOREIGN KEY(folder_id) REFERENCES photo_folders(id)
            )
        ''')

        # --- Tagging tables (new normalized structure) ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL COLLATE NOCASE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS photo_tags (
                photo_id INTEGER NOT NULL,
                tag_id   INTEGER NOT NULL,
                PRIMARY KEY (photo_id, tag_id),
                FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
        """)

        # Helpful indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id)")

        # --- Add missing columns dynamically if upgrading from older schema ---
        existing_cols = [r['name'] for r in c.execute("PRAGMA table_info(photo_metadata)")]
        wanted_cols = {
            "size_kb": "REAL",
            "modified": "TEXT",
            "embedding": "BLOB",
            "date_taken": "TEXT",
            "tags": "TEXT",
            "updated_at": "TEXT",
            "metadata_status": "TEXT DEFAULT 'pending'",
            "metadata_fail_count": "INTEGER DEFAULT 0",
            "created_ts": "INTEGER",
            "created_date": "TEXT",
            "created_year": "INTEGER",
        }
        for col, col_type in wanted_cols.items():
            if col not in existing_cols:
                try:
                    # Some SQLite versions don't accept column default expressions with ALTER TABLE, so split
                    if col == "metadata_fail_count":
                        c.execute(f"ALTER TABLE photo_metadata ADD COLUMN {col} INTEGER DEFAULT 0")
                    elif col == "metadata_status":
                        c.execute(f"ALTER TABLE photo_metadata ADD COLUMN {col} TEXT DEFAULT 'pending'")
                    else:
                        c.execute(f"ALTER TABLE photo_metadata ADD COLUMN {col} {col_type}")
                except Exception:
                    # best-effort: ignore if it fails (older DB locked, etc.)
                    pass
                    
#                c.execute(f"ALTER TABLE photo_metadata ADD COLUMN {col} {col_type}")


        # helpful indexes for date & metadata
        c.execute("CREATE INDEX IF NOT EXISTS idx_meta_date      ON photo_metadata(date_taken)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_meta_modified  ON photo_metadata(modified)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_meta_updated   ON photo_metadata(updated_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_meta_folder    ON photo_metadata(folder_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_meta_status    ON photo_metadata(metadata_status)")

        # PERFORMANCE: Add path index for faster lookups (photo existence checks, tag operations)
        c.execute("CREATE INDEX IF NOT EXISTS idx_meta_path ON photo_metadata(path)")

        # indexes for created_* columns (used for date-based navigation)
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_year ON photo_metadata(created_year)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_date ON photo_metadata(created_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_ts ON photo_metadata(created_ts)")

        # PERFORMANCE: Add folder hierarchy index for faster tree operations
        c.execute("CREATE INDEX IF NOT EXISTS idx_folder_parent ON photo_folders(parent_id)")

        c.execute("CREATE INDEX IF NOT EXISTS idx_fbreps_proj ON face_branch_reps(project_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fbreps_proj_branch ON face_branch_reps(project_id, branch_key)")
        


        # ---- helpful indexes (no-op if already present) ----
        c.execute("CREATE INDEX IF NOT EXISTS idx_branches_project ON branches(project_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_branches_key ON branches(project_id, branch_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_projimgs_project ON project_images(project_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_projimgs_branch ON project_images(project_id, branch_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_projimgs_path ON project_images(image_path)")
        
        
        conn.commit()
        conn.close()

    # --- Safe connection wrapper with pooling ---
    @contextmanager
    def _connect(self):
        """
        CRITICAL FIX: Thread-safe connection pooling.
        
        Provides a context-managed database connection from the connection pool.
        Connections are reused across queries to minimize overhead and prevent
        connection proliferation.
        
        Usage:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(...)
        
        Returns:
            sqlite3.Connection: A connection from the pool with foreign keys enabled
                               and Row factory configured.
        """
        thread_id = threading.get_ident()
        conn = None

        with self._pool_lock:
            # Try to get existing connection for this thread
            if thread_id in self._connection_pool:
                conn = self._connection_pool[thread_id]

                # Verify connection is still valid
                try:
                    conn.execute("SELECT 1")
                except sqlite3.Error:
                    # Connection is broken, remove it and create new one
                    try:
                        conn.close()
                    except sqlite3.Error as e:
                        print(f"[ReferenceDB] Failed to close broken connection: {e}")
                    conn = None
                    del self._connection_pool[thread_id]

            # Create new connection if needed — INSIDE the lock to prevent
            # concurrent sqlite3.connect() calls which cause access violations
            # on Windows (Python 3.11 + WAL mode race condition).
            if conn is None:
                conn = sqlite3.connect(self.db_file, check_same_thread=False)
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.row_factory = sqlite3.Row

                # Evict oldest connection if pool is full.
                # Only evict entries for threads that are NO LONGER ALIVE
                # to avoid closing a connection still in use by another thread.
                if len(self._connection_pool) >= self._max_pool_size:
                    dead_thread = None
                    for tid in self._connection_pool:
                        # Check if thread is still alive
                        alive = False
                        for t in threading.enumerate():
                            if t.ident == tid:
                                alive = True
                                break
                        if not alive:
                            dead_thread = tid
                            break

                    evict_tid = dead_thread if dead_thread else next(iter(self._connection_pool))
                    try:
                        self._connection_pool[evict_tid].close()
                    except sqlite3.Error as e:
                        print(f"[ReferenceDB] Failed to close pooled connection: {e}")
                    del self._connection_pool[evict_tid]

                self._connection_pool[thread_id] = conn
        
        try:
            yield conn
            conn.commit()  # Auto-commit on successful context exit
        except Exception:
            conn.rollback()  # Auto-rollback on exception
            raise

    def get_connection(self):
        """Compatibility shim — delegates to _connect().

        102+ callers across the codebase use ``db.get_connection()`` as a
        context-manager.  The real implementation lives in ``_connect()``;
        this thin wrapper keeps every call-site working without renaming.
        """
        return self._connect()

    @classmethod
    def close_all_connections(cls):
        """
        Close all pooled connections for graceful shutdown.

        Call this method when the application is shutting down to ensure
        all database connections are properly closed.
        """
        with cls._pool_lock:
            for thread_id, conn in list(cls._connection_pool.items()):
                try:
                    conn.close()
                    print(f"[ReferenceDB] Closed connection for thread {thread_id}")
                except Exception as e:
                    print(f"[ReferenceDB] Warning: Failed to close connection for thread {thread_id}: {e}")
            cls._connection_pool.clear()
            print(f"[ReferenceDB] All {len(cls._connection_pool)} connections closed")

    def close(self):
        """
        Close this instance's resources (for worker cleanup).

        For singleton ReferenceDB, this closes the current thread's pooled connection.
        Use close_all_connections() for full shutdown.
        """
        import threading
        thread_id = threading.current_thread().ident

        with self._pool_lock:
            if thread_id in self._connection_pool:
                try:
                    self._connection_pool[thread_id].close()
                    del self._connection_pool[thread_id]
                    print(f"[ReferenceDB] Closed connection for thread {thread_id}")
                except Exception as e:
                    print(f"[ReferenceDB] Warning: Failed to close connection: {e}")

    def __enter__(self):
        """Context manager entry for 'with ReferenceDB() as db:' pattern."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes current thread's connection."""
        self.close()
        return False  # Don't suppress exceptions
        

    # ---- New lightweight helpers for UI (fast SQL-backed) ----
    def count_images_by_branch(self, project_id: int, branch_key: str) -> int:
        """
        Fast count for images associated with a branch (project_images table).
        Returns 0 if none found.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM project_images
                WHERE project_id = ? AND branch_key = ?
            """, (project_id, branch_key))
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def get_all_folders(self, project_id: int | None = None) -> list[dict]:
        """
        Return all folders as list of dicts: {id, parent_id, path, name}.

        Args:
            project_id: Filter folders by project_id (Schema v3.0.0 direct column filtering).
                       If None, returns all folders globally (backward compatibility).

        Returns:
            List of folder dicts with keys: id, parent_id, path, name

        Note: Schema v3.0.0 uses direct project_id column in photo_folders table.
              This is much faster than v2.0.0's junction table approach.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Schema v3.0.0: Direct project_id column filtering
                cur.execute("""
                    SELECT id, parent_id, path, name
                    FROM photo_folders
                    WHERE project_id = ?
                    ORDER BY parent_id IS NOT NULL, parent_id, name
                """, (project_id,))
            else:
                # No filter - return all folders globally (backward compatibility)
                cur.execute("SELECT id, parent_id, path, name FROM photo_folders ORDER BY parent_id IS NOT NULL, parent_id, name")
            rows = [{"id": r[0], "parent_id": r[1], "path": r[2], "name": r[3]} for r in cur.fetchall()]
        return rows

    def count_for_folder(self, folder_id: int, project_id: int | None = None) -> int:
        """
        Count photos in a folder (faster direct SQL for the UI).

        Args:
            folder_id: The folder ID to count photos in
            project_id: Filter by project_id (Schema v3.0.0). If None, counts all photos.

        Returns:
            Number of photos in the folder

        Note: Schema v3.0.0 uses direct project_id column in photo_metadata table.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id = ? AND project_id = ?", (folder_id, project_id))
            else:
                # No project filter
                cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id = ?", (folder_id,))
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    # ======================================================
    #           REFERENCE ENTRIES
    # ======================================================

    def rebuild_date_index(self, progress_cb=None):
        """
        Rebuild or refresh the date index used for date branches.
        Optionally calls progress_cb(percentage) to report progress.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            # Count total photos
            total = cur.execute("SELECT COUNT(*) FROM photo_metadata").fetchone()[0]
            if total == 0:
                if progress_cb:
                    progress_cb(100)
                return

            # Simple loop over photos to (re)index dates
            done = 0
            for row in cur.execute("SELECT id, capture_date FROM photo_metadata"):
                photo_id, date_str = row
                # --- place your actual date indexing logic here ---
                # e.g. insert/update into a date index table
                # (if you already have one, you can just skip this step)
                done += 1
                if progress_cb and total:
                    progress_cb(int(done * 100 / total))

            conn.commit()
            if progress_cb:
                progress_cb(100)

## merge_face_branches wird ein Wrapper ##
    def merge_face_branches(self, project_id, src_branch, target_branch, keep_label=None):
        """
#        Merge face clusters: move all faces from src_branch to target_branch.
#        Updates face_crops table and recalculates counts in face_branch_reps.
#        Returns number of faces moved.

        DEPRECATED: Use merge_face_clusters() to keep semantics, counts and undo consistent.
        Wrapper kept for backward compatibility.

        """
#        with self._connect() as conn:
#            cur = conn.cursor()
#
#            # Update face_crops table - move all faces from source to target
#            cur.execute(
#                "UPDATE face_crops SET branch_key=? WHERE project_id=? AND branch_key=?",
#                (target_branch, project_id, src_branch),
#            )
#            moved = cur.rowcount
#
#            # Recalculate count for target branch in face_branch_reps
#            cur.execute("""
#                UPDATE face_branch_reps
#                SET count = (
#                    SELECT COUNT(*)
#                    FROM face_crops
#                    WHERE project_id=? AND branch_key=?
#                )
#                WHERE project_id=? AND branch_key=?
#            """, (project_id, target_branch, project_id, target_branch))
#
#            # Update label if provided
#            if keep_label:
#                cur.execute(
#                    "UPDATE face_branch_reps SET label=? WHERE project_id=? AND branch_key=?",
#                    (keep_label, project_id, target_branch)
#                )
#
#            # Delete source branch from face_branch_reps
#            cur.execute(
#                "DELETE FROM face_branch_reps WHERE project_id=? AND branch_key=?",
#                (project_id, src_branch)
#            )
#
#            # Also update legacy project_images table if it exists (for backward compatibility)
#            try:
#                cur.execute(
#                    "UPDATE project_images SET branch_key=?, label=? WHERE project_id=? AND branch_key=?",
#                    (target_branch, keep_label, project_id, src_branch),
#                )
#            except Exception:
#                pass  # Table might not exist in new installations
#
#            conn.commit()
#            cur.close()
#            print(f"✅ merge_face_branches: moved {moved} faces from {src_branch} → {target_branch} (project {project_id})")
#            return moved

        res = self.merge_face_clusters(
            project_id=project_id,
            target_branch=target_branch,
            source_branches=[src_branch],
            log_undo=False
        )
        return int(res.get("moved_faces", 0))

    def delete_branch(self, project_id, branch_key):
        """
        Delete a branch and all its associated entries from the DB.
        Handles both legacy branches and face clusters.
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # Delete from legacy tables
            cur.execute("DELETE FROM branches WHERE project_id=? AND branch_key=?", (project_id, branch_key))
            cur.execute("DELETE FROM project_images WHERE project_id=? AND branch_key=?", (project_id, branch_key))

            # Delete from face detection tables
            cur.execute("DELETE FROM face_crops WHERE project_id=? AND branch_key=?", (project_id, branch_key))
            cur.execute("DELETE FROM face_branch_reps WHERE project_id=? AND branch_key=?", (project_id, branch_key))

            conn.commit()
            cur.close()
            print(f"🗑️ delete_branch: removed branch '{branch_key}' from project {project_id}")

 
    def insert_reference(self, filepath, label):
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO reference_entries (filepath, label) VALUES (?, ?)",
                    (filepath, label)
                )
        except Exception as e:
            print(f"[DB ERROR] insert_reference failed: {e}")

    def get_all_references(self):
        with self._connect() as conn:
            return conn.execute("SELECT id, label, filepath FROM reference_entries").fetchall()

    def delete_reference(self, filepath):
        with self._connect() as conn:
            conn.execute("DELETE FROM reference_entries WHERE filepath = ?", (filepath,))
            
            
    def get_all_references_existing(self):
        """Return only references whose files still exist."""
        rows = self.get_all_references()
        existing = [r for r in rows if os.path.isfile(r[2])]
        return existing

    def purge_missing_references(self) -> int:
        """Delete reference entries whose files no longer exist. Returns count removed."""
        rows = self.get_all_references()
        removed = 0
        with self._connect() as conn:
            for _id, label, path in rows:
                if not os.path.isfile(path):
                    conn.execute("DELETE FROM reference_entries WHERE id = ?", (_id,))
                    removed += 1
        return removed

    # ======================================================
    #           MATCH AUDIT
    # ======================================================
    def log_match_result(self, filename, label, score, match_mode=None):
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO match_audit (filename, matched_label, confidence, match_mode) VALUES (?, ?, ?, ?)",
                    (filename, label, score, match_mode)
                )
        except Exception as e:
            print(f"[DB ERROR] log_match_result failed: {e}")

    # ======================================================
    #           LABELS
    # ======================================================
    def insert_or_update_label(self, label: str, folder_path: str, threshold: float = 0.3):
        with self._connect() as conn:
            conn.execute('''
                INSERT INTO reference_labels (label, folder_path, threshold)
                VALUES (?, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    folder_path = excluded.folder_path,
                    threshold = excluded.threshold
            ''', (label, folder_path, threshold))

    def get_all_labels(self):
        with self._connect() as conn:
            return [row[0] for row in conn.execute("SELECT DISTINCT label FROM reference_labels")]

    def get_all_label_metadata(self):
        with self._connect() as conn:
            cur = conn.execute("SELECT label, folder_path, threshold FROM reference_labels")
            return [{"label": row[0], "folder": row[1], "threshold": row[2]} for row in cur.fetchall()]

    def get_label_folder(self, label: str):
        with self._connect() as conn:
            cur = conn.execute("SELECT folder_path FROM reference_labels WHERE label = ?", (label,))
            row = cur.fetchone()
            return row[0] if row else None

    def get_threshold_for_label(self, label: str) -> float:
        with self._connect() as conn:
            cur = conn.execute("SELECT threshold FROM reference_labels WHERE label = ?", (label,))
            row = cur.fetchone()
            return row[0] if row else 0.3

    def set_threshold_for_label(self, label: str, threshold: float):
        with self._connect() as conn:
            conn.execute("UPDATE reference_labels SET threshold = ? WHERE label = ?", (threshold, label))

    def delete_label(self, label: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM reference_labels WHERE label = ?", (label,))

    # ======================================================
    #           PROJECTS
    # ======================================================
    def create_project(self, name: str, folder: str, mode: str) -> int:
        """Create a new project and return its ID."""
        with self._connect() as conn:
            cur = conn.execute(
                '''
                INSERT INTO projects (name, folder, mode, created_at)
                VALUES (?, ?, ?, ?)
                ''',
                (name, folder, mode, datetime.now().isoformat())
            )
            return cur.lastrowid

    def get_all_projects(self):
        with self._connect() as conn:
            cur = conn.execute("SELECT id, name, mode, created_at FROM projects ORDER BY created_at DESC")
            return [{"id": row[0], "name": row[1], "mode": row[2], "created_at": row[3]} for row in cur.fetchall()]

    def delete_project(self, project_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ======================================================
    #           BRANCHES
    # ======================================================
    
    def create_branch(self, project_id: int, branch_key: str, display_name: str) -> int:
        """Create a branch if it doesn't exist already. Returns branch ID."""
        with self._connect() as conn:
            cur = conn.cursor()
            # check if it already exists
            cur.execute(
                "SELECT id FROM branches WHERE project_id = ? AND branch_key = ?",
                (project_id, branch_key)
            )
            row = cur.fetchone()
            if row:
                return row[0]  # ✅ already exists — don't reinsert

            cur.execute(
                '''
                INSERT INTO branches (project_id, branch_key, display_name)
                VALUES (?, ?, ?)
                ''',
                (project_id, branch_key, display_name)
            )
            conn.commit()
            return cur.lastrowid


    # --- when mode="faces", branches come from labels
    
    def get_branches(self, project_id: int):
        """
        Return all branches for a project from the branches table.
        Always ensures and returns 'all' at the top with photo count.
        Filters out date branches (by_date:*) as they're shown in the "By Date" section.
        """
        out = []
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT branch_key, display_name FROM branches WHERE project_id = ? ORDER BY branch_key",
                (project_id,)
            )
            rows = [{"branch_key": r[0], "display_name": r[1]} for r in cur.fetchall()]

            # Get total photo count for "All Photos" - Schema v3.0.0: filter by project_id
            cur.execute("SELECT COUNT(DISTINCT path) FROM photo_metadata WHERE project_id = ?", (project_id,))
            total_count = cur.fetchone()[0]

        # ensure 'all' exists and is first, with count
        has_all = any(r["branch_key"] == "all" for r in rows)
        if not has_all:
            out.append({"branch_key": "all", "display_name": f"📁 All Photos", "count": total_count})
        else:
            # move 'all' to front with count
            all_branch = next(r for r in rows if r["branch_key"] == "all")
            all_branch["count"] = total_count
            out.append(all_branch)
            rows = [r for r in rows if r["branch_key"] != "all"]

        # Filter out date branches (by_date:*) - they're redundant with "By Date" section
        rows = [r for r in rows if not r["branch_key"].startswith("by_date:")]

        out.extend(rows)
        return out

    # ---------- BRANCH UTILITIES (faces/date) ----------

    # ======================================================
    # 📁 FACE BRANCH MANAGEMENT HELPERS
    # ======================================================

    def delete_branches_for_project(self, project_id: int, prefix: str = "face_"):
        """Delete all branches (and associated project_images) for a project that start with a given prefix."""
        with self._connect() as conn:
            # delete project_images for those branches
            conn.execute(
                '''
                DELETE FROM project_images 
                WHERE project_id = ? AND branch_key LIKE ?
                ''',
                (project_id, f"{prefix}%")
            )
            # delete branches themselves
            conn.execute(
                '''
                DELETE FROM branches 
                WHERE project_id = ? AND branch_key LIKE ?
                ''',
                (project_id, f"{prefix}%")
            )
            conn.commit()
        print(f"🗑️ Deleted face branches with prefix '{prefix}' for project {project_id}")

    # ======================================================
    #           PROJECT IMAGES
    # ======================================================
    
    # --- Accept Label
    def add_project_image(self, project_id: int, image_path: str, branch_key: str = None, label: str = None) -> int:
        """Insert an image into a branch for a project. Supports optional face label."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                '''
                INSERT INTO project_images (project_id, image_path, branch_key, label)
                VALUES (?, ?, ?, ?)
                ''',
                (project_id, image_path, branch_key, label)
            )
            conn.commit()
            return cur.lastrowid


    def get_project_images(self, project_id: int, branch_key: str = None):
        """
        Return image paths for a given project.
        - If branch_key is None → return all UNIQUE images (DISTINCT paths).
        - If branch_key is 'all' or '__ALL__' → filter by branch_key='all' specifically.
        - If branch_key starts with 'face_' or 'date:' → filter by that branch.
        - If branch_key does NOT match any branch → try matching by label (for face branches like 'Person A').

        CRITICAL FIX: 'all' is now treated as a specific branch, not "return everything".
        This prevents count inflation (e.g., showing 554 instead of 298).
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # 🟢 Case 1: No branch specified - return all UNIQUE images
            if branch_key is None:
                cur.execute(
                    "SELECT DISTINCT image_path FROM project_images WHERE project_id = ?",
                    (project_id,)
                )
                rows = cur.fetchall()
                paths = [row[0] for row in rows]
                return paths

            # 🟢 Case 2: 'all' branch - filter specifically for branch_key='all'
            # CRITICAL: This is the fix for count inflation bug
            if branch_key == "all" or branch_key == "__ALL__":
                cur.execute(
                    "SELECT image_path FROM project_images WHERE project_id = ? AND branch_key = ?",
                    (project_id, 'all')
                )
                rows = cur.fetchall()
                paths = [row[0] for row in rows]
                return paths

            # 🟠 Case 3: exact branch_key match (date-based or face_x)
            cur.execute(
                "SELECT image_path FROM project_images WHERE project_id = ? AND branch_key = ?",
                (project_id, branch_key)
            )
            rows = cur.fetchall()
            if rows:
                paths = [row[0] for row in rows]
                return paths

            # 🔎 Case 4: fallback — maybe user clicked "Person A" which is a label, not a branch_key
            cur.execute(
                "SELECT image_path FROM project_images WHERE project_id = ? AND label = ?",
                (project_id, branch_key)
            )
            rows = cur.fetchall()
            if rows:
                paths = [row[0] for row in rows]
                return paths

            # ❌ Nothing found
            self.logger.debug(f"No images found for branch or label '{branch_key}' (project={project_id})")
            return []

        self.logger.debug(f"get_project_images(project={project_id}, branch={branch_key}) returned {len(rows)} rows")



    def _trash_move_label_folder(folder_path):
        try:
            trash_dir = os.path.join(ROOT_DIR, ".trash")
            os.makedirs(trash_dir, exist_ok=True)
            folder_name = os.path.basename(folder_path.rstrip(os.sep))
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(trash_dir, f"{folder_name}_{timestamp}")
            shutil.move(folder_path, backup_path)
            return True, backup_path
        except Exception as e:
            return False, str(e)

    # ======================================================
    # 📁 FACE BRANCH MANAGEMENT HELPERS
    # ======================================================

    def delete_project_images_for_project(self, project_id: int):
        """
        🗑️ Delete all image records associated with a given project.
        This is called before inserting new branches to ensure a clean state.
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM project_images WHERE project_id = ?",
                (project_id,)
            )
            conn.commit()
        print(f"🗑️ Cleared all project_images entries for project {project_id}")


    def ensure_all_branch(self, project_id: int):
        """Ensure that the 'all' branch exists for a project."""
        with self._connect() as conn:
            conn.execute(
                '''
                INSERT OR IGNORE INTO branches (project_id, branch_key, display_name)
                VALUES (?, 'all', '📁 All Photos')
                ''',
                (project_id,)
            )
            conn.commit()
        print(f"📁 Ensured 'all' branch exists for project {project_id}")


    def ensure_branch(self, project_id: int, branch_key: str, display_name: str) -> int:
        """Ensure a branch exists; create it if missing. Returns branch ID."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM branches WHERE project_id = ? AND branch_key = ?",
                (project_id, branch_key)
            )
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                '''
                INSERT INTO branches (project_id, branch_key, display_name)
                VALUES (?, ?, ?)
                ''',
                (project_id, branch_key, display_name)
            )
            conn.commit()
            new_id = cur.lastrowid
        print(f"📁 Ensured branch '{branch_key}' created for project {project_id}")
        return new_id


    def add_project_images_bulk(self, project_id: int, image_paths: list, branch_key: str = None, label: str = None):
        """Insert many images into a branch efficiently. Ignores duplicates."""
        if not image_paths:
            return 0
        with self._connect() as conn:
            cur = conn.cursor()
            cur.executemany(
                '''
                INSERT OR IGNORE INTO project_images (project_id, image_path, branch_key, label)
                VALUES (?, ?, ?, ?)
                ''',
                [(project_id, path, branch_key, label) for path in image_paths]
            )
            conn.commit()
        print(f"📸 Bulk-inserted {len(image_paths)} images into branch '{branch_key}' (label={label}) for project {project_id}")
        return len(image_paths)


    # ======================================================
    # 📁 Representative face per branch 
    # ======================================================

    def delete_face_branch_reps_for_project(self, project_id: int):
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("DELETE FROM face_branch_reps WHERE project_id=?", (project_id,))
            con.commit()

    def upsert_face_branch_rep(self, project_id: int, branch_key: str, label: str | None, count: int, centroid_bytes: bytes | None, rep_path: str | None, rep_thumb_png: bytes | None):
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                INSERT INTO face_branch_reps (project_id, branch_key, label, count, centroid, rep_path, rep_thumb_png)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, branch_key) DO UPDATE SET
                    label=excluded.label,
                    count=excluded.count,
                    centroid=excluded.centroid,
                    rep_path=excluded.rep_path,
                    rep_thumb_png=excluded.rep_thumb_png
            """, (project_id, branch_key, label, count, centroid_bytes, rep_path, rep_thumb_png))
            con.commit()

    def get_face_branch_reps(self, project_id: int) -> list[dict]:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute("""
                SELECT branch_key, label, count, centroid, rep_path, rep_thumb_png
                FROM face_branch_reps
                WHERE project_id=?
                ORDER BY branch_key ASC
            """, (project_id,))
            rows = cur.fetchall()
            result = []
            for branch_key, label, cnt, centroid, rep_path, rep_png in rows:
                result.append({
                    "id": branch_key,               # use branch_key like "face_0" as the tree iid
                    "name": label or branch_key,    # label shown in the tree
                    "count": cnt or 0,
                    "centroid_bytes": centroid,
                    "rep_path": rep_path,
                    "rep_thumb_png": rep_png,       # bytes (PNG). UI will decode to PhotoImage
                })
            return result


    # ======================================================
    #           FACE CROPS / REPRESENTATIVES    
    # ---------- FACE CROP HELPERS (PATH-BASED) ----------
    # ======================================================

    def clear_face_crops_for_project(self, project_id: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM face_crops WHERE project_id = ?", (project_id,))


    def add_face_crops_bulk(self, project_id: int, rows: list[tuple]) -> None:
        """
        rows: (branch_key, image_path, crop_path, is_representative: bool/int)
        Idempotent thanks to UNIQUE(project_id, branch_key, crop_path).
        """
        if not rows:
            return
        with self._connect() as con:
            con.executemany("""
                INSERT OR IGNORE INTO face_crops (project_id, branch_key, image_path, crop_path, is_representative)
                VALUES (?, ?, ?, ?, ?)
            """, [(project_id, b, p, c, int(rep)) for (b, p, c, rep) in rows])


    def get_face_branch_summary(self, project_id: int) -> list[dict]:
        """
        Return face-branch rows with a representative crop if present.
        Looks first in face_crops (is_representative=1), then falls back to
        face_branch_reps.rep_path if the first is missing.
        """
        sql = """
        WITH reps AS (
            SELECT branch_key, crop_path
            FROM face_crops
            WHERE project_id = ? AND is_representative = 1
        ),
        reps_fallback AS (
            SELECT branch_key, rep_path AS crop_path
            FROM face_branch_reps
            WHERE project_id = ?
        ),
        counts AS (
            SELECT branch_key, COUNT(*) AS cnt
            FROM face_crops
            WHERE project_id = ?
            GROUP BY branch_key
        )
        SELECT b.branch_key,
               b.display_name,
               COALESCE(r.crop_path, rf.crop_path) AS rep_crop,
               COALESCE(c.cnt, 0) AS face_count
        FROM branches b
        LEFT JOIN reps r          ON r.branch_key  = b.branch_key
        LEFT JOIN reps_fallback rf ON rf.branch_key = b.branch_key
        LEFT JOIN counts c        ON c.branch_key  = b.branch_key
        WHERE b.project_id = ? AND b.branch_key LIKE 'face_%'
        ORDER BY b.branch_key;
        """
        with self._connect() as con:
            cur = con.execute(sql, (project_id, project_id, project_id, project_id))
            return [{
                "branch_key": row[0],
                "display_name": row[1],
                "rep_crop": row[2],
                "count": row[3],
            } for row in cur.fetchall()]


    def reset_face_data_for_project(self, project_id: int):
        """Deletes all face-related rows for a clean rebuild."""
        with self._connect() as con:
            con.execute("DELETE FROM face_crops WHERE project_id=?", (project_id,))
            con.execute("DELETE FROM face_branch_reps WHERE project_id=?", (project_id,))
            con.execute("DELETE FROM branches WHERE project_id=? AND branch_key LIKE 'face_%'", (project_id,))
            con.commit()
        print(f"🧹 Reset all face data for project {project_id}.")


    # ============================================================
    # 🧠 DB helper for manual face merging
    # ============================================================
        
    def merge_faces(self, project_id, target_label, face_ids):
        """
        Assigns selected face_crops (by id) to the same label (target_label).
        After this, all these face_crops share that label in DB.
        """
        if not face_ids:
            return 0

        placeholders = ",".join("?" * len(face_ids))
        query = f"UPDATE face_crops SET branch_key=?, is_representative=0 WHERE project_id=? AND id IN ({placeholders})"
        params = [f"face_{target_label}", project_id] + face_ids

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            updated = cur.rowcount
            conn.commit()
            print(f"✅ merge_faces: reassigned {updated} face_crops to label '{target_label}' (project {project_id})")
            return updated


    # ======================================================
    #           FACE LABEL MERGE SUPPORT
    # ======================================================
    def merge_face_labels(self, target_label: str, source_labels: list[str], project_id: int | None = None):
        """
        Merge multiple face labels into a target label.
        Moves all reference_entries and (optionally) project-related entries.
        If project_id is provided, also merges project_images / face_crops / face_branch_reps.
        """
        if not source_labels:
            return
        with self._connect() as conn:
            for src in source_labels:
                if src == target_label:
                    continue

                # --- Global reference tables ---
                conn.execute(
                    "UPDATE reference_entries SET label = ? WHERE label = ?",
                    (target_label, src)
                )
                conn.execute("DELETE FROM reference_labels WHERE label = ?", (src,))

                # --- Project-scoped tables (optional) ---
                if project_id is not None:
                    # Update label fields in project_images
                    conn.execute(
                        "UPDATE project_images SET label = ? WHERE project_id = ? AND label = ?",
                        (target_label, project_id, src)
                    )
                    # Update face_crops branch names
                    conn.execute(
                        "UPDATE face_crops SET branch_key = ? WHERE project_id = ? AND branch_key = ?",
                        (f'face_{target_label}', project_id, f'face_{src}')
                    )
                    # Remove old representative branch rows
                    conn.execute(
                        "DELETE FROM face_branch_reps WHERE project_id = ? AND branch_key = ?",
                        (project_id, f'face_{src}')
                    )
            conn.commit()


    def rename_branch_display_name(self, project_id: int, branch_key: str, new_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE branches SET display_name = ? WHERE project_id = ? AND branch_key = ?",
                (new_name, project_id, branch_key)
            )
            conn.commit()


    def log_export_action(self, project_id, branch_key, count, source_paths, dest_paths, dest_folder):
        """Archive export action in DB (minimal)."""
        import json, datetime
        ts = datetime.datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO export_history (project_id, branch_key, photo_count, source_paths, dest_paths, dest_folder, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            project_id,
            branch_key,
            count,
            json.dumps(source_paths),
            json.dumps(dest_paths),
            dest_folder,
            ts
        ))
        conn.commit()


    def scan_repository(self, repo_path: str, project_id: int = None):
        """Recursively scan a photo repo and index in photo_folders and photo_metadata.

        Args:
            repo_path: Path to repository to scan
            project_id: Project ID (uses default if None)
        """
        repo = Path(repo_path).resolve()
        if not repo.exists():
            raise FileNotFoundError(f"Repository not found: {repo}")

        # Get or create default project
        if project_id is None:
            project_id = self._get_or_create_default_project()

        supported_ext = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.tif', '.tiff'}

        def get_or_create_folder(conn, folder_path: Path):
            parent_id = None
            parent = folder_path.parent if folder_path.parent != folder_path else None
            if parent and parent.exists():
                cur = conn.cursor()
                cur.execute("SELECT id FROM photo_folders WHERE path=? AND project_id=?", (str(parent), project_id))
                prow = cur.fetchone()
                if prow:
                    parent_id = prow[0]
                else:
                    parent_id = get_or_create_folder(conn, parent)

            cur = conn.cursor()
            cur.execute("SELECT id FROM photo_folders WHERE path=? AND project_id=?", (str(folder_path), project_id))
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                "INSERT INTO photo_folders (parent_id, path, name, project_id) VALUES (?, ?, ?, ?)",
                (parent_id, str(folder_path), folder_path.name, project_id)
            )
            conn.commit()
            return cur.lastrowid

        with self._connect() as conn:
            for root, dirs, files in os.walk(repo):
                folder_path = Path(root)
                folder_id = get_or_create_folder(conn, folder_path)
                for f in files:
                    p = folder_path / f
                    if p.suffix.lower() not in supported_ext:
                        continue
                    stat = p.stat()
                    size_kb = stat.st_size / 1024
                    modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                    conn.execute("""
                        INSERT INTO photo_metadata (path, folder_id, name, size_kb, modified, width, height, embedding, tags, updated_at)
                        VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            folder_id=excluded.folder_id,
                            size_kb=excluded.size_kb,
                            modified=excluded.modified,
                            updated_at=excluded.updated_at
                    """, (str(p), folder_id, p.name, size_kb, modified, modified))
            conn.commit()


    def get_child_folders(self, parent_id, project_id: int | None = None):
        """
        Return child folders for a given parent.

        Args:
            parent_id: Parent folder ID. Use None for root folders.
            project_id: Filter folders by project_id (Schema v3.0.0 direct column filtering).
                       If None, returns all folders (backward compatibility).

        Returns:
            List of dicts with keys: id, name

        Note: Use IS NULL for root folders, because `parent_id = NULL` returns nothing in SQL.
              Schema v3.0.0 uses direct project_id column in photo_folders table.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Schema v3.0.0: Direct project_id column filtering
                if parent_id is None:
                    cur.execute("""
                        SELECT id, name
                        FROM photo_folders
                        WHERE parent_id IS NULL AND project_id = ?
                        ORDER BY name
                    """, (project_id,))
                else:
                    cur.execute("""
                        SELECT id, name
                        FROM photo_folders
                        WHERE parent_id = ? AND project_id = ?
                        ORDER BY name
                    """, (parent_id, project_id))
            else:
                # No filter - return all folders (backward compatibility)
                if parent_id is None:
                    cur.execute("""
                        SELECT id, name FROM photo_folders
                        WHERE parent_id IS NULL
                        ORDER BY name
                    """)
                else:
                    cur.execute("""
                        SELECT id, name FROM photo_folders
                        WHERE parent_id = ?
                        ORDER BY name
                    """, (parent_id,))
            rows = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
        return rows


    def get_descendant_folder_ids(self, folder_id: int, project_id: int | None = None) -> list[int]:
        """
        Recursively get all descendant folder IDs for a given folder.

        Args:
            folder_id: The root folder ID
            project_id: Filter folders by project_id (Schema v3.0.0). If None, gets all descendants.

        Returns:
            List including the folder_id itself and all nested subfolders

        Note: Schema v3.0.0 filters by direct project_id column in photo_folders table.
        """
        result = [folder_id]
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                # Get immediate children
                if project_id is not None:
                    # Schema v3.0.0: Filter by project_id
                    cur.execute("SELECT id FROM photo_folders WHERE parent_id = ? AND project_id = ?", (folder_id, project_id))
                else:
                    # No project filter
                    cur.execute("SELECT id FROM photo_folders WHERE parent_id = ?", (folder_id,))
                children = [r[0] for r in cur.fetchall()]

                # Recursively get descendants of each child
                for child_id in children:
                    result.extend(self.get_descendant_folder_ids(child_id, project_id=project_id))

            return result
        except Exception as e:
            print(f"[DB ERROR] get_descendant_folder_ids failed: {e}")
            return [folder_id]  # Fallback to just the folder itself

    def get_images_by_folder(self, folder_id: int, include_subfolders: bool = True, project_id: int | None = None):
        """
        Return list of image paths belonging to the given folder_id.

        Args:
            folder_id: The folder ID to query
            include_subfolders: If True (default), includes photos from all nested subfolders
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos in folder.

        Returns:
            List of photo paths

        Note: Schema v3.0.0 uses direct project_id column in photo_metadata table.
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()

                if include_subfolders:
                    # Get folder and all descendant folder IDs
                    folder_ids = self.get_descendant_folder_ids(folder_id, project_id=project_id)
                    placeholders = ','.join('?' * len(folder_ids))

                    if project_id is not None:
                        # Schema v3.0.0: Filter by project_id
                        query = f"SELECT path FROM photo_metadata WHERE folder_id IN ({placeholders}) AND project_id = ? ORDER BY path"
                        cur.execute(query, folder_ids + [project_id])
                    else:
                        # No project filter
                        query = f"SELECT path FROM photo_metadata WHERE folder_id IN ({placeholders}) ORDER BY path"
                        cur.execute(query, folder_ids)

                    rows = [r[0] for r in cur.fetchall()]
                    print(f"[DB] get_images_by_folder({folder_id}, subfolders=True, project={project_id}) -> {len(rows)} paths from {len(folder_ids)} folders")
                else:
                    # Only this folder
                    if project_id is not None:
                        # Schema v3.0.0: Filter by project_id
                        cur.execute("SELECT path FROM photo_metadata WHERE folder_id = ? AND project_id = ? ORDER BY path", (folder_id, project_id))
                    else:
                        # No project filter
                        cur.execute("SELECT path FROM photo_metadata WHERE folder_id = ? ORDER BY path", (folder_id,))

                    rows = [r[0] for r in cur.fetchall()]
                    print(f"[DB] get_images_by_folder({folder_id}, subfolders=False, project={project_id}) -> {len(rows)} paths")

                return rows
        except Exception as e:
            print(f"[DB ERROR] get_images_by_folder failed: {e}")
            return []

    def count_photos_in_folder(self, folder_id: int, project_id: int | None = None) -> int:
        """
        Count photos in a folder.

        Args:
            folder_id: The folder ID
            project_id: Filter by project_id (Schema v3.0.0). If None, counts all photos.

        Returns:
            Number of photos in the folder

        Note: Schema v3.0.0 uses direct project_id column in photo_metadata table.
        """
        with self._connect() as conn:
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                cur = conn.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=? AND project_id=?", (folder_id, project_id))
            else:
                # No project filter
                cur = conn.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (folder_id,))
            return cur.fetchone()[0] or 0

    def update_folder_counts(self):
        """Recalculate photo counts per folder for Sidebar display."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TEMP VIEW IF NOT EXISTS folder_counts AS
                SELECT folder_id, COUNT(*) AS count
                FROM photo_metadata
                GROUP BY folder_id
            """)
            conn.commit()

    def get_folder_photo_count(self, folder_id, project_id: int | None = None):
        """
        Get photo count for a specific folder.

        Args:
            folder_id: The folder ID
            project_id: Filter by project_id (Schema v3.0.0). If None, counts all photos.

        Returns:
            Number of photos in the folder

        Note: Schema v3.0.0 uses direct project_id column in photo_metadata table.
        """
        with self._connect() as conn:
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                cur = conn.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=? AND project_id=?", (folder_id, project_id))
            else:
                # No project filter
                cur = conn.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (folder_id,))
            row = cur.fetchone()
            return row[0] if row else 0

    # === Phase 3: Drag & Drop Support ===
    def set_folder_for_image(self, path: str, folder_id: int):
        """
        Update the folder_id for a specific image (drag & drop support).

        Args:
            path: Image file path
            folder_id: New folder ID to assign
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE photo_metadata SET folder_id = ? WHERE path = ?",
                (folder_id, path)
            )
            conn.commit()
            print(f"[DB] Updated folder_id={folder_id} for image: {path}")

    def get_images_by_branch(self, project_id: int, branch_key: str):
        """
        Return list of image paths based on branch selection.
        Uses old project_images table for compatibility.
        """
        print(f"[get_images_by_branch] project_id={project_id}, branch_key='{branch_key}'")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT image_path FROM project_images
                WHERE project_id = ? AND branch_key = ?
            """, (project_id, branch_key))
            results = [row[0] for row in cur.fetchall()]
            print(f"[get_images_by_branch] Found {len(results)} photos")
            if len(results) == 0:
                # Debug: show what branch_keys exist in DB
                cur.execute("""
                    SELECT DISTINCT branch_key FROM project_images WHERE project_id = ?
                """, (project_id,))
                existing_keys = [row[0] for row in cur.fetchall()]
                print(f"[get_images_by_branch] Available branch_keys in DB: {existing_keys[:10]}")
            return results

    def _get_or_create_default_project(self):
        """Get or create default project for scans. Returns project_id."""
        with self._connect() as conn:
            cur = conn.cursor()
            # Try to get first project
            cur.execute("SELECT id FROM projects ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            if row:
                return row[0]

            # No projects exist - create default
            cur.execute(
                "INSERT INTO projects (name, folder, mode) VALUES (?, ?, ?)",
                ("Default Project", ".", "date")
            )
            conn.commit()
            return cur.lastrowid

    def ensure_folder(self, path: str, name: str, parent_id: int | None, project_id: int = None):
        """Return folder_id; create if not exists.

        Args:
            path: Full folder path
            name: Folder name
            parent_id: Parent folder ID (None for root)
            project_id: Project ID (uses default if None)
        """
        # If no project_id provided, get or create default project
        if project_id is None:
            project_id = self._get_or_create_default_project()

        with self._connect() as conn:
            cur = conn.cursor()
            # Check if folder exists for this project
            cur.execute("SELECT id FROM photo_folders WHERE path=? AND project_id=?", (path, project_id))
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                "INSERT INTO photo_folders (name, path, parent_id, project_id) VALUES (?,?,?,?)",
                (name, path, parent_id, project_id)
            )
            conn.commit()
            return cur.lastrowid

    def insert_or_update_photo(self, path, folder_id, size, mtime, width, height):
        """Upsert into photo_metadata based on path."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM photo_metadata WHERE path=?", (path,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE photo_metadata
                    SET folder_id=?, size=?, mtime=?, width=?, height=?
                    WHERE path=?
                """, (folder_id, size, mtime, width, height, path))
            else:
                cur.execute("""
                    INSERT INTO photo_metadata (path, folder_id, size, mtime, width, height)
                    VALUES (?,?,?,?,?,?)
                """, (path, folder_id, size, mtime, width, height))
            conn.commit()

    def get_photo_metadata_by_path(self, path: str):
        """Return all metadata columns for a given photo path."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT path, folder_id, size_kb, modified, width, height, embedding, date_taken, tags
                FROM photo_metadata
                WHERE path = ?
            """, (path,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    # --- 🎬 Video Methods (Phase 4.3) ---

    def get_video_by_path(self, path: str, project_id: int):
        """
        Get video metadata by file path.

        Args:
            path: Video file path
            project_id: Project ID

        Returns:
            Video metadata dict or None
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, path, folder_id, project_id, size_kb, modified,
                       duration_seconds, width, height, fps, codec, bitrate,
                       date_taken, created_ts, created_date, created_year,
                       metadata_status, thumbnail_status
                FROM video_metadata
                WHERE path = ? AND project_id = ?
            """, (path, project_id))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    # ---------------------------
    # Metadata backfill helpers
    # ---------------------------
    def ensure_metadata_columns(self) -> None:
        """
        Idempotent: ensure metadata_status and metadata_fail_count exist.
        Call before running any backfill jobs.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {r[1] for r in cur.fetchall()}
            if "metadata_status" not in cols:
                try:
                    cur.execute("ALTER TABLE photo_metadata ADD COLUMN metadata_status TEXT DEFAULT 'pending'")
                except Exception:
                    pass
            if "metadata_fail_count" not in cols:
                try:
                    cur.execute("ALTER TABLE photo_metadata ADD COLUMN metadata_fail_count INTEGER DEFAULT 0")
                except Exception:
                    pass
            # ensure index
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_status    ON photo_metadata(metadata_status)")
            except Exception:
                pass
            conn.commit()

    def get_images_missing_metadata(self, limit: int | None = None, max_failures: int = 3) -> list[str]:
        """
        Return a list of photo paths that need metadata extraction.
        Criteria:
         - width IS NULL OR height IS NULL OR date_taken IS NULL
         - OR metadata_status IN ('pending','failed_retry') and metadata_fail_count < max_failures
        This allows re-trying transient failures up to max_failures.
        """
        q = """
            SELECT path FROM photo_metadata
            WHERE (width IS NULL OR height IS NULL OR date_taken IS NULL)
               OR (metadata_status IN ('pending','failed_retry') AND COALESCE(metadata_fail_count,0) < ?)
        """
        params = [int(max_failures)]
        if limit and limit > 0:
            q += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(q, params)
            rows = cur.fetchall()
            return [r[0] for r in rows]

    def mark_metadata_success(self, path: str, width: int | None, height: int | None, date_taken: str | None) -> bool:
        """
        Mark a row as successfully extracted: set width/height/date_taken, metadata_status='ok', metadata_fail_count=0.
        Returns True on success.
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE photo_metadata
                    SET width = ?, height = ?, date_taken = ?, metadata_status = 'ok', metadata_fail_count = 0, updated_at = ?
                    WHERE path = ?
                """, (width, height, date_taken, time.strftime("%Y-%m-%d %H:%M:%S"), path))
                conn.commit()
            return True
        except Exception as e:
            safe = getattr(self, "safe_log", None)
            if safe:
                try:
                    safe(f"[DB] mark_metadata_success failed for {path}: {e}")
                except Exception:
                    pass
            return False

    def mark_metadata_failure(self, path: str, error: str | None = None, max_retries: int = 3) -> bool:
        """
        Increment metadata_fail_count and set metadata_status to 'failed_retry' or 'failed' if threshold reached.
        Also logs the error in match_audit (lightweight reuse).
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COALESCE(metadata_fail_count,0) FROM photo_metadata WHERE path = ?", (path,))
                row = cur.fetchone()
                if not row:
                    # missing row: nothing to mark
                    return False
                fail_count = (row[0] or 0) + 1
                status = 'failed' if fail_count >= int(max_retries) else 'failed_retry'
                cur.execute("""
                    UPDATE photo_metadata
                    SET metadata_fail_count = ?, metadata_status = ?, updated_at = ?
                    WHERE path = ?
                """, (fail_count, status, time.strftime("%Y-%m-%d %H:%M:%S"), path))
                # lightweight logging in match_audit for diagnostic purposes
                try:
                    cur.execute("""
                        INSERT INTO match_audit (filename, matched_label, confidence, match_mode)
                        VALUES (?, ?, ?, ?)
                    """, (path, f"[meta_fail:{status}]", None, error or "meta_backfill"))
                except Exception:
                    pass
                conn.commit()
            return True
        except Exception as e:
            safe = getattr(self, "safe_log", None)
            if safe:
                try:
                    safe(f"[DB] mark_metadata_failure failed for {path}: {e}")
                except Exception:
                    pass
            return False

    def reset_metadata_failures(self, path: str) -> bool:
        """Reset metadata status and fail count for manual retry."""
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE photo_metadata SET metadata_status='pending', metadata_fail_count=0 WHERE path = ?", (path,))
                conn.commit()
            return True
        except Exception:
            return False

    def get_metadata_stats(self) -> dict:
        """Return counts: pending, ok, failed_retry, failed, total_missing."""
        with self._connect() as conn:
            cur = conn.cursor()
            stats = {}
            for s in ("ok", "pending", "failed_retry", "failed"):
                cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE metadata_status = ?", (s,))
                stats[s] = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE width IS NULL OR height IS NULL OR date_taken IS NULL")
            stats["missing_metadata"] = cur.fetchone()[0] or 0
            return stats

    # Keep existing methods below mostly unchanged — but ensure upsert_photo_metadata sets metadata_status ok when metadata present.
    def upsert_photo_metadata(self, path, folder_id, size_kb, modified, width, height, date_taken=None, tags=None, project_id=None):
        """
        Upsert row; if migration (created_* columns) exists, also write created_ts/date/year
        derived from date_taken (preferred) or modified (fallback). If not migrated yet,
        it safely uses the legacy column set.
        This updated version also sets metadata_status to 'ok' and metadata_fail_count=0 if width/height are provided.

        Args:
            project_id: Project ID (uses default if None)
        """
        # Get or create default project if not provided
        if project_id is None:
            project_id = self._get_or_create_default_project()

        with self._connect() as conn:
            cur = conn.cursor()
            ok_meta = (width is not None and height is not None) or (date_taken is not None)
            if self._has_created_columns():
                c_ts, c_date, c_year = self._normalize_created_fields(date_taken, modified)
                # When metadata is present, mark metadata_status ok
                if ok_meta:
                    cur.execute("""
                        INSERT INTO photo_metadata (path, folder_id, project_id, size_kb, modified, width, height, embedding, date_taken, tags, updated_at,
                                                    created_ts, created_date, created_year, metadata_status, metadata_fail_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'ok', 0)
                        ON CONFLICT(path, project_id) DO UPDATE SET
                            folder_id = excluded.folder_id,
                            size_kb   = excluded.size_kb,
                            modified  = excluded.modified,
                            width     = excluded.width,
                            height    = excluded.height,
                            date_taken= excluded.date_taken,
                            tags      = excluded.tags,
                            updated_at= excluded.updated_at,
                            created_ts   = COALESCE(excluded.created_ts, created_ts),
                            created_date = COALESCE(excluded.created_date, created_date),
                            created_year = COALESCE(excluded.created_year, created_year),
                            metadata_status = CASE WHEN excluded.width IS NOT NULL OR excluded.date_taken IS NOT NULL THEN 'ok' ELSE metadata_status END,
                            metadata_fail_count = CASE WHEN excluded.width IS NOT NULL OR excluded.date_taken IS NOT NULL THEN 0 ELSE metadata_fail_count END
                    """, (
                        path, folder_id, project_id, size_kb, modified, width, height,
                        date_taken, tags, time.strftime("%Y-%m-%d %H:%M:%S"),
                        c_ts, c_date, c_year
                    ))
                else:
                    cur.execute("""
                        INSERT INTO photo_metadata (path, folder_id, project_id, size_kb, modified, width, height, embedding, date_taken, tags, updated_at,
                                                    created_ts, created_date, created_year)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path, project_id) DO UPDATE SET
                            folder_id = excluded.folder_id,
                            size_kb   = excluded.size_kb,
                            modified  = excluded.modified,
                            width     = excluded.width,
                            height    = excluded.height,
                            date_taken= excluded.date_taken,
                            tags      = excluded.tags,
                            updated_at= excluded.updated_at,
                            created_ts   = COALESCE(excluded.created_ts, created_ts),
                            created_date = COALESCE(excluded.created_date, created_date),
                            created_year = COALESCE(excluded.created_year, created_year)
                    """, (
                        path, folder_id, project_id, size_kb, modified, width, height,
                        date_taken, tags, time.strftime("%Y-%m-%d %H:%M:%S"),
                        c_ts, c_date, c_year
                    ))
            else:
                if ok_meta:
                    cur.execute("""
                        INSERT INTO photo_metadata (path, folder_id, project_id, size_kb, modified, width, height, embedding, date_taken, tags, updated_at, metadata_status, metadata_fail_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'ok', 0)
                        ON CONFLICT(path, project_id) DO UPDATE SET
                            folder_id = excluded.folder_id,
                            size_kb   = excluded.size_kb,
                            modified  = excluded.modified,
                            width     = excluded.width,
                            height    = excluded.height,
                            date_taken= excluded.date_taken,
                            tags      = excluded.tags,
                            updated_at= excluded.updated_at,
                            metadata_status = CASE WHEN excluded.width IS NOT NULL OR excluded.date_taken IS NOT NULL THEN 'ok' ELSE metadata_status END,
                            metadata_fail_count = CASE WHEN excluded.width IS NOT NULL OR excluded.date_taken IS NOT NULL THEN 0 ELSE metadata_fail_count END
                    """, (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, time.strftime("%Y-%m-%d %H:%M:%S")))
                else:
                    cur.execute("""
                        INSERT INTO photo_metadata (path, folder_id, project_id, size_kb, modified, width, height, embedding, date_taken, tags, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                        ON CONFLICT(path, project_id) DO UPDATE SET
                            folder_id = excluded.folder_id,
                            size_kb   = excluded.size_kb,
                            modified  = excluded.modified,
                            width     = excluded.width,
                            height    = excluded.height,
                            date_taken= excluded.date_taken,
                            tags      = excluded.tags,
                            updated_at= excluded.updated_at
                    """, (path, folder_id, project_id, size_kb, modified, width, height, date_taken, tags, time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()


    # --------------------------
    # Date helpers & queries
    # --------------------------
    def _has_created_columns(self) -> bool:
        """Detect once and cache whether created_* columns exist."""
        if self._created_cols_present is not None:
            return self._created_cols_present
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {r[1] for r in cur.fetchall()}
            self._created_cols_present = all(c in cols for c in ("created_ts", "created_date", "created_year"))
            return self._created_cols_present

    def _normalize_created_fields(self, date_taken: str | None, modified: str | None):
        """
        Return (created_ts:int|None, created_date:'YYYY-MM-DD'|None, created_year:int|None).
        Uses date_taken if parseable, else falls back to modified.
        """
        import datetime as dt
        def parse_one(s: str | None):
            if not s:
                return None
            fmts = [
                "%Y:%m:%d %H:%M:%S",   # EXIF DateTimeOriginal
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%d.%m.%Y %H:%M:%S",
                "%Y-%m-%d",
            ]
            for f in fmts:
                try:
                    return dt.datetime.strptime(s, f)
                except Exception:
                    pass
            return None
        t = parse_one(date_taken) or parse_one(modified)
        if not t:
            return (None, None, None)
        ts = int(t.timestamp())
        dstr = t.strftime("%Y-%m-%d")
        return (ts, dstr, int(dstr[:4]))

    # CLI migration entrypoint for metadata columns:
    def ensure_created_date_fields(self) -> None:
        """Add created_ts / created_date / created_year + indexes (idempotent)."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {row[1] for row in cur.fetchall()}
            if "created_ts" not in cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER")
            if "created_date" not in cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_date TEXT")
            if "created_year" not in cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_year  ON photo_metadata(created_year)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_date  ON photo_metadata(created_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_ts    ON photo_metadata(created_ts)")
            conn.commit()

    # For convenience we expose a small CLI to add metadata columns from the command line.
    @staticmethod
    def _cli():
        ap = argparse.ArgumentParser(description="ReferenceDB utilities")
        ap.add_argument("--migrate-metadata", action="store_true", help="Ensure metadata columns exist in photo_metadata")
        ap.add_argument("--show-meta-stats", action="store_true", help="Show metadata status counts")
        args = ap.parse_args()
        db = ReferenceDB()
        if args.migrate_metadata:
            db.ensure_metadata_columns()
            print("metadata columns ensured")
        if args.show_meta_stats:
            print(json.dumps(db.get_metadata_stats(), indent=2))


    def list_years_with_counts(self, project_id: int | None = None) -> list[tuple[int, int]]:
        """
        Get list of years with photo counts.

        Args:
            project_id: Filter by project_id if provided, otherwise use all photos globally

        Returns:
            [(year, count)] newest first. Returns [] if migration not yet run.
        """
        if not self._has_created_columns():
            return []
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
                # Uses compound index idx_photo_metadata_project_date for fast filtering
                cur.execute("""
                    SELECT created_year, COUNT(*)
                    FROM photo_metadata
                    WHERE project_id = ?
                      AND created_year IS NOT NULL
                    GROUP BY created_year
                    ORDER BY created_year DESC
                """, (project_id,))
            else:
                # No project filter - use all photos globally
                cur.execute("""
                    SELECT created_year, COUNT(*)
                    FROM photo_metadata
                    WHERE created_year IS NOT NULL
                    GROUP BY created_year
                    ORDER BY created_year DESC
                """)
            return cur.fetchall()

    def list_days_in_year(self, year: int) -> list[tuple[str, int]]:
        """[(YYYY-MM-DD, count)] newest first. Returns [] if migration not yet run."""
        if not self._has_created_columns():
            return []
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT created_date, COUNT(*)
                FROM photo_metadata
                WHERE created_year = ?
                GROUP BY created_date
                ORDER BY created_date DESC
            """, (year,))
            return cur.fetchall()

    def get_images_by_year(self, year: int, project_id: int | None = None) -> list[str]:
        """
        All paths for a year. Returns [] if migration not yet run.

        Args:
            year: Year (e.g. 2024)
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos.
        """
        if not self._has_created_columns():
            return []
        with self._connect() as conn:
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                cur = conn.execute("""
                    SELECT path
                    FROM photo_metadata
                    WHERE created_year = ? AND project_id = ?
                    ORDER BY created_ts ASC, path ASC
                """, (year, project_id))
            else:
                # No project filter
                cur = conn.execute("""
                    SELECT path
                    FROM photo_metadata
                    WHERE created_year = ?
                    ORDER BY created_ts ASC, path ASC
                """, (year,))
            return [r[0] for r in cur.fetchall()]

    def get_images_by_date(self, ymd: str, project_id: int | None = None) -> list[str]:
        """
        All paths for a day (YYYY-MM-DD). Returns [] if migration not yet run.

        Args:
            ymd: Date string (YYYY-MM-DD)
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos.
        """
        if not self._has_created_columns():
            return []
        with self._connect() as conn:
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                cur = conn.execute("""
                    SELECT path
                    FROM photo_metadata
                    WHERE created_date = ? AND project_id = ?
                    ORDER BY created_ts ASC, path ASC
                """, (ymd, project_id))
            else:
                # No project filter
                cur = conn.execute("""
                    SELECT path
                    FROM photo_metadata
                    WHERE created_date = ?
                    ORDER BY created_ts ASC, path ASC
                """, (ymd,))
            return [r[0] for r in cur.fetchall()]

    def get_videos_by_date(self, ymd: str, project_id: int | None = None) -> list[str]:
        """
        Get all video paths for a specific day (YYYY-MM-DD).

        Args:
            ymd: Date string (YYYY-MM-DD)
            project_id: Filter by project_id if provided, otherwise return all videos

        Returns:
            List of video paths for that day, ordered by created_ts then path

        Example:
            >>> db.get_videos_by_date("2024-11-12", project_id=1)
            ['/videos/vid1.mp4', '/videos/vid2.mp4']
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id
                cur.execute("""
                    SELECT path
                    FROM video_metadata
                    WHERE created_date = ? AND project_id = ?
                    ORDER BY created_ts ASC, path ASC
                """, (ymd, project_id))
            else:
                # No project filter
                cur.execute("""
                    SELECT path
                    FROM video_metadata
                    WHERE created_date = ?
                    ORDER BY created_ts ASC, path ASC
                """, (ymd,))
            return [r[0] for r in cur.fetchall()]

    def get_media_by_date(self, ymd: str, project_id: int | None = None) -> list[str]:
        """
        SURGICAL FIX C: Get all media (photos + videos) for a specific day (YYYY-MM-DD).

        This combines photos and videos into a single list, ordered by timestamp.
        The grid renderer already detects video files, so no UI changes needed.

        Args:
            ymd: Date string (YYYY-MM-DD)
            project_id: Filter by project_id if provided, otherwise return all media

        Returns:
            Combined list of photo and video paths for that day, ordered by created_ts

        Example:
            >>> db.get_media_by_date("2024-11-12", project_id=1)
            ['/photos/img1.jpg', '/videos/vid1.mp4', '/photos/img2.jpg', '/videos/vid2.mp4']
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # UNION photos and videos, ordered by timestamp
                cur.execute("""
                    SELECT path, created_ts FROM photo_metadata
                    WHERE created_date = ? AND project_id = ?
                    UNION ALL
                    SELECT path, created_ts FROM video_metadata
                    WHERE created_date = ? AND project_id = ?
                    ORDER BY created_ts ASC, path ASC
                """, (ymd, project_id, ymd, project_id))
            else:
                # No project filter - get all media globally
                cur.execute("""
                    SELECT path, created_ts FROM photo_metadata
                    WHERE created_date = ?
                    UNION ALL
                    SELECT path, created_ts FROM video_metadata
                    WHERE created_date = ?
                    ORDER BY created_ts ASC, path ASC
                """, (ymd, ymd))
            return [r[0] for r in cur.fetchall()]


# === BEGIN: Quick-date helpers =============================================

    def optimize_indexes(self) -> None:
        """Create helpful indexes (no-op if they already exist)."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_date      ON photo_metadata(date_taken)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_modified  ON photo_metadata(modified)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_updated   ON photo_metadata(updated_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_meta_folder    ON photo_metadata(folder_id)")
            conn.commit()

    # -- internal: compute [start, end] iso dates for a quick key
    def _date_window_for_key(self, quick_key: str) -> tuple[str | None, str | None, str]:
        """
        Returns (start_iso, end_iso, mode)
        - mode 'meta'  -> filter by date(COALESCE(date_taken, modified))
        - mode 'updated' -> filter by updated_at (Recently Indexed)
        """
        from datetime import datetime, timedelta, timezone
        # local today (assume strings stored as local timestamps "YYYY-MM-DD HH:MM:SS")
        today = datetime.now().date()
        if quick_key == "date:today":
            start = today
            end = today
            return (start.isoformat(), end.isoformat(), "meta")
        if quick_key == "date:this-week":
            # Monday as first day of week
            start = today - timedelta(days=today.weekday())
            end = today
            return (start.isoformat(), end.isoformat(), "meta")
        if quick_key == "date:this-month":
            start = today.replace(day=1)
            end = today
            return (start.isoformat(), end.isoformat(), "meta")
        if quick_key == "date:last-30d":
            start = today - timedelta(days=29)
            end = today
            return (start.isoformat(), end.isoformat(), "meta")
        if quick_key == "date:this-year":
            start = today.replace(month=1, day=1)
            end = today
            return (start.isoformat(), end.isoformat(), "meta")
        if quick_key in ("date:recent", "date:indexed-7d"):
            # recent by UPDATED_AT (index-friendly)
            start_dt = datetime.now() - timedelta(days=7)
            return (start_dt.strftime("%Y-%m-%d %H:%M:%S"), None, "updated")
        # unsupported → no window
        return (None, None, "meta")

    def _count_between_meta_dates(self, conn, start_iso: str, end_iso: str, project_id: int | None = None) -> int:
        cur = conn.cursor()
        if project_id is not None:
            # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
            cur.execute(
                """
                SELECT COUNT(*)
                FROM photo_metadata
                WHERE project_id = ?
                  AND date(COALESCE(date_taken, modified)) BETWEEN ? AND ?
                """,
                (project_id, start_iso, end_iso)
            )
        else:
            # No project filter - count all photos globally
            cur.execute(
                """
                SELECT COUNT(*)
                FROM photo_metadata
                WHERE date(COALESCE(date_taken, modified)) BETWEEN ? AND ?
                """,
                (start_iso, end_iso)
            )
        row = cur.fetchone()
        return int(row[0] or 0)

    def _paths_between_meta_dates(self, conn, start_iso: str, end_iso: str, project_id: int | None = None) -> list[str]:
        cur = conn.cursor()
        if project_id is not None:
            # Schema v3.0.0: Filter by project_id
            cur.execute(
                """
                SELECT path
                FROM photo_metadata
                WHERE date(COALESCE(date_taken, modified)) BETWEEN ? AND ?
                  AND project_id = ?
                ORDER BY COALESCE(date_taken, modified) DESC, path
                """,
                (start_iso, end_iso, project_id)
            )
        else:
            # No project filter
            cur.execute(
                """
                SELECT path
                FROM photo_metadata
                WHERE date(COALESCE(date_taken, modified)) BETWEEN ? AND ?
                ORDER BY COALESCE(date_taken, modified) DESC, path
                """,
                (start_iso, end_iso)
            )
        return [r[0] for r in cur.fetchall()]

    def _count_recent_updated(self, conn, start_ts: str, project_id: int | None = None) -> int:
        cur = conn.cursor()
        if project_id is not None:
            # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
            cur.execute(
                """
                SELECT COUNT(*)
                FROM photo_metadata
                WHERE project_id = ?
                  AND updated_at >= ?
                """,
                (project_id, start_ts)
            )
        else:
            # No project filter - count all photos globally
            cur.execute(
                """
                SELECT COUNT(*)
                FROM photo_metadata
                WHERE updated_at >= ?
                """,
                (start_ts,)
            )
        row = cur.fetchone()
        return int(row[0] or 0)

    def _paths_recent_updated(self, conn, start_ts: str, project_id: int | None = None) -> list[str]:
        cur = conn.cursor()
        if project_id is not None:
            # Schema v3.0.0: Filter by project_id
            cur.execute(
                """
                SELECT path
                FROM photo_metadata
                WHERE updated_at >= ? AND project_id = ?
                ORDER BY updated_at DESC, path
                """,
                (start_ts, project_id)
            )
        else:
            # No project filter
            cur.execute(
                """
                SELECT path
                FROM photo_metadata
                WHERE updated_at >= ?
                ORDER BY updated_at DESC, path
                """,
                (start_ts,)
            )
        return [r[0] for r in cur.fetchall()]

    def get_quick_date_counts(self, project_id: int | None = None) -> list[dict]:
        """
        Return list of dicts: {key, label, count} for quick date branches.

        Args:
            project_id: Filter by project_id if provided, otherwise count all photos globally
        """
        QUICK = [
            ("date:today",       "Today"),
            ("date:this-week",   "This Week"),
            ("date:this-month",  "This Month"),
            ("date:last-30d",    "Last 30 Days"),
            ("date:this-year",   "This Year"),
            ("date:indexed-7d",  "Recently Indexed"),
        ]
        out = []
        with self._connect() as conn:
            for key, label in QUICK:
                start, end, mode = self._date_window_for_key(key)
                if mode == "updated":
                    cnt = self._count_recent_updated(conn, start, project_id) if start else 0
                else:
                    cnt = self._count_between_meta_dates(conn, start, end, project_id) if start and end else 0
                out.append({"key": key, "label": label, "count": cnt})
        return out

    def get_images_for_quick_key(self, key: str, project_id: int | None = None) -> list[str]:
        """
        Resolve a 'date:*' branch key (used in sidebar quick-date branches)
        to actual photo paths from photo_metadata.
        Supports date:today / date:this-week / date:this-month / date:last-30d /
        date:this-year / date:indexed-7d / date:YYYY / date:YYYY-MM-DD

        Args:
            key: Quick date key (e.g., "date:today", "date:2024", etc.)
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos.
        """
        # Normalize and detect "date:YYYY" / "date:YYYY-MM-DD"
        k = key.strip()
        if k.startswith("date:"):
            val = k[5:]
        else:
            val = k

        # direct date buckets (year/day) via created_* columns
        if len(val) == 4 and val.isdigit():
            y = int(val)
            return self.get_images_by_year(y, project_id)
        if len(val) == 10 and val[4] == "-" and val[7] == "-":
            return self.get_images_by_date(val, project_id)

        # otherwise treat as a "quick window" key
        start, end, mode = self._date_window_for_key(k)
        with self._connect() as conn:
            if mode == "updated":
                return self._paths_recent_updated(conn, start, project_id) if start else []
            if start and end:
                return self._paths_between_meta_dates(conn, start, end, project_id)
            return []

    def get_images_by_month_str(self, ym: str, project_id: int | None = None):
        """
        Accepts 'YYYY-MM' (or lenient 'YYYY-M') and normalizes internally.

        Args:
            ym: Month string in format YYYY-MM
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos.
        """
        import re
        ym = str(ym).strip()
        m = re.match(r"^(\d{4})-(\d{1,2})$", ym)
        if not m:
            return []
        y = int(m.group(1))
        mo = int(m.group(2))
        if mo < 1 or mo > 12:
            return []
        return self.get_images_by_month(y, mo, project_id)


# === END: Quick-date helpers ===============================================


    def build_date_branches(self, project_id: int):
        """
        Build branches for each created_date value in photo_metadata.
        If they already exist, skip.

        Args:
            project_id: The project ID to associate photos with

        NOTE: Uses created_date field (normalized YYYY-MM-DD format) for consistency.
        This ensures date hierarchy and date branches use the same field.
        Also populates the 'all' branch with all photos.
        """
        print(f"[build_date_branches] Using project_id={project_id}")

        with self._connect() as conn:
            cur = conn.cursor()

            # Verify project exists
            cur.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
            row = cur.fetchone()
            if not row:
                print(f"[build_date_branches] ERROR: Project {project_id} not found!")
                return 0

            # CRITICAL: First, populate the 'all' branch with ALL photos from THIS project
            # This ensures the default view shows all photos for the project
            # Schema v3.0.0: Filter by project_id
            cur.execute("SELECT path FROM photo_metadata WHERE project_id = ?", (project_id,))
            all_paths = [r[0] for r in cur.fetchall()]
            print(f"[build_date_branches] Populating 'all' branch with {len(all_paths)} photos for project {project_id}")

            # Ensure 'all' branch exists
            cur.execute(
                "INSERT OR IGNORE INTO branches (project_id, branch_key, display_name) VALUES (?,?,?)",
                (project_id, "all", "📁 All Photos"),
            )

            # Insert all photos into 'all' branch
            all_inserted = 0
            for p in all_paths:
                cur.execute(
                    "INSERT OR IGNORE INTO project_images (project_id, branch_key, image_path) VALUES (?,?,?)",
                    (project_id, "all", p),
                )
                if cur.rowcount > 0:
                    all_inserted += 1
            print(f"[build_date_branches] Inserted {all_inserted}/{len(all_paths)} photos into 'all' branch")

            # Now build date-specific branches using created_date (normalized YYYY-MM-DD format)
            # This is consistent with get_date_hierarchy() which also uses created_date
            cur.execute("""
                SELECT DISTINCT created_date
                FROM photo_metadata
                WHERE created_date IS NOT NULL
                  AND project_id = ?
                ORDER BY created_date
            """, (project_id,))
            dates = [r[0] for r in cur.fetchall()]
            print(f"[build_date_branches] Found {len(dates)} unique dates for project {project_id}")

            n_total = 0
            for d in dates:
                branch_key = f"by_date:{d}"
                branch_name = d
                # ensure branch exists
                cur.execute(
                    "INSERT OR IGNORE INTO branches (project_id, branch_key, display_name) VALUES (?,?,?)",
                    (project_id, branch_key, branch_name),
                )
                # link photos - match on created_date (Schema v3.0.0: filter by project_id)
                cur.execute(
                    "SELECT path FROM photo_metadata WHERE created_date = ? AND project_id = ?",
                    (d, project_id)
                )
                paths = [r[0] for r in cur.fetchall()]
                print(f"[build_date_branches] Date {d}: found {len(paths)} photos for project {project_id}")
                if len(paths) > 0:
                    print(f"[build_date_branches] Sample path: {paths[0]}")

                inserted = 0
                for p in paths:
                    cur.execute(
                        "INSERT OR IGNORE INTO project_images (project_id, branch_key, image_path) VALUES (?,?,?)",
                        (project_id, branch_key, p),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                # Note: inserted=0 is normal for incremental scans (photos already linked)
                status = "new" if inserted > 0 else "already linked"
                print(f"[build_date_branches] Date {d}: inserted {inserted}/{len(paths)} into project_images ({status})")
                n_total += len(paths)

            conn.commit()
            print(f"[build_date_branches] Total entries processed: {n_total}")

            # Verify what's in project_images table
            cur.execute("SELECT COUNT(*) FROM project_images WHERE project_id = ?", (project_id,))
            count = cur.fetchone()[0]
            print(f"[build_date_branches] project_images table has {count} rows for project {project_id}")
        return n_total

    def build_video_date_branches(self, project_id: int):
        """
        Build branches for each created_date value in video_metadata.
        Similar to build_date_branches() but for videos.

        Populates project_videos table with video paths organized by date.
        This enables video date hierarchy in sidebar and date-based filtering.

        Args:
            project_id: The project ID to associate videos with

        Returns:
            Number of video paths processed

        Note:
            Videos must have created_date populated (either from modified date during scan
            or from date_taken after background workers complete).
        """
        print(f"[build_video_date_branches] Using project_id={project_id}")

        with self._connect() as conn:
            cur = conn.cursor()

            # Verify project exists
            cur.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
            if not cur.fetchone():
                print(f"[build_video_date_branches] ERROR: Project {project_id} not found!")
                return 0

            # Get all videos with dates
            cur.execute("""
                SELECT path FROM video_metadata
                WHERE project_id = ? AND created_date IS NOT NULL
            """, (project_id,))
            all_video_paths = [r[0] for r in cur.fetchall()]
            print(f"[build_video_date_branches] Found {len(all_video_paths)} videos with dates for project {project_id}")

            if not all_video_paths:
                print(f"[build_video_date_branches] No videos with dates found, skipping branch creation")
                return 0

            # Ensure 'all' branch exists for videos
            cur.execute("""
                INSERT OR IGNORE INTO branches (project_id, branch_key, display_name)
                VALUES (?,?,?)
            """, (project_id, "videos:all", "🎬 All Videos"))

            # Insert all videos into 'all' branch
            all_inserted = 0
            for video_path in all_video_paths:
                cur.execute("""
                    INSERT OR IGNORE INTO project_videos (project_id, branch_key, video_path)
                    VALUES (?,?,?)
                """, (project_id, "videos:all", video_path))
                if cur.rowcount > 0:
                    all_inserted += 1
            print(f"[build_video_date_branches] Inserted {all_inserted}/{len(all_video_paths)} videos into 'all' branch")

            # Get unique dates from video_metadata
            cur.execute("""
                SELECT DISTINCT created_date
                FROM video_metadata
                WHERE project_id = ? AND created_date IS NOT NULL
                ORDER BY created_date DESC
            """, (project_id,))
            dates = [r[0] for r in cur.fetchall()]
            print(f"[build_video_date_branches] Found {len(dates)} unique video dates")

            # Create branch for each date
            n_total = 0
            for date_str in dates:
                branch_key = f"videos:by_date:{date_str}"

                # Ensure branch exists
                cur.execute("""
                    INSERT OR IGNORE INTO branches (project_id, branch_key, display_name)
                    VALUES (?,?,?)
                """, (project_id, branch_key, f"📹 {date_str}"))

                # Get videos for this date
                cur.execute("""
                    SELECT path FROM video_metadata
                    WHERE project_id = ? AND created_date = ?
                """, (project_id, date_str))
                video_paths = [r[0] for r in cur.fetchall()]

                # Insert videos into branch
                inserted = 0
                for video_path in video_paths:
                    cur.execute("""
                        INSERT OR IGNORE INTO project_videos (project_id, branch_key, video_path)
                        VALUES (?,?,?)
                    """, (project_id, branch_key, video_path))
                    if cur.rowcount > 0:
                        inserted += 1

                status = "new" if inserted > 0 else "already linked"
                print(f"[build_video_date_branches] Date {date_str}: inserted {inserted}/{len(video_paths)} ({status})")
                n_total += len(video_paths)

            conn.commit()
            print(f"[build_video_date_branches] Total entries processed: {n_total}")

            # Verify what's in project_videos table
            cur.execute("SELECT COUNT(*) FROM project_videos WHERE project_id = ?", (project_id,))
            count = cur.fetchone()[0]
            print(f"[build_video_date_branches] project_videos table has {count} rows for project {project_id}")

        return n_total


    # ===============================================
    # 📅 Phase 1: Date hierarchy + counts + loaders
    # ===============================================
    def get_date_hierarchy(self, project_id: int | None = None) -> dict:
        """
        Return nested dict {year: {month: [days...]}} from photo_metadata.created_date.
        Assumes created_date is 'YYYY-MM-DD'.

        NOTE: This is for PHOTOS ONLY. For videos, use get_video_date_hierarchy().

        Args:
            project_id: Filter by project_id if provided, otherwise use all photos globally

        Returns:
            Nested dict {year: {month: [days...]}}
        """
        from collections import defaultdict
        hier = defaultdict(lambda: defaultdict(list))
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
                # Uses compound index idx_photo_metadata_project_date for fast filtering
                cur.execute("""
                    SELECT DISTINCT created_date
                    FROM photo_metadata
                    WHERE project_id = ?
                      AND created_date IS NOT NULL
                    ORDER BY created_date ASC
                """, (project_id,))
            else:
                # No project filter - use all photos globally
                cur.execute("""
                    SELECT DISTINCT created_date
                    FROM photo_metadata
                    WHERE created_date IS NOT NULL
                    ORDER BY created_date ASC
                """)
            for (ds,) in cur.fetchall():
                try:
                    y, m, d = str(ds).split("-", 2)
                    hier[y][m].append(ds)
                except Exception:
                    pass
        return {y: dict(m) for y, m in hier.items()}

    def count_for_year(self, year: int | str, project_id: int | None = None) -> int:
        """
        Count photos for a given year.

        Args:
            year: Year to count (e.g., 2024)
            project_id: Filter by project_id if provided, otherwise count all photos globally
        """
        y = str(year)
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
                # Uses compound index idx_photo_metadata_project_date for fast filtering
                cur.execute("""
                    SELECT COUNT(*)
                    FROM photo_metadata
                    WHERE project_id = ?
                      AND created_date LIKE ? || '-%'
                """, (project_id, y))
            else:
                # No project filter - count all photos globally
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata
                    WHERE created_date LIKE ? || '-%'
                """, (y,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_for_month(self, year: int | str, month: int | str, project_id: int | None = None) -> int:
        """
        Count photos for a given year and month.

        Args:
            year: Year (e.g., 2024)
            month: Month (1-12)
            project_id: Filter by project_id if provided, otherwise count all photos globally
        """
        y = str(year)
        m = f"{int(month):02d}" if str(month).isdigit() else str(month)
        ym = f"{y}-{m}"
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
                # Uses compound index idx_photo_metadata_project_date for fast filtering
                cur.execute("""
                    SELECT COUNT(*)
                    FROM photo_metadata
                    WHERE project_id = ?
                      AND created_date LIKE ? || '-%'
                """, (project_id, ym))
            else:
                # No project filter - count all photos globally
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata
                    WHERE created_date LIKE ? || '-%'
                """, (ym,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_for_day(self, day_yyyymmdd: str, project_id: int | None = None) -> int:
        """
        Count photos for a given day.

        Args:
            day_yyyymmdd: Date in YYYY-MM-DD format
            project_id: Filter by project_id if provided, otherwise count all photos globally
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # PERFORMANCE: Use direct project_id column (schema v3.2.0+)
                # Uses compound index idx_photo_metadata_project_date for fast filtering
                cur.execute("""
                    SELECT COUNT(*)
                    FROM photo_metadata
                    WHERE project_id = ?
                      AND created_date = ?
                """, (project_id, day_yyyymmdd))
            else:
                # No project filter - count all photos globally
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata
                    WHERE created_date = ?
                """, (day_yyyymmdd,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_videos_for_day(self, day_yyyymmdd: str, project_id: int | None = None) -> int:
        """
        Count videos for a given day.

        Args:
            day_yyyymmdd: Date in YYYY-MM-DD format
            project_id: Filter by project_id if provided, otherwise count all videos globally
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM video_metadata
                    WHERE project_id = ?
                      AND created_date = ?
                """, (project_id, day_yyyymmdd))
            else:
                # No project filter - count all videos globally
                cur.execute("""
                    SELECT COUNT(*) FROM video_metadata
                    WHERE created_date = ?
                """, (day_yyyymmdd,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)


    # ===============================================
    # 🎬 VIDEO DATE HIERARCHY + COUNTS
    # ===============================================

    def get_video_date_hierarchy(self, project_id: int | None = None) -> dict:
        """
        Return nested dict {year: {month: [days...]}} from video_metadata.created_date.
        Assumes created_date is 'YYYY-MM-DD'.

        Args:
            project_id: Filter by project_id if provided, otherwise use all videos globally

        Returns:
            Nested dict {year: {month: [days...]}}
        """
        from collections import defaultdict
        hier = defaultdict(lambda: defaultdict(list))
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id (video_metadata has project_id column)
                cur.execute("""
                    SELECT DISTINCT created_date
                    FROM video_metadata
                    WHERE project_id = ?
                      AND created_date IS NOT NULL
                    ORDER BY created_date ASC
                """, (project_id,))
            else:
                # No project filter - use all videos globally
                cur.execute("""
                    SELECT DISTINCT created_date
                    FROM video_metadata
                    WHERE created_date IS NOT NULL
                    ORDER BY created_date ASC
                """)
            for (ds,) in cur.fetchall():
                try:
                    y, m, d = str(ds).split("-", 2)
                    hier[y][m].append(ds)
                except Exception:
                    pass
        return {y: dict(m) for y, m in hier.items()}

    def list_video_years_with_counts(self, project_id: int | None = None) -> list[tuple[int, int]]:
        """
        Get list of years with video counts.

        Args:
            project_id: Filter by project_id if provided, otherwise use all videos globally

        Returns:
            [(year, count)] newest first
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id
                cur.execute("""
                    SELECT created_year, COUNT(*)
                    FROM video_metadata
                    WHERE project_id = ?
                      AND created_year IS NOT NULL
                    GROUP BY created_year
                    ORDER BY created_year DESC
                """, (project_id,))
            else:
                # No project filter - count all videos globally
                cur.execute("""
                    SELECT created_year, COUNT(*)
                    FROM video_metadata
                    WHERE created_year IS NOT NULL
                    GROUP BY created_year
                    ORDER BY created_year DESC
                """)
            return cur.fetchall()

    def count_videos_for_year(self, year: int | str, project_id: int | None = None) -> int:
        """
        Count videos for a given year.

        Args:
            year: Year to count (e.g., 2024)
            project_id: Filter by project_id if provided, otherwise count all videos globally

        Returns:
            Count of videos in that year
        """
        y = str(year)
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id
                cur.execute("""
                    SELECT COUNT(*)
                    FROM video_metadata
                    WHERE project_id = ?
                      AND created_date LIKE ? || '-%'
                """, (project_id, y))
            else:
                # No project filter - count all videos globally
                cur.execute("""
                    SELECT COUNT(*) FROM video_metadata
                    WHERE created_date LIKE ? || '-%'
                """, (y,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_videos_for_month(self, year: int | str, month: int | str, project_id: int | None = None) -> int:
        """
        Count videos for a given year and month.

        Args:
            year: Year (e.g., 2024)
            month: Month (1-12)
            project_id: Filter by project_id if provided, otherwise count all videos globally

        Returns:
            Count of videos in that month
        """
        y = str(year)
        m = f"{int(month):02d}" if str(month).isdigit() else str(month)
        ym = f"{y}-{m}"
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id
                cur.execute("""
                    SELECT COUNT(*)
                    FROM video_metadata
                    WHERE project_id = ?
                      AND created_date LIKE ? || '-%'
                """, (project_id, ym))
            else:
                # No project filter - count all videos globally
                cur.execute("""
                    SELECT COUNT(*) FROM video_metadata
                    WHERE created_date LIKE ? || '-%'
                """, (ym,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_videos_for_day(self, day_yyyymmdd: str, project_id: int | None = None) -> int:
        """
        Count videos for a given day.

        Args:
            day_yyyymmdd: Date in YYYY-MM-DD format
            project_id: Filter by project_id if provided, otherwise count all videos globally

        Returns:
            Count of videos on that day
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id
                cur.execute("""
                    SELECT COUNT(*)
                    FROM video_metadata
                    WHERE project_id = ?
                      AND created_date = ?
                """, (project_id, day_yyyymmdd))
            else:
                # No project filter - count all videos globally
                cur.execute("""
                    SELECT COUNT(*) FROM video_metadata
                    WHERE created_date = ?
                """, (day_yyyymmdd,))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)


    # ===============================================
    # 📊 COMBINED MEDIA COUNTERS (Photos + Videos)
    # ===============================================
    # SURGICAL FIX B: Combined media counters for unified date hierarchy

    def count_media_for_year(self, year: int | str, project_id: int | None = None) -> int:
        """
        Count both photos and videos for a given year.

        Args:
            year: Year (e.g., 2024)
            project_id: Filter by project_id if provided, otherwise count all media globally

        Returns:
            Combined count of photos + videos for that year

        Example:
            >>> db.count_media_for_year(2024, project_id=1)
            523  # 395 photos + 128 videos
        """
        y = str(year)
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id for both tables
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM photo_metadata
                         WHERE project_id = ? AND created_date LIKE ? || '-%')
                        +
                        (SELECT COUNT(*) FROM video_metadata
                         WHERE project_id = ? AND created_date LIKE ? || '-%')
                """, (project_id, y, project_id, y))
            else:
                # No project filter - count all media globally
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM photo_metadata WHERE created_date LIKE ? || '-%')
                        +
                        (SELECT COUNT(*) FROM video_metadata WHERE created_date LIKE ? || '-%')
                """, (y, y))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_media_for_month(self, year: int | str, month: int | str, project_id: int | None = None) -> int:
        """
        Count both photos and videos for a given year and month.

        Args:
            year: Year (e.g., 2024)
            month: Month (1-12)
            project_id: Filter by project_id if provided, otherwise count all media globally

        Returns:
            Combined count of photos + videos for that month

        Example:
            >>> db.count_media_for_month(2024, 11, project_id=1)
            87  # 62 photos + 25 videos in November 2024
        """
        y = str(year)
        m = f"{int(month):02d}" if str(month).isdigit() else str(month)
        ym = f"{y}-{m}"
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id for both tables
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM photo_metadata
                         WHERE project_id = ? AND created_date LIKE ? || '-%')
                        +
                        (SELECT COUNT(*) FROM video_metadata
                         WHERE project_id = ? AND created_date LIKE ? || '-%')
                """, (project_id, ym, project_id, ym))
            else:
                # No project filter - count all media globally
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM photo_metadata WHERE created_date LIKE ? || '-%')
                        +
                        (SELECT COUNT(*) FROM video_metadata WHERE created_date LIKE ? || '-%')
                """, (ym, ym))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def count_media_for_day(self, day_yyyymmdd: str, project_id: int | None = None) -> int:
        """
        Count both photos and videos for a given day.

        Args:
            day_yyyymmdd: Date in YYYY-MM-DD format
            project_id: Filter by project_id if provided, otherwise count all media globally

        Returns:
            Combined count of photos + videos for that day

        Example:
            >>> db.count_media_for_day("2024-11-12", project_id=1)
            23  # 15 photos + 8 videos on Nov 12, 2024
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Filter by project_id for both tables
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM photo_metadata
                         WHERE project_id = ? AND created_date = ?)
                        +
                        (SELECT COUNT(*) FROM video_metadata
                         WHERE project_id = ? AND created_date = ?)
                """, (project_id, day_yyyymmdd, project_id, day_yyyymmdd))
            else:
                # No project filter - count all media globally
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM photo_metadata WHERE created_date = ?)
                        +
                        (SELECT COUNT(*) FROM video_metadata WHERE created_date = ?)
                """, (day_yyyymmdd, day_yyyymmdd))
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)


    def get_images_by_month(self, year: int | str, month: int | str, project_id: int | None = None) -> list[str]:
        """
        Return all photo paths for a given year + month (YYYY-MM).
        Auto-detects whether created_date exists, otherwise falls back to date_taken or modified.
        Works even if dates are stored with time parts (e.g. '2022-04-15 10:03:22').

        Args:
            year: Year (e.g. 2024)
            month: Month (e.g. 1 or 01)
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos.
        """
        y = str(year)
        m = f"{int(month):02d}" if str(month).isdigit() else str(month)
        prefix = f"{y}-{m}"

        with self._connect() as conn:
            cur = conn.cursor()
            # pick available column
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {r[1] for r in cur.fetchall()}
            if "created_date" in cols:
                date_col = "created_date"
            elif "date_taken" in cols:
                date_col = "date_taken"
            else:
                date_col = "modified"

            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                cur.execute(
                    f"""
                    SELECT path FROM photo_metadata
                    WHERE {date_col} LIKE ? || '%' AND project_id = ?
                    ORDER BY {date_col} ASC, path ASC
                    """,
                    (prefix, project_id)
                )
            else:
                # No project filter
                cur.execute(
                    f"""
                    SELECT path FROM photo_metadata
                    WHERE {date_col} LIKE ? || '%'
                    ORDER BY {date_col} ASC, path ASC
                    """,
                    (prefix,)
                )
            return [r[0] for r in cur.fetchall()]

    # ======================================================
    # 🏷️ New Tagging System (normalized)
    # ======================================================

    def _get_photo_id_by_path(self, path: str, project_id: int | None = None) -> int | None:
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                cur.execute("SELECT id FROM photo_metadata WHERE path = ? AND project_id = ?", (path, project_id))
            else:
                cur.execute("SELECT id FROM photo_metadata WHERE path = ?", (path,))
            row = cur.fetchone()
            return row[0] if row else None

    def add_tag(self, path: str, tag_name: str, project_id: int | None = None):
        """Assign a tag to a photo by path. Creates the tag if needed."""
        tag_name = tag_name.strip()
        if not tag_name:
            return
        photo_id = self._get_photo_id_by_path(path, project_id)
        if not photo_id:
            print(f"[ReferenceDB] ⚠️ Cannot add tag '{tag_name}': photo not found for path {path}")
            return
        with self._connect() as conn:
            cur = conn.cursor()
            
            # Ensure tag exists in tags table
            try:
                cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                conn.commit()  # Commit to make the tag visible to subsequent queries
                
                # Verify tag was created/exists
                cur.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag_name,))
                row = cur.fetchone()
                if not row:
                    print(f"[ReferenceDB] ⚠️ Failed to create/find tag '{tag_name}' - database may be locked or corrupted")
                    return
                tag_id = row[0]
                
                # Link photo to tag
                cur.execute("INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", (photo_id, tag_id))
                conn.commit()
                
                print(f"[ReferenceDB] ✓ Tagged photo {photo_id} with '{tag_name}' (tag_id={tag_id})")
            except Exception as e:
                print(f"[ReferenceDB] ⚠️ Error adding tag '{tag_name}': {e}")
                import traceback
                traceback.print_exc()
                raise

    def remove_tag(self, path: str, tag_name: str, project_id: int | None = None):
        """Remove a tag from a photo by path."""
        photo_id = self._get_photo_id_by_path(path, project_id)
        if not photo_id:
            return
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
            row = cur.fetchone()
            if row:
                tag_id = row[0]
                cur.execute("DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?", (photo_id, tag_id))
                conn.commit()

    def get_tags_for_photo(self, path: str, project_id: int | None = None) -> list[str]:
        """
        Return list of tags assigned to a specific photo path.

        DEPRECATED: Use TagService.get_tags_for_path() instead for proper architecture.
        This method is kept for backward compatibility only.

        CRITICAL FIX (2025-12-05): Added project_id filtering to prevent cross-project tag leakage.
        Previous implementation joined tags table without filtering by project_id, which could
        return tags from other projects if photo_id existed across multiple projects.

        Args:
            path: Photo file path
            project_id: Project ID for tag isolation (Schema v3.1.0)

        Returns:
            List of tag names for the photo within the specified project
        """
        photo_id = self._get_photo_id_by_path(path, project_id)
        if not photo_id:
            return []
        with self._connect() as conn:
            cur = conn.cursor()
            # CRITICAL FIX: Added project_id filtering to prevent cross-project tag leakage
            if project_id is not None:
                cur.execute("""
                    SELECT t.name
                    FROM tags t
                    JOIN photo_tags pt ON pt.tag_id = t.id
                    WHERE pt.photo_id = ? AND t.project_id = ?
                    ORDER BY t.name COLLATE NOCASE
                """, (photo_id, project_id))
            else:
                # Fallback for legacy code that doesn't pass project_id (not recommended)
                cur.execute("""
                    SELECT t.name
                    FROM tags t
                    JOIN photo_tags pt ON pt.tag_id = t.id
                    WHERE pt.photo_id = ?
                    ORDER BY t.name COLLATE NOCASE
                """, (photo_id,))
            return [r[0] for r in cur.fetchall()]

    def get_photos_by_tag(self, tag_name: str) -> list[str]:
        """Return all image paths with a given tag."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.path
                FROM photo_metadata p
                JOIN photo_tags pt ON pt.photo_id = p.id
                JOIN tags t        ON t.id = pt.tag_id
                WHERE t.name = ?
                ORDER BY p.path
            """, (tag_name,))
            return [r[0] for r in cur.fetchall()]

    def get_all_tags_priorperProject(self, project_id: int | None = None) -> list[str]:
        """
        Return list of all existing tag names sorted alphabetically.
        project_id is accepted for compatibility with callers (currently unused).
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE")
            rows = [r[0] for r in cur.fetchall()]
        return rows

    def get_all_tags(self, project_id: int | None = None) -> list[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is None:
                cur.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE")
            else:
                cur.execute("""
                    SELECT DISTINCT t.name
                    FROM tags t
                    JOIN photo_tags pt ON pt.tag_id = t.id
                    JOIN photo_metadata p ON p.id = pt.photo_id
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE f.id IN (
                        SELECT id FROM photo_folders WHERE path LIKE (
                            SELECT folder || '%' FROM projects WHERE id = ?
                        )
                    )
                    ORDER BY t.name COLLATE NOCASE
                """, (project_id,))
            return [r[0] for r in cur.fetchall()]


    def delete_tag(self, tag_name: str):
        """Completely remove a tag and all its assignments."""
        with self._connect() as conn:
            conn.execute("DELETE FROM tags WHERE name = ?", (tag_name,))
            conn.commit()

    def get_all_tags_with_counts(self) -> list[tuple[str, int]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.name, COUNT(pt.photo_id)
                FROM tags t
                LEFT JOIN photo_tags pt ON pt.tag_id = t.id
                GROUP BY t.id
                ORDER BY t.name COLLATE NOCASE
            """)
            return cur.fetchall()

    def ensure_tag(self, tag_name: str) -> int | None:
        """Ensure tag exists, return its ID."""
        tag_name = tag_name.strip()
        if not tag_name:
            return None
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
            conn.commit()  # Commit to make the tag visible
            cur.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
            row = cur.fetchone()
            return row[0] if row else None

    def rename_tag(self, old_name: str, new_name: str):
        """
        Rename a tag. If new_name already exists, merge old into new.
        """
        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name or old_name.lower() == new_name.lower():
            return

        with self._connect() as conn:
            cur = conn.cursor()
            # ensure new tag exists
            cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (new_name,))
            conn.commit()  # Commit to make the tag visible
            cur.execute("SELECT id FROM tags WHERE name = ?", (new_name,))
            row = cur.fetchone()
            if not row:
                print(f"[ReferenceDB] ⚠️ Failed to create/find new tag '{new_name}'")
                return
            new_id = row[0]

            # get old tag id
            cur.execute("SELECT id FROM tags WHERE name = ?", (old_name,))
            row = cur.fetchone()
            if not row:
                return
            old_id = row[0]

            # reassign photo_tags to new_id
            cur.execute("""
                INSERT OR IGNORE INTO photo_tags (photo_id, tag_id)
                SELECT photo_id, ? FROM photo_tags WHERE tag_id = ?
            """, (new_id, old_id))

            # delete old tag
            cur.execute("DELETE FROM tags WHERE id = ?", (old_id,))
            conn.commit()


    # --- GPS & Location methods ---
    
    def update_photo_gps(self, path: str, latitude: float | None, longitude: float | None, location_name: str | None = None):
        """
        Store or update GPS coordinates for a photo.

        This method is used for:
        1. Automatic GPS extraction from EXIF during photo scanning
        2. Manual location editing by user via Location Editor dialog

        Args:
            path: Photo file path
            latitude: GPS latitude (-90 to 90) or None to clear
            longitude: GPS longitude (-180 to 180) or None to clear
            location_name: Optional location name or None

        Note:
            Passing None for both lat/lon will clear the location data.
        """
        import logging
        import os
        import platform
        logger = logging.getLogger(__name__)

        # CRITICAL FIX: Normalize path before updating (database stores normalized paths)
        # This ensures manual GPS edits actually update the correct row
        normalized_path = os.path.normpath(path).replace('\\', '/')
        if platform.system() == 'Windows':
            normalized_path = normalized_path.lower()

        with self._connect() as conn:
            cur = conn.cursor()
            # Add GPS columns if they don't exist yet
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            if 'gps_latitude' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_latitude REAL")
            if 'gps_longitude' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_longitude REAL")
            if 'location_name' not in existing_cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN location_name TEXT")

            # Update the photo record (using normalized path)
            cur.execute("""
                UPDATE photo_metadata
                SET gps_latitude = ?, gps_longitude = ?, location_name = ?
                WHERE path = ?
            """, (latitude, longitude, location_name, normalized_path))

            rows_updated = cur.rowcount
            conn.commit()

            if rows_updated == 0:
                logger.warning(f"[ReferenceDB] No photo found with path: {normalized_path} (original: {path})")
            elif latitude is not None and longitude is not None:
                logger.info(f"[ReferenceDB] Updated GPS for {os.path.basename(path)}: ({latitude:.6f}, {longitude:.6f}) - {location_name}")
            else:
                logger.info(f"[ReferenceDB] Cleared GPS for {os.path.basename(path)}")
    
    def get_photos_by_location(self, project_id: int | None = None, radius_km: float = 5.0) -> dict[str, list[dict]]:
        """Group photos by location proximity.
        
        Returns:
            dict mapping location_key to list of photo dicts with path, lat, lon, location_name
        """
        with self._connect() as conn:
            cur = conn.cursor()
            # First check if GPS columns exist
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            if 'gps_latitude' not in existing_cols or 'gps_longitude' not in existing_cols:
                return {}
            
            # Query all photos with GPS data
            if project_id:
                cur.execute("""
                    SELECT p.path, p.gps_latitude, p.gps_longitude, p.location_name, f.path as folder_path
                    FROM photo_metadata p
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE p.gps_latitude IS NOT NULL 
                      AND p.gps_longitude IS NOT NULL
                      AND f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
                    ORDER BY p.gps_latitude, p.gps_longitude
                """, (project_id,))
            else:
                cur.execute("""
                    SELECT p.path, p.gps_latitude, p.gps_longitude, p.location_name, f.path as folder_path
                    FROM photo_metadata p
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE p.gps_latitude IS NOT NULL AND p.gps_longitude IS NOT NULL
                    ORDER BY p.gps_latitude, p.gps_longitude
                """)
            
            rows = cur.fetchall()
            
            # Group by proximity using simple clustering
            locations = {}
            for row in rows:
                photo = {
                    'path': row[0],
                    'lat': row[1],
                    'lon': row[2],
                    'location_name': row[3] or 'Unknown Location'
                }
                
                # Find nearest existing cluster
                found_cluster = None
                for loc_key, photos in locations.items():
                    first = photos[0]
                    dist = self._haversine_distance(
                        photo['lat'], photo['lon'],
                        first['lat'], first['lon']
                    )
                    if dist <= radius_km:
                        found_cluster = loc_key
                        break
                
                if found_cluster:
                    locations[found_cluster].append(photo)
                else:
                    # Create new cluster
                    loc_name = photo['location_name']
                    loc_key = f"{loc_name}_{len(locations)}"
                    locations[loc_key] = [photo]
            
            return locations
    
    def _haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS coordinates in kilometers."""
        import math
        
        # Earth radius in kilometers
        R = 6371.0
        
        # Convert to radians
        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)
        
        # Haversine formula
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    def get_location_clusters(self, project_id: int | None = None) -> list[dict]:
        """Get location cluster summaries for sidebar display.

        Returns:
            list of {name, count, lat, lon, paths}
        """
        import logging
        logger = logging.getLogger(__name__)

        from settings_manager_qt import SettingsManager
        sm = SettingsManager()
        radius_km = float(sm.get("gps_clustering_radius_km", 5.0))

        # DIAGNOSTIC: Check if GPS columns exist and have data
        with self._connect() as conn:
            cur = conn.cursor()

            # Check for GPS columns
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            has_gps_cols = 'gps_latitude' in existing_cols and 'gps_longitude' in existing_cols

            logger.info(f"[get_location_clusters] GPS columns exist: {has_gps_cols}")

            if has_gps_cols:
                # Count photos with GPS data
                if project_id:
                    cur.execute("""
                        SELECT COUNT(*) FROM photo_metadata p
                        JOIN photo_folders f ON f.id = p.folder_id
                        WHERE p.gps_latitude IS NOT NULL
                          AND p.gps_longitude IS NOT NULL
                          AND f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
                    """, (project_id,))
                else:
                    cur.execute("""
                        SELECT COUNT(*) FROM photo_metadata
                        WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL
                    """)

                gps_photo_count = cur.fetchone()[0]
                logger.info(f"[get_location_clusters] Found {gps_photo_count} photos with GPS data (project_id={project_id})")

                if gps_photo_count == 0:
                    # Check total photos in project
                    if project_id:
                        cur.execute("""
                            SELECT COUNT(*) FROM photo_metadata p
                            JOIN photo_folders f ON f.id = p.folder_id
                            WHERE f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
                        """, (project_id,))
                        total_photos = cur.fetchone()[0]
                        logger.warning(f"[get_location_clusters] No GPS data found. Total photos in project: {total_photos}")
                    else:
                        cur.execute("SELECT COUNT(*) FROM photo_metadata")
                        total_photos = cur.fetchone()[0]
                        logger.warning(f"[get_location_clusters] No GPS data found. Total photos in DB: {total_photos}")

        locations = self.get_photos_by_location(project_id, radius_km=radius_km)
        logger.info(f"[get_location_clusters] get_photos_by_location returned {len(locations)} location groups")

        clusters = []

        for loc_key, photos in locations.items():
            # Calculate center point (average)
            avg_lat = sum(p['lat'] for p in photos) / len(photos)
            avg_lon = sum(p['lon'] for p in photos) / len(photos)

            # Use most common location name
            name = photos[0]['location_name']

            clusters.append({
                'name': name,
                'count': len(photos),
                'lat': avg_lat,
                'lon': avg_lon,
                'paths': [p['path'] for p in photos]
            })

        # Sort by photo count (descending)
        clusters.sort(key=lambda x: x['count'], reverse=True)
        logger.info(f"[get_location_clusters] Returning {len(clusters)} location clusters")
        return clusters

    def create_location_branch(self, project_id: int, lat: float, lon: float, location_name: str = None) -> str:
        """Create or update a location branch and link photos to it.

        Args:
            project_id: Project ID
            lat: Latitude of location center
            lon: Longitude of location center
            location_name: Optional location name

        Returns:
            branch_key: The created/updated branch key (e.g., "location_37.7749_-122.4194")
        """
        from settings_manager_qt import SettingsManager
        sm = SettingsManager()
        radius_km = float(sm.get("gps_clustering_radius_km", 5.0))

        # Create branch key from coordinates
        branch_key = f"location_{lat:.4f}_{lon:.4f}"

        with self._connect() as conn:
            cur = conn.cursor()

            # Check if GPS columns exist
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            if 'gps_latitude' not in existing_cols or 'gps_longitude' not in existing_cols:
                return branch_key

            # Find all photos within radius of this location
            cur.execute("""
                SELECT p.path, p.gps_latitude, p.gps_longitude
                FROM photo_metadata p
                JOIN photo_folders f ON f.id = p.folder_id
                WHERE p.gps_latitude IS NOT NULL
                  AND p.gps_longitude IS NOT NULL
                  AND f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
            """, (project_id,))

            rows = cur.fetchall()

            # Filter photos by distance
            location_photos = []
            for row in rows:
                photo_path = row[0]
                photo_lat = row[1]
                photo_lon = row[2]

                dist = self._haversine_distance(lat, lon, photo_lat, photo_lon)
                if dist <= radius_km:
                    location_photos.append(photo_path)

            # Link photos to branch (create branch entries in project_images)
            if location_photos:
                # Clear existing photos for this branch
                cur.execute("""
                    DELETE FROM project_images
                    WHERE project_id = ? AND branch_key = ?
                """, (project_id, branch_key))

                # Insert photos for this location
                photo_data = [(project_id, branch_key, photo_path)
                             for photo_path in location_photos]
                cur.executemany("""
                    INSERT INTO project_images (project_id, branch_key, image_path)
                    VALUES (?, ?, ?)
                """, photo_data)

                conn.commit()

            return branch_key

    def cache_location_name(self, latitude: float, longitude: float, location_name: str):
        """Cache a reverse-geocoded location name to reduce API calls."""
        with self._connect() as conn:
            cur = conn.cursor()
            # Create cache table if it doesn't exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gps_location_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    location_name TEXT NOT NULL,
                    cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(latitude, longitude)
                )
            """)
            # Insert or update
            cur.execute("""
                INSERT OR REPLACE INTO gps_location_cache (latitude, longitude, location_name)
                VALUES (?, ?, ?)
            """, (latitude, longitude, location_name))
            conn.commit()
    
    def get_cached_location_name(self, latitude: float, longitude: float, tolerance: float = 0.001) -> str | None:
        """Get cached location name for coordinates (within tolerance)."""
        with self._connect() as conn:
            cur = conn.cursor()
            # Check if cache table exists
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='gps_location_cache'
            """)
            if not cur.fetchone():
                return None

            # Find nearest cached location within tolerance
            cur.execute("""
                SELECT location_name FROM gps_location_cache
                WHERE ABS(latitude - ?) < ? AND ABS(longitude - ?) < ?
                ORDER BY (ABS(latitude - ?) + ABS(longitude - ?))
                LIMIT 1
            """, (latitude, tolerance, longitude, tolerance, latitude, longitude))
            row = cur.fetchone()
            return row[0] if row else None

    def geocode_photos_missing_location_names(self, project_id: int | None = None,
                                             max_requests: int = 100,
                                             progress_callback=None) -> dict:
        """
        Automatically geocode photos that have GPS data but no location name.

        Args:
            project_id: Optional project ID to limit scope
            max_requests: Maximum API requests to make (default: 100)
            progress_callback: Optional callback(current, total, photo_path, location_name)

        Returns:
            dict with 'processed', 'geocoded', 'cached', 'failed' counts
        """
        import logging
        logger = logging.getLogger(__name__)

        logger.info(f"[ReferenceDB] Starting geocoding for project {project_id}...")

        with self._connect() as conn:
            cur = conn.cursor()

            # Check if GPS columns exist
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            if 'gps_latitude' not in existing_cols or 'gps_longitude' not in existing_cols:
                logger.warning("[ReferenceDB] GPS columns not found in photo_metadata")
                return {'processed': 0, 'geocoded': 0, 'cached': 0, 'failed': 0}

            # Find photos with GPS but no location name
            if project_id:
                cur.execute("""
                    SELECT p.path, p.gps_latitude, p.gps_longitude
                    FROM photo_metadata p
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE p.gps_latitude IS NOT NULL
                      AND p.gps_longitude IS NOT NULL
                      AND (p.location_name IS NULL OR p.location_name = '' OR p.location_name = 'Unknown Location')
                      AND f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
                    LIMIT ?
                """, (project_id, max_requests))
            else:
                cur.execute("""
                    SELECT path, gps_latitude, gps_longitude
                    FROM photo_metadata
                    WHERE gps_latitude IS NOT NULL
                      AND gps_longitude IS NOT NULL
                      AND (location_name IS NULL OR location_name = '' OR location_name = 'Unknown Location')
                    LIMIT ?
                """, (max_requests,))

            photos = cur.fetchall()

        if not photos:
            logger.info("[ReferenceDB] No photos need geocoding")
            return {'processed': 0, 'geocoded': 0, 'cached': 0, 'failed': 0}

        logger.info(f"[ReferenceDB] Found {len(photos)} photos to geocode")

        # Import geocoding service
        try:
            from services.geocoding_service import get_geocoding_service
            geocoding_service = get_geocoding_service()
        except ImportError as e:
            logger.error(f"[ReferenceDB] Failed to import geocoding service: {e}")
            return {'processed': 0, 'geocoded': 0, 'cached': 0, 'failed': 0}

        # Process photos
        stats = {'processed': 0, 'geocoded': 0, 'cached': 0, 'failed': 0}

        for i, (photo_path, lat, lon) in enumerate(photos, 1):
            try:
                # Check cache first
                cached_name = self.get_cached_location_name(lat, lon, tolerance=0.01)

                if cached_name:
                    location_name = cached_name
                    stats['cached'] += 1
                    logger.debug(f"[ReferenceDB] Cache hit: {photo_path} → {location_name}")
                else:
                    # Make API request
                    location_name = geocoding_service.reverse_geocode(lat, lon)
                    if location_name:
                        stats['geocoded'] += 1
                        logger.info(f"[ReferenceDB] Geocoded: {photo_path} → {location_name}")
                    else:
                        location_name = "Unknown Location"
                        stats['failed'] += 1
                        logger.warning(f"[ReferenceDB] Geocoding failed: {photo_path}")

                # Update photo metadata
                self.update_photo_gps(photo_path, lat, lon, location_name)
                stats['processed'] += 1

                # Progress callback
                if progress_callback:
                    progress_callback(i, len(photos), photo_path, location_name)

            except Exception as e:
                logger.error(f"[ReferenceDB] Error geocoding {photo_path}: {e}")
                stats['failed'] += 1

        logger.info(f"[ReferenceDB] Geocoding complete: {stats}")
        return stats

    def batch_geocode_unique_coordinates(self, project_id: int | None = None,
                                        max_locations: int = 50,
                                        progress_callback=None) -> dict:
        """
        Geocode unique GPS coordinates efficiently (batch mode).

        This method finds all unique GPS coordinates in the project,
        geocodes them once, then updates all photos with those coordinates.
        Much more efficient than geocoding each photo individually.

        Args:
            project_id: Optional project ID to limit scope
            max_locations: Maximum unique locations to geocode (default: 50)
            progress_callback: Optional callback(current, total, location)

        Returns:
            dict with 'locations_geocoded', 'photos_updated', 'cached', 'failed' counts
        """
        import logging
        logger = logging.getLogger(__name__)

        logger.info(f"[ReferenceDB] Starting batch geocoding for project {project_id}...")

        with self._connect() as conn:
            cur = conn.cursor()

            # Check if GPS columns exist
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            if 'gps_latitude' not in existing_cols or 'gps_longitude' not in existing_cols:
                logger.warning("[ReferenceDB] GPS columns not found in photo_metadata")
                return {'locations_geocoded': 0, 'photos_updated': 0, 'cached': 0, 'failed': 0}

            # Find unique GPS coordinates that need geocoding
            if project_id:
                cur.execute("""
                    SELECT DISTINCT p.gps_latitude, p.gps_longitude, COUNT(*) as photo_count
                    FROM photo_metadata p
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE p.gps_latitude IS NOT NULL
                      AND p.gps_longitude IS NOT NULL
                      AND (p.location_name IS NULL OR p.location_name = '' OR p.location_name = 'Unknown Location')
                      AND f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
                    GROUP BY p.gps_latitude, p.gps_longitude
                    ORDER BY photo_count DESC
                    LIMIT ?
                """, (project_id, max_locations))
            else:
                cur.execute("""
                    SELECT DISTINCT gps_latitude, gps_longitude, COUNT(*) as photo_count
                    FROM photo_metadata
                    WHERE gps_latitude IS NOT NULL
                      AND gps_longitude IS NOT NULL
                      AND (location_name IS NULL OR location_name = '' OR location_name = 'Unknown Location')
                    GROUP BY gps_latitude, gps_longitude
                    ORDER BY photo_count DESC
                    LIMIT ?
                """, (max_locations,))

            unique_coords = cur.fetchall()

        if not unique_coords:
            logger.info("[ReferenceDB] No unique coordinates need geocoding")
            return {'locations_geocoded': 0, 'photos_updated': 0, 'cached': 0, 'failed': 0}

        logger.info(f"[ReferenceDB] Found {len(unique_coords)} unique locations affecting "
                   f"{sum(c[2] for c in unique_coords)} photos")

        # Import geocoding service
        try:
            from services.geocoding_service import get_geocoding_service
            geocoding_service = get_geocoding_service()
        except ImportError as e:
            logger.error(f"[ReferenceDB] Failed to import geocoding service: {e}")
            return {'locations_geocoded': 0, 'photos_updated': 0, 'cached': 0, 'failed': 0}

        # Process unique coordinates
        stats = {'locations_geocoded': 0, 'photos_updated': 0, 'cached': 0, 'failed': 0}

        for i, (lat, lon, photo_count) in enumerate(unique_coords, 1):
            try:
                # Check cache first
                cached_name = self.get_cached_location_name(lat, lon, tolerance=0.01)

                if cached_name:
                    location_name = cached_name
                    stats['cached'] += 1
                    logger.debug(f"[ReferenceDB] Cache hit: ({lat:.4f}, {lon:.4f}) → {location_name}")
                else:
                    # Make API request
                    location_name = geocoding_service.reverse_geocode(lat, lon)
                    if location_name:
                        stats['locations_geocoded'] += 1
                        logger.info(f"[ReferenceDB] Geocoded: ({lat:.4f}, {lon:.4f}) → {location_name} ({photo_count} photos)")
                    else:
                        location_name = "Unknown Location"
                        stats['failed'] += 1
                        logger.warning(f"[ReferenceDB] Geocoding failed: ({lat:.4f}, {lon:.4f})")

                # Update all photos with these coordinates
                with self._connect() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE photo_metadata
                        SET location_name = ?
                        WHERE gps_latitude = ? AND gps_longitude = ?
                    """, (location_name, lat, lon))
                    updated_count = cur.rowcount
                    conn.commit()

                stats['photos_updated'] += updated_count
                logger.info(f"[ReferenceDB] Updated {updated_count} photos with location: {location_name}")

                # Progress callback
                if progress_callback:
                    progress_callback(i, len(unique_coords), (lat, lon, location_name))

            except Exception as e:
                logger.error(f"[ReferenceDB] Error geocoding ({lat:.4f}, {lon:.4f}): {e}")
                stats['failed'] += 1

        logger.info(f"[ReferenceDB] Batch geocoding complete: {stats}")
        return stats

    # --- End GPS & Location methods ---

    def get_tags_for_paths(self, paths: list[str], project_id: int | None = None) -> dict[str, list[str]]:
        if not paths:
            return {}
        import os
        def norm(p: str) -> str:
            try:
                # CRITICAL FIX: Convert backslashes to forward slashes to match DB storage
                normalized = os.path.normcase(os.path.abspath(os.path.normpath(p.strip())))
                return normalized.replace('\\', '/')
            except Exception:
                return str(p).strip().lower()

        # Map normalized->original so we can return tags keyed by original path
        orig_paths = [str(p) for p in paths]
        nmap = {norm(p): p for p in orig_paths}
        npaths = list(nmap.keys())
        
        # 🐛 DEBUG: Print first 3 paths to see normalization
        if len(npaths) > 0:
            print(f"[DB_TAGS_DEBUG] Sample normalized paths being queried:")
            for i, (np_key, orig) in enumerate(list(nmap.items())[:3]):
                print(f"  [{i}] norm='{np_key}'")
                print(f"      orig='{orig}'")

        out: dict[str, list[str]] = {p: [] for p in orig_paths}
        CHUNK = 400  # keep well below 999
        
        # 🐛 DEBUG: Track tag retrieval for debugging badge persistence
        tags_found = 0
        paths_with_tags = 0
        
        with self._connect() as conn:
            cur = conn.cursor()
            for i in range(0, len(npaths), CHUNK):
                chunk = npaths[i:i+CHUNK]
                if project_id is not None:
                    # 🐞 FIX: Query BOTH photo_metadata AND video_metadata tables for tags
                    # Photos query
                    q_photos = f"""
                        SELECT pm.path, t.name
                        FROM photo_metadata pm
                        JOIN photo_tags pt ON pt.photo_id = pm.id
                        JOIN tags t       ON t.id = pt.tag_id
                        WHERE pm.path IN ({','.join(['?']*len(chunk))})
                          AND pm.project_id = ?
                    """
                    # Videos query
                    q_videos = f"""
                        SELECT vm.path, t.name
                        FROM video_metadata vm
                        JOIN video_tags vt ON vt.video_id = vm.id
                        JOIN tags t        ON t.id = vt.tag_id
                        WHERE vm.path IN ({','.join(['?']*len(chunk))})
                          AND vm.project_id = ?
                    """
                    # 🐛 DEBUG: Show the actual SQL and first few params
                    if i == 0:
                        print(f"[DB_TAGS_SQL] Photos query: {q_photos[:150]}...")
                        print(f"[DB_TAGS_SQL] Videos query: {q_videos[:150]}...")
                        print(f"[DB_TAGS_SQL] First 3 params: {chunk[:3]}")
                        print(f"[DB_TAGS_SQL] project_id: {project_id}")
                    
                    # Execute both queries and combine results
                    cur.execute(q_photos, chunk + [project_id])
                    rows = cur.fetchall()
                    
                    cur.execute(q_videos, chunk + [project_id])
                    video_rows = cur.fetchall()
                    rows.extend(video_rows)
                    
                    # 🐛 DEBUG: Show ALL rows returned from query
                    if i == 0:
                        photos_count = len(rows) - len(video_rows)
                        print(f"[DB_TAGS_SQL] Query returned {len(rows)} rows ({photos_count} photos + {len(video_rows)} videos)")
                        if len(rows) > 0:
                            print(f"[DB_TAGS_SQL] First row: path='{rows[0][0]}', tag='{rows[0][1]}'")
                        else:
                            print(f"[DB_TAGS_SQL] No rows returned! Checking if ANY tags exist in DB...")
                            # Check what paths exist in photo_metadata with tags
                            test_cur = conn.cursor()
                            test_cur.execute("""
                                SELECT pm.path, t.name 
                                FROM photo_metadata pm
                                JOIN photo_tags pt ON pt.photo_id = pm.id
                                JOIN tags t ON t.id = pt.tag_id
                                WHERE pm.project_id = ?
                                LIMIT 3
                            """, [project_id])
                            test_rows = test_cur.fetchall()
                            print(f"[DB_TAGS_SQL] Sample tagged PHOTOS in DB:")
                            for tr in test_rows:
                                print(f"  DB path: '{tr[0]}' tag: '{tr[1]}'")
                            # Check videos too
                            test_cur.execute("""
                                SELECT vm.path, t.name 
                                FROM video_metadata vm
                                JOIN video_tags vt ON vt.video_id = vm.id
                                JOIN tags t ON t.id = vt.tag_id
                                WHERE vm.project_id = ?
                                LIMIT 3
                            """, [project_id])
                            test_video_rows = test_cur.fetchall()
                            print(f"[DB_TAGS_SQL] Sample tagged VIDEOS in DB:")
                            for tr in test_video_rows:
                                print(f"  DB path: '{tr[0]}' tag: '{tr[1]}'")
                else:
                    # 🐞 FIX: Query BOTH photo_metadata AND video_metadata tables for tags (no project filter)
                    q_photos = f"""
                        SELECT pm.path, t.name
                        FROM photo_metadata pm
                        JOIN photo_tags pt ON pt.photo_id = pm.id
                        JOIN tags t       ON t.id = pt.tag_id
                        WHERE pm.path IN ({','.join(['?']*len(chunk))})
                    """
                    q_videos = f"""
                        SELECT vm.path, t.name
                        FROM video_metadata vm
                        JOIN video_tags vt ON vt.video_id = vm.id
                        JOIN tags t        ON t.id = vt.tag_id
                        WHERE vm.path IN ({','.join(['?']*len(chunk))})
                    """
                    cur.execute(q_photos, chunk)
                    rows = cur.fetchall()
                    
                    cur.execute(q_videos, chunk)
                    video_rows = cur.fetchall()
                    rows.extend(video_rows)
                for row in rows:
                    npath, tagname = row[0], row[1]
                    tags_found += 1
                    
                    # 🐛 DEBUG: Print first few DB results to see stored paths
                    if tags_found <= 3:
                        print(f"[DB_TAGS_DEBUG] DB returned: path='{npath}', tag='{tagname}'")
                    
                    original = nmap.get(norm(npath))
                    if original:
                        out.setdefault(original, []).append(tagname)
                        if tagname == 'favorite':  # Debug favorite tags specifically
                            print(f"[DB_TAGS] Found favorite tag: {original}")
                    else:
                        # 🚨 PATH MISMATCH! DB path doesn't match any queried path
                        if tags_found <= 5:
                            print(f"[DB_TAGS_WARNING] Path mismatch! DB path '{npath}' not in query map")
                            print(f"  Normalized DB path: '{norm(npath)}'")
                            print(f"  Available keys: {list(nmap.keys())[:2]}...")
        
        paths_with_tags = sum(1 for v in out.values() if v)
        print(f"[DB_TAGS] Retrieved {tags_found} tag assignments across {paths_with_tags}/{len(orig_paths)} paths (project_id={project_id})")
        return out
    # <<< FIX 1

    def get_aspect_ratios_for_paths(self, paths: list[str], project_id: int | None = None) -> dict[str, float]:
        """
        Get aspect ratios (width/height) for multiple paths from database.

        FIX (2026-02-08): Prevents UI-thread blocking by using DB-stored dimensions
        instead of PIL.Image.open() calls. This follows Google Photos/iOS Photos
        best practice of never opening image files on the UI thread.

        Args:
            paths: List of image file paths
            project_id: Optional project ID filter

        Returns:
            Dict mapping path -> aspect_ratio (defaults to 1.5 if not found)
        """
        if not paths:
            return {}
        import os

        def norm(p: str) -> str:
            try:
                normalized = os.path.normcase(os.path.abspath(os.path.normpath(p.strip())))
                return normalized.replace('\\', '/')
            except Exception:
                return str(p).strip().lower()

        orig_paths = [str(p) for p in paths]
        nmap = {norm(p): p for p in orig_paths}
        npaths = list(nmap.keys())

        # Default aspect ratio for missing metadata
        DEFAULT_ASPECT = 1.5
        out: dict[str, float] = {p: DEFAULT_ASPECT for p in orig_paths}

        CHUNK = 400
        with self._connect() as conn:
            cur = conn.cursor()
            for i in range(0, len(npaths), CHUNK):
                chunk = npaths[i:i+CHUNK]

                if project_id is not None:
                    # Query with project filter for photos
                    q_photos = f"""
                        SELECT path, width, height FROM photo_metadata
                        WHERE path IN ({','.join(['?']*len(chunk))})
                          AND project_id = ?
                          AND width IS NOT NULL AND height IS NOT NULL
                          AND height > 0
                    """
                    cur.execute(q_photos, chunk + [project_id])
                else:
                    q_photos = f"""
                        SELECT path, width, height FROM photo_metadata
                        WHERE path IN ({','.join(['?']*len(chunk))})
                          AND width IS NOT NULL AND height IS NOT NULL
                          AND height > 0
                    """
                    cur.execute(q_photos, chunk)

                rows = cur.fetchall()
                for row in rows:
                    db_path, w, h = row[0], row[1], row[2]
                    original = nmap.get(norm(db_path))
                    if original and w and h and h > 0:
                        out[original] = float(w) / float(h)

        return out

    def get_image_paths_for_tag(self, tag_name: str, project_id: int | None = None) -> list[str]:
        """
        Return a list of image file paths for the given tag name using photo_tags table.

        Args:
            tag_name: Name of the tag to filter by
            project_id: Filter by project_id (Schema v3.0.0). If None, returns all photos.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Schema v3.0.0: Filter by project_id
                rows = cur.execute("""
                    SELECT DISTINCT p.path
                    FROM photo_metadata AS p
                    JOIN photo_tags AS pt ON p.id = pt.photo_id
                    JOIN tags AS tg ON tg.id = pt.tag_id
                    WHERE tg.name = ? AND p.project_id = ?
                """, (tag_name, project_id)).fetchall()
            else:
                # No project filter
                rows = cur.execute("""
                    SELECT DISTINCT p.path
                    FROM photo_metadata AS p
                    JOIN photo_tags AS pt ON p.id = pt.photo_id
                    JOIN tags AS tg ON tg.id = pt.tag_id
                    WHERE tg.name = ?
                """, (tag_name,)).fetchall()
            return [os.path.abspath(r[0]) for r in rows if r and r[0]]

    def get_images_by_branch_and_tag(self, project_id: int, branch_key: str, tag_name: str) -> list[str]:
        """
        Get image paths that match BOTH a branch AND a tag in a single efficient query.

        This method fixes the UI freeze issue caused by loading all branch photos (2856)
        then filtering in memory. Instead, it uses SQL JOIN to get only matching photos.

        Args:
            project_id: Project ID to filter by
            branch_key: Branch key (e.g., 'all', 'date:2024-01-15')
            tag_name: Tag name to filter by

        Returns:
            List of image paths that are in the branch AND have the tag

        Example:
            # Get photos in 'all' branch with tag 'Himmel' (returns 2 photos, not 2856!)
            paths = db.get_images_by_branch_and_tag(1, 'all', 'Himmel')
            # Result: 2 photos instead of loading 2856 and filtering in memory

        Performance:
            - OLD: Load 2856 photos → filter in memory → UI freezes for minutes
            - NEW: SQL JOIN returns 2 photos → UI responds instantly
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # Efficient query: JOIN branch + tag in single pass
            # Only returns photos that match BOTH conditions
            rows = cur.execute("""
                SELECT DISTINCT pm.path
                FROM photo_metadata pm
                JOIN project_images pi ON pm.path = pi.image_path AND pm.project_id = pi.project_id
                JOIN photo_tags pt ON pm.id = pt.photo_id
                JOIN tags t ON pt.tag_id = t.id
                WHERE pm.project_id = ?
                  AND pi.branch_key = ?
                  AND t.name = ?
                  AND t.project_id = ?
                ORDER BY pm.path
            """, (project_id, branch_key, tag_name, project_id)).fetchall()

            paths = [os.path.abspath(r[0]) for r in rows if r and r[0]]

            self.logger.debug(
                f"get_images_by_branch_and_tag(project={project_id}, branch={branch_key}, tag={tag_name}) "
                f"→ {len(paths)} photos (efficient JOIN query)"
            )

            return paths

    def get_images_by_folder_and_tag(self, project_id: int, folder_id: int, tag_name: str, include_subfolders: bool = True) -> list[str]:
        """
        Get image paths in a folder (optionally including subfolders) that have a specific tag.

        Efficient query using SQL JOIN instead of loading all folder photos then filtering.

        Args:
            project_id: Project ID to filter by
            folder_id: Folder ID
            tag_name: Tag name to filter by
            include_subfolders: If True, include photos from nested subfolders

        Returns:
            List of image paths that are in the folder AND have the tag
        """
        with self._connect() as conn:
            cur = conn.cursor()

            if include_subfolders:
                # Get all descendant folder IDs
                folder_ids = self.get_descendant_folder_ids(folder_id, project_id=project_id)
                placeholders = ','.join('?' * len(folder_ids))

                rows = cur.execute(f"""
                    SELECT DISTINCT pm.path
                    FROM photo_metadata pm
                    JOIN photo_tags pt ON pm.id = pt.photo_id
                    JOIN tags t ON pt.tag_id = t.id
                    WHERE pm.project_id = ?
                      AND pm.folder_id IN ({placeholders})
                      AND t.name = ?
                      AND t.project_id = ?
                    ORDER BY pm.path
                """, [project_id] + folder_ids + [tag_name, project_id]).fetchall()
            else:
                rows = cur.execute("""
                    SELECT DISTINCT pm.path
                    FROM photo_metadata pm
                    JOIN photo_tags pt ON pm.id = pt.photo_id
                    JOIN tags t ON pt.tag_id = t.id
                    WHERE pm.project_id = ?
                      AND pm.folder_id = ?
                      AND t.name = ?
                      AND t.project_id = ?
                    ORDER BY pm.path
                """, (project_id, folder_id, tag_name, project_id)).fetchall()

            paths = [os.path.abspath(r[0]) for r in rows if r and r[0]]

            self.logger.debug(
                f"get_images_by_folder_and_tag(project={project_id}, folder={folder_id}, tag={tag_name}, subfolders={include_subfolders}) "
                f"→ {len(paths)} photos"
            )

            return paths

    def get_images_by_date_and_tag(self, project_id: int, date_key: str, tag_name: str) -> list[str]:
        """
        Get image paths for a date (year/month/day) that have a specific tag.

        Args:
            project_id: Project ID to filter by
            date_key: Date key (YYYY, YYYY-MM, YYYY-MM-DD, or special keys like 'this-year', 'this-month', 'today')
            tag_name: Tag name to filter by

        Returns:
            List of image paths that match the date AND have the tag
        """
        from datetime import datetime, timedelta

        # Handle special date keys (this-year, this-month, today, etc.)
        if date_key in ('this-year', 'this-month', 'this-week', 'today', 'last-30d'):
            today = datetime.now().date()

            if date_key == 'this-year':
                # Photos from start of this year to today
                date_where = "pm.created_date >= ? AND pm.created_date <= ?"
                start = today.replace(month=1, day=1).isoformat()
                end = today.isoformat()
                date_params = [start, end]
            elif date_key == 'this-month':
                # Photos from start of this month to today
                date_where = "pm.created_date >= ? AND pm.created_date <= ?"
                start = today.replace(day=1).isoformat()
                end = today.isoformat()
                date_params = [start, end]
            elif date_key == 'this-week':
                # Photos from Monday to today
                date_where = "pm.created_date >= ? AND pm.created_date <= ?"
                start = (today - timedelta(days=today.weekday())).isoformat()
                end = today.isoformat()
                date_params = [start, end]
            elif date_key == 'today':
                # Photos from today only
                date_where = "pm.created_date = ?"
                date_params = [today.isoformat()]
            elif date_key == 'last-30d':
                # Photos from last 30 days
                date_where = "pm.created_date >= ? AND pm.created_date <= ?"
                start = (today - timedelta(days=29)).isoformat()
                end = today.isoformat()
                date_params = [start, end]
        # Handle concrete date formats
        elif len(date_key) == 4:  # Year (YYYY)
            date_where = "pm.created_year = ?"
            date_params = [int(date_key)]
        elif len(date_key) == 7:  # Year-Month (YYYY-MM)
            date_where = "pm.created_date LIKE ?"
            date_params = [f"{date_key}%"]
        elif len(date_key) == 10:  # Year-Month-Day (YYYY-MM-DD)
            date_where = "pm.created_date = ?"
            date_params = [date_key]
        else:
            self.logger.warning(f"Invalid date_key format: {date_key}")
            return []

        with self._connect() as conn:
            cur = conn.cursor()

            query = f"""
                SELECT DISTINCT pm.path
                FROM photo_metadata pm
                JOIN photo_tags pt ON pm.id = pt.photo_id
                JOIN tags t ON pt.tag_id = t.id
                WHERE pm.project_id = ?
                  AND {date_where}
                  AND t.name = ?
                  AND t.project_id = ?
                ORDER BY pm.path
            """

            params = [project_id] + date_params + [tag_name, project_id]
            rows = cur.execute(query, params).fetchall()

            paths = [os.path.abspath(r[0]) for r in rows if r and r[0]]

            self.logger.debug(
                f"get_images_by_date_and_tag(project={project_id}, date={date_key}, tag={tag_name}) "
                f"→ {len(paths)} photos"
            )

            return paths


    def get_image_count_recursive(self, folder_id: int, project_id: int | None = None) -> int:
        """
        Return total number of images under this folder, including its subfolders.

        Args:
            folder_id: Folder ID to count photos in
            project_id: Filter count to only photos from this project.
                       If None, counts all photos (backward compatibility).

        Uses recursive CTE for performance. Schema v3.2.0 uses direct project_id column.

        Performance: Uses compound index idx_photo_metadata_project_folder for fast filtering.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # PERFORMANCE: Use direct project_id column (no JOIN to project_images needed)
                # Schema v3.2.0 has project_id directly in photo_metadata and photo_folders
                cur.execute("""
                    WITH RECURSIVE subfolders(id) AS (
                        SELECT id FROM photo_folders
                        WHERE id = ? AND project_id = ?
                        UNION ALL
                        SELECT f.id
                        FROM photo_folders f
                        JOIN subfolders s ON f.parent_id = s.id
                        WHERE f.project_id = ?
                    )
                    SELECT COUNT(*)
                    FROM photo_metadata pm
                    WHERE pm.folder_id IN (SELECT id FROM subfolders)
                      AND pm.project_id = ?
                """, (folder_id, project_id, project_id, project_id))
            else:
                # No filter - count all photos (backward compatibility)
                cur.execute("""
                    WITH RECURSIVE subfolders(id) AS (
                        SELECT id FROM photo_folders WHERE id = ?
                        UNION ALL
                        SELECT f.id
                        FROM photo_folders f
                        JOIN subfolders s ON f.parent_id = s.id
                    )
                    SELECT COUNT(*) FROM photo_metadata p
                    WHERE p.folder_id IN (SELECT id FROM subfolders)
                """, (folder_id,))
            row = cur.fetchone()
            return row[0] if row else 0

    def get_video_count_recursive(self, folder_id: int, project_id: int | None = None) -> int:
        """
        Return total number of videos under this folder, including its subfolders.

        Args:
            folder_id: Folder ID to count videos in
            project_id: Filter count to only videos from this project.
                       If None, counts all videos (backward compatibility).

        Uses recursive CTE for performance, matching photo count implementation.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            if project_id is not None:
                # Use direct project_id column from video_metadata
                cur.execute("""
                    WITH RECURSIVE subfolders(id) AS (
                        SELECT id FROM photo_folders
                        WHERE id = ? AND project_id = ?
                        UNION ALL
                        SELECT f.id
                        FROM photo_folders f
                        JOIN subfolders s ON f.parent_id = s.id
                        WHERE f.project_id = ?
                    )
                    SELECT COUNT(*)
                    FROM video_metadata vm
                    WHERE vm.folder_id IN (SELECT id FROM subfolders)
                      AND vm.project_id = ?
                """, (folder_id, project_id, project_id, project_id))
            else:
                # No filter - count all videos (backward compatibility)
                cur.execute("""
                    WITH RECURSIVE subfolders(id) AS (
                        SELECT id FROM photo_folders WHERE id = ?
                        UNION ALL
                        SELECT f.id
                        FROM photo_folders f
                        JOIN subfolders s ON f.parent_id = s.id
                    )
                    SELECT COUNT(*) FROM video_metadata v
                    WHERE v.folder_id IN (SELECT id FROM subfolders)
                """, (folder_id,))
            row = cur.fetchone()
            return row[0] if row else 0

    def get_folder_counts_batch(self, project_id: int) -> dict[int, int]:
        """
        Get photo counts for ALL folders in ONE query (fixes N+1 problem).

        This is dramatically faster than calling get_image_count_recursive() for each folder.
        Used by sidebar folder tree to display counts efficiently.

        Args:
            project_id: Project ID to count photos for

        Returns:
            dict mapping folder_id -> photo_count (including subfolders)

        Performance:
            Before: N+1 queries (1 to get folders + 1 per folder for count)
            After: 1 query (get all counts at once)

        Example:
            counts = db.get_folder_counts_batch(project_id=1)
            # counts = {1: 150, 2: 75, 3: 0, ...}

        Note: Uses compound index idx_photo_metadata_project_folder for optimal performance.
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # OPTIMIZATION: Get counts for ALL folders at once using recursive CTE
            # This replaces N individual queries with ONE query
            cur.execute("""
                WITH RECURSIVE folder_tree AS (
                    -- Start with all folders in this project
                    SELECT id, parent_id, id as root_id
                    FROM photo_folders
                    WHERE project_id = ?

                    UNION ALL

                    -- Recursively include child folders, remembering the root ancestor
                    SELECT f.id, f.parent_id, ft.root_id
                    FROM photo_folders f
                    JOIN folder_tree ft ON f.parent_id = ft.id
                    WHERE f.project_id = ?
                )
                SELECT
                    ft.root_id as folder_id,
                    COUNT(pm.id) as photo_count
                FROM folder_tree ft
                LEFT JOIN photo_metadata pm
                    ON pm.folder_id = ft.id
                    AND pm.project_id = ?
                GROUP BY ft.root_id
            """, (project_id, project_id, project_id))

            # Convert to dict: folder_id -> count
            counts = {}
            for row in cur.fetchall():
                folder_id = row[0]
                photo_count = row[1] or 0
                counts[folder_id] = photo_count

            return counts

    def get_video_counts_batch(self, project_id: int) -> dict[int, int]:
        """
        Get video counts for ALL folders in ONE query (fixes N+1 problem).

        This mirrors get_folder_counts_batch() but for videos instead of photos.
        Dramatically faster than calling get_video_count_recursive() for each folder.

        Args:
            project_id: Project ID to count videos for

        Returns:
            dict mapping folder_id -> video_count (including subfolders)

        Performance:
            Before: N queries (1 per folder)
            After: 1 query (all counts at once)
            Speedup: 20x faster for 100 folders (1000ms → 50ms)

        Example:
            video_counts = db.get_video_counts_batch(project_id=1)
            # video_counts = {1: 25, 2: 10, 3: 0, ...}

        Note: Uses same recursive CTE pattern as photo counts.
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # OPTIMIZATION: Get counts for ALL folders at once using recursive CTE
            # This replaces N individual queries with ONE query
            cur.execute("""
                WITH RECURSIVE folder_tree AS (
                    -- Start with all folders in this project
                    SELECT id, parent_id, id as root_id
                    FROM photo_folders
                    WHERE project_id = ?

                    UNION ALL

                    -- Recursively include child folders, remembering the root ancestor
                    SELECT f.id, f.parent_id, ft.root_id
                    FROM photo_folders f
                    JOIN folder_tree ft ON f.parent_id = ft.id
                    WHERE f.project_id = ?
                )
                SELECT
                    ft.root_id as folder_id,
                    COUNT(vm.id) as video_count
                FROM folder_tree ft
                LEFT JOIN video_metadata vm
                    ON vm.folder_id = ft.id
                    AND vm.project_id = ?
                GROUP BY ft.root_id
            """, (project_id, project_id, project_id))

            # Convert to dict: folder_id -> count
            counts = {}
            for row in cur.fetchall():
                folder_id = row[0]
                video_count = row[1] or 0
                counts[folder_id] = video_count

            return counts

    def get_date_counts_batch(self, project_id: int) -> dict:
        """
        Get ALL date counts (year, month, day) in ONE query (fixes N+1 problem).

        This replaces multiple individual count queries with a single GROUP BY query.
        Dramatically faster for building date hierarchy in sidebar.

        Args:
            project_id: Project ID to count dates for

        Returns:
            dict with three sub-dicts:
            {
                'years': {2024: 523, 2023: 412, ...},
                'months': {'2024-11': 87, '2024-10': 93, ...},
                'days': {'2024-11-12': 23, '2024-11-13': 15, ...}
            }

        Performance:
            Before: 50+ individual COUNT queries (one per year, month, day)
            After: 1 query with GROUP BY
            Speedup: 8x faster (400ms → 50ms for large date hierarchies)

        Example:
            date_counts = db.get_date_counts_batch(project_id=1)
            year_count = date_counts['years'].get(2024, 0)  # 523
            month_count = date_counts['months'].get('2024-11', 0)  # 87
            day_count = date_counts['days'].get('2024-11-12', 0)  # 23

        Note: Uses compound indexes idx_photo_metadata_project_date and
              idx_video_metadata_project_date for optimal performance.
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # OPTIMIZATION: Single query with GROUP BY instead of N individual COUNTs
            # Combines photos and videos, groups by date fields
            cur.execute("""
                WITH all_dates AS (
                    -- Get all photo dates
                    SELECT created_date, created_year
                    FROM photo_metadata
                    WHERE project_id = ? AND created_date IS NOT NULL

                    UNION ALL

                    -- Get all video dates
                    SELECT created_date, created_year
                    FROM video_metadata
                    WHERE project_id = ? AND created_date IS NOT NULL
                )
                SELECT
                    created_year,
                    SUBSTR(created_date, 1, 7) as year_month,
                    created_date as day,
                    COUNT(*) as count
                FROM all_dates
                GROUP BY created_year, year_month, day
                ORDER BY created_date DESC
            """, (project_id, project_id))

            # Build three separate dictionaries for years, months, and days
            result = {
                'years': {},
                'months': {},
                'days': {}
            }

            for row in cur.fetchall():
                year = row[0]
                month = row[1]
                day = row[2]
                count = row[3]

                # Aggregate counts at each level
                result['years'][year] = result['years'].get(year, 0) + count
                result['months'][month] = result['months'].get(month, 0) + count
                result['days'][day] = count  # Day count is exact, no aggregation needed

            return result

    def get_video_date_counts_batch(self, project_id: int) -> dict:
        """
        Get ALL video date counts (year, month, day) in ONE query (fixes N+1 problem).

        Similar to get_date_counts_batch but for videos only.
        Used by video date hierarchy in sidebar to avoid individual count queries.

        Args:
            project_id: Project ID to count video dates for

        Returns:
            dict with three sub-dicts:
            {
                'years': {2024: 15, 2023: 8, ...},
                'months': {'2024-11': 5, '2024-10': 3, ...},
                'days': {'2024-11-12': 2, '2024-11-13': 1, ...}
            }

        Performance:
            Before: N individual COUNT queries (one per year, month, day)
            After: 1 query with GROUP BY
            Speedup: 10-20x faster for video date hierarchies

        Example:
            video_counts = db.get_video_date_counts_batch(project_id=1)
            year_count = video_counts['years'].get(2024, 0)  # 15
            month_count = video_counts['months'].get('2024-11', 0)  # 5
            day_count = video_counts['days'].get('2024-11-12', 0)  # 2
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # OPTIMIZATION: Single query with GROUP BY instead of N individual COUNTs
            cur.execute("""
                SELECT
                    created_year,
                    SUBSTR(created_date, 1, 7) as year_month,
                    created_date as day,
                    COUNT(*) as count
                FROM video_metadata
                WHERE project_id = ? AND created_date IS NOT NULL
                GROUP BY created_year, year_month, day
                ORDER BY created_date DESC
            """, (project_id,))

            # Build three separate dictionaries for years, months, and days
            result = {
                'years': {},
                'months': {},
                'days': {}
            }

            for row in cur.fetchall():
                year = row[0]
                month = row[1]
                day = row[2]
                count = row[3]

                # Aggregate counts at each level
                result['years'][year] = result['years'].get(year, 0) + count
                result['months'][month] = result['months'].get(month, 0) + count
                result['days'][day] = count  # Day count is exact, no aggregation needed

            return result


# --- Maintenance / Diagnostics ------------------------------------------------
    def fresh_reset(self):
        """
        Fully reset the reference database by:
          1. Closing active connections
          2. Forcing GC to release SQLite file handles
          3. Renaming existing DB to a timestamped backup
          4. Recreating the schema

        This method avoids WinError 32 (file locked) issues on Windows.
        """
        import os, time, gc

        # --- Step 1: close any open connection ---
        try:
            if hasattr(self, "_conn") and self._conn:
                try:
                    self._conn.close()
                    print("[DB] Active connection closed.")
                except Exception as e:
                    print(f"[DB] Warning: could not close connection cleanly: {e}")
                self._conn = None
        except Exception as e:
            print(f"[DB] Warning during connection cleanup: {e}")

        # --- Step 2: force garbage collection ---
        try:
            gc.collect()
        except Exception:
            pass

        # --- Step 3: rename existing DB if it exists ---
        if os.path.exists(self.db_file):
            backup_name = f"{self.db_file}.bak_{time.strftime('%Y%m%d_%H%M%S')}"
            for i in range(5):
                try:
                    os.rename(self.db_file, backup_name)
                    print(f"[DB] Moved existing DB to backup: {backup_name}")
                    break
                except PermissionError as e:
                    print(f"[DB] File lock detected, retrying {i+1}/5 ...")
                    time.sleep(0.4)
            else:
                print(f"[DB ERROR] fresh_reset failed: could not rename after 5 tries.")
                raise

        # --- Step 4: recreate new DB ---
        try:
            self._ensure_db()
            print("[DB] Fresh database created.")
        except Exception as e:
            print(f"[DB ERROR] Failed to recreate DB: {e}")
            raise


    def integrity_report(self) -> dict:
        """
        Return quick stats and integrity info to show in a message box.
        """
        out = {
            "ok": True,
            "errors": [],
            "counts": {}
        }
        try:
            with self._connect() as conn:
                cur = conn.cursor()

                # PRAGMA integrity_check
                try:
                    cur.execute("PRAGMA integrity_check;")
                    res = cur.fetchone()
                    out["ok"] = (res and res[0] == "ok")
                    if not out["ok"]:
                        out["errors"].append(f"PRAGMA integrity_check: {res[0] if res else 'unknown'}")
                except Exception as e:
                    out["ok"] = False
                    out["errors"].append(f"integrity_check error: {e}")

                # Basic counts
                def _count(tbl):
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    return cur.fetchone()[0] or 0

                counts = {
                    "photo_folders": _count("photo_folders"),
                    "photo_metadata": _count("photo_metadata"),
                    "projects": _count("projects"),
                    "branches": _count("branches"),
                    "project_images": _count("project_images"),
                }
                out["counts"] = counts

                # Orphans: metadata rows with missing folder
                cur.execute("""
                    SELECT COUNT(*)
                    FROM photo_metadata pm
                    LEFT JOIN photo_folders pf ON pf.id = pm.folder_id
                    WHERE pf.id IS NULL
                """)
                orphans = cur.fetchone()[0] or 0
                if orphans > 0:
                    out["errors"].append(f"Orphaned photo_metadata rows with missing folder_id: {orphans}")

        except Exception as e:
            out["ok"] = False
            out["errors"].append(str(e))
        return out

    def vacuum_analyze(self) -> None:
        """Optional: compact and refresh statistics."""
        with self._connect() as conn:
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            conn.commit()

    # --- add inside class ReferenceDB -------------------------------------------
    def ensure_created_date_fields(self) -> None:
        """Add created_ts / created_date / created_year + indexes (idempotent)."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {row[1] for row in cur.fetchall()}
            if "created_ts" not in cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER")
            if "created_date" not in cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_date TEXT")
            if "created_year" not in cols:
                cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_year  ON photo_metadata(created_year)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_date  ON photo_metadata(created_date)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_ts    ON photo_metadata(created_ts)")
            conn.commit()

    def count_missing_created_fields(self) -> int:
        """Return how many rows still need created_* filled. If cols missing, return total rows."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {row[1] for row in cur.fetchall()}
            if not {"created_ts", "created_date", "created_year"}.issubset(cols):
                cur.execute("SELECT COUNT(*) FROM photo_metadata")
                return cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*)
                FROM photo_metadata
                WHERE created_ts IS NULL OR created_date IS NULL OR created_year IS NULL
            """)
            return int(cur.fetchone()[0])

    def single_pass_backfill_created_fields(self, chunk_size: int = 1000) -> int:
        """
        Fill created_* for up to chunk_size rows. Returns number of rows updated this pass.
        Call repeatedly until it returns 0.
        """
        import datetime as _dt

        def _parse_any(s: str | None):
            if not s:
                return None
            fmts = [
                "%Y:%m:%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%d.%m.%Y %H:%M:%S",
                "%Y-%m-%d",
            ]
            for f in fmts:
                try:
                    return _dt.datetime.strptime(s, f)
                except Exception:
                    pass
            return None

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = {row[1] for row in cur.fetchall()}
            if not {"created_ts", "created_date", "created_year"}.issubset(cols):
                return 0

            cur.execute("""
                SELECT path, date_taken, modified
                FROM photo_metadata
                WHERE created_ts IS NULL OR created_date IS NULL OR created_year IS NULL
                LIMIT ?
            """, (chunk_size,))
            rows = cur.fetchall()
            if not rows:
                return 0

            updates = []
            for path, date_taken, modified in rows:
                t = _parse_any(date_taken) or _parse_any(modified)
                if not t:
                    updates.append((None, None, None, path))
                else:
                    ts = int(t.timestamp())
                    dstr = t.strftime("%Y-%m-%d")
                    updates.append((ts, dstr, int(dstr[:4]), path))

            cur.executemany("""
                UPDATE photo_metadata
                SET created_ts = ?, created_date = ?, created_year = ?
                WHERE path = ?
            """, updates)
            conn.commit()
            return len(updates)

    def single_pass_backfill_created_fields_videos(self, chunk_size: int = 1000) -> int:
        """
        SURGICAL FIX E: Fill created_* fields for videos from date_taken or modified.

        This mirrors single_pass_backfill_created_fields() but operates on video_metadata.
        Ensures videos have created_ts, created_date, created_year populated even if
        they were indexed without these fields.

        Args:
            chunk_size: Number of rows to process in this pass

        Returns:
            Number of rows updated (0 when all done)

        Usage:
            >>> while db.single_pass_backfill_created_fields_videos() > 0:
            ...     pass  # Keep calling until done
        """
        import datetime as _dt

        def _parse_any(s: str | None):
            """Parse date from various formats."""
            if not s:
                return None
            fmts = [
                "%Y:%m:%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%d.%m.%Y %H:%M:%S",
                "%Y-%m-%d",
            ]
            for f in fmts:
                try:
                    return _dt.datetime.strptime(s, f)
                except Exception:
                    pass
            return None

        with self._connect() as conn:
            cur = conn.cursor()

            # Check if video_metadata has created_* columns
            cur.execute("PRAGMA table_info(video_metadata)")
            cols = {row[1] for row in cur.fetchall()}
            if not {"created_ts", "created_date", "created_year"}.issubset(cols):
                return 0

            # Get videos missing created_* fields
            cur.execute("""
                SELECT path, date_taken, modified
                FROM video_metadata
                WHERE created_ts IS NULL OR created_date IS NULL OR created_year IS NULL
                LIMIT ?
            """, (chunk_size,))
            rows = cur.fetchall()

            if not rows:
                return 0

            # Compute created_* fields
            updates = []
            for path, date_taken, modified in rows:
                # Try date_taken first, fall back to modified
                t = _parse_any(date_taken) or _parse_any(modified)
                if not t:
                    updates.append((None, None, None, path))
                else:
                    ts = int(t.timestamp())
                    dstr = t.strftime("%Y-%m-%d")
                    updates.append((ts, dstr, int(dstr[:4]), path))

            # Update video_metadata
            cur.executemany("""
                UPDATE video_metadata
                SET created_ts = ?, created_date = ?, created_year = ?
                WHERE path = ?
            """, updates)
            conn.commit()
            return len(updates)

    # >>> NEW: Face cluster utilities (Phase 7.1)

    def get_face_clusters(self, project_id: int):
        try:
            with self._connect() as conn:
                # prefer modern table
                cur = conn.execute("""
                    SELECT 
                        branch_key,
                        COALESCE(label, branch_key) AS display_name,
                        count AS member_count,
                        rep_path,
                        rep_thumb_png
                    FROM face_branch_reps
                    WHERE project_id = ?
                    ORDER BY count DESC, branch_key ASC
                """, (project_id,))
                rows = cur.fetchall()
        except Exception as e:
            # fallback legacy
            print(f"[DB] get_face_clusters fallback due to {e}")
            try:
                with self._connect() as conn:
                    cur = conn.execute("""
                        SELECT 
                            branch_key,
                            display_name,
                            COUNT(id) AS member_count,
                            rep_path,
                            NULL
                        FROM face_clusters
                        WHERE project_id = ?
                        GROUP BY branch_key, display_name, rep_path
                        ORDER BY member_count DESC
                    """, (project_id,))
                    rows = cur.fetchall()
            except Exception as e2:
                print(f"[DB] get_face_clusters final fail: {e2}")
                return []

        return [
            {
                "branch_key": r[0],
                "display_name": r[1],
                "member_count": r[2] or 0,
                "rep_path": r[3],
                "rep_thumb_png": r[4],
            }
            for r in rows
        ]

    def get_face_clusters_for_project(self, project_id):
        """
        Patch H: Canonical loader for face clusters with explicit branch join.
        Provides display_name from branches table if not in reps.
        """
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT r.branch_key, r.rep_path, r.count, b.display_name
                FROM face_branch_reps r
                LEFT JOIN branches b
                  ON b.project_id=r.project_id AND b.branch_key=r.branch_key
                WHERE r.project_id=?
            """, (project_id,)).fetchall()

        return [dict(row) for row in rows]


    def rename_face_cluster(self, project_id: int, branch_key: str, new_label: str):
        """
        Rename a face cluster consistently in all modern places:
          * face_branch_reps.label
          * branches.display_name
        """
        if not project_id or not branch_key or not new_label:
            return

        new_label = str(new_label).strip()
        if not new_label:
            return

        with self._connect() as conn:
            cur = conn.cursor()

            # Modern face cluster label (preferred source of truth)
            cur.execute(
                """
                UPDATE face_branch_reps
                   SET label = ?
                 WHERE project_id = ? AND branch_key = ?
                """,
                (new_label, project_id, branch_key),
            )

            # Legacy / generic branch label for the same key
            cur.execute(
                """
                UPDATE branches
                   SET display_name = ?
                 WHERE project_id = ? AND branch_key = ?
                """,
                (new_label, project_id, branch_key),
            )

            conn.commit()
 


    def get_paths_for_cluster(self, project_id: int, branch_key: str):
        """
        Return all image paths belonging to the given face cluster.
        """
        with self._connect() as conn:
            cur = conn.execute("""

                SELECT crop_path FROM face_crops
                WHERE project_id=? AND branch_key=?
                ORDER BY id
            """, (project_id, branch_key))
            return [r[0] for r in cur.fetchall()]

#            return rows

    # ------------------------------------------------------
    # FEATURE #1: Face Detection Scope Selection Support
    # ------------------------------------------------------

    def get_all_paths_with_dates(self, project_id: int) -> List[Dict[str, Any]]:
        """
        FEATURE #1: Get all photo paths with their dates for scope selection.

        Returns:
            List of dicts with 'path' and 'date' keys
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT path, datetime(date_taken) as date
                FROM photo_metadata
                WHERE project_id = ?
                ORDER BY date_taken DESC
            """, (project_id,))

            return [
                {"path": row[0], "date": datetime.fromisoformat(row[1]) if row[1] else None}
                for row in cur.fetchall()
            ]

    def get_folders_with_counts(self, project_id: int) -> List[Dict[str, Any]]:
        """
        FEATURE #1: Get folders with photo counts for folder selection.

        Returns:
            List of dicts with 'id', 'name', 'path', 'parent_id', and 'count' keys
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT pf.id, pf.name, pf.path, pf.parent_id, COUNT(pm.id) as count
                FROM photo_folders pf
                LEFT JOIN photo_metadata pm ON pm.folder_id = pf.id
                WHERE pf.project_id = ?
                GROUP BY pf.id, pf.name, pf.path, pf.parent_id
                ORDER BY pf.path
            """, (project_id,))

            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "path": row[2],
                    "parent_id": row[3],
                    "count": row[4]
                }
                for row in cur.fetchall()
            ]

    def get_photos_for_folders(self, project_id: int, folder_ids: List[int]) -> List[str]:
        """
        FEATURE #1: Get photo paths for specific folder IDs (including subfolders).

        Args:
            project_id: Project ID
            folder_ids: List of folder IDs to get photos from

        Returns:
            List of photo paths from the selected folders
        """
        if not folder_ids:
            return []

        # Get all descendant folder IDs (including the selected folders themselves)
        all_folder_ids = set(folder_ids)

        # Recursively get all child folder IDs
        def get_all_descendants(fid):
            children = self.get_child_folders(fid, project_id)
            for child in children:
                child_id = child['id']
                if child_id not in all_folder_ids:
                    all_folder_ids.add(child_id)
                    get_all_descendants(child_id)

        for fid in folder_ids:
            get_all_descendants(fid)

        with self._connect() as conn:
            placeholders = ','.join('?' * len(all_folder_ids))
            cur = conn.execute(f"""
                SELECT path
                FROM photo_metadata
                WHERE project_id = ? AND folder_id IN ({placeholders})
                ORDER BY date_taken DESC
            """, (project_id, *all_folder_ids))

            return [row[0] for row in cur.fetchall()]

    def get_photo_ids_for_folders(self, project_id: int, folder_ids: List[int]) -> List[int]:
        """
        Get photo IDs for specific folder IDs (including subfolders).

        Args:
            project_id: Project ID
            folder_ids: List of folder IDs to get photos from

        Returns:
            List of photo IDs from the selected folders
        """
        if not folder_ids:
            return []

        # Get all descendant folder IDs (including the selected folders themselves)
        all_folder_ids = set(folder_ids)

        # Recursively get all child folder IDs
        def get_all_descendants(fid):
            children = self.get_child_folders(fid, project_id)
            for child in children:
                child_id = child['id']
                if child_id not in all_folder_ids:
                    all_folder_ids.add(child_id)
                    get_all_descendants(child_id)

        for fid in folder_ids:
            get_all_descendants(fid)

        with self._connect() as conn:
            placeholders = ','.join('?' * len(all_folder_ids))
            cur = conn.execute(f"""
                SELECT id
                FROM photo_metadata
                WHERE project_id = ? AND folder_id IN ({placeholders})
                ORDER BY date_taken DESC
            """, (project_id, *all_folder_ids))

            return [row[0] for row in cur.fetchall()]

    def get_photo_ids_with_embeddings(self, project_id: int) -> List[int]:
        """
        Get photo IDs that already have semantic embeddings.

        Used to skip already-processed photos in duplicate/similarity detection.

        Returns:
            List of photo IDs that have semantic embeddings
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT DISTINCT se.photo_id
                FROM semantic_embeddings se
                JOIN photo_metadata p ON se.photo_id = p.id
                WHERE p.project_id = ?
            """, (project_id,))

            return [row[0] for row in cur.fetchall()]

    def get_paths_with_embeddings(self, project_id: int) -> List[str]:
        """
        FEATURE #1: Get photo paths that already have face embeddings.

        Used to skip already-processed photos in face detection.

        Returns:
            List of photo paths that have face embeddings
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT DISTINCT image_path
                FROM face_crops
                WHERE project_id = ? AND embedding IS NOT NULL
            """, (project_id,))

            return [row[0] for row in cur.fetchall()]


    # ------------------------------------------------------
    # FACE CLUSTER MERGE / UNDO / SUGGESTIONS
    # ------------------------------------------------------

    def merge_face_clusters(self, project_id: int, target_branch: str, source_branches, log_undo: bool = True):
        """
        Merge one or more source face clusters into a target cluster.
        - project_id: current project
        - target_branch: e.g. "face_000"
        - source_branches: iterable of "face_XXX" keys (will be merged *into* target)

        This updates:
          * face_crops.branch_key
          * project_images.branch_key  (so branch-based views stay in sync)
          * face_branch_reps           (source reps removed, target kept)
          * branches                   (source branch rows removed)
        and writes a JSON snapshot into face_merge_history so we can undo later.
        """
        if not project_id:
            raise ValueError("merge_face_clusters requires a project_id")
        if not target_branch:
            raise ValueError("merge_face_clusters requires a target_branch")

        # Normalise & dedupe sources
        
        ## Patch Deterministic source dedupe (keine Sets)
        src_list = [str(b) for b in (source_branches or []) if b]
        # Deterministic de-dupe, preserve order
        seen = set()
        src_list = [b for b in src_list if b != target_branch and not (b in seen or seen.add(b))]
        ## END Patch
        
        if not src_list:
            return {"moved_faces": 0, "moved_images": 0, "deleted_reps": 0, "sources": [], "target": target_branch}

        print(f"[merge_face_clusters] project_id={project_id}, target='{target_branch}', sources={src_list}")

        # Prepare shared key list (target + sources) for snapshot
        all_keys = [target_branch] + src_list

        from datetime import datetime
        import json as _json

        with self._connect() as conn:

            # CRITICAL: We need named-column access (row["project_id"], etc.)
            # for the snapshot building below. Set row_factory BEFORE any execute calls.
            import sqlite3
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Enable foreign keys after setting row_factory
            cur.execute("PRAGMA foreign_keys = ON")

            # ---------------- SNAPSHOT (for undo) ----------------
#            snapshot: dict[str, list] = {
#                "branch_keys": all_keys,
#                "branches": [],
#                "face_branch_reps": [],
#                "face_crops": [],
#                "project_images": [],
#                "project_images_deleted": [],   # Patch: snapshot erweitern+rows vor delete selektieren
#            }

            snapshot: dict[str, list] = {
                "branch_keys": all_keys,
                "branches": [],
                "face_branch_reps": [],
                "face_crops": [],
                "project_images": [],
                "project_images_deleted": [],   # Patch: snapshot erweitern+rows vor delete selektieren
                # People Groups (v9.5.0): needed for correct undo and consistency
                "affected_group_ids": [],
                "person_groups": [],
                "person_group_members": [],
                "group_asset_matches": [],
            }

            placeholders = ",".join("?" * len(all_keys))
 
            # ---------------- GROUPS SNAPSHOT (v9.5.0 People Groups) ----------------
            # Capture any groups that reference target or sources, so undo can restore them.
            try:
                cur.execute(
                    f"""
                    SELECT DISTINCT pg.id
                    FROM person_groups pg
                    JOIN person_group_members pgm ON pgm.group_id = pg.id
                    WHERE pg.project_id = ?
                      AND pgm.branch_key IN ({placeholders})
                    """,
                    [project_id] + all_keys,
                )
                affected_group_ids = [int(r[0]) for r in cur.fetchall()]
                snapshot["affected_group_ids"] = affected_group_ids

                if affected_group_ids:
                    gid_ph = ",".join("?" * len(affected_group_ids))

                    # Snapshot person_groups rows (for completeness, supports rename/pin metadata)
                    cur.execute(
                        f"""
                        SELECT *
                        FROM person_groups
                        WHERE project_id = ? AND id IN ({gid_ph})
                        """,
                        [project_id] + affected_group_ids,
                    )
                    snapshot["person_groups"] = [dict(r) for r in cur.fetchall()]

                    # Snapshot members for these groups
                    cur.execute(
                        f"""
                        SELECT *
                        FROM person_group_members
                        WHERE group_id IN ({gid_ph})
                        """,
                        affected_group_ids,
                    )
                    snapshot["person_group_members"] = [dict(r) for r in cur.fetchall()]

                    # Snapshot cached matches, so undo can restore cache if you want deterministic behavior
                    cur.execute(
                        f"""
                        SELECT *
                        FROM group_asset_matches
                        WHERE group_id IN ({gid_ph})
                        """,
                        affected_group_ids,
                    )
                    snapshot["group_asset_matches"] = [dict(r) for r in cur.fetchall()]

                    print(f"[merge_face_clusters] Groups snapshot: {len(affected_group_ids)} group(s)")
            except Exception as e:
                print(f"[merge_face_clusters] Groups snapshot skipped: {e}")

            # branches
            cur.execute(
                f"SELECT project_id, branch_key, display_name "
                f"FROM branches WHERE project_id = ? AND branch_key IN ({placeholders})",
                [project_id] + all_keys,
            )
            branches_rows = cur.fetchall()
            print(f"[merge_face_clusters] Found {len(branches_rows)} branches. Row type: {type(branches_rows[0]) if branches_rows else 'N/A'}")
            for row in branches_rows:
                try:
                    snapshot["branches"].append(
                        {
                            "project_id": row["project_id"],
                            "branch_key": row["branch_key"],
                            "display_name": row["display_name"],
                        }
                    )
                except (TypeError, KeyError) as e:
                    print(f"[merge_face_clusters] ERROR accessing row: {e}, row type={type(row)}, row={row}")
                    # Fallback to tuple indexing if dict access fails
                    snapshot["branches"].append(
                        {
                            "project_id": row[0],
                            "branch_key": row[1],
                            "display_name": row[2],
                        }
                    )

            # face_branch_reps (NOTE: table has NO 'id' column, uses composite PK)
            cur.execute(
                f"SELECT project_id, branch_key, rep_path, rep_thumb_png, label, centroid, count "
                f"FROM face_branch_reps WHERE project_id = ? AND branch_key IN ({placeholders})",
                [project_id] + all_keys,
            )
            reps_rows = cur.fetchall()
            print(f"[merge_face_clusters] Found {len(reps_rows)} face_branch_reps rows")

            # CRITICAL: Convert BLOB fields (bytes) to base64 for JSON serialization
            import base64
            for row in reps_rows:
                # Encode bytes fields to base64 strings (so JSON can serialize them)
                centroid_b64 = base64.b64encode(row["centroid"]).decode('utf-8') if row["centroid"] else None
                rep_thumb_b64 = base64.b64encode(row["rep_thumb_png"]).decode('utf-8') if row["rep_thumb_png"] else None

                snapshot["face_branch_reps"].append(
                    {
                        "project_id": row["project_id"],
                        "branch_key": row["branch_key"],
                        "rep_path": row["rep_path"],
                        "rep_thumb_png": rep_thumb_b64,  # base64 string, not bytes
                        "label": row["label"],
                        "centroid": centroid_b64,  # base64 string, not bytes
                        "count": row["count"],  # CRITICAL: Include count for undo
                    }
                )

            # face_crops
            cur.execute(
                f"SELECT id, branch_key FROM face_crops "
                f"WHERE project_id = ? AND branch_key IN ({placeholders})",
                [project_id] + all_keys,
            )
            snapshot["face_crops"] = [
                {"id": r["id"], "branch_key": r["branch_key"]}
                for r in cur.fetchall()
            ]

            # project_images
            cur.execute(
                f"SELECT id, branch_key FROM project_images "
                f"WHERE project_id = ? AND branch_key IN ({placeholders})",
                [project_id] + all_keys,
            )
            snapshot["project_images"] = [
                {"id": r["id"], "branch_key": r["branch_key"]}
                for r in cur.fetchall()
            ]

            # Log snapshot for undo
            if log_undo:
                cur.execute(
                    """
                    INSERT INTO face_merge_history
                        (project_id, target_branch, source_branches, snapshot, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        target_branch,
                        ",".join(src_list),
                        _json.dumps(snapshot),
                        datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )

            # ---------------- DO THE MERGE ----------------
            src_placeholders = ",".join("?" * len(src_list))

            # 1) face_crops → move all crops into target cluster
            cur.execute(
                f"""
                UPDATE face_crops
                SET branch_key = ?
                WHERE project_id = ?
                  AND branch_key IN ({src_placeholders})
                """,
                [target_branch, project_id] + src_list,
            )
            moved_faces = cur.rowcount

            # 2) project_images → keep branch-based browsing consistent
            # CRITICAL FIX (2025-12-05): Properly handle merging of project_images
            # Previous bug: Deleted source entries if target had same image_path, but
            # didn't ensure they were properly linked to target. This caused photos to
            # disappear from grid (no project_images link).
            #
            # NEW APPROACH:
            # - If photo exists in BOTH source and target: keep target, delete source (true duplicate)
            # - If photo exists ONLY in source: UPDATE to target (move the link)
            # This ensures no photos lose their project_images link during merge

            ## Patch: snapshot erweitern + rows vor delete selektieren ##
            # First, handle true duplicates (photos in both source AND target branches)
            # Capture deleted rows fully for perfect undo.

            cur.execute(
                f"""
                SELECT id, project_id, branch_key, image_path, label
                FROM project_images
                WHERE project_id = ?
                  AND branch_key IN ({src_placeholders})
                  AND image_path IN (
                      SELECT pi_target.image_path
                      FROM project_images pi_target
                      WHERE pi_target.project_id = ?
                        AND pi_target.branch_key = ?
                  )
                """,
                [project_id] + src_list + [project_id, target_branch],
            )
            snapshot["project_images_deleted"] = [dict(r) for r in cur.fetchall()]
            ## END Patch ##            
            
            cur.execute(
                f"""
                DELETE FROM project_images
                WHERE project_id = ?
                  AND branch_key IN ({src_placeholders})
                  AND image_path IN (
                      SELECT pi_target.image_path
                      FROM project_images pi_target
                      WHERE pi_target.project_id = ?
                        AND pi_target.branch_key = ?
                  )
                """,
                [project_id] + src_list + [project_id, target_branch],
            )
            deleted_duplicates = cur.rowcount
            print(f"[merge_face_clusters] Deleted {deleted_duplicates} TRUE duplicate project_images entries (photos already in target)")

            # Now UPDATE remaining source entries to target (photos that only exist in source)
            # These are NOT duplicates - they need to be moved to target branch
            cur.execute(
                f"""
                UPDATE project_images
                SET branch_key = ?
                WHERE project_id = ?
                  AND branch_key IN ({src_placeholders})
                """,
                [target_branch, project_id] + src_list,
            )
            moved_images = cur.rowcount
            print(f"[merge_face_clusters] Moved {moved_images} unique project_images entries from source to target")

            # VALIDATION: Ensure all face_crops photos have project_images entries
            # This catches any logic bugs where photos might lose their link
            cur.execute(
                """
                SELECT COUNT(DISTINCT fc.image_path)
                FROM face_crops fc
                LEFT JOIN project_images pi ON fc.image_path = pi.image_path
                                            AND pi.project_id = fc.project_id
                                            AND pi.branch_key = fc.branch_key
                WHERE fc.project_id = ? AND fc.branch_key = ?
                  AND pi.image_path IS NULL
                """,
                [project_id, target_branch],
            )
            orphaned_count = cur.fetchone()[0]

            if orphaned_count > 0:
                # BUG: Some face_crops lost their project_images link!
                print(f"[merge_face_clusters] ⚠️ WARNING: {orphaned_count} face_crops for {target_branch} have no project_images link!")
                print(f"[merge_face_clusters] ⚠️ This will cause count mismatch in grid. Auto-fixing...")

                # Auto-fix: Insert missing project_images entries
                cur.execute(
                    """
                    INSERT OR IGNORE INTO project_images (project_id, branch_key, image_path)
                    SELECT fc.project_id, fc.branch_key, fc.image_path
                    FROM face_crops fc
                    LEFT JOIN project_images pi ON fc.image_path = pi.image_path
                                                AND pi.project_id = fc.project_id
                                                AND pi.branch_key = fc.branch_key
                    WHERE fc.project_id = ? AND fc.branch_key = ?
                      AND pi.image_path IS NULL
                    """,
                    [project_id, target_branch],
                )
                fixed_count = cur.rowcount
                print(f"[merge_face_clusters] ✓ Auto-fixed {fixed_count} missing project_images entries")

            total_moved = moved_images + deleted_duplicates

            # 3) Representatives: delete reps for source clusters (target kept as-is)
            cur.execute(
                f"""
                DELETE FROM face_branch_reps
                WHERE project_id = ?
                  AND branch_key IN ({src_placeholders})
                """,
                [project_id] + src_list,
            )
            deleted_reps = cur.rowcount

            # 4) Branch rows for source clusters
            cur.execute(
                f"""
                DELETE FROM branches
                WHERE project_id = ?
                  AND branch_key IN ({src_placeholders})
                """,
                [project_id] + src_list,
            )

 
            # ---------------- GROUPS FIX (v9.5.0 People Groups) ----------------
            # Rewrite group membership and invalidate cached matches so group pipeline stays correct.
            # Pattern: Apple/Google/Lightroom do not tolerate "dangling" people references.
            try:
                # Groups that contain any of the merged-away source branches
                cur.execute(
                    f"""
                    SELECT DISTINCT pg.id
                    FROM person_groups pg
                    JOIN person_group_members pgm ON pgm.group_id = pg.id
                    WHERE pg.project_id = ?
                      AND pgm.branch_key IN ({src_placeholders})
                    """,
                    [project_id] + src_list,
                )
                affected_group_ids = [int(r[0]) for r in cur.fetchall()]

                if affected_group_ids:
                    for gid in affected_group_ids:
                        # If target already exists as a member in this group,
                        # delete the source members to prevent duplicates.
                        cur.execute(
                            "SELECT 1 FROM person_group_members WHERE group_id = ? AND branch_key = ? LIMIT 1",
                            (gid, target_branch),
                        )
                        target_exists = cur.fetchone() is not None

                        if target_exists:
                            cur.execute(
                                f"""
                                DELETE FROM person_group_members
                                WHERE group_id = ?
                                  AND branch_key IN ({src_placeholders})
                                """,
                                [gid] + src_list,
                            )
                        else:
                            # Safe remap, target not present
                            cur.execute(
                                f"""
                                UPDATE person_group_members
                                SET branch_key = ?
                                WHERE group_id = ?
                                  AND branch_key IN ({src_placeholders})
                                """,
                                [target_branch, gid] + src_list,
                            )

                        # Invalidate cached matches for this group, all scopes
                        cur.execute("DELETE FROM group_asset_matches WHERE group_id = ?", (gid,))

                    print(f"[merge_face_clusters] Groups updated: {len(affected_group_ids)} affected group(s)")
            except Exception as e:
                print(f"[merge_face_clusters] ⚠️ Groups update skipped due to error: {e}")


             # 5) CRITICAL: Update count for target cluster to reflect UNIQUE PHOTOS


            # 5) CRITICAL: Update count for target cluster to reflect UNIQUE PHOTOS
            # Count must be based on DISTINCT photos (not face_crops count) to match grid display
            # Why: A photo can have multiple faces, but should count as 1 photo in the UI
            # Fix: Use COUNT(DISTINCT image_path) from face_crops + project_images join
            cur.execute(
                """
                UPDATE face_branch_reps
                SET count = (
                    SELECT COUNT(DISTINCT fc.image_path)
                    FROM face_crops fc
                    JOIN project_images pi ON fc.image_path = pi.image_path
                                          AND fc.project_id = pi.project_id
                                          AND fc.branch_key = pi.branch_key
                    WHERE fc.project_id = ? AND fc.branch_key = ?
                )
                WHERE project_id = ? AND branch_key = ?
                """,
                [project_id, target_branch, project_id, target_branch],
            )
            updated_count = cur.rowcount
            print(f"[merge_face_clusters] Updated count for target '{target_branch}' to reflect unique photos (rowcount={updated_count})")

            # 6) Get final photo count in target cluster (unique photos)
            cur.execute(
                """
                SELECT COUNT(DISTINCT image_path)
                FROM project_images
                WHERE project_id = ? AND branch_key = ?
                """,
                [project_id, target_branch],
            )
            total_photos = cur.fetchone()[0]

            # 7) BEST PRACTICE: Refresh ALL face cluster counts to reflect final database state
            # This ensures consistency across all clusters after merge operations
            # Especially important when dealing with photos that have multiple faces
            print(f"[merge_face_clusters] Refreshing ALL face cluster counts for project {project_id}...")
            cur.execute(
                """
                UPDATE face_branch_reps
                SET count = (
                    SELECT COUNT(DISTINCT fc.image_path)
                    FROM face_crops fc
                    JOIN project_images pi ON fc.image_path = pi.image_path
                                          AND fc.project_id = pi.project_id
                                          AND fc.branch_key = pi.branch_key
                    WHERE fc.project_id = face_branch_reps.project_id
                      AND fc.branch_key = face_branch_reps.branch_key
                )
                WHERE project_id = ?
                """,
                [project_id],
            )
            refreshed_count = cur.rowcount
            print(f"[merge_face_clusters] ✓ Refreshed counts for {refreshed_count} face clusters")

            conn.commit()

            # Enhanced result with duplicate detection info for UI notifications
            result = {
                "moved_faces": moved_faces,           # Face crops reassigned to target
                "duplicates_found": deleted_duplicates,  # Photos already in target (not duplicated)
                "unique_moved": moved_images,         # Unique photos moved from source
                "total_photos": total_photos,         # Final unique photo count in target
                "moved_images": total_moved,          # Total affected (for backwards compatibility)
                "deleted_reps": deleted_reps,
                "sources": src_list,
                "target": target_branch,
            }

            # Enhanced logging for debugging
            if deleted_duplicates > 0:
                print(f"[merge_face_clusters] ⚠️ DUPLICATE DETECTION: Found {deleted_duplicates} photos already in target")
            print(f"[merge_face_clusters] SUCCESS: Moved {moved_faces} faces, {deleted_duplicates} duplicates, {moved_images} unique photos")
            print(f"[merge_face_clusters] Final result: {total_photos} unique photos in '{target_branch}'")

            # Mark affected groups as stale (v9.5.0 People Groups)
            # Groups containing source or target branches need recomputation
            try:
                from services.people_group_service import PeopleGroupService
                group_service = PeopleGroupService(self)
                
#                affected_branches = [target_branch] + src_list
#                for branch in affected_branches:
#                    group_service.mark_groups_stale_for_person(project_id, branch)
                    
                group_service.mark_groups_stale_for_person(project_id, target_branch)
                
            except Exception as group_error:
                print(f"[merge_face_clusters] Failed to mark groups stale: {group_error}")

            return result


    def undo_last_face_merge(self, project_id: int):
        """
        Undo the *last* face merge for this project, if any.
        Uses the snapshot stored in face_merge_history.
        """
        import json as _json

        if not project_id:
            return None

        with self._connect() as conn:
            # Use Row here as well, because we index `row["id"]`.
            import sqlite3
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            row = cur.execute(
                """
                SELECT id, snapshot
                FROM face_merge_history
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()

            if not row:
                return None

            log_id = row["id"]
            snapshot = _json.loads(row["snapshot"])
            branch_keys = snapshot.get("branch_keys") or []
            placeholders = ",".join("?" * len(branch_keys)) if branch_keys else ""

            faces = snapshot.get("face_crops", [])
            imgs = snapshot.get("project_images", [])

            ## Patch: Undo reinsert deleted rows vor branch restore ##
            imgs_deleted = snapshot.get("project_images_deleted", [])
            ## END Patch ##
            
            branches = snapshot.get("branches", [])
            reps = snapshot.get("face_branch_reps", [])


            # People Groups snapshot
            affected_group_ids = snapshot.get("affected_group_ids", []) or []
            pg_rows = snapshot.get("person_groups", []) or []
            pgm_rows = snapshot.get("person_group_members", []) or []
            gam_rows = snapshot.get("group_asset_matches", []) or []

            faces_restored = 0
            images_restored = 0

            # Restore branch + rep tables first so views are consistent
            if branch_keys:
                # branches
                cur.execute(
                    f"DELETE FROM branches WHERE project_id = ? AND branch_key IN ({placeholders})",
                    [project_id] + branch_keys,
                )
                for b in branches:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO branches (project_id, branch_key, display_name)
                        VALUES (?, ?, ?)
                        """,
                        (b["project_id"], b["branch_key"], b.get("display_name")),
                    )

                # face_branch_reps
                cur.execute(
                    f"DELETE FROM face_branch_reps WHERE project_id = ? AND branch_key IN ({placeholders})",
                    [project_id] + branch_keys,
                )

                # CRITICAL: Decode base64 strings back to bytes for BLOB columns
                import base64
                for r in reps:
                    # Decode base64 strings to bytes (snapshot stores them as base64)
                    centroid_bytes = base64.b64decode(r["centroid"]) if r.get("centroid") else None
                    rep_thumb_bytes = base64.b64decode(r["rep_thumb_png"]) if r.get("rep_thumb_png") else None

                    cur.execute(
                        """
                        INSERT INTO face_branch_reps
                            (project_id, branch_key, rep_path, rep_thumb_png, label, centroid, count)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r["project_id"],
                            r["branch_key"],
                            r["rep_path"],
                            rep_thumb_bytes,  # bytes, decoded from base64
                            r["label"],
                            centroid_bytes,  # bytes, decoded from base64
                            r.get("count", 0),  # CRITICAL: Restore count from snapshot
                        ),
                    )

            # Restore face_crops
            for rec in faces:
                cur.execute(
                    "UPDATE face_crops SET branch_key = ? WHERE id = ?",
                    (rec["branch_key"], rec["id"]),
                )
                faces_restored += cur.rowcount
            
            ## Patch: Undo reinsert deleted rows vor branch restore ##
            # Re-insert any deleted duplicate project_images rows (perfect undo)
            for rec in imgs_deleted:
                cur.execute("SELECT 1 FROM project_images WHERE id = ?", (rec["id"],))
                if cur.fetchone() is None:
                    cur.execute(
                        """
                        INSERT INTO project_images (id, project_id, branch_key, image_path, label)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (rec["id"], rec["project_id"], rec["branch_key"], rec["image_path"], rec.get("label")),
                    )
            ## END Patch ##
            
            # Restore project_images
            for rec in imgs:
                cur.execute(
                    "UPDATE project_images SET branch_key = ? WHERE id = ?",
                    (rec["branch_key"], rec["id"]),
                )
                images_restored += cur.rowcount
 
            # ---------------- RESTORE GROUPS (v9.5.0 People Groups) ----------------
            # If merge touched groups, restore their members and cached matches.
            # This makes undo truly undo, not just undo faces.
            try:
                if affected_group_ids:
                    gid_ph = ",".join("?" * len(affected_group_ids))

                    # Restore person_groups metadata (optional but safe)
                    # If you prefer not to restore metadata, you can skip this block.
                    if pg_rows:
                        # Ensure rows exist, replace to restore previous state
                        # This assumes person_groups has a primary key id.
                        # If your schema differs, adjust columns accordingly.
                        # First delete current rows for these group ids, then reinsert snapshot.
                        cur.execute(
                            f"DELETE FROM person_groups WHERE project_id = ? AND id IN ({gid_ph})",
                            [project_id] + affected_group_ids,
                        )
                        # Reinsert snapshot rows
                        for r in pg_rows:
                            cols = list(r.keys())
                            vals = [r[c] for c in cols]
                            col_sql = ",".join(cols)
                            q_sql = ",".join(["?"] * len(cols))
                            cur.execute(
                                f"INSERT INTO person_groups ({col_sql}) VALUES ({q_sql})",
                                vals,
                            )

                    # Restore members: delete current, then reinsert snapshot
                    cur.execute(
                        f"DELETE FROM person_group_members WHERE group_id IN ({gid_ph})",
                        affected_group_ids,
                    )
                    for r in pgm_rows:
                        cols = list(r.keys())
                        vals = [r[c] for c in cols]
                        col_sql = ",".join(cols)
                        q_sql = ",".join(["?"] * len(cols))
                        cur.execute(
                            f"INSERT INTO person_group_members ({col_sql}) VALUES ({q_sql})",
                            vals,
                        )

                    # Restore cached matches (optional). You can also choose to clear cache instead.
                    cur.execute(
                        f"DELETE FROM group_asset_matches WHERE group_id IN ({gid_ph})",
                        affected_group_ids,
                    )
                    for r in gam_rows:
                        cols = list(r.keys())
                        vals = [r[c] for c in cols]
                        col_sql = ",".join(cols)
                        q_sql = ",".join(["?"] * len(cols))
                        cur.execute(
                            f"INSERT INTO group_asset_matches ({col_sql}) VALUES ({q_sql})",
                            vals,
                        )

                    print(f"[undo_last_face_merge] Groups restored: {len(affected_group_ids)} group(s)")
            except Exception as e:
                print(f"[undo_last_face_merge] ⚠️ Groups restore skipped due to error: {e}")




            # Remove history entry we just consumed
            cur.execute("DELETE FROM face_merge_history WHERE id = ?", (log_id,))

            # BEST PRACTICE: Refresh ALL face cluster counts after undo
            # Ensures counts reflect actual database state after restoration
            print(f"[undo_last_face_merge] Refreshing ALL face cluster counts for project {project_id}...")
            cur.execute(
                """
                UPDATE face_branch_reps
                SET count = (
                    SELECT COUNT(DISTINCT fc.image_path)
                    FROM face_crops fc
                    JOIN project_images pi ON fc.image_path = pi.image_path
                                          AND fc.project_id = pi.project_id
                                          AND fc.branch_key = pi.branch_key
                    WHERE fc.project_id = face_branch_reps.project_id
                      AND fc.branch_key = face_branch_reps.branch_key
                )
                WHERE project_id = ?
                """,
                [project_id],
            )
            refreshed_count = cur.rowcount
            print(f"[undo_last_face_merge] ✓ Refreshed counts for {refreshed_count} face clusters")

            conn.commit()

            return {
                "faces": faces_restored,
                "images": images_restored,
                "clusters": len(branch_keys),
            }


    def get_face_merge_suggestions(
        self,
        project_id: int,
        max_pairs: int = 20,
        threshold: float = 0.45,
        min_count: int = 3,
    ):
        """
        Compute 'smart' merge suggestions by comparing centroid embeddings
        between face clusters.

        Returns a list of dicts:
            {
                "a_branch", "b_branch",
                "a_label", "b_label",
                "a_count", "b_count",
                "distance": float
            }
        """
        import math
        import array

        reps = self.get_face_branch_reps(project_id)
        if not reps:
            return []

        # Only consider clusters with a centroid and at least min_count faces
        filtered = [
            r for r in reps
            if r.get("centroid_bytes") is not None
            and (r.get("count") or 0) >= min_count
        ]
        if len(filtered) < 2:
            return []

        # Decode centroid bytes into arrays of float32
        vecs = []
        for r in filtered:
            centroid_bytes = r["centroid_bytes"]
            arr = array.array("f")
            try:
                arr.frombytes(centroid_bytes)
            except Exception:
                # If decoding fails for some row, just skip it
                continue
            vecs.append(
                (
                    r["branch_key"],
                    r.get("label") or r["branch_key"],
                    r.get("count") or 0,
                    arr,
                )
            )

        suggestions: list[dict] = []
        n = len(vecs)
        for i in range(n):
            key_i, label_i, cnt_i, v_i = vecs[i]
            for j in range(i + 1, n):
                key_j, label_j, cnt_j, v_j = vecs[j]
                if len(v_i) != len(v_j) or not v_i:
                    continue
                # Euclidean distance
                dist = math.sqrt(
                    sum((v_i[k] - v_j[k]) ** 2 for k in range(len(v_i)))
                )
                if dist <= threshold:
                    suggestions.append(
                        {
                            "a_branch": key_i,
                            "b_branch": key_j,
                            "a_label": label_i,
                            "b_label": label_j,
                            "a_count": cnt_i,
                            "b_count": cnt_j,
                            "distance": dist,
                        }
                    )

        suggestions.sort(key=lambda d: d["distance"])
        return suggestions[:max_pairs]

    # =========================================================================
    # MOBILE DEVICE TRACKING METHODS (Phase 1: Device Registry)
    # =========================================================================

    def register_device(self, device_id: str, device_name: str, device_type: str,
                       serial_number: str = None, volume_guid: str = None,
                       mount_point: str = None) -> None:
        """
        Register a new mobile device or update existing device's last_seen.

        Args:
            device_id: Unique device identifier
            device_name: Human-readable name
            device_type: Type ("android", "ios", "camera", "usb", "sd_card")
            serial_number: Physical serial (optional)
            volume_guid: Volume GUID for Windows (optional)
            mount_point: Current mount path (optional)
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # Check if device exists
            cur.execute("""
                SELECT device_id FROM mobile_devices WHERE device_id = ?
            """, (device_id,))

            if cur.fetchone():
                # Update existing device
                cur.execute("""
                    UPDATE mobile_devices
                    SET device_name = ?,
                        device_type = ?,
                        serial_number = ?,
                        volume_guid = ?,
                        mount_point = ?,
                        last_seen = CURRENT_TIMESTAMP
                    WHERE device_id = ?
                """, (device_name, device_type, serial_number, volume_guid,
                      mount_point, device_id))
            else:
                # Insert new device
                cur.execute("""
                    INSERT INTO mobile_devices (
                        device_id, device_name, device_type,
                        serial_number, volume_guid, mount_point,
                        last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (device_id, device_name, device_type, serial_number,
                      volume_guid, mount_point))

            conn.commit()

    def get_device(self, device_id: str) -> dict | None:
        """Get device information by ID."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT device_id, device_name, device_type, serial_number,
                       volume_guid, mount_point, first_seen, last_seen,
                       last_import_session, total_imports,
                       total_photos_imported, total_videos_imported, notes
                FROM mobile_devices
                WHERE device_id = ?
            """, (device_id,))

            row = cur.fetchone()
            if not row:
                return None

            return {
                "device_id": row[0],
                "device_name": row[1],
                "device_type": row[2],
                "serial_number": row[3],
                "volume_guid": row[4],
                "mount_point": row[5],
                "first_seen": row[6],
                "last_seen": row[7],
                "last_import_session": row[8],
                "total_imports": row[9],
                "total_photos_imported": row[10],
                "total_videos_imported": row[11],
                "notes": row[12]
            }

    def list_all_devices(self, device_type: str = None) -> list[dict]:
        """
        List all registered devices, optionally filtered by type.

        Args:
            device_type: Filter by type (optional)

        Returns:
            List of device dictionaries, sorted by last_seen (newest first)
        """
        with self._connect() as conn:
            cur = conn.cursor()

            if device_type:
                cur.execute("""
                    SELECT device_id, device_name, device_type,
                           first_seen, last_seen, total_imports,
                           total_photos_imported, total_videos_imported
                    FROM mobile_devices
                    WHERE device_type = ?
                    ORDER BY last_seen DESC
                """, (device_type,))
            else:
                cur.execute("""
                    SELECT device_id, device_name, device_type,
                           first_seen, last_seen, total_imports,
                           total_photos_imported, total_videos_imported
                    FROM mobile_devices
                    ORDER BY last_seen DESC
                """)

            devices = []
            for row in cur.fetchall():
                devices.append({
                    "device_id": row[0],
                    "device_name": row[1],
                    "device_type": row[2],
                    "first_seen": row[3],
                    "last_seen": row[4],
                    "total_imports": row[5],
                    "total_photos_imported": row[6],
                    "total_videos_imported": row[7]
                })

            return devices

    def create_import_session(self, device_id: str, project_id: int,
                             import_type: str = "manual") -> int:
        """
        Create a new import session.

        Args:
            device_id: Source device ID
            project_id: Target project ID
            import_type: Type of import ("manual", "auto", "incremental")

        Returns:
            New session ID
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO import_sessions (
                    device_id, project_id, import_type, status
                ) VALUES (?, ?, ?, 'in_progress')
            """, (device_id, project_id, import_type))

            session_id = cur.lastrowid
            conn.commit()
            return session_id

    def complete_import_session(self, session_id: int, photos_imported: int = 0,
                               videos_imported: int = 0, duplicates_skipped: int = 0,
                               bytes_imported: int = 0, duration_seconds: int = None,
                               error_message: str = None) -> None:
        """
        Mark import session as completed and update device statistics.

        Args:
            session_id: Import session ID
            photos_imported: Number of photos imported
            videos_imported: Number of videos imported
            duplicates_skipped: Number of duplicates skipped
            bytes_imported: Total bytes imported
            duration_seconds: Import duration
            error_message: Error message if failed
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # Update session
            status = "completed" if not error_message else "failed"
            cur.execute("""
                UPDATE import_sessions
                SET status = ?,
                    photos_imported = ?,
                    videos_imported = ?,
                    duplicates_skipped = ?,
                    bytes_imported = ?,
                    duration_seconds = ?,
                    error_message = ?
                WHERE id = ?
            """, (status, photos_imported, videos_imported, duplicates_skipped,
                  bytes_imported, duration_seconds, error_message, session_id))

            # Get device_id for this session
            cur.execute("""
                SELECT device_id FROM import_sessions WHERE id = ?
            """, (session_id,))
            row = cur.fetchone()
            if row:
                device_id = row[0]

                # Update device statistics
                cur.execute("""
                    UPDATE mobile_devices
                    SET last_import_session = ?,
                        total_imports = total_imports + 1,
                        total_photos_imported = total_photos_imported + ?,
                        total_videos_imported = total_videos_imported + ?
                    WHERE device_id = ?
                """, (session_id, photos_imported, videos_imported, device_id))

            conn.commit()

    def get_device_import_history(self, device_id: str, limit: int = 10) -> list[dict]:
        """
        Get import history for a device.

        Args:
            device_id: Device ID
            limit: Maximum number of sessions to return

        Returns:
            List of import session dictionaries
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, project_id, import_date, import_type,
                       photos_imported, videos_imported, duplicates_skipped,
                       status, duration_seconds
                FROM import_sessions
                WHERE device_id = ?
                ORDER BY import_date DESC
                LIMIT ?
            """, (device_id, limit))

            sessions = []
            for row in cur.fetchall():
                sessions.append({
                    "session_id": row[0],
                    "project_id": row[1],
                    "import_date": row[2],
                    "import_type": row[3],
                    "photos_imported": row[4],
                    "videos_imported": row[5],
                    "duplicates_skipped": row[6],
                    "status": row[7],
                    "duration_seconds": row[8]
                })

            return sessions

    def track_device_file(self, device_id: str, device_path: str,
                         device_folder: str, file_hash: str, file_size: int,
                         file_mtime: str, import_session_id: int = None,
                         local_photo_id: int = None, local_video_id: int = None) -> None:
        """
        Track a file seen on a device.

        Args:
            device_id: Device ID
            device_path: Path on device
            device_folder: Folder name (Camera, Screenshots, etc.)
            file_hash: SHA256 hash
            file_size: File size in bytes
            file_mtime: Modification time
            import_session_id: Import session ID (if imported)
            local_photo_id: Local photo ID (if imported)
            local_video_id: Local video ID (if imported)
        """
        with self._connect() as conn:
            cur = conn.cursor()

            # Check if file already tracked
            cur.execute("""
                SELECT id, import_status FROM device_files
                WHERE device_id = ? AND device_path = ?
            """, (device_id, device_path))

            existing = cur.fetchone()

            if existing:
                # Update existing entry
                file_id, current_status = existing
                new_status = "imported" if (local_photo_id or local_video_id) else current_status

                cur.execute("""
                    UPDATE device_files
                    SET last_seen = CURRENT_TIMESTAMP,
                        import_status = ?,
                        local_photo_id = ?,
                        local_video_id = ?,
                        import_session_id = ?
                    WHERE id = ?
                """, (new_status, local_photo_id, local_video_id,
                      import_session_id, file_id))
            else:
                # Insert new entry
                import_status = "imported" if (local_photo_id or local_video_id) else "new"

                cur.execute("""
                    INSERT INTO device_files (
                        device_id, device_path, device_folder, file_hash,
                        file_size, file_mtime, import_status,
                        local_photo_id, local_video_id, import_session_id,
                        last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (device_id, device_path, device_folder, file_hash,
                      file_size, file_mtime, import_status,
                      local_photo_id, local_video_id, import_session_id))

            conn.commit()

    def get_new_files_on_device(self, device_id: str) -> list[dict]:
        """
        Get list of files on device that haven't been imported yet.

        Args:
            device_id: Device ID

        Returns:
            List of file dictionaries with import_status='new'
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT device_path, device_folder, file_hash, file_size, file_mtime
                FROM device_files
                WHERE device_id = ? AND import_status = 'new'
                ORDER BY device_path
            """, (device_id,))

            files = []
            for row in cur.fetchall():
                files.append({
                    "device_path": row[0],
                    "device_folder": row[1],
                    "file_hash": row[2],
                    "file_size": row[3],
                    "file_mtime": row[4]
                })

            return files

    # =========================================================================
    # PHASE 4: AUTO-IMPORT PREFERENCES
    # =========================================================================

    def set_device_auto_import(self, device_id: str, enabled: bool, folder: str = None) -> None:
        """
        Enable or disable auto-import for a device (Phase 4).

        Args:
            device_id: Device ID
            enabled: True to enable, False to disable
            folder: Folder to auto-import from (e.g., "Camera")
        """
        with self._connect() as conn:
            if enabled:
                conn.execute("""
                    UPDATE mobile_devices
                    SET auto_import = 1,
                        auto_import_folder = ?,
                        auto_import_enabled_date = CURRENT_TIMESTAMP
                    WHERE device_id = ?
                """, (folder, device_id))
            else:
                conn.execute("""
                    UPDATE mobile_devices
                    SET auto_import = 0,
                        auto_import_folder = NULL
                    WHERE device_id = ?
                """, (device_id,))
            conn.commit()

    def get_device_auto_import_status(self, device_id: str) -> dict:
        """
        Get auto-import settings for a device (Phase 4).

        Args:
            device_id: Device ID

        Returns:
            Dict with 'enabled', 'folder', and 'last_import' keys
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT auto_import, auto_import_folder, last_auto_import
                FROM mobile_devices
                WHERE device_id = ?
            """, (device_id,))
            row = cur.fetchone()

            if row:
                return {
                    'enabled': bool(row[0]),
                    'folder': row[1],
                    'last_import': row[2]
                }

        return {'enabled': False, 'folder': None, 'last_import': None}

    def update_device_last_auto_import(self, device_id: str) -> None:
        """
        Update last auto-import timestamp (Phase 4).

        Args:
            device_id: Device ID
        """
        with self._connect() as conn:
            conn.execute("""
                UPDATE mobile_devices
                SET last_auto_import = CURRENT_TIMESTAMP
                WHERE device_id = ?
            """, (device_id,))
            conn.commit()

    def get_auto_import_devices(self) -> list[dict]:
        """
        Get all devices with auto-import enabled (Phase 4).

        Returns:
            List of device dicts with auto_import=1
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT device_id, device_name, device_type, auto_import_folder,
                       mount_point, last_auto_import
                FROM mobile_devices
                WHERE auto_import = 1
                ORDER BY device_name
            """)

            devices = []
            for row in cur.fetchall():
                devices.append({
                    'device_id': row[0],
                    'device_name': row[1],
                    'device_type': row[2],
                    'auto_import_folder': row[3],
                    'mount_point': row[4],
                    'last_auto_import': row[5]
                })

            return devices

    # --- end new methods ---------------------------------------------------------

    # =========================================================================
    # PERSON GROUPS METHODS (v10.0.0)
    # User-defined groups of people for "Together (AND)" matching
    # =========================================================================

    def create_person_group(
        self,
        project_id: int,
        name: str,
        person_ids: list,
        pinned: bool = False,
        cover_asset_path: str = None
    ) -> int:
        """
        Create a new person group.

        Args:
            project_id: Project ID
            name: Group name
            person_ids: List of branch_keys (min 2)
            pinned: Show at top of list
            cover_asset_path: Optional cover photo path

        Returns:
            group_id
        """
        import time
        now = int(time.time())

        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO person_groups (
                    project_id, name, created_at, updated_at,
                    is_pinned, cover_asset_path
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (project_id, name, now, now, 1 if pinned else 0, cover_asset_path))

            group_id = cur.lastrowid

            for branch_key in person_ids:
                conn.execute("""
                    INSERT INTO person_group_members (group_id, branch_key, added_at)
                    VALUES (?, ?, ?)
                """, (group_id, branch_key, now))

            conn.commit()
            return group_id

    def update_person_group(
        self,
        group_id: int,
        name: str = None,
        person_ids: list = None,
        pinned: bool = None,
        cover_asset_path: str = None
    ) -> bool:
        """
        Update an existing person group.

        Returns:
            True if successful
        """
        import time
        now = int(time.time())

        with self._connect() as conn:
            updates = ["updated_at = ?"]
            params = [now]

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if pinned is not None:
                updates.append("is_pinned = ?")
                params.append(1 if pinned else 0)
            if cover_asset_path is not None:
                updates.append("cover_asset_path = ?")
                params.append(cover_asset_path)

            params.append(group_id)
            conn.execute(
                f"UPDATE person_groups SET {', '.join(updates)} WHERE id = ?",
                params
            )

            if person_ids is not None:
                conn.execute(
                    "DELETE FROM person_group_members WHERE group_id = ?",
                    (group_id,)
                )
                for branch_key in person_ids:
                    conn.execute("""
                        INSERT INTO person_group_members (group_id, branch_key, added_at)
                        VALUES (?, ?, ?)
                    """, (group_id, branch_key, now))

                # Clear cached matches
                conn.execute(
                    "DELETE FROM group_asset_matches WHERE group_id = ?",
                    (group_id,)
                )

            conn.commit()
            return True

    def delete_person_group(self, group_id: int) -> bool:
        """
        Delete a person group (soft delete).

        Returns:
            True if successful
        """
        import time
        with self._connect() as conn:
            conn.execute(
                "UPDATE person_groups SET is_deleted = 1, updated_at = ? WHERE id = ?",
                (int(time.time()), group_id)
            )
            conn.commit()
            return True

    def get_person_groups(self, project_id: int) -> list:
        """
        Get all person groups for a project.

        Returns:
            List of group dicts with member info
        """
        with self._connect() as conn:
            cur = conn.execute("""
                SELECT
                    g.id,
                    g.name,
                    g.created_at,
                    g.updated_at,
                    g.last_used_at,
                    g.is_pinned,
                    g.cover_asset_path,
                    (SELECT COUNT(*) FROM person_group_members WHERE group_id = g.id) AS member_count,
                    (SELECT COUNT(*) FROM group_asset_matches WHERE group_id = g.id AND scope = 'same_photo') AS photo_count
                FROM person_groups g
                WHERE g.project_id = ? AND g.is_deleted = 0
                ORDER BY g.is_pinned DESC, g.last_used_at DESC NULLS LAST, g.name ASC
            """, (project_id,))

            groups = []
            for row in cur.fetchall():
                group_id = row[0]

                members_cur = conn.execute("""
                    SELECT
                        pgm.branch_key,
                        COALESCE(fbr.label, pgm.branch_key) AS display_name,
                        fbr.rep_thumb_png
                    FROM person_group_members pgm
                    LEFT JOIN face_branch_reps fbr
                        ON fbr.project_id = ? AND fbr.branch_key = pgm.branch_key
                    WHERE pgm.group_id = ?
                """, (project_id, group_id))

                members = []
                for m_row in members_cur.fetchall():
                    members.append({
                        "branch_key": m_row[0],
                        "display_name": m_row[1],
                        "rep_thumb_png": m_row[2]
                    })

                groups.append({
                    "id": row[0],
                    "name": row[1],
                    "created_at": row[2],
                    "updated_at": row[3],
                    "last_used_at": row[4],
                    "pinned": bool(row[5]),
                    "cover_asset_path": row[6],
                    "member_count": row[7],
                    "photo_count": row[8],
                    "members": members
                })

            return groups

    def get_group_members(self, group_id: int) -> list:
        """
        Get member branch_keys for a group.

        Returns:
            List of branch_key strings
        """
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT branch_key FROM person_group_members WHERE group_id = ?",
                (group_id,)
            )
            return [row[0] for row in cur.fetchall()]

    def get_group_photos(
        self,
        group_id: int,
        scope: str = "same_photo",
        page: int = 0,
        page_size: int = 100
    ) -> dict:
        """
        Get photos matching a group (from cache).

        Args:
            group_id: Group to query
            scope: "same_photo" or "event_window"
            page: Page number (0-indexed)
            page_size: Results per page

        Returns:
            Dict with photo_ids, paths, total_count
        """
        import time
        with self._connect() as conn:
            # Update last_used_at
            conn.execute(
                "UPDATE person_groups SET last_used_at = ? WHERE id = ?",
                (int(time.time()), group_id)
            )

            # Get total count
            cur = conn.execute("""
                SELECT COUNT(*) FROM group_asset_matches
                WHERE group_id = ? AND scope = ?
            """, (group_id, scope))
            total_count = cur.fetchone()[0]

            # Get paginated results
            cur = conn.execute("""
                SELECT gam.photo_id, pm.path
                FROM group_asset_matches gam
                JOIN photo_metadata pm ON pm.id = gam.photo_id
                WHERE gam.group_id = ? AND gam.scope = ?
                ORDER BY pm.created_ts DESC
                LIMIT ? OFFSET ?
            """, (group_id, scope, page_size, page * page_size))

            photo_ids = []
            photo_paths = []
            for row in cur.fetchall():
                photo_ids.append(row[0])
                photo_paths.append(row[1])

            conn.commit()

            return {
                "group_id": group_id,
                "scope": scope,
                "photo_ids": photo_ids,
                "photo_paths": photo_paths,
                "total_count": total_count,
                "page": page,
                "page_size": page_size
            }

    def compute_group_matches(
        self,
        project_id: int,
        group_id: int,
        scope: str = "same_photo"
    ) -> list:
        """
        Compute group matches using live query.

        "Together (AND)" query: finds photos where all group members appear.

        Args:
            project_id: Project ID
            group_id: Group to compute
            scope: "same_photo" or "event_window"

        Returns:
            List of matching photo_ids
        """
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT person_id FROM person_group_members WHERE group_id = ?",
                (group_id,)
            )
            member_ids = [row[0] for row in cur.fetchall()]

            if len(member_ids) < 2:
                return []

            member_count = len(member_ids)

            if scope == "same_photo":
                cur = conn.execute(f"""
                    WITH members AS (
                        SELECT person_id FROM person_group_members WHERE group_id = ?
                    )
                    SELECT pm.id
                    FROM photo_metadata pm
                    JOIN face_crops fc ON fc.image_path = pm.path AND fc.project_id = pm.project_id
                    JOIN members m ON m.person_id = fc.branch_key
                    WHERE pm.project_id = ?
                    GROUP BY pm.id
                    HAVING COUNT(DISTINCT fc.branch_key) = ?
                    ORDER BY pm.created_ts DESC
                """, (group_id, project_id, member_count))

            elif scope == "event_window":
                cur = conn.execute(f"""
                    WITH members AS (
                        SELECT person_id FROM person_group_members WHERE group_id = ?
                    ),
                    events_with_all_members AS (
                        SELECT pe.event_id
                        FROM face_crops fc
                        JOIN photo_metadata pm ON pm.path = fc.image_path AND pm.project_id = fc.project_id
                        JOIN photo_events pe ON pe.project_id = pm.project_id AND pe.photo_id = pm.id
                        JOIN members m ON m.person_id = fc.branch_key
                        WHERE fc.project_id = ?
                        GROUP BY pe.event_id
                        HAVING COUNT(DISTINCT fc.branch_key) = ?
                    )
                    SELECT pm.id
                    FROM photo_events pe
                    JOIN events_with_all_members e ON e.event_id = pe.event_id
                    JOIN photo_metadata pm ON pm.id = pe.photo_id
                    WHERE pe.project_id = ?
                    ORDER BY pm.created_ts DESC
                """, (group_id, project_id, member_count, project_id))

            else:
                return []

            return [row[0] for row in cur.fetchall()]

    def store_group_matches(
        self,
        project_id: int,
        group_id: int,
        photo_ids: list,
        scope: str = "same_photo"
    ) -> int:
        """
        Store computed group matches in cache.

        Args:
            project_id: Project ID
            group_id: Group ID
            photo_ids: List of matching photo IDs
            scope: Match scope

        Returns:
            Number of matches stored
        """
        import time
        now = int(time.time())

        with self._connect() as conn:
            # Clear old cache
            conn.execute(
                "DELETE FROM group_asset_matches WHERE group_id = ? AND scope = ?",
                (group_id, scope)
            )

            # Insert new matches
            for photo_id in photo_ids:
                conn.execute("""
                    INSERT INTO group_asset_matches (
                        project_id, group_id, scope, photo_id, computed_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (project_id, group_id, scope, photo_id, now))

            conn.commit()
            return len(photo_ids)

    # --- end person groups methods -----------------------------------------------


# --- Compatibility shims for legacy imports ---
_db = ReferenceDB()

def get_all_references(): return _db.get_all_references()
def log_match_result(filename, label, score, match_mode=None): return _db.log_match_result(filename, label, score, match_mode)
def get_threshold_for_label(label): return _db.get_threshold_for_label(label)
def purge_missing_references(): return _db.purge_missing_references()

if __name__ == "__main__":
    # Simple CLI: ensure metadata columns or show stats
    ap = argparse.ArgumentParser()
    ap.add_argument("--migrate-metadata", action="store_true", help="Ensure metadata_status & metadata_fail_count columns exist")
    ap.add_argument("--show-meta-stats", action="store_true", help="Print metadata backfill stats")
    args = ap.parse_args()
    db = ReferenceDB()
    if args.migrate_metadata:
        db.ensure_metadata_columns()
        print("metadata columns ensured (if not present)")
    if args.show_meta_stats:
        print(json.dumps(db.get_metadata_stats(), indent=2))
        
# =========================================================
#  Module-level Migration Helpers (manual, from menu)
# =========================================================
def _connect_for_path(db_path: str | None):
    import sqlite3 as _sqlite3, os as _os
    path = db_path or ReferenceDB().db_file  # <-- unify defaul
    con = _sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    return con

def ensure_created_date_fields(db_path: str | None = None) -> None:
    """Add created_ts / created_date / created_year + indexes, idempotent."""
    with _connect_for_path(db_path) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        cols = {row[1] for row in cur.fetchall()}
        if "created_ts" not in cols:
            try: cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER"); 
            except Exception: pass
        if "created_date" not in cols:
            try: cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_date TEXT");
            except Exception: pass
        if "created_year" not in cols:
            try: cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER");
            except Exception: pass
        cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_year  ON photo_metadata(created_year)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_date  ON photo_metadata(created_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_created_ts    ON photo_metadata(created_ts)")
        conn.commit()

def count_missing_created_fields(db_path: str | None = None) -> int:
    """How many rows still need created_* filled. If cols missing, return total rows."""
    with _connect_for_path(db_path) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        cols = {row[1] for row in cur.fetchall()}
        if not {"created_ts","created_date","created_year"}.issubset(cols):
            cur.execute("SELECT COUNT(*) FROM photo_metadata")
            return cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*)
            FROM photo_metadata
            WHERE created_ts IS NULL OR created_date IS NULL OR created_year IS NULL
        """)
        return cur.fetchone()[0]

def single_pass_backfill_created_fields(db_path: str | None = None, chunk_size: int = 1000) -> int:
    """
    Fill created_* for up to chunk_size rows. Returns number of rows updated this pass.
    Call repeatedly until it returns 0.
    """
    import datetime as _dt
    def parse_any(s: str | None):
        if not s: return None
        fmts = [
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%d.%m.%Y %H:%M:%S",
            "%Y-%m-%d",
        ]
        for f in fmts:
            try: return _dt.datetime.strptime(s, f)
            except Exception: pass
        return None
    with _connect_for_path(db_path) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        cols = {row[1] for row in cur.fetchall()}
        if not {"created_ts","created_date","created_year"}.issubset(cols):
            return 0
        cur.execute("""
            SELECT path, date_taken, modified
            FROM photo_metadata
            WHERE created_ts IS NULL OR created_date IS NULL OR created_year IS NULL
            LIMIT ?
        """, (chunk_size,))
        rows = cur.fetchall()
        if not rows:
            return 0
        updates = []
        for path, date_taken, modified in rows:
            t = parse_any(date_taken) or parse_any(modified)
            if not t:
                updates.append((None, None, None, path))
            else:
                ts = int(t.timestamp())
                dstr = t.strftime("%Y-%m-%d")
                updates.append((ts, dstr, int(dstr[:4]), path))
        cur.executemany("""
            UPDATE photo_metadata
            SET created_ts = ?, created_date = ?, created_year = ?
            WHERE path = ?
        """, updates)
        conn.commit()
        return len(updates)
        
