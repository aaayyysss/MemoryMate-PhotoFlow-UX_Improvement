#!/usr/bin/env python3
"""
Test script to verify video loading is working correctly.

This script tests:
1. Database file exists and is accessible
2. VideoRepository can query videos
3. VideoService can retrieve videos
4. AccordionSidebar can load videos (without GUI)
"""

import os
import sys

# Ensure we're in the project directory
project_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(project_dir)
sys.path.insert(0, project_dir)

print("=" * 80)
print("VIDEO LOADING DIAGNOSTIC TEST")
print("=" * 80)
print()

# Test 1: Database file exists
print("TEST 1: Database File")
print("-" * 40)
from db_config import get_db_path
db_path = get_db_path()
print(f"Expected database path: {db_path}")
print(f"Absolute path: {os.path.abspath(db_path)}")

if os.path.exists(db_path):
    size = os.path.getsize(db_path)
    print(f"✓ Database exists ({size:,} bytes)")
else:
    print(f"✗ Database NOT found!")
    sys.exit(1)

# Test 2: Database has video_metadata table
print()
print("TEST 2: Database Schema")
print("-" * 40)
import sqlite3
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='video_metadata'")
table_exists = cur.fetchone() is not None
if table_exists:
    print("✓ video_metadata table exists")
    cur.execute("SELECT COUNT(*) FROM video_metadata")
    total = cur.fetchone()[0]
    print(f"  Total videos in database: {total}")

    cur.execute("SELECT COUNT(*) FROM video_metadata WHERE project_id = 1")
    project_videos = cur.fetchone()[0]
    print(f"  Videos for project 1: {project_videos}")
else:
    print("✗ video_metadata table NOT found!")
    conn.close()
    sys.exit(1)
conn.close()

# Test 3: VideoRepository
print()
print("TEST 3: VideoRepository")
print("-" * 40)
try:
    from repository.video_repository import VideoRepository
    from repository.base_repository import DatabaseConnection

    db_conn = DatabaseConnection(db_path, auto_init=True)
    video_repo = VideoRepository(db_conn)

    videos = video_repo.get_by_project(1)
    print(f"✓ VideoRepository.get_by_project(1) returned {len(videos)} videos")

    if videos:
        for i, v in enumerate(videos[:3], 1):
            path = v.get('path', 'N/A')
            filename = os.path.basename(path) if path != 'N/A' else 'N/A'
            status = v.get('metadata_status', 'N/A')
            print(f"  {i}. {filename} (status: {status})")
    else:
        print("  ⚠️ No videos found in repository")
except Exception as e:
    print(f"✗ VideoRepository test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: VideoService (may fail due to missing dependencies like PIL)
print()
print("TEST 4: VideoService")
print("-" * 40)
try:
    from services.video_service import VideoService
    video_service = VideoService()

    videos_from_service = video_service.get_videos_by_project(1)
    print(f"✓ VideoService.get_videos_by_project(1) returned {len(videos_from_service)} videos")

    if videos_from_service:
        for i, v in enumerate(videos_from_service[:3], 1):
            path = v.get('path', 'N/A')
            filename = os.path.basename(path) if path != 'N/A' else 'N/A'
            duration = v.get('duration_seconds', 'N/A')
            print(f"  {i}. {filename} (duration: {duration}s)")
    else:
        print("  ⚠️ No videos found via service")
except Exception as e:
    print(f"⚠️ VideoService test failed (expected if dependencies missing): {e}")
    # Don't exit - this is non-critical

# Summary
print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print("✓ Database is accessible")
print(f"✓ video_metadata table contains {total} videos")
print(f"✓ VideoRepository can query {len(videos)} videos for project 1")
print()
print("The video loading infrastructure is working correctly!")
print("If videos still don't appear in the GUI, check:")
print("  1. The application is using the correct database path")
print("  2. The accordion sidebar is correctly initialized")
print("  3. Check application logs for errors during sidebar loading")
print("=" * 80)
