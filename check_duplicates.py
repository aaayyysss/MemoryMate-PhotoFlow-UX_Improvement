#!/usr/bin/env python3
"""Quick script to check for duplicate paths in photo_metadata table."""

import sqlite3
import os

db_path = "reference_data.db"

if not os.path.exists(db_path):
    print(f"Database not found: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check total vs unique paths
cur.execute("SELECT COUNT(*) as total FROM photo_metadata")
total = cur.fetchone()['total']

cur.execute("SELECT COUNT(DISTINCT path) as unique_paths FROM photo_metadata")
unique = cur.fetchone()['unique_paths']

print(f"Total rows in photo_metadata: {total}")
print(f"Unique paths: {unique}")
print(f"Duplicates: {total - unique}")

if total != unique:
    print("\n=== Finding duplicate paths ===")
    cur.execute("""
        SELECT path, COUNT(*) as cnt
        FROM photo_metadata
        GROUP BY path
        HAVING cnt > 1
        ORDER BY cnt DESC
        LIMIT 20
    """)

    for row in cur.fetchall():
        print(f"  {row['path']} - {row['cnt']} times")

    # Check if paths differ only in slash direction or case
    print("\n=== Checking for path normalization issues ===")
    cur.execute("SELECT id, path FROM photo_metadata ORDER BY path")
    all_paths = cur.fetchall()

    normalized_map = {}
    for row in all_paths:
        path = row['path']
        # Normalize: lowercase, forward slashes
        norm = os.path.normcase(os.path.normpath(path)).replace('\\', '/')

        if norm in normalized_map:
            print(f"  DIFFERENT FORMATS:")
            print(f"    Original 1: {normalized_map[norm]}")
            print(f"    Original 2: {path}")
            print(f"    Normalized: {norm}")
        else:
            normalized_map[norm] = path

conn.close()
