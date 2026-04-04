# Similar Photos Enhancement - Implementation & Best Practices

## Executive Summary

This document explains the current similar photos implementation, compares it with industry best practices (Google Photos, iPhone Photos, Adobe Lightroom), and proposes future enhancements.

---

## Current Implementation ✅

### What's Been Implemented

**1. Fixed Dialog Routing**
- **Exact Duplicates** section → Opens `DuplicatesDialog`
- **Similar Shots** section → Opens `StackBrowserDialog` (new)
- Proper separation between exact and similar photo workflows

**2. StackBrowserDialog (NEW)**
A grid-based browser for all similar photo groups with:
- Grid view (3 columns) of all similar photo stacks
- Representative thumbnail for each group
- Click any card to open detailed `StackViewDialog`
- Group count and total photos display
- **Similarity threshold slider (50-100%)**

**3. Similarity Threshold Slider - How It Works**
```
Lower threshold (50%) = MORE photos visible per group
Higher threshold (100%) = FEWER photos visible (only near-identical)
```

The slider filters **which photos appear within each group**, NOT which groups are shown:
- At 50%: Show all photos with ≥50% similarity to representative
- At 92%: Show only photos with ≥92% similarity to representative
- At 100%: Show only near-perfect matches
- Representative photo is ALWAYS shown regardless of threshold

**Example:**
```
Group A has 10 photos with similarities: [100%, 95%, 90%, 85%, 80%, 75%, 70%, 65%, 60%, 55%]

At 50% threshold → Shows all 10 photos
At 70% threshold → Shows 7 photos (70% and above)
At 90% threshold → Shows 3 photos (90% and above)
At 99% threshold → Shows only representative (100%)
```

**4. Keep Best Button**
- One-click to select all non-representative photos for deletion
- Keeps only the highest quality photo (representative)
- Based on iOS Photos behavior

---

## How Stacks Are Created

### Current Stack Generation Algorithm

**Location:** `services/stack_generation_service.py`

**Method:**
1. **Time-based clustering**: Groups photos taken within 10-second windows
2. **Visual similarity**: Uses CLIP embeddings (512-dimensional semantic vectors)
3. **Same folder**: Only groups photos from the same folder
4. **Threshold**: Fixed at 92% similarity during generation
5. **Representative selection**: Chooses best quality photo as representative

**NOT INCLUDED (yet):**
- ❌ Face detection integration
- ❌ Person-based grouping
- ❌ Dynamic re-clustering based on threshold
- ❌ Location-based grouping
- ❌ Subject/object detection

### Why Only One Group?

If you're seeing only one group with >96% similarity, possible reasons:

1. **Limited photos in time window**: Most photos were taken more than 10 seconds apart
2. **Different folders**: Photos are in different folders (algorithm only groups within same folder)
3. **Low visual similarity**: Photos don't meet the 92% similarity threshold
4. **Insufficient embeddings**: Photos may not have CLIP embeddings generated yet

**How to increase groups:**
1. Ensure photos have embeddings (run "Generate Embeddings" from settings)
2. Check that similar photos are in the same folder
3. Lower the generation threshold in preferences (if available)

---

## Industry Best Practices Comparison

### Google Photos - "Similar"

**How it works:**
1. Groups visually similar photos automatically
2. Shows best shot as thumbnail
3. Can expand to see all similar shots
4. **No threshold slider** - uses fixed algorithm
5. Groups by: Visual similarity + Time proximity + Location

**Similarity to our implementation:**
- ✅ Grid view of groups
- ✅ Representative thumbnail
- ✅ Click to expand
- ❌ No threshold slider (they use fixed algorithm)
- ❌ No face-based grouping shown separately

### iPhone Photos - "Duplicates"

**How it works:**
1. Automatically detects exact and near-duplicates
2. Groups by: Perceptual hash + Visual similarity
3. Shows best quality photo first
4. **One-tap to keep best and delete others**
5. No threshold control (automatic)

**Similarity to our implementation:**
- ✅ Best quality photo selection
- ✅ "Keep Best" one-tap action
- ❌ No threshold slider
- ✅ Simple, clean interface

### Adobe Lightroom - "Stacks"

**How it works:**
1. **Manual stacking** - user creates stacks
2. **Auto-stack by capture time** - groups photos taken in burst
3. Can stack by: Time, GPS, custom criteria
4. Shows stack count badge
5. Expand/collapse stacks

**Similarity to our implementation:**
- ✅ Stack concept
- ✅ Representative photo
- ✅ Expand to see all
- ❌ No automatic visual similarity detection
- ❌ Manual user control

---

## Proposed Enhancements 🚀

### Enhancement 1: Face-Based Grouping (REQUESTED)

**User Request:**
> "any face must group its similar within the chosen threshold"

**Proposal:**
Create **person-centric similar photo groups** where:

1. **Group by person first**, then by similarity within each person's photos
2. Each person gets their own group showing all their photos
3. Similarity slider filters how similar the photos need to be within that person's group
4. Representative is the best quality photo of that person

**Implementation Plan:**

```python
# Step 1: Detect faces in all photos (already exists in codebase)
# services/face_detection_service.py

# Step 2: Cluster faces by person (already exists)
# workers/face_cluster_worker.py

# Step 3: Create person-based stacks
# NEW: services/person_stack_service.py

class PersonStackService:
    def generate_person_stacks(self, project_id, person_id, similarity_threshold):
        """
        Generate a stack of all photos containing a specific person.

        - Get all photos with face crops for this person
        - Compute similarity between face embeddings
        - Group photos by face similarity
        - Return stack with similarity scores
        """
        pass
```

**UI Changes:**

1. Add tab in StackBrowserDialog:
   - "Similar Shots" (current - visual similarity)
   - "People" (NEW - person-based groups)

2. People tab shows:
   - Grid of people (from face detection)
   - Click person → Shows all their photos
   - Similarity slider filters which photos of that person to show

**Benefits:**
- ✅ Matches user's mental model ("all photos of this person")
- ✅ Leverages existing face detection system
- ✅ More intuitive than generic visual similarity
- ✅ Matches Google Photos "people" feature

**Estimated Effort:** Medium (2-3 days)
- Integrate face detection with stack system
- Create person-stack service
- Add UI tab for people vs. similar shots

---

### Enhancement 2: Dynamic Re-clustering (Advanced)

**Proposal:**
Allow users to dynamically adjust clustering threshold BEFORE stacks are created.

**Current:** Stacks are pre-generated at 92% threshold
**Proposed:** Generate stacks on-demand at user-selected threshold

**Implementation:**
```python
def cluster_on_demand(project_id, similarity_threshold):
    """
    Re-cluster photos in real-time based on threshold.

    - Get all photos with embeddings
    - Apply clustering algorithm with custom threshold
    - Return temporary stacks (not persisted)
    """
    # This is expensive for large photo collections
    # Recommend caching results
    pass
```

**Pros:**
- More flexible exploration of similarity
- User can find optimal threshold for their photos

**Cons:**
- Computationally expensive for large collections
- May be slow/laggy
- Requires caching strategy

**Recommendation:** ⚠️ Not recommended for now. Current member-filtering approach is more performant.

---

### Enhancement 3: Multi-Criteria Grouping (Future)

**Proposal:**
Allow grouping by multiple criteria, similar to Lightroom:

1. **Time-based**: Group photos taken within X minutes/hours
2. **Location-based**: Group photos taken at same GPS location
3. **Visual similarity**: Current implementation
4. **Face/Person**: Proposed above
5. **Subject detection**: Group photos of same objects (cars, buildings, food)

**UI:**
Add dropdown in StackBrowserDialog:
```
Group by: [Visual Similarity ▼]
          [Time (Burst Shots)]
          [Location]
          [People/Faces]
          [Subject/Object]
```

**Estimated Effort:** Large (1-2 weeks)
- Requires multiple detection services
- Complex UI for criteria selection
- Need to balance performance vs. flexibility

---

## Recommendations (Priority Order)

### 🔴 HIGH PRIORITY: Face-Based Grouping
**Why:** This is what the user specifically requested
**Benefit:** Leverages existing face detection, matches user's mental model
**Effort:** Medium (2-3 days)
**Implementation:** See "Enhancement 1" above

### 🟡 MEDIUM PRIORITY: Improve Stack Generation Settings
**Why:** Give users control over how stacks are initially created
**Benefit:** More flexibility in what groups are created
**Effort:** Small (few hours)
**Implementation:**
- Add "Similarity Threshold" setting in Preferences → Duplicate Management
- Allow regenerating stacks with different thresholds
- Default: 92% (current), Range: 50-100%

### 🟢 LOW PRIORITY: Multi-Criteria Grouping
**Why:** Nice-to-have but complex
**Benefit:** Professional-level organization like Lightroom
**Effort:** Large (1-2 weeks)
**Implementation:** See "Enhancement 3" above

---

## Current Limitations & Workarounds

### Limitation 1: "I only see one group"

**Cause:** Limited photos meeting clustering criteria
**Workaround:**
1. Check that photos have embeddings generated
2. Ensure similar photos are in same folder
3. Check photos are within 10-second time window
4. Lower slider to 50% to see if more photos appear in that group

### Limitation 2: "Threshold slider doesn't create new groups"

**Cause:** Slider filters members, doesn't re-cluster
**Explanation:** This is by design for performance
**Future Fix:** Implement face-based grouping (see Enhancement 1)

### Limitation 3: "No face-based grouping"

**Cause:** Not implemented yet
**Status:** Proposed in Enhancement 1
**Workaround:** Use "People" section in sidebar (separate from duplicates)

---

## Conclusion

**What's Working:**
✅ Fixed dialog routing (exact vs. similar)
✅ Similarity threshold slider (filters photos within groups)
✅ Keep Best button (one-click cleanup)
✅ Professional UI matching Google Photos style

**What's Next:**
🚀 Face-based grouping (HIGH PRIORITY - matches user request)
⚙️ Configurable generation threshold (MEDIUM PRIORITY)
📊 Multi-criteria grouping (LOW PRIORITY - future enhancement)

**Recommended Next Step:**
Implement **Enhancement 1: Face-Based Grouping** to provide the person-centric similar photo management the user is expecting.

---

## Technical Notes

**Files Modified:**
- `layouts/google_components/stack_view_dialog.py` - StackBrowserDialog + filtering logic
- `ui/accordion_sidebar/duplicates_section.py` - Dialog routing

**Files to Create (for Enhancement 1):**
- `services/person_stack_service.py` - Person-based stack generation
- `repository/person_repository.py` - Person data access (may already exist)

**Integration Points:**
- Face detection: `services/face_detection_service.py`
- Face clustering: `workers/face_cluster_worker.py`
- Face database: Check `repository/` for face-related tables

---

**Document Version:** 1.0
**Date:** 2026-01-17
**Author:** Claude Code Assistant
