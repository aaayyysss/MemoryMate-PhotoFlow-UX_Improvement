#!/usr/bin/env python3
"""
Schema Check Helper
===================

Utility functions to validate database schema before running scripts.
Provides user-friendly error messages with recovery instructions.

Usage in scripts:
    from schema_check import ensure_database_exists, ensure_tables_exist

    ensure_database_exists()
    ensure_tables_exist(['photo_metadata', 'photo_folders'])
"""

import os
import sqlite3
import sys
from pathlib import Path
from db_config import get_db_path as _get_db_path_from_config


def get_db_path() -> str:
    """Get the canonical database path."""
    return _get_db_path_from_config()


def ensure_database_exists(db_path: str = None) -> str:
    """
    Check if database file exists, exit with helpful message if not.

    Args:
        db_path: Optional database path (defaults to reference_data.db)

    Returns:
        str: Database path (if exists)

    Exits:
        If database doesn't exist, prints error and exits with code 1
    """
    if db_path is None:
        db_path = get_db_path()

    if not os.path.exists(db_path):
        print(f"❌ ERROR: Database not found: {db_path}")
        print()
        print("The database has not been initialized yet.")
        print()
        print("To fix this, run:")
        print(f"  python initialize_database.py")
        print()
        print("This will create the database with the correct schema.")
        sys.exit(1)

    return db_path


def ensure_tables_exist(tables: list[str], db_path: str = None):
    """
    Check if required tables exist in database, exit with helpful message if not.

    Args:
        tables: List of table names that must exist
        db_path: Optional database path (defaults to reference_data.db)

    Exits:
        If any required tables are missing, prints error and exits with code 1
    """
    if db_path is None:
        db_path = get_db_path()

    # First ensure database exists
    ensure_database_exists(db_path)

    # Connect and check tables
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Get list of existing tables
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
        """)
        existing_tables = {row[0] for row in cur.fetchall()}

        # Check for missing tables
        missing_tables = set(tables) - existing_tables

        conn.close()

        if missing_tables:
            print(f"❌ ERROR: Missing required database tables: {', '.join(sorted(missing_tables))}")
            print()
            print(f"Found tables: {', '.join(sorted(existing_tables)) if existing_tables else '(none)'}")
            print()
            print("The database schema is incomplete or outdated.")
            print()
            print("To fix this, run:")
            print(f"  python initialize_database.py")
            print()
            print("This will create or migrate the database to the latest schema.")
            sys.exit(1)

    except sqlite3.Error as e:
        print(f"❌ ERROR: Failed to check database schema: {e}")
        print()
        print("The database may be corrupted.")
        print()
        print("To fix this:")
        print(f"  1. Backup your database: cp {db_path} {db_path}.backup")
        print(f"  2. Reinitialize: python initialize_database.py")
        sys.exit(1)


def ensure_schema_ready(required_tables: list[str] = None, db_path: str = None):
    """
    Convenience function to check both database and tables exist.

    Args:
        required_tables: Optional list of required tables (defaults to core tables)
        db_path: Optional database path (defaults to reference_data.db)

    Exits:
        If database or tables are missing, prints error and exits with code 1
    """
    if required_tables is None:
        # Default to checking core tables
        required_tables = [
            'schema_version',
            'projects',
            'photo_metadata',
            'photo_folders'
        ]

    ensure_tables_exist(required_tables, db_path)


if __name__ == "__main__":
    # Self-test
    print("Testing schema check...")
    ensure_schema_ready()
    print("✅ Schema check passed!")
