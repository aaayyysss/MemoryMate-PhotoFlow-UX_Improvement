# migrations/__init__.py
# Package marker for SQL and Python migration scripts
"""
Database migration scripts for MemoryMate-PhotoFlow.

Contains:
- SQL migration files (.sql) for schema changes
- Python migration modules (.py) for complex migrations that require logic

Migration naming convention:
    migration_v{major}_{feature}.sql   - Simple SQL migrations
    migration_v{major}_{feature}.py    - Complex Python migrations
"""
