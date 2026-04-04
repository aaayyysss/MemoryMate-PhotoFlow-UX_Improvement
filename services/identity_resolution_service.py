# services/identity_resolution_service.py

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


MERGE_CANDIDATE_ACCEPTED = "merge_candidate_accepted"
MERGE_CANDIDATE_REJECTED = "merge_candidate_rejected"
MERGE_CANDIDATE_SKIPPED = "merge_candidate_skipped"
MERGE_REVERSED = "merge_reversed"
IDENTITY_PROTECTED = "identity_protected"
IDENTITY_UNPROTECTED = "identity_unprotected"
IDENTITY_CLUSTER_DETACHED = "identity_cluster_detached"

PEOPLE_INDEX_REFRESH_REQUESTED = "people_index_refresh_requested"
PEOPLE_SIDEBAR_REFRESH_REQUESTED = "people_sidebar_refresh_requested"
PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED = "people_review_queue_refresh_requested"
SEARCH_PERSON_FACETS_REFRESH_REQUESTED = "search_person_facets_refresh_requested"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class IdentityResolutionService:
    """
    Business owner for durable person identity semantics.

    Key rules:
    - merges are non-destructive
    - a merge means "attach cluster to identity", not "destroy cluster"
    - undo deactivates links, it does not erase history
    - accepted/rejected review decisions are durable human intent
    """

    def __init__(self, identity_repo, people_review_repo, event_bus):
        self.identity_repo = identity_repo
        self.people_review_repo = people_review_repo
        self.event_bus = event_bus

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def ensure_identity_for_cluster(self, cluster_id: str, source: str = "system") -> str:
        existing = self.identity_repo.get_identity_by_cluster_id(cluster_id)
        if existing:
            return existing.identity_id

        now = _now_iso()
        identity_id = _new_id("pid")
        self.identity_repo.create_identity(
            identity_id=identity_id,
            display_name=None,
            canonical_cluster_id=cluster_id,
            created_at=now,
            updated_at=now,
            source=source,
        )
        self.identity_repo.attach_cluster_to_identity(
            link_id=_new_id("lnk"),
            identity_id=identity_id,
            cluster_id=cluster_id,
            link_type="canonical",
            created_at=now,
            source=source,
        )
        return identity_id

    def get_identity_for_cluster(self, cluster_id: str):
        return self.identity_repo.get_identity_by_cluster_id(cluster_id)

    def get_identity_snapshot(self, identity_id: str) -> dict[str, Any] | None:
        identity = self.identity_repo.get_identity(identity_id)
        if not identity:
            return None

        links = self.identity_repo.list_active_cluster_links(identity_id)
        cluster_ids = [link.cluster_id for link in links]

        return {
            "identity_id": identity.identity_id,
            "display_name": identity.display_name,
            "canonical_cluster_id": identity.canonical_cluster_id,
            "is_protected": identity.is_protected,
            "is_hidden": identity.is_hidden,
            "source": identity.source,
            "cluster_ids": cluster_ids,
            "link_count": len(cluster_ids),
        }

    # ------------------------------------------------------------------
    # Merge candidate workflow
    # ------------------------------------------------------------------

    def accept_merge_candidate(
        self,
        candidate_id: str,
        reviewed_by: str | None = None,
    ) -> dict[str, Any]:
        candidate = self.people_review_repo.get_merge_candidate(candidate_id)
        if not candidate:
            raise ValueError(f"Unknown merge candidate: {candidate_id}")

        if candidate.status not in ("unreviewed", "skipped"):
            raise ValueError(
                f"Candidate {candidate_id} is not mergeable from status={candidate.status}"
            )

        identity_a = self.identity_repo.get_identity_by_cluster_id(candidate.cluster_a_id)
        identity_b = self.identity_repo.get_identity_by_cluster_id(candidate.cluster_b_id)

        # Already same identity, invalidate stale suggestion
        if identity_a and identity_b and identity_a.identity_id == identity_b.identity_id:
            self.people_review_repo.update_merge_candidate_status(
                candidate_id=candidate_id,
                status="invalidated",
                reviewed_at=_now_iso(),
                reviewed_by=reviewed_by,
                invalidated_reason="already_same_identity",
            )
            self.event_bus.emit(
                PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED,
                {"reason": "already_same_identity", "candidate_id": candidate_id},
            )
            return {
                "status": "already_same_identity",
                "identity_id": identity_a.identity_id,
            }

        target_identity_id, secondary_cluster_id = self._resolve_merge_target(
            cluster_a_id=candidate.cluster_a_id,
            cluster_b_id=candidate.cluster_b_id,
            identity_a=identity_a,
            identity_b=identity_b,
        )

        now = _now_iso()

        link_id = self.identity_repo.attach_cluster_to_identity(
            link_id=_new_id("lnk"),
            identity_id=target_identity_id,
            cluster_id=secondary_cluster_id,
            link_type="merged_into_identity",
            created_at=now,
            source="merge_accept",
        )

        action_payload = {
            "candidate_id": candidate_id,
            "accepted_at": now,
            "target_identity_id": target_identity_id,
            "secondary_cluster_id": secondary_cluster_id,
            "cluster_a_id": candidate.cluster_a_id,
            "cluster_b_id": candidate.cluster_b_id,
            "link_id": link_id,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="merge_accepted",
            created_at=now,
            identity_id=target_identity_id,
            related_cluster_id=secondary_cluster_id,
            candidate_id=candidate_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=reviewed_by,
            is_undoable=True,
        )

        self.people_review_repo.update_merge_candidate_status(
            candidate_id=candidate_id,
            status="accepted",
            reviewed_at=now,
            reviewed_by=reviewed_by,
        )

        self._emit_post_identity_change_events(
            event_name=MERGE_CANDIDATE_ACCEPTED,
            primary_identity_id=target_identity_id,
            affected_cluster_ids=[candidate.cluster_a_id, candidate.cluster_b_id],
            payload={
                "candidate_id": candidate_id,
                "identity_id": target_identity_id,
                "cluster_a_id": candidate.cluster_a_id,
                "cluster_b_id": candidate.cluster_b_id,
                "secondary_cluster_id": secondary_cluster_id,
                "action_id": action_id,
            },
        )

        return {
            "status": "accepted",
            "identity_id": target_identity_id,
            "secondary_cluster_id": secondary_cluster_id,
            "action_id": action_id,
        }

    def reject_merge_candidate(
        self,
        candidate_id: str,
        reviewed_by: str | None = None,
    ) -> dict[str, Any]:
        candidate = self.people_review_repo.get_merge_candidate(candidate_id)
        if not candidate:
            raise ValueError(f"Unknown merge candidate: {candidate_id}")

        now = _now_iso()
        self.people_review_repo.update_merge_candidate_status(
            candidate_id=candidate_id,
            status="rejected",
            reviewed_at=now,
            reviewed_by=reviewed_by,
        )

        action_payload = {
            "candidate_id": candidate_id,
            "rejected_at": now,
            "cluster_a_id": candidate.cluster_a_id,
            "cluster_b_id": candidate.cluster_b_id,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="merge_rejected",
            created_at=now,
            candidate_id=candidate_id,
            related_cluster_id=candidate.cluster_a_id,
            cluster_id=candidate.cluster_b_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=reviewed_by,
            is_undoable=False,
        )

        self.event_bus.emit(
            MERGE_CANDIDATE_REJECTED,
            {
                "candidate_id": candidate_id,
                "cluster_a_id": candidate.cluster_a_id,
                "cluster_b_id": candidate.cluster_b_id,
                "action_id": action_id,
            },
        )
        self.event_bus.emit(
            PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED,
            {"reason": "merge_rejected", "candidate_id": candidate_id},
        )

        return {"status": "rejected", "action_id": action_id}

    def skip_merge_candidate(
        self,
        candidate_id: str,
        reviewed_by: str | None = None,
    ) -> dict[str, Any]:
        candidate = self.people_review_repo.get_merge_candidate(candidate_id)
        if not candidate:
            raise ValueError(f"Unknown merge candidate: {candidate_id}")

        now = _now_iso()
        self.people_review_repo.update_merge_candidate_status(
            candidate_id=candidate_id,
            status="skipped",
            reviewed_at=now,
            reviewed_by=reviewed_by,
        )

        action_payload = {
            "candidate_id": candidate_id,
            "skipped_at": now,
            "cluster_a_id": candidate.cluster_a_id,
            "cluster_b_id": candidate.cluster_b_id,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="merge_skipped",
            created_at=now,
            candidate_id=candidate_id,
            related_cluster_id=candidate.cluster_a_id,
            cluster_id=candidate.cluster_b_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=reviewed_by,
            is_undoable=False,
        )

        self.event_bus.emit(
            MERGE_CANDIDATE_SKIPPED,
            {
                "candidate_id": candidate_id,
                "cluster_a_id": candidate.cluster_a_id,
                "cluster_b_id": candidate.cluster_b_id,
                "action_id": action_id,
            },
        )
        self.event_bus.emit(
            PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED,
            {"reason": "merge_skipped", "candidate_id": candidate_id},
        )

        return {"status": "skipped", "action_id": action_id}

    # ------------------------------------------------------------------
    # Undo and correction flows
    # ------------------------------------------------------------------

    def reverse_last_merge_for_identity(
        self,
        identity_id: str,
        performed_by: str | None = None,
    ) -> dict[str, Any]:
        action = self.identity_repo.get_last_undoable_action_for_identity(identity_id)
        if not action:
            return {"status": "no_undoable_action"}

        if action["action_type"] != "merge_accepted":
            return {
                "status": "unsupported_last_action",
                "action_type": action["action_type"],
            }

        payload = action["payload"] or {}
        secondary_cluster_id = payload.get("secondary_cluster_id")
        if not secondary_cluster_id:
            raise ValueError(
                f"Cannot reverse merge action {action['action_id']}, missing secondary_cluster_id"
            )

        now = _now_iso()
        affected = self.identity_repo.deactivate_cluster_link(
            identity_id=identity_id,
            cluster_id=secondary_cluster_id,
            removed_at=now,
        )
        if affected == 0:
            return {
                "status": "nothing_reversed",
                "identity_id": identity_id,
                "secondary_cluster_id": secondary_cluster_id,
            }

        undo_payload = {
            "reversed_action_id": action["action_id"],
            "identity_id": identity_id,
            "secondary_cluster_id": secondary_cluster_id,
            "reversed_at": now,
        }
        undo_action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="merge_reversed",
            created_at=now,
            identity_id=identity_id,
            cluster_id=secondary_cluster_id,
            payload_json=json.dumps(undo_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=False,
        )
        self.identity_repo.mark_action_undone(
            action_id=action["action_id"],
            undone_by_action_id=undo_action_id,
        )

        self._emit_post_identity_change_events(
            event_name=MERGE_REVERSED,
            primary_identity_id=identity_id,
            affected_cluster_ids=[secondary_cluster_id],
            payload={
                "identity_id": identity_id,
                "secondary_cluster_id": secondary_cluster_id,
                "reversed_action_id": action["action_id"],
                "undo_action_id": undo_action_id,
            },
        )

        return {
            "status": "reversed",
            "identity_id": identity_id,
            "secondary_cluster_id": secondary_cluster_id,
            "undo_action_id": undo_action_id,
        }

    def detach_cluster_from_identity(
        self,
        identity_id: str,
        cluster_id: str,
        performed_by: str | None = None,
    ) -> dict[str, Any]:
        identity = self.identity_repo.get_identity(identity_id)
        if not identity:
            raise ValueError(f"Unknown identity: {identity_id}")

        if identity.canonical_cluster_id == cluster_id:
            return {
                "status": "canonical_cluster_detach_blocked",
                "identity_id": identity_id,
                "cluster_id": cluster_id,
            }

        now = _now_iso()
        affected = self.identity_repo.deactivate_cluster_link(
            identity_id=identity_id,
            cluster_id=cluster_id,
            removed_at=now,
        )
        if affected == 0:
            return {
                "status": "not_attached",
                "identity_id": identity_id,
                "cluster_id": cluster_id,
            }

        action_payload = {
            "identity_id": identity_id,
            "cluster_id": cluster_id,
            "detached_at": now,
        }
        action_id = self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="cluster_detached",
            created_at=now,
            identity_id=identity_id,
            cluster_id=cluster_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=False,
        )

        self._emit_post_identity_change_events(
            event_name=IDENTITY_CLUSTER_DETACHED,
            primary_identity_id=identity_id,
            affected_cluster_ids=[cluster_id],
            payload={
                "identity_id": identity_id,
                "cluster_id": cluster_id,
                "action_id": action_id,
            },
        )

        return {
            "status": "detached",
            "identity_id": identity_id,
            "cluster_id": cluster_id,
            "action_id": action_id,
        }

    # ------------------------------------------------------------------
    # Protection semantics
    # ------------------------------------------------------------------

    def set_identity_protected(
        self,
        identity_id: str,
        is_protected: bool,
        performed_by: str | None = None,
    ) -> None:
        now = _now_iso()
        self.identity_repo.set_identity_protected(
            identity_id=identity_id,
            is_protected=is_protected,
            updated_at=now,
        )

        action_payload = {
            "identity_id": identity_id,
            "is_protected": is_protected,
            "updated_at": now,
        }
        self.identity_repo.log_identity_action(
            action_id=_new_id("act"),
            action_type="identity_protected" if is_protected else "identity_unprotected",
            created_at=now,
            identity_id=identity_id,
            payload_json=json.dumps(action_payload, ensure_ascii=False),
            created_by=performed_by,
            is_undoable=False,
        )

        self.event_bus.emit(
            IDENTITY_PROTECTED if is_protected else IDENTITY_UNPROTECTED,
            {
                "identity_id": identity_id,
                "is_protected": is_protected,
            },
        )
        self.event_bus.emit(
            PEOPLE_SIDEBAR_REFRESH_REQUESTED,
            {
                "reason": "identity_protection_changed",
                "identity_id": identity_id,
            },
        )
        self.event_bus.emit(
            SEARCH_PERSON_FACETS_REFRESH_REQUESTED,
            {
                "reason": "identity_protection_changed",
                "identity_id": identity_id,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_merge_target(
        self,
        cluster_a_id: str,
        cluster_b_id: str,
        identity_a,
        identity_b,
    ) -> tuple[str, str]:
        """
        Returns:
            (target_identity_id, secondary_cluster_id_to_attach)
        """
        now = _now_iso()

        if identity_a and not identity_b:
            return identity_a.identity_id, cluster_b_id

        if identity_b and not identity_a:
            return identity_b.identity_id, cluster_a_id

        if identity_a and identity_b:
            # Conservative rule:
            # protected identity wins,
            # otherwise prefer one with canonical cluster,
            # otherwise keep identity_a as primary.
            target_identity_id = self._select_primary_identity(identity_a, identity_b)
            secondary_cluster_id = (
                cluster_b_id if target_identity_id == identity_a.identity_id else cluster_a_id
            )
            return target_identity_id, secondary_cluster_id

        # Neither cluster has identity yet, create one and make A canonical
        identity_id = _new_id("pid")
        self.identity_repo.create_identity(
            identity_id=identity_id,
            display_name=None,
            canonical_cluster_id=cluster_a_id,
            created_at=now,
            updated_at=now,
            source="merge_accept",
        )
        self.identity_repo.attach_cluster_to_identity(
            link_id=_new_id("lnk"),
            identity_id=identity_id,
            cluster_id=cluster_a_id,
            link_type="canonical",
            created_at=now,
            source="merge_accept",
        )
        return identity_id, cluster_b_id

    @staticmethod
    def _select_primary_identity(identity_a, identity_b) -> str:
        if identity_a.is_protected and not identity_b.is_protected:
            return identity_a.identity_id
        if identity_b.is_protected and not identity_a.is_protected:
            return identity_b.identity_id
        if identity_a.canonical_cluster_id and not identity_b.canonical_cluster_id:
            return identity_a.identity_id
        if identity_b.canonical_cluster_id and not identity_a.canonical_cluster_id:
            return identity_b.identity_id
        return identity_a.identity_id

    def _emit_post_identity_change_events(
        self,
        event_name: str,
        primary_identity_id: str | None,
        affected_cluster_ids: list[str],
        payload: dict[str, Any],
    ) -> None:
        self.event_bus.emit(event_name, payload)

        refresh_payload = {
            "identity_ids": [primary_identity_id] if primary_identity_id else [],
            "cluster_ids": affected_cluster_ids,
            "reason": event_name,
        }
        self.event_bus.emit(PEOPLE_INDEX_REFRESH_REQUESTED, refresh_payload)
        self.event_bus.emit(PEOPLE_SIDEBAR_REFRESH_REQUESTED, refresh_payload)
        self.event_bus.emit(PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED, refresh_payload)
        self.event_bus.emit(SEARCH_PERSON_FACETS_REFRESH_REQUESTED, refresh_payload)
