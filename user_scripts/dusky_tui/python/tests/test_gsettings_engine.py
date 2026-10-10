"""Comprehensive stress and regression test suite for GSettingsEngine and GSettings TUI."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from python.frontend.core_types import ConfigItem
from python.engines.gsettings import GSettingsEngine, SettingWriteResult


class GSettingsEngineTests(unittest.TestCase):
    def setUp(self):
        self.test_items = [
            ConfigItem(
                label="Theme",
                key="gtk-theme",
                scope="org.gnome.desktop.interface",
                type_="string",
                default="Adwaita",
            ),
            ConfigItem(
                label="Animations",
                key="enable-animations",
                scope="org.gnome.desktop.interface",
                type_="bool",
                default=True,
            ),
            ConfigItem(
                label="Cursor Size",
                key="cursor-size",
                scope="org.gnome.desktop.interface",
                type_="int",
                default=24,
            ),
            ConfigItem(
                label="Scaling",
                key="text-scaling-factor",
                scope="org.gnome.desktop.interface",
                type_="float",
                default=1.0,
            ),
        ]
        self.engine = GSettingsEngine(items=self.test_items)

    def test_target_path_defaults_to_dconf_user(self):
        expected = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dconf/user"
        self.assertEqual(Path(self.engine.target_path), expected)

    def test_installed_schemas_discovery(self):
        schemas = self.engine.get_installed_schemas()
        self.assertIsInstance(schemas, set)
        result = subprocess.run(["gsettings", "list-schemas"], check=True, capture_output=True, text=True)
        self.assertEqual(schemas, set(result.stdout.splitlines()))

    def test_load_state_populates_dual_mappings(self):
        state = self.engine.load_state()
        self.assertIsInstance(state, dict)
        if "org.gnome.desktop.interface" in self.engine.get_installed_schemas():
            self.assertIn("org.gnome.desktop.interface/gtk-theme", state)
            self.assertIn("org.gnome.desktop.interface.gtk-theme", state)
            self.assertEqual(
                state["org.gnome.desktop.interface/gtk-theme"],
                state["org.gnome.desktop.interface.gtk-theme"],
            )

    def test_cli_fallback_load_state(self):
        fallback_engine = GSettingsEngine(items=self.test_items)
        fallback_engine._gio_available = False

        def fake_run(cmd, *args, **kwargs):
            if cmd == ["gsettings", "list-schemas"]:
                return subprocess.CompletedProcess([], 0, "org.gnome.desktop.interface\n", "")
            if len(cmd) >= 4 and cmd[0] == "gsettings" and cmd[1] == "get":
                key = cmd[3]
                values = {
                    "gtk-theme": "'dusky-dark'\n",
                    "enable-animations": "true\n",
                    "cursor-size": "24\n",
                    "text-scaling-factor": "1.0\n",
                }
                return subprocess.CompletedProcess([], 0, values.get(key, "''\n"), "")
            return subprocess.CompletedProcess([], 1, "", "error")

        with patch("subprocess.run", side_effect=fake_run):
            state = fallback_engine.load_state()
            self.assertEqual(state.get("org.gnome.desktop.interface/gtk-theme"), "dusky-dark")
            self.assertEqual(state.get("org.gnome.desktop.interface/enable-animations"), "true")
            self.assertEqual(state.get("org.gnome.desktop.interface/cursor-size"), "24")

    def test_itemless_engine_loads_state(self):
        engine = GSettingsEngine()
        state = engine.load_state()
        if "org.gnome.desktop.interface" in engine.get_installed_schemas():
            self.assertIn("org.gnome.desktop.interface/gtk-theme", state)
            self.assertIn("org.gnome.desktop.interface.gtk-theme", state)

    def test_write_rejects_non_positive_cursor_size(self):
        ok, msg, debug = self.engine.write_value("cursor-size", "org.gnome.desktop.interface", "-10", "int")
        self.assertFalse(ok)
        self.assertIn("positive", debug.lower())

    def test_write_batch_rejects_missing_scope(self):
        changes = [("test-key", "DEFAULT", "val", "string")]
        ok, msg, debug = self.engine.write_batch(changes)
        self.assertFalse(ok)
        self.assertIn("Missing GSettings schema scope", debug)

    def test_write_batch_rejects_uninstalled_schema(self):
        changes = [("test-key", "com.nonexistent.schema.xyz", "val", "string")]
        ok, msg, debug = self.engine.write_batch(changes)
        self.assertFalse(ok)
        self.assertIn("is not installed", debug)

    def test_cursor_sync_creates_index_theme(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_home = Path(tmp_dir)
            with patch("pathlib.Path.home", return_value=fake_home), patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}):
                success = self.engine.sync_cursor_wayland("TestTheme", 32)
                self.assertTrue(success)
                index_theme = fake_home / ".icons" / "default" / "index.theme"
                self.assertTrue(index_theme.is_file())
                content = index_theme.read_text(encoding="utf-8")
                self.assertIn("[Icon Theme]", content)
                self.assertIn("Inherits=TestTheme", content)

    def test_cursor_sync_rejects_invalid_size(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("pathlib.Path.home", return_value=Path(directory)),
            patch("subprocess.run") as run,
        ):
            self.assertFalse(self.engine.sync_cursor_wayland("TestTheme", -5))
            run.assert_not_called()
            self.assertIn("positive", self.engine.last_sync_error)

    def test_tui_interactive_mounting_and_navigation(self):
        import asyncio
        from python.frontend.ui import DuskyTUI
        import user_scripts.gtk.tui_gsettings as schema_mod

        self.engine = GSettingsEngine(items=[item for items in schema_mod.SCHEMA.values() for item in items])

        async def run_pilot():
            app = DuskyTUI(
                engine_pool={("gsettings", schema_mod.TARGET_FILE): self.engine},
                default_engine_key=("gsettings", schema_mod.TARGET_FILE),
                schema=schema_mod.SCHEMA,
                tabs=schema_mod.TABS,
                title=schema_mod.APP_TITLE,
                tab_notices=schema_mod.TAB_NOTICES,
                user_presets_tab=schema_mod.USER_PRESETS_TAB,
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertEqual(app.user_presets_tab_idx, len(schema_mod.TABS) - 1)
                self.assertFalse(app.query_one("#file-link").display)
                self.assertEqual(app.query_one("#file-link").path, "")
                for _ in schema_mod.TABS:
                    await pilot.press("tab")
                    await pilot.pause()
                await pilot.resize_terminal(60, 20)
                await pilot.pause()
                await pilot.resize_terminal(140, 45)
                await pilot.pause()
                await pilot.press("down")
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                await pilot.press("q")

        asyncio.run(run_pilot())


class GSettingsRegressionTests(unittest.TestCase):
    def test_catalog_matches_installed_metadata(self):
        import user_scripts.gtk.tui_gsettings as catalog
        from gi.repository import Gio, GLib
        identities = set()
        for index, items in catalog.SCHEMA.items():
            self.assertTrue(items, catalog.TABS[index])
            for item in items:
                if item.type_ in {"action", "preset"}:
                    continue
                self.assertNotIn(item.uid, identities)
                identities.add(item.uid)
                schema = catalog.SOURCE.lookup(item.scope, True)
                metadata = schema.get_key(item.key)
                settings = Gio.Settings.new_full(schema, None, None)
                default_variant = settings.get_default_value(item.key)
                self.assertEqual(
                    item.default,
                    default_variant.print_(True) if default_variant.get_type_string() == "as" else default_variant.unpack(),
                )
                if item.type_ == "cycle":
                    self.assertEqual(item.options, list(metadata.get_range().unpack()[1]))
                signature = metadata.get_value_type().dup_string()
                variant = GLib.Variant.parse(metadata.get_value_type(), item.default, None, None) if signature == "as" else GLib.Variant(signature, item.default)
                self.assertTrue(metadata.range_check(variant))
        self.assertGreaterEqual(len(identities), 80)
        self.assertIn("org.gtk.gtk4.Settings.FileChooser.view-type", identities)
        self.assertIn("org.gnome.desktop.interface.font-name", identities)
        self.assertIn("org.gnome.desktop.interface.accent-color", identities)

    def test_xdg_theme_discovery_separates_icons_and_cursors(self):
        import user_scripts.gtk.tui_gsettings as catalog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Mixed", "PointerOnly"):
                theme = root / "icons" / name
                (theme / "cursors").mkdir(parents=True)
                (theme / "index.theme").write_text("[Icon Theme]\nName=" + name + "\n" + ("Directories=scalable/apps\n" if name == "Mixed" else ""))
            hidden = root / "icons/HiddenIcons"
            hidden.mkdir()
            (hidden / "index.theme").write_text(
                "[Icon Theme]\nName=HiddenIcons\nDirectories=scalable/apps\nHidden=true\n"
            )
            gtk = root / "themes/CustomGTK/gtk-3.0"
            gtk.mkdir(parents=True)
            (gtk / "gtk.css").write_text("/* fixture */")
            with (
                patch.dict(os.environ, {"XDG_DATA_HOME": directory, "XDG_DATA_DIRS": str(root / "empty")}),
                patch("pathlib.Path.home", return_value=root),
            ):
                self.assertEqual(catalog._discover_themes("gtk"), ["CustomGTK"])
                self.assertEqual(catalog._discover_themes("icon"), ["Mixed"])
                self.assertEqual(catalog._discover_themes("cursor"), ["Mixed", "PointerOnly"])

    def test_optional_schema_combinations(self):
        import importlib.util
        import user_scripts.gtk.tui_gsettings as catalog
        from gi.repository import Gio
        for available, expected in [({"org.gtk.gtk4.Settings.FileChooser"}, ["Dialogs", "Presets"]), (set(), ["Presets"])]:
            source = unittest.mock.Mock()
            source.list_schemas.return_value = (list(available), [])
            source.lookup.side_effect = catalog.SOURCE.lookup
            spec = importlib.util.spec_from_file_location("isolated_catalog", catalog.__file__)
            module = importlib.util.module_from_spec(spec)
            with patch.object(Gio.SettingsSchemaSource, "get_default", return_value=source):
                spec.loader.exec_module(module)
            self.assertEqual(module.TABS, expected)
            for items in module.SCHEMA.values():
                for item in items:
                    if item.type_ not in {"preset", "action"}:
                        self.assertEqual(item.scope, "org.gtk.gtk4.Settings.FileChooser")

    def test_literal_tui_strings(self):
        from user_scripts.gtk.tui_gsettings import GSettingsItem
        item = GSettingsItem(label="Literal", key="text", type_="string", default="")
        for text in ['"quoted"', "__VAR__literal", "  spaced  "]:
            self.assertEqual(item.deserialize(text), text)

    def test_presets_validate_using_the_same_schema_rules_as_writes(self):
        import user_scripts.gtk.tui_gsettings as catalog
        from gi.repository import GLib
        items = {item.key: item for rows in catalog.SCHEMA.values() for item in rows}
        for key, value in [
            ("enable-animations", "garbage"), ("text-scaling-factor", "nan"),
            ("text-scaling-factor", 99), ("color-scheme", "invalid-enum"),
            ("ignore-hosts", "not an array"), ("cursor-size", 0),
            ("cursor-theme", ""), ("font-name", "before\0after"),
            ("font-name", None),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises((ValueError, TypeError, GLib.Error)):
                items[key].validate_preset_value(value)
        self.assertTrue(items["enable-animations"].validate_preset_value("YES"))
        self.assertEqual(items["text-scaling-factor"].validate_preset_value("2e0"), 2.0)
        self.assertEqual(items["ignore-hosts"].validate_preset_value(["localhost"]), "['localhost']")

    def test_isolated_gio_stress(self):
        self._run_fixture("gio")

    def test_isolated_cli_and_dconf_persistence(self):
        self._run_fixture("cli")

    def test_isolated_gio_commit_and_reset_failures(self):
        self._run_fixture("gio-failure")

    def _run_fixture(self, mode):
        with tempfile.TemporaryDirectory(prefix="dusky-gsettings-test-") as directory:
            env = {k: v for k, v in os.environ.items() if k not in {
                "HYPRLAND_INSTANCE_SIGNATURE", "DBUS_SESSION_BUS_ADDRESS", "DCONF_PROFILE", "GSETTINGS_BACKEND", "GSETTINGS_SCHEMA_DIR"}}
            (Path(directory) / "run").mkdir(mode=0o700)
            env.update(HOME=directory, GIO_USE_VFS="local", XDG_RUNTIME_DIR=directory + "/run", XDG_CONFIG_HOME=directory + "/config",
                       XDG_CACHE_HOME=directory + "/cache", XDG_DATA_HOME=directory + "/data",
                       GSETTINGS_BACKEND="memory" if mode == "gio" else "dconf")
            cmd = [sys.executable, "-X", "dev", str(Path(__file__).resolve()), "--fixture", mode]
            if mode == "cli":
                cmd = ["dbus-run-session", "--", *cmd]
            elif mode == "gio-failure":
                env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + directory + "/absent-bus"
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=150)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("CRITICAL", result.stderr)
            self.assertNotIn("ResourceWarning", result.stderr)
            print(result.stdout.strip())

    def test_partial_batch_reports_each_write_once(self):
        engine = GSettingsEngine()
        engine._gio_available = False  # The write primitive is mocked below.
        with (
            patch.object(engine, "get_installed_schemas", return_value={"test.schema"}),
            patch.object(
                engine,
                "_write_single_setting",
                side_effect=[SettingWriteResult(True, "", "1"), SettingWriteResult(False, "Rejected")],
            ) as write,
        ):
            results = engine.write_batch_results([("good", "test.schema", "1", "int"), ("bad", "test.schema", "2", "int")])
        self.assertTrue(results[("good", "test.schema")].ok)
        self.assertFalse(results[("bad", "test.schema")].ok)
        self.assertEqual(write.call_count, 2)

    def test_cli_numeric_decoding_matches_gio(self):
        from gi.repository import GLib
        for signature, value in [
            ("y", 18), ("n", -32768), ("q", 65535), ("u", 4294967295),
            ("x", -9223372036854775808), ("t", 18446744073709551615),
            ("h", 2147483647), ("d", 0.1), ("d", 1.1), ("d", 1e-9),
        ]:
            with self.subTest(signature=signature, value=value):
                raw = GLib.Variant(signature, value).print_(True)
                self.assertEqual(GSettingsEngine._decode_cli(raw), str(value))
        self.assertEqual(GSettingsEngine._decode_cli("['one', 'two']"), "['one', 'two']")
        self.assertEqual(GSettingsEngine._decode_cli("'0.10000000000000001'"), "0.10000000000000001")

    def test_cli_timeouts_are_errors(self):
        engine = GSettingsEngine()
        engine._gio_available = False
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gsettings", 5)):
            self.assertFalse(engine._write_single_setting("test", "key", "x", "string").ok)
            self.assertFalse(engine.reset_key("test", "key")[0])

    def test_empty_registered_catalog_and_batch_do_no_io(self):
        engine = GSettingsEngine(items=[])
        with patch.object(engine, "get_installed_schemas", side_effect=AssertionError("unneeded discovery")):
            self.assertEqual(engine.load_state(), {})
            self.assertEqual(engine.write_batch_results([]), {})
            self.assertTrue(engine.write_batch([])[0])

    def test_invalid_cursor_themes_reject_before_writing(self):
        engine = GSettingsEngine()
        for theme in ("", "Theme\nInjected", "Theme\rInjected"):
            result = engine.write_value("cursor-theme", "org.gnome.desktop.interface", theme)
            self.assertFalse(result[0])
            self.assertIn("Cursor theme", result[1])

    def test_cursor_sync_rejects_noninteger_sizes(self):
        engine = GSettingsEngine()
        with patch("subprocess.run") as run:
            for size in (True, 32.5, "32", 2**31, 0, -1):
                self.assertFalse(engine.sync_cursor_wayland("Theme", size))
            run.assert_not_called()

    def test_cursor_sync_handles_absent_schema(self):
        from gi.repository import Gio
        engine = GSettingsEngine()
        with (
            patch.object(Gio.SettingsSchemaSource, "get_default", return_value=None),
            patch.object(Gio.Settings, "new_full") as create,
        ):
            self.assertFalse(engine.sync_cursor_wayland())
            self.assertIn("Unknown fixed schema", engine.last_sync_error)
            create.assert_not_called()

    def test_cli_one_read_failure_does_not_hide_other_keys(self):
        engine = GSettingsEngine(items=[
            ConfigItem(label=name, key=name, scope="fixture", type_="string", default="")
            for name in ("a", "b", "c")
        ])
        engine._gio_available = False
        with (
            patch.object(engine, "get_installed_schemas", return_value={"fixture"}),
            patch.object(engine, "_cli", side_effect=[
            subprocess.CompletedProcess([], 0, "'first'", ""),
            subprocess.TimeoutExpired("gsettings", 5),
            subprocess.CompletedProcess([], 0, "'last'", ""),
        ]),
        ):
            state = engine.load_state()
        self.assertEqual(state["fixture/a"], "first")
        self.assertNotIn("fixture/b", state)
        self.assertEqual(state["fixture/c"], "last")

    def test_cli_commit_warning_is_failure_even_with_zero_exit(self):
        engine = GSettingsEngine()
        engine._gio_available = False
        warning = "dconf-WARNING: failed to commit changes to dconf: connection failed"
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", warning)):
            self.assertFalse(engine._write_single_setting("fixture", "key", "text", "string").ok)
            self.assertFalse(engine.reset_key("fixture", "key")[0])

    def test_cursor_failure_and_atomic_cleanup(self):
        engine = GSettingsEngine()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("pathlib.Path.home", return_value=Path(directory)),
            patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "fixture"}),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "compositor rejected", "")),
        ):
            self.assertFalse(engine.sync_cursor_wayland("Theme", 32))
            self.assertIn("compositor rejected", engine.last_sync_error)
            self.assertEqual((Path(directory) / ".icons/default/index.theme").read_text(), "[Icon Theme]\nInherits=Theme\n")
            self.assertEqual(len(list((Path(directory) / ".icons/default").iterdir())), 1)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("pathlib.Path.home", return_value=Path(directory)),
            patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}),
            patch("pathlib.Path.replace", side_effect=OSError("replace failed")),
        ):
            self.assertFalse(engine.sync_cursor_wayland("Theme", 32))
            self.assertFalse(list((Path(directory) / ".icons/default").iterdir()))

    def test_hyprctl_zero_exit_with_error_response_is_failure(self):
        engine = GSettingsEngine()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("pathlib.Path.home", return_value=Path(directory)),
            patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "fixture"}),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "error: rejected", "")),
        ):
            self.assertFalse(engine.sync_cursor_wayland("Theme", 32))
            self.assertIn("error: rejected", engine.last_sync_error)

    def test_sync_reads_both_live_values(self):
        engine = GSettingsEngine()
        engine._gio_available = False
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("pathlib.Path.home", return_value=Path(directory)),
            patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "fixture"}),
            patch(
                "subprocess.run",
                side_effect=[subprocess.CompletedProcess([], 0, "'LiveTheme'", ""), subprocess.CompletedProcess([], 0, "48", ""), subprocess.CompletedProcess([], 0, "ok", "")],
            ) as run,
        ):
            self.assertTrue(engine.sync_cursor_wayland())
            self.assertEqual(run.call_args.args[0], ["hyprctl", "setcursor", "LiveTheme", "48"])


def integration_worker(mode):
    """Real variants and real CLI writes, always in a disposable process/home."""
    from xml.sax.saxutils import escape
    directory = Path.home() / "schemas"
    directory.mkdir()
    specs = {"text": ("s", "'default'"), "flag": ("b", "false"),
             "signed": ("i", "0"), "unsigned": ("u", "0"),
             "wide": ("x", "0"), "unsigned-wide": ("t", "0"),
             "real": ("d", "1.0"), "array": ("as", "[]"),
             "byte": ("y", "0"), "short": ("n", "0"),
             "unsigned-short": ("q", "0"), "handle": ("h", "0")}
    xml = '<schemalist><schema id="org.dusky.fixture" path="/org/dusky/fixture/">'
    for key, (signature, default) in specs.items():
        xml += f'<key name="{key}" type="{signature}"><default>{escape(default)}</default></key>'
    xml += '<key name="bounded" type="i"><default>2</default><range min="1" max="5"/></key></schema></schemalist>'
    (directory / "fixture.gschema.xml").write_text(xml, encoding="utf-8")
    subprocess.run(["glib-compile-schemas", "--strict", str(directory)], check=True)
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(directory)
    engine = GSettingsEngine(schemas=["org.dusky.fixture"])
    engine._gio_available = mode != "cli"
    if mode != "cli":
        from gi.repository import Gio, GLib
        settings = Gio.Settings.new("org.dusky.fixture")
    if mode == "gio-failure":
        # Seed a persistent override using a separate, functioning private bus.
        good_env = {key: value for key, value in os.environ.items() if key != "DBUS_SESSION_BUS_ADDRESS"}
        seeded = subprocess.run([
            "dbus-run-session", "--", "gsettings", "set",
            "org.dusky.fixture", "text", "'persisted'",
        ], env=good_env, capture_output=True, text=True, timeout=15)
        assert seeded.returncode == 0, seeded.stderr
        results = engine.write_batch_results([("text", "org.dusky.fixture", "lost write", "string")])
        result = results[("text", "org.dusky.fixture")]
        assert not result.ok and result.actual == "persisted", result
        assert engine.cache["org.dusky.fixture/text"] == "persisted"
        # Matching a schema default is insufficient if the explicit write failed.
        result = engine.write_value("flag", "org.dusky.fixture", "false", "bool")
        assert not result[0], result
        assert settings.get_user_value("flag") is None
        assert not engine.reset_key("org.dusky.fixture", "text")[0]
        assert settings.get_user_value("text").unpack() == "persisted"
        print("Gio commit and reset failures correctly reported")
        return
    cases = [("text", "  O'Reilly \\ \"quoted\" café\nnext\t  ", "string"),
             ("flag", "true", "bool"), ("signed", "-2147483648", "int"),
             ("unsigned", "4294967295", "int"), ("wide", "9223372036854775807", "int"),
             ("unsigned-wide", "18446744073709551615", "int"), ("real", "2.5", "float"),
             ("byte", "255", "int"), ("short", "-32768", "int"),
             ("unsigned-short", "65535", "int"), ("handle", "2147483647", "int")]
    import random
    generator = random.Random(17)
    alphabet = "abc ' \\\"\n\r\t\b\v café Ω 中 😀"
    corpus = ["", " ", "'", '\"', "__VAR__literal", "\\", *(
        "".join(generator.choices(alphabet, k=generator.randrange(1, 60)))
        for _ in range(50)
    )]
    for value in corpus:
        result = engine.write_value("text", "org.dusky.fixture", value, "string")
        assert result[0], (repr(value), result)
        assert engine.cache["org.dusky.fixture/text"] == value
        assert engine.load_state()["org.dusky.fixture/text"] == value
    for key, value, kind, canonical in [
        ("flag", "YES", "bool", "true"), ("flag", "off", "bool", "false"),
        ("signed", " +0042 ", "int", "42"), ("real", "2e0", "float", "2.0"),
        ("real", "0.1", "float", "0.1"), ("real", "1e-9", "float", "1e-09"),
    ]:
        assert engine.write_value(key, "org.dusky.fixture", value, kind)[0]
        assert engine.cache["org.dusky.fixture/" + key] == canonical
        assert engine.load_state()["org.dusky.fixture/" + key] == canonical
    for key, value in [("text", "before\0after"), ("text\0suffix", "bad")]:
        result = engine.write_value(key, "org.dusky.fixture", value, "string")
        assert not result[0] and "NUL" in result[1], result
    assert not engine.reset_key("org.dusky.fixture", "text\0suffix")[0]
    if mode == "gio":
        fd_before = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(100 if mode == "gio" else 8):
        results = engine.write_batch_results([(key, "org.dusky.fixture", value, kind) for key, value, kind in cases])
        assert all(result.ok for result in results.values()), results
        state = engine.load_state()
        for key, value, kind in cases:
            assert state[f"org.dusky.fixture/{key}"] == value, (key, state)
    for key, value, kind in [("flag", "garbage", "bool"), ("real", "nan", "float"), ("real", "inf", "float"), ("unsigned", "-1", "int"), ("signed", "2147483648", "int"), ("bounded", "6", "int"), ("missing", "1", "int"),
                             ("byte", "256", "int"), ("short", "-32769", "int"),
                             ("unsigned-short", "65536", "int"), ("handle", "2147483648", "int")]:
        assert not engine.write_value(key, "org.dusky.fixture", value, kind)[0], (key, value)
    assert engine.reset_key("org.dusky.fixture", "text")[0]
    assert engine.load_state()["org.dusky.fixture/text"] == "default"
    assert not engine.reset_key("org.dusky.fixture", "missing")[0]
    tracked = GSettingsEngine(items=[ConfigItem(
        label="Text", key="text", scope="org.dusky.fixture", type_="string", default="default",
    )])
    tracked._gio_available = mode == "gio"
    assert tracked.write_value("flag", "org.dusky.fixture", "true", "bool")[0]
    assert tracked.reset_key("org.dusky.fixture", "flag")[0]
    assert tracked.cache["org.dusky.fixture/flag"] == "false"
    assert tracked.load_state()["org.dusky.fixture/flag"] == "false"

    if mode == "gio":
        assert engine.write_value("array", "org.dusky.fixture", "['one', 'two']", "string")[0]
        assert settings.get_strv("array") == ["one", "two"]
        assert engine.write_value("signed", "org.dusky.fixture", "42", "string")[0]  # schema controls type
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(pool.map(lambda i: engine.write_value("signed", "org.dusky.fixture", str(i), "int")[0], range(200)))
        fd_after = len(list(Path("/proc/self/fd").iterdir()))
        assert fd_after <= fd_before, (fd_before, fd_after)
        print("file descriptors before/after stress", fd_before, fd_after)
        import user_scripts.gtk.tui_gsettings as catalog
        managed = [item for items in catalog.SCHEMA.values() for item in items if item.type_ not in {"preset", "action"}]
        catalog_engine = GSettingsEngine(items=managed)
        exercised = 0
        for item in managed:
            if item.read_only:
                continue
            candidates = item.options if item.type_ == "cycle" else [item.default]
            if item.type_ == "bool":
                candidates = [True, False]
            for value in candidates:
                result = catalog_engine.write_value(item.key, item.scope, item.serialize(value), item.type_)
                assert result[0], (item.uid, value, result)
                exercised += 1
        print("catalog writes", exercised)
        import asyncio
        from python.frontend.ui import DuskyTUI
        async def pilot_save():
            app = DuskyTUI(engine_pool={("gsettings", catalog.TARGET_FILE): catalog_engine},
                           default_engine_key=("gsettings", catalog.TARGET_FILE),
                           schema=catalog.SCHEMA, tabs=catalog.TABS, title=catalog.APP_TITLE,
                           tab_notices=catalog.TAB_NOTICES, user_presets_tab=catalog.USER_PRESETS_TAB,
                           hide_missing_items=True)
            async with app.run_test(size=(100, 35)) as pilot:
                await pilot.pause()
                item = next(item for item in catalog.SCHEMA[catalog.TABS.index("GTK")] if item.key == "enable-animations")
                tab = catalog.TABS.index("GTK")
                index = catalog.SCHEMA[tab].index(item)
                value = not item.value
                app._safe_apply_value(tab, index, item, value)
                await pilot.pause(0.5)
                assert catalog_engine.load_state()[item.scope + "/" + item.key] == item.serialize(value)
                assert not app.pending_commits, app.pending_commits
                # Canceling an input or picker must not write a new value.
                await pilot.press("enter", "escape")
                await pilot.pause()
                await pilot.press("f1", "escape")
                await pilot.pause()
                # Reject the entire preset before changing a valid preceding row.
                from unittest.mock import Mock
                schema_items = {row.key: row for rows in catalog.SCHEMA.values() for row in rows}
                untouched = {row.uid: row.serialize(row.value) for row in managed}
                bad_preset = ConfigItem(
                    label="Malformed fixture", key="invalid_fixture", type_="preset", default=None,
                    preset_payload={
                        row.uid: row.value for row in managed
                    } | {
                        schema_items["color-scheme"].uid: "prefer-dark",
                        schema_items["enable-animations"].uid: "garbage",
                    },
                )
                with patch.object(app, "notify_status", Mock()) as notify:
                    app.apply_preset(bad_preset)
                    await pilot.pause()
                    assert notify.call_args.kwargs["level"] == "error"
                    assert "Cannot apply preset value" in notify.call_args.args[0]
                assert {row.uid: row.serialize(row.value) for row in managed} == untouched
                assert not app.pending_commits
                cursor = next(row for row in catalog.SCHEMA[0] if row.key == "cursor-size")
                cursor_index = catalog.SCHEMA[0].index(cursor)
                value = cursor.value + 1
                with (
                    patch.object(catalog_engine, "sync_cursor_wayland", return_value=False),
                    patch.object(catalog_engine, "last_sync_error", "fixture hook failure"),
                ):
                    app._safe_apply_value(0, cursor_index, cursor, value)
                    await pilot.pause(0.5)
                    assert catalog_engine.cache[cursor.scope + "/" + cursor.key] == str(value)
                    assert cursor.value == value
                    assert not app.pending_commits, app.pending_commits
                    assert app._save_failure_pending
                await pilot.press("q")
        asyncio.run(pilot_save())
        with patch.object(Gio.Settings, "new_full", return_value=unittest.mock.Mock(is_writable=lambda key: False)):
            assert not engine.write_value("text", "org.dusky.fixture", "locked")[0]
            assert not engine.reset_key("org.dusky.fixture", "text")[0]
    else:
        # A separate CLI process proves persistence, beyond in-process cache.
        result = subprocess.run(["gsettings", "get", "org.dusky.fixture", "unsigned"], check=True, capture_output=True, text=True)
        assert engine._decode_cli(result.stdout) == "4294967295"
        assert Path(engine.target_path).is_file()
        # Repeated Gio reads must observe another process without a GLib loop.
        observer = GSettingsEngine(schemas=["org.dusky.fixture"])
        assert observer.load_state()["org.dusky.fixture/text"] == "default"
        for value in ("external-one", "external-two"):
            subprocess.run([
                "gsettings", "set", "org.dusky.fixture", "text", "'" + value + "'",
            ], check=True, capture_output=True)
            assert observer.load_state()["org.dusky.fixture/text"] == value
        assert engine.reset_key("org.dusky.fixture", "text")[0]
        assert observer.load_state()["org.dusky.fixture/text"] == "default"
        launcher = Path(__file__).resolve().parents[3] / "gtk/tui_gsettings.py"
        def launch(*args, expected=0):
            result = subprocess.run([sys.executable, str(launcher), *args], capture_output=True, text=True, timeout=30)
            assert result.returncode == expected, (args, result.stdout, result.stderr)
            return result
        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(Path.home() / "absent-bus")}):
            result = engine.write_value("text", "org.dusky.fixture", "cannot persist", "string")
            assert not result[0] and "failed to commit changes" in result[1], result
            assert not engine.reset_key("org.dusky.fixture", "unsigned")[0]
        assert engine.load_state()["org.dusky.fixture/text"] == "default"
        assert engine.load_state()["org.dusky.fixture/unsigned"] == "4294967295"
        launch("--default")
        launch("--set", "enable-animations=garbage", expected=1)
        launch("--set", "text-scaling-factor=nan", expected=1)
        launch("--set", "text-scaling-factor=99", expected=1)
        launch("--set", "cursor-size=-1", expected=1)
        launch("--set", "font-name=  O'Reilly café  ")
        exported = launch("--export-state")
        import json
        assert json.loads(exported.stdout)["org.gnome.desktop.interface/font-name"] == "  O'Reilly café  "
        launch("--reset-key", "font-name")
        launch("--backup", expected=1)
        launch("--restore", expected=1)
        launch("--sync-cursor")
        assert (Path.home() / ".icons/default/index.theme").is_file()
    print(mode, "stress and regression checks passed")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture":
        integration_worker(sys.argv[2])
    else:
        unittest.main()
