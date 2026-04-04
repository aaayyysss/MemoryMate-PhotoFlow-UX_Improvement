# controllers/sidebar_controller.py
# Version 11.01.01.03-19 dated 20260214

"""
SidebarController - Sidebar Navigation Event Handling

Extracted from main_window_qt.py (Phase 1, Step 1.3)

Responsibilities:
- Folder selection event handling
- Date branch navigation
- Videos tab selection
- Thumbnail refresh coordination
- Tag filter integration

Version: 09.20.00.00
"""


class SidebarController:
    """Encapsulates folder/branch event handling & thumbnail refresh."""
    def __init__(self, main):
        self.main = main

    def on_folder_selected(self, folder_id: int):
        self.main.grid.set_folder(folder_id)

        if getattr(self.main, "active_tag_filter", "all") != "all":
            self.main._apply_tag_filter(self.main.active_tag_filter)

        if self.main.thumbnails and hasattr(self.main.grid, "get_visible_paths"):
            self.main.thumbnails.clear()
            self.main.thumbnails.load_thumbnails(self.main.grid.get_visible_paths())

    def on_branch_selected(self, branch_key: str):
        self.main.grid.set_branch(branch_key)

        if getattr(self.main, "active_tag_filter", "all") != "all":
            self.main._apply_tag_filter(self.main.active_tag_filter)

        # REMOVED: Forced zoom slider update causes white thumbnails and incorrect layout
        # The grid already applies correct zoom geometry in reload()
        # if hasattr(self.main.grid, "_on_slider_changed"):
        #     self.main.grid._on_slider_changed(self.main.grid.zoom_slider.value())

        if hasattr(self.main.grid, "list_view"):
            self.main.grid.list_view.scrollToTop()

        if self.main.thumbnails and hasattr(self.main.grid, "get_visible_paths"):
            self.main.thumbnails.clear()
            self.main.thumbnails.load_thumbnails(self.main.grid.get_visible_paths())

    def on_group_selected(self, group_id: int):
        """Handle group selection — filter grid to show only group match photos.

        Routes through the current layout's filter_by_paths (GooglePhotosLayout)
        rather than the ThumbnailGridQt which does not support path filtering.
        Paths are frozen (copied) at query time to prevent mutable-reference bugs
        in the coalescing layer.
        """
        try:
            from services.group_service import GroupService
            from reference_db import ReferenceDB

            # Resolve project_id from layout or grid
            project_id = None
            layout = None
            if hasattr(self.main, 'layout_manager'):
                layout = self.main.layout_manager.get_current_layout()
                if layout and hasattr(layout, 'project_id'):
                    project_id = layout.project_id
            if project_id is None and hasattr(self.main, 'grid'):
                project_id = getattr(self.main.grid, 'project_id', None)
            if project_id is None:
                print("[SidebarController] Cannot resolve project_id for group selection")
                return

            db = ReferenceDB()
            paths = GroupService.get_cached_match_paths(db, project_id, group_id)
            paths = list(paths)  # freeze to avoid mutations while coalescing
            GroupService.touch_group(db, group_id)
            db.close()

            print(f"[SidebarController] Group {group_id} selected: {len(paths)} matching photos")

            # Prefer layout-level filtering (GooglePhotosLayout has filter_by_paths)
            routed = False
            if layout and hasattr(layout, "filter_by_paths"):
                layout.filter_by_paths(paths, navigation_mode="group")
                routed = True
            elif hasattr(self.main, 'grid'):
                grid = self.main.grid
                if hasattr(grid, "filter_by_paths"):
                    grid.filter_by_paths(paths, navigation_mode="group")
                    routed = True

            if not routed:
                print(f"[SidebarController] No filter_by_paths target found for group {group_id}")

        except Exception as e:
            print(f"[SidebarController] Group selection failed: {e}")
            import traceback
            traceback.print_exc()

    def on_videos_selected(self):
        """Handle videos tab selection - show all videos for current project"""
        # 🎬 Phase 4: Videos support
        if hasattr(self.main.grid, "set_videos"):
            self.main.grid.set_videos()

        if getattr(self.main, "active_tag_filter", "all") != "all":
            self.main._apply_tag_filter(self.main.active_tag_filter)

        if hasattr(self.main.grid, "list_view"):
            self.main.grid.list_view.scrollToTop()

        if self.main.thumbnails and hasattr(self.main.grid, "get_visible_paths"):
            self.main.thumbnails.clear()
            self.main.thumbnails.load_thumbnails(self.main.grid.get_visible_paths())
