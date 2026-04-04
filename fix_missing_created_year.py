#!/usr/bin/env python3
"""
Fix Missing created_year Fields - Backfill Script

Problem:
- 81% of photos (9,486 out of 11,783) are missing created_year
- These photos ALL have date_taken or modified dates
- This causes the "By Date" tree to show incorrect counts

Solution:
- Parse date_taken or modified for each photo
- Extract year and populate created_year, created_date, created_ts
- Use the same logic as metadata_service.py

Usage:
    python fix_missing_created_year.py [--dry-run] [--project-id 1]
"""

import sys
import argparse
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from schema_check import ensure_schema_ready
from reference_db import ReferenceDB
from services.metadata_service import MetadataService

# Ensure database and schema exist before running
ensure_schema_ready(required_tables=['photo_metadata', 'projects'])

def backfill_created_year(project_id: int = None, dry_run: bool = False):
    """
    Backfill missing created_year fields from existing date_taken/modified dates.

    Args:
        project_id: Optional project ID to limit backfill (None = all projects)
        dry_run: If True, only report what would be done without making changes
    """
    db = ReferenceDB()
    metadata_service = MetadataService()

    # Build query
    query = """
        SELECT id, path, date_taken, modified, project_id
        FROM photo_metadata
        WHERE created_year IS NULL
    """
    params = []

    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)

    # Get all photos missing created_year
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    print(f"\n{'=' * 80}")
    print(f"Backfilling created_year for {len(rows)} photos...")
    print(f"{'=' * 80}\n")

    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")

    success_count = 0
    error_count = 0
    no_date_count = 0

    for row_id, path, date_taken, modified, proj_id in rows:
        # Try date_taken first, fallback to modified
        date_str = date_taken or modified

        if not date_str:
            no_date_count += 1
            continue

        try:
            # Parse the date using metadata service logic
            dt = metadata_service.parse_date(date_str)

            if dt:
                created_ts = int(dt.timestamp())
                created_date = dt.strftime("%Y-%m-%d")
                created_year = dt.year

                if not dry_run:
                    # Update the database
                    cursor.execute("""
                        UPDATE photo_metadata
                        SET created_ts = ?, created_date = ?, created_year = ?
                        WHERE id = ?
                    """, (created_ts, created_date, created_year, row_id))

                success_count += 1

                if success_count % 1000 == 0:
                    print(f"  Processed {success_count} photos...")
                    if not dry_run:
                        conn.commit()
            else:
                error_count += 1
                print(f"  WARNING: Could not parse date for {path[-60:]}: {date_str}")

        except Exception as e:
            error_count += 1
            print(f"  ERROR processing {path[-60:]}: {e}")

    # Final commit
    if not dry_run:
        conn.commit()

    conn.close()

    # Print summary
    print(f"\n{'=' * 80}")
    print(f"Backfill Summary:")
    print(f"  Total photos processed: {len(rows)}")
    print(f"  Successfully backfilled: {success_count}")
    print(f"  Parse errors: {error_count}")
    print(f"  No dates available: {no_date_count}")
    print(f"{'=' * 80}\n")

    if dry_run:
        print("DRY RUN completed - no changes were made")
        print("Run without --dry-run to apply changes")
    else:
        print("✅ Backfill completed successfully!")
        print("\nNext steps:")
        print("1. Restart the app to see updated date counts")
        print("2. The 'By Date' tree should now show all photos")

    return success_count, error_count, no_date_count

def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing created_year fields from existing dates"
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
        help="Limit backfill to specific project ID (default: all projects)"
    )

    args = parser.parse_args()

    try:
        backfill_created_year(
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
