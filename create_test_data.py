#!/usr/bin/env python3
"""
Create test data to verify Folders and Dates tabs work correctly.
"""

from reference_db import ReferenceDB
from datetime import datetime, timedelta

def create_test_data():
    """Create sample folders, dates, and photos for testing."""
    db = ReferenceDB()

    print("Creating test data...")

    # Create a test project
    project_id = db.create_project("Test Project", folder="/test/photos", mode="scan")
    print(f"Created project: ID={project_id}")

    # Create folder hierarchy
    with db._connect() as conn:
        cur = conn.cursor()

        # Root folders
        cur.execute("INSERT INTO photo_folders (name, path, parent_id) VALUES (?, ?, ?)",
                   ("Vacation 2024", "/test/photos/vacation2024", None))
        vacation_id = cur.lastrowid

        cur.execute("INSERT INTO photo_folders (name, path, parent_id) VALUES (?, ?, ?)",
                   ("Family", "/test/photos/family", None))
        family_id = cur.lastrowid

        cur.execute("INSERT INTO photo_folders (name, path, parent_id) VALUES (?, ?, ?)",
                   ("Work", "/test/photos/work", None))
        work_id = cur.lastrowid

        # Subfolders under Vacation
        cur.execute("INSERT INTO photo_folders (name, path, parent_id) VALUES (?, ?, ?)",
                   ("Beach", "/test/photos/vacation2024/beach", vacation_id))
        beach_id = cur.lastrowid

        cur.execute("INSERT INTO photo_folders (name, path, parent_id) VALUES (?, ?, ?)",
                   ("Mountains", "/test/photos/vacation2024/mountains", vacation_id))
        mountains_id = cur.lastrowid

        # Subfolders under Family
        cur.execute("INSERT INTO photo_folders (name, path, parent_id) VALUES (?, ?, ?)",
                   ("Birthdays", "/test/photos/family/birthdays", family_id))
        birthdays_id = cur.lastrowid

        print(f"Created folders: Vacation (ID={vacation_id}), Family (ID={family_id}), Work (ID={work_id})")
        print(f"  Subfolders: Beach (ID={beach_id}), Mountains (ID={mountains_id}), Birthdays (ID={birthdays_id})")

        # Create test photos with dates
        base_date = datetime(2024, 1, 1)
        photo_data = []

        # Add photos to different folders and dates
        folders = [
            (vacation_id, "vacation"),
            (beach_id, "beach"),
            (mountains_id, "mountains"),
            (family_id, "family"),
            (birthdays_id, "birthdays"),
            (work_id, "work")
        ]

        photo_count = 0
        for month in range(1, 13):  # 12 months
            for day in [1, 15]:  # 2 days per month
                for folder_id, folder_name in folders:
                    date = datetime(2024, month, day)
                    date_str = date.strftime("%Y-%m-%d")
                    path = f"/test/photos/{folder_name}/photo_{date_str}_{folder_id}.jpg"

                    cur.execute("""
                        INSERT INTO photo_metadata
                        (path, folder_id, created_date, created_year, created_ts, size_kb, width, height)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (path, folder_id, date_str, 2024, int(date.timestamp()), 100.5, 1920, 1080))

                    photo_count += 1

        # Add some 2025 photos
        for month in [1, 2, 3]:
            for folder_id, folder_name in folders[:3]:  # Only first 3 folders
                date = datetime(2025, month, 15)
                date_str = date.strftime("%Y-%m-%d")
                path = f"/test/photos/{folder_name}/photo_{date_str}_{folder_id}.jpg"

                cur.execute("""
                    INSERT INTO photo_metadata
                    (path, folder_id, created_date, created_year, created_ts, size_kb, width, height)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (path, folder_id, date_str, 2025, int(date.timestamp()), 100.5, 1920, 1080))

                photo_count += 1

        conn.commit()
        print(f"Created {photo_count} test photos")

    # Verify data
    print("\nVerifying test data...")
    folders = db.get_all_folders()
    print(f"  Total folders: {len(folders)}")

    root_folders = db.get_child_folders(None)
    print(f"  Root folders: {len(root_folders)}")
    for folder in root_folders:
        count = db.get_image_count_recursive(folder['id'])
        print(f"    - {folder['name']}: {count} photos (recursive)")

    hierarchy = db.get_date_hierarchy()
    print(f"  Date hierarchy: {len(hierarchy)} years")
    for year in sorted(hierarchy.keys(), reverse=True):
        year_count = db.count_for_year(year)
        months = hierarchy[year]
        print(f"    - {year}: {len(months)} months, {year_count} photos")

    print("\n✅ Test data created successfully!")
    print("You can now test the Folders and Dates tabs in the application.")

if __name__ == "__main__":
    create_test_data()
