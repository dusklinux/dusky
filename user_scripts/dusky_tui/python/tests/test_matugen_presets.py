"""Preset organization and engine regressions; desktop application is mocked."""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

TUI_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = TUI_ROOT.parent
sys.path.insert(0, str(TUI_ROOT))

from python.engines.matugen_presets import MatugenPresetsEngine


def load_schema():
    spec = importlib.util.spec_from_file_location(
        "color_presets_schema", SCRIPTS_ROOT / "theme_matugen/tui_color_presets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PresetEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = MatugenPresetsEngine(Path(self.tmp.name) / "state.conf")
        self.engine.load_state()

    def test_loading_is_read_only_and_uses_own_state(self):
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.engine.state_file.write_text(
            'LAST_THEME_SOURCE=vivid\nLAST_VIVID_PRESET=test_pack\nLAST_APPLIED_HEX=#FF0055\n',
            encoding="utf-8",
        )
        cache = self.engine.load_state()
        self.assertEqual(cache["vivid_active_id"], "test_pack")
        self.assertEqual(cache["exact/active_color"], "#FF0055")

    def test_false_triggers_never_apply_or_delete(self):
        self.engine.favorites = [("Red", "#FF0000")]
        keys = ["color_vib_hyper_red__FF0000", "exact_preset_vivid_red",
                "fav_apply_0", "fav_remove_0", "action_del_exact_vivid_red",
                "vivid_apply_test", "action_resume_wallpaper", "action_add_favorite"]
        with patch.object(self.engine, "apply_color") as apply, \
             patch.object(self.engine, "delete_exact_preset") as delete, \
             patch.object(self.engine, "apply_vivid") as vivid:
            result = self.engine.write_batch([(k, "DEFAULT", "false", "bool") for k in keys])
        self.assertTrue(result[0])
        apply.assert_not_called()
        delete.assert_not_called()
        vivid.assert_not_called()
        self.assertEqual(self.engine.favorites, [("Red", "#FF0000")])

    def test_seed_and_recipe_dispatch(self):
        with patch.object(self.engine, "apply_color", return_value=(True, "applied")) as apply:
            self.assertTrue(self.engine.write_value("color_vib_hyper_red__FF0000", "DEFAULT", "true", "bool")[0])
            apply.assert_called_once_with("#FF0000")
            apply.reset_mock()
            self.assertTrue(self.engine.write_value("exact_preset_vivid_red", "DEFAULT", "true", "bool")[0])
            apply.assert_called_once_with("#FF0000", scheme_override="scheme-fidelity", contrast_override="0", mode_override="dark")

    def test_favorites_round_trip_and_duplicate(self):
        self.assertTrue(self.engine.add_favorite("ff0055", "Rose")[0])
        self.assertFalse(self.engine.add_favorite("#FF0055")[0])
        self.engine.load_state()
        self.assertEqual(self.engine.favorites, [("Rose", "#FF0055")])
        self.assertTrue(self.engine.remove_favorite("ff0055")[0])
        self.engine.load_state()
        self.assertEqual(self.engine.favorites, [])

    def test_favorite_write_failure_does_not_change_memory_or_report_success(self):
        with patch.object(self.engine, "_save_favorites", return_value=False):
            self.assertFalse(self.engine.add_favorite("FF0055")[0])
            self.assertEqual(self.engine.favorites, [])
            self.engine.favorites = [("Rose", "#FF0055")]
            self.assertFalse(self.engine.remove_favorite("Rose")[0])
            self.assertEqual(self.engine.favorites, [("Rose", "#FF0055")])
            self.engine.cache["last_applied_hex"] = "#FF0055"
            self.assertFalse(self.engine.write_value("action_remove_favorite", "DEFAULT", "true", "bool")[0])

    def test_favorite_labels_round_trip(self):
        for label in ("bad|label", "bad\nlabel", "#comment", "   "):
            with self.subTest(label=label):
                self.assertFalse(self.engine.add_favorite("FF0055", label)[0])

    def test_hsl_percentages_and_range(self):
        for text, expected in [("0 1% 50%", "#817E7E"), ("0 100% 1%", "#050000"),
                               ("hsl(120, 100%, 40%)", "#00CC00"), ("240 100 50", "#0000FF")]:
            with self.subTest(text=text):
                self.assertEqual(self.engine._parse_hsl_to_hex(text), expected)
        for text in ("361 50 50", "120 101% 50%", "120 50 -1"):
            self.assertIsNone(self.engine._parse_hsl_to_hex(text))

    def test_recipe_validation_and_empty_list(self):
        valid = {"id": "test", "label": "Test", "hex": "ff0055"}
        self.engine.exact_file.write_text(json.dumps([valid, None, {}, valid, dict(valid, id="bad", scheme=[])]), encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            self.engine.load_state()
        self.assertEqual(len(self.engine.exact_presets), 1)
        self.assertEqual(self.engine.exact_presets[0]["hex"], "#FF0055")
        self.engine.exact_file.write_text("[]", encoding="utf-8")
        self.engine.load_state()
        self.assertEqual(self.engine.exact_presets, [])

    def test_recipe_mutations_and_write_failure(self):
        self.assertFalse(self.engine.add_exact_preset("FF0055", scheme="invalid")[0])
        self.assertFalse(self.engine.add_exact_preset("FF0055", contrast=True)[0])
        before = list(self.engine.exact_presets)
        with patch.object(self.engine, "_save_exact_presets", return_value=False):
            self.assertFalse(self.engine.add_exact_preset("FF0055")[0])
            self.assertEqual(self.engine.exact_presets, before)
            self.assertFalse(self.engine.delete_exact_preset("vivid_red"))
            self.assertEqual(self.engine.exact_presets, before)
        ok, preset_id = self.engine.add_exact_preset("FF0055")
        self.assertTrue(ok)
        self.engine.load_state()
        self.assertTrue(any(p["id"] == preset_id for p in self.engine.exact_presets))
        self.assertTrue(self.engine.delete_exact_preset(preset_id))

    def test_state_preserves_external_keys_and_custom_source(self):
        self.engine.state_file.write_text('# Keep\nLIGHT_WAL="/a path/image.png"\nLAST_THEME_SOURCE=vivid\nLAST_VIVID_PRESET=test\n', encoding="utf-8")
        self.engine.cache.update(theme_source="custom", vivid_preset="none")
        self.assertTrue(self.engine._save_state_file())
        text = self.engine.state_file.read_text(encoding="utf-8")
        self.assertIn('# Keep\nLIGHT_WAL="/a path/image.png"\n', text)
        cache = self.engine.load_state()
        self.assertEqual(cache["theme_source"], "custom")
        self.assertEqual(cache["vivid_preset"], "none")

    def test_apply_updates_vivid_status_and_passes_recipe_settings(self):
        self.engine.cache.update(vivid_active_id="old_pack", vivid_active_hex="#00FF00")
        with patch("python.engines.matugen_presets.subprocess.run") as run:
            run.return_value.returncode = 0
            ok, _ = self.engine.apply_color("FF0055", "scheme-vibrant", "0.2", "light")
        self.assertTrue(ok)
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[1:4], ["set", "--no-wall", "--no-regen"])
        for flag, expected in (("--type", "scheme-vibrant"), ("--mode", "light"), ("--contrast", "0.2")):
            self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertEqual(run.call_args_list[1].args[0][1:], ["color", "#FF0055"])
        self.assertEqual(self.engine.cache["vivid_active_id"], "none")
        self.assertEqual(self.engine.cache["vivid_active_hex"], "#FF0055")
        self.assertEqual(self.engine.load_state()["theme_source"], "custom")

    def test_apply_reports_state_failure(self):
        with patch("python.engines.matugen_presets.subprocess.run") as run, \
             patch.object(self.engine, "_save_state_file", return_value=False):
            run.return_value.returncode = 0
            ok, message = self.engine.apply_color("FF0055")
        self.assertFalse(ok)
        self.assertIn("saving theme state failed", message)

    def test_installed_matugen_previews(self):
        previews = {}
        for mode in ("dark", "light"):
            for contrast in ("0", "0.2", "-0.2"):
                with self.subTest(mode=mode, contrast=contrast):
                    preview = self.engine.get_palette_preview("FF0000", "scheme-fidelity", mode, contrast)
                    self.assertEqual(len(preview), 15)
                    previews[mode, contrast] = preview
        self.assertNotEqual(previews["dark", "0"], previews["light", "0"])
        self.assertNotEqual(previews["dark", "0"], previews["dark", "0.2"])
        self.assertEqual(self.engine.get_palette_preview("bad"), {})


class VividFavoriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = MatugenPresetsEngine(Path(self.tmp.name) / "state.conf")
        self.engine.load_state()

    def test_animation_save_preserves_vivid_palette_without_controller_call(self):
        self.engine.cache.update(theme_source="vivid", vivid_preset="toxic_lime", t_dur="5")
        with patch("python.engines.matugen_presets.subprocess.run") as run:
            result = self.engine.write_value("action_apply_anim", "DEFAULT", "true", "bool")
        self.assertTrue(result[0])
        run.assert_not_called()
        state = self.engine.load_state()
        self.assertEqual(state["vivid_active_id"], "toxic_lime")
        self.assertEqual(state["t_dur"], "5")

    def test_theme_apply_from_vivid_uses_color_path(self):
        self.engine.cache.update(theme_source="vivid", vivid_preset="toxic_lime", last_applied_hex="#00FF00")
        with patch.object(self.engine, "apply_color", return_value=(True, "generated")) as apply:
            self.assertTrue(self.engine.apply_settings()[0])
        apply.assert_called_once_with("#00FF00")

    def test_active_vivid_favorite_round_trip_preserves_pack_id(self):
        self.engine.state_file.write_text(
            'LAST_THEME_SOURCE=vivid\nLAST_VIVID_PRESET=toxic_lime\nLAST_APPLIED_HEX=#00FF00\n',
            encoding="utf-8",
        )
        self.engine.load_state()
        self.assertTrue(self.engine.write_value("action_add_favorite", "vivid", "true", "bool")[0])
        self.assertEqual(self.engine.favorites, [])
        self.assertFalse(self.engine.favorites_file.exists())
        other = MatugenPresetsEngine(self.engine.state_file)
        other.load_state()
        self.assertEqual(other.vivid_favorites[0]["id"], "toxic_lime")
        self.assertEqual(other.cache["fav_count"], 1)
        self.assertNotIn("DEFAULT/DEFAULT/fav_count", other.cache)
        with patch.object(other, "apply_vivid", return_value=(True, "restored", "#00FF00")) as apply, \
             patch.object(other, "apply_color") as generate:
            result = other.write_value("fav_vivid_apply_toxic_lime", "DEFAULT", "true", "bool")
        self.assertTrue(result[0])
        apply.assert_called_once_with("toxic_lime")
        generate.assert_not_called()
        self.assertTrue(other.remove_active_favorite()[0])
        other.load_state()
        self.assertEqual(other.vivid_favorites, [])

    def test_same_hex_can_have_independent_seed_and_pack_favorites(self):
        self.assertTrue(self.engine.add_favorite("00FF00", "Green seed")[0])
        original_bytes = self.engine.favorites_file.read_bytes()
        definitions = [dict(id="pack_a", label="Pack A", hex="#00FF00"),
                       dict(id="pack_b", label="Pack B", hex="#00FF00")]
        with patch.object(self.engine, "list_vivid_presets", return_value=definitions):
            for pack in definitions:
                self.assertTrue(self.engine.add_vivid_favorite(pack["id"])[0])
            self.assertFalse(self.engine.add_vivid_favorite("pack_a")[0])
        self.assertEqual(self.engine.cache["fav_count"], 3)
        self.assertTrue(self.engine.remove_vivid_favorite("pack_a")[0])
        self.engine.load_state()
        self.assertEqual([p["id"] for p in self.engine.vivid_favorites], ["pack_b"])
        self.assertEqual(self.engine.favorites_file.read_bytes(), original_bytes)
        self.assertEqual(self.engine.favorites, [("Green seed", "#00FF00")])

    def test_seed_active_favorite_keeps_original_behavior(self):
        self.engine.cache.update(theme_source="custom", last_applied_hex="#FF0055")
        self.assertTrue(self.engine.add_active_favorite()[0])
        self.assertEqual(self.engine.favorites, [("Color #FF0055", "#FF0055")])
        self.assertFalse(self.engine.vivid_favorites_file.exists())
        self.assertTrue(self.engine.remove_active_favorite()[0])

    def test_vivid_write_failures_preserve_memory_and_disk(self):
        with patch.object(self.engine, "_save_vivid_favorites", return_value=False):
            self.assertFalse(self.engine.add_vivid_favorite("toxic_lime")[0])
        self.assertEqual(self.engine.vivid_favorites, [])
        self.assertEqual(self.engine.cache["fav_count"], 0)
        self.assertTrue(self.engine.add_vivid_favorite("toxic_lime")[0])
        original = self.engine.vivid_favorites_file.read_bytes()
        with patch.object(self.engine, "_save_vivid_favorites", return_value=False):
            self.assertFalse(self.engine.remove_vivid_favorite("toxic_lime")[0])
        self.assertEqual(self.engine.vivid_favorites_file.read_bytes(), original)
        self.assertEqual(self.engine.cache["fav_count"], 1)
        self.assertEqual(self.engine.vivid_favorites[0]["id"], "toxic_lime")

    def test_missing_pack_reports_failure_without_hex_fallback(self):
        self.assertFalse(self.engine.add_vivid_favorite("missing_pack")[0])
        self.assertTrue(self.engine.add_vivid_favorite("toxic_lime")[0])
        import vivid_theme
        with patch.object(vivid_theme.vivid_bake, "ensure_pack", return_value=(False, None, "missing definition")), \
             patch.object(self.engine, "apply_color") as generate:
            result = self.engine.write_value("fav_vivid_apply_toxic_lime", "DEFAULT", "true", "bool")
        self.assertFalse(result[0])
        self.assertIn("missing definition", result[1])
        generate.assert_not_called()
        self.assertTrue(self.engine.write_value("fav_vivid_remove_toxic_lime", "DEFAULT", "true", "bool")[0])

    def test_corrupt_vivid_favorites_are_not_overwritten(self):
        for raw in ('{bad', '{}', '[null]', '[{"id": "bad"}]',
                    '[{"id":"pack", "label":"Pack", "hex":false}]'):
            with self.subTest(raw=raw):
                self.engine.vivid_favorites_file.write_text(raw, encoding="utf-8")
                with redirect_stderr(io.StringIO()):
                    self.engine.load_state()
                self.assertTrue(self.engine.vivid_favorites_error)
                self.assertFalse(self.engine.add_vivid_favorite("toxic_lime")[0])
                self.assertEqual(self.engine.vivid_favorites_file.read_text(encoding="utf-8"), raw)
        self.engine.vivid_favorites_file.write_text("[]", encoding="utf-8")
        self.engine.load_state()
        self.assertTrue(self.engine.add_vivid_favorite("toxic_lime")[0])

    def test_false_vivid_favorite_triggers_are_inert(self):
        self.assertTrue(self.engine.add_vivid_favorite("toxic_lime")[0])
        original = self.engine.vivid_favorites_file.read_bytes()
        with patch.object(self.engine, "apply_vivid") as apply:
            for key in ("fav_vivid_apply_toxic_lime", "fav_vivid_remove_toxic_lime"):
                self.assertTrue(self.engine.write_value(key, "DEFAULT", "false", "bool")[0])
        apply.assert_not_called()
        self.assertEqual(self.engine.vivid_favorites_file.read_bytes(), original)

    def test_stale_seed_and_vivid_favorites_report_errors(self):
        for key in ("fav_apply_12", "fav_remove_12", "fav_apply_bad", "fav_vivid_apply_missing"):
            with self.subTest(key=key):
                self.assertFalse(self.engine.write_value(key, "DEFAULT", "true", "bool")[0])

    def test_favorite_cli_in_isolated_home(self):
        import os
        import subprocess

        root = Path(self.tmp.name)
        (root / "user_scripts").symlink_to(SCRIPTS_ROOT, target_is_directory=True)
        state_dir = root / ".config/dusky/settings/dusky_theme"
        state_dir.mkdir(parents=True)
        (state_dir / "state.conf").write_text(
            "LAST_THEME_SOURCE=vivid\nLAST_VIVID_PRESET=toxic_lime\nLAST_APPLIED_HEX=#00FF00\n",
            encoding="utf-8",
        )
        env = os.environ | {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config")}
        def invoke(*args):
            return subprocess.run(
                [sys.executable, str(SCRIPTS_ROOT / "theme_matugen/tui_color_presets.py"), *args],
                capture_output=True, text=True, env=env, timeout=10,
            )
        for args in (("--favorite-active",), ("--add-fav", "00FF00", "Green seed")):
            result = invoke(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = invoke("--list-fav")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved Favorites (2)", result.stdout)
        self.assertIn("[Vivid] Toxic Lime (toxic_lime)", result.stdout)
        self.assertIn("Green seed", result.stdout)
        self.assertEqual(invoke("--add-vivid-fav", "toxic_lime").returncode, 1)
        for args in (("--del-vivid-fav", "toxic_lime"), ("--del-fav", "00FF00")):
            result = invoke(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved Favorites (0)", invoke("--list-fav").stdout)
        self.assertEqual(invoke("--add-vivid-fav").returncode, 1)

    def test_vivid_favorite_publishes_payloads_through_real_vivid_engine(self):
        from contextlib import ExitStack, nullcontext
        from types import SimpleNamespace
        import vivid_theme

        self.assertTrue(self.engine.add_vivid_favorite("toxic_lime")[0])
        self.engine.state_file.write_text('THEME_MODE=dark\n', encoding="utf-8")
        root = Path(self.tmp.name)
        prepared = SimpleNamespace(
            id="toxic_lime", hex="#00FF00", mode="dark", fingerprint="fixture",
            payloads={"generated/colors.css": b"accent: #00FF00;", "generated/gtk-4.css": b"@define-color accent #00FF00;"},
            message="fixture",
        )
        with ExitStack() as stack:
            for name, value in {"STATE_FILE": self.engine.state_file, "STATE_DIR": root,
                                "CONFIG_ROOT": root / "config", "GENERATED_DIR": root / "generated",
                                "RESUME_SNAPSHOT_FILE": root / "vivid_resume.json"}.items():
                stack.enter_context(patch.object(vivid_theme, name, value))
            stack.enter_context(patch.object(vivid_theme, "publication_lock", return_value=nullcontext()))
            ensure = stack.enter_context(patch.object(vivid_theme.vivid_bake, "ensure_pack", return_value=(True, prepared, "")))
            for publisher in (vivid_theme.gtk_colors, vivid_theme.qt_colors, vivid_theme.kde_colors):
                stack.enter_context(patch.object(publisher, "publish"))
            stack.enter_context(patch.object(vivid_theme, "_dispatch_desktop_reloads", return_value=[]))
            generate = stack.enter_context(patch.object(self.engine, "apply_color"))
            result = self.engine.write_value("fav_vivid_apply_toxic_lime", "DEFAULT", "true", "bool")
        self.assertTrue(result[0], result[1])
        ensure.assert_called_once_with("toxic_lime")
        generate.assert_not_called()
        self.assertEqual((root / "generated/colors.css").read_bytes(), prepared.payloads["generated/colors.css"])
        self.assertEqual((root / "config/gtk-4.0/gtk.css").read_bytes(), prepared.payloads["generated/gtk-4.css"])
        self.assertEqual(self.engine.cache["vivid_active_id"], "toxic_lime")
        self.assertEqual(self.engine.cache["theme_source"], "vivid")
        self.assertTrue((root / "vivid_resume.json").exists())


class PresetSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()

    def test_organization_and_unique_seeds(self):
        schema = self.schema
        self.assertEqual(schema.TABS, ["Favorites", "Vivid", "Seeds", "Curated", "Custom", "Theme", "Anim", "Presets"])
        rows = schema.SCHEMA[schema.TABS.index("Seeds")]
        self.assertEqual(len(rows), 79)
        self.assertEqual(len({row.options[0] for row in rows}), 79)
        self.assertEqual(len({row.key for row in rows}), 79)
        self.assertEqual(len({row.group for row in rows}), 5)

    def test_vivid_identity_and_colors_preserved(self):
        schema = self.schema
        presets, diagnostics = schema.vivid_data.load_presets_and_diagnostics()
        self.assertEqual(diagnostics, [])
        rows = [row for row in schema.SCHEMA[1] if row.key.startswith("vivid_apply_")]
        self.assertEqual(len(rows), len(presets))
        for row, preset in zip(rows, presets, strict=True):
            self.assertEqual(row.label, preset["label"])
            self.assertNotIn("#", row.label)
            self.assertEqual(row.key, f"vivid_apply_{preset['id']}")
            self.assertEqual(row.options, [f"trigger:{preset['hex']}"])

    def test_deferred_refresh_tracks_tab_names(self):
        tabs, refreshed = self.schema.DEFERRED_LOAD()
        self.assertEqual(tabs, [0, 1, 3])
        self.assertEqual(set(refreshed), {0, 1, 3})
        self.assertTrue(any(row.key.startswith("vivid_apply_") for row in refreshed[1]))

    def test_favorite_rows_are_grouped_and_use_pack_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = MatugenPresetsEngine(Path(directory) / "state.conf")
            engine.load_state()
            for preset_id in ("toxic_lime", "hyper_red"):
                self.assertTrue(engine.add_vivid_favorite(preset_id)[0])
            self.assertTrue(engine.add_favorite("00FF00", "Green seed")[0])
            rows = self.schema._build_favorites_items(engine)
            keys = {row.key for row in rows}
            self.assertIn("fav_vivid_apply_toxic_lime", keys)
            self.assertIn("fav_vivid_remove_hyper_red", keys)
            self.assertIn("fav_apply_0", keys)
            self.assertNotIn("fav_notice_empty", keys)
            # Each group has one contiguous block, even with several saved packs.
            group_changes = [row.group for index, row in enumerate(rows)
                             if index == 0 or row.group != rows[index - 1].group]
            self.assertEqual(len(group_changes), len(set(group_changes)))

    def test_tui_favorite_save_refresh_apply_and_remove(self):
        import asyncio
        from python.frontend.ui import DuskyTUI

        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                engine = MatugenPresetsEngine(Path(directory) / "state.conf")
                engine.state_file.write_text(
                    "LAST_THEME_SOURCE=vivid\nLAST_VIVID_PRESET=toxic_lime\nLAST_APPLIED_HEX=#00FF00\n",
                    encoding="utf-8",
                )
                engine.load_state()
                schema = load_schema()
                with patch.object(schema, "MatugenPresetsEngine", return_value=engine):
                    _, refreshed = schema.DEFERRED_LOAD()
                    schema.SCHEMA.update(refreshed)
                    key = ("matugen_presets", str(engine.state_file))
                    app = DuskyTUI(
                        engine_pool={key: engine}, default_engine_key=key,
                        schema=schema.SCHEMA, tabs=schema.TABS, title=schema.APP_TITLE,
                        tab_notices=schema.TAB_NOTICES, enable_user_presets=False,
                        deferred_load=schema.DEFERRED_LOAD,
                    )
                    with patch.object(engine, "apply_vivid", return_value=(True, "restored", "#00FF00")) as apply, \
                         patch.object(engine, "apply_color") as generate:
                        async with app.run_test(size=(100, 35)) as pilot:
                            await pilot.pause()
                            async def activate(key):
                                row_index = next(i for i, row in enumerate(app.schema[0]) if row.key == key)
                                app.current_option_list.highlighted = app.current_option_list.get_option_index(f"item_0_{row_index}")
                                await pilot.press("enter")
                                await pilot.pause(.2)
                            await activate("action_add_favorite")
                            self.assertTrue(engine.vivid_favorites_file.exists())
                            await pilot.press("f5")
                            await pilot.pause(.2)
                            self.assertTrue(any(row.key == "fav_vivid_apply_toxic_lime" for row in app.schema[0]))
                            await activate("fav_vivid_apply_toxic_lime")
                            apply.assert_called_once_with("toxic_lime")
                            generate.assert_not_called()
                            await activate("fav_vivid_remove_toxic_lime")
                            await pilot.press("f5")
                            await pilot.pause(.2)
                            self.assertFalse(any(row.key.startswith("fav_vivid_") for row in app.schema[0]))
                        engine.load_state()
                        self.assertEqual(engine.vivid_favorites, [])

        asyncio.run(exercise())

    def test_tui_navigation_and_refresh(self):
        import asyncio
        from python.frontend.ui import DuskyTUI

        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                engine = MatugenPresetsEngine(Path(directory) / "state.conf")
                engine.load_state()
                schema = load_schema()
                with patch.object(schema, "MatugenPresetsEngine", return_value=engine):
                    tabs, refreshed = schema.DEFERRED_LOAD()
                    schema.SCHEMA.update(refreshed)
                    key = ("matugen_presets", str(engine.state_file))
                    app = DuskyTUI(
                        engine_pool={key: engine}, default_engine_key=key,
                        schema=schema.SCHEMA, tabs=schema.TABS, title=schema.APP_TITLE,
                        tab_notices=schema.TAB_NOTICES, user_presets_tab=schema.USER_PRESETS_TAB,
                        enable_user_presets=False, deferred_load=schema.DEFERRED_LOAD,
                    )
                    with patch.object(engine, "apply_color") as apply, \
                         patch.object(engine, "apply_vivid") as vivid:
                        async with app.run_test(size=(100, 35)) as pilot:
                            await pilot.pause()
                            self.assertEqual(app._current_tab_index(), 0)
                            for index in range(len(schema.TABS)):
                                app.action_switch_tab(index)
                                await pilot.pause()
                                self.assertEqual(app._current_tab_index(), index)
                            await pilot.press("f5")
                            await pilot.pause()
                            await pilot.resize_terminal(60, 20)
                            await pilot.pause()
                            app.action_switch_tab(1)
                            await pilot.pause()
                            self.assertEqual(app._current_tab_index(), 1)
                        apply.assert_not_called()
                        vivid.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
