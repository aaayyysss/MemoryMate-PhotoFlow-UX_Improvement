"""
Session State Manager
Saves and restores user's browsing state for improved UX.

Following best practices from Google Material Design and iOS Human Interface Guidelines:
- App should resume exactly where user left off
- Save navigation state, selection, scroll position
- Persist across app restarts
"""

import json
import os
import logging
from typing import Optional, Dict, Any
from app_env import get_data_dir

logger = logging.getLogger(__name__)

# State file location — uses get_data_dir() which picks a writable location
# (APP_DIR for portable setups, ~/.memorymate for full installs, or temp as fallback)
STATE_FILE = os.path.join(get_data_dir(), "session_state.json")


class SessionState:
    """
    Manages session state persistence.

    State includes:
    - project_id: Currently active project
    - section: Active sidebar section (folders/dates/people/locations/videos/devices)
    - selection_type: Type of selection (folder/date/person/location/device)
    - selection_id: ID of selected item (folder_id, date_branch, person_branch, etc.)
    - selection_name: Display name of selected item
    - scroll_position: Scroll position in grid view (0-1 percentage)
    - search_query: Active search query if any
    """

    def __init__(self):
        self.state: Dict[str, Any] = {
            "project_id": None,
            "section": None,  # folders/dates/people/locations/videos/devices
            "selection_type": None,  # folder/date/person/location/device
            "selection_id": None,  # folder_id, date_branch, person_branch, etc.
            "selection_name": None,  # Display name for UI
            "scroll_position": 0.0,  # 0-1 percentage
            "search_query": None
        }
        self.load()

    def load(self):
        """Load session state from file."""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state.update(data)
                    logger.info(f"[SessionState] Loaded: section={self.state.get('section')}, "
                               f"selection={self.state.get('selection_type')}/{self.state.get('selection_id')}")
            except Exception as e:
                logger.warning(f"[SessionState] Failed to load: {e}")

    def save(self):
        """Save session state to file."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"[SessionState] Saved: section={self.state.get('section')}, "
                        f"selection={self.state.get('selection_type')}/{self.state.get('selection_id')}")
        except Exception as e:
            logger.error(f"[SessionState] Failed to save: {e}")

    def set_project(self, project_id: Optional[int]):
        """Set active project."""
        if self.state["project_id"] != project_id:
            self.state["project_id"] = project_id
            self.save()

    def set_section(self, section: str):
        """
        Set active sidebar section.

        Args:
            section: folders/dates/people/locations/videos/devices
        """
        if self.state["section"] != section:
            self.state["section"] = section
            # Clear selection when changing sections
            self.state["selection_type"] = None
            self.state["selection_id"] = None
            self.state["selection_name"] = None
            self.save()

    def set_selection(self, selection_type: str, selection_id: Any, selection_name: Optional[str] = None):
        """
        Set current selection.

        Args:
            selection_type: folder/date/person/location/device
            selection_id: ID of selected item (folder_id, date_branch, person_branch, etc.)
            selection_name: Display name for UI
        """
        self.state["selection_type"] = selection_type
        self.state["selection_id"] = selection_id
        self.state["selection_name"] = selection_name
        self.save()

    def set_scroll_position(self, position: float):
        """
        Set scroll position (0-1 percentage).

        Args:
            position: Scroll position as percentage (0.0 = top, 1.0 = bottom)
        """
        self.state["scroll_position"] = max(0.0, min(1.0, position))
        # Don't save immediately on scroll (too frequent), save on navigation instead

    def set_search_query(self, query: Optional[str]):
        """Set active search query."""
        if self.state["search_query"] != query:
            self.state["search_query"] = query
            self.save()

    def get_project_id(self) -> Optional[int]:
        """Get last active project ID."""
        return self.state.get("project_id")

    def get_section(self) -> Optional[str]:
        """Get last active section."""
        return self.state.get("section")

    def get_selection(self) -> tuple[Optional[str], Optional[Any], Optional[str]]:
        """
        Get last selection.

        Returns:
            tuple: (selection_type, selection_id, selection_name)
        """
        return (
            self.state.get("selection_type"),
            self.state.get("selection_id"),
            self.state.get("selection_name")
        )

    def get_scroll_position(self) -> float:
        """Get last scroll position."""
        return self.state.get("scroll_position", 0.0)

    def get_search_query(self) -> Optional[str]:
        """Get last search query."""
        return self.state.get("search_query")

    def clear(self):
        """Clear all session state."""
        self.state = {
            "project_id": None,
            "section": None,
            "selection_type": None,
            "selection_id": None,
            "selection_name": None,
            "scroll_position": 0.0,
            "search_query": None
        }
        self.save()
        logger.info("[SessionState] Cleared all state")


# Singleton instance
_instance: Optional[SessionState] = None


def get_session_state() -> SessionState:
    """Get singleton session state instance."""
    global _instance
    if _instance is None:
        _instance = SessionState()
    return _instance
