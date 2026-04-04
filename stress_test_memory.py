#!/usr/bin/env python3
"""
Memory Leak Stress Test for MemoryMate-PhotoFlow
Tests database connections, thumbnail loading, and grid reloads under stress
"""

import sys
import os
import time
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("⚠️ psutil not installed, using gc for memory tracking")
import gc
import tracemalloc
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, Qt
from reference_db import ReferenceDB
from thumbnail_grid_qt import ThumbnailGridQt
from main_window_qt import MainWindow


class StressTestResults:
    """Track stress test metrics."""
    def __init__(self):
        self.start_memory_mb = 0
        self.peak_memory_mb = 0
        self.end_memory_mb = 0
        self.db_connections_created = 0
        self.db_connections_leaked = 0
        self.grid_reloads = 0
        self.thumbnail_loads = 0
        self.errors = []
        
    def report(self):
        """Print test results."""
        print("\n" + "="*80)
        print("🧪 STRESS TEST RESULTS")
        print("="*80)
        print(f"Memory Start:  {self.start_memory_mb:.1f} MB")
        print(f"Memory Peak:   {self.peak_memory_mb:.1f} MB")
        print(f"Memory End:    {self.end_memory_mb:.1f} MB")
        print(f"Memory Growth: {self.end_memory_mb - self.start_memory_mb:.1f} MB")
        print(f"\nGrid Reloads:     {self.grid_reloads}")
        print(f"Thumbnails Loaded: {self.thumbnail_loads}")
        print(f"DB Connections:    {self.db_connections_created}")
        print(f"DB Leaks Detected: {self.db_connections_leaked}")
        print(f"Errors:            {len(self.errors)}")
        
        if self.errors:
            print("\n⚠️ ERRORS:")
            for err in self.errors[:10]:  # Show first 10 errors
                print(f"  - {err}")
        
        # Check for memory leak
        memory_growth = self.end_memory_mb - self.start_memory_mb
        if memory_growth > 100:  # More than 100MB growth
            print(f"\n❌ MEMORY LEAK DETECTED: {memory_growth:.1f} MB growth")
            return False
        elif memory_growth > 50:
            print(f"\n⚠️ WARNING: Moderate memory growth ({memory_growth:.1f} MB)")
            return True
        else:
            print(f"\n✅ PASSED: Memory usage stable ({memory_growth:.1f} MB growth)")
            return True


def get_memory_usage_mb():
    """Get current process memory usage in MB."""
    if HAS_PSUTIL:
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / 1024 / 1024
    else:
        # Fallback: use gc stats (less accurate)
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
        return current / 1024 / 1024


def stress_test_database_connections():
    """
    Test 1: Database Connection Pooling
    Create many ReferenceDB instances and verify connection pooling works.
    """
    print("\n" + "="*80)
    print("TEST 1: Database Connection Pooling Stress Test")
    print("="*80)
    
    results = StressTestResults()
    results.start_memory_mb = get_memory_usage_mb()
    print(f"Starting memory: {results.start_memory_mb:.1f} MB")
    
    # Track connection pool size
    initial_pool_size = len(ReferenceDB._connection_pool)
    print(f"Initial connection pool size: {initial_pool_size}")
    
    print("\n🔄 Creating 100 ReferenceDB instances (should reuse singleton)...")
    db_instances = []
    for i in range(100):
        try:
            db = ReferenceDB()
            db_instances.append(db)
            results.db_connections_created += 1
            
            if (i + 1) % 20 == 0:
                pool_size = len(ReferenceDB._connection_pool)
                memory_mb = get_memory_usage_mb()
                print(f"  [{i+1}/100] Pool size: {pool_size}, Memory: {memory_mb:.1f} MB")
                
                if memory_mb > results.peak_memory_mb:
                    results.peak_memory_mb = memory_mb
                    
        except Exception as e:
            results.errors.append(f"DB creation error: {e}")
    
    # Verify singleton pattern
    print("\n🔍 Verifying singleton pattern...")
    all_same = all(id(db) == id(db_instances[0]) for db in db_instances)
    if all_same:
        print("✅ Singleton working: All instances are the same object")
    else:
        print("❌ Singleton broken: Multiple instances created!")
        results.errors.append("Singleton pattern not working")
    
    # Test connection pooling
    print("\n🔍 Testing connection pool (max 10 connections)...")
    final_pool_size = len(ReferenceDB._connection_pool)
    print(f"Final pool size: {final_pool_size}")
    
    if final_pool_size <= ReferenceDB._max_pool_size:
        print(f"✅ Connection pool within limit ({final_pool_size}/{ReferenceDB._max_pool_size})")
    else:
        print(f"❌ Connection pool exceeded limit ({final_pool_size}/{ReferenceDB._max_pool_size})")
        results.errors.append(f"Connection pool overflow: {final_pool_size} connections")
    
    # Test connection validity
    print("\n🔍 Testing connection validity...")
    try:
        with db._connect() as conn:
            result = conn.execute("SELECT COUNT(*) FROM projects").fetchone()
            print(f"✅ Connection valid: Found {result[0]} projects")
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        results.errors.append(f"Connection validity error: {e}")
    
    # Cleanup
    print("\n🧹 Cleaning up...")
    del db_instances
    
    results.end_memory_mb = get_memory_usage_mb()
    print(f"Ending memory: {results.end_memory_mb:.1f} MB")
    
    return results


def stress_test_grid_reloads(app):
    """
    Test 2: Grid Reload Stress Test
    Rapidly reload grid with different branches to test for memory leaks.
    """
    print("\n" + "="*80)
    print("TEST 2: Grid Reload Stress Test")
    print("="*80)
    
    results = StressTestResults()
    results.start_memory_mb = get_memory_usage_mb()
    print(f"Starting memory: {results.start_memory_mb:.1f} MB")
    
    # Create main window and grid
    print("\n🔧 Creating main window and grid...")
    try:
        main_window = MainWindow()
        grid = main_window.grid
        
        # Get test project and branches
        db = ReferenceDB()
        with db._connect() as conn:
            projects = conn.execute("SELECT id FROM projects LIMIT 1").fetchall()
            if not projects:
                print("❌ No projects found in database!")
                results.errors.append("No test projects available")
                return results
            
            project_id = projects[0][0]
            grid.project_id = project_id
            
            branches = conn.execute(
                "SELECT branch_key FROM branches WHERE project_id = ? LIMIT 5", 
                (project_id,)
            ).fetchall()
            
            if not branches:
                print("❌ No branches found for testing!")
                results.errors.append("No test branches available")
                return results
            
            branch_keys = [b[0] for b in branches]
            print(f"✅ Found {len(branch_keys)} branches for testing")
        
        # Rapid reload test
        print(f"\n🔄 Performing 50 rapid reloads across {len(branch_keys)} branches...")
        for i in range(50):
            try:
                # Cycle through branches
                branch_key = branch_keys[i % len(branch_keys)]
                grid.set_branch(branch_key)
                results.grid_reloads += 1
                
                # Process events to simulate real usage
                QApplication.processEvents()
                
                if (i + 1) % 10 == 0:
                    memory_mb = get_memory_usage_mb()
                    print(f"  [{i+1}/50] Reloads: {results.grid_reloads}, Memory: {memory_mb:.1f} MB")
                    
                    if memory_mb > results.peak_memory_mb:
                        results.peak_memory_mb = memory_mb
                        
            except Exception as e:
                results.errors.append(f"Reload {i+1} error: {e}")
                print(f"  ⚠️ Reload {i+1} failed: {e}")
        
        print("\n✅ Reload stress test completed")
        
        # Cleanup
        print("\n🧹 Cleaning up grid...")
        grid.model.clear()
        main_window.close()
        del grid
        del main_window
        
    except Exception as e:
        print(f"❌ Grid test setup failed: {e}")
        results.errors.append(f"Grid setup error: {e}")
        import traceback
        traceback.print_exc()
    
    results.end_memory_mb = get_memory_usage_mb()
    print(f"Ending memory: {results.end_memory_mb:.1f} MB")
    
    return results


def stress_test_concurrent_operations():
    """
    Test 3: Concurrent Operations Stress Test
    Test multiple threads accessing database simultaneously.
    """
    print("\n" + "="*80)
    print("TEST 3: Concurrent Database Access Stress Test")
    print("="*80)
    
    results = StressTestResults()
    results.start_memory_mb = get_memory_usage_mb()
    print(f"Starting memory: {results.start_memory_mb:.1f} MB")
    
    import threading
    errors = []
    
    def worker_thread(thread_id, iterations=50):
        """Worker thread that performs database operations."""
        try:
            for i in range(iterations):
                db = ReferenceDB()
                with db._connect() as conn:
                    # Perform some queries
                    conn.execute("SELECT COUNT(*) FROM projects").fetchone()
                    conn.execute("SELECT COUNT(*) FROM branches").fetchone()
                    conn.execute("SELECT COUNT(*) FROM photo_metadata LIMIT 10").fetchall()
                    
        except Exception as e:
            errors.append(f"Thread {thread_id} error: {e}")
    
    print("\n🔄 Spawning 10 threads, each performing 50 DB operations...")
    threads = []
    for i in range(10):
        t = threading.Thread(target=worker_thread, args=(i,))
        threads.append(t)
        t.start()
    
    # Wait for all threads
    for i, t in enumerate(threads):
        t.join()
        if (i + 1) % 3 == 0:
            memory_mb = get_memory_usage_mb()
            print(f"  [{i+1}/10] threads completed, Memory: {memory_mb:.1f} MB")
            
            if memory_mb > results.peak_memory_mb:
                results.peak_memory_mb = memory_mb
    
    results.errors.extend(errors)
    
    if not errors:
        print("✅ All threads completed successfully")
    else:
        print(f"⚠️ {len(errors)} thread errors occurred")
    
    # Check connection pool
    pool_size = len(ReferenceDB._connection_pool)
    print(f"\n🔍 Final connection pool size: {pool_size}")
    
    if pool_size <= ReferenceDB._max_pool_size:
        print(f"✅ Pool within limit ({pool_size}/{ReferenceDB._max_pool_size})")
    else:
        print(f"❌ Pool overflow ({pool_size}/{ReferenceDB._max_pool_size})")
        results.errors.append(f"Thread pool overflow: {pool_size}")
    
    results.end_memory_mb = get_memory_usage_mb()
    print(f"Ending memory: {results.end_memory_mb:.1f} MB")
    
    return results


def main():
    """Run all stress tests."""
    print("="*80)
    print("🧪 MemoryMate-PhotoFlow STRESS TEST SUITE")
    print("="*80)
    print(f"Python: {sys.version}")
    print(f"Process PID: {os.getpid()}")
    print(f"Initial Memory: {get_memory_usage_mb():.1f} MB")
    
    # Enable memory tracking
    tracemalloc.start()
    
    # Create QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    
    all_results = []
    
    try:
        # Test 1: Database Connection Pooling
        results1 = stress_test_database_connections()
        all_results.append(("Database Connections", results1))
        
        # Small delay between tests
        time.sleep(2)
        
        # Test 2: Grid Reloads
        results2 = stress_test_grid_reloads(app)
        all_results.append(("Grid Reloads", results2))
        
        # Small delay between tests
        time.sleep(2)
        
        # Test 3: Concurrent Operations
        results3 = stress_test_concurrent_operations()
        all_results.append(("Concurrent DB Access", results3))
        
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    # Cleanup
    print("\n" + "="*80)
    print("🧹 FINAL CLEANUP")
    print("="*80)
    
    print("Closing all database connections...")
    ReferenceDB.close_all_connections()
    
    # Get memory snapshot
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')
    
    print("\n📊 Top 10 Memory Allocations:")
    for stat in top_stats[:10]:
        print(f"  {stat}")
    
    tracemalloc.stop()
    
    # Print summary
    print("\n" + "="*80)
    print("📋 FINAL SUMMARY")
    print("="*80)
    
    passed = 0
    failed = 0
    
    for test_name, results in all_results:
        print(f"\n{test_name}:")
        success = results.report()
        if success:
            passed += 1
        else:
            failed += 1
    
    print("\n" + "="*80)
    print(f"Tests Passed: {passed}/{len(all_results)}")
    print(f"Tests Failed: {failed}/{len(all_results)}")
    
    if failed == 0:
        print("\n✅ ALL TESTS PASSED - No memory leaks detected!")
        return 0
    else:
        print("\n❌ SOME TESTS FAILED - Review results above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
