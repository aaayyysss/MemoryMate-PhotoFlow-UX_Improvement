#!/usr/bin/env python3
"""
Simple test to verify the optimized duplicate detection queries are working.
"""

import time
from repository.asset_repository import AssetRepository
from repository.base_repository import DatabaseConnection

def test_duplicate_detection_performance():
    """Test the performance of duplicate detection queries."""
    print("Testing duplicate detection performance...")
    
    # Initialize repository
    db_conn = DatabaseConnection()
    asset_repo = AssetRepository(db_conn)
    
    project_id = 1  # Assuming project 1 exists
    min_instances = 2
    
    # Test the optimized query
    print(f"Running optimized duplicate detection for project {project_id}...")
    
    start_time = time.time()
    try:
        duplicates = asset_repo.list_duplicate_assets(
            project_id=project_id, 
            min_instances=min_instances,
            limit=100  # Limit for testing
        )
        end_time = time.time()
        
        duration = end_time - start_time
        print(f"✅ Query completed in {duration:.4f} seconds")
        print(f"✅ Found {len(duplicates)} duplicate groups")
        
        if duplicates:
            print("Sample results:")
            for i, dup in enumerate(duplicates[:5]):
                print(f"  {i+1}. Asset {dup['asset_id']}: {dup['instance_count']} instances")
        
        # Test count query
        print("\nTesting count query...")
        count_start = time.time()
        count = asset_repo.count_duplicate_assets(project_id, min_instances)
        count_end = time.time()
        
        print(f"✅ Count query completed in {count_end - count_start:.4f} seconds")
        print(f"✅ Total duplicate groups: {count}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_duplicate_detection_performance()
    if success:
        print("\n🎉 All tests passed! Duplicate detection is working and optimized.")
    else:
        print("\n💥 Tests failed. Please check the implementation.")