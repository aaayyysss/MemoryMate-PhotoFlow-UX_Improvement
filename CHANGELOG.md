# Changelog

All notable changes to the MemoryMate PhotoFlow search pipeline are documented here.

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
