"""Optional setup choices survive interrupted runs without executing declined tasks."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / "000_dusky_vm.py"
spec = importlib.util.spec_from_file_location("setup_choice_tests", PATH)
setup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = setup
spec.loader.exec_module(setup)


class SetupChoiceTests(unittest.TestCase):
    def test_declined_choice_is_remembered_and_reset_reopens_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = setup.Task(root / "task.py", (), "U", False, False, "task", "Use GPU?")
            profile = setup.Profile(root / "profile.toml", "test", "", (task,))
            state = root / "state"
            with (patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}),
                  patch.object(setup, "execute", return_value=0) as execute,
                  patch("builtins.input", return_value="n") as answer,
                  redirect_stdout(io.StringIO())):
                self.assertEqual(setup.run_profile(profile, profile.tasks, state, [], False, False), 0)
                self.assertEqual(setup.run_profile(profile, profile.tasks, state, [], False, False), 0)
                execute.assert_not_called()
                self.assertEqual(answer.call_count, 1)
                receipt = state / profile.state_name / "task.json"
                self.assertFalse(json.loads(receipt.read_text())["selected"])
                receipt.unlink()
                answer.return_value = "yes"
                self.assertEqual(setup.run_profile(profile, profile.tasks, state, [], False, False), 0)
                execute.assert_called_once()

    def test_dry_run_never_asks_or_creates_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = setup.Task(root / "task.py", (), "U", False, False, "task", "Use GPU?")
            profile = setup.Profile(root / "profile.toml", "test", "", (task,))
            with patch("builtins.input", side_effect=AssertionError("unexpected prompt")), redirect_stdout(io.StringIO()):
                self.assertEqual(setup.run_profile(profile, profile.tasks, root / "state", [], True, False), 0)
            self.assertFalse((root / "state").exists())


if __name__ == "__main__":
    unittest.main()
