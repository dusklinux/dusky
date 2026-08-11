# Power Deck v2.3.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the v2.3 Power Hub into a sophisticated compact Power Deck with non-redundant power profiles, native-feeling controls, Logout and Dusky Soft Reboot, and a 2×3 action layout.

**Architecture:** `PowerControl.tsx` remains the single visual component and keeps AstalBattery/AstalPowerProfiles bindings. The profile selector becomes a discrete native slider rail, Caffeine becomes a real `Gtk.Switch`, and session actions become six compact tiles. `lib/dusky.ts` maps Logout, Soft reboot, Reboot, and Power off to Dusky's existing `user_scripts/wlogout/dusky_session.sh` backend so graceful teardown semantics are preserved.

**Tech Stack:** AGS v3, Gnim TSX, GTK4, AstalBattery, AstalPowerProfiles, Hyprland/Dusky shell helpers, pytest contract tests.

## Global Constraints

- Preserve all non-Power components and popup-state behavior.
- Remove duplicated active power-profile status text; the rail position is the status.
- Power profile rail exposes only profiles reported by AstalPowerProfiles.
- Caffeine uses a native `Gtk.Switch`; Caffeine ON still means Hypridle is not running.
- Session deck order: Lock, Sleep, Logout / Soft reboot, Reboot, Power off.
- Soft reboot calls `~/user_scripts/wlogout/dusky_session.sh soft-reboot`.
- Logout, Reboot, and Power off use the same Dusky session helper with their corresponding actions.
- Soft reboot, Reboot, and Power off require inline confirmation.
- Preserve Dark and Frosted Mist theme support.

---

### Task 1: Lock the v2.3.1 Power Deck contract

**Files:**
- Create: `tests/test_v231_power_deck.py`
- Modify later: `components/PowerControl.tsx`, `lib/dusky.ts`, `style.css`

**Interfaces:**
- Consumes: v2.3 Power Hub structure.
- Produces: regression contract for the approved Power Deck.

- [ ] **Step 1: Write failing tests** requiring no `.power-profile-current`, a discrete profile rail, `Gtk.Switch` for Caffeine, six action tiles in a two-row deck, Logout + Soft reboot helpers, and inline confirmation for Soft reboot/Reboot/Power off.
- [ ] **Step 2: Run `pytest -q tests/test_v231_power_deck.py` and verify RED.**
- [ ] **Step 3: Do not change runtime code until the failure is confirmed.**

### Task 2: Map Dusky session actions

**Files:**
- Modify: `lib/dusky.ts`

**Interfaces:**
- Produces: `logoutSession()`, `softRebootSession()`, `restartSession()`, `shutdownSession()` backed by `dusky_session.sh`.

- [ ] **Step 1:** Replace bare logout/reboot/poweroff commands with the existing Dusky session helper.
- [ ] **Step 2:** Add `softRebootSession()` using the `soft-reboot` action.
- [ ] **Step 3:** Run the focused contract.

### Task 3: Build the Power Deck UI

**Files:**
- Modify: `components/PowerControl.tsx`

**Interfaces:**
- Consumes: Astal battery/profile bindings, Caffeine state, session helpers.
- Produces: refined profile rail, native Caffeine switch, 2×3 command deck, inline confirmations.

- [ ] **Step 1:** Remove the duplicated active-profile label and old profile buttons.
- [ ] **Step 2:** Add a discrete slider rail whose thumb snaps to available Saver/Balanced/Boost positions and updates AstalPowerProfiles.
- [ ] **Step 3:** Replace the Caffeine row's text-only state with a `Gtk.Switch` synchronized to Hypridle state.
- [ ] **Step 4:** Replace vertical session rows with a two-row, three-column tile deck: Lock/Sleep/Logout and Soft reboot/Reboot/Power off.
- [ ] **Step 5:** Extend inline confirmation to Soft reboot as well as Reboot and Power off.
- [ ] **Step 6:** Run the focused contract.

### Task 4: Rice Dark and Frosted Mist Power Deck styling

**Files:**
- Modify: `style.css`

**Interfaces:**
- Produces: native-looking profile rail, polished switch, compact action tiles, hover/pressed/confirmation states in both themes.

- [ ] **Step 1:** Style the profile rail as a thick rounded three-stop track with clear labels and a prominent thumb.
- [ ] **Step 2:** Style Caffeine switch with restrained amber ON treatment and neutral OFF state.
- [ ] **Step 3:** Style 2×3 action tiles with layered glass, icon hierarchy, hover lift/glow, press feedback, and restrained danger accents.
- [ ] **Step 4:** Add Frosted Mist equivalents without plain-white surfaces.
- [ ] **Step 5:** Run focused and full regression tests.

### Task 5: Package and verify

**Files:**
- Modify: `README.md`, `install.sh`
- Create: `/mnt/data/adaptive-glass-ags-v2.3.1.zip`

**Interfaces:**
- Produces: installable v2.3.1 artifact.

- [ ] **Step 1:** Update version copy.
- [ ] **Step 2:** Run all pytest contracts, installer smoke, Bash syntax, TS/TSX syntax, GTK/CSS compatibility, and runtime isolation checks.
- [ ] **Step 3:** Remove test caches and build ZIP.
- [ ] **Step 4:** Verify ZIP integrity and rerun critical checks against packaged contents.
