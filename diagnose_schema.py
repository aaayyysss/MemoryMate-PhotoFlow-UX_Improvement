#!/usr/bin/env python3
"""
Schema Diagnostic Tool
Checks for schema version and project_id column mismatches
"""

import sqlite3
import os

def diagnose_schema(db_path="reference_data.db"):
    """Diagnose database schema version and column configuration"""

    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        return

    print("=" * 80)
    print("SCHEMA DIAGNOSTIC REPORT")
    print("=" * 80)
    print(f"Database: {db_path}\n")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Check schema version
    print("📋 SCHEMA VERSION:")
    try:
        cur.execute("SELECT version, description FROM schema_version ORDER BY applied_at DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            print(f"  Version: {row[0]}")
            print(f"  Description: {row[1]}")
        else:
            print("  ⚠️  No schema_version table found (pre-v2.0.0)")
    except sqlite3.OperationalError:
        print("  ⚠️  No schema_version table found (pre-v2.0.0)")

    print()

    # Check photo_folders schema
    print("📁 PHOTO_FOLDERS TABLE:")
    cur.execute("PRAGMA table_info(photo_folders)")
    columns = cur.fetchall()
    column_names = [col[1] for col in columns]

    print(f"  Columns: {', '.join(column_names)}")

    has_project_id = "project_id" in column_names
    if has_project_id:
        print("  ✅ HAS project_id column (v3.0.0 schema)")
    else:
        print("  ❌ NO project_id column (v2.0.0 schema - GLOBAL table)")

    print()

    # Check photo_metadata schema
    print("📸 PHOTO_METADATA TABLE:")
    cur.execute("PRAGMA table_info(photo_metadata)")
    columns = cur.fetchall()
    column_names = [col[1] for col in columns]

    print(f"  Columns: {', '.join(column_names)}")

    has_project_id_meta = "project_id" in column_names
    if has_project_id_meta:
        print("  ✅ HAS project_id column (v3.0.0 schema)")
    else:
        print("  ❌ NO project_id column (v2.0.0 schema - GLOBAL table)")

    print()

    # Check folder counts
    print("📊 FOLDER DATA:")
    cur.execute("SELECT COUNT(*) FROM photo_folders")
    folder_count = cur.fetchone()[0]
    print(f"  Total folders: {folder_count}")

    if has_project_id:
        cur.execute("SELECT project_id, COUNT(*) FROM photo_folders GROUP BY project_id")
        for row in cur.fetchall():
            print(f"    Project {row[0]}: {row[1]} folders")

    print()

    # Check photo counts
    print("📸 PHOTO DATA:")
    cur.execute("SELECT COUNT(*) FROM photo_metadata")
    photo_count = cur.fetchone()[0]
    print(f"  Total photos: {photo_count}")

    if has_project_id_meta:
        cur.execute("SELECT project_id, COUNT(*) FROM photo_metadata GROUP BY project_id")
        for row in cur.fetchall():
            print(f"    Project {row[0]}: {row[1]} photos")

    print()

    # Check project_images junction table
    print("🔗 PROJECT_IMAGES TABLE (Junction Table):")
    try:
        cur.execute("SELECT COUNT(*) FROM project_images")
        junction_count = cur.fetchone()[0]
        print(f"  Total entries: {junction_count}")

        cur.execute("SELECT project_id, COUNT(*) FROM project_images GROUP BY project_id")
        for row in cur.fetchall():
            print(f"    Project {row[0]}: {row[1]} entries")
    except sqlite3.OperationalError:
        print("  ⚠️  project_images table not found")

    print()

    # Diagnosis
    print("=" * 80)
    print("🔍 DIAGNOSIS:")
    print("=" * 80)

    if not has_project_id and not has_project_id_meta:
        print("✅ SCHEMA v2.0.0 DETECTED (Junction Table Architecture)")
        print()
        print("Your database uses:")
        print("  • photo_folders: GLOBAL table (no project_id)")
        print("  • photo_metadata: GLOBAL table (no project_id)")
        print("  • project_images: Junction table for project filtering")
        print()
        print("⚠️  YOUR CODE MUST NOT filter get_all_folders() by project_id!")
        print()
        print("Check sidebar_qt.py line ~556:")
        print("  ❌ WRONG: rows = self.db.get_all_folders(project_id=self.project_id)")
        print("  ✅ CORRECT: rows = self.db.get_all_folders()")

    elif has_project_id and has_project_id_meta:
        print("✅ SCHEMA v3.0.0 DETECTED (Direct Column Architecture)")
        print()
        print("Your database uses:")
        print("  • photo_folders: Has project_id column")
        print("  • photo_metadata: Has project_id column")
        print("  • Direct filtering: WHERE project_id = ?")
        print()
        print("✅ Your code SHOULD filter get_all_folders() by project_id")

    else:
        print("❌ MIXED SCHEMA DETECTED - INCONSISTENT STATE!")
        print()
        print(f"  • photo_folders has project_id: {has_project_id}")
        print(f"  • photo_metadata has project_id: {has_project_id_meta}")
        print()
        print("⚠️  This is an invalid state! You need to:")
        print("  1. Backup your database")
        print("  2. Either migrate fully to v3.0.0 or revert to v2.0.0")

    print("=" * 80)

    conn.close()

if __name__ == "__main__":
    diagnose_schema("reference_data.db")
