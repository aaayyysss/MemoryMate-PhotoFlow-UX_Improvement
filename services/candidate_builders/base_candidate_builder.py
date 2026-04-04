# services/candidate_builders/base_candidate_builder.py
# Abstract base for family-specific candidate builders.
#
# Each builder outputs a CandidateSet with:
#   - candidate_paths: the pool of paths to rank
#   - evidence_by_path: per-path retrieval evidence
#   - builder_confidence: how confident the builder is
#   - ready_state: index readiness indicator
#
# The ranking stage must never see the whole corpus unless the
# family explicitly allows that (scenic only).

"""
BaseCandidateBuilder - Abstract base class for candidate pool construction.

Usage:
    from services.candidate_builders.base_candidate_builder import (
        BaseCandidateBuilder, CandidateSet,
    )
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from services.query_intent_planner import QueryIntent
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CandidateSet:
    """Output of a candidate builder — the pool that the ranker scores."""
    family: str
    candidate_paths: List[str] = field(default_factory=list)
    evidence_by_path: Dict[str, dict] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)
    builder_confidence: float = 0.0
    ready_state: str = "ready"  # ready, partial, not_ready, empty
    notes: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.candidate_paths)

    @property
    def is_ready(self) -> bool:
        return self.ready_state in ("ready", "partial")


class BaseCandidateBuilder(ABC):
    """
    Abstract base for family-specific candidate builders.

    Subclasses implement build() to produce a CandidateSet using the
    right index first — OCR/structure for documents, face/person for
    people events, etc.
    """

    def __init__(self, project_id: int):
        self.project_id = project_id

    @abstractmethod
    def build(
        self,
        intent: QueryIntent,
        project_meta: Dict[str, dict],
        limit: int = 500,
    ) -> CandidateSet:
        """
        Build a candidate pool for the given intent.

        Args:
            intent: Normalized query intent from QueryIntentPlanner
            project_meta: {path: {metadata dict}} for all project assets
            limit: Maximum candidates to return

        Returns:
            CandidateSet with candidate paths, evidence, and readiness
        """
        ...

    @staticmethod
    def _empty(
        family: str,
        note: str,
        ready_state: str = "empty",
    ) -> CandidateSet:
        """Return an empty CandidateSet with explanation."""
        return CandidateSet(
            family=family,
            candidate_paths=[],
            evidence_by_path={},
            source_counts={},
            builder_confidence=0.0,
            ready_state=ready_state,
            notes=[note],
        )
