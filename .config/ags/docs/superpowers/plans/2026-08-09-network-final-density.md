# Network Final Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the approved Network Dashboard materially smaller by removing layout waste while preserving readable typography, icons, network behavior, and session-data logic.

**Architecture:** Keep `networkSession.ts` and all popup state untouched. Change only `NetworkControl.tsx` layout geometry and Network-specific CSS: replace homogeneous three-column metric layouts with two flexible metric columns separated by a true thin `Gtk.Separator`, then tighten shell/card/action spacing.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, AstalNetwork/AstalBluetooth, GTK CSS.

## Global Constraints

- Do not change Network session measurement or persistence logic.
- Do not change popup hover/click behavior.
- Keep current icon and principal text sizes readable.
- SSID remains displayed once only.
- Target panel width is approximately 295 px.
- Center dividers must consume only divider width, never one-third of a homogeneous row.

---

### Task 1: Remove homogeneous metric-layout waste

**Files:**
- Modify: `components/NetworkControl.tsx`
- Test: `tests/test_v203_network_final_density.py`

**Interfaces:**
- Consumes: existing `networkSession`, `formatRate`, and `formatBytes` bindings.
- Produces: two `hexpand` metric columns around vertical `Gtk.Separator` widgets.

- [ ] Write a failing static contract test that rejects `homogeneous` on `network-live-grid` and `network-session-breakdown`, and requires `hexpand` metric columns plus vertical separator classes.
- [ ] Run the test against v2.0.2 and verify it fails on homogeneous layout.
- [ ] Remove `homogeneous`, set metric/stat containers to `hexpand`, and add vertical divider classes.
- [ ] Run the focused test and verify it passes.

### Task 2: Compact Network geometry without shrinking typography

**Files:**
- Modify: `style.css`
- Modify: `components/NetworkControl.tsx`
- Test: `tests/test_v203_network_final_density.py`

**Interfaces:**
- Consumes: the Task 1 layout structure.
- Produces: ~295 px shell, 48 px live card, compact session card, and ~34 px action rows.

- [ ] Add failing assertions for `min-width: 295px`, `padding: 9px`, `min-height: 48px`, `min-height: 34px`, and tighter component spacing.
- [ ] Run the focused test and verify it fails before CSS changes.
- [ ] Apply only Network-specific density CSS and JSX spacing changes.
- [ ] Verify the focused test passes.

### Task 3: Regression and package verification

**Files:**
- Modify: `README.md`
- Verify: entire artifact

**Interfaces:**
- Consumes: completed Network layout/style changes.
- Produces: installable `adaptive-glass-ags-v2.0.3.zip`.

- [ ] Run all Python regression tests.
- [ ] Run installer and capture-helper smoke tests.
- [ ] Parse/transpile every TS/TSX file and run GTK compatibility scans.
- [ ] Confirm Network data/session files and popup state files are unchanged from v2.0.2.
- [ ] Build ZIP and verify archive integrity.
