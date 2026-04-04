#!/usr/bin/env python3
"""
Database Corruption Audit Script for face_crops Table

Purpose:
    Audit the face_crops table to identify entries where image_path
    incorrectly points to face crop files instead of original photos.

Root Issue:
    Some face_crops entries have image_path containing '/face_crops/',
    which causes crashes when trying to manually crop faces from the
    Face Quality Dashboard.

Usage:
    python scripts/audit_face_crops_corruption.py [--db-path PATH]

Output:
    - Console summary report
    - Detailed report: reports/face_crops_corruption_report.txt
    - CSV export: reports/corrupted_face_crops.csv

Author: Claude Code (MemoryMate PhotoFlow)
Date: 2025-12-18
"""

import sqlite3
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path


class FaceCropsAuditor:
    """Auditor for face_crops database corruption."""

    def __init__(self, db_path: str):
        """
        Initialize auditor.

        Args:
            db_path: Path to SQLite database (photos.db)
        """
        self.db_path = db_path
        self.results = {
            'total_entries': 0,
            'corrupted_entries': 0,
            'corruption_rate': 0.0,
            'corrupted_by_project': {},
            'sample_corrupted': [],
            'recoverable_count': 0,
            'unrecoverable_count': 0,
            'project_images': {}
        }

    def connect(self):
        """Create database connection."""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        return sqlite3.connect(self.db_path)

    def run_audit(self):
        """Execute full audit process."""
        print("=" * 80)
        print("FACE_CROPS DATABASE CORRUPTION AUDIT")
        print("=" * 80)
        print(f"Database: {self.db_path}")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()

        with self.connect() as conn:
            # Check if face_crops table exists
            if not self._check_table_exists(conn):
                print("⚠️  face_crops table does not exist in database")
                print("    This is expected for new projects without face detection")
                return

            # Step 1: Count total entries
            self._count_total_entries(conn)

            if self.results['total_entries'] == 0:
                print("ℹ️  face_crops table is empty")
                print("   No corruption audit needed")
                return

            # Step 2: Identify corrupted entries
            self._identify_corrupted_entries(conn)

            # Step 3: Analyze corruption by project
            self._analyze_by_project(conn)

            # Step 4: Check recoverability
            self._check_recoverability(conn)

            # Step 5: Sample corrupted entries
            self._collect_samples(conn)

        # Generate reports
        self._print_summary()
        self._generate_text_report()
        self._generate_csv_report()

    def _check_table_exists(self, conn) -> bool:
        """Check if face_crops table exists."""
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='face_crops'
        """)
        return cur.fetchone() is not None

    def _count_total_entries(self, conn):
        """Count total face_crops entries."""
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM face_crops")
        self.results['total_entries'] = cur.fetchone()[0]
        print(f"📊 Total face_crops entries: {self.results['total_entries']:,}")

    def _identify_corrupted_entries(self, conn):
        """Identify entries with corrupted image_path."""
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*)
            FROM face_crops
            WHERE image_path LIKE '%/face_crops/%'
               OR image_path LIKE '%\\face_crops\\%'
        """)
        self.results['corrupted_entries'] = cur.fetchone()[0]

        if self.results['total_entries'] > 0:
            self.results['corruption_rate'] = (
                self.results['corrupted_entries'] / self.results['total_entries'] * 100
            )

        print(f"🔴 Corrupted entries: {self.results['corrupted_entries']:,}")
        print(f"   Corruption rate: {self.results['corruption_rate']:.2f}%")
        print()

    def _analyze_by_project(self, conn):
        """Analyze corruption distribution by project."""
        cur = conn.cursor()
        cur.execute("""
            SELECT
                project_id,
                COUNT(*) as total,
                SUM(CASE
                    WHEN image_path LIKE '%/face_crops/%'
                      OR image_path LIKE '%\\face_crops\\%'
                    THEN 1 ELSE 0
                END) as corrupted
            FROM face_crops
            GROUP BY project_id
            ORDER BY corrupted DESC
        """)

        print("📈 Corruption by Project:")
        print("-" * 60)
        print(f"{'Project ID':<12} {'Total':<12} {'Corrupted':<12} {'Rate':<12}")
        print("-" * 60)

        for row in cur.fetchall():
            project_id, total, corrupted = row
            rate = (corrupted / total * 100) if total > 0 else 0
            self.results['corrupted_by_project'][project_id] = {
                'total': total,
                'corrupted': corrupted,
                'rate': rate
            }
            print(f"{project_id:<12} {total:<12} {corrupted:<12} {rate:>6.2f}%")

        print()

    def _check_recoverability(self, conn):
        """Check if corrupted entries can be recovered."""
        cur = conn.cursor()

        # Strategy: Try to parse original photo path from crop filename
        # Pattern: {original_basename}_face{idx}.jpg
        # Example: IMG_1234_face0.jpg -> IMG_1234.jpg

        cur.execute("""
            SELECT
                id,
                project_id,
                image_path,
                crop_path
            FROM face_crops
            WHERE image_path LIKE '%/face_crops/%'
               OR image_path LIKE '%\\face_crops\\%'
        """)

        recoverable = 0
        unrecoverable = 0

        for row in cur.fetchall():
            entry_id, project_id, image_path, crop_path = row

            # Try to infer original photo from crop_path
            if crop_path:
                crop_basename = os.path.basename(crop_path)
                # Remove _faceN.jpg suffix
                if '_face' in crop_basename:
                    potential_original = crop_basename.split('_face')[0]

                    # Check if this basename exists in project_images
                    check_cur = conn.cursor()
                    check_cur.execute("""
                        SELECT image_path
                        FROM project_images
                        WHERE project_id = ?
                          AND image_path LIKE ?
                        LIMIT 1
                    """, (project_id, f"%{potential_original}%"))

                    if check_cur.fetchone():
                        recoverable += 1
                    else:
                        unrecoverable += 1
                else:
                    unrecoverable += 1
            else:
                unrecoverable += 1

        self.results['recoverable_count'] = recoverable
        self.results['unrecoverable_count'] = unrecoverable

        print("🔧 Recovery Feasibility:")
        print(f"   Recoverable: {recoverable:,} ({recoverable/self.results['corrupted_entries']*100:.1f}%)")
        print(f"   Unrecoverable: {unrecoverable:,} ({unrecoverable/self.results['corrupted_entries']*100:.1f}%)")
        print()

    def _collect_samples(self, conn):
        """Collect sample corrupted entries for analysis."""
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                project_id,
                image_path,
                crop_path,
                bbox_x,
                bbox_y,
                bbox_w,
                bbox_h,
                confidence
            FROM face_crops
            WHERE image_path LIKE '%/face_crops/%'
               OR image_path LIKE '%\\face_crops\\%'
            LIMIT 10
        """)

        self.results['sample_corrupted'] = [
            {
                'id': row[0],
                'project_id': row[1],
                'image_path': row[2],
                'crop_path': row[3],
                'bbox_x': row[4],
                'bbox_y': row[5],
                'bbox_w': row[6],
                'bbox_h': row[7],
                'confidence': row[8]
            }
            for row in cur.fetchall()
        ]

    def _print_summary(self):
        """Print audit summary to console."""
        print("=" * 80)
        print("AUDIT SUMMARY")
        print("=" * 80)
        print()

        if self.results['corrupted_entries'] == 0:
            print("✅ No corruption detected!")
            print("   All face_crops entries have valid image_path values.")
        else:
            print("⚠️  CORRUPTION DETECTED")
            print()
            print(f"Total entries:      {self.results['total_entries']:,}")
            print(f"Corrupted entries:  {self.results['corrupted_entries']:,}")
            print(f"Corruption rate:    {self.results['corruption_rate']:.2f}%")
            print()
            print(f"Recoverable:        {self.results['recoverable_count']:,}")
            print(f"Unrecoverable:      {self.results['unrecoverable_count']:,}")
            print()

            print("🔍 Sample Corrupted Entries:")
            print("-" * 80)
            for idx, sample in enumerate(self.results['sample_corrupted'][:5], 1):
                print(f"{idx}. Entry ID: {sample['id']} (Project {sample['project_id']})")
                print(f"   image_path: {sample['image_path'][:70]}...")
                print(f"   crop_path:  {sample['crop_path'][:70] if sample['crop_path'] else 'NULL'}...")
                print()

        print("=" * 80)
        print()

    def _generate_text_report(self):
        """Generate detailed text report."""
        os.makedirs('reports', exist_ok=True)
        report_path = 'reports/face_crops_corruption_report.txt'

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("FACE_CROPS DATABASE CORRUPTION AUDIT REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Database: {self.db_path}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n")

            f.write("EXECUTIVE SUMMARY\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total face_crops entries:     {self.results['total_entries']:,}\n")
            f.write(f"Corrupted entries:            {self.results['corrupted_entries']:,}\n")
            f.write(f"Corruption rate:              {self.results['corruption_rate']:.2f}%\n")
            f.write(f"Recoverable entries:          {self.results['recoverable_count']:,}\n")
            f.write(f"Unrecoverable entries:        {self.results['unrecoverable_count']:,}\n")
            f.write("\n")

            f.write("CORRUPTION BY PROJECT\n")
            f.write("-" * 80 + "\n")
            f.write(f"{'Project ID':<12} {'Total':<12} {'Corrupted':<12} {'Rate':<12}\n")
            f.write("-" * 80 + "\n")
            for proj_id, stats in self.results['corrupted_by_project'].items():
                f.write(f"{proj_id:<12} {stats['total']:<12} {stats['corrupted']:<12} "
                       f"{stats['rate']:>6.2f}%\n")
            f.write("\n")

            f.write("SAMPLE CORRUPTED ENTRIES (First 10)\n")
            f.write("-" * 80 + "\n")
            for idx, sample in enumerate(self.results['sample_corrupted'], 1):
                f.write(f"\n{idx}. Entry ID: {sample['id']}\n")
                f.write(f"   Project ID:  {sample['project_id']}\n")
                f.write(f"   image_path:  {sample['image_path']}\n")
                f.write(f"   crop_path:   {sample['crop_path']}\n")
                f.write(f"   bbox:        ({sample['bbox_x']}, {sample['bbox_y']}, "
                       f"{sample['bbox_w']}, {sample['bbox_h']})\n")
                f.write(f"   confidence:  {sample['confidence']}\n")
            f.write("\n")

            f.write("NEXT STEPS\n")
            f.write("-" * 80 + "\n")
            f.write("1. Review this report to understand corruption scope\n")
            f.write("2. Run repair_face_crops_database.py with --dry-run to preview fixes\n")
            f.write("3. Back up database before applying repairs\n")
            f.write("4. Execute repair script to recover data\n")
            f.write("5. Deploy prevention mechanisms (constraints + validation)\n")
            f.write("\n")

        print(f"📄 Detailed report saved: {report_path}")

    def _generate_csv_report(self):
        """Generate CSV export of corrupted entries."""
        if self.results['corrupted_entries'] == 0:
            return

        os.makedirs('reports', exist_ok=True)
        csv_path = 'reports/corrupted_face_crops.csv'

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    id,
                    project_id,
                    image_path,
                    crop_path,
                    bbox_x,
                    bbox_y,
                    bbox_w,
                    bbox_h,
                    confidence
                FROM face_crops
                WHERE image_path LIKE '%/face_crops/%'
                   OR image_path LIKE '%\\face_crops\\%'
                ORDER BY project_id, id
            """)

            with open(csv_path, 'w', encoding='utf-8') as f:
                f.write("id,project_id,image_path,crop_path,bbox_x,bbox_y,bbox_w,bbox_h,confidence\n")
                for row in cur.fetchall():
                    # Escape commas in paths
                    row_escaped = [
                        f'"{str(val)}"' if val and ',' in str(val) else str(val) if val else ''
                        for val in row
                    ]
                    f.write(','.join(row_escaped) + '\n')

        print(f"📊 CSV export saved: {csv_path}")
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Audit face_crops table for database corruption',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/audit_face_crops_corruption.py
  python scripts/audit_face_crops_corruption.py --db-path /path/to/photos.db

Output:
  - Console summary
  - reports/face_crops_corruption_report.txt
  - reports/corrupted_face_crops.csv
        """
    )
    parser.add_argument(
        '--db-path',
        default='photos.db',
        help='Path to SQLite database (default: photos.db)'
    )

    args = parser.parse_args()

    # Run audit
    try:
        auditor = FaceCropsAuditor(args.db_path)
        auditor.run_audit()

        # Exit code based on corruption status
        if auditor.results['corrupted_entries'] > 0:
            print("⚠️  Audit complete: Corruption detected")
            print("   Next: Review reports and run repair script")
            sys.exit(1)
        else:
            print("✅ Audit complete: No corruption detected")
            sys.exit(0)

    except FileNotFoundError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(3)


if __name__ == '__main__':
    main()
