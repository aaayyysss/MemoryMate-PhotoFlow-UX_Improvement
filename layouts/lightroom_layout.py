# layouts/lightroom_layout.py
# Adobe Lightroom-style layout (PLACEHOLDER - Coming Soon)
# Professional 3-panel design: Library | Grid | Inspector

from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt
from typing import Optional
from .base_layout import BaseLayout


class LightroomLayout(BaseLayout):
    """
    Adobe Lightroom-style layout (COMING SOON).

    Planned Structure:
    ┌─────────────────────────────────────────────────────┐
    │  [🔍 Search]  [Library|Develop|Map] [Sort ▼] [⚙️]  │
    ├──────────┬────────────────────────┬─────────────────┤
    │          │                        │                 │
    │ Library  │    Photo Grid          │   Inspector     │
    │ ─────    │   ┌──┬──┬──┬──┐       │  ┌────────────┐ │
    │ 📅 All   │   │  │  │  │  │       │  │ [Preview]  │ │
    │ ⭐ Fav   │   ├──┼──┼──┼──┤       │  │            │ │
    │ 👤 People│   │  │  │  │  │       │  │  IMG_1234  │ │
    │ 📍 Places│   └──┴──┴──┴──┘       │  ├────────────┤ │
    │ 🏷️ Tags  │                        │  │ Metadata   │ │
    │          │  [Timeline ←────→]     │  │ Edit Tools │ │
    └──────────┴────────────────────────┴─────────────────┘

    Features (Planned):
    - 3-panel layout (Library | Grid | Inspector)
    - Single-click preview in inspector panel
    - Quick edit tools in inspector
    - Timeline slider for quick navigation
    - Professional workflow-oriented
    """

    def get_name(self) -> str:
        return "Lightroom Style"

    def get_id(self) -> str:
        return "lightroom"

    def create_layout(self) -> QWidget:
        """
        Create placeholder widget for Lightroom layout.
        """
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.setAlignment(Qt.AlignCenter)

        # "Coming Soon" message
        title = QLabel("📷 Lightroom Layout")
        title.setStyleSheet("font-size: 24pt; font-weight: bold; color: #31A8FF;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Coming Soon")
        subtitle.setStyleSheet("font-size: 14pt; color: #666;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        description = QLabel(
            "Professional 3-panel design\n"
            "Library • Grid • Inspector Panel\n"
            "Single-click preview • Quick edits • Timeline\n\n"
            "Stay tuned for the update!"
        )
        description.setStyleSheet("font-size: 11pt; color: #888; margin-top: 20px;")
        description.setAlignment(Qt.AlignCenter)
        layout.addWidget(description)

        return placeholder

    def get_sidebar(self):
        """Lightroom layout will have a library panel (when implemented)."""
        return None

    def get_grid(self):
        """Grid in center panel (when implemented)."""
        return None

    def get_inspector(self):
        """Inspector panel on right (when implemented)."""
        return None

    # ========== PHASE 3 Task 3.1: BaseLayout Interface Implementation ==========
    # Stub implementations for placeholder layout

    def set_project(self, project_id: int) -> None:
        """Not implemented - placeholder layout."""
        pass

    def get_current_project(self) -> Optional[int]:
        """Not implemented - placeholder layout."""
        return None

    def refresh_after_scan(self) -> None:
        """Not implemented - placeholder layout."""
        pass

    def refresh_thumbnails(self) -> None:
        """Not implemented - placeholder layout."""
        pass

    def filter_by_date(self, year: Optional[int] = None,
                      month: Optional[int] = None,
                      day: Optional[int] = None) -> None:
        """Not implemented - placeholder layout."""
        pass

    def filter_by_folder(self, folder_path: str) -> None:
        """Not implemented - placeholder layout."""
        pass

    def filter_by_person(self, person_branch_key: str) -> None:
        """Not implemented - placeholder layout."""
        pass

    def clear_filters(self) -> None:
        """Not implemented - placeholder layout."""
        pass

    def get_selected_paths(self) -> list:
        """Not implemented - placeholder layout."""
        return []

    def clear_selection(self) -> None:
        """Not implemented - placeholder layout."""
        pass
