# Adaptive Glass v2.8 Status And Motion Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken clock reel and popup edge expansion, then add status-aware workspace, Wi-Fi, power, and battery polish.

**Architecture:** Keep behavior inside existing AGS components and append final v2.8 CSS overrides at the end of `style.css`. Use helper functions in each component to map live state into stable GTK class names, and protect runtime CSS compatibility with tests.

**Tech Stack:** AGS v3, GTK4, AstalHyprland, AstalNetwork, AstalBattery, Python pytest guard tests, shell smoke tests.

## Global Constraints

- Do not change Waybar config or the bar switch contract.
- Do not add new runtime dependencies.
- GTK CSS must not use unsupported web properties such as `overflow`, `max-width`, `display`, or percentage-based `translate`.
- Top-bar controls must keep stable dimensions and avoid layout-shifting hover states.
- Motion can use opacity and pixel translate, but popup frames must not scale sideways.

---

### Task 1: Write v2.8 Red Tests

**Files:**
- Create: `.config/ags/tests/test_v28_status_motion_corrections.py`

**Interfaces:**
- Consumes: existing component text and CSS parser helpers.
- Produces: failing tests that define the expected v2.8 behavior.

- [ ] **Step 1: Write failing tests**

Create tests for:

```python
def test_clock_reel_uses_previous_and_current_faces_with_tick_classes():
    tsx = read("components/ClockCard.tsx")
    assert "previousTime" in tsx
    assert "clock-reel-old" in tsx
    assert "clock-reel-new" in tsx
    assert "clock-reel-tick-even" in tsx
    assert "clock-reel-tick-odd" in tsx
```

```python
def test_popup_reveal_has_no_scale_or_transform_transition():
    css = read("style.css")
    assert "scale(" not in keyframes(css, "popup-v28-stable-reveal")
    assert "transform" not in css_block(css, ".popup-window-frame")
```

```python
def test_workspace_active_indicator_is_pacman_and_color_indexed():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")
    assert "workspace-pacman" in tsx
    assert "workspace-id-${id}" in tsx
    assert ".workspace-button.workspace-id-1.active .workspace-pacman" in css
```

```python
def test_network_icon_uses_signal_strength_classes():
    tsx = read("components/NetworkControl.tsx")
    css = read("style.css")
    assert "wifiSignalClass" in tsx
    assert "wifi-signal-weak" in css
    assert "wifi-signal-strong" in css
```

```python
def test_power_trigger_uses_centered_icon_shell():
    tsx = read("components/PowerControl.tsx")
    css = read("style.css")
    assert "power-trigger-shell" in tsx
    assert "power-trigger-icon" in tsx
    assert ".power-trigger-shell" in css
```

```python
def test_battery_uses_level_classes_and_three_pulse_warning():
    tsx = read("components/Battery.tsx")
    css = read("style.css")
    assert "batteryLevelClass" in tsx
    assert "battery-level-critical" in tsx
    assert "battery-v28-warning-pulse" in css
    assert "3" in css_block(css, ".battery-card.battery-level-warning:not(.charging)")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v28_status_motion_corrections.py`

Expected: FAIL because v2.8 helpers/classes do not exist yet.

### Task 2: Fix Clock Reel And Popup Geometry

**Files:**
- Modify: `.config/ags/components/ClockCard.tsx`
- Modify: `.config/ags/components/PopupWindow.tsx`
- Modify: `.config/ags/style.css`
- Test: `.config/ags/tests/test_v28_status_motion_corrections.py`

**Interfaces:**
- Produces: `previousTime`, `clock-reel-old`, `clock-reel-new`, `clock-reel-tick-even`, `clock-reel-tick-odd`, and `popup-v28-stable-reveal`.

- [ ] **Step 1: Implement reel state**

Use `createState` for previous time and tick parity. Update the poll callback so each second stores the previous value before returning the next value.

- [ ] **Step 2: Render two faces per digit slot**

Each digit slot renders an old face and new face. Changed digits receive the current tick class. Unchanged digits render stable.

- [ ] **Step 3: Remove popup frame scale**

Change popup keyframes to a v2.8 reveal with opacity and `translateY(-6px)` only. The `.popup-window-frame` block must not transition transform and must not use scale.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v28_status_motion_corrections.py .config/ags/tests/test_v27_motion_popup_polish.py .config/ags/tests/test_v252_runtime_geometry.py`

Expected: PASS.

### Task 3: Add Status-Aware Top-Bar Icons

**Files:**
- Modify: `.config/ags/components/Workspaces.tsx`
- Modify: `.config/ags/components/NetworkControl.tsx`
- Modify: `.config/ags/components/PowerControl.tsx`
- Modify: `.config/ags/components/Battery.tsx`
- Modify: `.config/ags/style.css`
- Test: `.config/ags/tests/test_v28_status_motion_corrections.py`

**Interfaces:**
- Produces: `workspace-pacman`, `workspace-id-N`, `wifiSignalClass`, `power-trigger-shell`, `batteryLevelClass`.

- [ ] **Step 1: Workspace Pac-Man**

Add `workspace-id-${id}` to each workspace button. Replace active dot rendering with a visible active Pac-Man label and keep inactive numbers.

- [ ] **Step 2: Wi-Fi signal classes**

Add `wifiSignalClass(strength: number)` and apply it to the top-bar network trigger image plus panel header/quick action icons.

- [ ] **Step 3: Power trigger layout**

Wrap the power glyph in `power-trigger-shell` and move caffeine dot into a stable status-dot position.

- [ ] **Step 4: Battery level classes**

Add `batteryLevelClass(percent, charging)` and apply it to `.battery-card`. Add icon/percent classes for styling.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v28_status_motion_corrections.py .config/ags/tests/test_v23_power_hub.py .config/ags/tests/test_v20_network_dashboard.py .config/ags/tests/test_v253_elastic_workspace_rail.py`

Expected: PASS.

### Task 4: Full Verification And Live Smoke

**Files:**
- No new source files.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: committed implementation and live `$HOME` AGS smoke evidence.

- [ ] **Step 1: Run full automated checks**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests
bash -n user_scripts/bar/bar_switch.sh tests/test_bar_switch_adaptive.sh tests/test_adaptive_glass_source.sh .config/ags/install.sh .config/ags/scripts/*.sh .config/ags/tests/*.sh
bash tests/test_bar_switch_adaptive.sh
bash tests/test_adaptive_glass_source.sh
```

- [ ] **Step 2: Run AGS bundle smoke**

Run:

```bash
tmpdir=$(mktemp -d)
cp -a .config/ags "$tmpdir/ags"
ags types -u -d "$tmpdir/ags"
ags bundle "$tmpdir/ags/app.tsx" "$tmpdir/adaptive-glass.js" --root "$tmpdir/ags"
test -s "$tmpdir/adaptive-glass.js"
```

- [ ] **Step 3: Commit**

Run:

```bash
git add .config/ags/components .config/ags/style.css .config/ags/tests/test_v28_status_motion_corrections.py
GIT_AUTHOR_NAME='Codex' GIT_AUTHOR_EMAIL='codex@openai.com' GIT_COMMITTER_NAME='Codex' GIT_COMMITTER_EMAIL='codex@openai.com' git commit -m "feat: polish adaptive glass status motion"
```

- [ ] **Step 4: Live smoke**

Run:

```bash
./.config/ags/install.sh
: > "${XDG_RUNTIME_DIR:-/tmp}/dusky-adaptive-glass.log"
ags quit --instance dusky-adaptive-glass >/dev/null 2>&1 || true
/home/hangoma/user_scripts/bar/bar_switch.sh adaptive-glass
sleep 3
/home/hangoma/user_scripts/bar/bar_switch.sh status
ags request -i dusky-adaptive-glass state
tail -n 120 "${XDG_RUNTIME_DIR:-/tmp}/dusky-adaptive-glass.log"
```

Expected: Adaptive Glass running, state request returns JSON, runtime log has no warnings or errors.

## Self-Review

- Spec coverage: all requested items map to Tasks 2 and 3.
- Placeholder scan: no placeholder sections remain.
- Type consistency: helper names in tests match implementation tasks.
