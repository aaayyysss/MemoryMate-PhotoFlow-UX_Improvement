-- Migration v6.0.0: Add Auto-Import Preferences
-- Date: 2025-11-18
-- Description: Adds auto-import preferences to mobile_devices table for Phase 4

-- ============================================================================
-- Add schema version marker
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('6.0.0', 'Added auto-import preferences for mobile devices (Phase 4)');

-- ============================================================================
-- ADD AUTO-IMPORT COLUMNS TO MOBILE_DEVICES
-- ============================================================================

-- Note: SQLite doesn't have "ADD COLUMN IF NOT EXISTS", so these will fail
-- silently if columns already exist (safe to run multiple times)

PRAGMA foreign_keys=OFF;

-- Add auto-import preference columns
ALTER TABLE mobile_devices ADD COLUMN auto_import BOOLEAN DEFAULT 0;
ALTER TABLE mobile_devices ADD COLUMN auto_import_folder TEXT DEFAULT NULL;
ALTER TABLE mobile_devices ADD COLUMN last_auto_import TIMESTAMP DEFAULT NULL;
ALTER TABLE mobile_devices ADD COLUMN auto_import_enabled_date TIMESTAMP DEFAULT NULL;

PRAGMA foreign_keys=ON;

-- ============================================================================
-- CREATE INDEX FOR AUTO-IMPORT LOOKUPS
-- ============================================================================

-- Partial index for quick auto-import device lookup
CREATE INDEX IF NOT EXISTS idx_mobile_devices_auto_import
ON mobile_devices(auto_import) WHERE auto_import = 1;

-- ============================================================================
-- VERIFICATION
-- ============================================================================

-- Verify columns exist
SELECT 'auto_import column added' WHERE EXISTS (
    SELECT 1 FROM pragma_table_info('mobile_devices') WHERE name='auto_import'
);

SELECT 'auto_import_folder column added' WHERE EXISTS (
    SELECT 1 FROM pragma_table_info('mobile_devices') WHERE name='auto_import_folder'
);

SELECT 'last_auto_import column added' WHERE EXISTS (
    SELECT 1 FROM pragma_table_info('mobile_devices') WHERE name='last_auto_import'
);

SELECT 'auto_import_enabled_date column added' WHERE EXISTS (
    SELECT 1 FROM pragma_table_info('mobile_devices') WHERE name='auto_import_enabled_date'
);

-- Verify index exists
SELECT 'idx_mobile_devices_auto_import created' WHERE EXISTS (
    SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_mobile_devices_auto_import'
);

-- Show migration status
SELECT
    version,
    applied_at,
    description
FROM schema_version
WHERE version = '6.0.0';
