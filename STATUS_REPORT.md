# MemoryMate-PhotoFlow: Groups Pipeline Status Report

**Branch:** `claude/fix-photos-layout-dispatch-mgYLL`
**Date:** 2026-02-16
**Last commit:** `d8a97eb` — fix: Add 'New Group' button to populated groups tab and fix reload_groups signal leak

---

## Executive Summary

Over multiple sessions we built and polished the **Groups feature** — user-defined groups of 2+ people for finding photos where they appear together. The feature follows Google Photos / Apple Photos / Lightroom UX best practices. Groups is now embedded as a tab inside the People section (`[Individuals] [Groups]` toggle). We fixed **14+ bugs** including signal leaks, missing UI elements, schema mismatches, startup freezes, and stale state issues. The pipeline is stable and ready for feature extensions.

---

## Architecture Overview

### Groups as Tab Inside People Section

```
AccordionSidebar (ui/accordion_sidebar/__init__.py) — 8 sections
  └── PeopleSection (people_section.py)
       ├── Tab Bar: [Individuals] [Groups]
       ├── Page 0: Individuals — face cluster grid (PeopleGrid + PersonCards)
       └── Page 1: Groups — lazy-loaded GroupsSection content
            └── GroupsSection (groups_section.py) — embedded, no standalone header
                 ├── Empty state: icon + "Create New Group" button
                 └── Populated state: search bar + "New Group" button + scrollable GroupCards
```

### Signal Forwarding Chain

```
GroupsSection signals ─────→ PeopleSection signals ─────→ AccordionSidebar signals ─────→ GooglePhotosLayout handlers
  groupSelected                groupSelected                selectGroup                     _on_accordion_group_clicked
  newGroupRequested            newGroupRequested             newGroupRequested                _on_new_group_requested
  editGroupRequested           editGroupRequested            editGroupRequested               _on_group_edit_requested
  deleteGroupRequested         deleteGroupRequested          deleteGroupRequested             _on_group_deleted
  recomputeRequested           recomputeGroupRequested       recomputeGroupRequested          _on_recompute_group_requested
```

### Two Group Dialogs

| Dialog | File | Purpose |
|--------|------|---------|
| `NewGroupDialog` | `ui/dialogs/new_group_dialog.py` | Create new group from Groups tab |
| `CreateGroupDialog` | `ui/create_group_dialog.py` | Edit existing group (launched from GooglePhotosLayout) |

Both use Google Photos-style blue ring + checkmark badge for face selection highlighting.

### Two Service Layers

| Service | File | Pattern |
|---------|------|---------|
| `GroupService` | `services/group_service.py` | Static methods + `GroupServiceInstance` wrapper |
| `PeopleGroupService` | `services/people_group_service.py` | Instance methods, used by GroupsSection for listing |

Both use `branch_key` column consistently (correct schema).

---

## Key Files (4,400+ lines)

| File | Lines | Role |
|------|-------|------|
| `ui/accordion_sidebar/people_section.py` | 954 | Tab toggle, lazy-load, signal forwarding |
| `ui/accordion_sidebar/groups_section.py` | 525 | Group cards, search, context menu, new group button |
| `services/people_group_service.py` | 972 | CRUD, match computation queries |
| `services/group_service.py` | 682 | Static API, group creation/update/delete |
| `ui/create_group_dialog.py` | 540 | Edit group dialog with face selection |
| `ui/dialogs/new_group_dialog.py` | 473 | New group dialog with face selection |
| `workers/group_compute_worker.py` | 259 | Background match computation (QRunnable) |
| `ui/accordion_sidebar/__init__.py` | ~940 | Orchestrator, group handlers, new group dialog launch |

---

## Completed Fixes (Chronological)

### Session 1 — Initial Errors

1. **`GroupServiceInstance` missing `get_people_for_group_creation`** (`services/group_service.py`)
   - Added method to both `GroupServiceInstance` and `GroupService`, returning `branch_key`, `display_name`, `rep_thumb_png`, `rep_path`.

2. **`no such column: person_id`** in `_load_existing_group` (`ui/create_group_dialog.py`)
   - Table uses `branch_key` column. Fixed to use `GroupService.get_group()` instead of raw SQL.

3. **Groups sub-section still visible in People** (`ui/accordion_sidebar/people_section.py`)
   - Two sidebar implementations existed (monolithic `accordion_sidebar.py` + modularized `ui/accordion_sidebar/`). Fixed the active modularized version.

### Session 2 — Startup Crash

4. **`AccordionSidebar has no attribute createGroupRequested`** (`layouts/google_layout.py:1196`)
   - Signal was deleted but reference remained. Removed the stale connection.

### Session 3 — Edit Dialog Improvements

5. **Edit dialog shows no faces** (`ui/create_group_dialog.py`)
   - Added `rep_path` to service return data + file-path fallback for thumbnail loading.

6. **Face selection not highlighted** (`ui/create_group_dialog.py`)
   - Redesigned `PersonSelectCard` with Google Photos-style blue ring + checkmark badge via QPainter.

### Session 4 — Groups as Tab in People

7. **Embed Groups as tab inside People section** (`ui/accordion_sidebar/people_section.py`)
   - Added `[Individuals] [Groups]` tab toggle using QStackedWidget.
   - Removed Groups from standalone accordion sections (9 → 8 sections).
   - Implemented lazy-loading with generation-based stale data prevention.

### Session 5 — Polish

8. **Preferences dialog freeze** (`preferences_dialog.py`)
   - 3 synchronous blocking operations in `__init__`. Deferred via `QTimer.singleShot(50/100/150ms)`.

9. **NewGroupDialog selection not highlighted** (`ui/dialogs/new_group_dialog.py`)
   - Upgraded `PersonSelectionCard` with same blue ring + checkmark badge style.

### Session 6 — State Bugs

10. **Groups disappear on section switch** (`ui/accordion_sidebar/people_section.py`)
    - `_groups_loaded_once` stayed `True` after `create_content_widget()` rebuilt QStackedWidget.
    - Fixed by resetting `_groups_loaded_once = False` and `_groups_section = None` at top of method.

### Session 7 — Missing Button + Signal Leak

11. **Missing "Create New Group" button** (`ui/accordion_sidebar/groups_section.py`)
    - Button only existed in empty state and standalone header (no longer rendered).
    - Added `+ New Group` button to populated state content layout.

12. **`reload_groups()` signal duplication & memory leak** (`ui/accordion_sidebar/people_section.py`)
    - Every reload created a NEW `GroupsSection` with duplicate signal connections.
    - Fixed to reuse existing instance, calling `load_section()` instead of recreating.

---

## Deep Audit Results

| Area | Status | Notes |
|------|--------|-------|
| Signal chain end-to-end | OK | GroupsSection → PeopleSection → AccordionSidebar → GooglePhotosLayout |
| `google_layout.py` sidebar import | OK | Uses modularized `ui.accordion_sidebar` (correct) |
| `reference_db.py` dead code (`person_id`) | LATENT | Old methods never called; app uses services with `branch_key` |
| `on_groups_loaded` closure lifecycle | OK | Safe across section rebuilds due to `_groups_section = None` reset |
| `GroupsSection._on_data_loaded` | OK | No duplicate widget creation |
| `workers/group_compute_worker.py` | OK | Properly structured QRunnable |
| `create_content_widget` state reset | OK | Resets `_groups_loaded_once` and `_groups_section` |
| Generation token staleness | OK | Stale data correctly discarded |

---

## Known Latent Issues (Non-Blocking)

1. **`reference_db.py` dead code** — Methods `create_person_group()`, `update_person_group()` use `person_id` column but are never called. Clean up to avoid confusion.

2. **Dual service layers** — `GroupService` and `PeopleGroupService` overlap. Could consolidate.

3. **Migration SQL mismatch** — `repository/schema.py` uses `branch_key` (correct), but migration SQL in `reference_db.py` uses `person_id` (dead code, table created by `GroupService`).

---

## Suggested Next Steps / Features

### Polish & UX
- Add member face thumbnails to GroupCard (small circular avatars of group members)
- Add group icon picker (emoji or custom icon per group)
- Add group count badge on the "Groups" tab button
- Add drag-and-drop reordering of groups
- Animate tab transitions between Individuals/Groups

### Functionality
- Implement "Same Event" match mode (time-window based matching)
- Add bulk group operations (multi-select, batch delete/recompute)
- Add "Smart Groups" (auto-created based on frequently co-occurring people)
- Add group sharing / export as collection

### Technical Debt
- Consolidate `GroupService` + `PeopleGroupService` into single service
- Clean up dead `reference_db.py` group methods
- Add unit tests for group CRUD and match computation
- Remove old monolithic `accordion_sidebar.py` if fully replaced

---

## How to Resume

```bash
git checkout claude/fix-photos-layout-dispatch-mgYLL
# All changes are committed and pushed to remote

# Test checklist:
# 1. Open People section → Groups tab
# 2. Create a group → verify "New Group" button appears after creation
# 3. Toggle between Individuals/Groups tabs → verify groups persist
# 4. Switch to another section and back → verify groups reload correctly
# 5. Edit a group → verify faces show with selection highlighting
# 6. Delete a group → verify list updates
# 7. Create/delete multiple groups → verify no duplicate signals in logs
```

---

*Report generated from branch `claude/fix-photos-layout-dispatch-mgYLL` at commit `d8a97eb`*
