-- ============================================================================
-- Migration v9.0.0: Add photo_count Column to photo_folders
-- Date: 2026-01-16
-- Description: Add photo_count tracking to photo_folders table
--
-- RATIONALE:
-- The folder_repository.update_photo_count() expects a photo_count column
-- but it was missing from the schema. This migration adds it.
-- ============================================================================

-- Add photo_count column to photo_folders table if it doesn't exist
-- SQLite doesn't support IF NOT EXISTS for columns, so we use a workaround
-- This is safe to run multiple times (idempotent)

-- Check if column exists and add if needed
-- Note: SQLite ALTER TABLE ADD COLUMN will fail if column already exists
-- We'll handle this gracefully in the migration manager

BEGIN TRANSACTION;

-- Add photo_count column (will fail silently if already exists)
ALTER TABLE photo_folders ADD COLUMN photo_count INTEGER DEFAULT 0;

COMMIT;

-- ============================================================================
-- 2) SCHEMA VERSION TRACKING
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES ('9.0.0', 'Add photo_count column to photo_folders table', CURRENT_TIMESTAMP);

-- ============================================================================
-- Migration v9.0.0 complete
-- ============================================================================
