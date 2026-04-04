import sqlite3

def check_db():
    conn = sqlite3.connect("reference_data.db")
    conn.row_factory = sqlite3.Row

    print("--- Projects ---")
    cur = conn.execute("SELECT * FROM projects")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))
    if not rows:
        print("Empty")

    print("\n--- Photo Metadata (first 5) ---")
    cur = conn.execute("SELECT id, path, project_id FROM photo_metadata LIMIT 5")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))
    if not rows:
        print("Empty")

    print("\n--- Distinct project_ids in photo_metadata ---")
    cur = conn.execute("SELECT DISTINCT project_id FROM photo_metadata")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))

    print("\n--- media_asset (first 5) ---")
    cur = conn.execute("SELECT * FROM media_asset LIMIT 5")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))

    print("\n--- media_instance (first 5) ---")
    cur = conn.execute("SELECT * FROM media_instance LIMIT 5")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))

    print("\n--- FK Check ---")
    cur = conn.execute("PRAGMA foreign_key_check")
    rows = cur.fetchall()
    for row in rows:
        print(dict(row))
    if not rows:
        print("No FK violations found by PRAGMA")

    conn.close()

if __name__ == "__main__":
    check_db()
