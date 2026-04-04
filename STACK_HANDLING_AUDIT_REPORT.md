# Stack Handling and UI Refresh Audit Report

**Date:** 2026-01-18  
**Issue:** Stack deletion, destacking, restacking, and UI badge refresh verification  

## Summary

Audit reveals critical gaps in stack handling workflows that can lead to UI inconsistencies and "Stack not found" errors. While the core stack operations work correctly, the UI refresh mechanisms are incomplete.

## Key Issues Identified

### 1. Missing UI Refresh After Stack Operations ❌

**Problem:** The `StackGenerationService` correctly warns about needing UI refresh, but no automatic refresh mechanism exists.

**Evidence from logs:**
```
2026-01-18 22:35:00,829 [WARNING] IMPORTANT: 9 stacks were deleted. UI components displaying stack badges should be refreshed to prevent 'Stack not found' errors.
```

**Root Cause:** Stack operations clear/recreate database records, but photo grid UI components aren't notified to refresh their stack badge displays.

### 2. Incomplete Signal Propagation Chain ⚠️

**Problem:** Stack operations emit signals, but the chain from repository → service → controller → UI is broken.

**Current Flow Analysis:**
- `StackRepository.clear_stacks_by_type()` - ✅ Works correctly
- `StackGenerationService.regenerate_similar_shot_stacks()` - ✅ Emits warning log
- `ScanController._generate_similar_stacks()` - ✅ Calls service
- **Missing:** UI refresh notification to photo grids

### 3. Stack Badge Display Logic Gaps ⚠️

**Problem:** Photo grid components check for stack membership, but don't handle cases where stacks are deleted/recreated.

**Affected Components:**
- `layouts/google_layout.py` - Creates stack badges in `_create_thumbnail()`
- `thumbnail_grid_qt.py` - Displays badges via delegate painting
- `layouts/google_components/stack_badge_widget.py` - Handles badge clicks

**Risk:** Users may see stale stack badges pointing to deleted stacks, causing "Stack not found" errors when clicked.

## Detailed Component Analysis

### Stack Repository Layer ✅
```python
# repository/stack_repository.py
def clear_stacks_by_type(self, project_id: int, stack_type: str, rule_version: Optional[str] = None) -> int:
    # ✅ Correctly deletes stacks with CASCADE
    # ✅ Returns count of deleted stacks
    # ❌ No UI notification mechanism
```

### Stack Generation Service ✅/⚠️
```python
# services/stack_generation_service.py
def regenerate_similar_shot_stacks(self, project_id: int, params: StackGenParams) -> StackGenStats:
    # ✅ Clears existing stacks
    cleared = self.stack_repo.clear_stacks_by_type(...)
    # ✅ Logs warning about UI refresh needed
    if cleared > 0:
        self.logger.warning(
            f"IMPORTANT: {cleared} stacks were deleted. UI components displaying "
            f"stack badges should be refreshed to prevent 'Stack not found' errors."
        )
    # ⚠️ No automatic UI refresh triggered
```

### UI Layer - Missing Refresh Mechanisms ❌

#### Google Layout Thumbnails
```python
# layouts/google_layout.py
def _create_thumbnail(self, path: str, size: int) -> QWidget:
    # ✅ Creates stack badges during thumbnail creation
    stack = stack_repo.get_stack_by_photo_id(self.project_id, photo_id)
    if stack:
        stack_badge = create_stack_badge(member_count, stack_id, container)
        stack_badge.stack_clicked.connect(self._on_stack_badge_clicked)
    # ❌ No mechanism to refresh badges after stack operations
```

#### Thumbnail Grid Delegate
```python
# thumbnail_grid_qt.py
class CenteredThumbnailDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):
        # ✅ Paints stack badges as part of thumbnail rendering
        # ❌ No way to trigger repaint after stack data changes
```

## Recommendations

### Immediate Fixes Needed

1. **Implement UI Refresh Signal Chain**
   - Add signal from `StackGenerationService` to notify UI layers
   - Connect signal in `ScanController` to trigger grid refresh
   - Update photo grid components to refresh stack badge data

2. **Add Stack Badge Refresh Method**
   ```python
   # In GooglePhotosLayout class
   def refresh_stack_badges(self):
       """Refresh all stack badges in the current view"""
       # Re-query stack membership for all displayed photos
       # Update badge widgets with current stack data
       # Repaint grid to show updated badges
   ```

3. **Enhance Error Handling**
   ```python
   # In stack badge click handler
   def _on_stack_badge_clicked(self, stack_id: int):
       try:
           # Attempt to load stack
           stack = stack_repo.get_stack_by_id(project_id, stack_id)
           if not stack:
               # Stack was deleted - refresh UI and show user-friendly message
               self.refresh_stack_badges()
               QMessageBox.information(
                   self, 
                   "Stack Updated",
                   "This stack has been updated. The display has been refreshed."
               )
               return
           # Proceed with normal stack view
       except Exception as e:
           # Handle gracefully
   ```

### Long-term Improvements

1. **Real-time Stack Updates**
   - Implement observer pattern for stack changes
   - Auto-refresh affected UI components when stacks are modified

2. **Batch Operation Notifications**
   - Consolidate multiple stack operations into single refresh event
   - Prevent excessive UI updates during bulk operations

3. **Stale Data Detection**
   - Add timestamp checking for stack data validity
   - Proactive refresh of outdated badge displays

## Verification Plan

### Test Scenarios to Implement

1. **Stack Regeneration Workflow**
   ```
   Given: User has photos in similar shot stacks
   When: Stack regeneration is triggered
   Then: 
     - Existing stacks are cleared
     - New stacks are created
     - UI badges are refreshed automatically
     - No "Stack not found" errors occur
   ```

2. **Badge Click After Deletion**
   ```
   Given: Stack badge is displayed for photo
   When: Underlying stack is deleted and new one created
   And: User clicks the badge
   Then:
     - UI refreshes to show current stack data
     - Either opens updated stack or shows appropriate message
   ```

3. **Concurrent Operations**
   ```
   Given: Multiple stack operations running
   When: UI refresh is triggered
   Then:
     - Refresh happens efficiently (no flickering)
     - All badges show current data
     - No race conditions occur
   ```

## Priority Assessment

| Issue | Severity | Impact | Complexity | Priority |
|-------|----------|--------|------------|----------|
| Missing UI refresh after stack ops | High | Users see stale data/errors | Medium | P0 |
| Broken signal chain | Medium | Inconsistent state updates | Low | P1 |
| Stale badge handling | Medium | Error messages | Medium | P1 |

## Next Steps

1. **Implement refresh signal mechanism** (P0)
2. **Add stack badge refresh method** (P0)  
3. **Enhance error handling in badge click** (P1)
4. **Add integration tests** (P2)

This audit confirms that while stack operations work correctly at the data layer, the UI synchronization is incomplete and needs immediate attention to prevent user-facing errors.