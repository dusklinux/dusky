# Adaptive Glass Review Notes

Branch: `adaptive-glass-integration`

## Summary

This branch adds Adaptive Glass as a source-owned AGS bar option in Dusky while preserving Waybar as the fallback.

- AGS source: `.config/ags`
- Switcher: `user_scripts/bar/bar_switch.sh`
- Toggle keybind: `SUPER + ALT + G`
- Startup path: `bar_switch.sh start`
- Control Center page: `Status Bar`
- Default bar state: Waybar unless the user chooses Adaptive Glass

NovaBar is intentionally not integrated. A legacy saved `novabar` state is migrated to `adaptive-glass` so existing testers do not get stuck on an invalid value.

## Preferences

Adaptive Glass preferences are exposed in Dusky Control Center.

- Motion style: `soft-magnetic` default, `precise-futuristic` optional
- Feature flags: `workspace-preview`, `media-island`, `weather`, `notifications`
- Settings directory: `~/.config/dusky/settings/ags`
- Feature directory: `~/.config/dusky/settings/ags/features`

The default remains opinionated and fully featured. Feature toggles are for users who want a calmer or lighter bar.

## Runtime Notes

- Motion settings are read by `.config/ags/lib/motionState.ts`.
- Feature settings are read by `.config/ags/lib/featureState.ts`.
- Bar and popup root windows receive `motion-soft-magnetic` or `motion-precise-futuristic`.
- Workspace Preview is prevented before capture work starts when disabled.
- Weather, Notifications, and Media Island are mounted through `OptionalFeature`, so disabled entrypoints avoid their component-level polling.

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
