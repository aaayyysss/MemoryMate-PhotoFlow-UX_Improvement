#!/usr/bin/env python3
"""
Guardrail: Qt.UserRole must only store JSON strings, never Python dicts/lists.

Scans the codebase for violations of the UserRole contract:
  1. Direct dict writes:  setData(..., Qt.UserRole, {...})
  2. Writeback patterns:  setData(..., Qt.UserRole, data)  where data is a variable
  3. json.dumps inline:   setData(..., Qt.UserRole, json.dumps(...))  (should use role_set_json)

Run:
    python tools/check_userrole_contract.py

Exit code 0 = clean, 1 = violations found.

Known-safe patterns (not flagged):
  - Primitives: setData(0, Qt.UserRole, str(...)) / int(...) / "literal"
  - Helper:     role_set_json(item, {...}, role=Qt.UserRole)
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Patterns that indicate contract violations (hard errors)
VIOLATION_PATTERNS = [
    # Direct dict literal in setData — ALWAYS wrong
    (r"setData\(\s*\d+\s*,\s*Qt\.UserRole\s*,\s*\{", "DICT_LITERAL"),
    # Direct list literal in setData — ALWAYS wrong
    (r"setData\(\s*\d+\s*,\s*Qt\.UserRole\s*,\s*\[", "LIST_LITERAL"),
]

# Patterns that suggest migration but aren't necessarily violations
# Variables storing str/int are safe; variables storing dict/list are not.
# Since we can't statically type-check, these are advisories.
ADVISORY_PATTERNS = [
    (r"setData\(\s*\d+\s*,\s*Qt\.UserRole\s*,\s*json\.dumps\(", "INLINE_JSON_DUMPS"),
]

SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", "node_modules", "venv", ".venv"}


def scan_files():
    violations = []
    advisories = []

    for py_file in sorted(ROOT.rglob("*.py")):
        # Skip excluded directories
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        # Skip this script itself
        if py_file.name == "check_userrole_contract.py":
            continue

        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel_path = py_file.relative_to(ROOT)

        for line_num, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue

            for pattern, label in VIOLATION_PATTERNS:
                if re.search(pattern, line):
                    violations.append((str(rel_path), line_num, label, stripped))

            for pattern, label in ADVISORY_PATTERNS:
                if re.search(pattern, line):
                    advisories.append((str(rel_path), line_num, label, stripped))

    return violations, advisories


def main():
    violations, advisories = scan_files()

    if advisories:
        print(f"ADVISORY: {len(advisories)} inline json.dumps calls (should migrate to role_set_json):")
        for path, line, label, code in advisories[:20]:
            print(f"  {path}:{line}  [{label}]")
        if len(advisories) > 20:
            print(f"  ... and {len(advisories) - 20} more")
        print()

    if violations:
        print(f"FAILED: {len(violations)} UserRole contract violation(s) found:")
        for path, line, label, code in violations:
            print(f"  {path}:{line}  [{label}]  {code[:100]}")
        sys.exit(1)
    else:
        print(f"OK: No UserRole dict-write violations found. ({len(advisories)} advisory items)")
        sys.exit(0)


if __name__ == "__main__":
    main()
