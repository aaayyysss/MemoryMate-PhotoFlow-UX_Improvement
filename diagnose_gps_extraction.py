#!/usr/bin/env python3
"""
GPS Extraction Diagnostic Script
Tests GPS extraction from photos to identify why GPS data isn't being read.

Usage:
    python diagnose_gps_extraction.py [photo_path]

    If no path provided, will scan current project's photos.
"""

import sys
import os
from pathlib import Path
from PIL import Image, ExifTags
from PIL.ExifTags import GPSTAGS

def convert_gps_to_decimal(gps_coord, ref):
    """Convert GPS coordinates from DMS to decimal degrees."""
    try:
        degrees = float(gps_coord[0])
        minutes = float(gps_coord[1])
        seconds = float(gps_coord[2])

        decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)

        if ref in ['S', 'W']:
            decimal = -decimal

        return decimal
    except (IndexError, TypeError, ValueError) as e:
        print(f"  ⚠️  GPS coordinate conversion failed: {e}")
        return None

def check_photo_gps(photo_path: str, verbose: bool = True):
    """
    Check if photo has GPS data and test extraction.

    Returns:
        tuple: (has_gps, lat, lon, details)
    """
    details = {
        'file': os.path.basename(photo_path),
        'has_exif': False,
        'has_gps_ifd': False,
        'gps_tags': {},
        'extracted_lat': None,
        'extracted_lon': None,
        'errors': []
    }

    try:
        with Image.open(photo_path) as img:
            if verbose:
                print(f"\n{'='*70}")
                print(f"📷 Photo: {os.path.basename(photo_path)}")
                print(f"{'='*70}")

            # Get EXIF data
            exif = img.getexif()
            if not exif:
                if verbose:
                    print("❌ No EXIF data found")
                details['errors'].append("No EXIF data")
                return False, None, None, details

            details['has_exif'] = True
            if verbose:
                print(f"✓ EXIF data found ({len(exif)} tags)")

            # Debug: Print ALL EXIF tags
            if verbose:
                print("\n📋 All EXIF Tags:")
                for tag_id, value in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, f"Unknown({tag_id})")
                    # Skip printing GPSInfo here (will detail it below)
                    if tag_name != 'GPSInfo':
                        # Truncate long values
                        value_str = str(value)
                        if len(value_str) > 60:
                            value_str = value_str[:57] + "..."
                        print(f"  {tag_name}: {value_str}")

            # Find GPS IFD
            gps_ifd = None
            for tag_id, value in exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, tag_id)
                if tag_name == 'GPSInfo':
                    gps_ifd = value
                    break

            if not gps_ifd:
                if verbose:
                    print("\n❌ No GPS IFD (GPSInfo tag) found in EXIF")
                details['errors'].append("No GPSInfo tag")
                return False, None, None, details

            details['has_gps_ifd'] = True
            if verbose:
                print(f"\n✓ GPS IFD found")

            # Convert GPS IFD to readable dictionary
            gps_data = {}
            for tag_id in gps_ifd:
                tag_name = GPSTAGS.get(tag_id, tag_id)
                gps_data[tag_name] = gps_ifd[tag_id]
                details['gps_tags'][tag_name] = gps_ifd[tag_id]

            if verbose:
                print("\n📍 GPS Tags:")
                for tag_name, value in gps_data.items():
                    print(f"  {tag_name}: {value}")

            # Check for required tags
            if 'GPSLatitude' not in gps_data:
                if verbose:
                    print("\n❌ GPSLatitude tag missing")
                details['errors'].append("GPSLatitude missing")
                return False, None, None, details

            if 'GPSLongitude' not in gps_data:
                if verbose:
                    print("\n❌ GPSLongitude tag missing")
                details['errors'].append("GPSLongitude missing")
                return False, None, None, details

            # Extract and convert coordinates
            lat_ref = gps_data.get('GPSLatitudeRef', 'N')
            lon_ref = gps_data.get('GPSLongitudeRef', 'E')

            if verbose:
                print(f"\n🔄 Converting GPS coordinates...")
                print(f"  Latitude (raw): {gps_data['GPSLatitude']} {lat_ref}")
                print(f"  Longitude (raw): {gps_data['GPSLongitude']} {lon_ref}")

            lat = convert_gps_to_decimal(gps_data['GPSLatitude'], lat_ref)
            lon = convert_gps_to_decimal(gps_data['GPSLongitude'], lon_ref)

            if lat is None or lon is None:
                if verbose:
                    print("\n❌ GPS coordinate conversion failed")
                details['errors'].append("Conversion failed")
                return False, None, None, details

            details['extracted_lat'] = lat
            details['extracted_lon'] = lon

            # Validate coordinates
            if not (-90 <= lat <= 90):
                if verbose:
                    print(f"\n❌ Invalid latitude: {lat} (must be -90 to 90)")
                details['errors'].append(f"Invalid latitude: {lat}")
                return False, None, None, details

            if not (-180 <= lon <= 180):
                if verbose:
                    print(f"\n❌ Invalid longitude: {lon} (must be -180 to 180)")
                details['errors'].append(f"Invalid longitude: {lon}")
                return False, None, None, details

            if verbose:
                print(f"\n✅ GPS Coordinates Successfully Extracted:")
                print(f"  Latitude:  {lat:.6f}°")
                print(f"  Longitude: {lon:.6f}°")
                print(f"\n🌍 Google Maps: https://www.google.com/maps?q={lat},{lon}")

            return True, lat, lon, details

    except FileNotFoundError:
        msg = f"File not found: {photo_path}"
        if verbose:
            print(f"\n❌ {msg}")
        details['errors'].append(msg)
        return False, None, None, details

    except Exception as e:
        msg = f"Error processing photo: {e}"
        if verbose:
            print(f"\n❌ {msg}")
        details['errors'].append(msg)
        return False, None, None, details

def scan_project_photos(limit: int = 10):
    """Scan photos in current project for GPS data."""
    # Try to get photos from database
    try:
        from reference_db import ReferenceDatabase
        from settings_manager_qt import SettingsManager

        sm = SettingsManager()
        current_project_id = sm.get("current_project_id")

        if not current_project_id:
            print("⚠️  No current project set")
            return

        print(f"\n{'='*70}")
        print(f"🔍 Scanning Project #{current_project_id} Photos")
        print(f"{'='*70}")

        db = ReferenceDatabase()

        # Get photos from project
        with db._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.path, p.gps_latitude, p.gps_longitude
                FROM photo_metadata p
                JOIN photo_folders f ON f.id = p.folder_id
                WHERE f.path LIKE (SELECT folder || '%' FROM projects WHERE id = ?)
                LIMIT ?
            """, (current_project_id, limit))

            photos = cur.fetchall()

        if not photos:
            print("\n❌ No photos found in current project")
            return

        print(f"\nFound {len(photos)} photos in project (showing first {limit})")

        # Stats
        with_gps_in_db = 0
        with_gps_in_file = 0
        without_gps = 0
        errors = 0

        for idx, row in enumerate(photos, 1):
            photo_path = row[0]
            db_lat = row[1]
            db_lon = row[2]

            print(f"\n{'─'*70}")
            print(f"Photo {idx}/{len(photos)}: {os.path.basename(photo_path)}")
            print(f"{'─'*70}")

            # Check database GPS
            if db_lat is not None and db_lon is not None:
                print(f"✓ GPS in Database: ({db_lat:.6f}, {db_lon:.6f})")
                with_gps_in_db += 1
            else:
                print(f"⚠️  No GPS in Database")

            # Check file GPS
            if os.path.exists(photo_path):
                has_gps, lat, lon, details = check_photo_gps(photo_path, verbose=False)

                if has_gps:
                    print(f"✓ GPS in File EXIF: ({lat:.6f}, {lon:.6f})")
                    with_gps_in_file += 1

                    # Check if database matches file
                    if db_lat is not None and db_lon is not None:
                        if abs(db_lat - lat) > 0.0001 or abs(db_lon - lon) > 0.0001:
                            print(f"⚠️  MISMATCH: Database GPS doesn't match file EXIF!")
                    else:
                        print(f"❌ GPS in file but NOT in database - scan didn't extract it!")
                else:
                    print(f"❌ No GPS in File EXIF")
                    if details['errors']:
                        print(f"   Reason: {', '.join(details['errors'])}")
                    without_gps += 1
            else:
                print(f"⚠️  File not found at: {photo_path}")
                errors += 1

        # Summary
        print(f"\n{'='*70}")
        print(f"📊 Summary")
        print(f"{'='*70}")
        print(f"Total Photos: {len(photos)}")
        print(f"✓ GPS in Database: {with_gps_in_db}")
        print(f"✓ GPS in File EXIF: {with_gps_in_file}")
        print(f"❌ No GPS: {without_gps}")
        print(f"⚠️  Errors: {errors}")

        if with_gps_in_file > with_gps_in_db:
            print(f"\n⚠️  WARNING: {with_gps_in_file - with_gps_in_db} photos have GPS in EXIF but not in database!")
            print(f"   → This indicates GPS extraction is failing during scan")
            print(f"   → Recommendation: Re-scan folder with GPS extraction fixes")

    except Exception as e:
        print(f"\n❌ Error scanning project: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        # Test specific photo
        photo_path = sys.argv[1]
        if not os.path.exists(photo_path):
            print(f"❌ File not found: {photo_path}")
            sys.exit(1)

        has_gps, lat, lon, details = check_photo_gps(photo_path, verbose=True)

        if has_gps:
            print(f"\n{'='*70}")
            print(f"✅ SUCCESS: GPS data extracted successfully")
            print(f"{'='*70}")
            sys.exit(0)
        else:
            print(f"\n{'='*70}")
            print(f"❌ FAILED: Could not extract GPS data")
            print(f"Errors: {', '.join(details['errors'])}")
            print(f"{'='*70}")
            sys.exit(1)
    else:
        # Scan project photos
        scan_project_photos(limit=20)

if __name__ == "__main__":
    main()
