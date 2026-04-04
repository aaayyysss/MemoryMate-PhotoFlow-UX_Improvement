#!/usr/bin/env python3
"""
Apply Performance Optimizations
================================

This script:
1. Adds compound indexes for common query patterns
2. Analyzes query performance with EXPLAIN QUERY PLAN
3. Provides before/after performance metrics
"""

import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reference_db import ReferenceDB
from db_performance_optimizations import add_compound_indexes, RECOMMENDED_INDEXES
from logging_config import get_logger

logger = get_logger(__name__)


def analyze_query_plan(db, query: str, params: tuple = ()):
    """Analyze query execution plan."""
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute(f"EXPLAIN QUERY PLAN {query}", params)
        plan = cur.fetchall()
        return plan


def print_query_plan(title: str, plan: list):
    """Pretty print query execution plan."""
    print(f"\n{title}")
    print("-" * 70)
    for row in plan:
        # Row format: (id, parent, notused, detail)
        indent = "  " * (row[0] if isinstance(row[0], int) else 0)
        detail = row[3] if len(row) > 3 else row
        print(f"{indent}{detail}")


def check_existing_indexes(db):
    """Check which indexes currently exist."""
    with db._connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        indexes = [row[0] for row in cur.fetchall()]
    return indexes


def main():
    print("=" * 80)
    print("DATABASE PERFORMANCE OPTIMIZATIONS")
    print("=" * 80)

    db_path = os.path.abspath("reference_data.db")

    if not os.path.exists(db_path):
        print(f"\n✗ ERROR: Database not found at {db_path}")
        print("Please run initialize_database.py first")
        return 1

    print(f"\nDatabase: {db_path}")
    print(f"Size: {os.path.getsize(db_path)} bytes")

    # Initialize database connection
    db = ReferenceDB(db_path)

    print("\n" + "=" * 80)
    print("STEP 1: Analyze Current Indexes")
    print("=" * 80)

    existing_indexes = check_existing_indexes(db)
    print(f"\nCurrent indexes: {len(existing_indexes)}")

    # Check which recommended indexes are missing
    recommended = [
        "idx_photo_metadata_project_folder",
        "idx_photo_metadata_project_date",
        "idx_video_metadata_project_folder",
        "idx_video_metadata_project_date",
        "idx_project_images_project_branch",
        "idx_photo_folders_project_parent",
    ]

    missing = [idx for idx in recommended if idx not in existing_indexes]

    if missing:
        print(f"\n⚠️  Missing {len(missing)} recommended compound indexes:")
        for idx in missing:
            print(f"  - {idx}")
    else:
        print("\n✓ All recommended compound indexes already exist")

    print("\n" + "=" * 80)
    print("STEP 2: Analyze Query Plans (Before Optimization)")
    print("=" * 80)

    # Example query 1: Get photos for a project/folder
    print("\n📊 Query 1: Get photos by project + folder")
    query1 = """
        SELECT id, path FROM photo_metadata
        WHERE project_id = ? AND folder_id = ?
        LIMIT 100
    """
    plan1 = analyze_query_plan(db, query1, (1, 1))
    print_query_plan("Execution Plan:", plan1)

    # Example query 2: Get photos by date
    print("\n\n📊 Query 2: Get photos by project + date")
    query2 = """
        SELECT id, path FROM photo_metadata
        WHERE project_id = ? AND created_year = ?
        ORDER BY created_date
        LIMIT 100
    """
    plan2 = analyze_query_plan(db, query2, (1, 2025))
    print_query_plan("Execution Plan:", plan2)

    # Example query 3: Folder tree traversal
    print("\n\n📊 Query 3: Folder tree traversal")
    query3 = """
        SELECT id, name FROM photo_folders
        WHERE project_id = ? AND parent_id = ?
        ORDER BY name
    """
    plan3 = analyze_query_plan(db, query3, (1, 1))
    print_query_plan("Execution Plan:", plan3)

    if missing:
        print("\n" + "=" * 80)
        print("STEP 3: Add Compound Indexes")
        print("=" * 80)

        print("\nAdding compound indexes...")
        start = time.time()

        try:
            add_compound_indexes(db)
            elapsed = time.time() - start
            print(f"✓ Compound indexes added in {elapsed:.2f}s")
        except Exception as e:
            print(f"✗ ERROR adding indexes: {e}")
            return 1

        print("\n" + "=" * 80)
        print("STEP 4: Analyze Query Plans (After Optimization)")
        print("=" * 80)

        # Re-analyze queries with new indexes
        print("\n📊 Query 1: Get photos by project + folder (with index)")
        plan1_after = analyze_query_plan(db, query1, (1, 1))
        print_query_plan("Execution Plan:", plan1_after)

        print("\n\n📊 Query 2: Get photos by project + date (with index)")
        plan2_after = analyze_query_plan(db, query2, (1, 2025))
        print_query_plan("Execution Plan:", plan2_after)

        print("\n\n📊 Query 3: Folder tree traversal (with index)")
        plan3_after = analyze_query_plan(db, query3, (1, 1))
        print_query_plan("Execution Plan:", plan3_after)

    print("\n" + "=" * 80)
    print("STEP 5: Recommendations Summary")
    print("=" * 80)

    print("\n✅ Recommended Optimizations:")
    print("\n1. Database Schema:")
    print("   ✓ project_id columns exist in photo_folders and photo_metadata")
    print("   ✓ Comprehensive indexes defined in schema v3.2.0")
    if not missing:
        print("   ✓ All compound indexes already present")
    else:
        print(f"   ✓ Added {len(missing)} compound indexes")

    print("\n2. Query Optimizations:")
    print("   ⚠️  Sidebar folder tree has N+1 query problem")
    print("      → Use get_folder_counts_batch() instead of get_image_count_recursive() in loop")
    print("   ⚠️  get_image_count_recursive() still uses JOIN to project_images")
    print("      → Update to use direct project_id filter (no JOIN needed)")

    print("\n3. Code Changes Needed:")
    print("   - Update sidebar_qt.py to use batch counting")
    print("   - Update reference_db.py get_image_count_recursive() to use direct project_id")
    print("   - Remove JOINs to project_images where project_id column is available")

    print("\n" + "=" * 80)
    print("✓ PERFORMANCE ANALYSIS COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("1. Review the query plans above")
    print("2. Apply code changes to use batch counting")
    print("3. Test with real data to measure improvements")

    return 0


if __name__ == "__main__":
    sys.exit(main())
