#!/usr/bin/env python3
"""
Arch Linux Universal Gaming Architecture Installer.
Engineered for Bleeding-Edge Arch Linux, Pure Wayland, Hyprland, and Linux Kernel 7.x+.

Design Principles:
1. Declarative & Easily Configurable: All package lists, GPU drivers, Flatpaks, and tweaks
   are defined in clean catalogs at the top of the file for instant customization.
2. Intelligent Hardware Auto-Detection: Automatically resolves GPU matrices (AMD, Intel, NVIDIA, Hybrid),
   boot display adapter (boot_vga), virtualized GPUs (VirtIO/QEMU),
   and CPU microarchitecture tiers (x86-64-v3 / v4).
3. Flexible Packaging: Instant pre-compiled binaries (-bin) OR Native CPU Build (-march=native -O3).
4. Wayland Pipeline: Native Wayland sandbox sockets, Gamescope, and NVIDIA DRM modesetting checks.
5. Performance: Optional kernel sysctl tuning with a practical vm.max_map_count floor.
6. Desktop Integration: Native desktop notifications (Wayland DBus session aware) & instant launcher icon bridging.
"""

import argparse
import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ==============================================================================
# 1. DECLARATIVE CONFIGURATION CATALOGS (Easily add / remove packages here)
# ==============================================================================

# Core Native Package Categories
PACKAGE_CATALOG: Dict[str, Dict[str, Any]] = {
    "core_clients": {
        "title": "Core Gaming Clients",
        "description": "Native Steam, Lutris, and Flatpak package manager",
        "packages": ["steam", "lutris", "flatpak"]
    },
    "wine_stack": {
        "title": "Wine-Staging & Windows Compatibility",
        "description": "Wine-Staging with Esync/Fsync and native Wayland staging driver",
        "packages": [
            "wine-staging", "wine-gecko", "wine-mono", "winetricks",
            "cabextract", "samba", "zenity", "kdialog"
        ]
    },
    "runtime_32bit": {
        "title": "32-Bit Audio, Video, & Vulkan Compatibility Layer",
        "description": "Essential 32-bit runtimes preventing missing-symbol crashes in Wine/Proton",
        "packages": [
            "lib32-gnutls", "lib32-gtk3", "lib32-libpulse", "lib32-alsa-plugins",
            "lib32-vulkan-icd-loader", "vulkan-icd-loader", "vulkan-tools",
            "lib32-libxcomposite", "lib32-libxinerama", "lib32-libxrandr",
            "lib32-libxcursor", "lib32-libxi", "lib32-libxtst",
            "lib32-libpng", "lib32-libldap", "lib32-vkd3d",
            "ttf-liberation", "ttf-dejavu", "noto-fonts"
        ]
    },
    "performance_tools": {
        "title": "Performance & Overlay Stack (Pure Wayland)",
        "description": "Gamescope micro-compositor, GameMode daemon, MangoHud HUD, Goverlay GUI",
        "packages": [
            "gamescope", "gamemode", "lib32-gamemode",
            "mangohud", "lib32-mangohud", "goverlay",
            "qt5-wayland", "qt6-wayland"
        ]
    },
    "repack_tools": {
        "title": "Repack Extraction, Container, & Mount Utilities",
        "description": "Decompression & filesystem utilities for high-compression game repacks",
        "packages": [
            "desktop-file-utils", "fuse-overlayfs", "bubblewrap", "psmisc",
            "7zip", "unrar", "innoextract"
        ]
    }
}

# GPU Vendor Identification Matrix (Physical + Virtualized GPUs)
GPU_VENDOR_MAP: Dict[str, str] = {
    "0x8086": "Intel",
    "0x1002": "AMD",
    "0x10de": "NVIDIA",
    "0x1af4": "RedHat VirtIO (VM)",
    "0x15ad": "VMware (VM)",
    "0x80ee": "VirtualBox (VM)",
    "0x1234": "QEMU Bochs (VM)",
    "0x1414": "Hyper-V (VM)",
    "0x1b36": "RedHat QXL (VM)",
}

# GPU Driver Packages Matrix
GPU_DRIVER_CATALOG: Dict[str, Dict[str, Any]] = {
    "amd": {
        "name": "AMD (Radeon)",
        "packages": [
            "mesa", "lib32-mesa",
            "vulkan-radeon", "lib32-vulkan-radeon",
            "vulkan-mesa-layers", "lib32-vulkan-mesa-layers"
        ],
        "description": "AMD Radeon Vulkan (RADV) & 32/64-bit Mesa"
    },
    "intel": {
        "name": "Intel (Arc / Iris Xe)",
        "packages": [
            "mesa", "lib32-mesa",
            "vulkan-intel", "lib32-vulkan-intel",
            "intel-media-driver",
            "vulkan-mesa-layers", "lib32-vulkan-mesa-layers"
        ],
        "description": "Intel Vulkan (ANV), VA-API Media Driver, & 32/64-bit Mesa"
    },
    "nvidia": {
        "name": "NVIDIA (GeForce)",
        "packages": [
            "nvidia-open-dkms",
            "nvidia-utils", "lib32-nvidia-utils",
            "libva-nvidia-driver", "nvidia-settings",
            "opencl-nvidia", "lib32-opencl-nvidia",
            "egl-wayland"
        ],
        "description": "NVIDIA Open DKMS, 32/64-bit Vulkan/OpenGL, VA-API, & Wayland EGL"
    },
    "hybrid_nvidia_intel": {
        "name": "Hybrid (Intel iGPU + NVIDIA dGPU)",
        "packages": [
            "mesa", "lib32-mesa",
            "vulkan-intel", "lib32-vulkan-intel", "intel-media-driver",
            "nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils",
            "libva-nvidia-driver", "nvidia-prime", "egl-wayland"
        ],
        "description": "Intel iGPU (Display) + NVIDIA dGPU (3D Render) with prime-run offload"
    },
    "hybrid_nvidia_amd": {
        "name": "Hybrid (AMD iGPU + NVIDIA dGPU)",
        "packages": [
            "mesa", "lib32-mesa",
            "vulkan-radeon", "lib32-vulkan-radeon",
            "nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils",
            "libva-nvidia-driver", "nvidia-prime", "egl-wayland"
        ],
        "description": "AMD iGPU (Display) + NVIDIA dGPU (3D Render) with prime-run offload"
    },
    "virtual": {
        "name": "Virtualized GPU (VM / Container)",
        "packages": ["mesa", "lib32-mesa", "vulkan-virtio", "vulkan-swrast"],
        "description": "Virtualized 32/64-bit Mesa & VirtIO / Software Vulkan drivers"
    }
}

# Flatpak Applications Catalog
FLATPAK_APP_CATALOG: List[Dict[str, Any]] = [
    {"name": "Bottles", "id": "com.usebottles.bottles", "wayland": True, "host_fs": True},
    {"name": "Flatseal", "id": "com.github.tchx84.Flatseal", "wayland": False, "host_fs": False},
    {"name": "ProtonPlus", "id": "com.vysp3r.ProtonPlus", "wayland": False, "host_fs": False},
    {"name": "ProtonUp-Qt", "id": "net.davidotek.pupgui2", "wayland": False, "host_fs": False},
    {"name": "Heroic Games Launcher", "id": "com.heroicgameslauncher.hgl", "wayland": True, "host_fs": True}
]

# Flatpak Vulkan Runtime Layers
FLATPAK_VULKAN_LAYERS = ("MangoHud", "gamescope")

# Kernel & System Tuning Configuration
SYSCTL_GAMING_CONF = """# Gaming performance & stability optimizations for Arch Linux / Kernel 7.x+
# Minimum memory mapping limit for games with many mappings.
vm.max_map_count = 1048576
"""

LIMITS_GAMING_CONF = """# File descriptor limits for Wine/Proton ESYNC & FSYNC
* soft nofile 524288
* hard nofile 1048576
"""


# ==============================================================================
# 2. RUNTIME CONTEXT & PRE-FLIGHT INITIALIZATION
# ==============================================================================

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("The 'rich' library is required. Install it with: sudo pacman -Syu python-rich", file=sys.stderr)
    sys.exit(1)

console = Console()


@dataclass
class GPUInfo:
    dev_node: str
    pci_slot: str
    vendor_id: str
    vendor_name: str
    device_name: str
    boot_vga: int  # 1 = firmware boot display; 0 does not imply render-only
    driver: str


@dataclass
class CPUInfo:
    model: str
    cores: int
    x86_version: int  # 1 (baseline), 2 (SSE4.2), 3 (AVX2/BMI2), 4 (AVX-512)


@dataclass
class SelectedModules:
    categories: Set[str] = field(default_factory=lambda: set(PACKAGE_CATALOG.keys()))
    gpu_drivers: bool = True
    sysctl_tuning: bool = True
    dwarfs_mode: str = "bin"  # "bin", "native", "source", or "skip"
    dwarfs_reinstall: bool = False
    protonup_mode: str = "bin"  # "bin", "source", "flatpak", or "skip"
    flatpak_apps: bool = True
    launcher_bridge: bool = True
    extra_packages: List[str] = field(default_factory=list)


class SetupContext:
    def __init__(
        self,
        dry_run: bool = False,
        auto_yes: bool = False,
        modules: Optional[SelectedModules] = None
    ):
        self.dry_run = dry_run
        self.auto_yes = auto_yes
        self.modules = modules or SelectedModules()
        self.stop_sudo_event = threading.Event()
        self.sudo_thread: Optional[threading.Thread] = None
        self.failures: List[str] = []


def send_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    icon: str = "applications-games",
    expire_time_ms: int = 10000,
) -> None:
    """Dispatches a desktop notification to the Wayland session even across sudo boundaries."""
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass

    if not shutil.which("notify-send"):
        return

    env = os.environ.copy()
    sudo_uid = env.get("SUDO_UID")
    if sudo_uid and "DBUS_SESSION_BUS_ADDRESS" not in env:
        user_bus = Path(f"/run/user/{sudo_uid}/bus")
        if user_bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={user_bus}"

    cmd = [
        "notify-send",
        "-a", "Arch Gaming Setup",
        "-u", urgency,
        "-t", str(expire_time_ms),
        "-i", icon,
        title,
        message,
    ]
    try:
        subprocess.run(
            cmd,
            env=env,
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def keep_sudo_alive(stop_event: threading.Event):
    """Refreshes sudo credential timestamp cache in the background every 90 seconds."""
    while not stop_event.is_set():
        try:
            subprocess.run(["sudo", "-n", "-v"], check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        stop_event.wait(90)


def check_root_and_locks(ctx: SetupContext):
    """Never remove pacman's lock while another process could acquire it."""
    if os.geteuid() == 0:
        console.print("[bold red]CRITICAL ERROR: Do not run this script as root.[/bold red]")
        console.print("Run it as your normal user. Sudo will be invoked securely with proper permissions.")
        sys.exit(1)

    db_lck = Path("/var/lib/pacman/db.lck")
    if db_lck.exists():
        raise RuntimeError(f"Pacman database is locked at {db_lck}; inspect the owning package manager before retrying")


def run_command(
    ctx: SetupContext,
    command: Sequence[str],
    description: str,
    critical: bool = True,
    show_command: bool = True,
    retries: int = 1,
    extra_env: Optional[Dict[str, str]] = None
) -> bool:
    """Run an argument vector with dry-run simulation and retry support."""
    if isinstance(command, (str, bytes)) or not command or \
            not all(isinstance(part, str) for part in command):
        raise TypeError("run_command requires a nonempty sequence of string arguments")
    console.print(f"\n[bold cyan]Task:[/bold cyan] {description}")
    if show_command:
        console.print(f"[dim]{shlex.join(command)}[/dim]")

    if ctx.dry_run:
        console.print("[dim][DRY RUN] Skipped actual execution.[/dim]")
        return True

    if not ctx.auto_yes:
        if not Confirm.ask("[bold yellow]Execute this step?[/bold yellow]", default=True):
            console.print("[dim]Skipped by user.[/dim]")
            if critical:
                raise RuntimeError(f"Required step was skipped: {description}")
            ctx.failures.append(f"Skipped: {description}")
            return False

    console.print("[dim]" + "─" * 60 + "[/dim]")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                console.print(f"[yellow]Retrying task (attempt {attempt}/{retries})...[/yellow]")
            result = subprocess.run(list(command), env=env)
            console.print("[dim]" + "─" * 60 + "[/dim]")

            if result.returncode == 0:
                console.print("[bold green]✔ Success[/bold green]")
                return True
            else:
                console.print(f"[bold red]✘ Failed with exit code {result.returncode}[/bold red]")
                if attempt < retries:
                    time.sleep(2)
                    continue
                if critical:
                    console.print("[bold red]A critical step failed. Aborting installer to maintain system stability.[/bold red]")
                    send_notification("Gaming Setup Failed", f"Error executing: {description}", urgency="critical")
                    sys.exit(1)
                ctx.failures.append(description)
                return False
        except Exception as e:
            console.print(f"[bold red]✘ Execution error: {e}[/bold red]")
            if attempt < retries:
                time.sleep(2)
                continue
            if critical:
                send_notification("Gaming Setup Error", f"Fatal error: {e}", urgency="critical")
                sys.exit(1)
            ctx.failures.append(description)
            return False
    return False


# ==============================================================================
# 3. PACMAN & HARDWARE DETECTION ENGINES
# ==============================================================================

def enable_multilib_and_optimizations(
    ctx: SetupContext, pacman_conf: Path = Path("/etc/pacman.conf")
) -> bool:
    """Idempotently configures /etc/pacman.conf with multilib, ParallelDownloads, and timeout protection."""
    if not pacman_conf.exists():
        console.print("[bold red]Critical system file /etc/pacman.conf not found![/bold red]")
        sys.exit(1)

    try:
        content = pacman_conf.read_text()
        lines = content.splitlines()
    except Exception as e:
        console.print(f"[bold red]Failed to read pacman.conf: {e}[/bold red]")
        sys.exit(1)

    multilib_active = False
    include_active = False
    in_multilib_check = False
    has_disable_timeout = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[multilib]":
            multilib_active = True
            in_multilib_check = True
        elif in_multilib_check and stripped.startswith("[") and stripped.endswith("]"):
            in_multilib_check = False
        elif in_multilib_check and re.match(r"^Include\s*=", stripped):
            include_active = True
        if "DisableDownloadTimeout" in stripped and not stripped.startswith("#"):
            has_disable_timeout = True

    multilib_ready = multilib_active and include_active

    new_lines = []
    modified = False
    in_multilib_edit = False
    found_multilib_comment = False
    options_passed = False

    for line in lines:
        stripped = line.strip()

        if re.match(r"^#\s*Color\b", stripped):
            new_lines.append("Color")
            modified = True
            continue

        if re.match(r"^#\s*ParallelDownloads\b", stripped):
            new_lines.append("ParallelDownloads = 5")
            modified = True
            continue

        if stripped == "[options]":
            options_passed = True

        if options_passed and not has_disable_timeout and (stripped.startswith("[") and stripped != "[options]"):
            new_lines.append("DisableDownloadTimeout")
            has_disable_timeout = True
            modified = True

        if not multilib_ready:
            if re.match(r"^\s*#\s*\[multilib\]\s*$", line):
                new_lines.append("[multilib]")
                in_multilib_edit = True
                found_multilib_comment = True
                modified = True
                continue

            if in_multilib_edit:
                if re.match(r"^\s*#?\s*\[.*\]\s*$", line) and "multilib" not in line:
                    in_multilib_edit = False
                elif re.match(r"^\s*#\s*Include\s*=", line):
                    new_lines.append(re.sub(r"^\s*#\s*", "", line))
                    modified = True
                    continue

        new_lines.append(line)

    if options_passed and not has_disable_timeout:
        option_index = next(i for i, line in enumerate(new_lines) if line.strip() == "[options]")
        new_lines.insert(option_index + 1, "DisableDownloadTimeout")
        modified = True

    if not multilib_ready and not found_multilib_comment and not multilib_active:
        new_lines.extend(["", "[multilib]", "Include = /etc/pacman.d/mirrorlist"])
        modified = True

    if multilib_active and not include_active:
        start = new_lines.index("[multilib]")
        end = next((i for i in range(start + 1, len(new_lines))
                    if new_lines[i].strip().startswith("[")), len(new_lines))
        new_lines.insert(end, "Include = /etc/pacman.d/mirrorlist")
        modified = True

    if modified:
        if ctx.dry_run:
            console.print("[dim][DRY RUN] Would update /etc/pacman.conf[/dim]")
            return True
        with tempfile.TemporaryDirectory(prefix="gaming-pacman-") as tmp:
            temp_conf = Path(tmp) / "pacman.conf"
            temp_conf.write_text("\n".join(new_lines) + "\n")
            backup = f"/etc/pacman.conf.bak.{datetime.now():%Y%m%d_%H%M%S}"
            run_command(ctx, ["sudo", "cp", "--", str(pacman_conf), backup],
                        "Back up pacman.conf", show_command=False)
            run_command(ctx, ["sudo", "install", "-m", "644", "--", str(temp_conf), str(pacman_conf)],
                        "Configure pacman.conf", show_command=False)
        return True

    return False


def filter_missing_packages(pkgs: Sequence[str]) -> List[str]:
    """Filters package list using pacman -Q to only return packages not installed."""
    if not pkgs:
        return []
    try:
        res = subprocess.run(["pacman", "-Q", *pkgs], capture_output=True, text=True)
        installed = {line.split()[0] for line in res.stdout.splitlines() if line.strip()}
        return [p for p in pkgs if p not in installed]
    except Exception:
        return list(pkgs)


def detect_cpu_info() -> CPUInfo:
    """Detects CPU model name, logical thread count, and x86-64 microarchitecture tier (v1-v4)."""
    cores = os.cpu_count() or 4
    model_name = "Generic x86-64 CPU"
    x86_ver = 1

    try:
        flags_txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        per_cpu_flags: List[Set[str]] = []
        for line in flags_txt.splitlines():
            if line.startswith("model name"):
                model_name = line.split(":", 1)[1].strip()
            elif line.startswith("flags"):
                per_cpu_flags.append(set(line.split(":", 1)[1].split()))

        flags = set.intersection(*per_cpu_flags) if per_cpu_flags else set()

        v4_flags = {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
        v2_flags = {"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"}
        v3_flags = v2_flags | {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma",
                              "movbe", "xsave", "abm"}
        v4_flags |= v3_flags

        if flags and v4_flags.issubset(flags):
            x86_ver = 4
        elif v3_flags.issubset(flags):
            x86_ver = 3
        elif v2_flags.issubset(flags):
            x86_ver = 2
        else:
            x86_ver = 1
    except Exception:
        pass

    return CPUInfo(model=model_name, cores=cores, x86_version=x86_ver)


def probe_vaapi_drivers() -> Dict[str, bool]:
    """Probes /usr/lib/dri for installed VA-API hardware video acceleration drivers."""
    dri_dirs = [Path("/usr/lib/dri"), Path("/usr/lib64/dri")]
    found = {}
    for k in ["nvidia", "nouveau", "iHD", "i965", "radeonsi"]:
        name = f"{k}_drv_video.so"
        found[k] = any((d / name).exists() for d in dri_dirs)
    return found


def nvidia_modeset_confirmed(cards: Optional[List[GPUInfo]] = None) -> bool:
    """Verifies that nvidia_drm.modeset=1 is confirmed active on the running system."""
    p = Path("/sys/module/nvidia_drm/parameters/modeset")
    if p.exists():
        try:
            val = p.read_text().strip().lower()
            if val in ("y", "1", "n", "0"):
                return val in ("y", "1")
        except PermissionError:
            try:
                res = subprocess.run(["sudo", "-n", "cat", str(p)], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip().lower() in ("y", "1"):
                    return True
            except Exception:
                pass

    try:
        cmdline = Path("/proc/cmdline").read_text()
        if "nvidia-drm.modeset=1" in cmdline or "nvidia_drm.modeset=1" in cmdline:
            return True
    except Exception:
        pass

    try:
        ver_p = Path("/sys/module/nvidia/version")
        if ver_p.exists():
            ver_str = ver_p.read_text().strip()
            m = re.match(r"^(\d+)", ver_str)
            if m and int(m.group(1)) >= 560:
                return True
    except Exception:
        pass

    if cards:
        for c in cards:
            if "nvidia" in c.vendor_name.lower() and c.driver.lower() == "nvidia" and c.dev_node.startswith("/dev/dri/card"):
                return True

    return False


def detect_gpus() -> List[GPUInfo]:
    """
    Intelligently auto-detects all GPUs present on the system via DRM card nodes and lspci.
    Identifies vendor, PCI slot, firmware boot display status, and active driver.
    """
    gpus: List[GPUInfo] = []
    seen_slots: Set[str] = set()

    # Strategy 1: Scan /sys/class/drm/card* directly to resolve device nodes and boot_vga
    for s in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        p = Path(s)
        if not re.fullmatch(r"card\d+", p.name):
            continue
        dev_node = f"/dev/dri/{p.name}"
        if not Path(dev_node).exists():
            continue

        try:
            sys_dev = Path(os.path.realpath(p / "device"))
        except Exception:
            continue

        # Climb path up to vendor directory
        vdir = None
        cur = sys_dev
        for _ in range(10):
            if (cur / "vendor").exists():
                vdir = cur
                break
            if cur == cur.parent:
                break
            cur = cur.parent

        if not vdir:
            continue

        try:
            vid = vdir.joinpath("vendor").read_text().strip().lower()
        except Exception:
            continue

        pci_slot = vdir.name
        if pci_slot in seen_slots:
            continue
        seen_slots.add(pci_slot)

        boot_vga = 0
        for bp in [vdir / "boot_vga", sys_dev / "boot_vga"]:
            if bp.exists():
                try:
                    boot_vga = int(bp.read_text().strip())
                    break
                except Exception:
                    pass

        driver = "unknown"
        for d in [vdir / "driver", sys_dev / "driver"]:
            if d.exists():
                try:
                    driver = Path(os.path.realpath(d)).name
                    break
                except Exception:
                    pass

        vendor_name = GPU_VENDOR_MAP.get(vid, f"Unknown ({vid})")
        device_name = f"Graphics Device [{pci_slot}]"

        if shutil.which("lspci"):
            try:
                res = subprocess.run(["lspci", "-s", pci_slot], capture_output=True, text=True)
                if res.stdout.strip():
                    m = re.match(r"^[0-9a-fA-F:.]+ [^:]+: (.+)$", res.stdout.strip())
                    if m:
                        device_name = m.group(1)
            except Exception:
                pass

        gpus.append(GPUInfo(
            dev_node=dev_node,
            pci_slot=pci_slot,
            vendor_id=vid,
            vendor_name=vendor_name,
            device_name=device_name,
            boot_vga=boot_vga,
            driver=driver
        ))

    # Fallback to lspci if DRM nodes were not populated
    if not gpus and shutil.which("lspci"):
        try:
            res = subprocess.run(["lspci", "-mm", "-nn"], capture_output=True, text=True, check=True)
            for line in res.stdout.splitlines():
                if any(ctrl in line for ctrl in ['"0300"', '"0301"', '"0302"', "VGA", "3D", "Display"]):
                    parts = re.findall(r'"([^"]*)"', line)
                    slot = line.split()[0]
                    if slot in seen_slots:
                        continue
                    seen_slots.add(slot)

                    device_name = parts[2] if len(parts) > 2 else "Unknown Graphics Controller"
                    vendor_id = ""
                    vendor_name = parts[1] if len(parts) > 1 else "Unknown Vendor"

                    if "[10de]" in line or "10de:" in line:
                        vendor_id = "0x10de"
                        vendor_name = "NVIDIA"
                    elif "[1002]" in line or "1002:" in line:
                        vendor_id = "0x1002"
                        vendor_name = "AMD"
                    elif "[8086]" in line or "8086:" in line:
                        vendor_id = "0x8086"
                        vendor_name = "Intel"

                    gpus.append(GPUInfo(
                        dev_node=f"/dev/dri/card{len(gpus)}",
                        pci_slot=slot,
                        vendor_id=vendor_id,
                        vendor_name=vendor_name,
                        device_name=device_name,
                        boot_vga=1 if len(gpus) == 0 else 0,
                        driver="unknown"
                    ))
        except Exception:
            pass

    return sorted(gpus, key=lambda g: (-g.boot_vga, g.pci_slot))


def get_gpu_packages(detected_gpus: List[GPUInfo]) -> Tuple[List[str], str]:
    """Determines required GPU driver packages based on GPU_DRIVER_CATALOG."""
    pkgs: Set[str] = set()
    descriptions: List[str] = []

    has_amd = any("amd" in g.vendor_name.lower() or g.vendor_id == "0x1002" for g in detected_gpus)
    has_intel = any("intel" in g.vendor_name.lower() or g.vendor_id == "0x8086" for g in detected_gpus)
    has_nvidia = any("nvidia" in g.vendor_name.lower() or g.vendor_id == "0x10de" for g in detected_gpus)
    has_vm = any("(vm)" in g.vendor_name.lower() for g in detected_gpus)

    if has_vm and not (has_amd or has_intel or has_nvidia):
        pkgs.update(GPU_DRIVER_CATALOG["virtual"]["packages"])
        descriptions.append(GPU_DRIVER_CATALOG["virtual"]["description"])
    else:
        if has_amd:
            pkgs.update(GPU_DRIVER_CATALOG["amd"]["packages"])
            descriptions.append(GPU_DRIVER_CATALOG["amd"]["description"])
        if has_intel:
            pkgs.update(GPU_DRIVER_CATALOG["intel"]["packages"])
            descriptions.append(GPU_DRIVER_CATALOG["intel"]["description"])
        if has_nvidia:
            pkgs.update(GPU_DRIVER_CATALOG["nvidia"]["packages"])
            descriptions.append(GPU_DRIVER_CATALOG["nvidia"]["description"])
        if has_nvidia and (has_amd or has_intel):
            pkgs.add("nvidia-prime")

    return sorted(list(pkgs)), ", ".join(descriptions)


# ==============================================================================
# 4. MODULE EXECUTION HANDLERS
# ==============================================================================

def configure_gpu_drivers(ctx: SetupContext):
    """Presents detected GPUs and installs required Vulkan & OpenGL drivers."""
    if not ctx.modules.gpu_drivers:
        console.print("[dim]Skipping GPU driver installation as configured.[/dim]")
        return

    detected_gpus = detect_gpus()

    table = Table(title="Detected Graphics Hardware & Roles", show_header=True, header_style="bold magenta")
    table.add_column("Node", style="dim")
    table.add_column("PCI Slot", style="cyan")
    table.add_column("Vendor", style="bold green")
    table.add_column("Device Model", style="white")
    table.add_column("Role", style="bold yellow")
    table.add_column("Active Driver", style="dim")

    if detected_gpus:
        for g in detected_gpus:
            role_badge = "[bold green]Boot VGA[/bold green]" if g.boot_vga == 1 else "[cyan]Other display GPU[/cyan]"
            table.add_row(g.dev_node, g.pci_slot, g.vendor_name, g.device_name[:45], role_badge, g.driver or "Unknown")
        console.print(table)
    else:
        console.print("[yellow]No discrete or integrated GPUs auto-detected via PCI/DRM.[/yellow]")

    auto_pkgs, auto_desc = get_gpu_packages(detected_gpus)

    if detected_gpus and auto_pkgs:
        console.print(f"\n[bold green]Auto-detected profile:[/bold green] {auto_desc}")
        if ctx.auto_yes:
            gpu_choice = "1"
        else:
            console.print("\n[bold cyan]Select GPU Installation Mode:[/bold cyan]")
            console.print("1. Install auto-detected drivers [bold green](Recommended)[/bold green]")
            console.print("2. AMD (Radeon Vulkan + Mesa)")
            console.print("3. NVIDIA (GeForce Open-DKMS + Wayland + 32-bit)")
            console.print("4. Intel (Arc / Iris Xe Vulkan + Media Driver)")
            console.print("5. Hybrid (Intel/AMD iGPU + NVIDIA dGPU + prime-run)")
            console.print("6. Skip (I manually manage graphics drivers)")
            gpu_choice = Prompt.ask("Enter choice", choices=["1", "2", "3", "4", "5", "6"], default="1")
    else:
        console.print("\n[bold cyan]Select GPU Vendor for Vulkan & 32-bit Drivers:[/bold cyan]")
        console.print("1. AMD (Radeon Vulkan + Mesa)")
        console.print("2. NVIDIA (GeForce Open-DKMS + Wayland + 32-bit)")
        console.print("3. Intel (Arc / Iris Xe Vulkan + Media Driver)")
        console.print("4. Hybrid (Intel/AMD iGPU + NVIDIA dGPU + prime-run)")
        console.print("5. Skip (I manually manage graphics drivers)")
        gpu_choice = Prompt.ask("Enter choice", choices=["1", "2", "3", "4", "5"], default="5")

    target_pkgs = []
    target_desc = ""

    if detected_gpus and auto_pkgs and gpu_choice == "1":
        target_pkgs = auto_pkgs
        target_desc = f"Install auto-detected GPU drivers: {auto_desc}"
    elif gpu_choice == ("2" if (detected_gpus and auto_pkgs) else "1"):
        target_pkgs = GPU_DRIVER_CATALOG["amd"]["packages"]
        target_desc = GPU_DRIVER_CATALOG["amd"]["description"]
    elif gpu_choice == ("3" if (detected_gpus and auto_pkgs) else "2"):
        target_pkgs = GPU_DRIVER_CATALOG["nvidia"]["packages"]
        target_desc = GPU_DRIVER_CATALOG["nvidia"]["description"]
    elif gpu_choice == ("4" if (detected_gpus and auto_pkgs) else "3"):
        target_pkgs = GPU_DRIVER_CATALOG["intel"]["packages"]
        target_desc = GPU_DRIVER_CATALOG["intel"]["description"]
    elif gpu_choice == ("5" if (detected_gpus and auto_pkgs) else "4"):
        target_pkgs = GPU_DRIVER_CATALOG[
            "hybrid_nvidia_amd" if any(g.vendor_id == "0x1002" for g in detected_gpus)
            else "hybrid_nvidia_intel"
        ]["packages"]
        target_desc = "Install Hybrid Multi-GPU drivers (Intel/AMD + NVIDIA + prime-run offload)"
    else:
        console.print("[dim]Skipping GPU driver installation.[/dim]")
        return

    if target_pkgs:
        missing_pkgs = filter_missing_packages(target_pkgs)
        if missing_pkgs:
            pkgs_str = " ".join(missing_pkgs)
            run_command(ctx, ["sudo", "pacman", "-S", "--needed", "--noconfirm", "--", *missing_pkgs], f"{target_desc} ({len(missing_pkgs)} missing)", retries=3)
        else:
            console.print(f"[bold green]✔ All {len(target_pkgs)} required GPU driver packages are already installed.[/bold green]")

    # Validate NVIDIA DRM Modesetting on Wayland
    has_nvidia = any("nvidia" in g.vendor_name.lower() or g.vendor_id == "0x10de" for g in detected_gpus)
    if has_nvidia and not nvidia_modeset_confirmed(detected_gpus):
        console.print(Panel(
            "[bold yellow]Notice: NVIDIA DRM Modesetting[/bold yellow]\n"
            "Wayland compositors (Hyprland) and PRIME offload require kernel modesetting.\n"
            "Ensure [green]nvidia_drm.modeset=1[/green] is set in your kernel parameters if on drivers < 560.",
            border_style="yellow"
        ))


def apply_kernel_and_sysctl_optimizations(ctx: SetupContext):
    """Raise the mapping limit when needed and install login file limits."""
    if not ctx.modules.sysctl_tuning:
        console.print("[dim]Skipping kernel sysctl tweaks as configured.[/dim]")
        return

    console.print("\n[bold cyan]Configuring Kernel & System Gaming Optimizations...[/bold cyan]")

    sysctl_file = Path("/etc/sysctl.d/99-gaming.conf")
    limits_file = Path("/etc/security/limits.d/99-gaming.conf")

    if ctx.dry_run:
        console.print("[dim][DRY RUN] Would install sysctl and limits configuration.[/dim]")
        return
    with tempfile.TemporaryDirectory(prefix="gaming-tuning-") as tmp:
        temp_sysctl = Path(tmp) / "sysctl.conf"
        temp_limits = Path(tmp) / "limits.conf"
        current_map = 0
        with suppress(OSError, ValueError):
            current_map = int(Path("/proc/sys/vm/max_map_count").read_text().strip())
        temp_sysctl.write_text(SYSCTL_GAMING_CONF.replace("1048576", str(max(1048576, current_map))))
        temp_limits.write_text(LIMITS_GAMING_CONF)
        run_command(ctx, ["sudo", "install", "-m", "644", "--", str(temp_sysctl), str(sysctl_file)],
                    "Install gaming sysctl settings")
        run_command(ctx, ["sudo", "install", "-m", "644", "--", str(temp_limits), str(limits_file)],
                    "Install gaming login limits")
        run_command(ctx, ["sudo", "sysctl", "-p", str(sysctl_file)],
                    "Apply gaming sysctl settings", critical=False)


def install_native_gaming_stack(ctx: SetupContext):
    """Installs native gaming packages and 32/64-bit runtime libraries from PACKAGE_CATALOG."""
    native_packages: Set[str] = set()

    for category_key in ctx.modules.categories:
        if category_key in PACKAGE_CATALOG:
            native_packages.update(PACKAGE_CATALOG[category_key]["packages"])

    if ctx.modules.extra_packages:
        native_packages.update(ctx.modules.extra_packages)

    if native_packages:
        missing_native = filter_missing_packages(sorted(list(native_packages)))
        if missing_native:
            pkgs_str = " ".join(missing_native)
            run_command(
                ctx,
                ["sudo", "pacman", "-S", "--needed", "--noconfirm", "--", *missing_native],
                f"Install missing native gaming packages and runtime libraries ({len(missing_native)} missing).",
                retries=3
            )
        else:
            console.print(f"[bold green]✔ All {len(native_packages)} selected native gaming packages and runtime libraries are already installed.[/bold green]")

    # Ensure Lutris runner directory exists for ProtonUp-Qt / GE-Proton integration
    if not ctx.dry_run:
        try:
            lutris_runners_dir = Path.home() / ".local/share/lutris/runners/wine"
            lutris_runners_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    if "performance_tools" in ctx.modules.categories:
        # Enable GameMode daemon service for the current user session
        run_command(
            ctx,
            ["systemctl", "--user", "enable", "--now", "gamemoded.service"],
            "Enable and start Feral GameMode user daemon",
            critical=False
        )


def configure_dwarfs(ctx: SetupContext):
    """
    Installs DwarFS compression tools via AUR with user-selected packaging strategy:
    - 'bin': Fast pre-compiled binary (dwarfs-bin) - instant 2-second install.
    - 'native': CPU native architecture build (-march=native -O3) targeting host microarchitecture.
    - 'source': Standard AUR source build.
    - 'skip': Do not install.
    """
    mode = ctx.modules.dwarfs_mode
    if mode == "skip":
        console.print("[dim]Skipping DwarFS installation.[/dim]")
        return

    if shutil.which("dwarfs") and mode == "bin" and not ctx.modules.dwarfs_reinstall:
        console.print("[bold green]✔ DwarFS is already installed on the system.[/bold green]")
        return

    aur_helper = next((h for h in ("paru", "yay") if shutil.which(h)), None)
    if not aur_helper:
        console.print("[yellow]No AUR helper (paru/yay) detected. Skipping DwarFS installation.[/yellow]")
        return

    cpu = detect_cpu_info()

    if mode == "bin":
        run_command(
            ctx,
            [aur_helper, "-S", "--rebuild", "--noconfirm", "dwarfs-bin"],
            "Install pre-compiled DwarFS binary package (instant download, zero compile time)",
            critical=False
        )
    elif mode == "native":
        if ctx.dry_run:
            run_command(ctx, [aur_helper, "-S", "--rebuild", "--noconfirm", "dwarfs"],
                        f"Rebuild DwarFS for {cpu.model} with a temporary makepkg configuration",
                        critical=False)
        else:
            with tempfile.TemporaryDirectory(prefix="gaming-makepkg-") as td:
                conf = Path(td) / "makepkg.conf"
                conf.write_text(Path("/etc/makepkg.conf").read_text(encoding="utf-8") +
                                '\nCFLAGS="$CFLAGS -march=native -O3"\n' +
                                'CXXFLAGS="$CXXFLAGS -march=native -O3"\n' +
                                f'MAKEFLAGS="-j{cpu.cores}"\n', encoding="utf-8")
                helper_flags = (["--makepkgconf", str(conf)] if aur_helper == "yay"
                                else ["--mflags", f"--config {conf}"])
                run_command(ctx, [aur_helper, "-S", "--rebuild", "--noconfirm",
                                  *helper_flags, "dwarfs"],
                            f"Rebuild DwarFS for {cpu.model} using -march=native -O3",
                            critical=False)
    else:
        run_command(
            ctx,
            [aur_helper, "-S", "--rebuild", "--noconfirm", "dwarfs"],
            "Compile DwarFS tools from AUR",
            critical=False
        )


def configure_protonup(ctx: SetupContext):
    """
    Installs ProtonUp-Qt (GE-Proton / Wine-GE runner manager) via AUR with
    non-interactive provider resolution. Explicitly targets protonup-qt-bin
    to avoid paru's interactive provider prompt (protonup-qt vs -bin vs -git).
    Flatpak fallback (net.davidotek.pupgui2) is handled via FLATPAK_APP_CATALOG.
    """
    mode = ctx.modules.protonup_mode
    if mode == "skip":
        return

    if mode == "flatpak":
        if "net.davidotek.pupgui2" in get_installed_flatpaks():
            console.print("[bold green]✔ ProtonUp-Qt Flatpak is already installed.[/bold green]")
            return
        run_command(ctx, ["flatpak", "install", "--user", "-y", "--noninteractive",
                          "flathub", "net.davidotek.pupgui2"],
                    "Install ProtonUp-Qt Flatpak", critical=False)
        return

    if mode == "bin" and (shutil.which("protonup-qt") or shutil.which("pupgui2")):
        console.print("[bold green]✔ ProtonUp-Qt is already installed on the system.[/bold green]")
        return

    aur_helper = next((h for h in ("paru", "yay") if shutil.which(h)), None)
    if not aur_helper:
        console.print("[yellow]No AUR helper (paru/yay) detected. ProtonUp-Qt will be provided via Flatpak (net.davidotek.pupgui2) if Flatpak apps are enabled.[/yellow]")
        return

    console.print("\n[bold cyan]Installing ProtonUp-Qt (GE-Proton & Wine-GE manager for Lutris)...[/bold cyan]")
    target = "protonup-qt" if mode == "source" else "protonup-qt-bin"
    run_command(
        ctx,
        [aur_helper, "-S", "--rebuild", "--noconfirm", target],
        "Install ProtonUp-Qt (Proton-GE / Lutris-GE runner downloader) via AUR",
        critical=False
    )


def configure_dxvk_vkd3d(ctx: SetupContext):
    """
    Ensures Direct3D-to-Vulkan translation libraries (DXVK and VKD3D-Proton)
    are installed on the system and appropriately linked for Wine runners.
    """
    aur_helper = next((h for h in ("paru", "yay") if shutil.which(h)), None)

    missing = []
    if not Path("/usr/share/dxvk/x64/d3d11.dll").is_file():
        missing.append("dxvk-bin")
    if not (Path("/usr/share/vkd3d/x64/d3d12.dll").is_file() or Path("/usr/share/vkd3d-proton/x64/d3d12.dll").is_file()):
        missing.append("vkd3d-proton-bin")

    if missing:
        if aur_helper:
            pkgs_str = " ".join(missing)
            run_command(
                ctx,
                [aur_helper, "-S", "--needed", "--noconfirm", "--", *missing],
                f"Install Direct3D-to-Vulkan translation libraries ({pkgs_str}) via AUR",
                critical=False
            )
        else:
            console.print(f"[yellow]Warning: Missing translation libraries ({', '.join(missing)}) but no AUR helper found.[/yellow]")
    else:
        console.print("[bold green]✔ Direct3D-to-Vulkan translation libraries (DXVK & VKD3D-Proton) are already installed.[/bold green]")

def configure_flatpak_ecosystem(ctx: SetupContext):
    """Configures Flathub remotes, installs gaming Flatpaks, Vulkan layers, and native Wayland sandbox overrides."""
    if not ctx.modules.flatpak_apps:
        console.print("[dim]Skipping Flatpak applications as configured.[/dim]")
        return

    # 1. Add Flathub remotes for both user and system scope
    for scope in ("--user", "--system"):
        run_command(ctx, (["sudo"] if scope == "--system" else []) +
                    ["flatpak", "remote-add", scope, "--if-not-exists", "flathub",
                     "https://dl.flathub.org/repo/flathub.flatpakrepo"],
                    f"Initialize Flathub remote ({scope[2:]} scope)")

    # Flatpak selects matching GL/GL32 driver extensions during an update.
    for scope in ("--system", "--user"):
        run_command(ctx, (["sudo"] if scope == "--system" else []) +
                    ["flatpak", "update", scope, "-y", "--noninteractive"],
                    f"Update {scope[2:]} Flatpak apps, runtimes, and graphics drivers",
                    critical=False)
    ensure_flatpak_graphics_drivers(ctx)

    # 2. Install Flatpak apps from FLATPAK_APP_CATALOG
    installed_apps_by_scope = get_flatpak_inventory("--app")
    installed_apps = set().union(*installed_apps_by_scope.values())
    for app in FLATPAK_APP_CATALOG:
        if app["id"] in installed_apps:
            console.print(f"[bold green]✔ Flatpak application {app['name']} ({app['id']}) is already installed.[/bold green]")
        else:
            run_command(
                ctx,
                ["sudo", "flatpak", "install", "--system", "-y", "--noninteractive",
                 "--or-update", "flathub", app["id"]],
                f"Install {app['name']} via Flatpak sandbox.",
                critical=False
            )

    # 3. Install Flatpak MangoHud & Gamescope runtime layers
    installed_runtimes = get_flatpak_inventory("--runtime")
    for scope, refs in installed_runtimes.items():
        branches = {ref.partition("//")[2] for ref in refs
                    if ref.startswith("org.freedesktop.Platform//")}
        for branch in sorted(branches):
            for name in FLATPAK_VULKAN_LAYERS:
                layer_id = f"org.freedesktop.Platform.VulkanLayer.{name}//{branch}"
                if layer_id in refs:
                    continue
                run_command(ctx, (["sudo"] if scope == "--system" else []) +
                            ["flatpak", "install", scope, "-y", "--noninteractive",
                             "--or-update", "flathub", layer_id],
                            f"Install {scope[2:]} Flatpak Vulkan Layer {layer_id}",
                            critical=False)

    # 4. Configure native Wayland sockets and host filesystem overrides for gaming Flatpaks
    for app in FLATPAK_APP_CATALOG:
        permissions = []
        if app.get("wayland"):
            permissions.extend(("--socket=wayland", "--socket=fallback-x11"))
        if app.get("host_fs"):
            permissions.append("--filesystem=host")
        if not permissions:
            continue
        for scope, refs in installed_apps_by_scope.items():
            if app["id"] not in refs and not (scope == "--system" and app["id"] not in installed_apps):
                continue
            run_command(ctx, (["sudo"] if scope == "--system" else []) +
                        ["flatpak", "override", scope, *permissions, app["id"]],
                        f"Set {scope[2:]} Flatpak permissions for {app['name']}",
                        critical=False)


def get_flatpak_inventory(kind: str) -> Dict[str, Set[str]]:
    """Return installed refs by installation scope, preserving runtime branches."""
    result: Dict[str, Set[str]] = {"--system": set(), "--user": set()}
    for scope in result:
        query = ["flatpak", "list", scope, kind,
                 "--columns=application,branch" if kind == "--runtime" else "--columns=application"]
        try:
            cp = subprocess.run(query, capture_output=True, text=True, timeout=15, check=True)
            for line in cp.stdout.splitlines():
                parts = line.split()
                if parts:
                    result[scope].add(f"{parts[0]}//{parts[1]}" if kind == "--runtime" and len(parts) > 1
                                      else parts[0])
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def ensure_flatpak_graphics_drivers(ctx: SetupContext) -> None:
    """Install Flatpak NVIDIA GL extensions matching the active host driver."""
    try:
        cp = subprocess.run(["flatpak", "--gl-drivers"], capture_output=True,
                            text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        ctx.failures.append(f"Could not inspect Flatpak graphics drivers: {exc}")
        return
    active = next((line.strip() for line in cp.stdout.splitlines()
                   if line.strip().startswith("nvidia-")), "")
    if not active:
        return
    installed = get_flatpak_inventory("--runtime")
    for prefix in ("org.freedesktop.Platform.GL", "org.freedesktop.Platform.GL32"):
        ref = f"{prefix}.{active}"
        if any(item.startswith(ref + "//") for item in installed["--system"]):
            continue
        run_command(ctx, ["sudo", "flatpak", "install", "--system", "-y",
                          "--noninteractive", "--or-update", "flathub", ref],
                    f"Install Flatpak {ref} for the active NVIDIA driver",
                    critical=False)


def get_installed_flatpak_runtimes() -> Set[str]:
    """Dynamically fetches installed Flatpak runtime identifiers (e.g. app_id//branch)."""
    return set().union(*get_flatpak_inventory("--runtime").values())


def get_installed_flatpaks() -> List[str]:
    """Dynamically fetches a list of all installed Flatpak Application IDs across system and user scopes."""
    return sorted(set().union(*get_flatpak_inventory("--app").values()))


def integrate_desktop_and_icons(ctx: SetupContext):
    """Bridges Flatpak .desktop files and hicolor application icons into user XDG directories for Wayland launchers."""
    if not ctx.modules.launcher_bridge:
        console.print("[dim]Skipping launcher icon integration as configured.[/dim]")
        return

    if ctx.dry_run:
        console.print("[dim][DRY RUN] Would refresh Flatpak desktop entries and icons.[/dim]")
        return

    user_apps_dir = Path.home() / ".local/share/applications"
    user_icons_dir = Path.home() / ".local/share/icons/hicolor"

    user_apps_dir.mkdir(parents=True, exist_ok=True)
    user_icons_dir.mkdir(parents=True, exist_ok=True)

    system_export_dir = Path("/var/lib/flatpak/exports/share")
    user_export_dir = Path.home() / ".local/share/flatpak/exports/share"

    # Clean broken symlinks safely
    try:
        for f in user_apps_dir.iterdir():
            if f.is_symlink() and not f.exists():
                f.unlink()
    except Exception as e:
        console.print(f"[yellow]Warning during symlink cleanup: {e}[/yellow]")

    # Bridge .desktop entries
    installed_apps = get_installed_flatpaks()
    for app_id in installed_apps:
        desktop_file = f"{app_id}.desktop"
        target_path = None

        if (system_export_dir / "applications" / desktop_file).exists():
            target_path = system_export_dir / "applications" / desktop_file
        elif (user_export_dir / "applications" / desktop_file).exists():
            target_path = user_export_dir / "applications" / desktop_file

        if target_path:
            symlink_path = user_apps_dir / desktop_file
            if symlink_path.is_symlink() or symlink_path.exists():
                if not symlink_path.is_symlink():
                    continue
                try:
                    if os.readlink(symlink_path) == str(target_path):
                        continue
                except OSError:
                    pass
                symlink_path.unlink()

            symlink_path.symlink_to(target_path)

    # Bridge application icons across all hicolor resolutions
    for base_export in [system_export_dir / "icons/hicolor", user_export_dir / "icons/hicolor"]:
        if not base_export.exists():
            continue
        try:
            for size_dir in base_export.iterdir():
                if not size_dir.is_dir():
                    continue
                apps_icon_dir = size_dir / "apps"
                if not apps_icon_dir.exists():
                    continue

                target_user_icon_dir = user_icons_dir / size_dir.name / "apps"
                target_user_icon_dir.mkdir(parents=True, exist_ok=True)

                for icon_file in apps_icon_dir.iterdir():
                    if icon_file.is_file():
                        symlink_icon = target_user_icon_dir / icon_file.name
                        if symlink_icon.is_symlink() or symlink_icon.exists():
                            if not symlink_icon.is_symlink():
                                continue
                            try:
                                if os.readlink(symlink_icon) == str(icon_file):
                                    continue
                            except OSError:
                                pass
                            symlink_icon.unlink()
                        symlink_icon.symlink_to(icon_file)
        except Exception:
            pass

    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(user_apps_dir)], capture_output=True)
    if shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(user_icons_dir)], capture_output=True)


def integrate_game_runner_shortcuts(ctx: SetupContext) -> None:
    """Installs Master Runner desktop entries for all discovered game profiles."""
    runner_script = Path(__file__).resolve().parent / "runner/master_runner.py"
    if not runner_script.exists():
        runner_script = Path.home() / "user_scripts/gaming/runner/master_runner.py"
    if runner_script.exists():
        console.print("[cyan]Generating application launcher desktop shortcuts for all game profiles...[/cyan]")
        if not ctx.dry_run:
            res = subprocess.run([sys.executable, str(runner_script), "install-all-desktops"], capture_output=True, text=True)
            if res.returncode != 0:
                res = subprocess.run([sys.executable, str(runner_script), "desktop-all"], capture_output=True, text=True)
            if res.returncode == 0:
                console.print("[bold green]✔ Game profile desktop shortcuts installed into ~/.local/share/applications/.[/bold green]")
            else:
                console.print(f"[bold yellow]Warning: Could not install game desktop entries: {res.stderr.strip()}[/bold yellow]")


# ==============================================================================
# 5. INTERACTIVE DASHBOARD & SELECTION MENUS
# ==============================================================================

def check_system_installed_status() -> Dict[str, bool]:
    """Inspects the current system state to identify which components are already active."""
    status = {}
    status["steam"] = shutil.which("steam") is not None
    status["lutris"] = shutil.which("lutris") is not None
    status["wine"] = shutil.which("wine") is not None
    status["protonup"] = shutil.which("protonup-qt") is not None or shutil.which("pupgui2") is not None
    status["gamescope"] = shutil.which("gamescope") is not None
    status["gamemode"] = shutil.which("gamemoded") is not None
    status["mangohud"] = shutil.which("mangohud") is not None
    status["dwarfs"] = shutil.which("dwarfs") is not None
    status["dxvk"] = Path("/usr/share/dxvk/x64/d3d11.dll").is_file()
    status["vkd3d"] = Path("/usr/share/vkd3d/x64/d3d12.dll").is_file() or Path("/usr/share/vkd3d-proton/x64/d3d12.dll").is_file()

    try:
        res = subprocess.run(["sysctl", "-n", "vm.max_map_count"], capture_output=True, text=True)
        status["sysctl"] = int(res.stdout.strip()) >= 1048576
    except Exception:
        status["sysctl"] = False

    flatpaks = get_installed_flatpaks()
    status["flatpak_bottles"] = "com.usebottles.bottles" in flatpaks
    status["flatpak_heroic"] = "com.heroicgameslauncher.hgl" in flatpaks
    status["flatpak_pupgui"] = "net.davidotek.pupgui2" in flatpaks

    return status


def run_interactive_menu() -> Tuple[str, SelectedModules]:
    """Renders the main interactive selection dashboard with recommendations."""
    sys_status = check_system_installed_status()
    cpu = detect_cpu_info()
    vaapi = probe_vaapi_drivers()
    active_vaapi = [k for k, v in vaapi.items() if v]

    console.print(f"\n[bold cyan]Detected Processor:[/bold cyan] {cpu.model} ([bold green]{cpu.cores} threads, x86-64-v{cpu.x86_version}[/bold green])")
    if active_vaapi:
        console.print(f"[bold cyan]Hardware Video Acceleration (VA-API):[/bold cyan] [green]{', '.join(active_vaapi)}[/green]")

    console.print("\n[bold cyan]System State & Live Recommendations:[/bold cyan]")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Component", style="white")
    table.add_column("Status", style="bold")
    table.add_column("Recommendation", style="dim")

    table.add_row("Core Clients (Steam / Lutris)", "[green]✓ Installed[/green]" if (sys_status["steam"] and sys_status["lutris"]) else "[yellow]✗ Missing[/yellow]", "Essential")
    table.add_row("Wine-Staging & 32-bit Runtimes", "[green]✓ Installed[/green]" if sys_status["wine"] else "[yellow]✗ Missing[/yellow]", "Essential for Windows games")
    table.add_row("Direct3D Vulkan Translators (DXVK/VKD3D)", "[green]✓ Installed[/green]" if (sys_status["dxvk"] and sys_status["vkd3d"]) else "[yellow]✗ Missing[/yellow]", "Essential for D3D9/11/12 Wine games")
    table.add_row("ProtonUp-Qt (GE-Proton Runner Manager)", "[green]✓ Installed[/green]" if (sys_status["protonup"] or sys_status["flatpak_pupgui"]) else "[yellow]✗ Missing[/yellow]", "Recommended for Lutris runners")
    table.add_row("Performance Tools (Gamescope/MangoHud)", "[green]✓ Installed[/green]" if sys_status["mangohud"] else "[yellow]✗ Missing[/yellow]", "Recommended for Wayland")
    table.add_row("Kernel 7.x Sysctl Optimizations", "[green]✓ Active[/green]" if sys_status["sysctl"] else "[yellow]✗ Inactive[/yellow]", "Crucial (prevents UE5 crashes)")
    table.add_row("DwarFS Compression Tools", "[green]✓ Installed[/green]" if sys_status["dwarfs"] else "[dim]Optional[/dim]", "Required for repack mounts")
    table.add_row("Flatpak Sandbox Gaming (Bottles/Heroic)", "[green]✓ Installed[/green]" if sys_status["flatpak_bottles"] else "[dim]Optional[/dim]", "Recommended prefix manager")

    console.print(table)

    console.print("\n[bold cyan]Choose Setup Profile:[/bold cyan]")
    console.print("1. [bold green]Recommended Full Setup[/bold green] (Auto-detects GPU + all gaming tools + fast binary packages)")
    console.print("2. [bold yellow]Custom Component Checklist[/bold yellow] (Select exactly what to install/skip via interactive toggles)")
    console.print("3. [bold cyan]Minimal Core[/bold cyan] (GPU Drivers + Steam + Wine + Kernel Sysctl only)")
    console.print("4. [bold magenta]Performance Tuning Only[/bold magenta] (Sysctl vm.max_map_count & GameMode)")
    console.print("5. [bold blue]Maintenance / Icon Refresh[/bold blue] (Sync Flatpak desktop shortcuts, fix symlinks, update caches)")
    console.print("6. [red]Exit[/red]")

    choice = Prompt.ask("Enter selection", choices=["1", "2", "3", "4", "5", "6"], default="1")

    modules = SelectedModules()

    if choice == "1":
        modules.dwarfs_mode = "skip" if sys_status["dwarfs"] else "bin"
        modules.protonup_mode = "skip" if (sys_status["protonup"] or sys_status["flatpak_pupgui"]) else "bin"
        return "full", modules

    elif choice == "2":
        console.print("\n[bold cyan]── Custom Component Selection ──[/bold cyan]")
        modules.gpu_drivers = Confirm.ask("Install/Update GPU & Vulkan Drivers?", default=True)

        selected_cats: Set[str] = set()
        for cat_key, cat_data in PACKAGE_CATALOG.items():
            default_val = True
            if cat_key == "core_clients" and (sys_status["steam"] and sys_status["lutris"]):
                default_val = False
            elif cat_key == "wine_stack" and sys_status["wine"]:
                default_val = False
            elif cat_key == "performance_tools" and sys_status["mangohud"]:
                default_val = False

            if Confirm.ask(f"Install {cat_data['title']} ({len(cat_data['packages'])} packages)?", default=default_val):
                selected_cats.add(cat_key)

        modules.categories = selected_cats
        modules.sysctl_tuning = Confirm.ask("Apply vm.max_map_count and file descriptor limits?", default=not sys_status["sysctl"])

        # ProtonUp-Qt handling
        if sys_status["protonup"] or sys_status["flatpak_pupgui"]:
            console.print("[dim]ProtonUp-Qt is already installed (native or Flatpak).[/dim]")
            modules.protonup_mode = "skip"
        else:
            if Confirm.ask("Install ProtonUp-Qt (Lutris GE-Proton runner downloader via AUR/Flatpak)?", default=True):
                modules.protonup_mode = "bin"
            else:
                modules.protonup_mode = "skip"

        if sys_status["dwarfs"]:
            console.print("[dim]DwarFS is already installed.[/dim]")
            if Confirm.ask("Reinstall/Rebuild DwarFS?", default=False):
                modules.dwarfs_reinstall = True
                console.print("\n[bold cyan]DwarFS Packaging Mode:[/bold cyan]")
                console.print("1. Fast Pre-compiled Binary (dwarfs-bin) - Instant download (~2 seconds)")
                console.print(f"2. CPU Native Architecture Build (-march=native -O3) - Maximized performance on {cpu.cores} threads")
                console.print("3. Standard Source Build (dwarfs)")
                dw_choice = Prompt.ask("Choose DwarFS option", choices=["1", "2", "3"], default="1")
                modules.dwarfs_mode = "bin" if dw_choice == "1" else ("native" if dw_choice == "2" else "source")
            else:
                modules.dwarfs_mode = "skip"
        else:
            if Confirm.ask("Install DwarFS filesystem tools (used by compressed game repacks)?", default=True):
                console.print("\n[bold cyan]DwarFS Packaging Mode:[/bold cyan]")
                console.print("1. Fast Pre-compiled Binary (dwarfs-bin) - [bold green]Instant download (~2 seconds)[/bold green]")
                console.print(f"2. CPU Native Architecture Build (-march=native -O3) - [bold yellow]Maximized for {cpu.model}[/bold yellow]")
                console.print("3. Standard Source Build (dwarfs)")
                dw_choice = Prompt.ask("Choose DwarFS option", choices=["1", "2", "3"], default="1")
                modules.dwarfs_mode = "bin" if dw_choice == "1" else ("native" if dw_choice == "2" else "source")
            else:
                modules.dwarfs_mode = "skip"

        modules.flatpak_apps = Confirm.ask("Install Flatpak Gaming Apps (Bottles, Flatseal, ProtonPlus, Heroic)?", default=not sys_status["flatpak_bottles"])
        modules.launcher_bridge = Confirm.ask("Bridge Flatpak Desktop Shortcuts & Hicolor Icons into ~/.local/share/?", default=True)
        return "custom", modules

    elif choice == "3":
        modules.gpu_drivers = True
        modules.categories = {"core_clients", "wine_stack", "runtime_32bit"}
        modules.sysctl_tuning = True
        modules.dwarfs_mode = "skip"
        modules.protonup_mode = "bin"
        modules.flatpak_apps = False
        modules.launcher_bridge = False
        return "minimal", modules

    elif choice == "4":
        modules.gpu_drivers = False
        modules.categories = {"performance_tools"}
        modules.sysctl_tuning = True
        modules.dwarfs_mode = "skip"
        modules.protonup_mode = "skip"
        modules.flatpak_apps = False
        modules.launcher_bridge = False
        return "perf_only", modules

    elif choice == "5":
        modules.gpu_drivers = False
        modules.categories = set()
        modules.sysctl_tuning = False
        modules.dwarfs_mode = "skip"
        modules.protonup_mode = "skip"
        modules.flatpak_apps = False
        modules.launcher_bridge = True
        return "maintenance", modules

    else:
        console.print("[dim]Exiting installer.[/dim]")
        sys.exit(0)


def parse_arguments() -> Tuple[argparse.Namespace, SelectedModules]:
    parser = argparse.ArgumentParser(
        description="Arch Linux Universal Gaming Architecture - Bleeding-Edge Native Installer for Hyprland / Pure Wayland.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (automatically confirm all recommended steps with fast binary packages).")
    parser.add_argument("-i", "--interactive", action="store_true", help="Open interactive custom component checklist directly.")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Dry run mode (simulate operations without modifying system).")
    build_mode = parser.add_mutually_exclusive_group()
    build_mode.add_argument("--bin", action="store_true", help="Use pre-compiled DwarFS and ProtonUp-Qt AUR packages.")
    build_mode.add_argument("--native", action="store_true", help="Build DwarFS for this CPU and ProtonUp-Qt from source.")
    parser.add_argument("--extra-pkgs", nargs="*", default=[], help="Specify additional pacman packages to install.")
    parser.add_argument("--skip-gpu", action="store_true", help="Skip GPU driver detection and installation.")
    parser.add_argument("--skip-wine", action="store_true", help="Skip Wine-staging and 32-bit compatibility runtimes.")
    parser.add_argument("--skip-perf", action="store_true", help="Skip performance tools (Gamescope, GameMode, MangoHud).")
    parser.add_argument("--skip-flatpak", action="store_true", help="Skip Flatpak applications and runtime layers.")
    parser.add_argument("--skip-sysctl", action="store_true", help="Skip kernel sysctl and limits optimizations.")
    parser.add_argument("--skip-dwarfs", action="store_true", help="Skip DwarFS compression tools.")
    parser.add_argument("--skip-protonup", action="store_true", help="Skip ProtonUp-Qt runner manager.")

    args = parser.parse_args()

    modules = SelectedModules()
    if args.extra_pkgs:
        modules.extra_packages = args.extra_pkgs

    if args.skip_gpu:
        modules.gpu_drivers = False
    if args.skip_wine:
        modules.categories.discard("wine_stack")
        modules.categories.discard("runtime_32bit")
    if args.skip_perf:
        modules.categories.discard("performance_tools")
    if args.skip_flatpak:
        modules.flatpak_apps = False
    if args.skip_sysctl:
        modules.sysctl_tuning = False
    if args.skip_dwarfs:
        modules.dwarfs_mode = "skip"
    elif args.native:
        modules.dwarfs_mode = "native"
    elif args.bin:
        modules.dwarfs_mode = "bin"

    if args.skip_protonup:
        modules.protonup_mode = "skip"
    elif args.native:
        modules.protonup_mode = "source"
    else:
        # Default to bin unless explicitly skipped; respects --skip-protonup
        if modules.protonup_mode != "skip":
            modules.protonup_mode = "bin"

    return args, modules


# ==============================================================================
# 6. MAIN EXECUTION PIPELINE
# ==============================================================================

def main():
    args, cli_modules = parse_arguments()

    console.clear()
    console.print(Panel.fit(
        "[bold magenta]Arch Linux Universal Gaming Architecture[/bold magenta]\n"
        "[white]Bleeding-Edge Native Installer for Drivers, Steam, Lutris, Wine-Staging, Gamescope, & Pure Wayland/Hyprland.[/white]",
        border_style="magenta"
    ))

    cli_selected = bool(args.extra_pkgs or args.bin or args.native or args.skip_gpu
                        or args.skip_wine or args.skip_perf or args.skip_flatpak
                        or args.skip_sysctl or args.skip_dwarfs or args.skip_protonup)
    if args.interactive or (not args.yes and not cli_selected):
        preset_name, modules = run_interactive_menu()
        if args.skip_gpu:
            modules.gpu_drivers = False
        if args.skip_wine:
            modules.categories.difference_update({"wine_stack", "runtime_32bit"})
        if args.skip_perf:
            modules.categories.discard("performance_tools")
        if args.skip_flatpak:
            modules.flatpak_apps = False
        if args.skip_sysctl:
            modules.sysctl_tuning = False
        if args.skip_dwarfs:
            modules.dwarfs_mode = "skip"
        elif args.native:
            modules.dwarfs_mode = "native"
        elif args.bin:
            modules.dwarfs_mode = "bin"
        if args.skip_protonup:
            modules.protonup_mode = "skip"
        elif args.native:
            modules.protonup_mode = "source"
        elif args.bin:
            modules.protonup_mode = "bin"
        modules.extra_packages = args.extra_pkgs
    else:
        modules = cli_modules

    ctx = SetupContext(
        dry_run=args.dry_run,
        auto_yes=args.yes,
        modules=modules
    )

    check_root_and_locks(ctx)

    if not ctx.dry_run:
        console.print("\n[cyan]Authenticating with sudo for system configuration...[/cyan]")
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except subprocess.CalledProcessError:
            console.print("[bold red]Failed to authenticate with sudo. Exiting.[/bold red]")
            sys.exit(1)

        ctx.sudo_thread = threading.Thread(target=keep_sudo_alive, args=(ctx.stop_sudo_event,), daemon=True)
        ctx.sudo_thread.start()

    try:
        # Step 1: Pacman configuration & [multilib] activation
        if modules.gpu_drivers or len(modules.categories) > 0 or modules.extra_packages:
            console.print("\n[bold cyan]Step 1: Synchronizing Pacman Repositories & [multilib][/bold cyan]")
            multilib_changed = enable_multilib_and_optimizations(ctx)

            all_target_pkgs = set()
            for cat_key in modules.categories:
                if cat_key in PACKAGE_CATALOG:
                    all_target_pkgs.update(PACKAGE_CATALOG[cat_key]["packages"])
            all_target_pkgs.update(modules.extra_packages)
            if modules.gpu_drivers:
                gpus = detect_gpus()
                gpu_pkgs, _ = get_gpu_packages(gpus)
                all_target_pkgs.update(gpu_pkgs)

            missing_any = bool(filter_missing_packages(list(all_target_pkgs)))
            if multilib_changed or missing_any:
                run_command(
                    ctx,
                    ["sudo", "pacman", "-Syu", "--needed", "--noconfirm", "--", *sorted(filter_missing_packages(list(all_target_pkgs)))],
                    "Upgrade Arch packages coherently and install missing gaming packages.",
                    retries=3
                )
            else:
                console.print("[bold green]✔ All required packages are already installed.[/bold green]")

        # Step 2: GPU Detection and Driver Installation
        if modules.gpu_drivers:
            console.print("\n[bold cyan]Step 2: Graphics Architecture & Vulkan Drivers[/bold cyan]")
            configure_gpu_drivers(ctx)

        # Step 3: Kernel 7.x & Sysctl Gaming Optimizations
        if modules.sysctl_tuning:
            console.print("\n[bold cyan]Step 3: Kernel 7.x & Sysctl Performance Tuning[/bold cyan]")
            apply_kernel_and_sysctl_optimizations(ctx)

        # Step 4: Native Gaming Stack
        if len(modules.categories) > 0 or modules.extra_packages:
            console.print("\n[bold cyan]Step 4: Native Gaming Stack & 32-bit Runtimes (Pure Wayland)[/bold cyan]")
            install_native_gaming_stack(ctx)

        # Step 5: DwarFS filesystem tools (Binary or Native CPU build)
        if modules.dwarfs_mode != "skip":
            console.print("\n[bold cyan]Step 5: DwarFS Compression Tools[/bold cyan]")
            configure_dwarfs(ctx)

        # Step 5b: ProtonUp-Qt (GE-Proton / Lutris-GE runner manager)
        if modules.protonup_mode != "skip":
            console.print("\n[bold cyan]Step 5b: ProtonUp-Qt Runner Manager[/bold cyan]")
            configure_protonup(ctx)

        # Step 5c: Direct3D-to-Vulkan Translation Layers (DXVK & VKD3D-Proton)
        if "wine_stack" in modules.categories:
            console.print("\n[bold cyan]Step 5c: Direct3D-to-Vulkan Translation Layers (DXVK & VKD3D-Proton)[/bold cyan]")
            configure_dxvk_vkd3d(ctx)

        # Step 6: Flatpak Ecosystem & Runtime Layers
        if modules.flatpak_apps:
            console.print("\n[bold cyan]Step 6: Flatpak Sandbox & Runtime Layers[/bold cyan]")
            configure_flatpak_ecosystem(ctx)

        # Step 7: Application launcher and icon integration
        if modules.launcher_bridge:
            with console.status("[bold green]Bridging Flatpak desktop entries and application icons...[/bold green]", spinner="dots"):
                integrate_desktop_and_icons(ctx)
            if not ctx.dry_run:
                console.print("[bold green]✔ Application launcher and icon integration complete![/bold green]")

        # Step 8: Master Game Runner Integration
        console.print("\n[bold cyan]Step 8: Master Game Runner Integration[/bold cyan]")
        integrate_game_runner_shortcuts(ctx)
        report_text = Text()
        if ctx.failures:
            report_text.append("Gaming setup completed with failed or skipped steps.\n", style="bold yellow")
            for failure in ctx.failures:
                report_text.append(f"• {failure}\n", style="yellow")
        else:
            report_text.append("✔ Selected steps planned.\n" if ctx.dry_run else
                               "✔ Selected steps completed.\n", style="bold green")
        if ctx.dry_run:
            report_text.append("Dry run complete; no installation steps were executed.\n\n", style="white")
        elif not ctx.failures:
            report_text.append("Selected installation steps completed successfully.\n\n", style="white")
        report_text.append("Configured Modules:\n", style="bold cyan")
        if modules.gpu_drivers:
            report_text.append("• Pure Wayland Graphics Pipeline: Vulkan 32/64-bit with Hybrid GPU support (prime-run)\n", style="white")
        if "wine_stack" in modules.categories:
            report_text.append("• Wine-Staging with Esync/Fsync and native Wayland staging driver\n", style="white")
        if "performance_tools" in modules.categories:
            report_text.append("• Gamescope, MangoHud, and Feral GameMode packages selected\n", style="white")
        if modules.sysctl_tuning:
            report_text.append("• vm.max_map_count floor and login file descriptor limits selected\n", style="white")
        if "runtime_32bit" in modules.categories:
            report_text.append("• 32-bit Audio/Video codec stack (libpng, libldap, vkd3d, libxtst) + innoextract/7zip/unrar\n", style="white")
        if modules.protonup_mode != "skip":
            report_text.append("• ProtonUp-Qt: Runner manager for GE-Proton & Lutris-GE (via AUR protonup-qt-bin or Flatpak pupgui2)\n", style="white")
        if modules.flatpak_apps:
            report_text.append("• Flatpak native Wayland sockets & crisp icons bridged directly into ~/.local/share/\n", style="white")
        if modules.dwarfs_mode != "skip":
            report_text.append(f"• DwarFS Repack Tools ({'Fast Binary' if modules.dwarfs_mode == 'bin' else 'Native CPU -march=native build'})\n", style="white")

        report_text.append("\nHyprland & Wayland Pro-Tips:\n", style="bold yellow")
        report_text.append("1. Zero-Latency Tearing: Add `windowrulev2 = immediate, class:^(steam_app_.*)$` to your Hyprland window rules.\n", style="white")
        report_text.append("2. Hybrid GPUs: Run games on discrete GPU using `prime-run <command>` or gamescope.\n", style="white")
        report_text.append("3. FPS Limiting: Use `fps_limiter.py <fps> <command>` for universal low-latency frame capping.\n", style="white")
        report_text.append("4. Native Wayland Proton: Set `PROTON_ENABLE_WAYLAND=1` in Steam launch options for native Wayland surface presentation.\n", style="white")

        console.print(Panel(report_text, title="Installation Summary", border_style="yellow" if ctx.failures else "green"))

        # Send completion desktop notification to Wayland/Hyprland session
        if not ctx.dry_run and not ctx.failures:
            send_notification(
                "Gaming Architecture Ready",
                "Arch Linux gaming stack configured successfully.",
                urgency="normal",
                icon="applications-games"
            )
        if ctx.failures:
            raise SystemExit(1)

    finally:
        ctx.stop_sudo_event.set()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]Script interrupted by user. Exiting safely.[/bold red]")
        sys.exit(0)
