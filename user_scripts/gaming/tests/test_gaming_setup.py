"""Installer regressions and stress fixtures. No privileged changes or package transactions."""

import io
import itertools
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gaming_setup as setup
from rich.console import Console


class InstallerStressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gaming-stress-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        console_patch = patch.object(setup, "console", Console(file=io.StringIO()))
        console_patch.start()
        self.addCleanup(console_patch.stop)
        self.real_run = subprocess.run

    def local_sudo(self, command, **kwargs):
        """Run only fixture file operations as the current user; never execute sudo."""
        if command[0] != "sudo":
            return self.real_run(command, **kwargs)
        command = command[1:]
        if command[0] == "-n":
            command = command[1:]
        self.assertIn(command[0], {"install", "mv", "cp", "rm"})
        mutated = command[-1:] if command[0] in {"install", "cp"} else command[1:]
        for argument in mutated:
            if argument.startswith("/"):
                self.assertTrue(Path(argument).is_relative_to(self.root), argument)
        return self.real_run(command, **kwargs)

    def test_atomic_configuration_replacement(self):
        source = self.root / "new.conf"
        target = self.root / "current.conf"
        source.write_text("new configuration\n", encoding="utf-8")
        target.write_text("old configuration\n", encoding="utf-8")
        def execute(command, **kwargs):
            result = self.local_sudo(command, **kwargs)
            if "install" in command:
                self.assertEqual(target.read_text(encoding="utf-8"), "old configuration\n")
            return result
        with patch.object(setup.subprocess, "run", side_effect=execute):
            setup.install_configuration(setup.SetupContext(auto_yes=True), source, target, "fixture")
        self.assertEqual(target.read_text(encoding="utf-8"), "new configuration\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_failed_activation_preserves_old_configuration_and_cleans_stage(self):
        source = self.root / "new.conf"
        target = self.root / "current.conf"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        def execute(command, **kwargs):
            if "mv" in command:
                return subprocess.CompletedProcess(command, 1)
            return self.local_sudo(command, **kwargs)
        with patch.object(setup.subprocess, "run", side_effect=execute):
            with self.assertRaises(RuntimeError):
                setup.install_configuration(setup.SetupContext(auto_yes=True), source, target, "fixture")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_interruption_preserves_old_configuration_and_cleans_stage(self):
        source = self.root / "new.conf"
        target = self.root / "current.conf"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        def execute(command, **kwargs):
            if "mv" in command:
                raise KeyboardInterrupt
            return self.local_sudo(command, **kwargs)
        with patch.object(setup.subprocess, "run", side_effect=execute):
            with self.assertRaises(KeyboardInterrupt):
                setup.install_configuration(setup.SetupContext(auto_yes=True), source, target, "fixture")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_included_multilib_is_not_duplicated(self):
        include = self.root / "repositories.conf"
        include.write_text("[multilib]\nServer = https://example.invalid/repo\n", encoding="utf-8")
        content = f"[options]\nArchitecture=auto\nInclude = {include}\n\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n"
        config = self.root / "pacman.conf"
        config.write_text(content, encoding="utf-8")
        with patch.object(setup, "run_command", side_effect=AssertionError("Unneeded edit")):
            self.assertFalse(setup.enable_multilib_and_optimizations(setup.SetupContext(), config))
        self.assertEqual(config.read_text(encoding="utf-8"), content)

    def test_config_repair_matrix_is_idempotent(self):
        mirror = self.root / "mirrors"
        mirror.write_text("Server = https://example.invalid/repo\n", encoding="utf-8")
        sections = ("", f"#[multilib]\n#Include = {mirror}\n",
                    f"[multilib]\n#Include = {mirror}\n",
                    "[multilib]\nServer = https://example.invalid/repo\n")
        options = ("", "#Color\n", "Color\n#Color\n",
                   "#ParallelDownloads = 5\n", "ParallelDownloads = 12\n#ParallelDownloads = 5\n")
        config = self.root / "pacman.conf"
        with patch.object(setup.subprocess, "run", side_effect=self.local_sudo):
            for section, option in itertools.product(sections, options):
                with self.subTest(section=section, option=option):
                    config.write_text(f"[options]\nArchitecture=auto\n{option}{section}", encoding="utf-8")
                    setup.enable_multilib_and_optimizations(setup.SetupContext(auto_yes=True), config)
                    result = self.real_run(["pacman-conf", "--config", str(config), "--repo-list"],
                                           capture_output=True, encoding="utf-8", check=True)
                    self.assertEqual(result.stdout.splitlines().count("multilib"), 1)
                    self.assertFalse(setup.enable_multilib_and_optimizations(setup.SetupContext(), config))

    def test_cli_skip_and_build_matrix(self):
        flags = ("gpu", "wine", "perf", "flatpak", "sysctl", "dwarfs", "protonup")
        for switches, build in itertools.product(itertools.product((False, True), repeat=len(flags)),
                                                (None, "--native", "--bin")):
            argv = ["gaming_setup.py", "--yes", "--dry-run"]
            argv.extend(f"--skip-{name}" for name, active in zip(flags, switches, strict=True) if active)
            if build:
                argv.append(build)
            with self.subTest(argv=argv), patch.object(sys, "argv", argv):
                args, modules = setup.parse_arguments()
                second = setup.SelectedModules()
                setup.apply_cli_overrides(args, second)
                self.assertEqual(modules, second)
                for name, active in zip(flags, switches, strict=True):
                    if not active:
                        continue
                    match name:
                        case "wine":
                            self.assertTrue({"wine_stack", "runtime_32bit"}.isdisjoint(modules.categories))
                        case "perf":
                            self.assertNotIn("performance_tools", modules.categories)
                        case "dwarfs" | "protonup":
                            self.assertEqual(getattr(modules, name + "_mode"), "skip")
                        case _:
                            field = {"gpu": "gpu_drivers", "flatpak": "flatpak_apps", "sysctl": "sysctl_tuning"}[name]
                            self.assertFalse(getattr(modules, field))

    def test_skip_performance_does_not_query_or_install_flatpak_layers(self):
        ctx = setup.SetupContext(auto_yes=True)
        ctx.modules.categories.discard("performance_tools")
        commands = []
        def execute(_ctx, command, *_args, **_kwargs):
            commands.append(command)
            return True
        with patch.object(setup, "get_flatpak_inventory", return_value={"--user": set(), "--system": set()}), \
             patch.object(setup, "flatpak_layer_branches", side_effect=AssertionError("Performance skipped")), \
             patch.object(setup, "run_command", side_effect=execute):
            setup.configure_flatpak_ecosystem(ctx)
        self.assertFalse(any("VulkanLayer" in argument for command in commands for argument in command))

    def test_bridge_preserves_user_links_and_files(self):
        exports = (self.root / "system-export", self.root / "user-export")
        source = exports[1] / "icon.png"
        source.parent.mkdir()
        source.write_bytes(b"icon")
        custom = self.root / "custom-icon.png"
        custom.write_bytes(b"custom")
        link = self.root / "icon.png"
        link.symlink_to(custom)
        setup.bridge_export(source, link, exports)
        self.assertEqual(link.readlink(), custom)
        link.unlink()
        link.write_bytes(b"regular file")
        setup.bridge_export(source, link, exports)
        self.assertEqual(link.read_bytes(), b"regular file")

    def test_bridge_upgrades_relative_managed_links_and_is_idempotent(self):
        export = self.root / "exports"
        export.mkdir()
        old, new = export / "old.desktop", export / "new.desktop"
        new.write_text("new", encoding="utf-8")
        link = self.root / "app.desktop"
        link.symlink_to("exports/old.desktop")
        self.assertTrue(setup.managed_export_link(link, (export,)))
        setup.bridge_export(new, link, (export,))
        before = link.lstat().st_ino
        setup.bridge_export(new, link, (export,))
        self.assertEqual(link.lstat().st_ino, before)

    def test_vulkan_enumeration_once_and_without_x_display(self):
        ctx = setup.SetupContext()
        result = subprocess.CompletedProcess([], 0, "GPU0:\n deviceName = Intel\nGPU1:\n deviceName = NVIDIA\n", "")
        with patch.dict(os.environ, {"DISPLAY": ":99"}), patch.object(setup.subprocess, "run", return_value=result) as execute:
            setup.verify_vulkan(ctx)
        self.assertFalse(ctx.failures)
        self.assertEqual(execute.call_count, 1)
        self.assertNotIn("DISPLAY", execute.call_args.kwargs["env"])

    def test_vulkan_failure_empty_output_and_timeout_remain_visible(self):
        for result in (subprocess.CompletedProcess([], 0, "", ""),
                       subprocess.CalledProcessError(1, "vulkaninfo"),
                       subprocess.TimeoutExpired("vulkaninfo", 20), FileNotFoundError("vulkaninfo")):
            ctx = setup.SetupContext()
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with self.subTest(result=result), patch.object(setup.subprocess, "run", **kwargs):
                setup.verify_vulkan(ctx)
            self.assertTrue(ctx.failures)

    def test_vulkan_dry_run_is_read_only(self):
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("Dry run spawned a probe")):
            setup.verify_vulkan(setup.SetupContext(dry_run=True))

    def test_child_cancellation_is_not_retried(self):
        for code in (-signal.SIGINT, 128 + signal.SIGINT):
            with self.subTest(code=code), patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], code)) as execute:
                with self.assertRaises(KeyboardInterrupt):
                    setup.run_command(setup.SetupContext(auto_yes=True), ["fixture"], "cancel", retries=3)
            self.assertEqual(execute.call_count, 1)

    def test_lutris_proton_launcher_is_selected(self):
        self.assertIn("umu-launcher", setup.PACKAGE_CATALOG["wine_stack"]["packages"])

    def test_runtime_extension_branch_comes_from_metadata(self):
        ctx = setup.SetupContext()
        metadata = "[Extension org.freedesktop.Platform.VulkanLayer]\nversions=25.08; 26.08;\n[Build]\nx=1\n[Build]\nx=2\n"
        with patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, metadata, "")):
            branches = setup.flatpak_layer_branches(ctx, "--user", {"org.gnome.Platform//50", "org.kde.Platform//6.10"})
        self.assertEqual(branches, {"25.08", "26.08"})
        self.assertFalse(ctx.failures)

    def test_duplicate_icons_keep_user_precedence_without_link_churn(self):
        data = self.root / "data"
        system = self.root / "system/exports/share"
        user = data / "flatpak/exports/share"
        relative = Path("icons/hicolor/32x32/apps/com.example.App.png")
        for export, payload in ((system, b"system"), (user, b"user")):
            icon = export / relative
            icon.parent.mkdir(parents=True)
            icon.write_bytes(payload)
        def mapped_path(value):
            return system if str(value) == "/var/lib/flatpak/exports/share" else Path(value)
        ctx = setup.SetupContext(auto_yes=True)
        with patch.object(setup, "xdg_data_home", return_value=data), \
             patch.object(setup, "Path", side_effect=mapped_path), \
             patch.object(setup, "get_installed_flatpaks", return_value=[]), \
             patch.object(setup, "run_command", return_value=True):
            setup.integrate_desktop_and_icons(ctx)
            self.assertEqual((data / relative).readlink(), user / relative)
            with patch.object(Path, "unlink", side_effect=AssertionError("Unnecessary unlink")), \
                 patch.object(Path, "symlink_to", side_effect=AssertionError("Unnecessary relink")):
                setup.integrate_desktop_and_icons(ctx)
        self.assertFalse(ctx.failures)

    def test_maintenance_ignores_unrelated_pacman_lock(self):
        ctx = setup.SetupContext(modules=setup.SelectedModules(
            categories=set(), gpu_drivers=False, sysctl_tuning=False,
            dwarfs_mode="skip", protonup_mode="skip", flatpak_apps=False))
        with patch.object(setup.os, "geteuid", return_value=1000), patch.object(Path, "exists", return_value=True):
            setup.check_root_and_locks(ctx)
        ctx.modules.categories.add("core_clients")
        with patch.object(setup.os, "geteuid", return_value=1000), patch.object(Path, "exists", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                setup.check_root_and_locks(ctx)

    def test_each_translator_dll_is_checked(self):
        paths = [self.root / "dxvk" / arch / dll for arch in ("x32", "x64")
                 for dll in ("d3d8.dll", "d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll")]
        paths += [self.root / "vkd3d-proton/x64" / dll for dll in ("d3d12.dll", "d3d12core.dll")]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        def fixture_path(value):
            return self.root / Path(value).relative_to("/usr/share")
        with patch.object(setup, "Path", side_effect=fixture_path):
            self.assertTrue(all(setup.translation_libraries_ready().values()))
            for path in paths:
                with self.subTest(path=path):
                    path.unlink()
                    self.assertFalse(setup.translation_libraries_ready()[
                        "dxvk-bin" if "dxvk" in path.parts else "vkd3d-proton-bin"])
                    path.write_bytes(b"fixture")

    def test_translator_repair_forces_reinstallation_and_checks_result(self):
        ctx = setup.SetupContext(auto_yes=True)
        missing = {"dxvk-bin": False, "vkd3d-proton-bin": False}
        with patch.object(setup, "translation_libraries_ready", return_value=missing), \
             patch.object(setup.shutil, "which", return_value="/usr/bin/paru"), \
             patch.object(setup, "run_command", return_value=True) as execute:
            setup.configure_dxvk_vkd3d(ctx)
        self.assertNotIn("--needed", execute.call_args.args[1])
        self.assertEqual(len(ctx.failures), 2)

    def test_translator_repair_success_has_no_false_failure(self):
        ctx = setup.SetupContext(auto_yes=True)
        before = {"dxvk-bin": False, "vkd3d-proton-bin": True}
        after = {"dxvk-bin": True, "vkd3d-proton-bin": True}
        with patch.object(setup, "translation_libraries_ready", side_effect=[before, after]), \
             patch.object(setup.shutil, "which", return_value="/usr/bin/paru"), \
             patch.object(setup, "run_command", return_value=True) as execute:
            setup.configure_dxvk_vkd3d(ctx)
        self.assertFalse(ctx.failures)
        self.assertNotIn("vkd3d-proton-bin", execute.call_args.args[1])

    def test_translator_dry_run_does_not_check_uninstalled_result(self):
        ctx = setup.SetupContext(dry_run=True)
        with patch.object(setup, "translation_libraries_ready", return_value={"dxvk-bin": False}) as readiness, \
             patch.object(setup.shutil, "which", return_value="/usr/bin/paru"):
            setup.configure_dxvk_vkd3d(ctx)
        self.assertEqual(readiness.call_count, 1)
        self.assertFalse(ctx.failures)

    def test_unchanged_invalid_multilib_is_not_reported_as_ready(self):
        conf = self.root / "pacman.conf"
        conf.write_text("[options]\n[multilib]\nInclude = /nonexistent/mirrorlist\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unusable"):
            setup.enable_multilib_and_optimizations(setup.SetupContext(), conf)

    def test_atomic_replacement_preserves_configuration_symlink(self):
        source, target, link = (self.root / name for name in ("source.conf", "target.conf", "linked.conf"))
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        link.symlink_to("target.conf")
        with patch.object(setup.subprocess, "run", side_effect=self.local_sudo):
            setup.install_configuration(setup.SetupContext(auto_yes=True), source, link, "fixture")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_text(encoding="utf-8"), "new")

    def test_all_interactive_profiles(self):
        keys = ("steam", "lutris", "wine", "protonup", "gamescope", "gamemode", "mangohud",
                "dwarfs", "dxvk", "vkd3d", "sysctl", "flatpak_bottles", "flatpak_heroic", "flatpak_pupgui")
        for choice in ("1", "3", "4", "5", "6"):
            with self.subTest(choice=choice), \
                 patch.object(setup, "check_system_installed_status", return_value=dict.fromkeys(keys, False)), \
                 patch.object(setup.Prompt, "ask", return_value=choice):
                if choice == "6":
                    with self.assertRaises(SystemExit) as exit_status:
                        setup.run_interactive_menu()
                    self.assertEqual(exit_status.exception.code, 0)
                else:
                    profile, modules = setup.run_interactive_menu()
                    self.assertIn(profile, {"full", "minimal", "perf_only", "maintenance"})
                    if choice in {"4", "5"}:
                        self.assertFalse(modules.gpu_drivers)
                        self.assertEqual(modules.dwarfs_mode, "skip")
                    if choice == "5":
                        self.assertFalse(modules.categories)
                        self.assertFalse(modules.sysctl_tuning)

    def test_custom_interactive_choices(self):
        keys = ("steam", "lutris", "wine", "protonup", "gamescope", "gamemode", "mangohud",
                "dwarfs", "dxvk", "vkd3d", "sysctl", "flatpak_bottles", "flatpak_heroic", "flatpak_pupgui")
        for installed in (False, True):
            for choice, expected in (("1", "bin"), ("2", "native"), ("3", "source")):
                with self.subTest(installed=installed, choice=choice), \
                     patch.object(setup, "check_system_installed_status", return_value=dict.fromkeys(keys, installed)), \
                     patch.object(setup.Prompt, "ask", side_effect=["2", choice]), \
                     patch.object(setup.Confirm, "ask", return_value=True):
                    _, modules = setup.run_interactive_menu()
                    self.assertEqual(modules.dwarfs_mode, expected)
                    self.assertEqual(modules.dwarfs_reinstall, installed)
            with self.subTest(installed=installed, decline=True), \
                 patch.object(setup, "check_system_installed_status", return_value=dict.fromkeys(keys, installed)), \
                 patch.object(setup.Prompt, "ask", return_value="2"), \
                 patch.object(setup.Confirm, "ask", return_value=False):
                _, modules = setup.run_interactive_menu()
                self.assertEqual(modules.dwarfs_mode, "skip")
                self.assertFalse(modules.categories)
                self.assertFalse(modules.flatpak_apps)

    def test_cpu_tiers_and_inconsistent_core_flags(self):
        v2 = {"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"}
        v3 = v2 | {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "movbe", "xsave", "abm"}
        v4 = v3 | {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
        for tier, flags in enumerate((set(), v2, v3, v4), start=1):
            data = "model name: Fixture CPU\nflags: " + " ".join(sorted(flags)) + "\n"
            with self.subTest(tier=tier), patch.object(Path, "read_text", return_value=data):
                self.assertEqual(setup.detect_cpu_info().x86_version, tier)
        data = "flags: " + " ".join(sorted(v4)) + "\nflags: " + " ".join(sorted(v2)) + "\n"
        with patch.object(Path, "read_text", return_value=data):
            self.assertEqual(setup.detect_cpu_info().x86_version, 2)
        with patch.object(Path, "read_text", side_effect=OSError("unavailable")):
            self.assertEqual(setup.detect_cpu_info().x86_version, 1)

    def test_nvidia_modesetting_permission_failure_is_not_inferred_as_success(self):
        for value, expected in (("Y", True), ("N", False)):
            with self.subTest(value=value), patch.object(Path, "read_text", side_effect=PermissionError), \
                 patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, value, "")):
                self.assertEqual(setup.nvidia_modeset_confirmed(), expected)
        with patch.object(Path, "read_text", side_effect=PermissionError), \
             patch.object(setup.subprocess, "run", side_effect=subprocess.TimeoutExpired("sudo", 5)):
            self.assertFalse(setup.nvidia_modeset_confirmed())
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            self.assertFalse(setup.nvidia_modeset_confirmed())

    def test_disabled_modules_have_no_side_effects(self):
        ctx = setup.SetupContext(modules=setup.SelectedModules(
            categories=set(), gpu_drivers=False, sysctl_tuning=False, dwarfs_mode="skip",
            protonup_mode="skip", flatpak_apps=False, launcher_bridge=False))
        with patch.object(setup.subprocess, "run", side_effect=AssertionError("Disabled step executed")):
            setup.select_gpu_packages(ctx)
            setup.apply_kernel_and_sysctl_optimizations(ctx)
            setup.configure_dwarfs(ctx)
            setup.configure_protonup(ctx)
            setup.configure_flatpak_ecosystem(ctx)
            setup.integrate_desktop_and_icons(ctx)
        self.assertFalse(ctx.failures)

    def test_optional_reporting_and_keepalive_failures_do_not_crash(self):
        with patch.object(sys, "stdout", io.StringIO()), \
             patch.object(setup.shutil, "which", return_value=None):
            setup.send_notification("fixture", "fixture")
        with patch.object(sys, "stdout", io.StringIO()), \
             patch.dict(os.environ, {"SUDO_UID": "12345"}, clear=True), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(setup.shutil, "which", return_value="/fixture/notify-send"), \
             patch.object(setup.subprocess, "run", side_effect=subprocess.TimeoutExpired("notify-send", 5)) as execute:
            setup.send_notification("fixture", "fixture")
        self.assertEqual(execute.call_args.kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/12345/bus")
        class StopAfterRefresh:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self, _seconds): self.stopped = True
        with patch.object(setup.subprocess, "run", side_effect=subprocess.TimeoutExpired("sudo", 5)) as execute:
            setup.keep_sudo_alive(StopAfterRefresh())
        self.assertEqual(execute.call_count, 1)

    def test_current_status_uses_actual_query_results(self):
        for value, expected in (("2097152", True), ("bad data", False)):
            with self.subTest(value=value), patch.object(setup.shutil, "which", return_value="/fixture"), \
                 patch.object(setup, "translation_libraries_ready", return_value={"dxvk-bin": True, "vkd3d-proton-bin": True}), \
                 patch.object(setup, "get_installed_flatpaks", return_value=["com.usebottles.bottles"]), \
                 patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, value, "")):
                status = setup.check_system_installed_status()
                self.assertEqual(status["sysctl"], expected)
                self.assertTrue(status["flatpak_bottles"])
                self.assertFalse(status["flatpak_pupgui"])

    def test_flatpak_metadata_errors_are_reported(self):
        ctx = setup.SetupContext()
        with patch.object(setup.subprocess, "run", side_effect=subprocess.TimeoutExpired("flatpak", 15)):
            self.assertFalse(setup.flatpak_layer_branches(ctx, "--user", {"org.gnome.Platform//50"}))
        self.assertTrue(ctx.failures)
        with self.assertRaises(ValueError):
            setup.get_flatpak_inventory("--invalid-kind")

    def test_root_execution_is_rejected(self):
        with patch.object(setup.os, "geteuid", return_value=0):
            with self.assertRaises(SystemExit) as exit_status:
                setup.check_root_and_locks(setup.SetupContext())
        self.assertEqual(exit_status.exception.code, 1)

    def test_command_optional_decline_and_environment_override(self):
        ctx = setup.SetupContext()
        with patch.object(setup.Confirm, "ask", return_value=False), \
             patch.object(setup.subprocess, "run", side_effect=AssertionError("Declined step ran")):
            self.assertFalse(setup.run_command(ctx, ["fixture"], "declined", critical=False))
        self.assertTrue(ctx.failures)
        with patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as execute:
            self.assertTrue(setup.run_command(setup.SetupContext(auto_yes=True), ["fixture"], "environment", extra_env={"GAMING_AUDIT_FIXTURE": "1"}))
        self.assertEqual(execute.call_args.kwargs["env"]["GAMING_AUDIT_FIXTURE"], "1")
        self.assertNotIn("GAMING_AUDIT_FIXTURE", os.environ)

    def test_real_cli_help_and_invalid_flag_combination(self):
        script = Path(setup.__file__)
        help_result = self.real_run([sys.executable, str(script), "--help"], capture_output=True,
                                    encoding="utf-8", timeout=15)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        invalid = self.real_run([sys.executable, str(script), "--bin", "--native"], capture_output=True,
                                encoding="utf-8", timeout=15)
        self.assertEqual(invalid.returncode, 2)

    def test_real_cli_reports_read_failure_and_interruption_without_traceback(self):
        # Only the read-only pacman dependency query is replaced; dry-run forbids writes.
        executable = self.root / "pacman"
        for interrupt, expected in ((False, 1), (True, 130)):
            body = ("import os,signal; os.kill(os.getppid(),signal.SIGINT)" if interrupt
                    else "raise SystemExit(1)")
            executable.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
            executable.chmod(0o755)
            env = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", "")}
            result = self.real_run([sys.executable, str(Path(setup.__file__)), "--yes", "--dry-run"],
                                   env=env, capture_output=True, encoding="utf-8", timeout=20)
            with self.subTest(interrupt=interrupt):
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_missing_rich_reports_dependency(self):
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        result = self.real_run([sys.executable, "-S", str(Path(setup.__file__)), "--help"],
                               env=env, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn("python-rich", result.stderr)


if __name__ == "__main__":
    unittest.main()
