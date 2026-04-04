"""
UX-9A: PeopleMergeEngine — merge-intelligence backend for People review.

Produces ranked merge suggestions from cluster data using multiple weak signals:
- embedding similarity (70%)
- cluster size compatibility (18%)
- unnamed bonus (+6%)
- giant cluster penalty (−10–16%)

Does NOT merge automatically — only produces ranked suggestions.
Accepted/rejected pairs are excluded via caller-provided sets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import math


@dataclass
class MergeCandidate:
    left_id: str
    right_id: str
    score: float
    label: str
    rationale: Dict[str, Any]


class PeopleMergeEngine:
    """
    UX-9A merge-intelligence engine.

    Input:
        clusters = [
            {
                "id": "face_004",
                "label": "Face_004",
                "count": 23,
                "centroid": [...],          # optional embedding centroid
                "first_seen": "...",        # optional
                "last_seen": "...",         # optional
                "unnamed": True/False,      # optional
            },
            ...
        ]

    Output:
        ranked merge suggestions
    """

    def __init__(self):
        self.min_score = 0.52
        self.max_candidates = 20

    def build_merge_suggestions(
        self,
        clusters: List[Dict[str, Any]],
        accepted_pairs: Optional[set[tuple[str, str]]] = None,
        rejected_pairs: Optional[set[tuple[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        accepted_pairs = accepted_pairs or set()
        rejected_pairs = rejected_pairs or set()

        normalized = self._normalize_clusters(clusters)
        suggestions: List[MergeCandidate] = []

        for i in range(len(normalized)):
            for j in range(i + 1, len(normalized)):
                left = normalized[i]
                right = normalized[j]

                pair_key = self._pair_key(left["id"], right["id"])
                if pair_key in accepted_pairs or pair_key in rejected_pairs:
                    continue

                score, rationale = self._score_pair(left, right)
                if score < self.min_score:
                    continue

                label = f'{left["label"]} \u2194 {right["label"]}'
                suggestions.append(
                    MergeCandidate(
                        left_id=left["id"],
                        right_id=right["id"],
                        score=round(score, 4),
                        label=label,
                        rationale=rationale,
                    )
                )

        suggestions.sort(key=lambda x: x.score, reverse=True)
        return [
            {
                "left_id": s.left_id,
                "right_id": s.right_id,
                "score": s.score,
                "label": s.label,
                "rationale": s.rationale,
            }
            for s in suggestions[: self.max_candidates]
        ]

    def _normalize_clusters(self, clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for c in clusters or []:
            cid = c.get("id") or c.get("person_id") or c.get("label")
            if not cid:
                continue

            label = c.get("label") or c.get("name") or str(cid)
            count = int(c.get("count", 0) or 0)
            centroid = c.get("centroid")
            unnamed = bool(
                c.get("unnamed", False)
                or str(label).lower().startswith("face_")
                or "unnamed" in str(label).lower()
            )

            out.append(
                {
                    "id": str(cid),
                    "label": str(label),
                    "count": count,
                    "centroid": centroid,
                    "unnamed": unnamed,
                    "first_seen": c.get("first_seen"),
                    "last_seen": c.get("last_seen"),
                }
            )
        return out

    def _score_pair(self, left: Dict[str, Any], right: Dict[str, Any]) -> tuple[float, Dict[str, Any]]:
        similarity = self._cosine_similarity(left.get("centroid"), right.get("centroid"))
        size_score = self._size_compatibility(left.get("count", 0), right.get("count", 0))
        unnamed_bonus = 0.06 if (left.get("unnamed") or right.get("unnamed")) else 0.0
        giant_cluster_penalty = self._giant_cluster_penalty(left.get("count", 0), right.get("count", 0))

        # weighted score
        score = (
            0.70 * similarity
            + 0.18 * size_score
            + unnamed_bonus
            - giant_cluster_penalty
        )

        rationale = {
            "similarity": round(similarity, 4),
            "size_score": round(size_score, 4),
            "unnamed_bonus": round(unnamed_bonus, 4),
            "giant_cluster_penalty": round(giant_cluster_penalty, 4),
            "left_count": int(left.get("count", 0)),
            "right_count": int(right.get("count", 0)),
        }
        return score, rationale

    def _size_compatibility(self, left_count: int, right_count: int) -> float:
        if left_count <= 0 or right_count <= 0:
            return 0.0
        small = min(left_count, right_count)
        large = max(left_count, right_count)
        ratio = small / large
        return max(0.0, min(1.0, ratio))

    def _giant_cluster_penalty(self, left_count: int, right_count: int) -> float:
        # discourage swallowing small identities into large ambiguous clusters
        if max(left_count, right_count) >= 20 and min(left_count, right_count) <= 2:
            return 0.16
        if max(left_count, right_count) >= 15 and min(left_count, right_count) <= 3:
            return 0.10
        return 0.0

    def _cosine_similarity(self, a, b) -> float:
        if a is None or b is None:
            return 0.0
        try:
            if len(a) != len(b) or len(a) == 0:
                return 0.0
            dot = 0.0
            na = 0.0
            nb = 0.0
            for x, y in zip(a, b):
                fx = float(x)
                fy = float(y)
                dot += fx * fy
                na += fx * fx
                nb += fy * fy
            if na <= 0.0 or nb <= 0.0:
                return 0.0
            return dot / (math.sqrt(na) * math.sqrt(nb))
        except Exception:
            return 0.0

    def _pair_key(self, left_id: str, right_id: str) -> tuple[str, str]:
        return tuple(sorted((str(left_id), str(right_id))))
