#!/usr/bin/env python3
"""
Arsonix KVM/VFIO Pipeline -- Phase 3 (15_gpu_probing_kernal_param_mkinit.py)
IOMMU topology probe, VFIO claim, bootloader cmdline surgery, initramfs hardening.

Target : Arch Linux rolling (Aug 2026) / Linux 7.1.8+ / Python 3.14.7+ / systemd 261+
Sources: docs.kernel.org/driver-api/vfio, Documentation/admin-guide/kernel-parameters.txt,
         bootctl(1), mkinitcpio(8), modprobe.d(5)

Hard rules encoded here
-----------------------
* Device facts come from sysfs, never from scraped lspci text.
* A GPU is a *slot*, not a function: video + HDMI-audio + xHCI + UCSI all move.
* The token amd_iommu with value "on" is NOT parsed by the kernel
  (parse_amd_iommu_options accepts off / force_enable / force_isolation /
  pgtbl_v1 / pgtbl_v2 / ...). AMD-Vi is on by default when the IVRS table is
  sane, so we emit only iommu=pt unless the operator explicitly asks for
  --amd-force-enable.
* vendor:device is a *class* of hardware. Before claiming an ID we enumerate every
  PCI function that matches it; if the match set exceeds the selection we refuse to
  proceed silently (this is how people detach their second GPU, capture card, or an
  identically-branded NVMe controller by accident).
* snd_hda_intel / xhci_pci / i2c_designware are NEVER blacklisted -- only softdep'd.
"""

import argparse
import functools
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 15, 0)
STATE_DIR = Path("/var/lib/arsonix")
STATE_FILE = STATE_DIR / "state.json"
STATE_SCHEMA = 2

SYS_PCI = Path("/sys/bus/pci/devices")
MODPROBE_FILE = Path("/etc/modprobe.d/arsonix-vfio.conf")
MKINITCPIO_CONF = Path("/etc/mkinitcpio.conf")
MKINITCPIO_DROPIN_DIR = Path("/etc/mkinitcpio.conf.d")
MKINITCPIO_DROPIN = MKINITCPIO_DROPIN_DIR / "99-arsonix-vfio.conf"
KERNEL_CMDLINE = Path("/etc/kernel/cmdline")
CMDLINE_D = Path("/etc/cmdline.d")
CMDLINE_D_DROPIN = CMDLINE_D / "99-arsonix-vfio.conf"

MANAGED_KEYS = ("intel_iommu", "amd_iommu", "iommu", "module_blacklist", "vfio-pci.ids")
VOLATILE_TOKENS = {
    "single", "1", "s", "S", "rescue", "emergency", "init=/bin/sh",
    "systemd.unit=rescue.target", "systemd.unit=emergency.target", "systemd.debug-shell",
}
VOLATILE_PREFIXES = ("BOOT_IMAGE=", "initrd=", "cryptdevice_UNUSED=")

PCI_IDS_PATHS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"))

CLASS_NAMES = {
    "0300": "VGA controller", "0301": "XGA controller", "0302": "3D controller",
    "0380": "Display controller", "0403": "HD Audio", "0c03": "USB controller",
    "0c05": "SMBus", "0c80": "Serial (UCSI)", "0604": "PCI bridge", "0108": "NVMe",
    "0106": "SATA", "0200": "Ethernet", "0280": "Wireless",
}
GPU_CLASSES = {"0300", "0301", "0302", "0380"}

VENDOR_DRIVERS = {
    "10de": [
        "nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm",
        "nvidia_peermem", "nova", "nova_core", "nova_drm",
    ],
    "1002": ["amdgpu", "radeon"],
    "1022": ["amdgpu"],
    "8086": ["i915", "xe"],
    "1af4": ["virtio_gpu"],
}
# Shared host infrastructure: reroute with softdep, never blacklist.
NEVER_BLACKLIST = {
    "snd_hda_intel", "snd_hda_codec_hdmi", "xhci_pci", "xhci_hcd", "i2c_designware_pci",
    "typec_ucsi", "ucsi_ccg", "ahci", "nvme",
}


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
    if not sys.stdin.isatty() and not os.environ.get("SUDO_ASKPASS"):
        res = subprocess.run([sudo, "-n", "true"], capture_output=True)
        if res.returncode != 0:
            _hard_exit("Root privileges required, but terminal is non-interactive and passwordless sudo is not configured. Re-run with sudo.")
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
    _hard_exit("Python 3.15.0+ required.")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt
    from rich.table import Table
except ModuleNotFoundError:
    _hard_exit("python-rich is missing. Run Phase 1 (05_virtio_iso.py) first.")

console = Console(force_terminal=True, force_interactive=True)
DRY_RUN = False


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
    if DRY_RUN:
        console.print(f"[magenta]  [dry-run] would write {path}[/magenta]")
        return True
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
    if DRY_RUN:
        return
    data = state_load()
    data.update(kv)
    data["schema"] = STATE_SCHEMA
    data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write(STATE_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=0o644)


# ==============================================================================
# PCI / IOMMU TOPOLOGY  (sysfs is the source of truth)
# ==============================================================================
@dataclass(frozen=True, slots=True)
class PciDevice:
    addr: str
    vendor: str
    device: str
    klass: str
    driver: str | None
    iommu_group: str | None
    boot_vga: bool
    label: str

    @property
    def ids(self) -> str:
        return f"{self.vendor}:{self.device}"

    @property
    def slot(self) -> str:
        return self.addr.rsplit(".", 1)[0]

    @property
    def klass4(self) -> str:
        return self.klass[:4]

    @property
    def klass_name(self) -> str:
        return CLASS_NAMES.get(self.klass4, f"class {self.klass4}")

    @property
    def nodedev(self) -> str:
        return "pci_" + self.addr.replace(":", "_").replace(".", "_")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@functools.cache
def lookup_pci_ids(vendor_hex: str, device_hex: str) -> str | None:
    """Fallback parser for /usr/share/hwdata/pci.ids when lspci is unavailable."""
    v_clean = vendor_hex.lower().removeprefix("0x")
    d_clean = device_hex.lower().removeprefix("0x")
    if not (len(v_clean) == 4 and len(d_clean) == 4):
        return None
    for ids_path in PCI_IDS_PATHS:
        if not ids_path.exists():
            continue
        try:
            in_vendor = False
            v_name, d_name = "", ""
            with open(ids_path, encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    if not raw_line or raw_line.startswith("#"):
                        continue
                    if not raw_line.startswith("\t"):
                        if in_vendor:
                            break
                        if raw_line[:4].lower() == v_clean:
                            in_vendor = True
                            v_name = raw_line[4:].strip()
                    elif in_vendor and raw_line.startswith("\t") and not raw_line.startswith("\t\t"):
                        stripped = raw_line[1:]
                        if stripped[:4].lower() == d_clean:
                            d_name = stripped[4:].strip()
                            break
            if v_name and d_name:
                return f"{v_name} {d_name}"
            return d_name or v_name or None
        except OSError:
            pass
    return None


def lspci_labels() -> dict[str, str]:
    """Vendor/device marketing strings, keyed by full domain address."""
    if shutil.which("lspci") is None:
        return {}
    res = run(["lspci", "-Dmm"], timeout=60)
    labels: dict[str, str] = {}
    for line in res.out.splitlines():
        try:
            fields = shlex.split(line)
        except ValueError:
            continue
        if len(fields) >= 4:
            labels[fields[0]] = f"{fields[2]} {fields[3]}"
    return labels


def enumerate_pci() -> list[PciDevice]:
    labels = lspci_labels()
    devices: list[PciDevice] = []
    for node in sorted(SYS_PCI.iterdir()):
        vendor = _read(node / "vendor").removeprefix("0x")
        device = _read(node / "device").removeprefix("0x")
        klass = _read(node / "class").removeprefix("0x")
        if not vendor or not device or not klass:
            continue
        driver_link = node / "driver"
        driver = driver_link.resolve().name if driver_link.is_symlink() else None
        group_link = node / "iommu_group"
        group = group_link.resolve().name if group_link.is_symlink() else None
        label = labels.get(node.name)
        if not label:
            label_file = node / "label"
            if label_file.exists():
                label = _read(label_file)
            if not label:
                label = lookup_pci_ids(vendor, device) or "unknown device"
        devices.append(
            PciDevice(
                addr=node.name,
                vendor=vendor,
                device=device,
                klass=klass,
                driver=driver,
                iommu_group=group,
                boot_vga=_read(node / "boot_vga") == "1",
                label=label,
            )
        )
    return devices


def group_members(devices: list[PciDevice], group: str | None) -> list[PciDevice]:
    return [] if group is None else [d for d in devices if d.iommu_group == group]


def iommu_active() -> bool:
    root = Path("/sys/class/iommu")
    return root.is_dir() and any(root.iterdir())


def cpu_vendor() -> str:
    text = _read(Path("/proc/cpuinfo"))
    if "GenuineIntel" in text:
        return "intel"
    if "AuthenticAMD" in text:
        return "amd"
    return "unknown"


# ==============================================================================
# SELECTION
# ==============================================================================
@dataclass(slots=True)
class Claim:
    functions: list[PciDevice] = field(default_factory=list)
    groups: set[str] = field(default_factory=set)

    @property
    def ids(self) -> list[str]:
        return sorted({d.ids for d in self.functions})

    @property
    def addrs(self) -> list[str]:
        return [d.addr for d in self.functions]


def render_gpu_table(devices: list[PciDevice], slots: list[str]) -> None:
    table = Table(title="Discrete graphics slots", header_style="bold magenta")
    table.add_column("#", justify="center", style="cyan")
    table.add_column("Slot", style="dim")
    table.add_column("IOMMU", justify="center", style="bold red")
    table.add_column("Functions", style="green")
    table.add_column("Driver in use", style="yellow")
    table.add_column("Flags")

    for index, slot in enumerate(slots, start=1):
        funcs = [d for d in devices if d.slot == slot]
        primary = next((d for d in funcs if d.klass4 in GPU_CLASSES), funcs[0])
        fn_lines = "\n".join(
            f"{d.addr.split(':')[-1]} {d.klass_name} [{d.ids}] {d.label}" for d in funcs
        )
        drv_lines = "\n".join(d.driver or "-" for d in funcs)
        flags = []
        if primary.boot_vga:
            flags.append("[bold red]boot_vga[/bold red]")
        if any(d.driver == "vfio-pci" for d in funcs):
            flags.append("[green]already vfio[/green]")
        groups = sorted({d.iommu_group or "?" for d in funcs})
        table.add_row(str(index), slot, ",".join(groups), fn_lines, drv_lines, " ".join(flags))
    console.print(table)


def audit_groups(devices: list[PciDevice], claim: Claim) -> None:
    strangers: list[PciDevice] = []
    for group in sorted(claim.groups):
        for member in group_members(devices, group):
            if member.addr in claim.addrs:
                continue
            if member.klass4 == "0604":  # bridges cannot be bound and do not need to be
                continue
            strangers.append(member)
    if not strangers:
        console.print("[bold green]  ok[/bold green] IOMMU groups are clean (no foreign endpoints).")
        return
    table = Table(title="ACS WARNING -- foreign endpoints share the target IOMMU group(s)",
                  header_style="bold red")
    table.add_column("Address")
    table.add_column("Group", justify="center")
    table.add_column("Class")
    table.add_column("Driver")
    table.add_column("Label")
    for dev in strangers:
        table.add_row(dev.addr, dev.iommu_group or "?", dev.klass_name, dev.driver or "-", dev.label)
    console.print(table)
    console.print(
        "[yellow]Every endpoint in a group must be handed to the guest together. Move the card "
        "to a CPU-attached x16 slot or use a board with real ACS. Do NOT use ACS-override "
        "patches on a host that handles anything you care about.[/yellow]"
    )
    if not Confirm.ask("Continue despite the shared group?", default=False):
        raise SystemExit(0)


def audit_id_collisions(devices: list[PciDevice], claim: Claim) -> None:
    collisions: list[PciDevice] = []
    for dev in devices:
        if dev.ids in claim.ids and dev.addr not in claim.addrs:
            collisions.append(dev)
    if not collisions:
        console.print("[bold green]  ok[/bold green] No other PCI function matches the claimed IDs.")
        return
    table = Table(title="ID COLLISION -- vfio-pci ids= would also capture these",
                  header_style="bold red")
    table.add_column("Address")
    table.add_column("IDs")
    table.add_column("Class")
    table.add_column("Driver")
    table.add_column("Label")
    for dev in collisions:
        table.add_row(dev.addr, dev.ids, dev.klass_name, dev.driver or "-", dev.label)
    console.print(table)
    console.print(
        "[yellow]'options vfio-pci ids=' matches by vendor:device, not by address. These "
        "functions would be detached from the host at boot as well.[/yellow]\n"
        "[yellow]For identical twin cards, bind by address instead:\n"
        "  /etc/modprobe.d/arsonix-vfio.conf ->  (drop ids=)\n"
        "  echo vfio-pci > /sys/bus/pci/devices/<addr>/driver_override  via a systemd unit,\n"
        "  or use the 'driverctl set-override <addr> vfio-pci' persistence helper.[/yellow]"
    )
    if not Confirm.ask("Claim these IDs anyway?", default=False):
        raise SystemExit(0)


def select_claim(devices: list[PciDevice], want_slot: str | None) -> Claim:
    console.print("\n[bold blue]==>[/bold blue] [bold]Probing PCI / IOMMU topology[/bold]")
    if not iommu_active():
        console.print(
            "[yellow]  ! /sys/class/iommu is empty: the IOMMU is not active yet. Groups shown "
            "below may be absent. This is normal on the first Phase 3 run.[/yellow]"
        )
    gpu_slots = sorted({d.slot for d in devices if d.klass4 in GPU_CLASSES})
    if not gpu_slots:
        bail("No VGA/3D/display controller found in sysfs.")
    render_gpu_table(devices, gpu_slots)

    if want_slot:
        slot = want_slot if want_slot in gpu_slots else None
        if slot is None:
            bail(f"--slot {want_slot} is not a discrete graphics slot. Options: {gpu_slots}")
    elif len(gpu_slots) == 1:
        slot = gpu_slots[0]
        console.print(f"[dim]Only one graphics slot present: {slot}[/dim]")
    else:
        index = IntPrompt.ask(
            "\n[bold cyan]Slot to isolate for VFIO[/bold cyan]",
            choices=[str(i + 1) for i in range(len(gpu_slots))],
        )
        slot = gpu_slots[index - 1]

    funcs = [d for d in devices if d.slot == slot]
    primary = next((d for d in funcs if d.klass4 in GPU_CLASSES), funcs[0])
    if primary.boot_vga:
        console.print(
            Panel(
                f"{slot} is the firmware boot VGA device. Isolating it leaves the host with no "
                "console unless a second GPU (or serial/SSH) is available.",
                title="boot_vga",
                border_style="red",
            )
        )
        if not Confirm.ask("Proceed and blind the host console?", default=False):
            raise SystemExit(0)

    claim = Claim(functions=funcs, groups={d.iommu_group for d in funcs if d.iommu_group})
    console.print(
        f"[bold green]  ok[/bold green] Claiming {len(funcs)} function(s) in slot {slot}: "
        f"{', '.join(d.addr for d in funcs)}"
    )
    audit_groups(devices, claim)
    audit_id_collisions(devices, claim)
    return claim


# ==============================================================================
# KERNEL COMMAND LINE
# ==============================================================================
def desired_params(vendor: str, blacklist: set[str], ids: list[str], amd_force: bool,
                   cmdline_ids: bool) -> dict[str, str]:
    params: dict[str, str] = {"iommu": "pt"}
    match vendor:
        case "intel":
            params["intel_iommu"] = "on"
        case "amd":
            if amd_force:
                params["amd_iommu"] = "force_enable"
        case _:
            console.print("[yellow]  ! Unknown CPU vendor; emitting iommu=pt only.[/yellow]")
    if blacklist:
        params["module_blacklist"] = ",".join(sorted(blacklist))
    if cmdline_ids and ids:
        params["vfio-pci.ids"] = ",".join(sorted(ids))
    return params


def merge_cmdline(current: str, desired: dict[str, str], vendor: str) -> str:
    """
    Rebuild a kernel command line: keep every foreign token verbatim and in order,
    own MANAGED_KEYS outright, honour the '--' init separator, drop volatile tokens.
    """
    try:
        tokens = shlex.split(current, posix=False)
    except ValueError:
        tokens = current.split()
    kept: list[str] = []
    tail: list[str] = []
    seen_sep = False
    for token in tokens:
        if token == "--":
            seen_sep = True
            continue
        if seen_sep:
            tail.append(token)
            continue
        if token in VOLATILE_TOKENS or token.startswith(VOLATILE_PREFIXES):
            continue
        key = token.split("=", 1)[0].replace("vfio_pci.", "vfio-pci.")
        if key in MANAGED_KEYS:
            continue
        kept.append(token)

    # Cross-vendor de-pollution: never leave the other vendor's IOMMU switch behind.
    stale = "amd_iommu" if vendor == "intel" else "intel_iommu"
    kept = [t for t in kept if not t.startswith(stale + "=")]

    for key in MANAGED_KEYS:
        if key in desired:
            kept.append(f"{key}={desired[key]}")
    if tail:
        kept += ["--", *tail]
    return " ".join(kept)


# --- systemd-boot entry resolution -------------------------------------------
@dataclass(frozen=True, slots=True)
class BootEntry:
    kind: str  # "type1" | "type2"
    path: Path | None
    options: str
    ident: str


def _pick(entry: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in entry and entry[key] not in (None, ""):
            return entry[key]
    return None


def boot_root() -> Path:
    for flag in ("-x", "-p"):
        res = run(["bootctl", flag], timeout=30)
        if res.ok and res.out:
            candidate = Path(res.out.splitlines()[0].strip())
            if candidate.is_dir():
                return candidate
    bail("bootctl cannot resolve $BOOT/ESP. Is systemd-boot installed (bootctl status)?")


def parse_bootctl_text() -> list[dict]:
    """Deterministic secondary parser for 'bootctl list' key: value blocks."""
    res = run(["bootctl", "list", "--no-pager"], timeout=30)
    entries: list[dict] = []
    current: dict = {}
    for raw in res.out.splitlines():
        if not raw.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        match = re.match(r"^\s{2,}([a-z\- ]+):\s*(.*)$", raw)
        if match:
            current[match.group(1).strip()] = match.group(2).strip()
    if current:
        entries.append(current)
    normalised = []
    for entry in entries:
        kind = "type2" if "Type #2" in entry.get("type", "") else "type1"
        normalised.append(
            {
                "type": kind,
                "source": entry.get("source", ""),
                "options": entry.get("options", ""),
                "id": entry.get("id", ""),
                "isDefault": "(default)" in entry.get("title", ""),
                "isSelected": "(selected)" in entry.get("title", ""),
            }
        )
    return normalised


def resolve_boot_entry() -> BootEntry:
    console.print("\n[bold blue]==>[/bold blue] [bold]Resolving the active boot entry[/bold]")
    raw: list[dict] = []
    res = run(["bootctl", "list", "--json=short"], timeout=30)
    if res.ok and res.out.startswith(("[", "{")):
        try:
            parsed = json.loads(res.out)
            raw = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            raw = []
    if not raw:
        console.print("[yellow]  ! JSON unavailable; parsing bootctl text output.[/yellow]")
        raw = parse_bootctl_text()
    if not raw:
        bail("bootctl reported no boot entries.")

    def score(entry: dict) -> int:
        sel = bool(_pick(entry, ("isSelected", "is_selected")))
        dfl = bool(_pick(entry, ("isDefault", "is_default")))
        return (2 if sel else 0) + (1 if dfl else 0)

    candidates = [e for e in raw if str(_pick(e, ("type",)) or "").startswith("type")]
    if not candidates:
        bail("bootctl returned only automatic entries; no Type #1/#2 entry to patch.")
    best = max(candidates, key=score)
    kind = "type2" if "2" in str(_pick(best, ("type",))) else "type1"
    path_str = _pick(best, ("path", "source", "sourcePath"))
    ident = str(_pick(best, ("id",)) or "?")
    path: Path | None = None
    if isinstance(path_str, str):
        # bootctl prints paths like /efi//loader/entries/arch.conf
        path = Path(re.sub(r"/{2,}", "/", path_str))
    if path is None or not path.exists():
        guess = boot_root() / "loader" / "entries" / ident
        path = guess if guess.exists() else None
    options = str(_pick(best, ("options",)) or "")
    console.print(
        f"[bold green]  ok[/bold green] {kind} entry '{ident}'"
        + (f" -> {path}" if path else " (no on-disk snippet)")
    )
    return BootEntry(kind, path, options, ident)


def patch_type1(entry: BootEntry, desired: dict[str, str], vendor: str) -> None:
    if entry.path is None or entry.path.suffix != ".conf":
        bail(f"Type #1 entry '{entry.ident}' has no writable .conf snippet.")
    content = entry.path.read_text(encoding="utf-8")
    match = re.search(r"^[ \t]*options[ \t]+(.*)$", content, re.MULTILINE)
    if match:
        merged = merge_cmdline(match.group(1), desired, vendor)
        new_content = content[: match.start()] + f"options {merged}" + content[match.end():]
    else:
        merged = merge_cmdline("", desired, vendor)
        new_content = content.rstrip("\n") + f"\noptions {merged}\n"
    if atomic_write(entry.path, new_content):
        console.print(f"[green]  ~[/green] {entry.path}")
        console.print(f"[dim]    options {merged}[/dim]")
    else:
        console.print(f"[bold green]  ok[/bold green] {entry.path} already convergent.")


def depollute_cmdline_dropins(keep: Path | None) -> None:
    if not CMDLINE_D.is_dir():
        return
    for conf in sorted(CMDLINE_D.glob("*.conf")):
        if keep is not None and conf == keep:
            continue
        text = conf.read_text(encoding="utf-8")
        tokens = text.split()
        cleaned = [
            t for t in tokens
            if t.split("=", 1)[0].replace("vfio_pci.", "vfio-pci.") not in MANAGED_KEYS
        ]
        if len(cleaned) != len(tokens):
            atomic_write(conf, (" ".join(cleaned) + "\n") if cleaned else "\n")
            console.print(f"[green]  ~[/green] de-polluted {conf}")


def patch_type2(entry: BootEntry, desired: dict[str, str], vendor: str) -> None:
    """
    Unified Kernel Image: the command line is baked at build time. Own exactly one
    source file so mkinitcpio/ukify cannot concatenate duplicate managed keys.
    """
    dropins = sorted(CMDLINE_D.glob("*.conf")) if CMDLINE_D.is_dir() else []
    if KERNEL_CMDLINE.is_file():
        target, seed = KERNEL_CMDLINE, KERNEL_CMDLINE.read_text(encoding="utf-8")
        depollute_cmdline_dropins(None)
    elif dropins:
        target = CMDLINE_D_DROPIN
        seed = " ".join(f.read_text(encoding="utf-8").strip() for f in dropins)
        depollute_cmdline_dropins(CMDLINE_D_DROPIN)
    else:
        target = KERNEL_CMDLINE
        seed = entry.options
        if not seed.strip():
            console.print(
                "[yellow]  ! No /etc/kernel/cmdline and no baked options; seeding from "
                "/proc/cmdline with volatile tokens filtered.[/yellow]"
            )
            seed = Path("/proc/cmdline").read_text(encoding="utf-8")

    merged = merge_cmdline(seed, desired, vendor)
    if target == CMDLINE_D_DROPIN:
        # Only our managed keys live in the drop-in; foreign tokens stay where they were.
        merged = " ".join(f"{k}={desired[k]}" for k in MANAGED_KEYS if k in desired)
    if atomic_write(target, merged + "\n"):
        console.print(f"[green]  ~[/green] {target}")
        console.print(f"[dim]    {merged}[/dim]")
    else:
        console.print(f"[bold green]  ok[/bold green] {target} already convergent.")


def inject_cmdline(claim: Claim, vendor: str, blacklist: set[str], amd_force: bool,
                   cmdline_ids: bool) -> None:
    entry = resolve_boot_entry()
    desired = desired_params(vendor, blacklist, claim.ids, amd_force, cmdline_ids)
    console.print(
        "[dim]managed: " + " ".join(f"{k}={v}" for k, v in desired.items()) + "[/dim]"
    )
    if entry.kind == "type1":
        patch_type1(entry, desired, vendor)
    else:
        patch_type2(entry, desired, vendor)


# ==============================================================================
# INITRAMFS
# ==============================================================================
def find_array(content: str, name: str) -> tuple[int, int, str] | None:
    match = re.search(rf"^[ \t]*{re.escape(name)}[ \t]*\+?=[ \t]*\(", content, re.MULTILINE)
    if not match:
        return None
    index, depth = match.end(), 1
    while index < len(content) and depth:
        if content[index] == "(":
            depth += 1
        elif content[index] == ")":
            depth -= 1
        index += 1
    return match.start(), index, content[match.end(): index - 1]


def patch_hooks(path: Path) -> None:
    """modconf must precede kms so /etc/modprobe.d lands before DRM drivers bind."""
    if not path.is_file():
        return
    content = path.read_text(encoding="utf-8")
    found = find_array(content, "HOOKS")
    if found is None:
        return
    start, end, body = found
    try:
        hooks = shlex.split(body, comments=True)
    except ValueError:
        console.print(f"[yellow]  ! Unparsable HOOKS array in {path}; left untouched.[/yellow]")
        return
    original = list(hooks)
    if "modconf" in hooks:
        if "kms" in hooks and hooks.index("modconf") > hooks.index("kms"):
            hooks.remove("modconf")
            hooks.insert(hooks.index("kms"), "modconf")
    else:
        anchor = hooks.index("kms") if "kms" in hooks else len(hooks)
        hooks.insert(anchor, "modconf")
    if hooks == original:
        console.print(f"[bold green]  ok[/bold green] HOOKS order already correct in {path.name}.")
        return
    new_content = content[:start] + "HOOKS=(" + " ".join(hooks) + ")" + content[end:]
    if atomic_write(path, new_content):
        console.print(f"[green]  ~[/green] {path}: HOOKS=({' '.join(hooks)})")


def write_modules_dropin(main_conf: Path) -> None:
    payload = (
        "# Managed by Arsonix (Phase 3). VFIO must exist in early userspace so the\n"
        "# stub driver can claim the GPU before any DRM driver probes it.\n"
        'MODULES=(vfio_pci vfio vfio_iommu_type1 "${MODULES[@]}")\n'
        'if [[ ! " ${HOOKS[*]} " == *" modconf "* ]]; then HOOKS+=(modconf); fi\n'
    )
    if atomic_write(MKINITCPIO_DROPIN, payload):
        console.print(f"[green]  ~[/green] {MKINITCPIO_DROPIN}")
    else:
        console.print(f"[bold green]  ok[/bold green] {MKINITCPIO_DROPIN} already convergent.")


def configure_initramfs() -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Hardening early userspace[/bold]")
    if shutil.which("mkinitcpio") is None:
        console.print("[yellow]  ! mkinitcpio absent; skipping mkinitcpio configuration.[/yellow]")
        return
    if not MKINITCPIO_CONF.is_file():
        bail("/etc/mkinitcpio.conf missing while the mkinitcpio binary exists.")
    MKINITCPIO_DROPIN_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    write_modules_dropin(MKINITCPIO_CONF)
    patch_hooks(MKINITCPIO_CONF)
    for drop_in in sorted(MKINITCPIO_DROPIN_DIR.glob("*.conf")):
        if drop_in != MKINITCPIO_DROPIN:
            patch_hooks(drop_in)


def regenerate_initramfs() -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Rebuilding initramfs / UKI[/bold]")
    if DRY_RUN:
        console.print("[magenta]  [dry-run] skipping regeneration.[/magenta]")
        return
    if shutil.which("mkinitcpio") and any(Path("/etc/mkinitcpio.d").glob("*.preset")):
        argv = ["mkinitcpio", "-P"]
    elif shutil.which("dracut"):
        argv = ["dracut", "--regenerate-all", "--force"]
    elif shutil.which("kernel-install"):
        argv = ["kernel-install", "add-all"]
    else:
        bail("No initramfs generator found (mkinitcpio / dracut / kernel-install).")
    console.print(f"  [cyan]{' '.join(argv)}[/cyan]")
    proc = subprocess.run(argv, check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        bail(f"{argv[0]} failed with rc={proc.returncode}. The host is NOT safe to reboot yet.")
    console.print("[bold green]  ok[/bold green] Images regenerated.")


# ==============================================================================
# MODPROBE RULES
# ==============================================================================
def conflicting_modules(devices: list[PciDevice], claim: Claim) -> tuple[set[str], set[str]]:
    """Return (softdep_modules, blacklist_modules)."""
    softdeps: set[str] = set()
    for dev in claim.functions:
        if dev.driver and dev.driver not in {"vfio-pci", "pcieport"}:
            softdeps.add(dev.driver.replace("-", "_"))
        softdeps.update(VENDOR_DRIVERS.get(dev.vendor, []) if dev.klass4 in GPU_CLASSES else [])
        if dev.klass4 == "0403":
            softdeps.add("snd_hda_intel")
        if dev.klass4 == "0c03":
            softdeps.add("xhci_pci")
        if dev.klass4 == "0c80":
            softdeps.add("i2c_designware_pci")

    survivors = {
        d.vendor for d in devices
        if d.klass4 in GPU_CLASSES and d.addr not in claim.addrs
    }
    blacklist: set[str] = set()
    for dev in claim.functions:
        if dev.klass4 not in GPU_CLASSES:
            continue
        if dev.vendor in survivors:
            console.print(
                f"[yellow]  ! Another {dev.vendor} GPU stays on the host; its driver will NOT "
                "be blacklisted (softdep ordering only).[/yellow]"
            )
            continue
        blacklist.update(VENDOR_DRIVERS.get(dev.vendor, []))
    blacklist -= NEVER_BLACKLIST
    softdeps -= {"vfio_pci"}
    return softdeps, blacklist


def audit_foreign_modprobe() -> None:
    """Two 'options vfio-pci ids=' lines in modprobe.d do not merge -- they fight."""
    offenders: list[Path] = []
    for directory in (Path("/etc/modprobe.d"), Path("/run/modprobe.d"), Path("/usr/lib/modprobe.d")):
        if not directory.is_dir():
            continue
        for conf in sorted(directory.glob("*.conf")):
            if conf == MODPROBE_FILE:
                continue
            text = conf.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^[ \t]*options[ \t]+vfio[-_]pci\b", text, re.MULTILINE):
                offenders.append(conf)
    if not offenders:
        return
    console.print(
        Panel(
            "Other modprobe.d files already set 'options vfio-pci':\n  "
            + "\n  ".join(str(p) for p in offenders)
            + "\n\nkmod concatenates every matching options line; a second ids= assignment "
            "overwrites the first array slot instead of merging. Consolidate into "
            f"{MODPROBE_FILE} or the claim will be non-deterministic.",
            title="modprobe.d conflict",
            border_style="yellow",
        )
    )


def write_modprobe_rules(claim: Claim, softdeps: set[str], blacklist: set[str]) -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Writing static driver-binding rules[/bold]")
    audit_foreign_modprobe()
    lines = [
        "# Managed by Arsonix (Phase 3). Regenerated on every run -- edit the pipeline,",
        "# not this file. Single source of truth for the VFIO claim.",
        "#",
        "# Claimed functions:",
    ]
    lines += [f"#   {d.addr}  {d.ids}  {d.klass_name}  {d.label}" for d in claim.functions]
    lines.append("")
    lines.append(f"options vfio-pci ids={','.join(claim.ids)}")
    lines.append("")
    for module in sorted(softdeps):
        lines.append(f"softdep {module} pre: vfio-pci")
    if blacklist:
        lines.append("")
        for module in sorted(blacklist):
            lines.append(f"blacklist {module}")
    payload = "\n".join(lines) + "\n"
    if atomic_write(MODPROBE_FILE, payload):
        console.print(f"[green]  ~[/green] {MODPROBE_FILE}")
    else:
        console.print(f"[bold green]  ok[/bold green] {MODPROBE_FILE} already convergent.")
    console.print(f"[dim]    ids={','.join(claim.ids)}[/dim]")
    console.print(f"[dim]    softdep: {', '.join(sorted(softdeps)) or 'none'}[/dim]")
    console.print(f"[dim]    blacklist: {', '.join(sorted(blacklist)) or 'none'}[/dim]")


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    global DRY_RUN
    parser = argparse.ArgumentParser(description="Arsonix Phase 3: VFIO isolation")
    parser.add_argument("--slot", help="pre-select a PCI slot, e.g. 0000:01:00")
    parser.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    parser.add_argument("--cmdline-ids", action="store_true",
                        help="also mirror vfio-pci.ids onto the kernel command line")
    parser.add_argument("--amd-force-enable", action="store_true",
                        help="emit amd_iommu=force_enable for boards with a broken IVRS")
    parser.add_argument("--no-rebuild", action="store_true", help="skip initramfs regeneration")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if not DRY_RUN:
        elevate()

    console.clear()
    console.print(
        Panel(
            "[bold green]Arsonix Phase 3 -- VFIO Isolation & Kernel Plumbing[/bold green]\n"
            "sysfs topology / ACS audit / bootctl / mkinitcpio",
            expand=False,
            border_style="green",
        )
    )
    if DRY_RUN:
        console.print("[magenta]DRY RUN: no file will be modified.[/magenta]")

    devices = enumerate_pci()
    claim = select_claim(devices, args.slot)
    vendor = cpu_vendor()
    softdeps, blacklist = conflicting_modules(devices, claim)

    inject_cmdline(claim, vendor, blacklist, args.amd_force_enable, args.cmdline_ids)
    configure_initramfs()
    write_modprobe_rules(claim, softdeps, blacklist)
    if not args.no_rebuild:
        regenerate_initramfs()

    state_merge(
        vfio_ids=claim.ids,
        vfio_addrs=claim.addrs,
        vfio_nodedevs=[d.nodedev for d in claim.functions],
        vfio_groups=sorted(claim.groups),
        vfio_slot=claim.functions[0].slot,
        cpu_vendor=vendor,
        phase_15="complete",
    )

    table = Table(title="Phase 3 -- VFIO claim", header_style="bold magenta")
    table.add_column("Address", style="cyan")
    table.add_column("IDs")
    table.add_column("Class")
    table.add_column("IOMMU", justify="center")
    table.add_column("libvirt nodedev", style="dim")
    for dev in claim.functions:
        table.add_row(dev.addr, dev.ids, dev.klass_name, dev.iommu_group or "?", dev.nodedev)
    console.print(table)

    console.print("\n[bold green]=== PHASE 3 COMPLETE ===[/bold green]")
    console.print(
        Panel(
            "REBOOT REQUIRED. Verify afterwards:\n"
            "  cat /proc/cmdline | tr ' ' '\\n' | grep -E 'iommu|vfio'\n"
            "  lspci -nnk -d " + claim.ids[0] + "     # Kernel driver in use: vfio-pci\n"
            "  dmesg | grep -i -e DMAR -e IOMMU -e vfio",
            border_style="yellow",
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
