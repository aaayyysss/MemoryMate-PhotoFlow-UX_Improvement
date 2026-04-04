from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class ClusterSummary:
    cluster_id: str
    label: str
    count: int
    avg_embedding: list[float] | None = None
    years: list[int] | None = None
    camera_models: list[str] | None = None
    sample_paths: list[str] | None = None


@dataclass
class MergeCandidate:
    left_id: str
    right_id: str
    score: float
    label: str
    reasons: list[str] = field(default_factory=list)


class PeopleMergeIntelligence:
    """
    Lightweight merge-candidate engine.
    Uses a weighted score composed of:
    - embedding similarity  (55%)
    - size compatibility    (15%)
    - temporal overlap      (15%)
    - camera overlap        (15%)
    """

    # Weights
    W_EMBEDDING = 0.55
    W_SIZE = 0.15
    W_TEMPORAL = 0.15
    W_CAMERA = 0.15

    # Minimum score to surface a candidate
    MIN_SCORE = 0.45

    def rank_candidates(
        self,
        clusters: List[ClusterSummary],
        prior_decisions: Dict[Tuple[str, str], str] | None = None,
        max_candidates: int = 12,
    ) -> List[Dict[str, Any]]:
        prior_decisions = prior_decisions or {}
        out: list[MergeCandidate] = []

        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                left = clusters[i]
                right = clusters[j]

                pair_key = self._pair_key(left.cluster_id, right.cluster_id)
                if pair_key in prior_decisions:
                    continue

                score, reasons = self._score_pair(left, right)
                if score < self.MIN_SCORE:
                    continue

                out.append(
                    MergeCandidate(
                        left_id=left.cluster_id,
                        right_id=right.cluster_id,
                        score=score,
                        label=f"{left.label} \u2194 {right.label}",
                        reasons=reasons,
                    )
                )

        out.sort(key=lambda x: x.score, reverse=True)
        out = out[:max_candidates]

        return [
            {
                "left_id": c.left_id,
                "right_id": c.right_id,
                "score": c.score,
                "label": f"{c.label} ({', '.join(c.reasons)})" if c.reasons else c.label,
                "reasons": c.reasons,
            }
            for c in out
        ]

    def _score_pair(
        self, left: ClusterSummary, right: ClusterSummary
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.0

        emb_sim = self._cosine_similarity(left.avg_embedding, right.avg_embedding)
        if emb_sim is not None:
            score += emb_sim * self.W_EMBEDDING
            if emb_sim >= 0.72:
                reasons.append("high embedding similarity")
            elif emb_sim >= 0.60:
                reasons.append("moderate embedding similarity")

        size_score = self._size_compatibility(left.count, right.count)
        score += size_score * self.W_SIZE
        if size_score >= 0.8:
            reasons.append("similar cluster size")

        time_score = self._temporal_overlap(left.years or [], right.years or [])
        score += time_score * self.W_TEMPORAL
        if time_score > 0:
            reasons.append("temporal overlap")

        camera_score = self._camera_overlap(
            left.camera_models or [], right.camera_models or []
        )
        score += camera_score * self.W_CAMERA
        if camera_score > 0:
            reasons.append("same camera context")

        return score, reasons

    def _pair_key(self, a: str, b: str) -> tuple[str, str]:
        return tuple(sorted((str(a), str(b))))

    def _cosine_similarity(
        self, a: list[float] | None, b: list[float] | None
    ) -> float | None:
        if not a or not b or len(a) != len(b):
            return None

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return None
        return max(-1.0, min(1.0, dot / (na * nb)))

    def _size_compatibility(self, a: int, b: int) -> float:
        if a <= 0 or b <= 0:
            return 0.0
        return min(a, b) / max(a, b)

    def _temporal_overlap(
        self, left_years: list[int], right_years: list[int]
    ) -> float:
        if not left_years or not right_years:
            return 0.0
        ls = set(left_years)
        rs = set(right_years)
        union = len(ls | rs)
        if union == 0:
            return 0.0
        return len(ls & rs) / union

    def _camera_overlap(
        self, left_models: list[str], right_models: list[str]
    ) -> float:
        if not left_models or not right_models:
            return 0.0
        ls = {x for x in left_models if x}
        rs = {x for x in right_models if x}
        if not ls or not rs:
            return 0.0
        union = len(ls | rs)
        if union == 0:
            return 0.0
        return len(ls & rs) / union
