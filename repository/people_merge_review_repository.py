"""
UX-11A: Persistent merge review decisions with model versioning and invalidation.

Stores accepted / rejected / skipped merge review decisions and unnamed cluster
decisions so pairs are not resurfaced blindly in future suggestion runs.
Supports model-version tracking and decision invalidation when embeddings change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple


@dataclass
class ReviewDecision:
    """A single persisted review decision."""
    left_id: str
    right_id: str
    decision: str           # accepted | rejected | skipped
    model_version: str      # embedding model version at decision time
    created_at: str = ""
    invalidated_at: Optional[str] = None


@dataclass
class ClusterDecision:
    """A persisted unnamed-cluster governance decision."""
    cluster_id: str
    decision: str           # assigned | promoted | ignored | low_confidence | keep_separate
    target_id: str = ""     # target identity for assigned decisions
    new_label: str = ""     # label for promoted decisions
    model_version: str = ""
    created_at: str = ""


class PeopleMergeReviewRepository:
    """
    UX-11A persistent review repository.

    Tables:
      - people_merge_reviews: pairwise merge decisions (accept/reject/skip)
      - people_cluster_decisions: unnamed cluster governance decisions
    """

    def __init__(self, db):
        self.db = db
        self._ensure_schema()

    # ── Schema ────────────────────────────────────────────────────────

    def _ensure_schema(self):
        conn = self.db if hasattr(self.db, "execute") else None
        if conn is None:
            return

        # Merge review decisions (upgraded with model_version + invalidated_at)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_merge_reviews (
                left_id TEXT NOT NULL,
                right_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                model_version TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                invalidated_at TIMESTAMP DEFAULT NULL,
                PRIMARY KEY (left_id, right_id)
            )
        """)

        # Add columns if upgrading from UX-9A schema
        for col, typedef in [
            ("model_version", "TEXT NOT NULL DEFAULT ''"),
            ("invalidated_at", "TIMESTAMP DEFAULT NULL"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE people_merge_reviews ADD COLUMN {col} {typedef}"
                )
            except Exception:
                pass  # column already exists

        # Unnamed cluster governance decisions (new in UX-11A)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_cluster_decisions (
                cluster_id TEXT NOT NULL PRIMARY KEY,
                decision TEXT NOT NULL,
                target_id TEXT NOT NULL DEFAULT '',
                new_label TEXT NOT NULL DEFAULT '',
                model_version TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # ── Helpers ───────────────────────────────────────────────────────

    def _pair_key(self, left_id: str, right_id: str) -> tuple[str, str]:
        return tuple(sorted((str(left_id), str(right_id))))

    # ── Merge decisions ───────────────────────────────────────────────

    def set_decision(self, left_id: str, right_id: str, decision: str,
                     model_version: str = ""):
        left_id, right_id = self._pair_key(left_id, right_id)
        self.db.execute("""
            INSERT INTO people_merge_reviews
                (left_id, right_id, decision, model_version, invalidated_at)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(left_id, right_id)
            DO UPDATE SET decision=excluded.decision,
                         model_version=excluded.model_version,
                         invalidated_at=NULL,
                         created_at=CURRENT_TIMESTAMP
        """, (left_id, right_id, decision, model_version))

    def accept(self, left_id: str, right_id: str, model_version: str = ""):
        self.set_decision(left_id, right_id, "accepted", model_version)

    def reject(self, left_id: str, right_id: str, model_version: str = ""):
        self.set_decision(left_id, right_id, "rejected", model_version)

    def skip(self, left_id: str, right_id: str, model_version: str = ""):
        self.set_decision(left_id, right_id, "skipped", model_version)

    def get_pairs_by_decision(self, decision: str) -> Set[Tuple[str, str]]:
        rows = self.db.execute("""
            SELECT left_id, right_id
            FROM people_merge_reviews
            WHERE decision = ? AND invalidated_at IS NULL
        """, (decision,)).fetchall()
        return {self._pair_key(r[0], r[1]) for r in rows}

    def get_all_active_decisions(self) -> List[ReviewDecision]:
        """Return all non-invalidated merge decisions."""
        rows = self.db.execute("""
            SELECT left_id, right_id, decision, model_version, created_at, invalidated_at
            FROM people_merge_reviews
            WHERE invalidated_at IS NULL
            ORDER BY created_at DESC
        """).fetchall()
        return [
            ReviewDecision(
                left_id=r[0], right_id=r[1], decision=r[2],
                model_version=r[3], created_at=r[4] or "",
                invalidated_at=r[5],
            )
            for r in rows
        ]

    def get_decision_for_pair(self, left_id: str, right_id: str) -> Optional[ReviewDecision]:
        """Return active decision for a specific pair, or None."""
        left_id, right_id = self._pair_key(left_id, right_id)
        row = self.db.execute("""
            SELECT left_id, right_id, decision, model_version, created_at, invalidated_at
            FROM people_merge_reviews
            WHERE left_id = ? AND right_id = ? AND invalidated_at IS NULL
        """, (left_id, right_id)).fetchone()
        if not row:
            return None
        return ReviewDecision(
            left_id=row[0], right_id=row[1], decision=row[2],
            model_version=row[3], created_at=row[4] or "",
            invalidated_at=row[5],
        )

    # ── Invalidation ──────────────────────────────────────────────────

    def invalidate_by_model_version(self, old_version: str) -> int:
        """Invalidate all decisions made with a specific model version.
        Returns number of rows invalidated."""
        cursor = self.db.execute("""
            UPDATE people_merge_reviews
            SET invalidated_at = CURRENT_TIMESTAMP
            WHERE model_version = ? AND invalidated_at IS NULL
        """, (old_version,))
        return cursor.rowcount if hasattr(cursor, "rowcount") else 0

    def invalidate_pair(self, left_id: str, right_id: str):
        """Invalidate a single pair decision (e.g. after undo/split)."""
        left_id, right_id = self._pair_key(left_id, right_id)
        self.db.execute("""
            UPDATE people_merge_reviews
            SET invalidated_at = CURRENT_TIMESTAMP
            WHERE left_id = ? AND right_id = ? AND invalidated_at IS NULL
        """, (left_id, right_id))

    def get_invalidated_decisions(self) -> List[ReviewDecision]:
        """Return all invalidated decisions (for re-review queue)."""
        rows = self.db.execute("""
            SELECT left_id, right_id, decision, model_version, created_at, invalidated_at
            FROM people_merge_reviews
            WHERE invalidated_at IS NOT NULL
            ORDER BY invalidated_at DESC
        """).fetchall()
        return [
            ReviewDecision(
                left_id=r[0], right_id=r[1], decision=r[2],
                model_version=r[3], created_at=r[4] or "",
                invalidated_at=r[5],
            )
            for r in rows
        ]

    # ── Cluster governance decisions ──────────────────────────────────

    def set_cluster_decision(self, cluster_id: str, decision: str,
                             target_id: str = "", new_label: str = "",
                             model_version: str = ""):
        self.db.execute("""
            INSERT INTO people_cluster_decisions
                (cluster_id, decision, target_id, new_label, model_version)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id)
            DO UPDATE SET decision=excluded.decision,
                         target_id=excluded.target_id,
                         new_label=excluded.new_label,
                         model_version=excluded.model_version,
                         created_at=CURRENT_TIMESTAMP
        """, (cluster_id, decision, target_id, new_label, model_version))

    def get_cluster_decision(self, cluster_id: str) -> Optional[ClusterDecision]:
        row = self.db.execute("""
            SELECT cluster_id, decision, target_id, new_label, model_version, created_at
            FROM people_cluster_decisions
            WHERE cluster_id = ?
        """, (str(cluster_id),)).fetchone()
        if not row:
            return None
        return ClusterDecision(
            cluster_id=row[0], decision=row[1], target_id=row[2],
            new_label=row[3], model_version=row[4], created_at=row[5] or "",
        )

    def get_clusters_by_decision(self, decision: str) -> List[ClusterDecision]:
        rows = self.db.execute("""
            SELECT cluster_id, decision, target_id, new_label, model_version, created_at
            FROM people_cluster_decisions
            WHERE decision = ?
            ORDER BY created_at DESC
        """, (decision,)).fetchall()
        return [
            ClusterDecision(
                cluster_id=r[0], decision=r[1], target_id=r[2],
                new_label=r[3], model_version=r[4], created_at=r[5] or "",
            )
            for r in rows
        ]

    def get_decided_cluster_ids(self) -> Set[str]:
        """Return all cluster IDs that have any governance decision."""
        rows = self.db.execute("""
            SELECT cluster_id FROM people_cluster_decisions
        """).fetchall()
        return {r[0] for r in rows}

    # ── Stats ─────────────────────────────────────────────────────────

    def get_review_stats(self) -> dict:
        """Return summary counts for UI display."""
        row = self.db.execute("""
            SELECT
                SUM(CASE WHEN decision='accepted' AND invalidated_at IS NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN decision='rejected' AND invalidated_at IS NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN decision='skipped'  AND invalidated_at IS NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN invalidated_at IS NOT NULL THEN 1 ELSE 0 END)
            FROM people_merge_reviews
        """).fetchone()
        return {
            "accepted": int(row[0] or 0) if row else 0,
            "rejected": int(row[1] or 0) if row else 0,
            "skipped": int(row[2] or 0) if row else 0,
            "invalidated": int(row[3] or 0) if row else 0,
        }
