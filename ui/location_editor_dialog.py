#!/usr/bin/env python3
"""Location Editor Dialog

Allows users to manually add or edit GPS location for photos.
Similar to Google Photos' "Add location" / "Edit location" feature.

Features:
- View current location (if available)
- Enter coordinates manually (latitude, longitude)
- Enter location name manually
- Optional: Geocode coordinates to get location name
- Validate coordinates (-90 to 90 lat, -180 to 180 lon)
- Preview location on map (opens in browser)
"""

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLabel, QLineEdit, QPushButton, QTextEdit,
                               QMessageBox, QGroupBox, QListWidget, QListWidgetItem, QComboBox,
                               QScrollArea, QWidget)
from PySide6.QtCore import Qt, Signal, QTimer, QUrl, Slot, QObject
from PySide6.QtGui import QDoubleValidator, QPixmap

# SPRINT 3: Embedded Map View
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebChannel import QWebChannel
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    logger_module = __import__('logging')
    logger_module.getLogger(__name__).warning("QWebEngineView not available - map view disabled")

import logging

logger = logging.getLogger(__name__)


# SPRINT 3: Separate QObject for map communication (avoids Qt property warnings)
class MapHandler(QObject):
    """Handles JavaScript ↔ Python communication for embedded map."""

    # Signal to notify dialog when coordinates change from map
    coordinatesChanged = Signal(float, float)

    @Slot(float, float)
    def updateCoordinatesFromMap(self, lat: float, lon: float):
        """
        Called from JavaScript when marker is dragged.

        Args:
            lat: Latitude from dragged marker
            lon: Longitude from dragged marker
        """
        logger.info(f"[MapHandler] Coordinates from map: ({lat:.6f}, {lon:.6f})")
        self.coordinatesChanged.emit(lat, lon)


class LocationEditorDialog(QDialog):
    """
    Dialog for editing photo GPS location.

    Allows manual entry of:
    - Latitude (-90 to 90)
    - Longitude (-180 to 180)
    - Location name (city, country, etc.)

    Optional features:
    - Geocode coordinates → location name
    - Open location in browser map (OpenStreetMap)
    """

    # Signal emitted when location is saved
    locationSaved = Signal(float, float, str)  # lat, lon, location_name

    def __init__(self, photo_path=None, current_lat=None, current_lon=None, current_name=None,
                 parent=None, batch_mode=False, batch_count=1, photo_paths=None):
        """
        Initialize location editor dialog.

        Args:
            photo_path: Path to photo being edited (or count string for batch mode)
            current_lat: Current latitude (if any)
            current_lon: Current longitude (if any)
            current_name: Current location name (if any)
            parent: Parent widget
            batch_mode: If True, editing multiple photos at once
            batch_count: Number of photos being edited in batch mode
            photo_paths: Optional list of photo paths for batch mode (enables thumbnail preview)
        """
        super().__init__(parent)

        self.photo_path = photo_path
        self.current_lat = current_lat
        self.current_lon = current_lon
        self.current_name = current_name
        self.batch_mode = batch_mode
        self.batch_count = batch_count
        self.photo_paths = photo_paths  # SPRINT 2: For batch thumbnail preview

        if batch_mode:
            self.setWindowTitle(f"Edit Location - {batch_count} Photos")
        else:
            self.setWindowTitle("Edit Location")

        self.setMinimumWidth(800)
        self.setMinimumHeight(700)
        self.setMaximumHeight(900)  # FIX: Prevent dialog from being taller than screen

        # SPRINT 3: Create map handler for JavaScript communication
        self.map_handler = MapHandler() if WEBENGINE_AVAILABLE else None
        if self.map_handler:
            self.map_handler.coordinatesChanged.connect(self._on_map_coordinates_changed)

        # FIX: Auto-geocode timer (debounce geocoding requests)
        self.geocode_timer = QTimer()
        self.geocode_timer.setSingleShot(True)
        self.geocode_timer.timeout.connect(self._auto_geocode_from_map)
        self.pending_geocode_coords = None  # Store coords for delayed geocoding

        self._init_ui()
        self._load_current_location()

    def _init_ui(self):
        """Initialize user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # FIX: Buttons at TOP (not bottom) so they're always visible
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        clear_btn = QPushButton("Clear Location")
        clear_btn.clicked.connect(self._clear_location)
        button_layout.addWidget(clear_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save_location)
        save_btn.setStyleSheet("background: #1a73e8; color: white; padding: 8px 24px; font-weight: bold; border-radius: 4px;")
        button_layout.addWidget(save_btn)

        main_layout.addLayout(button_layout)

        # FIX: Scrollable content area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Content widget
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # Photo info
        if self.photo_path:
            from pathlib import Path
            photo_label = QLabel(f"📷 {Path(self.photo_path).name}")
            photo_label.setStyleSheet("font-weight: bold; padding: 8px; background: #f0f0f0; border-radius: 4px;")
            layout.addWidget(photo_label)

        # SPRINT 2 ENHANCEMENT: Photo Preview (150x150px thumbnails)
        self._init_photo_preview(layout)

        # SPRINT 2 ENHANCEMENT: Recent Locations dropdown (quick reuse)
        recent_group = QGroupBox("⏱️ Recent Locations")
        recent_layout = QVBoxLayout()

        # Recent locations dropdown
        self.recent_combo = QComboBox()
        self.recent_combo.setStyleSheet("""
            QComboBox {
                padding: 6px 12px;
                border: 1px solid #dadce0;
                border-radius: 4px;
                background: white;
                min-height: 24px;
            }
            QComboBox:hover {
                border: 1px solid #1a73e8;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #5f6368;
                margin-right: 10px;
            }
        """)
        self.recent_combo.currentIndexChanged.connect(self._on_recent_location_selected)

        # Load recent locations
        self._load_recent_locations()

        recent_layout.addWidget(self.recent_combo)

        # Help text
        recent_help = QLabel("💡 Select a recently used location to auto-fill coordinates and name")
        recent_help.setStyleSheet("font-size: 10pt; color: #666; font-style: italic;")
        recent_layout.addWidget(recent_help)

        recent_group.setLayout(recent_layout)
        layout.addWidget(recent_group)

        # CRITICAL FIX: Location search by name (forward geocoding)
        search_group = QGroupBox("🔍 Search for Location")
        search_layout = QVBoxLayout()

        # Search input row
        search_input_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("e.g., Golden Gate Bridge, San Francisco, Paris...")
        self.search_input.returnPressed.connect(self._search_location)  # Search on Enter
        search_input_layout.addWidget(self.search_input)

        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.clicked.connect(self._search_location)
        self.search_btn.setStyleSheet("background: #1a73e8; color: white; padding: 6px 16px; font-weight: bold;")
        search_input_layout.addWidget(self.search_btn)

        search_layout.addLayout(search_input_layout)

        # Search results list
        self.search_results = QListWidget()
        self.search_results.setMaximumHeight(120)
        self.search_results.setStyleSheet("""
            QListWidget {
                border: 1px solid #dadce0;
                border-radius: 4px;
                background: #f8f9fa;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #e8eaed;
            }
            QListWidget::item:hover {
                background: #e8f0fe;
            }
            QListWidget::item:selected {
                background: #1a73e8;
                color: white;
            }
        """)
        self.search_results.itemDoubleClicked.connect(self._on_search_result_selected)
        self.search_results.hide()  # Hidden initially
        search_layout.addWidget(self.search_results)

        # Help text
        search_help = QLabel("💡 Type a place name and click Search, or press Enter")
        search_help.setStyleSheet("font-size: 10pt; color: #666; font-style: italic;")
        search_layout.addWidget(search_help)

        search_group.setLayout(search_layout)
        layout.addWidget(search_group)

        # Current location display
        current_group = QGroupBox("Current Location")
        current_layout = QVBoxLayout()

        self.current_display = QLabel()
        self.current_display.setWordWrap(True)
        self.current_display.setStyleSheet("padding: 8px; color: #666;")
        current_layout.addWidget(self.current_display)

        current_group.setLayout(current_layout)
        layout.addWidget(current_group)

        # Coordinate input
        coord_group = QGroupBox("GPS Coordinates")
        coord_layout = QFormLayout()

        # Latitude input
        self.lat_input = QLineEdit()
        self.lat_input.setPlaceholderText("e.g., 37.7749")
        lat_validator = QDoubleValidator(-90.0, 90.0, 6)
        lat_validator.setNotation(QDoubleValidator.StandardNotation)
        self.lat_input.setValidator(lat_validator)
        # SPRINT 3: Connect to map marker updates
        self.lat_input.textChanged.connect(self._on_coordinates_changed)
        coord_layout.addRow("Latitude (-90 to 90):", self.lat_input)

        # Longitude input
        self.lon_input = QLineEdit()
        self.lon_input.setPlaceholderText("e.g., -122.4194")
        lon_validator = QDoubleValidator(-180.0, 180.0, 6)
        lon_validator.setNotation(QDoubleValidator.StandardNotation)
        self.lon_input.setValidator(lon_validator)
        # SPRINT 3: Connect to map marker updates
        self.lon_input.textChanged.connect(self._on_coordinates_changed)
        coord_layout.addRow("Longitude (-180 to 180):", self.lon_input)

        # Map preview button
        map_btn_layout = QHBoxLayout()
        self.map_preview_btn = QPushButton("🗺️ Preview on Map")
        self.map_preview_btn.clicked.connect(self._preview_on_map)
        map_btn_layout.addWidget(self.map_preview_btn)
        map_btn_layout.addStretch()
        coord_layout.addRow("", map_btn_layout)

        coord_group.setLayout(coord_layout)
        layout.addWidget(coord_group)

        # SPRINT 3: Embedded Map View (Optional - only if QWebEngineView available)
        self._init_embedded_map(layout)

        # Location name input
        name_group = QGroupBox("Location Name")
        name_layout = QVBoxLayout()

        name_input_layout = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., San Francisco, California, USA")
        name_input_layout.addRow("Name:", self.name_input)

        name_layout.addLayout(name_input_layout)

        # Geocode button
        geocode_layout = QHBoxLayout()
        self.geocode_btn = QPushButton("🌍 Get Location Name from Coordinates")
        self.geocode_btn.clicked.connect(self._geocode_coordinates)
        geocode_layout.addWidget(self.geocode_btn)
        geocode_layout.addStretch()
        name_layout.addLayout(geocode_layout)

        name_group.setLayout(name_layout)
        layout.addWidget(name_group)

        # Help text
        help_text = QLabel(
            "💡 Tips:\n"
            "• Get coordinates from Google Maps: Right-click → Copy coordinates\n"
            "• Click 'Get Location Name' to automatically find location name\n"
            "• Preview shows location on OpenStreetMap"
        )
        help_text.setStyleSheet("padding: 12px; background: #f8f9fa; border-radius: 4px; color: #666;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        layout.addStretch()

        # FIX: Set content widget and add to scroll area
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

    def _init_photo_preview(self, layout: QVBoxLayout):
        """
        SPRINT 2 ENHANCEMENT: Initialize photo preview section.

        Shows 150x150px thumbnails of photos being edited:
        - Single mode: One thumbnail
        - Batch mode: 3-5 thumbnails + "... and N more"

        Loads asynchronously to avoid blocking the UI.
        """
        if not self.photo_path:
            return

        # Create preview group
        preview_group = QGroupBox("📸 Photo Preview")
        preview_group_layout = QVBoxLayout()
        preview_group_layout.setContentsMargins(4, 4, 4, 4)

        # ENHANCEMENT: Scrollable thumbnail area with both horizontal and vertical scrollbars
        # This allows users to browse ALL thumbnails during batch GPS editing
        self.thumbnail_scroll_area = QScrollArea()
        self.thumbnail_scroll_area.setWidgetResizable(True)
        self.thumbnail_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.thumbnail_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.thumbnail_scroll_area.setMinimumHeight(140)  # Enough for 120px thumbnails + margins
        self.thumbnail_scroll_area.setMaximumHeight(180)
        self.thumbnail_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #dadce0;
                border-radius: 4px;
                background: #f8f9fa;
            }
        """)

        # Container widget for thumbnails (will be placed inside scroll area)
        self.thumbnail_container = QWidget()
        preview_layout = QHBoxLayout(self.thumbnail_container)
        preview_layout.setSpacing(8)
        preview_layout.setContentsMargins(8, 8, 8, 8)

        # Storage for thumbnail labels
        self.thumbnail_labels = []

        if self.batch_mode:
            # Batch mode: Show message that thumbnails will load
            loading_label = QLabel("Loading thumbnails...")
            loading_label.setStyleSheet("color: #666; font-style: italic; padding: 8px;")
            loading_label.setAlignment(Qt.AlignCenter)
            preview_layout.addWidget(loading_label)
            self.thumbnail_labels.append(loading_label)
        else:
            # Single mode: Show one thumbnail placeholder
            thumbnail_label = QLabel()
            thumbnail_label.setFixedSize(150, 150)
            thumbnail_label.setAlignment(Qt.AlignCenter)
            thumbnail_label.setStyleSheet("""
                QLabel {
                    border: 2px solid #dadce0;
                    border-radius: 8px;
                    background: #f8f9fa;
                    color: #666;
                }
            """)
            thumbnail_label.setText("Loading...")
            preview_layout.addWidget(thumbnail_label)
            self.thumbnail_labels.append(thumbnail_label)

        preview_layout.addStretch()

        # Set container as scroll area widget
        self.thumbnail_scroll_area.setWidget(self.thumbnail_container)

        # Add scroll area to preview group
        preview_group_layout.addWidget(self.thumbnail_scroll_area)
        preview_group.setLayout(preview_group_layout)
        layout.addWidget(preview_group)

        # Load thumbnails asynchronously (non-blocking)
        # Use QTimer.singleShot to defer loading until after dialog is shown
        QTimer.singleShot(50, self._load_photo_thumbnails)

    def _load_photo_thumbnails(self):
        """
        Load photo thumbnails asynchronously.

        For single mode: Load one thumbnail
        For batch mode: Load up to 5 thumbnails + count indicator
        """
        try:
            from services.thumbnail_service import get_thumbnail_service
            from pathlib import Path

            thumb_service = get_thumbnail_service()

            if self.batch_mode:
                # Clear loading message
                for label in self.thumbnail_labels:
                    label.deleteLater()
                self.thumbnail_labels.clear()

                # Get the preview layout from thumbnail container
                preview_layout = self.thumbnail_container.layout()
                if not preview_layout:
                    return

                # ENHANCEMENT: Show ALL thumbnails (not just 5) with scrollbars
                # Users can now browse all photos during batch GPS editing
                if self.photo_paths and len(self.photo_paths) > 0:
                    # Show count label first
                    count_label = QLabel(f"📸 {len(self.photo_paths)} photos")
                    count_label.setStyleSheet("color: #1a73e8; font-weight: bold; padding: 8px; font-size: 10pt;")
                    count_label.setAlignment(Qt.AlignCenter)
                    preview_layout.addWidget(count_label)

                    # Show ALL thumbnails (user can scroll horizontally)
                    for i, photo_path in enumerate(self.photo_paths):
                        pixmap = thumb_service.get_thumbnail(photo_path, height=120)

                        thumbnail_label = QLabel()
                        thumbnail_label.setFixedSize(120, 120)
                        thumbnail_label.setAlignment(Qt.AlignCenter)
                        thumbnail_label.setStyleSheet("""
                            QLabel {
                                border: 2px solid #dadce0;
                                border-radius: 8px;
                                background: #f8f9fa;
                            }
                        """)

                        # Add photo filename as tooltip
                        from pathlib import Path
                        thumbnail_label.setToolTip(Path(photo_path).name)

                        if pixmap and not pixmap.isNull():
                            scaled_pixmap = pixmap.scaled(
                                120, 120,
                                Qt.KeepAspectRatio,
                                Qt.SmoothTransformation
                            )
                            thumbnail_label.setPixmap(scaled_pixmap)
                        else:
                            thumbnail_label.setText("⚠️")

                        preview_layout.addWidget(thumbnail_label)
                        self.thumbnail_labels.append(thumbnail_label)

                    logger.info(f"[LocationEditor] Loaded {len(self.photo_paths)} thumbnails with scrollbars")
                else:
                    # No photo_paths provided - show fallback message
                    msg_label = QLabel(f"📸 Editing {self.batch_count} photos")
                    msg_label.setStyleSheet("color: #666; padding: 8px; font-weight: bold;")
                    msg_label.setAlignment(Qt.AlignCenter)
                    preview_layout.addWidget(msg_label)

            else:
                # Single mode: Load one thumbnail
                pixmap = thumb_service.get_thumbnail(self.photo_path, height=150)

                if pixmap and not pixmap.isNull():
                    # Scale to fit 150x150 while preserving aspect ratio
                    scaled_pixmap = pixmap.scaled(
                        150, 150,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    self.thumbnail_labels[0].setPixmap(scaled_pixmap)
                    self.thumbnail_labels[0].setText("")  # Clear "Loading..."
                else:
                    # Failed to load
                    self.thumbnail_labels[0].setText("⚠️\nPreview\nUnavailable")
                    self.thumbnail_labels[0].setStyleSheet("""
                        QLabel {
                            border: 2px solid #dadce0;
                            border-radius: 8px;
                            background: #fff3cd;
                            color: #856404;
                            font-size: 9pt;
                        }
                    """)

        except Exception as e:
            logger.warning(f"[LocationEditor] Failed to load thumbnail: {e}")
            # Show error in thumbnail placeholder
            if self.thumbnail_labels:
                self.thumbnail_labels[0].setText("⚠️\nPreview\nError")

    def _load_current_location(self):
        """Load and display current location data."""
        if self.batch_mode:
            # Batch mode display
            if self.current_lat is not None and self.current_lon is not None:
                # All photos have same location
                name_str = f" - {self.current_name}" if self.current_name else ""
                self.current_display.setText(
                    f"✓ All {self.batch_count} photos have the same location:\n"
                    f"({self.current_lat:.6f}, {self.current_lon:.6f}){name_str}"
                )
                # Pre-fill inputs
                self.lat_input.setText(str(self.current_lat))
                self.lon_input.setText(str(self.current_lon))
                if self.current_name:
                    self.name_input.setText(self.current_name)
            else:
                # Photos have different locations or no locations
                self.current_display.setText(
                    f"✏️ Editing location for {self.batch_count} photos.\n"
                    f"Enter coordinates to apply the same location to all photos."
                )
        else:
            # Single photo mode display
            if self.current_lat is not None and self.current_lon is not None:
                # Display current location
                name_str = f" - {self.current_name}" if self.current_name else ""
                self.current_display.setText(
                    f"✓ Location set: ({self.current_lat:.6f}, {self.current_lon:.6f}){name_str}"
                )

                # Pre-fill inputs
                self.lat_input.setText(str(self.current_lat))
                self.lon_input.setText(str(self.current_lon))
                if self.current_name:
                    self.name_input.setText(self.current_name)
            else:
                self.current_display.setText("⚠ No location data. Enter coordinates manually or paste from Google Maps.")

    def _preview_on_map(self):
        """Open location preview in browser (OpenStreetMap)."""
        try:
            lat_text = self.lat_input.text().strip()
            lon_text = self.lon_input.text().strip()

            if not lat_text or not lon_text:
                QMessageBox.warning(self, "Missing Coordinates", "Please enter latitude and longitude first.")
                return

            lat = float(lat_text)
            lon = float(lon_text)

            # Validate coordinates
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                QMessageBox.warning(self, "Invalid Coordinates",
                                  f"Coordinates out of range:\n"
                                  f"Latitude must be between -90 and 90\n"
                                  f"Longitude must be between -180 and 180")
                return

            # Open OpenStreetMap in browser
            import webbrowser
            map_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}"
            webbrowser.open(map_url)

            logger.info(f"[LocationEditor] Opened map preview: ({lat}, {lon})")

        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numeric coordinates.")
        except Exception as e:
            logger.error(f"[LocationEditor] Map preview failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open map:\n{e}")

    def _search_location(self):
        """
        Search for location by name (forward geocoding).

        This allows users to type "San Francisco" instead of manually
        entering coordinates.
        """
        search_text = self.search_input.text().strip()

        if not search_text:
            QMessageBox.warning(self, "Empty Search", "Please enter a location name to search.")
            return

        # Show progress
        self.search_btn.setEnabled(False)
        self.search_btn.setText("🔍 Searching...")
        self.search_results.clear()

        try:
            # Import forward geocoding service
            from services.geocoding_service import forward_geocode

            # Search for locations
            results = forward_geocode(search_text, limit=5)

            if results:
                # Show results in list
                self.search_results.show()
                for result in results:
                    name = result['name']
                    lat = result['lat']
                    lon = result['lon']
                    result_type = result.get('type', 'location')

                    # Create list item with data
                    item_text = f"{name}\n({lat:.6f}, {lon:.6f})"
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.UserRole, result)  # Store full result data
                    item.setToolTip(f"Type: {result_type}\nDouble-click to select")

                    self.search_results.addItem(item)

                logger.info(f"[LocationEditor] Search '{search_text}' → {len(results)} result(s)")
            else:
                # No results found
                self.search_results.show()
                no_results = QListWidgetItem("❌ No results found. Try a different search term.")
                no_results.setFlags(Qt.NoItemFlags)  # Not selectable
                no_results.setForeground(Qt.gray)
                self.search_results.addItem(no_results)

                logger.info(f"[LocationEditor] Search '{search_text}' → No results")

        except ImportError as e:
            QMessageBox.critical(self, "Import Error",
                               f"Failed to import geocoding service:\n{e}\n\n"
                               f"Please ensure services/geocoding_service.py is available.")
            logger.error(f"[LocationEditor] Import error: {e}")
        except Exception as e:
            logger.error(f"[LocationEditor] Location search failed: {e}")
            QMessageBox.critical(self, "Search Error",
                               f"Failed to search for location:\n{e}\n\n"
                               f"Please check your internet connection.")
        finally:
            self.search_btn.setEnabled(True)
            self.search_btn.setText("🔍 Search")

    def _on_search_result_selected(self, item: QListWidgetItem):
        """
        Handle selection of a search result.

        Auto-fills coordinates and location name when user double-clicks a result.
        """
        result = item.data(Qt.UserRole)
        if not result:
            return

        # Auto-fill coordinates
        self.lat_input.setText(str(result['lat']))
        self.lon_input.setText(str(result['lon']))

        # Auto-fill location name
        self.name_input.setText(result['name'])

        # Hide search results
        self.search_results.hide()

        # Clear search input
        self.search_input.clear()

        logger.info(f"[LocationEditor] Selected: {result['name']} ({result['lat']}, {result['lon']})")

        # Show confirmation
        QMessageBox.information(
            self,
            "Location Selected",
            f"✓ Coordinates and name auto-filled:\n\n"
            f"Location: {result['name']}\n"
            f"Coordinates: ({result['lat']:.6f}, {result['lon']:.6f})\n\n"
            f"Click 'Save' to apply this location to your photo(s)."
        )

    def _load_recent_locations(self):
        """
        Load recent locations from settings and populate dropdown.

        Shows most recently used locations at the top for quick selection.
        """
        try:
            from settings_manager_qt import SettingsManager

            sm = SettingsManager()
            recents = sm.get_recent_locations(limit=10)

            # Clear existing items
            self.recent_combo.clear()

            # Add placeholder item
            self.recent_combo.addItem("-- Select Recent Location --", None)

            if not recents:
                # No recent locations
                self.recent_combo.addItem("(No recent locations yet)", None)
                self.recent_combo.setEnabled(False)
                return

            # Add recent locations
            for loc in recents:
                name = loc.get('name', 'Unknown')
                lat = loc.get('lat', 0)
                lon = loc.get('lon', 0)
                use_count = loc.get('use_count', 1)

                # Format display text
                if use_count > 1:
                    display_text = f"{name} (used {use_count}x)"
                else:
                    display_text = name

                # Store full location data
                self.recent_combo.addItem(display_text, loc)

            logger.info(f"[LocationEditor] Loaded {len(recents)} recent locations")

        except Exception as e:
            logger.error(f"[LocationEditor] Failed to load recent locations: {e}")
            # Don't crash, just disable the dropdown
            self.recent_combo.addItem("(Error loading recents)", None)
            self.recent_combo.setEnabled(False)

    def _on_recent_location_selected(self, index: int):
        """
        Handle selection of a recent location from dropdown.

        Auto-fills coordinates and location name when user selects a recent location.
        """
        if index <= 0:  # Placeholder or "no recents" item
            return

        location_data = self.recent_combo.itemData(index)
        if not location_data:
            return

        # Auto-fill coordinates
        lat = location_data.get('lat')
        lon = location_data.get('lon')
        name = location_data.get('name', '')

        if lat is not None and lon is not None:
            self.lat_input.setText(str(lat))
            self.lon_input.setText(str(lon))

        if name:
            self.name_input.setText(name)

        logger.info(f"[LocationEditor] Selected recent location: {name} ({lat}, {lon})")

        # Show brief confirmation (non-blocking)
        # User can immediately click Save without dismissing dialog
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: None)  # Process events

    def _geocode_coordinates(self):
        """Geocode coordinates to get location name."""
        try:
            lat_text = self.lat_input.text().strip()
            lon_text = self.lon_input.text().strip()

            if not lat_text or not lon_text:
                QMessageBox.warning(self, "Missing Coordinates", "Please enter latitude and longitude first.")
                return

            lat = float(lat_text)
            lon = float(lon_text)

            # Validate coordinates
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                QMessageBox.warning(self, "Invalid Coordinates",
                                  "Coordinates out of range.")
                return

            # Show progress
            self.geocode_btn.setEnabled(False)
            self.geocode_btn.setText("🌍 Getting location name...")

            # Import geocoding service
            try:
                from services.geocoding_service import reverse_geocode
            except ImportError as e:
                QMessageBox.critical(self, "Import Error",
                                   f"Failed to import geocoding service:\n{e}")
                return

            # Geocode
            location_name = reverse_geocode(lat, lon)

            if location_name:
                self.name_input.setText(location_name)
                QMessageBox.information(self, "Location Found",
                                      f"✓ Location: {location_name}")
                logger.info(f"[LocationEditor] Geocoded ({lat}, {lon}) → {location_name}")
            else:
                QMessageBox.warning(self, "Geocoding Failed",
                                  "Could not find location name for these coordinates.\n"
                                  "Please enter location name manually.")

        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numeric coordinates.")
        except Exception as e:
            logger.error(f"[LocationEditor] Geocoding failed: {e}")
            QMessageBox.critical(self, "Error", f"Geocoding failed:\n{e}")
        finally:
            self.geocode_btn.setEnabled(True)
            self.geocode_btn.setText("🌍 Get Location Name from Coordinates")

    def _clear_location(self):
        """Clear location data."""
        reply = QMessageBox.question(
            self,
            "Clear Location",
            "Remove GPS location data from this photo?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.lat_input.clear()
            self.lon_input.clear()
            self.name_input.clear()
            logger.info(f"[LocationEditor] Location data cleared")

    def _save_location(self):
        """Save location data."""
        try:
            lat_text = self.lat_input.text().strip()
            lon_text = self.lon_input.text().strip()
            location_name = self.name_input.text().strip() or None

            # Check if clearing location
            if not lat_text and not lon_text:
                # Saving empty location (clearing)
                self.locationSaved.emit(None, None, None)
                self.accept()
                return

            # Validate inputs
            if not lat_text or not lon_text:
                QMessageBox.warning(self, "Incomplete Data",
                                  "Please enter both latitude and longitude.")
                return

            lat = float(lat_text)
            lon = float(lon_text)

            # Reject null-island (0, 0) — almost certainly a default / uninitialised value
            if lat == 0.0 and lon == 0.0:
                QMessageBox.warning(
                    self, "Invalid Location",
                    "Coordinates (0, 0) point to 'Null Island' in the Atlantic Ocean "
                    "and are almost certainly not a real location.\n\n"
                    "Please select a valid location on the map or enter real coordinates.",
                )
                return

            # Validate range
            if not (-90 <= lat <= 90):
                QMessageBox.warning(self, "Invalid Latitude",
                                  "Latitude must be between -90 and 90.")
                return

            if not (-180 <= lon <= 180):
                QMessageBox.warning(self, "Invalid Longitude",
                                  "Longitude must be between -180 and 180.")
                return

            # Emit signal with location data
            self.locationSaved.emit(lat, lon, location_name)
            logger.info(f"[LocationEditor] Location saved: ({lat}, {lon}) - {location_name}")

            self.accept()

        except ValueError:
            QMessageBox.warning(self, "Invalid Input",
                              "Please enter valid numeric coordinates.")
        except Exception as e:
            logger.error(f"[LocationEditor] Save failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save location:\n{e}")

    def _init_embedded_map(self, layout: QVBoxLayout):
        """
        SPRINT 3: Initialize embedded map view with Leaflet.js.

        Adds an interactive map with draggable marker for visual GPS selection.
        Falls back gracefully if QWebEngineView is not available.
        """
        if not WEBENGINE_AVAILABLE:
            # QWebEngineView not available - skip map view
            logger.info("[LocationEditor] Embedded map disabled (QWebEngineView not available)")
            self.map_view = None
            return

        # Create map group
        map_group = QGroupBox("🗺️ Interactive Map (Optional)")
        map_layout = QVBoxLayout()

        # Map info label
        map_info = QLabel("Drag the marker to set GPS coordinates visually")
        map_info.setStyleSheet("color: #666; font-size: 9pt; padding: 4px;")
        map_layout.addWidget(map_info)

        # Create QWebEngineView
        try:
            self.map_view = QWebEngineView()
            self.map_view.setMinimumHeight(250)  # FIX: Reduced from 300
            self.map_view.setMaximumHeight(300)  # FIX: Reduced from 400

            # Generate Leaflet.js HTML
            map_html = self._generate_leaflet_html()

            # Load HTML
            self.map_view.setHtml(map_html, QUrl("qrc:/"))

            # FIX: Create web channel with dedicated MapHandler (avoids Qt property warnings)
            self.channel = QWebChannel()
            self.channel.registerObject("pyHandler", self.map_handler)  # FIX: Use map_handler
            self.map_view.page().setWebChannel(self.channel)

            map_layout.addWidget(self.map_view)

            logger.info("[LocationEditor] Embedded map initialized successfully")

        except Exception as e:
            logger.warning(f"[LocationEditor] Failed to initialize map: {e}")
            error_label = QLabel(f"⚠️ Map view unavailable: {e}")
            error_label.setStyleSheet("color: #d93025; padding: 8px;")
            map_layout.addWidget(error_label)
            self.map_view = None

        map_group.setLayout(map_layout)
        layout.addWidget(map_group)

    def _generate_leaflet_html(self) -> str:
        """
        Generate Leaflet.js HTML for embedded map.

        Returns:
            HTML string with Leaflet.js map and draggable marker
        """
        # Get initial coordinates (default to San Francisco if not set)
        init_lat = self.current_lat if self.current_lat else 37.7749
        init_lon = self.current_lon if self.current_lon else -122.4194
        init_zoom = 13 if self.current_lat else 10

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Location Map</title>

    <!-- Leaflet.js CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />

    <!-- QWebChannel for Python ↔ JS communication -->
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>

    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }}
        #map {{
            width: 100%;
            height: 250px;
        }}
        .coord-display {{
            position: absolute;
            top: 10px;
            right: 10px;
            background: white;
            padding: 8px 12px;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            z-index: 1000;
            font-size: 11px;
            font-family: monospace;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="coord-display" id="coordDisplay">
        <b>📍 Marker:</b><br>
        Lat: <span id="markerLat">{init_lat:.6f}</span><br>
        Lon: <span id="markerLon">{init_lon:.6f}</span>
    </div>

    <!-- Leaflet.js JavaScript -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

    <script>
        // Initialize map
        var map = L.map('map').setView([{init_lat}, {init_lon}], {init_zoom});

        // Add OpenStreetMap tiles
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
            maxZoom: 19
        }}).addTo(map);

        // Add draggable marker
        var marker = L.marker([{init_lat}, {init_lon}], {{
            draggable: true,
            title: "Drag me to set location"
        }}).addTo(map);

        // Popup
        marker.bindPopup("<b>GPS Location</b><br>Drag marker to adjust").openPopup();

        // Update coordinates display
        function updateCoordDisplay(lat, lng) {{
            document.getElementById('markerLat').textContent = lat.toFixed(6);
            document.getElementById('markerLon').textContent = lng.toFixed(6);
        }}

        // Handle marker drag
        marker.on('dragend', function(e) {{
            var pos = marker.getLatLng();
            updateCoordDisplay(pos.lat, pos.lng);

            // Send coordinates to Python
            if (window.pyHandler) {{
                window.pyHandler.updateCoordinatesFromMap(pos.lat, pos.lng);
            }}
        }});

        // Function to update marker from Python (when user types coordinates)
        function updateMarkerPosition(lat, lng) {{
            var newPos = L.latLng(lat, lng);
            marker.setLatLng(newPos);
            map.panTo(newPos);
            updateCoordDisplay(lat, lng);
        }}

        // Set up QWebChannel for Python communication
        new QWebChannel(qt.webChannelTransport, function(channel) {{
            window.pyHandler = channel.objects.pyHandler;

            // Expose function to Python
            window.updateMarkerPosition = updateMarkerPosition;
        }});
    </script>
</body>
</html>
"""
        return html

    def _on_coordinates_changed(self):
        """
        Handle coordinate input changes - update map marker.

        Called when user types latitude or longitude in text fields.
        Updates the map marker position accordingly.
        """
        if not hasattr(self, 'map_view') or not self.map_view:
            return

        try:
            lat_text = self.lat_input.text().strip()
            lon_text = self.lon_input.text().strip()

            if not lat_text or not lon_text:
                return

            lat = float(lat_text)
            lon = float(lon_text)

            # Validate range
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return

            # Update map marker via JavaScript
            js_code = f"if (typeof updateMarkerPosition === 'function') {{ updateMarkerPosition({lat}, {lon}); }}"
            self.map_view.page().runJavaScript(js_code)

        except (ValueError, AttributeError):
            # Invalid input or map not ready - ignore
            pass

    def _on_map_coordinates_changed(self, lat: float, lon: float):
        """
        Handle coordinates changed from map marker drag (via MapHandler signal).

        Args:
            lat: Latitude from dragged marker
            lon: Longitude from dragged marker
        """
        # Temporarily disconnect signals to avoid feedback loop
        self.lat_input.textChanged.disconnect(self._on_coordinates_changed)
        self.lon_input.textChanged.disconnect(self._on_coordinates_changed)

        # Update text fields
        self.lat_input.setText(f"{lat:.6f}")
        self.lon_input.setText(f"{lon:.6f}")

        # Reconnect signals
        self.lat_input.textChanged.connect(self._on_coordinates_changed)
        self.lon_input.textChanged.connect(self._on_coordinates_changed)

        logger.info(f"[LocationEditor] Coordinates updated from map: ({lat:.6f}, {lon:.6f})")

        # FIX: Auto-geocode after 1 second of inactivity (debounce)
        # This prevents spamming geocoding API while user is dragging
        self.pending_geocode_coords = (lat, lon)
        self.geocode_timer.stop()  # Cancel any pending geocode
        self.geocode_timer.start(1000)  # Wait 1 second after last drag

    def _auto_geocode_from_map(self):
        """
        Auto-geocode coordinates from map marker drag (debounced).

        Called 1 second after user stops dragging marker.
        Automatically populates location name field.
        """
        if not self.pending_geocode_coords:
            return

        lat, lon = self.pending_geocode_coords
        logger.info(f"[LocationEditor] Auto-geocoding map coordinates: ({lat:.6f}, {lon:.6f})")

        try:
            # Import geocoding service
            from services.geocoding_service import reverse_geocode

            # Geocode coordinates to location name
            location_name = reverse_geocode(lat, lon)

            if location_name:
                # Update location name field
                self.name_input.setText(location_name)
                logger.info(f"[LocationEditor] Auto-geocoded: ({lat:.6f}, {lon:.6f}) → {location_name}")
            else:
                logger.warning(f"[LocationEditor] Auto-geocode returned no result for ({lat:.6f}, {lon:.6f})")

        except Exception as e:
            logger.warning(f"[LocationEditor] Auto-geocode failed: {e}")
            # Don't show error to user - they can manually geocode if needed


# Standalone testing
if __name__ == '__main__':
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Test with existing location
    dialog = LocationEditorDialog(
        photo_path="/path/to/photo.jpg",
        current_lat=37.7749,
        current_lon=-122.4194,
        current_name="San Francisco, California, USA"
    )

    dialog.locationSaved.connect(
        lambda lat, lon, name: print(f"Location saved: ({lat}, {lon}) - {name}")
    )

    dialog.show()
    sys.exit(app.exec())

