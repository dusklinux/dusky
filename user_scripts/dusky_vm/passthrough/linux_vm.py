#!/usr/bin/env python3
"""Arch Linux ISO launcher: KVM/UEFI, optional tmpfs disk, SSH and VirtioFS.

Invoked by 30_kvm_vm_deploy.py. Python 3.15+; no package downloads or GPU
isolation. Boot the ISO and use its installer normally. Re-run --configure
after installing/rebooting to provision the installed guest through QGA.
"""

import argparse
import base64
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

GIB = 1 << 30
RAM_STORAGE = Path("/mnt/zram1")
PERSISTENT_STORAGE = Path("/var/lib/libvirt/images")
URI = "qemu:///system"
TAG = "dusky-host"
NS = "https://dusky.local/vm/1"
ENTRY = Path(__file__).with_name("30_kvm_vm_deploy.py")
CLIPBOARD = Path(__file__).with_name("wayland_clipboard.py")
CLIPBOARD_MANAGER = Path(__file__).with_name("vm_clipboard.py")


def run(argv: list[str], *, check: bool = True, timeout: float = 60,
        input: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, input=input, text=True, encoding="utf-8",
                            capture_output=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{shlex.join(argv)}\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def virsh(*args: str, check: bool = True, timeout: float = 60):
    return run(["virsh", "--connect", URI, *args], check=check, timeout=timeout)


def tool(name: str, interpreter: str | None) -> list[str]:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"Missing {name}; run 05_virtio_iso.py --skip-virtio first.")
    return [interpreter, executable] if interpreter else [executable]


def operator() -> pwd.struct_passwd:
    name = os.environ.get("SUDO_USER")
    user = pwd.getpwnam(name) if name else pwd.getpwuid(os.getuid())
    if user.pw_uid == 0:
        raise RuntimeError("Run from your user session, optionally through sudo.")
    return user


def elevate() -> None:
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "--preserve-env=WAYLAND_DISPLAY", "--",
                           sys.executable, str(ENTRY), *sys.argv[1:]])


def memory_available() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("Cannot read MemAvailable from /proc/meminfo.")


def existing_parent(path: Path) -> Path:
    existing = path
    while not existing.exists():
        existing = existing.parent
    return existing


def filesystem_type(path: Path) -> str:
    existing = existing_parent(path)
    return run(["findmnt", "--noheadings", "--output", "FSTYPE", "--target", str(existing)]).stdout.strip()


def ram_storage_reason(path: Path, ram_mib: int, available: int | None = None) -> str:
    available = memory_available() if available is None else available
    if not existing_parent(path).is_dir() or filesystem_type(path) not in {"tmpfs", "ramfs"}:
        return f"{path} is not a mounted RAM-backed directory"
    needed = 20 * GIB + ram_mib * (1 << 20) + 4 * GIB
    if available <= 24 * GIB or available < needed:
        return f"needs >24 GiB available and {needed / GIB:.1f} GiB for disk + guest + host headroom"
    if shutil.disk_usage(existing_parent(path)).free < 20 * GIB:
        return "less than 20 GiB free in the RAM filesystem"
    return ""


def discover_isos(directory: Path = RAM_STORAGE) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted((p.resolve() for p in directory.iterdir()
                   if p.is_file() and p.name.casefold().startswith("dusky")
                   and p.suffix.casefold() == ".iso"),
                  key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def ask_iso(value: str | None, home: Path, yes: bool) -> Path:
    while True:
        raw = value if value is not None else ("" if yes else input(
            f"Arch/Dusky ISO [Enter: find dusky*.iso in {RAM_STORAGE}]: ").strip())
        if raw:
            path = Path(raw.replace("~", str(home), 1) if raw.startswith("~/") else raw)
            if path.is_file() and path.suffix.casefold() == ".iso":
                return path.resolve()
            if value is not None or yes:
                raise RuntimeError(f"Not an ISO file: {path}")
            print(f"Not an ISO file: {path}")
            continue
        candidates = discover_isos()
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            if yes:
                raise RuntimeError(f"No dusky*.iso found in {RAM_STORAGE}; pass --iso PATH.")
            print(f"No dusky*.iso found in {RAM_STORAGE}. Enter the ISO path.")
            continue
        print("Found multiple ISOs (newest modification time first):")
        for index, candidate in enumerate(candidates, 1):
            print(f"  {index}. {candidate.name}")
        if yes:
            raise RuntimeError("Multiple ISOs found; select explicitly with --iso PATH.")
        choice = input("ISO number [1]: ").strip() or "1"
        if choice.isdecimal() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]


def expand_path(value: str, home: Path) -> Path:
    if value == "~":
        return home
    return home / value[2:] if value.startswith("~/") else Path(value).expanduser()


def choose_storage(value: str | None, ram_mib: int, yes: bool, home: Path | None = None) -> Path:
    home = Path.home() if home is None else home
    if value:
        path = expand_path(value, home).resolve()
    elif yes:
        path = PERSISTENT_STORAGE
    else:
        reason = ram_storage_reason(RAM_STORAGE, ram_mib)
        print(f"1. Persistent: {PERSISTENT_STORAGE} (default)")
        print(f"2. RAM-backed: {RAM_STORAGE}" + (f" [unavailable: {reason}]" if reason else ""))
        print("3. Custom directory")
        while True:
            choice = input("Disk storage [1]: ").strip() or "1"
            if choice == "1":
                path = PERSISTENT_STORAGE
                break
            if choice == "2" and not reason:
                path = RAM_STORAGE
                break
            if choice == "3":
                raw = input("Absolute storage directory: ").strip()
                candidate = expand_path(raw, home)
                if candidate.is_absolute():
                    path = candidate.resolve()
                    break
            print("Choose an available storage option.")
    if filesystem_type(path) in {"tmpfs", "ramfs"}:
        reason = ram_storage_reason(path, ram_mib)
        if reason:
            raise RuntimeError(f"RAM-backed storage unavailable: {reason}")
    return path


def choose_share(value: str | None, home: Path, yes: bool) -> Path:
    raw = value if value is not None else ("" if yes else input(
        f"Host shared directory [{RAM_STORAGE}]: ").strip())
    if not raw:
        if not RAM_STORAGE.is_dir() or filesystem_type(RAM_STORAGE) not in {"tmpfs", "ramfs"}:
            raise RuntimeError(f"Default share {RAM_STORAGE} is not RAM-backed. Mount it or use --share-dir PATH.")
        return RAM_STORAGE.resolve()
    path = expand_path(raw, home).resolve()
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"Shared path is not a directory: {path}")
    return path


def prepare_share(path: Path, user: pwd.struct_passwd) -> None:
    # Existing exports include mount roots such as /mnt/zram1. Preserve their
    # ownership, mode, sticky bit and ACLs; only provision directories we create.
    if not path.exists():
        path.mkdir(parents=True)
        os.chown(path, user.pw_uid, user.pw_gid)
        run(["setfacl", "-m", f"u:{user.pw_uid}:rwx,d:u:{user.pw_uid}:rwx", "--", str(path)])


def select_rendernode(mode: str, explicit: str | None) -> Path | None:
    if mode == "2d":
        return None
    nodes = [Path(explicit)] if explicit else sorted(Path("/dev/dri").glob("renderD*"))
    # Prefer Mesa Intel/AMD drivers. Proprietary NVIDIA must be selected explicitly.
    for node in nodes:
        driver = Path("/sys/class/drm") / node.name / "device/driver"
        if node.exists() and (explicit or driver.resolve().name in {"i915", "xe", "amdgpu"}):
            return node.resolve()
    if mode == "3d":
        raise RuntimeError("No supported render node found; pass --rendernode PATH or --graphics 2d.")
    return None


@dataclass(frozen=True, slots=True)
class Spec:
    name: str
    iso: Path
    storage: Path
    share: Path
    ram_mib: int
    vcpus: int
    network: str
    rendernode: Path | None

    @property
    def disk(self) -> Path:
        return self.storage / f"{self.name}.qcow2"

    @property
    def assets(self) -> Path:
        return self.storage / f".{self.name}"


def baseline_command(spec: Spec, interpreter: str | None) -> list[str]:
    graphics = "spice,listen=none"
    if spec.rendernode:
        graphics += ",gl.enable=yes"
    return [*tool("virt-install", interpreter), "--connect", URI, "--name", spec.name,
            "--osinfo", "archlinux", "--virt-type", "kvm", "--machine", "q35",
            "--memory", str(spec.ram_mib), "--vcpus", f"{spec.vcpus},sockets=1,cores={spec.vcpus},threads=1",
            "--cpu", "host-passthrough,migratable=off", "--disk", "none", "--network", "none",
            "--boot", "uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no,hd,cdrom,menu=on",
            "--graphics", graphics, "--video", "virtio,accel3d=yes" if spec.rendernode else "virtio",
            "--channel", "unix,target.type=virtio,target.name=org.qemu.guest_agent.0",
            "--tpm", "none", "--rng", "/dev/urandom", "--import", "--noautoconsole", "--print-xml"]


def compose_xml(spec: Spec, baseline: str) -> str:
    root = ET.fromstring(baseline)
    devices = root.find("devices")
    os_elem = root.find("os")
    if devices is None or os_elem is None or os_elem.get("firmware") != "efi":
        raise RuntimeError("virt-install did not generate a UEFI domain.")
    ET.SubElement(os_elem, "nvram").text = str(spec.assets / "nvram.fd")
    backing = ET.SubElement(root, "memoryBacking")
    ET.SubElement(backing, "source", type="memfd")
    ET.SubElement(backing, "access", mode="shared")
    volatile = filesystem_type(spec.storage) in {"tmpfs", "ramfs"}
    for device, path, bus, target, fmt in (
        ("disk", spec.disk, "virtio", "vda", "qcow2"),
        ("cdrom", spec.iso, "sata", "sda", "raw"),
    ):
        disk = ET.SubElement(devices, "disk", type="file", device=device)
        driver = ET.SubElement(disk, "driver", name="qemu", type=fmt)
        if device == "disk":
            driver.attrib.update(cache="writeback" if volatile else "none", io="io_uring",
                                 discard="unmap", error_policy="stop")
        else:
            ET.SubElement(disk, "readonly")
        ET.SubElement(disk, "source", file=str(path))
        ET.SubElement(disk, "target", dev=target, bus=bus)
    nic = ET.SubElement(devices, "interface", type="network")
    ET.SubElement(nic, "source", network=spec.network)
    ET.SubElement(nic, "model", type="virtio")
    share = ET.SubElement(devices, "filesystem", type="mount", accessmode="passthrough")
    ET.SubElement(share, "driver", type="virtiofs", queue="1024")
    ET.SubElement(share, "source", dir=str(spec.share))
    ET.SubElement(share, "target", dir=TAG)
    binary = ET.SubElement(share, "binary", path=str(spec.assets / "virtiofsd"))
    # Host-side atomic replacement can invalidate cached guest dentries/handles.
    ET.SubElement(binary, "cache", mode="none")
    if spec.rendernode:
        devices.find("graphics/gl").set("rendernode", str(spec.rendernode))
    metadata = root.find("metadata")
    if metadata is None:
        metadata = ET.SubElement(root, "metadata")
    ET.SubElement(metadata, f"{{{NS}}}config", assets=str(spec.assets), share=str(spec.share))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def ensure_network(name: str) -> None:
    root = ET.fromstring(virsh("net-dumpxml", name).stdout)
    bridge = root.find("bridge")
    if bridge is None or not bridge.get("name"):
        raise RuntimeError(f"Network {name} has no host bridge for host-to-guest SSH.")
    if name not in virsh("net-list", "--name").stdout.splitlines():
        virsh("net-start", name)
    virsh("net-autostart", name)


def prepare_backend(assets: Path) -> None:
    paths = run(["pacman", "-Qlq", "virtiofsd"]).stdout.splitlines()
    daemon = next((Path(p) for p in paths if Path(p).name == "virtiofsd"
                   and Path(p).is_file() and os.access(p, os.X_OK)), None)
    if daemon is None:
        raise RuntimeError("Cannot locate the installed virtiofsd executable.")
    # A retained O_PATH descriptor remains usable after host-side unlink/rename;
    # reopening an exportfs file handle can return ESTALE in that race.
    launcher = assets / "virtiofsd"
    launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(str(daemon))} --inode-file-handles=never "$@"\n')
    launcher.chmod(0o755)


def prepare_files(spec: Spec, user: pwd.struct_passwd) -> None:
    if spec.disk.exists(follow_symlinks=False) or spec.assets.exists(follow_symlinks=False):
        raise RuntimeError(f"Disk or assets already exist for {spec.name}; choose another name. No files overwritten.")
    spec.storage.mkdir(parents=True, exist_ok=True)
    spec.assets.mkdir(mode=0o755)
    prepare_backend(spec.assets)
    prepare_share(spec.share, user)
    run(["qemu-img", "create", "-f", "qcow2", "-o", "lazy_refcounts=on", str(spec.disk), "20G"])
    os.chown(spec.disk, user.pw_uid, user.pw_gid)
    os.chown(spec.assets, user.pw_uid, user.pw_gid)
    key = spec.assets / "ssh-key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"dusky-vm:{spec.name}", "-f", str(key)])
    for path in (key, key.with_suffix(".pub")):
        os.chown(path, user.pw_uid, user.pw_gid)


def agent(domain: str, command: str, arguments: dict | None = None, *, check: bool = True):
    request = {"execute": command}
    if arguments is not None:
        request["arguments"] = arguments
    result = virsh("qemu-agent-command", domain, "--timeout", "5", json.dumps(request),
                   check=check, timeout=15)
    if result.returncode:
        return None
    response = json.loads(result.stdout)
    if "error" in response:
        if check:
            raise RuntimeError(f"Guest agent: {response['error']}")
        return None
    return response.get("return")


def wait_agent(domain: str, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if agent(domain, "guest-ping", check=False) is not None:
            return
        time.sleep(2)
    raise RuntimeError(f"Guest agent unavailable after {seconds}s. In the guest install qemu-guest-agent, "
                       "start qemu-guest-agent.service, then re-run --configure " + domain)


def guest_exec(domain: str, script: str, seconds: int = 60) -> str:
    started = agent(domain, "guest-exec", {"path": "/usr/bin/python3", "arg": ["-c", script],
                                        "capture-output": True})
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = agent(domain, "guest-exec-status", {"pid": started["pid"]})
        if status.get("exited"):
            out = base64.b64decode(status.get("out-data", "")).decode("utf-8", "replace")
            err = base64.b64decode(status.get("err-data", "")).decode("utf-8", "replace")
            if status.get("exitcode", 1):
                raise RuntimeError(f"Guest setup failed: {err or out}")
            return out
        time.sleep(0.25)
    raise RuntimeError("Guest setup timed out.")


def bootstrap_script(public_key: str, guest_user: str | None) -> str:
    # This code runs in the supplied ISO's distro Python, currently 3.14.
    return f'''
import json, os, pwd, shutil, subprocess
from pathlib import Path
requested = {guest_user!r}
users = sorted((u for u in pwd.getpwall() if 1000 <= u.pw_uid < 65534 and u.pw_dir.startswith('/home/')), key=lambda u: u.pw_uid)
user = pwd.getpwnam(requested) if requested else (users[0] if users else pwd.getpwnam('root'))
sshdir = Path(user.pw_dir) / '.ssh'
sshdir.mkdir(mode=0o700, exist_ok=True)
authorized = sshdir / 'authorized_keys'
existing = authorized.read_text() if authorized.exists() else ''
key = {public_key.strip()!r}
if key not in existing.splitlines():
    authorized.write_text(existing.rstrip('\\n') + ('\\n' if existing else '') + key + '\\n')
os.chmod(sshdir, 0o700)
os.chmod(authorized, 0o600)
for path in (sshdir, authorized):
    os.chown(path, user.pw_uid, user.pw_gid)
if user.pw_uid == 0:
    conf = Path('/etc/ssh/sshd_config.d/00-dusky-vm.conf')
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text('PermitRootLogin prohibit-password\\nPubkeyAuthentication yes\\n')
subprocess.run(['sshd', '-t'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'sshd.service'], check=True, capture_output=True)
subprocess.run(['systemctl', 'reload', 'sshd.service'], check=True, capture_output=True)
mountpoint = Path('/media/host')
mountpoint.mkdir(parents=True, exist_ok=True)
fstab = Path('/etc/fstab')
text = fstab.read_text()
line = '{TAG} /media/host virtiofs defaults,nofail,x-systemd.automount 0 0'
if not any(l.split()[:2] == ['{TAG}', '/media/host'] for l in text.splitlines()):
    fstab.write_text(text.rstrip('\\n') + '\\n' + line + '\\n')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'start', 'media-host.automount'], check=True, capture_output=True)
with os.scandir(mountpoint):  # trigger automount without listing shared files
    pass
if not os.path.ismount(mountpoint):
    raise RuntimeError('VirtioFS did not mount at /media/host')
print(json.dumps({{'user': user.pw_name, 'uid': user.pw_uid, 'share': str(mountpoint),
                  'clipboard_tools': bool(shutil.which('wl-copy') and shutil.which('wl-paste')),
                  'host_key': Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().strip()}}))
'''


def handoff_script(public_key: str, guest_user: str | None) -> str:
    """Stage integration into an Arch installer's /mnt, without changing its installer.

    The supplied ISO auto-starts its installer. A small live-only service waits
    for a real mounted installation root; all actual guest configuration happens
    on its next boot, after the installer has finished creating users/fstab.
    """
    firstboot = '''
import subprocess
from pathlib import Path
packages = Path(__file__).parent / 'packages'
if not Path('/usr/bin/qemu-ga').exists():
    archives = sorted(str(p) for p in packages.glob('*.pkg.tar.zst'))
    if not archives:
        raise RuntimeError('qemu-guest-agent missing; install it from the ISO package repository')
    subprocess.run(['pacman', '-U', '--needed', '--noconfirm', *archives], check=True)
subprocess.run(['systemctl', 'start', 'qemu-guest-agent.service'], check=True)
''' + bootstrap_script(public_key, guest_user)
    unit = """[Unit]
Description=Dusky VM SSH and shared-folder integration
After=local-fs.target
ConditionVirtualization=kvm
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/dusky-vm/setup.py
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
"""
    watcher = f'''
import os, shutil, subprocess, time
from pathlib import Path
target = Path('/mnt')
deadline = time.monotonic() + 86400
while time.monotonic() < deadline:
    if os.path.ismount(target) and (target / 'etc/os-release').exists() and (target / 'usr/bin/python3').exists():
        dest = target / 'usr/local/lib/dusky-vm'
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'setup.py').write_text({firstboot!r})
        packages = dest / 'packages'
        packages.mkdir(exist_ok=True)
        repo = Path('/run/archiso/bootmnt/arch/repo')
        for name in ('qemu-guest-agent', 'liburing', 'numactl'):
            archives = sorted(repo.glob(name + '-[0-9]*.pkg.tar.zst'))
            if archives:
                shutil.copyfile(archives[-1], packages / archives[-1].name)
        units = target / 'etc/systemd/system'
        units.mkdir(parents=True, exist_ok=True)
        (units / 'dusky-vm-integration.service').write_text({unit!r})
        wants = units / 'multi-user.target.wants'
        wants.mkdir(exist_ok=True)
        link = wants / 'dusky-vm-integration.service'
        if not link.is_symlink():
            link.symlink_to('../dusky-vm-integration.service')
        break
    time.sleep(5)
else:
    raise RuntimeError('No installed Arch root found at /mnt in 24 hours')
'''
    return f'''
import subprocess
from pathlib import Path
if Path('/run/archiso').is_dir():
    Path('/run/dusky-vm-handoff.py').write_text({watcher!r})
    unit = Path('/run/systemd/system/dusky-vm-handoff.service')
    unit.write_text('[Unit]\\nDescription=Stage Dusky VM integration into the installed guest\\n[Service]\\nType=exec\\nExecStart=/usr/bin/python3 /run/dusky-vm-handoff.py\\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'restart', 'dusky-vm-handoff.service'], check=True)
    print('Installed-guest integration will be staged when the installer mounts /mnt.')
'''


def domain_config(domain: str) -> tuple[Path, Path]:
    root = ET.fromstring(virsh("dumpxml", "--inactive", domain).stdout)
    config = root.find(f"metadata/{{{NS}}}config")
    if config is None:
        raise RuntimeError(f"{domain} was not created by this Linux workflow.")
    return Path(config.attrib["assets"]), Path(config.attrib["share"])


def configure(domain: str, seconds: int, guest_user: str | None, user: pwd.struct_passwd) -> dict:
    assets, share = domain_config(domain)
    wait_agent(domain, seconds)
    setup = json.loads(guest_exec(domain, bootstrap_script((assets / "ssh-key.pub").read_text(), guest_user)))
    handoff = guest_exec(domain, handoff_script((assets / "ssh-key.pub").read_text(), guest_user))
    if handoff.strip():
        print(handoff.strip())
    deadline = time.monotonic() + 30
    address = None
    while time.monotonic() < deadline:
        for nic in agent(domain, "guest-network-get-interfaces"):
            if nic.get("name") == "lo":
                continue
            for item in nic.get("ip-addresses", []):
                ip = ipaddress.ip_address(item["ip-address"])
                if ip.version == 4 and not ip.is_loopback and not ip.is_link_local:
                    address = str(ip)
                    break
            if address:
                break
        if address:
            break
        time.sleep(1)
    if not address:
        raise RuntimeError("Guest has no usable IPv4 address yet; re-run --configure after DHCP.")
    setup.update(domain=domain, address=address, assets=str(assets), share_host=str(share))
    known = assets / "known_hosts"
    known.write_text(f"{address} {setup['host_key']}\n")
    os.chown(known, user.pw_uid, user.pw_gid)
    # The key belongs to this VM, avoiding interference with the operator's SSH keys.
    prefix = ["sudo", "-u", user.pw_name, "--"] if os.geteuid() == 0 else []
    result = run([*prefix, *ssh_command(setup), "true"], check=False)
    if result.returncode:
        raise RuntimeError(f"Guest configured, but SSH verification failed:\n{result.stderr}")
    path = assets / "connection.json"
    path.write_text(json.dumps(setup, indent=2) + "\n")
    os.chown(path, user.pw_uid, user.pw_gid)
    print(f"SSH verified: {shlex.join(ssh_command(setup))}")
    print(f"Shared folder: {share} -> /media/host")
    print(f"After installing/rebooting: {ENTRY} --configure {domain}")
    return setup


def ssh_command(config: dict) -> list[str]:
    assets = Path(config["assets"])
    return ["ssh", "-i", str(assets / "ssh-key"), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={assets / 'known_hosts'}", f"{config['user']}@{config['address']}"]


def launch_viewer(domain: str, interpreter: str | None, user: pwd.struct_passwd) -> None:
    assets, _share = domain_config(domain)
    cache = assets / "cache"
    cache.mkdir(exist_ok=True)
    os.chown(cache, user.pw_uid, user.pw_gid)
    argv = ["env", f"XDG_CACHE_HOME={cache}", *tool("virt-manager", interpreter),
            "--connect", URI, "--show-domain-console", domain]
    # Launch in the calling desktop session, never root's desktop session.
    if os.geteuid() == 0:
        argv = ["sudo", "-u", user.pw_name, "--", "env", f"HOME={user.pw_dir}",
                f"XDG_RUNTIME_DIR=/run/user/{user.pw_uid}",
                *([f"WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}"] if os.environ.get("WAYLAND_DISPLAY") else []),
                "GDK_BACKEND=wayland", *argv]
    log = assets / "viewer.log"
    with log.open("a") as output:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 start_new_session=True)
    time.sleep(1)
    if child.poll() not in (None, 0):
        print(f"virt-manager could not open; see {log}. VM remains running.")


def clipboard(domain: str) -> int:
    assets, _share = domain_config(domain)
    return subprocess.run([sys.executable, str(CLIPBOARD_MANAGER), "enable", domain,
                           "--from-json", str(assets / "connection.json")], check=False).returncode


def start_clipboard(domain: str, config: dict, user: pwd.struct_passwd) -> None:
    runtime = f"/run/user/{user.pw_uid}"
    prefix = ["sudo", "-u", user.pw_name, "--"] if os.geteuid() == 0 else []
    argv = [*prefix, "env", f"HOME={user.pw_dir}",
            f"XDG_RUNTIME_DIR={runtime}",
            *([f"WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}"] if os.environ.get("WAYLAND_DISPLAY") else []),
            sys.executable, str(CLIPBOARD_MANAGER), "enable", domain, "--from-json",
            str(Path(config["assets"]) / "connection.json")]
    print(run(argv).stdout)


def boot_installed(domain: str, seconds: int) -> None:
    domain_config(domain)  # limit this operation to VMs created by this workflow
    if domain in virsh("list", "--name").stdout.splitlines():
        virsh("shutdown", domain, "--mode", "agent,acpi")
        deadline = time.monotonic() + seconds
        while domain in virsh("list", "--name").stdout.splitlines():
            if time.monotonic() >= deadline:
                raise RuntimeError("Guest did not shut down; complete shutdown in virt-manager, then retry.")
            time.sleep(1)
    root = ET.fromstring(virsh("dumpxml", "--inactive", domain).stdout)
    source = root.find("devices/disk[@device='cdrom']/source")
    if source is not None and source.attrib:
        virsh("change-media", domain, "sda", "--eject", "--config")
    virsh("start", domain)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    action = result.add_mutually_exclusive_group()
    result.add_argument("--iso", help="Arch/Dusky ISO; blank discovers dusky*.iso under /mnt/zram1")
    result.add_argument("--name", default="dusky-linux")
    result.add_argument("--storage", help="disk directory; default /var/lib/libvirt/images")
    result.add_argument("--share-dir", help="host shared directory; default /mnt/zram1 (RAM-backed)")
    result.add_argument("--memory", type=int, default=4096, metavar="MiB")
    result.add_argument("--vcpus", type=int, default=min(4, os.process_cpu_count() or 1))
    result.add_argument("--network", default="default")
    result.add_argument("--graphics", choices=("auto", "2d", "3d"), default="2d",
                        help="2d by default; opt into accelerated rendering with 3d or auto")
    result.add_argument("--rendernode", help="explicit render device for 3D")
    result.add_argument("--tool-python", help="optional interpreter for distro virt-install/virt-manager launchers")
    result.add_argument("--yes", action="store_true", help="use defaults without prompting")
    action.add_argument("--dump-only", action="store_true", help="generate XML without writing files or changing libvirt")
    result.add_argument("--no-viewer", action="store_true")
    result.add_argument("--wait", type=int, default=180, metavar="SECONDS", help="guest-agent startup budget")
    action.add_argument("--configure", metavar="DOMAIN", help="configure SSH/share in a running installed or live guest")
    action.add_argument("--boot-installed", metavar="DOMAIN", help="after installation: shut down, eject ISO, boot disk and configure integration")
    result.add_argument("--guest-user", help="SSH account; default first regular guest user, or root on live ISO")
    action.add_argument("--clipboard", metavar="DOMAIN", help="run bidirectional native Wayland text clipboard sharing")
    action.add_argument("--ssh", metavar="DOMAIN", help="open SSH using this VM's generated key and discovered account")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if sys.version_info < (3, 15):
            raise RuntimeError("Python 3.15+ is required for the deployment script.")
        if args.wait < 1:
            raise RuntimeError("--wait must be positive.")
        user = operator()
        if args.clipboard or args.ssh:
            if os.geteuid() == 0:
                os.setgroups(os.getgrouplist(user.pw_name, user.pw_gid))
                os.setgid(user.pw_gid)
                os.setuid(user.pw_uid)
                os.environ.update(HOME=user.pw_dir, XDG_RUNTIME_DIR=f"/run/user/{user.pw_uid}")
            if args.clipboard:
                return clipboard(args.clipboard)
            assets, _share = domain_config(args.ssh)
            config = json.loads((assets / "connection.json").read_text())
            return subprocess.run(ssh_command(config), check=False).returncode
        if args.configure:
            config = configure(args.configure, args.wait, args.guest_user, user)
            start_clipboard(args.configure, config, user)
            return 0
        if args.boot_installed:
            boot_installed(args.boot_installed, args.wait)
            if not args.no_viewer:
                launch_viewer(args.boot_installed, args.tool_python, user)
            config = configure(args.boot_installed, args.wait, args.guest_user, user)
            start_clipboard(args.boot_installed, config, user)
            return 0
        if not args.dump_only:
            elevate()
        for binary in ("virsh", "qemu-img", "findmnt", "setfacl", "ssh", "ssh-keygen"):
            if not shutil.which(binary):
                raise RuntimeError(f"Missing command: {binary}")
        if not Path("/dev/kvm").exists():
            raise RuntimeError("/dev/kvm is unavailable; hardware virtualization is required.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,49}", args.name):
            raise RuntimeError("Use a VM name of 1–50 letters, digits, dots, underscores, plus or hyphen.")
        if args.memory < 2048 or args.memory * (1 << 20) + 4 * GIB > memory_available():
            raise RuntimeError("Guest needs at least 2048 MiB and must leave 4 GiB available for the host.")
        if not 1 <= args.vcpus <= (os.process_cpu_count() or 1):
            raise RuntimeError("vCPUs must be positive and no more than available host CPUs.")
        iso = ask_iso(args.iso, Path(user.pw_dir), args.yes)
        storage = choose_storage(args.storage, args.memory, args.yes, Path(user.pw_dir))
        share = choose_share(args.share_dir, Path(user.pw_dir), args.yes)
        spec = Spec(args.name, iso, storage, share, args.memory, args.vcpus,
                    args.network, select_rendernode(args.graphics, args.rendernode))
        if args.name in virsh("list", "--all", "--name").stdout.splitlines():
            raise RuntimeError(f"Domain {args.name} already exists; use --configure or choose another name.")
        # virt-install logs verbosely even for --print-xml. Keep its temporary
        # cache in shared RAM and remove it, including during previews.
        with tempfile.TemporaryDirectory(prefix="dusky-virt-install-", dir="/dev/shm") as cache:
            baseline = run(["env", f"XDG_CACHE_HOME={cache}", *baseline_command(spec, args.tool_python)]).stdout
        xml = compose_xml(spec, baseline)
        if args.dump_only:
            print(xml, end="")
            return 0
        ensure_network(spec.network)
        prepare_files(spec, user)
        xml_path = spec.assets / "domain.xml"
        xml_path.write_text(xml)
        virsh("define", str(xml_path), "--validate")
        virsh("start", spec.name)
        print(f"Started {spec.name}: UEFI/KVM, {args.memory} MiB, {args.vcpus} vCPUs, 20 GiB qcow2.")
        print(f"Disk: {spec.disk}; ISO: {spec.iso}")
        if filesystem_type(storage) in {"tmpfs", "ramfs"}:
            print("RAM-backed VM: disk and firmware variables disappear when this filesystem is cleared.")
        if not args.no_viewer:
            launch_viewer(spec.name, args.tool_python, user)
        config = configure(spec.name, args.wait, args.guest_user, user)
        start_clipboard(spec.name, config, user)
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Any VM already started is left available in virt-manager.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
