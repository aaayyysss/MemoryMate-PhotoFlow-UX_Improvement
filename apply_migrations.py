#!/usr/bin/env python3
"""
Apply pending migrations to fix database schema.

This script applies migrations v6.0.0 (ML job infrastructure) and v7.0.0 (semantic embeddings).
Run this when you see "no such table: ml_job" errors.

Usage:
    python3 apply_migrations.py
"""

import sys
import os
import sqlite3
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


def apply_migration_v6(conn: sqlite3.Connection):
    """Apply migration v6.0.0: Visual Semantics Infrastructure (includes ml_job table)"""
    from migrations import migration_v6_visual_semantics

    logger.info("=" * 80)
    logger.info("Applying Migration v6.0.0: Visual Semantics Infrastructure")
    logger.info("=" * 80)

    try:
        migration_v6_visual_semantics.migrate_up(conn)

        # Verify migration
        success, errors = migration_v6_visual_semantics.verify_migration(conn)
        if not success:
            logger.error("Migration v6.0.0 verification failed:")
            for error in errors:
                logger.error(f"  - {error}")
            return False

        logger.info("✓ Migration v6.0.0 applied and verified successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to apply migration v6.0.0: {e}", exc_info=True)
        return False


def apply_migration_v9_1(conn: sqlite3.Connection):
    """Apply migration v9.1.0: Project Canonical Semantic Model"""
    logger.info("=" * 80)
    logger.info("Applying Migration v9.1.0: Project Canonical Semantic Model")
    logger.info("=" * 80)

    try:
        from migrations import migration_v9_1_semantic_model

        if migration_v9_1_semantic_model.migrate_up(conn):
            # Verify migration
            success, errors = migration_v9_1_semantic_model.verify_migration(conn)
            if not success:
                logger.error("Migration v9.1.0 verification failed:")
                for error in errors:
                    logger.error(f"  - {error}")
                return False

            logger.info("Migration v9.1.0 applied and verified successfully")
            return True
        else:
            return False

    except Exception as e:
        logger.error(f"Failed to apply migration v9.1.0: {e}", exc_info=True)
        return False


def apply_migration_v7(conn: sqlite3.Connection):
    """Apply migration v7.0.0: Semantic Embeddings Separation"""
    logger.info("=" * 80)
    logger.info("Applying Migration v7.0.0: Semantic Embeddings Separation")
    logger.info("=" * 80)

    try:
        # Check if already applied
        cursor = conn.execute(
            "SELECT version FROM schema_version WHERE version = '7.0.0'"
        )
        if cursor.fetchone():
            logger.info("Migration v7.0.0 already applied, skipping")
            return True

        # Read SQL file
        sql_path = project_root / "migrations" / "migration_v7_semantic_separation.sql"
        if not sql_path.exists():
            logger.warning(f"Migration file not found: {sql_path}")
            logger.info("Skipping migration v7.0.0")
            return True

        with open(sql_path, 'r') as f:
            migration_sql = f.read()

        # Apply migration
        conn.executescript(migration_sql)
        conn.commit()

        logger.info("✓ Migration v7.0.0 applied successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to apply migration v7.0.0: {e}", exc_info=True)
        return False


def check_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def check_column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table"""
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def main():
    """Main entry point"""
    logger.info("=" * 80)
    logger.info("MIGRATION RUNNER")
    logger.info("=" * 80)

    try:
        # Connect to database
        db = DatabaseConnection()

        with db.get_connection() as conn:
            # Check current state
            logger.info("\n[1] Checking current database state...")

            has_ml_job = check_table_exists(conn, 'ml_job')
            has_semantic_embeddings = check_table_exists(conn, 'semantic_embeddings')

            logger.info(f"   ml_job table exists: {has_ml_job}")
            logger.info(f"   semantic_embeddings table exists: {has_semantic_embeddings}")

            # Apply migrations as needed
            applied_count = 0

            if not has_ml_job:
                logger.info("\n[2] Applying migration v6.0.0 (creates ml_job table)...")
                if apply_migration_v6(conn):
                    applied_count += 1
                else:
                    logger.error("Migration v6.0.0 failed!")
                    return 1
            else:
                logger.info("\n[2] Migration v6.0.0 already applied, skipping")

            if not has_semantic_embeddings:
                logger.info("\n[3] Applying migration v7.0.0 (creates semantic_embeddings table)...")
                if apply_migration_v7(conn):
                    applied_count += 1
                else:
                    logger.error("Migration v7.0.0 failed!")
                    return 1
            else:
                logger.info("\n[3] Migration v7.0.0 already applied, skipping")

            # Check for v9.1.0 migration (semantic_model column in projects)
            has_semantic_model_column = check_column_exists(conn, 'projects', 'semantic_model')
            logger.info(f"   projects.semantic_model column exists: {has_semantic_model_column}")

            if not has_semantic_model_column:
                logger.info("\n[4] Applying migration v9.1.0 (adds projects.semantic_model column)...")
                if apply_migration_v9_1(conn):
                    applied_count += 1
                else:
                    logger.error("Migration v9.1.0 failed!")
                    return 1
            else:
                logger.info("\n[4] Migration v9.1.0 already applied, skipping")

            # Final verification
            logger.info("\n[5] Final verification...")

            has_ml_job = check_table_exists(conn, 'ml_job')
            has_semantic_embeddings = check_table_exists(conn, 'semantic_embeddings')
            has_semantic_model_column = check_column_exists(conn, 'projects', 'semantic_model')

            logger.info(f"   ml_job table exists: {has_ml_job}")
            logger.info(f"   semantic_embeddings table exists: {has_semantic_embeddings}")
            logger.info(f"   projects.semantic_model column exists: {has_semantic_model_column}")

            if has_ml_job and has_semantic_embeddings and has_semantic_model_column:
                logger.info("\n" + "=" * 80)
                logger.info(f"SUCCESS: Applied {applied_count} migration(s)")
                logger.info("Database is now up to date")
                logger.info("=" * 80)
                return 0
            else:
                logger.error("\n" + "=" * 80)
                logger.error("FAILED: Some tables/columns are still missing")
                logger.error("=" * 80)
                return 1

    except Exception as e:
        logger.error(f"\n✗ Migration failed with exception: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
