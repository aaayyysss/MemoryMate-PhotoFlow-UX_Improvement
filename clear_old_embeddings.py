"""
Clear Old Embeddings (Optional)

This script is OPTIONAL. Only run it if:
- You want to force re-extraction with the new large model
- You have mixed embeddings (some 512-D, some 768-D)
- You want to ensure all embeddings use the same model

The app can work with mixed embeddings, but for consistency you may want to clear and re-extract.

Usage:
    python clear_old_embeddings.py

What it does:
    1. Creates backup of current embeddings
    2. Deletes all embeddings from database
    3. Next extraction will use the large model
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime


def find_database():
    """Find the reference database file."""
    # Common locations
    possible_paths = [
        Path.cwd() / 'data' / 'reference.db',
        Path.cwd() / 'reference.db',
        Path.home() / 'AppData' / 'Local' / 'MemoryMate' / 'reference.db',  # Windows
    ]

    for path in possible_paths:
        if path.exists():
            return path

    return None


def clear_embeddings():
    """Clear all embeddings from database."""

    print("=" * 70)
    print("Clear Old Embeddings (Optional)")
    print("=" * 70)
    print()
    print("⚠️  This script is OPTIONAL!")
    print()
    print("You only need to run this if:")
    print("  • You want to force re-extraction with the large model")
    print("  • You want consistency (all embeddings from same model)")
    print()
    print("The app can handle mixed embeddings just fine.")
    print()

    proceed = input("Continue? [y/N]: ").strip().lower()
    if proceed != 'y':
        print("\nCancelled. No changes made.")
        return

    print()

    # Find database
    print("[Step 1/3] Locating database...")
    db_path = find_database()

    if not db_path:
        print("  ✗ Database not found!")
        print()
        db_input = input("  Enter path to reference.db: ").strip()
        db_path = Path(db_input)

        if not db_path.exists():
            print(f"  ✗ File not found: {db_path}")
            sys.exit(1)

    print(f"  ✓ Found: {db_path}")
    print()

    # Connect and check
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get current count
        cursor.execute("SELECT COUNT(*) FROM photo_embeddings WHERE 1=1")
        total_count = cursor.fetchone()[0]

        # Get count by dimension
        cursor.execute("""
            SELECT dim, COUNT(*)
            FROM photo_embeddings
            GROUP BY dim
        """)
        dim_counts = dict(cursor.fetchall())

        print(f"  Current embeddings:")
        for dim, count in dim_counts.items():
            model_name = "large-patch14" if dim == 768 else "base-patch32"
            print(f"    {count} embeddings ({dim}-D, {model_name})")
        print(f"    Total: {total_count}")
        print()

    except Exception as e:
        print(f"  ✗ Database error: {e}")
        conn.close()
        sys.exit(1)

    # Backup
    print("[Step 2/3] Creating backup...")
    backup_file = db_path.parent / f"embeddings_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"

    try:
        with open(backup_file, 'w') as f:
            for line in conn.iterdump():
                if 'photo_embeddings' in line or 'embedding_models' in line:
                    f.write(f"{line}\n")

        print(f"  ✓ Backup: {backup_file}")

    except Exception as e:
        print(f"  ⚠️  Backup failed: {e}")

    print()

    # Confirm
    print("[Step 3/3] Ready to clear embeddings")
    print()
    confirm = input(f"  Delete {total_count} embeddings? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("\n  Cancelled")
        conn.close()
        return

    print()

    try:
        cursor.execute("DELETE FROM photo_embeddings")
        cursor.execute("DELETE FROM embedding_models")
        conn.commit()
        conn.close()

        print(f"  ✓ Deleted {total_count} embeddings")
        print()
        print("=" * 70)
        print("SUCCESS! Embeddings cleared")
        print("=" * 70)
        print()
        print("Next steps:")
        print("  1. Open MemoryMate-PhotoFlow app")
        print("  2. Go to Tools → Extract Embeddings")
        print("  3. App will use large model for extraction")
        print()

    except Exception as e:
        print(f"  ✗ Delete failed: {e}")
        conn.rollback()
        conn.close()
        sys.exit(1)


if __name__ == '__main__':
    try:
        clear_embeddings()
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
