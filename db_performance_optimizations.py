"""
Database Performance Optimizations
===================================

This module contains optimized database methods to fix N+1 query problems
and leverage the new schema v3.2.0 with direct project_id columns.

Key optimizations:
1. Batch folder counting (replaces N+1 individual queries)
2. Direct project_id filtering (no JOIN to project_images needed)
3. Compound indexes for common query patterns
"""

def get_folder_counts_batch_optimized(db, project_id: int) -> dict[int, int]:
    """
    Get photo counts for ALL folders in ONE query (fixes N+1 problem).

    This is dramatically faster than calling get_image_count_recursive() for each folder.

    Args:
        db: Database connection (ReferenceDB instance)
        project_id: Project ID to count photos for

    Returns:
        dict mapping folder_id -> photo_count (including subfolders)

    Performance:
        Before: N+1 queries (1 + one per folder)
        After: 1 query (get all counts at once)

    Example:
        counts = get_folder_counts_batch_optimized(db, project_id=1)
        # counts = {1: 150, 2: 75, 3: 0, ...}
    """
    with db._connect() as conn:
        cur = conn.cursor()

        # OPTIMIZATION: Get counts for ALL folders at once using recursive CTE
        # This replaces N individual queries with ONE query
        cur.execute("""
            WITH RECURSIVE folder_tree AS (
                -- Start with all folders in this project
                SELECT id, parent_id, id as root_id
                FROM photo_folders
                WHERE project_id = ?

                UNION ALL

                -- Recursively include child folders, remembering the root ancestor
                SELECT f.id, f.parent_id, ft.root_id
                FROM photo_folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
                WHERE f.project_id = ?
            )
            SELECT
                ft.root_id as folder_id,
                COUNT(pm.id) as photo_count
            FROM folder_tree ft
            LEFT JOIN photo_metadata pm
                ON pm.folder_id = ft.id
                AND pm.project_id = ?
            GROUP BY ft.root_id
        """, (project_id, project_id, project_id))

        # Convert to dict: folder_id -> count
        counts = {}
        for row in cur.fetchall():
            folder_id = row[0]
            photo_count = row[1] or 0
            counts[folder_id] = photo_count

        return counts


def get_image_count_recursive_optimized(db, folder_id: int, project_id: int) -> int:
    """
    Get photo count for ONE folder (optimized for schema v3.2.0).

    This uses direct project_id filtering instead of JOIN to project_images.

    Args:
        db: Database connection
        folder_id: Folder ID to count
        project_id: Project ID to filter by

    Returns:
        int: Number of photos in folder and subfolders

    Performance:
        Before: JOIN to project_images table (slower, complex)
        After: Direct project_id filter (faster, simpler)
    """
    with db._connect() as conn:
        cur = conn.cursor()

        # OPTIMIZATION: Use direct project_id column (no JOIN needed)
        cur.execute("""
            WITH RECURSIVE subfolders(id) AS (
                SELECT id FROM photo_folders
                WHERE id = ? AND project_id = ?

                UNION ALL

                SELECT f.id
                FROM photo_folders f
                JOIN subfolders s ON f.parent_id = s.id
                WHERE f.project_id = ?
            )
            SELECT COUNT(*)
            FROM photo_metadata pm
            WHERE pm.folder_id IN (SELECT id FROM subfolders)
              AND pm.project_id = ?
        """, (folder_id, project_id, project_id, project_id))

        row = cur.fetchone()
        return row[0] if row else 0


def get_video_counts_batch_optimized(db, project_id: int) -> dict[int, int]:
    """
    Get video counts for ALL folders in ONE query (fixes N+1 problem).

    Same optimization as photo counts, but for videos.

    Args:
        db: Database connection
        project_id: Project ID to count videos for

    Returns:
        dict mapping folder_id -> video_count (including subfolders)
    """
    with db._connect() as conn:
        cur = conn.cursor()

        cur.execute("""
            WITH RECURSIVE folder_tree AS (
                SELECT id, parent_id, id as root_id
                FROM photo_folders
                WHERE project_id = ?

                UNION ALL

                SELECT f.id, f.parent_id, ft.root_id
                FROM photo_folders f
                JOIN folder_tree ft ON f.parent_id = ft.id
                WHERE f.project_id = ?
            )
            SELECT
                ft.root_id as folder_id,
                COUNT(vm.id) as video_count
            FROM folder_tree ft
            LEFT JOIN video_metadata vm
                ON vm.folder_id = ft.id
                AND vm.project_id = ?
            GROUP BY ft.root_id
        """, (project_id, project_id, project_id))

        counts = {}
        for row in cur.fetchall():
            folder_id = row[0]
            video_count = row[1] or 0
            counts[folder_id] = video_count

        return counts


# Add these methods to ReferenceDB class
def patch_reference_db():
    """
    Add optimized methods to ReferenceDB class.

    Call this at application startup to enable performance optimizations.

    Usage:
        from db_performance_optimizations import patch_reference_db
        patch_reference_db()
    """
    from reference_db import ReferenceDB

    # Add batch counting method
    ReferenceDB.get_folder_counts_batch = lambda self, project_id: get_folder_counts_batch_optimized(self, project_id)

    # Replace existing method with optimized version
    ReferenceDB.get_image_count_recursive_optimized = lambda self, folder_id, project_id: get_image_count_recursive_optimized(self, folder_id, project_id)

    # Add video batch counting
    ReferenceDB.get_video_counts_batch = lambda self, project_id: get_video_counts_batch_optimized(self, project_id)

    print("✓ Database performance optimizations enabled")


# ============================================================================
# COMPOUND INDEX RECOMMENDATIONS
# ============================================================================

RECOMMENDED_INDEXES = """
-- Compound index for photo_metadata filtering by project + folder
-- Used in: folder tree queries, photo listing
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project_folder
ON photo_metadata(project_id, folder_id);

-- Compound index for photo_metadata filtering by project + date
-- Used in: date branch queries, timeline views
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project_date
ON photo_metadata(project_id, created_year, created_date);

-- Compound index for video_metadata filtering by project + folder
-- Used in: folder tree queries (videos), video listing
CREATE INDEX IF NOT EXISTS idx_video_metadata_project_folder
ON video_metadata(project_id, folder_id);

-- Compound index for video_metadata filtering by project + date
-- Used in: video date branches, video timeline
CREATE INDEX IF NOT EXISTS idx_video_metadata_project_date
ON video_metadata(project_id, created_year, created_date);

-- Compound index for project_images filtering
-- Used in: branch queries, photo assignments
CREATE INDEX IF NOT EXISTS idx_project_images_project_branch
ON project_images(project_id, branch_key, image_path);

-- Compound index for photo_folders recursive queries
-- Used in: folder tree traversal, subfolder lookups
CREATE INDEX IF NOT EXISTS idx_photo_folders_project_parent
ON photo_folders(project_id, parent_id);
"""


def add_compound_indexes(db):
    """
    Add recommended compound indexes for performance.

    These indexes optimize common query patterns:
    - Filtering photos/videos by project + folder
    - Filtering by project + date
    - Folder tree traversal

    Args:
        db: ReferenceDB instance
    """
    with db._connect() as conn:
        conn.executescript(RECOMMENDED_INDEXES)
        conn.commit()

    print("✓ Compound indexes added")


if __name__ == "__main__":
    print("Database Performance Optimizations")
    print("=" * 70)
    print()
    print("Key Optimizations:")
    print("1. Batch folder counting (fixes N+1 queries)")
    print("2. Direct project_id filtering (no JOINs needed)")
    print("3. Compound indexes for common patterns")
    print()
    print("Usage:")
    print("  from db_performance_optimizations import patch_reference_db")
    print("  patch_reference_db()")
    print()
    print("To add compound indexes:")
    print("  from db_performance_optimizations import add_compound_indexes")
    print("  add_compound_indexes(db)")
