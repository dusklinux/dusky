# Elastic Workspace Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the moving workspace overlay plate with stable-width elastic workspace segments whose hover expansion is independent from the focused-workspace dot+ring indicator.

**Architecture:** `Workspaces.tsx` owns a local hovered-workspace state separate from popup state. Each segment derives `active`, `hovered`, and `expanded` classes; exactly one segment is expanded at a time. CSS animates segment width/padding and paint while preserving a fixed total rail footprint.

**Tech Stack:** AGS v3, GTK4/Gnim JSX, AstalHyprland, existing workspace preview/popup state.

## Global Constraints

- Preserve workspace preview behavior and `focusWorkspace(id)` click behavior.
- Preserve five visible workspace segments.
- Active workspace number is replaced by a centered glowing dot.
- Active workspace always keeps a visible ring, even when compact during hover elsewhere.
- Exactly one workspace owns expanded width: hovered workspace if any; otherwise active workspace.
- Rail total width must remain stable while expansion transfers.
- No vertical `transform` bounce.
- Do not modify Network, Audio, Display, Power, Media, Calendar, or their state logic.

---

### Task 1: Elastic state model and markup

**Files:**
- Modify: `components/Workspaces.tsx`
- Test: `tests/test_v253_elastic_workspace_rail.py`

**Interfaces:**
- Consumes: `Hyprland.focusedWorkspace`, `openWorkspacePreview(id)`, `hoverPanel("workspace")`, `leaveTrigger("workspace")`, `focusWorkspace(id)`.
- Produces: local `hoveredWorkspace`, segment classes `active`, `hovered`, `expanded`, active-dot child, number child.

- [ ] **Step 1: Write a failing contract** asserting local hover state, removal of selection plate, exactly-one-expanded logic, active dot/number visibility, and preserved preview/focus callbacks.
- [ ] **Step 2: Run the focused test and confirm RED.**
- [ ] **Step 3: Implement the minimal reactive segment state and markup.**
- [ ] **Step 4: Run the focused test and confirm GREEN.**

### Task 2: Elastic geometry and visual states

**Files:**
- Modify: `style.css`
- Test: `tests/test_v253_elastic_workspace_rail.py`

**Interfaces:**
- Consumes: `.workspace-button.active`, `.hovered`, `.expanded`, `.workspace-active-dot`, `.workspace-number`.
- Produces: stable-width rail, separated glass segments, compact/expanded geometry, active ring, active dot glow, hover sheen.

- [ ] **Step 1: Extend the failing contract** for stable deck width, segment gaps, compact/expanded widths, active ring/dot, hover lighting, and no moving overlay selectors.
- [ ] **Step 2: Confirm RED.**
- [ ] **Step 3: Implement minimal CSS overrides, including dark and Frosted Mist states.**
- [ ] **Step 4: Run focused test, then full regression suite.**

### Task 3: Package verification

**Files:**
- Modify: `README.md`, `install.sh` only for version text if needed.

**Interfaces:**
- Produces: installable `adaptive-glass-ags-v2.5.3.zip`.

- [ ] **Step 1: Run full pytest, Bash syntax, installer smoke, capture helper, TS/TSX syntax, GTK CSS guard, nested-Fragment guard, and diff isolation.**
- [ ] **Step 2: Package ZIP, extract it, and rerun critical tests from extracted package.**
