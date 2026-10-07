"""Configuration round trips, failed commits, and Lua gesture boundaries."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.json_engine import JsonEngine
from python.engines.toml import TomlEngine
from python.engines.trackpad import find_gesture_blocks, TrackpadLuaEngine
from python.engines.dusky_sites import DuskySitesEngine


class StructuredEngineTests(unittest.TestCase):
    def test_slash_alias_preserves_literal_dots_in_ancestor_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, text in ((JsonEngine, '{"a.b":{"c":{"x":1}}}'),
                              (TomlEngine, '["a.b".c]\nx=1\n')):
                with self.subTest(engine=cls.__name__):
                    path = Path(directory) / cls.__name__
                    path.write_text(text, encoding="utf-8")
                    state = cls(str(path)).load_state()
                    self.assertEqual(state["a.b/c/x"], 1)
                    self.assertNotIn("a/b/c/x", state)

    def test_deep_scope_aliases_and_scalar_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, text in ((JsonEngine, '{"a":{"b":{"x":1}}}'),
                              (TomlEngine, "[a.b]\nx=1\n")):
                with self.subTest(engine=cls.__name__):
                    path = Path(directory) / cls.__name__
                    path.write_text(text, encoding="utf-8")
                    engine = cls(str(path))
                    state = engine.load_state()
                    self.assertEqual(state["a/b/x"], 1)
                    self.assertEqual(state["a.b.x"], 1)
                    original = path.read_bytes()
                    result = engine.write_value("child", "a/b/x", "2", "int")
                    self.assertFalse(result[0], result)
                    self.assertEqual(path.read_bytes(), original)

    def test_parallel_distinct_writes_preserve_every_value(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls in (JsonEngine, TomlEngine):
                with self.subTest(engine=cls.__name__):
                    path = Path(directory) / cls.__name__
                    engine = cls(str(path))
                    def write(i):
                        return engine.write_value(f"key{i}", "a/b", str(2**60 + i), "int")
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        results = list(pool.map(write, range(64)))
                    self.assertTrue(all(result[0] for result in results), results)
                    state = engine.load_state()
                    for i in range(64):
                        self.assertEqual(state[f"a/b/key{i}"], 2**60 + i)

    def test_failed_replace_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for cls, text in ((JsonEngine, '{"x":1}'), (TomlEngine, "x=1\n")):
                with self.subTest(engine=cls.__name__):
                    path = Path(directory) / cls.__name__
                    path.write_text(text, encoding="utf-8")
                    engine = cls(str(path))
                    original = path.read_bytes()
                    with patch("os.replace", side_effect=OSError("fixture failure")):
                        result = engine.write_value("x", "DEFAULT", "2", "int")
                    self.assertFalse(result[0], result)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(list(Path(directory).glob("tmp*")))

    def test_toml_strings_round_trip_all_controls_and_unicode(self):
        generator = random.Random(315)
        alphabet = ''.join(chr(i) for i in range(128)) + "é值😀"
        for _ in range(500):
            key = ''.join(generator.choices(alphabet, k=12))
            value = ''.join(generator.choices(alphabet, k=100))
            data = {key: value, "nested": {"empty": {}, "items": [value, {key: value}]}}
            self.assertEqual(tomllib.loads(TomlEngine._dump_toml(data)), data)

    def test_invalid_utf8_toml_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_bytes(b'x="\xff"\n')
            engine = TomlEngine(str(path))
            self.assertEqual(engine.load_state(), {})
            self.assertFalse(engine.write_value("x", "DEFAULT", "2", "int")[0])
            self.assertEqual(path.read_bytes(), b'x="\xff"\n')

    def test_sites_refuses_malformed_or_nonobject_json_before_actions(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory}):
            path = Path(directory) / "sites.json"
            for content in ("{broken", "[]", "null"):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    engine = DuskySitesEngine(str(path))
                    self.assertIsInstance(engine.load_state(), dict)
                    result = engine.write_value("action_add_site", "DEFAULT", "example.org", "action")
                    self.assertFalse(result[0], result)
                    self.assertEqual(path.read_text(encoding="utf-8"), content)
                    self.assertFalse((engine.sites_dir / "example.org.css").exists())


class GestureTests(unittest.TestCase):
    def test_callback_locals_do_not_override_table_metadata(self):
        for callback in (
            'function() local fingers=4; local direction="up" end',
            'function() if true then local fingers=4 end; local direction="up" end',
            'function() for i=1,2 do local fingers=4 end; repeat local direction="up" until true end',
            'function() local inner=function() local fingers=4 end; local direction="up" end',
        ):
            for fields in (f'fingers=3,direction="left",action={callback}',
                           f'action={callback},fingers=3,direction="left"'):
                block = f'hl.gesture({{{fields}}})'
                with self.subTest(block=block):
                    self.assertEqual(find_gesture_blocks(block)[0][2:], (block, "3", "left"))

    def test_metadata_ignores_comments_strings_and_nested_tables(self):
        block = '''hl.gesture({
 -- fingers=4, direction="up"
 fingers=3, direction="left",
 nested={fingers=5, direction="right"},
 action="fingers=6, direction='down'",
} -- closing comment
)'''
        blocks = find_gesture_blocks(block)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][2:], (block, "3", "left"))

    def test_comments_strings_and_long_brackets_do_not_create_gestures(self):
        gesture = 'hl.gesture({fingers=3,direction="left",action="workspace"})'
        for wrapper in ("-- {}\n", "--[=[ {} ]=]\n", "[==[ {} ]==]", "'{}'", '"{}"'):
            text = wrapper.format(gesture.replace('"', '\\"') if wrapper == '"{}"' else gesture)
            self.assertEqual(find_gesture_blocks(text), [], text)

    def test_escaped_quotes_comments_nested_tables_and_long_strings(self):
        block = '''hl.gesture({
 fingers = 3, direction = 'left',
 action = function()
   local ending = "backslash\\\\"
   local quoted = "escaped\\\" }"
   local text = [==[ } ) { ]==]
   -- } )
   --[=[ } ) ]=]
   local nested = { x = {} }
 end,
})'''
        content = "-- prefix\n" + block + "\n-- suffix\n"
        blocks = find_gesture_blocks(content)
        self.assertEqual(len(blocks), 1)
        start, end, actual, fingers, direction = blocks[0]
        self.assertEqual((actual, fingers, direction), (block, "3", "left"))
        self.assertEqual(content[start:end], block)

    def test_many_gestures_retain_exact_offsets(self):
        block = 'hl.gesture({fingers=3,direction="left",action="workspace"})\n'
        content = block * 5000
        blocks = find_gesture_blocks(content)
        self.assertEqual(len(blocks), 5000)
        for start, end, actual, _, _ in blocks:
            self.assertEqual(content[start:end], actual)

    def test_native_lua_gesture_enable_disable_preserves_commented_example(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.lua"
            comment = '-- hl.gesture({fingers=3,direction="left",action="workspace"})\n'
            path.write_text(comment, encoding="utf-8")
            engine = TrackpadLuaEngine(str(path))
            self.assertEqual(engine.load_state()["gesture/3/left/action"], "Disabled / Unbound")
            for value in ("Native Workspace Swipe", "Move Window", "Disabled / Unbound"):
                result = engine.write_value("action", "gesture/3/left", value, "string")
                self.assertTrue(result[0], result)
                self.assertEqual(engine.load_state()["gesture/3/left/action"], value)
                self.assertTrue(path.read_text(encoding="utf-8").startswith(comment))
