"""
Google Photos Layout Components
Extracted components from google_layout.py for better organization and maintainability.

Phase 3A: UI Widgets
- FlowLayout, CollapsibleSection, PersonCard, PeopleGridView

Phase 3C: Media Lightbox
- MediaLightbox, TrimMarkerSlider
- PreloadImageSignals, PreloadImageWorker
- ProgressiveImageSignals, ProgressiveImageWorker

Phase 3D: Photo Workers & Helpers
- PhotoButton, ThumbnailSignals, ThumbnailLoader
- PhotoLoadSignals, PhotoLoadWorker
- GooglePhotosEventFilter, AutocompleteEventFilter

Phase 3E: Dialog Classes
- PersonPickerDialog
"""

from google_components.widgets import (
    FlowLayout,
    CollapsibleSection,
    PersonCard,
    PeopleGridView
)

from google_components.media_lightbox import (
    MediaLightbox,
    TrimMarkerSlider,
    PreloadImageSignals,
    PreloadImageWorker,
    ProgressiveImageSignals,
    ProgressiveImageWorker
)

from google_components.photo_helpers import (
    PhotoButton,
    ThumbnailSignals,
    ThumbnailLoader,
    PhotoLoadSignals,
    PhotoLoadWorker,
    GooglePhotosEventFilter,
    AutocompleteEventFilter
)

from google_components.dialogs import (
    PersonPickerDialog
)

__all__ = [
    # Phase 3A: UI Widgets
    'FlowLayout',
    'CollapsibleSection',
    'PersonCard',
    'PeopleGridView',

    # Phase 3C: Media Lightbox
    'MediaLightbox',
    'TrimMarkerSlider',
    'PreloadImageSignals',
    'PreloadImageWorker',
    'ProgressiveImageSignals',
    'ProgressiveImageWorker',

    # Phase 3D: Photo Workers & Helpers
    'PhotoButton',
    'ThumbnailSignals',
    'ThumbnailLoader',
    'PhotoLoadSignals',
    'PhotoLoadWorker',
    'GooglePhotosEventFilter',
    'AutocompleteEventFilter',

    # Phase 3E: Dialog Classes
    'PersonPickerDialog',
]
