#!/usr/bin/env python3
"""
Test script for migration v8.0.0 (Asset-centric duplicate management)

This script validates:
1. Migration v8 can be applied successfully
2. All tables are created correctly
3. Basic CRUD operations work
4. Foreign key constraints are enforced

Usage:
    python test_migration_v8.py
"""

import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from repository.base_repository import DatabaseConnection
from repository.migrations import MigrationManager, get_migration_status
from repository.asset_repository import AssetRepository
from repository.stack_repository import StackRepository
from logging_config import get_logger

logger = get_logger(__name__)


def test_migration_v8():
    """Test migration v8.0.0 on a fresh database."""
    print("=" * 80)
    print("Testing Migration v8.0.0: Asset-Centric Duplicate Management")
    print("=" * 80)

    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        test_db_path = tmp_file.name

    print(f"\n1. Creating test database: {test_db_path}")

    try:
        # Initialize database connection (will auto-apply migrations)
        db_conn = DatabaseConnection(test_db_path, auto_init=True)

        print("✓ Database initialized with auto_init=True")

        # Check migration status
        print("\n2. Checking migration status...")
        status = get_migration_status(db_conn)

        print(f"   Current version: {status['current_version']}")
        print(f"   Target version: {status['target_version']}")
        print(f"   Needs migration: {status['needs_migration']}")
        print(f"   Applied migrations: {status['applied_count']}")

        # Print pending migrations
        if status['pending_count'] > 0:
            print(f"\n   Pending migrations ({status['pending_count']}):")
            for mig in status['pending_migrations']:
                print(f"     - {mig['version']}: {mig['description']}")

            # Manually apply pending migrations
            print(f"\n   Applying pending migrations...")
            manager = MigrationManager(db_conn)
            results = manager.apply_all_migrations()

            for result in results:
                if result['status'] == 'success':
                    print(f"     ✓ Applied {result['version']} ({result['duration_seconds']:.2f}s)")
                else:
                    print(f"     ✗ Failed {result['version']}: {result.get('error', 'Unknown error')}")

            # Recheck status
            status = get_migration_status(db_conn)
            print(f"\n   Updated current version: {status['current_version']}")

        if status['current_version'] != "8.0.0":
            print(f"✗ Migration failed: Expected v8.0.0, got {status['current_version']}")
            return False

        print("✓ Migration v8.0.0 applied successfully")

        # Verify tables exist
        print("\n3. Verifying schema...")
        with db_conn.get_connection(read_only=True) as conn:
            cur = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name LIKE 'media_%'
                ORDER BY name
            """)
            tables = [row['name'] for row in cur.fetchall()]

            expected_tables = [
                'media_asset',
                'media_instance',
                'media_stack',
                'media_stack_member',
                'media_stack_meta'
            ]

            print(f"   Found tables: {tables}")

            for table in expected_tables:
                if table in tables:
                    print(f"   ✓ {table} exists")
                else:
                    print(f"   ✗ {table} MISSING")
                    return False

        # Test basic operations
        print("\n4. Testing AssetRepository operations...")

        # Create test project and photos
        with db_conn.get_connection() as conn:
            # Check if projects table exists
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
            if not cur.fetchone():
                print("   ⚠ projects table doesn't exist, creating minimal version...")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS projects (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        folder TEXT,
                        mode TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            # Create test project
            conn.execute("DELETE FROM projects WHERE id = 1")  # Clean up first
            conn.execute(
                "INSERT INTO projects (id, name, folder, mode) VALUES (1, 'Test', '', 'date')"
            )

            # Check if photo_metadata table exists
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='photo_metadata'")
            if not cur.fetchone():
                print("   ⚠ photo_metadata table doesn't exist, creating minimal version...")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS photo_metadata (
                        id INTEGER PRIMARY KEY,
                        path TEXT NOT NULL,
                        project_id INTEGER NOT NULL,
                        FOREIGN KEY (project_id) REFERENCES projects(id)
                    )
                """)

            # Create test folder if needed
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='photo_folders'")
            if cur.fetchone():
                conn.execute("DELETE FROM photo_folders WHERE id = 1")
                conn.execute("INSERT INTO photo_folders (id, name, path, project_id) VALUES (1, 'test', '/test', 1)")

            # Create test photos
            conn.execute("DELETE FROM photo_metadata WHERE id IN (1, 2)")  # Clean up first
            conn.execute(
                "INSERT INTO photo_metadata (id, path, project_id, folder_id) VALUES (1, '/test/photo1.jpg', 1, 1)"
            )
            conn.execute(
                "INSERT INTO photo_metadata (id, path, project_id, folder_id) VALUES (2, '/test/photo2.jpg', 1, 1)"
            )
            conn.commit()

        print("   ✓ Created test project and photos")

        asset_repo = AssetRepository(db_conn)

        # Test: Create asset
        test_hash = "a" * 64  # Fake SHA256
        asset_id = asset_repo.create_asset_if_missing(
            project_id=1,
            content_hash=test_hash,
            representative_photo_id=1
        )
        print(f"   ✓ Created asset {asset_id} with hash {test_hash[:16]}...")

        # Test: Get asset by hash
        asset = asset_repo.get_asset_by_hash(1, test_hash)
        if asset and asset['asset_id'] == asset_id:
            print(f"   ✓ Retrieved asset by hash")
        else:
            print(f"   ✗ Failed to retrieve asset by hash")
            return False

        # Test: Link instance
        asset_repo.link_instance(
            project_id=1,
            asset_id=asset_id,
            photo_id=1,
            file_size=1024000
        )
        print(f"   ✓ Linked photo 1 to asset {asset_id}")

        # Test: List instances
        instances = asset_repo.list_asset_instances(1, asset_id)
        if len(instances) == 1 and instances[0]['photo_id'] == 1:
            print(f"   ✓ Retrieved {len(instances)} instance(s)")
        else:
            print(f"   ✗ Failed to retrieve instances")
            return False

        # Test StackRepository operations
        print("\n5. Testing StackRepository operations...")

        stack_repo = StackRepository(db_conn)

        # Test: Create stack
        stack_id = stack_repo.create_stack(
            project_id=1,
            stack_type="similar",
            representative_photo_id=1,
            rule_version="1",
            params_json='{"threshold": 0.92}'
        )
        print(f"   ✓ Created stack {stack_id}")

        # Test: Add members
        stack_repo.add_stack_member(
            project_id=1,
            stack_id=stack_id,
            photo_id=1,
            similarity_score=1.0,
            rank=1
        )
        stack_repo.add_stack_member(
            project_id=1,
            stack_id=stack_id,
            photo_id=2,
            similarity_score=0.95,
            rank=2
        )
        print(f"   ✓ Added 2 members to stack")

        # Test: List members
        members = stack_repo.list_stack_members(1, stack_id)
        if len(members) == 2:
            print(f"   ✓ Retrieved {len(members)} member(s)")
        else:
            print(f"   ✗ Failed to retrieve members")
            return False

        # Test: Get stack meta
        meta = stack_repo.get_stack_meta(1, stack_id)
        if meta and meta.get('params'):
            print(f"   ✓ Retrieved stack metadata: {meta['params']}")
        else:
            print(f"   ✗ Failed to retrieve stack metadata")
            return False

        # Test foreign key constraints
        print("\n6. Testing foreign key constraints...")

        try:
            # Try to create asset with non-existent project (should fail)
            with db_conn.get_connection() as conn:
                conn.execute(
                    "INSERT INTO media_asset (project_id, content_hash) VALUES (9999, 'test')"
                )
                conn.commit()
            print("   ✗ Foreign key constraint NOT enforced (should have failed)")
            return False
        except Exception as e:
            print(f"   ✓ Foreign key constraint enforced: {str(e)[:60]}...")

        print("\n" + "=" * 80)
        print("✓ ALL TESTS PASSED")
        print("=" * 80)

        return True

    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        if os.path.exists(test_db_path):
            os.unlink(test_db_path)
            print(f"\nCleaned up test database: {test_db_path}")


if __name__ == "__main__":
    success = test_migration_v8()
    sys.exit(0 if success else 1)
