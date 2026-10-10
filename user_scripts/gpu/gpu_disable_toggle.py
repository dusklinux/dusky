#!/usr/bin/env python3
"""gpu-disable-toggle — stage PCI removal of dedicated GPU(s) on Arch / kernel 7.3+.

Disables a discrete GPU for subsequent boots (for hybrid laptops
and systems with an unused dGPU). Vendor-agnostic: NVIDIA, AMD,
Intel-dGPU, or anything else presenting as VGA/3D/Display in sysfs.

Method:
  1. udev hide rule → ATTR{remove}="1" on add for exact PCI addresses and IDs,
     so the GPU is logically removed from the PCI bus at boot coldplug and any
     rescan. Hidden from normal PCI enumeration after the rule runs.
  2. /etc/modprobe.d/99-gpu-disable.conf → options vfio-pci ids=<slot IDs>,
     blacklist <vendor DRM drivers>, softdep <each> pre: vfio-pci (safety net
     if the bus is ever rescanned). Shared audio/USB are softdep'd, NEVER
     blacklisted.
  3. Kernel cmdline → vfio-pci.ids + module_blacklist + iommu=pt +
     intel_iommu=on (Intel) — vfio-pci probe fails with -EINVAL without IOMMU.
  4. mkinitcpio drop-in → early vfio stub so nothing else probes the card.

Enable reverses all of the above from /var/lib/gpu-disable/state.json.

PCI removal does not guarantee physical power-off or prevent built-in drivers
from probing before userspace. Firmware and kernel configuration still matter.
Targets kernel 7.3+ and Python 3.15+ only.

Usage:
  ./gpu_disable_toggle.py --status
  ./gpu_disable_toggle.py --disable [--slot 0000:01:00] [--all] [--dry-run] [--no-rebuild]
  ./gpu_disable_toggle.py --enable [--dry-run] [--no-rebuild]
  Optional ASUS notebook firmware gate: --disable --asus-power-gate

Supported boot pipeline: systemd-boot Type #1/#2 with mkinitcpio presets using
default configuration and command-line sources. Other generators need separate
configuration and are not silently substituted.
"""

import argparse
import errno
import fcntl
import fnmatch
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
from typing import NoReturn

MIN_PY = (3, 15, 0)
MIN_KERNEL = (7, 3)


def check_versions() -> None:
    m = re.prefixmatch(r"(\d+)\.(\d+)", Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").strip())
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_KERNEL:
        sys.stderr.write("[FATAL] Kernel 7.3+ required.\n")
        raise SystemExit(1)


# Enforce Python version before third-party imports
if sys.version_info[:3] < MIN_PY:
    sys.stderr.write(
        f"[FATAL] Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required, "
        f"have {sys.version.split()[0]}.\n"
    )
    raise SystemExit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt
    from rich.table import Table
except ModuleNotFoundError:
    sys.stderr.write("[FATAL] 'python-rich' is missing: pacman -S python-rich\n")
    raise SystemExit(1)

console = Console()

MODPROBE_FILE = Path("/etc/modprobe.d/99-gpu-disable.conf")
UDEV_RULE = Path("/etc/udev/rules.d/99-gpu-hide.rules")
PRESET_DIR = Path("/etc/mkinitcpio.d")
MKINITCPIO_CONF = Path("/etc/mkinitcpio.conf")
MKINITCPIO_DROPIN_DIR = Path("/etc/mkinitcpio.conf.d")
MKINITCPIO_DROPIN = MKINITCPIO_DROPIN_DIR / "99-gpu-disable.conf"
KERNEL_CMDLINE = Path("/etc/kernel/cmdline")
VENDOR_CMDLINE = Path("/usr/lib/kernel/cmdline")
CMDLINE_D = Path("/etc/cmdline.d")
CMDLINE_D_DROPIN = CMDLINE_D / "99-gpu-disable.conf"
STATE_DIR = Path("/var/lib/gpu-disable")
STATE_FILE = STATE_DIR / "state.json"
ASUS_DGPU_DISABLE = Path("/sys/devices/platform/asus-nb-wmi/dgpu_disable")
ASUS_ARMOURY_DGPU_DISABLE = Path("/sys/devices/virtual/firmware-attributes/asus-armoury/attributes/dgpu_disable/current_value")
ASUS_TMPFILES = Path("/etc/tmpfiles.d/99-asus-dgpu-disable.conf")

# Only these cmdline keys are owned. The iommu keys are required: without an
# active IOMMU, vfio-pci probe fails with -EINVAL and the GPU stays half-claimed.
MANAGED_KEYS = ("vfio-pci.ids", "module_blacklist", "iommu", "intel_iommu", "amd_iommu")
VOLATILE_TOKENS = {
    "single", "1", "s", "S", "rescue", "emergency", "init=/bin/sh",
    "systemd.unit=rescue.target", "systemd.unit=emergency.target",
    "systemd.debug-shell",
}
VOLATILE_PREFIXES = ("BOOT_IMAGE=", "initrd=")

GPU_CLASSES = {"0300", "0301", "0302", "0380"}

# DRM drivers to keep off the disabled card, per PCI GPU vendor.
VENDOR_DRM = frozendict({
    "10de": ("nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia_peermem"),
    "1002": ("amdgpu", "radeon"),
    "8086": ("i915", "xe"),
    "1af4": ("virtio_gpu",),
})

# Shared host infrastructure: reroute ordering only, never blacklist.
NEVER_BLACKLIST = {
    "snd_hda_intel", "snd_hda_codec_hdmi", "xhci_pci", "xhci_hcd",
    "i2c_designware_pci", "typec_ucsi", "ucsi_ccg", "ahci", "nvme",
}

SYS_PCI = Path("/sys/bus/pci/devices")

DRY_RUN = False


# --------------------------------------------------------------------------
# bootstrap / guards
# --------------------------------------------------------------------------

def elevate() -> None:
    if os.geteuid() == 0:
        return
    sudo = shutil.which("sudo")
    if sudo is None:
        sys.stderr.write("[FATAL] Need root (sudo not found). Re-run as root.\n")
        raise SystemExit(1)
    try:
        script = Path(sys.argv[0]).resolve(strict=True)
    except OSError:
        script = Path(sys.argv[0]).resolve()
    sys.stderr.write("[INFO] Elevating via sudo…\n")
    os.execv(sudo, [sudo, "--", sys.executable, str(script), *sys.argv[1:]])


def bail(msg: str) -> NoReturn:
    console.print(Panel(f"[bold red]FATAL[/bold red]\n{msg}", border_style="red"))
    raise SystemExit(1)


def run(argv: list[str], *, timeout: float = 30.0, input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Execute command safely, converting missing binary or timeout into a CompletedProcess."""
    try:
        return subprocess.run(
            argv, text=True, encoding="utf-8", errors="replace", capture_output=True,
            timeout=timeout, input=input,
            stdin=subprocess.DEVNULL if input is None else None, check=False,
            env=os.environ | {"LC_ALL": "C.UTF-8"},
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: command not found\n")
    except subprocess.TimeoutExpired:
        console.print(f"[yellow]  ! {argv[0]} timed out after {timeout}s.[/yellow]")
        return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]}: timed out after {timeout}s\n")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 126, "", str(exc))


def sync_directory(path: Path) -> None:
    """Persist rename/unlink metadata; report actual I/O failures."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.EOPNOTSUPP}:
                raise
    finally:
        os.close(fd)


def remove_file(path: Path) -> None:
    if DRY_RUN:
        console.print(f"[magenta]  (dry-run) would remove {path}[/magenta]")
        return
    path.unlink(missing_ok=True)
    sync_directory(path.parent)


def atomic_write(path: Path, content: str) -> bool:
    """Write atomically and durably, inheriting existing mode/ownership. Returns True if changed."""
    if path.is_symlink():
        path = Path(os.path.realpath(path))
    if path.exists():
        try:
            unchanged = path.read_text(encoding="utf-8") == content
        except (UnicodeDecodeError, OSError):
            unchanged = False
        if unchanged:
            if not DRY_RUN:
                sync_directory(path.parent)
            return False
        st = path.stat()
        mode, uid, gid = stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid
    else:
        mode, uid, gid = 0o644, 0, 0
    if DRY_RUN:
        console.print(f"[magenta]  (dry-run) would write {path}[/magenta]")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write(content)
            h.flush()
            # FAT uses mount-wide ownership. Ignore only unsupported permission
            # changes, never media errors or read-only filesystem failures.
            for operation, arguments in ((os.fchmod, (mode,)), (os.fchown, (uid, gid))):
                try:
                    operation(h.fileno(), *arguments)
                except OSError as exc:
                    if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP}:
                        raise
            os.fsync(h.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
        return True
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def state_load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        bail(f"Cannot read recovery state {STATE_FILE}: {exc}")
    if not isinstance(data, dict):
        bail(f"Invalid recovery state in {STATE_FILE}: expected an object.")
    for key in ("preserved_cmdline", "bridges"):
        value = data.get(key, {})
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            bail(f"Invalid recovery state: {key} must map strings to strings.")
    for key in ("action", "pending_action"):
        value = data.get(key)
        allowed = {None, "disabled", "enabled"} if key == "action" else {None, "disable", "enable"}
        if not isinstance(value, (str, type(None))) or value not in allowed:
            bail(f"Invalid recovery state: unexpected {key}.")
    for key in ("rebuilt", "asus_power_gate"):
        if key in data and not isinstance(data[key], bool):
            bail(f"Invalid recovery state: {key} must be boolean.")
    if data.get("asus_restore", "0") not in ("0", "1"):
        bail("Invalid recovery state: ASUS restore value must be 0 or 1.")
    if data.get("boot_id") is not None and not isinstance(data["boot_id"], str):
        bail("Invalid recovery state: boot_id must be a string.")
    for key in ("ids", "addrs", "slots", "blacklist"):
        if key in data and (not isinstance(data[key], list)
                            or not all(isinstance(v, str) for v in data[key])):
            bail(f"Invalid recovery state: {key} must be a list of strings.")
    return data


def state_save(**kv: object) -> None:
    if DRY_RUN:
        return
    data = state_load()
    data.update(kv)
    data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write(STATE_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# PCI discovery (sysfs is the source of truth)
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PciDevice:
    addr: str
    vendor: str
    device: str
    klass: str
    driver: str | None
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


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def lspci_labels() -> dict[str, str]:
    if shutil.which("lspci") is None:
        return {}
    res = run(["lspci", "-Dmm"], timeout=60)
    labels: dict[str, str] = {}
    for line in res.stdout.splitlines():
        try:
            f = shlex.split(line)
        except ValueError:
            continue
        if len(f) >= 4:
            labels[f[0]] = f"{f[2]} {f[3]}"
    return labels


def enumerate_pci() -> list[PciDevice]:
    labels = lspci_labels()
    devs: list[PciDevice] = []
    if not SYS_PCI.is_dir():
        bail("/sys/bus/pci/devices missing — not a PCI Linux host?")
    for node in sorted(SYS_PCI.iterdir()):
        vendor = _read(node / "vendor").removeprefix("0x")
        device = _read(node / "device").removeprefix("0x")
        klass = _read(node / "class").removeprefix("0x")
        if not vendor or not device or not klass:
            continue
        link = node / "driver"
        driver = link.resolve().name if link.is_symlink() else None
        devs.append(PciDevice(
            addr=node.name, vendor=vendor, device=device, klass=klass,
            driver=driver, boot_vga=_read(node / "boot_vga") == "1",
            label=labels.get(node.name, "unknown device"),
        ))
    return devs


@dataclass(slots=True)
class Claim:
    """Whole-slot claim: every PCI function in the slot(s) moves together."""
    functions: list[PciDevice] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return sorted({d.ids for d in self.functions})

    @property
    def addrs(self) -> list[str]:
        return [d.addr for d in self.functions]

    @property
    def slots(self) -> list[str]:
        return sorted({d.slot for d in self.functions})


def gpu_slots(devices: list[PciDevice]) -> list[str]:
    return sorted({d.slot for d in devices if d.klass[:4] in GPU_CLASSES})


def normalize_slot(slot_arg: str, slots: list[str]) -> str | None:
    slot_arg = slot_arg.lower()
    if not re.fullmatch(r"(?:[0-9a-f]{4}:)?[0-9a-f]{2}:[0-9a-f]{2}(?:\.[0-7])?", slot_arg):
        return None
    if "." in slot_arg:
        slot_arg = slot_arg.rsplit(".", 1)[0]
    if slot_arg in slots:
        return slot_arg
    hits = [s for s in slots if s.endswith(slot_arg)]
    return hits[0] if len(hits) == 1 else None


def claim_from_state(state: dict) -> Claim | None:
    """Rebuild a claim for a slot already hidden from the PCI bus.

    After bus removal sysfs no longer enumerates the functions, so the live
    lookup fails. State (written by the run that hid it) is the source of truth.
    """
    metadata = state.get("functions", [])
    if not isinstance(metadata, list):
        bail("Invalid recovery state: functions must be a list.")
    functions = []
    for item in metadata:
        if not isinstance(item, dict):
            bail("Invalid recovery state: function must be an object.")
        addr, ids, klass = item.get("addr"), item.get("ids"), item.get("klass4")
        if (not isinstance(addr, str) or not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", addr)
                or not isinstance(ids, str) or not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{4}", ids)
                or not isinstance(klass, str) or not re.fullmatch(r"[0-9a-f]{4}", klass)):
            bail("Invalid recovery state: malformed PCI function metadata.")
        driver = item.get("driver")
        if driver is not None and not isinstance(driver, str):
            bail("Invalid recovery state: driver must be a string or null.")
        vendor, device = ids.split(":")
        functions.append(PciDevice(addr, vendor, device, klass + "00", driver, False,
                                   "hidden (removed from PCI bus)"))
    # Sorted unique IDs cannot be paired with addresses reliably. Never guess.
    if not functions and state.get("addrs"):
        bail("Recovery state lacks per-function metadata. Use --enable first.")
    return Claim(functions) if functions else None


def drm_blacklist_for(devices: list[PciDevice], claim: Claim) -> tuple[set[str], set[str]]:
    """Return (softdeps, blacklist). Shared host infra is softdep'd, never blacklisted."""
    softdeps: set[str] = set()
    for dev in claim.functions:
        drv = dev.driver.replace("-", "_") if dev.driver else None
        if drv and drv not in {"vfio_pci", "pcieport"}:
            softdeps.add(drv)
        if dev.klass4 in GPU_CLASSES:
            softdeps.update(VENDOR_DRM.get(dev.vendor, []))
        if dev.klass4 == "0403":
            softdeps.add("snd_hda_intel")
        if dev.klass4 in ("0c03", "0c80"):
            softdeps.add("xhci_pci")

    survivors = {d.vendor for d in devices
                 if d.klass4 in GPU_CLASSES and d.addr not in claim.addrs}
    blacklist: set[str] = set()
    for dev in claim.functions:
        if dev.klass4 not in GPU_CLASSES:
            continue
        if dev.vendor in survivors:
            console.print(
                f"[yellow]  ! Another {dev.vendor} GPU stays on the host; its driver "
                "will NOT be blacklisted (softdep ordering only).[/yellow]"
            )
            continue
        blacklist.update(VENDOR_DRM.get(dev.vendor, []))
        drv = dev.driver.replace("-", "_") if dev.driver else None
        if drv and drv not in NEVER_BLACKLIST and drv != "vfio_pci":
            # Live driver unknown to the map: still block it.
            blacklist.add(drv)
    surviving_drivers = {d.driver.replace("-", "_") for d in devices
                         if d.addr not in claim.addrs and d.driver}
    blacklist -= NEVER_BLACKLIST | surviving_drivers
    softdeps -= {"vfio_pci"}
    return softdeps, blacklist


def render_gpus(devices: list[PciDevice], slots: list[str]) -> None:
    table = Table(title="GPU slots (sysfs)", header_style="bold magenta")
    table.add_column("#", justify="center", style="cyan")
    table.add_column("Slot", style="dim")
    table.add_column("Functions", style="green")
    table.add_column("Driver", style="yellow")
    table.add_column("Flags")
    for i, slot in enumerate(slots, 1):
        funcs = [d for d in devices if d.slot == slot]
        fn = "\n".join(f"{d.addr} [{d.ids}] {d.klass4} {d.label}" for d in funcs)
        drv = "\n".join(d.driver or "-" for d in funcs)
        flags = []
        if any(d.boot_vga for d in funcs):
            flags.append("[bold red]boot_vga[/bold red]")
        if any(d.driver == "vfio-pci" for d in funcs):
            flags.append("[green]vfio-pci[/green]")
        table.add_row(str(i), slot, fn, drv, " ".join(flags))
    console.print(table)


def select_claim(devices: list[PciDevice], slots: list[str], args: argparse.Namespace) -> Claim:
    if args.slot is not None:
        slot = normalize_slot(args.slot, slots)
        if slot is None:
            # May already be hidden from the bus — caller falls back to state.
            return Claim()
        funcs = [d for d in devices if d.slot == slot]
        return Claim(functions=funcs)

    dedicated = [s for s in slots
                 if not any(d.boot_vga for d in devices if d.slot == s)]
    if args.all:
        if not dedicated:
            bail("No non-boot_vga GPU slot found — nothing safe to disable. "
                 "Use --slot with --allow-boot-vga if you really mean the boot GPU.")
        funcs = [d for d in devices if d.slot in dedicated]
        return Claim(functions=funcs)

    headless = args.auto or not sys.stdin.isatty()
    if len(dedicated) == 1 and headless:
        console.print(f"[dim]Single dedicated GPU slot: {dedicated[0]}[/dim]")
        return Claim(functions=[d for d in devices if d.slot == dedicated[0]])
    if len(slots) == 1 and headless:
        return Claim(functions=[d for d in devices if d.slot == slots[0]])
    if headless:
        render_gpus(devices, slots)
        bail(f"Ambiguous: {len(slots)} GPU slot(s) and {len(dedicated)} dedicated -- "
             "no interactive TTY to ask. Pass --slot <addr> or --all.")

    render_gpus(devices, slots)
    idx = IntPrompt.ask("Slot to disable", choices=[str(i + 1) for i in range(len(slots))])
    slot = slots[idx - 1]
    return Claim(functions=[d for d in devices if d.slot == slot])


def check_id_collisions(devices: list[PciDevice], claim: Claim) -> None:
    """vfio-pci.ids is ID-based; module_blacklist blocks entire modules.

    If a device outside the claim shares vendor:device with one inside it, the
    selection cannot be expressed without collateral damage -- the surviving
    device would get bound by vfio-pci and its driver blacklisted anyway.
    Refuse rather than half-disable a surviving GPU.
    """
    claimed = set(claim.ids)
    strays = sorted(f"{d.addr} [{d.ids}] {d.klass4} {d.label}"
                    for d in devices
                    if d.ids in claimed and d.addr not in claim.addrs)
    if strays:
        detail = "\n  ".join(strays)
        bail(f"ID collision: the following devices outside the selected claim share "
             f"IDs with claimed hardware:\n  {detail}\n\n"
             "vfio-pci.ids is ID-based, so these devices would be claimed too.\n"
             "Use --all to take all matching GPU slots, or pick a different slot.")


def guard_claim(claim: Claim, devices: list[PciDevice], allow_boot_vga: bool,
                *, from_state: bool = False) -> None:
    if from_state:
        # Claim was synthesised from state.json because the card is already hidden
        # from the PCI bus. Live-topology checks are meaningless here.
        return
    if any(d.boot_vga for d in claim.functions):
        if not allow_boot_vga:
            slots = ", ".join(claim.slots)
            bail(f"Slot {slots} is the firmware boot VGA device. Refusing to blind "
                 "the host console. Re-run with --allow-boot-vga if you truly have "
                 "serial/SSH access and mean it.")
        console.print("[bold yellow]  ! Disabling the boot_vga device. Host console will go dark.[/bold yellow]")
    remaining = [s for s in gpu_slots(devices) if s not in claim.slots]
    if not remaining and not allow_boot_vga:
        bail("This would disable the last display GPU in the system. Re-run with "
             "--allow-boot-vga (and preferably SSH access) if you mean it.")
    if not remaining:
        console.print("[bold yellow]  ! This disables the last display GPU in the system.[/bold yellow]")


def warn_modprobe_conflicts() -> None:
    """modprobe concatenates every matching 'options' line -- duplicates are
    ambiguous. Also flag any hybrid-GPU managers which fight this tool."""
    conflicts = []
    seen = set()
    pat = re.compile(r"^\s*options\s+vfio[-_]pci\b.*\bids=", re.MULTILINE)
    for d in (Path("/etc/modprobe.d"), Path("/run/modprobe.d"),
              Path("/usr/local/lib/modprobe.d"), Path("/usr/lib/modprobe.d")):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.conf")):
            if f.name in seen:
                continue
            seen.add(f.name)
            if f.name == MODPROBE_FILE.name:
                continue
            if pat.search(_read(f).replace("\\\n", "")):
                conflicts.append(str(f))
    if conflicts:
        bail("Conflicting vfio-pci ids settings: " + ", ".join(conflicts)
             + ". Consolidate them before disabling a GPU.")
    for unit in ("optimus-manager.service", "supergfxd.service"):
        if run(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0:
            console.print(f"[bold yellow]  ! {unit} is enabled; it rebinds GPU drivers at "
                          "runtime and will fight this configuration.[/bold yellow]")


# --------------------------------------------------------------------------
# kernel cmdline merge
# --------------------------------------------------------------------------

def desired_params(blacklist: set[str], ids: list[str], vendor: str, amd_force: bool,
                   baseline: dict[str, str] | None = None) -> dict[str, str]:
    params = dict(baseline or {})
    params["iommu"] = "pt"
    match vendor:
        case "intel":
            params["intel_iommu"] = "on"
        case "amd":
            # AMD-Vi is on by default with a sane IVRS; only force on request.
            if amd_force:
                params["amd_iommu"] = "force_enable"
        case _:
            console.print("[yellow]  ! Unknown CPU vendor; emitting iommu=pt only.[/yellow]")
    for key, additions in (("vfio-pci.ids", ids), ("module_blacklist", blacklist)):
        values = {v for v in params.get(key, "").strip('"').split(",") if v}
        values.update(additions)
        if values:
            params[key] = ",".join(sorted(values))
    return params


def cpu_vendor() -> str:
    text = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    if "GenuineIntel" in text:
        return "intel"
    if "AuthenticAMD" in text:
        return "amd"
    return "unknown"


def cmdline_tokens(text: str) -> list[str]:
    """Preserve kernel double-quoted values, including quotes after '='."""
    text = " ".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    if text.count('"') % 2:
        bail("Unbalanced double quotes in kernel command line.")
    return re.findall(r'(?:[^\s"]|"[^"]*")+', text)


def merge_cmdline(current: str, desired: dict[str, str], *, running_seed: bool = False) -> str:
    """Replace managed tokens, preserving unrelated persistent boot arguments."""
    tokens = cmdline_tokens(current)
    kept: list[str] = []
    tail: list[str] = []
    seen_sep = False
    for tok in tokens:
        if seen_sep:
            tail.append(tok)
            continue
        if tok == "--":
            seen_sep = True
            continue
        if running_seed and (tok in VOLATILE_TOKENS or tok.startswith(VOLATILE_PREFIXES)):
            continue
        if tok.split("=", 1)[0].replace("vfio_pci.", "vfio-pci.") in MANAGED_KEYS:
            continue
        kept.append(tok)
    for key in MANAGED_KEYS:
        if key in desired:
            kept.append(f"{key}={desired[key]}")
    if seen_sep:
        kept += ["--", *tail]
    return " ".join(kept)


# --------------------------------------------------------------------------
# bootloader
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BootEntry:
    kind: str  # "type1" | "type2"
    path: Path | None
    options: str
    ident: str


def _esp_roots() -> list[Path]:
    roots: list[Path] = []
    for flag in ("--print-boot-path", "--print-esp-path"):
        res = run(["bootctl", flag])
        if res.returncode == 0 and res.stdout.strip():
            cand = Path(res.stdout.splitlines()[0].strip())
            if cand.is_dir() and cand not in roots:
                roots.append(cand)
    for cand in (Path("/boot"), Path("/efi")):
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    return roots


def scan_entries_on_disk() -> BootEntry | None:
    """Use an unambiguous on-disk default when bootctl cannot inspect the ESP."""
    candidates = []
    for root in _esp_roots():
        confs = sorted((root / "loader" / "entries").glob("*.conf"))
        candidates.extend(confs)
        defaults = re.findall(r"^default[ \t]+(\S+)", _read(root / "loader" / "loader.conf"), re.MULTILINE)
        if defaults:
            pattern = defaults[-1]
            if not pattern.endswith(".conf"):
                pattern += ".conf"
            matches = [p for p in confs if fnmatch.fnmatchcase(p.name, pattern)]
            if len(matches) == 1:
                return BootEntry("type1", matches[0], "", matches[0].name)
    if len(candidates) == 1:
        return BootEntry("type1", candidates[0], "", candidates[0].name)
    return None


def _patchable_type(typ: str) -> bool:
    """Accept the documented systemd 262 boot entry types."""
    return typ in ("type1", "type2")


def _resolve_entry_path(entry: dict, ident: str) -> Path | None:
    # systemd 262 exposes the absolute entry path.
    ps = entry.get("path")
    if isinstance(ps, str) and ps:
        cand = Path(ps)
        if cand.suffix in {".conf", ".efi"} and cand.is_file():
            return cand
    if ident.endswith(".conf"):
        for root in _esp_roots():
            hit = root / "loader" / "entries" / ident
            if hit.exists():
                return hit
    return None


def find_boot_entry(*, quiet: bool = False, ident: str | None = None) -> BootEntry | None:
    res = run(["bootctl", "--no-pager", "list", "--json=short"])
    raw: list[dict] = []
    if res.returncode == 0 and res.stdout.strip().startswith(("[", "{")):
        try:
            parsed = json.loads(res.stdout)
            raw = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            raw = []
    if raw:
        def score(e: dict) -> int:
            # Prefer the configured default over the currently selected entry.
            dfl = bool(e.get("isDefault", False))
            sel = bool(e.get("isSelected", False))
            return (2 if dfl else 0) + (1 if sel else 0)
        cands = [e for e in raw if isinstance(e, dict) and _patchable_type(str(e.get("type", "")))]
        if ident is not None:
            cands = [e for e in cands if e.get("id") == ident]
        if cands:
            if ident is None and not any(score(e) for e in cands) and len(cands) != 1:
                return None
            best = max(cands, key=score)
            typ = str(best.get("type", ""))
            kind = "type2" if typ == "type2" else "type1"
            ident = str(best.get("id", "?"))
            path = _resolve_entry_path(best, ident)
            why = "default" if score(best) >= 2 else "selected"
            if not quiet:
                console.print(f"[dim]Boot entry: {ident} ({kind}, {why})[/dim]")
            if kind == "type1" and path is None:
                if not quiet:
                    console.print("[yellow]  ! bootctl gave no usable .conf path; "
                                  "falling back to an on-disk scan.[/yellow]")
            else:
                return BootEntry(kind, path,
                                 str(best.get("options", "") or ""),
                                 ident)
    # Fallback: on-disk scan (covers ESPs bootctl rejects by partition type).
    if ident is not None:
        path = _resolve_entry_path({}, ident)
        return BootEntry("type1", path, "", ident) if path else None
    found = scan_entries_on_disk()
    if found is not None:
        if not quiet:
            console.print(f"[yellow]  ! bootctl JSON unavailable; using {found.path}[/yellow]")
        return found
    return None


def resolve_boot_entry(state: dict | None = None) -> BootEntry:
    ident = (state or {}).get("boot_id")
    entry = find_boot_entry(ident=ident)
    if entry is None:
        bail(f"No unambiguous systemd-boot entry found to patch ({ident or 'default'}).")
    return entry


def read_target_options(entry: BootEntry) -> str:
    """Return the raw options text of whichever file patch_bootloader would edit."""
    if entry.kind == "type1" and entry.path is not None and entry.path.exists():
        return " ".join(re.findall(r"^[ \t]*options[ \t]+(.*)$",
                                  entry.path.read_text(encoding="utf-8"), re.MULTILINE))
    parts = []
    for path in (KERNEL_CMDLINE, VENDOR_CMDLINE):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
            break
    parts.extend(p.read_text(encoding="utf-8") for p in sorted_dropins(CMDLINE_D))
    return "\n".join(parts) if parts else entry.options


def parse_managed_keys(options_text: str) -> dict[str, str]:
    """Parse and normalize all MANAGED_KEYS present in options_text."""
    snap: dict[str, str] = {}
    tokens = cmdline_tokens(options_text)
    for tok in tokens:
        if tok == "--":
            break
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        key = k.replace("vfio_pci.", "vfio-pci.")
        if key in MANAGED_KEYS:
            if key in snap:
                bail(f"Duplicate managed kernel parameter: {key}.")
            snap[key] = v
    return snap


def managed_snapshot(options_text: str) -> dict[str, str]:
    """Capture the baseline before any writes, then reuse it until enable succeeds."""
    return parse_managed_keys(options_text)


def patch_bootloader(entry: BootEntry, blacklist: set[str], ids: list[str], vendor: str,
                     amd_force: bool, *, enable: bool,
                     restore: dict[str, str] | None = None) -> None:
    if enable:
        desired = dict(restore) if restore else {}
    else:
        desired = desired_params(blacklist, ids, vendor, amd_force, restore)
    if entry.kind == "type1":
        if entry.path is None or entry.path.suffix != ".conf":
            bail(f"Type #1 entry '{entry.ident}' has no writable .conf.")
        content = entry.path.read_text(encoding="utf-8")
        merged = merge_cmdline(read_target_options(entry), desired)
        lines = content.splitlines(keepends=True)
        new_lines = []
        replaced = False
        for line in lines:
            if re.prefixmatch(r"[ \t]*options[ \t]+", line):
                if not replaced:
                    new_lines.append(f"options {merged}\n")
                    replaced = True
            else:
                new_lines.append(line)
        new = "".join(new_lines)
        if not replaced:
            new = new.rstrip("\n") + f"\noptions {merged}\n"
        if atomic_write(entry.path, new):
            console.print(f"[green]  ~[/green] {entry.path.name}: options {merged}")
        else:
            console.print(f"[bold green]  ok[/bold green] {entry.path.name} already convergent.")
    else:
        foreign = [p for p in CMDLINE_D.glob("*.conf") if p != CMDLINE_D_DROPIN]
        if foreign and not KERNEL_CMDLINE.exists() and not VENDOR_CMDLINE.exists():
            # The existing fragments supply root and other boot options. Own
            # only the managed fragment, avoiding copies of foreign arguments.
            if desired:
                atomic_write(CMDLINE_D_DROPIN,
                             " ".join(f"{k}={v}" for k, v in desired.items()) + "\n")
            elif CMDLINE_D_DROPIN.exists():
                if DRY_RUN:
                    console.print(f"[magenta]  (dry-run) would remove {CMDLINE_D_DROPIN}[/magenta]")
                else:
                    remove_file(CMDLINE_D_DROPIN)
            return
        running_seed = not entry.options
        seed = entry.options or _read(Path("/proc/cmdline"))
        for path in (KERNEL_CMDLINE, VENDOR_CMDLINE):
            if path.exists():
                seed = path.read_text(encoding="utf-8")
                running_seed = False
                break
        merged = merge_cmdline(seed, desired, running_seed=running_seed)
        if atomic_write(KERNEL_CMDLINE, merged + "\n"):
            console.print(f"[green]  ~[/green] {KERNEL_CMDLINE}: {merged}")
        if CMDLINE_D_DROPIN.exists():
            if DRY_RUN:
                console.print(f"[magenta]  (dry-run) would remove {CMDLINE_D_DROPIN}[/magenta]")
            else:
                remove_file(CMDLINE_D_DROPIN)


# --------------------------------------------------------------------------
# modprobe + initramfs
# --------------------------------------------------------------------------

def write_modprobe(claim: Claim, softdeps: set[str], blacklist: set[str],
                   *, ids: list[str] | None = None) -> None:
    ids = claim.ids if ids is None else ids
    lines = [
        "# Managed by gpu-disable-toggle. Do not hand-edit — re-run the script.",
        "#",
        "# Disabled slot functions:",
    ]
    lines += [f"#   {d.addr}  {d.ids}  {d.klass4}  {d.label}" for d in claim.functions]
    lines += ["", f"options vfio-pci ids={','.join(ids)}", ""]
    for mod in sorted(softdeps):
        lines.append(f"softdep {mod} pre: vfio-pci")
    if blacklist:
        lines.append("")
        for mod in sorted(blacklist):
            lines.append(f"blacklist {mod}")
    payload = "\n".join(lines) + "\n"
    if atomic_write(MODPROBE_FILE, payload):
        console.print(f"[green]  ~[/green] {MODPROBE_FILE}")
    else:
        console.print(f"[bold green]  ok[/bold green] {MODPROBE_FILE} already convergent.")
    console.print(f"[dim]    ids={','.join(ids)}[/dim]")
    console.print(f"[dim]    blacklist: {', '.join(sorted(blacklist)) or 'none'}[/dim]")


def remove_modprobe() -> None:
    if not MODPROBE_FILE.exists():
        console.print(f"[dim]No {MODPROBE_FILE.name} present.[/dim]")
        return
    if DRY_RUN:
        console.print(f"[magenta]  (dry-run) would remove {MODPROBE_FILE}[/magenta]")
        return
    remove_file(MODPROBE_FILE)
    console.print(f"[green]  ~[/green] removed {MODPROBE_FILE}")


def write_udev_hide(claim: Claim) -> None:
    """Logically remove the slot's functions from the PCI bus at add-time.

    The rule fires on add events, including boot coldplug and rescans.
    Removal hides the device from normal PCI enumeration; it does not
    guarantee physical power-off or exclude earlier kernel probing.

    Matched on KERNEL (the PCI address) AND vendor:device, so an identical
    second card in another slot is never removed, and a card swap stops
    matching instead of removing the wrong device.
    """
    lines = [
        "# Managed by gpu-disable-toggle. Logically removes the disabled GPU",
        "# from the PCI bus at add-time (boot coldplug + any rescan), so it is",
        "# hidden from normal PCI enumeration after the rule runs.",
    ]
    for dev in sorted(claim.functions, key=lambda d: d.addr):
        lines.append(
            f'ACTION=="add", SUBSYSTEM=="pci", KERNEL=="{dev.addr}", '
            f'ATTR{{vendor}}=="0x{dev.vendor}", ATTR{{device}}=="0x{dev.device}", '
            'ATTR{remove}="1"'
        )
    payload = "\n".join(lines) + "\n"
    changed = atomic_write(UDEV_RULE, payload)
    if DRY_RUN:
        return
    verified = run(["udevadm", "verify", str(UDEV_RULE)])
    if verified.returncode:
        bail(f"udev hide rule failed validation: {verified.stderr.strip()}")
    if changed:
        console.print(f"[green]  ~[/green] {UDEV_RULE}")
    else:
        console.print(f"[bold green]  ok[/bold green] {UDEV_RULE.name} already convergent.")
    # A prior rename may have succeeded before a sync/reload failure. Reload
    # even when the on-disk rule is already identical on retry.
    reload_udev()


def remove_udev_hide() -> None:
    if not UDEV_RULE.exists():
        console.print(f"[dim]No {UDEV_RULE.name} present.[/dim]")
        if not DRY_RUN:
            reload_udev()
        return
    if DRY_RUN:
        console.print(f"[magenta]  (dry-run) would remove {UDEV_RULE}[/magenta]")
        return
    remove_file(UDEV_RULE)
    console.print(f"[green]  ~[/green] removed {UDEV_RULE}")
    reload_udev()


def reload_udev() -> None:
    res = run(["udevadm", "control", "--reload"])
    if res.returncode != 0:
        console.print("[yellow]  ! udevadm reload failed; rule applies after reboot.[/yellow]")
    else:
        console.print("[dim]    udev rules reloaded.[/dim]")


def asus_wmi_available() -> bool:
    return ASUS_ARMOURY_DGPU_DISABLE.exists() or ASUS_DGPU_DISABLE.exists()


def set_asus_dgpu_disable(disabled: bool, *, restore: str = "0") -> None:
    """Stage the firmware power gate for the next boot, leaving live GPUs alone."""
    if disabled:
        paths = [p for p in (ASUS_ARMOURY_DGPU_DISABLE, ASUS_DGPU_DISABLE) if p.exists()]
        if paths:
            payload = "# Managed by gpu-disable-toggle. Apply firmware dGPU disable at boot.\n"
            payload += f"w- {paths[0]} - - - - 1\n"
            atomic_write(ASUS_TMPFILES, payload)
    elif ASUS_TMPFILES.exists():
        if DRY_RUN:
            console.print(f"[magenta]  (dry-run) would remove {ASUS_TMPFILES}[/magenta]")
        else:
            remove_file(ASUS_TMPFILES)
    # Some firmware retains this setting across reboot. Restoring it is required
    # for enable, but disable must never cut power during the active session.
    if not disabled:
        for path in (ASUS_ARMOURY_DGPU_DISABLE, ASUS_DGPU_DISABLE):
            if path.exists():
                if DRY_RUN:
                    console.print(f"[magenta]  (dry-run) would set {path} to {restore}[/magenta]")
                else:
                    path.write_text(restore, encoding="utf-8")
                return
        bail("ASUS firmware gate cannot be restored: its sysfs attribute is missing. Recovery state retained.")


def find_upstream_bridge(slot: str) -> Path | None:
    """Resolve the actual PCI parent instead of guessing bus ranges or domains."""
    for node in SYS_PCI.glob(f"{slot}.*"):
        parent = node.resolve().parent
        if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", parent.name):
            return SYS_PCI / parent.name
    return None


def pci_bridge_power_state(bridge: Path) -> tuple[str, str]:
    return (_read(bridge / "power_state") or "unknown",
            _read(bridge / "power" / "runtime_status") or "unknown")


def sorted_dropins(directory: Path, *, extra: Path | None = None) -> list[Path]:
    names = {p.name for p in directory.glob("*.conf") if p.is_file()}
    if extra is not None:
        names.add(extra.name)
    if not names:
        return []
    result = run(["sort", "--zero-terminated", "--version-sort", "--unique"],
                 input="\0".join(sorted(names)) + "\0")
    if result.returncode:
        bail(f"Cannot order configuration drop-ins: {result.stderr.strip()}")
    return [directory / name for name in result.stdout.split("\0") if name]


def effective_config(*, managed_payload: str | None = None) -> tuple[list[str], list[str], list[str]]:
    """Evaluate Bash arrays in the same version order as mkinitcpio 42.2."""
    sources = [f"source {shlex.quote(str(MKINITCPIO_CONF))}"]
    for path in sorted_dropins(MKINITCPIO_DROPIN_DIR,
                               extra=MKINITCPIO_DROPIN if managed_payload is not None else None):
        if path == MKINITCPIO_DROPIN:
            if managed_payload is not None:
                sources.append(managed_payload)
        else:
            sources.append(f"source {shlex.quote(str(path))}")
    script = "set -e\nMODULES=()\nFILES=()\nHOOKS=()\n" + "\n".join(sources)
    script += '\nprintf "%s\\0" "${#MODULES[@]}" "${MODULES[@]}" "${#FILES[@]}" "${FILES[@]}" "${#HOOKS[@]}" "${HOOKS[@]}"\n'
    proc = run(["bash", "--noprofile", "--norc", "-c", script])
    if proc.returncode:
        bail(f"Cannot evaluate mkinitcpio configuration: {proc.stderr.strip()}")
    fields = proc.stdout.split("\0")[:-1]
    arrays = []
    try:
        for _ in range(3):
            count = int(fields.pop(0))
            if count < 0 or count > len(fields):
                raise ValueError("invalid array length")
            arrays.append(fields[:count])
            del fields[:count]
        if fields:
            raise ValueError("unexpected output")
    except (IndexError, ValueError):
        bail("Unexpected output evaluating mkinitcpio configuration; check for commands printing to stdout.")
    return arrays[0], arrays[1], arrays[2]


def effective_hooks() -> list[str]:
    return effective_config()[2]


def initramfs_payload() -> str:
    payload = (
        "# Managed by gpu-disable-toggle. Load vfio-pci before explicit GPU modules.\n"
        'MODULES=(vfio_pci "${MODULES[@]}")\n'
        f'FILES+=({shlex.quote(str(UDEV_RULE))})\n'
    )
    if "modconf" not in effective_hooks():
        payload += "HOOKS+=(modconf)\n"
    modules, files, hooks = effective_config(managed_payload=payload)
    if (not modules or modules[0].replace("-", "_") != "vfio_pci"
            or str(UDEV_RULE) not in files or "modconf" not in hooks
            or not ({"udev", "systemd"} & set(hooks))):
        bail("Effective mkinitcpio drop-ins override the required VFIO modules, hide rule, or hooks.")
    return payload


def configure_initramfs(*, enable: bool) -> None:
    """Own exactly one file: MKINITCPIO_DROPIN. Never rewrite user config files."""
    if enable:
        if not MKINITCPIO_DROPIN.exists():
            console.print(f"[dim]No {MKINITCPIO_DROPIN.name} present.[/dim]")
            return
        if DRY_RUN:
            console.print(f"[magenta]  (dry-run) would remove {MKINITCPIO_DROPIN}[/magenta]")
            return
        remove_file(MKINITCPIO_DROPIN)
        console.print(f"[green]  ~[/green] removed {MKINITCPIO_DROPIN.name}")
        return

    payload = initramfs_payload()

    if atomic_write(MKINITCPIO_DROPIN, payload):
        console.print(f"[green]  ~[/green] {MKINITCPIO_DROPIN}")
    else:
        console.print(f"[bold green]  ok[/bold green] {MKINITCPIO_DROPIN.name} already convergent.")


def preflight(entry: BootEntry, *, enable: bool) -> list[Path]:
    """Validate the supported boot pipeline before touching persistent config."""
    for command in ("mkinitcpio", "bash", "udevadm", "sort", "lsinitcpio"):
        if shutil.which(command) is None:
            bail(f"Required command missing: {command}. This tool uses mkinitcpio.")
    presets = sorted(PRESET_DIR.glob("*.preset"))
    if not presets:
        bail("No mkinitcpio presets found.")
    if not enable:
        initramfs_payload()
    if entry.kind == "type2":
        for fragment in CMDLINE_D.glob("*.conf"):
            if fragment != CMDLINE_D_DROPIN and parse_managed_keys(fragment.read_text(encoding="utf-8")):
                bail(f"{fragment} sets managed parameters; consolidate them in {KERNEL_CMDLINE} first.")
    outputs = []
    for preset in presets:
        # Presets are Bash configuration, not assignments that can safely be
        # inferred with regex. Evaluate each in an isolated shell, as mkinitcpio does.
        script = r'''set -e
source "$1"
for p in "${PRESETS[@]}"; do
    config="${p}_config"; options="${p}_options"; cmdline="${p}_cmdline"
    uki="${p}_uki"; image="${p}_image"; kver="${p}_kver"
    printf '%s\0' "${!config:-$ALL_config}" "${!options}" "${!cmdline:-$ALL_cmdline}" "${!uki}" "${!image}" "${!kver:-$ALL_kver}"
done
'''
        proc = run(["bash", "--noprofile", "--norc", "-c", script, "gpu-presets", str(preset)])
        if proc.returncode:
            bail(f"Cannot evaluate {preset}: {proc.stderr.strip()}")
        fields = proc.stdout.split("\0")[:-1]
        if len(fields) % 6:
            bail(f"Unexpected output evaluating {preset}.")
        for i in range(0, len(fields), 6):
            config, options, cmdline, uki, image, kver = fields[i:i + 6]
            if not kver:
                continue
            flags = shlex.split(options)
            if config or any(f in {"-c", "--config"} or f.startswith("--config=") for f in flags):
                bail(f"{preset} bypasses mkinitcpio drop-ins with an explicit config. Remove that override first.")
            if entry.kind == "type2" and (cmdline or any(f.startswith("--cmdline") or f == "--no-cmdline" for f in flags)):
                bail(f"{preset} overrides the UKI command line. Use the default command-line source first.")
            outputs.extend(Path(v) for v in (uki, image) if v)
    if entry.kind == "type2" and (entry.path is None or entry.path not in outputs):
        bail("The selected UKI is not regenerated by a mkinitcpio preset.")
    if entry.kind == "type1" and entry.path is not None:
        content = entry.path.read_text(encoding="utf-8")
        images = re.findall(r"^[ \t]*initrd[ \t]+(\S+)", content, re.MULTILINE)
        boot_root = entry.path.parents[2]
        if not any(boot_root / image.lstrip("/") in outputs for image in images):
            bail("The selected boot entry's initramfs is not regenerated by a mkinitcpio preset.")
    if not outputs:
        bail("mkinitcpio presets define no generated images.")
    # Validate quoted command-line syntax before saving state or any boot edits.
    cmdline_tokens(read_target_options(entry))
    # Rebuild -P affects all presets: verify each generated image, including UKIs.
    return sorted(set(outputs))


def verify_images(images: list[Path], *, enable: bool) -> None:
    expected = {str(path).lstrip("/") for path in (UDEV_RULE, MODPROBE_FILE)}
    for image in images:
        result = run(["lsinitcpio", "--nocolor", "--list", str(image)], timeout=120)
        if result.returncode:
            bail(f"Cannot inspect rebuilt image {image}: {result.stderr.strip()}")
        contents = {line.removeprefix("./") for line in result.stdout.splitlines()}
        present = expected & contents
        if (enable and present) or (not enable and present != expected):
            bail(f"Rebuilt image {image} contains incorrect GPU hide files. Recovery state retained.")
    console.print("[bold green]  ok[/bold green] GPU hide files verified in rebuilt images.")


def rebuild_initramfs(*, no_rebuild: bool) -> None:
    if no_rebuild or DRY_RUN:
        console.print("[yellow]Images were not rebuilt; rebuild before rebooting.[/yellow]")
        return
    console.print("\n[bold blue]==>[/bold blue] Rebuilding initramfs / UKI: mkinitcpio -P")
    proc = subprocess.run(["mkinitcpio", "-P"], check=False, stdin=subprocess.DEVNULL)
    if proc.returncode:
        bail(f"mkinitcpio failed (rc={proc.returncode}). Recovery state retained; fix before rebooting.")
    console.print("[bold green]  ok[/bold green] Images regenerated.")


def verify_staged_entry(entry: BootEntry, expected: dict[str, str]) -> None:
    """Verify that the staged boot options match the expected values."""
    refreshed = find_boot_entry(ident=entry.ident)
    if refreshed is None or refreshed.kind != entry.kind:
        bail(f"Cannot verify the original boot entry {entry.ident}.")
    options = (
        refreshed.options
        if refreshed.kind == "type2"
        else read_target_options(refreshed)
    )
    actual = parse_managed_keys(options)
    all_keys = set(expected.keys()) | set(actual.keys())
    mismatches = []
    for k in sorted(all_keys):
        exp_v = expected.get(k)
        act_v = actual.get(k)
        if exp_v != act_v:
            mismatches.append(f"{k}: expected '{exp_v or '(absent)'}', got '{act_v or '(absent)'}'")
    if mismatches:
        bail("Staged boot options failed verification:\n" + "\n".join(mismatches)
             + "\nRecovery state retained. Fix the build before rebooting.")
    else:
        console.print("[bold green]  ok[/bold green] Staged boot entry verified.")


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def do_status() -> None:
    devices = enumerate_pci()
    slots = gpu_slots(devices)
    render_gpus(devices, slots)
    modprobe = MODPROBE_FILE.exists()
    hide = UDEV_RULE.exists()
    dropin = MKINITCPIO_DROPIN.exists()
    running_cmdline = _read(Path("/proc/cmdline"))
    running_ids = re.search(r"vfio[-_]pci\.ids=([0-9a-fA-F:,]+)", running_cmdline)
    state = state_load()
    action = state.get("action", "unknown" if state else "none")
    pending = state.get("pending_action")
    staged_ids = state.get("ids", [])

    staged_cmdline_ids = None
    entry = find_boot_entry(quiet=True, ident=state.get("boot_id") if action == "disabled" or pending else None)
    if entry:
        opts = read_target_options(entry)
        m = re.search(r"vfio[-_]pci\.ids=([0-9a-fA-F:,]+)", opts)
        if m:
            staged_cmdline_ids = m.group(1)
    elif os.geteuid() != 0:
        staged_cmdline_ids = "(run with sudo to check staged boot entries)"

    table = Table(title="Disable state", header_style="bold magenta")
    table.add_column("Component")
    table.add_column("State")
    table.add_row(str(MODPROBE_FILE), "[green]present[/green]" if modprobe else "[dim]absent[/dim]")
    table.add_row(str(UDEV_RULE), "[green]present (bus-hide)[/green]" if hide else "[dim]absent[/dim]")
    table.add_row(str(MKINITCPIO_DROPIN), "[green]present[/green]" if dropin else "[dim]absent[/dim]")
    table.add_row("running cmdline vfio-pci.ids",
                  f"[green]{running_ids.group(1)}[/green]" if running_ids else "[dim]absent[/dim]")
    table.add_row("staged cmdline vfio-pci.ids",
                  f"[green]{staged_cmdline_ids}[/green]" if staged_cmdline_ids else "[dim]absent[/dim]")
    table.add_row("state.json action",
                  f"[cyan]{action}[/cyan]" if state else "[dim]none[/dim]")
    table.add_row("managed boot entry", entry.ident if entry else "unknown")
    table.add_row("pending operation", str(pending or "none"))
    table.add_row("images rebuilt", str(state.get("rebuilt", "unknown")))
    table.add_row("state.json claimed IDs",
                  f"[green]{', '.join(staged_ids)}[/green]" if staged_ids else "[dim]none[/dim]")

    # Show PCIe Root Port power status for disabled slot
    target_slots = state.get("slots", [])
    for s in target_slots:
        bridge = find_upstream_bridge(s)
        bridge_addr = state.get("bridges", {}).get(s)
        if bridge is None and isinstance(bridge_addr, str):
            bridge = SYS_PCI / bridge_addr
        if bridge and bridge.exists():
            pst, rst = pci_bridge_power_state(bridge)
            style = "green" if pst == "D3cold" else "yellow"
            table.add_row(f"PCIe Root Port ({bridge.name})", f"[{style}]{pst} ({rst})[/{style}]")

    # Show ASUS hardware power gate if supported
    if asus_wmi_available():
        val = None
        for p in (ASUS_ARMOURY_DGPU_DISABLE, ASUS_DGPU_DISABLE):
            if p.exists():
                val = _read(p)
                break
        status_str = {"1": "[green]firmware disable requested[/green]",
                      "0": "[yellow]firmware disable cleared[/yellow]"}.get(val, "unknown")
        table.add_row("ASUS WMI dgpu_disable", status_str)

    console.print(table)
    if modprobe:
        console.print(Panel(MODPROBE_FILE.read_text(encoding="utf-8").strip(),
                            title=str(MODPROBE_FILE), border_style="dim"))
    if pending:
        console.print(f"[yellow]Operation '{pending}' is incomplete; rerun it before rebooting.[/yellow]")
    elif action == "disabled":
        visible = set(state.get("addrs", [])) & {d.addr for d in devices}
        if visible:
            console.print("[yellow]Claimed PCI functions remain visible. Reboot to apply the staged removal.[/yellow]")
        else:
            console.print("[green]Claimed PCI functions are absent from the current PCI bus.[/green]")
    elif action == "enabled" and running_ids:
        console.print("[yellow]The running kernel still has VFIO IDs. Reboot if the released GPU remains unavailable.[/yellow]")


def do_disable(args: argparse.Namespace) -> None:
    devices = enumerate_pci()
    slots = gpu_slots(devices)
    prior = state_load()
    from_state = False
    saved = claim_from_state(prior) if prior.get("action") == "disabled" else None
    if saved is not None:
        console.print(f"[dim]Reusing disabled claim: {', '.join(saved.slots)}[/dim]")
        if args.slot is not None and normalize_slot(args.slot, saved.slots) is None:
            bail("A different GPU claim is already staged. Use --enable before changing targets.")
        if "preserved_cmdline" not in prior:
            bail("Disabled recovery state lacks its original command-line snapshot. Use --enable first.")
        if any(d.slot in saved.slots and d.addr not in saved.addrs for d in devices):
            bail("PCI functions changed within the disabled slot. Use --enable before selecting it again.")
        live = {d.addr: d for d in devices}
        functions = []
        for dev in saved.functions:
            current = live.get(dev.addr)
            if current and current.ids != dev.ids:
                bail(f"Hardware changed at {dev.addr}. Use --enable before selecting a new GPU.")
            if current and current.driver == "vfio-pci":
                current = PciDevice(current.addr, current.vendor, current.device, current.klass,
                                    dev.driver, current.boot_vga, current.label)
            functions.append(current or dev)
        claim = Claim(functions)
        from_state = not any(d.addr in live for d in saved.functions)
    else:
        if any(p.exists() for p in (MODPROBE_FILE, UDEV_RULE, MKINITCPIO_DROPIN)):
            bail("Disable config exists without usable recovery state. Use --enable first.")
        claim = select_claim(devices, slots, args) if slots else Claim()

    if not claim.functions:
        bail(f"--slot {args.slot or '(auto)'} matches no visible GPU slot and no disabled state.\n"
             f"Visible GPU slots: {', '.join(slots) or 'none (all hidden?)'}")

    guard_claim(claim, devices, args.allow_boot_vga, from_state=from_state)
    check_id_collisions(devices, claim)
    warn_modprobe_conflicts()

    softdeps, blacklist = drm_blacklist_for(devices, claim)
    vendor = cpu_vendor()
    entry = resolve_boot_entry(prior if saved else None)
    images = preflight(entry, enable=False)

    # Snapshot user's pre-existing managed keys once. If already disabled, keep
    # the original baseline so re-running --disable never poisons preserved_cmdline!
    if prior.get("action") == "disabled" and isinstance(prior.get("preserved_cmdline"), dict):
        preserved = {k: v for k, v in prior["preserved_cmdline"].items()
                     if isinstance(k, str) and isinstance(v, str) and k in MANAGED_KEYS}
        console.print("[dim]Reusing pre-existing cmdline snapshot taken before first disable.[/dim]")
    else:
        preserved = managed_snapshot(read_target_options(entry))

    console.print(Panel(
        f"[bold red]Disabling {len(claim.functions)} function(s)[/bold red]\n"
        f"Slots: {', '.join(claim.slots)}\nAddrs: {', '.join(claim.addrs)}\n"
        f"IDs: {', '.join(claim.ids)}\n"
        f"Blacklist: {', '.join(sorted(blacklist)) or 'none'}\n"
        f"Boot target: {entry.ident} ({entry.kind})",
        expand=False, border_style="red"))
    if not args.yes and sys.stdin.isatty() and not DRY_RUN:
        if not Confirm.ask("Apply?", default=False):
            raise SystemExit(0)

    bridges = dict(prior.get("bridges", {})) if saved else {}
    for slot in claim.slots:
        if bridge := find_upstream_bridge(slot):
            bridges[slot] = bridge.name
    asus_gate = bool(prior.get("asus_power_gate")) or args.asus_power_gate
    asus_restore = prior.get("asus_restore", "0")
    if asus_gate and not prior.get("asus_power_gate"):
        path = next((p for p in (ASUS_ARMOURY_DGPU_DISABLE, ASUS_DGPU_DISABLE) if p.exists()), None)
        if path is None:
            bail("--asus-power-gate requested, but no supported ASUS firmware attribute exists.")
        asus_restore = path.read_text(encoding="utf-8").strip()
        if asus_restore not in {"0", "1"}:
            bail(f"Unexpected ASUS firmware value: {asus_restore!r}.")
    # Journal the original cmdline and hardware before the first config write.
    state_save(ids=claim.ids, addrs=claim.addrs, slots=claim.slots,
               functions=[{"addr": d.addr, "ids": d.ids, "klass4": d.klass4, "driver": d.driver}
                          for d in claim.functions],
               blacklist=sorted(blacklist), cpu_vendor=vendor, bridges=bridges,
               preserved_cmdline=preserved, boot_kind=entry.kind, boot_id=entry.ident,
               asus_power_gate=asus_gate, asus_restore=asus_restore,
               action="disabled", pending_action="disable", rebuilt=False)
    desired = desired_params(blacklist, claim.ids, vendor, args.amd_force_enable, preserved)
    write_modprobe(claim, softdeps, blacklist, ids=desired["vfio-pci.ids"].split(","))
    patch_bootloader(entry, blacklist, claim.ids, vendor, args.amd_force_enable,
                     enable=False, restore=preserved)
    configure_initramfs(enable=False)
    write_udev_hide(claim)
    if asus_gate:
        set_asus_dgpu_disable(True)
    rebuild_initramfs(no_rebuild=args.no_rebuild)
    if not args.no_rebuild and not DRY_RUN:
        verify_images(images, enable=False)
        verify_staged_entry(entry, desired)
        state_save(pending_action=None, rebuilt=True)
    if DRY_RUN:
        console.print("\n[magenta]Disable preview complete; nothing changed.[/magenta]")
    elif args.no_rebuild:
        console.print("\n[yellow]Disable config staged; rebuild images before rebooting.[/yellow]")
    else:
        console.print("\n[bold green]=== DISABLE STAGED — REBOOT TO APPLY ===[/bold green]")
    console.print("[dim]Verify after reboot: the IDs below must print NOTHING:[/dim]")
    console.print(f"[dim]  lspci -Dnn | grep -E '{'|'.join(claim.ids)}'[/dim]")


def do_enable(args: argparse.Namespace) -> None:
    state = state_load()
    ids = state.get("ids", [])
    restore = {k: v for k, v in state.get("preserved_cmdline", {}).items()
               if isinstance(k, str) and isinstance(v, str) and k in MANAGED_KEYS}
    console.print(Panel("[bold yellow]Re-enabling dedicated GPU(s)[/bold yellow]\n"
                        f"Releasing IDs: {', '.join(ids) or 'unknown (sweeping managed keys)'}\n"
                        f"Restoring cmdline: {restore or 'none (clean sweep)'}",
                        expand=False))
    if state.get("action") == "enabled" and not state.get("pending_action") and not any(
            p.exists() for p in (MODPROBE_FILE, UDEV_RULE, MKINITCPIO_DROPIN, ASUS_TMPFILES, CMDLINE_D_DROPIN)):
        console.print("[green]GPU disable configuration is already removed.[/green]")
        return
    if not state and not any(p.exists() for p in (MODPROBE_FILE, UDEV_RULE, MKINITCPIO_DROPIN, ASUS_TMPFILES, CMDLINE_D_DROPIN)):
        console.print("[green]No GPU disable configuration to remove.[/green]")
        return
    entry = resolve_boot_entry(state)
    images = preflight(entry, enable=True)
    state_save(pending_action="enable", rebuilt=False,
               asus_power_gate=bool(state.get("asus_power_gate")) or ASUS_TMPFILES.exists(),
               asus_restore=state.get("asus_restore", "0"))
    remove_modprobe()
    remove_udev_hide()
    if state.get("asus_power_gate") or ASUS_TMPFILES.exists():
        set_asus_dgpu_disable(False, restore=state.get("asus_restore", "0"))
    patch_bootloader(entry, set(), [], cpu_vendor(), args.amd_force_enable,
                     enable=True, restore=restore)
    # Stale cmdline.d drop-in sweep if active target is type1 or KERNEL_CMDLINE
    if (entry.kind == "type1" or KERNEL_CMDLINE.is_file()) and CMDLINE_D_DROPIN.exists():
        if DRY_RUN:
            console.print(f"[magenta]  (dry-run) would remove stale {CMDLINE_D_DROPIN}[/magenta]")
        else:
            remove_file(CMDLINE_D_DROPIN)
            console.print(f"[green]  ~[/green] removed stale {CMDLINE_D_DROPIN}")

    configure_initramfs(enable=True)
    rebuild_initramfs(no_rebuild=args.no_rebuild)
    if not args.no_rebuild and not DRY_RUN:
        verify_images(images, enable=True)
        verify_staged_entry(entry, restore)
        state_save(action="enabled", pending_action=None, rebuilt=True,
                   ids=[], addrs=[], slots=[], functions=[], bridges={}, blacklist=[],
                   preserved_cmdline={}, asus_power_gate=False)
    if DRY_RUN:
        console.print("\n[magenta]Enable preview complete; nothing changed.[/magenta]")
    elif args.no_rebuild:
        console.print("\n[yellow]Enable config staged; rebuild images before rebooting.[/yellow]")
    else:
        console.print("\n[bold green]=== RE-ENABLE STAGED — REBOOT TO APPLY ===[/bold green]")
    console.print("[dim]The GPU re-enumerates on the PCI bus after reboot; "
                  "its native driver loads again.[/dim]")


def main() -> None:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Stage PCI removal / re-enable dedicated GPU(s) for the next boot.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--disable", action="store_true", help="Disable the dedicated GPU slot(s).")
    g.add_argument("--enable", action="store_true", help="Remove the disable config (re-enable).")
    g.add_argument("--status", action="store_true", help="Show GPUs + disable state.")

    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--slot", help="PCI slot, e.g. 0000:01:00 (bare 01:00 also works).")
    sel.add_argument("--all", action="store_true", help="With --disable: all non-boot_vga GPU slots.")

    ap.add_argument("--auto", action="store_true", help="Non-interactive (pick single/dedicated).")
    ap.add_argument("--allow-boot-vga", action="store_true",
                    help="Permit disabling the boot_vga / last GPU (console goes dark).")
    ap.add_argument("--amd-force-enable", action="store_true",
                    help="On AMD CPUs with a broken IVRS, emit amd_iommu=force_enable.")
    ap.add_argument("--asus-power-gate", action="store_true",
                    help="With --disable: stage the ASUS notebook firmware dGPU power gate (all internal dGPUs).")
    ap.add_argument("--yes", "-y", action="store_true", help="Skip confirmation.")
    ap.add_argument("--dry-run", action="store_true", help="Print changes, write nothing.")
    ap.add_argument("--no-rebuild", action="store_true", help="Skip initramfs regeneration.")
    args = ap.parse_args()

    # CLI semantic validation
    if args.status and any((args.slot is not None, args.all, args.auto, args.allow_boot_vga, args.amd_force_enable, args.asus_power_gate, args.yes, args.dry_run, args.no_rebuild)):
        ap.error("--status takes no other options.")
    if args.enable and any((args.slot is not None, args.all, args.auto, args.allow_boot_vga, args.asus_power_gate)):
        ap.error("--slot/--all/--auto/--allow-boot-vga/--asus-power-gate only apply to --disable.")

    DRY_RUN = args.dry_run
    check_versions()

    if args.disable or args.enable:
        elevate()

    console.print(Panel.fit(
        "[bold cyan]GPU Disable Toggle[/bold cyan]  "
        f"kernel {_read(Path('/proc/sys/kernel/osrelease'))}  "
        f"python {'.'.join(map(str, sys.version_info[:3]))}",
        border_style="cyan"))
    if DRY_RUN:
        console.print("[magenta]DRY RUN: no file will be modified.[/magenta]")

    if args.status:
        do_status()
        return
    with Path(__file__).open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            bail("Another GPU toggle operation is running.")
        if args.disable:
            do_disable(args)
        else:
            do_enable(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        bail(f"Operation failed: {exc}. Recovery state is retained if staging began.")
    except KeyboardInterrupt:
        console.print("\n[bold red]! Interrupted.[/bold red]")
        raise SystemExit(130) from None
