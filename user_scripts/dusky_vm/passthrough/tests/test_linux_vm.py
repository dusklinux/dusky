"""Focused regressions; run with Python 3.15 and TMPDIR on a RAM filesystem."""

import ast
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux import deploy_vm as vm
from clipboard import wayland_clipboard as clipboard


class LinuxVMTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_iso_discovery_ignores_date_format_and_orders_by_mtime(self):
        for name in ("dusky_10_26.iso", "dusky_02_27.iso", "DUSKY-next.ISO", "other.iso", "dusky.txt"):
            (self.root / name).touch()
        os.utime(self.root / "dusky_02_27.iso", ns=(1_000_000_000, 1_000_000_000))
        os.utime(self.root / "dusky_10_26.iso", ns=(2_000_000_000, 2_000_000_000))
        os.utime(self.root / "DUSKY-next.ISO", ns=(3_000_000_000, 3_000_000_000))
        self.assertEqual([p.name for p in vm.discover_isos(self.root)],
                         ["DUSKY-next.ISO", "dusky_10_26.iso", "dusky_02_27.iso"])

    def test_ambiguous_unattended_iso_does_not_silently_pick(self):
        with patch.object(vm, "discover_isos", return_value=[Path("a.iso"), Path("b.iso")]):
            with self.assertRaisesRegex(RuntimeError, "Multiple ISOs"):
                vm.ask_iso(None, self.root, True)

    def test_ram_storage_budget_counts_guest_and_host_memory(self):
        with patch.object(vm, "filesystem_type", return_value="tmpfs"), \
                patch.object(vm.shutil, "disk_usage") as usage:
            usage.return_value.free = 100 * vm.GIB
            self.assertTrue(vm.ram_storage_reason(self.root, 4096, 24 * vm.GIB))
            self.assertTrue(vm.ram_storage_reason(self.root, 4096, 27 * vm.GIB))
            self.assertFalse(vm.ram_storage_reason(self.root, 4096, 28 * vm.GIB))
            usage.return_value.free = 19 * vm.GIB
            self.assertTrue(vm.ram_storage_reason(self.root, 4096, 64 * vm.GIB))

    def test_persistent_storage_is_enter_default(self):
        with patch("builtins.input", return_value=""), \
                patch.object(vm, "filesystem_type", return_value="ext4"), \
                patch.object(vm, "ram_storage_reason", return_value=""):
            self.assertEqual(vm.choose_storage(None, 4096, False), vm.PERSISTENT_STORAGE)

    def test_operator_home_expansion_survives_sudo(self):
        self.assertEqual(vm.expand_path("~/VMs", self.root), self.root / "VMs")
        self.assertEqual(vm.expand_path("~", self.root), self.root)

    def test_default_share_is_ram_backed_independently_of_disk_storage(self):
        with patch.object(vm, "RAM_STORAGE", self.root), \
                patch.object(vm, "filesystem_type", return_value="tmpfs"), \
                patch("builtins.input", return_value=""):
            self.assertEqual(vm.choose_share(None, self.root, False), self.root)
            self.assertEqual(vm.choose_share(None, self.root, True), self.root)

    def test_default_share_does_not_fall_back_to_persistent_storage(self):
        with patch.object(vm, "RAM_STORAGE", self.root), \
                patch.object(vm, "filesystem_type", return_value="ext4"):
            with self.assertRaisesRegex(RuntimeError, "not RAM-backed"):
                vm.choose_share(None, self.root, True)

    def test_share_override_can_select_another_directory(self):
        self.assertEqual(vm.choose_share("~/custom-share", self.root, True), self.root / "custom-share")

    def test_existing_shared_directory_is_not_chowned_or_repermissioned(self):
        with patch.object(vm.os, "chown") as chown, patch.object(vm, "run") as execute:
            vm.prepare_share(self.root, vm.operator())
            chown.assert_not_called()
            execute.assert_not_called()

    def test_backend_launcher_discovers_path_and_preserves_arguments(self):
        package = self.root / "package with spaces"
        package.mkdir()
        daemon = package / "virtiofsd"
        daemon.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        daemon.chmod(0o755)
        assets = self.root / "assets"
        assets.mkdir()
        result = subprocess.CompletedProcess([], 0, str(daemon) + "\n", "")
        with patch.object(vm, "run", return_value=result):
            vm.prepare_backend(assets)
        output = subprocess.run([str(assets / "virtiofsd"), "--print-capabilities"],
                                capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(output, ["--inode-file-handles=never", "--print-capabilities"])

    def test_boot_installed_accepts_an_already_empty_cdrom(self):
        xml = '<domain><devices><disk device="cdrom"><target dev="sda"/></disk></devices></domain>'
        def call(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, xml if args[0] == "dumpxml" else "", "")
        with patch.object(vm, "domain_config"), patch.object(vm, "virsh", side_effect=call) as execute:
            vm.boot_installed("test", 10)
        self.assertFalse(any(c.args[0] == "change-media" for c in execute.call_args_list))
        self.assertEqual(execute.call_args_list[-1].args, ("start", "test"))

    def test_paths_with_commas_and_xml_characters_survive_xml(self):
        spec = vm.Spec("test", self.root / "dusky,a&b.iso", self.root,
                       self.root / "share,a&b", 4096, 4, "default", None)
        baseline = '<domain type="kvm"><os firmware="efi"/><devices/><metadata/></domain>'
        with patch.object(vm, "filesystem_type", return_value="tmpfs"):
            root = ET.fromstring(vm.compose_xml(spec, baseline))
        self.assertEqual(root.find("devices/disk/source").get("file"), str(spec.disk))
        self.assertEqual(root.findall("devices/disk/source")[1].get("file"), str(spec.iso))
        self.assertEqual(root.find("devices/filesystem/source").get("dir"), str(spec.share))
        self.assertEqual(root.find("devices/filesystem/binary/cache").get("mode"), "none")
        self.assertEqual(root.find("devices/filesystem/binary").get("path"), str(spec.assets / "virtiofsd"))
        self.assertEqual(root.find("memoryBacking/source").get("type"), "memfd")
        self.assertEqual(root.find("devices/disk/driver").get("cache"), "writeback")
        with patch.object(vm, "filesystem_type", return_value="ext4"):
            persistent = ET.fromstring(vm.compose_xml(spec, baseline))
        self.assertEqual(persistent.find("devices/disk/driver").get("cache"), "none")

    def test_existing_image_is_not_overwritten(self):
        spec = vm.Spec("existing", self.root / "dusky.iso", self.root,
                       self.root / "shared", 4096, 4, "default", None)
        spec.disk.write_bytes(b"existing VM disk")
        with patch.object(vm, "run") as execute:
            with self.assertRaisesRegex(RuntimeError, "No files overwritten"):
                vm.prepare_files(spec, None)
            execute.assert_not_called()
        self.assertEqual(spec.disk.read_bytes(), b"existing VM disk")

    def test_wayland_discovery_ignores_wallpaper_daemon_sockets(self):
        for name in ("wayland-1", "wayland-1-awww-daemon.sock"):
            connection = socket.socket(socket.AF_UNIX)
            self.addCleanup(connection.close)
            connection.bind(str(self.root / name))
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.root), "WAYLAND_DISPLAY": ""}):
            env = clipboard.session_environment(False)
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-1")

    def test_queued_clipboard_event_cannot_carry_stale_text(self):
        class Input:
            buffer = io.BytesIO(b"stale echoed clipboard")
        output = io.StringIO()
        with patch.object(clipboard.sys, "stdin", Input()), \
                patch.object(clipboard.sys, "stdout", output), \
                patch.object(clipboard.subprocess, "run") as read_selection, \
                patch.dict(os.environ, {"CLIPBOARD_STATE": "data"}):
            clipboard.emit()
        self.assertEqual(json.loads(output.getvalue()), {"changed": True})
        read_selection.assert_not_called()
        self.assertEqual(Input.buffer.read(), b"")

    def test_guest_scripts_and_embedded_handoff_compile(self):
        bootstrap = vm.bootstrap_script("ssh-ed25519 fixture", None)
        self.assertNotIn("/mnt/host", bootstrap)
        compile(bootstrap, "guest-setup", "exec")
        handoff = vm.handoff_script("ssh-ed25519 fixture", None)
        compile(handoff, "handoff", "exec")
        # Check nested generated Python as well as the outer writer.
        strings = [n.value for n in ast.walk(ast.parse(handoff))
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        watcher = next(s for s in strings if "deadline = time.monotonic()" in s)
        compile(watcher, "watcher", "exec")
        nested = [n.value for n in ast.walk(ast.parse(watcher))
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        firstboot = next(s for s in nested if "qemu-guest-agent missing" in s)
        compile(firstboot, "firstboot", "exec")

    def test_daemon_configuration_keeps_one_active_setting(self):
        path = vm.ROOT / "10_virt_modular_daemon.py"
        tree = ast.parse(path.read_text())
        helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "enforce_kv_config")
        writes = []
        class Console:
            def print(self, *args): pass
        ns = {"Path": Path, "re": re, "console": Console(),
              "atomic_write": lambda path, content: writes.append(content) or True}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), ns)
        conf = self.root / "daemon.conf"
        conf.write_text('#auth_unix_rw = "none"\nauth_unix_rw = "none"\nkeep = 1\n')
        ns["enforce_kv_config"](conf, {"auth_unix_rw": '"polkit"'}, "test")
        self.assertEqual(writes[0].count('auth_unix_rw = "polkit"'), 1)
        self.assertIn("keep = 1", writes[0])


if __name__ == "__main__":
    unittest.main()
