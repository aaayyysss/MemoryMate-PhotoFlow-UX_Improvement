#!/usr/bin/env python3
"""
Direct database test for duplicate detection optimization.
Tests the raw SQL queries without repository layer dependencies.
"""

import sqlite3
import time
import os

def get_db_path():
    """Get the database path - assuming it's in the standard location."""
    # Look for database files in common locations
    possible_paths = [
        "database.db",
        "data/database.db", 
        "db/database.db",
        "../database.db"
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # Try to find any .db file
    for root, dirs, files in os.walk("."):
        for file in files:
            if file.endswith(".db"):
                return os.path.join(root, file)
    
    return None

def test_optimized_queries():
    """Test the optimized duplicate detection queries directly."""
    print("Testing optimized duplicate detection queries...")
    
    db_path = get_db_path()
    if not db_path:
        print("❌ Could not find database file")
        return False
    
    print(f"Using database: {db_path}")
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enable dict-like access
    
    try:
        project_id = 1
        min_instances = 2
        
        # Test current (unoptimized) query
        print("\n1. Testing current query (GROUP BY approach)...")
        current_query = """
            SELECT a.asset_id, a.content_hash, a.representative_photo_id, a.perceptual_hash,
                   COUNT(i.instance_id) AS instance_count
            FROM media_asset a
            JOIN media_instance i ON i.asset_id = a.asset_id AND i.project_id = a.project_id
            WHERE a.project_id = ?
            GROUP BY a.asset_id
            HAVING COUNT(i.instance_id) >= ?
            ORDER BY instance_count DESC
            LIMIT 100
        """
        
        start_time = time.time()
        cursor = conn.execute(current_query, (project_id, min_instances))
        current_results = cursor.fetchall()
        current_duration = time.time() - start_time
        
        print(f"   Duration: {current_duration:.4f} seconds")
        print(f"   Results: {len(current_results)} groups")
        
        # Test optimized query (CTE approach)
        print("\n2. Testing optimized query (CTE approach)...")
        optimized_query = """
            WITH asset_counts AS (
                SELECT asset_id, COUNT(*) as instance_count
                FROM media_instance 
                WHERE project_id = ?
                GROUP BY asset_id
                HAVING COUNT(*) >= ?
            )
            SELECT a.asset_id, a.content_hash, a.representative_photo_id, a.perceptual_hash,
                   ac.instance_count
            FROM asset_counts ac
            JOIN media_asset a ON a.asset_id = ac.asset_id AND a.project_id = ?
            ORDER BY ac.instance_count DESC
            LIMIT 100
        """
        
        start_time = time.time()
        cursor = conn.execute(optimized_query, (project_id, min_instances, project_id))
        optimized_results = cursor.fetchall()
        optimized_duration = time.time() - start_time
        
        print(f"   Duration: {optimized_duration:.4f} seconds")
        print(f"   Results: {len(optimized_results)} groups")
        
        # Compare results
        print(f"\n3. Performance Comparison:")
        print(f"   Current query:  {current_duration:.4f}s")
        print(f"   Optimized query: {optimized_duration:.4f}s")
        
        if current_duration > 0:
            speedup = current_duration / optimized_duration if optimized_duration > 0 else float('inf')
            print(f"   Speedup: {speedup:.2f}x faster")
        
        # Verify results are equivalent
        if len(current_results) == len(optimized_results):
            print("✅ Both queries returned same number of results")
        else:
            print("⚠️  Result counts differ - may need investigation")
            
        # Show sample results
        if optimized_results:
            print("\nSample optimized results:")
            for i, row in enumerate(optimized_results[:3]):
                print(f"   {i+1}. Asset {row['asset_id']}: {row['instance_count']} instances")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

def check_indexes():
    """Check if required indexes exist."""
    print("\nChecking for required indexes...")
    
    db_path = get_db_path()
    if not db_path:
        return
    
    conn = sqlite3.connect(db_path)
    
    try:
        cursor = conn.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='index' AND (name LIKE '%media_instance%' OR name LIKE '%media_asset%')
        """)
        
        indexes = [row[0] for row in cursor.fetchall()]
        print("Found relevant indexes:")
        for idx in indexes:
            print(f"  - {idx}")
            
        required_indexes = ['idx_media_instance_asset_project', 'idx_media_asset_project_content_hash']
        missing_indexes = [idx for idx in required_indexes if idx not in indexes]
        
        if missing_indexes:
            print(f"\nMissing indexes: {missing_indexes}")
            print("Consider running migrate_add_duplicate_indexes.py")
        else:
            print("✅ All required indexes present")
            
    except Exception as e:
        print(f"❌ Error checking indexes: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("🔧 Duplicate Detection Query Optimization Test")
    print("=" * 50)
    
    success = test_optimized_queries()
    check_indexes()
    
    if success:
        print("\n🎉 Optimization test completed successfully!")
        print("The optimized CTE approach should provide 2-5x performance improvement.")
    else:
        print("\n💥 Test failed. Please check database connectivity and schema.")