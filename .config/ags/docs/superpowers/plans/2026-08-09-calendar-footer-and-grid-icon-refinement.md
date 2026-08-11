# Calendar Footer and Workspace Grid Icon Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the already-approved calendar footer actions and remove the remaining optical offset in the workspace-preview header icon without changing popup behavior.

**Architecture:** Keep the v1.9 calendar and workspace preview behavior intact. Replace the footer's plain icon/text layout with compact inset action tiles and replace the theme-dependent symbolic workspace grid icon with a small CSS-drawn 2x2 grid whose geometry can be centered exactly.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, CSS/Matugen tokens, pytest contract tests.

## Global Constraints

- Do not modify popup hover/click behavior.
- Do not modify workspace switching, preview capture, or exact-window focus behavior.
- Keep the calendar layout, date header, native month navigation, Today action, and Clocks action unchanged functionally.
- Do not touch Waybar.

---

### Task 1: Lock the refinement contract

**Files:**
- Create: `tests/test_v191_micro_polish.py`

**Interfaces:**
- Consumes: v1.9 `ClockCard.tsx`, `WorkspacePreview.tsx`, and `style.css`.
- Produces: regression assertions for premium calendar action tiles and the custom centered workspace grid glyph.

- [ ] Write tests requiring distinct icon shells/classes for Today and Clocks, a compact footer action tile treatment, and a CSS-drawn 2x2 workspace grid.
- [ ] Run the tests and verify they fail on the copied v1.9 implementation.

### Task 2: Refine the calendar footer actions

**Files:**
- Modify: `components/ClockCard.tsx`
- Modify: `style.css`

**Interfaces:**
- Consumes: `goToday()` and `openClocks()`.
- Produces: identical actions with improved visual hierarchy and icons.

- [ ] Replace the generic footer button internals with icon-shell + label composition using calendar and clock symbolic icons.
- [ ] Add inset glass styling, compact dimensions, accent icon shells, and hover elevation.
- [ ] Run the new test and verify calendar action assertions pass.

### Task 3: Eliminate workspace header icon optical offset

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`

**Interfaces:**
- Consumes: existing fixed workspace-header icon shell.
- Produces: a centered, theme-independent 2x2 grid glyph.

- [ ] Replace `view-grid-symbolic` with four CSS-drawn cells in a centered 2x2 layout.
- [ ] Style the glyph with exact dimensions and no font/icon-theme bearings.
- [ ] Run the new test and verify grid assertions pass.

### Task 4: Full regression and package verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: installable v1.9.1 artifact preserving all existing behavior.

- [ ] Run all Python tests.
- [ ] Run shell helper and installer smoke tests.
- [ ] Parse all TS/TSX files.
- [ ] Verify Waybar/popup/workspace state files are unchanged from v1.9.
- [ ] Build and verify `adaptive-glass-ags-v1.9.1.zip`.
