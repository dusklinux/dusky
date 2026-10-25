"""Health reporting, bounded repair and repeated convergence regressions."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import pwd
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import xml.etree.ElementTree as ET


BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


doctor = load("doctor_tests", BASE / "doctor.py")
config = load("doctor_config_tests", BASE / "60_configure_client_ini.py")
phase = load("doctor_memory_tests", BASE / "25_looking_glass.py")


class DoctorTests(unittest.TestCase):
    def test_looking_glass_preserves_explicit_balloon_choice(self):
        for model in ('virtio', 'none'):
            root = ET.fromstring(f"<domain><devices><memballoon model='{model}'><stats period='5'/></memballoon></devices></domain>")
            before = ET.tostring(root.find('./devices/memballoon'))
            phase.apply_latency_tuning(root)
            self.assertEqual(ET.tostring(root.find('./devices/memballoon')), before)
        root = ET.fromstring('<domain><devices/></domain>')
        phase.apply_latency_tuning(root)
        self.assertEqual(root.find('./devices/memballoon').get('model'), 'none')

    def test_recorded_access_wins_over_device_inference(self):
        root = ET.fromstring("<domain><metadata><d:windows xmlns:d='urn:dusky:vm:windows:1' access='rdp'/></metadata><devices><shmem name='looking-glass'/></devices></domain>")
        self.assertEqual(doctor.access_method(root), "rdp")
        self.assertEqual(doctor.access_method(root, "both"), "both")

    def test_old_domains_do_not_imply_rdp_consent(self):
        root = ET.fromstring("<domain><devices><shmem name='looking-glass'/></devices></domain>")
        self.assertEqual(doctor.access_method(root), "looking-glass")
        self.assertEqual(doctor.access_method(ET.fromstring("<domain><devices/></domain>")), "spice")

    def test_channel_repairs_converge_without_changing_cpu_or_gpu(self):
        root = ET.fromstring("<domain><cpu mode='host-passthrough'><topology sockets='1' cores='4' threads='2'/></cpu><devices><hostdev type='pci'><source><address domain='0' bus='1' slot='0' function='0'/></source></hostdev><graphics type='spice'/></devices></domain>")
        before = (ET.tostring(root.find("cpu")), ET.tostring(root.find("./devices/hostdev")))
        self.assertTrue(doctor.repair_channels(root))
        for _ in range(1000):
            self.assertFalse(doctor.repair_channels(root))
        self.assertEqual(len(root.findall("./devices/channel")), 2)
        self.assertEqual(before, (ET.tostring(root.find("cpu")), ET.tostring(root.find("./devices/hostdev"))))

    def test_live_and_persistent_changes_are_distinguishable(self):
        root = ET.fromstring("<domain><devices><shmem name='looking-glass'><model type='ivshmem-plain'/><size unit='M'>64</size></shmem></devices></domain>")
        before = doctor.managed_devices(root)
        doctor.repair_channels(root)
        self.assertNotEqual(before, doctor.managed_devices(root))
        self.assertEqual(doctor.shm_size(root), 64 << 20)

    def test_bad_memory_sizes_are_not_silently_accepted(self):
        for unit, size in (("M", "96"), ("invalid", "64"), ("M", "-1")):
            with self.subTest(unit=unit, size=size):
                root = ET.fromstring(f"<domain><devices><shmem name='looking-glass'><size unit='{unit}'>{size}</size></shmem></devices></domain>")
                with self.assertRaises(ValueError):
                    doctor.shm_size(root)

    def test_unverified_checks_cannot_return_success(self):
        for states, expected in ((["ok"], 0), (["ok", "optional"], 0), (["ok", "pending"], 2), (["pending", "issue"], 1)):
            self.assertEqual(doctor.exit_code({"checks": [{"status": state} for state in states]}), expected)

    def test_config_merge_converges_over_5000_reruns(self):
        content = "; keep\n[win]\nautoResize=yes\n[wayland]\nfractionScale=no\n"
        expected = config.merge_config(content, "/dev/shm/looking-glass")
        for _ in range(5000):
            content = config.merge_config(content, "/dev/shm/looking-glass")
            self.assertEqual(content, expected)
        self.assertIn("fractionScale=no", content)

    def test_concurrent_atomic_config_writes_are_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.ini"
            content = config.merge_config("", "/dev/shm/looking-glass")
            def write(_):
                config.atomic_write(path, content, os.getuid(), os.getgid())
                self.assertEqual(path.read_text(encoding="utf-8"), content)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(write, range(100)))
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_guest_agent_failures_are_reported(self):
        with patch.object(doctor, "virsh", return_value='{"error":{"desc":"missing guest agent"}}'):
            with self.assertRaisesRegex(RuntimeError, "missing guest agent"):
                doctor.guest_agent("vm", {"execute": "guest-ping"})

    def test_active_mapping_resize_and_forced_recreation_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping"
            path.write_bytes(b"sentinel")
            inode = path.stat().st_ino
            def query(args, **kwargs):
                output = "busy-vm" if args[0] == "list" else "<domain><devices><shmem name='looking-glass'/></devices></domain>"
                return phase.Cmd(args, 0, output, "")
            with (patch.object(phase, "SHM_PATH", path), patch.object(phase, "virsh", side_effect=query),
                  patch.object(phase, "console"), patch.object(phase.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid()))):
                for size, force in ((1024, False), (8, True)):
                    with self.assertRaises(SystemExit):
                        phase.stage_shm(pwd.getpwuid(os.getuid()), size, force)
                    self.assertEqual(path.stat().st_ino, inode)
                    self.assertEqual(path.read_bytes(), b"sentinel")

    def test_memory_permission_repair_preserves_inode_across_100_reruns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping"
            path.write_bytes(b"sentinel")
            inode = path.stat().st_ino
            with (patch.object(phase, "SHM_PATH", path), patch.object(phase, "console"),
                  patch.object(phase.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid()))):
                for _ in range(100):
                    path.chmod(0o600)
                    phase.stage_shm(pwd.getpwuid(os.getuid()), 8, False)
                    self.assertEqual(path.stat().st_ino, inode)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o660)
                    self.assertEqual(path.read_bytes(), b"sentinel")

    def test_failed_allocation_leaves_no_partial_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping"
            with (patch.object(phase, "SHM_PATH", path), patch.object(phase, "console"),
                  patch.object(phase, "virsh", return_value=phase.Cmd([], 0, "", "")),
                  patch.object(phase.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid())),
                  patch.object(phase.os, "posix_fallocate", side_effect=OSError("injected allocation failure"))):
                with self.assertRaisesRegex(OSError, "injected"):
                    phase.stage_shm(pwd.getpwuid(os.getuid()), 1024, False)
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
