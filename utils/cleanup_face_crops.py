#!/usr/bin/env python3
"""
Cleanup utility for orphaned face_crops entries.

This script removes face_crops entries for photos that don't exist in
photo_metadata or project_images tables, ensuring data integrity and
fixing count mismatches between people cards and grid displays.

Usage:
    python3 -m utils.cleanup_face_crops [--project-id PROJECT_ID] [--dry-run]
"""

import argparse
import sqlite3
from pathlib import Path


def cleanup_orphaned_face_crops(db_path: str, project_id: int = None, dry_run: bool = True):
    """
    Remove orphaned face_crops entries that reference photos not in photo_metadata or project_images.

    Args:
        db_path: Path to reference_data.db
        project_id: Optional project ID to limit cleanup (None = all projects)
        dry_run: If True, only show what would be deleted without actually deleting

    Returns:
        Number of entries that would be (or were) deleted
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print("=" * 80)
    print("FACE_CROPS CLEANUP UTILITY")
    print("=" * 80)
    print(f"Database: {db_path}")
    print(f"Project ID: {project_id if project_id else 'ALL'}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will delete)'}")
    print()

    # Build query to find orphaned entries
    if project_id is not None:
        query = """
            SELECT fc.id, fc.image_path, fc.branch_key, fc.project_id
            FROM face_crops fc
            LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
            LEFT JOIN project_images pi ON fc.image_path = pi.image_path AND pi.project_id = fc.project_id
            WHERE fc.project_id = ?
            AND (pm.path IS NULL OR pi.image_path IS NULL)
            ORDER BY fc.branch_key, fc.image_path
        """
        cur.execute(query, (project_id,))
    else:
        query = """
            SELECT fc.id, fc.image_path, fc.branch_key, fc.project_id
            FROM face_crops fc
            LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
            LEFT JOIN project_images pi ON fc.image_path = pi.image_path AND pi.project_id = fc.project_id
            WHERE pm.path IS NULL OR pi.image_path IS NULL
            ORDER BY fc.project_id, fc.branch_key, fc.image_path
        """
        cur.execute(query)

    orphaned = cur.fetchall()

    if not orphaned:
        print("✅ No orphaned face_crops entries found!")
        print("   All face_crops reference valid photos in photo_metadata and project_images.")
        conn.close()
        return 0

    print(f"Found {len(orphaned)} orphaned face_crops entries:")
    print()

    # Group by branch_key
    by_branch = {}
    for fc_id, path, branch_key, proj_id in orphaned:
        key = (proj_id, branch_key)
        if key not in by_branch:
            by_branch[key] = []
        by_branch[key].append((fc_id, path))

    for (proj_id, branch_key), entries in sorted(by_branch.items()):
        print(f"Project {proj_id} / {branch_key}: {len(entries)} orphaned entries")
        for fc_id, path in entries[:3]:  # Show first 3
            print(f"  - {path}")
        if len(entries) > 3:
            print(f"  ... and {len(entries) - 3} more")
        print()

    if dry_run:
        print("=" * 80)
        print("DRY RUN MODE - No changes made")
        print("=" * 80)
        print()
        print(f"Would delete {len(orphaned)} orphaned face_crops entries.")
        print("Run with --no-dry-run to actually delete these entries.")
    else:
        print("=" * 80)
        print("DELETING ORPHANED ENTRIES")
        print("=" * 80)
        print()

        # Delete orphaned entries
        orphaned_ids = [fc_id for fc_id, _, _, _ in orphaned]
        placeholders = ','.join(['?'] * len(orphaned_ids))

        cur.execute(f"DELETE FROM face_crops WHERE id IN ({placeholders})", orphaned_ids)
        conn.commit()

        print(f"✅ Deleted {len(orphaned)} orphaned face_crops entries.")
        print()
        print("NEXT STEPS:")
        print("1. Re-run face clustering to update face_branch_reps counts")
        print("2. Verify people card counts now match grid displays")

    conn.close()
    return len(orphaned)


def main():
    parser = argparse.ArgumentParser(description="Cleanup orphaned face_crops entries")
    parser.add_argument(
        "--db-path",
        default="reference_data.db",
        help="Path to reference_data.db (default: reference_data.db)"
    )
    parser.add_argument(
        "--project-id",
        type=int,
        help="Limit cleanup to specific project ID"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be deleted without actually deleting (default: True)"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually delete orphaned entries"
    )

    args = parser.parse_args()

    # Check if database exists
    if not Path(args.db_path).exists():
        print(f"❌ ERROR: Database not found at {args.db_path}")
        return 1

    cleanup_orphaned_face_crops(args.db_path, args.project_id, args.dry_run)
    return 0


if __name__ == "__main__":
    exit(main())
