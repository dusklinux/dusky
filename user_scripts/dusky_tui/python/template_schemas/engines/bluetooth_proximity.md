# Engine: `bluetooth_proximity`

- Class: `BluetoothProximityEngine` in `engines/bluetooth_proximity.py`.
- Target: `${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/bluetooth_proximity/config.toml`.
- Schema: `~/user_scripts/bluetooth/proximity_lock/tui_dusky_proximity.py`.

## Bindings

Global TOML keys use `scope="DEFAULT"`. Device keys use `scope="devices/N"`,
where N is the zero-based index in the TOML `devices` array. Dynamic device rows
are populated by schema `DEFERRED_LOAD` at startup and F5.

All global/per-device fields supported by the daemon are exposed. Deprecated
aliases and legacy single-device settings are normalized when saving without
changing their behavior. Unknown settings remain subject to native validation.

Optional numeric overrides are string rows: empty removes the binding (inherit).
An empty global `proximity_level` removes the preset, enabling explicit thresholds.
Hook rows accept a shell command or a TOML string array, for example
`['"$HOME/a path/script.sh"', 'playerctl pause']`. State displays hooks as TOML
arrays, avoiding ambiguity with quoted command paths. Global empty input disables
the hook. Per-device empty input inherits; `[]` explicitly disables that hook.

## Controls

Momentary bool triggers in scope `controls`, default False:

- `add_device`: append a blank disabled target.
- `remove_last_device`: remove the final device; previous indices stay stable.
- `validate`: check the saved TOML without replacing it.
- `restart`: check the saved TOML, then restart `dusky_proximity.service`.

False control writes are no-ops. Structure/service controls must be separate
from ordinary batch changes. Save pending edits before running them; press F5
following device management. Disable arbitrary targets to exclude them.
In the default batch mode these buttons queue operations for Ctrl+S.

## Save behavior

Default mode is batch (Ctrl+S). Every candidate is checked by the installed
`~/.local/bin/dusky_proximity --config PATH --check-config`; install via
`proximity_lock/setup.sh` before saving. Candidate validation never executes hooks
or scans Bluetooth. Invalid batches leave the target untouched. Service restarts
are explicit and report failures; the engine never changes enablement.

Uses shared snapshot and atomic-write helpers, serializes engine operations,
rejects stale writes, preserves ownership/mode and syncs commits. Regenerates
TOML, discarding comments and retaining `[[devices]]` tables. Missing config and
four optional executable hook files are provisioned from the tracked daemon
source templates; existing files are preserved. Profiles are disabled to avoid
resetting device identities through generic profile snapshots.
