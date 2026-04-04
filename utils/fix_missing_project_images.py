#!/usr/bin/env python3
"""
Fix for missing project_images entries after merge operations.

This script restores project_images entries for photos that exist in face_crops
but are missing from project_images table (deleted during merge as "duplicates").

Usage:
    python3 -m utils.fix_missing_project_images [--project-id PROJECT_ID] [--branch-key BRANCH_KEY] [--dry-run]
"""

import argparse
import sqlite3
from pathlib import Path


def fix_missing_project_images(db_path: str, project_id: int = None, branch_key: str = None, dry_run: bool = True):
    """
    Restore missing project_images entries for face_crops photos.

    Args:
        db_path: Path to reference_data.db
        project_id: Optional project ID to limit fix (None = all projects)
        branch_key: Optional branch key to limit fix (None = all branches)
        dry_run: If True, only show what would be fixed without actually fixing

    Returns:
        Number of entries that would be (or were) restored
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print("=" * 80)
    print("FIX MISSING PROJECT_IMAGES ENTRIES")
    print("=" * 80)
    print(f"Database: {db_path}")
    print(f"Project ID: {project_id if project_id else 'ALL'}")
    print(f"Branch Key: {branch_key if branch_key else 'ALL'}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will fix)'}")
    print()

    # Build query to find face_crops without project_images
    where_clauses = []
    params = []

    if project_id is not None:
        where_clauses.append("fc.project_id = ?")
        params.append(project_id)

    if branch_key is not None:
        where_clauses.append("fc.branch_key = ?")
        params.append(branch_key)

    where_clause = " AND " + " AND ".join(where_clauses) if where_clauses else ""

    query = f"""
        SELECT fc.project_id, fc.branch_key, fc.image_path
        FROM face_crops fc
        LEFT JOIN project_images pi ON fc.image_path = pi.image_path
                                    AND pi.project_id = fc.project_id
                                    AND pi.branch_key = fc.branch_key
        WHERE pi.image_path IS NULL{where_clause}
        ORDER BY fc.project_id, fc.branch_key, fc.image_path
    """

    cur.execute(query, params)
    missing = cur.fetchall()

    if not missing:
        print("✅ No missing project_images entries found!")
        print("   All face_crops photos have corresponding project_images links.")
        conn.close()
        return 0

    print(f"Found {len(missing)} face_crops entries missing from project_images:")
    print()

    # Group by project and branch
    by_branch = {}
    for proj_id, br_key, path in missing:
        key = (proj_id, br_key)
        if key not in by_branch:
            by_branch[key] = []
        by_branch[key].append(path)

    for (proj_id, br_key), paths in sorted(by_branch.items()):
        print(f"Project {proj_id} / {br_key}: {len(paths)} missing entries")
        for path in paths[:3]:  # Show first 3
            print(f"  - {path}")
        if len(paths) > 3:
            print(f"  ... and {len(paths) - 3} more")
        print()

    if dry_run:
        print("=" * 80)
        print("DRY RUN MODE - No changes made")
        print("=" * 80)
        print()
        print(f"Would restore {len(missing)} missing project_images entries.")
        print("Run with --no-dry-run to actually restore these entries.")
    else:
        print("=" * 80)
        print("RESTORING MISSING ENTRIES")
        print("=" * 80)
        print()

        # Insert missing entries
        for proj_id, br_key, path in missing:
            cur.execute("""
                INSERT OR IGNORE INTO project_images (project_id, branch_key, image_path)
                VALUES (?, ?, ?)
            """, (proj_id, br_key, path))

        conn.commit()

        print(f"✅ Restored {len(missing)} missing project_images entries.")
        print()
        print("VERIFICATION:")
        print("1. Check people grid - counts should now match")
        print("2. Filter by affected branch_key - all photos should appear")
        print("3. Run diagnostic script to verify no orphans remain")

    conn.close()
    return len(missing)


def main():
    parser = argparse.ArgumentParser(description="Fix missing project_images entries")
    parser.add_argument(
        "--db-path",
        default="reference_data.db",
        help="Path to reference_data.db (default: reference_data.db)"
    )
    parser.add_argument(
        "--project-id",
        type=int,
        help="Limit fix to specific project ID"
    )
    parser.add_argument(
        "--branch-key",
        help="Limit fix to specific branch key (e.g., face_003)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be fixed without actually fixing (default: True)"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually fix missing entries"
    )

    args = parser.parse_args()

    # Check if database exists
    if not Path(args.db_path).exists():
        print(f"❌ ERROR: Database not found at {args.db_path}")
        return 1

    count = fix_missing_project_images(args.db_path, args.project_id, args.branch_key, args.dry_run)

    if count == 0:
        print("✅ No fixes needed - database is healthy!")
    elif args.dry_run:
        print(f"\n⚠️  Run with --no-dry-run to restore {count} missing entries")
    else:
        print(f"\n✅ Successfully restored {count} missing entries!")

    return 0


if __name__ == "__main__":
    exit(main())
