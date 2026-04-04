"""
Migration v6.0.0: Visual Semantics Infrastructure
Date: 2025-12-29

This migration adds ML-powered visual understanding to MemoryMate:
- Image embeddings (CLIP/SigLIP for semantic search)
- Captions (BLIP2 for natural language descriptions)
- Tag suggestions (ML-powered tagging with user review)
- Object detections (open-vocabulary detection)
- Event clustering (weddings, trips, "days")
- Job orchestration (persistent, crash-safe background jobs)

CRITICAL: This migration is idempotent and safe to run multiple times.
"""

import sqlite3
import os
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)

# Path to the CREATE TABLES SQL file
MIGRATION_SQL_PATH = os.path.join(
    os.path.dirname(__file__),
    "migration_v6_visual_semantics_create.sql"
)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """
    Check if a column exists in a table.

    Args:
        conn: SQLite connection
        table: Table name
        column: Column name

    Returns:
        bool: True if column exists, False otherwise
    """
    cursor = conn.execute(f"PRAGMA table_info({table})")
    rows = cursor.fetchall()

    # Handle different row factory types
    columns = []
    for row in rows:
        if isinstance(row, dict):
            # Dict row factory - PRAGMA table_info returns 'name' column
            columns.append(row.get('name', ''))
        elif isinstance(row, sqlite3.Row):
            # sqlite3.Row - can access by index (column name is at index 1)
            columns.append(row[1])
        else:
            # Regular tuple - column name is at index 1
            columns.append(row[1])

    return column in columns


def _add_column_if_not_exists(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    column_type: str,
    default: str = None
) -> bool:
    """
    Add a column to a table if it doesn't already exist.

    Args:
        conn: SQLite connection
        table: Table name
        column: Column name
        column_type: Column type (e.g., 'TEXT', 'INTEGER')
        default: Default value clause (e.g., "DEFAULT 'user'" or "DEFAULT 0")

    Returns:
        bool: True if column was added, False if already existed
    """
    if _column_exists(conn, table, column):
        logger.debug(f"Column {table}.{column} already exists, skipping")
        return False

    default_clause = f" {default}" if default else ""
    sql = f"ALTER TABLE {table} ADD COLUMN {column} {column_type}{default_clause}"

    try:
        conn.execute(sql)
        logger.info(f"✓ Added column {table}.{column}")
        return True
    except sqlite3.OperationalError as e:
        # This should not happen if _column_exists works correctly
        logger.warning(f"Failed to add column {table}.{column}: {e}")
        return False


def _ensure_foreign_keys_enabled(conn: sqlite3.Connection):
    """
    Ensure foreign keys are enabled for this connection.

    CRITICAL: SQLite does NOT enforce foreign key constraints by default.
    Without this, ON DELETE CASCADE will silently fail.

    Args:
        conn: SQLite connection
    """
    # Check current state (handle both tuple and dict row factories)
    result = conn.execute("PRAGMA foreign_keys").fetchone()
    if result:
        # Handle dict row factory (common in repositories)
        if isinstance(result, dict):
            fk_value = result.get('foreign_keys', 0)
        else:
            # Handle tuple row factory
            fk_value = result[0]

        if fk_value == 1:
            logger.debug("Foreign keys already enabled")
            return

    conn.execute("PRAGMA foreign_keys = ON")
    logger.info("✓ Enabled foreign key constraints")

    # Verify (handle both row factory types)
    result = conn.execute("PRAGMA foreign_keys").fetchone()
    if not result:
        raise RuntimeError("CRITICAL: Failed to enable foreign key constraints!")

    # Debug: log what we got
    logger.debug(f"PRAGMA foreign_keys result type: {type(result)}")
    logger.debug(f"PRAGMA foreign_keys result: {result}")

    # Handle different row factory types
    if isinstance(result, dict):
        fk_value = result.get('foreign_keys', 0)
    elif isinstance(result, sqlite3.Row):
        # sqlite3.Row supports index access
        fk_value = result[0]
    else:
        # Regular tuple
        fk_value = result[0]

    logger.debug(f"Extracted FK value: {fk_value}")

    if fk_value != 1:
        raise RuntimeError(f"CRITICAL: Failed to enable foreign key constraints! Got value: {fk_value}")


def _recover_zombie_jobs(conn: sqlite3.Connection):
    """
    Recover jobs left in 'running' state after a crash.

    Any job in 'running' state with an expired lease is moved to 'failed'
    with reason "crash recovery".

    Args:
        conn: SQLite connection
    """
    # Check if ml_job table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ml_job'"
    )
    if not cursor.fetchone():
        logger.debug("ml_job table does not exist yet, skipping zombie recovery")
        return

    # Find zombie jobs (running but lease expired)
    result = conn.execute("SELECT datetime('now')").fetchone()

    # Handle different row factory types
    if isinstance(result, dict):
        # Get first column (name depends on SELECT alias, use values()[0])
        current_ts = list(result.values())[0]
    elif isinstance(result, sqlite3.Row):
        current_ts = result[0]
    else:
        current_ts = result[0]

    zombie_count = conn.execute("""
        UPDATE ml_job
        SET status = 'failed',
            error = 'Crash recovery: job was running when app crashed',
            updated_at = ?
        WHERE status = 'running'
          AND (lease_expires_at IS NULL OR lease_expires_at < ?)
    """, (current_ts, current_ts)).rowcount

    if zombie_count > 0:
        logger.warning(f"⚠️ Recovered {zombie_count} zombie jobs from crash")
    else:
        logger.debug("No zombie jobs found")


def migrate_up(conn: sqlite3.Connection):
    """
    Apply migration v6.0.0: Visual Semantics Infrastructure.

    This migration:
    1. Ensures foreign keys are enabled (CRITICAL for CASCADE deletes)
    2. Creates new tables (idempotent with IF NOT EXISTS)
    3. Adds columns to existing tables (guarded with PRAGMA table_info checks)
    4. Recovers zombie jobs from crashes
    5. Backfills existing data

    Args:
        conn: SQLite connection (transaction managed by caller)
    """
    logger.info("=" * 80)
    logger.info("Starting migration v6.0.0: Visual Semantics Infrastructure")
    logger.info("=" * 80)

    # Step 1: CRITICAL - Enable foreign keys
    _ensure_foreign_keys_enabled(conn)

    # Step 2: Create new tables (idempotent)
    logger.info("Creating new tables...")
    with open(MIGRATION_SQL_PATH, 'r') as f:
        create_sql = f.read()

    conn.executescript(create_sql)
    logger.info("✓ New tables created")

    # Step 3: Add columns to existing tables (guarded)
    logger.info("Extending existing tables...")

    # Extend 'tags' table for ML tagging
    _add_column_if_not_exists(
        conn, 'tags', 'family', 'TEXT', "DEFAULT 'user'"
    )
    _add_column_if_not_exists(
        conn, 'tags', 'is_sensitive', 'INTEGER', "DEFAULT 0"
    )
    _add_column_if_not_exists(
        conn, 'tags', 'synonyms_json', 'TEXT'
    )

    # Create index on family (if not exists)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tags_family ON tags(family)"
    )

    logger.info("✓ Existing tables extended")

    # Step 4: Backfill existing data
    logger.info("Backfilling existing data...")

    # Set family='user' for all existing tags (if family is NULL)
    backfill_count = conn.execute("""
        UPDATE tags
        SET family = 'user'
        WHERE family IS NULL
    """).rowcount

    if backfill_count > 0:
        logger.info(f"✓ Backfilled {backfill_count} existing tags as 'user' family")

    # Step 5: Recover zombie jobs (if ml_job table exists)
    logger.info("Checking for zombie jobs...")
    _recover_zombie_jobs(conn)

    logger.info("=" * 80)
    logger.info("✓ Migration v6.0.0 completed successfully")
    logger.info("=" * 80)


def migrate_down(conn: sqlite3.Connection):
    """
    Rollback migration v6.0.0.

    WARNING: This drops all ML-generated data (embeddings, captions, suggestions, etc.)
    but preserves user-confirmed tags in photo_tags.

    Args:
        conn: SQLite connection (transaction managed by caller)
    """
    logger.warning("=" * 80)
    logger.warning("Rolling back migration v6.0.0: Visual Semantics Infrastructure")
    logger.warning("⚠️ This will DELETE all ML-generated artifacts!")
    logger.warning("=" * 80)

    # Drop new tables (reverse order of dependencies)
    tables_to_drop = [
        'event_photo',
        'event',
        'photo_detection',
        'photo_tag_decision',
        'photo_tag_suggestion',
        'photo_caption',
        'photo_embedding',
        'ml_job',
        'ml_model'
    ]

    for table in tables_to_drop:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        logger.info(f"✓ Dropped table {table}")

    # Remove columns from 'tags' table
    # NOTE: SQLite does not support DROP COLUMN before version 3.35.0
    # For full rollback, you would need to recreate the table
    logger.warning("⚠️ Cannot remove columns from 'tags' (SQLite limitation)")
    logger.warning("   Columns 'family', 'is_sensitive', 'synonyms_json' will remain")

    # Remove schema version entry
    conn.execute("DELETE FROM schema_version WHERE version = '6.0.0'")

    logger.warning("=" * 80)
    logger.warning("✓ Migration v6.0.0 rolled back (with column remnants)")
    logger.warning("=" * 80)


def verify_migration(conn: sqlite3.Connection) -> Tuple[bool, List[str]]:
    """
    Verify that migration v6.0.0 was applied correctly.

    Returns:
        Tuple[bool, List[str]]: (success, list of errors)
    """
    errors = []

    # Check new tables exist
    expected_tables = [
        'ml_model',
        'photo_embedding',
        'photo_caption',
        'photo_tag_suggestion',
        'photo_tag_decision',
        'photo_detection',
        'event',
        'event_photo',
        'ml_job'
    ]

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    rows = cursor.fetchall()

    # Handle different row factory types
    existing_tables = set()
    for row in rows:
        if isinstance(row, dict):
            existing_tables.add(row.get('name', ''))
        elif isinstance(row, sqlite3.Row):
            existing_tables.add(row[0])
        else:
            existing_tables.add(row[0])

    for table in expected_tables:
        if table not in existing_tables:
            errors.append(f"Table '{table}' not found")

    # Check columns were added to 'tags'
    required_columns = ['family', 'is_sensitive', 'synonyms_json']
    for column in required_columns:
        if not _column_exists(conn, 'tags', column):
            errors.append(f"Column 'tags.{column}' not found")

    # Check foreign keys are enabled
    result = conn.execute("PRAGMA foreign_keys").fetchone()
    if not result:
        errors.append("Foreign keys are NOT enabled (CRITICAL)")
    else:
        # Handle different row factory types
        if isinstance(result, dict):
            fk_value = list(result.values())[0]
        elif isinstance(result, sqlite3.Row):
            fk_value = result[0]
        else:
            fk_value = result[0]

        if fk_value != 1:
            errors.append("Foreign keys are NOT enabled (CRITICAL)")

    # Check schema version
    cursor = conn.execute(
        "SELECT version FROM schema_version WHERE version = '6.0.0'"
    )
    if not cursor.fetchone():
        errors.append("Schema version 6.0.0 not recorded")

    if errors:
        logger.error("Migration verification FAILED:")
        for error in errors:
            logger.error(f"  - {error}")
        return False, errors
    else:
        logger.info("✓ Migration verification PASSED")
        return True, []


# Convenience function for testing
def test_migration():
    """Test migration on an in-memory database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # CRITICAL: Enable foreign keys BEFORE any other operations
    # PRAGMA foreign_keys can only be changed when no transactions are active
    conn.execute("PRAGMA foreign_keys = ON")

    # Create minimal schema (projects, photo_metadata, tags)
    conn.executescript("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE photo_metadata (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            embedding BLOB,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE photo_tags (
            photo_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY(photo_id, tag_id),
            FOREIGN KEY(photo_id) REFERENCES photo_metadata(id) ON DELETE CASCADE,
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );

        CREATE TABLE schema_version (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        );
    """)

    # Insert test data
    conn.execute("INSERT INTO projects (id, name) VALUES (1, 'Test Project')")
    conn.execute("INSERT INTO tags (id, name, project_id) VALUES (1, 'test', 1)")

    # Run migration
    try:
        migrate_up(conn)
        success, errors = verify_migration(conn)

        if success:
            print("✓ Migration test PASSED")
            return True
        else:
            print("✗ Migration test FAILED:")
            for error in errors:
                print(f"  - {error}")
            return False

    except Exception as e:
        print(f"✗ Migration test FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    # Run test migration
    logging.basicConfig(level=logging.INFO)
    test_migration()
