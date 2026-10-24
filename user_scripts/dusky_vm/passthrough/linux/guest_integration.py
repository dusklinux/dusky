#!/usr/bin/env python3
"""Configure a Linux VM's virtiofs share and optional native Wayland clipboard.

Run after creating the VM. Host setup runs as the desktop user; only package
installation and guest system configuration use sudo. No passwords are stored.
The guest must run Arch Linux and import its Wayland environment into its
systemd user manager (as the Dusky Hyprland session already does).
"""

import argparse
import hashlib
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

URI = "qemu:///system"
UNIT_BANNER = "# Managed by linux/guest_integration.py\n"
RECIPE_FILES = ("PKGBUILD", "Cargo.lock", "clipboard-owner.patch", "dusky_vdagent.service")


def run(argv: list[str], *, capture: bool = False, timeout: float = 300,
        env: dict[str, str] | None = None) -> str:
    result = subprocess.run(argv, text=True, capture_output=capture, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f"Command failed: {shlex.join(argv)}\n{result.stderr or ''}")
    return (result.stdout or "").strip()


def write_changed(path: Path, content: str) -> None:
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def configure_xml(original: str, share: Path, tag: str) -> str:
    root = ET.fromstring(original)
    backing = root.find("memoryBacking")
    if backing is None:
        backing = ET.Element("memoryBacking")
        root.insert(list(root).index(root.find("vcpu")), backing)
    source = backing.find("source")
    if source is None:
        source = ET.SubElement(backing, "source", type="memfd")
    elif source.get("type") not in ("memfd", "file"):
        raise RuntimeError("Existing memory backing is incompatible with virtiofs.")
    access = backing.find("access")
    if access is None:
        access = ET.SubElement(backing, "access")
    access.set("mode", "shared")

    devices = root.find("devices")
    if devices is None:
        raise RuntimeError("Domain XML has no devices element.")
    shares = devices.findall(f"filesystem/target[@dir='{tag}']/..")
    if len(shares) > 1:
        raise RuntimeError(f"Duplicate virtiofs tag: {tag}")
    if shares:
        filesystem = shares[0]
        driver = filesystem.find("driver")
        if driver is None or driver.get("type") != "virtiofs":
            raise RuntimeError(f"Tag {tag} is already used by another filesystem driver.")
        source = filesystem.find("source")
        if source is None:
            raise RuntimeError(f"Filesystem {tag} has no source directory.")
        source.set("dir", str(share))
    else:
        filesystem = ET.SubElement(devices, "filesystem", type="mount", accessmode="passthrough")
        ET.SubElement(filesystem, "driver", type="virtiofs", queue="1024")
        ET.SubElement(filesystem, "source", dir=str(share))
        ET.SubElement(filesystem, "target", dir=tag)

    graphics = devices.find("graphics[@type='spice']")
    if graphics is not None:
        clipboard = graphics.find("clipboard")
        if clipboard is not None:
            clipboard.set("copypaste", "yes")
        if devices.find("channel/target[@name='com.redhat.spice.0']") is None:
            channel = ET.SubElement(devices, "channel", type="spicevmc")
            ET.SubElement(channel, "target", type="virtio", name="com.redhat.spice.0")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def guest_build(args: argparse.Namespace) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Build as the guest desktop user, not root.")
    recipe = Path(args.recipe).resolve(strict=True)
    config = Path(args.makepkg_config).resolve(strict=True)
    settings = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "dusky/settings/dusky_vm/guest_integration"
    settings.mkdir(parents=True, exist_ok=True)
    run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "base-devel", "rust", "pkgconf",
         "patch", "mold", "ccache", "spice-vdagent", "wl-clipboard", "wayland"], timeout=3600)
    fingerprint = hashlib.sha256()
    for path in [*(recipe / name for name in RECIPE_FILES), config]:
        fingerprint.update(path.read_bytes())
    fingerprint.update(run(["rustc", "--print", "cfg", "-C", "target-cpu=native"], capture=True).encode())
    fingerprint.update(run(["rustc", "--version"], capture=True).encode())
    marker = settings / "build.sha256"
    installed = subprocess.run(["pacman", "-Qq", "dusky-wayland-vdagent"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    command = ["sudo", "--", "python", str(Path(__file__).resolve()), "guest", "--user", args.user,
               "--tag", args.tag, "--mount-path", args.mount_path]
    if installed and not args.rebuild_clipboard and marker.exists() and marker.read_text().strip() == fingerprint.hexdigest():
        print("Native clipboard build is current; skipping compilation.", flush=True)
        run([*command, "--enable-clipboard"])
        return
    mount_info = run(["findmnt", "-T", "/tmp", "-n", "-o", "FSTYPE,OPTIONS"], capture=True).split()
    ram_build = mount_info[0] == "tmpfs" and "noexec" not in mount_info[1].split(",")
    parent = Path("/tmp") if ram_build else settings
    with tempfile.TemporaryDirectory(prefix="dusky-vdagent-build-", dir=parent) as temporary:
        work = Path(temporary)
        for name in RECIPE_FILES:
            shutil.copy2(recipe / name, work / name)
        # --config replaces the system configuration: retain its normal defaults
        # and drop-ins before applying the supplied per-user override layer.
        wrapper = work / "makepkg.conf"
        wrapper.write_text("source /etc/makepkg.conf\n"
                           "for dusky_conf in /etc/makepkg.conf.d/*.conf; do\n"
                           "  [[ -f $dusky_conf ]] && source \"$dusky_conf\"\n"
                           "done\nsource " + shlex.quote(str(config)) + "\n")
        env = os.environ.copy()
        env.pop("CARGO_TARGET_DIR", None)
        for name in ("BUILDDIR", "SRCDEST", "PKGDEST", "SRCPKGDEST", "LOGDEST", "CARGO_HOME", "CCACHE_DIR"):
            destination = work / name.lower()
            destination.mkdir()
            env[name] = str(destination)
        if args.build_inputs:
            inputs = Path(args.build_inputs).resolve(strict=True)
            for source, destination in (("sources", "SRCDEST"), ("cargo", "CARGO_HOME")):
                shutil.copytree(inputs / source, env[destination], dirs_exist_ok=True)
            env["CARGO_NET_OFFLINE"] = "true"
        log = settings / "build.log"
        print(f"Building for the guest CPU in {work}; log: {log}", flush=True)
        with log.open("w") as handle:
            result = subprocess.run(["makepkg", "--config", str(wrapper), "--dir", str(work),
                                     "--clean", "--noconfirm"], env=env, stdout=handle,
                                    stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Native clipboard build failed; see {log}")
        packages = run(["makepkg", "--config", str(wrapper), "--dir", str(work), "--packagelist"],
                       capture=True, env=env).splitlines()
        if len(packages) != 1 or not Path(packages[0]).is_file():
            raise RuntimeError(f"Expected one compiled clipboard package; see {log}")
        run([*command, "--clipboard-package", packages[0], "--enable-clipboard"])
        run(["wayland-vdagent", "--version"])
        write_changed(marker, fingerprint.hexdigest() + "\n")
    print("Native build installed; temporary sources, caches and package removed.", flush=True)


def guest_setup(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Guest setup must run through sudo.")
    operator = pwd.getpwnam(args.user)
    home = Path(operator.pw_dir)
    mount = Path(args.mount_path)
    if str(mount).startswith("~/"):
        mount = home / str(mount)[2:]
    elif not mount.is_absolute():
        mount = home / mount
    mount = mount.resolve()
    if not mount.exists():
        mount.mkdir(parents=True, exist_ok=True)
        os.chown(mount, operator.pw_uid, operator.pw_gid)
    unit = run(["systemd-escape", "--path", "--suffix=mount", str(mount)], capture=True)
    automount = unit.removesuffix(".mount") + ".automount"
    # Escape systemd specifiers and characters with special meaning in unit values.
    where = str(mount).replace("\\", "\\x5c").replace("%", "%%").replace("\n", "\\x0a")
    write_changed(Path("/etc/systemd/system") / unit, UNIT_BANNER + f"""[Unit]
Description=Host shared folder ({args.tag})

[Mount]
What={args.tag}
Where={where}
Type=virtiofs
Options=rw
TimeoutSec=15
""")
    write_changed(Path("/etc/systemd/system") / automount, UNIT_BANNER + f"""[Unit]
Description=Automount host shared folder ({args.tag})

[Automount]
Where={where}

[Install]
WantedBy=multi-user.target
""")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", automount])

    if args.clipboard_package:
        run(["pacman", "-S", "--needed", "--noconfirm", "spice-vdagent", "wl-clipboard"])
        run(["pacman", "-U", "--noconfirm", args.clipboard_package])
    if args.clipboard_package or args.enable_clipboard:
        run(["systemctl", "--global", "mask", "spice-vdagent.service"])
        # Per-user enablement lets the service TUI disable startup normally.
        # Remove the global enablement used by earlier integration deployments.
        run(["systemctl", "--global", "disable", "dusky_vdagent.service"])
        run(["runuser", "-u", args.user, "--", "systemctl", "--user", "--no-reload",
             "enable", "dusky_vdagent.service"])
        # The package's udev rule starts this static socket on subsequent boots.
        run(["systemctl", "start", "spice-vdagentd.socket"])
        runtime = run(["loginctl", "show-user", args.user, "--property=RuntimePath", "--value"], capture=True)
        if runtime:
            userctl = ["runuser", "-u", args.user, "--", "env", f"XDG_RUNTIME_DIR={runtime}",
                       f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus", "systemctl", "--user"]
            run([*userctl, "daemon-reload"])
            run([*userctl, "stop", "spice-vdagent.service"])
            if subprocess.run([*userctl, "is-active", "--quiet", "graphical-session.target"]).returncode == 0:
                run([*userctl, "restart" if args.clipboard_package else "start", "dusky_vdagent.service"])
    print(f"Guest share prepared: {mount}")


def host_setup(args: argparse.Namespace) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run host setup as your desktop user; it invokes sudo where needed.")
    for command in ("ssh", "scp", "virsh", "pacman"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Missing command: {command}")
    share = Path(args.share).expanduser().resolve(strict=True)
    if not share.is_dir():
        raise RuntimeError(f"Share is not a directory: {share}")
    if not args.tag or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.tag):
        raise RuntimeError("Share tag must contain only letters, numbers, underscores and hyphens.")
    package = None
    if not args.no_clipboard:
        if args.clipboard_package:
            package = Path(args.clipboard_package).resolve(strict=True)
        else:
            recipe = Path(__file__).resolve().parents[1] / "clipboard/native_agent"
            for name in RECIPE_FILES:
                (recipe / name).resolve(strict=True)
            config = Path(args.makepkg_config or (Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "pacman/makepkg.conf")).expanduser().resolve(strict=True)
    virsh = ["virsh", "-c", URI]
    original = run([*virsh, "dumpxml", "--inactive", args.domain], capture=True)
    root = ET.fromstring(original)
    if not args.no_clipboard and root.find("devices/graphics[@type='spice']") is None:
        raise RuntimeError("Clipboard integration requires this VM to have a SPICE display.")
    updated = configure_xml(original, share, args.tag)
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "dusky/vm-integration" / root.findtext("uuid")
    state.mkdir(parents=True, exist_ok=True)
    # Retain the first definition, rather than replacing the backup on reruns.
    backup = state / "before.xml"
    if not backup.exists():
        write_changed(backup, original + "\n")
    definition = state / "configured.xml"
    write_changed(definition, updated)
    running = root.findtext("uuid") in run([*virsh, "list", "--uuid"], capture=True).splitlines()
    needs_restart = False
    if running:
        live = ET.fromstring(run([*virsh, "dumpxml", args.domain], capture=True))
        ET.indent(live, space="  ")
        live_xml = ET.tostring(live, encoding="unicode") + "\n"
        needs_restart = configure_xml(live_xml, share, args.tag) != live_xml
    if subprocess.run(["pacman", "-Qq", "virtiofsd"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "virtiofsd"])

    with tempfile.TemporaryDirectory(prefix="dusky-vm-") as temporary:
        ssh = ["ssh", "-o", "ControlMaster=auto", "-o", "ControlPersist=300",
               "-o", f"ControlPath={temporary}/ssh", "-o", "StrictHostKeyChecking=accept-new"]
        scp = ["scp", "-q", *ssh[1:]]
        remote = None
        try:
            run([*ssh, args.guest, "true"])
            guest_user = run([*ssh, args.guest, "id -un"], capture=True)
            remote = run([*ssh, args.guest, "mktemp -d /tmp/dusky-integration.XXXXXXXX"], capture=True)
            run([*scp, str(Path(__file__).resolve()), f"{args.guest}:{remote}/setup.py"])
            if package:
                run([*scp, str(package), f"{args.guest}:{remote}/clipboard.pkg.tar.zst"])
            elif not args.no_clipboard:
                run([*ssh, args.guest, shlex.join(["mkdir", f"{remote}/recipe"])])
                run([*scp, *(str(recipe / name) for name in RECIPE_FILES), f"{args.guest}:{remote}/recipe/"])
                run([*scp, str(config), f"{args.guest}:{remote}/user-makepkg.conf"])
                if args.build_inputs:
                    inputs = Path(args.build_inputs).expanduser().resolve(strict=True)
                    run([*scp, "-r", str(inputs), f"{args.guest}:{remote}/build-inputs"], timeout=3600)
            command = ["python", f"{remote}/setup.py", "build" if not args.no_clipboard and not package else "guest",
                       "--user", guest_user, "--tag", args.tag, "--mount-path", args.mount_path]
            if package:
                command += ["--clipboard-package", f"{remote}/clipboard.pkg.tar.zst"]
            if not args.no_clipboard and not package:
                command += ["--recipe", f"{remote}/recipe", "--makepkg-config", f"{remote}/user-makepkg.conf"]
                if args.rebuild_clipboard:
                    command += ["--rebuild-clipboard"]
                if args.build_inputs:
                    command += ["--build-inputs", f"{remote}/build-inputs"]
            else:
                command = ["sudo", "--", *command]
            run([*ssh, "-t", args.guest, shlex.join(command)], timeout=3600)
            run([*virsh, "define", "--validate", str(definition)])
            write_changed(state / "settings.json", json.dumps({"guest": args.guest,
                          "share": str(share), "tag": args.tag, "mount_path": args.mount_path}, indent=2) + "\n")
            run([*ssh, args.guest, shlex.join(["rm", "-rf", "--", remote])])
            remote = None
            if args.restart and running:
                run([*virsh, "shutdown", args.domain, "--mode", "acpi"])
                deadline = time.monotonic() + 120
                while root.findtext("uuid") in run([*virsh, "list", "--uuid"], capture=True).splitlines():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Guest did not shut down in 120s; it was not forcibly stopped.")
                    time.sleep(1)
                run([*virsh, "start", args.domain])
            elif needs_restart:
                print("Shared memory/device changes take effect after a full guest shutdown/start.")
        finally:
            if remote:
                subprocess.run([*ssh, args.guest, shlex.join(["rm", "-rf", "--", remote])], timeout=30)
            subprocess.run([*ssh, "-O", "exit", args.guest], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Configured {args.domain}: {share} → guest {args.mount_path}")
    print(f"Original definition: {backup}")
    if args.no_clipboard:
        print("Clipboard unchanged (--no-clipboard).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    host = sub.add_parser("host", help="configure a VM and its guest through SSH")
    host.add_argument("--domain", required=True)
    host.add_argument("--guest", required=True, help="SSH destination, e.g. user@guest-address")
    host.add_argument("--share", required=True, help="host directory, without a wildcard")
    host.add_argument("--restart", action="store_true", help="fully shut down/start a running guest")
    host.add_argument("--no-clipboard", action="store_true", help="configure file sharing only")
    host.add_argument("--makepkg-config", help="per-user makepkg override (default: host XDG pacman/makepkg.conf)")
    host.add_argument("--rebuild-clipboard", action="store_true", help="rebuild even when the native guest build is current")
    host.add_argument("--build-inputs", help="offline source inputs directory containing sources/ and cargo/")
    guest = sub.add_parser("guest", help="configure guest locally as root")
    guest.add_argument("--user", required=True, help="guest desktop user")
    guest.add_argument("--enable-clipboard", action="store_true", help="enable an already installed native clipboard package")
    build = sub.add_parser("build", help="build and install the clipboard agent as the guest desktop user")
    build.add_argument("--user", required=True)
    build.add_argument("--recipe", required=True)
    build.add_argument("--makepkg-config", required=True)
    build.add_argument("--rebuild-clipboard", action="store_true")
    build.add_argument("--build-inputs")
    for command in (host, guest, build):
        command.add_argument("--tag", default="dusky_shared")
        command.add_argument("--mount-path", default="Documents/a_host", help="absolute path or path relative to guest home")
        command.add_argument("--clipboard-package", help="offline dusky-wayland-vdagent package")
    args = parser.parse_args()
    if sys.version_info < (3, 14, 7):
        parser.error("Python 3.14.7+ is required.")
    try:
        {"host": host_setup, "guest": guest_setup, "build": guest_build}[args.mode](args)
    except (RuntimeError, OSError, subprocess.SubprocessError, ET.ParseError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
