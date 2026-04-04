"""
BatchIterator - Chunked Processing with Checkpoints

Version: 1.0.0
Date: 2026-02-01

Provides resumable batch processing for large datasets.
Works with any iterable (photo IDs, file paths, etc.) and saves
checkpoints to database for crash recovery.

Key Features:
1. Configurable batch size
2. Checkpoint saving/loading
3. Resume from last checkpoint
4. Cancellation checking between batches
5. Progress calculation

Usage:
    from services.batch_iterator import BatchIterator

    # Create iterator with checkpoint support
    iterator = BatchIterator(
        items=photo_ids,
        batch_size=50,
        job_id=123,
        checkpoint_key='face_scan_123'
    )

    # Resume from checkpoint if exists
    iterator.load_checkpoint()

    # Process batches
    for batch in iterator:
        for item in batch:
            process_item(item)

        # Save checkpoint after each batch
        iterator.save_checkpoint()

        # Check for cancellation
        if should_cancel:
            break

    # Clear checkpoint on completion
    iterator.clear_checkpoint()
"""

import json
from typing import TypeVar, List, Iterator, Optional, Any, Dict, Callable
from dataclasses import dataclass, field
from datetime import datetime

from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


@dataclass
class BatchCheckpoint:
    """Checkpoint data for batch processing."""
    checkpoint_key: str
    items_processed: int
    total_items: int
    last_item_index: int
    last_item_id: Optional[Any] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)
    saved_at: Optional[str] = None


class BatchIterator(Iterator[List[T]]):
    """
    Iterator that yields batches of items with checkpoint support.

    Enables resumable processing of large datasets by saving progress
    to the database after each batch.
    """

    def __init__(
        self,
        items: List[T],
        batch_size: int = 50,
        job_id: Optional[int] = None,
        checkpoint_key: Optional[str] = None,
        on_batch_complete: Optional[Callable[[int, int], None]] = None
    ):
        """
        Initialize batch iterator.

        Args:
            items: List of items to process
            batch_size: Number of items per batch
            job_id: Optional job ID for checkpoint key generation
            checkpoint_key: Custom checkpoint key (or auto-generated from job_id)
            on_batch_complete: Optional callback(processed, total) after each batch
        """
        self.items = items
        self.batch_size = batch_size
        self.job_id = job_id
        self.checkpoint_key = checkpoint_key or (f"job_{job_id}" if job_id else None)
        self.on_batch_complete = on_batch_complete

        self._current_index = 0
        self._total = len(items)
        self._db = DatabaseConnection()

        # Ensure checkpoint table exists
        self._ensure_checkpoint_table()

    def __iter__(self) -> 'BatchIterator':
        return self

    def __next__(self) -> List[T]:
        if self._current_index >= self._total:
            raise StopIteration

        # Get next batch
        end_index = min(self._current_index + self.batch_size, self._total)
        batch = self.items[self._current_index:end_index]

        # Update position
        self._current_index = end_index

        return batch

    @property
    def progress(self) -> float:
        """Current progress as a fraction (0.0 to 1.0)."""
        return self._current_index / self._total if self._total > 0 else 0.0

    @property
    def processed(self) -> int:
        """Number of items processed so far."""
        return self._current_index

    @property
    def total(self) -> int:
        """Total number of items."""
        return self._total

    @property
    def remaining(self) -> int:
        """Number of items remaining."""
        return self._total - self._current_index

    @property
    def is_complete(self) -> bool:
        """Check if all items have been processed."""
        return self._current_index >= self._total

    def peek_next_batch(self) -> List[T]:
        """Preview the next batch without advancing the iterator."""
        if self._current_index >= self._total:
            return []
        end_index = min(self._current_index + self.batch_size, self._total)
        return self.items[self._current_index:end_index]

    # ─────────────────────────────────────────────────────────────────────────
    # Checkpoint Management
    # ─────────────────────────────────────────────────────────────────────────

    def _ensure_checkpoint_table(self):
        """Ensure the checkpoint table exists."""
        try:
            with self._db.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS batch_checkpoints (
                        checkpoint_key TEXT PRIMARY KEY,
                        items_processed INTEGER NOT NULL,
                        total_items INTEGER NOT NULL,
                        last_item_index INTEGER NOT NULL,
                        last_item_id TEXT,
                        extra_data TEXT,
                        saved_at TEXT NOT NULL
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"[BatchIterator] Could not create checkpoint table: {e}")

    def save_checkpoint(self, extra_data: Optional[Dict[str, Any]] = None) -> bool:
        """
        Save current progress as a checkpoint.

        Args:
            extra_data: Optional additional data to save with checkpoint

        Returns:
            bool: True if saved successfully
        """
        if not self.checkpoint_key:
            return False

        try:
            # Get current item ID if available
            last_item_id = None
            if self._current_index > 0 and self._current_index <= len(self.items):
                last_item = self.items[self._current_index - 1]
                last_item_id = str(last_item) if last_item is not None else None

            with self._db.get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO batch_checkpoints (
                        checkpoint_key, items_processed, total_items,
                        last_item_index, last_item_id, extra_data, saved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.checkpoint_key,
                    self._current_index,
                    self._total,
                    self._current_index,
                    last_item_id,
                    json.dumps(extra_data) if extra_data else None,
                    datetime.now().isoformat()
                ))
                conn.commit()

            logger.debug(
                f"[BatchIterator] Saved checkpoint '{self.checkpoint_key}': "
                f"{self._current_index}/{self._total}"
            )

            # Call completion callback if provided
            if self.on_batch_complete:
                self.on_batch_complete(self._current_index, self._total)

            return True

        except Exception as e:
            logger.error(f"[BatchIterator] Failed to save checkpoint: {e}")
            return False

    def load_checkpoint(self) -> Optional[BatchCheckpoint]:
        """
        Load checkpoint and resume from saved position.

        Returns:
            BatchCheckpoint if found, None otherwise
        """
        if not self.checkpoint_key:
            return None

        try:
            with self._db.get_connection() as conn:
                row = conn.execute("""
                    SELECT * FROM batch_checkpoints WHERE checkpoint_key = ?
                """, (self.checkpoint_key,)).fetchone()

                if not row:
                    return None

                checkpoint = BatchCheckpoint(
                    checkpoint_key=row['checkpoint_key'],
                    items_processed=row['items_processed'],
                    total_items=row['total_items'],
                    last_item_index=row['last_item_index'],
                    last_item_id=row['last_item_id'],
                    extra_data=json.loads(row['extra_data']) if row['extra_data'] else {},
                    saved_at=row['saved_at']
                )

                # Resume from checkpoint
                self._current_index = checkpoint.last_item_index

                logger.info(
                    f"[BatchIterator] Loaded checkpoint '{self.checkpoint_key}': "
                    f"resuming from {self._current_index}/{self._total}"
                )

                return checkpoint

        except Exception as e:
            logger.warning(f"[BatchIterator] Failed to load checkpoint: {e}")
            return None

    def clear_checkpoint(self) -> bool:
        """
        Clear the checkpoint (call on successful completion).

        Returns:
            bool: True if cleared successfully
        """
        if not self.checkpoint_key:
            return False

        try:
            with self._db.get_connection() as conn:
                conn.execute(
                    "DELETE FROM batch_checkpoints WHERE checkpoint_key = ?",
                    (self.checkpoint_key,)
                )
                conn.commit()

            logger.debug(f"[BatchIterator] Cleared checkpoint '{self.checkpoint_key}'")
            return True

        except Exception as e:
            logger.error(f"[BatchIterator] Failed to clear checkpoint: {e}")
            return False

    def has_checkpoint(self) -> bool:
        """Check if a checkpoint exists for this iterator."""
        if not self.checkpoint_key:
            return False

        try:
            with self._db.get_connection() as conn:
                row = conn.execute("""
                    SELECT 1 FROM batch_checkpoints WHERE checkpoint_key = ?
                """, (self.checkpoint_key,)).fetchone()
                return row is not None
        except Exception:
            return False


class ChunkedProcessor:
    """
    High-level chunked processing with automatic checkpoint handling.

    Simplifies the pattern of:
    1. Load checkpoint if exists
    2. Process items in batches
    3. Save checkpoint after each batch
    4. Clear checkpoint on completion

    Usage:
        def process_photo(photo_id):
            # Do something with photo
            pass

        processor = ChunkedProcessor(
            items=photo_ids,
            process_fn=process_photo,
            batch_size=50,
            checkpoint_key='my_job_123'
        )

        # Returns True if completed, False if canceled
        success = processor.run(
            on_progress=lambda cur, total: print(f"{cur}/{total}"),
            should_cancel=lambda: user_requested_cancel
        )
    """

    def __init__(
        self,
        items: List[T],
        process_fn: Callable[[T], Any],
        batch_size: int = 50,
        checkpoint_key: Optional[str] = None,
        on_item_error: Optional[Callable[[T, Exception], None]] = None
    ):
        """
        Initialize chunked processor.

        Args:
            items: List of items to process
            process_fn: Function to call for each item
            batch_size: Items per batch
            checkpoint_key: Key for checkpoint storage
            on_item_error: Optional error handler (item, exception)
        """
        self.items = items
        self.process_fn = process_fn
        self.batch_size = batch_size
        self.checkpoint_key = checkpoint_key
        self.on_item_error = on_item_error

        self.success_count = 0
        self.failed_count = 0
        self.skipped_count = 0

    def run(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_batch_complete: Optional[Callable[[List[T]], None]] = None
    ) -> bool:
        """
        Run the chunked processing.

        Args:
            on_progress: Progress callback (current, total)
            should_cancel: Cancellation check function
            on_batch_complete: Callback after each batch

        Returns:
            bool: True if completed, False if canceled
        """
        iterator = BatchIterator(
            items=self.items,
            batch_size=self.batch_size,
            checkpoint_key=self.checkpoint_key
        )

        # Try to resume from checkpoint
        checkpoint = iterator.load_checkpoint()
        if checkpoint:
            self.success_count = checkpoint.extra_data.get('success_count', 0)
            self.failed_count = checkpoint.extra_data.get('failed_count', 0)
            self.skipped_count = checkpoint.extra_data.get('skipped_count', 0)

        try:
            for batch in iterator:
                # Check for cancellation before each batch
                if should_cancel and should_cancel():
                    iterator.save_checkpoint(extra_data={
                        'success_count': self.success_count,
                        'failed_count': self.failed_count,
                        'skipped_count': self.skipped_count,
                        'canceled': True
                    })
                    return False

                # Process batch items
                for item in batch:
                    try:
                        self.process_fn(item)
                        self.success_count += 1
                    except Exception as e:
                        self.failed_count += 1
                        if self.on_item_error:
                            self.on_item_error(item, e)
                        else:
                            logger.error(f"[ChunkedProcessor] Item error: {e}")

                # Save checkpoint after batch
                iterator.save_checkpoint(extra_data={
                    'success_count': self.success_count,
                    'failed_count': self.failed_count,
                    'skipped_count': self.skipped_count
                })

                # Progress callback
                if on_progress:
                    on_progress(iterator.processed, iterator.total)

                # Batch complete callback
                if on_batch_complete:
                    on_batch_complete(batch)

            # Clear checkpoint on successful completion
            iterator.clear_checkpoint()
            return True

        except Exception as e:
            logger.error(f"[ChunkedProcessor] Fatal error: {e}")
            iterator.save_checkpoint(extra_data={
                'success_count': self.success_count,
                'failed_count': self.failed_count,
                'skipped_count': self.skipped_count,
                'error': str(e)
            })
            raise


def get_checkpoint_info(checkpoint_key: str) -> Optional[BatchCheckpoint]:
    """
    Get checkpoint info without creating an iterator.

    Args:
        checkpoint_key: Checkpoint key to look up

    Returns:
        BatchCheckpoint if found, None otherwise
    """
    try:
        db = DatabaseConnection()
        with db.get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM batch_checkpoints WHERE checkpoint_key = ?
            """, (checkpoint_key,)).fetchone()

            if not row:
                return None

            return BatchCheckpoint(
                checkpoint_key=row['checkpoint_key'],
                items_processed=row['items_processed'],
                total_items=row['total_items'],
                last_item_index=row['last_item_index'],
                last_item_id=row['last_item_id'],
                extra_data=json.loads(row['extra_data']) if row['extra_data'] else {},
                saved_at=row['saved_at']
            )
    except Exception:
        return None


def clear_all_checkpoints(prefix: Optional[str] = None) -> int:
    """
    Clear all checkpoints (or those matching a prefix).

    Args:
        prefix: Optional prefix to filter checkpoints

    Returns:
        int: Number of checkpoints cleared
    """
    try:
        db = DatabaseConnection()
        with db.get_connection() as conn:
            if prefix:
                result = conn.execute(
                    "DELETE FROM batch_checkpoints WHERE checkpoint_key LIKE ?",
                    (f"{prefix}%",)
                )
            else:
                result = conn.execute("DELETE FROM batch_checkpoints")
            conn.commit()
            return result.rowcount
    except Exception as e:
        logger.error(f"[BatchIterator] Failed to clear checkpoints: {e}")
        return 0
