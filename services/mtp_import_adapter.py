"""
MTP Import Adapter - Bridge between MTP devices and import workflow

Adapts MTP device access to work with the DeviceImportService infrastructure.
Since MTP devices require special Shell COM API access, this adapter:

1. Enumerates files from MTP device (via Shell COM API)
2. Creates DeviceMediaFile objects compatible with import dialog
3. Copies selected files from MTP to library during import
4. Tracks device ID and folder for proper organization

Usage:
    adapter = MTPImportAdapter(db, project_id)
    media_files = adapter.enumerate_mtp_folder(mtp_path, device_name, folder_name)
    # Show in import dialog
    imported_paths = adapter.import_selected_files(selected_files, import_options)
"""

import os
import tempfile
import logging
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
from dataclasses import dataclass

from services.device_import_service import DeviceMediaFile

logger = logging.getLogger(__name__)


@dataclass
class MTPFileInfo:
    """Information about a file on MTP device"""
    mtp_path: str           # Shell namespace path
    filename: str           # Original filename
    size_bytes: int         # File size
    modified_date: datetime # Last modified date
    is_folder: bool = False


class MTPImportAdapter:
    """Adapter for importing from MTP devices using Shell COM API"""

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp'
    }

    def __init__(self, db, project_id: int):
        """
        Initialize MTP import adapter.

        Args:
            db: ReferenceDB instance
            project_id: Target project ID
        """
        self.db = db
        self.project_id = project_id

    def enumerate_mtp_folder(
        self,
        mtp_path: str,
        device_name: str,
        folder_name: str,
        max_files: int = 500
    ) -> List[DeviceMediaFile]:
        """
        Enumerate files in MTP device folder without copying.

        This creates DeviceMediaFile objects that can be shown in import dialog.
        Files are NOT copied yet - just enumerated for preview.

        Args:
            mtp_path: Shell namespace path to device folder
            device_name: Device name (e.g., "A54 von Ammar")
            folder_name: Folder name (e.g., "Camera")
            max_files: Maximum files to enumerate

        Returns:
            List of DeviceMediaFile objects for import dialog
        """
        print(f"[MTPAdapter] Enumerating MTP folder: {folder_name} on {device_name}")
        print(f"[MTPAdapter] Path: {mtp_path}")

        try:
            # Import COM libraries
            import win32com.client
            import pythoncom

            # Initialize COM in this thread
            pythoncom.CoInitialize()

            try:
                # Navigate to device folder using "This PC" approach
                shell = win32com.client.Dispatch("Shell.Application")
                computer = shell.Namespace(17)  # This PC

                if not computer:
                    raise Exception("Cannot access 'This PC' namespace")

                # Find device and navigate to folder
                folder = self._navigate_to_mtp_folder(shell, computer, mtp_path)

                if not folder:
                    raise Exception(f"Cannot access folder: {mtp_path}")

                print(f"[MTPAdapter] Successfully accessed folder")

                # Enumerate files
                media_files = []
                file_count = 0

                items = folder.Items()
                for item in items:
                    if file_count >= max_files:
                        print(f"[MTPAdapter] Reached max files limit ({max_files})")
                        break

                    if not item.IsFolder:
                        filename = item.Name
                        name_lower = filename.lower()

                        # Check if it's a media file
                        if any(name_lower.endswith(ext) for ext in self.MEDIA_EXTENSIONS):
                            # Get file info
                            try:
                                size = item.Size if hasattr(item, 'Size') else 0
                                modified = item.ModifyDate if hasattr(item, 'ModifyDate') else datetime.now()
                                if isinstance(modified, str):
                                    try:
                                        modified = datetime.fromisoformat(modified)
                                    except (ValueError, TypeError) as e:
                                        logger.debug(f"Failed to parse MTP date '{modified}' for {filename}: {e}, using current time")
                                        modified = datetime.now()

                                # Create temp path for thumbnail (not actual file yet)
                                # We'll use item.Path as identifier
                                temp_path = f"mtp://{device_name}/{folder_name}/{filename}"

                                # Create DeviceMediaFile
                                media_file = DeviceMediaFile(
                                    path=temp_path,  # Virtual path for now
                                    filename=filename,
                                    size_bytes=size,
                                    modified_date=modified,
                                    already_imported=False,
                                    device_folder=folder_name
                                )

                                # Store actual MTP path for later import
                                media_file.mtp_item_path = item.Path  # Custom attribute

                                media_files.append(media_file)
                                file_count += 1

                                if file_count % 10 == 0:
                                    print(f"[MTPAdapter] Enumerated {file_count} files...")

                            except Exception as e:
                                print(f"[MTPAdapter] Error getting info for {filename}: {e}")
                                continue

                print(f"[MTPAdapter] ✓ Enumerated {len(media_files)} media files")
                return media_files

            finally:
                pythoncom.CoUninitialize()

        except Exception as e:
            print(f"[MTPAdapter] ERROR enumerating MTP folder: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _navigate_to_mtp_folder(self, shell, computer, target_path: str):
        """
        Navigate from 'This PC' to target MTP folder.

        Args:
            shell: Shell.Application COM object
            computer: Computer folder namespace
            target_path: Target MTP path

        Returns:
            Folder object or None
        """
        try:
            # Find device
            device_folder = None
            storage_folder = None

            for item in computer.Items():
                if item.IsFolder and not item.IsFileSystem:
                    if item.Path and item.Path in target_path:
                        device_folder = shell.Namespace(item.Path)

                        if device_folder:
                            # Find storage location
                            storage_items = device_folder.Items()
                            for storage_item in storage_items:
                                if storage_item.IsFolder:
                                    if storage_item.Path and storage_item.Path in target_path:
                                        storage_folder = storage_item.GetFolder
                                        break
                            if storage_folder:
                                break

            if not storage_folder:
                return None

            # Navigate through subfolders
            folder = storage_folder

            if "}" in target_path:
                path_parts = target_path.split("}")
                if len(path_parts) > 1:
                    subfolder_path = path_parts[-1].strip("\\")
                    if subfolder_path:
                        subfolders = [p for p in subfolder_path.split("\\") if p]

                        for subfolder_name in subfolders:
                            found = False
                            items = folder.Items()
                            for item in items:
                                if item.IsFolder and item.Name == subfolder_name:
                                    folder = item.GetFolder
                                    found = True
                                    break

                            if not found:
                                return None

            return folder

        except Exception as e:
            print(f"[MTPAdapter] Error navigating to folder: {e}")
            return None

    def import_selected_files(
        self,
        mtp_path: str,
        selected_files: List[DeviceMediaFile],
        device_name: str,
        folder_name: str,
        import_date: Optional[datetime] = None,
        progress_callback=None
    ) -> List[str]:
        """
        Import selected files from MTP device to library with duplicate detection.

        Copies files from device to proper library structure and adds to database.
        Automatically skips files that are already imported to prevent duplicates.

        Args:
            mtp_path: MTP folder path to import from
            selected_files: List of DeviceMediaFile objects to import
            device_name: Device name for organization
            folder_name: Folder name for organization
            import_date: Import date (defaults to now)
            progress_callback: Optional callback(stage, current, total, detail) for progress updates

        Returns:
            List of imported file paths in library
        """
        if not import_date:
            import_date = datetime.now()

        import_date_str = import_date.strftime("%Y-%m-%d")

        # Prepare destination directory
        # Structure: Device_Imports/{Device}/{Folder}/{Date}/
        # Use current working directory as base (aligns with existing scan repository pattern)
        cwd = Path.cwd()
        dest_base = cwd / "Device_Imports"
        device_safe = self._sanitize_filename(device_name)
        folder_safe = self._sanitize_filename(folder_name)

        dest_folder = dest_base / device_safe / folder_safe / import_date_str
        dest_folder.mkdir(parents=True, exist_ok=True)

        print(f"[MTPAdapter] Importing to: {dest_folder}")

        # DUPLICATE DETECTION: Check which files already exist in database
        new_files = []
        duplicate_files = []

        for media_file in selected_files:
            expected_path = dest_folder / media_file.filename

            # Check if file already in database (by exact path)
            is_duplicate = self._check_if_imported(str(expected_path))

            if is_duplicate:
                duplicate_files.append(media_file)
                print(f"[MTPAdapter] ⊗ Skipping duplicate: {media_file.filename}")
            else:
                new_files.append(media_file)

        # Report duplicate detection results
        print(f"[MTPAdapter] Duplicate detection: {len(new_files)} new, {len(duplicate_files)} duplicates")

        if not new_files:
            print(f"[MTPAdapter] All files already imported. Nothing to do.")
            return []

        imported_paths = []

        try:
            # Import COM libraries
            import win32com.client
            import pythoncom

            # Initialize COM
            pythoncom.CoInitialize()

            try:
                shell = win32com.client.Dispatch("Shell.Application")
                dest_namespace = shell.Namespace(str(dest_folder))

                if not dest_namespace:
                    raise Exception(f"Cannot access destination folder: {dest_folder}")

                # Navigate to source MTP folder (same way as enumeration)
                # We need to navigate once, then find items by filename
                computer = shell.Namespace(17)  # This PC
                if not computer:
                    raise Exception("Cannot access 'This PC' namespace")

                # Navigate to MTP folder using the original mtp_path
                # We'll extract it from the first file's path
                if not selected_files:
                    print(f"[MTPAdapter] No files to import")
                    return imported_paths

                # Navigate to MTP folder using the same method as enumeration
                # This ensures we can access the files consistently
                source_folder = self._navigate_to_mtp_folder(shell, computer, mtp_path)

                if not source_folder:
                    raise Exception("Cannot navigate to source MTP folder during import")

                print(f"[MTPAdapter] Successfully accessed source folder for import")

                # Get all items from source folder once
                source_items_dict = {}
                for item in source_folder.Items():
                    if not item.IsFolder:
                        source_items_dict[item.Name] = item

                # Import only new files (skip duplicates)
                for idx, media_file in enumerate(new_files, 1):
                    print(f"[MTPAdapter] Importing {idx}/{len(new_files)}: {media_file.filename}")

                    # Report progress
                    if progress_callback:
                        progress_callback("Copying", idx, len(new_files), media_file.filename)

                    try:
                        # Find source item by filename
                        source_item = source_items_dict.get(media_file.filename)

                        if source_item:
                            # Copy file
                            dest_namespace.CopyHere(source_item, 4 | 16)

                            # Wait for copy to complete
                            expected_path = dest_folder / media_file.filename
                            import time
                            max_wait = 30
                            waited = 0

                            while waited < max_wait:
                                if expected_path.exists():
                                    print(f"[MTPAdapter] ✓ Copied {media_file.filename}")
                                    imported_paths.append(str(expected_path))

                                    # Add to database
                                    self._add_to_database(
                                        expected_path,
                                        device_name,
                                        folder_name,
                                        import_date
                                    )
                                    break

                                time.sleep(0.1)
                                waited += 0.1
                            else:
                                print(f"[MTPAdapter] ✗ Timeout importing {media_file.filename}")
                        else:
                            print(f"[MTPAdapter] ✗ Cannot find source item: {media_file.filename}")

                    except Exception as e:
                        print(f"[MTPAdapter] ✗ Error importing {media_file.filename}: {e}")
                        continue

                print(f"[MTPAdapter] ✓ Import complete: {len(imported_paths)}/{len(new_files)} files (skipped {len(duplicate_files)} duplicates)")

                # AUTO-ORGANIZATION: Organize imported files into Folders and Dates sections
                if imported_paths:
                    print(f"[MTPAdapter] Auto-organizing {len(imported_paths)} imported files...")
                    self._organize_imported_files(
                        imported_paths=imported_paths,
                        device_name=device_name,
                        folder_name=folder_name,
                        progress_callback=progress_callback
                    )

                return imported_paths

            finally:
                pythoncom.CoUninitialize()

        except Exception as e:
            print(f"[MTPAdapter] ERROR during import: {e}")
            import traceback
            traceback.print_exc()
            return imported_paths

    def _check_if_imported(self, file_path: str) -> bool:
        """
        Check if a file has already been imported to the database.

        Args:
            file_path: Absolute path to file

        Returns:
            True if file already exists in project_images for this project
        """
        try:
            with self.db._connect() as conn:
                cur = conn.cursor()
                # Check if this exact path exists for this project in any branch
                cur.execute("""
                    SELECT COUNT(*) FROM project_images
                    WHERE project_id = ? AND LOWER(image_path) = LOWER(?)
                """, (self.project_id, file_path))
                count = cur.fetchone()[0]
                return count > 0
        except Exception as e:
            print(f"[MTPAdapter] Error checking for duplicate: {e}")
            return False

    def _add_to_database(
        self,
        file_path: Path,
        device_name: str,
        folder_name: str,
        import_date: datetime
    ):
        """
        Add imported file to database.

        Args:
            file_path: Path to imported file
            device_name: Source device name
            folder_name: Source folder name
            import_date: Import date
        """
        try:
            # Create branch_key for device folder organization
            # Format: "device_folder:Camera [A54 von Ammar]"
            device_branch_key = f"device_folder:{folder_name} [{device_name}]"

            # Add to project_images table using ReferenceDB method
            # Add to "all" branch first (so it appears in All Photos)
            image_id = self.db.add_project_image(
                project_id=self.project_id,
                image_path=str(file_path),
                branch_key="all",
                label=None
            )

            # Also add to device-specific branch
            self.db.add_project_image(
                project_id=self.project_id,
                image_path=str(file_path),
                branch_key=device_branch_key,
                label=None
            )

            print(f"[MTPAdapter] ✓ Added to database: {file_path.name} (id={image_id}, branches=['all', '{device_branch_key}'])")

        except Exception as e:
            print(f"[MTPAdapter] ✗ Error adding to database: {e}")
            import traceback
            traceback.print_exc()

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename for filesystem compatibility"""
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip()

    def _organize_imported_files(
        self,
        imported_paths: List[str],
        device_name: str,
        folder_name: str,
        progress_callback=None
    ):
        """
        Organize imported files into Folders and Dates sections.

        This method:
        1. Parses EXIF dates from all files
        2. Creates folder hierarchy (Device_Imports → Device → Folder → Date)
        3. Adds files to photo_metadata with EXIF dates for date organization
        4. Groups files by capture date

        Args:
            imported_paths: List of imported file paths
            device_name: Device name (e.g., "A54 von Ammar")
            folder_name: Folder name (e.g., "Camera", "Screenshots")
            progress_callback: Optional callback(stage, current, total, detail) for progress updates
        """
        try:
            from services.exif_parser import EXIFParser
            from PIL import Image
            import os

            parser = EXIFParser()

            # Group files by capture date
            files_by_date = {}  # {date_str: [file_paths]}

            print(f"[MTPAdapter]   Parsing EXIF dates...")
            if progress_callback:
                progress_callback("Organizing", 0, 100, "Parsing dates from photos...")

            for idx, file_path in enumerate(imported_paths, 1):
                try:
                    # Parse capture date
                    capture_date = parser.get_capture_date(file_path)
                    date_key = capture_date.strftime("%Y-%m-%d")

                    if date_key not in files_by_date:
                        files_by_date[date_key] = []
                    files_by_date[date_key].append(file_path)

                except Exception as e:
                    print(f"[MTPAdapter]   ✗ Error parsing date for {Path(file_path).name}: {e}")
                    # Fallback to "Unknown Date"
                    if "unknown" not in files_by_date:
                        files_by_date["unknown"] = []
                    files_by_date["unknown"].append(file_path)

            print(f"[MTPAdapter]   ✓ Parsed dates: {len(files_by_date)} unique dates found")

            # Create folder hierarchy: Device_Imports → Device → Folder → Dates
            print(f"[MTPAdapter]   Creating folder hierarchy...")
            if progress_callback:
                progress_callback("Organizing", 0, 100, "Creating folders...")
            file_folder_map = self._create_folder_hierarchy(device_name, folder_name, files_by_date)

            # Add files to photo_metadata with EXIF dates
            print(f"[MTPAdapter]   Adding files to photo_metadata...")
            if progress_callback:
                progress_callback("Organizing", 0, 100, "Adding photos to database...")
            self._add_to_photo_metadata(files_by_date, file_folder_map)

            # Register videos in videos table
            video_files = [p for p in imported_paths if parser._is_video(p)]
            if video_files:
                print(f"[MTPAdapter]   Registering {len(video_files)} videos...")
                if progress_callback:
                    progress_callback("Organizing", 0, len(video_files), "Registering videos...")
                self._register_videos(video_files, files_by_date, file_folder_map, progress_callback)

            print(f"[MTPAdapter] ✓ Auto-organization complete:")
            print(f"[MTPAdapter]   • Organized {len(imported_paths)} files into folder hierarchy")
            print(f"[MTPAdapter]   • Grouped by {len(files_by_date)} unique dates")
            if video_files:
                print(f"[MTPAdapter]   • Registered {len(video_files)} videos in Videos section")
            print(f"[MTPAdapter]   • Files will now appear in Folders, Dates, and Videos sections")

        except Exception as e:
            print(f"[MTPAdapter] ✗ Error during auto-organization: {e}")
            import traceback
            traceback.print_exc()
            # Don't fail the import if organization fails
            print(f"[MTPAdapter]   Files were imported successfully but auto-organization failed")

    def _create_folder_hierarchy(
        self,
        device_name: str,
        folder_name: str,
        files_by_date: dict
    ) -> dict:
        """
        Create folder hierarchy in database.

        Structure:
        Device_Imports/
          ├─ {Device Name}/
          │   ├─ {Folder Name}/
          │   │   ├─ {Date}/
          │   │   │   └─ files...

        Args:
            device_name: Device name (e.g., "A54 von Ammar")
            folder_name: Folder name (e.g., "Camera")
            files_by_date: Dict of {date_str: [file_paths]}

        Returns:
            Dict mapping {file_path: folder_id} for all files
        """
        file_folder_map = {}

        try:
            # 1. Get or create "Device_Imports" root folder
            device_imports_path = str(Path.cwd() / "Device_Imports")
            device_imports_id = self.db.ensure_folder(
                path=device_imports_path,
                name="Device_Imports",
                parent_id=None,
                project_id=self.project_id
            )
            print(f"[MTPAdapter]     ✓ Root folder: Device_Imports (id={device_imports_id})")

            # 2. Get or create device folder (e.g., "A54 von Ammar")
            device_folder_path = str(Path.cwd() / "Device_Imports" / device_name)
            device_folder_id = self.db.ensure_folder(
                path=device_folder_path,
                name=device_name,
                parent_id=device_imports_id,
                project_id=self.project_id
            )
            print(f"[MTPAdapter]     ✓ Device folder: {device_name} (id={device_folder_id})")

            # 3. Get or create source folder (e.g., "Camera")
            source_folder_path = str(Path.cwd() / "Device_Imports" / device_name / folder_name)
            source_folder_id = self.db.ensure_folder(
                path=source_folder_path,
                name=folder_name,
                parent_id=device_folder_id,
                project_id=self.project_id
            )
            print(f"[MTPAdapter]     ✓ Source folder: {folder_name} (id={source_folder_id})")

            # 4. Create date subfolders and link files
            for date_str, file_paths in files_by_date.items():
                if date_str == "unknown":
                    # Link unknown date files directly to source folder
                    for file_path in file_paths:
                        self.db.set_folder_for_image(
                            path=file_path,
                            folder_id=source_folder_id
                        )
                        # Track folder_id for video registration
                        file_folder_map[file_path] = source_folder_id
                else:
                    # Create date folder
                    date_folder_path = str(Path.cwd() / "Device_Imports" / device_name / folder_name / date_str)
                    date_folder_id = self.db.ensure_folder(
                        path=date_folder_path,
                        name=date_str,
                        parent_id=source_folder_id,
                        project_id=self.project_id
                    )

                    # Link files to date folder
                    for file_path in file_paths:
                        self.db.set_folder_for_image(
                            path=file_path,
                            folder_id=date_folder_id
                        )
                        # Track folder_id for video registration
                        file_folder_map[file_path] = date_folder_id

                    print(f"[MTPAdapter]     ✓ Date folder: {date_str} ({len(file_paths)} files, id={date_folder_id})")

            return file_folder_map

        except Exception as e:
            print(f"[MTPAdapter]   ✗ Error creating folder hierarchy: {e}")
            import traceback
            traceback.print_exc()
            return file_folder_map  # Return partial map even on error

    def _add_to_photo_metadata(self, files_by_date: dict, file_folder_map: dict):
        """
        Add files to photo_metadata table with EXIF dates.

        This enables date-based organization in the "By Dates" section.

        Args:
            files_by_date: Dict of {date_str: [file_paths]}
            file_folder_map: Dict mapping {file_path: folder_id}
        """
        try:
            from services.exif_parser import EXIFParser
            from PIL import Image
            import os

            parser = EXIFParser()

            for date_str, file_paths in files_by_date.items():
                for file_path in file_paths:
                    try:
                        file_path_obj = Path(file_path)
                        file_name = file_path_obj.name

                        # Get folder_id from mapping (required - photo_metadata table has NOT NULL constraint)
                        folder_id = file_folder_map.get(file_path)
                        if folder_id is None:
                            print(f"[MTPAdapter]     ⚠️ Skipping photo (no folder_id): {file_name}")
                            continue

                        # Get file stats
                        stat = file_path_obj.stat()
                        size_kb = stat.st_size / 1024
                        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

                        # Get image dimensions
                        width, height = None, None
                        if parser._is_image(file_path):
                            try:
                                with Image.open(file_path) as img:
                                    width, height = img.size
                                    print(f"[MTPAdapter]     ✓ Dimensions: {width}x{height} - {file_name}")
                            except Exception as e:
                                print(f"[MTPAdapter]     ⚠️ Cannot get dimensions for {file_name}: {e}")

                        # Get EXIF date
                        date_taken = None
                        if date_str != "unknown":
                            try:
                                capture_date = parser.get_capture_date(file_path)
                                if capture_date:
                                    date_taken = capture_date.strftime("%Y-%m-%d %H:%M:%S")
                                    print(f"[MTPAdapter]     ✓ Date: {date_taken} - {file_name}")
                                else:
                                    print(f"[MTPAdapter]     ⚠️ No date extracted for {file_name}")
                            except Exception as e:
                                print(f"[MTPAdapter]     ✗ Date extraction failed for {file_name}: {e}")

                        # Add to photo_metadata (auto-creates created_date fields for date organization)
                        self.db.upsert_photo_metadata(
                            path=file_path,
                            folder_id=folder_id,  # Use folder_id from hierarchy
                            size_kb=size_kb,
                            modified=modified,
                            width=width,
                            height=height,
                            date_taken=date_taken,
                            tags=None,
                            project_id=self.project_id
                        )

                    except Exception as e:
                        print(f"[MTPAdapter]     ✗ Error adding {Path(file_path).name} to photo_metadata: {e}")
                        continue

            print(f"[MTPAdapter]     ✓ Added {sum(len(files) for files in files_by_date.values())} files to photo_metadata")

        except Exception as e:
            print(f"[MTPAdapter]   ✗ Error adding to photo_metadata: {e}")
            import traceback
            traceback.print_exc()

    def _register_videos(self, video_paths: List[str], files_by_date: dict, file_folder_map: dict, progress_callback=None):
        """
        Register video files in videos table for Videos section.

        Args:
            video_paths: List of video file paths
            files_by_date: Dict mapping dates to file paths (for getting dates)
            file_folder_map: Dict mapping {file_path: folder_id}
            progress_callback: Optional callback(stage, current, total, detail) for progress updates
        """
        try:
            from services.video_service import VideoService
            from services.exif_parser import EXIFParser

            video_service = VideoService()
            parser = EXIFParser()

            registered_count = 0

            for idx, video_path in enumerate(video_paths, 1):
                try:
                    file_path_obj = Path(video_path)
                    file_name = file_path_obj.name

                    # Report progress
                    if progress_callback:
                        progress_callback("Registering Videos", idx, len(video_paths), file_name)

                    # Get folder_id from mapping (required - videos table has NOT NULL constraint)
                    folder_id = file_folder_map.get(video_path)
                    if folder_id is None:
                        print(f"[MTPAdapter]     ⚠️ Skipping video (no folder_id): {file_name}")
                        continue

                    # Get file stats
                    stat = file_path_obj.stat()
                    size_kb = stat.st_size / 1024
                    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

                    # Get video date
                    capture_date = parser.get_capture_date(video_path)
                    created_ts = int(capture_date.timestamp())
                    created_date = capture_date.strftime("%Y-%m-%d")
                    created_year = capture_date.year

                    # Register video (index_video creates entry with 'pending' status)
                    video_id = video_service.index_video(
                        path=video_path,
                        project_id=self.project_id,
                        folder_id=folder_id,  # Use folder_id from hierarchy
                        size_kb=size_kb,
                        modified=modified,
                        created_ts=created_ts,
                        created_date=created_date,
                        created_year=created_year
                    )

                    if video_id:
                        print(f"[MTPAdapter]     ✓ Registered video: {file_name} (id={video_id})")
                        registered_count += 1
                    else:
                        print(f"[MTPAdapter]     ⚠️ Video already registered: {file_name}")

                except Exception as e:
                    print(f"[MTPAdapter]     ✗ Error registering video {Path(video_path).name}: {e}")
                    continue

            print(f"[MTPAdapter]     ✓ Registered {registered_count}/{len(video_paths)} videos")

        except Exception as e:
            print(f"[MTPAdapter]   ✗ Error registering videos: {e}")
            import traceback
            traceback.print_exc()
