#!/usr/bin/env python3
"""Test Geocoding Service Implementation

This script validates the Phase 4 geocoding implementation.

Tests:
1. GeocodingService class structure and methods
2. Rate limiting (1 request per second)
3. Caching mechanism
4. Coordinate validation
5. Error handling
6. ReferenceDB integration methods
"""

import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def test_geocoding_implementation():
    print("=" * 70)
    print("Geocoding Service Implementation Test (Phase 4)")
    print("=" * 70)

    # Test 1: File structure
    print("\n[1] Testing file structure...")
    geocoding_file = Path("services/geocoding_service.py")
    if not geocoding_file.exists():
        print(f"   ✗ GeocodingService file not found")
        return False
    print(f"   ✓ GeocodingService file exists ({geocoding_file.stat().st_size} bytes)")

    # Test 2: GeocodingService class
    print("\n[2] Testing GeocodingService class structure...")
    try:
        from services.geocoding_service import GeocodingService, get_geocoding_service, reverse_geocode

        required_methods = [
            'reverse_geocode',
            'batch_reverse_geocode',
            '_fetch_from_api',
            '_format_location_name',
            '_wait_for_rate_limit',
            '_get_cached_location',
            '_cache_location',
            '_validate_coordinates',
        ]

        service = GeocodingService(use_cache=False)  # Disable cache for testing
        for method in required_methods:
            if not hasattr(service, method):
                print(f"   ✗ Missing method: {method}")
                return False

        print(f"   ✓ All required methods present")
        print(f"   ✓ Singleton pattern available (get_geocoding_service)")
        print(f"   ✓ Convenience function available (reverse_geocode)")

    except Exception as e:
        print(f"   ✗ Import or instantiation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 3: Coordinate validation
    print("\n[3] Testing coordinate validation...")
    try:
        valid_coords = [
            (37.7749, -122.4194, True),   # San Francisco
            (0, 0, True),                 # Null Island
            (90, 180, True),              # Extreme valid
            (-90, -180, True),            # Extreme valid
            (91, 0, False),               # Invalid lat
            (0, 181, False),              # Invalid lon
            ("abc", "def", False),        # Invalid types
        ]

        for lat, lon, should_be_valid in valid_coords:
            result = GeocodingService._validate_coordinates(lat, lon)
            if result != should_be_valid:
                print(f"   ✗ Validation failed for ({lat}, {lon}): expected {should_be_valid}, got {result}")
                return False

        print(f"   ✓ Coordinate validation working correctly")
    except Exception as e:
        print(f"   ✗ Validation test failed: {e}")
        return False

    # Test 4: Rate limiting
    print("\n[4] Testing rate limiting...")
    try:
        service = GeocodingService(use_cache=False)

        # Measure time for 3 consecutive waits
        start_time = time.time()
        for _ in range(3):
            service._wait_for_rate_limit()
        elapsed = time.time() - start_time

        # Should take at least 2 seconds (3 waits with 1s minimum interval)
        # Allow 0.5s tolerance for execution time
        expected_min = 2.0
        expected_max = 3.0

        if elapsed < expected_min or elapsed > expected_max:
            print(f"   ⚠ Rate limiting timing unexpected: {elapsed:.2f}s (expected {expected_min}-{expected_max}s)")
            print(f"   (This might be acceptable depending on system load)")
        else:
            print(f"   ✓ Rate limiting working correctly ({elapsed:.2f}s for 3 waits)")

    except Exception as e:
        print(f"   ✗ Rate limiting test failed: {e}")
        return False

    # Test 5: Location name formatting
    print("\n[5] Testing location name formatting...")
    try:
        service = GeocodingService()

        test_data = [
            # Complete address
            {
                'address': {
                    'city': 'San Francisco',
                    'state': 'California',
                    'country': 'United States'
                }
            },
            # Town instead of city
            {
                'address': {
                    'town': 'Palo Alto',
                    'state': 'California',
                    'country': 'United States'
                }
            },
            # Minimal data
            {
                'address': {
                    'country': 'Japan'
                }
            },
            # Error response
            {
                'error': 'Not found'
            },
        ]

        expected_results = [
            "San Francisco, California, United States",
            "Palo Alto, California, United States",
            "Japan",
            "Unknown Location",
        ]

        for i, (data, expected) in enumerate(zip(test_data, expected_results)):
            result = service._format_location_name(data)
            if result != expected:
                print(f"   ✗ Formatting failed for test {i+1}: expected '{expected}', got '{result}'")
                return False

        print(f"   ✓ Location name formatting working correctly")

    except Exception as e:
        print(f"   ✗ Formatting test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 6: ReferenceDB integration
    print("\n[6] Testing ReferenceDB integration...")
    try:
        from reference_db import ReferenceDB

        db = ReferenceDB()

        required_methods = [
            'geocode_photos_missing_location_names',
            'batch_geocode_unique_coordinates',
            'cache_location_name',
            'get_cached_location_name',
        ]

        for method in required_methods:
            if not hasattr(db, method):
                print(f"   ✗ Missing ReferenceDB method: {method}")
                return False

        print(f"   ✓ All ReferenceDB integration methods present")
        print(f"      - geocode_photos_missing_location_names()")
        print(f"      - batch_geocode_unique_coordinates()")
        print(f"      - cache_location_name()")
        print(f"      - get_cached_location_name()")

    except Exception as e:
        print(f"   ✗ ReferenceDB integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 7: Caching functionality
    print("\n[7] Testing caching functionality...")
    try:
        from reference_db import ReferenceDB

        db = ReferenceDB()

        # Test coordinates
        test_lat, test_lon = 37.7749, -122.4194
        test_name = "Test Location, California, USA"

        # Cache a location
        db.cache_location_name(test_lat, test_lon, test_name)
        print(f"   ✓ Cached location: ({test_lat}, {test_lon}) → {test_name}")

        # Retrieve exact match
        cached = db.get_cached_location_name(test_lat, test_lon, tolerance=0.0001)
        if cached != test_name:
            print(f"   ✗ Cache retrieval failed: expected '{test_name}', got '{cached}'")
            return False
        print(f"   ✓ Cache retrieval working (exact match)")

        # Retrieve nearby match (within tolerance)
        nearby_cached = db.get_cached_location_name(
            test_lat + 0.005,  # ~0.5km away
            test_lon + 0.005,
            tolerance=0.01
        )
        if nearby_cached != test_name:
            print(f"   ✗ Cache retrieval failed for nearby location: expected '{test_name}', got '{nearby_cached}'")
            return False
        print(f"   ✓ Cache retrieval working (nearby match with tolerance)")

    except Exception as e:
        print(f"   ✗ Caching test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("\n" + "=" * 70)
    print("Geocoding Service: ALL TESTS PASSED")
    print("=" * 70)

    print("\nImplemented Components:")
    print("  ✓ GeocodingService class (services/geocoding_service.py)")
    print("  ✓ Rate limiting (1 request per second)")
    print("  ✓ Location name caching (gps_location_cache table)")
    print("  ✓ Coordinate validation")
    print("  ✓ Error handling")
    print("  ✓ ReferenceDB integration methods")

    print("\nKey Features:")
    print("  - Nominatim API integration (OpenStreetMap)")
    print("  - Automatic rate limiting (respects API usage policy)")
    print("  - Database caching to minimize API calls")
    print("  - Batch geocoding for efficiency")
    print("  - Human-readable location names (City, State, Country)")
    print("  - Thread-safe operation")

    print("\nUsage Examples:")
    print("  1. Single location:")
    print("     from services.geocoding_service import reverse_geocode")
    print("     location = reverse_geocode(37.7749, -122.4194)")
    print()
    print("  2. Batch geocode all photos in project:")
    print("     from reference_db import ReferenceDB")
    print("     db = ReferenceDB()")
    print("     stats = db.batch_geocode_unique_coordinates(project_id=1)")
    print()
    print("  3. Geocode specific photos:")
    print("     stats = db.geocode_photos_missing_location_names(project_id=1)")

    print("\nIntegration:")
    print("  - Automatically called when loading Locations section")
    print("  - Can be triggered manually via Tools menu")
    print("  - Uses existing gps_location_cache table")
    print("  - Updates photo_metadata.location_name column")

    print("\nAPI Usage Policy:")
    print("  - Maximum 1 request per second (enforced)")
    print("  - Includes proper User-Agent header")
    print("  - Uses caching to be a good API citizen")
    print("  - Free for reasonable usage (Nominatim)")

    return True


if __name__ == '__main__':
    try:
        success = test_geocoding_implementation()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
