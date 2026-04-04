# repository/migrations.py
# Version 2.0.2 dated 20260214
# Database migration system for schema upgrades
# FIX: Check if DB file exists before opening in read-only mode (prevents error on fresh DB)
#
# This module handles schema migrations from legacy databases to current version.
# It provides safe, incremental schema upgrades with full tracking and validation.

"""
Database migration system for MemoryMate-PhotoFlow.

This module provides:
- Migration definitions for each schema version
- Migration detection (current version vs target version)
- Safe migration application with transaction support
- Migration history tracking
- Pre-flight checks and validation

Usage:
    from repository.migrations import MigrationManager

    manager = MigrationManager(db_connection)

    # Check if migrations are needed
    if manager.needs_migration():
        print(f"Migrations needed: {manager.get_pending_migrations()}")

        # Apply all pending migrations
        results = manager.apply_all_migrations()

        # Check results
        for result in results:
            print(f"Applied: {result['version']} - {result['status']}")
"""

import sqlite3
from typing import List, Dict, Any, Optional, Tuple
from logging_config import get_logger
from datetime import datetime

logger = get_logger(__name__)


# =============================================================================
# MIGRATION DEFINITIONS
# =============================================================================

class Migration:
    """
    Base class for database migrations.

    Each migration represents an atomic schema change with:
    - Version number (semantic versioning)
    - Description of changes
    - SQL to apply the migration
    - Optional rollback SQL
    - Pre-flight checks
    """

    def __init__(self, version: str, description: str, sql: str, rollback_sql: str = ""):
        self.version = version
        self.description = description
        self.sql = sql
        self.rollback_sql = rollback_sql

    def __repr__(self):
        return f"Migration(version={self.version}, description={self.description})"


# Migration from legacy (no schema_version table) to v1.5.0 (add created_* columns)
MIGRATION_1_5_0 = Migration(
    version="1.5.0",
    description="Add created_ts, created_date, created_year columns and indexes",
    sql="""
-- Check if columns already exist (idempotent)
-- SQLite doesn't have IF NOT EXISTS for ALTER TABLE, so we'll handle in code

-- Add created_ts column
-- ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER;

-- Add created_date column
-- ALTER TABLE photo_metadata ADD COLUMN created_date TEXT;

-- Add created_year column
-- ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER;

-- Create indexes for date-based queries
CREATE INDEX IF NOT EXISTS idx_photo_created_year ON photo_metadata(created_year);
CREATE INDEX IF NOT EXISTS idx_photo_created_date ON photo_metadata(created_date);
CREATE INDEX IF NOT EXISTS idx_photo_created_ts ON photo_metadata(created_ts);
""",
    rollback_sql="""
-- Cannot drop columns in SQLite without recreating table
-- This is intentionally left empty as column drops are complex
-- Manual rollback required if needed
"""
)

# Migration to v2.0.0 (full repository layer schema)
MIGRATION_2_0_0 = Migration(
    version="2.0.0",
    description="Repository layer schema with schema_version tracking",
    sql="""
-- This migration brings legacy databases up to v2.0.0 standard

-- 1. Create schema_version table if it doesn't exist
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- 2. Ensure all tables exist (idempotent)
-- Reference images for face recognition
CREATE TABLE IF NOT EXISTS reference_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    matched_label TEXT,
    confidence REAL,
    match_mode TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reference_labels (
    label TEXT PRIMARY KEY,
    folder_path TEXT NOT NULL,
    threshold REAL DEFAULT 0.3
);

-- Projects and branches
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    folder TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key)
);

CREATE TABLE IF NOT EXISTS project_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT,
    image_path TEXT NOT NULL,
    label TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Face recognition tables
CREATE TABLE IF NOT EXISTS face_crops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    image_path TEXT NOT NULL,
    crop_path TEXT NOT NULL,
    is_representative INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key, crop_path)
);

CREATE TABLE IF NOT EXISTS face_branch_reps (
    project_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    label TEXT,
    count INTEGER DEFAULT 0,
    centroid BLOB,
    rep_path TEXT,
    rep_thumb_png BLOB,
    PRIMARY KEY (project_id, branch_key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS face_merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    target_branch TEXT NOT NULL,
    source_branches TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS export_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    branch_key TEXT,
    photo_count INTEGER,
    source_paths TEXT,
    dest_paths TEXT,
    dest_folder TEXT,
    timestamp TEXT
);

-- Tags (normalized structure)
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (photo_id, tag_id),
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- 3. Create all indexes (idempotent)
CREATE INDEX IF NOT EXISTS idx_face_crops_proj ON face_crops(project_id);
CREATE INDEX IF NOT EXISTS idx_face_crops_proj_branch ON face_crops(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_face_crops_proj_rep ON face_crops(project_id, is_representative);
CREATE INDEX IF NOT EXISTS idx_fbreps_proj ON face_branch_reps(project_id);
CREATE INDEX IF NOT EXISTS idx_fbreps_proj_branch ON face_branch_reps(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_face_merge_history_proj ON face_merge_history(project_id);
CREATE INDEX IF NOT EXISTS idx_branches_project ON branches(project_id);
CREATE INDEX IF NOT EXISTS idx_branches_key ON branches(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_projimgs_project ON project_images(project_id);
CREATE INDEX IF NOT EXISTS idx_projimgs_branch ON project_images(project_id, branch_key);
CREATE INDEX IF NOT EXISTS idx_projimgs_path ON project_images(image_path);
CREATE INDEX IF NOT EXISTS idx_meta_date ON photo_metadata(date_taken);
CREATE INDEX IF NOT EXISTS idx_meta_modified ON photo_metadata(modified);
CREATE INDEX IF NOT EXISTS idx_meta_updated ON photo_metadata(updated_at);
CREATE INDEX IF NOT EXISTS idx_meta_folder ON photo_metadata(folder_id);
CREATE INDEX IF NOT EXISTS idx_meta_status ON photo_metadata(metadata_status);
CREATE INDEX IF NOT EXISTS idx_photo_created_year ON photo_metadata(created_year);
CREATE INDEX IF NOT EXISTS idx_photo_created_date ON photo_metadata(created_date);
CREATE INDEX IF NOT EXISTS idx_photo_created_ts ON photo_metadata(created_ts);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);

-- 4. Record migration
INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('2.0.0', 'Repository layer schema with full migration', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v3.0.0 (add project_id for project isolation)
MIGRATION_3_0_0 = Migration(
    version="3.0.0",
    description="Add project_id to photo_folders and photo_metadata for clean project isolation",
    sql="""
-- This migration adds project_id columns to photo_folders and photo_metadata
-- for proper project isolation at the schema level

-- 1. Add project_id columns with default value of 1 (first project)
--    Note: ALTER TABLE will be handled in code (see _add_project_id_columns_if_missing)

-- 2. Create indexes for project_id (if they don't exist yet)
CREATE INDEX IF NOT EXISTS idx_photo_folders_project ON photo_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project ON photo_metadata(project_id);

-- 3. Ensure default project exists
INSERT OR IGNORE INTO projects (id, name, folder, mode, created_at)
VALUES (1, 'Default Project', '', 'date', CURRENT_TIMESTAMP);

-- 4. Record migration
INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('3.0.0', 'Added project_id to photo_folders and photo_metadata for clean project isolation', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v4.0.0 (add file_hash for duplicate detection during device imports)
MIGRATION_4_0_0 = Migration(
    version="4.0.0",
    description="Add file_hash column for duplicate detection during device imports",
    sql="""
-- This migration adds file_hash column to photo_metadata for duplicate detection
-- during mobile device imports (prevents importing same photo twice)

-- Note: ALTER TABLE will be handled in code (see _add_file_hash_column_if_missing)

-- Create index for faster duplicate detection
CREATE INDEX IF NOT EXISTS idx_photo_metadata_hash ON photo_metadata(file_hash);

-- Record migration
INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('4.0.0', 'Added file_hash column for duplicate detection during device imports', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v6.0.0 (ML infrastructure: ml_job, embeddings, captions, etc.)
MIGRATION_6_0_0 = Migration(
    version="6.0.0",
    description="ML infrastructure: ml_job, ml_model, photo_embedding, captions, detections, events",
    sql="""
-- Migration v6.0.0 handled by migration_v6_visual_semantics.py
-- This placeholder ensures version tracking works with MigrationManager

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('6.0.0', 'ML infrastructure: ml_job, ml_model, photo_embedding, captions, detections, events', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v7.0.0 (Semantic embeddings separation)
MIGRATION_7_0_0 = Migration(
    version="7.0.0",
    description="Semantic embeddings separation: semantic_embeddings, semantic_index_meta",
    sql="""
-- Migration v7.0.0 handled by migration_v7_semantic_separation.sql
-- This placeholder ensures version tracking works with MigrationManager

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('7.0.0', 'Semantic embeddings separation: semantic_embeddings, semantic_index_meta', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v8.0.0 (Asset-centric duplicate model + stacks)
MIGRATION_8_0_0 = Migration(
    version="8.0.0",
    description="Asset-centric duplicates model + stacks: media_asset, media_instance, media_stack",
    sql="""
-- Migration v8.0.0 handled by migration_v8_media_assets_and_stacks.sql
-- This placeholder ensures version tracking works with MigrationManager

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('8.0.0', 'Asset-centric duplicates model + stacks: media_asset, media_instance, media_stack', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)

# Migration to v9.0.0 (Add photo_count column to photo_folders)
MIGRATION_9_0_0 = Migration(
    version="9.0.0",
    description="Add photo_count column to photo_folders table",
    sql="""
-- Add photo_count column to photo_folders table
-- SQLite doesn't fail if column already exists when using IF NOT EXISTS workaround

-- Try to add the column (will fail silently if already exists)
-- We'll use a pragma check to see if the column exists first

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('9.0.0', 'Add photo_count column to photo_folders table', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v9.1.0 (Add semantic_model column to projects)
MIGRATION_9_1_0 = Migration(
    version="9.1.0",
    description="Project canonical semantic model: projects.semantic_model for embedding consistency",
    sql="""
-- Migration v9.1.0 handled by migration_v9_1_semantic_model.py
-- This placeholder ensures version tracking works with MigrationManager

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('9.1.0', 'Project canonical semantic model: projects.semantic_model for embedding consistency', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v9.2.0 (Add GPS columns to photo_metadata)
MIGRATION_9_2_0 = Migration(
    version="9.2.0",
    description="Add GPS columns to photo_metadata for location-based browsing",
    sql="""
-- Migration v9.2.0: Add GPS columns for location-based browsing
-- Note: ALTER TABLE is handled in _add_gps_columns_if_missing()

-- Create partial index for GPS queries (fast location lookups)
CREATE INDEX IF NOT EXISTS idx_photo_metadata_gps ON photo_metadata(project_id, gps_latitude, gps_longitude)
    WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL;

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('9.2.0', 'Add GPS columns to photo_metadata for location-based browsing', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v9.3.0 (Add image_content_hash for pixel-based staleness detection)
MIGRATION_9_3_0 = Migration(
    version="9.3.0",
    description="Add image_content_hash for pixel-based embedding staleness detection",
    sql="""
-- Migration v9.3.0: Add image_content_hash for pixel-based staleness detection
-- This uses perceptual hash (dHash) which is resilient to metadata-only changes
-- Replaces mtime-based staleness detection that caused unnecessary re-embedding on EXIF edits
-- Note: ALTER TABLE is handled in _add_image_content_hash_column_if_missing()

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('9.3.0', 'Add image_content_hash for pixel-based embedding staleness detection', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration to v9.4.0 (Add metadata editing fields for Lightroom-style workflow)
MIGRATION_9_4_0 = Migration(
    version="9.4.0",
    description="Add rating, flag, title, caption for Lightroom-style metadata editing",
    sql="""
-- Migration v9.4.0: Add user-editable metadata fields
-- These fields enable non-destructive editing (DB-first approach)
-- Optional XMP sidecar export supported via MetadataEditorDock
-- Note: ALTER TABLE is handled in _add_metadata_editing_columns_if_missing()

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('9.4.0', 'Add rating, flag, title, caption for Lightroom-style metadata editing', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


MIGRATION_10_0_0 = Migration(
    version="10.0.0",
    description="People Groups: person_groups, person_group_members, group_asset_matches",
    sql="""
-- Migration v10.0.0: People Groups - user-defined groups of people for co-occurrence browsing
-- Enables "show me photos where Ammar + Alya appear together" workflow

CREATE TABLE IF NOT EXISTS person_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_used_at INTEGER,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    cover_asset_path TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS person_group_members (
    group_id INTEGER NOT NULL,
    branch_key TEXT NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (group_id, branch_key),
    FOREIGN KEY (group_id) REFERENCES person_groups(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_asset_matches (
    group_id INTEGER NOT NULL,
    scope TEXT NOT NULL DEFAULT 'same_photo',
    photo_id INTEGER NOT NULL,
    event_id INTEGER,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (group_id, scope, photo_id),
    FOREIGN KEY (group_id) REFERENCES person_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_person_groups_project ON person_groups(project_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_person_groups_last_used ON person_groups(project_id, last_used_at);
CREATE INDEX IF NOT EXISTS idx_person_groups_pinned ON person_groups(project_id, is_pinned) WHERE is_pinned = 1;
CREATE INDEX IF NOT EXISTS idx_group_members_branch ON person_group_members(branch_key, group_id);
CREATE INDEX IF NOT EXISTS idx_group_asset_matches_group ON group_asset_matches(group_id, scope);
CREATE INDEX IF NOT EXISTS idx_group_asset_matches_photo ON group_asset_matches(photo_id);
CREATE INDEX IF NOT EXISTS idx_face_crops_person_photo ON face_crops(project_id, branch_key, image_path);

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('10.0.0', 'People Groups: person_groups, person_group_members, group_asset_matches', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


MIGRATION_10_1_0 = Migration(
    version="10.1.0",
    description="Smart Find custom presets: smart_find_presets",
    sql="""
-- Migration v10.1.0: Smart Find custom presets
-- User-created search presets with CLIP prompts and metadata filters

CREATE TABLE IF NOT EXISTS smart_find_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    icon TEXT DEFAULT '🔖',
    category TEXT DEFAULT 'custom',
    config_json TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_smart_find_presets_project
    ON smart_find_presets(project_id, sort_order);

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('10.1.0', 'Smart Find custom presets: smart_find_presets', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


MIGRATION_11_0_0 = Migration(
    version="11.0.0",
    description="search_asset_features: flattened search index table",
    sql="""
-- Migration v11.0.0: search_asset_features - Flattened search index table
-- Performance optimization: pre-computed search metadata per asset.
-- Avoids rebuilding metadata views from JOINs on every query.
-- Critical for scaling beyond 50k photos.

CREATE TABLE IF NOT EXISTS search_asset_features (
    path TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    media_type TEXT,
    width INTEGER,
    height INTEGER,
    has_gps INTEGER DEFAULT 0,
    face_count INTEGER DEFAULT 0,
    is_screenshot INTEGER DEFAULT 0,
    flag TEXT,
    ext TEXT,
    date_taken TEXT,
    duplicate_group_id INTEGER,
    ocr_text TEXT,
    rating INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_saf_project
    ON search_asset_features(project_id);
CREATE INDEX IF NOT EXISTS idx_saf_screenshot
    ON search_asset_features(project_id, is_screenshot) WHERE is_screenshot = 1;
CREATE INDEX IF NOT EXISTS idx_saf_faces
    ON search_asset_features(project_id, face_count) WHERE face_count > 0;
CREATE INDEX IF NOT EXISTS idx_saf_gps
    ON search_asset_features(project_id, has_gps) WHERE has_gps = 1;
CREATE INDEX IF NOT EXISTS idx_saf_flag
    ON search_asset_features(project_id, flag);
CREATE INDEX IF NOT EXISTS idx_saf_date
    ON search_asset_features(project_id, date_taken);

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('11.0.0', 'search_asset_features: flattened search index table', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


MIGRATION_12_0_0 = Migration(
    version="12.0.0",
    description="OCR pipeline: ocr_text column + FTS5 virtual table for text-in-image search",
    sql="""
-- Migration v12.0.0: OCR text extraction pipeline
-- Adds ocr_text column to photo_metadata for storing extracted text.
-- Creates ocr_fts5 FTS5 virtual table for full-text search of OCR results.
-- The column is populated by the OCR pipeline worker (EasyOCR backend).

-- NOTE: ALTER TABLE ADD COLUMN is handled in Python code below
-- (SQLite doesn't support IF NOT EXISTS for ALTER TABLE).

-- FTS5 virtual table for fast MATCH queries on OCR text
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts5 USING fts5(
    ocr_text,
    content='photo_metadata',
    content_rowid='id'
);

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('12.0.0', 'OCR pipeline: ocr_text column + FTS5 virtual table', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)

MIGRATION_12_1_0 = Migration(
    version="12.1.0",
    description="Screenshot confidence: multi-signal screenshot detection",
    sql="""
-- Migration v12.1.0: Screenshot confidence column
-- Replaces boolean is_screenshot with a confidence score (0.0 to 1.0)
-- so screenshot detection uses combined evidence instead of
-- resolution-only hard positives.

-- NOTE: ALTER TABLE ADD COLUMN is handled in Python code below.

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('12.1.0', 'Screenshot confidence: multi-signal screenshot detection', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Migration v13.0.0: Family-first hybrid retrieval tables
# Adds tables for OCR text storage, person clusters, and asset-person links
# required by the candidate builder architecture.
MIGRATION_13_0_0 = Migration(
    version="13.0.0",
    description="Family-first hybrid retrieval: OCR text, person clusters, asset-person links, search features extension",
    sql="""
-- Migration v13.0.0: Family-first hybrid retrieval tables
-- These tables support the candidate builder architecture where each
-- search family (type, people_event, scenic, pet, utility) uses
-- index-specific retrieval first, then ranking inside the candidate pool.

-- ── asset_ocr_text: Dedicated OCR text storage ──
-- Separates OCR storage from photo_metadata for cleaner indexing.
CREATE TABLE IF NOT EXISTS asset_ocr_text (
    asset_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    ocr_text TEXT,
    ocr_lang TEXT,
    ocr_confidence REAL DEFAULT 0.0,
    token_count INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_aot_project ON asset_ocr_text(project_id);
CREATE INDEX IF NOT EXISTS idx_aot_path ON asset_ocr_text(path);

-- ── asset_ocr_text_fts: FTS5 for OCR text search ──
CREATE VIRTUAL TABLE IF NOT EXISTS asset_ocr_text_fts USING fts5(
    ocr_text,
    content='asset_ocr_text',
    content_rowid='asset_id'
);

-- ── person_clusters: Named person clusters ──
CREATE TABLE IF NOT EXISTS person_clusters (
    cluster_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    display_name TEXT,
    cluster_confidence REAL DEFAULT 0.0,
    representative_asset_id INTEGER,
    face_count INTEGER DEFAULT 0,
    is_named INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_pc_project ON person_clusters(project_id);
CREATE INDEX IF NOT EXISTS idx_pc_project_named ON person_clusters(project_id, is_named);

-- ── asset_person_links: Photo-person associations ──
CREATE TABLE IF NOT EXISTS asset_person_links (
    asset_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    cluster_id INTEGER NOT NULL,
    face_count_in_asset INTEGER DEFAULT 1,
    match_confidence REAL DEFAULT 0.0,
    PRIMARY KEY (asset_id, cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_apl_project_cluster ON asset_person_links(project_id, cluster_id);
CREATE INDEX IF NOT EXISTS idx_apl_project_asset ON asset_person_links(project_id, asset_id);

-- ── search_asset_features extensions ──
-- Add new columns for candidate builder evidence (handled in Python code
-- since SQLite does not support ADD COLUMN IF NOT EXISTS).

-- ── query_history: For suggestions and analytics ──
CREATE TABLE IF NOT EXISTS query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    raw_query TEXT NOT NULL,
    normalized_query TEXT,
    family TEXT,
    result_count INTEGER,
    confidence_label TEXT,
    executed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qh_project ON query_history(project_id, executed_at);

INSERT OR REPLACE INTO schema_version (version, description, applied_at)
VALUES ('13.0.0', 'Family-first hybrid retrieval tables', CURRENT_TIMESTAMP);
""",
    rollback_sql=""
)


# Ordered list of all migrations
ALL_MIGRATIONS = [
    MIGRATION_1_5_0,
    MIGRATION_2_0_0,
    MIGRATION_3_0_0,
    MIGRATION_4_0_0,
    MIGRATION_6_0_0,
    MIGRATION_7_0_0,
    MIGRATION_8_0_0,
    MIGRATION_9_0_0,
    MIGRATION_9_1_0,
    MIGRATION_9_2_0,
    MIGRATION_9_3_0,
    MIGRATION_9_4_0,
    MIGRATION_10_0_0,
    MIGRATION_10_1_0,
    MIGRATION_11_0_0,
    MIGRATION_12_0_0,
    MIGRATION_12_1_0,
    MIGRATION_13_0_0,
]


# =============================================================================
# MIGRATION MANAGER
# =============================================================================

class MigrationManager:
    """
    Manages database schema migrations.

    Responsibilities:
    - Detect current schema version
    - Identify pending migrations
    - Apply migrations safely with transactions
    - Track migration history
    - Validate schema after migrations
    """

    def __init__(self, db_connection):
        """
        Initialize migration manager.

        Args:
            db_connection: DatabaseConnection instance
        """
        from .base_repository import DatabaseConnection
        self.db_connection: DatabaseConnection = db_connection
        self.logger = get_logger(self.__class__.__name__)

    def get_current_version(self) -> str:
        """
        Get the current schema version from the database.

        Returns:
            str: Current version (e.g., "2.0.0") or "0.0.0" if no schema exists
        """
        import os
        db_path = self.db_connection._db_path

        # CRITICAL FIX: If database file doesn't exist, return 0.0.0 immediately
        # Cannot open non-existent file in read-only mode - SQLite will fail
        if not os.path.exists(db_path):
            return "0.0.0"

        try:
            with self.db_connection.get_connection(read_only=True) as conn:
                cur = conn.cursor()

                # Check if schema_version table exists
                cur.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='schema_version'
                """)

                schema_version_result = cur.fetchone()

                if not schema_version_result:
                    # No schema_version table - this is a legacy database
                    # Check if photo_metadata exists to distinguish v0 from v1
                    cur.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='photo_metadata'
                    """)

                    photo_metadata_result = cur.fetchone()

                    if photo_metadata_result:
                        # Has tables but no versioning - legacy v1.0
                        return "1.0.0"
                    else:
                        # No tables at all - fresh database
                        return "0.0.0"

                # Get latest version from schema_version table
                # Note: We can't rely on applied_at DESC because multiple migrations
                # might be applied in the same second. Instead, get all versions
                # and find the highest one using semantic versioning.
                cur.execute("""
                    SELECT version FROM schema_version
                """)

                results = cur.fetchall()

                if not results:
                    return "0.0.0"

                # Find the highest version using semantic version comparison
                versions = [row['version'] for row in results]
                highest_version = "0.0.0"
                for v in versions:
                    if self._compare_versions(v, highest_version) > 0:
                        highest_version = v

                return highest_version

        except Exception as e:
            self.logger.error(f"Error getting current version: {e}", exc_info=True)
            return "0.0.0"

    def get_target_version(self) -> str:
        """
        Get the target schema version (latest available).

        Returns:
            str: Target version
        """
        from .schema import get_schema_version
        return get_schema_version()

    def needs_migration(self) -> bool:
        """
        Check if any migrations need to be applied.

        Returns:
            bool: True if migrations are pending
        """
        current = self.get_current_version()
        target = self.get_target_version()

        return self._compare_versions(current, target) < 0

    def get_pending_migrations(self) -> List[Migration]:
        """
        Get list of pending migrations that need to be applied.

        Returns:
            List[Migration]: Migrations to apply, in order
        """
        current = self.get_current_version()
        pending = []

        for migration in ALL_MIGRATIONS:
            if self._compare_versions(current, migration.version) < 0:
                pending.append(migration)

        return pending

    def apply_migration(self, migration: Migration) -> Dict[str, Any]:
        """
        Apply a single migration.

        Args:
            migration: Migration to apply

        Returns:
            dict: Result with status, version, duration, etc.
        """
        start_time = datetime.now()

        try:
            self.logger.info(f"Applying migration {migration.version}: {migration.description}")

            with self.db_connection.get_connection() as conn:
                # First, add any missing columns (ALTER TABLE can't be in executescript)
                if migration.version == "1.5.0":
                    self._add_created_columns_if_missing(conn)
                elif migration.version == "2.0.0":
                    self._add_created_columns_if_missing(conn)
                    self._add_metadata_columns_if_missing(conn)
                elif migration.version == "3.0.0":
                    self._add_project_id_columns_if_missing(conn)
                elif migration.version == "4.0.0":
                    self._add_file_hash_column_if_missing(conn)
                elif migration.version == "6.0.0":
                    # Apply migration v6 using migration_v6_visual_semantics.py
                    self._apply_migration_v6(conn)
                elif migration.version == "7.0.0":
                    # Apply migration v7 using migration_v7_semantic_separation.sql
                    self._apply_migration_v7(conn)
                elif migration.version == "8.0.0":
                    # Apply migration v8 using migration_v8_media_assets_and_stacks.sql
                    self._apply_migration_v8(conn)
                elif migration.version == "9.0.0":
                    # Apply migration v9: add photo_count column to photo_folders
                    self._add_photo_count_column_if_missing(conn)
                elif migration.version == "9.1.0":
                    # Apply migration v9.1: add semantic_model column to projects
                    self._apply_migration_v9_1(conn)
                elif migration.version == "9.2.0":
                    # Apply migration v9.2: add GPS columns to photo_metadata
                    self._add_gps_columns_if_missing(conn)
                elif migration.version == "9.3.0":
                    # Apply migration v9.3: add image_content_hash column
                    self._add_image_content_hash_column_if_missing(conn)
                elif migration.version == "9.4.0":
                    # Apply migration v9.4: add metadata editing columns
                    self._add_metadata_editing_columns_if_missing(conn)
                # Note: v10.0.0 migration (People Groups) has table creation in SQL
                elif migration.version == "12.0.0":
                    # Apply migration v12.0: add ocr_text column to photo_metadata
                    self._add_ocr_text_column_if_missing(conn)
                elif migration.version == "12.1.0":
                    # Apply migration v12.1: add screenshot_confidence column
                    self._add_screenshot_confidence_column_if_missing(conn)
                elif migration.version == "13.0.0":
                    # Apply migration v13.0: extend search_asset_features
                    from repository.schema import ensure_search_features_table
                    ensure_search_features_table(conn)

                # Execute migration SQL (version tracking)
                conn.executescript(migration.sql)
                conn.commit()

            duration = (datetime.now() - start_time).total_seconds()

            self.logger.info(f"✓ Migration {migration.version} applied successfully ({duration:.2f}s)")

            return {
                "status": "success",
                "version": migration.version,
                "description": migration.description,
                "duration_seconds": duration,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            self.logger.error(f"✗ Migration {migration.version} failed: {e}", exc_info=True)

            return {
                "status": "failed",
                "version": migration.version,
                "description": migration.description,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": datetime.now().isoformat()
            }

    def apply_all_migrations(self) -> List[Dict[str, Any]]:
        """
        Apply all pending migrations in order.

        Returns:
            List[dict]: Results for each migration
        """
        pending = self.get_pending_migrations()

        if not pending:
            self.logger.info("No pending migrations")
            return []

        self.logger.info(f"Applying {len(pending)} pending migrations")
        results = []

        for migration in pending:
            result = self.apply_migration(migration)
            results.append(result)

            # Stop if migration failed
            if result["status"] == "failed":
                self.logger.error(f"Migration failed, stopping at {migration.version}")
                break

        return results

    def get_migration_history(self) -> List[Dict[str, Any]]:
        """
        Get history of applied migrations.

        Returns:
            List[dict]: Migration history records
        """
        try:
            with self.db_connection.get_connection(read_only=True) as conn:
                cur = conn.cursor()

                # Check if schema_version table exists
                cur.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='schema_version'
                """)

                if not cur.fetchone():
                    return []

                cur.execute("""
                    SELECT version, description, applied_at
                    FROM schema_version
                    ORDER BY applied_at ASC
                """)

                return [
                    {
                        "version": row['version'],
                        "description": row['description'],
                        "applied_at": row['applied_at']
                    }
                    for row in cur.fetchall()
                ]

        except Exception as e:
            self.logger.error(f"Error getting migration history: {e}", exc_info=True)
            return []

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        Compare two semantic version strings.

        Args:
            v1: First version (e.g., "1.5.0")
            v2: Second version (e.g., "2.0.0")

        Returns:
            int: -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
        """
        def parse_version(v: str) -> Tuple[int, int, int]:
            parts = v.split(".")
            return (
                int(parts[0]) if len(parts) > 0 else 0,
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0
            )

        v1_parts = parse_version(v1)
        v2_parts = parse_version(v2)

        if v1_parts < v2_parts:
            return -1
        elif v1_parts > v2_parts:
            return 1
        else:
            return 0

    def _add_created_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add created_ts, created_date, created_year columns if they don't exist.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        columns = {row['name'] for row in cur.fetchall()}

        if 'created_ts' not in columns:
            self.logger.info("Adding column: created_ts")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_ts INTEGER")

        if 'created_date' not in columns:
            self.logger.info("Adding column: created_date")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_date TEXT")

        if 'created_year' not in columns:
            self.logger.info("Adding column: created_year")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN created_year INTEGER")

        conn.commit()

    def _add_metadata_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add metadata_status and metadata_fail_count columns if they don't exist.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        columns = {row['name'] for row in cur.fetchall()}

        if 'metadata_status' not in columns:
            self.logger.info("Adding column: metadata_status")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN metadata_status TEXT DEFAULT 'pending'")

        if 'metadata_fail_count' not in columns:
            self.logger.info("Adding column: metadata_fail_count")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN metadata_fail_count INTEGER DEFAULT 0")

        conn.commit()

    def _add_project_id_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add project_id columns to photo_folders and photo_metadata if they don't exist.

        This is the core of the v3.0.0 migration - adds project ownership to photos and folders.
        Existing rows will default to project_id=1 (default project).

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_folders for project_id column
        cur.execute("PRAGMA table_info(photo_folders)")
        folder_columns = {row['name'] for row in cur.fetchall()}

        if 'project_id' not in folder_columns:
            self.logger.info("Adding column photo_folders.project_id (default=1)")
            cur.execute("""
                ALTER TABLE photo_folders
                ADD COLUMN project_id INTEGER NOT NULL DEFAULT 1
            """)
            # Add foreign key constraint note: SQLite doesn't enforce FK on ALTER,
            # but new schema creation will have proper FK

        # Check photo_metadata for project_id column
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'project_id' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.project_id (default=1)")
            cur.execute("""
                ALTER TABLE photo_metadata
                ADD COLUMN project_id INTEGER NOT NULL DEFAULT 1
            """)

        conn.commit()
        self.logger.info("✓ Project ID columns added successfully")

    def _add_file_hash_column_if_missing(self, conn: sqlite3.Connection):
        """
        Add file_hash column to photo_metadata if it doesn't exist.

        This is the core of the v4.0.0 migration - adds file_hash for duplicate detection
        during mobile device imports.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_metadata for file_hash column
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'file_hash' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.file_hash")
            cur.execute("""
                ALTER TABLE photo_metadata
                ADD COLUMN file_hash TEXT
            """)

        conn.commit()
        self.logger.info("✓ File hash column added successfully")

    def _add_photo_count_column_if_missing(self, conn: sqlite3.Connection):
        """
        Add photo_count column to photo_folders if it doesn't exist.

        This is the core of the v9.0.0 migration - adds photo_count for efficient
        folder photo counting during deletions.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_folders for photo_count column
        cur.execute("PRAGMA table_info(photo_folders)")
        folder_columns = {row['name'] for row in cur.fetchall()}

        if 'photo_count' not in folder_columns:
            self.logger.info("Adding column photo_folders.photo_count")
            cur.execute("""
                ALTER TABLE photo_folders
                ADD COLUMN photo_count INTEGER DEFAULT 0
            """)

        conn.commit()
        self.logger.info("✓ Photo count column added successfully")

    def _apply_migration_v6(self, conn: sqlite3.Connection):
        """
        Apply migration v6.0.0 using migration_v6_visual_semantics.py module.

        Args:
            conn: Database connection
        """
        self.logger.info("Applying migration v6.0.0 tables...")

        try:
            from migrations import migration_v6_visual_semantics

            # Apply the migration (this handles all table creation, column additions, etc.)
            migration_v6_visual_semantics.migrate_up(conn)

            self.logger.info("✓ Migration v6.0.0 tables created successfully")

        except Exception as e:
            self.logger.error(f"Failed to apply migration v6.0.0: {e}")
            raise

    def _apply_migration_v7(self, conn: sqlite3.Connection):
        """
        Apply migration v7.0.0 using migration_v7_semantic_separation.sql file.

        Args:
            conn: Database connection
        """
        self.logger.info("Applying migration v7.0.0 tables...")

        try:
            import os
            from pathlib import Path

            # Find migration SQL file
            migrations_dir = Path(__file__).parent.parent / "migrations"
            sql_file = migrations_dir / "migration_v7_semantic_separation.sql"

            if not sql_file.exists():
                raise FileNotFoundError(f"Migration file not found: {sql_file}")

            # Read and execute SQL
            with open(sql_file, 'r') as f:
                migration_sql = f.read()

            conn.executescript(migration_sql)
            conn.commit()

            self.logger.info("✓ Migration v7.0.0 tables created successfully")

        except Exception as e:
            self.logger.error(f"Failed to apply migration v7.0.0: {e}")
            raise

    def _apply_migration_v8(self, conn: sqlite3.Connection):
        """
        Apply migration v8.0.0 using migration_v8_media_assets_and_stacks.sql file.

        Args:
            conn: Database connection
        """
        self.logger.info("Applying migration v8.0.0 tables (asset-centric duplicate model)...")

        try:
            import os
            from pathlib import Path

            # Find migration SQL file
            migrations_dir = Path(__file__).parent.parent / "migrations"
            sql_file = migrations_dir / "migration_v8_media_assets_and_stacks.sql"

            if not sql_file.exists():
                raise FileNotFoundError(f"Migration file not found: {sql_file}")

            # Read and execute SQL
            with open(sql_file, 'r') as f:
                migration_sql = f.read()

            conn.executescript(migration_sql)
            conn.commit()

            self.logger.info("✓ Migration v8.0.0 tables created successfully")
            self.logger.info("  - media_asset: unique content identity")
            self.logger.info("  - media_instance: file occurrences")
            self.logger.info("  - media_stack: grouping containers")
            self.logger.info("  - media_stack_member: stack memberships")

        except Exception as e:
            self.logger.error(f"Failed to apply migration v8.0.0: {e}")
            raise

    def _apply_migration_v9_1(self, conn: sqlite3.Connection):
        """
        Apply migration v9.1.0 using migration_v9_1_semantic_model.py module.

        Adds semantic_model column to projects table for canonical model enforcement.

        Args:
            conn: Database connection
        """
        self.logger.info("Applying migration v9.1.0 (semantic_model column)...")

        try:
            from migrations import migration_v9_1_semantic_model

            # Apply the migration
            success = migration_v9_1_semantic_model.migrate_up(conn)

            if success:
                self.logger.info("Migration v9.1.0 applied successfully")
            else:
                raise Exception("Migration v9.1.0 returned failure status")

        except Exception as e:
            self.logger.error(f"Failed to apply migration v9.1.0: {e}")
            raise

    def _add_gps_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add GPS columns to photo_metadata if they don't exist.

        This is the core of the v9.2.0 migration - adds gps_latitude, gps_longitude,
        and location_name for location-based photo browsing in the Locations sidebar.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_metadata for GPS columns
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'gps_latitude' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.gps_latitude")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_latitude REAL")

        if 'gps_longitude' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.gps_longitude")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN gps_longitude REAL")

        if 'location_name' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.location_name")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN location_name TEXT")

        conn.commit()
        self.logger.info("✓ GPS columns added successfully")

    def _add_image_content_hash_column_if_missing(self, conn: sqlite3.Connection):
        """
        Add image_content_hash column to photo_metadata if it doesn't exist.

        This is the core of the v9.3.0 migration - adds image_content_hash for
        pixel-based embedding staleness detection using perceptual hash (dHash).

        The dHash is computed from decoded pixel data and is resilient to:
        - EXIF metadata changes (GPS, date, camera settings)
        - File re-saves without pixel changes
        - Minor compression artifacts

        This replaces mtime-based staleness detection which incorrectly marked
        embeddings as stale after EXIF-only edits.

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_metadata for image_content_hash column
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'image_content_hash' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.image_content_hash")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN image_content_hash TEXT")
            conn.commit()
            self.logger.info("✓ image_content_hash column added successfully")
        else:
            self.logger.info("✓ image_content_hash column already exists")

    def _add_metadata_editing_columns_if_missing(self, conn: sqlite3.Connection):
        """
        Add Lightroom-style metadata editing columns to photo_metadata.

        This is the core of the v9.4.0 migration - adds rating, flag, title, caption
        for non-destructive metadata editing (DB-first approach).

        Columns added:
        - rating (INTEGER): 0-5 star rating
        - flag (TEXT): 'pick', 'reject', or 'none'
        - title (TEXT): User-defined title
        - caption (TEXT): User-defined description/caption

        Args:
            conn: Database connection
        """
        cur = conn.cursor()

        # Check photo_metadata columns
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        columns_to_add = [
            ('rating', 'INTEGER DEFAULT 0'),
            ('flag', "TEXT DEFAULT 'none'"),
            ('title', 'TEXT'),
            ('caption', 'TEXT'),
        ]

        for col_name, col_def in columns_to_add:
            if col_name not in metadata_columns:
                self.logger.info(f"Adding column photo_metadata.{col_name}")
                cur.execute(f"ALTER TABLE photo_metadata ADD COLUMN {col_name} {col_def}")

        conn.commit()
        self.logger.info("✓ Metadata editing columns (rating, flag, title, caption) added successfully")

    def _add_ocr_text_column_if_missing(self, conn: sqlite3.Connection):
        """
        Add ocr_text column to photo_metadata for OCR pipeline (v12.0.0).

        Args:
            conn: Database connection
        """
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photo_metadata)")
        metadata_columns = {row['name'] for row in cur.fetchall()}

        if 'ocr_text' not in metadata_columns:
            self.logger.info("Adding column photo_metadata.ocr_text")
            cur.execute("ALTER TABLE photo_metadata ADD COLUMN ocr_text TEXT")
            conn.commit()
            self.logger.info("✓ ocr_text column added successfully")
        else:
            self.logger.info("✓ ocr_text column already exists")

    def _add_screenshot_confidence_column_if_missing(self, conn: sqlite3.Connection):
        """
        Add screenshot_confidence column to search_asset_features (v12.1.0).

        Stores a 0.0–1.0 confidence score for screenshot detection,
        replacing the old boolean-only approach. is_screenshot is now
        derived from screenshot_confidence >= 0.50.
        """
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(search_asset_features)")
        columns = {row['name'] for row in cur.fetchall()}

        if 'screenshot_confidence' not in columns:
            self.logger.info("Adding column search_asset_features.screenshot_confidence")
            cur.execute(
                "ALTER TABLE search_asset_features "
                "ADD COLUMN screenshot_confidence REAL DEFAULT 0.0"
            )
            conn.commit()
            self.logger.info("✓ screenshot_confidence column added successfully")
        else:
            self.logger.info("✓ screenshot_confidence column already exists")


def get_migration_status(db_connection) -> Dict[str, Any]:
    """
    Get comprehensive migration status for a database.

    Args:
        db_connection: DatabaseConnection instance

    Returns:
        dict: Migration status information
    """
    manager = MigrationManager(db_connection)

    current = manager.get_current_version()
    target = manager.get_target_version()
    needs_migration = manager.needs_migration()
    pending = manager.get_pending_migrations()
    history = manager.get_migration_history()

    return {
        "current_version": current,
        "target_version": target,
        "needs_migration": needs_migration,
        "pending_count": len(pending),
        "pending_migrations": [
            {"version": m.version, "description": m.description}
            for m in pending
        ],
        "applied_count": len(history),
        "migration_history": history
    }
