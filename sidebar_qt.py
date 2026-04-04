# sidebar_qt.py
# Version 10.01.01.03 dated 20260115
# Tab-based sidebar with per-tab status labels, improved timeout handling,
# and dynamic branch/folder/date/tag loading.

from PySide6.QtWidgets import (
    QWidget, QTreeView, QMenu, QFileDialog,
    QVBoxLayout, QMessageBox, QTreeWidgetItem, QTreeWidget,
    QHeaderView, QHBoxLayout, QPushButton, QLabel, QTabWidget, QListWidget, QListWidgetItem, QProgressBar, QAbstractItemView,
    QTableWidget, QTableWidgetItem, QScrollArea, QLineEdit, QTextBrowser, QDialog, QFrame
)
from PySide6.QtCore import Qt, QPoint, Signal, QTimer, QSize
from PySide6.QtGui import (
    QStandardItemModel, QStandardItem,
    QFont, QColor, QIcon, QImage,
    QTransform, QPainter, QPixmap
)

from app_services import list_branches, export_branch
from reference_db import ReferenceDB
from services.tag_service import get_tag_service
from services.device_monitor import get_device_monitor  # OPTIMIZATION: Windows device change detection
from ui.people_list_view import PeopleListView, make_circular_pixmap
from translation_manager import tr
from utils.qt_guards import connect_guarded

import threading
import traceback
import time
import re
import os

from datetime import datetime
from PIL import Image, ImageOps
from io import BytesIO


# SettingsManager is used to persist sidebar display preference
try:
    from settings_manager_qt import SettingsManager
except Exception:
    SettingsManager = None



from PySide6.QtCore import Signal, QObject


# === Phase 3: Drag & Drop Support ===
class DroppableTreeView(QTreeView):
    """
    Custom QTreeView that accepts photo drops for folder assignment.
    Emits photoDropped signal with (folder_id, photo_paths) when photos are dropped.
    """
    photoDropped = Signal(int, list)  # (folder_id, list of photo paths)
    tagDropped = Signal(str, list)    # (tag_name, list of photo paths)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        """Accept drag events if they contain photo paths."""
        if event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-photo-paths'):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Update drop indicator as drag moves over items."""
        if event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-photo-paths'):
            # Find the item under the cursor
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self.setCurrentIndex(index)
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle photo drop onto folder/tag."""
        if not (event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-photo-paths')):
            event.ignore()
            return

        # Get the item where photos were dropped
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            event.ignore()
            return

        # Extract photo paths from MIME data
        paths = []
        if event.mimeData().hasFormat('application/x-photo-paths'):
            paths_data = event.mimeData().data('application/x-photo-paths')
            paths_text = bytes(paths_data).decode('utf-8')
            paths = [p.strip() for p in paths_text.split('\n') if p.strip()]
        elif event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]

        if not paths:
            event.ignore()
            return

        # Get the folder/branch ID from the item
        item = self.model().itemFromIndex(index)
        if not item:
            event.ignore()
            return

        # Check item type and emit appropriate signal
        folder_id = item.data(Qt.UserRole)
        branch_key = item.data(Qt.UserRole + 1)

        if folder_id is not None:
            # Dropped on folder - emit photoDropped signal
            print(f"[DragDrop] Dropped {len(paths)} photo(s) on folder ID: {folder_id}")
            self.photoDropped.emit(folder_id, paths)
            event.acceptProposedAction()
        elif branch_key is not None:
            # Dropped on branch/tag - emit tagDropped signal
            print(f"[DragDrop] Dropped {len(paths)} photo(s) on branch: {branch_key}")
            self.tagDropped.emit(branch_key, paths)
            event.acceptProposedAction()
        else:
            event.ignore()


# =====================================================================
# 1️: SidebarTabs — full tabs-based controller (new)
# ====================================================================

class SidebarTabs(QWidget):
    # Signals to parent (SidebarQt/MainWindow) so the grid can change context
    selectBranch = Signal(str)     # branch_key    e.g. "all" or "face_john"
    selectFolder = Signal(int)     # folder_id
    selectDate   = Signal(str)     # e.g. "2025-10" or "2025"
    selectTag    = Signal(str)     # tag name

    # Signals for async worker completion
    # ▼ add with your other Signals
    _finishBranchesSig = Signal(int, list, float, int)  # (idx, rows, started, gen)
    _finishFoldersSig  = Signal(int, list, float, int)
    _finishDuplicatesSig = Signal(int, dict, float, int)  # 🔍 Phase 3A (idx, counts_dict, started, gen)
    _finishDatesSig    = Signal(int, object, float, int)  # object to accept dict or list
    _finishTagsSig     = Signal(int, list, float, int)
    _finishPeopleSig   = Signal(int, list, float, int)  # 👥 NEW
    _finishQuickSig    = Signal(int, list, float, int)  # Quick dates

    
    def __init__(self, project_id: int | None, parent=None):
        super().__init__(parent)
        self._dbg("__init__ started")
        self.db = ReferenceDB()
        self.project_id = project_id

        # internal state (lives here now)
        self._tab_populated: set[str] = set()
        self._tab_loading: set[str]   = set()
        self._tab_timers: dict[int, QTimer] = {}
        self._tab_status_labels: dict[int, QLabel] = {}
        self._count_targets: list[tuple] = []               # optional future use
        self._tab_indexes: dict[str, int] = {}              # "branches"/"folders"/"dates"/"tags"/"quick" -> tab index
        # ▼ add near your state vars
        self._tab_gen: dict[str, int] = {"branches":0, "folders":0, "duplicates":0, "dates":0, "tags":0, "quick":0}
        # Guard against concurrent refresh_all calls
        self._refreshing_all = False

        # UI
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.tab_widget = QTabWidget()
        v.addWidget(self.tab_widget, 1)

        # connections - Use Qt.QueuedConnection to ensure slots run in main thread
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self._finishBranchesSig.connect(self._finish_branches, Qt.QueuedConnection)
        self._finishFoldersSig.connect(self._finish_folders, Qt.QueuedConnection)
        self._finishDuplicatesSig.connect(self._finish_duplicates, Qt.QueuedConnection)  # 🔍 Phase 3A
        self._finishDatesSig.connect(self._finish_dates, Qt.QueuedConnection)
        self._finishTagsSig.connect(self._finish_tags, Qt.QueuedConnection)
        self._finishPeopleSig.connect(self._finish_people, Qt.QueuedConnection)
        self._finishQuickSig.connect(self._finish_quick, Qt.QueuedConnection)

        # initial build – do not populate yet
        self._build_tabs()
        self._dbg("__init__ completed")

    # === helper for consistent debug output ===
    def _bump_gen(self, tab_type:str) -> int:
        g = (self._tab_gen.get(tab_type, 0) + 1) % 1_000_000
        self._tab_gen[tab_type] = g
        return g

    def _is_stale(self, tab_type:str, gen:int) -> bool:
        return gen != self._tab_gen.get(tab_type, -1)
        
    
    def _dbg(self, msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{ts}] [Tabs] {msg}")

    # ---------- public API ----------
    def set_project(self, project_id: int | None):
        self.project_id = project_id
        self.refresh_all(force=True)

    def refresh_all(self, force=False):
        """Repopulate tabs (typically after scans or project switch)."""
        self._dbg(f"refresh_all(force={force}) called")

        # Guard against concurrent refresh_all calls
        if self._refreshing_all:
            self._dbg("refresh_all blocked - already refreshing")
            return

        try:
            self._refreshing_all = True
            for key in ("branches", "folders", "duplicates", "dates", "tags", "quick"):
                idx = self._tab_indexes.get(key)
                self._dbg(f"refresh_all: key={key}, idx={idx}, force={force}")
                if idx is not None:
                    self._populate_tab(key, idx, force=force)
            self._dbg(f"refresh_all(force={force}) completed")
        finally:
            self._refreshing_all = False

    def refresh_tab(self, tab_name: str):
        """Refresh a single tab (e.g., 'tags', 'folders', 'dates')."""
        self._dbg(f"refresh_tab({tab_name}) called")
        idx = self._tab_indexes.get(tab_name)
        if idx is not None:
            self._populate_tab(tab_name, idx, force=True)
            self._dbg(f"refresh_tab({tab_name}) completed")
        else:
            self._dbg(f"refresh_tab({tab_name}) - tab not found")

    def show_tabs(self): self.show()
    def hide_tabs(self):
        """Hide tabs and cancel any pending workers"""
        self._dbg("hide_tabs() called - canceling pending workers")

        # CRITICAL FIX: Cleanup PeopleListView before hiding
        # This ensures signals are disconnected and prevents crashes on toggle
        if hasattr(self, 'people_list_view') and self.people_list_view:
            try:
                if hasattr(self.people_list_view, '_cleanup'):
                    self._dbg("hide_tabs() - calling _cleanup() on people_list_view")
                    self.people_list_view._cleanup()
            except (RuntimeError, AttributeError) as e:
                self._dbg(f"hide_tabs() - people_list_view cleanup error: {e}")
            self.people_list_view = None

        # Bump all generations to invalidate any in-flight workers
        for key in self._tab_gen.keys():
            self._bump_gen(key)
        # Clear loading state
        self._tab_loading.clear()
        # Cancel all timers
        for idx, timer in list(self._tab_timers.items()):
            try:
                timer.stop()
            except (RuntimeError, AttributeError) as e:
                # RuntimeError: wrapped C/C++ object has been deleted
                # AttributeError: timer is None or not a QTimer
                pass
        self._tab_timers.clear()
        self._tab_status_labels.clear()
        self.hide()

    # ---------- internal ----------
    def _build_tabs(self):
        self._dbg("_build_tabs → building tab widgets")
        self.tab_widget.clear()
        self._tab_indexes.clear()

        for tab_type, label in [
            ("branches", "Branches"),
            ("folders",  "Folders"),
            ("duplicates", "⚡ Duplicates"),  # 🔍 Phase 3A
            ("dates",    "By Date"),
            ("tags",     "Tags"),
            ("people",   "People"),          # 👥 NEW
            ("quick",    "Quick Dates"),
        ]:

            w = QWidget()
            w.setProperty("tab_type", tab_type)
            v = QVBoxLayout(w)
            v.setContentsMargins(6, 6, 6, 6)
            v.addWidget(QLabel(f"Loading {label}…"))
            idx = self.tab_widget.addTab(w, label)
            self._tab_indexes[tab_type] = idx

        self._tab_loading.clear()
        self._tab_populated.clear()
        QTimer.singleShot(0, lambda: self._on_tab_changed(self.tab_widget.currentIndex()))
        self._dbg(f"_build_tabs → added {len(self._tab_indexes)} tabs")

    def _on_tab_changed(self, idx: int):
        self._dbg(f"_on_tab_changed(idx={idx})")
        if idx < 0:
            return
        w = self.tab_widget.widget(idx)
        tab_type = w.property("tab_type") if w else None
        if not tab_type:
            return
        self._start_timeout(idx, tab_type)
        self._populate_tab(tab_type, idx)
        self._dbg(f"_on_tab_changed → tab_type={tab_type}")

    def _start_timeout(self, idx, tab_type, ms=120000):
        self._dbg(f"_start_timeout idx={idx} type={tab_type}")

        t = self._tab_timers.get(idx)
        if t:
            try:
                t.stop()
            except (RuntimeError, AttributeError):
                pass
        timer = QTimer(self)
        timer.setSingleShot(True)

        def on_to():
            self._dbg(f"⚠️ timeout reached for tab={tab_type}")

            if tab_type in self._tab_loading:
                self._tab_loading.discard(tab_type)
                self._clear_tab(idx)
                self._set_tab_empty(idx, "No items (timeout)")
            self._tab_timers.pop(idx, None)
            self._tab_status_labels.pop(idx, None)

        timer.timeout.connect(on_to)
        timer.start(ms)
        self._tab_timers[idx] = timer

    def _cancel_timeout(self, idx):
        t = self._tab_timers.pop(idx, None)
        if t:
            try:
                t.stop()
            except (RuntimeError, AttributeError):
                pass

    def _show_loading(self, idx, label="Loading…"):
        self._dbg(f"_show_loading idx={idx} label='{label}'")

        self._clear_tab(idx)
        tab = self.tab_widget.widget(idx)
        v = tab.layout()
        title = QLabel(f"<b>{label}</b>")
        pb = QProgressBar(); pb.setRange(0,0)
        st = QLabel(""); st.setStyleSheet("color:#666; font-size:11px;")
        v.addWidget(title); v.addWidget(pb); v.addWidget(st)
        self._tab_status_labels[idx] = st

    def _clear_tab(self, idx):
        self._dbg(f"_clear_tab idx={idx}")
        self._cancel_timeout(idx)

        tab = self.tab_widget.widget(idx)
        if not tab:
            self._dbg(f"_clear_tab idx={idx} - tab is None, skipping")
            return
        v = tab.layout()
        if not v:
            self._dbg(f"_clear_tab idx={idx} - layout is None, skipping")
            return
        try:
            for i in reversed(range(v.count())):
                item = v.itemAt(i)
                if not item:
                    continue
                w = item.widget()
                if w:
                    # CRITICAL FIX: Call _cleanup() on widgets that have it
                    # This ensures signals are disconnected and event filters removed
                    # BEFORE deleteLater() is called, preventing crashes from pending signals
                    if hasattr(w, '_cleanup') and callable(w._cleanup):
                        try:
                            self._dbg(f"_clear_tab idx={idx} - calling _cleanup() on {type(w).__name__}")
                            w._cleanup()
                        except Exception as cleanup_err:
                            self._dbg(f"_clear_tab idx={idx} - _cleanup() failed: {cleanup_err}")

                    w.setParent(None)
                    w.deleteLater()
        except Exception as e:
            self._dbg(f"_clear_tab idx={idx} - Exception during widget cleanup: {e}")
            import traceback
            traceback.print_exc()

    def _set_tab_empty(self, idx, msg="No items"):
        tab = self.tab_widget.widget(idx)
        if not tab: return
        v = tab.layout()
        v.addWidget(QLabel(f"<b>{msg}</b>"))

    def _wrap_in_scroll_area(self, widget):
        """Wrap a widget in a QScrollArea for vertical scrolling support"""
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll

    # ---------- collapse/expand support ----------

    def toggle_collapse_expand(self):
        """Toggle collapse/expand all for tree widgets in current tab"""
        try:
            current_idx = self.tab_widget.currentIndex()
            tab = self.tab_widget.widget(current_idx)
            if not tab:
                return

            # Find QTreeWidget in current tab
            tree = None
            for i in range(tab.layout().count()):
                widget = tab.layout().itemAt(i).widget()
                if isinstance(widget, QTreeWidget):
                    tree = widget
                    break

            if not tree:
                return

            # Check if any items are expanded
            any_expanded = False
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if item.isExpanded():
                    any_expanded = True
                    break

            # Toggle: collapse all if any expanded, else expand all
            if any_expanded:
                tree.collapseAll()
            else:
                tree.expandAll()

        except Exception as e:
            print(f"[SidebarTabs] toggle_collapse_expand failed: {e}")

    # ---------- population dispatcher ----------

    def _populate_tab(self, tab_type: str, idx: int, force=False):
        self._dbg(f"_populate_tab({tab_type}, idx={idx}, force={force})")
        self._dbg(f"  populated={tab_type in self._tab_populated}, loading={tab_type in self._tab_loading}")

        # Force refresh: clear both populated and loading states
        if force:
            if tab_type in self._tab_populated:
                self._dbg(f"  Force refresh: removing {tab_type} from populated set")
                self._tab_populated.discard(tab_type)
            if tab_type in self._tab_loading:
                self._dbg(f"  Force refresh: removing {tab_type} from loading set (canceling in-progress)")
                self._tab_loading.discard(tab_type)
                # Bump generation to invalidate any in-progress workers
                self._bump_gen(tab_type)

        if tab_type in self._tab_populated or tab_type in self._tab_loading:
            self._dbg(f"  Skipping {tab_type}: already populated or loading")
            if tab_type == "branches":
                self._set_branch_context_from_list(idx)
            return

        self._dbg(f"  Starting load for {tab_type}")
        self._tab_loading.add(tab_type)
        gen = self._bump_gen(tab_type)

        if tab_type == "branches":
            self._show_loading(idx, "Loading Branches…")
            self._load_branches(idx, gen)
        elif tab_type == "folders":
            self._show_loading(idx, "Loading Folders…")
            self._load_folders(idx, gen)
        elif tab_type == "duplicates":
            self._show_loading(idx, "Loading Duplicates…")
            self._load_duplicates(idx, gen)
        elif tab_type == "dates":
            self._show_loading(idx, "Loading Dates…")
            self._load_dates(idx, gen)
        elif tab_type == "tags":
            self._show_loading(idx, "Loading Tags…")
            self._load_tags(idx, gen)
        elif tab_type == "people":
            self._show_loading(idx, "Loading People…")
            self._load_people(idx, gen)

        elif tab_type == "quick":
            self._show_loading(idx, "Loading Quick Dates…")
            self._load_quick(idx, gen)

    # ---------- branches ----------
    def _load_branches(self, idx:int, gen:int):
        started = time.time()
        def work():
            try:
                rows = []
                if self.project_id:
                    rows = self.db.get_branches(self.project_id) or []
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishBranchesSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- BRANCHES ----------
    def _finish_branches(self, idx:int, rows:list, started:float, gen:int):
        if self._is_stale("branches", gen):
            self._dbg(f"_finish_branches (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        # normalize to [(key, name, count)]
        norm = []
        for r in (rows or []):
            count = None
            if isinstance(r, (tuple, list)) and len(r) >= 2:
                key, name = r[0], r[1]
                count = r[2] if len(r) >= 3 else None
            elif isinstance(r, dict):
                key  = r.get("branch_key") or r.get("key") or r.get("id") or r.get("name")
                name = r.get("display_name") or r.get("label") or r.get("name") or str(key)
                count = r.get("count")
            else:
                key = name = str(r)
            if key is None:
                continue
            norm.append((str(key), str(name), count))

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel(f"<b>{tr('sidebar.branches')}</b>"))

        # Create 2-column table: Branch/Folder | Photos
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Branch/Folder", "Photos"])
        table.setRowCount(len(norm))
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        for row, (key, name, count) in enumerate(norm):
            # Column 0: Branch name
            item_name = QTableWidgetItem(name)
            item_name.setData(Qt.UserRole, key)
            table.setItem(row, 0, item_name)

            # Column 1: Count (right-aligned, light grey like List view)
            count_str = str(count) if count is not None else "0"
            item_count = QTableWidgetItem(count_str)
            item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_count.setForeground(QColor("#BBBBBB"))
            table.setItem(row, 1, item_count)

        table.cellDoubleClicked.connect(lambda row, col: self.selectBranch.emit(table.item(row, 0).data(Qt.UserRole)))
        tab.layout().addWidget(self._wrap_in_scroll_area(table), 1)

        self._tab_populated.add("branches")
        self._tab_loading.discard("branches")
        st = self._tab_status_labels.get(idx)
        if st: st.setText(f"{len(norm)} item(s) • {time.time()-started:.2f}s")
        if norm:
            self.selectBranch.emit(norm[0][0])

    def _set_branch_context_from_list(self, idx):
        tab = self.tab_widget.widget(idx)
        if not tab: return
        try:
            # Find QTableWidget in tab layout
            table = next((tab.layout().itemAt(i).widget()
                          for i in range(tab.layout().count())
                          if isinstance(tab.layout().itemAt(i).widget(), QTableWidget)), None)
            if table and table.currentRow() >= 0:
                self.selectBranch.emit(table.item(table.currentRow(), 0).data(Qt.UserRole))
        except Exception:
            pass

    # ---------- folders ----------
    def _load_folders(self, idx:int, gen:int):
        started = time.time()
        def work():
            try:
                # CRITICAL FIX: Pass project_id to filter folders by project
                rows = self.db.get_all_folders(self.project_id) or []    # expect list[dict{id,path}] or tuples
                self._dbg(f"_load_folders → got {len(rows)} rows for project_id={self.project_id}")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishFoldersSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- FOLDERS ----------
    def _finish_folders(self, idx:int, rows:list, started:float, gen:int):
        if self._is_stale("folders", gen):
            self._dbg(f"_finish_folders (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel(f"<b>{tr('sidebar.by_folder')}</b>"))

        # Create tree widget matching List view's Folders-Branch appearance
        tree = QTreeWidget()
        tree.setHeaderLabels([tr('sidebar.header_folder'), tr('sidebar.header_photos')])
        tree.setColumnCount(2)
        tree.setSelectionMode(QTreeWidget.SingleSelection)
        tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        tree.setAlternatingRowColors(True)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        # Build tree structure recursively using database hierarchy (like List view)
        try:
            self._add_folder_tree_items(tree, None)
        except Exception as e:
            print(f"[SidebarTabs] _finish_folders tree build failed: {e}")
            traceback.print_exc()

        if tree.topLevelItemCount() == 0:
            self._set_tab_empty(idx, "No folders found")
        else:
            # Connect double-click to emit folder selection
            tree.itemDoubleClicked.connect(
                lambda item, col: self.selectFolder.emit(item.data(0, Qt.UserRole)) if item.data(0, Qt.UserRole) else None
            )
            tab.layout().addWidget(self._wrap_in_scroll_area(tree), 1)

        self._tab_populated.add("folders")
        self._tab_loading.discard("folders")
        st = self._tab_status_labels.get(idx)
        folder_count = self._count_tree_folders(tree)
        if st: st.setText(f"{folder_count} folder(s) • {time.time()-started:.2f}s")

    def _add_folder_tree_items(self, parent_widget_or_item, parent_id=None):
        """Recursively add folder items to QTreeWidget (matches List view's _add_folder_items)"""
        try:
            rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        except Exception as e:
            print(f"[SidebarTabs] get_child_folders({parent_id}, project_id={self.project_id}) failed: {e}")
            return

        for row in rows:
            name = row["name"]
            fid = row["id"]

            # Get recursive photo count (includes subfolders)
            if hasattr(self.db, "get_image_count_recursive"):
                photo_count = int(self.db.get_image_count_recursive(fid) or 0)
            else:
                # Fallback to non-recursive count
                try:
                    folder_paths = self.db.get_images_by_folder(fid, project_id=self.project_id)
                    photo_count = len(folder_paths) if folder_paths else 0
                except Exception:
                    photo_count = 0

            # Create tree item with emoji prefix (matching List view)
            item = QTreeWidgetItem([f"📁 {name}", f"{photo_count:>5}"])
            item.setData(0, Qt.UserRole, int(fid))

            # Set count column formatting (right-aligned, grey color like List view)
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
        """Count total folders in tree"""
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

    # ---------- duplicates ----------
    def _load_duplicates(self, idx:int, gen:int):
        """Load duplicate counts (exact, similar shots, burst groups)."""
        started = time.time()
        def work():
            try:
                from repository.asset_repository import AssetRepository
                from repository.stack_repository import StackRepository
                from repository.base_repository import DatabaseConnection

                # Initialize repositories
                db_conn = DatabaseConnection()
                asset_repo = AssetRepository(db_conn)
                stack_repo = StackRepository(db_conn)

                # Get exact duplicate counts (assets with 2+ instances)
                exact_assets = asset_repo.list_duplicate_assets(self.project_id, min_instances=2)
                exact_photo_count = sum(asset['instance_count'] for asset in exact_assets)
                exact_group_count = len(exact_assets)

                # Get similar shot stacks (type="similar")
                similar_stacks = stack_repo.list_stacks(self.project_id, stack_type="similar", limit=10000)
                similar_photo_count = 0
                for stack in similar_stacks:
                    member_count = stack_repo.count_stack_members(self.project_id, stack['stack_id'])
                    similar_photo_count += member_count
                similar_group_count = len(similar_stacks)

                counts = {
                    'exact_photos': exact_photo_count,
                    'exact_groups': exact_group_count,
                    'similar_photos': similar_photo_count,
                    'similar_groups': similar_group_count
                }

                self._dbg(f"_load_duplicates → got counts: {counts}")

            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[SidebarTabs] _load_duplicates error: {e}")
                counts = {
                    'exact_photos': 0,
                    'exact_groups': 0,
                    'similar_photos': 0,
                    'similar_groups': 0
                }

            self._finishDuplicatesSig.emit(idx, counts, started, gen)

        threading.Thread(target=work, daemon=True).start()

    # ---------- DUPLICATES ----------
    def _finish_duplicates(self, idx:int, counts:dict, started:float, gen:int):
        """Populate duplicates tab with counts and action buttons."""
        if self._is_stale("duplicates", gen):
            self._dbg(f"_finish_duplicates (stale gen={gen}) — ignoring")
            return

        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        layout = tab.layout()

        # Title
        title_label = QLabel("<b>⚡ DUPLICATES</b>")
        title_label.setStyleSheet("font-size: 11pt; padding: 4px;")
        layout.addWidget(title_label)

        # Info text
        info_label = QLabel("Manage and organize duplicate photos")
        info_label.setStyleSheet("color: #666; font-size: 9pt; padding: 2px 4px 8px 4px;")
        layout.addWidget(info_label)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Create scrollable area for duplicate types
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(12)

        # Exact Duplicates section
        exact_widget = self._create_duplicate_type_widget(
            icon="🔍",
            title="Exact Duplicates",
            photo_count=counts.get('exact_photos', 0),
            group_count=counts.get('exact_groups', 0),
            duplicate_type="exact"
        )
        scroll_layout.addWidget(exact_widget)

        # Similar Shots section
        similar_widget = self._create_duplicate_type_widget(
            icon="📸",
            title="Similar Shots",
            photo_count=counts.get('similar_photos', 0),
            group_count=counts.get('similar_groups', 0),
            duplicate_type="similar"
        )
        scroll_layout.addWidget(similar_widget)

        scroll_layout.addStretch(1)

        scroll_area.setWidget(scroll_widget)
        layout.addWidget(scroll_area, 1)

        # Bottom action buttons
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(8, 8, 8, 8)
        button_layout.setSpacing(8)

        # Refresh button
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.setToolTip("Refresh duplicate counts")
        btn_refresh.clicked.connect(lambda: self.refresh_tab("duplicates"))
        button_layout.addWidget(btn_refresh)

        # Settings button
        btn_settings = QPushButton("⚙️ Settings")
        btn_settings.setToolTip("Configure duplicate detection settings")
        btn_settings.clicked.connect(self._open_duplicate_settings)
        button_layout.addWidget(btn_settings)

        layout.addLayout(button_layout)

        # Mark as populated
        self._tab_populated.add("duplicates")
        self._tab_loading.discard("duplicates")

        # Update status
        st = self._tab_status_labels.get(idx)
        total_groups = counts.get('exact_groups', 0) + counts.get('similar_groups', 0)
        if st:
            st.setText(f"{total_groups} duplicate groups • {time.time()-started:.2f}s")

        self._dbg(f"_finish_duplicates completed: {total_groups} groups")

    def _create_duplicate_type_widget(self, icon: str, title: str, photo_count: int, group_count: int, duplicate_type: str):
        """Create a clickable widget for a duplicate type with counts."""
        widget = QWidget()
        widget.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
                border: 1px solid #ddd;
                border-radius: 6px;
                padding: 8px;
            }
            QWidget:hover {
                background-color: #e9ecef;
                border-color: #2196F3;
            }
        """)
        widget.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Title row
        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 16pt;")
        title_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold; font-size: 10pt;")
        title_layout.addWidget(title_label)
        title_layout.addStretch(1)

        layout.addLayout(title_layout)

        # Count labels
        if group_count > 0:
            count_text = f"{photo_count} photos • {group_count} groups"
            count_label = QLabel(count_text)
            count_label.setStyleSheet("color: #555; font-size: 9pt; padding-left: 32px;")
            layout.addWidget(count_label)
        else:
            no_dupes_label = QLabel("No duplicates found")
            no_dupes_label.setStyleSheet("color: #999; font-size: 9pt; padding-left: 32px;")
            layout.addWidget(no_dupes_label)

        # Store duplicate type for click handler
        widget.setProperty("duplicate_type", duplicate_type)
        widget.setProperty("has_duplicates", group_count > 0)

        # Make clickable
        widget.mousePressEvent = lambda event: self._on_duplicate_type_clicked(duplicate_type, group_count > 0)

        return widget

    def _on_duplicate_type_clicked(self, duplicate_type: str, has_duplicates: bool):
        """Handle click on duplicate type widget."""
        if not has_duplicates:
            return

        # Import here to avoid circular imports
        from layouts.google_components.duplicates_dialog import DuplicatesDialog

        # Get main window to pass as parent
        main_window = self.window()

        # Open duplicates dialog filtered to this type
        dialog = DuplicatesDialog(
            project_id=self.project_id,
            parent=main_window
        )

        # TODO: Add filtering by duplicate_type when dialog supports it
        # For now, just show all duplicates

        dialog.exec()

        # Refresh counts after dialog closes
        self.refresh_tab("duplicates")

    def _open_duplicate_settings(self):
        """Open preferences dialog to duplicate management settings."""
        try:
            # Get main window
            main_window = self.window()

            # Import preferences dialog
            from preferences_dialog import PreferencesDialog

            # Open preferences at Duplicate Management tab
            dialog = PreferencesDialog(parent=main_window)

            # Try to switch to duplicate management tab (tab index 4)
            if hasattr(dialog, 'tabs'):
                dialog.tabs.setCurrentIndex(4)

            dialog.exec()

            # Refresh duplicates tab after settings change
            self.refresh_tab("duplicates")

        except Exception as e:
            print(f"[SidebarTabs] Failed to open duplicate settings: {e}")
            import traceback
            traceback.print_exc()

    # ---------- dates ----------
    def _load_dates(self, idx:int, gen:int):
        started = time.time()
        def work():
            rows = []
            try:
                # Get hierarchical date data: {year: {month: [days]}}
                # CRITICAL FIX: Pass project_id to filter dates by project
                if hasattr(self.db, "get_date_hierarchy"):
                    hier = self.db.get_date_hierarchy(self.project_id) or {}
                    # Also get year counts - now filtered by project_id
                    year_counts = {}
                    if hasattr(self.db, "list_years_with_counts"):
                        year_list = self.db.list_years_with_counts(self.project_id) or []
                        year_counts = {str(y): c for y, c in year_list}
                    # Build result with hierarchy and counts
                    rows = {"hierarchy": hier, "year_counts": year_counts}
                else:
                    self._dbg("_load_dates → No date hierarchy method available")
                self._dbg(f"_load_dates → got hierarchy data for project_id={self.project_id}")
            except Exception:
                traceback.print_exc()
                rows = {}
            self._finishDatesSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- DATES ----------
    def _finish_dates(self, idx:int, rows:list|dict, started:float, gen:int):
        if gen is not None and self._is_stale("dates", gen):
            self._dbg(f"_finish_dates (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel(f"<b>{tr('sidebar.by_date')}</b>"))

        # Extract hierarchy and counts from result
        if isinstance(rows, dict):
            hier = rows.get("hierarchy", {})
            year_counts = rows.get("year_counts", {})
        else:
            hier = {}
            year_counts = {}

        if not hier:
            self._set_tab_empty(idx, "No date index found")
        else:
            # Create tree widget: Years → Months → Days
            tree = QTreeWidget()
            tree.setHeaderLabels([tr('sidebar.header_year_month_day'), tr('sidebar.header_photos')])
            tree.setColumnCount(2)
            tree.setSelectionMode(QTreeWidget.SingleSelection)
            tree.setEditTriggers(QTreeWidget.NoEditTriggers)
            tree.setAlternatingRowColors(True)
            tree.header().setStretchLastSection(False)
            tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
            tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            # Populate tree: Years (top level)
            for year in sorted(hier.keys(), reverse=True):
                # Get accurate year count from database
                year_count = 0
                try:
                    if hasattr(self.db, "count_for_year"):
                        year_count = self.db.count_for_year(year)
                    else:
                        year_count = year_counts.get(str(year), 0)
                except Exception:
                    year_count = year_counts.get(str(year), 0)

                year_item = QTreeWidgetItem([str(year), str(year_count)])
                year_item.setData(0, Qt.UserRole, str(year))
                tree.addTopLevelItem(year_item)

                # Months (children of year)
                months_dict = hier[year]
                month_names = ["", tr('sidebar.month_jan'), tr('sidebar.month_feb'), tr('sidebar.month_mar'), 
                               tr('sidebar.month_apr'), tr('sidebar.month_may'), tr('sidebar.month_jun'),
                               tr('sidebar.month_jul'), tr('sidebar.month_aug'), tr('sidebar.month_sep'), 
                               tr('sidebar.month_oct'), tr('sidebar.month_nov'), tr('sidebar.month_dec')]

                for month in sorted(months_dict.keys(), reverse=True):
                    days_list = months_dict[month]
                    month_num = int(month) if month.isdigit() else 0
                    month_label = month_names[month_num] if 0 < month_num <= 12 else month

                    # Get accurate month count from database (not just len(days_list))
                    month_count = 0
                    try:
                        if hasattr(self.db, "count_for_month"):
                            month_count = self.db.count_for_month(year, month)
                        else:
                            month_count = len(days_list)
                    except Exception:
                        month_count = len(days_list)

                    month_item = QTreeWidgetItem([f"{month_label} {year}", str(month_count)])
                    month_item.setData(0, Qt.UserRole, f"{year}-{month}")
                    year_item.addChild(month_item)

                    # Days (children of month) - WITH COUNTS
                    for day in sorted(days_list, reverse=True):
                        # Get day count from database
                        day_count = 0
                        try:
                            if hasattr(self.db, "count_for_day"):
                                day_count = self.db.count_for_day(day, project_id=self.project_id)
                            else:
                                # Fallback: count from get_images_by_date
                                day_paths = self.db.get_images_by_date(day) if hasattr(self.db, "get_images_by_date") else []
                                day_count = len(day_paths) if day_paths else 0
                        except Exception:
                            day_count = 0

                        day_item = QTreeWidgetItem([str(day), str(day_count) if day_count > 0 else ""])
                        day_item.setData(0, Qt.UserRole, str(day))
                        month_item.addChild(day_item)

            # Connect double-click to emit date selection
            tree.itemDoubleClicked.connect(lambda item, col: self.selectDate.emit(item.data(0, Qt.UserRole)))
            tab.layout().addWidget(self._wrap_in_scroll_area(tree), 1)

        self._tab_populated.add("dates")
        self._tab_loading.discard("dates")
        st = self._tab_status_labels.get(idx)
        if st:
            year_count = len(hier.keys()) if hier else 0
            st.setText(f"{year_count} year(s) • {time.time()-started:.2f}s")

    # ---------- tags ----------
    def _load_tags(self, idx:int, gen:int):
        """
        Load tags using TagService (service layer).

        ARCHITECTURE: UI Layer → TagService → TagRepository → Database
        """
        started = time.time()
        project_id = self.project_id  # Capture project_id before thread starts
        def work():
            rows = []
            try:
                # Use TagService for proper layered architecture
                tag_service = get_tag_service()
                rows = tag_service.get_all_tags_with_counts(project_id) or []  # list of (tag_name, count) tuples
                self._dbg(f"_load_tags → got {len(rows)} rows for project_id={project_id}")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishTagsSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- TAGS ----------
    def _finish_tags(self, idx:int, rows:list, started:float, gen:int):
        self._dbg(f"_finish_tags called: idx={idx}, gen={gen}, rows_count={len(rows) if rows else 0}")
        if self._is_stale("tags", gen):
            self._dbg(f"_finish_tags (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        if not tab:
            self._dbg(f"_finish_tags - tab is None at idx={idx}, aborting")
            return
        layout = tab.layout()
        if not layout:
            self._dbg(f"_finish_tags - layout is None at idx={idx}, aborting")
            return
        layout.addWidget(QLabel(f"<b>{tr('sidebar.tags')}</b>"))

        # Process rows which can be: tuples (tag, count), dicts, or strings
        tag_items = []  # list of (tag_name, count)
        for r in (rows or []):
            if isinstance(r, tuple) and len(r) == 2:
                # Format: (tag_name, count) from get_all_tags_with_counts()
                tag_name, count = r
                tag_items.append((tag_name, count))
            elif isinstance(r, dict):
                # Format: dict with 'tag'/'name'/'label' key
                tag_name = r.get("tag") or r.get("name") or r.get("label")
                count = r.get("count", 0)
                if tag_name:
                    tag_items.append((tag_name, count))
            else:
                # Format: plain string
                tag_name = str(r)
                if tag_name:
                    tag_items.append((tag_name, 0))

        if not tag_items:
            self._set_tab_empty(idx, "No tags found")
        else:
            # Create 2-column table: Tag | Photos
            table = QTableWidget()
            table.setColumnCount(2)
            table.setHorizontalHeaderLabels([tr('sidebar.tag'), tr('sidebar.header_photos')])
            table.setRowCount(len(tag_items))
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            for row, (tag_name, count) in enumerate(tag_items):
                # Column 0: Tag name (no emoji prefix to match List view)
                item_name = QTableWidgetItem(tag_name)
                item_name.setData(Qt.UserRole, tag_name)
                table.setItem(row, 0, item_name)

                # Column 1: Count badge (right-aligned, badge style)
                count_str = str(count) if count else ""
                badge = QLabel(count_str)
                badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                badge.setStyleSheet("QLabel { background-color: #E8F4FD; color: #245; border: 1px solid #B3D9F2; border-radius: 10px; padding: 2px 6px; min-width: 24px; }")
                table.setCellWidget(row, 1, badge)

            table.cellDoubleClicked.connect(lambda row, col: self.selectTag.emit(table.item(row, 0).data(Qt.UserRole)))
            if tab.layout():
                tab.layout().addWidget(self._wrap_in_scroll_area(table), 1)
            else:
                self._dbg(f"_finish_tags - layout is None when adding table, aborting")

        self._tab_populated.add("tags")
        self._tab_loading.discard("tags")
        st = self._tab_status_labels.get(idx)
        if st: st.setText(f"{len(tag_items)} item(s) • {time.time()-started:.2f}s")
    # ---------- quick ----------
    def _load_quick(self, idx:int, gen:int):
        started = time.time()
        def work():
            rows = []
            try:
                if hasattr(self.db, "get_quick_date_counts"):
                    rows = self.db.get_quick_date_counts() or []
                else:
                    # Fallback: simple list without counts
                    rows = [
                        {"key": "today", "label": "Today", "count": 0},
                        {"key": "this-week", "label": "This Week", "count": 0},
                        {"key": "this-month", "label": "This Month", "count": 0}
                    ]
                self._dbg(f"_load_quick → got {len(rows)} rows")
            except Exception:
                traceback.print_exc()
                rows = []
            # Emit using same signature as other tabs
            self._finishQuickSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- QUICK ----------
    def _finish_quick(self, idx:int, rows:list, started:float|None=None, gen:int|None=None):
        if gen is not None and self._is_stale("quick", gen):
            self._dbg(f"_finish_quick (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel(f"<b>{tr('sidebar.quick_dates')}</b>"))

        # Normalize rows to (key, label, count)
        quick_items = []
        for r in (rows or []):
            if isinstance(r, dict):
                key = r.get("key", "")
                label = r.get("label", "")
                count = r.get("count", 0)
                # Strip "date:" prefix from key if present
                if key.startswith("date:"):
                    key = key[5:]
                quick_items.append((key, label, count))
            elif isinstance(r, (tuple, list)) and len(r) >= 2:
                key, label = r[0], r[1]
                count = r[2] if len(r) >= 3 else 0
                quick_items.append((key, label, count))

        if not quick_items:
            self._set_tab_empty(idx, "No quick dates")
        else:
            # Create 2-column table: Period | Photos
            table = QTableWidget()
            table.setColumnCount(2)
            table.setHorizontalHeaderLabels(["Period", "Photos"])
            table.setRowCount(len(quick_items))
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            for row, (key, label, count) in enumerate(quick_items):
                # Column 0: Period label
                item_name = QTableWidgetItem(label)
                item_name.setData(Qt.UserRole, key)
                table.setItem(row, 0, item_name)

                # Column 1: Count badge (right-aligned, light badge)
                badge = QLabel(str(count))
                badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                badge.setStyleSheet("QLabel { background-color: #F0F6FF; color: #456; border: 1px solid #C7DAF7; border-radius: 10px; padding: 2px 6px; min-width: 24px; }")
                table.setCellWidget(row, 1, badge)

            table.cellDoubleClicked.connect(lambda row, col: self.selectDate.emit(table.item(row, 0).data(Qt.UserRole)))
            tab.layout().addWidget(self._wrap_in_scroll_area(table), 1)

        self._tab_populated.add("quick")
        self._tab_loading.discard("quick")

    # ---------- people ----------
    def _load_people(self, idx: int, gen: int):
        started = time.time()
        def work():
            try:
                rows = []
                if self.project_id and hasattr(self.db, "get_face_clusters"):
                    rows = self.db.get_face_clusters(self.project_id) or []
                self._dbg(f"_load_people → got {len(rows)} clusters")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishPeopleSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- PEOPLE ----------
    def _finish_people(self, idx: int, rows: list, started: float, gen: int):
        if self._is_stale("people", gen):
            self._dbg(f"_finish_people (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)

        # CRITICAL FIX: Clear people_list_view reference before deleting old widget
        # This prevents accessing stale widget references during deletion
        if hasattr(self, 'people_list_view'):
            self.people_list_view = None

        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        layout = tab.layout()

        # === Header row with label + 🔍 Detect Faces + 🔁 Re-Cluster ===
        header = QWidget()
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(8)

        lbl = QLabel("<b>👥 People / Face Clusters</b>")

        # Phase 8: Detect & Group Faces button (automatic pipeline)
        btn_detect = QPushButton("⚡ Detect & Group")
        btn_detect.setFixedHeight(24)
        btn_detect.setToolTip("Automatically detect faces and group them into person albums (one-click)")
        btn_detect.setStyleSheet("QPushButton{padding:3px 8px;}")

        btn_recluster = QPushButton("🔁 Re-Cluster")
        btn_recluster.setFixedHeight(24)
        btn_recluster.setToolTip("Run face clustering again in background")
        btn_recluster.setStyleSheet("QPushButton{padding:3px 8px;}")

        hbox.addWidget(lbl)
        hbox.addStretch(1)
        hbox.addWidget(btn_detect)
        hbox.addWidget(btn_recluster)
        layout.addWidget(header)

        # === Phase 8: Automatic Face Grouping Pipeline ===
        # Replaces manual two-step process with automatic: detect → cluster → refresh
        def on_detect_and_group_faces():
            """
            Launch automatic face grouping pipeline.

            Pipeline: Detection → Clustering → UI Refresh
            - Detection: Scans photos, detects faces, generates embeddings
            - Clustering: Groups similar faces using DBSCAN
            - Refresh: Auto-updates People tab with results

            User sees: Single button click → Automatic results ✅
            (vs old flow: Click Detect → Wait → Click Re-Cluster → Wait → Manual refresh)
            """
            try:
                from PySide6.QtCore import QThreadPool
                from PySide6.QtWidgets import QMessageBox, QProgressBar, QVBoxLayout, QDialog, QLabel, QPushButton
                from workers.face_detection_worker import FaceDetectionWorker
                from workers.face_cluster_worker import FaceClusterWorker

                # Confirm action
                reply = QMessageBox.question(
                    self,
                    "Detect & Group Faces",
                    f"This will automatically:\n"
                    f"1. Detect faces in all photos\n"
                    f"2. Group similar faces into person albums\n"
                    f"3. Show results in the People tab\n\n"
                    f"This may take 10-20 minutes for large photo collections.\n\n"
                    f"Continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply != QMessageBox.Yes:
                    return

                print(f"[People] Launching automatic face grouping pipeline for project {self.project_id}")

                # Create progress dialog
                progress_dialog = QDialog(self)
                progress_dialog.setWindowTitle("Grouping Faces")
                progress_dialog.setModal(True)
                progress_dialog.setMinimumWidth(400)

                layout = QVBoxLayout()
                status_label = QLabel("Starting face detection...")
                progress_bar = QProgressBar()
                progress_bar.setRange(0, 100)
                progress_bar.setValue(0)

                cancel_btn = QPushButton("Cancel")
                cancel_btn.setStyleSheet("QPushButton{padding:5px 15px;}")

                layout.addWidget(status_label)
                layout.addWidget(progress_bar)
                layout.addWidget(cancel_btn)
                progress_dialog.setLayout(layout)

                # Worker references (for cancellation)
                current_detection_worker = None
                current_cluster_worker = None

                def cancel_pipeline():
                    """Cancel the entire pipeline."""
                    if current_detection_worker:
                        current_detection_worker.cancel()
                    if current_cluster_worker:
                        current_cluster_worker.cancel()
                    progress_dialog.close()
                    print("[People] Pipeline cancelled by user")

                cancel_btn.clicked.connect(cancel_pipeline)

                # Step 1: Start detection worker
                detection_worker = FaceDetectionWorker(project_id=self.project_id)
                current_detection_worker = detection_worker

                def on_detection_progress(current, total, message):
                    """Update progress during detection (0-50%)."""
                    pct = int((current / total) * 50) if total > 0 else 0
                    progress_bar.setValue(pct)
                    status_label.setText(f"[1/2] {message}")
                    print(f"[FaceDetection] [{current}/{total}] {message}")

                def on_detection_finished(success, failed, total_faces):
                    """Detection complete → Auto-start clustering."""
                    print(f"[FaceDetection] Complete: {success} photos, {total_faces} faces detected")

                    if total_faces == 0:
                        progress_dialog.close()
                        QMessageBox.information(
                            self,
                            "No Faces Found",
                            f"No faces detected in {success} photos.\n\n"
                            f"Try photos with clear, front-facing faces for best results."
                        )
                        return

                    # Step 2: Auto-start clustering worker
                    nonlocal current_cluster_worker
                    cluster_worker = FaceClusterWorker(project_id=self.project_id)
                    current_cluster_worker = cluster_worker

                    def on_cluster_progress(current, total, message):
                        """Update progress during clustering (50-100%)."""
                        pct = int(50 + (current / total) * 50) if total > 0 else 50
                        progress_bar.setValue(pct)
                        status_label.setText(f"[2/2] {message}")
                        print(f"[FaceCluster] {message}")

                    def on_cluster_finished(cluster_count, total_clustered):
                        """Clustering complete → Auto-refresh UI."""
                        progress_dialog.close()
                        print(f"[FaceCluster] Complete: {cluster_count} person groups created")

                        # Refresh the people tab
                        if hasattr(self.parent(), "refresh_sidebar"):
                            self.parent().refresh_sidebar()

                        # Show success notification
                        QMessageBox.information(
                            self,
                            "Face Grouping Complete",
                            f"✅ Found {cluster_count} people in your photos!\n\n"
                            f"Grouped {total_clustered} faces from {success} photos.\n\n"
                            f"View results in the People tab below."
                        )

                    def on_cluster_error(error_msg):
                        """Handle clustering errors."""
                        progress_dialog.close()
                        QMessageBox.warning(
                            self,
                            "Clustering Failed",
                            f"Face detection succeeded ({total_faces} faces found),\n"
                            f"but clustering failed:\n\n{error_msg}\n\n"
                            f"Try clicking 🔁 Re-Cluster to retry."
                        )

                    gen0 = int(getattr(self.window(), "_ui_generation", 0))
                    connect_guarded(cluster_worker.signals.progress, self, on_cluster_progress, generation=gen0)
                    connect_guarded(cluster_worker.signals.finished, self, on_cluster_finished, generation=gen0)
                    connect_guarded(cluster_worker.signals.error, self, on_cluster_error, generation=gen0)

                    QThreadPool.globalInstance().start(cluster_worker)

                gen0 = int(getattr(self.window(), "_ui_generation", 0))
                connect_guarded(detection_worker.signals.progress, self, on_detection_progress, generation=gen0)
                connect_guarded(detection_worker.signals.finished, self, on_detection_finished, generation=gen0)

                # Start detection worker
                QThreadPool.globalInstance().start(detection_worker)

                # Show progress dialog
                progress_dialog.show()

                # CRITICAL FIX: Explicitly center face detection progress dialog on main window
                # This ensures the dialog appears in the center of the application geometry
                try:
                    # Ensure dialog geometry is calculated
                    progress_dialog.adjustSize()
                    from PySide6.QtWidgets import QApplication
                    QApplication.processEvents()

                    # Get geometries
                    parent_rect = self.window().geometry()
                    dialog_rect = progress_dialog.geometry()

                    # Calculate center position
                    center_x = parent_rect.x() + (parent_rect.width() - dialog_rect.width()) // 2
                    center_y = parent_rect.y() + (parent_rect.height() - dialog_rect.height()) // 2

                    # Move dialog to center
                    progress_dialog.move(center_x, center_y)
                    print(f"[Sidebar] Face detection progress dialog centered at ({center_x}, {center_y})")
                except Exception as e:
                    print(f"[Sidebar] Could not center face detection progress dialog: {e}")

            except ImportError as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    self,
                    "Missing Library",
                    f"InsightFace library not installed.\n\n"
                    f"Install with:\npip install insightface onnxruntime\n\n"
                    f"Error: {e}"
                )
            except Exception as e:
                import traceback
                traceback.print_exc()
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Face Grouping Failed", str(e))

        btn_detect.clicked.connect(on_detect_and_group_faces)

        # === Launch clustering worker (manual mode) ===
        def on_recluster():
            """
            Manually re-run clustering on existing face detections.

            Use case: User wants to re-group faces without re-detecting
            (e.g., after adjusting clustering parameters, or if auto-clustering failed)
            """
            try:
                from PySide6.QtCore import QThreadPool
                from PySide6.QtWidgets import QMessageBox, QProgressDialog
                from workers.face_cluster_worker import FaceClusterWorker

                # Check if faces exist
                with self.db._connect() as conn:
                    cur = conn.execute("SELECT COUNT(*) FROM face_crops WHERE project_id = ?", (self.project_id,))
                    face_count = cur.fetchone()[0]

                if face_count == 0:
                    QMessageBox.warning(
                        self,
                        "No Faces Detected",
                        "No faces have been detected yet.\n\n"
                        "Click 🔍 Detect Faces first to scan your photos."
                    )
                    return

                print(f"[People] Launching clustering worker for {face_count} detected faces")

                # Create progress dialog
                progress = QProgressDialog("Grouping faces...", "Cancel", 0, 100, self)
                progress.setWindowTitle("Re-Clustering Faces")
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                progress.setValue(0)

                # Create worker
                worker = FaceClusterWorker(project_id=self.project_id)

                def on_progress(current, total, message):
                    progress.setLabelText(message)
                    progress.setValue(current)
                    print(f"[FaceCluster] {message}")

                def on_finished(cluster_count, total_faces):
                    progress.close()
                    print(f"[FaceCluster] Complete: {cluster_count} person groups created")

                    # Refresh sidebar
                    if hasattr(self.parent(), "refresh_sidebar"):
                        self.parent().refresh_sidebar()

                    QMessageBox.information(
                        self,
                        "Clustering Complete",
                        f"✅ Grouped {total_faces} faces into {cluster_count} person albums.\n\n"
                        f"View results in the People tab below."
                    )

                def on_error(error_msg):
                    progress.close()
                    QMessageBox.critical(
                        self,
                        "Clustering Failed",
                        f"Failed to cluster faces:\n\n{error_msg}"
                    )

                def on_cancel():
                    worker.cancel()

                gen2 = int(getattr(self.window(), "_ui_generation", 0))
                connect_guarded(worker.signals.progress, self, on_progress, generation=gen2)
                connect_guarded(worker.signals.finished, self, on_finished, generation=gen2)
                connect_guarded(worker.signals.error, self, on_error, generation=gen2)
                progress.canceled.connect(on_cancel)

                # Start worker
                QThreadPool.globalInstance().start(worker)

            except Exception as e:
                import traceback
                traceback.print_exc()
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Re-Cluster Failed", str(e))

        btn_recluster.clicked.connect(on_recluster)

        # === Populate cluster list ===
        if not rows:
            # Check if faces were detected but not clustered
            try:
                with self.db._connect() as conn:
                    cur = conn.execute("""
                        SELECT COUNT(*) FROM face_crops WHERE project_id = ?
                    """, (self.project_id,))
                    face_count = cur.fetchone()[0]
            except Exception as e:
                print(f"[People] Failed to count faces: {e}")
                face_count = 0

            if face_count > 0:
                # Faces detected but not clustered
                msg = QLabel(
                    f"<div style='padding:20px;text-align:center;'>"
                    f"<p style='font-size:14px;color:#FF8800;'>⚠️ <b>{face_count} faces detected</b></p>"
                    f"<p style='color:#666;'>Click <b>🔁 Re-Cluster</b> to group similar faces together.</p>"
                    f"<p style='color:#999;font-size:12px;'>Creates person albums based on facial similarity.</p>"
                    f"</div>"
                )
                msg.setWordWrap(True)
                layout.addWidget(msg, 1)
                print(f"[People] {face_count} faces detected, awaiting clustering")
            else:
                # No faces detected yet
                msg = QLabel(
                    f"<div style='padding:20px;text-align:center;'>"
                    f"<p style='font-size:14px;color:#888;'>ℹ️ <b>No faces detected yet</b></p>"
                    f"<p style='color:#666;'>Click <b>⚡ Detect & Group</b> to find people in your photos.</p>"
                    f"<p style='color:#999;font-size:12px;'>Automatically detects faces and groups them by person.</p>"
                    f"</div>"
                )
                msg.setWordWrap(True)
                layout.addWidget(msg, 1)
                print("[People] No faces detected yet")

            self._tab_populated.add("people")
            self._tab_loading.discard("people")
            st = self._tab_status_labels.get(idx)
            if st:
                if face_count > 0:
                    st.setText(f"{face_count} faces detected • Click Re-Cluster")
                else:
                    st.setText("No faces detected")
            return

        # ========== NEW: Use dedicated PeopleListView widget ==========
        people_view = PeopleListView(self)
        people_view.set_database(self.db, self.project_id)
        people_view.load_people(rows)

        # CRITICAL FIX: Store reference for cross-view rename sync
        # This allows list view to update tabs view after rename
        self.people_list_view = people_view

        # Wire up signals
        def on_person_activated(branch_data):
            """Handle person activation with status bar update"""
            # Emit signal to main window grid
            self.selectBranch.emit(branch_data)

            # Update status bar like the list mode sidebar does
            try:
                mw = self.window()
                if hasattr(mw, 'statusBar'):
                    branch_key = branch_data.split(":", 1)[1] if ":" in branch_data else branch_data
                    paths = self.db.get_paths_for_cluster(self.project_id, branch_key) if self.project_id else []
                    # Get person name from current rows
                    person_name = next(
                        (row.get("display_name") or row.get("branch_key") for row in rows if row["branch_key"] == branch_key),
                        branch_key
                    )
                    # Humanize if needed
                    if person_name.startswith("face_"):
                        try:
                            cluster_num = int(person_name.split("_")[1])
                            person_name = f"Unnamed #{cluster_num}"
                        except (ValueError, IndexError):
                            pass
                    mw.statusBar().showMessage(f"👤 Showing {len(paths)} photo(s) of {person_name}")
            except Exception as e:
                print(f"[PeopleListView] Failed to update status bar: {e}")

        people_view.personActivated.connect(on_person_activated)
        people_view.personExportRequested.connect(lambda branch_key: self._do_export(branch_key) if hasattr(self, '_do_export') else None)

        layout.addWidget(people_view, 1)

        self._tab_populated.add("people")
        self._tab_loading.discard("people")
        st = self._tab_status_labels.get(idx)
        if st:
            # Get counts from PeopleListView widget
            total_faces = people_view.get_total_faces()
            people_count = people_view.get_people_count()
            st.setText(f"{people_count} people • {total_faces} faces • {time.time()-started:.2f}s")

# =====================================================================
# 2️ SidebarQt — main sidebar container with toggle
# =====================================================================

class SidebarQt(QWidget):
    folderSelected = Signal(int)
    # Signal for thread-safe counts update from worker thread
    _countsReady = Signal(list, int)  # (results, generation)

    def __init__(self, project_id=None):
        super().__init__()
        self._disposed = False  # Lifecycle flag: True after cleanup()
        self.db = ReferenceDB()
        self.project_id = project_id

        # settings
        self.settings = SettingsManager() if SettingsManager else None

        # UI state
        self._reload_block = False
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._do_reload_throttled)

        # Worker generation for list mode (to cancel stale workers)
        self._list_worker_gen = 0

        # Refresh guard to prevent concurrent reloads
        self._refreshing = False

        # Initialization flag to prevent processEvents() during startup
        self._initialized = False

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(60)
        self._spin_timer.timeout.connect(self._tick_spinner)
        self._spin_angle = 0
        self._base_pm = self._make_reload_pixmap(18, 18)

        # OPTIMIZATION: Device auto-detection system
        # Uses Windows WM_DEVICECHANGE events (instant) + fallback timer (30s polling)
        self._device_refresh_timer = QTimer(self)
        self._device_refresh_timer.setInterval(30000)  # 30 seconds
        self._device_refresh_timer.timeout.connect(self._check_device_changes)
        self._last_device_count = 0
        # FIX #3: Default must match settings_manager (False) — never run
        # COM enumeration unless the user explicitly enabled it.
        self._device_auto_refresh_enabled = self.settings.get("device_auto_refresh", False) if self.settings else False
        self._device_monitor = None  # Will be initialized if auto-refresh enabled

        if self._device_auto_refresh_enabled:
            print(f"[Sidebar] Device auto-detection enabled")

            # Try to use native device monitor (Windows only)
            try:
                import platform
                if platform.system() == "Windows":
                    self._device_monitor = get_device_monitor()
                    self._device_monitor.deviceChanged.connect(self._on_device_event)
                    self._device_monitor.start()
                    print(f"[Sidebar] Native Windows device monitoring active (instant detection)")
                else:
                    print(f"[Sidebar] Native monitoring not available, using timer polling")
            except Exception as e:
                print(f"[Sidebar] Failed to initialize device monitor: {e}")
                print(f"[Sidebar] Falling back to timer-based polling")

            # Always start timer as fallback (even with monitor, provides redundancy)
            self._device_refresh_timer.start()
            print(f"[Sidebar] Timer fallback active (30s polling)")
        else:
            print(f"[Sidebar] Device auto-detection disabled (manual refresh only)")


        # Header with prominent action buttons (Google Photos / Apple Photos inspired)
        header_bar = QWidget()
        header_layout = QVBoxLayout(header_bar)
        header_layout.setContentsMargins(4, 4, 4, 8)
        header_layout.setSpacing(6)

        # Top row: Title + Mode toggle + Collapse
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)

        title_lbl = QLabel("📁 Library")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 11pt; padding-left: 4px;")
        top_layout.addWidget(title_lbl)
        top_layout.addStretch(1)

        # Mode toggle (cycle through: List → Tabs → Accordion → List ...)
        self.btn_mode_toggle = QPushButton("")
        self.btn_mode_toggle.setCheckable(False)  # Not checkable, we cycle through 3 modes
        current_mode = self.settings.get("sidebar_mode", "list") if self.settings else "list"
        self._update_mode_toggle_text()
        self.btn_mode_toggle.setToolTip("Cycle Sidebar Mode: List → Tabs → Accordion")
        self.btn_mode_toggle.clicked.connect(self._on_mode_toggled)
        self.btn_mode_toggle.setStyleSheet("""
            QPushButton {
                background: #E8E8E8;
                border: 1px solid #C0C0C0;
                border-radius: 3px;
                padding: 3px 8px;
                font-size: 9pt;
            }
            QPushButton:hover { background: #D0D0D0; }
            QPushButton:pressed { background: #B8B8B8; }
        """)
        top_layout.addWidget(self.btn_mode_toggle)

        # collapse/expand
        self.btn_collapse = QPushButton("⇵")
        self.btn_collapse.setFixedSize(28, 24)
        self.btn_collapse.setToolTip("Collapse/Expand main sections")
        self.btn_collapse.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
            }
            QPushButton:hover { background: #E0E0E0; border: 1px solid #C0C0C0; }
        """)
        top_layout.addWidget(self.btn_collapse)
        self.btn_collapse.clicked.connect(self._on_collapse_clicked)

        header_layout.addWidget(top_row)

        # Prominent Scan Repository button (Google Photos inspired)
        self.btn_scan_repo = QPushButton("📸 Scan Repository")
        self.btn_scan_repo.setToolTip("Scan a folder to add photos to your library")
        self.btn_scan_repo.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                            stop:0 #4A90E2, stop:1 #357ABD);
                color: white;
                border: 1px solid #2E6BA6;
                border-radius: 5px;
                padding: 8px 12px;
                font-weight: bold;
                font-size: 10pt;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                            stop:0 #5AA0F2, stop:1 #4A90E2);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                            stop:0 #357ABD, stop:1 #2E6BA6);
            }
        """)
        self.btn_scan_repo.clicked.connect(self._on_scan_repo_clicked)
        header_layout.addWidget(self.btn_scan_repo)

        # Secondary action buttons row
        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        actions_layout.addStretch()
        header_layout.addWidget(actions_row)

        # Tree (list mode) - Phase 3: Use DroppableTreeView for drag & drop support
        self.tree = DroppableTreeView(self)
        self.tree.setAlternatingRowColors(True)
        self.tree.setEditTriggers(QTreeView.NoEditTriggers)
        self.tree.setSelectionBehavior(QTreeView.SelectRows)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setIconSize(QSize(32, 32))  # Circular face thumbnails
        self.model = QStandardItemModel(self.tree)
        self.model.setHorizontalHeaderLabels(["Folder / Branch", "Photos"])
        self.tree.setModel(self.model)
        header = self.tree.header()
        header.setStretchLastSection(False)  # Don't stretch last column
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # Name column stretches
        header.setSectionResizeMode(1, QHeaderView.Fixed)  # Count column fixed width
        header.resizeSection(1, 70)  # Set count column to 70px
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # Professional styling for tree rows
        self.tree.setStyleSheet("""
            QTreeView {
                background-color: white;
                border: 1px solid #E0E0E0;
                border-radius: 4px;
                font-size: 10pt;
            }
            QTreeView::item {
                padding: 4px 2px;
                border-bottom: 1px solid #F5F5F5;
            }
            QTreeView::item:hover {
                background-color: #F0F8FF;
            }
            QTreeView::item:selected {
                background-color: #E3F2FD;
                color: #1976D2;
            }
            QHeaderView::section {
                background-color: #FAFAFA;
                border: none;
                border-bottom: 2px solid #E0E0E0;
                padding: 6px 8px;
                font-weight: bold;
                font-size: 9pt;
                color: #666;
            }
        """)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        
        # Allow selecting multiple face clusters for batch merge
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Phase 3: Connect drag & drop signals
        self.tree.photoDropped.connect(self._on_photos_dropped_to_folder)
        self.tree.tagDropped.connect(self._on_photos_dropped_to_tag)

        # ========== IMPROVEMENT: Add search/filter box for tree view ==========
        search_container = QWidget()
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(4, 2, 4, 2)
        search_layout.setSpacing(4)

        search_label = QLabel("🔍")
        self.tree_search_box = QLineEdit()
        self.tree_search_box.setPlaceholderText(tr('search.placeholder_filter_sidebar'))
        self.tree_search_box.setClearButtonEnabled(True)
        self.tree_search_box.textChanged.connect(self._on_tree_search_changed)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.tree_search_box, 1)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        layout.addWidget(header_bar)
        layout.addWidget(search_container)  # Search box
        layout.addWidget(self.tree, 1)
        
        # FIX #2: Lazy-init sidebar modes — only build the active mode.
        # Controllers start as None and are created on first use via
        # _ensure_tabs_controller() / _ensure_accordion_controller().
        self._sidebar_layout = layout          # keep ref for lazy addWidget
        self.tabs_controller = None
        self.accordion_controller = None
        
        # Connect counts update signal from worker thread to UI handler
        self._countsReady.connect(self._apply_counts_defensive, Qt.QueuedConnection)        
        
        
        # Click handlers
        self.tree.clicked.connect(self._on_item_clicked)
        self.tree.doubleClicked.connect(self._on_item_double_clicked)

        # Build once via switch_display_mode (which calls _build_tree_model internally).
        # Avoids the previous double-build: __init__ built once, then switch_display_mode rebuilt.
        try:
            mode = current_mode.lower()
            if mode in ("tabs", "accordion"):
                self.switch_display_mode(mode)
            else:
                self.switch_display_mode("list")
        except Exception:
            self.switch_display_mode("list")

        # Apply fold state if persisted
        try:
            folded = bool(self.settings.get("sidebar_folded", False)) if self.settings else False
            if folded:
                self.collapse_all()
        except Exception:
            pass

        # Mark initialization as complete - safe to call processEvents() now
        self._initialized = True

    def closeEvent(self, event):
        """
        Clean up timers and resources when sidebar is closed.
        Prevents crashes from timers firing after widget deletion.
        """
        # Stop auto-refresh timer to prevent it from interfering with cleanup
        if hasattr(self, '_device_refresh_timer'):
            self._device_refresh_timer.stop()

        # OPTIMIZATION: Stop device monitor if active
        if hasattr(self, '_device_monitor') and self._device_monitor:
            try:
                self._device_monitor.stop()
                print(f"[Sidebar] Device monitor stopped during cleanup")
            except Exception as e:
                print(f"[Sidebar] Warning: Error stopping device monitor: {e}")

        # Stop other timers
        if hasattr(self, '_reload_timer'):
            self._reload_timer.stop()

        if hasattr(self, '_spin_timer'):
            self._spin_timer.stop()

        event.accept()

    # ---- lazy sidebar mode factories (FIX #2) ----

    def _ensure_tabs_controller(self):
        """Create SidebarTabs on first use."""
        if self.tabs_controller is not None:
            return self.tabs_controller
        self.tabs_controller = SidebarTabs(project_id=self.project_id, parent=self)
        self.tabs_controller.hide()
        self._sidebar_layout.addWidget(self.tabs_controller, 1)
        self.tabs_controller.selectBranch.connect(lambda key: self._set_grid_context("branch", key))
        self.tabs_controller.selectFolder.connect(lambda folder_id: self._set_grid_context("folder", folder_id))
        self.tabs_controller.selectDate.connect(lambda key: self._set_grid_context("date", key))
        self.tabs_controller.selectTag.connect(
            lambda name: self.window()._apply_tag_filter(name) if hasattr(self.window(), "_apply_tag_filter") else None
        )
        print("[SidebarQt] Lazy-created SidebarTabs controller")
        return self.tabs_controller

    def _ensure_accordion_controller(self):
        """Create AccordionSidebar on first use."""
        if self.accordion_controller is not None:
            return self.accordion_controller
        from accordion_sidebar import AccordionSidebar
        self.accordion_controller = AccordionSidebar(project_id=self.project_id, parent=self)
        self.accordion_controller.hide()
        self._sidebar_layout.addWidget(self.accordion_controller, 1)
        self.accordion_controller.selectBranch.connect(lambda key: self._set_grid_context("branch", key))
        self.accordion_controller.selectFolder.connect(lambda folder_id: self._set_grid_context("folder", folder_id))
        self.accordion_controller.selectDate.connect(lambda key: self._set_grid_context("date", key))
        self.accordion_controller.selectTag.connect(
            lambda name: self.window()._apply_tag_filter(name) if hasattr(self.window(), "_apply_tag_filter") else None
        )
        print("[SidebarQt] Lazy-created AccordionSidebar controller")
        return self.accordion_controller

    # ---- header helpers ----


    def _find_model_item_by_key(self, key, role=Qt.UserRole+1):
        """Return (QStandardItem for column0, QStandardItem for column1) where column0.data(role)==key, or (None,None)."""
        def recurse(parent):
            for r in range(parent.rowCount()):
                n0 = parent.child(r, 0)
                n1 = parent.child(r, 1)
                if n0 and n0.data(role) == key:
                    return n0, (n1 if n1 else None)
                # search recursively
                res = recurse(n0)
                if res != (None, None):
                    return res
            return (None, None)
        # top-level roots
        for top in range(self.model.rowCount()):
            root = self.model.item(top, 0)
            # check children of root
            res = recurse(root)
            if res != (None, None):
                return res
        return (None, None)

    def _update_mode_toggle_text(self):
        # Cycle through 3 modes: List → Tabs → Accordion → List ...
        current_mode = self.settings.get("sidebar_mode", "list") if self.settings else "list"
        mode_labels = {"list": "List", "tabs": "Tabs", "accordion": "Accordion"}
        self.btn_mode_toggle.setText(mode_labels.get(current_mode, "List"))

    def _on_mode_toggled(self, checked):
        # Cycle through 3 modes instead of toggle between 2
        current_mode = self.settings.get("sidebar_mode", "list") if self.settings else "list"

        # Cycle: list → tabs → accordion → list ...
        mode_cycle = {"list": "tabs", "tabs": "accordion", "accordion": "list"}
        next_mode = mode_cycle.get(current_mode, "list")

        self._update_mode_toggle_text()
        try:
            if self.settings:
                self.settings.set("sidebar_mode", next_mode)
        except Exception:
            pass
        self.switch_display_mode(next_mode)

        # Update button text after mode switch
        self._update_mode_toggle_text()

    def _on_refresh_clicked(self):
        self._start_spinner()
        self.reload()
        QTimer.singleShot(150, self._stop_spinner)

    def _on_scan_repo_clicked(self):
        """Handle Scan Repository button click."""
        mw = self.window()
        if hasattr(mw, '_on_scan_repository'):
            mw._on_scan_repository()
        else:
            print("[Sidebar] No scan handler found in main window")

    def _on_detect_faces_clicked(self):
        """Handle Detect & Group Faces button click."""
        mw = self.window()
        if hasattr(mw, '_on_detect_and_group_faces'):
            mw._on_detect_and_group_faces()
        else:
            print("[Sidebar] No face detection handler found in main window")

    def _on_tree_search_changed(self, text):
        """
        Filter tree view based on search text.
        Shows/hides items recursively based on whether they match the search term.
        """
        search_term = text.lower().strip()

        def should_show_item(item):
            """Recursively determine if an item or any of its children match the search."""
            if not search_term:
                return True

            # Check if this item matches
            item_text = item.text().lower()
            if search_term in item_text:
                return True

            # Check if any children match
            for row_idx in range(item.rowCount()):
                child = item.child(row_idx, 0)
                if child and should_show_item(child):
                    return True

            return False

        def set_item_visibility(item, index):
            """Set visibility for an item and its children."""
            should_show = should_show_item(item)
            self.tree.setRowHidden(index.row(), index.parent(), not should_show)

            # Recursively process children
            for row_idx in range(item.rowCount()):
                child = item.child(row_idx, 0)
                if child:
                    child_index = self.model.indexFromItem(child)
                    set_item_visibility(child, child_index)

        # Process all top-level items
        for row_idx in range(self.model.rowCount()):
            item = self.model.item(row_idx, 0)
            if item:
                index = self.model.indexFromItem(item)
                set_item_visibility(item, index)

        # If searching, expand all visible sections to show matches
        if search_term:
            self.tree.expandAll()

    def _on_collapse_clicked(self):
        try:
            mode = self._effective_display_mode()
            if mode == "tabs":
                # Collapse/expand trees in active tab
                if self.tabs_controller is not None:
                    self.tabs_controller.toggle_collapse_expand()
            else:
                any_expanded = False
                for r in range(self.model.rowCount()):
                    idx = self.model.index(r, 0)
                    if self.tree.isExpanded(idx):
                        any_expanded = True
                        break
                if any_expanded:
                    self.collapse_all()
                else:
                    self.expand_all()
        except Exception as e:
            print(f"[Sidebar] collapse action failed: {e}")


    def _get_photo_count(self, folder_id: int) -> int:
        try:
            if hasattr(self.db, "count_for_folder"):
                return int(self.db.count_for_folder(folder_id, project_id=self.project_id) or 0)
            if hasattr(self.db, "get_folder_photo_count"):
                return int(self.db.get_folder_photo_count(folder_id, project_id=self.project_id) or 0)
            with self.db._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (folder_id,))
                val = cur.fetchone()
                return int(val[0]) if val else 0
        except Exception:
            return 0


    def _on_item_clicked(self, index):
        if not index.isValid():
            return

        # Always normalize to the first column
        index = index.sibling(index.row(), 0)
        item = self.model.itemFromIndex(index)
        if not item:
            return

        mode = item.data(Qt.UserRole)
        value = item.data(Qt.UserRole + 1)
        mw = self.window()

        if not hasattr(mw, "grid"):
            return
        
        # ==========================================================
        # Helpers - MUST be defined FIRST before any usage
        # ==========================================================

        def _clear_tag_if_needed():
            """Clear any tag filters when navigating into folders/branches."""
            if mode in ("folder", "branch", "date", "people"):
                # CRITICAL FIX: Clear tag_filter from grid.context directly
                if hasattr(mw, "grid") and hasattr(mw.grid, "context"):
                    if isinstance(mw.grid.context, dict) and "tag_filter" in mw.grid.context:
                        mw.grid.context["tag_filter"] = None
                        print(f"[TAG FILTER] Cleared tag filter when navigating to {mode}")

        def _ensure_video_paths_only(paths):
            """
            Guarantees that mixed content is filtered down to videos only.
            🐞 CRITICAL: Prevents photos from appearing in video sections.
            """
            from main_window_qt import is_video_file
            import os
            
            if not paths:
                return []
            
            original_count = len(paths)
            # Filter: must be video AND exist
            filtered = [p for p in paths if is_video_file(p) and os.path.exists(p)]
            removed_count = original_count - len(filtered)
            
            if removed_count > 0:
                non_video_count = sum(1 for p in paths if not is_video_file(p))
                missing_count = sum(1 for p in paths if is_video_file(p) and not os.path.exists(p))
                print(f"[VIDEO_FILTER] ⚠️ Filtered: {non_video_count} non-video, {missing_count} missing = {removed_count} total removed from {original_count} paths")
            
            return filtered
        
        # 🚨 CRITICAL FIX: Section headers should show total counts and navigate to all content
        # Updated behavior:
        # - Section headers display sum of all child counts
        # - Clicking section header shows ALL photos from that section
        if mode is None or mode == "None":
            section_name = item.text()
            
            print(f"[SIDEBAR] Section header '{section_name}' clicked")
            
            # Determine section type and navigate - NO count calculation, just direct navigation
            if "🌿" in section_name or "Branches" in section_name:
                # Branches section - show all photos
                if hasattr(mw.grid, "context") and isinstance(mw.grid.context, dict):
                    mw.grid.context["tag_filter"] = None
                mw.grid.set_context("branch", "all")
                return
            elif "📅" in section_name and "Quick" in section_name:
                # Quick Dates section - show all photos
                if hasattr(mw.grid, "context") and isinstance(mw.grid.context, dict):
                    mw.grid.context["tag_filter"] = None
                mw.grid.set_context("branch", "all")
                return
            elif "📁" in section_name or "Folders" in section_name:
                # Folders section - show all photos from all folders
                if hasattr(mw.grid, "context") and isinstance(mw.grid.context, dict):
                    mw.grid.context["tag_filter"] = None
                mw.grid.set_context("branch", "all")  # All photos = all folders
                return
            elif "📅" in section_name and "By Date" in section_name:
                # 🐞 FIX: Check if this is inside Videos section by looking at parent
                parent_item = item.parent()
                if parent_item and "🎬" in parent_item.text():
                    # Inside Videos section - this should now have mode data, but fallback just in case
                    print(f"[SIDEBAR] Video date header clicked - should not reach here if mode data is set")
                    return
                # Photos By Date section - show all photos
                if hasattr(mw.grid, "context") and isinstance(mw.grid.context, dict):
                    mw.grid.context["tag_filter"] = None
                mw.grid.set_context("branch", "all")
                return
            elif "👥" in section_name or "People" in section_name:
                # People section - if empty, just expand; if has children, show all faces
                if item.rowCount() == 0:
                    # No faces detected - just toggle
                    item_index = self.model.indexFromItem(item)
                    if self.tree.isExpanded(item_index):
                        self.tree.collapse(item_index)
                    else:
                        self.tree.expand(item_index)
                    return
                
                # Has face clusters - collect all paths
                if hasattr(mw.grid, "context") and isinstance(mw.grid.context, dict):
                    mw.grid.context["tag_filter"] = None
                all_paths = []
                for row in range(item.rowCount()):
                    face_item = item.child(row, 0)
                    if face_item:
                        cluster_key = face_item.data(Qt.UserRole + 1)
                        if cluster_key and cluster_key.startswith("facecluster:"):
                            branch_key = cluster_key.split(":", 1)[1]
                            try:
                                paths = self.db.get_paths_for_cluster(self.project_id, branch_key)
                                all_paths.extend(paths)
                            except Exception as e:
                                print(f"[SIDEBAR] Error getting paths for cluster {branch_key}: {e}")
                
                if all_paths:
                    # Remove duplicates
                    all_paths = list(dict.fromkeys(all_paths))  # Preserves order
                    mw.grid.model.clear()
                    mw.grid.load_custom_paths(all_paths, content_type="mixed")
                    mw.statusBar().showMessage(f"👥 Showing {len(all_paths)} photo(s) with faces")
                else:
                    mw.statusBar().showMessage("⚠️ No photos with detected faces")
                return
            elif "🏷" in section_name or "Tags" in section_name:
                # Tags section - if empty, just expand; if has tags, show all tagged photos
                if item.rowCount() == 0:
                    # No tags - just toggle
                    item_index = self.model.indexFromItem(item)
                    if self.tree.isExpanded(item_index):
                        self.tree.collapse(item_index)
                    else:
                        self.tree.expand(item_index)
                    return
                
                # Has tags - collect all tagged photos
                if hasattr(mw.grid, "context") and isinstance(mw.grid.context, dict):
                    mw.grid.context["tag_filter"] = None
                all_paths = []
                for row in range(item.rowCount()):
                    tag_item = item.child(row, 0)
                    if tag_item:
                        tag_name = tag_item.data(Qt.UserRole + 1)
                        if tag_name:
                            try:
                                paths = self.db.get_image_paths_for_tag(tag_name, self.project_id)
                                all_paths.extend(paths)
                            except Exception as e:
                                print(f"[SIDEBAR] Error getting paths for tag {tag_name}: {e}")
                
                if all_paths:
                    # Remove duplicates
                    all_paths = list(dict.fromkeys(all_paths))  # Preserves order
                    mw.grid.model.clear()
                    mw.grid.load_custom_paths(all_paths, content_type="mixed")
                    mw.statusBar().showMessage(f"🏷️ Showing {len(all_paths)} tagged photo(s)")
                else:
                    mw.statusBar().showMessage("⚠️ No tagged photos")
                return
            elif "📱" in section_name or "Mobile" in section_name:
                # Mobile Devices section - empty or no devices
                item_index = self.model.indexFromItem(item)
                if self.tree.isExpanded(item_index):
                    self.tree.collapse(item_index)
                else:
                    self.tree.expand(item_index)
                return
            elif "🎬" in section_name or "Videos" in section_name:
                # 🐞 FIX: Main Videos section header - show all videos
                print(f"[SIDEBAR] Main Videos section header clicked")
                _clear_tag_if_needed()
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                paths = _ensure_video_paths_only([v["path"] for v in videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"🎬 Showing {len(paths)} videos")
                return
            else:
                # 🐞 FIX: Unknown section - check if it's a video subsection by parent context
                parent_item = item.parent()
                if parent_item and "🎬" in parent_item.text():
                    # This is a video subsection header that fell through - should have mode data
                    print(f"[SIDEBAR] ERROR: Video subsection '{section_name}' has no mode data - should not happen!")
                    print(f"[SIDEBAR] Parent: {parent_item.text()}")
                    # Just toggle expansion as fallback
                    item_index = self.model.indexFromItem(item)
                    if self.tree.isExpanded(item_index):
                        self.tree.collapse(item_index)
                    else:
                        self.tree.expand(item_index)
                    return
                # Unknown section - just toggle expansion
                item_index = self.model.indexFromItem(item)
                if self.tree.isExpanded(item_index):
                    self.tree.collapse(item_index)
                else:
                    self.tree.expand(item_index)
            return

        # ==========================================================
        # Folder
        # ==========================================================
        if mode == "folder" and value:
            _clear_tag_if_needed()
            mw.grid.set_context("folder", value)
            return

        # ==========================================================
        # Branch (photos)
        # ==========================================================
        if mode == "branch" and value:
            _clear_tag_if_needed()
            val_str = str(value)

            # Date branch
            if val_str.startswith("date:"):
                mw.grid.set_context("date", val_str.replace("date:", ""))
            elif val_str.startswith("facecluster:"):
                branch_key = val_str.split(":", 1)[1]
                mw.grid.set_context("people", branch_key)

                # Update status bar like folders/dates do
                try:
                    paths = self.db.get_paths_for_cluster(self.project_id, branch_key) if self.project_id else []
                    # Get person name from face_branch_reps
                    clusters = self.db.get_face_clusters(self.project_id) if self.project_id else []
                    person_name = next(
                        (c["display_name"] for c in clusters if c["branch_key"] == branch_key),
                        branch_key
                    )
                    mw.statusBar().showMessage(f"👤 Showing {len(paths)} photo(s) of {person_name}")
                except Exception as e:
                    print(f"[Sidebar] Failed to update status bar for people: {e}")

            else:
                mw.grid.set_context("branch", val_str)
            return

        # ==========================================================
        # People (face clusters) — FIX: route through branch pipeline
        # ==========================================================
        if mode == "people" and value:
            _clear_tag_if_needed()

            branch_val = str(value)

            # Normalize: Accept "facecluster:face_000" or "face_000"
            if branch_val.startswith("facecluster:"):
                branch_key = branch_val.split(":", 1)[1]
            else:
                branch_key = branch_val

            # 🔥 CRITICAL FIX:
            # People clusters **must** be routed through branch mode.
            # This is the only path that correctly calls:
            #   get_images_by_branch()
            # which is exactly what your logs show is working.
            mw.grid.set_context("branch", branch_key)
            mw.statusBar().showMessage(f"👥 Showing photos for {branch_key}")

            return

        # ==========================================================
        # Date (photos)
        # ==========================================================
        if mode == "date" and value:
            _clear_tag_if_needed()
            mw.grid.set_context("date", value)
            return

        # ==========================================================
        # Tags
        # ==========================================================
        if mode == "tag" and value:
            # CRITICAL FIX: Clear navigation context when clicking on tag
            # Tags should show ALL photos with that tag across the entire project,
            # not filter within current date/branch context
            _clear_tag_if_needed()  # Clear any existing tag filter
            
            # Set grid to show all photos with this tag (no branch/date/folder context)
            if hasattr(mw, "grid") and hasattr(mw.grid, "set_context"):
                # Clear navigation context and set tag filter
                mw.grid.context = {
                    "mode": None,  # No navigation mode
                    "key": None,   # No navigation key
                    "tag_filter": value  # Only tag filter active
                }
                mw.grid.reload()
                print(f"[TAG FILTER] Showing ALL photos with tag '{value}' (no navigation context)")
            return

        # ==========================================================
        # VIDEO MODES
        # ==========================================================

        from services.video_service import VideoService
        video_service = VideoService()

        # 🐞 FIX: Video date header - show all dated videos
        if mode == "videos_date_header" and value == "all":
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                # Filter to only videos with dates (created_date or date_taken)
                dated_videos = [v for v in videos if v.get('created_date') or v.get('date_taken')]
                print(f"[VIDEO_FILTER] Date header clicked: {len(dated_videos)} dated videos out of {len(videos)} total")
                paths = _ensure_video_paths_only([v["path"] for v in dated_videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📅 Showing {len(paths)} videos with dates")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in date header: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading dated videos: {e}")
            return

        # 🐞 FIX: Video duration header - show all videos with duration metadata
        if mode == "videos_duration_header" and value == "all":
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                duration_videos = [v for v in videos if v.get('duration_seconds')]
                print(f"[VIDEO_FILTER] Duration header clicked: {len(duration_videos)} videos with duration out of {len(videos)} total")
                paths = _ensure_video_paths_only([v["path"] for v in duration_videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"⏱️ Showing {len(paths)} videos with duration metadata")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in duration header: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading duration videos: {e}")
            return

        # 🐞 FIX: Video resolution header - show all videos with resolution metadata
        if mode == "videos_resolution_header" and value == "all":
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                res_videos = [v for v in videos if v.get('width') and v.get('height')]
                print(f"[VIDEO_FILTER] Resolution header clicked: {len(res_videos)} videos with resolution out of {len(videos)} total")
                paths = _ensure_video_paths_only([v["path"] for v in res_videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📺 Showing {len(paths)} videos with resolution metadata")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in resolution header: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading resolution videos: {e}")
            return

        # 🐞 FIX: Video codec header - show all videos with codec metadata
        if mode == "videos_codec_header" and value == "all":
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                codec_videos = [v for v in videos if v.get('codec')]
                print(f"[VIDEO_FILTER] Codec header clicked: {len(codec_videos)} videos with codec out of {len(videos)} total")
                paths = _ensure_video_paths_only([v["path"] for v in codec_videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"🎞️ Showing {len(paths)} videos with codec metadata")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in codec header: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading codec videos: {e}")
            return

        # 🐞 FIX: Video size header - show all videos with size metadata
        if mode == "videos_size_header" and value == "all":
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                size_videos = [v for v in videos if v.get('size_kb')]
                print(f"[VIDEO_FILTER] Size header clicked: {len(size_videos)} videos with size out of {len(videos)} total")
                paths = _ensure_video_paths_only([v["path"] for v in size_videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📦 Showing {len(paths)} videos with size metadata")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in size header: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading size videos: {e}")
            return

        # All videos
        if mode == "videos" and value == "all":
            _clear_tag_if_needed()
            try:
                # PHASE 3: Save video selection to session state
                try:
                    from session_state_manager import get_session_state
                    get_session_state().set_section("videos")
                    get_session_state().set_selection("video", "all", "All Videos")
                    print(f"[SidebarQt] PHASE 3: Saved video selection: All Videos")
                except Exception as se:
                    print(f"[SidebarQt] Failed to save session state: {se}")

                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                paths = _ensure_video_paths_only([v["path"] for v in videos])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"🎬 Showing {len(paths)} videos")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR loading all videos: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading videos: {e}")
            return

        # Duration filter
        if mode == "videos_duration" and value:
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                print(f"[VIDEO_FILTER] Duration '{value}': Loaded {len(videos)} total videos")
                filtered = video_service.filter_by_duration_key(videos, value)
                print(f"[VIDEO_FILTER] Duration '{value}': Filtered to {len(filtered)} videos")
                paths = _ensure_video_paths_only([v["path"] for v in filtered])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"⏱ Showing {len(paths)} {value} videos")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in duration filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by duration: {e}")
            return

        # Resolution filter
        if mode == "videos_resolution" and value:
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                print(f"[VIDEO_FILTER] Resolution '{value}': Loaded {len(videos)} total videos")
                filtered = video_service.filter_by_resolution_key(videos, value)
                print(f"[VIDEO_FILTER] Resolution '{value}': Filtered to {len(filtered)} videos")
                paths = _ensure_video_paths_only([v["path"] for v in filtered])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📺 Showing {len(paths)} {value} videos")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in resolution filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by resolution: {e}")
            return

        # Codec filter
        if mode == "videos_codec" and value:
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                print(f"[VIDEO_FILTER] Codec '{value}': Loaded {len(videos)} total videos")
                filtered = video_service.filter_by_codec_key(videos, value)
                print(f"[VIDEO_FILTER] Codec '{value}': Filtered to {len(filtered)} videos")
                paths = _ensure_video_paths_only([v["path"] for v in filtered])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"🎞️ Showing {len(paths)} {value} videos")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in codec filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by codec: {e}")
            return

        # File size filter
        if mode == "videos_size" and value:
            _clear_tag_if_needed()
            try:
                videos = video_service.get_videos_by_project(self.project_id)
                print(f"[VIDEO_FILTER] Size '{value}': Loaded {len(videos)} total videos")
                filtered = video_service.filter_by_file_size(videos, size_range=value)
                print(f"[VIDEO_FILTER] Size '{value}': Filtered to {len(filtered)} videos")
                paths = _ensure_video_paths_only([v["path"] for v in filtered])
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📦 Showing {len(paths)} {value} videos")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in size filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by size: {e}")
            return

        # Video by year
        if mode == "videos_year" and value:
            _clear_tag_if_needed()
            try:
                # PHASE 3: Save video selection to session state
                try:
                    from session_state_manager import get_session_state
                    get_session_state().set_section("videos")
                    get_session_state().set_selection("video", f"year:{value}", f"Videos {value}")
                    print(f"[SidebarQt] PHASE 3: Saved video selection: Videos {value}")
                except Exception as se:
                    print(f"[SidebarQt] Failed to save session state: {se}")

                videos = video_service.get_videos_by_project(self.project_id)
                print(f"[VIDEO_FILTER] Year {value}: Loaded {len(videos)} total videos from project")
                year = int(value)
                filtered = video_service.filter_by_date(videos, year=year)
                print(f"[VIDEO_FILTER] Year {value}: Filtered to {len(filtered)} videos")
                if filtered:
                    print(f"[VIDEO_FILTER] Sample filtered video: {filtered[0].get('path')} (date_taken={filtered[0].get('date_taken')}, created_date={filtered[0].get('created_date')})")
                paths = _ensure_video_paths_only([v["path"] for v in filtered])
                print(f"[VIDEO_FILTER] Year {value}: Final {len(paths)} video paths")
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📅 Showing {len(paths)} videos from {year}")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in year filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by year: {e}")
            return

        # Video by month
        if mode == "videos_month" and value:
            _clear_tag_if_needed()
            try:
                # PHASE 3: Save video selection to session state
                try:
                    from session_state_manager import get_session_state
                    get_session_state().set_section("videos")
                    get_session_state().set_selection("video", f"month:{value}", f"Videos {value}")
                    print(f"[SidebarQt] PHASE 3: Saved video selection: Videos {value}")
                except Exception as se:
                    print(f"[SidebarQt] Failed to save session state: {se}")

                parts = value.split("-")
                year, month = int(parts[0]), int(parts[1])
                videos = video_service.get_videos_by_project(self.project_id)
                print(f"[VIDEO_FILTER] Month {value}: Loaded {len(videos)} total videos from project")
                filtered = video_service.filter_by_date(videos, year=year, month=month)
                print(f"[VIDEO_FILTER] Month {value}: Filtered to {len(filtered)} videos")
                if filtered:
                    print(f"[VIDEO_FILTER] Sample filtered video: {filtered[0].get('path')} (date_taken={filtered[0].get('date_taken')}, created_date={filtered[0].get('created_date')})")
                paths = _ensure_video_paths_only([v["path"] for v in filtered])
                print(f"[VIDEO_FILTER] Month {value}: Final {len(paths)} video paths")
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📅 Showing {len(paths)} videos from {year}-{month:02d}")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in month filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by month: {e}")
            return

        # Video by day
        if mode == "videos_day" and value:
            _clear_tag_if_needed()
            try:
                # PHASE 3: Save video selection to session state
                try:
                    from session_state_manager import get_session_state
                    get_session_state().set_section("videos")
                    get_session_state().set_selection("video", f"day:{value}", f"Videos {value}")
                    print(f"[SidebarQt] PHASE 3: Saved video selection: Videos {value}")
                except Exception as se:
                    print(f"[SidebarQt] Failed to save session state: {se}")

                print(f"[VIDEO_FILTER] Day {value}: Using direct DB query")
                paths = self.db.get_videos_by_date(value, project_id=self.project_id)
                print(f"[VIDEO_FILTER] Day {value}: DB returned {len(paths)} video paths")
                if paths:
                    print(f"[VIDEO_FILTER] Sample path: {paths[0]}")
                paths = _ensure_video_paths_only(paths)
                print(f"[VIDEO_FILTER] Day {value}: Final {len(paths)} video paths after filter")
                mw.grid.model.clear()
                mw.grid.load_custom_paths(paths, content_type="videos")
                mw.statusBar().showMessage(f"📅 Showing {len(paths)} videos from {value}")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in day filter: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error filtering videos by day: {e}")
            return

        # Search videos
        if mode == "videos_search" and value == "search":
            _clear_tag_if_needed()
            try:
                from PySide6.QtWidgets import QInputDialog
                query, ok = QInputDialog.getText(self, "Search Videos", "Search:")
                if ok and query:
                    print(f"[VIDEO_FILTER] Searching for: '{query}'")
                    videos = video_service.get_videos_by_project(self.project_id)
                    print(f"[VIDEO_FILTER] Searching in {len(videos)} videos")
                    filtered = video_service.search_videos(videos, query)
                    print(f"[VIDEO_FILTER] Found {len(filtered)} matching videos")
                    paths = _ensure_video_paths_only([v["path"] for v in filtered])
                    mw.grid.model.clear()
                    mw.grid.load_custom_paths(paths, content_type="videos")
                    mw.statusBar().showMessage(f"🔍 Found {len(paths)} video(s) matching '{query}'")
                else:
                    print(f"[VIDEO_FILTER] Search cancelled")
            except Exception as e:
                print(f"[VIDEO_FILTER] ERROR in video search: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error searching videos: {e}")
            return

        # ==========================================================
        # Location (GPS-based grouping)
        # ==========================================================
        if mode == "location" and value:
            _clear_tag_if_needed()
            try:
                loc_data = value  # dict with lat, lon, name, paths
                paths = loc_data.get('paths', [])
                lat = loc_data.get('lat')
                lon = loc_data.get('lon')
                name = loc_data.get('name')
                
                if paths:
                    # Filter out non-existent files
                    import os
                    valid_paths = [p for p in paths if os.path.exists(p)]
                    
                    # Load photos in grid
                    mw.grid.model.clear()
                    mw.grid.load_custom_paths(valid_paths, content_type="mixed")
                    
                    # Show static map in details panel if available
                    if hasattr(mw, 'details') and lat and lon:
                        self._show_location_map(lat, lon, name, len(valid_paths))
                    
                    mw.statusBar().showMessage(f"📍 Showing {len(valid_paths)} photos from {name}")
                else:
                    mw.statusBar().showMessage(f"⚠️ No photos found for location {name}")
            except Exception as e:
                print(f"[LOCATION] Error loading location photos: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading location: {e}")
            return
        
        # Location root - show all GPS photos
        if mode == "locations_root":
            _clear_tag_if_needed()
            try:
                # Get all location clusters and combine paths
                clusters = self.db.get_location_clusters(self.project_id)
                all_paths = []
                for cluster in clusters:
                    all_paths.extend(cluster['paths'])
                
                # Remove duplicates
                all_paths = list(dict.fromkeys(all_paths))
                
                # Filter out non-existent files
                import os
                valid_paths = [p for p in all_paths if os.path.exists(p)]
                
                mw.grid.model.clear()
                mw.grid.load_custom_paths(valid_paths, content_type="mixed")
                mw.statusBar().showMessage(f"🗺️ Showing {len(valid_paths)} photos with GPS data")
            except Exception as e:
                print(f"[LOCATION] Error loading all GPS photos: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Error loading GPS photos: {e}")
            return

        # ==========================================================
        # DEEP SCAN — Recursive scan of MTP device for all media folders
        # ==========================================================
        if mode == "device_deep_scan":
            from PySide6.QtWidgets import QMessageBox
            from ui.mtp_deep_scan_dialog import MTPDeepScanDialog
            from services.device_sources import DeviceScanner
            import win32com.client
            import pythoncom

            print(f"[Sidebar] Deep scan clicked for device")

            # Get stored device data
            device_obj = item.data(Qt.UserRole + 3)
            device_type = item.data(Qt.UserRole + 2)
            root_path = item.data(Qt.UserRole + 1)

            if not device_obj:
                QMessageBox.warning(mw, "Deep Scan Error", "Device information not available.")
                return

            device_name = device_obj.label
            print(f"[Sidebar] Starting deep scan for: {device_name}")
            print(f"[Sidebar]   Root path: {root_path}")
            print(f"[Sidebar]   Device type: {device_type}")

            # Confirm with user (deep scan can take time)
            reply = QMessageBox.question(
                mw,
                "Deep Scan Device?",
                f"Run deep scan on {device_name}?\n\n"
                f"This will recursively scan the entire device to find media folders\n"
                f"in deep paths (WhatsApp, Telegram, Instagram, etc.).\n\n"
                f"This may take several minutes depending on device size.\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                print(f"[Sidebar] Deep scan cancelled by user")
                return

            try:
                # Create scanner instance (no device registration needed for deep scan)
                scanner = DeviceScanner(db=self.db, register_devices=False)

                # Use scanner helper to find storage item by re-enumerating devices
                # (MTP paths cannot be navigated by parsing path strings)
                print(f"[Sidebar] Finding device storage for deep scan...")
                storage_item, found_device_name = scanner.find_storage_item_by_path(root_path)

                if not storage_item:
                    raise Exception(
                        f"Cannot find device storage.\n\n"
                        f"The device may have been disconnected or locked.\n\n"
                        f"Please ensure:\n"
                        f"• {device_name} is connected via USB\n"
                        f"• Device is unlocked\n"
                        f"• MTP mode is enabled"
                    )

                print(f"[Sidebar] ✓ Found storage item: {found_device_name}")

                # Show deep scan dialog with progress
                dialog = MTPDeepScanDialog(
                    device_name=device_name,
                    scanner=scanner,
                    storage_item=storage_item,
                    device_type=device_type,
                    max_depth=8,
                    parent=mw
                )

                # Execute dialog
                if dialog.exec():
                    # Deep scan successful - add new folders to sidebar
                    new_folders = dialog.new_folders

                    if new_folders:
                        print(f"[Sidebar] Adding {len(new_folders)} new folders to sidebar...")

                        # Get device item from tree
                        device_item = item.parent()
                        if not device_item:
                            print(f"[Sidebar] Error: Cannot find device item")
                            return

                        # Find deep scan button index to insert folders before it
                        deep_scan_row = -1
                        for row in range(device_item.rowCount()):
                            child = device_item.child(row, 0)
                            if child and child.data(Qt.UserRole) == "device_deep_scan":
                                deep_scan_row = row
                                break

                        # Add new folders to device tree (before deep scan button)
                        insert_row = deep_scan_row if deep_scan_row >= 0 else device_item.rowCount()

                        for folder in new_folders:
                            folder_name = f"  • {folder.name}"
                            folder_item = QStandardItem(folder_name)
                            folder_item.setEditable(False)
                            folder_item.setData("device_folder", Qt.UserRole)
                            folder_item.setData(folder.path, Qt.UserRole + 1)

                            # Make count item
                            from PySide6.QtGui import QColor
                            count_item = QStandardItem(str(folder.photo_count) if folder.photo_count > 0 else "")
                            count_item.setEditable(False)
                            count_item.setForeground(QColor("#888888"))
                            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

                            # Insert before deep scan button
                            device_item.insertRow(insert_row, [folder_item, count_item])
                            insert_row += 1

                            print(f"[Sidebar]   Added: {folder.name} ({folder.photo_count} files)")

                        # Update device photo count
                        device_count_item = device_item.parent().child(device_item.row(), 1)
                        if device_count_item:
                            current_count = int(device_count_item.text()) if device_count_item.text() else 0
                            new_count = current_count + sum(f.photo_count for f in new_folders)
                            device_count_item.setText(str(new_count))

                        print(f"[Sidebar] ✓ Deep scan complete: {len(new_folders)} folders added")

                        mw.statusBar().showMessage(
                            f"🔍 Deep scan complete: {len(new_folders)} new folder(s) found"
                        )

                        # Expand device tree to show new folders
                        device_index = self.model.indexFromItem(device_item)
                        self.tree.expand(device_index)

                    else:
                        print(f"[Sidebar] Deep scan found no new folders")
                        mw.statusBar().showMessage("🔍 Deep scan complete: no new folders found")

                else:
                    # User cancelled
                    print(f"[Sidebar] Deep scan cancelled")
                    mw.statusBar().showMessage("🔍 Deep scan cancelled")

            except Exception as e:
                print(f"[Sidebar] Deep scan failed: {e}")
                import traceback
                traceback.print_exc()

                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    mw,
                    "Deep Scan Failed",
                    f"Failed to scan device:\n\n{e}\n\n"
                    f"The device may have been disconnected or locked."
                )

            return

        # ==========================================================
        # MOBILE DEVICE FOLDERS — Direct access to device media
        # ==========================================================
        if mode == "device_folder" and value:
            _clear_tag_if_needed()
            from pathlib import Path

            # CRITICAL: Ensure project exists before importing
            if not self.project_id:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    mw,
                    "No Project Selected",
                    "Please create or select a project before importing from devices.\n\n"
                    "Use 'Project → New Project' or 'Project → Open Project' from the menu."
                )
                return

            # Check if this is a Windows Shell namespace path (MTP device)
            is_shell_path = value.startswith("::")

            media_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
                              '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
            media_paths = []

            try:
                if is_shell_path:
                    # Windows MTP device - show import dialog instead of direct copy
                    print(f"[Sidebar] Opening MTP import dialog for: {value}")
                    try:
                        from ui.mtp_import_dialog import MTPImportDialog

                        # Extract folder name from path
                        folder_name = value.split("\\")[-1] if "\\" in value else "device folder"

                        # Extract device name from parent item
                        device_name = "Unknown Device"
                        try:
                            # Get clicked item from tree
                            current_index = self.tree.currentIndex()
                            if current_index.isValid():
                                # Get parent device item
                                parent_index = current_index.parent()
                                if parent_index.isValid():
                                    parent_item = self.model.itemFromIndex(parent_index)
                                    if parent_item:
                                        # Extract device label (e.g., "⚪ A54 von Ammar - Interner Speicher")
                                        device_label = parent_item.text()

                                        # Remove status icon prefix (⚪, 🟢, 🟡, etc.)
                                        if device_label and len(device_label) > 2 and device_label[1] == ' ':
                                            device_label = device_label[2:].strip()

                                        # Extract base device name (before " - ")
                                        if " - " in device_label:
                                            device_name = device_label.split(" - ")[0].strip()
                                        else:
                                            device_name = device_label.strip()

                                        print(f"[Sidebar] Extracted device name: {device_name}")
                        except Exception as e:
                            print(f"[Sidebar] Error extracting device name: {e}")

                        # Show import dialog
                        dialog = MTPImportDialog(
                            device_name=device_name,
                            folder_name=folder_name,
                            mtp_path=value,
                            db=self.db,
                            project_id=self.project_id,
                            parent=mw
                        )

                        # Execute dialog and handle result
                        if dialog.exec():
                            # Import successful - load imported files into grid
                            imported_paths = dialog.imported_paths
                            if imported_paths:
                                print(f"[Sidebar] Import successful: {len(imported_paths)} files")

                                # Load imported files into grid
                                mw.grid.model.clear()
                                mw.grid.load_custom_paths(imported_paths, content_type="mixed")

                                mw.statusBar().showMessage(
                                    f"📱 Imported and showing {len(imported_paths)} item(s) from {folder_name} [{device_name}]"
                                )

                                # AUTO-REFRESH: Update sidebar sections to show newly imported files
                                print(f"[Sidebar] Refreshing sidebar sections...")

                                # Refresh tree view (list mode)
                                print(f"[Sidebar]   Rebuilding tree view...")
                                self._build_tree_model()

                                # Refresh tabs (tabs mode)
                                if self.tabs_controller is not None and hasattr(self.tabs_controller, '_tab_populated'):
                                    # Refresh Folders tab (force reload)
                                    if "folders" in self.tabs_controller._tab_populated:
                                        self.tabs_controller._tab_populated.discard("folders")
                                        print(f"[Sidebar]   ✓ Cleared Folders tab cache")

                                    # Refresh Dates tab (force reload)
                                    if "dates" in self.tabs_controller._tab_populated:
                                        self.tabs_controller._tab_populated.discard("dates")
                                        print(f"[Sidebar]   ✓ Cleared Dates tab cache")

                                    # Refresh Branches tab (force reload - counts may change)
                                    if "branches" in self.tabs_controller._tab_populated:
                                        self.tabs_controller._tab_populated.discard("branches")
                                        print(f"[Sidebar]   ✓ Cleared Branches tab cache")

                                    # Refresh Tags tab (force reload - new tags may be added)
                                    if "tags" in self.tabs_controller._tab_populated:
                                        self.tabs_controller._tab_populated.discard("tags")
                                        print(f"[Sidebar]   ✓ Cleared Tags tab cache")

                                    # If current tab is Folders, Dates, Branches, or Tags, trigger reload
                                    current_tab_idx = self.tabs_controller.tab_widget.currentIndex()
                                    if current_tab_idx >= 0:
                                        tab_widget = self.tabs_controller.tab_widget.widget(current_tab_idx)
                                        if tab_widget:
                                            tab_type = tab_widget.property("tab_type")
                                            if tab_type in ["folders", "dates", "branches", "tags"]:
                                                print(f"[Sidebar]   Reloading {tab_type} tab...")
                                                self.tabs_controller._populate_tab(tab_type, current_tab_idx, force=True)

                                print(f"[Sidebar] ✓ Import complete, grid loaded with {len(imported_paths)} files")
                                print(f"[Sidebar] ✓ Sidebar tabs will refresh when viewed")
                            else:
                                print(f"[Sidebar] No files imported")
                                mw.statusBar().showMessage("📱 No files were imported")
                        else:
                            # User cancelled
                            print(f"[Sidebar] Import cancelled by user")
                            mw.statusBar().showMessage("📱 Import cancelled")

                    except ImportError as e:
                        print(f"[Sidebar] Import dialog not available: {e}")
                        import traceback
                        traceback.print_exc()
                        mw.statusBar().showMessage(f"⚠️ Cannot import from MTP device: {e}")
                        return
                    except Exception as e:
                        print(f"[Sidebar] Failed to show import dialog: {e}")
                        import traceback
                        traceback.print_exc()
                        mw.statusBar().showMessage(f"⚠️ Failed to import from device: {e}")
                        return

                else:
                    # Regular file system path (Linux/Mac MTP mounts, SD cards, etc.)
                    device_folder_path = Path(value)
                    if not device_folder_path.exists():
                        mw.statusBar().showMessage(f"⚠️ Device folder not accessible: {value}")
                        return

                    # Recursively scan folder for media files (limited depth for performance)
                    def scan_folder(folder, depth=0, max_depth=3):
                        if depth > max_depth:
                            return
                        try:
                            for item in folder.iterdir():
                                if item.is_file():
                                    if item.suffix.lower() in media_extensions:
                                        media_paths.append(str(item))
                                elif item.is_dir() and not item.name.startswith('.'):
                                    scan_folder(item, depth + 1, max_depth)
                        except (PermissionError, OSError):
                            pass

                    scan_folder(device_folder_path)

                    # Load paths into grid
                    mw.grid.model.clear()
                    mw.grid.load_custom_paths(media_paths, content_type="mixed")

                    folder_name = device_folder_path.name
                    mw.statusBar().showMessage(f"📱 Showing {len(media_paths)} item(s) from {folder_name}")

                    print(f"[Sidebar] Loaded {len(media_paths)} media files from device folder: {value}")

            except Exception as e:
                print(f"[Sidebar] Failed to load device folder: {e}")
                import traceback
                traceback.print_exc()
                mw.statusBar().showMessage(f"⚠️ Failed to load device folder: {e}")

            return

        # ==========================================================
        # CATCHALL - Unhandled video modes
        # ==========================================================
        if mode and mode.startswith("videos_"):
            # 🐞 FIX: Any video mode that wasn't handled above is an error
            print(f"[SIDEBAR] ERROR: Unhandled video mode '{mode}' with value '{value}'")
            print(f"[SIDEBAR] This should not happen - all video modes should be handled above")
            mw.statusBar().showMessage(f"⚠️ Unknown video filter: {mode}")
            return

        # ------------------------------------------------------
        # After any content change: reflow
        # ------------------------------------------------------
        QTimer.singleShot(0, lambda: (
            mw.grid.list_view.doItemsLayout(),
            mw.grid.list_view.viewport().update()
        ))


    def _on_item_double_clicked(self, index):
        """
        Handle double-click on tree items.
        For People items: trigger rename dialog
        For other items: do nothing (single-click already handles navigation)
        """
        if not index.isValid():
            return

        # Always normalize to the first column
        index = index.sibling(index.row(), 0)
        item = self.model.itemFromIndex(index)
        if not item:
            return

        mode = item.data(Qt.UserRole)
        value = item.data(Qt.UserRole + 1)

        # Double-click on People items triggers rename
        if mode in ("facecluster", "people"):
            branch_key = value
            if isinstance(branch_key, str) and branch_key.startswith("facecluster:"):
                branch_key = branch_key.split(":", 1)[1]

            # Trigger rename dialog
            self._rename_face_cluster(branch_key, item.text())
            return

        # For all other items, double-click does nothing
        # (single-click already handles navigation to the content)


    # ---- tree mode builder ----
    def _build_tree_model(self):
        # Build tree synchronously for folders (counts populated right away),
        # and register branch targets for async fill to keep responsiveness.
        print(f"[SidebarQt] _build_tree_model() called with project_id={self.project_id}")

        # CRITICAL: Prevent concurrent rebuilds that cause Qt crashes during rapid project switching
        # Similar to grid reload() guard pattern
        if getattr(self, '_rebuilding_tree', False):
            print("[Sidebar] _build_tree_model() blocked - already rebuilding (prevents concurrent rebuild crash)")
            return

        try:
            self._rebuilding_tree = True

            # CRITICAL FIX: Cancel any pending count workers before rebuilding
            self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000

            # CRITICAL FIX: Detach model from view FIRST before any processEvents
            # This prevents re-entrant calls from accessing invalid model state
            print("[Sidebar] Detaching old model from tree view")
            self.tree.setModel(None)

            # Clear selection to release any Qt internal references
            if hasattr(self.tree, 'selectionModel') and self.tree.selectionModel():
                try:
                    self.tree.selectionModel().clear()
                except (RuntimeError, AttributeError):
                    pass

            # CRITICAL FIX: Only process events if initialized AND model is detached
            # Reduced to single pass to minimize re-entrancy window
            # This ensures pending deleteLater() and worker callbacks complete
            # CRITICAL FIX: Avoid forcing event processing here to prevent re-entrant crashes
            # Let Qt process deleteLater() naturally via the event loop
            # Previously: QCoreApplication.processEvents() caused instability during project switches
            print("[Sidebar] Pending events processed (skipped explicit processEvents)")

            # CRITICAL FIX: Create a completely fresh model instead of clearing the old one
            # This is safer than model.clear() which can cause Qt C++ segfaults
            print("[Sidebar] Creating fresh model (avoiding Qt segfault)")
            
            old_model = self.model
            self.model = QStandardItemModel(self.tree)
            self.model.setHorizontalHeaderLabels(["Folder / Branch", "Photos"])

            # Schedule old model for deletion (let Qt clean it up safely)
            if old_model is not None:
                try:
                    old_model.deleteLater()
                except (RuntimeError, AttributeError) as e:
                    print(f"[Sidebar] Warning: Could not schedule old model for deletion: {e}")

            # Attach the fresh model to the tree view
            print("[Sidebar] Attaching fresh model to tree view")
            
            self.tree.setModel(self.model)

            self._count_targets = []
            try:
                # Get total photo count for displaying on top-level sections
                total_photos = 0
                if self.project_id:
                    try:
                        # Get count from "all" branch
                        all_photos = self.db.get_project_images(self.project_id, branch_key='all')
                        total_photos = len(all_photos) if all_photos else 0
                    except Exception as e:
                        print(f"[Sidebar] Could not get total photo count: {e}")
                        total_photos = 0

                # Helper to create styled count item
                def _make_count_item(count_val):
                    item = QStandardItem(str(count_val) if count_val else "")
                    item.setEditable(False)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setForeground(QColor("#BBBBBB"))
                    return item

                branch_root = QStandardItem(tr("sidebar.branches"))
                branch_root.setEditable(False)
                branch_count_item = _make_count_item(total_photos)
                self.model.appendRow([branch_root, branch_count_item])
                branches = list_branches(self.project_id) if self.project_id else []

                # DEBUG: Log branches loaded
                print(f"[SidebarQt] list_branches() returned {len(branches)} branches")
                if len(branches) > 0:
                    print(f"[SidebarQt] Sample branches: {branches[:5]}")

                for b in branches:
#                    name_item = QStandardItem(b["display_name"])
                    # Do NOT show face clusters here – they have their own
                    # dedicated "👥 People" section.
                    branch_key = b.get("branch_key") or ""
                    if isinstance(branch_key, str) and branch_key.startswith("face_"):
                        continue

                    name_item = QStandardItem(b.get("display_name") or branch_key)
                    
                    count_item = QStandardItem("")
                    name_item.setEditable(False)
                    count_item.setEditable(False)
                    name_item.setData("branch", Qt.UserRole)
                    
#                    name_item.setData(b["branch_key"], Qt.UserRole + 1)
                    name_item.setData(branch_key, Qt.UserRole + 1)
                    
                    count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    count_item.setForeground(QColor("#BBBBBB"))
                    branch_root.appendRow([name_item, count_item])
                    # register branch for async counts
#                    self._count_targets.append(("branch", b["branch_key"], name_item, count_item))
                    self._count_targets.append(("branch", branch_key, name_item, count_item))

                quick_root = QStandardItem(tr("sidebar.quick_dates"))
                quick_root.setEditable(False)
                quick_count_item = _make_count_item(total_photos)
                self.model.appendRow([quick_root, quick_count_item])
                try:
                    quick_rows = self.db.get_quick_date_counts(project_id=self.project_id)
                except Exception:
                    quick_rows = []
                for row in quick_rows:
                    name_item = QStandardItem(row["label"])
                    count_item = QStandardItem(str(row["count"]) if row and row.get("count") else "")
                    name_item.setEditable(False)
                    count_item.setEditable(False)
                    name_item.setData("branch", Qt.UserRole)
                    name_item.setData(row["key"], Qt.UserRole + 1)
                    count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    count_item.setForeground(QColor("#BBBBBB"))
                    quick_root.appendRow([name_item, count_item])

                # IMPORTANT FIX: use synchronous folder population as in the previous working version,
                # so folder counts are calculated and displayed immediately.
                folder_root = QStandardItem(tr("sidebar.folders"))
                folder_root.setEditable(False)
                folder_count_item = _make_count_item(total_photos)
                self.model.appendRow([folder_root, folder_count_item])
                # synchronous (restores the previous working behavior)
                self._add_folder_items(folder_root, None)



                self._build_by_date_section()
#                self._build_tag_section()

                # >>> NEW: 🎬 Videos section
                try:
                    from services.video_service import VideoService
                    video_service = VideoService()
                    print(f"[Sidebar] Loading videos for project_id={self.project_id}")
                    videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                    total_videos = len(videos)
                    print(f"[Sidebar] Found {total_videos} videos in project {self.project_id}")
                except Exception as e:
                    print(f"[Sidebar] Failed to load videos: {e}")
                    import traceback
                    traceback.print_exc()
                    total_videos = 0
                    videos = []

                if videos:
                    # Build Videos section with all subsections
                    root_name_item = QStandardItem(tr("sidebar.videos"))
                    root_cnt_item = _make_count_item(total_videos)
                    root_name_item.setEditable(False)
                    root_cnt_item.setEditable(False)
                    self.model.appendRow([root_name_item, root_cnt_item])

                    # Add "All Videos" option
                    all_videos_item = QStandardItem(tr("sidebar.all_videos"))
                    all_videos_item.setEditable(False)
                    all_videos_item.setData("videos", Qt.UserRole)
                    all_videos_item.setData("all", Qt.UserRole + 1)
                    all_count = QStandardItem(str(total_videos))
                    all_count.setEditable(False)
                    all_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    all_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([all_videos_item, all_count])

                    # 🎯 Filter by Duration
                    duration_parent = QStandardItem(tr("sidebar.by_duration"))
                    duration_parent.setEditable(False)
                    # 🐞 FIX: Set mode data so clicking header shows all videos with duration metadata
                    duration_parent.setData("videos_duration_header", Qt.UserRole)
                    duration_parent.setData("all", Qt.UserRole + 1)

                    # Count videos by duration
                    short_videos = [v for v in videos if v.get('duration_seconds') and v['duration_seconds'] < 30]
                    medium_videos = [v for v in videos if v.get('duration_seconds') and 30 <= v['duration_seconds'] < 300]
                    long_videos = [v for v in videos if v.get('duration_seconds') and v['duration_seconds'] >= 300]

                    # CRITICAL FIX: Show sum count for Duration section
                    total_duration_videos = len(short_videos) + len(medium_videos) + len(long_videos)
                    duration_count = QStandardItem(str(total_duration_videos))
                    duration_count.setEditable(False)
                    duration_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    duration_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([duration_parent, duration_count])

                    # Short videos (< 30s)
                    short_item = QStandardItem(tr("sidebar.duration_short"))
                    short_item.setEditable(False)
                    short_item.setData("videos_duration", Qt.UserRole)
                    short_item.setData("short", Qt.UserRole + 1)
                    short_count = QStandardItem(str(len(short_videos)))
                    short_count.setEditable(False)
                    short_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    short_count.setForeground(QColor("#888888"))
                    duration_parent.appendRow([short_item, short_count])

                    # Medium videos (30s - 5min)
                    medium_item = QStandardItem(tr("sidebar.duration_medium"))
                    medium_item.setEditable(False)
                    medium_item.setData("videos_duration", Qt.UserRole)
                    medium_item.setData("medium", Qt.UserRole + 1)
                    medium_count = QStandardItem(str(len(medium_videos)))
                    medium_count.setEditable(False)
                    medium_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    medium_count.setForeground(QColor("#888888"))
                    duration_parent.appendRow([medium_item, medium_count])

                    # Long videos (> 5min)
                    long_item = QStandardItem(tr("sidebar.duration_long"))
                    long_item.setEditable(False)
                    long_item.setData("videos_duration", Qt.UserRole)
                    long_item.setData("long", Qt.UserRole + 1)
                    long_count = QStandardItem(str(len(long_videos)))
                    long_count.setEditable(False)
                    long_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    long_count.setForeground(QColor("#888888"))
                    duration_parent.appendRow([long_item, long_count])

                    # 📺 Filter by Resolution
                    res_parent = QStandardItem("📺 By Resolution")
                    res_parent.setEditable(False)
                    # 🐞 FIX: Set mode data so clicking header shows all videos with resolution metadata
                    res_parent.setData("videos_resolution_header", Qt.UserRole)
                    res_parent.setData("all", Qt.UserRole + 1)

                    # Count videos by resolution (require both width and height metadata)
                    # Use max(width, height) to handle portrait/landscape videos consistently
                    # This matches videos_section.py and video_service.py bucketing logic
                    sd_videos = [v for v in videos if v.get('width') and v.get('height') and max(v['width'], v['height']) < 720]
                    hd_videos = [v for v in videos if v.get('width') and v.get('height') and 720 <= max(v['width'], v['height']) < 1080]
                    fhd_videos = [v for v in videos if v.get('width') and v.get('height') and 1080 <= max(v['width'], v['height']) < 2160]
                    uhd_videos = [v for v in videos if v.get('width') and v.get('height') and max(v['width'], v['height']) >= 2160]

                    # CRITICAL FIX: Show sum count for Resolution section
                    total_res_videos = len(sd_videos) + len(hd_videos) + len(fhd_videos) + len(uhd_videos)
                    res_count = QStandardItem(str(total_res_videos))
                    res_count.setEditable(False)
                    res_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    res_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([res_parent, res_count])

                    # SD videos (< 720p)
                    sd_item = QStandardItem("SD (< 720p)")
                    sd_item.setEditable(False)
                    sd_item.setData("videos_resolution", Qt.UserRole)
                    sd_item.setData("sd", Qt.UserRole + 1)
                    sd_cnt = QStandardItem(str(len(sd_videos)))
                    sd_cnt.setEditable(False)
                    sd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    sd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([sd_item, sd_cnt])

                    # HD videos (720p)
                    hd_item = QStandardItem("HD (720p)")
                    hd_item.setEditable(False)
                    hd_item.setData("videos_resolution", Qt.UserRole)
                    hd_item.setData("hd", Qt.UserRole + 1)
                    hd_cnt = QStandardItem(str(len(hd_videos)))
                    hd_cnt.setEditable(False)
                    hd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    hd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([hd_item, hd_cnt])

                    # Full HD videos (1080p)
                    fhd_item = QStandardItem("Full HD (1080p)")
                    fhd_item.setEditable(False)
                    fhd_item.setData("videos_resolution", Qt.UserRole)
                    fhd_item.setData("fhd", Qt.UserRole + 1)
                    fhd_cnt = QStandardItem(str(len(fhd_videos)))
                    fhd_cnt.setEditable(False)
                    fhd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    fhd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([fhd_item, fhd_cnt])

                    # 4K videos (2160p+)
                    uhd_item = QStandardItem("4K (2160p+)")
                    uhd_item.setEditable(False)
                    uhd_item.setData("videos_resolution", Qt.UserRole)
                    uhd_item.setData("4k", Qt.UserRole + 1)
                    uhd_cnt = QStandardItem(str(len(uhd_videos)))
                    uhd_cnt.setEditable(False)
                    uhd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    uhd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([uhd_item, uhd_cnt])

                    # 🎞️ Filter by Codec (Option 7)
                    codec_parent = QStandardItem("🎞️ By Codec")
                    codec_parent.setEditable(False)
                    # 🐞 FIX: Set mode data so clicking header shows all videos with codec metadata
                    codec_parent.setData("videos_codec_header", Qt.UserRole)
                    codec_parent.setData("all", Qt.UserRole + 1)

                    # Count videos by codec
                    h264_videos = [v for v in videos if v.get('codec') and v['codec'].lower() in ['h264', 'avc']]
                    hevc_videos = [v for v in videos if v.get('codec') and v['codec'].lower() in ['hevc', 'h265']]
                    vp9_videos = [v for v in videos if v.get('codec') and v['codec'].lower() == 'vp9']
                    av1_videos = [v for v in videos if v.get('codec') and v['codec'].lower() == 'av1']
                    mpeg4_videos = [v for v in videos if v.get('codec') and v['codec'].lower() in ['mpeg4', 'xvid', 'divx']]

                    # CRITICAL FIX: Show sum count for Codec section
                    total_codec_videos = len(h264_videos) + len(hevc_videos) + len(vp9_videos) + len(av1_videos) + len(mpeg4_videos)
                    codec_count = QStandardItem(str(total_codec_videos))
                    codec_count.setEditable(False)
                    codec_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    codec_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([codec_parent, codec_count])

                    # H.264
                    h264_item = QStandardItem("H.264 / AVC")
                    h264_item.setEditable(False)
                    h264_item.setData("videos_codec", Qt.UserRole)
                    h264_item.setData("h264", Qt.UserRole + 1)
                    h264_cnt = QStandardItem(str(len(h264_videos)))
                    h264_cnt.setEditable(False)
                    h264_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    h264_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([h264_item, h264_cnt])

                    # H.265 / HEVC
                    hevc_item = QStandardItem("H.265 / HEVC")
                    hevc_item.setEditable(False)
                    hevc_item.setData("videos_codec", Qt.UserRole)
                    hevc_item.setData("hevc", Qt.UserRole + 1)
                    hevc_cnt = QStandardItem(str(len(hevc_videos)))
                    hevc_cnt.setEditable(False)
                    hevc_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    hevc_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([hevc_item, hevc_cnt])

                    # VP9
                    vp9_item = QStandardItem("VP9")
                    vp9_item.setEditable(False)
                    vp9_item.setData("videos_codec", Qt.UserRole)
                    vp9_item.setData("vp9", Qt.UserRole + 1)
                    vp9_cnt = QStandardItem(str(len(vp9_videos)))
                    vp9_cnt.setEditable(False)
                    vp9_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    vp9_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([vp9_item, vp9_cnt])

                    # AV1
                    av1_item = QStandardItem("AV1")
                    av1_item.setEditable(False)
                    av1_item.setData("videos_codec", Qt.UserRole)
                    av1_item.setData("av1", Qt.UserRole + 1)
                    av1_cnt = QStandardItem(str(len(av1_videos)))
                    av1_cnt.setEditable(False)
                    av1_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    av1_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([av1_item, av1_cnt])

                    # MPEG-4
                    mpeg4_item = QStandardItem("MPEG-4")
                    mpeg4_item.setEditable(False)
                    mpeg4_item.setData("videos_codec", Qt.UserRole)
                    mpeg4_item.setData("mpeg4", Qt.UserRole + 1)
                    mpeg4_cnt = QStandardItem(str(len(mpeg4_videos)))
                    mpeg4_cnt.setEditable(False)
                    mpeg4_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    mpeg4_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([mpeg4_item, mpeg4_cnt])

                    # 📦 Filter by File Size (Option 7)
                    size_parent = QStandardItem("📦 By File Size")
                    size_parent.setEditable(False)
                    # 🐞 FIX: Set mode data so clicking header shows all videos with size metadata
                    size_parent.setData("videos_size_header", Qt.UserRole)
                    size_parent.setData("all", Qt.UserRole + 1)

                    # Count videos by file size
                    small_videos = [v for v in videos if v.get('size_kb') and v['size_kb'] / 1024 < 100]
                    medium_size_videos = [v for v in videos if v.get('size_kb') and 100 <= v['size_kb'] / 1024 < 1024]
                    large_videos = [v for v in videos if v.get('size_kb') and 1024 <= v['size_kb'] / 1024 < 5120]
                    xlarge_videos = [v for v in videos if v.get('size_kb') and v['size_kb'] / 1024 >= 5120]

                    # CRITICAL FIX: Show sum count for File Size section
                    total_size_videos = len(small_videos) + len(medium_size_videos) + len(large_videos) + len(xlarge_videos)
                    size_count = QStandardItem(str(total_size_videos))
                    size_count.setEditable(False)
                    size_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    size_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([size_parent, size_count])

                    # Small (< 100MB)
                    small_size_item = QStandardItem("Small (< 100MB)")
                    small_size_item.setEditable(False)
                    small_size_item.setData("videos_size", Qt.UserRole)
                    small_size_item.setData("small", Qt.UserRole + 1)
                    small_size_cnt = QStandardItem(str(len(small_videos)))
                    small_size_cnt.setEditable(False)
                    small_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    small_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([small_size_item, small_size_cnt])

                    # Medium (100MB - 1GB)
                    medium_size_item = QStandardItem("Medium (100MB - 1GB)")
                    medium_size_item.setEditable(False)
                    medium_size_item.setData("videos_size", Qt.UserRole)
                    medium_size_item.setData("medium", Qt.UserRole + 1)
                    medium_size_cnt = QStandardItem(str(len(medium_size_videos)))
                    medium_size_cnt.setEditable(False)
                    medium_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    medium_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([medium_size_item, medium_size_cnt])

                    # Large (1GB - 5GB)
                    large_size_item = QStandardItem("Large (1GB - 5GB)")
                    large_size_item.setEditable(False)
                    large_size_item.setData("videos_size", Qt.UserRole)
                    large_size_item.setData("large", Qt.UserRole + 1)
                    large_size_cnt = QStandardItem(str(len(large_videos)))
                    large_size_cnt.setEditable(False)
                    large_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    large_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([large_size_item, large_size_cnt])

                    # XLarge (> 5GB)
                    xlarge_size_item = QStandardItem("XLarge (> 5GB)")
                    xlarge_size_item.setEditable(False)
                    xlarge_size_item.setData("videos_size", Qt.UserRole)
                    xlarge_size_item.setData("xlarge", Qt.UserRole + 1)
                    xlarge_size_cnt = QStandardItem(str(len(xlarge_videos)))
                    xlarge_size_cnt.setEditable(False)
                    xlarge_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    xlarge_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([xlarge_size_item, xlarge_size_cnt])

                    # 📅 Filter by Date - Full Year/Month/Day Hierarchy for Videos
                    date_parent = QStandardItem("📅 By Date")
                    date_parent.setEditable(False)
                    # 🐞 FIX: Set mode data so clicking header shows all videos (not photos)
                    date_parent.setData("videos_date_header", Qt.UserRole)
                    date_parent.setData("all", Qt.UserRole + 1)

                    # Get video date hierarchy: {year: {month: [days...]}}
                    try:
                        video_hier = self.db.get_video_date_hierarchy(self.project_id) or {}
                    except Exception as e:
                        print(f"[Sidebar] Failed to get video date hierarchy: {e}")
                        video_hier = {}

                    # Count total videos with dates
                    total_dated_videos = sum(
                        self.db.count_videos_for_year(year, self.project_id)
                        for year in video_hier.keys()
                    ) if video_hier else 0

                    # Build full year/month/day hierarchy (like photos)
                    for year in sorted(video_hier.keys(), key=lambda y: int(str(y)), reverse=True):
                        # Year node
                        year_count = self.db.count_videos_for_year(year, self.project_id)
                        year_item = QStandardItem(str(year))
                        year_item.setEditable(False)
                        year_item.setData("videos_year", Qt.UserRole)
                        year_item.setData(year, Qt.UserRole + 1)

                        year_cnt = QStandardItem(str(year_count))
                        year_cnt.setEditable(False)
                        year_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        year_cnt.setForeground(QColor("#888888"))

                        date_parent.appendRow([year_item, year_cnt])

                        # Month nodes under year
                        months = video_hier[year]
                        for month in sorted(months.keys(), key=lambda m: int(str(m))):
                            month_label = f"{int(month):02d}"
                            month_count = self.db.count_videos_for_month(year, month, self.project_id)
                            month_item = QStandardItem(month_label)
                            month_item.setEditable(False)
                            month_item.setData("videos_month", Qt.UserRole)
                            month_item.setData(f"{year}-{month_label}", Qt.UserRole + 1)
                            month_cnt = QStandardItem(str(month_count))
                            month_cnt.setEditable(False)
                            month_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                            month_cnt.setForeground(QColor("#888888"))
                            year_item.appendRow([month_item, month_cnt])

                            # Day nodes under month
                            days = months[month]
                            day_numbers = set()
                            for ymd in days:
                                try:
                                    parts = ymd.split("-")
                                    if len(parts) == 3:
                                        day_numbers.add(int(parts[2]))
                                except (ValueError, IndexError):
                                    pass

                            for day in sorted(day_numbers):
                                day_label = f"{day:02d}"
                                ymd = f"{year}-{month_label}-{day_label}"
                                day_count = self.db.count_videos_for_day(ymd, self.project_id)
                                day_item = QStandardItem(day_label)
                                day_item.setEditable(False)
                                day_item.setData("videos_day", Qt.UserRole)
                                day_item.setData(ymd, Qt.UserRole + 1)
                                day_cnt = QStandardItem(str(day_count))
                                day_cnt.setEditable(False)
                                day_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                                day_cnt.setForeground(QColor("#888888"))
                                month_item.appendRow([day_item, day_cnt])

                    # Set total count on date parent
                    date_count = QStandardItem(str(total_dated_videos))
                    date_count.setEditable(False)
                    date_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    date_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([date_parent, date_count])

                    # Log the hierarchy build (for debugging)
                    year_count_total = len(video_hier)
                    month_count_total = sum(len(months) for months in video_hier.values())
                    print(f"[VideoDateHierarchy] Building: {year_count_total} years, {month_count_total} months, {total_dated_videos} videos")

                    # 🔍 Search Videos
                    search_item = QStandardItem("🔍 Search Videos...")
                    search_item.setEditable(False)
                    search_item.setData("videos_search", Qt.UserRole)
                    search_item.setData("search", Qt.UserRole + 1)
                    search_count = QStandardItem("")
                    search_count.setEditable(False)
                    root_name_item.appendRow([search_item, search_count])

                    print(f"[Sidebar] Added 🎬 Videos section with {total_videos} videos and filters.")
                # <<< NEW
                
                # ---------------------------------------------------------
                # 🗺️ LOCATIONS SECTION — GPS-based photo grouping
                # ---------------------------------------------------------
                try:
                    location_clusters = self.db.get_location_clusters(self.project_id)
                    
                    if location_clusters:
                        locations_root = QStandardItem("🗺️ Locations")
                        locations_root.setEditable(False)
                        locations_root.setData("locations_root", Qt.UserRole)
                        locations_count_item = QStandardItem("")
                        locations_count_item.setEditable(False)
                        self.model.appendRow([locations_root, locations_count_item])
                        
                        total_gps_photos = 0
                        
                        for cluster in location_clusters:
                            name = cluster['name']
                            count = cluster['count']
                            lat = cluster['lat']
                            lon = cluster['lon']
                            paths = cluster['paths']
                            
                            total_gps_photos += count
                            
                            # Location item
                            loc_item = QStandardItem(f"📍 {name}")
                            loc_item.setEditable(False)
                            loc_item.setData("location", Qt.UserRole)
                            # Store location data for filtering and map display
                            loc_item.setData({
                                'lat': lat,
                                'lon': lon,
                                'name': name,
                                'paths': paths
                            }, Qt.UserRole + 1)
                            
                            # Count item
                            count_item = QStandardItem(str(count))
                            count_item.setEditable(False)
                            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                            count_item.setForeground(QColor("#888888"))
                            
                            locations_root.appendRow([loc_item, count_item])
                        
                        # Update root count
                        locations_count_item.setText(str(total_gps_photos))
                        print(f"[Sidebar] Added 🗺️ Locations section with {len(location_clusters)} locations, {total_gps_photos} photos.")
                except Exception as e:
                    print(f"[Sidebar] Failed to load GPS locations: {e}")
                    import traceback
                    traceback.print_exc()

                # ---------------------------------------------------------
                # 👥 PEOPLE SECTION — CLEAN, FIXED, UNIFIED
                # ---------------------------------------------------------
                try:
                    clusters = self.db.get_face_clusters(self.project_id)
                except Exception as e:
                    print("[Sidebar] get_face_clusters failed:", e)
                    clusters = []

                # Create People root
                people_root = QStandardItem("👥 People")
                people_count_item = QStandardItem("")
                people_root.setEditable(False)
                people_count_item.setEditable(False)
                self.model.appendRow([people_root, people_count_item])

                if clusters:
                    total_faces = 0

#                    for row in clusters:
#                        raw_name = row.get("display_name") or row.get("branch_key")
#                        cluster_id = str(row.get("branch_key"))
#                        count = row.get("member_count", 0) or 0
#                        rep_path = row.get("rep_path", "")
#                        rep_thumb_png = row.get("rep_thumb_png")
#
#                        total_faces += count
#
#                        # Humanize unnamed clusters
#                        if raw_name.startswith("face_"):
#                            try:
#                                num = int(raw_name.split("_")[1])
#                                display_name = f"Unnamed #{num}"
#                            except:
#                                display_name = raw_name
#                        else:
#                            display_name = raw_name
#
#                        name_item = QStandardItem(display_name)
                        
                    for row in clusters:
                        raw_name = row.get("display_name") or row.get("branch_key")
                        cluster_id = str(row.get("branch_key"))
                        count_raw = row.get("member_count", 0) or 0
                        rep_path = row.get("rep_path", "")
                        rep_thumb_png = row.get("rep_thumb_png")

                        # CRITICAL FIX: Handle different types from database (bytes, int, str)
                        # Same issue as get_total_faces() - Qt/SQLite can return bytes instead of int
                        if isinstance(count_raw, (int, float)):
                            count = int(count_raw)
                        elif isinstance(count_raw, bytes):
                            try:
                                count = int.from_bytes(count_raw, byteorder='little')
                            except (ValueError, OverflowError):
                                count = 0
                        elif isinstance(count_raw, str):
                            try:
                                count = int(count_raw)
                            except ValueError:
                                count = 0
                        else:
                            count = 0

                        total_faces += count

                        # Use the DB label as-is so that People and any other
                        # views (branches, etc.) show the same names.
                        display_name = str(raw_name)

                        name_item = QStandardItem(display_name)


                        name_item.setEditable(False)

                        # Load thumbnail icon with EXIF correction and circular masking
                        icon_loaded = False
                        pixmap = None

                        # Try loading from PNG bytes first
                        if rep_thumb_png:
                            try:
                                from PySide6.QtCore import QByteArray
                                pixmap = QPixmap()
                                if pixmap.loadFromData(QByteArray(rep_thumb_png)):
                                    icon_loaded = True
                            except Exception as e:
                                print("[Sidebar] PNG icon load failed:", e)
                                pixmap = None

                        # Fall back to file path with EXIF correction
                        if not icon_loaded and rep_path and os.path.exists(rep_path):
                            try:
                                # BUG-C2 FIX: Use context manager to prevent resource leak
                                with Image.open(rep_path) as pil_image:
                                    pil_image = ImageOps.exif_transpose(pil_image)  # Auto-rotate based on EXIF

                                    # Convert PIL Image to QPixmap
                                    if pil_image.mode != 'RGB':
                                        pil_image = pil_image.convert('RGB')

                                    # Convert to bytes and load into QImage
                                    buffer = BytesIO()
                                    pil_image.save(buffer, format='PNG')
                                    image = QImage.fromData(buffer.getvalue())

                                if not image.isNull():
                                    pixmap = QPixmap.fromImage(image)
                                    icon_loaded = True
                            except Exception as e:
                                # Fallback to direct QPixmap loading without EXIF
                                try:
                                    pixmap = QPixmap(rep_path)
                                    if not pixmap.isNull():
                                        icon_loaded = True
                                except (RuntimeError, OSError):
                                    pass

                        # Apply circular masking and set icon (32x32 for tree view)
                        if icon_loaded and pixmap and not pixmap.isNull():
                            circular = make_circular_pixmap(pixmap, 32)
                            name_item.setIcon(QIcon(circular))

                        # Set mode + cluster ID (unified)
                        name_item.setData("people", Qt.UserRole)
                        name_item.setData(f"facecluster:{cluster_id}", Qt.UserRole + 1)

                        # Count item
                        count_item = QStandardItem(str(count) if count > 0 else "")
                        count_item.setEditable(False)
                        count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        count_item.setForeground(QColor("#888888"))

                        people_root.appendRow([name_item, count_item])

                    # Show total count on root
                    people_count_item.setText(str(total_faces))

                else:
                    # No clusters or no faces
                    status_item = QStandardItem("ℹ️ No faces detected")
                    status_item.setEditable(False)
                    status_item.setForeground(QColor("#888888"))
                    status_item.setData("people", Qt.UserRole)
                    people_root.appendRow([status_item, QStandardItem("")])


                # ---------------------------------------------------------
                # 📱 MOBILE DEVICES SECTION — Direct access to mounted phones
                # ---------------------------------------------------------
                try:
                    from services.device_sources import scan_mobile_devices

                    # Skip the actual device scan when auto-detection is disabled.
                    # The scan enumerates drives and COM devices, which is slow on
                    # systems with mapped drives, corporate policies, or VPNs.
                    if not getattr(self, '_device_auto_refresh_enabled', False):
                        mobile_devices = []
                    else:
                        # Use cached scan results (TTL 300s) — avoids heavy COM scan on every sidebar reload
                        mobile_devices = scan_mobile_devices(db=self.db, register_devices=True)
                    if mobile_devices:
                        print(f"[Sidebar] Device scan: {len(mobile_devices)} device(s) found")

                    # Update device count for auto-refresh tracking
                    self._last_device_count = len(mobile_devices)

                    # Always show Mobile Devices section (even if no devices found)
                    # This makes the feature discoverable and accessible for troubleshooting
                    devices_root = QStandardItem("📱 Mobile Devices")
                    devices_root.setEditable(False)
                    devices_root.setData("mobile_devices_root", Qt.UserRole)
                    devices_count_item = _make_count_item("")
                    self.model.appendRow([devices_root, devices_count_item])

                    if mobile_devices:
                        # Devices found - show them with history
                        total_device_photos = 0

                        for device in mobile_devices:
                            # Get device history from database
                            device_history = None
                            last_import_info = ""
                            status_icon = ""

                            if device.device_id:
                                try:
                                    # Get device info from database
                                    device_history = self.db.get_device(device.device_id)

                                    if device_history:
                                        # Format last import info
                                        last_import = device_history.get('last_seen')
                                        total_imports = device_history.get('total_imports', 0)
                                        photos_imported = device_history.get('total_photos_imported', 0)
                                        videos_imported = device_history.get('total_videos_imported', 0)

                                        # Calculate days since last import
                                        if last_import:
                                            from datetime import datetime
                                            try:
                                                last_seen_dt = datetime.fromisoformat(last_import)
                                                days_ago = (datetime.now() - last_seen_dt).days

                                                if days_ago == 0:
                                                    time_str = "today"
                                                elif days_ago == 1:
                                                    time_str = "yesterday"
                                                elif days_ago < 7:
                                                    time_str = f"{days_ago} days ago"
                                                elif days_ago < 30:
                                                    weeks = days_ago // 7
                                                    time_str = f"{weeks} week{'s' if weeks > 1 else ''} ago"
                                                else:
                                                    months = days_ago // 30
                                                    time_str = f"{months} month{'s' if months > 1 else ''} ago"

                                                # Build info string
                                                if total_imports > 0:
                                                    last_import_info = f"Last seen: {time_str}"
                                                    if photos_imported > 0 or videos_imported > 0:
                                                        last_import_info += f" • {photos_imported} photos"
                                                        if videos_imported > 0:
                                                            last_import_info += f", {videos_imported} videos"
                                                    # Status icon based on recency
                                                    if days_ago < 7:
                                                        status_icon = "🟢"  # Recently used
                                                    elif days_ago < 30:
                                                        status_icon = "🟡"  # Used this month
                                                    else:
                                                        status_icon = "⚪"  # Older
                                                else:
                                                    last_import_info = "Never imported from this device"
                                                    status_icon = "⚪"
                                            except Exception as e:
                                                print(f"[Sidebar] Error parsing date: {e}")

                                except Exception as e:
                                    print(f"[Sidebar] Error getting device history: {e}")

                            # Create device item with status icon
                            device_label = f"{status_icon} {device.label}" if status_icon else device.label
                            device_item = QStandardItem(device_label)
                            device_item.setEditable(False)
                            device_item.setData("device", Qt.UserRole)
                            device_item.setData(device.root_path, Qt.UserRole + 1)
                            device_item.setData(device.device_id, Qt.UserRole + 2)

                            # Set tooltip with device history
                            if last_import_info:
                                tooltip = f"{device.label}\n{last_import_info}"
                                if device.device_id:
                                    tooltip += f"\nDevice ID: {device.device_id}"
                                device_item.setToolTip(tooltip)
                            else:
                                # Basic tooltip without history
                                tooltip = f"{device.label}"
                                if device.device_id:
                                    tooltip += f"\nDevice ID: {device.device_id}"
                                    tooltip += "\nNo import history yet"
                                else:
                                    tooltip += "\nDevice ID not available"
                                device_item.setToolTip(tooltip)

                            # Count total photos across all folders on this device
                            device_photo_count = sum(folder.photo_count for folder in device.folders)
                            total_device_photos += device_photo_count

                            device_count_item = _make_count_item(device_photo_count if device_photo_count > 0 else "")
                            devices_root.appendRow([device_item, device_count_item])

                            # Add device folders as children
                            for folder in device.folders:
                                folder_name = f"  • {folder.name}"
                                folder_item = QStandardItem(folder_name)
                                folder_item.setEditable(False)
                                folder_item.setData("device_folder", Qt.UserRole)
                                folder_item.setData(folder.path, Qt.UserRole + 1)

                                folder_count_item = _make_count_item(folder.photo_count if folder.photo_count > 0 else "")
                                device_item.appendRow([folder_item, folder_count_item])

                            # Add deep scan button under device (after all folders)
                            deep_scan_item = QStandardItem("  🔍 Run Deep Scan...")
                            deep_scan_item.setEditable(False)
                            deep_scan_item.setForeground(QColor("#0066CC"))  # Blue color to indicate action
                            deep_scan_item.setData("device_deep_scan", Qt.UserRole)
                            deep_scan_item.setData(device.root_path, Qt.UserRole + 1)  # Store root path for scanning
                            deep_scan_item.setData(device.device_type, Qt.UserRole + 2)  # Store device type
                            deep_scan_item.setData(device, Qt.UserRole + 3)  # Store full device object
                            deep_scan_item.setToolTip("Recursively scan entire device for media folders\n(finds WhatsApp, Telegram, etc. in deep paths)")
                            device_item.appendRow([deep_scan_item, QStandardItem("")])

                        # Set total count on root
                        devices_count_item.setText(str(total_device_photos) if total_device_photos > 0 else "")
                        print(f"[Sidebar] Added Mobile Devices section with {len(mobile_devices)} devices, {total_device_photos} total photos")
                    else:
                        # No devices found - show helpful message
                        no_devices_item = QStandardItem("  No devices detected")
                        no_devices_item.setEditable(False)
                        no_devices_item.setForeground(QColor("#888888"))
                        no_devices_item.setData("no_devices", Qt.UserRole)
                        devices_root.appendRow([no_devices_item, QStandardItem("")])

                        help_item = QStandardItem("  → Right-click for help")
                        help_item.setEditable(False)
                        help_item.setForeground(QColor("#0066CC"))
                        help_item.setData("no_devices_help", Qt.UserRole)
                        devices_root.appendRow([help_item, QStandardItem("")])

                        print("[Sidebar] No mobile devices detected - added help message")

                except Exception as e:
                    print(f"[Sidebar] Failed to scan mobile devices: {e}")
                    import traceback
                    traceback.print_exc()

                    # Show error in sidebar
                    devices_root = QStandardItem("📱 Mobile Devices")
                    devices_root.setEditable(False)
                    devices_root.setData("mobile_devices_root", Qt.UserRole)
                    self.model.appendRow([devices_root, QStandardItem("")])

                    error_item = QStandardItem(f"  ⚠️ Scan failed: {str(e)[:50]}")
                    error_item.setEditable(False)
                    error_item.setForeground(QColor("#CC0000"))
                    error_item.setData("device_error", Qt.UserRole)
                    devices_root.appendRow([error_item, QStandardItem("")])

                    help_item = QStandardItem("  → Right-click for help")
                    help_item.setEditable(False)
                    help_item.setForeground(QColor("#0066CC"))
                    help_item.setData("no_devices_help", Qt.UserRole)
                    devices_root.appendRow([help_item, QStandardItem("")])

                # ---------------------------------------------------------
                # NEW POSITION: Build Tags AFTER Mobile Devices
                # ---------------------------------------------------------
                self._build_tag_section()

                for r in range(self.model.rowCount()):
                    idx = self.model.index(r, 0)
                    self.tree.expand(idx)

                # Force column width recalculation after building tree
                QTimer.singleShot(0, self._recalculate_columns)
            except Exception as e:
                QMessageBox.warning(self, "Load Error", f"Failed to build navigation:\n{e}")

            # populate branch counts asynchronously while folder counts are already set
            if self._count_targets:
                print(f"[Sidebar] starting async count population for {len(self._count_targets)} branch targets")
                self._async_populate_counts()

        finally:
            # Always reset flag even if exception occurs
            self._rebuilding_tree = False


    def _add_folder_items_async(self, parent_item, parent_id=None):
        # kept for folder-tab lazy usage if desired, but not used for tree-mode counts
        rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        for row in rows:
            name = row["name"]
            fid = row["id"]
            name_item = QStandardItem(f"📁 {name}")
            count_item = QStandardItem("")
            name_item.setEditable(False)
            count_item.setEditable(False)
            name_item.setData("folder", Qt.UserRole)
            name_item.setData(fid, Qt.UserRole + 1)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_item.setForeground(QColor("#888888"))
            parent_item.appendRow([name_item, count_item])
            # register for async, but tree-mode uses _add_folder_items synchronous
            self._count_targets.append(("folder", fid, name_item, count_item))
            self._add_folder_items_async(name_item, fid)


    def _apply_counts(self, results):  # with async_populate_counts_priorFix
        try:
            for name_item, count_item, cnt in results:
                try:
                    text = str(cnt) if cnt is not None else ""
                    if isinstance(count_item, QStandardItem):
                        try:
                            count_item.setText(text)
                        except Exception:
                            try:
                                idx = count_item.index()
                                if idx.isValid():
                                    self.model.setData(idx, text)
                            except Exception:
                                pass
                        continue
                    if count_item is not None and hasattr(count_item, "setText") and not isinstance(count_item, QStandardItem):
                        try:
                            count_item.setText(1, text)
                        except Exception:
                            pass
                        continue
                    if name_item is not None:
                        try:
                            if isinstance(name_item, QStandardItem):
                                idx = name_item.index()
                                if idx.isValid():
                                    sibling_idx = idx.sibling(idx.row(), 1)
                                    self.model.setData(sibling_idx, text)
                                    continue
                        except Exception:
                            pass
                        try:
                            if hasattr(name_item, "setText") and not isinstance(name_item, QStandardItem):
                                name_item.setText(1, text)
                                continue
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                self.tree.viewport().update()
            except Exception:
                pass

            # Recalculate columns after count updates
            QTimer.singleShot(0, self._recalculate_columns)

            print("[Sidebar][counts applied] updated UI with counts")
        except Exception:
            traceback.print_exc()


    def _async_populate_counts(self):
        targets = list(self._count_targets)
        if not targets:
            print("[Sidebar][counts] no targets to populate")
            return

        # Bump generation to invalidate any previous workers
        self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000
        current_gen = self._list_worker_gen

        # CRITICAL FIX: Extract only data (typ, key), NOT Qt objects, before passing to worker
        data_only = [(typ, key) for typ, key, name_item, count_item in targets]

        def worker():
            results = []
            try:
                print(f"[Sidebar][counts worker gen={current_gen}] running for {len(data_only)} targets...")
                # Work only with data, NO Qt objects in worker thread
                for typ, key in data_only:
                    try:
                        cnt = 0
                        if typ == "branch":
                            # DEBUG: Check if project_id is set
                            if self.project_id is None:
                                print(f"[Sidebar][counts worker] WARNING: project_id is None for branch '{key}'")
                            if hasattr(self.db, "count_images_by_branch"):
                                cnt = int(self.db.count_images_by_branch(self.project_id, key) or 0)
                            else:
                                rows = self.db.get_images_by_branch(self.project_id, key) or []
                                cnt = len(rows)
                            # DEBUG: Log count result for date branches
                            if key.startswith("by_date:"):
                                print(f"[Sidebar][counts worker] Date branch '{key}' has {cnt} photos")

                        elif typ == "folder":
                            # Use recursive count including all subfolders
                            if hasattr(self.db, "get_image_count_recursive"):
                                cnt = int(self.db.get_image_count_recursive(key) or 0)
                            elif hasattr(self.db, "count_for_folder"):
                                cnt = int(self.db.count_for_folder(key, project_id=self.project_id) or 0)
                            else:
                                with self.db._connect() as conn:
                                    cur = conn.cursor()
                                    cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (key,))
                                    v = cur.fetchone()
                                    cnt = int(v[0]) if v else 0

                        # IMPORTANT: Only pass data (typ, key, cnt), NOT Qt objects
                        results.append((typ, key, cnt))
                    except Exception:
                        traceback.print_exc()
                        results.append((typ, key, 0))
                print(f"[Sidebar][counts worker gen={current_gen}] finished scanning targets, scheduling UI update")
            except Exception:
                traceback.print_exc()
            # Schedule UI update in main thread with generation check
            # CRITICAL: Emit signal to safely post from worker thread to main thread
            try:
                self._countsReady.emit(results, current_gen)
            except Exception as e:
                print(f"[Sidebar][counts] Failed to emit counts update signal: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_counts_defensive(self, results, gen=None):
        """
        Apply counts to UI by finding QStandardItems in model by key.
        This method runs in the MAIN THREAD (called via QTimer.singleShot).

        Args:
            results: List of (typ, key, cnt) tuples from worker thread
            gen: Generation number to check if results are stale
        """
        # Check if this worker is stale
        if gen is not None and gen != self._list_worker_gen:
            print(f"[Sidebar][counts] Ignoring stale worker results (gen={gen}, current={self._list_worker_gen})")
            return

        # CRITICAL SAFETY: Check if model is detached (being rebuilt)
        # If model is not attached to tree view, skip update to prevent crashes
        try:
            if self.tree is None or self.model is None:
                print("[Sidebar][counts] Tree or model is None, skipping count update")
                return
            tree_model = self.tree.model()
        except (RuntimeError, AttributeError) as e:
            # RuntimeError: wrapped C/C++ object has been deleted
            # AttributeError: tree/model not available
            print(f"[Sidebar][counts] Tree/model not available (likely rebuilding): {e}")
            return
        if tree_model != self.model:
            print("[Sidebar][counts] Model is detached (rebuilding), skipping count update")
            return

        # Safety check: ensure model is valid before accessing
        try:
            if not self.model or self.model.rowCount() == 0:
                print("[Sidebar][counts] Model is empty or invalid, skipping count update")
                return
        except (RuntimeError, AttributeError) as e:
            print(f"[Sidebar][counts] Model access failed: {e}")
            return

        try:
            for typ, key, cnt in results:
                text = str(cnt) if cnt is not None else ""

                # Find the model item by key and update count column
                try:
                    found_name, found_count = self._find_model_item_by_key(key)

                    # Try updating count_item directly
                    if found_count is not None:
                        try:
                            found_count.setText(text)
                            continue
                        except Exception:
                            # Fallback: try model-level setData on its index
                            try:
                                idx = found_count.index()
                                if idx.isValid():
                                    self.model.setData(idx, text)
                                    continue
                            except Exception:
                                pass

                    # Fallback: if only found_name is present, set its sibling column
                    if found_name is not None:
                        try:
                            idx = found_name.index()
                            if idx.isValid():
                                sib = idx.sibling(idx.row(), 1)
                                self.model.setData(sib, text)
                                continue
                        except Exception:
                            pass

                except Exception:
                    traceback.print_exc()

            # Refresh view to show updated counts
            try:
                self.tree.viewport().update()
            except Exception:
                pass

            # Recalculate columns after count updates
            QTimer.singleShot(0, self._recalculate_columns)

            print("[Sidebar][counts applied] updated UI with counts")
        except Exception:
            traceback.print_exc()

    def _add_folder_items(self, parent_item, parent_id=None, _folder_counts=None):
        # CRITICAL FIX: Pass project_id to filter folders and counts by project
        try:
            rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        except Exception as e:
            print(f"[Sidebar] Error in get_child_folders: {e}")
            import traceback
            traceback.print_exc()
            rows = []

        # PERFORMANCE OPTIMIZATION: Get all folder counts in ONE query (only at root level)
        # This dramatically improves performance when there are many folders
        if _folder_counts is None and parent_id is None:
            # Root level call - get all counts at once to avoid N+1 queries
            if hasattr(self.db, "get_folder_counts_batch") and self.project_id:
                try:
                    _folder_counts = self.db.get_folder_counts_batch(self.project_id)
                    print(f"[Sidebar] Loaded {len(_folder_counts)} folder counts in batch (performance optimization)")
                except Exception as e:
                    print(f"[Sidebar] Error in get_folder_counts_batch: {e}")
                    import traceback
                    traceback.print_exc()
                    _folder_counts = {}
            else:
                _folder_counts = {}

        for row in rows:
            try:
                name = row["name"]
                fid = row["id"]

                # Get count from batch result (fast) or fall back to individual query (slow)
                if _folder_counts and fid in _folder_counts:
                    photo_count = _folder_counts[fid]
                elif hasattr(self.db, "get_image_count_recursive"):
                    # Fallback: Individual query (N+1 problem, but works if batch failed)
                    # CRITICAL FIX: Pass project_id to count only photos from this project
                    try:
                        photo_count = int(self.db.get_image_count_recursive(fid, project_id=self.project_id) or 0)
                    except Exception as e:
                        print(f"[Sidebar] Error in get_image_count_recursive for folder {fid}: {e}")
                        photo_count = 0
                else:
                    photo_count = self._get_photo_count(fid)

                name_item = QStandardItem(f"📁 {name}")
                count_item = QStandardItem(str(photo_count))
                count_item.setText(f"{photo_count:>5}")
                name_item.setEditable(False)
                count_item.setEditable(False)
                name_item.setData("folder", Qt.UserRole)
                name_item.setData(fid, Qt.UserRole + 1)
                count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                count_item.setForeground(QColor("#888888"))
                parent_item.appendRow([name_item, count_item])

                # Recursive call with error handling - pass counts down to avoid re-fetching
                self._add_folder_items(name_item, fid, _folder_counts)
            except Exception as e:
                print(f"[Sidebar] Error adding folder item: {e}")
                import traceback
                traceback.print_exc()
                continue


    def _build_by_date_section(self):
        from PySide6.QtGui import QStandardItem, QColor
        from PySide6.QtCore import Qt
        try:
            hier = self.db.get_date_hierarchy(project_id=self.project_id)
        except Exception:
            return
        if not hier or not isinstance(hier, dict):
            return

        root_name_item = QStandardItem("📅 By Date")
        root_cnt_item = QStandardItem("")
        for it in (root_name_item, root_cnt_item):
            it.setEditable(False)
        self.model.appendRow([root_name_item, root_cnt_item])

        def _cnt_item(num):
            c = QStandardItem("" if not num else str(num))
            c.setEditable(False)
            c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            c.setForeground(QColor("#888888"))
            return c

        # PERFORMANCE OPTIMIZATION: Get ALL date counts in ONE query instead of N individual queries
        # This eliminates the N+1 problem: 50+ queries → 1 query (8x speedup: 400ms → 50ms)
        date_counts = {'years': {}, 'months': {}, 'days': {}}
        if hasattr(self.db, 'get_date_counts_batch'):
            try:
                date_counts = self.db.get_date_counts_batch(self.project_id)
                print(f"[Sidebar] Loaded date counts in batch: {len(date_counts['years'])} years, {len(date_counts['months'])} months, {len(date_counts['days'])} days")
            except Exception as e:
                print(f"[Sidebar] Error in get_date_counts_batch (falling back to individual queries): {e}")

        for year in sorted(hier.keys(), key=lambda y: int(str(y))):
            # Get count from batch result (fast) or fall back to individual query (slow)
            if date_counts and year in date_counts['years']:
                y_count = date_counts['years'][year]
            else:
                try:
                    y_count = self.db.count_media_for_year(year, project_id=self.project_id)
                except Exception:
                    y_count = 0

            y_item = QStandardItem(str(year))
            y_item.setEditable(False)
            y_item.setData("branch", Qt.UserRole)
            y_item.setData(f"date:{year}", Qt.UserRole + 1)
            root_name_item.appendRow([y_item, _cnt_item(y_count)])

            months = hier.get(year, {})
            if not isinstance(months, dict):
                continue

            for month in sorted(months.keys(), key=lambda m: int(str(m))):
                m_label = f"{int(month):02d}"
                year_month_key = f"{year}-{m_label}"

                # Get count from batch result (fast) or fall back to individual query (slow)
                if date_counts and year_month_key in date_counts['months']:
                    m_count = date_counts['months'][year_month_key]
                else:
                    try:
                        m_count = self.db.count_media_for_month(year, month, project_id=self.project_id)
                    except Exception:
                        m_count = 0

                m_item = QStandardItem(m_label)
                m_item.setEditable(False)
                m_item.setData("branch", Qt.UserRole)
                m_item.setData(f"date:{year}-{m_label}", Qt.UserRole + 1)
                y_item.appendRow([m_item, _cnt_item(m_count)])

                day_ymd_list = months.get(month, []) or []
                day_numbers = []
                for ymd in day_ymd_list:
                    try:
                        dd = str(ymd).split("-")[2]
                        day_numbers.append(int(dd))
                    except Exception:
                        pass
                for day in sorted(set(day_numbers)):
                    d_label = f"{int(day):02d}"
                    ymd = f"{year}-{m_label}-{d_label}"

                    # Get count from batch result (fast) or fall back to individual query (slow)
                    if date_counts and ymd in date_counts['days']:
                        d_count = date_counts['days'][ymd]
                    else:
                        try:
                            d_count = self.db.count_media_for_day(ymd, project_id=self.project_id)
                        except Exception:
                            d_count = 0

                    d_item = QStandardItem(d_label)
                    d_item.setEditable(False)
                    d_item.setData("branch", Qt.UserRole)
                    d_item.setData(f"date:{ymd}", Qt.UserRole + 1)
                    m_item.appendRow([d_item, _cnt_item(d_count)])

    def _build_tag_section(self):
        try:
            tag_service = get_tag_service()
            tag_rows = tag_service.get_all_tags_with_counts(self.project_id)
        except Exception:
            tag_rows = []

        if not tag_rows:
            return

        root_name_item = QStandardItem("🏷️ Tags")
        root_count_item = QStandardItem("")
        root_name_item.setEditable(False)
        root_count_item.setEditable(False)
        self.model.appendRow([root_name_item, root_count_item])

        for tag_name, count in tag_rows:
            text = tag_name
            count_text = str(count) if count else ""

            name_item = QStandardItem(text)
            count_item = QStandardItem(count_text)
            name_item.setEditable(False)
            count_item.setEditable(False)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_item.setForeground(QColor("#888888"))

            name_item.setData("tag", Qt.UserRole)
            name_item.setData(tag_name, Qt.UserRole + 1)

            root_name_item.appendRow([name_item, count_item])

    
    def reload_tags_only(self):
        """
        Reload tags in both list mode (tree) and tabs mode.

        ARCHITECTURE: UI Layer → TagService → TagRepository → Database
        """
        try:
            # Use TagService for proper layered architecture
            tag_service = get_tag_service()
            # CRITICAL: Pass project_id to filter tags by current project (Schema v3.0.0)
            tag_rows = tag_service.get_all_tags_with_counts(self.project_id)
            print(f"[Sidebar] reload_tags_only → got {len(tag_rows)} tags for project_id={self.project_id}")
        except Exception as e:
            print(f"[Sidebar] reload_tags_only skipped: {e}")
            return

        # Update tree view (list mode)
        tag_root = self._find_root_item("🏷️ Tags")
        if tag_root is None:
            tag_root = QStandardItem("🏷️ Tags")
            count_col = QStandardItem("")
            tag_root.setEditable(False)
            count_col.setEditable(False)
            self.model.appendRow([tag_root, count_col])

        while tag_root.rowCount() > 0:
            tag_root.removeRow(0)

        for tag_name, count in tag_rows:
            name_item = QStandardItem(tag_name)
            cnt_item = QStandardItem(str(count) if count else "")
            name_item.setEditable(False)
            cnt_item.setEditable(False)
            cnt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cnt_item.setForeground(QColor("#888888"))

            name_item.setData("tag", Qt.UserRole)
            name_item.setData(tag_name, Qt.UserRole + 1)

            tag_root.appendRow([name_item, cnt_item])

        self.tree.expand(self.model.indexFromItem(tag_root))
        self.tree.viewport().update()

        # Also refresh tabs mode if it's active
        if hasattr(self, 'tabs_controller') and self.tabs_controller:
            mode = self._effective_display_mode()
            if mode == "tabs":
                # Refresh just the tags tab
                try:
                    if hasattr(self.tabs_controller, 'refresh_tab'):
                        self.tabs_controller.refresh_tab("tags")
                    else:
                        # Fallback: refresh all tabs
                        self.tabs_controller.refresh_all(force=True)
                except Exception as e:
                    print(f"[Sidebar] Failed to refresh tags tab: {e}")


    def _on_folder_selected(self, folder_id: int):
        if hasattr(self, "on_folder_selected") and callable(self.on_folder_selected):
            self.on_folder_selected(folder_id)

    def set_project(self, project_id: int):
        print(f"[SidebarQt] set_project({project_id}) called")
        self.project_id = project_id
        if self.tabs_controller is not None:
            self.tabs_controller.set_project(project_id)
        if self.accordion_controller is not None:
            self.accordion_controller.set_project(project_id)
        print(f"[SidebarQt] Calling reload() after setting project_id")
        self.reload()

    def _show_menu_1st(self, pos: QPoint):
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return
        index = index.sibling(index.row(), 0)
        item = self.model.itemFromIndex(index)
        if not item:
            return

        mode = item.data(Qt.UserRole)
        value = item.data(Qt.UserRole + 1)
        label = item.text().strip()
        db = self.db
        menu = QMenu(self)

        # 👥 Face cluster context menu (Rename person)
        if mode in ("facecluster", "people") and isinstance(value, str):
            branch_key = value
            # Extract current name from label (remove count if present)
            current_name = label.split("(")[0].strip() if "(" in label else label

            act_rename = menu.addAction(tr('context_menu.rename_person'))
            menu.addSeparator()
            act_export = menu.addAction(tr('context_menu.export_photos'))

            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_rename:
                from PySide6.QtWidgets import QInputDialog
                # Clear the input field if it's an "Unnamed #" label
                default_text = "" if current_name.startswith("Unnamed #") else current_name
                new_name, ok = QInputDialog.getText(self, "Rename Person", "Person name:", text=default_text)
                if ok and new_name.strip() and new_name.strip() != current_name:
                    try:
                        # Use the helper method from reference_db if available
                        if hasattr(db, 'rename_branch_display_name'):
                            db.rename_branch_display_name(self.project_id, branch_key, new_name.strip())
                        else:
                            # Fallback: direct SQL update
                            with db._connect() as conn:
                                conn.execute("""
                                    UPDATE branches
                                    SET display_name = ?
                                    WHERE project_id = ? AND branch_key = ?
                                """, (new_name.strip(), self.project_id, branch_key))
                                conn.execute("""
                                    UPDATE face_branch_reps
                                    SET label = ?
                                    WHERE project_id = ? AND branch_key = ?
                                """, (new_name.strip(), self.project_id, branch_key))
                                conn.commit()

                        # Reload sidebar to show new name
                        self.reload()
                        QMessageBox.information(self, "Renamed", f"Person renamed to '{new_name.strip()}'")
                    except Exception as e:
                        QMessageBox.critical(self, "Rename Failed", str(e))
            elif chosen is act_export:
                self._do_export(branch_key)
            return

        if mode == "tag" and isinstance(value, str):
            tag_name = value
            act_filter = menu.addAction(f"Filter by tag: {tag_name}")
            menu.addSeparator()
            act_rename = menu.addAction(tr('context_menu.rename_tag'))
            act_delete = menu.addAction(tr('context_menu.delete_tag'))

            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_filter:
                if hasattr(self.parent(), "_apply_tag_filter"):
                    self.parent()._apply_tag_filter(tag_name)
            elif chosen is act_rename:
                from PySide6.QtWidgets import QInputDialog
                new_name, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=tag_name)
                if ok and new_name.strip() and new_name.strip() != tag_name:
                    try:
                        tag_service = get_tag_service()
                        tag_service.rename_tag(tag_name, new_name.strip(), self.project_id)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Rename Failed", str(e))
            elif chosen is act_delete:
                ret = QMessageBox.question(self, "Delete Tag",
                                           f"Delete tag '{tag_name}'?\nThis will unassign it from all photos.",
                                           QMessageBox.Yes | QMessageBox.No)
                if ret == QMessageBox.Yes:
                    try:
                        tag_service = get_tag_service()
                        tag_service.delete_tag(tag_name, self.project_id)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Delete Failed", str(e))
            return

        if label.startswith("🏷️ Tags"):
            act_new = menu.addAction("➕ New Tag…")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_new:
                from PySide6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
                if ok and name.strip():
                    try:
                        tag_service = get_tag_service()
                        tag_service.ensure_tag_exists(name.strip(), self.project_id)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Create Failed", str(e))
            return

        act_export = menu.addAction("📁 Export Photos to Folder…")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_export:
            self._do_export(item.data(Qt.UserRole + 1))

    def _show_empty_area_menu(self, global_pos: QPoint):
        """
        Show context menu when right-clicking on empty area of sidebar.
        Provides general actions like refresh and troubleshooting.
        """
        menu = QMenu(self)

        act_refresh_all = menu.addAction(tr('context_menu.refresh_all'))
        act_refresh_all.setToolTip("Reload folders, tags, people, and scan for mobile devices")

        act_refresh_devices = menu.addAction(tr('context_menu.refresh_devices'))
        act_refresh_devices.setToolTip("Scan for newly connected mobile devices")

        menu.addSeparator()

        act_help_devices = menu.addAction(tr('context_menu.device_troubleshooting'))
        act_help_devices.setToolTip("Help with connecting Android/iOS devices")

        chosen = menu.exec(global_pos)

        if chosen == act_refresh_all:
            # Full refresh
            self._start_spinner()
            self.reload()
            QTimer.singleShot(150, self._stop_spinner)
            self.window().statusBar().showMessage("✓ Refreshed sidebar and devices")

        elif chosen == act_refresh_devices:
            # Device-specific refresh
            self._start_spinner()
            self.reload()
            QTimer.singleShot(150, self._stop_spinner)
            self.window().statusBar().showMessage("✓ Scanned for mobile devices")

        elif chosen == act_help_devices:
            # Show troubleshooting dialog
            self._show_device_troubleshooting()

    def _show_menu(self, pos: QPoint):
        # Always convert local click position to global screen position
        global_pos = self.tree.viewport().mapToGlobal(pos)

        index = self.tree.indexAt(pos)

        # Handle click on empty area - show general menu
        if not index.isValid():
            self._show_empty_area_menu(global_pos)
            return

        index = index.sibling(index.row(), 0)
        item = self.model.itemFromIndex(index)
        if not item:
            return

        mode = item.data(Qt.UserRole)
        value = item.data(Qt.UserRole + 1)
        label = item.text().strip()
        db = self.db
        menu = QMenu(self)

        # Mobile Devices root context menu
        if mode == "mobile_devices_root":
            act_refresh = menu.addAction(tr('context_menu.refresh_devices'))
            act_help = menu.addAction(tr('context_menu.device_troubleshooting'))

            chosen = menu.exec(global_pos)
            if chosen == act_refresh:
                self.reload()
            elif chosen == act_help:
                self._show_device_troubleshooting()
            return

        # Help/error items in Mobile Devices section
        if mode in ("no_devices", "no_devices_help", "device_error"):
            act_refresh = menu.addAction(tr('context_menu.scan_devices'))
            act_help = menu.addAction(tr('context_menu.device_troubleshooting'))

            chosen = menu.exec(global_pos)
            if chosen == act_refresh:
                self.reload()
            elif chosen == act_help:
                self._show_device_troubleshooting()
            return

        # Face cluster context menu (People)
        if mode in ("facecluster", "people"):
            branch_key = value
            if isinstance(branch_key, str) and branch_key.startswith("facecluster:"):
                branch_key = branch_key.split(":", 1)[1]

            menu.addSeparator()
            rename_action = menu.addAction("Rename person…")
            export_action = menu.addAction("Export photos of this person…")

            # NEW: batch-merge, suggestions, undo
            merge_action = menu.addAction("Merge selected into…")
            suggest_action = menu.addAction("💡 Smart merge suggestions…")
            undo_action = menu.addAction("Undo last face merge")

            chosen = menu.exec(global_pos)
            if chosen == rename_action:
                self._rename_face_cluster(branch_key, item.text())
            elif chosen == export_action:
                self._export_face_cluster_photos(branch_key, item.text())
            elif chosen == merge_action:
                self._merge_selected_people_clusters()
            elif chosen == suggest_action:
                self._show_face_merge_suggestions()
            elif chosen == undo_action:
                self._undo_last_face_merge()
            return

        if mode == "tag" and isinstance(value, str):
            tag_name = value
            act_filter = menu.addAction(f"Filter by tag: {tag_name}")
            menu.addSeparator()
            act_rename = menu.addAction(tr('context_menu.rename_tag'))
            act_delete = menu.addAction(tr('context_menu.delete_tag'))

            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_filter:
                if hasattr(self.parent(), "_apply_tag_filter"):
                    self.parent()._apply_tag_filter(tag_name)
            elif chosen is act_rename:
                from PySide6.QtWidgets import QInputDialog
                new_name, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=tag_name)
                if ok and new_name.strip() and new_name.strip() != tag_name:
                    try:
                        tag_service = get_tag_service()
                        tag_service.rename_tag(tag_name, new_name.strip(), self.project_id)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Rename Failed", str(e))
            elif chosen is act_delete:
                ret = QMessageBox.question(self, "Delete Tag",
                                           f"Delete tag '{tag_name}'?\nThis will unassign it from all photos.",
                                           QMessageBox.Yes | QMessageBox.No)
                if ret == QMessageBox.Yes:
                    try:
                        tag_service = get_tag_service()
                        tag_service.delete_tag(tag_name, self.project_id)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Delete Failed", str(e))
            return

        if label.startswith("🏷️ Tags"):
            act_new = menu.addAction("➕ New Tag…")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_new:
                from PySide6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
                if ok and name.strip():
                    try:
                        tag_service = get_tag_service()
                        tag_service.ensure_tag_exists(name.strip(), self.project_id)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Create Failed", str(e))
            return

        # Mobile device context menu
        if mode == "device_folder" and isinstance(value, str):
            device_folder_path = value

            # Get device_id from parent device item (Phase 4)
            device_id = None
            device_root_path = None
            parent_item = item.parent()
            if parent_item:
                device_id = parent_item.data(Qt.UserRole + 2)
                device_root_path = parent_item.data(Qt.UserRole + 1)

            act_import = menu.addAction("📥 Import from this folder…")

            # Phase 4B: Add quick import button if device_id available
            act_quick_import = None
            if device_id:
                act_quick_import = menu.addAction("⚡ Import New Files (Quick)")
                act_quick_import.setToolTip("Import only new files with smart defaults")

            act_browse = menu.addAction("👁️ Browse (view only)")
            act_refresh = menu.addAction("🔄 Refresh device")
            menu.addSeparator()
            act_help = menu.addAction("❓ Device Troubleshooting...")

            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_import:
                self._import_from_device_folder(device_folder_path, device_id, device_root_path)
            elif act_quick_import and chosen is act_quick_import:
                # Phase 4B: Quick import
                self._quick_import_from_device(device_folder_path, device_root_path, device_id)
            elif chosen is act_browse:
                # Browse without importing (existing behavior)
                index = self.tree.currentIndex()
                if index.isValid():
                    self._on_item_clicked(index)
            elif chosen is act_refresh:
                self.reload()
            elif chosen == act_help:
                self._show_device_troubleshooting()
            return

        if mode == "device" and isinstance(value, str):
            device_root_path = value
            device_id = item.data(Qt.UserRole + 2)

            act_scan = menu.addAction("📱 Scan device for photos…")

            # Phase 4B: Add quick import button if device_id available
            act_quick_import = None
            if device_id:
                act_quick_import = menu.addAction("⚡ Import New Files (Quick)")
                act_quick_import.setToolTip("Import only new files from all folders")

            menu.addSeparator()

            # Phase 4B: Add auto-import toggle
            act_auto_import = None
            if device_id:
                # Check current auto-import status
                auto_import_status = self.db.get_device_auto_import_status(device_id)
                if auto_import_status.get('enabled'):
                    act_auto_import = menu.addAction("✓ Disable Auto-Import")
                    act_auto_import.setToolTip(f"Currently auto-importing from: {auto_import_status.get('folder', 'Camera')}")
                else:
                    act_auto_import = menu.addAction("⚙️ Enable Auto-Import…")
                    act_auto_import.setToolTip("Configure this device to auto-import")

            menu.addSeparator()
            act_refresh = menu.addAction("🔄 Refresh device list")
            act_help = menu.addAction("❓ Device Troubleshooting...")

            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_scan:
                # Show import dialog for entire device
                self._import_from_device_folder(device_root_path, device_id, device_root_path)
            elif act_quick_import and chosen is act_quick_import:
                # Phase 4B: Quick import from device root
                self._quick_import_from_device(device_root_path, device_root_path, device_id)
            elif act_auto_import and chosen is act_auto_import:
                # Phase 4B: Toggle auto-import
                self._toggle_auto_import(device_id, auto_import_status.get('enabled'))
            elif chosen is act_refresh:
                self.reload()
            elif chosen == act_help:
                self._show_device_troubleshooting()
            return

        # Folder context menu
        if mode == "folder" and value:
            folder_id = value
            act_view = menu.addAction("📂 View Photos")
            menu.addSeparator()
            act_extract_embeddings = menu.addAction("🧠 Extract Embeddings for Folder")
            act_extract_embeddings.setToolTip("Generate AI embeddings for photos in this folder")
            act_show_stats = menu.addAction("📊 Embedding Statistics")
            menu.addSeparator()
            act_export = menu.addAction("📁 Export Photos to Folder…")

            chosen = menu.exec(global_pos)
            if chosen == act_view:
                mw = self.window()
                if hasattr(mw, 'grid'):
                    mw.grid.set_context("folder", folder_id)
            elif chosen == act_extract_embeddings:
                self._extract_embeddings_for_folder(folder_id)
            elif chosen == act_show_stats:
                self._show_folder_embedding_stats(folder_id)
            elif chosen == act_export:
                self._do_export(folder_id)
            return

        # Branch (project) context menu
        if mode == "branch" and value:
            branch_key = value
            act_view = menu.addAction("📂 View Photos")
            menu.addSeparator()
            act_extract_embeddings = menu.addAction("🧠 Extract All Embeddings")
            act_extract_embeddings.setToolTip("Generate AI embeddings for all photos in this project")
            act_show_stats = menu.addAction("📊 Embedding Statistics Dashboard")
            act_migrate_float16 = menu.addAction("⚡ Migrate to Float16")
            act_migrate_float16.setToolTip("Convert embeddings to half-precision (50% space savings)")
            menu.addSeparator()
            act_export = menu.addAction("📁 Export Photos to Folder…")

            chosen = menu.exec(global_pos)
            if chosen == act_view:
                mw = self.window()
                if hasattr(mw, 'grid'):
                    mw.grid.set_context("branch", branch_key)
            elif chosen == act_extract_embeddings:
                self._extract_embeddings_for_project()
            elif chosen == act_show_stats:
                self._show_project_embedding_dashboard()
            elif chosen == act_migrate_float16:
                self._migrate_embeddings_to_float16()
            elif chosen == act_export:
                self._do_export(branch_key)
            return

        act_export = menu.addAction("📁 Export Photos to Folder…")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_export:
            self._do_export(item.data(Qt.UserRole + 1))



    # --------------------------------------------------
    # MOBILE DEVICE IMPORT
    # --------------------------------------------------

    def _import_from_device_folder(self, device_folder_path: str, device_id: str = None, root_path: str = None):
        """
        Show import dialog for device folder (Phase 4: Enhanced with device tracking).

        Args:
            device_folder_path: Path to device folder
            device_id: Device identifier (Phase 4, optional)
            root_path: Device root path (Phase 4, optional)
        """
        try:
            from ui.device_import_dialog import DeviceImportDialog

            # Show import dialog (Phase 4: Pass device_id and root_path)
            dialog = DeviceImportDialog(
                self.db,
                self.project_id,
                device_folder_path,
                parent=self,
                device_id=device_id,
                root_path=root_path
            )

            # If import successful, reload sidebar
            if dialog.exec():
                print(f"[Sidebar] Import completed, reloading...")
                self.reload()

                # Notify main window to refresh grid
                mw = self.window()
                if hasattr(mw, 'grid') and hasattr(mw.grid, 'reload'):
                    mw.grid.reload()

                mw.statusBar().showMessage("✓ Import completed successfully")

        except Exception as e:
            print(f"[Sidebar] Import dialog error: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Import Error",
                f"Failed to show import dialog:\n{e}"
            )

    def _quick_import_from_device(self, device_folder_path: str, root_path: str, device_id: str):
        """
        Quick import: Import only new files with smart defaults (Phase 4B).

        Uses DeviceImportService.quick_import_new_files() to:
        - Scan incrementally (new files only)
        - Skip cross-device duplicates
        - Import without showing dialog
        - Show toast notification when complete

        Args:
            device_folder_path: Path to device folder to import from
            root_path: Device root path
            device_id: Device identifier
        """
        try:
            from services.device_import_service import DeviceImportService
            from PySide6.QtWidgets import QProgressDialog
            from PySide6.QtCore import Qt

            # Create progress dialog
            progress = QProgressDialog("Scanning device for new files...", "Cancel", 0, 0, self)
            progress.setWindowTitle("Quick Import")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.show()

            # Create import service
            service = DeviceImportService(self.db, self.project_id, device_id=device_id)

            try:
                # Run quick import
                print(f"[Sidebar] Starting quick import from {device_folder_path}")
                stats = service.quick_import_new_files(
                    device_folder_path,
                    root_path,
                    skip_cross_device_duplicates=True
                )

                progress.close()

                # Show results
                imported = stats.get('imported', 0)
                skipped = stats.get('skipped', 0)
                failed = stats.get('failed', 0)

                if imported > 0:
                    # Success - show toast notification
                    message = f"✓ Imported {imported} new photo(s)"
                    if skipped > 0:
                        message += f"\nSkipped {skipped} duplicate(s)"

                    QMessageBox.information(self, "Quick Import Complete", message)

                    # Reload sidebar and grid
                    self.reload()
                    mw = self.window()
                    if hasattr(mw, 'grid') and hasattr(mw.grid, 'reload'):
                        mw.grid.reload()
                    mw.statusBar().showMessage(f"✓ Quick import: {imported} photos imported")

                elif skipped > 0:
                    QMessageBox.information(
                        self,
                        "No New Files",
                        f"No new files to import.\n{skipped} file(s) already imported or duplicates."
                    )
                else:
                    QMessageBox.information(
                        self,
                        "No New Files",
                        "No new files found on device."
                    )

            except Exception as e:
                progress.close()
                print(f"[Sidebar] Quick import failed: {e}")
                import traceback
                traceback.print_exc()
                QMessageBox.critical(
                    self,
                    "Quick Import Failed",
                    f"Failed to import files:\n{e}"
                )

        except Exception as e:
            print(f"[Sidebar] Quick import error: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Quick Import Error",
                f"Failed to start quick import:\n{e}"
            )

    def _toggle_auto_import(self, device_id: str, currently_enabled: bool):
        """
        Toggle auto-import for a device (Phase 4B).

        Args:
            device_id: Device identifier
            currently_enabled: Whether auto-import is currently enabled
        """
        try:
            if currently_enabled:
                # Disable auto-import
                reply = QMessageBox.question(
                    self,
                    "Disable Auto-Import",
                    "Disable auto-import for this device?\n\n"
                    "You will need to manually import files.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    self.db.set_device_auto_import(device_id, enabled=False)
                    QMessageBox.information(
                        self,
                        "Auto-Import Disabled",
                        "Auto-import has been disabled for this device."
                    )
                    # Reload to update menu
                    self.reload()
            else:
                # Enable auto-import - ask which folder
                from PySide6.QtWidgets import QInputDialog

                folders = ["Camera", "DCIM", "All Folders", "Custom..."]
                folder, ok = QInputDialog.getItem(
                    self,
                    "Enable Auto-Import",
                    "Which folder should be auto-imported?",
                    folders,
                    0,
                    False
                )

                if ok and folder:
                    if folder == "Custom...":
                        folder, ok = QInputDialog.getText(
                            self,
                            "Custom Folder",
                            "Enter folder name:"
                        )
                        if not ok or not folder:
                            return

                    self.db.set_device_auto_import(device_id, enabled=True, folder=folder)
                    QMessageBox.information(
                        self,
                        "Auto-Import Enabled",
                        f"Auto-import enabled for folder: {folder}\n\n"
                        f"Note: Auto-import currently requires manual trigger.\n"
                        f"Use 'Import New Files' to quickly import."
                    )
                    # Reload to update menu
                    self.reload()

        except Exception as e:
            print(f"[Sidebar] Auto-import toggle error: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Auto-Import Error",
                f"Failed to toggle auto-import:\n{e}"
            )

    def _show_device_troubleshooting(self):
        """
        Show platform-specific troubleshooting guide for mobile device detection.
        """
        import platform

        system = platform.system()

        # Build help text based on platform
        help_text = """<h2>📱 Mobile Device Troubleshooting</h2>"""

        # Current device detection status
        try:
            from services.device_sources import scan_mobile_devices
            devices = scan_mobile_devices(db=self.db, register_devices=True)
            if devices:
                help_text += f"<p><b>✓ Currently detected:</b> {len(devices)} device(s)</p>"
                for dev in devices:
                    help_text += f"<p style='margin-left: 20px;'>• {dev.label} ({len(dev.folders)} folders)</p>"
            else:
                help_text += "<p><b>⚠️ No devices currently detected</b></p>"
        except Exception as e:
            help_text += f"<p><b>❌ Error scanning:</b> {e}</p>"

        help_text += "<hr>"

        # Android instructions
        help_text += """
        <h3>🤖 For Android Devices:</h3>
        <ol>
            <li><b>Connect</b> your device via USB cable</li>
            <li><b>Swipe down</b> the notification panel on your phone</li>
            <li><b>Tap</b> the "USB" notification</li>
            <li><b>Select</b> "File Transfer" or "MTP" mode (NOT "Charging Only")</li>
        """

        if system == "Linux":
            help_text += """
            <li><b>Linux specific:</b> Install MTP tools if not already installed:
                <pre style='background: #f0f0f0; padding: 5px;'>sudo apt install mtp-tools libmtp-common libmtp-runtime</pre>
            </li>
        """

        help_text += """
            <li><b>Verify:</b> Open your file manager and check if device appears</li>
            <li><b>Refresh:</b> Click the refresh button in sidebar or right-click → "Refresh Devices"</li>
        </ol>
        """

        # iOS instructions
        help_text += """
        <h3>🍎 For iOS Devices (iPhone/iPad):</h3>
        <ol>
            <li><b>Connect</b> your device via USB cable</li>
            <li><b>Unlock</b> your iPhone/iPad</li>
            <li><b>Tap</b> "Trust This Computer" when prompted</li>
            <li><b>Enter</b> device passcode if asked</li>
        """

        if system == "Linux":
            help_text += """
            <li><b>Linux specific:</b> Install iOS tools:
                <pre style='background: #f0f0f0; padding: 5px;'>sudo apt install libimobiledevice-utils ifuse</pre>
            </li>
            <li><b>Pair device:</b> Run in terminal:
                <pre style='background: #f0f0f0; padding: 5px;'>idevicepair pair</pre>
            </li>
        """
        elif system == "Windows":
            help_text += """
            <li><b>Windows specific:</b> Install iTunes or Apple Mobile Device Support from Apple's website</li>
        """

        help_text += """
            <li><b>Verify:</b> Device should appear in file manager</li>
            <li><b>Refresh:</b> Click refresh button in sidebar</li>
        </ol>
        """

        # SD Cards
        help_text += """
        <h3>💾 For SD Cards:</h3>
        <ol>
            <li><b>Insert</b> SD card into card reader</li>
            <li><b>Connect</b> card reader to computer</li>
            <li><b>Wait</b> for auto-mount (usually automatic)</li>
            <li><b>Verify:</b> Card should have a DCIM folder</li>
            <li><b>Refresh:</b> Click refresh button in sidebar</li>
        </ol>
        """

        # Troubleshooting tips
        help_text += """
        <hr>
        <h3>🔍 Still Not Working?</h3>
        <ol>
            <li><b>Check file manager:</b> If you can't see your device in your system's file manager (Finder/Explorer/Nautilus),
               the operating system hasn't mounted it yet. Fix OS-level mounting first.</li>
            <li><b>Try different USB port/cable:</b> Some cables are charge-only</li>
            <li><b>Restart devices:</b> Unplug device, restart app, reconnect device</li>
            <li><b>Check DCIM folder:</b> Device must have a DCIM folder to be detected</li>
        """

        if system == "Linux":
            help_text += """
            <li><b>Check permissions:</b> Add yourself to plugdev group:
                <pre style='background: #f0f0f0; padding: 5px;'>sudo usermod -a -G plugdev $USER</pre>
                Then log out and back in.
            </li>
            <li><b>Check device in terminal:</b> For Android, run: <code>mtp-detect</code></li>
        """

        help_text += """
            <li><b>Run diagnostic tool:</b> From terminal in app directory:
                <pre style='background: #f0f0f0; padding: 5px;'>python debug_device_detection.py</pre>
            </li>
        </ol>
        """

        # Common mount locations
        help_text += f"""
        <hr>
        <h3>📂 Where App Looks for Devices:</h3>
        """

        if system == "Windows":
            help_text += "<p><b>Windows:</b> Drive letters D: through Z:</p>"
        elif system == "Darwin":
            help_text += "<p><b>macOS:</b> /Volumes/</p>"
        elif system == "Linux":
            help_text += """
            <p><b>Linux:</b></p>
            <ul>
                <li>/media/ and /media/$USER/</li>
                <li>/mnt/</li>
                <li>/run/media/ and /run/media/$USER/</li>
            </ul>
            """

        help_text += """
        <hr>
        <p><b>For more details,</b> see MOBILE_DEVICE_GUIDE.md in the app directory.</p>
        """

        # Show dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Mobile Device Troubleshooting")
        dialog.setMinimumWidth(700)
        dialog.setMinimumHeight(600)

        layout = QVBoxLayout(dialog)

        # Scrollable text area
        text_browser = QTextBrowser()
        text_browser.setHtml(help_text)
        text_browser.setOpenExternalLinks(True)
        layout.addWidget(text_browser)

        # Buttons
        button_box = QHBoxLayout()

        btn_run_diagnostic = QPushButton("🔍 Run Diagnostic Tool")
        btn_run_diagnostic.setToolTip("Opens terminal with diagnostic script")
        btn_run_diagnostic.clicked.connect(lambda: self._run_diagnostic_tool())

        btn_refresh = QPushButton("🔄 Refresh Devices Now")
        btn_refresh.clicked.connect(lambda: (dialog.accept(), self.reload()))

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)

        button_box.addWidget(btn_run_diagnostic)
        button_box.addWidget(btn_refresh)
        button_box.addStretch()
        button_box.addWidget(btn_close)

        layout.addLayout(button_box)

        dialog.exec()

    def _run_diagnostic_tool(self):
        """
        Open terminal and run the diagnostic script.
        """
        import os
        import platform

        # Get script path
        script_path = os.path.join(os.path.dirname(__file__), "debug_device_detection.py")

        if not os.path.exists(script_path):
            QMessageBox.warning(
                self,
                "Diagnostic Tool Not Found",
                f"Could not find debug_device_detection.py\n\nExpected location:\n{script_path}"
            )
            return

        system = platform.system()

        try:
            if system == "Windows":
                # Windows: Open cmd and run script
                os.system(f'start cmd /k python "{script_path}"')
            elif system == "Darwin":
                # macOS: Open Terminal.app and run script
                os.system(f'open -a Terminal.app "{script_path}"')
            elif system == "Linux":
                # Linux: Try common terminal emulators
                terminals = ["gnome-terminal", "konsole", "xterm", "x-terminal-emulator"]
                for term in terminals:
                    if os.system(f"which {term} > /dev/null 2>&1") == 0:
                        os.system(f'{term} -e "python {script_path}; read -p \'Press Enter to close...\'" &')
                        break
            else:
                QMessageBox.information(
                    self,
                    "Manual Run Required",
                    f"Please run manually from terminal:\n\npython {script_path}"
                )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Failed to Open Terminal",
                f"Could not open terminal automatically.\n\nPlease run manually:\n\npython {script_path}\n\nError: {e}"
            )

    # --------------------------------------------------
    # PEOPLE / FACE CLUSTER MERGE HELPERS
    # --------------------------------------------------

    def _rename_face_cluster_1st(self, branch_key: str, current_label: str):
        """
        Rename a face cluster / person.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        # Extract current name from label (remove count if present)
        current_name = current_label.split("(")[0].strip() if "(" in current_label else current_label

        # Clear the input field if it's an "Unnamed #" label
        default_text = "" if current_name.startswith("Unnamed #") else current_name
        new_name, ok = QInputDialog.getText(self, "Rename Person", "Person name:", text=default_text)

        if not ok or not new_name.strip() or new_name.strip() == current_name:
            return

        try:
            # Use the helper method from reference_db if available
            if hasattr(self.db, 'rename_branch_display_name'):
                self.db.rename_branch_display_name(self.project_id, branch_key, new_name.strip())
            else:
                # Fallback: direct SQL update
                with self.db._connect() as conn:
                    conn.execute("""
                        UPDATE branches
                        SET display_name = ?
                        WHERE project_id = ? AND branch_key = ?
                    """, (new_name.strip(), self.project_id, branch_key))
                    conn.execute("""
                        UPDATE face_branch_reps
                        SET label = ?
                        WHERE project_id = ? AND branch_key = ?
                    """, (new_name.strip(), self.project_id, branch_key))
                    conn.commit()

            # Reload sidebar to show new name
            self._build_tree_model()
            QMessageBox.information(self, "Renamed", f"Person renamed to '{new_name.strip()}'")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Rename Failed", str(e))


    def _export_face_cluster_photos(self, branch_key: str, label: str):
        """
        Export all photos containing faces from this cluster.
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        dest = QFileDialog.getExistingDirectory(self, f"Export photos of: {label}")
        if not dest:
            return

        try:
            # Get all image paths for this face cluster
            if hasattr(self.db, 'get_images_by_branch'):
                paths = self.db.get_images_by_branch(self.project_id, branch_key) or []
            else:
                paths = []

            if not paths:
                QMessageBox.information(self, "Export", "No photos found for this person.")
                return

            # Copy photos to destination
            import shutil
            import os
            copied = 0
            for src_path in paths:
                if not os.path.exists(src_path):
                    continue
                filename = os.path.basename(src_path)
                dest_path = os.path.join(dest, filename)

                # Handle duplicate filenames
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(dest_path):
                        dest_path = os.path.join(dest, f"{base}_{counter}{ext}")
                        counter += 1

                shutil.copy2(src_path, dest_path)
                copied += 1

            QMessageBox.information(self, "Export Completed",
                                  f"Exported {copied} photos from '{label}' to:\n{dest}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Export Failed", str(e))


    def _collect_selected_people_clusters(self):
        """
        Return a list of (branch_key, display_name) for all selected
        tree rows that are 'people' items.
        """
        result = []
        sel_model = self.tree.selectionModel()
        if not sel_model:
            return result

        # We only care about the first column
        selected_rows = sel_model.selectedRows(0)
        for idx in selected_rows:
            item = self.model.itemFromIndex(idx.sibling(idx.row(), 0))
            if not item:
                continue
            mode = item.data(Qt.UserRole)
            value = item.data(Qt.UserRole + 1)
            if mode != "people" or not value:
                continue

            branch_key = value
            if isinstance(branch_key, str) and branch_key.startswith("facecluster:"):
                branch_key = branch_key.split(":", 1)[1]

            result.append((str(branch_key), item.text()))

        # Deduplicate by branch_key, preserving first label
        seen = set()
        final = []
        for key, label in result:
            if key in seen:
                continue
            seen.add(key)
            final.append((key, label))
        return final


    def _merge_selected_people_clusters(self):
        """
        Batch-merge mode:
          1. User selects multiple people in the tree
          2. Right-click → 'Merge selected into…'
          3. Choose target person
          4. Confirm preview
          5. Call DB merge (with undo snapshot)
        """
        from PySide6.QtWidgets import QInputDialog

        clusters = self._collect_selected_people_clusters()
        if len(clusters) < 2:
            QMessageBox.information(
                self,
                "Merge People",
                "Select at least two people in the list, then choose\n"
                "‘Merge selected into…’ from the context menu.",
            )
            return

        # Build label list for the target picker
        label_list = [f"{name}   [{key}]" for key, name in clusters]

        target_label, ok = QInputDialog.getItem(
            self,
            "Merge into…",
            "Choose the person to merge *into*:",
            label_list,
            0,
            False,
        )
        if not ok or not target_label:
            return

        try:
            target_index = label_list.index(target_label)
        except ValueError:
            return

        target_key, target_name = clusters[target_index]
        source_keys = [key for i, (key, _) in enumerate(clusters) if i != target_index]

        # --- Safety preview / confirmation ---
        try:
            cluster_rows = {
                row["branch_key"]: row
                for row in self.db.get_face_clusters(self.project_id)
            }
        except Exception:
            cluster_rows = {}

        total_faces = 0
        for key in [target_key] + source_keys:
            row = cluster_rows.get(key)
            if row:
                count_raw = row.get("member_count", 0) or 0
                # CRITICAL FIX: Handle bytes/int/str from database
                if isinstance(count_raw, (int, float)):
                    total_faces += int(count_raw)
                elif isinstance(count_raw, bytes):
                    try:
                        total_faces += int.from_bytes(count_raw, byteorder='little')
                    except (ValueError, OverflowError):
                        pass
                elif isinstance(count_raw, str):
                    try:
                        total_faces += int(count_raw)
                    except ValueError:
                        pass

        lines = [
            f"Target: {target_name} [{target_key}]",
            "",
            "Sources to merge:",
        ]
        for key, name in clusters:
            if key == target_key:
                continue
            row = cluster_rows.get(key) if cluster_rows else None
            cnt = (row.get("member_count") if row else None) or ""
            if cnt != "":
                lines.append(f"  • {name} [{key}]  ({cnt} faces)")
            else:
                lines.append(f"  • {name} [{key}]")

        if total_faces:
            lines.append("")
            lines.append(f"Approx. faces affected: {total_faces}")

        lines.append("")
        lines.append("You can undo this once via “Undo last face merge”.")

        confirm = QMessageBox.question(
            self,
            "Confirm merge",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        # --- Perform merge via DB ---
        try:
            stats = self.db.merge_face_clusters(self.project_id, target_key, source_keys)

            # Build comprehensive merge notification following Google Photos pattern
            msg_lines = [f"✓ Merged {len(source_keys)} people into '{target_name}'"]
            msg_lines.append("")  # Blank line

            # Show duplicate detection if any found
            duplicates = stats.get("duplicates_found", 0) if isinstance(stats, dict) else 0
            unique_moved = stats.get("unique_moved", 0) if isinstance(stats, dict) else 0
            total_photos = stats.get("total_photos", 0) if isinstance(stats, dict) else 0
            moved_faces = stats.get("moved_faces", 0) if isinstance(stats, dict) else 0

            if duplicates > 0:
                msg_lines.append(f"⚠️ Found {duplicates} duplicate photo{'s' if duplicates != 1 else ''}")
                msg_lines.append("   (already in target, not duplicated)")
                msg_lines.append("")

            if unique_moved > 0:
                msg_lines.append(f"• Moved {unique_moved} unique photo{'s' if unique_moved != 1 else ''}")
            elif duplicates > 0:
                msg_lines.append(f"• No unique photos to move (all were duplicates)")

            msg_lines.append(f"• Reassigned {moved_faces} face crop{'s' if moved_faces != 1 else ''}")
            msg_lines.append("")
            msg_lines.append(f"Total: {total_photos} photo{'s' if total_photos != 1 else ''} in '{target_name}'")

            QMessageBox.information(
                self,
                "Merge complete",
                "\n".join(msg_lines),
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Merge failed",
                f"Could not merge people:\n{e}",
            )
            return

        # Refresh sidebar to reflect new clusters
        self._build_tree_model()

        # Sync-up People counts so UI shows post-merge member_count immediately.
        try:
            cluster_rows = {r["branch_key"]: r for r in self.db.get_face_clusters(self.project_id)}
        except Exception as e:
            print(f"[Sidebar] post-merge: failed to reload face clusters: {e}")
            cluster_rows = {}

        try:
            # Find top-level People root and its count column item
            for top_row in range(self.model.rowCount()):
                top_idx = self.model.index(top_row, 0)
                top_item = self.model.itemFromIndex(top_idx)
                if not top_item:
                    continue
                if top_item.text() and top_item.text().startswith("👥"):
                    # update children counts
                    total_faces = 0
                    for i in range(top_item.rowCount()):
                        name_item = top_item.child(i, 0)
                        count_item = top_item.child(i, 1)
                        if not name_item or not count_item:
                            continue
                        # branch_key stored at Qt.UserRole + 1 (or fallback to UserRole)
                        bk = name_item.data(Qt.UserRole + 1)
                        if bk is None:
                            bk = name_item.data(Qt.UserRole)
                            if isinstance(bk, str) and bk.startswith("facecluster:"):
                                bk = bk.split(":", 1)[1]
                        if isinstance(bk, str) and bk.startswith("facecluster:"):
                            bk = bk.split(":", 1)[1]
                        row = cluster_rows.get(bk)
                        cnt = (row.get("member_count") if row else None)
                        if cnt is None:
                            count_item.setText("")
                        else:
                            # CRITICAL FIX: Handle bytes/int/str types before setText()
                            # After merge, member_count can be bytes which displays as random chars
                            if isinstance(cnt, (int, float)):
                                count_text = str(int(cnt))
                            elif isinstance(cnt, bytes):
                                try:
                                    count_text = str(int.from_bytes(cnt, byteorder='little'))
                                except (ValueError, OverflowError):
                                    count_text = "0"
                            elif isinstance(cnt, str):
                                count_text = cnt
                            else:
                                count_text = str(cnt)

                            count_item.setText(count_text)
                            try:
                                total_faces += int(count_text)
                            except Exception:
                                pass
                    # Update the People root count (second column in the top-level row)
                    people_count_item = self.model.item(top_row, 1)
                    if people_count_item:
                        people_count_item.setText(str(total_faces) if total_faces else "")
                    break
        except Exception as e:
            print(f"[Sidebar] post-merge: failed to update People UI counts: {e}")
        # Also re-run async count population for other registered branch targets
        if getattr(self, "_count_targets", None):
            try:
                self._async_populate_counts()
            except Exception as e:
                print(f"[Sidebar] _async_populate_counts failed after merge: {e}")


    def _rename_face_cluster(self, branch_key: str, current_label: str):
        """
        Rename a single face cluster (person) and refresh the sidebar.
        Works with raw 'face_000' or 'facecluster:face_000' values.

        CRITICAL FIX: Uses targeted item update instead of full rebuild to prevent
        freeze/crash caused by heavy _build_tree_model() operation.
        """
        from PySide6.QtWidgets import QInputDialog

        if not self.project_id or not branch_key:
            return

        # Normalise key
        if isinstance(branch_key, str) and branch_key.startswith("facecluster:"):
            branch_key = branch_key.split(":", 1)[1]

        # Ask user for new label
        base_text = current_label or ""
        new_label, ok = QInputDialog.getText(
            self,
            "Rename person",
            "New name:",
            text=base_text,
        )
        if not ok:
            return

        new_label = new_label.strip()
        if not new_label or new_label == current_label:
            return

        # Persist in DB
        try:
            if hasattr(self.db, "rename_face_cluster"):
                self.db.rename_face_cluster(self.project_id, branch_key, new_label)
            else:
                # Fallback: at least rename branches row
                with self.db._connect() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE branches SET display_name = ? "
                        "WHERE project_id = ? AND branch_key = ?",
                        (new_label, self.project_id, branch_key),
                    )
                    conn.commit()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Rename failed",
                f"Could not rename person:\n{e}",
            )
            return

        # CRITICAL FIX: Use targeted update instead of full rebuild
        # This prevents the freeze/crash caused by _build_tree_model()
        self._update_person_name_in_tree(branch_key, new_label)

    def _update_person_name_in_tree(self, branch_key: str, new_label: str):
        """
        Update a specific person's display name in BOTH tree and tabs views.

        This is a targeted, lightweight update that prevents the freeze/crash
        caused by calling _build_tree_model() after renaming.

        CRITICAL FIX: Also updates PeopleListView in tabs to sync both views.
        SAFETY: Checks widget validity to prevent crashes from deleted Qt objects.

        Args:
            branch_key: The branch_key of the person (e.g., "face_000")
            new_label: The new display name for the person
        """
        try:
            # Update LIST VIEW (tree model)
            people_root = None
            for row in range(self.model.rowCount()):
                item = self.model.item(row, 0)
                if item and item.text().startswith("👥 People"):
                    people_root = item
                    break

            if people_root:
                # Find the specific person item under People root
                for child_row in range(people_root.rowCount()):
                    child_item = people_root.child(child_row, 0)
                    if not child_item:
                        continue

                    # Check if this item matches the branch_key
                    item_branch_key = child_item.data(Qt.UserRole + 1)

                    # CRITICAL FIX: Normalize both keys for comparison
                    # Tree items store "facecluster:face_000" but branch_key is just "face_000"
                    normalized_item_key = item_branch_key
                    if isinstance(normalized_item_key, str) and normalized_item_key.startswith("facecluster:"):
                        normalized_item_key = normalized_item_key.split(":", 1)[1]

                    if normalized_item_key == branch_key:
                        # Found it! Update the display name
                        print(f"[Sidebar] Updating person name in tree: {branch_key} → {new_label}")
                        child_item.setText(new_label)

                        # CRITICAL FIX: Force visual refresh of tree view
                        # tree.update() doesn't always repaint, use viewport().update()
                        try:
                            self.tree.viewport().update()
                            # Also trigger a model data change signal
                            index = self.model.indexFromItem(child_item)
                            if index.isValid():
                                self.model.dataChanged.emit(index, index)
                        except (RuntimeError, AttributeError):
                            pass
                        break
            else:
                print(f"[Sidebar] Could not find People root to update {branch_key}")

            # CRITICAL FIX: Also update TABS VIEW (PeopleListView) for cross-view sync
            # SAFETY: Check widget validity before accessing to prevent crash
            if hasattr(self, 'tabs_controller') and self.tabs_controller:
                try:
                    # Check if tabs_controller is still valid (not deleted)
                    if not self.tabs_controller.isVisible():
                        # Tabs view is hidden, skip update (widget may be deleted)
                        print(f"[Sidebar] Tabs view hidden, skipping tabs update")
                        return

                    if hasattr(self.tabs_controller, 'people_list_view'):
                        people_view = self.tabs_controller.people_list_view

                        # CRITICAL: Check if widget is still valid before accessing
                        # Qt C++ object might be deleted even if Python ref exists
                        if people_view and not getattr(people_view, '_is_being_deleted', False):
                            try:
                                # Test widget validity by accessing a property
                                _ = people_view.table.rowCount()

                                # Widget is valid, proceed with update
                                for row_idx in range(people_view.table.rowCount()):
                                    item = people_view.table.item(row_idx, 1)  # Name column
                                    if item:
                                        stored_key = item.data(Qt.UserRole + 1)
                                        if stored_key == branch_key:
                                            print(f"[Sidebar] Updating person name in tabs view: {branch_key} → {new_label}")
                                            item.setText(new_label)
                                            people_view.table.viewport().update()
                                            break
                            except (RuntimeError, AttributeError) as widget_err:
                                # Widget was deleted or C++ object is gone
                                print(f"[Sidebar] Tabs widget deleted, skipping update: {widget_err}")
                except (RuntimeError, AttributeError) as ctrl_err:
                    # tabs_controller itself was deleted
                    print(f"[Sidebar] Tabs controller deleted, skipping update: {ctrl_err}")

            print(f"[Sidebar] Successfully updated person name in visible views")

        except Exception as e:
            print(f"[Sidebar] Failed to update person name in tree: {e}")
            import traceback
            traceback.print_exc()

    def _show_face_merge_suggestions(self):
        """
        Uses centroid distance to suggest likely duplicates.
        """
        try:
            suggestions = self.db.get_face_merge_suggestions(self.project_id)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Suggestions failed",
                f"Could not compute merge suggestions:\n{e}",
            )
            return

        if not suggestions:
            QMessageBox.information(
                self,
                "Merge suggestions",
                "No obvious merge suggestions were found.\n"
                "You may need to detect/cluster more faces first.",
            )
            return

        lines = [
            "Smaller distance → higher similarity.\n",
        ]
        for s in suggestions:
            lines.append(
                f"{s['a_label']} [{s['a_branch']}]  ↔  "
                f"{s['b_label']} [{s['b_branch']}]  "
                f"(d = {s['distance']:.3f})"
            )

        QMessageBox.information(
            self,
            "Merge suggestions",
            "\n".join(lines),
        )


    def _undo_last_face_merge(self):
        """
        Undo the last merge_face_clusters() operation using the DB log.
        """
        try:
            stats = self.db.undo_last_face_merge(self.project_id)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Undo failed",
                f"Could not undo last face merge:\n{e}",
            )
            return

        if not stats:
            QMessageBox.information(
                self,
                "Undo merge",
                "There is no face merge operation to undo.",
            )
            return

        QMessageBox.information(
            self,
            "Undo merge",
            (
                f"Restored {stats.get('faces', 0)} face crops and "
                f"{stats.get('images', 0)} image-branch assignments\n"
                f"across {stats.get('clusters', 0)} clusters."
            ),
        )

        self._build_tree_model()


    def _do_export(self, branch_key: str):
        dest = QFileDialog.getExistingDirectory(self, f"Export branch: {branch_key}")
        if not dest:
            return
        try:
            count = export_branch(self.project_id, branch_key, dest)
            QMessageBox.information(self, "Export Completed",
                                    f"Exported {count} photos from '{branch_key}' to:\n{dest}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _extract_embeddings_for_folder(self, folder_id: int):
        """Extract embeddings for all photos in a specific folder."""
        try:
            # Get photos in this folder
            photos = self.db.get_images_by_folder(folder_id, self.project_id) or []
            photo_ids = [p.get('id') for p in photos if p.get('id')]

            if not photo_ids:
                QMessageBox.information(self, "No Photos", "No photos found in this folder.")
                return

            # Confirm with user
            reply = QMessageBox.question(
                self,
                "Extract Embeddings",
                f"Extract AI embeddings for {len(photo_ids)} photos in this folder?\n\n"
                "This enables semantic search (e.g., 'sunset at beach').",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if reply != QMessageBox.Yes:
                return

            # Start embedding extraction
            mw = self.window()
            if hasattr(mw, '_on_extract_embeddings'):
                # Pass photo_ids to extraction method
                mw._start_embedding_extraction(photo_ids)
            else:
                QMessageBox.warning(self, "Not Available", "Embedding extraction is not available.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract embeddings:\n{str(e)}")

    def _show_folder_embedding_stats(self, folder_id: int):
        """Show embedding statistics for a specific folder."""
        try:
            from services.semantic_embedding_service import get_semantic_embedding_service

            # Get photos in this folder
            photos = self.db.get_images_by_folder(folder_id, self.project_id) or []
            photo_ids = [p.get('id') for p in photos if p.get('id')]

            if not photo_ids:
                QMessageBox.information(self, "No Photos", "No photos found in this folder.")
                return

            service = get_semantic_embedding_service()

            # Count photos with embeddings
            with_embeddings = sum(1 for pid in photo_ids if service.has_embedding(pid))
            coverage = (with_embeddings / len(photo_ids) * 100) if photo_ids else 0

            QMessageBox.information(
                self,
                "Folder Embedding Stats",
                f"Photos in folder: {len(photo_ids)}\n"
                f"With embeddings: {with_embeddings}\n"
                f"Coverage: {coverage:.1f}%\n\n"
                f"{'✓ Ready for semantic search!' if with_embeddings > 0 else 'Run Extract Embeddings to enable semantic search.'}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get stats:\n{str(e)}")

    def _extract_embeddings_for_project(self):
        """Extract embeddings for all photos in the current project."""
        try:
            mw = self.window()
            if hasattr(mw, '_on_extract_embeddings'):
                mw._on_extract_embeddings()
            else:
                QMessageBox.warning(self, "Not Available", "Embedding extraction is not available.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start extraction:\n{str(e)}")

    def _show_project_embedding_dashboard(self):
        """Show the embedding statistics dashboard for the current project."""
        try:
            mw = self.window()
            if hasattr(mw, '_on_open_embedding_dashboard'):
                mw._on_open_embedding_dashboard()
            else:
                # Fallback: try to open dashboard directly
                from ui.embedding_stats_dashboard import show_embedding_stats_dashboard
                if self.project_id:
                    self._embedding_dashboard = show_embedding_stats_dashboard(self.project_id, self)
                else:
                    QMessageBox.warning(self, "No Project", "Please select a project first.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open dashboard:\n{str(e)}")

    def _migrate_embeddings_to_float16(self):
        """Migrate float32 embeddings to float16 format."""
        try:
            mw = self.window()
            if hasattr(mw, '_on_migrate_embeddings_float16'):
                mw._on_migrate_embeddings_float16()
            else:
                # Fallback: do it directly
                from services.semantic_embedding_service import get_semantic_embedding_service

                service = get_semantic_embedding_service()
                stats = service.get_project_embedding_stats(self.project_id) if self.project_id else {}
                float32_count = stats.get('float32_count', 0)

                if float32_count == 0:
                    QMessageBox.information(
                        self,
                        "No Migration Needed",
                        "All embeddings are already in float16 format."
                    )
                    return

                reply = QMessageBox.question(
                    self,
                    "Migrate to Float16",
                    f"Found {float32_count} embeddings in legacy float32 format.\n\n"
                    "Converting to float16 will save ~50% storage space.\n\n"
                    "Proceed?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )

                if reply != QMessageBox.Yes:
                    return

                migrated = 0
                while True:
                    batch_migrated = service.migrate_to_half_precision(batch_size=500)
                    if batch_migrated == 0:
                        break
                    migrated += batch_migrated

                QMessageBox.information(
                    self,
                    "Migration Complete",
                    f"Successfully migrated {migrated} embeddings to float16 format."
                )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to migrate embeddings:\n{str(e)}")

    def _find_root_item(self, title: str):
        for row in range(self.model.rowCount()):
            it = self.model.item(row, 0)
            if not it:
                continue
            txt = it.text().strip()
            if txt.startswith(title):
                return it
        return None


    def collapse_all(self):
        try:
            self.tree.collapseAll()
            # Force column width recalculation after collapse
            QTimer.singleShot(0, self._recalculate_columns)
            try:
                if self.settings:
                    self.settings.set("sidebar_folded", True)
            except Exception:
                pass
        except Exception:
            pass

    def expand_all(self):
        try:
            for r in range(self.model.rowCount()):
                idx = self.model.index(r, 0)
                self.tree.expand(idx)
            # Force column width recalculation after expand
            QTimer.singleShot(0, self._recalculate_columns)
            try:
                if self.settings:
                    self.settings.set("sidebar_folded", False)
            except Exception:
                pass
        except Exception:
            pass

    def _recalculate_columns(self):
        """Force tree view to recalculate column widths"""
        try:
            header = self.tree.header()
            # Recalculate column 1 (counts) to fit content
            header.resizeSection(1, header.sectionSizeHint(1))
            # Force viewport update to ensure column 0 (names) uses remaining space
            self.tree.viewport().update()
            self.tree.scheduleDelayedItemsLayout()
        except Exception as e:
            print(f"[Sidebar] _recalculate_columns failed: {e}")

    def toggle_fold(self, folded: bool):
        if folded:
            self.collapse_all()
        else:
            self.expand_all()

    def _effective_display_mode(self):
        try:
            if self.settings:
                mode = str(self.settings.get("sidebar_mode", "list")).lower()
                if mode in ("tabs", "list"):
                    return mode
        except Exception:
            pass
        return "list"

    def switch_display_mode(self, mode: str):
        mode = (mode or "list").lower()
        if mode not in ("list", "tabs", "accordion"):
            mode = "list"
        try:
            if self.settings:
                self.settings.set("sidebar_mode", mode)
        except Exception:
            pass

        print(f"[SidebarQt] switch_display_mode({mode}) - canceling old workers")

        # CRITICAL: Process pending events before mode switch
        # This ensures all pending widget deletions are completed
        # Only process events after initialization is complete
        if self._initialized:
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()

        if mode == "tabs":
            # Cancel list mode workers by bumping generation
            self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000
            print(f"[SidebarQt] Canceled list workers (new gen={self._list_worker_gen})")

            print("[SidebarQt] Hiding tree view and accordion")
            self.tree.hide()
            if self.accordion_controller is not None:
                self.accordion_controller.hide()
            print("[SidebarQt] Showing tabs controller")
            tabs = self._ensure_tabs_controller()
            tabs.show_tabs()
            # Force refresh tabs when switching to tabs mode (ensures fresh data after scans)
            print("[SidebarQt] Calling tabs_controller.refresh_all(force=True) after mode switch")
            try:
                tabs.refresh_all(force=True)
                print("[SidebarQt] tabs_controller.refresh_all() completed after mode switch")
            except Exception as e:
                print(f"[SidebarQt] ERROR in tabs_controller.refresh_all() after mode switch: {e}")
                import traceback
                traceback.print_exc()
        elif mode == "accordion":
            # Cancel list mode and tabs mode workers
            self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000
            print(f"[SidebarQt] Canceled list workers (new gen={self._list_worker_gen})")

            print("[SidebarQt] Hiding tree view and tabs")
            self.tree.hide()
            if self.tabs_controller is not None:
                self.tabs_controller.hide_tabs()
            print("[SidebarQt] Showing accordion controller")
            accordion = self._ensure_accordion_controller()
            accordion.show()
            # Force refresh accordion when switching to accordion mode (ensures fresh data after scans)
            print("[SidebarQt] Calling accordion_controller.refresh_all(force=True) after mode switch")
            try:
                accordion.refresh_all(force=True)
                print("[SidebarQt] accordion_controller.refresh_all() completed after mode switch")
            except Exception as e:
                print(f"[SidebarQt] ERROR in accordion_controller.refresh_all() after mode switch: {e}")
                import traceback
                traceback.print_exc()
        else:
            # Cancel tab and accordion workers
            print("[SidebarQt] Hiding tabs controller and accordion")
            if self.tabs_controller is not None:
                self.tabs_controller.hide_tabs()
            if self.accordion_controller is not None:
                self.accordion_controller.hide()
            print("[SidebarQt] Canceled tab/accordion workers via hide")

            # Process events again after hiding tabs/accordion to clear widgets
            # Only after initialization is complete
            if self._initialized:
                print("[SidebarQt] Processing pending events after hide_tabs()/hide()")
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.processEvents()
                print("[SidebarQt] Finished processing events")

            # CRITICAL FIX: Clear tree view selection before showing to prevent stale Qt references
            print("[SidebarQt] Clearing tree view selection before rebuild")
            try:
                if hasattr(self.tree, 'selectionModel') and self.tree.selectionModel():
                    self.tree.selectionModel().clear()
                # Clear any expand/collapse state that might hold stale references
                self.tree.collapseAll()
            except Exception as e:
                print(f"[SidebarQt] Warning: Could not clear tree selection: {e}")

            print("[SidebarQt] Showing tree view")
            self.tree.show()
            print("[SidebarQt] Calling _build_tree_model()")
            try:
                self._build_tree_model()
                print("[SidebarQt] _build_tree_model() completed")
            except Exception as e:
                print(f"[SidebarQt] ERROR in _build_tree_model(): {e}")
                import traceback
                traceback.print_exc()

        try:
            self.btn_mode_toggle.setChecked(mode == "tabs")
            self._update_mode_toggle_text()
        except Exception:
            pass


    def reload_throttled(self, delay_ms: int = 800):
        if self._reload_block:
            return
        self._reload_block = True
        if not self._reload_timer.isActive():
            self._reload_timer.start(delay_ms)

    def _do_reload_throttled(self):
        try:
            self.reload()
        finally:
            self._reload_block = False

    def cleanup(self):
        """Mark widget as disposed so background workers skip stale refreshes."""
        if self._disposed:
            return
        self._disposed = True
        print("[SidebarQt] cleanup() — marked as disposed")

    def reload(self):
        # CRITICAL: Guard against crashes during widget deletion
        if self._disposed:
            print("[SidebarQt] reload() blocked - widget disposed")
            return
        try:
            # Check if widget is being deleted
            if not self.isVisible():
                print("[SidebarQt] reload() blocked - widget not visible (likely being deleted)")
                return

            # Check if model exists
            if not hasattr(self, 'model') or not self.model:
                print("[SidebarQt] reload() blocked - model not initialized")
                return

        except (RuntimeError, AttributeError):
            print("[SidebarQt] reload() blocked - widget deleted or being deleted")
            return

        # Guard against concurrent reloads
        if self._refreshing:
            print("[SidebarQt] reload() blocked - already refreshing")
            return

        try:
            self._refreshing = True
            mode = self._effective_display_mode()
            tabs_visible = self.tabs_controller.isVisible() if self.tabs_controller else False
            print(f"[SidebarQt] reload() called, display_mode={mode}, tabs_visible={tabs_visible}")

            # CRITICAL FIX: Only refresh tabs if they're actually visible
            # This prevents crashes when reload() is called after switching to list mode
            # but before settings are fully updated
            if mode == "tabs" and tabs_visible:
                print(f"[SidebarQt] Calling tabs_controller.refresh_all(force=True)")
                try:
                    self.tabs_controller.refresh_all(force=True)
                    print(f"[SidebarQt] tabs_controller.refresh_all() completed")
                except Exception as e:
                    print(f"[SidebarQt] ERROR in tabs_controller.refresh_all(): {e}")
                    import traceback
                    traceback.print_exc()
            elif mode == "tabs" and not tabs_visible:
                print(f"[SidebarQt] WARNING: mode=tabs but tabs not visible, skipping refresh")
            else:
                print(f"[SidebarQt] Calling _build_tree_model() instead of tabs refresh")
                try:
                    self._build_tree_model()
                except Exception as e:
                    print(f"[SidebarQt] ERROR in _build_tree_model(): {e}")
                    import traceback
                    traceback.print_exc()
        finally:
            # Always reset flag, even if error occurs
            self._refreshing = False

    def _on_device_event(self, event_type: str):
        """
        Handle device change event from Windows monitor.

        Called immediately when a device is connected or disconnected.
        This provides instant detection vs 30-second timer polling.

        Args:
            event_type: "connected" or "disconnected"
        """
        print(f"\n[Sidebar] ===== Device event received: {event_type} =====")

        # OPTIMIZATION: Invalidate device scan cache to force fresh scan
        try:
            from services.device_sources import DeviceScanner
            DeviceScanner.invalidate_cache()
            print(f"[Sidebar] Device scan cache invalidated")
        except Exception as e:
            print(f"[Sidebar] Warning: Failed to invalidate cache: {e}")

        # Trigger device check (will use fresh scan due to cache invalidation)
        self._check_device_changes()

        print(f"[Sidebar] ===== End device event handling =====\n")

    def _check_device_changes(self):
        """
        Periodically check for device changes and refresh if needed.
        Called by auto-refresh timer every 30 seconds OR by device event handler.
        """
        # CRITICAL: Safety checks to prevent crashes during cleanup
        try:
            # Check if widget is being deleted (prevents RuntimeError)
            if not self.isVisible():
                return

            # Check if parent window exists
            if not self.window():
                return

            # Skip if already refreshing
            if self._refreshing:
                return

            # Check if model is valid
            if not hasattr(self, 'model') or not self.model:
                return

        except (RuntimeError, AttributeError):
            # Widget deleted or being deleted, stop timer
            if hasattr(self, '_device_refresh_timer'):
                self._device_refresh_timer.stop()
            return

        try:
            from services.device_sources import scan_mobile_devices

            # OPTIMIZATION: Progress callback to show status in UI
            def show_progress(message: str):
                """Show scan progress in status bar."""
                try:
                    main_window = self.window()
                    if main_window and hasattr(main_window, 'statusBar'):
                        main_window.statusBar().showMessage(message, 2000)
                except (RuntimeError, AttributeError):
                    pass  # Ignore if window is being closed

            # Quick scan for devices (with device registration and progress feedback)
            print("\n[Sidebar] ===== Auto-refresh device check =====")
            print(f"[Sidebar] Previous device count: {self._last_device_count}")
            devices = scan_mobile_devices(db=self.db, register_devices=True, progress_callback=show_progress)
            current_count = len(devices)
            print(f"[Sidebar] Current device count: {current_count}")

            # Check if device count changed
            if current_count != self._last_device_count:
                print(f"[Sidebar] Device count changed: {self._last_device_count} → {current_count}")
            else:
                print(f"[Sidebar] No change in device count")
            print("[Sidebar] ===== End auto-refresh check =====\n")

            if current_count != self._last_device_count:
                if current_count > self._last_device_count:
                    # New device(s) connected
                    new_count = current_count - self._last_device_count
                    print(f"[Sidebar] Auto-refresh: {new_count} new device(s) detected")

                    # Safe status bar access
                    try:
                        main_window = self.window()
                        if main_window and hasattr(main_window, 'statusBar'):
                            main_window.statusBar().showMessage(f"✓ Detected {new_count} new device(s), refreshing...", 3000)
                    except (RuntimeError, AttributeError):
                        pass

                else:
                    # Device(s) disconnected
                    removed_count = self._last_device_count - current_count
                    print(f"[Sidebar] Auto-refresh: {removed_count} device(s) disconnected")

                    # Safe status bar access
                    try:
                        main_window = self.window()
                        if main_window and hasattr(main_window, 'statusBar'):
                            main_window.statusBar().showMessage(f"Device(s) disconnected, refreshing...", 3000)
                    except (RuntimeError, AttributeError):
                        pass

                # Update count and refresh
                self._last_device_count = current_count
                self.reload()

        except (RuntimeError, AttributeError) as widget_error:
            # Widget deleted during execution, stop timer
            print(f"[Sidebar] Widget deleted during auto-refresh, stopping timer: {widget_error}")
            if hasattr(self, '_device_refresh_timer'):
                self._device_refresh_timer.stop()

        except Exception as e:
            # Other errors - log but don't crash
            print(f"[Sidebar] Device auto-refresh check failed: {e}")
            # Don't show error to user, just log it

    def _start_spinner(self):
        if not self._spin_timer.isActive():
            self._spin_angle = 0
            self._spin_timer.start()

    def _stop_spinner(self):
        if self._spin_timer.isActive():
            self._spin_timer.stop()
        self.btn_refresh.setIcon(QIcon(self._base_pm))

    def _tick_spinner(self):
        self._spin_angle = (self._spin_angle + 30) % 360
        pm = self._rotate_pixmap(self._base_pm, self._spin_angle)
        self.btn_refresh.setIcon(QIcon(pm))

    def _make_reload_pixmap(self, w: int, h: int) -> QPixmap:
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        font = p.font()
        font.setPointSize(int(h * 0.9))
        p.setFont(font)
        p.setPen(Qt.darkGray)
        p.drawText(pm.rect(), Qt.AlignCenter, "↻")
        p.end()
        return pm

    def _rotate_pixmap(self, pm: QPixmap, angle: int) -> QPixmap:
        if pm.isNull():
            return pm
        tr = QTransform()
        tr.rotate(angle)
        rotated = pm.transformed(tr, Qt.SmoothTransformation)
        final_pm = QPixmap(pm.size())
        final_pm.fill(Qt.transparent)
        p = QPainter(final_pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        x = (final_pm.width() - rotated.width()) // 2
        y = (final_pm.height() - rotated.height()) // 2
        p.drawPixmap(x, y, rotated)
        p.end()
        return final_pm
            
    def auto_refresh_sidebar_tabs(self):
        # Thin delegate to the new tabs widget
        if self.tabs_controller is not None:
            self.tabs_controller.refresh_all(force=True)
  

    def _set_grid_context(self, mode: str, value):
        mw = self.window()
        if not hasattr(mw, "grid"):
            return

        # clear tag filter when switching main contexts
        if mode in ("folder", "branch", "date") and hasattr(mw, "_clear_tag_filter"):
            mw._clear_tag_filter()

        # Normalize value: strip known prefixes added by accordion/tabs/people
        if mode == "branch" and isinstance(value, str):
            if value.startswith("branch:"):
                value = value[7:]
            elif value.startswith("facecluster:"):
                value = value.split(":", 1)[1]

        if mode == "branch" and isinstance(value, str) and value.startswith("date:"):
            mw.grid.set_context("date", value.replace("date:", ""))
        elif mode == "branch" and isinstance(value, str) and value.startswith("videos"):
            # Route video branches to videos mode
            mw.grid.set_context("videos", value)
        elif mode == "branch" and isinstance(value, str) and (
            value.startswith("face_") or value == "face_unidentified"
        ):
            # Route face branches to people mode so breadcrumb shows person name
            mw.grid.set_context("people", value)
        else:
            mw.grid.set_context(mode, value)

        # nudge layout
        def _reflow():
            try:
                g = mw.grid
                if hasattr(g, "_apply_zoom_geometry"):
                    g._apply_zoom_geometry()
                g.list_view.doItemsLayout()
                g.list_view.viewport().update()
            except Exception as e:
                print(f"[Sidebar] reflow failed: {e}")
        QTimer.singleShot(0, _reflow)


    # === Phase 3: Drag & Drop Handlers ===

    def _on_photos_dropped_to_folder(self, folder_id: int, photo_paths: list):
        """
        Handle photos dropped onto a folder in the sidebar tree.
        Updates the folder_id for all dropped photos in the database.
        """
        try:
            print(f"[DragDrop] Moving {len(photo_paths)} photo(s) to folder ID: {folder_id}")

            # Update folder_id for each photo in the database
            db = self.db if hasattr(self, 'db') else ReferenceDB()
            updated_count = 0

            for path in photo_paths:
                try:
                    db.set_folder_for_image(path, folder_id)
                    updated_count += 1
                except Exception as e:
                    print(f"[DragDrop] Failed to update folder for {path}: {e}")

            # Show success message
            QMessageBox.information(
                self,
                "Photos Moved",
                f"Successfully moved {updated_count} photo(s) to the selected folder."
            )

            # Refresh sidebar and grid to reflect changes
            if hasattr(self, '_do_reload_throttled'):
                self._do_reload_throttled()

            # Notify main window to refresh grid
            if hasattr(self.parent(), 'grid'):
                self.parent().grid.reload()

            print(f"[DragDrop] Successfully updated {updated_count}/{len(photo_paths)} photo(s)")

        except Exception as e:
            print(f"[DragDrop] Error moving photos to folder: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to move photos to folder:\n{str(e)}"
            )

    def _on_photos_dropped_to_tag(self, branch_key: str, photo_paths: list):
        """
        Handle photos dropped onto a tag/branch in the sidebar tree.
        Applies the tag to all dropped photos.
        """
        try:
            print(f"[DragDrop] Adding tag '{branch_key}' to {len(photo_paths)} photo(s)")

            # Determine tag name from branch key
            tag_name = None
            if branch_key == "favorite":
                tag_name = "favorite"
            elif branch_key.startswith("face_"):
                tag_name = "face"
            else:
                # For other branches, use the branch key as tag name
                tag_name = branch_key

            if not tag_name:
                print(f"[DragDrop] Unknown branch key: {branch_key}")
                return

            # Apply tag to each photo
            db = self.db if hasattr(self, 'db') else ReferenceDB()
            tag_service = get_tag_service()
            tagged_count = 0

            for path in photo_paths:
                try:
                    # Add tag to photo
                    tag_service.add_tag(path, tag_name)
                    tagged_count += 1
                except Exception as e:
                    print(f"[DragDrop] Failed to tag {path}: {e}")

            # Show success message
            QMessageBox.information(
                self,
                "Photos Tagged",
                f"Successfully tagged {tagged_count} photo(s) with '{tag_name}'."
            )

            # Refresh sidebar and grid to reflect changes
            if hasattr(self, '_do_reload_throttled'):
                self._do_reload_throttled()

            # Notify main window to refresh grid
            if hasattr(self.parent(), 'grid'):
                self.parent().grid.reload()

            print(f"[DragDrop] Successfully tagged {tagged_count}/{len(photo_paths)} photo(s)")

        except Exception as e:
            print(f"[DragDrop] Error tagging photos: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to tag photos:\n{str(e)}"
            )

    def _launch_detached(self, script_path: str):
        """Launch a script in a detached subprocess (used for heavy workers)."""
        try:
            import subprocess, sys
            subprocess.Popen([sys.executable, script_path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL,
                             close_fds=True)
            print(f"[Sidebar] Detached worker launched: {script_path}")
        except Exception as e:
            print(f"[Sidebar] Failed to launch worker: {e}")
    
    def _show_location_map(self, lat: float, lon: float, name: str, photo_count: int):
        """Display a static map in the details panel for a location."""
        try:
            # Get the details panel
            mw = self.window()
            if not hasattr(mw, 'details'):
                return
            
            details = mw.details
            
            # Generate static map HTML using OpenStreetMap tiles
            # We'll use a simple HTML/CSS map representation with a link
            map_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=13"
            
            # Create HTML for map display
            map_html = f"""
                <div style='padding: 12px; background: #f8f9fa; border-radius: 8px; margin: 8px 0;'>
                    <div style='font-size: 14pt; font-weight: 700; color: #0078d4; margin-bottom: 8px;'>
                        🗺️ {name}
                    </div>
                    <div style='color: #666; margin-bottom: 8px;'>
                        📍 {lat:.6f}, {lon:.6f}
                    </div>
                    <div style='background: #e8f4f8; padding: 12px; border-radius: 4px; border-left: 4px solid #0078d4; margin-bottom: 8px;'>
                        <div style='font-weight: 600; color: #0078d4; margin-bottom: 4px;'>
                            📷 {photo_count} photo{"s" if photo_count != 1 else ""} from this location
                        </div>
                        <div style='font-size: 9pt; color: #666;'>
                            Click map link below to view on OpenStreetMap
                        </div>
                    </div>
                    <a href='{map_url}' style='display: inline-block; padding: 8px 16px; background: #0078d4; 
                                                 color: white; text-decoration: none; border-radius: 4px; 
                                                 font-weight: 600;'>
                        🗺️ View on OpenStreetMap
                    </a>
                    <div style='margin-top: 12px; padding: 8px; background: white; border-radius: 4px;'>
                        <div style='font-size: 9pt; color: #888;'>
                            💡 <b>Tip:</b> Photos are grouped within {int(self.db.get_cached_location_name.__defaults__[0] if hasattr(self.db.get_cached_location_name, '__defaults__') else 5)} km radius
                        </div>
                    </div>
                </div>
            """
            
            # Display in meta label
            details.meta.setText(map_html)
            
            # Clear thumbnail to show map info prominently
            details.thumb.setText("🗺️\nMap View")
            details.thumb.setStyleSheet("""
                QLabel {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                              stop:0 #667eea, stop:1 #764ba2);
                    color: white;
                    font-size: 18pt;
                    font-weight: bold;
                    border-radius: 8px;
                    padding: 20px;
                }
            """)
            
            print(f"[LOCATION] Displayed map for {name} at {lat}, {lon}")
        except Exception as e:
            print(f"[LOCATION] Failed to show location map: {e}")
            import traceback
            traceback.print_exc()

