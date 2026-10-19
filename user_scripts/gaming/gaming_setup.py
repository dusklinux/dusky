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
3. Flexible Packaging: Pre-compiled binaries (-bin) OR Native CPU Build (-march=native -O3).
4. Wayland Pipeline: Native Wayland sandbox sockets, Gamescope, and NVIDIA DRM modesetting checks.
5. Performance: Optional kernel sysctl tuning with a practical vm.max_map_count floor.
6. Desktop Integration: Native desktop notifications (Wayland DBus session aware) & instant launcher icon bridging.
"""

import argparse
import configparser
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import suppress
from pathlib import Path
from collections.abc import Sequence
from typing import TypedDict

# ==============================================================================
# 1. DECLARATIVE CONFIGURATION CATALOGS (Easily add / remove packages here)
# ==============================================================================

class PackageCategory(TypedDict, closed=True):
    title: str
    description: str
    packages: list[str]


class GPUProfile(TypedDict, closed=True):
    name: str
    packages: list[str]
    description: str


class FlatpakApp(TypedDict, closed=True):
    name: str
    id: str
    wayland: bool
    host_fs: bool


# Core Native Package Categories
PACKAGE_CATALOG: dict[str, PackageCategory] = {
    "core_clients": {
        "title": "Core Gaming Clients",
        "description": "Native Steam, Lutris, and Flatpak package manager",
        "packages": ["steam", "lutris", "flatpak"]
    },
    "wine_stack": {
        "title": "Wine-Staging & Windows Compatibility",
        "description": "Wine-Staging with native Wayland support",
        "packages": [
            "wine-staging", "wine-gecko", "wine-mono", "winetricks",
            "cabextract", "samba", "zenity", "kdialog", "umu-launcher"
        ]
    },
    "runtime_32bit": {
        "title": "32-Bit Audio, Video, & Vulkan Compatibility Layer",
        "description": "Essential 32-bit runtimes preventing missing-symbol crashes in Wine/Proton",
        "packages": [
            "lib32-gnutls", "lib32-gtk3", "lib32-libpulse", "lib32-alsa-plugins",
            "lib32-vulkan-icd-loader", "vulkan-icd-loader", "vulkan-tools",
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
GPU_VENDOR_MAP: frozendict[str, str] = frozendict({
    "0x8086": "Intel",
    "0x1002": "AMD",
    "0x10de": "NVIDIA",
    "0x1af4": "RedHat VirtIO (VM)",
    "0x15ad": "VMware (VM)",
    "0x80ee": "VirtualBox (VM)",
    "0x1234": "QEMU Bochs (VM)",
    "0x1414": "Hyper-V (VM)",
    "0x1b36": "RedHat QXL (VM)",
})

# GPU Driver Packages Matrix
GPU_DRIVER_CATALOG: dict[str, GPUProfile] = {
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
        "packages": ["mesa", "lib32-mesa", "vulkan-virtio", "lib32-vulkan-virtio",
                     "vulkan-swrast", "lib32-vulkan-swrast"],
        "description": "Virtualized 32/64-bit Mesa & VirtIO / Software Vulkan drivers"
    }
}

# Flatpak Applications Catalog
FLATPAK_APP_CATALOG: list[FlatpakApp] = [
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

LIMITS_GAMING_CONF = """# Login file descriptor limits for game clients and compatibility tools
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
    categories: set[str] = field(default_factory=lambda: set(PACKAGE_CATALOG.keys()))
    gpu_drivers: bool = True
    sysctl_tuning: bool = True
    dwarfs_mode: str = "bin"  # "bin", "native", "source", or "skip"
    dwarfs_reinstall: bool = False
    protonup_mode: str = "bin"  # "bin", "source", "flatpak", or "skip"
    flatpak_apps: bool = True
    launcher_bridge: bool = True
    extra_packages: list[str] = field(default_factory=list)


class SetupContext:
    def __init__(
        self,
        dry_run: bool = False,
        auto_yes: bool = False,
        modules: SelectedModules | None = None
    ):
        self.dry_run = dry_run
        self.auto_yes = auto_yes
        self.modules = modules or SelectedModules()
        self.stop_sudo_event = threading.Event()
        self.sudo_thread: threading.Thread | None = None
        self.failures: list[str] = []


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
    except OSError:
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
    except (OSError, subprocess.SubprocessError):
        pass


def keep_sudo_alive(stop_event: threading.Event):
    """Refreshes sudo credential timestamp cache in the background every 90 seconds."""
    while not stop_event.is_set():
        try:
            subprocess.run(["sudo", "-n", "-v"], check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        stop_event.wait(90)


def check_root_and_locks(ctx: SetupContext):
    """Never remove pacman's lock while another process could acquire it."""
    if os.geteuid() == 0:
        console.print("[bold red]CRITICAL ERROR: Do not run this script as root.[/bold red]")
        console.print("Run it as your normal user. Sudo will be invoked securely with proper permissions.")
        sys.exit(1)

    db_lck = Path("/var/lib/pacman/db.lck")
    modules = ctx.modules
    needs_pacman = (modules.gpu_drivers or modules.categories or modules.extra_packages
                    or modules.flatpak_apps or modules.dwarfs_mode != "skip"
                    or modules.protonup_mode != "skip")
    if needs_pacman and db_lck.exists():
        raise RuntimeError(f"Pacman database is locked at {db_lck}; inspect the owning package manager before retrying")


def run_command(
    ctx: SetupContext,
    command: Sequence[str],
    description: str,
    critical: bool = True,
    show_command: bool = True,
    retries: int = 1,
    extra_env: dict[str, str] | None = None
) -> bool:
    """Run an argument vector with dry-run simulation and retry support."""
    if isinstance(command, (str, bytes)) or not command or \
            not all(isinstance(part, str) for part in command):
        raise TypeError("run_command requires a nonempty sequence of string arguments")
    console.print(f"\n[bold cyan]Task:[/bold cyan] {description}")
    if show_command:
        console.print(Text(shlex.join(command), style="dim"))

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

    if retries < 1:
        raise ValueError("retries must be at least 1")
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(list(command), env=env, check=False)
            if result.returncode in (-signal.SIGINT, 128 + signal.SIGINT):
                raise KeyboardInterrupt
            if result.returncode == 0:
                console.print("[bold green]✔ Success[/bold green]")
                return True
            error = f"exit code {result.returncode}"
        except OSError as exc:
            error = str(exc)
        console.print(f"[bold red]✘ {description}: {error}[/bold red]")
        if attempt < retries:
            time.sleep(2)
    if critical:
        raise RuntimeError(f"{description}: {error}")
    ctx.failures.append(f"{description}: {error}")
    return False


# ==============================================================================
# 3. PACMAN & HARDWARE DETECTION ENGINES
# ==============================================================================

def install_configuration(ctx: SetupContext, source: Path, destination: Path, description: str) -> None:
    """Stage a complete file beside its destination, then replace it atomically."""
    destination = destination.resolve()
    staged = destination.with_name(f".{destination.name}.{source.parent.name}.tmp")
    try:
        run_command(ctx, ["sudo", "install", "-m", "644", "--", str(source), str(staged)],
                    description, show_command=False)
        run_command(ctx, ["sudo", "mv", "-fT", "--", str(staged), str(destination)],
                    f"Activate {destination.name}", show_command=False)
    finally:
        if not ctx.dry_run and staged.exists():
            # Clean only this operation's unfinished staging file, including after cancellation.
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["sudo", "-n", "rm", "-f", "--", str(staged)], check=False,
                               timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def enable_multilib_and_optimizations(
    ctx: SetupContext, pacman_conf: Path = Path("/etc/pacman.conf")
) -> bool:
    """Enable multilib and commented display/download options, preserving other settings."""
    content = pacman_conf.read_text(encoding="utf-8")
    lines = content.splitlines()
    configured = subprocess.run(
        ["pacman-conf", "--config", str(pacman_conf), "--repo", "multilib", "Server"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=10, check=False,
    )
    multilib_ready = configured.returncode == 0 and bool(configured.stdout.strip())
    if not multilib_ready:
        # Prefer an active section over a commented example; never create duplicate repositories.
        active = next((i for i, line in enumerate(lines)
                       if line.split("#", 1)[0].strip() == "[multilib]"), None)
        start = active
        if start is None:
            start = next((i for i, line in enumerate(lines)
                          if re.fullmatch(r"\s*#\s*\[multilib\]\s*(?:#.*)?", line)), None)
        if start is None:
            lines.extend(["", "[multilib]", "Include = /etc/pacman.d/mirrorlist"])
        else:
            lines[start] = "[multilib]"
            end = next((i for i in range(start + 1, len(lines))
                        if re.prefixmatch(r"\s*#?\s*\[", lines[i])), len(lines))
            ready = any(re.prefixmatch(r"\s*(?:Include|Server)\s*=", line)
                        for line in lines[start + 1:end])
            if not ready:
                include = next((i for i in range(start + 1, end)
                                if re.prefixmatch(r"\s*#\s*Include\s*=", lines[i])), None)
                if include is None:
                    lines.insert(end, "Include = /etc/pacman.d/mirrorlist")
                else:
                    lines[include] = lines[include].lstrip().removeprefix("#").lstrip()

    section = ""
    active_options = {line.split("#", 1)[0].split("=", 1)[0].strip() for line in lines
                      if not line.lstrip().startswith("#")}
    for i, line in enumerate(lines):
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("["):
            section = stripped
        if section == "[options]":
            for name, value in (("Color", "Color"), ("ParallelDownloads", "ParallelDownloads = 5")):
                if name not in active_options and re.prefixmatch(rf"\s*#\s*{name}\b", line):
                    lines[i] = value
                    active_options.add(name)

    updated = "\n".join(lines) + "\n"
    if updated == content:
        if not multilib_ready:
            raise RuntimeError(f"Multilib configuration is unusable: {configured.stderr.strip()}")
        return False
    if ctx.dry_run:
        console.print(f"[dim][DRY RUN] Would update {pacman_conf}[/dim]")
        return True
    with tempfile.TemporaryDirectory(prefix="gaming-pacman-") as tmp:
        temp_conf = Path(tmp) / "pacman.conf"
        temp_conf.write_text(updated, encoding="utf-8")
        cp = subprocess.run(["pacman-conf", "--config", str(temp_conf), "--repo", "multilib", "Server"],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=10, check=False)
        if cp.returncode != 0 or not cp.stdout.strip():
            raise RuntimeError(f"Invalid multilib configuration: {cp.stderr.strip()}")
        backup = f"{pacman_conf}.bak.{datetime.now():%Y%m%d_%H%M%S_%f}"
        run_command(ctx, ["sudo", "cp", "--", str(pacman_conf), backup],
                    "Back up pacman.conf", show_command=False)
        install_configuration(ctx, temp_conf, pacman_conf, "Configure pacman.conf")
    return True


def filter_missing_packages(pkgs: Sequence[str]) -> list[str]:
    """Ask libalpm which package dependencies are missing, including provided names."""
    if not pkgs:
        return []
    res = subprocess.run(["pacman", "-T", "--", *pkgs], capture_output=True,
                         encoding="utf-8", errors="replace", timeout=30, check=False)
    if res.returncode not in (0, 127):
        raise RuntimeError(f"Could not query installed packages: {res.stderr.strip()}")
    missing = set(res.stdout.splitlines())
    return [pkg for pkg in pkgs if pkg in missing]


def detect_cpu_info() -> CPUInfo:
    """Detects CPU model name, logical thread count, and x86-64 microarchitecture tier (v1-v4)."""
    cores = os.process_cpu_count() or 1
    model_name = "Generic x86-64 CPU"
    x86_ver = 1

    try:
        flags_txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        per_cpu_flags: list[set[str]] = []
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
    except (OSError, ValueError):
        pass

    return CPUInfo(model=model_name, cores=cores, x86_version=x86_ver)


def probe_vaapi_drivers() -> dict[str, bool]:
    """Probes /usr/lib/dri for installed VA-API hardware video acceleration drivers."""
    dri_dirs = [Path("/usr/lib/dri"), Path("/usr/lib64/dri")]
    found = {}
    for k in ["nvidia", "nouveau", "iHD", "i965", "radeonsi"]:
        name = f"{k}_drv_video.so"
        found[k] = any((d / name).exists() for d in dri_dirs)
    return found


def nvidia_modeset_confirmed() -> bool:
    """Confirm the running module's setting, rather than infer it from defaults."""
    path = Path("/sys/module/nvidia_drm/parameters/modeset")
    try:
        return path.read_text(encoding="utf-8").strip().lower() in {"y", "1"}
    except PermissionError:
        try:
            cp = subprocess.run(["sudo", "-n", "cat", "--", str(path)],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=5, check=False)
            return cp.returncode == 0 and cp.stdout.strip().lower() in {"y", "1"}
        except (OSError, subprocess.SubprocessError):
            return False
    except OSError:
        return False


def detect_gpus() -> list[GPUInfo]:
    """Scan PCI display devices, including GPUs with no bound driver or DRM node."""
    names = {}
    if shutil.which("lspci"):
        try:
            cp = subprocess.run(["lspci", "-D", "-mm", "-nn"], capture_output=True,
                                encoding="utf-8", errors="replace", timeout=10, check=True,
                                env={**os.environ, "LC_ALL": "C"})
            for line in cp.stdout.splitlines():
                parts = shlex.split(line)
                if len(parts) >= 4:
                    names[parts[0]] = parts[3]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass  # PCI IDs still provide reliable vendor identification.
    cards = {}
    for card in Path("/sys/class/drm").glob("card[0-9]*"):
        if re.fullmatch(r"card\d+", card.name):
            cards[(card / "device").resolve()] = f"/dev/dri/{card.name}"
    gpus = []
    for device in sorted(Path("/sys/bus/pci/devices").glob("*")):
        try:
            if int((device / "class").read_text(encoding="utf-8").strip(), 16) >> 16 != 0x03:
                continue
            vendor = (device / "vendor").read_text(encoding="utf-8").strip().lower()
            boot_vga = 0
            with suppress(OSError, ValueError):
                boot_vga = int((device / "boot_vga").read_text(encoding="utf-8").strip())
            driver = (device / "driver").resolve().name if (device / "driver").exists() else "unbound"
            gpus.append(GPUInfo(
                dev_node=cards.get(device.resolve(), "unavailable"), pci_slot=device.name,
                vendor_id=vendor, vendor_name=GPU_VENDOR_MAP.get(vendor, f"Unknown ({vendor})"),
                device_name=names.get(device.name, f"Graphics Device [{device.name}]"),
                boot_vga=boot_vga, driver=driver,
            ))
        except (OSError, ValueError):
            continue  # A device can disappear during enumeration.
    return sorted(gpus, key=lambda gpu: (-gpu.boot_vga, gpu.pci_slot))


def kernel_header_packages() -> list[str]:
    """Use each installed kernel's pkgbase; custom kernels need matching headers too."""
    headers = set()
    for pkgbase in Path("/usr/lib/modules").glob("*/pkgbase"):
        if not (pkgbase.parent / "build/Makefile").is_file():
            headers.add(pkgbase.read_text(encoding="utf-8").strip() + "-headers")
    return sorted(headers)


def get_gpu_packages(detected_gpus: list[GPUInfo]) -> tuple[list[str], str]:
    """Determines required GPU driver packages based on GPU_DRIVER_CATALOG."""
    pkgs: set[str] = set()
    descriptions: list[str] = []

    has_amd = any("amd" in g.vendor_name.lower() or g.vendor_id == "0x1002" for g in detected_gpus)
    has_intel = any("intel" in g.vendor_name.lower() or g.vendor_id == "0x8086" for g in detected_gpus)
    has_nvidia = any("nvidia" in g.vendor_name.lower() or g.vendor_id == "0x10de" for g in detected_gpus)
    has_vm = any("(vm)" in g.vendor_name.lower() for g in detected_gpus)

    if has_vm:
        pkgs.update(GPU_DRIVER_CATALOG["virtual"]["packages"])
        descriptions.append(GPU_DRIVER_CATALOG["virtual"]["description"])
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

    return sorted(pkgs), ", ".join(descriptions)


# ==============================================================================
# 4. MODULE EXECUTION HANDLERS
# ==============================================================================

def select_gpu_packages(ctx: SetupContext) -> list[str]:
    """Select the GPU package plan before any package installation."""
    if not ctx.modules.gpu_drivers:
        console.print("[dim]Skipping GPU driver installation as configured.[/dim]")
        return []

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

    if ctx.auto_yes:
        if not auto_pkgs:
            ctx.failures.append("No supported GPU profile detected; GPU drivers were skipped")
            return []
        console.print(f"[green]Auto-detected profile: {auto_desc}[/green]")
        target_pkgs = auto_pkgs
    else:
        choices = [("Auto-detected drivers: " + auto_desc, auto_pkgs)] if auto_pkgs else []
        for key in ("amd", "nvidia", "intel"):
            profile = GPU_DRIVER_CATALOG[key]
            choices.append((profile["description"], profile["packages"]))
        hybrid_key = ("hybrid_nvidia_amd" if any(g.vendor_id == "0x1002" for g in detected_gpus)
                      else "hybrid_nvidia_intel")
        choices.append(("Hybrid Intel/AMD + NVIDIA with PRIME offload",
                        GPU_DRIVER_CATALOG[hybrid_key]["packages"]))
        choices.append(("Skip (I manually manage graphics drivers)", []))
        console.print("\n[bold cyan]Select GPU Installation Mode:[/bold cyan]")
        for number, (title, _) in enumerate(choices, start=1):
            console.print(Text(f"{number}. {title}"))
        selected = Prompt.ask("Enter choice", choices=[str(i) for i in range(1, len(choices) + 1)],
                              default="1" if auto_pkgs else str(len(choices)))
        target_pkgs = choices[int(selected) - 1][1]
    if not target_pkgs:
        console.print("[dim]Skipping GPU driver installation.[/dim]")
        return []

    if "nvidia-open-dkms" in target_pkgs:
        provider = subprocess.run(["pacman", "-T", "NVIDIA-MODULE"], capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=30, check=False)
        if provider.returncode not in (0, 127):
            raise RuntimeError(f"Could not inspect NVIDIA module provider: {provider.stderr.strip()}")
        if provider.returncode == 0 and filter_missing_packages(["nvidia-open-dkms"]):
            target_pkgs = [pkg for pkg in target_pkgs if pkg != "nvidia-open-dkms"]
        else:
            target_pkgs = [*target_pkgs, *kernel_header_packages()]
        if provider.returncode == 127:
            console.print("[yellow]NVIDIA open kernel modules require Turing or newer hardware; "
                          "older GPUs need a separately managed driver.[/yellow]")
    return sorted({*target_pkgs, "vulkan-tools"})


def verify_gpu_configuration(ctx: SetupContext) -> None:
    if ctx.dry_run:
        return
    if any(g.vendor_id == "0x10de" for g in detect_gpus()) and not nvidia_modeset_confirmed():
        console.print("[yellow]NVIDIA DRM modesetting could not be confirmed. "
                      "After installing a driver, reboot and verify nvidia_drm.modeset=1.[/yellow]")


def verify_vulkan(ctx: SetupContext) -> None:
    """Enumerate the running Vulkan devices once; package presence alone is insufficient."""
    if ctx.dry_run:
        console.print("[dim][DRY RUN] Would verify Vulkan device enumeration after installation.[/dim]")
        return
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env.pop("DISPLAY", None)
    try:
        result = subprocess.run(["vulkaninfo", "--summary"], capture_output=True,
                                encoding="utf-8", errors="replace", timeout=20, check=True,
                                env=env)
        devices = re.findall(r"^\s*deviceName\s*=\s*(.+)$", result.stdout, flags=re.MULTILINE)
        if not devices:
            ctx.failures.append("Vulkan enumeration returned no devices; inspect vulkaninfo --summary")
            return
        console.print(Text("Vulkan enumerated: " + "; ".join(devices), style="green"))
        console.print("[dim]Enumeration does not test game rendering or 32-bit Vulkan. "
                      "New kernel drivers may require a reboot.[/dim]")
    except (OSError, subprocess.SubprocessError) as exc:
        ctx.failures.append(f"Vulkan enumeration failed: {exc}; a newly installed driver may need a reboot")


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
            current_map = int(Path("/proc/sys/vm/max_map_count").read_text(encoding="utf-8").strip())
        temp_sysctl.write_text(SYSCTL_GAMING_CONF.replace("1048576", str(max(1048576, current_map))), encoding="utf-8")
        temp_limits.write_text(LIMITS_GAMING_CONF, encoding="utf-8")
        install_configuration(ctx, temp_sysctl, sysctl_file, "Install gaming sysctl settings")
        install_configuration(ctx, temp_limits, limits_file, "Install gaming login limits")
        run_command(ctx, ["sudo", "sysctl", "-p", str(sysctl_file)],
                    "Apply gaming sysctl settings", critical=False)


def configure_native_gaming_stack(ctx: SetupContext):
    """Configure directories and services after installing the selected native packages."""
    # Ensure Lutris runner directory exists for ProtonUp-Qt / GE-Proton integration
    if not ctx.dry_run and "core_clients" in ctx.modules.categories:
        try:
            lutris_runners_dir = xdg_data_home() / "lutris/runners/wine"
            lutris_runners_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ctx.failures.append(f"Create Lutris runner directory: {exc}")

    if "performance_tools" in ctx.modules.categories:
        # Enable the installed GameMode user service
        run_command(
            ctx,
            ["systemctl", "--user", "enable", "--now", "gamemoded.service"],
            "Enable and start Feral GameMode user daemon",
            critical=False
        )


def configure_dwarfs(ctx: SetupContext):
    """
    Installs DwarFS compression tools via AUR with user-selected packaging strategy:
    - 'bin': Fast pre-compiled binary (dwarfs-bin) - pre-compiled package.
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
        ctx.failures.append("DwarFS requested but no AUR helper is available")
        return

    cpu = detect_cpu_info()

    if mode == "bin":
        run_command(
            ctx,
            [aur_helper, "-S", "--rebuild", "--noconfirm", "dwarfs-bin"],
            "Install pre-compiled DwarFS binary package (no DwarFS source compilation)",
            critical=False
        )
    elif mode == "native":
        if ctx.dry_run:
            run_command(ctx, [aur_helper, "-S", "--rebuild", "--noconfirm", "dwarfs"],
                        f"Rebuild DwarFS for {cpu.model} with a temporary makepkg configuration",
                        critical=False)
        else:
            with tempfile.TemporaryDirectory(prefix="gaming-makepkg-", dir="/tmp") as td:
                conf = Path(td) / "makepkg.conf"
                config_paths = [Path("/etc/makepkg.conf"),
                                *sorted(Path("/etc/makepkg.conf.d").glob("*.conf"))]
                config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                user_conf = config_home / "pacman/makepkg.conf"
                if not user_conf.is_file():
                    user_conf = Path.home() / ".makepkg.conf"
                if user_conf.is_file():
                    config_paths.append(user_conf)
                conf.write_text("".join(f"source {shlex.quote(str(path))}\n"
                                        for path in config_paths) +
                                '\nCFLAGS="$CFLAGS -march=native -O3"\n' +
                                'CXXFLAGS="$CXXFLAGS -march=native -O3"\n' +
                                f'MAKEFLAGS="-j{cpu.cores}"\n', encoding="utf-8")
                helper_flags = (["--makepkgconf", str(conf)] if aur_helper == "yay"
                                else ["--mflags", f"--config={conf}"])
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
        run_command(ctx, ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                          "https://dl.flathub.org/repo/flathub.flatpakrepo"],
                    "Initialize user Flathub remote")
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
        if not ctx.modules.flatpak_apps:
            ctx.failures.append("ProtonUp-Qt requested but no AUR helper or Flatpak installation selected")
        return

    console.print("\n[bold cyan]Installing ProtonUp-Qt (GE-Proton & Wine-GE manager for Lutris)...[/bold cyan]")
    target = "protonup-qt" if mode == "source" else "protonup-qt-bin"
    run_command(
        ctx,
        [aur_helper, "-S", "--rebuild", "--noconfirm", target],
        "Install ProtonUp-Qt (Proton-GE / Lutris-GE runner downloader) via AUR",
        critical=False
    )


def translation_libraries_ready() -> dict[str, bool]:
    """Check each DLL the standalone runner links, rather than a single sentinel file."""
    dxvk_dlls = ("d3d8.dll", "d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll")
    return {
        "dxvk-bin": all((Path("/usr/share/dxvk") / arch / dll).is_file()
                        for arch in ("x32", "x64") for dll in dxvk_dlls),
        "vkd3d-proton-bin": any(
            all((base / "x64" / dll).is_file() for dll in ("d3d12.dll", "d3d12core.dll"))
            for base in (Path("/usr/share/vkd3d"), Path("/usr/share/vkd3d-proton"))
        ),
    }


def configure_dxvk_vkd3d(ctx: SetupContext):
    """Install system DLLs for the standalone runner; Lutris/Proton manage their own copies."""
    missing = [package for package, ready in translation_libraries_ready().items() if not ready]
    if not missing:
        console.print("[bold green]✔ System DXVK and VKD3D-Proton DLLs are present.[/bold green]")
        return
    aur_helper = next((helper for helper in ("paru", "yay") if shutil.which(helper)), None)
    if not aur_helper:
        ctx.failures.append("DXVK/VKD3D-Proton DLLs are missing but no AUR helper is available")
        return
    # These targets have missing files, even if their package versions are current.
    if run_command(ctx, [aur_helper, "-S", "--noconfirm", "--", *missing],
                   f"Install or repair system translation DLLs: {', '.join(missing)}", critical=False):
        if not ctx.dry_run:
            for package, ready in translation_libraries_ready().items():
                if not ready:
                    ctx.failures.append(f"{package}: required DLLs still missing after installation")


def configure_flatpak_ecosystem(ctx: SetupContext):
    """Configures Flathub remotes, installs gaming Flatpaks, Vulkan layers, and native Wayland sandbox overrides."""
    if not ctx.modules.flatpak_apps:
        console.print("[dim]Skipping Flatpak applications as configured.[/dim]")
        return

    # 1. Add Flathub remotes for the managed user and default system installations
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

    # 2. Install Flatpak apps from FLATPAK_APP_CATALOG
    installed_apps_by_scope = get_flatpak_inventory("--app")
    installed_apps = set().union(*installed_apps_by_scope.values())
    for app in FLATPAK_APP_CATALOG:
        if app["id"] == "net.davidotek.pupgui2" and (
                ctx.modules.protonup_mode == "skip" or shutil.which("protonup-qt")
                or shutil.which("pupgui2")):
            continue
        if app["id"] in installed_apps:
            console.print(f"[bold green]✔ Flatpak application {app['name']} ({app['id']}) is already installed.[/bold green]")
        else:
            if run_command(
                ctx,
                ["sudo", "flatpak", "install", "--system", "-y", "--noninteractive",
                 "--or-update", "flathub", app["id"]],
                f"Install {app['name']} via Flatpak sandbox.",
                critical=False
            ):
                installed_apps_by_scope["--system"].add(app["id"])

    # 3. Install Flatpak MangoHud & Gamescope runtime layers
    if "performance_tools" in ctx.modules.categories:
        installed_runtimes = get_flatpak_inventory("--runtime")
        for scope, refs in installed_runtimes.items():
            branches = flatpak_layer_branches(ctx, scope, refs)
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
            permissions.append("--socket=wayland")
        if app.get("host_fs"):
            permissions.append("--filesystem=host")
        if not permissions:
            continue
        for scope, refs in installed_apps_by_scope.items():
            if app["id"] not in refs:
                continue
            run_command(ctx, (["sudo"] if scope == "--system" else []) +
                        ["flatpak", "override", scope, *permissions, app["id"]],
                        f"Set {scope[2:]} Flatpak permissions for {app['name']}",
                        critical=False)


def get_flatpak_inventory(kind: str) -> dict[str, set[str]]:
    """Return installed refs by installation scope, preserving runtime branches."""
    if kind not in {"--app", "--runtime"}:
        raise ValueError(f"Unsupported Flatpak inventory kind: {kind}")
    result: dict[str, set[str]] = {"--system": set(), "--user": set()}
    if not shutil.which("flatpak"):
        return result
    for scope in result:
        query = ["flatpak", "list", scope, kind,
                 "--columns=application,branch" if kind == "--runtime" else "--columns=application"]
        try:
            cp = subprocess.run(query, capture_output=True, encoding="utf-8", errors="replace", timeout=15, check=True)
            for line in cp.stdout.splitlines():
                parts = line.split()
                if parts:
                    result[scope].add(f"{parts[0]}//{parts[1]}" if kind == "--runtime" and len(parts) > 1
                                      else parts[0])
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not query {scope} Flatpak inventory: {exc}") from exc
    return result


def flatpak_layer_branches(ctx: SetupContext, scope: str, refs: set[str]) -> set[str]:
    """Read the extension ABI declared by Freedesktop, GNOME, and KDE runtimes."""
    branches = set()
    for ref in sorted(refs):
        app_id = ref.partition("//")[0]
        if app_id not in {"org.freedesktop.Platform", "org.gnome.Platform", "org.kde.Platform"}:
            continue
        try:
            cp = subprocess.run(["flatpak", "info", scope, "--show-metadata", ref],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=15, check=True)
            metadata = configparser.ConfigParser(interpolation=None, strict=False)
            metadata.read_string(cp.stdout)
            section = "Extension org.freedesktop.Platform.VulkanLayer"
            if metadata.has_section(section):
                versions = metadata.get(section, "versions", fallback="") or metadata.get(
                    section, "version", fallback=ref.partition("//")[2])
                branches.update(version.strip() for version in versions.split(";") if version.strip())
        except (OSError, subprocess.SubprocessError, configparser.Error) as exc:
            ctx.failures.append(f"Inspect Flatpak Vulkan layers for {scope} {ref}: {exc}")
    return branches


def get_installed_flatpak_runtimes() -> set[str]:
    """Dynamically fetches installed Flatpak runtime identifiers (e.g. app_id//branch)."""
    return set().union(*get_flatpak_inventory("--runtime").values())


def get_installed_flatpaks() -> list[str]:
    """Dynamically fetches a list of all installed Flatpak Application IDs across system and user scopes."""
    return sorted(set().union(*get_flatpak_inventory("--app").values()))


def xdg_data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if Path(value).is_absolute() else Path.home() / ".local/share"


def managed_export_link(path: Path, exports: Sequence[Path]) -> bool:
    if not path.is_symlink():
        return False
    target = Path(os.path.abspath(path.parent / path.readlink()))
    return any(target.is_relative_to(export) for export in exports)


def bridge_export(source: Path, destination: Path, exports: Sequence[Path]) -> None:
    """Replace only links previously managed by this bridge; preserve user overrides."""
    if destination.is_symlink():
        if destination.readlink() == source or not managed_export_link(destination, exports):
            return
        destination.unlink()
    elif destination.exists():
        return
    destination.symlink_to(source)


def integrate_desktop_and_icons(ctx: SetupContext):
    """Bridges Flatpak .desktop files and hicolor application icons into user XDG directories for Wayland launchers."""
    if not ctx.modules.launcher_bridge:
        console.print("[dim]Skipping launcher icon integration as configured.[/dim]")
        return

    if ctx.dry_run:
        console.print("[dim][DRY RUN] Would refresh Flatpak desktop entries and icons.[/dim]")
        return

    user_apps_dir = xdg_data_home() / "applications"
    user_icons_dir = xdg_data_home() / "icons/hicolor"

    user_apps_dir.mkdir(parents=True, exist_ok=True)
    user_icons_dir.mkdir(parents=True, exist_ok=True)

    system_export_dir = Path("/var/lib/flatpak/exports/share")
    user_export_dir = xdg_data_home() / "flatpak/exports/share"

    exports = (system_export_dir, user_export_dir)

    # Clean only broken links into Flatpak exports.
    try:
        for f in user_apps_dir.iterdir():
            if f.is_symlink() and not f.exists() and managed_export_link(f, exports):
                f.unlink()
    except OSError as exc:
        ctx.failures.append(f"Clean Flatpak desktop links: {exc}")

    # Bridge .desktop entries
    installed_apps = get_installed_flatpaks()
    for app_id in installed_apps:
        desktop_file = f"{app_id}.desktop"
        target_path = None

        if (user_export_dir / "applications" / desktop_file).exists():
            target_path = user_export_dir / "applications" / desktop_file
        elif (system_export_dir / "applications" / desktop_file).exists():
            target_path = system_export_dir / "applications" / desktop_file

        if target_path:
            try:
                bridge_export(target_path, user_apps_dir / desktop_file, exports)
            except OSError as exc:
                ctx.failures.append(f"Bridge Flatpak desktop entry {desktop_file}: {exc}")

    # Resolve user-over-system precedence before touching any icon link.
    icons: dict[Path, Path] = {}
    for export in exports:
        for icon in (export / "icons/hicolor").glob("*/apps/*"):
            if icon.is_file():
                icons[icon.relative_to(export / "icons/hicolor")] = icon
    for relative, source in icons.items():
        try:
            destination = user_icons_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            bridge_export(source, destination, exports)
        except OSError as exc:
            ctx.failures.append(f"Bridge Flatpak icon {source.name}: {exc}")

    if shutil.which("update-desktop-database"):
        run_command(ctx, ["update-desktop-database", str(user_apps_dir)],
                    "Refresh application database", critical=False)
    if shutil.which("gtk-update-icon-cache"):
        run_command(ctx, ["gtk-update-icon-cache", "-f", "-t", str(user_icons_dir)],
                    "Refresh icon cache", critical=False)


def integrate_game_runner_shortcuts(ctx: SetupContext) -> None:
    """Installs Master Runner desktop entries for all discovered game profiles."""
    runner_script = Path(__file__).resolve().parent / "runner/master_runner.py"
    if runner_script.is_file():
        run_command(ctx, [sys.executable, str(runner_script), "desktop-all"],
                    "Install game profile desktop shortcuts", critical=False)


# ==============================================================================
# 5. INTERACTIVE DASHBOARD & SELECTION MENUS
# ==============================================================================

def check_system_installed_status() -> dict[str, bool]:
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
    translators = translation_libraries_ready()
    status["dxvk"] = translators["dxvk-bin"]
    status["vkd3d"] = translators["vkd3d-proton-bin"]

    try:
        res = subprocess.run(["sysctl", "-n", "vm.max_map_count"], capture_output=True, encoding="utf-8", errors="replace", timeout=5, check=False)
        status["sysctl"] = res.returncode == 0 and int(res.stdout.strip()) >= 1048576
    except (OSError, ValueError, subprocess.SubprocessError):
        status["sysctl"] = False

    flatpaks = get_installed_flatpaks()
    status["flatpak_bottles"] = "com.usebottles.bottles" in flatpaks
    status["flatpak_heroic"] = "com.heroicgameslauncher.hgl" in flatpaks
    status["flatpak_pupgui"] = "net.davidotek.pupgui2" in flatpaks

    return status


def run_interactive_menu() -> tuple[str, SelectedModules]:
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
    table.add_row("ProtonUp-Qt (GE-Proton Runner Manager)", "[green]✓ Installed[/green]" if (sys_status["protonup"] or sys_status["flatpak_pupgui"]) else "[yellow]✗ Missing[/yellow]", "Optional runner downloader; GE-Proton also uses umu")
    table.add_row("Performance Tools (Gamescope/MangoHud)", "[green]✓ Installed[/green]" if sys_status["mangohud"] else "[yellow]✗ Missing[/yellow]", "Recommended for Wayland")
    table.add_row("Kernel 7.x Sysctl Optimizations", "[green]✓ Active[/green]" if sys_status["sysctl"] else "[yellow]✗ Inactive[/yellow]", "Crucial (prevents UE5 crashes)")
    table.add_row("DwarFS Compression Tools", "[green]✓ Installed[/green]" if sys_status["dwarfs"] else "[dim]Optional[/dim]", "Required for repack mounts")
    table.add_row("Flatpak Sandbox Gaming (Bottles/Heroic)", "[green]✓ Installed[/green]" if sys_status["flatpak_bottles"] else "[dim]Optional[/dim]", "Recommended prefix manager")

    console.print(table)

    console.print("\n[bold cyan]Choose Setup Profile:[/bold cyan]")
    console.print("1. [bold green]Recommended Full Setup[/bold green] (Auto-detects GPU + all gaming tools + fast binary packages)")
    console.print("2. [bold yellow]Custom Component Checklist[/bold yellow] (Select exactly what to install/skip via interactive toggles)")
    console.print("3. [bold cyan]Native Setup[/bold cyan] (GPU + Steam/Lutris + Wine runtimes + ProtonUp-Qt + tuning)")
    console.print("4. [bold magenta]Performance Tools & Tuning[/bold magenta] (Sysctl + Gamescope/GameMode/MangoHud)")
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

        selected_cats: set[str] = set()
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
                console.print("1. Fast Pre-compiled Binary (dwarfs-bin) - No source compilation")
                console.print(f"2. CPU Native Architecture Build (-march=native -O3) - Parallel build on {cpu.cores} threads")
                console.print("3. Standard Source Build (dwarfs)")
                dw_choice = Prompt.ask("Choose DwarFS option", choices=["1", "2", "3"], default="1")
                modules.dwarfs_mode = "bin" if dw_choice == "1" else ("native" if dw_choice == "2" else "source")
            else:
                modules.dwarfs_mode = "skip"
        else:
            if Confirm.ask("Install DwarFS filesystem tools (used by compressed game repacks)?", default=True):
                console.print("\n[bold cyan]DwarFS Packaging Mode:[/bold cyan]")
                console.print("1. Fast Pre-compiled Binary (dwarfs-bin) - [bold green]No source compilation[/bold green]")
                console.print(f"2. CPU Native Architecture Build (-march=native -O3) - [bold yellow]Build for {cpu.model}[/bold yellow]")
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


def parse_arguments() -> tuple[argparse.Namespace, SelectedModules]:
    parser = argparse.ArgumentParser(
        description="Arch Linux Universal Gaming Architecture - Bleeding-Edge Native Installer for Hyprland / Pure Wayland.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    interaction = parser.add_mutually_exclusive_group()
    interaction.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (automatically confirm all recommended steps with fast binary packages).")
    interaction.add_argument("-i", "--interactive", action="store_true", help="Open the interactive setup profile menu.")
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
    apply_cli_overrides(args, modules)
    return args, modules


def apply_cli_overrides(args: argparse.Namespace, modules: SelectedModules) -> None:
    """Apply explicit CLI choices consistently to any interactive profile."""
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
    elif args.bin:
        modules.protonup_mode = "bin"


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
        _, modules = run_interactive_menu()
        apply_cli_overrides(args, modules)
    else:
        modules = cli_modules

    ctx = SetupContext(
        dry_run=args.dry_run,
        auto_yes=args.yes,
        modules=modules
    )

    check_root_and_locks(ctx)

    needs_sudo = (modules.gpu_drivers or modules.categories or modules.extra_packages
                  or modules.sysctl_tuning or modules.flatpak_apps
                  or modules.dwarfs_mode != "skip" or modules.protonup_mode != "skip")
    if not ctx.dry_run and needs_sudo:
        console.print("\n[cyan]Authenticating with sudo for system configuration...[/cyan]")
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except subprocess.CalledProcessError:
            console.print("[bold red]Failed to authenticate with sudo. Exiting.[/bold red]")
            sys.exit(1)

        ctx.sudo_thread = threading.Thread(target=keep_sudo_alive, args=(ctx.stop_sudo_event,), daemon=True)
        ctx.sudo_thread.start()

    try:
        gpu_packages = select_gpu_packages(ctx) if modules.gpu_drivers else []
        native_packages = {*PACKAGE_CATALOG[key]["packages"] for key in modules.categories}
        native_packages.update(modules.extra_packages)
        native_packages.update(gpu_packages)
        if modules.flatpak_apps or modules.protonup_mode == "flatpak":
            native_packages.add("flatpak")
        aur_selected = (modules.dwarfs_mode != "skip" or
                        modules.protonup_mode in {"bin", "source"} or
                        "wine_stack" in modules.categories)
        if native_packages or aur_selected:
            console.print("\n[bold cyan]Synchronizing selected native packages[/bold cyan]")
            changed = enable_multilib_and_optimizations(ctx)
            missing = filter_missing_packages(sorted(native_packages))
            if changed or missing or aur_selected:
                run_command(ctx, ["sudo", "pacman", "-Syu", "--needed", "--noconfirm",
                                  "--", *missing],
                            "Upgrade Arch coherently and install selected gaming packages", retries=3)
            else:
                console.print("[green]All selected native packages are already installed.[/green]")
        if gpu_packages:
            verify_gpu_configuration(ctx)
            verify_vulkan(ctx)

        # Step 3: Kernel 7.x & Sysctl Gaming Optimizations
        if modules.sysctl_tuning:
            console.print("\n[bold cyan]Step 3: Kernel 7.x & Sysctl Performance Tuning[/bold cyan]")
            apply_kernel_and_sysctl_optimizations(ctx)

        # Step 4: Native Gaming Stack
        if modules.categories or modules.extra_packages:
            console.print("\n[bold cyan]Step 4: Native Gaming Stack & 32-bit Runtimes (Pure Wayland)[/bold cyan]")
            configure_native_gaming_stack(ctx)

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
        if modules.launcher_bridge:
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
            report_text.append("• Wine-Staging with native Wayland support\n", style="white")
        if "performance_tools" in modules.categories:
            report_text.append("• Gamescope, MangoHud, and Feral GameMode packages selected\n", style="white")
        if modules.sysctl_tuning:
            report_text.append("• vm.max_map_count floor and login file descriptor limits selected\n", style="white")
        if "runtime_32bit" in modules.categories:
            report_text.append("• 32-bit audio/video and Vulkan runtimes selected\n", style="white")
        if modules.protonup_mode != "skip":
            report_text.append("• ProtonUp-Qt: Runner manager for GE-Proton & Lutris-GE (via AUR protonup-qt-bin or Flatpak pupgui2)\n", style="white")
        if modules.flatpak_apps:
            report_text.append("• Flatpak gaming applications and native Wayland permissions selected\n", style="white")
        if modules.dwarfs_mode != "skip":
            report_text.append(f"• DwarFS Repack Tools ({modules.dwarfs_mode})\n", style="white")

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
        if ctx.sudo_thread is not None:
            ctx.sudo_thread.join(timeout=6)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        console.print(Text(f"Gaming setup failed: {exc}", style="bold red"))
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n\n[bold red]Script interrupted by user. Exiting safely.[/bold red]")
        sys.exit(130)
