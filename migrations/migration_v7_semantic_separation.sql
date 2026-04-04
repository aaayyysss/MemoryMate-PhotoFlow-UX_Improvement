-- ============================================================================
-- Migration v7.0.0: Semantic/Face Separation - Clean Architecture
-- Date: 2026-01-05
-- Description: Proper separation of semantic and face embedding systems
--
-- RATIONALE:
-- Face recognition and semantic understanding are TWO ORTHOGONAL AI systems.
-- They must share photos, not meaning.
--
-- This migration creates clean separation:
-- - Face embeddings → face_crops table (already exists)
-- - Semantic embeddings → semantic_embeddings table (NEW, this migration)
-- - No mixing of concerns
-- ============================================================================

-- ============================================================================
-- 1. SEMANTIC EMBEDDINGS (Separate from face embeddings)
-- ============================================================================
CREATE TABLE IF NOT EXISTS semantic_embeddings (
    photo_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,               -- 'clip-vit-b32', 'clip-vit-l14', 'siglip-base'
    embedding BLOB NOT NULL,           -- float32 bytes (normalized)
    dim INTEGER NOT NULL,              -- 512, 768, etc.
    norm REAL NOT NULL,                -- Precomputed L2 norm (should be 1.0 for normalized)

    -- Freshness tracking
    source_photo_hash TEXT,            -- SHA256 of source image
    source_photo_mtime TEXT,           -- mtime at computation time
    artifact_version TEXT DEFAULT '1.0',  -- Recompute trigger

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE
);

-- ============================================================================
-- 2. SEMANTIC INDEX METADATA (For FAISS handoff later)
-- ============================================================================
CREATE TABLE IF NOT EXISTS semantic_index_meta (
    model TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    total_vectors INTEGER NOT NULL,
    last_rebuild TIMESTAMP,
    notes TEXT
);

-- ============================================================================
-- 3. PERFORMANCE INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_semantic_model
ON semantic_embeddings(model);

CREATE INDEX IF NOT EXISTS idx_semantic_hash
ON semantic_embeddings(source_photo_hash);

CREATE INDEX IF NOT EXISTS idx_semantic_computed
ON semantic_embeddings(computed_at);

-- ============================================================================
-- 4. MIGRATION NOTES
-- ============================================================================

-- This migration does NOT touch existing tables:
-- - photo_embedding (v6.0.0) remains for backward compatibility if needed
-- - face_crops (existing) remains the canonical face embedding storage
-- - No data migration required (fresh start)

-- Going forward:
-- - Face workflows use face_crops.embedding
-- - Semantic workflows use semantic_embeddings.embedding
-- - NEVER mix the two

-- ============================================================================
-- 5. SCHEMA VERSION
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description)
VALUES ('7.0.0', 'Semantic/face separation: semantic_embeddings table for clean architectural separation');
