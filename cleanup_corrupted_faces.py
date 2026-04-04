# Database Cleanup Script - Remove Corrupted Faces
# This script removes face crops that don't have valid embeddings
# Run this if app crashes on startup after manual face cropping

import sqlite3
import os
import sys

def cleanup_corrupted_faces():
    """
    Remove face crops without embeddings from database.
    These corrupted records cause app crashes on startup.
    """
    db_path = "reference_data.db"

    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        return False

    print(f"🔍 Scanning database for corrupted face crops...")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Find all face_crops without embeddings in face_branch_reps
    cur.execute("""
        SELECT fc.face_id, fc.image_path, fc.crop_path, fc.branch_key
        FROM face_crops fc
        LEFT JOIN face_branch_reps fbr ON fc.branch_key = fbr.branch_key
        WHERE fc.branch_key LIKE 'manual_%'
        AND (fbr.centroid IS NULL OR fbr.centroid = '')
    """)

    corrupted = cur.fetchall()

    if not corrupted:
        print("✅ No corrupted face crops found!")
        conn.close()
        return True

    print(f"\n⚠️  Found {len(corrupted)} corrupted face crop(s):\n")

    for face_id, image_path, crop_path, branch_key in corrupted:
        print(f"  • {face_id}")
        print(f"    Branch: {branch_key}")
        print(f"    Crop: {crop_path or 'N/A'}")

    response = input(f"\n❓ Delete these {len(corrupted)} corrupted face(s)? [y/N]: ")

    if response.lower() != 'y':
        print("❌ Cleanup cancelled")
        conn.close()
        return False

    # Delete corrupted faces
    deleted_count = 0
    deleted_files = 0

    for face_id, image_path, crop_path, branch_key in corrupted:
        try:
            # Delete from face_crops table
            cur.execute("DELETE FROM face_crops WHERE face_id = ?", (face_id,))

            # Delete from face_branch_reps if exists
            cur.execute("DELETE FROM face_branch_reps WHERE branch_key = ?", (branch_key,))

            deleted_count += 1
            print(f"  ✓ Deleted database record: {face_id}")

            # Delete crop file if exists
            if crop_path and os.path.exists(crop_path):
                os.remove(crop_path)
                deleted_files += 1
                print(f"  ✓ Deleted crop file: {crop_path}")

        except Exception as e:
            print(f"  ✗ Failed to delete {face_id}: {e}")

    conn.commit()
    conn.close()

    print(f"\n✅ Cleanup complete!")
    print(f"  • Deleted {deleted_count} database record(s)")
    print(f"  • Deleted {deleted_files} crop file(s)")
    print(f"\n💡 You can now restart the app - it should work correctly.")

    return True

if __name__ == "__main__":
    print("=" * 60)
    print("  Face Crop Database Cleanup Tool")
    print("=" * 60)
    print("\nThis tool removes corrupted face crops that cause app crashes.")
    print("Corrupted faces have NO embedding and cannot be clustered.\n")

    try:
        success = cleanup_corrupted_faces()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Cleanup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
