"""
Project switch service — Phase 1B MainWindow decomposition extraction.

Owns project bootstrap, switching, and session-state restoration previously
living directly on MainWindow. This service is held by MainWindow and
delegated to from thin wrappers so behavior is byte-for-byte identical — only
ownership has moved.

Responsibilities extracted from main_window_qt.MainWindow:
- _bootstrap_active_project        → bootstrap_active_project
- _on_project_changed_by_id        → on_project_changed_by_id
- _refresh_project_list            → refresh_project_list
- _restore_session_state           → restore_session_state
- _restore_selection               → restore_selection
- _restore_selection_sidebarqt     → restore_selection_sidebarqt

Non-responsibilities (still owned by MainWindow, referenced via self.mw):
- _maybe_prompt_clip_upgrade (semantic workflow coordinator — Phase 4)
- _refresh_people_quick_section (people workflow coordinator — Phase 3)
- grid / sidebar / layout_manager / search_controller attribute ownership
"""

import logging
from typing import Optional

from PySide6.QtCore import QTimer

logger = logging.getLogger(__name__)


class ProjectSwitchService:
    """Owns project bootstrap + switching + session restore for MainWindow."""

    def __init__(self, main_window):
        self.mw = main_window

    # ------------------------------------------------------------------
    # Bootstrap: runs once during early MainWindow init to pick a project.
    # ------------------------------------------------------------------
    def bootstrap_active_project(self) -> Optional[int]:
        """
        Canonical project bootstrap policy:
        1. if there is a last-used project and it still exists, auto-select it.
        2. else if exactly one project exists, auto-select it.
        3. else enter an explicit onboarding state (project_id=None).
        """
        from session_state_manager import get_session_state
        from repository.project_repository import ProjectRepository
        from repository.base_repository import DatabaseConnection

        session_state = get_session_state()
        last_pid = session_state.get_project_id()

        # Step 1: Check last-used project
        if last_pid is not None:
            try:
                proj = ProjectRepository(DatabaseConnection()).get_by_id(last_pid)
                if proj:
                    logger.info(f"[Bootstrap] Policy: Restoring last-used project_id={last_pid}")
                    return last_pid
                else:
                    logger.info(f"[Bootstrap] Policy: Last-used project_id={last_pid} no longer exists")
                    session_state.set_project(None)
            except Exception as e:
                logger.info(f"[Bootstrap] Policy: Session check failed: {e}")

        # Step 2: Auto-select if single project exists
        try:
            from app_services import list_projects
            projects = list_projects()
            if len(projects) == 1:
                pid = projects[0]["id"]
                logger.info(f"[Bootstrap] Policy: Auto-selecting single existing project_id={pid}")
                session_state.set_project(pid)
                return pid
            elif len(projects) > 1:
                logger.info(f"[Bootstrap] Policy: {len(projects)} projects exist, user selection required")
            else:
                logger.info("[Bootstrap] Policy: No projects found, entering onboarding")
        except Exception as e:
            logger.info(f"[Bootstrap] Policy: Project list check failed: {e}")

        return None

    # ------------------------------------------------------------------
    # User-driven project switch
    # ------------------------------------------------------------------
    def on_project_changed_by_id(self, project_id: int):
        """
        Switch to a project by ID.

        Delegates to the **active layout** via BaseLayout.set_project() so
        that GooglePhotosLayout refreshes its AccordionSidebar while
        CurrentLayout refreshes SidebarQt + grid.  MainWindow no longer
        pokes hidden/dead widgets directly.
        """
        mw = self.mw
        print(f"\n[MainWindow] ========== _on_project_changed_by_id({project_id}) STARTED ==========")
        try:
            if hasattr(mw, "search_controller"):
                mw.search_controller.set_active_project(project_id)

            # Already on this project?
            current_project_id = getattr(mw.grid, 'project_id', None) if hasattr(mw, 'grid') else None
            if current_project_id == project_id:
                print(f"[MainWindow] Already on project {project_id}, skipping switch")
                return

            # 1. Persist to session state
            from session_state_manager import get_session_state
            get_session_state().set_project(project_id)

            # 2. Delegate to the active layout (the layout owns its sidebar)
            layout = None
            if hasattr(mw, 'layout_manager') and mw.layout_manager:
                layout = mw.layout_manager.get_current_layout()

            mw.active_project_id = project_id

            if layout is not None:
                layout.set_project(project_id)
                print(f"[MainWindow] Delegated to {type(layout).__name__}.set_project({project_id})")
            else:
                # Fallback: no layout manager yet (very early startup)
                if hasattr(mw, "grid") and mw.grid:
                    mw.grid.project_id = project_id
                if hasattr(mw, "sidebar") and mw.sidebar:
                    mw.sidebar.set_project(project_id)

            # 3. Reset grid branch to "all" for CurrentLayout's grid
            #    (Google layout handles its own reload inside set_project)
            layout_id = ""
            if hasattr(mw, 'layout_manager') and mw.layout_manager:
                layout_id = mw.layout_manager.get_current_layout_id() or ""
            if layout_id not in ("google", "google_legacy"):
                if hasattr(mw, "grid") and mw.grid:
                    mw.grid.set_branch("all")

            # CLIP upgrade check (Phase: Better Model Awareness)
            QTimer.singleShot(1500, mw._maybe_prompt_clip_upgrade)

            # Breadcrumb auto-updates via gridReloaded signal

            # Phase 5: refresh People shell after project switch
            if hasattr(mw, "_refresh_people_quick_section"):
                mw._refresh_people_quick_section()

            print(f"[MainWindow] ========== _on_project_changed_by_id({project_id}) COMPLETED ==========\n")
        except Exception as e:
            print(f"[MainWindow] ERROR switching project: {e}")
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Project list refresh (after new project creation)
    # ------------------------------------------------------------------
    def refresh_project_list(self):
        """
        Phase 2: Refresh the project list (called after creating a new project).
        Updates the cached project list for breadcrumb navigation.
        """
        mw = self.mw
        try:
            from app_services import list_projects
            mw._projects = list_projects()
            print(f"[MainWindow] Refreshed project list: {len(mw._projects)} projects")
        except Exception as e:
            print(f"[MainWindow] Error refreshing project list: {e}")

    # ------------------------------------------------------------------
    # Session state restoration (section + selection)
    # ------------------------------------------------------------------
    def restore_session_state(self):
        """
        PHASE 2 & 3: Restore last browsing state (section expansion + selection).
        Called after UI is fully loaded via QTimer.singleShot.
        """
        mw = self.mw
        # One-shot guard: this may be scheduled from multiple init paths
        if getattr(mw, '_session_restored', False):
            return
        mw._session_restored = True

        try:
            from session_state_manager import get_session_state
            session_state = get_session_state()

            # PHASE 2: Restore last expanded section
            last_section = session_state.get_section()
            if not last_section:
                print(f"[MainWindow] PHASE 2: No previous section to restore")
                return

            # Find the AccordionSidebar (could be in different locations depending on layout)
            accordion_sidebar = None

            # Case 1: GooglePhotosLayout - sidebar IS the AccordionSidebar
            if hasattr(mw, 'layout_manager') and hasattr(mw.layout_manager, 'current_layout'):
                current_layout = mw.layout_manager.current_layout
                if hasattr(current_layout, 'sidebar') and hasattr(current_layout.sidebar, '_expand_section'):
                    accordion_sidebar = current_layout.sidebar
                    print(f"[MainWindow] PHASE 2: Found AccordionSidebar in GooglePhotosLayout")

            # Case 2: Old SidebarQt - sidebar.accordion
            if not accordion_sidebar and hasattr(mw, 'sidebar'):
                if hasattr(mw.sidebar, 'accordion') and hasattr(mw.sidebar.accordion, '_expand_section'):
                    accordion_sidebar = mw.sidebar.accordion
                    print(f"[MainWindow] PHASE 2: Found AccordionSidebar in sidebar.accordion")

            # Case 3: Current Layout with SidebarQt (tree-based, not accordion-based)
            if not accordion_sidebar and hasattr(mw, 'sidebar') and hasattr(mw.sidebar, 'tree'):
                print(f"[MainWindow] PHASE 2: Using Current Layout (SidebarQt) - will restore via tree selection")
                # For SidebarQt, we restore by triggering the selection programmatically
                QTimer.singleShot(300, lambda: self.restore_selection_sidebarqt(session_state))
                return

            if accordion_sidebar:
                print(f"[MainWindow] PHASE 2: Restoring section={last_section} from session state")
                accordion_sidebar._expand_section(last_section)

                # PHASE 3: Restore selection after section loads (defer 300ms for section to load)
                QTimer.singleShot(300, lambda: self.restore_selection(session_state, accordion_sidebar))
            else:
                print(f"[MainWindow] PHASE 2: Could not find any sidebar to restore")

        except Exception as e:
            print(f"[MainWindow] PHASE 2: Failed to restore session state: {e}")
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Selection restore for AccordionSidebar (Google layout)
    # ------------------------------------------------------------------
    def restore_selection(self, session_state, accordion_sidebar):
        """
        PHASE 3: Restore last selection (folder/date/person/video).
        Called after section is expanded and loaded.
        """
        try:
            sel_type, sel_id, sel_name = session_state.get_selection()

            if not sel_type or sel_id is None:
                print(f"[MainWindow] PHASE 3: No selection to restore")
                return

            print(f"[MainWindow] PHASE 3: Restoring {sel_type} selection: {sel_name} (ID={sel_id})")

            # Use the passed accordion_sidebar to access section_logic
            if not hasattr(accordion_sidebar, 'section_logic'):
                print(f"[MainWindow] PHASE 3: AccordionSidebar has no section_logic")
                return

            # Trigger selection based on type
            if sel_type == "folder":
                folders_section = accordion_sidebar.section_logic.get("folders")
                if folders_section and hasattr(folders_section, 'folderSelected'):
                    folders_section.folderSelected.emit(sel_id)
                    print(f"[MainWindow] PHASE 3: Restored folder selection: {sel_name}")

            elif sel_type == "date":
                dates_section = accordion_sidebar.section_logic.get("dates")
                if dates_section and hasattr(dates_section, 'dateSelected'):
                    dates_section.dateSelected.emit(sel_id)
                    print(f"[MainWindow] PHASE 3: Restored date selection: {sel_name}")

            elif sel_type == "person":
                people_section = accordion_sidebar.section_logic.get("people")
                if people_section and hasattr(people_section, 'personSelected'):
                    people_section.personSelected.emit(sel_id)
                    print(f"[MainWindow] PHASE 3: Restored person selection: {sel_name}")

            elif sel_type == "video":
                # PHASE 3 FIX: Add video selection restoration
                videos_section = accordion_sidebar.section_logic.get("videos")
                if videos_section and hasattr(videos_section, 'videoFilterSelected'):
                    videos_section.videoFilterSelected.emit(sel_id)
                    print(f"[MainWindow] PHASE 3: Restored video selection: {sel_name}")

        except Exception as e:
            print(f"[MainWindow] PHASE 3: Failed to restore selection: {e}")
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Selection restore for classic SidebarQt (Current Layout)
    # ------------------------------------------------------------------
    def restore_selection_sidebarqt(self, session_state):
        """
        PHASE 3: Restore last selection for SidebarQt (Current Layout).
        Called after UI is fully loaded.
        """
        mw = self.mw
        try:
            sel_type, sel_id, sel_name = session_state.get_selection()

            if not sel_type or sel_id is None:
                print(f"[MainWindow] PHASE 3 (SidebarQt): No selection to restore")
                return

            print(f"[MainWindow] PHASE 3 (SidebarQt): Restoring {sel_type} selection: {sel_name} (ID={sel_id})")

            # For SidebarQt, we need to programmatically trigger the _on_item_clicked with appropriate mode/value
            if not hasattr(mw, 'sidebar') or not hasattr(mw.sidebar, '_on_item_clicked'):
                print(f"[MainWindow] PHASE 3 (SidebarQt): Sidebar has no _on_item_clicked method")
                return

            # Map selection type to SidebarQt signals
            if sel_type == "video":
                # Parse video selection format
                if sel_id == "all":
                    # All videos - emit appropriate signal
                    if hasattr(mw.sidebar, 'selectVideos'):
                        mw.sidebar.selectVideos.emit("all")
                    print(f"[MainWindow] PHASE 3 (SidebarQt): Restored all videos selection")
                elif sel_id.startswith("year:"):
                    # Year filter: "year:2024"
                    year = sel_id.split(":", 1)[1]
                    if hasattr(mw.sidebar, 'selectVideos'):
                        mw.sidebar.selectVideos.emit(f"year:{year}")
                    print(f"[MainWindow] PHASE 3 (SidebarQt): Restored video year selection: {year}")
                elif sel_id.startswith("month:"):
                    # Month filter: "month:2024-07"
                    month = sel_id.split(":", 1)[1]
                    if hasattr(mw.sidebar, 'selectVideos'):
                        mw.sidebar.selectVideos.emit(f"month:{month}")
                    print(f"[MainWindow] PHASE 3 (SidebarQt): Restored video month selection: {month}")
                elif sel_id.startswith("day:"):
                    # Day filter: "day:2024-07-15"
                    day = sel_id.split(":", 1)[1]
                    if hasattr(mw.sidebar, 'selectVideos'):
                        mw.sidebar.selectVideos.emit(f"day:{day}")
                    print(f"[MainWindow] PHASE 3 (SidebarQt): Restored video day selection: {day}")
                else:
                    print(f"[MainWindow] PHASE 3 (SidebarQt): Unknown video filter format: {sel_id}")
                    return

            elif sel_type == "folder":
                # Folder selection - emit selectFolder signal
                if hasattr(mw.sidebar, 'selectFolder'):
                    mw.sidebar.selectFolder.emit(sel_id)
                print(f"[MainWindow] PHASE 3 (SidebarQt): Restored folder selection: {sel_name} (ID={sel_id})")

            elif sel_type == "date":
                # Date selection - emit selectDate signal
                if hasattr(mw.sidebar, 'selectDate'):
                    mw.sidebar.selectDate.emit(sel_id)
                print(f"[MainWindow] PHASE 3 (SidebarQt): Restored date selection: {sel_name} (ID={sel_id})")

            elif sel_type == "person":
                # Person selection - emit selectBranch signal for person branches
                if hasattr(mw.sidebar, 'selectBranch'):
                    mw.sidebar.selectBranch.emit(sel_id)
                print(f"[MainWindow] PHASE 3 (SidebarQt): Restored person selection: {sel_name} (ID={sel_id})")

        except Exception as e:
            print(f"[MainWindow] PHASE 3 (SidebarQt): Failed to restore selection: {e}")
            import traceback
            traceback.print_exc()
