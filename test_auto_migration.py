#!/usr/bin/env python3
"""
Test automatic database migrations on app startup.

This verifies that migrations v6 and v7 are applied automatically
when the app starts with an older schema version.
"""

import sys
import os
import sqlite3
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def test_auto_migration():
    """Test that migrations are applied automatically on DatabaseConnection init"""
    print("=" * 80)
    print("Testing Automatic Database Migration System")
    print("=" * 80)

    # Create a temporary database with old schema (v4.0.0)
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        temp_db_path = tmp.name

    try:
        # Create minimal v4.0.0 schema (with tags table that migration v6 expects)
        conn = sqlite3.connect(temp_db_path)
        conn.executescript("""
            CREATE TABLE schema_version (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            );

            INSERT INTO schema_version (version, description)
            VALUES ('4.0.0', 'Old schema version');

            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                folder TEXT NOT NULL,
                mode TEXT NOT NULL
            );

            CREATE TABLE photo_metadata (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                project_id INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL COLLATE NOCASE
            );

            CREATE TABLE photo_tags (
                photo_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (photo_id, tag_id),
                FOREIGN KEY (photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            INSERT INTO projects (id, name, folder, mode)
            VALUES (1, 'Test Project', '/tmp/test', 'date');
        """)
        conn.commit()
        conn.close()

        print("\n[1] Created test database with schema v4.0.0")

        # Now initialize DatabaseConnection with this database
        # This should trigger automatic migrations
        print("\n[2] Initializing DatabaseConnection (should trigger auto-migration)...")

        from repository.base_repository import DatabaseConnection

        # Create connection with our test database
        db = DatabaseConnection(db_path=temp_db_path, auto_init=True)

        print("   ✓ DatabaseConnection initialized")

        # Check that migrations were applied
        print("\n[3] Verifying migrations were applied...")

        with db.get_connection() as conn:
            # Check schema version
            cursor = conn.execute("""
                SELECT version FROM schema_version
                ORDER BY applied_at DESC
                LIMIT 1
            """)
            result = cursor.fetchone()
            current_version = result['version'] if result else "unknown"

            print(f"   Current schema version: {current_version}")

            if current_version != "7.0.0":
                print(f"   ✗ Expected version 7.0.0, got {current_version}")
                return False

            print("   ✓ Schema upgraded to 7.0.0")

            # Check that ml_job table exists
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='ml_job'
            """)
            if not cursor.fetchone():
                print("   ✗ ml_job table not found")
                return False

            print("   ✓ ml_job table created")

            # Check that semantic_embeddings table exists
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='semantic_embeddings'
            """)
            if not cursor.fetchone():
                print("   ✗ semantic_embeddings table not found")
                return False

            print("   ✓ semantic_embeddings table created")

            # Check all schema versions were recorded
            cursor = conn.execute("""
                SELECT version FROM schema_version
                ORDER BY version
            """)
            versions = [row['version'] for row in cursor.fetchall()]
            print(f"\n   Applied versions: {', '.join(versions)}")

            expected_versions = ["4.0.0", "6.0.0", "7.0.0"]
            for ver in expected_versions:
                if ver not in versions:
                    print(f"   ✗ Missing version {ver}")
                    return False

            print("   ✓ All migrations recorded in schema_version")

        print("\n" + "=" * 80)
        print("✓ AUTO-MIGRATION TEST PASSED")
        print("=" * 80)
        print("\nVerified:")
        print("  1. DatabaseConnection detects old schema (v4.0.0)")
        print("  2. Automatically applies migrations v6 and v7")
        print("  3. Creates ml_job table (migration v6)")
        print("  4. Creates semantic_embeddings table (migration v7)")
        print("  5. Updates schema version to 7.0.0")
        print("  6. Records all migrations in schema_version table")
        print("\nUser experience:")
        print("  - NO manual migration scripts needed")
        print("  - NO error messages about missing tables")
        print("  - Tables created automatically on first run")
        print("  - Everything just works!")

        return True

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)


if __name__ == '__main__':
    success = test_auto_migration()
    sys.exit(0 if success else 1)
