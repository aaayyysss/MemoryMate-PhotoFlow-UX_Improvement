
import sys
import os
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from main_window_qt import MainWindow
from ui.embedding_stats_dashboard import EmbeddingStatsDashboard
from ui.face_detection_scope_dialog import FaceDetectionScopeDialog
from repository.project_repository import ProjectRepository
from repository.base_repository import DatabaseConnection

def capture_ui():
    print("Starting UI Capture (Offscreen)...")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication(sys.argv)

    # Setup: Create a project if none exists
    db_conn = DatabaseConnection()
    repo = ProjectRepository(db_conn)
    projects = repo.get_all_with_details()

    if not projects:
        repo.create("Test Project", "/tmp", "scan")
        projects = repo.get_all_with_details()

    pid = projects[0]['id']

    # Create Main Window
    mw = MainWindow()
    mw.resize(1280, 720)
    mw.show()

    # 1. Capture MainWindow in whatever its default layout is
    print("Capturing MainWindow (Default Layout)...")
    QApplication.processEvents()
    time.sleep(2) # Wait for layout
    QApplication.processEvents()
    pix = mw.grab()
    pix.save("/home/jules/verification/mainwindow_default.png")

    # 2. Force switch to Current Layout to verify UX-1 shell
    print("Switching to Current Layout...")
    mw.layout_manager.switch_layout("current")
    QApplication.processEvents()
    time.sleep(1)
    QApplication.processEvents()
    pix = mw.grab()
    pix.save("/home/jules/verification/mainwindow_current.png")

    # 3. Verify onboarding state (No project)
    print("Testing onboarding state...")
    # We'll simulate this by creating a fresh MainWindow with no projects (hard to do without wiping DB)
    # Instead, we can just look at the search shell when project is None.
    mw.search_controller.set_active_project(None)
    QApplication.processEvents()
    time.sleep(1)
    pix = mw.grab()
    pix.save("/home/jules/verification/mainwindow_onboarding.png")

    # Restore project
    mw.search_controller.set_active_project(pid)

    # Create and capture FaceDetectionScopeDialog
    print("Capturing FaceDetectionScopeDialog...")
    scope_dialog = FaceDetectionScopeDialog(pid, parent=mw)
    scope_dialog.show()
    QApplication.processEvents()
    time.sleep(1)
    QApplication.processEvents()
    pix = scope_dialog.grab()
    pix.save("/home/jules/verification/scope_dialog.png")
    scope_dialog.close()

    # Create a project with a legacy model to force the upgrade section
    print("Capturing EmbeddingStatsDashboard (Legacy)...")
    legacy_pid = repo.create("Legacy Project", "/tmp", "scan", semantic_model="openai/clip-vit-base-patch32")

    # Mock best available model to be something else to force upgrade section
    from repository.project_repository import ProjectRepository as PR
    original_best = PR._get_best_available_model
    PR._get_best_available_model = lambda self: "openai/clip-vit-large-patch14"

    dashboard = EmbeddingStatsDashboard(legacy_pid, parent=mw)
    dashboard.resize(900, 650)
    dashboard.show()
    QApplication.processEvents()
    time.sleep(2)
    QApplication.processEvents()

    pix = dashboard.grab()
    pix.save("/home/jules/verification/dashboard_upgrade.png")

    # Restore original method
    PR._get_best_available_model = original_best

    dashboard.close()
    mw.close()
    print("UI Capture Finished.")

if __name__ == "__main__":
    if not os.path.exists("/home/jules/verification"):
        os.makedirs("/home/jules/verification")

    try:
        capture_ui()
    except Exception as e:
        print(f"UI Capture failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
