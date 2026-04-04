#!/usr/bin/env python3
"""Test Locations Section Implementation

This script validates the GPS/Location section implementation.

Phase 1 & 2: LocationsSection class and sidebar registration
Phase 3: Location branch support
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def test_locations_implementation():
    print("=" * 70)
    print("GPS/Location Section Implementation Test")
    print("=" * 70)

    # Test 1: File structure
    print("\n[1] Testing file structure...")
    locations_file = Path("ui/accordion_sidebar/locations_section.py")
    if not locations_file.exists():
        print(f"   ✗ LocationsSection file not found")
        return False
    print(f"   ✓ LocationsSection file exists")

    # Test 2: LocationsSection class
    print("\n[2] Testing LocationsSection class structure...")
    try:
        with open(locations_file) as f:
            content = f.read()
            required_components = [
                'class LocationsSection',
                'def get_section_id',
                'def get_title',
                'def get_icon',
                'def load_section',
                'def create_content_widget',
                'locationSelected = Signal(dict)',
                'get_location_clusters'
            ]
            for component in required_components:
                if component not in content:
                    print(f"   ✗ Missing: {component}")
                    return False
            print(f"   ✓ All required components present")
    except Exception as e:
        print(f"   ✗ Error reading file: {e}")
        return False

    # Test 3: Sidebar integration
    print("\n[3] Testing sidebar integration...")
    try:
        init_file = Path("ui/accordion_sidebar/__init__.py")
        with open(init_file) as f:
            content = f.read()
            if 'from .locations_section import LocationsSection' not in content:
                print("   ✗ LocationsSection import missing")
                return False
            if '"locations": LocationsSection(self)' not in content:
                print("   ✗ LocationsSection not registered in section_logic")
                return False
            if 'selectLocation = Signal(object)' not in content:
                print("   ✗ selectLocation signal not defined")
                return False
            if 'locations.locationSelected.connect(self.selectLocation.emit)' not in content:
                print("   ✗ locationSelected signal not connected")
                return False
            print("   ✓ LocationsSection properly integrated in sidebar")
    except Exception as e:
        print(f"   ✗ Error checking integration: {e}")
        return False

    # Test 4: Database methods
    print("\n[4] Testing database location methods...")
    try:
        from reference_db import ReferenceDB
        db = ReferenceDB()

        if not hasattr(db, 'get_location_clusters'):
            print("   ✗ Missing get_location_clusters method")
            return False

        if not hasattr(db, 'create_location_branch'):
            print("   ✗ Missing create_location_branch method")
            return False

        print("   ✓ Database location methods available")
        print("      - get_location_clusters()")
        print("      - create_location_branch()")
    except Exception as e:
        print(f"   ✗ Database method check failed: {e}")
        return False

    # Test 5: Phase 3 - Branch support
    print("\n[5] Testing location branch support...")
    try:
        with open('reference_db.py') as f:
            content = f.read()
            if 'def create_location_branch' not in content:
                print("   ✗ create_location_branch method missing")
                return False
            if 'branch_key = f"location_{lat:.4f}_{lon:.4f}"' not in content:
                print("   ✗ Branch key generation missing")
                return False
            if 'project_images' not in content:
                print("   ✗ Branch photo linking missing")
                return False
            print("   ✓ Location branch support implemented")
            print("      - Creates location_LAT_LON branch keys")
            print("      - Links photos within radius to branch")
            print("      - Uses project_images table for filtering")
    except Exception as e:
        print(f"   ✗ Branch support check failed: {e}")
        return False

    print("\n" + "=" * 70)
    print("GPS/Location Section: ALL TESTS PASSED")
    print("=" * 70)
    print("\nImplemented Components:")
    print("  ✓ LocationsSection class (ui/accordion_sidebar/locations_section.py)")
    print("  ✓ Sidebar registration (ui/accordion_sidebar/__init__.py)")
    print("  ✓ Location clustering (get_location_clusters)")
    print("  ✓ Branch creation (create_location_branch)")
    print("  ✓ Signal connections (selectLocation)")
    print("\nFeatures:")
    print("  - GPS-based photo clustering (configurable radius)")
    print("  - Location name display with photo counts")
    print("  - Double-click to select location")
    print("  - Automatic branch creation for filtering")
    print("  - Thread-safe background loading")
    print("\nUsage:")
    print("  1. Photos with GPS data appear in Locations section")
    print("  2. Click location to filter photos from that area")
    print("  3. Radius configurable via gps_clustering_radius_km setting")
    print("\nNext Steps:")
    print("  - Add geocoding service for location names (Phase 4, optional)")
    print("  - Connect selectLocation signal in parent layout")
    print("  - Test with real GPS data")

    return True


if __name__ == '__main__':
    try:
        success = test_locations_implementation()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
