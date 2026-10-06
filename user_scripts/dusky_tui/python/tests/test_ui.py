"""UI regression tests; all configuration I/O uses in-memory engines."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.frontend import ui
from python.frontend.core_types import ConfigItem
from textual.widgets import Input, OptionList

if source := os.environ.get('DUSKY_UI_TEST_SOURCE'):
    spec = importlib.util.spec_from_file_location('audit_ui', source)
    ui = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ui
    spec.loader.exec_module(ui)


class Engine:
    target_path = ''

    def __init__(self, state=None):
        self.state = dict(state or {})
        self.writes = []
        self.batches = []
        self.fail = False
        self.started = None
        self.release = None

    def load_state(self):
        return self.state.copy()

    def write_value(self, key, scope, value, item_type='string'):
        self.writes.append((key, scope, value, item_type))
        if self.started:
            self.started.set()
            self.release.wait(2)
        if self.fail:
            return False, 'failed', ''
        self.state[key] = value
        return True, '', ''

    def write_batch(self, changes):
        self.batches.append(changes)
        if self.fail:
            return False, 'failed', ''
        for key, scope, value, kind in changes:
            self.state[key] = value
        return True, '', ''


def item(key='x', **kwargs):
    return ConfigItem(label=key, key=key, type_=kwargs.pop('type_', 'int'), default=kwargs.pop('default', 0), **kwargs)


def app_for(schema=None, *, mode='batch', engine=None, **kwargs):
    engine = engine or Engine()
    schema = schema or {0: [item()]}
    app = ui.DuskyTUI({('fake', ''): engine}, ('fake', ''), schema,
                       {key: f'Tab {key}' for key in schema},
                       default_mode=mode, enable_user_presets=False, **kwargs)
    app.play_reset_sound = lambda: None
    return app


class UITests(unittest.IsolatedAsyncioTestCase):
    async def boot(self, app, pilot):
        for _ in range(100):
            await pilot.pause(0.01)
            if getattr(app, '_boot_complete', False):
                return
        self.fail('boot never completed')

    async def test_search_accepts_j_and_k(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.action_search()
            await pilot.pause()
            await pilot.press('j', 'k')
            self.assertEqual(app.screen.query_one(Input).value, 'jk')

    async def test_hybrid_preserves_custom_initial_value(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.push_screen(ui.HybridInputScreen('value', 'custom', ['first', 'second']))
            await pilot.pause()
            self.assertEqual(app.screen.query_one(Input).value, 'custom')

    async def test_menu_with_scoped_short_parent_reference(self):
        menu = item('parent', type_='menu', default=None, scope='section', expanded=True)
        child = item('child', scope='section', parent_ref='parent')
        app = app_for({0: [menu, child]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertTrue(app._indent_cache['item_0_1'])
            app.action_toggle_expand()
            await pilot.pause()
            self.assertEqual(app.current_option_list.option_count, 1)

    async def test_reset_syncs_duplicates_and_can_undo(self):
        a, b = item(value=4), item(value=4)
        app = app_for({0: [a], 1: [b]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_transaction([(0, 0, 4, 0)])
            self.assertEqual((a.value, b.value), (0, 0))
            app.action_undo()
            self.assertEqual((a.value, b.value), (4, 4))
            self.assertFalse(app.pending_commits)

    async def test_batch_deduplicates_views(self):
        app = app_for({0: [item()], 1: [item()]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_transaction([(0, 0, 0, 2), (1, 0, 0, 2)])
            self.assertEqual(app._pending_setting_count(), 1)
            await app._save_batch_async()
            self.assertEqual(len(app.engine_pool[app.default_engine_key].batches[0]), 1)
            self.assertFalse(app.pending_commits)

    async def test_autosave_updates_baseline(self):
        app = app_for(mode='auto')
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._apply_value(0, 0, setting, 3)
            await pilot.pause(0.4)
            self.assertEqual(app._committed[(0, 0)], 3)
            app.auto_save = False
            app._apply_value(0, 0, setting, 4)
            app._apply_value(0, 0, setting, 3)
            self.assertFalse(app.pending_commits)

    async def test_stale_autosave_keeps_new_timer(self):
        app = app_for(mode='auto')
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._apply_value(0, 0, setting, 1)
            old_gen = app._write_generation[app._uid_engine_key(setting)]
            app._apply_value(0, 0, setting, 2)
            new_timer = app._save_timers[(0, 0)]
            await app._do_auto_save_async(0, 0, setting, '1', 0, old_gen, [(0, 0, 0, 1)])
            self.assertIs(app._save_timers.get((0, 0)), new_timer)
            await pilot.pause(0.4)
            self.assertEqual(app.engine_pool[app.default_engine_key].state['x'], '2')

    async def test_deferred_load_preserves_edits(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._apply_value(0, 0, setting, 3)
            app._apply_deferred_tabs([0], {app.default_engine_key: {'x': 8}})
            self.assertEqual(setting.value, 3)
            self.assertEqual(app._committed[(0, 0)], 0)

    async def test_presets_convert_boolean_strings(self):
        setting = item(type_='bool', default=True)
        preset = item('preset', type_='preset', default=None, preset_payload={'x': 'false'})
        app = app_for({0: [setting, preset]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.apply_preset(preset)
            self.assertIs(setting.value, False)

    async def test_failed_autosave_reverts_duplicates(self):
        engine = Engine()
        engine.fail = True
        app = app_for({0: [item()], 1: [item()]}, mode='auto', engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            with patch.object(app, 'play_reset_sound'):
                app._apply_value(0, 0, app.schema[0][0], 3)
                await pilot.pause(0.4)
            self.assertEqual([app.schema[i][0].value for i in (0, 1)], [0, 0])
            self.assertFalse(app.undo_stack)

    async def test_cancelled_io_finishes_before_unlock(self):
        app = app_for()
        started, release = threading.Event(), threading.Event()
        def write():
            started.set()
            release.wait(2)
        task = asyncio.create_task(app._run_save_io(write))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_action_drains_large_output(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.execute_action(item('action', type_='action', default="python -c 'print(\"x\" * 100000)'", force_interactive=False))
            for _ in range(100):
                await pilot.pause(0.02)
                if not app._action_tasks and not app._action_cleanup_tasks:
                    break
            self.assertFalse(app._action_tasks)
            self.assertFalse(app._action_procs)

    async def test_notification_sound_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            player = Path(directory) / 'player'
            player.write_text('#!/usr/bin/env python3\n')
            player.chmod(0o755)
            app = app_for()
            async with app.run_test() as pilot:
                await self.boot(app, pilot)
                with patch.object(ui, '_AUDIO_PLAYER_CACHE', str(player)), patch.object(ui.Path, 'exists', return_value=True):
                    ui.DuskyTUI.play_reset_sound(app)
                for _ in range(100):
                    await pilot.pause(0.02)
                    if not app._action_tasks and not app._action_cleanup_tasks:
                        break
                self.assertFalse(app._action_tasks)
                self.assertFalse(app._action_cleanup_tasks)
                self.assertFalse(app._action_procs)

    async def test_notification_sound_is_stopped_on_quit(self):
        with tempfile.TemporaryDirectory() as directory:
            player = Path(directory) / 'player'
            player.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
            player.chmod(0o755)
            app = app_for()
            async with app.run_test() as pilot:
                await self.boot(app, pilot)
                with patch.object(ui, '_AUDIO_PLAYER_CACHE', str(player)), patch.object(ui.Path, 'exists', return_value=True):
                    ui.DuskyTUI.play_reset_sound(app)
                for _ in range(100):
                    await pilot.pause(0.02)
                    if app._action_procs:
                        break
                self.assertTrue(app._action_procs)
                processes = list(app._action_procs)
            self.assertTrue(all(process.returncode is not None for process in processes))
            self.assertFalse(app._action_tasks)
            self.assertFalse(app._action_cleanup_tasks)
            self.assertFalse(app._action_procs)

    async def test_sparse_tab_indices(self):
        app = app_for({3: [item()]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertEqual(app._current_tab_index(), 3)
            self.assertEqual(app.current_option_list.option_count, 1)

    async def test_ready_tabs_are_warmed_before_switch(self):
        app = app_for({0: [item('first')], 1: [item('second')], 2: [item('third')]},
                      engine=Engine({'first': '1', 'second': '2', 'third': '3'}))
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            for _ in range(100):
                if {1, 2}.issubset(app._populated_tabs):
                    break
                await pilot.pause(0.01)
            self.assertTrue({1, 2}.issubset(app._populated_tabs))
            self.assertEqual(app.schema[1][0].value, 2)
            app._refresh_all_ui()
            for _ in range(100):
                if not ({1, 2} & app._tab_dirty):
                    break
                await pilot.pause(0.01)
            self.assertFalse({1, 2} & app._tab_dirty)
            with patch.object(app, '_populate_option_list', wraps=app._populate_option_list) as populate:
                app.action_switch_tab(1)
                await pilot.pause()
                self.assertFalse(any(call.args[0] == 1 for call in populate.call_args_list))

    async def test_discovered_tab_is_warmed_after_deferred_load(self):
        app = app_for({0: [item('first')], 1: []})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertNotIn(1, app._populated_tabs)
            app._apply_deferred_tabs([1], {app.default_engine_key: {'second': '4'}},
                                     {1: [item('second')]})
            for _ in range(100):
                if 1 in app._populated_tabs:
                    break
                await pilot.pause(0.01)
            self.assertIn(1, app._populated_tabs)
            self.assertEqual(app.query_one('#list-1', ui.ConfigOptionList).option_count, 1)
            self.assertEqual(app.schema[1][0].value, 4)

    async def test_deferred_state_skips_duplicate_engine_read(self):
        class CountingEngine(Engine):
            reads = 0

            def load_state(self):
                self.reads += 1
                return super().load_state()

        engine = CountingEngine({'first': '1'})
        def discover():
            return [1], {1: [item('second')]}, {'first': '1', 'second': '4'}

        app = app_for({0: [item('first')], 1: []}, engine=engine, deferred_load=discover)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            for _ in range(100):
                if 1 in app._populated_tabs:
                    break
                await pilot.pause(0.01)
            self.assertIn(1, app._populated_tabs)
            self.assertEqual(app.schema[1][0].value, 4)
            self.assertEqual(engine.reads, 1)

    async def test_deferred_state_reloads_after_edit_during_discovery(self):
        class CountingEngine(Engine):
            reads = 0

            def load_state(self):
                self.reads += 1
                return super().load_state()

        started, release = threading.Event(), threading.Event()
        def discover():
            started.set()
            release.wait(2)
            return [1], {1: [item('second')]}, {'first': '1', 'second': '4'}

        engine = CountingEngine({'first': '1', 'second': '5'})
        app = app_for({0: [item('first')], 1: []}, engine=engine, deferred_load=discover)
        async with app.run_test() as pilot:
            try:
                await self.boot(app, pilot)
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                app._apply_value(0, 0, app.schema[0][0], 3)
            finally:
                release.set()
            for _ in range(100):
                if 1 in app._populated_tabs:
                    break
                await pilot.pause(0.01)
            self.assertIn(1, app._populated_tabs)
            self.assertEqual(app.schema[1][0].value, 5)
            self.assertEqual(engine.reads, 2)

    async def test_batch_pending_marker_clears_on_save(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._apply_value(0, 0, setting, 2)
            self.assertIn('[+]', app._build_option(setting).plain)
            await app._save_batch_async()
            self.assertNotIn('[+]', app._build_option(setting).plain)

    async def test_quit_dialog_save_applies_change_and_exits(self):
        for mode in ('batch', 'auto'):
            with self.subTest(mode=mode):
                app = app_for(mode=mode)
                engine = app.engine_pool[app.default_engine_key]
                async with app.run_test() as pilot:
                    await self.boot(app, pilot)
                    # An AUTO session can also have a queued batch change.
                    app._apply_value(0, 0, app.schema[0][0], 2, batch_mode=True)
                    with patch.object(app, 'exit') as exit_app:
                        app.action_quit()
                        await pilot.pause()
                        self.assertIsInstance(app.screen, ui.UnsavedChangesDialog)
                        await pilot.click('#btn-save')
                        for _ in range(50):
                            await pilot.pause(0.01)
                            if exit_app.called:
                                break
                        self.assertTrue(exit_app.called)
                    self.assertEqual(engine.state['x'], '2')
                    self.assertFalse(app.pending_commits)

    async def test_quit_save_retries_after_password_dialog(self):
        class AuthEngine(Engine):
            def write_batch(self, changes):
                if not self.batches:
                    self.batches.append(changes)
                    return False, 'AUTH_REQUIRED', ''
                return super().write_batch(changes)

        engine = AuthEngine()
        app = app_for(engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 2, batch_mode=True)
            with patch.object(app, 'exit') as exit_app, patch.object(ui.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
                app.action_quit()
                await pilot.pause()
                await pilot.click('#btn-save')
                for _ in range(50):
                    await pilot.pause(0.01)
                    if isinstance(app.screen, ui.PasswordScreen):
                        break
                self.assertIsInstance(app.screen, ui.PasswordScreen)
                app.screen.dismiss('password')
                for _ in range(50):
                    await pilot.pause(0.01)
                    if exit_app.called:
                        break
                self.assertTrue(exit_app.called)
            self.assertEqual(engine.state['x'], '2')
            self.assertFalse(app.pending_commits)

    async def test_accepted_save_continues_if_dialog_opens(self):
        app = app_for()
        engine = app.engine_pool[app.default_engine_key]
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 2, batch_mode=True)
            self.assertTrue(app.action_save_batch())
            app.push_screen(ui.AlertDialog('Notice'))
            for _ in range(50):
                await pilot.pause(0.01)
                if not app._save_tasks:
                    break
            self.assertEqual(engine.state['x'], '2')
            self.assertFalse(app.pending_commits)

    async def test_quit_save_failure_keeps_change_pending(self):
        engine = Engine()
        engine.fail = True
        app = app_for(engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 2, batch_mode=True)
            with patch.object(app, 'exit') as exit_app:
                app.action_quit()
                await pilot.pause()
                await pilot.click('#btn-save')
                for _ in range(50):
                    await pilot.pause(0.01)
                    if not app._save_tasks and not isinstance(app.screen, ui.UnsavedChangesDialog):
                        break
                self.assertFalse(exit_app.called)
            self.assertIn((0, 0), app.pending_commits)

    async def test_quit_waits_during_authorization_handoff(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 2, batch_mode=True)
            app._save_auth_pending = 1
            app.action_quit()
            self.assertTrue(app._quit_after_save)
            self.assertNotIsInstance(app.screen, ui.UnsavedChangesDialog)
            app.action_quit()
            self.assertNotIsInstance(app.screen, ui.UnsavedChangesDialog)
            app._save_auth_pending = 0

    async def test_password_execution_error_keeps_batch_pending(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 2, batch_mode=True)
            results = []
            with patch.object(ui.subprocess, 'run', side_effect=OSError('sudo unavailable')):
                await app._on_batch_password('password', results.append)
            self.assertEqual(results, [False])
            self.assertIn((0, 0), app.pending_commits)
            self.assertTrue(app._save_failure_pending)

    async def test_quit_flushes_pending_autosave_timer(self):
        app = app_for(mode='auto')
        engine = app.engine_pool[app.default_engine_key]
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 2)
            with patch.object(app, 'exit') as exit_app:
                app.action_quit()
                for _ in range(50):
                    await pilot.pause(0.01)
                    if exit_app.called:
                        break
                self.assertTrue(exit_app.called)
            self.assertEqual(engine.state['x'], '2')
            self.assertFalse(app.pending_commits)

    async def test_failed_coalesced_autosave_keeps_unsaved_value_pending(self):
        engine = Engine()
        engine.fail = True
        app = app_for(mode='auto', engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._apply_value(0, 0, setting, 1)
            app._apply_value(0, 0, setting, 2)
            await pilot.pause(0.4)
            self.assertEqual(setting.value, 1)
            self.assertIn((0, 0), app.pending_commits)

    async def test_slow_autosave_does_not_rollback_new_edit(self):
        engine = Engine()
        engine.started, engine.release = threading.Event(), threading.Event()
        app = app_for(mode='auto', engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._apply_value(0, 0, setting, 1)
            await pilot.pause(0.3)
            self.assertTrue(engine.started.is_set())
            app._apply_value(0, 0, setting, 2)
            engine.release.set()
            await pilot.pause(0.4)
            self.assertEqual(setting.value, 2)
            self.assertEqual(engine.state['x'], '2')
            self.assertEqual(app._committed[(0, 0)], 2)
            self.assertFalse(app.pending_commits)

    async def test_preset_snapshot_ignores_other_target(self):
        app = app_for({0: [item(value=1), item(value=9, target_file_override='/tmp/other')]})
        app.engine_pool[('fake', '/tmp/other')] = Engine()
        app.enable_user_presets = True
        with tempfile.TemporaryDirectory() as path:
            app.user_presets_dir = Path(path)
            async with app.run_test() as pilot:
                await self.boot(app, pilot)
                app.action_save_preset()
                await pilot.pause()
                app.screen.dismiss('snapshot')
                await pilot.pause()
                import json
                self.assertEqual(json.loads((Path(path) / 'snapshot.json').read_text()), {'x': 1})

    async def test_folder_reset_is_undoable(self):
        folder = item('folder', type_='menu', default=None, is_parent=True, expanded=True)
        child = item('child', value=4, parent_ref='folder')
        app = app_for({0: [folder, child]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.action_reset_item()
            self.assertEqual(child.value, 0)
            self.assertTrue(app.undo_stack)
            app.action_undo()
            self.assertEqual(child.value, 4)

    async def test_read_failure_blocks_mutations(self):
        engine = Engine()
        def fail():
            raise OSError('unavailable')
        engine.load_state = fail
        app = app_for(engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            setting = app.schema[0][0]
            app._safe_apply_value(0, 0, setting, 5)
            self.assertEqual(setting.value, 0)
            self.assertFalse(engine.writes)

    async def test_background_action_cancel_reaps_process(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.execute_action(item('action', type_='action', default='sleep 30', force_interactive=False))
            await pilot.pause(0.05)
            procs = list(app._action_procs)
            self.assertTrue(procs)
            await app._shutdown_background_actions()
            self.assertTrue(all(proc.returncode is not None for proc in procs))
            self.assertFalse(app._action_procs)


    async def test_cyclic_parents_remain_visible(self):
        a = item('a', type_='menu', default=None, parent_ref='b', expanded=True)
        b = item('b', type_='menu', default=None, parent_ref='a', expanded=True)
        app = app_for({0: [a, b]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertEqual(app.current_option_list.option_count, 2)

    async def test_external_reload_updates_committed_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config'
            target.write_text('initial')
            engine = Engine({'x': 1})
            engine.target_path = str(target)
            app = app_for(engine=engine)
            async with app.run_test() as pilot:
                await self.boot(app, pilot)
                await app.watch_target_file()
                target.write_text('changed')
                engine.state['x'] = 8
                await app.watch_target_file()
                self.assertEqual(app.schema[0][0].value, 8)
                self.assertEqual(app._committed[(0, 0)], 8)
                self.assertFalse(app._item_is_pending(app.schema[0][0]))

    async def test_theme_reload_accepts_older_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'theme.json'
            target.write_text('{"accent": "#123456"}')
            app = app_for(theme_path=str(target))
            async with app.run_test() as pilot:
                await self.boot(app, pilot)
                old = target.stat().st_mtime
                target.write_text('{"accent": "#abcdef"}')
                os.utime(target, (old - 10, old - 10))
                await app.watch_theme_file()
                self.assertEqual(app.theme_colors['accent'], '#abcdef')

    async def test_external_reload_hides_removed_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config'
            target.write_text('initial')
            engine = Engine({'x': 1})
            engine.target_path = str(target)
            app = app_for(engine=engine, hide_missing_items=True)
            async with app.run_test() as pilot:
                await self.boot(app, pilot)
                await app.watch_target_file()
                target.write_text('changed')
                engine.state.clear()
                await app.watch_target_file()
                self.assertFalse(app.schema[0][0].exists_in_target)
                self.assertEqual(app.current_option_list.option_count, 0)

    async def test_named_custom_view_respects_option_groups(self):
        app = app_for({0: [item('visible', group='Visible'), item('hidden', group='Hidden')]},
                      custom_views={'Tab 0': {'view': 'Content', 'show_options': True,
                                              'option_groups': ['Visible']}})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            ids = {option.id for option in app.current_option_list.options}
            self.assertIn('item_0_0', ids)
            self.assertNotIn('item_0_1', ids)

    async def test_help_mounts_once_on_demand_and_tracks_selection(self):
        app = app_for({0: [item('first', extended_help='First documentation'),
                           item('second', extended_help='Second documentation')]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertFalse(app.query('#help-markdown'))
            await app.action_toggle_help()
            await pilot.pause()
            markdown = app.query_one('#help-markdown', ui.Markdown)
            self.assertEqual(markdown._dusky_help_text, 'First documentation')
            app.current_option_list.highlighted = 1
            await pilot.pause()
            self.assertEqual(markdown._dusky_help_text, 'Second documentation')
            await app.action_toggle_help()
            await app.action_toggle_help()
            self.assertEqual(len(app.query('#help-markdown')), 1)

    async def test_null_state_is_present_during_boot_and_refresh(self):
        setting = item('nullable', type_='string', default='default')
        engine = Engine({'nullable': None})
        app = app_for({0: [setting]}, engine=engine, hide_missing_items=True)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            self.assertTrue(setting.exists_in_target)
            self.assertIsNone(setting.value)
            app._apply_refreshed_states({app.default_engine_key: {}})
            self.assertFalse(setting.exists_in_target)
            app._apply_refreshed_states({app.default_engine_key: {'nullable': None}})
            self.assertTrue(setting.exists_in_target)
            self.assertIsNone(setting.value)

    async def test_large_decimal_input_is_exact(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app.prompt_string(0, 0, app.schema[0][0])
            await pilot.pause()
            app.screen.dismiss('09007199254740993')
            await pilot.pause()
            self.assertEqual(app.schema[0][0].value, 9007199254740993)

    async def test_background_action_timeout_reaps_process(self):
        app = app_for()
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            with patch.object(ui, '_ACTION_TIMEOUT', 0.05):
                app.execute_action(item('action', type_='action', default='sleep 30', force_interactive=False))
                for _ in range(150):
                    await pilot.pause(0.02)
                    if not app._action_tasks and not app._action_cleanup_tasks:
                        break
            self.assertFalse(app._action_tasks)
            self.assertFalse(app._action_procs)


    async def test_autosave_trigger_finishes_without_pending_commit(self):
        trigger = item(type_='bool', default=False, options=['trigger'])
        app = app_for({0: [trigger]}, mode='auto')
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, trigger, True)
            await pilot.pause(0.5)
            self.assertIs(trigger.value, False)
            self.assertFalse(app.pending_commits)
            self.assertFalse(app._committed[(0, 0)])

    async def test_batch_trigger_duplicates_run_once(self):
        a = item(type_='bool', default=False, options=['trigger'])
        b = item(type_='bool', default=False, options=['trigger'])
        app = app_for({0: [a], 1: [b]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, a, True)
            await app._save_batch_async()
            engine = app.engine_pool[app.default_engine_key]
            self.assertEqual(len(engine.writes), 1)
            self.assertFalse(engine.batches)
            self.assertEqual((a.value, b.value), (False, False))
            self.assertFalse(app.pending_commits)
            self.assertFalse(app._save_tasks)

    async def test_trigger_reset_does_not_cancel_newer_edit(self):
        trigger = item(type_='bool', default=False, options=['trigger'])
        app = app_for({0: [trigger]}, mode='auto')
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, trigger, True)
            await pilot.pause(0.3)
            app._apply_value(0, 0, trigger, True)
            await pilot.pause(0.5)
            engine = app.engine_pool[app.default_engine_key]
            self.assertEqual(len(engine.writes), 2)
            self.assertFalse(app.pending_commits)


    async def test_deferred_replacement_remaps_pending_and_undo(self):
        app = app_for({0: [item('x'), item('y')]})
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, app.schema[0][0], 7)
            replacement = [item('y'), item('x'), item('new')]
            app._apply_deferred_tabs([0], {app.default_engine_key: {'x': 0, 'y': 0, 'new': 3}}, {0: replacement})
            self.assertEqual(replacement[1].value, 7)
            self.assertEqual(replacement[2].value, 3)
            self.assertEqual(app.pending_commits, {(0, 1)})
            app.action_undo()
            self.assertEqual(replacement[1].value, 0)
            self.assertFalse(app.pending_commits)


    async def test_failed_batch_trigger_is_not_retried(self):
        trigger = item(type_='bool', default=False, options=['trigger'])
        engine = Engine()
        engine.fail = True
        app = app_for({0: [trigger]}, engine=engine)
        async with app.run_test() as pilot:
            await self.boot(app, pilot)
            app._apply_value(0, 0, trigger, True)
            await app._save_batch_async()
            self.assertEqual(len(engine.writes), 1)
            self.assertFalse(engine.batches)



class UtilityTests(unittest.TestCase):
    def test_color_malformed_does_not_raise(self):
        self.assertEqual(ui.color_to_rgb('hsl(.., 50%, 50%)'), (128, 128, 128))

    def test_color_rgb_clamped(self):
        self.assertEqual(ui.color_to_rgb('rgb(999, 10, 0)'), (255, 10, 0))

    def test_color_negative_hue(self):
        self.assertEqual(ui.color_to_rgb('hsl(-120, 100%, 50%)'), (0, 0, 255))

    def test_color_short_alpha_preserved(self):
        self.assertEqual(ui.format_rgb('Red', 'hex', '#1234'), '#ff000044')

    def test_override_path_resolution_cached(self):
        app = app_for()
        setting = item(target_file_override='/tmp/example')
        app._get_item_engine_info(setting)
        with patch.object(Path, 'resolve', side_effect=AssertionError('repeated disk lookup')):
            app._get_item_engine_info(setting)
            app._uid_engine_key(setting)

    def test_bad_preset_does_not_hide_good_presets(self):
        app = app_for()
        app.enable_user_presets = True
        with tempfile.TemporaryDirectory() as path:
            app.user_presets_dir = Path(path)
            (Path(path) / 'bad.json').write_bytes(b'\xff')
            (Path(path) / 'good.json').write_text('{"x": 1}')
            records = app._read_user_presets()
            self.assertEqual(len(records), 2)
            self.assertTrue(records[0][2])
            self.assertEqual(records[1][1], {'x': 1})

    def test_preset_matrix_incremental_matches_rebuild(self):
        setting = item()
        preset = item('preset', type_='preset', default=None, preset_payload={'x': 2})
        app = app_for({0: [setting, preset]})
        for value, exists in [(2, True), (1, True), (1, False), (2, True)]:
            setting.value, setting.exists_in_target = value, exists
            app._preset_matrix.on_item_changed(setting)
            expected = app._preset_matrix.ratio(preset)
            app._preset_matrix.rebuild(app._configurable_items + app._preset_items)
            self.assertEqual(app._preset_matrix.ratio(preset), expected)

    def test_interactive_command_without_operator_spaces(self):
        self.assertTrue(app_for()._check_interactive('true&&nvim /tmp/example'))

    def test_same_preset_uid_does_not_double_increment(self):
        setting = item()
        setting.exists_in_target = True
        a = item('preset', type_='preset', default=None, preset_payload={'x': 2})
        b = item('preset', type_='preset', default=None, preset_payload={'x': 2})
        app = app_for({0: [setting, a], 1: [b]})
        setting.value = 2
        app._preset_matrix.on_item_changed(setting)
        self.assertEqual(app._preset_matrix.ratio(a), 1.0)

    def test_custom_factory_signature_is_cached(self):
        def factory(app):
            return 'content'
        widget = ui.CustomRichTabWidget(factory)
        self.assertEqual(widget._invoke_factory(), 'content')
        with patch('inspect.signature', side_effect=AssertionError('repeated introspection')):
            self.assertEqual(widget._invoke_factory(), 'content')

    def test_custom_factory_type_error_is_not_retried(self):
        calls = []
        def factory(*args):
            calls.append(args)
            raise TypeError('inside callback')
        widget = ui.CustomRichTabWidget(factory)
        with patch('inspect.signature', side_effect=ValueError('opaque callable')):
            with self.assertRaisesRegex(TypeError, 'inside callback'):
                widget._invoke_factory()
        self.assertEqual(len(calls), 1)

    def test_atomic_preset_failure_preserves_original(self):
        app = app_for()
        with tempfile.TemporaryDirectory() as path:
            target = Path(path) / 'preset.json'
            app._write_preset_atomically(target, {'x': 1}, exclusive=True)
            with self.assertRaises(FileExistsError):
                app._write_preset_atomically(target, {'x': 2}, exclusive=True)
            self.assertEqual(target.read_text(), '{\n    "x": 1\n}')
            self.assertEqual(len(list(Path(path).iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
