#!/usr/bin/env python3
"""
Tests for Name Autocomplete in Bulk Review.

Tests autocomplete functionality for person naming in People Tools.

Author: Claude Code
Date: December 16, 2025
"""

import pytest
from PySide6.QtWidgets import QLineEdit, QCompleter, QApplication
from PySide6.QtCore import Qt


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication for Qt tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestNameAutocomplete:
    """Test suite for name autocomplete functionality."""

    def test_completer_creation(self, qapp):
        """Test that QCompleter can be created with name list."""
        names = ["John Smith", "Jane Doe", "Bob Johnson"]

        line_edit = QLineEdit()
        completer = QCompleter(names)
        line_edit.setCompleter(completer)

        assert line_edit.completer() is not None
        assert line_edit.completer().model().rowCount() == 3

    def test_completer_case_insensitive(self, qapp):
        """Test that completer is case insensitive."""
        names = ["John Smith"]

        line_edit = QLineEdit()
        completer = QCompleter(names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        line_edit.setCompleter(completer)

        assert completer.caseSensitivity() == Qt.CaseInsensitive

    def test_completer_match_contains(self, qapp):
        """Test that completer uses MatchContains filter mode."""
        names = ["John Smith", "Jane Doe"]

        line_edit = QLineEdit()
        completer = QCompleter(names)
        completer.setFilterMode(Qt.MatchContains)
        line_edit.setCompleter(completer)

        assert completer.filterMode() == Qt.MatchContains

    def test_completer_popup_mode(self, qapp):
        """Test that completer uses popup completion mode."""
        names = ["John Smith"]

        line_edit = QLineEdit()
        completer = QCompleter(names)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        line_edit.setCompleter(completer)

        assert completer.completionMode() == QCompleter.PopupCompletion

    def test_completer_with_empty_list(self, qapp):
        """Test that completer works with empty name list."""
        names = []

        line_edit = QLineEdit()
        completer = QCompleter(names)
        line_edit.setCompleter(completer)

        assert line_edit.completer() is not None
        assert line_edit.completer().model().rowCount() == 0

    def test_completer_filters_correctly(self, qapp):
        """Test that completer filters suggestions correctly."""
        names = ["John Smith", "Jane Doe", "John Doe", "Bob Johnson"]

        line_edit = QLineEdit()
        completer = QCompleter(names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        line_edit.setCompleter(completer)

        # Set completion prefix
        completer.setCompletionPrefix("john")

        # Count matches
        matches = []
        for i in range(completer.completionCount()):
            completer.setCurrentRow(i)
            matches.append(completer.currentCompletion())

        assert len(matches) == 3  # "John Smith", "John Doe", "Bob Johnson"
        assert "John Smith" in matches
        assert "John Doe" in matches
        assert "Bob Johnson" in matches

    def test_autocomplete_integration_pattern(self, qapp):
        """Test the integration pattern used in bulk review."""
        # Simulate the pattern used in google_layout.py
        existing_names = ["Alice Brown", "Bob Smith", "Charlie Davis"]

        line_edit = QLineEdit()
        line_edit.setPlaceholderText("Unnamed (12 photos)")

        if existing_names:
            completer = QCompleter(existing_names)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCompletionMode(QCompleter.PopupCompletion)
            line_edit.setCompleter(completer)

        # Verify setup
        assert line_edit.completer() is not None
        assert line_edit.completer().caseSensitivity() == Qt.CaseInsensitive
        assert line_edit.completer().filterMode() == Qt.MatchContains
        assert line_edit.completer().completionMode() == QCompleter.PopupCompletion
        assert line_edit.placeholderText() == "Unnamed (12 photos)"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
