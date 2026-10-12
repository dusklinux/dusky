# Dusky Bluetooth automation

Rust Bluetooth proximity and connection automation for Linux, BlueZ and Hyprland
on Wayland. Locking is the default departure action. Every action is configurable
in TOML; phones, beacons, headsets and wearables can have different connection hooks.

## Install on a fresh system

```bash
~/user_scripts/bluetooth/proximity_trigger/setup.sh
```

This downloads missing dependencies from crates.io, builds the versions in
`Cargo.lock`, installs a real executable at `~/.local/bin/dusky_proximity`,
creates the configuration and four optional hook
scripts, installs/enables the user service, and preserves existing settings/scripts.
It requires a Linux Rust/Cargo toolchain, linker, Bash and systemd. Network access is
needed for dependencies that are not already in Cargo's cache. The installer builds
for the compiler's native host, even if `CARGO_BUILD_TARGET` is set for another architecture.

Created configuration layout:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/bluetooth_proximity/
  config.toml
  hooks/on_far.sh
  hooks/on_near.sh
  hooks/on_connect.sh
  hooks/on_disconnect.sh
```

Fresh defaults select proximity locking: hyprlock on departure, automatic unlock
and display waking on confirmed return. No enabled device is shipped. Configure
your target through the TUI steps below, then restart the service.
With no enabled devices the program exits successfully without
Bluetooth/session actions; the service remains enabled for future sessions. Do not
expect an address supplied by the installer: hardware identity belongs in your TOML.

The daemon also creates this default layout if launched directly with no configuration.
`--init-config` explicitly creates a layout (including at `--config PATH`) without
running actions. Creation is atomic and does not overwrite existing files. A missing
explicit `--config PATH` is an error unless `--init-config` is supplied.

```bash
~/.local/bin/dusky_proximity --init-config
~/.local/bin/dusky_proximity --scan
~/.local/bin/dusky_proximity --check-config
systemctl --user restart dusky_proximity.service
journalctl --user -u dusky_proximity.service -f
```

The service follows `graphical-session.target` and runs after session preparation.
The Hyprland session should import `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, and
`HYPRLAND_INSTANCE_SIGNATURE` into the user manager. Setup imports its config base
(`XDG_CONFIG_HOME`) so daemon and installer resolve the same location. If only one
Hyprland socket exists, display waking can discover it without the instance signature.
Configuration errors exit with status 2 and stop automatic retries; Bluetooth/bus
failures are retried by systemd. Restart after correcting the configuration.

With an adapter present but powered off, the daemon sleeps on D-Bus events:
proximity timers and periodic health polling are paused, and startup does not
retry discovery. Pending connection hooks finish their debounce before timers
sleep. A power-on event reacquires discovery and starts fresh signal evidence.
Radio-off silence never triggers departure. Adapter removal or BlueZ owner loss
restarts observation through systemd. `--scan` requires a powered radio.
Changes to another client's scan filter can briefly interrupt controller discovery.
An owned discovery session gets five seconds to settle before forced recovery;
stale proximity evidence is cleared during that interruption. Persistent bus or
controller failures still use the service's normal restart policy.
Bluetooth methods allow up to ten seconds for a reply, including slow controller
discovery transitions. A stalled backend therefore takes longer to report an
error; failures identify the operation that failed.
Discovery changes from the daemon, scan and test commands are serialized using
a shared file lock. This avoids overlapping pending BlueZ discovery replies.
The lock is released automatically on process exit; it is never held while
observing devices or sleeping with the radio off. Competing discovery changes
wait up to 30 seconds. Other applications do not participate in this lock.

## Configure with the Dusky TUI

```bash
~/user_scripts/bluetooth/proximity_trigger/tui_dusky_proximity.py
```

The editor targets the deployed TOML under `${XDG_CONFIG_HOME:-$HOME/.config}`.
It creates missing configuration/hook files and exposes all current global and
per-device settings, including command lists and optional overrides. Install the
daemon with `setup.sh` first: saves use its native `--check-config` validator.
Ctrl+S commits a validated batch; the Service tab validates/restarts the saved
configuration. Restart is explicit. Invalid batches and stale file writes are rejected.
Device-management and Validate/Restart buttons queue operations; Ctrl+S executes them.
The editor starts in batch mode so MAC, thresholds and enablement can be validated
together. Its scan action uses the saved adapter and transport.

### First setup: proximity locking

1. Turn Bluetooth on and make your target discoverable or advertising.
2. In **Devices**, run **Scan known/live devices** and copy the target's MAC.
3. Open **1: Bluetooth Device**, fill its MAC and name, and enable it. Leave
   **Use for proximity** enabled. Press **Ctrl+S** to save the complete batch.
4. In **Service**, select **Restart service**, then press **Ctrl+S** to activate.
5. Use **Live RSSI dry run** in Service to observe signal readings, and tune
   **Signal** and **Timing** for your device/environment. This diagnostic process
   does not execute hooks; the separately running service still does.

The default departure command uses `~/user_scripts/hyprlock/lock.sh` when executable,
otherwise `hyprlock` from PATH. Hyprlock and its configuration must be installed.
Scan is an observation tool: pairing and Bluetooth power are managed with your
normal Bluetooth controls. Devices with rotating addresses should be paired and
configured using their resolved BlueZ identity. Later changes to actions, device
overrides, signal policy and timings follow the same validated save/restart flow.

In Commands, enter a shell command or TOML array of strings. Empty global hooks
and `[]` disable commands. Empty device overrides inherit global hooks; device
`[]` explicitly disables inheritance. Clear optional numeric overrides to inherit.
Clear the global level to use explicit global RSSI thresholds.
Wrap shell commands beginning with `[` in a TOML string array to distinguish them
from an array, for example `['[ -f "$HOME/file" ] && printf found']`.

The Devices tab adds disabled blank targets and can remove the last target;
disable other entries to stop using them. Save edits before device management,
then press F5 to refresh rows. Editing regenerates TOML and removes comments,
while preserving settings. Profiles are disabled because device identities should
not be reset by a generic settings profile.

## Connection-only phone automation

This example runs commands when a phone's actual Bluetooth connection **to this
computer** changes. It does not scan, infer distance, lock automatically, or unlock
through the built-in proximity actions. Pair/connect the phone through your normal
Bluetooth tools; this observer does not create or repair the phone connection.

```toml
proximity_enabled = false
connect_hold_secs = 0
disconnect_hold_secs = 2
run_initial_connection_hooks = false
notifications = true

[[devices]]
mac = "AA:BB:CC:DD:EE:FF"  # replace with your phone's identity address
name = "Phone"
enabled = true
on_connect = ['"$HOME/user_scripts/phone_connected.sh"']
on_disconnect = ['"$HOME/user_scripts/phone_disconnected.sh"']
```

Multiple devices can each specify their own `on_connect`/`on_disconnect`. Omitted
per-device hooks inherit global hooks; explicit `[]` disables that global hook for
that device. Hook lists replace the global list rather than appending to it.
`proximity = false` excludes one device from distance decisions while leaving its
connection hooks active, so a beacon can drive locking while a phone drives other scripts.

Startup seeds link state from BlueZ without treating cached RSSI as live reception.
Hooks normally run only for subsequent changes. `run_initial_connection_hooks = true`
also runs the applicable initial connect/disconnect hook, subject to its hold time.
Reconnection during the disconnect hold cancels that pending hook. Duplicate updates
do not restart a hold. Device-object removal counts as disconnection. An unavailable
observer/BlueZ owner is not itself reported as a physical connection change; a new
observer seeds its baseline again. Connection hooks concern this computer's link,
not a phone's connection to a watch or another host.

## Proximity settings

All global settings must precede `[[devices]]`. Keys after a device header apply
only to that device. Unknown/misplaced keys are rejected. The complete commented
`config.toml` shipped here documents the settings and default commands.

| Global setting | Shipped value | Meaning |
|---|---:|---|
| `proximity_enabled` | true | Enable distance actions; false means connection hooks only |
| `transport` | `le` | Proximity discovery: `le`, `bredr`, or `auto` |
| `adapter_path` | empty | Auto-select adapter; or specify its BlueZ object path |
| `proximity_level` | 8 | Signal threshold preset; remove to use explicit thresholds |
| `far_rssi` / `near_rssi` | -80 / -70 | Departure below / approach at-or-above thresholds |
| `rssi_window` | 5 | Median window, odd 1–31; 1 disables smoothing |
| `consecutive_drops` | 3 | Consecutive weak samples required after filtering |
| `far_hold_secs` | 6 | Continuous weak hold; also added to silence timeout |
| `near_hold_secs` | 2 | Continuous strong hold before approach |
| `sample_max_gap_secs` | 10 | Larger RSSI gaps reset strength holds and filtering |
| `signal_loss_action` | `far` | `far`: depart after silence; `hold`: retain last proximity |
| `signal_timeout_secs` | 120 | Silence allowance before additional departure hold |
| `startup_grace_secs` | 10 | Departure grace after startup or observation reset |
| `return_cooldown_secs` | 3 | Minimum interval from departure to approach actions |
| `connect_hold_secs` / `disconnect_hold_secs` | 0 / 2 | Connection event debounce |
| `run_initial_connection_hooks` | false | Run hooks for startup link state too |
| `direct_hyprlock_signal` | true | On proximity approach, SIGUSR1 unlock via stable pidfd |
| `wake_display` / `dpms_settle_ms` | true / 200 | On proximity approach, wake monitors after delay |
| `notifications` | true | Desktop notifications for emitted events |

The preset replaces global explicit RSSI thresholds. A per-device preset replaces
the global thresholds; explicit per-device `far_rssi`/`near_rssi` then override that
preset. Per-device `signal_timeout_secs` overrides the global silence allowance.
Other proximity/debounce timers are global. CLI `--far-rssi`, `--near-rssi` and
`--timeout` override global values; explicit per-device overrides still take precedence.

Each `[[devices]]` supports `mac`, `name`, `enabled`, `proximity`, optional
`proximity_level`, `far_rssi`, `near_rssi`, `signal_timeout_secs`, `on_connect`,
and `on_disconnect`. Enabled MACs must be unique. Disabled entries may have an empty
MAC. Devices whose addresses rotate need the paired/resolved identity address that
BlueZ uses, not a temporary advertising address.

| Level | Departure below | Approach at or above |
|---|---:|---:|
| 1 | -45 | -35 |
| 2 | -50 | -40 |
| 3 | -55 | -45 |
| 4 | -60 | -50 |
| 5 | -65 | -55 |
| 6 | -70 | -60 |
| 7 | -75 | -65 |
| 8 | -80 | -70 |
| 9 | -85 | -75 |
| 10 | -90 | -80 |

Levels are signal presets, not metres. Body position, interference and hardware
change RSSI. Level 10 cannot promise a distance immediately before disconnection.
Use live measurements to select thresholds and timers for the actual device:

```bash
~/.local/bin/dusky_proximity --test
~/.local/bin/dusky_proximity --far-rssi -82 --near-rssi -68 --check-config
```

`--test` runs observations but executes no hooks, notifications, unlock signals,
or display commands. An independently running service remains active. `--scan`
shows cached names/pairing status but labels only readings received during its
five-second scan as live RSSI. Scan can use `--adapter` and `--transport` overrides.
Scan, test and validation modes are mutually exclusive.

Any confirmed near proximity target maintains aggregate presence. All participating
targets must be confirmed far/absent before departure. Connection-only targets do
not affect that decision. Initial presence establishes a baseline without approach
hooks/unlock. Strength holds require fresh RSSI measurements; middle-band samples
reset pending approach holds. Advertisement data proves reception without fabricating
strength measurements. RSSI 127 is unavailable and ignored. Suspend/stall, radio-off,
and discovery recovery clear stale evidence; valid connection events remain independent.

With `signal_loss_action = "far"`, complete silence locks after about 120 + 6 seconds
at the shipped settings. A nearby wearable connected to a phone can be silent too:
**no finite timeout guarantees freedom from false locks while the target is silent.**
`"hold"` avoids silence-driven departures but can miss a real departure during silence.
Neither policy supplies missing radio measurements. Connected devices need to keep
advertising to support reliable passive distance estimation. These choices apply to
any Bluetooth target, not specifically to watches.

## Commands and hooks

`on_far`, `on_near`, `on_connect` and `on_disconnect` accept a shell command or array.
Empty arrays disable hooks. The default `on_far` locks through the existing Dusky
lock script if present, otherwise through `hyprlock`. For custom proximity automation,
replace the hooks and disable `direct_hyprlock_signal`/`wake_display` if unwanted.

Commands are passed unchanged to `/bin/sh -c`. Whitespace-only commands are skipped;
meaningful leading/trailing whitespace, including continued newlines, is preserved.
Quote paths and use literal `$HOME`:

```toml
on_far = ['playerctl pause', '"$HOME/user_scripts/hyprlock/lock.sh"']
on_near = ['printf "Returned: %s\n" "$PROXIMITY_DEVICE_NAME"']
```

Within an event, commands run in order and later commands still run after a failure.
Put a long-running locker last or background it explicitly. Separate event tasks can
overlap so return can unlock a running locker; custom scripts must implement any
cross-event ordering they need. Output/errors are visible in the journal. The default
lock command avoids duplicate hyprlock launches. Restarting/stopping the observer
leaves active lockers and hooks running (`KillMode=process`).

Environment provided to each hook:

- `PROXIMITY_EVENT`: `far`, `near`, `connect`, or `disconnect`
- `PROXIMITY_STATE`: `FAR`, `NEAR`, or `UNKNOWN` for connection hooks
- `PROXIMITY_DEVICE_MAC`, `PROXIMITY_DEVICE_NAME`
- `PROXIMITY_RSSI`: filtered RSSI for strength transitions, or `N/A`

Existing `lock_rssi`/`unlock_rssi`, `lock_hold_secs`/`unlock_hold_secs`,
`lock_cooldown_secs`, and `on_lock`/`on_unlock` remain accepted aliases. Legacy
`device_mac`/`device_name` works when no device list is configured. `lock_cmd` and
`unlock_cmd`, when present, override the corresponding proximity hook list. Do not
specify a canonical key and its alias together.

## Dependency updates and verification

Keep `Cargo.lock` in the source distribution. Builds use `--locked`, so a build cannot
silently change dependency versions, and reuse Cargo's normal download cache.
Rust and Python both parse TOML 1.1. Startup discards queued changes already covered
by its initial BlueZ snapshot, preventing old connection events from being replayed.
Builds use a unique temporary directory and atomic executable replacement. `/tmp`
may be RAM or disk; there is no requirement for a particular mount/filesystem.
Include `hook-example.sh` in the source distribution for configuration initialization.

```bash
cargo test --locked --manifest-path ~/user_scripts/bluetooth/proximity_trigger/Cargo.toml
cargo clippy --locked --all-targets --manifest-path ~/user_scripts/bluetooth/proximity_trigger/Cargo.toml -- -D warnings
shellcheck ~/user_scripts/bluetooth/proximity_trigger/{build,setup,hook-example}.sh
```

Build and development commands download missing dependencies as needed. Integration
tests require `dbus-daemon` and use an isolated bus; they do not change live Bluetooth connections.
Tests cover configuration creation/preservation, event baselines/debounce, phone
hook execution, RSSI/timeout policies, discovery recovery, BlueZ owner changes,
radio-off sleep without polling, power-on recovery, adapter removal, TOML 1.1,
600,000 mixed signal inputs, a 4,096-message burst, 128 controller recovery cycles,
slow discovery replies exceeding three seconds,
concurrent initialization and shell command preservation/ordering,
pidfd signalling and socket reply handling. Hardware distance and real suspend/resume
still need target-system validation. Check final ISO versions/features at assembly;
development-machine checks do not establish the final ISO baseline.

References: [BlueZ adapter API](https://bluez.readthedocs.io/en/latest/adapter-api/),
[BlueZ device API](https://bluez.readthedocs.io/en/latest/device-api/),
[D-Bus ObjectManager](https://dbus.freedesktop.org/doc/dbus-specification.html#standard-interfaces-objectmanager),
[hyprlock signals](https://wiki.hypr.land/Hypr-Ecosystem/hyprlock/#user-signals),
[Cargo build](https://doc.rust-lang.org/cargo/commands/cargo-build.html).
