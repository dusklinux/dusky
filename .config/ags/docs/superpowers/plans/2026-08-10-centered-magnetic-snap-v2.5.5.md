# Centered Magnetic Snap v2.5.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the v2.5.4 workspace elastic animation so its temporary overshoot grows symmetrically around the pill center, peaks more clearly, and recoils/settles more slowly without changing resting dimensions or hover ownership.

**Architecture:** Preserve the existing v2.5.4 interaction state and `.expanded` layout padding. Replace padding-based keyframe overshoot with paint-time horizontal `scaleX()` keyframes, allowing the visual snap to extend left and right while the GTK layout footprint remains stable. Keep lighting synchronized with the overshoot curve.

**Tech Stack:** AGS v3, Gnim JSX, GTK4 CSS, AstalHyprland, pytest contract tests.

## Global Constraints

- Keep v2.5.4 shared rail/preview hover ownership unchanged.
- Keep resting expanded geometry at `padding: 0 7px`.
- Overshoot must be visual only and centered; it must not alter measured rail width.
- No vertical translation or scaleY animation.
- Peak overshoot should be visibly larger than resting expanded state, approximately 15–16% wider.
- Total snap duration should be approximately 400–420 ms with a slower recoil/settle than v2.5.4.
- Preserve active dot/ring, exact workspace click behavior, preview capture/focus behavior, and all non-workspace components.

---

### Task 1: Centered two-sided snap animation

**Files:**
- Modify: `style.css`
- Create: `tests/test_v255_centered_magnetic_snap.py`

**Interfaces:**
- Consumes: existing `.workspace-button.expanded` class from `components/Workspaces.tsx`.
- Produces: `@keyframes workspace-elastic-snap` using centered `scaleX(...)` only for overshoot, while `.expanded` keeps `padding: 0 7px`.

- [ ] **Step 1: Write the failing test** requiring no animated padding, a peak `scaleX` of at least 1.15, a recoil keyframe, final `scaleX(1)`, and a 400–420 ms animation duration.
- [ ] **Step 2: Run `pytest -q tests/test_v255_centered_magnetic_snap.py` and confirm RED against v2.5.4 behavior.**
- [ ] **Step 3: Replace padding keyframes with centered horizontal scale keyframes while keeping resting `.expanded` padding unchanged.**
- [ ] **Step 4: Run the focused test and confirm GREEN.**
- [ ] **Step 5: Run the full regression suite and update only assertions that intentionally encode v2.5.4's 260 ms padding overshoot.**

### Task 2: Package and runtime-safety verification

**Files:**
- Modify: `README.md`
- Modify: `install.sh`
- Verify: full project and extracted ZIP

**Interfaces:**
- Consumes: v2.5.5 runtime files.
- Produces: `/mnt/data/adaptive-glass-ags-v2.5.5.zip`.

- [ ] **Step 1: Update version metadata to v2.5.5.**
- [ ] **Step 2: Run full pytest, TS/TSX syntax, Bash syntax, installer smoke, capture helper, GTK/Gnim compatibility and Waybar-isolation checks.**
- [ ] **Step 3: Verify diff isolation against v2.5.4.**
- [ ] **Step 4: Package ZIP, extract it fresh, rerun critical tests, and verify ZIP integrity.**
