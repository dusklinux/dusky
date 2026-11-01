#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# d: Generate the Hyprland GPU config (Arch Linux | Kernel 7.3+ | Hyprland 0.55+ | Python 3.15+)

# ── 1. Rich bootstrap ──
import os
import shutil
import subprocess
import sys

try:
    import rich  # noqa: F401
except ImportError:
    pm = shutil.which("pacman")
    if not pm:
        print("pacman not found; install python-rich manually.", file=sys.stderr)
        sys.exit(1)
    cmd = [pm, "-S", "--needed", "--noconfirm", "python-rich"]
    if os.geteuid() != 0 and shutil.which("sudo"):
        sudo_flags = ["sudo", "-v"] if sys.stdin.isatty() else ["sudo", "-n", "-v"]
        subprocess.run(sudo_flags, check=False)
        cmd = ["sudo", *cmd]
    print(f"[BOOTSTRAP] Installing python-rich: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    os.execv(sys.executable, [sys.executable, *sys.argv])

import argparse
import importlib
import pwd
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

console = Console()

pyudev: ModuleType | None = None
try:
    import pyudev as _pyudev
    pyudev = _pyudev
except ImportError:
    for p in sorted(Path("/usr/lib").glob("python3.*/site-packages"), reverse=True):
        if (p / "pyudev").is_dir() and str(p) not in sys.path:
            sys.path.append(str(p))
            try:
                import pyudev as _pyudev
                pyudev = _pyudev
                break
            except ImportError:
                pass


# ── 2. Privilege, XDG & package helpers ──
def real_user_info() -> tuple[int, int, Path]:
    """Return (uid, gid, home_dir) of the invoking user, even under sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if os.geteuid() == 0 and sudo_user:
        try:
            pw = pwd.getpwnam(sudo_user)
            return pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except KeyError:
            return 1000, 1000, Path(f"/home/{sudo_user}")
    return os.getuid(), os.getgid(), Path.home()


def real_home() -> Path:
    return real_user_info()[2]


def xdg_config_home() -> Path:
    """Resolve XDG_CONFIG_HOME safely without leaking root's env under sudo."""
    home = real_home()
    env_xdg = os.environ.get("XDG_CONFIG_HOME")
    if env_xdg:
        candidate = Path(env_xdg)
        if os.geteuid() != 0 or candidate.is_relative_to(home):
            return candidate
    return home / ".config"


OUT_DEFAULT = xdg_config_home() / "hypr" / "gpu.lua"
DRI_DIRS = (Path("/usr/lib/dri"), Path("/usr/lib64/dri"))
GBM_DIRS = (Path("/usr/lib/gbm"), Path("/usr/lib64/gbm"))
PCI_IDS_PATHS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"))

FALLBACK_DRIVERS = frozenset({"simpledrm", "vesadrm", "ofdrm", "efifb", "simple-framebuffer"})
BMC_DRIVERS = frozenset({"ast", "mgag200"})
PCI_BDF_RE = re.compile(r"^([0-9a-fA-F]{4,}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])$")
INTERNAL_PANEL_RE = re.compile(r"^card\d+-(?:eDP|LVDS|DSI)-", re.IGNORECASE)


def ensure_sudo() -> bool:
    if os.geteuid() == 0:
        return True
    if not shutil.which("sudo"):
        return False
    sudo_cmd = ["sudo", "-v"] if sys.stdin.isatty() else ["sudo", "-n", "-v"]
    try:
        subprocess.run(sudo_cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def pacman_install(pkgs: list[str]) -> bool:
    if not pkgs:
        return True
    pm = shutil.which("pacman")
    if not pm:
        console.print("[red]pacman not found[/]")
        return False
    if os.geteuid() != 0:
        if not ensure_sudo():
            return False
        cmd = ["sudo", pm, "-S", "--needed", "--noconfirm", *pkgs]
    else:
        cmd = [pm, "-S", "--needed", "--noconfirm", *pkgs]
    console.print(f"[yellow]Installing {', '.join(pkgs)}...[/]")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]pacman failed: {e}[/]")
        return False


def ensure_bin(bin_name: str, pkg: str) -> bool:
    if shutil.which(bin_name):
        return True
    return pacman_install([pkg])


def ensure_pyudev() -> bool:
    """Ensure pyudev is installed and bound to the global `pyudev` reference."""
    global pyudev
    if pyudev is not None:
        return True
    try:
        pyudev = importlib.import_module("pyudev")
        return True
    except ImportError:
        for p in sorted(Path("/usr/lib").glob("python3.*/site-packages"), reverse=True):
            if (p / "pyudev").is_dir() and str(p) not in sys.path:
                sys.path.append(str(p))
                try:
                    pyudev = importlib.import_module("pyudev")
                    return True
                except ImportError:
                    pass
        if pacman_install(["python-pyudev"]):
            importlib.invalidate_caches()
            try:
                pyudev = importlib.import_module("pyudev")
                return True
            except ImportError:
                return False
        return False


def fix_ownership(p: Path) -> None:
    """Restore invoking user's ownership when executed via sudo."""
    if os.geteuid() == 0 and "SUDO_USER" in os.environ:
        uid, gid, _ = real_user_info()
        try:
            os.chown(p, uid, gid)
        except OSError:
            pass


def mkdir_user_owned(directory: Path) -> None:
    """Create directory hierarchy and fix ownership on any newly created parents."""
    missing: list[Path] = []
    cur = directory.resolve()
    while not cur.exists() and cur != cur.parent:
        missing.append(cur)
        cur = cur.parent
    directory.mkdir(parents=True, exist_ok=True)
    for d in reversed(missing):
        fix_ownership(d)


# ── 3. Data model ──
VENDOR_MAP = frozendict({
    "0x8086": "Intel",
    "0x1002": "AMD",
    "0x10de": "NVIDIA",
    "0x1af4": "RedHat VirtIO",
    "0x15ad": "VMware",
    "0x80ee": "VirtualBox",
    "0x1234": "QEMU Bochs",
    "0x1414": "Hyper-V",
    "0x1b36": "RedHat QXL",
    "0x1013": "Cirrus",
    "0x1a03": "ASPEED",
    "0x102b": "Matrox",
    "0x106b": "Apple",
    "0x5143": "Qualcomm",
})


@dataclass(slots=True)
class Gpu:
    dev_node: str
    pci: str
    vendor_id: str
    vendor_label: str
    name: str
    boot_vga: int
    driver: str
    by_path: str
    is_real: bool
    is_bmc: bool = False
    total_outputs: int = 1
    connected_outputs: int = 0
    has_internal_panel: bool = False


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def vendor_label(v: str) -> str:
    return VENDOR_MAP.get(v.lower(), f"Vendor {v}")


def _card_num(dev_node: str) -> int:
    m = re.search(r"card(\d+)$", dev_node)
    return int(m.group(1)) if m else 999


def pci_sort_key(gpu: Gpu) -> tuple[int, int, int, int, int, str]:
    """Deterministic numeric sort key for PCI BDF addresses and platform nodes."""
    m = PCI_BDF_RE.prefixmatch(gpu.pci)
    if m:
        return (
            int(m.group(1), 16),
            int(m.group(2), 16),
            int(m.group(3), 16),
            int(m.group(4), 16),
            _card_num(gpu.dev_node),
            gpu.dev_node,
        )
    return (0xFFFF_FFFF, 0xFF, 0xFF, 0xFF, _card_num(gpu.dev_node), gpu.pci)


def _lookup_pci_ids(vendor_hex: str, device_hex: str) -> str | None:
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
            with open(ids_path, encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    if not raw_line or raw_line.startswith("#") or raw_line.startswith("\t\t"):
                        continue
                    if not raw_line.startswith("\t"):
                        if in_vendor:
                            break
                        if raw_line[:4].lower() == v_clean:
                            in_vendor = True
                    elif in_vendor:
                        stripped = raw_line[1:]
                        if stripped[:4].lower() == d_clean:
                            return stripped[4:].strip()
        except OSError:
            pass
    return None


def pci_name(pci: str, sys_dev: Path | None = None) -> str:
    if PCI_BDF_RE.prefixmatch(pci) and shutil.which("lspci"):
        out = sh(["lspci", "-D", "-s", pci])
        if out:
            first_line = out.splitlines()[0]
            m = re.prefixmatch(r"^[0-9a-fA-F:.]+ [^:]+: (.+)$", first_line)
            return m.group(1) if m else first_line

    # Fallback 1: sysfs PCI label or hwdata pci.ids lookup
    pci_sys = Path(f"/sys/bus/pci/devices/{pci}")
    label_path = pci_sys / "label"
    if label_path.exists():
        try:
            text = label_path.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass

    if (pci_sys / "vendor").exists() and (pci_sys / "device").exists():
        try:
            v_hex = (pci_sys / "vendor").read_text(encoding="utf-8").strip()
            d_hex = (pci_sys / "device").read_text(encoding="utf-8").strip()
            hw_name = _lookup_pci_ids(v_hex, d_hex)
            if hw_name:
                return hw_name
        except OSError:
            pass

    # Fallback 2: DeviceTree (of_node) or uevent for ARM/SoC/Platform GPUs
    if sys_dev is not None:
        for dt_file in (sys_dev / "of_node" / "model", sys_dev / "of_node" / "compatible"):
            if dt_file.exists():
                try:
                    raw_dt = dt_file.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
                    if raw_dt:
                        return raw_dt.split()[0]
                except OSError:
                    pass
        uevent_file = sys_dev / "uevent"
        if uevent_file.exists():
            try:
                for line in uevent_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith(("OF_COMPATIBLE_0=", "OF_NAME=", "MODALIAS=")):
                        return line.split("=", 1)[1].strip()
            except OSError:
                pass

    return "Unknown PCI Device"


def find_pci_or_vendor_dir(start: Path) -> tuple[Path | None, bool]:
    """Walk up sysfs to find canonical PCI device directory (subsystem == pci or BDF)."""
    cur = start.resolve()
    fallback_vendor: Path | None = None
    for _ in range(12):
        subsys = cur / "subsystem"
        is_pci_subsys = False
        if subsys.exists():
            try:
                is_pci_subsys = subsys.resolve().name == "pci"
            except OSError:
                is_pci_subsys = False
        if (is_pci_subsys or PCI_BDF_RE.prefixmatch(cur.name)) and (cur / "vendor").exists():
            return cur, True
        if fallback_vendor is None and (cur / "vendor").exists():
            fallback_vendor = cur
        if cur == cur.parent:
            break
        cur = cur.parent
    return fallback_vendor, False


def probe_connectors(card_name: str) -> tuple[int, int, bool]:
    """Return `(total_outputs, connected_outputs, has_internal_panel)` for `/sys/class/drm/cardN-*`."""
    total = 0
    connected = 0
    has_internal = False
    drm_root = Path("/sys/class/drm")
    try:
        for conn in drm_root.glob(f"{card_name}-*"):
            if conn.name.startswith(f"{card_name}-Writeback"):
                continue
            total += 1
            if INTERNAL_PANEL_RE.prefixmatch(conn.name):
                has_internal = True
            status_file = conn / "status"
            if status_file.exists():
                try:
                    if status_file.read_text(encoding="utf-8").strip().lower() == "connected":
                        connected += 1
                except OSError:
                    pass
    except OSError:
        return 1, 0, False
    return total, connected, has_internal


# ── 4. Topology discovery ──
def detect() -> list[Gpu]:
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as prog:
        prog.add_task("Scanning /sys/class/drm/card* + udev", total=None)
        raw: list[Gpu] = []
        drm_root = Path("/sys/class/drm")
        for p in sorted(drm_root.glob("card[0-9]*"), key=lambda x: _card_num(x.name)):
            if not re.fullmatch(r"card\d+", p.name):
                continue
            dev = f"/dev/dri/{p.name}"
            if not Path(dev).exists():
                continue
            try:
                sys_dev = (p / "device").resolve(strict=True)
            except OSError:
                continue

            total_out, conn_out, has_internal = probe_connectors(p.name)
            vdir, is_pci = find_pci_or_vendor_dir(sys_dev)

            drv = "unknown"
            for d in ([sys_dev / "driver", vdir / "driver"] if vdir else [sys_dev / "driver"]):
                if d.exists():
                    try:
                        drv = d.resolve().name
                        break
                    except OSError:
                        pass

            if not vdir or not is_pci:
                vid = "0x0000"
                if vdir and (vdir / "vendor").exists():
                    try:
                        vid = (vdir / "vendor").read_text(encoding="utf-8").strip().lower()
                    except OSError:
                        vid = "0x0000"
                vlabel = vendor_label(vid) if vid != "0x0000" else "Platform"
                plat_id = f"platform:{sys_dev.name}"
                by = "unavailable"
                by_path_dir = Path("/dev/dri/by-path")
                if by_path_dir.exists():
                    for cand in sorted(by_path_dir.glob(f"*{sys_dev.name}*-card")):
                        if cand.exists():
                            by = str(cand)
                            break
                is_real = drv not in FALLBACK_DRIVERS
                is_bmc = drv in BMC_DRIVERS
                raw.append(
                    Gpu(
                        dev_node=dev,
                        pci=plat_id,
                        vendor_id=vid,
                        vendor_label=vlabel,
                        name=pci_name(plat_id, sys_dev),
                        boot_vga=0,
                        driver=drv,
                        by_path=by,
                        is_real=is_real,
                        is_bmc=is_bmc,
                        total_outputs=total_out,
                        connected_outputs=conn_out,
                        has_internal_panel=has_internal,
                    )
                )
                continue

            try:
                vid = (vdir / "vendor").read_text(encoding="utf-8").strip().lower()
            except OSError:
                continue

            pci = vdir.name
            boot = 0
            for bp in (vdir / "boot_vga", sys_dev / "boot_vga"):
                if bp.exists():
                    try:
                        boot = int(bp.read_text(encoding="utf-8").strip())
                    except (OSError, ValueError):
                        boot = 0
                    break

            by = "unavailable"
            by_path_dir = Path("/dev/dri/by-path")
            if by_path_dir.exists():
                for pat in (f"pci-{pci}-card", f"pci-{pci}*card"):
                    matches = [l for l in sorted(by_path_dir.glob(pat)) if l.exists()]
                    if matches:
                        by = str(matches[0])
                        break

            is_real = drv not in FALLBACK_DRIVERS
            is_bmc = drv in BMC_DRIVERS
            raw.append(
                Gpu(
                    dev_node=dev,
                    pci=pci,
                    vendor_id=vid,
                    vendor_label=vendor_label(vid),
                    name=pci_name(pci, sys_dev),
                    boot_vga=boot,
                    driver=drv,
                    by_path=by,
                    is_real=is_real,
                    is_bmc=is_bmc,
                    total_outputs=total_out,
                    connected_outputs=conn_out,
                    has_internal_panel=has_internal,
                )
            )

    if not raw:
        console.print("[red]No DRM nodes - check KMS or run after first boot[/]")
        raise SystemExit(1)

    # pyudev enrichment for ID_PATH -> canonical /dev/dri/by-path/<ID_PATH>-card
    if pyudev is not None:
        try:
            ctx = pyudev.Context()
            for d in ctx.list_devices(subsystem="drm", DEVTYPE="drm_minor"):
                if not d.sys_name.startswith("card"):
                    continue
                node = d.device_node
                idp = d.get("ID_PATH")
                if idp and node:
                    cand = Path(f"/dev/dri/by-path/{idp}-card")
                    if cand.exists():
                        for c in raw:
                            if c.dev_node == node:
                                c.by_path = str(cand)
        except Exception:
            pass

    real = [c for c in raw if c.is_real]
    return sorted(real if real else raw, key=pci_sort_key)


def default_gpu(cards: list[Gpu]) -> tuple[Gpu, str]:
    """Select the optimal primary GPU across desktop, hybrid laptop, and server topologies."""
    non_bmc = [c for c in cards if not c.is_bmc]
    pool = non_bmc if non_bmc else cards

    connected = [c for c in pool if c.connected_outputs > 0]
    if len(connected) == 1:
        c = connected[0]
        return c, f"active display ({c.connected_outputs} connected)"
    if len(connected) > 1:
        internal = [c for c in connected if c.has_internal_panel]
        if len(internal) == 1:
            return internal[0], "internal panel (eDP/DSI)"
        conn_boots = [c for c in connected if c.boot_vga == 1]
        if len(conn_boots) == 1:
            return conn_boots[0], "active display + boot_vga"
        if len(conn_boots) > 1:
            return sorted(conn_boots, key=pci_sort_key)[0], "active display + boot_vga (lowest PCI)"
        return sorted(connected, key=pci_sort_key)[0], "active display (lowest PCI)"

    boots = [c for c in pool if c.boot_vga == 1]
    if not boots:
        return sorted(pool, key=pci_sort_key)[0], "No boot_vga, lowest PCI"
    if len(boots) == 1:
        return boots[0], "boot_vga"
    return sorted(boots, key=pci_sort_key)[0], "Multiple boot_vga, lowest PCI"


def select_gpu(cards: list[Gpu], auto: bool) -> tuple[Gpu, str]:
    def_card, reason = default_gpu(cards)
    tbl = Table(
        title=f"GPU Topology - default {def_card.dev_node} ({reason})",
        show_header=True,
        header_style="bold magenta",
    )
    for h in ("#", "Node", "Vendor", "Name", "PCI", "Driver", "Outputs", "Flags"):
        tbl.add_column(h)
    for i, c in enumerate(cards, 1):
        flags: list[str] = []
        if c.connected_outputs > 0:
            flags.append("[cyan]connected[/]")
        if c.has_internal_panel:
            flags.append("[blue]internal[/]")
        if c.boot_vga:
            flags.append("[yellow]boot_vga[/]")
        if c.dev_node == def_card.dev_node:
            flags.append("[green]default[/]")
        if not c.is_real:
            flags.append("[dim]simpledrm/virt[/]")
        if c.is_bmc:
            flags.append("[dim]bmc[/]")
        out_str = f"{c.connected_outputs}/{c.total_outputs}"
        tbl.add_row(
            str(i),
            c.dev_node,
            c.vendor_label,
            c.name[:50],
            c.pci,
            c.driver,
            out_str,
            " ".join(flags),
        )
    console.print(tbl)
    if len(cards) == 1 or auto or not os.isatty(0):
        return def_card, "auto" if auto else "single"
    idx = str(cards.index(def_card) + 1)
    ans = Prompt.ask("Primary GPU", choices=[str(i) for i in range(1, len(cards) + 1)], default=idx)
    try:
        return cards[int(ans) - 1], "manual"
    except (ValueError, IndexError):
        return def_card, "manual"


# ── 5. VA-API & driver probe ──
def probe_drivers() -> dict[str, bool]:
    extra_dirs = [
        Path(p) for p in os.environ.get("LIBVA_DRIVERS_PATH", "").split(":") if p.strip()
    ]
    search_dirs = (*extra_dirs, *DRI_DIRS)
    found: dict[str, bool] = {}
    for k in ("nvidia", "nouveau", "iHD", "i965", "radeonsi", "r600", "virtio_gpu"):
        name = f"{k}_drv_video.so"
        found[k] = any((d / name).exists() for d in search_dirs)
    return found


def nvidia_target(primary: Gpu) -> str:
    """Resolve active NVIDIA driver stack (proprietary/open `nvidia` vs `nouveau`/`nova`)."""
    if primary.vendor_id.lower() != "0x10de":
        return primary.driver.lower()
    drv = primary.driver.lower()
    if drv in ("nvidia", "nvidia_drm"):
        return "nvidia"
    if drv in ("nouveau", "nova", "nova_drm", "nova_core"):
        return "nouveau"
    if any((d / "nvidia-drm_gbm.so").exists() for d in GBM_DIRS):
        return "nvidia"
    return drv


# ── 6. Lua generation ──
def _lua_quote(val: str) -> str:
    escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def gen_lua(primary: Gpu, ordered: list[Gpu], mode: str) -> str:
    pr = probe_drivers()
    L: list[str] = []
    L.append("-- -----------------------------------------------------------------")
    L.append(f"-- Hyprland GPU | Mode: {mode.upper()} | Primary: {primary.dev_node}")
    L.append(f"-- GPU: {primary.vendor_label} | {primary.name} | {primary.pci} | drv:{primary.driver}")
    L.append("-- Gen: Python 3.15+ + Rich + pyudev | Kernel 7.3+ | Hyprland 0.55+ Lua")
    L.append("-- DRM nodes shift per reboot - resolved via stable by-path")
    L.append("-- -----------------------------------------------------------------")
    L.append("local function resolve_card(pci_address, fallback)")
    L.append(
        '  local cmd = "for d in /dev/dri/by-path/pci-"..pci_address.."-card '
        '/dev/dri/by-path/pci-"..pci_address.."*card '
        '/dev/dri/by-path/*"..pci_address.."*card; '
        'do [ -e \\"$d\\" ] && readlink -f \\"$d\\" 2>/dev/null && break; done"'
    )
    L.append("  local h = io.popen(cmd)")
    L.append("  if h then")
    L.append('    local path = h:read("*l")')
    L.append("    h:close()")
    L.append('    if path and path ~= "" then return path end')
    L.append("  end")
    L.append("  return fallback")
    L.append("end")
    L.append("")

    parts: list[str] = []
    for c in ordered:
        if not c.pci.startswith("platform:"):
            parts.append(f"resolve_card({_lua_quote(c.pci)}, {_lua_quote(c.dev_node)})")
        elif c.by_path != "unavailable" and Path(c.by_path).name.endswith("-card"):
            token = Path(c.by_path).name.removesuffix("-card")
            parts.append(f"resolve_card({_lua_quote(token)}, {_lua_quote(c.dev_node)})")
        else:
            parts.append(_lua_quote(c.dev_node))

    lua_concat_sep = ' .. ":" .. '
    L.append(f'hl.env("AQ_DRM_DEVICES", {lua_concat_sep.join(parts)})')
    L.append("")

    vid = primary.vendor_id.lower()
    drv = primary.driver.lower()
    vlabel = primary.vendor_label.lower()

    match vid:
        case "0x8086" | _ if "intel" in vlabel or drv in ("i915", "xe"):
            L.append("-- Intel (libva automatically maps i915/xe to iHD per DRM FD)")
        case "0x1002" | _ if "amd" in vlabel or drv == "amdgpu" or "radeon" in primary.name.lower() or "amd" in primary.name.lower():
            L.append("-- AMD (libva automatically maps amdgpu to radeonsi per DRM FD)")
        case "0x10de":
            tgt = nvidia_target(primary)
            if tgt == "nvidia":
                L.append("-- NVIDIA Proprietary")
                L.append('hl.env("GBM_BACKEND", "nvidia-drm")')
                L.append('hl.env("__GLX_VENDOR_LIBRARY_NAME", "nvidia")')
                # Note: Do not export LIBVA_DRIVER_NAME="nvidia" globally!
                # libva 2.24+ maps nvidia-drm -> nvidia per DRM FD; global export poisons secondary iGPUs.
                if pr["nvidia"]:
                    L.append('hl.env("NVD_BACKEND", "direct")')
            elif tgt == "nouveau":
                L.append("-- NVIDIA Nouveau" if drv == "nouveau" else f"-- NVIDIA Open-Source ({drv})")
                if drv == "nouveau":
                    L.append('hl.env("MESA_LOADER_DRIVER_OVERRIDE", "nouveau")')
            else:
                L.append(f"-- NVIDIA unknown driver {drv}")
        case _:
            L.append(f"-- Generic/VM ({primary.vendor_label}) - by-path only")
    L.append("")
    return "\n".join(L)


def atomic_write(p: Path, data: str) -> None:
    mkdir_user_owned(p.parent)
    if p.exists():
        try:
            if p.read_text(encoding="utf-8") == data:
                console.print(f"[green][OK] Up to date: {p}[/]")
                return
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".gpu.")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o644)
        tmp_path.replace(p)
        fix_ownership(p)
        console.print(f"[green][OK] Atomically written {p}[/]")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ── 7. Robustness guards ──
REQUIRE_GPU_RE = re.compile(
    r'''require\s*\(\s*["']gpu["']\s*\)|require\s+["']gpu["']|pcall\s*\(\s*require\s*,\s*["']gpu["']'''
)


def ensure_privilege(out: Path) -> None:
    """Fail gracefully (clean message, no traceback) when target is not writable."""
    if os.geteuid() == 0:
        return
    try:
        mkdir_user_owned(out.parent)
        ok = os.access(out.parent, os.W_OK) and (not out.exists() or os.access(out, os.W_OK))
    except OSError:
        ok = False
    if not ok:
        console.print(
            Panel.fit(
                f"[bold red]Permission denied[/] - cannot write to [cyan]{out}[/]\n"
                "Choose a writable --output path (e.g. under your home dir) and re-run.",
                style="bold red",
            )
        )
        raise SystemExit(1)


def hyprland_version() -> str | None:
    for binp, arg in ((shutil.which("hyprctl"), "version"), (shutil.which("hyprland"), "--version")):
        if not binp:
            continue
        try:
            out = subprocess.run([binp, arg], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"Hyprland\s+v?(\d+)\.(\d+)", out or "", re.IGNORECASE)
            if m:
                return f"{m.group(1)}.{m.group(2)}"
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def check_hyprland_version() -> None:
    ver = hyprland_version()
    if ver is None:
        console.print("[dim][INFO] Hyprland not detectable. gpu.lua requires Hyprland >= 0.55 (Lua configs).[/]")
        return
    major, minor = (int(x) for x in ver.split("."))
    if (major, minor) < (0, 55):
        console.print(
            Panel.fit(
                f"[bold red]Unsupported Hyprland {ver}[/] - Lua configs (hl.env) were introduced in 0.55.\n"
                "The generated gpu.lua will NOT be loaded. Upgrade to Hyprland >= 0.55.",
                style="bold red",
            )
        )
    else:
        console.print(f"[green][OK] Hyprland {ver} detected - Lua configs supported.[/]")


def nvidia_modeset_confirmed(cards: list[Gpu] | None = None) -> bool:
    """True if nvidia_drm.modeset=1 is active. Returns False immediately if explicitly 0/n."""
    p = Path("/sys/module/nvidia_drm/parameters/modeset")
    if p.exists():
        try:
            val = p.read_text(encoding="utf-8").strip().lower()
            if val in ("y", "1"):
                return True
            if val in ("n", "0"):
                return False
        except PermissionError:
            try:
                res = subprocess.run(["sudo", "-n", "cat", str(p)], capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    sval = res.stdout.strip().lower()
                    if sval in ("y", "1"):
                        return True
                    if sval in ("n", "0"):
                        return False
            except (OSError, subprocess.SubprocessError):
                pass
        except OSError:
            pass

    try:
        cl = Path("/proc/cmdline").read_text(encoding="utf-8")
        if re.search(r"\bnvidia[-_]drm\.modeset=0\b", cl):
            return False
        if re.search(r"\bnvidia[-_]drm\.modeset=1\b", cl):
            return True
    except OSError:
        pass

    for mp_dir in (Path("/etc/modprobe.d"), Path("/usr/lib/modprobe.d")):
        if not mp_dir.exists():
            continue
        try:
            for conf in sorted(mp_dir.glob("*.conf")):
                try:
                    content = conf.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in content.splitlines():
                    code = line.split("#", 1)[0].strip()
                    if re.search(r"^options\s+nvidia[-_]drm\b.*\bmodeset=0\b", code):
                        return False
                    if re.search(r"^options\s+nvidia[-_]drm\b.*\bmodeset=1\b", code):
                        return True
        except OSError:
            pass

    try:
        ver_p = Path("/sys/module/nvidia/version")
        if ver_p.exists():
            ver_str = ver_p.read_text(encoding="utf-8").strip()
            m = re.prefixmatch(r"^(\d+)", ver_str)
            if m and int(m.group(1)) >= 560:
                return True
    except OSError:
        pass

    if cards:
        for c in cards:
            if (
                c.vendor_id.lower() == "0x10de"
                and c.driver.lower() == "nvidia"
                and c.dev_node.startswith("/dev/dri/card")
                and c.total_outputs > 0
            ):
                return True

    return False


def wire_gpu_require(out: Path) -> None:
    """Verify (and idempotently auto-wire) pcall(require, "gpu") in sibling hyprland.lua."""
    hl = out.parent / "hyprland.lua"
    if not hl.exists():
        console.print('[yellow][WARN] No hyprland.lua beside gpu.lua - ensure it contains: pcall(require, "gpu")[/]')
        return
    try:
        text = hl.read_text(encoding="utf-8")
    except OSError as e:
        console.print(f'[yellow][WARN] Cannot read {hl}: {e}. Ensure it contains: pcall(require, "gpu")[/]')
        return

    for line in text.splitlines():
        active_code = line.split("--", 1)[0]
        if REQUIRE_GPU_RE.search(active_code):
            console.print(f"[green][OK] {hl} already loads gpu.lua[/]")
            return

    looks_lua = ("require(" in text) or ("hl." in text) or text.lstrip().startswith("--") or text.strip() == ""
    if not looks_lua:
        console.print(
            '[yellow][WARN] hyprland.lua does not look like a Lua config (Hyprland < 0.55?). '
            'Skipping auto-wire - add manually: pcall(require, "gpu")[/]'
        )
        return
    try:
        prefix = "\n" if text and not text.endswith("\n") else ""
        with open(hl, "a", encoding="utf-8") as f:
            f.write(f'{prefix}-- Auto-added by 383_configure_hyprland_gpu.py\npcall(require, "gpu")\n')
        fix_ownership(hl)
        console.print(f"[green][OK] Auto-wired: added 'pcall(require, \"gpu\")' to {hl}[/]")
    except OSError as e:
        console.print(f'[yellow][WARN] Could not auto-wire {hl}: {e}. Add manually: pcall(require, "gpu")[/]')


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate ~/.config/hypr/gpu.lua")
    ap.add_argument("--auto", action="store_true", help="Non-interactive")
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()

    ensure_bin("lspci", "pciutils")
    ensure_pyudev()

    out = args.output or OUT_DEFAULT
    kernel_rel = os.uname().release
    arch_label = "Arch Linux" if Path("/etc/arch-release").exists() else "Linux"
    console.print(
        Panel.fit(
            f"Hyprland GPU Configurator\n"
            f"Python {'.'.join(map(str, sys.version_info[:3]))} | {arch_label} ({kernel_rel}) | Hyprland 0.55+ | systemd 262+",
            style="bold cyan",
        )
    )

    ensure_privilege(out)
    check_hyprland_version()

    cards = detect()
    # Filter to display-capable GPUs for AQ_DRM_DEVICES (0-connector compute cards excluded)
    display_cards = [c for c in cards if c.total_outputs > 0]
    if not display_cards:
        console.print("[yellow][WARN] No display-capable GPUs detected — falling back to all cards[/]")
        display_cards = cards

    # Exclude unaccelerated server BMC chips (ast, mgag200) from AQ_DRM_DEVICES when real 3D display GPUs exist
    non_bmc_display = [c for c in display_cards if not c.is_bmc]
    bmc_excluded = [c for c in display_cards if c.is_bmc] if non_bmc_display else []
    if non_bmc_display:
        display_cards = non_bmc_display

    primary, mode = select_gpu(display_cards, args.auto)
    secondary = sorted(
        [c for c in display_cards if c.dev_node != primary.dev_node],
        key=lambda c: (-c.connected_outputs, -int(c.has_internal_panel), -c.boot_vga, pci_sort_key(c)),
    )
    ordered = [primary, *secondary]

    compute_only = [c for c in cards if c not in display_cards and c not in bmc_excluded]
    for c in compute_only:
        console.print(
            f"[dim]Compute-only GPU excluded from AQ_DRM_DEVICES (still via DRI_PRIME/render): "
            f"{c.dev_node} {c.vendor_label} {c.name} {c.pci}[/]"
        )
    for c in bmc_excluded:
        console.print(
            f"[dim]Server BMC GPU excluded from AQ_DRM_DEVICES: "
            f"{c.dev_node} {c.vendor_label} {c.name} {c.pci}[/]"
        )

    lua = gen_lua(primary, ordered, mode)
    atomic_write(out, lua)

    nv_primary = primary.vendor_id.lower() == "0x10de" and nvidia_target(primary) == "nvidia"
    nv_any = any(c.vendor_id.lower() == "0x10de" and nvidia_target(c) == "nvidia" for c in cards)
    if nv_any and not nvidia_modeset_confirmed(cards):
        if nv_primary:
            console.print(
                Panel.fit(
                    "[bold red]WARNING: nvidia_drm.modeset=1 not confirmed[/]\n"
                    "GBM_BACKEND=nvidia-drm requires kernel modesetting on the NVIDIA driver,\n"
                    "otherwise Hyprland may fail to start (black screen). Add to kernel params:\n"
                    "  [green]nvidia_drm.modeset=1[/]   (verify: cat /sys/module/nvidia_drm/parameters/modeset)",
                    style="bold red",
                )
            )
        else:
            console.print(
                "[yellow][NOTE] NVIDIA GPU present (offload). If you plan to use it (DRI_PRIME/offload apps),\n"
                "        add [green]nvidia_drm.modeset=1[/] to kernel params - PRIME offload requires it.[/]"
            )

    console.print("\n[bold]Preview:[/]")
    for line in lua.splitlines():
        if any(k in line for k in ("AQ_DRM", "LIBVA", "GBM_", "__GLX", "MESA_", "NVD_")):
            console.print(f" {line}")

    wire_gpu_require(out)
    console.print(f"File: [cyan]{out}[/] -> [bold]hyprctl reload[/] or relogin")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[red]Aborted[/]")
        raise SystemExit(130)
