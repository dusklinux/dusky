"""Isolated Bluetooth editor fixtures; native checks never execute user hooks."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.bluetooth_proximity import BluetoothProximityEngine, initialize


class BluetoothProximityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'nested/config.toml'
        self.engine = BluetoothProximityEngine(str(self.path))
        self.engine.load_state()

    def data(self):
        return tomllib.loads(self.path.read_text(encoding='utf-8'))

    def save(self, *changes):
        result = self.engine.write_batch(list(changes))
        self.assertTrue(result[0], result)

    def test_initialize_preserves_custom_hooks_and_config(self):
        hook = self.path.parent / 'hooks/on_connect.sh'
        hook.write_text('#!/bin/sh\necho custom\n', encoding='utf-8')
        before = self.path.read_bytes()
        initialize(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('custom', hook.read_text(encoding='utf-8'))
        self.assertEqual(hook.stat().st_mode & 0o777, 0o755)

    def test_initialize_never_overwrites_a_concurrent_creator(self):
        from python.engines.bluetooth_proximity import _write_new
        path = self.path.parent / 'raced-hook.sh'
        link = os.link

        def concurrent_link(source, destination):
            Path(destination).write_text('concurrent creator\n', encoding='utf-8')
            return link(source, destination)

        with patch('os.link', side_effect=concurrent_link):
            _write_new(path, self.path, 0o755)
        self.assertEqual(path.read_text(encoding='utf-8'), 'concurrent creator\n')
        self.assertEqual({p.name for p in self.path.parent.iterdir()}, {'config.toml', 'hooks', 'raced-hook.sh'})

    def test_batch_can_enable_valid_device_and_set_phone_hooks(self):
        self.save(('mac', 'devices/0', 'AA:BB:CC:DD:EE:FF', 'string'),
                  ('enabled', 'devices/0', 'true', 'bool'),
                  ('proximity', 'devices/0', 'false', 'bool'),
                  ('on_connect', 'devices/0', '[\'"$HOME/a path/script.sh"\', "printf café"]', 'string'))
        device = self.data()['devices'][0]
        self.assertTrue(device['enabled'])
        self.assertFalse(device['proximity'])
        self.assertEqual(device['on_connect'], ['"$HOME/a path/script.sh"', 'printf café'])
        self.assertIn('[[devices]]', self.path.read_text(encoding='utf-8'))

    def test_first_setup_defaults_and_later_edits_preserve_locking(self):
        defaults = self.data()
        self.assertTrue(defaults['proximity_enabled'])
        self.assertEqual(defaults['proximity_source'], 'connection')
        self.assertTrue(defaults['direct_hyprlock_signal'])
        self.assertTrue(defaults['wake_display'])
        self.assertFalse(defaults['devices'][0]['enabled'])
        self.assertTrue(defaults['devices'][0]['proximity'])
        self.assertIn('hyprlock', defaults['on_far'][0])
        self.save(('mac', 'devices/0', 'AA:BB:CC:DD:EE:FF', 'string'),
                  ('name', 'devices/0', 'My wearable', 'string'),
                  ('enabled', 'devices/0', 'true', 'bool'))
        self.engine.load_state()
        self.assertTrue(self.engine.cache['devices/0/enabled'])
        self.save(('proximity_level', 'DEFAULT', '6', 'string'),
                  ('far_hold_secs', 'DEFAULT', '9', 'int'),
                  ('signal_timeout_secs', 'devices/0', '180', 'string'))
        saved = self.data()
        self.assertEqual(saved['on_far'], defaults['on_far'])
        self.assertTrue(saved['devices'][0]['proximity'])
        self.assertEqual(saved['proximity_level'], 6)
        self.assertEqual(saved['far_hold_secs'], 9)
        self.assertEqual(saved['devices'][0]['signal_timeout_secs'], 180)

    def test_sensing_modes_sampling_and_reconnection_round_trip(self):
        self.save(('proximity_source', 'DEFAULT', 'advertisement', 'string'),
                  ('poll_interval_ms', 'DEFAULT', '500', 'int'),
                  ('auto_connect', 'DEFAULT', 'false', 'bool'),
                  ('reconnect_interval_secs', 'DEFAULT', '30', 'int'))
        self.engine.load_state()
        self.assertEqual(self.engine.cache['proximity_source'], 'advertisement')
        self.assertEqual(self.engine.cache['poll_interval_ms'], 500)
        self.assertFalse(self.engine.cache['auto_connect'])
        self.save(('proximity_source', 'DEFAULT', 'connected', 'string'))
        self.assertEqual(self.data()['proximity_source'], 'connected')
        self.save(('proximity_source', 'DEFAULT', 'connection', 'string'))
        self.assertEqual(self.data()['proximity_source'], 'connection')
        before = self.path.read_bytes()
        for change in [('proximity_source', 'DEFAULT', 'unknown', 'string'),
                       ('poll_interval_ms', 'DEFAULT', '499', 'int'),
                       ('reconnect_interval_secs', 'DEFAULT', '4', 'int')]:
            self.assertFalse(self.engine.write_batch([change])[0])
            self.assertEqual(self.path.read_bytes(), before)

    def test_default_lock_command_uses_wrapper_and_plain_hyprlock(self):
        import subprocess
        base = Path(self.directory.name)
        home = base / 'home'
        wrapper = home / 'user_scripts/hyprlock/lock.sh'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text('#!/bin/sh\nprintf wrapper\n', encoding='utf-8')
        wrapper.chmod(0o755)
        binaries = base / 'bin'
        binaries.mkdir()
        hyprlock = binaries / 'hyprlock'
        hyprlock.write_text('#!/bin/sh\nprintf hyprlock\n', encoding='utf-8')
        hyprlock.chmod(0o755)
        env = os.environ | {'HOME': str(home), 'PATH': str(binaries)}
        command = self.data()['on_far'][0]
        for expected in ['wrapper', 'hyprlock']:
            result = subprocess.run(['/bin/sh', '-c', command], env=env, capture_output=True,
                                    text=True, timeout=5, check=True)
            self.assertEqual(result.stdout, expected)
            if wrapper.exists():
                wrapper.unlink()

    def test_hooks_distinguish_inherit_disabled_and_single_command(self):
        self.save(('on_connect', 'DEFAULT', 'printf "$PROXIMITY_DEVICE_NAME"', 'string'),
                  ('on_connect', 'devices/0', '[]', 'string'))
        self.assertEqual(self.data()['devices'][0]['on_connect'], [])
        self.assertEqual(self.engine.cache['devices/0/on_connect'], '[]')
        self.save(('on_connect', 'devices/0', '', 'string'))
        self.assertNotIn('on_connect', self.data()['devices'][0])
        self.assertEqual(self.engine.cache['devices/0/on_connect'], '')
        self.save(('on_far', 'DEFAULT', '', 'string'))
        self.assertEqual(self.data()['on_far'], [])

    def test_invalid_batch_and_command_arrays_never_commit(self):
        for changes in [
            [('enabled', 'devices/0', 'true', 'bool')],
            [('rssi_window', 'DEFAULT', '4', 'int')],
            [('on_connect', 'DEFAULT', '[1]', 'string')],
            [('on_connect', 'DEFAULT', '["cmd"]\nother=1', 'string')],
            [('far_rssi', 'DEFAULT', 'not a number', 'int')],
            [('transport', 'DEFAULT', 'unknown', 'string')],
        ]:
            before = self.path.read_bytes()
            result = self.engine.write_batch(changes)
            self.assertFalse(result[0], result)
            self.assertEqual(self.path.read_bytes(), before)

    def test_device_management_and_deleted_row(self):
        self.save(('add_device', 'controls', 'true', 'bool'))
        self.assertEqual(len(self.data()['devices']), 2)
        self.assertFalse(self.data()['devices'][1]['enabled'])
        self.save(('name', 'devices/1', 'Phone', 'string'))
        self.save(('remove_last_device', 'controls', 'true', 'bool'))
        self.assertFalse(self.engine.write_value('name', 'devices/1', 'stale', 'string')[0])
        self.save(('remove_last_device', 'controls', 'true', 'bool'))
        self.assertEqual(self.data()['devices'], [])
        self.save(('add_device', 'controls', 'true', 'bool'))
        self.assertEqual(len(self.data()['devices']), 1)

    def test_management_cannot_mix_with_settings(self):
        before = self.path.read_bytes()
        self.assertFalse(self.engine.write_batch([
            ('add_device', 'controls', 'true', 'bool'), ('notifications', 'DEFAULT', 'false', 'bool')])[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_validate_and_false_controls_do_not_rewrite(self):
        before = self.path.read_bytes()
        self.save(('validate', 'controls', 'true', 'bool'))
        self.save(('remove_last_device', 'controls', 'false', 'bool'))
        self.assertEqual(self.path.read_bytes(), before)

    def test_restart_uses_validated_saved_file_and_reports_failure(self):
        before = self.path.read_bytes()
        from subprocess import CompletedProcess
        with patch.object(self.engine, 'validate_text') as validate, patch('subprocess.run', return_value=CompletedProcess([], 1, '', 'fixture failure')) as run:
            result = self.engine.write_value('restart', 'controls', 'true', 'bool')
        self.assertFalse(result[0])
        self.assertIn('fixture failure', result[1])
        validate.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'restart', 'dusky_proximity.service'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_recovery_uses_shared_helper_without_configuration_validation_or_rewrite(self):
        from subprocess import CompletedProcess
        before = self.path.read_bytes()
        with patch.object(self.engine, 'validate_text') as validate, patch('subprocess.run', return_value=CompletedProcess([], 0, 'Bluetooth recovered.', '')) as run:
            result = self.engine.write_value('recover', 'controls', 'true', 'bool')
        self.assertTrue(result[0], result)
        self.assertIn('Bluetooth recovered.', result[1])
        validate.assert_not_called()
        self.assertEqual(Path(run.call_args.args[0][1]).name, 'bluetooth_recovery.py')
        self.assertEqual(self.path.read_bytes(), before)
        with patch('subprocess.run', return_value=CompletedProcess([], 1, '', 'restart fixture failure')):
            result = self.engine.write_value('recover', 'controls', 'true', 'bool')
        self.assertFalse(result[0])
        self.assertIn('restart fixture failure', result[1])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.engine.write_batch([
            ('recover', 'controls', 'true', 'bool'), ('notifications', 'DEFAULT', 'false', 'bool')])[0])

    def test_external_edit_and_failed_replacement_are_preserved(self):
        self.path.write_text(self.path.read_text(encoding='utf-8') + '\n# external\n', encoding='utf-8')
        before = self.path.read_bytes()
        self.assertFalse(self.engine.write_value('notifications', 'DEFAULT', 'false', 'bool')[0])
        self.assertEqual(self.path.read_bytes(), before)
        self.engine.load_state()
        with patch('os.replace', side_effect=OSError('fixture failure')):
            self.assertFalse(self.engine.write_value('notifications', 'DEFAULT', 'false', 'bool')[0])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({p.name for p in self.path.parent.iterdir()}, {'config.toml', 'hooks'})

    def test_legacy_aliases_and_single_device_migrate_without_behavior_loss(self):
        self.path.write_text('device_mac="AA:BB:CC:DD:EE:FF"\ndevice_name="Phone"\nlock_hold_secs=9\nlock_cmd="echo lock"\non_far=[]\n', encoding='utf-8')
        state = self.engine.load_state()
        self.assertEqual(state['far_hold_secs'], 9)
        self.assertEqual(state['on_far'], '["echo lock"]')
        self.save(('notifications', 'DEFAULT', 'false', 'bool'))
        data = self.data()
        self.assertNotIn('device_mac', data)
        self.assertNotIn('lock_cmd', data)
        self.assertEqual(data['on_far'], 'echo lock')
        self.assertEqual(data['devices'][0]['name'], 'Phone')

    def test_optional_thresholds_and_presets_can_be_removed(self):
        self.save(('proximity_level', 'DEFAULT', '', 'string'),
                  ('proximity_level', 'devices/0', '6', 'string'),
                  ('near_rssi', 'devices/0', '-55', 'string'))
        self.assertNotIn('proximity_level', self.data())
        self.assertEqual(self.data()['devices'][0]['near_rssi'], -55)
        self.save(('near_rssi', 'devices/0', '', 'string'))
        self.assertNotIn('near_rssi', self.data()['devices'][0])

    def test_schema_covers_all_shipped_keys_and_valid_folder_uids(self):
        source = Path(__file__).resolve().parents[3] / 'bluetooth/proximity_trigger'
        spec = importlib.util.spec_from_file_location('proximity_schema_fixture', source / 'tui_dusky_proximity.py')
        schema = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': self.directory.name}):
            spec.loader.exec_module(schema)
            _, dynamic = schema.discover_devices()
            self.assertIn('First setup:', schema.TAB_NOTICES[0]['message'])
            self.assertTrue(any(i.key == 'action_scan' for i in dynamic[4]))
            self.assertFalse(any(i.key == 'action_scan' for i in schema.SCHEMA[5]))
        schema.SCHEMA.update(dynamic)
        items = [*tab for tab in schema.SCHEMA.values()]
        uids = {i.uid for i in items}
        self.assertEqual(len(uids), len(items))
        defaults = tomllib.loads((source / 'config.toml').read_text(encoding='utf-8'))
        self.assertTrue(set(defaults) - {'devices'} <= uids)
        for i in items:
            self.assertTrue(i.extended_help)
            if i.parent_ref:
                self.assertIn(i.parent_ref, uids)
        source_control = next(i for i in items if i.key == 'proximity_source')
        self.assertEqual(source_control.options, ['connection', 'connected', 'advertisement'])
        self.assertTrue({'far_rssi', 'near_rssi', 'rssi_window', 'far_hold_secs', 'near_hold_secs'} <= uids)
        self.assertEqual(schema.DEFAULT_MODE, 'batch')
        engine = BluetoothProximityEngine(schema.TARGET_FILE)
        engine.load_state()
        result = engine.write_batch([
            ('mac', 'devices/0', 'AA:BB:CC:DD:EE:FF', 'string'),
            ('enabled', 'devices/0', 'true', 'bool'),
        ])
        self.assertTrue(result[0], result)
        schema.discover_devices()
        self.assertNotIn('First setup:', schema.TAB_NOTICES[0]['message'])
