# v2.5.2 Runtime and Geometry Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct v2.5.1's AGS startup crash, restore the workspace rail, remove hover geometry snapping, and make the clock content-sized.

**Architecture:** Keep the existing monitor-reactive `<For>` lifecycle but ensure there is exactly one application-level `<This>` per monitor. Correct GTK4 Overlay child typing for the workspace selection plate. Apply a late CSS corrective layer so hover feedback changes paint only, never widget geometry.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, Astal, AstalHyprland, CSS.

## Global Constraints

- Preserve all v2.5.1 media, power, network, audio, display, calendar and window-focus behavior.
- Preserve the moving workspace selection plate.
- Hover feedback must not translate or resize top-bar pills or workspace tiles.
- Clock width must follow content instead of a fixed minimum width.
- Do not modify Waybar fallback files.

---

### Task 1: Remove nested application parent

**Files:**
- Modify: `app.tsx`
- Modify: `components/PopupWindows.tsx`
- Modify: `components/Bar.tsx`
- Modify: `components/PopupWindow.tsx`
- Test: `tests/test_v252_runtime_geometry.py`

**Interfaces:**
- Consumes: `app`, `For`, `This`, monitor binding.
- Produces: one `This this={app}` ownership boundary per monitor containing bar and popup windows.

- [ ] Write a failing contract forbidding nested application `This` ownership.
- [ ] Run the focused test and confirm RED.
- [ ] Move `Bar` into the single monitor-level `This` returned by `PopupWindows` and remove duplicate `application={app}` registration.
- [ ] Run focused test and confirm GREEN.

### Task 2: Restore workspace overlay geometry

**Files:**
- Modify: `components/Workspaces.tsx`
- Test: `tests/test_v252_runtime_geometry.py`

**Interfaces:**
- Consumes: existing `workspace-deck` main child and `workspace-selection-plate` state.
- Produces: a measured five-button deck with the moving plate as `$type="overlay"`.

- [ ] Write a failing contract requiring `$type="overlay"` on the plate.
- [ ] Confirm RED.
- [ ] Add GTK4 overlay child typing without changing selection state logic.
- [ ] Confirm GREEN.

### Task 3: Remove hover geometry movement and compact clock

**Files:**
- Modify: `style.css`
- Test: `tests/test_v252_runtime_geometry.py`

**Interfaces:**
- Consumes: v2.5.1 top-bar and preview classes.
- Produces: paint-only hover feedback and content-sized clock geometry.

- [ ] Write failing assertions for no hover `translateY` in final corrective rules and no fixed clock min-width.
- [ ] Confirm RED.
- [ ] Add final v2.5.2 CSS overrides using stable shadow geometry, `transform: none`, and compact clock padding/min-width.
- [ ] Run focused/full suite and package verification.
