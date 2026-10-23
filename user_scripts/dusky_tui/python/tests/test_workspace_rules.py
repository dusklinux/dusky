"""Workspace inheritance, native value types, and shared Lua mutation regressions."""
from pathlib import Path
import runpy
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'dusky_tui'))
from python.engines.lua import HyprlandLuaEngine


class WorkspaceRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = runpy.run_path(str(ROOT / 'hypr/workspace/tui_workspace_rules.py'))
        cls.items = [item for rows in cls.schema['SCHEMA'].values() for item in rows
                     if item.type_ not in {'menu', 'preset', 'action'}]

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'workspace_rules.lua'

    def engine(self, content):
        self.path.write_text(content)
        engine = HyprlandLuaEngine(str(self.path))
        engine.load_state()
        return engine

    def test_factory_defaults_have_editable_bindings_and_round_trip(self):
        content = (ROOT / 'hypr/defaults/edit_here/workspace_rules.lua').read_text()
        engine = self.engine(content)
        changes = []
        for item in self.items:
            lookup = f'{item.scope}/{item.key}'
            self.assertIn(lookup, engine.cache, item.uid)
            self.assertEqual(item.deserialize(engine.cache[lookup]), item.default, item.uid)
            changes.append((item.key, item.scope, item.serialize(item.default), item.type_))
        self.assertEqual(len(changes), len({(row[0], row[1]) for row in changes}))
        result = engine.write_batch(changes)
        self.assertTrue(result[0], result)
        for item in self.items:
            self.assertEqual(item.deserialize(engine.cache[f'{item.scope}/{item.key}']), item.default, item.uid)

    def test_optional_rules_round_trip_native_types_and_inheritance(self):
        engine = self.engine('hl.workspace_rule({ workspace="1", layout=nil, border_size=nil, gaps_in=nil, no_border=nil, layout_opts={orientation=nil} })\n')
        for key, scope, kind, values in (
            ('layout', 'workspace_rule/1', 'cycle', ('master', 'nil', 'scrolling')),
            ('orientation', 'workspace_rule/1/layout_opts', 'cycle', ('top', 'nil', 'center')),
            ('no_border', 'workspace_rule/1', 'cycle', ('true', 'false', 'nil', 'false')),
            ('border_size', 'workspace_rule/1', 'string', ('8', 'nil', '0')),
            ('gaps_in', 'workspace_rule/1', 'string', ('12', '{top = 4, left = 8, right = 8, bottom = 4}', '0', '{}', 'nil')),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    result = engine.write_value(key, scope, value, kind)
                    self.assertTrue(result[0], result)
                    self.assertEqual(engine.cache[f'{scope}/{key}'], value)
        content = self.path.read_text()
        self.assertIn('border_size=0', content)
        self.assertIn('no_border=false', content)
        self.assertNotIn('"false"', content)

    def test_invalid_gap_and_border_values_do_not_modify_file(self):
        engine = self.engine('hl.workspace_rule({workspace="1", gaps_in=nil, border_size=nil})\n')
        original = self.path.read_bytes()
        for key, value in (('gaps_in', 'not a gap'), ('gaps_in', '{top="4"}'),
                           ('gaps_in', '{wrong=4}'), ('gaps_in', '3.5'), ('border_size', '{}')):
            result = engine.write_value(key, 'workspace_rule/1', value, 'string')
            self.assertFalse(result[0], result)
            self.assertEqual(self.path.read_bytes(), original)

    def test_nil_scan_ignores_dormant_and_ambiguous_calls(self):
        engine = self.engine('''local dormant = function()
    hl.workspace_rule({workspace="99", layout=nil})
end
for i=1,2 do
    hl.workspace_rule({workspace=tostring(i), layout=nil})
end
hl.workspace_rule({workspace="3", layout=nil})
''')
        self.assertEqual(engine.cache['workspace_rule/3/layout'], 'nil')
        self.assertNotIn('workspace_rule/99/layout', engine.cache)
        self.assertNotIn('workspace_rule/1/layout', engine.cache)
        before = self.path.read_text()
        self.assertTrue(engine.write_value('layout', 'workspace_rule/3', 'master', 'cycle')[0])
        self.assertEqual(self.path.read_text(), before.replace('workspace="3", layout=nil', 'workspace="3", layout="master"'))

    def test_profiles_preserve_unrelated_workspace_settings(self):
        for preset in self.schema['SCHEMA'][5]:
            if preset.type_ != 'preset':
                continue
            self.assertTrue(preset.preset_partial, preset.label)
            content = (ROOT / 'hypr/defaults/edit_here/workspace_rules.lua').read_text()
            engine = self.engine(content.replace('monitor = ""', 'monitor = "desc:custom display"', 1))
            before = {key: value for key, value in engine.cache.items() if key.startswith('workspace_rule/')}
            items = {item.uid: item for item in self.items}
            changes = [(items[uid].key, items[uid].scope, items[uid].serialize(value), items[uid].type_)
                       for uid, value in preset.preset_payload.items()]
            self.assertTrue(engine.write_batch(changes)[0])
            self.assertEqual({key: value for key, value in engine.cache.items() if key.startswith('workspace_rule/')}, before)


if __name__ == '__main__':
    unittest.main()
