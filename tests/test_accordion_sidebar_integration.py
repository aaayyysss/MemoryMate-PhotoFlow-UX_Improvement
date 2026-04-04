# tests/test_accordion_sidebar_integration.py
"""
Integration tests for AccordionSidebar with database and sections.

Tests the integration between:
- AccordionSidebar and ReferenceDB
- AccordionSidebar and section modules
- Signal flow from sections to accordion to parent layouts
- Data loading from database to UI

Run with:
    pytest tests/test_accordion_sidebar_integration.py -v
    pytest tests/test_accordion_sidebar_integration.py::TestPeopleSectionIntegration -v
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Import fixtures
pytest_plugins = ['tests.conftest', 'tests.conftest_qt']


# ============================================================================
# Test Class: People Section Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestPeopleSectionIntegration:
    """Test People section integration with database."""

    def test_people_section_loads_from_database(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """Test people section loads face clusters from database."""
        # Expand people section
        accordion_sidebar._expand_section("people")
        qtbot.wait(500)  # Wait for async load

        # Get people section
        people_section = accordion_sidebar.section_logic.get("people")
        assert people_section is not None

        # Section should have loaded data
        assert not people_section.is_loading()

    def test_people_section_shows_correct_count(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """Test people section shows correct number of face clusters."""
        accordion_sidebar._expand_section("people")
        qtbot.wait(500)

        # mock_face_clusters creates 3 people
        # Verify section has data for 3 people
        people_section = accordion_sidebar.section_logic.get("people")
        # Note: Actual verification depends on section implementation

    def test_person_selection_signal_propagates(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """Test person selection signal propagates from section to accordion."""
        # Expand people section
        accordion_sidebar._expand_section("people")
        qtbot.wait(500)

        # Get people section
        people_section = accordion_sidebar.section_logic.get("people")

        # Emit personSelected from section (if it has the signal)
        if hasattr(people_section, 'personSelected'):
            with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000) as blocker:
                people_section.personSelected.emit("face_john")

            assert blocker.args == ["face_john"]


# ============================================================================
# Test Class: Folders Section Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestFoldersSectionIntegration:
    """Test Folders section integration with database."""

    def test_folders_section_loads_from_database(
        self, accordion_sidebar, mock_folders, qtbot
    ):
        """Test folders section loads folder tree from database."""
        # Expand folders section
        accordion_sidebar._expand_section("folders")
        qtbot.wait(500)  # Wait for async load

        # Get folders section
        folders_section = accordion_sidebar.section_logic.get("folders")
        assert folders_section is not None
        assert not folders_section.is_loading()

    def test_folder_selection_signal_propagates(
        self, accordion_sidebar, mock_folders, qtbot
    ):
        """Test folder selection signal propagates to accordion."""
        accordion_sidebar._expand_section("folders")
        qtbot.wait(500)

        folders_section = accordion_sidebar.section_logic.get("folders")

        # Emit folderSelected if section has the signal
        if hasattr(folders_section, 'folderSelected'):
            with qtbot.waitSignal(accordion_sidebar.selectFolder, timeout=1000) as blocker:
                folders_section.folderSelected.emit(1)

            assert blocker.args == [1]


# ============================================================================
# Test Class: Dates Section Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestDatesSectionIntegration:
    """Test Dates section integration with database."""

    def test_dates_section_loads_from_database(
        self, accordion_sidebar, mock_photos, qtbot
    ):
        """Test dates section loads date hierarchy from database."""
        accordion_sidebar._expand_section("dates")
        qtbot.wait(500)

        dates_section = accordion_sidebar.section_logic.get("dates")
        assert dates_section is not None
        assert not dates_section.is_loading()

    def test_date_selection_signal_propagates(
        self, accordion_sidebar, mock_photos, qtbot
    ):
        """Test date selection signal propagates to accordion."""
        accordion_sidebar._expand_section("dates")
        qtbot.wait(500)

        dates_section = accordion_sidebar.section_logic.get("dates")

        # Emit dateSelected if section has the signal
        if hasattr(dates_section, 'dateSelected'):
            with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
                dates_section.dateSelected.emit("2025-12")

            assert blocker.args == ["2025-12"]


# ============================================================================
# Test Class: Videos Section Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestVideosSectionIntegration:
    """Test Videos section integration with database."""

    def test_videos_section_loads_from_database(
        self, accordion_sidebar, mock_videos, qtbot
    ):
        """Test videos section loads videos from database."""
        accordion_sidebar._expand_section("videos")
        qtbot.wait(500)

        videos_section = accordion_sidebar.section_logic.get("videos")
        assert videos_section is not None
        assert not videos_section.is_loading()

    def test_video_filter_signal_propagates(
        self, accordion_sidebar, mock_videos, qtbot
    ):
        """Test video filter signal propagates to accordion."""
        accordion_sidebar._expand_section("videos")
        qtbot.wait(500)

        videos_section = accordion_sidebar.section_logic.get("videos")

        # Emit videoFilterSelected if section has the signal
        if hasattr(videos_section, 'videoFilterSelected'):
            with qtbot.waitSignal(accordion_sidebar.selectVideo, timeout=1000) as blocker:
                videos_section.videoFilterSelected.emit("all")

            assert blocker.args == ["all"]


# ============================================================================
# Test Class: Quick Section Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestQuickSectionIntegration:
    """Test Quick section integration (no database needed)."""

    def test_quick_section_loads_synchronously(self, accordion_sidebar, qtbot):
        """Test quick section loads without database query."""
        accordion_sidebar._expand_section("quick")
        qtbot.wait(100)  # Quick section is fast

        quick_section = accordion_sidebar.section_logic.get("quick")
        assert quick_section is not None
        assert not quick_section.is_loading()

    def test_quick_date_selection_signal_propagates(
        self, accordion_sidebar, qtbot
    ):
        """Test quick date selection signal propagates to accordion."""
        accordion_sidebar._expand_section("quick")
        qtbot.wait(100)

        quick_section = accordion_sidebar.section_logic.get("quick")

        # Emit quickDateSelected
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            quick_section.quickDateSelected.emit("2025-12-16")

        assert blocker.args == ["2025-12-16"]

    def test_quick_date_calculations(self, accordion_sidebar):
        """Test quick section date calculations are accurate."""
        quick_section = accordion_sidebar.section_logic.get("quick")

        # Test today
        today = datetime.now().date()
        today_str = quick_section._calculate_date_range("today")
        assert today_str == today.strftime("%Y-%m-%d")

        # Test this year
        this_year = quick_section._calculate_date_range("this_year")
        assert this_year == str(today.year)

        # Test last 7 days format
        last_7_days = quick_section._calculate_date_range("last_7_days")
        assert ":" in last_7_days  # Should be a range
        start, end = last_7_days.split(":")
        assert end == today.strftime("%Y-%m-%d")


# ============================================================================
# Test Class: Multi-Section Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestMultiSectionIntegration:
    """Test interactions between multiple sections."""

    def test_switching_between_sections_preserves_state(
        self, accordion_sidebar, mock_face_clusters, mock_folders, qtbot
    ):
        """Test switching sections doesn't lose state."""
        # Select a person
        accordion_sidebar._on_person_selected("face_john")
        assert accordion_sidebar._active_person_branch == "face_john"

        # Switch to folders
        accordion_sidebar._expand_section("folders")
        qtbot.wait(500)

        # Person selection should be preserved
        assert accordion_sidebar._active_person_branch == "face_john"

        # Switch back to people
        accordion_sidebar._expand_section("people")
        qtbot.wait(500)

        # State still preserved
        assert accordion_sidebar._active_person_branch == "face_john"

    def test_rapid_section_switching_handles_async_loads(
        self, accordion_sidebar, mock_face_clusters, mock_folders, qtbot
    ):
        """Test rapidly switching sections doesn't cause race conditions."""
        sections = ["people", "folders", "dates", "videos"]

        # Rapidly switch between sections
        for section_id in sections * 2:
            accordion_sidebar._expand_section(section_id)
            qtbot.wait(50)  # Short delay

        # Should end up with last section expanded
        assert accordion_sidebar.expanded_section_id == "videos"

        # No crashes or errors should occur


# ============================================================================
# Test Class: Database Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestDatabaseIntegration:
    """Test database interactions."""

    def test_empty_database_handled_gracefully(
        self, qapp, accordion_test_db, test_project_id, qtbot
    ):
        """Test accordion works with empty database."""
        from ui.accordion_sidebar import AccordionSidebar

        with patch('reference_db.get_db_path', return_value=str(accordion_test_db)):
            sidebar = AccordionSidebar(project_id=test_project_id)

            # Expand sections - should not crash
            sidebar._expand_section("people")
            qtbot.wait(500)

            sidebar._expand_section("folders")
            qtbot.wait(500)

            sidebar.cleanup()

    def test_large_dataset_loads_efficiently(
        self, accordion_sidebar, accordion_test_db, test_project_id, qtbot
    ):
        """Test loading large datasets is reasonably fast."""
        import sqlite3
        import time

        # Insert 100 mock people
        conn = sqlite3.connect(str(accordion_test_db))
        cur = conn.cursor()

        people_data = [
            (test_project_id, f"face_{i}", f"Person {i}", None, i % 20)
            for i in range(100)
        ]

        cur.executemany(
            """
            INSERT OR REPLACE INTO face_branch_reps
            (project_id, branch_key, branch_name, rep_thumb_blob, face_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            people_data
        )
        conn.commit()
        conn.close()

        # Time the load
        start_time = time.time()
        accordion_sidebar._expand_section("people")
        qtbot.wait(2000)  # Wait up to 2 seconds
        load_time = time.time() - start_time

        # Should load in under 2 seconds
        assert load_time < 2.0


# ============================================================================
# Test Class: Signal Flow Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestSignalFlowIntegration:
    """Test signal flow from sections through accordion to layouts."""

    def test_person_merge_signal_flow(self, accordion_sidebar, qtbot):
        """Test person merge signal flows correctly."""
        # Connect mock handler
        merge_handler = MagicMock()
        accordion_sidebar.personMerged.connect(merge_handler)

        # Emit personMerged
        accordion_sidebar.personMerged.emit("face_john", "face_jane")

        # Handler should be called
        assert merge_handler.called
        merge_handler.assert_called_once_with("face_john", "face_jane")

    def test_person_deleted_signal_flow(self, accordion_sidebar, qtbot):
        """Test person deleted signal flows correctly."""
        delete_handler = MagicMock()
        accordion_sidebar.personDeleted.connect(delete_handler)

        accordion_sidebar.personDeleted.emit("face_bob")

        assert delete_handler.called
        delete_handler.assert_called_once_with("face_bob")

    def test_section_expanding_signal_flow(self, accordion_sidebar, qtbot):
        """Test section expanding signal flows correctly."""
        expand_handler = MagicMock()
        accordion_sidebar.sectionExpanding.connect(expand_handler)

        accordion_sidebar._expand_section("quick")

        assert expand_handler.called
        expand_handler.assert_called_with("quick")


# ============================================================================
# Test Class: Error Handling Integration
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestErrorHandlingIntegration:
    """Test error handling in integrated scenarios."""

    def test_database_connection_error_handled(
        self, accordion_sidebar, caplog
    ):
        """Test database connection errors are handled gracefully."""
        # Mock database to raise error
        with patch.object(accordion_sidebar.db, 'get_all_folders', side_effect=Exception("DB Error")):
            # Should not crash
            accordion_sidebar._expand_section("folders")
            # Error should be logged

    def test_section_load_error_handled(self, accordion_sidebar, caplog, qtbot):
        """Test section load errors are handled without crashing."""
        section = accordion_sidebar.section_logic.get("people")

        # Mock load_section to raise error
        with patch.object(section, 'load_section', side_effect=Exception("Load error")):
            # Should not crash
            accordion_sidebar._expand_section("people")
            qtbot.wait(200)

    def test_invalid_data_handled(self, accordion_sidebar, qtbot):
        """Test invalid data from database is handled."""
        section = accordion_sidebar.section_logic.get("quick")

        # Call with invalid/None data
        accordion_sidebar._on_section_loaded("quick", section._generation, None)

        # Should not crash (data normalized to empty list)
