# tests/test_accordion_sidebar_unit.py
"""
Unit tests for AccordionSidebar class.

Tests the AccordionSidebar orchestrator's core functionality:
- Initialization
- Section management
- Signal connections
- State management
- Person selection logic
- Section expansion/collapse

Run with:
    pytest tests/test_accordion_sidebar_unit.py -v
    pytest tests/test_accordion_sidebar_unit.py -v --cov=ui.accordion_sidebar
"""

import pytest
from unittest.mock import MagicMock, patch, call
from PySide6.QtCore import SignalInstance

# Import fixtures from conftest_qt
pytest_plugins = ['tests.conftest', 'tests.conftest_qt']


# ============================================================================
# Test Class: Initialization
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestAccordionSidebarInit:
    """Test AccordionSidebar initialization."""

    def test_creates_with_project_id(self, accordion_sidebar):
        """Test AccordionSidebar initializes with project_id."""
        assert accordion_sidebar.project_id == 1
        assert accordion_sidebar.expanded_section_id is None
        assert accordion_sidebar._active_person_branch is None

    def test_creates_all_sections(self, accordion_sidebar):
        """Test all expected sections are created."""
        expected_sections = ["folders", "dates", "videos", "people", "devices", "quick"]
        actual_sections = list(accordion_sidebar.section_widgets.keys())

        assert set(actual_sections) == set(expected_sections)
        assert len(actual_sections) == 6

    def test_creates_section_logic_modules(self, accordion_sidebar):
        """Test section logic modules are instantiated."""
        expected_sections = ["folders", "dates", "videos", "people", "devices", "quick"]

        for section_id in expected_sections:
            assert section_id in accordion_sidebar.section_logic
            assert accordion_sidebar.section_logic[section_id] is not None

    def test_creates_navigation_buttons(self, accordion_sidebar):
        """Test navigation buttons are created for all sections."""
        expected_sections = ["folders", "dates", "videos", "people", "devices", "quick"]

        for section_id in expected_sections:
            assert section_id in accordion_sidebar.nav_buttons
            button = accordion_sidebar.nav_buttons[section_id]
            assert button is not None
            assert button.isEnabled()

    def test_database_connection_initialized(self, accordion_sidebar):
        """Test database connection is initialized."""
        assert hasattr(accordion_sidebar, 'db')
        assert accordion_sidebar.db is not None

    def test_signals_are_defined(self, accordion_sidebar):
        """Test all required signals are defined."""
        required_signals = [
            'selectBranch',
            'selectFolder',
            'selectDate',
            'selectTag',
            'selectPerson',
            'selectVideo',
            'selectDevice',
            'personMerged',
            'personDeleted',
            'mergeHistoryRequested',
            'undoLastMergeRequested',
            'redoLastUndoRequested',
            'peopleToolsRequested',
            'sectionExpanding',
        ]

        for signal_name in required_signals:
            assert hasattr(accordion_sidebar, signal_name)
            signal = getattr(accordion_sidebar, signal_name)
            assert isinstance(signal, SignalInstance)


# ============================================================================
# Test Class: Section Expansion
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestSectionExpansion:
    """Test section expansion/collapse behavior."""

    def test_expand_section_sets_expanded_state(self, accordion_sidebar):
        """Test expanding a section sets expanded_section_id."""
        accordion_sidebar._expand_section("people")
        assert accordion_sidebar.expanded_section_id == "people"

    def test_expand_section_collapses_others(self, accordion_sidebar, qtbot):
        """Test expanding one section collapses all others."""
        # Expand folders first
        accordion_sidebar._expand_section("folders")
        qtbot.wait(50)
        assert accordion_sidebar.section_widgets["folders"].is_expanded()

        # Expand people - folders should collapse
        accordion_sidebar._expand_section("people")
        qtbot.wait(50)
        assert accordion_sidebar.section_widgets["people"].is_expanded()
        assert not accordion_sidebar.section_widgets["folders"].is_expanded()

    def test_expand_section_emits_signal(self, accordion_sidebar, qtbot):
        """Test expanding section emits sectionExpanding signal."""
        with qtbot.waitSignal(accordion_sidebar.sectionExpanding, timeout=1000) as blocker:
            accordion_sidebar._expand_section("dates")

        assert blocker.args == ["dates"]

    def test_expand_unknown_section_logs_warning(self, accordion_sidebar, caplog):
        """Test expanding unknown section logs warning."""
        accordion_sidebar._expand_section("unknown_section")
        assert "Unknown section" in caplog.text

    def test_expand_section_triggers_load(self, accordion_sidebar):
        """Test expanding section triggers section load."""
        section = accordion_sidebar.section_logic["quick"]

        with patch.object(section, 'load_section') as mock_load:
            accordion_sidebar._expand_section("quick")
            assert mock_load.called

    def test_only_one_section_expanded_at_time(self, accordion_sidebar, qtbot):
        """Test only one section can be expanded at a time."""
        # Expand multiple sections sequentially
        for section_id in ["folders", "dates", "people", "quick"]:
            accordion_sidebar._expand_section(section_id)
            qtbot.wait(50)

        # Count expanded sections
        expanded_count = sum(
            1 for widget in accordion_sidebar.section_widgets.values()
            if widget.is_expanded()
        )

        assert expanded_count == 1
        assert accordion_sidebar.expanded_section_id == "quick"


# ============================================================================
# Test Class: Person Selection
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestPersonSelection:
    """Test person selection and toggling behavior."""

    def test_person_selected_emits_signal(self, accordion_sidebar, qtbot):
        """Test selecting person emits selectPerson signal."""
        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000) as blocker:
            accordion_sidebar._on_person_selected("face_john")

        assert blocker.args == ["face_john"]

    def test_person_selected_sets_active_branch(self, accordion_sidebar):
        """Test selecting person sets _active_person_branch."""
        accordion_sidebar._on_person_selected("face_john")
        assert accordion_sidebar._active_person_branch == "face_john"

    def test_person_toggle_emits_empty_string(self, accordion_sidebar, qtbot):
        """Test toggling person selection emits empty string."""
        # Select first
        accordion_sidebar._on_person_selected("face_john")

        # Toggle (select same person again)
        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000) as blocker:
            accordion_sidebar._on_person_selected("face_john")

        assert blocker.args == [""]

    def test_person_toggle_clears_active_branch(self, accordion_sidebar):
        """Test toggling clears _active_person_branch."""
        # Select
        accordion_sidebar._on_person_selected("face_john")
        assert accordion_sidebar._active_person_branch == "face_john"

        # Toggle
        accordion_sidebar._on_person_selected("face_john")
        assert accordion_sidebar._active_person_branch is None

    def test_person_toggle_updates_section_state(self, accordion_sidebar):
        """Test toggling updates people section active branch."""
        people_section = accordion_sidebar.section_logic.get("people")

        # Mock set_active_branch if exists
        if hasattr(people_section, 'set_active_branch'):
            with patch.object(people_section, 'set_active_branch') as mock_set:
                # Select
                accordion_sidebar._on_person_selected("face_john")
                mock_set.assert_called_with("face_john")

                # Toggle
                accordion_sidebar._on_person_selected("face_john")
                mock_set.assert_called_with(None)

    def test_switching_person_changes_active(self, accordion_sidebar, qtbot):
        """Test switching from one person to another updates active branch."""
        # Select John
        accordion_sidebar._on_person_selected("face_john")
        assert accordion_sidebar._active_person_branch == "face_john"

        # Select Jane
        with qtbot.waitSignal(accordion_sidebar.selectPerson, timeout=1000) as blocker:
            accordion_sidebar._on_person_selected("face_jane")

        assert accordion_sidebar._active_person_branch == "face_jane"
        assert blocker.args == ["face_jane"]


# ============================================================================
# Test Class: Section Loading
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestSectionLoading:
    """Test section loading and data handling."""

    def test_trigger_section_load_calls_load_method(self, accordion_sidebar):
        """Test _trigger_section_load calls section's load_section method."""
        section = accordion_sidebar.section_logic["quick"]

        with patch.object(section, 'load_section') as mock_load:
            accordion_sidebar._trigger_section_load("quick")
            assert mock_load.called

    def test_trigger_section_load_skips_if_loading(self, accordion_sidebar):
        """Test _trigger_section_load skips if section is already loading."""
        section = accordion_sidebar.section_logic["people"]

        # Mock is_loading to return True
        with patch.object(section, 'is_loading', return_value=True):
            with patch.object(section, 'load_section') as mock_load:
                accordion_sidebar._trigger_section_load("people")
                assert not mock_load.called

    def test_on_section_loaded_updates_widget(self, accordion_sidebar):
        """Test _on_section_loaded updates section widget."""
        section_id = "quick"
        section = accordion_sidebar.section_logic[section_id]
        test_data = [("today", "Today"), ("yesterday", "Yesterday")]

        # Set current generation
        section._generation = 1

        # Call with matching generation
        with patch.object(section, 'create_content_widget') as mock_create:
            accordion_sidebar._on_section_loaded(section_id, 1, test_data)
            # Should call create_content_widget (indirectly)

    def test_on_section_loaded_discards_stale_data(self, accordion_sidebar, caplog):
        """Test _on_section_loaded discards stale data based on generation."""
        section_id = "people"
        section = accordion_sidebar.section_logic[section_id]

        # Set current generation to 5
        section._generation = 5

        # Call with old generation (3)
        accordion_sidebar._on_section_loaded(section_id, 3, [])

        # Should log discard message
        assert "Discarding stale data" in caplog.text


# ============================================================================
# Test Class: Signal Connections
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestSignalConnections:
    """Test signal connections between sections and accordion."""

    def test_people_section_signals_connected(self, accordion_sidebar):
        """Test people section signals are connected."""
        people_section = accordion_sidebar.section_logic.get("people")

        # Check section has expected signals
        if hasattr(people_section, 'personSelected'):
            assert people_section.personSelected is not None

    def test_quick_section_signals_connected(self, accordion_sidebar):
        """Test quick section signals are connected."""
        quick_section = accordion_sidebar.section_logic.get("quick")

        # Check section has quickDateSelected signal
        assert hasattr(quick_section, 'quickDateSelected')
        assert quick_section.quickDateSelected is not None

    def test_folders_section_signals_connected(self, accordion_sidebar):
        """Test folders section signals are connected."""
        folders_section = accordion_sidebar.section_logic.get("folders")

        # Check section has folderSelected signal
        if hasattr(folders_section, 'folderSelected'):
            assert folders_section.folderSelected is not None


# ============================================================================
# Test Class: Cleanup
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestCleanup:
    """Test resource cleanup."""

    def test_cleanup_calls_section_cleanup(self, accordion_sidebar):
        """Test cleanup calls cleanup on all sections."""
        # Get sections
        sections = list(accordion_sidebar.section_logic.values())

        # Mock cleanup on sections that have it
        with patch.object(sections[0], 'cleanup', create=True) as mock_cleanup:
            accordion_sidebar.cleanup()
            # At least one section cleanup should be attempted

    def test_cleanup_closes_database(self, accordion_sidebar):
        """Test cleanup closes database connection."""
        # Mock db.close()
        with patch.object(accordion_sidebar.db, 'close') as mock_close:
            accordion_sidebar.cleanup()
            assert mock_close.called

    def test_cleanup_handles_errors_gracefully(self, accordion_sidebar, caplog):
        """Test cleanup handles errors without crashing."""
        # Mock db.close to raise error
        with patch.object(accordion_sidebar.db, 'close', side_effect=Exception("Test error")):
            # Should not raise
            accordion_sidebar.cleanup()
            assert "Error closing database" in caplog.text


# ============================================================================
# Test Class: Reload Methods
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestReloadMethods:
    """Test section reload functionality."""

    def test_reload_people_section(self, accordion_sidebar):
        """Test reload_people_section triggers people section load."""
        with patch.object(accordion_sidebar, '_trigger_section_load') as mock_trigger:
            accordion_sidebar.reload_people_section()
            mock_trigger.assert_called_once_with("people")

    def test_reload_all_sections(self, accordion_sidebar):
        """Test reload_all_sections triggers load for all sections."""
        with patch.object(accordion_sidebar, '_trigger_section_load') as mock_trigger:
            accordion_sidebar.reload_all_sections()

            # Should trigger load for each section
            assert mock_trigger.call_count == len(accordion_sidebar.section_logic)


# ============================================================================
# Test Class: Edge Cases
# ============================================================================

@pytest.mark.accordion
@pytest.mark.qt
class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_expand_section_with_none_project_id(self, qapp, accordion_test_db):
        """Test accordion works with None project_id."""
        from ui.accordion_sidebar import AccordionSidebar

        with patch('reference_db.get_db_path', return_value=str(accordion_test_db)):
            sidebar = AccordionSidebar(project_id=None)
            sidebar._expand_section("quick")
            # Should not crash
            sidebar.cleanup()

    def test_person_selected_with_empty_string(self, accordion_sidebar):
        """Test selecting empty string person doesn't crash."""
        # Should not raise
        accordion_sidebar._on_person_selected("")
        assert accordion_sidebar._active_person_branch == ""

    def test_multiple_rapid_section_expansions(self, accordion_sidebar, qtbot):
        """Test rapidly expanding multiple sections doesn't crash."""
        sections = ["folders", "dates", "people", "videos", "devices", "quick"]

        for section_id in sections * 3:  # Cycle through 3 times
            accordion_sidebar._expand_section(section_id)
            qtbot.wait(5)  # Very short delay

        # Should not crash, last section should be expanded
        assert accordion_sidebar.expanded_section_id == "quick"
