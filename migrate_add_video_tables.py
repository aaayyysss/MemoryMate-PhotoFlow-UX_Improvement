#!/usr/bin/env python3
"""
Migration Script: Add Video Tables (Schema v3.2.0)

This script adds complete video infrastructure to existing databases.
Creates three new tables: video_metadata, project_videos, video_tags.

SAFE TO RUN:
- Uses CREATE TABLE IF NOT EXISTS (won't fail if tables already exist)
- Creates automatic backup before migration
- Checks current schema version
- Adds comprehensive indexes for performance

Date: 2025-11-09
Author: Claude (AI Assistant)
"""

import sqlite3
import os
import sys
import shutil
from datetime import datetime

def migrate_database(db_path: str, dry_run: bool = False):
    """
    Add video tables to existing database.

    Args:
        db_path: Path to database file
        dry_run: If True, show what would be done without executing
    """
    print("="*80)
    print("VIDEO INFRASTRUCTURE MIGRATION - Add Video Tables (Schema v3.2.0)")
    print("="*80)
    print(f"\nDatabase: {db_path}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will modify database)'}")
    print(f"Timestamp: {datetime.now()}")

    if not os.path.exists(db_path):
        print(f"\n❌ ERROR: Database not found: {db_path}")
        return False

    #  Backup database first
    if not dry_run:
        backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(db_path, backup_path)
        print(f"\n✓ Created backup: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # 1. Check current schema
        print("\n[1] Checking current schema version...")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        existing_tables = [row['name'] for row in cur.fetchall()]

        print(f"  Found {len(existing_tables)} tables:")
        for table in existing_tables:
            print(f"    - {table}")

        # Check if video tables already exist
        video_tables = ['video_metadata', 'project_videos', 'video_tags']
        existing_video_tables = [t for t in video_tables if t in existing_tables]

        if existing_video_tables:
            print(f"\n  ⚠️  Video tables already exist: {existing_video_tables}")
            print("  → Migration may have already been applied")
            if not dry_run:
                response = input("\n  Continue anyway? (y/n): ")
                if response.lower() != 'y':
                    print("  Aborted by user")
                    return False

        if dry_run:
            print("\n[DRY RUN] Would execute the following:")
            print("  1. Create video_metadata table")
            print("  2. Create project_videos table")
            print("  3. Create video_tags table")
            print("  4. Create 10 indexes for video tables")
            print("  5. Update schema_version to 3.2.0")
            return True

        # 2. Create video_metadata table
        print("\n[2] Creating video_metadata table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS video_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                folder_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,

                -- File metadata
                size_kb REAL,
                modified TEXT,

                -- Video-specific metadata
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                fps REAL,
                codec TEXT,
                bitrate INTEGER,

                -- Timestamps (for date-based browsing)
                date_taken TEXT,
                created_ts INTEGER,
                created_date TEXT,
                created_year INTEGER,
                updated_at TEXT,

                -- Processing status
                metadata_status TEXT DEFAULT 'pending',
                metadata_fail_count INTEGER DEFAULT 0,
                thumbnail_status TEXT DEFAULT 'pending',

                FOREIGN KEY (folder_id) REFERENCES photo_folders(id) ON DELETE CASCADE,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(path, project_id)
            )
        """)
        print("  ✓ video_metadata table created")

        # 3. Create project_videos table
        print("\n[3] Creating project_videos table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                branch_key TEXT,
                video_path TEXT NOT NULL,
                label TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, branch_key, video_path)
            )
        """)
        print("  ✓ project_videos table created")

        # 4. Create video_tags table
        print("\n[4] Creating video_tags table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS video_tags (
                video_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (video_id, tag_id),
                FOREIGN KEY (video_id) REFERENCES video_metadata(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)
        print("  ✓ video_tags table created")

        # 5. Create indexes
        print("\n[5] Creating indexes for video tables...")

        indexes = [
            ("idx_video_metadata_project", "CREATE INDEX IF NOT EXISTS idx_video_metadata_project ON video_metadata(project_id)"),
            ("idx_video_metadata_folder", "CREATE INDEX IF NOT EXISTS idx_video_metadata_folder ON video_metadata(folder_id)"),
            ("idx_video_metadata_date", "CREATE INDEX IF NOT EXISTS idx_video_metadata_date ON video_metadata(date_taken)"),
            ("idx_video_metadata_year", "CREATE INDEX IF NOT EXISTS idx_video_metadata_year ON video_metadata(created_year)"),
            ("idx_video_metadata_status", "CREATE INDEX IF NOT EXISTS idx_video_metadata_status ON video_metadata(metadata_status)"),
            # UNIFIED MEDIA FIX: Add compound index for project_id + created_date (mirrors photo_metadata)
            ("idx_video_project_date", "CREATE INDEX IF NOT EXISTS idx_video_project_date ON video_metadata(project_id, created_date)"),
            # UNIFIED MEDIA FIX: Add index for created_ts for timestamp-based queries
            ("idx_video_created_ts", "CREATE INDEX IF NOT EXISTS idx_video_created_ts ON video_metadata(created_ts)"),
            ("idx_project_videos_project", "CREATE INDEX IF NOT EXISTS idx_project_videos_project ON project_videos(project_id)"),
            ("idx_project_videos_branch", "CREATE INDEX IF NOT EXISTS idx_project_videos_branch ON project_videos(project_id, branch_key)"),
            ("idx_project_videos_path", "CREATE INDEX IF NOT EXISTS idx_project_videos_path ON project_videos(video_path)"),
            ("idx_video_tags_video", "CREATE INDEX IF NOT EXISTS idx_video_tags_video ON video_tags(video_id)"),
            ("idx_video_tags_tag", "CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id)"),
        ]

        for idx_name, idx_sql in indexes:
            cur.execute(idx_sql)
            print(f"  ✓ {idx_name}")

        # 6. Update schema version
        print("\n[6] Updating schema version...")
        cur.execute("""
            INSERT OR REPLACE INTO schema_version (version, description)
            VALUES ('3.2.0', 'Added complete video infrastructure (video_metadata, project_videos, video_tags)')
        """)
        print("  ✓ Schema version updated to 3.2.0")

        # 7. Commit changes
        conn.commit()
        print("\n✓ Migration completed successfully!")

        # 8. Verify migration
        print("\n[Verification] Checking created tables and indexes...")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'video%' ORDER BY name")
        video_tables = [row['name'] for row in cur.fetchall()]

        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_video%' ORDER BY name")
        video_indexes = [row['name'] for row in cur.fetchall()]

        print(f"\n  Video tables ({len(video_tables)}):")
        for table in video_tables:
            print(f"    ✓ {table}")

        print(f"\n  Video indexes ({len(video_indexes)}):")
        for idx in video_indexes:
            print(f"    ✓ {idx}")

        # Check schema version
        cur.execute("SELECT version, description FROM schema_version WHERE version = '3.2.0'")
        version_row = cur.fetchone()
        if version_row:
            print(f"\n  Schema version: {version_row['version']}")
            print(f"  Description: {version_row['description']}")
        else:
            print(f"\n  ⚠️  Warning: Schema version 3.2.0 not found in schema_version table")

        return True

    except Exception as e:
        print(f"\n❌ ERROR during migration: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Add video tables to database (Schema v3.2.0)")
    parser.add_argument("--db", default="reference_data.db", help="Path to database file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")

    args = parser.parse_args()

    success = migrate_database(args.db, dry_run=args.dry_run)

    sys.exit(0 if success else 1)
