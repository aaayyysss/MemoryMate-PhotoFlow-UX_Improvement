"""
UX-11D: Centralized domain event names.

All event names used by the identity/review system.
Since the app uses Qt signals exclusively, these constants are used
as identifiers for signal emission and subscription routing.
"""

# ── Merge candidate events ────────────────────────────────────────────
MERGE_CANDIDATE_ACCEPTED = "merge_candidate_accepted"
MERGE_CANDIDATE_REJECTED = "merge_candidate_rejected"
MERGE_CANDIDATE_SKIPPED = "merge_candidate_skipped"
MERGE_CANDIDATE_INVALIDATED = "merge_candidate_invalidated"

# ── Unnamed cluster events ────────────────────────────────────────────
UNNAMED_CLUSTER_ASSIGNED = "unnamed_cluster_assigned"
UNNAMED_CLUSTER_KEPT_SEPARATE = "unnamed_cluster_kept_separate"
UNNAMED_CLUSTER_IGNORED = "unnamed_cluster_ignored"
UNNAMED_CLUSTER_LOW_CONFIDENCE = "unnamed_cluster_low_confidence"

# ── Identity events ───────────────────────────────────────────────────
IDENTITY_PROTECTED = "identity_protected"
IDENTITY_UNPROTECTED = "identity_unprotected"
IDENTITY_CLUSTER_DETACHED = "identity_cluster_detached"
MERGE_REVERSED = "merge_reversed"

# ── Refresh requests ──────────────────────────────────────────────────
PEOPLE_INDEX_REFRESH_REQUESTED = "people_index_refresh_requested"
PEOPLE_SIDEBAR_REFRESH_REQUESTED = "people_sidebar_refresh_requested"
PEOPLE_REVIEW_QUEUE_REFRESH_REQUESTED = "people_review_queue_refresh_requested"
SEARCH_PERSON_FACETS_REFRESH_REQUESTED = "search_person_facets_refresh_requested"
