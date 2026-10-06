"""Regression coverage for device outputs in the shared Hyprland Lua engine."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.lua import HyprlandLuaEngine


@unittest.skipUnless(shutil.which("lua"), "Lua interpreter required")
class LuaOutputTests(unittest.TestCase):
    def test_device_outputs_are_settings_and_rule_outputs_are_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.lua"
            path.write_text('''hl.config({ input = {
                touchdevice = { output = "[[Auto]]" },
                tablet = { output = "" },
            } })
            hl.monitor({ output = "example-output", scale = 1 })
            hl.window_rule({ name = "example-rule", match = { workspace = "2" }, no_blur = true })
            ''')
            engine = HyprlandLuaEngine(str(path))
            state = engine.load_state()
            self.assertEqual(state["input/touchdevice/output"], "[[Auto]]")
            self.assertEqual(state["input/tablet/output"], "")
            self.assertEqual(state["monitor/example-output/scale"], 1)
            self.assertNotIn("monitor/example-output/output", state)
            self.assertEqual(state["window_rule/example-rule/match/workspace"], "2")
            self.assertNotIn("window_rule/example-rule/name", state)
            ok, message, _ = engine.write_batch([
                ("output", "input/touchdevice", "example-output", "string"),
                ("output", "input/tablet", "example-output", "string"),
            ])
            self.assertTrue(ok, message)
            self.assertNotIn("Partial", message)
            state = engine.load_state()
            self.assertEqual(state["input/touchdevice/output"], "example-output")
            self.assertEqual(state["input/tablet/output"], "example-output")
            self.assertEqual(state["monitor/example-output/scale"], 1)


if __name__ == "__main__":
    unittest.main()
