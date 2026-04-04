#!/usr/bin/env python3
"""
Fix Missing Database Tables - Apply v7 and v8 Migrations

This script applies missing migrations to fix errors like:
- "no such table: semantic_embeddings"
- "no such table: media_instance"
- "no such table: media_asset"

Usage:
    python fix_missing_tables.py

The script will:
1. Check current database schema version
2. Apply missing migrations (v7 and v8)
3. Verify tables were created successfully

Safe to run multiple times (migrations are idempotent).
"""

import sys
import sqlite3
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from repository.base_repository import DatabaseConnection
from repository.migrations import MigrationManager, get_migration_status
from logging_config import get_logger

logger = get_logger(__name__)


def check_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def main():
    """Main function to fix missing tables."""
    print("\n" + "=" * 70)
    print("  MemoryMate-PhotoFlow: Database Migration Fix")
    print("=" * 70)

    try:
        # Connect to database
        print("\n[1/5] Connecting to database...")
        db_conn = DatabaseConnection(auto_init=False)  # Don't auto-migrate yet
        print("   ✓ Connected to database")

        # Check current status
        print("\n[2/5] Checking database status...")
        status = get_migration_status(db_conn)

        print(f"   Current version: {status['current_version']}")
        print(f"   Target version: {status['target_version']}")
        print(f"   Needs migration: {status['needs_migration']}")

        # Check for missing tables
        print("\n[3/5] Checking for missing tables...")
        missing_tables = []
        critical_tables = [
            'semantic_embeddings',  # v7.0.0
            'media_asset',          # v8.0.0
            'media_instance',       # v8.0.0
            'media_stack',          # v8.0.0
            'media_stack_member',   # v8.0.0
        ]

        with db_conn.get_connection() as conn:
            for table in critical_tables:
                exists = check_table_exists(conn, table)
                status_icon = "✓" if exists else "✗"
                print(f"   {status_icon} {table}: {'exists' if exists else 'MISSING'}")
                if not exists:
                    missing_tables.append(table)

        if not missing_tables:
            print("\n   ✓ All tables exist! No migration needed.")
            return

        print(f"\n   ⚠️ Found {len(missing_tables)} missing table(s)")

        # Show pending migrations
        if status['pending_migrations']:
            print("\n[4/5] Pending migrations:")
            for mig in status['pending_migrations']:
                print(f"   - v{mig['version']}: {mig['description']}")
        else:
            print("\n[4/5] No pending migrations detected")
            print("   This might indicate a schema_version tracking issue")

        # Apply migrations
        print("\n[5/5] Applying migrations...")
        print("   This may take a moment...")

        manager = MigrationManager(db_conn)

        # Manually apply v7 and v8 SQL files if they're not in the pending list
        migrations_dir = project_root / 'migrations'

        # Apply v7 if needed
        if 'semantic_embeddings' in missing_tables:
            print("\n   Applying v7.0.0 (semantic_embeddings)...")
            v7_sql_file = migrations_dir / 'migration_v7_semantic_separation.sql'
            if v7_sql_file.exists():
                with open(v7_sql_file, 'r', encoding='utf-8') as f:
                    v7_sql = f.read()
                with db_conn.get_connection() as conn:
                    conn.executescript(v7_sql)
                    conn.commit()
                print("   ✓ v7.0.0 applied successfully")
            else:
                print(f"   ✗ Migration file not found: {v7_sql_file}")

        # Apply v8 if needed
        if any(t in missing_tables for t in ['media_asset', 'media_instance', 'media_stack']):
            print("\n   Applying v8.0.0 (media assets and stacks)...")
            v8_sql_file = migrations_dir / 'migration_v8_media_assets_and_stacks.sql'
            if v8_sql_file.exists():
                with open(v8_sql_file, 'r', encoding='utf-8') as f:
                    v8_sql = f.read()
                with db_conn.get_connection() as conn:
                    conn.executescript(v8_sql)
                    conn.commit()
                print("   ✓ v8.0.0 applied successfully")
            else:
                print(f"   ✗ Migration file not found: {v8_sql_file}")

        # Verify tables now exist
        print("\n" + "=" * 70)
        print("  Verification")
        print("=" * 70)

        all_created = True
        with db_conn.get_connection() as conn:
            for table in critical_tables:
                exists = check_table_exists(conn, table)
                status_icon = "✓" if exists else "✗"
                print(f"   {status_icon} {table}: {'exists' if exists else 'STILL MISSING!'}")
                if not exists:
                    all_created = False

        if all_created:
            print("\n✅ SUCCESS! All tables created successfully.")
            print("\nYou can now:")
            print("   1. Run duplicate detection")
            print("   2. Generate semantic embeddings")
            print("   3. Use similar shot detection")
            print("\nRestart the application to use the new features.")
        else:
            print("\n⚠️ WARNING: Some tables are still missing.")
            print("   Check the logs above for errors.")
            print("   You may need to manually inspect the database.")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.error(f"Migration failed: {e}", exc_info=True)
        print("\nTroubleshooting:")
        print("   1. Check database file permissions")
        print("   2. Backup your database before retrying")
        print("   3. Check application logs for details")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
