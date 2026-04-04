# services/people_review_service.py

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


UNNAMED_CLUSTER_ASSIGNED = "unnamed_cluster_assigned"
UNNAMED_CLUSTER_KEPT_SEPARATE = "unnamed_cluster_kept_separate"
UNNAMED_CLUSTER_IGNORED = "unnamed_cluster_ignored"
UNNAMED_CLUSTER_LOW_CONFIDENCE = "unnamed_cluster_low_confidence"

PEOPLE_INDEX_REFRESH_REQUESTED = "people_index_refresh_requested"
PEOPLE_SIDEBAR_REFRESH_REQUESTED = "people_sidebar_refresh_requested"
PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED = "people_review_queue_refresh_requested"
SEARCH_PERSON_FACETS_REFRESH_REQUESTED = "search_person_facets_refresh_requested"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class PeopleReviewService:
    """
    UI-facing review service for:
    - merge review queue reads
    - compare payload generation
    - unnamed cluster workflows
    """

    def __init__(
        self,
        people_review_repo,
        identity_repo,
        identity_resolution_service,
        event_bus,
        cluster_stats_service=None,
    ):
        self.people_review_repo = people_review_repo
        self.identity_repo = identity_repo
        self.identity_resolution_service = identity_resolution_service
        self.event_bus = event_bus
        self.cluster_stats_service = cluster_stats_service

    # ------------------------------------------------------------------
    # Merge review queue
    # ------------------------------------------------------------------

    def get_merge_review_queue(
        self,
        include_reviewed: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if include_reviewed:
            statuses = ["unreviewed", "skipped", "accepted", "rejected"]
        else:
            statuses = ["unreviewed", "skipped"]

        items = self.people_review_repo.list_merge_candidates(
            status=statuses,
            include_invalidated=False,
            limit=limit,
        )

        return [self._to_merge_queue_item(item) for item in items]

    def get_merge_candidate_compare_payload(self, candidate_id: str) -> dict[str, Any]:
        candidate = self.people_review_repo.get_merge_candidate(candidate_id)
        if not candidate:
            return {}

        return {
            "candidate_id": candidate.candidate_id,
            "confidence_score": candidate.confidence_score,
            "confidence_band": candidate.confidence_band,
            "status": candidate.status,
            "rationale": candidate.rationale,
            "left_cluster": self._build_cluster_panel_payload(candidate.cluster_a_id),
            "right_cluster": self._build_cluster_panel_payload(candidate.cluster_b_id),
        }

    def get_merge_compare_payload(self, left_id: str, right_id: str) -> dict[str, Any]:
        """Look up candidate by cluster pair and return compare payload with left/right keys."""
        candidate = self.people_review_repo.find_existing_pair(left_id, right_id)
        if not candidate:
            return {}
        panel = self.get_merge_candidate_compare_payload(candidate.candidate_id)
        # Map to the left/right keys the dialog expects
        return {
            "left": panel.get("left_cluster", {}),
            "right": panel.get("right_cluster", {}),
            "candidate_id": panel.get("candidate_id"),
            "confidence_score": panel.get("confidence_score"),
            "rationale": panel.get("rationale"),
        }

    def get_cluster_assign_suggestions(
        self,
        cluster_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Minimal first-pass implementation.

        Replace this later with:
        - embedding similarity to identities
        - event co-occurrence hints
        - time-span proximity
        """
        cur = self.identity_repo.conn.execute(
            """
            SELECT
                identity_id,
                display_name,
                canonical_cluster_id,
                is_protected
            FROM person_identity
            WHERE is_hidden = 0
            ORDER BY
                is_protected DESC,
                updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "identity_id": row[0],
                "display_name": row[1],
                "canonical_cluster_id": row[2],
                "is_protected": bool(row[3]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Unnamed cluster queue
    # ------------------------------------------------------------------

    def get_unnamed_review_queue(
        self,
        include_low_value: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Pulls raw unresolved clusters from repository and enriches them lightly.
        """
        raw_items = self.people_review_repo.list_unnamed_review_candidates(limit=limit)
        queue: list[dict[str, Any]] = []

        for item in raw_items:
            cluster_id = item["cluster_id"]
            active_decision = self.people_review_repo.get_active_cluster_review_decision(cluster_id)

            if active_decision and active_decision.decision_type == "ignore":
                continue
            if active_decision and active_decision.decision_type == "low_confidence" and not include_low_value:
                continue

            queue.append(
                {
                    "cluster_id": cluster_id,
                    "photo_count": item.get("photo_count", 0),
                    "last_seen_at": item.get("last_seen_at"),
                    "decision_type": active_decision.decision_type if active_decision else None,
                    "badges": self._build_cluster_badges(active_decision.decision_type if active_decision else None),
                    "suggestions": self.get_cluster_assign_suggestions(cluster_id, limit=3),
                }
            )

        queue.sort(
            key=lambda x: (
                0 if x["decision_type"] is None else 1,
                -(x.get("photo_count") or 0),
                x.get("last_seen_at") or "",
            )
        )
        return queue

    # ------------------------------------------------------------------
    # Unnamed cluster actions
    # ------------------------------------------------------------------

    def assign_cluster_to_existing_identity(
        self,
        cluster_id: str,
        target_identity_id: str,
        performed_by: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()

        self.people_review_repo.deactivate_cluster_review_decisions(cluster_id)
        self.people_review_repo.save_cluster_review_decision(
            decision_id=_new_id("dec"),
            cluster_id=cluster_id,
            decision_type="assign_existing",
            target_identity_id=target_identity_id,
            notes=None,
            created_at=now,
            created_by=performed_by,
            is_active=True,
            source="user",
        )

        link_id = self.identity_repo.attach_cluster_to_identity(
            link_id=_new_id("lnk"),
            identity_id=target_identity_id,
            cluster_id=cluster_id,
            link_type="manual_assign",
            created_at=now,
            source="user_assign",
        )

        action_payload = {
            "cluster_id": cluster_id,
            "target_identity_id": target_identity_id,
            "linked_at": now,
            "link_id": link_id,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="cluster_assigned",
            created_at=now,
            identity_id=target_identity_id,
            cluster_id=cluster_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=True,
        )

        self._emit_cluster_decision_events(
            event_name=UNNAMED_CLUSTER_ASSIGNED,
            identity_ids=[target_identity_id],
            cluster_ids=[cluster_id],
            payload={
                "cluster_id": cluster_id,
                "identity_id": target_identity_id,
                "action_id": action_id,
            },
        )

        return {
            "status": "assigned",
            "identity_id": target_identity_id,
            "cluster_id": cluster_id,
            "action_id": action_id,
        }

    def keep_cluster_as_separate_person(
        self,
        cluster_id: str,
        performed_by: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()

        self.people_review_repo.deactivate_cluster_review_decisions(cluster_id)
        self.people_review_repo.save_cluster_review_decision(
            decision_id=_new_id("dec"),
            cluster_id=cluster_id,
            decision_type="keep_separate",
            target_identity_id=None,
            notes=None,
            created_at=now,
            created_by=performed_by,
            is_active=True,
            source="user",
        )

        identity_id = self.identity_resolution_service.ensure_identity_for_cluster(
            cluster_id=cluster_id,
            source="keep_separate",
        )

        action_payload = {
            "cluster_id": cluster_id,
            "identity_id": identity_id,
            "kept_separate_at": now,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="cluster_kept_separate",
            created_at=now,
            identity_id=identity_id,
            cluster_id=cluster_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=False,
        )

        self._emit_cluster_decision_events(
            event_name=UNNAMED_CLUSTER_KEPT_SEPARATE,
            identity_ids=[identity_id],
            cluster_ids=[cluster_id],
            payload={
                "cluster_id": cluster_id,
                "identity_id": identity_id,
                "action_id": action_id,
            },
        )

        return {
            "status": "kept_separate",
            "identity_id": identity_id,
            "cluster_id": cluster_id,
            "action_id": action_id,
        }

    def ignore_cluster(
        self,
        cluster_id: str,
        performed_by: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()

        self.people_review_repo.deactivate_cluster_review_decisions(cluster_id)
        self.people_review_repo.save_cluster_review_decision(
            decision_id=_new_id("dec"),
            cluster_id=cluster_id,
            decision_type="ignore",
            target_identity_id=None,
            notes=notes,
            created_at=now,
            created_by=performed_by,
            is_active=True,
            source="user",
        )

        action_payload = {
            "cluster_id": cluster_id,
            "ignored_at": now,
            "notes": notes,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="cluster_ignored",
            created_at=now,
            cluster_id=cluster_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=False,
        )

        self._emit_cluster_decision_events(
            event_name=UNNAMED_CLUSTER_IGNORED,
            identity_ids=[],
            cluster_ids=[cluster_id],
            payload={
                "cluster_id": cluster_id,
                "action_id": action_id,
            },
        )

        return {
            "status": "ignored",
            "cluster_id": cluster_id,
            "action_id": action_id,
        }

    def mark_cluster_low_confidence(
        self,
        cluster_id: str,
        performed_by: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()

        self.people_review_repo.deactivate_cluster_review_decisions(cluster_id)
        self.people_review_repo.save_cluster_review_decision(
            decision_id=_new_id("dec"),
            cluster_id=cluster_id,
            decision_type="low_confidence",
            target_identity_id=None,
            notes=notes,
            created_at=now,
            created_by=performed_by,
            is_active=True,
            source="user",
        )

        action_payload = {
            "cluster_id": cluster_id,
            "marked_at": now,
            "notes": notes,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="cluster_low_confidence",
            created_at=now,
            cluster_id=cluster_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=False,
        )

        self._emit_cluster_decision_events(
            event_name=UNNAMED_CLUSTER_LOW_CONFIDENCE,
            identity_ids=[],
            cluster_ids=[cluster_id],
            payload={
                "cluster_id": cluster_id,
                "action_id": action_id,
            },
        )

        return {
            "status": "low_confidence",
            "cluster_id": cluster_id,
            "action_id": action_id,
        }

    # ------------------------------------------------------------------
    # Internal UI payload builders
    # ------------------------------------------------------------------

    def _to_merge_queue_item(self, item) -> dict[str, Any]:
        left_identity = self.identity_repo.get_identity_by_cluster_id(item.cluster_a_id)
        right_identity = self.identity_repo.get_identity_by_cluster_id(item.cluster_b_id)

        return {
            "candidate_id": item.candidate_id,
            "cluster_a_id": item.cluster_a_id,
            "cluster_b_id": item.cluster_b_id,
            "confidence_score": item.confidence_score,
            "confidence_band": item.confidence_band,
            "status": item.status,
            "created_at": item.created_at,
            "rationale": item.rationale,
            "left_label": left_identity.display_name if left_identity and left_identity.display_name else item.cluster_a_id,
            "right_label": right_identity.display_name if right_identity and right_identity.display_name else item.cluster_b_id,
            "badges": self._build_merge_badges(item.confidence_band, item.status),
        }

    def _build_cluster_panel_payload(self, cluster_id: str) -> dict[str, Any]:
        """
        Minimal payload.
        Replace/enrich later with:
        - hero face thumbnail
        - date span
        - preview strip
        - co-occurrence hints
        """
        identity = self.identity_repo.get_identity_by_cluster_id(cluster_id)
        decision = self.people_review_repo.get_active_cluster_review_decision(cluster_id)

        return {
            "cluster_id": cluster_id,
            "identity_id": identity.identity_id if identity else None,
            "display_name": identity.display_name if identity else None,
            "canonical_cluster_id": identity.canonical_cluster_id if identity else None,
            "is_protected": bool(identity.is_protected) if identity else False,
            "decision_type": decision.decision_type if decision else None,
            "badges": self._build_cluster_badges(decision.decision_type if decision else None),
        }

    @staticmethod
    def _build_merge_badges(confidence_band: str, status: str) -> list[str]:
        badges: list[str] = []

        if confidence_band == "high":
            badges.append("High confidence")
        elif confidence_band == "medium":
            badges.append("Review")
        elif confidence_band == "low":
            badges.append("Review carefully")

        if status == "accepted":
            badges.append("Reviewed")
        elif status == "rejected":
            badges.append("Rejected")
        elif status == "skipped":
            badges.append("Skipped")

        return badges

    @staticmethod
    def _build_cluster_badges(decision_type: str | None) -> list[str]:
        badges: list[str] = []
        if decision_type == "assign_existing":
            badges.append("Assigned")
        elif decision_type == "keep_separate":
            badges.append("Separate")
        elif decision_type == "ignore":
            badges.append("Ignored")
        elif decision_type == "low_confidence":
            badges.append("Low confidence")
        return badges

    def _emit_cluster_decision_events(
        self,
        event_name: str,
        identity_ids: list[str],
        cluster_ids: list[str],
        payload: dict[str, Any],
    ) -> None:
        self.event_bus.emit(event_name, payload)

        refresh_payload = {
            "identity_ids": identity_ids,
            "cluster_ids": cluster_ids,
            "reason": event_name,
        }
        self.event_bus.emit(PEOPLE_INDEX_REFRESH_REQUESTED, refresh_payload)
        self.event_bus.emit(PEOPLE_SIDEBAR_REFRESH_REQUESTED, refresh_payload)
        self.event_bus.emit(PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED, refresh_payload)
        self.event_bus.emit(SEARCH_PERSON_FACETS_REFRESH_REQUESTED, refresh_payload)
