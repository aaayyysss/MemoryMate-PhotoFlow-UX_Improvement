#!/usr/bin/env python3
"""Location Editor Integration

Helper functions for integrating location editing into photo context menus
and other UI components.

Usage:
    from ui.location_editor_integration import edit_photo_location

    # In your context menu handler:
    def on_edit_location():
        edit_photo_location(photo_path, parent_widget)
"""

import logging
from pathlib import Path
from typing import Optional
from PySide6.QtWidgets import QWidget, QMessageBox

logger = logging.getLogger(__name__)


def get_photo_location(photo_path: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Get current GPS location for a photo from database.

    Args:
        photo_path: Path to photo file

    Returns:
        Tuple of (latitude, longitude, location_name) or (None, None, None)
    """
    try:
        from reference_db import ReferenceDB
        from repository.photo_repository import PhotoRepository

        db = ReferenceDB()

        # CRITICAL FIX: Normalize path before querying (database stores normalized paths)
        photo_repo = PhotoRepository()
        normalized_path = photo_repo._normalize_path(photo_path)

        with db._connect() as conn:
            cur = conn.cursor()

            # Check if GPS columns exist (Row objects use dict-like access)
            existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
            if 'gps_latitude' not in existing_cols or 'gps_longitude' not in existing_cols:
                return (None, None, None)

            # Get GPS data for photo (using normalized path)
            cur.execute("""
                SELECT gps_latitude, gps_longitude, location_name
                FROM photo_metadata
                WHERE path = ?
            """, (normalized_path,))

            row = cur.fetchone()
            if row:
                return (row['gps_latitude'], row['gps_longitude'], row['location_name'])

            return (None, None, None)

    except Exception as e:
        logger.error(f"[LocationEditor] Failed to get photo location: {e}")
        return (None, None, None)


def save_photo_location(photo_path: str, latitude: Optional[float],
                       longitude: Optional[float], location_name: Optional[str]) -> bool:
    """
    Save GPS location for a photo to both database AND photo file EXIF metadata.

    CRITICAL FIX: This now writes GPS data to TWO places:
    1. Application database (for quick queries, Locations section, etc.)
    2. Photo file EXIF metadata (for persistence across database resets)

    Args:
        photo_path: Path to photo file
        latitude: GPS latitude or None to clear
        longitude: GPS longitude or None to clear
        location_name: Location name or None

    Returns:
        True if successful, False otherwise
    """
    try:
        from reference_db import ReferenceDB
        from services.exif_gps_writer import write_gps_to_exif

        # Step 1: Update database (existing behavior)
        db = ReferenceDB()
        db.update_photo_gps(photo_path, latitude, longitude, location_name)

        # Step 2: Write GPS data to photo file EXIF metadata (NEW - CRITICAL FIX)
        # This ensures GPS data persists with the photo file, not just in database
        exif_success = write_gps_to_exif(photo_path, latitude, longitude)

        if not exif_success:
            logger.warning(
                f"[LocationEditor] GPS written to database but FAILED to write to photo file EXIF. "
                f"Location will be lost if database is cleared. Photo: {Path(photo_path).name}"
            )

        logger.info(
            f"[LocationEditor] Saved location for {Path(photo_path).name}: "
            f"({latitude}, {longitude}) - {location_name} "
            f"(DB: ✓, EXIF: {'✓' if exif_success else '✗'})"
        )

        # SPRINT 2 ENHANCEMENT: Add location to recent locations for quick reuse
        # Only add if coordinates and name are provided (not clearing)
        if latitude is not None and longitude is not None and location_name:
            try:
                from settings_manager_qt import SettingsManager
                sm = SettingsManager()
                sm.add_recent_location(location_name, latitude, longitude)
                logger.debug(f"[LocationEditor] Added to recent locations: {location_name}")
            except Exception as e:
                logger.warning(f"[LocationEditor] Failed to add to recent locations: {e}")
                # Don't fail the whole operation if recents update fails

        return True

    except Exception as e:
        logger.error(f"[LocationEditor] Failed to save photo location: {e}")
        return False


def edit_photo_location(photo_path: str, parent: Optional[QWidget] = None) -> bool:
    """
    Show location editor dialog for a photo.

    This is the main entry point for editing photo locations from context menus.

    Args:
        photo_path: Path to photo file
        parent: Parent widget for dialog

    Returns:
        True if location was changed, False if cancelled or error
    """
    try:
        from ui.location_editor_dialog import LocationEditorDialog

        # Get current location
        current_lat, current_lon, current_name = get_photo_location(photo_path)

        # Show editor dialog
        dialog = LocationEditorDialog(
            photo_path=photo_path,
            current_lat=current_lat,
            current_lon=current_lon,
            current_name=current_name,
            parent=parent
        )

        # Connect save signal
        location_saved = [False]  # Use list for closure

        def on_location_saved(lat, lon, name):
            success = save_photo_location(photo_path, lat, lon, name)
            if success:
                location_saved[0] = True

                if lat is not None and lon is not None:
                    QMessageBox.information(
                        parent,
                        "Location Saved",
                        f"✓ Location updated successfully!\n\n"
                        f"Coordinates: ({lat:.6f}, {lon:.6f})\n"
                        f"Location: {name if name else 'Not specified'}\n\n"
                        f"The photo will now appear in the Locations section."
                    )
                else:
                    QMessageBox.information(
                        parent,
                        "Location Cleared",
                        "✓ Location data removed from photo."
                    )
            else:
                QMessageBox.critical(
                    parent,
                    "Error",
                    "Failed to save location data.\nPlease check the logs for details."
                )

        dialog.locationSaved.connect(on_location_saved)

        # Show dialog
        result = dialog.exec()

        return location_saved[0]

    except Exception as e:
        logger.error(f"[LocationEditor] Failed to show dialog: {e}")
        QMessageBox.critical(
            parent,
            "Error",
            f"Failed to open location editor:\n{e}"
        )
        return False


def edit_photos_location_batch(photo_paths: list[str], parent: Optional[QWidget] = None) -> bool:
    """
    Show location editor dialog for multiple photos (batch editing).

    Follows Google Photos pattern:
    - User selects multiple photos with same or different locations
    - Opens single dialog to set location for all selected photos
    - Applies the same location to all photos at once

    Args:
        photo_paths: List of photo file paths
        parent: Parent widget for dialog

    Returns:
        True if location was changed for any photo, False if cancelled or error
    """
    if not photo_paths:
        return False

    try:
        from ui.location_editor_dialog import LocationEditorDialog

        # Check if all photos have the same location (common case)
        first_lat, first_lon, first_name = get_photo_location(photo_paths[0])
        all_same = True

        for path in photo_paths[1:]:
            lat, lon, name = get_photo_location(path)
            if lat != first_lat or lon != first_lon or name != first_name:
                all_same = False
                break

        # Show dialog with current location if all photos have same location
        dialog = LocationEditorDialog(
            photo_path=f"{len(photo_paths)} photos",  # Show count instead of single filename
            current_lat=first_lat if all_same else None,
            current_lon=first_lon if all_same else None,
            current_name=first_name if all_same else None,
            parent=parent,
            batch_mode=True,
            batch_count=len(photo_paths),
            photo_paths=photo_paths  # SPRINT 2: Pass paths for thumbnail preview
        )

        # Connect save signal
        location_saved = [False]  # Use list for closure
        success_count = [0]
        failed_count = [0]

        def on_location_saved(lat, lon, name):
            """Apply location to all photos in batch."""
            from PySide6.QtWidgets import QProgressDialog
            from PySide6.QtCore import Qt

            # SPRINT 2 ENHANCEMENT: Show progress dialog for batches > 10 photos
            progress_dialog = None
            show_progress = len(photo_paths) > 10

            if show_progress:
                progress_dialog = QProgressDialog(
                    "Updating GPS location...",
                    "Cancel",
                    0,
                    len(photo_paths),
                    parent
                )
                progress_dialog.setWindowTitle("Batch Location Update")
                progress_dialog.setWindowModality(Qt.WindowModal)
                progress_dialog.setMinimumDuration(0)  # Show immediately
                progress_dialog.setAutoClose(False)  # Manual close
                progress_dialog.setAutoReset(False)
                progress_dialog.setValue(0)

            cancelled = [False]  # Track cancellation

            for idx, photo_path in enumerate(photo_paths):
                # Check for cancellation
                if progress_dialog and progress_dialog.wasCanceled():
                    cancelled[0] = True
                    logger.info(f"[LocationEditor] Batch update cancelled after {idx} photos")
                    break

                try:
                    # Update progress dialog
                    if progress_dialog:
                        photo_name = Path(photo_path).name
                        progress_dialog.setLabelText(
                            f"Updating GPS location...\n\n"
                            f"Photo {idx + 1} of {len(photo_paths)}: {photo_name}\n\n"
                            f"✓ Success: {success_count[0]}  ⚠ Failed: {failed_count[0]}"
                        )
                        progress_dialog.setValue(idx)

                    # Save location
                    success = save_photo_location(photo_path, lat, lon, name)
                    if success:
                        success_count[0] += 1
                    else:
                        failed_count[0] += 1

                except Exception as e:
                    logger.error(f"[LocationEditor] Batch save failed for {Path(photo_path).name}: {e}")
                    failed_count[0] += 1

            # Close progress dialog
            if progress_dialog:
                progress_dialog.setValue(len(photo_paths))
                progress_dialog.close()

            # Show summary
            if cancelled[0]:
                # User cancelled - show partial results
                msg = f"⚠ Batch update cancelled\n\n"
                msg += f"✓ {success_count[0]} photo(s) updated successfully\n"
                if failed_count[0] > 0:
                    msg += f"⚠ {failed_count[0]} photo(s) failed\n"
                remaining = len(photo_paths) - success_count[0] - failed_count[0]
                if remaining > 0:
                    msg += f"• {remaining} photo(s) not processed"

                QMessageBox.warning(parent, "Batch Update Cancelled", msg)

                # Mark as saved if at least some photos succeeded
                if success_count[0] > 0:
                    location_saved[0] = True

            elif success_count[0] > 0:
                location_saved[0] = True

                if lat is not None and lon is not None:
                    msg = f"✓ Location updated for {success_count[0]} photo(s)!\n\n"
                    msg += f"Coordinates: ({lat:.6f}, {lon:.6f})\n"
                    msg += f"Location: {name if name else 'Not specified'}\n\n"
                    msg += f"These photos will now appear in the Locations section."
                else:
                    msg = f"✓ Location data removed from {success_count[0]} photo(s)."

                if failed_count[0] > 0:
                    msg += f"\n\n⚠ {failed_count[0]} photo(s) failed to update."

                QMessageBox.information(parent, "Batch Location Update", msg)
            else:
                QMessageBox.critical(
                    parent,
                    "Error",
                    f"Failed to save location data for all {len(photo_paths)} photos.\n"
                    f"Please check the logs for details."
                )

        dialog.locationSaved.connect(on_location_saved)

        # Show dialog
        result = dialog.exec()

        return location_saved[0]

    except Exception as e:
        logger.error(f"[LocationEditor] Failed to show batch dialog: {e}")
        QMessageBox.critical(
            parent,
            "Error",
            f"Failed to open location editor:\n{e}"
        )
        return False


# Example: Adding to photo context menu
def create_location_menu_action(photo_path: str, parent: QWidget):
    """
    Create a QAction for "Edit Location" context menu item.

    Example usage:
        from ui.location_editor_integration import create_location_menu_action

        # In your photo grid/list widget:
        def create_context_menu(photo_path):
            menu = QMenu()

            # ... other actions ...

            location_action = create_location_menu_action(photo_path, self)
            menu.addAction(location_action)

            return menu

    Args:
        photo_path: Path to photo file
        parent: Parent widget

    Returns:
        QAction configured for location editing
    """
    from PySide6.QtGui import QAction

    action = QAction("📍 Edit Location...", parent)
    action.setToolTip("Add or edit GPS location for this photo")
    action.triggered.connect(lambda: edit_photo_location(photo_path, parent))

    return action


if __name__ == '__main__':
    # Test the integration
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Test with a sample photo path
    test_photo = "/path/to/test/photo.jpg"
    print(f"Testing location editor for: {test_photo}")

    # Get current location
    lat, lon, name = get_photo_location(test_photo)
    print(f"Current location: ({lat}, {lon}) - {name}")

    # Show editor
    result = edit_photo_location(test_photo)
    print(f"Edit result: {result}")

    sys.exit(0)
