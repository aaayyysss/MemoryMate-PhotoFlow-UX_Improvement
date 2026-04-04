# Stack Generation vs. Viewing Parameters - Analysis & Recommendations

## Executive Summary

There is a **critical architectural mismatch** between:
1. **Stack Generation** (at scan time) - Creates stacks with fixed parameters
2. **Stack Viewing** (in UI) - Filters existing stacks with different parameters

This document analyzes the issue and provides recommendations based on best practices.

---

## Current Architecture

### 1. Stack Generation (Scan Time)

**When:** During photo scanning/import
**Where:** `PreScanOptionsDialog` → `SimilarShotStackWorker` → `StackGenerationService`

**User-Configurable Parameters:**

| Parameter | Range | Default | Purpose |
|-----------|-------|---------|---------|
| `time_window_seconds` | 1-60 | 10 | Only compare photos within this time window |
| `similarity_threshold` | 0.80-0.99 | 0.92 (92%) | Minimum visual similarity to form a stack |
| `min_stack_size` | 2-10 | 3 | Minimum photos required to create a stack |

**Algorithm (`services/stack_generation_service.py`):**
```python
1. Get all photos with timestamps (ordered by created_ts)
2. For each photo:
   a. Find candidates within time_window_seconds
   b. In same folder only
   c. Compute cosine similarity using CLIP embeddings
   d. If similarity >= similarity_threshold:
      - Add to cluster
3. If cluster has >= min_stack_size photos:
   - Create stack
   - Choose representative (best quality)
   - Store similarity scores
4. Persist to database (media_stack, media_stack_member)
```

**Key Point:** Stacks are **materialized** (saved to database) with these fixed parameters.

---

### 2. Stack Viewing (UI Time)

**When:** User clicks "Similar Shots" in Duplicates section
**Where:** `StackBrowserDialog` with similarity slider

**User-Configurable Parameters:**

| Parameter | Range | Default | Purpose |
|-----------|-------|---------|---------|
| `similarity_threshold` | 50%-100% | 92% | Filter which photos to show within each stack |

**Filtering Logic (`_filter_and_display_stacks`):**
```python
1. Load all pre-created stacks from database
2. For each stack:
   a. Filter members by similarity_threshold
   b. Always include representative
   c. Only show stack if >= 2 photos remain
3. Display filtered stacks in grid
```

**Key Point:** This only filters **existing stack members**, it doesn't re-cluster.

---

## The Problem: Architectural Mismatch

### Scenario 1: Missing Similar Photos

**User Workflow:**
1. User scans photos with `similarity_threshold = 0.92` (default)
2. Photos A, B, C have similarities: [100%, 90%, 85%]
3. **Result:** Only A and B are grouped (C excluded, <92%)
4. User opens StackBrowserDialog, slides to 50%
5. **Expected:** Photo C appears
6. **Actual:** Photo C still doesn't appear (it was never added to the stack)

**Why:** Lowering the slider to 50% only filters members that were **already added** at 92%. It doesn't fetch photos that were excluded during generation.

---

### Scenario 2: Can't See Less Similar Photos

**User Workflow:**
1. User has 10 photos of a sunset taken over 5 minutes
2. Similarities range from 60%-95%
3. Scan with default 92% threshold
4. **Result:** Only 3-4 photos form a stack
5. User wants to see all sunset photos together
6. Slides to 60%
7. **Actual:** Still only sees 3-4 photos

**Why:** The other 6 photos were never included in the stack because they didn't meet the 92% generation threshold.

---

### Scenario 3: Time Window Limitation

**User Workflow:**
1. User takes photos of an event over 2 hours
2. Similar poses/scenes repeated throughout
3. Default `time_window_seconds = 10`
4. **Result:** Photos separated by >10 seconds are never compared
5. **Actual:** Multiple small stacks instead of one large stack

**Why:** Time window is applied during generation and can't be changed in the UI.

---

## How Industry Leaders Handle This

### Google Photos

**Approach:** **Real-time clustering** with machine learning

- No pre-scanning required
- Clustering happens automatically in the background
- Uses multiple factors:
  - Visual similarity (embeddings)
  - Time proximity
  - Location (GPS)
  - **Face recognition** (most important)
  - Subject detection (objects, scenes)
- **No user-configurable thresholds**
- Algorithm continuously improves with more data

**Pros:**
- ✅ Always up-to-date
- ✅ No manual scanning
- ✅ Intelligent grouping

**Cons:**
- ❌ Requires cloud processing
- ❌ No user control
- ❌ Black box algorithm

---

### Apple Photos (iPhone/Mac)

**Approach:** **Hybrid** - Automatic + Manual

**Duplicates Detection:**
- Automatic detection on-device
- Shows exact + near-duplicates
- **Fixed algorithm** (no threshold settings)
- "Keep Best" one-tap action

**Stacks/Bursts:**
- Manual stacking by user
- Auto-burst detection (same second)
- Stack picker shows all burst shots
- **No similarity threshold** - shows ALL burst photos

**Pros:**
- ✅ Simple, no configuration
- ✅ Fast on-device processing
- ✅ Privacy-focused

**Cons:**
- ❌ Limited to bursts (same second)
- ❌ No user control over threshold

---

### Adobe Lightroom Classic

**Approach:** **Manual stacking** with smart assists

**Stack Creation:**
1. **Auto-Stack by Capture Time:**
   - User sets time threshold (0-60 seconds)
   - Creates stacks for photos within threshold
   - **Real-time** - can change threshold and re-stack

2. **Manual Stacking:**
   - User selects photos and creates stack
   - Full control

**Stack Viewing:**
- Collapsed: Shows only top photo
- Expanded: Shows all stack members
- **No similarity filtering** - always shows all members

**Pros:**
- ✅ Full user control
- ✅ Flexible time threshold
- ✅ Can re-stack anytime

**Cons:**
- ❌ No automatic visual similarity
- ❌ Manual work required

---

## Recommended Enhancements

### Option 1: **Hybrid Model** (Recommended - HIGH PRIORITY)

**Generate stacks at LOWEST reasonable threshold, filter in UI:**

**Generation Changes:**
- Always generate stacks at **0.80 threshold** (lowest)
- Store ALL similarity scores in database
- Include photos within larger time window (30-60 seconds)
- Don't filter during generation

**Viewing Changes:**
- Filter members in UI based on user's slider (50-100%)
- Lower slider → more photos visible
- Higher slider → fewer photos visible
- **NOW IT WORKS** because all photos are in the database

**Implementation:**

```python
# In PreScanOptionsDialog - Remove similarity threshold setting
# Always use 0.80 internally

# In StackGenerationService
params = StackGenParams(
    similarity_threshold=0.80,  # Fixed, lowest threshold
    time_window_seconds=30,     # Larger window
    min_stack_size=2            # Lower minimum
)
```

**Pros:**
- ✅ UI slider actually works as expected
- ✅ Users can explore full range of similarity
- ✅ No re-scanning needed
- ✅ Simple to implement

**Cons:**
- ⚠️ More stacks created (uses more database space)
- ⚠️ More computation during scanning
- ⚠️ May group dissimilar photos initially

---

### Option 2: **Dynamic Re-clustering** (Advanced)

**Generate clusters on-demand based on UI parameters:**

**Approach:**
- Don't pre-create stacks during scanning
- Only store:
  - Photo embeddings
  - Photo timestamps
  - Photo metadata
- Cluster on-demand when user opens StackBrowserDialog
- Cache results for performance

**Implementation:**

```python
class StackBrowserDialog:
    def _load_stacks(self):
        # Don't load from media_stack table
        # Instead, perform real-time clustering

        clusterer = DynamicClusterer()
        stacks = clusterer.cluster_photos(
            project_id=self.project_id,
            time_window=self.time_window,
            similarity_threshold=self.similarity_threshold
        )
```

**Pros:**
- ✅ Complete flexibility
- ✅ Always accurate to current settings
- ✅ Can change time window too

**Cons:**
- ❌ Slow for large collections (10,000+ photos)
- ❌ Complex caching required
- ❌ Major architecture change

---

### Option 3: **Multiple Pre-Generated Levels** (Middle Ground)

**Generate stacks at multiple thresholds:**

**Approach:**
- Generate 3 sets of stacks:
  - High precision: 95% threshold
  - Medium: 85% threshold
  - Low precision: 75% threshold
- Store all three in database
- UI slider selects which set to show

**Implementation:**

```python
# Generate three levels
for threshold in [0.75, 0.85, 0.95]:
    stats = stack_gen_service.regenerate_similar_shot_stacks(
        project_id=project_id,
        params=StackGenParams(
            similarity_threshold=threshold,
            rule_version=f"1_{threshold}"  # Different version per level
        )
    )

# UI loads appropriate level
if similarity_threshold < 0.80:
    rule_version = "1_0.75"
elif similarity_threshold < 0.90:
    rule_version = "1_0.85"
else:
    rule_version = "1_0.95"
```

**Pros:**
- ✅ Good performance
- ✅ Reasonable flexibility

**Cons:**
- ❌ 3x database storage
- ❌ 3x scanning time
- ❌ Still not fully flexible

---

### Option 4: **Face-First Approach** (Best for People Photos)

**Prioritize face-based grouping over visual similarity:**

**Approach:**
1. Run face detection first
2. Group photos by person
3. Within each person's group:
   - Show all photos of that person
   - Filter by face pose similarity
4. Visual similarity becomes secondary

**This is what we just implemented!**

**Pros:**
- ✅ Matches user mental model
- ✅ More intuitive for portrait photos
- ✅ Leverages existing face detection
- ✅ Already implemented in People tab

**Cons:**
- ⚠️ Only works for photos with faces
- ⚠️ Doesn't help with landscape/object photos

---

## Recommended Implementation Plan

### Phase 1: Quick Fix (Immediate - 1 day)

**Lower generation threshold to match UI range:**

1. **Change default generation threshold:**
   ```python
   # In PreScanOptionsDialog
   self.similarity_threshold = 0.50  # Was 0.92
   ```

2. **Update UI range:**
   ```python
   # In PreScanOptionsDialog
   self.spin_similarity.setRange(0.50, 0.99)  # Was 0.80-0.99
   ```

3. **Update min_stack_size:**
   ```python
   self.min_stack_size = 2  # Was 3 (allow smaller stacks)
   ```

4. **Increase time window:**
   ```python
   self.time_window_seconds = 30  # Was 10 (catch more candidates)
   ```

**Result:**
- Users can now see full range of similarity
- UI slider works as expected
- More stacks created but more useful

**Testing:**
- Generate stacks with new 50% threshold
- Open StackBrowserDialog
- Slide from 50% to 100% - should see gradual filtering

---

### Phase 2: UI Improvements (1-2 days)

**Add regeneration controls:**

1. **Add "Regenerate Stacks" button in StackBrowserDialog:**
   ```python
   regenerate_btn = QPushButton("🔄 Regenerate with Current Settings")
   regenerate_btn.clicked.connect(self._regenerate_stacks)
   ```

2. **Store generation parameters in database:**
   ```sql
   ALTER TABLE media_stack ADD COLUMN generation_params TEXT;
   -- JSON: {"threshold": 0.92, "time_window": 10, ...}
   ```

3. **Show warning if viewing params don't match generation:**
   ```python
   if viewing_threshold < generation_threshold:
       show_warning("Lower threshold won't show more photos. Regenerate stacks?")
   ```

---

### Phase 3: Dynamic Clustering (2-3 days)

**Implement smart caching:**

1. **Cache clustering results:**
   ```python
   # Cache key: (project_id, threshold, time_window)
   cache_key = f"{project_id}_{threshold}_{time_window}"
   if cache_key in cluster_cache:
       return cluster_cache[cache_key]
   ```

2. **Progressive loading:**
   - Load representative photo first
   - Lazy-load members when expanded

3. **Background re-clustering:**
   - Allow user to adjust settings
   - Re-cluster in background thread
   - Update UI when complete

---

### Phase 4: Best Practices Integration (3-5 days)

**Match Google Photos behavior:**

1. **Multi-factor clustering:**
   - Visual similarity (current)
   - Time proximity (current)
   - **GPS location** (new)
   - **Face recognition** (already have data!)
   - **Subject detection** (future - use CLIP labels)

2. **Smart representative selection:**
   - Highest quality (resolution)
   - Best focus (image sharpness)
   - Faces visible and smiling
   - Center-framed composition

3. **Auto-improvement:**
   - Track which photos users keep/delete
   - Adjust clustering algorithm over time

---

## Critical Issues to Address

### Issue 1: Parameter Visibility

**Problem:** Users don't know what generation parameters were used

**Solution:** Show in UI:
```
📊 Stacks generated with:
- Similarity: 92%
- Time window: 10 seconds
- Date: 2026-01-15

Current viewing filter: 50%
⚠️ Lower viewing threshold won't show photos excluded during generation
```

---

### Issue 2: Stale Stacks

**Problem:** Stacks are outdated if photos are added

**Solution:** Auto-detect and prompt:
```
⚠️ 150 new photos added since last stack generation
   [Regenerate Stacks] [Ignore]
```

---

### Issue 3: Time Window Too Restrictive

**Problem:** 10 seconds misses many similar shots

**Solution:**
- Change default to 30-60 seconds
- Or use adaptive window:
  ```python
  # If in burst mode (>5 photos/second): 5 seconds
  # If normal shooting (1-2 photos/minute): 120 seconds
  ```

---

### Issue 4: Folder Restriction

**Problem:** Similar photos in different folders not grouped

**Current:**
```python
folder_id=photo.get("folder_id")  # Same folder only
```

**Recommendation:** Make folder restriction optional:
```python
if settings.get('group_across_folders', False):
    folder_id = None  # Compare all folders
else:
    folder_id = photo.get("folder_id")
```

---

## Conclusion & Immediate Action

### **CRITICAL RECOMMENDATION:**

**Implement Phase 1 immediately** (lower generation threshold to 0.50)

This single change will:
- ✅ Make the UI slider functional
- ✅ Align generation with viewing
- ✅ Provide better user experience
- ✅ Require minimal code changes

**Files to modify:**
1. `ui/prescan_options_dialog.py` - Change defaults
2. `services/stack_generation_service.py` - Update StackGenParams
3. Documentation - Update user guide

**Expected Outcome:**
- Users can slide from 50% to 100% and see gradual filtering
- More photos grouped together
- Better alignment with user expectations

---

## Testing Checklist

After implementing Phase 1:

- [ ] Generate stacks with 50% threshold
- [ ] Verify all photos within time window are included
- [ ] Open StackBrowserDialog
- [ ] Slide threshold from 50% to 100%
- [ ] Verify photo count decreases smoothly
- [ ] Verify at 100% only near-identical photos remain
- [ ] Test with different photo collections:
  - [ ] Bursts (rapid fire)
  - [ ] Portraits (same person, different poses)
  - [ ] Landscapes (same location, different times)
  - [ ] Events (long duration)

---

**Document Version:** 1.0
**Date:** 2026-01-17
**Author:** Claude Code Assistant
**Status:** Recommendations Pending Implementation
