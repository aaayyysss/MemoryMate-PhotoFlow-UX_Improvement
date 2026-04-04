"""
PhotoOperationsController - Handles photo operations (favorite, tag, export, move, delete).

Extracted from main_window_qt.py (Phase 3, Step 3.1)

Responsibilities:
- Toggle favorite tag for selected photos
- Add tags to selected photos
- Export photos to external folder
- Move photos to different folders
- Delete photos from database and/or disk

Version: 09.20.00.00
"""

from PySide6.QtWidgets import QMessageBox, QFileDialog, QInputDialog
from reference_db import ReferenceDB


class PhotoOperationsController:
    """
    Controller for photo operations triggered from selection toolbar or menus.

    Handles:
    - Favorite/unfavorite photos
    - Tag assignment
    - Export (copy) to folder
    - Move to folder
    - Delete from database and/or disk
    """

    def __init__(self, main_window):
        """
        Initialize controller with reference to main window.

        Args:
            main_window: MainWindow instance for accessing grid, statusBar, etc.
        """
        self.main = main_window

    def toggle_favorite_selection(self):
        """
        Phase 2.3: Toggle favorite tag for all selected photos.
        Called from selection toolbar.
        """
        paths = self.main.grid.get_selected_paths()
        if not paths:
            return

        # Check if any photo is already favorited
        db = ReferenceDB()
        has_favorite = False
        for path in paths:
            tags = db.get_tags_for_paths([path], self.main.grid.project_id).get(path, [])
            if "favorite" in tags:
                has_favorite = True
                break

        # Toggle: if any is favorite, unfavorite all; otherwise favorite all
        if has_favorite:
            # Unfavorite all
            for path in paths:
                db.remove_tag(path, "favorite", self.main.grid.project_id)
            msg = f"Removed favorite from {len(paths)} photo(s)"
        else:
            # Favorite all
            for path in paths:
                db.add_tag(path, "favorite", self.main.grid.project_id)
            msg = f"Added {len(paths)} photo(s) to favorites"

        # Refresh grid to show updated tag icons
        if hasattr(self.main.grid, "_refresh_tags_for_paths"):
            self.main.grid._refresh_tags_for_paths(paths)
        if hasattr(self.main.grid, 'tagsChanged'):
            self.main.grid.tagsChanged.emit()

        self.main.statusBar().showMessage(msg, 3000)
        print(f"[Favorite] {msg}")

    def add_tag_to_selection(self):
        """Prompt for a tag name and assign to selected photos."""
        paths = self.main.grid.get_selected_paths()
        if not paths:
            return

        name, ok = QInputDialog.getText(self.main, "Add Tag", "Tag name:")
        if ok and name.strip():
            try:
                from services.tag_service import get_tag_service
                svc = get_tag_service()
                svc.assign_tags_bulk(paths, name.strip(), self.main.grid.project_id)
                # Update grid tags overlay without full reload
                if hasattr(self.main.grid, '_refresh_tags_for_paths'):
                    self.main.grid._refresh_tags_for_paths(paths)
                if hasattr(self.main.grid, 'tagsChanged'):
                    self.main.grid.tagsChanged.emit()
                self.main.statusBar().showMessage(f"Tagged {len(paths)} photo(s) with '{name.strip()}'", 3000)
            except Exception as e:
                QMessageBox.critical(self.main, "Tag Failed", str(e))

    def export_selection_to_folder(self):
        """Export selected photos to a chosen folder (copies)."""
        paths = self.main.grid.get_selected_paths()
        if not paths:
            return
        folder = QFileDialog.getExistingDirectory(self.main, "Export to Folder")
        if not folder:
            return
        import shutil, os
        ok, fail = 0, 0
        for p in paths:
            try:
                shutil.copy2(p, os.path.join(folder, os.path.basename(p)))
                ok += 1
            except Exception:
                fail += 1
        self.main.statusBar().showMessage(f"Exported {ok}, failed {fail}", 5000)

    def move_selection_to_folder(self):
        """Assign selected photos to a folder (by folder_id)."""
        paths = self.main.grid.get_selected_paths()
        if not paths:
            return

        folder_id, ok = QInputDialog.getInt(self.main, "Move to Folder", "Folder ID:", 0, 0)
        if not ok:
            return
        try:
            db = ReferenceDB()
            for p in paths:
                db.set_folder_for_image(p, folder_id)
            self.main.grid.reload()
            self.main.statusBar().showMessage(f"Moved {len(paths)} photo(s) to folder {folder_id}", 3000)
        except Exception as e:
            QMessageBox.critical(self.main, "Move Failed", str(e))

    def request_delete_from_selection(self):
        """Get selected paths and request deletion confirmation."""
        paths = []
        try:
            if hasattr(self.main, 'grid') and hasattr(self.main.grid, 'get_selected_paths'):
                paths = self.main.grid.get_selected_paths() or []
        except Exception:
            paths = []
        if not paths:
            return
        self.confirm_delete(paths)

    def confirm_delete(self, paths: list[str]):
        """Delete photos from database (and optionally from disk)."""
        if not paths:
            return

        # Ask user about deletion scope
        msg = QMessageBox(self.main)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Delete Photos")
        msg.setText(f"Delete {len(paths)} photo(s)?")
        msg.setInformativeText("Choose deletion scope:")

        # Add custom buttons
        db_only_btn = msg.addButton("Database Only", QMessageBox.ActionRole)
        db_and_files_btn = msg.addButton("Database && Files", QMessageBox.DestructiveRole)
        cancel_btn = msg.addButton(QMessageBox.Cancel)

        msg.setDefaultButton(db_only_btn)
        msg.exec()

        clicked = msg.clickedButton()

        if clicked == cancel_btn or clicked is None:
            return

        delete_files = (clicked == db_and_files_btn)

        # Import and use deletion service
        from services import PhotoDeletionService
        deletion_service = PhotoDeletionService()

        try:
            result = deletion_service.delete_photos(
                paths=paths,
                delete_files=delete_files,
                invalidate_cache=True
            )

            # Show result summary
            summary = f"Deleted {result.photos_deleted_from_db} photos from database"
            if delete_files:
                summary += f"\nDeleted {result.files_deleted_from_disk} files from disk"
                if result.files_not_found > 0:
                    summary += f"\n{result.files_not_found} files not found"

            if result.errors:
                summary += f"\n\nErrors:\n" + "\n".join(result.errors[:5])
                QMessageBox.warning(self.main, "Deletion Completed with Errors", summary)
            else:
                QMessageBox.information(self.main, "Deletion Successful", summary)

            # Reload grid to reflect changes
            self.main.grid.reload()

        except Exception as e:
            QMessageBox.critical(
                self.main,
                "Deletion Failed",
                f"Failed to delete photos: {e}"
            )
