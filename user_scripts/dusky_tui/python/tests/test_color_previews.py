"""Shared color presentation must preserve values and activation behavior."""
from io import StringIO
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.frontend import colors, ui
from python.frontend.core_types import ConfigItem
from rich.console import Console
from rich.text import Text
from textual.widgets import Input, OptionList


class Engine:
    target_path = ""

    def load_state(self):
        return {}


def app_for(rows):
    return ui.DuskyTUI(
        {("fixture", ""): Engine()}, ("fixture", ""), {0: rows}, ["Colors"],
        default_mode="batch", enable_user_presets=False,
    )


class ColorPreviewTests(unittest.TestCase):
    def test_plain_tui_does_not_load_color_module(self):
        code = '''
import sys
from python.frontend import ui
from python.frontend.core_types import ConfigItem
row = ConfigItem(label="Count", key="count", type_="int", default=1)
app = ui.DuskyTUI({}, ("fixture", ""), {0: [row]}, ["Test"], enable_user_presets=False)
app._build_option(row)
assert "python.frontend.colors" not in sys.modules
assert ui._color_preview_hex("ordinary text") is None
assert ui._color_preview_hex("rgb_mode") is None
assert ui._color_preview_hex("RGB") is None
assert "python.frontend.colors" not in sys.modules
'''
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=root,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hue_adjustment_preserves_format_and_alpha(self):
        for value, expected in {
            "#ff000080": "#ff400080", "#f008": "#ff400088",
            "0x80ff0000": "0x80ff4000", "0xff0000": "0xff4000",
            "rgba(ff000080)": "rgba(ff400080)", "rgb(ff0000)": "rgb(ff4000)",
            "rgba(255,0,0,.5)": "rgba(255, 64, 0, .5)",
            "rgba(100%,0%,0%,50%)": "rgba(255, 64, 0, 50%)",
            "hsla(0,100%,50%,.5)": "hsla(15, 100%, 50%, .5)",
            "oklch(60% 0.2 30deg / 50%)": "oklch(60% 0.2 45deg / 50%)",
            "ff000080": "ff400080", "Red": "#ff4000",
        }.items():
            with self.subTest(value=value):
                adjusted = colors.adjust_color_hue(value, 15)
                self.assertEqual(adjusted, expected)
                self.assertEqual(colors.parse_literal_color(adjusted, True).alpha,
                                 colors.parse_literal_color(value, True).alpha)
        for value in ("#00000080", "#ffffff", "#888", "transparent"):
            self.assertEqual(colors.adjust_color_hue(value, 15), value)
        for value in ("$primary", "rgba($primary)", "#badHEX"):
            self.assertIsNone(colors.adjust_color_hue(value, 15))
        self.assertEqual(colors.adjust_color_hue("#abcdef", 360), "#abcdef")
        self.assertIsNone(colors.adjust_color_hue("#abcdef", float("inf")))
        adjusted = colors.adjust_color_hue("hsl(0, 100%, 50%)", .0000001)
        self.assertIsNotNone(colors.parse_literal_color(adjusted))

    def test_hex_and_plain_text_never_use_css_parser(self):
        ui._color_preview_hex.cache_clear()
        colors.parse_literal_color.cache_clear()
        with patch.object(colors, "Color") as parser:
            for value, expected in {
                "#FF0055": "#ff0055", "#abc": "#aabbcc", "#abcd": "#aabbcc",
                "#12345678": "#123456", "0x8044aaff": "#44aaff",
                "rgb(44aaff)": "#44aaff", "rgba(44aaff80)": "#44aaff",
                "fade": None, "deadbeef": None, "123456": None,
                "$primary": None, "var(--primary)": None,
                "#ff0055junk": None, "#-00000": None, "#ff_000": None,
                "0xzz44aaff": None, "rgb(44aaff80)": None,
                "rgba(44aaff)": None,
            }.items():
                with self.subTest(value=value):
                    self.assertEqual(ui._color_preview_hex(value), expected)
            parser.parse.assert_not_called()

    def test_other_color_formats_and_variables(self):
        for value, expected in {
            "FF0055": "#ff0055", "Red": "#ff0000", "rebeccapurple": "#663399",
            "rgb(255, 0, 85)": "#ff0055", "hsl(340, 100%, 50%)": "#ff0055",
            "Rgb(255, 0, 85)": "#ff0055",
            "oklch(0.6 0.2 30)": "#de3e2d", "rgb(255, 0, 85)junk": None,
            "oklch(60% 0.2 30deg / 50%)": "#de3e2d",
            "oklch(0.6 0.2 30)junk": None, "hsl(.., 50%, 50%)": None,
            "rgba($primary)": None, "{{colors.primary.default.hex}}": None,
        }.items():
            with self.subTest(value=value):
                self.assertEqual(ui._color_preview_hex(value, True), expected)

    def test_shared_rows_show_exact_color_without_changing_serialization(self):
        rows = [ConfigItem(label=kind, key=kind, type_=kind, default="#FF0055",
                           exists_in_target=True)
                for kind in ("string", "picker", "cycle", "color")]
        rows.append(ConfigItem(label="Preset", key="preset", type_="bool",
                               default=False, options=["trigger:#FF0055"],
                               exists_in_target=True))
        app = app_for(rows)
        for row in rows:
            with self.subTest(kind=row.type_):
                before = row.serialize(row.value)
                rendered = app._build_option(row)
                self.assertIn("⬤", rendered.plain)
                self.assertNotIn("#FF0055", rendered.plain)
                self.assertTrue(any(str(span.style) == "#ff0055" for span in rendered.spans))
                self.assertEqual(row.serialize(row.value), before)
        # Changing a value must not reuse the old swatch from the row cache.
        rows[0].value = "#00FF55"
        self.assertTrue(any(str(span.style) == "#00ff55" for span in app._build_option(rows[0]).spans))

    def test_unknown_color_keeps_original_variable(self):
        row = ConfigItem(label="Variable", key="variable", type_="color",
                         default="rgba($primary)", exists_in_target=True)
        rendered = app_for([row])._build_option(row)
        self.assertIn("rgba($primary)", rendered.plain)
        self.assertNotIn("⬤", rendered.plain)

    def test_swatch_emits_exact_truecolor(self):
        text = Text()
        ui._append_value_preview(text, "#FF0055")
        output = StringIO()
        Console(file=output, force_terminal=True, color_system="truecolor",
                no_color=False).print(text, end="")
        self.assertIn("\x1b[38;2;255;0;85m", output.getvalue())


class ColorInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_theme_fallback_and_later_updates(self):
        with TemporaryDirectory() as directory:
            theme_path = Path(directory) / "theme.json"
            row = ConfigItem(label="Color", key="color", type_="color", default="#FF0055")
            app = ui.DuskyTUI(
                {("fixture", ""): Engine()}, ("fixture", ""), {0: [row]}, ["Colors"],
                theme_path=theme_path, default_mode="batch", enable_user_presets=False,
            )
            fallback = app.theme_colors.copy()
            for value in fallback.values():
                self.assertIsNotNone(colors.parse_literal_color(value))
            async with app.run_test() as pilot:
                for _ in range(100):
                    if app._boot_complete:
                        break
                    await pilot.pause(.01)
                self.assertTrue(app._boot_complete)
                self.assertIn("⬤", app._build_option(row).plain)
                with patch.object(ui.LOGGER, "exception") as logger:
                    await app.watch_theme_file()
                    await app.watch_theme_file()
                    self.assertEqual(app.theme_colors, fallback)

                    theme_path.write_text('{"primary": "#abcdef"}', encoding="utf-8")
                    await app.watch_theme_file()
                    self.assertEqual(app.theme_colors["accent"], "#abcdef")
                    self.assertEqual(app.theme_colors["error"], fallback["error"])

                    theme_path.write_text('{', encoding="utf-8")
                    await app.watch_theme_file()
                    self.assertEqual(app.theme_colors["accent"], "#abcdef")
                    theme_path.unlink()
                    await app.watch_theme_file()
                    self.assertEqual(app.theme_colors["accent"], "#abcdef")

                    theme_path.write_text('{"primary": "#fedcba"}', encoding="utf-8")
                    await app.watch_theme_file()
                    self.assertEqual(app.theme_colors["accent"], "#fedcba")
                    logger.assert_not_called()

    async def test_freeform_color_adjusts_hue_and_schema_choices_still_cycle(self):
        rows = [
            ConfigItem(label="Free", key="free", type_="color", default="#ff000080", step=30),
            ConfigItem(label="Choices", key="choices", type_="color", default="$primary",
                       options=["$primary", "#abcdef"]),
            ConfigItem(label="Variable", key="variable", type_="color", default="$primary"),
        ]
        app = app_for(rows)
        async with app.run_test() as pilot:
            for _ in range(100):
                if app._boot_complete:
                    break
                await pilot.pause(.01)
            self.assertTrue(app._boot_complete)
            await pilot.press("right")
            self.assertEqual(rows[0].value, "#ff800080")
            await pilot.press("down", "right")
            self.assertEqual(rows[1].value, "#abcdef")
            await pilot.press("down", "right")
            self.assertEqual(rows[2].value, "$primary")

    async def test_edit_picker_and_trigger_keep_original_values(self):
        rows = [
            ConfigItem(label="Editable", key="editable", type_="string", default="#FF0055"),
            ConfigItem(label="Picker", key="picker", type_="picker", default="#FF0055",
                       options=["#FF0055", "#00FF55"]),
            ConfigItem(label="Preset", key="preset", type_="bool", default=False,
                       options=["trigger:#FF0055"]),
        ]
        app = app_for(rows)
        async with app.run_test() as pilot:
            for _ in range(100):
                if app._boot_complete:
                    break
                await pilot.pause(.01)
            self.assertTrue(app._boot_complete)

            app.prompt_string(0, 0, rows[0])
            await pilot.pause()
            self.assertEqual(app.screen.query_one(Input).value, "#FF0055")
            app.screen.dismiss(None)
            await pilot.pause()

            app.prompt_picker(0, 1, rows[1])
            await pilot.pause()
            picker = app.screen.query_one(OptionList)
            self.assertIn("⬤", picker.get_option_at_index(1).prompt.plain)
            await pilot.press("down", "enter")
            self.assertEqual(rows[1].value, "#00FF55")

            app.current_option_list.highlighted = app.current_option_list.get_option_index("item_0_2")
            await pilot.pause()
            await pilot.press("enter")
            self.assertIs(rows[2].value, True)
            self.assertEqual(rows[2].options, ["trigger:#FF0055"])

    async def test_hybrid_options_and_diff_share_previews(self):
        row = ConfigItem(label="Editable", key="editable", type_="string", default="#FF0055")
        app = app_for([row])
        async with app.run_test() as pilot:
            await pilot.pause()
            selected = []
            app.push_screen(ui.HybridInputScreen("Color", "#FF0055", ["#FF0055", "#00FF55"]),
                            selected.append)
            await pilot.pause()
            self.assertIn("⬤", app.screen.query_one(OptionList).get_option_at_index(0).prompt.plain)
            await pilot.press("down", "enter")
            await pilot.pause()
            self.assertEqual(selected, ["#00FF55"])
            row.initial_value, row.value = "#FF0055", "#00FF55"
            app.push_screen(ui.DiffScreen())
            await pilot.pause()
            prompt = app.screen.query_one(OptionList).get_option_at_index(0).prompt
            self.assertEqual(prompt.plain.count("⬤"), 2)
            self.assertNotIn("#FF0055", prompt.plain)


if __name__ == "__main__":
    unittest.main()
