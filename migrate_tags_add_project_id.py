#!/usr/bin/env python3
"""
Migration Script: Add project_id to tags table (Schema v3.1.0)

This script migrates the tags table from global tags to project-scoped tags.
Fixes cross-project tag pollution where tags from P01 appear in P02.

CRITICAL ISSUE FIXED:
- Tags were global (no project_id column)
- Tagging photos in P01 made tags appear in P02
- This breaks multi-project workflows

SOLUTION:
- Add project_id column to tags table
- Migrate existing tags to be scoped to their project
- Add UNIQUE constraint on (name, project_id)
- Add indexes for efficient querying

Date: 2025-11-09
Author: Claude (AI Assistant)
"""

import sqlite3
import os
import sys
from datetime import datetime

def migrate_database(db_path: str, dry_run: bool = False):
    """
    Migrate tags table to add project_id column.

    Args:
        db_path: Path to database file
        dry_run: If True, show what would be done without executing
    """
    print("="*80)
    print("TAGS TABLE MIGRATION - Add project_id for Project Isolation")
    print("="*80)
    print(f"\nDatabase: {db_path}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will modify database)'}")
    print(f"Timestamp: {datetime.now()}")

    if not os.path.exists(db_path):
        print(f"\n❌ ERROR: Database not found: {db_path}")
        return False

    # Backup database first
    if not dry_run:
        backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"\n✓ Created backup: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # 1. Check current schema
        print("\n[1] Checking current tags table schema...")
        cur.execute("PRAGMA table_info(tags)")
        columns = {row['name']: row for row in cur.fetchall()}

        print(f"  Current columns: {list(columns.keys())}")

        if 'project_id' in columns:
            print("\n✓ Migration already applied - tags table already has project_id column")
            return True

        # 2. Analyze existing tags and their projects
        print("\n[2] Analyzing existing tags...")
        cur.execute("""
            SELECT
                t.id,
                t.name,
                pm.project_id,
                COUNT(*) as photo_count
            FROM tags t
            JOIN photo_tags pt ON t.id = pt.tag_id
            JOIN photo_metadata pm ON pt.photo_id = pm.id
            GROUP BY t.id, t.name, pm.project_id
            ORDER BY t.name, pm.project_id
        """)
        tag_projects = cur.fetchall()

        # Count unique tags and their usage
        tag_usage = {}
        for row in tag_projects:
            tag_name = row['name']
            if tag_name not in tag_usage:
                tag_usage[tag_name] = []
            tag_usage[tag_name].append({
                'tag_id': row['id'],
                'project_id': row['project_id'],
                'photo_count': row['photo_count']
            })

        print(f"\n  Found {len(tag_usage)} unique tags across projects:")
        for tag_name, projects in tag_usage.items():
            if len(projects) > 1:
                print(f"    ⚠ '{tag_name}' used in {len(projects)} projects (will be split):")
                for proj in projects:
                    print(f"       - Project {proj['project_id']}: {proj['photo_count']} photos")
            else:
                proj = projects[0]
                print(f"    ✓ '{tag_name}' in Project {proj['project_id']}: {proj['photo_count']} photos")

        if dry_run:
            print("\n[DRY RUN] Would execute the following migration steps:")
            print("  1. Create new tags_new table with project_id column")
            print("  2. Copy existing tags with project_id from photo associations")
            print("  3. Drop old tags table")
            print("  4. Rename tags_new to tags")
            print("  5. Create indexes on (project_id, name)")
            print("  6. Update schema_version to 3.1.0")
            return True

        # 3. Create new tags table with project_id
        print("\n[3] Creating new tags table with project_id...")
        cur.execute("""
            CREATE TABLE tags_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE,
                project_id INTEGER NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(name, project_id)
            )
        """)
        print("  ✓ Created tags_new table")

        # 4. Migrate data with project_id
        print("\n[4] Migrating tag data...")
        migrated = {}  # Map old tag_id to new tag_id per project

        for tag_name, projects in tag_usage.items():
            for proj_info in projects:
                old_tag_id = proj_info['tag_id']
                project_id = proj_info['project_id']

                # Insert tag for this project
                cur.execute("""
                    INSERT INTO tags_new (name, project_id)
                    VALUES (?, ?)
                """, (tag_name, project_id))

                new_tag_id = cur.lastrowid
                migrated[(old_tag_id, project_id)] = new_tag_id

                print(f"  ✓ Migrated tag '{tag_name}' (old_id={old_tag_id}) → (new_id={new_tag_id}, project={project_id})")

        # 5. Update photo_tags to use new tag IDs
        print("\n[5] Updating photo_tags with new tag IDs...")
        cur.execute("""
            SELECT pt.photo_id, pt.tag_id, pm.project_id
            FROM photo_tags pt
            JOIN photo_metadata pm ON pt.photo_id = pm.id
        """)
        photo_tag_mappings = cur.fetchall()

        # Create temporary mapping table
        cur.execute("""
            CREATE TEMP TABLE photo_tags_new (
                photo_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (photo_id, tag_id)
            )
        """)

        for row in photo_tag_mappings:
            photo_id = row['photo_id']
            old_tag_id = row['tag_id']
            project_id = row['project_id']

            # Find new tag_id for this photo's project
            key = (old_tag_id, project_id)
            if key in migrated:
                new_tag_id = migrated[key]
                cur.execute("""
                    INSERT OR IGNORE INTO photo_tags_new (photo_id, tag_id)
                    VALUES (?, ?)
                """, (photo_id, new_tag_id))

        print(f"  ✓ Updated {len(photo_tag_mappings)} photo-tag associations")

        # 6. Replace old tables with new ones
        print("\n[6] Replacing old tables with new schema...")
        cur.execute("DROP TABLE photo_tags")
        cur.execute("DROP TABLE tags")
        cur.execute("ALTER TABLE tags_new RENAME TO tags")
        cur.execute("ALTER TABLE photo_tags_new RENAME TO photo_tags")
        print("  ✓ Tables replaced")

        # 7. Add foreign key constraints to photo_tags
        print("\n[7] Creating photo_tags table with foreign keys...")
        cur.execute("""
            CREATE TABLE photo_tags_with_fk (
                photo_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (photo_id, tag_id),
                FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)
        cur.execute("INSERT INTO photo_tags_with_fk SELECT * FROM photo_tags")
        cur.execute("DROP TABLE photo_tags")
        cur.execute("ALTER TABLE photo_tags_with_fk RENAME TO photo_tags")
        print("  ✓ Added foreign key constraints")

        # 8. Create indexes
        print("\n[8] Creating indexes...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tags_project ON tags(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tags_project_name ON tags(project_id, name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id)")
        print("  ✓ Indexes created")

        # 9. Update schema version
        print("\n[9] Updating schema version...")
        cur.execute("""
            INSERT OR REPLACE INTO schema_version (version, description)
            VALUES ('3.1.0', 'Added project_id to tags table for proper tag isolation')
        """)
        print("  ✓ Schema version updated to 3.1.0")

        # 10. Commit changes
        conn.commit()
        print("\n✓ Migration completed successfully!")

        # 11. Verify migration
        print("\n[Verification] Checking migrated data...")
        cur.execute("""
            SELECT t.name, t.project_id, COUNT(*) as photo_count
            FROM tags t
            LEFT JOIN photo_tags pt ON t.id = pt.tag_id
            GROUP BY t.id, t.name, t.project_id
            ORDER BY t.name, t.project_id
        """)
        migrated_tags = cur.fetchall()

        print(f"\n  Tags after migration:")
        for row in migrated_tags:
            print(f"    - '{row['name']}' in Project {row['project_id']}: {row['photo_count']} photos")

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

    parser = argparse.ArgumentParser(description="Migrate tags table to add project_id column")
    parser.add_argument("--db", default="reference_data.db", help="Path to database file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")

    args = parser.parse_args()

    success = migrate_database(args.db, dry_run=args.dry_run)

    sys.exit(0 if success else 1)
