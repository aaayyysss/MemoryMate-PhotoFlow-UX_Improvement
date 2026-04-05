# Changelog

All notable changes to the MemoryMate PhotoFlow search pipeline are documented here.

## [Unreleased] - 2026-04-05

### Phase 6B — Shell-First Routing (Legacy Fallback Retained)

Shell becomes the first routing surface for stable actions. Legacy accordion remains
visible, functional, and available as fallback. No legacy routing removed.

#### Shell-First Direct Actions (`layouts/google_layout.py`)
- `_on_passive_shell_branch_clicked()` upgraded to Phase 6B shell-first router
- All Photos: direct grid reset via `_clear_filter()` (shell-first, no accordion)
- Quick dates (today, yesterday, last 7/30 days, this/last month, this/last year):
  direct grid action via new `_on_shell_quick_date_clicked()` helper
- Quick dates also expand legacy Dates/Quick section for visual continuity
- People branches: continue delegating to MainWindow `_handle_people_branch()`
- Legacy-detailed sections (dates, folders, devices, videos, locations, find, etc.):
  accordion fallback retained with dedupe guard

#### New Helper (`layouts/google_layout.py`)
- `_on_shell_quick_date_clicked()`: synthesizes quick-date clicks to existing
  date-click logic or accordion quick-section API when available

#### MainWindow Router (`main_window_qt.py`)
- `_handle_search_sidebar_branch_request()` upgraded to Phase 6B conservative
  shell-first router: Google layout gets first chance, legacy fallback remains alive
- People branches stay delegated through dedicated `_handle_people_branch()`

#### Routing Unit Tests (`tests/test_phase6b_routing.py`) — NEW
- 115 tests covering all Phase 6B routing paths, no PySide6/Qt required
- **TestAllPhotosRouting** (12 tests): filter clearing for each filter field,
  skip-when-clean, reload-when-never-loaded, no-op-without-project
- **TestQuickDatesRouting** (24 tests): all 8 quick-date branches call handler,
  expand legacy section, don't fall through to legacy map
- **TestShellQuickDateClicked** (10 tests): today/yesterday/this_year/last_year/
  this_month synthesize to date-click, range dates use _request_load,
  prefers quick-section API, invalid key no-op
- **TestPeopleBranchRouting** (27 tests): all 9 People branches delegate to
  MainWindow, no accordion expand, no grid reload
- **TestLegacySectionRouting** (35 tests): 16 legacy branches expand correct
  accordion section, no grid reload, unknown branch no-op, dedupe guard
- **TestNoAccordionGuard** (1 test): graceful no-op when accordion is None
- **TestMainWindowSearchBranchRouter** (6 tests): People→people handler,
  non-People→Google layout, non-Google→sidebar fallback
- Uses lazy-mock import strategy (auto-mocks missing deps like PySide6/numpy)
  and AST extraction for MainWindow method (avoids QMainWindow subclass issue)

### Files Changed
- `layouts/google_layout.py`
- `main_window_qt.py`
- `tests/test_phase6b_routing.py` (new)
- `CHANGELOG.md`

---

### Phase 6A — Visual Polish (Shell Sidebar)

Styling-only pass on the Google shell sidebar. No routing or ownership changes.

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- Body margins tightened: `(10, 2, 10, 6)` → `(10, 0, 10, 8)`, spacing `2` → `4`
- Content area margins widened: `(6, 6, 6, 6)` → `(8, 8, 8, 8)`, spacing `6` → `8`
- Browse section reorganized with visual subheadings: Library, Sources, Collections, Places, Quick Dates
- Years/Months/Days replaced with single "Dates" entry (legacy Dates section remains detailed owner)
- Added `_subhead()` helper and `ShellSubhead` style: `#5f6368`, 11px, bold, 6px top padding
- Section header font bumped `12px` → `13px`, padding refined to `9px 12px 7px 12px`
- Section border-radius `10px` → `12px`, border color `#e6e8eb` → `#e7eaee`
- Header hover `#f5f7fa` → `#f6f8fb` with `12px` border-radius
- Hint bottom padding added: `0 2px` → `0 2px 4px 2px`
- Nav button padding `5px 8px` → `6px 8px`

#### Layout Container (`layouts/google_layout.py`)
- Legacy Tools group border-radius `10px` → `12px`, border color `#e6e8eb` → `#e7eaee`
- Container border-right color aligned to `#e7eaee`

### Files Changed
- `ui/search/google_shell_sidebar.py`
- `layouts/google_layout.py`
- `CHANGELOG.md`

---

### Phase 5 — People Migration (Passive Parity)

Full People domain representation in the new shell, with all legacy People actions accessible via bridge delegation. Still passive — legacy People section remains the action owner.

#### Phase 5 Performance Correction (pass 2) — Remaining Section Guards

**`layouts/google_layout.py`**
- `browse_all` now checks if grid is already loaded before requesting redundant reload
- Skips reload when `_last_reload_signature` is set and no active filters

**`ui/accordion_sidebar/locations_section.py`**
- Added `_loaded_project_id` / `_tree_built` freshness cache
- `load_section()` skips rebuild when already current for same project

**`ui/accordion_sidebar/videos_section.py`**
- Added `_loaded_project_id` / `_tree_built` freshness cache
- `load_section()` skips rebuild when already current for same project

**`ui/accordion_sidebar/people_section.py`**
- Added `_loaded_project_id` / `_tree_built` freshness cache
- `load_section()` skips rebuild when already current for same project

**`ui/accordion_sidebar/devices_section.py`**
- Added `_last_scan_ts` / `_scan_cache_seconds` (60s) cache-age guard
- `load_section()` skips rescan when last scan is within cache window
- Timestamp updated on successful `_on_devices_loaded()`

#### Phase 5 Performance Correction (pass 1) — Interaction Churn Fix

Addressed repeated accordion section rebuilds and mixed Browse action ownership that caused excessive DatesSection/FoldersSection reloads (generation 3–19 churn).

**`layouts/google_layout.py`**
- Added `_last_passive_section` / `_last_passive_section_ts` dedupe: skips duplicate section expand within 1 second
- Rewrote `_on_passive_shell_branch_clicked()` with one-action-path-per-item routing:
  - `all` → grid reload only, no accordion expand
  - `years/months/days/today/...` → expand Dates accordion only
  - `folders` → expand Folders only
  - `videos` → expand Videos only
  - `locations` → expand Locations only
  - `documents/screenshots/favorites` → expand Find only
  - People branches → delegate to MainWindow only, no accordion expand
- Removed double-action paths (expand + reload) that caused duplicate work

**`ui/accordion_sidebar/__init__.py`**
- Added no-op guard in `_expand_section()`: skips if section is already expanded and not loading

**`ui/accordion_sidebar/dates_section.py`**
- Added `_loaded_project_id` / `_tree_built` freshness cache
- `load_section()` skips rebuild if already current for same project
- Flags set on successful `_on_data_loaded()`

**`ui/accordion_sidebar/folders_section.py`**
- Same `_loaded_project_id` / `_tree_built` freshness cache pattern
- `load_section()` skips rebuild if already current for same project
- Flags set on successful `_on_data_loaded()`

#### Phase 5 Correction (pass 2)
- Restructured `_handle_people_branch()` with individual try/except per branch for isolated error handling
- Extracted `_open_people_merge_review()`: gathers merge suggestions via `_suggest_cluster_merges` → `_show_merge_suggestions_dialog`, falls back to accordion expand
- Extracted `_open_unnamed_cluster_review()`: expands People accordion section for unnamed cluster review
- Simplified `_handle_search_sidebar_branch_request()`: clean delegation of `people_*` branches, sidebar fallback for others
- All 9 branch names verified aligned: `people_merge_review`, `people_unnamed`, `people_show_all`, `people_tools`, `people_merge_history`, `people_undo_merge`, `people_redo_merge`, `people_expand`, `people_person:<id>`

#### Phase 5 Correction (pass 1)
- Fixed `_handle_people_branch()`: routes through layout's own handler methods (`_on_people_tools_requested`, `_on_people_merge_history_requested`, `_on_people_undo_requested`, `_on_people_redo_requested`, `_on_accordion_person_clicked`) instead of broken `section_logic` path
- Added `_handle_search_sidebar_branch_request()`: general-purpose router for `SearchSidebar.selectBranch` — delegates `people_*` branches to `_handle_people_branch`, others to sidebar fallback
- Fixed `_refresh_people_quick_section()`: payload now targets `self.sidebar` (SearchSidebar) and `layout.search_sidebar` instead of non-existent `google_shell_sidebar.set_people_quick_payload`

#### People Section (`ui/search/sections/people_quick_section.py`)
- Complete rewrite with top-people list (QListWidget, max 10 items), merge review/unnamed cluster buttons with counts, Show All/Tools buttons, and Legacy Actions (History/Undo/Redo/Expand)
- Signals: `mergeReviewRequested`, `unnamedRequested`, `showAllPeopleRequested`, `peopleToolsRequested`, `mergeHistoryRequested`, `undoMergeRequested`, `redoMergeRequested`, `expandPeopleRequested`, `personRequested(str)`
- `set_people_rows()`, `set_counts()`, `set_legacy_actions_enabled()` payload methods

#### Search Sidebar (`ui/search/search_sidebar.py`)
- Replaced People placeholder with real `PeopleQuickSection` widget
- Wired all 9 People signals → `selectBranch` forwarding
- Added `set_people_quick_payload()` method for count/row updates
- People enabled/disabled follows project state

#### Layout Bridge (`layouts/google_layout.py`)
- Expanded `_on_passive_shell_branch_clicked()` legacy map: added `people_tools`, `people_merge_history`, `people_undo_merge`, `people_redo_merge`, `people_expand`
- Added `people_person:<id>` branch handling → expands People accordion section
- People branches delegate to `main_window._handle_people_branch()` for legacy handler access

#### MainWindow (`main_window_qt.py`)
- Added `_refresh_people_quick_section()`: populates passive People shell from legacy People section data (top_people, merge_candidates, unnamed_count)
- Added `_handle_people_branch()`: routes 9 People branch keys to legacy section handlers (merge review, unnamed clusters, show all, tools, history, undo, redo, expand, person selection)
- People refresh called after project switch in `_on_project_changed_by_id()`

#### Acceptance Checklist
- [x] People panel populated (counts start at zero)
- [x] Review Possible Merges → legacy merge review path
- [x] Show Unnamed Clusters → legacy unnamed cluster review path
- [x] Show All People → legacy People expand
- [x] People Tools → legacy People tools
- [x] History/Undo/Redo/Expand → legacy handlers
- [x] Legacy People section remains underneath as fallback
- [x] `google_legacy` remains untouched

### Files Changed
- `ui/search/sections/people_quick_section.py`
- `ui/search/search_sidebar.py`
- `layouts/google_layout.py`
- `main_window_qt.py`
- `CHANGELOG.md`

---

### Phase 4 — Browse Migration (Passive Parity)

Full Browse domain representation in the new shell, with all legacy Browse categories now accessible. Still passive — legacy accordion remains the action owner.

#### Browse Section (`ui/search/sections/browse_section.py`)
- Complete rewrite with expandable subsections: Library, Sources, Collections, Places, Quick Access
- Library: All Photos, Years, Months, Days
- Sources: Folders, Devices
- Collections: Favorites, Videos, Documents, Screenshots, Duplicates
- Places: Locations
- Quick Access: Today, Yesterday, Last 7/30 days, This/Last month, This/Last year
- `set_counts()` method for future count badge integration
- `browseNodeSelected` signal with key-based routing

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- Expanded Browse section from 5 items to full 20-item parity
- Added: Years, Months, Days, Devices, Favorites, Documents, Screenshots, Duplicates
- Added Quick Access dates: Today, Yesterday, Last 7/30 days, This/Last month, This/Last year

#### Search Sidebar (`ui/search/search_sidebar.py`)
- Integrated `BrowseSection` widget (replaces placeholder)
- Wired `browseNodeSelected` → `selectBranch` signal forwarding
- Added `set_browse_payload()` method for count updates
- Browse enabled/disabled follows project state

#### Layout Bridge (`layouts/google_layout.py`)
- Expanded `_on_passive_shell_branch_clicked()` legacy map: 28 branch keys → accordion section targets
- Added `_refresh_passive_browse_payload()` helper for future count integration
- Browse payload refresh called on: shell creation, project switch, project creation
- "All Photos" click triggers a reload via `request_reload(reason="browse_all")`

#### Acceptance Checklist
- [x] All legacy Browse domains represented in new shell
- [x] Clicking new Browse items gives visible reaction (legacy accordion expand)
- [x] Legacy Browse remains underneath as fallback
- [x] `google_legacy` remains untouched

### Files Changed
- `ui/search/sections/browse_section.py`
- `ui/search/google_shell_sidebar.py`
- `ui/search/search_sidebar.py`
- `layouts/google_layout.py`
- `CHANGELOG.md`

---

### Phase 3 — Shell Quality Checkpoint

Visual polish pass on the passive shell to meet the product-feel baseline before any Browse/People migration begins.

#### Shell Layout (`layouts/google_layout.py`)
- Tightened shell max width from 340px → 300px (target: 280–300px)
- Added `setMaximumHeight(160)` on legacy accordion to keep it visually subordinate
- Refined legacy group box styling: softer border color (#e6e8eb), title color (#5f6368), 10px border-radius

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- Tightened outer margins from 8px → 6px and section spacing from 6px → 6px (consistent)
- Tightened body spacing from 3px → 2px for more compact sections
- Added "Library, sources, collections" hint to Browse section
- Removed duplicate "Duplicates" from Browse (already in Discover)
- Replaced Discover items with plan-specified presets: Beach, Mountains, City
- Refined stylesheet: calmer hover (#f5f7fa for headers, #eef3ff for nav), nav text color aligned to #202124, border-radius 8px on nav buttons

#### Section Expansion Defaults (verified)
| Section | Default |
|---------|---------|
| Search Hub | expanded |
| Discover | expanded |
| People | collapsed |
| Browse | expanded |
| Filters | collapsed |
| Activity | collapsed |
| Legacy Tools | collapsed |

### Files Changed
- `layouts/google_layout.py`
- `ui/search/google_shell_sidebar.py`
- `CHANGELOG.md`

---

### Phase 2B — Passive Shell Insertion

Added a new visual navigation shell above the legacy accordion in `GooglePhotosLayout`. The shell is passive — the legacy accordion remains the sole action owner.

#### New Shell Structure
- **`ui/search/google_shell_sidebar.py`** (new): `GoogleShellSidebar` widget with collapsible card sections: Search Hub, Discover, People, Browse, Filters, Activity. Google Photos-inspired visual design with soft borders, section headers, and hover states.
- **`layouts/google_layout.py`**: Left panel now contains the new shell on top with the legacy accordion below in a collapsible "Legacy Tools" group box. Shell clicks bridge to the accordion via `_on_passive_shell_branch_clicked()` which expands the matching legacy section.
- **`layouts/google_layout_legacy.py`**: Unchanged — no shell insertion.

#### Passive Behavior
- Shell branch clicks expand the corresponding accordion section (e.g. "Folders" → accordion folders section)
- "Open Activity Center" delegates to MainWindow's existing toggle
- No new routing, no `_load_photos()` wiring, no Browse/People migration

### Files Changed
- `ui/search/google_shell_sidebar.py` (new)
- `layouts/google_layout.py`
- `CHANGELOG.md`

---

### Phase 0 — Baseline Bug Fixes

- **AssetRepository.list_duplicate_assets()**: Added missing `limit` and `offset` parameters to support pagination from the service layer. Previously caused `TypeError` at runtime when the duplicate loading worker attempted paginated queries.
- **AssetRepository.delete_asset()**: Added new method for project-scoped asset deletion. Previously `AssetService` called a non-existent `delete()` method with a dict argument, which would crash at runtime when deleting assets with no remaining instances.

### Phase 1 — Layout Split: GoogleLayout + GoogleLayoutLegacy

Separated the Google Photos layout into a frozen stable reference and an active improvement track, following the revised UX master strategy.

#### Layout Separation
- **Created `layouts/google_layout_legacy.py`**: Exact copy of the stable Google Photos layout, frozen as the known-good fallback. Class renamed to `GooglePhotosLayoutLegacy`, layout ID `google_legacy`, display name `Google Photos Legacy`.
- **Preserved `layouts/google_layout.py`**: Unchanged, remains as `GooglePhotosLayout` with layout ID `google` — the active future improvement surface.

#### Registration & Startup
- **`layout_manager.py`**: Registered `GooglePhotosLayoutLegacy` alongside all existing layouts. Changed default startup layout from `current` to `google`.
- **`layouts/__init__.py`**: Added `GooglePhotosLayoutLegacy` to module exports.
- **`main_window_qt.py`**: Updated layout menu fallback from `current` to `google` for consistency.

#### Naming Convention
| Asset | Class | ID | Display Name |
|-------|-------|----|-------------|
| Legacy (frozen) | `GooglePhotosLayoutLegacy` | `google_legacy` | Google Photos Legacy |
| Active (improvement) | `GooglePhotosLayout` | `google` | Google Photos Style |

### Phase 1 Correction — Startup Default Fix

- **`layout_manager.py`**: Fixed `switch_layout()` short-circuit that prevented initial startup from switching to `google` when `_current_layout_id` was already `"current"` but no layout instance existed yet. Added `self._current_layout is not None` guard to the early-return check.
- **`layout_manager.py`**: Added explicit override of saved `"current"` preference to `"google"` in `initialize_default_layout()`. Previous fix only handled the short-circuit; this handles the case where the settings file has `"current_layout": "current"` persisted from an earlier session.

### Phase 2A — Project-Load Ownership Stabilization

Eliminated unnecessary loads during onboarding and project creation. `set_project()` is now the sole owner of project-bound loading in both layouts.

#### Load Gate & Debounce (both `google_layout.py` and `google_layout_legacy.py`)
- **`request_reload()` + `_execute_debounced_reload()`**: New 120ms debounce gate that suppresses duplicate reloads, coalesces rapid project-switch requests, and blocks loads when `project_id is None`. Kwargs are filtered via `_LOAD_PHOTOS_KWARGS` whitelist to prevent passing metadata (e.g. `project_id`) through to `_load_photos()`.
- **`set_project()`**: Rewritten as the single owner of project-bound loading. Includes re-entrancy guard (`_project_switch_in_progress`), same-project early-return, and deferred follow-up reload.
- **`_on_project_changed()`**: Now delegates to `set_project()` instead of duplicating load logic.
- **`_on_create_project_clicked()`**: No longer calls `_load_photos()` directly; delegates to `set_project()`.
- **`_on_search_state_changed()`**: Added `project_id` guard to suppress search-triggered grid reloads before a project is set.

#### Startup & Activation Guards
- **`create_layout()`**: Post-startup layout switch now uses `request_reload()` gated on `project_id`.
- **`_on_startup_ready()`**: Suppresses load when no project is set.
- **`_recheck_column_count()`**: Early-returns when no project is set.
- **`refresh_after_scan()`**: Early-returns when no project is set; also resets debounce signature.

#### Cross-Layout Awareness
- **`main_window_qt.py`**: Grid branch reset and layout-specific refresh checks now recognize `google_legacy` alongside `google`.
- **`services/ui_refresh_mediator.py`**: Google-style refresh path now applies to `google_legacy` layout as well.

### Files Changed
- `repository/asset_repository.py`
- `services/asset_service.py`
- `layouts/google_layout.py`
- `layouts/google_layout_legacy.py` (new)
- `layouts/layout_manager.py`
- `layouts/__init__.py`
- `main_window_qt.py`
- `services/ui_refresh_mediator.py`

---

## [Unreleased] - 2026-03-22

### Industrial-Grade Face Pipeline & Bootstrap Policy

Final comprehensive overhaul of the face processing stack, eliminating filtering bottlenecks and ensuring maximum recall for screenshots and group photos.

#### Face Detection
- **FaceDetectionWorker**:
  - **Zero Truncation**: Eliminated the per-screenshot face cap for `include_cluster` mode (previously 14-18), allowing all detected faces (e.g., 20+ in dense collages) to reach the database.
  - **Always-on Classification**: Mandatory screenshot classification regardless of policy ensures consistent behavior across all modes.
  - **Policy-aware Caps**: Retained tiered limits for standard modes: `exclude` (0), `detect_only` (4).

#### Face Clustering
- **FaceClusterWorker**:
  - **Zero-drop Small Face Policy**: Fully disabled small-face dropping in `include_cluster` mode. Faces are no longer filtered by area ratio, ensuring small background faces in screenshots are clustered.
  - **Very Aggressive Merge Bias**: Increased DBSCAN epsilon to 0.70 for screenshot-inclusive runs to combat over-fragmentation and ensure noisy social media faces are grouped effectively.
  - **Lexicon Expansion**: Added international markers (bildschirmfoto, 스크린샷, etc.) to the clustering-side screenshot detector for localized OS support.
  - **Accounting Granularity**: Implemented class-level `_skip_stats` to track specific attrition reasons (bad_dim, low_conf, small_face_screenshot).

#### Face Pipeline
- **FacePipelineWorker**:
  - **Enhanced Accounting**: Final `FACE_ACCOUNTING` summary now exposes full attrition (detected -> DB -> loaded -> dropped) including screenshot-specific skip statistics.
  - **Policy Consistency**: Guaranteed that interim clustering passes strictly adhere to the user's active screenshot policy.

#### Project Management & Reliability
- **Bootstrap Policy**: Implemented canonical startup selection (Last-used -> Single existing -> Onboarding/Selection state) in `main_window_qt.py` to ensure valid application state on startup.
- **Model Intelligence**: `ProjectRepository` now automatically selects the highest-tier CLIP model available (Large > B16 > B32) for new projects by searching multiple common model directory patterns.
- **UI Feedback**: Added "Model Upgrade Assistant" tooltip and a clearer explanation of screenshot clustering behavior in the scope dialog.
- **Database Stability**: Increased `busy_timeout` to 30,000ms across `DatabaseConnection` and `ReferenceDB` to mitigate locking issues during concurrent background tasks.
- **Concurrency Fix**: Implemented chunked commits (every 50 clusters) in `FaceClusterWorker` to prevent long-held write locks during massive re-clustering operations.
- **Signal Integrity**: Fixed signature mismatches in `MainWindow` and `PeopleManagerDialog` signal handlers to correctly propagate the new 3-mode screenshot policy.

### Files Changed
- `workers/face_detection_worker.py`
- `workers/face_cluster_worker.py`
- `workers/face_pipeline_worker.py`
- `main_window_qt.py`
- `repository/project_repository.py`
- `repository/base_repository.py`
- `reference_db.py`
- `ui/face_detection_scope_dialog.py`
- `ui/people_manager_dialog.py`

### Test Updates
- Verified with 279 tests (100% pass rate).

## [Unreleased] - 2026-03-23

### Search Shell Centralization (UX-1 Completion)

Completed the transition of search ownership to `MainWindow`, establishing a centralized search shell that maintains consistent state across layout switches and handles onboarding mode gracefully.

#### Search Shell Architecture
- **Centralized Ownership**: `MainWindow` is now the sole owner of `SearchStateStore`, `SearchController`, `TopSearchBar`, `SearchResultsHeader`, `ActiveChipsBar`, and `SearchSidebar`.
- **Search Bridge**: Implemented a robust `_on_ux1_search_requested` bridge in `MainWindow` that routes centralized search requests to existing search pipelines, ensuring a low-risk integration.
- **Legacy Cleanup**: Removed redundant search widgets (`SearchBarWidget`, `SemanticSearchWidget`) from `MainWindow` and `GooglePhotosLayout` toolbars to eliminate UI confusion.
- **Cross-Layout Sync**: Updated `LayoutManager` to ensure the centralized search shell remains visible and consistent across all UI layouts (Current, Google, Apple, Lightroom).

#### UI & Onboarding Improvements
- **Onboarding Awareness**: `SearchResultsHeader` now explicitly displays "No active project" during onboarding.
- **Project-Bound Logic**: `SearchSidebar` now disables project-specific sections (Discover, People, Filters) when no project is active, providing a clearer onboarding path.
- **Startup Suppression**: Enhanced startup logic in `MainWindow` to suppress project-bound layout loading when starting in an unbound onboarding state.
- **Navigation Parity**: Updated the `Ctrl+F` shortcut to focus the new centralized `TopSearchBar` across all layouts.

#### Files Changed
- `main_window_qt.py`
- `layouts/google_layout.py`
- `layouts/layout_manager.py`
- `ui/search/search_sidebar.py`
- `ui/search/search_results_header.py`
- `ui/search/active_chips_bar.py`
- `ui/search/empty_state_view.py`
