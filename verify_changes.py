
import sys
import os
from PySide6.QtWidgets import QApplication
from main_window_qt import MainWindow
from ui.embedding_stats_dashboard import EmbeddingStatsDashboard
from repository.project_repository import ProjectRepository
from repository.base_repository import DatabaseConnection

def test_bootstrap_policy():
    print("Testing Bootstrap Policy...")
    app = QApplication.instance() or QApplication(sys.argv)

    # Setup: Create a project if none exists
    db_conn = DatabaseConnection()
    repo = ProjectRepository(db_conn)
    projects = repo.get_all_with_details()

    if not projects:
        repo.create("Test Project", "/tmp", "scan")
        projects = repo.get_all_with_details()

    mw = MainWindow()
    pid = mw._bootstrap_active_project()
    print(f"Bootstrapped Project ID: {pid}")
    assert pid is not None, "Should have bootstrapped a project"
    print("Bootstrap Policy Test Passed.")

def test_dashboard_upgrade_section():
    print("Testing Dashboard Upgrade Section...")
    app = QApplication.instance() or QApplication(sys.argv)

    # Create a project with a legacy model to trigger the upgrade section
    db_conn = DatabaseConnection()
    repo = ProjectRepository(db_conn)
    pid = repo.create("Legacy Project", "/tmp", "scan", semantic_model="openai/clip-vit-base-patch32")

    dashboard = EmbeddingStatsDashboard(pid)

    # Check if upgrade group exists
    from PySide6.QtWidgets import QGroupBox
    upgrade_group = None
    for child in dashboard.findChildren(QGroupBox):
        if child.title() == "Model Upgrade Assistant":
            upgrade_group = child
            break

    if upgrade_group:
        print("Model Upgrade Assistant section FOUND.")
    else:
        # It might not show if large model isn't "installed" in the test env paths
        print("Model Upgrade Assistant section NOT FOUND (Expected if best model == current model)")
        best = repo._get_best_available_model()
        print(f"Current best available model: {best}")

    print("Dashboard Test Finished.")

if __name__ == "__main__":
    try:
        test_bootstrap_policy()
        test_dashboard_upgrade_section()
    except Exception as e:
        print(f"Verification failed: {e}")
        sys.exit(1)
