"""Disposable regression checks for the runner's destructive failure paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "runner"))
import gaming_setup as setup
import master_runner as runner


class ReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="runner-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        runtime_patch = mock.patch.object(runner, "RUNTIME_DIR", self.root / "runtime")
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def profile(self, extra: str = "", *, executable: bool = True) -> tuple[runner.ProfileManager, runner.Profile, Path]:
        (self.root / "profiles").mkdir(exist_ok=True)
        game = self.root / "game"
        game.mkdir(exist_ok=True)
        exe = game / "start.sh"
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o700 if executable else 0o600)
        (self.root / "profiles/test.toml").write_text(
            f'schema = 3\n[paths]\ngame_dir = "{game}"\nexecutable = "start.sh"\n{extra}'
        )
        mgr = runner.ProfileManager(self.root)
        return mgr, mgr.load("test"), exe

    def test_schema_and_boolean_types(self) -> None:
        for bad in ('schema = 999\n', 'schema = 3\n[meta]\nenabled = "false"\n',
                    'schema = 3\n[runtime]\ntype = "nonsense"\n',
                    'schema = 3\n[paths]\ngame_dir = "/tmp"\nextraneous = true\n',
                    'schema = 3\n[graphics.gamescope]\nallow_tearing = true\n'):
            with self.subTest(bad=bad):
                (self.root / "profiles").mkdir(exist_ok=True)
                (self.root / "profiles/test.toml").write_text(bad)
                with self.assertRaises(runner.ConfigError):
                    runner.ProfileManager(self.root).load("test")

    def test_dry_run_preserves_executable_and_sandbox_home(self) -> None:
        mgr, prof, exe = self.profile(
            f'[sandbox]\nenabled = true\nsandbox_home = "{self.root}/sandbox-home"\n',
            executable=False,
        )
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        mode = exe.stat().st_mode
        self.assertEqual(runner.GameSession(mgr, prof, runner.RunOptions(dry_run=True)).run(), 0)
        self.assertEqual(exe.stat().st_mode, mode)
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")), before)

    def test_cli_dry_run_does_not_create_state_directories(self) -> None:
        mgr, _, _ = self.profile()
        state = self.root / "new-state"
        cache = self.root / "new-cache"
        runtime = self.root / "new-runtime"
        with mock.patch.object(runner, "STATE_DIR", state), \
             mock.patch.object(runner, "CACHE_DIR", cache), \
             mock.patch.object(runner, "RUNTIME_DIR", runtime):
            self.assertEqual(runner.main(["--root", str(mgr.root), "run", "test", "-n"]), 0)
        self.assertFalse(state.exists())
        self.assertFalse(cache.exists())
        self.assertFalse(runtime.exists())

    def test_xwayland_requires_a_real_host_display(self) -> None:
        _, prof, _ = self.profile('[graphics]\nprefer_xwayland = true\n')
        with mock.patch.dict(os.environ, {"DISPLAY": ""}), \
             mock.patch.object(runner, "run_cmd", return_value=runner.Ran(1, "", "no user manager")):
            builder = runner.EnvironmentBuilder(prof, runner.resolve_paths(prof), dry_run=True)
            with self.assertRaisesRegex(runner.ConfigError, "DISPLAY is unset"):
                builder.build(under_gamescope=False)

    def test_native_wayland_uses_single_sdl_backend(self) -> None:
        _, prof, _ = self.profile('[graphics]\nwayland_native = true\n')
        builder = runner.EnvironmentBuilder(prof, runner.resolve_paths(prof), dry_run=True)
        env = builder.build(under_gamescope=False)
        self.assertEqual(env["SDL_VIDEODRIVER"], "wayland")

    def test_default_audio_does_not_force_unavailable_sdl_client(self) -> None:
        _, prof, _ = self.profile()
        with mock.patch.dict(os.environ, {}, clear=True):
            builder = runner.EnvironmentBuilder(prof, runner.resolve_paths(prof), dry_run=True)
            env = builder.build(under_gamescope=False)
        self.assertNotIn("SDL_AUDIODRIVER", env)
        self.assertNotIn("ALSOFT_DRIVERS", env)

    def test_xwayland_uses_published_display_after_compositor_reload(self) -> None:
        _, prof, _ = self.profile('[graphics]\nprefer_xwayland = true\n')
        with mock.patch.dict(os.environ, {"DISPLAY": ""}), \
             mock.patch.object(runner, "run_cmd", return_value=runner.Ran(0, "DISPLAY=:47\n", "")), \
             mock.patch.object(Path, "exists", return_value=True):
            builder = runner.EnvironmentBuilder(prof, runner.resolve_paths(prof), dry_run=True)
            env = builder.build(under_gamescope=False)
        self.assertEqual(env["DISPLAY"], ":47")

    def test_explicit_non_wayland_wine_selects_x11_driver(self) -> None:
        _, prof, _ = self.profile(
            f'[runtime]\ntype = "wine"\n[runtime.wine]\n'
            f'prefix_dir = "{self.root}/prefix"\ndxvk = false\nvkd3d = false\n'
            '[graphics]\nwayland_native = false\nshader_cache = false\n'
        )
        with mock.patch.object(runner, "host_x11_display", return_value=":0"), \
             mock.patch.object(runner.WinePrefix, "provision"), \
             mock.patch.object(runner.WinePrefix, "set_graphics_driver") as select:
            builder = runner.EnvironmentBuilder(prof, runner.resolve_paths(prof), dry_run=False)
            env = builder.build(under_gamescope=False)
        self.assertEqual(env["DISPLAY"], ":0")
        select.assert_called_once_with("x11", mock.ANY)

    def test_overlay_work_cannot_be_game_directory(self) -> None:
        _, prof, _ = self.profile('dwarfs_image = "game.dwarfs"\noverlay_work = "."\n')
        (self.root / "game/game.dwarfs").write_bytes(b"image")
        with self.assertRaises(runner.ConfigError):
            runner.resolve_paths(prof)
        self.assertTrue((self.root / "game/start.sh").exists())

    def test_workdir_cleanup_requires_ownership_marker(self) -> None:
        work = self.root / "game/.game-work"
        work.mkdir(parents=True)
        save = work / "save.dat"
        save.write_bytes(b"keep")
        with self.assertRaises(runner.ConfigError):
            runner.MountEngine._claim_workdir(work)
        runner.MountEngine._purge_workdir(work)
        self.assertEqual(save.read_bytes(), b"keep")
        save.unlink()
        runner.MountEngine._claim_workdir(work)
        save.write_bytes(b"disposable")
        runner.MountEngine._purge_workdir(work)
        self.assertFalse(save.exists())
        self.assertTrue(runner.MountEngine._work_marker(work).is_file())

    def test_overlay_work_rejects_symlink_parent_and_external_path(self) -> None:
        _, prof, _ = self.profile('dwarfs_image = "game.dwarfs"\noverlay_work = "alias/.game-work"\n')
        (self.root / "game/game.dwarfs").write_bytes(b"image")
        (self.root / "game/alias").symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaises(runner.ConfigError):
            runner.resolve_paths(prof)
        prof.cfg["paths"]["overlay_work"] = str(self.root / "elsewhere/.game-work")
        with self.assertRaises(runner.ConfigError):
            runner.resolve_paths(prof)

    def test_timeout_kills_descendants(self) -> None:
        pidfile = self.root / "child.pid"
        script = ("import subprocess,sys,time; "
                  "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']); "
                  f"open({str(pidfile)!r},'w').write(str(c.pid));time.sleep(10)")
        result = runner.run_cmd([sys.executable, "-c", script], timeout=0.2)
        self.assertEqual(result.rc, 124)
        self.assertFalse(runner._pid_running(int(pidfile.read_text())))

    def test_interrupt_kills_helper_descendants(self) -> None:
        pidfile = self.root / "helper-child.pid"
        helper = ("import subprocess,sys,time; "
                  "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']); "
                  f"open({str(pidfile)!r},'w').write(str(c.pid));time.sleep(20)")
        launcher = ("import sys;sys.path.insert(0,'runner');import master_runner as r;"
                    f"r.run_cmd([sys.executable,'-c',{helper!r}],timeout=30)")
        proc = subprocess.Popen([sys.executable, "-c", launcher], cwd=PROJECT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pidfile.exists())
            proc.send_signal(signal.SIGINT)
            proc.communicate(timeout=8)
            self.assertFalse(runner._pid_running(int(pidfile.read_text())))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)

    def test_supervisor_waits_for_descendant(self) -> None:
        script = ("import subprocess,sys; "
                  "subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.4)'])")
        proc = subprocess.Popen([sys.executable, "-c", script], process_group=0)
        start = time.monotonic()
        self.assertEqual(runner.Supervisor(proc).wait(), 0)
        self.assertGreaterEqual(time.monotonic() - start, 0.3)

    def test_interrupt_during_setup_is_recorded(self) -> None:
        mgr, prof, _ = self.profile()
        records: list[dict[str, object]] = []
        with mock.patch.object(runner.MountEngine, "mount", side_effect=KeyboardInterrupt), \
             mock.patch.object(runner, "_append_session_record", side_effect=records.append):
            with self.assertRaises(KeyboardInterrupt):
                runner.GameSession(mgr, prof, runner.RunOptions()).run()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["stage"], "mount")
        self.assertEqual(records[0]["rc"], 130)

    def test_gpu_role_uses_vendor_not_boot_flag(self) -> None:
        def gpu(vendor: int, boot: bool, slot: str) -> runner.Gpu:
            driver = "i915" if vendor == 0x8086 else "nvidia"
            return runner.Gpu("card0", "", "", slot, vendor, 1, "", driver, boot,
                              "", "GPU")
        intel = gpu(0x8086, False, "0000:00:02.0")
        nvidia = gpu(0x10DE, True, "0000:01:00.0")
        with mock.patch.object(runner, "gpus", return_value=(nvidia, intel)):
            self.assertIs(runner.select_gpu("integrated"), intel)
            self.assertIs(runner.select_gpu("discrete"), nvidia)
            self.assertIs(runner.select_gpu("primary"), nvidia)

    def test_doctor_fix_keeps_higher_existing_map_limit(self) -> None:
        with mock.patch.object(runner, "sysctl_read", return_value="2147483642"), \
             mock.patch.object(runner, "run_cmd") as execute, \
             mock.patch.object(runner, "collect_checks", return_value=[]):
            self.assertEqual(runner.doctor(fix=True, as_json=True), 0)
        execute.assert_not_called()

    def test_failed_prefix_has_no_success_stamp(self) -> None:
        prefix = runner.WinePrefix(self.root / "prefix", "no-such-wine-executable")
        with self.assertRaises(runner.ConfigError):
            prefix.provision(root_dir=self.root, redistributables=[], winetricks=[],
                             want_dxvk=False, want_vkd3d=False, want_nvapi=False,
                             want_dlss=False, force=False, dry_run=False)
        self.assertFalse(prefix.stamp.exists())

    def test_failed_translator_link_does_not_stamp_prefix(self) -> None:
        prefix = runner.WinePrefix(self.root / "prefix")
        prefix.pfx.mkdir(parents=True)
        for reg in ("system.reg", "user.reg"):
            (prefix.pfx / reg).write_text("ready")
        with mock.patch.object(runner.WinePrefix, "prune_broken_symlinks", return_value=0), \
             mock.patch.object(runner.WinePrefix, "unify_user_dirs"), \
             mock.patch.object(runner.WinePrefix, "suppress_crash_dialogs"), \
             mock.patch.object(runner.WinePrefix, "clean_stale_crash_markers"), \
             mock.patch.object(runner.WinePrefix, "serverwait"), \
             mock.patch.object(runner.WinePrefix, "link_translators", side_effect=runner.ConfigError("missing DLL")):
            with self.assertRaises(runner.ConfigError):
                prefix.provision(root_dir=self.root, redistributables=[], winetricks=[],
                                 want_dxvk=True, want_vkd3d=False, force=False)
        self.assertFalse(prefix.stamp.exists())

    def test_umu_launch_keeps_proton_prefix_management_separate(self) -> None:
        launcher = self.root / "umu-run"
        observed = self.root / "umu-observed.json"
        launcher.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            f"open({str(observed)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], "
            "'gameid': os.environ.get('GAMEID'), 'prefix': os.environ.get('WINEPREFIX')}))\n"
        )
        launcher.chmod(0o700)
        prefix = self.root / "umu-prefix"
        extra = (
            f'[runtime]\ntype = "umu"\n[runtime.wine]\nwine_binary = "{launcher}"\n'
            f'prefix_dir = "{prefix}"\n[runtime.umu]\ngame_id = "umu-test"\n'
            '[runner]\nuse_systemd_scope = false\ninhibit_idle = false\n'
            'notifications = false\nenable_io_shim = false\n'
            '[performance]\ngamemode = false\n[graphics]\nshader_cache = false\n'
        )
        mgr, prof, _ = self.profile(extra)
        state_patch = mock.patch.object(runner, "STATE_DIR", self.root / "state")
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.assertEqual(runner.GameSession(mgr, prof, runner.RunOptions()).run(), 0)
        payload = json.loads(observed.read_text())
        self.assertEqual(payload["gameid"], "umu-test")
        self.assertEqual(payload["prefix"], str(prefix))
        self.assertFalse(prefix.exists())

    def test_dll_is_restored_after_translator_disable(self) -> None:
        prefix_path = self.root / "prefix"
        sys32 = prefix_path / "drive_c/windows/system32"
        sys32.mkdir(parents=True)
        dll = sys32 / "d3d11.dll"
        dll.write_bytes(b"original")
        prefix = runner.WinePrefix(prefix_path)
        prefix.link_translators(want_dxvk=True, want_vkd3d=False)
        self.assertTrue(dll.is_symlink())
        self.assertEqual((sys32 / "d3d11.dll.master-runner-orig").read_bytes(), b"original")
        prefix.link_translators(want_dxvk=False, want_vkd3d=False)
        self.assertFalse(dll.is_symlink())
        self.assertEqual(dll.read_bytes(), b"original")

    def test_config_patch_rejects_bad_json_and_backs_up_valid_file(self) -> None:
        target = self.root / "settings.json"
        target.write_text("{broken")
        with self.assertRaises(runner.ConfigError):
            runner.ConfigPatcher._patch_json(target, {"Width": "1920"}, {}, dry_run=False)
        self.assertEqual(target.read_text(), "{broken")
        target.write_text('{"Width": 800}\n')
        runner.ConfigPatcher._patch_json(target, {"Width": "1920"}, {}, dry_run=False)
        self.assertEqual(json.loads(target.read_text())["Width"], 1920)
        self.assertEqual(json.loads((self.root / "settings.json.master-runner.bak").read_text())["Width"], 800)

    def test_ini_key_case_is_preserved(self) -> None:
        target = self.root / "settings.ini"
        target.write_text("[Video]\nResolutionWidth = 800\nPercent = 100%\n")
        runner.ConfigPatcher._patch_ini(target, {"Video.ResolutionWidth": "1920"}, {}, dry_run=False)
        self.assertIn("ResolutionWidth = 1920", target.read_text())
        self.assertIn("Percent = 100%", target.read_text())

    def test_shim_default_preserves_writes_and_inode_identity(self) -> None:
        if not runner.have("gcc"):
            self.skipTest("gcc unavailable")
        shim = self.root / "shim.so"
        subprocess.run(["gcc", "-O2", "-fPIC", "-shared", "-Wall", "-Wextra",
                        str(PROJECT / "runner/lib/runner_shim.c"), "-o", str(shim), "-ldl"],
                       check=True, capture_output=True)
        asset = self.root / "asset.pak"
        asset.write_bytes(b"ab")
        code = ("import os,sys; f=os.open(sys.argv[1],os.O_RDWR); "
                "a=os.fstat(f).st_ino; b=os.fstat(f).st_ino; "
                "os.write(f,b'Z'); print(a==b); os.close(f)")
        env = dict(os.environ, LD_PRELOAD=str(shim))
        env.pop("MASTER_RUNNER_SHIM_READONLY_ASSETS", None)
        env.pop("MASTER_RUNNER_SHIM_MONO_INODES", None)
        cp = subprocess.run([sys.executable, "-c", code, str(asset)], env=env,
                            capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), "True")
        self.assertEqual(asset.read_bytes(), b"Zb")
        probe = self.root / "tmpfile-probe"
        source = ("#define _GNU_SOURCE\n#include <fcntl.h>\n#include <stdio.h>\n"
                  "#include <sys/stat.h>\n#include <unistd.h>\n"
                  "int main(void){int fd=open(\"/tmp\",O_TMPFILE|O_RDWR,0600);"
                  "if(fd<0)return 2;struct stat st;if(fstat(fd,&st))return 3;"
                  "printf(\"%03o\\n\",st.st_mode&0777);close(fd);return 0;}\n")
        subprocess.run(["gcc", "-x", "c", "-", "-o", str(probe)], input=source,
                       text=True, capture_output=True, check=True)
        baseline = subprocess.run([str(probe)], capture_output=True, text=True)
        if baseline.returncode == 0:
            env["MASTER_RUNNER_SHIM_READONLY_ASSETS"] = "1"
            with_shim = subprocess.run([str(probe)], env=env, capture_output=True, text=True)
            self.assertEqual(with_shim.returncode, 0, with_shim.stderr)
            self.assertEqual(with_shim.stdout, baseline.stdout)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="setup-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_installer_command_uses_argument_vector(self) -> None:
        ctx = setup.SetupContext(auto_yes=True)
        argv = ["true", "safe; printf INJECTED"]
        with mock.patch.object(setup.subprocess, "run", return_value=mock.Mock(returncode=0)) as call:
            self.assertTrue(setup.run_command(ctx, argv, "fixture"))
        self.assertEqual(call.call_args.args[0], argv)
        self.assertNotIn("shell", call.call_args.kwargs)
        with self.assertRaises(TypeError):
            setup.run_command(ctx, "true; printf INJECTED", "rejected")

    def test_multilib_repairs_partial_section_once(self) -> None:
        conf = self.root / "pacman.conf"
        conf.write_text("[options]\nColor\nDisableDownloadTimeout\n\n[multilib]\n#Include = /etc/pacman.d/mirrorlist\n")
        ctx = setup.SetupContext(auto_yes=True)

        def install_command(_ctx, argv, _description, **_kwargs):
            if isinstance(argv, list) and len(argv) > 2 and argv[1] == "install":
                conf.write_text(Path(argv[-2]).read_text())
            return True

        with mock.patch.object(setup, "run_command", side_effect=install_command):
            self.assertTrue(setup.enable_multilib_and_optimizations(ctx, conf))
            self.assertIn("\nInclude = /etc/pacman.d/mirrorlist\n", conf.read_text())
            self.assertFalse(setup.enable_multilib_and_optimizations(ctx, conf))

    def test_native_dwarfs_build_uses_makepkg_config(self) -> None:
        ctx = setup.SetupContext(auto_yes=True)
        ctx.modules.dwarfs_mode = "native"
        seen = []

        def inspect_command(_ctx, argv, _description, **_kwargs):
            seen.append(argv)
            self.assertIn("--rebuild", argv)
            if "--makepkgconf" in argv:
                conf = Path(argv[argv.index("--makepkgconf") + 1])
            else:
                conf = Path(argv[argv.index("--mflags") + 1].split()[-1])
            self.assertIn('-march=native -O3', conf.read_text())
            result = subprocess.run(["bash", "-c", 'source "$1"; printf "%s\\n" "$CFLAGS" "$CXXFLAGS"',
                                     "bash", str(conf)], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.count("-march=native -O3"), 2)
            return True

        with mock.patch.object(setup, "run_command", side_effect=inspect_command):
            setup.configure_dwarfs(ctx)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
