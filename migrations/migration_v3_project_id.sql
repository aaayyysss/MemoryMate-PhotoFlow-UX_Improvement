-- Migration v3.0.0: Add project_id to photo_folders and photo_metadata
-- Date: 2025-11-07
-- Purpose: Enable clean project isolation at the schema level
--
-- This migration adds project_id as a first-class column to core tables,
-- eliminating the need for complex JOINs through project_images.

-- ============================================================================
-- BACKUP OLD TABLES
-- ============================================================================

-- Rename existing tables for backup
ALTER TABLE photo_folders RENAME TO photo_folders_v2_backup;
ALTER TABLE photo_metadata RENAME TO photo_metadata_v2_backup;

-- ============================================================================
-- CREATE NEW SCHEMA (v3.0.0)
-- ============================================================================

-- Photo folders with project_id
CREATE TABLE photo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    parent_id INTEGER NULL,
    project_id INTEGER NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)  -- Same path can exist in multiple projects
);

-- Photo metadata with project_id
CREATE TABLE photo_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
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
    FOREIGN KEY(folder_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)  -- Same photo can exist in multiple projects
);

-- ============================================================================
-- CREATE INDEXES
-- ============================================================================

-- Folder indexes
CREATE INDEX idx_photo_folders_project ON photo_folders(project_id);
CREATE INDEX idx_photo_folders_parent ON photo_folders(parent_id);
CREATE INDEX idx_photo_folders_path ON photo_folders(path);

-- Photo metadata indexes (project_id for filtering)
CREATE INDEX idx_photo_metadata_project ON photo_metadata(project_id);

-- Photo metadata indexes (existing)
CREATE INDEX idx_meta_folder ON photo_metadata(folder_id);
CREATE INDEX idx_meta_date ON photo_metadata(date_taken);
CREATE INDEX idx_meta_modified ON photo_metadata(modified);
CREATE INDEX idx_meta_updated ON photo_metadata(updated_at);
CREATE INDEX idx_meta_status ON photo_metadata(metadata_status);

-- Photo metadata indexes (created_* columns for date-based browsing)
CREATE INDEX idx_photo_created_year ON photo_metadata(created_year);
CREATE INDEX idx_photo_created_date ON photo_metadata(created_date);
CREATE INDEX idx_photo_created_ts ON photo_metadata(created_ts);

-- ============================================================================
-- MIGRATE DATA FROM OLD SCHEMA
-- ============================================================================

-- Migrate photo_folders
-- Strategy: Use project_images to determine which project owns each folder
-- Fallback to project_id=1 if no association found
INSERT INTO photo_folders (id, name, path, parent_id, project_id)
SELECT DISTINCT
    pf.id,
    pf.name,
    pf.path,
    pf.parent_id,
    COALESCE(
        (SELECT DISTINCT pi.project_id
         FROM photo_metadata_v2_backup pm
         JOIN project_images pi ON pm.path = pi.image_path
         WHERE pm.folder_id = pf.id
         LIMIT 1),
        1  -- Default to first project if no association
    ) as project_id
FROM photo_folders_v2_backup pf;

-- Migrate photo_metadata
-- Strategy: Use project_images to determine project ownership
-- Fallback to project_id=1 if no association found
INSERT INTO photo_metadata (
    id, path, folder_id, project_id, size_kb, modified,
    width, height, embedding, date_taken, tags, updated_at,
    metadata_status, metadata_fail_count, created_ts, created_date, created_year
)
SELECT
    pm.id,
    pm.path,
    pm.folder_id,
    COALESCE(pi.project_id, 1) as project_id,
    pm.size_kb,
    pm.modified,
    pm.width,
    pm.height,
    pm.embedding,
    pm.date_taken,
    pm.tags,
    pm.updated_at,
    pm.metadata_status,
    pm.metadata_fail_count,
    pm.created_ts,
    pm.created_date,
    pm.created_year
FROM photo_metadata_v2_backup pm
LEFT JOIN project_images pi ON pm.path = pi.image_path
WHERE pi.id = (
    SELECT MIN(id) FROM project_images WHERE image_path = pm.path
);

-- ============================================================================
-- VERIFICATION QUERIES (Run these to verify migration)
-- ============================================================================

-- Check folder counts
-- SELECT 'Folders migrated' as check_name,
--        COUNT(*) as new_count,
--        (SELECT COUNT(*) FROM photo_folders_v2_backup) as old_count
-- FROM photo_folders;

-- Check photo counts
-- SELECT 'Photos migrated' as check_name,
--        COUNT(*) as new_count,
--        (SELECT COUNT(*) FROM photo_metadata_v2_backup) as old_count
-- FROM photo_metadata;

-- Check project distribution
-- SELECT 'Folders by project' as check_name, project_id, COUNT(*) as count
-- FROM photo_folders
-- GROUP BY project_id;

-- SELECT 'Photos by project' as check_name, project_id, COUNT(*) as count
-- FROM photo_metadata
-- GROUP BY project_id;

-- ============================================================================
-- CLEANUP (Optional - Only after verification)
-- ============================================================================

-- Once you've verified the migration worked, uncomment these to drop backups:
-- DROP TABLE photo_folders_v2_backup;
-- DROP TABLE photo_metadata_v2_backup;

-- ============================================================================
-- UPDATE SCHEMA VERSION
-- ============================================================================

INSERT INTO schema_version (version, description)
VALUES ('3.0.0', 'Added project_id to photo_folders and photo_metadata for clean project isolation');

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

-- NEXT STEPS:
-- 1. Verify data integrity using the verification queries above
-- 2. Update application code to use project_id in queries
-- 3. Test with fresh scans to ensure new data uses project_id correctly
-- 4. After confirming everything works, drop the backup tables
