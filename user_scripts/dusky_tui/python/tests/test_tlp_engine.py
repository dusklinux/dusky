"""TLP engine and future router regression tests using temporary configurations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.tlp import TlpConfigEngine


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "tlp.conf"
        self.defaults = self.root / "defaults.conf"
        self.defaults.write_text('USB_DENYLIST="aaaa:bbbb"\nCPU_MAX_PERF_ON_PRF=100\n')
        self.dropins = self.root / "tlp.d"
        self.dropins.mkdir()
        self.config.write_text('# personal settings\nCPU_MAX_PERF_ON_AC=80\nCPU_MAX_PERF_ON_PRF=40 # keep note\nUSB_DENYLIST+="cccc:dddd"\n')
        self.engine = TlpConfigEngine(str(self.config), defaults_path=str(self.defaults), dropin_path=str(self.dropins))
        self.engine.load_state()

    def test_alias_last_assignment_and_append(self):
        values = self.engine.load_state()
        self.assertEqual(values["DEFAULT/CPU_MAX_PERF_ON_PRF"], "40")
        self.assertEqual(values["DEFAULT/USB_DENYLIST"], "aaaa:bbbb cccc:dddd")
        self.assertNotIn("DEFAULT/CPU_MAX_PERF_ON_AC", values)

    def test_duplicate_alias_update_preserves_comments_mode_and_no_growth(self):
        self.config.chmod(0o640)
        self.engine.load_state()
        self.assertTrue(self.engine.write_value("CPU_MAX_PERF_ON_PRF", "DEFAULT", "55")[0])
        text = self.config.read_text()
        self.assertIn('#CPU_MAX_PERF_ON_AC=80\n', text)
        self.assertIn('CPU_MAX_PERF_ON_PRF="55" # keep note\n', text)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertTrue(self.engine.write_value("CPU_MAX_PERF_ON_PRF", "DEFAULT", "65")[0])
        self.assertEqual(len(self.config.read_text()), len(text))

    def test_nil_and_empty_have_distinct_meanings(self):
        self.assertTrue(self.engine.write_value("USB_DENYLIST", "DEFAULT", "")[0])
        self.assertEqual(self.engine.load_state()["DEFAULT/USB_DENYLIST"], "")
        self.assertTrue(self.engine.write_value("USB_DENYLIST", "DEFAULT", "nil")[0])
        self.assertNotIn("DEFAULT/USB_DENYLIST", self.engine.load_state())
        before = self.config.read_bytes()
        self.assertTrue(self.engine.write_value("NEVER_SET", "DEFAULT", "nil")[0])
        self.assertEqual(self.config.read_bytes(), before)

    def test_external_edit_and_deletion_are_detected(self):
        self.config.write_text(self.config.read_text().replace("80", "70"))
        self.assertFalse(self.engine.write_value("CPU_MAX_PERF_ON_PRF", "DEFAULT", "60")[0])
        self.engine.load_state()
        self.config.unlink()
        self.assertFalse(self.engine.write_value("CPU_MAX_PERF_ON_PRF", "DEFAULT", "60")[0])

    def test_invalid_batch_does_not_write(self):
        before = self.config.read_bytes()
        for key, value in (("CPU_MAX_PERF_ON_PRF", "101"), ("START_CHARGE_THRESH_BAT0", "-1"),
                           ("CPU_SCALING_MIN_FREQ_ON_BAL", "2.5"), ("DISK_APM_LEVEL_ON_PRF", "0"),
                           ("USB_DENYLIST", 'abcd:1234"\nTLP_ENABLE=0'), ("TLP_ENABLE", "2"),
                           ("TLP_PROFILE_AC", "invalid"), ("DEVICES_TO_DISABLE_ON_SAV", "wiifi")):
            with self.subTest(key=key):
                self.assertFalse(self.engine.write_batch([("TLP_ENABLE", "DEFAULT", "1", "string"), (key, "DEFAULT", value, "string")])[0])
                self.assertEqual(self.config.read_bytes(), before)

    def test_absent_reset_preserves_missing_final_newline(self):
        self.config.write_text("TLP_ENABLE=1")
        self.engine.load_state()
        self.assertTrue(self.engine.write_value("USB_DENYLIST", "DEFAULT", "nil")[0])
        self.assertEqual(self.config.read_text(), "TLP_ENABLE=1")

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        before = self.config.read_bytes()
        with patch("python.engines.tlp.os.replace", side_effect=OSError("fixture write failure")):
            self.assertFalse(self.engine.write_value("TLP_ENABLE", "DEFAULT", "1")[0])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".tlp-*")), [])

    def test_failed_reload_does_not_accept_external_changes(self):
        self.config.write_text("CPU_MAX_PERF_ON_PRF=70\n")
        self.defaults.unlink()
        with self.assertRaises(FileNotFoundError):
            self.engine.load_state()
        self.assertFalse(self.engine.write_value("CPU_MAX_PERF_ON_PRF", "DEFAULT", "60")[0])
        self.assertEqual(self.config.read_text(), "CPU_MAX_PERF_ON_PRF=70\n")

    def test_new_file_and_all_nil_defaults(self):
        self.config.unlink()
        self.engine.load_state()
        changes = [(key, "DEFAULT", "nil", "string")
                   for key in ("TLP_ENABLE", "CPU_MAX_PERF_ON_PRF", "USB_DENYLIST")]
        self.assertTrue(self.engine.write_batch(changes)[0])
        self.assertFalse(self.config.exists())
        self.assertTrue(self.engine.write_value("TLP_ENABLE", "DEFAULT", "1")[0])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o644)

    def test_matches_installed_tlp_parser(self):
        # Run TLP's parser copy with fixture paths: no /run, /etc, or sysfs writes.
        parser = Path("/usr/share/tlp/tlp-readconfs").read_text()
        for original, replacement in (("/etc/tlp.conf", self.config), ("/etc/tlp.d", self.dropins),
                                      ("/usr/share/tlp/defaults.conf", self.defaults)):
            parser = parser.replace(f"'{original}'", f"'{replacement}'")
        fixture_parser = self.root / "readconfs"
        fixture_parser.write_text(parser)
        self.assertTrue(self.engine.write_batch([("USB_DENYLIST", "DEFAULT", "eeee:ffff 1234:5678", "string"),
                                                 ("CPU_MAX_PERF_ON_PRF", "DEFAULT", "50", "string")])[0])
        result = subprocess.run(["perl", str(fixture_parser), "--notrace"], capture_output=True, text=True, check=True)
        self.assertIn('CPU_MAX_PERF_ON_PRF="50"', result.stdout)
        self.assertIn('USB_DENYLIST="eeee:ffff 1234:5678"', result.stdout)
        self.assertNotIn('CPU_MAX_PERF_ON_PRF="80"', result.stdout)

    def test_inheritance_and_disabled_defaults_match_installed_parser(self):
        parser = Path("/usr/share/tlp/tlp-readconfs").read_text()
        for original, replacement in (("/etc/tlp.conf", self.config), ("/etc/tlp.d", self.dropins),
                                      ("/usr/share/tlp/defaults.conf", self.defaults)):
            parser = parser.replace(f"'{original}'", f"'{replacement}'")
        fixture_parser = self.root / "readconfs"
        fixture_parser.write_text(parser)
        (self.dropins / "10-test.conf").write_text('USB_DENYLIST="1111:2222"\n')
        for content in ('USB_DENYLIST+="3333:4444"\n',
                        'TLP_DISABLE_DEFAULTS=1\nUSB_DENYLIST+="3333:4444"\nUSB_DENYLIST+="5555:6666"\n',
                        'CPU_MAX_PERF_ON_PRF=50\nCPU_MAX_PERF_ON_PRF+=20\n',
                        'USB_DENYLIST="abcd:1234" # note\nUSB_DENYLIST+="ffff:eeee"\n'):
            with self.subTest(content=content):
                self.config.write_text(content)
                effective = subprocess.run(["perl", str(fixture_parser), "--notrace"], capture_output=True, text=True, check=True).stdout
                for uid, value in self.engine.load_state().items():
                    self.assertIn(f'{uid.split("/")[1]}="{value}"', effective)

    def test_headless_router_reads_writes_and_resets_fixture(self):
        wrapper = self.root / "fixture_schema.py"
        wrapper.write_text(
            'from python.frontend.core_types import ConfigItem\n'
            'ENGINE_TYPE = "tlp"\n'
            f'TARGET_FILE = {str(self.config)!r}\n'
            'REQUIRE_ROOT = False\nTHEME_FILE = None\n'
            'APP_TITLE = "TLP Fixture"\nTABS = ["General"]\n'
            'SCHEMA = {0: [ConfigItem(label="CPU", key="CPU_MAX_PERF_ON_PRF", '
            'scope="DEFAULT", type_="string", default="nil")]}\n'
        )
        router = Path(__file__).resolve().parents[1] / "main/main.py"
        for arguments in (("--set", "CPU_MAX_PERF_ON_PRF=61"), ("--export-state",),
                          ("--reset-key", "CPU_MAX_PERF_ON_PRF")):
            result = subprocess.run([sys.executable, str(router), str(wrapper), *arguments],
                                    capture_output=True, text=True, timeout=10,
                                    env={**os.environ, "XDG_STATE_HOME": str(self.root / "state")})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if arguments == ("--export-state",):
                self.assertEqual(json.loads(result.stdout)["DEFAULT/CPU_MAX_PERF_ON_PRF"], "61")
        self.assertNotIn("DEFAULT/CPU_MAX_PERF_ON_PRF", self.engine.load_state())


if __name__ == "__main__":
    unittest.main()
