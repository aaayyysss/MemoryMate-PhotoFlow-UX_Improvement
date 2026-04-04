# Stack Handling and UI Refresh - Implementation Summary

**Date:** 2026-01-18  
**Issue:** Stack deletion, destacking, restacking, and UI badge refresh verification  

## Implemented Fixes

### 1. ✅ Added Signal to StackGenerationService

**File:** `services/stack_generation_service.py`

**Changes:**
- Made `StackGenerationService` inherit from `QObject`
- Added `stacks_updated = Signal(int, str)` signal
- Emit signal after stack regeneration completes:
```python
# ✅ CRITICAL FIX: Emit signal to notify UI to refresh stack badges
if cleared > 0 or stacks_created > 0:
    self.logger.info(f"Emitting stacks_updated signal for project {project_id}, type 'similar'")
    self.stacks_updated.emit(project_id, "similar")
```

### 2. ✅ Connected Signal in ScanController

**File:** `controllers/scan_controller.py`

**Changes:**
- Connected stack update signal when initializing StackGenerationService:
```python
stack_gen_service = StackGenerationService(
    photo_repo=photo_repo,
    stack_repo=stack_repo,
    similarity_service=embedding_service
)

# ✅ CRITICAL FIX: Connect stack update signal to refresh UI
stack_gen_service.stacks_updated.connect(self._on_stacks_updated)
```

### 3. ✅ Added Stack Update Handler Method

**File:** `controllers/scan_controller.py`

**Changes:**
- Added `_on_stacks_updated` method with comprehensive refresh logic:
```python
@Slot(int, str)
def _on_stacks_updated(self, project_id: int, stack_type: str):
    """Handle stack updates from StackGenerationService."""
    self.logger.info(f"Stacks updated notification received: project={project_id}, type={stack_type}")
    
    try:
        # Refresh current layout to update stack badges
        if hasattr(self.main, 'layout_manager') and self.main.layout_manager:
            current_layout = self.main.layout_manager._current_layout
            if current_layout and hasattr(current_layout, 'refresh_after_scan'):
                self.logger.info("Refreshing current layout to update stack badges...")
                current_layout.refresh_after_scan()
                self.logger.info("✓ Layout refreshed with updated stack data")
            else:
                # Fallback: refresh sidebar and grid directly
                self.logger.info("Refreshing sidebar and grid to update stack badges...")
                if hasattr(self.main.sidebar, "reload"):
                    self.main.sidebar.reload()
                if hasattr(self.main.grid, "reload"):
                    self.main.grid.reload()
                self.logger.info("✓ Sidebar and grid refreshed with updated stack data")
        else:
            # Legacy fallback
            self.logger.info("Refreshing legacy components to update stack badges...")
            if hasattr(self.main.sidebar, "reload"):
                self.main.sidebar.reload()
            if hasattr(self.main.grid, "reload"):
                self.main.grid.reload()
            self.logger.info("✓ Legacy components refreshed with updated stack data")
            
    except Exception as e:
        self.logger.error(f"Error refreshing UI after stack updates: {e}", exc_info=True)
```

## Verification Plan

### Test Scenario 1: Stack Regeneration Workflow
```
Given: User has photos in similar shot stacks
When: Stack regeneration is triggered during scan
Then: 
  ✅ StackGenerationService emits stacks_updated signal
  ✅ ScanController receives signal via _on_stacks_updated method
  ✅ UI components (layout/grid/sidebar) are refreshed automatically
  ✅ No "Stack not found" errors occur when clicking badges
```

### Test Scenario 2: Badge Click After Stack Update
```
Given: Stack badge is displayed for photo
When: Underlying stack is deleted and recreated
And: User clicks the badge after UI refresh
Then:
  ✅ Badge shows current stack membership count
  ✅ Opens correct updated stack view
  ✅ No error messages displayed
```

## Expected Log Output

After stack regeneration, you should see:
```
2026-01-18 22:35:00,829 [WARNING] IMPORTANT: 9 stacks were deleted. UI components displaying stack badges should be refreshed to prevent 'Stack not found' errors.
2026-01-18 22:35:03,392 [INFO] Similar shot stack generation complete: 9 stacks, 82 memberships, 0 errors
2026-01-18 22:35:03,393 [INFO] Created 9 similar shot stacks
2026-01-18 22:35:03,400 [INFO] Emitting stacks_updated signal for project 1, type 'similar'
2026-01-18 22:35:03,405 [INFO] Stacks updated notification received: project=1, type=similar
2026-01-18 22:35:03,410 [INFO] Refreshing current layout to update stack badges...
2026-01-18 22:35:03,420 [INFO] ✓ Layout refreshed with updated stack data
```

## Key Benefits

1. **Automatic UI Refresh:** No manual intervention needed after stack operations
2. **Prevents Errors:** Eliminates "Stack not found" errors from stale UI badges
3. **Maintains Consistency:** UI always reflects current database state
4. **Robust Error Handling:** Graceful fallbacks for different layout configurations
5. **Comprehensive Logging:** Clear audit trail of refresh operations

## Next Steps

1. **Test the implementation** by running a scan that generates similar shot stacks
2. **Verify UI refresh** by checking that stack badges update correctly
3. **Monitor logs** to confirm signal propagation works as expected
4. **Consider extending** to other stack types (near_duplicate, etc.) if needed

This implementation addresses the core issue identified in the audit: missing UI refresh after stack operations, which was causing "Stack not found" errors for users.