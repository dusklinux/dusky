#!/usr/bin/env python3
"""Back up and restore local qemu:///system definitions and persistent VM state.

Target: Linux 7.3+, Python 3.15+, systemd 262+, current Arch libvirt.
Guest disks, external snapshot data, managed-save RAM, firmware binaries and host
packages are NOT backed up. Guest recovery uses a cold boot.
Do not change libvirt configuration or start guests during backup/restore.
Restore merges into the target host; it does not delete unrelated definitions.
"""

import argparse
import fcntl
lazy import glob
import hashlib
import json
import os
import platform
import pwd
lazy import readline
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
except ImportError:
    sys.exit("Install python-rich before running this tool.")

console = Console(highlight=False, markup=False)
URI = "qemu:///system"
SCHEMA = "3.0.0"
MANIFEST = "backup_manifest.json"
TAR_METADATA = ("--xattrs", "--xattrs-include=*", "--acls", "--selinux", "--sparse")
DAEMONS = ("libvirtd", "virtqemud", "virtsecretd", "virtnetworkd", "virtstoraged",
           "virtnwfilterd", "virtproxyd", "virtnodedevd", "virtinterfaced")


class BackupError(Exception):
    """An operation could not be completed reliably."""


@dataclass(frozen=True, slots=True)
class StateTarget:
    key: str
    path: Path
    excludes: tuple[str, ...] = ()


# One definition controls both backup and restore destinations. Runtime QEMU
# directories (including master-key.aes) are not portable cold-boot state.
STATE_TARGETS = (
    StateTarget("nvram", Path("/var/lib/libvirt/qemu/nvram")),
    StateTarget("varstore", Path("/var/lib/libvirt/qemu/varstore")),
    StateTarget("swtpm", Path("/var/lib/libvirt/swtpm")),
    StateTarget("swtpm_localca", Path("/var/lib/swtpm-localca")),
    StateTarget("secrets_var", Path("/var/lib/libvirt/secrets")),
    StateTarget("secrets_etc", Path("/etc/libvirt/secrets")),
    StateTarget("hooks", Path("/etc/libvirt/hooks")),
    StateTarget("hooks_qemu_d", Path("/etc/libvirt/hooks.qemu.d")),
    StateTarget("daemon_configs", Path("/etc/libvirt"), (
        "libvirt/qemu", "libvirt/secrets", "libvirt/nwfilter", "libvirt/storage",
        "libvirt/hooks", "libvirt/hooks.qemu.d", "libvirt/lxc")),
)


def run_cmd(cmd: list[str], *, check: bool = True,
            timeout: float | None = None, discard_stdout: bool = False
            ) -> subprocess.CompletedProcess[str]:
    """Use stable English output for the few tabular virsh interfaces."""
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="strict", timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"}, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"Cannot execute {cmd[0]}: {exc}") from exc
    if check and result.returncode:
        raise BackupError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
                          f"{(result.stderr or result.stdout or "").strip()}")
    return result


def virsh(*args: str, check: bool = True) -> str:
    return run_cmd(["virsh", "--connect", URI, *args], check=check).stdout.rstrip("\n")


def names(*args: str) -> list[str]:
    return [line for line in virsh(*args).splitlines() if line]


def resolve_user_path(value: str) -> Path:
    if value == "~" or value.startswith("~/"):
        user = os.environ.get("SUDO_USER")
        home = Path(pwd.getpwnam(user).pw_dir) if user else Path.home()
        return (home / value[2:]).resolve()
    return Path(value).expanduser().resolve()


def path_completer(text: str, state: int) -> str | None:
    try:
        expanded = glob.escape(str(resolve_user_path(text)))
        pattern = expanded + ("/*" if not text or text.endswith("/") or text == "~" else "*")
        matches = sorted(glob.glob(pattern))
        match = matches[state]
        return match + "/" if Path(match).is_dir() else match
    except (IndexError, OSError, KeyError):
        return None


def ask(prompt: str) -> str:
    console.print(prompt)
    try:
        value = input("> ").strip()
    except EOFError as exc:
        raise BackupError("Input closed; operation cancelled.") from exc
    if not value:
        raise BackupError("No selection/path provided; operation cancelled.")
    return value


def compute_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup_path(root: Path, relative: str) -> Path:
    """Reject broken manifest paths before reading or restoring any state."""
    path = root / relative
    if not relative or Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
        raise BackupError(f"Invalid backup path: {relative!r}")
    return path


def write_payload(root: Path, relative: str, text: str, manifest: dict[str, Any]) -> str:
    path = backup_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    manifest["file_hashes"][relative] = compute_sha256(path)
    return relative


def handle_running_vms(interactive: bool, shutdown_running: bool) -> None:
    active = names("list", "--name")
    if not active:
        return
    action = "shutdown" if shutdown_running else ""
    if not action and interactive:
        console.print(f"Active guests: {', '.join(active)}")
        action = ask("Enter shutdown or abort:")
    if action != "shutdown":
        raise BackupError("Shut down guests first, or use --shutdown-running.")
    for vm in active:
        virsh("shutdown", "--domain", vm, "--mode", "acpi")
    deadline = time.monotonic() + 60
    while names("list", "--name"):
        if time.monotonic() >= deadline:
            raise BackupError("Guests did not stop within 60 seconds.")
        time.sleep(2)


@contextmanager
def offline_state() -> Iterator[None]:
    """Stop activation sockets as well as daemons; always recover active units.

    libvirt autostarts once per host boot. Do not remove its /run markers or
    copy them to another host. Other clients must remain idle during this block.
    """
    if names("list", "--name"):
        raise BackupError("A guest started during the operation; state copy aborted.")
    patterns = [f"{daemon}*" for daemon in DAEMONS]
    output = run_cmd(["systemctl", "list-units", "--all", "--plain", "--no-legend",
                      "--no-pager", *patterns]).stdout
    units = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0].endswith((".socket", ".service")):
            if fields[2] == "active":
                units.append(fields[0])
            elif fields[2] in {"activating", "deactivating", "reloading"}:
                raise BackupError(f"Wait for {fields[0]} to finish its state transition.")
    if not units:
        raise BackupError("No active libvirt systemd units found; cannot quiesce state.")
    console.print("Temporarily stopping libvirt management daemons and sockets.")
    try:
        run_cmd(["systemctl", "stop", *units])
        yield
    finally:
        # Socket activation must be restored even if stop or state copy failed.
        run_cmd(["systemctl", "start", *units])


def export_entities(root: Path, manifest: dict[str, Any]) -> list[Path]:
    """Export persistent infrastructure, retaining human names only as metadata."""
    firmware_paths: list[Path] = []
    for category, listing, dump, tag in (
        ("networks", "net-list", "net-dumpxml", "network"),
        ("pools", "pool-list", "pool-dumpxml", "pool"),
        ("domains", "list", "dumpxml", "domain"),
    ):
        transient = names(listing, "--all", "--transient", "--name")
        if transient:
            raise BackupError(f"Persist or remove transient {category} first: {transient}")
        autostart = set(names(listing, "--all", "--autostart", "--name"))
        active = set(names(listing, "--name"))
        manifest[category] = []
        for name in names(listing, "--all", "--persistent", "--name"):
            flags = ["--inactive"] + (["--security-info"] if tag == "domain" else [])
            xml = virsh(dump, f"--{tag}", name, *flags)
            tree = ET.fromstring(xml)
            uuid = tree.findtext("uuid")
            if not uuid:
                raise BackupError(f"Missing UUID in {category}: {name}")
            relative = write_payload(root, f"{category}/{uuid}.xml", xml, manifest)
            item = {"name": name, "uuid": uuid, "xml": relative,
                    "autostart": name in autostart, "active": name in active}
            manifest[category].append(item)
            if tag == "domain":
                info = dict(line.split(":", 1) for line in virsh("dominfo", "--domain", uuid).splitlines() if ":" in line)
                once = info.get("Autostart Once", "").strip()
                if once not in {"enable", "disable"}:
                    raise BackupError(f"Cannot determine one-time autostart for {name}")
                item["autostart_once"] = once == "enable"
                firmware_paths.extend(domain_state_paths(tree))
                export_history(root, manifest, item)
        console.print(f"Exported {len(manifest[category])} {category}.")

    # These two virsh commands have no --name/--uuid output mode.
    for category, listing, dump in (("nwfilters", "nwfilter-list", "nwfilter-dumpxml"),
                                     ("secrets", "secret-list", "secret-dumpxml")):
        manifest[category] = []
        for line in virsh(listing).splitlines()[2:]:
            fields = line.split()
            if not fields:
                continue
            uuid = fields[0]
            xml = virsh(dump, uuid)
            tree = ET.fromstring(xml)
            if category == "secrets" and tree.get("ephemeral") == "yes":
                raise BackupError(f"Ephemeral secret {uuid} cannot be backed up from persistent state.")
            relative = write_payload(root, f"{category}/{uuid}.xml", xml, manifest)
            manifest[category].append({"name": tree.get("name") or uuid,
                                       "uuid": uuid, "xml": relative})
    return firmware_paths


def domain_state_paths(tree: ET.Element) -> list[Path]:
    paths = []
    for element in tree.findall("./os/nvram") + tree.findall("./os/varstore"):
        source = element.find("source")
        if element.get("type", "file") != "file":
            raise BackupError("Only file-backed firmware state is supported.")
        value = (element.get("path") if element.tag == "varstore" else
                 source.get("file") if source is not None else (element.text or "").strip())
        if value:
            path = Path(value)
            paths.append(path)
            if path.is_symlink():
                paths.append(path.resolve(strict=True))
    for backend in tree.findall("./devices/tpm/backend"):
        if backend.get("type") != "emulator":
            if backend.get("type") == "external":
                raise BackupError("External TPM state must be backed up through its emulator; only libvirt-managed emulators are supported.")
            continue
        source = backend.find("source")
        value = source.get("path") if source is not None else None
        if value:
            path = Path(value)
            paths.append(path)
            if path.is_symlink():
                paths.append(path.resolve(strict=True))
    return paths


def export_history(root: Path, manifest: dict[str, Any], domain: dict[str, Any]) -> None:
    uuid = domain["uuid"]
    for category, command in (("snapshots", "snapshot"), ("checkpoints", "checkpoint")):
        entries = []
        for name in names(f"{command}-list", "--domain", uuid, "--name", "--topological"):
            xml = virsh(f"{command}-dumpxml", "--domain", uuid,
                        f"--{command}name", name, "--security-info")
            ET.fromstring(xml)
            filename = hashlib.sha256(name.encode("utf-8")).hexdigest()
            relative = write_payload(root, f"{category}/{uuid}/{filename}.xml", xml, manifest)
            entries.append({"name": name, "xml": relative})
        domain[category] = entries
    if domain["snapshots"]:
        current = run_cmd(["virsh", "--connect", URI, "snapshot-current", "--domain", uuid, "--name"], check=False)
        if current.returncode == 0:
            domain["current_snapshot"] = current.stdout.rstrip("\n")
        elif "the domain does not have a current snapshot" not in current.stderr:
            raise BackupError(f"Cannot query current snapshot for {uuid}: {current.stderr.strip()}")


def archive_state(root: Path, manifest: dict[str, Any], extra_paths: list[Path]) -> None:
    targets = list(STATE_TARGETS)
    disk_paths = []
    for domain in manifest.get("domains", []):
        tree = ET.parse(root / domain["xml"]).getroot()
        disk_paths.extend(Path(source.get("file")).resolve()
                          for source in tree.findall("./devices/disk//source") if source.get("file"))
    # Tar preserves symlinks rather than following them. Save both the link and
    # its state directory, including links in a custom path's parent directories.
    extra_paths = [*extra_paths, *(target.path.resolve(strict=True)
                                 for target in targets if target.path.is_symlink())]
    extra_paths = [*extra_paths, *(path.resolve(strict=True) for path in extra_paths if path.exists())]
    covered = [target.path for target in targets if target.key != "daemon_configs"]
    for path in sorted(set(extra_paths)):
        if not path.is_absolute():
            raise BackupError(f"VM state path is not absolute: {path}")
        if not path.exists():
            # A never-started domain may have no generated firmware/TPM state yet.
            console.print(f"No generated state at {path}; skipped.")
            continue
        if not any(path.is_relative_to(parent) for parent in covered):
            key = "custom_" + hashlib.sha256(os.fsencode(path)).hexdigest()
            targets.append(StateTarget(key, path))
            covered.append(path)
    for target in targets:
        if target.path.exists() and not (target.path.is_file() or target.path.is_dir()):
            raise BackupError(f"State must be a regular file or directory: {target.path}")
        if any(disk.is_relative_to(target.path.resolve()) for disk in disk_paths):
            raise BackupError(f"Guest disk overlaps archived state: {target.path}")
        if root.resolve().is_relative_to(target.path.resolve()):
            raise BackupError(f"Backup destination is inside archived state: {target.path}")
    manifest["archives"] = []
    (root / "state_archives").mkdir()
    for target in targets:
        if not target.path.exists():
            continue
        relative = f"state_archives/{target.key}.tar.zst"
        archive = root / relative
        run_cmd(["tar", "--create", "--zstd", *TAR_METADATA,
                 *(f"--exclude={pattern}" for pattern in target.excludes),
                 "--file", str(archive), "--directory", str(target.path.parent),
                 "--", target.path.name])
        manifest["file_hashes"][relative] = compute_sha256(archive)
        manifest["archives"].append({"key": target.key, "file": relative,
                                     "destination": str(target.path.parent),
                                     "member": target.path.name})
        console.print(f"Archived {target.path}.")


SECRET_KEY = Path("/var/lib/libvirt/secrets/secrets-encryption-key")


def export_secret_key(root: Path, manifest: dict[str, Any]) -> None:
    """Make Arch's machine-bound libvirt credential recoverable on another host."""
    if not SECRET_KEY.is_file():
        return
    relative = "secrets-encryption-key"
    output = root / relative
    run_cmd(["systemd-creds", "decrypt", "--name=secrets-encryption-key",
             str(SECRET_KEY), str(output)], discard_stdout=True)
    output.chmod(0o600)
    if output.stat().st_size != 32:
        raise BackupError("Libvirt secrets encryption key must contain exactly 32 bytes.")
    manifest["secret_key"] = relative
    manifest["file_hashes"][relative] = compute_sha256(output)


@contextmanager
def prepared_secret_key(source: Path | None) -> Iterator[Path | None]:
    """Rewrap before state injection; keep the result ready for atomic replacement."""
    if source is None:
        yield None
        return
    SECRET_KEY.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-key-", dir=SECRET_KEY.parent) as temporary:
        output = Path(temporary) / SECRET_KEY.name
        run_cmd(["systemd-creds", "encrypt", "--name=secrets-encryption-key",
                 str(source), str(output)], discard_stdout=True)
        output.chmod(0o600)
        yield output


def execute_backup(dest_dir_arg: str | None = None, non_interactive: bool = False,
                   shutdown_running: bool = False) -> Path:
    target = resolve_user_path(dest_dir_arg or ask("Backup destination (new or empty directory):"))
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError("Backup destination must be a new or empty directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    virsh("uri")
    handle_running_vms(not non_interactive, shutdown_running)
    manifest: dict[str, Any] = {
        "version": SCHEMA, "created_at": datetime.now(UTC).isoformat(),
        "uri": URI, "kernel": os.uname().release, "hostname": os.uname().nodename,
        "os_name": platform.freedesktop_os_release().get("PRETTY_NAME", "Linux"),
        "python_version": sys.version.split()[0], "libvirt_version": virsh("--version"),
        "file_hashes": {},
    }
    # A manifest appears at the requested destination only after every step passes.
    with tempfile.TemporaryDirectory(prefix=f".{target.name}-", dir=target.parent) as temporary:
        staging = Path(temporary)
        extra_paths = export_entities(staging, manifest)
        export_secret_key(staging, manifest)
        with offline_state():
            archive_state(staging, manifest, extra_paths)
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        validate_backup(staging, check_hashes=False)
        staging.rename(target)
    console.print(f"Backup complete: {target}\nGuest disks, external snapshot data and managed-save RAM are excluded.")
    return target


def upgrade_manifest(data: dict[str, Any]) -> dict[str, Any]:
    """Read existing v2 backups without trusting their directory contents."""
    data = dict(data)
    archives = data.get("archives")
    if not isinstance(archives, dict):
        raise BackupError("Invalid version 2 archive inventory.")
    for key in ("snapshots", "checkpoints", "current_snapshots"):
        if not isinstance(data.get(key, {}), dict):
            raise BackupError(f"Invalid version 2 {key} metadata.")
    if not isinstance(data.get("file_hashes"), dict):
        raise BackupError("Invalid version 2 checksum inventory.")
    for category in ("domains", "networks", "pools", "nwfilters", "secrets"):
        if not isinstance(data.get(category), list):
            raise BackupError(f"Invalid version 2 {category} inventory.")
    targets = {target.key: target for target in STATE_TARGETS}
    data["archives"] = []
    for key, filename in archives.items():
        if key == "save":
            # RAM images are deliberately outside this tool's recovery scope.
            data["file_hashes"].pop(f"state_archives/{filename}", None)
            continue
        if key not in targets or not isinstance(filename, str):
            raise BackupError(f"Unknown version 2 archive: {key}")
        target = targets[key]
        data["archives"].append({"key": key, "file": f"state_archives/{filename}",
                                 "destination": str(target.path.parent), "member": target.path.name})
    for category in ("domains", "networks", "pools", "nwfilters", "secrets"):
        converted = []
        for item in data[category]:
            item = dict(item)
            directory = "vms" if category == "domains" else category
            filename = item["uuid"] if category == "secrets" else item["name"]
            item["xml"] = f"{directory}/{filename}.xml"
            converted.append(item)
            if category == "domains":
                item["autostart_once"] = None
                for history in ("snapshots", "checkpoints"):
                    entries = []
                    for name in data.get(history, {}).get(item["name"], []):
                        safe = name.replace("/", "_").replace("\\", "_").replace("..", "_")
                        entries.append({"name": name, "xml": f"{history}/{item['name']}/{safe}.xml"})
                    item[history] = entries
                if current := data.get("current_snapshots", {}).get(item["name"]):
                    item["current_snapshot"] = current
        data[category] = converted
    data.update(version=SCHEMA, uri=URI)
    return data


def validate_backup(source: Path, *, check_hashes: bool = True) -> dict[str, Any]:
    """Check payloads, archive decompression and restore metadata before host writes.

    Backup publication skips rehashing newly written payloads; --verify and
    --restore always perform full checks.
    """
    data = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("version") == "2.0.0":
        data = upgrade_manifest(data)
    if not isinstance(data, dict) or data.get("version") != SCHEMA or data.get("uri") != URI:
        raise BackupError(f"Expected a {SCHEMA} {URI} backup. Backups without a version 2/3 manifest need separate recovery.")
    hashes = data.get("file_hashes")
    if not isinstance(hashes, dict):
        raise BackupError("Manifest is missing its file hash inventory.")
    referenced: set[str] = set()

    def reference(relative: str) -> Path:
        if not isinstance(relative, str) or relative in referenced:
            raise BackupError(f"Invalid or duplicate payload reference: {relative!r}")
        referenced.add(relative)
        path = backup_path(source, relative)
        expected = hashes.get(relative)
        if not isinstance(expected, str) or len(expected) != 64 or not path.is_file() or (check_hashes and compute_sha256(path) != expected):
            raise BackupError(f"Missing or corrupted payload: {relative}")
        return path

    for category in ("domains", "networks", "pools", "nwfilters", "secrets"):
        items = data.get(category)
        if not isinstance(items, list):
            raise BackupError(f"Manifest is missing {category}.")
        seen = set()
        for item in items:
            if isinstance(item, dict) and not item.get("uuid") and item.get("xml"):
                path = backup_path(source, item["xml"])
                expected = hashes.get(item["xml"])
                if not expected or compute_sha256(path) != expected:
                    raise BackupError(f"Missing or corrupted payload: {item['xml']}")
                item["uuid"] = ET.parse(path).getroot().findtext("uuid")
            if (not isinstance(item, dict) or
                    not all(isinstance(item.get(key), str) and item[key] for key in ("name", "uuid", "xml"))):
                raise BackupError(f"Invalid {category} entry.")
            if item["uuid"] in seen:
                raise BackupError(f"Duplicate {category} UUID: {item['uuid']}")
            seen.add(item["uuid"])
            tree = ET.parse(reference(item["xml"])).getroot()
            expected_tag = {"domains": "domain", "networks": "network", "pools": "pool",
                            "nwfilters": "filter", "secrets": "secret"}[category]
            if tree.tag != expected_tag:
                raise BackupError(f"Wrong XML type for {category}: {item['xml']}")
            if tree.findtext("uuid") != item["uuid"]:
                raise BackupError(f"XML UUID does not match manifest: {item['xml']}")
            xml_name = tree.get("name") if category == "nwfilters" else tree.findtext("name")
            if category != "secrets" and xml_name != item["name"]:
                raise BackupError(f"XML name does not match manifest: {item['xml']}")
            if category in {"domains", "networks", "pools"}:
                if any(type(item.get(key)) is not bool for key in ("active", "autostart")):
                    raise BackupError(f"Missing active/autostart metadata: {item['name']}")
            if category == "domains":
                if item.get("autostart_once") is not None and type(item["autostart_once"]) is not bool:
                    raise BackupError(f"Missing one-time autostart metadata: {item['name']}")
                for history in ("snapshots", "checkpoints"):
                    entries = item.get(history)
                    if not isinstance(entries, list):
                        raise BackupError(f"Missing {history} metadata: {item['name']}")
                    history_names = set()
                    for entry in entries:
                        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
                            raise BackupError(f"Invalid {history} entry.")
                        tree = ET.parse(reference(entry["xml"])).getroot()
                        if tree.findtext("name") != entry["name"] or entry["name"] in history_names:
                            raise BackupError(f"Invalid {history} name: {entry['name']}")
                        embedded_uuid = tree.findtext("domain/uuid")
                        if embedded_uuid and embedded_uuid != item["uuid"]:
                            raise BackupError(f"History belongs to another domain: {entry['xml']}")
                        parent = tree.findtext("parent/name")
                        if parent and parent not in history_names:
                            raise BackupError(f"Invalid {history} parent order: {parent}")
                        history_names.add(entry["name"])
                    if history == "snapshots" and item.get("current_snapshot") not in history_names | {None}:
                        raise BackupError("Current snapshot is absent from snapshot inventory.")
    archives = data.get("archives")
    if not isinstance(archives, list):
        raise BackupError("Missing archive inventory.")
    archive_keys = set()
    for archive in archives:
        if (not isinstance(archive, dict) or
                not all(isinstance(archive.get(key), str) and archive[key]
                        for key in ("key", "file", "destination", "member"))):
            raise BackupError("Invalid archive entry.")
        if archive["key"] in archive_keys:
            raise BackupError(f"Duplicate archive: {archive['key']}")
        archive_keys.add(archive["key"])
        path = reference(archive["file"])
        destination = Path(archive["destination"])
        member = archive["member"]
        if not destination.is_absolute() or not member or Path(member).name != member or member in {".", ".."}:
            raise BackupError(f"Invalid archive destination: {destination}/{member}")
        known = next((target for target in STATE_TARGETS if target.key == archive["key"]), None)
        if known is not None and known.path != destination / member:
            raise BackupError(f"Incorrect destination for {archive['key']}")
        if known is None and not archive["key"].startswith("custom_"):
            raise BackupError(f"Unknown archive key: {archive['key']}")
        if check_hashes:
            # Discard member listings: a large archive can contain millions of files.
            run_cmd(["tar", "--list", "--zstd", "--file", str(path)], discard_stdout=True)
    credential = data.get("secret_key")
    if credential is not None:
        if not isinstance(credential, str) or reference(credential).stat().st_size != 32:
            raise BackupError("Invalid portable libvirt secrets key.")
    if referenced != set(hashes):
        raise BackupError("Payload references and checksum inventory differ.")
    return data


def execute_verify(source_dir_arg: str | None = None) -> bool:
    source = resolve_user_path(source_dir_arg or ask("Backup directory to verify:"))
    try:
        data = validate_backup(source)
    except (BackupError, OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        console.print(f"Verification FAILED: {exc}")
        return False
    console.print(f"Verified {len(data['file_hashes'])} payloads, XML identities and history ordering.\n"
                  f"Created: {data.get('created_at', 'unknown')} on {data.get('hostname', 'unknown')}")
    return True


def restore_entities(source: Path, data: dict[str, Any]) -> None:
    for category, command, tag in (("secrets", "secret-define", "secret"),
                                    ("nwfilters", "nwfilter-define", "filter"),
                                    ("pools", "pool-define", "pool"),
                                    ("networks", "net-define", "network"),
                                    ("domains", "define", "domain")):
        for item in data[category]:
            virsh(command, str(source / item["xml"]))
            if category in {"pools", "networks", "domains"}:
                prefix = {"pools": "pool-", "networks": "net-", "domains": ""}[category]
                virsh(f"{prefix}autostart", f"--{tag}", item["uuid"],
                      *([] if item["autostart"] else ["--disable"]))
                if category == "domains" and item.get("autostart_once") is not None:
                    virsh("autostart", "--domain", item["uuid"], "--once",
                          *([] if item["autostart_once"] else ["--disable"]))
                if category != "domains" and item["active"]:
                    active = set(names(f"{prefix}list", "--uuid"))
                    if item["uuid"] not in active:
                        virsh(f"{prefix}start", f"--{tag}", item["uuid"])
            console.print(f"Defined {category}: {item['name']}")
    for domain in data["domains"]:
        for category, command in (("snapshots", "snapshot"), ("checkpoints", "checkpoint")):
            for entry in domain[category]:
                virsh(f"{command}-create", "--domain", domain["uuid"],
                      "--xmlfile", str(source / entry["xml"]), "--redefine")
        if current := domain.get("current_snapshot"):
            virsh("snapshot-current", "--domain", domain["uuid"], "--snapshotname", current)


def execute_restore(source_dir_arg: str | None = None, non_interactive: bool = False,
                    shutdown_running: bool = False) -> None:
    source = resolve_user_path(source_dir_arg or ask("Backup directory to restore:"))
    data = validate_backup(source)
    credential = data.get("secret_key")
    with prepared_secret_key(source / credential if credential else None) as prepared:
        virsh("uri")
        saved = set(names("list", "--all", "--with-managed-save", "--uuid"))
        conflicts = [item["name"] for item in data["domains"] if item["uuid"] in saved]
        if conflicts:
            raise BackupError(f"Remove existing managed-save RAM before cold restoration (virsh managedsave-remove): {', '.join(conflicts)}")
        handle_running_vms(not non_interactive, shutdown_running)
        console.print("Restoring state; guest disks and external snapshot data must already be available.")
        with offline_state():
            for archive in data["archives"]:
                destination = Path(archive["destination"])
                destination.mkdir(parents=True, exist_ok=True)
                run_cmd(["tar", "--extract", "--zstd", *TAR_METADATA,
                         "--same-owner", "--same-permissions", "--file", str(source / archive["file"]),
                         "--directory", str(destination)])
            if prepared is not None:
                prepared.replace(SECRET_KEY)
    restore_entities(source, data)
    console.print("Restoration complete. Guests remain stopped; autostart settings were restored.")


def require_root() -> None:
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--backup", metavar="DEST", help="Back up to a new or empty directory")
    actions.add_argument("--restore", metavar="SRC", help="Restore a version 2/3 backup")
    actions.add_argument("--verify", metavar="DIR", help="Verify a version 2/3 backup without libvirt/root")
    parser.add_argument("--shutdown-running", action="store_true", help="Request ACPI shutdown and wait up to 60s")
    parser.add_argument("--non-interactive", "-y", action="store_true", help="Require explicit action and path; never prompt")
    args = parser.parse_args()
    if args.verify:
        return 0 if execute_verify(args.verify) else 1
    if args.non_interactive and not (args.backup or args.restore):
        parser.error("--non-interactive requires --backup, --restore or --verify")
    if not (args.backup or args.restore):
        readline.set_completer_delims("\t\n")
        readline.parse_and_bind("tab: complete")
        readline.set_completer(path_completer)
        choice = ask("Select: 1 backup, 2 restore, 3 verify, 4 exit")
        match choice:
            case "1":
                args.backup = ask("Backup destination:")
            case "2":
                args.restore = ask("Backup directory to restore:")
            case "3":
                return 0 if execute_verify() else 1
            case "4":
                return 0
            case _:
                raise BackupError("Unknown selection.")
        # Preserve interactive selections through privilege elevation.
        sys.argv = [sys.argv[0], "--backup" if args.backup else "--restore", args.backup or args.restore]
        if args.shutdown_running:
            sys.argv.append("--shutdown-running")
    require_root()
    for command in ("virsh", "tar", "zstd", "systemctl", "systemd-creds"):
        if shutil.which(command) is None:
            raise BackupError(f"Required command is missing: {command}")
    # Prevent two instances of this script from interleaving export/injection.
    with Path("/run/lock/dusky-libvirt-backup.lock").open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Another backup/restore operation is running.") from exc
        operation = execute_backup if args.backup else execute_restore
        operation(args.backup or args.restore, args.non_interactive, args.shutdown_running)
    return 0


def terminate(signum: int, _frame: Any) -> None:
    # Unwind offline_state() before exiting on a normal service/job termination.
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("Interrupted; any in-progress restore may be partial.")
        sys.exit(130)
    except (BackupError, OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        console.print(f"ERROR: {exc}")
        sys.exit(1)
