# Workspace UI Polish v2.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Refine the workspace strip and workspace preview into a calmer, more intentional glass UI with restrained active states, graceful interaction motion, a compact header, and more readable window-list typography.

**Architecture:** Preserve the existing Hyprland workspace and preview-state backends. Restrict runtime changes to `components/Workspaces.tsx`, `components/WorkspacePreview.tsx`, and workspace-specific CSS, using class-driven GTK transitions rather than new dependencies. The workspace strip keeps the existing preview ownership state but uses a quiet active capsule treatment and hover motion; the preview card becomes denser in its header while increasing readable text sizes in the list.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, AstalHyprland, Matugen GTK CSS.

## Global Constraints

- Preserve workspace switching and hover preview behavior.
- Preserve exact-window focusing from the preview list.
- Preserve live/fresh capture behavior and current preview dimensions.
- Do not change Media, Network, Audio, Display, Power, Calendar, or Waybar files.
- Avoid unsupported web CSS properties; GTK-compatible CSS only.
- Dark glass and Frosted Mist must both remain supported.
- Reduce accent bloom/glow rather than shrinking the entire UI.
- Increase list readability while reducing header visual dominance.

---

### Task 1: Calm workspace strip with graceful interaction motion

**Files:**
- Modify: `components/Workspaces.tsx`
- Modify: `style.css`
- Test: `tests/test_v25_workspace_polish.py`

**Interfaces:**
- Consumes: `focusedWorkspace.id`, `activePanel()`, `previewWorkspaceLocalId()`.
- Produces: class-driven states `active`, `previewing`, and `hovering` on `.workspace-button`; restrained `.workspace-deck` and button motion.

- [x] **Step 1: Write a failing test** requiring reduced active/preview glow, 160–220ms transitions, a subtle hover transform, and preserved active/preview state ownership.
- [x] **Step 2: Run focused test and confirm RED.**
- [x] **Step 3: Implement calm capsule styling and hover state behavior without changing workspace switching.**
- [x] **Step 4: Run focused test and confirm GREEN.**

### Task 2: Compact workspace preview hierarchy and readable list

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`
- Test: `tests/test_v25_workspace_polish.py`

**Interfaces:**
- Consumes: existing preview state and capture APIs.
- Produces: compact header, quieter preview frame, larger row typography, smaller close control, and preserved exact-window focus behavior.

- [x] **Step 1: Extend the failing test** for a <=36px header, smaller title dominance, >=10px secondary/row text, restrained selected-row shadow, and compact list heading.
- [x] **Step 2: Run focused test and confirm RED.**
- [x] **Step 3: Implement the header and list hierarchy refinements while retaining the current hero preview dimensions.**
- [x] **Step 4: Run focused test and confirm GREEN.**
- [x] **Step 5: Run the complete regression suite, syntax checks, GTK CSS compatibility checks, and package verification.**

### Task 3: Icon-only open-window grid

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`
- Test: `tests/test_v25_workspace_polish.py`

**Interfaces:**
- Consumes: `previewClients`, `selectedAddress`, `selectPreviewClient`, `activatePreviewClient`.
- Produces: two compact icon rows (four tiles per row), native hover descriptions, exact-window hover preview and click focus.

- [x] **Step 1: Write failing icon-grid tests.**
- [x] **Step 2: Confirm RED.**
- [x] **Step 3: Replace vertical rows with icon-only tiles while retaining preview/focus handlers.**
- [x] **Step 4: Confirm focused and historical regression tests GREEN.**

### Task 4: Structured center clock pill

**Files:**
- Modify: `components/ClockCard.tsx`
- Modify: `style.css`
- Test: `tests/test_v25_clock_polish.py`

**Interfaces:**
- Consumes: local `GLib.DateTime`, existing `PanelTrigger panel="calendar"`.
- Produces: structured time + meridiem pill with restrained hover motion and Frosted Mist variant.

- [x] **Step 1: Write failing clock-pill tests.**
- [x] **Step 2: Confirm RED.**
- [x] **Step 3: Implement structured clock markup and CSS.**
- [x] **Step 4: Confirm focused and full regression suites GREEN.**
