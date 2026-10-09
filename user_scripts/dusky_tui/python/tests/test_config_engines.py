"""Regression fixtures for six configuration engines; never edit live configs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.ini import IniConfigEngine
from python.engines.bridged_ini import BridgedIniEngine
from python.engines.flatdotconfig import FlatDotConfigEngine
from python.engines.matugen import MatugenEngine
from python.engines.toml import TomlEngine
from python.engines.lua import HyprlandLuaEngine

FIXTURES = (
    (IniConfigEngine, '[main]\nx=1\n', 'x', 'main', 'main/x', '2', 'int'),
    (BridgedIniEngine, '[main]\n#x=0\nx=1\n', 'x', 'main', 'main/x', '2', 'int'),
    (FlatDotConfigEngine, 'main.x 1\n', 'x', 'main', 'main/x', '2', 'int'),
    (MatugenEngine, '[templates.main]\nx="1"\n', 'main', 'DEFAULT', 'main', 'false', 'bool'),
    (TomlEngine, '[main]\nx=1\n', 'x', 'main', 'main/x', '2', 'int'),
    (HyprlandLuaEngine, 'hl.config({main={x=1}})\n', 'x', 'main', 'main/x', '2', 'int'),
)


class ConfigEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.lua'

    def fixture(self, cls, content):
        self.path.write_text(content, encoding='utf-8')
        engine = cls(str(self.path))
        engine.load_state()
        return engine

    def test_cache_is_cleared_when_file_disappears(self):
        for cls, content, *_ in FIXTURES:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                self.path.unlink()
                self.assertEqual(engine.load_state(), {})
                self.assertEqual(engine.cache, {})

    def test_repeated_batch_binding_is_last_write_wins(self):
        for cls, content, key, scope, lookup, value, kind in FIXTURES:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                change = (key, scope, value, kind)
                result = engine.write_batch([change, change])
                self.assertTrue(result[0], result)
                self.assertEqual(str(engine.load_state()[lookup]).lower(), value)

    def test_older_external_mtime_is_rejected(self):
        for cls, content, key, scope, _, value, kind in FIXTURES:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                before = self.path.stat()
                self.path.write_text(content + '\n', encoding='utf-8')
                os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns - 1_000_000_000))
                external = self.path.read_bytes()
                result = engine.write_value(key, scope, value, kind)
                self.assertFalse(result[0], result)
                self.assertEqual(self.path.read_bytes(), external)

    def test_failed_replace_keeps_original_and_cleans_staging(self):
        for cls, content, key, scope, _, value, kind in FIXTURES:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                cache = engine.cache.copy()
                original = self.path.read_bytes()
                with patch('os.replace', side_effect=OSError('fixture replace failure')):
                    result = engine.write_value(key, scope, value, kind)
                self.assertFalse(result[0], result)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(engine.cache, cache)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replacement_with_same_mtime_is_rejected(self):
        for cls, content, key, scope, _, value, kind in FIXTURES:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                before = self.path.stat()
                replacement = self.path.with_name('replacement')
                replacement.write_text(content + '\n', encoding='utf-8')
                os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                replacement.replace(self.path)
                original = self.path.read_bytes()
                result = engine.write_value(key, scope, value, kind)
                self.assertFalse(result[0], result)
                self.assertEqual(self.path.read_bytes(), original)

    def test_failed_file_sync_preserves_original_and_cleans_staging(self):
        for cls, content, key, scope, _, value, kind in FIXTURES:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                original = self.path.read_bytes()
                with patch('os.fsync', side_effect=OSError('fixture sync failure')):
                    result = engine.write_value(key, scope, value, kind)
                self.assertFalse(result[0], result)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_crlf_is_preserved_for_line_formats(self):
        for cls, content in ((IniConfigEngine, '[main]\r\nx=1\r\n'), (FlatDotConfigEngine, 'main.x 1\r\n')):
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                self.assertTrue(engine.write_value('x', 'main', '2', 'int')[0])
                self.assertIn(b'2\r\n', self.path.read_bytes())

    def test_permissions_survive_commit(self):
        for cls, content, key, scope, _, value, kind in FIXTURES:
            with self.subTest(engine=cls.__name__):
                self.path.write_text(content, encoding='utf-8')
                self.path.chmod(0o640)
                engine = cls(str(self.path))
                engine.load_state()
                result = engine.write_value(key, scope, value, kind)
                self.assertTrue(result[0], result)
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_ini_defaults_and_comment_precedence(self):
        engine = self.fixture(BridgedIniEngine, '[main] # section\n#x=0\nx=1\n#x=9\n#y=2\n')
        self.assertEqual(engine.cache, {'main/x': '1', 'main/y': '2'})
        self.assertTrue(engine.write_value('x', 'main', '3', 'int')[0])
        self.assertEqual(engine.cache['main/x'], '3')
        active = [line for line in self.path.read_text(encoding='utf-8').splitlines() if line.startswith('x=')]
        self.assertEqual(active, ['x=3'])

    def test_ini_missing_deletions_and_internal_state_are_successful_noops(self):
        engine = self.fixture(IniConfigEngine, '[main]\nx=1\n')
        original = self.path.read_bytes()
        result = engine.write_batch([('absent', 'missing', '__DELETE__', 'bool'), ('target', '__ui', 'hello', 'string')])
        self.assertTrue(result[0], result)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(engine.cache['__ui/target'], 'hello')

    def test_ini_appends_after_unterminated_line_and_resolves_scope(self):
        engine = self.fixture(IniConfigEngine, 'root=1')
        result = engine.write_batch([('name', '__ui', 'player', 'string'), ('x', 'app="{name}"', '2', 'int')])
        self.assertTrue(result[0], result)
        self.assertIn('root=1\n', self.path.read_text(encoding='utf-8'))
        self.assertEqual(engine.load_state()['app="player"/x'], '2')

    def test_ini_appends_existing_scope_before_new_sections(self):
        engine = self.fixture(IniConfigEngine, 'root=1\n')
        result = engine.write_batch([('other','DEFAULT','2','int'), ('x','new','3','int'), ('y','another','4','int')])
        self.assertTrue(result[0], result)
        state = engine.load_state()
        self.assertEqual(state['DEFAULT/other'], '2')
        self.assertEqual(state['new/x'], '3')
        self.assertEqual(state['another/y'], '4')

    def test_ini_flags_and_typed_boolean(self):
        engine = self.fixture(IniConfigEngine, '[options]\nColor\nParallelDownloads=5\n')
        self.assertTrue(engine.write_batch([('Color','options',False,'bool'), ('ILoveCandy','options',True,'bool')])[0])
        state = engine.load_state()
        self.assertNotIn('options/Color', state)
        self.assertIs(state['options/ILoveCandy'], True)

    def test_single_file_cache_updates_without_reloading(self):
        for cls, content, key, scope, lookup, value, kind in FIXTURES[:-1]:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                result = engine.write_value(key, scope, value, kind)
                self.assertTrue(result[0], result)
                self.assertEqual(str(engine.cache[lookup]).lower(), value)

    def test_line_formats_reject_multiline_values_without_changes(self):
        for cls, content, key, scope, *_ in FIXTURES[:3]:
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                original = self.path.read_bytes()
                self.assertFalse(engine.write_value(key, scope, 'bad\nnew_key=value')[0])
                self.assertEqual(self.path.read_bytes(), original)

    def test_flat_duplicate_indices_aliases_and_bool_deletion(self):
        engine = self.fixture(FlatDotConfigEngine, 'audio.track one\naudio.track two\nrecord.options.fps 60')
        self.assertEqual(engine.cache['record.options/fps'], '60')
        self.assertTrue(engine.write_value('track:2', 'audio', 'changed')[0])
        self.assertEqual(engine.cache['audio/track'], 'one')
        self.assertEqual(engine.cache['audio/track:2'], 'changed')
        self.assertTrue(engine.write_value('track', 'audio', '__DELETE__', 'bool')[0])
        self.assertEqual(engine.cache['audio/track'], 'changed')
        self.assertTrue(engine.write_value('new', 'DEFAULT', 'value')[0])
        self.assertIn('fps 60\nnew value\n', self.path.read_text(encoding='utf-8'))
        original = self.path.read_bytes()
        self.assertFalse(engine.write_value('track:8', 'audio', 'value')[0])
        self.assertEqual(self.path.read_bytes(), original)

    def test_matugen_round_trip_multiline_comments_and_indentation(self):
        content = '''[config]
x = "''' + "'''" + ''' inside basic string"
  [templates."space name"]
input_path = "a"
# dormant = ''' + "'''" + '''
post_hook = ''' + "'''" + '''
[templates.fake]
# shell comment
''' + "'''" + '''
# inner comment

# description of next block
[templates.next]
input_path = "b"
'''
        engine = self.fixture(MatugenEngine, content)
        self.assertNotIn('fake', engine.cache)
        self.assertTrue(engine.write_value('space name', 'DEFAULT', 'false', 'bool')[0])
        self.assertIn('# description of next block\n[templates.next]', self.path.read_text(encoding='utf-8'))
        self.assertTrue(engine.write_value('space name', 'DEFAULT', 'true', 'bool')[0])
        self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_matugen_decorations_and_nested_array_do_not_become_blocks(self):
        content = '# [templates.first]\n# input_path="a"\n\n# ==========\n# heading\n# ==========\n\n# description\n[templates.second]\nitems = [\n ["templates.fake"],\n]\n'
        engine = self.fixture(MatugenEngine, content)
        self.assertEqual(set(engine.cache), {'first','second','DEFAULT/first','DEFAULT/second'})
        self.assertTrue(engine.write_value('first', 'DEFAULT', True, 'bool')[0])
        self.assertIn('# heading', self.path.read_text(encoding='utf-8'))
        self.assertTrue(engine.write_value('first', 'DEFAULT', False, 'bool')[0])
        self.assertEqual(self.path.read_text(encoding='utf-8'), content)
        self.assertTrue(engine.write_value('second', 'DEFAULT', False, 'bool')[0])
        self.assertTrue(engine.write_value('second', 'DEFAULT', True, 'bool')[0])
        self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_matugen_missing_block_aborts_whole_batch(self):
        engine = self.fixture(MatugenEngine, '[templates.main]\nx="a"\n')
        original = self.path.read_bytes()
        result = engine.write_batch([('main','DEFAULT','false','bool'), ('absent','DEFAULT','false','bool')])
        self.assertFalse(result[0], result)
        self.assertEqual(self.path.read_bytes(), original)

    def test_toml_native_types_and_missing_delete(self):
        data = {'date': date(2026, 10, 10), 'time': time(12, 30), 'stamp': datetime(2026,10,10,tzinfo=timezone.utc), 'array': [{'x':1}], 'nested': {'empty':{}}, 'big': 2**62}
        engine = self.fixture(TomlEngine, TomlEngine._dump_toml(data))
        self.assertTrue(engine.write_value('leaf', 'nonexistent', '__DELETE__')[0])
        self.assertEqual(tomllib.loads(self.path.read_text(encoding='utf-8')), data)
        self.assertTrue(engine.write_value('value', 'nested', 'true', 'string')[0])
        self.assertEqual(tomllib.loads(self.path.read_text(encoding='utf-8'))['nested']['value'], 'true')

    def test_parallel_single_file_writes(self):
        for cls, content in ((IniConfigEngine,''), (FlatDotConfigEngine,''), (TomlEngine,'')):
            with self.subTest(engine=cls.__name__):
                engine = self.fixture(cls, content)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda i: engine.write_value(f'key{i}', 'main', str(i), 'int'), range(20)))
                self.assertTrue(all(result[0] for result in results), results)
                state = engine.load_state()
                for i in range(20):
                    self.assertEqual(str(state[f'main/key{i}']), str(i))

    def test_lua_controls_unicode_and_long_string_replacement(self):
        for old in ('"old"', '[=[old]=]'):
            with self.subTest(old=old):
                engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=' + old + '}})\n')
                value = ''.join(chr(i) for i in range(128)) + 'é值😀'
                result = engine.write_value('x', 'main', value)
                self.assertTrue(result[0], result)
                self.assertEqual(engine.load_state()['main/x'], value)

    def test_lua_all_files_are_synced_before_any_replacement(self):
        child = self.path.parent / 'child.lua'
        child.write_text('hl.config({main={y=1}})\n', encoding='utf-8')
        engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=1}})\ndofile("child.lua")\n')
        originals = (self.path.read_bytes(), child.read_bytes())
        with patch('os.fsync', side_effect=[None, OSError('second staged file sync failed')]):
            result = engine.write_batch([('x','main','2','int'), ('y','main','2','int')])
        self.assertFalse(result[0], result)
        self.assertEqual((self.path.read_bytes(), child.read_bytes()), originals)
        self.assertEqual(set(self.path.parent.iterdir()), {self.path, child})

    def test_lua_partial_batch_aborts(self):
        engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=1}})\n')
        original = self.path.read_bytes()
        result = engine.write_batch([('x','main','2','int'), ('absent','main','2','int')])
        self.assertFalse(result[0], result)
        self.assertEqual(self.path.read_bytes(), original)

    def test_lua_rejects_invalid_numeric_and_invalid_source(self):
        engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=1}})\n')
        original = self.path.read_bytes()
        for value in ('nan', 'inf', '1_000', '1e999', 'not_a_number'):
            self.assertFalse(engine.write_value('x', 'main', value, 'float')[0], value)
            self.assertEqual(self.path.read_bytes(), original)
        self.path.write_text('hl.config({main={x=1}})\ninvalid lua !!!\n', encoding='utf-8')
        self.assertEqual(engine.load_state(), {})
        self.assertFalse(engine.write_value('x','main','2','int')[0])

    def test_lua_relative_include_return_value(self):
        child = self.path.parent / 'child.lua'
        child.write_text('hl.config({main={x=1}})\nreturn "child"\n', encoding='utf-8')
        engine = self.fixture(HyprlandLuaEngine, 'local value=dofile("child.lua")\nhl.config({other={name=value}})\n')
        self.assertEqual(engine.cache['other/name'], 'child')
        self.assertTrue(engine.write_value('x', 'main', '2', 'int')[0])
        self.assertEqual(engine.load_state()['main/x'], 2)

    def test_lua_env_and_shorthand_nil(self):
        engine = self.fixture(HyprlandLuaEngine, 'hl.env("NAME", "old")\n' 'hl.config{main={x=nil}}\n')
        self.assertEqual(engine.cache['main/x'], 'nil')
        result = engine.write_batch([('key','env/1','NEW_NAME','string'), ('value','env/1','new\nvalue','string')])
        self.assertTrue(result[0], result)
        state = engine.load_state()
        self.assertEqual(state['env/1/key'], 'NEW_NAME')
        self.assertEqual(state['env/1/value'], 'new\nvalue')

    def test_lua_record_identifier_ignores_callback_locals(self):
        callbacks = ('function() local name="wrong" end', 'function() if true then local name="wrong" end end', 'function() for i=1,2 do local name="wrong" end end')
        for callback in callbacks:
            with self.subTest(callback=callback):
                content = 'hl.window_rule({callback=' + callback + ',name="right",rounding=1})\n'
                engine = self.fixture(HyprlandLuaEngine, content)
                result = engine.write_value('rounding', 'window_rule/right', '2', 'int')
                self.assertTrue(result[0], result)
                self.assertEqual(engine.load_state()['window_rule/right/rounding'], 2)

    def test_lua_named_record_array_and_escaped_key(self):
        engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={items={{name="first",x=1}},["escaped\\nkey"]=1}})\n')
        self.assertTrue(engine.write_value('x', 'main/items/first', '2', 'int')[0])
        self.assertTrue(engine.write_value('escaped\nkey', 'main', '3', 'int')[0])
        state = engine.load_state()
        self.assertEqual(state['main/items/first/x'], 2)
        self.assertEqual(state['main/escaped\nkey'], 3)

    def test_physical_lines_preserve_unicode_values(self):
        for cls, text, key, scope, lookup in (
            (IniConfigEngine, '[main]\nx=old\n', 'x', 'main', 'main/x'),
            (BridgedIniEngine, '[main]\nx=old\n', 'x', 'main', 'main/x'),
            (FlatDotConfigEngine, 'main.x old\n', 'x', 'main', 'main/x'),
        ):
            for separator in ('\v', '\f', '\x85', '\u2028', '\u2029'):
                with self.subTest(engine=cls.__name__, separator=repr(separator)):
                    engine = self.fixture(cls, text)
                    value = 'before' + separator + 'after'
                    self.assertTrue(engine.write_value(key, scope, value, 'string')[0])
                    self.assertEqual(engine.cache[lookup], value)
                    self.assertEqual(engine.load_state()[lookup], value)

    def test_lua_runtime_identity_skips_inactive_callbacks(self):
        engine = self.fixture(HyprlandLuaEngine,
            'local cb=function() for i=1,2 do hl.env("IGNORE","a") end end\n'
            'hl.env("REAL","old")\n')
        self.assertTrue(engine.write_value('value', 'env/1', 'new', 'string')[0])
        self.assertIn('hl.env("IGNORE","a")', self.path.read_text(encoding="utf-8"))
        self.assertEqual(engine.cache['env/1/value'], 'new')

    def test_lua_runtime_identity_tracks_include_execution_order(self):
        child = self.path.parent / 'child.lua'
        child.write_text('hl.env("CHILD","child")\n', encoding='utf-8')
        engine = self.fixture(HyprlandLuaEngine,
            'hl.env("FIRST","first")\ndofile("child.lua")\nhl.env("LAST","last")\n')
        self.assertTrue(engine.write_batch([('value', 'env/2', 'CHANGED', 'string'),
                                          ('value', 'env/3', 'FINAL', 'string')])[0])
        self.assertIn('"CHANGED"', child.read_text(encoding="utf-8"))
        self.assertEqual(engine.cache['env/3/value'], 'FINAL')
        self.assertEqual(engine.cache['env/1/value'], 'first')

    def test_lua_ambiguous_call_sites_leave_source_unchanged(self):
        for source in ('for i=1,2 do hl.env("NAME","old") end\n',
                       'local cb=function() hl.env("IGNORE","old") end; hl.env("REAL","old")\n',
                       'hl.env("FIRST","old"); hl.env("LAST","old")\n'):
            engine = self.fixture(HyprlandLuaEngine, source)
            self.assertFalse(engine.write_value('value', 'env/1', 'new', 'string')[0])
            self.assertEqual(self.path.read_text(encoding="utf-8"), source)

    def test_lua_numeric_cache_matches_runtime(self):
        for value in ('9223372036854775808', '0xffffffffffffffff', '1.2345678901234567', '0x1.abcdefp+4'):
            engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=1}})\n')
            self.assertTrue(engine.write_value('x', 'main', value, 'float')[0], value)
            observed = engine.cache['main/x']
            self.assertEqual(observed, engine.load_state()['main/x'])
        self.assertEqual(observed, float.fromhex('0x1.abcdefp+4'))

    def test_lua_multiline_and_legacy_data_calls(self):
        engine = self.fixture(HyprlandLuaEngine, 'hl.env(\n"REAL",\n"old"\n)\n')
        self.assertTrue(engine.write_value('value', 'env/1', 'new', 'string')[0])
        engine = self.fixture(HyprlandLuaEngine,
            'local tui_window_data={name="test",rounding=1}\nhl.window_rule(tui_window_data)\n')
        self.assertTrue(engine.write_value('rounding', 'window_rule/test', '2', 'int')[0])
        self.assertEqual(engine.cache['window_rule/test/rounding'], 2)

    def test_matugen_final_prose_keeps_comment_layer(self):
        engine = self.fixture(MatugenEngine,
            '# [templates.main]\n# input_path="input"\n\n# Final explanation\n')
        self.assertTrue(engine.write_value('main', 'DEFAULT', 'true', 'bool')[0])
        self.assertIn('# Final explanation', self.path.read_text(encoding="utf-8"))

    def test_lua_inactive_nil_is_not_a_binding(self):
        engine = self.fixture(HyprlandLuaEngine,
            'local cb=function() hl.config({main={inactive=nil}}) end\n'
            'hl.config({main={active=nil}})\n')
        self.assertNotIn('main/inactive', engine.cache)
        self.assertEqual(engine.cache['main/active'], 'nil')
        engine = self.fixture(HyprlandLuaEngine,
            'local cb=function() hl.config({main={inactive=nil}}) end; '
            'hl.config({main={active=nil}})\n')
        self.assertNotIn('main/inactive', engine.cache)

    def test_ini_empty_scope_and_change_accounting(self):
        engine = self.fixture(IniConfigEngine, 'x=1\n')
        self.assertTrue(engine.write_value('x', '', '2', 'int')[0])
        self.assertEqual(engine.cache['DEFAULT/x'], '2')
        engine.cache['__ui/old'] = 'old'
        result = engine.write_value('x', 'DEFAULT', '3', 'int')
        self.assertIn('1 INI changes', result[1])
        self.assertEqual(engine.cache['__ui/old'], 'old')

    def test_flat_comment_key_is_rejected(self):
        engine = self.fixture(FlatDotConfigEngine, 'x old\n')
        self.assertFalse(engine.write_value('#x', 'DEFAULT', 'new', 'string')[0])
        self.assertEqual(self.path.read_text(encoding="utf-8"), 'x old\n')

    def test_lua_environment_initialization_does_not_corrupt_protocol(self):
        with patch.dict(os.environ, {'LUA_INIT': 'error("unexpected init")',
                                    'LUA_INIT_5_5': 'error("unexpected init")'}):
            engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=1}})\n')
            self.assertEqual(engine.cache['main/x'], 1)
            self.assertTrue(engine.write_value('x', 'main', '2', 'int')[0])
            self.assertEqual(engine.cache['main/x'], 2)

    def test_lua_unrelated_ambiguity_does_not_block_unique_binding(self):
        engine = self.fixture(HyprlandLuaEngine,
            'hl.config({other={x=1}}); hl.config({other={y=2}})\n'
            'hl.env("REAL","old")\n')
        self.assertTrue(engine.write_value('value', 'env/1', 'new', 'string')[0])
        self.assertEqual(engine.cache['env/1/value'], 'new')

    def test_lua_binding_identity_can_be_a_variable(self):
        engine = self.fixture(HyprlandLuaEngine,
            'local key="SUPER"\nhl.bind(key,"Q",{run="old"})\n')
        self.assertTrue(engine.write_value('run', 'bind/SUPER', 'new', 'string')[0])
        self.assertEqual(engine.cache['bind/SUPER/run'], 'new')

    def test_privileged_helper_import_from_unrelated_directory(self):
        import subprocess
        from python.shared.config_io import privileged_atomic_write, current_stamp
        run = subprocess.run

        def without_sudo(command, **kwargs):
            self.assertEqual(command[:2], ['sudo', '-n'])
            self.assertEqual(Path(command[-1]), Path(__file__).resolve().parents[2])
            return run(command[2:], cwd=self.directory.name, **kwargs)

        # Exercise the real child interpreter and its import bootstrap; bypass
        # only authorization so this fixture never requires root privileges.
        with patch('subprocess.run', side_effect=without_sudo):
            result = privileged_atomic_write(self.path, 'new file\n', None)
        self.assertEqual(result, current_stamp(self.path))
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'new file\n')

    def test_new_file_keeps_creator_ownership_in_shared_directory(self):
        from python.shared.config_io import atomic_write
        descriptor, name = tempfile.mkstemp(prefix='dusky-engine-audit-')
        os.close(descriptor)
        path = Path(name)
        self.addCleanup(path.unlink, missing_ok=True)
        path.unlink()
        atomic_write(path, 'new file\n', None)
        self.assertEqual(path.stat().st_uid, os.geteuid())
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(path.read_text(encoding='utf-8'), 'new file\n')

    def test_lua_derived_source_edits_refresh_call_sites(self):
        engine = self.fixture(HyprlandLuaEngine, 'hl.config({main={x=1}})\n')
        self.path.write_text('-- inserted by derived engine\n' + self.path.read_text(encoding='utf-8'), encoding='utf-8')
        engine.file_mtimes[str(self.path)] = self.path.stat().st_mtime
        self.assertTrue(engine.write_value('x', 'main', '2', 'int')[0])
        self.assertEqual(engine.cache['main/x'], 2)

    def test_lua_refresh_preserves_derived_virtual_defaults(self):
        from python.engines.trackpad import TrackpadLuaEngine
        engine = self.fixture(TrackpadLuaEngine, 'hl.config({input={sensitivity=1}})\n')
        self.assertIn('gestures/workspace_swipe_distance', engine.cache)
        self.assertTrue(engine.write_value('sensitivity', 'input', '2', 'int')[0])
        self.assertIn('gestures/workspace_swipe_distance', engine.cache)
        self.assertEqual(engine.cache['input/sensitivity'], 2)

    def test_lua_native_root_modules_and_local_precedence(self):
        config_home = Path(self.directory.name)
        root = config_home / 'hypr'
        directory = root / 'edit_here/source'
        directory.mkdir(parents=True)
        dependency = root / 'source/animations/active/active.lua'
        dependency.parent.mkdir(parents=True)
        dependency.write_text('hl.config({animations={enabled=false}})\n', encoding='utf-8')
        path = directory / 'appearance.lua'
        path.write_text('require("source.animations.active.active")\nhl.config({main={x=1}})\n', encoding='utf-8')
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}):
            engine = HyprlandLuaEngine(str(path))
            self.assertFalse(engine.load_state()['animations/enabled'])
            self.assertTrue(engine.write_value('x', 'main', '2', 'int')[0])
            self.assertEqual(engine.cache['main/x'], 2)
            self.assertEqual(dependency.read_text(encoding='utf-8'), 'hl.config({animations={enabled=false}})\n')
            local = directory / 'source/animations/active/active.lua'
            local.parent.mkdir(parents=True)
            local.write_text('hl.config({animations={enabled=true}})\n', encoding='utf-8')
            self.assertTrue(engine.load_state()['animations/enabled'])


if __name__ == '__main__':
    unittest.main()
