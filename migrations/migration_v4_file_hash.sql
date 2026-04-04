-- Migration v4: Add file_hash column for duplicate detection during device import
-- This allows the app to skip importing photos that are already in the library

-- Add file_hash column to photo_metadata table
ALTER TABLE photo_metadata ADD COLUMN file_hash TEXT;

-- Create index for faster duplicate detection
CREATE INDEX IF NOT EXISTS idx_photo_metadata_hash ON photo_metadata(file_hash);

-- Note: file_hash will be NULL for existing photos
-- It will be populated during device imports for new photos
