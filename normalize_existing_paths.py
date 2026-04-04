#!/usr/bin/env python3
"""
One-time migration to normalize all existing paths in photo_metadata table.

This MUST be run before using the application after the path normalization fix.
It updates all existing paths to use forward slashes (/) instead of backslashes (\).

Usage:
    python normalize_existing_paths.py
"""

import sys
import os
from repository.photo_repository import PhotoRepository
from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


def normalize_path(path: str) -> str:
    """Normalize a file path (same logic as PhotoRepository)."""
    normalized = os.path.normpath(path)
    normalized = normalized.replace('\\', '/')
    return normalized


def main():
    """Normalize all paths in photo_metadata table."""
    print("=" * 70)
    print("Path Normalization Migration")
    print("=" * 70)
    print()
    print("This will normalize all file paths in the photo_metadata table.")
    print("Paths with backslashes (\\) will be converted to forward slashes (/).")
    print()
    print("IMPORTANT: This MUST be run once after updating to the new version.")
    print()

    try:
        # Create database connection
        db_conn = DatabaseConnection()
        photo_repo = PhotoRepository(db_conn)

        # Get all photos
        with db_conn.get_connection() as conn:
            cur = conn.cursor()

            # Get current stats
            cur.execute("SELECT COUNT(*) as total FROM photo_metadata")
            total_before = cur.fetchone()['total']

            print(f"Total photos in database: {total_before}")
            print()

            # Get all paths that need normalization
            cur.execute("SELECT id, path FROM photo_metadata")
            all_photos = cur.fetchall()

            # Find paths that need updating
            updates = []
            for row in all_photos:
                photo_id = row['id']
                original_path = row['path']
                normalized_path = normalize_path(original_path)

                if original_path != normalized_path:
                    updates.append((normalized_path, photo_id, original_path))

            if not updates:
                print("✓ All paths are already normalized - no changes needed!")
                print()
                return 0

            print(f"Found {len(updates)} paths that need normalization.")
            print()
            print("Examples of changes:")
            for i, (new_path, photo_id, old_path) in enumerate(updates[:5]):
                print(f"  {i+1}. ID {photo_id}")
                print(f"     Old: {old_path}")
                print(f"     New: {new_path}")

            if len(updates) > 5:
                print(f"  ... and {len(updates) - 5} more")
            print()

            # Ask for confirmation
            response = input("Proceed with normalization? (yes/no): ").strip().lower()
            if response not in ('yes', 'y'):
                print("Migration cancelled.")
                return 0

            print()
            print("Normalizing paths...")

            # Update paths one by one to avoid conflicts
            updated_count = 0
            skipped_count = 0

            for normalized_path, photo_id, original_path in updates:
                try:
                    # Check if normalized path already exists (would cause conflict)
                    cur.execute("SELECT id FROM photo_metadata WHERE path = ? AND id != ?",
                               (normalized_path, photo_id))
                    conflict = cur.fetchone()

                    if conflict:
                        # Duplicate exists - will be handled by cleanup script
                        skipped_count += 1
                        logger.debug(f"Skipped ID {photo_id} - normalized path already exists as ID {conflict['id']}")
                        continue

                    # Update path
                    cur.execute("UPDATE photo_metadata SET path = ? WHERE id = ?",
                               (normalized_path, photo_id))
                    updated_count += 1

                except Exception as e:
                    logger.error(f"Failed to update ID {photo_id}: {e}")
                    skipped_count += 1

            conn.commit()

            # Get final stats
            cur.execute("SELECT COUNT(*) as total FROM photo_metadata")
            total_after = cur.fetchone()['total']

            print()
            print("=" * 70)
            print("Migration Complete!")
            print("=" * 70)
            print(f"Paths normalized: {updated_count}")
            print(f"Paths skipped:    {skipped_count}")
            print(f"Total photos:     {total_after}")
            print()

            if skipped_count > 0:
                print("⚠ Some paths were skipped because normalized versions already exist.")
                print("  Run cleanup_duplicate_photos.py to remove these duplicates.")
                print()

            print("✓ Path normalization complete!")
            print()
            print("NEXT STEP: Run cleanup_duplicate_photos.py to remove any duplicates.")

            return 0

    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        print()
        print("✗ Migration failed!")
        print(f"Error: {e}")
        print()
        print("Check the logs for more details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
