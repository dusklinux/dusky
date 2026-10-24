"""Lifecycle and configuration regressions for the host clipboard manager."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vm_clipboard as manager
import wayland_clipboard as endpoint

IDENT = "11111111-1111-1111-1111-111111111111"


class ClipboardLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def test_manual_bridge_resolves_the_managed_nat_network(self):
        responses = {
            ("dumpxml", "--inactive", "manual"): '<domain><devices><interface><source bridge="virbr0"/><mac address="aa:bb:cc:dd:ee:ff"/></interface></devices></domain>',
            ("net-list", "--name"): "\ndefault\n\n",
            ("net-dumpxml", "default"): '<network><bridge name="virbr0"/></network>',
            ("net-dhcp-leases", "default", "--mac", "aa:bb:cc:dd:ee:ff"): "2030-01-01 12:00:00 aa:bb:cc:dd:ee:ff ipv4 192.0.2.5/24 manual -",
        }
        def virsh(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, responses[args], "")
        with patch.object(manager.vm, "virsh", side_effect=virsh):
            self.assertEqual(manager.dhcp_address("manual"), "192.0.2.5")

    def test_deleted_vm_stops_without_event_retry_loop(self):
        (self.root / "config.json").write_text(json.dumps({"domain": "gone", "uuid": IDENT}))
        def virsh(*args, **kwargs):
            return subprocess.CompletedProcess([], 1 if args[0] == "dominfo" else 0, "", "")
        with patch.object(manager, "folder", return_value=self.root), \
                patch.object(manager.vm, "virsh", side_effect=virsh) as run:
            self.assertEqual(manager.run_bridge(IDENT), 0)
        self.assertEqual(len(run.call_args_list), 2)

    def test_manual_account_is_preserved_without_guest_agent(self):
        config = dict(uuid=IDENT, domain="manual", address="192.0.2.1", user="chosen", discover_user=False)
        with patch.object(manager, "folder", return_value=self.root), \
                patch.object(manager, "dhcp_address", return_value="192.0.2.2"), \
                patch.object(manager.vm, "agent") as agent:
            updated = manager.refresh(config)
        self.assertEqual(updated["user"], "chosen")
        self.assertEqual(updated["address"], "192.0.2.2")
        agent.assert_not_called()

    def test_auto_guest_setup_runs_once_per_boot(self):
        config = dict(uuid=IDENT, domain="auto", address="192.0.2.1", user="root", discover_user=True)
        (self.root / "key.pub").write_text("ssh-ed25519 fixture")
        info = {"user": "installed", "host_key": "ssh-ed25519 host-fixture"}
        with patch.object(manager, "folder", return_value=self.root), \
                patch.object(manager, "dhcp_address", return_value="192.0.2.2"), \
                patch.object(manager.vm, "agent", return_value={}), \
                patch.object(manager.vm, "guest_exec", side_effect=["boot-one", json.dumps(info), "boot-one"]) as execute:
            manager.refresh(config)
            manager.refresh(config)
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(config["user"], "installed")
        self.assertEqual((self.root / "known_hosts").read_text(), f"{IDENT} ssh-ed25519 host-fixture\n")

    def test_host_key_alias_survives_dhcp_address_changes(self):
        path = self.root / "known_hosts"
        path.write_text("192.0.2.1 ssh-ed25519 public-key\n")
        manager.alias_host_keys(path, IDENT)
        config = dict(uuid=IDENT, user="guest", address="192.0.2.2", port=22)
        with patch.object(manager, "folder", return_value=self.root):
            self.assertIn(f"HostKeyAlias={IDENT}", manager.command(config))
        self.assertEqual(path.read_text(), f"{IDENT} ssh-ed25519 public-key\n")

    def test_host_can_start_before_its_wayland_socket_exists(self):
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        def ready(_delay):
            server.bind(str(self.root / "wayland-7"))
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.root), "WAYLAND_DISPLAY": ""}), \
                patch.object(endpoint.time, "sleep", side_effect=ready), \
                patch.object(endpoint.shutil, "which", return_value="/usr/bin/tool"):
            env = endpoint.session_environment(False)
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-7")

    def test_unit_keeps_restart_logs_in_runtime_and_avoids_python_version_pin(self):
        text = manager.unit_text(Path('/home/example/a % folder/$clipboard.py'))
        self.assertIn("Restart=on-failure", text)
        self.assertIn("ExecStart=/usr/bin/env python3", text)
        self.assertIn("a %% folder/$$clipboard.py", text)
        self.assertIn("StandardOutput=append:%t/", text)
        self.assertNotIn("python3.14", text)

    def test_delayed_peer_cannot_overwrite_a_newer_host_copy(self):
        # Real pipes/selectors, controlled Wayland boundary. The peer delays an
        # old reply until a new local copy exists but its watcher has not fired.
        selection = self.root / "selection"
        selection.write_bytes(b"initial host")
        peer = '''
import base64,json,sys
from pathlib import Path
def send(data,revision):
    print(json.dumps(dict(text=base64.b64encode(data).decode(),revision=revision)),flush=True)
first=json.loads(sys.stdin.readline())
send(b'old revision',first['revision']-1)
Path(sys.argv[1]).write_bytes(b'new host copy')
send(b'delayed reply',first['revision'])
second=json.loads(sys.stdin.readline())
assert base64.b64decode(second['text'])==b'new host copy'
assert second['revision']>first['revision']
send(b'fresh guest copy',second['revision'])
'''
        real_popen = subprocess.Popen
        def spawn(argv, **kwargs):
            if argv[0] == "wl-paste":
                argv = [sys.executable, "-c", "import time; print('{\"changed\":true}',flush=True); time.sleep(30)"]
            return real_popen(argv, **kwargs)
        copied = []
        def copy(argv, *, input, **kwargs):
            self.assertEqual(argv[0], "wl-copy")
            copied.append(input)
            selection.write_bytes(input)
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(endpoint, "session_environment", return_value=os.environ.copy()), \
                patch.object(endpoint, "current_text", side_effect=lambda env=None: selection.read_bytes()), \
                patch.object(endpoint.subprocess, "Popen", side_effect=spawn), \
                patch.object(endpoint.subprocess, "run", side_effect=copy):
            self.assertEqual(endpoint.bridge([sys.executable, "-c", peer, str(selection)], False), 0)
        self.assertEqual(copied, [b"fresh guest copy"])


if __name__ == "__main__":
    unittest.main()
