#!/usr/bin/env python3
"""Configure shared memory and libvirt devices for the current Looking Glass IDD.

Target: Arch Linux, Python 3.15+, matching development client and guest builds.
Uses native ivshmem-plain and /dev/shm/looking-glass; initializes file permissions
without replacing active mappings, and preserves the VM's CPU configuration.
"""

import argparse
import grp
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 15, 0)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2
QEMU_NS = "http://libvirt.org/schemas/domain/qemu/1.0"
SHM_NAME = "looking-glass"
SHM_PATH = Path("/dev/shm") / SHM_NAME
TMPFILES = Path("/etc/tmpfiles.d/10-looking-glass.conf")


def _hard_exit(msg: str) -> Never:
    sys.stderr.write(f"\n[FATAL] {msg}\n\n")
    raise SystemExit(1)


def elevate() -> None:
    if os.geteuid() == 0:
        return
    if os.environ.get("ARSONIX_ELEVATED") == "1":
        _hard_exit("Re-exec loop detected: sudo did not yield uid 0.")
    sudo = shutil.which("sudo")
    if sudo is None:
        _hard_exit("'sudo' is not in PATH. Re-run this script as root.")
    try:
        script = Path(sys.argv[0]).resolve(strict=True)
    except OSError as exc:
        _hard_exit(f"Cannot resolve own script path: {exc}")
    sys.stderr.write("\n[INFO] Elevating via sudo (single re-exec)...\n")
    os.environ["ARSONIX_ELEVATED"] = "1"
    os.execv(
        sudo,
        [sudo, "--preserve-env=ARSONIX_ELEVATED", "--", sys.executable, str(script), *sys.argv[1:]],
    )


if sys.version_info[:3] < MIN_PY:
    _hard_exit("Python 3.15+ required.")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt, Prompt
    from rich.table import Table
except ModuleNotFoundError:
    _hard_exit("python-rich is missing. Run Phase 1 (05_virtio_iso.py) first.")

console = Console(force_terminal=True, force_interactive=True)


# ==============================================================================
# SHARED PRIMITIVES
# ==============================================================================
@dataclass(frozen=True, slots=True)
class Cmd:
    argv: list[str]
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def run(argv: list[str], *, check: bool = False, timeout: float = 120.0) -> Cmd:
    try:
        proc = subprocess.run(
            argv, check=False, timeout=timeout, text=True,
            capture_output=True, stdin=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
        res = Cmd(argv, proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip())
    except subprocess.TimeoutExpired:
        res = Cmd(argv, 124, "", f"timeout after {timeout}s")
    except FileNotFoundError:
        res = Cmd(argv, 127, "", f"executable not found: {argv[0]}")
    if check and not res.ok:
        bail(f"Command failed (rc={res.code}): {' '.join(argv)}\n{res.err or res.out}")
    return res


def bail(msg: str) -> Never:
    console.print(Panel(f"[bold red]FATAL[/bold red]\n{msg}", border_style="red"))
    raise SystemExit(1)


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> bool:
    keep_mode = 0o644 if mode is None else mode
    uid, gid = 0, 0
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except (UnicodeDecodeError, OSError):
            pass
        st = path.stat()
        keep_mode = stat.S_IMODE(st.st_mode) if mode is None else mode
        uid, gid = st.st_uid, st.st_gid
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".arsonix")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, keep_mode)
        os.chown(tmp, uid, gid)
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def state_load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"schema": STATE_SCHEMA}
    return data if isinstance(data, dict) else {"schema": STATE_SCHEMA}


def state_merge(**kv: object) -> None:
    data = state_load()
    data.update(kv)
    data["schema"] = STATE_SCHEMA
    data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write(STATE_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o644)


def resolve_operator() -> pwd.struct_passwd:
    cached = state_load().get("human_user")
    candidates = [str(cached)] if cached else []
    for key in ("SUDO_USER", "DOAS_USER"):
        val = os.environ.get(key, "").strip()
        if val:
            candidates.append(val)
    try:
        candidates.append(os.getlogin())
    except OSError:
        pass
    for name in candidates:
        if not name or name == "root":
            continue
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        if entry.pw_uid >= 1000:
            return entry
    while True:
        name = Prompt.ask("[bold cyan]Non-root Arch username[/bold cyan]").strip()
        try:
            return pwd.getpwnam(name)
        except KeyError:
            console.print(f"[red]  x '{name}' is not in the passwd database.[/red]")


def virsh(args: list[str], *, timeout: float = 60.0) -> Cmd:
    return run(["virsh", "-c", "qemu:///system", *args], timeout=timeout)


# ==============================================================================
# PACKAGES
# ==============================================================================
def installed(pkg: str) -> bool:
    return run(["pacman", "-Qq", pkg], timeout=20).ok


def in_repos(pkg: str) -> bool:
    return run(["pacman", "-Si", pkg], timeout=30).ok


def current_client_available() -> bool:
    client = shutil.which("looking-glass-client")
    if client is None:
        return False
    # --help prints to stderr and may return nonzero; inspect the option list.
    help_result = run([client, "--help"], timeout=15)
    options = help_result.out + "\n" + help_result.err
    return all(name in options for name in (
        "lgmp:shmDevice", "spice:enable", "spice:host", "spice:port",
        "spice:usbAudio", "wayland:fractionScale",
    ))


def install_packages(pkgs: list[str], operator: pwd.struct_passwd) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Looking Glass client toolchain[/bold]")
    missing = [p for p in pkgs if not installed(p)]
    if not missing:
        console.print("[bold green]  ok[/bold green] All packages already installed.")
        return
    repo = [p for p in missing if in_repos(p)]
    aur = [p for p in missing if p not in repo]
    if repo:
        proc = subprocess.run(["pacman", "-S", "--needed", "--noconfirm", *repo],
                              check=False, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            bail(f"pacman failed for: {' '.join(repo)}")
        console.print(f"[bold green]  ok[/bold green] repo: {' '.join(repo)}")
    if aur:
        paru = shutil.which("paru")
        if paru is None:
            bail(f"'paru' is required for AUR packages: {' '.join(aur)}")
        proc = subprocess.run(
            ["sudo", "-u", operator.pw_name, "--", paru, "-S", "--needed", "--noconfirm",
             "--skipreview", "--removemake", *aur],
            check=False,
        )
        if proc.returncode != 0:
            bail(f"paru failed for: {' '.join(aur)}")
        console.print(f"[bold green]  ok[/bold green] aur: {' '.join(aur)}")


# ==============================================================================
# SHARED MEMORY SIZING
# ==============================================================================
def shm_bytes_for(width: int, height: int) -> int:
    """
    Current Looking Glass IDD sizing rule (triple-buffered):
    (ceil(width * 4 / 256) * 256) * height * 3 + (4 * 1024 * 1024),
    rounded UP to the next power of two.
    ivshmem-plain requires a power-of-two region, so the rounding is mandatory.
    """
    if width <= 0 or height <= 0:
        raise ValueError("Width and height must be positive.")
    stride = ((width * 4 + 255) // 256) * 256
    raw = stride * height * 3 + (4 << 20)
    return 1 << (raw - 1).bit_length()


def choose_shm(explicit_mib: int | None) -> tuple[int, int]:
    if explicit_mib is not None:
        if explicit_mib <= 0:
            bail("--shm-mib must be positive.")
        size = explicit_mib << 20
        if size & (size - 1):
            bail(f"--shm-mib must be a power of two, got {explicit_mib}.")
        return explicit_mib, size

    presets = [("1", "1920x1080", 1920, 1080), ("2", "2560x1440", 2560, 1440),
               ("3", "3840x2160", 3840, 2160)]
    table = Table(title="Shared-memory sizing (IDD, triple-buffered)", header_style="bold magenta")
    table.add_column("Opt", style="cyan", justify="center")
    table.add_column("Guest resolution", style="green")
    table.add_column("Frame buffer (x3)", style="dim")
    table.add_column("Region (power of two)", style="bold yellow")
    for opt, label, width, height in presets:
        total = shm_bytes_for(width, height)
        stride = ((width * 4 + 255) // 256) * 256
        table.add_row(opt, label, f"{stride * height * 3 / (1 << 20):.1f} MiB + 4 MiB",
                      f"{total >> 20} MiB")
    table.add_row("4", "custom", "-", "computed")
    console.print(table)

    choice = Prompt.ask("[bold cyan]Target[/bold cyan]", choices=["1", "2", "3", "4"], default="2")
    if choice == "4":
        width = IntPrompt.ask("Width", default=3440)
        height = IntPrompt.ask("Height", default=1440)
    else:
        _, _, width, height = presets[int(choice) - 1]
    if width <= 0 or height <= 0:
        bail("Width and height must be positive.")
    size = shm_bytes_for(width, height)
    console.print(f"[bold green]  ok[/bold green] {width}x{height} -> {size >> 20} MiB ({size} B)")
    return size >> 20, size


# ==============================================================================
# HOST SHM STAGING
# ==============================================================================
def configure_tmpfiles(operator: pwd.struct_passwd) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]systemd-tmpfiles staging[/bold]")
    payload = (
        "# Managed by Arsonix (Phase 5).\n"
        "# 'f' creates the file if absent and never truncates an existing one, so a\n"
        "# running guest keeps its mapping across a tmpfiles --create.\n"
        f"f /dev/shm/{SHM_NAME} 0660 {operator.pw_name} kvm - -\n"
    )
    if atomic_write(TMPFILES, payload, mode=0o644):
        console.print(f"[green]  ~[/green] {TMPFILES}")
    else:
        console.print(f"[bold green]  ok[/bold green] {TMPFILES} already convergent.")
    res = run(["systemd-tmpfiles", "--create", str(TMPFILES)], timeout=60)
    if not res.ok:
        bail(f"systemd-tmpfiles failed: {res.err or res.out}")


def require_shm_stopped() -> None:
    """Do not replace/resize a region used by any active libvirt domain."""
    active = virsh(["list", "--name"])
    if not active.ok:
        bail(f"Cannot check active domains: {active.err or active.out}")
    for name in active.out.splitlines():
        if not name:
            continue
        dump = virsh(["dumpxml", name])
        if not dump.ok:
            bail(f"Cannot inspect active domain '{name}': {dump.err or dump.out}")
        root = ET.fromstring(dump.out)
        native = root.find(f"./devices/shmem[@name='{SHM_NAME}']")
        legacy = root.find(f"{{{QEMU_NS}}}commandline")
        if native is not None or (legacy is not None and any(
            str(SHM_PATH) in arg.get("value", "")
            for arg in legacy.findall(f"{{{QEMU_NS}}}arg")
        )):
            bail(f"Power off '{name}' before resizing or recreating {SHM_PATH}.")


def stage_shm(operator: pwd.struct_passwd, size: int, force: bool) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Shared-memory region[/bold]")
    try:
        kvm_gid = grp.getgrnam("kvm").gr_gid
    except KeyError:
        bail("Group 'kvm' does not exist.")

    if SHM_PATH.is_symlink():
        bail(f"{SHM_PATH} is a symlink. Remove it manually.")
    if SHM_PATH.exists():
        st = SHM_PATH.stat()
        if not stat.S_ISREG(st.st_mode):
            bail(f"{SHM_PATH} is not a regular file.")
        if st.st_size == size and not force:
            # Permission drift does not invalidate a mapping. Repair in place.
            fd = os.open(SHM_PATH, os.O_RDWR | os.O_NOFOLLOW)
            try:
                os.fchown(fd, operator.pw_uid, kvm_gid)
                os.fchmod(fd, 0o660)
            finally:
                os.close(fd)
            console.print(f"[bold green]  ok[/bold green] {SHM_PATH}: retained {size >> 20} MiB mapping, permissions corrected.")
            return

    require_shm_stopped()
    SHM_PATH.unlink(missing_ok=True)
    fd = os.open(SHM_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o660)
    try:
        os.posix_fallocate(fd, 0, size)
        os.fchown(fd, operator.pw_uid, kvm_gid)
        os.fchmod(fd, 0o660)
    except BaseException:
        SHM_PATH.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
    console.print(
        f"[bold green]  ok[/bold green] {SHM_PATH} = {size >> 20} MiB, "
        f"{operator.pw_name}:kvm, 0660."
    )


# ==============================================================================
# DOMAIN XML SURGERY
# ==============================================================================
def strip_legacy_qom(root: ET.Element) -> bool:
    """Remove the pre-<shmem> qemu:commandline ivshmem/kvmfr escape hatch."""
    cmdline = root.find(f"{{{QEMU_NS}}}commandline")
    if cmdline is None:
        return False
    args = list(cmdline.findall(f"{{{QEMU_NS}}}arg"))
    pairs: list[tuple[int, str, dict]] = []
    index = 0
    while index + 1 < len(args):
        flag = args[index].get("value", "")
        if flag not in ("-object", "-device"):
            index += 1
            continue
        payload = args[index + 1].get("value", "")
        if payload.startswith("{"):
            try:
                fields = json.loads(payload)
            except json.JSONDecodeError:
                # QEMU also accepts the single-quoted JSON shown in the
                # upstream IVSHMEM instructions. These device paths contain
                # no quotes; unrelated unparseable arguments stay untouched.
                try:
                    fields = json.loads(payload.replace("'", '"'))
                except json.JSONDecodeError:
                    index += 2
                    continue
            if not isinstance(fields, dict):
                index += 2
                continue
        else:
            parts = payload.split(",")
            fields = dict(part.split("=", 1) for part in parts if "=" in part)
            fields.setdefault("driver" if flag == "-device" else "qom-type", parts[0])
        pairs.append((index, flag, fields))
        index += 2
    # Match the memory backend first, then its device. Other IVSHMEM devices,
    # QEMU arguments and qemu:env entries belong to the operator.
    backends = {
        fields.get("id") for _, flag, fields in pairs
        if flag == "-object" and fields.get("id") and (
            fields.get("mem-path") == str(SHM_PATH)
            or str(fields.get("mem-path", "")).startswith("/dev/kvmfr")
        )
    }
    remove: set[int] = set()
    for index, flag, fields in pairs:
        if (flag == "-object" and fields.get("id") in backends) or (
            flag == "-device" and str(fields.get("driver", "")).startswith("ivshmem")
            and fields.get("memdev") in backends
        ):
            remove.update((index, index + 1))
    for index in sorted(remove, reverse=True):
        cmdline.remove(args[index])
    if len(cmdline) == 0:
        root.remove(cmdline)
    return bool(remove)


def apply_shmem(root: ET.Element, mib: int) -> None:
    devices = root.find("devices")
    if devices is None:
        devices = ET.SubElement(root, "devices")
    shmem = devices.find(f"shmem[@name='{SHM_NAME}']")
    if shmem is None:
        shmem = ET.SubElement(devices, "shmem", name=SHM_NAME)
    model = shmem.find("model")
    if model is None:
        model = ET.SubElement(shmem, "model")
    model.set("type", "ivshmem-plain")
    size = shmem.find("size")
    if size is None:
        size = ET.SubElement(shmem, "size")
    size.set("unit", "M")
    size.text = str(mib)
    # Doorbell servers are incompatible with ivshmem-plain. Keep the PCI address.
    for child in [*shmem.findall("server"), *shmem.findall("msi")]:
        shmem.remove(child)


def apply_latency_tuning(root: ET.Element) -> list[str]:
    notes: list[str] = []
    devices = root.find("devices")
    if devices is None:
        devices = ET.SubElement(root, "devices")

    balloon = devices.find("memballoon")
    if balloon is None:
        balloon = ET.SubElement(devices, "memballoon")
    if balloon.get("model") != "none":
        notes.append("memballoon -> none")
    balloon.set("model", "none")
    for child in list(balloon):
        balloon.remove(child)

    # Ensure SPICE graphics listener binds to localhost (not none)
    graphics = devices.find("graphics[@type='spice']")
    if graphics is not None:
        listen_elem = graphics.find("listen")
        if listen_elem is not None and listen_elem.get("type") == "none":
            listen_elem.set("type", "address")
            listen_elem.set("address", "127.0.0.1")
            graphics.set("autoport", "yes")
            graphics.set("listen", "127.0.0.1")
            notes.append("graphics spice listen -> 127.0.0.1 (autoport=yes)")
        elif listen_elem is None and graphics.get("listen") in (None, "none"):
            graphics.set("autoport", "yes")
            graphics.set("listen", "127.0.0.1")
            ET.SubElement(graphics, "listen", type="address", address="127.0.0.1")
            notes.append("graphics spice listen -> 127.0.0.1 (autoport=yes)")

    if graphics is None:
        return notes  # SPICE channels require a SPICE graphics device.

    has_agent = any(
        channel.get("type") == "spicevmc"
        and (channel.find("target") is not None)
        and channel.find("target").get("name") == "com.redhat.spice.0"
        for channel in devices.findall("channel")
    )
    if not has_agent:
        channel = ET.SubElement(devices, "channel", type="spicevmc")
        ET.SubElement(channel, "target", type="virtio", name="com.redhat.spice.0")
        notes.append("spicevmc agent channel added")

    has_redirdev = any(
        r.get("bus") == "usb" and r.get("type") == "spicevmc"
        for r in devices.findall("redirdev")
    )
    if not has_redirdev:
        ET.SubElement(devices, "redirdev", bus="usb", type="spicevmc")
        notes.append("spicevmc usb redirdev added (Looking Glass USB audio)")
    return notes


def transform_domain(xml_text: str, mib: int) -> tuple[str, list[str]]:
    ET.register_namespace("qemu", QEMU_NS)
    root = ET.fromstring(xml_text)
    notes: list[str] = []
    if strip_legacy_qom(root):
        notes.append("legacy qemu:commandline ivshmem args removed")
    apply_shmem(root, mib)
    notes.append(f"native <shmem name='{SHM_NAME}'> ivshmem-plain {mib} MiB")
    notes += apply_latency_tuning(root)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n", notes


def list_domains() -> list[tuple[str, str]]:
    res = virsh(["list", "--all", "--name"])
    if not res.ok:
        bail(f"Cannot list domains: {res.err or res.out}")
    domains: list[tuple[str, str]] = []
    for name in res.out.splitlines():
        state = virsh(["domstate", name]).out or "unknown"
        domains.append((name, state))
    return domains


def redefine(domain: str, mib: int) -> bool:
    console.print(f"\n[bold blue]==>[/bold blue] [bold]Rewriting domain '{domain}'[/bold]")
    dump = virsh(["dumpxml", "--inactive", domain])
    if not dump.ok:
        console.print(f"[red]  x dumpxml failed: {dump.err or dump.out}[/red]")
        return False
    try:
        new_xml, notes = transform_domain(dump.out, mib)
    except ET.ParseError as exc:
        console.print(f"[red]  x Domain XML is not parseable: {exc}[/red]")
        return False

    fd, tmp_name = tempfile.mkstemp(prefix=f"arsonix-{domain}-", suffix=".xml")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_xml)
            handle.flush()
            os.fsync(handle.fileno())
        res = virsh(["define", str(tmp), "--validate"])
        if not res.ok:
            backup = Path(f"/var/lib/arsonix/{domain}.rejected.xml")
            atomic_write(backup, new_xml)
            console.print(f"[red]  x libvirt rejected the XML: {res.err or res.out}[/red]")
            console.print(f"[dim]    rejected document saved to {backup}[/dim]")
            return False
    finally:
        tmp.unlink(missing_ok=True)

    for note in notes:
        console.print(f"[green]  ~[/green] {note}")
    console.print(f"[bold green]  ok[/bold green] '{domain}' redefined.")
    return True


def wait_for_shutdown(domain: str, budget: float = 900.0) -> bool:
    deadline = time.monotonic() + budget
    with console.status(f"[cyan]Waiting for '{domain}' to power off...") as status:
        while time.monotonic() < deadline:
            state = virsh(["domstate", domain]).out
            if "shut off" in state:
                return True
            status.update(f"[cyan]'{domain}' is {state}; waiting for power off...")
            time.sleep(2.0)
    return False


def interactive_domain_pass(mib: int) -> bool:
    domains = list_domains()
    if not domains:
        console.print("\n[yellow]No libvirt domains defined yet; Phase 6 will create one "
                      "with <shmem> already present.[/yellow]")
        return True
    console.print("\n[bold cyan]Domains[/bold cyan]")
    choices: list[str] = []
    for index, (name, state) in enumerate(domains, start=1):
        console.print(f"  [{index}] {name} [dim]({state})[/dim]")
        choices.append(str(index))
    skip = str(len(domains) + 1)
    console.print(f"  [{skip}] skip")
    choices.append(skip)
    choice = Prompt.ask("Select", choices=choices, default=skip)
    if choice == skip:
        return True
    name, state = domains[int(choice) - 1]
    if not redefine(name, mib):
        return False
    if state != "shut off":
        console.print(
            Panel(
                f"'{name}' is {state}. The new XML is persisted but QEMU keeps the old device "
                "model until a full power cycle (reboot from inside the guest is NOT enough).",
                border_style="yellow",
            )
        )
        if Confirm.ask("Send an ACPI shutdown now and wait?", default=False):
            virsh(["shutdown", name])
            if wait_for_shutdown(name) and Confirm.ask(f"Start '{name}' again?", default=True):
                res = virsh(["start", name])
                console.print(
                    f"[bold green]  ok[/bold green] started."
                    if res.ok else f"[red]  x {res.err or res.out}[/red]"
                )
    return True


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Arsonix Phase 5: Looking Glass")
    parser.add_argument("--shm-mib", type=int, help="explicit power-of-two region size in MiB")
    parser.add_argument("--force-recreate", action="store_true", help="always rebuild the region")
    parser.add_argument("--no-packages", action="store_true", help="skip package installation")
    parser.add_argument("--domain", help="non-interactive: patch this domain")
    args = parser.parse_args()
    elevate()

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 5 -- Looking Glass Frame Relay[/bold green]\n"
            "native <shmem> ivshmem-plain / tmpfiles / existing CPU topology preserved",
            expand=False,
            border_style="green",
        )
    )

    operator = resolve_operator()
    if not args.no_packages:
        packages = ["freerdp"]
        if not current_client_available():
            packages.append("looking-glass-git")
        install_packages(packages, operator)
    if not current_client_available():
        bail("A current Looking Glass IDD client with Wayland and SPICE support is required. "
             "Install the matching development build (looking-glass-git), or build the "
             "client from the same source release as your Windows IDD installer.")

    mib, size = choose_shm(args.shm_mib)
    stage_shm(operator, size, args.force_recreate)
    configure_tmpfiles(operator)

    if args.domain:
        if not redefine(args.domain, mib):
            bail(f"Failed to configure domain {args.domain!r}.")
    elif not interactive_domain_pass(mib):
        bail("Failed to configure the selected domain.")

    state_merge(
        shm_path=str(SHM_PATH),
        shm_mib=mib,
        shm_bytes=size,
        phase_25="complete",
    )

    console.print("\n[bold green]=== PHASE 5 COMPLETE ===[/bold green]")
    console.print(
        Panel(
            "Manual reference -- the ONLY XML the guest needs (inside <devices>):\n\n"
            f"  <shmem name='{SHM_NAME}'>\n"
            "    <model type='ivshmem-plain'/>\n"
            f"    <size unit='M'>{mib}</size>\n"
            "  </shmem>\n"
            "  <memballoon model='none'/>\n\n"
            "No xmlns:qemu, no <qemu:commandline>, no kvmfr kernel module.\n"
            f"Client:  looking-glass-client -f /dev/shm/{SHM_NAME}\n"
            "Guest:   install the Looking Glass IDD (or host application) matching the client build.",
            title="libvirt-native configuration",
            border_style="cyan",
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
