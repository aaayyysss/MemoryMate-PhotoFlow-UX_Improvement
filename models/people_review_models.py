"""
UX-11: Domain models for People review, identity, and merge governance.

Dataclasses used across repository, service, and UI layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Merge candidate models ────────────────────────────────────────────

@dataclass
class MergeRationale:
    code: str
    label: str
    weight: float = 0.0


@dataclass
class MergeCandidateModel:
    candidate_id: str
    cluster_a_id: str
    cluster_b_id: str
    confidence_score: float
    confidence_band: str          # high | medium | low
    rationale: List[MergeRationale]
    status: str                   # unreviewed | accepted | rejected | skipped | invalidated
    created_at: str
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    model_version: Optional[str] = None
    feature_version: Optional[str] = None
    invalidated_reason: Optional[str] = None
    superseded_by_candidate_id: Optional[str] = None


# ── Cluster review decision models ────────────────────────────────────

@dataclass
class ClusterReviewDecisionModel:
    decision_id: str
    cluster_id: str
    decision_type: str            # assign_existing | keep_separate | ignore | low_confidence
    target_identity_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""
    created_by: Optional[str] = None
    is_active: bool = True
    source: str = "user"


# ── Identity models ───────────────────────────────────────────────────

@dataclass
class PersonIdentityModel:
    identity_id: str
    display_name: Optional[str] = None
    canonical_cluster_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    is_protected: bool = False
    is_hidden: bool = False
    source: str = "system"


@dataclass
class IdentityClusterLinkModel:
    link_id: str
    identity_id: str
    cluster_id: str
    link_type: str                # canonical | merged_into_identity | manual_assign | auto_attach
    created_at: str = ""
    removed_at: Optional[str] = None
    is_active: bool = True
    source: str = "system"


@dataclass
class IdentitySnapshot:
    """UI-ready identity view with aggregated data."""
    identity: PersonIdentityModel
    cluster_ids: List[str] = field(default_factory=list)
    hero_face_path: Optional[str] = None
    photo_count: int = 0
    date_span: Optional[str] = None
    badges: List[str] = field(default_factory=list)


# ── Action log model ──────────────────────────────────────────────────

@dataclass
class IdentityActionModel:
    action_id: str
    action_type: str
    identity_id: Optional[str] = None
    cluster_id: Optional[str] = None
    related_identity_id: Optional[str] = None
    related_cluster_id: Optional[str] = None
    candidate_id: Optional[str] = None
    payload_json: Optional[str] = None
    created_at: str = ""
    created_by: Optional[str] = None
    is_undoable: bool = True
    undone_by_action_id: Optional[str] = None
