# Workspace Preview Polish v1.6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the functional real-thumbnail workspace navigator into a polished Adaptive Glass component while preserving the working hover, click, capture, workspace-switch, and exact-window-focus behavior.

**Architecture:** Keep the existing `workspacePreviewState.ts` capture/focus behavior unchanged. Refine only the workspace preview presentation in `WorkspacePreview.tsx` and its dedicated CSS, using reactive properties and `<For>` only so the Gnim nested-fragment regression cannot return. The preview stage becomes a framed hero surface with selected-app metadata, while the list becomes a compact window switcher with a distinct selected state.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, AstalHyprland/AstalApps, Matugen CSS tokens.

## Global Constraints

- Preserve the working dedicated AGS popup-window architecture.
- Preserve Option A capture semantics: fresh thumbnail on preview open or selected-row change, not continuous streaming.
- Preserve exact-window activation on row click.
- Do not reintroduce `<With>` into `WorkspacePreview.tsx`.
- Do not modify the rest of the bar in this phase.
- Keep Waybar untouched.

---

### Task 1: Lock the workspace-preview visual contract

**Files:**
- Create: `tests/test_v16_workspace_polish.py`
- Modify: none

**Interfaces:**
- Consumes: current `WorkspacePreview.tsx` and `style.css`.
- Produces: regression checks for hero preview metadata, compact list rows, selected-state affordance, and no nested Gnim fragments.

- [ ] **Step 1: Write failing tests** for new workspace hero classes and row metadata.
- [ ] **Step 2: Run the new test file and confirm RED.**
- [ ] **Step 3: Keep tests focused on visible contract rather than implementation internals.**

### Task 2: Refine workspace preview structure

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Test: `tests/test_v16_workspace_polish.py`

**Interfaces:**
- Consumes: `selectedClient`, `selectedAddress`, `previewPath`, `capturing`, `captureError`, `previewClients`.
- Produces: hero stage with selected-app identity, preview status badge, compact rows with selected marker and focus affordance.

- [ ] **Step 1: Add hero metadata overlay using reactive label/icon properties.**
- [ ] **Step 2: Add selected-app chip and concise preview-status treatment.**
- [ ] **Step 3: Restructure window rows into icon + primary/secondary copy + selected/focus affordance.**
- [ ] **Step 4: Run tests and confirm GREEN.**

### Task 3: Apply serious Adaptive Glass styling

**Files:**
- Modify: `style.css`
- Test: `tests/test_v16_workspace_polish.py`

**Interfaces:**
- Consumes: new CSS classes emitted by `WorkspacePreview.tsx`.
- Produces: 16:9 hero frame, layered glass hierarchy, compact rows, stronger selected-window glow, cleaner typography and spacing.

- [ ] **Step 1: Increase workspace navigator width modestly and reduce wasted vertical padding.**
- [ ] **Step 2: Style the preview as a cinematic 16:9 hero card with inner/outer strokes.**
- [ ] **Step 3: Style metadata overlay and status chip for clear hierarchy.**
- [ ] **Step 4: Style compact app rows with selected accent rail, hover elevation, and right-side focus affordance.**
- [ ] **Step 5: Run workspace polish tests and full regression suite.**

### Task 4: Package and verify

**Files:**
- Modify: `README.md`
- Verify: installer, Bash syntax, TS/TSX syntax, all tests, Waybar isolation, ZIP integrity.

**Interfaces:**
- Produces: `adaptive-glass-ags-v1.6.zip` for live testing.

- [ ] **Step 1: Update README with v1.6 Phase 1 scope and test instructions.**
- [ ] **Step 2: Run full verification.**
- [ ] **Step 3: Package clean ZIP.**
