#!/usr/bin/env python3
"""Check if video tables exist in database."""

import sqlite3
import sys
import io

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def check_tables():
    conn = sqlite3.connect('reference_data.db')
    cur = conn.cursor()

    print("=" * 80)
    print("CHECKING VIDEO TABLES IN DATABASE")
    print("=" * 80)

    # Get all tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]

    print(f"\n[1] Total tables in database: {len(tables)}")
    print("\nAll tables:")
    for table in tables:
        print(f"  - {table}")

    # Check for video tables specifically
    video_tables = ['video_metadata', 'project_videos', 'video_tags']
    print(f"\n[2] Checking for video tables:")
    for table in video_tables:
        if table in tables:
            print(f"  [OK] {table} EXISTS")

            # Show schema
            cur.execute(f"PRAGMA table_info({table})")
            columns = cur.fetchall()
            print(f"       Columns ({len(columns)}):")
            for col in columns:
                print(f"         - {col[1]} ({col[2]})")
        else:
            print(f"  [ERROR] {table} MISSING!")

    # Check schema version
    print(f"\n[3] Checking schema version:")
    if 'schema_version' in tables:
        cur.execute("SELECT version, description FROM schema_version ORDER BY version DESC LIMIT 1")
        version = cur.fetchone()
        if version:
            print(f"  Current schema: {version[0]} - {version[1]}")
        else:
            print(f"  [WARNING] schema_version table exists but is EMPTY")
    else:
        print(f"  [WARNING] schema_version table MISSING")

    conn.close()

    print("\n" + "=" * 80)
    print("If video tables are MISSING, you need to run database migration!")
    print("=" * 80)

if __name__ == "__main__":
    check_tables()
