# Adaptive Glass v2.5.1 Micro-Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Refine the v2.5 bar with a true sliding workspace selection plate, softer/lighter workspace preview, stronger clock hierarchy, and unified top-bar pill geometry.

**Architecture:** Keep all existing service/state backends intact and limit behavior changes to workspace visual targeting. The workspace strip becomes an overlay with one reactive selection plate whose position follows the hovered preview workspace and then the focused workspace; the other three tasks are styling/micro-layout changes layered at the end of `style.css` so older visual contracts remain easy to supersede without touching panel logic.

**Tech Stack:** AGS v3, Gnim JSX, GTK4/Astal widgets, Hyprland/AstalHyprland bindings, Matugen GTK color variables, pytest contract tests.

## Global Constraints

- Preserve v2.4.1 media source behavior and all v2.5 functionality.
- Preserve exact workspace switching and exact-window focus behavior.
- Preserve the 390×219 live workspace preview capture area.
- Do not reintroduce nested reactive Fragments in WorkspacePreview.
- Keep dark and Frosted Mist light themes visually coherent.
- Do not modify Waybar fallback files.
- Avoid unsupported web-only GTK CSS properties.

---

### Task 1: Sliding Workspace Selection Plate

**Files:**
- Modify: `components/Workspaces.tsx`
- Modify: `style.css`
- Test: `tests/test_v251_micro_polish.py`

**Interfaces:**
- Consumes: `focusedWorkspace.id`, `activePanel()`, `previewWorkspaceLocalId()`.
- Produces: `.workspace-switcher`, `.workspace-selection-plate.position-{1..5}` and transparent active/preview workspace buttons above the moving plate.

- [x] **Step 1: Write a failing test** that requires one overlay selection plate, five position classes, hover-driven target selection, and no per-button active fill/glow.
- [x] **Step 2: Run the focused test and verify RED** because v2.5 renders only individual button active backgrounds.
- [x] **Step 3: Implement the overlay and reactive plate class** while retaining existing preview hover and click-to-focus callbacks.
- [x] **Step 4: Add restrained 220 ms transform motion** and transparent active/preview button states.
- [x] **Step 5: Run the focused test and verify GREEN.**

### Task 2: Final Workspace Preview Refinement

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`
- Test: `tests/test_v251_micro_polish.py`

**Interfaces:**
- Consumes: existing preview state and `WindowTile` hover/click behavior.
- Produces: softer `.popup-workspace`, larger icon tiles, more readable `WINDOWS` heading, unchanged 390×219 hero.

- [x] **Step 1: Extend the failing contract** for softer popup border/shadow, slightly lighter glass, 30–32 px window icons, and 10 px `WINDOWS` heading.
- [x] **Step 2: Verify RED.**
- [x] **Step 3: Increase WindowTile icon size and tile breathing room without adding persistent text.**
- [x] **Step 4: Add final popup/hero/light-theme overrides with reduced edge contrast and shadow.**
- [x] **Step 5: Verify GREEN and confirm exact-window focus calls are unchanged.**

### Task 3: Stronger Clock Treatment

**Files:**
- Modify: `components/ClockCard.tsx`
- Modify: `style.css`
- Test: `tests/test_v251_micro_polish.py`

**Interfaces:**
- Consumes: existing `%I:%M` and `%p` polls plus `PanelTrigger panel="calendar"`.
- Produces: same click/hover calendar behavior with stronger numeric/meridiem hierarchy and unified pill geometry.

- [x] **Step 1: Extend contract** for larger time digits, stronger meridiem, accent marker, and normalized 28 px geometry.
- [x] **Step 2: Verify RED.**
- [x] **Step 3: Refine clock CSS only; retain CalendarPanel and trigger semantics.**
- [x] **Step 4: Verify GREEN.**

### Task 4: Unified Top-Bar Pill Geometry

**Files:**
- Modify: `components/MediaCard.tsx`
- Modify: `style.css`
- Test: `tests/test_v251_micro_polish.py`

**Interfaces:**
- Consumes: existing launcher/weather/notification/control/battery/media classes.
- Produces: a shared 28 px height, 10 px radius, restrained outline/shadow language across all visible top-bar pills while preserving semantic colors.

- [x] **Step 1: Extend contract** for the shared selector block and a compact 24 px media artwork footprint.
- [x] **Step 2: Verify RED.**
- [x] **Step 3: Add final shared geometry overrides and reduce media artwork to 24 px without changing MPRIS behavior.**
- [x] **Step 4: Run focused and full historical suites.**
- [x] **Step 5: Run TypeScript syntax, installer, capture, GTK CSS, runtime-isolation, ZIP-extraction and archive-integrity checks.**
