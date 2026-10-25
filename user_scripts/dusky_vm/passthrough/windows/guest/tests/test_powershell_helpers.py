"""Run inside Windows; these checks do not install or change services."""

from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock, patch


GUEST = Path(__file__).resolve().parents[1]
HELPERS = {
    name: runpy.run_path(str(GUEST / name))["powershell"]
    for name in ("setup_vdd.py", "setup_ssh.py")
}


@unittest.skipUnless(sys.platform == "win32", "Requires Windows PowerShell")
class PowerShellHelperTests(unittest.TestCase):
    def test_optional_missing_service_is_not_fatal(self):
        for name, execute in HELPERS.items():
            with self.subTest(helper=name):
                self.assertEqual(execute(
                    "Get-Service -Name 'DuskyMissingServiceForRegressionTest' "
                    "-ErrorAction SilentlyContinue"
                ), "")

    def test_early_return_preserves_output(self):
        for name, execute in HELPERS.items():
            with self.subTest(helper=name):
                self.assertEqual(execute("return 'Ready'"), "Ready")

    def test_terminating_error_still_fails(self):
        for name, execute in HELPERS.items():
            with self.subTest(helper=name):
                with self.assertRaisesRegex(RuntimeError, "intentional regression test error"):
                    execute("Write-Error 'intentional regression test error'")


class OpenSshServicingTests(unittest.TestCase):
    def setUp(self):
        self.install = HELPERS["setup_ssh.py"].__globals__["install_server"]

    def test_existing_service_does_not_reinstall(self):
        execute = Mock(return_value="Installed")
        with patch.dict(self.install.__globals__, powershell=execute):
            self.assertFalse(self.install(None, None))
        execute.assert_called_once()

    def test_fod_restart_is_propagated(self):
        execute = Mock(side_effect=["", "RestartRequired"])
        with patch.dict(self.install.__globals__, powershell=execute):
            self.assertTrue(self.install(Path("FoD media"), None))
        command = execute.call_args.args[0]
        self.assertIn("-LimitAccess", command)
        self.assertIn("RestartNeeded", command)

    def test_completed_fod_install_does_not_require_restart(self):
        execute = Mock(side_effect=["", ""])
        with patch.dict(self.install.__globals__, powershell=execute):
            self.assertFalse(self.install(Path("FoD media"), None))

    def test_missing_offline_media_is_actionable(self):
        with patch.dict(self.install.__globals__, powershell=Mock(return_value="")):
            with self.assertRaisesRegex(RuntimeError, "offline Windows"):
                self.install(None, None)


if __name__ == "__main__":
    unittest.main()
