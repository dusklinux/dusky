# Dusky Control Center implementation result

Date: 2026-09-26. This implements the confirmed defects in the historical [audit](IMPLEMENTATION_PLAN.md), with a focused service-status correction. The Python/GTK architecture and CSS were preserved. A source backup was made at `/tmp/dusky-control-center-before-20260926.tgz` before edits.

## Changes

1. **Radio state:** all four Bluetooth/Wi-Fi queries use `lib/radio_status.py`. It reads each rfkill device's soft and hard block state and returns `yes`, `no`, or `unavailable`. The polling channel now has an explicit failure callback. An unavailable query disables the toggle/card until a successful observation; a blocked radio is displayed as off.
2. **One writer per setting:** all 13 configured key-bound controls declare `persistence = "action"` or `"app"`. Only `left_handed_mouse` remains app-owned. Action-owned controls do not write the optimistic requested value. File deletion events refresh observed state. The clock helper was exercised against a temporary Waybar tree and writes lowercase `true`/`false` itself.
3. **Finite action results:** `lib/actions.py` observes finite commands with `Gio.Subprocess`, the installed `dusky-run` wrapper, process-group timeout cleanup, and bounded error text. Terminal/daemon actions are marked `mode = "launch"`; their feedback says Launched. Toggles, selections, entries, sliders and spin controls use completion results and revert or read back as appropriate. User-requested actions continue through widget reload. Disk-swap allocation has a 600-second timeout; the default is 90 seconds and numeric controls use 15 seconds.
4. **Numeric controls:** the five configured sliders now coalesce rapid changes, dispatch at most one action at a time, retain the latest replacement, invalidate older observations, and issue a fresh query after the last apply. A pending edit is flushed when its row is removed during reload. The same mechanism covers spin controls.
5. **Query cleanup:** selection option/value commands use the cancellable process-group query runner. The one configured nonperiodic command label does too. Hidden rows cancel observation subprocesses.
6. **Generated arguments:** Waybar, Hyprlock and WireGuard generated editor/action templates pass paths as argv elements. Fixed terminal shell scripts receive filenames as positional parameters. WireGuard interface status validates Linux interface names; generated status commands shell-quote the substituted name. The existing explicit delete confirmation remains. `sudo -n` is used only for the NOPASSWD wg-quick/wg-show paths; terminal deletion still allows sudo authentication.
7. **Entries:** clean entries refresh on reopening, while unsaved edits and delayed responses are preserved separately. Failed apply retains the typed value and reports failure.
8. **Validation and reload:** `lib/config_schema.py` validates current TOML node/action kinds, value types, numeric ranges, ownership, page IDs, redirects, generator placeholders and key service fields. `python dusky_control_center.py --validate [path]` runs without GTK or a display. The two obsolete `.conf` environment editor links were removed. Expanders reuse the canonical row builder. A CSS read failure rejects reload without replacing the working provider, and reload restores the selected page by stable ID.
9. **Services:** service rows/cards query `systemctl show` for load, runtime, substate and startup enablement in one read-only operation. They show runtime and startup separately; unavailable/missing/masked/static units are not presented as an ordinary off toggle. The existing action still means enable-and-start or disable-and-stop, and the tooltip states that contract. Reload no longer cancels an in-flight service mutation.

## Verification

- `PYTHONWARNINGS=ignore python -m unittest discover -s tests -q`: 55 tests passed. Tests cover real GTK map/unmap and reload lifecycle, subprocess success/failure/timeout and child cleanup, slider coalescing, file deletion, generated argument fidelity, headless schema validation, and service status.
- `env -u WAYLAND_DISPLAY -u DISPLAY python dusky_control_center.py --validate`: passed against all 18 configured pages.
- `python -m py_compile dusky_control_center.py lib/*.py tests/*.py`: passed.
- `/bin/sh -n -c` for all 335 configured shell command/query strings: no syntax failures. Nine generated actions now use argv.
- `/bin/sh -n -c` for the three fixed shell snippets embedded in argv actions: no syntax failures. The installed `kitty`, `sudoedit`, and `wg-quick` executables and the remaining Lua environment editor target were confirmed present.
- Real GTK construction: 18 root pages and 27 nested pages built successfully without invoking action controls.
- Disposable user systemd unit: `toggle_service_async` moved inactive/disabled to active/enabled and back; `check_unit_status_async` observed each state. The fixture unit and enablement link were removed afterward. No production service was toggled.
- Installed rfkill queries returned valid current status for Bluetooth and Wi-Fi. Fixture tests covered both block axes, absent devices, and invalid WireGuard names.
- The existing `dusky.service` user service was restarted after verification. `systemctl --user show` reported `ActiveState=active`, `SubState=running`, `Result=success`, `ExecMainStatus=0`, and a new Python main PID. Its startup journal contained no application errors.

## Limits and intentional deferrals

The tests do not execute destructive disk, VPN, firewall, installer, reset, power, or production service controls. Those helpers may have their own behavior and authorization requirements. Service status refreshes when a control is mapped or explicitly refreshed; a shared D-Bus observer for external changes while a page remains open is separate future work. Native Hyprland monitor editing, profiles, generic undo, CSS variable migration, and a language rewrite were not justified as part of these correctness fixes. No guarantee can cover all future Arch updates; rerun the validation and GTK/subprocess gates after stack changes.
