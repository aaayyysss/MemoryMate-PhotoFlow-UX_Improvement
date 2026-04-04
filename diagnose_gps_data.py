#!/usr/bin/env python3
"""GPS Data Diagnostic Tool

This script checks the database for GPS data and helps diagnose
why the Locations section might be showing no locations.

Run this to troubleshoot location issues.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def diagnose_gps_data():
    """Run comprehensive GPS data diagnostics."""
    print("=" * 70)
    print("GPS Data Diagnostic Tool")
    print("=" * 70)

    try:
        from reference_db import ReferenceDB
        db = ReferenceDB()
    except Exception as e:
        print(f"\n✗ Failed to connect to database: {e}")
        return False

    # Test 1: Check if GPS columns exist
    print("\n[1] Checking GPS columns in photo_metadata table...")
    try:
        with db._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(photo_metadata)")
            cols = [row[1] for row in cur.fetchall()]

            has_lat = 'gps_latitude' in cols
            has_lon = 'gps_longitude' in cols
            has_name = 'location_name' in cols

            if has_lat and has_lon:
                print(f"   ✓ GPS columns exist:")
                print(f"      - gps_latitude: {has_lat}")
                print(f"      - gps_longitude: {has_lon}")
                print(f"      - location_name: {has_name}")
            else:
                print(f"   ✗ GPS columns missing:")
                print(f"      - gps_latitude: {has_lat}")
                print(f"      - gps_longitude: {has_lon}")
                print(f"\n   Solution: GPS columns will be created automatically when")
                print(f"             photos with GPS EXIF data are scanned.")
                return False
    except Exception as e:
        print(f"   ✗ Error checking columns: {e}")
        return False

    # Test 2: Count total photos in database
    print("\n[2] Checking total photos in database...")
    try:
        with db._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM photo_metadata")
            total_photos = cur.fetchone()[0]
            print(f"   Total photos in database: {total_photos}")

            if total_photos == 0:
                print(f"   ⚠ No photos in database. Import photos first.")
                return False
    except Exception as e:
        print(f"   ✗ Error counting photos: {e}")
        return False

    # Test 3: Count photos with GPS data
    print("\n[3] Checking photos with GPS data...")
    try:
        with db._connect() as conn:
            cur = conn.cursor()

            # Count photos with GPS
            cur.execute("""
                SELECT COUNT(*) FROM photo_metadata
                WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL
            """)
            gps_photos = cur.fetchone()[0]

            print(f"   Photos with GPS data: {gps_photos} / {total_photos}")

            if gps_photos == 0:
                print(f"\n   ⚠ No photos have GPS data.")
                print(f"   Possible reasons:")
                print(f"   1. Photos were taken without GPS/location services")
                print(f"   2. GPS EXIF data was stripped from photos")
                print(f"   3. Photos haven't been scanned for metadata yet")
                print(f"\n   Solutions:")
                print(f"   - Use photos with GPS EXIF data (smartphone photos usually have this)")
                print(f"   - Rescan photos to extract GPS metadata")
                print(f"   - Check if original photos have GPS data using exiftool:")
                print(f"     exiftool -GPS* your_photo.jpg")
                return False

            # Show GPS data percentage
            gps_percent = (gps_photos / total_photos) * 100
            print(f"   GPS coverage: {gps_percent:.1f}%")

            # Sample GPS coordinates
            cur.execute("""
                SELECT path, gps_latitude, gps_longitude, location_name
                FROM photo_metadata
                WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL
                LIMIT 5
            """)
            samples = cur.fetchall()

            print(f"\n   Sample GPS data:")
            for i, (path, lat, lon, name) in enumerate(samples, 1):
                filename = Path(path).name
                location_str = name if name else "No location name"
                print(f"   {i}. {filename}")
                print(f"      Coordinates: ({lat:.4f}, {lon:.4f})")
                print(f"      Location: {location_str}")

    except Exception as e:
        print(f"   ✗ Error checking GPS data: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 4: Check GPS data by project
    print("\n[4] Checking GPS data by project...")
    try:
        with db._connect() as conn:
            cur = conn.cursor()

            # Get all projects
            cur.execute("SELECT id, name, folder FROM projects")
            projects = cur.fetchall()

            if not projects:
                print(f"   ⚠ No projects found")
                return False

            print(f"   Found {len(projects)} project(s):")

            for project_id, project_name, folder in projects:
                # Count photos in project
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata p
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE f.path LIKE ?
                """, (f"{folder}%",))
                project_photos = cur.fetchone()[0]

                # Count GPS photos in project
                cur.execute("""
                    SELECT COUNT(*) FROM photo_metadata p
                    JOIN photo_folders f ON f.id = p.folder_id
                    WHERE p.gps_latitude IS NOT NULL
                      AND p.gps_longitude IS NOT NULL
                      AND f.path LIKE ?
                """, (f"{folder}%",))
                project_gps = cur.fetchone()[0]

                gps_pct = (project_gps / project_photos * 100) if project_photos > 0 else 0

                print(f"\n   Project {project_id}: {project_name}")
                print(f"   Folder: {folder}")
                print(f"   Total photos: {project_photos}")
                print(f"   GPS photos: {project_gps} ({gps_pct:.1f}%)")

                if project_gps == 0 and project_photos > 0:
                    print(f"   ⚠ This project has no GPS data")

    except Exception as e:
        print(f"   ✗ Error checking projects: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 5: Test location clustering
    print("\n[5] Testing location clustering...")
    try:
        # Try to get location clusters (without project filter first)
        clusters = db.get_location_clusters(project_id=None)
        print(f"   Total location clusters (all projects): {len(clusters)}")

        if clusters:
            print(f"\n   Top 5 locations:")
            for i, cluster in enumerate(clusters[:5], 1):
                print(f"   {i}. {cluster['name']}")
                print(f"      Photos: {cluster['count']}")
                print(f"      Coordinates: ({cluster['lat']:.4f}, {cluster['lon']:.4f})")
        else:
            print(f"\n   ⚠ No location clusters found")
            print(f"   Possible reasons:")
            print(f"   1. Photos don't have location names yet (need geocoding)")
            print(f"   2. GPS coordinates are too spread out (increase radius)")
            print(f"   3. Query filtering is too restrictive")

            # Suggest geocoding
            if gps_photos > 0:
                print(f"\n   Solution: Run geocoding to add location names:")
                print(f"   from reference_db import ReferenceDB")
                print(f"   db = ReferenceDB()")
                print(f"   stats = db.batch_geocode_unique_coordinates(project_id=1)")

    except Exception as e:
        print(f"   ✗ Error testing clustering: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 6: Check gps_location_cache
    print("\n[6] Checking GPS location cache...")
    try:
        with db._connect() as conn:
            cur = conn.cursor()

            # Check if cache table exists
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='gps_location_cache'
            """)
            has_cache = cur.fetchone() is not None

            if has_cache:
                cur.execute("SELECT COUNT(*) FROM gps_location_cache")
                cache_count = cur.fetchone()[0]
                print(f"   ✓ Cache table exists")
                print(f"   Cached locations: {cache_count}")

                if cache_count > 0:
                    cur.execute("""
                        SELECT latitude, longitude, location_name, cached_at
                        FROM gps_location_cache
                        ORDER BY cached_at DESC
                        LIMIT 3
                    """)
                    cached = cur.fetchall()
                    print(f"\n   Recent cache entries:")
                    for lat, lon, name, cached_at in cached:
                        print(f"   - ({lat:.4f}, {lon:.4f}) → {name}")
                        print(f"     Cached: {cached_at}")
            else:
                print(f"   ⚠ Cache table doesn't exist yet")
                print(f"   It will be created when geocoding is first used")

    except Exception as e:
        print(f"   ✗ Error checking cache: {e}")
        return False

    print("\n" + "=" * 70)
    print("Diagnostic Summary")
    print("=" * 70)

    if gps_photos > 0:
        if len(clusters) > 0:
            print(f"✓ GPS data found and working correctly")
            print(f"  - {gps_photos} photos with GPS data")
            print(f"  - {len(clusters)} location clusters")
            print(f"\nLocations section should display locations normally.")
        else:
            print(f"⚠ GPS data exists but no location clusters")
            print(f"  - {gps_photos} photos with GPS data")
            print(f"  - 0 location clusters")
            print(f"\nLikely cause: Photos need geocoding (location names)")
            print(f"\nSolution: Run batch geocoding:")
            print(f"  python3 -c \"from reference_db import ReferenceDB; db = ReferenceDB(); stats = db.batch_geocode_unique_coordinates(project_id=1); print(stats)\"")
    else:
        print(f"✗ No GPS data in database")
        print(f"\nPhotos don't have GPS EXIF data.")
        print(f"Use photos taken with location services enabled.")

    return True


if __name__ == '__main__':
    try:
        success = diagnose_gps_data()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Diagnostic failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
