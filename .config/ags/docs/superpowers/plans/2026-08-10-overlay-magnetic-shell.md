# Overlay Magnetic Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the compact v2.5.5 workspace rail geometry while preserving delayed magnetic snap timing by moving the pulse onto a non-sizing Gtk.Overlay child.

**Architecture:** The workspace button remains the sole owner of measured width and uses the existing `expanded` padding transition. Its child becomes a `Gtk.Overlay`: the centered number/dot content is the main sizing child, while a non-targetable `$type="overlay"` magnetic shell is drawn above it and bound to `workspaceSnapId`. The existing delayed snap state in `workspaceInteractionState.ts` remains responsible for triggering the pulse after the elastic width settles.

**Tech Stack:** AGS v3/Gnim TSX, GTK4, AstalHyprland, GTK CSS animations, Python pytest regression contracts.

## Global Constraints

- Keep the v2.5.4/v2.5.5 resting workspace geometry: `min-width: 27px`, `padding: 0`, and `padding: 0 7px` for `.expanded`.
- Preserve rail-to-preview shared hover ownership and exact workspace/window focus behavior.
- The magnetic effect must not participate in GTK measurement and must not request horizontal expansion.
- No vertical translate/bounce.
- Preserve Waybar isolation and all unrelated panels/components.
- Pulse timing remains delayed after expansion; target visible profile is ~1.20 overshoot, ~0.96 recoil, then 1.00 settle.

---

### Task 1: Add a regression contract for non-sizing overlay geometry

**Files:**
- Create: `tests/test_v257_overlay_magnetic_shell.py`
- Test: `components/Workspaces.tsx`
- Test: `style.css`

**Interfaces:**
- Consumes: existing `workspaceSnapId()` transient state.
- Produces: a test contract requiring `Gtk.Overlay`, `$type="overlay"`, `canTarget={false}`, no `hexpand={true}` on the magnetic shell, and stable button geometry.

- [ ] **Step 1: Write the failing test** requiring an overlay shell instead of the v2.5.6 fill child.
- [ ] **Step 2: Run `pytest -q tests/test_v257_overlay_magnetic_shell.py`** and verify it fails against the copied v2.5.6 structure.
- [ ] **Step 3: Do not alter production code until the RED failure is confirmed.**

### Task 2: Replace the fill-sized snap child with a Gtk.Overlay decorative shell

**Files:**
- Modify: `components/Workspaces.tsx`
- Modify: `style.css`
- Test: `tests/test_v257_overlay_magnetic_shell.py`

**Interfaces:**
- Consumes: `workspaceSnapId`, `buttonClass`, active/inactive bindings.
- Produces: a button child `<overlay>` with the normal content as its main child and a non-targetable `.workspace-magnetic-shell` as `$type="overlay"`.

- [ ] **Step 1: Make the main workspace content the overlay's sizing child.**
- [ ] **Step 2: Add the decorative magnetic shell as `$type="overlay"`, `canTarget={false}`, centered/fill allocated, with no expansion request.**
- [ ] **Step 3: Move pulse CSS from `.workspace-snap-surface` to `.workspace-magnetic-shell` and keep real button padding unchanged.**
- [ ] **Step 4: Run the focused test until GREEN.**

### Task 3: Retire only superseded v2.5.6 structural assertions

**Files:**
- Modify: `tests/test_v256_two_stage_snap.py`
- Modify if needed: `tests/test_v255_centered_magnetic_snap.py`

**Interfaces:**
- Consumes: v2.5.7 overlay shell selectors and same delayed snap state.
- Produces: historical tests that continue checking delayed pulse, horizontal overshoot/recoil, no vertical bounce, and unchanged resting width without requiring the broken fill child.

- [ ] **Step 1: Run the full suite and identify only structural assertion failures caused by the approved overlay replacement.**
- [ ] **Step 2: Update those assertions without weakening behavioral coverage.**
- [ ] **Step 3: Re-run the full suite and require zero failures.**

### Task 4: Version, package, and verify the extracted artifact

**Files:**
- Modify: `README.md`
- Modify: `install.sh`
- Create: `/mnt/data/adaptive-glass-ags-v2.5.7.zip`

**Interfaces:**
- Produces: installable v2.5.7 artifact preserving the v2.5.6 feature set except for the repaired workspace pulse structure.

- [ ] **Step 1: Update visible version metadata to v2.5.7.**
- [ ] **Step 2: Run full pytest suite, TS/TSX syntax checks, installer smoke, capture helper, GTK/Gnim guards, and Waybar isolation.**
- [ ] **Step 3: Compare runtime files against v2.5.6 and confirm only expected workspace/version/test/docs files changed.**
- [ ] **Step 4: Build the ZIP, extract it into a fresh directory, and rerun the critical/full tests against the extracted copy.**
