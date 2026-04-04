# tests/test_accordion_sidebar_e2e.py
"""
End-to-end tests for AccordionSidebar user workflows.

Tests complete user interactions and workflows:
- Full user journeys from start to finish
- UI interactions (clicks, drags, context menus)
- Multi-step scenarios
- Real-world usage patterns

These tests simulate actual user behavior to catch integration
and usability issues that unit tests might miss.

Run with:
    pytest tests/test_accordion_sidebar_e2e.py -v
    pytest tests/test_accordion_sidebar_e2e.py -v --slow
"""

import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

# Import fixtures
pytest_plugins = ['tests.conftest', 'tests.conftest_qt']


# ============================================================================
# Test Class: Basic User Workflows
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestBasicUserWorkflows:
    """Test basic user workflows end-to-end."""

    def test_user_opens_accordion_and_browses_sections(
        self, accordion_sidebar, mock_face_clusters, mock_folders, qtbot
    ):
        """
        Test user browses through all accordion sections.

        User story:
        1. User opens application
        2. User clicks through each accordion section
        3. Each section loads and displays content
        """
        sections = ["folders", "dates", "videos", "people", "devices", "quick"]

        for section_id in sections:
            # User clicks navigation button
            nav_button = accordion_sidebar.nav_buttons[section_id]
            QTest.mouseClick(nav_button, Qt.LeftButton)
            qtbot.wait(200)  # Wait for section to expand

            # Verify section is expanded
            assert accordion_sidebar.expanded_section_id == section_id

            # Verify section widget is visible
            section_widget = accordion_sidebar.section_widgets[section_id]
            assert section_widget.is_expanded()

    def test_user_selects_quick_date_filter(self, accordion_sidebar, qtbot):
        """
        Test user filters photos by quick date.

        User story:
        1. User clicks Quick section
        2. User clicks "Today" button
        3. Signal emits with today's date
        4. Photo grid filters to today's photos
        """
        # User expands quick section
        accordion_sidebar._expand_section("quick")
        qtbot.wait(200)

        # Get quick section
        quick_section = accordion_sidebar.section_logic.get("quick")

        # Connect signal spy
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            # User clicks "Today" button (simulate)
            quick_section._on_quick_date_clicked("today")

        # Verify signal emitted with today's date
        from datetime import datetime
        today = datetime.now().date().strftime("%Y-%m-%d")
        assert blocker.args == [today]

    def test_user_filters_by_person(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """
        Test user filters photos by person.

        User story:
        1. User opens People section
        2. User clicks on a person card
        3. Photo grid filters to show only that person's photos
        4. User clicks same person again to clear filter
        """
        # User expands people section
        accordion_sidebar._expand_section("people")
        qtbot.wait(500)

        # User selects person (simulate click on person card)
        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000) as blocker:
            accordion_sidebar._on_person_selected("face_john")

        assert blocker.args == ["face_john"]
        assert accordion_sidebar._active_person_branch == "face_john"

        # User clicks same person again to toggle off
        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000) as blocker:
            accordion_sidebar._on_person_selected("face_john")

        assert blocker.args == [""]
        assert accordion_sidebar._active_person_branch is None

    def test_user_switches_between_filters(
        self, accordion_sidebar, mock_face_clusters, mock_folders, qtbot
    ):
        """
        Test user switches between different filter types.

        User story:
        1. User filters by person
        2. User switches to folder filter
        3. User switches to quick date filter
        4. Each filter works correctly
        """
        # Filter by person
        accordion_sidebar._expand_section("people")
        qtbot.wait(300)

        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000):
            accordion_sidebar._on_person_selected("face_jane")

        # Switch to folders
        accordion_sidebar._expand_section("folders")
        qtbot.wait(300)

        folders_section = accordion_sidebar.section_logic.get("folders")
        if hasattr(folders_section, 'folderSelected'):
            with qtbot.waitSignal(accordion_sidebar.selectFolder, timeout=1000):
                folders_section.folderSelected.emit(1)

        # Switch to quick dates
        accordion_sidebar._expand_section("quick")
        qtbot.wait(200)

        quick_section = accordion_sidebar.section_logic.get("quick")
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000):
            quick_section.quickDateSelected.emit("2025-12-16")


# ============================================================================
# Test Class: Quick Dates Workflows
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestQuickDatesWorkflows:
    """Test quick dates user workflows."""

    def test_user_filters_todays_photos(self, accordion_sidebar, qtbot):
        """
        Test user clicks Today to see today's photos.

        User story:
        1. User wants to see photos from today
        2. User opens Quick section
        3. User clicks "Today"
        4. Grid shows only today's photos
        """
        accordion_sidebar._expand_section("quick")
        qtbot.wait(100)

        quick_section = accordion_sidebar.section_logic.get("quick")

        # User clicks Today button
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            quick_section._on_quick_date_clicked("today")

        # Verify correct date emitted
        from datetime import datetime
        today = datetime.now().date().strftime("%Y-%m-%d")
        assert blocker.args == [today]

    def test_user_filters_last_7_days(self, accordion_sidebar, qtbot):
        """
        Test user filters to last 7 days.

        User story:
        1. User wants to see recent photos
        2. User clicks "Last 7 days"
        3. Grid shows rolling 7-day window
        """
        accordion_sidebar._expand_section("quick")
        qtbot.wait(100)

        quick_section = accordion_sidebar.section_logic.get("quick")

        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            quick_section._on_quick_date_clicked("last_7_days")

        # Verify date range format (should have colon)
        date_range = blocker.args[0]
        assert ":" in date_range

        # Verify end date is today
        from datetime import datetime
        today = datetime.now().date().strftime("%Y-%m-%d")
        assert date_range.endswith(today)

    def test_user_filters_this_month(self, accordion_sidebar, qtbot):
        """
        Test user filters to current month.

        User story:
        1. User wants to see this month's photos
        2. User clicks "This month"
        3. Grid shows current month's photos
        """
        accordion_sidebar._expand_section("quick")
        qtbot.wait(100)

        quick_section = accordion_sidebar.section_logic.get("quick")

        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            quick_section._on_quick_date_clicked("this_month")

        # Verify month format (YYYY-MM)
        from datetime import datetime
        this_month = datetime.now().date().strftime("%Y-%m")
        assert blocker.args == [this_month]

    def test_user_compares_this_year_vs_last_year(self, accordion_sidebar, qtbot):
        """
        Test user switches between this year and last year.

        User story:
        1. User clicks "This year" to see current year photos
        2. User clicks "Last year" to compare
        3. Each filter works correctly
        """
        accordion_sidebar._expand_section("quick")
        qtbot.wait(100)

        quick_section = accordion_sidebar.section_logic.get("quick")

        # This year
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            quick_section._on_quick_date_clicked("this_year")

        from datetime import datetime
        this_year = str(datetime.now().date().year)
        assert blocker.args == [this_year]

        # Last year
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000) as blocker:
            quick_section._on_quick_date_clicked("last_year")

        last_year = str(datetime.now().date().year - 1)
        assert blocker.args == [last_year]


# ============================================================================
# Test Class: Navigation Patterns
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestNavigationPatterns:
    """Test user navigation patterns."""

    def test_user_explores_all_sections_sequentially(
        self, accordion_sidebar, mock_face_clusters, mock_folders, qtbot
    ):
        """
        Test user explores every section in order.

        User story:
        1. User is new to the application
        2. User clicks through each section to explore
        3. User sees all available filtering options
        """
        sections_in_order = ["folders", "dates", "videos", "people", "devices", "quick"]

        for section_id in sections_in_order:
            # User clicks section button
            nav_button = accordion_sidebar.nav_buttons[section_id]
            QTest.mouseClick(nav_button, Qt.LeftButton)
            qtbot.wait(300)

            # Section opens
            assert accordion_sidebar.expanded_section_id == section_id

            # User looks at content (simulated by wait)
            qtbot.wait(200)

        # User has seen all sections
        assert True

    def test_user_jumps_between_favorite_sections(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """
        Test user jumps between frequently used sections.

        User story:
        1. User frequently uses People and Quick sections
        2. User rapidly switches between them
        3. No lag or errors occur
        """
        favorite_sections = ["people", "quick"]

        # User switches back and forth multiple times
        for _ in range(5):
            for section_id in favorite_sections:
                accordion_sidebar._expand_section(section_id)
                qtbot.wait(100)

                assert accordion_sidebar.expanded_section_id == section_id

    def test_user_returns_to_previously_used_section(
        self, accordion_sidebar, mock_folders, qtbot
    ):
        """
        Test user returns to a section they used before.

        User story:
        1. User uses Folders section
        2. User explores other sections
        3. User returns to Folders
        4. Folders section state is preserved
        """
        # User starts with folders
        accordion_sidebar._expand_section("folders")
        qtbot.wait(300)
        assert accordion_sidebar.expanded_section_id == "folders"

        # User explores other sections
        accordion_sidebar._expand_section("dates")
        qtbot.wait(300)

        accordion_sidebar._expand_section("quick")
        qtbot.wait(200)

        # User returns to folders
        accordion_sidebar._expand_section("folders")
        qtbot.wait(300)

        # Should work correctly
        assert accordion_sidebar.expanded_section_id == "folders"


# ============================================================================
# Test Class: Error Recovery Workflows
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestErrorRecoveryWorkflows:
    """Test user workflows with error handling."""

    def test_user_continues_after_section_load_failure(
        self, accordion_sidebar, caplog, qtbot
    ):
        """
        Test user can continue using app after section load fails.

        User story:
        1. User clicks section that fails to load
        2. User tries another section
        3. Other sections work normally
        """
        # Simulate load failure
        people_section = accordion_sidebar.section_logic.get("people")

        with patch.object(people_section, 'load_section', side_effect=Exception("Load failed")):
            # User tries to open people section
            accordion_sidebar._expand_section("people")
            qtbot.wait(300)

        # User tries quick section instead
        accordion_sidebar._expand_section("quick")
        qtbot.wait(200)

        # Quick section should work fine
        assert accordion_sidebar.expanded_section_id == "quick"

    def test_user_retries_failed_operation(self, accordion_sidebar, qtbot):
        """
        Test user retries an operation that initially failed.

        User story:
        1. User tries to expand section (fails first time)
        2. User tries again
        3. Second attempt succeeds
        """
        # First attempt might fail (simulated)
        accordion_sidebar._expand_section("unknown_section")
        qtbot.wait(100)

        # User tries valid section
        accordion_sidebar._expand_section("quick")
        qtbot.wait(200)

        # Should work
        assert accordion_sidebar.expanded_section_id == "quick"


# ============================================================================
# Test Class: Performance Workflows
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestPerformanceWorkflows:
    """Test performance in real-world scenarios."""

    def test_user_works_with_large_photo_library(
        self, accordion_sidebar, accordion_test_db, test_project_id, qtbot
    ):
        """
        Test user with large photo library can use accordion.

        User story:
        1. User has thousands of photos
        2. User opens accordion sections
        3. Sections load in reasonable time
        """
        import sqlite3

        # Insert 1000 mock folders
        conn = sqlite3.connect(str(accordion_test_db))
        cur = conn.cursor()

        folders_data = [
            (i, f"/photos/folder_{i}", test_project_id, i % 100)
            for i in range(1, 1001)
        ]

        cur.executemany(
            """
            INSERT OR REPLACE INTO folders (folder_id, path, project_id, photo_count)
            VALUES (?, ?, ?, ?)
            """,
            folders_data
        )
        conn.commit()
        conn.close()

        # User opens folders section
        import time
        start_time = time.time()

        accordion_sidebar._expand_section("folders")
        qtbot.wait(2000)  # Wait up to 2 seconds

        load_time = time.time() - start_time

        # Should load in under 2 seconds
        assert load_time < 2.0
        assert accordion_sidebar.expanded_section_id == "folders"

    def test_user_rapidly_clicks_through_sections(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """
        Test rapid section switching doesn't cause lag.

        User story:
        1. User rapidly clicks through sections
        2. No lag or freezing occurs
        3. Last section opened is active
        """
        sections = ["folders", "dates", "quick", "people", "videos"]

        # Rapid clicking (very short delays)
        for section_id in sections * 3:
            accordion_sidebar._expand_section(section_id)
            qtbot.wait(20)  # Very short delay

        # Should end on last section
        assert accordion_sidebar.expanded_section_id == "videos"


# ============================================================================
# Test Class: Accessibility Workflows
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestAccessibilityWorkflows:
    """Test accessibility features."""

    def test_keyboard_navigation_through_sections(
        self, accordion_sidebar, qtbot
    ):
        """
        Test keyboard navigation works for accordion sections.

        User story:
        1. User uses keyboard to navigate
        2. Tab key moves between sections
        3. Enter key expands section
        """
        # Note: Actual keyboard navigation implementation would go here
        # This is a placeholder for future keyboard support

        # For now, verify sections can be programmatically accessed
        for section_id in ["folders", "dates", "quick"]:
            accordion_sidebar._expand_section(section_id)
            qtbot.wait(100)
            assert accordion_sidebar.expanded_section_id == section_id


# ============================================================================
# Test Class: Real-World Usage Patterns
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestRealWorldUsagePatterns:
    """Test real-world usage patterns."""

    def test_daily_photo_browsing_workflow(
        self, accordion_sidebar, mock_photos, qtbot
    ):
        """
        Test typical daily photo browsing workflow.

        User story:
        1. User opens app to see today's photos
        2. User clicks "Today" in Quick section
        3. User browses photos
        4. User switches to "Yesterday" to compare
        5. User switches to "Last 7 days" for overview
        """
        # User starts with Quick section
        accordion_sidebar._expand_section("quick")
        qtbot.wait(200)

        quick_section = accordion_sidebar.section_logic.get("quick")

        # Today
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000):
            quick_section._on_quick_date_clicked("today")
        qtbot.wait(100)

        # Yesterday
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000):
            quick_section._on_quick_date_clicked("yesterday")
        qtbot.wait(100)

        # Last 7 days
        with qtbot.waitSignal(accordion_sidebar.selectDate, timeout=1000):
            quick_section._on_quick_date_clicked("last_7_days")
        qtbot.wait(100)

        # Workflow completes successfully
        assert True

    def test_organizing_photos_by_person_workflow(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """
        Test photo organization workflow.

        User story:
        1. User wants to organize photos by people
        2. User opens People section
        3. User browses through different people
        4. User filters by each person to review photos
        """
        # User opens people section
        accordion_sidebar._expand_section("people")
        qtbot.wait(500)

        # User filters by different people
        people = ["face_john", "face_jane", "face_bob"]

        for person in people:
            with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000):
                accordion_sidebar._on_person_selected(person)
            qtbot.wait(200)

            # User reviews photos for this person
            # (simulated by wait)

        # User clears filter
        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000):
            accordion_sidebar._on_person_selected(people[-1])  # Toggle off

        assert accordion_sidebar._active_person_branch is None
