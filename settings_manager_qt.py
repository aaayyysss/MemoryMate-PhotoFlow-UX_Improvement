# settings_manager_qt.py
# Version 09.16.01.02 dated 20260322
#

import json, os
import warnings
import logging
from app_env import app_path


SETTINGS_FILE = app_path("photo_app_settings.json")

DEFAULT_SETTINGS = {
    "skip_unchanged_photos": True,  # ✅ incremental scanning
    "use_exif_for_date": True,
    "dark_mode": False,
    "language": "en",  # Language code (en, ar, es, etc.)
    "thumbnail_cache_enabled": True,
    "cache_size_mb": 500,
    "show_decoder_warnings": False,  # if True, Qt + Pillow warnings are visible
    "db_debug_logging": False,
    "show_sql_queries": False,
    "use_cache_warmup": True,   # 👈 new toggle, on by default
    "cache_auto_cleanup": True,  # 👈 added new default
    "ffprobe_path": "",  # Custom path to ffprobe executable (empty = use system PATH)

    # Scan exclusions (folders to skip during photo scanning)
    # Empty list = use platform-specific defaults from PhotoScanService
    # Non-empty list = override defaults with custom exclusions
    # Used by: preferences_dialog.py (UI), photo_scan_service.py (scanning)
    "ignore_folders": [],  # Example: ["node_modules", ".git", "my_private_folder"]

    # --- Badge overlay settings ---
    "badge_overlays_enabled": True,
    "badge_size_px": 22,
    "badge_shape": "circle",  # circle | rounded | square
    "badge_max_count": 4,
    "badge_shadow": True,

    # --- GPS & Location settings ---
    "gps_clustering_radius_km": 5.0,  # Cluster photos within this radius (1-50 km)
    "gps_reverse_geocoding_enabled": True,  # Auto-fetch location names from coordinates
    "gps_geocoding_timeout_sec": 2.0,  # Timeout for reverse geocoding API calls
    "gps_cache_location_names": True,  # Cache location names to reduce API calls

    # --- Device Detection settings ---
    "device_auto_refresh": False,  # Auto-detect device connections (default: manual refresh only)

    # --- People Groups settings (v9.5.0) ---
    "groups_event_window_seconds": 30,  # Time window for "same event" mode (1-300 seconds)
    "groups_min_confidence": 0.5,  # Minimum face detection confidence for group matching
    "groups_include_videos": False,  # Include video frames in group results (future)
    "groups_auto_recompute": True,  # Auto-recompute group results when face data changes
    "groups_max_results": 1000,  # Max results per group (pagination)

    # --- Screenshot face handling defaults ---
    "screenshot_face_policy": "detect_only",  # exclude | detect_only | include_cluster
    "include_all_screenshot_faces": False,    # if True, do not cap screenshot detections in include_cluster mode
}


# ============================================================
# 🧠 Decoder warning toggle integration (Qt + Pillow)
# ============================================================

def apply_decoder_warning_policy():
    """
    Apply global decoder warning visibility according to settings.
    Called early in app startup (before any Qt GUI creation).
    """
    sm = SettingsManager()
    show_warnings = sm.get("show_decoder_warnings", False)

    if not show_warnings:
        # Silence Qt image I/O warnings globally
        os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio.warning=false"

        # Silence Pillow decompression & ICC noise
        warnings.filterwarnings("ignore", message=".*DecompressionBombWarning.*")
        warnings.filterwarnings("ignore", message=".*invalid rendering intent.*")
        warnings.filterwarnings("ignore", message=".*iCCP.*")
        logging.getLogger("PIL").setLevel(logging.ERROR)

        print("🔇 Decoder warnings suppressed (Qt, Pillow, ICC).")
    else:
        os.environ.pop("QT_LOGGING_RULES", None)
        logging.getLogger("PIL").setLevel(logging.INFO)
        print("⚠️ Decoder warnings ENABLED for debugging.")


class SettingsManager:
    def __init__(self):
        self._data = DEFAULT_SETTINGS.copy()
        self._load()

    def _load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._data.update(data)
            except Exception:
                pass

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print(f"[Settings] Save failed: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def get_setting(self, key, default=None):
        """Alias for get() for compatibility."""
        return self.get(key, default)

    def set_setting(self, key, value):
        """Alias for set() for compatibility."""
        self.set(key, value)

    # ============================================================
    # Recent Locations (Sprint 2 Enhancement)
    # ============================================================

    def get_recent_locations(self, limit: int = 15) -> list[dict]:
        """
        Get recently used GPS locations for quick reuse.

        Returns list of recent locations in reverse chronological order
        (most recent first). Each location is a dict with:
        - name: Location name (str)
        - lat: Latitude (float)
        - lon: Longitude (float)
        - timestamp: Unix timestamp when last used (float)
        - use_count: Number of times this location was used (int)

        Args:
            limit: Maximum number of recent locations to return (default: 15)

        Returns:
            List of location dicts, empty if none saved
        """
        recents = self.get("recent_locations", [])

        # Sort by timestamp (most recent first)
        recents.sort(key=lambda x: x.get('timestamp', 0), reverse=True)

        # Return only requested number
        return recents[:limit]

    def add_recent_location(self, name: str, lat: float, lon: float) -> None:
        """
        Add a location to recent locations list.
        """
        import time

        if not name or lat is None or lon is None:
            return

        recents = self.get("recent_locations", [])

        # Check if location already exists (within 0.001 degree tolerance ≈ 100m)
        tolerance = 0.001
        existing_index = None

        for i, loc in enumerate(recents):
            if (abs(loc.get('lat', 0) - lat) < tolerance and
                abs(loc.get('lon', 0) - lon) < tolerance and
                loc.get('name', '').lower() == name.lower()):
                existing_index = i
                break

        if existing_index is not None:
            # Update existing location: bump timestamp and increment count
            recents[existing_index]['timestamp'] = time.time()
            recents[existing_index]['use_count'] = recents[existing_index].get('use_count', 0) + 1
        else:
            # Add new location
            recents.append({
                'name': name,
                'lat': lat,
                'lon': lon,
                'timestamp': time.time(),
                'use_count': 1
            })

        # Sort by timestamp (most recent first)
        recents.sort(key=lambda x: x.get('timestamp', 0), reverse=True)

        # Keep only last 15 locations (auto-prune)
        recents = recents[:15]

        # Save updated list
        self.set("recent_locations", recents)

    def clear_recent_locations(self) -> None:
        """
        Clear all recent locations.
        """
        self.set("recent_locations", [])


_settings_instance = None


def get_settings():
    """Global singleton getter for SettingsManager."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = SettingsManager()
    return _settings_instance
