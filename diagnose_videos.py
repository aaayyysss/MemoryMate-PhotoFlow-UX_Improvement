#!/usr/bin/env python3
"""
Video Loading Diagnostic Script
Checks why videos aren't showing in the accordion sidebar.
"""

import sqlite3
import sys
import io

# Fix Windows console encoding issues
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def diagnose_videos():
    """Run comprehensive video diagnostics."""

    print("=" * 80)
    print("VIDEO LOADING DIAGNOSTIC")
    print("=" * 80)

    # Connect to database
    try:
        conn = sqlite3.connect('reference_data.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        print("[OK] Connected to reference_data.db\n")
    except Exception as e:
        print(f"[ERROR] Failed to connect to database: {e}")
        return

    # Check 1: Projects
    print("[1] CHECKING PROJECTS:")
    cur.execute("SELECT id, name, folder FROM projects")
    projects = cur.fetchall()
    if projects:
        for p in projects:
            print(f"  - Project {p['id']}: {p['name']} (folder: {p['folder']})")
    else:
        print("  [ERROR] NO PROJECTS FOUND!")
        return

    # Check 2: Video metadata table
    print("\n[2] CHECKING video_metadata TABLE:")
    cur.execute("SELECT COUNT(*) as count FROM video_metadata")
    total_videos = cur.fetchone()['count']
    print(f"  Total videos in video_metadata: {total_videos}")

    if total_videos == 0:
        print("  [ERROR] NO VIDEOS IN video_metadata TABLE!")
        print("  This means videos were NOT inserted during scan.")
        return

    # Check 3: Videos per project
    print("\n[3] VIDEOS PER PROJECT:")
    for p in projects:
        cur.execute("""
            SELECT COUNT(*) as count
            FROM video_metadata
            WHERE project_id = ?
        """, (p['id'],))
        count = cur.fetchone()['count']
        print(f"  Project {p['id']}: {count} videos")

        if count > 0:
            # Show video details
            cur.execute("""
                SELECT path, created_date, date_taken, metadata_status
                FROM video_metadata
                WHERE project_id = ?
                LIMIT 5
            """, (p['id'],))
            videos = cur.fetchall()
            for v in videos:
                print(f"    - {v['path']}")
                print(f"      created_date: {v['created_date']}")
                print(f"      date_taken: {v['date_taken']}")
                print(f"      status: {v['metadata_status']}")

    # Check 4: project_videos table
    print("\n[4] CHECKING project_videos TABLE:")
    cur.execute("SELECT COUNT(*) as count FROM project_videos")
    pv_count = cur.fetchone()['count']
    print(f"  Total entries in project_videos: {pv_count}")

    if pv_count == 0:
        print("  [WARNING] project_videos table is EMPTY!")
        print("  This is OK if videos have no dates (build_video_date_branches finds 0).")

    # Check 5: Test the actual query used by VideoRepository
    print("\n[5] TESTING VideoRepository.get_by_project() QUERY:")
    for p in projects:
        cur.execute("""
            SELECT * FROM video_metadata
            WHERE project_id = ?
            ORDER BY date_taken DESC, path
        """, (p['id'],))
        videos = cur.fetchall()
        print(f"  Project {p['id']}: Query returned {len(videos)} videos")

        if len(videos) > 0:
            print("  [OK] Videos FOUND! They should appear in accordion.")
        else:
            print("  [ERROR] Query returned 0 videos!")
            print("  Checking for path case-sensitivity issues...")

            # Check if videos exist with different case
            cur.execute("""
                SELECT COUNT(*) as count, path
                FROM video_metadata
                GROUP BY LOWER(path)
                HAVING COUNT(*) > 1
            """)
            dupes = cur.fetchall()
            if dupes:
                print("  [WARNING] Found duplicate paths with different casing:")
                for d in dupes:
                    print(f"    - {d['path']} (count: {d['count']})")

    # Check 6: VideoService test
    print("\n[6] TESTING VideoService:")
    try:
        from services.video_service import VideoService
        video_service = VideoService()

        for p in projects:
            videos = video_service.get_videos_by_project(p['id'])
            print(f"  Project {p['id']}: VideoService returned {len(videos)} videos")
            if videos:
                print("  [OK] VideoService working correctly!")
                for i, v in enumerate(videos[:3], 1):
                    print(f"    {i}. {v.get('path', 'N/A')}")
            else:
                print("  [ERROR] VideoService returned EMPTY list!")
    except Exception as e:
        print(f"  [ERROR] VideoService error: {e}")
        import traceback
        traceback.print_exc()

    # Summary
    print("\n" + "=" * 80)
    print("DIAGNOSIS COMPLETE")
    print("=" * 80)
    print("\nIf videos exist in video_metadata but VideoService returns 0,")
    print("the issue is in the VideoRepository/VideoService query logic.")
    print("\nIf video_metadata is empty, videos weren't inserted during scan.")

    conn.close()

if __name__ == "__main__":
    diagnose_videos()
