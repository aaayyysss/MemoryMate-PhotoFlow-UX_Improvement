#!/usr/bin/env python3
"""
Test script to verify build_date_branches() works correctly.
Run this AFTER scanning photos to test the date branch building.
"""
import sys
import sqlite3
from schema_check import ensure_schema_ready

# Ensure database and schema exist before running tests
ensure_schema_ready(required_tables=['photo_metadata', 'photo_folders'])

# Test 1: Check if photos have date_taken values
print("=" * 60)
print("Test 1: Checking photo_metadata for date_taken values")
print("=" * 60)

try:
    conn = sqlite3.connect("reference_data.db")
    cursor = conn.cursor()

    # Count total photos
    cursor.execute("SELECT COUNT(*) FROM photo_metadata")
    total = cursor.fetchone()[0]
    print(f"Total photos in database: {total}")

    if total == 0:
        print("\n⚠️  WARNING: No photos in database!")
        print("Please run a scan first before testing date branches.")
        sys.exit(1)

    # Count photos with date_taken
    cursor.execute("SELECT COUNT(*) FROM photo_metadata WHERE date_taken IS NOT NULL AND date_taken != ''")
    with_dates = cursor.fetchone()[0]
    print(f"Photos with date_taken: {with_dates}")

    # Show sample dates
    cursor.execute("SELECT DISTINCT substr(date_taken, 1, 10) FROM photo_metadata WHERE date_taken IS NOT NULL AND date_taken != '' LIMIT 5")
    sample_dates = [row[0] for row in cursor.fetchall()]
    print(f"Sample dates: {sample_dates}")

    conn.close()

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 2: Run build_date_branches()
print("\n" + "=" * 60)
print("Test 2: Running build_date_branches()")
print("=" * 60)

try:
    from reference_db import ReferenceDB

    db = ReferenceDB()
    print("ReferenceDB initialized")

    branch_count = db.build_date_branches()
    print(f"✅ Created {branch_count} date branch entries")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Verify branches were created
print("\n" + "=" * 60)
print("Test 3: Verifying branches were created")
print("=" * 60)

try:
    conn = sqlite3.connect("reference_data.db")
    cursor = conn.cursor()

    # Count branches
    cursor.execute("SELECT COUNT(*) FROM branches WHERE branch_key LIKE 'by_date:%'")
    branch_count = cursor.fetchone()[0]
    print(f"Date branches in database: {branch_count}")

    # Show sample branches
    cursor.execute("SELECT branch_key, display_name FROM branches WHERE branch_key LIKE 'by_date:%' LIMIT 5")
    branches = cursor.fetchall()
    print(f"\nSample branches:")
    for key, name in branches:
        print(f"  {key} → {name}")

    # Count images linked to branches
    cursor.execute("SELECT COUNT(*) FROM project_images WHERE branch_key LIKE 'by_date:%'")
    image_count = cursor.fetchone()[0]
    print(f"\nImages linked to date branches: {image_count}")

    conn.close()

    if branch_count > 0:
        print("\n✅ SUCCESS: Date branches created successfully!")
    else:
        print("\n❌ FAIL: No date branches were created")
        sys.exit(1)

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("All tests passed!")
print("=" * 60)
