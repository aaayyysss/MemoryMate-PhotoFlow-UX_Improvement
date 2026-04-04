#!/usr/bin/env python3
"""
Google Layout Health Check Script
Identifies duplicate methods, code quality issues, and architectural problems.

Usage:
    python tools/health_check_google_layout.py
    python tools/health_check_google_layout.py --fix-duplicates
"""

import re
import ast
from collections import defaultdict
from pathlib import Path
import argparse


class GoogleLayoutHealthChecker:
    """Analyzes google_layout.py for code quality issues."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.content = self.file_path.read_text(encoding='utf-8')
        self.lines = self.content.split('\n')
        self.issues = []

    def check_all(self):
        """Run all health checks."""
        print("🔍 Google Layout Health Check")
        print("=" * 60)
        print(f"File: {self.file_path}")
        print(f"Size: {len(self.lines):,} lines, {len(self.content):,} characters")
        print("=" * 60)
        print()

        self.check_file_size()
        self.check_duplicate_methods()
        self.check_class_structure()
        self.check_imports()

        print("\n" + "=" * 60)
        print(f"📊 SUMMARY: Found {len(self.issues)} issues")
        print("=" * 60)

        return len(self.issues)

    def check_file_size(self):
        """Check if file is too large."""
        print("📏 Checking file size...")

        if len(self.lines) > 10000:
            severity = "🚨 CRITICAL" if len(self.lines) > 15000 else "⚠️  WARNING"
            issue = f"{severity}: File has {len(self.lines):,} lines (recommended: <1000 per file)"
            print(f"   {issue}")
            self.issues.append(issue)
        else:
            print(f"   ✅ File size OK: {len(self.lines):,} lines")
        print()

    def check_duplicate_methods(self):
        """Find duplicate method definitions."""
        print("🔍 Checking for duplicate methods...")

        method_pattern = re.compile(r'^(\s*)def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', re.MULTILINE)

        methods = defaultdict(list)
        for line_num, line in enumerate(self.lines, 1):
            match = method_pattern.match(line)
            if match:
                indent = match.group(1)
                method_name = match.group(2)
                methods[method_name].append({
                    'line': line_num,
                    'indent': len(indent),
                    'full_line': line
                })

        # Find duplicates
        duplicates = {name: occurrences for name, occurrences in methods.items()
                     if len(occurrences) > 1}

        if duplicates:
            print(f"   🚨 Found {len(duplicates)} duplicate method definitions:")
            print()

            # Group by class (approximate based on indentation)
            for method_name in sorted(duplicates.keys()):
                occurrences = duplicates[method_name]
                lines = [occ['line'] for occ in occurrences]

                issue = f"Duplicate method: {method_name}() at lines {', '.join(map(str, lines))}"
                self.issues.append(issue)

                print(f"   ❌ {method_name}()")
                print(f"      Defined {len(occurrences)} times at lines: {', '.join(map(str, lines))}")

                # Show context for each occurrence
                for i, occ in enumerate(occurrences, 1):
                    line_num = occ['line']
                    print(f"      [{i}] Line {line_num}: {self.lines[line_num-1].strip()}")
                print()

            print(f"   📊 Total: {len(duplicates)} duplicate methods")
        else:
            print("   ✅ No duplicate methods found")

        print()
        return duplicates

    def check_class_structure(self):
        """Analyze class structure and nesting."""
        print("🏗️  Checking class structure...")

        class_pattern = re.compile(r'^(\s*)class\s+([a-zA-Z_][a-zA-Z0-9_]*)')

        classes = []
        for line_num, line in enumerate(self.lines, 1):
            match = class_pattern.match(line)
            if match:
                indent = len(match.group(1))
                class_name = match.group(2)
                classes.append({
                    'line': line_num,
                    'indent': indent,
                    'name': class_name
                })

        print(f"   Found {len(classes)} class definitions:")
        for cls in classes:
            print(f"      - {cls['name']} (line {cls['line']}, indent {cls['indent']})")

        if len(classes) > 10:
            issue = f"⚠️  Too many classes in one file: {len(classes)} (recommended: 1-3)"
            print(f"   {issue}")
            self.issues.append(issue)

        print()

    def check_imports(self):
        """Check import organization."""
        print("📦 Checking imports...")

        import_lines = []
        for line_num, line in enumerate(self.lines, 1):
            stripped = line.strip()
            if stripped.startswith('import ') or stripped.startswith('from '):
                import_lines.append(line_num)

        if import_lines:
            first_import = import_lines[0]
            last_import = import_lines[-1]
            import_span = last_import - first_import + 1

            print(f"   Imports span lines {first_import} to {last_import} ({import_span} lines)")

            # Check for late imports (after line 100)
            late_imports = [ln for ln in import_lines if ln > 100]
            if late_imports:
                issue = f"⚠️  Found {len(late_imports)} import statements after line 100 (may indicate conditional imports)"
                print(f"   {issue}")
                self.issues.append(issue)
        else:
            print("   ⚠️  No imports found (unexpected)")

        print()

    def generate_fix_plan(self, duplicates):
        """Generate a plan to fix duplicate methods."""
        print("\n" + "=" * 60)
        print("🔧 FIX PLAN FOR DUPLICATE METHODS")
        print("=" * 60)
        print()

        for method_name, occurrences in sorted(duplicates.items()):
            print(f"Method: {method_name}()")
            print(f"  Occurrences: {len(occurrences)}")
            print(f"  Action: Keep first occurrence at line {occurrences[0]['line']}")
            print(f"  Remove: Lines {', '.join(str(occ['line']) for occ in occurrences[1:])}")
            print()


def main():
    parser = argparse.ArgumentParser(description='Health check for google_layout.py')
    parser.add_argument('--file', default='layouts/google_layout.py',
                       help='Path to google_layout.py')
    parser.add_argument('--fix-duplicates', action='store_true',
                       help='Generate detailed fix plan for duplicates')

    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return 1

    checker = GoogleLayoutHealthChecker(file_path)
    issue_count = checker.check_all()

    if args.fix_duplicates:
        duplicates = checker.check_duplicate_methods()
        if duplicates:
            checker.generate_fix_plan(duplicates)

    print()
    if issue_count == 0:
        print("✅ All checks passed!")
        return 0
    else:
        print(f"⚠️  Found {issue_count} issues that need attention")
        return 1


if __name__ == '__main__':
    exit(main())
