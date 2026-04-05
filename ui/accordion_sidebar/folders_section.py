# ui/accordion_sidebar/folders_section.py
# Folders section for accordion sidebar

import threading
import traceback
import logging
from typing import Optional
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView, QSizePolicy, QLabel
from PySide6.QtCore import Signal, Qt, QObject
from PySide6.QtGui import QColor
from reference_db import ReferenceDB
from translation_manager import tr
from .base_section import BaseSection

logger = logging.getLogger(__name__)


class FoldersSectionSignals(QObject):
    """Signals for folders section loading."""
    loaded = Signal(int, list)  # (generation, folder_rows)
    error = Signal(int, str)    # (generation, error_message)


class FoldersSection(BaseSection):
    """
    Folders section implementation.

    Displays hierarchical folder tree with photo/video counts.
    Supports recursive folder structures and count aggregation.
    """

    # Signal emitted when folder is selected (double-click)
    folderSelected = Signal(int)  # folder_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = FoldersSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)
        self.signals.error.connect(self._on_error)
        self._loaded_project_id = None
        self._tree_built = False

        # Store DB reference for recursive queries (main thread only)
        self.db: Optional[ReferenceDB] = None

    def get_section_id(self) -> str:
        return "folders"

    def get_title(self) -> str:
        return tr('sidebar.header_folder')

    def get_icon(self) -> str:
        return "📁"

    def set_db(self, db: ReferenceDB):
        """Set database reference (for recursive tree building)."""
        self.db = db

    def load_section(self) -> None:
        """Load folders from database in background thread."""
        if not self.project_id:
            logger.warning("[FoldersSection] No project_id set")
            return

        # Skip rebuild if already current for this project
        if self._loaded_project_id == self.project_id and self._tree_built and not self._loading:
            logger.info("[FoldersSection] Skipping reload, already current for project %s", self.project_id)
            return

        # Increment generation
        self._generation += 1
        current_gen = self._generation
        self._loading = True

        logger.info(f"[FoldersSection] Loading folders (generation {current_gen})...")

        # Background worker
        def work():
            db = None
            try:
                db = ReferenceDB()  # Per-thread instance
                rows = db.get_all_folders(self.project_id) or []
                logger.info(f"[FoldersSection] Loaded {len(rows)} folders (gen {current_gen})")
                return rows
            except Exception as e:
                error_msg = f"Error loading folders: {e}"
                logger.error(f"[FoldersSection] {error_msg}")
                traceback.print_exc()
                return []
            finally:
                if db:
                    try:
                        db.close()
                    except Exception:
                        pass

        # Run in thread — always emit so _on_data_loaded resets _loading
        def on_complete():
            try:
                rows = work()
                self.signals.loaded.emit(current_gen, rows)
            except Exception as e:
                logger.error(f"[FoldersSection] Error in worker thread: {e}")
                traceback.print_exc()
                self.signals.error.emit(current_gen, str(e))

        threading.Thread(target=on_complete, daemon=True).start()

    def create_content_widget(self, data):
        """Create folder tree widget."""
        rows = data  # List of folder rows from database

        # Create tree widget
        tree = QTreeWidget()
        tree.setHeaderLabels([self.get_title(), "Photos | Videos"])
        tree.setColumnCount(2)
        tree.setSelectionMode(QTreeWidget.SingleSelection)
        tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        tree.setAlternatingRowColors(True)
        tree.setMinimumHeight(200)
        tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        tree.setStyleSheet("""
            QTreeWidget {
                border: none;
                background: transparent;
            }
            QTreeWidget::item {
                padding: 4px;
            }
            QTreeWidget::item:hover {
                background: #f1f3f4;
            }
            QTreeWidget::item:selected {
                background: #e8f0fe;
                color: #1a73e8;
            }
        """)

        # Build tree structure recursively
        try:
            self._add_folder_tree_items(tree, None)
        except Exception as e:
            logger.error(f"[FoldersSection] Error building tree: {e}")
            traceback.print_exc()

        if tree.topLevelItemCount() == 0:
            # No folders - return placeholder
            placeholder = QLabel("No folders found")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("padding: 20px; color: #666;")
            return placeholder

        # Connect double-click to emit folder selection
        tree.itemDoubleClicked.connect(
            lambda item, col: (
                self.folderSelected.emit(item.data(0, Qt.UserRole))
                if item.data(0, Qt.UserRole) is not None else None
            )
        )

        logger.info(f"[FoldersSection] Tree built with {tree.topLevelItemCount()} top-level folders")
        return tree

    def _add_folder_tree_items(self, parent_widget_or_item, parent_id=None):
        """
        Recursively add folder items to QTreeWidget.

        Args:
            parent_widget_or_item: QTreeWidget or QTreeWidgetItem to add children to
            parent_id: Parent folder ID (None for root folders)
        """
        if not self.db:
            logger.warning("[FoldersSection] No database reference set")
            return

        try:
            rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        except Exception as e:
            logger.error(f"[FoldersSection] get_child_folders({parent_id}) failed: {e}")
            return

        for row in rows:
            name = row["name"]
            fid = row["id"]

            # Get recursive photo count (includes subfolders)
            if hasattr(self.db, "get_image_count_recursive"):
                photo_count = int(self.db.get_image_count_recursive(fid, project_id=self.project_id) or 0)
            else:
                try:
                    folder_paths = self.db.get_images_by_folder(fid, project_id=self.project_id)
                    photo_count = len(folder_paths) if folder_paths else 0
                except Exception:
                    photo_count = 0

            # Get recursive video count (includes subfolders)
            if hasattr(self.db, "get_video_count_recursive"):
                video_count = int(self.db.get_video_count_recursive(fid, project_id=self.project_id) or 0)
            else:
                video_count = 0

            # Format count display with emoji icons
            if photo_count > 0 and video_count > 0:
                count_text = f"{photo_count}📷 {video_count}🎬"
            elif video_count > 0:
                count_text = f"{video_count}🎬"
            else:
                count_text = f"{photo_count:>5}"

            # Create tree item with emoji prefix
            item = QTreeWidgetItem([f"📁 {name}", count_text])
            item.setData(0, Qt.UserRole, int(fid))

            # Set count column formatting (right-aligned, grey color)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setForeground(1, QColor("#888888"))

            # Add to parent
            if isinstance(parent_widget_or_item, QTreeWidget):
                parent_widget_or_item.addTopLevelItem(item)
            else:
                parent_widget_or_item.addChild(item)

            # Recursively add child folders
            self._add_folder_tree_items(item, fid)

    def _count_tree_folders(self, tree):
        """Count total folders in tree."""
        count = 0

        def count_recursive(parent_item):
            nonlocal count
            for i in range(parent_item.childCount()):
                count += 1
                count_recursive(parent_item.child(i))

        for i in range(tree.topLevelItemCount()):
            count += 1
            count_recursive(tree.topLevelItem(i))

        return count

    def _on_data_loaded(self, generation: int, rows: list):
        """Callback when folders data is loaded."""
        # Always reset _loading — the background thread is done regardless
        # of whether the data is stale.  Without this, a generation bump
        # during loading leaves _loading=True permanently, blocking all
        # future reloads of this section.
        self._loading = False

        if generation != self._generation:
            logger.debug(f"[FoldersSection] Discarding stale data (gen {generation} vs {self._generation})")
            return

        self._loaded_project_id = self.project_id
        self._tree_built = True

        # NOTE: Do NOT call create_content_widget here - AccordionSidebar._on_section_loaded
        # handles widget creation when it receives the loaded signal. Calling it here
        # would cause duplicate widget creation.
        logger.info(f"[FoldersSection] Data loaded successfully (gen {generation})")

    def _on_error(self, generation: int, error_msg: str):
        """Callback when folders loading fails."""
        self._loading = False
        if generation != self._generation:
            return
        logger.error(f"[FoldersSection] Load failed: {error_msg}")
