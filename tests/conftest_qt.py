# tests/conftest_qt.py
"""
Qt-specific test fixtures for AccordionSidebar tests.

This module provides fixtures for testing Qt widgets and the AccordionSidebar
with proper QApplication setup, mock data, and test database configuration.

Requirements:
    pip install pytest-qt

Usage:
    pytest tests/test_accordion_sidebar_*.py -v
"""

import pytest
import sqlite3
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

# Qt imports
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


# ============================================================================
# QApplication Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def qapp():
    """
    QApplication fixture for Qt tests (session scope).

    Creates a single QApplication instance for the entire test session.
    This avoids the "QApplication already exists" error when running
    multiple Qt tests.

    Yields:
        QApplication: The application instance
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Don't quit here - let pytest handle cleanup


# ============================================================================
# Database Fixtures
# ============================================================================

@pytest.fixture
def test_project_id():
    """Test project ID for accordion sidebar tests."""
    return 1


@pytest.fixture
def accordion_test_db(test_db_path):
    """
    Initialize test database with accordion-specific schema and test data.

    Creates tables for:
    - folders (folder hierarchy)
    - face_branch_reps (people/face clusters)
    - video_metadata (videos)
    - photos (for date filtering)

    Args:
        test_db_path: Path to test database (from conftest.py)

    Returns:
        Path: Path to initialized test database
    """
    conn = sqlite3.connect(str(test_db_path))
    cur = conn.cursor()

    # Create folders table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            folder_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            photo_count INTEGER DEFAULT 0
        )
    """)

    # Create face_branch_reps table (people)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS face_branch_reps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            branch_key TEXT NOT NULL,
            branch_name TEXT,
            rep_thumb_blob BLOB,
            face_count INTEGER DEFAULT 0,
            UNIQUE(project_id, branch_key)
        )
    """)

    # Create video_metadata table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS video_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            duration_seconds REAL,
            width INTEGER,
            height INTEGER,
            metadata_status TEXT
        )
    """)

    # Create photos table (for date hierarchy)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            date_taken TEXT,
            folder_id INTEGER,
            FOREIGN KEY(folder_id) REFERENCES folders(folder_id)
        )
    """)

    conn.commit()
    conn.close()

    return test_db_path


# ============================================================================
# Mock Data Fixtures
# ============================================================================

@pytest.fixture
def mock_face_clusters(accordion_test_db, test_project_id):
    """
    Create mock face clusters in test database.

    Creates 3 test people:
    - face_john (John Doe) - 15 faces
    - face_jane (Jane Smith) - 12 faces
    - face_bob (Bob Johnson) - 8 faces

    Args:
        accordion_test_db: Initialized test database
        test_project_id: Test project ID

    Returns:
        list: List of tuples (project_id, branch_key, branch_name, face_count)
    """
    conn = sqlite3.connect(str(accordion_test_db))
    cur = conn.cursor()

    mock_data = [
        (test_project_id, "face_john", "John Doe", None, 15),
        (test_project_id, "face_jane", "Jane Smith", None, 12),
        (test_project_id, "face_bob", "Bob Johnson", None, 8),
    ]

    cur.executemany(
        """
        INSERT OR REPLACE INTO face_branch_reps
        (project_id, branch_key, branch_name, rep_thumb_blob, face_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        mock_data
    )
    conn.commit()
    conn.close()

    return [(d[0], d[1], d[2], d[4]) for d in mock_data]


@pytest.fixture
def mock_folders(accordion_test_db, test_project_id):
    """
    Create mock folder hierarchy in test database.

    Creates folders:
    - /photos/2024 (50 photos)
    - /photos/2023 (30 photos)
    - /photos/2024/vacation (20 photos)

    Args:
        accordion_test_db: Initialized test database
        test_project_id: Test project ID

    Returns:
        list: List of tuples (folder_id, path, project_id, photo_count)
    """
    conn = sqlite3.connect(str(accordion_test_db))
    cur = conn.cursor()

    mock_data = [
        (1, "/photos/2024", test_project_id, 50),
        (2, "/photos/2023", test_project_id, 30),
        (3, "/photos/2024/vacation", test_project_id, 20),
    ]

    cur.executemany(
        """
        INSERT OR REPLACE INTO folders (folder_id, path, project_id, photo_count)
        VALUES (?, ?, ?, ?)
        """,
        mock_data
    )
    conn.commit()
    conn.close()

    return mock_data


@pytest.fixture
def mock_photos(accordion_test_db, test_project_id):
    """
    Create mock photos with dates in test database.

    Creates photos with dates:
    - 2025-12-16 (today)
    - 2025-12-15 (yesterday)
    - 2025-12-10 to 2025-12-16 (last 7 days)
    - 2025-11 and 2025-12 (months)

    Args:
        accordion_test_db: Initialized test database
        test_project_id: Test project ID

    Returns:
        list: List of photo tuples
    """
    conn = sqlite3.connect(str(accordion_test_db))
    cur = conn.cursor()

    mock_data = [
        ("/photos/2025/photo_001.jpg", test_project_id, "2025-12-16", 1),
        ("/photos/2025/photo_002.jpg", test_project_id, "2025-12-15", 1),
        ("/photos/2025/photo_003.jpg", test_project_id, "2025-12-14", 1),
        ("/photos/2025/photo_004.jpg", test_project_id, "2025-12-10", 1),
        ("/photos/2025/photo_005.jpg", test_project_id, "2025-11-20", 1),
    ]

    cur.executemany(
        """
        INSERT INTO photos (path, project_id, date_taken, folder_id)
        VALUES (?, ?, ?, ?)
        """,
        mock_data
    )
    conn.commit()
    conn.close()

    return mock_data


@pytest.fixture
def mock_videos(accordion_test_db, test_project_id):
    """
    Create mock videos in test database.

    Creates videos:
    - Short video (30s, 1920x1080)
    - Long video (300s, 3840x2160)

    Args:
        accordion_test_db: Initialized test database
        test_project_id: Test project ID

    Returns:
        list: List of video tuples
    """
    conn = sqlite3.connect(str(accordion_test_db))
    cur = conn.cursor()

    mock_data = [
        ("/videos/short.mp4", test_project_id, 30.0, 1920, 1080, "complete"),
        ("/videos/long.mp4", test_project_id, 300.0, 3840, 2160, "complete"),
    ]

    cur.executemany(
        """
        INSERT INTO video_metadata
        (path, project_id, duration_seconds, width, height, metadata_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        mock_data
    )
    conn.commit()
    conn.close()

    return mock_data


# ============================================================================
# AccordionSidebar Fixtures
# ============================================================================

@pytest.fixture
def accordion_sidebar_factory(qapp, accordion_test_db, test_project_id):
    """
    Factory fixture for creating AccordionSidebar instances.

    Creates a sidebar with proper database path patching and cleanup.
    Use this when you need to create multiple sidebars in a test.

    Args:
        qapp: QApplication fixture
        accordion_test_db: Test database path
        test_project_id: Test project ID

    Returns:
        callable: Factory function that creates AccordionSidebar instances

    Example:
        def test_multiple_sidebars(accordion_sidebar_factory):
            sidebar1 = accordion_sidebar_factory()
            sidebar2 = accordion_sidebar_factory()
            # ... test code ...
    """
    created_sidebars = []

    def _create_sidebar():
        from ui.accordion_sidebar import AccordionSidebar

        # Patch database path for this sidebar
        with patch('reference_db.get_db_path', return_value=str(accordion_test_db)):
            sidebar = AccordionSidebar(project_id=test_project_id)
            created_sidebars.append(sidebar)
            return sidebar

    yield _create_sidebar

    # Cleanup all created sidebars
    for sidebar in created_sidebars:
        try:
            sidebar.cleanup()
        except:
            pass


@pytest.fixture
def accordion_sidebar(accordion_sidebar_factory):
    """
    Single AccordionSidebar instance fixture.

    Creates one AccordionSidebar for the test with proper cleanup.

    Args:
        accordion_sidebar_factory: Factory fixture

    Returns:
        AccordionSidebar: Configured accordion sidebar instance

    Example:
        def test_sidebar(accordion_sidebar):
            assert accordion_sidebar.project_id == 1
    """
    return accordion_sidebar_factory()


# ============================================================================
# Mock Section Fixtures
# ============================================================================

@pytest.fixture
def mock_people_section(qapp):
    """
    Mock PeopleSection for unit testing.

    Returns:
        MagicMock: Mocked PeopleSection with common methods
    """
    section = MagicMock()
    section.get_section_id.return_value = "people"
    section.get_title.return_value = "People"
    section.get_icon.return_value = "👥"
    section.is_loading.return_value = False
    section._generation = 0
    return section


@pytest.fixture
def mock_quick_section(qapp):
    """
    Mock QuickSection for unit testing.

    Returns:
        MagicMock: Mocked QuickSection with quick dates data
    """
    section = MagicMock()
    section.get_section_id.return_value = "quick"
    section.get_title.return_value = "Quick Dates"
    section.get_icon.return_value = "⚡"
    section.is_loading.return_value = False
    section._generation = 0
    section._quick_dates = [
        ("today", "Today"),
        ("yesterday", "Yesterday"),
        ("last_7_days", "Last 7 days"),
    ]
    return section


# ============================================================================
# Helper Functions
# ============================================================================

def wait_for_signal(signal, timeout=1000):
    """
    Helper to wait for a Qt signal with timeout.

    Args:
        signal: Qt signal to wait for
        timeout: Timeout in milliseconds (default 1000)

    Returns:
        bool: True if signal was emitted, False if timeout

    Example:
        assert wait_for_signal(sidebar.sectionExpanding)
    """
    from PySide6.QtTest import QSignalSpy
    spy = QSignalSpy(signal)
    return spy.wait(timeout)


def click_widget(widget, qtbot):
    """
    Helper to click a widget using qtbot.

    Args:
        widget: Widget to click
        qtbot: pytest-qt qtbot fixture

    Example:
        click_widget(button, qtbot)
    """
    from PySide6.QtTest import QTest
    QTest.mouseClick(widget, Qt.LeftButton)
    qtbot.wait(10)  # Small delay for event processing


# ============================================================================
# Pytest Configuration
# ============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers for accordion tests."""
    config.addinivalue_line(
        "markers", "accordion: mark test as accordion sidebar test"
    )
    config.addinivalue_line(
        "markers", "qt: mark test as Qt GUI test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running (>1s)"
    )
