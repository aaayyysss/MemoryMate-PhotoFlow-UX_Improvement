# ui/accordion_sidebar/locations_section.py
# Locations section for accordion sidebar - GPS-based photo grouping

import threading
import traceback
import logging
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView, QSizePolicy, QLabel
from PySide6.QtCore import Signal, Qt, QObject
from PySide6.QtGui import QColor
from reference_db import ReferenceDB
from .base_section import BaseSection

logger = logging.getLogger(__name__)


class LocationsSectionSignals(QObject):
    """Signals for locations section loading."""
    loaded = Signal(int, list)  # (generation, location_clusters)
    error = Signal(int, str)     # (generation, error_message)


class LocationsSection(BaseSection):
    """
    Locations section implementation.

    Displays GPS-based photo clusters grouped by proximity.
    Shows location names (if available) and photo counts.
    """

    # Signal emitted when location is selected (double-click)
    locationSelected = Signal(dict)  # location data dict with {name, lat, lon, count, paths}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = LocationsSectionSignals()
        self.signals.loaded.connect(self._on_data_loaded)
        self.signals.error.connect(self._on_error)

    def get_section_id(self) -> str:
        return "locations"

    def get_title(self) -> str:
        return "Locations"

    def get_icon(self) -> str:
        return "📍"

    def load_section(self) -> None:
        """Load location clusters from database in background thread."""
        if not self.project_id:
            logger.warning("[LocationsSection] No project_id set")
            return

        # Increment generation
        self._generation += 1
        current_gen = self._generation
        self._loading = True

        logger.info(f"[LocationsSection] Loading locations (generation {current_gen})...")

        # Background worker
        def work():
            db = None
            try:
                db = ReferenceDB()  # Per-thread instance

                # Get location clusters: [{name, count, lat, lon, paths}, ...]
                location_clusters = []
                if hasattr(db, "get_location_clusters"):
                    location_clusters = db.get_location_clusters(self.project_id) or []

                logger.info(f"[LocationsSection] Loaded {len(location_clusters)} location clusters (gen {current_gen})")
                return location_clusters

            except Exception as e:
                error_msg = f"Error loading locations: {e}"
                logger.error(f"[LocationsSection] {error_msg}")
                traceback.print_exc()
                return []
            finally:
                if db:
                    try:
                        db.close()
                    except Exception:
                        pass

        # Run in thread
        def on_complete():
            try:
                result = work()
                self.signals.loaded.emit(current_gen, result)
            except Exception as e:
                logger.error(f"[LocationsSection] Error in worker thread: {e}")
                traceback.print_exc()
                self.signals.error.emit(current_gen, str(e))

        threading.Thread(target=on_complete, daemon=True).start()

    def create_content_widget(self, data):
        """Create locations tree widget."""
        location_clusters = data or []

        if not location_clusters:
            placeholder = QLabel(
                "📍 No Locations Found\n\n"
                "Photos with GPS data will appear here.\n\n"
                "To add locations:\n"
                "• Use photos taken with location services enabled\n"
                "• Add location manually via photo details (coming soon)"
            )
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("padding: 20px; color: #666; line-height: 1.6;")
            placeholder.setWordWrap(True)
            return placeholder

        # Create tree widget
        tree = QTreeWidget()
        tree.setHeaderLabels([self.get_title(), "Count"])
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

        # Populate tree with location clusters
        # Sort by count descending (most photos first)
        sorted_locations = sorted(location_clusters, key=lambda x: x.get('count', 0), reverse=True)

        for location in sorted_locations:
            name = location.get('name', 'Unknown Location')
            count = location.get('count', 0)
            lat = location.get('lat', 0.0)
            lon = location.get('lon', 0.0)

            # Create location key for selection
            location_key = f"location_{lat:.4f}_{lon:.4f}"

            # Display name with coordinate hint if no name available
            if name == 'Unknown Location':
                display_name = f"{name} ({lat:.4f}, {lon:.4f})"
            else:
                display_name = name

            location_item = QTreeWidgetItem([display_name, str(count)])
            location_item.setData(0, Qt.UserRole, location_key)
            location_item.setData(0, Qt.UserRole + 1, location)  # Store full location data
            location_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            location_item.setForeground(1, QColor("#888888"))
            tree.addTopLevelItem(location_item)

        # Connect double-click
        tree.itemDoubleClicked.connect(
            lambda item, col: self._on_location_clicked(item)
        )

        logger.info(f"[LocationsSection] Tree built with {tree.topLevelItemCount()} locations")
        return tree

    def _on_data_loaded(self, generation: int, data: list):
        """Callback when locations data is loaded."""
        self._loading = False
        if generation != self._generation:
            logger.debug(f"[LocationsSection] Discarding stale data (gen {generation} vs {self._generation})")
            return
        logger.info(f"[LocationsSection] Data loaded successfully (gen {generation})")

    def _on_error(self, generation: int, error_msg: str):
        """Callback when locations loading fails."""
        self._loading = False
        if generation != self._generation:
            return
        logger.error(f"[LocationsSection] Load failed: {error_msg}")

    def _on_location_clicked(self, item):
        """Handle location item click - emit location data for branch creation."""
        location_data = item.data(0, Qt.UserRole + 1)
        if location_data:
            logger.info(f"[LocationsSection] Location selected: {location_data.get('name')} ({location_data.get('count')} photos)")
            self.locationSelected.emit(location_data)
        else:
            logger.warning("[LocationsSection] No location data found for clicked item")
