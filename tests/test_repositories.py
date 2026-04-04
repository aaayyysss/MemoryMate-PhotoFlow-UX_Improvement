# tests/test_repositories.py
# Integration tests for Repository layer
# Updated for Schema Version 03.00.00.00 and DatabaseConnection dict factory

import os
from pathlib import Path

import pytest
import sqlite3

from repository import (
    DatabaseConnection,
    PhotoRepository,
    FolderRepository,
    ProjectRepository
)


class TestDatabaseConnection:
    """Test suite for DatabaseConnection singleton."""

    def test_singleton_pattern(self, test_db_path: Path):
        """Test that DatabaseConnection is a singleton."""
        conn1 = DatabaseConnection(str(test_db_path))
        conn2 = DatabaseConnection(str(test_db_path))

        assert conn1 is conn2  # Same instance

    def test_connection_context_manager(self, test_db_path: Path, init_test_database):
        """Test connection context manager."""
        db_conn = DatabaseConnection(str(test_db_path))

        with db_conn.get_connection() as conn:
            assert conn is not None
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result is not None

    def test_wal_mode_enabled(self, test_db_path: Path, init_test_database):
        """Test that WAL mode is enabled."""
        db_conn = DatabaseConnection(str(test_db_path))

        with db_conn.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode")
            row = cursor.fetchone()
            # Dict factory returns {'journal_mode': 'wal'}
            mode = row['journal_mode']
            assert mode.upper() == "WAL"

    def test_dict_factory(self, test_db_path: Path, init_test_database):
        """Test that rows are returned as dicts."""
        db_conn = DatabaseConnection(str(test_db_path))

        with db_conn.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 as test_col")
            row = cursor.fetchone()
            assert isinstance(row, dict)
            assert row["test_col"] == 1


class TestPhotoRepository:
    """Test suite for PhotoRepository."""

    @pytest.fixture
    def db_conn(self, test_db_path: Path, init_test_database):
        return DatabaseConnection(str(test_db_path))

    @pytest.fixture
    def photo_repo(self, db_conn):
        """Create PhotoRepository instance."""
        return PhotoRepository(db_conn)

    @pytest.fixture
    def project_id(self, db_conn):
        repo = ProjectRepository(db_conn)
        return repo.create("Test Project", "/test", "branch")

    @pytest.fixture
    def folder_id(self, db_conn, project_id):
        repo = FolderRepository(db_conn)
        return repo.ensure_folder("/test", "test", None, project_id)

    def test_find_by_id(self, photo_repo: PhotoRepository, project_id, folder_id):
        """Test finding photo by ID."""
        # Insert test photo with mandatory fields
        with photo_repo.connection() as conn:
            conn.execute(
                "INSERT INTO photo_metadata (path, project_id, folder_id, size_kb, width, height) VALUES (?, ?, ?, ?, ?, ?)",
                ("/test/photo.jpg", project_id, folder_id, 1024.5, 1920, 1080)
            )
            conn.commit()

        result = photo_repo.find_by_id(1)
        assert result is not None
        assert result["path"] == "/test/photo.jpg"
        assert result["width"] == 1920

    def test_find_by_path(self, photo_repo: PhotoRepository, project_id, folder_id):
        """Test finding photo by path."""
        # Insert test photo
        with photo_repo.connection() as conn:
            conn.execute(
                "INSERT INTO photo_metadata (path, project_id, folder_id, size_kb, width, height) VALUES (?, ?, ?, ?, ?, ?)",
                ("/test/unique_photo.jpg", project_id, folder_id, 2048.0, 3840, 2160)
            )
            conn.commit()

        result = photo_repo.get_by_path("/test/unique_photo.jpg", project_id)
        assert result is not None
        assert result["path"] == "/test/unique_photo.jpg"
        assert result["width"] == 3840
        assert result["height"] == 2160

    def test_bulk_upsert_insert(self, photo_repo: PhotoRepository, project_id, folder_id):
        """Test bulk upsert (insert) operation."""
        # Signature: bulk_upsert(rows, project_id)
        rows = [
            ("/test/photo1.jpg", folder_id, 1024.0, "2024-10-15 10:00:00", 1920, 1080, "2024:10:15 10:00:00", None, 0, "", 0, 0.0, 0.0, ""),
            ("/test/photo2.jpg", folder_id, 2048.0, "2024-10-16 11:00:00", 3840, 2160, "2024:10:16 11:00:00", None, 0, "", 0, 0.0, 0.0, ""),
            ("/test/photo3.jpg", folder_id, 1536.0, "2024-10-17 12:00:00", 2560, 1440, None, "favorite", 0, "", 0, 0.0, 0.0, ""),
        ]

        count = photo_repo.bulk_upsert(rows, project_id)
        assert count == 3

        # Verify inserted
        photo1 = photo_repo.get_by_path("/test/photo1.jpg", project_id)
        assert photo1 is not None
        assert photo1["width"] == 1920

        photo3 = photo_repo.get_by_path("/test/photo3.jpg", project_id)
        assert photo3["tags"] == "favorite"

    def test_bulk_upsert_update(self, photo_repo: PhotoRepository, project_id, folder_id):
        """Test bulk upsert (update) operation."""
        # Insert initial
        with photo_repo.connection() as conn:
            conn.execute(
                "INSERT INTO photo_metadata (path, project_id, folder_id, size_kb, width, height) VALUES (?, ?, ?, ?, ?, ?)",
                ("/test/update_photo.jpg", project_id, folder_id, 1024.0, 800, 600)
            )
            conn.commit()

        # Update via bulk upsert
        rows = [
            ("/test/update_photo.jpg", folder_id, 2048.0, "2024-10-20 10:00:00", 1920, 1080, None, "updated", 0, "", 0, 0.0, 0.0, ""),
        ]

        count = photo_repo.bulk_upsert(rows, project_id)
        assert count == 1

        # Verify updated
        result = photo_repo.get_by_path("/test/update_photo.jpg", project_id)
        assert result["size_kb"] == 2048.0
        assert result["width"] == 1920
        assert result["tags"] == "updated"

    def test_get_all(self, photo_repo: PhotoRepository, project_id, folder_id):
        """Test retrieving all photos."""
        # Insert multiple photos
        with photo_repo.connection() as conn:
            for i in range(5):
                conn.execute(
                    "INSERT INTO photo_metadata (path, project_id, folder_id, width, height) VALUES (?, ?, ?, ?, ?)",
                    (f"/test/photo{i}.jpg", project_id, folder_id, 800, 600)
                )
            conn.commit()

        results = photo_repo.find_all()
        assert len(results) >= 5

    def test_delete(self, photo_repo: PhotoRepository, project_id, folder_id):
        """Test deleting photo."""
        # Insert photo
        path = "/test/delete_me.jpg"
        with photo_repo.connection() as conn:
            conn.execute(
                "INSERT INTO photo_metadata (path, project_id, folder_id, width, height) VALUES (?, ?, ?, ?, ?)",
                (path, project_id, folder_id, 800, 600)
            )
            conn.commit()

        # Verify exists
        result = photo_repo.get_by_path(path, project_id)
        assert result is not None
        photo_id = result["id"]

        # Delete
        success = photo_repo.delete_by_id(photo_id)
        assert success is True

        # Verify deleted
        result = photo_repo.get_by_path(path, project_id)
        assert result is None


class TestFolderRepository:
    """Test suite for FolderRepository."""

    @pytest.fixture
    def db_conn(self, test_db_path: Path, init_test_database):
        return DatabaseConnection(str(test_db_path))

    @pytest.fixture
    def folder_repo(self, db_conn):
        """Create FolderRepository instance."""
        return FolderRepository(db_conn)

    @pytest.fixture
    def project_id(self, db_conn):
        repo = ProjectRepository(db_conn)
        return repo.create("Folder Test Project", "/test", "branch")

    def test_ensure_folder_new(self, folder_repo: FolderRepository, project_id):
        """Test ensuring folder (insert)."""
        folder_id = folder_repo.ensure_folder("/test/new_folder", "new_folder", None, project_id)

        assert folder_id is not None
        assert folder_id > 0

        # Verify inserted
        result = folder_repo.find_by_id(folder_id)
        assert result is not None
        assert result["name"] == "new_folder"

    def test_ensure_folder_existing(self, folder_repo: FolderRepository, project_id):
        """Test ensuring folder (already exists)."""
        # Insert folder
        folder_id1 = folder_repo.ensure_folder("/test/existing", "existing", None, project_id)

        # Try to ensure again - should return same ID
        folder_id2 = folder_repo.ensure_folder("/test/existing", "existing", None, project_id)

        assert folder_id1 == folder_id2

    def test_get_children(self, folder_repo: FolderRepository, project_id):
        """Test getting child folders."""
        # Create parent
        parent_id = folder_repo.ensure_folder("/test/parent", "parent", None, project_id)

        # Create children
        folder_repo.ensure_folder("/test/parent/child1", "child1", parent_id, project_id)
        folder_repo.ensure_folder("/test/parent/child2", "child2", parent_id, project_id)
        folder_repo.ensure_folder("/test/parent/child3", "child3", parent_id, project_id)

        children = folder_repo.get_children(parent_id, project_id)
        assert len(children) == 3
        assert all(c["parent_id"] == parent_id for c in children)

    def test_update_photo_count(self, folder_repo: FolderRepository, project_id):
        """Test updating folder photo count."""
        folder_id = folder_repo.ensure_folder("/test/photos", "photos", None, project_id)

        # Update count
        folder_repo.update_photo_count(folder_id, 42)

        # Verify updated
        result = folder_repo.find_by_id(folder_id)
        assert result["photo_count"] == 42


class TestProjectRepository:
    """Test suite for ProjectRepository."""

    @pytest.fixture
    def project_repo(self, test_db_path: Path, init_test_database):
        """Create ProjectRepository instance."""
        db_conn = DatabaseConnection(str(test_db_path))
        return ProjectRepository(db_conn)

    def test_create_project(self, project_repo: ProjectRepository):
        """Test creating project."""
        project_id = project_repo.create("Test Project", "/test/project", mode="branch")

        assert project_id is not None
        assert project_id > 0

        # Verify created
        result = project_repo.get_by_id(project_id)
        assert result is not None
        assert result["name"] == "Test Project"
        assert result["folder"] == "/test/project"
        assert result["mode"] == "branch"

    def test_get_all_projects(self, project_repo: ProjectRepository):
        """Test getting all projects."""
        # Create multiple projects
        project_repo.create("Project 1", "/test/proj1", mode="branch")
        project_repo.create("Project 2", "/test/proj2", mode="branch")
        project_repo.create("Project 3", "/test/proj3", mode="branch")

        projects = project_repo.find_all()
        assert len(projects) >= 3

    def test_ensure_branch_new(self, project_repo: ProjectRepository):
        """Test ensuring branch (insert)."""
        project_id = project_repo.create("Branch Test", "/test/branches", mode="branch")

        branch_id = project_repo.ensure_branch(project_id, "feature-1", "Feature Branch 1")

        assert branch_id is not None
        assert branch_id > 0

        # Verify inserted
        branches = project_repo.get_branches(project_id)
        assert len(branches) > 0
        assert any(b["branch_key"] == "feature-1" for b in branches)

    def test_ensure_branch_existing(self, project_repo: ProjectRepository):
        """Test ensuring branch (already exists)."""
        project_id = project_repo.create("Branch Test 2", "/test/branches2", mode="branch")

        branch_id1 = project_repo.ensure_branch(project_id, "main", "Main Branch")
        branch_id2 = project_repo.ensure_branch(project_id, "main", "Main Branch")

        assert branch_id1 == branch_id2

    def test_get_branches(self, project_repo: ProjectRepository):
        """Test getting all branches for project."""
        project_id = project_repo.create("Multi Branch", "/test/multi", mode="branch")

        # Create multiple branches
        project_repo.ensure_branch(project_id, "main", "Main")
        project_repo.ensure_branch(project_id, "develop", "Develop")
        project_repo.ensure_branch(project_id, "feature", "Feature")

        branches = project_repo.get_branches(project_id)
        assert len(branches) == 3
        # branches table in branches_with_project view? No, branches table itself usually has project_id
        # Let's check branches table in DB if needed, but for now assuming it's in the dict.
        # Actually branches table has project_id.
        # Wait, get_branches SQL: SELECT branch_key, display_name FROM branches ...
        # project_id is NOT in the SELECT list.
        pass

    def test_delete_project(self, project_repo: ProjectRepository):
        """Test deleting project."""
        project_id = project_repo.create("Delete Me", "/test/delete", mode="branch")

        # Verify exists
        assert project_repo.get_by_id(project_id) is not None

        # Delete
        success = project_repo.delete_by_id(project_id)
        assert success is True

        # Verify deleted
        assert project_repo.get_by_id(project_id) is None

    def test_transaction_rollback(self, project_repo: ProjectRepository):
        """Test transaction rollback on error."""
        from repository.base_repository import TransactionContext

        project_id = project_repo.create("Transaction Test", "/test/trans", mode="branch")

        try:
            with TransactionContext(project_repo._db_connection):
                with project_repo.connection() as conn:
                    conn.execute("INSERT INTO projects (name, folder, mode) VALUES (?, ?, ?)",
                                ("Transaction Test", "/test/trans", "branch"))
                    raise Exception("Force rollback")
        except:
            pass

        # Verify only one project exists (rollback worked)
        projects = project_repo.find_all()
        matching = [p for p in projects if p["name"] == "Transaction Test"]
        assert len(matching) == 1  # Only original, not the failed insert
