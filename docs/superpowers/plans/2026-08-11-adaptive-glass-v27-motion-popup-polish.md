# Adaptive Glass v2.7 Motion And Popup Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish Adaptive Glass workspace recoil, time pill reel motion, calendar popup styling, and shared popup reveal motion while preserving stable bar geometry.

**Architecture:** Keep behavior inside the current AGS v3 GTK4 app. Add focused contract tests, adjust workspace timing in `motionState.ts`, keep workspace recoil on the existing overlay shell, split the clock display into fixed digit slots in `ClockCard.tsx`, and add final CSS overrides at the end of `style.css`.

**Tech Stack:** AGS v3 GTK4/TypeScript, Gnim state bindings, GTK CSS keyframes, GLib time polling, pytest static contract tests, AGS bundle smoke tests.

## Global Constraints

- Do not change workspace count or workspace focus behavior.
- Do not animate workspace layout properties such as `padding`, `margin`, `width`, `height`, or `min-width` inside recoil keyframes.
- Do not add looping animation.
- Keep `panel="calendar"` on the time pill.
- Remove the clock accent dot and its visible glow.
- Keep the native `Gtk.Calendar` and existing Today/Clocks actions.
- Preserve `ags request -i dusky-adaptive-glass state`.
- Keep Waybar fallback and bar switch integration untouched.

---

## Task 1: Add v2.7 Polish Contract Tests

**Files:**

- Create: `.config/ags/tests/test_v27_motion_popup_polish.py`

**Interfaces:**

- Consumes: existing source files under `.config/ags`.
- Produces: failing tests that define the v2.7 polish contract.

- [ ] **Step 1: Write the failing test file**

```python
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(re.escape(selector) + r"\\s*\\{(.*?)\\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def keyframes(css: str, name: str) -> str:
    start = css.index(f"@keyframes {name}")
    next_marker = css.find("@keyframes", start + 1)
    next_comment = css.find("/*", start + 1)
    ends = [pos for pos in [next_marker, next_comment] if pos != -1]
    end = min(ends) if ends else len(css)
    return css[start:end]


def test_workspace_recoil_is_v27_stronger_visible_and_non_sizing():
    css = read("style.css")
    assert "v2.7 — premium workspace recoil" in css
    block = keyframes(css, "workspace-v27-premium-recoil")
    scales = [float(v) for v in re.findall(r"scaleX\\((\\d+(?:\\.\\d+)?)\\)", block)]
    assert max(scales) >= 1.28
    assert any(0.88 <= value <= 0.94 for value in scales)
    for forbidden in ("padding:", "margin:", "min-width", "width:", "height:"):
        assert forbidden not in block
    assert block.count("box-shadow:") >= 4


def test_motion_modes_use_v27_recoil_timing():
    motion = read("lib/motionState.ts")
    css = read("style.css")
    assert "snapDelayMs: 120" in motion
    assert "snapPulseMs: 640" in motion
    assert "snapDelayMs: 70" in motion
    assert "snapPulseMs: 360" in motion
    soft = css_block(css, ".motion-soft-magnetic .workspace-magnetic-shell.snapping")
    precise = css_block(css, ".motion-precise-futuristic .workspace-magnetic-shell.snapping")
    assert "workspace-v27-premium-recoil" in soft
    assert "640ms" in soft
    assert "workspace-v27-premium-recoil" in precise
    assert "360ms" in precise


def test_clock_uses_fixed_reel_digits_without_accent_dot():
    tsx = read("components/ClockCard.tsx")
    assert "ClockReelDigit" in tsx
    assert "clock-reel" in tsx
    assert "clock-reel-digit" in tsx
    assert "clock-reel-separator" in tsx
    assert "clock-accent-dot" not in tsx
    assert 'panel="calendar"' in tsx


def test_clock_reel_css_has_vertical_casino_motion_and_stable_slots():
    css = read("style.css")
    assert "@keyframes clock-v27-reel-in" in css
    reel = keyframes(css, "clock-v27-reel-in")
    assert "translateY(-" in reel
    assert "translateY(0" in reel
    slot = css_block(css, ".clock-reel-digit")
    assert "min-width:" in slot
    assert "min-height:" in slot
    assert "overflow: hidden" in slot
    assert "animation: clock-v27-reel-in" in css
    assert ".clock-accent-dot" in css
    assert "display: none" in css_block(css, ".clock-accent-dot")


def test_calendar_keeps_native_behavior_and_gets_v27_surface_polish():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")
    assert "<Gtk.Calendar" in tsx
    assert 'label="Today"' in tsx
    assert 'label="Clocks"' in tsx
    assert "calendar.select_day(GLib.DateTime.new_now_local())" in tsx
    assert "v2.7 — calendar frosted refinement" in css
    panel = css_block(css, ".calendar-panel")
    widget = css_block(css, ".calendar-widget")
    footer = css_block(css, ".calendar-footer-action")
    assert "padding: 11px" in panel
    assert "border-radius: 16px" in widget
    assert "min-height: 26px" in footer


def test_popup_frames_have_open_state_and_shared_reveal_motion():
    tsx = read("components/PopupWindow.tsx")
    css = read("style.css")
    assert "popup-open" in tsx
    assert "@keyframes popup-v27-reveal" in css
    frame = css_block(css, ".popup-window-frame")
    assert "animation: popup-v27-reveal" in frame
    assert "transform-origin: top center" in frame
    assert ".motion-soft-magnetic .popup-window-frame" in css
    assert ".motion-precise-futuristic .popup-window-frame" in css
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v27_motion_popup_polish.py
```

Expected: FAIL because v2.7 CSS tokens, clock reel slots, and popup-open state do not exist yet.

- [ ] **Step 3: Commit after green in later tasks**

Do not commit this test file while it is red unless pausing work.

## Task 2: Intensify Workspace Recoil Without Layout Changes

**Files:**

- Modify: `.config/ags/lib/motionState.ts`
- Modify: `.config/ags/style.css`
- Test: `.config/ags/tests/test_v27_motion_popup_polish.py`

**Interfaces:**

- Consumes: existing `getWorkspaceMotionTiming()` and `workspace-magnetic-shell.snapping`.
- Produces: stronger recoil timings and a new `workspace-v27-premium-recoil` keyframe.

- [ ] **Step 1: Update workspace timing**

Change `WORKSPACE_TIMINGS` to:

```ts
const WORKSPACE_TIMINGS = {
  "soft-magnetic": {
    interactionReleaseDelayMs: 130,
    snapDelayMs: 120,
    snapPulseMs: 640,
  },
  "precise-futuristic": {
    interactionReleaseDelayMs: 75,
    snapDelayMs: 70,
    snapPulseMs: 360,
  },
} as const
```

- [ ] **Step 2: Add final CSS recoil override**

Append a v2.7 block to `style.css`:

```css
/* v2.7 — premium workspace recoil */
@keyframes workspace-v27-premium-recoil {
  0% { transform: scaleX(1.00); opacity: 0.34; box-shadow: 0 1px 3px alpha(#000000, 0.08); }
  26% { transform: scaleX(1.32); opacity: 1.00; box-shadow: 0 4px 13px alpha(@primary, 0.30), 0 0 22px alpha(@secondary, 0.30), inset 0 1px alpha(@on_surface, 0.16); }
  48% { transform: scaleX(0.91); opacity: 0.86; box-shadow: 0 2px 7px alpha(@primary, 0.15), 0 0 10px alpha(@secondary, 0.10), inset 0 1px alpha(@on_surface, 0.08); }
  72% { transform: scaleX(1.06); opacity: 0.50; box-shadow: 0 1px 5px alpha(@primary, 0.08), 0 0 6px alpha(@secondary, 0.05); }
  100% { transform: scaleX(1.00); opacity: 0; box-shadow: 0 0 0 alpha(@secondary, 0); }
}
```

Add mode-specific `.workspace-magnetic-shell.snapping` overrides using `640ms` and `360ms`.

- [ ] **Step 3: Run focused test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v27_motion_popup_polish.py::test_workspace_recoil_is_v27_stronger_visible_and_non_sizing .config/ags/tests/test_v27_motion_popup_polish.py::test_motion_modes_use_v27_recoil_timing
```

Expected: PASS.

## Task 3: Replace Clock Label With Fixed Reel Digits

**Files:**

- Modify: `.config/ags/components/ClockCard.tsx`
- Modify: `.config/ags/style.css`
- Test: `.config/ags/tests/test_v27_motion_popup_polish.py`

**Interfaces:**

- Consumes: existing `ClockCard` as the `calendar` trigger.
- Produces: `ClockReelDigit` component and stable reel slot classes.

- [ ] **Step 1: Add reel helper in `ClockCard.tsx`**

Implement:

```tsx
function ClockReelDigit({ value }: { value: () => string }) {
  return (
    <box class={value((char) => `clock-reel-digit clock-reel-value-${char}`)}>
      <label class="clock-reel-digit-face" label={value} />
    </box>
  )
}
```

Build `timeDigits` from the existing `time` accessor with one slot for each character. Keep `:` as a separator label.

- [ ] **Step 2: Remove visible accent dot**

Remove `<box class="clock-accent-dot" ... />` from the `ClockCard` JSX.

- [ ] **Step 3: Add clock reel CSS**

Append v2.7 clock CSS:

```css
@keyframes clock-v27-reel-in {
  0% { transform: translateY(-115%); opacity: 0.36; }
  58% { transform: translateY(10%); opacity: 1; }
  100% { transform: translateY(0); opacity: 1; }
}

.clock-reel { min-height: 18px; }
.clock-reel-digit { min-width: 8px; min-height: 18px; overflow: hidden; }
.clock-reel-digit-face { animation: clock-v27-reel-in 420ms cubic-bezier(0.18, 0.82, 0.24, 1); }
.clock-reel-separator { min-width: 4px; }
.clock-accent-dot { display: none; box-shadow: none; }
```

- [ ] **Step 4: Run focused test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v27_motion_popup_polish.py::test_clock_uses_fixed_reel_digits_without_accent_dot .config/ags/tests/test_v27_motion_popup_polish.py::test_clock_reel_css_has_vertical_casino_motion_and_stable_slots
```

Expected: PASS.

## Task 4: Refine Calendar And Shared Popup Reveal

**Files:**

- Modify: `.config/ags/components/PopupWindow.tsx`
- Modify: `.config/ags/style.css`
- Test: `.config/ags/tests/test_v27_motion_popup_polish.py`

**Interfaces:**

- Consumes: existing `activePanel()` and popup id classes.
- Produces: `popup-open` frame state and reusable reveal animation.

- [ ] **Step 1: Add popup-open frame class**

In `PopupWindow.tsx`, compute:

```tsx
const frameClass = createComputed(() =>
  activePanel() === id
    ? `popup-window-frame popup-${id} popup-open`
    : `popup-window-frame popup-${id}`
)
```

Use `class={frameClass}` on the frame box.

- [ ] **Step 2: Add popup reveal CSS**

Append:

```css
@keyframes popup-v27-reveal {
  0% { opacity: 0; transform: translateY(-7px) scale(0.982); }
  62% { opacity: 1; transform: translateY(1px) scale(1.004); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
}

.popup-window-frame {
  transform-origin: top center;
  animation: popup-v27-reveal 210ms cubic-bezier(0.18, 0.82, 0.24, 1);
}
```

Add motion-specific durations:

```css
.motion-soft-magnetic .popup-window-frame { animation-duration: 250ms; }
.motion-precise-futuristic .popup-window-frame { animation-duration: 145ms; }
```

- [ ] **Step 3: Add calendar refinement CSS**

Append a v2.7 calendar block:

```css
/* v2.7 — calendar frosted refinement */
.calendar-panel { min-width: 252px; padding: 11px; }
.calendar-widget { border-radius: 16px; }
.calendar-footer-action { min-height: 26px; }
```

Include final visual values for softer borders, tighter header spacing, refined selected/today states, and calmer footer chips.

- [ ] **Step 4: Run focused test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_v27_motion_popup_polish.py::test_calendar_keeps_native_behavior_and_gets_v27_surface_polish .config/ags/tests/test_v27_motion_popup_polish.py::test_popup_frames_have_open_state_and_shared_reveal_motion
```

Expected: PASS.

## Task 5: Full Verification, Commit, And Live Smoke

**Files:**

- Modify: all task files
- Test: full AGS suite and live `$HOME` AGS runtime

**Interfaces:**

- Consumes: completed code from Tasks 1-4.
- Produces: committed v2.7 polish and live-smoke evidence.

- [ ] **Step 1: Run full local tests**

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests
bash -n .config/ags/install.sh .config/ags/scripts/*.sh .config/ags/tests/*.sh
```

- [ ] **Step 2: Run AGS bundle smoke**

```bash
TMP=$(mktemp -d)
cp -a .config/ags "$TMP/ags"
ags types -u -d "$TMP/ags"
ags bundle "$TMP/ags/app.tsx" "$TMP/adaptive-glass.js" --root "$TMP/ags"
test -s "$TMP/adaptive-glass.js"
```

- [ ] **Step 3: Commit implementation**

```bash
git add .config/ags/components/ClockCard.tsx .config/ags/components/PopupWindow.tsx .config/ags/lib/motionState.ts .config/ags/style.css .config/ags/tests/test_v27_motion_popup_polish.py
git commit -m "feat: polish adaptive glass motion and popups"
```

- [ ] **Step 4: Deploy into live `$HOME` config**

```bash
ags quit --instance dusky-adaptive-glass 2>/dev/null || true
./.config/ags/install.sh
/home/hangoma/user_scripts/bar/bar_switch.sh adaptive-glass
```

- [ ] **Step 5: Live smoke**

```bash
/home/hangoma/user_scripts/bar/bar_switch.sh status
ags request -i dusky-adaptive-glass state
grim -g '0,0 1920x120' "/tmp/adaptive-glass-v27-topbar.png"
tail -n 80 /run/user/1000/dusky-adaptive-glass.log
```

Expected:

- Adaptive Glass running.
- Runtime state request returns JSON.
- Screenshot shows the cleaned time pill with no clock dot.
- AGS log has no new errors.

## Self-Review

- Spec coverage: workspace recoil, clock reel, calendar polish, shared popup reveal, tests, and live smoke are covered.
- Incomplete-section scan: no unfinished task content remains.
- Type consistency: plan uses existing `motionState.ts`, `ClockCard.tsx`, `PopupWindow.tsx`, `style.css`, and pytest test style.
