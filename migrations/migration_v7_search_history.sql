-- ============================================================================
-- Migration v7.0.0: Search History
-- Date: 2026-01-01
-- Description: Track search history for quick recall and analytics
-- ============================================================================

-- ============================================================================
-- 1. SEARCH HISTORY TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS search_history (
  search_id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_type TEXT NOT NULL,              -- 'semantic_text', 'semantic_image', 'semantic_multi', 'traditional'
  query_text TEXT,                       -- Search query text (NULL for image-only)
  query_image_path TEXT,                 -- Path to query image (NULL for text-only)

  -- Results
  result_count INTEGER DEFAULT 0,        -- Number of results found
  top_photo_ids TEXT,                    -- JSON array of top 10 photo IDs

  -- Filters (if any)
  filters_json TEXT,                     -- JSON object with filter criteria

  -- Metadata
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  execution_time_ms REAL,                -- Search execution time
  model_id INTEGER,                      -- Model used (for semantic search)

  FOREIGN KEY(model_id) REFERENCES ml_model(model_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_search_history_type ON search_history(query_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_history_created ON search_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_history_text ON search_history(query_text)
  WHERE query_text IS NOT NULL;

-- ============================================================================
-- 2. SAVED SEARCHES (User-bookmarked searches)
-- ============================================================================
CREATE TABLE IF NOT EXISTS saved_search (
  saved_search_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                    -- User-friendly name
  description TEXT,                      -- Optional description

  -- Search criteria (same as search_history)
  query_type TEXT NOT NULL,
  query_text TEXT,
  query_image_path TEXT,
  filters_json TEXT,

  -- Metadata
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  last_used_at TEXT,
  use_count INTEGER DEFAULT 0,

  UNIQUE(name)
);

CREATE INDEX IF NOT EXISTS idx_saved_search_name ON saved_search(name);
CREATE INDEX IF NOT EXISTS idx_saved_search_used ON saved_search(last_used_at DESC);

-- ============================================================================
-- SCHEMA VERSION
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('7.0.0', 'Search history and saved searches');
