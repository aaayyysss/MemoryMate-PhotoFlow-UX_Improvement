#!/usr/bin/env python3
"""
Migration script to add missing video metadata indexes for unified media queries.

This adds two critical indexes:
- idx_video_project_date: Compound index on (project_id, created_date) for fast date tree queries
- idx_video_created_ts: Index on created_ts for timestamp-based sorting

These indexes mirror the photo_metadata indexes and are required for efficient
unified photo+video queries in the date tree.

Usage:
    python3 migrate_add_video_indexes.py [database_path]

If no database path is provided, uses default 'reference_data.db'
"""

import sqlite3
import sys
from pathlib import Path


def add_video_indexes(db_path: str = "reference_data.db"):
    """
    Add missing video metadata indexes to the database.

    Args:
        db_path: Path to the SQLite database file
    """
    db_file = Path(db_path)

    if not db_file.exists():
        print(f"❌ Database not found: {db_path}")
        print("   Please provide a valid database path")
        return False

    print(f"📊 Adding video metadata indexes to: {db_path}")
    print()

    try:
        conn = sqlite3.connect(str(db_file))
        cur = conn.cursor()

        # Check if video_metadata table exists
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='video_metadata'
        """)

        if not cur.fetchone():
            print("⚠️  video_metadata table not found - skipping migration")
            print("   Run migrate_add_video_tables.py first")
            conn.close()
            return False

        print("[1] Adding compound index: idx_video_project_date")
        print("    Enables fast date tree queries filtering by project_id + created_date")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_project_date
            ON video_metadata(project_id, created_date)
        """)
        print("    ✅ idx_video_project_date created")

        print()
        print("[2] Adding timestamp index: idx_video_created_ts")
        print("    Enables fast timestamp-based sorting and filtering")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_created_ts
            ON video_metadata(created_ts)
        """)
        print("    ✅ idx_video_created_ts created")

        # Verify indexes were created
        print()
        print("[3] Verifying indexes...")
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index'
              AND tbl_name='video_metadata'
              AND name IN ('idx_video_project_date', 'idx_video_created_ts')
            ORDER BY name
        """)

        found_indexes = [row[0] for row in cur.fetchall()]

        if 'idx_video_project_date' in found_indexes:
            print("    ✅ idx_video_project_date verified")
        else:
            print("    ⚠️  idx_video_project_date not found")

        if 'idx_video_created_ts' in found_indexes:
            print("    ✅ idx_video_created_ts verified")
        else:
            print("    ⚠️  idx_video_created_ts not found")

        # Commit changes
        conn.commit()
        print()
        print("✅ Migration completed successfully!")
        print()
        print("Impact:")
        print("  • Date tree now includes videos alongside photos")
        print("  • UNION queries on photo_metadata + video_metadata are optimized")
        print("  • Sidebar date navigation is faster for projects with many videos")

        conn.close()
        return True

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Get database path from command line or use default
    db_path = sys.argv[1] if len(sys.argv) > 1 else "reference_data.db"

    success = add_video_indexes(db_path)
    sys.exit(0 if success else 1)
