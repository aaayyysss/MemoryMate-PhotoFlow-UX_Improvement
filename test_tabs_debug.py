#!/usr/bin/env python3
"""
Test script to diagnose Folders and Dates tab issues.
"""

from reference_db import ReferenceDB

def test_folders():
    """Test folder retrieval and hierarchy."""
    print("\n" + "=" * 60)
    print("TESTING FOLDERS")
    print("=" * 60)

    db = ReferenceDB()

    # Test 1: Get all folders
    print("\n1. Testing get_all_folders():")
    folders = db.get_all_folders()
    print(f"   Found {len(folders) if folders else 0} folders")
    if folders:
        for i, folder in enumerate(folders[:5]):
            print(f"   [{i}] {folder}")
        if len(folders) > 5:
            print(f"   ... and {len(folders) - 5} more")

    # Test 2: Get root folders (parent_id = None)
    print("\n2. Testing get_child_folders(None) - root folders:")
    root_folders = db.get_child_folders(None)
    print(f"   Found {len(root_folders) if root_folders else 0} root folders")
    if root_folders:
        for i, folder in enumerate(root_folders[:5]):
            print(f"   [{i}] {folder}")
            # Test recursive count
            folder_id = folder.get('id')
            if folder_id and hasattr(db, 'get_image_count_recursive'):
                count = db.get_image_count_recursive(folder_id)
                print(f"       -> Recursive photo count: {count}")

    # Test 3: Get child folders for first root
    if root_folders:
        first_root = root_folders[0]
        first_root_id = first_root.get('id')
        print(f"\n3. Testing get_child_folders({first_root_id}) - children of '{first_root.get('name')}':")
        children = db.get_child_folders(first_root_id)
        print(f"   Found {len(children) if children else 0} child folders")
        if children:
            for i, child in enumerate(children[:3]):
                print(f"   [{i}] {child}")

def test_dates():
    """Test date hierarchy retrieval."""
    print("\n" + "=" * 60)
    print("TESTING DATES")
    print("=" * 60)

    db = ReferenceDB()

    # Test 1: Get date hierarchy
    print("\n1. Testing get_date_hierarchy():")
    if hasattr(db, 'get_date_hierarchy'):
        hierarchy = db.get_date_hierarchy()
        print(f"   Found {len(hierarchy) if hierarchy else 0} years")
        if hierarchy:
            for year in sorted(hierarchy.keys(), reverse=True)[:3]:
                months = hierarchy[year]
                print(f"   Year {year}: {len(months)} months")
                for month in sorted(months.keys(), reverse=True)[:2]:
                    days = months[month]
                    print(f"     Month {month}: {len(days)} days - {days[:3]}")
    else:
        print("   ERROR: get_date_hierarchy() not available")

    # Test 2: Year counts
    print("\n2. Testing count_for_year():")
    if hasattr(db, 'count_for_year'):
        if hierarchy:
            for year in sorted(hierarchy.keys(), reverse=True)[:3]:
                count = db.count_for_year(year)
                print(f"   Year {year}: {count} photos")
    else:
        print("   ERROR: count_for_year() not available")

    # Test 3: Month counts
    print("\n3. Testing count_for_month():")
    if hasattr(db, 'count_for_month'):
        if hierarchy:
            year = list(sorted(hierarchy.keys(), reverse=True))[0]
            months = hierarchy[year]
            for month in sorted(months.keys(), reverse=True)[:3]:
                count = db.count_for_month(year, month)
                print(f"   {year}-{month}: {count} photos")
    else:
        print("   ERROR: count_for_month() not available")

    # Test 4: Day counts
    print("\n4. Testing count_for_day():")
    if hasattr(db, 'count_for_day'):
        if hierarchy:
            year = list(sorted(hierarchy.keys(), reverse=True))[0]
            months = hierarchy[year]
            month = list(sorted(months.keys(), reverse=True))[0]
            days = months[month]
            for day in days[:3]:
                count = db.count_for_day(day)
                print(f"   {day}: {count} photos")
    else:
        print("   ERROR: count_for_day() not available")

def test_database_connection():
    """Test basic database connectivity."""
    print("\n" + "=" * 60)
    print("TESTING DATABASE CONNECTION")
    print("=" * 60)

    db = ReferenceDB()

    # Test 1: Check if database file exists
    print(f"\n1. Database path: {db.db_path if hasattr(db, 'db_path') else 'unknown'}")

    # Test 2: Check photo_folders table
    print("\n2. Checking photo_folders table:")
    try:
        with db._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM photo_folders")
            count = cur.fetchone()[0]
            print(f"   Total folders in database: {count}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # Test 3: Check photo_metadata table
    print("\n3. Checking photo_metadata table:")
    try:
        with db._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM photo_metadata")
            count = cur.fetchone()[0]
            print(f"   Total photos in database: {count}")

            # Check if created_date is populated
            cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE created_date IS NOT NULL")
            date_count = cur.fetchone()[0]
            print(f"   Photos with created_date: {date_count}")
    except Exception as e:
        print(f"   ERROR: {e}")

if __name__ == "__main__":
    test_database_connection()
    test_folders()
    test_dates()
    print("\n" + "=" * 60)
    print("DIAGNOSTICS COMPLETE")
    print("=" * 60 + "\n")
