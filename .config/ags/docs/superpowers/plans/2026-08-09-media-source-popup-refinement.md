# Media Source Popup Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the media popup exist only when there are additional currently-playing MPRIS sources, exclude the primary bar source from the popup, and close the popup automatically when no alternatives remain.

**Architecture:** Keep `mediaSnapshot` as the single source of truth for primary-player recency. Add a pure helper that derives only non-primary players whose playback state is currently `playing`. The bar uses the same snapshot to decide whether hover/pin behavior is enabled, while `MediaPanel` renders only those derived alternatives and closes the media panel when eligibility disappears.

**Tech Stack:** AGS v3, Gnim/GTK4, AstalMpris, TypeScript/TSX, Python pytest source-contract tests.

## Global Constraints

- Preserve newest-playing-source ownership of the single bar island.
- Never duplicate the current primary player in the popup.
- Paused and stopped players must not appear in the popup.
- With zero or one currently playing source, hover/click on the media island must not open or pin a popup.
- If an open or pinned media popup loses its last alternative, close and unpin it automatically.
- Preserve source-specific Previous / Play-Pause / Next controls for every popup row.
- Preserve Dark and Frosted Mist visual language and all non-media components.

---

### Task 1: Lock the v2.4.1 behavior with a failing contract

**Files:**
- Create: `tests/test_v241_media_popup_refinement.py`

**Interfaces:**
- Consumes: existing `components/MediaCard.tsx`, `components/MediaPanel.tsx`, `lib/mediaState.ts`, `lib/popupState.ts`.
- Produces: regression contract requiring alternative-playing derivation, conditional popup activation, no primary duplication, and auto-close support.

- [ ] **Step 1: Write the failing test**

Create assertions that require `additionalPlayingPlayers`, require MediaCard popup interaction to be guarded by alternative availability, reject `NOW PLAYING` and `MediaPrimary` from MediaPanel, require only `OTHER MEDIA` rows, reject paused-row rendering, and require `closePanel("media")` when alternatives disappear.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_v241_media_popup_refinement.py`
Expected: FAIL because v2.4 still renders the primary-rich popup and enables popup hover unconditionally.

### Task 2: Derive only additional currently-playing sources

**Files:**
- Modify: `lib/mediaState.ts`

**Interfaces:**
- Consumes: `MediaSnapshot`, `normalizePlaybackStatus`, current `primary` selection.
- Produces: `additionalPlayingPlayers(snapshot: MediaSnapshot): any[]` and reactive alternative availability derived from `mediaSnapshot`.

- [ ] **Step 1: Implement the pure alternative-player helper**

Filter `snapshot.ordered` to players that are not `snapshot.primary` and whose playback state is `playing`, preserving existing recency order.

- [ ] **Step 2: Expose reactive popup eligibility**

Derive a boolean from `mediaSnapshot` that is true only when `additionalPlayingPlayers(snapshot).length > 0`.

### Task 3: Make the bar popup interaction conditional

**Files:**
- Modify: `components/MediaCard.tsx`

**Interfaces:**
- Consumes: `mediaSnapshot`, `additionalPlayingPlayers`, `hoverPanel`, `leaveTrigger`, `togglePin`.
- Produces: one primary island whose hover/click popup behavior exists only when at least one additional source is currently playing.

- [ ] **Step 1: Use one reactive snapshot boundary**

Replace the primary-only `With` binding with a single `With value={mediaSnapshot}` to avoid nested Gnim fragments while deriving both the primary player and popup eligibility in the same render callback.

- [ ] **Step 2: Guard hover and pin actions**

Only call `hoverPanel("media")`, `leaveTrigger("media")`, and `togglePin("media")` when alternatives exist; otherwise the island remains transport-only with no source-popup affordance.

### Task 4: Reduce the popup to alternative playing sources only

**Files:**
- Modify: `components/MediaPanel.tsx`
- Modify: `lib/popupState.ts`

**Interfaces:**
- Consumes: `mediaSnapshot`, `additionalPlayingPlayers`, source-specific transport methods.
- Produces: compact `OTHER MEDIA` list and `closePanel(id)` helper.

- [ ] **Step 1: Add targeted panel close support**

Add `closePanel(id: PanelId)` that clears `pinnedPanel` only when it matches the requested id and clears `activePanel` only when it matches the requested id.

- [ ] **Step 2: Remove duplicated primary presentation**

Delete the `MediaPrimary` block and `NOW PLAYING` section. Render only `additionalPlayingPlayers(snapshot)` beneath one `OTHER MEDIA` heading.

- [ ] **Step 3: Auto-close when alternatives disappear**

Keep a lightweight poll in the always-mounted MediaPanel that calls `closePanel("media")` when the derived alternative list becomes empty.

### Task 5: Tighten popup styling and preserve regressions

**Files:**
- Modify: `style.css`
- Modify only superseded assertions in `tests/test_v24_media_island.py` if the full suite proves they conflict.

**Interfaces:**
- Consumes: existing media source-row classes.
- Produces: smaller source-switcher popup with no primary-card visual weight.

- [ ] **Step 1: Tighten the popup surface**

Reduce popup width/padding to fit source rows and retain Dark/Frosted Mist row styling.

- [ ] **Step 2: Run focused v2.4.1 contract**

Run: `pytest -q tests/test_v241_media_popup_refinement.py`
Expected: PASS.

- [ ] **Step 3: Run full regression suite**

Run: `pytest -q`
Expected: PASS after updating only assertions that explicitly require the superseded v2.4 duplicated-primary popup.

- [ ] **Step 4: Run installer, syntax, CSS, isolation, and ZIP checks**

Run the existing installer smoke and capture helper tests, Bash syntax checks, TypeScript/TSX parse scan, GTK CSS unsupported-property scan, non-media isolation comparison against v2.4, and ZIP extraction regression test.
