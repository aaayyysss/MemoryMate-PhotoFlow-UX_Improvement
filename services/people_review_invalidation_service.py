"""
UX-11D: People Review Invalidation Service.

Handles what happens when clustering/model version changes.

Critical rule: Do NOT automatically erase accepted or rejected human decisions.
Invalidate only stale 'unreviewed' and 'skipped' candidates.
Human decisions remain durable unless explicitly reset.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from repository.identity_repository import IdentityRepository
from repository.people_review_repository import PeopleReviewRepository
from services.domain_events import (
    MERGE_CANDIDATE_INVALIDATED,
    PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED,
)

logger = logging.getLogger(__name__)


class PeopleReviewInvalidationService:
    """
    UX-11D: Invalidation logic for merge candidates.

    Called when:
      - Reclustering changes cluster membership
      - Embedding model version changes
      - Manual reset requested
    """

    def __init__(
        self,
        people_review_repo: PeopleReviewRepository,
        identity_repo: IdentityRepository,
        event_bus=None,
    ):
        self.people_review_repo = people_review_repo
        self.identity_repo = identity_repo
        self.event_bus = event_bus

    def invalidate_candidates_for_reclustered_clusters(
        self,
        cluster_ids: List[str],
        reason: str = "cluster_membership_changed",
    ) -> int:
        """Invalidate unreviewed/skipped candidates involving changed clusters.
        Does NOT touch accepted/rejected human decisions."""
        if not cluster_ids:
            return 0

        count = self.people_review_repo.invalidate_candidates_for_cluster_ids(
            cluster_ids, reason,
        )

        if count > 0:
            logger.warning(
                "[Invalidation] Invalidated %d candidates for %d reclustered clusters",
                count, len(cluster_ids),
            )
            self._emit_event(MERGE_CANDIDATE_INVALIDATED, {
                "cluster_ids": cluster_ids,
                "count": count,
                "reason": reason,
            })
            self._emit_event(PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED, {
                "reason": reason,
            })

        return count

    def invalidate_candidates_for_model_change(
        self,
        old_model_version: str,
        new_model_version: str,
    ) -> int:
        """Invalidate unreviewed/skipped candidates from an outdated model.
        Human decisions (accepted/rejected) are preserved."""
        reason = f"model_version_changed:{old_model_version}->{new_model_version}"
        count = self.people_review_repo.invalidate_candidates_for_model_version_change(
            old_model_version, new_model_version, reason,
        )

        if count > 0:
            logger.warning(
                "[Invalidation] Invalidated %d candidates from model v=%s (now v=%s)",
                count, old_model_version, new_model_version,
            )
            self._emit_event(MERGE_CANDIDATE_INVALIDATED, {
                "old_model_version": old_model_version,
                "new_model_version": new_model_version,
                "count": count,
                "reason": reason,
            })
            self._emit_event(PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED, {
                "reason": reason,
            })

        return count

    def emit_revalidation_events(self) -> None:
        """Emit refresh events after invalidation cycle completes."""
        self._emit_event(PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED, {
            "reason": "revalidation_complete",
        })

    def _emit_event(self, event_name: str, payload: dict) -> None:
        if self.event_bus and hasattr(self.event_bus, "emit"):
            try:
                self.event_bus.emit(event_name, payload)
            except Exception:
                logger.debug("[Invalidation] Event emission failed: %s",
                             event_name, exc_info=True)
