-- ============================================================================
-- Migration v8.0.0: Media Asset Model + Stacks (Duplicates, Near-Duplicates, Similar)
-- Date: 2026-01-15
-- Version: 01.00.00.00
--
-- Goals:
-- 1) Introduce asset-centric model:
--    - media_asset: unique content identity (content_hash)
--    - media_instance: physical occurrences (files) linked to existing photo_metadata rows
-- 2) Introduce stack model:
--    - media_stack: grouping container (duplicate, near_duplicate, similar, burst)
--    - media_stack_member: members and scores
--
-- Notes:
-- - This migration is additive, no destructive schema changes.
-- - Backfill is handled by services (hash backfill, asset linking, stack generation).
-- - All tables include project_id for project isolation (matches existing schema patterns).
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ============================================================================
-- 1) MEDIA ASSET
--    Represents unique visual content identity
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_asset (
    asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,

    -- Cryptographic identity (SHA256 or equivalent)
    -- Maps to photo_metadata.file_hash for exact duplicates
    content_hash TEXT NOT NULL,

    -- Optional perceptual hash for near-duplicate detection (future use)
    -- Will be populated by hash backfill worker with pHash/dHash
    perceptual_hash TEXT,

    -- Chosen representative photo for previews and UI
    representative_photo_id INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Ensure one asset per content_hash per project
    UNIQUE(project_id, content_hash),

    FOREIGN KEY (representative_photo_id) REFERENCES photo_metadata(id) ON DELETE SET NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_media_asset_project_hash
ON media_asset(project_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_media_asset_representative
ON media_asset(project_id, representative_photo_id);

CREATE INDEX IF NOT EXISTS idx_media_asset_project_id
ON media_asset(project_id);

-- ============================================================================
-- 2) MEDIA INSTANCE
--    Links existing photo_metadata rows to media_asset (asset-centric model)
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_instance (
    instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,

    -- Link to asset (many instances can share one asset)
    asset_id INTEGER NOT NULL,

    -- Link to existing photo_metadata row (one-to-one mapping)
    photo_id INTEGER NOT NULL,

    -- Traceability: where did this file come from
    source_device_id TEXT,
    source_path TEXT,
    import_session_id TEXT,

    -- File metadata (denormalized for performance)
    file_size INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Ensure one instance per photo per project
    UNIQUE(project_id, photo_id),

    FOREIGN KEY (asset_id) REFERENCES media_asset(asset_id) ON DELETE CASCADE,
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Indexes for fast lookups and joins
CREATE INDEX IF NOT EXISTS idx_media_instance_project_asset
ON media_instance(project_id, asset_id);

CREATE INDEX IF NOT EXISTS idx_media_instance_project_photo
ON media_instance(project_id, photo_id);

CREATE INDEX IF NOT EXISTS idx_media_instance_project_device
ON media_instance(project_id, source_device_id);

CREATE INDEX IF NOT EXISTS idx_media_instance_asset_id
ON media_instance(asset_id);

-- ============================================================================
-- 3) MEDIA STACK
--    Grouping container for duplicates, near-duplicates, similar shots, bursts
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_stack (
    stack_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,

    -- Stack type: duplicate, near_duplicate, similar, burst
    stack_type TEXT NOT NULL CHECK(stack_type IN ('duplicate', 'near_duplicate', 'similar', 'burst')),

    -- Representative photo for preview (shown in timeline)
    representative_photo_id INTEGER,

    -- Rule version allows algorithm evolution and regeneration
    rule_version TEXT NOT NULL DEFAULT '1',

    -- Optional: track who/what created this stack (system, user, ml)
    created_by TEXT DEFAULT 'system',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (representative_photo_id) REFERENCES photo_metadata(id) ON DELETE SET NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Indexes for stack queries
CREATE INDEX IF NOT EXISTS idx_media_stack_project_type
ON media_stack(project_id, stack_type);

CREATE INDEX IF NOT EXISTS idx_media_stack_project_representative
ON media_stack(project_id, representative_photo_id);

CREATE INDEX IF NOT EXISTS idx_media_stack_project_id
ON media_stack(project_id);

CREATE INDEX IF NOT EXISTS idx_media_stack_type_version
ON media_stack(stack_type, rule_version);

-- ============================================================================
-- 4) MEDIA STACK MEMBER
--    Junction table linking photos to stacks with similarity scores
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_stack_member (
    stack_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,

    photo_id INTEGER NOT NULL,

    -- Similarity score (meaning depends on stack_type)
    -- - For exact duplicates: 1.0 (identical)
    -- - For near-duplicates: perceptual hash distance mapped to [0, 1]
    -- - For similar shots: cosine similarity from embeddings
    similarity_score REAL,

    -- Rank for stable ordering (lower = better/more representative)
    rank INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (stack_id, photo_id),

    FOREIGN KEY (stack_id) REFERENCES media_stack(stack_id) ON DELETE CASCADE,
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- Indexes for member queries
CREATE INDEX IF NOT EXISTS idx_media_stack_member_project_stack
ON media_stack_member(project_id, stack_id);

CREATE INDEX IF NOT EXISTS idx_media_stack_member_project_photo
ON media_stack_member(project_id, photo_id);

CREATE INDEX IF NOT EXISTS idx_media_stack_member_stack_id
ON media_stack_member(stack_id);

CREATE INDEX IF NOT EXISTS idx_media_stack_member_photo_id
ON media_stack_member(photo_id);

-- ============================================================================
-- 5) MEDIA STACK META (optional parameters for debugging and auditability)
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_stack_meta (
    stack_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,

    -- JSON string with parameters used to build this stack
    -- Example: {"time_window_sec": 10, "similarity_threshold": 0.92, "min_stack_size": 3}
    params_json TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (stack_id) REFERENCES media_stack(stack_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_media_stack_meta_project_id
ON media_stack_meta(project_id);

-- ============================================================================
-- 6) SCHEMA VERSION TRACKING
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES ('8.0.0', 'Asset-centric duplicate model + stack grouping (media_asset, media_instance, media_stack)', CURRENT_TIMESTAMP);

-- ============================================================================
-- Migration v8.0.0 complete
-- ============================================================================
