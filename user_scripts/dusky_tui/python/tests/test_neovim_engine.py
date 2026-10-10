"""Neovim engine integration tests using isolated deployed-config fixtures."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.neovim import NeovimEngine, bindings
from python.frontend.core_types import ConfigItem


class NeovimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dusky nvim ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'options.lua'
        self.path.write_text('-- header\nvim.opt.number = true -- numbers\nvim.opt.scrolloff = 10\nvim.opt.spelllang = "en_us"\n', encoding='utf-8')
        self.engine = NeovimEngine(str(self.path))
        self.engine.load_state()

    def test_comment_preserving_batch(self):
        self.path.chmod(0o640)
        self.engine.load_state()
        self.assertTrue(self.engine.write_batch([('number', 'options', 'false', 'bool'), ('scrolloff', 'options', '12', 'int')])[0])
        self.assertEqual(self.path.read_text(encoding='utf-8'), '-- header\nvim.opt.number = false -- numbers\nvim.opt.scrolloff = 12\nvim.opt.spelllang = "en_us"\n')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.engine.load_state()['options/scrolloff'], 12)

    def test_external_change_rejected_then_reloaded(self):
        self.path.write_text(self.path.read_text(encoding='utf-8') + '-- other editor\n', encoding='utf-8')
        before = self.path.read_bytes()
        self.assertFalse(self.engine.write_value('number', 'options', 'false', 'bool')[0])
        self.assertEqual(self.path.read_bytes(), before)
        self.engine.load_state()
        self.assertTrue(self.engine.write_value('number', 'options', 'false', 'bool')[0])

    def test_invalid_native_option_rejects_whole_batch(self):
        before = self.path.read_bytes()
        self.assertFalse(self.engine.write_batch([('number', 'options', 'false', 'bool'), ('spelllang', 'options', 'invalid!', 'string')])[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_binding_rejects_whole_batch(self):
        before = self.path.read_bytes()
        self.assertFalse(self.engine.write_batch([('number', 'options', 'false', 'bool'), ('absent', 'options', '1', 'int')])[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_assignments_rejected(self):
        self.path.write_text('vim.opt.number = true\nvim.opt.number = false\n', encoding='utf-8')
        self.engine.load_state()
        self.assertFalse(self.engine.write_value('number', 'options', 'true', 'bool')[0])

    def test_complex_assignment_and_duplicate_rejected(self):
        self.path.write_text('vim.opt.scrolloff = 2 * 5\nvim.opt.scrolloff = 10\n', encoding='utf-8')
        self.engine.load_state()
        self.assertNotIn('options/scrolloff', self.engine.load_state())
        self.assertFalse(self.engine.write_value('scrolloff', 'options', '12', 'int')[0])

    def test_comments_and_long_strings_are_not_bindings(self):
        text = '-- vim.opt.number = false\n--[=[\nvim.opt.number = false\n]=]\nlocal example=[=[vim.opt.number = false]=]\nvim.opt.number = true\n'
        self.path.write_text(text, encoding='utf-8')
        self.engine.load_state()
        self.assertTrue(self.engine.write_value('number', 'options', 'false', 'bool')[0])
        self.assertEqual(self.path.read_text(encoding='utf-8'), text[:-5] + 'false\n')

    def test_nested_plugin_fields_do_not_collide(self):
        self.path.write_text('return {opts={latex={enabled=false},checkbox={enabled=true},code={width="block"}}}\n', encoding='utf-8')
        self.engine.load_state()
        self.assertTrue(self.engine.write_value('enabled', 'opts/checkbox', 'false', 'bool')[0])
        state = self.engine.load_state()
        self.assertFalse(state['opts/latex/enabled'])
        self.assertFalse(state['opts/checkbox/enabled'])
        self.assertEqual(state['opts/code/width'], 'block')

    def test_unicode_quotes_and_control_round_trip(self):
        self.path.write_text('return {opts={label="old"}}\n', encoding='utf-8')
        self.engine.load_state()
        value = '🌙 "quoted" \\ path\n\t\0'
        self.assertTrue(self.engine.write_value('label', 'opts', value)[0])
        self.assertEqual(self.engine.load_state()['opts/label'], value)

    def test_float_scientific_round_trip(self):
        self.path.write_text('return {opts={max_file_size=1}}\n', encoding='utf-8')
        self.engine.load_state()
        self.assertTrue(self.engine.write_value('max_file_size', 'opts', '0.000001', 'float')[0])
        self.assertEqual(self.engine.load_state()['opts/max_file_size'], 0.000001)
        before = self.path.read_bytes()
        for value in ['nan', 'inf', '-1']:
            self.assertFalse(self.engine.write_value('max_file_size', 'opts', value, 'float')[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_schema_bounds_and_cycles_are_validated(self):
        self.path.write_text('return {opts={view={width=30,side="left"}}}\n', encoding='utf-8')
        items = [ConfigItem(label='Width', key='width', scope='opts/view', type_='int', default=30, min_val=15, max_val=120),
                 ConfigItem(label='Side', key='side', scope='opts/view', type_='cycle', default='left', options=['left', 'right'])]
        engine = NeovimEngine(str(self.path), items=items)
        engine.load_state()
        for key, value, kind in [('width', '0', 'int'), ('width', '121', 'int'), ('side', 'middle', 'cycle')]:
            self.assertFalse(engine.write_value(key, 'opts/view', value, kind)[0])
        self.assertTrue(engine.write_value('side', 'opts/view', 'right', 'cycle')[0])

    def test_threshold_inserted_before_bootstrap_only_once(self):
        path = self.root / 'init.lua'
        path.write_text('vim.g.dusky_nvim = true\nrequire("config.lazy")\n', encoding='utf-8')
        engine = NeovimEngine(str(path));self.assertEqual(engine.load_state()['globals/dusky_bigfile_size'], 1048576)
        self.assertTrue(engine.write_value('dusky_bigfile_size', 'globals', '2097152', 'int')[0])
        self.assertTrue(engine.write_value('dusky_bigfile_size', 'globals', '4194304', 'int')[0])
        text = path.read_text(encoding='utf-8')
        self.assertEqual(text.count('vim.g.dusky_bigfile_size'), 1)
        self.assertLess(text.index('vim.g.dusky_bigfile_size'), text.index('require'))
        self.assertFalse(engine.write_value('dusky_bigfile_size', 'globals', '0', 'int')[0])

    def test_complex_threshold_is_not_overwritten(self):
        path = self.root / 'init.lua'
        path.write_text('vim.g.dusky_bigfile_size = 1024 * 1024\nrequire("config.lazy")\n', encoding='utf-8')
        engine = NeovimEngine(str(path));engine.load_state()
        before = path.read_bytes()
        self.assertFalse(engine.write_value('dusky_bigfile_size', 'globals', '2097152', 'int')[0])
        self.assertEqual(path.read_bytes(), before)

    def test_bootstrap_comments_are_not_insertion_targets(self):
        path = self.root / 'init.lua'
        comment = '--[=[\nrequire("config.lazy")\n]=]\n'
        path.write_text(comment + 'require("config.lazy")\n', encoding='utf-8')
        engine = NeovimEngine(str(path))
        engine.load_state()
        self.assertTrue(engine.write_value('dusky_bigfile_size', 'globals', '32', 'int')[0])
        self.assertTrue(path.read_text(encoding='utf-8').startswith(comment))
        self.assertEqual(engine.load_state()['globals/dusky_bigfile_size'], 32)
        path.write_text(comment, encoding='utf-8')
        engine.load_state()
        self.assertFalse(engine.write_value('dusky_bigfile_size', 'globals', '32', 'int')[0])

    def test_invalid_lua_and_missing_target(self):
        self.path.write_text('invalid lua !\n', encoding='utf-8')
        with self.assertRaises(ValueError):self.engine.load_state()
        with self.assertRaises(FileNotFoundError):NeovimEngine(str(self.root / 'missing.lua')).load_state()

    def test_empty_valid_file_returns_mapping(self):
        self.path.write_text('-- no supported bindings\n', encoding='utf-8')
        self.assertEqual(self.engine.load_state(), {})
        self.assertFalse(self.engine.write_value('number', 'options', 'true', 'bool')[0])

    def test_schema_router_defaults_and_every_binding(self):
        home = Path.home(); config = self.root / 'xdg' / 'nvim'
        shutil.copytree(home / '.config/nvim', config)
        schema_path = home / 'user_scripts/nvim/tui_dusky_nvim.py'
        router = home / 'user_scripts/dusky_tui/python/main/main.py'
        env = os.environ | {'XDG_CONFIG_HOME': str(config.parent)}
        spec = importlib.util.spec_from_file_location('test_nvim_schema', schema_path)
        schema = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, env):spec.loader.exec_module(schema)
        uids = set(); routes = {}
        for tab in schema.SCHEMA.values():
            for item in tab:
                self.assertTrue(item.extended_help)
                self.assertNotIn(item.uid, uids);uids.add(item.uid)
                if item.type_ not in {'action', 'menu', 'preset'}:
                    path = item.target_file_override or schema.TARGET_FILE
                    self.assertTrue(Path(path).is_relative_to(config))
                    routes.setdefault(path, []).append(item)
        for path, items in routes.items():
            engine = NeovimEngine(path, items=items)
            state = engine.load_state()
            for item in items:self.assertIn(f'{item.scope}/{item.key}', state)
            self.assertTrue(engine.write_batch([(item.key, item.scope, item.serialize(item.default), item.type_) for item in items])[0])
        for args, expected in [(['--export-state'], 0), (['--set','options.scrolloff=14'], 0),
                               (['--set','opts/view.side=right'], 0), (['--set','opts/view.width=0'], 1),
                               (['--set','width=40'], 1), (['--reset-key','options.scrolloff'], 0), (['--default'], 0)]:
            p = subprocess.run([sys.executable, str(router), str(schema_path), *args], env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
        self.assertEqual(NeovimEngine(str(config/'lua/config/options.lua')).load_state()['options/scrolloff'], 10)

    def test_router_backup_restore_all_four_deployed_targets(self):
        home = Path.home()
        fake_home = self.root / 'home'
        (fake_home / 'user_scripts').mkdir(parents=True)
        (fake_home / 'user_scripts/dusky_tui').symlink_to(home / 'user_scripts/dusky_tui')
        config = self.root / 'xdg/nvim'
        shutil.copytree(home / '.config/nvim', config)
        targets = ['init.lua', 'lua/config/options.lua', 'lua/plugins/nvim-tree.lua',
                   'lua/plugins/render-markdown.lua']
        original = {name: (config / name).read_bytes() for name in targets}
        script = home / 'user_scripts/nvim/tui_dusky_nvim.py'
        env = os.environ | {'HOME': str(fake_home), 'XDG_CONFIG_HOME': str(config.parent)}
        def run(*args):
            return subprocess.run([sys.executable, str(script), *args], env=env,
                                  capture_output=True, text=True, encoding='utf-8', timeout=15)
        result = run('--backup')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list((fake_home / 'Documents/dusky_backups/tui_reset').glob('*.latest.bak'))), 4)
        self.assertEqual(run('--set', 'options.scrolloff=20').returncode, 0)
        self.assertEqual(run('--set', 'opts/view.width=40').returncode, 0)
        result = run('--restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({name: (config / name).read_bytes() for name in targets}, original)


if __name__ == '__main__':
    unittest.main()
