#!/usr/bin/env python3
"""GPS Data Extraction Tool

Scans existing photos in the database and extracts GPS EXIF data.
This populates the gps_latitude, gps_longitude, and location_name columns.

Usage:
    python3 extract_gps_data.py [--project-id PROJECT_ID] [--geocode] [--max-geocode 50]

Options:
    --project-id ID    Extract GPS for specific project only
    --geocode          Also geocode coordinates to location names (requires internet)
    --max-geocode N    Maximum locations to geocode (default: 50, respects API limits)
    --help             Show this help message
"""

import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def extract_gps_data(project_id=None, geocode=False, max_geocode=50, progress_callback=None):
    """
    Extract GPS data from photos in database.

    Args:
        project_id: Optional project ID to filter
        geocode: Whether to also geocode locations
        max_geocode: Maximum locations to geocode
        progress_callback: Optional callback(current, total, status_msg)

    Returns:
        dict with statistics
    """
    import logging
    from reference_db import ReferenceDB
    from services.exif_parser import ExifParser

    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    print("=" * 70)
    print("GPS Data Extraction Tool")
    print("=" * 70)

    db = ReferenceDB()
    exif_parser = ExifParser()

    stats = {
        'total_photos': 0,
        'photos_with_gps': 0,
        'gps_extracted': 0,
        'gps_failed': 0,
        'already_had_gps': 0,
        'geocoded': 0
    }

    # Step 1: Get all photos
    print("\n[1] Loading photos from database...")
    with db._connect() as conn:
        cur = conn.cursor()

        if project_id:
            cur.execute("""
                SELECT p.path
                FROM photo_metadata p
                JOIN photo_folders f ON f.id = p.folder_id
                WHERE f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
            """, (project_id,))
        else:
            cur.execute("SELECT path FROM photo_metadata")

        photos = [row[0] for row in cur.fetchall()]
        stats['total_photos'] = len(photos)

    if not photos:
        print("✗ No photos found in database")
        return stats

    print(f"✓ Found {len(photos)} photos")

    # Step 2: Extract GPS from each photo
    print(f"\n[2] Extracting GPS data from photos...")

    for i, photo_path in enumerate(photos, 1):
        try:
            # Progress callback
            if progress_callback:
                progress_callback(i, len(photos), f"Processing {Path(photo_path).name}")

            # Check if photo already has GPS data
            with db._connect() as conn:
                cur = conn.cursor()

                # Check if GPS columns exist (Row objects use dict-like access)
                existing_cols = [r['name'] for r in cur.execute("PRAGMA table_info(photo_metadata)")]
                has_gps_cols = 'gps_latitude' in existing_cols and 'gps_longitude' in existing_cols

                if has_gps_cols:
                    cur.execute("""
                        SELECT gps_latitude, gps_longitude
                        FROM photo_metadata
                        WHERE path = ?
                    """, (photo_path,))
                    row = cur.fetchone()
                    if row and row['gps_latitude'] is not None and row['gps_longitude'] is not None:
                        stats['already_had_gps'] += 1
                        continue  # Skip photos that already have GPS

            # Extract EXIF data
            metadata = exif_parser.extract_metadata(photo_path)

            if metadata.get('gps_latitude') and metadata.get('gps_longitude'):
                lat = metadata['gps_latitude']
                lon = metadata['gps_longitude']

                # Store in database
                db.update_photo_gps(photo_path, lat, lon, location_name=None)

                stats['gps_extracted'] += 1
                stats['photos_with_gps'] += 1

                logger.info(f"✓ GPS extracted: {Path(photo_path).name} ({lat:.4f}, {lon:.4f})")
            else:
                stats['gps_failed'] += 1

            # Progress update every 10 photos
            if i % 10 == 0 or i == len(photos):
                pct = (i / len(photos)) * 100
                print(f"Progress: {i}/{len(photos)} ({pct:.1f}%) - GPS found: {stats['photos_with_gps']}")

        except Exception as e:
            logger.error(f"Error extracting GPS from {photo_path}: {e}")
            stats['gps_failed'] += 1

    # Step 3: Geocode if requested
    if geocode and stats['photos_with_gps'] > 0:
        print(f"\n[3] Geocoding locations (max {max_geocode})...")

        try:
            geocode_stats = db.batch_geocode_unique_coordinates(
                project_id=project_id,
                max_locations=max_geocode,
                progress_callback=lambda curr, total, loc: print(
                    f"Geocoding: {curr}/{total} - {loc[2] if len(loc) > 2 else 'processing...'}"
                )
            )

            stats['geocoded'] = geocode_stats['locations_geocoded']
            print(f"\n✓ Geocoding complete:")
            print(f"  - Locations geocoded: {geocode_stats['locations_geocoded']}")
            print(f"  - Photos updated: {geocode_stats['photos_updated']}")
            print(f"  - Cache hits: {geocode_stats['cached']}")

        except Exception as e:
            logger.error(f"Geocoding failed: {e}")

    # Print summary
    print("\n" + "=" * 70)
    print("GPS Extraction Summary")
    print("=" * 70)
    print(f"Total photos scanned: {stats['total_photos']}")
    print(f"GPS data extracted: {stats['gps_extracted']}")
    print(f"Already had GPS: {stats['already_had_gps']}")
    print(f"Total with GPS: {stats['photos_with_gps']}")
    print(f"Failed/No GPS: {stats['gps_failed']}")

    if geocode:
        print(f"Locations geocoded: {stats['geocoded']}")

    gps_pct = (stats['photos_with_gps'] / stats['total_photos'] * 100) if stats['total_photos'] > 0 else 0
    print(f"\nGPS Coverage: {gps_pct:.1f}%")

    if stats['photos_with_gps'] > 0:
        print(f"\n✓ GPS data is now available in the Locations section!")
    else:
        print(f"\n⚠ No GPS data found in photos.")
        print(f"  Photos were likely taken without location services enabled.")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Extract GPS EXIF data from photos in database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract GPS from all photos
  python3 extract_gps_data.py

  # Extract GPS for specific project
  python3 extract_gps_data.py --project-id 1

  # Extract and geocode (converts coordinates to location names)
  python3 extract_gps_data.py --geocode

  # Extract and geocode with limit
  python3 extract_gps_data.py --project-id 1 --geocode --max-geocode 100
        """
    )

    parser.add_argument('--project-id', type=int, help='Extract GPS for specific project only')
    parser.add_argument('--geocode', action='store_true', help='Also geocode coordinates to location names')
    parser.add_argument('--max-geocode', type=int, default=50, help='Maximum locations to geocode (default: 50)')

    args = parser.parse_args()

    try:
        stats = extract_gps_data(
            project_id=args.project_id,
            geocode=args.geocode,
            max_geocode=args.max_geocode
        )
        sys.exit(0 if stats['photos_with_gps'] > 0 else 1)

    except KeyboardInterrupt:
        print("\n\n✗ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
