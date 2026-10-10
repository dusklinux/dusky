"""Fresh-process engine stress fixtures; all files live under a temporary HOME.

Lua parsing and mutation use the installed interpreter. System commands are
simulated, so these fixtures cannot validate daemon or hardware integration.
Run a longer matrix with DUSKY_ENGINE_STRESS_LOOPS=100.
"""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULES = sorted(p.stem for p in (ROOT / "python/engines").glob("*.py"))


def exercise(module, loops):
    sys.path.insert(0, str(ROOT))
    real_run = subprocess.run

    def run(args, *pos, **kw):
        if Path(args[0]).name in {"lua", "lua5.4", "lua54"}:
            return real_run(args, *pos, **kw)
        output = ""
        if args[0] == "hyprctl":
            output = "[]"
        elif args[0] == "tlp":
            output = "TLP 1.11.0\n"
        elif args[0] == "nmcli" and "radio" in args:
            output = "enabled\n"
        elif args[0] == "timedatectl":
            output = "Timezone=UTC\nNTP=yes\nLocalRTC=no\n"
        elif "list-unit-files" in args:
            output = "fixture.service enabled\n"
        elif args[0] == "bootctl":
            output = "[]"
        elif args[0] == "ufw":
            output = "Status: inactive\n"
        return subprocess.CompletedProcess(args, 0, output, "")

    with tempfile.TemporaryDirectory(prefix="dusky-engine-matrix-") as directory:
        home = Path(directory)
        env = {"HOME": directory, "XDG_CONFIG_HOME": str(home / ".config"),
               "XDG_CACHE_HOME": str(home / ".cache"), "XDG_RUNTIME_DIR": str(home / "run")}
        with patch.dict(os.environ, env), patch("subprocess.run", side_effect=run), \
                patch("subprocess.Popen", side_effect=AssertionError("Unexpected process launch")):
            # Real Lua runs need Popen; only allow its known executable.
            m = importlib.import_module("python.engines." + module)
            target = home / "fixture.conf"
            fixtures = {
                "ini": ("IniConfigEngine", "[main]\nx=1\n", "x", "main", "main/x"),
                "bridged_ini": ("BridgedIniEngine", "[main]\nx=1\n", "x", "main", "main/x"),
                "systemd_power": ("SystemdPowerEngine", "[Login]\nHandleLidSwitch=ignore\n", "HandleLidSwitch", "Login", "HandleLidSwitch"),
                "tlp": ("TlpConfigEngine", "X=1\n", "X", "DEFAULT", "DEFAULT/X"),
                "cmdline": ("CmdlineEngine", "quiet x=1\n", "x", "DEFAULT", "DEFAULT/x"),
                "flatdotconfig": ("FlatDotConfigEngine", "main.x 1\n", "x", "main", "main/x"),
                "environment_variables": ("ShellEnvEngine", "export X=1\n", "X", "DEFAULT", "DEFAULT/X"),
                "shell_fallback": ("ShellFallbackEngine", 'readonly X="${X:-1}"\n', "X", "DEFAULT", "DEFAULT/X"),
                "hyprlang": ("HyprlangEngine", "main {\n x = 1\n}\n", "x", "main", "main/x"),
                "json_engine": ("JsonEngine", '{"main":{"nested":{"x":1}},"keep":"值"}', "x", "main/nested", "main/nested/x"),
                "toml": ("TomlEngine", '[main.nested]\nx=1\nkeep="值"\n', "x", "main/nested", "main/nested/x"),
            }
            if module in fixtures:
                cls, content, key, scope, lookup = fixtures[module]
                target.write_text(content, encoding="utf-8")
                engine = getattr(m, cls)(str(target))
                for i in range(loops):
                    state = engine.load_state()
                    assert lookup in state, (module, lookup, state)
                    result = engine.write_value(key, scope, str(i + 2), "int")
                    assert result[0], result
                    state = engine.load_state()
                    assert str(state[lookup]) == str(i + 2), (module, state)
                assert not list(home.glob("tmp*")), list(home.iterdir())
            elif module in {"lua", "autostart_engine", "trackpad", "monitor_engine"}:
                cls = {"lua": "HyprlandLuaEngine", "autostart_engine": "AutostartLuaEngine",
                       "trackpad": "TrackpadLuaEngine", "monitor_engine": "MonitorLuaEngine"}[module]
                target = home / "fixture.lua"
                target.write_text("hl.config({input = {sensitivity = 1}})\n", encoding="utf-8")
                # Restore Popen only around the installed Lua evaluator.
                with patch("subprocess.Popen", REAL_POPEN):
                    engine = getattr(m, cls)(str(target))
                    for i in range(loops):
                        engine.load_state()
                        result = engine.write_value("sensitivity", "input", str(i + 2), "int")
                        assert result[0], result
                        assert str(engine.load_state()["input/sensitivity"]) == str(i + 2)
            elif module == "matugen":
                target.write_text('[templates.fixture]\ninput_path="a"\noutput_path="b"\n', encoding="utf-8")
                engine = m.MatugenEngine(str(target))
                for i in range(loops):
                    engine.load_state()
                    enabled = i % 2 == 0
                    result = engine.write_value("fixture", "DEFAULT", str(enabled).lower(), "bool")
                    assert result[0], result
                    assert engine.load_state()["fixture"] == enabled
            elif module == "fontconfig":
                engine = m.FontconfigEngine(str(target))
                with patch.object(engine, "refresh_cache"), patch.object(engine, "_sync_system_fonts", return_value=True):
                    for i in range(loops):
                        enabled = i % 2 == 0
                        result = engine.write_value("antialias", "DEFAULT", str(enabled).lower(), "bool")
                        assert result[0], result
                        assert engine.load_state()["antialias"] == enabled
            elif module == "dusky_sites":
                target.write_text('{"webThemeEnabled":false,"disabledSites":[]}', encoding="utf-8")
                engine = m.DuskySitesEngine(str(target))
                for i in range(loops):
                    enabled = i % 2 == 0
                    assert engine.write_value("webThemeEnabled", "DEFAULT", str(enabled).lower(), "bool")[0]
                    assert engine.load_state()["webThemeEnabled"] == enabled
            elif module == "locale_gen":
                target.write_text("#en_US.UTF-8 UTF-8\n", encoding="utf-8")
                engine = m.LocaleGenEngine(str(target))
                engine.locale_conf_path = home / "locale.conf"
                engine.vconsole_conf_path = home / "vconsole.conf"
                for i in range(loops):
                    engine.load_state()
                    enabled = i % 2 == 0
                    result = engine.write_value("en_US.UTF-8", "DEFAULT", str(enabled).lower(), "bool")
                    assert result[0], result
                    assert engine.load_state()["timezone"] == "UTC"
            elif module == "dns_systemd":
                target.write_text("[Resolve]\nDNS=1.1.1.1\n", encoding="utf-8")
                engine = m.SystemdDnsEngine(str(target))
                for _ in range(loops):
                    assert engine.load_state()["DNS"] == "1.1.1.1"
            elif module == "systemd_boot":
                target.write_text("title Fixture\nlinux /vmlinuz-fixture\noptions quiet x=1\n", encoding="utf-8")
                loader = home / "loader.conf"
                loader.write_text("timeout 3\n", encoding="utf-8")
                with patch.object(m.SystemdBootEngine, "_resolve_loader_path", return_value=loader):
                    engine = m.SystemdBootEngine(str(target))
                    for i in range(loops):
                        engine.load_state()
                        result = engine.write_value("x", "DEFAULT", str(i + 2), "int")
                        assert result[0], result
                        assert str(engine.load_state()["DEFAULT/x"]) == str(i + 2)
            elif module == "fstab":
                target.write_text("UUID=fixture / ext4 defaults 0 1\n", encoding="utf-8")
                engine = m.FstabEngine(str(target))
                for _ in range(loops):
                    assert engine.load_state()
            elif module == "kokoro":
                target.write_text('[engine]\nprovider="cpu"\n[voice]\nspec="af_heart"\n', encoding="utf-8")
                engine = m.KokoroEngine(str(target))
                for _ in range(loops):
                    assert engine.load_state()["engine.provider"] == "cpu"
            elif module == "systemd":
                engine = m.SystemdEngine()
                for _ in range(loops):
                    assert engine.load_state()["user/fixture.service"] == "true"
            elif module == "network_manager":
                engine = m.NetworkManagerEngine(str(target))
                for _ in range(loops):
                    assert isinstance(engine.load_state(), dict)
                engine.shutdown()
            elif module == "ufw":
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for name in ("UFW_SYSCTL_CONF", "UFW_BEFORE_RULES", "UFW_AFTER_RULES",
                                 "UFW_AFTER6_RULES", "UFW_BEFORE6_RULES", "UFW_CONF", "UFW_AFTER_INIT", "DOMAINS_STORAGE"):
                        path = home / name
                        if name != "DOMAINS_STORAGE":
                            path.write_text("", encoding="utf-8")
                        stack.enter_context(patch.object(m, name, path))
                    engine = m.UfwEngine(str(target))
                    for _ in range(loops):
                        assert isinstance(engine.load_state(), dict)
            elif module == "cpu_core":
                with patch.object(m, "detect_topology", return_value=([0], [], {0})), \
                        patch.object(m.CpuCoreEngine, "find_package_domain", return_value=None):
                    engine = m.CpuCoreEngine(str(target))
                    for _ in range(loops):
                        assert isinstance(engine.load_state(), dict)
                    engine.shutdown()
                    engine.shutdown()
            elif module == "pkg_throttle":
                with patch.object(m, "RAPL_BASE", home), \
                        patch.object(m.PlatformHardwareExtension, "_discover_ppt_nodes", return_value=(None, None, "None")):
                    engine = m.PkgThrottleEngine(str(target))
                    for _ in range(loops):
                        assert isinstance(engine.load_state(), dict)
                    engine.shutdown()
            elif module == "starship":
                target.write_text('format="fixture"\n', encoding="utf-8")
                engine = m.StarshipEngine(str(target))
                engine.presets_dir = home / "presets"
                engine.presets_dir.mkdir()
                (engine.presets_dir / "fixture.toml").write_text('format="fixture"\n', encoding="utf-8")
                for _ in range(loops):
                    result = engine.write_value("active_prompt", "DEFAULT", "preset:fixture", "string")
                    assert result[0], result
                    assert engine.load_state()["active_prompt"] == "preset:fixture"
            elif module == "hyprlock":
                target.write_text("general {\n}\n", encoding="utf-8")
                theme = home / "themes" / "fixture"
                theme.mkdir(parents=True)
                (theme / "hyprlock.conf").write_text("general {\n}\n", encoding="utf-8")
                engine = m.HyprlockEngine(str(target), str(theme.parent))
                for _ in range(loops):
                    result = engine.write_value("hyprlock", "DEFAULT", "1", "int")
                    assert result[0], result
                    assert engine.load_state()["active_theme_folder"] == "fixture"
            elif module == "waybar_engine":
                root = home / "waybar"
                root.mkdir()
                theme = root / "fixture"
                theme.mkdir()
                (theme / "config.jsonc").write_text('{"position":"top"}', encoding="utf-8")
                (theme / "style.css").write_text("", encoding="utf-8")
                engine = m.WaybarEngine(str(root))
                with patch.object(engine, "_async_restart_waybar"):
                    for i in range(loops):
                        result = engine.write_value("action_invert_pos", "DEFAULT", "true", "bool")
                        assert result[0], result
                        data = json.loads((theme / "config.jsonc").read_text(encoding="utf-8"))
                        assert data["position"] == ("bottom" if i % 2 == 0 else "top")
            elif module == "rich_speedtest":
                for _ in range(loops):
                    assert len(m.make_sparkline([0, 1, 2])) == 28
            elif module == "gsettings":
                from python.frontend.core_types import ConfigItem
                item = ConfigItem(
                    label="Theme",
                    key="gtk-theme",
                    scope="org.gnome.desktop.interface",
                    type_="string",
                    default="Adwaita",
                )
                engine = m.GSettingsEngine(items=[item])
                engine._gio_available = False  # Writes are simulated in this fixture.
                with patch.object(engine, "get_installed_schemas", return_value={"org.gnome.desktop.interface"}), \
                        patch.object(engine, "_write_single_setting", return_value=m.SettingWriteResult(True, "", "dusky-test")):
                    for _ in range(loops):
                        result = engine.write_value("gtk-theme", "org.gnome.desktop.interface", "dusky-test", "string")
                        assert result[0], result
                        assert engine.cache["org.gnome.desktop.interface/gtk-theme"] == "dusky-test"
            else:
                raise AssertionError(f"Missing engine fixture: {module}")


REAL_POPEN = subprocess.Popen


class EngineMatrixTests(unittest.TestCase):
    def test_every_engine_defers_write_dependencies_on_import(self):
        code = (
            "import importlib,sys\n"
            "from python.frontend import core_types\n"
            "before=set(sys.modules)\n"
            "importlib.import_module('python.engines.'+sys.argv[1])\n"
            "assert not ({'tempfile','subprocess'} & (set(sys.modules)-before)), sys.argv[1]\n"
        )
        for module in MODULES:
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-c", code, module], cwd=ROOT,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)

    def check_engine(self, module):
        result = subprocess.run(
            [sys.executable, "-X", "dev", str(Path(__file__).resolve()), "--worker", module,
             os.environ.get("DUSKY_ENGINE_STRESS_LOOPS", "8")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
            env={k: v for k, v in os.environ.items() if k not in {"SUDO_USER", "SUDO_UID", "PKEXEC_UID"}},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("ResourceWarning", result.stderr)


for _module in MODULES:
    setattr(EngineMatrixTests, "test_" + _module,
            lambda self, module=_module: self.check_engine(module))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        exercise(sys.argv[2], int(sys.argv[3]))
    else:
        unittest.main()
