# AccordionSidebar Test Suite

Comprehensive test suite for the GoogleLayout AccordionSidebar component.

## Overview

**Total Tests:** 73 tests
- **Unit Tests:** 33 tests
- **Integration Tests:** 22 tests
- **End-to-End Tests:** 18 tests

**Coverage Target:** 80%+ for `ui/accordion_sidebar/` module

---

## Installation

Install test dependencies:

```bash
pip install -r tests/requirements-test.txt
```

Required packages:
- `pytest` - Test framework
- `pytest-qt` - Qt widget testing support
- `pytest-cov` - Code coverage reporting
- `pytest-xdist` - Parallel test execution
- `PySide6` - Qt library

---

## Running Tests

### Run All Accordion Tests

```bash
# All accordion sidebar tests
pytest tests/test_accordion_sidebar_*.py -v

# With coverage
pytest tests/test_accordion_sidebar_*.py --cov=ui.accordion_sidebar --cov-report=html

# Parallel execution (faster)
pytest tests/test_accordion_sidebar_*.py -n auto
```

### Run Specific Test Categories

```bash
# Unit tests only
pytest tests/test_accordion_sidebar_unit.py -v

# Integration tests only
pytest tests/test_accordion_sidebar_integration.py -v

# E2E tests only
pytest tests/test_accordion_sidebar_e2e.py -v
```

### Run Specific Test Classes

```bash
# Test initialization
pytest tests/test_accordion_sidebar_unit.py::TestAccordionSidebarInit -v

# Test person selection
pytest tests/test_accordion_sidebar_unit.py::TestPersonSelection -v

# Test quick dates
pytest tests/test_accordion_sidebar_integration.py::TestQuickSectionIntegration -v
```

### Run Tests by Marker

```bash
# Run only accordion tests
pytest -m accordion

# Run only Qt GUI tests
pytest -m qt

# Skip slow tests
pytest -m "not slow"
```

---

## Test Structure

### 1. conftest_qt.py - Test Infrastructure

Provides fixtures and utilities for Qt testing:

**QApplication Fixtures:**
- `qapp` - Session-scoped QApplication instance

**Database Fixtures:**
- `accordion_test_db` - Test database with schema
- `test_project_id` - Test project ID

**Mock Data Fixtures:**
- `mock_face_clusters` - Mock people/face data
- `mock_folders` - Mock folder hierarchy
- `mock_photos` - Mock photos with dates
- `mock_videos` - Mock video metadata

**AccordionSidebar Fixtures:**
- `accordion_sidebar_factory` - Factory for creating sidebars
- `accordion_sidebar` - Single sidebar instance
- `mock_people_section` - Mocked PeopleSection
- `mock_quick_section` - Mocked QuickSection

**Helper Functions:**
- `wait_for_signal(signal, timeout)` - Wait for Qt signal
- `click_widget(widget, qtbot)` - Click widget helper

### 2. test_accordion_sidebar_unit.py - Unit Tests (33 tests)

Tests individual AccordionSidebar methods in isolation.

**Test Classes:**
- `TestAccordionSidebarInit` - Initialization (6 tests)
- `TestSectionExpansion` - Section expand/collapse (6 tests)
- `TestPersonSelection` - Person selection/toggle (6 tests)
- `TestSectionLoading` - Data loading (4 tests)
- `TestSignalConnections` - Signal wiring (3 tests)
- `TestCleanup` - Resource cleanup (3 tests)
- `TestReloadMethods` - Section reload (2 tests)
- `TestEdgeCases` - Edge cases (3 tests)

**Example Tests:**
- `test_creates_with_project_id` - Verify initialization
- `test_expand_section_collapses_others` - One section at a time
- `test_person_toggle_emits_empty_string` - Toggle behavior
- `test_on_section_loaded_discards_stale_data` - Generation tokens

### 3. test_accordion_sidebar_integration.py - Integration Tests (22 tests)

Tests integration between accordion and other components.

**Test Classes:**
- `TestPeopleSectionIntegration` - People + database (3 tests)
- `TestFoldersSectionIntegration` - Folders + database (2 tests)
- `TestDatesSectionIntegration` - Dates + database (2 tests)
- `TestVideosSectionIntegration` - Videos + database (2 tests)
- `TestQuickSectionIntegration` - Quick dates (3 tests)
- `TestMultiSectionIntegration` - Multi-section scenarios (2 tests)
- `TestDatabaseIntegration` - Database interactions (2 tests)
- `TestSignalFlowIntegration` - Signal propagation (3 tests)
- `TestErrorHandlingIntegration` - Error scenarios (3 tests)

**Example Tests:**
- `test_people_section_loads_from_database` - DB integration
- `test_quick_date_calculations` - Date calculation accuracy
- `test_rapid_section_switching_handles_async_loads` - Race conditions
- `test_large_dataset_loads_efficiently` - Performance

### 4. test_accordion_sidebar_e2e.py - E2E Tests (18 tests)

Tests complete user workflows and real-world scenarios.

**Test Classes:**
- `TestBasicUserWorkflows` - Basic user journeys (4 tests)
- `TestQuickDatesWorkflows` - Quick date scenarios (4 tests)
- `TestNavigationPatterns` - Navigation patterns (3 tests)
- `TestErrorRecoveryWorkflows` - Error recovery (2 tests)
- `TestPerformanceWorkflows` - Performance scenarios (2 tests)
- `TestAccessibilityWorkflows` - Accessibility (1 test)
- `TestRealWorldUsagePatterns` - Real usage (2 tests)

**Example Tests:**
- `test_user_opens_accordion_and_browses_sections` - Full browse flow
- `test_user_filters_by_person` - Person filter workflow
- `test_daily_photo_browsing_workflow` - Daily usage pattern
- `test_user_works_with_large_photo_library` - Large dataset

---

## Test Coverage

Generate HTML coverage report:

```bash
pytest tests/test_accordion_sidebar_*.py --cov=ui.accordion_sidebar --cov-report=html
```

View coverage:

```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```

**Coverage Targets:**
- **Minimum:** 70%
- **Target:** 80%
- **Stretch:** 90%

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Accordion Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r tests/requirements-test.txt
      - name: Run tests
        run: |
          pytest tests/test_accordion_sidebar_*.py --cov=ui.accordion_sidebar --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

---

## Writing New Tests

### Unit Test Template

```python
@pytest.mark.accordion
@pytest.mark.qt
class TestNewFeature:
    """Test new feature."""

    def test_feature_works(self, accordion_sidebar, qtbot):
        """Test feature works correctly."""
        # Arrange
        accordion_sidebar._expand_section("people")
        qtbot.wait(100)

        # Act
        result = accordion_sidebar.some_method()

        # Assert
        assert result == expected_value
```

### Integration Test Template

```python
@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestNewIntegration:
    """Test new integration."""

    def test_integration_with_db(
        self, accordion_sidebar, mock_face_clusters, qtbot
    ):
        """Test integration with database."""
        # Test code here
```

### E2E Test Template

```python
@pytest.mark.accordion
@pytest.mark.qt
@pytest.mark.slow
class TestNewWorkflow:
    """Test new user workflow."""

    def test_user_does_something(self, accordion_sidebar, qtbot):
        """
        Test user workflow.

        User story:
        1. User opens section
        2. User performs action
        3. User sees expected result
        """
        # Test code here
```

---

## Troubleshooting

### QApplication Errors

If you see `"QApplication: invalid style override"`:

```python
# Add this to conftest_qt.py
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
```

### Signal Timeout Errors

If signals timeout:

```python
# Increase timeout
with qtbot.waitSignal(signal, timeout=5000):  # 5 seconds
    # trigger signal
```

### Slow Tests

Run without slow tests:

```bash
pytest tests/test_accordion_sidebar_*.py -m "not slow"
```

Or run tests in parallel:

```bash
pytest tests/test_accordion_sidebar_*.py -n auto
```

### Import Errors

Make sure project root is in PYTHONPATH:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest tests/test_accordion_sidebar_*.py
```

---

## Test Markers

Available markers:

- `@pytest.mark.accordion` - Accordion sidebar tests
- `@pytest.mark.qt` - Qt GUI tests
- `@pytest.mark.slow` - Slow running tests (>1s)

Configure in `pytest.ini` or `pyproject.toml`:

```ini
[pytest]
markers =
    accordion: AccordionSidebar tests
    qt: Qt GUI tests
    slow: Slow running tests
```

---

## Contributing

When adding new features to AccordionSidebar:

1. **Write tests first** (TDD approach)
2. **Add unit tests** for new methods
3. **Add integration tests** for DB interactions
4. **Add E2E tests** for user workflows
5. **Run full test suite** before committing
6. **Check coverage** meets 80% target

---

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-qt documentation](https://pytest-qt.readthedocs.io/)
- [PySide6 testing guide](https://doc.qt.io/qtforpython/)

---

**Test Suite Created:** 2025-12-16
**Last Updated:** 2025-12-16
**Maintained by:** Claude Code
