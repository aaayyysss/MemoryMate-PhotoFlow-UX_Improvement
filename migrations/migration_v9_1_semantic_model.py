"""
Migration v9.1.0: Project Canonical Semantic Model

Version: 9.1.0
Date: 2026-01-28

Adds semantic_model column to projects table to enforce one canonical
embedding model per project. This prevents vector space contamination
where embeddings from different models are silently mixed.

Why this matters:
- Cosine similarity is meaningless across different embedding spaces
- Google Photos and Lightroom both enforce single embedding space per library
- Model selection should be project metadata, not a UI preference

The column defaults to 'clip-vit-b32' for backward compatibility with
existing projects.

Usage:
    python apply_migrations.py
"""

import sqlite3
from typing import Tuple, List
from logging_config import get_logger

logger = get_logger(__name__)

MIGRATION_VERSION = "9.1.0"
MIGRATION_DESCRIPTION = "Project canonical semantic model: projects.semantic_model for embedding consistency"


def check_column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    rows = cursor.fetchall()
    # Handle both sqlite3.Row objects and tuples
    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    columns = []
    for row in rows:
        try:
            # Try dictionary/Row access first (column name)
            columns.append(row['name'])
        except (KeyError, TypeError):
            # Fall back to tuple index access
            columns.append(row[1])
    return column in columns


def migrate_up(conn: sqlite3.Connection) -> bool:
    """
    Apply migration: Add semantic_model column to projects table.

    Args:
        conn: SQLite database connection

    Returns:
        True if migration successful, False otherwise
    """
    logger.info(f"[Migration {MIGRATION_VERSION}] Starting migration...")

    try:
        # Check if already migrated
        if check_column_exists(conn, "projects", "semantic_model"):
            logger.info(f"[Migration {MIGRATION_VERSION}] Column 'semantic_model' already exists, skipping")
            return True

        # Add the column with default value
        logger.info(f"[Migration {MIGRATION_VERSION}] Adding 'semantic_model' column to projects table...")
        conn.execute("""
            ALTER TABLE projects
            ADD COLUMN semantic_model TEXT DEFAULT 'clip-vit-b32'
        """)

        # Update existing projects to have the default model
        # This ensures consistency for projects created before this migration
        conn.execute("""
            UPDATE projects
            SET semantic_model = 'clip-vit-b32'
            WHERE semantic_model IS NULL
        """)

        # Record migration in schema_version
        conn.execute("""
            INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (?, ?)
        """, (MIGRATION_VERSION, MIGRATION_DESCRIPTION))

        conn.commit()

        logger.info(f"[Migration {MIGRATION_VERSION}] Successfully added 'semantic_model' column")
        return True

    except Exception as e:
        logger.error(f"[Migration {MIGRATION_VERSION}] Failed: {e}", exc_info=True)
        return False


def migrate_down(conn: sqlite3.Connection) -> bool:
    """
    Rollback migration: Remove semantic_model column from projects table.

    Note: SQLite doesn't support DROP COLUMN directly in older versions.
    This requires table recreation.

    Args:
        conn: SQLite database connection

    Returns:
        True if rollback successful, False otherwise
    """
    logger.info(f"[Migration {MIGRATION_VERSION}] Rolling back migration...")

    try:
        # Check if column exists
        if not check_column_exists(conn, "projects", "semantic_model"):
            logger.info(f"[Migration {MIGRATION_VERSION}] Column 'semantic_model' doesn't exist, nothing to rollback")
            return True

        # SQLite 3.35.0+ supports DROP COLUMN, but for compatibility
        # we use the table recreation pattern
        logger.info(f"[Migration {MIGRATION_VERSION}] Removing 'semantic_model' column...")

        # Create temp table without the column
        conn.execute("""
            CREATE TABLE projects_temp AS
            SELECT id, name, folder, mode, created_at
            FROM projects
        """)

        # Drop original table
        conn.execute("DROP TABLE projects")

        # Rename temp table
        conn.execute("ALTER TABLE projects_temp RENAME TO projects")

        # Remove migration record
        conn.execute("""
            DELETE FROM schema_version WHERE version = ?
        """, (MIGRATION_VERSION,))

        conn.commit()

        logger.info(f"[Migration {MIGRATION_VERSION}] Rollback complete")
        return True

    except Exception as e:
        logger.error(f"[Migration {MIGRATION_VERSION}] Rollback failed: {e}", exc_info=True)
        return False


def verify_migration(conn: sqlite3.Connection) -> Tuple[bool, List[str]]:
    """
    Verify migration was applied correctly.

    Args:
        conn: SQLite database connection

    Returns:
        Tuple of (success: bool, errors: List[str])
    """
    errors = []

    # Check column exists
    if not check_column_exists(conn, "projects", "semantic_model"):
        errors.append("Column 'semantic_model' not found in projects table")

    # Check schema_version record
    cursor = conn.execute(
        "SELECT version FROM schema_version WHERE version = ?",
        (MIGRATION_VERSION,)
    )
    if not cursor.fetchone():
        errors.append(f"Migration version {MIGRATION_VERSION} not recorded in schema_version")

    # Check all projects have semantic_model set
    cursor = conn.execute("""
        SELECT COUNT(*) as cnt FROM projects WHERE semantic_model IS NULL
    """)
    row = cursor.fetchone()
    try:
        null_count = row['cnt']
    except (KeyError, TypeError):
        null_count = row[0]
    if null_count > 0:
        errors.append(f"{null_count} projects have NULL semantic_model")

    return (len(errors) == 0, errors)
