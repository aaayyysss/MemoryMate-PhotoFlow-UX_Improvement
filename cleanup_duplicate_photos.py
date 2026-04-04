#!/usr/bin/env python3
"""
Complete cleanup script for photo path issues:
1. Normalizes all existing paths (converts backslashes to forward slashes)
2. Removes duplicate photo entries

This script handles both issues in the correct order.

Usage:
    python cleanup_duplicate_photos.py
"""

import sys
import os
from repository.photo_repository import PhotoRepository
from repository.project_repository import ProjectRepository
from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


def normalize_path(path: str) -> str:
    """Normalize a file path (same logic as PhotoRepository)."""
    normalized = os.path.normpath(path)
    normalized = normalized.replace('\\', '/')
    return normalized


def main():
    """Run the complete cleanup process."""
    print("=" * 70)
    print("Photo Metadata Complete Cleanup Tool")
    print("=" * 70)
    print()
    print("This tool will:")
    print("  1. Normalize all file paths (convert \\ to /)")
    print("  2. Remove duplicate photo entries")
    print()

    # Ask for confirmation
    response = input("Do you want to proceed? (yes/no): ").strip().lower()
    if response not in ('yes', 'y'):
        print("Cleanup cancelled.")
        return 0

    print()
    print("=" * 70)
    print("STEP 1: Normalizing Paths")
    print("=" * 70)
    print()

    try:
        # Create database connection
        db_conn = DatabaseConnection()
        photo_repo = PhotoRepository(db_conn)

        # Get stats before
        stats_before = photo_repo.get_statistics()
        total_before = stats_before['total_photos']

        print(f"Photos in database: {total_before}")
        print()

        # STEP 1: Normalize all paths
        with db_conn.get_connection() as conn:
            cur = conn.cursor()

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

            if updates:
                print(f"Found {len(updates)} paths that need normalization.")
                print("Normalizing...")

                # Update paths
                updated_count = 0
                for normalized_path, photo_id, original_path in updates:
                    try:
                        cur.execute("UPDATE photo_metadata SET path = ? WHERE id = ?",
                                   (normalized_path, photo_id))
                        updated_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to update ID {photo_id}: {e}")

                conn.commit()
                print(f"✓ Normalized {updated_count} paths")
            else:
                print("✓ All paths already normalized")

        print()
        print("=" * 70)
        print("STEP 2: Removing Duplicates")
        print("=" * 70)
        print()

        # STEP 2: Remove duplicates for each project
        project_repo = ProjectRepository(db_conn)
        all_projects = project_repo.get_all_with_details()

        deleted_count = 0
        for project in all_projects:
            project_id = project['id']
            project_name = project.get('name', f'Project {project_id}')
            count = photo_repo.cleanup_duplicate_paths(project_id)
            if count > 0:
                print(f"  - {project_name}: removed {count} duplicates")
            deleted_count += count

        # Get stats after (global)
        stats_after = photo_repo.get_statistics()  # Global stats for comparison
        total_after = stats_after['total_photos']

        print()
        print("=" * 70)
        print("Cleanup Complete!")
        print("=" * 70)
        print(f"Photos before:     {total_before}")
        print(f"Photos after:      {total_after}")
        print(f"Paths normalized:  {len(updates) if updates else 0}")
        print(f"Duplicates removed: {deleted_count}")
        print()

        if deleted_count > 0 or (updates and len(updates) > 0):
            print("✓ Successfully cleaned up photo database!")
            print()
            print("NOTE: The grid will now show the correct number of photos.")
        else:
            print("✓ No issues found - database is clean!")

        return 0

    except Exception as e:
        logger.error(f"Cleanup failed: {e}", exc_info=True)
        print()
        print("✗ Cleanup failed!")
        print(f"Error: {e}")
        print()
        print("Check the logs for more details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
