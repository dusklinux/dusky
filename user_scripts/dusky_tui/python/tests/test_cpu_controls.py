"""CPU control regressions using temporary sysfs fixtures; never writes live hardware."""
import importlib.util
import json
import contextlib
import io
import runpy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines import cpu_core, pkg_throttle as power


class PowerControlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.rapl = self.home / "powercap"
        self.rapl.mkdir()
        self.domains = []
        for name in ("intel-rapl:0", "intel-rapl-mmio:0"):
            domain = self.rapl / name
            domain.mkdir()
            self.domains.append(domain)
            (domain / "name").write_text("package-0", encoding="ascii")
            (domain / "enabled").write_text("1", encoding="ascii")
            # Deliberately reverse the usual constraint indices.
            for index, label, watts in ((0, "short_term", 65), (1, "long_term", 45)):
                (domain / f"constraint_{index}_name").write_text(label, encoding="ascii")
                (domain / f"constraint_{index}_power_limit_uw").write_text(str(watts * 1_000_000), encoding="ascii")
                (domain / f"constraint_{index}_time_window_us").write_text("1000000", encoding="ascii")
            (domain / "energy_uj").write_text("1000000", encoding="ascii")
            (domain / "max_energy_range_uj").write_text("10000000", encoding="ascii")
        for mock in (
            patch.object(power, "RAPL_BASE", self.rapl),
            patch.object(power, "STATE_FILE", self.home / "state.json"),
            patch.object(power, "get_real_user", return_value=("fixture", 1000, 1000, self.home)),
            patch.object(power, "ensure_real_user_ownership"),
            patch.object(power, "atomic_write", side_effect=self.write_file),
            patch.object(power.PlatformHardwareExtension, "_discover_ppt_nodes", return_value=(None, None, "None")),
        ):
            mock.start()
            self.addCleanup(mock.stop)
        self.engine = power.PkgThrottleEngine()
        self.addCleanup(self.engine.shutdown)

    @staticmethod
    def write_file(path, content, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write(self, **values):
        return self.engine.write_batch([(k, "DEFAULT", str(v), "float") for k, v in values.items()])

    def test_constraint_names_and_all_interfaces(self):
        self.assertTrue(self.write(pl1=35, pl2=35)[0])
        for domain in self.domains:
            self.assertEqual(power.safe_read_int(domain / "constraint_1_power_limit_uw"), 35_000_000)
            self.assertEqual(power.safe_read_int(domain / "constraint_0_power_limit_uw"), 35_000_000)

    def test_nonfinite_negative_unknown_rejected_before_writes(self):
        for key, value in (("pl1", "nan"), ("pl1", "inf"), ("pl1", "-1"), ("pl1", "1e999"), ("nope", "1")):
            with self.subTest(key=key, value=value):
                self.assertFalse(self.write(**{key: value})[0])
        self.assertEqual(self.engine.load_state()["pl1"], 45)

    def test_unsupported_primary_rejected_before_writes(self):
        self.assertFalse(self.write(pl1=35, pl4=35)[0])
        self.assertEqual(self.engine.load_state()["pl1"], 45)

    def test_missing_secondary_constraint_skipped(self):
        (self.domains[1] / "constraint_0_power_limit_uw").unlink()
        ok, msg, _ = self.write(pl2=35)
        self.assertTrue(ok)
        self.assertIn("skipped", msg)

    def test_disabled_domain_enabled(self):
        (self.domains[0] / "enabled").write_text("0", encoding="ascii")
        self.assertTrue(self.write(pl1=35)[0])
        self.assertEqual(power.safe_read_int(self.domains[0] / "enabled"), 1)

    def test_disabled_controller_not_reported_as_success(self):
        (self.rapl / "enabled").write_text("0", encoding="ascii")
        self.assertFalse(self.write(pl1=35)[0])

    def test_failed_write_keeps_saved_configuration(self):
        self.assertTrue(self.write(pl1=35)[0])
        saved = self.home / ".config/dusky/settings/dusky_pkg_power"
        before = saved.read_bytes()
        with patch.object(power, "safe_write", return_value=False):
            ok, msg, _ = self.write(pl1=25)
        self.assertFalse(ok)
        self.assertIn("previous saved configuration retained", msg)
        self.assertEqual(saved.read_bytes(), before)

    def test_platform_applied_before_rapl_and_failure_reported(self):
        platform = Mock(supported=True)
        self.engine.platform = platform

        def firmware(*args, **kwargs):
            for domain in self.domains:
                (domain / "constraint_1_power_limit_uw").write_text("45000000", encoding="ascii")
            return True, ""

        platform.apply.side_effect = firmware
        self.assertTrue(self.engine._apply_values({"pl1": 35_000_000})[0])
        self.assertEqual(self.engine.load_state()["pl1"], 35)
        platform.apply.side_effect = None
        platform.apply.return_value = False, "firmware rejected value"
        ok, msg = self.engine._apply_values({"pl1": 25_000_000})
        self.assertTrue(ok)
        self.assertIn("Firmware synchronization unavailable", msg)
        self.assertEqual(self.engine.load_state()["pl1"], 25)

    def test_platform_readback_and_no_hidden_clamp(self):
        node = self.home / "ppt"
        node.write_text("90", encoding="ascii")
        self.assertTrue(power.PlatformHardwareExtension._write_limit(node, 3))
        self.assertEqual(node.read_text(encoding="ascii").strip(), "3")
        with patch.object(power, "safe_read_int", return_value=90):
            self.assertFalse(power.PlatformHardwareExtension._write_limit(node, 35))

    def test_modern_firmware_discovery(self):
        attributes = self.home / "firmware/asus-armoury/attributes"
        for key in ("ppt_pl1_spl", "ppt_pl2_sppt"):
            node = attributes / key / "current_value"
            node.parent.mkdir(parents=True)
            node.write_text("90", encoding="ascii")
        # Call the real function rather than the setup discovery mock.
        with patch.object(power, "FIRMWARE_ATTRIBUTES", self.home / "firmware"):
            pl1, pl2, vendor = ORIGINAL_DISCOVERY()
        self.assertEqual(pl1, attributes / "ppt_pl1_spl/current_value")
        self.assertEqual(pl2, attributes / "ppt_pl2_sppt/current_value")
        self.assertEqual(vendor, "ASUS Armoury PPT")

    def test_firmware_bounds_reported_without_changing_rapl_request(self):
        node = self.home / "ppt_pl1_spl/current_value"
        node.parent.mkdir()
        node.write_text("90", encoding="ascii")
        (node.parent / "min_value").write_text("28", encoding="ascii")
        (node.parent / "max_value").write_text("90", encoding="ascii")
        self.engine.platform.pl1_node = node
        ok, msg = self.engine._apply_values({"pl1": 20_000_000})
        self.assertTrue(ok)
        self.assertIn("firmware range requires 28 W", msg)
        self.assertEqual(power.safe_read_int(node), 28)
        self.assertEqual(self.engine.load_state()["pl1"], 20)

    def test_firmware_same_value_needs_no_write(self):
        node = self.home / "ppt"
        node.write_text("35", encoding="ascii")
        with patch.object(Path, "write_text", side_effect=AssertionError("Unnecessary write")):
            self.assertTrue(power.PlatformHardwareExtension._write_limit(node, 35))

    def test_per_domain_restore_round_trip(self):
        self.assertTrue(self.write(pl1=35)[0])
        (self.domains[1] / "constraint_1_time_window_us").write_text("2000000", encoding="ascii")
        self.engine.save_persistent_state()
        for domain in self.domains:
            (domain / "constraint_1_power_limit_uw").write_text("65000000", encoding="ascii")
            (domain / "constraint_1_time_window_us").write_text("9000000", encoding="ascii")
        self.assertTrue(self.engine.restore_state())
        self.assertEqual(power.safe_read_int(self.domains[0] / "constraint_1_time_window_us"), 1_000_000)
        self.assertEqual(power.safe_read_int(self.domains[1] / "constraint_1_time_window_us"), 2_000_000)

    def test_missing_baseline_file_reset_does_not_crash(self):
        path = self.home / ".config/dusky/settings/dusky_pkg_bios_baseline.json"
        path.unlink()
        with patch.object(self.engine, "get_boot_limits", return_value={"constraint_1_power_limit_uw": 45_000_000}):
            self.assertTrue(self.engine.restore_defaults()[0])

    def test_wrong_cpu_or_domain_restore_rejected(self):
        self.assertTrue(self.write(pl1=35)[0])
        path = self.home / ".config/dusky/settings/dusky_pkg_power"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["_packages"].pop(self.domains[1].name)
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(self.engine.restore_state())
        state["_cpu_model"] = "Other CPU"
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(self.engine.restore_state())

    def test_telemetry_sample_floor_fractional_limit_and_wrap(self):
        engine = self.engine
        engine.reader.close()
        engine.reader = Mock(fd=1)
        self.addCleanup(engine.reader.close)
        engine.reader.read.return_value = 500_000
        engine.last_e, engine.last_t = 9_500_000, 10.0
        with patch.object(power.time, "perf_counter", return_value=10.05):
            self.assertIn("sampling", engine.get_telemetry())
        self.assertEqual(engine.last_e, 9_500_000)
        (self.domains[0] / "constraint_1_power_limit_uw").write_text("35625000", encoding="ascii")
        with patch.object(power.time, "perf_counter", return_value=10.5):
            result = engine.get_telemetry()
        self.assertIn("2.0 W", result)
        self.assertIn("35.625 W", result)
        self.assertIn("PL2 avg: 65 W", result)

    def test_energy_read_failure_not_zero_watts(self):
        self.engine.reader.close()
        self.engine.reader = Mock(fd=1)
        self.engine.reader.read.return_value = None
        self.assertIn("N/A", self.engine.get_telemetry())


class CoreControlsTests(unittest.TestCase):
    CLI = Path(__file__).resolve().parents[3] / "performance/cpu/tui_dusky_core_manager.py"

    def test_cpu_list_round_trip_and_invalid_tokens(self):
        for cpus in ({0}, {0, 2, 4, 5, 6}, set(range(128))):
            self.assertEqual(cpu_core.parse_cpu_list(cpu_core.format_cpu_list(cpus), 127)[2], cpus)
        for val in ("0,,2", "-1", "2-3-4", "128", ""):
            self.assertFalse(cpu_core.parse_cpu_list(val, 127)[0])

    def test_batch_enables_then_affinity_then_disables(self):
        with patch.object(cpu_core, "detect_topology", return_value=([0, 1, 2], [], {0})), \
                patch.object(cpu_core.CpuCoreEngine, "find_package_domain", return_value=None):
            engine = cpu_core.CpuCoreEngine()
        self.addCleanup(engine.shutdown)
        events = []
        with patch.object(cpu_core, "set_core_status", side_effect=lambda cpu, enabled: (events.append((cpu, enabled)) or (True, ""))), \
                patch.object(engine, "set_systemd_affinity", side_effect=lambda *a, **k: (events.append("affinity") or (True, ""))), \
                patch.object(engine, "save_persistent_state"):
            ok, _, _ = engine.write_batch([("cpu1", "DEFAULT", "false", "bool"), ("systemd_cpu_affinity", "DEFAULT", "2", "string"), ("cpu2", "DEFAULT", "true", "bool")])
        self.assertTrue(ok)
        self.assertEqual(events, [(2, True), "affinity", (1, False)])

    def test_sparse_cli_all_uses_existing_cpus(self):
        spec = importlib.util.spec_from_file_location("cpu_cli_fixture", self.CLI)
        module = importlib.util.module_from_spec(spec)
        with patch.object(cpu_core, "detect_topology", return_value=([0, 2, 4], [], {0})):
            spec.loader.exec_module(module)
        self.assertEqual(module.parse_core_args(["all"], [0, 2, 4]), [0, 2, 4])

    def test_restore_all_attempts_power_after_core_failure(self):
        with patch.object(cpu_core, "detect_topology", return_value=([0], [], {0})), \
                patch.object(cpu_core, "CpuCoreEngine") as engine, \
                patch("os.geteuid", return_value=0), \
                patch.object(sys, "argv", [str(self.CLI), "--restore-all"]), \
                patch("subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            engine.return_value.restore_state.return_value = False
            run.return_value.returncode = 0
            with self.assertRaises(SystemExit) as exit_result:
                runpy.run_path(str(self.CLI), run_name="__main__")
            self.assertEqual(exit_result.exception.code, 1)
            self.assertEqual(run.call_args.args[0][-1], "--restore")

    def test_restore_all_launch_error_has_failed_exit(self):
        with patch.object(cpu_core, "detect_topology", return_value=([0], [], {0})), \
                patch.object(cpu_core, "CpuCoreEngine") as engine, \
                patch("os.geteuid", return_value=0), \
                patch.object(sys, "argv", [str(self.CLI), "--restore-all"]), \
                patch("subprocess.run", side_effect=OSError("fixture launch failure")), \
                contextlib.redirect_stdout(io.StringIO()):
            engine.return_value.restore_state.return_value = True
            with self.assertRaises(SystemExit) as exit_result:
                runpy.run_path(str(self.CLI), run_name="__main__")
            self.assertEqual(exit_result.exception.code, 1)


ORIGINAL_DISCOVERY = power.PlatformHardwareExtension._discover_ppt_nodes

if __name__ == "__main__":
    unittest.main()
