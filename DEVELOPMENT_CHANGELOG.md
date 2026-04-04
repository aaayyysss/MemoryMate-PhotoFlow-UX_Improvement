# MemoryMate PhotoFlow — Development Changelog

This document tracks all features, modifications, and bug fixes applied to the codebase, organized by area. Use it to follow the development progress and plan future work.

---

## Search Architecture — Phase 11: Scenic Calibration, Search Feature Cache & Retrieval Stability

### Scenic Quality Calibration
- Re-calibrated **`ScenicCandidateBuilder`** to reward positive scenic signals (GPS, panorama geometry, low OCR) with `scenic_positive` score.
- Rebalanced scenic ranking weights to reduce CLIP over-dominance and introduce 10% structural support.
- Implemented duplicate-family collapse for scenic results to prevent repeated file families from dominating top results.

### Search Feature Enrichment
- Enriched **`search_asset_features`** cache table with:
  - `duplicate_group_id` (precomputed content-identity links),
  - `media_type` (first-class video/photo separation),
  - `ocr_text` (denormalized for fast structural scoring).
- Added `ensure_search_features_table` idempotent migration to wire new columns.

### Retrieval Stability (Windows)
- **`services/semantic_search_service.py`** — Dedicated singleton CLIP executor thread to eliminate native MKL/OpenBLAS crashes on Windows.
- Added array `.copy()` to embedding deserialization to prevent memory access violations (`0xC0000005`) under concurrent search load.

### Hybrid Retrieval Extension
- **`repository/asset_repository.py`** — New repository for media assets and instances, enabling cross-project content identity tracking.
- Added dedicated retrieval tables: `asset_ocr_text`, `person_clusters`, `asset_person_links`.

---

## Search Architecture — Phase 10: Screenshot Recall Recovery, Utility Logging Cleanup, CLIP Cache Validation

### Screenshot Recall Recovery
- Expanded screenshot filename markers and UI OCR lexicon.
- Added low-confidence screenshot recovery for assets with at least two non-semantic screenshot signals.
- Added broader screen-geometry support for tablet/desktop captures.
- Added screenshot acceptance diagnostics:
  - `acceptance_reasons`
  - `recovery_signal_count`
  - `low_confidence_recovery`

### Utility Logging Cleanup
- Added explicit `UTILITY_ROUTE` logging before metadata-only utility execution.
- Utility presets now log a first-class route instead of looking like legacy fallback.

### CLIP Cache Validation
- `_has_model_weights()` now recognizes Hugging Face cache roots with:
  - direct config + weights,
  - `snapshots/<hash>/config.json`,
  - `refs/main` -> snapshot resolution.
- Stored CLIP model preference is no longer incorrectly cleared when it points to a valid Hugging Face cache root.

---

## Search Architecture — Phase 7: Constrained Admission & CLIP Warmup Stability

### Screenshot Admission Tightening
- **`SearchConfidencePolicy`** now routes `family="type"` through preset-aware evaluators:
  - documents → `_evaluate_document_type()`
  - screenshots → `_evaluate_screenshot_type()`
- Screenshot confidence now distinguishes:
  - strong screenshot evidence,
  - weak screenshot evidence,
  - supplement-only semantic matches.
- Added screenshot trust diagnostics:
  - `supplement_admitted`
  - `supplement_rejected`
  - `weak_semantic_only`

### Screenshot Supplement Gating
- **`search_orchestrator.py`** no longer lets screenshot semantic supplement hits become legal screenshot candidates by semantic similarity alone.
- Added constrained screenshot supplement admission:
  - strong score passes,
  - medium score requires non-semantic screenshot signals,
  - weak semantic-only hits are rejected from screenshot preset results.
- Improved structured logging:
  - `SCREENSHOT_SUPPLEMENT`
  - `SCREENSHOT_SUPPLEMENT_FILTER`

### CLIP Warmup Crash Fix
- **`semantic_embedding_service.py`** fixed `UnboundLocalError` in `_load_model()` caused by local shadowing of `os`.
- Removed inner `import os` from `_load_model()` so module-level `os.environ[...]` is safe during startup.
- `_MODEL_READY_EVENT` now clears at load start and on failure, and sets only after successful model load and warmup completion.

---

## Search Architecture — Phase 9: Document Precision Tightening

### Canonical Document Contract
- **`DocumentEvidenceEvaluator`** now distinguishes between strong native documents, strong raster documents, and weak page-like lookalikes.
- Removed `.png` from `DOC_NATIVE_EXTENSIONS` to avoid false document positives from screenshots and exported graphics.
- Raster images (JPG, HEIC, PNG) now require stronger multi-signal evidence (e.g., page geometry PLUS text-dense layout) to qualify as documents.
- Added `has_text_dense_layout` signal to detect OCR-rich raster pages.

### Document Builder Tightening
- **`DocumentCandidateBuilder`** no longer admits geometry-only raster candidates as low-confidence documents.
- Admittance now requires at least a text-dense layout for structural-only candidates.
- Expanded evidence payload and diagnostics with `strong_raster_documents` and `text_dense_layout_admits`.

### Gate and Survivor Tightening
- **`GateEngine`** document rescue now accepts only canonical document evidence or strong raster-document evidence.
- **`SearchOrchestrator._prune_document_survivors()`** now rejects geometry-only document survivors.
- Added `DOCUMENT_PRUNE_DROP` debug logging to trace document precision filtering.

### Test Suite Expansion
- Added regression tests for:
  - page-like PNG without OCR rejection,
  - dense-text PNG acceptance,
  - sparse-text JPG rejection,
  - geometry-only survivor pruning.

---

## Search Architecture — Phase 6: Screenshot Builder, Fusion & Diagnostics

### ScreenshotCandidateBuilder
- **`ScreenshotCandidateBuilder`** (`services/candidate_builders/screenshot_candidate_builder.py`) — Dedicated multi-signal screenshot detection. Composite `screenshot_score` [0..1] from: `is_screenshot` metadata flag, filename markers (screenshot, bildschirmfoto, captura, etc.), UI-text OCR patterns (battery, wifi, settings — 2+ required), screen-like aspect ratio + OCR density, and query text term matching.
- Feeds into `w_screenshot` scoring channel for ranking.

### Preset-Level Builder Dispatch
- **`PRESET_BUILDERS`** map — Preset-specific builder overrides that take priority over family-level `CANDIDATE_BUILDERS`. The `"screenshots"` preset now routes to `ScreenshotCandidateBuilder` instead of falling through to `DocumentCandidateBuilder`.

### Candidate Set Fusion
- **`_fuse_candidate_sets()`** — Google-style multi-builder fusion. Merges multiple `CandidateSet` results by path with evidence accumulation (later builder wins on key conflicts). Inherits family from first non-empty set; takes max confidence.

### Builder Diagnostics
- **`CandidateSet.diagnostics`** field — Rejection tracking and debug metadata on every `CandidateSet`.
- **`DocumentCandidateBuilder`** tracks rejection counts by reason and logs histograms.
- **`PeopleCandidateBuilder`** diagnostics: named_hits, cluster_hits, cooccurrence_hits, face_presence_hits, top event_scores.
- **`SearchConfidencePolicy`** enriches low-confidence warnings with top rejection reasons (documents) and face presence breakdown (people).
- **`get_last_candidate_diagnostics()`** accessor on orchestrator.

### Ranker
- **`w_screenshot`** weight channel added to `ScoringWeights` (default 0.00, reserved for screenshot evidence).

---

## Search Architecture — Phase 5: Query Routing & Evidence Alignment

### Planner Taxonomy Fix
- **Deterministic preset routing** — Preset clicks now always use `PRESET_FAMILIES` map directly; NLP heuristics only apply to free-text queries. Eliminates misclassification of "Favorites", "Videos", "Panoramas" as `family=type`.
- **New `utility` family** — "Videos", "Favorites", "GPS Photos" routed to `utility` (metadata-only, no CLIP, no OCR). "Panoramas" routed to `scenic`.
- **Non-person term exclusion** — Preset names like "favorites", "videos", "panoramas" can no longer be parsed as person name candidates.

### Canonical Document Evidence Contract
- **`DocumentEvidenceEvaluator`** (`services/document_evidence_evaluator.py`) — Single source of truth for document evidence evaluation. Used by `DocumentCandidateBuilder`, `GateEngine`, and `SearchConfidencePolicy`. Eliminates the contract mismatch where builder accepted candidates that the gate later rejected.
- **`GateEngine`** now delegates document gate logic to the canonical evaluator.
- **`DocumentCandidateBuilder`** now uses the same evaluator for candidate inclusion.

### Event-Aware People Retrieval
- **`PeopleCandidateBuilder`** upgraded from face-presence to event-evidence ranking. Candidates now scored with: face count (group events), co-occurrence density, portrait orientation, favorite flag, and named-person hits.
- Candidates sorted by `event_score` so "Wedding" returns group photos with multiple faces, not just any photo with a face.
- **Group retrieval** — Builder now queries `group_asset_matches` / `person_groups` from the existing schema for richer co-occurrence data. No parallel person tables introduced.

### Structured Fallback Logging
- **`FAMILY_FALLBACK`** events logged when a family has no dedicated builder and falls through to legacy CLIP pipeline.
- **`LEGACY_FALLBACK`** events logged when the builder is inactive and the orchestrator uses the old inline candidate path.

---

## Search Architecture — Phase 4: Family-First Hybrid Retrieval

### New Components
- **QueryIntentPlanner** (`services/query_intent_planner.py`) — Decomposes natural-language queries into structured intent with family hints, person/scene/date terms, and confidence scoring.
- **DocumentCandidateBuilder** (`services/candidate_builders/document_candidate_builder.py`) — OCR/structure-first retrieval for documents and screenshots; hard-excludes scenic JPGs without textual evidence.
- **PeopleCandidateBuilder** (`services/candidate_builders/people_candidate_builder.py`) — Face-index-first retrieval; blocks results until face index readiness is confirmed.
- **SearchConfidencePolicy** (`services/search_confidence_policy.py`) — Evaluates per-family result trustworthiness; produces confidence labels and user-facing warnings.
- **Migration v13.0.0** (`repository/migrations.py`) — Adds OCR text, person clusters, asset-person link, and query history tables.
- **Unit tests** (`tests/test_candidate_builders.py`) — Comprehensive test suite for candidate builder pipeline.

### Orchestrator Integration
- Builder pipeline runs **before** legacy CLIP path for supported families (type, people_event).
- Graceful fallback to legacy pipeline when no builder handles the family.
- Confidence policy applied as final annotation step on result quality.

### Search Quality Improvements
- Strict document/pet gates and family-aware ranking with narrower presets.
- All family weights (scene, text, face, temporal, structural, OCR) exposed as **dynamic preferences**.
- Scoring contract: added OCR weight, tightened structural scoring, hardened gates.
- Fixed semantic search returning 0 matches: added model alias handling and auto-population of `search_asset_features`.
- Fixed pets search false positives and OCR fallback handling.
- Structure-first retrieval for type-family; face readiness block; pets hardening.

---

## CLIP / Semantic Embedding Stability (Windows)

A series of fixes targeting native access violation crashes (`0xC0000005`, `0xC0000374`) in CLIP/MKL on Windows:

| Commit | Summary |
|--------|---------|
| `2cf9493` | Pin `OMP/MKL/OPENBLAS_NUM_THREADS=1` at module load; stop restoring `set_num_threads`; use `return_tensors="np"` to bypass CLIPProcessor crash path. |
| `78c34f2` | Call `torch.set_num_threads(1)` before `from_pretrained()`; dummy inference pass during model load to stabilize MKL single-thread state. |
| `d6cbf77` | Global inference lock (`_GLOBAL_CLIP_INFER_LOCK`); model-ready event; causal-mask patch idempotency guard; warmup/search race fix. |
| `e338310` | `np.frombuffer` dangling-buffer `.copy()` fix; `_patched_forward` closure leak fix; `_SmartFindWorker` QRunnable leak fix. |
| `cc11f1e` | Skip CLIP entirely for type-family presets; dedicated `ClipExecutor` daemon thread; cancellation checks before each prompt batch. |
| `5d49086` | Fix `_make_causal_mask` crash in CLIP text encoding on Windows. |
| `fce25fd` | Fix access violation crash in CLIP image embedding. |
| `8f983a8` | Fix CLIP import failure causing massive error spam in portable Python environments. |

---

## Crash Fixes & Memory Safety

- **FlowLayout double-add** — Prevented widgets from being added twice to flow layout. (`1d34f9c`)
- **QImage GC** — Fixed garbage-collection of QImage backing data causing display corruption. (`1d34f9c`)
- **Lambda capture race** — Fixed lambda parameter capture in `find_section.py` signal connections. (`dc1b1d7`)
- **QRunnable autoDelete crash** — Fixed `find_section.py` QRunnable lifecycle management. (`dc1b1d7`)
- **Missing `os` import** — Added missing stdlib import causing crash on certain code paths. (`1d34f9c`)
- **Face coverage 0%** — Fixed face coverage reporting as 0% after face pipeline completes. (`4be2c4f`)

---

## Search Orchestrator Fixes

- Fixed SQL rating bug in search orchestrator. (`8e870b4`)
- Fixed backoff duplicate results. (`8e870b4`)
- Fixed thread safety issues in concurrent search execution. (`8e870b4`)
- Fixed missing `project_id` in search queries. (`8e870b4`)

---

## Portable Python / Environment Support

- **APP_DIR migration** — Replaced all `os.getcwd()` calls with `APP_DIR` for correct portable Python operation. (`a39aa2a`)
- **NumPy 2.x compatibility** — Added version check and graceful error handling for NumPy 2.x breaking changes. (`2af01b2`)

---

## Housekeeping

- Added `thumbnails_cache.db` to `.gitignore`. (`d06c1c1`)
- Removed stale log files from repository tracking (`logs/Log-1stRun_FreshDB.txt`, `logs/Log-2ndRun_ExistingDB.txt`).

---

## Files Changed (vs. main)

| Area | Key Files |
|------|-----------|
| Core search | `services/search_orchestrator.py`, `services/semantic_search_service.py`, `services/semantic_embedding_service.py`, `services/smart_find_service.py` |
| New modules | `services/query_intent_planner.py`, `services/search_confidence_policy.py`, `services/document_evidence_evaluator.py`, `services/candidate_builders/*` |
| Database | `repository/migrations.py` |
| UI | `ui/accordion_sidebar/find_section.py`, `ui/semantic_search_widget.py` |
| Entry point | `main_qt.py` |
| Tests | `tests/test_candidate_builders.py` |

---

## Phase 2 Refinement & Stability Overhaul

### Screenshot Detection & Fusion (Phase 2 Core)
- Refined **`ScreenshotCandidateBuilder`** to ensure informative rejection reasons (`no_screenshot_signals`, `weak_screenshot_score`) are always assigned.
- Enabled evidence preservation for rejected candidates, allowing the **`SearchOrchestrator`** to perform "semantic rescue" fusion for near-miss screenshots.
- Verified "Circular Reasoning Fix" in Orchestrator to prevent supplemental hits from self-admitting without independent structural signals.

### Database Integrity & Transaction Safety
- Resolved `sqlite3.IntegrityError: FOREIGN KEY constraint failed` in asset backfill.
- Updated **`BaseRepository`** and all repository classes to support shared connections.
- Refactored **`AssetService`** to wrap the hashing and instance-linking loop in a single atomic transaction.

### Multi-Threaded Stability (CLIP/Windows)
- Refactored **`SemanticSearchWorker`** to route all text encoding through the dedicated safe `ClipExecutor` thread.
- Implemented robust embedding deserialization with mandatory `.copy()` to prevent memory access violations on Windows.

### Test Suite Modernization
- Updated integration tests for Repositories, `PhotoScanService`, and `MetadataService` to align with Schema v3.0.0 and current API signatures.
- Verified all 247 search integration tests pass with the refined fusion logic.

---

*Last updated: 2026-03-15*
