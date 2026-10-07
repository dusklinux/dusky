"""Exercise lazy first-use, search, disposal and CPU-aware startup on real GTK."""
import gc
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import weakref
from unittest.mock import patch
import dusky_control_center as cc
from lib import rows, utility


def drain_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    context = cc.GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('GTK operation did not finish')


class LazyStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc.Adw.init()

    def test_read_only_settings_do_not_create_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'value').write_text('true', encoding='utf-8')
            with patch.object(utility._SettingsWriteBuffer, '_instance', None), patch.object(utility, '_settings_dir_cache', utility._ResolvedDirectoryCache(root)):
                self.assertTrue(utility.load_setting('value', False))
                self.assertIsNone(utility._SettingsWriteBuffer._instance)

    def test_worker_budget_respects_process_cpu_count(self):
        for count, expected in ((1, 5), (2, 6), (64, 32)):
            result = subprocess.run([sys.executable, '-X', f'cpu_count={count}', '-c',
                'import dusky_control_center; from lib import rows; print(rows.EXECUTOR_MAX_WORKERS)'],
                cwd=cc.SCRIPT_DIR, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(int(result.stdout), expected)

    def test_optional_modules_remain_lazy_until_first_use(self):
        code = '''import sys
import dusky_control_center as c
from lib import rows
assert "lib.actions" not in sys.modules
assert "lib.service_manager" not in sys.modules
c.Adw.init()
r = rows.ServiceToggleRow({"service": "fixture"})
assert "lib.service_manager" in sys.modules
assert "lib.actions" not in sys.modules
rows.actions.run_action({}, "fixture", lambda result: None)
assert "lib.actions" in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=cc.SCRIPT_DIR, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lazy_dependency_failure_occurs_at_first_use(self):
        code = '''import sys
import dusky_control_center
from lib import rows
sys.modules["lib.actions"] = None
try:
    rows.actions.run_action({}, "fixture", lambda result: None)
except ImportError:
    pass
else:
    raise AssertionError("Missing dependency was not reported")
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=cc.SCRIPT_DIR, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_configuration_worker_keeps_ui_on_main_thread(self):
        app = cc.DuskyControlCenter()
        app.set_application_id(cc.APP_ID + '.startup_test')
        app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE)
        threads = {}
        load, build = app._load_config_and_css_sync, app._build_ui
        def tracked_load():
            threads['load'] = threading.get_ident()
            return load()
        def tracked_build():
            threads['build'] = threading.get_ident()
            return build()
        with patch.object(app, '_load_config_and_css_sync', side_effect=tracked_load), patch.object(app, '_build_ui', side_effect=tracked_build):
            app.register(None)
        try:
            self.assertNotEqual(threads['load'], threading.get_ident())
            self.assertEqual(threads['build'], threading.get_ident())
            self.assertIsNone(app._state.config_error)
        finally:
            app._window.destroy()
            app.release()

    def test_expander_materializes_once_and_preserves_edits(self):
        item = {'type': 'entry', 'properties': {'title': 'Child'}}
        built = []
        def builder(config, context):
            child = rows.EntryRow(config['properties'], context=context)
            built.append(child)
            return child
        row = rows.ExpanderRow({'title': 'Parent'}, [item], {'row_builder': builder})
        self.assertFalse(row._children_built)
        row.set_expanded(True)
        built[0].set_text('unsaved edit')
        for _ in range(100):
            row.set_expanded(False)
            row.set_expanded(True)
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0].get_text(), 'unsaved edit')
        rows._batch_source_remove(*rows._dispose_row(row))
        rows._batch_source_remove(*rows._dispose_row(built[0]))

    def test_parallel_startup_errors_produce_recoverable_error_page(self):
        app = cc.DuskyControlCenter()
        app.set_application_id(cc.APP_ID + '.invalid_startup_test')
        app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE)
        with patch.object(app, '_do_load_config', return_value=({'pages': []}, 'Fixture invalid TOML')), patch.object(app, '_do_load_css', side_effect=PermissionError('Fixture denied')):
            app.register(None)
        try:
            self.assertEqual(app._state.config_error, 'Fixture invalid TOML')
            self.assertEqual(app._stack.get_visible_child_name(), cc.ERROR_PAGE_ID)
            self.assertIsNone(app._search_page)
        finally:
            app._window.destroy()
            app._remove_css_provider()
            app.release()

    def test_search_reveals_only_nested_ancestors(self):
        app = cc.DuskyControlCenter()
        target = {'type': 'button', 'properties': {'title': 'Target'}}
        nested = {'type': 'expander', 'properties': {'title': 'Nested'}, 'items': [target]}
        other = {'type': 'expander', 'properties': {'title': 'Other'}, 'items': [
            {'type': 'button', 'properties': {'title': 'Unrelated'}}]}
        root = rows.ExpanderRow({'title': 'Parent'}, [nested, other], {'row_builder': app._build_item_row})
        group = cc.Adw.PreferencesGroup()
        group.add(root)
        window = cc.Adw.Window(content=group)
        try:
            found = app._find_widget_by_name(root, app._generate_widget_id(target))
            self.assertIsNotNone(found)
            self.assertTrue(root.get_expanded())
            nested_row = app._find_widget_by_name(root, app._generate_widget_id(nested))
            other_row = app._find_widget_by_name(root, app._generate_widget_id(other))
            self.assertTrue(nested_row.get_expanded())
            self.assertFalse(other_row._children_built)
            window.present()
            drain_until(found.get_mapped)
        finally:
            window.destroy()

    def test_unopened_expander_cleanup_releases_context(self):
        row = rows.ExpanderRow({'title': 'Parent'}, [], {'row_builder': lambda *_: None})
        group = cc.Adw.PreferencesGroup()
        group.add(row)
        window = cc.Adw.Window(content=group)
        window.present()
        drain_until(row.get_mapped)
        reference = weakref.ref(row)
        group.remove(row)
        self.assertTrue(row._state.is_destroyed)
        row.set_expanded(True)
        self.assertFalse(row._children_built)
        del row
        gc.collect()
        self.assertIsNone(reference())
        window.destroy()

    def test_real_search_navigation_highlights_lazy_target(self):
        app = cc.DuskyControlCenter()
        app.set_application_id(cc.APP_ID + '.search_test')
        app.set_flags(cc.Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        target = {'type': 'button', 'properties': {'title': 'Deep target'}}
        outer = {'type': 'expander', 'properties': {'title': 'Outer'}, 'items': [target]}
        app._state.config = {'pages': [{'id': 'fixture', 'title': 'Fixture', 'layout': [
            {'type': 'section', 'items': [outer]}]}]}
        app._clear_and_rebuild_ui(0)
        try:
            self.assertIsNone(app._search_page)
            app._window.present()
            drain_until(app._window.get_mapped)
            hit = next(app._iter_matching_items('deep target'))
            app._execute_search('deep target')
            app._navigate_from_search(hit)
            page = app._stack.get_visible_child().get_visible_page()
            def highlighted():
                # Observe without invoking the helper that materializes children.
                pending = [page]
                while pending:
                    widget = pending.pop()
                    if widget.get_name() == hit.unique_id:
                        return widget.get_mapped() and widget.has_css_class('highlight-pulse')
                    child = widget.get_first_child()
                    while child is not None:
                        pending.append(child)
                        child = child.get_next_sibling()
                return False
            drain_until(highlighted)
        finally:
            app._cancel_debounce()
            app._window.destroy()
            app._remove_css_provider()
            app.release()

    def test_expanded_child_queries_pause_and_resume(self):
        item = {'type': 'label', 'properties': {'title': 'Child'}, 'value': {'type': 'exec', 'command': 'printf Ready'}}
        app = cc.DuskyControlCenter()
        row = rows.ExpanderRow({'title': 'Parent'}, [item], {'row_builder': app._build_item_row})
        group = cc.Adw.PreferencesGroup()
        group.add(row)
        window = cc.Adw.Window(content=group)
        try:
            window.present()
            drain_until(row.get_mapped)
            self.assertFalse(row._children_built)
            row.set_expanded(True)
            child = app._find_widget_by_name(row, app._generate_widget_id(item))
            drain_until(lambda: child.value_label.get_label() == 'Ready')
            window.set_visible(False)
            self.assertTrue(all(slot.source_id == 0 and slot.cancellable is None for slot in child._state._slots))
            window.present()
            drain_until(child.get_mapped)
        finally:
            window.destroy()


if __name__ == '__main__':
    unittest.main()
