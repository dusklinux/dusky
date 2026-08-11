# Adaptive Glass Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an opinionated Adaptive Glass preferences layer: Dusky Control Center exposes motion style and feature toggles, AGS consumes those settings live, Soft Magnetic remains the default premium motion style, and Workspace Preview can be disabled while the rest of the bar stays stable.

**Architecture:** Reuse Dusky's existing flat-file settings backend under `~/.config/dusky/settings`. Add small AGS state modules beside `themeState.ts`, expose class helpers to bar and popup root windows, and gate optional bar/popup features through reactive boolean accessors. Keep defaults fully featured and only hide or stop behavior when users opt out.

**Tech Stack:** AGS v3 GTK4/TypeScript, Gnim state bindings, GLib/Gio file monitoring, GTK CSS, Dusky Control Center TOML rows, shell and pytest static contract tests.

## Global Constraints

- Keep Waybar as the fallback and keep the existing `bar_switch.sh` integration untouched except for tests if needed.
- Do not reintroduce NovaBar.
- Default to `soft-magnetic`.
- Default all feature toggles to `true`, especially `workspace-preview`.
- Keep visual polish scoped with root classes: `motion-soft-magnetic` and `motion-precise-futuristic`.
- Preserve stable widget dimensions; motion changes must use paint properties, timing, opacity, shadow, and transform, not hover-driven layout churn.
- Avoid looping animation. Motion should respond to user intent only.

## Task 1: Add Preference Contract Tests

Create failing tests first so the settings surface and runtime contracts are pinned before implementation.

- [ ] Add `.config/ags/tests/test_preferences_contract.py`.
- [ ] Assert `.config/ags/lib/motionState.ts` exists and defines:
  - `AdaptiveMotionStyle`
  - `DEFAULT_MOTION_STYLE = "soft-magnetic"`
  - `motionStyle`
  - `motionClass`
  - `getWorkspaceMotionTiming`
  - both accepted tokens: `soft-magnetic`, `precise-futuristic`
- [ ] Assert `.config/ags/lib/featureState.ts` exists and defines:
  - feature keys `workspace-preview`, `media-island`, `weather`, `notifications`
  - default value `true`
  - exported accessors for workspace, media, weather, and notifications
- [ ] Assert `Bar.tsx` and `PopupWindow.tsx` include `motionClass`.
- [ ] Assert `PopupWindow.tsx` supports an optional `enabled` binding.
- [ ] Assert `Workspaces.tsx` checks `workspacePreviewEnabled` before opening previews.
- [ ] Assert `PopupWindows.tsx` gates the workspace popup with `workspacePreviewEnabled`.
- [ ] Assert `style.css` contains scoped rules for both motion classes and does not define motion-specific `min-width` overrides.
- [ ] Assert Dusky Control Center config contains:
  - page title `Status Bar`
  - `Adaptive Glass Motion`
  - `Workspace Preview`
  - `Media Island`
  - `Weather`
  - `Notifications`
  - key `ags/adaptive-glass-motion`
  - keys under `ags/features/`

Run expected-red tests:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_preferences_contract.py
```

## Task 2: Implement AGS Motion State

Add `.config/ags/lib/motionState.ts` with the same durable pattern as `themeState.ts`, but without shelling out for ordinary writes.

- [ ] Read from `~/.config/dusky/settings/ags/adaptive-glass-motion`.
- [ ] Validate values strictly:
  - `soft-magnetic`
  - `precise-futuristic`
- [ ] Fall back to `soft-magnetic` for missing or invalid files.
- [ ] Monitor the parent directory with `Gio.File.monitor_directory` so Control Center changes apply without restarting AGS.
- [ ] Export:

```ts
export type AdaptiveMotionStyle = "soft-magnetic" | "precise-futuristic"
export const DEFAULT_MOTION_STYLE: AdaptiveMotionStyle = "soft-magnetic"
export const [motionStyle, setMotionStyle] = createState<AdaptiveMotionStyle>(loadMotionStyle())
export const motionClass = motionStyle((style) => `motion-${style}`)
export function getWorkspaceMotionTiming() { ... }
```

Use timing values that feel distinct but not theatrical:

```ts
const WORKSPACE_TIMINGS = {
  "soft-magnetic": {
    interactionReleaseDelayMs: 140,
    snapDelayMs: 205,
    snapPulseMs: 560,
  },
  "precise-futuristic": {
    interactionReleaseDelayMs: 85,
    snapDelayMs: 125,
    snapPulseMs: 320,
  },
} as const
```

## Task 3: Apply Motion Classes and Timings

Wire motion into root windows and workspace interaction timing.

- [ ] Update `.config/ags/components/Bar.tsx`:

```tsx
class={createComputed(() =>
  `adaptive-glass-root theme-${themeMode()} ${motionClass()}`
)}
```

- [ ] Update `.config/ags/components/PopupWindow.tsx` similarly:

```tsx
class={createComputed(() =>
  `adaptive-glass-popup-window theme-${themeMode()} ${motionClass()}`
)}
```

- [ ] Update `.config/ags/lib/workspaceInteractionState.ts` so `scheduleSnap` and `scheduleRelease` call `getWorkspaceMotionTiming()` at schedule time instead of using fixed exported delays.
- [ ] Keep existing exported timing constants only if tests still require them; otherwise replace tests with token/timing contract coverage.
- [ ] Do not change workspace focus behavior or the five-workspace rail model in this task.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_preferences_contract.py .config/ags/tests
```

## Task 4: Implement Feature State and Workspace Preview Toggle

Add the feature foundation and fully wire Workspace Preview first.

- [ ] Add `.config/ags/lib/featureState.ts`.
- [ ] Read feature files from `~/.config/dusky/settings/ags/features/`.
- [ ] Accept only clear boolean strings:
  - true: `true`, `yes`, `1`, `on`, `enabled`
  - false: `false`, `no`, `0`, `off`, `disabled`
- [ ] Fall back to `true` for missing or invalid files.
- [ ] Monitor the feature directory and update individual states live.
- [ ] Export:

```ts
export const workspacePreviewEnabled = featureAccessors["workspace-preview"]
export const mediaIslandEnabled = featureAccessors["media-island"]
export const weatherEnabled = featureAccessors.weather
export const notificationsEnabled = featureAccessors.notifications
```

- [ ] Update `.config/ags/lib/workspacePreviewState.ts`:

```ts
if (!workspacePreviewEnabled()) {
  closeWorkspacePreview()
  return 0
}
```

- [ ] Update `.config/ags/components/Workspaces.tsx` so hover still claims the magnetic workspace interaction, but disabled preview closes the workspace panel and does not call `openWorkspacePreview`.
- [ ] Update `.config/ags/components/PopupWindow.tsx` with an optional enabled binding:

```ts
enabled?: Accessor<boolean>
visible={createComputed(() => (enabled?.() ?? true) && activePanel() === id)}
```

- [ ] Update `.config/ags/components/PopupWindows.tsx`:

```tsx
<PopupWindow
  id="workspace"
  ...
  enabled={workspacePreviewEnabled}
/>
```

- [ ] Optionally hide Media, Weather, and Notification entrypoints in this pass using the same feature state if AGS conditional rendering is straightforward. Do not risk new polling bugs for those components; the priority is the workspace preview toggle.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_preferences_contract.py .config/ags/tests
```

## Task 5: Add Dusky Control Center Rows

Update `user_scripts/dusky_system/control_center/dusky_config.toml`.

- [ ] Rename the navigation title from `Status Bar (Waybar)` to `Status Bar`.
- [ ] Rename the description to cover both bars.
- [ ] Add an `Adaptive Glass` section before `Waybar Management`.
- [ ] Add a selection row:

```toml
[[pages.layout.items.layout.items]]
type = "selection"
[pages.layout.items.layout.items.properties]
title = "Adaptive Glass Motion"
description = "Workspace and panel animation style"
icon = "preferences-desktop-effects-symbolic"
key = "ags/adaptive-glass-motion"
interval = 2
options = ["Soft Magnetic", "Precise Futuristic"]
options_map = { "soft-magnetic" = "Soft Magnetic", "precise-futuristic" = "Precise Futuristic" }
```

- [ ] Add toggle cards:

```toml
[[pages.layout.items.layout.items]]
type = "toggle_card"
[pages.layout.items.layout.items.properties]
title = "Workspace Preview"
description = "Show live workspace window previews"
icon = "view-grid-symbolic"
key = "ags/features/workspace-preview"
```

Repeat for:

```toml
title = "Media Island"
key = "ags/features/media-island"

title = "Weather"
key = "ags/features/weather"

title = "Notifications"
key = "ags/features/notifications"
```

- [ ] Add `on_toggle.enabled` and `on_toggle.disabled` commands only if Control Center needs explicit side effects. Prefer key-only rows if the existing ToggleCard persists state automatically.

Run:

```bash
python - <<'PY'
import tomllib
from pathlib import Path
tomllib.loads(Path("user_scripts/dusky_system/control_center/dusky_config.toml").read_text())
PY
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_preferences_contract.py
```

## Task 6: Add Premium Motion CSS

Append a small, final CSS layer to `.config/ags/style.css` so later rules win without editing old versioned layers.

- [ ] Add a section comment: `/* Adaptive Glass preferences: motion styles */`.
- [ ] For `motion-soft-magnetic`:
  - slightly longer transitions
  - elastic cubic-bezier
  - softer active/hover glow
  - preview entrance with gentle opacity/scale
  - workspace snap animation duration `560ms`
- [ ] For `motion-precise-futuristic`:
  - shorter transitions
  - sharper cubic-bezier
  - lower glow spread
  - crisper selected states
  - workspace snap animation duration `320ms`
- [ ] Scope CSS through root window classes:

```css
.motion-soft-magnetic .workspace-button { ... }
.motion-precise-futuristic .workspace-button { ... }
.motion-soft-magnetic .workspace-magnetic-shell.snapping { ... }
.motion-precise-futuristic .workspace-magnetic-shell.snapping { ... }
```

- [ ] Do not add new width, height, margin, or padding in motion-scoped rules.
- [ ] Keep the palette multi-tone and restrained; avoid a single-hue theme or loud gradients.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests/test_preferences_contract.py .config/ags/tests
```

## Task 7: Verify, Live Smoke, and Commit

Run the full integration checks already proven in the branch.

- [ ] Bash syntax:

```bash
bash -n user_scripts/bar/bar_switch.sh tests/test_bar_switch_adaptive.sh tests/test_adaptive_glass_source.sh .config/ags/install.sh .config/ags/scripts/*.sh .config/ags/tests/*.sh
```

- [ ] Root shell tests:

```bash
bash tests/test_bar_switch_adaptive.sh
bash tests/test_adaptive_glass_source.sh
bash .config/ags/tests/test_capture_helper.sh
bash .config/ags/tests/test_install_smoke.sh
```

- [ ] Full pytest suite:

```bash
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests
```

- [ ] Lua syntax:

```bash
luac -p .config/hypr/source/keybinds.lua user_scripts/hypr/defaults/edit_here/autostart.lua user_scripts/hypr/defaults/edit_here/keybinds.lua
```

- [ ] Temp AGS bootstrap and bundle:

```bash
TMP=$(mktemp -d)
cp -a .config/ags "$TMP/ags"
ags types -u -d "$TMP/ags"
test -e "$TMP/ags/tsconfig.json"
test -e "$TMP/ags/node_modules/ags"
ags bundle "$TMP/ags/app.tsx" "$TMP/adaptive-glass.js" --root "$TMP/ags"
test -s "$TMP/adaptive-glass.js"
rm -rf "$TMP"
```

- [ ] Artifact sweep:

```bash
test ! -e .config/ags/@girs
test ! -e .config/ags/node_modules
test ! -e .config/ags/tsconfig.json
test ! -e .config/ags/env.d.ts
find .config/ags -name '__pycache__' -o -name '.pytest_cache'
```

- [ ] Live smoke if the current desktop is available:
  - switch to Adaptive Glass
  - set `ags/adaptive-glass-motion` to each style
  - set `ags/features/workspace-preview` false, hover workspace, confirm no preview opens
  - set it true, hover populated workspace, confirm preview opens

- [ ] Commit with focused message:

```bash
git add .config/ags user_scripts/dusky_system/control_center/dusky_config.toml docs/superpowers/plans/2026-08-11-adaptive-glass-preferences.md
git commit -m "feat: add adaptive glass preferences"
```

## Rollback Notes

- Set `~/.config/dusky/settings/ags/adaptive-glass-motion` to `soft-magnetic` to restore default motion.
- Delete `~/.config/dusky/settings/ags/features/*` to restore fully featured defaults.
- Use `SUPER + ALT + G` or `~/user_scripts/bar/bar_switch.sh waybar` to return to Waybar.
