-- ============================================================================
-- Migration v6.0.0: Visual Semantics Infrastructure - CREATE TABLES
-- Date: 2025-12-29
-- Description: New tables for embeddings, captions, tags, detections, events, ML jobs
--
-- IMPORTANT: This file contains ONLY new table creation (idempotent).
--            Column additions to existing tables are handled in Python migration.
-- ============================================================================

-- ============================================================================
-- 1. MODEL REGISTRY
-- ============================================================================
CREATE TABLE IF NOT EXISTS ml_model (
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                -- 'clip', 'siglip', 'blip2', 'groundingdino', 'insightface'
  variant TEXT NOT NULL,             -- 'ViT-B/32', 'base', 'large', 'buffalo_l'
  version TEXT NOT NULL,             -- semantic version or git hash
  task TEXT NOT NULL,                -- 'visual_embedding', 'face_embedding', 'captioning', 'detection'
  runtime TEXT NOT NULL,             -- 'cpu', 'gpu_local', 'gpu_remote'
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(name, variant, version, task)
);

-- ============================================================================
-- 2. VISUAL EMBEDDINGS (separate from face embeddings in photo_metadata)
-- ============================================================================
CREATE TABLE IF NOT EXISTS photo_embedding (
  photo_id INTEGER NOT NULL,
  model_id INTEGER NOT NULL,
  embedding_type TEXT NOT NULL,      -- 'visual_semantic' (CLIP), 'face' (InsightFace), 'object'
  dim INTEGER NOT NULL,              -- Dimensionality (512, 768, etc.)
  embedding BLOB NOT NULL,           -- float32 bytes (dim * 4 bytes)
  norm REAL,                         -- Optional L2 norm for cosine similarity

  -- Freshness tracking (Critical Change #1)
  source_photo_hash TEXT,            -- SHA256 of source image at computation time
  source_photo_mtime TEXT,           -- mtime of source file at computation time
  artifact_version TEXT DEFAULT '1.0', -- Recompute trigger (bump when algo changes)

  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  computed_at TEXT DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY(photo_id, model_id, embedding_type),
  FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
  FOREIGN KEY(model_id) REFERENCES ml_model(model_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_photo_embedding_model ON photo_embedding(model_id, embedding_type);
CREATE INDEX IF NOT EXISTS idx_photo_embedding_photo ON photo_embedding(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_embedding_hash ON photo_embedding(source_photo_hash);

-- ============================================================================
-- 3. CAPTIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS photo_caption (
  photo_id INTEGER NOT NULL,
  model_id INTEGER NOT NULL,
  caption TEXT NOT NULL,
  confidence REAL,

  -- Freshness tracking
  source_photo_hash TEXT,
  artifact_version TEXT DEFAULT '1.0',

  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(photo_id, model_id),
  FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
  FOREIGN KEY(model_id) REFERENCES ml_model(model_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_photo_caption_photo ON photo_caption(photo_id);
CREATE INDEX IF NOT EXISTS idx_photo_caption_model ON photo_caption(model_id);

-- ============================================================================
-- 4. TAG SUGGESTIONS (ML-generated, not yet confirmed)
-- ============================================================================
CREATE TABLE IF NOT EXISTS photo_tag_suggestion (
  photo_id INTEGER NOT NULL,
  model_id INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,           -- References existing tags table
  score REAL NOT NULL,               -- Confidence score (0.0 to 1.0)
  evidence_type TEXT,                -- 'caption' | 'embedding' | 'detection'
  evidence_ref TEXT,                 -- Optional pointer (e.g., detection_id)
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY(photo_id, model_id, tag_id),
  FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
  FOREIGN KEY(model_id) REFERENCES ml_model(model_id) ON DELETE RESTRICT,
  FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_tag_sugg_tag ON photo_tag_suggestion(tag_id, score);
CREATE INDEX IF NOT EXISTS idx_tag_sugg_photo ON photo_tag_suggestion(photo_id);
CREATE INDEX IF NOT EXISTS idx_tag_sugg_model ON photo_tag_suggestion(model_id);

-- ============================================================================
-- 5. TAG DECISIONS (audit trail + suppression)
-- ============================================================================
CREATE TABLE IF NOT EXISTS photo_tag_decision (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  photo_id INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,
  decision TEXT NOT NULL,            -- 'confirm' | 'reject'
  source_model_id INTEGER,           -- Which model suggested it
  source_score REAL,                 -- Original suggestion score
  note TEXT,                         -- Optional user note

  -- Suppression mechanism (Critical Change #6)
  suppress_until_ts TEXT,            -- If reject: don't re-suggest until this timestamp

  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
  FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE RESTRICT,
  FOREIGN KEY(source_model_id) REFERENCES ml_model(model_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tag_decision_photo ON photo_tag_decision(photo_id, tag_id);
CREATE INDEX IF NOT EXISTS idx_tag_decision_suppress ON photo_tag_decision(photo_id, tag_id, suppress_until_ts)
  WHERE decision = 'reject' AND suppress_until_ts IS NOT NULL;

-- ============================================================================
-- 6. OBJECT DETECTIONS (Phase 3 - evidence for tags/search)
-- ============================================================================
CREATE TABLE IF NOT EXISTS photo_detection (
  detection_id INTEGER PRIMARY KEY AUTOINCREMENT,
  photo_id INTEGER NOT NULL,
  model_id INTEGER NOT NULL,
  label TEXT NOT NULL,               -- Open-vocab label (e.g., "river", "mountain")
  score REAL NOT NULL,               -- Detection confidence
  x REAL NOT NULL,                   -- Bounding box (normalized 0..1)
  y REAL NOT NULL,
  w REAL NOT NULL,
  h REAL NOT NULL,
  mask_path TEXT,                    -- Optional path to segmentation mask

  -- Freshness tracking
  source_photo_hash TEXT,
  artifact_version TEXT DEFAULT '1.0',

  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
  FOREIGN KEY(model_id) REFERENCES ml_model(model_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_detection_photo ON photo_detection(photo_id);
CREATE INDEX IF NOT EXISTS idx_detection_label ON photo_detection(label, score);
CREATE INDEX IF NOT EXISTS idx_detection_model ON photo_detection(model_id);

-- ============================================================================
-- 7. EVENTS (Phase 4 - weddings, trips, "days")
-- ============================================================================
CREATE TABLE IF NOT EXISTS event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                         -- 'wedding', 'trip', 'birthday', 'day'
  start_ts TEXT,
  end_ts TEXT,
  confidence REAL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_photo (
  event_id INTEGER NOT NULL,
  photo_id INTEGER NOT NULL,
  role TEXT,                         -- 'member' | 'cover' | 'key_moment'
  score REAL,                        -- Relevance score
  PRIMARY KEY(event_id, photo_id),
  FOREIGN KEY(event_id) REFERENCES event(event_id) ON DELETE CASCADE,
  FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_photo_photo ON event_photo(photo_id);
CREATE INDEX IF NOT EXISTS idx_event_kind ON event(kind);

-- ============================================================================
-- 8. JOB ORCHESTRATION (persistent, restart-safe queue)
-- ============================================================================
CREATE TABLE IF NOT EXISTS ml_job (
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                -- 'embed', 'caption', 'tag_suggest', 'detect', 'event_propose'
  status TEXT NOT NULL,              -- 'queued','running','paused','failed','done','canceled'
  priority INTEGER DEFAULT 0,
  backend TEXT NOT NULL,             -- 'cpu' | 'gpu_local' | 'gpu_remote'
  payload_json TEXT NOT NULL,        -- Job parameters (photo_ids, model_id, etc.)
  progress REAL DEFAULT 0.0,         -- 0.0 to 1.0
  error TEXT,                        -- Error message if failed

  -- Crash recovery (Critical Change #2)
  worker_id TEXT,                    -- Which worker claimed this job
  lease_expires_at TEXT,             -- Lease expiration timestamp
  last_heartbeat_at TEXT,            -- Last heartbeat timestamp

  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT,
  project_id INTEGER,                -- Link to project for isolation

  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ml_job_status ON ml_job(status, kind);
CREATE INDEX IF NOT EXISTS idx_ml_job_project ON ml_job(project_id);
CREATE INDEX IF NOT EXISTS idx_ml_job_lease ON ml_job(status, lease_expires_at)
  WHERE status = 'running';

-- ============================================================================
-- SEED DATA
-- ============================================================================

-- Register existing InsightFace model (backward compatibility)
INSERT OR IGNORE INTO ml_model (name, variant, version, task, runtime)
VALUES ('insightface', 'buffalo_l', '1.0', 'face_embedding', 'cpu');

-- ============================================================================
-- SCHEMA VERSION
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('6.0.0', 'Visual semantics infrastructure: embeddings, captions, tags, detections, events, ML jobs');
