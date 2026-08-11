# Two-Stage Magnetic Pulse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workspace expansion visibly distinct from its magnetic overshoot by delaying a centered inner-layer pulse until after the normal elastic width has nearly settled.

**Architecture:** Preserve v2.5.5 workspace ownership and resting geometry. The outer `.workspace-button` continues to own layout expansion (`padding: 0 7px`, 190 ms). A new transient `workspaceSnapId` state starts after a 170 ms delay when interaction ownership transfers, and an inner `.workspace-snap-surface` performs a centered 480 ms scaleX pulse (1.00 → 1.22 → 0.95 → 1.00) without changing measured rail width.

**Tech Stack:** AGS v3, GTK4/Gnim JSX, GLib timers, AstalHyprland, GTK CSS animations.

## Global Constraints

- Preserve v2.5.5 rail→preview shared interaction ownership.
- Preserve resting expanded geometry exactly: `.workspace-button.expanded { padding: 0 7px; }`.
- Do not animate layout padding in the magnetic pulse.
- Expansion phase remains approximately 190 ms; pulse starts after 170 ms.
- Magnetic pulse is centered horizontally and uses no vertical transform.
- Pulse profile: 1.00 → 1.22 → 0.95 → 1.00 over 480 ms.
- Preserve active dot + ring semantics and total rail footprint.
- Do not modify media, network, audio, display, power, calendar, preview capture, or exact-window focus behavior.

---

### Task 1: Transient delayed snap state

**Files:**
- Modify: `lib/workspaceInteractionState.ts`
- Test: `tests/test_v256_two_stage_snap.py`

**Interfaces:**
- Produces: `workspaceSnapId(): number | null` and delayed snap scheduling from `claimWorkspaceInteraction(id)`.
- Timing constants: `SNAP_DELAY_MS = 170`, `SNAP_PULSE_MS = 480`.

- [ ] **Step 1: Write the failing test** requiring a separate `workspaceSnapId`, delayed timer, and pulse-clear timer.
- [ ] **Step 2: Run the focused test and confirm RED against v2.5.5.**
- [ ] **Step 3: Implement delayed snap scheduling and cancellation without changing shared hover ownership.**
- [ ] **Step 4: Run the focused state test and confirm GREEN.**

### Task 2: Separate layout expansion from paint-time pulse

**Files:**
- Modify: `components/Workspaces.tsx`
- Modify: `style.css`
- Test: `tests/test_v256_two_stage_snap.py`

**Interfaces:**
- Consumes: `workspaceSnapId()` from Task 1.
- Produces: `.workspace-snap-surface.snapping` on the inner visual layer only.

- [ ] **Step 1: Extend the failing contract** to require `workspace-snap-surface`, no animation on `.workspace-button.expanded`, and the new 480 ms keyframe.
- [ ] **Step 2: Confirm RED.**
- [ ] **Step 3: Wrap existing number/dot content in the snap surface and bind `snapping` only to the transient snap ID.**
- [ ] **Step 4: Move keyframe animation to the inner surface with 1.22 peak, 0.95 recoil, and 480 ms duration.**
- [ ] **Step 5: Run focused tests and confirm GREEN.**

### Task 3: Full regression and artifact verification

**Files:**
- Modify: version metadata/tests only where old v2.5.5 snap assertions are superseded.

**Interfaces:**
- Produces: installable `adaptive-glass-ags-v2.5.6.zip`.

- [ ] **Step 1: Run full pytest suite and update only obsolete snap assertions.**
- [ ] **Step 2: Run TS/TSX syntax, installer smoke, capture helper, GTK/Gnim compatibility and Waybar isolation checks.**
- [ ] **Step 3: Compare runtime diff against v2.5.5 and reject unrelated changes.**
- [ ] **Step 4: Package ZIP, extract it fresh, rerun critical tests, and verify ZIP integrity.**
