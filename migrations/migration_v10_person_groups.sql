-- ============================================================================
-- Migration v10.0.0: Person Groups (People → Groups sub-section)
-- Date: 2026-02-14
-- Version: 01.00.00.00
--
-- Goals:
-- 1) Enable users to create named groups of existing People (2+ persons)
-- 2) Support "Together (AND)" matching - show photos where all group members appear
-- 3) Support "Event Window" matching - members across different photos within same event
-- 4) Materialize group matches for fast retrieval (group_asset_matches cache)
--
-- Design Principles:
-- - Groups is an interaction layer on top of existing face clusters, not new clustering
-- - Groups scoped per-project only (matches existing project isolation pattern)
-- - AND logic is always default; scope controlled via preferences
-- - Incremental indexing via JobManager pattern
--
-- References:
-- - Apple Photos: People → Groups sub-area
-- - Google Photos: Face groups with combination searching
-- - Lightroom: People view with catalog indexing
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ============================================================================
-- 1) PERSON_GROUPS
--    User-defined groups of existing People (face clusters)
-- ============================================================================
CREATE TABLE IF NOT EXISTS person_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,

    -- Group metadata
    name TEXT NOT NULL,                      -- User-defined name or auto-generated ("Ammar + Alya")

    -- Timestamps
    created_at INTEGER NOT NULL,             -- Unix timestamp
    updated_at INTEGER NOT NULL,             -- Unix timestamp
    last_used_at INTEGER NULL,               -- For "Recently used" sorting

    -- UI state
    pinned INTEGER NOT NULL DEFAULT 0,       -- 1 = show at top of list
    cover_photo_id INTEGER NULL,             -- Optional custom cover photo

    -- Versioning for recomputation
    rule_version TEXT NOT NULL DEFAULT '1',  -- Algorithm version for cache invalidation

    -- Soft delete support
    is_deleted INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (cover_photo_id) REFERENCES photo_metadata(id) ON DELETE SET NULL
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_person_groups_project
ON person_groups(project_id, is_deleted);

CREATE INDEX IF NOT EXISTS idx_person_groups_last_used
ON person_groups(project_id, last_used_at);

CREATE INDEX IF NOT EXISTS idx_person_groups_pinned
ON person_groups(project_id, pinned DESC, last_used_at DESC);

-- ============================================================================
-- 2) PERSON_GROUP_MEMBERS
--    Junction table linking groups to person identities (branch_keys)
-- ============================================================================
CREATE TABLE IF NOT EXISTS person_group_members (
    group_id INTEGER NOT NULL,
    person_id TEXT NOT NULL,                 -- branch_key from face_branch_reps

    -- Metadata
    added_at INTEGER NOT NULL,               -- Unix timestamp

    -- Composite primary key
    PRIMARY KEY (group_id, person_id),

    FOREIGN KEY (group_id) REFERENCES person_groups(id) ON DELETE CASCADE
    -- Note: person_id references branch_key in face_branch_reps (not a strict FK)
);

-- Index for fast lookup by person (for updates when faces are re-clustered)
CREATE INDEX IF NOT EXISTS idx_group_members_person
ON person_group_members(person_id, group_id);

-- ============================================================================
-- 3) GROUP_ASSET_MATCHES
--    Materialized match results cache (precomputed for performance)
--    Avoids expensive JOIN queries on every grid scroll
-- ============================================================================
CREATE TABLE IF NOT EXISTS group_asset_matches (
    project_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,

    -- Match scope: 'same_photo' (all members in one photo) or 'event_window' (within event)
    scope TEXT NOT NULL CHECK(scope IN ('same_photo', 'event_window')),

    -- Matched photo
    photo_id INTEGER NOT NULL,

    -- For event_window scope: which event this match belongs to
    event_id INTEGER NULL,

    -- Match quality (future use: confidence-weighted scoring)
    match_score REAL NULL,

    -- Cache metadata
    computed_at INTEGER NOT NULL,            -- Unix timestamp

    -- Composite primary key (one entry per group+scope+photo)
    PRIMARY KEY (group_id, scope, photo_id),

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES person_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE
);

-- Index for fast group retrieval (main query path)
CREATE INDEX IF NOT EXISTS idx_group_asset_matches_group
ON group_asset_matches(project_id, group_id, scope);

-- Index for photo-centric queries (e.g., "which groups does this photo belong to?")
CREATE INDEX IF NOT EXISTS idx_group_asset_matches_photo
ON group_asset_matches(project_id, photo_id);

-- Index for event-based queries
CREATE INDEX IF NOT EXISTS idx_group_asset_matches_event
ON group_asset_matches(project_id, event_id)
WHERE event_id IS NOT NULL;

-- ============================================================================
-- 4) PHOTO_EVENTS (if not exists)
--    Time-based event clustering for "event window" scope
--    Note: Only created if not already present from previous migrations
-- ============================================================================
CREATE TABLE IF NOT EXISTS photo_events (
    project_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,

    PRIMARY KEY (project_id, photo_id),

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_photo_events_event
ON photo_events(project_id, event_id);

-- ============================================================================
-- 5) EVENTS (if not exists)
--    Event metadata (time ranges, representative photos)
-- ============================================================================
CREATE TABLE IF NOT EXISTS events (
    project_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,

    -- Time boundaries
    start_ts INTEGER NOT NULL,               -- Unix timestamp
    end_ts INTEGER NOT NULL,                 -- Unix timestamp

    -- UI metadata
    representative_photo_id INTEGER NULL,

    PRIMARY KEY (project_id, event_id),

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (representative_photo_id) REFERENCES photo_metadata(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_events_time
ON events(project_id, start_ts, end_ts);

-- ============================================================================
-- 6) GROUP_INDEX_JOBS
--    Track indexing job state for incremental updates
-- ============================================================================
CREATE TABLE IF NOT EXISTS group_index_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,

    -- Job state
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),

    -- Progress tracking
    progress REAL DEFAULT 0.0,               -- 0.0 to 1.0

    -- Scope to compute
    scope TEXT NOT NULL CHECK(scope IN ('same_photo', 'event_window', 'all')),

    -- Metadata
    created_at INTEGER NOT NULL,
    started_at INTEGER NULL,
    completed_at INTEGER NULL,
    error_message TEXT NULL,

    -- Worker claim (for concurrent safety)
    worker_id TEXT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES person_groups(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_index_jobs_status
ON group_index_jobs(project_id, status);

CREATE INDEX IF NOT EXISTS idx_group_index_jobs_group
ON group_index_jobs(group_id, status);

-- ============================================================================
-- 7) SCHEMA VERSION TRACKING
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES ('10.0.0', 'Person Groups: user-defined groups of people with same_photo/event_window matching', CURRENT_TIMESTAMP);

-- ============================================================================
-- Migration v10.0.0 complete
-- ============================================================================
