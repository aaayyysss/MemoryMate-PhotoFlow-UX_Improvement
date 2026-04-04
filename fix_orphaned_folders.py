#!/usr/bin/env python3
"""
Fix Orphaned Folders - Cleanup Script

Problem:
- Orphaned folders appear in sidebar with incorrect counts
- Created during rescans with case-mismatched paths on Windows
- Have parent_id = NULL when they should have proper parent folders

Solution:
- Find orphaned folders (lowercase paths, parent_id=NULL, not actual roots)
- Reassign their photos to correct parent folders
- Delete the orphaned folder entries

Usage:
    python fix_orphaned_folders.py [--dry-run] [--project-id 1]
"""

import sys
import argparse
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from schema_check import ensure_schema_ready
from reference_db import ReferenceDB

# Ensure database and schema exist before running
ensure_schema_ready(required_tables=['photo_folders', 'photo_metadata', 'projects'])

def normalize_path(path_str: str) -> str:
    """Normalize path for case-insensitive comparison on Windows."""
    if not path_str:
        return ""
    # Convert to lowercase for comparison
    return path_str.lower().replace('/', '\\')

def find_matching_folder(conn, orphan_path: str, project_id: int):
    """Find the legitimate folder that matches this orphaned path."""
    cursor = conn.cursor()
    norm_path = normalize_path(orphan_path)

    # Find folder with matching normalized path but different ID
    cursor.execute("""
        SELECT id, name, path, parent_id
        FROM photo_folders
        WHERE project_id = ? AND LOWER(REPLACE(path, '/', '\\')) = ?
        ORDER BY parent_id  -- Prefer folders with parent_id (not orphaned)
    """, (project_id, norm_path))

    results = cursor.fetchall()
    if len(results) > 1:
        # Return the non-orphaned one (has parent_id)
        for folder_id, name, path, parent_id in results:
            if parent_id is not None:
                return folder_id, name, path, parent_id

    return None

def fix_orphaned_folders(project_id: int = None, dry_run: bool = False):
    """
    Fix orphaned folders by reassigning photos and deleting duplicates.

    Args:
        project_id: Optional project ID to limit fix (None = all projects)
        dry_run: If True, only report what would be done without making changes
    """
    db = ReferenceDB()
    conn = db.get_connection()
    cursor = conn.cursor()

    # Build query to find orphaned folders
    query = """
        SELECT id, name, path, project_id
        FROM photo_folders
        WHERE parent_id IS NULL
    """
    params = []

    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)

    query += " ORDER BY id"

    cursor.execute(query, params)
    folders = cursor.fetchall()

    print(f"\n{'=' * 80}")
    print(f"Fixing Orphaned Folders...")
    print(f"{'=' * 80}\n")

    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")

    orphaned_count = 0
    fixed_count = 0
    error_count = 0
    legitimate_roots = []

    for folder_id, name, path, proj_id in folders:
        # Check if this is a legitimate root (first scan folder)
        # Legitimate roots typically have proper case and are project root folders
        is_lowercase = path and path == path.lower()

        # Check if there are photos in this folder
        cursor.execute("""
            SELECT COUNT(*) FROM photo_metadata
            WHERE folder_id = ? AND project_id = ?
        """, (folder_id, proj_id))
        photo_count = cursor.fetchone()[0]

        if not is_lowercase:
            # This is a legitimate root folder
            legitimate_roots.append((folder_id, name, path))
            print(f"✓ Folder ID {folder_id}: '{name}' is legitimate root (keeping)")
            print(f"  Path: {path}")
            print(f"  Photos: {photo_count}\n")
            continue

        # This is an orphaned folder (lowercase path, parent=NULL)
        orphaned_count += 1
        print(f"⚠️  Orphaned Folder ID {folder_id}: '{name}'")
        print(f"  Path: {path}")
        print(f"  Photos: {photo_count}")

        # Try to find matching legitimate folder
        match = find_matching_folder(conn, path, proj_id)

        if match:
            correct_folder_id, correct_name, correct_path, correct_parent = match
            print(f"  → Found match: Folder ID {correct_folder_id} ('{correct_name}')")
            print(f"     Correct path: {correct_path}")

            if not dry_run:
                # Reassign photos to correct folder
                cursor.execute("""
                    UPDATE photo_metadata
                    SET folder_id = ?
                    WHERE folder_id = ? AND project_id = ?
                """, (correct_folder_id, folder_id, proj_id))
                updated = cursor.rowcount
                print(f"  → Reassigned {updated} photos to correct folder")

                # Delete orphaned folder
                cursor.execute("DELETE FROM photo_folders WHERE id = ?", (folder_id,))
                print(f"  → Deleted orphaned folder")

                conn.commit()
                fixed_count += 1
            else:
                print(f"  → Would reassign {photo_count} photos and delete folder")
        else:
            error_count += 1
            print(f"  ❌ No matching folder found! Manual intervention needed.")

        print()

    conn.close()

    # Print summary
    print(f"{'=' * 80}")
    print(f"Fix Summary:")
    print(f"  Legitimate root folders: {len(legitimate_roots)}")
    print(f"  Orphaned folders found: {orphaned_count}")
    print(f"  Successfully fixed: {fixed_count}")
    print(f"  Errors (no match found): {error_count}")
    print(f"{'=' * 80}\n")

    if dry_run:
        print("DRY RUN completed - no changes were made")
        print("Run without --dry-run to apply changes")
    elif fixed_count > 0:
        print("✅ Orphaned folders fixed successfully!")
        print("\nNext steps:")
        print("1. Restart the app to see corrected sidebar")
        print("2. Folder counts should now be accurate")
        print("3. Orphaned 'inbox' entries should be gone")
    elif orphaned_count == 0:
        print("✅ No orphaned folders found - database is clean!")

    return fixed_count, error_count

def main():
    parser = argparse.ArgumentParser(
        description="Fix orphaned folders by reassigning photos and removing duplicates"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--project-id",
        type=int,
        default=None,
        help="Limit fix to specific project ID (default: all projects)"
    )

    args = parser.parse_args()

    try:
        fix_orphaned_folders(
            project_id=args.project_id,
            dry_run=args.dry_run
        )
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
