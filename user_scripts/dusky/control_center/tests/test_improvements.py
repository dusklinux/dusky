"""Behavior checks for confirmed control center reliability fixes."""

import copy
import os
import subprocess
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import dusky_control_center as cc
from gi.repository import Gio, GLib
from lib import actions, radio_status, rows, service_manager, utility, wireguard_status
from lib.config_schema import validate_config


def drain_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Async callback did not finish")


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid = tomllib.loads(Path(cc.SCRIPT_DIR / cc.CONFIG_FILENAME).read_text())

    def test_real_config_and_semantic_failures(self):
        validate_config(self.valid)
        samples = [
            (lambda d: d['pages'][0]['layout'][0]['items'][0].update(type='invented'), 'type'),
            (lambda d: d['pages'][1].update(id='home'), 'duplicate page ID'),
            (lambda d: d['pages'][0]['layout'][0]['items'][0]['on_toggle']['enabled'].update(command=42), 'command'),
            (lambda d: d['pages'][0]['layout'][0]['items'][0]['properties'].update(state_command=42), 'state_command'),
        ]
        for mutate, expected in samples:
            with self.subTest(expected=expected):
                data = copy.deepcopy(self.valid)
                mutate(data)
                with self.assertRaisesRegex((ValueError, TypeError), expected):
                    validate_config(data)

    def test_validation_is_headless(self):
        environment = os.environ.copy()
        environment.pop('WAYLAND_DISPLAY', None)
        environment.pop('DISPLAY', None)
        result = subprocess.run(
            ['python', str(cc.SCRIPT_DIR / 'dusky_control_center.py'), '--validate'],
            env=environment, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class RadioTests(unittest.TestCase):
    def test_blocked_unblocked_and_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(radio_status.radio_state('wlan', root), 'unavailable')
            rfkill = root / 'rfkill0'
            rfkill.mkdir()
            (rfkill / 'type').write_text('wlan')
            (rfkill / 'soft').write_text('1')
            (rfkill / 'hard').write_text('0')
            self.assertEqual(radio_status.radio_state('wlan', root), 'no')
            (rfkill / 'soft').write_text('0')
            self.assertEqual(radio_status.radio_state('wlan', root), 'yes')
            (rfkill / 'hard').write_text('1')
            self.assertEqual(radio_status.radio_state('wlan', root), 'no')

    def test_wireguard_interface_name_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wg0').mkdir()
            self.assertEqual(wireguard_status.interface_state('wg0', root), 'yes')
            self.assertEqual(wireguard_status.interface_state('work', root), 'no')
            for invalid in ('', '-bad', '../bad', 'a' * 16, 'name;touch /tmp/evil'):
                self.assertEqual(wireguard_status.interface_state(invalid, root), 'unavailable')


class ActionTests(unittest.TestCase):
    def test_completion_failure_and_timeout_kills_child(self):
        results = []
        actions.run_action({'type': 'exec', 'argv': ['/bin/sh', '-c', 'exit 0']}, 'Fixture', results.append)
        drain_until(lambda: len(results) == 1)
        self.assertTrue(results.pop().success)

        actions.run_action({'type': 'exec', 'argv': ['/bin/sh', '-c', 'echo broken >&2; exit 7']}, 'Fixture', results.append)
        drain_until(lambda: len(results) == 1)
        failure = results.pop()
        self.assertFalse(failure.success)
        self.assertIn('broken', failure.message)

        actions.run_action({'type': 'exec', 'argv': ['/nonexistent/dusky-command']}, 'Fixture', results.append)
        drain_until(lambda: len(results) == 1)
        self.assertFalse(results.pop().success)

        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'pid'
            action = {'type': 'exec', 'argv': ['/bin/sh', '-c', 'sleep 30 & echo $! > "$1"; wait', 'fixture', str(pidfile)]}
            handle = actions.run_action(action, 'Fixture', results.append, timeout=1)
            self.assertIsNotNone(handle)
            drain_until(lambda: pidfile.exists())
            child = int(pidfile.read_text())
            try:
                drain_until(lambda: len(results) == 1, timeout=3)
                self.assertFalse(results[0].success)
                self.assertEqual(results[0].message, 'Timed out')
                drain_until(lambda: not Path(f'/proc/{child}').exists() or Path(f'/proc/{child}/stat').read_text().split()[2] == 'Z')
            finally:
                handle._stop_process_group()


class ServiceStatusTests(unittest.TestCase):
    def test_systemd_status_axes_from_fixture_responses(self):
        for active, startup in (
            ('active', 'disabled'), ('inactive', 'enabled'), ('failed', 'enabled'),
            ('activating', 'static'), ('inactive', 'masked'),
        ):
            with self.subTest(active=active, startup=startup):
                results = []
                output = f'LoadState=loaded\nActiveState={active}\nSubState=dead\nUnitFileState={startup}\n'
                with patch.object(service_manager, '_run_argv_async', side_effect=lambda _args, _time, cb: cb(output, '', True, 0)):
                    service_manager.check_unit_status_async('user', 'fixture.service', on_result=results.append)
                self.assertEqual(results[0].active_state, active)
                self.assertEqual(results[0].unit_file_state, startup)

    def test_real_read_only_systemd_status(self):
        results = []
        service_manager.check_unit_status_async('user', 'dusky.service', on_result=results.append)
        drain_until(lambda: bool(results))
        self.assertIsNotNone(results[0])
        self.assertEqual(results[0].load_state, 'loaded')
        self.assertIn(results[0].active_state, {'active', 'inactive'})
        self.assertTrue(results[0].unit_file_state)

    def test_not_found_and_option_like_unit(self):
        results = []
        service_manager.check_unit_status_async('user', 'nonexistent-dusky-test.service', on_result=results.append)
        drain_until(lambda: bool(results))
        self.assertEqual(results[0].load_state, 'not-found')
        self.assertIsNone(service_manager.normalize_service_spec('-bad', 'user'))

    def test_mapped_service_row_displays_both_states(self):
        cc.Adw.init()
        row = rows.ServiceToggleRow({'title': 'Fixture', 'service': 'dusky', 'scope': 'user'})
        group = cc.Adw.PreferencesGroup()
        group.add(row)
        window = cc.Adw.Window(content=group)
        try:
            window.present()
            drain_until(lambda: 'Startup:' in row.get_subtitle())
            self.assertIn('Runtime:', row.get_subtitle())
        finally:
            window.destroy()

    def test_mapped_service_card_displays_both_states(self):
        cc.Adw.init()
        card = rows.ServiceToggleCard({'title': 'Fixture', 'service': 'dusky', 'scope': 'user'})
        box = cc.Gtk.Box()
        box.append(card)
        window = cc.Adw.Window(content=box)
        try:
            window.present()
            drain_until(lambda: ' • ' in card.status_lbl.get_label())
            self.assertIn('Startup:', card.get_tooltip_text())
        finally:
            window.destroy()

    def test_missing_service_control_is_disabled(self):
        cc.Adw.init()
        row = rows.ServiceToggleRow({'title': 'Fixture', 'service': 'nonexistent-dusky-test', 'scope': 'user'})
        group = cc.Adw.PreferencesGroup()
        group.add(row)
        window = cc.Adw.Window(content=group)
        try:
            window.present()
            drain_until(lambda: 'Not-found' in row.get_subtitle())
            self.assertFalse(row.toggle_switch.get_sensitive())
        finally:
            window.destroy()


class WidgetImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc.Adw.init()

    def test_rapid_slider_emits_only_first_and_latest(self):
        callbacks = []
        commands = []
        def fake_run(action, _title, callback, **_kwargs):
            commands.append(action['command'])
            callbacks.append(callback)
        with patch.object(rows.actions, 'run_action', side_effect=fake_run):
            slider = rows.SliderRow({'min': 0, 'max': 100, 'debounce': False}, {'type': 'exec', 'command': 'set {value}'})
            try:
                for value in range(1, 81):
                    slider.slider.set_value(value)
                drain_until(lambda: len(commands) == 1)
                self.assertEqual(commands, ['set 80'])
                slider.slider.set_value(90)
                drain_until(lambda: slider._pending_value == 90)
                callbacks.pop(0)(actions.ActionResult(True, 'Applied'))
                drain_until(lambda: len(commands) == 2)
                self.assertEqual(commands[-1], 'set 90')
                callbacks.pop(0)(actions.ActionResult(True, 'Applied'))
            finally:
                rows._batch_source_remove(*rows._dispose_row(slider))

    def test_radio_state_transitions_and_unavailable(self):
        for widget in (rows.ToggleRow({'title': 'Radio', 'state_command': 'fixture'}), rows.GridToggleCard({'title': 'Radio', 'state_command': 'fixture'})):
            widget._handle_state_output('yes')
            self.assertTrue(widget.toggle_switch.get_active() if isinstance(widget, rows.ToggleRow) else widget.is_active)
            widget._handle_state_output('no')
            self.assertFalse(widget.toggle_switch.get_active() if isinstance(widget, rows.ToggleRow) else widget.is_active)
            widget._handle_state_failure()
            self.assertFalse(widget.toggle_switch.get_sensitive() if isinstance(widget, rows.ToggleRow) else widget.get_sensitive())
            widget._handle_state_output('yes')
            self.assertTrue(widget.toggle_switch.get_sensitive() if isinstance(widget, rows.ToggleRow) else widget.get_sensitive())

    def test_setting_file_deletion_refreshes_toggle(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / 'fixture'
            target.write_text('true')
            with patch.object(utility, '_settings_dir_cache', utility._ResolvedDirectoryCache(base)):
                row = rows.ToggleRow({'title': 'Fixture', 'key': 'fixture', 'persistence': 'action'})
                self.assertTrue(row.toggle_switch.get_active())
                target.unlink()
                file = Gio.File.new_for_path(str(target))
                with patch.object(row, 'get_mapped', return_value=True):
                    row._on_file_changed(None, file, None, Gio.FileMonitorEvent.DELETED, 'fixture', 'fixture')
                self.assertFalse(row.toggle_switch.get_active())

    def test_action_owned_toggle_never_writes_failed_requested_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / 'fixture'
            state.write_text('false')
            cache = utility._ResolvedDirectoryCache(base)
            with patch.object(utility, '_settings_dir_cache', cache):
                row = rows.ToggleRow({'title': 'Fixture', 'key': 'fixture', 'persistence': 'action'}, {
                    'enabled': {'type': 'exec', 'argv': ['/bin/sh', '-c', 'exit 7']},
                    'disabled': {'type': 'exec', 'argv': ['/bin/true']},
                })
                row._on_toggle_changed(row.toggle_switch, True)
                drain_until(lambda: not row._operation_busy)
                self.assertEqual(state.read_text(), 'false')
                self.assertFalse(row.toggle_switch.get_active())

                row.on_action['enabled'] = {'type': 'exec', 'argv': ['/bin/sh', '-c', 'printf true > "$1"', 'fixture', str(state)]}
                row._on_toggle_changed(row.toggle_switch, True)
                drain_until(lambda: not row._operation_busy)
                self.assertEqual(state.read_text(), 'true')
                self.assertTrue(row.toggle_switch.get_active())

    def test_one_shot_label_uses_cancellable_runner(self):
        label = rows.LabelRow({'title': 'Fixture'}, {'type': 'exec', 'command': 'printf Hello'})
        group = cc.Adw.PreferencesGroup()
        group.add(label)
        window = cc.Adw.Window(content=group)
        try:
            window.present()
            drain_until(lambda: label.value_label.get_label() == 'Hello')
            self.assertEqual(label._state.value.source_id, 0)
        finally:
            window.destroy()

    def test_failed_entry_and_selection_do_not_claim_success(self):
        callbacks = []
        messages = []
        with patch.object(rows.actions, 'run_action', side_effect=lambda _action, _title, callback, **_kw: callbacks.append(callback)), patch.object(utility, 'toast', side_effect=lambda _overlay, message, *_args: messages.append(message)):
            entry = rows.EntryRow({'title': 'Fixture'}, {'type': 'exec', 'command': 'false {value}'})
            entry.set_text('42')
            entry._on_apply()
            callbacks.pop()(actions.ActionResult(False, 'Exit status 7'))
            self.assertEqual(entry.get_text(), '42')
            self.assertTrue(entry._entry_dirty)
            self.assertTrue(any('Failed' in text for text in messages))
            self.assertFalse(any('Applied' in text for text in messages))

            selection = rows.SelectionRow({'title': 'Fixture', 'options': ['A', 'B']}, {'B': {'type': 'exec', 'command': 'false'}})
            selection.set_selected(1)
            callbacks.pop()(actions.ActionResult(False, 'Exit status 7'))
            self.assertEqual(selection.get_selected_item().get_string(), 'A')

    def test_generated_paths_are_literal_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "odd ' ; $(touch SHOULD_NOT_EXIST) {relpath}"
            target = root / f'{name}.conf'
            target.touch()
            app = cc.DuskyControlCenter()
            with patch.object(app, '_file_generator_cache', {}):
                generator = {'properties': {'path': str(root), 'glob': '*.conf'}, 'item_template': {
                    'type': 'button', 'properties': {'title': '{name}', 'state_command': 'printf "%s" {name}'},
                    'on_press': {'type': 'exec', 'argv': ['/bin/printf', '%s', '{path}']},
                }}
                generated = list(app._process_file_generator(generator))
            self.assertEqual(generated[0]['on_press']['argv'][-1], str(target))
            self.assertEqual(generated[0]['properties']['title'], name)
            query = generated[0]['properties']['state_command']
            self.assertEqual(subprocess.check_output(['/bin/sh', '-c', query], text=True), name)
            self.assertFalse((Path.cwd() / 'SHOULD_NOT_EXIST').exists())
            output = root / 'captured'
            results = []
            actions.run_action({
                'type': 'exec',
                'argv': ['/bin/sh', '-c', 'printf %s "$1" > "$2"', 'fixture', generated[0]['on_press']['argv'][-1], str(output)],
            }, 'Fixture', results.append)
            drain_until(lambda: bool(results))
            self.assertTrue(results[0].success)
            self.assertEqual(output.read_text(), str(target))

    def test_real_generator_actions_use_argv(self):
        config = tomllib.loads(Path(cc.SCRIPT_DIR / cc.CONFIG_FILENAME).read_text())
        generators = []
        def walk(value):
            if isinstance(value, dict):
                if value.get('type') in {'directory_generator', 'file_generator'}:
                    generators.append(value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(config)
        self.assertEqual(len(generators), 3)
        for generator in generators:
            actions_found = []
            def collect(value):
                if isinstance(value, dict):
                    if value.get('type') == 'exec':
                        actions_found.append(value)
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)
            collect(generator['item_template'])
            self.assertTrue(actions_found)
            self.assertTrue(all('argv' in action and 'command' not in action for action in actions_found))

    def test_wireguard_toggle_dispatch_keeps_path_as_one_argument(self):
        config = tomllib.loads(Path(cc.SCRIPT_DIR / cc.CONFIG_FILENAME).read_text())
        generator = None
        def find(value):
            nonlocal generator
            if isinstance(value, dict):
                if value.get('type') == 'file_generator' and generator is None:
                    generator = value
                for child in value.values():
                    find(child)
            elif isinstance(value, list):
                for child in value:
                    find(child)
        find(config)
        self.assertIsNotNone(generator)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "odd ' ; $(touch SHOULD_NOT_EXIST).conf"
            path.touch()
            fixture = copy.deepcopy(generator)
            fixture['properties']['path'] = str(root)
            app = cc.DuskyControlCenter()
            item = list(app._process_file_generator(fixture))[0]['items'][0]
            captured = []
            with patch.object(rows.actions, 'run_action', side_effect=lambda action, _title, _callback: captured.append(action)):
                row = rows.ToggleRow(item['properties'], item['on_toggle'])
                row._on_toggle_changed(row.toggle_switch, True)
            self.assertEqual(captured[0]['argv'][-1], str(path))
            self.assertFalse((Path.cwd() / 'SHOULD_NOT_EXIST').exists())

    def test_pending_slider_edit_survives_window_destruction(self):
        commands = []
        callbacks = []
        def fake_run(action, _title, callback, **_kwargs):
            commands.append(action['command'])
            callbacks.append(callback)
        with patch.object(rows.actions, 'run_action', side_effect=fake_run):
            slider = rows.SliderRow({'min': 0, 'max': 100, 'debounce': False}, {'type': 'exec', 'command': 'set {value}'})
            group = cc.Adw.PreferencesGroup()
            group.add(slider)
            window = cc.Adw.Window(content=group)
            window.present()
            drain_until(slider.get_mapped)
            slider.slider.set_value(70)
            group.remove(slider)
            window.destroy()
            drain_until(lambda: bool(commands))
            self.assertTrue(slider._state.is_destroyed)
            self.assertEqual(commands, ['set 70'])
            callbacks.pop()(actions.ActionResult(True, 'Applied'))


class SettingsPathTests(unittest.TestCase):
    def test_clock_helper_owns_lowercase_state(self):
        helper = Path.home() / 'user_scripts/waybar/toggle_time.sh'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            waybar = root / 'waybar' / 'fixture'
            waybar.mkdir(parents=True)
            config = waybar / 'config.jsonc'
            config.write_text('{:%I:%M %p}')
            stub_dir = root / 'bin'
            stub_dir.mkdir()
            pgrep = stub_dir / 'pgrep'
            pgrep.write_text('#!/bin/sh\nexit 1\n')
            pgrep.chmod(0o755)
            environment = os.environ.copy()
            environment['XDG_CONFIG_HOME'] = str(root)
            environment['PATH'] = f"{stub_dir}:{environment['PATH']}"
            state = root / 'dusky/settings/time_format'
            for flag, expected, fragment in (('--24', 'true', '{:%H:%M}'), ('--12', 'false', '{:%I:%M %p}')):
                result = subprocess.run([str(helper), flag], env=environment, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state.read_text().strip(), expected)
                self.assertIn(fragment, config.read_text())

    def test_external_symlinks_rejected_internal_accepted(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as outside_dir:
            base = Path(base_dir)
            outside = Path(outside_dir) / 'value'
            inside = base / 'value'
            inside.write_text('yes')
            (base / 'internal').symlink_to(inside)
            (base / 'external').symlink_to(outside)
            with patch.object(utility, '_settings_dir_cache', utility._ResolvedDirectoryCache(base)):
                self.assertEqual(utility._validate_settings_path('internal'), inside)
                self.assertIsNone(utility._validate_settings_path('external'))
                self.assertIsNone(utility._validate_settings_path('.'))
