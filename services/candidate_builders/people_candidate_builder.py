# services/candidate_builders/people_candidate_builder.py
# Person/face-first candidate builder for people_event family.
#
# Retrieves people-event results from face/person indexes first,
# never from generic CLIP alone when face index is ready.
#
# Retrieval priority:
#   1. Named person lookup (via PersonSearchService)
#   2. Person cluster links
#   3. Face-count candidates (photos with faces)
#   4. Co-occurrence candidates
#   5. Event context (semantic within face-having pool)
#
# If face index is not ready:
#   - Return ready_state="not_ready"
#   - Do NOT silently degrade to broad scenic CLIP retrieval

"""
PeopleCandidateBuilder - Face/person-first retrieval for people_event family.

Usage:
    from services.candidate_builders.people_candidate_builder import (
        PeopleCandidateBuilder,
    )

    builder = PeopleCandidateBuilder(project_id=1)
    candidate_set = builder.build(intent, project_meta)
"""

from __future__ import annotations
from typing import Dict, List, Set, Optional, Tuple

from services.candidate_builders.base_candidate_builder import (
    BaseCandidateBuilder,
    CandidateSet,
)
from services.query_intent_planner import QueryIntent
from logging_config import get_logger

logger = get_logger(__name__)

# Minimum face coverage to consider the face index "ready"
_FACE_COVERAGE_FLOOR = 0.10


class PeopleCandidateBuilder(BaseCandidateBuilder):
    """
    Retrieve people-event results from face/person indexes first.

    Never falls back to generic CLIP alone when face index is ready.
    When face index is not ready, returns a clear not_ready state
    instead of silently degraded results.
    """

    def __init__(self, project_id: int):
        super().__init__(project_id)
        self._person_service = None

    def _get_person_service(self):
        """Lazy-load PersonSearchService."""
        if self._person_service is None:
            try:
                from services.person_search_service import PersonSearchService
                self._person_service = PersonSearchService(self.project_id)
            except Exception:
                pass
        return self._person_service

    def build(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int = 500,
    ) -> CandidateSet:
        """Build candidate pool for people_event queries."""
        if not project_meta:
            return self._empty("people_event", "No project metadata available")

        # Step 1: Check face index readiness
        face_ready, ready_msg = self._check_face_index_ready(project_meta)
        if not face_ready:
            return CandidateSet(
                family="people_event",
                candidate_paths=[],
                evidence_by_path={},
                source_counts={},
                builder_confidence=0.0,
                ready_state="not_ready",
                notes=[ready_msg],
                diagnostics={
                    "rejections": {"face_index_not_ready": len(project_meta)},
                    "face_coverage_floor": _FACE_COVERAGE_FLOOR,
                },
            )

        # Step 2: Resolve named people
        person_terms = intent.person_terms or []
        named_cluster_ids = self._resolve_named_people(person_terms)

        # Step 3: Build candidate pool from multiple sources
        named_paths = set()
        cluster_paths = set()
        face_presence_paths = set()
        cooccurrence_paths = set()

        pss = self._get_person_service()

        if named_cluster_ids and pss:
            # Named person retrieval via PersonSearchService
            # Uses face_branch_reps + face_crops (canonical schema)
            try:
                for branch_key in named_cluster_ids:
                    paths = pss.get_person_photo_paths(branch_key)
                    named_paths.update(paths)
            except Exception as e:
                logger.debug(
                    f"[PeopleCandidateBuilder] Named person retrieval failed: {e}"
                )

            # Co-occurrence if multiple people
            if len(named_cluster_ids) > 1:
                try:
                    cooccurrence_paths = pss.get_co_occurrence_paths(
                        named_cluster_ids
                    )
                except Exception:
                    pass

            # Also check precomputed group_asset_matches from person_groups
            # for richer co-occurrence data (existing schema, not parallel)
            try:
                group_paths = pss.get_group_match_paths(named_cluster_ids)
                if group_paths:
                    cooccurrence_paths |= group_paths
                    cluster_paths.update(group_paths)
            except Exception:
                pass

        # Face-presence candidates (photos that have faces)
        face_presence_paths = self._query_face_presence_paths(
            project_meta, min_faces=1
        )

        # Merge sources with priority
        if named_paths:
            # When we have named matches, use those as primary
            all_candidates = named_paths
            if cooccurrence_paths:
                # If co-occurrence available, prefer it
                all_candidates = cooccurrence_paths | named_paths
        else:
            # No named matches — use all face-having photos
            all_candidates = face_presence_paths

        # Step 4: Build evidence with event-aware scoring
        candidates = []
        evidence_by_path = {}

        for path in all_candidates:
            if path not in project_meta:
                continue
            meta = project_meta[path]

            evidence = self._build_evidence(
                path, meta,
                named_paths, cluster_paths,
                face_presence_paths, cooccurrence_paths,
                person_terms,
            )
            candidates.append(path)
            evidence_by_path[path] = evidence

        # Sort by event_score descending so top candidates have strongest
        # event evidence, not just arbitrary insertion order
        candidates.sort(
            key=lambda p: evidence_by_path[p].get("event_score", 0),
            reverse=True,
        )
        candidates = candidates[:limit]

        source_counts = {
            "named_person": len(named_paths),
            "cluster": len(cluster_paths),
            "face_presence": len(face_presence_paths),
            "cooccurrence": len(cooccurrence_paths),
            "final_candidates": len(candidates),
        }

        confidence = self._score_builder_confidence(
            face_ready, len(named_paths), len(cluster_paths)
        )

        logger.info(
            f"[PeopleCandidateBuilder] {len(candidates)} candidates "
            f"(named={len(named_paths)}, face_presence={len(face_presence_paths)}, "
            f"cooccurrence={len(cooccurrence_paths)})"
        )

        return CandidateSet(
            family="people_event",
            candidate_paths=candidates,
            evidence_by_path=evidence_by_path,
            source_counts=source_counts,
            builder_confidence=confidence,
            ready_state="ready" if candidates else "empty",
            notes=[f"People builder: {len(candidates)} candidates"],
            diagnostics={
                "named_hits": len(named_paths),
                "cluster_hits": len(cluster_paths),
                "cooccurrence_hits": len(cooccurrence_paths),
                "face_presence_hits": len(face_presence_paths),
                "top_event_scores": [
                    evidence_by_path[p].get("event_score", 0.0)
                    for p in candidates[:10]
                ],
            },
        )

    # ── Index readiness ──

    @staticmethod
    def _check_face_index_ready(
        project_meta: Dict[str, dict],
    ) -> Tuple[bool, str]:
        """Check if face index has sufficient coverage."""
        total = len(project_meta)
        if total == 0:
            return False, "Empty project — no photos to search"

        face_photo_count = sum(
            1 for m in project_meta.values()
            if (m.get("face_count", 0) or 0) > 0
        )
        coverage = face_photo_count / total

        if coverage >= _FACE_COVERAGE_FLOOR:
            return True, f"Face index ready ({face_photo_count}/{total})"

        return (
            False,
            f"Face index not ready: {face_photo_count}/{total} photos "
            f"({coverage:.0%}) < {_FACE_COVERAGE_FLOOR:.0%} floor. "
            f"Run face detection for accurate people results.",
        )

    # ── Retrieval sources ──

    def _resolve_named_people(
        self, person_terms: List[str],
    ) -> List[str]:
        """Resolve person name terms to branch_keys."""
        pss = self._get_person_service()
        if not pss or not person_terms:
            return []

        resolved = []
        try:
            for term in person_terms:
                keys = pss.resolve_person_name(term)
                if keys:
                    resolved.extend(keys)
        except Exception as e:
            logger.debug(f"[PeopleCandidateBuilder] Name resolution failed: {e}")

        return resolved

    @staticmethod
    def _query_face_presence_paths(
        project_meta: Dict[str, dict],
        min_faces: int = 1,
    ) -> Set[str]:
        """Get all paths that have at least min_faces detected."""
        return {
            path
            for path, meta in project_meta.items()
            if (meta.get("face_count") or 0) >= min_faces
        }

    @staticmethod
    def _build_evidence(
        path: str,
        meta: dict,
        named_paths: Set[str],
        cluster_paths: Set[str],
        face_presence_paths: Set[str],
        cooccurrence_paths: Set[str],
        person_terms: List[str],
    ) -> dict:
        """Build per-path evidence for people candidates.

        Includes event-aware signals beyond simple face presence:
        - face_count: more faces → more likely a group/event photo
        - is_portrait: portrait orientation suggests posed/people photo
        - is_favorite: user curation signal
        - named_person_count: how many queried people appear
        - event_score: composite event-relevance score [0..1]
        """
        face_count = meta.get("face_count") or 0
        is_named = path in named_paths
        is_cooccurrence = path in cooccurrence_paths

        # Portrait orientation detection
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        is_portrait = (h > w * 1.1) if (w > 0 and h > 0) else False

        # Favorite flag
        is_favorite = bool(meta.get("flag") or meta.get("is_favorite"))

        # Compute event score — composite signal beyond face presence
        event_score = 0.0
        # Named person match is strongest signal
        if is_named:
            event_score += 0.40
        if is_cooccurrence:
            event_score += 0.20
        # Multiple faces suggest group/event context
        if face_count >= 3:
            event_score += 0.15
        elif face_count >= 2:
            event_score += 0.10
        # Portrait orientation suggests posed people photo
        if is_portrait:
            event_score += 0.05
        # User curation is a quality signal
        if is_favorite:
            event_score += 0.10
        # Face presence is baseline
        if face_count > 0:
            event_score += 0.10

        return {
            "builder": "people",
            "face_count": face_count,
            "is_named_match": is_named,
            "is_cluster_match": path in cluster_paths,
            "is_face_presence": path in face_presence_paths,
            "is_cooccurrence": is_cooccurrence,
            "matched_people": person_terms if is_named else [],
            "is_portrait": is_portrait,
            "is_favorite": is_favorite,
            "event_score": min(1.0, event_score),
        }

    @staticmethod
    def _score_builder_confidence(
        face_ready: bool,
        named_hits: int,
        cluster_hits: int,
    ) -> float:
        """Score builder confidence."""
        if not face_ready:
            return 0.0
        score = 0.3  # Base for ready index
        if named_hits > 0:
            score += 0.4
        if cluster_hits > 0:
            score += 0.2
        return min(1.0, score)
