# repository/base_repository.py
# Version 03.00.00.00 dated 20260115
# Base repository pattern for data access layer
# UPDATED: Added schema initialization and migration support

import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Generator
from logging_config import get_logger

logger = get_logger(__name__)


class DatabaseConnection:
    """
    Manages database connections with proper pooling and lifecycle management.

    This singleton class ensures:
    - One instance per database file (singleton per path)
    - Connections are properly configured (foreign keys, WAL mode)
    - Thread-safe access
    - Proper connection cleanup
    """

    _instances: Dict[str, 'DatabaseConnection'] = {}

    def __new__(cls, db_path: str = "reference_data.db", auto_init: bool = True):
        # CRITICAL FIX: Normalize path to absolute for consistent singleton lookup
        # This prevents different relative/absolute path references from creating multiple instances
        norm_path = os.path.abspath(db_path)

        if norm_path not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instances[norm_path] = instance

        return cls._instances[norm_path]

    def __init__(self, db_path: str = "reference_data.db", auto_init: bool = True):
        if self._initialized:
            return

        # CRITICAL FIX: Convert relative paths to absolute paths
        # Worker threads in different contexts may resolve relative paths differently,
        # causing database connection issues. Using absolute paths ensures all threads
        # access the same database file.
        import os
        self._db_path = os.path.abspath(db_path)
        self._auto_init = auto_init
        self._initialized = True

        # Auto-initialize schema if requested
        if self._auto_init:
            self._ensure_schema()

        logger.info(f"DatabaseConnection initialized with path: {self._db_path}")

    @contextmanager
    def get_connection(self, read_only: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a database connection as a context manager.

        Args:
            read_only: If True, opens connection in read-only mode

        Yields:
            sqlite3.Connection: Database connection

        Example:
            with db_conn.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM photos")
        """
        conn = None
        try:
            # FIX: SQLite URIs require forward slashes, even on Windows
            # Convert backslashes to forward slashes for URI mode
            if read_only:
                uri_path = self._db_path.replace('\\', '/')
                uri = f"file:{uri_path}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=10.0, check_same_thread=False)
            else:
                conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)

            # CRITICAL: Enable foreign key constraints (required for CASCADE deletes)
            # SQLite does NOT enforce foreign keys by default!
            conn.execute("PRAGMA foreign_keys = ON")

            # CRITICAL: Verify foreign keys are actually enabled
            fk_check = conn.execute("PRAGMA foreign_keys").fetchone()
            if not fk_check or fk_check[0] != 1:
                raise RuntimeError(
                    "CRITICAL: Failed to enable foreign key constraints! "
                    "This will break CASCADE deletes and data integrity."
                )

            # Set busy timeout to avoid "database is locked" errors under concurrent access
            # This gives SQLite up to 30 seconds to acquire a lock before failing
            conn.execute("PRAGMA busy_timeout = 30000")

            # Configure journal mode with graceful fallback
            # Priority: WAL > DELETE > PERSIST (based on performance and reliability)
            if not read_only:
                journal_modes = ['WAL', 'DELETE', 'PERSIST']
                journal_set = False

                for mode in journal_modes:
                    try:
                        result = conn.execute(f"PRAGMA journal_mode={mode}").fetchone()
                        if result and result[0].upper() == mode:
                            logger.debug(f"Journal mode set to {mode}")
                            journal_set = True
                            break
                    except sqlite3.OperationalError as e:
                        logger.debug(f"Could not set journal mode {mode}: {e}")
                        continue

                if not journal_set:
                    logger.warning("Could not set any journal mode, using default")

            # Return dictionary-like rows for easier access
            conn.row_factory = self._dict_factory

            if read_only:
                yield conn
            else:
                # CRITICAL: Use transaction context manager for write connections
                # This ensures that all operations are committed automatically
                # or rolled back on error.
                with conn:
                    yield conn

        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}", exc_info=True)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Error closing connection: {e}")

    @staticmethod
    def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> Dict[str, Any]:
        """Convert row tuples to dictionaries using column names."""
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    def execute_script(self, script: str):
        """
        Execute a SQL script (for migrations, schema setup).

        Args:
            script: SQL script to execute
        """
        with self.get_connection() as conn:
            conn.executescript(script)
            conn.commit()
        logger.info("SQL script executed successfully")

    def _ensure_schema(self):
        """
        Ensure database schema exists and is up to date.

        This method is called automatically during initialization if auto_init=True.
        It handles three scenarios:
        1. Fresh database (no tables) - creates full schema from scratch
        2. Legacy database (v1.0) - applies migrations to upgrade to v2.0
        3. Current database (v2.0) - no action needed

        The schema creation/migration is idempotent - safe to call multiple times.
        """
        try:
            from .schema import get_schema_sql, get_schema_version
            from .migrations import MigrationManager, get_migration_status

            target_version = get_schema_version()

            # Check if database needs migration
            manager = MigrationManager(self)

            current_version = manager.get_current_version()

            logger.info(f"Schema check: current={current_version}, target={target_version}")

            if current_version == "0.0.0":
                # Fresh database - create full schema from scratch
                logger.info(f"Creating fresh database schema (version {target_version})")

                # CRITICAL: Create schema in DELETE mode and keep it that way
                # WAL mode causes threading issues where worker threads can't see schema
                conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
                try:
                    conn.execute("PRAGMA journal_mode=DELETE")
                    conn.execute("PRAGMA foreign_keys = ON")

                    conn.executescript(get_schema_sql())
                    conn.commit()
                finally:
                    conn.close()

                # VERIFY: Open a new connection and check tables exist
                with self.get_connection() as verify_conn:
                    cursor = verify_conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                    tables = [row['name'] if isinstance(row, dict) else row[0] for row in cursor.fetchall()]

                    if 'photo_metadata' not in tables:
                        logger.error("Schema creation failed - photo_metadata table not found")
                        logger.error(f"All tables: {tables}")
                        raise Exception("Schema creation failed - photo_metadata table not found")

                logger.info(f"✓ Fresh schema created (version {target_version})")

            elif current_version != target_version:
                # Legacy database - apply migrations
                logger.info(f"Migrating database from {current_version} to {target_version}")

                status = get_migration_status(self)
                logger.info(f"Pending migrations: {status['pending_count']}")

                if status['needs_migration']:
                    results = manager.apply_all_migrations()

                    # Check results
                    success_count = sum(1 for r in results if r['status'] == 'success')
                    failed_count = sum(1 for r in results if r['status'] == 'failed')

                    if failed_count > 0:
                        logger.error(f"✗ Migrations failed: {failed_count} failed, {success_count} succeeded")
                        raise Exception(f"Migration failed - database may be in inconsistent state")

                    # Checkpoint WAL after migrations
                    try:
                        with self.get_connection() as conn:
                            conn.execute("PRAGMA wal_checkpoint(FULL)")
                            conn.commit()
                    except Exception as e:
                        logger.warning(f"Post-migration WAL checkpoint warning: {e}")

                    logger.info(f"✓ Migrations completed: {success_count} applied successfully")
                else:
                    logger.info("✓ Database already up to date")

            else:
                # Already at target version
                logger.info(f"✓ Database already at target version {target_version}")

        except Exception as e:
            logger.error(f"Schema initialization/migration failed: {e}", exc_info=True)
            raise

    def validate_schema(self) -> bool:
        """
        Validate that database schema matches expected structure.

        Returns:
            bool: True if schema is valid, False otherwise
        """
        try:
            from .schema import get_expected_tables, get_expected_indexes

            with self.get_connection(read_only=True) as conn:
                cur = conn.cursor()

                # Check tables
                cur.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """)
                actual_tables = {row['name'] for row in cur.fetchall()}
                expected_tables = set(get_expected_tables())

                missing_tables = expected_tables - actual_tables
                if missing_tables:
                    logger.error(f"Missing tables: {missing_tables}")
                    return False

                # Check indexes (optional - some may not exist in legacy DBs)
                cur.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='index' AND name NOT LIKE 'sqlite_%'
                """)
                actual_indexes = {row['name'] for row in cur.fetchall()}
                expected_indexes = set(get_expected_indexes())

                missing_indexes = expected_indexes - actual_indexes
                if missing_indexes:
                    logger.warning(f"Missing indexes (non-critical): {missing_indexes}")
                    # Don't fail on missing indexes - just warn

                logger.info("Schema validation passed")
                return True

        except Exception as e:
            logger.error(f"Schema validation failed: {e}", exc_info=True)
            return False

    def get_schema_version(self) -> str:
        """
        Get the current schema version from the database.

        Returns:
            str: Schema version string, or "unknown" if not found
        """
        try:
            with self.get_connection(read_only=True) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT version FROM schema_version
                    ORDER BY applied_at DESC
                    LIMIT 1
                """)
                result = cur.fetchone()
                return result['version'] if result else "unknown"
        except Exception:
            return "unknown"


class BaseRepository(ABC):
    """
    Abstract base class for all repositories.

    Repositories handle all database operations for a specific domain entity.
    This promotes:
    - Single Responsibility Principle
    - Testability (can mock repositories)
    - Clean separation between business logic and data access

    Usage:
        class PhotoRepository(BaseRepository):
            def get_by_id(self, photo_id: int) -> Optional[Dict]:
                with self.connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
                    return cur.fetchone()
    """

    def __init__(self, db_connection: Optional[DatabaseConnection] = None):
        """
        Initialize repository with database connection.

        Args:
            db_connection: Optional DatabaseConnection instance.
                          If None, uses default singleton.
        """
        self._db_connection = db_connection or DatabaseConnection()
        self.logger = get_logger(self.__class__.__name__)

    @contextmanager
    def connection(self, read_only: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """
        Get a database connection for repository operations.

        Args:
            read_only: Whether to open in read-only mode

        Yields:
            Database connection
        """
        with self._db_connection.get_connection(read_only=read_only) as conn:
            yield conn

    @abstractmethod
    def _table_name(self) -> str:
        """Return the primary table name this repository manages."""
        pass

    def count(self, where_clause: str = "", params: tuple = ()) -> int:
        """
        Count rows in the repository's table.

        Args:
            where_clause: Optional WHERE clause (without 'WHERE' keyword)
            params: Parameters for the where clause

        Returns:
            Number of matching rows
        """
        sql = f"SELECT COUNT(*) as count FROM {self._table_name()}"
        if where_clause:
            sql += f" WHERE {where_clause}"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, params or ())
            result = cur.fetchone()
            return result['count'] if result else 0

    def exists(self, where_clause: str, params: tuple) -> bool:
        """
        Check if any rows match the criteria.

        Args:
            where_clause: WHERE clause (without 'WHERE' keyword)
            params: Parameters for the where clause

        Returns:
            True if at least one row exists
        """
        return self.count(where_clause, params) > 0

    def find_by_id(self, id_value: Any, id_column: str = "id") -> Optional[Dict[str, Any]]:
        """
        Find a single row by ID.

        Args:
            id_value: The ID value to search for
            id_column: Name of the ID column (default: "id")

        Returns:
            Dictionary representing the row, or None if not found
        """
        sql = f"SELECT * FROM {self._table_name()} WHERE {id_column} = ?"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (id_value,))
            return cur.fetchone()

    def find_all(self,
                 where_clause: str = "",
                 params: tuple = (),
                 order_by: str = "",
                 limit: Optional[int] = None,
                 offset: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Find all rows matching criteria.

        Args:
            where_clause: Optional WHERE clause
            params: Parameters for where clause
            order_by: Optional ORDER BY clause (e.g., "created_at DESC")
                     WARNING: Not parameterized - only pass trusted/validated strings
                     Never pass user input directly to prevent SQL injection
            limit: Optional maximum number of rows
            offset: Optional number of rows to skip

        Returns:
            List of dictionaries representing rows
        """
        sql = f"SELECT * FROM {self._table_name()}"

        if where_clause:
            sql += f" WHERE {where_clause}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None and limit is not None:
            sql += f" OFFSET {int(offset)}"

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, params or ())
            return cur.fetchall()

    def delete_by_id(self, id_value: Any, id_column: str = "id") -> bool:
        """
        Delete a row by ID.

        Args:
            id_value: The ID value
            id_column: Name of the ID column

        Returns:
            True if a row was deleted
        """
        sql = f"DELETE FROM {self._table_name()} WHERE {id_column} = ?"

        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (id_value,))
            conn.commit()
            deleted = cur.rowcount > 0

        if deleted:
            self.logger.info(f"Deleted row with {id_column}={id_value} from {self._table_name()}")

        return deleted


class TransactionContext:
    """
    Context manager for database transactions.

    Usage:
        with TransactionContext(db_connection) as conn:
            repo1.insert(..., conn=conn)
            repo2.update(..., conn=conn)
            # Commits automatically if no exception
    """

    def __init__(self, db_connection: DatabaseConnection):
        self.db_connection = db_connection
        self.conn = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = sqlite3.connect(self.db_connection._db_path,
                                    timeout=10.0,
                                    check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        # Match the journal mode used by DatabaseConnection.get_connection()
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self.conn.row_factory = DatabaseConnection._dict_factory
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            try:
                self.conn.commit()
                logger.debug("Transaction committed successfully")
            except Exception as e:
                logger.error(f"Commit failed: {e}", exc_info=True)
                try:
                    self.conn.rollback()
                except Exception as rollback_err:
                    logger.warning(f"Rollback after commit failure failed: {rollback_err}")
                raise
        else:
            logger.warning(f"Transaction rolled back due to: {exc_val}")
            try:
                self.conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Rollback failed: {rollback_err}")

        try:
            self.conn.close()
        except Exception:
            pass

        return False  # Re-raise exception if occurred
