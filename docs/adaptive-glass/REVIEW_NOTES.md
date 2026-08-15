# Adaptive Glass Review Notes

Branch: `adaptive-glass-integration`

## Summary

This branch adds Adaptive Glass as a source-owned AGS bar option in Dusky while preserving Waybar as the fallback.

- Submitter/contact: Charles Hangoma <charleshangoma7@gmail.com>
- AGS source: `.config/ags`
- Switcher: `user_scripts/bar/bar_switch.sh`
- Toggle keybind: `SUPER + ALT + G`
- Startup path: `bar_switch.sh start`
- Control Center page: `Status Bar`
- Default bar state: Waybar unless the user chooses Adaptive Glass
- PR screenshots staged locally: `/home/hangoma/adaptive bar screenshots`

NovaBar is intentionally not integrated. A legacy saved `novabar` state is migrated to `adaptive-glass` so existing testers do not get stuck on an invalid value.

## Preferences

Adaptive Glass preferences are exposed in Dusky Control Center.

- `Use Adaptive Bar` runs `~/.config/ags/install.sh --interactive --activate`.
- `Use Waybar` runs `~/user_scripts/bar/bar_switch.sh waybar`.
- `Check Adaptive Dependencies` runs `~/.config/ags/install.sh --check`.
- Motion style: `soft-magnetic` default, `precise-futuristic` optional
- Feature flags: `workspace-preview`, `media-island`, `weather`, `notifications`
- Settings directory: `~/.config/dusky/settings/ags`
- Feature directory: `~/.config/dusky/settings/ags/features`

The default remains opinionated and fully featured. Feature toggles are for users who want a calmer or lighter bar.

## Public Installer

Adaptive Glass is opt-in. Dusky updates should ship the source and Control Center entry but should not force an immediate AGS install.

- `install.sh --interactive --activate` is the Control Center install path.
- `install.sh --auto --activate` is available for autonomous test runs.
- `install.sh --check` verifies dependency and startup persistence without copying or switching.
- `install.sh --skip-deps --no-activate` is used by source-copy tests.
- Missing packages are retried; the installer aborts if AGS/GJS/Astal/Hyprland capture probes still fail.
- `.adaptive-glass-managed` marks the installed runtime directory so future installs can replace it safely.
- Unmanaged existing AGS configs are backed up before replacement.

This is review-ready but not final. Suggestions and improvements are expected after the pull request, especially for distribution package names, optional feature defaults, and visual refinement.

## Runtime Notes

- Motion settings are read by `.config/ags/lib/motionState.ts`.
- Feature settings are read by `.config/ags/lib/featureState.ts`.
- Bar and popup root windows receive `motion-soft-magnetic` or `motion-precise-futuristic`.
- Workspace Preview is prevented before capture work starts when disabled.
- Weather, Notifications, and Media Island are mounted through `OptionalFeature`, so disabled entrypoints avoid their component-level polling.

## Contributor Map

- `.config/ags/app.tsx`: AGS entry point, Matugen stylesheet loading, request handler.
- `.config/ags/components`: visible bar and popup components.
- `.config/ags/lib`: Dusky commands, setting monitors, feature/motion/theme state, popup/workspace state.
- `.config/ags/style.css`: final visual cascade. New polish is appended as versioned layers.
- `.config/ags/install.sh`: public installer, dependency verification, managed copy, persistence checks.
- `user_scripts/bar/bar_switch.sh`: runtime switcher and active bar state.
- `user_scripts/dusky_system/control_center/dusky_config.toml`: user-facing install/switch/preferences surface.

## Verification

Last local verification before this note:

```bash
bash -n user_scripts/bar/bar_switch.sh tests/test_bar_switch_adaptive.sh tests/test_adaptive_glass_source.sh .config/ags/install.sh .config/ags/scripts/*.sh .config/ags/tests/*.sh
bash tests/test_bar_switch_adaptive.sh
bash tests/test_adaptive_glass_source.sh
bash .config/ags/tests/test_capture_helper.sh
bash .config/ags/tests/test_install_smoke.sh
python -m py_compile user_scripts/dusky_system/control_center/lib/rows.py
python - <<'PY'
import tomllib
from pathlib import Path
tomllib.loads(Path("user_scripts/dusky_system/control_center/dusky_config.toml").read_text())
PY
luac -p .config/hypr/source/keybinds.lua user_scripts/hypr/defaults/edit_here/autostart.lua user_scripts/hypr/defaults/edit_here/keybinds.lua
PYTHONDONTWRITEBYTECODE=1 uvx --from pytest pytest -q .config/ags/tests
```

The AGS temp bootstrap and bundle check also passed with `ags types -u` and `ags bundle` against a copied `.config/ags` directory.
