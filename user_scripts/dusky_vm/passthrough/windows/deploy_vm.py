#!/usr/bin/env python3
"""Windows KVM/VFIO domain deployment.

Called by the root 30_kvm_vm_deploy.py --windows dispatcher or directly.
Uses the existing /var/lib/arsonix/state.json setup state.
"""

import argparse
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 15, 0)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2
SHM_NAME = "looking-glass"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,49}$")


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


def run(argv: list[str], *, check: bool = False, timeout: float = 300.0,
        env: dict[str, str] | None = None) -> Cmd:
    try:
        proc = subprocess.run(
            argv, check=False, timeout=timeout, text=True,
            capture_output=True, stdin=subprocess.DEVNULL,
            env=env,
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


def virsh(args: list[str], *, timeout: float = 120.0) -> Cmd:
    return run(["virsh", "-c", "qemu:///system", *args], timeout=timeout)


class PathCompleter:
    def __init__(self, home: Path) -> None:
        self.home = home

    def expand(self, text: str) -> str:
        if text.startswith("~/"):
            return str(self.home / text[2:])
        return str(self.home) if text == "~" else text

    def __call__(self, text: str, state: int) -> str | None:
        base = Path(self.expand(text))
        parent = base if text.endswith("/") else base.parent
        pattern = "*" if text.endswith("/") else base.name + "*"
        try:
            hits = sorted(str(p) + ("/" if p.is_dir() else "") for p in parent.glob(pattern))
        except OSError:
            return None
        return hits[state] if state < len(hits) else None

    def __enter__(self) -> "PathCompleter":
        import readline

        self._rl = readline
        self._prev = readline.get_completer()
        self._delims = readline.get_completer_delims()
        readline.set_completer_delims(" \t\n;")
        readline.parse_and_bind("tab: complete")
        readline.set_completer(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._rl.set_completer(self._prev)
        self._rl.set_completer_delims(self._delims)


def ask_path(prompt: str, home: Path, *, allow_blank: bool = True) -> Path | None:
    with PathCompleter(home) as completer:
        while True:
            console.print(f"[bold cyan]{prompt}[/bold cyan]"
                          + (" [dim](blank = none)[/dim]" if allow_blank else ""))
            try:
                raw = input("> ").strip().strip("\"'")
            except EOFError:
                raw = ""
            if not raw and allow_blank:
                return None
            candidate = Path(completer.expand(raw))
            if candidate.is_file():
                return candidate
            console.print("[red]  x Not a regular file.[/red]")


# ==============================================================================
# HOST FACTS
# ==============================================================================
def host_ram_mib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return 0


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


def active_networks() -> list[str]:
    return virsh(["net-list", "--name", "--state-active"]).out.split()


def domain_exists(name: str) -> bool:
    return name in virsh(["list", "--all", "--name"]).out.split()


# ==============================================================================
# STORAGE
# ==============================================================================
def provision_disk(path: Path, gib: int, qemu_user: str, force: bool) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Backing store[/bold]")
    if path.exists():
        if not force and not Confirm.ask(
            f"[yellow]{path.name} exists ({path.stat().st_size >> 30} GiB). Recreate?[/yellow]",
            default=False,
        ):
            console.print("[cyan]  Re-using the existing image.[/cyan]")
            return
        path.unlink()
    free = shutil.disk_usage(path.parent).free
    if gib << 30 > free:
        console.print(
            f"[yellow]  ! Requested {gib} GiB but only {free >> 30} GiB free. qcow2 is sparse, "
            "so this will only fail once the guest actually fills it.[/yellow]"
        )
    run(
        ["qemu-img", "create", "-f", "qcow2",
         "-o", "cluster_size=64k,lazy_refcounts=on", str(path), f"{gib}G"],
        check=True, timeout=300,
    )
    if qemu_user != "root":
        try:
            shutil.chown(path, user=qemu_user)
        except (LookupError, PermissionError) as exc:
            console.print(f"[yellow]  ! chown to {qemu_user}: {exc} (Phase 1.5 ACLs cover this)[/yellow]")
    os.chmod(path, 0o660)
    console.print(f"[bold green]  ok[/bold green] {path} ({gib} GiB, qcow2 v3, lazy_refcounts)")


def ensure_pool(directory: Path) -> None:
    """Expose the image directory as a libvirt dir pool so virt-manager can see it."""
    for pool in virsh(["pool-list", "--all", "--name"]).out.split():
        dump = virsh(["pool-dumpxml", pool])
        if not dump.ok:
            continue
        try:
            root = ET.fromstring(dump.out)
        except ET.ParseError:
            continue
        target = root.findtext("target/path")
        if target and Path(target) == directory:
            virsh(["pool-refresh", pool])
            console.print(f"[bold green]  ok[/bold green] pool '{pool}' already covers {directory}.")
            return
    name = "arsonix-" + (directory.name or "images")
    res = virsh(["pool-define-as", name, "dir", "--target", str(directory)])
    if not res.ok:
        console.print(f"[yellow]  ! pool-define-as: {res.err or res.out}[/yellow]")
        return
    virsh(["pool-build", name])
    virsh(["pool-start", name])
    virsh(["pool-autostart", name])
    console.print(f"[bold green]  ok[/bold green] libvirt pool '{name}' -> {directory}")


# ==============================================================================
# BASELINE XML VIA virt-install
# ==============================================================================
HYPERV = ",".join(
    f"hyperv.{feature}.state=on"
    for feature in ("relaxed", "vapic", "spinlocks", "vpindex", "synic", "stimer",
                    "frequencies", "reenlightenment", "tlbflush", "ipi")
)


@dataclass(slots=True)
class Spec:
    name: str
    osinfo: str
    windows: bool
    ram_mib: int
    vcpus: int
    disk: Path
    network: str
    graphics: str          # "spice" | "spice-gl" | "passthrough"
    install_iso: Path | None
    virtio_iso: Path | None
    hostdevs: list[str]


def build_command(spec: Spec) -> list[str]:
    smt = host_threads_per_core()
    threads = smt if smt > 1 and spec.vcpus % smt == 0 else 1
    cores = max(spec.vcpus // threads, 1)

    cmd = [
        "virt-install",
        "--connect", "qemu:///system",
        "--name", spec.name,
        "--osinfo", spec.osinfo,
        "--machine", "q35",
        "--memory", str(spec.ram_mib),
        "--vcpus", f"{spec.vcpus},sockets=1,cores={cores},threads={threads}",
        "--cpu", "host-passthrough,migratable=off,cache.mode=passthrough",
        "--memballoon", "none",
        "--rng", "/dev/urandom",
        "--controller", "type=usb,model=qemu-xhci",
        "--input", "type=tablet,bus=usb",
        "--channel", "spicevmc,target.type=virtio,target.name=com.redhat.spice.0",
        "--channel", "unix,target.type=virtio,target.name=org.qemu.guest_agent.0",
        "--disk",
        f"path={spec.disk},format=qcow2,bus=virtio,driver.cache=none,"
        "driver.io=io_uring,driver.discard=unmap",
        "--network", f"network={spec.network},model=virtio",
        "--boot", "uefi,cdrom,hd,menu=on",
    ]

    if spec.install_iso:
        cmd += ["--disk", f"path={spec.install_iso},device=cdrom,bus=sata,readonly=on"]
    if spec.virtio_iso:
        cmd += ["--disk", f"path={spec.virtio_iso},device=cdrom,bus=sata,readonly=on"]

    if spec.windows:
        # TPM 2.0 + SMM are hard requirements of the win11 osinfo profile.
        cmd += ["--tpm", "backend.type=emulator,backend.version=2.0,model=tpm-crb"]
        cmd += ["--features", f"smm.state=on,{HYPERV}"]
        cmd += ["--clock", "timer0.name=hypervclock,timer0.present=yes"]
        cmd += ["--redirdev", "type=spicevmc,bus=usb"]
        cmd += ["--sound", "model=ich9,audio.id=1", "--audio", "id=1,type=spice"]

    match spec.graphics:
        case "spice":
            cmd += ["--graphics", "spice,listen=127.0.0.1", "--video", "virtio"]
        case "spice-gl":
            cmd += ["--graphics", "spice,listen=socket,gl.enable=yes",
                    "--video", "virtio,accel3d=yes"]
        case _:
            # Keep a Windows setup/IDD-recovery display before guest drivers exist.
            video = "vga" if spec.windows else "model=none"
            cmd += ["--graphics", "spice,listen=127.0.0.1,gl.enable=no", "--video", video]

    for nodedev in spec.hostdevs:
        cmd += ["--hostdev", nodedev]

    cmd += ["--noautoconsole", "--print-xml"]
    return cmd


def generate_xml(spec: Spec) -> str:
    console.print("\n[bold blue]==>[/bold blue] [bold]Composing baseline with virt-install[/bold]")
    cmd = build_command(spec)
    console.print(f"[dim]{' '.join(cmd)}[/dim]")
    # Packaged virt-install uses /usr/bin/env python3. Prefer the distribution's
    # executable path so its interpreter and compiled bindings stay paired,
    # even when this script runs with a locally installed Python in PATH.
    env = {**os.environ, "PATH": os.defpath + os.pathsep + os.environ.get("PATH", "")}
    res = run(cmd, timeout=300, env=env)
    if not res.ok or not res.out.lstrip().startswith("<"):
        bail(f"virt-install refused the specification:\n{res.err or res.out}")
    console.print("[bold green]  ok[/bold green] Baseline domain XML generated.")
    return res.out


# ==============================================================================
# DOM SURGERY
# ==============================================================================
def inject_shmem(xml_text: str, mib: int) -> str:
    root = ET.fromstring(xml_text)
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
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def define_domain(name: str, xml_text: str) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Registering the domain[/bold]")
    fd, tmp_name = tempfile.mkstemp(prefix=f"arsonix-{name}-", suffix=".xml")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(xml_text)
            handle.flush()
            os.fsync(handle.fileno())
        res = virsh(["define", str(tmp), "--validate"])
        if not res.ok:
            keep = STATE_DIR / f"{name}.rejected.xml"
            atomic_write(keep, xml_text)
            bail(f"libvirt rejected the domain:\n{res.err or res.out}\nDocument kept at {keep}")
    finally:
        tmp.unlink(missing_ok=True)
    console.print(f"[bold green]  ok[/bold green] Domain '{name}' defined.")


# ==============================================================================
# INTERVIEW
# ==============================================================================
def interview(state: dict, home: Path) -> tuple[Spec, int]:
    storage = Path(str(state.get("storage_dir") or "/var/lib/libvirt/images"))
    if not storage.is_dir():
        bail(f"Storage directory {storage} does not exist. Re-run Phase 1.5.")

    nets = active_networks()
    preferred = str(state.get("libvirt_network") or "")
    network = preferred if preferred in nets else ("host-bridge" if "host-bridge" in nets
                                                   else ("default" if "default" in nets else ""))
    if not network:
        bail("No active libvirt network. Run Phase 4 (20_networking_nmcli.py).")

    while True:
        name = Prompt.ask("\n[bold cyan]Domain name[/bold cyan]", default="arsonix-guest").strip()
        if not NAME_RE.fullmatch(name):
            console.print("[red]  x Use [A-Za-z0-9._+-], max 50 chars, no spaces.[/red]")
            continue
        if domain_exists(name):
            console.print(f"[red]  x A domain called '{name}' already exists.[/red]")
            continue
        break

    console.print("\n[bold cyan]Guest OS[/bold cyan]")
    console.print("  [1] Arch Linux")
    console.print("  [2] Windows 11 (TPM 2.0 + SMM + Hyper-V enlightenments)")
    console.print("  [3] Other (enter an osinfo id)")
    os_choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    match os_choice:
        case "1":
            osinfo, windows = "archlinux", False
        case "2":
            osinfo, windows = "win11", True
        case _:
            osinfo = Prompt.ask("osinfo id [dim](virt-install --osinfo list)[/dim]",
                                default="linux2024")
            windows = osinfo.startswith("win")

    console.print("\n[bold cyan]Display topology[/bold cyan]")
    console.print("  [1] SPICE + virtio 2D")
    console.print("  [2] SPICE + virgl 3D acceleration")
    console.print("  [3] VFIO passthrough + Looking Glass (Windows VGA setup fallback)")
    gfx_choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    graphics = {"1": "spice", "2": "spice-gl", "3": "passthrough"}[gfx_choice]

    hostdevs: list[str] = []
    if graphics == "passthrough":
        recorded = [str(x) for x in (state.get("vfio_nodedevs") or [])]
        if recorded:
            console.print(f"[green]  Using the Phase 3 claim: {', '.join(recorded)}[/green]")
            hostdevs = recorded if Confirm.ask("Attach these functions?", default=True) else []
        if not hostdevs:
            raw = Prompt.ask(
                "PCI functions to attach [dim](comma separated, e.g. 0000:01:00.0,0000:01:00.1)[/dim]"
            )
            for token in raw.split(","):
                token = token.strip()
                if not token:
                    continue
                hostdevs.append("pci_" + token.replace(":", "_").replace(".", "_")
                                if ":" in token else token)
        for nodedev in hostdevs:
            probe = virsh(["nodedev-dumpxml", nodedev])
            if not probe.ok:
                bail(f"libvirt does not know nodedev '{nodedev}'. Check the Phase 3 claim.")
            if "vfio-pci" not in probe.out:
                console.print(
                    f"[yellow]  ! {nodedev} is not bound to vfio-pci. Did the host reboot after "
                    "Phase 3?[/yellow]"
                )

    total_ram = host_ram_mib()
    ram_gib = IntPrompt.ask(f"\nRAM (GiB) [dim]host has {total_ram // 1024} GiB[/dim]", default=8)
    if ram_gib * 1024 > total_ram - 2048:
        console.print("[yellow]  ! Leaves under 2 GiB for the host; the OOM killer will act.[/yellow]")
    vcpus = IntPrompt.ask(f"vCPUs [dim]host has {os.cpu_count()} threads[/dim]", default=6)
    if vcpus > (os.cpu_count() or 1):
        bail("More vCPUs than host threads is a latency trap; reduce the count.")
    disk_gib = IntPrompt.ask("Disk (GiB)", default=80)

    console.print("\n[bold cyan]Installation media[/bold cyan]")
    install_iso = ask_path("Path to the OS installer ISO", home)
    virtio_iso = None
    if windows:
        candidate = storage / "virtio-win.iso"
        if candidate.exists():
            virtio_iso = candidate
            console.print(f"[green]  Using {candidate} for VirtIO drivers.[/green]")
        else:
            virtio_iso = ask_path("Path to virtio-win.iso", home)

    return Spec(
        name=name,
        osinfo=osinfo,
        windows=windows,
        ram_mib=ram_gib * 1024,
        vcpus=vcpus,
        disk=storage / f"{name}.qcow2",
        network=network,
        graphics=graphics,
        install_iso=install_iso,
        virtio_iso=virtio_iso,
        hostdevs=hostdevs,
    ), disk_gib


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Arsonix Phase 6: domain deployment")
    parser.add_argument("--force-disk", action="store_true", help="recreate the qcow2 unprompted")
    parser.add_argument("--dump-only", action="store_true", help="print the XML, do not define")
    args = parser.parse_args()
    elevate()

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 6 -- Domain Deployment[/bold green]\n"
            "virt-install baseline / native <shmem> / VFIO hostdev",
            expand=False,
            border_style="green",
        )
    )

    for binary in ("virt-install", "virsh", "qemu-img"):
        if shutil.which(binary) is None:
            bail(f"Missing binary: {binary}. Run Phase 1.")
    if run(["systemctl", "is-active", "virtqemud.socket"], timeout=30).out != "active":
        bail("virtqemud.socket is inactive. Run Phase 2.")

    state = state_load()
    operator_name = str(state.get("human_user") or os.environ.get("SUDO_USER") or "root")
    try:
        home = Path(pwd.getpwnam(operator_name).pw_dir)
    except KeyError:
        home = Path("/root")

    table = Table(title="Inherited pipeline state", header_style="bold magenta")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    for key in ("storage_dir", "libvirt_network", "vfio_nodedevs", "shm_mib", "qemu_user"):
        table.add_row(key, str(state.get(key, "[dim]unset[/dim]")))
    console.print(table)

    if not Confirm.ask("\nDeploy a new virtual machine now?", default=True):
        console.print("[yellow]Nothing to do.[/yellow]")
        return

    spec, disk_gib = interview(state, home)
    provision_disk(spec.disk, disk_gib, str(state.get("qemu_user") or "root"), args.force_disk)
    ensure_pool(spec.disk.parent)

    xml_text = generate_xml(spec)
    if spec.graphics == "passthrough":
        mib = int(state.get("shm_mib") or 0)
        if not mib:
            mib = IntPrompt.ask("Looking Glass region (MiB, power of two)", default=64)
        xml_text = inject_shmem(xml_text, mib)
        console.print(f"[bold green]  ok[/bold green] <shmem> ivshmem-plain {mib} MiB injected.")

    if args.dump_only:
        console.print(xml_text)
        return

    define_domain(spec.name, xml_text)
    state_merge(last_domain=spec.name, last_domain_disk=str(spec.disk), phase_30="complete")

    console.print("\n[bold green]=== PHASE 6 COMPLETE ===[/bold green]")
    steps = [
        f"virsh -c qemu:///system start {spec.name}",
        f"virsh -c qemu:///system dumpxml {spec.name} | grep -E 'shmem|hostdev|memballoon'",
    ]
    if spec.graphics == "passthrough":
        steps.append("looking-glass-client -f /dev/shm/looking-glass")
    if spec.windows:
        steps.append("Load the VirtIO storage driver from the second CD-ROM during Setup.")
    console.print(Panel("\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1)),
                        title="Next", border_style="cyan"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
