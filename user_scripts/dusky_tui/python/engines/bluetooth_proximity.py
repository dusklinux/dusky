"""Dusky Bluetooth TOML editor: indexed devices, hooks and native validation."""
import tomllib
lazy import os
from pathlib import Path
lazy import subprocess
lazy import tempfile

from python.engines.toml import TomlEngine
from python.shared.config_io import atomic_write, boolean, read_text

_SOURCE = Path(__file__).resolve().parents[3] / 'bluetooth/proximity_trigger'
HOOKS = frozenset({'on_far', 'on_near', 'on_connect', 'on_disconnect'})
OPTIONAL = frozenset({'proximity_level', 'far_rssi', 'near_rssi', 'signal_timeout_secs'})
ALIASES = frozendict({
    'lock_rssi': 'far_rssi', 'unlock_rssi': 'near_rssi',
    'lock_hold_secs': 'far_hold_secs', 'unlock_hold_secs': 'near_hold_secs',
    'lock_cooldown_secs': 'return_cooldown_secs',
    'on_lock': 'on_far', 'far_commands': 'on_far', 'far_scripts': 'on_far',
    'lock_commands': 'on_far', 'lock_scripts': 'on_far',
    'on_unlock': 'on_near', 'near_commands': 'on_near', 'near_scripts': 'on_near',
    'unlock_commands': 'on_near', 'unlock_scripts': 'on_near',
    'connect_commands': 'on_connect', 'connect_scripts': 'on_connect',
    'disconnect_commands': 'on_disconnect', 'disconnect_scripts': 'on_disconnect',
})
DEVICE_DEFAULTS = frozendict({'mac': '', 'name': 'Bluetooth Device', 'enabled': True, 'proximity': True})


def canonical(data: dict) -> dict:
    """Normalize accepted legacy bindings without masking duplicate aliases."""
    for old, new in ALIASES.items():
        if old in data:
            if new in data:
                raise ValueError(f'Duplicate settings: {old} and {new}')
            data[new] = data.pop(old)
    for old, new in [('lock_cmd', 'on_far'), ('unlock_cmd', 'on_near')]:
        if old in data:
            data[new] = data.pop(old)
    if not data.get('devices') and data.get('device_mac'):
        data['devices'] = [{'mac': data['device_mac'], 'name': data.get('device_name', 'Bluetooth Device')}]
    data.pop('device_mac', None)
    data.pop('device_name', None)
    devices = data.setdefault('devices', [])
    if not isinstance(devices, list) or not all(isinstance(d, dict) for d in devices):
        raise ValueError('devices must be a TOML array of tables')
    for device in devices:
        for old, new in [('lock_rssi', 'far_rssi'), ('unlock_rssi', 'near_rssi')]:
            if old in device:
                if new in device:
                    raise ValueError(f'Duplicate device settings: {old} and {new}')
                device[new] = device.pop(old)
    return data


def initialize(path: Path) -> None:
    """Provision only missing files from the same templates as the daemon."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_new(path, _SOURCE / 'config.toml', 0o644)
    hooks = path.parent / 'hooks'
    hooks.mkdir(exist_ok=True)
    for event in ['far', 'near', 'connect', 'disconnect']:
        hook = hooks / f'on_{event}.sh'
        _write_new(hook, _SOURCE / 'hook-example.sh', 0o755)


def _write_new(path: Path, source: Path, mode: int) -> None:
    """Publish a complete inode without overwriting a concurrent creator."""
    if path.exists():
        return
    with tempfile.NamedTemporaryFile(dir=path.parent) as stream:
        stream.write(source.read_bytes())
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        try:
            os.link(stream.name, path)
        except FileExistsError:
            pass


class BluetoothProximityEngine(TomlEngine):
    """Reuse shared atomic I/O; every saved document passes the Rust validator."""

    @staticmethod
    def state(data: dict) -> dict:
        defaults = tomllib.loads((_SOURCE / 'config.toml').read_text(encoding='utf-8'))
        defaults.pop('devices')
        defaults['proximity_level'] = ''  # absent preset means explicit thresholds
        state = defaults | {k: v for k, v in data.items() if k != 'devices'}
        state['proximity_level'] = str(data.get('proximity_level', ''))
        for key in HOOKS:
            val = state[key]
            state[key] = TomlEngine._format_val([val] if isinstance(val, str) else val)
        for index, device in enumerate(data['devices']):
            values = dict(DEVICE_DEFAULTS) | device
            for key in OPTIONAL:
                values[key] = str(device.get(key, ''))
            for key in ['on_connect', 'on_disconnect']:
                val = device.get(key)
                values[key] = '' if val is None else TomlEngine._format_val([val] if isinstance(val, str) else val)
            for key, val in values.items():
                state[f'devices/{index}/{key}'] = val
        for key in ['add_device', 'remove_last_device', 'validate', 'restart']:
            state[f'controls/{key}'] = False
        return state

    def load_state(self) -> dict:
        with self._lock:
            self.cache = {}
            self._loaded = False
            initialize(self.config_path)
            text, self._snapshot = read_text(self.config_path)
            self.cache = self.state(canonical(tomllib.loads(text)))
            self._loaded = True
            return self.cache

    @staticmethod
    def dump(data: dict) -> str:
        text = TomlEngine._dump_toml({k: v for k, v in data.items() if k != 'devices'})
        if not data['devices']:
            # An explicit empty list prevents fallback to legacy device bindings.
            return text + '\ndevices = []\n'
        for device in data['devices']:
            text += '\n[[devices]]\n'
            for key, val in device.items():
                text += f'{TomlEngine._quote_key(key)} = {TomlEngine._format_val(val)}\n'
        return text

    @staticmethod
    def validate_text(text: str) -> None:
        binary = Path.home() / '.local/bin/dusky_proximity'
        if not binary.is_file():
            raise ValueError('Run proximity_trigger/setup.sh first to install the native configuration validator.')
        with tempfile.TemporaryDirectory(prefix='dusky-proximity-check-') as directory:
            path = Path(directory) / 'config.toml'
            path.write_text(text, encoding='utf-8')
            result = subprocess.run([str(binary), '--config', str(path), '--check-config'],
                                    capture_output=True, text=True, encoding='utf-8', timeout=10)
        if result.returncode:
            raise ValueError(result.stderr.strip() or result.stdout.strip() or 'Configuration validation failed')

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, 'No pending changes.', ''
        with self._lock:
            try:
                text, snapshot = read_text(self.config_path)
                if not self._loaded:
                    return False, 'Load configuration before editing. Reload required.', ''
                if snapshot != self._snapshot:
                    return False, 'Configuration changed externally. Reload required.', ''
                changes = [(key, scope, val, kind) for key, scope, val, kind in changes
                           if scope != 'controls' or boolean(val)]
                if not changes:
                    return True, 'No pending changes.', ''
                service_actions = [key for key, scope, _, _ in changes
                                   if scope == 'controls' and key in {'validate', 'restart'}]
                if service_actions:
                    if len(changes) != 1:
                        raise ValueError('Save pending edits before service actions.')
                    self.validate_text(text)
                    if service_actions[0] == 'restart':
                        result = subprocess.run(['systemctl', '--user', 'restart', 'dusky_proximity.service'],
                                                capture_output=True, text=True, encoding='utf-8', timeout=15)
                        if result.returncode:
                            return False, 'Service restart failed: ' + result.stderr.strip(), ''
                        return True, 'Configuration valid. Service restarted.', ''
                    return True, 'Saved configuration is valid.', ''
                data = canonical(tomllib.loads(text))
                structural = [key for key, scope, val, _ in changes
                              if scope == 'controls' and key in {'add_device', 'remove_last_device'} and boolean(val)]
                if structural and len(changes) != 1:
                    raise ValueError('Save pending edits before adding or removing devices.')
                for key, scope, val, kind in changes:
                    if scope == 'controls':
                        match key:
                            case 'add_device':
                                data['devices'].append({'mac': '', 'name': 'Bluetooth Device', 'enabled': False, 'proximity': True})
                            case 'remove_last_device':
                                if data['devices']:
                                    data['devices'].pop()
                            case _:
                                raise ValueError(f'Unknown control: {key}')
                        continue
                    if scope == 'DEFAULT':
                        target = data
                    elif scope.startswith('devices/') and scope[8:].isdigit():
                        index = int(scope[8:])
                        if index >= len(data['devices']):
                            raise ValueError('Device no longer exists. Press F5 to refresh.')
                        target = data['devices'][index]
                    else:
                        raise ValueError(f'Unknown scope: {scope}')
                    if key in HOOKS:
                        if not str(val).strip():
                            if target is data:
                                target[key] = []
                            else:
                                target.pop(key, None)
                        elif str(val).lstrip().startswith('['):
                            document = tomllib.loads(f'commands = {val}')
                            if set(document) != {'commands'}:
                                raise ValueError('Enter only a TOML array of command strings.')
                            parsed = document['commands']
                            if not isinstance(parsed, list) or not all(isinstance(c, str) for c in parsed):
                                raise ValueError('Hooks require a shell command or TOML array of strings.')
                            target[key] = parsed
                        else:
                            target[key] = str(val)
                    elif key in OPTIONAL and (target is not data or key == 'proximity_level'):
                        if str(val).strip():
                            target[key] = int(val)
                        else:
                            target.pop(key, None)
                    elif kind == 'bool':
                        target[key] = boolean(val)
                    elif kind == 'int':
                        target[key] = int(val)
                    else:
                        target[key] = str(val)
                rendered = self.dump(data)
                tomllib.loads(rendered)
                self.validate_text(rendered)
                self._snapshot = atomic_write(self.config_path, rendered, snapshot) if rendered != text else snapshot
                self.cache = self.state(data)
                return True, 'Configuration valid. Saved; restart service to apply.', ''
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                return False, str(exc), ''
