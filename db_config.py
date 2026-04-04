"""
Database Configuration
======================

Centralized database path configuration for MemoryMate-PhotoFlow.

This module provides a single source of truth for the database file path,
preventing confusion and inconsistencies across the codebase.

Usage:
    from db_config import get_db_path

    db_path = get_db_path()  # Returns 'reference_data.db'

Migration from previous code:
    OLD: DB_PATH = "photo_app.db"  (unused, confusing)
    OLD: DB_FILE = "reference_data.db"  (reference_db.py)
    OLD: Hard-coded "reference_data.db" strings

    NEW: get_db_path() everywhere (single source of truth)
"""

import os
from pathlib import Path
from app_env import APP_DIR, app_path


# Canonical database file name
_DB_FILENAME = "reference_data.db"


def get_db_path(base_dir: str = None) -> str:
    """
    Get the canonical database path.

    Args:
        base_dir: Optional base directory (defaults to APP_DIR — the
                  application root, which works for both portable and
                  full Python installations)

    Returns:
        str: Full absolute path to database file

    Examples:
        >>> get_db_path()
        '/path/to/app/reference_data.db'

        >>> get_db_path('/path/to/project')
        '/path/to/project/reference_data.db'
    """
    if base_dir is None:
        return app_path(_DB_FILENAME)

    return str(Path(base_dir) / _DB_FILENAME)


def get_db_filename() -> str:
    """
    Get the database filename without path.

    Returns:
        str: Database filename ('reference_data.db')

    Example:
        >>> get_db_filename()
        'reference_data.db'
    """
    return _DB_FILENAME


def ensure_db_directory(db_path: str = None) -> str:
    """
    Ensure the database directory exists.

    Args:
        db_path: Optional database path (defaults to get_db_path())

    Returns:
        str: Database path with directory created

    Raises:
        OSError: If directory creation fails
    """
    if db_path is None:
        db_path = get_db_path()

    db_dir = os.path.dirname(db_path)

    # If path is just filename (no directory), nothing to create
    if not db_dir or db_dir == '.':
        return db_path

    # Create directory if it doesn't exist
    os.makedirs(db_dir, exist_ok=True)

    return db_path


# Legacy compatibility exports
# These maintain backward compatibility with existing code
DB_PATH = get_db_path()   # Now returns absolute path under APP_DIR
DB_FILE = get_db_path()   # Now returns absolute path under APP_DIR
DB_FILENAME = _DB_FILENAME


if __name__ == "__main__":
    # Self-test
    print("Database configuration:")
    print(f"  Filename: {get_db_filename()}")
    print(f"  Default path: {get_db_path()}")
    print(f"  Custom path: {get_db_path('/tmp/test')}")
    print(f"  Legacy DB_PATH: {DB_PATH}")
    print(f"  Legacy DB_FILE: {DB_FILE}")
