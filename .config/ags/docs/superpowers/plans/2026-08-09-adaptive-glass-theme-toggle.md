# Adaptive Glass Theme Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Display Light/Dark buttons with one persistent toggle and make the full AGS shell visibly switch between dark and light appearances.

**Architecture:** Add one reactive/persistent theme state module. Bar and popup windows expose `theme-light` or `theme-dark` classes from that shared state. The Display toggle updates the shared state immediately, persists it, and delegates the system theme change to the existing Dusky theme controller. Light-mode CSS is scoped under `.theme-light`, preserving Matugen accents while overriding glass surfaces/text for a genuine frosted-light shell.

**Tech Stack:** AGS v3, Gnim state, GTK4 CSS, GJS/GLib, existing Dusky theme script.

## Global Constraints

- Do not change Brightness, Wallpaper, Night controls, Network, Audio, Workspace, Calendar, Media, Power, or popup interaction behavior.
- Preserve the existing Dusky theme backend (`theme_ctl.sh`).
- Persist Adaptive Glass theme mode across AGS restarts.
- Dark remains the default if no saved Adaptive Glass mode exists.
- Matugen continues to provide accent colors.

---

### Task 1: Theme state and persistence

**Files:**
- Create: `lib/themeState.ts`
- Test: `tests/test_v221_theme_toggle.py`

**Interfaces:**
- Produces: `themeMode`, `setAdaptiveTheme(mode)`, and persisted mode at `~/.config/dusky/settings/ags/adaptive-glass-theme`.

- [ ] Write a static contract test requiring a shared `createState`, persistent state path, and call to existing `runTheme`.
- [ ] Run the test and verify RED.
- [ ] Implement `themeState.ts` with load/persist/update behavior.
- [ ] Run the focused test and verify GREEN.

### Task 2: Root theme classes and toggle UI

**Files:**
- Modify: `components/Bar.tsx`
- Modify: `components/PopupWindow.tsx`
- Modify: `components/DisplayControl.tsx`
- Test: `tests/test_v221_theme_toggle.py`

**Interfaces:**
- Consumes: `themeMode`, `setAdaptiveTheme`.
- Produces: reactive `theme-light`/`theme-dark` classes and one compact toggle button.

- [ ] Extend the focused test to require root theme classes and removal of the two old Light/Dark choice buttons.
- [ ] Verify RED.
- [ ] Add reactive root classes and the compact toggle control.
- [ ] Verify GREEN.

### Task 3: Genuine light appearance

**Files:**
- Modify: `style.css`
- Test: `tests/test_v221_theme_toggle.py`

**Interfaces:**
- Consumes: `.theme-light` root class.
- Produces: frosted light bar/popups and readable dark text while preserving Matugen accent roles.

- [ ] Extend the focused test to require scoped light-mode rules for the bar, popup frame, module surfaces, and common text/action surfaces.
- [ ] Verify RED.
- [ ] Add scoped `.theme-light` rules without changing the dark rules.
- [ ] Verify GREEN.

### Task 4: Regression and package verification

**Files:**
- Modify: `README.md`

- [ ] Run all Python contract tests.
- [ ] Run installer/capture smoke tests and Bash syntax.
- [ ] Parse all TS/TSX files with TypeScript.
- [ ] Scan GTK CSS compatibility.
- [ ] Verify runtime diff scope.
- [ ] Package ZIP and verify integrity.
