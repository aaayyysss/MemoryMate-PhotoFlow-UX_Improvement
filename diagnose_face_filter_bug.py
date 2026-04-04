#!/usr/bin/env python3
"""
Diagnostic script for face filter bug.
Identifies which 2 photos are missing from grid display.

Usage:
    python3 diagnose_face_filter_bug.py

This will analyze the reference_data.db database and show exactly which photos
are in face_crops but not appearing in the grid (missing from photo_metadata or project_images).
"""

import sqlite3
import os

# Configuration
DB_PATH = "DBfromTest/reference_data.db"  # User provided database
PROJECT_ID = 1
BRANCH_KEY = "face_003"

def main():
    print("=" * 80)
    print("FACE FILTER BUG DIAGNOSTIC")
    print("=" * 80)
    print(f"Database: {DB_PATH}")
    print(f"Project ID: {PROJECT_ID}")
    print(f"Branch Key: {BRANCH_KEY}")
    print()

    # Check if database exists
    if not os.path.exists(DB_PATH):
        print(f"❌ ERROR: Database not found at {DB_PATH}")
        print(f"   Please ensure the database file is in the correct location.")
        return

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("=" * 80)
    print("QUERY 1: Total photos in face_crops")
    print("=" * 80)

    cur.execute("""
        SELECT COUNT(DISTINCT image_path)
        FROM face_crops
        WHERE project_id = ? AND branch_key = ?
    """, (PROJECT_ID, BRANCH_KEY))

    total_in_face_crops = cur.fetchone()[0]
    print(f"Total photos in face_crops: {total_in_face_crops}")
    print()

    print("=" * 80)
    print("QUERY 2: Photos returned by grid query (with JOINs)")
    print("=" * 80)

    cur.execute("""
        SELECT COUNT(DISTINCT pm.path)
        FROM photo_metadata pm
        JOIN project_images pi ON pm.path = pi.image_path
        WHERE pi.project_id = ?
        AND pm.path IN (
            SELECT DISTINCT image_path
            FROM face_crops
            WHERE project_id = ? AND branch_key = ?
        )
    """, (PROJECT_ID, PROJECT_ID, BRANCH_KEY))

    total_in_grid = cur.fetchone()[0]
    print(f"Photos returned by grid query: {total_in_grid}")
    print()

    missing_count = total_in_face_crops - total_in_grid
    print(f"⚠️  MISMATCH: {missing_count} photos missing from grid")
    print()

    if missing_count == 0:
        print("✅ No discrepancy found! Grid matches face_crops count.")
        conn.close()
        return

    print("=" * 80)
    print(f"QUERY 3: Detailed analysis of ALL {total_in_face_crops} photos")
    print("=" * 80)
    print()

    cur.execute("""
        SELECT
            fc.image_path,
            CASE WHEN pm.path IS NULL THEN '❌ MISSING' ELSE '✅ EXISTS' END as in_photo_metadata,
            CASE WHEN pi.image_path IS NULL THEN '❌ MISSING' ELSE '✅ EXISTS' END as in_project_images,
            CASE WHEN pm.path IS NOT NULL AND pi.image_path IS NOT NULL THEN '✅ SHOWN'
                 ELSE '❌ HIDDEN' END as grid_status
        FROM face_crops fc
        LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
        LEFT JOIN project_images pi ON fc.image_path = pi.image_path AND pi.project_id = fc.project_id
        WHERE fc.project_id = ? AND fc.branch_key = ?
        ORDER BY grid_status DESC, fc.image_path
    """, (PROJECT_ID, BRANCH_KEY))

    rows = cur.fetchall()

    print(f"{'Photo Path':<60} {'Metadata':<15} {'Project':<15} {'Grid Status':<10}")
    print("-" * 110)

    shown_count = 0
    hidden_count = 0
    hidden_photos = []

    for path, metadata, project, status in rows:
        basename = os.path.basename(path)
        short_path = f"...{path[-50:]}" if len(path) > 50 else path

        print(f"{short_path:<60} {metadata:<15} {project:<15} {status:<10}")

        if status == '✅ SHOWN':
            shown_count += 1
        else:
            hidden_count += 1
            hidden_photos.append((path, metadata, project))

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"✅ Photos shown in grid: {shown_count}")
    print(f"❌ Photos hidden from grid: {hidden_count}")
    print()

    if hidden_photos:
        print("=" * 80)
        print(f"MISSING PHOTOS ({len(hidden_photos)} total)")
        print("=" * 80)
        print()

        for i, (path, metadata, project) in enumerate(hidden_photos, 1):
            print(f"{i}. {path}")
            print(f"   - In photo_metadata: {metadata}")
            print(f"   - In project_images: {project}")

            # Check if file exists on disk
            if os.path.exists(path):
                file_size = os.path.getsize(path)
                print(f"   - File on disk: ✅ YES ({file_size:,} bytes)")
                print(f"   - Recommendation: Add to photo_metadata and project_images")
            else:
                print(f"   - File on disk: ❌ NO (deleted or moved)")
                print(f"   - Recommendation: Remove from face_crops (orphaned entry)")
            print()

    print("=" * 80)
    print("ROOT CAUSE ANALYSIS")
    print("=" * 80)
    print()

    if hidden_photos:
        missing_from_metadata = sum(1 for _, m, _ in hidden_photos if m == '❌ MISSING')
        missing_from_project = sum(1 for _, _, p in hidden_photos if p == '❌ MISSING')

        if missing_from_metadata > 0:
            print(f"❌ {missing_from_metadata} photo(s) not in photo_metadata table")
            print("   → Photos were detected by face recognition but never scanned/imported")
            print("   → OR photos were removed from metadata after face detection")
            print()

        if missing_from_project > 0:
            print(f"❌ {missing_from_project} photo(s) not in project_images table")
            print("   → Photos not properly linked to project")
            print("   → OR link was removed after face detection")
            print()

    print("=" * 80)
    print("RECOMMENDED FIX")
    print("=" * 80)
    print()

    files_exist = sum(1 for path, _, _ in hidden_photos if os.path.exists(path))
    files_missing = len(hidden_photos) - files_exist

    if files_exist > 0:
        print(f"✅ {files_exist} missing photo(s) still exist on disk")
        print("   → Recommendation: Re-scan repository to add them to photo_metadata")
        print("   → This will restore the missing photos to the grid")
        print()

    if files_missing > 0:
        print(f"❌ {files_missing} missing photo(s) deleted from disk")
        print("   → Recommendation: Clean up orphaned face_crops entries")
        print("   → Run face clustering again to update counts")
        print()

    print("=" * 80)
    print("SQL FIX QUERIES")
    print("=" * 80)
    print()

    if files_missing > 0:
        print("-- Remove orphaned face_crops entries (photos that don't exist):")
        print(f"""
DELETE FROM face_crops
WHERE id IN (
    SELECT fc.id
    FROM face_crops fc
    LEFT JOIN photo_metadata pm ON fc.image_path = pm.path
    LEFT JOIN project_images pi ON fc.image_path = pi.image_path AND pi.project_id = fc.project_id
    WHERE fc.project_id = {PROJECT_ID}
    AND fc.branch_key = '{BRANCH_KEY}'
    AND (pm.path IS NULL OR pi.image_path IS NULL)
);
        """.strip())
        print()
        print("-- Then re-run face clustering to update counts")
        print()

    conn.close()

    print("=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
