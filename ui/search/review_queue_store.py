"""
UX-11D: Review Queue Store — UI-facing state holder for merge and unnamed queues.

Qt signal-based store that centralizes queue state and emits change signals.
Dialogs and sidebar consume this store instead of rebuilding state themselves.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class ReviewQueueStore(QObject):
    """
    UX-11D: Centralized store for review queue state.

    Signals:
        mergeQueueChanged: emitted when merge review queue updates
        unnamedQueueChanged: emitted when unnamed cluster queue updates
        selectionChanged: emitted when selected item changes
        badgesChanged: emitted when badge counts change
    """

    mergeQueueChanged = Signal()
    unnamedQueueChanged = Signal()
    selectionChanged = Signal()
    badgesChanged = Signal()

    def __init__(self, people_review_service=None, parent=None):
        super().__init__(parent)
        self.people_review_service = people_review_service

        # Queue data
        self.merge_queue: List[Dict[str, Any]] = []
        self.unnamed_queue: List[Dict[str, Any]] = []

        # Selection state
        self.selected_merge_candidate_id: Optional[str] = None
        self.selected_unnamed_cluster_id: Optional[str] = None

        # Badge counts cache
        self._merge_counts: Dict[str, int] = {
            "unreviewed": 0,
            "skipped": 0,
            "total_pending": 0,
        }
        self._unnamed_counts: Dict[str, int] = {
            "total": 0,
            "decided": 0,
            "pending": 0,
        }

    # ── Queue refresh ─────────────────────────────────────────────────

    def refresh_merge_queue(self, include_reviewed: bool = False) -> None:
        """Refresh merge review queue from service."""
        if not self.people_review_service:
            return
        try:
            self.merge_queue = self.people_review_service.get_merge_review_queue(
                include_reviewed=include_reviewed,
            )
            self._merge_counts = self.people_review_service.get_merge_queue_counts()
            self.mergeQueueChanged.emit()
            self.badgesChanged.emit()
        except Exception:
            logger.debug("[ReviewQueueStore] refresh_merge_queue failed", exc_info=True)

    def refresh_unnamed_queue(self, include_low_value: bool = False) -> None:
        """Refresh unnamed cluster queue from service."""
        if not self.people_review_service:
            return
        try:
            self.unnamed_queue = self.people_review_service.get_unnamed_review_queue(
                include_low_value=include_low_value,
            )
            self.unnamedQueueChanged.emit()
            self.badgesChanged.emit()
        except Exception:
            logger.debug("[ReviewQueueStore] refresh_unnamed_queue failed", exc_info=True)

    # ── Selection ─────────────────────────────────────────────────────

    def set_selected_merge_candidate(self, candidate_id: Optional[str]) -> None:
        if self.selected_merge_candidate_id != candidate_id:
            self.selected_merge_candidate_id = candidate_id
            self.selectionChanged.emit()

    def set_selected_unnamed_cluster(self, cluster_id: Optional[str]) -> None:
        if self.selected_unnamed_cluster_id != cluster_id:
            self.selected_unnamed_cluster_id = cluster_id
            self.selectionChanged.emit()

    # ── Payloads ──────────────────────────────────────────────────────

    def get_selected_merge_compare_payload(self) -> Optional[Dict[str, Any]]:
        if not self.selected_merge_candidate_id or not self.people_review_service:
            return None
        try:
            return self.people_review_service.get_merge_candidate_compare_payload(
                self.selected_merge_candidate_id,
            )
        except Exception:
            return None

    # ── Badge counts ──────────────────────────────────────────────────

    def get_merge_queue_counts(self) -> Dict[str, int]:
        return dict(self._merge_counts)

    def get_unnamed_queue_counts(self) -> Dict[str, int]:
        return dict(self._unnamed_counts)
