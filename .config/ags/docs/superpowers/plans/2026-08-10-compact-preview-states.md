# Compact Preview States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the v2.6.1 workspace navigator so multi-window grids use their full width, single-window previews are smaller, empty workspaces use a tiny no-preview popup, and window tiles no longer trigger the crowded native tooltip.

**Architecture:** Keep the existing preview state, pagination, capture pipeline, active-workspace suppression, and rail→preview ownership unchanged. Add explicit UI state branches in `WorkspacePreview.tsx` for empty, single-window, and multi-window workspaces; let the multi-window 4×2 rows distribute four tiles evenly across the available width; remove tile `tooltipText`; and add state-specific CSS classes whose final cascade controls footprint without changing icon readability.

**Tech Stack:** AGS v3 / Gnim TSX, GTK4, AstalHyprland/AstalApps, CSS, Python pytest/static contract tests.

## Global Constraints

- Preserve `PREVIEW_PAGE_SIZE = 8`; all windows remain pageable rather than truncated.
- Preserve active-workspace preview suppression and rail→preview hover ownership from v2.6.
- Preserve exact-window focus behavior and capture helper behavior.
- Preserve the v2.5.7 elastic/magnetic workspace rail untouched.
- Preserve Waybar fallback isolation.
- Do not add web-only GTK CSS properties.
- Empty workspaces must not render a hero preview.
- Single-window workspaces must use a smaller footprint than multi-window workspaces.
- Remove the large native tile tooltip; selected-window identity remains the primary readable label.

---

### Task 1: Add v2.6.2 regression contract

**Files:**
- Create: `tests/test_v262_compact_preview_states.py`
- Inspect: `components/WorkspacePreview.tsx`
- Inspect: `style.css`

**Interfaces:**
- Consumes: existing `previewClients`, `previewPageClients`, `selectedClient` reactive state.
- Produces: executable assertions for empty/single/multi state classes, full-width rows, no tile tooltip, and state-specific compact CSS.

- [ ] **Step 1: Write the failing test** requiring `workspace-empty compact`, `workspace-preview-content single-window`, `workspace-preview-content multi-window`, `homogeneous`/fill rows, no `tooltipText`, and final compact CSS markers.
- [ ] **Step 2: Run `pytest -q tests/test_v262_compact_preview_states.py`** and confirm failure against v2.6.1.
- [ ] **Step 3: Do not change production code until the failure is caused only by the missing v2.6.2 behavior.**

### Task 2: Implement state-specific preview structure

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Test: `tests/test_v262_compact_preview_states.py`

**Interfaces:**
- Consumes: `previewClients`, `previewPageClients`, pagination functions, selected-window state.
- Produces: compact empty card, smaller single-window preview, ordinary multi-window navigator with full-width grid.

- [ ] **Step 1: Remove `tooltipText` from `WindowTile`.** Keep hover selection and click focus unchanged.
- [ ] **Step 2: Mark the empty state with a compact class and reduce its copy to `No preview available` / `No open windows` without a hero.**
- [ ] **Step 3: Split populated content into `single-window` vs `multi-window` class state using reactive class computation.**
- [ ] **Step 4: Hide the WINDOWS section entirely for a single-window workspace; identity + hero already identify the only app.**
- [ ] **Step 5: Make each 4-tile row fill the available width with homogeneous cells; preserve two rows and existing page slicing.**
- [ ] **Step 6: Run the focused test and confirm GREEN.**

### Task 3: Add compact state-specific CSS

**Files:**
- Modify: `style.css`
- Test: `tests/test_v262_compact_preview_states.py`

**Interfaces:**
- Consumes: `workspace-empty.compact`, `workspace-preview-content.single-window`, `workspace-preview-content.multi-window` classes.
- Produces: final GTK cascade for compact dimensions and full-width icon distribution.

- [ ] **Step 1: Append a v2.6.2 authoritative CSS block rather than rewriting historical rules.**
- [ ] **Step 2: Set empty-state card to a small footprint with no hero-sized minimum.**
- [ ] **Step 3: Set single-window hero/card smaller than multi-window while preserving identity label readability.**
- [ ] **Step 4: Let multi-window grid rows fill width and let each tile fill its homogeneous cell without growing the outer navigator.**
- [ ] **Step 5: Keep icon pixel/visual size consistent; compactness comes from distribution and state-specific chrome, not unreadably small icons.**
- [ ] **Step 6: Run focused test GREEN.**

### Task 4: Update package metadata and historical assertions

**Files:**
- Modify: `README.md`
- Modify: `install.sh`
- Modify only historical tests whose assertions encode superseded v2.6.1 empty/single/grid geometry.

**Interfaces:**
- Produces: package identified as v2.6.2 with existing unrelated contracts preserved.

- [ ] **Step 1: Update version strings to v2.6.2 and document the compact-state behavior.**
- [ ] **Step 2: Run the entire `pytest -q tests` suite.**
- [ ] **Step 3: If failures are only obsolete geometry assertions, update those specific assertions; stop on any unrelated failure.**
- [ ] **Step 4: Re-run full suite to GREEN.**

### Task 5: Verify and package

**Files:**
- Verify all runtime files.
- Create: `/mnt/data/adaptive-glass-ags-v2.6.2.zip`

**Interfaces:**
- Produces: validated downloadable v2.6.2 artifact.

- [ ] **Step 1: Run TS/TSX syntax/transpile checks used by the existing project.**
- [ ] **Step 2: Run shell syntax, installer smoke, capture helper, GTK/Gnim regression guards, and Waybar isolation checks.**
- [ ] **Step 3: Diff runtime files against v2.6.1 and confirm only the expected workspace preview/CSS/version files changed.**
- [ ] **Step 4: Zip the artifact, extract it to a fresh directory, and rerun the full suite plus focused v2.6.2 contract from the extracted copy.**
