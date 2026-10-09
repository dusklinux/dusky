"""Focused fontconfig regressions using temporary configuration files.

Run with Python 3.15+: python3 -m unittest discover -s python/tests -p test_fontconfig.py
Fontconfig commands use the installed tools; toolkit writes are isolated/mocked.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ENGINE_PATH = Path(os.environ.get("DUSKY_FONT_TEST_ENGINE", ROOT / "python/engines/fontconfig.py"))
spec = importlib.util.spec_from_file_location("fontconfig_under_test", ENGINE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FontconfigEngine = module.FontconfigEngine


class FontconfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dusky-font-test-")
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home),
                                     "XDG_CACHE_HOME": str(self.home / "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.target = self.home / "fontconfig/conf.d/99-dusky-fonts.conf"
        self.engine = FontconfigEngine(str(self.target))
        sync = patch.object(self.engine, "_sync_system_fonts", return_value=True)
        sync.start()
        self.addCleanup(sync.stop)
        refresh = patch.object(self.engine, "refresh_cache")
        self.refresh = refresh.start()
        self.addCleanup(refresh.stop)

    def write_xml(self, xml):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(xml, encoding="utf-8")

    def apply(self, key="antialias", value=True, kind="bool"):
        result = self.engine.write_value(key, "DEFAULT", value, kind)
        self.assertTrue(result[0], result)
        return ET.parse(self.target).getroot()

    def test_custom_include_keeps_precedence(self):
        self.write_xml('<fontconfig><match target="font"><edit name="antialias" mode="assign"><bool>false</bool></edit></match><include ignore_missing="yes">missing.conf</include></fontconfig>')
        root = self.apply()
        self.assertEqual([node.tag for node in root], ["match", "include"])

    def test_custom_generic_alias_survives(self):
        self.write_xml('<fontconfig><alias><family>sans-serif</family><accept><family>Liberation Sans</family></accept><default><family>serif</family></default></alias></fontconfig>')
        root = self.apply()
        self.assertEqual(root.findtext("alias/accept/family"), "Liberation Sans")
        self.assertEqual(root.findtext("alias/default/family"), "serif")

    def test_directory_order_and_salt_survive(self):
        self.write_xml(f'<fontconfig><dir salt="first">{self.home / "a"}</dir><reset-dirs/><dir salt="last">{self.home / "b"}</dir></fontconfig>')
        root = self.apply()
        self.assertLess([node.tag for node in root].index("dir"), [node.tag for node in root].index("reset-dirs"))
        self.assertEqual([node.get("salt") for node in root.findall("dir")], ["first", "last"])

    def test_repeated_directory_keeps_each_position_and_salt(self):
        self.write_xml(f'<fontconfig><dir salt="first">{self.home / "a"}</dir><reset-dirs/><dir salt="last">{self.home / "a"}</dir></fontconfig>')
        root = self.apply()
        self.assertEqual([node.get("salt") for node in root.findall("dir")], ["first", "last"])
        self.assertEqual([node.tag for node in root][1:], ["dir", "reset-dirs", "dir"])

    def test_relative_directory_resolves_against_config(self):
        self.write_xml('<fontconfig><dir prefix="relative">archive</dir></fontconfig>')
        self.assertEqual(self.engine.load_state()["font_dir"], str(self.target.parent / "archive"))

    def test_xdg_directory_resolves_against_data_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "data")}):
            self.write_xml('<fontconfig><dir prefix="xdg">fonts</dir></fontconfig>')
            self.assertEqual(self.engine.load_state()["font_dir"], str(self.home / "data/fonts"))

    def test_xdg_directory_write_does_not_apply_prefix_twice(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "data")}):
            self.write_xml('<fontconfig><dir prefix="xdg" salt="archive">fonts</dir></fontconfig>')
            root = self.apply()
        node = root.find("dir")
        self.assertEqual(node.text, str(self.home / "data/fonts"))
        self.assertNotIn("prefix", node.attrib)
        self.assertEqual(node.get("salt"), "archive")

    def test_invalid_boolean_does_not_overwrite(self):
        self.write_xml('<fontconfig/>')
        self.assertFalse(self.engine.write_value("antialias", "DEFAULT", "invalid", "bool")[0])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '<fontconfig/>')

    def test_root_attributes_preserved(self):
        self.write_xml('<fontconfig since="2.18.0"/>')
        # Metadata round trip only: fontconfig 2.18 does not support since yet.
        with patch.object(self.engine, "_validate_families"):
            self.assertEqual(self.apply().get("since"), "2.18.0")

    def test_malformed_xml_does_not_overwrite(self):
        self.write_xml('<fontconfig>')
        self.assertFalse(self.engine.write_value("antialias", "DEFAULT", True, "bool")[0])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '<fontconfig>')

    def test_invalid_rendering_does_not_overwrite(self):
        self.write_xml('<fontconfig/>')
        self.assertFalse(self.engine.write_value("hintstyle", "DEFAULT", "bogus", "picker")[0])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '<fontconfig/>')

    def test_missing_family_does_not_overwrite(self):
        self.write_xml('<fontconfig/>')
        result = self.engine.write_value("sans-serif", "DEFAULT", "Dusky Missing Fixture Family", "picker")
        self.assertFalse(result[0])
        self.assertIn("Missing font families", result[1])
        self.assertEqual(self.target.read_text(encoding="utf-8"), '<fontconfig/>')

    def test_rendering_precedence_and_idempotence(self):
        self.write_xml('<fontconfig><match target="font"><edit name="antialias" mode="assign"><bool>false</bool></edit></match><match target="font"><test name="family"><string>Liberation Sans</string></test><edit name="hinting" mode="assign"><bool>false</bool></edit></match><match target="font"><edit name="antialias" mode="assign"><bool>false</bool></edit></match></fontconfig>')
        root = self.apply()
        self.assertEqual([node.findtext("edit/bool") for node in root.findall("match")], ["false", "false", "true"])
        first = self.target.read_bytes()
        self.apply()
        self.assertEqual(self.target.read_bytes(), first)

    def test_bitmap_toggle_retains_color_fonts(self):
        root = self.apply("embeddedbitmap", False)
        self.assertEqual([(node.find("test").get("compare"), node.findtext("edit/bool")) for node in root.findall("match")], [("not_eq", "false"), ("eq", "true")])
        root = self.apply("embeddedbitmap", True)
        self.assertFalse(root.findall("match/test"))
        self.assertTrue(self.engine.load_state()["embeddedbitmap"])

    def test_qt_absent_mono_preserves_fixed(self):
        path = self.home / "qt6ct/qt6ct.conf"
        path.parent.mkdir()
        fixed = 'Existing Mono,13,-1,5,700,0,0,0,1,0'
        path.write_text(f'[Fonts]\nfixed="{fixed}"\n[Other]\ncustom=yes\n', encoding="utf-8")
        self.assertTrue(self.engine._patch_qt_conf(path, "qt6", "Liberation Sans", "", True))
        self.assertEqual(self.engine._qt_slots(path)["fixed"], fixed)
        self.assertIn('custom=yes', path.read_text(encoding="utf-8"))

    def test_qt_absent_mono_does_not_create_empty_fixed(self):
        path = self.home / "qt6ct/qt6ct.conf"
        self.assertTrue(self.engine._patch_qt_conf(path, "qt6", "Liberation Sans", "", True))
        self.assertNotIn("fixed", self.engine._qt_slots(path))

    def test_qt_serializations_keep_attributes(self):
        path = self.home / "qt6ct/qt6ct.conf"
        self.engine._qt_write(path, {"general": "Old,14,-1,5,700,1,0,0,0,0", "fixed": "Mono,16,-1,5,400,0,0,0,1,0"})
        self.engine._patch_qt_conf(path, "qt6", 'New "Family"', "Liberation Mono", True)
        self.assertEqual(self.engine._qt_slots(path)["general"], 'New "Family",14,-1,5,700,1,0,0,0,0')
        self.assertTrue(self.engine._qt_slots(path)["fixed"].startswith('Liberation Mono,16,'))

    def test_gtk_preserves_sizes_and_other_sections(self):
        path = self.home / "gtk-4.0/settings.ini"
        path.parent.mkdir()
        path.write_text('[Settings]\ngtk-font-name=Old Family 13.5\ngtk-monospace-font-name=Old Mono 12\ngtk-theme-name=Theme\n[Other]\nvalue=1\n', encoding="utf-8")
        self.assertEqual(self.engine._existing_gtk_size(path=path), "13.5")
        self.engine._patch_gtk_ini(path, {"gtk-font-name": "Liberation Sans 13.5"}, {"gtk-monospace-font-name"})
        content = path.read_text(encoding="utf-8")
        self.assertIn('gtk-theme-name=Theme', content)
        self.assertIn('[Other]\nvalue=1', content)
        self.assertNotIn('gtk-monospace-font-name', content)

    def test_sync_uses_cache_without_reloading(self):
        self.engine.cache = {"sans-serif": ["Liberation Sans", "FreeSans"], "monospace": ["Liberation Mono"]}
        with patch.object(self.engine, "load_state", side_effect=AssertionError("unnecessary reload")), patch.object(self.engine, "_sync_gtk_toolkits", return_value=True), patch.object(self.engine, "_patch_qt_conf", return_value=True):
            self.assertTrue(FontconfigEngine._sync_system_fonts(self.engine, True))

    def test_cache_retry_preserves_force(self):
        self.refresh.side_effect = [subprocess.TimeoutExpired("fc-cache", 120), None]
        self.assertFalse(self.engine.write_batch([("antialias", "DEFAULT", True, "bool")], force_cache=True)[0])
        self.apply("hinting", True)
        self.assertEqual(self.refresh.call_args_list[1].kwargs, {"force": True})

    def test_atomic_write_preserves_existing_permissions(self):
        self.write_xml("original")
        self.target.chmod(0o600)
        self.engine._atomic_write(self.target, "replacement")
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_atomic_write_preserves_config_symlink(self):
        referent = self.home / "shared/settings.ini"
        referent.parent.mkdir()
        referent.write_text("original", encoding="utf-8")
        link = self.home / "settings.ini"
        link.symlink_to(referent)
        self.engine._atomic_write(link, "replacement")
        self.assertTrue(link.is_symlink())
        self.assertEqual(referent.read_text(encoding="utf-8"), "replacement")

    def test_atomic_failure_preserves_target_and_cleans_temp(self):
        self.write_xml('original')
        with patch.object(Path, "replace", side_effect=OSError("fixture rename failure")):
            with self.assertRaises(OSError):
                self.engine._atomic_write(self.target, 'replacement')
        self.assertEqual(self.target.read_text(encoding="utf-8"), 'original')
        self.assertFalse(list(self.target.parent.glob('*.tmp-*')))

    def load_schema(self):
        spec = importlib.util.spec_from_file_location("font_schema_under_test", ROOT.parent / "fonts/tui_fonts.py")
        schema = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema)
        return schema

    def test_schema_scans_all_configured_directories(self):
        for name in ("first", "second"):
            (self.target.parent / name).mkdir(parents=True, exist_ok=True)
        self.write_xml('<fontconfig><dir prefix="relative">first</dir><dir prefix="relative">second</dir></fontconfig>')
        def discover(args, **kwargs):
            output = "" if args[0] == "fc-list" else f"{Path(args[-1]).name}\t100\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        with patch("subprocess.run", side_effect=discover) as run:
            schema = self.load_schema()
        self.assertEqual(schema._INSTALLED["mono"], ["first", "second"])
        self.assertEqual(sum(call.args[0][0] == "fc-scan" for call in run.call_args_list), 2)

    def test_schema_empty_archive_and_missing_defaults(self):
        archive = self.home / "archive"
        archive.mkdir()
        self.write_xml(f'<fontconfig><dir>{archive}</dir></fontconfig>')
        def discover(args, **kwargs):
            return subprocess.CompletedProcess(args, 1 if args[0] == "fc-scan" else 0, "", "")
        with patch("subprocess.run", side_effect=discover):
            schema = self.load_schema()
        self.assertEqual(schema._MONO_OPTIONS, ["JetBrainsMono Nerd Font Mono"])
        self.assertTrue(schema._MONO_HINTS[0].startswith("Missing:"))

    def test_schema_reports_fontconfig_errors(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "fixture configuration error")):
            with self.assertRaisesRegex(RuntimeError, "fixture configuration error"):
                self.load_schema()

    def test_schema_classification_excludes_icon_fonts(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            schema = self.load_schema()
        buckets = schema._classify_families({"Symbols Nerd Font Mono": "100", "Example Propo": "", "Example Mono": "100", "Noto Color Emoji": "", "Example Serif": ""})
        self.assertEqual(buckets, {"sans": ["Example Propo"], "serif": ["Example Serif"], "mono": ["Example Mono"], "emoji": ["Noto Color Emoji"]})

    def test_cross_process_writes_do_not_lose_settings(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
from python.engines.fontconfig import FontconfigEngine
engine = FontconfigEngine(sys.argv[2])
engine._sync_system_fonts = lambda quiet: True
result = engine.write_value(sys.argv[3], 'DEFAULT', False, 'bool')
assert result[0], result
"""
        processes = [subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(self.target), key],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
                     for key in ("antialias", "hinting", "autohint", "embeddedbitmap")]
        for process in processes:
            output, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, output + error)
        state = self.engine.load_state()
        for key in ("antialias", "hinting", "autohint", "embeddedbitmap"):
            self.assertIs(state[key], False)


if __name__ == "__main__":
    unittest.main()
