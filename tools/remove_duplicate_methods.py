#!/usr/bin/env python3
"""
Safe Duplicate Method Removal Script

Automatically removes duplicate method definitions from google_layout.py
while preserving the first (or most complete) implementation.

Usage:
    python tools/remove_duplicate_methods.py --dry-run  # Preview changes
    python tools/remove_duplicate_methods.py --execute  # Apply changes
"""

import re
import argparse
from pathlib import Path
from typing import List, Tuple, Set


class DuplicateMethodRemover:
    """Safely removes duplicate method definitions."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.backup_path = self.file_path.with_suffix('.py.backup')
        self.lines = []
        self.load_file()

    def load_file(self):
        """Load file into memory."""
        with open(self.file_path, 'r', encoding='utf-8') as f:
            self.lines = f.readlines()
        print(f"✅ Loaded {len(self.lines):,} lines from {self.file_path}")

    def create_backup(self):
        """Create backup of original file."""
        with open(self.backup_path, 'w', encoding='utf-8') as f:
            f.writelines(self.lines)
        print(f"💾 Backup created: {self.backup_path}")

    def find_method_boundaries(self, start_line: int) -> Tuple[int, int]:
        """
        Find the start and end line numbers of a method.

        Args:
            start_line: Line number where method def starts (1-indexed)

        Returns:
            (start_line, end_line) tuple (1-indexed, inclusive)
        """
        # Convert to 0-indexed
        idx = start_line - 1

        # Detect initial indentation
        def_line = self.lines[idx]
        method_indent = len(def_line) - len(def_line.lstrip())

        # Find end of method (next line with same or less indentation that's not blank)
        end_idx = idx + 1
        while end_idx < len(self.lines):
            line = self.lines[end_idx]
            stripped = line.strip()

            # Skip blank lines and comments
            if not stripped or stripped.startswith('#'):
                end_idx += 1
                continue

            # Check indentation
            current_indent = len(line) - len(line.lstrip())

            # If we find a line with same or less indentation, method ends
            if current_indent <= method_indent:
                break

            end_idx += 1

        # end_line is the line BEFORE the next method/class (1-indexed)
        return (start_line, end_idx)  # end_idx is already 0-indexed, so equals end_line when converted

    def remove_duplicates(self, duplicates_to_remove: List[int]) -> Tuple[List[str], Set[int]]:
        """
        Remove duplicate methods from lines.

        Args:
            duplicates_to_remove: List of line numbers to remove (1-indexed)

        Returns:
            (new_lines, removed_line_set) tuple
        """
        # Find all method boundaries
        methods_to_remove = []
        for line_num in duplicates_to_remove:
            start, end = self.find_method_boundaries(line_num)
            methods_to_remove.append((start, end))
            print(f"   Method at line {line_num}: spans lines {start}-{end}")

        # Create set of all lines to remove (1-indexed)
        lines_to_remove = set()
        for start, end in methods_to_remove:
            lines_to_remove.update(range(start, end + 1))

        # Filter out removed lines
        new_lines = []
        for i, line in enumerate(self.lines, start=1):
            if i not in lines_to_remove:
                new_lines.append(line)

        return (new_lines, lines_to_remove)

    def preview_changes(self, duplicates_to_remove: List[int]):
        """Preview what will be removed."""
        print(f"\n{'=' * 70}")
        print("PREVIEW: Methods to be removed")
        print(f"{'=' * 70}\n")

        for line_num in sorted(duplicates_to_remove):
            start, end = self.find_method_boundaries(line_num)
            method_line = self.lines[line_num - 1].strip()

            print(f"❌ Lines {start:5d}-{end:5d} ({end-start+1:3d} lines): {method_line}")

        new_lines, removed_lines = self.remove_duplicates(duplicates_to_remove)

        print(f"\n{'=' * 70}")
        print(f"📊 Summary:")
        print(f"   Original: {len(self.lines):,} lines")
        print(f"   Removed:  {len(removed_lines):,} lines")
        print(f"   New:      {len(new_lines):,} lines")
        print(f"   Savings:  {len(self.lines) - len(new_lines):,} lines ({(len(removed_lines)/len(self.lines)*100):.1f}%)")
        print(f"{'=' * 70}\n")

    def execute_removal(self, duplicates_to_remove: List[int]):
        """Actually remove duplicates and save file."""
        self.create_backup()

        new_lines, removed_lines = self.remove_duplicates(duplicates_to_remove)

        # Save new file
        with open(self.file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        print(f"\n✅ File updated: {self.file_path}")
        print(f"📊 Removed {len(removed_lines):,} lines")
        print(f"📊 New file size: {len(new_lines):,} lines")


def main():
    parser = argparse.ArgumentParser(description='Remove duplicate methods from google_layout.py')
    parser.add_argument('--file', default='layouts/google_layout.py',
                       help='Path to google_layout.py')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview changes without modifying file')
    parser.add_argument('--execute', action='store_true',
                       help='Execute removal (creates backup first)')

    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("❌ Must specify either --dry-run or --execute")
        parser.print_help()
        return 1

    # Duplicates to remove (from cleanup plan)
    duplicates_to_remove = [
        # GooglePhotosLayout duplicates
        9552,    # _build_tags_tree
        10137, 10243, 10330, 10417,  # _on_accordion_device_selected (keep 9991)
        10172, 10259, 10346, 10433, 10504,  # _on_accordion_person_deleted (keep 10066)
        10084, 10190, 10277, 10364, 10451,  # _on_accordion_person_merged (keep 9764)
        12926,   # _on_drag_merge (keep 11852)
        10115, 10221, 10308, 10395, 10482,  # _on_people_merge_history_requested (keep 9795)
        10129, 10235, 10322, 10409, 10496,  # _on_people_redo_requested (keep 9809)
        10122, 10228, 10315, 10402, 10489,  # _on_people_undo_requested (keep 9802)
        9600,    # _on_tags_item_clicked (keep 9378)
        13004,   # _redo_last_undo (keep 11930)
        10108, 10214, 10301, 10388, 10475,  # _refresh_people_sidebar (keep 9788)
        12947,   # _undo_last_merge (keep 11873)
        13087,   # _update_undo_redo_state (keep 12007)

        # MediaLightbox duplicates
        4885,    # _toggle_info_panel (keep 1907)
        7623,    # eventFilter (keep 1841)
        6197,    # keyPressEvent (keep 4076)
        5919,    # resizeEvent (keep 1506)
        6175,    # showEvent (keep 1391)
    ]

    print(f"\n{'=' * 70}")
    print("🔧 Duplicate Method Removal Tool")
    print(f"{'=' * 70}\n")
    print(f"Target file: {args.file}")
    print(f"Duplicates to remove: {len(duplicates_to_remove)}")
    print()

    remover = DuplicateMethodRemover(args.file)

    if args.dry_run:
        print("🔍 DRY RUN MODE - No changes will be made\n")
        remover.preview_changes(duplicates_to_remove)
        print("✅ Dry run complete. Use --execute to apply changes.")
        return 0

    if args.execute:
        print("⚠️  EXECUTE MODE - File will be modified\n")
        remover.preview_changes(duplicates_to_remove)

        print("\n" + "=" * 70)
        response = input("Proceed with removal? (yes/no): ")
        print("=" * 70 + "\n")

        if response.lower() != 'yes':
            print("❌ Aborted by user")
            return 1

        remover.execute_removal(duplicates_to_remove)
        print("\n✅ Cleanup complete!")
        print(f"💡 Backup saved to: {remover.backup_path}")
        print(f"💡 To restore: cp {remover.backup_path} {remover.file_path}")
        return 0


if __name__ == '__main__':
    exit(main())
