# Workspace Navigator v2.6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scale the workspace preview to all windows with 12-per-page navigation, readable hero identity, tighter spacing, and no preview for the active workspace.

**Architecture:** Keep v2.5.7 rail behavior untouched. Extend `workspacePreviewState.ts` with page state and all-client snapshots; update `WorkspacePreview.tsx` to consume paged clients and render the identity/navigation UI; update `Workspaces.tsx` so active-workspace hover closes/suppresses the preview. CSS changes are limited to the popup navigator.

**Tech Stack:** AGS v3 / Gnim TSX, GTK4, AstalHyprland, AstalApps, Hyprland/grim capture helper, Python contract tests.

## Global Constraints
- Active workspace hover must never display the workspace preview.
- Preserve rail-to-preview interaction ownership for non-active workspaces.
- Retain all visible clients; do not truncate at eight or twelve.
- Page size is exactly 12 clients, laid out as 6 columns x 2 rows.
- Hero remains 390 x 219 and uses readable full-window fit without cropping.
- Preserve exact-window focusing and fresh capture behavior.
- Do not modify Waybar or unrelated popup components.

---

### Task 1: Add v2.6 regression contract

**Files:**
- Create: `tests/test_v26_workspace_navigator.py`

**Interfaces:**
- Consumes: current v2.5.7 workspace preview implementation.
- Produces: structural contract for active-preview suppression, all-client pagination, 6x2 grid, identity strip, and readable hero allocation.

- [ ] **Step 1: Write failing tests** for removal of `MAX_CLIENTS`, page size 12, two six-client rows, pager controls, active workspace suppression, identity strip, full hero allocation, and reduced spacing.
- [ ] **Step 2: Run `pytest -q tests/test_v26_workspace_navigator.py`** and verify failures are caused by missing v2.6 behavior.

### Task 2: Implement all-client paging state

**Files:**
- Modify: `lib/workspacePreviewState.ts`

**Interfaces:**
- Produces: `PREVIEW_PAGE_SIZE`, `previewPage`, `previewPageCount`, `previewPageClients`, `previousPreviewPage()`, `nextPreviewPage()`, `closeWorkspacePreview()`.

- [ ] **Step 1:** Remove the eight-client truncation from `snapshotClients()`.
- [ ] **Step 2:** Add zero-based page state and computed 12-client page slice.
- [ ] **Step 3:** Reset page to zero when opening a workspace preview.
- [ ] **Step 4:** Add bounded previous/next navigation and preview-state cleanup.
- [ ] **Step 5:** Run the focused test and confirm the paging-state assertions pass.

### Task 3: Suppress previews for the active workspace

**Files:**
- Modify: `components/Workspaces.tsx`

**Interfaces:**
- Consumes: active workspace binding and `closeWorkspacePreview()`.
- Produces: hover behavior where active workspace closes/suppresses the popup and non-active workspace opens it.

- [ ] **Step 1:** Branch workspace hover on `active() === id`.
- [ ] **Step 2:** For active hover, keep interaction claim but close preview state and `closePanel("workspace")` without calling `openWorkspacePreview()` or `hoverPanel()`.
- [ ] **Step 3:** Preserve existing non-active hover path unchanged.
- [ ] **Step 4:** Run focused tests.

### Task 4: Build 6x2 paged navigator and identity strip

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`

**Interfaces:**
- Consumes: paged preview state and selected client.
- Produces: two rows of six tiles, pager controls, selected-window identity strip, and tighter density.

- [ ] **Step 1:** Render `previewPageClients` in slices `0..6` and `6..12`.
- [ ] **Step 2:** Add previous/next buttons plus `current / total` indicator, visible only when page count > 1.
- [ ] **Step 3:** Add selected app icon/name/title identity strip beneath the hero.
- [ ] **Step 4:** Allocate `Gtk.Picture` with horizontal/vertical fill inside the 390 x 219 viewport while keeping `Gtk.ContentFit.CONTAIN`.
- [ ] **Step 5:** Reduce navigator/header/content gaps and style pager/identity controls for readability.
- [ ] **Step 6:** Run focused tests.

### Task 5: Update superseded historical contracts and verify

**Files:**
- Modify only historical tests whose assertions explicitly encode the retired 4x2/8-window layout.
- Modify: `README.md`, `install.sh` version metadata.

**Interfaces:**
- Produces: a v2.6 package with unchanged unrelated behavior.

- [ ] **Step 1:** Run full pytest suite and identify only intentional historical expectation mismatches.
- [ ] **Step 2:** Update those obsolete assertions to the new 6x2/12-page design without weakening unrelated behavior checks.
- [ ] **Step 3:** Set version metadata to v2.6.
- [ ] **Step 4:** Run full pytest, TS/TSX syntax, shell syntax, installer smoke, capture helper, GTK/Gnim guards, and Waybar isolation.
- [ ] **Step 5:** Package ZIP, extract to a fresh directory, and rerun full tests plus critical v2.6 contract from the extracted artifact.
