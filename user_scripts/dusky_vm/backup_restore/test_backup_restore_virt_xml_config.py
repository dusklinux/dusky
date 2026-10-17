"""Run with python3 -m unittest discover -s this_directory -v; no live host writes."""

import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("backup_restore_virt_xml_config.py")
spec = importlib.util.spec_from_file_location("virt_backup", SCRIPT)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)
app.console.file = io.StringIO()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(app, "SECRET_KEY", self.root / "host-credential").start()
        self.manifest = {"version": app.SCHEMA, "uri": app.URI, "file_hashes": {}, "archives": [],
                         **{key: [] for key in ("domains", "networks", "pools", "nwfilters", "secrets")}}

    def save(self):
        (self.root / app.MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")

    def add_domain(self):
        item = {"name": "guest with spaces", "uuid": "guest-id", "autostart": False,
                "autostart_once": True, "active": False, "snapshots": [], "checkpoints": []}
        item["xml"] = app.write_payload(self.root, "domains/guest-id.xml",
                                       "<domain><name>guest with spaces</name><uuid>guest-id</uuid></domain>", self.manifest)
        self.manifest["domains"].append(item)
        return item

    def add_history(self, domain, category, name, parent=None):
        xml = f"<history><name>{name}</name>" + (f"<parent><name>{parent}</name></parent>" if parent else "") + "</history>"
        relative = app.write_payload(self.root, f"{category}/{len(domain[category])}.xml", xml, self.manifest)
        domain[category].append({"name": name, "xml": relative})

    def test_valid_identity_and_history(self):
        domain = self.add_domain()
        self.add_history(domain, "checkpoints", "z-parent")
        self.add_history(domain, "checkpoints", "a-child", "z-parent")
        self.save()
        self.assertEqual(app.validate_backup(self.root)["domains"][0], domain)

    def test_missing_and_corrupt_payloads_fail(self):
        domain = self.add_domain()
        self.save()
        path = self.root / domain["xml"]
        for content in (b"broken", None):
            if content is None:
                path.unlink()
            else:
                path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(app.BackupError):
                app.validate_backup(self.root)

    def test_incomplete_inventory_fails(self):
        self.add_domain()
        self.manifest["file_hashes"] = {}
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_unreferenced_payload_fails(self):
        self.manifest["file_hashes"]["unused"] = "a" * 64
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_wrong_xml_identity_fails(self):
        domain = self.add_domain()
        domain["uuid"] = "other-id"
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_child_before_parent_fails(self):
        domain = self.add_domain()
        self.add_history(domain, "checkpoints", "child", "parent")
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_duplicate_history_reference_fails(self):
        domain = self.add_domain()
        self.add_history(domain, "snapshots", "snapshot")
        domain["snapshots"].append(copy.deepcopy(domain["snapshots"][0]))
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_path_traversal_and_symlink_escape_fail(self):
        for relative in ("../outside", "/absolute", "../../"):
            with self.subTest(relative=relative), self.assertRaises(app.BackupError):
                app.backup_path(self.root, relative)
        (self.root / "link").symlink_to(self.root.parent)
        with self.assertRaises(app.BackupError):
            app.backup_path(self.root, "link/outside")

    def test_restore_preflight_before_host_commands(self):
        self.add_domain()
        self.save()
        (self.root / self.manifest["domains"][0]["xml"]).unlink()
        with patch.object(app, "run_cmd") as command, self.assertRaises(app.BackupError):
            app.execute_restore(str(self.root), True)
        command.assert_not_called()

    def test_verify_exit_codes_and_exclusive_actions(self):
        self.save()
        for args, expected in ((["--verify", str(self.root)], 0),
                               (["--verify", str(self.root / "missing")], 1),
                               (["--backup", "x", "--restore", "y"], 2),
                               (["--non-interactive"], 2), (["--help"], 0)):
            result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, expected, result.stderr + result.stdout)

    def test_backup_published_only_on_success(self):
        target = self.root / "backup"
        def export(root, manifest):
            manifest.update({key: [] for key in ("domains", "networks", "pools", "nwfilters", "secrets")})
            return []
        def archive(root, manifest, paths):
            manifest["archives"] = []
        with patch.object(app, "virsh", return_value="12.8.0"), patch.object(app, "handle_running_vms"), \
             patch.object(app, "offline_state", side_effect=lambda: nullcontext()), \
             patch.object(app, "export_entities", side_effect=export), patch.object(app, "archive_state", side_effect=app.BackupError("tar failed")):
            with self.assertRaises(app.BackupError):
                app.execute_backup(str(target), True)
            self.assertFalse(target.exists())
            self.assertEqual(list(self.root.iterdir()), [])
        target.mkdir()
        with patch.object(app, "virsh", return_value="12.8.0"), patch.object(app, "handle_running_vms"), \
             patch.object(app, "offline_state", side_effect=lambda: nullcontext()), \
             patch.object(app, "export_entities", side_effect=export), patch.object(app, "archive_state", side_effect=archive):
            app.execute_backup(str(target), True)
        self.assertEqual(app.validate_backup(target)["version"], app.SCHEMA)
        with self.assertRaises(app.BackupError):
            app.execute_backup(str(target), True)

    def test_daemon_recovery_after_copy_or_stop_failure(self):
        listing = "virtqemud.service loaded active running description\nvirtqemud.socket loaded active running socket\n"
        for fail_stop in (False, True):
            calls = []
            def command(cmd):
                calls.append(cmd)
                if "list-units" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, listing, "")
                if "stop" in cmd and fail_stop:
                    raise app.BackupError("stop failed")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            with self.subTest(fail_stop=fail_stop), patch.object(app, "names", return_value=[]), \
                 patch.object(app, "run_cmd", side_effect=command), self.assertRaises(app.BackupError):
                with app.offline_state():
                    raise app.BackupError("copy failed")
            self.assertEqual(calls[-1], ["systemctl", "start", "virtqemud.service", "virtqemud.socket"])

    def test_active_query_failure_is_not_empty(self):
        with patch.object(app, "run_cmd", side_effect=app.BackupError("connection lost")), self.assertRaises(app.BackupError):
            app.handle_running_vms(False, True)

    def test_restore_order_and_autostart_disabled(self):
        domain = self.add_domain()
        self.add_history(domain, "checkpoints", "z-parent")
        self.add_history(domain, "checkpoints", "a-child", "z-parent")
        with patch.object(app, "virsh", return_value="") as command:
            app.restore_entities(self.root, self.manifest)
        calls = [call.args for call in command.call_args_list]
        self.assertIn(("autostart", "--domain", "guest-id", "--disable"), calls)
        self.assertIn(("autostart", "--domain", "guest-id", "--once"), calls)
        history = [call for call in calls if call[0] == "checkpoint-create"]
        self.assertTrue(history[0][4].endswith("checkpoints/0.xml"))
        self.assertTrue(history[1][4].endswith("checkpoints/1.xml"))

    def test_restore_failure_propagates(self):
        self.add_domain()
        with patch.object(app, "virsh", side_effect=app.BackupError("define failed")), self.assertRaises(app.BackupError):
            app.restore_entities(self.root, self.manifest)

    def test_custom_firmware_paths(self):
        tree = app.ET.fromstring("<domain><os><nvram>/custom/vars.fd</nvram><varstore path='/custom/vars.json'/></os><devices><tpm><backend type='emulator'><source path='/custom/tpm'/></backend></tpm></devices></domain>")
        self.assertEqual(app.domain_state_paths(tree), [Path("/custom/vars.fd"), Path("/custom/vars.json"), Path("/custom/tpm")])
        tree = app.ET.fromstring("<domain><os><nvram type='block'><source dev='/dev/nvram'/></nvram></os></domain>")
        with self.assertRaises(app.BackupError):
            app.domain_state_paths(tree)

    def test_v2_manifest_preserves_existing_backups(self):
        domain = self.add_domain()
        (self.root / "vms").mkdir()
        (self.root / domain["xml"]).rename(self.root / "vms/guest with spaces.xml")
        old_hash = self.manifest["file_hashes"].pop(domain["xml"])
        self.manifest["file_hashes"]["vms/guest with spaces.xml"] = old_hash
        self.manifest.update(version="2.0.0", archives={})
        domain.pop("xml")
        domain.pop("snapshots")
        domain.pop("checkpoints")
        self.save()
        converted = app.validate_backup(self.root)
        self.assertEqual(converted["domains"][0]["xml"], "vms/guest with spaces.xml")

    def test_real_tar_roundtrip_metadata_and_sparse_state(self):
        state = self.root / "state"
        state.mkdir(mode=0o710)
        data = state / "vars.fd"
        data.write_bytes(b"persistent state")
        data.chmod(0o640)
        os.setxattr(data, "user.virt-audit", b"roundtrip")
        app.run_cmd(["setfacl", "--modify", "user:12345:r--", str(data)])
        (state / "link").symlink_to("vars.fd")
        sparse = state / "managed.save"
        with sparse.open("wb") as stream:
            stream.seek(8 * 1024 * 1024)
            stream.write(b"saved state")
        backup = self.root / "backup"
        backup.mkdir()
        output = self.root / "restored"
        output.mkdir()
        target = app.StateTarget("custom_fixture", state)
        with patch.object(app, "STATE_TARGETS", (target,)):
            app.archive_state(backup, self.manifest, [])
        archive = backup / self.manifest["archives"][0]["file"]
        app.run_cmd(["tar", "--extract", "--zstd", *app.TAR_METADATA, "--same-permissions", "--file", str(archive), "--directory", str(output)])
        restored = output / "state"
        self.assertEqual((restored / "vars.fd").read_bytes(), data.read_bytes())
        self.assertEqual((restored / "vars.fd").stat().st_mode, data.stat().st_mode)
        self.assertEqual((restored / "vars.fd").stat().st_uid, data.stat().st_uid)
        self.assertEqual(os.getxattr(restored / "vars.fd", "user.virt-audit"), b"roundtrip")
        self.assertEqual((restored / "link").readlink(), Path("vars.fd"))
        self.assertEqual(app.run_cmd(["getfacl", "--omit-header", "--numeric", str(data)]).stdout,
                         app.run_cmd(["getfacl", "--omit-header", "--numeric", str(restored / "vars.fd")]).stdout)
        self.assertEqual(app.compute_sha256(restored / "managed.save"), app.compute_sha256(sparse))
        self.assertLess((restored / "managed.save").stat().st_blocks * 512, sparse.stat().st_size)

    def test_export_domain_spaces_and_snapshot_filename_collisions(self):
        xml = "<domain><name>guest with spaces</name><uuid>guest-id</uuid><os><varstore path='/custom/vars.json'/></os></domain>"
        def fake(*args, **kwargs):
            cmd = args[0]
            if cmd in {"net-list", "pool-list"} or "--transient" in args:
                return ""
            if cmd == "list":
                return "guest with spaces" if "--persistent" in args or "--autostart" in args else ""
            if cmd == "dumpxml":
                self.assertIn("guest with spaces", args)
                return xml
            if cmd == "dominfo":
                return "Autostart Once: enable"
            if cmd == "snapshot-list":
                return "a/b\na_b"
            if cmd == "checkpoint-list":
                return ""
            if cmd == "snapshot-dumpxml":
                return f"<domainsnapshot><name>{args[4]}</name></domainsnapshot>"
            return ""
        no_current = subprocess.CompletedProcess([], 1, "", "the domain does not have a current snapshot")
        with patch.object(app, "virsh", side_effect=fake), patch.object(app, "run_cmd", return_value=no_current):
            paths = app.export_entities(self.root, self.manifest)
        self.save()
        app.validate_backup(self.root)
        self.assertEqual(paths, [Path("/custom/vars.json")])
        self.assertEqual(len(set(item["xml"] for item in self.manifest["domains"][0]["snapshots"])), 2)
        self.assertTrue(self.manifest["domains"][0]["autostart"])

    def test_transient_infrastructure_is_not_silently_lost(self):
        with patch.object(app, "names", return_value=["transient"]), self.assertRaises(app.BackupError):
            app.export_entities(self.root, self.manifest)

    def test_ephemeral_secret_is_not_silently_lost(self):
        def fake(*args):
            if args[0] == "secret-list":
                return "UUID Usage\n--------\nsecret-id unused"
            if args[0] == "secret-dumpxml":
                return "<secret ephemeral='yes'><uuid>secret-id</uuid></secret>"
            return ""
        with patch.object(app, "virsh", side_effect=fake), self.assertRaises(app.BackupError):
            app.export_entities(self.root, self.manifest)

    def test_full_restore_with_real_tar_in_temporary_host(self):
        state = self.root / "original-state"
        state.mkdir()
        (state / "nvram").write_bytes(b"firmware variables")
        self.add_domain()
        with patch.object(app, "STATE_TARGETS", (app.StateTarget("custom_fixture", state),)):
            app.archive_state(self.root, self.manifest, [])
        destination = self.root / "restored"
        destination.mkdir()
        self.manifest["archives"][0]["destination"] = str(destination)
        self.save()
        with patch.object(app, "virsh", return_value="") as command, \
             patch.object(app, "handle_running_vms") as guests, \
             patch.object(app, "offline_state", side_effect=lambda: nullcontext()):
            app.execute_restore(str(self.root), True)
        self.assertEqual((destination / state.name / "nvram").read_bytes(), b"firmware variables")
        guests.assert_called_once_with(False, False)
        self.assertTrue(any(call.args[0] == "define" for call in command.call_args_list))

    def test_broken_archive_rejected_before_guest_shutdown(self):
        path = self.root / "broken.tar.zst"
        path.write_bytes(b"not a zstd archive")
        self.manifest["file_hashes"][path.name] = app.compute_sha256(path)
        self.manifest["archives"].append({"key": "custom_fixture", "file": path.name,
                                          "destination": str(self.root), "member": "state"})
        self.save()
        with patch.object(app, "virsh") as command, patch.object(app, "handle_running_vms") as guests, \
             self.assertRaises(app.BackupError):
            app.execute_restore(str(self.root), True)
        command.assert_not_called()
        guests.assert_not_called()

    def test_completion_lists_directory_children(self):
        (self.root / "child with spaces").mkdir()
        self.assertEqual(app.path_completer(str(self.root) + "/", 0), str(self.root / "child with spaces") + "/")
        self.assertIsNone(app.path_completer(str(self.root) + "/", 1))

    def test_v2_pool_uuid_is_recovered_from_checksummed_xml(self):
        xml = "<pool><name>pool</name><uuid>pool-id</uuid></pool>"
        app.write_payload(self.root, "pools/pool.xml", xml, self.manifest)
        self.manifest["pools"].append({"name": "pool", "uuid": "", "active": True, "autostart": False})
        self.manifest.update(version="2.0.0", archives={})
        self.save()
        self.assertEqual(app.validate_backup(self.root)["pools"][0]["uuid"], "pool-id")

    def test_sigterm_unwinds_daemon_context(self):
        listing = "virtqemud.service loaded active running description\n"
        def command(cmd):
            return subprocess.CompletedProcess(cmd, 0, listing if "list-units" in cmd else "", "")
        with patch.object(app, "names", return_value=[]), patch.object(app, "run_cmd", side_effect=command) as calls:
            with self.assertRaises(SystemExit) as error:
                with app.offline_state():
                    app.terminate(15, None)
            self.assertEqual(error.exception.code, 143)
            self.assertEqual(calls.call_args.args[0], ["systemctl", "start", "virtqemud.service"])

    def test_backup_cannot_be_inside_archived_state(self):
        state = self.root / "state"
        state.mkdir()
        staging = state / "backup"
        staging.mkdir()
        with patch.object(app, "STATE_TARGETS", (app.StateTarget("custom_fixture", state),)), \
             patch.object(app, "run_cmd") as command:
            with self.assertRaises(app.BackupError):
                app.archive_state(staging, self.manifest, [])
        command.assert_not_called()

    def test_symlinked_state_directory_includes_target_contents(self):
        state = self.root / "real-state"
        state.mkdir()
        (state / "vars.fd").write_bytes(b"must not disappear")
        link = self.root / "linked-state"
        link.symlink_to(state, target_is_directory=True)
        backup = self.root / "backup"
        backup.mkdir()
        with patch.object(app, "STATE_TARGETS", (app.StateTarget("custom_fixture", link),)):
            app.archive_state(backup, self.manifest, [])
        listing = "".join(app.run_cmd(["tar", "--list", "--zstd", "--file", str(backup / item["file"])]).stdout
                          for item in self.manifest["archives"])
        self.assertIn("vars.fd", listing)

    def test_archive_format_is_checked_by_verify(self):
        path = self.root / "broken.tar.zst"
        path.write_bytes(b"not a zstd archive")
        self.manifest["file_hashes"][path.name] = app.compute_sha256(path)
        self.manifest["archives"].append({"key": "custom_fixture", "file": path.name,
                                          "destination": str(self.root), "member": "state"})
        self.save()
        self.assertFalse(app.execute_verify(str(self.root)))

    def test_managed_save_is_outside_backup_scope(self):
        self.assertNotIn("save", {target.key for target in app.STATE_TARGETS})
        with patch.object(app, "names", return_value=["guest"]), \
             patch.object(app, "ask", return_value="managedsave"), patch.object(app, "virsh") as command, \
             self.assertRaises(app.BackupError):
            app.handle_running_vms(True, False)
        command.assert_not_called()

    def test_guest_disk_overlapping_state_is_rejected(self):
        domain = self.add_domain()
        state = self.root / "state"
        state.mkdir()
        disk = state / "disk.qcow2"
        disk.write_bytes(b"guest data must never be archived")
        xml = f"<domain><devices><disk type='file'><source file='{disk}'/></disk></devices></domain>"
        (self.root / domain["xml"]).write_text(xml, encoding="utf-8")
        with patch.object(app, "STATE_TARGETS", (app.StateTarget("custom_fixture", state),)), \
             patch.object(app, "run_cmd") as command, self.assertRaises(app.BackupError):
            app.archive_state(self.root, self.manifest, [])
        command.assert_not_called()

    def test_external_tpm_socket_is_not_archived(self):
        xml = "<domain><devices><tpm><backend type='external'><source type='unix' path='/run/tpm.sock'/></backend></tpm></devices></domain>"
        with self.assertRaises(app.BackupError):
            app.domain_state_paths(app.ET.fromstring(xml))

    def test_v2_managed_save_is_omitted(self):
        self.manifest.update(version="2.0.0", archives={"save":"save_state.tar.zst"})
        self.manifest["file_hashes"]["state_archives/save_state.tar.zst"] = "a" * 64
        self.save()
        result = app.validate_backup(self.root)
        self.assertEqual(result["archives"], [])
        self.assertEqual(result["file_hashes"], {})

    def test_large_inventory_and_deep_history(self):
        # Reverse lexical history names expose accidental filename-based restore order.
        domain = self.add_domain()
        parent = None
        for index in range(2000):
            name = f"{2000-index:04d}-checkpoint"
            self.add_history(domain, "checkpoints", name, parent)
            parent = name
        self.save()
        validated = app.validate_backup(self.root)
        with patch.object(app, "virsh", return_value="") as command:
            app.restore_entities(self.root, validated)
        self.assertEqual(sum(call.args[0] == "checkpoint-create" for call in command.call_args_list), 2000)

    def test_plain_secret_key_is_part_of_integrity_inventory(self):
        credential = self.root / "portable-key"
        credential.write_bytes(os.urandom(32))
        self.manifest["secret_key"] = credential.name
        self.manifest["file_hashes"][credential.name] = app.compute_sha256(credential)
        self.save()
        app.validate_backup(self.root)
        credential.write_bytes(os.urandom(32))
        self.assertFalse(app.execute_verify(str(self.root)))

    def test_secret_rewrap_failure_keeps_previous_credential(self):
        app.SECRET_KEY.write_bytes(b"previous encrypted credential")
        portable = self.root / "portable"
        portable.write_bytes(os.urandom(32))
        with patch.object(app, "run_cmd", side_effect=app.BackupError("encrypt failed")), self.assertRaises(app.BackupError):
            with app.prepared_secret_key(portable):
                self.fail("Encryption should fail before a credential is prepared")
        self.assertEqual(app.SECRET_KEY.read_bytes(), b"previous encrypted credential")

    def test_restore_rejects_stale_managed_save_before_shutdown(self):
        self.add_domain()
        self.save()
        with patch.object(app, "virsh", return_value=""), \
             patch.object(app, "names", return_value=["guest-id"]), \
             patch.object(app, "handle_running_vms") as guests, \
             patch.object(app, "offline_state") as offline, self.assertRaises(app.BackupError):
            app.execute_restore(str(self.root), True)
        guests.assert_not_called()
        offline.assert_not_called()

    def test_xml_with_wrong_root_is_rejected(self):
        item = self.add_domain()
        app.write_payload(self.root, item["xml"], "<network><name>guest with spaces</name><uuid>guest-id</uuid></network>", self.manifest)
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_history_from_other_domain_is_rejected(self):
        item = self.add_domain()
        self.add_history(item, "snapshots", "snapshot")
        app.write_payload(self.root, item["snapshots"][0]["xml"], "<domainsnapshot><name>snapshot</name><domain><uuid>another-guest</uuid></domain></domainsnapshot>", self.manifest)
        self.save()
        with self.assertRaises(app.BackupError):
            app.validate_backup(self.root)

    def test_invalid_manifest_container_types_fail_cleanly(self):
        base = copy.deepcopy(self.manifest)
        for version in (app.SCHEMA, "2.0.0"):
            for field in ("domains", "networks", "pools", "nwfilters", "secrets", "file_hashes", "archives"):
                for value in (None, 7, "invalid", True):
                    with self.subTest(version=version, field=field, value=value):
                        self.manifest = copy.deepcopy(base)
                        self.manifest["version"] = version
                        if version == "2.0.0":
                            self.manifest["archives"] = {}
                        self.manifest[field] = value
                        self.save()
                        self.assertFalse(app.execute_verify(str(self.root)))

    def test_real_sigterm_recovers_daemons(self):
        # Exercise the actual signal handler in a child, with daemon calls logged.
        code = r'''import importlib.util, json, signal, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("child_app", sys.argv[1])
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)
root = Path(sys.argv[2])
signal.signal(signal.SIGTERM, a.terminate)
a.names = lambda *args: []
def command(cmd):
    with (root / "commands").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(cmd) + "\n")
    return a.subprocess.CompletedProcess(cmd, 0, "virtqemud.service loaded active running daemon\n" if "list-units" in cmd else "", "")
a.run_cmd = command
with a.offline_state():
    (root / "ready").touch()
    time.sleep(30)
'''
        child = subprocess.Popen([sys.executable, "-c", code, str(SCRIPT), str(self.root)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        deadline = time.monotonic() + 5
        while not (self.root / "ready").exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((self.root / "ready").exists())
        child.terminate()
        _, errors = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 143, errors)
        commands = [json.loads(line) for line in (self.root / "commands").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(commands[-1], ["systemctl", "start", "virtqemud.service"])

    def test_real_tar_write_failure_does_not_publish_backup(self):
        state = self.root / "state"
        state.mkdir()
        (state / "payload").write_bytes(os.urandom(1024 * 1024))
        target = self.root / "backup"
        real_run = app.run_cmd
        def command(cmd, **kwargs):
            if cmd[0] == "tar" and "--create" in cmd:
                cmd = list(cmd)
                cmd[cmd.index("--file") + 1] = "/dev/full"
            return real_run(cmd, **kwargs)
        def export(root, manifest):
            manifest.update({key: [] for key in ("domains", "networks", "pools", "nwfilters", "secrets")})
            return []
        with patch.object(app, "STATE_TARGETS", (app.StateTarget("custom_fixture", state),)), \
             patch.object(app, "run_cmd", side_effect=command), patch.object(app, "virsh", return_value="12.8.0"), \
             patch.object(app, "handle_running_vms"), patch.object(app, "export_entities", side_effect=export), \
             patch.object(app, "offline_state", side_effect=lambda: nullcontext()), self.assertRaises(app.BackupError):
            app.execute_backup(str(target), True)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.iterdir()), [state])

    def test_key_encryption_failure_precedes_restore_injection(self):
        self.add_domain()
        credential = self.root / "portable-key"
        credential.write_bytes(os.urandom(32))
        self.manifest["secret_key"] = credential.name
        self.manifest["file_hashes"][credential.name] = app.compute_sha256(credential)
        self.save()
        with patch.object(app, "run_cmd", side_effect=app.BackupError("encrypt failed")), \
             patch.object(app, "virsh") as command, patch.object(app, "handle_running_vms") as guests, \
             patch.object(app, "offline_state") as offline, self.assertRaises(app.BackupError):
            app.execute_restore(str(self.root), True)
        command.assert_not_called()
        guests.assert_not_called()
        offline.assert_not_called()

    def test_archive_of_secret_directory_cannot_overwrite_rewrapped_key(self):
        host = self.root / "host-secrets"
        host.mkdir()
        encrypted = host / "secrets-encryption-key"
        encrypted.write_bytes(b"source-machine encrypted blob")
        portable = self.root / "portable-key"
        portable.write_bytes(os.urandom(32))
        self.manifest["secret_key"] = portable.name
        self.manifest["file_hashes"][portable.name] = app.compute_sha256(portable)
        with patch.object(app, "STATE_TARGETS", (app.StateTarget("custom_fixture", host),)):
            app.archive_state(self.root, self.manifest, [])
        self.save()
        encrypted.write_bytes(b"old destination-machine blob")
        real_run = app.run_cmd
        def command(cmd, **kwargs):
            if cmd[0] == "systemd-creds":
                Path(cmd[-1]).write_bytes(b"correct rewrapped credential")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, **kwargs)
        with patch.object(app, "SECRET_KEY", encrypted), patch.object(app, "run_cmd", side_effect=command), \
             patch.object(app, "virsh", return_value=""), patch.object(app, "handle_running_vms"), \
             patch.object(app, "offline_state", side_effect=lambda: nullcontext()):
            app.execute_restore(str(self.root), True)
        self.assertEqual(encrypted.read_bytes(), b"correct rewrapped credential")
        self.assertEqual(list(host.iterdir()), [encrypted])


if __name__ == "__main__":
    unittest.main()
