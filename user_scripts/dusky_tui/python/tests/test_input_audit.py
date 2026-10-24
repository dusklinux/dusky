"""Input schemas and hybrid Lua transactions; never launch login commands."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'dusky_tui'))
from python.engines.lua import HyprlandLuaEngine
from python.engines.trackpad import TrackpadLuaEngine
from python.engines.autostart_engine import AutostartLuaEngine


class HybridInputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'config.lua'

    def engine(self, cls, content):
        self.path.write_text(content)
        engine = cls(str(self.path))
        engine.load_state()
        return engine

    def test_gesture_action_edit_preserves_custom_fields(self):
        text = '''hl.gesture({ fingers=3, direction="up", mods="SUPER", scale=2,
 disable_inhibit=true, action=function() local nested={action="workspace"} end })\n'''
        engine = self.engine(TrackpadLuaEngine, text)
        result = engine.write_value('action', 'gesture/3/up', 'Move Window', 'picker')
        self.assertTrue(result[0], result)
        changed = self.path.read_text()
        for fragment in ('mods="SUPER"', 'scale=2', 'disable_inhibit=true'):
            self.assertIn(fragment, changed)
        self.assertIn('action="move"', changed)

    def test_dormant_gesture_is_preserved(self):
        dormant = 'local example=function() hl.gesture({fingers=3,direction="up",action="close"}) end\n'
        engine = self.engine(TrackpadLuaEngine, dormant)
        self.assertEqual(engine.cache['gesture/3/up/action'], 'Disabled / Unbound')
        result = engine.write_value('action', 'gesture/3/up', 'Move Window', 'picker')
        self.assertTrue(result[0], result)
        self.assertTrue(self.path.read_text().startswith(dormant))
        self.assertEqual(engine.cache['gesture/3/up/action'], 'Move Window')

    def test_disabling_does_not_assume_a_base_file_is_loaded(self):
        root = self.path.parent
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root)}):
            base = root / 'hypr/source/trackpad.lua'
            base.parent.mkdir(parents=True)
            base.write_text('hl.gesture({fingers=3,direction="horizontal",action="workspace"})\n')
            self.path = root / 'hypr/edit_here/source/trackpad.lua'
            self.path.parent.mkdir(parents=True)
            engine = self.engine(TrackpadLuaEngine, base.read_text())
            result = engine.write_value('action', 'gesture/3/horizontal', 'Disabled / Unbound', 'picker')
            self.assertTrue(result[0], result)
            self.assertNotIn('hl.gesture(', self.path.read_text())
            self.assertEqual(engine.cache['gesture/3/horizontal/action'], 'Disabled / Unbound')

    def test_invalid_hybrid_batch_does_not_commit_standard_changes(self):
        for cls, change in (
            (TrackpadLuaEngine, ('action', 'gesture/3/up', 'unknown action', 'picker')),
            (AutostartLuaEngine, ('unknown_command', 'autostart', 'true', 'bool')),
        ):
            engine = self.engine(cls, 'hl.config({input={sensitivity=0.0}})\n')
            before = self.path.read_bytes()
            result = engine.write_batch([('sensitivity', 'input', '0.5', 'float'), change])
            self.assertFalse(result[0], result)
            self.assertEqual(self.path.read_bytes(), before)

    def test_external_changes_reject_gesture_and_startup_edits(self):
        for cls, change in (
            (TrackpadLuaEngine, ('action', 'gesture/3/up', 'Move Window', 'picker')),
            (AutostartLuaEngine, ('waybar', 'autostart', 'true', 'bool')),
        ):
            engine = self.engine(cls, '-- initial\n')
            self.path.write_text('-- external change\n')
            before = self.path.read_bytes()
            result = engine.write_batch([change])
            self.assertFalse(result[0], result)
            self.assertEqual(self.path.read_bytes(), before)

    def test_startup_and_shutdown_use_the_correct_events(self):
        engine = self.engine(AutostartLuaEngine, '')
        self.assertFalse(engine.cache['autostart/waybar'])
        for key in ('waybar', 'shutdown_target'):
            result = engine.write_value(key, 'autostart', 'true', 'bool')
            self.assertTrue(result[0], result)
            self.assertTrue(engine.cache['autostart/'+key])
        content = self.path.read_text()
        self.assertIn('hyprland.shutdown', content)
        self.assertIn('hyprland.start', content)

    def test_comments_strings_and_dormant_handlers_do_not_report_active(self):
        content = '''-- hl.exec_cmd("waybar")
local example=function() hl.on("hyprland.start",function() hl.exec_cmd("waybar") end) end
hl.on("hyprland.start",function()
 local help = [=[
-- hl.exec_cmd("waybar")
]=]
 --[[ hl.exec_cmd("waybar") ]]
end)
'''
        engine = self.engine(AutostartLuaEngine, content)
        self.assertFalse(engine.cache['autostart/waybar'])
        self.assertTrue(engine.write_value('waybar', 'autostart', 'true', 'bool')[0])
        self.assertTrue(engine.cache['autostart/waybar'])
        self.assertIn(content[:content.index('hl.on("hyprland.start",function()\n')], self.path.read_text())

    def test_unused_nested_startup_function_is_not_an_enabled_command(self):
        content = '''hl.on("hyprland.start",function()
 local unused=function()
  hl.exec_cmd("waybar --config unused")
  -- hl.exec_cmd("awww-daemon")
 end
end)
'''
        engine = self.engine(AutostartLuaEngine, content)
        self.assertFalse(engine.cache['autostart/waybar'])
        self.assertFalse(engine.cache['autostart/awww_daemon'])
        result = engine.write_value('waybar', 'autostart', 'true', 'bool')
        self.assertTrue(result[0], result)
        self.assertIn('hl.exec_cmd("waybar --config unused")', self.path.read_text())
        self.assertTrue(engine.cache['autostart/waybar'])
        result = engine.write_value('waybar', 'autostart', 'false', 'bool')
        self.assertTrue(result[0], result)
        self.assertIn('hl.exec_cmd("waybar --config unused")', self.path.read_text())
        self.assertFalse(engine.cache['autostart/waybar'])

    def test_multiline_clipboard_commands_round_trip(self):
        for key, command in (
            ('cliphist_text', 'wl-paste --type text --watch cliphist store'),
            ('cliphist_image', 'wl-paste --type image --watch cliphist store'),
            ('cliphist_db_text', 'sh -c \\\'. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type text --watch cliphist store\\\''),
        ):
            content = 'hl.on("hyprland.start",function()\n hl.exec_cmd(\n "'+command+'"\n )\nend)\n'
            engine = self.engine(AutostartLuaEngine, content)
            self.assertTrue(engine.cache['autostart/'+key], key)
            self.assertTrue(engine.write_value(key, 'autostart', 'false', 'bool')[0])
            self.assertFalse(engine.cache['autostart/'+key], key)
            self.assertTrue(engine.write_value(key, 'autostart', 'true', 'bool')[0])
            self.assertEqual(self.path.read_text(), content)

    def test_disabling_and_enabling_preserves_multiline_and_duplicate_commands(self):
        content = '''hl.on("hyprland.start",function()
 hl.exec_cmd(
  "waybar --config first"
 )
 hl.exec_cmd("waybar --config second")
end)
'''
        engine = self.engine(AutostartLuaEngine, content)
        self.assertTrue(engine.cache['autostart/waybar'])
        self.assertTrue(engine.write_value('waybar', 'autostart', 'false', 'bool')[0])
        self.assertFalse(engine.cache['autostart/waybar'])
        self.assertTrue(engine.write_value('waybar', 'autostart', 'true', 'bool')[0])
        self.assertTrue(engine.cache['autostart/waybar'])
        self.assertEqual(self.path.read_text(), content)

    def test_successful_mixed_batch_is_fully_reflected_in_cache(self):
        engine = self.engine(AutostartLuaEngine, 'hl.config({input={sensitivity=0.0}})\n')
        result = engine.write_batch([('sensitivity', 'input', '0.5', 'float'), ('waybar', 'autostart', 'true', 'bool')])
        self.assertTrue(result[0], result)
        self.assertEqual(engine.cache['input/sensitivity'], 0.5)
        self.assertTrue(engine.cache['autostart/waybar'])

    def test_factory_input_bindings_and_partial_profiles(self):
        schema = runpy.run_path(str(ROOT / 'hypr/input/tui_input.py'))
        engine = self.engine(HyprlandLuaEngine, (ROOT / 'hypr/defaults/edit_here/input.lua').read_text())
        for rows in schema['SCHEMA'].values():
            for item in rows:
                if item.type_ in {'menu', 'action'}:
                    continue
                if item.type_ == 'preset':
                    self.assertTrue(item.preset_partial)
                    self.assertIs(item.preset_payload['input.left_handed'], False)
                    if item.key == 'preset_raw_gaming':
                        self.assertEqual(item.preset_payload['cursor.no_hardware_cursors'], 0)
                    continue
                self.assertIn(item.scope+'/'+item.key, engine.cache, item.uid)
                self.assertEqual(item.deserialize(engine.cache[item.scope+'/'+item.key]), item.default, item.uid)
        schema = runpy.run_path(str(ROOT / 'hypr/input/tui_trackpad.py'))
        self.assertTrue(all(i.preset_partial for i in schema['SCHEMA'][3]))


    def test_compound_bootstrap_controls_remain_independent(self):
        command = ('systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE PATH '
                   '&& { dbus-update-activation-environment --systemd --all; '
                   'systemctl --user start hyprland-session.target; }')
        content = 'hl.on("hyprland.start",function()\n hl.exec_cmd("'+command+'")\nend)\n'
        keys = ('systemd_env', 'dbus_env', 'session_target')
        for key in keys:
            engine = self.engine(AutostartLuaEngine, content)
            self.assertTrue(all(engine.cache['autostart/'+k] for k in keys))
            result = engine.write_value(key, 'autostart', 'false', 'bool')
            self.assertTrue(result[0], result)
            for other in keys:
                self.assertEqual(engine.cache['autostart/'+other], other != key)
            result = engine.write_value(key, 'autostart', 'true', 'bool')
            self.assertTrue(result[0], result)
            self.assertEqual(self.path.read_text(), content)
        engine = self.engine(AutostartLuaEngine, content)
        self.assertTrue(engine.write_batch([(k, 'autostart', 'false', 'bool') for k in keys])[0])
        self.assertTrue(all(not engine.cache['autostart/'+k] for k in keys))
        self.assertTrue(engine.write_batch([(k, 'autostart', 'true', 'bool') for k in keys])[0])
        self.assertEqual(self.path.read_text(), content)

    def test_autostart_retains_existing_defaults_and_controls(self):
        from python.engines.autostart_engine import AUTOSTART_DEFAULTS
        schema = runpy.run_path(str(ROOT / 'hypr/misc/tui_autostart.py'))
        self.assertEqual(schema['TABS'], ['System', 'User', 'Dusky', 'Profiles'])
        items = [i for rows in schema['SCHEMA'].values() for i in rows]
        for name in ('awww_daemon', 'waybar'):
            self.assertIs(AUTOSTART_DEFAULTS['autostart/'+name]['default'], True)
            self.assertIs(next(i for i in items if i.key == name).default, True)
        for name in ('systemd_env', 'dbus_env', 'session_target', 'xhost_root', 'glance_gpu_power',
                     'glance_disk_read', 'cliphist_db_text', 'cliphist_db_image', 'clip_persist'):
            self.assertIn('autostart/'+name, AUTOSTART_DEFAULTS)
            self.assertTrue(any(i.key == name for i in items), name)
        self.assertEqual(schema['SCHEMA'][3][1].preset_payload,
                         {'xwayland.enabled': True, 'autostart.waybar': True, 'autostart.awww_daemon': True})


if __name__ == '__main__':
    unittest.main()
