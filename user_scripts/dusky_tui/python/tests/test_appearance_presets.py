"""Appearance presets and fresh overlay deployment, using temporary configs."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'dusky_tui'))
from python.engines.lua import HyprlandLuaEngine


class AppearancePresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = runpy.run_path(str(ROOT / 'hypr/visual/tui_appearance.py'))
        cls.items = {item.uid: item for rows in cls.schema['SCHEMA'].values()
                     for item in rows if item.type_ != 'preset'}

    def test_styles_cover_variants_with_valid_values_and_preserve_hardware(self):
        variants = set()
        signatures = set()
        for preset in self.schema['SCHEMA'][0]:
            payload = preset.preset_payload
            variants.add(payload.get('decoration/blur.variant', 'kawase'))
            self.assertLessEqual(payload.keys(), self.items.keys())
            for uid, value in payload.items():
                setting = self.items[uid]
                if setting.type_ in {'float', 'int'}:
                    self.assertGreaterEqual(value, setting.min_val, (preset.label, uid))
                    self.assertLessEqual(value, setting.max_val, (preset.label, uid))
                elif setting.type_ == 'cycle':
                    self.assertIn(value, setting.options, (preset.label, uid))
            if preset.key == 'pre_smooth_blur':
                self.assertFalse(preset.preset_partial)
                continue
            self.assertTrue(preset.preset_partial)
            self.assertFalse(any(uid.startswith(('render.', 'opengl.', 'debug.', 'misc.', 'general/snap.'))
                                 for uid in payload))
            self.assertNotIn('general.allow_tearing', payload)
            self.assertNotIn('group/groupbar.middle_click_close', payload)
            self.assertNotIn('group/groupbar.scrolling', payload)
            for rule in ('single_window_style', 'maximized_window_style', 'special_magic_style'):
                self.assertEqual(payload[f'window_rule/{rule}.opacity'], 1.0)
                self.assertEqual(payload[f'window_rule/{rule}.no_blur'], not payload['decoration/blur.enabled'])
            signatures.add(tuple(sorted(payload.items())))
        self.assertEqual(variants, set(self.items['decoration/blur.variant'].options))
        self.assertEqual(len(signatures), len(self.schema['SCHEMA'][0]) - 1)

    def test_all_presets_follow_matugen_and_generic_font_selections(self):
        for preset in self.schema['SCHEMA'][0]:
            for uid, item in self.items.items():
                value = preset.preset_payload.get(uid, item.default)
                if item.type_ == 'color' and value != 'nil':
                    self.assertRegex(value, r'^[A-Za-z_][A-Za-z0-9_]*(?:@[0-9a-f]{2})?$', (preset.label, uid))
                    self.assertIn(value.partition('@')[0], self.schema['COLOR_ALIASES'])
                if item.key == 'font_family':
                    self.assertIn(value, ('', 'Sans', 'sans', 'sans-serif', 'serif', 'monospace'))

    def test_fresh_setup_deploys_factory_defaults_and_preserves_existing_edits(self):
        setup_path = ROOT / 'arch_setup_scripts/scripts/005_hypr_custom_config_setup.py'
        templates = ROOT / 'hypr/defaults/edit_here'
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            defaults = home / 'user_scripts/hypr/defaults/edit_here'
            defaults.mkdir(parents=True)
            for name in ('default_apps.lua', 'appearance.lua'):
                shutil.copyfile(templates / name, defaults / name)
            hypr = home / '.config/hypr'
            animation = hypr / 'source/animations/active/active.lua'
            animation.parent.mkdir(parents=True)
            animation.write_text('hl.config({animations={enabled=true}})\n', encoding='utf-8')
            with patch.object(Path, 'home', return_value=home), \
                 patch.dict(os.environ, {'HYPRLAND_INSTANCE_SIGNATURE': '', 'XDG_CONFIG_HOME': str(home / '.config')}), \
                 patch.object(sys, 'argv', [str(setup_path), '--appearance']), \
                 redirect_stdout(io.StringIO()):
                setup = runpy.run_path(str(setup_path))
                self.assertEqual(setup['main'](), 0)
                deployed = hypr / 'edit_here/source/appearance.lua'
                self.assertEqual(deployed.read_bytes(), (templates / 'appearance.lua').read_bytes())
                state = HyprlandLuaEngine(str(deployed)).load_state()
                for uid, item in self.items.items():
                    self.assertEqual(item.deserialize(state[item.scope + '/' + item.key]), item.default, uid)
                modules = list(setup['required_lines']((hypr / 'hyprland.lua').read_text(encoding='utf-8')).values())
                self.assertEqual(modules, [setup['APPS_MODULE'], setup['OVERLAY_MODULE']])
                custom = deployed.read_text(encoding='utf-8').replace('border_size = 2,', 'border_size = 7,', 1)
                deployed.write_text(custom, encoding='utf-8')
                self.assertEqual(setup['main'](), 0)
                self.assertEqual(deployed.read_text(encoding='utf-8'), custom)


if __name__ == '__main__':
    unittest.main()
