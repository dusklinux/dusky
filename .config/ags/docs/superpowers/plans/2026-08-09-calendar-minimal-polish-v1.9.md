# Minimal Clock + Calendar Polish v1.9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the existing Clock + Calendar popup to match the approved minimal floating-calendar concept while fixing the workspace-preview header icon centering.

**Architecture:** Preserve the existing dedicated AGS popup-window state machine and clock trigger. Reuse `Gtk.Calendar` for month navigation and date selection, adding only a compact full-date header and Today/Clocks actions. Replace the font-glyph workspace header icon with a centered symbolic image so the alignment fix does not affect workspace behavior.

**Tech Stack:** AGS v3, Gnim JSX, GTK4 `Gtk.Calendar`, GLib DateTime, Matugen-generated GTK CSS.

## Global Constraints

- Do not change popup hover/click behavior, workspace switching, real window capture, media, network, audio, display, or power behavior.
- Calendar remains a dedicated AGS popup window anchored under the center clock.
- Bottom actions are exactly `Today` and `Clocks`; no Terminal or Peaclock action in this calendar UI.
- Use the existing Matugen palette and Adaptive Glass visual language.
- Workspace preview header icon fix is visual only.

---

### Task 1: Lock calendar and icon visual contract

**Files:**
- Create: `tests/test_v19_calendar_polish.py`
- Test: `tests/test_v19_calendar_polish.py`

**Interfaces:**
- Consumes: existing `CalendarPanel`, `ClockCard`, workspace preview header.
- Produces: regression expectations for v1.9 visual structure.

- [ ] **Step 1: Write failing tests** asserting full-date header, Today + Clocks actions, absence of Peaclock/Terminal from `ClockCard.tsx`, compact calendar CSS selectors, and symbolic workspace grid icon.
- [ ] **Step 2: Run only the new test file** and confirm it fails against copied v1.8.
- [ ] **Step 3: Keep the failing output as the RED evidence before production changes.**

### Task 2: Implement minimal floating calendar

**Files:**
- Modify: `components/ClockCard.tsx`
- Modify: `style.css`

**Interfaces:**
- Consumes: `openClocks()`, `Gtk.Calendar`, `GLib.DateTime`, existing `PanelTrigger`.
- Produces: compact calendar with current full-date header, native month navigation, Today reset action, Clocks action.

- [ ] **Step 1: Acquire the `Gtk.Calendar` instance using Gnim's `$` setup function.**
- [ ] **Step 2: Add a compact date header using `GLib.DateTime.new_now_local()` and remove the old CALENDAR kicker/title hierarchy.**
- [ ] **Step 3: Add `Today` to reset the calendar to the current date and retain `Clocks` to open GNOME Clocks.**
- [ ] **Step 4: Remove the Peaclock action from the calendar popup.**
- [ ] **Step 5: Add v1.9 CSS overrides for compact width, darker glass, refined native Gtk.Calendar header/day grid, Matugen today/selected state, and minimal footer actions.**
- [ ] **Step 6: Run `tests/test_v19_calendar_polish.py` and confirm GREEN.**

### Task 3: Center workspace preview header icon

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`

**Interfaces:**
- Consumes: existing workspace header icon shell.
- Produces: centered symbolic grid icon without altering workspace state or interaction.

- [ ] **Step 1: Replace the Nerd Font label glyph with a GTK symbolic image.**
- [ ] **Step 2: Center the image explicitly inside the fixed square shell and add a dedicated glyph class.**
- [ ] **Step 3: Run v1.9 contract tests.**

### Task 4: Regression and package verification

**Files:**
- Modify: `README.md`
- Package: `/mnt/data/adaptive-glass-ags-v1.9.zip`

**Interfaces:**
- Consumes: completed v1.9 source tree.
- Produces: installable verified package.

- [ ] **Step 1: Run all Python regression tests.**
- [ ] **Step 2: Run Bash syntax and installer smoke tests.**
- [ ] **Step 3: Parse all TS/TSX files for syntax.**
- [ ] **Step 4: Verify workspace/popup/capture state files are unchanged from v1.8.**
- [ ] **Step 5: Update README with v1.9 scope and install/restart commands.**
- [ ] **Step 6: Build ZIP and test archive integrity.**
