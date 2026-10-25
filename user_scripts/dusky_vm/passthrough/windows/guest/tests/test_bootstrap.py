"""Administrator-only Windows bootstrap tests; installers are inert fixtures."""

import ctypes
import json
import os
import base64
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


IS_ADMIN = sys.platform == "win32" and ctypes.windll.shell32.IsUserAnAdmin()
BOOTSTRAP = Path(__file__).resolve().parents[1] / "00_bootstrap.ps1"


@unittest.skipUnless(IS_ADMIN, "Run with Python 3.15+ in an Administrator Windows terminal")
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dusky bootstrap's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(BOOTSTRAP, self.root / "00_bootstrap.ps1")
        self.installer = self.root / "installer's bundle" / "looking-glass-idd-setup.exe"
        self.installer.parent.mkdir()
        self.installer.write_bytes(b"fixture only; never executed")
        self.certificate = self.root / "publisher's.cer"
        self.certificate.write_bytes(b"fixture only; never imported")
        (self.root / "FoD").mkdir()
        (self.root / "old-python.cmd").write_bytes(b"@echo off\r\nexit /b 1\r\n")
        stub = (
            "import json,sys\nfrom pathlib import Path\np=Path(__file__)\n"
            "p.with_suffix('.args.json').write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\n"
            "sys.exit(3010 if p.with_suffix('.restart').exists() else 0)\n"
        )
        for name in ("setup_ssh.py", "setup_vdd.py"):
            (self.root / name).write_text(stub, encoding="utf-8")

    def invoke(self, *args, python=None, installer=None):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.root / "00_bootstrap.ps1"), "-SkipSpice",
             "-PythonExe", str(python or sys.executable),
             "-IddInstaller", str(installer or self.installer.relative_to(self.root)), *args],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            env={**os.environ, "ProgramData": str(self.root / "ProgramData")},
        )

    def test_relative_paths_with_spaces_and_apostrophes(self):
        result = self.invoke("-SkipSsh", "-IddPublisherCertificate", self.certificate.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads((self.root / "setup_vdd.args.json").read_text(encoding="utf-8"))
        self.assertIn(str(self.installer), arguments)
        self.assertIn(str(self.certificate), arguments)

    def test_missing_installer_stops_before_helpers(self):
        result = self.invoke("-SkipSsh", installer="missing.exe")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Provide the matching", result.stderr)
        self.assertFalse((self.root / "setup_vdd.args.json").exists())

    def test_explicit_invalid_runtime_is_not_replaced(self):
        result = self.invoke("-SkipSsh", python="old-python.cmd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cannot use -PythonExe", result.stderr)
        self.assertFalse((self.root / "setup_vdd.args.json").exists())

    def test_ssh_restart_stops_before_idd(self):
        (self.root / "setup_ssh.restart").touch()
        result = self.invoke("-OpenSshSource", "FoD")
        self.assertEqual(result.returncode, 3010, result.stderr)
        self.assertFalse((self.root / "setup_vdd.args.json").exists())
        arguments = json.loads((self.root / "setup_ssh.args.json").read_text(encoding="utf-8"))
        self.assertIn("--non-interactive", arguments)

    def test_publisher_switch_reaches_idd_helper(self):
        result = self.invoke("-SkipSsh", "-TrustIddPublisher")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads((self.root / "setup_vdd.args.json").read_text(encoding="utf-8"))
        self.assertIn("--trust-installer-publisher", arguments)

    def test_saved_choices_are_idempotent(self):
        self.assertEqual(self.invoke('-SkipSsh').returncode, 0)
        path = self.root / 'ProgramData/DuskyVM/choices.json'
        before = (path.stat().st_ino, path.read_bytes())
        self.assertEqual(self.invoke('-SkipSsh').returncode, 0)
        self.assertEqual(before, (path.stat().st_ino, path.read_bytes()))
        self.assertFalse(json.loads(path.read_text(encoding='utf-8'))['ssh'])

    def test_guided_console_only_needs_no_python_or_idd(self):
        path = str(self.root / '00_bootstrap.ps1').replace("'", "''")
        prompts = str(self.root / 'prompts.txt').replace("'", "''")
        script = f"function Read-Host {{ param($Prompt); Add-Content -LiteralPath '{prompts}' -Value $Prompt; return 'n' }}; & '{path}' -Interactive -SkipSpice -SkipSsh"
        encoded = base64.b64encode(script.encode('utf-16le')).decode()
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',encoded],
                                cwd=self.root, capture_output=True, text=True, timeout=60,
                                env={**os.environ,'ProgramData':str(self.root/'ProgramData')})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'setup_vdd.args.json').exists())
        choices = json.loads((self.root / 'ProgramData/DuskyVM/choices.json').read_text(encoding='utf-8'))
        self.assertFalse(any(choices[key] for key in ('lookingGlass','rdp','ssh','spice')))
        self.assertEqual(len((self.root/'prompts.txt').read_text(encoding='utf-8').splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
