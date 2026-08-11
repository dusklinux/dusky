# Compact Display Hub v2.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prototype Display popup with a compact brightness-first Display Hub mapped to existing Dusky brightness, theme, wallpaper, and night-control actions.

**Architecture:** Keep the existing `DisplayPanel` backend calls and `PanelTrigger` behavior. Restructure only the JSX and Display-specific CSS: a compact heading, one thick brightness control, a direct Light/Dark segmented theme control, and two compact action rows for Wallpaper and Night controls.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, `brightnessctl`, existing Dusky shell helpers, GTK CSS.

## Global Constraints

- Do not change popup hover/click state architecture.
- Do not change Network, Audio, Workspace, Calendar, Media, Power, or Battery components.
- Keep brightness clamped to 10–100% as in the current implementation.
- Theme actions must call the existing `runTheme("light")` and `runTheme("dark")` helpers.
- Wallpaper must call `runWallpaper()`.
- Night controls must call `runNightControls()`.
- Do not add resolution, scaling, refresh-rate, monitor arrangement, or color-profile controls.
- Target a compact Display popup around 290 px wide.

---

### Task 1: Lock the Display Hub structure

**Files:**
- Create: `tests/test_v22_display_hub.py`
- Modify: `components/DisplayControl.tsx`
- Modify: `style.css`

**Interfaces:**
- Consumes: `runTheme`, `runWallpaper`, `runNightControls`, `brightnessctl` polling/setter.
- Produces: `DisplayPanel()` with brightness, Light/Dark theme controls, Wallpaper row, Night row.

- [ ] **Step 1: Write the failing contract test**

The test must assert: no `action-grid`/`action-card`; presence of `display-brightness-row`, `display-thick-slider`, `display-theme-segment`, Light/Dark buttons calling the existing helpers, compact Wallpaper/Night rows, and a Display panel width between 280 and 300 px.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.test_v22_display_hub -v`
Expected: FAIL because the v2.1.1 Display panel still uses the old action grid and generic slider.

- [ ] **Step 3: Implement the minimal Display Hub JSX and CSS**

Preserve brightness read/write logic. Replace the old three square action cards with a segmented Light/Dark control and two compact action rows. Use a thick rounded slider consistent with the Audio visual language.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m unittest tests.test_v22_display_hub -v`
Expected: PASS.

- [ ] **Step 5: Run complete regression and isolation checks**

Run all Python tests, installer smoke, capture helper, Bash syntax, TSX syntax, GTK compatibility scan, and diff isolation against v2.1.1.
