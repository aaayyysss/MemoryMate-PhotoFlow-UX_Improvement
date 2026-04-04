"""
UX-11D: Review State Badge Factory — centralized badge rendering rules.

Produces badge descriptors for merge candidates, identity snapshots,
and cluster review decisions. UI widgets consume these to render
consistent status indicators.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ReviewStateBadgeFactory:
    """Central factory for badge descriptors used across People UI."""

    # ── Merge candidate badges ────────────────────────────────────────

    @staticmethod
    def badges_for_merge_candidate(candidate: Dict[str, Any]) -> List[Dict[str, str]]:
        """Return badges for a merge candidate queue item."""
        badges = []
        status = candidate.get("status", "unreviewed")
        band = candidate.get("confidence_band", "")

        if status == "accepted":
            badges.append({"text": "Merged", "kind": "success"})
        elif status == "rejected":
            badges.append({"text": "Not Same", "kind": "rejected"})
        elif status == "skipped":
            badges.append({"text": "Skipped", "kind": "neutral"})
        elif status == "invalidated":
            badges.append({"text": "Needs Re-review", "kind": "warning"})

        if band == "high":
            badges.append({"text": "High Confidence", "kind": "confidence_high"})
        elif band == "medium":
            badges.append({"text": "Review Carefully", "kind": "confidence_medium"})
        elif band == "low":
            badges.append({"text": "Low Confidence", "kind": "confidence_low"})

        return badges

    # ── Identity badges ───────────────────────────────────────────────

    @staticmethod
    def badges_for_identity_snapshot(snapshot: Any) -> List[Dict[str, str]]:
        """Return badges for an identity snapshot."""
        badges = []
        if not snapshot:
            return badges

        identity = getattr(snapshot, "identity", None)
        if not identity:
            return badges

        if getattr(identity, "is_protected", False):
            badges.append({"text": "Protected", "kind": "protected"})
        if getattr(identity, "is_hidden", False):
            badges.append({"text": "Hidden", "kind": "hidden"})
        if getattr(identity, "display_name", None):
            badges.append({"text": "Named", "kind": "named"})
        else:
            badges.append({"text": "Unnamed", "kind": "unnamed"})

        cluster_count = len(getattr(snapshot, "cluster_ids", []))
        if cluster_count > 1:
            badges.append({"text": f"Merged ({cluster_count})", "kind": "merged"})

        return badges

    # ── Cluster review badges ─────────────────────────────────────────

    @staticmethod
    def badges_for_cluster_review(
        decision: Optional[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """Return badges for a cluster review decision."""
        badges = []
        if not decision:
            return badges

        dtype = decision.get("decision_type", "")
        if dtype == "assign_existing":
            badges.append({"text": "Assigned", "kind": "success"})
        elif dtype == "keep_separate":
            badges.append({"text": "Separate", "kind": "neutral"})
        elif dtype == "ignore":
            badges.append({"text": "Ignored", "kind": "muted"})
        elif dtype == "low_confidence":
            badges.append({"text": "Low Confidence", "kind": "warning"})

        return badges

    # ── Badge kind → style mapping ────────────────────────────────────

    @staticmethod
    def style_for_badge_kind(kind: str) -> Dict[str, str]:
        """Return color/style properties for a badge kind."""
        styles = {
            "success": {"bg": "#e6f4ea", "fg": "#188038", "border": "#ceead6"},
            "rejected": {"bg": "#fce8e6", "fg": "#c5221f", "border": "#f5c6c2"},
            "neutral": {"bg": "#f1f3f4", "fg": "#5f6368", "border": "#dadce0"},
            "warning": {"bg": "#fef7e0", "fg": "#f9ab00", "border": "#fde293"},
            "protected": {"bg": "#e8f0fe", "fg": "#1a73e8", "border": "#d2e3fc"},
            "hidden": {"bg": "#f1f3f4", "fg": "#80868b", "border": "#dadce0"},
            "named": {"bg": "#e8f0fe", "fg": "#174ea6", "border": "#d2e3fc"},
            "unnamed": {"bg": "#f1f3f4", "fg": "#5f6368", "border": "#dadce0"},
            "merged": {"bg": "#e6f4ea", "fg": "#188038", "border": "#ceead6"},
            "muted": {"bg": "#f1f3f4", "fg": "#80868b", "border": "#dadce0"},
            "confidence_high": {"bg": "#e6f4ea", "fg": "#188038", "border": "#ceead6"},
            "confidence_medium": {"bg": "#e8f0fe", "fg": "#1a73e8", "border": "#d2e3fc"},
            "confidence_low": {"bg": "#fef7e0", "fg": "#f9ab00", "border": "#fde293"},
        }
        return styles.get(kind, styles["neutral"])
