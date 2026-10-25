"""Run inside Windows; these checks do not install or change services."""

from pathlib import Path
import runpy
import sys
import unittest


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


if __name__ == "__main__":
    unittest.main()
