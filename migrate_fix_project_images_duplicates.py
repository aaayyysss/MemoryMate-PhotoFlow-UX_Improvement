#!/usr/bin/env python3
"""
Migration: Fix duplicate entries in project_images table

ISSUE: project_images table was missing UNIQUE constraint on (project_id, branch_key, image_path)
RESULT: Each scan created duplicate entries, causing wrong photo counts

This script:
1. Creates backup
2. Removes duplicate entries (keeps the oldest ID for each unique combination)
3. Recreates project_images table with UNIQUE constraint
4. Verifies the migration

Usage:
    python migrate_fix_project_images_duplicates.py [--db path/to/db] [--dry-run]
"""

import sqlite3
import shutil
import sys
from datetime import datetime
from pathlib import Path


def migrate_database(db_path: str, dry_run: bool = False):
    """
    Fix duplicate entries in project_images table.

    Args:
        db_path: Path to database file
        dry_run: If True, show what would be done without executing
    """
    print("=" * 80)
    print("PROJECT_IMAGES DUPLICATE FIX MIGRATION")
    print("=" * 80)
    print(f"\nDatabase: {db_path}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will modify database)'}")
    print(f"Timestamp: {datetime.now()}\n")

    if not Path(db_path).exists():
        print(f"ERROR: Database file not found: {db_path}")
        return False

    if not dry_run:
        # Create backup
        backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(db_path, backup_path)
        print(f"✓ Created backup: {backup_path}\n")

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    try:
        # Step 1: Analyze current state
        print("[1] Analyzing project_images table...")
        cur.execute("SELECT COUNT(*) FROM project_images")
        total_rows = cur.fetchone()[0]
        print(f"  Total rows: {total_rows}")

        # Count duplicates
        cur.execute("""
            SELECT project_id, branch_key, image_path, COUNT(*) as count
            FROM project_images
            GROUP BY project_id, branch_key, image_path
            HAVING COUNT(*) > 1
        """)
        duplicates = cur.fetchall()
        duplicate_count = len(duplicates)
        total_duplicate_rows = sum(row['count'] - 1 for row in duplicates)

        print(f"  Unique combinations with duplicates: {duplicate_count}")
        print(f"  Total duplicate rows to remove: {total_duplicate_rows}")

        if duplicate_count > 0:
            print(f"\n  Sample duplicates:")
            for i, dup in enumerate(duplicates[:5]):
                print(f"    {i+1}. project={dup['project_id']}, branch={dup['branch_key']}, "
                      f"path={dup['image_path'][:50]}... (count={dup['count']})")

        if dry_run:
            print("\n[DRY RUN] Would execute the following:")
            print(f"  1. Delete {total_duplicate_rows} duplicate rows")
            print("  2. Create new project_images table with UNIQUE constraint")
            print("  3. Copy remaining unique entries to new table")
            print("  4. Drop old table and rename new table")
            return True

        # Step 2: Remove duplicates (keep oldest ID for each unique combination)
        print("\n[2] Removing duplicate entries...")
        if duplicate_count > 0:
            cur.execute("""
                DELETE FROM project_images
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM project_images
                    GROUP BY project_id, branch_key, image_path
                )
            """)
            deleted = cur.rowcount
            print(f"  ✓ Deleted {deleted} duplicate rows")
        else:
            print("  No duplicates found, skipping deletion")

        # Step 3: Create new table with UNIQUE constraint
        print("\n[3] Creating new project_images table with UNIQUE constraint...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_images_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                branch_key TEXT,
                image_path TEXT NOT NULL,
                label TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, branch_key, image_path)
            )
        """)
        print("  ✓ Created project_images_new table")

        # Step 4: Copy data to new table
        print("\n[4] Copying data to new table...")
        cur.execute("""
            INSERT INTO project_images_new (project_id, branch_key, image_path, label)
            SELECT project_id, branch_key, image_path, label
            FROM project_images
        """)
        copied = cur.rowcount
        print(f"  ✓ Copied {copied} rows")

        # Step 5: Replace old table with new table
        print("\n[5] Replacing old table with new table...")
        cur.execute("DROP TABLE project_images")
        cur.execute("ALTER TABLE project_images_new RENAME TO project_images")
        print("  ✓ Tables replaced")

        # Step 6: Recreate indexes
        print("\n[6] Recreating indexes...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_projimgs_project ON project_images(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_projimgs_branch ON project_images(project_id, branch_key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_projimgs_path ON project_images(image_path)")
        print("  ✓ Indexes recreated")

        # Commit changes
        db.commit()
        print("\n✓ Migration completed successfully!")

        # Step 7: Verification
        print("\n[Verification] Checking final state...")
        cur.execute("SELECT COUNT(*) FROM project_images")
        final_rows = cur.fetchone()[0]
        print(f"  Rows before: {total_rows}")
        print(f"  Rows after: {final_rows}")
        print(f"  Rows removed: {total_rows - final_rows}")

        # Check for any remaining duplicates
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT project_id, branch_key, image_path, COUNT(*) as count
                FROM project_images
                GROUP BY project_id, branch_key, image_path
                HAVING COUNT(*) > 1
            )
        """)
        remaining_duplicates = cur.fetchone()[0]
        if remaining_duplicates == 0:
            print(f"  ✓ No duplicate entries remain")
        else:
            print(f"  ✗ WARNING: {remaining_duplicates} duplicate combinations still exist!")

        return True

    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False

    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fix duplicate entries in project_images table")
    parser.add_argument("--db", default="reference_data.db", help="Path to database file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")

    args = parser.parse_args()

    success = migrate_database(args.db, dry_run=args.dry_run)

    sys.exit(0 if success else 1)
