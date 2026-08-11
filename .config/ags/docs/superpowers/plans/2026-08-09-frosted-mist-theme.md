# Frosted Mist Light Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Light/Dark toggle physically track its state and give Adaptive Glass a layered Frosted Mist light appearance instead of near-white surfaces.

**Architecture:** Replace the custom movable-knob button with a native `Gtk.Switch` bound to the existing persistent `themeMode` state. Keep `setAdaptiveTheme()` as the only mutation path. Rewrite only `.theme-light` neutral surface rules so all shell and popup surfaces use cool translucent mist shades while Matugen remains responsible for accents.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, TypeScript, GTK CSS, existing Dusky theme scripts, Matugen palette.

## Global Constraints

- Dark mode appearance remains unchanged.
- Light is switch off/left; Dark is switch on/right.
- No separate Light/Dark buttons.
- No custom knob alignment logic.
- Matugen accents remain dynamic.
- Existing brightness, wallpaper, night, network, audio, workspace, calendar, media, power, and popup-state behavior remain unchanged.

---

### Task 1: Native theme switch

**Files:**
- Modify: `components/DisplayControl.tsx`
- Modify: `style.css`
- Test: `tests/test_v222_frosted_mist.py`

**Interfaces:**
- Consumes: `themeMode(): "light" | "dark"`, `setAdaptiveTheme(mode)`
- Produces: one `Gtk.Switch` with `active = dark`, so GTK owns knob position.

- [ ] **Step 1: Write the failing contract test** requiring `Gtk.Switch`, reactive `active`, `onStateSet`, no `.display-theme-knob`, and Frosted Mist palette markers.
- [ ] **Step 2: Run the focused test and verify it fails against v2.2.1 behavior.**
- [ ] **Step 3: Replace the custom button/track/knob with the native switch and keep the existing theme backend call.**
- [ ] **Step 4: Replace near-white `.theme-light` fills with cool mist/pearl layers and style GTK switch checked/unchecked/hover states.**
- [ ] **Step 5: Run the focused test and verify it passes.**

### Task 2: Regression and packaging

**Files:**
- Modify only obsolete tests if they encode the superseded custom toggle internals.
- Package: `/mnt/data/adaptive-glass-ags-v2.2.2.zip`

**Interfaces:**
- Consumes: v2.2.2 runtime tree.
- Produces: installable isolated artifact.

- [ ] **Step 1: Run the complete Python contract suite.**
- [ ] **Step 2: Run installer smoke and capture-helper tests.**
- [ ] **Step 3: Run Bash syntax, TS/TSX syntax, GTK CSS compatibility, and runtime isolation checks.**
- [ ] **Step 4: Remove generated caches and package the clean artifact.**
- [ ] **Step 5: Verify ZIP integrity and report exact evidence.**
