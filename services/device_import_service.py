"""
Device Import Service - Import photos/videos from mobile devices

Phase 2: Incremental Sync Support
- Scan device and track files in database
- Detect new files since last import
- Track import sessions and history
- Preserve device folder structure (Camera/Screenshots)
- Detect deleted files on device

Phase 3: Smart Deduplication
- Cross-device duplicate detection
- Show which device already has this file
- Give user control over duplicate handling
- Link duplicates across devices

Usage:
    service = DeviceImportService(db, project_id, device_id="android:ABC123")

    # Get only new files since last import
    new_files = service.scan_incremental("/path/to/device/DCIM")

    # Import with session tracking
    session_id = service.start_import_session()
    stats = service.import_files(new_files)
    service.complete_import_session(session_id, stats)
"""

import os
import shutil
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool


@dataclass
class DuplicateInfo:
    """Information about a duplicate file from another source (Phase 3)"""
    photo_id: int                    # Photo ID in database
    device_id: str                   # Which device has this file
    device_name: str                 # User-friendly device name
    device_folder: Optional[str]     # Folder on device (Camera/Screenshots)
    import_date: datetime            # When it was imported
    project_id: int                  # Which project contains it
    project_name: str                # Project name
    file_path: str                   # Local path to imported file
    is_same_device: bool = False     # True if from current device
    is_same_project: bool = False    # True if in current project


@dataclass
class DeviceMediaFile:
    """Represents a media file on device"""
    path: str                    # Full path on device
    filename: str                # Original filename
    size_bytes: int              # File size
    modified_date: datetime      # Last modified date
    thumbnail_path: Optional[str] = None  # Thumbnail preview
    already_imported: bool = False        # Already in library
    file_hash: Optional[str] = None       # SHA256 hash for dedup
    device_folder: Optional[str] = None   # Device folder (Camera/Screenshots/etc)
    import_status: str = "new"            # new/imported/skipped/modified
    # Phase 3: Cross-device duplicate detection
    duplicate_info: List[DuplicateInfo] = field(default_factory=list)  # Duplicates from other sources
    is_cross_device_duplicate: bool = False  # True if exists from another device


class DeviceImportService:
    """Service for importing media from mobile devices"""

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'
    }

    def __init__(self, db, project_id: int, device_id: Optional[str] = None):
        """
        Initialize import service.

        Args:
            db: ReferenceDB instance
            project_id: Target project ID
            device_id: Device identifier for tracking (Phase 2)
        """
        self.db = db
        self.project_id = project_id
        self.device_id = device_id
        self.current_session_id = None  # Set when import session starts

    def scan_device_folder(self, folder_path: str, max_depth: int = 3) -> List[DeviceMediaFile]:
        """
        Scan device folder for media files.

        Args:
            folder_path: Device folder to scan
            max_depth: Maximum recursion depth

        Returns:
            List of DeviceMediaFile objects
        """
        media_files = []
        folder = Path(folder_path)

        if not folder.exists():
            return media_files

        def scan_recursive(current_folder: Path, depth: int = 0):
            if depth > max_depth:
                return

            try:
                for item in current_folder.iterdir():
                    if item.is_file():
                        if item.suffix.lower() in self.MEDIA_EXTENSIONS:
                            # Create media file entry
                            stat = item.stat()
                            media_file = DeviceMediaFile(
                                path=str(item),
                                filename=item.name,
                                size_bytes=stat.st_size,
                                modified_date=datetime.fromtimestamp(stat.st_mtime)
                            )

                            # Check if already imported (by hash)
                            media_file.file_hash = self._calculate_hash(str(item))
                            media_file.already_imported = self._is_already_imported(media_file.file_hash)

                            media_files.append(media_file)

                    elif item.is_dir() and not item.name.startswith('.'):
                        # Recurse into subdirectories
                        scan_recursive(item, depth + 1)

            except (PermissionError, OSError) as e:
                print(f"[DeviceImport] Cannot access {current_folder}: {e}")

        scan_recursive(folder)
        return media_files

    def scan_with_tracking(
        self,
        folder_path: str,
        root_path: str,
        max_depth: int = 3
    ) -> List[DeviceMediaFile]:
        """
        Scan device folder and track all files in device_files table (Phase 2).

        Args:
            folder_path: Folder to scan
            root_path: Device root path (for extracting device folder)
            max_depth: Maximum recursion depth

        Returns:
            List of DeviceMediaFile with tracking info
        """
        if not self.device_id:
            # Fall back to basic scan if no device_id
            return self.scan_device_folder(folder_path, max_depth)

        media_files = []
        folder = Path(folder_path)
        root = Path(root_path)

        if not folder.exists():
            return media_files

        def scan_recursive(current_folder: Path, depth: int = 0):
            if depth > max_depth:
                return

            try:
                for item in current_folder.iterdir():
                    if item.is_file():
                        if item.suffix.lower() in self.MEDIA_EXTENSIONS:
                            # Create media file entry
                            stat = item.stat()
                            device_path = str(item)
                            file_hash = self._calculate_hash(device_path)

                            # Extract device folder (Camera/Screenshots/etc)
                            device_folder = self._extract_device_folder(device_path, str(root))

                            # Check if already tracked in database
                            import_status, already_imported = self._check_file_status(
                                device_path, file_hash
                            )

                            # Phase 3: Check for cross-device duplicates
                            cross_device_dups = self.check_cross_device_duplicates(file_hash)
                            is_cross_device_dup = len(cross_device_dups) > 0

                            media_file = DeviceMediaFile(
                                path=device_path,
                                filename=item.name,
                                size_bytes=stat.st_size,
                                modified_date=datetime.fromtimestamp(stat.st_mtime),
                                file_hash=file_hash,
                                device_folder=device_folder,
                                import_status=import_status,
                                already_imported=already_imported,
                                duplicate_info=cross_device_dups,  # Phase 3
                                is_cross_device_duplicate=is_cross_device_dup  # Phase 3
                            )

                            media_files.append(media_file)

                            # Track file in database
                            if self.device_id:
                                try:
                                    self.db.track_device_file(
                                        device_id=self.device_id,
                                        device_path=device_path,
                                        device_folder=device_folder,
                                        file_hash=file_hash,
                                        file_size=stat.st_size,
                                        file_mtime=datetime.fromtimestamp(stat.st_mtime).isoformat()
                                    )
                                except Exception as e:
                                    print(f"[DeviceImport] Failed to track file: {e}")

                    elif item.is_dir() and not item.name.startswith('.'):
                        # Recurse into subdirectories
                        scan_recursive(item, depth + 1)

            except (PermissionError, OSError) as e:
                print(f"[DeviceImport] Cannot access {current_folder}: {e}")

        scan_recursive(folder)
        return media_files

    def scan_incremental(self, folder_path: str, root_path: str, max_depth: int = 3) -> List[DeviceMediaFile]:
        """
        Scan device and return ONLY new files since last import (Phase 2).

        Args:
            folder_path: Folder to scan
            root_path: Device root path
            max_depth: Maximum recursion depth

        Returns:
            List of NEW DeviceMediaFile only
        """
        # Scan with tracking
        all_files = self.scan_with_tracking(folder_path, root_path, max_depth)

        # Filter to only new files
        new_files = [f for f in all_files if f.import_status == "new"]

        print(f"[DeviceImport] Incremental scan: {len(new_files)} new / {len(all_files)} total")
        return new_files

    def start_import_session(self, import_type: str = "manual") -> int:
        """
        Start a new import session (Phase 2).

        Args:
            import_type: Type of import ("manual", "auto", "incremental")

        Returns:
            Session ID
        """
        if not self.device_id:
            raise ValueError("device_id required for session tracking")

        session_id = self.db.create_import_session(
            device_id=self.device_id,
            project_id=self.project_id,
            import_type=import_type
        )
        self.current_session_id = session_id
        print(f"[DeviceImport] Started import session {session_id}")
        return session_id

    def complete_import_session(self, session_id: int, stats: Dict[str, any]):
        """
        Complete import session with statistics (Phase 2).

        Args:
            session_id: Session ID
            stats: Import statistics dict
        """
        photos_imported = stats.get('imported', 0)
        duplicates_skipped = stats.get('skipped', 0)
        bytes_imported = stats.get('bytes_imported', 0)

        error_message = None
        if stats.get('failed', 0) > 0:
            error_message = "; ".join(stats.get('errors', []))

        self.db.complete_import_session(
            session_id=session_id,
            photos_imported=photos_imported,
            videos_imported=0,  # TODO: Separate video tracking
            duplicates_skipped=duplicates_skipped,
            bytes_imported=bytes_imported,
            duration_seconds=None,
            error_message=error_message
        )

        self.current_session_id = None
        print(f"[DeviceImport] Completed session {session_id}: {photos_imported} imported, {duplicates_skipped} skipped")

    # ============================================================================
    # PHASE 4: QUICK IMPORT (AUTO-IMPORT WORKFLOWS)
    # ============================================================================

    def quick_import_new_files(
        self,
        device_folder_path: str,
        root_path: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        skip_cross_device_duplicates: bool = True
    ) -> dict:
        """
        Quick import: Import only new files with smart defaults (Phase 4).

        This is the main method for auto-import workflows. It uses all Phase 2 and Phase 3
        features to provide a smart, one-click import experience.

        Smart defaults:
        - Incremental scan (new files only)
        - Skip cross-device duplicates (optional)
        - Import to root folder
        - Create import session automatically
        - Update device last_auto_import timestamp

        Args:
            device_folder_path: Device folder to scan
            root_path: Device root path
            progress_callback: Optional callback(current, total, filename)
            skip_cross_device_duplicates: Skip duplicates from other devices (default: True)

        Returns:
            Stats dict with imported/skipped/failed counts
        """
        if not self.device_id:
            raise ValueError("device_id required for quick import")

        print(f"[DeviceImport] Starting quick import from {device_folder_path}")

        # Start session with type="quick"
        session_id = self.start_import_session(import_type="quick")

        try:
            # Scan for new files only (Phase 2 incremental)
            new_files = self.scan_incremental(device_folder_path, root_path)

            print(f"[DeviceImport] Found {len(new_files)} new files")

            # Filter out cross-device duplicates if requested (Phase 3)
            files_to_import = new_files
            if skip_cross_device_duplicates:
                files_to_import = [
                    f for f in new_files
                    if not f.is_cross_device_duplicate
                ]
                dup_count = len(new_files) - len(files_to_import)
                if dup_count > 0:
                    print(f"[DeviceImport] Skipping {dup_count} cross-device duplicates")

            if not files_to_import:
                print("[DeviceImport] No new files to import")
                stats = {
                    'imported': 0,
                    'skipped': len(new_files),
                    'failed': 0,
                    'bytes_imported': 0,
                    'errors': []
                }
                self.complete_import_session(session_id, stats)
                return stats

            # Import files
            stats = self.import_files(
                files_to_import,
                destination_folder_id=None,  # Import to root
                progress_callback=progress_callback
            )

            # Update stats with skipped duplicates
            stats['skipped'] += (len(new_files) - len(files_to_import))

            # Complete session
            self.complete_import_session(session_id, stats)

            # Update device last_auto_import timestamp
            self.db.update_device_last_auto_import(self.device_id)

            print(f"[DeviceImport] Quick import complete: {stats['imported']} imported, "
                  f"{stats['skipped']} skipped, {stats['failed']} failed")

            return stats

        except Exception as e:
            print(f"[DeviceImport] Quick import failed: {e}")
            import traceback
            traceback.print_exc()

            # Mark session as failed
            self.complete_import_session(session_id, {
                'imported': 0,
                'skipped': 0,
                'failed': 1,
                'errors': [str(e)]
            })
            raise

    def _extract_device_folder(self, device_path: str, root_path: str) -> str:
        """
        Extract device folder name from path (Camera/Screenshots/WhatsApp/etc).

        Args:
            device_path: Full path on device
            root_path: Device root path

        Returns:
            Folder name or "Unknown"
        """
        try:
            rel_path = Path(device_path).relative_to(Path(root_path))
            parts = rel_path.parts

            # Look for meaningful folder names
            folder_indicators = [
                "Camera", "Screenshots", "Screen", "WhatsApp", "Instagram",
                "Telegram", "Download", "Pictures", "Photos", "DCIM"
            ]

            for part in parts:
                for indicator in folder_indicators:
                    if indicator.lower() in part.lower():
                        return part

            # Fallback: Use first folder after DCIM
            if "DCIM" in parts:
                dcim_idx = parts.index("DCIM")
                if dcim_idx + 1 < len(parts):
                    return parts[dcim_idx + 1]

            # Last resort: Use first folder
            if len(parts) > 1:
                return parts[0]

            return "Unknown"

        except Exception:
            return "Unknown"

    def _check_file_status(self, device_path: str, file_hash: str) -> tuple[str, bool]:
        """
        Check if file has been imported before (Phase 2).

        Args:
            device_path: Path on device
            file_hash: SHA256 hash

        Returns:
            Tuple of (import_status, already_imported)
        """
        if not self.device_id:
            # Fall back to basic hash check
            return ("new", self._is_already_imported(file_hash))

        try:
            # Check device_files table
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT import_status, local_photo_id
                    FROM device_files
                    WHERE device_id = ? AND device_path = ?
                """, (self.device_id, device_path))
                row = cur.fetchone()

                if row:
                    status = row[0]
                    local_photo_id = row[1]
                    already_imported = (local_photo_id is not None)
                    return (status, already_imported)

                # Not tracked yet - check by hash
                already_imported = self._is_already_imported(file_hash)
                return ("new", already_imported)

        except Exception as e:
            print(f"[DeviceImport] Error checking file status: {e}")
            return ("new", False)

    def _calculate_hash(self, file_path: str, chunk_size: int = 8192) -> str:
        """
        Calculate SHA256 hash of file for duplicate detection.

        Args:
            file_path: Path to file
            chunk_size: Read chunk size

        Returns:
            SHA256 hexdigest
        """
        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                while chunk := f.read(chunk_size):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            print(f"[DeviceImport] Hash calculation failed for {file_path}: {e}")
            return ""

    def _is_already_imported(self, file_hash: str) -> bool:
        """
        Check if file with this hash is already in project.

        Args:
            file_hash: SHA256 hash

        Returns:
            True if already imported
        """
        if not file_hash:
            return False

        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT COUNT(*) FROM photo_metadata
                    WHERE project_id = ? AND file_hash = ?
                """, (self.project_id, file_hash))
                count = cur.fetchone()[0]
                return count > 0
        except Exception:
            # If file_hash column doesn't exist, can't check
            return False

    # ============================================================================
    # PHASE 3: CROSS-DEVICE DUPLICATE DETECTION
    # ============================================================================

    def get_duplicate_info(self, file_hash: str, include_same_device: bool = True) -> List[DuplicateInfo]:
        """
        Get all instances of this file across devices and projects (Phase 3).

        Args:
            file_hash: SHA256 hash of file
            include_same_device: Include duplicates from current device

        Returns:
            List of DuplicateInfo objects with details about each duplicate
        """
        if not file_hash:
            return []

        duplicates = []

        try:
            with self.db._connect() as conn:
                # Query photo_metadata joined with device info and project info
                cur = conn.execute("""
                    SELECT
                        pm.id,
                        pm.device_id,
                        md.device_name,
                        pm.device_folder,
                        pm.import_session_id,
                        pm.project_id,
                        p.name AS project_name,
                        pm.path
                    FROM photo_metadata pm
                    LEFT JOIN mobile_devices md ON pm.device_id = md.device_id
                    LEFT JOIN projects p ON pm.project_id = p.id
                    WHERE pm.file_hash = ?
                    ORDER BY pm.import_session_id DESC
                """, (file_hash,))

                for row in cur.fetchall():
                    photo_id, device_id, device_name, device_folder, session_id, \
                        project_id, project_name, file_path = row

                    # Get import date from session
                    import_date = None
                    if session_id:
                        session_cur = conn.execute("""
                            SELECT import_date FROM import_sessions WHERE id = ?
                        """, (session_id,))
                        session_row = session_cur.fetchone()
                        if session_row:
                            import_date = datetime.fromisoformat(session_row[0])

                    # Create duplicate info
                    duplicate = DuplicateInfo(
                        photo_id=photo_id,
                        device_id=device_id or "unknown",
                        device_name=device_name or "Unknown Device",
                        device_folder=device_folder,
                        import_date=import_date or datetime.now(),
                        project_id=project_id,
                        project_name=project_name or "Unknown Project",
                        file_path=file_path,
                        is_same_device=(device_id == self.device_id),
                        is_same_project=(project_id == self.project_id)
                    )

                    # Filter by device if requested
                    if include_same_device or not duplicate.is_same_device:
                        duplicates.append(duplicate)

        except Exception as e:
            print(f"[DeviceImport] Error getting duplicate info: {e}")
            import traceback
            traceback.print_exc()

        return duplicates

    def check_cross_device_duplicates(self, file_hash: str) -> List[DuplicateInfo]:
        """
        Check if file exists from OTHER devices (Phase 3).

        This is the main method for cross-device deduplication.
        It only returns duplicates from different devices.

        Args:
            file_hash: SHA256 hash of file

        Returns:
            List of DuplicateInfo for duplicates from other devices
        """
        all_duplicates = self.get_duplicate_info(file_hash, include_same_device=True)

        # Filter to only other devices
        cross_device_duplicates = [
            dup for dup in all_duplicates
            if dup.device_id != self.device_id
        ]

        return cross_device_duplicates

    def get_duplicate_summary(self, file_hash: str) -> str:
        """
        Get human-readable summary of duplicates (Phase 3).

        Args:
            file_hash: SHA256 hash of file

        Returns:
            String like "Already imported from Galaxy S22 on Nov 15, 2024"
        """
        cross_device_dups = self.check_cross_device_duplicates(file_hash)

        if not cross_device_dups:
            return ""

        # Get most recent duplicate
        most_recent = cross_device_dups[0]

        device_name = most_recent.device_name
        import_date_str = most_recent.import_date.strftime("%b %d, %Y")

        if len(cross_device_dups) == 1:
            return f"Already imported from {device_name} on {import_date_str}"
        else:
            return f"Already imported from {device_name} and {len(cross_device_dups)-1} other device(s)"

    def import_files(
        self,
        files: List[DeviceMediaFile],
        destination_folder_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, any]:
        """
        Import files from device to project.

        Args:
            files: List of DeviceMediaFile to import
            destination_folder_id: Target folder ID (None for root)
            progress_callback: Callback(current, total, filename)

        Returns:
            Dict with import statistics
        """
        stats = {
            'total': len(files),
            'imported': 0,
            'skipped': 0,
            'failed': 0,
            'bytes_imported': 0,  # Phase 2: Track bytes
            'errors': []
        }

        # Get project directory
        project_dir = self._get_project_directory()
        if not project_dir:
            stats['errors'].append("Could not determine project directory")
            return stats

        # Create import subdirectory with timestamp
        import_dir = Path(project_dir) / f"imported_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        import_dir.mkdir(parents=True, exist_ok=True)

        for idx, media_file in enumerate(files, 1):
            if progress_callback:
                progress_callback(idx, len(files), media_file.filename)

            # Skip if already imported
            if media_file.already_imported:
                stats['skipped'] += 1
                continue

            try:
                # Copy file to import directory
                source_path = Path(media_file.path)
                dest_path = import_dir / media_file.filename

                # Handle duplicate filenames
                counter = 1
                while dest_path.exists():
                    stem = source_path.stem
                    suffix = source_path.suffix
                    dest_path = import_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                shutil.copy2(source_path, dest_path)

                # Track bytes imported (Phase 2)
                stats['bytes_imported'] += media_file.size_bytes

                # Register in database
                local_photo_id = self._register_imported_file(
                    str(dest_path),
                    media_file.file_hash,
                    destination_folder_id,
                    device_path=media_file.path,
                    device_folder=media_file.device_folder
                )

                stats['imported'] += 1

            except Exception as e:
                error_msg = f"Failed to import {media_file.filename}: {e}"
                print(f"[DeviceImport] {error_msg}")
                stats['errors'].append(error_msg)
                stats['failed'] += 1

        return stats

    def _get_project_directory(self) -> Optional[str]:
        """
        Get project directory path from database.

        Returns:
            Project directory path or None
        """
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT root_folder FROM projects WHERE id = ?
                """, (self.project_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            print(f"[DeviceImport] Could not get project directory: {e}")
            return None

    def _register_imported_file(
        self,
        file_path: str,
        file_hash: str,
        folder_id: Optional[int],
        device_path: Optional[str] = None,
        device_folder: Optional[str] = None
    ) -> Optional[int]:
        """
        Register imported file in database (Phase 2: Enhanced).

        Args:
            file_path: Path to imported file
            file_hash: SHA256 hash
            folder_id: Destination folder ID
            device_path: Original path on device (Phase 2)
            device_folder: Device folder name (Phase 2)

        Returns:
            Photo ID if successfully registered
        """
        try:
            # P2-20 FIX: Validate folder_id exists before proceeding
            if folder_id is not None:
                with self.db._connect() as conn:
                    cursor = conn.execute(
                        "SELECT id FROM folders WHERE id = ? AND project_id = ?",
                        (folder_id, self.project_id)
                    )
                    if not cursor.fetchone():
                        raise ValueError(
                            f"Invalid folder_id={folder_id}: Folder does not exist in project {self.project_id}"
                        )

            # Use existing add_project_image method
            if hasattr(self.db, 'add_project_image'):
                self.db.add_project_image(
                    project_id=self.project_id,
                    image_path=file_path,
                    folder_id=folder_id
                )

            # Get the photo_id for the just-inserted photo
            local_photo_id = None
            try:
                with self.db._connect() as conn:
                    # P1-1 FIX: Wrap all database operations in transaction with proper rollback
                    try:
                        # P1-3 FIX: Check for duplicate hash atomically within transaction
                        # This prevents race condition where two processes import same file
                        cur = conn.execute("""
                            SELECT id, path FROM photo_metadata
                            WHERE project_id = ? AND file_hash = ? AND file_hash != ''
                        """, (self.project_id, file_hash))
                        existing = cur.fetchone()

                        if existing and existing[1] != file_path:
                            # P1-3 FIX: File with same hash already exists - skip to prevent duplicate
                            print(f"[DeviceImport] Duplicate detected: {file_path} matches existing {existing[1]} (hash: {file_hash[:8]}...)")
                            return existing[0]  # Return existing photo_id

                        # Update hash and device info (Phase 2)
                        conn.execute("""
                            UPDATE photo_metadata
                            SET file_hash = ?,
                                device_id = ?,
                                device_path = ?,
                                device_folder = ?,
                                import_session_id = ?
                            WHERE project_id = ? AND path = ?
                        """, (file_hash, self.device_id, device_path, device_folder,
                              self.current_session_id, self.project_id, file_path))

                        # Get the photo_id
                        cur = conn.execute("""
                            SELECT id FROM photo_metadata
                            WHERE project_id = ? AND path = ?
                        """, (self.project_id, file_path))
                        row = cur.fetchone()
                        if row:
                            local_photo_id = row[0]

                        # Update device_files table (Phase 2)
                        if self.device_id and device_path and local_photo_id:
                            self.db.track_device_file(
                                device_id=self.device_id,
                                device_path=device_path,
                                device_folder=device_folder or "Unknown",
                                file_hash=file_hash,
                                file_size=0,  # Already tracked during scan
                                file_mtime="",
                                import_session_id=self.current_session_id,
                                local_photo_id=local_photo_id
                            )

                        # P1-1 FIX: Commit only after ALL operations succeed
                        conn.commit()

                    except Exception as e:
                        # P1-1 FIX: Explicit rollback on any failure
                        conn.rollback()
                        raise

            except Exception as e:
                print(f"[DeviceImport] Warning: Could not update device tracking: {e}")

            return local_photo_id

        except Exception as e:
            print(f"[DeviceImport] Failed to register {file_path}: {e}")
            raise


class DeviceImportWorker(QRunnable):
    """Background worker for device imports"""

    class Signals(QObject):
        """Worker signals"""
        progress = Signal(int, int, str)  # current, total, filename
        finished = Signal(dict)            # statistics
        error = Signal(str)                # error message

    def __init__(
        self,
        import_service: DeviceImportService,
        files: List[DeviceMediaFile],
        destination_folder_id: Optional[int] = None
    ):
        super().__init__()
        self.import_service = import_service
        self.files = files
        self.destination_folder_id = destination_folder_id
        self.signals = self.Signals()

    def run(self):
        """Run import in background thread"""
        try:
            stats = self.import_service.import_files(
                self.files,
                self.destination_folder_id,
                progress_callback=self._on_progress
            )
            self.signals.finished.emit(stats)
        except Exception as e:
            self.signals.error.emit(str(e))

    def _on_progress(self, current: int, total: int, filename: str):
        """Emit progress signal"""
        self.signals.progress.emit(current, total, filename)
