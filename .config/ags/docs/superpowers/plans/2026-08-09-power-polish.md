# Power Control Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the power-profile slider with a compact segmented selector and reduce the visual weight of the Caffeine switch without changing the Power Deck/session backends.

**Architecture:** Keep AstalPowerProfiles as the source of truth and render only profiles exposed by the machine. Each segment directly calls `set_active_profile`; Caffeine continues using the existing native `Gtk.Switch` and Hypridle state but with smaller CSS geometry.

**Tech Stack:** AGS v3, GTK4/Gnim JSX, AstalPowerProfiles, existing Adaptive Glass CSS.

## Global Constraints

- Preserve the 2×3 Power Deck and all Dusky session mappings.
- Preserve Dark and Frosted Mist themes.
- Remove the continuous profile slider entirely.
- Use compact segmented buttons with a clear active state.
- Make the Caffeine switch visibly smaller than v2.3.1.

---

### Task 1: Power profile segmented selector

**Files:**
- Modify: `components/PowerControl.tsx`
- Modify: `style.css`
- Test: `tests/test_v232_power_polish.py`

**Interfaces:**
- Consumes: `availableProfileNames()`, `AstalPowerProfiles.activeProfile`, `set_active_profile(profile)`.
- Produces: `.power-profile-segments`, `.power-profile-segment.active`, smaller `.power-caffeine-switch`.

- [ ] **Step 1: Write the failing test** requiring no `power-profile-rail`, segmented controls, active-state binding, and smaller Caffeine geometry.
- [ ] **Step 2: Run the focused test and confirm RED.**
- [ ] **Step 3: Replace the slider/labels with compact profile segments and shrink Caffeine CSS.**
- [ ] **Step 4: Run focused and full regression tests and confirm GREEN.**
- [ ] **Step 5: Package and verify the isolated v2.3.2 artifact.**
