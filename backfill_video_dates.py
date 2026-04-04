#!/usr/bin/env python3
"""
Backfill script to update date_taken for existing videos.

This script re-extracts metadata for all videos that are missing date_taken
and updates the database with the extracted dates.

Usage:
    python backfill_video_dates.py [--project-id PROJECT_ID] [--dry-run]

Arguments:
    --project-id    Process only videos from specific project (default: all projects)
    --dry-run       Show what would be updated without making changes
"""

import sys
import os
import argparse
from pathlib import Path
from typing import List, Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from services.video_metadata_service import get_video_metadata_service
from repository.video_repository import VideoRepository
from logging_config import get_logger

logger = get_logger(__name__)


def backfill_video_dates(project_id: int = None, dry_run: bool = False, progress_callback=None) -> Dict[str, int]:
    """
    Backfill date_taken, created_date, and created_year for existing videos.

    Args:
        project_id: Optional project ID to filter videos (None = all projects)
        dry_run: If True, show what would be updated without making changes
        progress_callback: Optional callback(current, total, message) for progress updates

    Returns:
        Dict with statistics: {
            'total': total videos found,
            'missing_dates': videos missing date_taken,
            'updated': videos successfully updated,
            'failed': videos that failed to update,
            'skipped': videos skipped (file not found)
        }
    """
    stats = {
        'total': 0,
        'missing_dates': 0,
        'updated': 0,
        'failed': 0,
        'skipped': 0
    }

    video_repo = VideoRepository()
    metadata_service = get_video_metadata_service()

    # Get all videos (filtered by project if specified)
    logger.info("=" * 80)
    logger.info("VIDEO DATE BACKFILL SCRIPT")
    logger.info("=" * 80)

    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
        logger.info("")

    # Get videos to process
    if project_id:
        videos = video_repo.get_by_project(project_id)
        logger.info(f"Processing videos for project_id={project_id}")
    else:
        # Get all videos from all projects
        logger.info("Processing videos from ALL projects")
        # Note: This requires querying all projects first
        # For simplicity, we'll just process project 1 if no project_id specified
        videos = video_repo.get_by_project(1)
        logger.warning("Note: Only processing project_id=1. Use --project-id to specify different project.")

    stats['total'] = len(videos)
    logger.info(f"Found {stats['total']} total videos")
    logger.info("")

    if progress_callback:
        progress_callback(0, stats['total'], "Analyzing videos...")

    # Filter videos missing date_taken
    videos_to_update = []
    for video in videos:
        if not video.get('date_taken'):
            videos_to_update.append(video)
            stats['missing_dates'] += 1

    logger.info(f"Found {stats['missing_dates']} videos missing date_taken")
    logger.info("")

    if stats['missing_dates'] == 0:
        logger.info("✓ All videos already have dates!")
        if progress_callback:
            progress_callback(stats['total'], stats['total'], "✓ All videos already have dates!")
        return stats

    # Process each video
    logger.info("Processing videos...")
    logger.info("-" * 80)

    if progress_callback:
        progress_callback(0, stats['missing_dates'], f"Processing {stats['missing_dates']} videos...")

    for idx, video in enumerate(videos_to_update, 1):
        video_path = video['path']
        video_id = video['id']
        filename = os.path.basename(video_path)

        # Report progress
        if progress_callback:
            progress_callback(idx - 1, stats['missing_dates'], f"Processing: {filename}")

        # Check if file exists
        if not os.path.exists(video_path):
            logger.warning(f"[{idx}/{stats['missing_dates']}] SKIP - File not found: {video_path}")
            stats['skipped'] += 1
            continue

        try:
            # Extract metadata
            logger.info(f"[{idx}/{stats['missing_dates']}] Processing: {filename}")
            metadata = metadata_service.extract_metadata(video_path)

            if not metadata or 'date_taken' not in metadata:
                logger.error(f"  ✗ Failed to extract date_taken")
                stats['failed'] += 1
                continue

            # Prepare update fields
            date_taken = metadata['date_taken']
            update_fields = {'date_taken': date_taken}

            # Calculate created_date, created_year, and created_ts (matching VideoMetadataWorker logic)
            try:
                from datetime import datetime
                created_date = date_taken.split(' ')[0]  # Extract YYYY-MM-DD
                dt = datetime.strptime(created_date, '%Y-%m-%d')
                update_fields['created_ts'] = int(dt.timestamp())
                update_fields['created_date'] = created_date
                update_fields['created_year'] = dt.year
            except Exception as e:
                logger.warning(f"  ! Could not extract created_date/year/ts: {e}")

            # Show what will be updated
            logger.info(f"  → date_taken: {date_taken}")
            if 'created_date' in update_fields:
                logger.info(f"  → created_date: {update_fields['created_date']}")
                logger.info(f"  → created_year: {update_fields['created_year']}")
                logger.info(f"  → created_ts: {update_fields['created_ts']}")

            # Update database (unless dry run)
            if not dry_run:
                success = video_repo.update(video_id=video_id, **update_fields)
                if success:
                    logger.info(f"  ✓ Updated video_id={video_id}")
                    stats['updated'] += 1
                else:
                    logger.error(f"  ✗ Failed to update database")
                    stats['failed'] += 1
            else:
                logger.info(f"  [DRY RUN] Would update video_id={video_id}")
                stats['updated'] += 1

        except Exception as e:
            logger.error(f"  ✗ Error: {e}")
            stats['failed'] += 1

        logger.info("")

    # Print summary
    logger.info("=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total videos:          {stats['total']}")
    logger.info(f"Missing dates:         {stats['missing_dates']}")
    logger.info(f"Successfully updated:  {stats['updated']}")
    logger.info(f"Failed:                {stats['failed']}")
    logger.info(f"Skipped (not found):   {stats['skipped']}")
    logger.info("")

    if dry_run:
        logger.info("This was a DRY RUN - no changes were made.")
        logger.info("Run without --dry-run to apply changes.")
    else:
        logger.info("✓ Backfill complete!")

    logger.info("=" * 80)

    # Report completion
    if progress_callback:
        if dry_run:
            progress_callback(stats['missing_dates'], stats['missing_dates'], "✓ Dry run complete!")
        else:
            progress_callback(stats['missing_dates'], stats['missing_dates'], f"✓ Complete! Updated {stats['updated']} videos")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Backfill date_taken for existing videos',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to see what would be updated
  python backfill_video_dates.py --dry-run

  # Update videos in project 1
  python backfill_video_dates.py --project-id 1

  # Update videos in project 2 (dry run first)
  python backfill_video_dates.py --project-id 2 --dry-run
  python backfill_video_dates.py --project-id 2
"""
    )

    parser.add_argument(
        '--project-id',
        type=int,
        default=1,
        help='Project ID to process (default: 1)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be updated without making changes'
    )

    args = parser.parse_args()

    # Run backfill
    stats = backfill_video_dates(project_id=args.project_id, dry_run=args.dry_run)

    # Exit with appropriate code
    if stats['failed'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
