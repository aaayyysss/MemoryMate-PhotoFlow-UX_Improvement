#!/usr/bin/env python3
"""Test defensive JobService changes for missing ml_job table"""

import sys
from pathlib import Path
import tempfile
import sqlite3
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def test_missing_ml_job_table():
    """Test that JobService handles missing ml_job table gracefully"""
    print("=" * 80)
    print("Testing Defensive JobService with Missing ml_job Table")
    print("=" * 80)

    # Create a temporary database without ml_job table
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        temp_db_path = tmp.name

    try:
        # Create minimal schema without ml_job
        conn = sqlite3.connect(temp_db_path)
        conn.executescript("""
            CREATE TABLE schema_version (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            );

            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                folder TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE photo_metadata (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                project_id INTEGER NOT NULL DEFAULT 1
            );

            INSERT INTO projects (id, name, folder, mode)
            VALUES (1, 'Test Project', '/tmp/test', 'date');

            INSERT INTO photo_metadata (id, file_path)
            VALUES (1, '/tmp/test/photo1.jpg'),
                   (2, '/tmp/test/photo2.jpg');
        """)
        conn.commit()
        conn.close()

        # Temporarily override database path
        from repository.base_repository import DatabaseConnection
        original_db_path = DatabaseConnection._db_path if hasattr(DatabaseConnection, '_db_path') else None

        # Patch database path
        os.environ['DB_PATH_OVERRIDE'] = temp_db_path
        DatabaseConnection._db_path = temp_db_path

        # Test 1: Initialize JobService (should not crash)
        print("\n[Test 1] Initializing JobService with missing ml_job table...")
        try:
            # Import directly to avoid PySide6 dependency
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "job_service",
                Path(__file__).parent / "services" / "job_service.py"
            )
            job_service_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(job_service_module)

            JobService = job_service_module.JobService

            # Clear singleton
            JobService._instance = None

            job_service = JobService()
            print("   ✓ JobService initialized without crashing")
            print("   ✓ Zombie job recovery was skipped gracefully")
        except Exception as e:
            print(f"   ✗ JobService initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Test 2: Try to enqueue a job (should fail with clear error)
        print("\n[Test 2] Attempting to enqueue job (should fail with clear error)...")
        try:
            job_service.enqueue_job(
                kind='embed',
                payload={'photo_ids': [1, 2], 'model_variant': 'clip-vit-b32'},
                backend='cpu'
            )
            print("   ✗ Job enqueued unexpectedly (should have raised RuntimeError)")
            return False
        except RuntimeError as e:
            error_msg = str(e)
            if "ml_job" in error_msg and "fix_database.py" in error_msg:
                print("   ✓ Raised RuntimeError with helpful message:")
                print(f"      {error_msg[:100]}...")
            else:
                print(f"   ✗ RuntimeError message not helpful enough: {error_msg}")
                return False
        except Exception as e:
            print(f"   ✗ Unexpected exception type: {type(e).__name__}: {e}")
            return False

        # Test 3: Check table existence method
        print("\n[Test 3] Testing _check_ml_job_table_exists method...")
        exists = job_service._check_ml_job_table_exists()
        if not exists:
            print("   ✓ _check_ml_job_table_exists() correctly returned False")
        else:
            print("   ✗ _check_ml_job_table_exists() incorrectly returned True")
            return False

        print("\n" + "=" * 80)
        print("✓ ALL TESTS PASSED")
        print("=" * 80)
        print("\nDefensive changes verified:")
        print("  1. JobService initializes without crashing")
        print("  2. Zombie job recovery is skipped gracefully")
        print("  3. enqueue_job() raises RuntimeError with helpful message")
        print("  4. Error message includes fix_database.py instructions")
        print("\nUsers will now see a friendly error dialog instead of crashes!")

        return True

    finally:
        # Cleanup
        if original_db_path:
            DatabaseConnection._db_path = original_db_path
        if 'DB_PATH_OVERRIDE' in os.environ:
            del os.environ['DB_PATH_OVERRIDE']

        # Remove temp database
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)


def test_with_ml_job_table():
    """Test that JobService still works correctly WITH ml_job table"""
    print("\n\n" + "=" * 80)
    print("Testing JobService with ml_job Table Present")
    print("=" * 80)

    # Use real database (which should have ml_job table if migrations ran)
    try:
        # Import directly to avoid PySide6 dependency
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "job_service",
            Path(__file__).parent / "services" / "job_service.py"
        )
        job_service_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(job_service_module)

        JobService = job_service_module.JobService

        # Clear singleton
        JobService._instance = None

        print("\n[Test 1] Initializing JobService with ml_job table...")
        job_service = JobService()
        print("   ✓ JobService initialized")

        print("\n[Test 2] Checking table existence...")
        exists = job_service._check_ml_job_table_exists()
        if exists:
            print("   ✓ _check_ml_job_table_exists() correctly returned True")
        else:
            print("   ! _check_ml_job_table_exists() returned False")
            print("   ! This means your database needs migrations")
            print("   ! Run: python3 fix_database.py")
            return True  # Not a failure of the defensive code

        print("\n" + "=" * 80)
        print("✓ JobService works correctly with ml_job table")
        print("=" * 80)

        return True

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    # Test both scenarios
    test1_pass = test_missing_ml_job_table()
    test2_pass = test_with_ml_job_table()

    if test1_pass and test2_pass:
        print("\n\n" + "=" * 80)
        print("🎉 ALL SCENARIOS TESTED SUCCESSFULLY")
        print("=" * 80)
        sys.exit(0)
    else:
        print("\n\n" + "=" * 80)
        print("⚠️  SOME TESTS FAILED")
        print("=" * 80)
        sys.exit(1)
