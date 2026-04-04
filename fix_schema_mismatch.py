#!/usr/bin/env python3
"""
Schema Mismatch Fix Tool
Automatically fixes code to match v2.0.0 schema (global folders)
"""

import os
import shutil
import re

def backup_file(filepath):
    """Create backup of file before modifying"""
    backup = filepath + ".backup"
    if not os.path.exists(backup):
        shutil.copy2(filepath, backup)
        print(f"✅ Created backup: {backup}")
    return backup

def fix_sidebar_qt():
    """Fix sidebar_qt.py to use global folders (no project_id filtering)"""
    filepath = "sidebar_qt.py"

    if not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return False

    print(f"\n📝 Fixing {filepath}...")

    # Backup first
    backup_file(filepath)

    # Read current content
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if already using project_id filtering
    if 'self.db.get_all_folders(project_id=self.project_id)' in content:
        print("  ⚠️  Found incorrect project_id filtering in get_all_folders()")

        # Fix 1: Remove project_id parameter from get_all_folders call
        content = content.replace(
            'rows = self.db.get_all_folders(project_id=self.project_id) or []',
            'rows = self.db.get_all_folders() or []'
        )

        # Fix 2: Update debug log message
        content = re.sub(
            r'self\._dbg\(f"_load_folders → got \{len\(rows\)\} rows for project_id=\{self\.project_id\}"\)',
            'self._dbg(f"_load_folders → got {len(rows)} folders (global table)")',
            content
        )

        # Write back
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print("  ✅ Fixed get_all_folders() call - removed project_id filtering")
        print("  ✅ Updated debug log message")
        return True
    else:
        print("  ℹ️  Code already correct (no project_id filtering)")
        return False

def fix_reference_db():
    """Fix reference_db.py get_all_folders() method"""
    filepath = "reference_db.py"

    if not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return False

    print(f"\n📝 Checking {filepath}...")

    # Backup first
    backup_file(filepath)

    # Read current content
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check if get_all_folders has project_id parameter
    if 'def get_all_folders(self, project_id: int | None = None)' in content:
        print("  ⚠️  Found incorrect project_id parameter in get_all_folders()")

        # Find and replace the entire method
        # Pattern to match the method with project_id parameter
        old_pattern = r'def get_all_folders\(self, project_id: int \| None = None\) -> list\[dict\]:.*?(?=\n    def |\nclass |\Z)'

        new_method = '''def get_all_folders(self) -> list[dict]:
        """
        Return all folders as list of dicts: {id, parent_id, path, name}.
        Useful to build an in-memory tree quickly in the UI thread.

        NOTE: In schema v2.0.0, photo_folders is a GLOBAL table (no project_id).
        Folders are shared across all projects. Project filtering happens at
        the photo level via the project_images junction table.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, parent_id, path, name FROM photo_folders ORDER BY parent_id IS NOT NULL, parent_id, name")
            rows = [{"id": r[0], "parent_id": r[1], "path": r[2], "name": r[3]} for r in cur.fetchall()]
        return rows

    '''

        content = re.sub(old_pattern, new_method, content, flags=re.DOTALL)

        # Write back
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print("  ✅ Fixed get_all_folders() method - removed project_id parameter")
        return True
    else:
        print("  ℹ️  Method already correct (no project_id parameter)")
        return False

def main():
    print("=" * 80)
    print("SCHEMA MISMATCH FIX TOOL")
    print("=" * 80)
    print()
    print("This tool fixes code to match schema v2.0.0 (global folders)")
    print()

    # Check current directory
    if not os.path.exists("sidebar_qt.py") or not os.path.exists("reference_db.py"):
        print("❌ ERROR: Run this script from the project root directory!")
        print("   Expected files: sidebar_qt.py, reference_db.py")
        return

    # Fix files
    fixed_sidebar = fix_sidebar_qt()
    fixed_db = fix_reference_db()

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if fixed_sidebar or fixed_db:
        print("✅ Fixes applied successfully!")
        print()
        print("Changed files:")
        if fixed_sidebar:
            print("  • sidebar_qt.py")
        if fixed_db:
            print("  • reference_db.py")
        print()
        print("Backups created with .backup extension")
        print()
        print("⚠️  IMPORTANT: You also need to fix the CRASH issue!")
        print()
        print("The crash happens in sidebar_qt.py at model.clear()")
        print("You need to pull the latest crash fixes from GitHub:")
        print()
        print("  git pull origin claude/debug-issue-011CUstrEnRPeyq1j7XfX7h1")
        print()
        print("Or manually apply the fixes from commits:")
        print("  • c05677b: Qt widget lifecycle crash fixes")
        print("  • 7ac391b: Schema v2.0.0 documentation")
        print("  • 434c804: Debug status update")
    else:
        print("ℹ️  No changes needed - code already correct")

    print("=" * 80)

if __name__ == "__main__":
    main()
