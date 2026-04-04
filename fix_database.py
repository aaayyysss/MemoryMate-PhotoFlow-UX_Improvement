#!/usr/bin/env python3
"""
Simple script to fix missing ml_job and semantic_embeddings tables.

Run this when you see "no such table: ml_job" errors.
"""

import sys
import sqlite3
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from repository.base_repository import DatabaseConnection


def check_table(conn, table_name):
    """Check if table exists"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def main():
    print("=" * 80)
    print("DATABASE MIGRATION FIXER")
    print("=" * 80)

    try:
        # Connect to database
        print("\n[1] Connecting to database...")
        db = DatabaseConnection()

        with db.get_connection() as conn:
            # Check current state
            print("\n[2] Checking current state...")
            has_ml_job = check_table(conn, 'ml_job')
            has_semantic_embeddings = check_table(conn, 'semantic_embeddings')

            print(f"   ml_job table: {'✓ exists' if has_ml_job else '✗ missing'}")
            print(f"   semantic_embeddings table: {'✓ exists' if has_semantic_embeddings else '✗ missing'}")

            if has_ml_job and has_semantic_embeddings:
                print("\n✓ Database is already up to date!")
                return 0

            # Apply migrations
            if not has_ml_job:
                print("\n[3] Applying migration v6.0.0 (creates ml_job table)...")
                from migrations import migration_v6_visual_semantics

                migration_v6_visual_semantics.migrate_up(conn)

                # Verify
                success, errors = migration_v6_visual_semantics.verify_migration(conn)
                if not success:
                    print("✗ Verification failed:")
                    for error in errors:
                        print(f"  - {error}")
                    return 1

                print("   ✓ Migration v6.0.0 applied successfully")

            if not has_semantic_embeddings:
                print("\n[4] Applying migration v7.0.0 (creates semantic_embeddings table)...")

                # Check if already recorded
                cursor = conn.execute(
                    "SELECT version FROM schema_version WHERE version = '7.0.0'"
                )
                if cursor.fetchone():
                    print("   ! Migration v7.0.0 already recorded in schema_version")
                else:
                    # Read and apply SQL
                    sql_path = Path(__file__).parent / "migrations" / "migration_v7_semantic_separation.sql"
                    if sql_path.exists():
                        with open(sql_path, 'r') as f:
                            migration_sql = f.read()

                        conn.executescript(migration_sql)
                        conn.commit()
                        print("   ✓ Migration v7.0.0 applied successfully")
                    else:
                        print(f"   ! Migration file not found: {sql_path}")

            # Final check
            print("\n[5] Final verification...")
            has_ml_job = check_table(conn, 'ml_job')
            has_semantic_embeddings = check_table(conn, 'semantic_embeddings')

            print(f"   ml_job table: {'✓ exists' if has_ml_job else '✗ missing'}")
            print(f"   semantic_embeddings table: {'✓ exists' if has_semantic_embeddings else '✗ missing'}")

            if has_ml_job and has_semantic_embeddings:
                print("\n" + "=" * 80)
                print("✓ SUCCESS: Database is now fixed and up to date!")
                print("=" * 80)
                print("\nYou can now:")
                print("  1. Restart the application")
                print("  2. Use AI → Extract Embeddings to create semantic embeddings")
                print("  3. Use the similarity and semantic search features")
                return 0
            else:
                print("\n✗ FAILED: Some tables are still missing")
                return 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
