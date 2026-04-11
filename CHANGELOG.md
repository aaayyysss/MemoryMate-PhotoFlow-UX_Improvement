# Changelog

All notable changes to the MemoryMate PhotoFlow search pipeline are documented here.

## [Unreleased] - 2026-04-11

### Phase 10C Fix Pack v3 — Shell/Legacy Parity Audit (Dead Signal Rewire, Filters Population, Discover Search, Inline Search Input)

Follow-up fix pack addressing shell sidebar UX gaps versus `google_layout_legacy.py`
parity expectations. User-reported issue: "the behaviour and handling of the shell
in the google_layout sidebar different sections are faraway from acceptable".
Audit of the legacy sidebar (9 sections) vs shell (8 sections) revealed six P0
gaps where shell branches were dead ends or no-ops. `google_layout_legacy.py`
remains untouched — all fixes applied to the shell path only.

#### Dead Signal Rewire (`layouts/google_layout.py`)
- **Replaced non-existent `_datesLoaded` / `_foldersLoaded` `hasattr` guards**
  with proper `section_logic["dates"|"folders"|"locations"].signals.loaded`
  subscriptions — the previous guards always returned False because the
  accordion sidebar root never exposed these signals, so shell trees never
  auto-refreshed after initial accordion load
- Each section's `loaded` signal now drives a lambda that calls
  `_sync_shell_date_tree` / `_sync_shell_folder_tree` / `_sync_shell_location_tree`
- All three subscriptions wrapped in a single try/except to survive mocked
  accordion sidebars during headless test runs

#### folder_id Branch View-Mode Wiring (`layouts/google_layout.py`)
- `folder_id:<N>` branches now call `_set_view_mode("all", "Folder • <N>")` and
  `_set_shell_active_branch(branch)` before `_execute_folder_click()` — matches
  legacy behavior where clicking a folder sets the state-text pill and
  highlights the active branch in the shell
- Previously folder_id branches only set `_pending_folder_id` and dispatched
  reload, leaving shell state stale

#### Filters Section Populated (`ui/search/google_shell_sidebar.py`)
- Legacy "Filters" section was an empty stub. Now structured with two subheads
  matching iPhone / Google Photos library filter UX:
  - **Media Type**: "All Media" (`all`), "Photos Only" (`filter_photos_only`),
    "Videos Only" (`videos`)
  - **Collections**: "Favorites" (`filter_favorites`),
    "Documents" (`filter_documents`), "Screenshots" (`filter_screenshots`)
- Added 4 new filter branches to `_project_required_branches` set so they gate
  on project availability
- Added matching router handlers in `_on_passive_shell_branch_clicked` — each
  sets shell-primary emphasis, active branch, view mode, and state text
  (placed before the `section_only_map` fallback to avoid being swallowed by
  retired-section handling)
- Added all 4 branches to `shell_active_map` for self-highlight on click

#### Discover Presets Drive Real Search (`layouts/google_layout.py`)
- `discover_beach` / `discover_mountains` / `discover_city` were previously
  no-op highlight-only shell buttons. They now:
  1. Set shell active branch + shell-primary emphasis
  2. Flip view mode to `search` with state-text `"Discover preset, <Name>"`
  3. Reach into `accordion_sidebar.section_logic["find"]._content_widget`,
     locate `_search_field`, seed the preset text, and call
     `_execute_text_search()`
  4. Mirror the query into the shell's new inline search input via
     `set_search_query`
  5. Expand the accordion `find` section so the user sees the result grid
     context (visible outcome — satisfies Phase 9 contract)
- Handler placed before `section_only_map` so it doesn't get caught by the
  retired-section branch

#### Inline Search Input (`ui/search/google_shell_sidebar.py`)
- Added `QLineEdit` import and new `searchQuerySubmitted = Signal(str)` signal
- Replaced the static Search Hub content with an inline `QLineEdit`
  (objectName `ShellSearchInput`) featuring:
  - Placeholder "Search your library..."
  - Clear button enabled
  - `returnPressed` → `_on_search_submitted` private slot
- `_on_search_submitted` emits `disabledBranchRequested("find")` when no
  project is loaded; otherwise strips the text and emits
  `searchQuerySubmitted(text)`
- Added `set_search_query(text)` public setter so layout code can mirror
  queries (used by Discover preset handler)

#### searchQuerySubmitted Wire-Up (`layouts/google_layout.py`)
- `_build_left_panel_with_shell` now connects
  `google_shell_sidebar.searchQuerySubmitted` to new
  `_on_shell_search_submitted` method
- `_on_shell_search_submitted` routes inline queries through the accordion
  find section's `_search_field` + `_execute_text_search`, flips view mode
  to `search` with state-text `"Search • <query>"`, sets active branch to
  `find`, and expands the find accordion section as visible outcome

#### Test Coverage (`tests/test_phase10c_dynamic_shell.py`)
- Added 4 new test classes / 24 tests:
  - `TestFiltersSectionBranches` (7): all 4 filter branches set view mode,
    state text, and active branch correctly
  - `TestDiscoverPresetsExpandFind` (4): each discover preset expands find,
    seeds accordion `_search_field`, and mirrors text in shell input
  - `TestShellSearchSubmit` (6): empty/whitespace ignored, non-empty sets
    search mode, seeds find section, sets active branch, state text format
  - `TestFolderIdSetsViewMode` (3): folder_id sets "all" mode, state text,
    active branch

#### Test Updates for Visible-Outcome Contract
- `tests/test_phase6b_routing.py`: moved `discover_*` branches from
  `RETIRED_SKIP_ACCORDION` to `RETIRED_EXPAND_ACCORDION` (find section)
- `tests/test_phase8_legacy_retirement.py`: same reclassification, plus
  introduced `RETIRED_EXPAND_TARGET` dict so parametrized assertion can
  look up each branch's expected accordion target (all discover_* → `find`)

#### Totals
- **Total shell-phase test count: 452 (up from 428)**
- **`layouts/google_layout_legacy.py`: 0 modifications (standing order upheld)**

### Phase 10C Fix Pack v2 — Runtime Bug Fixes (DB-Backed Trees, Full Video Classification, Similar Shots Crash)

Follow-up fix pack addressing five runtime issues discovered during post-fix-pack
verification: trees not rendering in shell (root cause: helpers queried non-existent
`section_logic` attribute on accordion sidebar), missing video classifications
versus legacy parity, and a crash in the Similar Shots dialog when the
`photo_embedding` table doesn't exist yet.

#### Sync Helpers Rewritten (`layouts/google_layout.py`)
- **`_sync_shell_date_tree`**, **`_sync_shell_folder_tree`**,
  **`_sync_shell_location_tree`** now read directly from `ReferenceDB` instead
  of the non-existent `accordion_sidebar.section_logic` dict
- Uses `db.get_date_hierarchy`, `db.list_years_with_counts`, `db.get_child_folders`,
  `db.get_location_clusters`
- Graceful early-return on missing sidebar / missing project_id; all DB failures
  caught and logged without crashing the UI thread
- Folder tree is built recursively via `build_tree(parent_id)` walking the full
  hierarchy from root
- **Wired to accordion internal signals**: layout now re-syncs shell trees after
  accordion section loads complete, via `_datesLoaded` and `_foldersLoaded`
  signals from the accordion sidebar — ensures trees appear as soon as the
  underlying section logic finishes loading its data

#### Full Video Classification Parity (`ui/search/google_shell_sidebar.py`)
- Added SD resolution branch (`videos_resolution_sd`) — "SD (< 720p)"
- Added 5 codec branches: `videos_codec_h264` (H.264 / AVC),
  `videos_codec_hevc` (H.265 / HEVC), `videos_codec_vp9` (VP9),
  `videos_codec_av1` (AV1), `videos_codec_mpeg4` (MPEG-4)
- Added 4 file size branches: `videos_size_small` (< 100 MB),
  `videos_size_medium` (100 MB - 1 GB), `videos_size_large` (1 - 5 GB),
  `videos_size_xlarge` (> 5 GB)
- Shell videos section now matches legacy classification options (1 + 3 + 4 + 5 + 4 = 17 total)

#### Video Branch Routing (`layouts/google_layout.py`)
- Added router handlers for all 10 new video branches, each calling
  `_on_accordion_video_clicked` with the correct filter spec
  (`resolution:sd`, `codec:h264`..`codec:mpeg4`, `size:small`..`size:xlarge`)
- Extended `shell_active_map` so shell active-branch highlighting works for every
  new video subsection and for `similar_shots → duplicates`

#### Similar Shots Stats Crash Fix (`ui/similar_photo_dialog.py`)
- `_load_existing_stats()` now handles the `photo_embedding` table not yet
  existing — the table is created on first duplicate detection run, not project
  creation
- Specific `sqlite3.OperationalError` catch with a "no such table" check shows a
  user-friendly status: `"Embeddings: not yet generated (run duplicate detection first)"`
- Prevents the dialog from crashing when the user clicks Similar Shots before
  any embeddings have been generated

#### Dynamic Shell Tests Updated (`tests/test_phase10c_dynamic_shell.py`)
- Rewrote `TestSyncShellDateTree`, `TestSyncShellFolderTree`,
  `TestSyncShellLocationTree` to mock `ReferenceDB` instead of
  `section_logic` — reflects the new DB-backed implementation
- Added `test_sync_no_project_id_does_not_crash` for each sync helper
- Added `test_sync_nested_folders` exercising the recursive folder builder
- Extended `TestVideoClassificationBranches` to cover all 10 new branches —
  parametrized on `(branch, expected_spec)` tuples for SD, 5 codecs, 4 sizes
- Added `test_codec_branch_state_text` and `test_size_branch_state_text`
  parametrized classes
- **Total phase10c test count: 90 (up from 60)**
- **Total phase test count: 428 (all passing)**

### Phase 10C Fix Pack — QTreeWidget Dynamic Trees, Renamed Video Branches, Simplified Router

Replaces static shell containers with QTreeWidget-based dynamic trees for Dates,
Folders, and Locations. Renames video branches to structured convention. Adds
folder and location tree sync helpers. Simplifies MainWindow router.

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- **QTreeWidget imports**: Added `QTreeWidget`, `QTreeWidgetItem`
- **Dynamic Dates tree**: `QTreeWidget` with `set_date_tree(years_payload)` — year/month hierarchy
  - Replaces static `_dates_container` with proper tree widget
  - Emits `year_YYYY` and `month_YYYY-MM` branches on click
- **Dynamic Folder tree**: `QTreeWidget` with `set_folder_tree(folder_payload)` — recursive folder hierarchy
  - Emits `folder_id:N` branches on click
- **Dynamic Location tree**: `QTreeWidget` with `set_location_tree(location_payload)` — flat location list
  - Emits `location_name:NAME` branches on click
- **Videos section renamed**: `videos_all` → `videos`, `videos_short` → `videos_duration_short`,
  `videos_medium` → `videos_duration_medium`, `videos_long` → `videos_duration_long`,
  `videos_hd` → `videos_resolution_hd`, `videos_fhd` → `videos_resolution_fhd`,
  `videos_4k` → `videos_resolution_4k`
- **Review section**: Duplicates and Similar Shots tracked via `_review_buttons`
- **ShellTree CSS**: Styled tree items with hover/selection states
- Tree click handlers: `_on_date_tree_item_clicked`, `_on_folder_tree_item_clicked`, `_on_location_tree_item_clicked`

#### Google Layout (`layouts/google_layout.py`)
- **`_sync_shell_date_tree()`** now reads from `accordion_sidebar.section_logic["dates"].years_data`
- **`_sync_shell_folder_tree()`** (new) reads from `accordion_sidebar.section_logic["folders"]._folder_tree_data`
- **`_sync_shell_location_tree()`** (new) reads from `accordion_sidebar.section_logic["locations"].location_clusters`
- All three syncs wired in `_build_left_panel_with_shell()` and `set_project()`
- Video routing updated for renamed branches: `videos_duration_*`, `videos_resolution_*`
- Duplicates routing simplified: mode "review", description "Duplicates"
- Similar Shots now calls `main_window._on_find_similar_photos()` instead of `_open_duplicates_dialog()`
- New routing: `location_name:*`, `folder_id:*`, `month_*` branches
- Date/folder/location tree syncs after accordion clicks

#### MainWindow (`main_window_qt.py`)
- Simplified router: removed `year_` special case, everything routes through `layout._on_passive_shell_branch_clicked(branch)`

#### Dynamic Shell Tests (`tests/test_phase10c_dynamic_shell.py`) — REWRITTEN
- 56 tests covering all Phase 10C fix features
- **TestVideoClassificationBranches** (20 tests): renamed branches, filter specs, mode, state text
- **TestDuplicatesRouting** (3 tests): review mode, dialog, state text
- **TestSimilarShotsRouting** (3 tests): review mode, find_similar call, state text
- **TestDynamicDateRouting** (6 tests): year/month routing, mode, state text
- **TestDynamicFolderRouting** (2 tests): folder_id routing, invalid ID handling
- **TestDynamicLocationRouting** (4 tests): location_name routing, mode, state text, not-found guard
- **TestSyncShellDateTree** (4 tests): section_logic read, payload structure, no-sidebar guard, empty logic
- **TestSyncShellFolderTree** (3 tests): set_folder_tree call, no-sidebar guard, empty logic
- **TestSyncShellLocationTree** (3 tests): set_location_tree call, no-sidebar guard, empty logic
- **TestMainWindowPhase10CRouter** (8 tests): docstring, people, year/video/similar/folder/location/month routing
- **Total test count: 394 (all passing)**

### Phase 10C — Dynamic Dates Tree, Video Classifications, Review Section (first pass)

Shell sidebar now has structured subsections for Videos and Review, a dynamic
Dates tree populated from project data, and video classification filters that
use the real `_on_accordion_video_clicked` filter pipeline.

---

### Phase 10B — Shell-Native Outcome Corrections

Corrects Phase 10 outcomes so retired sections use real filter paths instead
of broken or weak fallbacks.

#### Google Layout (`layouts/google_layout.py`)
- **Videos**: now calls `_on_accordion_video_clicked("all")` which queries `video_metadata`
  table for actual video results, instead of `request_reload(video_only=True)` which silently
  dropped the `video_only` kwarg and returned all media
- **Find**: robust 3-tier fallback — `top_search_bar` → `search_bar` → accordion `find` section
- **Locations**: state text updated to "Pick a location cluster below" (actionable guidance)
- **Devices**: state text updated to "Pick a device source below" (actionable guidance)

#### Test Updates
- Videos test now verifies `_on_accordion_video_clicked("all")` is called
- Added Find fallback-to-accordion test (no search bars available)
- Added Videos fallback-to-load_photos test
- Phase 9 tests updated for corrected state text
- **Total test count: 338 (all passing)**

---

### Phase 10 — Shell-Native Result Surfaces & View Modes

Shell sections now operate in explicit view modes instead of just triggering
reloads. Each retired section sets a named mode (search, videos, locations,
review, devices) with formatted state text. Shell-level Dates Overview provides
quick year shortcuts. This is the first phase where the app feels like it has
distinct navigation modes rather than just filter variants.

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- Added "Dates Overview" quick-access: 2026, 2025, 2024 year shortcuts
- Simplified `_status()` builder method
- Class docstring already at Phase 9 level (shell-native visible outcomes)

#### Google Layout (`layouts/google_layout.py`)
- Added `_current_view_mode` state field (all | videos | locations | search | review | devices)
- Added `_set_view_mode(mode, description)` — sets mode state + formats "MODE . description" text
- Find → search mode, focuses top_search_bar with fallback to search_bar
- Videos → videos mode, requests reload with video_only=True
- Locations → locations mode, expands accordion location section
- Duplicates → review mode, opens duplicate dialog
- Devices → devices mode, expands accordion devices section
- Discover presets → search mode with preset description
- Favorites/Documents/Screenshots → all mode with description
- All Photos → all mode on branch click
- `_clear_shell_state_text()` now sets "Ready" instead of calling clear

#### MainWindow (`main_window_qt.py`)
- Router upgraded to Phase 10 with year shortcut routing (year_2026/2025/2024)
- Year shortcuts route directly to layout.request_reload(reason="year_filter", year=N)
- Router documentation updated to Phase 10

#### View Mode Tests (`tests/test_phase10_view_modes.py`) — NEW
- 39 tests covering all Phase 10 view mode transitions, no PySide6/Qt required
- **TestSetViewMode** (4 tests): mode state, text format with/without description
- **TestFindSearchMode** (4 tests): search mode, state text, focus top/fallback search bar
- **TestVideosMode** (3 tests): videos mode, state text, reload with video_only
- **TestLocationsMode** (3 tests): locations mode, state text, accordion expand
- **TestDuplicatesReviewMode** (3 tests): review mode, state text, open dialog
- **TestDevicesMode** (3 tests): devices mode, state text, accordion expand
- **TestAllPhotosMode** (2 tests): all mode, state text
- **TestDiscoverPresetsMode** (6 tests): search mode, preset descriptions
- **TestFavoritesDocumentsMode** (3 tests): all mode with labels
- **TestClearShellStateTextReady** (1 test): "Ready" text
- **TestMainWindowYearShortcuts** (4 tests): year routing, no people fallthrough
- **TestMainWindowPhase10Router** (3 tests): docstring, delegation

#### Regression Test Updates
- All phase test mocks updated with _set_view_mode, _is_legacy_section_retired, _set_shell_state_text bindings
- Phase 9 state text assertions updated to "MODE . description" format
- Phase 7A/7B/8/9: docstring checks updated to accept Phase 10
- **Total test count: 335 (all passing)**

---

### Phase 9 — Shell-Native Visible Outcomes

Retired shell sections now produce direct, visible outcomes instead of only
changing internal ownership or suppressing accordion expansion.

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- Added shell status message row in Search Hub (`ShellStatus` QLabel)
- Added `set_shell_state_text()` / `clear_shell_state_text()`
- New `_status()` builder method and `_shell_status_label` / `_shell_state_text` state
- New CSS: `QLabel#ShellStatus` — blue text, light blue background, rounded border
- Shell now visibly reports the current shell-native outcome
- Class docstring updated to Phase 9

#### Google Layout (`layouts/google_layout.py`)
- Added `_set_shell_state_text()` / `_clear_shell_state_text()` helpers
- No-project shell clicks now update shell status text
- All Photos visibly reports "Showing all photos"
- Quick dates visibly report the active quick-date context
- Dates, folders, locations, and people now sync visible shell state text
- Retired sections now produce visible outcomes:
  - Find → focuses main search field, reports "Search is ready"
  - Favorites → runs favorite filter, reports "Showing favorites"
  - Documents/Screenshots → loads photos, reports section name
  - Videos → attempts video filter, reports "Showing videos"
  - Locations → expands accordion location section, reports "Showing location results"
  - Duplicates → opens duplicate review dialog, reports "Opening duplicate review"
  - Devices → expands accordion devices section, reports "Showing device sources"
  - Discover presets → report preset name (Beach/Mountains/City)
- Activity center toggle reports "Opening Activity Center"
- Project set clears onboarding status text
- Legacy block remains visible and alive
- Legacy Dates remains the detailed owner

#### MainWindow (`main_window_qt.py`)
- Router documentation updated to Phase 9 shell-native visible outcomes
- No removals, fallback remains alive

#### Visible Outcome Tests (`tests/test_phase9_visible_outcomes.py`) — NEW
- 43 tests covering all Phase 9 visible outcome paths, no PySide6/Qt required
- **TestShellStateTextHelpers** (4 tests): set/clear state text, no-sidebar guards
- **TestFindVisibleOutcome** (4 tests): state text, focus, selectAll, emphasis
- **TestVideosVisibleOutcome** (3 tests): state text, emphasis, active branch
- **TestLocationsVisibleOutcome** (3 tests): state text, accordion expand, emphasis
- **TestDuplicatesVisibleOutcome** (3 tests): state text, emphasis, open dialog
- **TestDevicesVisibleOutcome** (3 tests): state text, accordion expand, emphasis
- **TestDiscoverPresetsVisibleOutcome** (6 tests): 3 presets × state text + emphasis
- **TestFavoritesDocumentsScreenshots** (4 tests): state text, filter call
- **TestQuickDatesShellStateText** (8 tests): all 8 quick dates
- **TestClearFilterShellStateText** (1 test): "Showing all photos"
- **TestActivityCenterShellStateText** (1 test): "Opening Activity Center"
- **TestMainWindowPhase9Router** (3 tests): docstring, people delegation, layout delegation

#### Regression Test Updates
- Phase 6B: split retired branches into skip-accordion and expand-accordion groups
- Phase 8: split retired branches into skip-accordion and expand-accordion groups
- Phase 7A/7B/8: docstring checks updated to accept Phase 9
- **Total test count: 296 (all passing)**

#### Product Effect
- Retired sections now feel meaningfully different from Phase 7B/8
- The shell now communicates results directly instead of only changing routing ownership

---

### Phase 8 — Gradual Legacy Retirement (Wave 1)

First wave of legacy section retirement. Retired sections (find, devices, videos,
locations, duplicates) no longer expand the accordion — the shell handles them
directly. Non-retired sections (dates, folders, people) remain fully functional
through the accordion fallback.

#### Shell Sidebar (`ui/search/google_shell_sidebar.py`)
- Added `_retired_legacy_sections` set tracking retired sections
- Added `set_retired_legacy_sections()` and `is_legacy_section_retired()` methods

#### Google Layout (`layouts/google_layout.py`)
- Added `_retired_legacy_sections` class-level set: find, devices, videos, locations, duplicates
- Added `_is_legacy_section_retired()` helper method
- Added `_refresh_legacy_visibility_state()` — updates legacy group title and shell emphasis
- Retired-section short-circuit in `_on_passive_shell_branch_clicked()` — skips accordion
- Expanded shell-primary emphasis set: retired branches now set emphasis=False
- Non-retired legacy branches (dates, folders) continue to expand accordion with emphasis=True

#### MainWindow (`main_window_qt.py`)
- Router documentation updated to Phase 8 gradual legacy retirement model
- No removals, non-retired fallback remains alive

#### Retirement Tests (`tests/test_phase8_legacy_retirement.py`) — NEW
- 53 tests covering all Phase 8 retirement logic, no PySide6/Qt required
- **TestIsLegacySectionRetired** (9 tests): 5 retired=True, 3 non-retired=False, 1 unknown=False
- **TestRetiredSectionsSkipAccordion** (22 tests): 11 branches skip accordion, 11 set active branch
- **TestNonRetiredStillExpandAccordion** (5 tests): dates/years/months/days/folders still expand
- **TestShellPrimaryEmphasisExpanded** (11 tests): retired branches set emphasis=False
- **TestRefreshLegacyVisibility** (3 tests): fallback title, no-group guard, all-retired title
- **TestMainWindowPhase8Router** (3 tests): docstring, people delegation, layout delegation

#### Regression Test Updates
- Phase 6B: split legacy section map into live/retired, added `test_retired_branch_skips_accordion`
- Phase 7B: split emphasis branches into retired (emphasis=False) and live (emphasis=True)
- Phase 7A/7B: docstring checks updated to accept Phase 8
- **Total test count: 253 (all passing)**

---

### Phase 7B — Shell-Primary Normal-Use Refinement

Shell remains the preferred normal-use surface, while the legacy accordion stays
visible, functional, and available for recovery. No routing removed.

#### Shell Refinement (`ui/search/google_shell_sidebar.py`)
- Added `disabledBranchRequested` signal for no-project shell clicks
- Added `_project_available` state and project-required branch set (26 branches)
- Shell nav buttons now visually soften when clicked before a project exists
- `_nav()` now sets `disabledShell` property and routes through `_emit_branch()`
- Added `set_project_available()` for onboarding/no-project cleanup
- Added `set_legacy_emphasis()` hook for visual separation between shell-primary
  and legacy fallback use
- New CSS: `QPushButton#ShellNavBtn[disabledShell="true"]` — dimmed text, soft hover

#### Google Layout (`layouts/google_layout.py`)
- Wired `disabledBranchRequested` signal to `_on_disabled_shell_branch_requested()`
- Shell project-availability syncs from: `set_project()`, `_build_left_panel_with_shell()`,
  `on_layout_activated()`
- `_on_disabled_shell_branch_requested()` shows informational dialog when no project
- Legacy-emphasis toggling added to all routing paths:
  - Shell-primary actions (All Photos, quick dates, People) → emphasis=False
  - Legacy-detailed actions (dates, folders, locations, etc.) → emphasis=True
  - `_clear_filter()` resets to emphasis=False

#### MainWindow (`main_window_qt.py`)
- Router documentation updated to Phase 7B shell-primary refinement model
- No removals, fallback remains alive

#### Refinement Tests (`tests/test_phase7b_refinement.py`) — NEW
- 44 tests covering all Phase 7B refinement paths, no PySide6/Qt required
- **TestShellOnboardingGating** (3 tests): disabled handler exists, doesn't raise
- **TestSetProjectSyncsShell** (2 tests): set_project enables/disables shell
- **TestLegacyEmphasisFromShellClicks** (28 tests): 12 shell-primary→False,
  16 legacy-detailed→True
- **TestLegacyEmphasisFromAccordion** (4 tests): dates/locations→True,
  person/quick-date→False
- **TestClearFilterResetsEmphasis** (2 tests): emphasis False, active "all"
- **TestLayoutActivationSyncsShell** (2 tests): with/without project
- **TestMainWindowPhase7BRouter** (3 tests): docstring, People, layout delegation

#### Explicit Retentions
- Legacy Tools block remains visible
- Legacy routing remains active
- Legacy Dates remains the detailed owner
- No removals yet

### Files Changed
- `ui/search/google_shell_sidebar.py`
- `layouts/google_layout.py`
- `main_window_qt.py`
- `tests/test_phase7a_active_branch.py` (updated docstring check)
- `tests/test_phase7b_refinement.py` (new)
- `CHANGELOG.md`

---

### Phase 7A — Shell-Primary Usage (Legacy Retained)

Shell becomes the primary normal-use surface, while the legacy accordion remains
visible, functional, and available for recovery. No legacy routing removed.

#### Shell Primary UX (`ui/search/google_shell_sidebar.py`)
- Added active-branch visual state for shell nav buttons
- `_nav()` now registers buttons in `_branch_buttons` dict with `active` property
- `set_active_branch()` / `clear_active_branch()` methods toggle CSS highlight
- New CSS rule: `QPushButton#ShellNavBtn[active="true"]` — blue background/text

#### Google Layout Sync (`layouts/google_layout.py`)
- Added `_set_shell_active_branch()` / `_clear_shell_active_branch()` helpers
- Shell active state now syncs from:
  - shell clicks (via `shell_active_map` in `_on_passive_shell_branch_clicked`)
  - accordion date selection (`_on_accordion_date_clicked`)
  - accordion folder execution (`_execute_folder_click`)
  - accordion branch/person selection (`_on_accordion_branch_clicked`, `_on_accordion_person_clicked`)
  - accordion location selection (`_on_accordion_location_clicked`)
  - quick-date actions (`_on_shell_quick_date_clicked`)
  - clear-filter / All Photos reset (`_clear_filter`)

#### MainWindow Router (`main_window_qt.py`)
- `_handle_search_sidebar_branch_request()` upgraded to Phase 7A shell-primary router
- Google layout gets first chance for non-People branches
- Legacy sidebar fallback remains alive

#### Active-Branch Sync Tests (`tests/test_phase7a_active_branch.py`) — NEW
- 52 tests covering all active-branch sync paths, no PySide6/Qt required
- **TestShellActiveHelpers** (4 tests): set/clear/None/no-sidebar guard
- **TestShellClickSetsActiveBranch** (32 tests): all 31 mapped branches + unknown→None
- **TestAccordionSyncsShellHighlight** (5 tests): dates, branch, person, person-clear, location
- **TestClearFilterSyncsShell** (1 test): _clear_filter → "all"
- **TestQuickDateSyncsShell** (8 tests): all 8 quick-date keys
- **TestAllPhotosShellActive** (2 tests): with/without active filters
- **TestMainWindowPhase7ARouter** (3 tests): docstring, People delegation, layout delegation

#### Explicit Retentions
- Legacy Tools block remains visible
- Legacy routing remains active
- Legacy Dates remains the detailed owner for years/months/days/quick-date drilldown
- No removals yet

### Files Changed
- `ui/search/google_shell_sidebar.py`
- `layouts/google_layout.py`
- `main_window_qt.py`
- `tests/test_phase7a_active_branch.py` (new)
- `CHANGELOG.md`

---

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
