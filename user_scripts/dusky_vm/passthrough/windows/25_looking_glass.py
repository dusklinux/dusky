#!/usr/bin/env python3
"""
Arsonix KVM/VFIO Pipeline -- Phase 5 (25_looking_glass.py)
Looking Glass shared-memory relay: host staging + native libvirt <shmem> device.

Target : Arch Linux rolling (Aug 2026) / libvirt 12.6+ / Looking Glass B7+
Policy : Use the libvirt-native <shmem> device. The <qemu:commandline> ivshmem
         hack is DELETED, not carried forward: raw QOM args bypass libvirt's
         device model, block migration/validation, force the xmlns:qemu escape
         hatch, and silently desynchronise from <memballoon>/NUMA handling.
         libvirt emits the identical -device ivshmem-plain + memory-backend-file
         pair from <shmem>, and additionally creates/labels /dev/shm/<name>.
"""

import argparse
import grp
import json
import math
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

MIN_PY: tuple[int, int, int] = (3, 14, 7)
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
    _hard_exit("Python 3.14.7+ required.")
elevate()

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
    Looking Glass B7+ IDD sizing rule (triple-buffered):
    (ceil(width * 4 / 256) * 256) * height * 3 + (4 * 1024 * 1024),
    rounded UP to the next power of two.
    ivshmem-plain requires a power-of-two region, so the rounding is mandatory.
    """
    stride = math.ceil((width * 4) / 256) * 256
    raw = stride * height * 3 + (4 << 20)
    return 1 << math.ceil(math.log2(raw))


def choose_shm(explicit_mib: int | None) -> tuple[int, int]:
    if explicit_mib:
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
        stride = math.ceil((width * 4) / 256) * 256
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
        console.print(f"[yellow]  ! systemd-tmpfiles: {res.err or res.out}[/yellow]")


def stage_shm(operator: pwd.struct_passwd, size: int, force: bool) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Shared-memory region[/bold]")
    try:
        kvm_gid = grp.getgrnam("kvm").gr_gid
    except KeyError:
        bail("Group 'kvm' does not exist.")

    # Symlink defense: /dev/shm is world-writable + sticky, never follow
    if SHM_PATH.is_symlink():
        bail(f"{SHM_PATH} is a symlink (possible TOCTOU attack). Remove it manually.")
    if SHM_PATH.exists():
        try:
            st = SHM_PATH.stat()
        except OSError as exc:
            bail(f"Cannot stat {SHM_PATH}: {exc}")
        if stat.S_ISDIR(st.st_mode):
            bail(f"{SHM_PATH} is a directory (a stale kvmfr/mount artefact). Remove it manually.")
        if st.st_size == size and st.st_uid == operator.pw_uid and st.st_gid == kvm_gid \
                and stat.S_IMODE(st.st_mode) == 0o660 and not force:
            console.print(
                f"[bold green]  ok[/bold green] {SHM_PATH} already {size >> 20} MiB "
                f"{operator.pw_name}:kvm 0660."
            )
            return
        if st.st_size and st.st_size != size:
            console.print(
                f"[yellow]  ! Resizing {SHM_PATH} from {st.st_size >> 20} to "
                f"{size >> 20} MiB. Any running guest mapping it must be powered off.[/yellow]"
            )
        # Any mismatch requires replacing the inode; warn and unlink (live mapping already
        # invalidated by size/owner/mode drift). Use O_EXCL to avoid raced truncation.
        try:
            SHM_PATH.unlink()
        except OSError as exc:
            bail(f"Cannot remove stale {SHM_PATH}: {exc}")

    fd = os.open(SHM_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o660)
    try:
        # tmpfs on Linux 7.1.8 guarantees fallocate; sparse fallback is a legacy shim and is
        # deleted per the Aug 2026 no-compat directive.
        os.posix_fallocate(fd, 0, size)
        os.fchown(fd, operator.pw_uid, kvm_gid)
        os.fchmod(fd, 0o660)
    finally:
        os.close(fd)
    console.print(
        f"[bold green]  ok[/bold green] {SHM_PATH} = {size >> 20} MiB, "
        f"{operator.pw_name}:kvm, 0660."
    )


# ==============================================================================
# HOST CPU TOPOLOGY
# ==============================================================================
def host_threads_per_core() -> int:
    path = Path("/sys/devices/system/cpu/cpu0/topology/thread_siblings_list")
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 1
    count = 0
    for chunk in raw.split(","):
        if "-" in chunk:
            low, high = chunk.split("-", 1)
            count += int(high) - int(low) + 1
        else:
            count += 1
    return max(count, 1)


# ==============================================================================
# DOMAIN XML SURGERY
# ==============================================================================
def sub(parent: ET.Element, tag: str, **attrs: str) -> ET.Element:
    found = parent.find(tag)
    if found is None:
        found = ET.SubElement(parent, tag)
    for key, val in attrs.items():
        found.set(key, val)
    return found


def strip_legacy_qom(root: ET.Element) -> bool:
    """Remove the pre-<shmem> qemu:commandline ivshmem/kvmfr escape hatch."""
    cmdline = root.find(f"{{{QEMU_NS}}}commandline")
    if cmdline is None:
        return False
    args = list(cmdline.findall(f"{{{QEMU_NS}}}arg"))
    keep: list[ET.Element] = []
    skip_next = False
    removed = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            removed = True
            continue
        value = arg.get("value", "")
        if value in ("-device", "-object") and index + 1 < len(args):
            payload = args[index + 1].get("value", "")
            if "looking-glass" in payload or "kvmfr" in payload or "ivshmem" in payload:
                skip_next = True
                removed = True
                continue
        if "looking-glass" in value or "kvmfr" in value:
            removed = True
            continue
        keep.append(arg)
    for child in list(cmdline):
        cmdline.remove(child)
    for arg in keep:
        cmdline.append(arg)
    if len(cmdline) == 0:
        root.remove(cmdline)
    return removed


def apply_shmem(root: ET.Element, mib: int) -> None:
    devices = root.find("devices")
    if devices is None:
        devices = ET.SubElement(root, "devices")
    for existing in devices.findall("shmem"):
        if existing.get("name") == SHM_NAME:
            devices.remove(existing)
    shmem = ET.SubElement(devices, "shmem", name=SHM_NAME)
    ET.SubElement(shmem, "model", type="ivshmem-plain")
    size = ET.SubElement(shmem, "size", unit="M")
    size.text = str(mib)


def apply_cpu_topology(root: ET.Element) -> str:
    vcpu_elem = root.find("vcpu")
    try:
        vcpus = int((vcpu_elem.text or "1").strip()) if vcpu_elem is not None else 1
    except ValueError:
        vcpus = 1
    smt = host_threads_per_core()
    threads = smt if smt > 1 and vcpus % smt == 0 else 1
    cores = max(vcpus // threads, 1)
    cpu = root.find("cpu")
    if cpu is None:
        cpu = ET.SubElement(root, "cpu")
    cpu.set("mode", "host-passthrough")
    cpu.set("check", "none")
    cpu.set("migratable", "off")
    sub(cpu, "topology", sockets="1", dies="1", cores=str(cores), threads=str(threads))
    return f"1 socket x {cores} cores x {threads} threads = {cores * threads} vCPU (declared {vcpus})"


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
    notes.append(apply_cpu_topology(root))
    notes += apply_latency_tuning(root)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n", notes


def list_domains() -> list[tuple[str, str]]:
    res = virsh(["list", "--all", "--name"])
    domains: list[tuple[str, str]] = []
    for name in res.out.split():
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
        res = virsh(["define", str(tmp)])
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


def interactive_domain_pass(mib: int) -> None:
    domains = list_domains()
    if not domains:
        console.print("\n[yellow]No libvirt domains defined yet; Phase 6 will create one "
                      "with <shmem> already present.[/yellow]")
        return
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
        return
    name, state = domains[int(choice) - 1]
    if not redefine(name, mib):
        return
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

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 5 -- Looking Glass Frame Relay[/bold green]\n"
            "native <shmem> ivshmem-plain / tmpfiles / host-passthrough topology",
            expand=False,
            border_style="green",
        )
    )

    operator = resolve_operator()
    if not args.no_packages:
        install_packages(["looking-glass", "freerdp"], operator)

    mib, size = choose_shm(args.shm_mib)
    configure_tmpfiles(operator)
    stage_shm(operator, size, args.force_recreate)

    if args.domain:
        redefine(args.domain, mib)
    else:
        interactive_domain_pass(mib)

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
