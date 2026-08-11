# Workspace Preview Minimal Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild only the workspace preview card to match the approved minimal reference while preserving all working capture, hover, workspace-switch, and exact-window-focus behavior.

**Architecture:** Keep `workspacePreviewState.ts` untouched. Refactor `WorkspacePreview.tsx` presentation only, then override the v1.6 workspace CSS with a v1.7 minimal visual layer. The popup remains an AGS layer-shell window controlled by the existing popup state.

**Tech Stack:** AGS v3, GTK4/Gnim JSX, AstalHyprland, AstalApps, Matugen CSS tokens.

## Global Constraints

- Do not change hover/click popup behavior.
- Do not change real thumbnail capture behavior.
- Do not change workspace-number click switching.
- Do not change exact-window focus behavior.
- Remove duplicated workspace identity and unnecessary badges.
- Preserve Gnim fragment safety: no nested `<With>`.

---

### Task 1: Minimal workspace card hierarchy

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Test: `tests/test_v17_workspace_minimal.py`

**Interfaces:**
- Consumes: existing reactive workspace/client state and `activatePreviewClient`/`selectPreviewClient`.
- Produces: a minimal header, hero preview, and compact row list.

- [ ] Write failing tests for removal of duplicate hierarchy and presence of minimal header/hero/list classes.
- [ ] Run tests and confirm RED.
- [ ] Refactor JSX without touching state logic.
- [ ] Run tests and confirm GREEN.

### Task 2: Minimal glass visual system

**Files:**
- Modify: `style.css`
- Test: `tests/test_v17_workspace_minimal.py`

**Interfaces:**
- Consumes: v1.7 class names from Task 1.
- Produces: compact glass card matching the approved reference direction.

- [ ] Add failing CSS contract assertions.
- [ ] Run tests and confirm RED.
- [ ] Add v1.7 CSS override section.
- [ ] Run full regression suite and syntax checks.
