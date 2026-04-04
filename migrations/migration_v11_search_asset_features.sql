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
    has_gps INTEGER DEFAULT 0,        -- 0/1
    face_count INTEGER DEFAULT 0,
    is_screenshot INTEGER DEFAULT 0,  -- 0/1
    flag TEXT,                         -- pick / reject / null
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
