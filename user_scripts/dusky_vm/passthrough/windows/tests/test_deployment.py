"""Fresh-install defaults and XML-preview regressions."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


path = Path(__file__).resolve().parents[1] / "deploy_vm.py"
spec = importlib.util.spec_from_file_location("windows_deploy_tests", path)
deploy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = deploy
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = Path(self.directory.name)
        self.vm = deploy.Spec("win11", "win11", True, 8192, 8,
                              self.storage / "new.qcow2", "default", "spice", None, None, [])

    def test_accepting_defaults_selects_windows_and_looking_glass(self):
        with (patch.object(deploy, "console", MagicMock()),
              patch.object(deploy, "active_networks", return_value=["default"]),
              patch.object(deploy, "domain_exists", return_value=False),
              patch.object(deploy, "host_ram_mib", return_value=32768),
              patch.object(deploy.os, "cpu_count", return_value=16),
              patch.object(deploy, "ask_path", return_value=None),
              patch.object(deploy.Confirm, "ask", return_value=False),
              patch.object(deploy.Prompt, "ask", side_effect=lambda *a, **k: k["default"]),
              patch.object(deploy.IntPrompt, "ask", side_effect=lambda *a, **k: k["default"])):
            vm, size = deploy.interview({"storage_dir": str(self.storage)}, self.storage)
        self.assertTrue(vm.windows)
        self.assertEqual(vm.osinfo, "win11")
        self.assertEqual(vm.graphics, "spice")
        self.assertEqual(vm.hostdevs, [])
        self.assertEqual(vm.disk_gib, size)
        self.assertEqual(vm.ram_mib, 8192)
        self.assertFalse(vm.balloon)

    def test_memory_default_stays_eight_gib_on_small_and_large_hosts(self):
        for host in (7680, 8192, 16384, 65536, 262144):
            with (self.subTest(host_mib=host), patch.object(deploy, "console", MagicMock()),
                  patch.object(deploy, "host_ram_mib", return_value=host),
                  patch.object(deploy.IntPrompt, "ask", side_effect=lambda *a, **k: k['default']),
                  patch.object(deploy.Confirm, "ask", side_effect=lambda *a, **k: k['default'])):
                self.assertEqual(deploy.choose_memory(passthrough=False), (8192, False))

    def test_custom_memory_and_balloon_choice_are_not_clamped(self):
        for gib in (4, 8, 50):
            for balloon in (False, True):
                with (self.subTest(gib=gib, balloon=balloon), patch.object(deploy, "console", MagicMock()),
                      patch.object(deploy, "host_ram_mib", return_value=8192),
                      patch.object(deploy.IntPrompt, "ask", return_value=gib),
                      patch.object(deploy.Confirm, "ask", return_value=balloon)):
                    self.assertEqual(deploy.choose_memory(passthrough=True), (gib * 1024, balloon))

    def test_invalid_ram_reprompts_instead_of_building_bad_xml(self):
        with (patch.object(deploy, "console", MagicMock()),
              patch.object(deploy, "host_ram_mib", return_value=8192),
              patch.object(deploy.IntPrompt, "ask", side_effect=[0, -4, 4]),
              patch.object(deploy.Confirm, "ask", return_value=False)):
            self.assertEqual(deploy.choose_memory(passthrough=False), (4096, False))

    def test_memory_amount_and_mode_reach_command_and_metadata(self):
        import xml.etree.ElementTree as ET
        for gib in (4, 8, 50):
            for balloon in (False, True):
                with self.subTest(gib=gib, balloon=balloon):
                    self.vm.ram_mib, self.vm.balloon = gib * 1024, balloon
                    command = deploy.build_command(self.vm)
                    self.assertEqual(command[command.index('--memory') + 1], str(gib * 1024))
                    self.assertEqual(command[command.index('--memballoon') + 1],
                                     'virtio,stats.period=5' if balloon else 'none')
                    root = ET.fromstring(deploy.record_windows_choices('<domain><devices/></domain>', self.vm))
                    settings = root.find(f'./metadata/{{{deploy.WINDOWS_NS}}}windows')
                    self.assertEqual(settings.get('memory'), 'balloon' if balloon else 'fixed')

    def test_xml_generation_requests_no_storage_side_effects(self):
        command = deploy.build_command(self.vm)
        self.assertIn("--dry-run", command)
        self.assertIn("--print-xml", command)
        self.assertIn("size=80", command[command.index("--disk") + 1])
        self.assertEqual(command[command.index("--video") + 1], "vga")

    def run_main(self, argv, state):
        mutations = {name: MagicMock() for name in (
            "provision_disk", "ensure_pool", "define_domain", "state_merge")}
        with (patch.object(sys, "argv", [str(path), *argv]),
              patch.object(deploy, "elevate"), patch.object(deploy, "console", MagicMock()),
              patch.object(deploy.shutil, "which", return_value="/usr/bin/tool"),
              patch.object(deploy, "run", return_value=deploy.Cmd([], 0, "active", "")),
              patch.object(deploy, "state_load", return_value=state),
              patch.object(deploy.Confirm, "ask", return_value=True),
              patch.object(deploy, "interview", return_value=(self.vm, 80)),
              patch.object(deploy, "generate_xml", return_value="<domain><devices/></domain>"),
              patch.multiple(deploy, **mutations)):
            try:
                deploy.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        for operation in mutations.values():
            operation.assert_not_called()
        return code

    def test_dump_only_does_not_create_or_replace_storage_even_with_force(self):
        self.assertEqual(self.run_main(["--dump-only", "--force-disk"], {}), 0)
        self.assertFalse(self.vm.disk.exists())

    def test_invalid_shared_memory_stops_before_disk_changes(self):
        self.assertEqual(self.run_main([], {"shm_mib": 96}), 1)

    def test_missing_phase_25_stops_before_disk_changes(self):
        self.assertEqual(self.run_main([], {}), 1)

    def test_access_and_device_choices_are_independent(self):
        import xml.etree.ElementTree as ET
        for access in ("both", "looking-glass", "rdp", "spice"):
            for virtio in (True, False):
                for graphics in ("spice", "passthrough"):
                    with self.subTest(access=access, virtio=virtio, graphics=graphics):
                        self.vm.access, self.vm.virtio, self.vm.graphics = access, virtio, graphics
                        self.assertEqual(deploy.needs_looking_glass(self.vm), access in ("both", "looking-glass"))
                        command = deploy.build_command(self.vm)
                        self.assertIn("bus=virtio" if virtio else "bus=sata", command[command.index("--disk")+1])
                        root = ET.fromstring(deploy.record_windows_choices("<domain><devices/></domain>", self.vm))
                        selected = root.find(f"./metadata/{{{deploy.WINDOWS_NS}}}windows")
                        self.assertEqual(selected.get("access"), access)
                        self.assertEqual(selected.get("virtio"), "yes" if virtio else "no")

    def test_rdp_only_preview_does_not_require_shared_memory(self):
        self.vm.access = "rdp"
        self.assertEqual(self.run_main(["--dump-only"], {"shm_mib": 96}), 0)


if __name__ == "__main__":
    unittest.main()
