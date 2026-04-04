#!/usr/bin/env python3
"""
Diagnostic script to audit SQLite WAL files and database configuration.

This script verifies:
1. WAL files (.db-wal, .db-shm) are normal and expected
2. Database is properly configured for WAL mode
3. Transactions are handled correctly
4. WAL checkpointing is working
"""

import sys
from pathlib import Path
import sqlite3
import os

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

def check_wal_files_exist(db_path: str):
    """Check if WAL-related files exist and report their status."""
    print("🔍 Checking WAL-related files...")
    
    wal_file = db_path + "-wal"
    shm_file = db_path + "-shm"
    
    db_exists = os.path.exists(db_path)
    wal_exists = os.path.exists(wal_file)
    shm_exists = os.path.exists(shm_file)
    
    print(f"   Database file (.db):     {'✅ EXISTS' if db_exists else '❌ MISSING'}")
    print(f"   WAL file (.db-wal):       {'✅ EXISTS' if wal_exists else '⭕ ABSENT (normal)'}")
    print(f"   SHM file (.db-shm):       {'✅ EXISTS' if shm_exists else '⭕ ABSENT (normal)'}")
    
    # Check file sizes
    if db_exists:
        db_size = os.path.getsize(db_path)
        print(f"   Database size:            {db_size:,} bytes ({db_size/1024/1024:.2f} MB)")
    
    if wal_exists:
        wal_size = os.path.getsize(wal_file)
        print(f"   WAL file size:            {wal_size:,} bytes ({wal_size/1024:.1f} KB)")
    
    if shm_exists:
        shm_size = os.path.getsize(shm_file)
        print(f"   SHM file size:            {shm_size:,} bytes ({shm_size/1024:.1f} KB)")
    
    return wal_exists, shm_exists

def check_database_wal_mode(db_path: str):
    """Verify database is properly configured for WAL mode."""
    print("\n🔧 Checking database WAL configuration...")
    
    try:
        # Connect to database
        conn = sqlite3.connect(db_path)
        
        # Check journal mode
        cursor = conn.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        print(f"   Journal mode:             {journal_mode.upper()}")
        
        # Check if WAL is active
        wal_active = journal_mode.upper() == "WAL"
        status = "✅ ACTIVE" if wal_active else "❌ NOT ACTIVE"
        print(f"   WAL mode status:          {status}")
        
        # Check synchronous setting
        cursor = conn.execute("PRAGMA synchronous")
        sync_setting = cursor.fetchone()[0]
        sync_names = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
        sync_name = sync_names.get(sync_setting, f"UNKNOWN({sync_setting})")
        print(f"   Synchronous setting:      {sync_name}")
        
        # Check foreign keys
        cursor = conn.execute("PRAGMA foreign_keys")
        fk_enabled = cursor.fetchone()[0] == 1
        status = "✅ ENABLED" if fk_enabled else "❌ DISABLED"
        print(f"   Foreign keys:             {status}")
        
        # Check busy timeout
        cursor = conn.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        print(f"   Busy timeout:             {timeout} ms")
        
        conn.close()
        return wal_active
        
    except Exception as e:
        print(f"   ❌ Error checking database: {e}")
        return False

def check_transactions_work(db_path: str):
    """Test that transactions work properly in WAL mode."""
    print("\n💳 Testing transaction handling...")
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        
        # Start transaction
        conn.execute("BEGIN IMMEDIATE")
        print("   ✅ Transaction started")
        
        # Perform a simple operation
        cursor = conn.execute("SELECT count(*) as cnt FROM sqlite_master WHERE type='table'")
        table_count = cursor.fetchone()['cnt']
        print(f"   ✅ Read operation successful (found {table_count} tables)")
        
        # Commit transaction
        conn.commit()
        print("   ✅ Transaction committed")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"   ❌ Transaction test failed: {e}")
        return False

def check_wal_checkpoint(db_path: str):
    """Check WAL checkpoint status and perform manual checkpoint if needed."""
    print("\n🔄 Checking WAL checkpoint status...")
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Check current WAL status
        cursor = conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        result = cursor.fetchone()
        log_frame, ckpt_frame, busy = result
        
        print(f"   Log frames:               {log_frame}")
        print(f"   Checkpointed frames:      {ckpt_frame}")
        print(f"   Busy status:              {'BUSY' if busy else 'NOT BUSY'}")
        
        # If there are uncheckpointed frames, perform checkpoint
        if log_frame > 0 and ckpt_frame < log_frame:
            print("   ⚠️  WAL has uncommitted changes, performing checkpoint...")
            cursor = conn.execute("PRAGMA wal_checkpoint(FULL)")
            result = cursor.fetchone()
            log_frame_after, ckpt_frame_after, busy_after = result
            
            print(f"   After checkpoint:")
            print(f"     Log frames:             {log_frame_after}")
            print(f"     Checkpointed frames:    {ckpt_frame_after}")
            
            if ckpt_frame_after >= log_frame_after:
                print("   ✅ WAL checkpoint completed successfully")
            else:
                print("   ⚠️  WAL checkpoint incomplete")
        else:
            print("   ✅ WAL is clean (no pending checkpoint)")
            
        conn.close()
        return True
        
    except Exception as e:
        print(f"   ❌ WAL checkpoint check failed: {e}")
        return False

def main():
    """Main diagnostic function."""
    print("SQLite WAL Diagnostic Tool")
    print("=" * 50)
    
    # Check reference_data.db
    db_path = "reference_data.db"
    print(f"\n📁 Checking database: {db_path}")
    
    if not os.path.exists(db_path):
        print("❌ Database file not found!")
        return
    
    # Run all checks
    wal_exists, shm_exists = check_wal_files_exist(db_path)
    wal_active = check_database_wal_mode(db_path)
    transactions_work = check_transactions_work(db_path)
    checkpoint_ok = check_wal_checkpoint(db_path)
    
    # Summary
    print("\n" + "=" * 50)
    print("📋 DIAGNOSTIC SUMMARY")
    print("=" * 50)
    
    if wal_active:
        print("✅ WAL mode is properly configured")
    else:
        print("❌ WAL mode is not active")
    
    if transactions_work:
        print("✅ Transactions work correctly")
    else:
        print("❌ Transaction handling has issues")
    
    if checkpoint_ok:
        print("✅ WAL checkpointing works properly")
    else:
        print("❌ WAL checkpointing has issues")
    
    # WAL file explanation
    print(f"\n📘 WAL FILES EXPLANATION:")
    print(f"   • .db-wal: Write-Ahead Log file (normal in WAL mode)")
    print(f"   • .db-shm: Shared memory file (normal in WAL mode)")
    print(f"   • These files are automatically created and managed by SQLite")
    print(f"   • They improve concurrency and performance")
    print(f"   • They are safe to ignore - NOT corrupted database files")
    
    # Final verdict
    all_good = wal_active and transactions_work and checkpoint_ok
    print(f"\n🎯 VERDICT: {'✅ DATABASE IS HEALTHY' if all_good else '❌ DATABASE ISSUES DETECTED'}")
    
    if all_good:
        print("\n💡 TIPS:")
        print("   • WAL files (.db-wal, .db-shm) are completely normal")
        print("   • They improve database performance and concurrency")
        print("   • You can safely ignore these files in your project")
        print("   • They will be automatically cleaned up by SQLite")
    else:
        print("\n🔧 RECOMMENDATIONS:")
        print("   • Check database connection configuration")
        print("   • Verify PRAGMA settings are correct")
        print("   • Consider restarting the application")

if __name__ == "__main__":
    main()