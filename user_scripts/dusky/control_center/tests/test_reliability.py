"""Regression checks; run with python -m unittest discover -s tests -v."""
import atexit
import gc
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

import dusky_control_center as cc
import dusky_text_editor as editor
from lib import rows, service_manager as services, utility
from gi.repository import GLib


def drain_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('Async callback did not finish')


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.old_instance = utility._SettingsWriteBuffer._instance
        utility._SettingsWriteBuffer._instance = None
        self.paths = patch.object(utility, '_settings_dir_cache', utility._ResolvedDirectoryCache(self.base))
        self.paths.start()
        self.buffer = utility._SettingsWriteBuffer()

    def tearDown(self):
        self.buffer._flush_synchronously()
        atexit.unregister(self.buffer._flush_synchronously)
        utility._SettingsWriteBuffer._instance = self.old_instance
        self.paths.stop()
        self.tmp.cleanup()

    def start_batch(self):
        GLib.source_remove(self.buffer._source_id)
        self.buffer._flush_buffer_cb()

    def test_read_during_flush_and_shutdown_write_order(self):
        entered, release = threading.Event(), threading.Event()
        original = utility._write_to_disk_atomic

        def slow_write(target, value):
            if value == 'older':
                entered.set()
                if not release.wait(3):
                    raise AssertionError('Write release timed out')
            return original(target, value)

        with patch.object(utility, '_write_to_disk_atomic', side_effect=slow_write):
            utility.save_setting('test', 'older')
            self.start_batch()
            self.assertTrue(entered.wait(3))
            self.assertEqual(utility.load_setting('test', ''), 'older')
            utility.save_setting('test', 'newer')
            self.assertEqual(utility.load_setting('test', ''), 'newer')
            timer = threading.Timer(0.05, release.set)
            timer.start()
            utility.flush_settings()
            timer.join()
        self.assertEqual((self.base / 'test').read_text(), 'newer')

    def test_multiline_round_trip_and_normalized_settings(self):
        text = '  indented\n\n'
        utility.save_setting('text', text)
        self.assertEqual(utility.load_setting('text', '', preserve_whitespace=True), text)
        utility.flush_settings()
        self.assertEqual(utility.load_setting('text', '', preserve_whitespace=True), text)
        self.assertEqual(utility.load_setting('text', ''), 'indented')


class ServiceTests(unittest.TestCase):
    def test_spawn_failure_callback(self):
        results = []
        services._run_argv_async(['/nonexistent/dusky-test-command'], 1, lambda *args: results.append(args))
        drain_until(lambda: bool(results))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][2])
        self.assertTrue(results[0][1])

    def test_batch_spawn_failure_completes_once(self):
        results = []
        with patch.object(services, 'SYSTEMCTL_PATH', '/nonexistent/dusky-test-command'):
            services.check_multiple_services_async({'user': ['a'], 'system': ['b']}, on_result=results.append)
            drain_until(lambda: bool(results))
        self.assertEqual(results, [{('user', 'a.service'): None, ('system', 'b.service'): None}])

    def test_empty_output_is_unknown(self):
        results = []
        def failed(_argv, _timeout, callback):
            callback('', 'No bus', False, 1)
        with patch.object(services, '_run_argv_async', side_effect=failed):
            services.check_single_service_async('user', 'a', on_result=results.append)
            drain_until(lambda: bool(results))
        self.assertEqual(results, [None])


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc.Adw.init()

    def test_poll_once_per_map_and_preserve_intervals(self):
        row = rows.ToggleRow({'title': 'Test', 'state_command': 'true', 'interval': 2,
                              'icon': {'type': 'exec', 'command': 'true', 'interval': 9}})
        with patch.object(row, 'get_mapped', return_value=True), patch.object(rows, '_run_shell_async', return_value=None) as run:
            row._on_base_map(row)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(row._state.icon.interval, 9)
            self.assertEqual(row._state.monitor.interval, 2)
            row._on_base_unmap(row)
            self.assertTrue(all(slot.source_id == 0 for slot in row._state._slots))
            row._on_base_map(row)
            self.assertEqual(run.call_count, 4)
            row._on_base_unmap(row)

    def test_old_worker_cannot_stall_reopened_widget(self):
        row = rows.ButtonRow({'title': 'Test', 'button_text_file': '/nonexistent/state'})
        slot = row._state.misc
        slot.is_running = True
        generation = slot.generation
        row._pause_all_polls()
        self.assertFalse(slot.is_running)
        self.assertGreater(slot.generation, generation)
        with patch.object(row, 'get_mapped', return_value=True), patch.object(rows, '_submit_task_safe', return_value=True) as submit:
            row._on_base_map(row)
            self.assertEqual(submit.call_count, 1)
            self.assertGreater(slot.source_id, 0)
            row._on_base_unmap(row)

    def test_color_initialization_does_not_execute_action(self):
        with patch.object(utility, 'load_setting', return_value='#123456'), patch.object(utility, 'execute_command') as run, patch.object(utility, 'save_setting') as save:
            row = rows.ColorRow({'key': 'test'}, {'command': 'test {value}'})
            run.assert_not_called()
            save.assert_not_called()
            row._on_color_changed(row.btn, None)
            save.assert_called_once_with('test', '#123456')

    def test_entry_fetch_does_not_overwrite_typing(self):
        row = rows.EntryRow({'title': 'Test'})
        row.set_text('typed')
        row._on_initial_value_loaded('late result')
        self.assertEqual(row.get_text(), 'typed')

    def test_delayed_selection_does_not_revert_user_choice(self):
        row = rows.SelectionRow({'options': ['Old', 'New']})
        generation = row._selection_fetch_generation
        row.set_selected(1)
        row._update_selection_ui('Old', generation)
        self.assertEqual(row.get_selected(), 1)

    def test_options_refresh_preserves_selection(self):
        row = rows.SelectionRow({'options': ['Old', 'New']})
        row.set_selected(1)
        row._update_options_ui(['Other', 'New', 'Old'], row._options_fetch_generation)
        self.assertEqual(row.get_selected_item().get_string(), 'New')

    def test_worker_output_applied_before_followup_generation(self):
        row = rows.SelectionRow({'options': ['Old'], 'options_command': 'unused'})
        row._options_fetch_running = True
        row._options_fetch_pending = True
        with patch.object(rows, '_run_shell_async', side_effect=lambda _cmd, _timeout, callback: GLib.idle_add(callback, 'New\n')):
            row._fetch_options_async(row._options_fetch_generation)
            drain_until(lambda: row._options_fetch_generation == 1)
        self.assertEqual(row.options_list, ['New'])

    def test_delayed_numeric_result_does_not_revert_pending_edit(self):
        for cls, attr in ((rows.SliderRow, 'slider'), (rows.SpinRow, 'spin')):
            row = cls({'min': 0, 'max': 100})
            control = getattr(row, attr)
            control.set_value(80)
            row._apply_value_update(10)
            self.assertEqual(control.get_value(), 80)
            rows._batch_source_remove(*rows._dispose_row(row))

    def test_numeric_values_are_finite_and_small_steps_display_correctly(self):
        for value in (float('inf'), float('nan'), 10 ** 1000):
            self.assertEqual(rows._safe_float(value, 1.0), 1.0)
        row = rows.SpinRow({'step': 1e-7})
        self.assertEqual(row.spin.get_digits(), 7)

    def test_hidden_widgets_have_no_periodic_sources(self):
        widgets = [rows.ButtonRow({'button_text_file': '/nonexistent/state'}),
                   rows.GridCard({'badge_file': '/nonexistent/badge', 'button_text_file': '/nonexistent/state'}),
                   rows.SelectionRow({'value_command': 'true'})]
        for widget in widgets:
            self.assertTrue(all(slot.source_id == 0 for slot in widget._state._slots))

    def test_real_map_hide_reopen_lifecycle(self):
        row = rows.ToggleRow({'title': 'Audit', 'state_command': 'true', 'interval': 2})
        group = cc.Adw.PreferencesGroup()
        group.add(row)
        window = cc.Adw.Window(title='Control Center lifecycle check', content=group)
        with patch.object(rows, '_run_shell_async', return_value=None) as run:
            try:
                for expected in (1, 2):
                    window.present()
                    drain_until(row.get_mapped)
                    self.assertEqual(run.call_count, expected)
                    window.set_visible(False)
                    self.assertTrue(all(slot.source_id == 0 for slot in row._state._slots))
            finally:
                window.destroy()

    def test_hiding_service_does_not_cancel_transaction(self):
        for widget in (rows.ServiceToggleRow({'service': 'example'}), rows.ServiceToggleCard({'service': 'example'})):
            from unittest.mock import Mock
            handle = Mock()
            widget._toggle_handle = handle
            if isinstance(widget, rows.ServiceToggleRow):
                widget._on_service_unmap(widget)
            else:
                widget._on_card_unmap(widget)
            handle.cancel.assert_not_called()

    def test_removing_service_row_does_not_cancel_transaction(self):
        from unittest.mock import Mock
        row = rows.ServiceToggleRow({'service': 'example'})
        group = cc.Adw.PreferencesGroup()
        group.add(row)
        window = cc.Adw.Window(content=group)
        with patch.object(services, 'check_unit_status_async', return_value=None):
            window.present()
            drain_until(row.get_mapped)
            handle = Mock()
            row._toggle_handle = handle
            group.remove(row)
            self.assertTrue(row._state.is_destroyed)
            handle.cancel.assert_not_called()
        window.destroy()


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = cc.DuskyControlCenter()
        cls.app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    @classmethod
    def tearDownClass(cls):
        cls.app._window.destroy()

    def test_01_home_preloaded_other_pages_lazy(self):
        count = len(self.app._state.config['pages'])
        roots = [self.app._stack.get_child_by_name(f'page-{i}').get_visible_page() for i in range(count)]
        self.assertIsNotNone(roots[0])
        self.assertEqual(sum(root is not None for root in roots), 1)
        self.app._sidebar_list.select_row(self.app._sidebar_list.get_row_at_index(1))
        self.assertIsNotNone(self.app._stack.get_child_by_name('page-1').get_visible_page())

    def test_bad_css_preserves_provider(self):
        provider, css = self.app._css_provider, self.app._state.css_content
        self.app._state.css_content = 'button { invalid-property: 1; }'
        try:
            with self.assertRaises(ValueError):
                self.app._apply_css()
            self.assertIs(self.app._css_provider, provider)
        finally:
            self.app._state.css_content = css

    def test_bad_reload_preserves_working_config(self):
        config = self.app._state.config
        with patch.object(self.app, '_do_load_config', return_value=({'pages': []}, 'Invalid TOML')), patch.object(self.app, '_run_in_background', side_effect=lambda task, callback: callback(task(), None)):
            self.app._reload_app_async()
        self.assertIs(self.app._state.config, config)

    def test_css_read_failure_preserves_working_theme(self):
        config, provider, css = self.app._state.config, self.app._css_provider, self.app._state.css_content
        def complete(task, callback):
            try:
                callback(task(), None)
            except OSError as error:
                callback(None, error)
        with patch.object(self.app, '_do_load_css', side_effect=PermissionError('fixture denied')), patch.object(self.app, '_run_in_background', side_effect=complete), patch.object(cc.log, 'error'):
            self.app._reload_app_async()
        self.assertIs(self.app._state.config, config)
        self.assertIs(self.app._css_provider, provider)
        self.assertEqual(self.app._state.css_content, css)

    def test_reload_restores_page_by_id(self):
        original = self.app._state.config
        self.app._sidebar_list.select_row(self.app._sidebar_list.get_row_at_index(2))
        page_id = original['pages'][2]['id']
        changed = dict(original)
        changed['pages'] = list(original['pages'])
        changed['pages'][1], changed['pages'][2] = changed['pages'][2], changed['pages'][1]
        try:
            with patch.object(self.app, '_do_load_config', return_value=(changed, None)), patch.object(self.app, '_run_in_background', side_effect=lambda task, callback: callback(task(), None)):
                self.app._reload_app_async()
            selected = self.app._sidebar_list.get_selected_row().get_index()
            self.assertEqual(self.app._state.config['pages'][selected]['id'], page_id)
        finally:
            self.app._state.config = original
            self.app._clear_and_rebuild_ui(2)

    def test_search_clear_returns_to_page(self):
        page = self.app._stack.get_visible_child_name()
        self.app._execute_search('audio')
        self.assertEqual(self.app._stack.get_visible_child_name(), cc.SEARCH_PAGE_ID)
        self.app._execute_search('')
        self.assertEqual(self.app._stack.get_visible_child_name(), page)

    def test_reloads_release_all_old_views_and_rows(self):
        references = []

        def remember(widget):
            if hasattr(widget, '_state') or isinstance(widget, cc.Adw.NavigationView):
                references.append(weakref.ref(widget))
            child = widget.get_first_child()
            while child is not None:
                remember(child)
                child = child.get_next_sibling()

        for _ in range(3):
            for i in range(len(self.app._state.config['pages'])):
                self.app._sidebar_list.select_row(self.app._sidebar_list.get_row_at_index(i))
                remember(self.app._stack.get_child_by_name(f'page-{i}'))
            self.app._clear_and_rebuild_ui(0)
            drain_until(lambda: True)
            gc.collect()
            self.assertFalse(any(ref() is not None for ref in references))


class CommandTests(unittest.TestCase):
    @staticmethod
    def process_stopped(pid):
        try:
            return Path(f'/proc/{pid}/stat').read_text().split()[2] == 'Z'
        except (FileNotFoundError, ProcessLookupError):
            return True

    def test_poll_cancellation_stops_child_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'pid'
            results = []
            handle = rows._run_shell_async(f'sleep 30 & echo $! > {pid_file}; exit 0', 5, results.append)
            try:
                drain_until(lambda: pid_file.exists() and bool(pid_file.read_text().strip()))
                pid = int(pid_file.read_text())
                handle.cancel()
                drain_until(lambda: bool(results) and self.process_stopped(pid))
                self.assertEqual(results, [None])
            finally:
                handle.cancel()

    def test_selector_cleanup_stops_children_of_exited_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'pid'
            proc = subprocess.Popen(['/bin/sh', '-c', f'sleep 30 & echo $! > {pid_file}; exit 0'],
                                    stdout=subprocess.PIPE, start_new_session=True)
            proc.wait(timeout=3)
            pid = int(pid_file.read_text())
            row = rows.AsyncSelectorRow({})
            try:
                row._kill_and_reap_process(proc)
                drain_until(lambda: self.process_stopped(pid))
            finally:
                if not self.process_stopped(pid):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                proc.communicate(timeout=3)

    def test_shell_expansion_preserves_quotes(self):
        command = "printf '%s' '$HOME'"
        normalized = utility._normalize_command(command)
        self.assertEqual(normalized, command)
        self.assertEqual(utility._build_command_list(normalized, 'test', False, False), ['printf', '%s', '$HOME'])
        self.assertEqual(utility._build_command_list('true\nfalse', 'test', False, False), ['sh', '-c', 'true\nfalse'])

    def test_double_quoted_escapes_keep_shell_semantics(self):
        command = r'printf "%s" "\$HOME"'
        argv = utility._build_command_list(command, 'test', False, False)
        self.assertEqual(subprocess.check_output(argv, text=True), '$HOME')

    def test_terminal_path_and_arguments(self):
        with patch.object(utility, '_get_configured_terminal', return_value='/usr/bin/kitty --single-instance'):
            argv = utility._build_command_list('true', 'Test', True, False)
        self.assertEqual(argv, ['/usr/bin/kitty', '--single-instance', '--class', 'dusky-term',
                                '--title', 'Test', '--hold', 'sh', '-c', 'true'])
        self.assertEqual(utility._build_terminal_wrapped('wezterm', 'Test', ['true']),
                         ['wezterm', 'start', '--class', 'dusky-term', '--', 'true'])

    def test_plain_commands_do_not_read_terminal_configuration(self):
        with patch.object(utility, '_get_configured_terminal') as get_terminal:
            self.assertEqual(utility._build_command_list('true', 'Test', False, False), ['true'])
            get_terminal.assert_not_called()

    def test_terminal_editor_with_absolute_path_and_arguments(self):
        with patch.object(editor, 'parse_config', return_value=('/usr/bin/hx --readonly', '/usr/bin/kitty')), patch.object(editor.sys, 'argv', ['editor', '/tmp/a file']), patch.object(editor.os, 'execvp') as execute:
            editor.main()
        execute.assert_called_once_with('dusky-run', ['dusky-run', '/usr/bin/kitty', '--class', 'hx', '/usr/bin/hx', '--readonly', '/tmp/a file'])


if __name__ == '__main__':
    unittest.main()
