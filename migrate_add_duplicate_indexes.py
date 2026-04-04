#!/usr/bin/env python3
"""
Migration script to add performance indexes for duplicate detection.

Adds composite index for JOIN optimization in duplicate detection queries.
"""

import sqlite3
from repository.base_repository import DatabaseConnection

def add_duplicate_detection_indexes():
    """Add indexes to optimize duplicate detection performance."""
    print("Adding performance indexes for duplicate detection...")
    
    db_conn = DatabaseConnection()
    
    # Composite index for JOIN condition optimization
    # Covers: media_instance.asset_id = media_asset.asset_id AND project_id match
    indexes_to_create = [
        """
        CREATE INDEX IF NOT EXISTS idx_media_instance_asset_project 
        ON media_instance(asset_id, project_id);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_media_asset_project_content_hash
        ON media_asset(project_id, content_hash);
        """
    ]
    
    with db_conn.get_connection(read_only=False) as conn:
        for i, index_sql in enumerate(indexes_to_create, 1):
            try:
                conn.execute(index_sql)
                print(f"✅ Created index {i}/{len(indexes_to_create)}")
            except Exception as e:
                print(f"❌ Failed to create index {i}: {e}")
        
        conn.commit()
        print("✅ All indexes created successfully!")

def verify_indexes():
    """Verify that indexes were created successfully."""
    print("\nVerifying indexes...")
    
    db_conn = DatabaseConnection()
    
    with db_conn.get_connection(read_only=True) as conn:
        # Check if our indexes exist
        cursor = conn.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='index' AND name LIKE '%media_instance%' OR name LIKE '%media_asset%'
            ORDER BY name
        """)
        
        indexes = [row[0] for row in cursor.fetchall()]
        print("Found indexes:")
        for idx in indexes:
            print(f"  - {idx}")

if __name__ == "__main__":
    add_duplicate_detection_indexes()
    verify_indexes()
    print("\n✅ Migration complete! Duplicate detection queries should now be faster.")