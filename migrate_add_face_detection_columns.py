#!/usr/bin/env python3
"""
Migration script to add face detection columns to face_crops table.

Adds columns for:
- embedding: Face embedding vector (BLOB)
- confidence: Detection confidence score (REAL)
- bbox_top, bbox_right, bbox_bottom, bbox_left: Bounding box coordinates (INTEGER)

Usage:
    python3 migrate_add_face_detection_columns.py [database_path]

If no database path is provided, uses default 'reference_data.db'
"""

import sqlite3
import sys
from pathlib import Path


def migrate_face_crops_table(db_path: str = "reference_data.db"):
    """
    Add face detection columns to face_crops table.

    Args:
        db_path: Path to the SQLite database file
    """
    db_file = Path(db_path)

    if not db_file.exists():
        print(f"❌ Database not found: {db_path}")
        print("   Please provide a valid database path")
        return False

    print(f"📊 Adding face detection columns to: {db_path}")
    print()

    try:
        conn = sqlite3.connect(str(db_file))
        cur = conn.cursor()

        # Check if face_crops table exists
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='face_crops'
        """)

        if not cur.fetchone():
            print("⚠️  face_crops table not found")
            print("   Creating face_crops table with all columns...")

            # Create table with all columns
            cur.execute("""
                CREATE TABLE IF NOT EXISTS face_crops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    branch_key TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    crop_path TEXT NOT NULL,
                    embedding BLOB,
                    confidence REAL DEFAULT 1.0,
                    bbox_top INTEGER,
                    bbox_right INTEGER,
                    bbox_bottom INTEGER,
                    bbox_left INTEGER,
                    is_representative INTEGER DEFAULT 0,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    UNIQUE(project_id, branch_key, crop_path)
                )
            """)
            print("    ✅ face_crops table created with all columns")

            # Create indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_proj ON face_crops(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_proj_branch ON face_crops(project_id, branch_key)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_image ON face_crops(image_path)")
            print("    ✅ Indexes created")

            conn.commit()
            conn.close()
            print()
            print("✅ Migration completed successfully!")
            return True

        # Check which columns already exist
        cur.execute("PRAGMA table_info(face_crops)")
        existing_columns = {row[1] for row in cur.fetchall()}

        columns_to_add = {
            "embedding": "BLOB",
            "confidence": "REAL DEFAULT 1.0",
            "bbox_top": "INTEGER",
            "bbox_right": "INTEGER",
            "bbox_bottom": "INTEGER",
            "bbox_left": "INTEGER",
        }

        added_count = 0
        for col_name, col_type in columns_to_add.items():
            if col_name not in existing_columns:
                print(f"[{added_count + 1}/6] Adding column: {col_name} ({col_type})")
                try:
                    cur.execute(f"ALTER TABLE face_crops ADD COLUMN {col_name} {col_type}")
                    print(f"    ✅ {col_name} added")
                    added_count += 1
                except sqlite3.OperationalError as e:
                    print(f"    ⚠️  {col_name}: {e}")
            else:
                print(f"[{added_count + 1}/6] Column {col_name} already exists - skipping")

        # Add index on image_path for faster lookups
        print()
        print("[7/7] Adding index on image_path...")
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_face_crops_image ON face_crops(image_path)")
            print("    ✅ idx_face_crops_image created")
        except sqlite3.OperationalError:
            print("    ⚠️  Index already exists")

        # Verify columns were added
        print()
        print("[8/7] Verifying columns...")
        cur.execute("PRAGMA table_info(face_crops)")
        final_columns = {row[1] for row in cur.fetchall()}

        all_present = True
        for col_name in columns_to_add.keys():
            if col_name in final_columns:
                print(f"    ✅ {col_name} verified")
            else:
                print(f"    ❌ {col_name} NOT FOUND")
                all_present = False

        # Commit changes
        conn.commit()
        print()

        if all_present:
            print("✅ Migration completed successfully!")
            print()
            print("Impact:")
            print("  • Face detection worker can now store embeddings and bounding boxes")
            print("  • Face clustering will use stored embeddings for grouping")
            print("  • Face crops can be displayed with bounding box overlays")
        else:
            print("⚠️  Migration completed with warnings - some columns may be missing")

        conn.close()
        return all_present

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Get database path from command line or use default
    db_path = sys.argv[1] if len(sys.argv) > 1 else "reference_data.db"

    success = migrate_face_crops_table(db_path)
    sys.exit(0 if success else 1)
