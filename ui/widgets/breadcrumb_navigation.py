"""
BreadcrumbNavigation - Breadcrumb navigation widget with project management.

Extracted from main_window_qt.py (Phase 2, Step 2.1)

Responsibilities:
- Home button that opens project selector/creator
- Breadcrumb trail showing current location (Project > Folder/Date/Tag)
- Clickable segments for navigation to parent levels
- Project management menu (create new, switch projects)

Version: 09.20.00.00
"""

from functools import partial
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel,
    QMenu, QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt
from reference_db import ReferenceDB


class BreadcrumbNavigation(QWidget):
    """
    Phase 2 (High Impact): Breadcrumb navigation widget with project management.
    - Home button: Opens project selector/creator
    - Breadcrumb trail: Shows current location (Project > Folder/Date/Tag)
    - Clickable segments: Navigate to parent levels
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(32)
        self.main_window = parent

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Home icon button - opens project management menu
        self.btn_home = QPushButton("🏠")
        self.btn_home.setFixedSize(28, 28)
        self.btn_home.setToolTip("Project Management (Create/Switch Projects)")
        self.btn_home.setStyleSheet("""
            QPushButton {
                border: none;
                background-color: transparent;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1);
                border-radius: 4px;
            }
        """)
        self.btn_home.clicked.connect(self._show_project_menu)
        layout.addWidget(self.btn_home)

        # Breadcrumb labels container (will be populated dynamically)
        self.breadcrumb_container = QWidget()
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb_container)
        self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb_layout.setSpacing(4)
        layout.addWidget(self.breadcrumb_container)

        layout.addStretch()

        # Store breadcrumb path
        self.breadcrumbs = []

    def _show_project_menu(self):
        """Show project management menu (create new, switch project)."""
        print(f"[Breadcrumb] _show_project_menu() called")

        # CRITICAL FIX: Refresh project list before showing menu
        # This ensures we always show the latest projects
        try:
            from app_services import list_projects
            if hasattr(self.main_window, "_projects"):
                self.main_window._projects = list_projects()
                print(f"[Breadcrumb] Refreshed project list: {len(self.main_window._projects)} projects")
        except Exception as e:
            print(f"[Breadcrumb] Warning: Could not refresh project list: {e}")

        menu = QMenu(self)

        # Add "Create New Project" action
        act_new_project = menu.addAction("📁 Create New Project...")
        act_new_project.triggered.connect(self._create_new_project)

        menu.addSeparator()

        # Add existing projects
        if hasattr(self.main_window, "_projects") and self.main_window._projects:
            print(f"[Breadcrumb] Adding {len(self.main_window._projects)} projects to menu")
            for project in self.main_window._projects:
                proj_id = project.get("id")
                proj_name = project.get("name", f"Project {proj_id}")
                proj_mode = project.get("mode", "scan")

                # CRITICAL FIX: Check if this is the current project
                is_current = False
                if hasattr(self.main_window, "grid") and hasattr(self.main_window.grid, "project_id"):
                    is_current = (proj_id == self.main_window.grid.project_id)

                # Mark current project with checkmark
                action_text = f"{'✓ ' if is_current else '  '}{proj_name} ({proj_mode})"
                action = menu.addAction(action_text)
                action.setData(proj_id)

                # CRITICAL FIX: Use proper closure to capture proj_id
                # Old: lambda checked=False, pid=proj_id: self._switch_project(pid)
                # New: Use functools.partial for proper binding
                action.triggered.connect(partial(self._switch_project, proj_id))

                print(f"[Breadcrumb]   - {action_text} (ID: {proj_id})")
        else:
            print(f"[Breadcrumb] No projects found in main_window._projects")
            # Add a disabled item showing no projects
            no_proj_action = menu.addAction("  (No projects found)")
            no_proj_action.setEnabled(False)

        # Show menu below the home button
        print(f"[Breadcrumb] Showing menu...")
        menu.exec(self.btn_home.mapToGlobal(self.btn_home.rect().bottomLeft()))

    def _create_new_project(self):
        """Create a new project via dialog."""
        project_name, ok = QInputDialog.getText(
            self,
            "Create New Project",
            "Enter project name:",
            text="My New Project"
        )

        if ok and project_name.strip():
            try:
                # Create project with scan mode
                db = ReferenceDB()
                proj_id = db.create_project(project_name.strip(), folder="", mode="scan")

                QMessageBox.information(
                    self,
                    "Project Created",
                    f"Project '{project_name}' created successfully!\n\nProject ID: {proj_id}"
                )

                # Switch to new project
                self._switch_project(proj_id)

                # Refresh project list
                if hasattr(self.main_window, "_refresh_project_list"):
                    self.main_window._refresh_project_list()

            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Failed to create project:\n{e}"
                )

    def _switch_project(self, project_id: int):
        """Switch to a different project."""
        print(f"[Breadcrumb] _switch_project({project_id}) called")

        # CRITICAL FIX: Check if already on this project
        if hasattr(self.main_window, "grid") and hasattr(self.main_window.grid, "project_id"):
            current_id = self.main_window.grid.project_id
            if current_id == project_id:
                print(f"[Breadcrumb] Already on project {project_id}, skipping switch")
                return

        if hasattr(self.main_window, "_on_project_changed_by_id"):
            self.main_window._on_project_changed_by_id(project_id)
        elif hasattr(self.main_window, "project_controller"):
            # Fallback to project controller
            self.main_window.project_controller.switch_project(project_id)
        else:
            print(f"[Breadcrumb] ERROR: No method available to switch projects!")

        print(f"[Breadcrumb] Switched to project ID: {project_id}")

    def set_path(self, segments: list[tuple[str, callable]]):
        """
        Set breadcrumb path with clickable segments.

        Args:
            segments: List of (label, callback) tuples
                     Example: [("My Photos", lambda: navigate_home()),
                              ("2024", lambda: navigate_to_year(2024))]
        """
        print(f"[BreadcrumbNav] set_path() called with {len(segments)} segments")

        try:
            # CRITICAL FIX: Clear widgets safely without processEvents()
            # processEvents() during signal processing causes re-entrant crashes!
            print(f"[BreadcrumbNav] Clearing {self.breadcrumb_layout.count()} existing widgets")
            widgets_to_delete = []
            while self.breadcrumb_layout.count():
                item = self.breadcrumb_layout.takeAt(0)
                if item.widget():
                    widgets_to_delete.append(item.widget())

            # Delete widgets via deleteLater - Qt will handle cleanup safely
            for widget in widgets_to_delete:
                widget.setParent(None)
                widget.deleteLater()

            # REMOVED: QCoreApplication.processEvents() - causes re-entrant crash!
            # Qt's event loop will process deleteLater() at the appropriate time

            print(f"[BreadcrumbNav] Cleared all existing widgets")

            self.breadcrumbs = segments

            for i, (label, callback) in enumerate(segments):
                # Add separator before each segment (except first)
                if i > 0:
                    sep = QLabel("›")
                    sep.setStyleSheet("color: #999; font-size: 12px;")
                    self.breadcrumb_layout.addWidget(sep)

                # Add clickable segment
                btn = QPushButton(label)
                btn.setStyleSheet("""
                    QPushButton {
                        border: none;
                        background-color: transparent;
                        color: #333;
                        font-size: 13px;
                        padding: 4px 8px;
                    }
                    QPushButton:hover {
                        background-color: rgba(0, 0, 0, 0.1);
                        border-radius: 4px;
                        text-decoration: underline;
                    }
                """)
                btn.setCursor(Qt.PointingHandCursor)
                # Phase 1: Emphasize first breadcrumb segment (project)
                if i == 0:
                    btn.setStyleSheet(btn.styleSheet() + "QPushButton { font-weight: 600; }")

                # CRITICAL FIX: Disconnect all previous signals before connecting new one
                try:
                    btn.clicked.disconnect()
                except:
                    pass  # No previous connections

                if callback:
                    btn.clicked.connect(callback)

                # Last segment is bold (current location)
                if i == len(segments) - 1:
                    btn.setStyleSheet(btn.styleSheet() + "QPushButton { font-weight: bold; color: #000; }")

                self.breadcrumb_layout.addWidget(btn)
                print(f"[BreadcrumbNav] Added segment {i}: {label}")

            print(f"[BreadcrumbNav] set_path() completed - {len(segments)} segments added")

        except Exception as e:
            print(f"[BreadcrumbNav] ✗✗✗ ERROR in set_path(): {e}")
            import traceback
            traceback.print_exc()
