# repository/people_review_repository.py

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MergeCandidateRecord:
    candidate_id: str
    cluster_a_id: str
    cluster_b_id: str
    confidence_score: float
    confidence_band: str
    rationale: list[dict[str, Any]]
    status: str
    created_at: str
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    model_version: Optional[str] = None
    feature_version: Optional[str] = None
    invalidated_reason: Optional[str] = None
    superseded_by_candidate_id: Optional[str] = None


@dataclass
class ClusterReviewDecisionRecord:
    decision_id: str
    cluster_id: str
    decision_type: str
    target_identity_id: Optional[str]
    notes: Optional[str]
    created_at: str
    created_by: Optional[str]
    is_active: bool
    source: str


class PeopleReviewRepository:
    """
    Repository for persistent People review state.

    Notes:
    - rationale is stored as JSON text but exposed as Python list[dict]
    - status values expected:
      unreviewed, accepted, rejected, skipped, invalidated
    - decision_type values expected:
      assign_existing, keep_separate, ignore, low_confidence
    """

    def __init__(self, conn):
        self.conn = conn

    # ------------------------------------------------------------------
    # Merge candidates
    # ------------------------------------------------------------------

    def upsert_merge_candidate(
        self,
        candidate_id: str,
        cluster_a_id: str,
        cluster_b_id: str,
        confidence_score: float,
        confidence_band: str,
        rationale: list[dict[str, Any]],
        created_at: str,
        status: str = "unreviewed",
        reviewed_at: str | None = None,
        reviewed_by: str | None = None,
        model_version: str | None = None,
        feature_version: str | None = None,
        invalidated_reason: str | None = None,
        superseded_by_candidate_id: str | None = None,
    ) -> None:
        rationale_json = json.dumps(rationale, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO merge_candidate (
                candidate_id,
                cluster_a_id,
                cluster_b_id,
                confidence_score,
                confidence_band,
                rationale_json,
                status,
                created_at,
                reviewed_at,
                reviewed_by,
                model_version,
                feature_version,
                invalidated_reason,
                superseded_by_candidate_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                cluster_a_id = excluded.cluster_a_id,
                cluster_b_id = excluded.cluster_b_id,
                confidence_score = excluded.confidence_score,
                confidence_band = excluded.confidence_band,
                rationale_json = excluded.rationale_json,
                status = excluded.status,
                created_at = excluded.created_at,
                reviewed_at = excluded.reviewed_at,
                reviewed_by = excluded.reviewed_by,
                model_version = excluded.model_version,
                feature_version = excluded.feature_version,
                invalidated_reason = excluded.invalidated_reason,
                superseded_by_candidate_id = excluded.superseded_by_candidate_id
            """,
            (
                candidate_id,
                cluster_a_id,
                cluster_b_id,
                confidence_score,
                confidence_band,
                rationale_json,
                status,
                created_at,
                reviewed_at,
                reviewed_by,
                model_version,
                feature_version,
                invalidated_reason,
                superseded_by_candidate_id,
            ),
        )
        self.conn.commit()

    def get_merge_candidate(self, candidate_id: str) -> MergeCandidateRecord | None:
        cur = self.conn.execute(
            """
            SELECT
                candidate_id,
                cluster_a_id,
                cluster_b_id,
                confidence_score,
                confidence_band,
                rationale_json,
                status,
                created_at,
                reviewed_at,
                reviewed_by,
                model_version,
                feature_version,
                invalidated_reason,
                superseded_by_candidate_id
            FROM merge_candidate
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        )
        row = cur.fetchone()
        return self._row_to_merge_candidate(row) if row else None

    def list_merge_candidates(
        self,
        status: str | list[str] | None = None,
        include_invalidated: bool = False,
        limit: int | None = None,
    ) -> list[MergeCandidateRecord]:
        sql = """
            SELECT
                candidate_id,
                cluster_a_id,
                cluster_b_id,
                confidence_score,
                confidence_band,
                rationale_json,
                status,
                created_at,
                reviewed_at,
                reviewed_by,
                model_version,
                feature_version,
                invalidated_reason,
                superseded_by_candidate_id
            FROM merge_candidate
            WHERE 1=1
        """
        params: list[Any] = []

        if not include_invalidated:
            sql += " AND status != 'invalidated'"

        if status is not None:
            if isinstance(status, str):
                sql += " AND status = ?"
                params.append(status)
            else:
                placeholders = ",".join("?" for _ in status)
                sql += f" AND status IN ({placeholders})"
                params.extend(status)

        sql += " ORDER BY created_at DESC, confidence_score DESC"

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        cur = self.conn.execute(sql, tuple(params))
        rows = cur.fetchall()
        return [self._row_to_merge_candidate(row) for row in rows]

    def find_existing_pair(
        self,
        cluster_a_id: str,
        cluster_b_id: str,
        include_invalidated: bool = False,
    ) -> MergeCandidateRecord | None:
        sql = """
            SELECT
                candidate_id,
                cluster_a_id,
                cluster_b_id,
                confidence_score,
                confidence_band,
                rationale_json,
                status,
                created_at,
                reviewed_at,
                reviewed_by,
                model_version,
                feature_version,
                invalidated_reason,
                superseded_by_candidate_id
            FROM merge_candidate
            WHERE (
                (cluster_a_id = ? AND cluster_b_id = ?)
                OR
                (cluster_a_id = ? AND cluster_b_id = ?)
            )
        """
        params: list[Any] = [cluster_a_id, cluster_b_id, cluster_b_id, cluster_a_id]

        if not include_invalidated:
            sql += " AND status != 'invalidated'"

        sql += " ORDER BY created_at DESC LIMIT 1"

        cur = self.conn.execute(sql, tuple(params))
        row = cur.fetchone()
        return self._row_to_merge_candidate(row) if row else None

    def update_merge_candidate_status(
        self,
        candidate_id: str,
        status: str,
        reviewed_at: str | None = None,
        reviewed_by: str | None = None,
        invalidated_reason: str | None = None,
        superseded_by_candidate_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE merge_candidate
            SET
                status = ?,
                reviewed_at = ?,
                reviewed_by = ?,
                invalidated_reason = ?,
                superseded_by_candidate_id = ?
            WHERE candidate_id = ?
            """,
            (
                status,
                reviewed_at,
                reviewed_by,
                invalidated_reason,
                superseded_by_candidate_id,
                candidate_id,
            ),
        )
        self.conn.commit()

    def invalidate_candidates_for_cluster_ids(
        self,
        cluster_ids: list[str],
        reason: str,
    ) -> int:
        if not cluster_ids:
            return 0

        placeholders = ",".join("?" for _ in cluster_ids)
        params = [reason, *cluster_ids, *cluster_ids]

        cur = self.conn.execute(
            f"""
            UPDATE merge_candidate
            SET
                status = 'invalidated',
                invalidated_reason = ?
            WHERE
                status IN ('unreviewed', 'skipped')
                AND (
                    cluster_a_id IN ({placeholders})
                    OR cluster_b_id IN ({placeholders})
                )
            """,
            tuple(params),
        )
        self.conn.commit()
        return cur.rowcount

    def invalidate_candidates_for_model_version_change(
        self,
        old_model_version: str,
        new_model_version: str,
        reason: str,
    ) -> int:
        cur = self.conn.execute(
            """
            UPDATE merge_candidate
            SET
                status = 'invalidated',
                invalidated_reason = ?
            WHERE
                status IN ('unreviewed', 'skipped')
                AND model_version = ?
            """,
            (f"{reason}: {old_model_version} -> {new_model_version}", old_model_version),
        )
        self.conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Cluster review decisions
    # ------------------------------------------------------------------

    def save_cluster_review_decision(
        self,
        decision_id: str,
        cluster_id: str,
        decision_type: str,
        target_identity_id: str | None,
        notes: str | None,
        created_at: str,
        created_by: str | None,
        is_active: bool = True,
        source: str = "user",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO cluster_review_decision (
                decision_id,
                cluster_id,
                decision_type,
                target_identity_id,
                notes,
                created_at,
                created_by,
                is_active,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                cluster_id,
                decision_type,
                target_identity_id,
                notes,
                created_at,
                created_by,
                1 if is_active else 0,
                source,
            ),
        )
        self.conn.commit()

    def get_active_cluster_review_decision(
        self,
        cluster_id: str,
    ) -> ClusterReviewDecisionRecord | None:
        cur = self.conn.execute(
            """
            SELECT
                decision_id,
                cluster_id,
                decision_type,
                target_identity_id,
                notes,
                created_at,
                created_by,
                is_active,
                source
            FROM cluster_review_decision
            WHERE cluster_id = ? AND is_active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (cluster_id,),
        )
        row = cur.fetchone()
        return self._row_to_cluster_review_decision(row) if row else None

    def deactivate_cluster_review_decisions(self, cluster_id: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE cluster_review_decision
            SET is_active = 0
            WHERE cluster_id = ? AND is_active = 1
            """,
            (cluster_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def list_unnamed_review_candidates(self, limit: int | None = None) -> list[dict[str, Any]]:
        """
        Raw queue source for unnamed-cluster workflow.

        A cluster is considered "unnamed/unresolved" if it has no active identity link
        and either has no governance decision or only a low_confidence decision.

        Uses face_branch_reps as the existing cluster source table.
        """
        sql = """
            SELECT
                fbr.branch_key AS cluster_id,
                COALESCE(fbr.count, 0) AS photo_count,
                fbr.label
            FROM face_branch_reps fbr
            LEFT JOIN identity_cluster_link icl
                ON icl.cluster_id = fbr.branch_key
               AND icl.is_active = 1
            LEFT JOIN cluster_review_decision crd
                ON crd.cluster_id = fbr.branch_key
               AND crd.is_active = 1
            WHERE
                icl.cluster_id IS NULL
                AND (
                    crd.decision_type IS NULL
                    OR crd.decision_type IN ('low_confidence')
                )
                AND (
                    fbr.label LIKE 'face_%'
                    OR fbr.label LIKE 'Face_%'
                    OR fbr.label LIKE '%unnamed%'
                )
                AND fbr.label != '__ignored__'
            ORDER BY
                COALESCE(fbr.count, 0) DESC
        """
        params: list[Any] = []

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        cur = self.conn.execute(sql, tuple(params))
        rows = cur.fetchall()
        return [
            {
                "cluster_id": row[0],
                "photo_count": row[1],
                "label": row[2],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_merge_candidate(row) -> MergeCandidateRecord:
        return MergeCandidateRecord(
            candidate_id=row[0],
            cluster_a_id=row[1],
            cluster_b_id=row[2],
            confidence_score=float(row[3]),
            confidence_band=row[4],
            rationale=json.loads(row[5]) if row[5] else [],
            status=row[6],
            created_at=row[7],
            reviewed_at=row[8],
            reviewed_by=row[9],
            model_version=row[10],
            feature_version=row[11],
            invalidated_reason=row[12],
            superseded_by_candidate_id=row[13],
        )

    @staticmethod
    def _row_to_cluster_review_decision(row) -> ClusterReviewDecisionRecord:
        return ClusterReviewDecisionRecord(
            decision_id=row[0],
            cluster_id=row[1],
            decision_type=row[2],
            target_identity_id=row[3],
            notes=row[4],
            created_at=row[5],
            created_by=row[6],
            is_active=bool(row[7]),
            source=row[8],
        )
