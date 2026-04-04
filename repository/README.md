# Repository Pattern Documentation

## Overview

The Repository Pattern provides a clean abstraction layer between business logic and data access. This architecture:

- ✅ **Separates concerns**: Business logic doesn't know about SQL
- ✅ **Testable**: Can mock repositories for unit tests
- ✅ **Maintainable**: Database changes isolated to repositories
- ✅ **Type-safe**: Returns typed dictionaries
- ✅ **Consistent**: All data access follows same patterns

## Architecture

```
┌─────────────────────────────────────────────┐
│         Business Logic / Services           │
│    (PhotoScanService, MetadataService)      │
└───────────────────┬─────────────────────────┘
                    │
         ┌──────────┴──────────┐
         │   Repository Layer   │
         └──────────┬──────────┘
                    │
    ┌───────────────┼───────────────┐
    │               │               │
┌───▼────┐   ┌─────▼──────┐  ┌────▼─────┐
│ Photo  │   │   Folder   │  │ Project  │
│  Repo  │   │    Repo    │  │   Repo   │
└───┬────┘   └─────┬──────┘  └────┬─────┘
    │              │              │
    └──────────────┼──────────────┘
                   │
        ┌──────────▼──────────┐
        │ DatabaseConnection  │
        │   (Singleton)       │
        └──────────┬──────────┘
                   │
            ┌──────▼──────┐
            │   SQLite    │
            │ (reference  │
            │  _data.db)  │
            └─────────────┘
```

## Quick Start

### 1. Using a Repository

```python
from repository import PhotoRepository

# Create repository instance
photo_repo = PhotoRepository()

# Find a photo by path
photo = photo_repo.get_by_path("/photos/image.jpg")
if photo:
    print(f"Photo dimensions: {photo['width']}x{photo['height']}")

# Get all photos in a folder
photos = photo_repo.get_by_folder(folder_id=123, limit=100)

# Search photos
results = photo_repo.search("vacation")
```

### 2. Inserting/Updating Data

```python
from repository import PhotoRepository

photo_repo = PhotoRepository()

# Upsert (insert or update)
photo_id = photo_repo.upsert(
    path="/photos/img.jpg",
    folder_id=42,
    size_kb=3456.7,
    modified="2025-11-01 12:34:56",
    width=4000,
    height=3000,
    date_taken="2025:10:15 14:23:00",
    tags="vacation,beach"
)

print(f"Photo saved with ID: {photo_id}")
```

### 3. Bulk Operations

```python
# Bulk insert
rows = [
    ("/photo1.jpg", folder_id, 1024, "2025-11-01", 800, 600, None, None),
    ("/photo2.jpg", folder_id, 2048, "2025-11-01", 1920, 1080, None, None),
]

count = photo_repo.bulk_upsert(rows)
print(f"Inserted {count} photos")
```

### 4. Folder Operations

```python
from repository import FolderRepository

folder_repo = FolderRepository()

# Ensure folder exists (creates if missing)
folder_id = folder_repo.ensure_folder(
    path="/photos/2025/vacation",
    name="vacation",
    parent_id=parent_folder_id
)

# Get folder hierarchy
folders = folder_repo.get_all_with_counts()
for folder in folders:
    print(f"{folder['name']}: {folder['photo_count']} photos")
```

### 5. Project & Branches

```python
from repository import ProjectRepository

project_repo = ProjectRepository()

# Create project
project_id = project_repo.create(
    name="My Photo Collection",
    folder="/photos/2025",
    mode="date"
)

# Create branch
branch_id = project_repo.ensure_branch(
    project_id=project_id,
    branch_key="2025-11",
    display_name="November 2025"
)

# Get all projects with stats
projects = project_repo.get_all_with_details()
for proj in projects:
    print(f"{proj['name']}: {proj['image_count']} images in {proj['branch_count']} branches")
```

## Creating a New Repository

### Step 1: Create Repository Class

```python
# repository/tag_repository.py
from typing import List, Dict, Any
from .base_repository import BaseRepository

class TagRepository(BaseRepository):
    def _table_name(self) -> str:
        return "tags"  # Primary table

    def find_by_name(self, name: str) -> Optional[Dict]:
        """Find tag by name."""
        return self.find_all(
            where_clause="name = ? COLLATE NOCASE",
            params=(name,),
            limit=1
        )

    def get_popular_tags(self, limit: int = 20) -> List[Dict]:
        """Get most used tags."""
        sql = """
            SELECT t.*, COUNT(pt.photo_id) as usage_count
            FROM tags t
            LEFT JOIN photo_tags pt ON pt.tag_id = t.id
            GROUP BY t.id
            ORDER BY usage_count DESC
            LIMIT ?
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (limit,))
            return cur.fetchall()
```

### Step 2: Add to Package

```python
# repository/__init__.py
from .tag_repository import TagRepository

__all__ = [
    # ... existing exports
    'TagRepository',
]
```

### Step 3: Use in Service Layer

```python
# services/tag_service.py
from repository import TagRepository

class TagService:
    def __init__(self):
        self.tag_repo = TagRepository()

    def add_tags_to_photo(self, photo_id: int, tag_names: List[str]):
        for name in tag_names:
            tag = self.tag_repo.find_by_name(name)
            if not tag:
                tag_id = self.tag_repo.create(name=name)
            # ... associate tag with photo
```

## Advanced Features

### Transactions

Use transactions when multiple operations must succeed or fail together:

```python
from repository import TransactionContext, DatabaseConnection, PhotoRepository

db_conn = DatabaseConnection()

with TransactionContext(db_conn) as conn:
    # All operations share the same connection
    photo_repo = PhotoRepository(db_conn)

    photo_id1 = photo_repo.upsert(path="/img1.jpg", ...)
    photo_id2 = photo_repo.upsert(path="/img2.jpg", ...)

    if some_condition:
        raise Exception("Rollback both inserts")

    # Auto-commits if no exception
```

### Read-Only Queries

For performance, use read-only connections when you don't need to write:

```python
# Read-only is slightly faster and prevents accidental writes
with photo_repo.connection(read_only=True) as conn:
    cur = conn.cursor()
    cur.execute("SELECT * FROM photo_metadata LIMIT 1000")
    results = cur.fetchall()
```

### Custom Queries

For complex queries, use the connection directly:

```python
class PhotoRepository(BaseRepository):
    def get_photos_by_year_with_stats(self, year: int):
        sql = """
            SELECT
                strftime('%Y-%m', date_taken) as month,
                COUNT(*) as photo_count,
                AVG(size_kb) as avg_size,
                SUM(size_kb) as total_size
            FROM photo_metadata
            WHERE date_taken LIKE ?
            GROUP BY month
            ORDER BY month
        """

        with self.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(sql, (f"{year}%",))
            return cur.fetchall()
```

## Migration from ReferenceDB

### Before (Direct SQL)

```python
from reference_db import ReferenceDB

db = ReferenceDB()
with db._connect() as conn:
    cur = conn.cursor()
    cur.execute("SELECT * FROM photo_metadata WHERE path = ?", (path,))
    photo = cur.fetchone()
```

### After (Repository Pattern)

```python
from repository import PhotoRepository

photo_repo = PhotoRepository()
photo = photo_repo.get_by_path(path)
```

### Benefits

| Before | After |
|--------|-------|
| SQL scattered everywhere | SQL centralized in repositories |
| Hard to test (needs DB) | Easy to mock |
| Typos in column names | Type-safe dict access |
| Inconsistent error handling | Consistent logging |
| Direct cursor manipulation | Clean API |

## Testing

Repositories are designed to be easily testable:

```python
# tests/test_photo_repository.py
import unittest
from repository import PhotoRepository, DatabaseConnection

class TestPhotoRepository(unittest.TestCase):
    def setUp(self):
        # Use in-memory database for tests
        self.db_conn = DatabaseConnection(":memory:")
        self.repo = PhotoRepository(self.db_conn)

        # Setup test schema
        self.db_conn.execute_script("""
            CREATE TABLE photo_metadata (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                folder_id INTEGER,
                size_kb REAL
            )
        """)

    def test_upsert_new_photo(self):
        photo_id = self.repo.upsert(
            path="/test.jpg",
            folder_id=1,
            size_kb=100.0
        )

        self.assertIsNotNone(photo_id)

        # Verify inserted
        photo = self.repo.get_by_path("/test.jpg")
        self.assertEqual(photo['size_kb'], 100.0)

    def test_upsert_existing_updates(self):
        # First insert
        self.repo.upsert(path="/test.jpg", folder_id=1, size_kb=100.0)

        # Update
        self.repo.upsert(path="/test.jpg", folder_id=1, size_kb=200.0)

        # Verify updated
        photo = self.repo.get_by_path("/test.jpg")
        self.assertEqual(photo['size_kb'], 200.0)
```

## Best Practices

### ✅ DO:

1. **Use repositories for all database access**
   ```python
   # ✅ Good
   photos = photo_repo.get_by_folder(folder_id)
   ```

2. **Keep business logic OUT of repositories**
   ```python
   # ✅ Good - Repository just retrieves data
   class PhotoRepository:
       def get_by_folder(self, folder_id):
           return self.find_all(where_clause="folder_id = ?", ...)

   # ✅ Business logic in service layer
   class PhotoService:
       def get_vacation_photos(self, folder_id):
           photos = self.photo_repo.get_by_folder(folder_id)
           return [p for p in photos if 'vacation' in p.get('tags', '')]
   ```

3. **Use transactions for multi-step operations**

4. **Log important operations**
   ```python
   self.logger.info(f"Deleted {count} photos from folder {folder_id}")
   ```

### ❌ DON'T:

1. **Don't bypass repositories with direct SQL**
   ```python
   # ❌ Bad
   conn = sqlite3.connect("reference_data.db")
   cur = conn.execute("SELECT * FROM photo_metadata")
   ```

2. **Don't put business logic in repositories**
   ```python
   # ❌ Bad
   def get_large_photos(self):
       # Business rule: "large" means > 5MB
       return self.find_all(where_clause="size_kb > 5120")
   ```

3. **Don't forget error handling**
   ```python
   # ❌ Bad
   try:
       photo_repo.upsert(...)
   except:
       pass  # Silent failure

   # ✅ Good
   try:
       photo_repo.upsert(...)
   except Exception as e:
       logger.error(f"Failed to save photo: {e}", exc_info=True)
       raise
   ```

## Performance Tips

1. **Batch operations**: Use `bulk_upsert()` instead of looping `upsert()`
2. **Use read-only connections**: `connection(read_only=True)` for queries
3. **Limit results**: Always use `limit` for potentially large result sets
4. **Index appropriately**: Make sure frequently queried columns are indexed

## Connection Management

The `DatabaseConnection` singleton ensures:
- Only one database file path is used
- Connections are properly configured
- Foreign keys are enabled
- WAL mode is activated (better concurrency)
- Rows returned as dictionaries

You don't need to manage connections manually - repositories handle it automatically.

## Questions?

See `base_repository.py` for implementation details or specific repository files for examples.
