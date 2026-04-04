"""
Diagnostic and Fix Script for Corrupted Embeddings

This script:
1. Diagnoses why embeddings are corrupted (9 bytes instead of 2048 bytes)
2. Shows sample data from the database
3. Provides option to clear corrupted embeddings
4. Guides user to re-extract embeddings

Usage:
    python fix_corrupted_embeddings.py
"""

import sqlite3
from pathlib import Path

def diagnose_embeddings():
    """Diagnose embedding corruption issue."""
    print("=" * 80)
    print("EMBEDDING CORRUPTION DIAGNOSTIC")
    print("=" * 80)
    print()

    # Find database
    db_path = Path("reference.db")
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return

    print(f"✓ Found database: {db_path}")
    print()

    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    cursor = conn.cursor()

    # Check if photo_embedding table exists
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='photo_embedding'
    """)
    if not cursor.fetchone():
        print("❌ photo_embedding table does not exist!")
        print("   Run embedding extraction first.")
        conn.close()
        return

    print("✓ photo_embedding table exists")
    print()

    # Count embeddings
    cursor.execute("SELECT COUNT(*) FROM photo_embedding WHERE embedding_type = 'visual_semantic'")
    count = cursor.fetchone()[0]
    print(f"📊 Total embeddings: {count}")
    print()

    if count == 0:
        print("⚠️  No embeddings found! Please run embedding extraction.")
        conn.close()
        return

    # Sample first 5 rows
    print("🔍 Sampling first 5 embeddings:")
    print()

    cursor.execute("""
        SELECT photo_id, model_id, dim,
               length(embedding) as blob_size,
               typeof(embedding) as blob_type,
               substr(quote(embedding), 1, 50) as blob_preview
        FROM photo_embedding
        WHERE embedding_type = 'visual_semantic'
        LIMIT 5
    """)

    rows = cursor.fetchall()

    for i, row in enumerate(rows, 1):
        print(f"  Row {i}:")
        print(f"    photo_id: {row['photo_id']}")
        print(f"    model_id: {row['model_id']}")
        print(f"    dim (stored): {row['dim']}")
        print(f"    blob_size (actual): {row['blob_size']} bytes")
        print(f"    blob_type: {row['blob_type']}")
        print(f"    blob_preview: {row['blob_preview']}")
        print()

        # Diagnose issues
        expected_size = row['dim'] * 4  # float32 = 4 bytes
        actual_size = row['blob_size']

        if actual_size != expected_size:
            print(f"    ❌ SIZE MISMATCH!")
            print(f"       Expected: {expected_size} bytes ({row['dim']} dims * 4 bytes)")
            print(f"       Actual:   {actual_size} bytes")
            print()

        if actual_size == 9:
            print(f"    ⚠️  CORRUPTION DETECTED!")
            print(f"       Embedding is 9 bytes - likely stored as string 'photo_id'")
            print(f"       This means embedding extraction is storing column names instead of data!")
            print()

    # Statistics
    print("📈 Statistics:")
    cursor.execute("""
        SELECT
            length(embedding) as size,
            COUNT(*) as count
        FROM photo_embedding
        WHERE embedding_type = 'visual_semantic'
        GROUP BY length(embedding)
        ORDER BY count DESC
    """)

    stats = cursor.fetchall()
    for row in stats:
        expected = 512 * 4  # CLIP dimensions
        status = "✓" if row['size'] == expected else "❌"
        print(f"  {status} {row['size']} bytes: {row['count']} embeddings")

    print()
    print("=" * 80)

    # Offer to clear corrupted embeddings
    cursor.execute("""
        SELECT COUNT(*) FROM photo_embedding
        WHERE embedding_type = 'visual_semantic'
        AND length(embedding) != 2048
    """)
    corrupted_count = cursor.fetchone()[0]

    if corrupted_count > 0:
        print()
        print(f"⚠️  Found {corrupted_count} corrupted embeddings (not 2048 bytes)")
        print()
        response = input("Delete all corrupted embeddings? (yes/no): ").strip().lower()

        if response == 'yes':
            cursor.execute("""
                DELETE FROM photo_embedding
                WHERE embedding_type = 'visual_semantic'
                AND length(embedding) != 2048
            """)
            conn.commit()
            print(f"✓ Deleted {corrupted_count} corrupted embeddings")
            print()
            print("Next steps:")
            print("1. Re-run embedding extraction in the app")
            print("2. Menu → Scan → Extract Visual Embeddings")
            print("3. Wait for extraction to complete")
            print("4. Try semantic search again")
        else:
            print("No changes made.")

    conn.close()
    print()
    print("=" * 80)
    print("DIAGNOSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    diagnose_embeddings()
