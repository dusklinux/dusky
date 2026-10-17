#!/usr/bin/env python3
"""Full TOML configurator for Dusky Bluetooth proximity and link automation."""
import os
import sys
import shlex
import json
import tomllib
from pathlib import Path

_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[2] / 'dusky_tui'
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))
from python.frontend.core_types import ConfigItem
lazy from python.engines.bluetooth_proximity import canonical, initialize

ENGINE_TYPE = 'bluetooth_proximity'
TARGET_FILE = str(Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'dusky/settings/bluetooth_proximity/config.toml')
APP_TITLE = 'Bluetooth Proximity Trigger'
DEFAULT_MODE = 'batch'
THEME_FILE = '~/.config/matugen/generated/dusky_tui.json'
ENABLE_USER_PRESETS = False
_BINARY = Path.home() / '.local/bin/dusky_proximity'
TABS = ['General', 'Signal', 'Timing', 'Commands', 'Devices', 'Service']
TAB_NOTICES = {
    0: {'level': 'info', 'message': 'Default: Bluetooth connection state; lock on departure, unlock and wake on return. Pair/connect the target first. Ctrl+S saves; queue Service > Restart service, then Ctrl+S applies.'},
    1: {'level': 'info', 'message': 'Ignored in connection-state mode. RSSI is not a distance ruler: calibrate at your desk and departure point. A preset overrides global thresholds; clear it to use the explicit values.'},
    3: {'level': 'info', 'message': 'Enter a command or TOML string array. Wrap commands starting [ in an array. [] disables commands.'},
    4: {'level': 'info', 'message': 'Setup: pair/connect your target first; scanning is optional. Enter its identity MAC/name in its device menu and enable it. Ctrl+S saves; Service > Restart service, then Ctrl+S applies.'},
    5: {'level': 'info', 'message': 'Save edits first. Validate/Restart buttons queue actions for Ctrl+S. Diagnostics use saved settings.'},
}
_GENERAL_NOTICE = TAB_NOTICES[0]['message']


def item(label, key, default, help_text, *, group, scope='DEFAULT', type_=None, **kwargs):
    kind = type_ or ('bool' if isinstance(default, bool) else 'int' if isinstance(default, int) else 'string')
    return ConfigItem(label=label, key=key, scope=scope, type_=kind, default=default,
                      group=group, extended_help=help_text, **kwargs)


def seconds(label, key, default, help_text, *, group, minimum=0):
    return item(label, key, default, help_text, group=group, min_val=minimum, max_val=86400, step=1)


def control(label, key, help_text, *, group, **kwargs):
    return item(label, key, False, help_text, group=group, scope='controls', options=['trigger'], **kwargs)


SCHEMA = {
    0: [
        item('Proximity actions', 'proximity_enabled', True, 'Enable departure/return actions using the selected presence source. Disable for connect/disconnect hooks only.', group='Operation'),
        item('Presence source', 'proximity_source', 'connection', 'connection: lock after sustained link loss and unlock after stable reconnection; ignores RSSI and does not scan. Bluetooth range is not a three-foot boundary. connected: optional LE RSSI sensing without discovery; wrist/body/table obstruction can cause false locks. advertisement: optional scan-based sensing; can interfere with HID reconnection. Locking arms after an observed connection in both link modes.', group='Operation', type_='cycle', options=['connection', 'connected', 'advertisement']),
        item('Reconnect paired targets', 'auto_connect', False, 'Off by default: observe links managed by BlueZ or your Bluetooth controls. Unlocking still follows a returning connection. Opt-in retries initiate controller connections and can compete with input reconnection, even without discovery. Retries defer while a paired trusted input device is disconnected. Dry runs never connect.', group='Operation'),
        item('Discovery transport', 'transport', 'le', 'Used only in advertisement mode and the explicit five-second scan. le = Bluetooth LE; bredr = classic Bluetooth; auto = both supported transports. Connection-only operation does not scan.', group='Operation', type_='cycle', options=['le', 'bredr', 'auto']),
        item('Adapter path', 'adapter_path', '', 'Empty selects an adapter automatically. Set a BlueZ object path such as /org/bluez/hci0 to select one explicitly. All targets use that adapter.', group='Operation'),
        item('Silence policy', 'signal_loss_action', 'far', 'Policy when RSSI becomes unavailable: far eventually departs; hold keeps the last state and can miss departures. In connected mode a confirmed disconnection always departs after its disconnect hold, regardless of this policy.', group='Operation', type_='cycle', options=['far', 'hold']),
        item('Desktop notifications', 'notifications', True, 'Show notifications for emitted proximity and connection events.', group='Desktop'),
        item('Automatic hyprlock unlock', 'direct_hyprlock_signal', True, 'Signal a running hyprlock on confirmed proximity return. Disable for custom automation that should not unlock the screen. Actual connection hooks do not use this behavior.', group='Desktop'),
        item('Wake displays on return', 'wake_display', True, 'Wake Hyprland monitors on confirmed proximity return, independently of on_near commands.', group='Desktop'),
        item('Display settle delay (ms)', 'dpms_settle_ms', 200, 'Delay before waking displays after a proximity return.', group='Desktop', min_val=0, max_val=60000, step=50),
    ],
    1: [
        item('Proximity level', 'proximity_level', '8', '1 = strongest/closest preset; 10 = weakest/farthest signal preset. Level 8 uses far -80 and near -70 dBm. Clear this field to use explicit global thresholds. Per-device overrides take precedence.', group='Thresholds', options=['', *map(str, range(1, 11))]),
        item('Far threshold (dBm)', 'far_rssi', -80, 'Departure needs raw and filtered RSSI strictly below this threshold. Ignored when a global proximity level is set. Must be below the near threshold.', group='Thresholds', min_val=-127, max_val=20, step=1),
        item('Near threshold (dBm)', 'near_rssi', -70, 'Return needs raw and filtered RSSI at or above this threshold. Ignored when a global proximity level is set.', group='Thresholds', min_val=-127, max_val=20, step=1),
        item('Median window', 'rssi_window', 5, 'Odd sample count, 1 through 31. One disables median filtering. Smoothing rejects brief RSSI outliers.', group='Filtering', min_val=1, max_val=31, step=2),
        item('Consecutive weak samples', 'consecutive_drops', 3, 'Number of consecutive raw and filtered weak readings required in addition to the departure hold. Positive integer.', group='Filtering', min_val=1, step=1),
    ],
    2: [
        item('Connected sampling interval (ms)', 'poll_interval_ms', 1000, 'Read connected LE RSSI every 500..10000 ms without discovery. Kernel/controller measurements may be briefly cached. Lower intervals do not guarantee fresher readings.', group='Connections', min_val=500, max_val=10000, step=500),
        seconds('Reconnect retry interval (s)', 'reconnect_interval_secs', 15, 'Minimum interval between automatic connection attempt batches. Only configured paired proximity targets are attempted; in-flight batches never overlap.', group='Connections', minimum=5),
        seconds('Silence timeout (s)', 'signal_timeout_secs', 120, 'No usable measurement for this many seconds plus the far hold causes departure under the far policy. Advertisement data also refreshes reception in advertisement mode. Connected-mode disconnection uses its own shorter hold.', group='Proximity', minimum=1),
        seconds('Far hold (s)', 'far_hold_secs', 6, 'Continuous weak-reading hold; also added to the signal silence timeout.', group='Proximity'),
        seconds('Near hold (s)', 'near_hold_secs', 2, 'Continuous strong-reading hold before return. Fresh measurements are required.', group='Proximity'),
        seconds('Maximum sample gap (s)', 'sample_max_gap_secs', 10, 'Longer RSSI gaps clear median and strength-hold evidence. Data heartbeats prove reception but do not complete strength holds.', group='Proximity', minimum=1),
        seconds('Startup/resume grace (s)', 'startup_grace_secs', 10, 'Minimum grace before departure after startup or an observation reset.', group='Proximity'),
        seconds('Return cooldown (s)', 'return_cooldown_secs', 3, 'Minimum interval between departure and return actions; stale samples cannot complete a return.', group='Proximity'),
        seconds('Connect debounce (s)', 'connect_hold_secs', 2, 'Require a stable Bluetooth link before on_connect and connection-state return/unlock. Duplicate updates do not restart the hold.', group='Connections'),
        seconds('Disconnect debounce (s)', 'disconnect_hold_secs', 5, 'Require disconnection to persist before on_disconnect and, in connected proximity mode, departure/locking. A brief reconnection cancels departure. Connection-state mode returns after connect debounce; connected RSSI mode requires fresh strong readings.', group='Connections'),
        item('Run initial link hooks', 'run_initial_connection_hooks', False, 'Also emit connect or disconnect for the initial BlueZ link state, subject to debounce. Normally startup only establishes a baseline.', group='Connections'),
    ],
    3: [
        item('On departure', 'on_far', json.dumps(tomllib.loads(Path(__file__).with_name('config.toml').read_text(encoding='utf-8'))['on_far']), 'Aggregate departure command list. Default locks the screen. Any near target maintains presence; all proximity targets must depart to run this hook. Commands within an event run in order.', group='Proximity'),
        item('On return', 'on_near', '[]', 'Aggregate proximity return command list. Built-in unlock and display waking have separate toggles. Commands from separate events can overlap.', group='Proximity'),
        item('On connection', 'on_connect', '[]', 'Default command list for actual link connection to this computer. Devices may replace this list. Example: [\'"$HOME/user_scripts/phone_connected.sh"\'].', group='Connections'),
        item('On disconnection', 'on_disconnect', '[]', 'Default command list for actual link disconnection to this computer; not inferred from RSSI or a phone-to-watch connection. Hook variables: PROXIMITY_EVENT, PROXIMITY_STATE, PROXIMITY_DEVICE_MAC, PROXIMITY_DEVICE_NAME, PROXIMITY_RSSI.', group='Connections'),
    ],
    4: [],
    5: [
        control('Validate saved configuration', 'validate', 'Queue a native --check-config check; Ctrl+S runs it. Does not scan, replace the file or execute configured hooks.', group='Service'),
        control('Restart service', 'restart', 'Save pending edits first. Queue validation and restart, then Ctrl+S runs it. Does not change service enablement.', group='Service'),
        control('Recover Bluetooth', 'recover', 'Queue Bluetooth recovery; Ctrl+S runs it. Briefly disconnects Bluetooth devices, restarts BlueZ, unblocks and powers on adapters, then restores proximity monitoring only if it was running. Does not change pairing or configuration.', group='Service'),
        item('Live signal / calibration dry run', 'action_test', shlex.join([str(_BINARY), '--config', TARGET_FILE, '--test']), 'Observe the saved presence source, connection transitions and (in signal modes) live RSSI without hooks or connection attempts from this process. Pair/connect the target first for connected mode. Compare desk versus departure readings before tuning thresholds. The running service remains active; Ctrl+C ends the test.', group='Diagnostics', type_='action', force_interactive=True),
    ],
}

def discover_devices():
    path = Path(TARGET_FILE)
    initialize(path)
    data = canonical(tomllib.loads(path.read_text(encoding='utf-8')))
    if not any(device.get('enabled', True) for device in data['devices']):
        TAB_NOTICES[0]['message'] = 'First setup: pair/connect the target with Bluetooth settings; Devices > Scan can find its identity address. Enter MAC/name and enable it; Ctrl+S saves; queue Service > Restart service, then Ctrl+S applies. Default sensing uses connection state; RSSI settings apply only to optional signal modes.'
    else:
        TAB_NOTICES[0]['message'] = f"Signal source: {data.get('proximity_source', 'connection')}. " + _GENERAL_NOTICE
    rows = [
        item('Scan known/live devices', 'action_scan', shlex.join([str(_BINARY), '--config', TARGET_FILE, '--scan']), 'Optional discovery diagnostic: turn Bluetooth on and make the target discoverable or advertising. Temporarily scan for five seconds using saved adapter/transport, even in connected mode. Pair/connect through normal Bluetooth settings. Copy its MAC into the device menu, enter its name and enable it; Ctrl+S saves. Restart the service to activate. For rotating addresses, pair the device and use its resolved BlueZ identity.', group='Setup', type_='action', force_interactive=True),
        control('Add disabled device', 'add_device', 'Save pending edits first. Queue a blank disabled target, Ctrl+S commits it, then F5 displays it. Fill its MAC before enabling.', group='Management'),
        control('Remove last device', 'remove_last_device', 'Save edits first. Queue removal of the final entry; Ctrl+S commits it, then F5 refreshes rows. Other indices remain stable. Disable other targets to stop using them.', group='Management', confirm_message='Remove the last configured device?'),
    ]
    for index, device in enumerate(data['devices']):
        scope = f'devices/{index}'
        parent = f'{scope}.device_menu'
        group = f'Device {index + 1}'
        rows.append(item(f"{index + 1}: {device.get('name', 'Bluetooth Device')}", 'device_menu', None, 'Configure this target. Empty per-device overrides inherit global settings. Connect/disconnect always means the link to this computer.', group=group, scope=scope, type_='menu', is_parent=True))
        specs = [
            ('MAC address', 'mac', '', 'Bluetooth identity address, AA:BB:CC:DD:EE:FF. Pair rotating-address devices and use the resolved address shown by BlueZ.'),
            ('Device name', 'name', 'Bluetooth Device', 'Friendly name used in logs, notifications and hook environment.'),
            ('Enabled', 'enabled', False, 'Enable this target. A valid unique MAC is required. Disabled entries may be blank; resetting a target disables it.'),
            ('Use for proximity', 'proximity', True, 'Participate in aggregate near/far decisions using the global presence source. Connection state requires a link; connected RSSI requires an LE link to this computer. False keeps only connection hooks.'),
            ('Level override', 'proximity_level', '', 'Empty inherits global thresholds. Otherwise 1..10; explicit device thresholds override this preset.'),
            ('Far RSSI override', 'far_rssi', '', 'Empty inherits global/preset departure threshold; otherwise an integer dBm value -127..20.'),
            ('Near RSSI override', 'near_rssi', '', 'Empty inherits global/preset approach threshold; otherwise an integer dBm value -127..20.'),
            ('Silence timeout override', 'signal_timeout_secs', '', 'Empty inherits global timeout; otherwise positive seconds, up to 86400.'),
            ('On connection override', 'on_connect', '', 'Empty inherits global hooks. [] explicitly disables them. A command or TOML string array replaces the global list.'),
            ('On disconnection override', 'on_disconnect', '', 'Empty inherits global hooks. [] explicitly disables them. A command or TOML string array replaces the global list.'),
        ]
        rows.extend(item(label, key, default, help_text, group=group, scope=scope, parent_ref=parent)
                    for label, key, default, help_text in specs)
    return [4], {4: rows}


DEFERRED_LOAD = discover_devices

if __name__ == '__main__':
    os.execv(sys.executable, [sys.executable, str(_DUSKY_TUI_ROOT / 'python/main/main.py'), str(Path(__file__).resolve()), *sys.argv[1:]])
