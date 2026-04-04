#!/usr/bin/env python3
"""
Initialize database with schema v3.2.0.

This script creates a fresh database with:
- project_id columns in photo_folders and photo_metadata
- All comprehensive indexes for performance
- Video infrastructure tables
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repository.base_repository import DatabaseConnection
from repository.schema import get_schema_version
from logging_config import get_logger

logger = get_logger(__name__)


def main():
    """Initialize database with current schema."""
    print("=" * 80)
    print("DATABASE INITIALIZATION")
    print("=" * 80)

    db_path = os.path.abspath("reference_data.db")
    print(f"\nDatabase path: {db_path}")

    # Check if database exists and has data
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        print(f"Database size: {size} bytes")
        if size > 0:
            print("\n⚠️  WARNING: Database already exists with data!")
            response = input("Do you want to continue? This will verify/migrate the schema. (y/n): ")
            if response.lower() != 'y':
                print("Aborted.")
                return

    print(f"\nInitializing database with schema {get_schema_version()}...")

    try:
        # Create database connection with auto-initialization
        db_conn = DatabaseConnection(db_path, auto_init=True)

        print("\n✓ Database connection established")

        # Validate schema
        if db_conn.validate_schema():
            print("✓ Schema validation passed")
        else:
            print("✗ Schema validation failed - check logs for details")
            return

        # Print statistics
        with db_conn.get_connection(read_only=True) as conn:
            cur = conn.cursor()

            # Count tables
            cur.execute("SELECT COUNT(*) as count FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            table_count = cur.fetchone()['count']

            # Count indexes
            cur.execute("SELECT COUNT(*) as count FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
            index_count = cur.fetchone()['count']

            print(f"\n📊 Database Statistics:")
            print(f"  - Tables: {table_count}")
            print(f"  - Indexes: {index_count}")
            print(f"  - Schema version: {get_schema_version()}")

            # List all tables
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            tables = [row['name'] for row in cur.fetchall()]

            print(f"\n📋 Tables created:")
            for table in tables:
                print(f"  - {table}")

            # List key indexes
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            indexes = [row['name'] for row in cur.fetchall()]

            print(f"\n🔍 Indexes created (first 10):")
            for idx in indexes[:10]:
                print(f"  - {idx}")
            if len(indexes) > 10:
                print(f"  ... and {len(indexes) - 10} more")

        print("\n" + "=" * 80)
        print("✓ DATABASE INITIALIZED SUCCESSFULLY")
        print("=" * 80)
        print(f"\nDatabase ready at: {db_path}")
        print(f"Schema version: {get_schema_version()}")

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        logger.error("Database initialization failed", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
