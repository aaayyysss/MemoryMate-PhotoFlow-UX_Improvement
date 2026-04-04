"""
Device Import Dialog - Photos-app-style import interface

Phase 2: Incremental Sync Support
- Shows new files vs already imported
- "Import New Only" filter mode
- Import session tracking
- Shows statistics (X new / Y total)

Usage:
    dialog = DeviceImportDialog(
        db, project_id, device_folder_path, parent,
        device_id="android:ABC123",  # For Phase 2 tracking
        root_path="/media/user/device"
    )
    if dialog.exec():
        # Files imported successfully with session tracking
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QGridLayout, QCheckBox, QProgressBar,
    QMessageBox, QFrame
)
from PySide6.QtCore import Qt, QSize, Signal, QThreadPool
from PySide6.QtGui import QPixmap, QImage, QIcon

from pathlib import Path
from typing import List, Optional
from utils.qt_guards import connect_guarded

from services.device_import_service import (
    DeviceImportService, DeviceMediaFile, DeviceImportWorker
)


class MediaThumbnailWidget(QFrame):
    """Widget displaying a media file thumbnail with checkbox"""

    toggled = Signal(bool)  # Emitted when checkbox changes

    def __init__(self, media_file: DeviceMediaFile, parent=None, skip_duplicates: bool = True):
        super().__init__(parent)
        self.media_file = media_file

        # Phase 3: Determine default selection state
        # Deselect if already imported OR if cross-device duplicate (when skip enabled)
        if media_file.already_imported:
            self.selected = False
        elif skip_duplicates and media_file.is_cross_device_duplicate:
            self.selected = False  # Deselect cross-device duplicates by default
        else:
            self.selected = True  # Select new files

        self._init_ui()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Thumbnail
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(120, 120)
        self.thumbnail_label.setScaledContents(False)
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #f0f0f0;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)

        # Load thumbnail
        self._load_thumbnail()

        layout.addWidget(self.thumbnail_label)

        # Checkbox + filename
        info_layout = QHBoxLayout()
        info_layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(self.selected)
        self.checkbox.toggled.connect(self._on_checkbox_toggled)

        filename = self.media_file.filename
        if len(filename) > 15:
            filename = filename[:12] + "..."

        self.filename_label = QLabel(filename)
        self.filename_label.setToolTip(self.media_file.filename)
        self.filename_label.setStyleSheet("font-size: 11px;")

        info_layout.addWidget(self.checkbox)
        info_layout.addWidget(self.filename_label, 1)

        layout.addLayout(info_layout)

        # "Already imported" badge
        if self.media_file.already_imported:
            badge = QLabel("✓ Imported")
            badge.setStyleSheet("""
                QLabel {
                    color: #888;
                    font-size: 10px;
                    background-color: #e8e8e8;
                    padding: 2px 6px;
                    border-radius: 3px;
                }
            """)
            layout.addWidget(badge)

        # Phase 3: Cross-device duplicate badge
        if self.media_file.is_cross_device_duplicate and self.media_file.duplicate_info:
            dup = self.media_file.duplicate_info[0]  # Most recent duplicate
            device_name = dup.device_name
            if len(device_name) > 20:
                device_name = device_name[:17] + "..."

            dup_badge = QLabel(f"⚠️ {device_name}")
            dup_badge.setStyleSheet("""
                QLabel {
                    color: #856404;
                    font-size: 10px;
                    background-color: #fff3cd;
                    padding: 2px 6px;
                    border-radius: 3px;
                    border: 1px solid #ffc107;
                }
            """)

            # Build tooltip with duplicate details
            tooltip_lines = ["Cross-device duplicate found:", ""]
            for i, d in enumerate(self.media_file.duplicate_info[:3]):  # Show up to 3
                date_str = d.import_date.strftime("%b %d, %Y")
                tooltip_lines.append(f"• {d.device_name} ({date_str})")
                if d.project_name != "Unknown Project":
                    tooltip_lines.append(f"  Project: {d.project_name}")

            if len(self.media_file.duplicate_info) > 3:
                tooltip_lines.append(f"... and {len(self.media_file.duplicate_info) - 3} more")

            dup_badge.setToolTip("\n".join(tooltip_lines))
            layout.addWidget(dup_badge)

        # Style frame
        self.setFrameShape(QFrame.Box)
        if self.media_file.already_imported:
            self.setStyleSheet("""
                QFrame {
                    background-color: #f9f9f9;
                    border: 1px solid #ddd;
                    border-radius: 6px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: white;
                    border: 2px solid #ddd;
                    border-radius: 6px;
                }
                QFrame:hover {
                    border-color: #0078d4;
                }
            """)

    def _load_thumbnail(self):
        """Load thumbnail preview via SafeImageLoader (capped at 256px, never full resolution)."""
        try:
            from services.safe_image_loader import safe_decode_qimage

            # Check for video files first (SafeImageLoader doesn't handle video)
            if self.media_file.path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv',
                                                       '.webm', '.flv', '.wmv', '.m4v')):
                self.thumbnail_label.setText("🎬\nVideo")
                return

            # Decode at capped size — never full resolution for a preview thumbnail
            qimage = safe_decode_qimage(
                self.media_file.path,
                max_dim=256,
                enable_retry_ladder=True,
            )

            if not qimage.isNull():
                pixmap = QPixmap.fromImage(qimage)
                scaled = pixmap.scaled(
                    120, 120,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.thumbnail_label.setPixmap(scaled)
            else:
                self.thumbnail_label.setText("📷\nPhoto")

        except Exception as e:
            print(f"[ImportDialog] Failed to load thumbnail for {self.media_file.filename}: {e}")
            self.thumbnail_label.setText("❌")

    def _on_checkbox_toggled(self, checked: bool):
        """Handle checkbox toggle"""
        self.selected = checked
        self.toggled.emit(checked)

        # Update border style
        if checked and not self.media_file.already_imported:
            self.setStyleSheet("""
                QFrame {
                    background-color: white;
                    border: 2px solid #0078d4;
                    border-radius: 6px;
                }
            """)
        elif not self.media_file.already_imported:
            self.setStyleSheet("""
                QFrame {
                    background-color: white;
                    border: 2px solid #ddd;
                    border-radius: 6px;
                }
            """)

    def is_selected(self) -> bool:
        """Check if file is selected for import"""
        return self.selected and not self.media_file.already_imported


class DeviceImportDialog(QDialog):
    """Photos-app-style import dialog with Phase 2 incremental sync"""

    def __init__(
        self,
        db,
        project_id: int,
        device_folder_path: str,
        parent=None,
        device_id: Optional[str] = None,      # Phase 2: Device tracking
        root_path: Optional[str] = None       # Phase 2: For folder extraction
    ):
        super().__init__(parent)
        self._ui_generation: int = 0
        self.db = db
        self.project_id = project_id
        self.device_folder_path = device_folder_path
        self.device_id = device_id
        self.root_path = root_path or device_folder_path

        self.import_service = DeviceImportService(db, project_id, device_id=device_id)
        self.media_files: List[DeviceMediaFile] = []
        self.thumbnail_widgets: List[MediaThumbnailWidget] = []
        self.show_new_only = True  # Phase 2: Default to showing only new files
        self.current_session_id = None  # Phase 2: Track current import session
        self.skip_cross_device_duplicates = True  # Phase 3: Default duplicate handling

        self.setWindowTitle(f"Import from Device")
        self.setMinimumSize(800, 600)

        self._init_ui()
        self._scan_device()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()

        folder_name = Path(self.device_folder_path).name
        header_label = QLabel(f"<h3>📱 Import from {folder_name}</h3>")
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Status label with statistics (Phase 2)
        self.status_label = QLabel("Scanning device...")
        self.status_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Filter controls (Phase 2: Import New Only)
        if self.device_id:
            filter_layout = QHBoxLayout()

            self.new_only_checkbox = QCheckBox("Show New Files Only")
            self.new_only_checkbox.setChecked(self.show_new_only)
            self.new_only_checkbox.setToolTip("Show only files that haven't been imported yet")
            self.new_only_checkbox.toggled.connect(self._on_filter_toggled)

            self.stats_label = QLabel("")
            self.stats_label.setStyleSheet("color: #0078d4; font-weight: bold; font-size: 12px;")

            filter_layout.addWidget(self.new_only_checkbox)
            filter_layout.addSpacing(20)
            filter_layout.addWidget(self.stats_label)
            filter_layout.addStretch()

            layout.addLayout(filter_layout)

            # Phase 3: Duplicate handling options
            dup_layout = QHBoxLayout()

            self.skip_duplicates_checkbox = QCheckBox("Skip Cross-Device Duplicates")
            self.skip_duplicates_checkbox.setChecked(self.skip_cross_device_duplicates)
            self.skip_duplicates_checkbox.setToolTip(
                "Automatically skip files that were already imported from other devices"
            )
            self.skip_duplicates_checkbox.toggled.connect(self._on_duplicate_handling_toggled)

            self.duplicate_count_label = QLabel("")
            self.duplicate_count_label.setStyleSheet("color: #856404; font-size: 12px;")

            dup_layout.addWidget(self.skip_duplicates_checkbox)
            dup_layout.addSpacing(20)
            dup_layout.addWidget(self.duplicate_count_label)
            dup_layout.addStretch()

            layout.addLayout(dup_layout)
        else:
            # No device tracking - hide filter controls
            self.new_only_checkbox = None
            self.stats_label = None
            self.skip_duplicates_checkbox = None
            self.duplicate_count_label = None

        # Thumbnail grid (scrollable)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(12)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)

        scroll_area.setWidget(self.grid_widget)
        layout.addWidget(scroll_area, 1)

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Progress label
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("font-size: 11px; color: #666;")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        # Action buttons
        button_layout = QHBoxLayout()

        # Selection buttons
        self.select_all_btn = QPushButton("Select All New")
        self.select_all_btn.clicked.connect(self._select_all_new)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(self._deselect_all)

        button_layout.addWidget(self.select_all_btn)
        button_layout.addWidget(self.deselect_all_btn)
        button_layout.addStretch()

        # Import/Cancel buttons
        self.import_btn = QPushButton("Import Selected")
        self.import_btn.setDefault(True)
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                padding: 8px 20px;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QPushButton:disabled {
                background-color: #ccc;
            }
        """)
        self.import_btn.clicked.connect(self._start_import)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.import_btn)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def _scan_device(self):
        """Scan device folder for media files (Phase 2: with incremental support)"""
        try:
            self.status_label.setText("Scanning device...")

            # Phase 2: Use incremental scan if device_id available
            if self.device_id and self.show_new_only:
                # Incremental scan - only new files
                all_files = self.import_service.scan_with_tracking(
                    self.device_folder_path,
                    self.root_path
                )
                self.media_files = [f for f in all_files if f.import_status == "new"]
                total_count = len(all_files)
                new_count = len(self.media_files)

                # Update stats label
                if self.stats_label:
                    self.stats_label.setText(f"📊 {new_count} new / {total_count} total files")

            elif self.device_id:
                # Show all files with tracking
                self.media_files = self.import_service.scan_with_tracking(
                    self.device_folder_path,
                    self.root_path
                )
                new_count = sum(1 for f in self.media_files if f.import_status == "new")
                total_count = len(self.media_files)

                # Update stats label
                if self.stats_label:
                    self.stats_label.setText(f"📊 {new_count} new / {total_count} total files")

            else:
                # No device tracking - basic scan
                self.media_files = self.import_service.scan_device_folder(self.device_folder_path)
                new_count = sum(1 for f in self.media_files if not f.already_imported)
                total_count = len(self.media_files)

            if not self.media_files:
                if self.show_new_only and self.device_id:
                    self.status_label.setText("✅ All files from this device have been imported!")
                else:
                    self.status_label.setText("No media files found on device.")
                self.import_btn.setEnabled(False)
                return

            # Count new vs already imported
            new_count = sum(1 for f in self.media_files if not f.already_imported)
            imported_count = len(self.media_files) - new_count

            # Phase 3: Count cross-device duplicates
            cross_device_dup_count = sum(
                1 for f in self.media_files if f.is_cross_device_duplicate
            )

            self.status_label.setText(
                f"Found {len(self.media_files)} files "
                f"({new_count} new, {imported_count} already imported)"
            )

            # Phase 3: Update duplicate count label
            if self.duplicate_count_label and cross_device_dup_count > 0:
                self.duplicate_count_label.setText(
                    f"⚠️ {cross_device_dup_count} cross-device duplicate(s)"
                )
            elif self.duplicate_count_label:
                self.duplicate_count_label.setText("")

            # Display thumbnails
            self._display_thumbnails()

        except Exception as e:
            self.status_label.setText(f"Error scanning device: {e}")
            self.import_btn.setEnabled(False)
            print(f"[ImportDialog] Scan error: {e}")
            import traceback
            traceback.print_exc()

    def _display_thumbnails(self):
        """Display media file thumbnails in grid (Phase 3: with duplicate handling)"""
        columns = 5  # 5 thumbnails per row

        for idx, media_file in enumerate(self.media_files):
            row = idx // columns
            col = idx % columns

            # Phase 3: Pass skip_duplicates setting to widget
            thumbnail_widget = MediaThumbnailWidget(
                media_file, self,
                skip_duplicates=self.skip_cross_device_duplicates
            )
            thumbnail_widget.toggled.connect(self._update_import_button)

            self.thumbnail_widgets.append(thumbnail_widget)
            self.grid_layout.addWidget(thumbnail_widget, row, col)

        self._update_import_button()

    def _select_all_new(self):
        """Select all new (not imported) files"""
        for widget in self.thumbnail_widgets:
            if not widget.media_file.already_imported:
                widget.checkbox.setChecked(True)

    def _deselect_all(self):
        """Deselect all files"""
        for widget in self.thumbnail_widgets:
            widget.checkbox.setChecked(False)

    def _on_filter_toggled(self, checked: bool):
        """Handle 'Show New Files Only' filter toggle (Phase 2)"""
        self.show_new_only = checked

        # Clear current thumbnails
        for widget in self.thumbnail_widgets:
            widget.deleteLater()
        self.thumbnail_widgets.clear()

        # Clear grid layout
        for i in reversed(range(self.grid_layout.count())):
            self.grid_layout.itemAt(i).widget().setParent(None)

        # Re-scan with new filter
        self._scan_device()

    def _on_duplicate_handling_toggled(self, checked: bool):
        """Handle duplicate handling checkbox toggle (Phase 3)"""
        self.skip_cross_device_duplicates = checked

        # Update selection state of cross-device duplicates
        for widget in self.thumbnail_widgets:
            if widget.media_file.is_cross_device_duplicate:
                if self.skip_cross_device_duplicates:
                    # Deselect duplicates
                    widget.checkbox.setChecked(False)
                else:
                    # Re-select if not already imported
                    if not widget.media_file.already_imported:
                        widget.checkbox.setChecked(True)

        self._update_import_button()

    def _update_import_button(self):
        """Update import button text with count"""
        selected_count = sum(1 for w in self.thumbnail_widgets if w.is_selected())

        if selected_count > 0:
            self.import_btn.setText(f"Import {selected_count} Selected")
            self.import_btn.setEnabled(True)
        else:
            self.import_btn.setText("Import Selected")
            self.import_btn.setEnabled(False)

    def _start_import(self):
        """Start importing selected files (Phase 2: with session tracking)"""
        # Get selected files
        selected_files = [
            w.media_file for w in self.thumbnail_widgets if w.is_selected()
        ]

        if not selected_files:
            QMessageBox.warning(self, "No Selection", "Please select files to import.")
            return

        # Confirm import
        reply = QMessageBox.question(
            self,
            "Confirm Import",
            f"Import {len(selected_files)} file(s) to this project?\n\n"
            f"Files will be copied to the project directory.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply != QMessageBox.Yes:
            return

        # Phase 2: Start import session tracking
        if self.device_id:
            session_id = self.import_service.start_import_session(import_type="manual")
            self.current_session_id = session_id
            print(f"[ImportDialog] Started import session {session_id}")
        else:
            self.current_session_id = None

        # Disable UI during import
        self.import_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.select_all_btn.setEnabled(False)
        self.deselect_all_btn.setEnabled(False)

        # Show progress bar
        self.progress_bar.setMaximum(len(selected_files))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.progress_label.show()

        # Create worker
        worker = DeviceImportWorker(
            self.import_service,
            selected_files,
            destination_folder_id=None  # Import to root
        )

        # Connect signals (guarded against shutdown/teardown)
        gen = int(getattr(self.parent() or self.window(), "_ui_generation", self._ui_generation))
        connect_guarded(worker.signals.progress, self, self._on_import_progress, generation=gen)
        connect_guarded(worker.signals.finished, self, self._on_import_finished, generation=gen)
        connect_guarded(worker.signals.error, self, self._on_import_error, generation=gen)

        # Start import in background
        QThreadPool.globalInstance().start(worker)

    def _on_import_progress(self, current: int, total: int, filename: str):
        """Handle import progress update"""
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Importing {filename}... ({current}/{total})")

    def _on_import_finished(self, stats: dict):
        """Handle import completion (Phase 2: with session completion)"""
        self.progress_bar.hide()
        self.progress_label.hide()

        # Phase 2: Complete import session tracking
        if self.device_id and self.current_session_id:
            try:
                self.import_service.complete_import_session(
                    self.current_session_id,
                    stats
                )
                print(f"[ImportDialog] Completed import session {self.current_session_id}")
                print(f"[ImportDialog] Session stats: {stats['imported']} imported, "
                      f"{stats['skipped']} skipped, {stats.get('bytes_imported', 0)} bytes")
            except Exception as e:
                print(f"[ImportDialog] Failed to complete import session: {e}")

        # Show results
        message = (
            f"Import completed!\n\n"
            f"Imported: {stats['imported']}\n"
            f"Skipped: {stats['skipped']}\n"
            f"Failed: {stats['failed']}"
        )

        if stats['errors']:
            message += f"\n\nErrors:\n" + "\n".join(stats['errors'][:5])

        QMessageBox.information(self, "Import Complete", message)

        # Close dialog if successful
        if stats['imported'] > 0:
            self.accept()
        else:
            # Re-enable UI to allow retry
            self.import_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.select_all_btn.setEnabled(True)
            self.deselect_all_btn.setEnabled(True)

    def _on_import_error(self, error_msg: str):
        """Handle import error"""
        self.progress_bar.hide()
        self.progress_label.hide()

        QMessageBox.critical(self, "Import Error", f"Import failed:\n{error_msg}")

        # Re-enable UI
        self.import_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.select_all_btn.setEnabled(True)
        self.deselect_all_btn.setEnabled(True)
