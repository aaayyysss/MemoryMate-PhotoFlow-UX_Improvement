# services/search_confidence_policy.py
# Decides whether search results are trustworthy enough to show normally.
#
# This honesty layer is essential: Apple and Google can hide indexing
# complexity, but this app should be explicit when an index-backed
# family is not ready rather than showing fake confidence.
#
# Evaluates candidate sets and ranked results to produce a SearchDecision:
#   - show_results: whether to display results normally
#   - confidence_label: high, medium, low, not_ready, empty
#   - warning_message: user-facing explanation when confidence is low
#   - recommended_actions: what the user can do to improve results

"""
SearchConfidencePolicy - Result trust evaluation.

Usage:
    from services.search_confidence_policy import (
        SearchConfidencePolicy, SearchDecision,
    )

    policy = SearchConfidencePolicy()
    decision = policy.evaluate(intent, candidate_set, ranked_results, family)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional

from services.query_intent_planner import QueryIntent
from services.candidate_builders.base_candidate_builder import CandidateSet
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SearchDecision:
    """Output of SearchConfidencePolicy.evaluate()."""
    show_results: bool = True
    confidence_label: str = "high"  # high, medium, low, not_ready, empty
    warning_message: Optional[str] = None
    explanation: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)


class SearchConfidencePolicy:
    """
    Decide whether results are trustworthy enough to show normally.

    Family-specific evaluation:
    - type: check OCR/structural evidence density (documents, screenshots only)
    - people_event: check face index readiness and face presence
    - scenic: mostly trust CLIP, check for anti-type contamination
    - animal_object: check that results exclude faces
    - utility: metadata-driven (favorites, videos, gps_photos), always trust
    """

    def evaluate(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
        family: str,
    ) -> SearchDecision:
        """
        Evaluate result trustworthiness.

        Args:
            intent: The query intent
            candidate_set: The candidate pool from the builder
            ranked_results: Final ranked results (ScoredResult list)
            family: The resolved family

        Returns:
            SearchDecision with display guidance
        """
        # Not-ready from builder
        if candidate_set.ready_state == "not_ready":
            return SearchDecision(
                show_results=False,
                confidence_label="not_ready",
                warning_message=candidate_set.notes[0] if candidate_set.notes else (
                    "Search index not ready for this query type."
                ),
                explanation=["Builder reported not_ready state"],
                recommended_actions=self._actions_for_not_ready(family),
            )

        # Empty
        if not ranked_results:
            return self._evaluate_empty(intent, candidate_set, family)

        # Family-specific evaluation
        dispatch = {
            "type": self._evaluate_type_family,
            "people_event": self._evaluate_people_family,
            "scenic": self._evaluate_scenic_family,
            "animal_object": self._evaluate_pet_family,
            "utility": self._evaluate_utility_family,
        }

        evaluator = dispatch.get(family, self._evaluate_scenic_family)
        return evaluator(intent, candidate_set, ranked_results)

    # ── Family evaluators ──

    def _evaluate_type_family(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """
        Evaluate type-family results.

        IMPORTANT:
        - documents and screenshots both map to family="type"
        - confidence evaluation must still distinguish them
        """
        preset_id = (intent.preset_id or "").lower()

        if preset_id == "screenshots":
            return self._evaluate_screenshot_type(
                intent, candidate_set, ranked_results
            )

        return self._evaluate_document_type(
            intent, candidate_set, ranked_results
        )

    def _evaluate_document_type(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """Document-specific trust evaluation."""
        hard_evidence = self._count_hard_evidence(
            ranked_results, candidate_set, "documents"
        )
        total = len(ranked_results)
        failures = self._detect_trust_failure_patterns(
            ranked_results, candidate_set, "documents"
        )

        if total == 0:
            return self._empty_decision("documents")

        evidence_ratio = hard_evidence / total
        diag = candidate_set.diagnostics or {}
        low_conf_count = (
            diag.get("low_confidence_candidates", 0)
            or diag.get("structural_only_admits", 0)
        )
        rejection_hist = diag.get("rejections", {})

        if evidence_ratio >= 0.6 and low_conf_count == 0:
            return SearchDecision(
                show_results=True,
                confidence_label="high",
                explanation=[
                    f"{hard_evidence}/{total} results have strong document evidence"
                ],
            )

        if evidence_ratio >= 0.6 and low_conf_count > 0:
            return SearchDecision(
                show_results=True,
                confidence_label="medium",
                warning_message=(
                    f"{hard_evidence}/{total} results have strong document evidence, "
                    f"but {low_conf_count} candidates are structural-only "
                    f"without OCR confirmation."
                ),
                explanation=[
                    f"{low_conf_count} candidates admitted by structural evidence only"
                ],
                recommended_actions=[
                    "Run OCR processing for full confidence",
                ],
            )

        if evidence_ratio >= 0.3:
            return SearchDecision(
                show_results=True,
                confidence_label="medium",
                warning_message=(
                    f"Some results may not be documents. "
                    f"{hard_evidence}/{total} have strong document evidence."
                    + (
                        f" {low_conf_count} are structural-only."
                        if low_conf_count > 0 else ""
                    )
                ),
                explanation=failures,
            )

        diag_detail = ""
        if rejection_hist:
            top_reasons = sorted(
                rejection_hist.items(), key=lambda x: x[1], reverse=True
            )[:3]
            diag_detail = (
                " Top rejection reasons: "
                + ", ".join(f"{r}({c})" for r, c in top_reasons)
                + "."
            )

        if low_conf_count > 0:
            diag_detail += (
                f" {low_conf_count} candidates were admitted by structural evidence only."
            )

        return SearchDecision(
            show_results=True,
            confidence_label="low",
            warning_message=(
                f"Low document confidence: only {hard_evidence}/{total} "
                f"results have OCR or structural document evidence."
                f"{diag_detail}"
            ),
            explanation=failures,
            recommended_actions=[
                "Run OCR processing on your library",
                "Try a more specific document query",
            ],
        )

    def _evaluate_screenshot_type(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """Screenshot-specific trust evaluation with stricter supplement control."""
        hard_evidence = self._count_hard_evidence(
            ranked_results, candidate_set, "screenshots"
        )
        soft_evidence = self._count_soft_evidence(
            ranked_results, candidate_set, "screenshots"
        )
        total = len(ranked_results)
        failures = self._detect_trust_failure_patterns(
            ranked_results, candidate_set, "screenshots"
        )

        if total == 0:
            return self._empty_decision("screenshots")

        effective_evidence = hard_evidence + (0.5 * soft_evidence)
        evidence_ratio = effective_evidence / total

        diag = candidate_set.diagnostics or {}
        rejection_hist = diag.get("rejections", {})
        supplement_admitted = diag.get("supplement_admitted", 0)
        supplement_rejected = diag.get("supplement_rejected", 0)
        weak_semantic_only = diag.get("weak_semantic_only", 0)

        if evidence_ratio >= 0.7 and weak_semantic_only == 0:
            return SearchDecision(
                show_results=True,
                confidence_label="high",
                explanation=[
                    f"{hard_evidence}/{total} results have strong screenshot evidence"
                ],
            )

        if evidence_ratio >= 0.4:
            return SearchDecision(
                show_results=True,
                confidence_label="medium",
                warning_message=(
                    f"Some results may not be screenshots. "
                    f"{hard_evidence}/{total} have strong screenshot evidence."
                ),
                explanation=failures + (
                    [f"Supplement admitted={supplement_admitted}, rejected={supplement_rejected}"]
                    if (supplement_admitted or supplement_rejected) else []
                ),
            )

        return SearchDecision(
            show_results=True,
            confidence_label="low",
            warning_message=(
                f"Low screenshot confidence: only {hard_evidence}/{total} "
                f"results have strong screenshot evidence, with {soft_evidence} "
                f"weak screenshot matches. "
                f"Builder rejections={rejection_hist}."
            ),
            explanation=failures,
            recommended_actions=[
                "Rebuild screenshot detection metadata",
                "Try a more specific UI/text query",
            ],
        )

    def _evaluate_people_family(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """Evaluate people_event results."""
        # Check for zero-face results
        face_results = self._count_hard_evidence(
            ranked_results, candidate_set, "people_event"
        )
        total = len(ranked_results)
        failures = self._detect_trust_failure_patterns(
            ranked_results, candidate_set, "people_event"
        )

        if total == 0:
            return self._empty_decision("people_event")

        face_ratio = face_results / total

        if face_ratio >= 0.7:
            return SearchDecision(
                show_results=True,
                confidence_label="high",
                explanation=[
                    f"{face_results}/{total} results contain detected faces"
                ],
            )

        if face_ratio >= 0.3:
            return SearchDecision(
                show_results=True,
                confidence_label="medium",
                warning_message=(
                    f"Some results may not contain people. "
                    f"{face_results}/{total} have faces detected."
                ),
                explanation=failures,
            )

        # Enrich with builder diagnostics
        diag = candidate_set.diagnostics or {}
        diag_detail = ""
        if diag.get("face_presence_hits") is not None:
            diag_detail = (
                f" Face presence: {diag.get('face_presence_hits', 0)} photos, "
                f"named: {diag.get('named_hits', 0)}, "
                f"cooccurrence: {diag.get('cooccurrence_hits', 0)}."
            )

        return SearchDecision(
            show_results=True,
            confidence_label="low",
            warning_message=(
                f"Low people confidence: only {face_results}/{total} "
                f"results have detected faces."
                f"{diag_detail} "
                f"Run face detection for better results."
            ),
            explanation=failures,
            recommended_actions=[
                "Run face detection pipeline",
                "Try searching by person name",
            ],
        )

    def _evaluate_scenic_family(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """Evaluate scenic results — mostly trust CLIP."""
        if not ranked_results:
            return self._empty_decision("scenic")

        # Check builder diagnostics for pre-filtering stats
        diag = candidate_set.diagnostics or {}
        hard_exclusions = diag.get("hard_exclusions", {})
        total_excluded = sum(hard_exclusions.values()) if hard_exclusions else 0

        # For scenic, check for contamination (documents/screenshots)
        failures = self._detect_trust_failure_patterns(
            ranked_results, candidate_set, "scenic"
        )

        explanation = []
        if total_excluded > 0:
            explanation.append(
                f"ScenicCandidateBuilder pre-filtered {total_excluded} "
                f"non-scenic assets ({hard_exclusions})"
            )

        if failures:
            return SearchDecision(
                show_results=True,
                confidence_label="medium",
                explanation=explanation + failures,
            )

        explanation.append("Scenic search completed with builder pre-filtering")
        return SearchDecision(
            show_results=True,
            confidence_label="high",
            explanation=explanation,
        )

    def _evaluate_pet_family(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """Evaluate pet/animal results."""
        if not ranked_results:
            return self._empty_decision("animal_object")

        # Check for human face contamination
        face_contaminated = sum(
            1 for r in ranked_results[:10]
            if self._result_has_faces(r, candidate_set)
        )

        if face_contaminated > 3:
            return SearchDecision(
                show_results=True,
                confidence_label="low",
                warning_message=(
                    "Some pet results may contain people instead. "
                    "Run face detection to improve filtering."
                ),
                explanation=[
                    f"{face_contaminated}/10 top results have faces"
                ],
            )

        return SearchDecision(
            show_results=True,
            confidence_label="high",
        )

    def _evaluate_utility_family(
        self,
        intent: QueryIntent,
        candidate_set: CandidateSet,
        ranked_results: list,
    ) -> SearchDecision:
        """Evaluate utility results — metadata-driven, always trust."""
        if not ranked_results:
            return self._empty_decision("utility")

        return SearchDecision(
            show_results=True,
            confidence_label="high",
            explanation=["Metadata-driven utility search"],
        )

    # ── Evidence counting ──

    @staticmethod
    def _count_hard_evidence(
        results: list,
        candidate_set: CandidateSet,
        family: str,
    ) -> int:
        """Count results with strong family-specific evidence."""
        count = 0
        top_n = min(20, len(results))

        for r in results[:top_n]:
            path = r.path if hasattr(r, "path") else r
            evidence = candidate_set.evidence_by_path.get(path, {})

            if family == "screenshots":
                score = float(evidence.get("screenshot_score", 0.0) or 0.0)
                provenance = evidence.get("builder") or ""
                if (
                    score >= 0.35
                    or bool(evidence.get("is_screenshot_flag"))
                    or bool(evidence.get("filename_marker"))
                    or bool(evidence.get("ui_text_hit"))
                    or bool(evidence.get("looks_like_phone_screen"))
                ):
                    count += 1
                elif provenance == "screenshot_supplement" and SearchConfidencePolicy._has_screenshot_signal(evidence):
                    count += 1

            elif family == "documents":
                if evidence.get("confidence_level") == "low":
                    pass
                elif (
                    evidence.get("ocr_fts_hit")
                    or evidence.get("ocr_lexicon_hit")
                    or evidence.get("doc_extension")
                    or evidence.get("structural_hit")
                    or evidence.get("low_confidence_admit")
                ):
                    count += 1

            elif family == "type":
                if evidence.get("confidence_level") == "low":
                    pass
                elif (
                    evidence.get("ocr_fts_hit")
                    or evidence.get("ocr_lexicon_hit")
                    or evidence.get("doc_extension")
                    or evidence.get("structural_hit")
                ):
                    count += 1
            elif family == "people_event":
                # Strong evidence: has faces
                if (evidence.get("face_count", 0) > 0
                        or evidence.get("is_named_match")
                        or evidence.get("is_face_presence")):
                    count += 1
            else:
                count += 1  # Other families: count all

        return count

    @staticmethod
    def _count_soft_evidence(
        results: list,
        candidate_set: CandidateSet,
        family: str,
    ) -> int:
        """Count weaker evidence that should not be treated as hard proof."""
        count = 0
        top_n = min(20, len(results))

        for r in results[:top_n]:
            path = r.path if hasattr(r, "path") else r
            evidence = candidate_set.evidence_by_path.get(path, {})

            if family == "screenshots":
                score = float(evidence.get("screenshot_score", 0.0) or 0.0)
                if 0.20 <= score < 0.35:
                    count += 1

        return count

    @staticmethod
    def _has_screenshot_signal(evidence: dict) -> bool:
        """Return True if evidence contains any non-trivial screenshot signal."""
        if not evidence:
            return False

        score = float(evidence.get("screenshot_score", 0.0) or 0.0)
        return any([
            bool(evidence.get("is_screenshot_flag")),
            bool(evidence.get("filename_marker")),
            bool(evidence.get("ui_text_hit")),
            bool(evidence.get("looks_like_phone_screen")),
            bool(evidence.get("looks_like_tablet_screen")),
            bool(evidence.get("looks_like_desktop_screen")),
            bool(evidence.get("dense_ui_ocr")),
            bool(evidence.get("flat_ui_fallback")),
            score >= 0.30,
        ])

    @staticmethod
    def _detect_trust_failure_patterns(
        results: list,
        candidate_set: CandidateSet,
        family: str,
    ) -> List[str]:
        """Detect patterns that indicate trust failures."""
        failures = []
        top_n = min(10, len(results))

        if family == "screenshots":
            no_evidence = 0
            weak_semantic_only = 0

            for r in results[:top_n]:
                path = r.path if hasattr(r, "path") else r
                evidence = candidate_set.evidence_by_path.get(path, {})
                score = float(evidence.get("screenshot_score", 0.0) or 0.0)

                if not SearchConfidencePolicy._has_screenshot_signal(evidence):
                    no_evidence += 1

                if (
                    evidence.get("builder") == "screenshot_supplement"
                    and score > 0.0
                    and not any([
                        evidence.get("is_screenshot_flag"),
                        evidence.get("filename_marker"),
                        evidence.get("ui_text_hit"),
                        evidence.get("looks_like_phone_screen"),
                        evidence.get("flat_ui_fallback"),
                    ])
                ):
                    weak_semantic_only += 1

            if no_evidence > top_n * 0.4:
                failures.append(
                    f"{no_evidence}/{top_n} top results lack screenshot signals"
                )
            if weak_semantic_only > 0:
                failures.append(
                    f"{weak_semantic_only}/{top_n} top results are supplement-only screenshot matches"
                )

        elif family == "documents":
            no_evidence = 0
            low_conf = 0

            for r in results[:top_n]:
                path = r.path if hasattr(r, "path") else r
                evidence = candidate_set.evidence_by_path.get(path, {})
                if evidence.get("confidence_level") == "low" or evidence.get("low_confidence_admit"):
                    low_conf += 1
                elif (
                    not evidence.get("ocr_fts_hit")
                    and not evidence.get("ocr_lexicon_hit")
                    and not evidence.get("doc_extension")
                    and not evidence.get("structural_hit")
                ):
                    no_evidence += 1

            if no_evidence > top_n * 0.5:
                failures.append(
                    f"{no_evidence}/{top_n} top results lack OCR or structural document evidence"
                )
            if low_conf > 0:
                failures.append(
                    f"{low_conf}/{top_n} top results are structural-only without OCR confirmation"
                )

        elif family == "type":
            # Check if top results are scenic photos with no OCR
            no_evidence = 0
            for r in results[:top_n]:
                path = r.path if hasattr(r, "path") else r
                evidence = candidate_set.evidence_by_path.get(path, {})
                if (
                    not evidence.get("ocr_fts_hit")
                    and not evidence.get("ocr_lexicon_hit")
                    and not evidence.get("doc_extension")
                    and not evidence.get("structural_hit")
                ):
                    no_evidence += 1
            if no_evidence > top_n * 0.5:
                failures.append(
                    f"{no_evidence}/{top_n} top results lack type-family structural evidence"
                )

        elif family == "people_event":
            no_faces = 0
            for r in results[:top_n]:
                path = r.path if hasattr(r, "path") else r
                evidence = candidate_set.evidence_by_path.get(path, {})
                if evidence.get("face_count", 0) == 0:
                    no_faces += 1
            if no_faces > top_n * 0.5:
                failures.append(
                    f"{no_faces}/{top_n} top results have no detected faces"
                )

        elif family == "scenic":
            # Check for document/screenshot contamination in top results
            contaminated = 0
            for r in results[:top_n]:
                path = r.path if hasattr(r, "path") else r
                evidence = candidate_set.evidence_by_path.get(path, {})
                soft_pen = evidence.get("soft_penalty", 0.0)
                if soft_pen < -0.10:
                    contaminated += 1
            if contaminated > top_n * 0.3:
                failures.append(
                    f"{contaminated}/{top_n} top scenic results have "
                    f"document-like signals (soft-penalized)"
                )

        return failures

    @staticmethod
    def _result_has_faces(result, candidate_set: CandidateSet) -> bool:
        """Check if a result has detected faces."""
        path = result.path if hasattr(result, "path") else result
        evidence = candidate_set.evidence_by_path.get(path, {})
        return (evidence.get("face_count", 0) or 0) > 0

    # ── Helpers ──

    @staticmethod
    def _empty_decision(family: str) -> SearchDecision:
        """Return decision for empty results."""
        return SearchDecision(
            show_results=False,
            confidence_label="empty",
            warning_message=f"No {family} results found.",
            explanation=["No candidates survived filtering"],
        )

    @staticmethod
    def _evaluate_empty(
        intent: QueryIntent,
        candidate_set: CandidateSet,
        family: str,
    ) -> SearchDecision:
        """Evaluate an empty result set."""
        notes = candidate_set.notes or []
        return SearchDecision(
            show_results=False,
            confidence_label="empty",
            warning_message=f"No results found for this search.",
            explanation=notes,
            recommended_actions=[
                "Try broadening your search",
                "Check if the relevant index has been built",
            ],
        )

    @staticmethod
    def _actions_for_not_ready(family: str) -> List[str]:
        """Recommended actions for not-ready states."""
        if family == "people_event":
            return [
                "Run face detection pipeline to index faces",
                "Face detection is required for people searches",
            ]
        if family == "type":
            return [
                "Run OCR processing to index text in images",
                "OCR is required for document and screenshot searches",
            ]
        return ["Build the required search index"]
