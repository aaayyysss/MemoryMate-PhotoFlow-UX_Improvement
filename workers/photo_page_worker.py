# workers/photo_page_worker.py
# Generation-aware paged photo/video loader.
#
# Uses PhotoQueryService to fetch a single page of results and emits
# typed signals so the UI can merge rows incrementally without blocking.
#
# Generation tracking lets the caller discard stale pages after a
# filter change or project switch.

import logging
from typing import Optional, Dict, Any

from PySide6.QtCore import QRunnable, QObject, Signal

logger = logging.getLogger(__name__)


class PhotoPageSignals(QObject):
    """Signals emitted by PhotoPageWorker.

    Create ONE instance per layout and pass it to every worker via the
    *signals* constructor argument.  This avoids repeated connect/disconnect.
    """

    # (generation, offset, rows)  — rows is list[dict]
    page_ready = Signal(int, int, list)

    # (generation, total_loaded)  — all requested pages delivered
    done = Signal(int, int)

    # (generation, error_message)
    error = Signal(int, str)

    # (generation, total_count) — emitted once after the initial count query
    count_ready = Signal(int, int)


class PhotoPageWorker(QRunnable):
    """
    Background worker that fetches a single page of photo/video rows.

    Usage (shared signals — connect once):
        self._page_signals = PhotoPageSignals()
        self._page_signals.page_ready.connect(self._on_page_ready)
        ...
        worker = PhotoPageWorker(
            project_id=1, generation=42, offset=0, limit=250,
            signals=self._page_signals,
        )
        QThreadPool.globalInstance().start(worker)
    """

    def __init__(
        self,
        project_id: int,
        generation: int,
        offset: int = 0,
        limit: int = 250,
        filters: Optional[Dict[str, Any]] = None,
        signals: Optional[PhotoPageSignals] = None,
        *,
        include_count: bool = False,
    ):
        super().__init__()
        self.setAutoDelete(True)
        self.signals = signals or PhotoPageSignals()
        self.project_id = project_id
        self.generation = generation
        self.offset = offset
        self.limit = limit
        self.filters = filters or {}
        self._include_count = include_count

    def run(self):
        """Execute query in background thread."""
        try:
            from services.photo_query_service import PhotoQueryService

            svc = PhotoQueryService()

            # Optional: emit total count (first page only)
            if self._include_count:
                total = svc.count_photos(self.project_id, self.filters)
                self.signals.count_ready.emit(self.generation, total)
                logger.debug(
                    "[PhotoPageWorker] gen=%d count=%d", self.generation, total,
                )

            rows = svc.fetch_page(
                self.project_id,
                self.filters,
                offset=self.offset,
                limit=self.limit,
            )

            self.signals.page_ready.emit(self.generation, self.offset, rows)

            logger.debug(
                "[PhotoPageWorker] gen=%d offset=%d -> %d rows",
                self.generation, self.offset, len(rows),
            )

        except Exception as exc:
            import traceback
            msg = f"{exc}\n{traceback.format_exc()}"
            logger.error("[PhotoPageWorker] gen=%d error: %s", self.generation, exc)
            self.signals.error.emit(self.generation, msg)
