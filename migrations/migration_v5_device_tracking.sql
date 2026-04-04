-- Migration v5.0.0: Add Mobile Device Tracking
-- Date: 2025-11-18
-- Description: Adds device registry, import sessions, and file tracking for mobile imports

-- ============================================================================
-- Add schema version marker
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('5.0.0', 'Added mobile device tracking: devices, import sessions, and file provenance');

-- ============================================================================
-- MOBILE DEVICE TRACKING TABLES
-- ============================================================================

-- Mobile devices registry (tracks all connected devices)
CREATE TABLE IF NOT EXISTS mobile_devices (
    device_id TEXT PRIMARY KEY,           -- Unique device identifier (MTP serial, iOS UUID, Volume GUID)
    device_name TEXT NOT NULL,            -- User-friendly name ("Samsung Galaxy S22", "John's iPhone")
    device_type TEXT NOT NULL,            -- Device type: "android", "ios", "camera", "usb", "sd_card"
    serial_number TEXT,                   -- Physical serial number (if available)
    volume_guid TEXT,                     -- Volume GUID for removable storage (Windows)
    mount_point TEXT,                     -- Last known mount path ("/media/user/phone")
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When device first connected
    last_seen TIMESTAMP,                  -- Last time device was detected
    last_import_session INTEGER,          -- ID of most recent import session
    total_imports INTEGER DEFAULT 0,      -- Total number of import sessions
    total_photos_imported INTEGER DEFAULT 0,  -- Cumulative photo count
    total_videos_imported INTEGER DEFAULT 0,  -- Cumulative video count
    notes TEXT                            -- User notes about device
);

-- Import sessions (tracks each import operation)
CREATE TABLE IF NOT EXISTS import_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,              -- Which device was imported from
    project_id INTEGER NOT NULL,          -- Target project
    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    import_type TEXT DEFAULT 'manual',    -- "manual", "auto", "incremental"
    photos_imported INTEGER DEFAULT 0,
    videos_imported INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    bytes_imported INTEGER DEFAULT 0,
    duration_seconds INTEGER,
    status TEXT DEFAULT 'completed',      -- "in_progress", "completed", "partial", "failed"
    error_message TEXT,
    FOREIGN KEY (device_id) REFERENCES mobile_devices(device_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Device files (tracks all files ever seen on devices)
CREATE TABLE IF NOT EXISTS device_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,              -- Which device this file is on
    device_path TEXT NOT NULL,            -- Original path on device (e.g., "/DCIM/Camera/IMG_001.jpg")
    device_folder TEXT,                   -- Folder name on device ("Camera", "Screenshots", "WhatsApp")
    file_hash TEXT NOT NULL,              -- SHA256 hash for duplicate detection
    file_size INTEGER,                    -- File size in bytes
    file_mtime TIMESTAMP,                 -- File modification time on device
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When first detected
    last_seen TIMESTAMP,                  -- Last time seen on device
    import_status TEXT DEFAULT 'new',     -- "new", "imported", "skipped", "deleted"
    local_photo_id INTEGER,               -- Link to photo_metadata.id (if imported)
    local_video_id INTEGER,               -- Link to video_metadata.id (if imported)
    import_session_id INTEGER,            -- Which session imported this file
    FOREIGN KEY (device_id) REFERENCES mobile_devices(device_id) ON DELETE CASCADE,
    FOREIGN KEY (import_session_id) REFERENCES import_sessions(id) ON DELETE SET NULL,
    FOREIGN KEY (local_photo_id) REFERENCES photo_metadata(id) ON DELETE SET NULL,
    FOREIGN KEY (local_video_id) REFERENCES video_metadata(id) ON DELETE SET NULL,
    UNIQUE(device_id, device_path)
);

-- ============================================================================
-- CREATE INDEXES FOR PERFORMANCE
-- ============================================================================

-- Mobile device indexes
CREATE INDEX IF NOT EXISTS idx_mobile_devices_type ON mobile_devices(device_type);
CREATE INDEX IF NOT EXISTS idx_mobile_devices_last_seen ON mobile_devices(last_seen);

-- Import session indexes
CREATE INDEX IF NOT EXISTS idx_import_sessions_device ON import_sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_import_sessions_project ON import_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_import_sessions_date ON import_sessions(import_date);
CREATE INDEX IF NOT EXISTS idx_import_sessions_status ON import_sessions(status);

-- Device file indexes
CREATE INDEX IF NOT EXISTS idx_device_files_device ON device_files(device_id);
CREATE INDEX IF NOT EXISTS idx_device_files_hash ON device_files(file_hash);
CREATE INDEX IF NOT EXISTS idx_device_files_status ON device_files(device_id, import_status);
CREATE INDEX IF NOT EXISTS idx_device_files_photo ON device_files(local_photo_id);
CREATE INDEX IF NOT EXISTS idx_device_files_video ON device_files(local_video_id);
CREATE INDEX IF NOT EXISTS idx_device_files_session ON device_files(import_session_id);
CREATE INDEX IF NOT EXISTS idx_device_files_last_seen ON device_files(device_id, last_seen);

-- ============================================================================
-- ADD DEVICE TRACKING COLUMNS TO EXISTING TABLES (Optional Enhancement)
-- ============================================================================

-- Note: These columns are optional for Phase 1 but useful for Phase 2+
-- They link imported photos/videos back to their source devices

-- Add device tracking to photo_metadata (if columns don't exist)
-- SQLite doesn't have "ADD COLUMN IF NOT EXISTS", so we use a workaround

-- Check if we need to add columns (they might already exist in newer installs)
-- This is safe to run multiple times

PRAGMA foreign_keys=OFF;

-- Try to add device_id column (will fail silently if exists)
ALTER TABLE photo_metadata ADD COLUMN device_id TEXT DEFAULT NULL;
ALTER TABLE photo_metadata ADD COLUMN device_path TEXT DEFAULT NULL;
ALTER TABLE photo_metadata ADD COLUMN device_folder TEXT DEFAULT NULL;
ALTER TABLE photo_metadata ADD COLUMN import_session_id INTEGER DEFAULT NULL;

-- Try to add device tracking to video_metadata
ALTER TABLE video_metadata ADD COLUMN device_id TEXT DEFAULT NULL;
ALTER TABLE video_metadata ADD COLUMN device_path TEXT DEFAULT NULL;
ALTER TABLE video_metadata ADD COLUMN device_folder TEXT DEFAULT NULL;
ALTER TABLE video_metadata ADD COLUMN import_session_id INTEGER DEFAULT NULL;

PRAGMA foreign_keys=ON;

-- Create indexes for device tracking columns
CREATE INDEX IF NOT EXISTS idx_photo_device ON photo_metadata(device_id);
CREATE INDEX IF NOT EXISTS idx_photo_import_session ON photo_metadata(import_session_id);
CREATE INDEX IF NOT EXISTS idx_video_device ON video_metadata(device_id);
CREATE INDEX IF NOT EXISTS idx_video_import_session ON video_metadata(import_session_id);

-- ============================================================================
-- DATA MIGRATION (Mark existing photos as "unknown device")
-- ============================================================================

-- Create a special "unknown" device for photos imported before device tracking
INSERT OR IGNORE INTO mobile_devices (
    device_id,
    device_name,
    device_type,
    first_seen,
    last_seen,
    notes
) VALUES (
    'unknown',
    'Unknown Device (Pre-Tracking)',
    'unknown',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP,
    'Photos imported before device tracking was enabled'
);

-- Mark existing photos with null device_id as coming from "unknown" device
-- This preserves data integrity and allows users to see all photos
UPDATE photo_metadata
SET device_id = 'unknown',
    device_folder = 'Unknown'
WHERE device_id IS NULL;

UPDATE video_metadata
SET device_id = 'unknown',
    device_folder = 'Unknown'
WHERE device_id IS NULL;

-- ============================================================================
-- VERIFICATION
-- ============================================================================

-- Verify tables exist
SELECT 'mobile_devices table created' WHERE EXISTS (
    SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_devices'
);

SELECT 'import_sessions table created' WHERE EXISTS (
    SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_sessions'
);

SELECT 'device_files table created' WHERE EXISTS (
    SELECT 1 FROM sqlite_master WHERE type='table' AND name='device_files'
);

-- Show migration status
SELECT
    version,
    applied_at,
    description
FROM schema_version
WHERE version = '5.0.0';
